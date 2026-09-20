"""Transactional lookup indexes derived from immutable change artifacts and events."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import insert, select, update

from orgrebase.change_events import ChangeEvent
from orgrebase.clock import utc_datetime
from orgrebase.database import Connection, artifacts, execute_core, workspace_changes
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError


def _artifact(
    connection: Connection, workspace_id: str, artifact_id: str, expected_digest: str | None
) -> dict[str, Any]:
    row = execute_core(
        connection,
        select(artifacts).where(
            artifacts.c.workspace_id == workspace_id, artifacts.c.artifact_id == artifact_id
        ),
    ).fetchone()
    if row is None:
        raise IntegrityError("CHANGE_PROJECTION_ARTIFACT_MISSING")
    payload = json.loads(row["payload_json"])
    if (
        canonical_json(payload) != row["payload_json"]
        or sha256_digest(payload) != row["payload_digest"]
        or (expected_digest is not None and row["payload_digest"] != expected_digest)
    ):
        raise IntegrityError("CHANGE_PROJECTION_ARTIFACT_BINDING_INVALID")
    return {"payload": payload, "digest": row["payload_digest"]}


def project_change(
    connection: Connection,
    *,
    workspace_id: str,
    sequence_no: int,
    event_type: str,
    payload: dict[str, Any],
    next_ordinal: int,
) -> tuple[int, int]:
    """Return changes to total/pending counts, never business authority decisions."""
    if event_type in {"WORKSPACE_SOURCE_GROUP_APPLIED", "WORKSPACE_SOURCE_GROUP_REJECTED"}:
        event_ids = payload.get("event_ids")
        if (
            not isinstance(event_ids, list)
            or not 1 <= len(event_ids) <= 32
            or any(not isinstance(item, str) or not item for item in event_ids)
            or event_ids != sorted(set(event_ids))
        ):
            raise IntegrityError("CHANGE_GROUP_PROJECTION_MEMBERS_INVALID")
        _artifact(connection, workspace_id, payload["artifact_id"], payload["artifact_digest"])
        criteria = (
            workspace_changes.c.workspace_id == workspace_id,
            workspace_changes.c.event_id.in_(event_ids),
        )
        rows = execute_core(connection, select(workspace_changes).where(*criteria)).fetchall()
        resolution = "APPLIED" if event_type == "WORKSPACE_SOURCE_GROUP_APPLIED" else "REJECTED"
        if len(rows) != len(event_ids) or any(
            row["operation"] != "READMIT" or row["resolution"] not in (None, resolution) for row in rows
        ):
            raise IntegrityError("CHANGE_GROUP_PROJECTION_STATE_INVALID")
        changed = execute_core(
            connection,
            update(workspace_changes)
            .where(*criteria, workspace_changes.c.resolution.is_(None))
            .values(resolution=resolution),
        ).rowcount
        return 0, -changed
    if event_type == "WORKSPACE_CHANGE_REGISTERED":
        artifact_id = f"workspace-change:{payload['event_id']}@r1"
        artifact = _artifact(connection, workspace_id, artifact_id, None)
        event = ChangeEvent.model_validate(artifact["payload"])
        if event.event_id != payload["event_id"] or event.digest != payload["event_digest"]:
            raise IntegrityError("CHANGE_EVENT_JOURNAL_BINDING_INVALID")
        execute_core(
            connection,
            insert(workspace_changes).values(
                workspace_id=workspace_id,
                event_id=event.event_id,
                ordinal=next_ordinal,
                registration_sequence=sequence_no,
                artifact_id=artifact_id,
                artifact_digest=artifact["digest"],
                event_digest=event.digest,
                owner_id=event.owner_id,
                object_id=event.proposal.id,
                base_version=event.base_version,
                base_digest=event.base_digest,
                operation=event.operation,
                valid_from=utc_datetime(event.proposal.valid_from).timestamp(),
                valid_to=utc_datetime(event.proposal.valid_to).timestamp()
                if event.proposal.valid_to
                else None,
            ),
        )
        return 1, 1
    kinds = {
        "WORKSPACE_CHANGE_REJECTED": "event_id",
        "WORKSPACE_CHANGE_OUTCOME_RECORDED": "kind",
        "WORKSPACE_CHANGE_PREVIEWED": "kind",
        "WORKSPACE_CHANGE_APPROVED": "kind",
    }
    key = kinds.get(event_type)
    if key is None or not isinstance(payload.get(key), str):
        return 0, 0
    event_id = payload[key]
    where = (workspace_changes.c.workspace_id == workspace_id, workspace_changes.c.event_id == event_id)
    registered = execute_core(connection, select(workspace_changes).where(*where)).fetchone()
    if registered is None:
        return 0, 0
    if event_type in {"WORKSPACE_CHANGE_REJECTED", "WORKSPACE_CHANGE_OUTCOME_RECORDED"}:
        if event_type == "WORKSPACE_CHANGE_REJECTED":
            rejection = _artifact(connection, workspace_id, f"workspace-rejection:{event_id}@r1", None)["payload"]
            if (rejection != payload or rejection.get("event_digest") != registered["event_digest"]
                    or rejection.get("status") != "REJECTED"):
                raise IntegrityError("CHANGE_REJECTION_PROJECTION_BINDING_INVALID")
        else:
            if payload.get("artifact_id") != f"workspace-outcome:{event_id}@r1" or not payload.get("artifact_digest"):
                raise IntegrityError("CHANGE_OUTCOME_PROJECTION_BINDING_INVALID")
            outcome = _artifact(connection, workspace_id, payload["artifact_id"], payload["artifact_digest"])["payload"]
            spec = outcome.get("change_spec", {})
            quote = outcome.get("quote", {})
            if (not isinstance(spec, dict) or not isinstance(quote, dict)
                    or outcome.get("kind") != event_id
                    or spec.get("object_id") != registered["object_id"]
                    or spec.get("base_version") != registered["base_version"]
                    or not outcome.get("approval_digest")
                    or outcome.get("approval_digest") != payload.get("approval_digest")
                    or payload.get("quote_ref") != f"{quote.get('id')}@{quote.get('version')}"):
                raise IntegrityError("CHANGE_OUTCOME_PROJECTION_BINDING_INVALID")
        changed = execute_core(
            connection,
            update(workspace_changes)
            .where(*where, workspace_changes.c.resolution.is_(None))
            .values(resolution="REJECTED" if event_type == "WORKSPACE_CHANGE_REJECTED" else "APPLIED"),
        ).rowcount
        return 0, -changed
    artifact = _artifact(connection, workspace_id, payload["artifact_id"], payload["artifact_digest"])[
        "payload"
    ]
    if event_type == "WORKSPACE_CHANGE_PREVIEWED":
        expiry = utc_datetime(artifact["run_envelope"]["expires_at"]).timestamp()
        values = {
            "preview_expires_at": expiry,
            "preview_snapshot_ref": artifact["snapshot_ref"],
            "preview_snapshot_digest": artifact["snapshot_digest"],
        }
    else:
        values = {"approval_expires_at": utc_datetime(artifact["expires_at"]).timestamp()}
    execute_core(connection, update(workspace_changes).where(*where).values(**values))
    return 0, 0


def backfill_event_projections(connection: Connection) -> None:
    """Explicit migration replay; source events and their digests remain unchanged."""
    from orgrebase.database import domain_events, workspace_registry
    from orgrebase.event_projection import ZERO_DIGEST, append_scope, empty_scopes, event_subject

    for workspace in execute_core(connection, select(workspace_registry)).fetchall():
        workspace_id = workspace["workspace_id"]
        scopes = empty_scopes()
        count = pending = sequence = 0
        previous = ZERO_DIGEST
        rows = execute_core(
            connection,
            select(domain_events)
            .where(domain_events.c.workspace_id == workspace_id)
            .order_by(domain_events.c.sequence_no),
        ).fetchall()
        for row in rows:
            sequence += 1
            payload = json.loads(row["payload_json"])
            envelope = {
                "sequence_no": sequence,
                "event_type": row["event_type"],
                "payload": payload,
                "previous_digest": previous,
            }
            if (
                row["sequence_no"] != sequence
                or row["previous_digest"] != previous
                or sha256_digest(envelope) != row["event_digest"]
            ):
                raise IntegrityError("CHANGE_PROJECTION_HISTORY_INVALID")
            total_delta, pending_delta = project_change(
                connection,
                workspace_id=workspace_id,
                sequence_no=sequence,
                event_type=row["event_type"],
                payload=payload,
                next_ordinal=count + 1,
            )
            count += total_delta
            pending += pending_delta
            subject = event_subject(payload)
            if subject is not None:
                execute_core(
                    connection,
                    update(domain_events)
                    .where(
                        domain_events.c.workspace_id == workspace_id, domain_events.c.sequence_no == sequence
                    )
                    .values(subject_key=subject),
                )
            scopes = append_scope(scopes, {**envelope, "event_digest": row["event_digest"]})
            previous = row["event_digest"]
        if sequence != workspace["audit_sequence"] or previous != workspace["audit_head"]:
            raise IntegrityError("STATE_STORE_AUDIT_HEAD_MISMATCH")
        execute_core(
            connection,
            update(workspace_registry)
            .where(workspace_registry.c.workspace_id == workspace_id)
            .values(
                change_count=count, pending_change_count=pending, event_scopes_json=canonical_json(scopes)
            ),
        )
