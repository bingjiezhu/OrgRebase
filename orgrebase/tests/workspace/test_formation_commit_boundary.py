from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from orgrebase.auth import request_authorization
from orgrebase.domain import IntegrityError
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.formation_integrity import verify_prepared_formation
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture(params=("sqlite", "postgres"))
def commit_service(request, tmp_path):
    if request.param == "postgres":
        tenant = WorkspaceFormationService.default_request().organization_id
        runtime = request.getfixturevalue("postgres_runtime")(tenant_id=tenant)
        service = WorkspaceService(
            store_path=runtime["runtime_dsn"], store_tenant_id=tenant, store_migrate=False,
        )
    else:
        service = WorkspaceService(store_path=tmp_path / "formation.sqlite")
    try:
        yield service
    finally:
        service.close()


@pytest.mark.parametrize("caller_transaction", (False, True))
def test_commit_verifies_once_and_rechecks_authorization_on_replay(commit_service, caller_transaction):
    service = commit_service
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    authorized = []
    token = request_authorization.set(lambda: authorized.append(True))
    try:
        with patch(
            "orgrebase.workspace.formation_integrity.verify_prepared_formation",
            wraps=verify_prepared_formation,
        ) as verify:
            if caller_transaction:
                with service.store.transaction() as connection:
                    first = service.formation.commit_quote(prepared, connection=connection)
            else:
                first = service.formation.commit_quote(prepared)
            assert verify.call_count == 1
            before = service.store.verify_event_chain()
            replay = service.formation.commit_quote(prepared)
            assert verify.call_count == 2
        assert first == replay
        assert authorized == [True, True, True, True]
        assert service.store.verify_event_chain() == before
    finally:
        request_authorization.reset(token)


def test_revoked_authorization_cannot_replay_a_committed_receipt(commit_service):
    service = commit_service
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    service.formation.commit_quote(prepared)
    before = service.store.verify_event_chain()

    def revoked():
        raise PermissionError("MEMBERSHIP_REVOKED")

    token = request_authorization.set(revoked)
    try:
        with pytest.raises(PermissionError, match="MEMBERSHIP_REVOKED"):
            service.formation.commit_quote(prepared)
    finally:
        request_authorization.reset(token)
    assert service.store.verify_event_chain() == before


@pytest.mark.parametrize("interruption", (RuntimeError, KeyboardInterrupt))
def test_caller_rollback_removes_the_whole_formation(commit_service, interruption):
    service = commit_service
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    before = service.store.verify_event_chain()
    with pytest.raises(interruption), service.store.transaction() as connection:
        service.formation.commit_quote(prepared, connection=connection)
        raise interruption("caller aborted")
    assert service.state()["quote"] is None
    assert service.store.get_idempotent(prepared.idempotency_key, prepared.request_digest) is None
    assert service.store.verify_event_chain() == before
    service.formation.commit_quote(prepared)
    assert service.current_quote().version == "v1"


def test_idempotent_replay_still_rejects_resealed_false_proof(commit_service):
    service = commit_service
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    service.formation.commit_quote(prepared)
    before = service.store.verify_event_chain()
    quote = prepared.deliverable.model_dump(mode="json", exclude={"digest"})
    quote["payload"]["currency"] = "BTC"
    forged_quote = type(prepared.deliverable).model_validate(quote)
    bundle = prepared.model_dump(mode="json", exclude={"digest"})
    bundle["deliverable"] = forged_quote.model_dump(mode="json")
    forged = type(prepared).model_validate(bundle)
    with pytest.raises(
        IntegrityError, match="PREPARED_FORMATION_PROOF_GRAPH_INVALID:DELIVERABLE_REPLAY_MISMATCH",
    ):
        service.formation.commit_quote(forged)
    assert service.current_quote().payload["currency"] == "USD"
    assert service.store.verify_event_chain() == before


def test_service_replay_reauthorizes_once_without_repreparing_or_reverifying(commit_service):
    service = commit_service
    authorized = []
    token = request_authorization.set(lambda: authorized.append(True))
    try:
        with patch.object(service.formation, "prepare_quote", wraps=service.formation.prepare_quote) as prepare, patch(
            "orgrebase.workspace.formation_integrity.verify_prepared_formation",
            wraps=verify_prepared_formation,
        ) as verify:
            first = service.form_quote()
            assert authorized == [True, True]
            assert prepare.call_count == verify.call_count == 1
            before = service.store.count_records()
            chain = service.store.verify_event_chain()
            replay = service.form_quote()
            assert authorized == [True, True, True]
            assert prepare.call_count == verify.call_count == 1
        assert replay == first
        assert service.store.count_records() == before
        assert service.store.verify_event_chain() == chain
    finally:
        request_authorization.reset(token)


@pytest.mark.parametrize("with_dependency_evidence", (False, True))
def test_service_revocation_rejects_cached_formation_without_new_records(commit_service, with_dependency_evidence):
    service = commit_service
    form = service.form_quote_with_dependency_evidence if with_dependency_evidence else service.form_quote
    form()
    before = service.store.count_records()
    chain = service.store.verify_event_chain()
    authorization_calls = []

    def revoked():
        authorization_calls.append(True)
        raise PermissionError("MEMBERSHIP_REVOKED")

    token = request_authorization.set(revoked)
    try:
        with pytest.raises(PermissionError, match="MEMBERSHIP_REVOKED"):
            form()
    finally:
        request_authorization.reset(token)
    assert authorization_calls == [True]
    assert service.store.count_records() == before
    assert service.store.verify_event_chain() == chain


@dataclass
class MutableClock:
    value: str

    def now(self) -> str:
        return self.value


@pytest.mark.parametrize("boundary", ("after_verification", "after_persistence", "after_borrowed_return"))
@pytest.mark.parametrize("change", ("revoked", "local_lease_expired"))
def test_commit_rechecks_authorization_and_local_lease_at_owned_transaction_exit(
    commit_service, boundary, change,
):
    service = commit_service
    clock = MutableClock(service.formation.clock.now())
    service.formation.clock = clock
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    context = next(
        write.payload for write in prepared.artifact_writes
        if write.artifact_id == prepared.task_receipt.context_manifest_ref
    )
    before = service.store.count_records()
    chain = service.store.verify_event_chain()
    allowed = True

    def authorize():
        if not allowed:
            raise PermissionError("MEMBERSHIP_REVOKED")

    def change_boundary():
        nonlocal allowed
        if change == "revoked":
            allowed = False
        else:
            # The original lease is half-open: its exact expiry is invalid.
            clock.value = context["expires_at"]

    def verify(*args, **kwargs):
        lease = verify_prepared_formation(*args, **kwargs)
        if boundary == "after_verification":
            change_boundary()
        return lease

    persist = service.store.save_idempotent

    def save_idempotent(*args, **kwargs):
        persist(*args, **kwargs)
        if boundary == "after_persistence":
            change_boundary()

    expected_error = PermissionError if change == "revoked" else IntegrityError
    expected_message = "MEMBERSHIP_REVOKED" if change == "revoked" else "LOCAL_FORMATION_CLOCK_OUTSIDE_LEASE"
    token = request_authorization.set(authorize)
    try:
        with (
            patch("orgrebase.workspace.formation_integrity.verify_prepared_formation", side_effect=verify) as verifier,
            patch.object(service.store, "save_idempotent", side_effect=save_idempotent),
            pytest.raises(expected_error, match=expected_message),
            service.store.transaction() if boundary == "after_borrowed_return" else nullcontext(None) as connection,
        ):
            service.formation.commit_quote(prepared, connection=connection)
            if boundary == "after_borrowed_return":
                service.store.append_event(connection, "CALLER_CONTINUED", {})
                change_boundary()
        assert verifier.call_count == 1
    finally:
        request_authorization.reset(token)
    assert service.store.count_records() == before
    assert service.store.verify_event_chain() == chain
    assert service.store.get_idempotent(prepared.idempotency_key, prepared.request_digest) is None
    with pytest.raises(KeyError):
        service.store.get_object(prepared.deliverable.id)
    clock.value = context["created_at"]
    assert service.formation.commit_quote(prepared) == prepared.task_receipt


@pytest.mark.parametrize("failure", (RuntimeError, KeyboardInterrupt))
def test_swallowed_borrowed_formation_failure_still_aborts_whole_transaction(commit_service, failure):
    service = commit_service
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    before = service.store.count_records()
    chain = service.store.verify_event_chain()
    save = service.store.save_artifact

    def fail_after_artifact(*args, **kwargs):
        save(*args, **kwargs)
        raise failure("formation interrupted")

    with pytest.raises(IntegrityError, match="FORMATION_COMMIT_INCOMPLETE"), service.store.transaction() as connection:
        service.store.append_event(connection, "CALLER_STARTED", {})
        with patch.object(service.store, "save_artifact", side_effect=fail_after_artifact), pytest.raises(failure):
            service.formation.commit_quote(prepared, connection=connection)
        service.store.append_event(connection, "CALLER_CONTINUED", {})
    assert service.store.count_records() == before
    assert service.store.verify_event_chain() == chain
    assert service.store.get_idempotent(prepared.idempotency_key, prepared.request_digest) is None
    assert service.formation.commit_quote(prepared) == prepared.task_receipt


def test_cached_receipt_does_not_renew_or_reapply_expired_local_formation(commit_service):
    service = commit_service
    clock = MutableClock(service.formation.clock.now())
    service.formation.clock = clock
    prepared = service.formation.prepare_quote(WorkspaceFormationService.default_request())
    receipt = service.formation.commit_quote(prepared)
    before = service.store.count_records()
    chain = service.store.verify_event_chain()
    context = next(
        write.payload for write in prepared.artifact_writes
        if write.artifact_id == prepared.task_receipt.context_manifest_ref
    )
    clock.value = context["expires_at"]
    with pytest.raises(IntegrityError, match="LOCAL_FORMATION_CLOCK_OUTSIDE_LEASE"):
        service.formation.commit_quote(prepared)
    assert service.form_quote() == receipt
    assert service.store.count_records() == before
    assert service.store.verify_event_chain() == chain
