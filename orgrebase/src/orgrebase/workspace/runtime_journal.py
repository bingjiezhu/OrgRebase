"""File-backed write-ahead journal for recoverable runtime actions.

The journal is deliberately smaller than the workspace control plane.  It does
not decide whether an action is allowed and it never becomes business truth.
Callers persist an ``INTENT`` before performing a side effect, then persist
``COMMITTED`` only after that side effect has an exact result.  A supervisor can
open the same SQLite file after a process death and either retry the unfinished
intent with a new fencing token or adopt the already committed result.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.local_storage import prepare_private_sqlite_path, require_safe_sqlite_wal_runtime

ZERO_DIGEST = "sha256:" + "0" * 64
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RuntimeJournalError(RuntimeError):
    """Base class for durable runtime journal failures."""


class RuntimeJournalConflict(RuntimeJournalError):
    """An idempotency key was reused for different content."""


class RuntimeJournalNotFound(RuntimeJournalError):
    """The requested run or action does not exist."""


class StaleRuntimeAttempt(RuntimeJournalError):
    """An inactive attempt tried to checkpoint or commit."""


class RuntimeJournalIntegrityError(RuntimeJournalError):
    """Persisted journal content failed its content-addressed invariants."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_REQUIRED")
    return value


def _require_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field}_INVALID")
    return value


def _json_object(value: Mapping[str, Any], field: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field}_MAPPING_REQUIRED")
    payload = dict(value)
    try:
        encoded = canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_NOT_CANONICAL_JSON") from exc
    return encoded, payload


def _decode_object(value: str, field: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeJournalIntegrityError(f"{field}_JSON_INVALID") from exc
    if not isinstance(decoded, dict):
        raise RuntimeJournalIntegrityError(f"{field}_OBJECT_REQUIRED")
    return decoded


class RuntimeJournal:
    """Minimal durable action/attempt/checkpoint journal.

    ``begin_action`` is idempotent for ``(run_id, action_key,
    request_digest)``.  After an uncertain process exit, call
    ``recover_action``.  ``RETRY_INTENT`` always rotates the attempt and
    fencing token; ``ADOPT_COMMITTED`` returns the exact stored result without
    executing it again.

    The caller remains responsible for making the external operation safe to
    retry.  The journal prevents stale workers from committing, but cannot make
    an arbitrary remote side effect transactional with SQLite.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("RUNTIME_JOURNAL_FILE_BACKED_PATH_REQUIRED")
        require_safe_sqlite_wal_runtime()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(prepare_private_sqlite_path(self.path))
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self._create_schema()

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._closed = True

    def __enter__(self) -> RuntimeJournal:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive resource cleanup
        with suppress(Exception):
            self.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_runs (
                run_id TEXT PRIMARY KEY,
                head_action_digest TEXT NOT NULL,
                next_action_sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runtime_actions (
                run_id TEXT NOT NULL REFERENCES runtime_runs(run_id),
                action_key TEXT NOT NULL,
                action_type TEXT NOT NULL,
                request_json TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('INTENT', 'COMMITTED')),
                active_attempt INTEGER NOT NULL CHECK (active_attempt >= 1),
                active_fencing_token TEXT NOT NULL,
                result_json TEXT,
                result_digest TEXT,
                latest_receipt_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, action_key),
                CHECK (
                    (state = 'INTENT' AND result_json IS NULL AND result_digest IS NULL)
                    OR
                    (state = 'COMMITTED' AND result_json IS NOT NULL AND result_digest IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS runtime_attempts (
                run_id TEXT NOT NULL,
                action_key TEXT NOT NULL,
                attempt INTEGER NOT NULL CHECK (attempt >= 1),
                fencing_token TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'FENCED', 'COMMITTED')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                PRIMARY KEY (run_id, action_key, attempt),
                UNIQUE (fencing_token),
                FOREIGN KEY (run_id, action_key)
                    REFERENCES runtime_actions(run_id, action_key)
            );

            CREATE TABLE IF NOT EXISTS runtime_checkpoints (
                run_id TEXT NOT NULL,
                action_key TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                checkpoint_sequence INTEGER NOT NULL CHECK (checkpoint_sequence >= 1),
                checkpoint_key TEXT NOT NULL,
                previous_checkpoint_digest TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL,
                checkpoint_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, action_key, attempt, checkpoint_key),
                UNIQUE (run_id, action_key, attempt, checkpoint_sequence),
                FOREIGN KEY (run_id, action_key, attempt)
                    REFERENCES runtime_attempts(run_id, action_key, attempt)
            );

            CREATE TABLE IF NOT EXISTS runtime_action_receipts (
                run_id TEXT NOT NULL REFERENCES runtime_runs(run_id),
                sequence INTEGER NOT NULL CHECK (sequence >= 1),
                action_key TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('INTENT', 'COMMITTED')),
                previous_action_digest TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                action_digest TEXT NOT NULL UNIQUE,
                PRIMARY KEY (run_id, sequence),
                FOREIGN KEY (run_id, action_key)
                    REFERENCES runtime_actions(run_id, action_key)
            );

            CREATE INDEX IF NOT EXISTS runtime_receipts_by_action
                ON runtime_action_receipts(run_id, action_key, sequence);
            CREATE INDEX IF NOT EXISTS runtime_checkpoints_by_attempt
                ON runtime_checkpoints(run_id, action_key, attempt, checkpoint_sequence);
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise RuntimeJournalError("RUNTIME_JOURNAL_CLOSED")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @staticmethod
    def _action(
        connection: sqlite3.Connection, run_id: str, action_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM runtime_actions WHERE run_id=? AND action_key=?",
            (run_id, action_key),
        ).fetchone()

    @staticmethod
    def _ensure_run(connection: sqlite3.Connection, run_id: str, created_at: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO runtime_runs("
            "run_id, head_action_digest, next_action_sequence, created_at, updated_at"
            ") VALUES (?, ?, 1, ?, ?)",
            (run_id, ZERO_DIGEST, created_at, created_at),
        )

    @staticmethod
    def _latest_receipt(
        connection: sqlite3.Connection, run_id: str, action_key: str
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT receipt_json FROM runtime_action_receipts "
            "WHERE run_id=? AND action_key=? ORDER BY sequence DESC LIMIT 1",
            (run_id, action_key),
        ).fetchone()
        if row is None:
            raise RuntimeJournalIntegrityError("ACTION_RECEIPT_MISSING")
        return _decode_object(row["receipt_json"], "ACTION_RECEIPT")

    @staticmethod
    def _request_matches(row: sqlite3.Row, request_digest: str) -> None:
        if row["request_digest"] != request_digest:
            raise RuntimeJournalConflict(
                f"ACTION_IDEMPOTENCY_CONFLICT:{row['run_id']}:{row['action_key']}"
            )

    @staticmethod
    def _fencing_token(
        *,
        run_id: str,
        action_key: str,
        request_digest: str,
        attempt: int,
        previous_action_digest: str,
    ) -> str:
        return sha256_digest(
            {
                "schema_version": "orgrebase.runtime-fencing-token.v1",
                "run_id": run_id,
                "action_key": action_key,
                "request_digest": request_digest,
                "attempt": attempt,
                "previous_action_digest": previous_action_digest,
            }
        )

    @staticmethod
    def _active_attempt(
        row: sqlite3.Row, attempt: int, fencing_token: str, *, allow_committed: bool = False
    ) -> None:
        _require_digest(fencing_token, "FENCING_TOKEN")
        if row["active_attempt"] != attempt or row["active_fencing_token"] != fencing_token:
            raise StaleRuntimeAttempt(
                f"STALE_RUNTIME_ATTEMPT:{row['run_id']}:{row['action_key']}:{attempt}"
            )
        if row["state"] == "COMMITTED" and not allow_committed:
            raise RuntimeJournalConflict(
                f"ACTION_ALREADY_COMMITTED:{row['run_id']}:{row['action_key']}"
            )

    @staticmethod
    def _append_receipt(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        state: str,
        transition: str,
        attempt: int,
        fencing_token: str,
        checkpoint_digest: str | None,
        result_digest: str | None,
        recovery_disposition: str | None,
        created_at: str,
    ) -> dict[str, Any]:
        run = connection.execute(
            "SELECT head_action_digest, next_action_sequence FROM runtime_runs WHERE run_id=?",
            (row["run_id"],),
        ).fetchone()
        if run is None:
            raise RuntimeJournalIntegrityError("RUNTIME_RUN_MISSING")
        sequence = int(run["next_action_sequence"])
        base: dict[str, Any] = {
            "schema_version": "orgrebase.workspace-runtime-action-receipt.v1",
            "receipt_id": f"runtime-action:{row['run_id']}:{sequence}",
            "sequence": sequence,
            "run_id": row["run_id"],
            "action_key": row["action_key"],
            "action_type": row["action_type"],
            "request_digest": row["request_digest"],
            "state": state,
            "transition": transition,
            "attempt": attempt,
            "fencing_token": fencing_token,
            "checkpoint_digest": checkpoint_digest,
            "result_digest": result_digest,
            "recovery_disposition": recovery_disposition,
            "previous_action_digest": run["head_action_digest"],
            "created_at": created_at,
        }
        receipt = {**base, "digest": sha256_digest(base)}
        connection.execute(
            "INSERT INTO runtime_action_receipts("
            "run_id, sequence, action_key, state, previous_action_digest, receipt_json, action_digest"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["run_id"],
                sequence,
                row["action_key"],
                state,
                base["previous_action_digest"],
                canonical_json(receipt),
                receipt["digest"],
            ),
        )
        connection.execute(
            "UPDATE runtime_runs SET head_action_digest=?, next_action_sequence=?, updated_at=? "
            "WHERE run_id=?",
            (receipt["digest"], sequence + 1, created_at, row["run_id"]),
        )
        return receipt

    def begin_action(
        self,
        *,
        run_id: str,
        action_key: str,
        action_type: str,
        request: Mapping[str, Any],
        request_digest: str | None = None,
    ) -> dict[str, Any]:
        """Persist an INTENT and return its attempt-bound action receipt.

        Reusing the same action key and exact request is a read-only idempotent
        replay.  Reusing the key for different content is always rejected.
        """

        run_id = _require_text(run_id, "RUN_ID")
        action_key = _require_text(action_key, "ACTION_KEY")
        action_type = _require_text(action_type, "ACTION_TYPE")
        request_json, request_object = _json_object(request, "REQUEST")
        computed_request_digest = sha256_digest(request_object)
        if request_digest is not None:
            _require_digest(request_digest, "REQUEST_DIGEST")
            if request_digest != computed_request_digest:
                raise RuntimeJournalConflict("REQUEST_DIGEST_MISMATCH")
        request_digest = computed_request_digest
        created_at = _now()
        with self._transaction() as connection:
            self._ensure_run(connection, run_id, created_at)
            existing = self._action(connection, run_id, action_key)
            if existing is not None:
                self._request_matches(existing, request_digest)
                if existing["action_type"] != action_type:
                    raise RuntimeJournalConflict(
                        f"ACTION_TYPE_CONFLICT:{run_id}:{action_key}"
                    )
                if existing["request_json"] != request_json:
                    raise RuntimeJournalIntegrityError("REQUEST_DIGEST_COLLISION")
                return self._latest_receipt(connection, run_id, action_key)

            run = connection.execute(
                "SELECT head_action_digest FROM runtime_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise RuntimeJournalIntegrityError("RUNTIME_RUN_MISSING")
            attempt = 1
            fencing_token = self._fencing_token(
                run_id=run_id,
                action_key=action_key,
                request_digest=request_digest,
                attempt=attempt,
                previous_action_digest=run["head_action_digest"],
            )
            connection.execute(
                "INSERT INTO runtime_actions("
                "run_id, action_key, action_type, request_json, request_digest, state, "
                "active_attempt, active_fencing_token, result_json, result_digest, "
                "latest_receipt_digest, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, 'INTENT', ?, ?, NULL, NULL, ?, ?, ?)",
                (
                    run_id,
                    action_key,
                    action_type,
                    request_json,
                    request_digest,
                    attempt,
                    fencing_token,
                    ZERO_DIGEST,
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO runtime_attempts("
                "run_id, action_key, attempt, fencing_token, status, started_at, finished_at"
                ") VALUES (?, ?, ?, ?, 'ACTIVE', ?, NULL)",
                (run_id, action_key, attempt, fencing_token, created_at),
            )
            row = self._action(connection, run_id, action_key)
            if row is None:
                raise RuntimeJournalIntegrityError("RUNTIME_ACTION_INSERT_FAILED")
            receipt = self._append_receipt(
                connection,
                row=row,
                state="INTENT",
                transition="INTENT_STARTED",
                attempt=attempt,
                fencing_token=fencing_token,
                checkpoint_digest=None,
                result_digest=None,
                recovery_disposition=None,
                created_at=created_at,
            )
            connection.execute(
                "UPDATE runtime_actions SET latest_receipt_digest=?, updated_at=? "
                "WHERE run_id=? AND action_key=?",
                (receipt["digest"], created_at, run_id, action_key),
            )
            return receipt

    def recover_action(
        self, *, run_id: str, action_key: str, request_digest: str
    ) -> dict[str, Any]:
        """Fence an unfinished attempt or adopt an exact committed result."""

        run_id = _require_text(run_id, "RUN_ID")
        action_key = _require_text(action_key, "ACTION_KEY")
        request_digest = _require_digest(request_digest, "REQUEST_DIGEST")
        created_at = _now()
        with self._transaction() as connection:
            row = self._action(connection, run_id, action_key)
            if row is None:
                raise RuntimeJournalNotFound(f"ACTION_NOT_FOUND:{run_id}:{action_key}")
            self._request_matches(row, request_digest)
            checkpoint = self._latest_checkpoint(connection, run_id, action_key)
            if row["state"] == "COMMITTED":
                receipt = self._latest_receipt(connection, run_id, action_key)
                result = _decode_object(row["result_json"], "ACTION_RESULT")
                return self._recovery_decision(
                    disposition="ADOPT_COMMITTED",
                    row=row,
                    receipt=receipt,
                    checkpoint=checkpoint,
                    result=result,
                )

            previous_attempt = int(row["active_attempt"])
            connection.execute(
                "UPDATE runtime_attempts SET status='FENCED', finished_at=? "
                "WHERE run_id=? AND action_key=? AND attempt=? AND status='ACTIVE'",
                (created_at, run_id, action_key, previous_attempt),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeJournalIntegrityError("ACTIVE_ATTEMPT_MISSING")
            run = connection.execute(
                "SELECT head_action_digest FROM runtime_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise RuntimeJournalIntegrityError("RUNTIME_RUN_MISSING")
            attempt = previous_attempt + 1
            fencing_token = self._fencing_token(
                run_id=run_id,
                action_key=action_key,
                request_digest=request_digest,
                attempt=attempt,
                previous_action_digest=run["head_action_digest"],
            )
            connection.execute(
                "INSERT INTO runtime_attempts("
                "run_id, action_key, attempt, fencing_token, status, started_at, finished_at"
                ") VALUES (?, ?, ?, ?, 'ACTIVE', ?, NULL)",
                (run_id, action_key, attempt, fencing_token, created_at),
            )
            connection.execute(
                "UPDATE runtime_actions SET active_attempt=?, active_fencing_token=?, updated_at=? "
                "WHERE run_id=? AND action_key=?",
                (attempt, fencing_token, created_at, run_id, action_key),
            )
            row = self._action(connection, run_id, action_key)
            if row is None:
                raise RuntimeJournalIntegrityError("RUNTIME_ACTION_MISSING")
            receipt = self._append_receipt(
                connection,
                row=row,
                state="INTENT",
                transition="INTENT_RECOVERED",
                attempt=attempt,
                fencing_token=fencing_token,
                checkpoint_digest=(checkpoint["digest"] if checkpoint is not None else None),
                result_digest=None,
                recovery_disposition="RETRY_INTENT",
                created_at=created_at,
            )
            connection.execute(
                "UPDATE runtime_actions SET latest_receipt_digest=?, updated_at=? "
                "WHERE run_id=? AND action_key=?",
                (receipt["digest"], created_at, run_id, action_key),
            )
            row = self._action(connection, run_id, action_key)
            if row is None:
                raise RuntimeJournalIntegrityError("RUNTIME_ACTION_MISSING")
            return self._recovery_decision(
                disposition="RETRY_INTENT",
                row=row,
                receipt=receipt,
                checkpoint=checkpoint,
                result=None,
            )

    @staticmethod
    def _recovery_decision(
        *,
        disposition: str,
        row: sqlite3.Row,
        receipt: Mapping[str, Any],
        checkpoint: Mapping[str, Any] | None,
        result: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "schema_version": "orgrebase.runtime-recovery-decision.v1",
            "run_id": row["run_id"],
            "action_key": row["action_key"],
            "request_digest": row["request_digest"],
            "disposition": disposition,
            "state": row["state"],
            "attempt": row["active_attempt"],
            "fencing_token": row["active_fencing_token"],
            "checkpoint": dict(checkpoint) if checkpoint is not None else None,
            "result": dict(result) if result is not None else None,
            "action_receipt": dict(receipt),
        }
        return {**base, "digest": sha256_digest(base)}

    def write_checkpoint(
        self,
        *,
        run_id: str,
        action_key: str,
        attempt: int,
        fencing_token: str,
        checkpoint_key: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one attempt-local, idempotently named checkpoint."""

        run_id = _require_text(run_id, "RUN_ID")
        action_key = _require_text(action_key, "ACTION_KEY")
        checkpoint_key = _require_text(checkpoint_key, "CHECKPOINT_KEY")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError("ATTEMPT_INVALID")
        fencing_token = _require_digest(fencing_token, "FENCING_TOKEN")
        _, payload_object = _json_object(payload, "CHECKPOINT")
        created_at = _now()
        with self._transaction() as connection:
            row = self._action(connection, run_id, action_key)
            if row is None:
                raise RuntimeJournalNotFound(f"ACTION_NOT_FOUND:{run_id}:{action_key}")
            self._active_attempt(row, attempt, fencing_token)
            existing = connection.execute(
                "SELECT checkpoint_json FROM runtime_checkpoints "
                "WHERE run_id=? AND action_key=? AND attempt=? AND checkpoint_key=?",
                (run_id, action_key, attempt, checkpoint_key),
            ).fetchone()
            if existing is not None:
                checkpoint = _decode_object(existing["checkpoint_json"], "CHECKPOINT")
                if checkpoint["payload"] != payload_object:
                    raise RuntimeJournalConflict(
                        f"CHECKPOINT_IDEMPOTENCY_CONFLICT:{run_id}:{action_key}:{checkpoint_key}"
                    )
                return checkpoint
            previous = connection.execute(
                "SELECT checkpoint_sequence, checkpoint_digest FROM runtime_checkpoints "
                "WHERE run_id=? AND action_key=? AND attempt=? "
                "ORDER BY checkpoint_sequence DESC LIMIT 1",
                (run_id, action_key, attempt),
            ).fetchone()
            sequence = int(previous["checkpoint_sequence"]) + 1 if previous else 1
            previous_digest = previous["checkpoint_digest"] if previous else ZERO_DIGEST
            base: dict[str, Any] = {
                "schema_version": "orgrebase.runtime-checkpoint.v1",
                "run_id": run_id,
                "action_key": action_key,
                "attempt": attempt,
                "fencing_token": fencing_token,
                "checkpoint_sequence": sequence,
                "checkpoint_key": checkpoint_key,
                "payload": payload_object,
                "previous_checkpoint_digest": previous_digest,
                "created_at": created_at,
            }
            checkpoint = {**base, "digest": sha256_digest(base)}
            connection.execute(
                "INSERT INTO runtime_checkpoints("
                "run_id, action_key, attempt, checkpoint_sequence, checkpoint_key, "
                "previous_checkpoint_digest, checkpoint_json, checkpoint_digest, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    action_key,
                    attempt,
                    sequence,
                    checkpoint_key,
                    previous_digest,
                    canonical_json(checkpoint),
                    checkpoint["digest"],
                    created_at,
                ),
            )
            return checkpoint

    def commit_action(
        self,
        *,
        run_id: str,
        action_key: str,
        attempt: int,
        fencing_token: str,
        result: Mapping[str, Any],
        result_digest: str | None = None,
    ) -> dict[str, Any]:
        """Append COMMITTED for the active fenced attempt.

        An exact replay by the winning attempt returns the prior receipt.  A
        different result or a late attempt fails closed.
        """

        run_id = _require_text(run_id, "RUN_ID")
        action_key = _require_text(action_key, "ACTION_KEY")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError("ATTEMPT_INVALID")
        fencing_token = _require_digest(fencing_token, "FENCING_TOKEN")
        result_json, result_object = _json_object(result, "RESULT")
        computed_result_digest = sha256_digest(result_object)
        if result_digest is not None:
            _require_digest(result_digest, "RESULT_DIGEST")
            if result_digest != computed_result_digest:
                raise RuntimeJournalConflict("RESULT_DIGEST_MISMATCH")
        created_at = _now()
        with self._transaction() as connection:
            row = self._action(connection, run_id, action_key)
            if row is None:
                raise RuntimeJournalNotFound(f"ACTION_NOT_FOUND:{run_id}:{action_key}")
            self._active_attempt(row, attempt, fencing_token, allow_committed=True)
            if row["state"] == "COMMITTED":
                if (
                    row["result_digest"] != computed_result_digest
                    or row["result_json"] != result_json
                ):
                    raise RuntimeJournalConflict(
                        f"COMMITTED_RESULT_CONFLICT:{run_id}:{action_key}"
                    )
                return self._latest_receipt(connection, run_id, action_key)
            checkpoint = self._latest_checkpoint(
                connection, run_id, action_key, attempt=attempt
            )
            receipt = self._append_receipt(
                connection,
                row=row,
                state="COMMITTED",
                transition="ACTION_COMMITTED",
                attempt=attempt,
                fencing_token=fencing_token,
                checkpoint_digest=(checkpoint["digest"] if checkpoint is not None else None),
                result_digest=computed_result_digest,
                recovery_disposition=None,
                created_at=created_at,
            )
            connection.execute(
                "UPDATE runtime_attempts SET status='COMMITTED', finished_at=? "
                "WHERE run_id=? AND action_key=? AND attempt=? AND status='ACTIVE'",
                (created_at, run_id, action_key, attempt),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeJournalIntegrityError("ACTIVE_ATTEMPT_MISSING")
            connection.execute(
                "UPDATE runtime_actions SET state='COMMITTED', result_json=?, result_digest=?, "
                "latest_receipt_digest=?, updated_at=? WHERE run_id=? AND action_key=?",
                (
                    result_json,
                    computed_result_digest,
                    receipt["digest"],
                    created_at,
                    run_id,
                    action_key,
                ),
            )
            return receipt

    @staticmethod
    def _latest_checkpoint(
        connection: sqlite3.Connection,
        run_id: str,
        action_key: str,
        *,
        attempt: int | None = None,
    ) -> dict[str, Any] | None:
        if attempt is None:
            row = connection.execute(
                "SELECT checkpoint_json FROM runtime_checkpoints "
                "WHERE run_id=? AND action_key=? "
                "ORDER BY attempt DESC, checkpoint_sequence DESC LIMIT 1",
                (run_id, action_key),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT checkpoint_json FROM runtime_checkpoints "
                "WHERE run_id=? AND action_key=? AND attempt=? "
                "ORDER BY checkpoint_sequence DESC LIMIT 1",
                (run_id, action_key, attempt),
            ).fetchone()
        if row is None:
            return None
        return _decode_object(row["checkpoint_json"], "CHECKPOINT")

    def get_action(self, *, run_id: str, action_key: str) -> dict[str, Any] | None:
        """Return the current projection plus immutable evidence references."""

        run_id = _require_text(run_id, "RUN_ID")
        action_key = _require_text(action_key, "ACTION_KEY")
        with self._transaction() as connection:
            row = self._action(connection, run_id, action_key)
            if row is None:
                return None
            return {
                "run_id": run_id,
                "action_key": action_key,
                "action_type": row["action_type"],
                "request": _decode_object(row["request_json"], "ACTION_REQUEST"),
                "request_digest": row["request_digest"],
                "state": row["state"],
                "active_attempt": row["active_attempt"],
                "active_fencing_token": row["active_fencing_token"],
                "result": (
                    _decode_object(row["result_json"], "ACTION_RESULT")
                    if row["result_json"] is not None
                    else None
                ),
                "result_digest": row["result_digest"],
                "latest_checkpoint": self._latest_checkpoint(
                    connection, run_id, action_key
                ),
                "latest_receipt": self._latest_receipt(connection, run_id, action_key),
            }

    def list_action_receipts(self, run_id: str) -> list[dict[str, Any]]:
        """Return the immutable receipt chain in run sequence order."""

        run_id = _require_text(run_id, "RUN_ID")
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT 1 FROM runtime_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise RuntimeJournalNotFound(f"RUN_NOT_FOUND:{run_id}")
            rows = connection.execute(
                "SELECT receipt_json FROM runtime_action_receipts "
                "WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            return [_decode_object(row["receipt_json"], "ACTION_RECEIPT") for row in rows]

    def verify_chain(self, run_id: str) -> dict[str, Any]:
        """Verify action receipts, attempts, checkpoints, and current projections."""

        run_id = _require_text(run_id, "RUN_ID")
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise RuntimeJournalNotFound(f"RUN_NOT_FOUND:{run_id}")
            receipt_rows = connection.execute(
                "SELECT * FROM runtime_action_receipts WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            previous = ZERO_DIGEST
            latest_by_action: dict[str, dict[str, Any]] = {}
            intent_receipts: dict[tuple[str, int], dict[str, Any]] = {}
            for expected_sequence, receipt_row in enumerate(receipt_rows, start=1):
                receipt = _decode_object(receipt_row["receipt_json"], "ACTION_RECEIPT")
                base = {key: value for key, value in receipt.items() if key != "digest"}
                if (
                    receipt_row["sequence"] != expected_sequence
                    or receipt.get("sequence") != expected_sequence
                    or receipt.get("run_id") != run_id
                    or receipt.get("action_key") != receipt_row["action_key"]
                    or receipt.get("state") != receipt_row["state"]
                    or receipt.get("previous_action_digest") != previous
                    or receipt_row["previous_action_digest"] != previous
                    or receipt.get("digest") != receipt_row["action_digest"]
                    or sha256_digest(base) != receipt.get("digest")
                ):
                    raise RuntimeJournalIntegrityError(
                        f"ACTION_RECEIPT_CHAIN_INVALID:{run_id}:{expected_sequence}"
                    )
                previous = receipt["digest"]
                latest_by_action[receipt["action_key"]] = receipt
                if receipt["state"] == "INTENT":
                    intent_receipts[(receipt["action_key"], receipt["attempt"])] = receipt
            if (
                previous != run["head_action_digest"]
                or run["next_action_sequence"] != len(receipt_rows) + 1
            ):
                raise RuntimeJournalIntegrityError("RUN_ACTION_HEAD_INVALID")

            action_rows = connection.execute(
                "SELECT * FROM runtime_actions WHERE run_id=? ORDER BY action_key", (run_id,)
            ).fetchall()
            attempt_count = 0
            for action in action_rows:
                request = _decode_object(action["request_json"], "ACTION_REQUEST")
                latest = latest_by_action.get(action["action_key"])
                if (
                    sha256_digest(request) != action["request_digest"]
                    or latest is None
                    or latest["digest"] != action["latest_receipt_digest"]
                    or latest["state"] != action["state"]
                    or latest["attempt"] != action["active_attempt"]
                    or latest["fencing_token"] != action["active_fencing_token"]
                ):
                    raise RuntimeJournalIntegrityError(
                        f"ACTION_PROJECTION_INVALID:{run_id}:{action['action_key']}"
                    )
                if action["state"] == "COMMITTED":
                    result = _decode_object(action["result_json"], "ACTION_RESULT")
                    if (
                        sha256_digest(result) != action["result_digest"]
                        or latest["result_digest"] != action["result_digest"]
                    ):
                        raise RuntimeJournalIntegrityError(
                            f"ACTION_RESULT_INVALID:{run_id}:{action['action_key']}"
                        )
                elif action["result_json"] is not None or action["result_digest"] is not None:
                    raise RuntimeJournalIntegrityError(
                        f"INTENT_RESULT_PRESENT:{run_id}:{action['action_key']}"
                    )
                attempts = connection.execute(
                    "SELECT * FROM runtime_attempts WHERE run_id=? AND action_key=? ORDER BY attempt",
                    (run_id, action["action_key"]),
                ).fetchall()
                attempt_count += len(attempts)
                if len(attempts) != action["active_attempt"]:
                    raise RuntimeJournalIntegrityError("ATTEMPT_SEQUENCE_INVALID")
                for expected_attempt, attempt_row in enumerate(attempts, start=1):
                    intent = intent_receipts.get((action["action_key"], expected_attempt))
                    if (
                        attempt_row["attempt"] != expected_attempt
                        or intent is None
                        or intent["fencing_token"] != attempt_row["fencing_token"]
                        or self._fencing_token(
                            run_id=run_id,
                            action_key=action["action_key"],
                            request_digest=action["request_digest"],
                            attempt=expected_attempt,
                            previous_action_digest=intent["previous_action_digest"],
                        )
                        != attempt_row["fencing_token"]
                    ):
                        raise RuntimeJournalIntegrityError("ATTEMPT_FENCING_CHAIN_INVALID")
                    expected_status = "FENCED"
                    if expected_attempt == action["active_attempt"]:
                        expected_status = (
                            "COMMITTED" if action["state"] == "COMMITTED" else "ACTIVE"
                        )
                    if attempt_row["status"] != expected_status:
                        raise RuntimeJournalIntegrityError("ATTEMPT_STATUS_INVALID")

            checkpoint_rows = connection.execute(
                "SELECT * FROM runtime_checkpoints WHERE run_id=? "
                "ORDER BY action_key, attempt, checkpoint_sequence",
                (run_id,),
            ).fetchall()
            checkpoint_heads: dict[tuple[str, int], str] = {}
            checkpoint_sequences: dict[tuple[str, int], int] = {}
            for checkpoint_row in checkpoint_rows:
                checkpoint = _decode_object(checkpoint_row["checkpoint_json"], "CHECKPOINT")
                group = (checkpoint_row["action_key"], checkpoint_row["attempt"])
                expected_sequence = checkpoint_sequences.get(group, 0) + 1
                expected_previous = checkpoint_heads.get(group, ZERO_DIGEST)
                base = {key: value for key, value in checkpoint.items() if key != "digest"}
                if (
                    checkpoint.get("run_id") != run_id
                    or checkpoint.get("action_key") != checkpoint_row["action_key"]
                    or checkpoint.get("attempt") != checkpoint_row["attempt"]
                    or checkpoint_row["checkpoint_sequence"] != expected_sequence
                    or checkpoint.get("checkpoint_sequence") != expected_sequence
                    or checkpoint.get("checkpoint_key") != checkpoint_row["checkpoint_key"]
                    or checkpoint.get("previous_checkpoint_digest") != expected_previous
                    or checkpoint_row["previous_checkpoint_digest"] != expected_previous
                    or checkpoint.get("digest") != checkpoint_row["checkpoint_digest"]
                    or sha256_digest(base) != checkpoint.get("digest")
                ):
                    raise RuntimeJournalIntegrityError(
                        f"CHECKPOINT_CHAIN_INVALID:{run_id}:{group[0]}:{group[1]}"
                    )
                checkpoint_sequences[group] = expected_sequence
                checkpoint_heads[group] = checkpoint["digest"]

            return {
                "schema_version": "orgrebase.runtime-journal-verification.v1",
                "run_id": run_id,
                "valid": True,
                "action_count": len(action_rows),
                "attempt_count": attempt_count,
                "checkpoint_count": len(checkpoint_rows),
                "action_receipt_count": len(receipt_rows),
                "head_action_digest": previous,
            }


__all__ = [
    "ZERO_DIGEST",
    "RuntimeJournal",
    "RuntimeJournalConflict",
    "RuntimeJournalError",
    "RuntimeJournalIntegrityError",
    "RuntimeJournalNotFound",
    "StaleRuntimeAttempt",
]
