from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.context_residency import TaskAgentContextEnvelopeBuilder
from orgrebase.workspace.demand_formation import (
    TaskFormationDecisionReceipt,
    TaskFormationDecisionReceiptVerifier,
    build_task_formation_decision_receipt,
    demand_formation_policy_digest,
)
from orgrebase.workspace.formation import MEDIA
from orgrebase.workspace.models import (
    ActorContextProjection,
    CoalitionPlan,
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


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _replace(model, **updates: Any):
    payload = model.model_dump(mode="json", exclude={"digest"})
    payload.update(updates)
    return type(model).model_validate(payload)


def _inputs(workspace_service):
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
    return (
        {
            "organization_snapshot": _load(SNAPSHOT_PATH),
            "organizational_demand": demand,
            "task": task,
            "template": enterprise_quote_template(),
            "coalition": coalition,
            "capability_cards": default_capability_cards(),
            "authority_policy_digests": AUTHORITY_POLICY_DIGESTS,
        },
        task_context,
        projections,
    )


def test_compiler_emits_complete_causal_edges_and_control_review(workspace_service) -> None:
    inputs, _task_context, _projections = _inputs(workspace_service)
    receipt = build_task_formation_decision_receipt(**inputs)
    bindings = {item.evidence_obligation_resource_id: item for item in receipt.obligation_bindings}

    assert receipt.objective == ("produce one governed enterprise quote with zero external effects")
    assert receipt.effect_ceiling == "ZERO_EXTERNAL_EFFECTS"
    assert receipt.candidate_only is True
    assert receipt.canonical_target_writes == 0
    assert receipt.policy_digests == {
        "demand_formation": demand_formation_policy_digest(),
        **AUTHORITY_POLICY_DIGESTS,
    }
    assert receipt.selected_domain_ids == ("finance", "gtm", "legal", "product")
    assert receipt.unknown_obligation_resource_ids == ()
    assert bindings["obligation:quote:product"].requirement_refs == (
        "data_residency",
        "launch_date",
        "product_plan",
    )
    assert bindings["obligation:quote:finance"].requirement_refs == (
        "currency",
        "price_band",
    )
    independent = bindings["obligation:quote:independent-review"]
    assert independent.status == "MAPPED_CONTROL"
    assert independent.requirement_refs == ("control-requirement:independent-review",)
    assert independent.domain_ids == ("reviewer",)
    assert independent.capability_card_refs == ()


def test_unknown_obligation_is_preserved_without_inferred_authority(workspace_service) -> None:
    inputs, _task_context, _projections = _inputs(workspace_service)
    demand = copy.deepcopy(inputs["organizational_demand"])
    demand.pop("digest")
    unknown_ref = {
        "apiVersion": "oac.dev/v0alpha1",
        "kind": "EvidenceObligation",
        "namespace": "oac.orgrebase.evergreen-industries",
        "resourceId": "obligation:quote:security",
        "revision": 1,
        "digest": sha256_digest({"obligation": "security", "status": "unknown"}),
    }
    demand["spec"]["evidenceObligationRefs"].append(unknown_ref)
    demand["digest"] = sha256_digest(demand)
    inputs["organizational_demand"] = demand

    receipt = build_task_formation_decision_receipt(**inputs)
    security = next(
        item
        for item in receipt.obligation_bindings
        if item.evidence_obligation_resource_id == "obligation:quote:security"
    )

    assert receipt.unknown_obligation_resource_ids == ("obligation:quote:security",)
    assert security.status == "UNKNOWN"
    assert security.requirement_refs == ()
    assert security.domain_ids == ()
    assert security.capability_card_refs == ()
    assert security.reason_codes == ("NO_DETERMINISTIC_OBLIGATION_RULE",)


def test_independent_verifier_rejects_candidate_decision_substitution(
    workspace_service,
) -> None:
    inputs, _task_context, _projections = _inputs(workspace_service)
    receipt = build_task_formation_decision_receipt(**inputs)
    forged_bindings = []
    for item in receipt.obligation_bindings:
        if item.evidence_obligation_resource_id == "obligation:quote:legal":
            forged_bindings.append(
                _replace(
                    item,
                    requirement_refs=("data_residency",),
                    reason_codes=("EXACT_DOMAIN_SUFFIX_AND_COVERAGE_MATCH",),
                )
            )
        else:
            forged_bindings.append(item)
    forged = _replace(
        receipt,
        obligation_bindings=tuple(forged_bindings),
        requirement_obligation_set_digest=sha256_digest([item.digest for item in forged_bindings]),
    )

    with pytest.raises(IntegrityError, match="INDEPENDENT_RECOMPUTATION_MISMATCH"):
        TaskFormationDecisionReceiptVerifier.verify(receipt=forged, **inputs)


def test_compiler_rejects_snapshot_and_effect_ceiling_substitution(workspace_service) -> None:
    inputs, _task_context, _projections = _inputs(workspace_service)
    demand = copy.deepcopy(inputs["organizational_demand"])
    demand.pop("digest")
    demand["spec"]["effectCeiling"] = "write_external_system"
    demand["digest"] = sha256_digest(demand)
    inputs["organizational_demand"] = demand

    with pytest.raises(IntegrityError, match="EFFECT_CEILING_EXPANSION"):
        build_task_formation_decision_receipt(**inputs)


def test_context_envelope_transitively_binds_formation_decision(workspace_service) -> None:
    inputs, task_context, projections = _inputs(workspace_service)
    receipt = build_task_formation_decision_receipt(**inputs)
    envelope = TaskAgentContextEnvelopeBuilder.build(
        task=inputs["task"],
        coalition=inputs["coalition"],
        capability_cards=inputs["capability_cards"],
        task_context=task_context,
        actor_projections=projections,
        admitted_organizational_intent_digest=receipt.organizational_demand_digest,
        task_formation_decision_receipt_digest=receipt.digest,
        now=workspace_service.formation.clock.now(),
    )

    assert envelope.task_formation_decision_receipt_digest == receipt.digest
    assert envelope.candidate_only is True
    assert envelope.canonical_target_writes == 0


def test_receipt_schema_matches_runtime_contract() -> None:
    checked_in = json.loads(
        (ROOT / "schemas/workspace-task-formation-decision-receipt.schema.json").read_text(encoding="utf-8")
    )
    generated = TaskFormationDecisionReceipt.model_json_schema(mode="validation")
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    generated["$id"] = "https://orgrebase.local/schemas/workspace-task-formation-decision-receipt.schema.json"

    assert checked_in == generated
    assert checked_in["additionalProperties"] is False
    assert checked_in["properties"]["candidate_only"]["const"] is True
    assert checked_in["properties"]["canonical_target_writes"]["const"] == 0
