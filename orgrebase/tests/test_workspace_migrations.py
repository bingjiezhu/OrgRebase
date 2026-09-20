from __future__ import annotations

import json

import psycopg
import pytest
from psycopg import sql

from orgrebase.database import SCOPED_TABLES
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.store import StateStore
from orgrebase.store_operations import migrate_postgres
from orgrebase.store_schema_v2 import POSTGRES_SCHEMA_V2

_ORDER = (
    "store_metadata",
    "object_versions",
    "version_states",
    "current_pointers",
    "domain_events",
    "idempotency_records",
    "artifacts",
    "external_operation_journal",
    "compensation_sagas",
    "effect_intents",
    "target_barriers",
    "source_checkpoints",
    "private_records",
)


def _create_v2(connection):
    for name in _ORDER:
        contract = POSTGRES_SCHEMA_V2[name]
        fields = [
            sql.SQL("{} {} {}").format(
                sql.Identifier(column), sql.SQL(native), sql.SQL("NOT NULL" if nullable == "NO" else "")
            )
            for column, (native, nullable) in contract["columns"].items()
        ]
        fields.extend(sql.SQL(value) for value in contract["constraints"])
        connection.execute(
            sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(name), sql.SQL(",").join(fields))
        )
    connection.execute("INSERT INTO store_metadata VALUES(0,2,1,'org:legacy')")
    # Explicit named columns make the retained descriptor independent of column order.
    values = {
        "object_versions": dict(
            version_key="quote@v1",
            object_id="quote",
            version="v1",
            payload_json=' {"preserved": true} ',
            payload_digest="old-digest",
        ),
        "version_states": dict(version_key="quote@v1", state="CURRENT"),
        "current_pointers": dict(object_id="quote", version_key="quote@v1", revision=7),
        "idempotency_records": dict(key="key", request_digest="request", result_json=' {"result":1} '),
        "artifacts": dict(
            artifact_id="a",
            media_type="application/json",
            payload_json=canonical_json({"value": 1}),
            payload_digest=sha256_digest({"value": 1}),
        ),
        "external_operation_journal": dict(
            operation_key="op",
            request_digest="r",
            operation="COMMIT",
            repository_id="r",
            status="RECORDED",
            expected_external_parent="p",
            external_operation_id="c",
            result_json="{}",
            error_code=None,
        ),
        "compensation_sagas": dict(
            saga_key="saga", request_digest="r", status="DONE", attempt=2, receipt_json="{}"
        ),
        "effect_intents": dict(
            effect_id="e",
            target_key="target",
            state="COMMIT_UNKNOWN",
            request_digest="r",
            request_json=' {"old": true} ',
            result_json=None,
            error_code="TIMEOUT",
            created_at="2026-09-09T00:00:00Z",
            updated_at="2026-09-09T00:00:00Z",
            lease_owner=None,
            lease_until=None,
            fence=2,
        ),
        "target_barriers": dict(target_key="target", effect_id="e"),
        "source_checkpoints": dict(
            connector_id="source", cursor="cursor", revision=3, lease_owner=None, lease_until=None, fence=1
        ),
        "private_records": dict(
            record_id="private",
            scope_ref="run",
            owner_id="person",
            content_digest=sha256_digest({"secret": "original"}),
            content_json=' {"secret": "original"} ',
            created_at="2026-09-09T00:00:00Z",
            expires_at="2026-10-09T00:00:00Z",
            deleted_at=None,
            deletion_reason=None,
        ),
    }
    previous = "sha256:" + "0" * 64
    for sequence in range(1, 4):
        envelope = dict(
            sequence_no=sequence, event_type="LEGACY", payload={"n": sequence}, previous_digest=previous
        )
        digest = sha256_digest(envelope)
        connection.execute(
            "INSERT INTO domain_events(sequence_no,event_type,payload_json,previous_digest,event_digest) VALUES(%s,%s,%s,%s,%s)",
            (sequence, "LEGACY", json.dumps(envelope["payload"], indent=2), previous, digest),
        )
        previous = digest
    for table, payload in values.items():
        connection.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, payload)),
                sql.SQL(",").join(sql.Placeholder() for _ in payload),
            ),
            tuple(payload.values()),
        )
    return previous


def _original_rows(connection):
    return {
        name: connection.execute(
            sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                sql.SQL(",").join(map(sql.Identifier, contract["columns"])),
                sql.Identifier(name),
                sql.Identifier(next(iter(contract["columns"]))),
            )
        ).fetchall()
        for name, contract in POSTGRES_SCHEMA_V2.items()
    }


def test_real_postgres_v2_migration_preserves_original_bytes_and_shared_unknown(postgres_dsn):
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        head = _create_v2(connection)
        before = _original_rows(connection)
    migrate_postgres(postgres_dsn, tenant_id="org:legacy")
    with StateStore(postgres_dsn, tenant_id="org:legacy", maintenance=True) as store:
        after = _original_rows(store.connection)
        for name in before:
            if name != "store_metadata":
                assert [tuple(row) for row in after[name]] == before[name]
        assert store.audit_head() == {"sequence_no": 3, "head_digest": head}
        assert store.verify_event_chain() == {"status": "PASS", "events": 3, "head_digest": head}
        for table in SCOPED_TABLES:
            rows = store.connection.execute(
                sql.SQL("SELECT DISTINCT workspace_id FROM {}").format(sql.Identifier(table.name))
            ).fetchall()
            assert {row[0] for row in rows} <= {"default"}
        assert store.get_target_barrier("target") == "e"
        assert store.get_effect("e")["state"] == "COMMIT_UNKNOWN"
        store.record_event("NEW", {})
    with StateStore(postgres_dsn, tenant_id="org:legacy", migrate=False, maintenance=True) as reopened:
        assert reopened.verify_event_chain()["events"] == 4
        assert reopened.load_artifact("a").payload == {"value": 1}


def test_real_postgres_v3_migration_failure_rolls_back_all_ddl(postgres_dsn, monkeypatch):
    from orgrebase import store_migrations

    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        _create_v2(connection)
        before = _original_rows(connection)
    original = store_migrations._create_postgres_policies

    def fail_after_alterations(connection):
        original(connection)
        raise RuntimeError("INJECTED_MIGRATION_INTERRUPTION")

    monkeypatch.setattr(store_migrations, "_create_postgres_policies", fail_after_alterations)
    with pytest.raises(RuntimeError, match="INJECTED_MIGRATION_INTERRUPTION"):
        StateStore(postgres_dsn, tenant_id="org:legacy")
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        assert _original_rows(connection) == before
        assert connection.execute("SELECT to_regclass('workspace_registry')").fetchone()[0] is None
        assert connection.execute("SELECT schema_version FROM store_metadata").fetchone()[0] == 2
    monkeypatch.setattr(store_migrations, "_create_postgres_policies", original)
    migrate_postgres(postgres_dsn, tenant_id="org:legacy")
    with StateStore(postgres_dsn, tenant_id="org:legacy", maintenance=True) as store:
        assert store.verify_event_chain()["events"] == 3


def test_sqlite_v2_transition_preserves_private_effect_and_audit_bytes(tmp_path):
    import sqlite3

    from orgrebase.store_migrations import _SCHEMA_V1, _SCHEMA_V2

    path = tmp_path / "released-v2.sqlite"
    with sqlite3.connect(path) as connection:
        for statement in (*_SCHEMA_V1, *_SCHEMA_V2):
            connection.execute(statement)
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            "INSERT INTO store_metadata(singleton,tenant_id,schema_version) VALUES(1,'org:legacy',2)"
        )
        _payload = canonical_json({"old": True})
        connection.execute(
            "INSERT INTO artifacts VALUES('artifact','application/json',?,?)",
            (_payload, sha256_digest({"old": True})),
        )
        connection.execute(
            "INSERT INTO effect_intents(effect_id,target_key,request_digest,request_json,state,created_at,updated_at) VALUES('effect','target','digest',?,'COMMIT_UNKNOWN','2026-09-09T00:00:00Z','2026-09-09T00:00:00Z')",
            (_payload,),
        )
        connection.execute("INSERT INTO target_barriers VALUES('target','effect')")
        connection.execute(
            "INSERT INTO private_records VALUES('private','scope','owner','digest',NULL,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z','2026-09-09T00:00:00Z','USER_REQUEST')"
        )
        private_before = connection.execute("SELECT * FROM private_records").fetchone()
    # Persist local recovery isolation before the atomic schema transition.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE store_metadata SET recovery_required=1")
    with StateStore(path, tenant_id="org:legacy", maintenance=True) as store:
        assert store.load_artifact("artifact").payload == {"old": True}
        assert store.get_effect("effect")["state"] == "COMMIT_UNKNOWN"
        assert store.get_target_barrier("target") == "effect"
        assert (
            tuple(store.connection.execute("SELECT * FROM private_records").fetchone())[1:] == private_before
        )
        assert store.verify_event_chain()["events"] == 0
