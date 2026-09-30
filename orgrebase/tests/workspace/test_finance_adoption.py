"""Normal Finance adoption stays closed until independently qualified."""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from orgrebase.auth import request_authorization
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError
from orgrebase.impact import ImpactEngine
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.experience_governance_operations import ExperiencePhaseActors, _recall
from orgrebase.workspace.finance_adoption import (
    POLICY_ENV,
    USE_MEDIA,
    FinanceAdoptionPolicy,
    adopted_adapter_for_preview,
    configured_policy,
    finance_cluster_digest,
    prepare_normal_adoption,
    reserve_selection,
    selection_for_bundle,
)
from orgrebase.workspace.finance_skill_qualification import CONTENT_RELEASE_MEDIA, QUALIFICATION_MEDIA
from orgrebase.workspace.model_budget import ModelBudget
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_evolution_v2 import CONTENT_MEDIA, FinanceSkillHeadService
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_finance_advice_v4 import _provider
from tests.workspace.test_finance_explanation_operations import _as, _fake_send


def _policy(workspace, head, cluster, *, mode="ADOPTED"):
    return FinanceAdoptionPolicy(
        schema_version="orgrebase.finance-adoption-policy.v1",
        mode=mode, revision="finance-pilot-001",
        tenant_id=workspace.profile.organization_id,
        workspace_id=workspace.store.workspace_id,
        head_ref=head.head_ref, head_digest=head.head_digest,
        head_generation=1, package_digest=head.package_digest,
        qualification_ref="finance-qualification:unissued",
        qualification_digest=sha256_digest("unissued"),
        content_release_ref="finance-content-release:unissued",
        cluster_digests=(cluster,), runtime_actor_ids=("actor:finance-operator",),
        max_cases=1, max_calls=100, max_reserved_microusd=100_000_000,
        expires_at_epoch_s=4_000_000_000,
    )


def _save_policy(tmp_path, policy, monkeypatch):
    path = tmp_path / "finance-adoption-policy.json"
    path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv(POLICY_ENV, str(path))
    return path


def test_policy_file_rejects_writable_and_duplicate_keys(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text('{"schema_version":"orgrebase.finance-adoption-policy.v1","mode":"OFF","mode":"ADOPTED"}')
    os.chmod(path, 0o600)
    monkeypatch.setenv(POLICY_ENV, str(path))
    with pytest.raises(IntegrityError, match="POLICY_INVALID"):
        configured_policy()
    os.chmod(path, 0o666)
    with pytest.raises(IntegrityError, match="POLICY_INVALID"):
        configured_policy()


def test_policy_file_requires_regular_owned_path_and_closed_cohort(tmp_path, monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    assert configured_policy() is None
    target = tmp_path / "real-policy.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o600)
    symlink = tmp_path / "linked-policy.json"
    symlink.symlink_to(target)
    monkeypatch.setenv(POLICY_ENV, str(symlink))
    with pytest.raises(IntegrityError, match="POLICY_INVALID"):
        configured_policy()
    with pytest.raises(ValueError, match="FINANCE_ADOPTION_POLICY_SET_INVALID"):
        FinanceAdoptionPolicy.model_validate({
            "schema_version": "orgrebase.finance-adoption-policy.v1",
            "mode": "ADOPTED", "revision": "one",
            "tenant_id": "tenant:one", "workspace_id": "workspace:one",
            "head_ref": "head:one", "head_digest": sha256_digest("head"),
            "head_generation": 1, "package_digest": sha256_digest("package"),
            "qualification_ref": "qualification:one",
            "qualification_digest": sha256_digest("qualification"),
            "content_release_ref": "release:one",
            "cluster_digests": [sha256_digest("cluster"), sha256_digest("cluster")],
            "runtime_actor_ids": ["actor:one"],
            "max_cases": 1, "max_calls": 1,
            "max_reserved_microusd": 1, "expires_at_epoch_s": 4_000_000_000,
        })


@pytest.mark.parametrize("mode", ["OFF", "SHADOW_ONLY"])
def test_non_adopted_policy_never_enters_normal_preview(workspace, tmp_path, monkeypatch, mode):
    submitted = submit_change(workspace, command(workspace, slot="currency", value="EUR"))
    event_id = submitted["event"]["event_id"]
    spec = workspace.change_spec(event_id)
    fixture = workspace._fixture_for_change(spec)
    change_set = workspace.change_builder.build(fixture=fixture, spec=spec)
    cluster = finance_cluster_digest(workspace, event_id, change_set)
    fake_head = type("Head", (), {
        "head_ref": "skill-head:unissued@g00000001",
        "head_digest": sha256_digest("unissued-head"),
        "package_digest": sha256_digest("unissued-package"),
    })()
    _save_policy(tmp_path, _policy(workspace, fake_head, cluster, mode=mode), monkeypatch)
    bundle = workspace.preview_change(event_id)
    assert bundle.advisory.ingestion_receipt.target_writes == 0
    assert not workspace.store.list_artifacts(
        artifact_id_prefix="finance-adoption-selection:",
    )
    assert selection_for_bundle(workspace, event_id, bundle) is None


def test_adopted_policy_cannot_use_unqualified_genesis_or_dispatch(
    workspace, tmp_path, monkeypatch,
):
    tenant = workspace.profile.organization_id
    target = WorkspaceService(
        store_path=tmp_path / "finance-adoption.sqlite",
        store_tenant_id=tenant,
        runtime_configuration=workspace.runtime_configuration,
        clock=workspace.clock,
        review_duration_seconds=0,
        advisory_provider=_provider(),
        advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
    )
    try:
        target.form_quote()
        with _as(target, "actor:finance-governor", "governor"):
            genesis = FinanceSkillHeadService(target.store, tenant_id=tenant).bootstrap()
        event_id = submit_change(target, command(target, slot="currency", value="EUR"))["event"]["event_id"]
        spec = target.change_spec(event_id)
        fixture = target._fixture_for_change(spec)
        change_set = target.change_builder.build(fixture=fixture, spec=spec)
        preview = ImpactEngine(fixture).preview(change_set)
        cluster = finance_cluster_digest(target, event_id, change_set)
        _save_policy(tmp_path, _policy(target, genesis, cluster), monkeypatch)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="CURRENT_QUALIFICATION_REQUIRED"
        ):
            target.preview_change(event_id)
        assert target._preview_record(event_id) is None
        assert target._change_status(event_id) == "RECEIVED"
        assert not target.store.list_artifacts(
            artifact_id_prefix="finance-adoption-selection:",
        )
        assert preview.state == "READY"
    finally:
        target.close()


@contextmanager
def _controlled_adoption(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend,
):
    """Mechanism-only fixture; synthetic QUALIFIED bytes are never release evidence."""

    tenant = workspace.profile.organization_id
    storage = (
        postgres_runtime(tenant_id=tenant)["runtime_dsn"]
        if backend == "postgresql" else tmp_path / "finance-adoption-positive.sqlite"
    )
    target = WorkspaceService(
        store_path=storage,
        store_tenant_id=tenant,
        store_migrate=backend == "sqlite",
        runtime_configuration=workspace.runtime_configuration,
        clock=workspace.clock,
        review_duration_seconds=0,
        advisory_provider=_provider(),
        advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
    )
    try:
        target.form_quote()
        with _as(target, "actor:finance-governor", "governor"):
            service = FinanceSkillHeadService(target.store, tenant_id=tenant)
            genesis = service.bootstrap()
        candidate = service.prepare_instruction_patch(
            genesis.bundle.instruction_text + "\nReview the current Finance source.\n",
            expected_head_ref=genesis.head_ref,
            expected_head_digest=genesis.head_digest,
            expected_generation=0,
            expected_package_digest=genesis.package_digest,
        )
        qualification_ref = "finance-skill-qualification:controlled-fixture"
        qualification = {
            "schema_version": "orgrebase.finance-skill-qualification.v1",
            "status": "QUALIFIED", "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
        }
        release_ref = "finance-content-release:controlled-fixture"
        release = {
            "schema_version": "orgrebase.finance-content-release.v1",
            "qualification_ref": qualification_ref,
            "qualification_digest": sha256_digest(qualification),
            "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
            "adoption_enabled": False,
        }
        previous = service._current_source()
        promoted = service._head_object(
            generation=1, transition_kind="PROMOTE", bundle=candidate,
            previous=previous, actor_id="actor:finance-governor",
            qualification_status="QUALIFIED", qualification_ref=qualification_ref,
            content_release_ref=release_ref,
        )
        with target.store.transaction() as connection:
            target.store.save_artifact(connection, service._bundle_ref(candidate), CONTENT_MEDIA, candidate.payload)
            target.store.save_artifact(connection, qualification_ref, QUALIFICATION_MEDIA, qualification)
            target.store.save_artifact(connection, release_ref, CONTENT_RELEASE_MEDIA, release)
            target.store.insert_version(connection, promoted, make_current=False)
            target.store.promote_version(connection, service.head_id, previous.version, promoted.version)
        current = service.resolve()
        target.identity_issuer = "https://issuer.example"
        target.verify_membership = lambda _subject, _actor, _action: None
        event_id = submit_change(target, command(target, slot="currency", value="EUR"))["event"]["event_id"]
        spec = target.change_spec(event_id)
        fixture = target._fixture_for_change(spec)
        change_set = target.change_builder.build(fixture=fixture, spec=spec)
        cluster = finance_cluster_digest(target, event_id, change_set)
        policy = _policy(target, current, cluster).model_copy(update={
            "qualification_ref": qualification_ref,
            "qualification_digest": sha256_digest(qualification),
            "content_release_ref": release_ref,
        })
        policy_path = _save_policy(tmp_path, policy, monkeypatch)
        for name, actor in {
            "COLLECTOR": "actor:experience-collector",
            "AUTHOR": "actor:experience-author",
            "EVALUATOR": "actor:experience-evaluator",
            "REVIEWER": "actor:experience-reviewer",
            "CORPUS": "actor:experience-corpus",
        }.items():
            monkeypatch.setenv(f"ORGREBASE_EXPERIENCE_{name}_ACTOR_ID", actor)
        monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "finance-rubric-v1")
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, target))
        yield target, event_id, policy, policy_path, sent
    finally:
        target.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_controlled_qualified_fixture_freezes_real_v4_use_before_business_preview(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend,
):
    """Wiring fixture only: this hand-built head is not qualification evidence."""

    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, event_id, policy, policy_path, sent):
        tenant = target.profile.organization_id
        storage = target.store.path
        # Mechanism-only stub: production leaves this unset until the sealed
        # qualification verifier is configured by the deployment.
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            bundle = target.preview_change(event_id)
            selection = selection_for_bundle(target, event_id, bundle)
            assert selection is not None
            assert selection.execution_mode == "ADOPTED"
            assert len(sent) >= 1
            assert target.store.load_artifact(
                "finance-adoption-use:" + sha256_digest(selection.attempt_key)[7:], USE_MEDIA,
            ).payload["target_writes"] == 0
            assert target.preview_change(event_id).model_dump(mode="json") == bundle.model_dump(mode="json")
            assert len(sent) >= 1
        with _as(target, "actor:finance-executor", "executor"):
            adapted = adopted_adapter_for_preview(
                target, event_id=event_id, bundle=bundle,
                base_adapter=target.advisory_factory,
            )
            assert adapted is not None
            assert adapted[0].finance_advice.execution_mode == "ADOPTED"
        resumed = WorkspaceService(
            store_path=storage, store_tenant_id=tenant,
            store_migrate=backend == "sqlite",
            runtime_configuration=workspace.runtime_configuration,
            clock=workspace.clock, review_duration_seconds=0,
            advisory_provider=_provider(), advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
        )
        try:
            resumed.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
            with _as(resumed, "actor:finance-operator", "operator"):
                assert resumed.preview_change(event_id).model_dump(mode="json") == bundle.model_dump(mode="json")
            assert len(sent) >= 1
        finally:
            resumed.close()
        assert target.current_quote().payload["currency"] != "EUR"
        policy_path.write_text(
            json.dumps(policy.model_copy(update={"mode": "OFF"}).model_dump(mode="json")),
            encoding="utf-8",
        )
        with _as(target, target.change_owner[event_id], "approver"), pytest.raises(
            IntegrityError, match="POLICY_CHANGED"
        ):
            target.approve_change(
                event_id, actor_id=target.change_owner[event_id],
                preview_digest=bundle.preview.digest,
            )
        assert target._approval_record(event_id) is None
        assert len(sent) >= 1
        policy_path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        with _as(target, target.change_owner[event_id], "approver"):
            approval = target.approve_change(
                event_id, actor_id=target.change_owner[event_id],
                preview_digest=bundle.preview.digest,
            )
        policy_path.write_text(
            json.dumps(policy.model_copy(update={"mode": "OFF"}).model_dump(mode="json")),
            encoding="utf-8",
        )
        with _as(target, "actor:finance-executor", "executor"), pytest.raises(
            IntegrityError, match="POLICY_CHANGED"
        ):
            target.apply_approved_change(
                event_id, approval_digest=approval["approval_digest"],
            )
        assert target.current_quote().payload["currency"] != "EUR"
        policy_path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        with _as(target, "actor:finance-executor", "executor"):
            applied = target.apply_approved_change(
                event_id, approval_digest=approval["approval_digest"],
            )
            assert applied["outcome"]["quote"]["payload"]["currency"] == "EUR"


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_default_deep_verifier_rejection_leaves_no_attempt_before_test_stub(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, event_id, _policy_value, _path, sent):
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="RELEASE_EVIDENCE_INVALID",
        ):
            target.preview_change(event_id)
        assert target._preview_record(event_id) is None
        assert sent == []
        # Qualification fails before the first durable attempt or any send.
        # A test-only verifier can then exercise the wiring without reviving
        # an UNKNOWN result; production never supplies this stub.
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            bundle = target.preview_change(event_id)
        assert selection_for_bundle(target, event_id, bundle) is not None
        assert sent


@pytest.mark.parametrize(
    ("policy_change", "actor", "reason"), [
        ({"workspace_id": "other-workspace"}, "actor:finance-operator", "POLICY_SCOPE_MISMATCH"),
        ({"head_digest": sha256_digest("wrong-head")}, "actor:finance-operator", "CURRENT_QUALIFICATION_REQUIRED"),
        ({"qualification_digest": sha256_digest("wrong-qualification")}, "actor:finance-operator", "QUALIFICATION_EVIDENCE_INVALID"),
        ({}, "actor:unlisted-operator", "ACTOR_NOT_ADMITTED"),
    ],
)
def test_server_policy_and_current_evidence_reject_before_any_model_dispatch(
    workspace, tmp_path, monkeypatch, postgres_runtime, policy_change, actor, reason,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, policy, policy_path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        changed = policy.model_copy(update=policy_change)
        policy_path.write_text(json.dumps(changed.model_dump(mode="json")), encoding="utf-8")
        with _as(target, actor, "operator"), pytest.raises(IntegrityError, match=reason):
            target.preview_change(event_id)
        assert sent == []
        assert target._preview_record(event_id) is None
        assert not target.store.list_artifacts(artifact_id_prefix="finance-adoption-selection:")
        assert target.current_quote().payload["currency"] != "EUR"


@pytest.mark.parametrize("stage", ["approve", "execute"])
def test_private_query_erasure_holds_frozen_manifest_across_roles(
    workspace, tmp_path, monkeypatch, postgres_runtime, stage,
):
    """A reviewer may verify the frozen input, but cannot inherit its author's private query."""

    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy_value, _path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            bundle = target.preview_change(event_id)
            selection = selection_for_bundle(target, event_id, bundle)
            assert selection is not None
        assert len(sent) >= 1
        approver = target.change_owner[event_id]
        with _as(target, approver, "approver"):
            recall = _recall(target, ExperiencePhaseActors.from_deployment())
            with pytest.raises(IntegrityError, match="EXPERIENCE_MANIFEST_BINDING_INVALID"):
                recall.validate_manifest_for_consumption(
                    selection.manifest_ref, snapshot_ref=selection.snapshot_ref,
                    purpose=selection.profile_id, recipient="workspace-advisory",
                )
            assert adopted_adapter_for_preview(
                target, event_id=event_id, bundle=bundle,
                base_adapter=target.advisory_factory, action="approve",
            ) is not None
            if stage == "execute":
                approval = target.approve_change(
                    event_id, actor_id=approver, preview_digest=bundle.preview.digest,
                )
        with _as(target, "actor:finance-operator", "operator"):
            manifest = target.store.load_artifact(selection.manifest_ref).payload
            assert target.private_records.erase(
                manifest["query_ref"], actor_id="actor:finance-operator",
            )
        if stage == "approve":
            with _as(target, approver, "approver"), pytest.raises(
                IntegrityError, match="EXPERIENCE_MEMORY_HOLD",
            ):
                target.approve_change(
                    event_id, actor_id=approver, preview_digest=bundle.preview.digest,
                )
            assert target._approval_record(event_id) is None
        else:
            with _as(target, "actor:finance-executor", "executor"), pytest.raises(
                IntegrityError, match="EXPERIENCE_MEMORY_HOLD",
            ):
                target.apply_approved_change(
                    event_id, approval_digest=approval["approval_digest"],
                )
            assert target._outcome_record(event_id) is None
        assert target.current_quote().payload["currency"] != "EUR"
        assert len(sent) >= 1


def _prepare_for_reservation(target, event_id):
    spec = target.change_spec(event_id)
    fixture = target._fixture_for_change(spec)
    change_set = target.change_builder.build(fixture=fixture, spec=spec)
    preview = ImpactEngine(fixture).preview(change_set)
    with _as(target, "actor:finance-operator", "operator"):
        prepared = prepare_normal_adoption(
            target, event_id=event_id, fixture=fixture, change_set=change_set,
            preview=preview, envelope=target._run_envelope(spec),
            base_adapter=target.advisory_factory, recovery_digest=None,
        )
    assert prepared is not None
    return prepared


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("limit", ["case", "calls", "reserved_microusd"])
def test_policy_family_budget_rejects_second_case_before_model_dispatch(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend, limit,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, event_id, policy, policy_path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        second_id = "edit-2"
        submit_change(target, command(target, event_id=second_id, slot="currency", value="GBP"))
        second_spec = target.change_spec(second_id)
        second_fixture = target._fixture_for_change(second_spec)
        second_change = target.change_builder.build(fixture=second_fixture, spec=second_spec)
        first_spec = target.change_spec(event_id)
        first_fixture = target._fixture_for_change(first_spec)
        first_change = target.change_builder.build(fixture=first_fixture, spec=first_spec)
        first_cost = target.advisory_factory.cost_reservation(
            fixture=first_fixture, change_set=first_change,
            preview=ImpactEngine(first_fixture).preview(first_change),
        )
        assert first_cost is not None
        policy = policy.model_copy(update={
            "cluster_digests": tuple(sorted((
                policy.cluster_digests[0],
                finance_cluster_digest(target, second_id, second_change),
            ))),
            "max_cases": 1 if limit == "case" else 2,
            "max_calls": first_cost["calls"] if limit == "calls" else 100,
            "max_reserved_microusd": (
                first_cost["reserved_microusd"] if limit == "reserved_microusd"
                else 100_000_000
            ),
        })
        policy_path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        with _as(target, "actor:finance-operator", "operator"):
            first_bundle = target.preview_change(event_id)
        first_selection = selection_for_bundle(target, event_id, first_bundle)
        assert first_selection is not None
        assert len(sent) >= 1
        prepared = _prepare_for_reservation(target, second_id)
        with (
            _as(target, "actor:finance-operator", "operator"),
            target.store.transaction() as connection,
            pytest.raises(IntegrityError, match="FINANCE_ADOPTION_POLICY_BUDGET_EXHAUSTED"),
        ):
            reserve_selection(
                target, connection, prepared,
                expected_attempt_key=prepared.selection.attempt_key,
            )
        assert not target.store.list_artifacts(
            artifact_id_prefix=prepared.selection.ref,
        )
        assert target._preview_record(second_id) is None
        assert target.current_quote().payload["currency"] != "EUR"


@pytest.mark.parametrize("field", [
    "actor_id", "cluster_digest", "policy_digest", "head_ref",
    "qualification_digest", "advice_digest", "attempt_key",
])
def test_reservation_rechecks_prepared_selection_binding(
    workspace, tmp_path, monkeypatch, postgres_runtime, field,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy_value, _path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        prepared = _prepare_for_reservation(target, event_id)
        original = prepared.selection
        forged = type(original).model_validate({
            **original.model_dump(mode="json", exclude={"digest"}),
            field: (
                sha256_digest("other-value") if field.endswith("digest")
                else "workspace-preview-attempt:forged" if field == "attempt_key"
                else "actor:other" if field == "actor_id" else "other-value"
            ),
        })
        with (
            _as(target, "actor:finance-operator", "operator"),
            target.store.transaction() as connection,
            pytest.raises(
                IntegrityError,
                match=r"FINANCE_ADOPTION_(SELECTION_SCOPE_INVALID|ATTEMPT_BINDING_INVALID)",
            ),
        ):
            reserve_selection(
                target, connection, replace(prepared, selection=forged),
                expected_attempt_key=forged.attempt_key,
            )
        assert sent == []
        assert not target.store.list_artifacts(artifact_id_prefix="finance-adoption-selection:")


def test_ambiguous_provider_result_cannot_reuse_or_rename_attempt(
    workspace, tmp_path, monkeypatch, postgres_runtime,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy_value, _path, _sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        wire_sends = []

        def ambiguous(http_request, *, timeout):
            assert timeout > 0
            wire_sends.append(http_request.data)
            raise TimeoutError("fake ambiguous transport after possible send")

        monkeypatch.setattr(urllib.request, "urlopen", ambiguous)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="WORKSPACE_ADVISORY_MODEL_INCOMPLETE",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1
        assert target._preview_record(event_id) is None
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match=r"(RESULT_UNKNOWN|IN_PROGRESS)",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1
        target.workflow_run_nonce = "nonce:forged-retry"
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="FINANCE_ADOPTION_EVENT_ATTEMPT_BOUND",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1


@pytest.mark.parametrize("new_nonce", [False, True])
def test_unknown_attempt_cannot_fall_back_to_off_policy(
    workspace, tmp_path, monkeypatch, postgres_runtime, new_nonce,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, policy, policy_path, _sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        wire_sends = []

        def ambiguous(http_request, *, timeout):
            assert timeout > 0
            wire_sends.append(http_request.data)
            raise TimeoutError("controlled transport ambiguity")

        monkeypatch.setattr(urllib.request, "urlopen", ambiguous)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="WORKSPACE_ADVISORY_MODEL_INCOMPLETE",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1
        policy_path.write_text(
            json.dumps(policy.model_copy(update={"mode": "OFF"}).model_dump(mode="json")),
            encoding="utf-8",
        )
        if new_nonce:
            target.workflow_run_nonce = "nonce:off-policy-retry"
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match=r"FINANCE_ADOPTION_(POLICY_CHANGED|EVENT_ATTEMPT_BOUND)",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1
        assert target._preview_record(event_id) is None


@pytest.mark.parametrize("alias", [False, True])
def test_unknown_attempt_cannot_rename_same_business_input(
    workspace, tmp_path, monkeypatch, postgres_runtime, alias,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy_value, _path, _sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        second = command(target, event_id="edit-2", slot="currency", value="EUR")
        if alias:
            second = second.model_copy(update={"source_ref": "source:alias-of-reviewed-spec"})
        submit_change(target, second)
        wire_sends = []

        def ambiguous(http_request, *, timeout):
            assert timeout > 0
            wire_sends.append(http_request.data)
            raise TimeoutError("controlled transport ambiguity")

        monkeypatch.setattr(urllib.request, "urlopen", ambiguous)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="WORKSPACE_ADVISORY_MODEL_INCOMPLETE",
        ):
            target.preview_change(event_id)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="FINANCE_ADOPTION_BUSINESS_INPUT_ALREADY_BOUND",
        ):
            target.preview_change("edit-2")
        assert len(wire_sends) == 1
        assert target._preview_record("edit-2") is None


def _raised_price_budget(provider):
    budget = provider.model_budget
    assert budget is not None
    return ModelBudget.model_validate({
        **budget.model_dump(mode="json"),
        "input_usd_per_million_ceiling": "0.00000100",
        "price_source_ref": "test://finance-v4-price-increased-after-freeze",
    })


@pytest.mark.parametrize("stage", ["before_reserve", "before_dispatch"])
def test_price_change_never_dispatches_using_old_family_reservation(
    workspace, tmp_path, monkeypatch, postgres_runtime, stage,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy_value, _path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        provider = target.advisory_factory.provider
        if stage == "before_reserve":
            prepared = _prepare_for_reservation(target, event_id)
            provider.model_budget = _raised_price_budget(provider)
            with (
                _as(target, "actor:finance-operator", "operator"),
                target.store.transaction() as connection,
                pytest.raises(IntegrityError, match="FINANCE_ADOPTION_COST_CONTRACT_CHANGED"),
            ):
                reserve_selection(
                    target, connection, prepared,
                    expected_attempt_key=prepared.selection.attempt_key,
                )
            assert not target.store.list_artifacts(artifact_id_prefix="finance-adoption-selection:")
        else:
            from orgrebase.workspace import finance_adoption

            original = finance_adoption.reserve_selection

            def reserve_then_reprice(*args, **kwargs):
                result = original(*args, **kwargs)
                provider.model_budget = _raised_price_budget(provider)
                return result

            monkeypatch.setattr(finance_adoption, "reserve_selection", reserve_then_reprice)
            with _as(target, "actor:finance-operator", "operator"), pytest.raises(
                IntegrityError, match="FINANCE_ADOPTION_COST_CONTRACT_CHANGED",
            ):
                target.preview_change(event_id)
        assert sent == []
        assert target._preview_record(event_id) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("first_mode", ["ADOPTED", "LEGACY_V3"])
def test_competing_v3_and_adopted_previews_share_business_guard_before_dispatch(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend, first_mode,
):
    """The first transaction wins; neither ordering may produce two physical sends."""

    from orgrebase.workspace import service as service_module

    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, adopted_id, _policy_value, _path, _sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        legacy_id = "edit-2"
        submit_change(target, command(target, event_id=legacy_id, slot="currency", value="EUR"))
        first_id, second_id = (
            (adopted_id, legacy_id) if first_mode == "ADOPTED"
            else (legacy_id, adopted_id)
        )
        physical_sends = []

        def ambiguous(http_request, *, timeout):
            assert timeout > 0
            physical_sends.append(http_request.data)
            raise TimeoutError("controlled transport ambiguity")

        monkeypatch.setattr(urllib.request, "urlopen", ambiguous)
        first_reserved = threading.Event()
        allow_first_send = threading.Event()
        first_ident = []
        real_execute = service_module.execute_attempt

        def hold_after_reservation(*args, **kwargs):
            if first_ident and threading.get_ident() == first_ident[0]:
                first_reserved.set()
                assert allow_first_send.wait(10), "first preview never released"
            return real_execute(*args, **kwargs)

        monkeypatch.setattr(service_module, "execute_attempt", hold_after_reservation)

        def first_preview():
            first_ident.append(threading.get_ident())
            with (
                _as(target, "actor:finance-operator", "operator"),
                pytest.raises(IntegrityError, match="WORKSPACE_ADVISORY_MODEL_INCOMPLETE"),
            ):
                target.preview_change(first_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(first_preview)
            assert first_reserved.wait(10), "first preview never reserved"
            try:
                with _as(target, "actor:finance-operator", "operator"), pytest.raises(
                    IntegrityError, match="FINANCE_ADOPTION_BUSINESS_INPUT_ALREADY_BOUND",
                ):
                    target.preview_change(second_id)
                assert physical_sends == []
            finally:
                allow_first_send.set()
            future.result(timeout=10)
        assert len(physical_sends) == 1
        assert target._preview_record(second_id) is None


@pytest.mark.parametrize("invalidation", ["policy_expired", "membership_revoked", "source_revoked"])
def test_adoption_rechecks_live_authority_before_approval(
    workspace, tmp_path, monkeypatch, postgres_runtime, invalidation,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, policy, _path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            bundle = target.preview_change(event_id)
        sent_count = len(sent)
        assert sent_count >= 1
        original_quote = target.current_quote().digest
        if invalidation == "policy_expired":
            monkeypatch.setattr(target, "_wall_clock_epoch_ms", lambda: policy.expires_at_epoch_s * 1000)
            expected_error, reason = IntegrityError, "FINANCE_ADOPTION_POLICY_EXPIRED"
        elif invalidation == "source_revoked":
            target.changes.invalidate("currency", "source:permission-revoked", "PERMISSION_DENIED")
            expected_error, reason = (FreshnessError, IntegrityError, RuntimeError), None
        else:
            expected_error, reason = AuthorizationError, "MEMBERSHIP_REVOKED"
        owner = target.change_owner[event_id]
        with _as(target, owner, "approver"):
            if invalidation == "membership_revoked":
                def revoked():
                    raise AuthorizationError("MEMBERSHIP_REVOKED")
                token = request_authorization.set(revoked)
            else:
                token = None
            try:
                with pytest.raises(expected_error, match=reason):
                    target.approve_change(
                        event_id, actor_id=owner, preview_digest=bundle.preview.digest,
                    )
            finally:
                if token is not None:
                    request_authorization.reset(token)
        assert target._approval_record(event_id) is None
        assert target._outcome_record(event_id) is None
        assert target.current_quote().digest == original_quote
        assert len(sent) == sent_count


def test_promoted_head_becoming_unqualified_holds_frozen_preview(
    workspace, tmp_path, monkeypatch, postgres_runtime,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy_value, _path, sent):
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            bundle = target.preview_change(event_id)
        service = FinanceSkillHeadService(target.store, tenant_id=target.profile.organization_id)
        previous = service._current_source()
        successor = service._head_object(
            generation=2, transition_kind="ROLLBACK", bundle=service.resolve().bundle,
            previous=previous, actor_id="actor:finance-governor",
            qualification_status="UNQUALIFIED", qualification_ref=None,
            content_release_ref=None,
        )
        with target.store.transaction() as connection:
            target.store.insert_version(connection, successor, make_current=False)
            target.store.promote_version(connection, service.head_id, previous.version, successor.version)
        with _as(target, target.change_owner[event_id], "approver"), pytest.raises(
            IntegrityError, match="FINANCE_SKILL_PROMOTED_HEAD_INVALID",
        ):
            target.approve_change(
                event_id, actor_id=target.change_owner[event_id],
                preview_digest=bundle.preview.digest,
            )
        assert target._approval_record(event_id) is None
        assert target.current_quote().payload["currency"] != "EUR"
        assert len(sent) >= 1
