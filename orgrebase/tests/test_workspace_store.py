from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import psycopg
import pytest

from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore

STAMP = "2026-09-09T00:00:00Z"
PROFILE = "sha256:" + "1" * 64


def _register(store: StateStore) -> None:
    store.bind_workspace(profile_digest=PROFILE, pack_digest=None, quote_object_id="quote:a")
    store.register_workspace(
        "quote-b", profile_digest=PROFILE, pack_digest=None, quote_object_id="quote:b", created_at=STAMP
    )


def test_real_postgres_workspace_rows_and_raw_sql_are_isolated(postgres_runtime) -> None:
    database = postgres_runtime(tenant_id="org:workspace-test")
    arguments = {"tenant_id": "org:workspace-test", "migrate": False}
    with StateStore(database["runtime_dsn"], **arguments) as first:
        _register(first)
        assert first.check_health()["runtime_role_safe"] is True
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as second:
            for store, value in ((first, "first"), (second, "second")):
                with store.transaction() as connection:
                    store.save_artifact(connection, "same-artifact", "application/json", {"value": value})
                    store.append_event(connection, "CREATED", {"value": value})
            assert first.load_artifact("same-artifact").payload == {"value": "first"}
            assert second.load_artifact("same-artifact").payload == {"value": "second"}
            assert first.audit_head()["sequence_no"] == second.audit_head()["sequence_no"] == 1
            assert first.audit_head()["head_digest"] != second.audit_head()["head_digest"]
            rows = first.connection.execute("SELECT workspace_id,payload_json FROM artifacts").fetchall()
            assert len(rows) == 1 and rows[0][0] == "default"
            assert (
                first.connection.execute("SELECT * FROM artifacts WHERE workspace_id='quote-b'").fetchall()
                == []
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                first.connection.execute("UPDATE artifacts SET workspace_id='quote-b'")
            assert first.load_artifact("same-artifact").payload == {"value": "first"}
    with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as reopened:
        assert reopened.verify_event_chain()["events"] == 1
        assert reopened.load_artifact("same-artifact").payload == {"value": "second"}
        reopened.connection.execute("SELECT set_config('orgrebase.workspace_id','default',false)")
        with pytest.raises(IntegrityError, match="WORKSPACE_BINDING_LOST"):
            reopened.load_artifact("same-artifact")


def test_real_postgres_workspace_append_heads_do_not_block_other_quote(postgres_runtime) -> None:
    database = postgres_runtime(tenant_id="org:parallel")
    arguments = {"tenant_id": "org:parallel", "migrate": False}
    with StateStore(database["runtime_dsn"], **arguments) as first:
        _register(first)
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as second:
            entered, release = Event(), Event()

            def hold_first():
                with first.transaction() as connection:
                    first.append_event(connection, "A", {})
                    entered.set()
                    assert release.wait(5)

            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(hold_first)
                try:
                    assert entered.wait(5)
                    other = pool.submit(second.record_event, "B", {})
                    other.result(timeout=2)
                finally:
                    release.set()
                future.result(timeout=2)
            assert first.verify_event_chain()["events"] == second.verify_event_chain()["events"] == 1


def test_real_postgres_target_barrier_is_shared_across_workspaces(postgres_runtime) -> None:
    database = postgres_runtime(tenant_id="org:effects")
    arguments = {"tenant_id": "org:effects", "migrate": False}
    with StateStore(database["runtime_dsn"], **arguments) as first:
        _register(first)
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as second:
            for store, effect in ((first, "effect-a"), (second, "effect-b")):
                with store.transaction() as connection:
                    store.put_effect(
                        connection,
                        effect_id=effect,
                        target_key="same-target",
                        request_digest=effect,
                        request={"effect": effect},
                        created_at=STAMP,
                    )
            with first.transaction() as connection:
                assert first.acquire_target_barrier(
                    connection, target_key="same-target", effect_id="effect-a"
                )
                first.update_effect(
                    connection,
                    effect_id="effect-a",
                    expected_state="READY",
                    state="COMMIT_UNKNOWN",
                    updated_at=STAMP,
                )
            with second.transaction() as connection:
                assert not second.acquire_target_barrier(
                    connection, target_key="same-target", effect_id="effect-b"
                )
            assert second.get_target_barrier("same-target") == "effect-a"
            assert second.get_effect("effect-a") is None
            assert (
                second.connection.execute("SELECT effect_id FROM effect_intents").fetchall()[0][0]
                == "effect-b"
            )
            assert (
                second.connection.execute(
                    "UPDATE effect_intents SET state='REJECTED' WHERE effect_id='effect-a'"
                ).rowcount
                == 0
            )
            with (
                pytest.raises(IntegrityError, match="EFFECT_IDEMPOTENCY_CONFLICT"),
                second.transaction() as connection,
            ):
                second.put_effect(
                    connection,
                    effect_id="effect-a",
                    target_key="same-target",
                    request_digest="effect-a",
                    request={"effect": "effect-a"},
                    created_at=STAMP,
                )


def test_sqlite_rejects_nondefault_workspace_at_connection_and_database() -> None:
    with pytest.raises(ValueError, match="POSTGRESQL_REQUIRED"):
        StateStore(workspace_id="other")
    with StateStore() as store, pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        store.connection.execute(
            "INSERT INTO artifacts(workspace_id,artifact_id,media_type,payload_json,payload_digest) VALUES('other','a','application/json','{}','x')"
        )


def test_real_postgres_effect_command_revision_and_scope(postgres_runtime) -> None:
    database = postgres_runtime(tenant_id="org:commands")
    kwargs = {"tenant_id": "org:commands", "migrate": False}
    with (
        StateStore(database["runtime_dsn"], **kwargs) as first,
        StateStore(database["runtime_dsn"], **kwargs) as worker,
    ):
        _register(first)
        with first.transaction() as connection:
            first.put_effect(
                connection,
                effect_id="effect",
                target_key="target",
                request_digest="request",
                request={},
                created_at=STAMP,
            )
            command = first.request_effect_action(
                connection,
                effect_id="effect",
                request_digest="request",
                action="EXECUTE",
                expected_state="READY",
            )
            assert command["command_revision"] == 1
            assert (
                first.request_effect_action(
                    connection,
                    effect_id="effect",
                    request_digest="request",
                    action="EXECUTE",
                    expected_state="READY",
                )["command_revision"]
                == 1
            )
        assert worker.pending_effects()[0]["effect_id"] == "effect"
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **kwargs) as other:
            assert other.pending_effects() == ()
            with pytest.raises(PermissionError, match="TENANT_OPERATOR_REQUIRED"):
                other.pending_effects(all_workspaces=True)
            with other.transaction() as connection:
                assert not other.clear_effect_action(
                    connection, effect_id="effect", expected_command_revision=1
                )
        with worker.transaction() as connection:
            worker.update_effect(
                connection,
                effect_id="effect",
                expected_state="READY",
                state="COMMIT_UNKNOWN",
                updated_at=STAMP,
            )
        assert worker.pending_effects()[0]["command_revision"] == 1
        with worker.transaction() as connection:
            assert worker.clear_effect_action(connection, effect_id="effect", expected_command_revision=1)
        assert worker.pending_effects() == ()
        with first.transaction() as connection:
            second = first.request_effect_action(
                connection,
                effect_id="effect",
                request_digest="request",
                action="QUERY",
                expected_state="COMMIT_UNKNOWN",
            )
            assert second["command_revision"] == 2
            assert not first.clear_effect_action(connection, effect_id="effect", expected_command_revision=1)
        with pytest.raises(RuntimeError, match="COMMAND_PENDING"), first.transaction() as connection:
            first.request_effect_action(
                connection,
                effect_id="effect",
                request_digest="request",
                action="CANCEL",
                expected_state="COMMIT_UNKNOWN",
            )
        with worker.transaction() as connection:
            worker.update_effect(
                connection,
                effect_id="effect",
                expected_state="COMMIT_UNKNOWN",
                state="CONFIRMED",
                updated_at=STAMP,
            )
        with pytest.raises(IntegrityError, match="ACTION_NOT_ALLOWED"), first.transaction() as connection:
            first.request_effect_action(
                connection,
                effect_id="effect",
                request_digest="request",
                action="EXECUTE",
                expected_state="CONFIRMED",
            )
        assert first.pending_effects() == ()


def test_real_postgres_catalog_head_and_binding_are_protected(postgres_runtime):
    database = postgres_runtime(tenant_id="org:catalog")
    with StateStore(database["runtime_dsn"], tenant_id="org:catalog", migrate=False) as store:
        _register(store)
        assert (
            store.connection.execute(
                "UPDATE workspace_registry SET audit_sequence=99 WHERE workspace_id='quote-b'"
            ).rowcount
            == 0
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="WORKSPACE_BINDING_IMMUTABLE"):
            store.connection.execute(
                "UPDATE workspace_registry SET quote_object_id='replacement' WHERE workspace_id='default'"
            )
        assert (
            store.connection.execute("DELETE FROM workspace_registry WHERE workspace_id='quote-b'").rowcount
            == 0
        )
        assert store.get_workspace("quote-b")["audit_sequence"] == 0
    with (
        StateStore(
            database["runtime_dsn"], tenant_id="org:catalog", maintenance=True, migrate=False
        ) as maintenance,
        pytest.raises(PermissionError, match="OPERATOR_DATABASE_ROLE_REQUIRED"),
    ):
        maintenance.pending_effects(all_workspaces=True)


def test_real_postgres_barrier_raw_mutation_requires_own_terminal_effect(postgres_runtime):
    database = postgres_runtime(tenant_id="org:barrier-policy")
    kwargs = {"tenant_id": "org:barrier-policy", "migrate": False}
    with StateStore(database["runtime_dsn"], **kwargs) as first:
        _register(first)
        with first.transaction() as connection:
            first.put_effect(
                connection,
                effect_id="own",
                target_key="target",
                request_digest="request",
                request={},
                created_at=STAMP,
            )
            assert first.acquire_target_barrier(connection, target_key="target", effect_id="own")
            first.update_effect(
                connection, effect_id="own", expected_state="READY", state="COMMIT_UNKNOWN", updated_at=STAMP
            )
        assert first.connection.execute("DELETE FROM target_barriers WHERE target_key='target'").rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            first.connection.execute(
                "INSERT INTO target_barriers(target_key,effect_id) VALUES('wrong-target','own')"
            )
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **kwargs) as other:
            assert other.get_target_barrier("target") == "own"
            assert other.connection.execute("DELETE FROM target_barriers").rowcount == 0
            assert other.connection.execute("UPDATE target_barriers SET target_key='changed'").rowcount == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                other.connection.execute(
                    "INSERT INTO target_barriers(target_key,effect_id) VALUES('target','own')"
                )
        with first.transaction() as connection:
            first.update_effect(
                connection,
                effect_id="own",
                expected_state="COMMIT_UNKNOWN",
                state="CONFIRMED",
                updated_at=STAMP,
            )
            assert first.release_target_barrier(connection, target_key="target", effect_id="own")
        assert first.get_target_barrier("target") is None


def test_artifact_keyset_pages_support_both_orders():
    with StateStore() as store:
        with store.transaction() as connection:
            for artifact_id in ("a:001", "a:002", "a:003", "aa:004"):
                store.save_artifact(connection, artifact_id, "application/json", {"id": artifact_id})
        descending = store.artifact_page(artifact_id_prefix="a:", descending=True, limit=2)
        assert [item.artifact_id for item in descending["items"]] == ["a:003", "a:002"]
        assert descending["next_cursor"] == "a:002"
        final = store.artifact_page(artifact_id_prefix="a:", descending=True, after="a:002", limit=2)
        assert [item.artifact_id for item in final["items"]] == ["a:001"]
        assert final["next_cursor"] is None
        assert [
            item.artifact_id for item in store.artifact_page(artifact_id_prefix="a:", limit=2)["items"]
        ] == ["a:001", "a:002"]


def test_full_audit_detects_valid_prefix_truncation():
    with StateStore() as store:
        store.record_event("FIRST", {})
        store.record_event("SECOND", {})
        store.connection.execute("DELETE FROM domain_events WHERE sequence_no=2")
        store.connection.commit()
        with pytest.raises(IntegrityError, match="AUDIT_HEAD_MISMATCH"):
            store.verify_event_chain()


def test_real_postgres_runtime_role_cannot_own_isolation_schema(postgres_runtime):
    from psycopg import sql

    database = postgres_runtime(tenant_id="org:role")
    with psycopg.connect(database["migration_dsn"], autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(sql.Identifier(database["role_name"]))
        )
    with StateStore(database["runtime_dsn"], tenant_id="org:role", migrate=False) as store:
        assert store.check_health()["runtime_role_safe"] is False
