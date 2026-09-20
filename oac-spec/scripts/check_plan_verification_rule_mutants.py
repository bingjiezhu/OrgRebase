#!/usr/bin/env python3
"""Kill exact Go source-rule erasures with the frozen Plan-verification matrix.

This is deliberately different from a response postprocessor mutant. Each mutant
copies the Go verifier into an isolated temporary tree, applies exact source
preimages, builds a fresh binary, and executes the complete frozen capsule. A
source preimage that is absent or appears more than once fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
GO_SOURCE = ROOT / "implementations/go-plan-verifier-v01-internal"
HARNESS_SOURCE = ROOT / "scripts/check_plan_verification_parity.py"
OUTPUT = (
    ROOT
    / "experiments/plan-verification-portability/v0.1-seed-1/rule-mutation-summary.json"
)


class MutationGateFailure(RuntimeError):
    """The source mutation gate could not establish its bounded claim."""


@dataclass(frozen=True)
class Replacement:
    relative_path: str
    before: str
    after: str


@dataclass(frozen=True)
class Mutant:
    mutant_id: str
    relation_rule: str
    dedicated_kill_cases: tuple[str, ...]
    replacements: tuple[Replacement, ...]


_QUALIFICATION_BLOCK = (
    '\t\t\tvalid = role.AdmissionStatus == "admitted" && principal.AdmissionStatus == "admitted" && principal.Status == "active" &&\n'
    "\t\t\t\tcontains(principal.EligibleRoleRefs, role.RoleID) && subset(stringSet(role.RequiredQualifications), stringSet(principal.QualificationRefs)) &&\n"
    "\t\t\t\tequalStrings(instance.QualificationRefs, role.RequiredQualifications) && instance.Mission == role.Mission\n"
)

_MINIMALITY_BLOCK = (
    '\tif plan.Spec.Minimality.Level != "inclusion_minimal" || !reflect.DeepEqual(stringSet(plan.Spec.Minimality.NonRemovableRefs), nonRemovable) ||\n'
    "\t\t!reflect.DeepEqual(stringSet(plan.Spec.Minimality.ConsideredRoleRefs), consideredRoles) || !reflect.DeepEqual(stringSet(plan.Spec.Minimality.ConsideredPrincipalRefs), consideredPrincipals) {\n"
    '\t\tadd("PLAN_NOT_MINIMAL")\n'
    "\t}\n"
)

MUTANTS = (
    Mutant(
        "coverage-exactly-once-erasure",
        "every derived obligation is covered by exactly one WorkUnit and RoleInstance",
        ("PV-NEG-DUPLICATE-ROLE-COVERAGE",),
        (
            Replacement(
                "plan_verify.go",
                "\t\tif _, ok := obligations[ref]; !ok || workCount[ref] != 1 || roleCount[ref] != 1 {\n",
                "\t\tif _, ok := obligations[ref]; !ok || workCount[ref] == 0 || roleCount[ref] == 0 {\n",
            ),
        ),
    ),
    Mutant(
        "accountable-binding-erasure",
        "the accountable RoleInstance belongs to and contributes to its WorkUnit",
        ("PV-NEG-ACCOUNTABLE-BINDING",),
        (
            Replacement(
                "plan_verify.go",
                (
                    "\t\tif !contains(work.RoleInstanceRefs, work.AccountableRoleInstanceRef) {\n"
                    '\t\t\tadd("OBLIGATION_UNSATISFIED")\n'
                    "\t\t}\n"
                ),
                "",
            ),
            Replacement(
                "plan_verify.go",
                (
                    "\t\taccountable, ok := roles[work.AccountableRoleInstanceRef]\n"
                    "\t\tif !ok || len(intersection(workObligations, stringSet(accountable.ObligationRefs))) == 0 || !reflect.DeepEqual(workObligations, contributed) {\n"
                    "\t\t\tvalid = false\n"
                    "\t\t}\n"
                ),
                (
                    "\t\tif !reflect.DeepEqual(workObligations, contributed) {\n"
                    "\t\t\tvalid = false\n"
                    "\t\t}\n"
                ),
            ),
        ),
    ),
    Mutant(
        "principal-active-erasure",
        "a selected principal is active",
        ("PV-NEG-INACTIVE-PRINCIPAL-BINDING",),
        (
            Replacement(
                "plan_verify.go",
                _QUALIFICATION_BLOCK,
                (
                    '\t\t\tvalid = role.AdmissionStatus == "admitted" && principal.AdmissionStatus == "admitted" &&\n'
                    "\t\t\t\tcontains(principal.EligibleRoleRefs, role.RoleID) && subset(stringSet(role.RequiredQualifications), stringSet(principal.QualificationRefs)) &&\n"
                    "\t\t\t\tequalStrings(instance.QualificationRefs, role.RequiredQualifications) && instance.Mission == role.Mission\n"
                ),
            ),
        ),
    ),
    Mutant(
        "principal-admission-erasure",
        "a selected principal is admitted",
        ("PV-NEG-NONADMITTED-PRINCIPAL-BINDING",),
        (
            Replacement(
                "plan_verify.go",
                _QUALIFICATION_BLOCK,
                (
                    '\t\t\tvalid = role.AdmissionStatus == "admitted" && principal.Status == "active" &&\n'
                    "\t\t\t\tcontains(principal.EligibleRoleRefs, role.RoleID) && subset(stringSet(role.RequiredQualifications), stringSet(principal.QualificationRefs)) &&\n"
                    "\t\t\t\tequalStrings(instance.QualificationRefs, role.RequiredQualifications) && instance.Mission == role.Mission\n"
                ),
            ),
        ),
    ),
    Mutant(
        "qualification-refs-erasure",
        "RoleInstance qualificationRefs equal the role contract and the principal satisfies it",
        ("PV-NEG-ROLE-QUALIFICATION-REFS",),
        (
            Replacement(
                "plan_verify.go",
                _QUALIFICATION_BLOCK,
                (
                    '\t\t\tvalid = role.AdmissionStatus == "admitted" && principal.AdmissionStatus == "admitted" && principal.Status == "active" &&\n'
                    "\t\t\t\tcontains(principal.EligibleRoleRefs, role.RoleID) && instance.Mission == role.Mission\n"
                ),
            ),
        ),
    ),
    Mutant(
        "order-reason-refs-erasure",
        "each Plan order edge binds the exact derived reasonRefs",
        ("PV-NEG-ORDER-REASON-REFS",),
        (
            Replacement(
                "plan_verify.go",
                (
                    "\t\treasonSet := map[string]struct{}{}\n"
                    "\t\tfor pair := range applicable {\n"
                    "\t\t\tfor _, ref := range expectedReasons[pair][work] {\n"
                    "\t\t\t\treasonSet[ref] = struct{}{}\n"
                    "\t\t\t}\n"
                    "\t\t}\n"
                    "\t\texpectedReasonRefs := sortedKeys(reasonSet)\n"
                    "\t\tif len(applicable) != 0 && reflect.DeepEqual(induced, applicable) && reflect.DeepEqual(edge.ReasonRefs, expectedReasonRefs) {\n"
                ),
                "\t\tif len(applicable) != 0 && reflect.DeepEqual(induced, applicable) {\n",
            ),
        ),
    ),
    Mutant(
        "order-dangling-endpoint-erasure",
        "every Plan order endpoint resolves to a WorkUnit",
        ("PV-NEG-ORDER-DANGLING-ENDPOINT",),
        (
            Replacement(
                "plan_verify.go",
                (
                    "\t\tif !leftOK || !rightOK {\n"
                    '\t\t\tadd("ORDER_CONSTRAINT_INVALID")\n'
                    "\t\t\tcontinue\n"
                    "\t\t}\n"
                ),
                "\t\tif !leftOK || !rightOK {\n\t\t\tcontinue\n\t\t}\n",
            ),
        ),
    ),
    Mutant(
        "order-duplicate-admission-erasure",
        "duplicate Plan order triples fail schema admission",
        ("PV-NEG-ORDER-DUPLICATE-EDGE",),
        (
            Replacement("admit.go", "\torders := map[string]struct{}{}\n", ""),
            Replacement(
                "admit.go",
                (
                    "\t\trelation := order.Relation\n"
                    "\t\tif relation == \"\" {\n"
                    '\t\t\trelation = "must_complete_before"\n'
                    "\t\t}\n"
                    '\t\tkey := order.PredecessorRef + "\\x00" + order.SuccessorRef + "\\x00" + relation\n'
                    "\t\tif _, duplicate := orders[key]; duplicate {\n"
                    '\t\t\treturn errors.New("duplicate plan order constraint")\n'
                    "\t\t}\n"
                    "\t\torders[key] = struct{}{}\n"
                ),
                "",
            ),
        ),
    ),
    Mutant(
        "minimality-considered-sets-erasure",
        "minimality consideredRoleRefs and consideredPrincipalRefs equal the governed sets",
        ("PV-NEG-MINIMALITY-CONSIDERED-SETS",),
        (
            Replacement(
                "plan_verify.go",
                _MINIMALITY_BLOCK,
                (
                    '\tif plan.Spec.Minimality.Level != "inclusion_minimal" || !reflect.DeepEqual(stringSet(plan.Spec.Minimality.NonRemovableRefs), nonRemovable) {\n'
                    '\t\tadd("PLAN_NOT_MINIMAL")\n'
                    "\t}\n"
                ),
            ),
        ),
    ),
    Mutant(
        "minimality-nonremovable-refs-erasure",
        "minimality nonRemovableRefs equal all selected RoleInstances and WorkUnits",
        ("PV-NEG-MINIMALITY-NONREMOVABLE-REFS",),
        (
            Replacement(
                "plan_verify.go",
                _MINIMALITY_BLOCK,
                (
                    '\tif plan.Spec.Minimality.Level != "inclusion_minimal" ||\n'
                    "\t\t!reflect.DeepEqual(stringSet(plan.Spec.Minimality.ConsideredRoleRefs), consideredRoles) || !reflect.DeepEqual(stringSet(plan.Spec.Minimality.ConsideredPrincipalRefs), consideredPrincipals) {\n"
                    '\t\tadd("PLAN_NOT_MINIMAL")\n'
                    "\t}\n"
                ),
            ),
        ),
    ),
    Mutant(
        "role-instance-effect-admission-erasure",
        "RoleInstance effectCeiling is zero_effect at schema admission",
        ("PV-NEG-ROLE-EFFECT-ENUM",),
        (
            Replacement(
                "admit.go",
                (
                    "\t\tif !validID(role.RoleInstanceID) || !validID(role.RoleDefinitionRef) || !validID(role.PrincipalRef) || !validID(role.Mission) || !validStringSetNonEmpty(role.ObligationRefs) || role.QualificationRefs == nil || !validStringSet(role.QualificationRefs, false) || role.EffectCeiling != \"zero_effect\" {\n"
                ),
                (
                    "\t\tif !validID(role.RoleInstanceID) || !validID(role.RoleDefinitionRef) || !validID(role.PrincipalRef) || !validID(role.Mission) || !validStringSetNonEmpty(role.ObligationRefs) || role.QualificationRefs == nil || !validStringSet(role.QualificationRefs, false) {\n"
                ),
            ),
        ),
    ),
    Mutant(
        "work-unit-effect-admission-erasure",
        "WorkUnit effectCeiling is zero_effect at schema admission",
        ("PV-NEG-WORK-EFFECT-ENUM",),
        (
            Replacement(
                "admit.go",
                (
                    "\t\tif !validID(work.WorkUnitID) || !validStringSetNonEmpty(work.RoleInstanceRefs) || !validID(work.AccountableRoleInstanceRef) || !validStringSetNonEmpty(work.ObligationRefs) || !validStringSetNonEmpty(work.EvidenceOutputs) || work.EffectCeiling != \"zero_effect\" {\n"
                ),
                (
                    "\t\tif !validID(work.WorkUnitID) || !validStringSetNonEmpty(work.RoleInstanceRefs) || !validID(work.AccountableRoleInstanceRef) || !validStringSetNonEmpty(work.ObligationRefs) || !validStringSetNonEmpty(work.EvidenceOutputs) {\n"
                ),
            ),
        ),
    ),
    Mutant(
        "candidate-decision-content-erasure",
        "candidate/disputed edge, ImpactRule, and UnknownTransitionDuty decision content",
        ("PV-NEG-CANDIDATE-DECISION-CONTENT",),
        tuple(
            Replacement(
                "plan_verify.go",
                f'\t\tif {name}.AdmissionStatus == "admitted" {{\n\t\t\tcontinue\n\t\t}}\n',
                f'\t\tif {name}.AdmissionStatus != "retracted" {{\n\t\t\tcontinue\n\t\t}}\n',
            )
            for name in ("edge", "rule", "duty")
        ),
    ),
    Mutant(
        "retracted-decision-content-erasure",
        "retracted edge, ImpactRule, and UnknownTransitionDuty decision content",
        ("PV-NEG-RETRACTED-DECISION-CONTENT",),
        tuple(
            Replacement(
                "plan_verify.go",
                f'\t\tif {name}.AdmissionStatus == "admitted" {{\n\t\t\tcontinue\n\t\t}}\n',
                f'\t\tif {name}.AdmissionStatus == "admitted" || {name}.AdmissionStatus == "retracted" {{\n\t\t\tcontinue\n\t\t}}\n',
            )
            for name in ("edge", "rule", "duty")
        ),
    ),
)


def _load_harness() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "_oac_plan_verification_parity",
        HARNESS_SOURCE,
    )
    if specification is None or specification.loader is None:
        raise MutationGateFailure("cannot load Plan-verification parity harness")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _apply_mutant(source: Path, mutant: Mutant) -> list[dict[str, str]]:
    changed: list[dict[str, str]] = []
    original_digests: dict[str, str] = {}
    for replacement in mutant.replacements:
        path = source / replacement.relative_path
        if replacement.relative_path not in original_digests:
            original_digests[replacement.relative_path] = _sha256(path.read_bytes())
        current = path.read_text(encoding="utf-8")
        count = current.count(replacement.before)
        if count != 1:
            raise MutationGateFailure(
                f"{mutant.mutant_id}: {replacement.relative_path} exact preimage count is {count}, expected 1"
            )
        path.write_text(current.replace(replacement.before, replacement.after, 1), encoding="utf-8")
    for relative_path, before_digest in sorted(original_digests.items()):
        changed.append(
            {
                "path": f"implementations/go-plan-verifier-v01-internal/{relative_path}",
                "beforeRawSha256": before_digest,
                "afterRawSha256": _sha256((source / relative_path).read_bytes()),
            }
        )
    return changed


def _build_and_run(
    go: str,
    source: Path,
    harness: ModuleType,
    capsule: dict[str, Any],
    requirements: dict[str, Any],
    materials: dict[str, bytes],
    validator: Any,
    registered_reason_codes: frozenset[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="oac-plan-rule-mutant-build-") as directory:
        temporary = Path(directory)
        binary = temporary / "oac-go-plan-verifier"
        environment = harness._allowed_environment()
        environment["GOCACHE"] = str(temporary / "go-build-cache")
        completed = subprocess.run(
            [go, "build", "-trimpath", "-o", str(binary), "."],
            cwd=source,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
        if completed.returncode:
            raise MutationGateFailure(
                f"Go build failed for {source}: {completed.stderr[:4096]}"
            )
        return harness._run_suite(
            [str(binary)],
            capsule,
            requirements,
            materials,
            validator,
            registered_reason_codes,
            include_observations=False,
        )


def run() -> dict[str, Any]:
    go = shutil.which("go")
    if go is None:
        raise MutationGateFailure("Go is required for the source mutation gate")
    harness = _load_harness()
    capsule, capability, requirements, materials = harness._load_capsule(
        verify_source_drift=False
    )
    validator = harness._schema_validator(capsule, materials)
    registered_reason_codes = harness._registered_reason_codes(capsule, materials)
    case_ids = {item["caseId"] for item in requirements["cases"]}
    expected_kill_cases = {
        case_id for mutant in MUTANTS for case_id in mutant.dedicated_kill_cases
    }
    if missing := expected_kill_cases - case_ids:
        raise MutationGateFailure(f"dedicated kill cases are absent: {sorted(missing)}")

    baseline, _ = _build_and_run(
        go,
        GO_SOURCE,
        harness,
        capsule,
        requirements,
        materials,
        validator,
        registered_reason_codes,
    )
    if baseline["failed"]:
        raise MutationGateFailure("unmutated Go baseline does not satisfy the frozen suite")

    results: list[dict[str, Any]] = []
    for mutant in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"oac-{mutant.mutant_id}-") as directory:
            source = Path(directory) / "go-plan-verifier"
            shutil.copytree(GO_SOURCE, source)
            changed_sources = _apply_mutant(source, mutant)
            mutant_summary, observations = _build_and_run(
                go,
                source,
                harness,
                capsule,
                requirements,
                materials,
                validator,
                registered_reason_codes,
            )
        failed_cases = [
            item["caseId"] for item in observations if item["policy"] == "FAIL"
        ]
        dedicated_results = {
            case_id: case_id in failed_cases for case_id in mutant.dedicated_kill_cases
        }
        if not all(dedicated_results.values()):
            raise MutationGateFailure(
                f"{mutant.mutant_id} survived dedicated cases: {dedicated_results}"
            )
        if failed_cases != list(mutant.dedicated_kill_cases):
            raise MutationGateFailure(
                f"{mutant.mutant_id} is not uniquely killed by its dedicated cases: {failed_cases}"
            )
        if mutant_summary["failed"] != len(mutant.dedicated_kill_cases):
            raise MutationGateFailure(
                f"{mutant.mutant_id} failure count does not equal its dedicated kill count"
            )
        results.append(
            {
                "mutantId": mutant.mutant_id,
                "relationRule": mutant.relation_rule,
                "dedicatedKillCases": list(mutant.dedicated_kill_cases),
                "dedicatedKillVector": dedicated_results,
                "killed": True,
                "required": mutant_summary["required"],
                "passed": mutant_summary["passed"],
                "failed": mutant_summary["failed"],
                "failedCaseIds": failed_cases,
                "changedSources": changed_sources,
            }
        )

    summary: dict[str, Any] = {
        "apiVersion": "oac.plan-verification.rule-mutants/v0alpha1",
        "kind": "PlanVerificationRuleMutationSummary",
        "capsuleDigest": capsule["capsuleDigest"],
        "capabilitySetDigest": capability["capabilitySetDigest"],
        "requirementSetDigest": requirements["requirementSetDigest"],
        "requiredCases": len(capsule["cases"]),
        "baseline": {
            "required": baseline["required"],
            "passed": baseline["passed"],
            "failed": baseline["failed"],
        },
        "sourceMutantsRequired": len(MUTANTS),
        "sourceMutantsKilled": sum(item["killed"] for item in results),
        "mutants": results,
        "excludedFromKillDenominator": [
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
        ],
        "claim": (
            "the frozen seed-1 matrix kills the enumerated exact Go source-rule erasures; "
            "this is bounded mutation evidence, not completeness or implementation independence"
        ),
    }
    summary["summaryDigest"] = _sha256(rfc8785.dumps(summary))
    if summary["sourceMutantsKilled"] != summary["sourceMutantsRequired"]:
        raise MutationGateFailure("not every required source mutant was killed")
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "--write",
        action="store_true",
        help="write the recomputed exact-JCS summary instead of checking the frozen bytes",
    )
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        summary = run()
        expected_raw = rfc8785.dumps(summary) + b"\n"
        if args.write:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_bytes(expected_raw)
        else:
            if OUTPUT.read_bytes() != expected_raw:
                raise MutationGateFailure(
                    "frozen rule-mutation summary differs from the recomputed exact JCS bytes"
                )
    except (
        MutationGateFailure,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"plan verification rule mutation gate failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "capsuleDigest": summary["capsuleDigest"],
                "requiredCases": summary["requiredCases"],
                "sourceMutantsKilled": summary["sourceMutantsKilled"],
                "sourceMutantsRequired": summary["sourceMutantsRequired"],
                "summaryDigest": summary["summaryDigest"],
                "mode": "write" if args.write else "check",
                "output": OUTPUT.relative_to(ROOT).as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
