from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oac.applicability import (
    evaluate_applicability,
    normalized_applicability,
    strong_kleene_all,
    strong_kleene_any,
)
from oac.canonical import calculate_digest, parse_resource, seal_resource
from oac.models import (
    CONTEXTUAL_PREDICATE_VERSION,
    LEGACY_PREDICATE_VERSION,
    AdmissionStatus,
    ApplicabilityResult,
    CompletenessManifest,
    ContextualApplicabilityPredicate,
    DependencyEdge,
    OrganizationSnapshot,
    RelationType,
    ScopeSelector,
    SemanticChangeSet,
    SubjectSelector,
)
from oac.registry import PREDICATE_VERSION_REGISTRY, REASON_CODE_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "profiles" / "supplier-change" / "inputs"


def _resource(path: Path) -> OrganizationSnapshot | SemanticChangeSet:
    resource = parse_resource(path.read_bytes())
    assert isinstance(resource, (OrganizationSnapshot, SemanticChangeSet))
    return resource


def _snapshot() -> OrganizationSnapshot:
    resource = _resource(INPUTS / "veracier-proc01.snapshot.json")
    assert isinstance(resource, OrganizationSnapshot)
    return resource


def _change(case_id: str = "SC-008") -> SemanticChangeSet:
    resource = _resource(INPUTS / f"{case_id}.change.json")
    assert isinstance(resource, SemanticChangeSet)
    return resource


def _contextual_snapshot(
    *,
    missing_behavior: str = "unknown",
    extra_scope_ref: str | None = None,
    predicate_version: str = CONTEXTUAL_PREDICATE_VERSION,
) -> OrganizationSnapshot:
    snapshot = _snapshot()
    first = snapshot.spec.dependency_edges[0]
    scope_refs = ("production:aero", "production:energy")
    if extra_scope_ref is not None:
        scope_refs = (*scope_refs, extra_scope_ref)
    predicate = ContextualApplicabilityPredicate(
        predicateVersion=predicate_version,
        semanticType="supplier.status",
        afterState="known",
        afterValues=("qualified",),
        subjectSelector=SubjectSelector(
            subjectRefs=("alternative:acieries-savoie",),
            nodeTypes=("supplier-alternative",),
            domainRefs=("domain:quality",),
        ),
        scopeSelector=ScopeSelector(
            refs=scope_refs,
            matchMode="all",
            missingBehavior=missing_behavior,
        ),
        relationTypes=(RelationType.BUSINESS_DEPENDENCY,),
    )
    contextual_edge = DependencyEdge(
        edgeId=first.edge_id,
        sourceRef=first.source_ref,
        targetRef=first.target_ref,
        relationType=first.relation_type,
        transferPredicate=predicate,
        admissionStatus=first.admission_status,
    )
    spec = snapshot.spec.model_copy(
        update={
            "dependency_edges": (contextual_edge, *snapshot.spec.dependency_edges[1:])
        }
    )
    return seal_resource(snapshot.model_copy(update={"spec": spec, "digest": None}))


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((ApplicabilityResult.TRUE, ApplicabilityResult.TRUE), ApplicabilityResult.TRUE),
        ((ApplicabilityResult.TRUE, ApplicabilityResult.UNKNOWN), ApplicabilityResult.UNKNOWN),
        ((ApplicabilityResult.FALSE, ApplicabilityResult.UNKNOWN), ApplicabilityResult.FALSE),
        ((), ApplicabilityResult.TRUE),
    ],
)
def test_strong_kleene_conjunction(
    values: tuple[ApplicabilityResult, ...], expected: ApplicabilityResult
) -> None:
    assert strong_kleene_all(values) is expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((ApplicabilityResult.FALSE, ApplicabilityResult.FALSE), ApplicabilityResult.FALSE),
        ((ApplicabilityResult.FALSE, ApplicabilityResult.UNKNOWN), ApplicabilityResult.UNKNOWN),
        ((ApplicabilityResult.TRUE, ApplicabilityResult.UNKNOWN), ApplicabilityResult.TRUE),
        ((), ApplicabilityResult.FALSE),
    ],
)
def test_strong_kleene_disjunction(
    values: tuple[ApplicabilityResult, ...], expected: ApplicabilityResult
) -> None:
    assert strong_kleene_any(values) is expected


def test_legacy_predicate_has_lossless_in_memory_normal_form() -> None:
    predicate = _snapshot().spec.dependency_edges[0].transfer_predicate
    normalized = normalized_applicability(predicate)
    assert normalized.predicate_version == LEGACY_PREDICATE_VERSION
    assert normalized.after_state == "known"
    assert normalized.after_values == predicate.after_values
    assert normalized.subject_selector is None
    assert normalized.scope_selector is None
    assert normalized.relation_types == ()


def test_contextual_predicate_matches_exact_subject_scope_and_relation() -> None:
    snapshot = _contextual_snapshot()
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.TRUE
    assert evaluation.reason_codes == ()
    assert evaluation.witness_refs == tuple(sorted(set(evaluation.witness_refs)))
    assert any(ref.endswith("/subjectRef") for ref in evaluation.witness_refs)
    assert any(ref.endswith("/scopeRefs") for ref in evaluation.witness_refs)
    assert any(ref.endswith("/relationType") for ref in evaluation.witness_refs)


@pytest.mark.parametrize(
    ("missing_behavior", "expected", "reason"),
    [
        ("false", ApplicabilityResult.FALSE, "APPLICABILITY_SCOPE_MISMATCH"),
        ("unknown", ApplicabilityResult.UNKNOWN, "APPLICABILITY_INPUT_MISSING"),
    ],
)
def test_scope_missing_behavior_is_explicit(
    missing_behavior: str,
    expected: ApplicabilityResult,
    reason: str,
) -> None:
    snapshot = _contextual_snapshot(
        missing_behavior=missing_behavior,
        extra_scope_ref="production:not-declared",
    )
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is expected
    assert reason in evaluation.reason_codes


def test_candidate_subject_cannot_establish_applicability() -> None:
    snapshot = _contextual_snapshot()
    nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
        if node.node_id == "alternative:acieries-savoie"
        else node
        for node in snapshot.spec.nodes
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert "CANDIDATE_INPUT_NOT_AUTHORITY" in evaluation.reason_codes


def test_candidate_subject_fields_cannot_establish_false() -> None:
    snapshot = _contextual_snapshot()
    nodes = tuple(
        node.model_copy(
            update={
                "node_type": "candidate-only-type",
                "admission_status": AdmissionStatus.CANDIDATE,
            }
        )
        if node.node_id == "alternative:acieries-savoie"
        else node
        for node in snapshot.spec.nodes
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert "APPLICABILITY_SUBJECT_MISMATCH" not in evaluation.reason_codes


def test_known_predicate_mismatch_dominates_candidate_source_unknown() -> None:
    snapshot = _contextual_snapshot()
    edge = snapshot.spec.dependency_edges[0].model_copy(
        update={"admission_status": AdmissionStatus.CANDIDATE}
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={
                        "dependency_edges": (
                            edge,
                            *snapshot.spec.dependency_edges[1:],
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    evaluation = evaluate_applicability(
        snapshot,
        _change("SC-009"),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.FALSE
    assert "CANDIDATE_INPUT_NOT_AUTHORITY" in evaluation.reason_codes
    assert "APPLICABILITY_AFTER_VALUE_MISMATCH" in evaluation.reason_codes


def test_unsupported_predicate_version_is_total_and_stable() -> None:
    snapshot = _contextual_snapshot(predicate_version="oac.supplier.applicability/v999")
    edge = snapshot.spec.dependency_edges[0]
    first = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    second = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert first == second
    assert first.result is ApplicabilityResult.UNKNOWN
    assert first.reason_codes == ("PREDICATE_VERSION_UNSUPPORTED",)


def test_registered_legacy_version_cannot_be_smuggled_in_contextual_wire() -> None:
    snapshot = _contextual_snapshot(predicate_version=LEGACY_PREDICATE_VERSION)
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        _change(),
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert evaluation.reason_codes == ("PREDICATE_DEFINITION_INVALID",)


@pytest.mark.parametrize("source_kind", ["rule", "duty"])
def test_relation_types_require_an_edge_relation_context(source_kind: str) -> None:
    snapshot = _resource(INPUTS / "veracier-proc01-contextual.snapshot.json")
    assert isinstance(snapshot, OrganizationSnapshot)
    if source_kind == "rule":
        source = snapshot.spec.impact_rules[0]
        mutated_source = source.model_copy(
            update={
                "applicability": source.applicability.model_copy(  # type: ignore[union-attr]
                    update={"relation_types": (RelationType.BUSINESS_DEPENDENCY,)}
                )
            }
        )
        snapshot = seal_resource(
            snapshot.model_copy(
                update={
                    "spec": snapshot.spec.model_copy(
                        update={
                            "impact_rules": (
                                mutated_source,
                                *snapshot.spec.impact_rules[1:],
                            )
                        }
                    ),
                    "digest": None,
                }
            )
        )
        source_ref = mutated_source.rule_id
    else:
        source = snapshot.spec.unknown_transition_duties[0]
        mutated_source = source.model_copy(
            update={
                "applicability": source.applicability.model_copy(
                    update={"relation_types": (RelationType.BUSINESS_DEPENDENCY,)}
                )
            }
        )
        snapshot = seal_resource(
            snapshot.model_copy(
                update={
                    "spec": snapshot.spec.model_copy(
                        update={"unknown_transition_duties": (mutated_source,)}
                    ),
                    "digest": None,
                }
            )
        )
        source_ref = mutated_source.duty_id

    evaluation = evaluate_applicability(
        snapshot,
        _change("SC-009"),
        mutated_source,
        source_ref=source_ref,
    )
    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert evaluation.reason_codes == ("PREDICATE_DEFINITION_INVALID",)


def test_synthetic_unknown_is_not_a_known_business_value() -> None:
    snapshot = _contextual_snapshot()
    change = _change()
    delta = change.spec.deltas[0].model_copy(
        update={"after": change.spec.deltas[0].after.model_copy(update={"value": "unknown"})}
    )
    change = seal_resource(
        change.model_copy(
            update={
                "spec": change.spec.model_copy(update={"deltas": (delta,)}),
                "digest": None,
            }
        )
    )
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        change,
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert "SYNTHETIC_UNKNOWN_VALUE" in evaluation.reason_codes


@pytest.mark.parametrize(
    ("state", "operation"),
    [("not_observable", "invalidate"), ("not_applicable", "remove")],
)
def test_non_known_after_states_preserve_legacy_unknown(
    state: str, operation: str
) -> None:
    snapshot = _snapshot()
    change_data = _change("SC-010").model_dump(mode="json", by_alias=True)
    change_data["spec"]["deltas"][0]["operation"] = operation
    change_data["spec"]["deltas"][0]["after"] = {"state": state}
    change_data["digest"] = None
    change = seal_resource(
        SemanticChangeSet.model_validate_json(
            json.dumps(change_data, ensure_ascii=False)
        )
    )
    edge = snapshot.spec.dependency_edges[0]
    evaluation = evaluate_applicability(
        snapshot,
        change,
        edge.transfer_predicate,
        source_ref=edge.edge_id,
    )
    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert "APPLICABILITY_INPUT_UNKNOWN" in evaluation.reason_codes


def test_predicate_registry_and_evaluator_codes_are_public() -> None:
    assert set(PREDICATE_VERSION_REGISTRY) == {
        LEGACY_PREDICATE_VERSION,
        CONTEXTUAL_PREDICATE_VERSION,
    }
    assert {
        "APPLICABILITY_EVALUATION_MISSING",
        "APPLICABILITY_EVALUATION_MISMATCH",
        "APPLICABILITY_WITNESS_MISMATCH",
        "UNKNOWN_TRANSITION_DUTY_MISSING",
        "ROOT_APPLICABILITY_UNKNOWN",
        "PREDICATE_VERSION_UNSUPPORTED",
        "SYNTHETIC_UNKNOWN_VALUE",
    }.issubset(REASON_CODE_REGISTRY)


def test_evaluator_has_no_benchmark_or_annotation_oracle_and_all_codes_are_registered() -> None:
    path = ROOT / "src" / "oac" / "applicability.py"
    source = path.read_text(encoding="utf-8")
    assert "SC-" not in source
    assert "constraint_set" not in source
    assert "annotation_status" not in source

    tree = ast.parse(source)
    prefixes = (
        "APPLICABILITY_",
        "PREDICATE_",
        "SYNTHETIC_",
        "CANDIDATE_",
        "RETRACTED_",
        "CHANGE_",
        "DIGEST_",
    )
    emitted = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(prefixes)
    }
    assert emitted <= REASON_CODE_REGISTRY.keys()


def test_contextual_discovery_fields_are_all_or_none() -> None:
    completeness = _snapshot().spec.completeness
    with pytest.raises(ValidationError):
        CompletenessManifest.model_validate(
            {
                **completeness.model_dump(mode="json", by_alias=True),
                "discoveryTargetRef": "boundary:source-discovery",
            }
        )


def test_legacy_resources_keep_exact_detached_digests_and_wire_projection() -> None:
    legacy_inputs = [
        *(INPUTS / f"SC-{index:03d}.change.json" for index in range(1, 11)),
        INPUTS / "veracier-proc01.snapshot.json",
        INPUTS / "veracier-proc01-truncated.snapshot.json",
    ]
    paths = [
        *legacy_inputs,
        *sorted((ROOT / "profiles" / "supplier-change" / "witnesses").glob("*.json")),
    ]
    assert len(paths) == 14
    for path in paths:
        raw = json.loads(path.read_bytes())
        resource = parse_resource(path.read_bytes())
        assert calculate_digest(resource) == raw["digest"], path
        dumped = resource.model_dump(mode="json", by_alias=True)
        if resource.kind == "OrganizationSnapshot":
            assert "unknownTransitionDuties" not in dumped["spec"]
            completeness = dumped["spec"]["completeness"]
            assert "discoveryTargetRef" not in completeness
            assert "discoveryObligationType" not in completeness
            assert "discoveryEvidence" not in completeness
        if resource.kind == "OrganizationPlan":
            assert "applicabilityEvaluations" not in dumped["spec"]
            assert all("evaluationRefs" not in path for path in dumped["spec"]["impactPaths"])

    # Cases are benchmark resources, not immutable legacy wire vectors.  Spec
    # 002 intentionally upgrades them from conceptual refs to obligation types.
    for path in sorted((ROOT / "profiles" / "supplier-change" / "cases").glob("SC-*.json")):
        raw = json.loads(path.read_bytes())
        resource = parse_resource(path.read_bytes())
        assert calculate_digest(resource) == raw["digest"], path
        dumped = resource.model_dump(mode="json", by_alias=True)
        assert "requiredObligationTypes" in dumped["spec"]["constraintSet"]
