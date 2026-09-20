from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from enterprise_pack_factory import make_enterprise_pack
from pydantic import ValidationError

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import FreshnessError, IntegrityError, VersionedObject
from orgrebase.workspace import runtime_revision
from orgrebase.workspace.change_proposals import (
    SUBMISSION_MEDIA_TYPE,
    ChangeProposalInput,
    ReviewObservationInput,
    change_detail,
    change_options,
    record_review,
    submit_change,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def workspace(tmp_path: Path):
    service = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite",
        runtime_configuration=load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path)),
        review_duration_seconds=0,
    )
    service.form_quote()
    try:
        yield service
    finally:
        service.close()


def command(workspace, event_id="edit-1", slot="product_plan", value="Enterprise Plus", **kwargs):
    field = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == slot)
    return ChangeProposalInput(
        event_id=event_id, slot_id=slot, value=value, source_ref="source:owner-reviewed-spec@r2",
        base_version=field["current"]["version"], base_digest=field["current"]["digest"], **kwargs,
    )


def apply(workspace, event_id):
    preview = workspace.preview_change(event_id)
    approval = workspace.approve_change(event_id, actor_id=workspace.change_owner[event_id],
                                        preview_digest=preview.preview.digest)
    return workspace.apply_approved_change(event_id, approval_digest=approval["approval_digest"])


def test_edit_is_idempotent_and_uses_the_existing_preview_approval_apply_path(workspace):
    currency = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == "currency")
    assert currency["allowed_operations"] == ["UPDATE"] and currency["blocked_reason"] is None
    before = workspace.current_quote()
    request = command(workspace)
    first = submit_change(workspace, request)
    assert submit_change(workspace, request) == first
    assert workspace.current_quote().digest == before.digest
    assert change_detail(workspace, request.event_id)["status"] == "RECEIVED"
    apply(workspace, request.event_id)
    assert workspace.current_quote().payload["product_plan"] == request.value
    assert submit_change(workspace, request) == first
    assert change_detail(workspace, request.event_id)["allowed_actions"] == []


def test_detail_reuses_the_loaded_preview_and_refreshes_after_rejection(workspace, monkeypatch):
    request = command(workspace)
    submit_change(workspace, request)
    bundle = workspace.preview_change(request.event_id)
    read_preview = Mock(wraps=workspace._preview_record)
    monkeypatch.setattr(workspace, "_preview_record", read_preview)

    detail = change_detail(workspace, request.event_id)
    # Status validation and response loading each read once; rendering does not read again.
    assert read_preview.call_count == 2
    assert detail["preview"]["preview_digest"] == bundle.preview.digest
    assert detail["runtime_compatibility"]["decision"] == "CONTINUE"
    assert detail["status"] == "PREVIEWED"

    workspace.reject_change(request.event_id, actor_id=workspace.change_owner[request.event_id],
                            reason="Source owner requested a revision")
    read_preview.reset_mock()
    rejected = change_detail(workspace, request.event_id)
    assert read_preview.call_count == 1
    assert rejected["status"] == "REJECTED"
    assert "APPROVE" not in rejected["allowed_actions"]
    assert rejected["preview"] == detail["preview"]


def test_two_competing_edits_do_not_collide_in_the_version_table(workspace):
    first, second = command(workspace), command(workspace, "edit-2", value="Another plan")
    a, b = submit_change(workspace, first), submit_change(workspace, second)
    assert a["event"]["proposal"]["version"] != b["event"]["proposal"]["version"]
    apply(workspace, first.event_id)
    assert change_detail(workspace, second.event_id)["status"] == "STALE"


def test_same_event_id_with_changed_command_cannot_borrow_prior_result(workspace):
    first = command(workspace)
    submit_change(workspace, first)
    with pytest.raises(IntegrityError, match="CHANGE_EVENT_ID_CONFLICT"):
        submit_change(workspace, first.model_copy(update={"source_ref": "source:another-document@r1"}))


def test_rejected_edit_is_revised_with_a_new_event_and_no_reused_approval(workspace):
    old = command(workspace)
    submit_change(workspace, old)
    workspace.preview_change(old.event_id)
    workspace.reject_change(old.event_id, actor_id=workspace.change_owner[old.event_id], reason="Needs another plan")
    newer = command(workspace, "edit-2", value="Revised Plan", revises_event_id=old.event_id)
    submit_change(workspace, newer)
    detail = change_detail(workspace, newer.event_id)
    assert detail["revises_event_id"] == old.event_id
    assert detail["preview"] is None and detail["approval"] is None
    assert change_detail(workspace, old.event_id)["rejection"]["reason"] == "Needs another plan"
    apply(workspace, newer.event_id)
    assert change_detail(workspace, old.event_id)["status"] == "REJECTED"


@pytest.mark.parametrize("previously_approved", [False, True])
def test_runtime_change_can_be_revised_previewed_approved_and_applied(workspace, monkeypatch, previously_approved):
    old = command(workspace)
    submit_change(workspace, old)
    original_preview = workspace.preview_change(old.event_id)
    if previously_approved:
        workspace.approve_change(old.event_id, actor_id=workspace.change_owner[old.event_id],
                                 preview_digest=original_preview.preview.digest)
    original_approval = workspace._approval_record(old.event_id)
    before_quote = workspace.current_quote().digest
    current = runtime_revision.current_revision()
    changed = {**current, "handler_contract": "orgrebase.quote-handler.delta-set.test-upgrade"}
    changed["revision_digest"] = sha256_digest({key: value for key, value in changed.items() if key != "revision_digest"})
    monkeypatch.setattr(runtime_revision, "current_revision", lambda: changed)

    detail = change_detail(workspace, old.event_id)
    assert detail["status"] == "EXPIRED" and detail["status_reason"] == "RUNTIME_IMPLEMENTATION_CHANGED"
    assert "REVISE" in detail["allowed_actions"]
    assert not {"PREVIEW", "APPROVE", "APPLY"}.intersection(detail["allowed_actions"])
    revised = command(workspace, "edit-revised", value=old.value, revises_event_id=old.event_id)
    submit_change(workspace, revised)
    detail = change_detail(workspace, revised.event_id)
    assert detail["status"] == "RECEIVED" and "PREVIEW" in detail["allowed_actions"]
    assert detail["revises_event_id"] == old.event_id and detail["approval"] is None
    fresh_preview = workspace.preview_change(revised.event_id)
    assert change_detail(workspace, revised.event_id)["runtime_compatibility"]["decision"] == "CONTINUE"
    assert workspace.current_quote().digest == before_quote
    approval = workspace.approve_change(revised.event_id, actor_id=workspace.change_owner[revised.event_id],
                                        preview_digest=fresh_preview.preview.digest)
    workspace.apply_approved_change(revised.event_id, approval_digest=approval["approval_digest"])
    assert workspace.current_quote().payload["product_plan"] == old.value
    assert workspace._preview_record(old.event_id)["preview_digest"] == original_preview.preview.digest
    assert workspace._approval_record(old.event_id) == original_approval


def test_expired_source_does_not_advertise_an_unsubmittable_revision(workspace):
    binding = next(item for item in workspace.enterprise_binding.resources if item.slot_id == "product_plan")
    current = workspace.store.get_object(binding.object_id)
    expires_at = timestamp(utc_datetime(workspace.clock.now()) + timedelta(minutes=5))
    expiring = VersionedObject.model_validate({
        **current.model_dump(mode="json", exclude={"digest"}), "version": "source-expiring", "valid_to": expires_at,
    })
    with workspace.store.transaction() as connection:
        workspace.store.insert_version(connection, expiring, make_current=True)
    submit_change(workspace, command(workspace))
    workspace.clock = FrozenClock(expires_at)
    field = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == "product_plan")
    assert field["allowed_operations"] == [] and field["blocked_reason"] == "SOURCE_VALIDITY_EXPIRED"
    detail = change_detail(workspace, "edit-1")
    assert detail["status"] == "EXPIRED"
    assert "REVISE" not in detail["allowed_actions"]
    with pytest.raises(FreshnessError, match="SOURCE_VALIDITY_EXPIRED"):
        submit_change(workspace, command(workspace, "edit-revised", revises_event_id="edit-1"))


def test_revision_cannot_cross_fields_or_silently_replace_an_open_proposal(workspace):
    submit_change(workspace, command(workspace))
    with pytest.raises(IntegrityError, match="CHANGE_REVISION_REQUIRES_CLOSED_PROPOSAL"):
        submit_change(workspace, command(workspace, "edit-2", revises_event_id="edit-1"))
    with pytest.raises(IntegrityError, match="CHANGE_REVISION_SCOPE_MISMATCH"):
        submit_change(workspace, command(workspace, "edit-3", slot="currency", value="GBP", revises_event_id="edit-1"))


def test_stale_form_does_not_rebase_itself_silently(workspace):
    stale = command(workspace, "stale")
    submit_change(workspace, command(workspace))
    apply(workspace, "edit-1")
    with pytest.raises(FreshnessError, match="CHANGE_EVENT_BASE_STALE"):
        submit_change(workspace, stale)


def test_submission_failure_rolls_back_event_candidate_and_receipt(workspace, monkeypatch):
    request = command(workspace)
    save = workspace.store.save_artifact

    def fail(connection, artifact_id, media_type, payload):
        if media_type == SUBMISSION_MEDIA_TYPE:
            raise RuntimeError("submission storage unavailable")
        return save(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(workspace.store, "save_artifact", fail)
    with pytest.raises(RuntimeError, match="submission storage"):
        submit_change(workspace, request)
    with pytest.raises(KeyError):
        workspace.changes.get(request.event_id)
    assert workspace.changes.count == 0
    monkeypatch.setattr(workspace.store, "save_artifact", save)
    assert submit_change(workspace, request)["event_id"] == request.event_id


def test_readmission_is_explicit_and_can_restore_the_same_business_value(workspace):
    field = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == "product_plan")
    workspace.changes.invalidate("product_plan", "source:permission-revoked", "PERMISSION_DENIED")
    updated = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == "product_plan")
    assert updated["allowed_operations"] == ["READMIT"]
    request = command(workspace, value=field["current"]["value"], operation="READMIT")
    submit_change(workspace, request)
    apply(workspace, request.event_id)
    assert workspace.current_quote().payload["product_plan"] == field["current"]["value"]


@pytest.mark.parametrize("slot,value", [("currency", "usd"), ("launch_date", "2026-02-30")])
def test_business_field_validation_is_not_bypassed_by_the_web_editor(workspace, slot, value):
    with pytest.raises(IntegrityError):
        submit_change(workspace, command(workspace, slot=slot, value=value))


@pytest.mark.parametrize("action,outcome", [("APPROVE", "ERROR"), ("APPROVE", "SUCCESS"), ("REJECT", "SUCCESS")])
def test_review_observation_is_exact_bounded_and_never_an_approval(workspace, action, outcome):
    submit_change(workspace, command(workspace))
    preview = workspace.preview_change("edit-1")
    observation = ReviewObservationInput(observation_id="review-1", preview_digest=preview.preview.digest,
                                         active_ms=1200, action=action, outcome=outcome, reason_code="REVIEW_PENDING")
    before = workspace.current_quote().digest
    result = record_review(workspace, "edit-1", observation)
    assert record_review(workspace, "edit-1", observation) == result
    assert result["canonical_writes"] == 0
    assert workspace.current_quote().digest == before
    detail = change_detail(workspace, "edit-1")
    assert detail["approval"] is None and detail["rejection"] is None
    assert detail["status"] == "PREVIEWED" and "APPROVE" in detail["allowed_actions"]
    with pytest.raises(IntegrityError, match="REVIEW_OBSERVATION_PREVIEW_MISMATCH"):
        record_review(workspace, "edit-1", observation.model_copy(update={"preview_digest": "sha256:" + "f" * 64}))
    with pytest.raises(ValidationError):
        ReviewObservationInput(**{**observation.model_dump(), "active_ms": True})


def test_read_only_principal_cannot_submit_or_see_authority_buttons(workspace):
    token = request_principal.set(Principal("https://identity.example", "reader", workspace.profile.organization_id,
                                          "user:reader", frozenset({"reader"}), int(time.time()) + 300))
    try:
        assert all(item["allowed_operations"] == [] for item in change_options(workspace)["fields"])
        with pytest.raises(AuthenticationError, match="AUTH_ACTION_DENIED"):
            submit_change(workspace, command(workspace))
    finally:
        request_principal.reset(token)
