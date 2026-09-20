from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from orgrebase.domain import (
    FreshnessError,
    IntegrityError,
    ObjectState,
    VersionedObject,
)
from orgrebase.service import OrgRebaseService
from orgrebase.workflow import RebaseWorkflow


def _apply_arguments(service: OrgRebaseService) -> dict:
    service.preview()
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )
    return {
        "change_set": service.change_set,
        "preview": service.preview_receipt,
        "minimal_rebase_certificate": service.minimal_rebase_certificate,
        "approval": approval,
        "collaboration": service.collaboration,
        "run_envelope": service.run_envelope,
    }


def _database_snapshot(service: OrgRebaseService) -> tuple[str, ...]:
    return tuple(service.store.connection.iterdump())


def test_core_has_no_implicit_legacy_rebuild_fallback(service: OrgRebaseService) -> None:
    arguments = _apply_arguments(service)
    before = _database_snapshot(service)
    workflow = RebaseWorkflow(service.fixture, service.store)
    with pytest.raises(IntegrityError, match="NO_REBUILD_HANDLER:<missing>"):
        workflow.apply(**arguments)
    assert _database_snapshot(service) == before


def test_missing_one_handler_rolls_back_the_entire_change(
    service: OrgRebaseService, tmp_path: Path
) -> None:
    data = service.fixture.model_dump(mode="json")
    support = next(item for item in data["objects"] if item["id"] == "work:support_doc_b")
    support["payload"]["deliverable_kind"] = "SUPPORT_DOCUMENT"
    support["digest"] = ""
    fixture_path = tmp_path / "unsupported-deliverable.json"
    fixture_path.write_text(json.dumps(data), encoding="utf-8")
    runtime = OrgRebaseService(fixture_path=fixture_path)
    try:
        arguments = _apply_arguments(runtime)
        before = _database_snapshot(runtime)
        with pytest.raises(IntegrityError, match="NO_REBUILD_HANDLER:SUPPORT_DOCUMENT"):
            runtime.workflow.apply(**arguments)
        assert _database_snapshot(runtime) == before
    finally:
        runtime.store.close()


@pytest.mark.parametrize(
    "mutation",
    ["unknown_object", "unknown_kind", "changed_payload", "new_version", "wrong_delta", "wrong_context"],
)
def test_legacy_adapter_is_closed_to_unregistered_business_inputs(
    service: OrgRebaseService, mutation: str
) -> None:
    service.preview()
    handler = service.workflow.rebuild_handlers[""]
    old = service.store.get_object("work:sales_quote_a")
    delta = service.change_set.deltas[0]
    # The context consumes the proposed source only after its transactional promotion.
    with service.store.transaction() as connection:
        service.store.promote_version(
            connection, delta.object_id, delta.base_version, delta.proposed_version
        )
    context = service.workflow.context_compiler.compile("sales-agent")
    if mutation == "unknown_object":
        old = old.model_copy(update={"id": "work:unregistered", "digest": ""})
    elif mutation == "unknown_kind":
        old = old.model_copy(update={"kind": "InvoiceVersion", "digest": ""})
    elif mutation == "changed_payload":
        old = old.model_copy(update={"payload": {**old.payload, "amount": "1000.00"}, "digest": ""})
    elif mutation == "new_version":
        old = old.model_copy(update={"version": "v2", "digest": ""})
    elif mutation == "wrong_delta":
        data = delta.model_dump(mode="json")
        data.update(object_id="policy:finance.currency", digest="")
        delta = type(delta).model_validate(data)
    else:
        context = context.model_copy(update={"target_object_id": "work:other", "digest": ""})
    with pytest.raises(IntegrityError, match=r"LEGACY_REBUILD_.*_NOT_ADMITTED"):
        handler.rebuild_payload(old=old, deltas=(delta,), context=context)


def test_handler_business_validation_failure_is_zero_write(
    service: OrgRebaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _apply_arguments(service)
    handler = service.workflow.rebuild_handlers[""]
    original_rebuild = handler.rebuild_payload

    def corrupt_payload(**kwargs):
        return {**original_rebuild(**kwargs), "canonical_premise": "unchanged-business-value"}

    # Keep the independent validator intact while corrupting only the producer.
    monkeypatch.setattr(handler, "rebuild_payload", corrupt_payload)
    before = _database_snapshot(service)
    with pytest.raises(IntegrityError, match="LEGACY_REBUILD_PAYLOAD_MISMATCH"):
        service.workflow.apply(**arguments)
    assert _database_snapshot(service) == before


@pytest.mark.parametrize(
    ("object_id", "state_only"),
    [
        ("work:sales_quote_a", False),
        ("work:legal_review_c", False),
        ("work:partner_brief_e", True),
        ("skill:enterprise-launch-readiness", True),
        ("claim:product.launch_date", True),
    ],
)
def test_apply_rechecks_entire_effect_set_inside_transaction(
    service: OrgRebaseService,
    monkeypatch: pytest.MonkeyPatch,
    object_id: str,
    state_only: bool,
) -> None:
    arguments = _apply_arguments(service)
    original_qualify = service.workflow.skill_evaluator.qualify
    committed_concurrent_state = []

    def qualify_with_concurrent_edit():
        result = original_qualify()
        with service.store.transaction() as connection:
            if state_only:
                service.store.transition_current(connection, object_id, ObjectState.REVIEW_REQUIRED)
            else:
                data = service.store.get_object(object_id).model_dump(mode="json")
                data.update(version="v3", digest="")
                data["payload"] = {**data["payload"], "independent_business_edit": "must-survive"}
                service.store.insert_version(
                    connection, VersionedObject.model_validate(data), make_current=True
                )
        committed_concurrent_state.extend(_database_snapshot(service))
        return result

    monkeypatch.setattr(service.workflow.skill_evaluator, "qualify", qualify_with_concurrent_edit)
    expected_error = "SOURCE_PREMISE_NOT_CURRENT" if object_id == "claim:product.launch_date" else "APPLY_OBJECT_DRIFT"
    with pytest.raises(FreshnessError, match=expected_error):
        service.workflow.apply(**arguments)
    assert _database_snapshot(service) == tuple(committed_concurrent_state)


def test_concurrent_same_request_returns_one_committed_receipt(
    service: OrgRebaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _apply_arguments(service)
    initial_records = service.store.connection.execute(
        "SELECT COUNT(*) FROM idempotency_records"
    ).fetchone()[0]
    both_checked = Barrier(2)
    original_get = service.store.get_idempotent

    def get_after_both_preflights(key, request_digest, *, connection=None):
        result = original_get(key, request_digest, connection=connection)
        if connection is None and result is None:
            both_checked.wait(timeout=5)
        return result

    monkeypatch.setattr(service.store, "get_idempotent", get_after_both_preflights)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(service.workflow.apply, **arguments) for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]
    assert receipts[0] == receipts[1]
    assert service.store.get_object("work:sales_quote_a").version == "v2"
    assert service.store.connection.execute(
        "SELECT COUNT(*) FROM idempotency_records"
    ).fetchone()[0] == initial_records + 1
    assert service.store.verify_event_chain()["events"] == 4
