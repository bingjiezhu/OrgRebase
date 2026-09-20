"""Planner boundary regressions; typed inputs exercise the reusable public API."""

from __future__ import annotations

from itertools import combinations
from random import Random

import pytest

from orgrebase.domain import ObjectState
from orgrebase.workspace.models import HealthStatus, TaskRequest
from orgrebase.workspace.planner import CoalitionPlanner
from orgrebase.workspace.task_agent import TemplateBoundTaskInterpreter
from orgrebase.workspace.templates import TemplateRegistry, default_capability_cards


def _replace(model, **updates):
    payload = model.model_dump(mode="json", exclude={"digest"})
    payload.update(updates)
    return type(model).model_validate(payload)


def _problem(template_ref="template:public_launch_summary@v1"):
    template = TemplateRegistry().get(template_ref)
    task = TaskRequest(
        id="task:provider-binding",
        organization_id="org:test",
        actor_id="employee:test",
        purpose=template.id.split(":", 1)[1],
        deliverable_kind=template.deliverable_kind,
        requested_at="2026-09-08T00:00:00Z",
        template_ref=template.ref,
        idempotency_key="provider-binding:never-persisted",
    )
    candidates, _ = TemplateBoundTaskInterpreter().propose(task=task, template=template)
    return {
        "task": task,
        "template": template,
        "candidates": candidates,
        "cards": default_capability_cards(template_ref),
        "revision_lock": {"graph": "r1"},
    }


def _card(problem, domain):
    return next(card for card in problem["cards"] if card.domain_id == domain)


def test_cross_domain_slot_claims_cannot_supply_another_domain() -> None:
    problem = _problem()
    rogue = _replace(
        _card(problem, "gtm"),
        id="card:rogue",
        declared_cost=0,
        supported_slot_ids=tuple(slot.slot_id for slot in problem["template"].slots),
    )
    problem["cards"] = (rogue,)
    with pytest.raises(RuntimeError, match=r"^NO_PROVIDER:product$"):
        CoalitionPlanner().plan(**problem)


def test_partial_cards_in_one_domain_are_not_silently_overwritten() -> None:
    problem = _problem()
    product = _card(problem, "product")
    problem["cards"] = (
        _replace(product, id="card:product-a", supported_slot_ids=("product_plan",)),
        _replace(product, id="card:product-b", supported_slot_ids=("launch_date",)),
        _card(problem, "gtm"),
    )
    with pytest.raises(RuntimeError, match=r"^AMBIGUOUS_PROVIDER:product$"):
        CoalitionPlanner().plan(**problem)


def test_expired_card_cannot_be_selected() -> None:
    problem = _problem()
    problem["cards"] = (
        _replace(
            _card(problem, "product"),
            valid_from="2019-01-01T00:00:00Z",
            valid_to="2020-01-01T00:00:00Z",
        ),
        _card(problem, "gtm"),
    )
    with pytest.raises(RuntimeError, match=r"^NO_PROVIDER:product$"):
        CoalitionPlanner().plan(**problem)


def test_missing_required_slots_cannot_produce_partial_success() -> None:
    problem = _problem()
    problem["candidates"] = problem["candidates"][:1]
    with pytest.raises(ValueError, match=r"^MISSING_REQUIRED_SLOTS:launch_date,public_message$"):
        CoalitionPlanner().plan(**problem)


@pytest.mark.parametrize("template", TemplateRegistry().templates, ids=lambda item: item.id)
def test_default_templates_keep_the_historical_minimum_team_and_cost(template) -> None:
    problem = _problem(template.ref)
    plan = CoalitionPlanner().plan(**problem)
    required = {candidate.slot_id for candidate in problem["candidates"]}
    # Small exhaustive oracle over the historically legal, unique-domain catalog.
    legal = []
    for size in range(1, len(problem["cards"]) + 1):
        for subset in combinations(problem["cards"], size):
            if required <= set().union(*(set(card.supported_slot_ids) for card in subset)):
                key = (
                    size,
                    sum(card.declared_cost for card in subset),
                    tuple(sorted(card.domain_id for card in subset)),
                )
                legal.append((key, tuple(sorted(card.ref for card in subset))))
    expected_key, expected_refs = min(legal)
    assert plan.tie_break_tuple == expected_key
    assert plan.total_declared_cost == expected_key[1]
    assert plan.selected_card_refs == expected_refs
    by_ref = {card.ref: card for card in problem["cards"]}
    slot_domains = {slot.slot_id: slot.domain_id for slot in template.slots}
    for binding in plan.coverage:
        card = by_ref[binding.card_ref]
        assert binding.slot_id in card.supported_slot_ids
        assert card.domain_id == binding.domain_id == slot_domains[binding.slot_id]


def test_card_and_candidate_order_do_not_change_plan_or_mutate_inputs() -> None:
    problem = _problem()
    original = {key: repr(value) for key, value in problem.items()}
    expected = CoalitionPlanner().plan(**problem)
    rng = Random(17)
    for _ in range(20):
        cards = list(problem["cards"])
        candidates = list(problem["candidates"])
        rng.shuffle(cards)
        rng.shuffle(candidates)
        assert (
            CoalitionPlanner().plan(**{**problem, "cards": tuple(cards), "candidates": tuple(candidates)})
            == expected
        )
    assert {key: repr(value) for key, value in problem.items()} == original


@pytest.mark.parametrize("declared_cost", [0, 2, 100])
def test_multiple_available_providers_are_ambiguous_even_with_different_costs(declared_cost) -> None:
    problem = _problem()
    problem["cards"] += (
        _replace(_card(problem, "product"), id="card:product-alternative", declared_cost=declared_cost),
    )
    with pytest.raises(RuntimeError, match=r"^AMBIGUOUS_PROVIDER:product$"):
        CoalitionPlanner().plan(**problem)


@pytest.mark.parametrize(
    ("updates", "selected"),
    [
        ({"valid_from": "2026-09-08T00:00:00Z"}, True),
        ({"valid_from": "2026-09-08T08:00:00+08:00"}, True),
        ({"valid_from": "2026-09-08T00:00:00.000001Z"}, False),
        ({"valid_to": "2026-09-08T00:00:00Z"}, False),
        ({"valid_to": "2026-09-08T08:00:00+08:00"}, False),
        ({"valid_to": "2026-09-08T00:00:00.000001Z"}, True),
        ({"health_status": HealthStatus.DEGRADED}, False),
        ({"health_status": HealthStatus.INACTIVE}, False),
        ({"allowed_purposes": ("enterprise_quote",)}, False),
        ({"accepted_input_schema_refs": ("schema:unrelated@v1",)}, False),
        ({"output_schema_refs": ("schema:unrelated@v1",)}, False),
        ({"supported_slot_ids": ("product_plan",)}, False),
    ],
)
def test_provider_eligibility_checks_time_health_purpose_schema_and_full_coverage(updates, selected):
    problem = _problem()
    product = _replace(_card(problem, "product"), **updates)
    problem["cards"] = (product, _card(problem, "gtm"))
    if selected:
        assert product.ref in CoalitionPlanner().plan(**problem).selected_card_refs
    else:
        with pytest.raises(RuntimeError, match=r"^NO_PROVIDER:product$"):
            CoalitionPlanner().plan(**problem)


@pytest.mark.parametrize(
    "updates",
    [
        {"valid_from": "not-a-time"},
        {"valid_from": "2026-09-08T00:00:00"},
        {"valid_to": "not-a-time"},
        {"valid_from": "2026-09-08T00:00:00Z", "valid_to": "2026-09-08T00:00:00Z"},
        {"valid_from": "2026-09-08T00:00:00Z", "valid_to": "2026-09-07T00:00:00Z"},
    ],
)
def test_invalid_card_time_contract_fails_closed(updates) -> None:
    problem = _problem()
    problem["cards"] = (_replace(_card(problem, "product"), **updates), _card(problem, "gtm"))
    with pytest.raises(ValueError, match=r"^INVALID_PROVIDER_VALIDITY:capability-card:product@v1$"):
        CoalitionPlanner().plan(**problem)


@pytest.mark.parametrize("requested_at", ["not-a-time", "2026-09-08T00:00:00"])
def test_planning_time_must_be_an_explicit_timezone_aware_timestamp(requested_at) -> None:
    problem = _problem()
    problem["task"] = _replace(problem["task"], requested_at=requested_at)
    with pytest.raises(ValueError, match=r"^INVALID_TASK_REQUESTED_AT$"):
        CoalitionPlanner().plan(**problem)


def test_unavailable_alternative_does_not_create_ambiguity() -> None:
    problem = _problem()
    expected_refs = CoalitionPlanner().plan(**problem).selected_card_refs
    problem["cards"] += (
        _replace(_card(problem, "product"), id="card:expired", valid_to="2026-09-07T00:00:00Z"),
    )
    assert CoalitionPlanner().plan(**problem).selected_card_refs == expected_refs


def test_duplicate_card_refs_are_rejected_before_binding() -> None:
    problem = _problem()
    problem["cards"] += (_card(problem, "product"),)
    with pytest.raises(ValueError, match=r"^CAPABILITY_CATALOG_DUPLICATE_REF$"):
        CoalitionPlanner().plan(**problem)


@pytest.mark.parametrize(
    ("part", "updates", "code"),
    [
        ("task", {"template_ref": "template:other@v1"}, "TASK_TEMPLATE_REF_MISMATCH"),
        ("task", {"deliverable_kind": "QUOTE"}, "TASK_TEMPLATE_DELIVERABLE_MISMATCH"),
        ("template", {"state": ObjectState.SUPERSEDED}, "TASK_TEMPLATE_NOT_ACTIVE"),
    ],
)
def test_task_template_contract_is_validated_at_the_planning_boundary(part, updates, code) -> None:
    problem = _problem()
    problem[part] = _replace(problem[part], **updates)
    with pytest.raises(ValueError, match=rf"^{code}$"):
        CoalitionPlanner().plan(**problem)


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"task_ref": "task:unrelated"}, "REQUIREMENT_TASK_MISMATCH:product_plan"),
        ({"slot_id": "nonexistent"}, "EXTRA_REQUIREMENT_NOT_ALLOWED:nonexistent"),
        ({"proposed_domain_id": "gtm"}, "REQUIREMENT_DOMAIN_MISMATCH:product_plan"),
    ],
)
def test_candidate_contract_is_validated_at_the_planning_boundary(updates, code) -> None:
    problem = _problem()
    problem["candidates"] = (_replace(problem["candidates"][0], **updates), *problem["candidates"][1:])
    with pytest.raises(ValueError, match=rf"^{code}$"):
        CoalitionPlanner().plan(**problem)


def test_slot_purpose_restriction_is_enforced() -> None:
    problem = _problem()
    problem["template"] = _replace(
        problem["template"],
        slots=(
            _replace(problem["template"].slots[0], allowed_purposes=("enterprise_quote",)),
            *problem["template"].slots[1:],
        ),
    )
    with pytest.raises(ValueError, match=r"^REQUIREMENT_PURPOSE_MISMATCH:product_plan$"):
        CoalitionPlanner().plan(**problem)


def test_duplicate_requirement_candidates_are_rejected() -> None:
    problem = _problem()
    problem["candidates"] += (problem["candidates"][0],)
    with pytest.raises(ValueError, match=r"^DUPLICATE_REQUIREMENT_SLOT:product_plan$"):
        CoalitionPlanner().plan(**problem)


def test_large_unrelated_catalog_does_not_expand_the_team() -> None:
    problem = _problem()
    expected = CoalitionPlanner().plan(**problem)
    product = _card(problem, "product")
    problem["cards"] += tuple(
        _replace(
            product,
            id=f"card:unrelated-{index}",
            domain_id=f"unrelated-{index}",
            supported_slot_ids=("unrelated-slot",),
        )
        for index in range(2000)
    )
    actual = CoalitionPlanner().plan(**problem)
    assert actual.coverage == expected.coverage
    assert actual.tie_break_tuple == expected.tie_break_tuple
    assert actual.input_digest != expected.input_digest


def test_new_plans_identify_the_binding_policy_version() -> None:
    assert CoalitionPlanner().plan(**_problem()).planner_version == (
        "workspace-coalition-domain-binding@2.0.0"
    )


def _receipt_inputs(workspace_service):
    import json
    from pathlib import Path

    from orgrebase.digest import sha256_digest

    formation = workspace_service.formation
    task = formation.default_request(workspace_service.profile)
    template, _, _, _, coalition = formation.compile_quote_contracts(task)
    artifacts = (
        Path(__file__).resolve().parents[2] / "evidence/oac-quote-adaptation/latest/artifacts/evergreen"
    )
    snapshot = json.loads((artifacts / "organization-snapshot.json").read_text())
    demand = json.loads((artifacts / "organizational-demand.json").read_text())
    demand["spec"]["triggerRefs"][0]["resourceId"] = task.id
    demand["spec"]["triggerRefs"][0]["digest"] = task.digest
    demand["digest"] = sha256_digest({key: value for key, value in demand.items() if key != "digest"})
    return {
        "organization_snapshot": snapshot,
        "organizational_demand": demand,
        "task": task,
        "template": template,
        "coalition": coalition,
        "capability_cards": default_capability_cards(),
        "authority_policy_digests": {"source_admission": sha256_digest("test:source-admission")},
    }


@pytest.mark.parametrize("version", sorted(CoalitionPlanner.supported_plan_versions))
def test_receipt_verification_accepts_a_safe_plan_under_a_known_policy_version(
    workspace_service, version
) -> None:
    from orgrebase.workspace.demand_formation import (
        TaskFormationDecisionReceiptVerifier,
        build_task_formation_decision_receipt,
    )

    inputs = _receipt_inputs(workspace_service)
    inputs["coalition"] = _replace(inputs["coalition"], planner_version=version)
    receipt = build_task_formation_decision_receipt(**inputs)
    assert TaskFormationDecisionReceiptVerifier.verify(receipt=receipt, **inputs) == receipt
    assert receipt.coalition_plan_digest == inputs["coalition"].digest


def test_legacy_version_does_not_bypass_current_provider_validation(workspace_service) -> None:
    from orgrebase.workspace.demand_formation import build_task_formation_decision_receipt

    inputs = _receipt_inputs(workspace_service)
    inputs["coalition"] = _replace(
        inputs["coalition"], planner_version="workspace-coalition-exhaustive@1.0.0"
    )
    inputs["capability_cards"] = tuple(
        _replace(card, valid_from="2019-01-01T00:00:00Z", valid_to="2020-01-01T00:00:00Z")
        if card.domain_id == "product"
        else card
        for card in inputs["capability_cards"]
    )
    with pytest.raises(RuntimeError, match=r"^NO_PROVIDER:product$"):
        build_task_formation_decision_receipt(**inputs)


def test_legacy_version_does_not_bypass_binding_recomputation(workspace_service) -> None:
    from orgrebase.domain import IntegrityError
    from orgrebase.workspace.demand_formation import build_task_formation_decision_receipt

    inputs = _receipt_inputs(workspace_service)
    plan = inputs["coalition"]
    coverage = [item.model_dump() for item in plan.coverage]
    next(item for item in coverage if item["domain_id"] == "product")["card_ref"] = "capability-card:gtm@v1"
    inputs["coalition"] = _replace(
        plan, planner_version="workspace-coalition-exhaustive@1.0.0", coverage=coverage
    )
    with pytest.raises(IntegrityError, match="COALITION_NOT_MINIMUM_RECOMPUTED_COVER"):
        build_task_formation_decision_receipt(**inputs)


def test_unknown_planner_version_cannot_be_read_as_a_supported_plan(workspace_service) -> None:
    from orgrebase.domain import IntegrityError
    from orgrebase.workspace.demand_formation import build_task_formation_decision_receipt

    inputs = _receipt_inputs(workspace_service)
    inputs["coalition"] = _replace(inputs["coalition"], planner_version="planner:unrecognized@1")
    with pytest.raises(IntegrityError, match="COALITION_NOT_MINIMUM_RECOMPUTED_COVER"):
        build_task_formation_decision_receipt(**inputs)


def test_no_candidates_are_explicitly_rejected() -> None:
    problem = _problem()
    problem["candidates"] = ()
    with pytest.raises(ValueError, match=r"^NO_ADMITTED_REQUIREMENT_SLOTS$"):
        CoalitionPlanner().plan(**problem)


def test_optional_slots_are_bound_only_when_admitted() -> None:
    problem = _problem()
    problem["template"] = _replace(
        problem["template"],
        slots=tuple(
            _replace(slot, required=False) if slot.slot_id == "public_message" else slot
            for slot in problem["template"].slots
        ),
    )
    problem["candidates"] = tuple(
        candidate for candidate in problem["candidates"] if candidate.slot_id != "public_message"
    )
    plan = CoalitionPlanner().plan(**problem)
    assert plan.selected_domain_ids == ("product",)
    assert plan.admitted_slot_ids == ("launch_date", "product_plan")
