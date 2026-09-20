"""Rebuild effect target projections without changing approved request identities."""

from __future__ import annotations

import json
import time

import psycopg
from sqlalchemy import delete, select, update

from orgrebase.database import (
    Connection,
    effect_intents,
    execute_core,
    source_checkpoints,
    store_metadata,
    target_barriers,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.effect_identity import DATAVERSE_DRAFT_ACTION, effect_barrier_key


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("NON_FINITE_JSON_NUMBER")


def _projected_key(row, tenant_id: str | None) -> str:
    """The existing generic/Git ledger keeps its original projection unchanged."""
    try:
        request = json.loads(
            row["request_json"], object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (ValueError, TypeError) as error:
        raise IntegrityError("STATE_STORE_EFFECT_REQUEST_INVALID") from error
    if not isinstance(request, dict):
        raise IntegrityError("STATE_STORE_EFFECT_REQUEST_INVALID")
    if request.get("action") != DATAVERSE_DRAFT_ACTION:
        return row["target_key"]
    try:
        digest = sha256_digest(request)
    except (ValueError, TypeError, UnicodeError) as error:
        raise IntegrityError("STATE_STORE_EFFECT_REQUEST_INVALID") from error
    if (
        not isinstance(tenant_id, str)
        or request.get("effect_id") != row["effect_id"]
        or request.get("tenant_id") != tenant_id
        or not isinstance(request.get("target_key"), str)
        or row["request_digest"] != digest
        or row["target_key"] != sha256_digest({"tenant": tenant_id, "target": request["target_key"]})
    ):
        raise IntegrityError("STATE_STORE_EFFECT_REQUEST_BINDING_INVALID")
    try:
        return effect_barrier_key(
            tenant_id=tenant_id, target_key=request["target_key"], action=request["action"]
        )
    except (TypeError, ValueError) as error:
        raise IntegrityError("STATE_STORE_EFFECT_TARGET_INVALID") from error


def migrate_effect_barriers(connection: Connection) -> None:
    """Require stopped writers, then atomically remap every occupied target.

    A database cannot revoke an external request already sent by an old worker.
    Operators must stop the old workers and fence outbound access before this
    transition, and must keep the deployment quarantined until reconciliation.
    """
    if isinstance(connection, psycopg.Connection):
        role = connection.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not role or not role[0]:
            raise IntegrityError("STATE_STORE_MIGRATION_REQUIRES_RLS_BYPASS")
        connection.execute(
            "LOCK TABLE effect_intents,target_barriers,source_checkpoints IN ACCESS EXCLUSIVE MODE"
        )
    now = time.time()
    for table in (effect_intents, source_checkpoints):
        active = execute_core(
            connection,
            select(table.c.lease_owner).where(table.c.lease_until > now).limit(1),
        ).fetchone()
        if active is not None:
            raise IntegrityError("STATE_STORE_MIGRATION_ACTIVE_LEASE")
    metadata = execute_core(
        connection, select(store_metadata.c.tenant_id, store_metadata.c.recovery_required)
    ).fetchone()
    tenant_id = metadata["tenant_id"]
    if (
        not metadata["recovery_required"]
        and execute_core(connection, select(effect_intents.c.effect_id).limit(1)).fetchone() is not None
    ):
        raise IntegrityError("STATE_STORE_EFFECT_MIGRATION_REQUIRES_QUARANTINE")
    occupied = execute_core(
        connection,
        select(
            effect_intents.c.effect_id,
            effect_intents.c.target_key,
            effect_intents.c.request_digest,
            effect_intents.c.request_json,
            target_barriers.c.target_key.label("occupied_key"),
        ).select_from(target_barriers.outerjoin(effect_intents)),
    ).fetchall()
    remapped_barriers: dict[str, str] = {}
    for row in occupied:
        if row["effect_id"] is None or row["target_key"] != row["occupied_key"]:
            raise IntegrityError("STATE_STORE_EFFECT_BARRIER_INVALID")
        key = _projected_key(row, tenant_id)
        if key in remapped_barriers:
            raise IntegrityError("STATE_STORE_EFFECT_TARGET_COLLISION")
        remapped_barriers[key] = row["effect_id"]
    missing = execute_core(
        connection,
        select(effect_intents.c.effect_id)
        .select_from(effect_intents.outerjoin(target_barriers))
        .where(
            effect_intents.c.state.in_(("DISPATCHING", "COMMIT_UNKNOWN")),
            target_barriers.c.effect_id.is_(None),
        )
        .limit(1),
    ).fetchone()
    if missing is not None:
        raise IntegrityError("STATE_STORE_EFFECT_BARRIER_MISSING")
    after = None
    while True:
        statement = (
            select(
                effect_intents.c.effect_id,
                effect_intents.c.target_key,
                effect_intents.c.request_digest,
                effect_intents.c.request_json,
            )
            .order_by(effect_intents.c.effect_id)
            .limit(1000)
        )
        if after is not None:
            statement = statement.where(effect_intents.c.effect_id > after)
        rows = execute_core(connection, statement).fetchall()
        if not rows:
            break
        for row in rows:
            key = _projected_key(row, tenant_id)
            if key != row["target_key"]:
                execute_core(
                    connection,
                    update(effect_intents)
                    .where(effect_intents.c.effect_id == row["effect_id"])
                    .values(target_key=key),
                )
        after = rows[-1]["effect_id"]
    execute_core(connection, delete(target_barriers))
    if remapped_barriers:
        execute_core(
            connection,
            target_barriers.insert().values(
                [
                    {"target_key": key, "effect_id": effect_id}
                    for key, effect_id in sorted(remapped_barriers.items())
                ]
            ),
        )
