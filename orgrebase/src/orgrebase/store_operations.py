"""Operator-only PostgreSQL backup and isolated restore qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import select

from orgrebase.clock import Clock, SystemClock
from orgrebase.database import (
    effect_intents,
    execute_core,
    metadata,
    postgres_row_factory,
    runtime_role_is_safe,
    source_checkpoints,
    target_barriers,
    workspace_registry,
)
from orgrebase.domain import IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.store_migrations import (
    STATE_STORE_SCHEMA_VERSION,
    prepare_legacy_restore_inspection,
    validate_state_store,
)


class StoreOperationError(RuntimeError):
    """An operator action was rejected or left an isolated restore to inspect."""


def database_dsn(dsn: str, database: str) -> str:
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("POSTGRES_URI_REQUIRED")
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if key != "dbname"])
    return urlunsplit(parsed._replace(path="/" + quote(database, safe=""), query=query))


def _client_environment(dsn: str) -> dict[str, str]:
    # Credentials stay in the child environment, never in command arguments or reports.
    fields = conninfo_to_dict(dsn)
    aliases = {
        "dbname": "PGDATABASE",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "application_name": "PGAPPNAME",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "target_session_attrs": "PGTARGETSESSIONATTRS",
        "channel_binding": "PGCHANNELBINDING",
    }
    supported = {
        "host",
        "hostaddr",
        "port",
        "dbname",
        "user",
        "password",
        "options",
        "application_name",
        "connect_timeout",
        "sslmode",
        "sslcert",
        "sslkey",
        "sslrootcert",
        "sslcrl",
        "sslcrldir",
        "gssencmode",
        "target_session_attrs",
        "channel_binding",
        "service",
        "passfile",
    }
    if set(fields) - supported:
        raise StoreOperationError("POSTGRES_CLIENT_OPTION_UNSUPPORTED")
    inherited = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PGSSLMODE",
        "PGSSLCERT",
        "PGSSLKEY",
        "PGSSLROOTCERT",
        "PGSSLCRL",
        "PGSSLCRLDIR",
        "PGCHANNELBINDING",
        "PGGSSENCMODE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGPASSFILE",
    }
    environment = {key: value for key, value in os.environ.items() if key in inherited}
    environment.update({aliases.get(key, "PG" + key.upper()): value for key, value in fields.items()})
    environment.setdefault("PGCONNECT_TIMEOUT", "10")
    return environment


def _client(name: str, arguments: list[str], dsn: str) -> None:
    executable = shutil.which(name)
    if executable is None:
        raise StoreOperationError(f"POSTGRES_CLIENT_REQUIRED:{name}")
    result = subprocess.run(
        [executable, *arguments],
        env=_client_environment(dsn),
        capture_output=True,
        text=True,
        timeout=600,
        umask=0o077,
    )
    if result.returncode:
        # Server error text can contain private SQL values or connection details.
        raise StoreOperationError(f"POSTGRES_CLIENT_FAILED:{name}:exit={result.returncode}")


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _require_existing_store(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
        if connection.execute("SELECT to_regclass('store_metadata')").fetchone()[0] is None:
            raise StoreOperationError("STATE_STORE_NOT_INITIALIZED")


def _recovery_records(store: StateStore) -> dict[str, Any]:
    with store.read_connection() as connection:
        effects = execute_core(
            connection,
            select(
                effect_intents.c.workspace_id,
                effect_intents.c.effect_id,
                effect_intents.c.target_key,
                effect_intents.c.state,
                effect_intents.c.request_digest,
                effect_intents.c.fence,
            )
            .where(
                effect_intents.c.workspace_id == store.workspace_id,
                effect_intents.c.state.in_(("DISPATCHING", "COMMIT_UNKNOWN")),
            )
            .order_by(effect_intents.c.effect_id),
        ).fetchall()
        barriers = execute_core(
            connection, select(target_barriers).order_by(target_barriers.c.target_key)
        ).fetchall()
        checkpoints = execute_core(
            connection,
            select(
                source_checkpoints.c.workspace_id,
                source_checkpoints.c.connector_id,
                source_checkpoints.c.cursor,
                source_checkpoints.c.revision,
            )
            .where(source_checkpoints.c.workspace_id == store.workspace_id)
            .order_by(source_checkpoints.c.workspace_id, source_checkpoints.c.connector_id),
        ).fetchall()
        return {
            "unresolved_effects": [dict(row) for row in effects],
            "target_barriers": [dict(row) for row in barriers],
            "source_checkpoints": [dict(row) for row in checkpoints],
        }


def _workspace_inventory(
    dsn: str, store: StateStore, snapshot: str, clock: Clock
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Read every registered workspace from the same exported MVCC snapshot."""
    rows = execute_core(
        store.connection, select(workspace_registry).order_by(workspace_registry.c.workspace_id)
    ).fetchall()
    inventory = []
    ledgers = []
    recovery = {"unresolved_effects": [], "target_barriers": [], "source_checkpoints": []}
    version = store.check_health()["schema_version"]
    historical_version = version if version in {3, 4} else None
    for row in rows:
        workspace_id = row["workspace_id"]
        with StateStore(
            dsn, tenant_id=store.tenant_id, workspace_id=workspace_id, maintenance=True, read_only=True,
            read_schema_version=historical_version,
        ) as scoped:
            scoped.connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                scoped.connection.execute(
                    sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(snapshot))
                )
                chain = scoped.verify_event_chain()
                if chain["events"] != row["audit_sequence"] or chain["head_digest"] != row["audit_head"]:
                    raise IntegrityError("STATE_STORE_AUDIT_HEAD_MISMATCH")
                inventory.append(
                    {"workspace": dict(row), "event_chain": chain, "record_counts": scoped.count_records()}
                )
                ledgers.append(
                    {
                        "workspace_id": workspace_id,
                        "records": PrivateRecordStore(scoped, clock).deletion_ledger(),
                    }
                )
                scoped_recovery = _recovery_records(scoped)
                recovery["unresolved_effects"].extend(scoped_recovery["unresolved_effects"])
                recovery["source_checkpoints"].extend(scoped_recovery["source_checkpoints"])
                recovery["target_barriers"] = scoped_recovery["target_barriers"]
            finally:
                scoped.connection.rollback()
    recovery["unresolved_effects"].sort(key=lambda item: item["effect_id"])
    return (
        inventory,
        {
            "schema_version": "orgrebase.private-deletions.v2",
            "tenant_id": store.tenant_id,
            "workspaces": ledgers,
        },
        recovery,
    )


def export_deletion_ledger(
    dsn: str, *, tenant_id: str, clock: Clock | None = None, read_schema_version: int | None = None
) -> dict[str, Any]:
    """Export the current deletion authority for all registered workspaces."""
    with StateStore(
        dsn, tenant_id=tenant_id, maintenance=True, read_only=True, read_schema_version=read_schema_version
    ) as store:
        store.connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            snapshot = store.connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
            return _workspace_inventory(dsn, store, snapshot, clock or SystemClock())[1]
        finally:
            store.connection.rollback()


def backup_postgres(
    dsn: str, *, tenant_id: str, output: Path, clock: Clock | None = None,
    read_schema_version: int | None = None,
) -> dict[str, Any]:
    """Export the workspace manifests and dump from one PostgreSQL snapshot."""
    _require_existing_store(dsn)
    selected_clock = clock or SystemClock()
    try:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise StoreOperationError("BACKUP_OUTPUT_ALREADY_EXISTS") from exc
    dump = output / "database.dump"
    with StateStore(
        dsn, tenant_id=tenant_id, maintenance=True, read_only=True, read_schema_version=read_schema_version
    ) as store:
        store.connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            snapshot = store.connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
            workspaces, deletions, recovery = _workspace_inventory(dsn, store, snapshot, selected_clock)
            manifest = {
                "schema_version": "orgrebase.postgres-backup.v2",
                "tenant_id": tenant_id,
                "state_store_schema_version": store.check_health()["schema_version"],
                "created_at": selected_clock.now(),
                "workspaces": workspaces,
                **recovery,
            }
            _client(
                "pg_dump",
                [
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--snapshot",
                    snapshot,
                    "--file",
                    str(dump),
                ],
                dsn,
            )
        finally:
            store.connection.rollback()
    manifest["dump_sha256"] = _file_digest(dump)
    _write_json(output / "deletion-ledger.json", deletions)
    manifest["deletion_ledger_sha256"] = _file_digest(output / "deletion-ledger.json")
    _write_json(output / "manifest.json", manifest)
    return manifest


def migrate_postgres(dsn: str, *, tenant_id: str, runtime_role: str | None = None) -> dict[str, Any]:
    """Provision the schema with operator authority before runtime connections open."""
    # Commit quarantine separately: a rejected remap must not reopen the old writers.
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
        if connection.execute("SELECT to_regclass('store_metadata')").fetchone()[0] is not None:
            row = connection.execute("SELECT tenant_id,schema_version FROM store_metadata WHERE singleton=1").fetchone()
            if row is None or row[0] not in (None, tenant_id):
                raise IntegrityError("STATE_STORE_TENANT_MISMATCH")
            if row[1] in (2, 3):
                validate_state_store(connection, version=row[1])
                role = connection.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                if not role or not role[0]:
                    raise IntegrityError("STATE_STORE_MIGRATION_REQUIRES_RLS_BYPASS")
                if connection.execute("SELECT 1 FROM effect_intents LIMIT 1").fetchone() is not None:
                    connection.execute("UPDATE store_metadata SET recovery_required=1 WHERE singleton=1")
    with StateStore(dsn, tenant_id=tenant_id, maintenance=True, migrate=True) as store:
        if runtime_role is not None:
            with store.transaction() as connection:
                if not runtime_role_is_safe(connection, runtime_role):
                    raise StoreOperationError("RUNTIME_ROLE_UNSAFE")
                schema = connection.execute("SELECT current_schema()").fetchone()[0]
                schema_id = sql.Identifier(schema)
                role_id = sql.Identifier(runtime_role)
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema_id, role_id))
                for table in metadata.sorted_tables:
                    privileges = (
                        sql.SQL("SELECT")
                        if table.name == "store_metadata"
                        else sql.SQL("SELECT,INSERT,UPDATE,DELETE")
                    )
                    connection.execute(
                        sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                            privileges, schema_id, sql.Identifier(table.name), role_id
                        )
                    )
        return {
            "status": "SCHEMA_MIGRATED",
            "tenant_id": tenant_id,
            "schema_version": STATE_STORE_SCHEMA_VERSION,
            "runtime_role": runtime_role,
            "runtime_role_required": "NOSUPERUSER_NOBYPASSRLS",
            "workspace_isolation": "FORCE_ROW_LEVEL_SECURITY",
            "writes_released": False,
        }


def qualify_postgres(dsn: str, *, tenant_id: str, read_schema_version: int | None = None) -> dict[str, Any]:
    _require_existing_store(dsn)
    with StateStore(
        dsn, tenant_id=tenant_id, maintenance=True, read_only=True, read_schema_version=read_schema_version
    ) as store:
        store.connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            snapshot = store.connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
            workspaces, _, recovery = _workspace_inventory(dsn, store, snapshot, SystemClock())
            return {
                "status": "LOCAL_PERSISTENCE_VERIFIED",
                "database": store.check_health(),
                "workspaces": workspaces,
                **recovery,
                "external_effect_inventory": "NOT_VERIFIED",
                "current_identity_authority": "NOT_VERIFIED",
                "writes_released": False,
            }
        finally:
            store.connection.rollback()


def _current_ledgers(
    payload: Any, *, tenant_id: str, workspace_ids: set[str]
) -> dict[str, list[dict[str, str]]]:
    # Released single-workspace ledgers remain importable only for that scope.
    if isinstance(payload, list) and workspace_ids == {"default"}:
        return {"default": payload}
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "tenant_id", "workspaces"}
        or payload["schema_version"] != "orgrebase.private-deletions.v2"
        or payload["tenant_id"] != tenant_id
        or not isinstance(payload["workspaces"], list)
    ):
        raise ValueError("CURRENT_DELETION_LEDGER_REQUIRED")
    result = {}
    for item in payload["workspaces"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"workspace_id", "records"}
            or not isinstance(item["workspace_id"], str)
            or not isinstance(item["records"], list)
            or item["workspace_id"] in result
        ):
            raise ValueError("CURRENT_DELETION_LEDGER_INVALID")
        result[item["workspace_id"]] = item["records"]
    if set(result) != workspace_ids:
        raise IntegrityError("RESTORE_DELETION_WORKSPACE_SET_MISMATCH")
    return result


def _verify_restored_snapshot(manifest: dict[str, Any], observed: dict[str, Any]) -> None:
    """Check the backup's own identities before applying a derived-key migration."""
    workspaces = observed["workspaces"]
    if manifest["schema_version"] == "orgrebase.postgres-backup.v2":
        if workspaces != manifest.get("workspaces"):
            raise IntegrityError("RESTORE_SNAPSHOT_MISMATCH")
    elif (
        len(workspaces) != 1
        or workspaces[0]["workspace"]["workspace_id"] != "default"
        or workspaces[0]["event_chain"] != manifest.get("event_chain")
        or workspaces[0]["record_counts"] != manifest.get("record_counts")
    ):
        raise IntegrityError("RESTORE_SNAPSHOT_MISMATCH")
    for key in ("unresolved_effects", "target_barriers", "source_checkpoints"):
        records = observed[key]
        if manifest["schema_version"] == "orgrebase.postgres-backup.v1":
            records = [{name: value for name, value in item.items() if name != "workspace_id"} for item in records]
        if records != manifest.get(key):
            raise IntegrityError("RESTORE_EFFECT_OR_SOURCE_SNAPSHOT_MISMATCH")


def restore_postgres(
    admin_dsn: str,
    *,
    tenant_id: str,
    backup: Path,
    latest_deletion_ledger: dict[str, Any] | list[dict[str, str]],
    new_database: str | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Restore only into a new database; never release its application access."""

    selected_clock = clock or SystemClock()
    if not isinstance(latest_deletion_ledger, (list, dict)):
        raise ValueError("CURRENT_DELETION_LEDGER_REQUIRED")
    name = new_database or f"orgrebase_restore_{uuid4().hex[:16]}"
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise ValueError("RESTORE_DATABASE_NAME_INVALID")
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema_version"), manifest.get("state_store_schema_version")) not in {
        ("orgrebase.postgres-backup.v1", 2),
        ("orgrebase.postgres-backup.v1", 3),
        ("orgrebase.postgres-backup.v1", 4),
        ("orgrebase.postgres-backup.v1", STATE_STORE_SCHEMA_VERSION),
        ("orgrebase.postgres-backup.v2", 3),
        ("orgrebase.postgres-backup.v2", 4),
        ("orgrebase.postgres-backup.v2", STATE_STORE_SCHEMA_VERSION),
    } or manifest.get("tenant_id") != tenant_id:
        raise StoreOperationError("BACKUP_MANIFEST_BINDING_MISMATCH")
    if _file_digest(backup / "database.dump") != manifest.get("dump_sha256"):
        raise IntegrityError("BACKUP_DUMP_DIGEST_MISMATCH")
    if _file_digest(backup / "deletion-ledger.json") != manifest.get("deletion_ledger_sha256"):
        raise IntegrityError("BACKUP_DELETION_LEDGER_DIGEST_MISMATCH")
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=10) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            raise StoreOperationError("RESTORE_DATABASE_ALREADY_EXISTS")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
    restored_dsn = database_dsn(admin_dsn, name)
    _client(
        "pg_restore",
        [
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            name,
            str(backup / "database.dump"),
        ],
        restored_dsn,
    )
    with psycopg.connect(restored_dsn, autocommit=True, connect_timeout=10) as connection:
        connection.execute("UPDATE store_metadata SET recovery_required=1 WHERE singleton=1")
        restored_binding = connection.execute("SELECT tenant_id,schema_version FROM store_metadata WHERE singleton=1").fetchone()
        if restored_binding is None or restored_binding[0] != tenant_id:
            raise IntegrityError("STATE_STORE_TENANT_MISMATCH")
        restored_version = restored_binding[1]
    if restored_version != manifest["state_store_schema_version"]:
        raise IntegrityError("RESTORE_SCHEMA_VERSION_BINDING_MISMATCH")
    inspection_version = restored_version
    if restored_version == 2:
        with psycopg.connect(restored_dsn, autocommit=True, connect_timeout=10,
                             row_factory=postgres_row_factory) as connection, connection.transaction():
            prepare_legacy_restore_inspection(connection)
        inspection_version = 3
    original_snapshot = qualify_postgres(
        restored_dsn, tenant_id=tenant_id, read_schema_version=inspection_version if inspection_version in {3, 4} else None
    )
    _verify_restored_snapshot(manifest, original_snapshot)
    # Schema upgrades are operator-only and retain the original default event bytes.
    with (
        StateStore(restored_dsn, tenant_id=tenant_id, maintenance=True) as store,
        store.transaction() as connection,
    ):
        invalidated_sessions = connection.execute("DELETE FROM browser_sessions").rowcount
        invalidated_logins = connection.execute("DELETE FROM oidc_login_transactions").rowcount
    verified = qualify_postgres(restored_dsn, tenant_id=tenant_id)
    workspaces = verified["workspaces"]
    if workspaces != original_snapshot["workspaces"]:
        raise IntegrityError("RESTORE_CANONICAL_SNAPSHOT_CHANGED_DURING_MIGRATION")
    ledgers = _current_ledgers(
        latest_deletion_ledger,
        tenant_id=tenant_id,
        workspace_ids={item["workspace"]["workspace_id"] for item in workspaces},
    )
    erased = expired = 0
    for workspace_id, ledger in ledgers.items():
        with StateStore(
            restored_dsn, tenant_id=tenant_id, workspace_id=workspace_id, maintenance=True, migrate=False
        ) as store:
            private = PrivateRecordStore(store, selected_clock)
            erased += private.reapply_deletions(ledger)
            while count := private.purge_expired():
                expired += count
    result = qualify_postgres(restored_dsn, tenant_id=tenant_id)
    result.update(
        status="ISOLATED_RESTORE_VERIFIED",
        database_name=name,
        reapplied_deletions=erased,
        expired_private_records=expired,
        invalidated_browser_sessions=invalidated_sessions,
        invalidated_login_transactions=invalidated_logins,
        authentication_recovery="FRESH_LOGIN_REQUIRED",
        latest_deletion_ledger="OPERATOR_SUPPLIED_AND_REAPPLIED",
        backup_workspace_heads={
            item["workspace"]["workspace_id"]: item["event_chain"]["head_digest"] for item in workspaces
        },
        restored_schema_version=restored_version,
        derived_target_identity_migrated=restored_version < 4,
        original_effect_snapshot_verified=True,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "backup", "restore", "qualify", "deletion-ledger"))
    parser.add_argument("--database-env", default="ORGREBASE_WORKSPACE_DB")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--deletion-ledger", type=Path)
    parser.add_argument("--new-database")
    parser.add_argument("--runtime-role")
    parser.add_argument("--read-schema-version", type=int, choices=(3, 4))
    args = parser.parse_args(argv)
    dsn = os.environ.get(args.database_env, "")
    if not dsn:
        parser.error("the configured database environment variable is empty")
    if args.read_schema_version is not None and args.action not in ("qualify", "backup", "deletion-ledger"):
        parser.error("--read-schema-version is only available for read-only database operations")
    if args.action == "migrate":
        result = migrate_postgres(dsn, tenant_id=args.tenant, runtime_role=args.runtime_role)
    elif args.action == "deletion-ledger":
        if args.output is None:
            parser.error("deletion-ledger requires --output (a new private file)")
        _write_json(args.output, export_deletion_ledger(
            dsn, tenant_id=args.tenant, read_schema_version=args.read_schema_version
        ))
        result = {"status": "CURRENT_DELETION_LEDGER_EXPORTED", "tenant_id": args.tenant}
    elif args.action == "backup":
        if args.output is None:
            parser.error("backup requires --output (a new private directory)")
        try:
            result = backup_postgres(
                dsn, tenant_id=args.tenant, output=args.output, read_schema_version=args.read_schema_version
            )
        except StoreOperationError as exc:
            if str(exc) != "BACKUP_OUTPUT_ALREADY_EXISTS":
                raise
            print(json.dumps({
                "status": "REJECTED",
                "code": "BACKUP_OUTPUT_ALREADY_EXISTS",
                "message": "The output already exists. Preserve the existing backup and choose a new --output directory.",
            }), file=sys.stderr)
            return 2
    elif args.action == "restore":
        if args.backup is None or args.deletion_ledger is None:
            parser.error("restore requires --backup and a current --deletion-ledger")
        ledger = json.loads(args.deletion_ledger.read_text(encoding="utf-8"))
        result = restore_postgres(
            dsn,
            tenant_id=args.tenant,
            backup=args.backup,
            latest_deletion_ledger=ledger,
            new_database=args.new_database,
        )
    else:
        result = qualify_postgres(dsn, tenant_id=args.tenant, read_schema_version=args.read_schema_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
