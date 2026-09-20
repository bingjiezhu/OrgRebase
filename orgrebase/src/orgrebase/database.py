"""Database connections and SQLAlchemy Core schema for the canonical store."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.sql import Executable

from orgrebase.digest import canonical_json
from orgrebase.event_projection import empty_scopes

Connection = sqlite3.Connection | psycopg.Connection
metadata = MetaData()
DEFAULT_WORKSPACE_ID = "default"
EMPTY_EVENT_DIGEST = "sha256:" + "0" * 64

workspace_registry = Table(
    "workspace_registry",
    metadata,
    Column("workspace_id", Text, primary_key=True),
    Column("profile_digest", Text),
    Column("pack_digest", Text),
    Column("quote_object_id", Text),
    Column("registered_at", Text),
    Column("audit_sequence", BigInteger, nullable=False, server_default="0"),
    Column("audit_head", Text, nullable=False, server_default=EMPTY_EVENT_DIGEST),
    Column("change_count", BigInteger, nullable=False, server_default="0"),
    Column("pending_change_count", BigInteger, nullable=False, server_default="0"),
    Column("event_scopes_json", Text, nullable=False, server_default=canonical_json(empty_scopes())),
    CheckConstraint("audit_sequence >= 0"),
    CheckConstraint("change_count >= 0"),
    CheckConstraint("pending_change_count >= 0 AND pending_change_count <= change_count"),
    CheckConstraint("(profile_digest IS NULL) = (quote_object_id IS NULL)"),
)


def _workspace_column(*, primary_key: bool = True) -> Column:
    return Column(
        "workspace_id",
        Text,
        ForeignKey("workspace_registry.workspace_id"),
        primary_key=primary_key,
        nullable=False,
        server_default=DEFAULT_WORKSPACE_ID,
    )


object_versions = Table(
    "object_versions",
    metadata,
    _workspace_column(),
    Column("version_key", Text, primary_key=True),
    Column("object_id", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("payload_digest", Text, nullable=False),
    UniqueConstraint("workspace_id", "object_id", "version"),
)
version_states = Table(
    "version_states",
    metadata,
    _workspace_column(),
    Column("version_key", Text, primary_key=True),
    ForeignKeyConstraint(
        ["workspace_id", "version_key"],
        ["object_versions.workspace_id", "object_versions.version_key"],
        deferrable=True,
    ),
    Column("state", Text, nullable=False),
)
current_pointers = Table(
    "current_pointers",
    metadata,
    _workspace_column(),
    Column("object_id", Text, primary_key=True),
    Column("version_key", Text, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "version_key"],
        ["object_versions.workspace_id", "object_versions.version_key"],
        deferrable=True,
    ),
    Column("revision", BigInteger, nullable=False, server_default="1"),
)
domain_events = Table(
    "domain_events",
    metadata,
    _workspace_column(),
    Column("sequence_no", BigInteger, primary_key=True, autoincrement=False),
    Column("event_type", Text, nullable=False),
    Column("subject_key", Text),
    Column("payload_json", Text, nullable=False),
    Column("previous_digest", Text, nullable=False),
    Column("event_digest", Text, nullable=False),
    UniqueConstraint("workspace_id", "event_digest"),
)
idempotency_records = Table(
    "idempotency_records",
    metadata,
    _workspace_column(),
    Column("key", Text, primary_key=True),
    Column("request_digest", Text, nullable=False),
    Column("result_json", Text, nullable=False),
)
artifacts = Table(
    "artifacts",
    metadata,
    _workspace_column(),
    Column("artifact_id", Text, primary_key=True),
    Column("media_type", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("payload_digest", Text, nullable=False),
)
external_operation_journal = Table(
    "external_operation_journal",
    metadata,
    _workspace_column(),
    Column("operation_key", Text, primary_key=True),
    Column("request_digest", Text, nullable=False),
    Column("operation", Text, nullable=False),
    Column("repository_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("expected_external_parent", Text, nullable=False),
    Column("external_operation_id", Text),
    Column("result_json", Text),
    Column("error_code", Text),
)
compensation_sagas = Table(
    "compensation_sagas",
    metadata,
    _workspace_column(),
    Column("saga_key", Text, primary_key=True),
    Column("request_digest", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt", BigInteger, nullable=False),
    Column("receipt_json", Text, nullable=False),
)
store_metadata = Table(
    "store_metadata",
    metadata,
    Column("singleton", Integer, primary_key=True, autoincrement=False),
    Column("tenant_id", Text),
    Column("schema_version", Integer, nullable=False),
    Column("recovery_required", Integer, nullable=False, server_default="0"),
    CheckConstraint("singleton = 1"),
    CheckConstraint("recovery_required IN (0, 1)"),
)
effect_intents = Table(
    "effect_intents",
    metadata,
    _workspace_column(primary_key=False),
    Column("effect_id", Text, primary_key=True),
    Column("target_key", Text, nullable=False),
    Column("request_digest", Text, nullable=False),
    Column("request_json", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("result_json", Text),
    Column("error_code", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("lease_owner", Text),
    Column("lease_until", Double),
    Column("fence", BigInteger, nullable=False, server_default="0"),
    Column("requested_action", Text),
    Column("command_revision", BigInteger, nullable=False, server_default="0"),
    Column("requested_at", Text),
    CheckConstraint("requested_action IS NULL OR requested_action IN ('EXECUTE', 'QUERY', 'CANCEL')"),
    CheckConstraint("command_revision >= 0"),
    CheckConstraint("(requested_action IS NULL) = (requested_at IS NULL)"),
    CheckConstraint("state IN ('READY', 'DISPATCHING', 'COMMIT_UNKNOWN', 'CONFIRMED', 'REJECTED')"),
    CheckConstraint("fence >= 0"),
    CheckConstraint("(lease_owner IS NULL) = (lease_until IS NULL)"),
)
EFFECT_QUERY_INDEXES = (
    Index(
        "effect_intents_pending_commands",
        effect_intents.c.workspace_id,
        effect_intents.c.requested_at,
        effect_intents.c.effect_id,
        sqlite_where=effect_intents.c.requested_action.is_not(None),
        postgresql_where=effect_intents.c.requested_action.is_not(None),
    ),
    Index(
        "effect_intents_workspace_state",
        effect_intents.c.workspace_id,
        effect_intents.c.state,
        effect_intents.c.effect_id,
    ),
)
target_barriers = Table(
    "target_barriers",
    metadata,
    Column("target_key", Text, primary_key=True),
    Column("effect_id", Text, ForeignKey("effect_intents.effect_id"), nullable=False, unique=True),
)
source_checkpoints = Table(
    "source_checkpoints",
    metadata,
    _workspace_column(),
    Column("connector_id", Text, primary_key=True),
    Column("cursor", Text),
    Column("revision", BigInteger, nullable=False, server_default="0"),
    Column("lease_owner", Text),
    Column("lease_until", Double),
    Column("fence", BigInteger, nullable=False, server_default="0"),
    CheckConstraint("revision >= 0"),
    CheckConstraint("fence >= 0"),
    CheckConstraint("(lease_owner IS NULL) = (lease_until IS NULL)"),
)
private_records = Table(
    "private_records",
    metadata,
    _workspace_column(),
    Column("record_id", Text, primary_key=True),
    Column("scope_ref", Text, nullable=False),
    Column("owner_id", Text, nullable=False),
    Column("content_digest", Text, nullable=False),
    Column("content_json", Text),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("deleted_at", Text),
    Column("deletion_reason", Text),
    CheckConstraint(
        "(content_json IS NOT NULL AND deleted_at IS NULL AND deletion_reason IS NULL) OR "
        "(content_json IS NULL AND deleted_at IS NOT NULL AND deletion_reason IS NOT NULL)"
    ),
)
Index("private_records_expiry", private_records.c.workspace_id, private_records.c.expires_at)
Index(
    "domain_events_by_type",
    domain_events.c.workspace_id,
    domain_events.c.event_type,
    domain_events.c.sequence_no,
)

workspace_changes = Table(
    "workspace_changes",
    metadata,
    _workspace_column(),
    Column("event_id", Text, primary_key=True),
    Column("ordinal", BigInteger, nullable=False),
    Column("registration_sequence", BigInteger, nullable=False),
    Column("artifact_id", Text, nullable=False),
    Column("artifact_digest", Text, nullable=False),
    Column("event_digest", Text, nullable=False),
    Column("owner_id", Text, nullable=False),
    Column("object_id", Text, nullable=False),
    Column("base_version", Text, nullable=False),
    Column("base_digest", Text, nullable=False),
    Column("operation", Text, nullable=False),
    Column("valid_from", Double, nullable=False),
    Column("valid_to", Double),
    Column("preview_expires_at", Double),
    Column("preview_snapshot_ref", Text),
    Column("preview_snapshot_digest", Text),
    Column("approval_expires_at", Double),
    Column("resolution", Text),
    UniqueConstraint("workspace_id", "ordinal"),
    ForeignKeyConstraint(
        ("workspace_id", "artifact_id"), ("artifacts.workspace_id", "artifacts.artifact_id")
    ),
    ForeignKeyConstraint(
        ("workspace_id", "registration_sequence"), ("domain_events.workspace_id", "domain_events.sequence_no")
    ),
    CheckConstraint("ordinal > 0"),
    CheckConstraint("resolution IS NULL OR resolution IN ('APPLIED', 'REJECTED')"),
)
Index(
    "domain_events_by_subject",
    domain_events.c.workspace_id,
    domain_events.c.event_type,
    domain_events.c.subject_key,
    domain_events.c.sequence_no,
)
Index(
    "workspace_changes_pending",
    workspace_changes.c.workspace_id,
    workspace_changes.c.resolution,
    workspace_changes.c.ordinal,
)
Index(
    "workspace_changes_source",
    workspace_changes.c.workspace_id,
    workspace_changes.c.object_id,
    workspace_changes.c.base_version,
    workspace_changes.c.base_digest,
    workspace_changes.c.resolution,
    workspace_changes.c.ordinal,
)

SCOPED_TABLES = frozenset(
    (
        object_versions,
        version_states,
        current_pointers,
        domain_events,
        idempotency_records,
        artifacts,
        external_operation_journal,
        compensation_sagas,
        source_checkpoints,
        private_records,
        workspace_changes,
    )
)

# SQLite remains the isolated single-workspace backend. Its constraint rejects
# accidental namespace writes even when a caller uses a direct SQL statement.
for _table in (*SCOPED_TABLES, effect_intents, workspace_registry):
    _table.append_constraint(
        CheckConstraint("workspace_id = 'default'", info={"sqlite_only": True}).ddl_if(dialect="sqlite")
    )

oidc_login_transactions = Table(
    "oidc_login_transactions",
    metadata,
    Column("transaction_id", Text, primary_key=True),
    Column("browser_digest", Text, nullable=False),
    Column("payload_ciphertext", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    CheckConstraint("expires_at > created_at"),
)
browser_sessions = Table(
    "browser_sessions",
    metadata,
    Column("session_id", Text, primary_key=True),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("sid", Text),
    Column("payload_ciphertext", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("revoked_at", BigInteger),
    CheckConstraint("expires_at > created_at"),
    CheckConstraint(
        "(revoked_at IS NULL AND payload_ciphertext IS NOT NULL) OR (revoked_at IS NOT NULL AND payload_ciphertext IS NULL)"
    ),
)
browser_session_revocations = Table(
    "browser_session_revocations",
    metadata,
    Column("scope_id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("revoked_before", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    CheckConstraint("kind IN ('SUBJECT', 'SID', 'EVENT')"),
)
Index("browser_sessions_subject", browser_sessions.c.issuer, browser_sessions.c.subject)
Index("browser_sessions_sid", browser_sessions.c.issuer, browser_sessions.c.sid)
Index("browser_sessions_expiry", browser_sessions.c.expires_at)
Index("oidc_login_transactions_expiry", oidc_login_transactions.c.expires_at)
Index("browser_session_revocations_expiry", browser_session_revocations.c.expires_at)


class DatabaseRow:
    """The named and positional access supported by both database drivers."""

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        self._names = names
        self._values = values

    def __getitem__(self, key: str | int) -> Any:
        return self._values[self._names.index(key) if isinstance(key, str) else key]

    def __iter__(self):
        return iter(self._values)

    def keys(self) -> Sequence[str]:
        return self._names


def postgres_row_factory(cursor: psycopg.Cursor):
    names = tuple(column.name for column in cursor.description or ())
    return lambda values: DatabaseRow(names, values)


def in_transaction(connection: Connection) -> bool:
    if isinstance(connection, sqlite3.Connection):
        return connection.in_transaction
    return connection.info.transaction_status != TransactionStatus.IDLE


def execute_core(connection: Connection, statement: Executable):
    """Compile parameterized expressions using the driver's actual SQL dialect."""

    dialect = sqlite_dialect() if isinstance(connection, sqlite3.Connection) else postgres_dialect()
    compiled = statement.compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
    parameters = compiled.params
    for name, processor in compiled._bind_processors.items():
        if name in parameters:
            parameters[name] = processor(parameters[name])
    values = tuple(parameters[name] for name in compiled.positiontup) if compiled.positional else parameters
    return connection.execute(str(compiled), values)


def runtime_role_is_safe(connection: psycopg.Connection, role: str | None = None) -> bool:
    """Runtime roles cannot own or redefine the schema that enforces isolation."""
    row = connection.execute(
        """SELECT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
          AND NOT has_schema_privilege(r.oid, current_schema(), 'CREATE')
          AND NOT EXISTS (SELECT 1 FROM pg_database d WHERE d.datname=current_database()
              AND pg_has_role(r.oid,d.datdba,'MEMBER'))
          AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace=current_schema()::regnamespace
              AND pg_has_role(r.oid,c.relowner,'MEMBER'))
          AND NOT EXISTS (SELECT 1 FROM pg_roles elevated WHERE (elevated.rolsuper OR elevated.rolbypassrls)
              AND pg_has_role(r.oid,elevated.oid,'MEMBER'))
          FROM pg_roles r WHERE r.rolname=COALESCE(%s,current_user)""",
        (role,),
    ).fetchone()
    return row is not None and bool(row[0])
