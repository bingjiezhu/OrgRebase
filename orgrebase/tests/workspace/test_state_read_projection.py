from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import Mock

import pytest
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_proposals import command


@pytest.fixture
def workspace(tmp_path: Path):
    service = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite",
        runtime_configuration=load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path)),
        review_duration_seconds=0,
    )
    try:
        yield service
    finally:
        service.close()


def test_state_reuses_active_selection_and_refreshes_after_each_command(workspace, monkeypatch):
    active = Mock(wraps=workspace._active_event_id)
    complete = Mock(wraps=workspace._business_complete)
    monkeypatch.setattr(workspace, "_active_event_id", active)
    monkeypatch.setattr(workspace, "_business_complete", complete)

    def observe(stage, status, operation, *, business_complete=False):
        active.reset_mock()
        complete.reset_mock()
        state = workspace.state()
        assert active.call_count == complete.call_count == 1
        assert state["stage"] == stage
        assert state["business_complete"] is business_complete
        actions = state["actions"]
        assert actions["next_operation"] == operation
        for name in ("preview", "approve", "apply"):
            assert actions[f"can_{name}"] is (operation == name)
        if state["change_events"]:
            event = state["change_events"][0]
            assert event["status"] == status
            expected_active = event["event_id"] if operation is not None else None
            assert state["active_event_id"] == actions["next_event_id"] == expected_active
            assert actions["owner_id"] == (event["owner_id"] if expected_active else None)
        return state

    empty = observe("EMPTY", None, None)
    assert empty["quote"] is None and empty["actions"]["can_form"] is True
    workspace.form_quote()
    observe("CURRENT", None, None, business_complete=True)

    request = command(workspace)
    submit_change(workspace, request)
    received = observe("CURRENT", "RECEIVED", "preview")
    bundle = workspace.preview_change(request.event_id)
    observe("PREVIEWED", "PREVIEWED", "approve")
    approval = workspace.approve_change(request.event_id, actor_id=workspace.change_owner[request.event_id],
                                        preview_digest=bundle.preview.digest)
    observe("APPROVED", "APPROVED", "apply")
    workspace.apply_approved_change(request.event_id, approval_digest=approval["approval_digest"])
    applied = observe("CURRENT", "APPLIED", None, business_complete=True)
    assert applied["quote"]["payload"]["product_plan"] == request.value
    assert received["change_events"][0]["status"] == "RECEIVED"
    assert received["quote"]["digest"] != applied["quote"]["digest"]


def test_history_page_reuses_selected_event_without_losing_an_older_pending_change(workspace, monkeypatch):
    workspace.form_quote()
    first = command(workspace, event_id="older")
    second = command(workspace, event_id="newer", value="Second plan")
    submit_change(workspace, first)
    submit_change(workspace, second)
    active = Mock(wraps=workspace._active_event_id)
    status = Mock(wraps=workspace._change_status)
    monkeypatch.setattr(workspace, "_active_event_id", active)
    monkeypatch.setattr(workspace, "_change_status", status)

    state = workspace.state(history_limit=1)

    assert active.call_count == 1
    assert Counter(call.args[0] for call in status.call_args_list) == {"older": 2, "newer": 1}
    assert [event["event_id"] for event in state["change_events"]] == ["older", "newer"]
    assert state["change_history"] == {"total": 2, "pending": 2, "limit": 1, "returned": 2}
    assert state["active_event_id"] == state["actions"]["next_event_id"] == "older"
    assert state["actions"]["owner_id"] == workspace.change_owner["older"]


def test_competition_projection_uses_one_business_completion_decision(workspace, monkeypatch):
    evidence = {"run_id": workspace.effective_workflow_run_id}
    monkeypatch.setattr(workspace, "_competition_evidence_record", lambda: evidence)
    complete = Mock(wraps=workspace._business_complete)
    monkeypatch.setattr(workspace, "_business_complete", complete)

    state = workspace.state()

    assert complete.call_count == 1
    assert state["business_complete"] is False
    assert state["experience_governance"]["status"] == "WAITING_FOR_PENDING_CHANGES"
    assert state["experience_governance"]["target_writes"] == 0


def test_state_http_bounds_history_but_retains_active_work(workspace):
    workspace.form_quote()
    for number in range(8):
        submit_change(workspace, command(workspace, event_id=f"change-{number}", value=f"Plan {number}"))
    head = workspace.store.audit_head()
    with TestClient(create_app(workspace_service=workspace)) as client:
        complete = client.get("/api/workspace/state").json()
        bounded = client.get("/api/workspace/state?history_limit=2").json()
        assert bounded["change_history"] == {"total": 8, "pending": 8, "limit": 2, "returned": 3}
        assert {e["event_id"] for e in bounded["change_events"]} == {"change-0", "change-6", "change-7"}
        assert bounded["active_event_id"] == complete["active_event_id"] == "change-0"
        assert bounded["quote"] == complete["quote"]
        assert bounded["actions"] == complete["actions"]
        for limit in ("0", "101", "not-a-number"):
            assert client.get(f"/api/workspace/state?history_limit={limit}").status_code == 422
        assert workspace.store.audit_head() == head


@pytest.mark.parametrize("phase", ["RECEIVED", "PREVIEWED", "APPROVED", "APPLIED"])
def test_reused_records_preserve_full_state_with_fewer_reads(workspace, monkeypatch, phase):
    workspace.form_quote()
    request = command(workspace)
    submit_change(workspace, request)
    if phase != "RECEIVED":
        bundle = workspace.preview_change(request.event_id)
    if phase in {"APPROVED", "APPLIED"}:
        approval = workspace.approve_change(request.event_id, actor_id=workspace.change_owner[request.event_id],
                                            preview_digest=bundle.preview.digest)
    if phase == "APPLIED":
        workspace.apply_approved_change(request.event_id, approval_digest=approval["approval_digest"])
    original_status = workspace._change_status
    audit = workspace.store.audit_head()

    def observe(*, reuse_records):
        def status(event_id, *, records=None):
            return original_status(event_id, records=records if reuse_records else None)

        monkeypatch.setattr(workspace, "_change_status", status)
        statements = []
        workspace.store.connection.set_trace_callback(statements.append)
        try:
            state = workspace.state()
        finally:
            workspace.store.connection.set_trace_callback(None)
        return state, sum(sql.lstrip().upper().startswith("SELECT") for sql in statements)

    previous, previous_reads = observe(reuse_records=False)
    current, current_reads = observe(reuse_records=True)

    assert current == previous
    assert current["change_events"][0]["status"] == phase
    assert current_reads < previous_reads
    assert workspace.store.audit_head() == audit


def test_reused_records_do_not_cache_current_owner_authorization(workspace, monkeypatch):
    from orgrebase.domain import FreshnessError
    from orgrebase.workspace import enterprise_binding

    workspace.form_quote()
    request = command(workspace)
    submit_change(workspace, request)
    bundle = workspace.preview_change(request.event_id)
    records = workspace.state()["changes"][request.event_id]
    assert records["preview"]["preview_digest"] == bundle.preview.digest

    def denied(*args):
        raise FreshnessError("OWNER_CHANGED", "Current source ownership changed")

    monkeypatch.setattr(enterprise_binding, "require_change_owner", denied)
    with workspace.store.read_snapshot():
        assert workspace._change_status(request.event_id, records=records) == "EXPIRED"


@pytest.mark.parametrize("phase", ["RECEIVED", "PREVIEWED", "APPROVED", "APPLIED"])
def test_batched_history_preserves_state_and_reduces_database_reads(workspace, monkeypatch, phase):
    workspace.form_quote()
    request = command(workspace)
    submit_change(workspace, request)
    if phase != "RECEIVED":
        bundle = workspace.preview_change(request.event_id)
    if phase in {"APPROVED", "APPLIED"}:
        approval = workspace.approve_change(request.event_id, actor_id=workspace.change_owner[request.event_id],
                                            preview_digest=bundle.preview.digest)
    if phase == "APPLIED":
        workspace.apply_approved_change(request.event_id, approval_digest=approval["approval_digest"])
    for index in range(5):
        submit_change(workspace, command(workspace, event_id=f"pending-{index}", value=f"Plan {index}"))

    batch_reader = workspace._change_records
    audit = workspace.store.audit_head()

    def individual_reader(event_ids):
        return {event_id: {
            "preview": workspace._preview_record(event_id),
            "approval": workspace._approval_record(event_id),
            "outcome": workspace._outcome_record(event_id),
        } for event_id in event_ids}

    def observe(reader):
        monkeypatch.setattr(workspace, "_change_records", reader)
        statements = []
        workspace.store.connection.set_trace_callback(statements.append)
        try:
            state = workspace.state()
        finally:
            workspace.store.connection.set_trace_callback(None)
        return state, sum(sql.lstrip().upper().startswith("SELECT") for sql in statements)

    previous, previous_reads = observe(individual_reader)
    current, current_reads = observe(batch_reader)
    assert current == previous
    assert current_reads < previous_reads
    assert workspace.store.audit_head() == audit


@pytest.mark.parametrize("record_type", ["preview", "approval", "outcome"])
@pytest.mark.parametrize("corruption,error", [
    ("media_type", "ARTIFACT_MEDIA_TYPE_MISMATCH"),
    ("payload_digest", "ARTIFACT_DIGEST_MISMATCH"),
])
def test_batched_history_rejects_corrupt_optional_records(workspace, record_type, corruption, error):
    from orgrebase.domain import IntegrityError

    workspace.form_quote()
    request = command(workspace)
    submit_change(workspace, request)
    bundle = workspace.preview_change(request.event_id)
    approval = workspace.approve_change(request.event_id, actor_id=workspace.change_owner[request.event_id],
                                        preview_digest=bundle.preview.digest)
    workspace.apply_approved_change(request.event_id, approval_digest=approval["approval_digest"])
    record = workspace.state()["changes"][request.event_id][record_type]
    # The column is a fixed parameterized test value, not an application input.
    workspace.store.connection.execute(
        f"UPDATE artifacts SET {corruption}=? WHERE artifact_id=?", ("tampered", record["artifact_id"]),
    )
    with workspace.store.read_snapshot(), pytest.raises(IntegrityError, match=error):
        workspace._change_records((request.event_id,))
