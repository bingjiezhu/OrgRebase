from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from threading import Event

import httpx2 as httpx
import psycopg
import pytest
from enterprise_pack_factory import make_enterprise_pack
from psycopg import sql
from test_dataverse_target import QUOTE_ID, DataverseProtocolServer

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.clock import FrozenClock
from orgrebase.commit_gateway import CommitGateway
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.store_operations import backup_postgres, database_dsn, qualify_postgres, restore_postgres
from orgrebase.workspace.effect_worker import process_effect_commands
from orgrebase.workspace.effects import (
    EffectActionInput,
    EffectApprovalInput,
    EffectProposalInput,
    EffectWorkerConfig,
    approve_effect,
    propose_effect,
    record_target_observation,
    request_effect_action,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def pg_effect_workflow(postgres_runtime, tmp_path):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    database = postgres_runtime(tenant_id=runtime.profile.organization_id)
    clock = FrozenClock("2026-09-09T00:00:00Z")
    server = DataverseProtocolServer()
    config = EffectWorkerConfig(
        target=DataverseDraftTargetSettings(
            tenant_id=runtime.profile.organization_id, instance_url="https://sales.example", quote_id=QUOTE_ID
        ),
        owner_id="user:owner",
        target_token_variable="TARGET_ACCESS",
        access_token_variable="WORKER_ACCESS",
    )
    with ExitStack() as stack:

        def open_workspace(workspace_id="default"):
            workspace = WorkspaceService(
                store_path=database["runtime_dsn"],
                store_tenant_id=runtime.profile.organization_id,
                store_migrate=False,
                workspace_id=workspace_id,
                clock=clock,
                review_duration_seconds=0,
                runtime_configuration=runtime,
            )
            stack.callback(workspace.close)
            return workspace

        first = open_workspace()
        first.form_quote()
        second = open_workspace()

        @contextmanager
        def identity(subject, role):
            principal = Principal(
                "https://identity.example",
                subject,
                runtime.profile.organization_id,
                f"user:{subject}",
                frozenset({role}),
                int(time.time()) + 900,
            )
            token = request_principal.set(principal)
            try:
                yield
            finally:
                request_principal.reset(token)

        def verify(subject, actor, action):
            if (
                actor != f"user:{subject}"
                or {"owner": "approve", "executor": "execute"}.get(subject) != action
            ):
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

        def target_factory(handler):
            def factory(check):
                def token():
                    check()
                    return "test-target-access"

                return DataverseDraftTarget(config.target, token, transport=httpx.MockTransport(handler))

            return factory

        def prepare():
            with identity("worker", "executor"):
                record_target_observation(first, config, target_factory(server)(lambda: None), "observation")
            with identity("operator", "operator"):
                proposal = propose_effect(
                    first,
                    config,
                    EffectProposalInput(
                        proposal_id="proposal",
                        observation_id="observation",
                        changes={"description": "Approved update"},
                        reason="Clarify scope",
                    ),
                )
            with identity("owner", "approver"):
                approved = approve_effect(
                    first,
                    config,
                    "proposal",
                    EffectApprovalInput(proposal_digest=proposal["proposal_digest"]),
                )
            with identity("executor", "executor"):
                queued = request_effect_action(
                    first,
                    config,
                    "proposal",
                    EffectActionInput(action="EXECUTE", request_digest=approved["effect"]["request_digest"]),
                )
            return queued["effect"]

        def run(workspace, worker, handler=server, membership=verify):
            with identity("worker", "executor"):
                return process_effect_commands(
                    workspace,
                    config,
                    create_target=target_factory(handler),
                    verify_member=membership,
                    worker_id=worker,
                )

        yield dict(
            first=first,
            second=second,
            server=server,
            config=config,
            database=database,
            runtime=runtime,
            identity=identity,
            prepare=prepare,
            run=run,
            open_workspace=open_workspace,
            clock=clock,
        )


def test_real_postgres_two_workers_share_one_command_and_effect_fence(pg_effect_workflow):
    data = pg_effect_workflow
    effect = data["prepare"]()
    entered, release = Event(), Event()

    def held_target(request):
        if request.method == "POST" and request.url.path.endswith("/$batch"):
            entered.set()
            assert release.wait(5)
        return data["server"](request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(data["run"], data["first"], "worker:first", held_target)
        try:
            assert entered.wait(5)
            concurrent = data["run"](data["second"], "worker:second")["commands"][0]
            assert concurrent["error_code"] == "EFFECT_IN_PROGRESS" and concurrent["pending"] is True
            record = data["second"].store.get_effect(effect["effect_id"])
            assert record["requested_action"] == "EXECUTE" and record["lease_owner"] == "worker:first"
        finally:
            release.set()
        assert active.result(timeout=5)["commands"][0]["state"] == "CONFIRMED"
    assert data["server"].writes == 1 and data["second"].store.pending_effects() == ()
    assert data["second"].store.verify_event_chain()["status"] == "PASS"


def test_real_postgres_rejected_competitor_cannot_clear_an_active_command(pg_effect_workflow):
    data = pg_effect_workflow
    effect = data["prepare"]()
    entered, release = Event(), Event()

    def held_target(request):
        if request.method == "POST" and request.url.path.endswith("/$batch"):
            entered.set()
            assert release.wait(5)
        return data["server"](request)

    def revoked(*args):
        raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(data["run"], data["first"], "worker:first", held_target)
        try:
            assert entered.wait(5)
            rejected = data["run"](data["second"], "worker:second", data["server"], revoked)["commands"][0]
            assert rejected["error_code"] == "AUTH_MEMBERSHIP_DENIED"
            record = data["second"].store.get_effect(effect["effect_id"])
            assert record["requested_action"] == "EXECUTE", (
                "A worker without the active lease cleared another worker's command"
            )
        finally:
            release.set()
            active.result(timeout=5)


def test_real_postgres_interrupted_finalization_reconciles_without_resend(pg_effect_workflow, monkeypatch):
    data = pg_effect_workflow
    effect = data["prepare"]()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("FAULT_AFTER_TARGET_COMMIT")

    # Suppress cleanup to model an abruptly lost process after the target commit.
    with monkeypatch.context() as patch:
        patch.setattr(CommitGateway, "_resolve", interrupt)
        patch.setattr(CommitGateway, "_unknown", lambda *args, **kwargs: None)
        with pytest.raises(KeyboardInterrupt, match="FAULT_AFTER_TARGET_COMMIT"):
            data["run"](data["first"], "worker:lost")
    assert data["server"].writes == 1
    row = data["second"].store.get_effect(effect["effect_id"])
    assert row["state"] == "DISPATCHING" and row["requested_action"] == "EXECUTE"
    data["second"].clock = FrozenClock("2026-09-09T00:02:00Z")
    recovered = data["run"](data["second"], "worker:recovered")["commands"][0]
    assert recovered["state"] == "CONFIRMED" and data["server"].writes == 1
    assert data["second"].store.get_effect(effect["effect_id"])["fence"] == 2


def test_real_postgres_restored_missing_intent_never_claims_external_recovery(pg_effect_workflow, tmp_path):
    data = pg_effect_workflow
    database = data["database"]
    tenant = data["runtime"].profile.organization_id
    backup = tmp_path / "before-intent"
    backup_postgres(database["migration_dsn"], tenant_id=tenant, output=backup, clock=data["clock"])
    effect = data["prepare"]()
    assert data["run"](data["first"], "worker:first")["commands"][0]["state"] == "CONFIRMED"
    assert data["server"].writes == 1
    name = "worker_lost_intent_restore"
    try:
        report = restore_postgres(
            database["migration_dsn"],
            tenant_id=tenant,
            backup=backup,
            latest_deletion_ledger=[],
            new_database=name,
            clock=data["clock"],
        )
        assert report["unresolved_effects"] == [] and report["target_barriers"] == []
        assert report["external_effect_inventory"] == "NOT_VERIFIED" and report["writes_released"] is False
        restored = database_dsn(database["migration_dsn"], name)
        with pytest.raises(IntegrityError, match="RECOVERY_QUALIFICATION_REQUIRED"):
            StateStore(restored, tenant_id=tenant, migrate=False)
        with StateStore(restored, tenant_id=tenant, maintenance=True, read_only=True) as store:
            assert store.get_effect(effect["effect_id"]) is None
        assert qualify_postgres(restored, tenant_id=tenant)["writes_released"] is False
        assert data["server"].writes == 1
    finally:
        with psycopg.connect(database["migration_dsn"], autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def test_real_postgres_worker_scope_cannot_read_or_dispatch_another_workspace(pg_effect_workflow):
    data = pg_effect_workflow
    effect = data["prepare"]()
    binding = data["first"].store.get_workspace("default")
    data["first"].store.register_workspace(
        "other-quote",
        profile_digest=binding["profile_digest"],
        pack_digest=binding["pack_digest"],
        quote_object_id=binding["quote_object_id"],
        created_at=data["clock"].now(),
    )
    other = data["open_workspace"]("other-quote")
    other_config = data["config"].model_copy(update={"workspace_id": "other-quote"})

    def unexpected(*args):
        raise AssertionError("Another workspace caused target or identity I/O")

    with data["identity"]("worker", "executor"):
        result = process_effect_commands(
            other, other_config, create_target=unexpected, verify_member=unexpected, worker_id="worker:other"
        )
    assert result == {"workspace_id": "other-quote", "commands": []}
    assert other.store.get_effect(effect["effect_id"]) is None
    assert other.store.connection.execute("SELECT effect_id FROM effect_intents").fetchall() == []
    assert data["first"].store.pending_effects()[0]["effect_id"] == effect["effect_id"]
    assert data["server"].writes == 0


def test_real_postgres_lost_worker_after_command_expiry_can_request_reconciliation(
    pg_effect_workflow, monkeypatch
):
    data = pg_effect_workflow
    effect = data["prepare"]()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("FAULT_AFTER_TARGET_COMMIT")

    with monkeypatch.context() as patch:
        patch.setattr(CommitGateway, "_resolve", interrupt)
        patch.setattr(CommitGateway, "_unknown", lambda *args, **kwargs: None)
        with pytest.raises(KeyboardInterrupt, match="FAULT_AFTER_TARGET_COMMIT"):
            data["run"](data["first"], "worker:lost")
    data["second"].clock = FrozenClock("2026-09-09T00:10:00Z")
    report = data["run"](data["second"], "worker:recovery")["commands"][0]
    assert report["error_code"] == "EFFECT_AUTHORITY_EXPIRED"
    assert report["state"] == "COMMIT_UNKNOWN", (
        "An expired command left DISPATCHING with no pending command or recovery action"
    )
    with data["identity"]("executor", "executor"):
        request_effect_action(
            data["second"],
            data["config"],
            "proposal",
            EffectActionInput(action="QUERY", request_digest=effect["request_digest"]),
        )
    recovered = data["run"](data["second"], "worker:recovery")["commands"][0]
    assert recovered["state"] == "CONFIRMED" and data["server"].writes == 1


def test_real_postgres_expired_worker_identity_cannot_normalize_lost_dispatch(
    pg_effect_workflow, monkeypatch
):
    from dataclasses import replace

    from orgrebase.auth import request_authorization

    data = pg_effect_workflow
    effect = data["prepare"]()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("FAULT_AFTER_TARGET_COMMIT")

    with monkeypatch.context() as patch:
        patch.setattr(CommitGateway, "_resolve", interrupt)
        patch.setattr(CommitGateway, "_unknown", lambda *args, **kwargs: None)
        with pytest.raises(KeyboardInterrupt):
            data["run"](data["first"], "worker:lost")
    data["second"].clock = FrozenClock("2026-09-09T00:10:00Z")
    calls = 0

    def expires_before_cleanup():
        nonlocal calls
        calls += 1
        if calls >= 3:
            request_principal.set(replace(request_principal.get(), expires_at=0))

    token = request_authorization.set(expires_before_cleanup)
    request_count = len(data["server"].requests)
    try:
        with pytest.raises(AuthenticationError, match="AUTH_TOKEN_EXPIRED"):
            data["run"](data["second"], "worker:expired")
    finally:
        request_authorization.reset(token)
    current = data["second"].store.get_effect(effect["effect_id"])
    assert current["state"] == "DISPATCHING" and current["fence"] == 1
    assert current["requested_action"] == "EXECUTE" and current["lease_owner"] == "worker:lost"
    assert len(data["server"].requests) == request_count


def test_real_postgres_expired_lease_takeover_fences_late_original_worker(pg_effect_workflow):
    data = pg_effect_workflow
    effect = data["prepare"]()
    entered, release = Event(), Event()

    def held_target(request):
        if request.method == "POST" and request.url.path.endswith("/$batch"):
            entered.set()
            assert release.wait(5)
        return data["server"](request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(data["run"], data["first"], "worker:late", held_target)
        try:
            assert entered.wait(5)
            data["second"].clock = FrozenClock("2026-09-09T00:10:00Z")
            normalized = data["run"](data["second"], "worker:recovered")["commands"][0]
            assert (
                normalized["state"] == "COMMIT_UNKNOWN"
                and normalized["error_code"] == "EFFECT_AUTHORITY_EXPIRED"
            )
            row = data["second"].store.get_effect(effect["effect_id"])
            assert row["fence"] == 2 and row["requested_action"] is None
            assert data["second"].store.get_target_barrier(effect["target_key"]) == effect["effect_id"]
        finally:
            release.set()
        stale = active.result(timeout=5)["commands"][0]
    assert stale["state"] == "COMMIT_UNKNOWN" and stale["error_code"] == "EFFECT_COMMAND_CHANGED"
    assert data["server"].writes == 1
    with data["identity"]("executor", "executor"):
        request_effect_action(
            data["second"],
            data["config"],
            "proposal",
            EffectActionInput(action="QUERY", request_digest=effect["request_digest"]),
        )
    assert data["run"](data["second"], "worker:recovered")["commands"][0]["state"] == "CONFIRMED"
    assert data["server"].writes == 1
