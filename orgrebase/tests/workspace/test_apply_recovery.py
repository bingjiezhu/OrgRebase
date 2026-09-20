from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.domain import IntegrityError, RebaseReceipt
from orgrebase.workspace.change_agentteams import ChangeAgentTeamsConfig
from orgrebase.workspace.change_proposals import (
    ChangeProposalInput,
    change_detail,
    change_options,
    submit_change,
)
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def committed_without_outcome(tmp_path, monkeypatch):
    service = WorkspaceService(store_path=tmp_path / "recovery.sqlite")
    try:
        service.form_quote()
        preview = service.preview_change("currency")
        approval = service.approve_change("currency", actor_id=service.change_owner["currency"],
                                          preview_digest=preview.preview.digest)
        with monkeypatch.context() as fault:
            def fail_projection(*args, **kwargs):
                raise RuntimeError("AFTER_CORE_COMMIT")
            fault.setattr(service, "_persist_outcome", fail_projection)
            with pytest.raises(RuntimeError, match="AFTER_CORE_COMMIT"):
                service.apply_approved_change("currency", approval_digest=approval["approval_digest"])
        assert service._outcome_record("currency") is None
        yield service, approval
    finally:
        service.close()


def test_committed_change_offers_only_recovery_and_gets_do_not_write(committed_without_outcome):
    service, approval = committed_without_outcome
    before = tuple(service.store.connection.iterdump())
    detail = change_detail(service, "currency")
    assert detail["status"] == "RECOVERY_REQUIRED"
    assert detail["allowed_actions"] == ["APPLY"]
    assert detail["approval"]["approval_digest"] == approval["approval_digest"]
    assert detail["outcome"] is None
    state = service.state()
    assert next(event for event in state["change_events"] if event["event_id"] == "currency")["status"] == "RECOVERY_REQUIRED"
    assert state["business_complete"] is False
    assert tuple(service.store.connection.iterdump()) == before


@pytest.mark.parametrize("role,actions", [("reader", []), ("approver", []), ("executor", ["APPLY"])])
def test_recovery_action_still_requires_execution_permission(committed_without_outcome, role, actions):
    service, _ = committed_without_outcome
    token = request_principal.set(Principal("https://identity.example", "operator", service.profile.organization_id,
                                            "user:operator", frozenset({role}), int(time.time()) + 900))
    try:
        detail = change_detail(service, "currency")
        assert detail["status"] == "RECOVERY_REQUIRED"
        assert detail["allowed_actions"] == actions
    finally:
        request_principal.reset(token)


def test_committed_change_cannot_be_rejected_or_revised(committed_without_outcome):
    service, _ = committed_without_outcome
    field = next(item for item in change_options(service)["fields"] if item["slot_id"] == "currency")
    request = ChangeProposalInput(event_id="replacement", slot_id="currency", value="JPY", source_ref="source:corrected",
                                  base_version=field["current"]["version"], base_digest=field["current"]["digest"],
                                  revises_event_id="currency")
    before = tuple(service.store.connection.iterdump())
    with pytest.raises(IntegrityError, match="CHANGE_REVISION_REQUIRES_CLOSED_PROPOSAL"):
        submit_change(service, request)
    with pytest.raises(IntegrityError, match="CHANGE_ALREADY_APPLIED"):
        service.reject_change("currency", actor_id=service.change_owner["currency"], reason="Too late")
    assert tuple(service.store.connection.iterdump()) == before


def test_recovery_after_restart_expiry_and_later_change_keeps_historical_successor(
    committed_without_outcome, monkeypatch,
):
    service, approval = committed_without_outcome
    committed = service.current_quote()
    preview = service.preview_change("launch_date")
    later_approval = service.approve_change("launch_date", actor_id=service.change_owner["launch_date"],
                                            preview_digest=preview.preview.digest)
    service.apply_approved_change("launch_date", approval_digest=later_approval["approval_digest"])
    latest = service.current_quote()
    assert latest.version != committed.version
    store_path = service.store_path
    expired = FrozenClock(timestamp(utc_datetime(service.clock.now()) + timedelta(days=2)))
    service.close()
    reopened = WorkspaceService.reopen(store_path, clock=expired)
    try:
        def forbid_execution(**kwargs):
            pytest.fail("recovery must not execute the business change again")
        monkeypatch.setattr(reopened, "_execute_apply", forbid_execution)
        assert change_detail(reopened, "currency")["status"] == "RECOVERY_REQUIRED"
        state = reopened.state()
        assert state["active_event_id"] == "currency"
        assert state["stage"] == "RECOVERY_REQUIRED"
        assert state["actions"]["next_operation"] == "apply"
        assert state["actions"]["next_event_id"] == "currency"
        with pytest.raises(RuntimeError, match="WORKSPACE_APPROVAL_DIGEST_MISMATCH"):
            reopened.apply_approved_change("currency", approval_digest="sha256:" + "0" * 64)
        result = reopened.apply_approved_change("currency", approval_digest=approval["approval_digest"])
        assert result["outcome"]["quote"]["id"] == committed.id
        assert result["outcome"]["quote"]["version"] == committed.version
        assert result["outcome"]["quote"]["digest"] == committed.digest
        assert reopened.current_quote() == latest
        after = tuple(reopened.store.connection.iterdump())
        reopened.apply_approved_change("currency", approval_digest=approval["approval_digest"])
        assert tuple(reopened.store.connection.iterdump()) == after
        assert change_detail(reopened, "currency")["status"] == "APPLIED"
    finally:
        reopened.close()


@pytest.mark.parametrize("field,value", [
    ("id", "rebase:another"), ("status", "FAILED"),
    ("workflow_run_id", "run:another"), ("run_nonce", "another-nonce"),
    ("change_set_ref", "changeset:another@r1"), ("preview_ref", "preview:another"),
    ("approval_ref", "approval:another"), ("approval_actor_id", "user:another"),
    ("minimal_rebase_certificate_digest", "sha256:" + "0" * 64),
])
def test_misbound_or_unsuccessful_receipt_cannot_offer_or_perform_recovery(
    committed_without_outcome, monkeypatch, field, value,
):
    service, approval = committed_without_outcome
    receipt_id = "rebase:workspace:workspace-currency@r1"
    original_read = service.store.load_artifact
    payload = original_read(receipt_id).payload
    changed = {key: item for key, item in payload.items() if key != "digest"}
    forged = RebaseReceipt.model_validate({**changed, field: value})

    def read(artifact_id, expected_media_type=None):
        if artifact_id == receipt_id:
            return SimpleNamespace(payload=forged.model_dump(mode="json"))
        return original_read(artifact_id, expected_media_type)

    before = tuple(service.store.connection.iterdump())
    monkeypatch.setattr(service.store, "load_artifact", read)
    with pytest.raises(IntegrityError, match="WORKSPACE_COMMITTED_RECEIPT_BINDING_INVALID"):
        change_detail(service, "currency")
    with pytest.raises(IntegrityError, match="WORKSPACE_COMMITTED_RECEIPT_BINDING_INVALID"):
        service.apply_approved_change("currency", approval_digest=approval["approval_digest"])
    assert tuple(service.store.connection.iterdump()) == before


def test_missing_receipt_cannot_be_replaced_by_the_existing_idempotency_record(
    committed_without_outcome, monkeypatch,
):
    service, approval = committed_without_outcome
    original_read = service.store.load_artifact

    def read(artifact_id, expected_media_type=None):
        if artifact_id == "rebase:workspace:workspace-currency@r1":
            raise KeyError(artifact_id)
        return original_read(artifact_id, expected_media_type)

    before = tuple(service.store.connection.iterdump())
    monkeypatch.setattr(service.store, "load_artifact", read)
    detail = change_detail(service, "currency")
    assert detail["status"] != "RECOVERY_REQUIRED"
    assert "APPLY" not in detail["allowed_actions"]
    with pytest.raises(RuntimeError, match="WORKSPACE_APPROVAL_PREDECESSOR_STALE"):
        service.apply_approved_change("currency", approval_digest=approval["approval_digest"])
    assert tuple(service.store.connection.iterdump()) == before


def test_another_events_preview_cannot_supply_recovery_evidence(committed_without_outcome, monkeypatch):
    service, _ = committed_without_outcome
    service.preview_change("launch_date")
    unrelated = service._preview_record("launch_date")
    original_read = service._preview_record
    before = tuple(service.store.connection.iterdump())
    monkeypatch.setattr(service, "_preview_record", lambda kind: unrelated if kind == "currency" else original_read(kind))
    with pytest.raises(IntegrityError, match="WORKSPACE_COMMITTED_RECEIPT_BINDING_INVALID"):
        change_detail(service, "currency")
    assert tuple(service.store.connection.iterdump()) == before


@pytest.mark.parametrize("missing_lock", [False, True])
def test_committed_recovery_does_not_require_a_new_native_agentteams_run(
    committed_without_outcome, monkeypatch, tmp_path, missing_lock,
):
    service, approval = committed_without_outcome
    service.advisory_factory.native_required = True
    if missing_lock:
        service.advisory_factory.native_config = ChangeAgentTeamsConfig(
            checkout=tmp_path / "unavailable-checkout", lock_path=tmp_path / "missing-lock.json", evidence_root=tmp_path,
        )

    def forbid_agentteams(*args, **kwargs):
        pytest.fail("committed recovery must not require a new AgentTeams execution")

    monkeypatch.setattr(service.advisory_factory, "require_available", forbid_agentteams)
    monkeypatch.setattr(service.advisory_factory, "run", forbid_agentteams)
    before = service.current_quote()
    detail = change_detail(service, "currency")
    assert detail["status"] == "RECOVERY_REQUIRED"
    assert detail["runtime_compatibility"]["decision"] == "READ_ONLY_HISTORICAL"
    assert detail["runtime_compatibility"]["current_revision"] is None
    assert detail["runtime_compatibility"]["previous_revision"]
    service.apply_approved_change("currency", approval_digest=approval["approval_digest"])
    assert service.current_quote() == before
    assert change_detail(service, "currency")["status"] == "APPLIED"


def test_uncommitted_preview_still_requires_current_native_configuration(tmp_path):
    service = WorkspaceService(store_path=tmp_path / "uncommitted.sqlite")
    try:
        service.form_quote()
        service.preview_change("currency")
        service.advisory_factory.native_required = True
        service.advisory_factory.native_config = ChangeAgentTeamsConfig(
            checkout=tmp_path / "unavailable-checkout", lock_path=tmp_path / "missing-lock.json", evidence_root=tmp_path,
        )
        before = tuple(service.store.connection.iterdump())
        with pytest.raises(FileNotFoundError):
            change_detail(service, "currency")
        assert tuple(service.store.connection.iterdump()) == before
    finally:
        service.close()
