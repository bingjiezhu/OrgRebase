from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from orgrebase.api import _workspace_current_run_archive_view
from orgrebase.domain import FreshnessError, IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

PACK = Path(__file__).resolve().parents[2] / "examples/enterprise-quote-pilot/evergreen"


def proposal(service: WorkspaceService, event_id: str, slot_id: str, value: str) -> ChangeEvent:
    binding = next(item for item in service.enterprise_binding.resources if item.slot_id == slot_id)
    base = service.store.get_object(binding.object_id)
    payload = base.model_dump(mode="json", exclude={"digest"})
    payload.update(
        version=f"v{int(base.version[1:]) + 1}",
        state=ObjectState.PROPOSED,
        payload={**base.payload, "canonical_value": value},
        source_refs=(f"source:change-feed:{event_id}@r1",),
    )
    return ChangeEvent(
        event_id=event_id,
        organization_id=service.profile.organization_id,
        slot_id=slot_id,
        owner_id=binding.owner_id,
        base_version=base.version,
        base_digest=base.digest,
        proposal=VersionedObject.model_validate(payload),
        occurred_at="2026-08-15T00:03:00Z",
    )


class ReviewClock:
    def __init__(self) -> None:
        self.value = 1_800_000_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self) -> None:
        self.value += 1


def apply(service: WorkspaceService, event_id: str) -> dict:
    preview = service.preview_change(event_id)
    if isinstance(service._wall_clock, ReviewClock):
        service._wall_clock.advance()
    approval = service.approve_change(
        event_id, actor_id=service.change_owner[event_id], preview_digest=preview.preview.digest
    )
    return service.apply_approved_change(event_id, approval_digest=approval["approval_digest"])


def make_service(path: Path, *, review_clock: ReviewClock | None = None) -> WorkspaceService:
    service = WorkspaceService(store_path=path, runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
                               review_duration_seconds=0.001 if review_clock else 0, wall_clock=review_clock)
    service.form_quote()
    for event_id in ("currency", "launch_date"):
        apply(service, event_id)
    return service


def test_twenty_changes_with_reordered_fields_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "continuous.sqlite"
    review_clock = ReviewClock()
    service = make_service(path, review_clock=review_clock)
    previous_version = service.current_quote().version
    for index in range(20):
        slot = ("product_plan", "currency", "launch_date")[index % 3]
        value = {
            "product_plan": f"Enterprise {index}",
            "currency": "USD" if index % 2 else "EUR",
            "launch_date": str(date(2027, 1, 1) + timedelta(days=index)),
        }[slot]
        if slot == "currency" and service.current_quote().payload[slot] == value:
            value = "GBP"
        event = proposal(service, f"feed-{index:02d}", slot, value)
        registered = service.register_change(event)
        before = service.current_quote()
        assert service.register_change(event)["event_digest"] == registered["event_digest"]
        assert service.current_quote().digest == before.digest
        result = apply(service, event.event_id)
        after = service.current_quote()
        assert after.payload[slot] == value
        assert int(after.version[1:]) == int(previous_version[1:]) + 1
        for field in ("product_plan", "currency", "launch_date"):
            if field != slot:
                assert after.payload[field] == before.payload[field]
        assert service.register_change(event)["event_digest"] == event.digest
        assert (
            service.apply_approved_change(
                event.event_id, approval_digest=result["outcome"]["approval_digest"]
            )["artifact_digest"]
            == result["artifact_digest"]
        )
        previous_version = after.version
        if index == 9:
            service.close()
            service = WorkspaceService.reopen(
                path, runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
                review_duration_seconds=0.001, wall_clock=review_clock,
            )
            assert service.current_quote().digest == after.digest
    state = service.state()
    assert state["quote"]["version"] == "v23"
    assert state["stage"] == "CURRENT"
    assert state["business_complete"] is True
    assert len(state["change_events"]) == 22
    assert state["event_chain"]["status"] == "PASS"
    archive = _workspace_current_run_archive_view(service)
    assert archive["status"] == "ARCHIVED", archive.get("failures")
    assert [item["successor_quote_version"] for item in archive["record"]["selective_rebase_receipts"]] == [f"v{i}" for i in range(2, 24)]
    service.close()


def test_same_event_id_with_another_payload_is_rejected(tmp_path: Path) -> None:
    service = make_service(tmp_path / "conflict.sqlite")
    event = proposal(service, "same-id", "product_plan", "Plan A")
    service.register_change(event)
    other = proposal(service, "same-id", "product_plan", "Plan B")
    before = service.current_quote().digest
    with pytest.raises(IntegrityError, match="CHANGE_EVENT_ID_CONFLICT"):
        service.register_change(other)
    assert service.current_quote().digest == before
    service.close()


def test_old_source_revision_does_not_overwrite_another_change(tmp_path: Path) -> None:
    service = make_service(tmp_path / "stale.sqlite")
    stale = proposal(service, "out-of-order", "product_plan", "Late Plan")
    first = proposal(service, "first", "product_plan", "Current Plan")
    service.register_change(first)
    apply(service, first.event_id)
    before = service.current_quote().digest
    with pytest.raises(FreshnessError, match="CHANGE_EVENT_BASE_STALE"):
        service.register_change(stale)
    assert service.current_quote().digest == before
    service.close()


def test_related_change_invalidates_approved_snapshot(tmp_path: Path) -> None:
    service = make_service(tmp_path / "pending.sqlite")
    first = proposal(service, "pending-plan", "product_plan", "Plan A")
    other = proposal(service, "new-currency", "currency", "USD")
    service.register_change(first)
    preview = service.preview_change(first.event_id)
    approval = service.approve_change(
        first.event_id, actor_id=first.owner_id, preview_digest=preview.preview.digest
    )
    service.register_change(other)
    apply(service, other.event_id)
    before = service.current_quote().digest
    assert service._change_status(first.event_id) == "STALE"
    with pytest.raises(RuntimeError, match="WORKSPACE_APPROVAL_PREDECESSOR_STALE"):
        service.apply_approved_change(first.event_id, approval_digest=approval["approval_digest"])
    assert service.current_quote().digest == before
    service.close()


@pytest.mark.parametrize(
    "slot,value,code",
    [
        ("launch_date", "2026-02-30", "CHANGE_EVENT_DATE_INVALID"),
        ("currency", "usd", "CHANGE_EVENT_CURRENCY_INVALID"),
        ("product_plan", "", "CHANGE_EVENT_VALUE_REQUIRED"),
    ],
)
def test_invalid_business_values_are_not_admitted(tmp_path: Path, slot: str, value: str, code: str) -> None:
    service = make_service(tmp_path / "invalid.sqlite")
    event = proposal(service, "invalid", slot, value)
    before = service.current_quote().digest
    with pytest.raises(IntegrityError, match=code):
        service.register_change(event)
    assert service.current_quote().digest == before
    service.close()


def test_two_workspaces_do_not_share_event_or_object_state(tmp_path: Path) -> None:
    first = make_service(tmp_path / "first.sqlite")
    second_pack = tmp_path / "second-pack"
    shutil.copytree(PACK, second_pack)
    manifest_path = second_pack / "pack.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runtime"].update(quote_object_id="work:quote-second-order",
                               quote_label="Second order quote",
                               default_run_id="run:workspace:second-order@v1",
                               graph_snapshot_id="workspace-graph:second-order")
    manifest_path.write_text(json.dumps(manifest))
    second_runtime = load_enterprise_quote_pilot_pack(second_pack)
    second = WorkspaceService(store_path=tmp_path / "second.sqlite",
                              runtime_configuration=second_runtime,
                              workflow_run_id=second_runtime.default_run_id)
    second.form_quote()
    for event_id in ("currency", "launch_date"):
        apply(second, event_id)
    assert first.profile.organization_id == second.profile.organization_id
    assert first.current_quote().id != second.current_quote().id
    second_before = second.current_quote().digest
    event = proposal(first, "isolated", "product_plan", "First only")
    first.register_change(event)
    apply(first, event.event_id)
    assert second.current_quote().digest == second_before
    assert "isolated" not in second.change_order
    with pytest.raises(KeyError):
        second.changes.get(event.event_id)
    first.close()
    second.close()


def test_source_invalidation_requires_explicit_owner_readmission(tmp_path: Path) -> None:
    service = make_service(tmp_path / "readmit.sqlite")
    initial = service.current_quote()
    source_url = "https://source.example/quotes/123/product-plan"
    receipt = service.invalidate_source("product_plan", source_url, "FIELD_NULL")
    assert receipt["status"] == "OWNER_READMISSION_REQUIRED"
    assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
    assert service.state()["source_gaps"][0]["slot_id"] == "product_plan"
    with pytest.raises(RuntimeError, match="OWNER_READMISSION_REQUIRED"):
        service.export_quote()
    rejected = proposal(service, "ordinary-update", "product_plan", "Replacement")
    with pytest.raises(FreshnessError, match="CHANGE_EVENT_SOURCE_STATE_MISMATCH"):
        service.register_change(rejected)
    observed = proposal(service, "owner-readmission", "product_plan", initial.payload["product_plan"])
    data = observed.model_dump(mode="json", exclude={"digest"})
    data["operation"] = "READMIT"
    event = ChangeEvent.model_validate(data)
    service.register_change(event)
    preview = service.preview_change(event.event_id)
    assert preview.change_set.state == "READMISSION_ADMITTED"
    assert preview.change_set.deltas[0].semantic_classification.value == "TRUST_REVALIDATION"
    with pytest.raises(RuntimeError, match="WORKSPACE_APPROVER_MISMATCH"):
        service.approve_change(event.event_id, actor_id="wrong-owner", preview_digest=preview.preview.digest)
    result = apply(service, event.event_id)
    assert result["state"]["source_gaps"] == []
    assert service.current_quote().state == ObjectState.CURRENT
    assert service.current_quote().payload["product_plan"] == initial.payload["product_plan"]
    second = service.invalidate_source("product_plan", source_url, "FIELD_NULL")
    assert second["object_ref"] != receipt["object_ref"]
    assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
    assert service.state()["source_gaps"]
    service.close()


def test_deleted_unrelated_premise_blocks_apply_and_preserves_gap(tmp_path: Path) -> None:
    service = make_service(tmp_path / "premise.sqlite")
    event = proposal(service, "update-plan", "product_plan", "Another plan")
    service.register_change(event)
    preview = service.preview_change(event.event_id)
    approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                      preview_digest=preview.preview.digest)
    service.invalidate_source("currency", "source:currency", "SOURCE_DELETED")
    before = service.current_quote().digest
    with pytest.raises((RuntimeError, ValueError, IntegrityError), match="SOURCE_PREMISE_NOT_CURRENT"):
        service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
    assert service.current_quote().digest == before
    assert service.state()["source_gaps"][0]["slot_id"] == "currency"
    service.close()


def test_event_registration_does_not_escape_caller_rollback(tmp_path: Path) -> None:
    service = make_service(tmp_path / "rollback.sqlite")
    event = proposal(service, "rollback-event", "product_plan", "Rolled back")
    with pytest.raises(RuntimeError, match="rollback"), service.store.transaction() as connection:
        service.register_change(event, connection=connection)
        raise RuntimeError("rollback")
    assert event.event_id not in service.change_order
    with pytest.raises(KeyError):
        service.changes.get(event.event_id)
    service.close()


def test_polling_does_not_rescan_journal_or_all_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(tmp_path / "incremental.sqlite")
    service.state()
    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded historical scan")
    monkeypatch.setattr(service.store, "event_envelopes", forbidden)
    monkeypatch.setattr(service.store, "event_records", forbidden)
    monkeypatch.setattr(service.store, "verify_event_chain", forbidden)
    monkeypatch.setattr(service.store, "list_artifacts", forbidden)
    for _ in range(3):
        state = service.state(history_limit=1)
        assert len(state["change_events"]) == 1
        assert state["change_history"]["total"] == 2
        assert state["business_complete"] is True
    page = service.change_history(limit=1)
    assert page["next_cursor"] == 1
    assert service.change_history(after=1, limit=1)["next_cursor"] is None
    service.close()


def test_scheduled_and_expired_events_cannot_preview(tmp_path: Path) -> None:
    service = make_service(tmp_path / "effective.sqlite")
    for event_id, valid_from, valid_to, status in (
        ("future", "2027-01-01T00:00:00Z", None, "SCHEDULED"),
        ("expired", "2026-08-01T00:00:00Z", "2026-08-14T00:00:00Z", "EXPIRED"),
    ):
        event = proposal(service, event_id, "product_plan", event_id)
        data = event.model_dump(mode="json", exclude={"digest"})
        data["proposal"].pop("digest")
        data["proposal"].update(version=f"proposal-{event_id}", valid_from=valid_from, valid_to=valid_to)
        service.register_change(ChangeEvent.model_validate(data))
        assert service._change_status(event_id) == status
        with pytest.raises(RuntimeError, match="NOT_PREVIEWABLE"):
            service.preview_change(event_id)
    service.close()


def test_unknown_slot_is_rejected_without_writes(tmp_path: Path) -> None:
    service = make_service(tmp_path / "unknown.sqlite")
    event = proposal(service, "unknown", "product_plan", "New Plan")
    data = event.model_dump(mode="json", exclude={"digest"})
    data["slot_id"] = "discount_formula"
    before = service.store.verify_event_chain()
    with pytest.raises(IntegrityError, match="CHANGE_EVENT_SLOT_UNSUPPORTED"):
        service.register_change(ChangeEvent.model_validate(data))
    assert service.store.verify_event_chain() == before
    service.close()


def test_owner_rejection_blocks_approval_and_revision_uses_new_event(tmp_path: Path) -> None:
    service = make_service(tmp_path / "rejected.sqlite")
    event = proposal(service, "review-1", "product_plan", "Unacceptable plan")
    service.register_change(event)
    preview = service.preview_change(event.event_id)
    approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                      preview_digest=preview.preview.digest)
    with pytest.raises(RuntimeError, match="WORKSPACE_APPROVER_MISMATCH"):
        service.reject_change(event.event_id, actor_id="other-owner", reason="Declined")
    rejected = service.reject_change(event.event_id, actor_id=event.owner_id, reason="Plan must include support")
    assert rejected["rejection"]["status"] == "REJECTED"
    assert service.reject_change(event.event_id, actor_id=event.owner_id, reason="Plan must include support")["rejection"] == rejected["rejection"]
    before = service.current_quote().digest
    with pytest.raises(RuntimeError, match="WORKSPACE_CHANGE_NOT_APPLICABLE"):
        service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
    assert service.current_quote().digest == before
    revision = proposal(service, "review-2", "product_plan", "Enterprise with support")
    data = revision.model_dump(mode="json", exclude={"digest"})
    data["proposal"].pop("digest")
    data["proposal"]["version"] += "-revision-2"
    revised = ChangeEvent.model_validate(data)
    service.register_change(revised)
    apply(service, revised.event_id)
    assert service._change_status(event.event_id) == "REJECTED"
    assert service.current_quote().payload["product_plan"] == "Enterprise with support"
    service.close()

def test_committed_rebase_cannot_be_rejected_after_outcome_projection_crash(tmp_path, monkeypatch):
    service = make_service(tmp_path / "committed-before-outcome.sqlite")
    event = proposal(service, "commit-before-outcome", "product_plan", "Committed plan")
    service.register_change(event)
    preview = service.preview_change(event.event_id)
    approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                     preview_digest=preview.preview.digest)
    with monkeypatch.context() as fault:
        def crash(*args, **kwargs):
            raise RuntimeError("AFTER_CORE_COMMIT")
        fault.setattr(service, "_persist_outcome", crash)
        with pytest.raises(RuntimeError, match="AFTER_CORE_COMMIT"):
            service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
    committed = service.current_quote()
    assert service._outcome_record(event.event_id) is None
    with pytest.raises(IntegrityError, match="CHANGE_ALREADY_APPLIED"):
        service.reject_change(event.event_id, actor_id=event.owner_id, reason="Too late")
    assert service.changes.rejection(event.event_id) is None
    recovered = service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
    assert recovered["state"]["quote"]["digest"] == committed.digest
    assert service.current_quote().version == committed.version
    service.close()


def test_rejected_event_cannot_return_an_old_approval_as_a_new_approval(tmp_path):
    service = make_service(tmp_path / "reject-approved-retry.sqlite")
    event = proposal(service, "declined-approval", "product_plan", "Declined plan")
    service.register_change(event)
    preview = service.preview_change(event.event_id)
    service.approve_change(event.event_id, actor_id=event.owner_id,
                           preview_digest=preview.preview.digest)
    service.reject_change(event.event_id, actor_id=event.owner_id, reason="Revise support scope")
    before = service.store.verify_event_chain()
    with pytest.raises(RuntimeError, match="WORKSPACE_CHANGE_REJECTED"):
        service.approve_change(event.event_id, actor_id=event.owner_id,
                               preview_digest=preview.preview.digest)
    assert service.store.verify_event_chain() == before
    service.close()


def test_expired_authorization_cannot_add_tool_evidence_to_existing_formation(tmp_path):
    from orgrebase.auth import request_authorization
    from orgrebase.domain import AuthorizationError
    service = WorkspaceService(store_path=tmp_path / "expired-tool-retry.sqlite")
    service.form_quote()
    before_chain = service.store.verify_event_chain()
    before_counts = service.store.count_records()
    def expired():
        raise AuthorizationError("SESSION_EXPIRED")
    token = request_authorization.set(expired)
    try:
        with pytest.raises(AuthorizationError, match="SESSION_EXPIRED"):
            service.form_quote_with_dependency_evidence()
    finally:
        request_authorization.reset(token)
    assert service.store.verify_event_chain() == before_chain
    assert service.store.count_records() == before_counts
    assert service.state()["dependency_evidence_tool"]["status"] == "NOT_RUN"
    service.close()
