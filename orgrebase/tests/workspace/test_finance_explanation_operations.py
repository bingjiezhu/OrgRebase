"""Durable static Finance evaluation with fake Vertex transport."""

from __future__ import annotations

import json
import urllib.request
from contextlib import contextmanager

import pytest
from sqlalchemy import update

from orgrebase.auth import AuthenticationError, Principal, request_authorization, request_principal
from orgrebase.database import artifacts, private_records
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.workspace.advisory import AdvisoryGenerationError
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.experience_contracts import MemorySnapshot
from orgrebase.workspace.finance_experiment import FinanceExperimentService
from orgrebase.workspace.finance_explanation_operations import (
    INTENT_MEDIA,
    RESULT_MEDIA,
    SNAPSHOT_MEDIA,
    evaluate_registered_finance_arm,
    evaluate_static_finance,
    finance_evaluation_refs,
    read_static_finance_evaluation,
)
from orgrebase.workspace.finance_skill_qualification import CONTENT_RELEASE_MEDIA, QUALIFICATION_MEDIA
from orgrebase.workspace.model_budget import ModelBudget
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID
from orgrebase.workspace.skill_evolution_v2 import CONTENT_MEDIA, FinanceSkillHeadService
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_finance_advice_v4 import _provider, _Response


@contextmanager
def _as(workspace, actor: str, *roles: str):
    with _as_tenant(workspace.profile.organization_id, actor, *roles):
        yield


@contextmanager
def _as_tenant(tenant_id: str, actor: str, *roles: str):
    principal = Principal(
        issuer="https://issuer.example", subject=f"subject:{actor}",
        tenant_id=tenant_id, actor_id=actor,
        roles=frozenset(roles), expires_at=4_000_000_000,
    )
    principal_token = request_principal.set(principal)
    authorization_token = request_authorization.set(lambda: None)
    try:
        yield
    finally:
        request_authorization.reset(authorization_token)
        request_principal.reset(principal_token)


def _prepare(workspace):
    with _as(workspace, "actor:finance-governor", "governor"):
        head = FinanceSkillHeadService(
            workspace.store, tenant_id=workspace.profile.organization_id
        ).bootstrap()
    submitted = submit_change(workspace, command(workspace, slot="currency", value="EUR"))
    event_id = submitted["event"]["event_id"]
    workspace.preview_change(event_id)
    return event_id, head


def _fake_send(sent, workspace):
    def send(http_request: urllib.request.Request, *, timeout: float):
        assert timeout > 0
        body = json.loads(http_request.data)
        sent.append(body)
        user = json.loads(body["contents"][0]["parts"][0]["text"])
        projections = user.get("business_input_projections", user.get("input_projections"))
        candidate = {
            "domain_id": user["binding"]["domain_id"],
            "object_ids": user["binding"]["object_ids"],
            "source_refs": [item["ref"] for item in projections],
            "explanation": "Review the admitted change and source before approval.",
        }
        return _Response({
            "responseId": f"finance-static-{len(sent)}",
            "modelVersion": VERTEX_CANDIDATE_MODEL_ID,
            "createTime": workspace.clock.now(),
            "candidates": [{
                "content": {"parts": [{"text": json.dumps(candidate)}]},
                "finishReason": "STOP",
            }],
        })

    return send


def _rewrite_artifact_for_integrity_probe(workspace, artifact_ref, payload):
    """Simulate a compromised storage row, including its ordinary row digest."""
    with workspace.store.transaction() as connection:
        workspace.store.execute(
            connection,
            update(artifacts)
            .where(artifacts.c.artifact_id == artifact_ref)
            .values(
                payload_json=canonical_json(payload),
                payload_digest=sha256_digest(payload),
            ),
        )


def _controlled_family(workspace, event_id, head):
    """A pre-registered local family; the synthetic extra cases are never evaluated."""
    service = FinanceExperimentService(
        workspace.store, tenant_id=workspace.profile.organization_id,
        evaluator_actor_id="actor:experiment-evaluator",
        operator_actor_id="actor:experiment-operator", workspace=workspace,
    )
    preview = workspace._preview_record(event_id)["bundle"]
    clusters = ("cluster:current", "cluster:validation", "cluster:sealed")
    cases = {
        event_id: {
            "case_revision_digest": sha256_digest({
                "change_set_digest": preview["change_set"]["digest"],
                "preview_digest": preview["preview"]["digest"],
            }),
            "independence_cluster_id": clusters[0], "split": "DEVELOPMENT",
            "source_change_key": "source:current", "time_ordinal": 1,
        },
        "case:validation": {
            "case_revision_digest": sha256_digest("validation"),
            "independence_cluster_id": clusters[1], "split": "VALIDATION",
            "source_change_key": "source:validation", "time_ordinal": 2,
        },
        "case:sealed": {
            "case_revision_digest": sha256_digest("sealed"),
            "independence_cluster_id": clusters[2], "split": "SEALED_HOLDOUT",
            "source_change_key": "source:sealed", "time_ordinal": 3,
        },
    }
    with _as(workspace, "actor:experiment-evaluator", "governor"):
        family_ref = service.freeze_family(
            family_id="family:foreign-candidate", parent_head_ref=head.head_ref,
            parent_head_digest=head.head_digest, cases=cases,
            seeds=(11, 23, 37), orders={
                "CHRONOLOGICAL": clusters,
                "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
                "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
            }, rubric_digest=sha256_digest("controlled-rubric"),
            max_queries=3, max_reserved_calls=9,
            max_reserved_microusd=50_000_000,
        )
    return service, family_ref


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_static_finance_intent_precedes_fake_wire_and_private_result_isolated(
    workspace, postgres_runtime, monkeypatch, backend,
):
    if backend == "postgresql":
        from orgrebase.workspace.service import WorkspaceService

        tenant = workspace.profile.organization_id
        database = postgres_runtime(tenant_id=tenant)
        target = WorkspaceService(
            store_path=database["runtime_dsn"], store_tenant_id=tenant,
            store_migrate=False,
            runtime_configuration=workspace.runtime_configuration, clock=workspace.clock,
            review_duration_seconds=0,
        )
        target.form_quote()
    else:
        target = workspace
    try:
        event_id, head = _prepare(target)
        quote_before = target.current_quote().digest
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, target))
        with _as(target, "actor:finance-evaluator", "operator"):
            first = evaluate_static_finance(
                target, event_id=event_id, operation_id="finance-static-001",
                provider_override=_provider(),
            )
            repeated = evaluate_static_finance(
                target, event_id=event_id, operation_id="finance-static-001",
                provider_override=_provider(),
            )
            assert repeated == first
            with pytest.raises(IntegrityError, match="FINANCE_EVALUATION_CASE_ALREADY_BOUND"):
                evaluate_static_finance(
                    target, event_id=event_id, operation_id="finance-static-002",
                    provider_override=_provider(),
                )
            assert read_static_finance_evaluation(target, event_id) == first
        assert first["status"] == "PROTOCOL_VALID", first
        assert len(sent) == 2  # Finance explanation and dependent GTM candidate.
        assert first["scope"] == "STATIC_BASELINE_ONLY"
        assert first["quality_status"] == "NOT_EVALUATED"
        assert first["target_writes"] == 0
        assert first["candidate_bundle_digest"] == head.bundle.digest
        assert target.current_quote().digest == quote_before
        assert target._approval_record(event_id) is None
        assert target._outcome_record(event_id) is None
        refs = finance_evaluation_refs(target, event_id)
        intent = target.store.load_artifact(refs["intent_ref"], INTENT_MEDIA).payload
        result = target.store.load_artifact(refs["result_ref"], RESULT_MEDIA).payload
        assert intent["reserved_microusd"] > 0
        assert intent["candidate_bundle_digest"] == result["candidate_bundle_digest"]
        assert result["finance_wire_body_digest"].startswith("sha256:")
        assert "Review the admitted change and source" not in str(intent) + str(result)
        private = PrivateRecordStore(
            target.store, target.clock, retention_seconds=target.private_retention_seconds
        ).read(refs["private_ref"])
        assert private is not None
        assert private["finance_wire_body"]["contents"]
        assert "Review the admitted change and source" in str(private)
        with _as(target, "actor:other-operator", "operator"):
            with pytest.raises(AuthorizationError, match="FINANCE_EVALUATION_OWNER_REQUIRED"):
                read_static_finance_evaluation(target, event_id)
            with pytest.raises(PermissionError, match="PRIVATE_RECORD_BINDING_DENIED"):
                PrivateRecordStore(
                    target.store, target.clock,
                    retention_seconds=target.private_retention_seconds,
                ).read_owned(
                    refs["private_ref"], owner_id="actor:other-operator",
                    scope_ref=refs["intent_ref"],
                )
            with pytest.raises(AuthorizationError, match="FINANCE_EVALUATION_OWNER_REQUIRED"):
                evaluate_static_finance(
                    target, event_id=event_id, operation_id="finance-static-001",
                    provider_override=_provider(),
                )
    finally:
        if target is not workspace:
            target.close()


def test_static_finance_crash_after_intent_stays_unknown_and_never_redispatches(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    from orgrebase.workspace.advisory import WorkspaceChangeAdvisoryAdapter

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt("simulated process interruption after durable intent")

    monkeypatch.setattr(WorkspaceChangeAdvisoryAdapter, "run", interrupted)
    with _as(workspace, "actor:finance-evaluator", "operator"):
        with pytest.raises(KeyboardInterrupt):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="finance-static-unknown",
                provider_override=_provider(),
            )
        assert read_static_finance_evaluation(workspace, event_id)["status"] == "RESULT_UNKNOWN"
        same = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="finance-static-unknown",
            provider_override=_provider(),
        )
        assert same["status"] == "RESULT_UNKNOWN"
        with pytest.raises(IntegrityError, match="FINANCE_EVALUATION_CASE_ALREADY_BOUND"):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="finance-static-other",
                provider_override=_provider(),
            )
    from orgrebase.workspace.service import WorkspaceService

    restarted = WorkspaceService(
        store_path=workspace.store.path,
        runtime_configuration=workspace.runtime_configuration,
        clock=workspace.clock,
        review_duration_seconds=0,
    )
    try:
        with _as(restarted, "actor:finance-evaluator", "operator"):
            assert evaluate_static_finance(
                restarted, event_id=event_id, operation_id="finance-static-unknown",
                provider_override=_provider(),
            )["status"] == "RESULT_UNKNOWN"
    finally:
        restarted.close()
    assert sent == []


def test_static_finance_requires_distinct_authenticated_operator(workspace):
    event_id, _ = _prepare(workspace)
    with _as(workspace, "actor:finance-governor", "governor", "operator"), pytest.raises(
        IntegrityError, match="FINANCE_EVALUATION_STATIC_HEAD_REQUIRED"
    ):
        evaluate_static_finance(
            workspace, event_id=event_id, operation_id="finance-static-governor",
            provider_override=_provider(),
        )
    assert workspace.store.load_artifacts(
        [finance_evaluation_refs(workspace, event_id)["intent_ref"]], INTENT_MEDIA
    ) == {}


def test_static_finance_transport_unknown_keeps_reserved_cost_and_one_identity(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sends = []

    def ambiguous(http_request: urllib.request.Request, *, timeout: float):
        del timeout
        sends.append(http_request.data)
        raise TimeoutError("fake timeout after send may have occurred")

    monkeypatch.setattr(urllib.request, "urlopen", ambiguous)
    with _as(workspace, "actor:finance-evaluator", "operator"):
        first = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="finance-static-ambiguous",
            provider_override=_provider(),
        )
        assert first["status"] == "RESULT_UNKNOWN"
        assert first["reserved_microusd"] > 0
        assert first["physical_attempts_observed"] == 1
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="finance-static-ambiguous",
            provider_override=_provider(),
        ) == first
        with pytest.raises(IntegrityError, match="FINANCE_EVALUATION_CASE_ALREADY_BOUND"):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="finance-static-fresh",
                provider_override=_provider(),
            )
    assert len(sends) == 1


def test_static_finance_revoked_query_after_send_holds_and_does_not_store_candidate(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    refs = finance_evaluation_refs(workspace, event_id)
    sent = []
    normal = _fake_send(sent, workspace)

    def revoke_during_send(http_request: urllib.request.Request, *, timeout: float):
        response = normal(http_request, timeout=timeout)
        PrivateRecordStore(
            workspace.store, workspace.clock,
            retention_seconds=workspace.private_retention_seconds,
        ).erase(
            f"finance-evaluation-query:{refs['guard']}",
            actor_id="actor:finance-evaluator",
        )
        return response

    monkeypatch.setattr(urllib.request, "urlopen", revoke_during_send)
    with _as(workspace, "actor:finance-evaluator", "operator"):
        result = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="finance-static-revoked",
            provider_override=_provider(),
        )
    assert result["status"] == "HOLD"
    assert result["quality_status"] == "NOT_EVALUATED"
    assert len(sent) == 1
    private = PrivateRecordStore(
        workspace.store, workspace.clock,
        retention_seconds=workspace.private_retention_seconds,
    ).read(refs["private_ref"])
    assert private is not None
    assert private["collaboration"] is None
    assert private["finance_wire_body"] is None
    assert "Review the admitted change and source" not in str(private)
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_static_finance_rejects_missing_price_contract_before_intent_or_send(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    provider = _provider()
    provider.model_budget = None
    with _as(workspace, "actor:finance-evaluator", "operator"), pytest.raises(
        IntegrityError, match="FINANCE_EVALUATION_PRICE_OR_MODEL_REQUIRED"
    ):
        evaluate_static_finance(
            workspace, event_id=event_id, operation_id="no-price-contract",
            provider_override=provider,
        )
    assert sent == []
    assert workspace.store.load_artifacts(
        [finance_evaluation_refs(workspace, event_id)["intent_ref"]], INTENT_MEDIA,
    ) == {}


def test_static_finance_private_record_corruption_fails_readback_without_redispatch(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        first = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="corrupt-private-result",
            provider_override=_provider(),
        )
        assert first["status"] == "PROTOCOL_VALID"
        refs = finance_evaluation_refs(workspace, event_id)
        with workspace.store.transaction() as connection:
            workspace.store.execute(
                connection,
                update(private_records)
                .where(private_records.c.record_id == refs["private_ref"])
                .values(
                    content_json='{"tampered":true}',
                    content_digest=sha256_digest({"tampered": True}),
                ),
            )
        with pytest.raises(IntegrityError, match="FINANCE_EVALUATION_PRIVATE_RESULT_MISMATCH"):
            read_static_finance_evaluation(workspace, event_id)
        with workspace.store.transaction() as connection:
            workspace.store.execute(
                connection,
                update(private_records)
                .where(private_records.c.record_id == refs["private_ref"])
                .values(content_json='{"tampered":"corrupt"}'),
            )
        with pytest.raises(IntegrityError, match="PRIVATE_RECORD_DIGEST_MISMATCH"):
            read_static_finance_evaluation(workspace, event_id)
        with pytest.raises(IntegrityError, match="PRIVATE_RECORD_DIGEST_MISMATCH"):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="corrupt-private-result",
                provider_override=_provider(),
            )
    assert len(sent) == 2
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_deleted_private_result_is_reported_as_unavailable_and_replay_does_not_send(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        original = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="deleted-private-result",
            provider_override=_provider(),
        )
        assert PrivateRecordStore(
            workspace.store, workspace.clock,
            retention_seconds=workspace.private_retention_seconds,
        ).erase(original["private_record_ref"], actor_id="actor:finance-evaluator")
        history = read_static_finance_evaluation(workspace, event_id)
        assert history["private_record_status"] == "DELETED"
        assert history["quality_status"] == "NOT_EVALUATED"
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="deleted-private-result",
            provider_override=_provider(),
        ) == history
    assert len(sent) == 2


def test_controlled_arm_rejects_candidate_from_another_head_before_wire(
    workspace, tmp_path, monkeypatch,
):
    event_id, head = _prepare(workspace)
    service, family_ref = _controlled_family(workspace, event_id, head)
    foreign_tenant = "tenant:foreign-finance-candidate"
    with StateStore(tmp_path / "foreign-finance.sqlite3", tenant_id=foreign_tenant) as store:
        with _as_tenant(foreign_tenant, "actor:other-governor", "governor"):
            foreign_service = FinanceSkillHeadService(store)
            foreign_head = foreign_service.bootstrap()
        foreign_candidate = foreign_service.prepare_instruction_patch(
            foreign_head.bundle.instruction_text + "Review source support.\n",
            expected_head_ref=foreign_head.head_ref,
            expected_head_digest=foreign_head.head_digest,
            expected_generation=foreign_head.generation,
            expected_package_digest=foreign_head.package_digest,
        )
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:experiment-operator", "operator"), pytest.raises(
        IntegrityError, match="WORKSPACE_FINANCE_EVALUATION_INPUT_MISMATCH"
    ):
        evaluate_registered_finance_arm(
            workspace, ledger=service, family_ref=family_ref,
            event_id=event_id, seed=11, order="CHRONOLOGICAL",
            arm="HUMAN_REVIEWED", provider_override=_provider(),
            candidate_bundle=foreign_candidate,
        )
    assert sent == []
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_completed_static_evaluation_restarts_from_original_identity_without_redispatch(
    workspace, monkeypatch,
):
    from orgrebase.workspace.service import WorkspaceService

    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        first = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="complete-then-restart",
            provider_override=_provider(),
        )
    assert first["status"] == "PROTOCOL_VALID"
    assert len(sent) == 2
    restarted = WorkspaceService(
        store_path=workspace.store.path,
        runtime_configuration=workspace.runtime_configuration,
        clock=workspace.clock, review_duration_seconds=0,
    )
    try:
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *_args, **_kwargs: pytest.fail("completed operation was redispatched"),
        )
        with _as(restarted, "actor:finance-evaluator", "operator"):
            assert read_static_finance_evaluation(restarted, event_id) == first
            assert evaluate_static_finance(
                restarted, event_id=event_id, operation_id="complete-then-restart",
                provider_override=_provider(),
            ) == first
            with pytest.raises(IntegrityError, match="FINANCE_EVALUATION_CASE_ALREADY_BOUND"):
                evaluate_static_finance(
                    restarted, event_id=event_id, operation_id="new-operation-after-restart",
                    provider_override=_provider(),
                )
        assert restarted._approval_record(event_id) is None
        assert restarted._outcome_record(event_id) is None
    finally:
        restarted.close()


def test_current_source_revocation_during_finance_wire_holds_result_and_private_output(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    refs = finance_evaluation_refs(workspace, event_id)
    sent = []
    normal = _fake_send(sent, workspace)

    def revoke_source(http_request: urllib.request.Request, *, timeout: float):
        response = normal(http_request, timeout=timeout)
        workspace.changes.invalidate(
            "currency", "source:currency-revoked-during-finance", "PERMISSION_DENIED",
        )
        return response

    monkeypatch.setattr(urllib.request, "urlopen", revoke_source)
    with _as(workspace, "actor:finance-evaluator", "operator"):
        result = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="source-revoked-during-send",
            provider_override=_provider(),
        )
        assert result["status"] == "HOLD"
        assert result["quality_status"] == "NOT_EVALUATED"
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="source-revoked-during-send",
            provider_override=_provider(),
        ) == result
    private = PrivateRecordStore(
        workspace.store, workspace.clock,
        retention_seconds=workspace.private_retention_seconds,
    ).read(refs["private_ref"])
    assert private is not None
    assert private["finance_wire_body"] is None
    assert private["collaboration"] is None
    assert len(sent) == 1
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_too_small_price_ceiling_fails_before_intent_or_network(workspace, monkeypatch):
    event_id, _ = _prepare(workspace)
    provider = _provider()
    provider.model_budget = ModelBudget.model_validate({
        **provider.model_budget.model_dump(mode="json"),
        "max_preview_usd": "0.00000001",
    })
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("insufficient budget dispatched a model"),
    )
    with _as(workspace, "actor:finance-evaluator", "operator"), pytest.raises(
        AdvisoryGenerationError, match="WORKSPACE_ADVISORY_COST_LIMIT_EXCEEDED"
    ):
        evaluate_static_finance(
            workspace, event_id=event_id, operation_id="price-ceiling-too-small",
            provider_override=provider,
        )
    assert workspace.store.load_artifacts(
        [finance_evaluation_refs(workspace, event_id)["intent_ref"]], INTENT_MEDIA,
    ) == {}


def test_wrong_observed_vertex_model_cannot_be_a_valid_finance_receipt(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    normal = _fake_send(sent, workspace)

    def wrong_model(http_request: urllib.request.Request, *, timeout: float):
        response = normal(http_request, timeout=timeout)
        payload = json.loads(response.payload)
        payload["modelVersion"] = "gemini-unreviewed-model"
        return _Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", wrong_model)
    with _as(workspace, "actor:finance-evaluator", "operator"):
        result = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="wrong-model-response",
            provider_override=_provider(),
        )
        assert result["status"] == "HOLD"
        assert result["quality_status"] == "NOT_EVALUATED"
        assert result["target_writes"] == 0
        assert result["physical_attempts_observed"] == 1
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="wrong-model-response",
            provider_override=_provider(),
        ) == result
    assert len(sent) == 1
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_unsupported_finance_source_ref_is_rejected_after_real_v4_wire(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    normal = _fake_send(sent, workspace)

    def invented_source(http_request: urllib.request.Request, *, timeout: float):
        response = normal(http_request, timeout=timeout)
        payload = json.loads(response.payload)
        candidate = json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])
        candidate["source_refs"] = ["source:invented-by-model"]
        payload["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(candidate)
        return _Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", invented_source)
    with _as(workspace, "actor:finance-evaluator", "operator"):
        result = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="unsupported-finance-source",
            provider_override=_provider(),
        )
        assert result["status"] == "HOLD"
        assert result["quality_status"] == "NOT_EVALUATED"
        assert result["target_writes"] == 0
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="unsupported-finance-source",
            provider_override=_provider(),
        ) == result
    assert len(sent) == 1  # Finance fails before the dependent GTM task is sent.
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_historical_valid_result_reports_current_source_hold_without_redispatch(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        historical = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="historical-then-source-revoked",
            provider_override=_provider(),
        )
        assert historical["status"] == "PROTOCOL_VALID"
        workspace.changes.invalidate(
            "currency", "source:revoked-after-evaluation", "PERMISSION_DENIED",
        )
        current = read_static_finance_evaluation(workspace, event_id)
        assert current["status"] == "PROTOCOL_VALID"
        assert current["quality_status"] == "NOT_EVALUATED"
        assert current["current_qualification"] == "HOLD"
        assert current["current_reason_code"]
        assert "source:revoked-after-evaluation" not in str(current)
        assert evaluate_static_finance(
            workspace, event_id=event_id,
            operation_id="historical-then-source-revoked",
            provider_override=_provider(),
        ) == current
    assert len(sent) == 2
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_historical_valid_result_reports_current_head_hold_when_resolution_fails(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        historical = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="historical-then-head-unavailable",
            provider_override=_provider(),
        )
        assert historical["status"] == "PROTOCOL_VALID"
        monkeypatch.setattr(
            FinanceSkillHeadService, "resolve",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                IntegrityError("FINANCE_HEAD_UNAVAILABLE")
            ),
        )
        current = read_static_finance_evaluation(workspace, event_id)
        assert current["status"] == "PROTOCOL_VALID"
        assert current["quality_status"] == "NOT_EVALUATED"
        assert current["current_qualification"] == "HOLD"
        assert current["current_reason_code"]
        assert evaluate_static_finance(
            workspace, event_id=event_id,
            operation_id="historical-then-head-unavailable",
            provider_override=_provider(),
        ) == current
    assert len(sent) == 2


def test_completed_evaluation_rejects_cross_bound_intent_and_result_rows(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        original_read = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="cross-bound-readback",
            provider_override=_provider(),
        )
        assert original_read["status"] == "PROTOCOL_VALID"
        refs = finance_evaluation_refs(workspace, event_id)
        intent = workspace.store.load_artifact(refs["intent_ref"], INTENT_MEDIA).payload
        result = workspace.store.load_artifact(refs["result_ref"], RESULT_MEDIA).payload
        probes = (
            (refs["intent_ref"], intent, {"event_id": "another-event"},
             "FINANCE_EVALUATION_INTENT_INVALID"),
            (refs["intent_ref"], intent, {"reserved_microusd": intent["reserved_microusd"] + 1},
             "FINANCE_EVALUATION_RESULT_INVALID"),
            (refs["result_ref"], result, {"intent_digest": sha256_digest("other-intent")},
             "FINANCE_EVALUATION_RESULT_INVALID"),
            (refs["result_ref"], result, {"private_record_ref": "finance-evaluation-private:other"},
             "FINANCE_EVALUATION_RESULT_INVALID"),
            (refs["result_ref"], result, {"budget_contract_digest": sha256_digest("other-price")},
             "FINANCE_EVALUATION_RESULT_INVALID"),
            (refs["result_ref"], result, {"candidate_bundle_digest": sha256_digest("other-head")},
             "FINANCE_EVALUATION_RESULT_INVALID"),
        )
        for ref, original, changed, reason in probes:
            _rewrite_artifact_for_integrity_probe(workspace, ref, {**original, **changed})
            with pytest.raises(IntegrityError, match=reason):
                read_static_finance_evaluation(workspace, event_id)
            _rewrite_artifact_for_integrity_probe(workspace, ref, original)
            assert read_static_finance_evaluation(workspace, event_id) == original_read
        snapshot_ref = intent["snapshot_ref"]
        original_snapshot = workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload
        different_valid_snapshot = MemorySnapshot.model_validate({
            **{key: value for key, value in original_snapshot.items() if key != "digest"},
            "retrieval_version": "controlled-drift-probe.v2",
        })
        _rewrite_artifact_for_integrity_probe(
            workspace, snapshot_ref, different_valid_snapshot.model_dump(mode="json"),
        )
        stale = read_static_finance_evaluation(workspace, event_id)
        assert stale["status"] == "PROTOCOL_VALID"
        assert stale["current_qualification"] == "HOLD"
        assert stale["current_reason_code"] == "FINANCE_EVALUATION_CURRENT_INPUT_STALE"
        _rewrite_artifact_for_integrity_probe(workspace, snapshot_ref, original_snapshot)
        assert read_static_finance_evaluation(workspace, event_id) == original_read
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="cross-bound-readback",
            provider_override=_provider(),
        ) == original_read
    assert len(sent) == 2
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_expired_price_contract_fails_before_intent_or_network(workspace, monkeypatch):
    event_id, _ = _prepare(workspace)
    provider = _provider()
    provider.model_budget = ModelBudget.model_validate({
        **provider.model_budget.model_dump(mode="json"),
        "valid_until": "2020-01-01T00:00:00Z",
    })
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("expired price dispatched a model"),
    )
    with _as(workspace, "actor:finance-evaluator", "operator"), pytest.raises(
        ValueError, match="MODEL_PRICE_CONTRACT_EXPIRED"
    ):
        evaluate_static_finance(
            workspace, event_id=event_id, operation_id="expired-price-contract",
            provider_override=provider,
        )
    refs = finance_evaluation_refs(workspace, event_id)
    assert workspace.store.load_artifacts([refs["intent_ref"]], INTENT_MEDIA) == {}
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None


def test_static_finance_preflight_requires_valid_identity_input_and_private_retention(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    with pytest.raises(AuthenticationError, match="FINANCE_EVALUATION_PRINCIPAL_REQUIRED"):
        read_static_finance_evaluation(workspace, event_id)
    principal = Principal(
        issuer="https://issuer.example", subject="subject:actor:finance-evaluator",
        tenant_id=workspace.profile.organization_id,
        actor_id="actor:finance-evaluator", roles=frozenset({"operator"}),
        expires_at=4_000_000_000,
    )
    token = request_principal.set(principal)
    authorization_token = request_authorization.set(None)
    try:
        with pytest.raises(
            AuthenticationError, match="FINANCE_EVALUATION_CURRENT_AUTHORIZATION_REQUIRED"
        ):
            read_static_finance_evaluation(workspace, event_id)
    finally:
        request_authorization.reset(authorization_token)
        request_principal.reset(token)
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid preflight dispatched a model"),
    )
    with _as(workspace, "actor:finance-evaluator", "operator"):
        assert read_static_finance_evaluation(workspace, event_id) == {
            "status": "NOT_STARTED", "event_id": event_id, "target_writes": 0,
        }
        with pytest.raises(ValueError, match="FINANCE_EVALUATION_EVENT_ID_INVALID"):
            finance_evaluation_refs(workspace, "bad/event")
        with pytest.raises(ValueError, match="FINANCE_EVALUATION_OPERATION_ID_INVALID"):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="bad/operation",
                provider_override=_provider(),
            )
        with pytest.raises(IntegrityError, match="FINANCE_EVALUATION_VERTEX_PROVIDER_REQUIRED"):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="wrong-provider",
                provider_override=object(),
            )
        previous_retention = workspace.private_retention_seconds
        workspace.private_retention_seconds = 0
        try:
            with pytest.raises(
                IntegrityError, match="FINANCE_EVALUATION_PRIVATE_RETENTION_REQUIRED"
            ):
                evaluate_static_finance(
                    workspace, event_id=event_id, operation_id="disabled-retention",
                    provider_override=_provider(),
                )
        finally:
            workspace.private_retention_seconds = previous_retention
    refs = finance_evaluation_refs(workspace, event_id)
    assert workspace.store.load_artifacts([refs["intent_ref"]], INTENT_MEDIA) == {}


def test_completed_evaluation_keeps_history_when_preview_becomes_approved(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        historical = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="preview-then-approved",
            provider_override=_provider(),
        )
        assert historical["current_qualification"] == "CURRENT_INPUTS"
        preview_digest = workspace._preview_record(event_id)["bundle"]["preview"]["digest"]
    with _as(workspace, workspace.change_owner[event_id], "approver"):
        workspace.approve_change(
            event_id, actor_id=workspace.change_owner[event_id],
            preview_digest=preview_digest,
        )
    with _as(workspace, "actor:finance-evaluator", "operator"):
        current = read_static_finance_evaluation(workspace, event_id)
        assert current["status"] == "PROTOCOL_VALID"
        assert current["current_qualification"] == "HOLD"
        assert current["current_reason_code"] == "FINANCE_EVALUATION_CURRENT_INPUT_STALE"
        assert current["quality_status"] == "NOT_EVALUATED"
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="preview-then-approved",
            provider_override=_provider(),
        ) == current
    assert len(sent) == 2
    assert workspace._outcome_record(event_id) is None


def test_completed_static_evaluation_holds_after_current_head_advances(
    workspace, monkeypatch,
):
    event_id, genesis = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace, "actor:finance-evaluator", "operator"):
        historical = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="head-then-advanced",
            provider_override=_provider(),
        )
        assert historical["current_qualification"] == "CURRENT_INPUTS"
    service = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id,
    )
    candidate = service.prepare_instruction_patch(
        genesis.bundle.instruction_text + "Review current Finance sources.\n",
        expected_head_ref=genesis.head_ref,
        expected_head_digest=genesis.head_digest,
        expected_generation=genesis.generation,
        expected_package_digest=genesis.package_digest,
    )
    # Controlled head movement only: these test artifacts do not prove real qualification.
    qualification_ref = "finance-skill-qualification:head-drift-probe"
    qualification = {"status": "CONTROLLED_FIXTURE", "candidate_bundle_digest": candidate.digest}
    release_ref = "finance-content-release:head-drift-probe"
    release = {"qualification_ref": qualification_ref, "candidate_bundle_digest": candidate.digest}
    previous = service._current_source()
    promoted = service._head_object(
        generation=1, transition_kind="PROMOTE", bundle=candidate,
        previous=previous, actor_id="actor:finance-governor",
        qualification_status="QUALIFIED", qualification_ref=qualification_ref,
        content_release_ref=release_ref,
    )
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, service._bundle_ref(candidate), CONTENT_MEDIA, candidate.payload)
        workspace.store.save_artifact(connection, qualification_ref, QUALIFICATION_MEDIA, qualification)
        workspace.store.save_artifact(connection, release_ref, CONTENT_RELEASE_MEDIA, release)
        workspace.store.insert_version(connection, promoted, make_current=False)
        workspace.store.promote_version(connection, service.head_id, previous.version, promoted.version)
    assert service.resolve().generation == 1
    with _as(workspace, "actor:finance-evaluator", "operator"):
        current = read_static_finance_evaluation(workspace, event_id)
        assert current["status"] == "PROTOCOL_VALID"
        assert current["current_qualification"] == "HOLD"
        assert current["current_reason_code"] == "FINANCE_EVALUATION_CURRENT_INPUT_STALE"
        assert current["quality_status"] == "NOT_EVALUATED"
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="head-then-advanced",
            provider_override=_provider(),
        ) == current
    assert len(sent) == 2
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None
