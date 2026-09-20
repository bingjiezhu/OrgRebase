from __future__ import annotations

import asyncio
import sqlite3

import psycopg
import pytest

from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime


@pytest.fixture
def faulty_store(monkeypatch):
    class FaultConnection(sqlite3.Connection):
        failure = None
        close_failed = False

        def rollback(self):
            if self.failure == "rollback":
                raise sqlite3.OperationalError("rollback failed")
            return super().rollback()

        def execute(self, sql, *args, **kwargs):
            if self.failure == "begin" and sql == "BEGIN":
                raise sqlite3.OperationalError("begin failed")
            if self.failure == "restore" and sql == "PRAGMA query_only = 0":
                raise sqlite3.OperationalError("restore failed")
            return super().execute(sql, *args, **kwargs)

        def close(self):
            if self.close_failed:
                raise sqlite3.OperationalError("close failed")
            return super().close()

    connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=FaultConnection, **kw))
    store = StateStore()
    try:
        yield store
    finally:
        # A simulated close I/O error deliberately leaves the native handle open.
        sqlite3.Connection.close(store.connection)
        store.close()


@pytest.mark.parametrize("cleanup", ["rollback", "restore"])
@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_cleanup_failure_discards_connection_and_preserves_original_error(faulty_store, cleanup, failure):
    store = faulty_store
    original = failure("body failed") if failure else None
    with pytest.raises(failure or sqlite3.OperationalError) as caught, store.read_snapshot():
        store.connection.failure = cleanup
        if original:
            raise original
    if original:
        assert caught.value is original
        assert cleanup + " failed" in " ".join(caught.value.__notes__)
    else:
        assert cleanup + " failed" in str(caught.value)
    assert store._closed is True
    assert store._snapshot_depth == 0
    assert store._snapshot_transaction_read_only is False
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.connection.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="STATE_STORE_CLOSED"):
        store.read_snapshot().__enter__()


def test_failed_begin_restores_query_only_without_poisoning_next_write(faulty_store):
    store = faulty_store
    store.connection.failure = "begin"
    with pytest.raises(sqlite3.OperationalError, match="begin failed"), store.read_snapshot():
        pytest.fail("a failed BEGIN cannot enter the body")
    assert store.connection.execute("PRAGMA query_only").fetchone()[0] == 0
    assert not store.connection.in_transaction
    store.connection.failure = None
    store.record_event("NEXT", {})


def test_even_failed_native_close_cannot_reuse_the_store(faulty_store):
    store = faulty_store
    with pytest.raises(sqlite3.OperationalError, match="rollback failed") as caught, store.read_snapshot():
        store.connection.failure = "rollback"
        store.connection.close_failed = True
    assert "close failed" in " ".join(caught.value.__notes__)
    assert store._closed
    for operation in (lambda: store.record_event("MUST_NOT_WRITE", {}), store.check_health,
                      lambda: store.read_snapshot().__enter__()):
        with pytest.raises(RuntimeError, match="STATE_STORE_CLOSED"):
            operation()
    store.connection.close_failed = False


def test_postgres_rollback_failure_closes_only_the_owned_connection(postgres_runtime, monkeypatch):
    db = postgres_runtime(tenant_id="org:cleanup")
    with StateStore(db["runtime_dsn"], tenant_id="org:cleanup", migrate=False) as store:
        original = psycopg.Connection.rollback

        def failed(connection):
            if connection is store.connection:
                raise psycopg.OperationalError("rollback disconnected")
            return original(connection)

        with pytest.raises(RuntimeError, match="body failed") as caught, store.read_snapshot():
            monkeypatch.setattr(psycopg.Connection, "rollback", failed)
            raise RuntimeError("body failed")
        assert store.connection.closed and store._closed
        assert "rollback disconnected" in " ".join(caught.value.__notes__)


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_snapshot_exit_rechecks_recovery_in_a_new_view(backend, tmp_path, request):
    if backend == "postgresql":
        db = request.getfixturevalue("postgres_runtime")(tenant_id="org:recovery")
        writer = psycopg.connect(db["migration_dsn"], autocommit=True)
        store = StateStore(db["runtime_dsn"], tenant_id="org:recovery", migrate=False)
    else:
        path = tmp_path / "recovery.sqlite"
        store = StateStore(path)
        writer = sqlite3.connect(path, autocommit=True)
    try:
        with pytest.raises(IntegrityError, match="STATE_STORE_RECOVERY_QUALIFICATION_REQUIRED"), store.read_snapshot():
            writer.execute("UPDATE store_metadata SET recovery_required=1")
            # This database fact is intentionally pinned inside the snapshot.
            assert store.check_health()["recovery_required"] is False
        with pytest.raises(IntegrityError, match="STATE_STORE_RECOVERY_QUALIFICATION_REQUIRED"):
            store.record_event("REJECT_AFTER_RECOVERY", {})
        assert store.connection.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0] == 0
    finally:
        writer.close()
        store.close()


def test_postgres_binding_is_rechecked_inside_the_same_snapshot(postgres_runtime):
    db = postgres_runtime(tenant_id="org:binding")
    with StateStore(db["runtime_dsn"], tenant_id="org:binding", migrate=False) as store:
        with pytest.raises(IntegrityError, match="STATE_STORE_WORKSPACE_BINDING_LOST"), store.read_snapshot():
            store.connection.execute("SELECT set_config('orgrebase.workspace_id', 'other', true)")
            store.check_health()
        assert store.check_health()["workspace_id"] == "default"
