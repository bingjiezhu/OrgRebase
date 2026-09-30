"""P0 post-Apply evaluation is durable and cannot become production adoption."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from orgrebase.auth import Principal, request_authorization, request_principal
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.quote_recovery_learning import quote_recovery_content_bundle
from orgrebase.workspace.quote_recovery_use import (
    QuoteRecoveryEvaluationCommand,
    compare_quote_next_step_actions,
    evaluate_quote_recovery_candidate,
    read_quote_recovery_evaluation,
)
from orgrebase.workspace.routes import change_router
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_packages import SkillCandidateOverlayRegistry
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_continuous_changes import apply, proposal
from tests.workspace.test_enterprise_pilot_delivery import _make_distinct_enterprise_pack
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    CORPUS,
    EVALUATOR,
    GOVERNOR,
    TENANT,
    WORKSPACE,
    _identity,
    _prepare,
    _service,
)
from tests.workspace.test_quote_recovery_operations import _recovery_workspace


def _candidate(store):
    service = _service(store)
    proposal, boundary = _prepare(service)
    bundle = quote_recovery_content_bundle(service.registry)
    (candidate_ref,) = service.propose(
        proposal, actor_id=AUTHOR, boundary=boundary, content_bundle=bundle.payload,
    )
    assert not service._family("decision")
    assert not service._family("admission")
    return service, candidate_ref


def _policy(path, candidate_ref, *, enabled=True):
    body = {
        "schema_version": "orgrebase.quote-recovery-evaluation-policy.v1",
        "enabled": enabled, "policy_revision": "p0-evaluation-v1",
        "organization_id": TENANT, "workspace_id": WORKSPACE,
        "candidate_ref": candidate_ref,
        "corpus_actor_id": CORPUS, "author_actor_id": AUTHOR,
        "evaluator_actor_id": EVALUATOR, "governor_actor_id": GOVERNOR,
        "not_after_epoch": 4_000_000_000,
        "max_total_attempts": 10, "max_attempts_per_case": 3,
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _command(event_id, candidate_ref, *, operation="p0-evaluation-1",
             attempt="attempt-1", previous=None):
    return QuoteRecoveryEvaluationCommand(
        operation_key=operation, event_id=event_id,
        attempt_id=attempt, candidate_ref=candidate_ref,
        previous_selection_ref=previous,
    )


def test_unpublished_candidate_uses_same_consumer_without_adoption(tmp_path, monkeypatch):
    with StateStore(tmp_path / "evaluation.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        service, candidate_ref = _candidate(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "evaluation.json", candidate_ref)
        command = _command(event.event_id, candidate_ref)
        checks = []
        with _identity(EVALUATOR, frozenset({"governor"}), checks):
            monkeypatch.delenv("ORGREBASE_QUOTE_RECOVERY_EVALUATION_CONFIG", raising=False)
            with pytest.raises(IntegrityError, match="QUOTE_EVALUATION_DISABLED"):
                evaluate_quote_recovery_candidate(workspace, command)
            assert not store.list_artifacts(artifact_id_prefix="quote-recovery-eval-selection:")
            first = evaluate_quote_recovery_candidate(workspace, command, config_path=policy)
            assert evaluate_quote_recovery_candidate(workspace, command, config_path=policy) == first
        assert first["execution_mode"] == "EVALUATION_ONLY"
        assert first["status"] == "CONSUMED"
        assert first["action"] == "HANDOFF"
        assert first["oracle_status"] == "ACTION_MATCH_ONLY"
        assert first["qualification_status"] == "NOT_QUALIFIED"
        assert first["quality_status"] == "NOT_EVALUATED"
        assert first["behavior_delta_status"] == "NO_BEHAVIOR_DELTA"
        assert first["consumed_resource_digests"]
        assert set(first["consumed_resource_digests"]) <= set(first["loaded_resource_digests"])
        assert first["model_invocations"] == first["model_cost_usd"] == 0
        assert not service._family("invocation-result")
        assert not service._family("admission")
        assert store.verify_event_chain()["status"] == "PASS"
        _policy(policy, candidate_ref, enabled=False)
        with _identity(EVALUATOR, frozenset({"reader"}), checks):
            assert read_quote_recovery_evaluation(workspace, first["selection_ref"]) == first
        with _identity(AUTHOR, frozenset({"reader"}), checks), pytest.raises(
            AuthorizationError, match="READER_NOT_EVALUATOR"
        ):
            read_quote_recovery_evaluation(workspace, first["selection_ref"])
        with _identity(EVALUATOR, frozenset({"governor"}), checks), pytest.raises(
            IntegrityError, match="QUOTE_EVALUATION_DISABLED"
        ):
            evaluate_quote_recovery_candidate(
                workspace, _command(event.event_id, candidate_ref, operation="new", attempt="new"),
                config_path=policy,
            )


def test_new_outcome_needs_linked_new_attempt_and_old_abstain_is_frozen(tmp_path, monkeypatch):
    with StateStore(tmp_path / "attempts.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        _, candidate_ref = _candidate(store)
        workspace, event, detail = _recovery_workspace(store, monkeypatch)
        detail["status"] = "READY_FOR_REVIEW"
        detail["outcome"] = None
        policy = _policy(tmp_path / "evaluation.json", candidate_ref)
        first_command = _command(event.event_id, candidate_ref)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            first = evaluate_quote_recovery_candidate(workspace, first_command, config_path=policy)
        assert first["status"] == "CONSUMED"
        assert first["action"] == "ABSTAIN"
        assert first["outcome_artifact_digest"] is None
        detail["status"] = "APPLIED"
        detail["outcome"] = {"artifact_digest": "sha256:" + "a" * 64}
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            assert evaluate_quote_recovery_candidate(workspace, first_command, config_path=policy) == first
            with pytest.raises(IntegrityError, match="PREVIOUS_ATTEMPT_REQUIRED"):
                evaluate_quote_recovery_candidate(
                    workspace,
                    _command(event.event_id, candidate_ref, operation="second", attempt="attempt-2"),
                    config_path=policy,
                )
            second = evaluate_quote_recovery_candidate(
                workspace,
                _command(event.event_id, candidate_ref, operation="second", attempt="attempt-2",
                         previous=first["selection_ref"]),
                config_path=policy,
            )
        assert second["status"] == "CONSUMED"
        assert second["action"] == "HANDOFF"
        assert second["previous_selection_ref"] == first["selection_ref"]
        assert second["case_revision_digest"] != first["case_revision_digest"]
        assert store.verify_event_chain()["status"] == "PASS"


def test_reason_hash_and_copy_change_do_not_count_as_next_step_improvement():
    from orgrebase.workspace.quote_recovery_use import NEXT_STEP_RUBRIC_DIGEST

    oracle = {"rubric_digest": NEXT_STEP_RUBRIC_DIGEST, "expected_action": "ABSTAIN"}
    before = {
        "action": "ABSTAIN", "reason": "Need source outcome",
        "output_digest": "sha256:" + "a" * 64,
    }
    renamed = {
        "action": "ABSTAIN", "reason": "A longer approved sounding explanation",
        "output_digest": "sha256:" + "b" * 64,
    }
    assert compare_quote_next_step_actions(oracle, before, renamed) == "NO_BEHAVIOR_DELTA"
    assert compare_quote_next_step_actions(
        oracle, {"action": "HANDOFF"}, renamed,
    ) == "ACTION_FIX_REQUIRES_INDEPENDENT_REVIEW"


def test_authenticated_http_evaluation_and_other_actor_read_denial(tmp_path, monkeypatch):
    with StateStore(tmp_path / "http.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        _, candidate_ref = _candidate(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "evaluation.json", candidate_ref)
        monkeypatch.setenv("ORGREBASE_QUOTE_RECOVERY_EVALUATION_CONFIG", str(policy))
        app = FastAPI()
        app.include_router(change_router(lambda: workspace))
        identities = {"eval": (EVALUATOR, "governor"), "author": (AUTHOR, "reader")}

        @app.middleware("http")
        async def verified_test_token(request: Request, call_next):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in identities:
                return JSONResponse({"error": "AUTH_REQUIRED"}, status_code=401)
            actor, role = identities[token]
            principal = Principal(
                "https://issuer.example", f"subject:{actor}", TENANT,
                actor, frozenset({role}), 4_000_000_000,
            )
            principal_token = request_principal.set(principal)
            authorization_token = request_authorization.set(lambda: None)
            try:
                return await call_next(request)
            finally:
                request_authorization.reset(authorization_token)
                request_principal.reset(principal_token)

        @app.exception_handler(AuthorizationError)
        async def authorization_denied(_request: Request, error: AuthorizationError):
            return JSONResponse({"error": str(error)}, status_code=403)

        path = "/api/workspace/governed-learning/quote-recovery-evaluations"
        command = _command(event.event_id, candidate_ref)
        with TestClient(app) as client:
            assert client.post(path, json=command.model_dump(mode="json")).status_code == 401
            created = client.post(
                path, json=command.model_dump(mode="json"),
                headers={"Authorization": "Bearer eval"},
            )
            assert created.status_code == 200, created.text
            selection_ref = created.json()["selection_ref"]
            assert created.json()["status"] == "CONSUMED"
            assert created.json()["execution_mode"] == "EVALUATION_ONLY"
            denied = client.get(
                f"{path}/{selection_ref}",
                headers={"Authorization": "Bearer author"},
            )
            assert denied.status_code == 403
            assert "QUOTE_EVALUATION_READER_NOT_EVALUATOR" in denied.text
            reread = client.get(
                f"{path}/{selection_ref}",
                headers={"Authorization": "Bearer eval"},
            )
            assert reread.status_code == 200
            assert reread.json() == created.json()


def test_crash_after_selection_is_unknown_and_cannot_change_attempt_id(tmp_path, monkeypatch):
    with StateStore(tmp_path / "unknown.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        _, candidate_ref = _candidate(store)
        workspace, event, detail = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "evaluation.json", candidate_ref)
        command = _command(event.event_id, candidate_ref)
        original = SkillCandidateOverlayRegistry.invoke_for_evaluation

        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt("test-only simulated process death")

        monkeypatch.setattr(SkillCandidateOverlayRegistry, "invoke_for_evaluation", interrupt)
        with _identity(EVALUATOR, frozenset({"governor"}), []), pytest.raises(KeyboardInterrupt):
            evaluate_quote_recovery_candidate(workspace, command, config_path=policy)
        monkeypatch.setattr(SkillCandidateOverlayRegistry, "invoke_for_evaluation", original)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            unknown = evaluate_quote_recovery_candidate(workspace, command, config_path=policy)
            assert unknown["status"] == "RESULT_UNKNOWN"
            detail["outcome"] = {"artifact_digest": "sha256:" + "b" * 64}
            with pytest.raises(IntegrityError, match="PREDECESSOR_NOT_TERMINAL_OR_NEW"):
                evaluate_quote_recovery_candidate(
                    workspace,
                    _command(event.event_id, candidate_ref, operation="after-unknown",
                             attempt="attempt-2", previous=unknown["selection_ref"]),
                    config_path=policy,
                )
        assert store.verify_event_chain()["status"] == "PASS"


def test_policy_revoked_after_consumer_rejects_result_and_preserves_history(
    tmp_path, monkeypatch,
):
    with StateStore(tmp_path / "revoked.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        _, candidate_ref = _candidate(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "evaluation.json", candidate_ref)
        original = SkillCandidateOverlayRegistry.invoke_for_evaluation

        def revoke(selected, *args, **kwargs):
            result = original(selected, *args, **kwargs)
            _policy(policy, candidate_ref, enabled=False)
            return result

        monkeypatch.setattr(SkillCandidateOverlayRegistry, "invoke_for_evaluation", revoke)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            rejected = evaluate_quote_recovery_candidate(
                workspace, _command(event.event_id, candidate_ref), config_path=policy,
            )
        assert rejected["status"] == "REJECTED"
        assert rejected["action"] is None
        assert rejected["oracle_status"] == "NOT_EVALUATED"
        with _identity(EVALUATOR, frozenset({"reader"}), []):
            assert read_quote_recovery_evaluation(workspace, rejected["selection_ref"]) == rejected


def test_postgres_restarts_read_exact_evaluation_without_redispatch(
    postgres_runtime, tmp_path, monkeypatch,
):
    database = postgres_runtime(tenant_id=TENANT)
    policy = tmp_path / "evaluation.json"
    with StateStore(database["runtime_dsn"], tenant_id=TENANT, migrate=False) as first_store:
        _, candidate_ref = _candidate(first_store)
        workspace, event, _ = _recovery_workspace(first_store, monkeypatch)
        _policy(policy, candidate_ref)
        command = _command(event.event_id, candidate_ref)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            first = evaluate_quote_recovery_candidate(workspace, command, config_path=policy)
        assert first["status"] == "CONSUMED"
    with StateStore(database["runtime_dsn"], tenant_id=TENANT, migrate=False) as restarted:
        workspace, _, detail = _recovery_workspace(restarted, monkeypatch, persist_receipts=False)
        detail["status"] = "REJECTED"
        detail["outcome"] = None
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            assert evaluate_quote_recovery_candidate(workspace, command, config_path=policy) == first
        assert restarted.verify_event_chain()["status"] == "PASS"


def test_real_workspace_post_apply_evaluation_consumes_without_touching_quote(
    tmp_path, monkeypatch,
):
    pack = _make_distinct_enterprise_pack(
        tmp_path / "pack", slug="quote-recovery",
        customer="evaluation-customer", product_plan="Evaluation Enterprise Plan",
        proposed_date="2027-02-15",
    )
    runtime = load_enterprise_quote_pilot_pack(pack)
    assert runtime.profile.organization_id == TENANT
    workspace = WorkspaceService(
        store_path=tmp_path / "real-workspace.sqlite",
        store_tenant_id=TENANT, runtime_configuration=runtime,
    )
    try:
        workspace.form_quote()
        for event_id in ("currency", "launch_date"):
            apply(workspace, event_id)
        configure(workspace, tmp_path / "native")
        event = proposal(
            workspace, "real-p0-evaluation", "product_plan", "Evaluation Enterprise Plan v2",
        )
        workspace.register_change(event)
        workspace.preview_change(event.event_id)
        returned = return_for_evidence(
            workspace, event.event_id, return_command(workspace, event.event_id),
        )
        resumed = resume_change(
            workspace, event.event_id, resume_command(workspace, event.event_id),
        )
        assert returned["state"] == "NEEDS_EVIDENCE"
        assert resumed["state"] == "READY_FOR_REVIEW"
        preview = workspace._preview_record(event.event_id)
        approval = workspace.approve_change(
            event.event_id, actor_id=event.owner_id,
            preview_digest=preview["preview_digest"],
            recovery_digest=resumed["recovery_digest"],
        )
        workspace.apply_approved_change(
            event.event_id, approval_digest=approval["approval_digest"],
        )
        quote_after_apply = workspace.current_quote().digest
        service, candidate_ref = _candidate(workspace.store)
        policy = _policy(tmp_path / "p0-evaluation.json", candidate_ref)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            result = evaluate_quote_recovery_candidate(
                workspace, _command(event.event_id, candidate_ref), config_path=policy,
            )
        assert result["status"] == "CONSUMED"
        assert result["action"] == "HANDOFF"
        assert result["qualification_status"] == "NOT_QUALIFIED"
        assert workspace.current_quote().digest == quote_after_apply
        assert service._family("invocation-result") == ()
    finally:
        workspace.close()
