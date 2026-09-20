from __future__ import annotations

import time
from contextlib import closing, contextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from enterprise_pack_factory import make_enterprise_pack
from test_dataverse_qualification import ORGANIZATION_ID, MetadataProtocol
from test_dataverse_target import QUOTE_ID, DataverseProtocolServer

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.commit_gateway import EffectError, EffectRequest, ResolutionState
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.domain import ObjectState
from orgrebase.workspace.effect_worker import process_effect_commands, run_effect_worker
from orgrebase.workspace.effects import (
    EffectActionInput,
    EffectApprovalInput,
    EffectProposalInput,
    EffectWorkerConfig,
    approve_effect,
    effect_detail,
    list_effect_proposals,
    propose_effect,
    record_target_observation,
    reject_effect,
    request_effect_action,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def workflow(tmp_path):
    with closing(WorkspaceService(store_path=tmp_path / "workspace.sqlite", review_duration_seconds=0,
                          runtime_configuration=load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path)))) as workspace:
        workspace.form_quote()
        server = DataverseProtocolServer()
        config = EffectWorkerConfig(target=DataverseDraftTargetSettings(
            tenant_id=workspace.profile.organization_id, instance_url="https://sales.example", quote_id=QUOTE_ID),
            owner_id="user:owner", organization_id=ORGANIZATION_ID,
            target_token_variable="TARGET_ACCESS", access_token_variable="WORKER_ACCESS")
        roles = {"owner": "approve", "executor": "execute", "worker": "execute"}

        @contextmanager
        def identity(subject, role):
            value = Principal("https://identity.example", subject, workspace.profile.organization_id,
                              f"user:{subject}", frozenset({role}), int(time.time()) + 900)
            token = request_principal.set(value)
            try:
                yield
            finally:
                request_principal.reset(token)

        def create_target(check):
            def token():
                check()
                return "test-target-access"
            return DataverseDraftTarget(config.target, token, transport=httpx.MockTransport(server))

        def verify(subject, actor, action):
            if actor != f"user:{subject}" or roles.get(subject) != action:
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

        def work():
            with identity("worker", "executor"):
                return process_effect_commands(workspace, config, create_target=create_target,
                                               verify_member=verify, worker_id="worker:1")

        with identity("worker", "executor"), create_target(lambda: None) as target:
            record_target_observation(workspace, config, target, "observation-1")
        request = EffectProposalInput(proposal_id="proposal-1", observation_id="observation-1",
                                      changes={"description": "Owner-approved revised draft"}, reason="Clarify delivered service")
        with identity("operator", "operator"):
            proposal = propose_effect(workspace, config, request)
        yield workspace, server, config, roles, identity, work, proposal


def approve_and_queue(workflow):
    workspace, _, config, _, identity, _, proposal = workflow
    with identity("owner", "approver"):
        approved = approve_effect(workspace, config, "proposal-1", EffectApprovalInput(proposal_digest=proposal["proposal_digest"]))
    with identity("executor", "executor"):
        queued = request_effect_action(workspace, config, "proposal-1", EffectActionInput(
            action="EXECUTE", request_digest=approved["effect"]["request_digest"]))
    return queued


def test_malformed_target_text_is_rejected_before_persisting_observation(workflow):
    workspace, server, config, _, identity, _, _ = workflow
    server.quote["description"] = {"private": ["MUST_NOT_BE_PERSISTED"]}
    before = workspace.store.event_records()
    with identity("worker", "executor"), DataverseDraftTarget(
        config.target, lambda: "test-target-access", transport=httpx.MockTransport(server),
    ) as target, pytest.raises(EffectError, match=r"^TARGET_QUOTE_FIELDS_INVALID$"):
        record_target_observation(workspace, config, target, "malformed-observation")
    assert not workspace.store.artifact_exists("external-observation:malformed-observation@r1")
    assert workspace.store.event_records() == before
    assert server.writes == 0


@pytest.mark.parametrize("readback", ["changed", "unavailable", "malformed"])
def test_historical_receipt_does_not_substitute_for_post_commit_observation(workflow, readback):
    workspace, server, config, _, identity, work, _ = workflow
    queued = approve_and_queue(workflow)
    work()
    effect = EffectRequest.model_validate(queued["effect"]["request"])
    confirmed = workspace.store.get_effect(effect.effect_id)
    assert confirmed["state"] == "CONFIRMED" and server.writes == 1
    # Simulate an independent administrator editing the target after our commit.
    server.quote.update(description="Administrator's later correction", **{"@odata.etag": 'W/"44"'})
    if readback == "malformed":
        server.quote["description"] = {"unexpected": "object"}
    requests = []

    def respond(request):
        requests.append(request)
        if readback == "unavailable" and request.url.path.endswith(f"quotes({QUOTE_ID})"):
            return httpx.Response(503)
        return server(request)

    with identity("worker", "executor"), DataverseDraftTarget(
        config.target, lambda: "test-target-access", transport=httpx.MockTransport(respond),
    ) as target:
        assert target.query_effect(effect).state == ResolutionState.CONFIRMED
        if readback == "changed":
            observed = record_target_observation(workspace, config, target, "after-commit")
            assert observed["fields"]["description"] == "Administrator's later correction"
            assert observed["fields"]["description"] != effect.payload["changes"]["description"]
            assert observed["version"] == 'W/"44"'
        else:
            code = "TARGET_QUOTE_UNAVAILABLE" if readback == "unavailable" else "TARGET_QUOTE_FIELDS_INVALID"
            with pytest.raises(EffectError, match=f"^{code}$"):
                record_target_observation(workspace, config, target, "after-commit")
            assert not workspace.store.artifact_exists("external-observation:after-commit@r1")
    assert workspace.store.get_effect(effect.effect_id) == confirmed
    assert server.writes == 1 and len(server.receipts) == 1
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("failure", [None, "metadata", "response_loss"])
def test_command_reuses_and_closes_client_across_qualification_and_execution(workflow, monkeypatch, failure):
    from test_dataverse_qualification import ORGANIZATION_ID, MetadataProtocol

    from orgrebase import dataverse_target
    from orgrebase.dataverse_qualification import probe_target

    workspace, server, config, _, identity, _, _ = workflow
    approve_and_queue(workflow)
    server.lose_response = failure == "response_loss"
    metadata = MetadataProtocol()
    clients, contexts, transports, requests, credential_checks = [], [], [], [], []
    client_type = httpx.Client
    create_context = dataverse_target.ssl.create_default_context

    def new_client(**kwargs):
        client = client_type(**kwargs)
        clients.append(client)
        return client

    def new_context(**kwargs):
        context = create_context(**kwargs)
        contexts.append(context)
        return context

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/WhoAmI") or "/EntityDefinitions" in request.url.path:
            if failure == "metadata":
                raise httpx.ReadError("Metadata unavailable", request=request)
            return metadata(request)
        return server(request)

    class Transport(httpx.MockTransport):
        close_count = 0

        def close(self):
            self.close_count += 1

    def create_target(check):
        def token():
            check()
            credential_checks.append(True)
            return "test-target-access"
        transport = Transport(respond)
        transports.append(transport)
        return DataverseDraftTarget(config.target, token, transport=transport)

    monkeypatch.setattr(httpx, "Client", new_client)
    monkeypatch.setattr(dataverse_target.ssl, "create_default_context", new_context)
    with identity("worker", "executor"):
        result = process_effect_commands(
            workspace, config, create_target=create_target, verify_member=lambda *args: None,
            worker_id="worker:connection-test",
            qualify_target=lambda target: probe_target(target, organization_id=ORGANIZATION_ID),
        )["commands"][0]
    assert len(clients) == len(contexts) == len(transports) == 1
    assert clients[0].is_closed and transports[0].close_count == 1
    assert len(credential_checks) == len(requests)
    if failure == "metadata":
        assert result["state"] == "READY" and server.writes == 0 and len(requests) == 1
    elif failure == "response_loss":
        assert result["state"] == "COMMIT_UNKNOWN" and server.writes == 1
    else:
        assert result["state"] == "CONFIRMED" and server.writes == 1
        assert len(requests) >= 13


def test_runtime_upgrade_requires_new_proposal_before_first_dispatch(workflow, monkeypatch):
    workspace, server, config, _, identity, work, _ = workflow
    queued = approve_and_queue(workflow)
    from orgrebase.workspace import effects
    revision = effects.effect_runtime_revision()
    monkeypatch.setattr(effects, "effect_runtime_revision", lambda: {**revision, "receipt_contract": "future.v2"})
    result = work()["commands"][0]
    assert result["error_code"] == "EFFECT_RUNTIME_REPLAN_REQUIRED"
    assert server.writes == 0
    assert workspace.store.get_effect(queued["effect"]["effect_id"])["state"] == "READY"
    with identity("owner", "approver"):
        assert "CANCEL" in effect_detail(workspace, config, "proposal-1")["allowed_actions"]


def test_runtime_upgrade_still_allows_query_of_an_already_dispatched_effect(workflow, monkeypatch):
    _, server, config, _, identity, work, _ = workflow
    approve_and_queue(workflow)
    server.lose_response = True
    assert work()["commands"][0]["state"] == "COMMIT_UNKNOWN"
    from orgrebase.workspace import effects
    revision = effects.effect_runtime_revision()
    monkeypatch.setattr(effects, "effect_runtime_revision", lambda: {**revision, "receipt_contract": "future.v2"})
    with identity("executor", "executor"):
        detail = effect_detail(workflow[0], config, "proposal-1")
        assert "QUERY" in detail["allowed_actions"]
        request_effect_action(workflow[0], config, "proposal-1", EffectActionInput(
            action="QUERY", request_digest=detail["effect"]["request_digest"]))
    assert work()["commands"][0]["state"] == "CONFIRMED"
    assert server.writes == 1


def test_approval_and_execution_are_separate_and_one_ledger_drives_the_worker(workflow):
    workspace, server, config, _, identity, work, proposal = workflow
    original = workspace.current_quote().digest
    with identity("owner", "approver"):
        approved = approve_effect(workspace, config, "proposal-1", EffectApprovalInput(proposal_digest=proposal["proposal_digest"]))
    assert approved["state"] == "READY" and server.writes == 0
    assert work()["commands"] == []
    with identity("executor", "executor"):
        command = EffectActionInput(action="EXECUTE", request_digest=approved["effect"]["request_digest"])
        first = request_effect_action(workspace, config, "proposal-1", command)
        again = request_effect_action(workspace, config, "proposal-1", command)
        assert again["effect"]["command_revision"] == first["effect"]["command_revision"]
    assert work()["commands"][0]["state"] == "CONFIRMED"
    assert work()["commands"] == [] and server.writes == 1
    assert workspace.current_quote().digest == original
    with identity("owner", "approver"):
        detail = effect_detail(workspace, config, "proposal-1")
        assert detail["allowed_actions"] == [] and detail["effect"]["result"]["outcome"] == "CONFIRMED"
        assert list_effect_proposals(workspace, config)["items"][0]["proposal_digest"] == proposal["proposal_digest"]


def test_wrong_owner_or_digest_cannot_approve(workflow):
    workspace, server, config, _, identity, _, proposal = workflow
    with identity("not-owner", "approver"), pytest.raises(EffectError, match="EFFECT_OWNER_REQUIRED"):
        approve_effect(workspace, config, "proposal-1", EffectApprovalInput(proposal_digest=proposal["proposal_digest"]))
    with identity("owner", "approver"), pytest.raises(EffectError, match="EFFECT_PROPOSAL_DIGEST_MISMATCH"):
        approve_effect(workspace, config, "proposal-1", EffectApprovalInput(proposal_digest="sha256:" + "f" * 64))
    assert server.writes == 0 and workspace.store.list_effects()["items"] == []


def test_revoked_owner_or_executor_cannot_dispatch_a_previously_queued_command(workflow):
    workspace, server, _, roles, _, work, _ = workflow
    approve_and_queue(workflow)
    roles.pop("owner")
    report = work()["commands"][0]
    assert report["error_code"] == "AUTH_MEMBERSHIP_DENIED" and report["state"] == "READY"
    assert not report["pending"] and server.writes == 0
    assert all(request.method == "GET" for request in server.requests)
    assert workspace.store.pending_effects() == ()


def test_expired_command_does_not_dispatch_or_silently_refresh_itself(workflow):
    workspace, server, _, _, _, work, _ = workflow
    approve_and_queue(workflow)
    workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(minutes=6)))
    result = work()["commands"][0]
    assert result["error_code"] == "EFFECT_AUTHORITY_EXPIRED" and result["state"] == "READY"
    assert server.writes == 0


def test_response_loss_only_allows_explicit_query_and_never_resends(workflow):
    workspace, server, config, _, identity, work, _ = workflow
    approve_and_queue(workflow)
    server.lose_response = True
    assert work()["commands"][0]["state"] == "COMMIT_UNKNOWN"
    assert server.writes == 1
    assert work()["commands"] == []
    workspace.clock = FrozenClock(timestamp(utc_datetime(workspace.clock.now()) + timedelta(minutes=20)))
    with identity("executor", "executor"):
        detail = effect_detail(workspace, config, "proposal-1")
        assert detail["allowed_actions"] == ["QUERY"]
        request_effect_action(workspace, config, "proposal-1", EffectActionInput(action="QUERY", request_digest=detail["effect"]["request_digest"]))
    report = work()["commands"][0]
    assert report["state"] == "CONFIRMED" and server.writes == 1, str(report)


def test_cancel_is_owner_authorized_and_fences_a_late_original_batch(workflow):
    workspace, server, config, _, identity, work, _ = workflow
    approve_and_queue(workflow)
    server.defer = True
    assert work()["commands"][0]["state"] == "COMMIT_UNKNOWN"
    with identity("executor", "executor"):
        detail = effect_detail(workspace, config, "proposal-1")
        with pytest.raises(AuthenticationError):
            request_effect_action(workspace, config, "proposal-1", EffectActionInput(action="CANCEL", request_digest=detail["effect"]["request_digest"]))
    with identity("owner", "approver"):
        request_effect_action(workspace, config, "proposal-1", EffectActionInput(action="CANCEL", request_digest=detail["effect"]["request_digest"]))
    report = work()["commands"][0]
    assert report["state"] == "REJECTED", str(report)
    assert server.batch(server.deferred).status_code == 409 and server.writes == 0
    assert workspace.store.get_target_barrier(detail["effect"]["target_key"]) is None


def test_changed_canonical_quote_invalidates_first_dispatch(workflow):
    workspace, server, _, _, _, work, _ = workflow
    approve_and_queue(workflow)
    workspace.effective_workflow_run_id = "different-run"
    result = work()["commands"][0]
    assert result["error_code"] == "EFFECT_SOURCE_QUOTE_CHANGED" and server.writes == 0


@pytest.mark.parametrize("boundary", ["propose", "approve", "queue", "dispatch"])
def test_source_invalidation_blocks_new_external_authority(workflow, boundary):
    workspace, server, config, _, identity, work, proposal = workflow
    approved = None
    if boundary in {"queue", "dispatch"}:
        with identity("owner", "approver"):
            approved = approve_effect(workspace, config, "proposal-1", EffectApprovalInput(
                proposal_digest=proposal["proposal_digest"]))
    if boundary == "dispatch":
        with identity("executor", "executor"):
            request_effect_action(workspace, config, "proposal-1", EffectActionInput(
                action="EXECUTE", request_digest=approved["effect"]["request_digest"]))
    original = workspace.current_quote()
    workspace.invalidate_source("launch_date", "source:revoked", "SOURCE_PERMISSION_DENIED")
    assert workspace.current_quote().digest == original.digest
    assert workspace.current_quote().state == ObjectState.REVIEW_REQUIRED
    assert len(workspace.changes.gaps()) == 1
    if boundary == "dispatch":
        result = work()["commands"][0]
        assert result["error_code"] == "EFFECT_SOURCE_QUOTE_NOT_CURRENT"
        assert result["state"] == "READY" and not result["pending"]
    else:
        subject, role = {"propose": ("operator", "operator"), "approve": ("owner", "approver"),
                         "queue": ("executor", "executor")}[boundary]
        with identity(subject, role), pytest.raises(EffectError, match="EFFECT_SOURCE_QUOTE_NOT_CURRENT"):
            if boundary == "propose":
                propose_effect(workspace, config, EffectProposalInput(
                    proposal_id="proposal-invalid", observation_id="observation-1",
                    changes={"description": "Invalidated source"}, reason="Must require readmission"))
            elif boundary == "approve":
                approve_effect(workspace, config, "proposal-1", EffectApprovalInput(
                    proposal_digest=proposal["proposal_digest"]))
            else:
                request_effect_action(workspace, config, "proposal-1", EffectActionInput(
                    action="EXECUTE", request_digest=approved["effect"]["request_digest"]))
        if boundary in {"propose", "approve"}:
            assert workspace.store.list_effects()["items"] == []
        else:
            assert workspace.store.pending_effects() == ()
    with identity("owner", "approver"):
        detail = effect_detail(workspace, config, "proposal-1")
        assert detail["blocked_reason"] == "EFFECT_SOURCE_QUOTE_NOT_CURRENT"
        assert "APPROVE" not in detail["allowed_actions"] and "EXECUTE" not in detail["allowed_actions"]
        if approved:
            cancelled = request_effect_action(workspace, config, "proposal-1", EffectActionInput(
                action="CANCEL", request_digest=approved["effect"]["request_digest"]))
            assert cancelled["state"] == "REJECTED"
    assert server.writes == 0


def test_current_quote_cannot_bypass_an_unresolved_source_gap(workflow):
    workspace, server, _, _, _, work, _ = workflow
    approve_and_queue(workflow)
    workspace.invalidate_source("launch_date", "source:revoked", "SOURCE_PERMISSION_DENIED")
    with workspace.store.transaction() as connection:
        workspace.store.transition_current(connection, workspace.current_quote().id, ObjectState.CURRENT)
    assert workspace.changes.gaps()
    result = work()["commands"][0]
    assert result["error_code"] == "EFFECT_SOURCE_READMISSION_REQUIRED"
    assert result["state"] == "READY" and not result["pending"] and server.writes == 0


@pytest.mark.parametrize("action", ["QUERY", "CANCEL"])
def test_source_invalidation_preserves_unknown_effect_reconciliation(workflow, action):
    workspace, server, config, _, identity, work, _ = workflow
    approve_and_queue(workflow)
    server.lose_response = action == "QUERY"
    server.defer = action == "CANCEL"
    assert work()["commands"][0]["state"] == "COMMIT_UNKNOWN"
    workspace.invalidate_source("launch_date", "source:revoked", "SOURCE_PERMISSION_DENIED")
    with identity("executor" if action == "QUERY" else "owner", "executor" if action == "QUERY" else "approver"):
        detail = effect_detail(workspace, config, "proposal-1")
        assert action in detail["allowed_actions"] and detail["blocked_reason"] is None
        request_effect_action(workspace, config, "proposal-1", EffectActionInput(
            action=action, request_digest=detail["effect"]["request_digest"]))
    result = work()["commands"][0]
    assert result["state"] == ("CONFIRMED" if action == "QUERY" else "REJECTED")
    if action == "CANCEL":
        assert server.batch(server.deferred).status_code == 409
    assert server.writes == (1 if action == "QUERY" else 0)


def test_source_invalidation_between_target_reads_prevents_the_batch(workflow):
    workspace, server, config, _, identity, work, _ = workflow
    approve_and_queue(workflow)

    def respond(request):
        result = server(request)
        if "/orgrebase_effectreceipts(" in request.url.path:
            workspace.invalidate_source("launch_date", "source:revoked", "SOURCE_PERMISSION_DENIED")
        return result

    def create_target(check):
        def token():
            check()
            return "test-target-access"
        return DataverseDraftTarget(config.target, token, transport=httpx.MockTransport(respond))

    with identity("worker", "executor"):
        result = process_effect_commands(workspace, config, create_target=create_target,
            verify_member=lambda *args: None, worker_id="worker:invalidation")["commands"][0]
    assert result["error_code"] == "EFFECT_SOURCE_QUOTE_NOT_CURRENT"
    assert result["state"] == "COMMIT_UNKNOWN" and server.writes == 0
    assert all(request.method == "GET" for request in server.requests)
    with identity("owner", "approver"):
        detail = effect_detail(workspace, config, "proposal-1")
        request_effect_action(workspace, config, "proposal-1", EffectActionInput(
            action="CANCEL", request_digest=detail["effect"]["request_digest"]))
    assert work()["commands"][0]["state"] == "REJECTED" and server.writes == 0


@pytest.mark.parametrize("boundary", ["qualification", "before_batch"])
def test_running_worker_rechecks_same_path_configuration_before_target_io(workflow, monkeypatch, tmp_path, boundary):
    from orgrebase import auth, dataverse_qualification, runtime_config
    from orgrebase.workspace import effect_worker

    workspace, server, config, roles, identity, _, _ = workflow
    approve_and_queue(workflow)
    config_path = tmp_path / "effect-worker.json"
    config_path.write_text(config.model_dump_json())
    settings = SimpleNamespace(mode="production", effect_config=config_path, identity=None,
                               authorize_workspace=lambda subject: None)
    settings.for_workspace = lambda workspace_id: settings
    principal = Principal("https://identity.example", "worker", config.target.tenant_id,
                          "user:worker", frozenset({"executor"}), int(time.time()) + 900)

    class Authenticator:
        def __init__(self, settings):
            pass

        def authenticate(self, header):
            assert header == "Bearer test"
            return principal

        def verify_membership(self, subject, actor, action):
            assert actor == f"user:{subject}" and roles[subject] == action

    replaced = False

    def replace_config():
        nonlocal replaced
        if not replaced:
            config_path.write_text(config.model_copy(update={"owner_id": "user:replacement"}).model_dump_json())
            replaced = True

    metadata = MetadataProtocol()

    def respond(request):
        if request.url.path.endswith("/WhoAmI") or "/EntityDefinitions" in request.url.path:
            return metadata(request)
        result = server(request)
        if boundary == "before_batch" and "/orgrebase_effectreceipts(" in request.url.path:
            replace_config()
        return result

    probe = dataverse_qualification.probe_target

    def qualify(target, *, organization_id):
        result = probe(target, organization_id=organization_id)
        if boundary == "qualification":
            replace_config()
        return result

    monkeypatch.setattr(auth, "JWTAuthenticator", Authenticator)
    monkeypatch.setattr(runtime_config.DeploymentSettings, "from_environment", lambda: settings)
    monkeypatch.setattr(runtime_config, "open_workspace", lambda settings: workspace)
    monkeypatch.setattr(workspace, "close", lambda: None)
    monkeypatch.setattr(dataverse_qualification, "probe_target", qualify)
    monkeypatch.setattr(effect_worker, "DataverseDraftTarget", lambda settings, token:
                        DataverseDraftTarget(settings, token, transport=httpx.MockTransport(respond)))
    monkeypatch.setenv(config.access_token_variable, "test")
    monkeypatch.setenv(config.target_token_variable, "test-target-access")
    result = run_effect_worker(config_path)["commands"][0]
    assert result["error_code"] == "EFFECT_CONFIGURATION_CHANGED"
    assert result["state"] == ("READY" if boundary == "qualification" else "COMMIT_UNKNOWN")
    assert not result["pending"] and server.writes == 0
    assert all(request.method == "GET" for request in server.requests)
    config_path.write_text(config.model_dump_json())
    with identity("owner", "approver"):
        detail = effect_detail(workspace, config, "proposal-1")
        request_effect_action(workspace, config, "proposal-1", EffectActionInput(
            action="CANCEL", request_digest=detail["effect"]["request_digest"]))
    if boundary == "before_batch":
        assert run_effect_worker(config_path)["commands"][0]["state"] == "REJECTED"
    assert server.writes == 0


def test_rejection_is_immutable_and_cannot_be_approved_later(workflow):
    workspace, server, config, _, identity, _, proposal = workflow
    with identity("owner", "approver"):
        request = EffectApprovalInput(proposal_digest=proposal["proposal_digest"])
        first = reject_effect(workspace, config, "proposal-1", request)
        assert reject_effect(workspace, config, "proposal-1", request) == first
        with pytest.raises(EffectError, match="EFFECT_PROPOSAL_REJECTED"):
            approve_effect(workspace, config, "proposal-1", request)
    assert server.writes == 0


def test_owner_can_cancel_queued_but_not_dispatched_operation_atomically(workflow):
    workspace, server, config, _, identity, work, _ = workflow
    queued = approve_and_queue(workflow)
    with identity("owner", "approver"):
        detail = request_effect_action(workspace, config, "proposal-1", EffectActionInput(
            action="CANCEL", request_digest=queued["effect"]["request_digest"]))
    assert detail["state"] == "REJECTED"
    assert detail["effect"]["result"]["not_dispatched"] is True
    assert work()["commands"] == [] and server.writes == 0
