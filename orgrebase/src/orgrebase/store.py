"""Canonical business storage with SQLite and PostgreSQL persistence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from orgrebase.database import (
    DEFAULT_WORKSPACE_ID,
    EMPTY_EVENT_DIGEST,
    SCOPED_TABLES,
    Connection,
    artifacts,
    compensation_sagas,
    current_pointers,
    domain_events,
    effect_intents,
    execute_core,
    external_operation_journal,
    idempotency_records,
    in_transaction,
    metadata,
    object_versions,
    postgres_row_factory,
    runtime_role_is_safe,
    source_checkpoints,
    store_metadata,
    target_barriers,
    version_states,
    workspace_registry,
)
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.event_projection import append_scope, empty_scopes, event_subject
from orgrebase.fixture import EnterpriseFixture
from orgrebase.local_storage import prepare_private_sqlite_path, require_safe_sqlite_wal_runtime
from orgrebase.runtime_contracts import ArtifactWrite, StoredArtifact
from orgrebase.store_migrations import STATE_STORE_SCHEMA_VERSION, migrate_state_store, validate_state_store


class StateStore:
    """Canonical local store.

    Business payload rows are immutable. Trust state and the current pointer are
    version-relative overlays with an append-only event for every transition.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        tenant_id: str | None = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        migrate: bool = True,
        maintenance: bool = False,
        read_only: bool = False,
        read_schema_version: int | None = None,
    ) -> None:
        if read_schema_version is not None and (
            type(read_schema_version) is not int or read_schema_version not in {3, 4} or not maintenance or not read_only
        ):
            raise ValueError("STATE_STORE_HISTORICAL_READ_REQUIRES_MAINTENANCE_READ_ONLY_V3_OR_V4")
        self._expected_schema_version = read_schema_version or STATE_STORE_SCHEMA_VERSION
        self._validate_workspace_id(workspace_id)
        self.workspace_id = workspace_id
        configured = str(path)
        self.backend = "postgresql" if configured.startswith(("postgresql://", "postgres://")) else "sqlite"
        if self.backend == "sqlite" and workspace_id != DEFAULT_WORKSPACE_ID:
            raise ValueError("WORKSPACE_POSTGRESQL_REQUIRED")
        if self.backend == "sqlite" and configured != ":memory:" and not read_only:
            require_safe_sqlite_wal_runtime()
        self.path = (
            configured if self.backend == "postgresql" or read_only else prepare_private_sqlite_path(path)
        )
        self._lock = threading.RLock()
        self._commit_checks: list[Callable[[], None]] | None = None
        self._closed = True
        self.tenant_id: str | None = None
        self._maintenance = maintenance
        self._read_only = read_only
        self._initializing = True
        if self.backend == "postgresql":
            if tenant_id is None:
                raise ValueError("POSTGRES_TENANT_ID_REQUIRED")
            self.connection = psycopg.connect(
                configured,
                autocommit=True,
                row_factory=postgres_row_factory,
                connect_timeout=10,
                options="-c lock_timeout=10000 -c statement_timeout=30000"
                + (" -c default_transaction_read_only=on" if read_only else ""),
            )
        else:
            self.connection = sqlite3.connect(
                Path(self.path).resolve(strict=True).as_uri() + "?mode=ro" if read_only else self.path,
                uri=read_only,
                timeout=10.0,
                isolation_level="DEFERRED",
                autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
                check_same_thread=False,
            )
            self.connection.row_factory = sqlite3.Row
        self._closed = False
        try:
            if self.backend == "postgresql":
                self.connection.execute(
                    "SELECT set_config('orgrebase.workspace_id', %s, false)", (self.workspace_id,)
                )
            if self.backend == "sqlite" and read_only:
                self.connection.execute("PRAGMA query_only = ON")
            elif self.backend == "sqlite":
                self.connection.execute("PRAGMA foreign_keys = ON")
                self.connection.execute("PRAGMA synchronous = FULL")
            if read_only or not migrate:
                validate_state_store(self.connection, version=self._expected_schema_version)
            else:
                self._migrate_schema()
            if self.backend == "sqlite" and self.path != ":memory:" and not read_only:
                mode = self.connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if mode != "wal":
                    raise RuntimeError("STATE_STORE_WAL_UNAVAILABLE")
            if tenant_id is not None:
                if read_only or not migrate:
                    self.tenant_id = tenant_id
                else:
                    self.bind_tenant(tenant_id)
            elif self._execute(self.connection, select(store_metadata.c.tenant_id)).fetchone()[0] is not None:
                raise IntegrityError("STATE_STORE_TENANT_ID_REQUIRED")
            self._initializing = False
            self._verify_tenant()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.connection.close()
            self._closed = True

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive resource cleanup
        with suppress(Exception):
            self.close()

    def _migrate_schema(self) -> None:
        if self.backend == "postgresql":
            row = self.connection.execute("SELECT to_regclass('store_metadata')").fetchone()
            if row[0] is not None:
                metadata_row = self.connection.execute("SELECT schema_version FROM store_metadata").fetchone()
                if metadata_row is None:
                    raise IntegrityError("STATE_STORE_SCHEMA_METADATA_INVALID")
                version = metadata_row[0]
                if version == STATE_STORE_SCHEMA_VERSION:
                    validate_state_store(self.connection)
                    return
        with self.transaction() as connection:
            migrate_state_store(connection)

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """One write unit; cancellation and failed commits also roll back.

        Nested units are rejected before taking ownership of the outer unit.
        This lock protects users of this connection; BEGIN IMMEDIATE and the
        bounded SQLite busy timeout arbitrate other connections/processes.
        """

        with self._lock:
            self._require_open()
            if self._read_only:
                raise RuntimeError("STATE_STORE_READ_ONLY")
            if in_transaction(self.connection):
                raise RuntimeError("STATE_STORE_TRANSACTION_ALREADY_ACTIVE")
            self.connection.execute("BEGIN IMMEDIATE" if self.backend == "sqlite" else "BEGIN")
            checks: list[Callable[[], None]] = []
            self._commit_checks = checks
            try:
                if not self._initializing:
                    self._verify_tenant()
                yield self.connection
                if not self._initializing:
                    self._verify_tenant()
                self._commit_checks = None
                for check in checks:
                    check()
                self.connection.commit()
            except BaseException as error:
                try:
                    self.connection.rollback()
                except (sqlite3.Error, psycopg.Error) as rollback_error:
                    error.add_note(f"StateStore rollback failed: {rollback_error}")
                    self.close()
                raise
            finally:
                self._commit_checks = None

    def require_before_commit(self, connection: Connection, check: Callable[[], None]) -> None:
        """Require a predicate at the owning transaction's final write boundary.

        Borrowers register predicates for the owning context manager. Catching
        an earlier operation error does not remove them. Checks cannot add more
        checks; this contract does not sandbox callers with raw connection access.
        """

        with self._lock:
            self._require_open()
            if connection is not self.connection:
                raise RuntimeError("STATE_STORE_FOREIGN_TRANSACTION")
            if self._commit_checks is None or not in_transaction(connection):
                raise RuntimeError("STATE_STORE_MANAGED_TRANSACTION_REQUIRED")
            self._commit_checks.append(check)

    def _execute(self, connection: Connection, statement):
        self._require_open()
        if connection is not self.connection:
            raise RuntimeError("STATE_STORE_FOREIGN_TRANSACTION")
        if getattr(statement, "is_dml", False):
            if getattr(self, "_snapshot_depth", 0):
                raise RuntimeError("STATE_STORE_READ_ONLY_SNAPSHOT")
            if not in_transaction(connection):
                raise RuntimeError("STATE_STORE_WRITE_REQUIRES_TRANSACTION")
        return execute_core(connection, self._scope_statement(statement))

    def execute(self, connection: Connection, statement):
        """Execute a Core statement with this store's workspace criteria."""
        return self._execute(connection, statement)

    def _scope_statement(self, statement):
        scoped_tables = SCOPED_TABLES | ({effect_intents} if not self._maintenance else set())
        if getattr(statement, "is_insert", False) and statement.table in scoped_tables:
            return statement.values(workspace_id=self.workspace_id)
        if (
            getattr(statement, "is_update", False) or getattr(statement, "is_delete", False)
        ) and statement.table in scoped_tables:
            return statement.where(statement.table.c.workspace_id == self.workspace_id)
        if getattr(statement, "is_select", False):
            from sqlalchemy.sql.selectable import Join

            def tables(item):
                if isinstance(item, Join):
                    yield from tables(item.left)
                    yield from tables(item.right)
                elif item in scoped_tables:
                    yield item

            selected = {table for item in statement.get_final_froms() for table in tables(item)}
            for table in selected:
                statement = statement.where(table.c.workspace_id == self.workspace_id)
        return statement

    @staticmethod
    def _validate_workspace_id(workspace_id: str) -> None:
        if not isinstance(workspace_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", workspace_id
        ):
            raise ValueError("WORKSPACE_ID_INVALID")

    @contextmanager
    def read_connection(self) -> Iterator[Connection]:
        """Share this instance's connection under its lock and tenant binding.

        This does not start a database snapshot. A caller needing a consistent
        projection should use one SQL statement or an explicit transaction.
        """

        with self._lock:
            self._verify_tenant()
            yield self.connection

    @contextmanager
    def read_snapshot(self) -> Iterator[Connection]:
        """Own a read-only snapshot, or join the caller's existing transaction.

        A new transaction uses PostgreSQL repeatable read or SQLite's WAL
        snapshot. An existing transaction retains its isolation and raw-SQL
        permissions; joining does not promise stronger consistency or finalize
        that transaction. Store-mediated writes are rejected in either case.
        """
        with self._lock:
            self._require_open()
            owns_transaction = not in_transaction(self.connection)
            query_only = None
            depth = getattr(self, "_snapshot_depth", 0)
            read_only_snapshot = getattr(self, "_snapshot_transaction_read_only", False)
            body_error = None
            try:
                if owns_transaction:
                    if self.backend == "sqlite":
                        query_only = self.connection.execute("PRAGMA query_only").fetchone()[0]
                        self.connection.execute("PRAGMA query_only = ON")
                    self.connection.execute(
                        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
                        if self.backend == "postgresql" else "BEGIN"
                    )
                self._snapshot_depth = depth + 1
                self._snapshot_transaction_read_only = read_only_snapshot or owns_transaction
                self._verify_tenant()
                yield self.connection
            except BaseException as error:
                body_error = error
                raise
            finally:
                self._snapshot_depth = depth
                self._snapshot_transaction_read_only = read_only_snapshot
                if owns_transaction:
                    try:
                        self.connection.rollback()
                        if query_only is not None:
                            self.connection.execute(f"PRAGMA query_only = {int(query_only)}")
                    except BaseException as cleanup_error:
                        try:
                            self.close()
                        except BaseException as close_error:
                            cleanup_error.add_note(f"StateStore close failed: {close_error}")
                        finally:
                            self._closed = True
                        if body_error is None:
                            raise
                        body_error.add_note(f"StateStore snapshot cleanup failed: {cleanup_error}")
            if owns_transaction:
                # A stable view is not permission to use a workspace that entered
                # recovery while the view was being built. Check the new DB view.
                self._verify_tenant()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("STATE_STORE_CLOSED")

    def _insert(self, table):
        return postgres_insert(table) if self.backend == "postgresql" else sqlite_insert(table)

    def _lock_row(self, statement, *, skip_locked: bool = False):
        if (self.backend == "postgresql" and in_transaction(self.connection)
                and not self._read_only and not getattr(self, "_snapshot_transaction_read_only", False)):
            return statement.with_for_update(skip_locked=skip_locked)
        return statement

    def _verify_tenant(self) -> None:
        self._require_open()
        # Keep every guard current in one statement. This fixed, parameterized
        # query runs on every store read; no tenant or recovery state is cached.
        parameter = "%s" if self.backend == "postgresql" else "?"
        binding = (
            "current_setting('orgrebase.workspace_id', true)"
            if self.backend == "postgresql" else parameter
        )
        row = self.connection.execute(
            "SELECT tenant_id, recovery_required, schema_version, "
            "EXISTS (SELECT 1 FROM workspace_registry WHERE workspace_id = "
            + parameter + ") AS registered, " + binding + " AS binding FROM store_metadata",
            (self.workspace_id,) if self.backend == "postgresql" else (self.workspace_id, self.workspace_id),
        ).fetchone()
        if row is not None and row["binding"] != self.workspace_id:
            raise IntegrityError("STATE_STORE_WORKSPACE_BINDING_LOST")
        if row is None or row[0] != self.tenant_id:
            raise IntegrityError("STATE_STORE_TENANT_MISMATCH")
        if row[2] != self._expected_schema_version:
            raise IntegrityError(f"STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:{row[2]}")
        if not row["registered"]:
            raise IntegrityError("STATE_STORE_WORKSPACE_NOT_REGISTERED")
        if row[1] and not self._maintenance:
            raise IntegrityError("STATE_STORE_RECOVERY_QUALIFICATION_REQUIRED")

    def bind_tenant(self, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip() or tenant_id != tenant_id.strip():
            raise ValueError("STATE_STORE_TENANT_ID_INVALID")
        with self.transaction() as connection:
            row = self._execute(connection, self._lock_row(select(store_metadata.c.tenant_id))).fetchone()
            if row is None or row[0] not in (None, tenant_id):
                raise IntegrityError("STATE_STORE_TENANT_MISMATCH")
            if row[0] is None:
                if any(
                    self._execute(connection, select(table).limit(1)).fetchone() is not None
                    for table in metadata.sorted_tables
                    if table not in (store_metadata, workspace_registry)
                ):
                    raise IntegrityError("STATE_STORE_EXISTING_DATA_REQUIRES_TENANT_MIGRATION")
                self._execute(connection, update(store_metadata).values(tenant_id=tenant_id))
        self.tenant_id = tenant_id

    def check_health(self) -> dict[str, Any]:
        with self._lock:
            self._verify_tenant()
            metadata = self._execute(
                self.connection, select(store_metadata.c.recovery_required, store_metadata.c.schema_version)
            ).fetchone()
            return {
                "status": "PASS",
                "backend": self.backend,
                "schema_version": metadata[1],
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "runtime_role_safe": self.runtime_role_safe(),
                "recovery_required": bool(metadata[0]),
            }

    def runtime_role_safe(self) -> bool:
        if self.backend != "postgresql":
            return False
        return runtime_role_is_safe(self.connection)

    @staticmethod
    def _workspace_record(row) -> dict[str, Any]:
        return {column.name: row[column.name] for column in workspace_registry.columns}

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        self._validate_workspace_id(workspace_id)
        with self.read_connection() as connection:
            row = self._execute(
                connection,
                select(workspace_registry).where(workspace_registry.c.workspace_id == workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError("WORKSPACE_NOT_REGISTERED")
            return self._workspace_record(row)

    def register_workspace(
        self,
        workspace_id: str,
        *,
        profile_digest: str,
        pack_digest: str | None,
        quote_object_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        self._validate_workspace_id(workspace_id)
        if self.backend != "postgresql" and workspace_id != DEFAULT_WORKSPACE_ID:
            raise ValueError("WORKSPACE_POSTGRESQL_REQUIRED")
        if (
            not re.fullmatch(r"sha256:[0-9a-f]{64}", profile_digest)
            or (pack_digest is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", pack_digest))
            or not isinstance(quote_object_id, str)
            or not quote_object_id.strip()
        ):
            raise ValueError("WORKSPACE_BINDING_INVALID")
        from orgrebase.clock import utc_datetime

        utc_datetime(created_at)
        binding = {
            "profile_digest": profile_digest,
            "pack_digest": pack_digest,
            "quote_object_id": quote_object_id,
        }
        with self.transaction() as connection:
            self._execute(
                connection,
                self._insert(workspace_registry)
                .values(workspace_id=workspace_id, registered_at=created_at, **binding)
                .on_conflict_do_nothing(index_elements=["workspace_id"]),
            )
            statement = select(workspace_registry).where(workspace_registry.c.workspace_id == workspace_id)
            if workspace_id == self.workspace_id:
                statement = self._lock_row(statement)
            row = self._execute(connection, statement).fetchone()
            if row["profile_digest"] is None and workspace_id == DEFAULT_WORKSPACE_ID:
                self._execute(
                    connection,
                    update(workspace_registry)
                    .where(workspace_registry.c.workspace_id == workspace_id)
                    .values(registered_at=created_at, **binding),
                )
            elif any(row[key] != value for key, value in binding.items()):
                raise IntegrityError("WORKSPACE_BINDING_MISMATCH")
        return self.get_workspace(workspace_id)

    def bind_workspace(
        self, *, profile_digest: str, pack_digest: str | None, quote_object_id: str
    ) -> dict[str, Any]:
        return self.register_workspace(
            self.workspace_id,
            profile_digest=profile_digest,
            pack_digest=pack_digest,
            quote_object_id=quote_object_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    def list_workspaces(self, *, after: str | None = None, limit: int = 50) -> dict[str, Any]:
        self._page_limit(limit)
        if after is not None:
            self._validate_workspace_id(after)
        statement = select(workspace_registry).order_by(workspace_registry.c.workspace_id).limit(limit + 1)
        if after is not None:
            statement = statement.where(workspace_registry.c.workspace_id > after)
        with self.read_connection() as connection:
            rows = self._execute(connection, statement).fetchall()
        items = [self._workspace_record(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["workspace_id"] if len(rows) > limit else None}

    def audit_head(self) -> dict[str, Any]:
        """Read the append projection; this does not claim a full history audit."""
        with self.read_connection() as connection:
            row = self._execute(
                connection,
                select(workspace_registry).where(workspace_registry.c.workspace_id == self.workspace_id),
            ).fetchone()
            return {"sequence_no": int(row["audit_sequence"]), "head_digest": row["audit_head"]}

    def count_records(self) -> dict[str, int]:
        with self._lock:
            self._verify_tenant()
            names = ("artifacts", "object_versions", "current_pointers", "domain_events")
            row = self._execute(
                self.connection,
                select(
                    *(
                        select(func.count())
                        .select_from(metadata.tables[name])
                        .where(metadata.tables[name].c.workspace_id == self.workspace_id)
                        .scalar_subquery()
                        .label(name)
                        for name in names
                    )
                ),
            ).fetchone()
            return {name: int(row[name]) for name in names}

    def reset_local_session(self, *, authorize: Callable[[], Any] | None = None) -> None:
        if self.backend != "sqlite" or self.tenant_id is not None:
            raise RuntimeError("STATE_STORE_RESET_LOCAL_SESSION_ONLY")
        with self.transaction() as connection:
            if authorize is not None:
                authorize()
            for table in reversed(metadata.sorted_tables):
                if table not in (store_metadata, workspace_registry):
                    self._execute(connection, delete(table))
            self._execute(
                connection,
                update(workspace_registry)
                .where(workspace_registry.c.workspace_id == self.workspace_id)
                .values(
                    profile_digest=None,
                    pack_digest=None,
                    quote_object_id=None,
                    registered_at=None,
                    audit_sequence=0,
                    audit_head=EMPTY_EVENT_DIGEST,
                    change_count=0,
                    pending_change_count=0,
                    event_scopes_json=canonical_json(empty_scopes()),
                ),
            )
            connection.execute("DELETE FROM sqlite_sequence WHERE name='domain_events'")

    def load_fixture(self, fixture: EnterpriseFixture) -> None:
        with self.transaction() as connection:
            existing = self._execute(connection, select(object_versions.c.version_key).limit(1)).fetchone()
            if existing:
                return
            for item in fixture.objects:
                self.insert_version(connection, item, make_current=False)
            for item in fixture.objects:
                if item.state in {ObjectState.CURRENT, ObjectState.ACTIVE}:
                    self._execute(
                        connection,
                        self._insert(current_pointers)
                        .values(
                            object_id=item.id,
                            version_key=item.ref,
                            revision=1,
                        )
                        .on_conflict_do_update(
                            index_elements=["workspace_id", "object_id"],
                            set_={"version_key": item.ref, "revision": 1},
                        ),
                    )
            self.append_event(
                connection,
                "FIXTURE_LOADED",
                {"fixture_digest": fixture.digest, "object_count": len(fixture.objects)},
            )

    def insert_version(
        self,
        connection: Connection,
        item: VersionedObject,
        *,
        make_current: bool,
    ) -> None:
        # A frozen Pydantic model can still contain a mutated nested ``dict``
        # or be forged with ``model_copy`` while retaining its old digest.
        # The canonical store is the final authority boundary, so reparse the
        # current JSON shape and recompute its content address before writing.
        try:
            item = VersionedObject.model_validate(item.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityError("VERSIONED_OBJECT_MODEL_INVALID") from exc
        self._execute(
            connection,
            insert(object_versions).values(
                version_key=item.ref,
                object_id=item.id,
                version=item.version,
                payload_json=item.model_dump_json(),
                payload_digest=item.digest,
            ),
        )
        self._execute(connection, insert(version_states).values(version_key=item.ref, state=item.state.value))
        if make_current:
            self._execute(
                connection,
                self._insert(current_pointers)
                .values(
                    object_id=item.id,
                    version_key=item.ref,
                    revision=1,
                )
                .on_conflict_do_update(
                    index_elements=["workspace_id", "object_id"],
                    set_={
                        "version_key": item.ref,
                        "revision": current_pointers.c.revision + 1,
                    },
                ),
            )

    def _row_to_object(self, row: sqlite3.Row) -> VersionedObject:
        payload = json.loads(row["payload_json"])
        persisted = VersionedObject.model_validate(payload)
        if persisted.digest != row["payload_digest"]:
            raise IntegrityError("stored object payload digest mismatch")
        payload["state"] = row["effective_state"]
        return VersionedObject.model_validate(payload)

    def _lock_current_pointers(
        self, connection: Connection, object_ids: tuple[str, ...] | list[str]
    ) -> dict[str, str]:
        # A locking JOIN can recheck a new pointer against the old statement
        # snapshot and miss its newly committed version. Lock stable pointers
        # first; the following statement sees the version they now reference.
        rows = self._execute(
            connection,
            self._lock_row(
                select(current_pointers.c.object_id, current_pointers.c.version_key)
                .where(current_pointers.c.object_id.in_(object_ids))
                .order_by(current_pointers.c.object_id)
            ),
        ).fetchall()
        return {row["object_id"]: row["version_key"] for row in rows}

    def get_object(self, object_id: str, version: str | None = None) -> VersionedObject:
        with self._lock:
            self._verify_tenant()
            statement = select(
                object_versions.c.payload_json,
                object_versions.c.payload_digest,
                version_states.c.state.label("effective_state"),
            )
            if version is None:
                if (in_transaction(self.connection) and not self._read_only
                        and not getattr(self, "_snapshot_transaction_read_only", False)):
                    pointers = self._lock_current_pointers(self.connection, (object_id,))
                    if object_id not in pointers:
                        raise KeyError(f"missing object {object_id}@current")
                    statement = statement.select_from(object_versions.join(version_states)).where(
                        object_versions.c.version_key == pointers[object_id]
                    )
                else:
                    statement = statement.select_from(
                        current_pointers.join(object_versions).join(version_states)
                    ).where(current_pointers.c.object_id == object_id)
            else:
                statement = statement.select_from(object_versions.join(version_states)).where(
                    object_versions.c.object_id == object_id, object_versions.c.version == version
                )
            row = self._execute(self.connection, self._lock_row(statement)).fetchone()
            if row is None:
                raise KeyError(f"missing object {object_id}@{version or 'current'}")
            return self._row_to_object(row)

    def state_snapshot(self, object_ids: tuple[str, ...] | list[str]) -> dict[str, dict[str, str]]:
        with self._lock:
            self._verify_tenant()
            snapshot: dict[str, dict[str, str]] = {}
            pointers = (
                self._lock_current_pointers(self.connection, object_ids)
                if (in_transaction(self.connection) and not self._read_only
                    and not getattr(self, "_snapshot_transaction_read_only", False))
                else None
            )
            statement = (
                select(
                    object_versions.c.object_id,
                    object_versions.c.payload_json,
                    object_versions.c.payload_digest,
                    version_states.c.state.label("effective_state"),
                )
                .select_from(
                    object_versions.join(version_states)
                    if pointers is not None
                    else current_pointers.join(object_versions).join(version_states)
                )
                .where(
                    object_versions.c.version_key.in_(tuple(pointers.values()))
                    if pointers is not None
                    else current_pointers.c.object_id.in_(object_ids)
                )
                .order_by(object_versions.c.object_id)
            )
            rows = self._execute(self.connection, self._lock_row(statement)).fetchall()
            objects = {row["object_id"]: self._row_to_object(row) for row in rows}
            for object_id in object_ids:
                if object_id not in objects:
                    raise KeyError(f"missing object {object_id}@current")
                item = objects[object_id]
                snapshot[object_id] = {
                    "version": item.version,
                    "state": item.state.value,
                    "digest": item.digest,
                }
            return snapshot

    def _switch_pointer(
        self,
        connection: Connection,
        object_id: str,
        base_version: str,
        proposed_version: str,
        *,
        base_state: ObjectState,
        proposed_state: ObjectState,
    ) -> None:
        pointer_key = self._lock_current_pointers(connection, (object_id,)).get(object_id)
        if pointer_key != f"{object_id}@{base_version}":
            raise RuntimeError("EXPECTED_REVISION_MISMATCH")
        proposed_key = f"{object_id}@{proposed_version}"
        if (
            self._execute(
                connection,
                select(object_versions.c.version_key).where(object_versions.c.version_key == proposed_key),
            ).fetchone()
            is None
        ):
            raise KeyError(proposed_key)
        changed = self._execute(
            connection,
            update(current_pointers)
            .where(
                current_pointers.c.object_id == object_id,
                current_pointers.c.version_key == pointer_key,
            )
            .values(version_key=proposed_key, revision=current_pointers.c.revision + 1),
        ).rowcount
        if changed != 1:
            raise RuntimeError("EXPECTED_REVISION_MISMATCH")
        self._execute(
            connection,
            update(version_states)
            .where(version_states.c.version_key == pointer_key)
            .values(state=base_state.value),
        )
        self._execute(
            connection,
            update(version_states)
            .where(version_states.c.version_key == proposed_key)
            .values(state=proposed_state.value),
        )

    def promote_version(
        self,
        connection: Connection,
        object_id: str,
        base_version: str,
        proposed_version: str,
    ) -> None:
        self._switch_pointer(
            connection,
            object_id,
            base_version,
            proposed_version,
            base_state=ObjectState.SUPERSEDED,
            proposed_state=ObjectState.CURRENT,
        )

    def activate_version(
        self,
        connection: Connection,
        object_id: str,
        base_version: str,
        proposed_version: str,
        *,
        base_state: ObjectState,
        proposed_state: ObjectState,
    ) -> None:
        self._switch_pointer(
            connection,
            object_id,
            base_version,
            proposed_version,
            base_state=base_state,
            proposed_state=proposed_state,
        )

    def transition_current(
        self,
        connection: Connection,
        object_id: str,
        state: ObjectState,
    ) -> str:
        row = self._execute(
            connection,
            self._lock_row(
                select(current_pointers.c.version_key).where(current_pointers.c.object_id == object_id)
            ),
        ).fetchone()
        if row is None:
            raise KeyError(object_id)
        self._execute(
            connection,
            update(version_states)
            .where(version_states.c.version_key == row["version_key"])
            .values(state=state.value),
        )
        return row["version_key"]

    def append_event(
        self,
        connection: Connection,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> str:
        row = self._execute(
            connection,
            self._lock_row(
                select(workspace_registry).where(workspace_registry.c.workspace_id == self.workspace_id)
            ),
        ).fetchone()
        if row is None:
            raise IntegrityError("STATE_STORE_WORKSPACE_NOT_REGISTERED")
        previous_digest = row["audit_head"]
        next_sequence = int(row["audit_sequence"]) + 1
        envelope = {
            "sequence_no": next_sequence,
            "event_type": event_type,
            "payload": dict(payload),
            "previous_digest": previous_digest,
        }
        event_digest = sha256_digest(envelope)
        self._execute(
            connection,
            insert(domain_events).values(
                sequence_no=next_sequence,
                event_type=event_type,
                subject_key=event_subject(dict(payload)),
                payload_json=canonical_json(dict(payload)),
                previous_digest=previous_digest,
                event_digest=event_digest,
            ),
        )
        from orgrebase.change_projection import project_change

        total_delta, pending_delta = project_change(
            connection,
            workspace_id=self.workspace_id,
            sequence_no=next_sequence,
            event_type=event_type,
            payload=dict(payload),
            next_ordinal=row["change_count"] + 1,
        )
        scopes = append_scope(
            json.loads(row["event_scopes_json"]), {**envelope, "event_digest": event_digest}
        )
        self._execute(
            connection,
            update(workspace_registry)
            .where(workspace_registry.c.workspace_id == self.workspace_id)
            .values(
                audit_sequence=next_sequence,
                audit_head=event_digest,
                change_count=row["change_count"] + total_delta,
                pending_change_count=row["pending_change_count"] + pending_delta,
                event_scopes_json=canonical_json(scopes),
            ),
        )
        return event_digest

    def record_event(self, event_type: str, payload: Mapping[str, Any]) -> str:
        with self.transaction() as connection:
            return self.append_event(connection, event_type, payload)

    def verify_event_chain(self) -> dict[str, Any]:
        with self._lock:
            self._verify_tenant()
            events = select(domain_events).where(domain_events.c.workspace_id == self.workspace_id).subquery()
            snapshot = self._execute(
                self.connection,
                select(workspace_registry.c.audit_sequence, workspace_registry.c.audit_head, events)
                .select_from(
                    workspace_registry.outerjoin(
                        events, workspace_registry.c.workspace_id == events.c.workspace_id
                    )
                )
                .where(workspace_registry.c.workspace_id == self.workspace_id)
                .order_by(events.c.sequence_no),
            ).fetchall()
            if not snapshot:
                raise IntegrityError("STATE_STORE_WORKSPACE_NOT_REGISTERED")
            rows = [row for row in snapshot if row["sequence_no"] is not None]
            previous = "sha256:" + "0" * 64
            for expected_sequence, row in enumerate(rows, start=1):
                payload = json.loads(row["payload_json"])
                envelope = {
                    "sequence_no": expected_sequence,
                    "event_type": row["event_type"],
                    "payload": payload,
                    "previous_digest": previous,
                }
                expected = sha256_digest(envelope)
                if (
                    row["sequence_no"] != expected_sequence
                    or row["previous_digest"] != previous
                    or row["event_digest"] != expected
                ):
                    raise IntegrityError(f"event chain failed at sequence {expected_sequence}")
                previous = expected
            if len(rows) != snapshot[0]["audit_sequence"] or previous != snapshot[0]["audit_head"]:
                raise IntegrityError("STATE_STORE_AUDIT_HEAD_MISMATCH")
            return {"status": "PASS", "events": len(rows), "head_digest": previous}

    def event_records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            self._verify_tenant()
            rows = self._execute(
                self.connection,
                select(
                    domain_events.c.sequence_no,
                    domain_events.c.event_type,
                    domain_events.c.previous_digest,
                    domain_events.c.event_digest,
                ).order_by(domain_events.c.sequence_no),
            ).fetchall()
            return tuple(
                {
                    "sequence_no": int(row["sequence_no"]),
                    "event_type": row["event_type"],
                    "previous_digest": row["previous_digest"],
                    "event_digest": row["event_digest"],
                }
                for row in rows
            )

    def event_envelopes(self) -> tuple[dict[str, Any], ...]:
        """Return the exact public event envelopes needed for offline chain replay."""

        with self._lock:
            self._verify_tenant()
            rows = self._execute(
                self.connection, select(domain_events).order_by(domain_events.c.sequence_no)
            ).fetchall()
            return tuple(
                {
                    "sequence_no": int(row["sequence_no"]),
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                    "previous_digest": row["previous_digest"],
                    "event_digest": row["event_digest"],
                }
                for row in rows
            )

    def get_idempotent(
        self,
        key: str,
        request_digest: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Read one idempotency record, optionally inside the caller transaction."""

        def read(selected: Connection) -> dict[str, Any] | None:
            self._verify_tenant()
            if connection is not None and self.backend == "postgresql":
                key_hash = int.from_bytes(
                    hashlib.sha256(f"orgrebase.idempotency:{self.workspace_id}:{key}".encode()).digest()[:8],
                    signed=True,
                )
                selected.execute("SELECT pg_advisory_xact_lock(%s)", (key_hash,))
            row = self._execute(
                selected,
                select(idempotency_records.c.request_digest, idempotency_records.c.result_json).where(
                    idempotency_records.c.key == key
                ),
            ).fetchone()
            if row is None:
                return None
            if row["request_digest"] != request_digest:
                raise RuntimeError("IDEMPOTENCY_CONFLICT")
            return json.loads(row["result_json"])

        if connection is not None:
            return read(connection)
        with self._lock:
            return read(self.connection)

    def save_idempotent(
        self,
        connection: Connection,
        key: str,
        request_digest: str,
        result: Mapping[str, Any],
    ) -> None:
        self._execute(
            connection,
            insert(idempotency_records).values(
                key=key, request_digest=request_digest, result_json=canonical_json(dict(result))
            ),
        )

    def save_artifact(
        self,
        connection: Connection,
        artifact_id: str,
        media_type: str,
        payload: Mapping[str, Any],
    ) -> str:
        payload_json = canonical_json(dict(payload))
        digest = sha256_digest(dict(payload))
        existing = self._execute(
            connection,
            select(artifacts.c.media_type, artifacts.c.payload_json, artifacts.c.payload_digest).where(
                artifacts.c.artifact_id == artifact_id
            ),
        ).fetchone()
        if existing is not None:
            if (
                existing["media_type"] != media_type
                or existing["payload_json"] != payload_json
                or existing["payload_digest"] != digest
            ):
                raise IntegrityError(f"ARTIFACT_ID_CONFLICT:{artifact_id}")
            return digest
        self._execute(
            connection,
            insert(artifacts).values(
                artifact_id=artifact_id,
                media_type=media_type,
                payload_json=payload_json,
                payload_digest=digest,
            ),
        )
        return digest

    def save_artifact_writes(
        self,
        connection: Connection,
        writes: tuple[ArtifactWrite, ...],
    ) -> tuple[str, ...]:
        """Save a prepared immutable artifact bundle inside the caller transaction."""

        digests: list[str] = []
        for write in writes:
            if not isinstance(write, ArtifactWrite):
                raise TypeError("artifact bundle contains an invalid write")
            if sha256_digest(write.payload) != write.payload_digest:
                raise IntegrityError(f"ARTIFACT_PREPARED_DIGEST_MISMATCH:{write.artifact_id}")
            digests.append(
                self.save_artifact(
                    connection,
                    write.artifact_id,
                    write.media_type,
                    write.payload,
                )
            )
        return tuple(digests)

    def get_pointer(self, object_id: str) -> dict[str, Any]:
        with self._lock:
            self._verify_tenant()
            row = self._execute(
                self.connection,
                self._lock_row(
                    select(
                        current_pointers.c.version_key,
                        current_pointers.c.revision,
                        object_versions.c.version,
                        object_versions.c.payload_digest,
                    )
                    .select_from(current_pointers.join(object_versions))
                    .where(current_pointers.c.object_id == object_id)
                ),
            ).fetchone()
            if row is None:
                raise KeyError(object_id)
            return {
                "object_id": object_id,
                "version_key": row["version_key"],
                "version": row["version"],
                "revision": int(row["revision"]),
                "payload_digest": row["payload_digest"],
            }

    def load_artifact(
        self,
        artifact_id: str,
        expected_media_type: str | None = None,
    ) -> StoredArtifact:
        """Verified exact-ID artifact read with canonical round-trip validation."""

        with self._lock:
            self._verify_tenant()
            row = self._execute(
                self.connection, select(artifacts).where(artifacts.c.artifact_id == artifact_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"ARTIFACT_NOT_FOUND:{artifact_id}")
            return self._row_to_artifact(row, expected_media_type)

    def load_artifacts(
        self,
        artifact_ids: tuple[str, ...] | list[str],
        expected_media_type: str | None = None,
    ) -> dict[str, StoredArtifact]:
        """Read a bounded set of exact IDs, verifying every found artifact.

        Missing IDs are omitted so callers can distinguish absent optional
        records from records that fail integrity validation.
        """
        identities = self._batch_ids(artifact_ids)
        if not identities:
            return {}
        with self.read_connection() as connection:
            rows = self._execute(
                connection, select(artifacts).where(artifacts.c.artifact_id.in_(identities))
            ).fetchall()
            return {
                row["artifact_id"]: self._row_to_artifact(row, expected_media_type) for row in rows
            }

    @staticmethod
    def _row_to_artifact(row, expected_media_type: str | None = None) -> StoredArtifact:
        artifact_id = row["artifact_id"]
        if expected_media_type is not None and row["media_type"] != expected_media_type:
            raise IntegrityError(f"ARTIFACT_MEDIA_TYPE_MISMATCH:{artifact_id}")
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"ARTIFACT_PAYLOAD_INVALID:{artifact_id}") from exc
        if not isinstance(payload, dict) or canonical_json(payload) != row["payload_json"]:
            raise IntegrityError(f"ARTIFACT_PAYLOAD_INVALID:{artifact_id}")
        digest = sha256_digest(payload)
        if digest != row["payload_digest"]:
            raise IntegrityError(f"ARTIFACT_DIGEST_MISMATCH:{artifact_id}")
        return StoredArtifact(
            artifact_id=artifact_id, media_type=row["media_type"], payload=payload, payload_digest=digest
        )

    def artifact_exists(self, artifact_id: str, *, connection: Connection | None = None) -> bool:
        with self._lock:
            self._verify_tenant()
            return (
                self._execute(
                    connection or self.connection,
                    select(artifacts.c.artifact_id).where(artifacts.c.artifact_id == artifact_id),
                ).fetchone()
                is not None
            )

    def list_artifacts(
        self,
        *,
        artifact_id_prefix: str,
        expected_media_type: str | None = None,
    ) -> tuple[StoredArtifact, ...]:
        """Return a verified immutable artifact family in stable ID order."""

        if not artifact_id_prefix:
            raise ValueError("artifact_id_prefix must be non-empty")
        with self._lock:
            self._verify_tenant()
            rows = self._execute(
                self.connection,
                select(artifacts)
                .where(
                    artifacts.c.artifact_id.startswith(artifact_id_prefix, autoescape=True),
                    func.substr(artifacts.c.artifact_id, 1, len(artifact_id_prefix)) == artifact_id_prefix,
                )
                .order_by(artifacts.c.artifact_id),
            ).fetchall()
            return tuple(self._row_to_artifact(row, expected_media_type) for row in rows)

    def get_external_operation(self, operation_key: str) -> dict[str, Any] | None:
        with self._lock:
            self._verify_tenant()
            row = self._execute(
                self.connection,
                select(external_operation_journal).where(
                    external_operation_journal.c.operation_key == operation_key
                ),
            ).fetchone()
            if row is None:
                return None
            return {
                "operation_key": row["operation_key"],
                "request_digest": row["request_digest"],
                "operation": row["operation"],
                "repository_id": row["repository_id"],
                "status": row["status"],
                "expected_external_parent": row["expected_external_parent"],
                "external_operation_id": row["external_operation_id"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "error_code": row["error_code"],
            }

    def get_compensation_saga(self, saga_key: str, request_digest: str) -> dict[str, Any] | None:
        with self._lock:
            self._verify_tenant()
            row = self._execute(
                self.connection, select(compensation_sagas).where(compensation_sagas.c.saga_key == saga_key)
            ).fetchone()
            if row is None:
                return None
            if row["request_digest"] != request_digest:
                raise RuntimeError("IDEMPOTENCY_CONFLICT")
            return {
                "status": row["status"],
                "attempt": int(row["attempt"]),
                "receipt": json.loads(row["receipt_json"]),
            }

    def save_compensation_saga(
        self,
        connection: Connection,
        *,
        saga_key: str,
        request_digest: str,
        status: str,
        attempt: int,
        receipt: Mapping[str, Any],
    ) -> None:
        existing = self._execute(
            connection,
            select(compensation_sagas.c.request_digest).where(compensation_sagas.c.saga_key == saga_key),
        ).fetchone()
        if existing is not None and existing["request_digest"] != request_digest:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        self._execute(
            connection,
            self._insert(compensation_sagas)
            .values(
                saga_key=saga_key,
                request_digest=request_digest,
                status=status,
                attempt=attempt,
                receipt_json=canonical_json(dict(receipt)),
            )
            .on_conflict_do_update(
                index_elements=["workspace_id", "saga_key"],
                set_={
                    "status": status,
                    "attempt": attempt,
                    "receipt_json": canonical_json(dict(receipt)),
                },
            ),
        )

    @staticmethod
    def _page_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("STATE_STORE_PAGE_LIMIT_INVALID")

    @staticmethod
    def _batch_ids(identities: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        if (
            not isinstance(identities, (tuple, list))
            or len(identities) > 1000
            or any(not isinstance(identity, str) or not identity for identity in identities)
        ):
            raise ValueError("STATE_STORE_BATCH_IDS_INVALID")
        return tuple(dict.fromkeys(identities))

    @staticmethod
    def _event_envelope(row: Any) -> dict[str, Any]:
        item = {
            "sequence_no": int(row["sequence_no"]),
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "previous_digest": row["previous_digest"],
        }
        if sha256_digest(item) != row["event_digest"]:
            raise IntegrityError(f"event chain failed at sequence {item['sequence_no']}")
        return {**item, "event_digest": row["event_digest"]}

    def event_by_digest(self, digest: str) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            row = self._execute(
                connection, select(domain_events).where(domain_events.c.event_digest == digest)
            ).fetchone()
            return self._event_envelope(row) if row is not None else None

    def event_page(
        self,
        *,
        after: int = 0,
        limit: int = 100,
        event_types: tuple[str, ...] | None = None,
        subject_key: str | None = None,
        descending: bool = False,
    ) -> dict[str, Any]:
        """Read bounded envelopes; full chain verification is a separate operation."""
        self._page_limit(limit)
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("STATE_STORE_EVENT_CURSOR_INVALID")
        if event_types is not None and (
            not event_types
            or len(event_types) > 100
            or any(not isinstance(value, str) or not value for value in event_types)
        ):
            raise ValueError("STATE_STORE_EVENT_TYPES_INVALID")
        if subject_key is not None and (not isinstance(subject_key, str) or not subject_key):
            raise ValueError("STATE_STORE_EVENT_SUBJECT_INVALID")
        statement = (
            select(domain_events)
            .order_by(domain_events.c.sequence_no.desc() if descending else domain_events.c.sequence_no)
            .limit(limit + 1)
        )
        if not descending or after:
            statement = statement.where(
                domain_events.c.sequence_no < after if descending else domain_events.c.sequence_no > after
            )
        if event_types is not None:
            statement = statement.where(domain_events.c.event_type.in_(event_types))
        if subject_key is not None:
            statement = statement.where(domain_events.c.subject_key == subject_key)
        with self.read_connection() as connection:
            rows = self._execute(connection, statement).fetchall()
            items = tuple(self._event_envelope(row) for row in rows[:limit])
            return {"items": items, "next_cursor": items[-1]["sequence_no"] if len(rows) > limit else None}

    def history_projection(self, *, record_limit: int = 100) -> dict[str, Any]:
        """Read the fixed append projection and a checked tail in one database snapshot.

        This validates returned envelopes and their checkpoint binding. It does
        not re-audit historical rows outside the requested tail.
        """
        self._page_limit(record_limit)
        tail = (
            select(domain_events)
            .where(domain_events.c.workspace_id == self.workspace_id)
            .order_by(domain_events.c.sequence_no.desc())
            .limit(record_limit)
            .subquery()
        )
        statement = (
            select(workspace_registry, tail)
            .select_from(
                workspace_registry.outerjoin(tail, workspace_registry.c.workspace_id == tail.c.workspace_id)
            )
            .where(workspace_registry.c.workspace_id == self.workspace_id)
            .order_by(tail.c.sequence_no)
        )
        with self.read_connection() as connection:
            rows = self._execute(connection, statement).fetchall()
        if not rows:
            raise IntegrityError("STATE_STORE_WORKSPACE_NOT_REGISTERED")
        checkpoint = rows[0]
        count, head = int(checkpoint["audit_sequence"]), checkpoint["audit_head"]
        items = [self._event_envelope(row) for row in rows if row["sequence_no"] is not None]
        if (
            len(items) != min(count, record_limit)
            or (items and (items[-1]["sequence_no"] != count or items[-1]["event_digest"] != head))
            or (not items and head != EMPTY_EVENT_DIGEST)
        ):
            raise IntegrityError("STATE_STORE_AUDIT_HEAD_MISMATCH")
        if items and items[0]["sequence_no"] == 1 and items[0]["previous_digest"] != EMPTY_EVENT_DIGEST:
            raise IntegrityError("CHANGE_EVENT_JOURNAL_CHAIN_INVALID")
        for previous, current in pairwise(items):
            if (
                current["sequence_no"] != previous["sequence_no"] + 1
                or current["previous_digest"] != previous["event_digest"]
            ):
                raise IntegrityError("CHANGE_EVENT_JOURNAL_CHAIN_INVALID")
        scopes = json.loads(checkpoint["event_scopes_json"])
        from orgrebase.event_projection import validate_scopes

        validate_scopes(scopes, events=count, head_digest=head)
        return {
            "status": "PASS",
            "events": count,
            "head_digest": head,
            "verification": "PERSISTED_APPEND_PROJECTION",
            "record_limit": record_limit,
            "records": [{key: value for key, value in item.items() if key != "payload"} for item in items],
            "scope_projection": scopes,
            "change_count": int(checkpoint["change_count"]),
            "pending_change_count": int(checkpoint["pending_change_count"]),
        }

    def artifact_page(
        self,
        *,
        artifact_id_prefix: str,
        after: str | None = None,
        limit: int = 100,
        expected_media_type: str | None = None,
        descending: bool = False,
    ) -> dict[str, Any]:
        self._page_limit(limit)
        if not isinstance(descending, bool):
            raise ValueError("STATE_STORE_PAGE_ORDER_INVALID")
        if not isinstance(artifact_id_prefix, str) or not artifact_id_prefix:
            raise ValueError("artifact_id_prefix must be non-empty")
        if after is not None and (not isinstance(after, str) or not after.startswith(artifact_id_prefix)):
            raise ValueError("STATE_STORE_ARTIFACT_CURSOR_INVALID")
        statement = (
            select(artifacts)
            .where(
                artifacts.c.artifact_id.startswith(artifact_id_prefix, autoescape=True),
                func.substr(artifacts.c.artifact_id, 1, len(artifact_id_prefix)) == artifact_id_prefix,
            )
            .order_by(artifacts.c.artifact_id.desc() if descending else artifacts.c.artifact_id)
            .limit(limit + 1)
        )
        if after is not None:
            statement = statement.where(
                artifacts.c.artifact_id < after if descending else artifacts.c.artifact_id > after
            )
        with self._lock:
            self._verify_tenant()
            rows = self._execute(self.connection, statement).fetchall()
            items = tuple(self._row_to_artifact(row, expected_media_type) for row in rows[:limit])
            return {"items": items, "next_cursor": items[-1].artifact_id if len(rows) > limit else None}

    @staticmethod
    def _effect_record(row) -> dict[str, Any]:
        return {
            name: row[name]
            for name in (
                "effect_id",
                "workspace_id",
                "target_key",
                "request_digest",
                "state",
                "error_code",
                "created_at",
                "updated_at",
                "lease_owner",
                "lease_until",
                "fence",
                "requested_action",
                "command_revision",
                "requested_at",
            )
        } | {
            "request": json.loads(row["request_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] is not None else None,
        }

    def put_effect(
        self,
        connection: Connection,
        *,
        effect_id: str,
        target_key: str,
        request_digest: str,
        request: Mapping[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (effect_id, target_key, request_digest, created_at)
        ):
            raise ValueError("EFFECT_IDENTITY_INVALID")
        encoded = canonical_json(dict(request))
        self._execute(
            connection,
            self._insert(effect_intents)
            .values(
                effect_id=effect_id,
                workspace_id=self.workspace_id,
                target_key=target_key,
                request_digest=request_digest,
                request_json=encoded,
                state="READY",
                created_at=created_at,
                updated_at=created_at,
                fence=0,
            )
            .on_conflict_do_nothing(index_elements=["effect_id"]),
        )
        existing = self.get_effect(effect_id, connection=connection)
        if existing is None or (
            existing["target_key"],
            existing["request_digest"],
            canonical_json(existing["request"]),
        ) != (target_key, request_digest, encoded):
            raise IntegrityError("EFFECT_IDEMPOTENCY_CONFLICT")
        return existing

    def get_effect(self, effect_id: str, *, connection: Connection | None = None) -> dict[str, Any] | None:
        with self._lock:
            self._verify_tenant()
            statement = select(effect_intents).where(effect_intents.c.effect_id == effect_id)
            if connection is not None:
                statement = self._lock_row(statement)
            row = self._execute(connection or self.connection, statement).fetchone()
            if row is not None and row["workspace_id"] != self.workspace_id and not self._maintenance:
                raise IntegrityError("EFFECT_WORKSPACE_MISMATCH")
            return self._effect_record(row) if row is not None else None

    def get_effects(self, effect_ids: tuple[str, ...] | list[str]) -> dict[str, dict[str, Any]]:
        """Read a bounded set of effects without locking or claiming them; omit missing IDs."""
        identities = self._batch_ids(effect_ids)
        if not identities:
            return {}
        with self.read_connection() as connection:
            rows = self._execute(
                connection, select(effect_intents).where(effect_intents.c.effect_id.in_(identities))
            ).fetchall()
            if not self._maintenance and any(row["workspace_id"] != self.workspace_id for row in rows):
                raise IntegrityError("EFFECT_WORKSPACE_MISMATCH")
            return {row["effect_id"]: self._effect_record(row) for row in rows}

    def update_effect(
        self,
        connection: Connection,
        *,
        effect_id: str,
        expected_state: str | tuple[str, ...],
        state: str,
        updated_at: str,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        expected_fence: int | None = None,
    ) -> dict[str, Any]:
        states = (expected_state,) if isinstance(expected_state, str) else expected_state
        if not states:
            raise ValueError("EFFECT_EXPECTED_STATE_REQUIRED")
        statement = update(effect_intents).where(
            effect_intents.c.effect_id == effect_id,
            effect_intents.c.state.in_(states),
        )
        if expected_fence is not None:
            statement = statement.where(effect_intents.c.fence == expected_fence)
        terminal = (
            {"requested_action": None, "requested_at": None} if state in {"CONFIRMED", "REJECTED"} else {}
        )
        changed = self._execute(
            connection,
            statement.values(
                state=state,
                updated_at=updated_at,
                result_json=canonical_json(dict(result)) if result is not None else None,
                error_code=error_code,
                **terminal,
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("EFFECT_STATE_CONFLICT")
        record = self.get_effect(effect_id, connection=connection)
        if record is None:
            raise IntegrityError("EFFECT_RECORD_MISSING")
        return record

    def claim_effect(
        self,
        connection: Connection,
        *,
        effect_id: str,
        worker_id: str,
        now: float,
        lease_seconds: float,
        allowed_states: tuple[str, ...] = ("READY", "COMMIT_UNKNOWN"),
    ) -> dict[str, Any] | None:
        if (
            not isinstance(worker_id, str)
            or not worker_id.strip()
            or not math.isfinite(now)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
            or not math.isfinite(now + lease_seconds)
        ):
            raise ValueError("EFFECT_LEASE_INVALID")
        statement = select(effect_intents).where(
            effect_intents.c.effect_id == effect_id,
            effect_intents.c.state.in_(allowed_states),
            or_(effect_intents.c.lease_until.is_(None), effect_intents.c.lease_until <= now),
        )
        row = self._execute(connection, self._lock_row(statement, skip_locked=True)).fetchone()
        if row is None:
            return None
        self._execute(
            connection,
            update(effect_intents)
            .where(effect_intents.c.effect_id == effect_id)
            .values(
                lease_owner=worker_id,
                lease_until=now + lease_seconds,
                fence=effect_intents.c.fence + 1,
            ),
        )
        return self.get_effect(effect_id, connection=connection)

    def release_effect_claim(
        self,
        connection: Connection,
        *,
        effect_id: str,
        worker_id: str,
        fence: int,
    ) -> bool:
        return (
            self._execute(
                connection,
                update(effect_intents)
                .where(
                    effect_intents.c.effect_id == effect_id,
                    effect_intents.c.lease_owner == worker_id,
                    effect_intents.c.fence == fence,
                )
                .values(lease_owner=None, lease_until=None),
            ).rowcount
            == 1
        )

    def request_effect_action(
        self,
        connection: Connection,
        *,
        effect_id: str,
        request_digest: str,
        action: str,
        expected_state: str | tuple[str, ...],
    ) -> dict[str, Any]:
        current = self.get_effect(effect_id, connection=connection)
        states = (expected_state,) if isinstance(expected_state, str) else expected_state
        if current is None or current["request_digest"] != request_digest:
            raise IntegrityError("EFFECT_REQUEST_BINDING_MISMATCH")
        if current["state"] not in states:
            raise RuntimeError("EFFECT_STATE_CONFLICT")
        allowed = {"READY": {"EXECUTE"}, "COMMIT_UNKNOWN": {"QUERY", "CANCEL"}}
        if action not in allowed.get(current["state"], set()):
            raise IntegrityError("EFFECT_ACTION_NOT_ALLOWED")
        if current["requested_action"] is not None:
            if current["requested_action"] != action:
                raise RuntimeError("EFFECT_COMMAND_PENDING")
            return current
        changed = self._execute(
            connection,
            update(effect_intents)
            .where(
                effect_intents.c.effect_id == effect_id,
                effect_intents.c.request_digest == request_digest,
                effect_intents.c.state == current["state"],
                effect_intents.c.command_revision == current["command_revision"],
                effect_intents.c.requested_action.is_(None),
            )
            .values(
                requested_action=action,
                command_revision=effect_intents.c.command_revision + 1,
                requested_at=datetime.now(UTC).isoformat(),
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("EFFECT_COMMAND_CONFLICT")
        return self.get_effect(effect_id, connection=connection)

    def list_effects(
        self,
        *,
        states: tuple[str, ...] | None = None,
        after: str | None = None,
        limit: int = 100,
        all_workspaces: bool = False,
    ) -> dict[str, Any]:
        self._page_limit(limit)
        if all_workspaces and not self._maintenance:
            raise PermissionError("EFFECT_TENANT_OPERATOR_REQUIRED")
        if all_workspaces and self.backend == "postgresql" and self.runtime_role_safe():
            raise PermissionError("EFFECT_OPERATOR_DATABASE_ROLE_REQUIRED")
        statement = select(effect_intents).order_by(effect_intents.c.effect_id).limit(limit + 1)
        if not all_workspaces:
            statement = statement.where(effect_intents.c.workspace_id == self.workspace_id)
        if states is not None:
            statement = statement.where(effect_intents.c.state.in_(states))
        if after is not None:
            statement = statement.where(effect_intents.c.effect_id > after)
        with self.read_connection() as connection:
            rows = self._execute(connection, statement).fetchall()
        items = [self._effect_record(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["effect_id"] if len(rows) > limit else None}

    def pending_effects(
        self, *, limit: int = 100, all_workspaces: bool = False
    ) -> tuple[dict[str, Any], ...]:
        self._page_limit(limit)
        if all_workspaces and not self._maintenance:
            raise PermissionError("EFFECT_TENANT_OPERATOR_REQUIRED")
        if all_workspaces and self.backend == "postgresql" and self.runtime_role_safe():
            raise PermissionError("EFFECT_OPERATOR_DATABASE_ROLE_REQUIRED")
        statement = (
            select(effect_intents)
            .where(
                effect_intents.c.requested_action.is_not(None),
                effect_intents.c.state.in_(("READY", "DISPATCHING", "COMMIT_UNKNOWN")),
            )
            .order_by(effect_intents.c.requested_at, effect_intents.c.effect_id)
            .limit(limit)
        )
        if not all_workspaces:
            statement = statement.where(effect_intents.c.workspace_id == self.workspace_id)
        with self.read_connection() as connection:
            rows = self._execute(connection, statement).fetchall()
        return tuple(self._effect_record(row) for row in rows)

    def clear_effect_action(
        self, connection: Connection, *, effect_id: str, expected_command_revision: int
    ) -> bool:
        if (
            isinstance(expected_command_revision, bool)
            or not isinstance(expected_command_revision, int)
            or expected_command_revision < 1
        ):
            raise ValueError("EFFECT_COMMAND_REVISION_INVALID")
        return (
            self._execute(
                connection,
                update(effect_intents)
                .where(
                    effect_intents.c.effect_id == effect_id,
                    effect_intents.c.command_revision == expected_command_revision,
                )
                .values(requested_action=None, requested_at=None),
            ).rowcount
            == 1
        )

    def acquire_target_barrier(self, connection: Connection, *, target_key: str, effect_id: str) -> bool:
        effect = self.get_effect(effect_id, connection=connection)
        if effect is None or effect["target_key"] != target_key:
            raise IntegrityError("TARGET_BARRIER_EFFECT_MISMATCH")
        self._execute(
            connection,
            self._insert(target_barriers)
            .values(
                target_key=target_key,
                effect_id=effect_id,
            )
            .on_conflict_do_nothing(index_elements=["target_key"]),
        )
        return self.get_target_barrier(target_key, connection=connection) == effect_id

    def get_target_barrier(self, target_key: str, *, connection: Connection | None = None) -> str | None:
        with self._lock:
            self._verify_tenant()
            statement = select(target_barriers.c.effect_id).where(target_barriers.c.target_key == target_key)
            row = self._execute(connection or self.connection, statement).fetchone()
            return row[0] if row is not None else None

    def release_target_barrier(self, connection: Connection, *, target_key: str, effect_id: str) -> bool:
        effect = self.get_effect(effect_id, connection=connection)
        if effect is None or effect["target_key"] != target_key:
            raise IntegrityError("TARGET_BARRIER_EFFECT_MISMATCH")
        if effect["state"] not in ("CONFIRMED", "REJECTED"):
            raise RuntimeError("TARGET_BARRIER_EFFECT_UNRESOLVED")
        return (
            self._execute(
                connection,
                delete(target_barriers).where(
                    target_barriers.c.target_key == target_key,
                    target_barriers.c.effect_id == effect_id,
                ),
            ).rowcount
            == 1
        )

    def get_source_checkpoint(
        self, connector_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            self._verify_tenant()
            statement = select(source_checkpoints).where(source_checkpoints.c.connector_id == connector_id)
            if connection is not None:
                statement = self._lock_row(statement)
            row = self._execute(connection or self.connection, statement).fetchone()
            return dict(row) if row is not None else None

    def claim_source(
        self,
        connection: Connection,
        *,
        connector_id: str,
        worker_id: str,
        now: float,
        lease_seconds: float,
    ) -> dict[str, Any] | None:
        if (
            not isinstance(connector_id, str)
            or not connector_id.strip()
            or not isinstance(worker_id, str)
            or not worker_id.strip()
            or not math.isfinite(now)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
            or not math.isfinite(now + lease_seconds)
        ):
            raise ValueError("SOURCE_LEASE_INVALID")
        self._execute(
            connection,
            self._insert(source_checkpoints)
            .values(
                connector_id=connector_id,
                revision=0,
                fence=0,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "connector_id"]),
        )
        row = self._execute(
            connection,
            self._lock_row(
                select(source_checkpoints).where(
                    source_checkpoints.c.connector_id == connector_id,
                    or_(source_checkpoints.c.lease_until.is_(None), source_checkpoints.c.lease_until <= now),
                ),
                skip_locked=True,
            ),
        ).fetchone()
        if row is None:
            return None
        self._execute(
            connection,
            update(source_checkpoints)
            .where(source_checkpoints.c.connector_id == connector_id)
            .values(
                lease_owner=worker_id,
                lease_until=now + lease_seconds,
                fence=source_checkpoints.c.fence + 1,
            ),
        )
        return self.get_source_checkpoint(connector_id, connection=connection)

    def commit_source_page(
        self,
        connection: Connection,
        *,
        connector_id: str,
        expected_cursor: str | None,
        cursor: str | None,
        worker_id: str,
        fence: int,
        now: float,
    ) -> dict[str, Any]:
        if not math.isfinite(now) or (cursor is not None and not isinstance(cursor, str)):
            raise ValueError("SOURCE_CHECKPOINT_INVALID")
        changed = self._execute(
            connection,
            update(source_checkpoints)
            .where(
                source_checkpoints.c.connector_id == connector_id,
                source_checkpoints.c.cursor == expected_cursor,
                source_checkpoints.c.lease_owner == worker_id,
                source_checkpoints.c.fence == fence,
                source_checkpoints.c.lease_until > now,
            )
            .values(
                cursor=cursor, revision=source_checkpoints.c.revision + 1, lease_owner=None, lease_until=None
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("SOURCE_CHECKPOINT_CONFLICT")
        record = self.get_source_checkpoint(connector_id, connection=connection)
        if record is None:
            raise IntegrityError("SOURCE_CHECKPOINT_MISSING")
        return record

    def release_source_claim(
        self, connection: Connection, *, connector_id: str, worker_id: str, fence: int
    ) -> bool:
        return (
            self._execute(
                connection,
                update(source_checkpoints)
                .where(
                    source_checkpoints.c.connector_id == connector_id,
                    source_checkpoints.c.lease_owner == worker_id,
                    source_checkpoints.c.fence == fence,
                )
                .values(lease_owner=None, lease_until=None),
            ).rowcount
            == 1
        )
