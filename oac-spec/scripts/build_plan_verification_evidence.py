#!/usr/bin/env python3
"""Build or verify the v0alpha1 Plan-verification evidence manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/plan-verification-portability/v0.1-seed-1"
MANIFEST_PATH = EXPERIMENT / "evidence-manifest.json"
RULE_MUTATION_PATH = EXPERIMENT / "rule-mutation-summary.json"
PUBLISHED_MANIFEST_DIGEST = (
    "sha256:f0ccc77267d4680c27a2d6c7dc802e307754afbd34dfcab5fad4360c4036a8e9"
)


class EvidenceFailure(RuntimeError):
    """Evidence bytes or semantic closures do not match the admitted claim."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise EvidenceFailure(f"{path} must contain an object")
    return value


def _entry(path: Path) -> dict[str, Any]:
    status = path.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise EvidenceFailure(f"evidence input must be a single-link file: {path}")
    raw = path.read_bytes()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "rawSha256": _digest(raw),
        "sizeBytes": len(raw),
    }


def _verify_detached(value: dict[str, Any], field: str, label: str) -> None:
    projection = {key: item for key, item in value.items() if key != field}
    if value.get(field) != _digest(rfc8785.dumps(projection)):
        raise EvidenceFailure(f"{label} detached digest mismatch")


def _portable_projection(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary[key]
        for key in (
            "capsuleDigest",
            "capabilitySetDigest",
            "requirementSetDigest",
            "protocolVersion",
            "requiredCases",
            "python",
            "go",
            "observations",
            "disagreements",
            "mutants",
            "oracleNonEvasion",
            "independence",
            "claim",
            "pythonObservations",
            "goObservations",
        )
    }


def _validate_historical_source_closure(value: object) -> dict[str, Any]:
    """Admit the byte ledger captured by this versioned evidence coordinate.

    The entries intentionally are not compared to a later repository HEAD.
    Their bytes are revision-scoped evidence and are anchored by the published
    outer manifest digest below.
    """

    if not isinstance(value, dict) or set(value) != {
        "algorithm",
        "entries",
        "closureDigest",
    }:
        raise EvidenceFailure("historical source closure is not closed")
    entries = value.get("entries")
    if (
        value.get("algorithm") != "sha256-jcs-entry-ledger/v1"
        or not isinstance(entries, list)
        or not entries
        or value.get("closureDigest") != _digest(rfc8785.dumps(entries))
    ):
        raise EvidenceFailure("historical source closure digest mismatch")
    paths = [item.get("path") for item in entries if isinstance(item, dict)]
    if (
        len(paths) != len(entries)
        or not all(isinstance(path, str) and path for path in paths)
        or paths != sorted(paths)
        or len(paths) != len(set(paths))
        or any(
            set(item) != {"path", "rawSha256", "sizeBytes"}
            or not isinstance(item["rawSha256"], str)
            or not item["rawSha256"].startswith("sha256:")
            or not isinstance(item["sizeBytes"], int)
            or item["sizeBytes"] <= 0
            for item in entries
        )
    ):
        raise EvidenceFailure("historical source closure entries are invalid")
    return value


def build_manifest(*, source_closure: dict[str, Any] | None = None) -> dict[str, Any]:
    if source_closure is None:
        if not MANIFEST_PATH.is_file():
            raise EvidenceFailure(
                "the frozen coordinate is missing; create a successor coordinate instead"
            )
        published = _load(MANIFEST_PATH)
        if published.get("manifestDigest") != PUBLISHED_MANIFEST_DIGEST:
            raise EvidenceFailure("published evidence-manifest identity drift")
        source_closure = _validate_historical_source_closure(
            published.get("sourceClosure")
        )
    else:
        source_closure = _validate_historical_source_closure(source_closure)
    capsule_path = EXPERIMENT / "capsule.json"
    summary_path = EXPERIMENT / "parity-summary.json"
    installed_summary_path = EXPERIMENT / "replay/installed-parity-summary.json"
    ledger_path = EXPERIMENT / "replay/installed-material-ledger.json"
    for path in (
        capsule_path,
        summary_path,
        installed_summary_path,
        ledger_path,
        RULE_MUTATION_PATH,
    ):
        if not path.is_file():
            raise EvidenceFailure(f"required evidence is missing: {path}")
    capsule = _load(capsule_path)
    summary = _load(summary_path)
    installed_summary = _load(installed_summary_path)
    ledger = _load(ledger_path)
    rule_mutations = _load(RULE_MUTATION_PATH)
    requirements = _load(
        ROOT / "ctk/bundles/plan-verification-v01-seed-1.requirement-set.json"
    )
    _verify_detached(capsule, "capsuleDigest", "capsule")
    _verify_detached(summary, "summaryDigest", "source summary")
    _verify_detached(installed_summary, "summaryDigest", "installed summary")
    _verify_detached(ledger, "ledgerDigest", "installed ledger")
    _verify_detached(rule_mutations, "summaryDigest", "rule mutation summary")
    _verify_detached(requirements, "requirementSetDigest", "RequirementSet")
    if summary["capsuleDigest"] != capsule["capsuleDigest"]:
        raise EvidenceFailure("source summary is not bound to the capsule")
    if installed_summary["capsuleDigest"] != capsule["capsuleDigest"]:
        raise EvidenceFailure("installed summary is not bound to the capsule")
    if ledger["sourceSummaryDigest"] != summary["summaryDigest"]:
        raise EvidenceFailure("installed ledger binds a stale source summary")
    if ledger["installedSummaryDigest"] != installed_summary["summaryDigest"]:
        raise EvidenceFailure("installed ledger binds a stale installed summary")
    portable = _portable_projection(summary)
    if portable != _portable_projection(installed_summary):
        raise EvidenceFailure("source and installed portable observations differ")
    if ledger["portableProjectionDigest"] != _digest(rfc8785.dumps(portable)):
        raise EvidenceFailure("installed ledger portable projection digest mismatch")
    if (
        rule_mutations["capsuleDigest"] != capsule["capsuleDigest"]
        or rule_mutations["capabilitySetDigest"] != summary["capabilitySetDigest"]
        or rule_mutations["requirementSetDigest"] != summary["requirementSetDigest"]
    ):
        raise EvidenceFailure("rule mutation summary is not bound to the frozen coordinate")
    if (
        rule_mutations["requiredCases"] != 39
        or rule_mutations["baseline"] != {"required": 39, "passed": 39, "failed": 0}
        or rule_mutations["sourceMutantsRequired"] != 14
        or rule_mutations["sourceMutantsKilled"] != 14
        or len(rule_mutations["mutants"]) != 14
        or any(
            not item["killed"]
            or item["failed"] != 1
            or item["failedCaseIds"] != item["dedicatedKillCases"]
            or not all(item["dedicatedKillVector"].values())
            for item in rule_mutations["mutants"]
        )
        or rule_mutations["excludedFromKillDenominator"]
        != [
            {
                "rule": "selected RoleDefinition admissionStatus is admitted",
                "classification": "EQUIVALENT_OR_UNREACHABLE_UNDER_SEED1",
                "reason": (
                    "the Supplier seed-1 derivation requires an affected owner role to be admitted, "
                    "and fixed-Plan qualification also binds every RoleInstance roleDefinitionRef "
                    "to the derived obligation requiredRoleRef; no admitted coherent root isolates "
                    "this conjunct from obligation projection and role matching"
                ),
            }
        ]
    ):
        raise EvidenceFailure("isolated source-rule mutation evidence does not satisfy the frozen gate")
    if (
        summary["requiredCases"] != 39
        or summary["python"]["passed"] != 39
        or summary["go"]["passed"] != 39
        or summary["observations"]["scoredIndeterminate"] != 0
        or summary["observations"]["exact"] != 36
        or summary["observations"]["permittedDiagnosticVariance"] != 3
        or len(summary["mutants"]) != 3
        or not all(item["killed"] for item in summary["mutants"])
    ):
        raise EvidenceFailure("source matrix does not satisfy the frozen evidence gate")
    outcome_counts = {
        "acceptedTopologies": sum(
            item.get("sutStatus") == "COMPLETED" and item.get("verdict") == "ACCEPT"
            for item in requirements["cases"]
        ),
        "provisionalControls": sum(
            item.get("sutStatus") == "COMPLETED"
            and item.get("verdict") == "PROVISIONAL"
            for item in requirements["cases"]
        ),
        "unknownControls": sum(
            item.get("sutStatus") == "COMPLETED"
            and item.get("verdict") == "UNKNOWN"
            for item in requirements["cases"]
        ),
        "completedRejectControls": sum(
            item.get("sutStatus") == "COMPLETED" and item.get("verdict") == "REJECT"
            for item in requirements["cases"]
        ),
        "admissionErrorControls": sum(
            item.get("sutStatus") == "ERROR" for item in requirements["cases"]
        ),
    }
    if outcome_counts != {
        "acceptedTopologies": 2,
        "provisionalControls": 1,
        "unknownControls": 1,
        "completedRejectControls": 28,
        "admissionErrorControls": 7,
    }:
        raise EvidenceFailure("RequirementSet outcome lattice differs from the frozen gate")

    evidence_paths = {
        capsule_path,
        summary_path,
        installed_summary_path,
        ledger_path,
        RULE_MUTATION_PATH,
        EXPERIMENT / "plurality-witness.json",
        ROOT / "ctk/capabilities/plan-verification-v01-seed-1.capability-set.json",
        ROOT / "ctk/bundles/plan-verification-v01-seed-1.requirement-set.json",
        ROOT / "ctk/generators/plan-verification-v01-seed-1.recipes.json",
        *EXPERIMENT.glob("disagreements/*.json"),
    }
    evidence = [_entry(path) for path in sorted(evidence_paths)]
    manifest = {
        "apiVersion": "oac.plan-verification.evidence/v0alpha1",
        "kind": "PlanVerificationEvidenceManifest",
        "coordinate": "oac.supplier.plan-verification/v0.1-seed-1",
        "digestAlgorithm": "sha256-jcs-detached/v1",
        "capsuleDigest": capsule["capsuleDigest"],
        "capabilitySetDigest": summary["capabilitySetDigest"],
        "requirementSetDigest": summary["requirementSetDigest"],
        "sourceSummaryDigest": summary["summaryDigest"],
        "installedSummaryDigest": installed_summary["summaryDigest"],
        "installedLedgerDigest": ledger["ledgerDigest"],
        "portableProjectionDigest": ledger["portableProjectionDigest"],
        "sourceClosure": source_closure,
        "evidenceArtifacts": evidence,
        "evidenceClosureDigest": _digest(rfc8785.dumps(evidence)),
        "matrix": {
            "requiredCases": 39,
            **outcome_counts,
            "negativeControls": 35,
            "pythonPassed": 39,
            "goPassed": 39,
            "exactCrossImplementationObservations": 36,
            "permittedDiagnosticVariance": 3,
            "scoredIndeterminate": 0,
            "mutantsKilled": 3,
            "isolatedSourceMutantsKilled": 14,
            "sourceInstalledPortableProjectionEqual": True,
        },
        "plurality": {
            "rootSet": "SC-008",
            "materialDifferences": [
                "obligation-to-WorkUnit partition",
                "plan-induced obligation-order reachability",
            ],
            "claim": "two structurally distinct Plans are members of the same bounded mandatory-contract acceptance set",
            "permittedStrengthening": "accepted Plans may impose different additional ordering beyond the shared mandatory relation",
            "notClaimed": "behavioral, cost, trace, outcome, or workflow equivalence",
        },
        "mutationEvidence": {
            "aggregateKilledClasses": [
                "accept-all",
                "reason-erasure",
                "aggregate-selected-relation-rule-erasure",
            ],
            "isolatedSourceMutantsRequired": 14,
            "isolatedSourceMutantsKilled": 14,
            "isolatedSourceMutationSummaryDigest": rule_mutations["summaryDigest"],
            "excludedEquivalentOrUnreachableRules": 1,
            "claimLimit": (
                "the enumerated exact Go source-rule erasures are killed by one dedicated frozen case each; "
                "this is bounded mutation evidence, not verifier completeness"
            ),
            "nextGate": "independent maintainer implementation plus hidden-profile mutation families",
        },
        "disagreementPolicy": {
            "majorityRule": "prohibited",
            "referenceWins": False,
            "resolvedPermittedVariance": 3,
            "openScoredDisagreements": 0,
        },
        "independenceClass": {
            "python": "reference implementation",
            "go": "internal same-repository implementation",
            "organizationalIndependence": False,
        },
        "provenanceClass": "digest-bound self-attested source and isolated-process replay",
        "claimCeiling": "bounded plural fixed-Plan acceptance relation on 39 frozen selected cases",
        "exclusions": [
            "no separately maintained or clean-room implementation",
            "no complete Supplier Profile coverage",
            "no hidden or held-out suite",
            "no clean Git archive reproduction",
            "no cryptographic execution provenance",
            "one RoleDefinition-admission conjunct is equivalent or unreachable under the frozen seed-1 root and is excluded from the kill denominator",
            "no general effect-ceiling domain mutant because the current Plan schema admits only zero_effect",
            "no enterprise correctness, ROI, or production safety evidence",
            "no behavioral-equivalence or universal-standard claim",
        ],
    }
    manifest["manifestDigest"] = _digest(rfc8785.dumps(manifest))
    return manifest


def check() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        raise EvidenceFailure("evidence manifest is missing")
    raw = MANIFEST_PATH.read_bytes()
    actual = json.loads(raw)
    if raw != rfc8785.dumps(actual) + b"\n":
        raise EvidenceFailure("evidence manifest must be exact JCS plus LF")
    if actual.get("manifestDigest") != PUBLISHED_MANIFEST_DIGEST:
        raise EvidenceFailure("published evidence-manifest identity drift")
    historical_source_closure = _validate_historical_source_closure(
        actual.get("sourceClosure")
    )
    expected = build_manifest(source_closure=historical_source_closure)
    if actual != expected:
        raise EvidenceFailure("evidence manifest differs from its frozen coordinate")
    return actual


def main() -> int:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--write", action="store_true")
    args = arguments.parse_args()
    try:
        if args.write:
            raise EvidenceFailure(
                "the published coordinate is immutable; create a versioned successor"
            )
        manifest = check()
    except (EvidenceFailure, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Plan-verification evidence check failed: {exc}", file=sys.stderr)
        return 2
    raw = MANIFEST_PATH.read_bytes()
    print(
        json.dumps(
            {
                "manifestDigest": manifest["manifestDigest"],
                "manifestRawSha256": _digest(raw),
                **manifest["matrix"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
