"""Build frozen, deterministic OAC TCK plan witnesses and mutations.

The generator reads only repository fixtures. It never downloads EDiTh PDFs, calls a
model, or treats project-authored constraints as human Ground Truth.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import rfc8785

from oac.canonical import parse_resource, resource_ref, seal_resource
from oac.compiler import compile_supplier_change
from oac.models import (
    CONTEXTUAL_PREDICATE_VERSION,
    OrganizationPlan,
    OrganizationSnapshot,
    OrgChangeCase,
    SemanticChangeSet,
)
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "supplier-change"
INPUTS = PROFILE / "inputs"
WITNESSES = PROFILE / "witnesses"
ANNOTATIONS = PROFILE / "annotations"
CASES = PROFILE / "cases"
FIXTURES = ROOT / "tck" / "fixtures"


def _load(path: Path, expected_type: type[Any]) -> Any:
    resource = parse_resource(path.read_bytes(), verify_digest=True)
    if not isinstance(resource, expected_type):
        raise TypeError(f"{path} is not {expected_type.__name__}")
    return resource


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _contextual_predicate(
    *,
    after_state: str,
    after_values: tuple[str, ...] = (),
    subject_refs: tuple[str, ...] = (),
    node_types: tuple[str, ...] = (),
    domain_refs: tuple[str, ...] = (),
    scope_refs: tuple[str, ...] = (),
    scope_mode: str = "all",
    missing_behavior: str = "false",
    relation_types: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build only the closed v0.2 predicate grammar used by frozen fixtures."""

    predicate: dict[str, Any] = {
        "predicateVersion": CONTEXTUAL_PREDICATE_VERSION,
        "semanticType": "supplier.status",
        "afterState": after_state,
    }
    if after_values:
        predicate["afterValues"] = list(after_values)
    subject_selector = {
        key: list(values)
        for key, values in (
            ("subjectRefs", subject_refs),
            ("nodeTypes", node_types),
            ("domainRefs", domain_refs),
        )
        if values
    }
    if subject_selector:
        predicate["subjectSelector"] = subject_selector
    if scope_refs:
        predicate["scopeSelector"] = {
            "refs": list(scope_refs),
            "matchMode": scope_mode,
            "missingBehavior": missing_behavior,
        }
    if relation_types:
        predicate["relationTypes"] = list(relation_types)
    return predicate


def _build_contextual_snapshots() -> tuple[OrganizationSnapshot, OrganizationSnapshot]:
    """Create immutable v0.2 successors without rewriting legacy source bytes."""

    legacy_full = _load(INPUTS / "veracier-proc01.snapshot.json", OrganizationSnapshot)
    full = legacy_full.model_dump(mode="json", by_alias=True)
    full["metadata"].update(
        {
            "id": "snapshot:veracier-proc01-contextual",
            "revision": 2,
            "sourceRefs": [
                *full["metadata"]["sourceRefs"],
                "oac:contextual-applicability-v0.2-fixture",
            ],
        }
    )
    for node in full["spec"]["nodes"]:
        if node["nodeId"] == "control:nuclear-substitution":
            node["admissionStatus"] = "candidate"

    supplier_subject = {
        "subject_refs": ("supplier:forges-martelliere",),
        "node_types": ("supplier",),
        "domain_refs": ("domain:procurement",),
    }
    alternative_subject = {
        "subject_refs": ("alternative:acieries-savoie",),
        "node_types": ("supplier-alternative",),
        "domain_refs": ("domain:quality",),
    }
    full["spec"]["dependencyEdges"] = [
        {
            "edgeId": "edge:contextual-alternative-aero",
            "sourceRef": "alternative:acieries-savoie",
            "targetRef": "production:aero",
            "relationType": "operational_dependency",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("qualified",),
                **alternative_subject,
                scope_refs=("alternative:acieries-savoie", "production:aero"),
                relation_types=("operational_dependency",),
            ),
            "admissionStatus": "admitted",
        },
        {
            "edgeId": "edge:contextual-alternative-energy",
            "sourceRef": "alternative:acieries-savoie",
            "targetRef": "production:energy",
            "relationType": "operational_dependency",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("qualified",),
                **alternative_subject,
                scope_refs=("alternative:acieries-savoie", "production:energy"),
                relation_types=("operational_dependency",),
            ),
            "admissionStatus": "admitted",
        },
        {
            "edgeId": "edge:contextual-supplier-nuclear-order",
            "sourceRef": "supplier:forges-martelliere",
            "targetRef": "order:energy-nuclear",
            "relationType": "business_dependency",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("judicial_restructuring",),
                **supplier_subject,
                scope_refs=("supplier:forges-martelliere", "order:energy-nuclear"),
                relation_types=("business_dependency",),
            ),
            "admissionStatus": "admitted",
        },
        {
            "edgeId": "edge:contextual-nuclear-production",
            "sourceRef": "order:energy-nuclear",
            "targetRef": "production:energy",
            "relationType": "operational_dependency",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("judicial_restructuring",),
                **supplier_subject,
                scope_refs=("order:energy-nuclear", "production:energy"),
                relation_types=("operational_dependency",),
            ),
            "admissionStatus": "admitted",
        },
        {
            "edgeId": "edge:contextual-production-compliance",
            "sourceRef": "production:energy",
            "targetRef": "control:nuclear-substitution",
            "relationType": "compliance_dependency",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("judicial_restructuring",),
                **supplier_subject,
                scope_refs=("control:nuclear-substitution",),
                missing_behavior="false",
                relation_types=("compliance_dependency",),
            ),
            "admissionStatus": "admitted",
        },
        {
            "edgeId": "edge:contextual-supplier-contract",
            "sourceRef": "supplier:forges-martelliere",
            "targetRef": "contract:forges-msa",
            "relationType": "contractual_dependency",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("judicial_restructuring",),
                **supplier_subject,
                scope_refs=("contract:forges-msa",),
                missing_behavior="false",
                relation_types=("contractual_dependency",),
            ),
            "admissionStatus": "admitted",
        },
        {
            "edgeId": "edge:contextual-supplier-rush-order",
            "sourceRef": "supplier:forges-martelliere",
            "targetRef": "order:aero-rush",
            "relationType": "financial_exposure",
            "transferPredicate": _contextual_predicate(
                after_state="known",
                after_values=("judicial_restructuring",),
                **supplier_subject,
                scope_refs=("order:aero-rush",),
                missing_behavior="false",
                relation_types=("financial_exposure",),
            ),
            "admissionStatus": "admitted",
        },
    ]

    qualified_quality = _contextual_predicate(
        after_state="known",
        after_values=("qualified",),
        **alternative_subject,
        scope_refs=("alternative:acieries-savoie",),
    )
    qualified_continuity = _contextual_predicate(
        after_state="known",
        after_values=("qualified",),
        **alternative_subject,
        scope_refs=("production:aero",),
    )
    qualified_commercial = _contextual_predicate(
        after_state="known",
        after_values=("qualified",),
        **alternative_subject,
        scope_refs=("production:energy",),
    )
    nuclear_base = {
        "after_state": "known",
        "after_values": ("judicial_restructuring",),
        **supplier_subject,
    }
    full["spec"]["impactRules"] = [
        {
            "ruleId": "rule:contextual-qualified-quality",
            "applicability": qualified_quality,
            "targetRef": "alternative:acieries-savoie",
            "requiredRoleRef": "role:quality-qualification",
            "obligationType": "qualification-evidence-check",
            "requiredEvidence": ["evidence:approved-qualification-record"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-qualified-continuity",
            "applicability": qualified_continuity,
            "targetRef": "production:aero",
            "requiredRoleRef": "role:operations-continuity",
            "obligationType": "continuity-option-selection",
            "requiredEvidence": ["evidence:selected-continuity-option"],
            "prerequisiteObligationTypes": ["qualification-evidence-check"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-qualified-commercial",
            "applicability": qualified_commercial,
            "targetRef": "supplier:forges-martelliere",
            "requiredRoleRef": "role:procurement-owner",
            "obligationType": "commercial-switch-review",
            "requiredEvidence": ["evidence:commercial-switch-review"],
            "prerequisiteObligationTypes": ["continuity-option-selection"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-nuclear-exposure",
            "applicability": _contextual_predicate(
                **nuclear_base,
                scope_refs=("supplier:forges-martelliere",),
            ),
            "targetRef": "supplier:forges-martelliere",
            "requiredRoleRef": "role:procurement-owner",
            "obligationType": "exposure-inventory",
            "requiredEvidence": ["evidence:proc01-index"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-nuclear-alternative",
            "applicability": _contextual_predicate(
                **nuclear_base,
                scope_refs=("order:energy-nuclear",),
            ),
            "targetRef": "alternative:acieries-savoie",
            "requiredRoleRef": "role:quality-qualification",
            "obligationType": "alternative-qualification",
            "requiredEvidence": ["evidence:nuclear-grade-qualification"],
            "prerequisiteObligationTypes": ["compliance-discovery"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-nuclear-continuity",
            "applicability": _contextual_predicate(
                **nuclear_base,
                scope_refs=("production:energy",),
            ),
            "targetRef": "production:energy",
            "requiredRoleRef": "role:operations-continuity",
            "obligationType": "continuity-assessment",
            "requiredEvidence": ["evidence:nuclear-continuity-assessment"],
            "prerequisiteObligationTypes": ["alternative-qualification"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-nuclear-contract-negative-control",
            "applicability": _contextual_predicate(
                **nuclear_base,
                scope_refs=("contract:forges-msa",),
                missing_behavior="false",
            ),
            "targetRef": "contract:forges-msa",
            "requiredRoleRef": "role:legal-reviewer",
            "obligationType": "contract-remedies",
            "requiredEvidence": ["evidence:msa-review"],
            "admissionStatus": "admitted",
        },
        {
            "ruleId": "rule:contextual-nuclear-finance-negative-control",
            "applicability": _contextual_predicate(
                **nuclear_base,
                scope_refs=("order:aero-rush",),
                missing_behavior="false",
            ),
            "targetRef": "ledger:supplier-exposure",
            "requiredRoleRef": "role:finance-exposure",
            "obligationType": "financial-exposure",
            "requiredEvidence": ["evidence:exposure-calculation"],
            "admissionStatus": "admitted",
        },
    ]
    full["spec"]["unknownTransitionDuties"] = [
        {
            "dutyId": "duty:contextual-nuclear-compliance-discovery",
            "applicability": _contextual_predicate(
                **nuclear_base,
                scope_refs=("control:nuclear-substitution",),
                missing_behavior="unknown",
            ),
            "targetRef": "control:nuclear-substitution",
            "requiredRoleRef": "role:compliance-reviewer",
            "obligationType": "compliance-discovery",
            "requiredEvidence": ["evidence:applicable-regulatory-basis"],
            "admissionStatus": "admitted",
        }
    ]
    full["digest"] = None
    contextual_full = seal_resource(
        OrganizationSnapshot.model_validate_json(json.dumps(full, ensure_ascii=False))
    )

    legacy_truncated = _load(
        INPUTS / "veracier-proc01-truncated.snapshot.json", OrganizationSnapshot
    )
    truncated = legacy_truncated.model_dump(mode="json", by_alias=True)
    truncated["metadata"].update(
        {
            "id": "snapshot:veracier-proc01-truncated-contextual",
            "revision": 2,
            "sourceRefs": [
                *truncated["metadata"]["sourceRefs"],
                "oac:contextual-applicability-v0.2-fixture",
            ],
        }
    )
    truncated["spec"]["unknownTransitionDuties"] = [
        {
            "dutyId": "duty:contextual-root-unknown-preservation",
            "applicability": _contextual_predicate(
                after_state="known",
                after_values=(
                    "operating",
                    "judicial_restructuring",
                    "liquidation",
                    "qualification_suspended",
                    "capacity_constrained",
                    "delivery_interrupted",
                ),
                **supplier_subject,
                scope_refs=("supplier:forges-martelliere",),
                missing_behavior="unknown",
            ),
            "targetRef": "supplier:forges-martelliere",
            "requiredRoleRef": "role:procurement-owner",
            "obligationType": "unknown-preservation",
            "requiredEvidence": ["evidence:confirmed-status-or-unknown-receipt"],
            "prerequisiteObligationTypes": ["source-discovery"],
            "admissionStatus": "admitted",
        }
    ]
    truncated["spec"]["completeness"].update(
        {
            "discoveryTargetRef": "boundary:source-discovery",
            "discoveryObligationType": "source-discovery",
            "discoveryEvidence": [
                "evidence:named-graph-gaps",
                "evidence:dependency-discovery-query-receipt",
            ],
        }
    )
    truncated["digest"] = None
    contextual_truncated = seal_resource(
        OrganizationSnapshot.model_validate_json(
            json.dumps(truncated, ensure_ascii=False)
        )
    )

    _dump(INPUTS / "veracier-proc01-contextual.snapshot.json", contextual_full)
    _dump(
        INPUTS / "veracier-proc01-truncated-contextual.snapshot.json",
        contextual_truncated,
    )
    return contextual_full, contextual_truncated


def _reseal_plan(data: dict[str, Any], plan_id: str) -> OrganizationPlan:
    data = deepcopy(data)
    data["metadata"]["id"] = plan_id
    data["digest"] = None
    parsed = OrganizationPlan.model_validate_json(json.dumps(data, ensure_ascii=False))
    return seal_resource(parsed)


def _mutation(
    base: OrganizationPlan,
    mutation_id: str,
    mutate: Callable[[dict[str, Any]], None],
) -> OrganizationPlan:
    data = base.model_dump(mode="json", by_alias=True)
    mutate(data)
    return _reseal_plan(data, f"plan:mutation:{mutation_id}")


def _omit_obligation(data: dict[str, Any]) -> None:
    work = next(item for item in data["spec"]["workUnits"] if len(item["obligationRefs"]) > 1)
    work["obligationRefs"].pop(0)


def _self_review(data: dict[str, Any]) -> None:
    role = next(
        item
        for item in data["spec"]["roleInstances"]
        if item["roleDefinitionRef"] == "role:quality-qualification"
    )
    role["principalRef"] = "principal:procurement-agent"


def _reverse_order(data: dict[str, Any]) -> None:
    edge = data["spec"]["happensBefore"][0]
    edge["predecessorRef"], edge["successorRef"] = (
        edge["successorRef"],
        edge["predecessorRef"],
    )


def _missing_evidence(data: dict[str, Any]) -> None:
    work = next(
        item
        for item in data["spec"]["workUnits"]
        if item["workUnitId"].endswith(":finance-exposure")
    )
    work["evidenceOutputs"].remove("evidence:exposure-calculation")


def _redundant_role(data: dict[str, Any]) -> None:
    original = next(
        item
        for item in data["spec"]["roleInstances"]
        if item["roleDefinitionRef"] == "role:legal-reviewer"
    )
    duplicate = deepcopy(original)
    duplicate["roleInstanceId"] += ":duplicate"
    data["spec"]["roleInstances"].append(duplicate)
    legal_work = next(
        item
        for item in data["spec"]["workUnits"]
        if item["workUnitId"].endswith(":legal-reviewer")
    )
    legal_work["roleInstanceRefs"].append(duplicate["roleInstanceId"])
    data["spec"]["minimality"]["nonRemovableRefs"].append(duplicate["roleInstanceId"])


def _input_root_mismatch(data: dict[str, Any]) -> None:
    data["spec"]["changeRef"]["digest"] = "sha256:" + ("0" * 64)


def _unknown_erasure(data: dict[str, Any]) -> None:
    data["spec"]["unresolvedRefs"] = []


def _false_non_impact(data: dict[str, Any]) -> None:
    path = next(item for item in data["spec"]["impactPaths"] if item["state"] == "unknown")
    path.update(
        {
            "state": "unaffected_proven",
            "edgeRefs": [],
            "ruleRefs": [],
            "origin": "bounded_non_impact",
            "reasonCodes": [],
            "truncated": False,
        }
    )


def _omit_applicability_evaluation(data: dict[str, Any]) -> None:
    referenced = next(
        evaluation_ref
        for path in data["spec"]["impactPaths"]
        for evaluation_ref in path.get("evaluationRefs", [])
    )
    data["spec"]["applicabilityEvaluations"] = [
        item
        for item in data["spec"]["applicabilityEvaluations"]
        if item["evaluationId"] != referenced
    ]
    for path in data["spec"]["impactPaths"]:
        if referenced in path.get("evaluationRefs", []):
            path["evaluationRefs"].remove(referenced)


def _forge_applicability_result(data: dict[str, Any]) -> None:
    evaluation = next(
        item
        for item in data["spec"]["applicabilityEvaluations"]
        if item["result"] == "TRUE"
    )
    evaluation["result"] = "FALSE"
    evaluation["reasonCodes"] = ["APPLICABILITY_SCOPE_MISMATCH"]


def _forge_applicability_witness(data: dict[str, Any]) -> None:
    evaluation = data["spec"]["applicabilityEvaluations"][0]
    evaluation["witnessRefs"] = sorted(
        {*evaluation["witnessRefs"], "snapshot:forged#/spec/nodes/0"}
    )


def _include_false_predicate_path(data: dict[str, Any]) -> None:
    false_ref = next(
        item["evaluationId"]
        for item in data["spec"]["applicabilityEvaluations"]
        if item["result"] == "FALSE"
    )
    path = next(item for item in data["spec"]["impactPaths"] if item["state"] == "affected")
    path["evaluationRefs"] = sorted({*path.get("evaluationRefs", []), false_ref})


def _omit_unknown_duty_path_binding(data: dict[str, Any]) -> None:
    path = next(item for item in data["spec"]["impactPaths"] if item.get("dutyRefs"))
    path["dutyRefs"] = []


def _remove_contextual_prerequisite(data: dict[str, Any]) -> None:
    data["spec"]["happensBefore"] = []


def _collapse_unknown_evaluation(data: dict[str, Any]) -> None:
    evaluation = next(
        item
        for item in data["spec"]["applicabilityEvaluations"]
        if item["result"] == "UNKNOWN"
    )
    evaluation["result"] = "TRUE"
    evaluation["reasonCodes"] = []


def _build_cases(witness_a: OrganizationPlan, witness_b: OrganizationPlan) -> None:
    label_set = json.loads(
        (PROFILE / "source-labels" / "edith-proc-01.json").read_text(encoding="utf-8")
    )
    labels_by_doc = {item["docId"]: item for item in label_set["masterIndexLabels"]}
    for packet_path in sorted(ANNOTATIONS.glob("SC-*.json")):
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        case_id = packet["caseId"]
        snapshot_path = (packet_path.parent / packet["frozenInputs"]["snapshotRef"]).resolve()
        try:
            snapshot_path.relative_to(PROFILE.resolve())
        except ValueError as exc:
            raise ValueError(f"snapshotRef escapes Supplier profile: {packet_path}") from exc
        snapshot = _load(snapshot_path, OrganizationSnapshot)
        change = _load(INPUTS / f"{case_id}.change.json", SemanticChangeSet)
        candidate = packet["candidateConstraints"]

        annotations: list[dict[str, Any]] = [
            {
                "field": "source.answerKey.PROC-01.groundTruth",
                "value": "empty-object",
                "evidenceRefs": ["edith:ANSWER_KEY.json#PROC-01"],
                "status": "source_gt",
            },
            {
                "field": "oac.annotationAuthority",
                "value": "project-authored-exploratory-not-human-gold",
                "evidenceRefs": [
                    f"profiles/supplier-change/annotations/{packet_path.name}"
                ],
                "status": "oac_candidate",
            },
            {
                "field": f"oac.counterfactual.{packet['counterfactual']['factor']}",
                "value": f"{packet['counterfactual']['before']}->{packet['counterfactual']['after']}",
                "evidenceRefs": [
                    f"authority:{packet['counterfactual']['assertedBy']}",
                    f"profiles/supplier-change/annotations/{packet_path.name}",
                ],
                "status": "oac_candidate",
            },
        ]
        for doc_id in packet["sourceLabels"]["documentRefs"]:
            label = labels_by_doc[doc_id]
            annotations.append(
                {
                    "field": f"source.masterIndex.{doc_id}.classification",
                    "value": label["classification"],
                    "evidenceRefs": [f"edith:MASTER_INDEX.csv#{doc_id}"],
                    "status": "source_gt",
                }
            )
        for obligation_type in candidate["requiredObligationTypes"]:
            annotations.append(
                {
                    "field": f"oac.candidateObligationType.{obligation_type}",
                    "value": "required-candidate",
                    "evidenceRefs": [
                        f"profiles/supplier-change/annotations/{packet_path.name}"
                    ],
                    "status": "oac_candidate",
                }
            )

        role_refs = tuple(candidate["admissibleRoleRefs"])
        principals = tuple(
            sorted(
                principal.principal_id
                for principal in snapshot.spec.principals
                if set(principal.eligible_role_refs).intersection(role_refs)
            )
        )
        required_obligations: tuple[str, ...] = ()
        required_obligation_types = tuple(candidate["requiredObligationTypes"])
        evidence_duties = tuple(candidate["evidenceDuties"])
        happens_before = [
            {
                "predecessorRef": left,
                "successorRef": right,
                "relation": "must_complete_before",
                "reasonRefs": [f"oac:candidate-order:{case_id}"],
            }
            for left, right in candidate["happensBefore"]
        ]
        witness_refs: list[dict[str, Any]] = []
        if case_id == "SC-001":
            required_obligations = tuple(
                item.obligation_id for item in witness_a.spec.obligations
            )
            role_refs = tuple(
                sorted(item.role_definition_ref for item in witness_a.spec.role_instances)
            )
            principals = tuple(
                sorted(item.principal_ref for item in witness_a.spec.role_instances)
            )
            evidence_duties = tuple(
                sorted(
                    {
                        evidence
                        for obligation in witness_a.spec.obligations
                        for evidence in obligation.required_evidence
                    }
                )
            )
            happens_before = [
                {
                    "predecessorRef": "role:procurement-owner",
                    "successorRef": role_ref,
                    "relation": "must_complete_before",
                    "reasonRefs": ["oac:derived-role-order:SC-001"],
                }
                for role_ref in (
                    "role:finance-exposure",
                    "role:legal-reviewer",
                    "role:operations-continuity",
                )
            ]
            witness_refs = [
                resource_ref(witness_a).model_dump(mode="json", by_alias=True),
                resource_ref(witness_b).model_dump(mode="json", by_alias=True),
            ]

        requested_verdict = candidate.get("requiredVerdictUntilDiscovery", "ACCEPT")
        case_data = {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "OrgChangeCase",
            "metadata": {
                "id": f"case:{case_id}",
                "namespace": "oac.examples.supplier",
                "revision": 1,
                "ownerRef": "profile:supplier-change",
                "governanceRef": "spec:001-oac-shadow-mvp:annotation-guide",
                "createdAt": "2026-08-23T00:00:00Z",
                "sourceRefs": [
                    "hf:lightonai/veracier-industries@844264a930674feacf6dee1844da77b0c4d66b2a",
                    f"profiles/supplier-change/annotations/{packet_path.name}",
                ],
            },
            "spec": {
                "snapshotRef": resource_ref(snapshot).model_dump(mode="json", by_alias=True),
                "changeRef": resource_ref(change).model_dump(mode="json", by_alias=True),
                "observationBoundary": packet["frozenInputs"]["observationBoundary"],
                "annotationStatus": "exploratory",
                "annotations": annotations,
                "constraintSet": {
                    "requiredObligationRefs": list(required_obligations),
                    "requiredObligationTypes": list(required_obligation_types),
                    "admissibleRoleRefs": list(role_refs),
                    "admissiblePrincipalRefs": list(principals),
                    "forbiddenCombinations": candidate.get("separation", []),
                    "happensBefore": happens_before,
                    "evidenceDuties": list(evidence_duties),
                    "acceptableUnknownRefs": candidate.get("acceptableUnknown", []),
                    "minimalityLevel": "inclusion_minimal",
                    "acceptableVerdicts": [requested_verdict],
                },
                "witnessRefs": witness_refs,
                "mutationFamily": ",".join(packet["mutationFamilies"]),
                "outcomeObservability": "none",
            },
            "digest": None,
        }
        case = OrgChangeCase.model_validate_json(json.dumps(case_data, ensure_ascii=False))
        _dump(CASES / packet_path.name, seal_resource(case))


def _refresh_matched_inputs() -> str:
    path = ROOT / "benchmark" / "matched-inputs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["cases"]:
        case = _load(ROOT / item["caseRef"], OrgChangeCase)
        item["caseDigest"] = case.digest
    projection = deepcopy(data)
    projection.pop("digest", None)
    digest = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    data["digest"] = digest
    _dump(path, data)
    for baseline_path in sorted((ROOT / "benchmark" / "baselines").glob("*.input.json")):
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["inputSetDigest"] = digest
        _dump(baseline_path, baseline)
    return digest


def build() -> dict[str, Any]:
    contextual_full, contextual_truncated = _build_contextual_snapshots()
    snapshot = _load(INPUTS / "veracier-proc01.snapshot.json", OrganizationSnapshot)
    change = _load(INPUTS / "SC-001.change.json", SemanticChangeSet)
    reference = compile_supplier_change(snapshot, change)
    if verify_plan(snapshot, change, reference).spec.verdict.value != "ACCEPT":
        raise RuntimeError("current compiler output must accept on legacy SC-001 roots")

    # These two v0alpha1 witnesses are immutable compatibility vectors.  Do not
    # rewrite them from the current compiler; semantic compatibility is proved
    # by the current verifier accepting their original canonical bytes.
    witness_a = _load(
        WITNESSES / "SC-001-witness-a.plan.json", OrganizationPlan
    )
    witness_b = _load(
        WITNESSES / "SC-001-witness-b.plan.json", OrganizationPlan
    )
    for witness in (witness_a, witness_b):
        certificate = verify_plan(snapshot, change, witness)
        if certificate.spec.verdict.value != "ACCEPT":
            raise RuntimeError(f"plural witness rejected: {witness.metadata.id}")
    _build_cases(witness_a, witness_b)
    input_set_digest = _refresh_matched_inputs()

    # This is a structurally valid horizontal SemanticChangeSet but outside the
    # Supplier Status Change Profile's exact one-delta applicability surface.
    # Keeping it sealed ensures the negative catches semantic over-acceptance,
    # not a wire-schema or digest failure.
    status_delta = change.spec.deltas[0]
    extra_delta = status_delta.model_copy(
        update={
            "path": "/renewalTerms",
            "before": status_delta.before.model_copy(update={"value": "net-30"}),
            "after": status_delta.after.model_copy(update={"value": "net-15"}),
            "before_version": "supplier-terms@12",
            "after_version": "supplier-terms@13",
        }
    )
    extra_delta_change = seal_resource(
        change.model_copy(
            update={
                "metadata": change.metadata.model_copy(
                    update={"id": "change:SC-001:extra-delta"}
                ),
                "spec": change.spec.model_copy(
                    update={"deltas": (*change.spec.deltas, extra_delta)}
                ),
                "digest": None,
            }
        )
    )
    extra_delta_plan_data = witness_a.model_dump(mode="json", by_alias=True)
    extra_delta_plan_data["metadata"]["sourceRefs"] = [
        snapshot.metadata.id,
        extra_delta_change.metadata.id,
    ]
    extra_delta_plan_data["spec"]["changeRef"] = resource_ref(
        extra_delta_change
    ).model_dump(mode="json", by_alias=True)
    extra_delta_plan = _reseal_plan(
        extra_delta_plan_data, "plan:negative:extra-delta"
    )
    _dump(FIXTURES / "negative" / "extra-delta.change.json", extra_delta_change)
    _dump(FIXTURES / "negative" / "extra-delta.plan.json", extra_delta_plan)

    unknown_change = _load(INPUTS / "SC-010.change.json", SemanticChangeSet)
    unknown_plan = _reseal_plan(
        compile_supplier_change(contextual_truncated, unknown_change).model_dump(
            mode="json", by_alias=True
        ),
        "plan:SC-010:unknown",
    )
    if verify_plan(
        contextual_truncated, unknown_change, unknown_plan
    ).spec.verdict.value != "UNKNOWN":
        raise RuntimeError("SC-010 contextual root Unknown must remain UNKNOWN")
    _dump(FIXTURES / "positive" / "SC-010-unknown.plan.json", unknown_plan)

    contextual_plans: dict[str, OrganizationPlan] = {}
    expected_contextual = {
        "SC-008": (
            "ACCEPT",
            {
                "role:quality-qualification",
                "role:operations-continuity",
                "role:procurement-owner",
            },
        ),
        "SC-009": (
            "PROVISIONAL",
            {
                "role:procurement-owner",
                "role:operations-continuity",
                "role:quality-qualification",
                "role:compliance-reviewer",
            },
        ),
    }
    for case_id, (expected_verdict, expected_roles) in expected_contextual.items():
        contextual_change = _load(INPUTS / f"{case_id}.change.json", SemanticChangeSet)
        contextual_plan = compile_supplier_change(contextual_full, contextual_change)
        contextual_certificate = verify_plan(
            contextual_full, contextual_change, contextual_plan
        )
        actual_roles = {
            item.role_definition_ref for item in contextual_plan.spec.role_instances
        }
        if contextual_certificate.spec.verdict.value != expected_verdict:
            raise RuntimeError(
                f"{case_id} contextual verdict must be {expected_verdict}, got "
                f"{contextual_certificate.spec.verdict.value}"
            )
        if actual_roles != expected_roles:
            raise RuntimeError(
                f"{case_id} contextual role set mismatch: {sorted(actual_roles)}"
            )
        contextual_plans[case_id] = contextual_plan
        _dump(
            FIXTURES / "positive" / f"{case_id}-contextual.plan.json",
            contextual_plan,
        )

    mutations: dict[str, tuple[OrganizationPlan, tuple[str, ...]]] = {
        "omit-obligation": (
            _mutation(witness_a, "omit-obligation", _omit_obligation),
            ("OBLIGATION_UNSATISFIED",),
        ),
        "self-review": (
            _mutation(witness_a, "self-review", _self_review),
            ("QUALIFICATION_INVALID", "SEPARATION_OF_DUTIES_VIOLATION"),
        ),
        "reverse-order": (
            _mutation(witness_a, "reverse-order", _reverse_order),
            ("ORDER_CONSTRAINT_MISSING",),
        ),
        "missing-evidence": (
            _mutation(witness_a, "missing-evidence", _missing_evidence),
            ("EVIDENCE_DUTY_MISSING",),
        ),
        "redundant-role": (
            _mutation(witness_a, "redundant-role", _redundant_role),
            ("OBLIGATION_UNSATISFIED",),
        ),
        "input-root-mismatch": (
            _mutation(witness_a, "input-root-mismatch", _input_root_mismatch),
            ("INPUT_ROOT_MISMATCH",),
        ),
        "unknown-erasure": (
            _mutation(unknown_plan, "unknown-erasure", _unknown_erasure),
            ("UNKNOWN_NOT_PRESERVED",),
        ),
        "false-non-impact": (
            _mutation(unknown_plan, "false-non-impact", _false_non_impact),
            ("IMPACT_PATH_OMITTED",),
        ),
    }
    records: list[dict[str, Any]] = []
    for mutation_id, (plan, reason_codes) in mutations.items():
        path = FIXTURES / "negative" / f"{mutation_id}.plan.json"
        _dump(path, plan)
        unknown_case = mutation_id in {"unknown-erasure", "false-non-impact"}
        records.append(
            {
                "mutationId": mutation_id,
                    "snapshotRef": (
                    "profiles/supplier-change/inputs/"
                    "veracier-proc01-truncated-contextual.snapshot.json"
                    if unknown_case
                    else "profiles/supplier-change/inputs/veracier-proc01.snapshot.json"
                ),
                "changeRef": (
                    "profiles/supplier-change/inputs/SC-010.change.json"
                    if unknown_case
                    else "profiles/supplier-change/inputs/SC-001.change.json"
                ),
                "planRef": str(path.relative_to(ROOT)),
                "expectedVerdict": "REJECT",
                "expectedReasonCodes": list(reason_codes),
                "targeted": True,
            }
        )

    contextual_mutations: dict[
        str, tuple[OrganizationPlan, str, tuple[str, ...]]
    ] = {
        "applicability-evaluation-omission": (
            _mutation(
                contextual_plans["SC-008"],
                "applicability-evaluation-omission",
                _omit_applicability_evaluation,
            ),
            "SC-008",
            ("APPLICABILITY_EVALUATION_MISSING",),
        ),
        "applicability-result-forgery": (
            _mutation(
                contextual_plans["SC-008"],
                "applicability-result-forgery",
                _forge_applicability_result,
            ),
            "SC-008",
            ("APPLICABILITY_EVALUATION_MISMATCH",),
        ),
        "applicability-witness-forgery": (
            _mutation(
                contextual_plans["SC-008"],
                "applicability-witness-forgery",
                _forge_applicability_witness,
            ),
            "SC-008",
            ("APPLICABILITY_WITNESS_MISMATCH",),
        ),
        "false-predicate-traversal": (
            _mutation(
                contextual_plans["SC-009"],
                "false-predicate-traversal",
                _include_false_predicate_path,
            ),
            "SC-009",
            ("PREDICATE_FALSE_PATH_INCLUDED",),
        ),
        "unknown-duty-omission": (
            _mutation(
                contextual_plans["SC-009"],
                "unknown-duty-omission",
                _omit_unknown_duty_path_binding,
            ),
            "SC-009",
            ("UNKNOWN_TRANSITION_DUTY_MISSING",),
        ),
        "contextual-prerequisite-removal": (
            _mutation(
                contextual_plans["SC-008"],
                "contextual-prerequisite-removal",
                _remove_contextual_prerequisite,
            ),
            "SC-008",
            ("PREREQUISITE_OBLIGATION_ORDER_MISSING",),
        ),
        "root-unknown-collapse": (
            _mutation(
                unknown_plan,
                "root-unknown-collapse",
                _collapse_unknown_evaluation,
            ),
            "SC-010",
            ("APPLICABILITY_EVALUATION_MISMATCH",),
        ),
    }
    for mutation_id, (plan, case_id, reason_codes) in contextual_mutations.items():
        path = FIXTURES / "negative" / f"{mutation_id}.plan.json"
        _dump(path, plan)
        records.append(
            {
                "mutationId": mutation_id,
                "snapshotRef": (
                    "profiles/supplier-change/inputs/"
                    "veracier-proc01-truncated-contextual.snapshot.json"
                    if case_id == "SC-010"
                    else "profiles/supplier-change/inputs/"
                    "veracier-proc01-contextual.snapshot.json"
                ),
                "changeRef": f"profiles/supplier-change/inputs/{case_id}.change.json",
                "planRef": str(path.relative_to(ROOT)),
                "expectedVerdict": "REJECT",
                "expectedReasonCodes": list(reason_codes),
                "targeted": True,
            }
        )

    digest_mismatch = witness_a.model_dump(mode="json", by_alias=True)
    digest_mismatch["digest"] = "sha256:" + ("0" * 64)
    _dump(FIXTURES / "negative" / "digest-mismatch.plan.json", digest_mismatch)
    invalid_effect = witness_a.model_dump(mode="json", by_alias=True)
    invalid_effect["spec"]["effectCeiling"] = "external_effect"
    invalid_effect["digest"] = "sha256:" + ("0" * 64)
    _dump(FIXTURES / "negative" / "invalid-effect.plan.json", invalid_effect)
    missing_digest = snapshot.model_dump(mode="json", by_alias=True)
    missing_digest.pop("digest", None)
    _dump(FIXTURES / "negative" / "missing-digest.snapshot.json", missing_digest)

    manifest = {
        "mutationManifestVersion": "1.1",
        "claimLimit": "Structural mechanics only; no enterprise-effectiveness claim.",
        "registeredMutationCount": len(records),
        "targetDetectionThreshold": 0.9,
        "records": records,
    }
    _dump(ROOT / "tck" / "mutations" / "manifest.json", manifest)
    return {
        "witnessA": witness_a.digest,
        "witnessB": witness_b.digest,
        "contextualFull": contextual_full.digest,
        "unknown": unknown_plan.digest,
        "extraDeltaChange": extra_delta_change.digest,
        "inputSet": input_set_digest,
        "mutations": len(records),
    }


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
