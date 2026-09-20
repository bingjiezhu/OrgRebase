"""Priced pack admission preserves legacy contracts and structured input boundaries."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from enterprise_pack_factory import make_enterprise_pack
from pydantic import ValidationError

from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.models import DomainPack, EnterpriseBinding, TaskRequest
from orgrebase.workspace.pilot import EnterpriseQuotePilotPackError, load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.planner import CoalitionPlanner
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.task_agent import TemplateBoundTaskInterpreter
from orgrebase.workspace.templates import (
    TemplateRegistry,
    default_capability_cards,
    enterprise_quote_template,
    selected_capability_cards,
)

ROOT = Path(__file__).resolve().parents[2]
BASKET = {"currency": "USD", "items": [{"line_id": "line-1", "sku": "SKU-1",
          "description": "Controlled test item", "quantity": 3, "unit_price": "12.50"}],
          "source_ref": "source:test-basket@v1"}
POLICY = {"discount_bps": 500, "tax_bps": 2000, "tax_label": "Controlled test tax",
          "source_ref": "source:test-policy@v1"}


def priced_draft(root: Path) -> Path:
    old = make_enterprise_pack(root / "legacy")
    draft = root / "priced-draft"
    shutil.copytree(old, draft)
    values = {name: json.loads((draft / name).read_text()) for name in (
        "pack.json", "profile.json", "components/domain.json", "components/knowledge.json",
        "components/capability.json")}
    profile = values["profile.json"]
    template = TemplateRegistry().get("template:enterprise_quote@v2")
    profile["default_task"]["template_ref"] = template.ref
    values["components/domain.json"]["projection"]["default_task"] = profile["default_task"]
    sources = values["components/knowledge.json"]["projection"]["source_values"]
    manifest = values["pack.json"]
    binding = manifest["enterprise_binding"]
    for slot, parent, object_id, value in (
        ("quote_basket", "product_plan", "claim:product.quote_basket", BASKET),
        ("pricing_policy", "currency", "policy:finance.pricing", POLICY),
    ):
        source = next(item for item in sources if item["slot_id"] == parent)
        sources.append({**source, "slot_id": slot, "object_ref": object_id + "@v1", "value": value,
                        "sensitivity": "INTERNAL", "raw_private_value": None})
        owner = next(item for item in binding["resources"] if item["slot_id"] == parent)
        binding["resources"].append({**owner, "slot_id": slot, "object_id": object_id})
    binding["domain_pack_digest"] = DomainPack.enterprise_quote(template.ref).digest
    binding.pop("digest")
    manifest["enterprise_binding"] = EnterpriseBinding.model_validate(binding).model_dump(mode="json")
    capability = values["components/capability.json"]["projection"]
    capability["template"] = {"ref": template.ref, "digest": template.digest}
    capability["capability_cards"] = [{"ref": card.ref, "digest": card.digest}
                                     for card in default_capability_cards(template.ref)]
    capability["runtime_components"]["renderer"] = "orgrebase.workspace.execution.QuoteRenderer@2.0.0"
    for name, value in values.items():
        (draft / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return draft


def test_v1_template_cards_and_sealed_pack_remain_unchanged() -> None:
    assert enterprise_quote_template().digest == "sha256:0550a9bb3e5dbf478a1639804994a9a46c7790389a5bcae28d2f0ff3009ffe97"
    assert tuple(card.version for card in default_capability_cards()) == ("v1",) * 4
    source = ROOT / "examples/enterprise-quote-pilot/evergreen"
    retained = ROOT / "evidence/enterprise-quote-pilot/latest/pack-sealed"
    assert load_enterprise_quote_pilot_pack(source).pack_digest == load_enterprise_quote_pilot_pack(retained).pack_digest


def test_v2_planner_selects_exact_capability_versions() -> None:
    template = TemplateRegistry().get("template:enterprise_quote@v2")
    task = TaskRequest(id="task:pricing", organization_id="org:test", actor_id="owner:test",
                       purpose="enterprise_quote", deliverable_kind="QUOTE", requested_at="2026-09-16T00:00:00Z",
                       template_ref=template.ref, idempotency_key="pricing-test")
    candidates, _ = TemplateBoundTaskInterpreter().propose(task=task, template=template)
    plan = CoalitionPlanner().plan(task=task, template=template, candidates=candidates,
                                  cards=default_capability_cards(template.ref), revision_lock={"graph": "r1"})
    selected = selected_capability_cards(plan)
    assert selected["product"].version == selected["finance"].version == "v2"
    assert selected["legal"].version == selected["gtm"].version == "v1"
    assert {"quote_basket", "pricing_policy"} <= set(plan.admitted_slot_ids)


def test_priced_pack_loads_structured_sources(tmp_path: Path) -> None:
    draft = priced_draft(tmp_path)
    seal_enterprise_quote_pilot_pack(draft, tmp_path / "priced")
    runtime = load_enterprise_quote_pilot_pack(tmp_path / "priced")
    assert runtime.source_values["quote_basket"].value == BASKET
    assert runtime.source_values["pricing_policy"].value == POLICY
    assert runtime.profile.default_task.template_ref == "template:enterprise_quote@v2"
    assert {"claim:product.quote_basket", "policy:finance.pricing"} <= set(runtime.snapshot_scope_roots)


@pytest.mark.parametrize("missing", ["quote_basket", "pricing_policy"])
def test_priced_pack_rejects_missing_source(tmp_path: Path, missing: str) -> None:
    draft = priced_draft(tmp_path)
    path = draft / "components/knowledge.json"
    value = json.loads(path.read_text())
    value["projection"]["source_values"] = [item for item in value["projection"]["source_values"]
                                            if item["slot_id"] != missing]
    path.write_text(json.dumps(value))
    with pytest.raises(EnterpriseQuotePilotPackError, match=r"ENTERPRISE_BINDING_RESOURCE_MISMATCH|PILOT_STANDARD_SLOT_SET_INVALID"):
        seal_enterprise_quote_pilot_pack(draft, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("slot,value", [
    ("pricing_policy", {**POLICY, "discount_bps": 1.5}),
    ("quote_basket", {**BASKET, "items": [{**BASKET["items"][0], "quantity": True}]}),
    ("quote_basket", {**BASKET, "currency": "GBP"}),
])
def test_priced_pack_rejects_malformed_or_mixed_currency_input(tmp_path: Path, slot: str, value: object) -> None:
    draft = priced_draft(tmp_path)
    path = draft / "components/knowledge.json"
    knowledge = json.loads(path.read_text())
    next(item for item in knowledge["projection"]["source_values"] if item["slot_id"] == slot)["value"] = value
    path.write_text(json.dumps(knowledge))
    with pytest.raises(EnterpriseQuotePilotPackError, match=r"PILOT_KNOWLEDGE_PROJECTION_INVALID|PILOT_PRICING_INPUT_INVALID"):
        seal_enterprise_quote_pilot_pack(draft, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("slot,value", [
    ("pricing_policy", {**POLICY, "discount_bps": 1.5}),
    ("pricing_policy", {**POLICY, "discount_bps": True}),
    ("pricing_policy", {**POLICY, "tax_bps": 10001}),
    ("quote_basket", {**BASKET, "items": [{**BASKET["items"][0], "unit_price": 12.5}]}),
    ("product_plan", POLICY),
    ("pricing_policy", "discount=5%"),
    ("product_plan", "x" * 2001),
    ("pricing_policy", {**POLICY, "unknown": "x" * 65536}),
])
def test_change_input_rejects_unsupported_values(slot: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ChangeProposalInput(event_id="change-1", slot_id=slot, base_version="v1", base_digest="sha256:" + "0" * 64,
                            value=value, source_ref="source:owner@v2")


def test_priced_change_submission_remains_candidate_only_and_rejects_currency_switch(tmp_path: Path) -> None:
    draft = priced_draft(tmp_path)
    seal_enterprise_quote_pilot_pack(draft, tmp_path / "priced")
    service = WorkspaceService(store_path=tmp_path / "workspace.sqlite",
                               runtime_configuration=load_enterprise_quote_pilot_pack(tmp_path / "priced"),
                               review_duration_seconds=0)
    try:
        fields = {item["slot_id"]: item for item in change_options(service)["fields"]}
        assert fields["pricing_policy"]["value_kind"] == "pricing_policy"
        assert fields["quote_basket"]["value_kind"] == "json"
        assert fields["currency"]["allowed_operations"] == []
        assert fields["currency"]["blocked_reason"] == "PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE"
        assert fields["quote_basket"]["allowed_operations"] == ["UPDATE"]
        assert fields["quote_basket"]["blocked_reason"] is None
        current = service.store.get_object("policy:finance.pricing")
        request = ChangeProposalInput(event_id="policy-edit", slot_id="pricing_policy", base_version=current.version,
                                      base_digest=current.digest, value={**POLICY, "discount_bps": 1000},
                                      source_ref="source:owner-reviewed@v2")
        receipt = submit_change(service, request)
        assert receipt["event"]["proposal"]["state"] == "PROPOSED"
        assert service.store.get_object("policy:finance.pricing").digest == current.digest
        assert submit_change(service, request) == receipt
        currency = fields["currency"]["current"]
        bad = ChangeProposalInput(event_id="currency-edit", slot_id="currency", base_version=currency["version"],
                                  base_digest=currency["digest"], value="GBP", source_ref="source:currency@v2")
        before = service.store.verify_event_chain()
        with pytest.raises(IntegrityError, match="CHANGE_EVENT_PRICING_CURRENCY_MISMATCH"):
            submit_change(service, bad)
        assert service.store.verify_event_chain() == before
        basket = fields["quote_basket"]["current"]
        mixed_basket = ChangeProposalInput(
            event_id="basket-currency-edit", slot_id="quote_basket", base_version=basket["version"],
            base_digest=basket["digest"], value={**BASKET, "currency": "GBP"}, source_ref="source:basket@v2")
        with pytest.raises(IntegrityError, match="CHANGE_EVENT_PRICING_CURRENCY_MISMATCH"):
            submit_change(service, mixed_basket)
        assert service.store.verify_event_chain() == before
        same_currency = mixed_basket.model_copy(update={
            "event_id": "basket-quantity-edit",
            "value": {**BASKET, "items": [{**BASKET["items"][0], "quantity": 4}]},
        })
        receipt = submit_change(service, same_currency)
        assert receipt["event"]["proposal"]["state"] == "PROPOSED"
        assert service.store.get_object(receipt["event"]["proposal"]["id"]).digest == basket["digest"]
        service.changes.invalidate("currency", "source:currency-revoked", "PERMISSION_DENIED")
        readmit = next(item for item in change_options(service)["fields"] if item["slot_id"] == "currency")
        assert readmit["allowed_operations"] == ["READMIT"] and readmit["blocked_reason"] is None
        receipt = submit_change(service, bad.model_copy(update={
            "event_id": "currency-readmit", "operation": "READMIT", "value": readmit["current"]["value"],
            "base_version": readmit["current"]["version"], "base_digest": readmit["current"]["digest"],
        }))
        assert receipt["event"]["operation"] == "READMIT"
    finally:
        service.close()
