#!/usr/bin/env python3
"""Build or verify the closed Supplier seed-2 evidence manifest."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SEED_ROOT = ROOT / "experiments/supplier-v02-portability/v0.2-seed-2"
REPLAY_ROOT = SEED_ROOT / "replay"
DISAGREEMENT_ROOT = SEED_ROOT / "disagreements"
SCHEMA_PATH = ROOT / "ctk/schemas/SupplierPortabilityEvidenceManifest.schema.json"
MANIFEST_PATH = SEED_ROOT / "evidence-manifest.json"
INCIDENT_SCHEMA_PATH = ROOT / "ctk/schemas/DisagreementIncident.schema.json"
RESOLUTION_SCHEMA_PATH = ROOT / "ctk/schemas/DisagreementResolution.schema.json"
INSTALLED_LEDGER_SCHEMA_PATH = ROOT / "ctk/schemas/InstalledPythonDistributionLedger.schema.json"
EXPECTED_INCIDENT_IDS = tuple(f"DIS-{index:03d}" for index in range(2, 9))


class EvidenceManifestError(RuntimeError):
    """The evidence root is incomplete, mutable, or internally inconsistent."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        bytes.fromhex(value.removeprefix("sha256:"))
    except ValueError:
        return False
    return True


@cache
def _load_script_module(stem: str) -> ModuleType:
    """Load a repository script without depending on the caller's ``sys.path``."""

    path = ROOT / "scripts" / f"{stem}.py"
    module_name = f"_oac_evidence_{stem}"
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise EvidenceManifestError(f"cannot load repository verifier: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    inserted_paths = [
        search_path
        for search_path in (str(ROOT), str(ROOT / "scripts"))
        if search_path not in sys.path
    ]
    sys.path[:0] = inserted_paths
    try:
        specification.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        for search_path in inserted_paths:
            sys.path.remove(search_path)
    return module


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceManifestError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _loads(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceManifestError(f"invalid JSON: {label}") from exc


def _load_json(path: Path, *, exact_jcs_lf: bool = False) -> dict[str, Any]:
    raw = path.read_bytes()
    value = _loads(raw, str(path))
    if not isinstance(value, dict):
        raise EvidenceManifestError(f"JSON root is not an object: {path}")
    if exact_jcs_lf and raw != rfc8785.dumps(value) + b"\n":
        raise EvidenceManifestError(f"artifact is not exact JCS plus LF: {path}")
    return value


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise EvidenceManifestError(f"artifact escapes repository: {path}") from exc


def _repository_artifact(path: Path) -> dict[str, str]:
    return {"path": _relative(path), "rawSha256": _digest(path.read_bytes())}


def _detached_artifact(path: Path, field: str) -> dict[str, str]:
    value = _load_json(path)
    claimed = value.get(field)
    if not isinstance(claimed, str):
        raise EvidenceManifestError(f"missing detached field {field}: {path}")
    projection = {key: item for key, item in value.items() if key != field}
    if claimed != _digest(rfc8785.dumps(projection)):
        raise EvidenceManifestError(f"detached digest mismatch: {path}")
    return {
        **_repository_artifact(path),
        "detachedField": field,
        "detachedDigest": claimed,
    }


def _source_ledger(root: Path, *, go: bool = False) -> list[dict[str, str]]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if (
            path.name == ".DS_Store"
            or "__pycache__" in path.parts
            or any(part.startswith(".") for part in relative.parts)
            or "tests" in relative.parts
        ):
            continue
        if go:
            if path.name in {"go.mod", "go.sum"} or (
                path.suffix == ".go" and not path.name.endswith("_test.go")
            ):
                paths.append(path)
        elif path.suffix in {".py", ".pyi"} and not (
            path.name.startswith("test_") or path.name.endswith("_test.py")
        ):
            paths.append(path)
    return [
        {"path": path.relative_to(root).as_posix(), "digest": _digest(path.read_bytes())}
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix())
    ]


def _source_closure(root: Path, *, go: bool = False) -> str:
    ledger = _source_ledger(root, go=go)
    if not ledger:
        raise EvidenceManifestError(f"empty production source closure: {root}")
    return _digest(rfc8785.dumps(ledger))


def _admit_capsule_and_expected_observations() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rebuild/admit every capsule byte and derive the public 52-case matrix."""

    builder = _load_script_module("build_supplier_v02_capsule")
    parity = _load_script_module("check_supplier_v02_parity")

    published = builder.check()
    capsule_path = SEED_ROOT / "capsule.json"
    capsule, frozen_cases = parity.load_capsule(
        capsule_path,
        verify_source_drift=False,
    )
    if capsule != published:
        raise EvidenceManifestError(
            "admitted capsule differs from the published immutable capsule"
        )
    _, recipe_set = parity._load_bound_seed2_artifacts(capsule_path, capsule)
    validate_cases = parity.build_validate_cases(frozen_cases)
    generated_cases = parity.generate_cases(recipe_set, frozen_cases)

    expected: list[dict[str, Any]] = []
    for item in validate_cases:
        expectation = item["expect"]
        expected_status = expectation["status"]
        expected_result = (
            expectation["result"]
            if expected_status == "COMPLETED"
            else {
                "sutStatus": expected_status,
                "errorCode": expectation["errorCode"],
            }
        )
        expected.append(
            {
                "caseId": item["caseId"],
                "operation": "validateResource",
                "semanticClass": f"admission-{item['class']}",
                "inputDigest": _digest(
                    rfc8785.dumps(
                        {
                            "expectedKind": item["expectedKind"],
                            "rawBase64": item["rawBase64"],
                        }
                    )
                ),
                "expectedStatus": expected_status,
                "expectedObservationDigest": _digest(rfc8785.dumps(expected_result)),
            }
        )
    for item in frozen_cases:
        expected.append(
            {
                "caseId": item["caseId"],
                "operation": "derive",
                "semanticClass": "frozen-baseline",
                "inputDigest": _digest(item["snapshot"] + b"\x00" + item["change"]),
                "expectedStatus": "COMPLETED",
                "expectedObservationDigest": item["expectedDigest"],
            }
        )
    for item in generated_cases:
        expected.append(
            {
                "caseId": item["caseId"],
                "operation": "derive",
                "semanticClass": item["semanticClass"],
                "inputDigest": item["inputDigest"],
            }
        )
    if len(expected) != 52 or len({item["caseId"] for item in expected}) != 52:
        raise EvidenceManifestError("public capsule/recipe matrix is not exactly 52 unique cases")
    if sum("expectedStatus" in item for item in expected) != 28:
        raise EvidenceManifestError("frozen runner oracle is not exactly 25+3 cases")
    return capsule, expected


def _assert_parity_summary(
    summary: Mapping[str, Any],
    label: str,
    *,
    capsule: Mapping[str, Any],
    expected_observations: Sequence[Mapping[str, Any]],
) -> None:
    summary_fields = {
        "apiVersion",
        "kind",
        "capsuleDigest",
        "harnessArtifactDigest",
        "generatorArtifactDigest",
        "recipeSetArtifactDigest",
        "recipeSetDigest",
        "capabilitySetDigest",
        "profileId",
        "profileVersion",
        "boundedCapabilityId",
        "implementations",
        "generatorRuntimeEvidence",
        "observationManifest",
        "observationManifestDigest",
        "semanticClassStats",
        "validateFrozenCases",
        "validateProbeCases",
        "validateTotalCases",
        "validatePassedCases",
        "deriveFrozenCases",
        "deriveGeneratedCases",
        "deriveTotalCases",
        "derivePassedCases",
        "totalCases",
        "passedCases",
        "mutants",
        "claim",
        "disagreements",
        "status",
    }
    if set(summary) != summary_fields:
        raise EvidenceManifestError(f"{label} parity summary is not closed")
    if (
        summary.get("apiVersion") != "oac.portability.parity-summary/v0alpha1"
        or summary.get("kind") != "SupplierPortabilityParitySummary"
        or summary.get("profileId") != "oac.supplier.transfer"
        or summary.get("profileVersion") != "v0.2"
        or summary.get("boundedCapabilityId")
        != "oac.supplier.transfer.portability-capsule/v0.2-seed-2"
        or summary.get("capsuleDigest") != capsule.get("capsuleDigest")
    ):
        raise EvidenceManifestError(f"{label} parity summary coordinate drift")
    expected_claim = {
        "established": "internal-bounded-cross-language-two-implementation-parity",
        "scope": "exact frozen and public generated seed-2 capsule cases only",
        "excludes": [
            "clean-room independence",
            "organizational independence",
            "complete OAC conformance",
            "enterprise correctness",
            "standard consensus",
        ],
    }
    if summary.get("claim") != expected_claim:
        raise EvidenceManifestError(f"{label} claim is not the exact closed ceiling")

    observation_manifest = summary.get("observationManifest")
    if not isinstance(observation_manifest, dict):
        raise EvidenceManifestError(f"{label} observation manifest is not an object")
    expected_manifest_fields = {
        "apiVersion",
        "kind",
        "capsuleDigest",
        "capabilitySetDigest",
        "generatorArtifactDigest",
        "recipeSetDigest",
        "implementationMaterials",
        "observations",
    }
    if set(observation_manifest) != expected_manifest_fields:
        raise EvidenceManifestError(f"{label} observation manifest is not closed")
    if (
        observation_manifest.get("apiVersion") != "oac.portability.observations/v0alpha1"
        or observation_manifest.get("kind") != "SupplierPortabilityObservationManifest"
        or observation_manifest.get("capsuleDigest") != capsule.get("capsuleDigest")
        or observation_manifest.get("capabilitySetDigest") != summary.get("capabilitySetDigest")
        or observation_manifest.get("generatorArtifactDigest")
        != summary.get("generatorArtifactDigest")
        or observation_manifest.get("recipeSetDigest") != summary.get("recipeSetDigest")
    ):
        raise EvidenceManifestError(f"{label} observation manifest material binding drift")

    implementations = summary.get("implementations")
    if not isinstance(implementations, list) or len(implementations) != 2:
        raise EvidenceManifestError(f"{label} must bind exactly two implementations")
    expected_implementations = (
        {
            "implementationId": "oac.reference.python.supplier-v02",
            "implementationVersion": "0.3.0a0",
            "language": "Python",
            "method": "bound-executable-version-probe",
        },
        {
            "implementationId": "oac.supplier.go.internal",
            "implementationVersion": "0.2.0-seed2",
            "language": "Go",
            "method": "go-binary-build-info",
        },
    )
    implementation_fields = {
        "implementationId",
        "implementationVersion",
        "commandDigest",
        "sourceClosureDigest",
        "runtime",
        "languageEvidence",
    }
    implementation_materials: list[dict[str, Any]] = []
    implementation_ids: set[str] = set()
    for implementation, expected_implementation in zip(
        implementations, expected_implementations, strict=True
    ):
        if not isinstance(implementation, dict) or set(implementation) != implementation_fields:
            raise EvidenceManifestError(f"{label} implementation evidence is invalid")
        material = {
            field: implementation[field]
            for field in (
                "implementationId",
                "implementationVersion",
                "commandDigest",
                "sourceClosureDigest",
            )
        }
        implementation_id = material["implementationId"]
        runtime = implementation["runtime"]
        language_evidence = implementation["languageEvidence"]
        if (
            not isinstance(implementation_id, str)
            or not implementation_id
            or implementation_id in implementation_ids
            or not isinstance(material["implementationVersion"], str)
            or not material["implementationVersion"]
            or not _is_sha256(material["commandDigest"])
            or not _is_sha256(material["sourceClosureDigest"])
            or implementation_id != expected_implementation["implementationId"]
            or material["implementationVersion"] != expected_implementation["implementationVersion"]
            or not isinstance(runtime, dict)
            or set(runtime) != {"descriptor", "evidenceDigest"}
            or not isinstance(runtime.get("descriptor"), str)
            or not runtime["descriptor"]
            or not _is_sha256(runtime.get("evidenceDigest"))
            or not isinstance(language_evidence, dict)
            or set(language_evidence) != {"language", "verified", "method"}
            or language_evidence
            != {
                "language": expected_implementation["language"],
                "verified": True,
                "method": expected_implementation["method"],
            }
        ):
            raise EvidenceManifestError(f"{label} implementation material is invalid or duplicated")
        implementation_ids.add(implementation_id)
        implementation_materials.append(material)
    if observation_manifest.get("implementationMaterials") != implementation_materials:
        raise EvidenceManifestError(
            f"{label} observation implementation materials do not match the summary"
        )

    generator_runtime = summary.get("generatorRuntimeEvidence")
    if (
        not isinstance(generator_runtime, dict)
        or set(generator_runtime)
        != {"language", "runtimeImplementation", "runtimeVersion", "dependencies"}
        or generator_runtime.get("language") != "Python"
        or not isinstance(generator_runtime.get("runtimeImplementation"), str)
        or not generator_runtime["runtimeImplementation"]
        or not isinstance(generator_runtime.get("runtimeVersion"), str)
        or not generator_runtime["runtimeVersion"]
        or not isinstance(generator_runtime.get("dependencies"), list)
        or len(generator_runtime["dependencies"]) != 1
    ):
        raise EvidenceManifestError(f"{label} generator runtime evidence is invalid")
    dependency = generator_runtime["dependencies"][0]
    if (
        not isinstance(dependency, dict)
        or set(dependency) != {"name", "version"}
        or dependency.get("name") != "rfc8785"
        or not isinstance(dependency.get("version"), str)
        or not dependency["version"]
    ):
        raise EvidenceManifestError(f"{label} generator dependency evidence is invalid")

    observations = observation_manifest.get("observations")
    if not isinstance(observations, list) or len(observations) != 52:
        raise EvidenceManifestError(f"{label} does not contain exactly 52 observations")
    observation_fields = {
        "caseId",
        "operation",
        "semanticClass",
        "inputDigest",
        "leftStatus",
        "rightStatus",
        "leftObservationDigest",
        "rightObservationDigest",
        "passed",
    }
    projections: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != observation_fields:
            raise EvidenceManifestError(f"{label} observation entry is not closed")
        case_id = observation.get("caseId")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise EvidenceManifestError(f"{label} caseId set is not unique")
        case_ids.add(case_id)
        if (
            observation.get("passed") is not True
            or not isinstance(observation.get("leftStatus"), str)
            or not observation.get("leftStatus")
            or observation.get("leftStatus") != observation.get("rightStatus")
            or not _is_sha256(observation.get("leftObservationDigest"))
            or observation.get("leftObservationDigest") != observation.get("rightObservationDigest")
        ):
            raise EvidenceManifestError(
                f"{label} observation does not establish exact bilateral parity: {case_id}"
            )
        projections.append(
            {
                field: observation[field]
                for field in ("caseId", "operation", "semanticClass", "inputDigest")
            }
        )
    expected_projections = [
        {field: item[field] for field in ("caseId", "operation", "semanticClass", "inputDigest")}
        for item in expected_observations
    ]
    if projections != expected_projections:
        raise EvidenceManifestError(
            f"{label} observations differ from the capsule/public-recipe matrix"
        )
    for observation, expected in zip(observations, expected_observations, strict=True):
        if "expectedStatus" not in expected:
            continue
        if (
            observation["leftStatus"] != expected["expectedStatus"]
            or observation["leftObservationDigest"] != expected["expectedObservationDigest"]
        ):
            raise EvidenceManifestError(
                f"{label} runner-owned oracle drift: {observation['caseId']}"
            )

    expected_observation_digest = _digest(rfc8785.dumps(observation_manifest))
    if summary.get("observationManifestDigest") != expected_observation_digest:
        raise EvidenceManifestError(f"{label} observationManifestDigest mismatch")

    class_totals = Counter(str(item["semanticClass"]) for item in observations)
    class_passes = Counter(
        str(item["semanticClass"]) for item in observations if item["passed"] is True
    )
    semantic_class_stats = [
        {
            "semanticClass": semantic_class,
            "totalCases": class_totals[semantic_class],
            "passedCases": class_passes[semantic_class],
        }
        for semantic_class in sorted(class_totals)
    ]
    if summary.get("semanticClassStats") != semantic_class_stats:
        raise EvidenceManifestError(f"{label} semanticClassStats are not derived evidence")

    validate_frozen = sum(
        item["operation"] == "validateResource" and item["semanticClass"] == "admission-frozen"
        for item in observations
    )
    validate_total = sum(item["operation"] == "validateResource" for item in observations)
    validate_probes = validate_total - validate_frozen
    derive_frozen = sum(
        item["operation"] == "derive" and item["semanticClass"] == "frozen-baseline"
        for item in observations
    )
    derive_total = sum(item["operation"] == "derive" for item in observations)
    derive_generated = derive_total - derive_frozen
    derived_counts = {
        "status": "PASS",
        "totalCases": len(observations),
        "passedCases": sum(item["passed"] is True for item in observations),
        "validateFrozenCases": validate_frozen,
        "validateProbeCases": validate_probes,
        "validateTotalCases": validate_total,
        "validatePassedCases": sum(
            item["operation"] == "validateResource" and item["passed"] is True
            for item in observations
        ),
        "deriveFrozenCases": derive_frozen,
        "deriveGeneratedCases": derive_generated,
        "deriveTotalCases": derive_total,
        "derivePassedCases": sum(
            item["operation"] == "derive" and item["passed"] is True for item in observations
        ),
    }
    bounded_counts = {
        "status": "PASS",
        "totalCases": 52,
        "passedCases": 52,
        "validateFrozenCases": 5,
        "validateProbeCases": 20,
        "validateTotalCases": 25,
        "validatePassedCases": 25,
        "deriveFrozenCases": 3,
        "deriveGeneratedCases": 24,
        "deriveTotalCases": 27,
        "derivePassedCases": 27,
    }
    if derived_counts != bounded_counts:
        raise EvidenceManifestError(f"{label} derived matrix cardinality drift")
    for field, value in derived_counts.items():
        if summary.get(field) != value:
            raise EvidenceManifestError(f"{label} parity count drift: {field}")
    if summary.get("disagreements") != []:
        raise EvidenceManifestError(f"{label} has an open within-set disagreement")
    mutants = summary.get("mutants")
    expected_mutants = [
        {
            "mutantId": "oac.mutant.canned-three",
            "frozenCasesPassed": 3,
            "rejected": True,
            "rejectedBy": "generated:scope-order",
            "conclusion": "canned-three-rejected",
        },
        {
            "mutantId": "oac.mutant.root-unknown-forgery",
            "frozenCasesPassed": 3,
            "rejected": True,
            "rejectedBy": "generated:candidate-root",
            "conclusion": "root-unknown-forgery-rejected",
        },
    ]
    if not isinstance(mutants, list) or len(mutants) != 2:
        raise EvidenceManifestError(f"{label} mutant gate is incomplete")
    mutant_artifacts: set[str] = set()
    for mutant, expected_mutant in zip(mutants, expected_mutants, strict=True):
        if (
            not isinstance(mutant, dict)
            or set(mutant) != {*expected_mutant, "artifactDigest"}
            or {key: mutant[key] for key in expected_mutant} != expected_mutant
            or not _is_sha256(mutant.get("artifactDigest"))
            or mutant["artifactDigest"] in mutant_artifacts
        ):
            raise EvidenceManifestError(f"{label} mutant gate drift")
        mutant_artifacts.add(mutant["artifactDigest"])


def _summary_pair(
    capsule: Mapping[str, Any],
    expected_observations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _load_json(SEED_ROOT / "parity-summary.json", exact_jcs_lf=True)
    installed = _load_json(REPLAY_ROOT / "seed2-installed-parity-summary.json", exact_jcs_lf=True)
    _assert_parity_summary(
        source,
        "source",
        capsule=capsule,
        expected_observations=expected_observations,
    )
    _assert_parity_summary(
        installed,
        "installed",
        capsule=capsule,
        expected_observations=expected_observations,
    )
    if source["mutants"] != installed["mutants"]:
        raise EvidenceManifestError("source and installed mutant gates differ")
    normalized_source = copy.deepcopy(source)
    normalized_installed = copy.deepcopy(installed)
    for value in (normalized_source, normalized_installed):
        value["implementations"][0]["commandDigest"] = "<python-command>"
        value["observationManifestDigest"] = "<observation-manifest>"
        value["observationManifest"]["implementationMaterials"][0]["commandDigest"] = (
            "<python-command>"
        )
    if normalized_source != normalized_installed:
        raise EvidenceManifestError(
            "source and installed summaries differ beyond Python command identity"
        )
    if (
        source["implementations"][0]["commandDigest"]
        == installed["implementations"][0]["commandDigest"]
        or source["observationManifestDigest"] == installed["observationManifestDigest"]
    ):
        raise EvidenceManifestError("installed summary does not carry a distinct Python identity")
    return source, installed


def _validate_frozen_summary_bindings(summary: Mapping[str, Any]) -> None:
    recipe_path = ROOT / "ctk/generators/supplier-v02-seed-2.recipes.json"
    checks = {
        "capsuleDigest": _detached_artifact(SEED_ROOT / "capsule.json", "capsuleDigest")[
            "detachedDigest"
        ],
        "harnessArtifactDigest": _digest(
            (ROOT / "scripts/check_supplier_v02_parity.py").read_bytes()
        ),
        "generatorArtifactDigest": _digest((ROOT / "scripts/supplier_v02_casegen.py").read_bytes()),
        "capabilitySetDigest": _detached_artifact(
            ROOT / "ctk/capabilities/supplier-v02-seed-2.capability-set.json",
            "capabilitySetDigest",
        )["detachedDigest"],
        "recipeSetArtifactDigest": _digest(recipe_path.read_bytes()),
        "recipeSetDigest": _detached_artifact(recipe_path, "recipeSetDigest")["detachedDigest"],
    }
    for field, expected in checks.items():
        if summary.get(field) != expected:
            raise EvidenceManifestError(f"parity summary material drift: {field}")


@cache
def _validate_live_source_parity() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_supplier_v02_parity.py"),
            "--check-summary-portable",
            str(SEED_ROOT / "parity-summary.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise EvidenceManifestError(
            "live Supplier seed-2 parity does not match the stored portable projection"
        )
    live = _loads(completed.stdout, "live Supplier seed-2 parity")
    if not isinstance(live, dict) or live.get("status") != "PASS":
        raise EvidenceManifestError("live Supplier seed-2 parity did not pass")


def _installed_material() -> dict[str, Any]:
    path = REPLAY_ROOT / "installed-python-ledger.json"
    value = _load_json(path, exact_jcs_lf=True)
    _detached_artifact(path, "digest")
    schema = _load_json(INSTALLED_LEDGER_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)

    def closure(item: object) -> str:
        return _digest(rfc8785.dumps(item))

    def paths_are_sorted_unique(items: Sequence[Mapping[str, Any]], field: str) -> bool:
        paths = [item.get(field) for item in items]
        return (
            all(isinstance(item, str) for item in paths)
            and paths == sorted(paths)
            and len(paths) == len(set(paths))
        )

    wheel = value["wheel"]
    wheel_entries = wheel["entries"]
    wheel_paths = [item["path"] for item in wheel_entries]
    if (
        wheel["entryCount"] != len(wheel_entries)
        or not paths_are_sorted_unique(wheel_entries, "path")
        or len({item.casefold() for item in wheel_paths}) != len(wheel_paths)
        or wheel["entryPreimageDigest"] != closure(wheel_entries)
        or wheel_paths.count(wheel["recordPath"]) != 1
    ):
        raise EvidenceManifestError("installed ledger wheel entry closure is invalid")

    payload = value["installedPayload"]
    payload_files = payload["files"]
    if (
        payload["distributionName"] != wheel["distributionName"]
        or payload["normalizedDistributionName"] != wheel["normalizedDistributionName"]
        or payload["version"] != wheel["version"]
        or payload["excludedComparisonPaths"] != [wheel["recordPath"]]
        or payload["matchedFileCount"] != len(payload_files)
        or payload["matchedFileCount"] != wheel["entryCount"] - 1
        or payload["payloadByteMatch"] is not True
        or payload["payloadClosureDigest"] != closure(payload_files)
        or not paths_are_sorted_unique(payload_files, "wheelPath")
        or len({item["installedPath"] for item in payload_files}) != len(payload_files)
    ):
        raise EvidenceManifestError("installed payload closure is invalid")
    entries_by_path = {item["path"]: item for item in wheel_entries}
    expected_payload_paths = set(wheel_paths) - {wheel["recordPath"]}
    if {item["wheelPath"] for item in payload_files} != expected_payload_paths:
        raise EvidenceManifestError("installed payload does not cover every non-RECORD entry")
    for item in payload_files:
        entry = entries_by_path[item["wheelPath"]]
        if (
            item["wheelRawSha256"] != entry["rawSha256"]
            or item["installedRawSha256"] != entry["rawSha256"]
            or item["sizeBytes"] != entry["sizeBytes"]
            or item["byteEqual"] is not True
        ):
            raise EvidenceManifestError("installed payload byte relation is invalid")

    environment = value["installedEnvironment"]
    distributions = environment["distributions"]
    distribution_sort_keys = [
        (item["normalizedName"], item["version"], item["metadataPath"]) for item in distributions
    ]
    if (
        environment["distributionCount"] != len(distributions)
        or distribution_sort_keys != sorted(distribution_sort_keys)
        or len(distribution_sort_keys) != len(set(distribution_sort_keys))
        or environment["environmentClosureDigest"] != closure(distributions)
    ):
        raise EvidenceManifestError("installed environment closure is invalid")
    for distribution in distributions:
        files = distribution["files"]
        normalized_name = re.sub(r"[-_.]+", "-", distribution["name"]).lower()
        if (
            distribution["normalizedName"] != normalized_name
            or distribution["fileCount"] != len(files)
            or not paths_are_sorted_unique(files, "path")
            or distribution["fileLedgerDigest"] != closure(files)
        ):
            raise EvidenceManifestError("installed distribution ledger is invalid")
    matching_distributions = [
        item
        for item in distributions
        if item["normalizedName"] == wheel["normalizedDistributionName"]
        and item["version"] == wheel["version"]
    ]
    if len(matching_distributions) != 1:
        raise EvidenceManifestError("installed wheel distribution identity is not unique")
    installed_distribution = matching_distributions[0]
    installed_files_by_path = {item["path"]: item for item in installed_distribution["files"]}
    for item in payload_files:
        expected_installed_path = PurePosixPath(
            installed_distribution["installationRootPath"], item["wheelPath"]
        ).as_posix()
        installed_file = installed_files_by_path.get(item["installedPath"])
        if (
            item["installedPath"] != expected_installed_path
            or installed_file is None
            or installed_file["rawSha256"] != item["installedRawSha256"]
            or installed_file["sizeBytes"] != item["sizeBytes"]
        ):
            raise EvidenceManifestError("installed payload is not bound to its environment")

    installation_root = PurePosixPath(installed_distribution["installationRootPath"])
    installed_module_sources: list[tuple[str, str]] = []
    for installed_file in installed_distribution["files"]:
        installed_path = PurePosixPath(installed_file["path"])
        try:
            package_relative = installed_path.relative_to(installation_root)
        except ValueError:
            continue
        if (
            len(package_relative.parts) >= 2
            and package_relative.parts[0] == "oac"
            and package_relative.suffix in {".py", ".pyi"}
        ):
            installed_module_sources.append(
                (package_relative.as_posix(), installed_file["rawSha256"])
            )
    installed_module_sources.sort()
    if not installed_module_sources or len(installed_module_sources) != len(
        {path for path, _ in installed_module_sources}
    ):
        raise EvidenceManifestError("installed OAC module ledger is empty or duplicated")
    installed_command_projection = {
        "command": ["<file:0:python>", "-I", "-m", "oac.ctk_adapter_v2"],
        "materials": [
            {
                "label": "command-file:0:python",
                "digest": value["targetPython"]["executableRawSha256"],
            },
            *[
                {"label": f"module-closure:{module_path}", "digest": module_digest}
                for module_path, module_digest in installed_module_sources
            ],
        ],
    }
    installed_command_digest = closure(installed_command_projection)

    repository = value["repositoryInputs"]
    repository_inputs = [repository["pyprojectToml"], repository["uvLock"]]
    if (
        [item["path"] for item in repository_inputs] != ["pyproject.toml", "uv.lock"]
        or any(not _is_sha256(item.get("rawSha256")) for item in repository_inputs)
        or any(not isinstance(item.get("sizeBytes"), int) for item in repository_inputs)
        or repository["closureDigest"] != closure(repository_inputs)
    ):
        raise EvidenceManifestError("historical repository-input ledger is invalid")

    semantic = value["semanticProduction"]
    semantic_files = semantic["files"]
    semantic_paths = [item.get("path") for item in semantic_files]
    if (
        not semantic_files
        or semantic["sourceRoot"] != "src/oac"
        or semantic_paths != sorted(semantic_paths)
        or len(semantic_paths) != len(set(semantic_paths))
        or any(
            not isinstance(path, str)
            or PurePosixPath(path).suffix not in {".py", ".pyi"}
            or not _is_sha256(item.get("rawSha256"))
            or not isinstance(item.get("sizeBytes"), int)
            for path, item in zip(semantic_paths, semantic_files, strict=True)
        )
        or semantic["fileCount"] != len(semantic_files)
        or semantic["closureDigest"] != closure(semantic_files)
    ):
        raise EvidenceManifestError("historical semantic-production ledger is invalid")

    payload_files_by_path = {item["wheelPath"]: item for item in payload_files}
    expected_semantic_wheel_paths = {f"oac/{item['path']}" for item in semantic_files}
    actual_semantic_wheel_paths = {
        item["path"]
        for item in wheel_entries
        if item["path"].startswith("oac/") and PurePosixPath(item["path"]).suffix in {".py", ".pyi"}
    }
    if not expected_semantic_wheel_paths <= actual_semantic_wheel_paths:
        raise EvidenceManifestError(
            "installed wheel is missing a semantic-production Python module"
        )
    for source in semantic_files:
        wheel_path = f"oac/{source['path']}"
        installed_path = PurePosixPath(installation_root, wheel_path).as_posix()
        wheel_entry = entries_by_path.get(wheel_path)
        installed_file = installed_files_by_path.get(installed_path)
        comparison = payload_files_by_path.get(wheel_path)
        expected_bytes = (source["rawSha256"], source["sizeBytes"])
        if (
            wheel_entry is None
            or installed_file is None
            or comparison is None
            or (wheel_entry["rawSha256"], wheel_entry["sizeBytes"]) != expected_bytes
            or (installed_file["rawSha256"], installed_file["sizeBytes"]) != expected_bytes
            or comparison["installedPath"] != installed_path
        ):
            raise EvidenceManifestError(
                "semantic production is not byte-bound to wheel and installed payload"
            )

    runtime_projection = {
        "wheelRawSha256": wheel["rawSha256"],
        "wheelEntryPreimageDigest": wheel["entryPreimageDigest"],
        "installedPayloadClosureDigest": payload["payloadClosureDigest"],
        "targetPython": value["targetPython"],
        "environmentClosureDigest": environment["environmentClosureDigest"],
        "repositoryInputClosureDigest": repository["closureDigest"],
        "semanticProductionSourceClosureDigest": semantic["closureDigest"],
    }
    if value["runtimeBindingDigest"] != closure(runtime_projection):
        raise EvidenceManifestError("installed ledger runtime binding is invalid")

    return {
        "replayBindingClass": value["replayBindingClass"],
        "wheelRawSha256": wheel["rawSha256"],
        "wheelEntryPreimageDigest": wheel["entryPreimageDigest"],
        "wheelEntryCount": wheel["entryCount"],
        "pythonExecutableRawSha256": value["targetPython"]["executableRawSha256"],
        "semanticProductionSourceClosureDigest": semantic["closureDigest"],
        "environmentClosureDigest": environment["environmentClosureDigest"],
        "runtimeBindingDigest": value["runtimeBindingDigest"],
        "repositoryInputClosureDigest": repository["closureDigest"],
        "installedPayloadClosureDigest": payload["payloadClosureDigest"],
        "installedDistributionFileLedgerDigest": installed_distribution["fileLedgerDigest"],
        "payloadByteMatch": payload["payloadByteMatch"],
        "matchedPayloadFiles": payload["matchedFileCount"],
        "distributionCount": environment["distributionCount"],
        "environmentFileCount": sum(item["fileCount"] for item in distributions),
        "limitations": value["limitations"],
        "installedCommandDigest": installed_command_digest,
        "semanticFiles": semantic_files,
    }


def _validate_installed_capability(capability_set: Mapping[str, Any]) -> dict[str, Any]:
    path = REPLAY_ROOT / "installed-capability.json"
    value = _load_json(path, exact_jcs_lf=True)
    expected = {
        "protocolVersion": capability_set["protocolVersion"],
        "requestId": "final-installed-capability",
        "result": {
            "adapterProtocolVersion": capability_set["protocolVersion"],
            "implementationId": "oac.reference.python.supplier-v02",
            "implementationVersion": "0.3.0a0",
            "tracks": capability_set["tracks"],
        },
        "sutStatus": "COMPLETED",
    }
    if value != expected:
        raise EvidenceManifestError("installed capability does not equal the frozen track set")
    return value


def _validate_legacy_tck_replay(path: Path) -> dict[str, Any]:
    value = _load_json(path, exact_jcs_lf=True)
    manifest = _load_json(ROOT / "tck/manifest.json")
    cases = manifest.get("cases")
    results = value.get("results")
    if (
        set(value) != {"suiteId", "claimLimit", "passed", "failed", "results"}
        or value.get("suiteId") != manifest.get("suiteId")
        or value.get("claimLimit") != manifest.get("claimLimit")
        or not isinstance(cases, list)
        or not isinstance(results, list)
        or len(results) != len(cases)
    ):
        raise EvidenceManifestError("installed legacy TCK replay envelope drift")

    expected_ids: list[str] = []
    for case, result in zip(cases, results, strict=True):
        if not isinstance(case, dict) or not isinstance(result, dict):
            raise EvidenceManifestError("installed legacy TCK case is not an object")
        case_id = case.get("id")
        expectation = case.get("expect")
        actual = result.get("actual")
        if (
            not isinstance(case_id, str)
            or not isinstance(expectation, dict)
            or set(result) != {"id", "passed", "actual"}
            or result.get("id") != case_id
            or result.get("passed") is not True
            or not isinstance(actual, dict)
            or set(actual) != {"outcome", "verdict", "reasonCodes"}
        ):
            raise EvidenceManifestError("installed legacy TCK result structure drift")
        actual_codes = actual.get("reasonCodes")
        expected_codes = expectation.get("reasonCodes")
        if (
            not isinstance(actual_codes, list)
            or not all(isinstance(item, str) for item in actual_codes)
            or actual_codes != sorted(set(actual_codes))
            or not isinstance(expected_codes, list)
            or not all(isinstance(item, str) for item in expected_codes)
        ):
            raise EvidenceManifestError("installed legacy TCK reason-code ledger drift")
        codes_match = (
            actual_codes == sorted(expected_codes)
            if expectation.get("match", "exact") == "exact"
            else set(expected_codes) <= set(actual_codes)
        )
        if (
            actual.get("outcome") != expectation.get("outcome")
            or actual.get("verdict") != expectation.get("verdict")
            or not codes_match
        ):
            raise EvidenceManifestError("installed legacy TCK observation violates expectation")
        expected_ids.append(case_id)
    if len(expected_ids) != len(set(expected_ids)):
        raise EvidenceManifestError("legacy TCK manifest contains duplicate case identifiers")
    passed = sum(result["passed"] is True for result in results)
    if value["passed"] != passed or value["failed"] != len(results) - passed:
        raise EvidenceManifestError("installed legacy TCK aggregate is not derived from results")
    return value


def _validate_phase_a_replay(
    path: Path,
    *,
    bundle_path: Path,
    requirement_path: Path,
    resource_profile_path: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    value = _load_json(path, exact_jcs_lf=True)
    bundle_value = _load_json(bundle_path)
    bundle = _detached_artifact(bundle_path, "bundleDigest")
    requirement_set = _load_json(requirement_path)

    artifacts = bundle_value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise EvidenceManifestError("Phase-A bundle artifact ledger is empty")
    artifact_paths: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "path",
            "digest",
            "size",
            "mediaType",
        }:
            raise EvidenceManifestError("Phase-A bundle artifact descriptor drift")
        relative = artifact.get("path")
        if not isinstance(relative, str) or "\\" in relative:
            raise EvidenceManifestError("Phase-A bundle artifact path is invalid")
        logical = PurePosixPath(relative)
        if logical.is_absolute() or ".." in logical.parts or logical.as_posix() != relative:
            raise EvidenceManifestError("Phase-A bundle artifact path is unsafe")
        candidate = bundle_path.parent / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise EvidenceManifestError("Phase-A bundle artifact is missing or a symlink")
        raw = candidate.read_bytes()
        if artifact["digest"] != _digest(raw) or artifact["size"] != len(raw):
            raise EvidenceManifestError("Phase-A bundle artifact byte ledger drift")
        artifact_paths.append(relative)
    if len(artifact_paths) != len(set(artifact_paths)):
        raise EvidenceManifestError("Phase-A bundle artifact paths are duplicated")

    if (
        value.get("runResultVersion") != "oac.ctk.run-result/v0alpha1"
        or value.get("bundleDigest") != bundle["detachedDigest"]
        or value.get("requirementSetDigest") != _digest(requirement_path.read_bytes())
        or bundle_value.get("requirementSetRef") != "requirements.json"
        or bundle_value.get("resourceProfileRef") != "resource-profile.json"
        or "requirements.json" not in artifact_paths
        or "resource-profile.json" not in artifact_paths
        or _digest(resource_profile_path.read_bytes())
        != next(
            artifact["digest"]
            for artifact in artifacts
            if artifact["path"] == "resource-profile.json"
        )
    ):
        raise EvidenceManifestError("installed Phase-A replay material binding drift")

    expected_operations = (
        "canonicalize",
        "deriveIdentifier",
        "witness",
        "strongKleene",
        "closureMicro",
        "resourceCheck",
    )
    expected_capability = {
        "adapterProtocolVersion": "oac.ctk.stdio/v1",
        "implementationId": "oac.reference.python",
        "implementationVersion": "0.3.0a0",
        "tracks": [
            {
                "operation": operation,
                "profileId": bundle_value["profileId"],
                "profileVersion": bundle_value["profileVersion"],
                "role": "semantic-kernel",
                "wireVersion": "oac.ctk.stdio/v1",
            }
            for operation in expected_operations
        ],
    }
    capability = value.get("capabilityStatement")
    environment = value.get("environment")
    if (
        capability != expected_capability
        or value.get("capabilityStatementDigest") != _digest(rfc8785.dumps(capability))
        or not isinstance(environment, dict)
        or set(environment) != {"system", "machine", "pythonImplementation", "pythonVersion"}
        or not all(isinstance(item, str) and item for item in environment.values())
        or value.get("environmentDigest") != _digest(rfc8785.dumps(environment))
    ):
        raise EvidenceManifestError("installed Phase-A capability or environment closure drift")

    required = requirement_set.get("required")
    not_scored = requirement_set.get("notScored")
    results = value.get("caseResults")
    if (
        not isinstance(required, list)
        or not required
        or len(required) != len(set(required))
        or not_scored != []
        or not isinstance(results, list)
        or len(results) != len(required)
    ):
        raise EvidenceManifestError("installed Phase-A requirement inventory drift")
    expected_case_ids = sorted(required)
    if [item.get("caseId") for item in results if isinstance(item, dict)] != expected_case_ids:
        raise EvidenceManifestError("installed Phase-A case inventory drift")
    for case_id, result in zip(expected_case_ids, results, strict=True):
        case_path = bundle_path.parent / "cases" / f"{case_id}.json"
        case = _load_json(case_path)
        if (
            set(result)
            != {
                "caseId",
                "caseOutcome",
                "sutStatus",
                "stage",
                "reasonCodes",
                "domainVerdict",
                "elapsedMs",
            }
            or result["caseOutcome"] != "PASS"
            or result["sutStatus"] != case.get("expect", {}).get("sutStatus")
            or result["stage"] != "VERDICT"
            or result["reasonCodes"] != []
            or result["domainVerdict"] is not None
            or isinstance(result["elapsedMs"], bool)
            or not isinstance(result["elapsedMs"], int)
            or result["elapsedMs"] < 0
        ):
            raise EvidenceManifestError("installed Phase-A case result drift")
    derived_summary = {
        "required": len(required),
        "passed": len(results),
        "failed": 0,
        "notScored": 0,
    }
    if value.get("summary") != derived_summary or value.get("requiredPassed") is not True:
        raise EvidenceManifestError("installed Phase-A aggregate is not derived from results")
    return value, bundle


def _disagreement_pairs() -> list[dict[str, Any]]:
    capture = _load_script_module("capture_supplier_v02_disagreements")
    checked = capture.check(DISAGREEMENT_ROOT)
    incidents = sorted(DISAGREEMENT_ROOT.glob("DIS-*.incident.json"))
    resolutions = sorted(DISAGREEMENT_ROOT.glob("DIS-*.resolution.json"))
    expected_checked = [path for pair in zip(incidents, resolutions, strict=True) for path in pair]
    if checked != expected_checked:
        raise EvidenceManifestError(
            "disagreement checker did not admit the exact incident/resolution inventory"
        )
    incident_ids = tuple(
        path.name.split("-", 2)[0] + "-" + path.name.split("-", 2)[1] for path in incidents
    )
    resolution_ids = tuple(
        path.name.split("-", 2)[0] + "-" + path.name.split("-", 2)[1] for path in resolutions
    )
    if incident_ids != EXPECTED_INCIDENT_IDS or resolution_ids != EXPECTED_INCIDENT_IDS:
        raise EvidenceManifestError(
            "disagreement inventory is incomplete or contains an extra file"
        )
    incident_validator = Draft202012Validator(_load_json(INCIDENT_SCHEMA_PATH))
    resolution_validator = Draft202012Validator(_load_json(RESOLUTION_SCHEMA_PATH))
    pairs: list[dict[str, Any]] = []
    for incident_id, incident_path, resolution_path in zip(
        EXPECTED_INCIDENT_IDS, incidents, resolutions, strict=True
    ):
        incident = _load_json(incident_path, exact_jcs_lf=True)
        resolution = _load_json(resolution_path, exact_jcs_lf=True)
        incident_validator.validate(incident)
        resolution_validator.validate(resolution)
        incident_descriptor = _detached_artifact(incident_path, "digest")
        resolution_descriptor = _detached_artifact(resolution_path, "digest")
        if (
            incident.get("incidentId") != incident_id
            or resolution.get("incidentRef", {}).get("incidentId") != incident_id
            or resolution.get("incidentRef", {}).get("digest")
            != incident_descriptor["detachedDigest"]
        ):
            raise EvidenceManifestError(f"disagreement pair is not content-bound: {incident_id}")
        pairs.append(
            {
                "incidentId": incident_id,
                "incident": incident_descriptor,
                "resolution": resolution_descriptor,
            }
        )
    return pairs


def build_manifest() -> dict[str, Any]:
    capsule, expected_observations = _admit_capsule_and_expected_observations()
    source, installed = _summary_pair(capsule, expected_observations)
    _validate_frozen_summary_bindings(source)
    _validate_frozen_summary_bindings(installed)
    source_python, source_go = source["implementations"]
    installed_python, installed_go = installed["implementations"]

    go_root = ROOT / "implementations/go-supplier-v02-internal"
    python_closure = source_python["sourceClosureDigest"]
    go_closure = source_go["sourceClosureDigest"]
    if installed_python["sourceClosureDigest"] != python_closure:
        raise EvidenceManifestError("historical Python production closures differ")
    if installed_go["sourceClosureDigest"] != go_closure:
        raise EvidenceManifestError("historical Go production closures differ")

    capability_path = ROOT / "ctk/capabilities/supplier-v02-seed-2.capability-set.json"
    capability_set = _load_json(capability_path, exact_jcs_lf=True)
    _validate_installed_capability(capability_set)
    installed_material = _installed_material()
    if (
        installed_material["installedCommandDigest"]
        != installed["implementations"][0]["commandDigest"]
    ):
        raise EvidenceManifestError(
            "installed summary Python command is not derived from the installed ledger"
        )
    source_python_command = {
        "command": ["<file:0:python3>", "-m", "oac.ctk_adapter_v2"],
        "materials": [
            {
                "label": "command-file:0:python3",
                "digest": installed_material["pythonExecutableRawSha256"],
            },
            *[
                {
                    "label": f"module-closure:oac/{item['path']}",
                    "digest": item["rawSha256"],
                }
                for item in installed_material["semanticFiles"]
            ],
        ],
    }
    if source_python["commandDigest"] != _digest(rfc8785.dumps(source_python_command)):
        raise EvidenceManifestError(
            "source summary Python command is not bound to its executable and modules"
        )

    statement_path = go_root / "independence-statement.json"
    statement = _load_json(statement_path)
    Draft202012Validator(
        _load_json(ROOT / "ctk/schemas/IndependenceStatement.schema.json")
    ).validate(statement)
    statement_descriptor = _detached_artifact(statement_path, "digest")
    if (
        statement["source"]["sourceDigest"] != go_closure
        or statement["generatedCode"]["generatedSourceDigest"] != go_closure
    ):
        raise EvidenceManifestError("Go IndependenceStatement source closure drift")
    expected_go_recipe = {
        "argv": ["go", "build", "-trimpath", "-o", "<artifact>", "."],
        "workingDirectory": "implementations/go-supplier-v02-internal",
    }
    if statement["build"]["recipeDigest"] != _digest(rfc8785.dumps(expected_go_recipe)):
        raise EvidenceManifestError("Go IndependenceStatement build recipe drift")
    go_mod_path = go_root / "go.mod"
    if statement["sbom"][
        "documentRef"
    ] != "go.mod (stdlib-only dependency declaration)" or statement["sbom"][
        "documentDigest"
    ] != _digest(go_mod_path.read_bytes()):
        raise EvidenceManifestError("Go IndependenceStatement module declaration drift")
    go_artifact_digest = statement["implementation"]["artifactDigest"]
    if statement["build"]["artifactDigest"] != go_artifact_digest:
        raise EvidenceManifestError("Go build and implementation artifact identities differ")
    expected_go_command = {
        "command": ["<file:0:oac-go-supplier-v02>"],
        "materials": [
            {
                "label": "command-file:0:oac-go-supplier-v02",
                "digest": go_artifact_digest,
            }
        ],
    }
    if source_go["commandDigest"] != _digest(rfc8785.dumps(expected_go_command)):
        raise EvidenceManifestError("Go summary command is not bound to the attested executable")

    legacy_path = REPLAY_ROOT / "installed-legacy-tck.json"
    legacy = _validate_legacy_tck_replay(legacy_path)

    phase_path = REPLAY_ROOT / "installed-phase-a-ctk.json"
    bundle_path = ROOT / "ctk/bundles/phase-a-v0.1/bundle.json"
    requirement_path = ROOT / "ctk/bundles/phase-a-v0.1/requirements.json"
    resource_profile_path = ROOT / "ctk/bundles/phase-a-v0.1/resource-profile.json"
    phase, bundle = _validate_phase_a_replay(
        phase_path,
        bundle_path=bundle_path,
        requirement_path=requirement_path,
        resource_profile_path=resource_profile_path,
    )

    demo_path = REPLAY_ROOT / "installed-demo.json"
    demo = _load_json(demo_path, exact_jcs_lf=True)
    expected_demo = {
        "caseId": "SC-001",
        "verdict": "ACCEPT",
        "planStatus": "planned",
        "effectCeiling": "zero_effect",
        "obligationCount": 17,
    }
    if any(demo.get(field) != value for field, value in expected_demo.items()):
        raise EvidenceManifestError("installed demo replay drift")

    if source_go != installed_go:
        raise EvidenceManifestError("Go identity differs between source and installed replay")

    ledger_path = REPLAY_ROOT / "installed-python-ledger.json"
    ledger_builder_path = ROOT / "scripts/build_installed_python_ledger.py"
    replay_readme = REPLAY_ROOT / "README.md"
    manifest: dict[str, Any] = {
        "apiVersion": "oac.portability.evidence-manifest/v0alpha2",
        "kind": "SupplierPortabilityEvidenceManifest",
        "manifestId": "oac.supplier.transfer/v0.2-seed-2/evidence",
        "serialization": "rfc8785+jcs+lf/v1",
        "schema": _repository_artifact(SCHEMA_PATH),
        "sourceState": {
            "baseRevision": "e301952ae08986e6398a636f04f883036bc88c8d",
            "workingTree": "uncommitted",
            "cleanArchiveReplayed": False,
        },
        "frozenMaterials": {
            "capsule": _detached_artifact(SEED_ROOT / "capsule.json", "capsuleDigest"),
            "protocol": _repository_artifact(ROOT / "ctk/protocol/stdio-v2.md"),
            "semanticRuleSet": _detached_artifact(
                ROOT / "profiles/supplier-change/semantic-rules-v0.2.json", "digest"
            ),
            "capabilitySet": _detached_artifact(capability_path, "capabilitySetDigest"),
            "recipeSet": _detached_artifact(
                ROOT / "ctk/generators/supplier-v02-seed-2.recipes.json",
                "recipeSetDigest",
            ),
            "generatorSource": _repository_artifact(ROOT / "scripts/supplier_v02_casegen.py"),
            "harnessSource": _repository_artifact(ROOT / "scripts/check_supplier_v02_parity.py"),
            "installedLedgerSchema": _repository_artifact(INSTALLED_LEDGER_SCHEMA_PATH),
            "installedLedgerBuilderSource": _repository_artifact(ledger_builder_path),
        },
        "summaries": {
            "source": {
                **_repository_artifact(SEED_ROOT / "parity-summary.json"),
                "observationManifestDigest": source["observationManifestDigest"],
            },
            "installed": {
                **_repository_artifact(REPLAY_ROOT / "seed2-installed-parity-summary.json"),
                "observationManifestDigest": installed["observationManifestDigest"],
            },
            "relationship": {
                "model": "same-evidence-except-python-command-identity/v1",
                "rawSummariesDiffer": True,
                "observationManifestDigestsDiffer": True,
                "pythonCommandDigestsDiffer": True,
                "goCommandDigestsEqual": True,
                "allOtherSummaryFieldsEqual": True,
            },
        },
        "implementations": {
            "production": [
                {
                    "implementationId": source_python["implementationId"],
                    "implementationVersion": source_python["implementationVersion"],
                    "language": "Python",
                    "productionSource": {
                        "root": "src/oac",
                        "algorithm": "executable-production-source-closure/v1",
                        "closureDigest": python_closure,
                    },
                    "commands": {
                        "sourceCommandDigest": source_python["commandDigest"],
                        "installedCommandDigest": installed_python["commandDigest"],
                    },
                },
                {
                    "implementationId": source_go["implementationId"],
                    "implementationVersion": source_go["implementationVersion"],
                    "language": "Go",
                    "productionSource": {
                        "root": "implementations/go-supplier-v02-internal",
                        "algorithm": "executable-production-source-closure/v1",
                        "closureDigest": go_closure,
                    },
                    "commands": {
                        "sourceCommandDigest": source_go["commandDigest"],
                        "installedCommandDigest": installed_go["commandDigest"],
                    },
                },
            ],
            "externalArtifacts": [
                {
                    "artifactId": "seed2-installed-python-wheel",
                    "artifactType": "python-wheel",
                    "rawSha256": installed_material["wheelRawSha256"],
                    "byteIdenticalBuilds": 2,
                    "verificationMode": "digest-attestation",
                    "repositoryBytesAvailable": False,
                    "attestationSourceId": "seed2-isolated-replay-record",
                },
                {
                    "artifactId": "seed2-final-go-executable",
                    "artifactType": "go-executable",
                    "rawSha256": go_artifact_digest,
                    "byteIdenticalBuilds": 2,
                    "verificationMode": "digest-attestation",
                    "repositoryBytesAvailable": False,
                    "attestationSourceId": "seed2-isolated-replay-record",
                },
            ],
            "externalArtifactAttestation": {
                "attestationSourceId": "seed2-isolated-replay-record",
                "bindingClass": installed_material["replayBindingClass"],
                "source": _repository_artifact(replay_readme),
            },
            "goIndependenceStatement": statement_descriptor,
            "installedPythonLedger": {
                "artifact": _detached_artifact(ledger_path, "digest"),
                "schema": _repository_artifact(INSTALLED_LEDGER_SCHEMA_PATH),
                "builderSource": _repository_artifact(ledger_builder_path),
                "replayBindingClass": installed_material["replayBindingClass"],
                "wheelRawSha256": installed_material["wheelRawSha256"],
                "wheelEntryPreimageDigest": installed_material["wheelEntryPreimageDigest"],
                "wheelEntryCount": installed_material["wheelEntryCount"],
                "pythonExecutableRawSha256": installed_material["pythonExecutableRawSha256"],
                "semanticProductionSourceClosureDigest": installed_material[
                    "semanticProductionSourceClosureDigest"
                ],
                "environmentClosureDigest": installed_material["environmentClosureDigest"],
                "runtimeBindingDigest": installed_material["runtimeBindingDigest"],
                "repositoryInputClosureDigest": installed_material["repositoryInputClosureDigest"],
                "installedPayloadClosureDigest": installed_material[
                    "installedPayloadClosureDigest"
                ],
                "installedDistributionFileLedgerDigest": installed_material[
                    "installedDistributionFileLedgerDigest"
                ],
                "payloadByteMatch": installed_material["payloadByteMatch"],
                "matchedPayloadFiles": installed_material["matchedPayloadFiles"],
                "distributionCount": installed_material["distributionCount"],
                "environmentFileCount": installed_material["environmentFileCount"],
                "limitations": installed_material["limitations"],
            },
        },
        "replays": {
            "bindingClass": installed_material["replayBindingClass"],
            "artifactExecutionProvenance": "not-cryptographically-established",
            "completeRuntimeEnvironmentReproducibility": False,
            "legacyTck": {
                "artifact": _repository_artifact(legacy_path),
                "passed": legacy["passed"],
                "failed": legacy["failed"],
            },
            "phaseA": {
                "artifact": _repository_artifact(phase_path),
                "bundle": bundle,
                "requirementSet": _repository_artifact(requirement_path),
                "resourceProfile": _repository_artifact(resource_profile_path),
                "required": phase["summary"]["required"],
                "passed": phase["summary"]["passed"],
                "failed": phase["summary"]["failed"],
                "requiredPassed": phase["requiredPassed"],
            },
            "demo": {
                "artifact": _repository_artifact(demo_path),
                **{field: demo[field] for field in expected_demo},
                "planDigest": demo["planDigest"],
                "certificateDigest": demo["certificateDigest"],
            },
            "installedCapability": _repository_artifact(REPLAY_ROOT / "installed-capability.json"),
        },
        "disagreements": _disagreement_pairs(),
        "mutants": source["mutants"],
        "matrixResult": {
            "status": source["status"],
            "totalCases": source["totalCases"],
            "passedCases": source["passedCases"],
            "openDisagreements": len(source["disagreements"]),
            "validation": {
                "frozen": source["validateFrozenCases"],
                "probes": source["validateProbeCases"],
                "total": source["validateTotalCases"],
                "passed": source["validatePassedCases"],
            },
            "derivation": {
                "frozen": source["deriveFrozenCases"],
                "publicGenerated": source["deriveGeneratedCases"],
                "total": source["deriveTotalCases"],
                "passed": source["derivePassedCases"],
            },
        },
        "claim": {
            "ceiling": "internal-bounded-cross-language-two-implementation-parity",
            "scope": "exact frozen and public generated seed-2 capsule cases only",
            "exclusions": [
                "clean-room independence",
                "organizational independence",
                "complete Supplier v0.2 conformance",
                "complete OAC conformance",
                "enterprise correctness",
                "standard consensus",
                "clean Git archive reproducibility",
                "sdist as final evidence",
                "hidden or held-out generalization",
                "cryptographic replay-to-artifact execution provenance",
                "complete Python standard-library and operating-system reproducibility",
            ],
            "sdistDisposition": "excluded-from-final-evidence",
            "archiveDisposition": "working-tree-evidence-not-clean-archive",
            "executionProvenanceDisposition": "self-attested-not-cryptographic",
            "runtimeEnvironmentDisposition": (
                "installed-distributions-bound-standard-library-and-os-excluded"
            ),
        },
        "digestAlgorithm": "sha256-jcs-detached/v1",
    }
    manifest["digest"] = _digest(rfc8785.dumps(manifest))
    return manifest


def check(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    manifest = _load_json(manifest_path, exact_jcs_lf=True)
    Draft202012Validator(schema).validate(manifest)
    claimed = manifest.get("digest")
    detached = {key: item for key, item in manifest.items() if key != "digest"}
    if claimed != _digest(rfc8785.dumps(detached)):
        raise EvidenceManifestError("evidence manifest detached digest mismatch")
    expected = build_manifest()
    if manifest != expected:
        raise EvidenceManifestError("evidence manifest does not equal repository evidence")
    return manifest


def verify_attested_builds(manifest: Mapping[str, Any]) -> None:
    artifacts = {
        item["artifactType"]: item["rawSha256"]
        for item in manifest["implementations"]["externalArtifacts"]
    }
    with tempfile.TemporaryDirectory(prefix="oac-evidence-build-") as temporary:
        root = Path(temporary)
        wheels: list[Path] = []
        for index in range(2):
            output = root / f"wheel-{index}"
            completed = subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(output)],
                cwd=ROOT,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if completed.returncode != 0:
                raise EvidenceManifestError("wheel attestation replay failed")
            matches = list(output.glob("*.whl"))
            if len(matches) != 1:
                raise EvidenceManifestError("wheel build did not emit one artifact")
            wheels.append(matches[0])
        if (
            _digest(wheels[0].read_bytes()) != _digest(wheels[1].read_bytes())
            or _digest(wheels[0].read_bytes()) != artifacts["python-wheel"]
        ):
            raise EvidenceManifestError("wheel build does not reproduce the attested digest")
        ledger_wheel = _load_json(REPLAY_ROOT / "installed-python-ledger.json", exact_jcs_lf=True)[
            "wheel"
        ]
        wheel_reader = _load_script_module("build_installed_python_ledger")
        expected_wheel = {key: item for key, item in ledger_wheel.items() if key != "fileName"}
        for wheel in wheels:
            observed_wheel = wheel_reader._read_wheel(wheel)["wheel"]
            observed_without_name = {
                key: item for key, item in observed_wheel.items() if key != "fileName"
            }
            if observed_without_name != expected_wheel:
                raise EvidenceManifestError(
                    "wheel build does not reproduce the installed ledger descriptor"
                )

        binaries: list[Path] = []
        go_root = ROOT / "implementations/go-supplier-v02-internal"
        for index in range(2):
            binary = root / f"go-{index}"
            completed = subprocess.run(
                ["go", "build", "-trimpath", "-o", str(binary), "."],
                cwd=go_root,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if completed.returncode != 0:
                raise EvidenceManifestError("Go attestation replay failed")
            binaries.append(binary)
        if (
            _digest(binaries[0].read_bytes()) != _digest(binaries[1].read_bytes())
            or _digest(binaries[0].read_bytes()) != artifacts["go-executable"]
        ):
            raise EvidenceManifestError("Go build does not reproduce the attested digest")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit", action="store_true")
    parser.add_argument("--verify-builds", action="store_true")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args(argv)
    try:
        if args.emit:
            manifest = build_manifest()
            sys.stdout.buffer.write(rfc8785.dumps(manifest) + b"\n")
        else:
            manifest = check(args.manifest)
        if args.verify_builds:
            verify_attested_builds(manifest)
        if not args.emit:
            print(manifest["digest"])
        return 0
    except (
        EvidenceManifestError,
        ValidationError,
        SchemaError,
        OSError,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        print(f"supplier evidence manifest failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
