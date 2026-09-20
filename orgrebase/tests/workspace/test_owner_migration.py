from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Barrier

import pytest
from enterprise_pack_factory import make_enterprise_pack
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_effect_operations import approve_and_queue
from test_effect_operations import workflow as workflow

from orgrebase.auth import AuthenticationError, Principal, request_action, request_principal
from orgrebase.clock import FrozenClock
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError
from orgrebase.workspace.approval_authority import DelegationInput, grant_delegation
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_detail, submit_change
from orgrebase.workspace.effects import EffectActionInput, request_effect_action
from orgrebase.workspace.enterprise_binding import (
    activate_binding,
    binding_revision,
    require_change_owner,
    resource_authority,
)
from orgrebase.workspace.models import EnterpriseBinding
from orgrebase.workspace.owner_change import (
    OwnerChangeCommand,
    OwnerChangeProposalInput,
    activate_owner_change,
    confirm_owner_change,
    list_owner_changes,
    owner_change_detail,
    owner_change_options,
    propose_owner_change,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.routes import change_router
from orgrebase.workspace.service import WorkspaceService
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime


@pytest.fixture
def migration_workspace(tmp_path):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    path = tmp_path / "workspace.sqlite"
    with closing(WorkspaceService(store_path=path, runtime_configuration=runtime,
                                 owner_change_policy="mutual-consent-v1")) as workspace:
        workspace.form_quote()
        members = {"operator": "user:operator", "successor": "user:successor", "outsider": "user:outsider"}
        members.update({r.slot_id: r.owner_id for r in workspace.enterprise_binding.resources})
        allowed = set(members)

        def configure(selected):
            selected.identity_issuer = "https://identity.example"

            def verify(subject, actor, action):
                if members.get(subject) != actor:
                    raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

            def scope(subject):
                if subject not in allowed:
                    raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)

            selected.verify_membership = verify
            selected.authorize_workspace_subject = scope
            selected.members_for_action = lambda action: tuple(
                {"subject": subject, "actor_id": actor} for subject, actor in members.items())

        configure(workspace)

        @contextmanager
        def principal(subject, *, role="administrator", tenant=None):
            token = request_principal.set(Principal(workspace.identity_issuer, subject,
                tenant or workspace.profile.organization_id, members.get(subject, "removed"),
                frozenset({role}), int(time.time()) + 900))
            try:
                yield
            finally:
                request_principal.reset(token)

        yield workspace, principal, members, allowed, runtime, path, configure


def proposal_request(workspace, *, proposal_id="handover", successor="successor", actor="user:successor"):
    return OwnerChangeProposalInput(proposal_id=proposal_id, slot_id="product_plan",
        expected_binding_digest=workspace.enterprise_binding.digest,
        expected_snapshot_digest=workspace.current_snapshot().digest,
        proposed_owner={"issuer": workspace.identity_issuer, "subject": successor, "actor_id": actor},
        reason="The current owner hands this resource to the verified successor.")


def confirmed(workspace, principal, *, proposal_id="handover", old="product_plan", successor="successor",
              actor="user:successor"):
    with principal("operator"):
        candidate = propose_owner_change(workspace, proposal_request(workspace, proposal_id=proposal_id,
            successor=successor, actor=actor))
    command = OwnerChangeCommand(proposal_digest=candidate["proposal_digest"])
    with principal(old):
        confirm_owner_change(workspace, proposal_id, command)
    with principal(successor):
        confirm_owner_change(workspace, proposal_id, command)
    return command


def pending(workspace, event_id, slot="product_plan", value="Reviewed successor service plan", revises=None):
    resource = next(r for r in workspace.enterprise_binding.resources if r.slot_id == slot)
    source = workspace.store.get_object(resource.object_id)
    submit_change(workspace, ChangeProposalInput(event_id=event_id, slot_id=slot, value=value,
        base_version=source.version, base_digest=source.digest, source_ref="source:owner-reviewed@r2",
        revises_event_id=revises))


def test_disabled_policy_cannot_write(migration_workspace):
    workspace, principal, *_ = migration_workspace
    workspace.owner_change_policy = "disabled"
    before = tuple(workspace.store.connection.iterdump())
    with principal("operator"), pytest.raises(AuthorizationError, match="OWNER_CHANGE_POLICY_DISABLED"):
        propose_owner_change(workspace, proposal_request(workspace))
    assert tuple(workspace.store.connection.iterdump()) == before


def test_owner_management_reads_are_scoped_paginated_and_do_not_write(migration_workspace):
    workspace, principal, _, allowed, *_ = migration_workspace
    with principal("operator"):
        for name in ("alpha", "beta", "gamma"):
            propose_owner_change(workspace, proposal_request(workspace, proposal_id=name))
    allowed.remove("outsider")
    before = tuple(workspace.store.connection.iterdump())
    with principal("operator", role="operator"):
        options = owner_change_options(workspace)
        assert options["can_propose"]
        assert options["snapshot_digest"] == workspace.current_snapshot().digest
        assert "outsider" not in {member["subject"] for member in options["eligible_owners"]}
    with principal("operator", role="reader"):
        options = owner_change_options(workspace)
        assert not options["can_propose"]
        assert options["eligible_owners"] == []
        first = list_owner_changes(workspace, limit=2)
        second = list_owner_changes(workspace, limit=2, after=first["next_cursor"])
        assert [item["proposal"]["proposal_id"] for item in first["items"] + second["items"]] == [
            "alpha", "beta", "gamma"]
        assert second["next_cursor"] is None
        assert all(item["allowed_actions"] == [] for item in first["items"])
    assert tuple(workspace.store.connection.iterdump()) == before


def test_owner_actions_follow_exact_identity_and_confirmation_receipts(migration_workspace):
    workspace, principal, *_ = migration_workspace
    with principal("operator"):
        proposal = propose_owner_change(workspace, proposal_request(workspace))
    command = OwnerChangeCommand(proposal_digest=proposal["proposal_digest"])
    with principal("outsider"):
        assert owner_change_detail(workspace, "handover")["allowed_actions"] == []
    with principal("product_plan", role="approver"):
        detail = owner_change_detail(workspace, "handover")
        assert detail["allowed_actions"] == ["CONFIRM_AUTHORIZATION"]
        assert detail["confirmation_role"] == "authorization"
        assert confirm_owner_change(workspace, "handover", command)["allowed_actions"] == []
    with principal("successor", role="approver"):
        assert owner_change_detail(workspace, "handover")["allowed_actions"] == ["CONFIRM_ACCEPTANCE"]
        assert confirm_owner_change(workspace, "handover", command)["allowed_actions"] == ["ACTIVATE"]
    with principal("product_plan", role="approver"):
        assert owner_change_detail(workspace, "handover")["allowed_actions"] == ["ACTIVATE"]
        assert activate_owner_change(workspace, "handover", command)["allowed_actions"] == []


def test_revoked_confirmation_blocks_activation_projection_and_disabled_policy_is_readable(migration_workspace):
    workspace, principal, _, allowed, *_ = migration_workspace
    confirmed(workspace, principal)
    allowed.remove("product_plan")
    with principal("successor", role="approver"):
        detail = owner_change_detail(workspace, "handover")
        assert detail["status"] == "REPLAN_REQUIRED"
        assert detail["blocked_reason"] == "AUTH_WORKSPACE_DENIED"
        assert detail["allowed_actions"] == []
    workspace.owner_change_policy = "disabled"
    with principal("operator", role="reader"):
        assert owner_change_options(workspace)["blocked_reason"] == "OWNER_CHANGE_POLICY_DISABLED"
        assert list_owner_changes(workspace)["items"][0]["proposal"]["proposal_id"] == "handover"


def test_owner_management_http_reads_reject_unbounded_pages(migration_workspace):
    workspace, principal, *_ = migration_workspace
    app = FastAPI()
    app.include_router(change_router(lambda: workspace))
    with TestClient(app) as client, principal("operator", role="operator"):
        prefix = "/api/workspace/organization/"
        assert client.get(prefix + "owner-change-options").json()["can_propose"]
        assert client.get(prefix + "owner-changes").json()["items"] == []
        assert client.get(prefix + "owner-changes?limit=101").status_code == 422


def test_new_owner_is_current_across_existing_process_and_restart_without_rewriting_seed(migration_workspace):
    workspace, principal, _, _, runtime, path, configure = migration_workspace
    seed_digest = workspace.profile.digest
    with principal("operator"):
        pending(workspace, "old-pending")
        pending(workspace, "unrelated", "currency", "EUR")
    event = workspace.changes.get("old-pending")
    other_epoch = resource_authority(workspace, "currency")
    with closing(WorkspaceService(store_path=path, runtime_configuration=runtime)) as second:
        configure(second)
        command = confirmed(workspace, principal)
        with principal("successor"):
            result = activate_owner_change(workspace, "handover", command)
            replay = activate_owner_change(workspace, "handover", command)
        assert result["activation"] == replay["activation"]
        assert result["status"] == "APPLIED"
        assert second.enterprise_binding.digest == workspace.enterprise_binding.digest
        assert workspace.profile.digest == seed_digest
        assert resource_authority(workspace, "currency") == other_epoch
        assert workspace._change_status("old-pending") == "EXPIRED"
        assert workspace._change_status("unrelated") == "RECEIVED"
        assert second.changes.get("old-pending").digest == event.digest
        with pytest.raises(FreshnessError, match="CHANGE_OWNER_REPLAN_REQUIRED"):
            require_change_owner(second, event)
    with closing(WorkspaceService(store_path=path, runtime_configuration=runtime)) as reopened:
        assert reopened.enterprise_binding.digest == workspace.enterprise_binding.digest
        assert reopened.owner_change_policy == "disabled"
        assert reopened.changes.get("old-pending").digest == event.digest


def test_old_approval_cannot_apply_and_successor_must_approve_new_event(migration_workspace):
    workspace, principal, *_ = migration_workspace
    with principal("operator"):
        pending(workspace, "approved-before-transfer")
        preview = workspace.preview_change("approved-before-transfer")
    with principal("product_plan"):
        approved = workspace.approve_change("approved-before-transfer", actor_id=workspace.changes.get(
            "approved-before-transfer").owner_id, preview_digest=preview.preview.digest)
    old_approval = workspace._approval_record("approved-before-transfer")
    command = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", command)
    with principal("operator"), pytest.raises((FreshnessError, RuntimeError), match=r"OWNER|EXPIRED|STALE"):
        workspace.apply_approved_change("approved-before-transfer", approval_digest=approved["approval_digest"])
    assert workspace._approval_record("approved-before-transfer") == old_approval
    with principal("operator", role="reader"):
        workspace.state()
        assert change_detail(workspace, "approved-before-transfer")["status"] == "EXPIRED"
    with principal("operator"):
        pending(workspace, "successor-event", revises="approved-before-transfer")
        preview = workspace.preview_change("successor-event")
    assert workspace.changes.get("successor-event").owner_id == "user:successor"
    with principal("successor"):
        approved = workspace.approve_change("successor-event", actor_id="user:successor",
                                            preview_digest=preview.preview.digest)
    with principal("operator"):
        workspace.apply_approved_change("successor-event", approval_digest=approved["approval_digest"])
    assert workspace.current_quote().payload["product_plan"] == "Reviewed successor service plan"
    assert workspace._change_status("successor-event") == "APPLIED"


def test_returning_owner_does_not_revive_old_authority(migration_workspace):
    workspace, principal, members, *_ = migration_workspace
    with principal("operator"):
        pending(workspace, "initial-event")
    first = resource_authority(workspace, "product_plan")
    command = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", command)
    command = confirmed(workspace, principal, proposal_id="return", old="successor",
                        successor="product_plan", actor=members["product_plan"])
    with principal("product_plan"):
        activate_owner_change(workspace, "return", command)
    assert resource_authority(workspace, "product_plan") != first
    assert workspace._change_status("initial-event") == "EXPIRED"


def test_competing_proposals_cannot_overwrite_winner(migration_workspace):
    workspace, principal, *_ = migration_workspace
    first = confirmed(workspace, principal, proposal_id="first")
    second = confirmed(workspace, principal, proposal_id="second")
    with principal("successor"):
        activate_owner_change(workspace, "first", first)
        revision = binding_revision(workspace)
        with pytest.raises(FreshnessError, match="OWNER_CHANGE_BINDING_CHANGED"):
            activate_owner_change(workspace, "second", second)
    assert binding_revision(workspace) == revision


def test_member_revocation_is_rechecked_before_activation(migration_workspace):
    workspace, principal, _, allowed, *_ = migration_workspace
    command = confirmed(workspace, principal)
    before = workspace.enterprise_binding.digest
    allowed.remove("product_plan")
    with principal("successor"), pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_DENIED"):
        activate_owner_change(workspace, "handover", command)
    assert workspace.enterprise_binding.digest == before


def test_admin_role_cannot_replace_missing_owner_confirmation(migration_workspace):
    workspace, principal, *_ = migration_workspace
    with principal("operator"):
        result = propose_owner_change(workspace, proposal_request(workspace))
    command = OwnerChangeCommand(proposal_digest=result["proposal_digest"])
    with principal("outsider"), pytest.raises(AuthorizationError, match="OWNER_CHANGE_PARTICIPANT_REQUIRED"):
        confirm_owner_change(workspace, "handover", command)
    with principal("successor"):
        confirm_owner_change(workspace, "handover", command)
        with pytest.raises(AuthorizationError, match="OWNER_CHANGE_TWO_CONFIRMATIONS_REQUIRED"):
            activate_owner_change(workspace, "handover", command)


def test_one_subject_cannot_confirm_both_roles(migration_workspace):
    workspace, principal, *_ = migration_workspace
    workspace.verify_membership = lambda subject, actor, action: None
    with principal("operator"):
        result = propose_owner_change(workspace, proposal_request(workspace, successor="product_plan"))
    command = OwnerChangeCommand(proposal_digest=result["proposal_digest"])
    with principal("product_plan"), pytest.raises(AuthorizationError, match="INDEPENDENT_IDENTITIES_REQUIRED"):
        confirm_owner_change(workspace, "handover", command)


def test_http_permissions_and_explicit_policy(migration_workspace):
    workspace, principal, *_ = migration_workspace
    app = FastAPI()
    app.include_router(change_router(lambda: workspace))
    prefix = "/api/workspace/organization/owner-changes"
    with TestClient(app) as client:
        with principal("operator", role="operator"):
            response = client.post(prefix, json=proposal_request(workspace).model_dump(mode="json"))
            assert response.status_code == 200, response.text
            command = {"proposal_digest": response.json()["proposal_digest"]}
            denied = client.post(prefix + "/handover/confirm", json=command)
            assert denied.status_code == 403
        for subject in ("product_plan", "successor"):
            with principal(subject, role="approver"):
                response = client.post(prefix + "/handover/confirm", json=command)
                assert response.status_code == 200, response.text
        with principal("successor", role="approver"):
            response = client.post(prefix + "/handover/activate", json=command)
            assert response.status_code == 200, response.text
        with principal("operator", role="reader"):
            assert client.get(prefix + "/handover").json()["status"] == "APPLIED"
    assert request_action("POST", prefix + "/handover/confirm") == "approve"
    assert request_action("POST", prefix + "/handover/activate") == "approve"


def test_history_remains_readable_when_policy_is_disabled(migration_workspace):
    workspace, principal, *_ = migration_workspace
    command = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", command)
    workspace.owner_change_policy = "disabled"
    with principal("operator", role="reader"):
        assert owner_change_detail(workspace, "handover")["status"] == "APPLIED"


@pytest.mark.parametrize("change", ["expired", "disabled"])
def test_expired_or_disabled_policy_cannot_activate_or_write(migration_workspace, change):
    workspace, principal, *_ = migration_workspace
    command = confirmed(workspace, principal)
    if change == "expired":
        workspace.clock = FrozenClock("2099-01-01T00:00:00Z")
    else:
        workspace.owner_change_policy = "disabled"
    before = tuple(workspace.store.connection.iterdump())
    with principal("successor"), pytest.raises((AuthorizationError, FreshnessError), match=r"EXPIRED|DISABLED"):
        activate_owner_change(workspace, "handover", command)
    assert tuple(workspace.store.connection.iterdump()) == before


def test_old_owner_cannot_grant_new_delegation_for_migrated_pending_event(migration_workspace):
    workspace, principal, members, *_ = migration_workspace
    with principal("operator"):
        pending(workspace, "old-event")
    event = workspace.changes.get("old-event")
    command = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", command)
    with principal("product_plan"), pytest.raises(FreshnessError, match="CHANGE_OWNER_REPLAN_REQUIRED"):
        grant_delegation(workspace, "old-event", DelegationInput(event_digest=event.digest,
            actor_id=members["outsider"], subject="outsider", reason="Review the old pending event.", valid_seconds=600))


@pytest.mark.parametrize("unknown", [False, True])
def test_unsent_effect_requires_review_but_unknown_keeps_original_identity_and_query(workflow, unknown):
    workspace, server, config, _, identity, work, _ = workflow
    queued = approve_and_queue(workflow)
    effect_id = queued["effect"]["effect_id"]
    if unknown:
        server.lose_response = True
        work()
        assert workspace.store.get_effect(effect_id)["state"] == "COMMIT_UNKNOWN"
        assert server.writes == 1
    original = workspace.store.get_effect(effect_id)
    current = workspace.enterprise_binding
    resource = current.resources[0]
    proposed = EnterpriseBinding.model_validate({**current.model_dump(mode="json", exclude={"digest"}),
        "resources": [item.model_copy(update={"owner_id": "user:successor"}).model_dump(mode="json")
                      if item.slot_id == resource.slot_id else item.model_dump(mode="json") for item in current.resources]})
    with workspace.store.transaction() as connection:
        activate_binding(workspace, connection, binding=proposed, expected_revision=binding_revision(workspace),
                         slot_id=resource.slot_id, migration_digest=sha256_digest({"controlled_test": "owner-change"}))
    assert workspace.store.get_effect(effect_id) == original
    assert config.owner_id == "user:owner"
    if unknown:
        server.lose_response = False
        with identity("executor", "executor"):
            request_effect_action(workspace, config, "proposal-1", EffectActionInput(
                action="QUERY", request_digest=original["request_digest"]))
        work()
        assert workspace.store.get_effect(effect_id)["state"] == "CONFIRMED"
        assert server.writes == 1
    else:
        result = work()["commands"][0]
        assert result["error_code"] == "EFFECT_OWNER_BINDING_REPLAN_REQUIRED"
        assert server.writes == 0


def test_postgres_two_connections_cas_one_current_binding(migration_workspace, postgres_runtime):
    _, principal, _, _, runtime, _, configure = migration_workspace
    database = postgres_runtime(tenant_id=runtime.profile.organization_id)
    options = {"store_path": database["runtime_dsn"], "store_tenant_id": runtime.profile.organization_id,
               "store_migrate": False, "runtime_configuration": runtime,
               "owner_change_policy": "mutual-consent-v1"}
    with closing(WorkspaceService(**options)) as first, closing(WorkspaceService(**options)) as second:
        configure(first)
        configure(second)
        first.form_quote()
        commands = [confirmed(first, principal, proposal_id=name) for name in ("first", "second")]
        barrier = Barrier(2)

        def activate(workspace, proposal_id, command):
            with principal("successor"):
                barrier.wait(timeout=20)
                try:
                    return activate_owner_change(workspace, proposal_id, command)["status"]
                except FreshnessError as error:
                    return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(activate, workspace, proposal_id, command) for workspace, proposal_id, command
                       in zip((first, second), ("first", "second"), commands, strict=True)]
            results = [future.result(timeout=40) for future in futures]
        assert sorted(results) == ["APPLIED", "OWNER_CHANGE_BINDING_CHANGED"]
        assert first.enterprise_binding.digest == second.enterprise_binding.digest
        assert len(first.store.event_page(event_types=("OWNER_MIGRATION_ACTIVATED",), limit=10)["items"]) == 1


def test_group_review_stays_readable_after_one_source_owner_changes(migration_workspace):
    from orgrebase.workspace.source_readmission import approve_group, group_detail
    from tests.workspace.test_source_readmission_groups import command, prepare

    workspace, principal, members, *_ = migration_workspace
    with principal("operator"):
        detail = prepare(workspace, slots=("product_plan", "currency"))
    for row in detail["owners"]:
        subject = next(subject for subject, actor in members.items() if actor == row["owner_id"])
        with principal(subject):
            approve_group(workspace, "recover-1", command(detail, row["owner_id"]))
    transfer = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", transfer)
    with principal("product_plan"):
        shown = group_detail(workspace, "recover-1")
        assert shown["state"] == "EXPIRED"
        assert shown["blocked_reason"] == "CHANGE_OWNER_REPLAN_REQUIRED"
        assert shown["allowed_actions"] == []
        assert all("APPROVE" not in row["allowed_actions"] for row in shown["owners"])
        workspace.state()


def test_reader_can_inspect_persisted_proposal_after_successor_scope_revocation(migration_workspace):
    workspace, principal, _, allowed, *_ = migration_workspace
    command = confirmed(workspace, principal)
    allowed.remove("successor")
    with principal("operator", role="reader"):
        detail = owner_change_detail(workspace, "handover")
        assert detail["status"] == "REPLAN_REQUIRED"
        assert detail["blocked_reason"] == "AUTH_WORKSPACE_DENIED"
        assert detail["proposal_digest"] == command.proposal_digest
        assert detail["authorization"] is not None and detail["acceptance"] is not None
    with principal("product_plan"), pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_DENIED"):
        activate_owner_change(workspace, "handover", command)
    with principal("successor"), pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_DENIED"):
        confirm_owner_change(workspace, "handover", command)
