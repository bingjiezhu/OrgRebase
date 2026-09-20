from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_oac_evolution_evidence.py"
PACK = ROOT / "evidence" / "oac-evolution" / "latest"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("independent_oac_evolution_evaluator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _seal_product(module: ModuleType, value: dict) -> None:
    value["digest"] = module._product({key: item for key, item in value.items() if key != "digest"})


def _seal_oac(module: ModuleType, value: dict) -> None:
    projection = {key: item for key, item in value.items() if key != "digest"}
    metadata = projection["metadata"]
    for field in ("ownerRef", "governanceRef", "effectiveFrom", "effectiveTo"):
        metadata.setdefault(field, None)
    metadata.setdefault("sourceRefs", [])
    value["digest"] = module._jcs(projection)


def _reindex(module: ModuleType, root: Path) -> None:
    path = root / "evidence-index.json"
    index = _read(path)
    for entry in index["entries"]:
        raw = (root / entry["artifact_ref"]).read_bytes()
        entry["sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    index["pack_digest"] = module._product(index["entries"])
    _write(path, index)


def _mutate_verdict(module: ModuleType, root: Path) -> None:
    decision_path = root / "runs/split-rejected/outcome-decision.json"
    decision = _read(decision_path)
    decision["verdict"] = "ACCEPT"
    _seal_product(module, decision)
    _write(decision_path, decision)
    certificate_path = root / "runs/split-rejected/outcome-certificate.json"
    certificate = _read(certificate_path)
    certificate["spec"]["verdict"] = "ACCEPT"
    _seal_oac(module, certificate)
    _write(certificate_path, certificate)


def _mutate_observation(module: ModuleType, root: Path) -> None:
    path = root / "runs/split-rejected/outcome-observation.json"
    observation = _read(path)
    fact = next(item for item in observation["facts"] if item["name"] == "qualification-evidence-check")
    fact["value"] = "qualification_record_approved"
    _seal_product(module, fact)
    _seal_product(module, observation)
    _write(path, observation)


def _mutate_authority(module: ModuleType, root: Path) -> None:
    path = root / "evolution/governance-decision.json"
    decision = _read(path)
    decision["actor_authority_ref"] = "authority:attacker"
    _seal_product(module, decision)
    _write(path, decision)


def _mutate_prepared_successor(module: ModuleType, root: Path) -> None:
    path = root / "evolution/proposal.json"
    proposal = _read(path)
    prepared = proposal["prepared_successors"]
    snapshot = json.loads(prepared["snapshot_payload_jcs"])
    snapshot["spec"]["nodes"] = [
        node for node in snapshot["spec"]["nodes"] if node["nodeType"] != "procedure-contract"
    ]
    prepared["snapshot_payload_jcs"] = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _seal_product(module, prepared)
    _seal_product(module, proposal)
    _write(path, proposal)


def _mutate_pointer(_module: ModuleType, root: Path) -> None:
    path = root / "evolution/pointer-transition.json"
    pointer = _read(path)
    pointer["after_rollback"]["version"] = "r2"
    _write(path, pointer)


def _mutate_quote(module: ModuleType, root: Path) -> None:
    fake = "sha256:" + "f" * 64
    summary_path = root / "summary.json"
    summary = _read(summary_path)
    summary["preliminary_regression"]["expected_quote_digest"] = fake
    summary["preliminary_regression"]["actual_quote_digest"] = fake
    _write(summary_path, summary)
    regression_path = root / "evolution/regression-evidence.json"
    regression = _read(regression_path)
    regression["source_refs"] = [
        f"quote-digest:{fake}" if item.startswith("quote-digest:") else item
        for item in regression["source_refs"]
    ]
    _seal_product(module, regression)
    _write(regression_path, regression)


MUTATIONS: tuple[tuple[str, Callable[[ModuleType, Path], None], str], ...] = (
    ("outcome_verdict", _mutate_verdict, "OUTCOME_INVALID:split-rejected"),
    ("observation", _mutate_observation, "OUTCOME_INVALID:split-rejected"),
    ("authority", _mutate_authority, "GOVERNANCE_AUTHORITY_INVALID"),
    ("prepared_successor", _mutate_prepared_successor, "PREPARED_SUCCESSOR_INVALID"),
    ("pointer", _mutate_pointer, "POINTER_LINEAGE_INVALID"),
    ("quote", _mutate_quote, "QUOTE_REGRESSION_INVALID"),
)


def test_evaluator_has_no_product_import_and_passes_in_independent_process() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name == "orgrebase" or name.startswith("orgrebase.") for name in imported)
    assert not any(name == "oac" or name.startswith("oac.") for name in imported)

    environment = {**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": ""}
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(PACK)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result == {
        "schema_version": "orgrebase.oac-evolution-independent-verification.v1",
        "status": "PASS",
        "artifact_count": 51,
        "event_count": 13,
        "pack_digest": "sha256:40b2f34f2542b8ae0d263f987dacae3fcf99af98dd6c2ac7809adb930ba67cd8",
        "product_imports": 0,
        "failures": [],
    }


@pytest.mark.parametrize(("name", "mutate", "expected_failure"), MUTATIONS)
def test_semantic_mutation_fails_after_attacker_reindexes_pack(
    tmp_path: Path,
    name: str,
    mutate: Callable[[ModuleType, Path], None],
    expected_failure: str,
) -> None:
    module = _module()
    root = tmp_path / name
    shutil.copytree(PACK, root)
    original_index = _read(root / "evidence-index.json")

    mutate(module, root)
    _reindex(module, root)
    reindexed = _read(root / "evidence-index.json")
    assert reindexed["pack_digest"] == module._product(reindexed["entries"])
    assert reindexed["pack_digest"] != original_index["pack_digest"]

    result = module.evaluate(root)
    assert result["status"] == "FAIL"
    assert expected_failure in result["failures"], result
