"""Bounded retention for private inputs outside immutable business artifacts."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from orgrebase.clock import Clock, utc_datetime
from orgrebase.database import artifacts, private_records
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore


def _time(value) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


_REASONS = frozenset({"USER_REQUESTED", "RETENTION_EXPIRED", "RETENTION_DISABLED", "RESTORE_RECONCILIATION"})


class PrivateRecordStore:
    def __init__(self, store: StateStore, clock: Clock, *, retention_seconds: int = 86_400) -> None:
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, int)
            or not 0 <= retention_seconds <= 604_800
        ):
            raise ValueError("PRIVATE_RETENTION_INVALID")
        self.store = store
        self.clock = clock
        self.retention_seconds = retention_seconds

    def _row(self, connection, record_id: str):
        return self.store.execute(
            connection, select(private_records).where(private_records.c.record_id == record_id)
        ).fetchone()

    def write(
        self, connection, *, record_id: str, scope_ref: str, owner_id: str, payload: dict[str, Any]
    ) -> None:
        if not record_id or not scope_ref or not owner_id:
            raise ValueError("PRIVATE_RECORD_BINDING_REQUIRED")
        digest = sha256_digest(payload)
        existing = self._row(connection, record_id)
        if existing is not None:
            if (existing["content_digest"], existing["scope_ref"], existing["owner_id"]) != (
                digest,
                scope_ref,
                owner_id,
            ):
                raise IntegrityError("PRIVATE_RECORD_CONFLICT")
            # A retry never recreates deleted data or renews its retention window.
            return
        observed = utc_datetime(self.clock.now())
        now = _time(observed)
        disabled = self.retention_seconds == 0
        insert = postgres_insert if self.store.backend == "postgresql" else sqlite_insert
        self.store.execute(
            connection,
            insert(private_records)
            .values(
                record_id=record_id,
                scope_ref=scope_ref,
                owner_id=owner_id,
                content_digest=digest,
                content_json=None if disabled else canonical_json(payload),
                created_at=now,
                expires_at=_time(observed + timedelta(seconds=self.retention_seconds)),
                deleted_at=now if disabled else None,
                deletion_reason="RETENTION_DISABLED" if disabled else None,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "record_id"]),
        )
        stored = self._row(connection, record_id)
        if (stored["content_digest"], stored["scope_ref"], stored["owner_id"]) != (
            digest,
            scope_ref,
            owner_id,
        ):
            raise IntegrityError("PRIVATE_RECORD_CONFLICT")

    def read(self, record_id: str) -> dict[str, Any] | None:
        with self.store.read_connection() as connection:
            row = self._row(connection, record_id)
            if row is None or row["deleted_at"] is not None:
                return None
            if utc_datetime(self.clock.now()) >= utc_datetime(row["expires_at"]):
                return None
            payload = json.loads(row["content_json"])
            if not isinstance(payload, dict) or sha256_digest(payload) != row["content_digest"]:
                raise IntegrityError("PRIVATE_RECORD_DIGEST_MISMATCH")
            return payload

    def record_status(self, record_id: str) -> str:
        """Expose retention state without reading or returning the private payload."""
        with self.store.read_connection() as connection:
            row = self.store.execute(
                connection,
                select(private_records.c.deleted_at, private_records.c.expires_at).where(
                    private_records.c.record_id == record_id
                ),
            ).fetchone()
            if row is None:
                return "MISSING"
            if row["deleted_at"] is not None:
                return "DELETED"
            if utc_datetime(self.clock.now()) >= utc_datetime(row["expires_at"]):
                return "EXPIRED"
            return "AVAILABLE"

    def matches(self, record_id: str, payload: dict[str, Any]) -> bool:
        with self.store.read_connection() as connection:
            row = self._row(connection, record_id)
            return row is not None and row["content_digest"] == sha256_digest(payload)

    def _erase(self, connection, row, reason: str) -> bool:
        if row["deleted_at"] is not None:
            return False
        now = _time(utc_datetime(self.clock.now()))
        changed = self.store.execute(
            connection,
            update(private_records)
            .where(
                private_records.c.record_id == row["record_id"],
                private_records.c.deleted_at.is_(None),
            )
            .values(content_json=None, deleted_at=now, deletion_reason=reason),
        )
        if changed.rowcount != 1:
            return False
        self.store.append_event(
            connection,
            "PRIVATE_CONTENT_DELETED",
            {
                "record_id": row["record_id"],
                "scope_ref": row["scope_ref"],
                "content_digest": row["content_digest"],
                "deleted_at": now,
                "reason": reason,
            },
        )
        return True

    def erase(self, record_id: str, *, actor_id: str, reason: str = "USER_REQUESTED") -> bool:
        if reason not in _REASONS:
            raise ValueError("PRIVATE_DELETION_REASON_INVALID")
        with self.store.transaction() as connection:
            row = self._row(connection, record_id)
            if row is None:
                return False
            if row["owner_id"] != actor_id:
                raise PermissionError("PRIVATE_RECORD_OWNER_REQUIRED")
            return self._erase(connection, row, reason)

    def purge_expired(self, *, limit: int = 1000) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("PRIVATE_PURGE_LIMIT_INVALID")
        now = _time(utc_datetime(self.clock.now()))
        with self.store.transaction() as connection:
            rows = self.store.execute(
                connection,
                select(private_records)
                .where(
                    private_records.c.content_json.is_not(None),
                    private_records.c.expires_at <= now,
                )
                .order_by(private_records.c.expires_at, private_records.c.record_id)
                .limit(limit),
            ).fetchall()
            return sum(self._erase(connection, row, "RETENTION_EXPIRED") for row in rows)

    def deletion_ledger(self) -> list[dict[str, str]]:
        with self.store.read_connection() as connection:
            rows = self.store.execute(
                connection,
                select(private_records)
                .where(private_records.c.deleted_at.is_not(None))
                .order_by(private_records.c.record_id),
            ).fetchall()
            return [
                {
                    key: row[key]
                    for key in (
                        "record_id",
                        "scope_ref",
                        "owner_id",
                        "content_digest",
                        "deleted_at",
                        "deletion_reason",
                    )
                }
                for row in rows
            ]

    def reapply_deletions(self, ledger: list[dict[str, str]]) -> int:
        """Apply an administrator-supplied deletion ledger before exposing a restored database."""
        expected = {"record_id", "scope_ref", "owner_id", "content_digest", "deleted_at", "deletion_reason"}
        with self.store.transaction() as connection:
            pending = []
            seen = set()
            for entry in ledger:
                if (
                    set(entry) != expected
                    or not all(isinstance(value, str) and value for value in entry.values())
                    or entry["deletion_reason"] not in _REASONS
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry["content_digest"])
                ):
                    raise IntegrityError("PRIVATE_DELETION_LEDGER_INVALID")
                if entry["record_id"] in seen:
                    raise IntegrityError("PRIVATE_DELETION_LEDGER_DUPLICATE")
                seen.add(entry["record_id"])
                utc_datetime(entry["deleted_at"])
                row = self._row(connection, entry["record_id"])
                if row is not None and any(
                    row[key] != entry[key] for key in ("scope_ref", "owner_id", "content_digest")
                ):
                    raise IntegrityError("PRIVATE_DELETION_BINDING_MISMATCH")
                pending.append((row, entry))
            count = 0
            for row, entry in pending:
                if row is not None:
                    count += self._erase(connection, row, "RESTORE_RECONCILIATION")
                    continue
                now = _time(utc_datetime(self.clock.now()))
                insert = postgres_insert if self.store.backend == "postgresql" else sqlite_insert
                added = self.store.execute(
                    connection,
                    insert(private_records)
                    .values(
                        record_id=entry["record_id"],
                        scope_ref=entry["scope_ref"],
                        owner_id=entry["owner_id"],
                        content_digest=entry["content_digest"],
                        content_json=None,
                        created_at=now,
                        expires_at=now,
                        deleted_at=entry["deleted_at"],
                        deletion_reason="RESTORE_RECONCILIATION",
                    )
                    .on_conflict_do_nothing(index_elements=["workspace_id", "record_id"]),
                )
                current = self._row(connection, entry["record_id"])
                if any(current[key] != entry[key] for key in ("scope_ref", "owner_id", "content_digest")):
                    raise IntegrityError("PRIVATE_DELETION_BINDING_MISMATCH")
                if added.rowcount == 1:
                    self.store.append_event(
                        connection,
                        "PRIVATE_DELETION_RESTORED",
                        {
                            "record_id": entry["record_id"],
                            "content_digest": entry["content_digest"],
                            "restored_at": now,
                        },
                    )
                    count += 1
                else:
                    count += self._erase(connection, current, "RESTORE_RECONCILIATION")
            return count

    def migrate_artifact(self, record_id: str, media_type: str) -> bool:
        """Move the former private artifact once; business artifacts are never rewritten."""
        with self.store.transaction() as connection:
            row = self.store.execute(
                connection, select(artifacts).where(artifacts.c.artifact_id == record_id)
            ).fetchone()
            if row is None:
                return False
            if row["media_type"] != media_type:
                raise IntegrityError("PRIVATE_ARTIFACT_MEDIA_TYPE_MISMATCH")
            payload = json.loads(row["payload_json"])
            if sha256_digest(payload) != row["payload_digest"]:
                raise IntegrityError("PRIVATE_ARTIFACT_DIGEST_MISMATCH")
            self.write(
                connection,
                record_id=record_id,
                scope_ref=payload["run_id"],
                owner_id=payload["actor_id"],
                payload=payload,
            )
            self.store.execute(connection, delete(artifacts).where(artifacts.c.artifact_id == record_id))
            self.store.append_event(
                connection,
                "PRIVATE_CONTENT_STORAGE_MIGRATED",
                {
                    "record_id": record_id,
                    "content_digest": row["payload_digest"],
                    "retention_started_at": _time(utc_datetime(self.clock.now())),
                    "historical_creation_time": "UNAVAILABLE",
                },
            )
            return True
