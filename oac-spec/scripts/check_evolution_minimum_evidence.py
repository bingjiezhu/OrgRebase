#!/usr/bin/env python3
"""Verify the versioned, source-bound Spec 009 minimum-profile evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

import rfc8785

from oac.evolution import (
    DEMAND_ROOT_DOMAIN,
    EVOLUTION_ROOT_DOMAIN,
    EXECUTION_ROOT_DOMAIN,
    OUTCOME_ROOT_DOMAIN,
    PLAN_ROOT_DOMAIN,
    SOURCE_ROOT_DOMAIN,
    project_demand_root,
    project_execution_root,
    project_outcome_root,
    project_plan_root,
    project_source_root,
)
from oac.models import ResourceRef

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "experiments/evolution-minimum/v0.1-seed-7"
PREDECESSOR_PATH = "experiments/evolution-minimum/v0.1-seed-6/evidence-manifest.json"
PREDECESSOR_DIGEST = "sha256:bfcc0e1d7c55dd1f7045950c8c3861c184c5b643c613cac4346e69def0d4ab80"
PREDECESSOR_RAW_DIGEST = "sha256:95fc8b1faeedd259e861d6f19fba5e6ca9ea31064f490ced24aac787eb79f8ee"
MANIFEST_PATH = EVIDENCE_ROOT / "evidence-manifest.json"


class EvolutionEvidenceError(RuntimeError):
    """The versioned evidence coordinate is incomplete or has drifted."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _entry(relative: str) -> dict[str, object]:
    path = ROOT / relative
    try:
        path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    except ValueError as exc:
        raise EvolutionEvidenceError(f"evidence path escapes repository: {relative}") from exc
    status = path.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or path.is_symlink():
        raise EvolutionEvidenceError(f"evidence input is not a single-link file: {relative}")
    raw = path.read_bytes()
    return {"path": relative, "rawSha256": _digest(raw), "sizeBytes": len(raw)}


def _closure(paths: tuple[str, ...]) -> dict[str, object]:
    entries = [_entry(path) for path in sorted(paths)]
    return {
        "algorithm": "sha256-jcs-entry-ledger/v1",
        "entries": entries,
        "closureDigest": _digest(rfc8785.dumps(entries)),
    }


def _root_vector() -> dict[str, object]:
    path = ROOT / "tck/fixtures/evolution/root-vector.json"
    value = json.loads(path.read_bytes())
    refs = {name: ResourceRef.model_validate(item) for name, item in value["refs"].items()}
    observed = {
        "source": project_source_root(refs["snapshot"], (refs["sourceAdmission"],)),
        "demand": project_demand_root(refs["demand"], (refs["change"],)),
        "plan": project_plan_root(refs["plan"], refs["planCertificate"]),
        "execution": project_execution_root(
            refs["runtimeBinding"],
            refs["runtimeBundle"],
            refs["executionReceipt"],
            (refs["executionEvidence"],),
        ),
    }
    observed["outcome"] = project_outcome_root(
        observed["source"],
        observed["demand"],
        observed["plan"],
        observed["execution"],
        refs["outcomeCertificate"],
    )
    if observed != value.get("expectedRoots"):
        raise EvolutionEvidenceError("reference implementation differs from root vector")
    return {
        "artifact": _entry("tck/fixtures/evolution/root-vector.json"),
        "expectedRoots": observed,
        "rootSetDigest": _digest(rfc8785.dumps(observed)),
    }


def _mutation_inventory() -> dict[str, object]:
    value = json.loads((ROOT / "tck/fixtures/evolution/mutations.json").read_bytes())
    mutations = value.get("mutations")
    if not isinstance(mutations, list) or len(mutations) != 9:
        raise EvolutionEvidenceError("mutation inventory is not the frozen nine-case set")
    identifiers = [item.get("id") for item in mutations if isinstance(item, dict)]
    if len(identifiers) != 9 or len(identifiers) != len(set(identifiers)):
        raise EvolutionEvidenceError("mutation identifiers are incomplete or duplicated")
    return {
        "artifact": _entry("tck/fixtures/evolution/mutations.json"),
        "caseIds": identifiers,
        "inventoryDigest": _digest(rfc8785.dumps(mutations)),
    }


def _historical_manifest(
    relative: str, expected_digest: str, expected_raw_digest: str
) -> tuple[dict[str, Any], dict[str, object]]:
    entry = _entry(relative)
    raw = (ROOT / relative).read_bytes()
    previous = json.loads(raw)
    if (
        not isinstance(previous, dict)
        or entry["rawSha256"] != expected_raw_digest
        or raw != rfc8785.dumps(previous) + b"\n"
        or previous.get("digest") != expected_digest
        or _digest(
            rfc8785.dumps({key: value for key, value in previous.items() if key != "digest"})
        )
        != expected_digest
    ):
        raise EvolutionEvidenceError("historical predecessor identity mismatch")
    return previous, entry


def _historical_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [
        entry
        for name in ("sourceClosure", "schemaClosure", "contractClosure")
        for entry in manifest[name]["entries"]
    ]
    entries += [manifest["rootVector"]["artifact"], manifest["mutationInventory"]["artifact"]]
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        raise EvolutionEvidenceError("historical material paths are duplicated")
    return entries


def _preserved_ancestors(previous: dict[str, Any]) -> list[dict[str, Any]]:
    ancestors = []
    seen = {PREDECESSOR_PATH}
    while (link := previous.get("predecessor")) is not None:
        relative = link["manifest"]["path"]
        if relative in seen:
            raise EvolutionEvidenceError("historical predecessor chain contains a cycle")
        seen.add(relative)
        previous, manifest_entry = _historical_manifest(
            relative, link["digest"], link["manifest"]["rawSha256"]
        )
        if manifest_entry != link["manifest"]:
            raise EvolutionEvidenceError("historical ancestor manifest entry mismatch")
        if capture := link.get("capturedManifest"):
            _, observed_capture = _historical_manifest(
                capture["path"], link["digest"], link["manifest"]["rawSha256"]
            )
            if observed_capture != capture:
                raise EvolutionEvidenceError("historical ancestor manifest capture mismatch")
        expected = {entry["path"]: entry for entry in _historical_entries(previous)}
        captures = link["materials"]
        paths = [entry["originalPath"] for entry in captures]
        if len(paths) != len(set(paths)) or set(paths) != set(expected):
            raise EvolutionEvidenceError("historical ancestor material set mismatch")
        observed_materials = []
        for entry in captures:
            observed = _entry(entry["path"])
            original = expected[entry["originalPath"]]
            if (
                observed["rawSha256"] != original["rawSha256"]
                or observed["sizeBytes"] != original["sizeBytes"]
                or observed["rawSha256"] != entry["rawSha256"]
                or observed["sizeBytes"] != entry["sizeBytes"]
            ):
                raise EvolutionEvidenceError("historical ancestor material mismatch")
            observed_materials.append({"originalPath": entry["originalPath"], **observed})
        ancestors.append(
            {"manifest": manifest_entry, "digest": link["digest"], "materials": observed_materials}
        )
    return ancestors


def _predecessor() -> dict[str, Any]:
    previous, original_manifest = _historical_manifest(
        PREDECESSOR_PATH, PREDECESSOR_DIGEST, PREDECESSOR_RAW_DIGEST
    )
    captured_manifest_path = (
        (EVIDENCE_ROOT / "predecessor-manifest.json").relative_to(ROOT).as_posix()
    )
    captured_previous, captured_manifest = _historical_manifest(
        captured_manifest_path, PREDECESSOR_DIGEST, PREDECESSOR_RAW_DIGEST
    )
    if captured_previous != previous:
        raise EvolutionEvidenceError("captured predecessor manifest differs from original")
    captured = []
    for entry in _historical_entries(previous):
        relative = (
            (EVIDENCE_ROOT / "predecessor-materials" / entry["path"]).relative_to(ROOT).as_posix()
        )
        observed = _entry(relative)
        if (observed["rawSha256"], observed["sizeBytes"]) != (
            entry["rawSha256"],
            entry["sizeBytes"],
        ):
            raise EvolutionEvidenceError("historical predecessor material mismatch")
        captured.append({"originalPath": entry["path"], **observed})
    return {
        "manifest": original_manifest,
        "capturedManifest": captured_manifest,
        "digest": PREDECESSOR_DIGEST,
        "materials": captured,
        "ancestors": _preserved_ancestors(previous),
        "verificationClass": "EXACT_HISTORICAL_MATERIAL_PRESERVATION",
    }


def build_manifest() -> dict[str, Any]:
    source_closure = _closure(
        (
            "scripts/check_evolution_minimum_evidence.py",
            "scripts/export_schemas.py",
            "ctk/runner/src/oac_ctk_runner/contracts.py",
            *(
                path.relative_to(ROOT).as_posix()
                for path in sorted((ROOT / "src/oac").rglob("*.py"))
            ),
        )
    )
    schema_closure = _closure(
        (
            "schemas/OrganizationalDemand.schema.json",
            "schemas/OutcomeCertificate.schema.json",
            "schemas/SourceAdmissionReceipt.schema.json",
            "schemas/evolution-semantic-validation-rules.json",
            "schemas/kind-registry.json",
            "schemas/reason-code-registry.json",
        )
    )
    contract_closure = _closure(
        (
            "specs/009-proof-carrying-evolution-minimum-profile/plan.md",
            "specs/009-proof-carrying-evolution-minimum-profile/research.md",
            "specs/009-proof-carrying-evolution-minimum-profile/spec.md",
            "specs/009-proof-carrying-evolution-minimum-profile/tasks.md",
            "tests/test_evolution.py",
            "tests/test_evolution_evidence.py",
            "docs/validation/HISTORICAL-EVIDENCE-VERSIONING.md",
            "docs/validation/EVOLUTION-SEED3-PUBLICATION.md",
            "specs/009-proof-carrying-evolution-minimum-profile/raw-admission-successor.md",
            "specs/009-proof-carrying-evolution-minimum-profile/raw-admission-protocol-closure.md",
            "specs/009-proof-carrying-evolution-minimum-profile/raw-cli-acceptance-closure.md",
            "tests/test_cli_evolution_admission.py",
            "tests/test_disposable_outcome_profile.py",
            "tests/fixtures/evolution/default-shared-observation.json",
            "tests/fixtures/evolution/README.md",
            "specs/009-proof-carrying-evolution-minimum-profile/default-provenance-compatibility.md",
            "tests/test_cli_raw_acceptance.py",
            "tests/test_raw_json_depth.py",
            "tests/test_benchmark_publication.py",
        )
    )
    manifest: dict[str, Any] = {
        "apiVersion": "oac.evolution.evidence/v0alpha3",
        "kind": "EvolutionMinimumEvidenceManifest",
        "coordinate": "oac.evolution.minimum/v0.1-seed-7",
        "serialization": "rfc8785+jcs+lf/v1",
        "sourceState": {
            "baseRevision": "e301952ae08986e6398a636f04f883036bc88c8d",
            "workingTree": "uncommitted",
            "cleanArchiveReplayed": False,
        },
        "profile": "oac.evolution.minimum/v0.1",
        "coreKinds": [
            "OrganizationalDemand",
            "OutcomeCertificate",
            "SourceAdmissionReceipt",
        ],
        "extensionRefKinds": [
            "EvolutionProposal",
            "ExecutionReceipt",
            "OutcomeObservation",
            "ProcedureContract",
        ],
        "rootDomains": {
            "S": SOURCE_ROOT_DOMAIN,
            "D": DEMAND_ROOT_DOMAIN,
            "P": PLAN_ROOT_DOMAIN,
            "X": EXECUTION_ROOT_DOMAIN,
            "O": OUTCOME_ROOT_DOMAIN,
            "E": EVOLUTION_ROOT_DOMAIN,
        },
        "predecessor": _predecessor(),
        "sourceClosure": source_closure,
        "schemaClosure": schema_closure,
        "contractClosure": contract_closure,
        "rootVector": _root_vector(),
        "mutationInventory": _mutation_inventory(),
        "verification": {
            "class": "source-bound-root-vector-and-negative-inventory/v1",
            "referenceTestCases": 7,
            "rootVectorRecomputed": True,
            "executionProvenanceEmbedded": False,
        },
        "claim": {
            "ceiling": "single-reference-implementation-source-and-root-vector-closure",
            "scope": "three core kinds, six root domains, one public root vector, and nine named controls",
            "exclusions": [
                "independent implementation agreement",
                "runtime execution provenance",
                "autonomous evolution",
                "enterprise outcome effectiveness",
                "complete OAC conformance",
                "production readiness",
                "clean archive reproducibility",
            ],
        },
        "digestAlgorithm": "sha256-jcs-detached/v1",
    }
    manifest["digest"] = _digest(rfc8785.dumps(manifest))
    return manifest


def check(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise EvolutionEvidenceError("evolution evidence manifest is missing")
    raw = path.read_bytes()
    actual = json.loads(raw)
    if not isinstance(actual, dict) or raw != rfc8785.dumps(actual) + b"\n":
        raise EvolutionEvidenceError("evolution evidence must be exact JCS plus LF")
    detached = {key: item for key, item in actual.items() if key != "digest"}
    if actual.get("digest") != _digest(rfc8785.dumps(detached)):
        raise EvolutionEvidenceError("evolution evidence detached digest mismatch")
    if actual != build_manifest():
        raise EvolutionEvidenceError("evolution evidence does not equal its versioned materials")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", dest="print_manifest")
    parser.add_argument(
        "--publish-new",
        action="store_true",
        help="Write the current successor coordinate once; never replace existing evidence",
    )
    args = parser.parse_args()
    try:
        if args.publish_new:
            payload = rfc8785.dumps(build_manifest()) + b"\n"
            MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
            with MANIFEST_PATH.open("xb") as stream:
                stream.write(payload)
        manifest = check()
    except (EvolutionEvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Evolution evidence check failed: {exc}", file=sys.stderr)
        return 2
    if args.print_manifest:
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    else:
        print(manifest["digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
