from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import select, update

from orgrebase.commit_gateway import EffectRequest
from orgrebase.database import (
    effect_intents,
    execute_core,
    postgres_row_factory,
    source_checkpoints,
    store_metadata,
    target_barriers,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.effect_identity import DATAVERSE_DRAFT_ACTION
from orgrebase.store import StateStore
from orgrebase.store_migrations import STATE_STORE_SCHEMA_VERSION
from orgrebase.store_operations import backup_postgres, database_dsn, migrate_postgres, restore_postgres

TENANT = "org:target-migration"
QUOTE = "00000000-0000-0000-0000-000000000001"
NOW = "2026-09-09T00:00:00Z"


def request(effect_id="effect:one", origin="https://CRM.EXAMPLE:443"):
    return EffectRequest(
        effect_id=effect_id,
        tenant_id=TENANT,
        target_key=f"{origin}/api/data/v9.2/quotes({QUOTE})",
        action=DATAVERSE_DRAFT_ACTION,
        expected_version='W/"7"',
        approval_digest="sha256:" + "a" * 64,
        payload={"fields": {"name": "Approved draft"}},
    )


@contextmanager
def raw(database):
    if str(database).startswith("postgresql:"):
        connection = psycopg.connect(str(database), autocommit=True, row_factory=postgres_row_factory)
    else:
        connection = sqlite3.connect(database, isolation_level=None)
        connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def seed_v3(database, rows, *, quarantined=False):
    with StateStore(database, tenant_id=TENANT, maintenance=True) as store:
        store.record_event("BEFORE_MIGRATION", {"preserve": True})
        with store.transaction() as connection:
            for effect, state, occupied in rows:
                old_key = sha256_digest({"tenant": TENANT, "target": effect.target_key})
                store.put_effect(
                    connection,
                    effect_id=effect.effect_id,
                    target_key=old_key,
                    request_digest=effect.digest,
                    request=effect.model_dump(mode="json"),
                    created_at=NOW,
                )
                if occupied:
                    assert store.acquire_target_barrier(
                        connection, target_key=old_key, effect_id=effect.effect_id
                    )
                store.update_effect(
                    connection,
                    effect_id=effect.effect_id,
                    expected_state="READY",
                    state=state,
                    updated_at=NOW,
                )
                execute_core(
                    connection,
                    update(effect_intents)
                    .where(effect_intents.c.effect_id == effect.effect_id)
                    .values(
                        request_json=json.dumps(effect.model_dump(mode="json"), indent=2),
                        fence=7,
                    ),
                )
    with raw(database) as connection:
        execute_core(
            connection, update(store_metadata).values(schema_version=3, recovery_required=int(quarantined))
        )
        if isinstance(connection, sqlite3.Connection):
            connection.execute("PRAGMA user_version=3")


def snapshot(database):
    with raw(database) as connection:
        return {
            table.name: [
                dict(row)
                for row in execute_core(
                    connection, select(table).order_by(*table.primary_key.columns)
                ).fetchall()
            ]
            for table in (effect_intents, target_barriers, store_metadata)
        }


def assert_content_unchanged(before, after):
    assert [{k: v for k, v in row.items() if k != "target_key"} for row in before["effect_intents"]] == [
        {k: v for k, v in row.items() if k != "target_key"} for row in after["effect_intents"]
    ]


def test_sqlite_v3_upgrade_preserves_request_bytes_and_all_terminal_intents(tmp_path):
    database = tmp_path / "state.sqlite3"
    active, ready, terminal = (
        request(),
        request("effect:ready", "https://crm.example"),
        request("effect:done"),
    )
    seed_v3(
        database,
        [(active, "COMMIT_UNKNOWN", True), (ready, "READY", False), (terminal, "CONFIRMED", False)],
        quarantined=True,
    )
    before = snapshot(database)
    with StateStore(database, tenant_id=TENANT, maintenance=True) as store:
        assert store.check_health()["schema_version"] == STATE_STORE_SCHEMA_VERSION
        assert store.get_target_barrier(active.barrier_key) == active.effect_id
        assert store.verify_event_chain()["events"] == 1
        assert store.check_health()["recovery_required"]
        assert {store.get_effect(effect.effect_id)["target_key"] for effect in (active, ready, terminal)} == {
            active.barrier_key
        }
    assert_content_unchanged(before, snapshot(database))
    with pytest.raises(IntegrityError, match="RECOVERY_QUALIFICATION_REQUIRED"):
        StateStore(database, tenant_id=TENANT, migrate=False)


def test_schema_v3_read_requires_explicit_maintenance_read_only(tmp_path):
    database = tmp_path / "state.sqlite3"
    effect = request()
    seed_v3(database, [(effect, "COMMIT_UNKNOWN", True)], quarantined=True)
    original = database.read_bytes()
    for options in ({"read_only": True}, {"maintenance": True}, {}):
        with pytest.raises(ValueError, match="HISTORICAL_READ_REQUIRES"):
            StateStore(database, tenant_id=TENANT, read_schema_version=3, **options)
    with pytest.raises(IntegrityError, match="SCHEMA_VERSION_UNSUPPORTED:3"):
        StateStore(database, tenant_id=TENANT, maintenance=True, read_only=True)
    with StateStore(
        database, tenant_id=TENANT, maintenance=True, read_only=True, read_schema_version=3
    ) as store:
        assert store.check_health()["schema_version"] == 3
        assert store.get_effect(effect.effect_id)["request_digest"] == effect.digest
        with pytest.raises(RuntimeError, match="STATE_STORE_READ_ONLY"), store.transaction():
            pass
    assert database.read_bytes() == original


@pytest.mark.parametrize(
    "defect,code",
    [
        ("collision", "EFFECT_TARGET_COLLISION"),
        ("missing_barrier", "EFFECT_BARRIER_MISSING"),
        ("wrong_barrier", "EFFECT_BARRIER_INVALID"),
        ("active_effect", "MIGRATION_ACTIVE_LEASE"),
        ("active_source", "MIGRATION_ACTIVE_LEASE"),
        ("wrong_tenant", "EFFECT_REQUEST_BINDING_INVALID"),
        ("wrong_effect_id", "EFFECT_REQUEST_BINDING_INVALID"),
        ("wrong_digest", "EFFECT_REQUEST_BINDING_INVALID"),
        ("missing_quarantine", "EFFECT_MIGRATION_REQUIRES_QUARANTINE"),
    ],
)
def test_sqlite_v4_rejects_incomplete_inputs_without_partial_projection(tmp_path, defect, code):
    database = tmp_path / "state.sqlite3"
    effect = request()
    rows = [(effect, "COMMIT_UNKNOWN", defect != "missing_barrier")]
    if defect == "collision":
        rows.append((request("effect:two", "https://crm.example."), "COMMIT_UNKNOWN", True))
    seed_v3(database, rows, quarantined=defect != "missing_quarantine")
    with raw(database) as connection:
        if defect == "wrong_barrier":
            execute_core(connection, update(target_barriers).values(target_key="corrupt-projection"))
        if defect == "active_effect":
            execute_core(
                connection,
                update(effect_intents).values(lease_owner="old-worker", lease_until=time.time() + 60),
            )
        if defect == "active_source":
            execute_core(
                connection,
                source_checkpoints.insert().values(
                    connector_id="source", lease_owner="old-reader", lease_until=time.time() + 60
                ),
            )
        if defect in ("wrong_tenant", "wrong_effect_id"):
            payload = effect.model_dump(mode="json")
            payload["tenant_id" if defect == "wrong_tenant" else "effect_id"] = "wrong"
            execute_core(
                connection,
                update(effect_intents).values(
                    request_json=json.dumps(payload), request_digest=sha256_digest(payload)
                ),
            )
        if defect == "wrong_digest":
            execute_core(connection, update(effect_intents).values(request_digest="sha256:" + "b" * 64))
    before = snapshot(database)
    with pytest.raises(IntegrityError, match=code):
        StateStore(database, tenant_id=TENANT, maintenance=True)
    assert snapshot(database) == before


def test_open_connection_rechecks_schema_before_next_transaction(tmp_path):
    database = tmp_path / "state.sqlite3"
    with StateStore(database, tenant_id=TENANT) as store:
        with raw(database) as connection:
            execute_core(connection, update(store_metadata).values(schema_version=3))
        with pytest.raises(IntegrityError, match="SCHEMA_VERSION_UNSUPPORTED:3"):
            store.record_event("MUST_NOT_COMMIT", {})


def test_real_postgres_v4_remap_blocks_alias_from_another_workspace(postgres_runtime):
    deployment = postgres_runtime(tenant_id=TENANT)
    database = deployment["migration_dsn"]
    with StateStore(database, tenant_id=TENANT) as store:
        store.register_workspace(
            "quote-b",
            profile_digest="sha256:" + "c" * 64,
            pack_digest=None,
            quote_object_id="quote:two",
            created_at=NOW,
        )
    effect = request()
    seed_v3(database, [(effect, "COMMIT_UNKNOWN", True)])
    before = snapshot(database)
    assert migrate_postgres(database, tenant_id=TENANT)["schema_version"] == STATE_STORE_SCHEMA_VERSION
    assert_content_unchanged(before, snapshot(database))
    with StateStore(database, tenant_id=TENANT, maintenance=True) as store:
        assert store.get_target_barrier(effect.barrier_key) == effect.effect_id
    # Only this isolated test releases quarantine, after all old writers have closed.
    with raw(database) as connection:
        execute_core(connection, update(store_metadata).values(recovery_required=0))
    other = request("effect:other", "https://crm.example.")
    with StateStore(
        deployment["runtime_dsn"], tenant_id=TENANT, workspace_id="quote-b", migrate=False
    ) as store:
        assert store.check_health()["runtime_role_safe"]
        with store.transaction() as connection:
            store.put_effect(
                connection,
                effect_id=other.effect_id,
                target_key=other.barrier_key,
                request_digest=other.digest,
                request=other.model_dump(mode="json"),
                created_at=NOW,
            )
            assert not store.acquire_target_barrier(
                connection, target_key=other.barrier_key, effect_id=other.effect_id
            )
        assert store.get_effect(effect.effect_id) is None


def test_real_postgres_v4_collision_keeps_quarantine_and_both_old_barriers(postgres_dsn):
    seed_v3(
        postgres_dsn,
        [
            (request(), "COMMIT_UNKNOWN", True),
            (request("effect:two", "https://crm.example."), "COMMIT_UNKNOWN", True),
        ],
    )
    before = snapshot(postgres_dsn)
    with pytest.raises(IntegrityError, match="EFFECT_TARGET_COLLISION"):
        migrate_postgres(postgres_dsn, tenant_id=TENANT)
    after = snapshot(postgres_dsn)
    assert after["effect_intents"] == before["effect_intents"]
    assert after["target_barriers"] == before["target_barriers"]
    assert after["store_metadata"][0]["schema_version"] == 3
    assert after["store_metadata"][0]["recovery_required"] == 1
    with StateStore(
        postgres_dsn, tenant_id=TENANT, maintenance=True, read_only=True, read_schema_version=3
    ) as old:
        assert old.get_target_barrier(before["target_barriers"][0]["target_key"]) is not None


def test_real_postgres_v3_backup_verified_before_barrier_remap(postgres_dsn, tmp_path):
    effect = request()
    seed_v3(postgres_dsn, [(effect, "COMMIT_UNKNOWN", True)])
    backup = tmp_path / "backup"
    manifest = backup_postgres(postgres_dsn, tenant_id=TENANT, output=backup, read_schema_version=3)
    original_manifest = (backup / "manifest.json").read_bytes()
    original_dump = (backup / "database.dump").read_bytes()
    assert manifest["state_store_schema_version"] == 3
    result = restore_postgres(postgres_dsn, tenant_id=TENANT, backup=backup, latest_deletion_ledger=[])
    try:
        assert result["status"] == "ISOLATED_RESTORE_VERIFIED"
        assert result["restored_schema_version"] == 3
        assert result["database"]["schema_version"] == STATE_STORE_SCHEMA_VERSION
        assert result["database"]["recovery_required"] is True
        assert result["target_barriers"] == [
            {"target_key": effect.barrier_key, "effect_id": effect.effect_id}
        ]
        assert (backup / "manifest.json").read_bytes() == original_manifest
        assert (backup / "database.dump").read_bytes() == original_dump
        restored = database_dsn(postgres_dsn, result["database_name"])
        with StateStore(restored, tenant_id=TENANT, maintenance=True, read_only=True) as store:
            assert store.get_effect(effect.effect_id)["request_digest"] == effect.digest
            assert store.verify_event_chain()["events"] == 1
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(result["database_name"]))
            )


@pytest.mark.parametrize("table", [effect_intents, source_checkpoints], ids=["effect", "source"])
def test_real_postgres_migration_rejects_live_claim_and_keeps_isolation(postgres_dsn, table):
    seed_v3(postgres_dsn, [(request(), "COMMIT_UNKNOWN", True)])
    with raw(postgres_dsn) as connection:
        if table is source_checkpoints:
            execute_core(connection, table.insert().values(connector_id="source"))
        execute_core(
            connection,
            update(table).values(lease_owner="stopped-but-live-claim", lease_until=time.time() + 60),
        )
    before = snapshot(postgres_dsn)
    with pytest.raises(IntegrityError, match="MIGRATION_ACTIVE_LEASE"):
        migrate_postgres(postgres_dsn, tenant_id=TENANT)
    after = snapshot(postgres_dsn)
    assert after["effect_intents"] == before["effect_intents"]
    assert after["target_barriers"] == before["target_barriers"]
    assert after["store_metadata"][0]["schema_version"] == 3
    assert after["store_metadata"][0]["recovery_required"] == 1


def test_real_postgres_open_transaction_rechecks_version_before_commit(postgres_dsn):
    with StateStore(postgres_dsn, tenant_id=TENANT) as store:
        with (
            pytest.raises(IntegrityError, match="SCHEMA_VERSION_UNSUPPORTED:3"),
            store.transaction() as connection,
        ):
            store.append_event(connection, "MUST_ROLL_BACK", {})
            with raw(postgres_dsn) as other:
                execute_core(other, update(store_metadata).values(schema_version=3))
        with raw(postgres_dsn) as other:
            execute_core(other, update(store_metadata).values(schema_version=STATE_STORE_SCHEMA_VERSION))
        assert store.verify_event_chain()["events"] == 0


def test_real_postgres_restore_rejects_old_manifest_before_projection_change(postgres_dsn, tmp_path):
    effect = request()
    seed_v3(postgres_dsn, [(effect, "COMMIT_UNKNOWN", True)])
    backup = tmp_path / "backup"
    manifest = backup_postgres(postgres_dsn, tenant_id=TENANT, output=backup, read_schema_version=3)
    manifest["target_barriers"][0]["target_key"] = "corrupt-manifest-projection"
    (backup / "manifest.json").write_text(json.dumps(manifest))
    name = "rejected_old_snapshot"
    try:
        with pytest.raises(IntegrityError, match="RESTORE_EFFECT_OR_SOURCE_SNAPSHOT_MISMATCH"):
            restore_postgres(
                postgres_dsn, tenant_id=TENANT, backup=backup, latest_deletion_ledger=[], new_database=name
            )
        observed = snapshot(database_dsn(postgres_dsn, name))
        assert observed["store_metadata"][0]["schema_version"] == 3
        assert observed["store_metadata"][0]["recovery_required"] == 1
        assert observed["target_barriers"] == [
            {
                "effect_id": effect.effect_id,
                "target_key": sha256_digest({"tenant": TENANT, "target": effect.target_key}),
            }
        ]
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def test_schema_v3_target_inventory_keeps_original_receipt_binding_and_database_bytes(tmp_path):
    import httpx2 as httpx

    from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
    from orgrebase.recovery_inventory import recovery_inventory
    from tests.test_recovery_inventory import SINCE, UNTIL, ReceiptServer, effect, receipt

    settings = DataverseDraftTargetSettings(
        tenant_id=TENANT, instance_url="https://SALES.EXAMPLE:443", quote_id=QUOTE
    )
    server = ReceiptServer()
    adapter = DataverseDraftTarget(
        settings, lambda: "read-only-test-token", transport=httpx.MockTransport(server)
    )
    old_request = effect(adapter)
    receipt(server, adapter, old_request)
    path = tmp_path / "state.sqlite3"
    seed_v3(path, [(old_request, "COMMIT_UNKNOWN", True)], quarantined=True)
    before = path.read_bytes()
    with StateStore(path, tenant_id=TENANT, maintenance=True, read_only=True, read_schema_version=3) as store:
        report = recovery_inventory(store, adapter, since=SINCE, until=UNTIL)
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert report["findings"][0]["classification"] == "REQUIRES_RECONCILIATION"
    assert report["errors"] == []
    assert report["target_writes"] == report["database_writes"] == 0
    assert all(item.method == "GET" for item in server.requests)
    assert path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["request_digest", "duplicate_json_key", "non_finite_json"])
def test_real_postgres_bad_request_rolls_back_all_projections(postgres_dsn, corruption):
    seed_v3(postgres_dsn, [(request(), "COMMIT_UNKNOWN", True)])
    with raw(postgres_dsn) as connection:
        if corruption == "request_digest":
            values = {"request_digest": "sha256:" + "f" * 64}
        else:
            payload = json.dumps(request().model_dump(mode="json"))
            suffix = (
                ', "effect_id": "duplicate"}' if corruption == "duplicate_json_key" else ', "invalid": NaN}'
            )
            values = {"request_json": payload[:-1] + suffix}
        execute_core(connection, update(effect_intents).values(**values))
    before = snapshot(postgres_dsn)
    with pytest.raises(IntegrityError, match=r"EFFECT_REQUEST_(INVALID|BINDING_INVALID)"):
        migrate_postgres(postgres_dsn, tenant_id=TENANT)
    after = snapshot(postgres_dsn)
    assert after["effect_intents"] == before["effect_intents"]
    assert after["target_barriers"] == before["target_barriers"]
    assert after["store_metadata"][0]["schema_version"] == 3
    assert after["store_metadata"][0]["recovery_required"] == 1


def test_real_postgres_runtime_role_cannot_migrate_global_effect_projection(postgres_runtime):
    deployment = postgres_runtime(tenant_id=TENANT)
    seed_v3(deployment["migration_dsn"], [(request(), "COMMIT_UNKNOWN", True)])
    before = snapshot(deployment["migration_dsn"])
    with pytest.raises(IntegrityError, match="MIGRATION_REQUIRES_RLS_BYPASS"):
        migrate_postgres(deployment["runtime_dsn"], tenant_id=TENANT)
    assert snapshot(deployment["migration_dsn"]) == before
