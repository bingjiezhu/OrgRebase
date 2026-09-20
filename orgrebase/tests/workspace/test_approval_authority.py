from __future__ import annotations

import time
from contextlib import closing, contextmanager
from datetime import timedelta

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.approval_authority import (
    CoordinationInput,
    DelegationInput,
    authority_detail,
    escalate_change,
    grant_delegation,
    revoke_delegation,
)
from orgrebase.workspace.change_proposals import (
    ChangeProposalInput,
    change_detail,
    change_options,
    submit_change,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def delegated(tmp_path):
    with closing(WorkspaceService(store_path=tmp_path / "workspace.sqlite", review_duration_seconds=0,
                                  runtime_configuration=load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path)))) as workspace:
        workspace.form_quote()
        field = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == "product_plan")
        request = ChangeProposalInput(event_id="edit-1", slot_id="product_plan", value="Renewed service plan",
                                      source_ref="source:owner-reviewed@r2", base_version=field["current"]["version"],
                                      base_digest=field["current"]["digest"])
        event = submit_change(workspace, request)["event"]
        workspace.identity_issuer = "https://identity.example"
        members = {"owner": (event["owner_id"], "approve"), "delegate": ("user:delegate", "approve")}
        allowed = {"owner", "delegate"}

        def verify(subject, actor, action):
            if members.get(subject) != (actor, action):
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

        def scope(subject):
            if subject not in allowed:
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)

        workspace.verify_membership = verify
        workspace.authorize_workspace_subject = scope

        @contextmanager
        def identity(subject, role="approver"):
            actor = members.get(subject, (f"user:{subject}", ""))[0]
            token = request_principal.set(Principal(workspace.identity_issuer, subject, workspace.profile.organization_id,
                                                    actor, frozenset({role}), int(time.time()) + 900))
            try:
                yield actor
            finally:
                request_principal.reset(token)

        grant = DelegationInput(subject="delegate", actor_id="user:delegate", event_digest=event["digest"],
                                reason="Primary owner is away", valid_seconds=900)
        with identity("owner"):
            grant_delegation(workspace, "edit-1", grant)
        yield workspace, event, members, allowed, identity, grant


def delegated_approval(delegated):
    workspace, _, _, _, identity, _ = delegated
    bundle = workspace.preview_change("edit-1")
    with identity("delegate") as actor:
        return workspace.approve_change("edit-1", actor_id=actor, preview_digest=bundle.preview.digest)


def test_delegate_approves_exact_proposal_without_changing_original_owner(delegated):
    workspace, event, _, _, identity, _ = delegated
    approved = delegated_approval(delegated)
    assert approved["approval"]["actor_id"] == "user:delegate"
    assert approved["binding"]["owner_id"] == event["owner_id"]
    assert workspace.changes.get("edit-1").owner_id == event["owner_id"]
    with identity("executor", "executor"):
        result = workspace.apply_approved_change("edit-1", approval_digest=approved["approval_digest"])
    assert result["outcome"]["quote"]["payload"]["product_plan"] == "Renewed service plan"
    assert change_detail(workspace, "edit-1")["status"] == "APPLIED"


def test_delegate_can_reject_and_original_owner_can_revoke_without_rewriting_history(delegated):
    workspace, event, _, _, identity, _ = delegated
    with identity("delegate") as actor:
        rejected = workspace.reject_change("edit-1", actor_id=actor, reason="Insufficient commercial detail")
    assert rejected["rejection"]["actor_id"] == "user:delegate"
    with identity("owner"):
        state = revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Owner returned"))
    assert state["delegation_active"] is False and workspace.changes.get("edit-1").owner_id == event["owner_id"]


@pytest.mark.parametrize("revoke", ["grant", "membership", "scope", "expiry"])
def test_revoked_or_expired_delegation_blocks_apply_and_retains_prior_canonical(delegated, revoke):
    workspace, event, members, allowed, identity, _ = delegated
    approved = delegated_approval(delegated)
    before = workspace.current_quote().digest
    if revoke == "grant":
        with identity("owner"):
            revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Stop pending approval"))
    elif revoke == "membership":
        members.pop("delegate")
    elif revoke == "scope":
        allowed.remove("delegate")
    else:
        workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(minutes=16)))
    with identity("executor", "executor"), pytest.raises((AuthenticationError, AuthorizationError, RuntimeError)):
        workspace.apply_approved_change("edit-1", approval_digest=approved["approval_digest"])
    assert workspace.current_quote().digest == before
    assert "APPLY" not in change_detail(workspace, "edit-1")["allowed_actions"]


def test_grant_is_exact_and_cannot_be_reassigned_in_place_or_by_delegate(delegated):
    workspace, _, _, _, identity, grant = delegated
    with identity("owner"):
        original = authority_detail(workspace, "edit-1")
        assert grant_delegation(workspace, "edit-1", grant) == original
        with pytest.raises(IntegrityError, match="AUTHORITY_EVENT_DIGEST_MISMATCH"):
            grant_delegation(workspace, "edit-1", grant.model_copy(update={"event_digest": "sha256:" + "a" * 64}))
        with pytest.raises(IntegrityError, match="DELEGATION_IMMUTABLE_REVISE_PROPOSAL"):
            grant_delegation(workspace, "edit-1", grant.model_copy(update={"reason": "Different authority"}))
    with identity("delegate"), pytest.raises(AuthorizationError, match="DELEGATION_ORIGINAL_OWNER_REQUIRED"):
        grant_delegation(workspace, "edit-1", grant)


def test_escalation_requests_coordination_without_granting_the_requester_authority(delegated):
    workspace, event, _, _, identity, _ = delegated
    with identity("operator", "operator") as actor:
        record = escalate_change(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Deadline needs coordination"))
        assert record["escalation"]["authority_granted"] is False
        preview = workspace.preview_change("edit-1")
        with pytest.raises((AuthenticationError, AuthorizationError)):
            workspace.approve_change("edit-1", actor_id=actor, preview_digest=preview.preview.digest)
    assert workspace._approval_record("edit-1") is None


def test_completed_archive_retains_original_owner_and_exact_delegated_approval(delegated):
    from orgrebase.api import _workspace_current_run_archive_view

    workspace, event, _, _, identity, _ = delegated
    wall = [time.time()]
    workspace._wall_clock = lambda: wall[0]
    workspace.review_duration_ms = 4000
    preview = workspace.preview_change("edit-1")
    wall[0] += 4.01
    with identity("delegate") as actor:
        approved = workspace.approve_change("edit-1", actor_id=actor, preview_digest=preview.preview.digest)
    with identity("executor", "executor"):
        workspace.apply_approved_change("edit-1", approval_digest=approved["approval_digest"])
    archive = _workspace_current_run_archive_view(workspace)
    assert archive["status"] == "ARCHIVED", archive
    receipt = archive["record"]["selective_rebase_receipts"][0]
    assert receipt["owner_id"] == event["owner_id"]
    assert receipt["approval_actor_id"] == "user:delegate"
    assert receipt["approval_authority"] == approved["authority"]
    with identity("owner"):
        revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Close delegation"))
    later = _workspace_current_run_archive_view(workspace)
    assert later["status"] == "ARCHIVED"
    assert later["record"]["selective_rebase_receipts"] == archive["record"]["selective_rebase_receipts"]
    assert later["record"]["quote"] == archive["record"]["quote"]
    assert later["record"]["quote_event_count"] == archive["record"]["quote_event_count"] + 1


def test_history_responsibility_is_bounded_private_and_preserves_decision_actor(delegated, monkeypatch):
    workspace, event, _, _, identity, _ = delegated

    def forbidden(*args, **kwargs):
        raise AssertionError("history must not enumerate members or replay all changes")

    monkeypatch.setattr(workspace, "members_for_action", forbidden, raising=False)
    monkeypatch.setattr(workspace.changes, "all", forbidden)
    batches = []
    original = workspace.store.load_artifacts

    def load(ids, *, expected_media_type):
        assert workspace.store._snapshot_depth > 0
        batches.append((tuple(ids), expected_media_type))
        return original(ids, expected_media_type=expected_media_type)

    monkeypatch.setattr(workspace.store, "load_artifacts", load)
    with identity("owner"):
        page = workspace.change_history(limit=1)
    responsibility = page["items"][0]["responsibility"]
    assert responsibility == {"owner_id": event["owner_id"], "delegate_id": "user:delegate",
                              "blocked_reason": None, "decision_actor_id": None}
    assert "subject" not in str(responsibility) and "issuer" not in str(responsibility)
    assert len(batches) == 4
    assert len(batches[-1][0]) == 3
    assert all("edit-1" in key for ids, _ in batches for key in ids)
    assert page["next_cursor"] is None and page["observed_at"]
    with identity("delegate") as actor:
        workspace.reject_change("edit-1", actor_id=actor, reason="Needs corrected scope")
    with identity("owner"):
        revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="Owner returned"))
        rejected = workspace.change_history(limit=1)["items"][0]
    assert rejected["status"] == "REJECTED"
    assert rejected["responsibility"]["decision_actor_id"] == "user:delegate"
    assert rejected["responsibility"]["delegate_id"] is None
    assert rejected["responsibility"]["blocked_reason"] == "DELEGATION_REVOKED"


@pytest.mark.parametrize("invalid", ["expiry", "revocation", "membership", "scope"])
def test_history_never_advertises_an_invalid_delegate(delegated, invalid):
    workspace, event, members, allowed, identity, _ = delegated
    if invalid == "expiry":
        workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(minutes=16)))
    elif invalid == "revocation":
        with identity("owner"):
            revoke_delegation(workspace, "edit-1", CoordinationInput(event_digest=event["digest"], reason="End delegation"))
    elif invalid == "membership":
        members.pop("delegate")
    else:
        allowed.remove("delegate")
    with identity("owner"):
        item = workspace.change_history(limit=1)["items"][0]
    assert item["responsibility"]["owner_id"] == event["owner_id"]
    assert item["responsibility"]["delegate_id"] is None
    assert item["responsibility"]["blocked_reason"]
