from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ctk/runner/src"))

from oac_ctk_runner import runner as runner_module  # noqa: E402
from oac_ctk_runner.bundle import Bundle, BundleError, load_bundle  # noqa: E402
from oac_ctk_runner.evidence import digest, observe_build, verify_result  # noqa: E402
from oac_ctk_runner.probes import run_probe  # noqa: E402
from oac_ctk_runner.runner import run_bundle  # noqa: E402

BUNDLE_PATH = ROOT / "ctk/bundles/foundation-boundaries-v0.2"
INPUTS = (ROOT / "src/oac/ctk_adapter.py", ROOT / "src/oac/resource_profile.py")
COMMAND = (sys.executable, "-m", "oac.ctk_adapter")


@pytest.fixture(scope="module")
def successor() -> Bundle:
    return load_bundle(BUNDLE_PATH)


@pytest.fixture(scope="module")
def reference_result(successor: Bundle) -> dict[str, Any]:
    return run_bundle(successor, COMMAND, build_inputs=INPUTS)


def test_frozen_successor_has_exact_target_denominators_and_preserves_legacy(successor: Bundle) -> None:
    assert len(successor.cases) == 153
    assert sum(case["testTarget"] == "SUT" for case in successor.cases) == 131
    assert sum(case["testTarget"] == "HARNESS" for case in successor.cases) == 22
    assert set(successor.requirement_set["required"]) == {case["caseId"] for case in successor.cases}
    assert successor.requirement_set["notScored"] == []
    previous = load_bundle(ROOT / "ctk/bundles/phase-a-v0.1")
    assert previous.digest == "sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526"
    current = {case["caseId"]: case for case in successor.cases}
    for old in previous.cases:
        assert current[old["caseId"]] == {**old, "testTarget": "SUT"}


def test_actual_reference_run_has_verifiable_complete_result(successor: Bundle, reference_result: dict[str, Any]) -> None:
    verify_result(reference_result, successor)
    assert reference_result["requiredPassed"] is True
    assert reference_result["summary"] == {"required": 153, "passed": 153, "failed": 0, "notScored": 0}
    assert reference_result["targetSummary"] == {
        "SUT": {"required": 131, "passed": 131, "failed": 0},
        "HARNESS": {"required": 22, "passed": 22, "failed": 0},
    }
    assert reference_result["disagreements"] == []
    assert len(reference_result["diagnostics"]) == 153
    for diagnostic in reference_result["diagnostics"]:
        if diagnostic["decodedResponse"] is not None:
            assert diagnostic["responseDigest"] == digest(diagnostic["decodedResponse"])
        assert diagnostic["stderrTextDigest"] == "sha256:" + hashlib.sha256(diagnostic["stderrText"].encode()).hexdigest()
    rows = reference_result["implementationBuild"]["sutInputs"]
    assert [row["digest"] for row in rows] == ["sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in INPUTS]


@pytest.mark.parametrize("mutation", ("digest", "resource", "case", "duplicate", "target", "request", "response", "summary", "invented-disagreement"))
def test_resealed_result_cannot_change_coverage_or_binding(successor: Bundle, reference_result: dict[str, Any], mutation: str) -> None:
    result = copy.deepcopy(reference_result)
    if mutation == "digest":
        result["implementationBuildDigest"] = "sha256:" + "0" * 64
    elif mutation == "resource":
        result["resourceProfileDigest"] = "sha256:" + "0" * 64
    elif mutation == "case":
        result["caseResults"].pop()
    elif mutation == "duplicate":
        result["caseResults"].append(result["caseResults"][0])
    elif mutation == "target":
        result["caseResults"][0]["testTarget"] = "HARNESS"
    elif mutation == "request":
        result["diagnostics"][0]["requestDigest"] = "sha256:" + "0" * 64
        result["diagnosticsDigest"] = digest(result["diagnostics"])
    elif mutation == "response":
        result["diagnostics"][0]["decodedResponse"] = {"unobserved": True}
        result["diagnosticsDigest"] = digest(result["diagnostics"])
    elif mutation == "summary":
        result["targetSummary"]["SUT"]["passed"] -= 1
    else:
        result["caseResults"][0]["caseOutcome"] = "INDETERMINATE"
        result["summary"]["passed"] -= 1
        result["summary"]["failed"] += 1
        result["targetSummary"]["SUT"]["passed"] -= 1
        result["targetSummary"]["SUT"]["failed"] += 1
        result["requiredPassed"] = False
    result["resultDigest"] = digest({key: value for key, value in result.items() if key != "resultDigest"})
    with pytest.raises(ValueError, match="SUCCESSOR_"):
        verify_result(result, successor)


@pytest.mark.parametrize("mutation", ("row-status", "row-stage", "row-elapsed", "diagnostic-elapsed",
    "response-score", "response-envelope", "response-status", "capability", "harness-observation"))
def test_resealed_case_must_match_admitted_observation_and_original_scorer(
        successor: Bundle, reference_result: dict[str, Any], mutation: str) -> None:
    result = copy.deepcopy(reference_result)
    case_id = "C0-CANON-001"
    if mutation == "harness-observation":
        case_id = next(case["caseId"] for case in successor.cases
                       if case["testTarget"] == "HARNESS" and case["input"]["probe"] == "response-crash")
    row = next(item for item in result["caseResults"] if item["caseId"] == case_id)
    diagnostic = next(item for item in result["diagnostics"] if item["caseId"] == case_id)
    if mutation == "row-status":
        row["sutStatus"] = "RESOURCE_EXHAUSTED"
    elif mutation == "row-stage":
        row["stage"] = "SCHEMA"
    elif mutation == "row-elapsed":
        row["elapsedMs"] += 1
    elif mutation == "diagnostic-elapsed":
        diagnostic["elapsedMs"] += 1
    elif mutation == "response-score":
        diagnostic["decodedResponse"]["result"]["digest"] = "sha256:" + "0" * 64
    elif mutation == "response-envelope":
        diagnostic["decodedResponse"]["requestId"] = "another-request"
    elif mutation == "response-status":
        diagnostic["sutStatus"] = row["sutStatus"] = "RESOURCE_EXHAUSTED"
    elif mutation == "capability":
        result["capabilityStatement"]["tracks"] = []
        result["capabilityStatementDigest"] = digest(result["capabilityStatement"])
    else:
        diagnostic["errorCode"] = "ADAPTER_START_FAILED"
    diagnostic["responseDigest"] = digest(diagnostic["decodedResponse"]) if diagnostic["decodedResponse"] is not None else None
    result["diagnosticsDigest"] = digest(result["diagnostics"])
    result["resultDigest"] = digest({key: value for key, value in result.items() if key != "resultDigest"})
    with pytest.raises(ValueError, match=r"SUCCESSOR_RESULT_(CASE_SCORE|RESPONSE_ADMISSION|PREREQUISITE_OBSERVATION)_INVALID"):
        verify_result(result, successor)


def test_actual_mutant_emits_unresolved_reproducible_disagreement(successor: Bundle) -> None:
    command = (sys.executable, str(ROOT / "tests/fixtures/ctk_mutant_adapter.py"))
    result = run_bundle(successor, command, build_inputs=(*INPUTS, ROOT / "tests/fixtures/ctk_mutant_adapter.py"))
    verify_result(result, successor)
    assert result["requiredPassed"] is False
    assert result["summary"]["failed"] > 0
    assert result["targetSummary"]["HARNESS"]["failed"] == 0
    assert result["disagreements"]
    for disagreement in result["disagreements"]:
        assert disagreement["status"] == "OPEN"
        assert disagreement["classification"] == "UNCLASSIFIED"
        assert disagreement["resolutionRef"] is None
        diagnostic = next(item for item in result["diagnostics"] if item["caseId"] == disagreement["caseId"])
        assert diagnostic["decodedResponse"]["result"]["result"] == "TRUE"
    wrong_stage = copy.deepcopy(result)
    record = wrong_stage["disagreements"][0]
    record["stage"] = "HARNESS" if record["stage"] != "HARNESS" else "SCHEMA"
    record["digest"] = digest({key: value for key, value in record.items() if key != "digest"})
    wrong_stage["disagreementsDigest"] = digest(wrong_stage["disagreements"])
    wrong_stage["resultDigest"] = digest({key: value for key, value in wrong_stage.items() if key != "resultDigest"})
    with pytest.raises(ValueError, match="SUCCESSOR_DISAGREEMENT_BINDING_INVALID"):
        verify_result(wrong_stage, successor)
    result["disagreements"][0]["status"] = "RESOLVED"
    result["disagreementsDigest"] = digest(result["disagreements"])
    result["resultDigest"] = digest({key: value for key, value in result.items() if key != "resultDigest"})
    with pytest.raises(ValueError, match="SUCCESSOR_"):
        verify_result(result, successor)


@pytest.mark.parametrize("mutation", ("missing-target", "unknown-target", "sut-harness", "harness-sut", "unknown-probe", "open-expect"))
def test_new_case_contract_rejects_ambiguous_targets(successor: Bundle, mutation: str) -> None:
    schema = json.loads((BUNDLE_PATH / "contracts/schemas/ConformanceCase.schema.json").read_bytes())
    case = copy.deepcopy(next(item for item in successor.cases if item["testTarget"] == ("HARNESS" if mutation in {"harness-sut", "unknown-probe", "open-expect"} else "SUT")))
    if mutation == "missing-target":
        case.pop("testTarget")
    elif mutation == "unknown-target":
        case["testTarget"] = "BOTH"
    elif mutation == "sut-harness":
        case["testTarget"] = "HARNESS"
    elif mutation == "harness-sut":
        case["testTarget"] = "SUT"
    elif mutation == "unknown-probe":
        case["input"]["probe"] = "execute-untrusted-command"
    else:
        case["expect"]["expectedFailure"] = True
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(case)


@pytest.mark.parametrize("kind", ("empty", "duplicate", "directory", "symlink", "fifo"))
def test_build_observation_rejects_missing_or_unsafe_material(tmp_path: Path, kind: str) -> None:
    file = tmp_path / "input"
    file.write_text("material")
    inputs = (file,)
    if kind == "empty":
        inputs = ()
    elif kind == "duplicate":
        inputs = (file, file)
    elif kind == "directory":
        inputs = (tmp_path,)
    else:
        linked = tmp_path / "link"
        if kind == "symlink":
            linked.symlink_to(file)
        else:
            os.mkfifo(linked)
        inputs = (linked,)
    with pytest.raises(ValueError, match="BUILD_INPUT"):
        observe_build(COMMAND, inputs)


def test_build_mutation_during_execution_invalidates_result(successor: Bundle, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    material = tmp_path / "sut.py"
    material.write_text("original")
    original = runner_module.invoke
    def changed(*args: Any, **kwargs: Any) -> Any:
        material.write_text("changed")
        return original(*args, **kwargs)
    monkeypatch.setattr(runner_module, "invoke", changed)
    # The real capability call is enough to prove the before/after boundary; a
    # bounded in-memory subset here is not presented as a published suite run.
    subset = copy.copy(successor)
    object.__setattr__(subset, "cases", ())
    object.__setattr__(subset, "requirement_set", {"required": [], "notScored": []})
    with pytest.raises(ValueError, match="BUILD_INPUT_CHANGED_DURING_RUN"):
        run_bundle(subset, COMMAND, build_inputs=(material,))


def test_probe_cannot_take_credit_for_preexisting_inventory_fault(successor: Bundle, tmp_path: Path) -> None:
    import shutil
    destination = tmp_path / "copied"
    shutil.copytree(BUNDLE_PATH, destination)
    (destination / "unexpected").write_text("not a declared mutation")
    altered = copy.copy(successor)
    object.__setattr__(altered, "root", destination)
    case = next(item for item in successor.cases if item["caseId"] == "H-BUNDLE-UNLISTED")
    with pytest.raises(BundleError, match="HARNESS_PROBE_BASELINE_CHANGED"):
        run_probe(altered, case)


def test_probe_cannot_repair_preexisting_duplicate_manifest_keys(successor: Bundle, tmp_path: Path) -> None:
    import shutil
    destination = tmp_path / "copied"
    shutil.copytree(BUNDLE_PATH, destination)
    manifest = destination / "bundle.json"
    value = manifest.read_text()
    manifest.write_text(value.replace('"bundleFormatVersion":', '"bundleFormatVersion": "discarded", "bundleFormatVersion":', 1))
    altered = copy.copy(successor)
    object.__setattr__(altered, "root", destination)
    case = next(item for item in successor.cases if item["caseId"] == "H-BUNDLE-DUPLICATE")
    with pytest.raises(BundleError, match="duplicate"):
        run_probe(altered, case)
