"""Free text in a priced quote is data; forbidden structural fields remain denied."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.rebuild import QuoteRebuildPayloadHandler
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_priced_quote_pack import POLICY, priced_draft


@pytest.fixture
def service(tmp_path):
    draft = priced_draft(tmp_path)
    path = draft / "components/knowledge.json"
    knowledge = json.loads(path.read_text())
    basket = next(item["value"] for item in knowledge["projection"]["source_values"]
                  if item["slot_id"] == "quote_basket")
    basket["items"][0]["description"] = "SECRET GARDEN NOTEBOOK"
    path.write_text(json.dumps(knowledge))
    seal_enterprise_quote_pilot_pack(draft, tmp_path / "sealed")
    instance = WorkspaceService(store_path=tmp_path / "quote.sqlite",
                                runtime_configuration=load_enterprise_quote_pilot_pack(tmp_path / "sealed"),
                                review_duration_seconds=0)
    try:
        instance.form_quote()
        yield instance
    finally:
        instance.close()


def approve_discount(service):
    current = next(field for field in change_options(service)["fields"]
                   if field["slot_id"] == "pricing_policy")["current"]
    submit_change(service, ChangeProposalInput(event_id="discount", slot_id="pricing_policy",
        base_version=current["version"], base_digest=current["digest"], value={**POLICY, "discount_bps": 1000},
        source_ref="source:reviewed-policy@v2"))
    preview = service.preview_change("discount")
    return service.approve_change("discount", actor_id=service.change_owner["discount"],
                                  preview_digest=preview.preview.digest)


def test_normal_product_description_survives_approved_price_change(service):
    approval = approve_discount(service)
    service.apply_approved_change("discount", approval_digest=approval["approval_digest"])
    pricing = service.current_quote().payload["pricing"]
    assert pricing["lines"][0]["description"] == "SECRET GARDEN NOTEBOOK"
    assert pricing["total"] == "40.50"


@pytest.mark.parametrize("location,key", [("root", "raw_contract_text"), ("line", "SECRET"), ("line", "internal_cost_floor")])
def test_sensitive_structural_fields_are_rejected_without_canonical_writes(service, monkeypatch, location, key):
    approval = approve_discount(service)
    before_quote = service.current_quote()
    before_policy = service.store.get_object("policy:finance.pricing")
    before_pointer = service.current_graph_pointer()
    original = QuoteRebuildPayloadHandler.rebuild_payload

    def inject(self, **kwargs):
        payload = deepcopy(original(self, **kwargs))
        target = payload if location == "root" else payload["pricing"]["lines"][0]
        target[key] = "Restricted content"
        return payload

    monkeypatch.setattr(QuoteRebuildPayloadHandler, "rebuild_payload", inject)
    with pytest.raises(IntegrityError, match="QUOTE_REBUILD_FORBIDDEN_SENSITIVE_FIELD"):
        service.apply_approved_change("discount", approval_digest=approval["approval_digest"])
    assert service.current_quote().digest == before_quote.digest
    assert service.store.get_object("policy:finance.pricing").digest == before_policy.digest
    assert service.current_graph_pointer().digest == before_pointer.digest
