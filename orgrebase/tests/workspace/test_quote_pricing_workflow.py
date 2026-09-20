"""Amount changes cross the same admission, approval and commit boundaries as other rules."""
from __future__ import annotations

from copy import deepcopy

import pytest

from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.rebuild import QuoteRebuildPayloadHandler
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_priced_quote_pack import POLICY, priced_draft


@pytest.fixture
def priced_service(tmp_path):
    seal_enterprise_quote_pilot_pack(priced_draft(tmp_path), tmp_path / "sealed")
    runtime = load_enterprise_quote_pilot_pack(tmp_path / "sealed")
    service = WorkspaceService(store_path=tmp_path / "quote.sqlite", runtime_configuration=runtime,
                               review_duration_seconds=0)
    try:
        yield service, runtime, tmp_path / "quote.sqlite"
    finally:
        service.close()


def propose(service, slot="pricing_policy", value=None, event="discount-10"):
    current = next(item for item in change_options(service)["fields"] if item["slot_id"] == slot)["current"]
    submit_change(service, ChangeProposalInput(
        event_id=event, slot_id=slot, base_version=current["version"], base_digest=current["digest"],
        value=value if value is not None else {**POLICY, "discount_bps": 1000},
        source_ref="source:controlled-finance-decision@v2",
    ))
    return service.preview_change(event)


def approve(service, bundle, event="discount-10"):
    return service.approve_change(event, actor_id=service.change_owner[event],
                                  preview_digest=bundle.preview.digest)


def test_prices_are_computed_previewed_approved_and_persisted(priced_service):
    service, runtime, path = priced_service
    service.form_quote()
    before = service.current_quote()
    assert before.payload["pricing"]["subtotal"] == "37.50"
    assert before.payload["pricing"]["total"] == "42.74"
    bundle = propose(service)
    assert bundle.pricing_comparison.before.total == "42.74"
    assert bundle.pricing_comparison.after.total == "40.50"
    assert bundle.pricing_comparison.before.basket_digest == bundle.pricing_comparison.after.basket_digest
    assert service.current_quote().digest == before.digest
    with pytest.raises(RuntimeError, match="WORKSPACE_APPROVAL_REQUIRED"):
        service.apply_approved_change("discount-10", approval_digest="sha256:" + "0" * 64)
    with pytest.raises(AuthorizationError, match="WORKSPACE_APPROVER_MISMATCH"):
        service.approve_change("discount-10", actor_id="wrong-owner", preview_digest=bundle.preview.digest)
    approval = approve(service, bundle)
    result = service.apply_approved_change("discount-10", approval_digest=approval["approval_digest"])
    after = service.current_quote()
    assert after.payload["pricing"]["total"] == "40.50"
    assert after.payload["pricing"] == bundle.pricing_comparison.after.model_dump(mode="json")
    assert service.apply_approved_change("discount-10", approval_digest=approval["approval_digest"]) == result
    service.close()
    reopened = WorkspaceService.reopen(path, runtime_configuration=runtime, review_duration_seconds=0)
    try:
        assert reopened.current_quote().digest == after.digest
        assert reopened._preview_record("discount-10")["pricing_comparison"]["before"]["total"] == "42.74"
        date_preview = propose(reopened, "launch_date", "2026-10-15", "date-only")
        assert date_preview.pricing_comparison.before == date_preview.pricing_comparison.after
        date_approval = approve(reopened, date_preview, "date-only")
        reopened.apply_approved_change("date-only", approval_digest=date_approval["approval_digest"])
        assert reopened.current_quote().payload["pricing"] == after.payload["pricing"]
    finally:
        reopened.close()


def test_forged_formation_amount_cannot_be_committed(priced_service):
    service, _, _ = priced_service
    prepared = service.formation.prepare_quote(service.profile.task_request())
    body = prepared.deliverable.model_dump(mode="json", exclude={"digest"})
    body["payload"]["pricing"]["total"] = "0.01"
    forged_quote = type(prepared.deliverable).model_validate(body)
    bundle = prepared.model_dump(mode="json", exclude={"digest"})
    bundle["deliverable"] = forged_quote.model_dump(mode="json")
    forged = type(prepared).model_validate(bundle)
    before = service.store.count_records()
    with pytest.raises(IntegrityError, match="DELIVERABLE_REPLAY_MISMATCH"):
        service.formation.commit_quote(forged)
    assert service.store.count_records() == before
    assert service.state()["quote"] is None


def test_unit_price_change_recomputes_amounts_under_the_basket_owner(priced_service):
    service, _, _ = priced_service
    service.form_quote()
    before = service.current_quote()
    policy = service.store.get_object("policy:finance.pricing")
    basket = deepcopy(next(item for item in change_options(service)["fields"]
                           if item["slot_id"] == "quote_basket")["current"]["value"])
    basket["items"][0]["unit_price"] = "13.00"
    bundle = propose(service, "quote_basket", basket, "catalog-price")
    assert bundle.pricing_comparison.after.subtotal == "39.00"
    assert bundle.pricing_comparison.after.discount_amount == "1.95"
    assert bundle.pricing_comparison.after.tax_amount == "7.41"
    assert bundle.pricing_comparison.after.total == "44.46"
    assert service.current_quote().digest == before.digest
    assert bundle.pricing_comparison.before.policy_digest == bundle.pricing_comparison.after.policy_digest
    approval = approve(service, bundle, "catalog-price")
    service.apply_approved_change("catalog-price", approval_digest=approval["approval_digest"])
    assert service.current_quote().payload["pricing"] == bundle.pricing_comparison.after.model_dump(mode="json")
    assert service.store.get_object("policy:finance.pricing").digest == policy.digest


def test_corrupted_rebuild_amount_rolls_back_quote_and_policy(priced_service, monkeypatch):
    service, _, _ = priced_service
    service.form_quote()
    bundle = propose(service)
    approval = approve(service, bundle)
    before = service.current_quote()
    policy = service.store.get_object("policy:finance.pricing")
    pointer = service.current_graph_pointer()
    original = QuoteRebuildPayloadHandler.rebuild_payload

    def corrupt(self, **kwargs):
        value = deepcopy(original(self, **kwargs))
        value["pricing"]["total"] = "0.01"
        return value

    monkeypatch.setattr(QuoteRebuildPayloadHandler, "rebuild_payload", corrupt)
    with pytest.raises(IntegrityError, match="QUOTE_REBUILD_PRICING_MISMATCH"):
        service.apply_approved_change("discount-10", approval_digest=approval["approval_digest"])
    assert service.current_quote().digest == before.digest
    assert service.store.get_object("policy:finance.pricing").digest == policy.digest
    assert service.current_graph_pointer().digest == pointer.digest
