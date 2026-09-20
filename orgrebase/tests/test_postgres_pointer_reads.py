from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from orgrebase.domain import ObjectState, VersionedObject
from orgrebase.store import StateStore


@pytest.mark.parametrize("operation", ["get_object", "state_snapshot", "promote_version"])
def test_waiting_reader_follows_newly_committed_pointer_with_fresh_version_snapshot(
    postgres_runtime, fixture, operation
):
    database = postgres_runtime(tenant_id="org:pointer-read")
    original = next(item for item in fixture.objects if item.state == ObjectState.CURRENT)
    following = VersionedObject.model_validate(
        original.model_dump(mode="json") | {"version": "concurrent-successor", "digest": ""}
    )
    final = VersionedObject.model_validate(
        original.model_dump(mode="json")
        | {"version": "second-successor", "state": "PROPOSED", "digest": ""}
    )
    settings = {"tenant_id": "org:pointer-read", "migrate": False}
    with (
        StateStore(database["runtime_dsn"], **settings) as writer,
        StateStore(database["runtime_dsn"], **settings) as reader,
    ):
        writer.load_fixture(fixture)
        with writer.transaction() as connection:
            writer.insert_version(connection, final, make_current=False)
        writer_pid = writer.connection.execute("SELECT pg_backend_pid()").fetchone()[0]
        reader_pid = reader.connection.execute("SELECT pg_backend_pid()").fetchone()[0]
        started = Event()

        def read_after_pointer_lock():
            with reader.transaction() as connection:
                started.set()
                if operation == "get_object":
                    return reader.get_object(original.id).ref
                if operation == "state_snapshot":
                    return reader.state_snapshot((original.id,))[original.id]["version"]
                reader.promote_version(connection, original.id, following.version, final.version)
                return reader.get_object(original.id).ref

        with ThreadPoolExecutor(max_workers=1) as pool:
            with writer.transaction() as connection:
                writer.transition_current(connection, original.id, ObjectState.SUPERSEDED)
                writer.insert_version(connection, following, make_current=True)
                # The new version does not exist in the reader's first statement snapshot.
                future = pool.submit(read_after_pointer_lock)
                assert started.wait(5)
                deadline = time.monotonic() + 5
                blockers = ()
                while time.monotonic() < deadline:
                    blockers = writer.connection.execute(
                        "SELECT pg_blocking_pids(%s)", (reader_pid,)
                    ).fetchone()[0]
                    if writer_pid in blockers:
                        break
                    time.sleep(0.01)
                assert writer_pid in blockers
                assert not future.done()
            result = future.result(timeout=5)
        expected = final.ref if operation == "promote_version" else (
            following.version if operation == "state_snapshot" else following.ref
        )
        assert result == expected
        assert reader.get_object(original.id).state == ObjectState.CURRENT
