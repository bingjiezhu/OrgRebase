from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from orgrebase.local_storage import require_safe_sqlite_wal_runtime
from orgrebase.store import StateStore
from orgrebase.workspace.runtime_journal import RuntimeJournal


@pytest.mark.parametrize("version", [
    (3, 7, 0), (3, 44, 5), (3, 45, 0), (3, 50, 6), (3, 51, 0), (3, 51, 2),
])
def test_affected_wal_engines_are_rejected(monkeypatch, version):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    with pytest.raises(RuntimeError, match="SQLITE_WAL_RUNTIME_UNSUPPORTED"):
        require_safe_sqlite_wal_runtime()


@pytest.mark.parametrize("version", [
    (3, 44, 6), (3, 44, 7), (3, 50, 7), (3, 50, 8), (3, 51, 3), (3, 53, 1),
])
def test_fixed_wal_engines_including_backports_are_accepted(monkeypatch, version):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    require_safe_sqlite_wal_runtime()


@pytest.mark.parametrize("factory", [StateStore, RuntimeJournal])
@pytest.mark.parametrize("existing", [False, True])
def test_wal_guard_precedes_file_changes_and_connections(tmp_path, monkeypatch, factory, existing):
    path = tmp_path / "runtime" / "workspace.sqlite3"
    original = b"existing database bytes remain unchanged"
    if existing:
        path.parent.mkdir()
        path.write_bytes(original)
        path.chmod(0o640)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Unsupported WAL runtime reached a database connection")

    monkeypatch.setattr(sqlite3, "connect", unexpected_connection)
    with pytest.raises(RuntimeError, match=r"SQLITE_WAL_RUNTIME_UNSUPPORTED:3\.51\.2"):
        factory(path)
    if existing:
        assert path.read_bytes() == original
        assert path.stat().st_mode & 0o777 == 0o640
    else:
        assert not path.parent.exists()


def test_wal_guard_does_not_restrict_memory_or_read_only_store(tmp_path: Path, monkeypatch):
    path = tmp_path / "workspace.sqlite3"
    with StateStore(path):
        pass
    original = path.read_bytes()
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    with StateStore() as memory:
        assert memory.connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    with StateStore(path, maintenance=True, read_only=True) as reader:
        assert reader.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert reader.verify_event_chain()["status"] == "PASS"
    assert path.read_bytes() == original


def test_postgresql_connection_does_not_use_sqlite_runtime_guard(monkeypatch):
    class PostgreSQLConnectionReached(Exception):
        pass

    def connect(*args, **kwargs):
        raise PostgreSQLConnectionReached

    def unexpected_guard():
        pytest.fail("PostgreSQL reached the SQLite WAL runtime guard")

    monkeypatch.setattr("orgrebase.store.psycopg.connect", connect)
    monkeypatch.setattr("orgrebase.store.require_safe_sqlite_wal_runtime", unexpected_guard)
    with pytest.raises(PostgreSQLConnectionReached):
        StateStore("postgresql://local-test/unused", tenant_id="org:test")
