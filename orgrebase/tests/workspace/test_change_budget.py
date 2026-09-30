from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import func, insert, select, update

from orgrebase.clock import FrozenClock
from orgrebase.database import deployment_budget_reservations, idempotency_records
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.store_operations import migrate_postgres
from orgrebase.workspace.change_budget import (
    DeploymentDispatchBudget,
    dispatch_budget,
    finish_dispatch_quota,
    reconcile_expired_dispatch_quotas,
    reserve_dispatch_quota,
)
from orgrebase.workspace.change_operations import prepare_pending_changes
from orgrebase.workspace.change_proposals import submit_change
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace


def budget(**updates) -> DeploymentDispatchBudget:
    values = {
        "schema_version": "orgrebase.deployment-dispatch-budget.v1",
        "deployment_scope": "deployment:test",
        "currency": "USD",
        "period_seconds": 60,
        "max_reserved_microusd": 100,
        "max_reserved_calls": 10,
        "max_dispatches_per_period": 10,
        "max_queue_reservations_per_period": 10,
        "max_active_attempts": 1,
    }
    values.update(updates)
    return DeploymentDispatchBudget.model_validate(values)


def _reserve(workspace, policy, *, key, amount, calls=1, deadline=None):
    with dispatch_budget(policy), workspace.store.transaction() as connection:
        return reserve_dispatch_quota(
            workspace,
            connection,
            attempt_key=key,
            request_digest=sha256_digest({"key": key}),
            cost_reservation={
                "scope": "ONE_PREVIEW_ATTEMPT",
                "currency": "USD",
                "reserved_microusd": amount,
                "limit_microusd": amount,
                "calls": calls,
            },
            deadline_epoch_ms=deadline or workspace._wall_clock_epoch_ms() + 10_000,
        )


def _finish(workspace, policy, key):
    with dispatch_budget(policy), workspace.store.transaction() as connection:
        finish_dispatch_quota(
            workspace, connection, attempt_key=key,
            request_digest=sha256_digest({"key": key}), state="COMPLETE",
        )


def test_active_attempt_crosses_period_without_releasing_concurrency(workspace, monkeypatch):
    now = [1_800_000_059_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    policy = budget()
    first = _reserve(workspace, policy, key="attempt:a", amount=100, deadline=now[0] + 120_000)
    now[0] += 2_000
    with pytest.raises(IntegrityError, match="DEPLOYMENT_CONCURRENCY_EXHAUSTED"):
        _reserve(workspace, policy, key="attempt:b", amount=100)
    _finish(workspace, policy, "attempt:a")
    second = _reserve(workspace, policy, key="attempt:b", amount=100)
    assert second["period_start_epoch_ms"] > first["period_start_epoch_ms"]
    rows = workspace.store.connection.execute(
        "SELECT state,reserved_microusd FROM deployment_budget_reservations"
    ).fetchall()
    assert sorted(tuple(row) for row in rows) == [("COMPLETE", 100), ("DISPATCHING", 100)]


@pytest.mark.parametrize("legacy_key", [False, True])
def test_restart_reuses_original_period_and_preserves_legacy_identity(tmp_path, legacy_key):
    database = tmp_path / "restart.sqlite"
    policy = budget()
    with StateStore(database, tenant_id="org:budget") as store:
        workspace = _pg_workspace(store, now=1_800_000_059_000)
        original = _reserve(workspace, policy, key="attempt:same", amount=100,
                            deadline=1_800_000_180_000)
        if legacy_key:
            # A v6 row from the previous writer used the raw Preview attempt key.
            with store.transaction() as connection:
                store.execute(connection, update(deployment_budget_reservations).values(
                    attempt_key="attempt:same"
                ))
        original_key = store.connection.execute(
            "SELECT attempt_key FROM deployment_budget_reservations"
        ).fetchone()[0]
    with StateStore(database, tenant_id="org:budget") as store:
        workspace = _pg_workspace(store, now=1_800_000_061_000)
        assert _reserve(workspace, policy, key="attempt:same", amount=100) == original
        _finish(workspace, policy, "attempt:same")
        _finish(workspace, policy, "attempt:same")
        rows = store.connection.execute(
            "SELECT attempt_key,state,period_start_epoch_ms,reserved_microusd "
            "FROM deployment_budget_reservations"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (original_key, "COMPLETE", original["period_start_epoch_ms"], 100)
        ]


def test_dry_run_preserves_expired_reservation_and_database(workspace, monkeypatch):
    submit_change(workspace, command(workspace))
    now = [1_800_000_000_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    policy = budget()
    _reserve(workspace, policy, key="attempt:expired", amount=100, deadline=now[0] + 1_000)
    now[0] += 2_000
    before = tuple(workspace.store.connection.iterdump())
    result = prepare_pending_changes(workspace, max_changes=1, dry_run=True, dispatch_policy=policy)
    assert result["records"][0]["result"] == "ELIGIBLE_NOT_DISPATCHED"
    assert tuple(workspace.store.connection.iterdump()) == before


def test_unknown_original_attempt_is_not_reserved_again_after_period_change(workspace, monkeypatch):
    now = [1_800_000_000_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    policy = budget()
    original = _reserve(workspace, policy, key="attempt:unknown", amount=100, deadline=now[0] + 1_000)
    now[0] += 61_000
    with dispatch_budget(policy):
        assert reconcile_expired_dispatch_quotas(workspace) == 1
    repeated = _reserve(workspace, policy, key="attempt:unknown", amount=100)
    assert repeated == {**original, "state": "RESULT_UNKNOWN"}
    assert workspace.store.connection.execute(
        "SELECT COUNT(*) FROM deployment_budget_reservations"
    ).fetchone()[0] == 1
    with pytest.raises(IntegrityError, match="DEPLOYMENT_TERMINAL_CONFLICT"):
        _finish(workspace, policy, "attempt:unknown")


def test_duplicate_legacy_and_scoped_reservation_fails_closed(workspace):
    policy = budget()
    _reserve(workspace, policy, key="attempt:ambiguous", amount=1)
    with workspace.store.transaction() as connection:
        row = dict(workspace.store.execute(connection, select(deployment_budget_reservations)).fetchone())
        row["attempt_key"] = "attempt:ambiguous"
        workspace.store.execute(connection, insert(deployment_budget_reservations).values(**row))
    before = tuple(workspace.store.connection.iterdump())
    with pytest.raises(IntegrityError, match="DEPLOYMENT_RESERVATION_CONFLICT"):
        _reserve(workspace, policy, key="attempt:ambiguous", amount=1)
    with pytest.raises(IntegrityError, match="DEPLOYMENT_RESERVATION_CONFLICT"):
        _finish(workspace, policy, "attempt:ambiguous")
    assert tuple(workspace.store.connection.iterdump()) == before


def test_prior_period_identity_cannot_change_request_or_policy(workspace, monkeypatch):
    now = [1_800_000_000_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    policy = budget()
    _reserve(workspace, policy, key="attempt:frozen", amount=1, deadline=now[0] + 120_000)
    now[0] += 61_000
    before = tuple(workspace.store.connection.iterdump())
    with pytest.raises(IntegrityError, match="DEPLOYMENT_RESERVATION_CONFLICT"):
        _reserve(workspace, budget(max_reserved_microusd=200), key="attempt:frozen", amount=1)
    with (
        dispatch_budget(policy),
        pytest.raises(IntegrityError, match="DEPLOYMENT_RESERVATION_CONFLICT"),
        workspace.store.transaction() as connection,
    ):
        reserve_dispatch_quota(workspace, connection, attempt_key="attempt:frozen",
            request_digest="sha256:" + "0" * 64,
            cost_reservation={"currency": "USD", "reserved_microusd": 1, "calls": 1},
            deadline_epoch_ms=now[0] + 10_000)
    assert tuple(workspace.store.connection.iterdump()) == before


def test_worker_reserves_deployment_quota_in_the_preview_attempt_transaction(workspace):
    submit_change(workspace, command(workspace))

    result = prepare_pending_changes(workspace, max_changes=1, dispatch_policy=budget())

    assert result["records"][0]["result"] == "CANDIDATE_PREPARED"
    with workspace.store.read_connection() as connection:
        ledger = workspace.store.execute(
            connection, select(deployment_budget_reservations)
        ).fetchone()
        attempt = workspace.store.execute(
            connection,
            select(idempotency_records).where(
                idempotency_records.c.key.startswith("workspace-preview-attempt:"),
                idempotency_records.c.request_digest == ledger["request_digest"],
            ),
        ).fetchone()
    assert ledger["tenant_id"] == workspace.profile.organization_id
    assert ledger["deployment_scope"] == "deployment:test"
    assert ledger["workspace_id"] == workspace.store.workspace_id
    assert ledger["request_digest"] == attempt["request_digest"]
    assert ledger["state"] == "COMPLETE"


def test_quota_rejection_rolls_back_both_ledger_and_preview_attempt(workspace, monkeypatch):
    submit_change(workspace, command(workspace))
    monkeypatch.setattr(
        workspace.advisory_factory,
        "cost_reservation",
        lambda **_kwargs: {
            "scope": "ONE_PREVIEW_ATTEMPT",
            "currency": "USD",
            "reserved_microusd": 11,
            "limit_microusd": 11,
            "calls": 1,
        },
    )

    with (
        dispatch_budget(budget(max_reserved_microusd=10)),
        pytest.raises(IntegrityError, match="DEPLOYMENT_COST_EXHAUSTED"),
    ):
        workspace.preview_change("edit-1")

    with workspace.store.read_connection() as connection:
        ledger_count = workspace.store.execute(
            connection, select(func.count()).select_from(deployment_budget_reservations)
        ).fetchone()[0]
        attempt_count = workspace.store.execute(
            connection,
            select(func.count())
            .select_from(idempotency_records)
            .where(idempotency_records.c.key.startswith("workspace-preview-attempt:")),
        ).fetchone()[0]
    assert ledger_count == attempt_count == 0


def test_unknown_cost_stays_reserved_while_expired_concurrency_is_released(workspace, monkeypatch):
    now = [1_800_000_000_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    policy = budget()
    _reserve(workspace, policy, key="attempt:a", amount=60, deadline=now[0] + 1_000)

    with pytest.raises(IntegrityError, match="DEPLOYMENT_CONCURRENCY_EXHAUSTED"):
        _reserve(workspace, policy, key="attempt:b", amount=1)

    now[0] += 2_000
    with dispatch_budget(policy):
        assert reconcile_expired_dispatch_quotas(workspace) == 1
    with pytest.raises(IntegrityError, match="DEPLOYMENT_COST_EXHAUSTED"):
        _reserve(workspace, policy, key="attempt:b", amount=41)
    _reserve(workspace, policy, key="attempt:b", amount=40)

    with workspace.store.read_connection() as connection:
        rows = workspace.store.execute(
            connection,
            select(deployment_budget_reservations).order_by(
                deployment_budget_reservations.c.attempt_key
            ),
        ).fetchall()
    assert {row["reserved_microusd"]: row["state"] for row in rows} == {
        60: "RESULT_UNKNOWN", 40: "DISPATCHING",
    }
    assert sum(row["reserved_microusd"] for row in rows) == 100


def test_terminal_receipt_releases_concurrency_but_not_period_rate(workspace, monkeypatch):
    now = 1_800_000_000_000
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now)
    policy = budget(max_dispatches_per_period=1)
    first = _reserve(workspace, policy, key="attempt:a", amount=1)
    assert first["state"] == "DISPATCHING"
    with dispatch_budget(policy), workspace.store.transaction() as connection:
        finish_dispatch_quota(
            workspace,
            connection,
            attempt_key="attempt:a",
            request_digest=sha256_digest({"key": "attempt:a"}),
            state="COMPLETE",
        )
    with pytest.raises(IntegrityError, match="DEPLOYMENT_RATE_EXHAUSTED"):
        _reserve(workspace, policy, key="attempt:b", amount=1)


def test_completed_attempt_still_occupies_the_period_queue_admission(workspace, monkeypatch):
    now = 1_800_000_000_000
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now)
    policy = budget(
        max_dispatches_per_period=2, max_queue_reservations_per_period=1
    )
    _reserve(workspace, policy, key="attempt:a", amount=1)
    with dispatch_budget(policy), workspace.store.transaction() as connection:
        finish_dispatch_quota(
            workspace,
            connection,
            attempt_key="attempt:a",
            request_digest=sha256_digest({"key": "attempt:a"}),
            state="COMPLETE",
        )
    with pytest.raises(IntegrityError, match="DEPLOYMENT_QUEUE_EXHAUSTED"):
        _reserve(workspace, policy, key="attempt:b", amount=1)


def test_period_switch_retains_unknown_history_without_charging_the_new_period(
    workspace, monkeypatch
):
    now = [1_800_000_000_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    policy = budget()
    _reserve(workspace, policy, key="attempt:a", amount=100, deadline=now[0] + 1_000)
    now[0] += 61_000
    with dispatch_budget(policy):
        assert reconcile_expired_dispatch_quotas(workspace) == 1
    _reserve(workspace, policy, key="attempt:b", amount=100)

    with workspace.store.read_connection() as connection:
        rows = workspace.store.execute(
            connection,
            select(deployment_budget_reservations).order_by(
                deployment_budget_reservations.c.period_start_epoch_ms
            ),
        ).fetchall()
    assert len({row["period_start_epoch_ms"] for row in rows}) == 2
    assert rows[0]["state"] == "RESULT_UNKNOWN"
    assert rows[0]["reserved_microusd"] == rows[1]["reserved_microusd"] == 100


def _pg_workspace(store: StateStore, *, now: int):
    return SimpleNamespace(
        store=store,
        profile=SimpleNamespace(organization_id="org:budget"),
        clock=FrozenClock("2026-09-26T00:00:00Z"),
        _wall_clock_epoch_ms=lambda: now,
    )


def test_postgres_two_workspaces_compete_for_one_deployment_period(postgres_runtime):
    database = postgres_runtime(tenant_id="org:budget")
    arguments = {"tenant_id": "org:budget", "migrate": False}
    with StateStore(database["runtime_dsn"], **arguments) as first_store:
        first_store.register_workspace(
            "quote-b",
            profile_digest="sha256:" + "1" * 64,
            pack_digest=None,
            quote_object_id="quote:b",
            created_at="2026-09-26T00:00:00Z",
        )
        with StateStore(
            database["runtime_dsn"], workspace_id="quote-b", **arguments
        ) as second_store:
            workspaces = (
                _pg_workspace(first_store, now=1_800_000_000_000),
                _pg_workspace(second_store, now=1_800_000_000_000),
            )

            def compete(index: int) -> str:
                try:
                    _reserve(
                        workspaces[index],
                        budget(max_active_attempts=2),
                        key=f"attempt:{index}",
                        amount=60,
                    )
                except IntegrityError as error:
                    return str(error)
                return "RESERVED"

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(compete, range(2)))
            assert sorted(outcomes) == [
                "RESERVED",
                "WORKSPACE_ADVISORY_DEPLOYMENT_COST_EXHAUSTED",
            ]
            rows = first_store.connection.execute(
                "SELECT workspace_id,reserved_microusd FROM deployment_budget_reservations"
            ).fetchall()
            assert len(rows) == 1 and rows[0][1] == 60


@pytest.mark.parametrize("legacy_key", [False, True])
def test_postgres_same_attempt_in_two_workspaces_finishes_independently_after_restart(
    postgres_runtime, legacy_key,
):
    database = postgres_runtime(tenant_id="org:budget")
    arguments = {"tenant_id": "org:budget", "migrate": False}
    policy = budget(max_active_attempts=2)
    with StateStore(database["runtime_dsn"], **arguments) as store:
        store.register_workspace("quote-b", profile_digest="sha256:" + "1" * 64,
                                 pack_digest=None, quote_object_id="quote:b",
                                 created_at="2026-09-26T00:00:00Z")
        first = _pg_workspace(store, now=1_800_000_000_000)
        _reserve(first, policy, key="attempt:same", amount=40, deadline=1_800_000_120_000)
        if legacy_key:
            with store.transaction() as connection:
                store.execute(connection, update(deployment_budget_reservations).values(
                    attempt_key="attempt:same"
                ))
    with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as store:
        second = _pg_workspace(store, now=1_800_000_000_000)
        _reserve(second, policy, key="attempt:same", amount=60, deadline=1_800_000_120_000)
    for workspace_id in ("quote-b", "default"):
        with StateStore(database["runtime_dsn"], workspace_id=workspace_id, **arguments) as store:
            restarted = _pg_workspace(store, now=1_800_000_061_000)
            _finish(restarted, policy, "attempt:same")
            _finish(restarted, policy, "attempt:same")
            rows = store.connection.execute(
                "SELECT workspace_id,state,reserved_microusd FROM deployment_budget_reservations "
                "ORDER BY workspace_id"
            ).fetchall()
    assert [tuple(row) for row in rows] == [("default", "COMPLETE", 40), ("quote-b", "COMPLETE", 60)]


def test_postgres_competing_reservations_straddling_period_share_concurrency_lock(postgres_runtime):
    database = postgres_runtime(tenant_id="org:budget")
    arguments = {"tenant_id": "org:budget", "migrate": False}
    policy = budget()
    barrier = Barrier(2)

    def compete(index):
        with StateStore(database["runtime_dsn"], **arguments) as store:
            workspace = _pg_workspace(store, now=1_800_000_059_000 + index * 2_000)
            barrier.wait(timeout=5)
            try:
                _reserve(workspace, policy, key=f"attempt:{index}", amount=100,
                         deadline=1_800_000_180_000)
            except IntegrityError as error:
                return str(error)
            return "RESERVED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sorted(results) == ["RESERVED", "WORKSPACE_ADVISORY_DEPLOYMENT_CONCURRENCY_EXHAUSTED"]


def test_postgres_budget_rls_rejects_another_tenant(postgres_runtime):
    database = postgres_runtime(tenant_id="org:budget")
    with StateStore(
        database["runtime_dsn"], tenant_id="org:budget", migrate=False
    ) as store:
        workspace = _pg_workspace(store, now=1_800_000_000_000)
        _reserve(workspace, budget(), key="attempt:a", amount=1)
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
            store.transaction() as connection,
        ):
            connection.execute(
                "UPDATE deployment_budget_reservations SET tenant_id='org:other'"
            )


def _stamp_sqlite_v5(database) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE deployment_budget_reservations")
        connection.execute("UPDATE store_metadata SET schema_version=5")
        connection.execute("PRAGMA user_version=5")


def test_sqlite_v5_upgrade_adds_empty_ledger_without_rewriting_existing_rows(tmp_path):
    database = tmp_path / "v5.sqlite"
    with (
        StateStore(database, tenant_id="org:budget") as store,
        store.transaction() as connection,
    ):
        store.save_artifact(connection, "preserved", "application/json", {"value": 1})
    _stamp_sqlite_v5(database)

    with StateStore(database, tenant_id="org:budget") as migrated:
        assert migrated.check_health()["schema_version"] == 6
        assert migrated.load_artifact("preserved").payload == {"value": 1}
        assert migrated.connection.execute(
            "SELECT COUNT(*) FROM deployment_budget_reservations"
        ).fetchone()[0] == 0


def test_sqlite_v5_upgrade_failure_rolls_back_table_and_version(tmp_path, monkeypatch):
    from orgrebase import store_migrations

    database = tmp_path / "v5-rollback.sqlite"
    with StateStore(database, tenant_id="org:budget"):
        pass
    _stamp_sqlite_v5(database)
    original = store_migrations._upgrade_deployment_budget

    def fail_after_ddl(connection):
        original(connection)
        raise RuntimeError("INJECTED_V6_MIGRATION_FAILURE")

    monkeypatch.setattr(store_migrations, "_upgrade_deployment_budget", fail_after_ddl)
    with pytest.raises(RuntimeError, match="INJECTED_V6_MIGRATION_FAILURE"):
        StateStore(database, tenant_id="org:budget")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='deployment_budget_reservations'"
        ).fetchone() is None


def test_postgres_v5_upgrade_adds_rls_ledger_and_preserves_rows(postgres_runtime):
    database = postgres_runtime(tenant_id="org:budget")
    with (
        StateStore(
            database["runtime_dsn"], tenant_id="org:budget", migrate=False
        ) as store,
        store.transaction() as connection,
    ):
        store.save_artifact(connection, "preserved", "application/json", {"value": 1})
    with psycopg.connect(database["migration_dsn"], autocommit=True) as connection:
        connection.execute("DROP TABLE deployment_budget_reservations")
        connection.execute("UPDATE store_metadata SET schema_version=5")

    migrate_postgres(
        database["migration_dsn"],
        tenant_id="org:budget",
        runtime_role=database["role_name"],
    )

    with StateStore(
        database["runtime_dsn"], tenant_id="org:budget", migrate=False
    ) as migrated:
        assert migrated.check_health()["schema_version"] == 6
        assert migrated.load_artifact("preserved").payload == {"value": 1}
        policy = migrated.connection.execute(
            "SELECT polname FROM pg_policy "
            "WHERE polrelid='deployment_budget_reservations'::regclass"
        ).fetchall()
        assert [row[0] for row in policy] == ["deployment_budget_tenant"]
