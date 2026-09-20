"""Durable dispatch and reconciliation of precisely approved external effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.clock import Clock, SystemClock, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.effect_identity import effect_barrier_key
from orgrebase.store import StateStore


class EffectError(RuntimeError):
    """An effect was refused or requires reconciliation."""


class EffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect_id: str = Field(min_length=1, max_length=512)
    tenant_id: str = Field(min_length=1, max_length=256)
    target_key: str = Field(min_length=1, max_length=1024)
    action: str = Field(min_length=1, max_length=128)
    expected_version: str = Field(min_length=1, max_length=512)
    approval_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payload: dict[str, Any]

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))

    @property
    def barrier_key(self) -> str:
        return effect_barrier_key(tenant_id=self.tenant_id, target_key=self.target_key, action=self.action)


class ResolutionState(StrEnum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    ABSENT_FINAL = "ABSENT_FINAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TargetResolution:
    state: ResolutionState
    effect_id: str
    request_digest: str
    operation_id: str | None = None
    evidence: Mapping[str, Any] | None = None
    reason: str | None = None


class TargetAdapter(Protocol):
    def execute(self, effect: EffectRequest) -> TargetResolution: ...

    def query_effect(self, effect: EffectRequest) -> TargetResolution: ...


Authorize = Callable[[EffectRequest, str], None]
Record = Callable[[Any, Mapping[str, Any]], Mapping[str, Any]]


class CommitGateway:
    """One effect ledger, with a target barrier independent of worker leases.

    Adapters own target-specific preconditions and positive result evidence.
    A missing response or an absent lookup is never a licence to dispatch again.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        tenant_id: str,
        worker_id: str,
        authorize: Authorize,
        clock: Clock | None = None,
        lease_seconds: float = 60.0,
    ) -> None:
        if not tenant_id or not worker_id or not 0 < lease_seconds <= 3600:
            raise ValueError("INVALID_EFFECT_WORKER_CONFIG")
        self.store = store
        self.tenant_id = tenant_id
        self.worker_id = worker_id
        self.authorize = authorize
        self.clock = clock or SystemClock()
        self.lease_seconds = lease_seconds

    def _time(self) -> datetime:
        return utc_datetime(self.clock.now())

    def run(
        self,
        effect: EffectRequest,
        adapter: TargetAdapter,
        *,
        record: Record | None = None,
        query_only: bool = False,
    ) -> dict[str, Any]:
        # Reparse to catch mutation of nested payloads in otherwise frozen models.
        effect = EffectRequest.model_validate(effect.model_dump(mode="json"))
        if effect.tenant_id != self.tenant_id:
            raise EffectError("EFFECT_TENANT_MISMATCH")
        bound_tenant = getattr(self.store, "tenant_id", None)
        if bound_tenant is not None and bound_tenant != effect.tenant_id:
            raise EffectError("EFFECT_STORE_TENANT_MISMATCH")
        current = self.store.get_effect(effect.effect_id)
        action = "effect.reconcile" if query_only or (current and current["state"] != "READY") else "effect.execute"
        self.authorize(effect, action)
        if query_only and (current is None or current["state"] == "READY"):
            raise EffectError("EFFECT_NOT_DISPATCHED")
        now = self._time()
        with self.store.transaction() as connection:
            # Establish dispatch authority in the same transaction that claims it.
            self.authorize(effect, action)
            current = self.store.put_effect(
                connection,
                effect_id=effect.effect_id,
                target_key=effect.barrier_key,
                request_digest=effect.digest,
                request=effect.model_dump(mode="json"),
                created_at=now.isoformat(),
            )
            if query_only and current["state"] == "READY":
                raise EffectError("EFFECT_NOT_DISPATCHED")
            if current["state"] == "CONFIRMED":
                return dict(current["result"])
            if current["state"] == "REJECTED":
                raise EffectError(current["error_code"] or "EFFECT_REJECTED")
            if not self.store.acquire_target_barrier(
                connection, target_key=effect.barrier_key, effect_id=effect.effect_id
            ):
                raise EffectError("TARGET_EFFECT_UNRESOLVED")
            claim = self.store.claim_effect(
                connection,
                effect_id=effect.effect_id,
                worker_id=self.worker_id,
                now=now.timestamp(),
                lease_seconds=self.lease_seconds,
                allowed_states=("READY", "DISPATCHING", "COMMIT_UNKNOWN"),
            )
            if claim is None:
                raise EffectError("EFFECT_IN_PROGRESS")
            dispatch = claim["state"] == "READY"
            fence = claim["fence"]
            self.store.update_effect(
                connection,
                effect_id=effect.effect_id,
                expected_state=claim["state"],
                state="DISPATCHING" if dispatch else "COMMIT_UNKNOWN",
                updated_at=now.isoformat(),
                result=claim["result"],
                expected_fence=fence,
            )
        try:
            # Recheck authority at the actual I/O boundary, after waiting for a claim.
            self.authorize(effect, "effect.execute" if dispatch else "effect.reconcile")
            resolution = adapter.execute(effect) if dispatch else adapter.query_effect(effect)
        except BaseException:
            self._unknown(effect, fence, "TARGET_RESPONSE_UNAVAILABLE")
            raise
        try:
            return self._resolve(effect, resolution, fence, record)
        except BaseException:
            self._unknown(effect, fence, "EFFECT_FINALIZATION_UNAVAILABLE", preserve_recorded_reason=True)
            raise

    def _unknown(
        self, effect: EffectRequest, fence: int, reason: str, *, preserve_recorded_reason: bool = False,
    ) -> None:
        with self.store.transaction() as connection:
            current = self.store.get_effect(effect.effect_id, connection=connection)
            if current is None or current["fence"] != fence:
                return
            if current["state"] not in {"DISPATCHING", "COMMIT_UNKNOWN"}:
                return
            # A resolution rejection already recorded its cause and released this
            # claim. A failing record callback still owns its claim after rollback.
            if (preserve_recorded_reason and current["state"] == "COMMIT_UNKNOWN"
                    and current["lease_owner"] is None and current["error_code"]):
                return
            self.store.update_effect(
                connection,
                effect_id=effect.effect_id,
                expected_state=("DISPATCHING", "COMMIT_UNKNOWN"),
                state="COMMIT_UNKNOWN",
                updated_at=self._time().isoformat(),
                error_code=reason,
                expected_fence=fence,
            )
            self.store.release_effect_claim(
                connection, effect_id=effect.effect_id, worker_id=self.worker_id, fence=fence
            )

    def _resolve(
        self,
        effect: EffectRequest,
        resolution: TargetResolution,
        fence: int,
        record: Record | None,
    ) -> dict[str, Any]:
        if not isinstance(resolution.state, ResolutionState):
            self._unknown(effect, fence, "TARGET_RESOLUTION_STATE_INVALID")
            raise EffectError("TARGET_RESOLUTION_STATE_INVALID")
        if resolution.effect_id != effect.effect_id or resolution.request_digest != effect.digest:
            self._unknown(effect, fence, "TARGET_RECEIPT_BINDING_MISMATCH")
            raise EffectError("TARGET_RECEIPT_BINDING_MISMATCH")
        if resolution.state == ResolutionState.UNKNOWN:
            self._unknown(effect, fence, resolution.reason or "TARGET_RESULT_UNKNOWN")
            raise EffectError("COMMIT_UNKNOWN")
        if not resolution.evidence or (
            resolution.state == ResolutionState.CONFIRMED and (
                not isinstance(resolution.operation_id, str) or not resolution.operation_id.strip()
            )
        ):
            self._unknown(effect, fence, "TARGET_POSITIVE_EVIDENCE_REQUIRED")
            raise EffectError("TARGET_POSITIVE_EVIDENCE_REQUIRED")
        now = self._time().isoformat()
        with self.store.transaction() as connection:
            current = self.store.get_effect(effect.effect_id, connection=connection)
            if current is None or current["fence"] != fence:
                raise EffectError("STALE_EFFECT_ATTEMPT")
            result = dict(resolution.evidence)
            if resolution.state == ResolutionState.CONFIRMED:
                if record is not None:
                    result = dict(record(connection, result))
                state = "CONFIRMED"
            elif resolution.state == ResolutionState.ABSENT_FINAL:
                state = "READY"
            else:
                state = "REJECTED"
            self.store.update_effect(
                connection,
                effect_id=effect.effect_id,
                expected_state=("DISPATCHING", "COMMIT_UNKNOWN"),
                state=state,
                updated_at=now,
                result=result,
                error_code=resolution.reason,
                expected_fence=fence,
            )
            self.store.release_effect_claim(
                connection, effect_id=effect.effect_id, worker_id=self.worker_id, fence=fence
            )
            if state in {"CONFIRMED", "REJECTED"}:
                self.store.release_target_barrier(
                    connection, target_key=effect.barrier_key, effect_id=effect.effect_id
                )
        if state == "READY":
            raise EffectError("EFFECT_ABSENT_FINAL_RETRY_AUTHORIZED")
        if state == "REJECTED":
            raise EffectError(resolution.reason or "EFFECT_REJECTED")
        return result
