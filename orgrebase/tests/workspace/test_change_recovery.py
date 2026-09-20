"""Real local AT execution and authority boundaries for same-ChangeSet recovery.

The local RecordingProvider cases are controlled contract tests, not live-model claims.
"""
from __future__ import annotations

import copy
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import IntegrityError
from orgrebase.local_role_session import LocalRoleSessionSettings
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.change_recovery import (
    EvidenceReference,
    ResumeChangeInput,
    ReturnForEvidenceInput,
    latest_request,
    recovery_detail,
    resume_change,
    resume_record,
    return_for_evidence,
)
from tests.test_local_role_sessions import ORIGIN, actor, choose, csrf
from tests.workspace.test_change_advisory import RecordingProvider
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_continuous_changes import make_service, proposal


def prepare(root, event_id="recovery", provider=None):
    workspace = make_service(root / "workspace.sqlite")
    configure(workspace, root / "native")
    if provider is not None:
        workspace.advisory_factory.provider = provider
        workspace.advisory_factory.model_id = "model:test"
    event = proposal(workspace, event_id, "product_plan", "Enterprise recovery reviewed")
    workspace.register_change(event)
    workspace.preview_change(event.event_id)
    return workspace, event


def return_command(workspace, event_id, operation="return-1"):
    detail = recovery_detail(workspace, event_id)
    task = detail["tasks"][0]
    return ReturnForEvidenceInput(operation_id=operation,
        expected_context_digest=detail["context_digest"], task_id=task["task_id"],
        reason="Recheck the admitted source material and previous candidate before continuing.",
        required_evidence_refs=task["evidence_refs"])


def resume_command(workspace, event_id, operation="resume-1"):
    detail = recovery_detail(workspace, event_id)
    task = next(item for item in detail["tasks"] if item["task_id"] == detail["task_id"])
    return ResumeChangeInput(operation_id=operation, recovery_digest=detail["recovery_digest"],
        executor_id=task["allowed_executors"][-1]["executor_id"],
        evidence=[EvidenceReference(ref=item["ref"], digest=item["digest"])
                  for item in detail["evidence_options"] if item["ref"] in detail["required_evidence_refs"]])


def test_polling_reads_exact_current_recovery_and_rejects_unbound_request_event(tmp_path, monkeypatch):
    workspace, event = prepare(tmp_path)
    with closing(workspace):
        return_for_evidence(workspace, event.event_id, return_command(workspace, event.event_id))
        request = latest_request(workspace, event.event_id)

        def forbidden(*args, **kwargs):
            raise AssertionError("polling must not replay recovery history")

        monkeypatch.setattr(workspace.store, "list_artifacts", forbidden)
        monkeypatch.setattr(workspace.store, "event_records", forbidden)
        monkeypatch.setattr(workspace.store, "event_envelopes", forbidden)
        for _ in range(3):
            assert latest_request(workspace, event.event_id) == request
            state = workspace.state(history_limit=1)
            assert state["change_events"][0]["status"] == "EVIDENCE_REQUIRED"

        # A journal row claiming a different request cannot select the existing
        # artifact or silently fall back to an older, previously valid round.
        with workspace.store.transaction() as connection:
            workspace.store.append_event(connection, "WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
                "event_id": event.event_id, "round": request["round"],
                "recovery_digest": "sha256:" + "0" * 64, "actor_id": request["requested_by"],
            })
        with pytest.raises(IntegrityError, match="CHANGE_RECOVERY_EVIDENCE_INVALID"):
            latest_request(workspace, event.event_id)


def test_real_native_round_keeps_intent_history_and_separate_approval_apply(tmp_path):
    workspace, event = prepare(tmp_path)
    with closing(workspace):
        before_quote = workspace.current_quote()
        old = copy.deepcopy(workspace._preview_record(event.event_id))
        request = return_command(workspace, event.event_id)
        returned = return_for_evidence(workspace, event.event_id, request)
        assert returned["state"] == "NEEDS_EVIDENCE"
        assert workspace._preview_record(event.event_id) is None
        assert return_for_evidence(workspace, event.event_id, request) == returned
        with pytest.raises(RuntimeError, match="NOT_PREVIEWABLE"):
            workspace.preview_change(event.event_id)
        command = resume_command(workspace, event.event_id)
        ready = resume_change(workspace, event.event_id, command)
        new = workspace._preview_record(event.event_id)
        assert ready["state"] == "READY_FOR_REVIEW" and ready["round"] == 1
        assert ready["preserved_context"]["previous_native_receipt_digest"] == old["native_execution"]["receipt_digest"]
        assert ready["preserved_context"]["previous_preview_artifact_digest"] == old["artifact_digest"]
        assert old["bundle"]["change_set"] == new["bundle"]["change_set"]
        assert old["preview_digest"] == new["preview_digest"]
        assert workspace.store.load_artifact(old["artifact_id"]).payload == old["bundle"]
        assert old["native_execution"]["project_id"] != new["native_execution"]["project_id"]
        assert new["native_execution"]["status"] == "COMPLETED"
        assert new["native_execution"]["native_agentteams_observed"] is True
        assert new["native_execution"]["distributed_execution"] is False
        old_handoffs = {item["id"] for item in old["bundle"]["advisory"]["handoffs"]}
        assert not old_handoffs.intersection(item["id"] for item in new["bundle"]["advisory"]["handoffs"])
        runs = new["bundle"]["advisory"]["agent_runs"]
        assert not {item["trace_id"] for item in runs}.intersection(
            item["trace_id"] for item in old["bundle"]["advisory"]["agent_runs"])
        handoff = next(item for item in new["bundle"]["advisory"]["handoffs"] if item["task_id"] == request.task_id)
        assert handoff["from_agent"] == command.executor_id
        native = new["bundle"]["advisory"]["native_execution"]
        native_task = next(item for item in native["tasks"] if item["logical_task_id"] == request.task_id)
        assert native_task["actor_id"] == command.executor_id
        assert native["actions"][-1]["action"] == "complete_project"
        assert ready["executor"]["capability_ref"] == returned["tasks"][0]["allowed_executors"][0]["capability_ref"]
        assert ready["executor"]["coalition_ref"] == workspace._formation_record().coalition_plan_ref
        assert handoff["payload"]["supplemental_evidence"][0]["digest"] == command.evidence[0].digest
        assert resume_record(workspace, event.event_id)["previous_candidate"]["task_id"] == request.task_id
        assert workspace.current_quote() == before_quote and workspace._approval_record(event.event_id) is None
        assert resume_change(workspace, event.event_id, command) == ready
        assert workspace._preview_record(event.event_id) == new
        for stale in (None, request.expected_context_digest, command.recovery_digest):
            with pytest.raises(IntegrityError, match="APPROVAL_BINDING_REQUIRED"):
                workspace.approve_change(event.event_id, actor_id=event.owner_id,
                    preview_digest=new["preview_digest"], recovery_digest=stale)
        approval = workspace.approve_change(event.event_id, actor_id=event.owner_id,
            preview_digest=new["preview_digest"], recovery_digest=ready["recovery_digest"])
        assert workspace.current_quote() == before_quote
        with pytest.raises(IntegrityError, match="ALREADY_APPROVED"):
            return_for_evidence(workspace, event.event_id, request.model_copy(update={"operation_id": "too-late"}))
        workspace.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
        assert workspace.current_quote().payload["product_plan"] == event.proposal.payload["canonical_value"]
        assert change_detail(workspace, event.event_id)["status"] == "APPLIED"
        assert recovery_detail(workspace, event.event_id)["allowed_actions"] == []


def test_bad_evidence_executor_and_command_reuse_cannot_dispatch(tmp_path):
    workspace, event = prepare(tmp_path)
    with closing(workspace):
        before = workspace.current_quote()
        request = return_command(workspace, event.event_id)
        for mutation, code in (({"expected_context_digest": "sha256:" + "0" * 64}, "CONTEXT_CHANGED"),
                               ({"required_evidence_refs": ["claim:outside@v1"]}, "EVIDENCE_SCOPE"),
                               ({"task_id": "task:coordinator"}, "EVIDENCE_SCOPE"),
                               ({"reason": "  "}, "REASON_REQUIRED")):
            with pytest.raises(IntegrityError, match=code):
                return_for_evidence(workspace, event.event_id, request.model_copy(update=mutation))
        return_for_evidence(workspace, event.event_id, request)
        with pytest.raises(IntegrityError, match="COMMAND_CONFLICT"):
            return_for_evidence(workspace, event.event_id, request.model_copy(update={"reason": "Another reason"}))
        command = resume_command(workspace, event.event_id)
        for mutation, code in (({"executor_id": "arbitrary:administrator"}, "EXECUTOR_NOT_ADMITTED"),
                               ({"recovery_digest": "sha256:" + "0" * 64}, "DIGEST_MISMATCH"),
                               ({"evidence": []}, "EVIDENCE_REQUIRED"),
                               ({"evidence": command.evidence * 2}, "EVIDENCE_REQUIRED"),
                               ({"evidence": [EvidenceReference(ref=command.evidence[0].ref,
                                 digest="sha256:" + "0" * 64)]}, "EVIDENCE_CHANGED")):
            with pytest.raises(IntegrityError, match=code):
                resume_change(workspace, event.event_id, command.model_copy(update=mutation))
        assert resume_record(workspace, event.event_id) is None
        assert workspace._preview_record(event.event_id) is None
        assert workspace.current_quote() == before


def test_failed_round_requires_new_owner_request_and_consumes_actual_source_context(tmp_path):
    provider = RecordingProvider(fail_at=3)
    workspace, event = prepare(tmp_path, provider=provider)
    with closing(workspace):
        request = return_command(workspace, event.event_id)
        return_for_evidence(workspace, event.event_id, request)
        command = resume_command(workspace, event.event_id)
        with pytest.raises(IntegrityError):
            resume_change(workspace, event.event_id, command)
        assert len(provider.requests) == 3
        failed = recovery_detail(workspace, event.event_id)
        assert failed["state"] == "FAILED" and failed["allowed_actions"] == ["RETURN_FOR_EVIDENCE"]
        with pytest.raises(IntegrityError, match="ATTEMPT_FAILED"):
            resume_change(workspace, event.event_id, command)
        assert len(provider.requests) == 3
        second_request = return_command(workspace, event.event_id, "return-2")
        return_for_evidence(workspace, event.event_id, second_request)
        second = resume_command(workspace, event.event_id, "resume-2")
        resumed = resume_change(workspace, event.event_id, second)
        assert resumed["round"] == 2 and resumed["state"] == "READY_FOR_REVIEW"
        assert len(resumed["history"]) == 2
        target_request = provider.requests[3]
        extra = next(item for item in target_request.input_projections if item.ref.startswith("recovery-evidence:"))
        record = resume_record(workspace, event.event_id)
        assert extra.content["projection"]["supplemental_content"] == record["evidence"][0]["object"]["payload"]
        assert extra.content["projection"]["previous_candidate"] == record["previous_candidate"]
        assert target_request.actor_id == second.executor_id
        assert target_request.request_id != provider.requests[2].request_id
        assert workspace._approval_record(event.event_id) is None


def test_real_cookie_api_enforces_owner_return_operator_resume_and_csrf(tmp_path):
    workspace, event = prepare(tmp_path)
    with closing(workspace):
        app = create_app(workspace_service=workspace, deployment_settings=DeploymentSettings(
            mode="local", local_role_session=LocalRoleSessionSettings(ORIGIN)))
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42000)) as client:
            initial = client.get("/api/session").json()
            operator = choose(client, actor(initial, "operator"))
            path = f"/api/workspace/changes/{event.event_id}"
            initial_detail = client.get(path).json()
            task = initial_detail["recovery"]["tasks"][0]
            body = {"operation_id": "http-return", "expected_context_digest": initial_detail["recovery"]["context_digest"],
                    "task_id": task["task_id"], "reason": "Owner requires exact source evidence", "required_evidence_refs": task["evidence_refs"]}
            assert client.post(path + "/return-for-evidence", json=body, headers=csrf(operator)).status_code == 403
            unrelated = next(item["actor_id"] for item in initial["actors"] if "approver" in item["roles"] and item["actor_id"] != event.owner_id)
            wrong_owner = choose(client, unrelated)
            assert client.post(path + "/return-for-evidence", json=body, headers=csrf(wrong_owner)).status_code == 403
            owner = choose(client, event.owner_id)
            assert owner["principal"]["roles"] == ["approver"]
            assert client.post(path + "/return-for-evidence", json=body).status_code == 403
            result = client.post(path + "/return-for-evidence", json=body, headers=csrf(owner))
            assert result.status_code == 200, result.text
            returned = result.json()
            resume = {"operation_id": "http-resume", "recovery_digest": returned["recovery_digest"],
                "executor_id": task["allowed_executors"][-1]["executor_id"],
                "evidence": [{"ref": item["ref"], "digest": item["digest"]} for item in returned["evidence_options"] if item["ref"] in returned["required_evidence_refs"]]}
            assert client.post(path + "/resume", json=resume, headers=csrf(owner)).status_code == 403
            operator = choose(client, actor(initial, "operator"))
            result = client.post(path + "/resume", json=resume, headers=csrf(operator))
            assert result.status_code == 200, result.text
            assert result.json()["state"] == "READY_FOR_REVIEW"
            ready = client.get(path).json()
            assert ready["preview"]["native_execution"]["status"] == "COMPLETED"
            assert ready["approval"] is None and ready["outcome"] is None


def test_uncertain_attempt_and_removed_capability_cannot_be_redelegated(tmp_path, monkeypatch):
    from orgrebase.workspace import change_recovery, preview_execution
    workspace, event = prepare(tmp_path)
    with closing(workspace):
        original = preview_execution.read_attempt_summary
        for unknown in ({"state": "RESULT_UNKNOWN"}, {"state": "IN_PROGRESS"},
                        {"state": "FAILED", "native_execution": {"status": "RESULT_UNKNOWN"}},
                        {"state": "FAILED", "receipt_summaries": [{"dispatch_state": "SENT_UNKNOWN"}]}):
            monkeypatch.setattr(preview_execution, "read_attempt_summary", lambda *args, value=unknown, **kwargs: value)
            detail = recovery_detail(workspace, event.event_id)
            assert "RETURN_FOR_EVIDENCE" not in detail["allowed_actions"]
        monkeypatch.setattr(preview_execution, "read_attempt_summary", original)
        return_for_evidence(workspace, event.event_id, return_command(workspace, event.event_id))
        command = resume_command(workspace, event.event_id)
        monkeypatch.setattr(change_recovery, "selected_capability_cards", lambda coalition: {})
        with pytest.raises(IntegrityError, match="EXECUTOR_NOT_ADMITTED"):
            resume_change(workspace, event.event_id, command)
        assert resume_record(workspace, event.event_id) is None


def test_other_instance_cannot_change_round_between_approval_read_and_commit(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
    from orgrebase.workspace.service import WorkspaceService
    from tests.workspace.test_continuous_changes import PACK

    workspace, event = prepare(tmp_path)
    with closing(workspace), closing(WorkspaceService.reopen(tmp_path / "workspace.sqlite",
            runtime_configuration=load_enterprise_quote_pilot_pack(PACK), review_duration_seconds=0)) as other:
        configure(other, tmp_path / "native")
        initial = workspace._preview_record(event.event_id)
        original_transaction = workspace.store.transaction
        interleaved = []

        @contextmanager
        def transaction_after_new_round():
            if not interleaved:
                interleaved.append(True)
                return_for_evidence(other, event.event_id, return_command(other, event.event_id))
                resume_change(other, event.event_id, resume_command(other, event.event_id))
                assert other._preview_record(event.event_id)["preview_digest"] == initial["preview_digest"]
            with original_transaction() as connection:
                yield connection

        monkeypatch.setattr(workspace.store, "transaction", transaction_after_new_round)
        with pytest.raises(IntegrityError, match="APPROVAL_BINDING_REQUIRED"):
            workspace.approve_change(event.event_id, actor_id=event.owner_id,
                                     preview_digest=initial["preview_digest"])
        assert interleaved == [True]
        assert workspace._approval_record(event.event_id) is None
        assert workspace._outcome_record(event.event_id) is None
        assert recovery_detail(workspace, event.event_id)["state"] == "READY_FOR_REVIEW"
