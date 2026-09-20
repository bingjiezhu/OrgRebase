from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg
import pytest
from test_postgres_store import postgres_cluster as postgres_cluster
from test_postgres_store import postgres_dsn as postgres_dsn

from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore


@pytest.mark.parametrize("migrate", [False, True])
def test_postgres_missing_metadata_is_rejected_without_repair(postgres_dsn: str, migrate: bool) -> None:
    with StateStore(postgres_dsn, tenant_id="org:test") as store:
        store.record_event("PRESERVE_EXISTING_HISTORY", {})
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        connection.execute("DELETE FROM store_metadata")
        before = connection.execute("SELECT event_digest FROM domain_events ORDER BY sequence_no").fetchall()
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_METADATA_INVALID"):
        StateStore(postgres_dsn, tenant_id="org:test", migrate=migrate)
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM store_metadata").fetchone()[0] == 0
        assert connection.execute("SELECT event_digest FROM domain_events ORDER BY sequence_no").fetchall() == before


@pytest.mark.parametrize("migrate", [False, True])
def test_sqlite_missing_metadata_is_rejected_without_repair(tmp_path: Path, migrate: bool) -> None:
    path = tmp_path / "store.sqlite3"
    with StateStore(path) as store:
        store.record_event("PRESERVE_EXISTING_HISTORY", {})
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM store_metadata")
        before = connection.execute("SELECT event_digest FROM domain_events ORDER BY sequence_no").fetchall()
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_METADATA_INVALID"):
        StateStore(path, migrate=migrate)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM store_metadata").fetchone()[0] == 0
        assert connection.execute("SELECT event_digest FROM domain_events ORDER BY sequence_no").fetchall() == before
