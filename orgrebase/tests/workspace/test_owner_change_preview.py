from __future__ import annotations

import time
from contextlib import closing, contextmanager

import pytest
from enterprise_pack_factory import make_enterprise_pack
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, VersionedObject
from orgrebase.workspace.change_proposals import ChangeProposalInput, submit_change
from orgrebase.workspace.owner_change import OwnerChangePreviewInput, preview_owner_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.routes import change_router
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def owner_workspace(tmp_path):
    with closing(WorkspaceService(
        store_path=tmp_path / "workspace.sqlite", review_duration_seconds=0,
        runtime_configuration=load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path)),
    )) as workspace:
        workspace.form_quote()
        for slot, value in (("product_plan", "Renewed service plan"), ("currency", "EUR")):
            resource = next(item for item in workspace.enterprise_binding.resources if item.slot_id == slot)
            source = workspace.store.get_object(resource.object_id)
            submit_change(workspace, ChangeProposalInput(
                event_id=f"pending-{slot}", slot_id=slot, value=value,
                source_ref="source:owner-reviewed@r2", base_version=source.version, base_digest=source.digest,
            ))
        workspace.identity_issuer = "https://identity.example"
        members = {"operator": ("user:operator", "propose"), "next-owner": ("user:next-owner", "approve")}
        allowed = set(members)

        def verify(subject, actor, action):
            if members.get(subject) != (actor, action):
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

        def scope(subject):
            if subject not in allowed:
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)

        workspace.verify_membership = verify
        workspace.authorize_workspace_subject = scope

        @contextmanager
        def principal(*, role="operator", issuer=None, tenant=None, expires=None):
            token = request_principal.set(Principal(
                issuer or workspace.identity_issuer, "operator",
                tenant or workspace.profile.organization_id, "user:operator",
                frozenset({role}), int(time.time()) + 900 if expires is None else expires,
            ))
            try:
                yield
            finally:
                request_principal.reset(token)

        request = OwnerChangePreviewInput(
            slot_id="product_plan", expected_binding_digest=workspace.enterprise_binding.digest,
            expected_snapshot_digest=workspace.current_snapshot().digest,
            proposed_owner={"issuer": workspace.identity_issuer, "subject": "next-owner", "actor_id": "user:next-owner"},
            reason="The product owner is handing over responsibility.",
        )
        yield workspace, request, principal, members, allowed


def _dump(workspace):
    return "\n".join(workspace.store.connection.iterdump())


def _effect(workspace, effect_id, state):
    request = {"effect_id": effect_id, "action": "update", "payload": {"changes": {"currency": "EUR"}}}
    with workspace.store.transaction() as connection:
        workspace.store.put_effect(connection, effect_id=effect_id, target_key="test:quote",
                                   request_digest=sha256_digest(request), request=request,
                                   created_at=workspace.clock.now())
        if state != "READY":
            workspace.store.update_effect(connection, effect_id=effect_id, expected_state="READY",
                                          state=state, updated_at=workspace.clock.now())


def test_preview_is_content_addressed_and_changes_no_database_state(owner_workspace, monkeypatch):
    workspace, request, principal, _, _ = owner_workspace
    before = _dump(workspace)
    binding = workspace.enterprise_binding.model_dump(mode="json")

    def no_state_read():
        pytest.fail("owner preview must not call the state view or create hidden nonces")

    monkeypatch.setattr(workspace, "state", no_state_read)
    with principal():
        preview = preview_owner_change(workspace, request)
    assert _dump(workspace) == before
    assert workspace.enterprise_binding.model_dump(mode="json") == binding
    assert preview.revalidated().digest == preview.digest
    assert preview.request_digest == sha256_digest(request)
    assert preview.binding_digest == request.expected_binding_digest
    assert preview.snapshot_digest == request.expected_snapshot_digest
    assert preview.source.ref == workspace.store.get_object(preview.resource.object_id).ref
    assert preview.resource.owner_id != preview.proposed_owner.actor_id
    assert preview.proposed_binding.digest != preview.binding_digest
    assert [(old.slot_id, old.owner_id, new.owner_id) for old, new in
            zip(workspace.enterprise_binding.resources, preview.proposed_binding.resources, strict=True)
            if old != new] == [(request.slot_id, preview.resource.owner_id, "user:next-owner")]
    assert not preview.activation_allowed and preview.database_writes == 0
    assert not preview.proposed_owner_in_declared_owner_refs
    assert preview.migration_policy_status == "ORGANIZATION_AUTHORIZATION_POLICY_REQUIRED"
    assert workspace.current_quote().ref in {work.ref for work in preview.dependent_work}
    assert {event.event_id for event in preview.pending_events} == {"pending-product_plan"}
    assert preview.pending_events[0].recorded_owner_id == preview.resource.owner_id
    assert preview.pending_events[0].required_review == "REPLAN_AND_REAUTHORIZE_IF_MIGRATION_APPROVED"


@pytest.mark.parametrize(("field", "value", "error"), [
    ("expected_binding_digest", "sha256:" + "0" * 64, "OWNER_CHANGE_BINDING_CHANGED"),
    ("expected_snapshot_digest", "sha256:" + "0" * 64, "OWNER_CHANGE_SNAPSHOT_CHANGED"),
    ("slot_id", "unknown-slot", "OWNER_CHANGE_RESOURCE_UNKNOWN"),
])
def test_preview_rejects_stale_or_unknown_request_without_writes(owner_workspace, field, value, error):
    workspace, request, principal, _, _ = owner_workspace
    before = _dump(workspace)
    request = request.model_copy(update={field: value})
    with principal(), pytest.raises(IntegrityError, match=error):
        preview_owner_change(workspace, request)
    assert _dump(workspace) == before


def test_current_source_must_match_frozen_snapshot(owner_workspace, monkeypatch):
    workspace, request, principal, _, _ = owner_workspace
    resource = next(item for item in workspace.enterprise_binding.resources if item.slot_id == request.slot_id)
    original = workspace.store.get_object
    source = original(resource.object_id)
    replacement = VersionedObject.model_validate({
        **source.model_dump(mode="json", exclude={"digest"}), "version": "changed-after-snapshot",
    })

    def changed(object_id, version=None):
        return replacement if object_id == resource.object_id and version is None else original(object_id, version)

    monkeypatch.setattr(workspace.store, "get_object", changed)
    with principal(), pytest.raises(IntegrityError, match="OWNER_CHANGE_SOURCE_CHANGED"):
        preview_owner_change(workspace, request)


def test_owner_cannot_be_replaced_with_same_verified_actor(owner_workspace):
    workspace, request, principal, members, _ = owner_workspace
    current = next(item.owner_id for item in workspace.enterprise_binding.resources if item.slot_id == request.slot_id)
    members["next-owner"] = (current, "approve")
    request = request.model_copy(update={"proposed_owner": request.proposed_owner.model_copy(update={"actor_id": current})})
    with principal(), pytest.raises(IntegrityError, match="OWNER_CHANGE_OWNER_UNCHANGED"):
        preview_owner_change(workspace, request)


@pytest.mark.parametrize("denial", ["no-principal", "read-only", "wrong-tenant", "expired", "issuer",
                                   "no-member", "wrong-actor", "no-approve", "no-scope", "no-verifier", "no-scope-verifier"])
def test_preview_requires_trusted_caller_and_independent_new_owner_membership(owner_workspace, denial):
    workspace, request, principal, members, allowed = owner_workspace
    options = {}
    if denial == "read-only":
        options["role"] = "reader"
    elif denial == "wrong-tenant":
        options["tenant"] = "different-organization"
    elif denial == "expired":
        options["expires"] = 0
    elif denial == "issuer":
        request = request.model_copy(update={"proposed_owner": request.proposed_owner.model_copy(update={"issuer": "https://untrusted.example"})})
    elif denial == "no-member":
        members.pop("next-owner")
    elif denial == "wrong-actor":
        members["next-owner"] = ("user:another", "approve")
    elif denial == "no-approve":
        members["next-owner"] = ("user:next-owner", "read")
    elif denial == "no-scope":
        allowed.remove("next-owner")
    elif denial == "no-verifier":
        workspace.verify_membership = None
    elif denial == "no-scope-verifier":
        workspace.authorize_workspace_subject = None
    before = _dump(workspace)
    if denial == "no-principal":
        with pytest.raises(AuthenticationError):
            preview_owner_change(workspace, request)
    else:
        with principal(**options), pytest.raises(AuthenticationError):
            preview_owner_change(workspace, request)
    assert _dump(workspace) == before


def test_unresolved_effects_keep_original_identity_and_do_not_authorize_resend(owner_workspace):
    workspace, request, principal, _, _ = owner_workspace
    for state in ("READY", "DISPATCHING", "COMMIT_UNKNOWN", "CONFIRMED", "REJECTED"):
        _effect(workspace, f"effect:{state}", state)
    before = _dump(workspace)
    with principal():
        preview = preview_owner_change(workspace, request)
    assert _dump(workspace) == before
    effects = {item.state: item for item in preview.unresolved_effects}
    assert set(effects) == {"READY", "DISPATCHING", "COMMIT_UNKNOWN"}
    assert effects["READY"].required_review == "REVIEW_EXISTING_INTENT"
    for state in ("DISPATCHING", "COMMIT_UNKNOWN"):
        assert effects[state].required_review == "RECONCILE_ORIGINAL_EFFECT_NO_RESEND"
    assert all(not item.dispatch_authorized and item.association == "WORKSPACE_SCOPE_UNCONFIRMED"
               for item in effects.values())


def test_effect_page_is_bounded_and_incomplete_is_unknown(owner_workspace):
    workspace, request, principal, _, _ = owner_workspace
    for number in range(101):
        _effect(workspace, f"effect:{number:03d}", "COMMIT_UNKNOWN")
    before = _dump(workspace)
    with principal():
        preview = preview_owner_change(workspace, request)
    assert _dump(workspace) == before
    assert len(preview.unresolved_effects) == 100
    assert preview.effect_coverage.status == "UNKNOWN"
    assert preview.effect_coverage.next_cursor == "effect:099"


def test_uninspected_pending_events_are_unknown_not_no_work(owner_workspace, monkeypatch):
    workspace, request, principal, _, _ = owner_workspace
    page = workspace.changes.page

    def limited(*, limit):
        assert limit == 100
        return {**page(limit=1), "next_cursor": 1}

    monkeypatch.setattr(workspace.changes, "page", limited)
    with principal():
        preview = preview_owner_change(workspace, request)
    assert preview.pending_event_coverage.status == "UNKNOWN"
    assert preview.pending_event_coverage.next_cursor == 1
    assert preview.pending_event_coverage.inspected_count == 1


def test_incomplete_dependency_proof_stays_unknown(owner_workspace, monkeypatch):
    from orgrebase.impact import ImpactEngine

    workspace, request, principal, _, _ = owner_workspace
    closure = ImpactEngine.dependency_closure

    def incomplete(engine, refs):
        return {**closure(engine, refs), "complete": False}

    monkeypatch.setattr(ImpactEngine, "dependency_closure", incomplete)
    with principal():
        preview = preview_owner_change(workspace, request)
    assert preview.dependency_coverage.status == "UNKNOWN"
    assert preview.pending_event_coverage.status == "UNKNOWN"


def test_http_options_and_owner_preview_share_frozen_bindings_without_writes(owner_workspace):
    workspace, request, principal, _, _ = owner_workspace
    app = FastAPI()
    app.include_router(change_router(lambda: workspace))
    before = _dump(workspace)
    with TestClient(app) as client:
        with principal():
            options = client.get("/api/workspace/change-options")
            assert options.status_code == 200
            baseline = options.json()
            payload = {
                **request.model_dump(mode="json"),
                "expected_binding_digest": baseline["enterprise_binding_digest"],
                "expected_snapshot_digest": baseline["snapshot_digest"],
            }
            result = client.post("/api/workspace/organization/owner-change-preview", json=payload)
            assert result.status_code == 200, result.text
            preview = result.json()
            assert preview["binding_digest"] == baseline["enterprise_binding_digest"]
            assert preview["snapshot_digest"] == baseline["snapshot_digest"]
            assert preview["activation_allowed"] is False
            assert preview["database_writes"] == 0
            stale = client.post("/api/workspace/organization/owner-change-preview", json={
                **payload, "expected_binding_digest": "sha256:" + "0" * 64,
            })
            assert stale.status_code == 409
            assert stale.json()["detail"] == {
                "code": "OWNER_CHANGE_BINDING_CHANGED", "message": "OWNER_CHANGE_BINDING_CHANGED",
            }
        with principal(role="reader"):
            denied = client.post("/api/workspace/organization/owner-change-preview", json=payload)
            assert denied.status_code == 403
            assert denied.json()["detail"]["code"] == "AUTH_ACTION_DENIED"
        anonymous = client.post("/api/workspace/organization/owner-change-preview", json=payload)
        assert anonymous.status_code == 401
    assert _dump(workspace) == before
