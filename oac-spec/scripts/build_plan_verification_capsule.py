#!/usr/bin/env python3
"""Build or admit the frozen v0.1 seed-1 plural Plan-verification capsule.

The recipe and RequirementSet are authored before any SUT execution. This builder
materializes their raw resources, seals generated Plans, and binds every byte into
one detached-JCS capsule. It does not derive expected verdicts from a verifier.
The published seed-1 output is immutable; the command-line gate only admits those
historical bytes and never rebuilds them from later repository-wide registries.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import rfc8785

from oac.canonical import parse_resource
from oac.compiler import compile_supplier_change

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "experiments/plan-verification-portability/v0.1-seed-1"
RECIPE_PATH = ROOT / "ctk/generators/plan-verification-v01-seed-1.recipes.json"
REQUIREMENT_PATH = (
    ROOT / "ctk/bundles/plan-verification-v01-seed-1.requirement-set.json"
)
CAPABILITY_PATH = (
    ROOT / "ctk/capabilities/plan-verification-v01-seed-1.capability-set.json"
)
CONTRACT_PATHS = (
    Path("standard/oac-plan-verification-relation-v0.1.md"),
    Path("ctk/protocol/stdio-v3-plan-verification.md"),
    Path("ctk/schemas/PlanVerificationResult.schema.json"),
    Path("ctk/schemas/PlanVerificationCapabilitySet.schema.json"),
    Path("schemas/OrganizationPlan.schema.json"),
    Path("schemas/reason-code-registry.json"),
    Path("standard/oac-sealed-resource-v1.md"),
    Path("profiles/supplier-change/profile.md"),
)
DETACHED_FIELDS = {
    "capabilitySetDigest",
    "recipeSetDigest",
    "requirementSetDigest",
}


class BuildError(RuntimeError):
    """The frozen source material is internally inconsistent."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BuildError(f"{path} must contain a JSON object")
    return value


def _verify_detached(value: Mapping[str, Any], field: str, label: str) -> None:
    projection = {key: item for key, item in value.items() if key != field}
    if value.get(field) != _digest(rfc8785.dumps(projection)):
        raise BuildError(f"{label} detached digest mismatch")


def _seal_map(value: Mapping[str, Any]) -> bytes:
    sealed = copy.deepcopy(dict(value))
    sealed.pop("digest", None)
    sealed["digest"] = _digest(rfc8785.dumps(sealed))
    return rfc8785.dumps(sealed) + b"\n"


def _with_fixture_identity(value: dict[str, Any]) -> None:
    """Assign an opaque identity from transformed Plan content, never from case metadata."""

    metadata = value["metadata"]
    value["spec"]["compilerId"] = "oac.fixture.plan-verification-generator"
    projection = copy.deepcopy(value)
    projection.pop("digest", None)
    projection["metadata"].pop("id", None)
    opaque_id = hashlib.sha256(
        rfc8785.dumps(
            {
                "domain": "oac.plan-verification.fixture-identity/v1",
                "planWithoutIdentity": projection,
            }
        )
    ).hexdigest()
    metadata["id"] = f"urn:oac:fixture:plan-verification:{opaque_id}"


def _tamper_claimed_digest(raw: bytes) -> bytes:
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("digest"), str):
        raise BuildError("digest-tamper source is not a sealed object")
    value["digest"] = "sha256:" + (
        "1" * 64 if value["digest"] == "sha256:" + "0" * 64 else "0" * 64
    )
    return rfc8785.dumps(value) + b"\n"


def _evidence_for(plan: Mapping[str, Any], obligation_refs: list[str]) -> list[str]:
    obligation_by_id = {
        item["obligationId"]: item for item in plan["spec"]["obligations"]
    }
    return sorted(
        {
            evidence
            for obligation_ref in obligation_refs
            for evidence in obligation_by_id[obligation_ref]["requiredEvidence"]
        }
    )


def _split_operations_work(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    role_by_id = {
        item["roleInstanceId"]: item for item in spec["roleInstances"]
    }
    operations = next(
        item
        for item in spec["workUnits"]
        if any(
            role_by_id[ref]["roleDefinitionRef"] == "role:operations-continuity"
            for ref in item["roleInstanceRefs"]
        )
    )
    prerequisite_ref = next(
        item["obligationId"]
        for item in spec["obligations"]
        if item["obligationType"] == "continuity-option-selection"
    )
    other_refs = [
        item for item in operations["obligationRefs"] if item != prerequisite_ref
    ]
    if not other_refs:
        raise BuildError("SC-008 Operations work has no legal split partition")
    prerequisite = copy.deepcopy(operations)
    prerequisite["workUnitId"] = f"{operations['workUnitId']}:split-prerequisite"
    prerequisite["obligationRefs"] = [prerequisite_ref]
    prerequisite["evidenceOutputs"] = _evidence_for(plan, [prerequisite_ref])
    other = copy.deepcopy(operations)
    other["workUnitId"] = f"{operations['workUnitId']}:split-other"
    other["obligationRefs"] = other_refs
    other["evidenceOutputs"] = _evidence_for(plan, other_refs)

    replacement: list[dict[str, Any]] = []
    for work in spec["workUnits"]:
        replacement.extend(
            [prerequisite, other]
            if work["workUnitId"] == operations["workUnitId"]
            else [work]
        )
    spec["workUnits"] = replacement

    obligation_by_id = {
        item["obligationId"]: item for item in spec["obligations"]
    }
    other_dependency_path_refs = {
        path_ref
        for obligation_ref in other_refs
        for path_ref in obligation_by_id[obligation_ref]["pathRefs"]
    }
    constraints: list[dict[str, Any]] = []
    for edge in spec["happensBefore"]:
        predecessor_refs = (
            [prerequisite["workUnitId"]]
            if edge["predecessorRef"] == operations["workUnitId"]
            else [edge["predecessorRef"]]
        )
        successor_refs = (
            [prerequisite["workUnitId"], other["workUnitId"]]
            if edge["successorRef"] == operations["workUnitId"]
            else [edge["successorRef"]]
        )
        for predecessor_ref in predecessor_refs:
            for successor_ref in successor_refs:
                item = copy.deepcopy(edge)
                item["predecessorRef"] = predecessor_ref
                item["successorRef"] = successor_ref
                if (
                    edge["successorRef"] == operations["workUnitId"]
                    and successor_ref == other["workUnitId"]
                ):
                    item["reasonRefs"] = sorted(
                        reason
                        for reason in edge["reasonRefs"]
                        if reason in other_dependency_path_refs
                    )
                constraints.append(item)
    spec["happensBefore"] = constraints
    spec["minimality"]["nonRemovableRefs"] = sorted(
        [item["roleInstanceId"] for item in spec["roleInstances"]]
        + [item["workUnitId"] for item in spec["workUnits"]]
    )


def _omit_work_and_role_obligation(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    work = next(item for item in spec["workUnits"] if len(item["obligationRefs"]) > 1)
    omitted = work["obligationRefs"][0]
    work["obligationRefs"] = work["obligationRefs"][1:]
    work["evidenceOutputs"] = _evidence_for(plan, work["obligationRefs"])
    role_ref = next(
        ref
        for ref in work["roleInstanceRefs"]
        if omitted
        in next(
            role["obligationRefs"]
            for role in spec["roleInstances"]
            if role["roleInstanceId"] == ref
        )
    )
    role = next(
        item for item in spec["roleInstances"] if item["roleInstanceId"] == role_ref
    )
    role["obligationRefs"] = [
        item for item in role["obligationRefs"] if item != omitted
    ]


def _flip_evaluation(plan: dict[str, Any]) -> None:
    evaluation = plan["spec"]["applicabilityEvaluations"][0]
    evaluation["result"] = "FALSE" if evaluation["result"] != "FALSE" else "TRUE"


def _omit_path(plan: dict[str, Any]) -> None:
    plan["spec"]["impactPaths"] = plan["spec"]["impactPaths"][1:]


def _erase_unknown(plan: dict[str, Any]) -> None:
    if not plan["spec"]["unresolvedRefs"]:
        raise BuildError("Unknown-erasure source Plan has no unresolved refs")
    plan["spec"]["unresolvedRefs"] = []


def _mismatch_input_root(plan: dict[str, Any]) -> None:
    plan["spec"]["snapshotRef"]["digest"] = "sha256:" + "0" * 64


def _mismatch_owner(plan: dict[str, Any]) -> None:
    plan["metadata"]["ownerRef"] = "owner:plan-verification-mismatch"


def _forge_obligation_projection(plan: dict[str, Any]) -> None:
    obligation = plan["spec"]["obligations"][0]
    obligation["requiredEvidence"] = sorted(
        [*obligation["requiredEvidence"], "evidence:forged-contract-duty"]
    )


def _mismatch_plan_status(plan: dict[str, Any]) -> None:
    plan["spec"]["status"] = "guarded_unresolved"


def _collapse_authority(plan: dict[str, Any]) -> None:
    quality = next(
        item
        for item in plan["spec"]["roleInstances"]
        if item["roleDefinitionRef"] == "role:quality-qualification"
    )
    quality["principalRef"] = "principal:procurement-agent"


def _mismatch_mission(plan: dict[str, Any]) -> None:
    plan["spec"]["roleInstances"][0]["mission"] = (
        "perform an unregistered mission outside the selected role contract"
    )


def _forge_responsibility(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    work = spec["workUnits"][0]
    existing = set(work["roleInstanceRefs"])
    unrelated = next(
        role["roleInstanceId"]
        for role in spec["roleInstances"]
        if role["roleInstanceId"] not in existing
        and not set(role["obligationRefs"]).intersection(work["obligationRefs"])
    )
    work["roleInstanceRefs"] = [*work["roleInstanceRefs"], unrelated]


def _omit_evidence(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    obligation_by_id = {
        item["obligationId"]: item for item in spec["obligations"]
    }
    work = next(item for item in spec["workUnits"] if item["evidenceOutputs"])
    required = sorted(
        {
            evidence
            for ref in work["obligationRefs"]
            for evidence in obligation_by_id[ref]["requiredEvidence"]
        }
    )
    omitted = next(item for item in required if item in work["evidenceOutputs"])
    work["evidenceOutputs"] = [
        item for item in work["evidenceOutputs"] if item != omitted
    ]
    if not work["evidenceOutputs"]:
        work["evidenceOutputs"] = ["evidence:present-but-insufficient"]


def _reverse_order(plan: dict[str, Any]) -> None:
    edge = plan["spec"]["happensBefore"][0]
    edge["predecessorRef"], edge["successorRef"] = (
        edge["successorRef"],
        edge["predecessorRef"],
    )


def _remove_quality_operations_order(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    role_by_id = {
        item["roleInstanceId"]: item["roleDefinitionRef"]
        for item in spec["roleInstances"]
    }
    roles_by_work = {
        work["workUnitId"]: {
            role_by_id[role_ref] for role_ref in work["roleInstanceRefs"]
        }
        for work in spec["workUnits"]
    }
    before = len(spec["happensBefore"])
    spec["happensBefore"] = [
        edge
        for edge in spec["happensBefore"]
        if not (
            "role:quality-qualification" in roles_by_work[edge["predecessorRef"]]
            and "role:operations-continuity"
            in roles_by_work[edge["successorRef"]]
        )
    ]
    if len(spec["happensBefore"]) != before - 1:
        raise BuildError("expected exactly one SC-008 quality->operations order")


def _add_order_cycle(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    role_by_id = {
        item["roleInstanceId"]: item["roleDefinitionRef"]
        for item in spec["roleInstances"]
    }
    work_by_role = {
        role_by_id[role_ref]: work["workUnitId"]
        for work in spec["workUnits"]
        for role_ref in work["roleInstanceRefs"]
    }
    extra = copy.deepcopy(spec["happensBefore"][0])
    extra["predecessorRef"] = work_by_role["role:operations-continuity"]
    extra["successorRef"] = work_by_role["role:quality-qualification"]
    spec["happensBefore"].append(extra)


def _forge_decision_ledger(plan: dict[str, Any]) -> None:
    decision = plan["spec"]["decisions"][0]
    decision["disposition"] = (
        "included" if decision["disposition"] != "included" else "excluded"
    )


def _add_unsupported_work(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    extra = copy.deepcopy(spec["workUnits"][0])
    extra["workUnitId"] = (
        "urn:oac:fixture:work-unit:"
        "9da825466b518fd991fb6b966680b8ca57b550302779e8bae41a1ecc118a5379"
    )
    extra["obligationRefs"] = [
        "urn:oac:fixture:obligation:"
        "cdd6abdf9cb1dbe8eeb9bc26fb81e6583da16fcd00b9efefe293924b3b715d4a"
    ]
    extra["evidenceOutputs"] = [
        "evidence:fixture:4e28d3b3f0aeb09ad81df5a34ebdcfd0"
    ]
    spec["workUnits"].append(extra)
    spec["minimality"]["nonRemovableRefs"] = sorted(
        [item["roleInstanceId"] for item in spec["roleInstances"]]
        + [item["workUnitId"] for item in spec["workUnits"]]
    )


def _duplicate_required_role_coverage(plan: dict[str, Any]) -> None:
    """Duplicate one valid RoleInstance without changing WorkUnit coverage."""

    spec = plan["spec"]
    role = copy.deepcopy(spec["roleInstances"][0])
    role["roleInstanceId"] = "urn:oac:fixture:role-instance:duplicate-coverage"
    spec["roleInstances"].append(role)
    for work in spec["workUnits"]:
        if set(work["obligationRefs"]).intersection(role["obligationRefs"]):
            work["roleInstanceRefs"] = sorted(
                [*work["roleInstanceRefs"], role["roleInstanceId"]]
            )
    spec["minimality"]["nonRemovableRefs"] = sorted(
        [item["roleInstanceId"] for item in spec["roleInstances"]]
        + [item["workUnitId"] for item in spec["workUnits"]]
    )


def _forge_accountable_binding(plan: dict[str, Any]) -> None:
    spec = plan["spec"]
    work = spec["workUnits"][0]
    work["accountableRoleInstanceRef"] = next(
        role["roleInstanceId"]
        for role in spec["roleInstances"]
        if role["roleInstanceId"] not in work["roleInstanceRefs"]
        and not set(role["obligationRefs"]).intersection(work["obligationRefs"])
    )


def _forge_role_qualification_refs(plan: dict[str, Any]) -> None:
    plan["spec"]["roleInstances"][0]["qualificationRefs"] = [
        "qualification:forged-but-well-formed"
    ]


def _forge_order_reason_refs(plan: dict[str, Any]) -> None:
    plan["spec"]["happensBefore"][0]["reasonRefs"] = [
        "reason-ref:forged-but-well-formed"
    ]


def _add_dangling_order_endpoint(plan: dict[str, Any]) -> None:
    edge = copy.deepcopy(plan["spec"]["happensBefore"][0])
    edge["predecessorRef"] = "urn:oac:fixture:work-unit:dangling"
    edge["reasonRefs"] = ["reason-ref:dangling-endpoint"]
    plan["spec"]["happensBefore"].append(edge)


def _duplicate_order_edge(plan: dict[str, Any]) -> None:
    plan["spec"]["happensBefore"].append(
        copy.deepcopy(plan["spec"]["happensBefore"][0])
    )


def _omit_minimality_considered_sets(plan: dict[str, Any]) -> None:
    minimality = plan["spec"]["minimality"]
    minimality["consideredRoleRefs"] = minimality["consideredRoleRefs"][1:]
    minimality["consideredPrincipalRefs"] = minimality[
        "consideredPrincipalRefs"
    ][1:]


def _omit_minimality_nonremovable_ref(plan: dict[str, Any]) -> None:
    minimality = plan["spec"]["minimality"]
    if len(minimality["nonRemovableRefs"]) < 2:
        raise BuildError("minimality non-removable source has no removable probe ref")
    minimality["nonRemovableRefs"] = minimality["nonRemovableRefs"][1:]


def _set_role_effect(plan: dict[str, Any]) -> None:
    plan["spec"]["roleInstances"][0]["effectCeiling"] = "external_effect"


def _set_work_effect(plan: dict[str, Any]) -> None:
    plan["spec"]["workUnits"][0]["effectCeiling"] = "external_effect"


def _replace_selected_principal(plan: dict[str, Any], selected_ref: str) -> None:
    alternative_ref = "principal:operations-z-active-alternative"
    instance = next(
        item
        for item in plan["spec"]["roleInstances"]
        if item["roleDefinitionRef"] == "role:operations-continuity"
    )
    if instance["principalRef"] != alternative_ref:
        raise BuildError("root-coherent compiler did not select the active alternative")
    instance["principalRef"] = selected_ref
    decision_by_subject = {
        item["subjectRef"]: item for item in plan["spec"]["decisions"]
    }
    selected = decision_by_subject[selected_ref]
    selected["disposition"] = "included"
    selected["reasonCodes"] = ["PRINCIPAL_SELECTED_QUALIFIED"]
    alternative = decision_by_subject[alternative_ref]
    alternative["disposition"] = "excluded"
    alternative["reasonCodes"] = ["PRINCIPAL_NOT_SELECTED"]


def _forge_nonauthority_decision(plan: dict[str, Any], subject_ref: str) -> None:
    decision = next(
        item for item in plan["spec"]["decisions"] if item["subjectRef"] == subject_ref
    )
    if decision["inputClass"] == "retracted":
        decision["disposition"] = "unresolved"
        decision["reasonCodes"] = ["CANDIDATE_EDGE_NOT_AUTHORITY"]
    elif decision["inputClass"] in {"candidate", "disputed"}:
        decision["disposition"] = "excluded"
        decision["reasonCodes"] = ["RETRACTED_SOURCE_EXCLUDED"]
    else:
        raise BuildError("non-authority decision transform received admitted input")


TRANSFORMS = {
    "reseal-plan": lambda plan: None,
    "split-operations-work": _split_operations_work,
    "mismatch-input-root": _mismatch_input_root,
    "mismatch-owner": _mismatch_owner,
    "forge-obligation-projection": _forge_obligation_projection,
    "omit-work-and-role-obligation": _omit_work_and_role_obligation,
    "flip-first-evaluation-result": _flip_evaluation,
    "omit-first-impact-path": _omit_path,
    "erase-unresolved-refs": _erase_unknown,
    "mismatch-plan-status": _mismatch_plan_status,
    "bind-quality-to-procurement-principal": _collapse_authority,
    "mismatch-role-mission": _mismatch_mission,
    "add-noncontributing-role-to-work": _forge_responsibility,
    "omit-required-evidence": _omit_evidence,
    "reverse-first-order": _reverse_order,
    "remove-quality-operations-order": _remove_quality_operations_order,
    "add-order-cycle": _add_order_cycle,
    "forge-decision-ledger": _forge_decision_ledger,
    "add-unsupported-work-unit": _add_unsupported_work,
    "duplicate-required-role-coverage": _duplicate_required_role_coverage,
    "forge-accountable-binding": _forge_accountable_binding,
    "forge-role-qualification-refs": _forge_role_qualification_refs,
    "forge-order-reason-refs": _forge_order_reason_refs,
    "add-dangling-order-endpoint": _add_dangling_order_endpoint,
    "duplicate-order-edge": _duplicate_order_edge,
    "omit-minimality-considered-sets": _omit_minimality_considered_sets,
    "omit-minimality-nonremovable-ref": _omit_minimality_nonremovable_ref,
    "set-role-effect-raw": _set_role_effect,
    "set-work-effect-raw": _set_work_effect,
}


def _artifact_descriptor(
    path: Path,
    raw: bytes,
    *,
    source_path: str,
    source_raw: bytes,
    transform: str,
) -> dict[str, Any]:
    return {
        "path": path.relative_to(OUTPUT).as_posix(),
        "rawSha256": _digest(raw),
        "sizeBytes": len(raw),
        "sourcePath": source_path,
        "sourceRawSha256": _digest(source_raw),
        "sourceSizeBytes": len(source_raw),
        "transform": transform,
    }


def _write_bound_copy(
    destination: Path, source: Path, *, transform: str = "exact-copy/v1"
) -> dict[str, Any]:
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return _artifact_descriptor(
        destination,
        raw,
        source_path=source.relative_to(ROOT).as_posix(),
        source_raw=raw,
        transform=transform,
    )


def _principal_authority_variant(
    snapshot: dict[str, Any], *, admission: str, status: str
) -> None:
    principals = snapshot["spec"]["principals"]
    selected = next(
        item
        for item in principals
        if item["principalId"] == "principal:operations-agent"
    )
    selected["admissionStatus"] = admission
    selected["status"] = status
    alternative = copy.deepcopy(selected)
    alternative["principalId"] = "principal:operations-z-active-alternative"
    alternative["admissionStatus"] = "admitted"
    alternative["status"] = "active"
    principals.append(alternative)


def _nonauthority_edge_variant(
    snapshot: dict[str, Any], *, admission: str, edge_id: str
) -> None:
    edge = copy.deepcopy(snapshot["spec"]["dependencyEdges"][0])
    edge["edgeId"] = edge_id
    edge["admissionStatus"] = admission
    snapshot["spec"]["dependencyEdges"].append(edge)


def _transform_snapshot_resource(
    value: dict[str, Any], transform: str
) -> bytes | None:
    if transform == "principal-inactive-with-active-alternative":
        _principal_authority_variant(value, admission="admitted", status="inactive")
    elif transform == "principal-candidate-with-active-alternative":
        _principal_authority_variant(value, admission="candidate", status="active")
    elif transform == "add-candidate-decision-edge":
        _nonauthority_edge_variant(
            value,
            admission="candidate",
            edge_id="edge:fixture:candidate-decision-input",
        )
    elif transform == "add-retracted-decision-edge":
        _nonauthority_edge_variant(
            value,
            admission="retracted",
            edge_id="edge:fixture:retracted-decision-input",
        )
    else:
        return None
    return _seal_map(value)


def _write_materialized_resource(
    destination: Path, source: Path, *, transform: str = "materialize"
) -> tuple[dict[str, Any], bytes]:
    """Materialize omission defaults without changing the legacy resource digest.

    Historical fixtures were sealed over the registered typed projection. The
    stdio-v3 boundary requires raw-map identity, so every default entering that
    historic digest must be explicit in the frozen capsule bytes.
    """

    source_raw = source.read_bytes()
    resource = parse_resource(source_raw, verify_digest=True)
    value = resource.model_dump(mode="json", by_alias=True)
    raw = rfc8785.dumps(value) + b"\n"
    if transform == "tamper-claimed-digest":
        raw = _tamper_claimed_digest(raw)
    elif transform != "materialize":
        transformed = _transform_snapshot_resource(value, transform)
        if transformed is None:
            raise BuildError(f"unknown root-resource transform: {transform}")
        raw = transformed
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return (
        _artifact_descriptor(
            destination,
            raw,
            source_path=source.relative_to(ROOT).as_posix(),
            source_raw=source_raw,
            transform=f"typed-projection-{transform}-jcs/v1",
        ),
        raw,
    )


def _plan_partition(plan: Mapping[str, Any]) -> list[list[str]]:
    return sorted(
        (sorted(item["obligationRefs"]) for item in plan["spec"]["workUnits"]),
        key=lambda item: tuple(item),
    )


def _obligation_order_reachability(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    work_obligations = {
        item["workUnitId"]: set(item["obligationRefs"])
        for item in plan["spec"]["workUnits"]
    }
    successors = {work_ref: set() for work_ref in work_obligations}
    for edge in plan["spec"]["happensBefore"]:
        if (
            edge["predecessorRef"] in successors
            and edge["successorRef"] in successors
        ):
            successors[edge["predecessorRef"]].add(edge["successorRef"])
    reachable: set[tuple[str, str]] = set()
    for start in successors:
        pending = list(successors[start])
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(successors[current])
        for end in seen:
            reachable.update(
                (left, right)
                for left in work_obligations[start]
                for right in work_obligations[end]
            )
    return [
        {
            "predecessorObligationRef": predecessor,
            "successorObligationRef": successor,
        }
        for predecessor, successor in sorted(reachable)
    ]


def _compile_root_coherent_plan(snapshot_raw: bytes, change_raw: bytes) -> dict[str, Any]:
    snapshot = parse_resource(snapshot_raw, verify_digest=True)
    change = parse_resource(change_raw, verify_digest=True)
    plan = compile_supplier_change(snapshot, change)
    value = plan.model_dump(mode="json", by_alias=True)
    if not isinstance(value, dict):  # pragma: no cover - pydantic contract guard
        raise BuildError("root-coherent compiler returned a non-object Plan")
    return value


def _forge_inactive_principal_plan(plan: dict[str, Any]) -> None:
    _replace_selected_principal(plan, "principal:operations-agent")


def _forge_candidate_principal_plan(plan: dict[str, Any]) -> None:
    _replace_selected_principal(plan, "principal:operations-agent")


def _forge_candidate_edge_decision(plan: dict[str, Any]) -> None:
    _forge_nonauthority_decision(plan, "edge:fixture:candidate-decision-input")


def _forge_retracted_edge_decision(plan: dict[str, Any]) -> None:
    _forge_nonauthority_decision(plan, "edge:fixture:retracted-decision-input")


ROOT_COHERENT_PLAN_TRANSFORMS = {
    "compile-root-and-bind-inactive-principal": _forge_inactive_principal_plan,
    "compile-root-and-bind-candidate-principal": _forge_candidate_principal_plan,
    "compile-root-and-forge-candidate-decision": _forge_candidate_edge_decision,
    "compile-root-and-forge-retracted-decision": _forge_retracted_edge_decision,
}


def build() -> dict[str, Any]:
    recipe = _load(RECIPE_PATH)
    requirements = _load(REQUIREMENT_PATH)
    capability = _load(CAPABILITY_PATH)
    _verify_detached(recipe, "recipeSetDigest", "recipe set")
    _verify_detached(requirements, "requirementSetDigest", "RequirementSet")
    _verify_detached(capability, "capabilitySetDigest", "capability set")
    recipe_case_ids = [item["caseId"] for item in recipe["cases"]]
    requirement_case_ids = [item["caseId"] for item in requirements["cases"]]
    if recipe_case_ids != requirement_case_ids or len(set(recipe_case_ids)) != len(
        recipe_case_ids
    ):
        raise BuildError("recipe and RequirementSet case order must be identical and unique")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    contract_descriptors = []
    for relative in CONTRACT_PATHS:
        contract_descriptors.append(
            _write_bound_copy(OUTPUT / "contracts" / relative, ROOT / relative)
        )
    capability_descriptor = _write_bound_copy(
        OUTPUT / "capability" / CAPABILITY_PATH.name, CAPABILITY_PATH
    )
    requirement_descriptor = _write_bound_copy(
        OUTPUT / "requirements" / REQUIREMENT_PATH.name, REQUIREMENT_PATH
    )
    recipe_descriptor = _write_bound_copy(
        OUTPUT / "generator" / RECIPE_PATH.name, RECIPE_PATH
    )
    generator_descriptor = _write_bound_copy(
        OUTPUT / "generator" / Path(__file__).name,
        Path(__file__),
    )

    root_descriptors: dict[str, dict[str, Any]] = {}
    root_raw: dict[str, tuple[bytes, bytes]] = {}
    for root_id, sources in recipe["rootSets"].items():
        if not isinstance(sources, dict):
            raise BuildError(f"root set {root_id} must be an object")
        snapshot_source = ROOT / sources["snapshot"]
        change_source = ROOT / sources["change"]
        snapshot_destination = OUTPUT / "artifacts" / root_id / snapshot_source.name
        change_destination = OUTPUT / "artifacts" / root_id / change_source.name
        snapshot_descriptor, snapshot_raw = _write_materialized_resource(
            snapshot_destination,
            snapshot_source,
            transform=sources.get("snapshotTransform", "materialize"),
        )
        change_descriptor, change_raw = _write_materialized_resource(
            change_destination,
            change_source,
            transform=sources.get("changeTransform", "materialize"),
        )
        root_descriptors[root_id] = {
            "snapshot": snapshot_descriptor,
            "change": change_descriptor,
        }
        root_raw[root_id] = (snapshot_raw, change_raw)

    case_descriptors: list[dict[str, Any]] = []
    materialized_plans: dict[str, dict[str, Any]] = {}
    for item in recipe["cases"]:
        case_id = item["caseId"]
        source_path = ROOT / item["sourcePlan"]
        source_raw = source_path.read_bytes()
        transform = item["transform"]
        root_snapshot_raw, root_change_raw = root_raw[item["rootSet"]]
        if transform in ROOT_COHERENT_PLAN_TRANSFORMS:
            plan = _compile_root_coherent_plan(root_snapshot_raw, root_change_raw)
        else:
            plan = json.loads(source_raw)
        if not isinstance(plan, dict):
            raise BuildError(f"{case_id} source Plan must be an object")
        if transform == "exact-copy":
            raw = source_raw
        elif transform == "tamper-plan-digest":
            raw = _tamper_claimed_digest(source_raw)
        else:
            if transform == "set-nonzero-effect-raw":
                plan["spec"]["effectCeiling"] = "external_effect"
            elif transform in ROOT_COHERENT_PLAN_TRANSFORMS:
                ROOT_COHERENT_PLAN_TRANSFORMS[transform](plan)
            else:
                try:
                    TRANSFORMS[transform](plan)
                except KeyError as exc:
                    raise BuildError(f"unknown transform: {transform}") from exc
            _with_fixture_identity(plan)
            raw = _seal_map(plan)
        destination = OUTPUT / "artifacts" / "plans" / f"{case_id}.plan.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        descriptor = _artifact_descriptor(
            destination,
            raw,
            source_path=item["sourcePlan"],
            source_raw=source_raw,
            transform=f"{transform}/v1",
        )
        case_descriptors.append(
            {
                "caseId": case_id,
                "rootSet": item["rootSet"],
                "mutationClass": item["mutationClass"],
                "plan": descriptor,
            }
        )
        materialized = json.loads(raw)
        if isinstance(materialized, dict):
            materialized_plans[case_id] = materialized

    base = materialized_plans["PV-POS-SC008-BASE"]
    split = materialized_plans["PV-POS-SC008-SPLIT"]
    if base["spec"]["obligations"] != split["spec"]["obligations"]:
        raise BuildError("plural Plans changed the topology-free obligation contract")
    base_partition = _plan_partition(base)
    split_partition = _plan_partition(split)
    if base_partition == split_partition:
        raise BuildError("plural Plans do not have materially distinct WorkUnit partitions")
    base_reachability = _obligation_order_reachability(base)
    split_reachability = _obligation_order_reachability(split)
    plurality = {
        "apiVersion": "oac.ctk.plurality-witness/v0alpha1",
        "kind": "PluralityWitness",
        "relationCoordinate": capability["capabilitySetId"],
        "snapshotDigest": json.loads(root_raw["SC-008"][0])["digest"],
        "changeDigest": json.loads(root_raw["SC-008"][1])["digest"],
        "planADigest": base["digest"],
        "planBDigest": split["digest"],
        "normalization": "work-id-erased-obligation-partition-and-order-reachability/v1",
        "materialDifferences": [
            "obligation-to-work-unit-partition",
            "plan-induced-obligation-order-reachability",
        ],
        "planAObligationPartition": base_partition,
        "planBObligationPartition": split_partition,
        "planAInducedObligationOrderReachability": base_reachability,
        "planBInducedObligationOrderReachability": split_reachability,
        "sameTopologyFreeObligations": True,
        "sameInducedObligationOrderReachability": (
            base_reachability == split_reachability
        ),
        "claimLimit": "shared acceptance-set membership is not behavioral equivalence",
    }
    plurality_raw = rfc8785.dumps(plurality) + b"\n"
    plurality_path = OUTPUT / "plurality-witness.json"
    plurality_path.write_bytes(plurality_raw)
    plurality_descriptor = _artifact_descriptor(
        plurality_path,
        plurality_raw,
        source_path=RECIPE_PATH.relative_to(ROOT).as_posix(),
        source_raw=RECIPE_PATH.read_bytes(),
        transform="plurality-projection/v1",
    )

    expected_dependencies = {
        "oac-contract": "0.3.0a0",
        "pydantic": "2.13.4",
        "rfc8785": "0.1.4",
    }
    actual_dependencies = {
        name: importlib.metadata.version(name) for name in expected_dependencies
    }
    if actual_dependencies != expected_dependencies or not (
        (3, 12) <= sys.version_info[:2] < (3, 15)
    ):
        raise BuildError("generator runtime does not satisfy the frozen runtime contract")
    runtime = {
        "runtimeContract": {
            "pythonRequires": ">=3.12,<3.15",
            "dependencies": expected_dependencies,
        },
        "generatorRawSha256": generator_descriptor["rawSha256"],
        "recipeSetDigest": recipe["recipeSetDigest"],
        "seed": recipe["seed"],
    }
    runtime_raw = rfc8785.dumps(runtime) + b"\n"
    runtime_path = OUTPUT / "generator" / "runtime-ledger.json"
    runtime_path.write_bytes(runtime_raw)
    runtime_descriptor = _artifact_descriptor(
        runtime_path,
        runtime_raw,
        source_path=RECIPE_PATH.relative_to(ROOT).as_posix(),
        source_raw=RECIPE_PATH.read_bytes(),
        transform="runtime-ledger/v1",
    )

    capsule = {
        "apiVersion": "oac.plan-verification.capsule/v0alpha1",
        "kind": "PlanVerificationPortabilityCapsule",
        "capsuleId": "oac.supplier.plan-verification/v0.1-seed-1",
        "protocolVersion": "oac.ctk.stdio/v3",
        "relationCoordinate": capability["capabilitySetId"],
        "digestAlgorithm": "sha256-jcs-detached/v1",
        "contracts": contract_descriptors,
        "capabilitySet": capability_descriptor,
        "requirementSet": requirement_descriptor,
        "generator": {
            "recipeSet": recipe_descriptor,
            "source": generator_descriptor,
            "runtime": runtime_descriptor,
        },
        "rootSets": root_descriptors,
        "cases": case_descriptors,
        "pluralityWitness": plurality_descriptor,
    }
    capsule["capsuleDigest"] = _digest(rfc8785.dumps(capsule))
    capsule_raw = rfc8785.dumps(capsule) + b"\n"
    (OUTPUT / "capsule.json").write_bytes(capsule_raw)
    return capsule


def check() -> dict[str, Any]:
    """Validate the published capsule's internal ledger and external digest anchor."""

    harness_path = ROOT / "scripts/check_plan_verification_parity.py"
    specification = importlib.util.spec_from_file_location(
        "_oac_frozen_plan_capsule_check",
        harness_path,
    )
    if specification is None or specification.loader is None:
        raise BuildError("cannot load the published capsule admission gate")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
        capsule, _, _, _ = module._load_capsule(verify_source_drift=False)
    except Exception as exc:
        raise BuildError(f"published capsule admission failed: {exc}") from exc
    finally:
        sys.modules.pop(specification.name, None)
    return capsule


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        parser.error(
            "the published seed-1 capsule is immutable; mint a new coordinate for new bytes"
        )
    try:
        capsule = check()
    except (BuildError, KeyError, TypeError, ValueError) as exc:
        print(f"plan verification capsule build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "capsuleDigest": capsule["capsuleDigest"],
                "cases": len(capsule["cases"]),
                "output": OUTPUT.relative_to(ROOT).as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
