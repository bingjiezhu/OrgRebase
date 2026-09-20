from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Barrier, Event

import pytest

from orgrebase.store import StateStore
from orgrebase.store_migrations import STATE_STORE_SCHEMA_VERSION


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit, asyncio.CancelledError, RuntimeError])
def test_interrupted_transaction_rolls_back_all_writes(tmp_path: Path, failure: type[BaseException]) -> None:
    path = tmp_path / "cancel.sqlite"
    with StateStore(path) as store, StateStore(path) as observer:
        with pytest.raises(failure), store.transaction() as connection:
            store.save_artifact(connection, "artifact:cancelled", "application/json", {"value": 1})
            store.append_event(connection, "CANCELLED", {"value": 1})
            store.save_idempotent(connection, "cancelled", "request", {"value": 1})
            raise failure("cancel the unit of work")
        assert not store.connection.in_transaction
        assert not observer.artifact_exists("artifact:cancelled")
        assert observer.verify_event_chain()["events"] == 0
        assert observer.get_idempotent("cancelled", "request") is None
        store.record_event("NEXT", {})
        assert observer.verify_event_chain()["events"] == 1


def test_real_commit_failure_rolls_back_and_connection_can_be_reused(tmp_path: Path) -> None:
    path = tmp_path / "commit.sqlite"
    with StateStore(path) as store, StateStore(path) as observer:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), store.transaction() as connection:
            connection.execute("PRAGMA defer_foreign_keys = ON")
            connection.execute("INSERT INTO version_states(version_key,state) VALUES ('missing', 'CURRENT')")
            store.save_artifact(connection, "artifact:failed", "application/json", {"value": 1})
            store.append_event(connection, "FAILED", {})
            store.save_idempotent(connection, "failed", "request", {"value": 1})
            # The deferred constraint allows every statement; COMMIT fails.
            assert connection.execute("SELECT COUNT(*) FROM version_states").fetchone()[0] == 1
        assert not store.connection.in_transaction
        assert not observer.artifact_exists("artifact:failed")
        assert observer.verify_event_chain()["events"] == 0
        assert observer.get_idempotent("failed", "request") is None
        assert observer.connection.execute("SELECT COUNT(*) FROM version_states").fetchone()[0] == 0
        store.record_event("RECOVERED", {})
        assert observer.verify_event_chain()["events"] == 1


def test_nested_transaction_rejection_does_not_rollback_outer_work() -> None:
    with StateStore() as store:
        with store.transaction() as connection:
            store.append_event(connection, "BEFORE", {})
            with pytest.raises(RuntimeError, match="STATE_STORE_TRANSACTION_ALREADY_ACTIVE"):
                store.record_event("NESTED", {})
            assert connection.in_transaction
            store.append_event(connection, "AFTER", {})
        assert [event["event_type"] for event in store.event_records()] == ["BEFORE", "AFTER"]


def test_failed_begin_does_not_claim_or_rollback_another_connection(tmp_path: Path) -> None:
    path = tmp_path / "busy.sqlite"
    with StateStore(path) as first, StateStore(path) as second:
        second.connection.execute("PRAGMA busy_timeout = 20")
        with first.transaction() as connection:
            first.append_event(connection, "FIRST", {})
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                second.record_event("BLOCKED", {})
            assert connection.in_transaction
            assert not second.connection.in_transaction
        second.record_event("SECOND", {})
        assert [event["event_type"] for event in first.event_records()] == ["FIRST", "SECOND"]
        assert first.verify_event_chain()["events"] == 2


def test_waiting_writer_succeeds_after_lock_release(tmp_path: Path) -> None:
    path = tmp_path / "wait.sqlite"
    started = Event()
    finished = Event()
    with StateStore(path) as first, StateStore(path) as second, ThreadPoolExecutor(max_workers=1) as pool:

        def competing_write() -> str:
            started.set()
            try:
                return second.record_event("SECOND", {})
            finally:
                finished.set()

        with first.transaction() as connection:
            first.append_event(connection, "FIRST", {})
            future = pool.submit(competing_write)
            assert started.wait(timeout=2)
            assert not finished.wait(timeout=0.05)
        assert future.result(timeout=2).startswith("sha256:")
        assert first.verify_event_chain()["events"] == 2


def test_two_connections_serialize_event_chain_and_read_only_sees_commits(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.sqlite"
    start = Barrier(2)
    with StateStore(path) as first, StateStore(path) as second:
        with first.transaction() as connection:
            first.save_artifact(connection, "artifact:new", "application/json", {"value": 1})
            assert not second.artifact_exists("artifact:new")
        assert second.artifact_exists("artifact:new")

        def append_many(store: StateStore, writer: int) -> None:
            start.wait(timeout=2)
            for number in range(20):
                store.record_event("CONCURRENT", {"writer": writer, "number": number})

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(append_many, first, 1), pool.submit(append_many, second, 2)]
            for future in futures:
                future.result(timeout=5)
        assert first.verify_event_chain()["events"] == 40
        assert first.event_envelopes() == second.event_envelopes()


def test_concurrent_first_open_migrates_only_once(tmp_path: Path) -> None:
    path = tmp_path / "new-concurrent.sqlite"
    start = Barrier(2)

    def open_and_write(number: int) -> None:
        start.wait(timeout=2)
        with StateStore(path) as store:
            store.record_event("OPEN", {"number": number})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_and_write, number) for number in range(2)]
        for future in futures:
            future.result(timeout=5)
    with StateStore(path) as store:
        assert store.verify_event_chain()["events"] == 2
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == STATE_STORE_SCHEMA_VERSION


def test_rollback_failure_closes_unusable_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailedRollback(sqlite3.Connection):
        def rollback(self) -> None:
            raise sqlite3.OperationalError("injected rollback I/O failure")

    connect = sqlite3.connect

    def configured_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return connect(*args, factory=FailedRollback, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", configured_connect)
    store = StateStore()
    with pytest.raises(RuntimeError, match="original failure") as raised, store.transaction() as connection:
        store.append_event(connection, "ROLLBACK_FAILURE", {})
        raise RuntimeError("original failure")
    assert "rollback I/O failure" in raised.value.__notes__[0]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.connection.execute("SELECT 1")
    store.close()


@pytest.mark.parametrize("failure", (RuntimeError, KeyboardInterrupt))
def test_completion_check_runs_after_borrower_writes_and_failure_rolls_back(failure) -> None:
    with StateStore() as store:
        observed = []

        def require_completion():
            observed.append([event["event_type"] for event in store.event_records()])
            raise failure("completion rejected")

        with pytest.raises(failure, match="completion rejected"), store.transaction() as connection:
            store.append_event(connection, "BEFORE", {})
            store.require_before_commit(connection, require_completion)
            store.append_event(connection, "AFTER", {})
        assert observed == [["BEFORE", "AFTER"]]
        assert store.verify_event_chain()["events"] == 0
        store.record_event("NEXT", {})
        assert observed == [["BEFORE", "AFTER"]]
        assert store.verify_event_chain()["events"] == 1


def test_completion_checks_reject_foreign_unmanaged_and_closed_transactions() -> None:
    with StateStore() as store, StateStore() as other:
        with pytest.raises(RuntimeError, match="STATE_STORE_MANAGED_TRANSACTION_REQUIRED"):
            store.require_before_commit(store.connection, lambda: None)
        store.connection.execute("BEGIN")
        try:
            with pytest.raises(RuntimeError, match="STATE_STORE_MANAGED_TRANSACTION_REQUIRED"):
                store.require_before_commit(store.connection, lambda: None)
        finally:
            store.connection.rollback()
        with store.transaction() as connection:
            with pytest.raises(RuntimeError, match="STATE_STORE_FOREIGN_TRANSACTION"):
                store.require_before_commit(other.connection, lambda: None)
            store.require_before_commit(connection, lambda: None)
        with pytest.raises(RuntimeError, match="STATE_STORE_MANAGED_TRANSACTION_REQUIRED"):
            store.require_before_commit(connection, lambda: None)


def test_completion_check_cannot_append_checks_while_checks_are_running() -> None:
    with StateStore() as store:
        with pytest.raises(RuntimeError, match="STATE_STORE_MANAGED_TRANSACTION_REQUIRED"), store.transaction() as connection:
            store.append_event(connection, "DENIED", {})
            store.require_before_commit(connection, lambda: store.require_before_commit(connection, lambda: None))
        assert store.verify_event_chain()["events"] == 0
        store.record_event("NEXT", {})
        assert store.verify_event_chain()["events"] == 1


@pytest.mark.parametrize("abort", (False, True))
def test_completion_checks_do_not_leak_to_next_transaction(abort) -> None:
    with StateStore() as store:
        observed = []
        with (
            pytest.raises(RuntimeError) if abort else nullcontext(),
            store.transaction() as connection,
        ):
            store.require_before_commit(connection, lambda: observed.append("checked"))
            if abort:
                raise RuntimeError("aborted before completion")
        assert observed == ([] if abort else ["checked"])
        store.record_event("NEXT", {})
        assert observed == ([] if abort else ["checked"])
