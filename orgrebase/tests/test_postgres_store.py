from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import psycopg
import pytest
from psycopg import sql

from orgrebase.clock import FrozenClock
from orgrebase.database import in_transaction
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.fixture import EnterpriseFixture
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_dsn as postgres_dsn


def test_postgres_main_store_round_trip(postgres_dsn: str, fixture: EnterpriseFixture) -> None:
    with StateStore(postgres_dsn, tenant_id=fixture.organization_id) as store:
        store.load_fixture(fixture)
        with store.transaction() as connection:
            store.save_artifact(connection, "artifact:pg", "application/json", {"database": "postgres"})
            store.save_idempotent(connection, "key:pg", "request", {"saved": True})
        pointers = store.state_snapshot(
            [item.id for item in fixture.objects if item.state.value in {"CURRENT", "ACTIVE"}]
        )
        events = store.event_envelopes()
        assert store.check_health()["backend"] == "postgresql"
    with StateStore(postgres_dsn, tenant_id=fixture.organization_id) as reopened:
        assert reopened.event_envelopes() == events
        assert reopened.state_snapshot(list(pointers)) == pointers
        assert reopened.load_artifact("artifact:pg").payload == {"database": "postgres"}
        assert reopened.get_idempotent("key:pg", "request") == {"saved": True}
        assert reopened.verify_event_chain()["events"] == 1


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_postgres_cancellation_rolls_back(postgres_dsn: str, failure: type[BaseException]) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        with pytest.raises(failure), store.transaction() as connection:
            store.save_artifact(connection, "cancelled", "application/json", {})
            store.append_event(connection, "CANCELLED", {})
            raise failure("cancelled")
        assert not in_transaction(store.connection)
        assert not store.artifact_exists("cancelled")
        assert store.verify_event_chain()["events"] == 0
        store.record_event("RECOVERED", {})
        assert store.verify_event_chain()["events"] == 1


def test_postgres_private_erasure_race_preserves_one_tombstone_and_event(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FrozenClock("2026-09-09T00:00:00Z")
    with (
        StateStore(postgres_dsn, tenant_id="org:test") as first,
        StateStore(postgres_dsn, tenant_id="org:test") as second,
    ):
        records = [PrivateRecordStore(store, clock) for store in (first, second)]
        with first.transaction() as connection:
            records[0].write(
                connection,
                record_id="private:race",
                scope_ref="run:1",
                owner_id="actor:1",
                payload={"text": "private original"},
            )
        ready = Barrier(2)

        def delayed_read(original):
            def read(connection, record_id):
                row = original(connection, record_id)
                ready.wait(timeout=5)
                return row

            return read

        with monkeypatch.context() as patch:
            for repository in records:
                patch.setattr(repository, "_row", delayed_read(repository._row))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(repository.erase, "private:race", actor_id="actor:1")
                    for repository in records
                ]
                assert sorted(future.result(timeout=10) for future in futures) == [False, True]
        assert records[0].read("private:race") is None
        assert len(records[0].deletion_ledger()) == 1
        assert first.verify_event_chain()["events"] == 1


def test_postgres_read_only_store_never_initializes_an_empty_database(postgres_dsn: str) -> None:
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_INCOMPATIBLE"):
        StateStore(postgres_dsn, tenant_id="org:test", read_only=True)
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        assert (
            connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()
            == []
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_read_only_store_enforces_native_write_boundary_and_tenant(
    backend: str, postgres_dsn: str, tmp_path: Path
) -> None:
    path = postgres_dsn if backend == "postgresql" else tmp_path / "read-only.sqlite3"
    with StateStore(path, tenant_id="org:test") as original:
        original.record_event("ORIGINAL", {})
        original.connection.execute("UPDATE store_metadata SET recovery_required=1")
        original.connection.commit()
    with pytest.raises(IntegrityError, match="STATE_STORE_TENANT_MISMATCH"):
        StateStore(path, tenant_id="org:wrong", maintenance=True, read_only=True)
    with StateStore(path, tenant_id="org:test", maintenance=True, read_only=True) as inspected:
        assert inspected.check_health()["recovery_required"] is True
        assert inspected.verify_event_chain()["events"] == 1
        with pytest.raises(RuntimeError, match="STATE_STORE_READ_ONLY"):
            inspected.record_event("FORBIDDEN", {})
        error = psycopg.errors.ReadOnlySqlTransaction if backend == "postgresql" else sqlite3.OperationalError
        with pytest.raises(error):
            inspected.connection.execute("UPDATE store_metadata SET recovery_required=0")
        inspected.connection.rollback()
        assert inspected.check_health()["recovery_required"] is True
        assert inspected.verify_event_chain()["events"] == 1


def test_sqlite_read_only_open_does_not_create_or_migrate_files(tmp_path: Path) -> None:
    absent = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        StateStore(absent, read_only=True)
    assert not absent.exists()
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE legacy (value TEXT)")
        connection.execute("PRAGMA user_version=1")
    before = legacy.read_bytes()
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:1"):
        StateStore(legacy, read_only=True)
    assert legacy.read_bytes() == before


def test_postgres_commit_constraint_failure_rolls_back(postgres_dsn: str) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        with pytest.raises(psycopg.errors.ForeignKeyViolation), store.transaction() as connection:
            connection.execute("SET CONSTRAINTS ALL DEFERRED")
            connection.execute("INSERT INTO version_states(version_key,state) VALUES ('absent', 'CURRENT')")
            store.save_artifact(connection, "failed", "application/json", {})
            store.append_event(connection, "FAILED", {})
        assert not in_transaction(store.connection)
        assert not store.artifact_exists("failed")
        assert store.verify_event_chain()["events"] == 0
        store.record_event("RECOVERED", {})


def test_postgres_interrupted_migration_rolls_back_all_ddl(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orgrebase.store as store_module

    migrate = store_module.migrate_state_store

    def interrupted(connection) -> None:
        migrate(connection)
        raise KeyboardInterrupt("interrupt before migration commit")

    monkeypatch.setattr(store_module, "migrate_state_store", interrupted)
    with pytest.raises(KeyboardInterrupt):
        StateStore(postgres_dsn, tenant_id="org:test")
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        assert (
            connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()
            == []
        )


def test_postgres_fence_is_a_64_bit_counter(postgres_dsn: str) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        with store.transaction() as connection:
            assert store.claim_source(
                connection, connector_id="source", worker_id="worker", now=0, lease_seconds=1
            )
            connection.execute("UPDATE source_checkpoints SET fence=2147483648")
        with store.transaction() as connection:
            claim = store.claim_source(
                connection, connector_id="source", worker_id="worker", now=2, lease_seconds=1
            )
            assert claim["fence"] == 2147483649


def test_postgres_concurrent_event_chain(postgres_dsn: str) -> None:
    with (
        StateStore(postgres_dsn, tenant_id="org:test") as first,
        StateStore(postgres_dsn, tenant_id="org:test") as second,
    ):
        start = Barrier(2)

        def append(store: StateStore, worker: int) -> None:
            start.wait(timeout=5)
            for index in range(20):
                store.record_event("CONCURRENT", {"worker": worker, "index": index})

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(append, first, 1), pool.submit(append, second, 2)]
            for future in futures:
                future.result(timeout=10)
        assert first.verify_event_chain()["events"] == 40
        assert first.event_envelopes() == second.event_envelopes()


def test_postgres_same_request_concurrency_returns_one_committed_result(postgres_dsn: str) -> None:
    with (
        StateStore(postgres_dsn, tenant_id="org:test") as first,
        StateStore(postgres_dsn, tenant_id="org:test") as second,
    ):
        start = Barrier(2)

        def apply(store: StateStore, worker: int) -> dict:
            start.wait(timeout=5)
            with store.transaction() as connection:
                existing = store.get_idempotent("shared", "request", connection=connection)
                if existing is not None:
                    return existing
                store.append_event(connection, "APPLIED", {"worker": worker})
                store.save_idempotent(connection, "shared", "request", {"worker": worker})
                return {"worker": worker}

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(apply, first, 1), pool.submit(apply, second, 2)]
            results = [future.result(timeout=5) for future in futures]
        assert results[0] == results[1]
        assert first.verify_event_chain()["events"] == 1


def test_postgres_pointer_precondition_serializes_only_competing_object(
    postgres_dsn: str, fixture: EnterpriseFixture
) -> None:
    original = next(item for item in fixture.objects if item.state == ObjectState.CURRENT)
    candidates = [
        VersionedObject.model_validate(
            original.model_dump(mode="json") | {"version": version, "state": "PROPOSED", "digest": ""}
        )
        for version in ("concurrent-a", "concurrent-b")
    ]
    with (
        StateStore(postgres_dsn, tenant_id="org:test") as first,
        StateStore(postgres_dsn, tenant_id="org:test") as second,
    ):
        first.load_fixture(fixture)
        with first.transaction() as connection:
            for candidate in candidates:
                first.insert_version(connection, candidate, make_current=False)
        start = Barrier(2)

        def promote(store: StateStore, candidate: VersionedObject) -> str:
            start.wait(timeout=5)
            try:
                with store.transaction() as connection:
                    store.promote_version(connection, original.id, original.version, candidate.version)
                    store.append_event(connection, "PROMOTED", {"version": candidate.version})
                return "APPLIED"
            except RuntimeError as exc:
                assert str(exc) == "EXPECTED_REVISION_MISMATCH"
                return "CONFLICT"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(promote, store, candidate)
                for store, candidate in zip((first, second), candidates, strict=True)
            ]
            assert sorted(future.result(timeout=5) for future in futures) == ["APPLIED", "CONFLICT"]
        winner = first.get_object(original.id)
        assert winner.version in {candidate.version for candidate in candidates}
        assert winner.state == ObjectState.CURRENT
        assert first.get_object(original.id, original.version).state == ObjectState.SUPERSEDED
        assert first.verify_event_chain()["events"] == 2


@pytest.mark.parametrize("alteration", ["check", "foreign_key", "unique_index"])
def test_postgres_rejects_changed_constraint_contract(postgres_dsn: str, alteration: str) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        if alteration == "check":
            name = store.connection.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid='effect_intents'::regclass AND pg_get_constraintdef(oid)='CHECK ((fence >= 0))'"
            ).fetchone()[0]
            store.connection.execute(
                sql.SQL("ALTER TABLE effect_intents DROP CONSTRAINT {}").format(sql.Identifier(name))
            )
            store.connection.execute("ALTER TABLE effect_intents ADD CHECK (fence >= -1)")
        elif alteration == "foreign_key":
            name = store.connection.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid='version_states'::regclass AND contype='f'"
            ).fetchone()[0]
            store.connection.execute(
                sql.SQL("ALTER TABLE version_states DROP CONSTRAINT {}").format(sql.Identifier(name))
            )
            store.connection.execute(
                "ALTER TABLE version_states ADD FOREIGN KEY(workspace_id,version_key) REFERENCES artifacts(workspace_id,artifact_id) DEFERRABLE"
            )
        else:
            store.connection.execute("CREATE UNIQUE INDEX unexpected_artifact_media ON artifacts(media_type)")
    with pytest.raises(
        IntegrityError, match=r"STATE_STORE_SCHEMA_(CONSTRAINTS_INVALID|UNEXPECTED_UNIQUE_INDEX)"
    ):
        StateStore(postgres_dsn, tenant_id="org:test")


def test_postgres_tenant_binding_rejects_wrong_instance(postgres_dsn: str) -> None:
    with pytest.raises(ValueError, match="POSTGRES_TENANT_ID_REQUIRED"):
        StateStore(postgres_dsn)
    with StateStore(postgres_dsn, tenant_id="org:one") as store, store.transaction() as connection:
        store.save_artifact(connection, "private", "application/json", {"tenant": "one"})
    with pytest.raises(IntegrityError, match="STATE_STORE_TENANT_MISMATCH"):
        StateStore(postgres_dsn, tenant_id="org:two")
    with StateStore(postgres_dsn, tenant_id="org:one") as store:
        assert store.load_artifact("private").payload == {"tenant": "one"}


def test_postgres_unknown_schema_version_is_not_repaired(postgres_dsn: str) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        store.connection.execute("UPDATE store_metadata SET schema_version=999")
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:999"):
        StateStore(postgres_dsn, tenant_id="org:test")
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        assert connection.execute("SELECT schema_version FROM store_metadata").fetchone()[0] == 999


def test_postgres_distinct_target_claims_do_not_share_write_lock(postgres_dsn: str) -> None:
    with (
        StateStore(postgres_dsn, tenant_id="org:test") as first,
        StateStore(postgres_dsn, tenant_id="org:test") as second,
    ):
        with first.transaction() as connection:
            for effect in ("one", "two"):
                first.put_effect(
                    connection,
                    effect_id=effect,
                    target_key=effect,
                    request_digest=effect,
                    request={},
                    created_at="2026-09-09T00:00:00Z",
                )
        claimed = Event()

        def claim_other() -> None:
            with second.transaction() as connection:
                assert second.claim_effect(
                    connection, effect_id="two", worker_id="worker:two", now=0, lease_seconds=60
                )
                assert second.acquire_target_barrier(connection, target_key="two", effect_id="two")
            claimed.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with first.transaction() as connection:
                assert first.claim_effect(
                    connection, effect_id="one", worker_id="worker:one", now=0, lease_seconds=60
                )
                future = pool.submit(claim_other)
                assert claimed.wait(timeout=3), "a different target was serialized behind this transaction"
            future.result(timeout=3)


def test_postgres_locked_effect_claim_skips_without_waiting(postgres_dsn: str) -> None:
    with (
        StateStore(postgres_dsn, tenant_id="org:test") as first,
        StateStore(postgres_dsn, tenant_id="org:test") as second,
    ):
        with first.transaction() as connection:
            first.put_effect(
                connection,
                effect_id="one",
                target_key="one",
                request_digest="one",
                request={},
                created_at="2026-09-09T00:00:00Z",
            )
        with first.transaction() as connection:
            assert first.claim_effect(connection, effect_id="one", worker_id="first", now=0, lease_seconds=60)
            with second.transaction() as other:
                assert (
                    second.claim_effect(other, effect_id="one", worker_id="second", now=61, lease_seconds=60)
                    is None
                )


@pytest.fixture(params=["sqlite", "postgresql"])
def persistent_store(request: pytest.FixtureRequest, tmp_path: Path, postgres_dsn: str):
    target = tmp_path / "store.sqlite" if request.param == "sqlite" else postgres_dsn
    with StateStore(target, tenant_id="org:test") as store:
        yield store


def test_store_effect_fence_and_persistent_target_barrier(persistent_store: StateStore) -> None:
    store = persistent_store
    with store.transaction() as connection:
        for effect in ("one", "two"):
            store.put_effect(
                connection,
                effect_id=effect,
                target_key="quote:one",
                request_digest=effect,
                request={"effect": effect},
                created_at="2026-09-09T00:00:00Z",
            )
        first = store.claim_effect(
            connection, effect_id="one", worker_id="worker:one", now=0, lease_seconds=10
        )
        assert first and first["fence"] == 1
        assert store.acquire_target_barrier(connection, target_key="quote:one", effect_id="one")
        store.update_effect(
            connection,
            effect_id="one",
            expected_state="READY",
            state="DISPATCHING",
            updated_at="2026-09-09T00:00:01Z",
            expected_fence=1,
        )
    with store.transaction() as connection:
        recovered = store.claim_effect(
            connection,
            effect_id="one",
            worker_id="worker:two",
            now=11,
            lease_seconds=10,
            allowed_states=("DISPATCHING",),
        )
        assert recovered and recovered["fence"] == 2 and recovered["state"] == "DISPATCHING"
        assert not store.acquire_target_barrier(connection, target_key="quote:one", effect_id="two")
        store.update_effect(
            connection,
            effect_id="one",
            expected_state="DISPATCHING",
            state="COMMIT_UNKNOWN",
            updated_at="2026-09-09T00:00:11Z",
            expected_fence=2,
        )
    with pytest.raises(RuntimeError, match="EFFECT_STATE_CONFLICT"), store.transaction() as connection:
        store.update_effect(
            connection,
            effect_id="one",
            expected_state="COMMIT_UNKNOWN",
            state="CONFIRMED",
            updated_at="2026-09-09T00:00:12Z",
            expected_fence=1,
        )
    with (
        pytest.raises(RuntimeError, match="TARGET_BARRIER_EFFECT_UNRESOLVED"),
        store.transaction() as connection,
    ):
        store.release_target_barrier(connection, target_key="quote:one", effect_id="one")
    assert store.get_target_barrier("quote:one") == "one"
    with store.transaction() as connection:
        store.update_effect(
            connection,
            effect_id="one",
            expected_state="COMMIT_UNKNOWN",
            state="CONFIRMED",
            updated_at="2026-09-09T00:00:13Z",
            result={"external_id": "confirmed"},
            expected_fence=2,
        )
        store.save_artifact(connection, "receipt:one", "application/json", {"confirmed": True})
        assert store.release_target_barrier(connection, target_key="quote:one", effect_id="one")
        assert store.acquire_target_barrier(connection, target_key="quote:one", effect_id="two")
    assert store.get_effect("one")["result"] == {"external_id": "confirmed"}
    assert store.load_artifact("receipt:one").payload == {"confirmed": True}


def test_store_cursor_page_and_artifact_commit_atomically(persistent_store: StateStore) -> None:
    store = persistent_store
    with store.transaction() as connection:
        claim = store.claim_source(
            connection, connector_id="connector:one", worker_id="worker:one", now=0, lease_seconds=60
        )
        assert claim and claim["cursor"] is None and claim["fence"] == 1
    with pytest.raises(KeyboardInterrupt), store.transaction() as connection:
        store.save_artifact(connection, "page:one", "application/json", {"items": [1]})
        store.commit_source_page(
            connection,
            connector_id="connector:one",
            expected_cursor=None,
            cursor="opaque/a?x=%_",
            worker_id="worker:one",
            fence=1,
            now=1,
        )
        raise KeyboardInterrupt("page transaction interrupted")
    assert not store.artifact_exists("page:one")
    assert store.get_source_checkpoint("connector:one")["revision"] == 0
    with store.transaction() as connection:
        store.save_artifact(connection, "page:one", "application/json", {"items": [1]})
        checkpoint = store.commit_source_page(
            connection,
            connector_id="connector:one",
            expected_cursor=None,
            cursor="opaque/a?x=%_",
            worker_id="worker:one",
            fence=1,
            now=2,
        )
    assert checkpoint["cursor"] == "opaque/a?x=%_" and checkpoint["revision"] == 1
    assert checkpoint["lease_owner"] is None and checkpoint["lease_until"] is None
    assert store.load_artifact("page:one").payload == {"items": [1]}


def test_store_cursor_rejects_expired_or_stale_worker(persistent_store: StateStore) -> None:
    store = persistent_store
    with store.transaction() as connection:
        assert store.claim_source(connection, connector_id="source", worker_id="old", now=0, lease_seconds=10)
    with pytest.raises(RuntimeError, match="SOURCE_CHECKPOINT_CONFLICT"), store.transaction() as connection:
        store.commit_source_page(
            connection,
            connector_id="source",
            expected_cursor=None,
            cursor="bad",
            worker_id="old",
            fence=1,
            now=10,
        )
    with store.transaction() as connection:
        assert (
            store.claim_source(connection, connector_id="source", worker_id="new", now=11, lease_seconds=10)[
                "fence"
            ]
            == 2
        )
    with pytest.raises(RuntimeError, match="SOURCE_CHECKPOINT_CONFLICT"), store.transaction() as connection:
        store.commit_source_page(
            connection,
            connector_id="source",
            expected_cursor=None,
            cursor="bad",
            worker_id="old",
            fence=1,
            now=12,
        )
    with store.transaction() as connection:
        assert not store.release_source_claim(connection, connector_id="source", worker_id="old", fence=1)
        assert store.release_source_claim(connection, connector_id="source", worker_id="new", fence=2)
    assert store.get_source_checkpoint("source")["cursor"] is None


def test_store_keyset_pages_escape_literal_prefix(persistent_store: StateStore) -> None:
    store = persistent_store
    with store.transaction() as connection:
        for artifact_id in ("item:%_A", "item:%_B", "item:%_C", "item:other", "ITEM:%_D"):
            store.save_artifact(connection, artifact_id, "application/json", {"id": artifact_id})
        for index in range(5):
            store.append_event(connection, "PAGE", {"index": index})
    first = store.artifact_page(artifact_id_prefix="item:%_", limit=2)
    second = store.artifact_page(artifact_id_prefix="item:%_", after=first["next_cursor"], limit=2)
    assert [item.artifact_id for item in first["items"] + second["items"]] == [
        "item:%_A",
        "item:%_B",
        "item:%_C",
    ]
    assert second["next_cursor"] is None
    assert tuple(item.artifact_id for item in store.list_artifacts(artifact_id_prefix="item:%_")) == (
        "item:%_A",
        "item:%_B",
        "item:%_C",
    )
    events = store.event_page(limit=3)
    remaining = store.event_page(after=events["next_cursor"], limit=3)
    assert [item["sequence_no"] for item in events["items"] + remaining["items"]] == [1, 2, 3, 4, 5]
    assert remaining["next_cursor"] is None
    assert store.count_records() == {
        "artifacts": 5,
        "current_pointers": 0,
        "object_versions": 0,
        "domain_events": 5,
    }


def test_store_rechecks_persisted_tenant_before_reads_and_writes(persistent_store: StateStore) -> None:
    store = persistent_store
    with (pytest.raises(IntegrityError, match="STATE_STORE_TENANT_MISMATCH"),
          store.transaction() as connection):
        store.save_artifact(connection, "rolled-back", "application/json", {})
        connection.execute("UPDATE store_metadata SET tenant_id='org:changed'")
    assert not store.artifact_exists("rolled-back")
    with store.transaction() as connection:
        store.save_artifact(connection, "private", "application/json", {})
    # Simulate an administrator changing the binding outside the business API.
    store.connection.execute("UPDATE store_metadata SET tenant_id='org:changed'")
    store.connection.commit()
    with pytest.raises(IntegrityError, match="STATE_STORE_TENANT_MISMATCH"):
        store.load_artifact("private")
    with pytest.raises(IntegrityError, match="STATE_STORE_TENANT_MISMATCH"):
        store.record_event("WRONG_TENANT", {})


def test_store_bound_database_cannot_be_reset(persistent_store: StateStore) -> None:
    with pytest.raises(RuntimeError, match="STATE_STORE_RESET_LOCAL_SESSION_ONLY"):
        persistent_store.reset_local_session()
