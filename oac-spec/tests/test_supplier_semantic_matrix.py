from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator

from oac.models import ApplicabilityResult, ImpactState
from oac.sealed import admit_sealed_resource
from oac.supplier import (
    ProfileError,
    derive_supplier_contract_from_admitted,
)

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "profiles/supplier-change/inputs/veracier-proc01-contextual.snapshot.json"
CHANGE_008 = ROOT / "profiles/supplier-change/inputs/SC-008.change.json"
CHANGE_009 = ROOT / "profiles/supplier-change/inputs/SC-009.change.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _seal(value: dict[str, Any]) -> bytes:
    projection = copy.deepcopy(value)
    projection.pop("digest", None)
    projection["digest"] = (
        "sha256:" + hashlib.sha256(rfc8785.dumps(projection)).hexdigest()
    )
    return rfc8785.dumps(projection)


def _derive(snapshot: dict[str, Any], change: dict[str, Any]):
    admitted_snapshot = admit_sealed_resource(
        _seal(snapshot), "OrganizationSnapshot"
    )
    admitted_change = admit_sealed_resource(_seal(change), "SemanticChangeSet")
    return derive_supplier_contract_from_admitted(admitted_snapshot, admitted_change)


def _node(snapshot: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(item for item in snapshot["spec"]["nodes"] if item["nodeId"] == node_id)


def _edge(snapshot: dict[str, Any], edge_id: str) -> dict[str, Any]:
    return next(
        item
        for item in snapshot["spec"]["dependencyEdges"]
        if item["edgeId"] == edge_id
    )


@pytest.mark.parametrize("status", ["candidate", "disputed"])
def test_candidate_or_disputed_subject_is_an_unknown_root(status: str) -> None:
    snapshot = _load(SNAPSHOT)
    change = _load(CHANGE_008)
    _node(snapshot, change["spec"]["subjectRef"])["admissionStatus"] = status
    # Remove prerequisite noise so this case observes root authority rather
    # than the independent fail-closed prerequisite rule.
    for rule in snapshot["spec"]["impactRules"]:
        rule.pop("prerequisiteObligationTypes", None)

    derived = _derive(snapshot, change)

    assert derived.root_applicability_unknown is True
    assert change["metadata"]["id"] in derived.unresolved_refs
    assert all(
        path.state is ImpactState.UNKNOWN
        for path in derived.impact_paths
        if path.target_ref == change["spec"]["subjectRef"]
    )
    assert not any(
        path.state is ImpactState.AFFECTED and path.rule_refs
        for path in derived.impact_paths
    )
    assert all(
        not any(
            witness.endswith(("/nodeType", "/domainRef"))
            and "/spec/nodes/13/" in witness
            for witness in evaluation.witness_refs
        )
        for evaluation in derived.applicability_evaluations
    )


def test_retracted_changed_subject_fails_before_closure() -> None:
    snapshot = _load(SNAPSHOT)
    change = _load(CHANGE_009)
    _node(snapshot, change["spec"]["subjectRef"])["admissionStatus"] = "retracted"

    with pytest.raises(ProfileError) as exc_info:
        _derive(snapshot, change)

    assert exc_info.value.reason_code == "RETRACTED_SOURCE_EXCLUDED"


def test_retracted_edge_remains_in_ledger_but_cannot_materialize_a_path() -> None:
    snapshot = _load(SNAPSHOT)
    change = _load(CHANGE_009)
    edge_id = "edge:contextual-supplier-nuclear-order"
    _edge(snapshot, edge_id)["admissionStatus"] = "retracted"

    derived = _derive(snapshot, change)
    evaluation = next(
        item for item in derived.applicability_evaluations if item.source_ref == edge_id
    )

    assert evaluation.result is ApplicabilityResult.FALSE
    assert "RETRACTED_SOURCE_EXCLUDED" in evaluation.reason_codes
    assert not any(edge_id in path.edge_refs for path in derived.impact_paths)


def test_candidate_edge_separates_predicate_and_closure_authority_reasons() -> None:
    snapshot = _load(SNAPSHOT)
    change = _load(CHANGE_009)
    edge_id = "edge:contextual-supplier-nuclear-order"
    _edge(snapshot, edge_id)["admissionStatus"] = "candidate"

    derived = _derive(snapshot, change)
    evaluation = next(
        item for item in derived.applicability_evaluations if item.source_ref == edge_id
    )
    guarded = [path for path in derived.impact_paths if edge_id in path.edge_refs]

    assert evaluation.result is ApplicabilityResult.UNKNOWN
    assert evaluation.reason_codes == ("CANDIDATE_INPUT_NOT_AUTHORITY",)
    assert guarded
    assert all("CANDIDATE_EDGE_NOT_AUTHORITY" in path.reason_codes for path in guarded)
    assert all("CANDIDATE_INPUT_NOT_AUTHORITY" in path.reason_codes for path in guarded)


def test_complete_boundary_residuals_distinguish_cut_unknown_and_retracted() -> None:
    change = _load(CHANGE_009)

    cut_snapshot = _load(SNAPSHOT)
    _node(cut_snapshot, "order:aero-rush")["admissionStatus"] = "candidate"
    cut = _derive(cut_snapshot, change)
    cut_path = next(
        path for path in cut.impact_paths if path.target_ref == "order:aero-rush"
    )
    assert cut_path.state is ImpactState.UNAFFECTED_PROVEN
    assert cut_path.origin == "bounded_non_impact"
    assert cut_path.evaluation_refs

    unknown_snapshot = _load(SNAPSHOT)
    _node(unknown_snapshot, "order:aero-av3000")["admissionStatus"] = "candidate"
    unknown = _derive(unknown_snapshot, change)
    unknown_path = next(
        path for path in unknown.impact_paths if path.target_ref == "order:aero-av3000"
    )
    assert unknown_path.state is ImpactState.UNKNOWN
    assert unknown_path.reason_codes == ("CANDIDATE_INPUT_NOT_AUTHORITY",)

    retracted_snapshot = _load(SNAPSHOT)
    _node(retracted_snapshot, "order:aero-av3000")["admissionStatus"] = "retracted"
    retracted = _derive(retracted_snapshot, change)
    retracted_path = next(
        path for path in retracted.impact_paths if path.target_ref == "order:aero-av3000"
    )
    assert retracted_path.state is ImpactState.OUT_OF_DECLARED_SCOPE
    assert retracted_path.origin == "excluded"
    assert retracted_path.reason_codes == ("RETRACTED_SOURCE_EXCLUDED",)


def test_legacy_discovery_and_implicit_boundary_identifiers_are_frozen() -> None:
    depth_snapshot = _load(SNAPSHOT)
    depth_snapshot["spec"]["completeness"]["maxDepth"] = 1
    depth = _derive(depth_snapshot, _load(CHANGE_009))
    discovery = next(
        obligation
        for obligation in depth.obligations
        if obligation.target_ref == "order:energy-nuclear"
        and obligation.origin == "discovery_gap"
    )
    assert discovery.obligation_type == "discover_dependency"
    assert discovery.required_evidence == ("dependency_admission_decision",)

    partial_snapshot = _load(SNAPSHOT)
    completeness = partial_snapshot["spec"]["completeness"]
    completeness["status"] = "partial"
    completeness["knownGaps"] = []
    completeness.pop("discoveryTargetRef", None)
    completeness.pop("discoveryObligationType", None)
    completeness.pop("discoveryEvidence", None)
    partial = _derive(partial_snapshot, _load(CHANGE_008))
    assert any(
        path.target_ref == "urn:oac:boundary:unobserved"
        and path.state is ImpactState.UNKNOWN
        for path in partial.impact_paths
    )


def test_non_admitted_unknown_duty_role_has_primary_error() -> None:
    snapshot = _load(SNAPSHOT)
    _node(snapshot, "control:nuclear-substitution")  # fixture coherence guard
    role = next(
        item
        for item in snapshot["spec"]["roleDefinitions"]
        if item["roleId"] == "role:compliance-reviewer"
    )
    role["admissionStatus"] = "candidate"

    with pytest.raises(ProfileError) as exc_info:
        _derive(snapshot, _load(CHANGE_009))

    assert exc_info.value.reason_code == "UNKNOWN_TRANSITION_DUTY_INVALID"


def test_supplier_semantic_rule_set_is_closed_digest_bound_and_unique() -> None:
    rules = _load(
        ROOT / "profiles/supplier-change/semantic-rules-v0.2.json"
    )
    schema = _load(ROOT / "ctk/schemas/ProfileSemanticRuleSet.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(rules)

    projection = {key: value for key, value in rules.items() if key != "digest"}
    assert rules["digest"] == (
        "sha256:" + hashlib.sha256(rfc8785.dumps(projection)).hexdigest()
    )
    rule_ids = [item["ruleId"] for item in rules["rules"]]
    assert len(rule_ids) == len(set(rule_ids)) == 10
