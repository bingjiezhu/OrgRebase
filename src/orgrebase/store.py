"""Small append-only SQLite store for the local deterministic profile."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.fixture import EnterpriseFixture


class StateStore:
    """Canonical local store.

    Business payload rows are immutable. Trust state and the current pointer are
    version-relative overlays with an append-only event for every transition.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self._closed = False
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        if self._closed:
            return
        self.connection.close()
        self._closed = True

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive resource cleanup
        try:
            self.close()
        except Exception:
            pass

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS object_versions (
                version_key TEXT PRIMARY KEY,
                object_id TEXT NOT NULL,
                version TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                UNIQUE(object_id, version)
            );
            CREATE TABLE IF NOT EXISTS version_states (
                version_key TEXT PRIMARY KEY REFERENCES object_versions(version_key),
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS current_pointers (
                object_id TEXT PRIMARY KEY,
                version_key TEXT NOT NULL REFERENCES object_versions(version_key),
                revision INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS domain_events (
                sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_digest TEXT NOT NULL,
                event_digest TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS idempotency_records (
                key TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                media_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_operation_journal (
                operation_key TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                operation TEXT NOT NULL,
                repository_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expected_external_parent TEXT NOT NULL,
                external_operation_id TEXT,
                result_json TEXT,
                error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS compensation_sagas (
                saga_key TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                receipt_json TEXT NOT NULL
            );
            """
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def load_fixture(self, fixture: EnterpriseFixture) -> None:
        with self.transaction() as connection:
            existing = connection.execute("SELECT COUNT(*) FROM object_versions").fetchone()[0]
            if existing:
                return
            for item in fixture.objects:
                self.insert_version(connection, item, make_current=False)
            for item in fixture.objects:
                if item.state in {ObjectState.CURRENT, ObjectState.ACTIVE}:
                    connection.execute(
                        "INSERT OR REPLACE INTO current_pointers(object_id, version_key, revision) "
                        "VALUES (?, ?, 1)",
                        (item.id, item.ref),
                    )
            self.append_event(
                connection,
                "FIXTURE_LOADED",
                {"fixture_digest": fixture.digest, "object_count": len(fixture.objects)},
            )

    def insert_version(
        self,
        connection: sqlite3.Connection,
        item: VersionedObject,
        *,
        make_current: bool,
    ) -> None:
        connection.execute(
            "INSERT INTO object_versions(version_key, object_id, version, payload_json, payload_digest) "
            "VALUES (?, ?, ?, ?, ?)",
            (item.ref, item.id, item.version, item.model_dump_json(), item.digest),
        )
        connection.execute(
            "INSERT INTO version_states(version_key, state) VALUES (?, ?)",
            (item.ref, item.state.value),
        )
        if make_current:
            connection.execute(
                "INSERT INTO current_pointers(object_id, version_key, revision) VALUES (?, ?, 1) "
                "ON CONFLICT(object_id) DO UPDATE SET version_key=excluded.version_key, "
                "revision=current_pointers.revision + 1",
                (item.id, item.ref),
            )

    def _row_to_object(self, row: sqlite3.Row) -> VersionedObject:
        payload = json.loads(row["payload_json"])
        persisted = VersionedObject.model_validate(payload)
        if persisted.digest != row["payload_digest"]:
            raise IntegrityError("stored object payload digest mismatch")
        payload["state"] = row["effective_state"]
        return VersionedObject.model_validate(payload)

    def get_object(self, object_id: str, version: str | None = None) -> VersionedObject:
        if version is None:
            row = self.connection.execute(
                "SELECT ov.payload_json, ov.payload_digest, vs.state AS effective_state "
                "FROM current_pointers cp "
                "JOIN object_versions ov ON ov.version_key=cp.version_key "
                "JOIN version_states vs ON vs.version_key=ov.version_key "
                "WHERE cp.object_id=?",
                (object_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT ov.payload_json, ov.payload_digest, vs.state AS effective_state "
                "FROM object_versions ov JOIN version_states vs USING(version_key) "
                "WHERE ov.object_id=? AND ov.version=?",
                (object_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"missing object {object_id}@{version or 'current'}")
        return self._row_to_object(row)

    def state_snapshot(self, object_ids: tuple[str, ...] | list[str]) -> dict[str, dict[str, str]]:
        snapshot: dict[str, dict[str, str]] = {}
        for object_id in object_ids:
            item = self.get_object(object_id)
            snapshot[object_id] = {
                "version": item.version,
                "state": item.state.value,
                "digest": item.digest,
            }
        return snapshot

    def _switch_pointer(
        self,
        connection: sqlite3.Connection,
        object_id: str,
        base_version: str,
        proposed_version: str,
        *,
        base_state: ObjectState,
        proposed_state: ObjectState,
    ) -> None:
        pointer = connection.execute(
            "SELECT ov.version, cp.version_key FROM current_pointers cp "
            "JOIN object_versions ov ON ov.version_key=cp.version_key WHERE cp.object_id=?",
            (object_id,),
        ).fetchone()
        if pointer is None or pointer["version"] != base_version:
            raise RuntimeError("EXPECTED_REVISION_MISMATCH")
        proposed_key = f"{object_id}@{proposed_version}"
        if (
            connection.execute(
                "SELECT 1 FROM object_versions WHERE version_key=?", (proposed_key,)
            ).fetchone()
            is None
        ):
            raise KeyError(proposed_key)
        connection.execute(
            "UPDATE version_states SET state=? WHERE version_key=?",
            (base_state.value, pointer["version_key"]),
        )
        connection.execute(
            "UPDATE version_states SET state=? WHERE version_key=?",
            (proposed_state.value, proposed_key),
        )
        connection.execute(
            "UPDATE current_pointers SET version_key=?, revision=revision+1 WHERE object_id=?",
            (proposed_key, object_id),
        )

    def promote_version(
        self,
        connection: sqlite3.Connection,
        object_id: str,
        base_version: str,
        proposed_version: str,
    ) -> None:
        self._switch_pointer(
            connection,
            object_id,
            base_version,
            proposed_version,
            base_state=ObjectState.SUPERSEDED,
            proposed_state=ObjectState.CURRENT,
        )

    def activate_version(
        self,
        connection: sqlite3.Connection,
        object_id: str,
        base_version: str,
        proposed_version: str,
        *,
        base_state: ObjectState,
        proposed_state: ObjectState,
    ) -> None:
        self._switch_pointer(
            connection,
            object_id,
            base_version,
            proposed_version,
            base_state=base_state,
            proposed_state=proposed_state,
        )

    def transition_current(
        self,
        connection: sqlite3.Connection,
        object_id: str,
        state: ObjectState,
    ) -> str:
        row = connection.execute(
            "SELECT version_key FROM current_pointers WHERE object_id=?", (object_id,)
        ).fetchone()
        if row is None:
            raise KeyError(object_id)
        connection.execute(
            "UPDATE version_states SET state=? WHERE version_key=?",
            (state.value, row["version_key"]),
        )
        return row["version_key"]

    def append_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> str:
        row = connection.execute(
            "SELECT sequence_no, event_digest FROM domain_events ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone()
        previous_digest = row["event_digest"] if row else "sha256:" + "0" * 64
        next_sequence = int(row["sequence_no"]) + 1 if row else 1
        envelope = {
            "sequence_no": next_sequence,
            "event_type": event_type,
            "payload": dict(payload),
            "previous_digest": previous_digest,
        }
        event_digest = sha256_digest(envelope)
        connection.execute(
            "INSERT INTO domain_events(sequence_no, event_type, payload_json, previous_digest, "
            "event_digest) VALUES (?, ?, ?, ?, ?)",
            (
                next_sequence,
                event_type,
                canonical_json(dict(payload)),
                previous_digest,
                event_digest,
            ),
        )
        return event_digest

    def record_event(self, event_type: str, payload: Mapping[str, Any]) -> str:
        with self.transaction() as connection:
            return self.append_event(connection, event_type, payload)

    def verify_event_chain(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT * FROM domain_events ORDER BY sequence_no").fetchall()
        previous = "sha256:" + "0" * 64
        for expected_sequence, row in enumerate(rows, start=1):
            payload = json.loads(row["payload_json"])
            envelope = {
                "sequence_no": expected_sequence,
                "event_type": row["event_type"],
                "payload": payload,
                "previous_digest": previous,
            }
            expected = sha256_digest(envelope)
            if (
                row["sequence_no"] != expected_sequence
                or row["previous_digest"] != previous
                or row["event_digest"] != expected
            ):
                raise IntegrityError(f"event chain failed at sequence {expected_sequence}")
            previous = expected
        return {"status": "PASS", "events": len(rows), "head_digest": previous}

    def event_records(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT sequence_no, event_type, previous_digest, event_digest "
            "FROM domain_events ORDER BY sequence_no"
        ).fetchall()
        return tuple(
            {
                "sequence_no": int(row["sequence_no"]),
                "event_type": row["event_type"],
                "previous_digest": row["previous_digest"],
                "event_digest": row["event_digest"],
            }
            for row in rows
        )

    def get_idempotent(self, key: str, request_digest: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT request_digest, result_json FROM idempotency_records WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != request_digest:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        return json.loads(row["result_json"])

    def save_idempotent(
        self,
        connection: sqlite3.Connection,
        key: str,
        request_digest: str,
        result: Mapping[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO idempotency_records(key, request_digest, result_json) VALUES (?, ?, ?)",
            (key, request_digest, canonical_json(dict(result))),
        )

    def save_artifact(
        self,
        connection: sqlite3.Connection,
        artifact_id: str,
        media_type: str,
        payload: Mapping[str, Any],
    ) -> str:
        payload_json = canonical_json(dict(payload))
        digest = sha256_digest(dict(payload))
        existing = connection.execute(
            "SELECT media_type, payload_json, payload_digest FROM artifacts "
            "WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["media_type"] != media_type
                or existing["payload_json"] != payload_json
                or existing["payload_digest"] != digest
            ):
                raise IntegrityError(f"ARTIFACT_ID_CONFLICT:{artifact_id}")
            return digest
        connection.execute(
            "INSERT INTO artifacts(artifact_id, media_type, payload_json, payload_digest) "
            "VALUES (?, ?, ?, ?)",
            (artifact_id, media_type, payload_json, digest),
        )
        return digest


    def save_artifact_writes(
        self,
        connection: sqlite3.Connection,
        writes: tuple["ArtifactWrite", ...],
    ) -> tuple[str, ...]:
        """Save a prepared immutable artifact bundle inside the caller transaction."""

        from orgrebase.workspace.models import ArtifactWrite

        digests: list[str] = []
        for write in writes:
            if not isinstance(write, ArtifactWrite):
                raise TypeError("artifact bundle contains an invalid write")
            if sha256_digest(write.payload) != write.payload_digest:
                raise IntegrityError(f"ARTIFACT_PREPARED_DIGEST_MISMATCH:{write.artifact_id}")
            digests.append(
                self.save_artifact(
                    connection,
                    write.artifact_id,
                    write.media_type,
                    write.payload,
                )
            )
        return tuple(digests)

    def get_pointer(self, object_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT cp.version_key, cp.revision, ov.version, ov.payload_digest "
            "FROM current_pointers cp JOIN object_versions ov ON ov.version_key=cp.version_key "
            "WHERE cp.object_id=?",
            (object_id,),
        ).fetchone()
        if row is None:
            raise KeyError(object_id)
        return {
            "object_id": object_id,
            "version_key": row["version_key"],
            "version": row["version"],
            "revision": int(row["revision"]),
            "payload_digest": row["payload_digest"],
        }

    def load_artifact(
        self,
        artifact_id: str,
        expected_media_type: str | None = None,
    ) -> "StoredArtifact":
        """Verified exact-ID artifact read with canonical round-trip validation."""

        from orgrebase.workspace.models import StoredArtifact

        row = self.connection.execute(
            "SELECT media_type, payload_json, payload_digest FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"ARTIFACT_NOT_FOUND:{artifact_id}")
        if expected_media_type is not None and row["media_type"] != expected_media_type:
            raise IntegrityError(f"ARTIFACT_MEDIA_TYPE_MISMATCH:{artifact_id}")
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"ARTIFACT_PAYLOAD_INVALID:{artifact_id}") from exc
        if not isinstance(payload, dict):
            raise IntegrityError(f"ARTIFACT_PAYLOAD_INVALID:{artifact_id}")
        if canonical_json(payload) != row["payload_json"]:
            raise IntegrityError(f"ARTIFACT_PAYLOAD_INVALID:{artifact_id}")
        digest = sha256_digest(payload)
        if digest != row["payload_digest"]:
            raise IntegrityError(f"ARTIFACT_DIGEST_MISMATCH:{artifact_id}")
        # JSON round-trip ensures callers cannot mutate a shared in-memory object.
        copied = json.loads(canonical_json(payload))
        return StoredArtifact(
            artifact_id=artifact_id,
            media_type=row["media_type"],
            payload=copied,
            payload_digest=digest,
        )

    def artifact_exists(self, artifact_id: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            is not None
        )

    def prepare_external_operation(
        self,
        connection: sqlite3.Connection,
        *,
        operation_key: str,
        request_digest: str,
        operation: str,
        repository_id: str,
        expected_external_parent: str,
    ) -> None:
        existing = connection.execute(
            "SELECT request_digest FROM external_operation_journal WHERE operation_key=?",
            (operation_key,),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != request_digest:
                raise RuntimeError("IDEMPOTENCY_CONFLICT")
            return
        connection.execute(
            "INSERT INTO external_operation_journal("
            "operation_key, request_digest, operation, repository_id, status, "
            "expected_external_parent) VALUES (?, ?, ?, ?, 'PREPARED', ?)",
            (
                operation_key,
                request_digest,
                operation,
                repository_id,
                expected_external_parent,
            ),
        )

    def update_external_operation(
        self,
        connection: sqlite3.Connection,
        *,
        operation_key: str,
        status: str,
        external_operation_id: str | None = None,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        changed = connection.execute(
            "UPDATE external_operation_journal SET status=?, external_operation_id=?, "
            "result_json=?, error_code=? WHERE operation_key=?",
            (
                status,
                external_operation_id,
                canonical_json(dict(result)) if result is not None else None,
                error_code,
                operation_key,
            ),
        ).rowcount
        if changed != 1:
            raise KeyError(operation_key)

    def get_external_operation(self, operation_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM external_operation_journal WHERE operation_key=?",
            (operation_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "operation_key": row["operation_key"],
            "request_digest": row["request_digest"],
            "operation": row["operation"],
            "repository_id": row["repository_id"],
            "status": row["status"],
            "expected_external_parent": row["expected_external_parent"],
            "external_operation_id": row["external_operation_id"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_code": row["error_code"],
        }

    def get_compensation_saga(
        self, saga_key: str, request_digest: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT request_digest, status, attempt, receipt_json "
            "FROM compensation_sagas WHERE saga_key=?",
            (saga_key,),
        ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != request_digest:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        return {
            "status": row["status"],
            "attempt": int(row["attempt"]),
            "receipt": json.loads(row["receipt_json"]),
        }

    def save_compensation_saga(
        self,
        connection: sqlite3.Connection,
        *,
        saga_key: str,
        request_digest: str,
        status: str,
        attempt: int,
        receipt: Mapping[str, Any],
    ) -> None:
        existing = connection.execute(
            "SELECT request_digest FROM compensation_sagas WHERE saga_key=?",
            (saga_key,),
        ).fetchone()
        if existing is not None and existing["request_digest"] != request_digest:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        connection.execute(
            "INSERT INTO compensation_sagas(saga_key, request_digest, status, attempt, "
            "receipt_json) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(saga_key) DO UPDATE SET status=excluded.status, "
            "attempt=excluded.attempt, receipt_json=excluded.receipt_json",
            (saga_key, request_digest, status, attempt, canonical_json(dict(receipt))),
        )
