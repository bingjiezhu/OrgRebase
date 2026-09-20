from __future__ import annotations

import pytest

from orgrebase.domain import AuthorizationError, IntegrityError, RunEnvelope
from orgrebase.workspace import templates
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.models import (
    AdmissionDecision,
    ClaimCandidate,
    CoalitionPlan,
    DomainCapabilityCardVersion,
)
from orgrebase.workspace.transport import WorkspaceTransportCompiler


@pytest.fixture
def compilation(workspace_service, monkeypatch):
    compiler = workspace_service.formation.context_compiler
    compile_context = compiler.compile
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return compile_context(**kwargs)

    monkeypatch.setattr(compiler, "compile", capture)
    workspace_service.formation.prepare_quote(WorkspaceFormationService.default_request())
    envelope = RunEnvelope(
        run_id="run:capability-selection",
        nonce="n" * 64,
        issued_at="2026-08-16T00:00:00Z",
        expires_at="2026-08-16T01:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class="LOCAL_DETERMINISTIC",
    )
    return compile_context, captured, envelope


def changed_plan(plan: CoalitionPlan, **changes) -> CoalitionPlan:
    return CoalitionPlan.model_validate({**plan.model_dump(exclude={"digest"}), **changes})


def projection_refs(plan: CoalitionPlan) -> dict[str, str]:
    return {domain: f"actor-context:selection:{domain}@v2" for domain in plan.selected_domain_ids}


def test_selected_card_version_controls_context_and_transport(compilation, monkeypatch):
    compile_context, kwargs, envelope = compilation
    catalog = templates.default_capability_cards()
    original = next(card for card in catalog if card.domain_id == "product")
    replacement = DomainCapabilityCardVersion.model_validate(
        {
            **original.model_dump(exclude={"digest"}),
            "version": "v2",
            "worker_id": "product-specialist",
            "output_schema_refs": ("schema:workspace.domain-candidate-bundle@v2",),
        }
    )
    monkeypatch.setattr(templates, "default_capability_cards", lambda: (*catalog, replacement))
    plan = kwargs["coalition"]
    plan = changed_plan(
        plan,
        selected_card_refs=tuple(
            sorted(replacement.ref if ref == original.ref else ref for ref in plan.selected_card_refs)
        ),
        coverage=tuple(
            item.model_copy(update={"card_ref": replacement.ref}) if item.card_ref == original.ref else item
            for item in plan.coverage
        ),
    )
    with pytest.raises(AuthorizationError, match="TASK_CONTEXT_WORKER_NOT_AUTHORIZED"):
        compile_context(**{**kwargs, "coalition": plan})
    candidates = tuple(
        ClaimCandidate.model_validate(
            {
                **candidate.model_dump(exclude={"digest"}),
                "recipients": tuple(
                    replacement.worker_id if actor == original.worker_id else actor
                    for actor in candidate.recipients
                ),
            }
        )
        for candidate in kwargs["candidates"]
    )
    candidate_refs = {
        before.digest: after.digest for before, after in zip(kwargs["candidates"], candidates, strict=True)
    }
    decisions = tuple(
        AdmissionDecision.model_validate(
            {
                **decision.model_dump(exclude={"digest"}),
                "candidate_ref": candidate_refs[decision.candidate_ref],
            }
        )
        for decision in kwargs["decisions"]
    )
    _, contexts = compile_context(
        **{**kwargs, "coalition": plan, "candidates": candidates, "decisions": decisions}
    )
    product_context = next(item for item in contexts if item.id.endswith(":product"))
    assert product_context.actor_id == "product-specialist"
    assert product_context.included_refs
    delegations = WorkspaceTransportCompiler().compile(
        plan=plan, projection_refs_by_domain=projection_refs(plan), run_envelope=envelope
    )
    WorkspaceTransportCompiler.verify(plan=plan, delegations=delegations, run_envelope=envelope)
    product_task = next(item for item in delegations if item.domain_id == "product")
    assert product_task.worker_id == product_context.actor_id
    assert product_task.allowed_output_schema_refs == replacement.output_schema_refs

    for changes, reason in (
        ({"worker_id": original.worker_id}, "TRANSPORT_WORKER_AUTHORITY_MISMATCH"),
        ({"allowed_output_schema_refs": original.output_schema_refs}, "TRANSPORT_OUTPUT_SCHEMA_EXPANSION"),
    ):
        with pytest.raises(IntegrityError, match=reason):
            WorkspaceTransportCompiler.verify(
                plan=plan,
                delegations=tuple(
                    item.model_copy(update=changes) if item.domain_id == "product" else item
                    for item in delegations
                ),
                run_envelope=envelope,
            )


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "wrong-domain", "wrong-card", "duplicate-slot"])
def test_invalid_selection_is_rejected_by_context_and_transport(compilation, mutation):
    compile_context, kwargs, envelope = compilation
    plan = kwargs["coalition"]
    refs = plan.selected_card_refs
    if mutation == "unknown":
        plan = changed_plan(plan, selected_card_refs=tuple(sorted((*refs[1:], "capability-card:unknown@v1"))))
    elif mutation == "duplicate":
        plan = changed_plan(plan, selected_card_refs=tuple(sorted((*refs, refs[0]))))
    else:
        coverage = list(plan.coverage)
        if mutation == "duplicate-slot":
            coverage.insert(0, coverage[0])
        else:
            other = next(item for item in coverage if item.domain_id != coverage[0].domain_id)
            change = (
                {"domain_id": other.domain_id} if mutation == "wrong-domain" else {"card_ref": other.card_ref}
            )
            coverage[0] = coverage[0].model_copy(update=change)
        plan = changed_plan(plan, coverage=tuple(coverage))

    with pytest.raises(IntegrityError, match="CAPABILITY_"):
        compile_context(**{**kwargs, "coalition": plan})
    with pytest.raises(IntegrityError, match="CAPABILITY_"):
        WorkspaceTransportCompiler().compile(
            plan=plan, projection_refs_by_domain=projection_refs(plan), run_envelope=envelope
        )
    with pytest.raises(IntegrityError, match="CAPABILITY_"):
        WorkspaceTransportCompiler.verify(plan=plan, delegations=(), run_envelope=envelope)


def test_two_selected_versions_cannot_silently_choose_one_domain(compilation, monkeypatch):
    compile_context, kwargs, envelope = compilation
    catalog = templates.default_capability_cards()
    original = catalog[0]
    replacement = original.model_copy(update={"version": "v2", "worker_id": "other-worker"})
    monkeypatch.setattr(templates, "default_capability_cards", lambda: (*catalog, replacement))
    plan = kwargs["coalition"]
    plan = changed_plan(plan, selected_card_refs=tuple(sorted((*plan.selected_card_refs, replacement.ref))))
    with pytest.raises(IntegrityError, match="CAPABILITY_DOMAIN_DUPLICATE"):
        compile_context(**{**kwargs, "coalition": plan})
    with pytest.raises(IntegrityError, match="CAPABILITY_DOMAIN_DUPLICATE"):
        WorkspaceTransportCompiler().compile(
            plan=plan, projection_refs_by_domain=projection_refs(plan), run_envelope=envelope
        )
