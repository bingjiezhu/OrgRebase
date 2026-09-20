from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from orgrebase.clock import FrozenClock, SystemClock, timestamp, utc_datetime, workflow_times
from orgrebase.domain import IntegrityError
from orgrebase.http_errors import public_error


class MutableClock:
    value = "2026-08-14T00:03:00Z"

    def now(self):
        return self.value


def arguments(service):
    service.preview()
    approval = service.workflow.approve(service.change_set, service.preview_receipt, service.minimal_rebase_certificate)
    return {
        "change_set": service.change_set, "preview": service.preview_receipt,
        "minimal_rebase_certificate": service.minimal_rebase_certificate,
        "approval": approval, "collaboration": service.collaboration, "run_envelope": service.run_envelope,
    }


def test_clock_parses_timezone_and_real_utc():
    assert utc_datetime("2026-09-09T08:00:00+08:00") == datetime(2026, 9, 9, tzinfo=UTC)
    assert utc_datetime(SystemClock().now()).tzinfo == UTC
    assert timestamp(datetime(2026, 9, 9, tzinfo=UTC)) == "2026-09-09T00:00:00Z"
    with pytest.raises(ValueError):
        timestamp(datetime(2026, 9, 9))
    for value in ["2026-09-09T00:00:00", "garbage", None]:
        with pytest.raises(ValueError):
            utc_datetime(value)
    times = workflow_times(FrozenClock("2026-09-09T00:00:00Z"))
    assert times.approved_at == times.apply_at == "2026-09-09T00:00:00Z"
    assert times.expires_at == "2026-09-09T00:15:00Z"
    with pytest.raises(ValueError):
        workflow_times(FrozenClock("2026-09-09T00:00:00Z"), 0)


def test_real_clock_approval_uses_current_time_and_ttl(service):
    clock = MutableClock()
    service.workflow.execution_clock = clock
    call = arguments(service)
    assert call["approval"].approved_at == clock.value
    assert call["approval"].expires_at == "2026-08-14T00:18:00Z"
    # Equality is valid, and one captured timestamp describes the entire commit.
    receipt = service.workflow.apply(**call)
    assert receipt.status == "COMPLETED"


@pytest.mark.parametrize("now", ["2026-08-14T00:18:00Z", "2026-08-14T00:19:00Z", "2026-08-14T00:02:59Z"])
def test_expired_future_or_boundary_approval_does_not_write(service, now):
    clock = MutableClock()
    service.workflow.execution_clock = clock
    call = arguments(service)
    before = tuple(service.store.connection.iterdump())
    clock.value = now
    with pytest.raises(IntegrityError, match="APPROVAL_TIME_WINDOW_INVALID") as rejected:
        service.workflow.apply(**call)
    assert public_error(rejected.value)["message"] == "APPROVAL_TIME_WINDOW_INVALID"
    assert public_error(rejected.value, message_as_code=True)["code"] == "APPROVAL_TIME_WINDOW_INVALID"
    assert tuple(service.store.connection.iterdump()) == before


def test_approval_expiring_while_waiting_for_transaction_is_rejected(service, monkeypatch):
    clock = MutableClock()
    service.workflow.execution_clock = clock
    call = arguments(service)
    before = tuple(service.store.connection.iterdump())
    transaction = service.store.transaction
    @contextmanager
    def delayed_transaction():
        with transaction() as connection:
            clock.value = "2026-08-14T00:18:00Z"
            yield connection
    monkeypatch.setattr(service.store, "transaction", delayed_transaction)
    with pytest.raises(IntegrityError, match="APPROVAL_TIME_WINDOW_INVALID") as rejected:
        service.workflow.apply(**call)
    assert public_error(rejected.value)["message"] == "APPROVAL_TIME_WINDOW_INVALID"
    assert public_error(rejected.value, message_as_code=True)["code"] == "APPROVAL_TIME_WINDOW_INVALID"
    assert tuple(service.store.connection.iterdump()) == before


def test_commit_reauthorizes_inside_transaction_before_any_mutation(service):
    call = arguments(service)
    before = tuple(service.store.connection.iterdump())
    def denied():
        assert service.store.connection.in_transaction
        raise PermissionError("authorization revoked")
    service.workflow.authorize_commit = denied
    with pytest.raises(PermissionError, match="revoked"):
        service.workflow.apply(**call)
    assert tuple(service.store.connection.iterdump()) == before


@pytest.mark.parametrize("state", ["STALE", "QUARANTINED", "REVIEW_REQUIRED"])
def test_unavailable_source_cannot_be_approved_or_applied(service, state):
    from orgrebase.domain import FreshnessError, ObjectState

    call = arguments(service)
    source_id = service.change_set.deltas[0].object_id
    with service.store.transaction() as connection:
        service.store.transition_current(connection, source_id, ObjectState(state))
    before = tuple(service.store.connection.iterdump())
    with pytest.raises(FreshnessError, match="SOURCE_PREMISE_NOT_CURRENT"):
        service.workflow.approve(service.change_set, service.preview_receipt, service.minimal_rebase_certificate)
    with pytest.raises(FreshnessError, match="SOURCE_PREMISE_NOT_CURRENT"):
        service.workflow.apply(**call)
    assert tuple(service.store.connection.iterdump()) == before


def test_readmission_contract_cannot_be_forged_with_trust_flag_alone(service):
    from orgrebase.domain import ObjectState, SemanticClassification

    service.preview()
    change = service.change_set
    source_id = change.deltas[0].object_id
    with service.store.transaction() as connection:
        service.store.transition_current(connection, source_id, ObjectState.STALE)
    delta = change.deltas[0].model_copy(update={
        "semantic_classification": SemanticClassification.TRUST_REVALIDATION,
        "changed_fields": ("trust_state",), "proposed_value": change.deltas[0].base_value,
        "admitted_by": change.owner_id,
    })
    same_value = change.model_copy(update={"deltas": (delta,)})
    with pytest.raises(IntegrityError, match="SOURCE_READMISSION_CONTRACT_REQUIRED"):
        service.workflow._assert_current_sources(same_value)
    explicit = same_value.model_copy(update={"state": "READMISSION_ADMITTED"})
    service.workflow._assert_current_sources(explicit)
    malformed = explicit.model_copy(update={"deltas": (delta.model_copy(update={"admitted_by": "other-owner"}),)})
    with pytest.raises(IntegrityError, match="SOURCE_READMISSION_CONTRACT_INVALID"):
        service.workflow._assert_current_sources(malformed)
