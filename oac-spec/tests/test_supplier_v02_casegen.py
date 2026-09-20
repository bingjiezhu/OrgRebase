from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator

from oac.sealed import admit_sealed_resource

ROOT = Path(__file__).resolve().parents[1]
CASEGEN_PATH = ROOT / "scripts/supplier_v02_casegen.py"
HARNESS_PATH = ROOT / "scripts/check_supplier_v02_parity.py"
RECIPE_PATH = ROOT / "ctk/generators/supplier-v02-seed-2.recipes.json"
CAPABILITY_PATH = (
    ROOT / "ctk/capabilities/supplier-v02-seed-2.capability-set.json"
)
HISTORICAL_CAPSULE_ROOT = ROOT / "experiments/supplier-v02-portability"


def _load_module(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


CASEGEN = _load_module(CASEGEN_PATH, "_test_supplier_v02_casegen")


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _reseal_artifact(value: dict[str, Any], digest_field: str) -> bytes:
    projection = {key: item for key, item in value.items() if key != digest_field}
    value[digest_field] = _digest(rfc8785.dumps(projection))
    return rfc8785.dumps(value) + b"\n"


def _frozen_cases() -> list[dict[str, Any]]:
    manifest = json.loads((HISTORICAL_CAPSULE_ROOT / "capsule.json").read_bytes())
    return [
        {
            "caseId": item["caseId"],
            "snapshot": (
                HISTORICAL_CAPSULE_ROOT / item["snapshot"]["path"]
            ).read_bytes(),
            "change": (
                HISTORICAL_CAPSULE_ROOT / item["change"]["path"]
            ).read_bytes(),
        }
        for item in manifest["cases"]
    ]


def test_public_recipe_and_capability_artifacts_validate_and_bind_content() -> None:
    pairs = (
        (
            ROOT / "ctk/schemas/DeterministicMutationRecipeSet.schema.json",
            RECIPE_PATH,
            "recipeSetDigest",
        ),
        (
            ROOT / "ctk/schemas/ProtocolCapabilitySet.schema.json",
            CAPABILITY_PATH,
            "capabilitySetDigest",
        ),
    )
    for schema_path, artifact_path, digest_field in pairs:
        schema = json.loads(schema_path.read_bytes())
        Draft202012Validator.check_schema(schema)
        artifact = json.loads(artifact_path.read_bytes())
        assert list(Draft202012Validator(schema).iter_errors(artifact)) == []
        claimed = artifact[digest_field]
        projection = {
            key: item for key, item in artifact.items() if key != digest_field
        }
        assert claimed == _digest(rfc8785.dumps(projection))
        assert artifact_path.read_bytes() == rfc8785.dumps(artifact) + b"\n"


def test_generator_replays_all_public_semantic_classes_deterministically() -> None:
    recipes = CASEGEN.admit_recipe_set(RECIPE_PATH.read_bytes())
    frozen = _frozen_cases()
    first = CASEGEN.generate_cases(recipes, frozen)
    second = CASEGEN.generate_cases(recipes, frozen)

    assert first == second
    assert len(first) == 24
    assert len({item["caseId"] for item in first}) == len(first)
    assert len({item["inputDigest"] for item in first}) == len(first)
    operations = {
        mutation["operation"]
        for case in recipes["cases"]
        for mutation in case["mutations"]
    }
    assert operations == {"append", "remove", "replace", "reverse"}
    required_classes = {
        "retracted-change-subject",
        "candidate-change-subject",
        "disputed-change-subject",
        "candidate-dependency-edge",
        "retracted-dependency-edge-ledger",
        "retracted-impact-rule-ledger",
        "retracted-unknown-duty-ledger",
        "retracted-complete-boundary-residual",
        "candidate-residual-authoritative-false-cut",
        "candidate-residual-without-false-cut",
        "candidate-downstream-source",
        "max-depth-one-frontier",
        "max-depth-two-continuation",
        "partial-implicit-boundary",
        "legacy-discovery-fallback",
        "contextual-discovery-aggregate",
        "unknown-duty-nonadmitted-role",
        "unreferenced-unknown-evaluation",
    }
    assert required_classes <= {item["semanticClass"] for item in first}

    for item in first:
        snapshot = admit_sealed_resource(item["snapshot"], "OrganizationSnapshot")
        change = admit_sealed_resource(item["change"], "SemanticChangeSet")
        assert item["inputDigest"] == _digest(
            item["snapshot"] + b"\x00" + item["change"]
        )
        assert snapshot.resource_digest == json.loads(item["snapshot"])["digest"]
        assert change.resource_digest == json.loads(item["change"])["digest"]


def test_recipe_admission_rejects_nonclosed_illegal_and_duplicate_cases() -> None:
    base = json.loads(RECIPE_PATH.read_bytes())

    nonclosed = json.loads(json.dumps(base))
    nonclosed["cases"][0]["mutations"][0]["unexpected"] = True
    with pytest.raises(CASEGEN.CaseGenerationError, match="not closed"):
        CASEGEN.admit_recipe_set(_reseal_artifact(nonclosed, "recipeSetDigest"))

    illegal_pointer = json.loads(json.dumps(base))
    illegal_pointer["cases"][0]["mutations"][0]["path"] = "/spec/~2invalid"
    with pytest.raises(CASEGEN.CaseGenerationError, match="illegal JSON Pointer"):
        CASEGEN.admit_recipe_set(
            _reseal_artifact(illegal_pointer, "recipeSetDigest")
        )

    duplicated = json.loads(json.dumps(base))
    duplicated["cases"][1]["caseId"] = duplicated["cases"][0]["caseId"]
    with pytest.raises(CASEGEN.CaseGenerationError, match="duplicate generated caseId"):
        CASEGEN.admit_recipe_set(_reseal_artifact(duplicated, "recipeSetDigest"))


def test_generator_rejects_missing_base_and_missing_pointer_target() -> None:
    missing_base = json.loads(RECIPE_PATH.read_bytes())
    missing_base["cases"][0]["baseCaseId"] = "SC-404"
    admitted_missing_base = CASEGEN.admit_recipe_set(
        _reseal_artifact(missing_base, "recipeSetDigest")
    )
    with pytest.raises(CASEGEN.CaseGenerationError, match="base case is missing"):
        CASEGEN.generate_cases(admitted_missing_base, _frozen_cases())

    missing_pointer = json.loads(RECIPE_PATH.read_bytes())
    missing_pointer["cases"][0]["mutations"][0]["path"] = "/spec/notThere"
    admitted_missing_pointer = CASEGEN.admit_recipe_set(
        _reseal_artifact(missing_pointer, "recipeSetDigest")
    )
    with pytest.raises(CASEGEN.CaseGenerationError, match="target is missing"):
        CASEGEN.generate_cases(admitted_missing_pointer, _frozen_cases())

    digest_drift = CASEGEN.admit_recipe_set(RECIPE_PATH.read_bytes())
    digest_drift["cases"][0]["publicSeed"] = "mutated-after-admission"
    with pytest.raises(CASEGEN.CaseGenerationError, match="not digest-bound"):
        CASEGEN.generate_cases(digest_drift, _frozen_cases())


def test_harness_casegen_import_is_path_anchored_from_arbitrary_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    harness = _load_module(HARNESS_PATH, "_test_path_anchored_supplier_harness")
    assert harness.CASEGEN_SOURCE == CASEGEN_PATH
    admitted = harness.admit_recipe_set(RECIPE_PATH.read_bytes())
    assert admitted["recipeSetDigest"] == (
        "sha256:6249d9247b215c8a8fe7dbdbb307877faf5f0c03f1e2f7b09bf081cffe5fd627"
    )


def test_request_ids_are_deterministic_opaque_tokens() -> None:
    harness = _load_module(HARNESS_PATH, "_test_opaque_supplier_harness")
    tokens = [harness._opaque_request_id(index) for index in range(1, 10)]
    assert tokens == [harness._opaque_request_id(index) for index in range(1, 10)]
    assert len(set(tokens)) == len(tokens)
    assert all(token.startswith("req-") and len(token) == 28 for token in tokens)
    assert all(
        marker not in token
        for token in tokens
        for marker in ("frozen", "generated", "case", "python", "go")
    )


def test_source_closure_excludes_cache_docs_tests_and_binds_executable_source(
    tmp_path: Path,
) -> None:
    harness = _load_module(HARNESS_PATH, "_test_source_closure_supplier_harness")

    python_root = tmp_path / "python-source"
    python_root.mkdir()
    production = python_root / "kernel.py"
    production.write_text("VALUE = 1\n", encoding="utf-8")
    python_baseline = harness._source_closure_digest([], python_root)
    (python_root / "README.md").write_text("first\n", encoding="utf-8")
    (python_root / "test_kernel.py").write_text("assert True\n", encoding="utf-8")
    cache = python_root / "__pycache__"
    cache.mkdir()
    (cache / "kernel.cpython-312.pyc").write_bytes(b"machine-specific")
    assert harness._source_closure_digest([], python_root) == python_baseline
    production.write_text("VALUE = 2\n", encoding="utf-8")
    assert harness._source_closure_digest([], python_root) != python_baseline

    go_root = tmp_path / "go-source"
    go_root.mkdir()
    (go_root / "go.mod").write_text("module example.invalid/kernel\n", encoding="utf-8")
    go_production = go_root / "main.go"
    go_production.write_text("package main\nfunc main() {}\n", encoding="utf-8")
    go_baseline = harness._source_closure_digest([], go_root)
    (go_root / "README.md").write_text("first\n", encoding="utf-8")
    (go_root / "main_test.go").write_text("package main\n", encoding="utf-8")
    (go_root / "independence-statement.json").write_text("{}\n", encoding="utf-8")
    assert harness._source_closure_digest([], go_root) == go_baseline
    go_production.write_text("package main\nfunc main() { println(1) }\n", encoding="utf-8")
    assert harness._source_closure_digest([], go_root) != go_baseline


def test_response_json_depth_limit_is_enforced() -> None:
    harness = _load_module(HARNESS_PATH, "_test_depth_supplier_harness")
    value: object = None
    for _ in range(harness.MAX_RESPONSE_JSON_DEPTH):
        value = [value]
    with pytest.raises(harness.HarnessFailure, match="maxResponseJsonDepth"):
        harness._enforce_json_depth(
            value,
        harness.MAX_RESPONSE_JSON_DEPTH,
        "adapter response",
    )


def test_root_unknown_forgery_mutant_is_materially_identified_and_rejected(
    tmp_path: Path,
) -> None:
    harness = _load_module(HARNESS_PATH, "_test_root_forgery_supplier_harness")
    capability_set = harness._admit_capability_set(CAPABILITY_PATH.read_bytes())
    recipes = harness.admit_recipe_set(RECIPE_PATH.read_bytes())
    _, frozen_evidence = harness.load_capsule()
    generated = harness.generate_variants(frozen_evidence, recipes)
    candidate = next(
        item for item in generated if item["caseId"] == "generated:candidate-root"
    )
    validator = Draft202012Validator(
        json.loads((ROOT / "schemas/ProfileDerivationReport.schema.json").read_bytes())
    )
    reference = harness._derive_observation(
        [harness.sys.executable, "-m", "oac.ctk_adapter_v2"],
        candidate["snapshot"],
        candidate["change"],
        harness._opaque_request_id(1),
        report_validator=validator,
    )
    mutant_path = tmp_path / "root_unknown_forgery.py"
    mutant_path.write_text(
        harness._root_forgery_mutant_script(
            capability_set,
            frozen_evidence,
            [{"case": candidate, "reference": reference}],
        ),
        encoding="utf-8",
    )
    mutant_command = [harness.sys.executable, str(mutant_path)]
    capabilities = harness._capabilities(
        mutant_command,
        harness._opaque_request_id(2),
        capability_set,
    )
    assert capabilities["implementationId"] == "oac.mutant.root-unknown-forgery"
    observed = harness._derive_observation(
        mutant_command,
        candidate["snapshot"],
        candidate["change"],
        harness._opaque_request_id(3),
        report_validator=validator,
    )
    assert observed["jcs"] != reference["jcs"]
    assert observed["response"]["result"]["report"]["rootApplicabilityUnknown"] is False
    assert _digest(mutant_path.read_bytes()).startswith("sha256:")
