"""Actual S05 resource use and later feedback remain separate from effect claims."""

from __future__ import annotations

import pytest

from orgrebase.auth import request_authorization
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace import quote_recovery_feedback as feedback_module
from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.quote_recovery_feedback import (
    QuoteRecoveryFollowupCommand,
    read_quote_recovery_actual_use,
    record_quote_recovery_actual_use,
    record_quote_recovery_followup,
)
from orgrebase.workspace.quote_recovery_operations import (
    QuoteRecoveryNewRun,
    execute_quote_recovery_new_run,
)
from orgrebase.workspace.service import WorkspaceService
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_continuous_changes import apply, proposal
from tests.workspace.test_enterprise_pilot_delivery import _make_distinct_enterprise_pack
from tests.workspace.test_quote_recovery_adoption_matrix import _verified_release
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    EVALUATOR,
    RUNTIME,
    TENANT,
    WORKSPACE,
    _identity,
)
from tests.workspace.test_quote_recovery_operations import _policy, _recovery_workspace


def _run(workspace, event_id, candidate, policy):
    command = QuoteRecoveryNewRun(
        operation_key="actual-use-operation",
        event_id=event_id, candidate_ref=candidate,
        new_run_id="run:actual-use-operation",
    )
    with _identity(RUNTIME, frozenset({"reader"}), []):
        return execute_quote_recovery_new_run(workspace, command, config_path=policy)


def test_real_s05_invocation_is_actual_use_but_not_business_improvement(tmp_path, monkeypatch):
    with StateStore(tmp_path / "actual-use.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        service, _, candidate, _, _, _ = _verified_release(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        receipt = _run(workspace, event.event_id, candidate, policy)
        assert receipt["status"] == "CONSUMED"
        with _identity(RUNTIME, frozenset({"reader"}), []):
            actual = read_quote_recovery_actual_use(workspace, receipt["operation_key"])
        assert actual["actual_use_status"] == "CONFIRMED_RESTRICTED_CONSUMER"
        assert actual["execution_mode"] == "S05_ADOPTED_ACTUAL_USE"
        assert actual["consumed_resource_digests"]
        assert set(actual["consumed_resource_digests"]) <= set(actual["loaded_resource_digests"])
        assert actual["invocation_receipt_digest"] == receipt["invocation_receipt_digest"]
        assert actual["business_improvement_status"] == "NOT_EVALUATED"
        assert actual["original_apply_attribution"] == "PRE_USE_CONTEXT_NEVER_SKILL_EFFECT"
        with _identity(RUNTIME, frozenset({"governor"}), []), pytest.raises(
            AuthorizationError, match="INDEPENDENT_ASSESSOR_REQUIRED"
        ):
            record_quote_recovery_actual_use(
                workspace, receipt["operation_key"], assessor_actor_id=RUNTIME,
            )
        with _identity(AUTHOR, frozenset({"governor"}), []), pytest.raises(
            AuthorizationError, match="INDEPENDENT_ASSESSOR_REQUIRED"
        ):
            record_quote_recovery_actual_use(
                workspace, receipt["operation_key"], assessor_actor_id=AUTHOR,
            )
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            anchor = record_quote_recovery_actual_use(
                workspace, receipt["operation_key"], assessor_actor_id=EVALUATOR,
            )
            assert record_quote_recovery_actual_use(
                workspace, receipt["operation_key"], assessor_actor_id=EVALUATOR,
            ) == anchor
            with pytest.raises(IntegrityError, match="ORIGINAL_APPLY_NOT_EFFECT"):
                record_quote_recovery_followup(
                    workspace,
                    QuoteRecoveryFollowupCommand(
                        use_ref=anchor["use_ref"], followup_event_id=event.event_id,
                        reviewer_verdict="REVIEWABLE", reason_code="ORIGINAL_APPLY",
                    ),
                    assessor_actor_id=EVALUATOR,
                )
        assert store.verify_event_chain()["status"] == "PASS"


def test_real_workspace_followup_after_actual_use_is_observed_without_effect_claim(
    tmp_path, monkeypatch,
):
    pack = _make_distinct_enterprise_pack(
        tmp_path / "pack", slug="quote-recovery", customer="feedback-customer",
        product_plan="Feedback Enterprise Plan", proposed_date="2027-02-15",
    )
    runtime = load_enterprise_quote_pilot_pack(pack)
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite",
        store_tenant_id=TENANT, runtime_configuration=runtime,
    )
    try:
        workspace.form_quote()
        for event_id in ("currency", "launch_date"):
            apply(workspace, event_id)
        configure(workspace, tmp_path / "native")
        event = proposal(
            workspace, "feedback-original", "product_plan", "Feedback Enterprise Plan v2",
        )
        workspace.register_change(event)
        workspace.preview_change(event.event_id)
        return_for_evidence(workspace, event.event_id, return_command(workspace, event.event_id))
        resumed = resume_change(workspace, event.event_id, resume_command(workspace, event.event_id))
        first_preview = workspace._preview_record(event.event_id)
        approval = workspace.approve_change(
            event.event_id, actor_id=event.owner_id,
            preview_digest=first_preview["preview_digest"],
            recovery_digest=resumed["recovery_digest"],
        )
        workspace.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
        quote_after_original_apply = workspace.current_quote().digest
        service, _, candidate, _, _, _ = _verified_release(workspace.store)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        operation = _run(workspace, event.event_id, candidate, policy)
        assert operation["status"] == "CONSUMED"
        assert workspace.current_quote().digest == quote_after_original_apply
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            use = record_quote_recovery_actual_use(
                workspace, operation["operation_key"], assessor_actor_id=EVALUATOR,
            )
        followup = proposal(
            workspace, "feedback-later", "product_plan", "Feedback Enterprise Plan v3",
        )
        workspace.register_change(followup)
        workspace.preview_change(followup.event_id)
        command = QuoteRecoveryFollowupCommand(
            use_ref=use["use_ref"], followup_event_id=followup.event_id,
            reviewer_verdict="NEEDS_REWORK", reason_code="CONTROLLED_LOCAL_REVIEW",
        )
        with _identity(EVALUATOR, frozenset({"governor"}), []), pytest.raises(
            IntegrityError, match="FOLLOWUP_OUTCOME_NOT_RECORDED"
        ):
            record_quote_recovery_followup(
                workspace, command, assessor_actor_id=EVALUATOR,
            )
        second_preview = workspace._preview_record(followup.event_id)
        second_approval = workspace.approve_change(
            followup.event_id, actor_id=followup.owner_id,
            preview_digest=second_preview["preview_digest"],
        )
        workspace.apply_approved_change(
            followup.event_id, approval_digest=second_approval["approval_digest"],
        )
        assert change_detail(workspace, followup.event_id)["status"] == "APPLIED"
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            observed = record_quote_recovery_followup(
                workspace, command, assessor_actor_id=EVALUATOR,
            )
            assert record_quote_recovery_followup(
                workspace, command, assessor_actor_id=EVALUATOR,
            ) == observed
            with pytest.raises(IntegrityError, match="SECOND_SAMPLE"):
                record_quote_recovery_followup(
                    workspace,
                    command.model_copy(update={"reviewer_verdict": "REVIEWABLE"}),
                    assessor_actor_id=EVALUATOR,
                )
        assert observed["followup_status"] == "POST_USE_OUTCOME_OBSERVED"
        assert observed["use_sequence_no"] < observed["followup_outcome_sequence_no"]
        assert observed["original_outcome_timing"] == "PRE_USE_NOT_ATTRIBUTABLE"
        assert observed["effect_status"] == "NO_EFFECT_CLAIM"
        assert observed["quality_status"] == "PENDING_INDEPENDENT_ASSESSMENT"
        assert observed["qualification_status"] == "NOT_QUALIFIED"
        assert workspace.store.verify_event_chain()["status"] == "PASS"
    finally:
        workspace.close()


def test_postgres_restart_reads_exact_actual_use_without_policy_redispatch(
    postgres_runtime, tmp_path, monkeypatch,
):
    database = postgres_runtime(tenant_id=TENANT)
    policy = tmp_path / "policy.json"
    with StateStore(database["runtime_dsn"], tenant_id=TENANT, migrate=False) as first:
        service, _, candidate, _, _, _ = _verified_release(first)
        workspace, event, _ = _recovery_workspace(first, monkeypatch)
        _policy(policy, candidate=candidate, service=service)
        operation = _run(workspace, event.event_id, candidate, policy)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            anchor = record_quote_recovery_actual_use(
                workspace, operation["operation_key"], assessor_actor_id=EVALUATOR,
            )
    with StateStore(database["runtime_dsn"], tenant_id=TENANT, migrate=False) as reopened:
        workspace, _, detail = _recovery_workspace(reopened, monkeypatch, persist_receipts=False)
        detail["status"] = "REJECTED"
        detail["outcome"] = None
        with _identity(RUNTIME, frozenset({"reader"}), []):
            reread = read_quote_recovery_actual_use(workspace, operation["operation_key"])
        assert reread == anchor["projection"]
        assert reread["actual_use_status"] == "CONFIRMED_RESTRICTED_CONSUMER"
        assert reopened.verify_event_chain()["status"] == "PASS"


def test_late_authorization_revocation_after_s05_read_returns_no_use_projection(
    tmp_path, monkeypatch,
):
    with StateStore(tmp_path / "late-revoke.sqlite", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        service, _, candidate, _, _, _ = _verified_release(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        operation = _run(workspace, event.event_id, candidate, policy)
        intent_ref = feedback_module._operation_storage(workspace, operation["operation_key"])[1]
        original_load = workspace.store.load_artifact
        active = {"allowed": True}
        reads = {"intent": 0}

        def revoke_during_projection(artifact_id, expected_media_type=None):
            value = original_load(artifact_id, expected_media_type)
            if artifact_id == intent_ref:
                reads["intent"] += 1
                if reads["intent"] == 2:
                    active["allowed"] = False
            return value

        def check():
            if not active["allowed"]:
                raise AuthorizationError("TEST_LATE_QUOTE_ACCESS_REVOKED")

        monkeypatch.setattr(workspace.store, "load_artifact", revoke_during_projection)
        before = store.audit_head()
        with _identity(RUNTIME, frozenset({"reader"}), []):
            token = request_authorization.set(check)
            try:
                with pytest.raises(AuthorizationError, match="TEST_LATE_QUOTE_ACCESS_REVOKED"):
                    read_quote_recovery_actual_use(workspace, operation["operation_key"])
            finally:
                request_authorization.reset(token)
        assert reads["intent"] == 2
        assert store.audit_head() == before
