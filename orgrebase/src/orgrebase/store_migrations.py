"""Versioned migrations for the canonical local StateStore database.

Version 1 adopts the released, unversioned schema without rewriting business
rows or content addresses. Schema changes must receive a new migration/version;
never edit the version 1 statements to upgrade an existing database in place.
"""

from __future__ import annotations

import json
import re
import sqlite3

import psycopg
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from orgrebase.database import (
    DEFAULT_WORKSPACE_ID,
    EFFECT_QUERY_INDEXES,
    EMPTY_EVENT_DIGEST,
    SCOPED_TABLES,
    Connection,
    metadata,
    workspace_registry,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store_schema_v2 import POSTGRES_SCHEMA_V2
from orgrebase.store_schema_v4 import migrate_effect_barriers

STATE_STORE_SCHEMA_VERSION = 5
_ASCII_CASE_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

_SCHEMA_V1 = (
    """CREATE TABLE object_versions (
        version_key TEXT PRIMARY KEY,
        object_id TEXT NOT NULL,
        version TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        UNIQUE(object_id, version)
        )""",
    """CREATE TABLE version_states (
        version_key TEXT PRIMARY KEY REFERENCES object_versions(version_key),
        state TEXT NOT NULL
        )""",
    """CREATE TABLE current_pointers (
        object_id TEXT PRIMARY KEY,
        version_key TEXT NOT NULL REFERENCES object_versions(version_key),
        revision INTEGER NOT NULL DEFAULT 1
        )""",
    """CREATE TABLE domain_events (
        sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_digest TEXT NOT NULL,
        event_digest TEXT NOT NULL UNIQUE
        )""",
    """CREATE TABLE idempotency_records (
        key TEXT PRIMARY KEY,
        request_digest TEXT NOT NULL,
        result_json TEXT NOT NULL
        )""",
    """CREATE TABLE artifacts (
        artifact_id TEXT PRIMARY KEY,
        media_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_digest TEXT NOT NULL
        )""",
    """CREATE TABLE external_operation_journal (
        operation_key TEXT PRIMARY KEY,
        request_digest TEXT NOT NULL,
        operation TEXT NOT NULL,
        repository_id TEXT NOT NULL,
        status TEXT NOT NULL,
        expected_external_parent TEXT NOT NULL,
        external_operation_id TEXT,
        result_json TEXT,
        error_code TEXT
        )""",
    """CREATE TABLE compensation_sagas (
        saga_key TEXT PRIMARY KEY,
        request_digest TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        receipt_json TEXT NOT NULL
        )""",
)

_SCHEMA_V2 = (
    """CREATE TABLE store_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        tenant_id TEXT,
        schema_version INTEGER NOT NULL,
        recovery_required INTEGER NOT NULL DEFAULT 0 CHECK (recovery_required IN (0, 1))
        )""",
    """CREATE TABLE effect_intents (
        effect_id TEXT PRIMARY KEY,
        target_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        request_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('READY', 'DISPATCHING', 'COMMIT_UNKNOWN', 'CONFIRMED', 'REJECTED')),
        result_json TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        lease_owner TEXT,
        lease_until DOUBLE PRECISION,
        fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0),
        CHECK ((lease_owner IS NULL) = (lease_until IS NULL))
        )""",
    """CREATE TABLE target_barriers (
        target_key TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE REFERENCES effect_intents(effect_id)
        )""",
    """CREATE TABLE source_checkpoints (
        connector_id TEXT PRIMARY KEY,
        cursor TEXT,
        revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
        lease_owner TEXT,
        lease_until DOUBLE PRECISION,
        fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0),
        CHECK ((lease_owner IS NULL) = (lease_until IS NULL))
        )""",
    """CREATE TABLE private_records (
        record_id TEXT PRIMARY KEY,
        scope_ref TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        content_digest TEXT NOT NULL,
        content_json TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        deleted_at TEXT,
        deletion_reason TEXT,
        CHECK ((content_json IS NOT NULL AND deleted_at IS NULL AND deletion_reason IS NULL) OR
               (content_json IS NULL AND deleted_at IS NOT NULL AND deletion_reason IS NOT NULL))
        )""",
)


def _normalized_definition(sql: str) -> tuple[str, ...]:
    # Ignore identifier formatting/case while preserving literals and constraints. An
    # unfamiliar schema fails closed instead of being silently stamped current.
    # SQLite identifier case comparison is ASCII-only: Unicode casefold would
    # mistake a different table such as artifact\u017f for the required artifacts.
    return tuple(
        token if token.startswith("'") else token.translate(_ASCII_CASE_FOLD)
        for token in re.findall(r"'(?:''|[^'])*'|\w+|[^\w\s]", sql)
    )


# Compare parsed CHECK definitions without discarding operator grouping.
_POSTGRES_CHECKS_V2 = {
    "store_metadata": ("CHECK ((singleton = 1))", "CHECK ((recovery_required = ANY (ARRAY[0, 1])))"),
    "effect_intents": (
        "CHECK (((lease_owner IS NULL) = (lease_until IS NULL)))",
        "CHECK ((fence >= 0))",
        "CHECK ((state = ANY (ARRAY['READY'::text, 'DISPATCHING'::text, 'COMMIT_UNKNOWN'::text, 'CONFIRMED'::text, 'REJECTED'::text])))",
    ),
    "source_checkpoints": (
        "CHECK (((lease_owner IS NULL) = (lease_until IS NULL)))",
        "CHECK ((fence >= 0))",
        "CHECK ((revision >= 0))",
    ),
    "private_records": (
        "CHECK ((((content_json IS NOT NULL) AND (deleted_at IS NULL) AND (deletion_reason IS NULL)) OR "
        "((content_json IS NULL) AND (deleted_at IS NOT NULL) AND (deletion_reason IS NOT NULL))))",
    ),
}


_POSTGRES_CHECKS_V3 = {
    **_POSTGRES_CHECKS_V2,
    "effect_intents": _POSTGRES_CHECKS_V2["effect_intents"]
    + (
        "CHECK (((requested_action IS NULL) OR (requested_action = ANY (ARRAY['EXECUTE'::text, 'QUERY'::text, 'CANCEL'::text]))))",
        "CHECK ((command_revision >= 0))",
        "CHECK (((requested_action IS NULL) = (requested_at IS NULL)))",
    ),
    "workspace_registry": (
        "CHECK ((audit_sequence >= 0))",
        "CHECK ((change_count >= 0))",
        "CHECK (((pending_change_count >= 0) AND (pending_change_count <= change_count)))",
        "CHECK (((profile_digest IS NULL) = (quote_object_id IS NULL)))",
    ),
    "workspace_changes": (
        "CHECK ((ordinal > 0))",
        "CHECK (((resolution IS NULL) OR (resolution = ANY (ARRAY['APPLIED'::text, 'REJECTED'::text]))))",
    ),
    "oidc_login_transactions": ("CHECK ((expires_at > created_at))",),
    "browser_sessions": (
        "CHECK ((expires_at > created_at))",
        "CHECK ((((revoked_at IS NULL) AND (payload_ciphertext IS NOT NULL)) OR ((revoked_at IS NOT NULL) AND (payload_ciphertext IS NULL))))",
    ),
    "browser_session_revocations": (
        "CHECK ((kind = ANY (ARRAY['SUBJECT'::text, 'SID'::text, 'EVENT'::text])))",
    ),
}


def _sqlite_statements(version: int) -> tuple[str, ...]:
    if version < 3:
        return _SCHEMA_V1 + (_SCHEMA_V2 if version == 2 else ())
    from sqlalchemy.dialects.sqlite import dialect

    return tuple(
        _SCHEMA_V2[0]
        if table.name == "store_metadata"
        else str(CreateTable(table).compile(dialect=dialect()))
        for table in metadata.sorted_tables
    )


def _validate_sqlite(connection: sqlite3.Connection, version: int) -> None:
    expected = {
        statement.split()[2]: _normalized_definition(statement) for statement in _sqlite_statements(version)
    }
    actual = {
        row[0].translate(_ASCII_CASE_FOLD): _normalized_definition(row[1])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        )
    }
    if actual != expected:
        raise IntegrityError("STATE_STORE_SCHEMA_INCOMPATIBLE")
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE type IN ('trigger', 'view') LIMIT 1").fetchone():
        raise IntegrityError("STATE_STORE_SCHEMA_UNEXPECTED_BEHAVIOR")
    for table in expected:
        for index in connection.execute(f"PRAGMA index_list({table})"):
            if index[2] and index[3] == "c":
                raise IntegrityError("STATE_STORE_SCHEMA_UNEXPECTED_UNIQUE_INDEX")
    if version >= 5:
        _validate_effect_query_indexes(connection)


def _validate_effect_query_indexes(connection: Connection) -> None:
    """A v5 store must retain the lookup paths its polling budget assumes."""
    if isinstance(connection, sqlite3.Connection):
        from sqlalchemy.dialects.sqlite import dialect

        for index in EFFECT_QUERY_INDEXES:
            row = connection.execute(
                "SELECT tbl_name,sql FROM sqlite_schema WHERE type='index' AND name=?", (index.name,)
            ).fetchone()
            expected = str(CreateIndex(index).compile(dialect=dialect()))
            if row is None or row[0] != "effect_intents" or _normalized_definition(row[1]) != _normalized_definition(expected):
                raise IntegrityError(f"STATE_STORE_EFFECT_INDEX_INVALID:{index.name}")
        return
    for index in EFFECT_QUERY_INDEXES:
        row = connection.execute(
            "SELECT i.indisvalid,i.indisready,i.indisunique,a.amname,"
            "ARRAY(SELECT pg_get_indexdef(i.indexrelid,n,false) FROM generate_series(1,i.indnatts) n),"
            "pg_get_expr(i.indpred,i.indrelid) "
            "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam "
            "WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=%s "
            "AND i.indrelid='effect_intents'::regclass", (index.name,)
        ).fetchone()
        predicate = "(requested_action IS NOT NULL)" if index is EFFECT_QUERY_INDEXES[0] else None
        if row is None or tuple(row[:4]) != (True, True, False, "btree") or row[4] != [column.name for column in index.columns] or row[5] != predicate:
            raise IntegrityError(f"STATE_STORE_EFFECT_INDEX_INVALID:{index.name}")


def _upgrade_effect_query_indexes(connection: Connection) -> None:
    from sqlalchemy.dialects import postgresql, sqlite

    dialect = postgresql.dialect() if isinstance(connection, psycopg.Connection) else sqlite.dialect()
    for index in EFFECT_QUERY_INDEXES:
        connection.execute(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)))
    _validate_effect_query_indexes(connection)
    connection.execute("UPDATE store_metadata SET schema_version=5")
    if isinstance(connection, sqlite3.Connection):
        connection.execute("PRAGMA user_version=5")


def _history_head(connection: Connection) -> tuple[int, str]:
    """Validate the legacy chain once during the explicit schema transition."""
    previous = EMPTY_EVENT_DIGEST
    count = 0
    for row in connection.execute(
        "SELECT sequence_no,event_type,payload_json,previous_digest,event_digest FROM domain_events ORDER BY sequence_no"
    ):
        count += 1
        envelope = {
            "sequence_no": count,
            "event_type": row[1],
            "payload": json.loads(row[2]),
            "previous_digest": previous,
        }
        if row[0] != count or row[3] != previous or row[4] != sha256_digest(envelope):
            raise IntegrityError(f"event chain failed at sequence {count}")
        previous = row[4]
    return count, previous


def _create_sqlite_indexes(connection: sqlite3.Connection) -> None:
    from sqlalchemy.dialects.sqlite import dialect

    for table in metadata.sorted_tables:
        for index in table.indexes:
            if index in EFFECT_QUERY_INDEXES:
                continue  # The v3 transition retains its released index set.
            connection.execute(str(CreateIndex(index).compile(dialect=dialect())))


def _upgrade_sqlite_v3(connection: sqlite3.Connection) -> None:
    from sqlalchemy.dialects.sqlite import dialect

    sequence, head = _history_head(connection)
    existing = {
        row[0].lower()
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        )
    }
    rebuilt = ({table.name for table in SCOPED_TABLES} | {"effect_intents", "target_barriers"}) & existing
    indexes = list(
        connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='index' AND sql IS NOT NULL")
    )
    managed_indexes = {index.name for table in metadata.sorted_tables for index in table.indexes}
    for name, _statement in indexes:
        connection.execute('DROP INDEX "' + name.replace('"', '""') + '"')
    for table in metadata.sorted_tables:
        if table.name in rebuilt:
            connection.execute(f'ALTER TABLE "{table.name}" RENAME TO "_v2_{table.name}"')
    for table in metadata.sorted_tables:
        if table.name not in existing or table.name in rebuilt:
            connection.execute(str(CreateTable(table).compile(dialect=dialect())))
    connection.execute(
        "INSERT INTO workspace_registry(workspace_id,audit_sequence,audit_head) VALUES (?,?,?)",
        (DEFAULT_WORKSPACE_ID, sequence, head),
    )
    for table in metadata.sorted_tables:
        if table.name not in rebuilt:
            continue
        columns = [name for name in POSTGRES_SCHEMA_V2[table.name]["columns"]]
        names = ",".join('"' + name + '"' for name in columns)
        connection.execute(f'INSERT INTO "{table.name}" ({names}) SELECT {names} FROM "_v2_{table.name}"')
    for table in reversed(metadata.sorted_tables):
        if table.name in rebuilt:
            connection.execute(f'DROP TABLE "_v2_{table.name}"')
    _create_sqlite_indexes(connection)
    for name, statement in indexes:
        if name not in managed_indexes:
            connection.execute(statement)
    from orgrebase.change_projection import backfill_event_projections
    backfill_event_projections(connection)
    connection.execute("UPDATE store_metadata SET schema_version=3")
    connection.execute("PRAGMA user_version = 3")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise IntegrityError("STATE_STORE_LEGACY_FOREIGN_KEY_VIOLATION")


def migrate_state_store(connection: Connection) -> None:
    """Apply a versioned migration atomically, without rewriting content addresses."""
    if isinstance(connection, psycopg.Connection):
        _migrate_postgres(connection)
        return
    if not connection.in_transaction:
        raise RuntimeError("STATE_STORE_MIGRATION_REQUIRES_TRANSACTION")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, 2, 3, 4, STATE_STORE_SCHEMA_VERSION):
        raise IntegrityError(f"STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:{version}")
    existing = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
    ).fetchone()
    if version == 0 and existing is None:
        for statement in _SCHEMA_V1:
            connection.execute(statement)
    _validate_sqlite(connection, 1 if version == 0 else version)
    if version < 2:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise IntegrityError("STATE_STORE_LEGACY_FOREIGN_KEY_VIOLATION")
        for statement in _SCHEMA_V2:
            connection.execute(statement)
        connection.execute("CREATE INDEX private_records_expiry ON private_records(expires_at)")
        connection.execute("INSERT INTO store_metadata(singleton,tenant_id,schema_version) VALUES (1,NULL,2)")
        connection.execute("PRAGMA user_version = 2")
        version = 2
    if version == 2:
        _upgrade_sqlite_v3(connection)
        version = 3
    if version == 3:
        validate_state_store(connection, version=3)
        migrate_effect_barriers(connection)
        connection.execute("UPDATE store_metadata SET schema_version=4")
        connection.execute("PRAGMA user_version = 4")
        version = 4
    if version == 4:
        validate_state_store(connection, version=4)
        _upgrade_effect_query_indexes(connection)
    validate_state_store(connection)


_BARRIER_OWN_EFFECT = """(EXISTS (SELECT 1 FROM effect_intents e WHERE
    ((e.effect_id = target_barriers.effect_id) AND (e.target_key = target_barriers.target_key)
    AND (e.workspace_id = current_setting('orgrebase.workspace_id'::text, true)))))"""
_BARRIER_TERMINAL_EFFECT = """(EXISTS (SELECT 1 FROM effect_intents e WHERE
    ((e.effect_id = target_barriers.effect_id) AND (e.target_key = target_barriers.target_key)
    AND (e.workspace_id = current_setting('orgrebase.workspace_id'::text, true))
    AND (e.state = ANY (ARRAY['CONFIRMED'::text, 'REJECTED'::text])))))"""


_REGISTRY_BINDING_GUARD = """BEGIN
    IF OLD.profile_digest IS NOT NULL AND
       ROW(NEW.workspace_id, NEW.profile_digest, NEW.pack_digest, NEW.quote_object_id, NEW.registered_at)
       IS DISTINCT FROM ROW(OLD.workspace_id, OLD.profile_digest, OLD.pack_digest, OLD.quote_object_id, OLD.registered_at)
    THEN
        RAISE EXCEPTION 'WORKSPACE_BINDING_IMMUTABLE' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END"""


def _create_postgres_policies(connection: psycopg.Connection) -> None:
    from psycopg import sql

    for table in (*SCOPED_TABLES, metadata.tables["effect_intents"]):
        identifier = sql.Identifier(table.name)
        connection.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(identifier))
        connection.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(identifier))
        connection.execute(
            sql.SQL(
                "CREATE POLICY workspace_isolation ON {} USING "
                "(workspace_id = current_setting('orgrebase.workspace_id', true)) "
                "WITH CHECK (workspace_id = current_setting('orgrebase.workspace_id', true))"
            ).format(identifier)
        )
    connection.execute("ALTER TABLE workspace_registry ENABLE ROW LEVEL SECURITY")
    connection.execute("ALTER TABLE workspace_registry FORCE ROW LEVEL SECURITY")
    connection.execute("CREATE POLICY workspace_catalog_read ON workspace_registry FOR SELECT USING (true)")
    connection.execute(
        "CREATE POLICY workspace_catalog_register ON workspace_registry FOR INSERT WITH CHECK (true)"
    )
    connection.execute(
        "CREATE POLICY workspace_catalog_update ON workspace_registry FOR UPDATE USING (workspace_id = current_setting('orgrebase.workspace_id', true)) WITH CHECK (workspace_id = current_setting('orgrebase.workspace_id', true))"
    )
    connection.execute(
        sql.SQL("CREATE FUNCTION preserve_workspace_binding() RETURNS trigger LANGUAGE plpgsql AS {}").format(
            sql.Literal(_REGISTRY_BINDING_GUARD)
        )
    )
    connection.execute(
        "CREATE TRIGGER preserve_workspace_binding BEFORE UPDATE ON workspace_registry FOR EACH ROW EXECUTE FUNCTION preserve_workspace_binding()"
    )
    connection.execute("ALTER TABLE target_barriers ENABLE ROW LEVEL SECURITY")
    connection.execute("ALTER TABLE target_barriers FORCE ROW LEVEL SECURITY")
    connection.execute("CREATE POLICY target_barriers_read ON target_barriers FOR SELECT USING (true)")
    connection.execute(
        "CREATE POLICY target_barriers_acquire ON target_barriers FOR INSERT WITH CHECK "
        + _BARRIER_OWN_EFFECT
    )
    connection.execute(
        "CREATE POLICY target_barriers_release ON target_barriers FOR DELETE USING "
        + _BARRIER_TERMINAL_EFFECT
    )


def _upgrade_postgres_v3(connection: psycopg.Connection) -> None:
    from psycopg import sql
    from sqlalchemy import MetaData
    from sqlalchemy.dialects.postgresql import dialect

    # AddConstraint changes SQLAlchemy's inline-DDL rule. Compile against a
    # detached schema so upgrading one database cannot affect another store.
    transition_metadata = MetaData()
    for table in metadata.sorted_tables:
        table.to_metadata(transition_metadata)
    sequence, head = _history_head(connection)
    connection.execute(str(CreateTable(workspace_registry).compile(dialect=dialect())))
    connection.execute(
        "INSERT INTO workspace_registry(workspace_id,audit_sequence,audit_head) VALUES (%s,%s,%s)",
        (DEFAULT_WORKSPACE_ID, sequence, head),
    )
    for table in (*SCOPED_TABLES, metadata.tables["effect_intents"]):
        if table.name not in POSTGRES_SCHEMA_V2:
            continue
        connection.execute(
            sql.SQL("ALTER TABLE {} ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'default'").format(
                sql.Identifier(table.name)
            )
        )
    connection.execute("ALTER TABLE domain_events ADD COLUMN subject_key TEXT")
    connection.execute("ALTER TABLE effect_intents ADD COLUMN requested_action TEXT")
    connection.execute("ALTER TABLE effect_intents ADD COLUMN command_revision BIGINT NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE effect_intents ADD COLUMN requested_at TEXT")
    for constraint in transition_metadata.tables["effect_intents"].constraints:
        if type(constraint).__name__ == "CheckConstraint" and any(
            name in str(constraint.sqltext) for name in ("requested_action", "command_revision")
        ):
            connection.execute(str(AddConstraint(constraint).compile(dialect=dialect())))
    # Rebuild only keys and foreign keys; payload columns are never rewritten.
    for table in SCOPED_TABLES:
        constraints = list(
            connection.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype IN ('p','u','f')",
                (table.name,),
            )
        )
        for row in constraints:
            connection.execute(
                sql.SQL("ALTER TABLE {} DROP CONSTRAINT {} CASCADE").format(
                    sql.Identifier(table.name), sql.Identifier(row[0])
                )
            )
    for table in metadata.sorted_tables:
        if table in SCOPED_TABLES and table.name in POSTGRES_SCHEMA_V2:
            for constraint in transition_metadata.tables[table.name].constraints:
                if type(constraint).__name__ in {
                    "PrimaryKeyConstraint",
                    "UniqueConstraint",
                    "ForeignKeyConstraint",
                }:
                    connection.execute(str(AddConstraint(constraint).compile(dialect=dialect())))
        elif table.name == "effect_intents":
            for constraint in transition_metadata.tables[table.name].foreign_key_constraints:
                connection.execute(str(AddConstraint(constraint).compile(dialect=dialect())))
        elif table.name not in POSTGRES_SCHEMA_V2 and table is not workspace_registry:
            connection.execute(str(CreateTable(table).compile(dialect=dialect())))
    connection.execute("DROP INDEX IF EXISTS private_records_expiry")
    for table in metadata.sorted_tables:
        for index in table.indexes:
            if index in EFFECT_QUERY_INDEXES:
                continue  # Added only by the v4-to-v5 transition.
            connection.execute(str(CreateIndex(index).compile(dialect=dialect())))
    from orgrebase.change_projection import backfill_event_projections
    backfill_event_projections(connection)
    _create_postgres_policies(connection)
    connection.execute("UPDATE store_metadata SET schema_version=3")


def _migrate_postgres(connection: psycopg.Connection) -> None:
    from psycopg.pq import TransactionStatus
    from sqlalchemy.dialects.postgresql import dialect

    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError("STATE_STORE_MIGRATION_REQUIRES_TRANSACTION")
    # Only schema transitions share this lock; current-schema runtime open is read-only.
    connection.execute("SELECT pg_advisory_xact_lock(7216815720491801)")
    tables = {
        row[0]
        for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
    }
    if not tables:
        for table in metadata.sorted_tables:
            connection.execute(str(CreateTable(table).compile(dialect=dialect())))
            for index in table.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=dialect())))
        connection.execute("INSERT INTO store_metadata(singleton,tenant_id,schema_version) VALUES (1,NULL,5)")
        connection.execute("INSERT INTO workspace_registry(workspace_id) VALUES ('default')")
        _create_postgres_policies(connection)
    else:
        if "store_metadata" not in tables:
            raise IntegrityError("STATE_STORE_SCHEMA_INCOMPATIBLE")
        version = connection.execute("SELECT schema_version FROM store_metadata").fetchone()[0]
        if version == 2:
            _validate_postgres(connection, version=2)
            _upgrade_postgres_v3(connection)
            version = 3
        if version == 3:
            _validate_postgres(connection, version=3)
            migrate_effect_barriers(connection)
            connection.execute("UPDATE store_metadata SET schema_version=4")
            version = 4
        if version == 4:
            _validate_postgres(connection, version=4)
            _upgrade_effect_query_indexes(connection)
        elif version != STATE_STORE_SCHEMA_VERSION:
            raise IntegrityError(f"STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:{version}")
    _validate_postgres(connection)


def prepare_legacy_restore_inspection(connection: psycopg.Connection) -> None:
    """Expose released v2 rows for read-only inspection before target remapping.

    This operator-only intermediate transition uses the same v2 migration as a
    normal upgrade. It is only used in an isolated restore, which must verify
    the original backup's target projections before proceeding to v4.
    """
    from psycopg.pq import TransactionStatus

    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError("STATE_STORE_MIGRATION_REQUIRES_TRANSACTION")
    connection.execute("SELECT pg_advisory_xact_lock(7216815720491801)")
    _validate_postgres(connection, version=2)
    row = connection.execute("SELECT recovery_required FROM store_metadata WHERE singleton=1").fetchone()
    if not row or not row[0]:
        raise IntegrityError("STATE_STORE_EFFECT_MIGRATION_REQUIRES_QUARANTINE")
    _upgrade_postgres_v3(connection)
    _validate_postgres(connection, version=3)


def validate_state_store(connection: Connection, *, version: int = STATE_STORE_SCHEMA_VERSION) -> None:
    """Inspect an existing current schema without creation, migration or rebinding."""
    if isinstance(connection, psycopg.Connection):
        _validate_postgres(connection, version=version)
        return
    actual_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if actual_version != version:
        raise IntegrityError(f"STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:{actual_version}")
    _validate_sqlite(connection, version)
    rows = connection.execute("SELECT singleton,schema_version FROM store_metadata").fetchall()
    if [tuple(row) for row in rows] != [(1, version)]:
        raise IntegrityError("STATE_STORE_SCHEMA_METADATA_INVALID")


def _validate_postgres(connection: psycopg.Connection, *, version: int = STATE_STORE_SCHEMA_VERSION) -> None:
    from sqlalchemy.dialects.postgresql import dialect

    expected_tables = set(POSTGRES_SCHEMA_V2) if version == 2 else set(metadata.tables)
    tables = {
        row[0]
        for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
    }
    if tables != expected_tables:
        raise IntegrityError("STATE_STORE_SCHEMA_INCOMPATIBLE")
    rows = connection.execute("SELECT singleton,schema_version FROM store_metadata").fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise IntegrityError("STATE_STORE_SCHEMA_METADATA_INVALID")
    if rows[0][1] != version:
        raise IntegrityError(f"STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:{rows[0][1]}")
    if version >= 5:
        _validate_effect_query_indexes(connection)
    type_names = {
        "TEXT": "text",
        "INTEGER": "integer",
        "BIGINT": "bigint",
        "DOUBLE PRECISION": "double precision",
    }
    for name in expected_tables:
        actual = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s",
                (name,),
            )
        }
        table = metadata.tables[name]
        if version == 2:
            expected = POSTGRES_SCHEMA_V2[name]["columns"]
            expected_constraints = POSTGRES_SCHEMA_V2[name]["constraints"]
        else:
            expected = {
                column.name: (
                    type_names[str(column.type.compile(dialect=dialect()))],
                    "YES" if column.nullable else "NO",
                )
                for column in table.columns
            }
            compiler = CreateTable(table).compile(dialect=dialect())
            expected_constraints = [
                compiler.process(c) for c in table.constraints if type(c).__name__ != "CheckConstraint"
            ] + list(_POSTGRES_CHECKS_V3.get(name, ()))
        if actual != expected:
            raise IntegrityError(f"STATE_STORE_SCHEMA_INCOMPATIBLE:{name}")
        actual_constraints = sorted(
            _normalized_definition(row[0])
            for row in connection.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass(%s)", (name,)
            )
        )
        if actual_constraints != sorted(_normalized_definition(value) for value in expected_constraints):
            raise IntegrityError(f"STATE_STORE_SCHEMA_CONSTRAINTS_INVALID:{name}")
    if connection.execute(
        "SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid WHERE t.relnamespace=current_schema()::regnamespace AND i.indisunique AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid=i.indexrelid) LIMIT 1"
    ).fetchone():
        raise IntegrityError("STATE_STORE_SCHEMA_UNEXPECTED_UNIQUE_INDEX")
    if connection.execute(
        "SELECT 1 FROM information_schema.views WHERE table_schema=current_schema() LIMIT 1"
    ).fetchone():
        raise IntegrityError("STATE_STORE_SCHEMA_UNEXPECTED_BEHAVIOR")
    triggers = [
        tuple(row)
        for row in connection.execute(
            "SELECT t.tgname,c.relname,t.tgtype,t.tgenabled,t.tgqual,t.tgnargs,p.proname,p.prosrc,p.prosecdef,l.lanname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_language l ON l.oid=p.prolang WHERE c.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal"
        )
    ]
    expected_triggers = (
        [
            (
                "preserve_workspace_binding",
                "workspace_registry",
                19,
                "O",
                None,
                0,
                "preserve_workspace_binding",
                _REGISTRY_BINDING_GUARD,
                False,
                "plpgsql",
            )
        ]
        if version >= 3
        else []
    )
    if triggers != expected_triggers:
        raise IntegrityError("STATE_STORE_SCHEMA_UNEXPECTED_BEHAVIOR")
    if version >= 3:
        expected_policy = _normalized_definition(
            "(workspace_id = current_setting('orgrebase.workspace_id'::text, true))"
        )
        for table in (*SCOPED_TABLES, metadata.tables["effect_intents"]):
            relation = connection.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(%s)",
                (table.name,),
            ).fetchone()
            policies = connection.execute(
                "SELECT polname,polcmd,polpermissive,pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid=to_regclass(%s)",
                (table.name,),
            ).fetchall()
            if (
                not relation
                or not all(relation)
                or len(policies) != 1
                or policies[0][0] != "workspace_isolation"
                or policies[0][1] != "*"
                or not policies[0][2]
                or any(
                    _normalized_definition(value) != expected_policy
                    for value in (policies[0][3], policies[0][4])
                )
            ):
                raise IntegrityError(f"STATE_STORE_WORKSPACE_POLICY_INVALID:{table.name}")

        relation = connection.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='workspace_registry'::regclass"
        ).fetchone()
        policies = {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                "SELECT polname,polcmd,polpermissive,pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid='workspace_registry'::regclass"
            )
        }
        scope = "(workspace_id = current_setting('orgrebase.workspace_id'::text, true))"
        if not all(relation) or policies != {
            "workspace_catalog_read": ("r", True, "true", None),
            "workspace_catalog_register": ("a", True, None, "true"),
            "workspace_catalog_update": ("w", True, scope, scope),
        }:
            raise IntegrityError("STATE_STORE_WORKSPACE_POLICY_INVALID:workspace_registry")

        relation = connection.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='target_barriers'::regclass"
        ).fetchone()
        policies = {
            row[0]: (
                row[1],
                row[2],
                _normalized_definition(row[3]) if row[3] else None,
                _normalized_definition(row[4]) if row[4] else None,
            )
            for row in connection.execute(
                "SELECT polname,polcmd,polpermissive,pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid='target_barriers'::regclass"
            )
        }
        if not all(relation) or policies != {
            "target_barriers_read": ("r", True, _normalized_definition("true"), None),
            "target_barriers_acquire": ("a", True, None, _normalized_definition(_BARRIER_OWN_EFFECT)),
            "target_barriers_release": ("d", True, _normalized_definition(_BARRIER_TERMINAL_EFFECT), None),
        }:
            raise IntegrityError("STATE_STORE_WORKSPACE_POLICY_INVALID:target_barriers")
