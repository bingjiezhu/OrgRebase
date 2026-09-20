from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from orgrebase.workspace.coalition import build_coalition_result_binding
from orgrebase.workspace.controlled_local import build_joint_otlp

ROOT = Path(__file__).resolve().parents[2]


def _runner() -> ModuleType:
    path = ROOT / "scripts/run_semifinal_closure.py"
    spec = importlib.util.spec_from_file_location("semifinal_closure_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verifier() -> ModuleType:
    path = ROOT / "scripts/verify_semifinal_closure.py"
    spec = importlib.util.spec_from_file_location("semifinal_closure_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mutations() -> ModuleType:
    path = ROOT / "scripts/verify_semifinal_mutations.py"
    spec = importlib.util.spec_from_file_location("semifinal_mutations", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _controlled_verifier() -> ModuleType:
    path = ROOT / "scripts/verify_controlled_local_evidence.py"
    spec = importlib.util.spec_from_file_location("controlled_local_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_retained_wheel_integrity_and_current_build_inputs_are_separate_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier()
    project = tmp_path / "project"
    project.mkdir()
    (project / "uv.lock").write_text("frozen dependency set\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("frozen project metadata\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "ROOT", project)
    pack = tmp_path / "pack"
    pack.mkdir()
    wheel = pack / "retained.whl"
    wheel.write_bytes(b"retained artifact bytes")
    summary = {"wheel_file": wheel.name, "wheel_sha256": verifier._file_digest(wheel)}
    properties = {
        "orgrebase.artifact.retained.whl.sha256": summary["wheel_sha256"].split(":", 1)[1],
        "orgrebase.artifact.retained.whl.bytes": str(wheel.stat().st_size),
        "orgrebase.uv-lock.sha256": hashlib.sha256((project / "uv.lock").read_bytes()).hexdigest(),
        "orgrebase.pyproject.sha256": hashlib.sha256((project / "pyproject.toml").read_bytes()).hexdigest(),
    }
    sbom = {"metadata": {"component": {"properties": [
        {"name": name, "value": value} for name, value in properties.items()
    ]}}}
    assert verifier._wheel_sbom_failures(pack, summary=summary, sbom=sbom) == []
    for name in ("uv.lock", "pyproject.toml"):
        original = (project / name).read_bytes()
        (project / name).write_bytes(original + b"current change\n")
        assert verifier._wheel_sbom_failures(pack, summary=summary, sbom=sbom) == [
            "WHEEL_CURRENT_BUILD_BINDING"
        ]
        assert verifier._current_build_binding(sbom)["status"] == "MISMATCH"
        assert verifier._wheel_sbom_failures(
            pack, summary=summary, sbom=sbom, require_current_build=False,
        ) == []
        (project / name).write_bytes(original)
    wheel.write_bytes(b"substituted artifact bytes")
    assert verifier._wheel_sbom_failures(
        pack, summary=summary, sbom=sbom, require_current_build=False,
    ) == ["WHEEL_SBOM_BINDING"]


def test_retained_wheel_still_requires_valid_recorded_build_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier()
    retained = ROOT / "evidence/semifinal-closure/latest"
    summary = json.loads((retained / "summary.json").read_bytes())
    sbom = json.loads((retained / "operations/operations/sbom.cdx.json").read_bytes())
    for item in sbom["metadata"]["component"]["properties"]:
        if item["name"] == "orgrebase.uv-lock.sha256":
            item["value"] = "unverifiable"
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    assert verifier._wheel_sbom_failures(
        retained, summary=summary, sbom=sbom, require_current_build=False,
    ) == ["WHEEL_SBOM_BUILD_DIGEST_INVALID"]


@pytest.fixture
def retained_quote_inputs(tmp_path: Path) -> tuple[Path, Path, dict]:
    pack = tmp_path / "parent"
    for relative in ("evidence-index.json", "quote-value/quote-value-receipt.json"):
        target = pack / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "evidence/semifinal-closure/latest" / relative, target)
    snapshot = tmp_path / "retained-inputs"
    shutil.copytree(ROOT / "evidence/semifinal-closure/supporting/quote-value-inputs", snapshot)
    receipt = json.loads((pack / "quote-value/quote-value-receipt.json").read_bytes())
    return pack, snapshot, receipt


def test_retained_quote_inputs_replay_after_current_input_drift(
    retained_quote_inputs: tuple[Path, Path, dict], tmp_path: Path,
) -> None:
    verifier = _verifier()
    pack, snapshot, receipt = retained_quote_inputs
    failures: list[str] = []
    source_root, binding = verifier._retained_quote_value_inputs(pack, snapshot, receipt, failures)
    assert failures == []
    assert binding["scope"] == "RETAINED_CONTENT_ADDRESSED_INPUTS"
    assert binding["source_count"] == 9
    assert binding["current_release_qualified"] is False
    current = tmp_path / "current"
    shutil.copytree(source_root, current)
    (current / "evidence/workspace/latest/evaluation-suite.json").write_text("{}\n")

    def replay(project: Path) -> dict:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/verify_quote_value_evidence.py"),
             "--receipt", str(pack / "quote-value/quote-value-receipt.json"),
             "--project-root", str(project)],
            capture_output=True, text=True, check=False,
        )
        return json.loads(result.stdout)

    assert replay(source_root)["status"] == "PASS"
    assert replay(current)["failures"] == ["SOURCE_FILE_DIGEST_MISMATCH:evaluation_suite"]


@pytest.mark.parametrize(
    "attack,marker",
    [
        ("missing", "FILE_SET"),
        ("extra", "FILE_SET"),
        ("changed_bytes", "SOURCE_BYTES"),
        ("source_symlink", "SYMLINK"),
        ("directory_symlink", "SYMLINK"),
        ("receipt_substitution", "RECEIPT_BINDING"),
        ("manifest_scope", "SCOPE"),
        ("manifest_digest", "MANIFEST_DIGEST"),
        ("path_escape", "RECEIPT_SOURCE_BINDING"),
        ("source_reorder", "RECEIPT_SOURCE_BINDING"),
        ("duplicate", "RECEIPT_SOURCE_BINDING"),
    ],
)
def test_retained_quote_input_snapshot_rejects_substitution(
    retained_quote_inputs: tuple[Path, Path, dict], tmp_path: Path, attack: str, marker: str,
) -> None:
    verifier = _verifier()
    pack, snapshot, receipt = retained_quote_inputs
    target = snapshot / "source-root/evidence/workspace/latest/evaluation-suite.json"
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if attack == "missing":
        target.unlink()
    elif attack == "extra":
        (snapshot / "unreviewed.json").write_text("{}")
    elif attack == "changed_bytes":
        target.write_text("{}")
    elif attack == "source_symlink":
        copy = tmp_path / "outside.json"
        target.rename(copy)
        target.symlink_to(copy)
    elif attack == "directory_symlink":
        moved = tmp_path / "outside-tree"
        target.parent.rename(moved)
        target.parent.symlink_to(moved, target_is_directory=True)
    elif attack == "receipt_substitution":
        (pack / "quote-value/quote-value-receipt.json").write_text("{}")
    else:
        if attack == "manifest_scope":
            manifest["current_release_qualified"] = True
        elif attack == "path_escape":
            manifest["sources"][0]["path"] = "../outside.json"
        elif attack == "source_reorder":
            manifest["sources"][0], manifest["sources"][1] = manifest["sources"][1], manifest["sources"][0]
        elif attack == "duplicate":
            manifest["sources"][1] = manifest["sources"][0]
        manifest["digest"] = verifier._digest({key: value for key, value in manifest.items() if key != "digest"})
        if attack == "manifest_digest":
            manifest["digest"] = "sha256:" + "0" * 64
        _write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="RETAINED_QUOTE_VALUE_INPUTS_" + marker):
        verifier._retained_quote_value_inputs(pack, snapshot, receipt, [])


def test_retained_quote_parent_drift_is_blocking_without_hiding_other_failures(
    retained_quote_inputs: tuple[Path, Path, dict],
) -> None:
    verifier = _verifier()
    pack, snapshot, receipt = retained_quote_inputs
    (pack / "evidence-index.json").write_text('{"pack_digest":"substituted"}\n')
    failures: list[str] = []
    source_root, _ = verifier._retained_quote_value_inputs(pack, snapshot, receipt, failures)
    assert source_root == snapshot / "source-root"
    assert failures == ["RETAINED_QUOTE_VALUE_INPUTS_PARENT_BINDING"]


def test_semifinal_input_scope_never_falls_back_to_current_inputs(tmp_path: Path) -> None:
    verifier = _verifier()
    with pytest.raises(SystemExit, match="RETAINED_QUOTE_VALUE_INPUTS_REQUIRED"):
        verifier.verify(tmp_path, require_current_build=False)
    with pytest.raises(SystemExit, match="RETAINED_INPUTS_IN_CURRENT_MODE"):
        verifier.verify(tmp_path, retained_quote_value_inputs=tmp_path / "snapshot")


@pytest.mark.parametrize("attack,marker", [("missing", "MISSING"), ("symlink", "SYMLINK"), ("inside_pack", "INSIDE_PARENT_PACK")])
def test_retained_snapshot_root_is_explicit_and_separate(
    retained_quote_inputs: tuple[Path, Path, dict], tmp_path: Path, attack: str, marker: str,
) -> None:
    verifier = _verifier()
    pack, snapshot, receipt = retained_quote_inputs
    if attack == "missing":
        snapshot = tmp_path / "missing"
    elif attack == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(snapshot, target_is_directory=True)
        snapshot = alias
    else:
        nested = pack / "unexpected-inputs"
        snapshot.rename(nested)
        snapshot = nested
    with pytest.raises(ValueError, match="RETAINED_QUOTE_VALUE_INPUTS_" + marker):
        verifier._retained_quote_value_inputs(pack, snapshot, receipt, [])


@pytest.mark.parametrize("historical", [False, True])
def test_mutation_runner_uses_same_explicit_scope_for_baseline_and_attacks(
    retained_quote_inputs: tuple[Path, Path, dict], monkeypatch: pytest.MonkeyPatch,
    historical: bool,
) -> None:
    runner = _mutations()
    pack, snapshot, _ = retained_quote_inputs
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0 if len(calls) == 1 else 1,
                               stdout='{"status":"PASS"}' if len(calls) == 1 else "",
                               stderr="" if len(calls) == 1 else "EXPECTED_SEMANTIC_REJECTION")

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "ATTACKS", (("controlled-attack", lambda root: None, ("EXPECTED_SEMANTIC_REJECTION",)),))
    lock = ROOT / "agentteams/historical/teamharness-v1.2.2.json"
    kwargs = {"require_current_build": False, "retained_quote_value_inputs": snapshot, "lock_path": lock} if historical else {}
    result = runner.verify_mutations(pack, **kwargs)
    assert result["attacks_rejected"] == 1
    assert result["verification_scope"] == ("RETAINED_ARTIFACT" if historical else "CURRENT_BUILD_INPUTS")
    assert len(calls) == 2 and calls[0][4:] == calls[1][4:]
    assert ("--retained-build" in calls[0]) is historical
    assert ("--retained-quote-value-inputs" in calls[0]) is historical
    assert "--checkout" not in calls[0]
    if historical:
        assert calls[0][calls[0].index("--lock") + 1] == str(lock)


@pytest.mark.parametrize(
    "unsafe",
    [
        ROOT,
        ROOT / "src",
        ROOT / "docs",
        ROOT / ".git",
        ROOT / "evidence",
        ROOT / "evidence/semifinal-closure",
        ROOT / "evidence/semifinal-closure/archive",
        ROOT / "evidence/semifinal-closure/failed",
        ROOT / "evidence/semifinal-closure/latest/nested",
    ],
)
def test_semifinal_runner_rejects_non_pack_output_targets(unsafe: Path) -> None:
    runner = _runner()
    with pytest.raises(runner.SemifinalClosureError, match="UNSAFE_OUTPUT_TARGET"):
        runner._validated_output_target(unsafe)


def test_semifinal_runner_accepts_one_direct_named_evidence_pack() -> None:
    runner = _runner()
    target = ROOT / "evidence/semifinal-closure/latest"
    output, output_root = runner._validated_output_target(target)
    assert output == target.resolve()
    assert output_root == (ROOT / "evidence/semifinal-closure").resolve()


def test_internal_test_harness_may_supply_an_explicit_isolated_root(
    tmp_path: Path,
) -> None:
    runner = _runner()
    allowed = tmp_path / "isolated-semifinal-evidence"
    output, output_root = runner._validated_output_target(
        allowed / "case-a",
        allowed_output_root=allowed,
    )
    assert output == (allowed / "case-a").resolve()
    assert output_root == allowed.resolve()


def test_semifinal_parent_verifier_rejects_host_specific_public_paths(
    tmp_path: Path,
) -> None:
    verifier = _verifier()
    portable = tmp_path / "portable.json"
    portable.write_text('{"workspaceDir":"PUBLIC_PATH_REDACTED"}', encoding="utf-8")
    leaked = tmp_path / "leaked.jsonl"
    leaked.write_text(
        '["mirror","/Users/example/private/stage","agentteams/shared"]\n',
        encoding="utf-8",
    )

    assert verifier._public_path_failures(tmp_path) == [
        "PUBLIC_HOST_PATH:leaked.jsonl:/Users/"
    ]


def test_semifinal_parent_verifier_rejects_reindexed_unreviewed_artifact(
    tmp_path: Path,
) -> None:
    verifier = _verifier()
    mutations = _mutations()
    retained = ROOT / "evidence/semifinal-closure/latest"
    copied = tmp_path / "reindexed-pack"
    shutil.copytree(retained, copied)

    summary = json.loads((copied / "summary.json").read_text(encoding="utf-8"))
    native = json.loads(
        (copied / "agentteams/lifecycle-receipt.json").read_text(encoding="utf-8")
    )
    coalition_path = copied / "agentteams/coalition-result-binding.json"
    if not coalition_path.is_file():
        coalition_path.write_text(
            json.dumps(
                build_coalition_result_binding(native),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    quote_shadow = copied / "quote-shadow"
    if not quote_shadow.is_dir():
        shutil.copytree(ROOT / "evidence/quote-shadow/latest", quote_shadow)
    assert verifier._publication_surface_failures(
        copied,
        summary=summary,
        native=native,
    ) == []

    mutations._mutate_unreviewed_publication_artifact(copied)
    index_failures: list[str] = []
    verifier._verify_index(copied, index_failures)

    assert index_failures == []
    assert verifier._publication_surface_failures(
        copied,
        summary=summary,
        native=native,
    ) == ["PUBLICATION_SURFACE_EXTRA:artifacts/unreviewed.bin"]


def test_semifinal_mutation_registry_includes_coalition_and_causal_attacks() -> None:
    names = [item[0] for item in _mutations().ATTACKS]

    assert len(names) == len(set(names)) == 9
    assert {
        "coalition-domain-result-substitution",
        "otlp-causal-order-substitution",
        "otlp-parent-substitution",
        "otlp-mixed-status-substitution",
    }.issubset(names)


def test_rehashed_coalition_substitution_still_fails_native_reconstruction(
    tmp_path: Path,
) -> None:
    mutations = _mutations()
    verifier = _verifier()
    native = json.loads(
        (
            ROOT
            / "evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json"
        ).read_text(encoding="utf-8")
    )
    coalition = build_coalition_result_binding(native)
    root = tmp_path / "coalition-pack"
    coalition_path = root / "agentteams/coalition-result-binding.json"
    _write_json(coalition_path, coalition)
    _write_json(root / "evidence-index.json", {"schema_version": "test.v1"})
    mutations._rehash_index(root)

    mutations._mutate_coalition_domain_result(root)

    mutated = json.loads(coalition_path.read_text(encoding="utf-8"))
    body = {
        key: value
        for key, value in mutated.items()
        if key != "coalition_digest"
    }
    assert mutated["coalition_digest"] == mutations._digest(body)
    assert mutated["coalition_digest"] != coalition["coalition_digest"]
    assert verifier._coalition_failures(native=native, coalition=mutated) == [
        "COALITION_NATIVE_RECONSTRUCTION"
    ]
    index_failures: list[str] = []
    verifier._verify_index(root, index_failures)
    assert index_failures == []


def _minimal_causal_pack(tmp_path: Path) -> Path:
    mutations = _mutations()
    root = tmp_path / "causal-pack"
    operations = root / "operations"
    bundle = build_joint_otlp(
        run_id="run:mutation:causal",
        organization_id="org:northstar",
        receipt_digest="sha256:" + "1" * 64,
        task_id="task:mutation",
        skill_digest="sha256:" + "2" * 64,
        layers={
            "SOURCE": "SUCCEEDED",
            "AGENTTEAMS": "ACCEPT",
            "TOOL": "SUCCEEDED",
            "SKILL": "CANARY",
            "TERMINAL": "COMPLETED",
        },
    )
    _write_json(operations / "observability/traces.otlp.json", bundle["traces"])
    _write_json(operations / "observability/logs.otlp.json", bundle["logs"])
    _write_json(operations / "evidence-index.json", {"schema_version": "test.v1"})
    operations_index = mutations._rehash_index(operations)
    _write_json(
        root / "verification/child-verifiers.json",
        {"operations": {"pack_digest": operations_index["pack_digest"]}},
    )
    _write_json(root / "evidence-index.json", {"schema_version": "test.v1"})
    mutations._rehash_index(root)
    return root


@pytest.mark.parametrize(
    ("mutation_name", "expected_marker"),
    [
        ("_mutate_otlp_causal_order", "OTLP_CAUSAL_ORDER"),
        ("_mutate_otlp_parent", "OTLP_CAUSAL_PARENT"),
        ("_mutate_otlp_mixed_status", "OTLP_CAUSAL_ORDER"),
    ],
)
def test_rehashed_causal_mutations_still_fail_semantic_validation(
    tmp_path: Path,
    mutation_name: str,
    expected_marker: str,
) -> None:
    mutations = _mutations()
    controlled = _controlled_verifier()
    root = _minimal_causal_pack(tmp_path)

    getattr(mutations, mutation_name)(root)

    traces = json.loads(
        (root / "operations/observability/traces.otlp.json").read_text(
            encoding="utf-8"
        )
    )
    logs = json.loads(
        (root / "operations/observability/logs.otlp.json").read_text(
            encoding="utf-8"
        )
    )
    assert expected_marker in controlled._causal_chain_failures(traces, logs)
    index_failures: list[str] = []
    _verifier()._verify_index(root, index_failures)
    assert index_failures == []
