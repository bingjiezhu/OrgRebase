from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.agentteams_execution_plan import (
    AgentTeamsExecutionPlan,
    AgentTeamsExecutionPlanVerifier,
    AgentTeamsExecutionTask,
    compile_agentteams_execution_plan,
)
from orgrebase.workspace.context_residency import TaskAgentContextEnvelopeBuilder
from orgrebase.workspace.demand_formation import build_task_formation_decision_receipt
from orgrebase.workspace.formation import MEDIA
from orgrebase.workspace.models import (
    ActorContextProjection,
    CoalitionPlan,
    DomainCandidateBundle,
    DomainDelegationTask,
    TaskContextManifest,
)
from orgrebase.workspace.templates import default_capability_cards, enterprise_quote_template

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "evidence/oac-quote-adaptation/latest/artifacts/evergreen/organization-snapshot.json"
DEMAND_PATH = ROOT / "evidence/oac-quote-adaptation/latest/artifacts/evergreen/organizational-demand.json"
AUTHORITY_POLICY_DIGESTS = {
    "oac_source_admission": sha256_digest(
        {
            "policy": "orgrebase-oac-enterprise-adaptation",
            "authority": "role:enterprise-contract-owner",
            "effect_ceiling": "ZERO_EXTERNAL_EFFECTS",
        }
    )
}
SCHEMA_DIGESTS = {
    "schema:workspace.domain-delegation@v1": sha256_digest(
        DomainDelegationTask.model_json_schema(mode="validation")
    ),
    "schema:workspace.domain-candidate-bundle@v1": sha256_digest(
        DomainCandidateBundle.model_json_schema(mode="validation")
    ),
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _replace(model, **updates: Any):
    payload = model.model_dump(mode="json", exclude={"digest"})
    payload.update(updates)
    return type(model).model_validate(payload)


def _inputs(workspace_service) -> dict[str, Any]:
    formation = workspace_service.formation
    task = formation.default_request(workspace_service.profile)
    prepared = formation.prepare_quote(task)
    coalition = CoalitionPlan.model_validate(
        next(item.payload for item in prepared.artifact_writes if item.media_type == MEDIA["coalition"])
    )
    task_context = TaskContextManifest.model_validate(
        next(item.payload for item in prepared.artifact_writes if item.media_type == MEDIA["context"])
    )
    projections = tuple(
        ActorContextProjection.model_validate(item.payload)
        for item in prepared.artifact_writes
        if item.media_type == MEDIA["projection"] and item.payload.get("task_context_ref") == task_context.ref
    )
    demand = _load(DEMAND_PATH)
    demand["spec"]["triggerRefs"][0]["resourceId"] = task.id
    demand["spec"]["triggerRefs"][0]["digest"] = sha256_digest(
        {"task_ref": task.id, "task_digest": task.digest}
    )
    demand["digest"] = sha256_digest({key: value for key, value in demand.items() if key != "digest"})
    cards = default_capability_cards()
    receipt = build_task_formation_decision_receipt(
        organization_snapshot=_load(SNAPSHOT_PATH),
        organizational_demand=demand,
        task=task,
        template=enterprise_quote_template(),
        coalition=coalition,
        capability_cards=cards,
        authority_policy_digests=AUTHORITY_POLICY_DIGESTS,
    )
    envelope = TaskAgentContextEnvelopeBuilder.build(
        task=task,
        coalition=coalition,
        capability_cards=cards,
        task_context=task_context,
        actor_projections=projections,
        admitted_organizational_intent_digest=receipt.organizational_demand_digest,
        task_formation_decision_receipt_digest=receipt.digest,
        now=formation.clock.now(),
    )
    return {
        "formation_receipt": receipt,
        "context_envelope": envelope,
        "coalition": coalition,
        "capability_cards": cards,
        "actor_projections": projections,
        "schema_digests": SCHEMA_DIGESTS,
    }


def _replace_plan_task(
    plan: AgentTeamsExecutionPlan,
    domain_id: str,
    **updates: Any,
) -> AgentTeamsExecutionPlan:
    tasks = tuple(_replace(item, **updates) if item.domain_id == domain_id else item for item in plan.tasks)
    return _replace(plan, tasks=tasks)


def test_compiler_seals_exact_domain_set_and_reviewer_barrier(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    plan = compile_agentteams_execution_plan(**inputs)
    domains = plan.tasks[:-1]
    reviewer = plan.tasks[-1]

    assert plan.selected_domain_ids == ("finance", "gtm", "legal", "product")
    assert tuple(item.domain_id for item in domains) == plan.selected_domain_ids
    assert reviewer.task_kind == "REVIEWER_BARRIER"
    assert reviewer.domain_id == "reviewer"
    assert reviewer.depends_on == tuple(item.task_id for item in domains)
    assert all(item.candidate_only and item.canonical_target_writes == 0 for item in plan.tasks)
    assert all(item.depends_on == () for item in domains)
    assert all(item.capability_card_ref and item.actor_projection_ref for item in domains)
    assert all(item.input_schema_digest and item.output_schema_digest for item in domains)
    assert reviewer.capability_card_ref is None
    assert reviewer.actor_projection_ref is None
    assert plan.candidate_only is True
    assert plan.canonical_target_writes == 0


def test_compiler_is_deterministic_and_independently_recomputed(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    first = compile_agentteams_execution_plan(**inputs)
    second = compile_agentteams_execution_plan(**inputs)

    assert first == second
    assert first.digest == second.digest
    assert AgentTeamsExecutionPlanVerifier.verify(plan=first, **inputs) == first


@pytest.mark.parametrize("mutation", ("add", "remove", "replace"))
def test_verifier_rejects_candidate_controlled_task_set_mutations(
    workspace_service,
    mutation: str,
) -> None:
    inputs = _inputs(workspace_service)
    plan = compile_agentteams_execution_plan(**inputs)
    domain_tasks = list(plan.tasks[:-1])
    reviewer = plan.tasks[-1]

    if mutation == "add":
        added = _replace(
            domain_tasks[-1],
            task_id="at-domain-security-added000001",
            domain_id="security",
        )
        domain_tasks.append(added)
        selected_domains = (*plan.selected_domain_ids, "security")
    elif mutation == "remove":
        domain_tasks = [item for item in domain_tasks if item.domain_id != "legal"]
        selected_domains = tuple(item.domain_id for item in domain_tasks)
    else:
        legal_index = next(index for index, item in enumerate(domain_tasks) if item.domain_id == "legal")
        domain_tasks[legal_index] = _replace(
            domain_tasks[legal_index],
            task_id="at-domain-security-replaced0001",
            domain_id="security",
        )
        domain_tasks.sort(key=lambda item: item.domain_id)
        selected_domains = tuple(item.domain_id for item in domain_tasks)

    forged_reviewer = _replace(
        reviewer,
        depends_on=tuple(item.task_id for item in domain_tasks),
    )
    forged = _replace(
        plan,
        selected_domain_ids=selected_domains,
        tasks=(*domain_tasks, forged_reviewer),
    )

    with pytest.raises(IntegrityError, match="INDEPENDENT_RECOMPUTATION_MISMATCH"):
        AgentTeamsExecutionPlanVerifier.verify(plan=forged, **inputs)


def test_verifier_rejects_domain_capability_substitution(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    plan = compile_agentteams_execution_plan(**inputs)
    product = next(item for item in plan.tasks if item.domain_id == "product")
    forged = _replace_plan_task(
        plan,
        "legal",
        capability_card_ref=product.capability_card_ref,
        capability_card_digest=product.capability_card_digest,
    )

    with pytest.raises(IntegrityError, match="INDEPENDENT_RECOMPUTATION_MISMATCH"):
        AgentTeamsExecutionPlanVerifier.verify(plan=forged, **inputs)


def test_compiler_rejects_cross_lineage_context_and_projection(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    forged_envelope = _replace(
        inputs["context_envelope"],
        task_formation_decision_receipt_digest=sha256_digest({"receipt": "substituted"}),
    )
    with pytest.raises(IntegrityError, match="FORMATION_CONTEXT_LINEAGE_MISMATCH"):
        compile_agentteams_execution_plan(**{**inputs, "context_envelope": forged_envelope})

    legal = next(item for item in inputs["actor_projections"] if item.actor_id == "legal-steward")
    forged_legal = _replace(legal, task_context_ref="task-context:cross-lineage@v1")
    forged_projections = tuple(
        forged_legal if item.ref == legal.ref else item for item in inputs["actor_projections"]
    )
    legal_binding = next(
        item for item in inputs["context_envelope"].domain_bindings if item.domain_id == "legal"
    )
    forged_bindings = tuple(
        (
            legal_binding.model_copy(update={"actor_projection_digest": forged_legal.digest})
            if item.domain_id == "legal"
            else item
        )
        for item in inputs["context_envelope"].domain_bindings
    )
    forged_envelope = _replace(inputs["context_envelope"], domain_bindings=forged_bindings)

    with pytest.raises(IntegrityError, match="CROSS_LINEAGE_ACTOR_PROJECTION:legal"):
        compile_agentteams_execution_plan(
            **{
                **inputs,
                "context_envelope": forged_envelope,
                "actor_projections": forged_projections,
            }
        )


def test_compiler_rejects_missing_or_substituted_schema_digest(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    missing = dict(SCHEMA_DIGESTS)
    missing.pop("schema:workspace.domain-candidate-bundle@v1")

    with pytest.raises(IntegrityError, match="OUTPUT_SCHEMA_BINDING_INVALID:finance"):
        compile_agentteams_execution_plan(**{**inputs, "schema_digests": missing})

    plan = compile_agentteams_execution_plan(**inputs)
    forged = _replace_plan_task(
        plan,
        "finance",
        output_schema_digest=sha256_digest({"schema": "substituted"}),
    )
    with pytest.raises(IntegrityError, match="INDEPENDENT_RECOMPUTATION_MISMATCH"):
        AgentTeamsExecutionPlanVerifier.verify(plan=forged, **inputs)


def test_verifier_rejects_duplicate_task_even_on_unvalidated_instance(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    plan = compile_agentteams_execution_plan(**inputs)
    duplicated = plan.model_copy(update={"tasks": (plan.tasks[0], plan.tasks[0], *plan.tasks[2:])})

    with pytest.raises(IntegrityError, match="PLAN_MODEL_INVALID"):
        AgentTeamsExecutionPlanVerifier.verify(plan=duplicated, **inputs)


def test_execution_plan_schema_matches_runtime_contract() -> None:
    checked_in = json.loads(
        (ROOT / "schemas/workspace-agentteams-execution-plan.schema.json").read_text(encoding="utf-8")
    )
    generated = AgentTeamsExecutionPlan.model_json_schema(mode="validation")
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    generated["$id"] = "https://orgrebase.local/schemas/workspace-agentteams-execution-plan.schema.json"

    assert checked_in == generated
    assert checked_in["additionalProperties"] is False
    assert checked_in["properties"]["candidate_only"]["const"] is True
    assert checked_in["properties"]["canonical_target_writes"]["const"] == 0


def test_task_model_rejects_domain_task_with_review_dependency(workspace_service) -> None:
    inputs = _inputs(workspace_service)
    task = compile_agentteams_execution_plan(**inputs).tasks[0]
    payload = task.model_dump(mode="json", exclude={"digest"})
    payload["depends_on"] = ("at-unrelated-review",)

    with pytest.raises(ValueError, match="domain task must bind"):
        AgentTeamsExecutionTask.model_validate(payload)
