"""Deterministic, mechanics-only benchmark runner for the exploratory Supplier cases.

The runner deliberately scores only project-authored candidate role sets.  It is useful for
counterfactual engineering and matched-policy comparison, not for a quality, enterprise-value, or
Ground Truth claim.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rfc8785

from .canonical import parse_resource
from .compiler import compile_supplier_change
from .models import (
    PROPAGATING_RELATIONS,
    AdmissionStatus,
    OrganizationSnapshot,
    OrgChangeCase,
    SemanticChangeSet,
)
from .verifier import verify_plan

if TYPE_CHECKING:
    from .annotation import AnnotationLedger
    from .annotation_models import AnnotationBenchmarkClaimSpec, QualificationBasis
    from .models import ResourceRef


RUN_MANIFEST_VERSION = "1.2"
REFERENCE_ID = "oac-reference-compiler"
REFERENCE_VERSION = "0.3.0a0"

type BenchmarkRow = tuple[
    str,
    OrgChangeCase,
    OrganizationSnapshot,
    SemanticChangeSet,
    dict[str, Any],
]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected an object: {path}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def build_file_closure(root: Path, paths: Iterable[Path]) -> dict[str, Any]:
    """Bind a repository-relative file closure to exact raw bytes.

    The closure digest is SHA-256 over the RFC 8785 encoding of the ordered
    ``[{path, sha256}]`` entries.  Publishing the entries as well as the digest
    makes omitted benchmark inputs and implementation files auditable.
    """

    resolved_root = root.resolve()
    by_relative_path: dict[str, Path] = {}
    for path in paths:
        resolved_path = path.resolve()
        try:
            relative_path = resolved_path.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"closure path is outside repository root: {path}") from exc
        by_relative_path[relative_path] = resolved_path

    entries = [
        {
            "path": relative_path,
            "sha256": _sha256_bytes(by_relative_path[relative_path].read_bytes()),
        }
        for relative_path in sorted(by_relative_path)
    ]
    return {
        "algorithm": "sha256",
        "canonicalization": "RFC8785(entries[path,sha256])",
        "entries": entries,
        "digest": _sha256_bytes(rfc8785.dumps(entries)),
    }


def _seal_mapping(value: dict[str, Any]) -> dict[str, Any]:
    projection = dict(value)
    projection.pop("digest", None)
    return {**projection, "digest": _sha256_bytes(rfc8785.dumps(projection))}


def _load_inputs(
    root: Path,
) -> tuple[
    dict[str, Any],
    list[BenchmarkRow],
    list[Path],
]:
    matched_path = root / "benchmark" / "matched-inputs.json"
    matched = _json(matched_path)
    projection = dict(matched)
    claimed = projection.pop("digest")
    if claimed != _sha256_bytes(rfc8785.dumps(projection)):
        raise ValueError("matched input digest does not verify")

    snapshot_paths = sorted(
        (root / "profiles" / "supplier-change" / "inputs").glob("*.snapshot.json")
    )
    snapshots: dict[tuple[str, str], OrganizationSnapshot] = {}
    for path in snapshot_paths:
        value = parse_resource(path.read_bytes(), verify_digest=True)
        if not isinstance(value, OrganizationSnapshot) or value.digest is None:
            raise TypeError(f"not an OrganizationSnapshot: {path}")
        snapshots[(value.metadata.id, value.digest)] = value

    change_paths = sorted(
        (root / "profiles" / "supplier-change" / "inputs").glob("SC-*.change.json")
    )
    changes: dict[tuple[str, str], SemanticChangeSet] = {}
    for path in change_paths:
        value = parse_resource(path.read_bytes(), verify_digest=True)
        if not isinstance(value, SemanticChangeSet) or value.digest is None:
            raise TypeError(f"not a SemanticChangeSet: {path}")
        changes[(value.metadata.id, value.digest)] = value

    rows: list[BenchmarkRow] = []
    input_files = [matched_path, *snapshot_paths, *change_paths]
    for item in matched["cases"]:
        case_path = root / item["caseRef"]
        case = parse_resource(case_path.read_bytes(), verify_digest=True)
        if not isinstance(case, OrgChangeCase) or case.digest != item["caseDigest"]:
            raise ValueError(f"case root mismatch: {item['caseId']}")
        snapshot = snapshots[(case.spec.snapshot_ref.resource_id, case.spec.snapshot_ref.digest)]
        change = changes[(case.spec.change_ref.resource_id, case.spec.change_ref.digest)]
        annotation_path = (
            root
            / "profiles"
            / "supplier-change"
            / "annotations"
            / case_path.name
        )
        annotation = _json(annotation_path)
        input_files.extend((case_path, annotation_path))
        rows.append((item["caseId"], case, snapshot, change, annotation))
    return matched, rows, input_files


def _subject_owner(snapshot: OrganizationSnapshot, change: SemanticChangeSet) -> set[str]:
    for node in snapshot.spec.nodes:
        if (
            node.node_id == change.spec.subject_ref
            and node.admission_status is AdmissionStatus.ADMITTED
            and node.owner_role_ref is not None
        ):
            return {node.owner_role_ref}
    return set()


def _graph_only_roles(snapshot: OrganizationSnapshot, change: SemanticChangeSet) -> set[str]:
    nodes = {node.node_id: node for node in snapshot.spec.nodes}
    outgoing: dict[str, list[str]] = {}
    for edge in snapshot.spec.dependency_edges:
        if (
            edge.admission_status is AdmissionStatus.ADMITTED
            and edge.relation_type in PROPAGATING_RELATIONS
        ):
            outgoing.setdefault(edge.source_ref, []).append(edge.target_ref)
    seen = {change.spec.subject_ref}
    queue = deque([change.spec.subject_ref])
    while queue:
        for target in sorted(outgoing.get(queue.popleft(), [])):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return {
        node.owner_role_ref
        for node_id in seen
        if (node := nodes.get(node_id)) is not None
        and node.admission_status is AdmissionStatus.ADMITTED
        and node.owner_role_ref is not None
    }


def _score_case(expected: set[str], selected: set[str]) -> dict[str, Any]:
    return {
        "selectedRoleRefs": sorted(selected),
        "candidateRoleRefs": sorted(expected),
        "truePositive": len(expected & selected),
        "falsePositive": len(selected - expected),
        "falseNegative": len(expected - selected),
        "exactCandidateRoleSetMatch": selected == expected,
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(item["score"]["truePositive"] for item in results)
    fp = sum(item["score"]["falsePositive"] for item in results)
    fn = sum(item["score"]["falseNegative"] for item in results)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "caseCount": len(results),
        "exactCandidateRoleSetMatches": sum(
            item["score"]["exactCandidateRoleSetMatch"] for item in results
        ),
        "microCounts": {"truePositive": tp, "falsePositive": fp, "falseNegative": fn},
        "microPrecision": round(precision, 6),
        "microRecall": round(recall, 6),
    }


def build_run_manifests(root: Path) -> dict[str, dict[str, Any]]:
    """Build byte-repeatable manifests for one reference and three transparent policies."""

    matched, rows, input_files = _load_inputs(root)
    source_files = [
        root / "src" / "oac" / name
        for name in (
            "__init__.py",
            "models.py",
            "registry.py",
            "canonical.py",
            "applicability.py",
            "supplier.py",
            "compiler.py",
            "verifier.py",
            "benchmark.py",
        )
    ]
    runner_files = [root / "scripts" / "run_benchmark.py"]
    environment_files = [root / "pyproject.toml", root / "uv.lock"]
    common_implementation_files = [*source_files, *runner_files, *environment_files]
    systems: dict[str, dict[str, Any]] = {
        REFERENCE_ID: {
            "version": REFERENCE_VERSION,
            "implementationFiles": common_implementation_files,
            "configurationRef": None,
        }
    }
    for path in sorted((root / "benchmark" / "baselines").glob("*.input.json")):
        definition = _json(path)
        systems[definition["baselineId"]] = {
            "version": definition["baselineInputVersion"],
            "implementationFiles": [*common_implementation_files, path],
            "configurationRef": path.relative_to(root).as_posix(),
            "definition": definition,
        }

    input_closure = build_file_closure(root, input_files)
    fixture_closure = build_file_closure(
        root,
        (path for path in input_files if path != root / "benchmark" / "matched-inputs.json"),
    )
    manifests: dict[str, dict[str, Any]] = {}
    for system_id, system in sorted(systems.items()):
        results: list[dict[str, Any]] = []
        for case_id, case, snapshot, change, annotation in rows:
            expected = set(annotation["candidateConstraints"]["admissibleRoleRefs"])
            details: dict[str, Any] = {}
            if system_id == REFERENCE_ID:
                plan = compile_supplier_change(snapshot, change)
                certificate = verify_plan(snapshot, change, plan)
                selected = {item.role_definition_ref for item in plan.spec.role_instances}
                selected_types = {item.obligation_type for item in plan.spec.obligations}
                candidate = annotation["candidateConstraints"]
                required_types = set(candidate["requiredObligationTypes"])
                forbidden_types = set(candidate.get("forbiddenObligationTypes", []))
                candidate_verdict = candidate.get(
                    "requiredVerdictUntilDiscovery", "ACCEPT"
                )
                details = {
                    "planDigest": plan.digest,
                    "certificateDigest": certificate.digest,
                    "verdict": certificate.spec.verdict.value,
                    "unresolvedCount": len(plan.spec.unresolved_refs),
                    "contractAgreement": {
                        "selectedObligationTypes": sorted(selected_types),
                        "candidateRequiredObligationTypes": sorted(required_types),
                        "requiredObligationTypesCovered": required_types.issubset(
                            selected_types
                        ),
                        "forbiddenObligationTypeViolations": sorted(
                            selected_types & forbidden_types
                        ),
                        "candidateVerdict": candidate_verdict,
                        "candidateVerdictMatch": (
                            certificate.spec.verdict.value == candidate_verdict
                        ),
                    },
                }
            elif system_id == "initiator-only":
                selected = _subject_owner(snapshot, change)
            elif system_id == "fixed-team":
                selected = set(system["definition"]["policy"]["selectedRoleRefs"])
            elif system_id == "graph-only":
                selected = _graph_only_roles(snapshot, change)
            else:  # pragma: no cover - systems are closed by the frozen definitions above
                raise ValueError(f"unknown benchmark system: {system_id}")
            score = _score_case(expected, selected)
            result = {
                "caseId": case_id,
                "caseDigest": case.digest,
                "snapshotDigest": snapshot.digest,
                "changeDigest": change.digest,
                "score": score,
                **details,
            }
            result["outputDigest"] = _sha256_bytes(rfc8785.dumps(result))
            results.append(result)

        implementation_closure = build_file_closure(root, system["implementationFiles"])
        metrics = _aggregate(results)
        if system_id == REFERENCE_ID:
            agreements = [item["contractAgreement"] for item in results]
            type_coverage_case_ids = [
                result["caseId"]
                for result, agreement in zip(results, agreements, strict=True)
                if agreement["requiredObligationTypesCovered"]
            ]
            verdict_match_case_ids = [
                result["caseId"]
                for result, agreement in zip(results, agreements, strict=True)
                if agreement["candidateVerdictMatch"]
            ]
            metrics.update(
                {
                    "requiredObligationTypeCoverageMatches": len(
                        type_coverage_case_ids
                    ),
                    "requiredObligationTypeCoverageCaseIds": type_coverage_case_ids,
                    "requiredObligationTypeGapCaseIds": [
                        result["caseId"]
                        for result in results
                        if result["caseId"] not in type_coverage_case_ids
                    ],
                    "candidateVerdictMatches": len(verdict_match_case_ids),
                    "candidateVerdictMatchCaseIds": verdict_match_case_ids,
                    "forbiddenObligationTypeViolationCases": sum(
                        bool(item["forbiddenObligationTypeViolations"])
                        for item in agreements
                    ),
                }
            )
        manifest = {
            "runManifestVersion": RUN_MANIFEST_VERSION,
            "runId": f"supplier-change-exploratory-10/{system_id}",
            "systemId": system_id,
            "systemVersion": system["version"],
            "evaluationClass": "mechanics-only-project-authored-candidate-labels",
            "claimLimit": (
                "Scores describe agreement with exploratory project-authored candidate role sets; "
                "they are not Ground Truth, enterprise effectiveness, or superiority evidence."
            ),
            "inputSetRef": "benchmark/matched-inputs.json",
            "inputSetDigest": matched["digest"],
            "inputClosure": input_closure,
            "inputClosureDigest": input_closure["digest"],
            "fixtureSetDigest": fixture_closure["digest"],
            "implementationClosure": implementation_closure,
            "implementationDigest": implementation_closure["digest"],
            "configurationRef": system["configurationRef"],
            "results": results,
            "metrics": metrics,
        }
        manifests[system_id] = _seal_mapping(manifest)
    return manifests


def qualify_annotation_benchmark(
    ledger: AnnotationLedger, claim_ref: ResourceRef, *,
    required_basis: QualificationBasis = "GOVERNANCE_ATTESTED_HUMAN",
) -> AnnotationBenchmarkClaimSpec:
    """Check a new-profile claim against the host's current annotation ledger.

    This never relabels retained exploratory cases. The host must restore the
    ledger against an independently pinned archive root before trusting it.
    A revoked source or review invalidates all dependent claims transitively.
    The default requires governance-attested human qualification. Synthetic
    callers must explicitly request the controlled-local basis.
    """
    from .annotation import AnnotationLedger
    from .models import ResourceRef

    if not isinstance(ledger, AnnotationLedger) or not isinstance(claim_ref, ResourceRef):
        raise TypeError("current AnnotationLedger and exact ResourceRef required")
    return ledger.qualify_benchmark(claim_ref, required_basis=required_basis)
