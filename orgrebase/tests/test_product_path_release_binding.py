from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "product_path_release_binding_gate", ROOT / "scripts" / "goai_gate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def current_product_path_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real wheel/HTTP run per session; never rewrite release evidence."""
    evidence = tmp_path_factory.mktemp("product-path-current")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "workspace_product_path_blackbox.py"),
            "--output",
            str(evidence / "product-path-blackbox.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return evidence


def test_current_source_real_http_bundle_passes_strict_release_binding(
    current_product_path_bundle: Path,
) -> None:
    gate = _load_gate()
    result = gate.verify_product_path_blackbox(
        ROOT, evidence_root=current_product_path_bundle
    )
    assert result["status"] == "PASS", result
    assert result["wheel_rebuild"] == "PASS"
    assert result["evaluator_replay"] == "PASS"
    assert result["gate_attack_summary"] == {"total": 2, "rejected": 2, "accepted": 0}
    report = json.loads(
        (current_product_path_bundle / "product-path-blackbox.json").read_text(encoding="utf-8")
    )
    assert report["case_summary"] == {"total": 12, "passed": 12, "failed": 0}
    assert report["mutation_summary"]["killed"] == report["mutation_summary"]["total"] == 11
    assert report["integrity_attack_summary"]["rejected"] == 5
    assert report["execution"]["uvicorn_processes_started"] == 15
    assert report["execution"]["real_process_restarts"] == 3
    assert report["runtime_contract"] == "orgrebase.product-path-runtime-contract.v2"


def _load_evaluator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "current_product_path_evaluator", ROOT / "scripts" / "workspace_product_path_evaluator.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("location,marker", [
    ("runner", "orgrebase.workspace-state.v999"),
    ("quote_export", None),
    ("evidence_export", "orgrebase.workspace-state.v999"),
])
def test_runtime_contract_rejects_missing_unknown_and_mixed_markers(
    current_product_path_bundle: Path, location: str, marker: str | None,
) -> None:
    observations = json.loads((current_product_path_bundle / "product-path-observations.json").read_text())
    target = observations["cases"][0] if location == "runner" else observations["oracle_surface"][location]
    target["workspace_state_schema_version"] = marker
    runtime, failures = _load_evaluator()._runtime_contract(observations)
    assert runtime == "UNSUPPORTED"
    assert failures == ["RUNTIME_CONTRACT_MARKER_MISMATCH"]


@pytest.mark.parametrize("attack,expected_failure", [
    ("registered_value", "CHANGE_EVENT_REGISTRATION_BINDING_INVALID"),
    ("registered_base", "CHANGE_EVENT_REGISTRATION_BINDING_INVALID"),
    ("missing_source", "TOOL_INITIAL_SOURCE_SET_INVALID"),
    ("quote_business_value", "QUOTE_FIELD_MISMATCH"),
])
def test_current_runtime_rejects_resealed_business_and_registration_evidence(
    current_product_path_bundle: Path, attack: str, expected_failure: str,
) -> None:
    evaluator = _load_evaluator()
    observations = json.loads((current_product_path_bundle / "product-path-observations.json").read_text())
    surface = copy.deepcopy(observations["oracle_surface"])
    evidence = surface["evidence_export"]
    corpus = ROOT / "benchmark" / "product-path-v0.3-task-intake-bound"
    gold = json.loads((corpus / "evaluator" / "gold.json").read_text())
    gold["_source_bound_contract"] = json.loads((corpus / "BENCHMARK-MIGRATION.json").read_text())["source_bound_contract"]
    gold["_runtime_contract"] = evaluator.CURRENT_RUNTIME_CONTRACT
    assert evaluator._product_oracle(surface, gold) == []
    if attack.startswith("registered_"):
        event = evidence["change_events"][0]
        if attack == "registered_value":
            event["proposal"]["payload"]["canonical_value"] = "2099-01-01"
            event["proposal"]["digest"] = evaluator._product_digest(
                {key: val for key, val in event["proposal"].items() if key not in {"digest", "state"}}
            )
        else:
            event["base_digest"] = "sha256:" + "f" * 64
        evaluator._reseal_export(event)
        envelope = evidence["change_registration_events"][0]
        envelope["payload"]["event_digest"] = event["digest"]
        envelope["event_digest"] = evaluator._product_digest(
            {key: val for key, val in envelope.items() if key != "event_digest"}
        )
    elif attack == "missing_source":
        snapshot = evidence["formation_graph_snapshot"]
        snapshot["edges"] = [edge for edge in snapshot["edges"]
                             if edge["provider_ref"] != "claim:product.enterprise_plan@v4"]
        evaluator._reseal_export(snapshot)
    else:
        for exported in (surface["quote_export"], evidence):
            quote = exported["quote"]
            quote["payload"]["product_plan"] = "Unauthorized contract tier"
            quote["digest"] = evaluator._product_digest(
                {key: val for key, val in quote.items() if key not in {"digest", "state"}}
            )
            evaluator._reseal_export(exported)
    evaluator._reseal_export(evidence)
    assert expected_failure in evaluator._product_oracle(surface, gold)


def _copy_wheel_build_inputs(destination: Path) -> None:
    """Copy the declared wheel inputs, excluding unrelated private evidence."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    paths = {
        "pyproject.toml",
        "README.md",
        ".gitignore",
        *config["project"]["license-files"],
        *wheel["packages"],
        *wheel["force-include"],
    }
    for relative in sorted(paths):
        source = ROOT / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def test_source_change_cannot_borrow_the_prechange_wheel_evidence(
    current_product_path_bundle: Path, tmp_path: Path
) -> None:
    gate = _load_gate()
    isolated_root = tmp_path / "isolated-wheel-source"
    _copy_wheel_build_inputs(isolated_root)
    wheel = current_product_path_bundle / "product-path-wheel.whl"
    assert gate._rebuild_product_path_wheel(isolated_root, wheel) == (True, None)
    source = isolated_root / "src" / "orgrebase" / "workflow.py"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n# Isolated source-binding mutation probe.\n",
        encoding="utf-8",
    )
    assert gate._rebuild_product_path_wheel(isolated_root, wheel) == (
        False,
        "WHEEL_REBUILD_DIGEST_MISMATCH",
    )


def test_missing_external_evidence_bundle_fails_closed(tmp_path: Path) -> None:
    result = _load_gate().verify_product_path_blackbox(ROOT, evidence_root=tmp_path)
    assert result["status"] == "FAIL"
    assert "MISSING:product-path-wheel.whl" in result["failures"]
