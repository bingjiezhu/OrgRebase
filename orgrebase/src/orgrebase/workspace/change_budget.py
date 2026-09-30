"""Deployment-scoped quota reservations for background candidate dispatch.

The model price contract bounds one provider attempt.  This module adds the
separate, persistent deployment-period boundary needed by an unattended
worker.  The reservation is written by ``reserve_attempt`` in the same SQL
transaction as the canonical preview idempotency record; provider I/O remains
outside that transaction.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, update

from orgrebase.database import deployment_budget_reservations
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError


class DeploymentDispatchBudget(BaseModel):
    """Frozen deployment-period quota supplied by deployment configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.deployment-dispatch-budget.v1"]
    deployment_scope: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    currency: Literal["USD"] = "USD"
    period_seconds: int = Field(ge=60, le=2_678_400)
    max_reserved_microusd: int = Field(ge=0)
    max_reserved_calls: int = Field(ge=0)
    max_dispatches_per_period: int = Field(ge=1, le=1_000_000)
    max_queue_reservations_per_period: int = Field(ge=1, le=1_000_000)
    max_active_attempts: int = Field(ge=1, le=10_000)

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


_ACTIVE_BUDGET: ContextVar[DeploymentDispatchBudget | None] = ContextVar(
    "deployment_dispatch_budget", default=None
)


@contextmanager
def dispatch_budget(policy: DeploymentDispatchBudget | None) -> Iterator[None]:
    """Bind one frozen policy to the current worker command."""

    token = _ACTIVE_BUDGET.set(policy)
    try:
        yield
    finally:
        _ACTIVE_BUDGET.reset(token)


def active_dispatch_budget() -> DeploymentDispatchBudget | None:
    return _ACTIVE_BUDGET.get()


def _tenant_id(workspace: Any) -> str:
    tenant_id = workspace.store.tenant_id or workspace.profile.organization_id
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_TENANT_REQUIRED")
    return tenant_id


def _period(policy: DeploymentDispatchBudget, now_epoch_ms: int) -> tuple[int, int]:
    period_ms = policy.period_seconds * 1000
    start = now_epoch_ms - now_epoch_ms % period_ms
    return start, start + period_ms


def _lock_scope(workspace: Any, connection: Any, *, tenant_id: str,
                policy: DeploymentDispatchBudget) -> None:
    if workspace.store.backend != "postgresql":
        # StateStore owns a BEGIN IMMEDIATE SQLite transaction at this point.
        return
    digest = sha256_digest(
        {
            "tenant_id": tenant_id,
            "deployment_scope": policy.deployment_scope,
            "lock": "deployment-dispatch-scope.v2",
        }
    )
    value = int(digest[7:23], 16)
    if value >= 2**63:
        value -= 2**64
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (value,))


def _reservation_key(workspace_id: str, attempt_key: str) -> str:
    """Scope the deployment ledger without changing the existing Preview key."""

    return "deployment-attempt:v2:" + sha256_digest({
        "workspace_id": workspace_id, "preview_attempt_key": attempt_key,
    })[7:]


def _find_reservation(workspace: Any, connection: Any, *, tenant_id: str,
                      policy: DeploymentDispatchBudget, attempt_key: str) -> Any | None:
    """Read a v2 or original v6 identity across periods; never rewrite history.

    Original rows stored the workspace-local Preview key. They remain valid for
    that workspace, but cannot block another workspace's v2 identity. More than
    one matching row is ambiguous and must not authorize another reservation.
    """

    table = deployment_budget_reservations
    rows = workspace.store.execute(connection, select(table).where(
        table.c.tenant_id == tenant_id,
        table.c.deployment_scope == policy.deployment_scope,
        table.c.workspace_id == workspace.store.workspace_id,
        table.c.attempt_key.in_((
            _reservation_key(workspace.store.workspace_id, attempt_key), attempt_key,
        )),
    )).fetchall()
    if len(rows) > 1:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RESERVATION_CONFLICT")
    return rows[0] if rows else None


def _cost_values(cost_reservation: Mapping[str, object] | None) -> tuple[int, int]:
    if cost_reservation is None:
        return 0, 0
    currency = cost_reservation.get("currency")
    amount = cost_reservation.get("reserved_microusd")
    calls = cost_reservation.get("calls")
    if (
        currency != "USD"
        or isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount < 0
        or isinstance(calls, bool)
        or not isinstance(calls, int)
        or calls < 0
    ):
        raise IntegrityError("WORKSPACE_ADVISORY_COST_RESERVATION_INVALID")
    return amount, calls


def reserve_dispatch_quota(
    workspace: Any,
    connection: Any,
    *,
    attempt_key: str,
    request_digest: str,
    cost_reservation: Mapping[str, object] | None,
    deadline_epoch_ms: int,
) -> dict[str, object] | None:
    """Atomically reserve deployment quota beside the preview attempt."""

    policy = active_dispatch_budget()
    if policy is None:
        return None
    tenant_id = _tenant_id(workspace)
    _lock_scope(
        workspace,
        connection,
        tenant_id=tenant_id,
        policy=policy,
    )
    now_epoch_ms = workspace._wall_clock_epoch_ms()
    if deadline_epoch_ms <= now_epoch_ms:
        raise IntegrityError("WORKSPACE_ADVISORY_DEADLINE_EXHAUSTED")
    period_start, period_end = _period(policy, now_epoch_ms)
    table = deployment_budget_reservations
    scope = (
        table.c.tenant_id == tenant_id,
        table.c.deployment_scope == policy.deployment_scope,
    )
    # A dispatch crash stays active until its fixed attempt deadline.  Crossing
    # that deadline records RESULT_UNKNOWN durably; its money/call reservation
    # remains in every cumulative sum for this period.
    workspace.store.execute(
        connection,
        update(table)
        .where(*scope, table.c.state == "DISPATCHING", table.c.deadline_epoch_ms <= now_epoch_ms)
        .values(state="RESULT_UNKNOWN", updated_at=workspace.clock.now()),
    )
    previous = _find_reservation(workspace, connection, tenant_id=tenant_id,
                                 policy=policy, attempt_key=attempt_key)
    amount, calls = _cost_values(cost_reservation)
    if previous is not None:
        if (
            previous["workspace_id"] != workspace.store.workspace_id
            or previous["request_digest"] != request_digest
            or previous["policy_digest"] != policy.digest
            or previous["reserved_microusd"] != amount
            or previous["reserved_calls"] != calls
        ):
            raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RESERVATION_CONFLICT")
        return _reservation_summary(previous)

    totals = workspace.store.execute(
        connection,
        select(
            func.count().label("admissions"),
            func.coalesce(func.sum(table.c.reserved_microusd), 0).label("microusd"),
            func.coalesce(func.sum(table.c.reserved_calls), 0).label("calls"),
            func.min(table.c.policy_digest).label("minimum_policy"),
            func.max(table.c.policy_digest).label("maximum_policy"),
        ).where(*scope, table.c.period_start_epoch_ms == period_start),
    ).fetchone()
    # Running work keeps occupying the deployment even when its billing period
    # ends. The scope lock serializes admissions on both sides of that boundary.
    active = workspace.store.execute(connection, select(func.count()).select_from(table).where(
        *scope, table.c.state == "DISPATCHING",
    )).fetchone()[0]
    if (
        totals["minimum_policy"] is not None
        and (totals["minimum_policy"] != policy.digest or totals["maximum_policy"] != policy.digest)
    ):
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_POLICY_CHANGED")
    admissions = int(totals["admissions"])
    if admissions + 1 > policy.max_dispatches_per_period:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RATE_EXHAUSTED")
    if admissions + 1 > policy.max_queue_reservations_per_period:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_QUEUE_EXHAUSTED")
    if int(active) + 1 > policy.max_active_attempts:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_CONCURRENCY_EXHAUSTED")
    if int(totals["microusd"]) + amount > policy.max_reserved_microusd:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_COST_EXHAUSTED")
    if int(totals["calls"]) + calls > policy.max_reserved_calls:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_CALLS_EXHAUSTED")
    values = {
        "tenant_id": tenant_id,
        "deployment_scope": policy.deployment_scope,
        "period_start_epoch_ms": period_start,
        "attempt_key": _reservation_key(workspace.store.workspace_id, attempt_key),
        "period_end_epoch_ms": period_end,
        "workspace_id": workspace.store.workspace_id,
        "request_digest": request_digest,
        "policy_digest": policy.digest,
        "state": "DISPATCHING",
        "reserved_microusd": amount,
        "reserved_calls": calls,
        "deadline_epoch_ms": deadline_epoch_ms,
        "created_at": workspace.clock.now(),
        "updated_at": workspace.clock.now(),
    }
    workspace.store.execute(connection, insert(table).values(**values))
    return _reservation_summary(values)


def reconcile_expired_dispatch_quotas(workspace: Any) -> int:
    """Durably close crashed dispatch windows once their fixed deadline passes."""

    policy = active_dispatch_budget()
    if policy is None:
        return 0
    tenant_id = _tenant_id(workspace)
    table = deployment_budget_reservations
    with workspace.store.transaction() as connection:
        _lock_scope(workspace, connection, tenant_id=tenant_id, policy=policy)
        now_epoch_ms = workspace._wall_clock_epoch_ms()
        changed = workspace.store.execute(
            connection,
            update(table)
            .where(
                table.c.tenant_id == tenant_id,
                table.c.deployment_scope == policy.deployment_scope,
                table.c.state == "DISPATCHING",
                table.c.deadline_epoch_ms <= now_epoch_ms,
            )
            .values(state="RESULT_UNKNOWN", updated_at=workspace.clock.now()),
        ).rowcount
    return int(changed)


def finish_dispatch_quota(
    workspace: Any,
    connection: Any,
    *,
    attempt_key: str,
    request_digest: str,
    state: Literal["COMPLETE", "FAILED"],
) -> None:
    """Release active concurrency only beside a durable terminal receipt."""

    policy = active_dispatch_budget()
    if policy is None:
        return
    table = deployment_budget_reservations
    tenant_id = _tenant_id(workspace)
    _lock_scope(workspace, connection, tenant_id=tenant_id, policy=policy)
    row = _find_reservation(workspace, connection, tenant_id=tenant_id,
                            policy=policy, attempt_key=attempt_key)
    if row is None:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RESERVATION_MISSING")
    if (
        row["workspace_id"] != workspace.store.workspace_id
        or row["request_digest"] != request_digest
        or row["policy_digest"] != policy.digest
    ):
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RESERVATION_CONFLICT")
    if row["state"] == state:
        return
    if row["state"] != "DISPATCHING":
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_TERMINAL_CONFLICT")
    changed = workspace.store.execute(
        connection,
        update(table)
        .where(
            table.c.tenant_id == tenant_id,
            table.c.deployment_scope == policy.deployment_scope,
            table.c.period_start_epoch_ms == row["period_start_epoch_ms"],
            table.c.workspace_id == workspace.store.workspace_id,
            table.c.attempt_key == row["attempt_key"],
            table.c.request_digest == request_digest,
            table.c.state == "DISPATCHING",
        )
        .values(state=state, updated_at=workspace.clock.now()),
    ).rowcount
    if changed != 1:
        raise IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RESERVATION_CONFLICT")


def _reservation_summary(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "scope": "DEPLOYMENT_PERIOD",
        "deployment_scope": row["deployment_scope"],
        "period_start_epoch_ms": row["period_start_epoch_ms"],
        "period_end_epoch_ms": row["period_end_epoch_ms"],
        "state": row["state"],
        "reserved_microusd": row["reserved_microusd"],
        "reserved_calls": row["reserved_calls"],
        "policy_digest": row["policy_digest"],
    }
