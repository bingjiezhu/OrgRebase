from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from test_postgres_store import postgres_cluster as postgres_cluster
from test_postgres_store import postgres_dsn as postgres_dsn

from orgrebase.clock import FrozenClock
from orgrebase.domain import IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.store_operations import (
    StoreOperationError,
    _client_environment,
    backup_postgres,
    database_dsn,
    main,
    qualify_postgres,
    restore_postgres,
)


def test_real_postgres_dump_restore_remains_isolated_and_reapplies_deletion(
    postgres_dsn: str,
    tmp_path: Path,
) -> None:
    clock = FrozenClock("2026-09-09T00:00:00Z")
    backup = tmp_path / "backup"
    with StateStore(postgres_dsn, tenant_id="org:test") as source:
        private = PrivateRecordStore(source, clock)
        with source.transaction() as connection:
            source.save_artifact(connection, "artifact:business", "application/json", {"amount": "10.00"})
            source.append_event(connection, "BUSINESS_RECORD_CREATED", {"artifact": "artifact:business"})
            private.write(
                connection,
                record_id="private:one",
                scope_ref="run:one",
                owner_id="person:one",
                payload={"text": "sensitive original"},
            )
            source.put_effect(
                connection,
                effect_id="effect:unknown",
                target_key="quote:one",
                request_digest="digest",
                request={},
                created_at=clock.now(),
            )
            source.acquire_target_barrier(connection, target_key="quote:one", effect_id="effect:unknown")
            source.update_effect(
                connection,
                effect_id="effect:unknown",
                expected_state="READY",
                state="DISPATCHING",
                updated_at=clock.now(),
            )
        manifest = backup_postgres(postgres_dsn, tenant_id="org:test", output=backup, clock=clock)
        assert manifest["workspaces"][0]["event_chain"]["events"] == 1
        assert manifest["unresolved_effects"][0]["effect_id"] == "effect:unknown"
        assert (backup / "database.dump").stat().st_mode & 0o077 == 0
        assert (backup / "manifest.json").stat().st_mode & 0o077 == 0
        assert private.erase("private:one", actor_id="person:one")
        with source.transaction() as connection:
            private.write(
                connection,
                record_id="private:after-backup",
                scope_ref="run:later",
                owner_id="person:one",
                payload={"text": "created after backup"},
            )
        assert private.erase("private:after-backup", actor_id="person:one")
        latest_ledger = private.deletion_ledger()
    result = restore_postgres(
        postgres_dsn, tenant_id="org:test", backup=backup, latest_deletion_ledger=latest_ledger, clock=clock
    )
    name = result["database_name"]
    restored = database_dsn(postgres_dsn, name)
    try:
        assert result["status"] == "ISOLATED_RESTORE_VERIFIED"
        assert result["reapplied_deletions"] == 2
        assert result["writes_released"] is False
        assert result["database"]["recovery_required"] is True
        assert result["external_effect_inventory"] == "NOT_VERIFIED"
        assert result["target_barriers"] == [{"target_key": "quote:one", "effect_id": "effect:unknown"}]
        with pytest.raises(IntegrityError, match="STATE_STORE_RECOVERY_QUALIFICATION_REQUIRED"):
            StateStore(restored, tenant_id="org:test")
        with StateStore(restored, tenant_id="org:test", maintenance=True) as maintenance:
            assert maintenance.load_artifact("artifact:business").payload == {"amount": "10.00"}
            assert PrivateRecordStore(maintenance, clock).read("private:one") is None
            private = PrivateRecordStore(maintenance, clock)
            with maintenance.transaction() as connection:
                private.write(
                    connection,
                    record_id="private:after-backup",
                    scope_ref="run:later",
                    owner_id="person:one",
                    payload={"text": "created after backup"},
                )
            assert private.read("private:after-backup") is None
            assert maintenance.get_effect("effect:unknown")["state"] == "DISPATCHING"
        with psycopg.connect(postgres_dsn, autocommit=True) as admin:
            public_grants = admin.execute(
                "SELECT 1 FROM pg_database d, aclexplode(d.datacl) a WHERE d.datname=%s AND a.grantee=0",
                (name,),
            ).fetchall()
            assert public_grants == []
        assert qualify_postgres(restored, tenant_id="org:test")["writes_released"] is False
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_backup_corruption_and_existing_target_cannot_be_restored(postgres_dsn: str, tmp_path: Path) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        store.record_event("BACKUP", {})
    backup = tmp_path / "backup"
    backup_postgres(postgres_dsn, tenant_id="org:test", output=backup)
    existing = psycopg.conninfo.conninfo_to_dict(postgres_dsn)["dbname"]
    with pytest.raises(StoreOperationError, match="RESTORE_DATABASE_ALREADY_EXISTS"):
        restore_postgres(
            postgres_dsn,
            tenant_id="org:test",
            backup=backup,
            latest_deletion_ledger=[],
            new_database=existing,
        )
    with (backup / "database.dump").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(IntegrityError, match="BACKUP_DUMP_DIGEST_MISMATCH"):
        restore_postgres(postgres_dsn, tenant_id="org:test", backup=backup, latest_deletion_ledger=[])


def test_qualification_command_uses_named_environment_without_disclosing_dsn(
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        store.record_event("QUALIFY", {})
    monkeypatch.setenv("TEST_OPERATOR_DATABASE", postgres_dsn)
    assert main(["qualify", "--database-env", "TEST_OPERATOR_DATABASE", "--tenant", "org:test"]) == 0
    output = capsys.readouterr().out
    assert postgres_dsn not in output
    assert json.loads(output)["workspaces"][0]["event_chain"]["events"] == 1


def test_operator_qualification_does_not_initialize_unknown_database(postgres_dsn: str) -> None:
    with pytest.raises(StoreOperationError, match="STATE_STORE_NOT_INITIALIZED"):
        qualify_postgres(postgres_dsn, tenant_id="org:test")
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        assert connection.execute("SELECT to_regclass('store_metadata')").fetchone()[0] is None


def test_native_database_client_does_not_inherit_unrelated_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "canary-model-secret")
    monkeypatch.setenv("TARGET_WRITE_TOKEN", "canary-target-secret")
    monkeypatch.setenv("PGPASSWORD", "unrelated-parent-database-secret")
    monkeypatch.setenv("PGDATABASE", "unrelated-parent-database")
    monkeypatch.setenv("PGOPTIONS", "-c search_path=wrong")
    monkeypatch.setenv("PGPASSFILE", "/private/operator/pgpass")
    environment = _client_environment("postgresql://operator:approved-password@localhost/approved")
    assert "OPENAI_API_KEY" not in environment and "TARGET_WRITE_TOKEN" not in environment
    assert "PGOPTIONS" not in environment
    assert environment["PGDATABASE"] == "approved"
    assert environment["PGPASSWORD"] == "approved-password"
    assert environment["PGPASSFILE"] == "/private/operator/pgpass"


def test_real_postgres_multiworkspace_restore_reapplies_scope_deletions_and_invalidates_sessions(
    postgres_dsn, tmp_path
):
    from orgrebase.store_operations import export_deletion_ledger

    clock = FrozenClock("2026-09-09T00:00:00Z")
    profile = "sha256:" + "1" * 64
    with StateStore(postgres_dsn, tenant_id="org:test") as catalog:
        catalog.bind_workspace(profile_digest=profile, pack_digest=None, quote_object_id="quote:a")
        catalog.register_workspace(
            "quote-b",
            profile_digest=profile,
            pack_digest=None,
            quote_object_id="quote:b",
            created_at=clock.now(),
        )
        with catalog.transaction() as connection:
            connection.execute(
                "INSERT INTO browser_sessions(session_id,issuer,subject,payload_ciphertext,created_at,expires_at) VALUES('session','https://id.example','person','encrypted',1,9999999999)"
            )
            connection.execute(
                "INSERT INTO oidc_login_transactions VALUES('login','browser','encrypted',1,9999999999)"
            )
            connection.execute(
                "INSERT INTO browser_session_revocations VALUES('fence','SUBJECT',1,9999999999)"
            )
    for workspace in ("default", "quote-b"):
        with StateStore(postgres_dsn, tenant_id="org:test", workspace_id=workspace, migrate=False) as store:
            with store.transaction() as connection:
                store.save_artifact(connection, "same-id", "application/json", {"scope": workspace})
                store.append_event(connection, "CREATED", {"scope": workspace})
                PrivateRecordStore(store, clock).write(
                    connection,
                    record_id="same-private",
                    scope_ref="run",
                    owner_id="person",
                    payload={"scope": workspace},
                )
            if workspace == "quote-b":
                with store.transaction() as connection:
                    store.put_effect(
                        connection,
                        effect_id="unresolved-b",
                        target_key="shared-target",
                        request_digest="r",
                        request={},
                        created_at=clock.now(),
                    )
                    store.acquire_target_barrier(
                        connection, target_key="shared-target", effect_id="unresolved-b"
                    )
                    store.update_effect(
                        connection,
                        effect_id="unresolved-b",
                        expected_state="READY",
                        state="COMMIT_UNKNOWN",
                        updated_at=clock.now(),
                    )
    backup = tmp_path / "multi-backup"
    manifest = backup_postgres(postgres_dsn, tenant_id="org:test", output=backup, clock=clock)
    assert {row["workspace"]["workspace_id"] for row in manifest["workspaces"]} == {"default", "quote-b"}
    assert manifest["unresolved_effects"][0]["workspace_id"] == "quote-b"
    with StateStore(postgres_dsn, tenant_id="org:test", workspace_id="quote-b", migrate=False) as scoped:
        assert PrivateRecordStore(scoped, clock).erase("same-private", actor_id="person")
    current = export_deletion_ledger(postgres_dsn, tenant_id="org:test", clock=clock)
    restored_name = "restore_multiscope_test"
    restored_dsn = database_dsn(postgres_dsn, restored_name)
    try:
        result = restore_postgres(
            postgres_dsn,
            tenant_id="org:test",
            backup=backup,
            latest_deletion_ledger=current,
            new_database=restored_name,
            clock=clock,
        )
        assert result["invalidated_browser_sessions"] == result["invalidated_login_transactions"] == 1
        assert result["authentication_recovery"] == "FRESH_LOGIN_REQUIRED"
        assert result["reapplied_deletions"] == 1 and result["writes_released"] is False
        for workspace in ("default", "quote-b"):
            with StateStore(
                restored_dsn, tenant_id="org:test", workspace_id=workspace, maintenance=True, migrate=False
            ) as scoped:
                assert scoped.load_artifact("same-id").payload == {"scope": workspace}
                record = PrivateRecordStore(scoped, clock).read("same-private")
                assert (record is None) == (workspace == "quote-b")
                assert scoped.get_target_barrier("shared-target") == "unresolved-b"
                assert scoped.connection.execute("SELECT count(*) FROM browser_sessions").fetchone()[0] == 0
                assert (
                    scoped.connection.execute("SELECT count(*) FROM oidc_login_transactions").fetchone()[0]
                    == 0
                )
                assert (
                    scoped.connection.execute("SELECT count(*) FROM browser_session_revocations").fetchone()[
                        0
                    ]
                    == 1
                )
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(restored_name))
            )


def test_multiworkspace_deletion_ledger_cannot_omit_or_relabel_scope():
    from orgrebase.store_operations import _current_ledgers

    payload = {
        "schema_version": "orgrebase.private-deletions.v2",
        "tenant_id": "org:test",
        "workspaces": [{"workspace_id": "default", "records": []}],
    }
    with pytest.raises(IntegrityError, match="WORKSPACE_SET_MISMATCH"):
        _current_ledgers(payload, tenant_id="org:test", workspace_ids={"default", "quote-b"})
    with pytest.raises(ValueError, match="CURRENT_DELETION_LEDGER_REQUIRED"):
        _current_ledgers([], tenant_id="org:test", workspace_ids={"default", "quote-b"})
    with pytest.raises(ValueError, match="CURRENT_DELETION_LEDGER_REQUIRED"):
        _current_ledgers(payload, tenant_id="org:other", workspace_ids={"default"})


def test_released_v1_backup_with_schema2_restores_only_into_isolation(postgres_dsn, tmp_path):
    from orgrebase.store_operations import _client, _file_digest, _write_json
    from tests.test_workspace_migrations import _create_v2

    backup = tmp_path / "released-backup"
    backup.mkdir(mode=0o700)
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        head = _create_v2(connection)
        counts = {
            name: connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))
            ).fetchone()[0]
            for name in ("artifacts", "object_versions", "current_pointers", "domain_events")
        }
    _client(
        "pg_dump",
        ["--format=custom", "--no-owner", "--no-privileges", "--file", str(backup / "database.dump")],
        postgres_dsn,
    )
    _write_json(backup / "deletion-ledger.json", [])
    _write_json(
        backup / "manifest.json",
        {
            "schema_version": "orgrebase.postgres-backup.v1",
            "tenant_id": "org:legacy",
            "state_store_schema_version": 2,
            "created_at": "2026-09-09T00:00:00Z",
            "event_chain": {"status": "PASS", "events": 3, "head_digest": head},
            "record_counts": counts,
            "unresolved_effects": [
                {
                    "effect_id": "e",
                    "target_key": "target",
                    "state": "COMMIT_UNKNOWN",
                    "request_digest": "r",
                    "fence": 2,
                }
            ],
            "target_barriers": [{"target_key": "target", "effect_id": "e"}],
            "source_checkpoints": [{"connector_id": "source", "cursor": "cursor", "revision": 3}],
            "dump_sha256": _file_digest(backup / "database.dump"),
            "deletion_ledger_sha256": _file_digest(backup / "deletion-ledger.json"),
        },
    )
    name = "released_restore_test"
    try:
        result = restore_postgres(
            postgres_dsn,
            tenant_id="org:legacy",
            backup=backup,
            latest_deletion_ledger=[],
            new_database=name,
            clock=FrozenClock("2026-09-09T00:00:00Z"),
        )
        assert result["status"] == "ISOLATED_RESTORE_VERIFIED"
        assert result["writes_released"] is False
        assert result["backup_workspace_heads"] == {"default": head}
        assert result["target_barriers"] == [{"target_key": "target", "effect_id": "e"}]
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.parametrize("existing_kind", ["directory", "file", "symlink"])
def test_backup_rejects_existing_output_without_modifying_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_kind: str,
) -> None:
    monkeypatch.setattr("orgrebase.store_operations._require_existing_store", lambda dsn: None)
    original = tmp_path / "original"
    original.write_bytes(b"existing backup")
    output = tmp_path / "backup"
    if existing_kind == "directory":
        output.mkdir()
        (output / "database.dump").write_bytes(b"existing dump")
    elif existing_kind == "file":
        output.write_bytes(b"existing file")
    else:
        output.symlink_to(original)
    with pytest.raises(StoreOperationError, match=r"^BACKUP_OUTPUT_ALREADY_EXISTS$"):
        backup_postgres("postgresql://unused", tenant_id="org:test", output=output)
    assert original.read_bytes() == b"existing backup"
    if existing_kind == "directory":
        assert list(output.iterdir()) == [output / "database.dump"]
        assert (output / "database.dump").read_bytes() == b"existing dump"
    elif existing_kind == "file":
        assert output.read_bytes() == b"existing file"
    else:
        assert output.is_symlink()


def test_backup_cli_reports_existing_output_without_disclosing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", "postgresql://secret:password@unused/db")
    monkeypatch.setattr("orgrebase.store_operations._require_existing_store", lambda dsn: None)
    assert main(["backup", "--tenant", "org:test", "--output", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    detail = json.loads(captured.err)
    assert detail["code"] == "BACKUP_OUTPUT_ALREADY_EXISTS"
    assert "Preserve" in detail["message"]
    assert not captured.out
    assert str(tmp_path) not in captured.err
    assert "password" not in captured.err
