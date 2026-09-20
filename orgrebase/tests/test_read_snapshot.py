from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import psycopg
import pytest

from orgrebase.database import in_transaction
from orgrebase.store import StateStore
from orgrebase.workspace.read_dependencies import _lock_scope_if_transaction
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime


@pytest.fixture(params=["sqlite", "postgresql"])
def stores(request, tmp_path):
    if request.param == "postgresql":
        database = request.getfixturevalue("postgres_runtime")(tenant_id="snapshot-tenant")
        options = {"path": database["runtime_dsn"], "tenant_id": "snapshot-tenant", "migrate": False}
    else:
        options = {"path": tmp_path / "snapshot.sqlite"}
    with StateStore(**options) as reader, StateStore(**options) as writer:
        yield reader, writer


def test_snapshot_pins_reads_while_another_connection_can_commit(stores):
    reader, writer = stores
    writer.record_event("BEFORE", {})
    with reader.read_snapshot():
        before = reader.verify_event_chain()
        writer.record_event("DURING", {})
        with reader.read_snapshot():
            assert reader.verify_event_chain() == before
        assert in_transaction(reader.connection)
    assert not in_transaction(reader.connection)
    assert reader.verify_event_chain()["events"] == 2


def test_snapshot_rejects_core_and_raw_writes_then_restores_connection(stores):
    reader, _ = stores
    with reader.read_snapshot() as connection, pytest.raises(RuntimeError, match="STATE_STORE_READ_ONLY_SNAPSHOT"):
        reader.save_artifact(connection, "must-not-exist", "application/json", {})
    with pytest.raises((sqlite3.OperationalError, psycopg.errors.ReadOnlySqlTransaction)), reader.read_snapshot() as connection:
        connection.execute("UPDATE store_metadata SET tenant_id = tenant_id")
    assert not in_transaction(reader.connection)
    assert not reader.artifact_exists("must-not-exist")
    reader.record_event("AFTER", {})
    assert reader.verify_event_chain()["events"] == 1


def test_borrowed_snapshot_never_commits_or_rolls_back_its_caller(stores):
    reader, writer = stores
    with reader.transaction() as connection:
        reader.append_event(connection, "BEFORE", {})
        with pytest.raises(KeyboardInterrupt), reader.read_snapshot():
            assert reader.verify_event_chain()["events"] == 1
            raise KeyboardInterrupt
        assert in_transaction(connection)
        assert writer.verify_event_chain()["events"] == 0
        reader.append_event(connection, "AFTER", {})
    assert writer.verify_event_chain()["events"] == 2


def test_cancelled_snapshot_releases_only_its_own_transaction(stores):
    reader, _ = stores
    with pytest.raises(KeyboardInterrupt), reader.read_snapshot():
        raise KeyboardInterrupt
    assert not in_transaction(reader.connection)
    reader.record_event("RECOVERED", {})


def test_joining_a_write_transaction_preserves_source_scope_lock(postgres_runtime):
    database = postgres_runtime(tenant_id="snapshot-lock")
    options = {"path": database["runtime_dsn"], "tenant_id": "snapshot-lock", "migrate": False}
    with StateStore(**options) as reader, StateStore(**options) as writer:
        writer.connection.execute("SET lock_timeout = '100ms'")
        with reader.transaction():
            with reader.read_snapshot():
                _lock_scope_if_transaction(SimpleNamespace(store=reader))
            with pytest.raises(psycopg.errors.LockNotAvailable):
                writer.record_event("MUST_WAIT_FOR_SOURCE_CHECK", {})
        writer.record_event("AFTER_SOURCE_CHECK", {})
        assert writer.verify_event_chain()["events"] == 1
