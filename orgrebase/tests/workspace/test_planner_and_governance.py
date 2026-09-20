from __future__ import annotations

import json

import pytest

from orgrebase.domain import AuthorizationError
from orgrebase.workspace.models import TaskRequest
from orgrebase.workspace.planner import CoalitionPlanner
from orgrebase.workspace.task_agent import TemplateBoundTaskInterpreter
from orgrebase.workspace.templates import TemplateRegistry, default_capability_cards


def _request(*, template_ref: str, kind: str, purpose: str, task_id: str) -> TaskRequest:
    return TaskRequest(
        id=task_id,
        organization_id="org:northstar",
        actor_id="employee:test",
        purpose=purpose,
        deliverable_kind=kind,
        requested_at="2026-08-15T00:00:00Z",
        template_ref=template_ref,
        input_values={},
        customer_id="customer:acme",
        idempotency_key=f"{task_id}@1",
    )


def _plan(task: TaskRequest):
    registry = TemplateRegistry()
    template = registry.get(task.template_ref)
    candidates, _ = TemplateBoundTaskInterpreter().propose(task=task, template=template)
    return CoalitionPlanner().plan(
        task=task,
        template=template,
        candidates=candidates,
        cards=default_capability_cards(),
        revision_lock={"graph": "r1"},
    )


def test_enterprise_quote_selects_exact_four_domain_coalition() -> None:
    plan = _plan(
        _request(
            template_ref="template:enterprise_quote@v1",
            kind="QUOTE",
            purpose="enterprise_quote",
            task_id="task:quote",
        )
    )
    assert plan.selected_card_refs == (
        "capability-card:finance@v1",
        "capability-card:gtm@v1",
        "capability-card:legal@v1",
        "capability-card:product@v1",
    )
    assert plan.tie_break_tuple == (4, 10, ("finance", "gtm", "legal", "product"))


def test_public_summary_does_not_select_legal_or_finance() -> None:
    plan = _plan(
        _request(
            template_ref="template:public_launch_summary@v1",
            kind="PUBLIC_SUMMARY",
            purpose="public_launch_summary",
            task_id="task:summary",
        )
    )
    assert plan.selected_card_refs == (
        "capability-card:gtm@v1",
        "capability-card:product@v1",
    )


def test_template_interpreter_rejects_unknown_optional_slot() -> None:
    registry = TemplateRegistry()
    template = registry.get("template:enterprise_quote@v1")
    task = _request(
        template_ref=template.ref,
        kind="QUOTE",
        purpose="enterprise_quote",
        task_id="task:bad-slot",
    )
    payload = task.model_dump(mode="json", exclude={"digest"})
    payload["input_values"] = {"optional_slots": ["export_all_customers"]}
    task = TaskRequest.model_validate(payload)
    with pytest.raises(ValueError, match="EXTRA_REQUIREMENT_NOT_ALLOWED"):
        TemplateBoundTaskInterpreter().propose(task=task, template=template)


def test_quote_context_never_contains_raw_contract(formed_service) -> None:
    receipt = formed_service.store.load_artifact(
        "task-context:quote_acme@v1",
        "application/vnd.orgrebase.task-context+json",
    )
    rendered = json.dumps(receipt.payload, ensure_ascii=False)
    assert "SYNTHETIC RESTRICTED SOURCE" not in rendered
    assert "raw_contract_text" not in rendered
    quote = formed_service.current_quote()
    assert "raw_contract_text" not in quote.payload
    assert "internal_cost_floor" not in quote.payload


def test_unsupported_premise_is_rejected(formed_service) -> None:
    from orgrebase.workspace.context import TaskContextCompiler
    from orgrebase.workspace.models import TaskContextManifest

    manifest = TaskContextManifest.model_validate(
        formed_service.store.load_artifact("task-context:quote_acme@v1").payload
    )
    with pytest.raises(AuthorizationError, match="UNSUPPORTED_PREMISE"):
        TaskContextCompiler.validate_used_premises(
            manifest, ("claim:product.launch_date@v7", "source:legal.customer-contract@v4")
        )
