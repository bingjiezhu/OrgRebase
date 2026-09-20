from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import select, update

from orgrebase.clock import FrozenClock
from orgrebase.database import execute_core, private_records
from orgrebase.domain import IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore


class Clock:
    value = "2026-09-09T00:00:00Z"
    def now(self):
        return self.value


def save(records, *, payload=None):
    with records.store.transaction() as connection:
        records.write(connection, record_id="private:1", scope_ref="run:1", owner_id="actor:1", payload=payload or {"text": "private-canary"})


def test_private_record_expiry_denies_read_then_purges_content_without_reviving():
    clock = Clock()
    with StateStore() as store:
        records = PrivateRecordStore(store, clock, retention_seconds=60)
        assert records.record_status("private:1") == "MISSING"
        save(records)
        assert records.record_status("private:1") == "AVAILABLE"
        assert records.read("private:1") == {"text": "private-canary"}
        assert "private-canary" not in str(store.event_envelopes())
        assert not store.artifact_exists("private:1")
        clock.value = "2026-09-09T00:01:00.000001Z"
        assert records.read("private:1") is None
        assert records.record_status("private:1") == "EXPIRED"
        assert records.purge_expired() == 1
        assert records.record_status("private:1") == "DELETED"
        assert records.purge_expired() == 0
        save(records)
        assert records.read("private:1") is None
        assert "private-canary" not in str(tuple(store.connection.iterdump()))
        assert records.deletion_ledger()[0]["deletion_reason"] == "RETENTION_EXPIRED"
        assert store.verify_event_chain()["status"] == "PASS"


def test_private_delete_requires_owner_and_preserves_idempotency_tombstone():
    with StateStore() as store:
        records = PrivateRecordStore(store, FrozenClock("2026-09-09T00:00:00Z"))
        save(records)
        with pytest.raises(PermissionError, match="PRIVATE_RECORD_OWNER_REQUIRED"):
            records.erase("private:1", actor_id="actor:2")
        assert records.erase("private:1", actor_id="actor:1")
        assert not records.erase("private:1", actor_id="actor:1")
        save(records)
        assert records.matches("private:1", {"text": "private-canary"})
        assert records.read("private:1") is None
        with pytest.raises(IntegrityError, match="PRIVATE_RECORD_CONFLICT"):
            save(records, payload={"text": "changed"})


def test_zero_retention_never_persists_source_text():
    with StateStore() as store:
        records = PrivateRecordStore(store, FrozenClock("2026-09-09T00:00:00Z"), retention_seconds=0)
        save(records)
        assert records.read("private:1") is None
        assert "private-canary" not in str(tuple(store.connection.iterdump()))
        assert records.deletion_ledger()[0]["deletion_reason"] == "RETENTION_DISABLED"


def test_deleted_content_is_reapplied_to_older_restored_database(tmp_path):
    clock = Clock()
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    with StateStore(source) as store:
        records = PrivateRecordStore(store, clock)
        save(records)
        with sqlite3.connect(backup) as target:
            store.connection.backup(target)
        records.erase("private:1", actor_id="actor:1")
        ledger = records.deletion_ledger()
    with StateStore(backup) as restored:
        records = PrivateRecordStore(restored, clock)
        assert records.read("private:1") is not None
        assert records.reapply_deletions(ledger) == 1
        assert records.read("private:1") is None
        save(records)
        assert records.read("private:1") is None
        with pytest.raises(IntegrityError, match="PRIVATE_DELETION_LEDGER_DUPLICATE"):
            records.reapply_deletions(ledger + ledger)


def test_private_artifact_migration_moves_only_exact_private_record():
    with StateStore() as store:
        payload = {"run_id": "run:1", "actor_id": "actor:1", "text": "private-canary"}
        with store.transaction() as connection:
            store.save_artifact(connection, "private:1", "private/type", payload)
            store.save_artifact(connection, "public:1", "application/json", {"public": True})
        records = PrivateRecordStore(store, FrozenClock("2026-09-09T00:00:00Z"))
        assert records.migrate_artifact("private:1", "private/type")
        assert not records.migrate_artifact("private:1", "private/type")
        assert not store.artifact_exists("private:1")
        assert store.artifact_exists("public:1")
        assert records.read("private:1") == payload
        assert "private-canary" not in str(store.event_envelopes())


def test_deletion_ledger_prevents_recreating_data_missing_from_older_backup():
    clock = FrozenClock("2026-09-09T00:00:00Z")
    with StateStore() as original:
        records = PrivateRecordStore(original, clock)
        save(records)
        records.erase("private:1", actor_id="actor:1")
        ledger = records.deletion_ledger()
    with StateStore() as restored:
        records = PrivateRecordStore(restored, clock)
        assert records.reapply_deletions(ledger) == 1
        assert records.reapply_deletions(ledger) == 0
        save(records)
        assert records.read("private:1") is None
        assert "private-canary" not in str(tuple(restored.connection.iterdump()))
        assert restored.verify_event_chain()["status"] == "PASS"


def test_private_tampering_and_bad_deletion_binding_fail_closed():
    with StateStore() as store:
        records = PrivateRecordStore(store, FrozenClock("2026-09-09T00:00:00Z"))
        save(records)
        with store.transaction() as connection:
            execute_core(connection, update(private_records).values(content_json='{"text":"tampered"}'))
        with pytest.raises(IntegrityError, match="PRIVATE_RECORD_DIGEST_MISMATCH"):
            records.read("private:1")
        row = execute_core(store.connection, select(private_records)).fetchone()
        entry = {key: row[key] for key in ("record_id", "scope_ref", "owner_id", "content_digest")}
        entry.update(content_digest="sha256:" + "f" * 64, deleted_at="2026-09-09T00:00:00Z", deletion_reason="USER_REQUESTED")
        with pytest.raises(IntegrityError, match="PRIVATE_DELETION_BINDING_MISMATCH"):
            records.reapply_deletions([entry])


@pytest.mark.parametrize("seconds", [-1, 604801, True, 1.5])
def test_invalid_retention_policy(seconds):
    with StateStore() as store, pytest.raises(ValueError, match="PRIVATE_RETENTION_INVALID"):
        PrivateRecordStore(store, FrozenClock("2026-09-09T00:00:00Z"), retention_seconds=seconds)
