from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql

from orgrebase.database import postgres_row_factory
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.store_operations import backup_postgres, database_dsn, restore_postgres

TENANT = "org:index-migration"
INDEXES = ("effect_intents_pending_commands", "effect_intents_workspace_state")


@pytest.fixture(params=["sqlite", "postgres"])
def database(request, tmp_path):
    return request.getfixturevalue("postgres_dsn") if request.param == "postgres" else tmp_path / "state.sqlite3"


@contextmanager
def raw(database):
    if str(database).startswith("postgresql:"):
        connection = psycopg.connect(database, autocommit=True, row_factory=postgres_row_factory)
    else:
        connection = sqlite3.connect(database, isolation_level=None)
        connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def seed_v4(database):
    with StateStore(database, tenant_id=TENANT, maintenance=True) as store:
        store.record_event("ORIGINAL_EVENT", {"content": "unchanged"})
        with store.transaction() as connection:
            store.save_artifact(connection, "original", "application/json", {"source": "original"})
            store.put_effect(connection, effect_id="effect:pending", target_key="target:one",
                             request_digest=sha256_digest({"target": "one"}), request={"target": "one"},
                             created_at="2026-09-12T00:00:00Z")
            store.request_effect_action(connection, effect_id="effect:pending", action="EXECUTE",
                                      request_digest=sha256_digest({"target": "one"}), expected_state="READY")
    with raw(database) as connection:
        for name in INDEXES:
            connection.execute(f"DROP INDEX {name}")
        connection.execute("UPDATE store_metadata SET schema_version=4")
        if isinstance(connection, sqlite3.Connection):
            connection.execute("PRAGMA user_version=4")


def contents(database):
    with raw(database) as connection:
        return {name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
                for name in ("effect_intents", "target_barriers", "artifacts", "domain_events", "current_pointers")}


def test_v4_read_only_and_v5_upgrade_preserve_all_business_rows(database):
    seed_v4(database)
    before = contents(database)
    with StateStore(database, tenant_id=TENANT, maintenance=True, read_only=True, read_schema_version=4) as store:
        assert store.check_health()["schema_version"] == 4
        assert len(store.pending_effects()) == 1
    assert contents(database) == before
    with StateStore(database, tenant_id=TENANT, maintenance=True) as store:
        assert store.check_health()["schema_version"] == 5
        assert store.pending_effects()[0]["effect_id"] == "effect:pending"
        assert store.verify_event_chain()["events"] == 1
    assert contents(database) == before
    with StateStore(database, tenant_id=TENANT, migrate=False) as store:
        assert store.check_health()["schema_version"] == 5


def test_index_name_collision_rolls_back_without_stamping_v5(database):
    seed_v4(database)
    before = contents(database)
    with raw(database) as connection:
        connection.execute("CREATE INDEX effect_intents_pending_commands ON effect_intents(state)")
    with pytest.raises(IntegrityError, match="STATE_STORE_EFFECT_INDEX_INVALID"):
        StateStore(database, tenant_id=TENANT, maintenance=True)
    with raw(database) as connection:
        assert connection.execute("SELECT schema_version FROM store_metadata").fetchone()[0] == 4
    assert contents(database) == before


def test_v5_runtime_rejects_missing_managed_index(database):
    with StateStore(database, tenant_id=TENANT):
        pass
    with raw(database) as connection:
        connection.execute("DROP INDEX effect_intents_pending_commands")
    with pytest.raises(IntegrityError, match="STATE_STORE_EFFECT_INDEX_INVALID"):
        StateStore(database, tenant_id=TENANT, migrate=False)


def test_pending_query_uses_index_without_scanning_completed_history(database):
    with StateStore(database, tenant_id=TENANT, maintenance=True):
        pass
    with raw(database) as connection:
        rows = [(f"effect:{i:05d}", "CONFIRMED" if i < 2000 else "READY",
                 "EXECUTE" if i >= 3000 else None, "2026-09-12T00:00:00Z" if i >= 3000 else None)
                for i in range(3002)]
        placeholder = "%s" if isinstance(connection, psycopg.Connection) else "?"
        statement = ("INSERT INTO effect_intents(workspace_id,effect_id,target_key,request_digest,request_json,"
                     "state,created_at,updated_at,requested_action,requested_at) "
                     f"VALUES ('default',{placeholder},'target','digest','{{}}',{placeholder},'now','now',{placeholder},{placeholder})")
        if isinstance(connection, psycopg.Connection):
            with connection.transaction(), connection.cursor() as cursor:
                cursor.executemany(statement, rows)
        else:
            connection.execute("BEGIN")
            connection.executemany(statement, rows)
            connection.commit()
        connection.execute("ANALYZE effect_intents")
        query = ("SELECT effect_id FROM effect_intents WHERE workspace_id='default' "
                 "AND requested_action IS NOT NULL AND state IN ('READY','DISPATCHING','COMMIT_UNKNOWN') "
                 "ORDER BY requested_at,effect_id LIMIT 100")
        explain = "EXPLAIN (FORMAT JSON) " if isinstance(connection, psycopg.Connection) else "EXPLAIN QUERY PLAN "
        plan = json.dumps([tuple(row) for row in connection.execute(explain + query)], default=str)
        assert "effect_intents_pending_commands" in plan
    with StateStore(database, tenant_id=TENANT, migrate=False) as store:
        assert [item["effect_id"] for item in store.pending_effects()] == ["effect:03000", "effect:03001"]


def test_v4_backup_restores_to_v5_without_relabeling_target_identity(postgres_dsn, tmp_path):
    seed_v4(postgres_dsn)
    backup = tmp_path / "backup"
    manifest = backup_postgres(postgres_dsn, tenant_id=TENANT, output=backup, read_schema_version=4)
    assert manifest["state_store_schema_version"] == 4
    result = restore_postgres(postgres_dsn, tenant_id=TENANT, backup=backup, latest_deletion_ledger=[])
    try:
        assert result["restored_schema_version"] == 4
        assert result["database"]["schema_version"] == 5
        assert result["derived_target_identity_migrated"] is False
        assert result["writes_released"] is False
        with StateStore(database_dsn(postgres_dsn, result["database_name"]), tenant_id=TENANT,
                        maintenance=True, read_only=True) as store:
            assert store.get_effect("effect:pending")["request_digest"] == sha256_digest({"target": "one"})
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(result["database_name"])))
