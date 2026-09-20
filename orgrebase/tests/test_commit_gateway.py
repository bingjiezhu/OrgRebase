from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from orgrebase.clock import FrozenClock
from orgrebase.commit_gateway import (
    CommitGateway,
    EffectError,
    EffectRequest,
    ResolutionState,
    TargetResolution,
)
from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore


def request(effect_id: str = "e1", target: str = "quote:q1") -> EffectRequest:
    return EffectRequest(
        effect_id=effect_id, tenant_id="tenant:one", target_key=target,
        action="draft.revise", expected_version='W/"opaque-v1"',
        approval_digest=sha256_digest({"approved": "exact proposal"}),
        payload={"currency": "EUR"},
    )


@dataclass
class Target:
    calls: int = 0
    queries: int = 0
    lose_response: bool = False
    unresolved: bool = False
    results: dict[str, TargetResolution] = field(default_factory=dict)

    def execute(self, effect: EffectRequest) -> TargetResolution:
        self.calls += 1
        result = TargetResolution(
            ResolutionState.CONFIRMED, effect.effect_id, effect.digest,
            operation_id=f"operation:{effect.effect_id}",
            evidence={"target_version": "opaque-next", "operation": effect.effect_id},
        )
        self.results[effect.effect_id] = result
        if self.lose_response:
            raise TimeoutError("injected response loss after effect")
        return result

    def query_effect(self, effect: EffectRequest) -> TargetResolution:
        self.queries += 1
        if self.unresolved:
            return TargetResolution(ResolutionState.UNKNOWN, effect.effect_id, effect.digest)
        return self.results[effect.effect_id]


def gateway(store: StateStore, **kwargs: object) -> CommitGateway:
    return CommitGateway(
        store, tenant_id="tenant:one", worker_id="worker:one",
        authorize=lambda effect, action: None,
        clock=FrozenClock("2026-09-09T00:00:00Z"), **kwargs,
    )


def test_response_loss_reconciles_after_database_reopen_without_resending(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    target = Target(lose_response=True)
    with StateStore(path, tenant_id="tenant:one") as store:
        with pytest.raises(TimeoutError):
            gateway(store).run(request(), target)
        assert store.get_effect("e1")["state"] == "COMMIT_UNKNOWN"
    with StateStore(path, tenant_id="tenant:one") as reopened:
        result = gateway(reopened).run(request(), target)
        assert result["operation"] == "e1"
        assert target.calls == 1
        assert target.queries == 1
        assert reopened.get_effect("e1")["state"] == "CONFIRMED"
        assert reopened.get_target_barrier(request().barrier_key) is None


def test_unknown_barrier_survives_lease_and_blocks_other_effect_not_other_target() -> None:
    with StateStore(tenant_id="tenant:one") as store:
        target = Target(lose_response=True, unresolved=True)
        with pytest.raises(TimeoutError):
            gateway(store).run(request(), target)
        with pytest.raises(EffectError, match="COMMIT_UNKNOWN"):
            gateway(store).run(request(), target)
        with pytest.raises(EffectError, match="TARGET_EFFECT_UNRESOLVED"):
            gateway(store).run(request("e2"), target)
        assert target.calls == 1
        target.lose_response = False
        assert gateway(store).run(request("e3", "quote:q2"), target)["operation"] == "e3"


def test_same_identity_changed_payload_and_cross_tenant_are_denied() -> None:
    with StateStore(tenant_id="tenant:one") as store:
        target = Target()
        gateway(store).run(request(), target)
        with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
            gateway(store).run(request().model_copy(update={"payload": {"currency": "USD"}}), target)
        with pytest.raises(EffectError, match="EFFECT_TENANT_MISMATCH"):
            gateway(store).run(request().model_copy(update={"tenant_id": "tenant:two"}), target)
        assert target.calls == 1


def test_empty_or_mismatched_confirmation_never_counts_as_success() -> None:
    class InvalidTarget(Target):
        def execute(self, effect: EffectRequest) -> TargetResolution:
            return TargetResolution(ResolutionState.CONFIRMED, effect.effect_id, effect.digest)

    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="TARGET_POSITIVE_EVIDENCE_REQUIRED"):
            gateway(store).run(request(), InvalidTarget())
        assert store.get_effect("e1")["state"] == "COMMIT_UNKNOWN"
        assert store.get_target_barrier(request().barrier_key) == "e1"


def test_unknown_adapter_state_cannot_release_target_barrier() -> None:
    class InvalidTarget(Target):
        def execute(self, effect: EffectRequest) -> TargetResolution:
            return TargetResolution("unexpected", effect.effect_id, effect.digest, evidence={"v": 1})

    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="TARGET_RESOLUTION_STATE_INVALID"):
            gateway(store).run(request(), InvalidTarget())
        assert store.get_effect("e1")["state"] == "COMMIT_UNKNOWN"
        assert store.get_target_barrier(request().barrier_key) == "e1"


def test_finalizer_and_effect_confirmation_are_one_transaction() -> None:
    with StateStore(tenant_id="tenant:one") as store:
        target = Target()

        def fail_record(connection: object, result: object) -> dict:
            store.save_artifact(connection, "receipt", "application/json", {"value": 1})
            raise RuntimeError("injected finalization failure")

        with pytest.raises(RuntimeError, match="finalization failure"):
            gateway(store).run(request(), target, record=fail_record)
        assert not store.artifact_exists("receipt")
        assert store.get_effect("e1")["state"] == "COMMIT_UNKNOWN"
        assert gateway(store).run(request(), target)["operation"] == "e1"
        assert target.calls == 1


def test_expired_attempt_cannot_confirm_or_release_successor_claim() -> None:
    with StateStore(tenant_id="tenant:one") as store:
        effect = request()
        with store.transaction() as connection:
            store.put_effect(
                connection, effect_id=effect.effect_id, target_key=effect.barrier_key,
                request_digest=effect.digest, request=effect.model_dump(mode="json"),
                created_at="2026-09-09T00:00:00Z",
            )
            first = store.claim_effect(
                connection, effect_id="e1", worker_id="old", now=0, lease_seconds=1,
            )
        with store.transaction() as connection:
            second = store.claim_effect(
                connection, effect_id="e1", worker_id="new", now=2, lease_seconds=1,
            )
            assert second["fence"] > first["fence"]
            assert not store.release_effect_claim(
                connection, effect_id="e1", worker_id="old", fence=first["fence"],
            )
        with (
            pytest.raises(RuntimeError, match="EFFECT_STATE_CONFLICT"),
            store.transaction() as connection,
        ):
            store.update_effect(
                connection, effect_id="e1", expected_state="READY", state="CONFIRMED",
                updated_at="2026-09-09T00:00:01Z", expected_fence=first["fence"],
            )


@pytest.mark.parametrize("revoked_at", [2, 3])
def test_authorization_rechecked_at_dispatch_and_reconciliation_boundary(revoked_at) -> None:
    calls: list[str] = []

    def authorize(effect: EffectRequest, action: str) -> None:
        calls.append(action)
        if len(calls) == revoked_at:
            raise PermissionError("revoked")

    with StateStore(tenant_id="tenant:one") as store:
        target = Target()
        instance = CommitGateway(
            store, tenant_id="tenant:one", worker_id="worker", authorize=authorize,
        )
        with pytest.raises(PermissionError, match="revoked"):
            instance.run(request(), target)
        assert calls == ["effect.execute"] * revoked_at
        assert target.calls == 0
        if revoked_at == 2:
            # Revocation before the claim rolls back without creating an intent.
            assert store.get_effect("e1") is None
        else:
            # Revocation at the I/O boundary keeps the already claimed intent for reconciliation.
            assert store.get_effect("e1")["state"] == "COMMIT_UNKNOWN"


@pytest.mark.parametrize("case,expected", [
    ("unknown", "REMOTE_RESULT_UNCERTAIN"),
    ("identity", "TARGET_RECEIPT_BINDING_MISMATCH"),
    ("missing", "TARGET_POSITIVE_EVIDENCE_REQUIRED"),
    ("state", "TARGET_RESOLUTION_STATE_INVALID"),
])
def test_resolution_failure_preserves_its_durable_reason(case, expected):
    class InvalidTarget(Target):
        def execute(self, effect):
            return TargetResolution(
                "invalid" if case == "state" else ResolutionState.UNKNOWN if case == "unknown" else ResolutionState.CONFIRMED,
                "different" if case == "identity" else effect.effect_id,
                effect.digest, reason="REMOTE_RESULT_UNCERTAIN",
            )
    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError):
            gateway(store).run(request(), InvalidTarget())
        result = store.get_effect("e1")
        assert result["state"] == "COMMIT_UNKNOWN"
        assert result["error_code"] == expected
        assert result["lease_owner"] is None
        assert store.get_target_barrier(request().barrier_key) == "e1"


def test_effect_error_from_record_callback_still_marks_finalization_unknown():
    with StateStore(tenant_id="tenant:one") as store:
        target = Target()
        def record(connection, result):
            store.save_artifact(connection, "unfinished", "application/json", {"v": 1})
            raise EffectError("CALLBACK_FAILED")
        with pytest.raises(EffectError, match="CALLBACK_FAILED"):
            gateway(store).run(request(), target, record=record)
        effect = store.get_effect("e1")
        assert effect["error_code"] == "EFFECT_FINALIZATION_UNAVAILABLE"
        assert effect["state"] == "COMMIT_UNKNOWN" and effect["lease_owner"] is None
        assert not store.artifact_exists("unfinished")
        assert gateway(store).run(request(), target)["operation"] == "e1"
        assert target.calls == 1


def test_reconciliation_callback_failure_replaces_previous_unknown_reason():
    with StateStore(tenant_id="tenant:one") as store:
        target = Target(lose_response=True)
        with pytest.raises(TimeoutError):
            gateway(store).run(request(), target)
        assert store.get_effect("e1")["error_code"] == "TARGET_RESPONSE_UNAVAILABLE"
        def record(connection, result):
            raise EffectError("RECONCILIATION_RECORD_FAILED")
        with pytest.raises(EffectError, match="RECONCILIATION_RECORD_FAILED"):
            gateway(store).run(request(), target, record=record)
        assert store.get_effect("e1")["error_code"] == "EFFECT_FINALIZATION_UNAVAILABLE"
        assert store.get_effect("e1")["lease_owner"] is None
        assert target.calls == target.queries == 1


def test_proven_absence_preserves_same_request_retry_and_barrier():
    class InitiallyAbsent(Target):
        def execute(self, effect):
            if self.calls == 0:
                self.calls += 1
                raise TimeoutError("BEFORE_WRITE")
            return super().execute(effect)
        def query_effect(self, effect):
            self.queries += 1
            return TargetResolution(ResolutionState.ABSENT_FINAL, effect.effect_id, effect.digest,
                                    evidence={"absence_proven": True}, reason="ABSENCE_CONFIRMED")
    with StateStore(tenant_id="tenant:one") as store:
        target = InitiallyAbsent()
        with pytest.raises(TimeoutError):
            gateway(store).run(request(), target)
        with pytest.raises(EffectError, match="EFFECT_ABSENT_FINAL_RETRY_AUTHORIZED"):
            gateway(store).run(request(), target)
        assert store.get_effect("e1")["state"] == "READY"
        assert store.get_target_barrier(request().barrier_key) == "e1"
        with pytest.raises(EffectError, match="TARGET_EFFECT_UNRESOLVED"):
            gateway(store).run(request("other"), target)
        assert gateway(store).run(request(), target)["operation"] == "e1"
        assert store.get_effect("e1")["state"] == "CONFIRMED"
        assert store.get_target_barrier(request().barrier_key) is None
        assert target.calls == 2 and target.queries == 1
