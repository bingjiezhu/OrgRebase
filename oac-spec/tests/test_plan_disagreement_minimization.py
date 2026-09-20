from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def reducer():
    path = ROOT / "scripts/minimize_plan_verification_disagreements.py"
    specification = importlib.util.spec_from_file_location("_plan_reducer_test", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        specification.loader.exec_module(module)
    finally:
        sys.path[:] = previous
    return module


def inputs(reducer):
    snapshot = reducer.seal({"kind": "OrganizationSnapshot", "items": ["a", "b", "c"]})
    change = reducer.seal({"kind": "SemanticChangeSet", "items": [1, 2]})
    plan = reducer.seal({"kind": "OrganizationPlan", "spec": {
        "snapshotRef": {"digest": snapshot["digest"]},
        "changeRef": {"digest": change["digest"]}, "items": ["anchor", "extra"],
    }})
    return {"snapshot": snapshot, "change": change, "plan": plan}


def policy():
    return {"sutStatus": "COMPLETED", "verdict": "REJECT", "requiredReasonCodes": ["TARGET"],
            "forbiddenReasonCodes": ["FORBIDDEN"]}


def observations():
    return {"python": {"sutStatus": "COMPLETED", "result": {
        "verdict": "REJECT", "reasonCodes": ["EXTRA", "TARGET"]}},
        "go": {"sutStatus": "COMPLETED", "result": {
            "verdict": "REJECT", "reasonCodes": ["TARGET"]}}}


def incident():
    return {"disagreementId": "TEST", **{side + "Observation": value
                                        for side, value in observations().items()}}


def test_operators_are_deterministic_schema_limited_and_pointer_escaped(reducer):
    values = {"snapshot": {"z": 1, "a/b~": [{"optional": 1}, {"optional": 2}],
                           "union": {"optional": 3}, "digest": "excluded"},
              "change": {}, "plan": {}}
    schema = {"properties": {"digest": {}, "z": {}, "a/b~": {"type": "array", "items": {
        "$ref": "#/$defs/Entry"}}, "union": {"anyOf": [{"properties": {"optional": {}}}]}},
        "required": ["a/b~", "union"], "$defs": {"Entry": {"properties": {"optional": {}}}}}
    schemas = {"snapshot": schema, "change": {}, "plan": {}}
    result = reducer.operations(values, schemas)
    assert [item["pointer"] for item in result] == [
        "/a~1b~0/1", "/a~1b~0/1/optional", "/a~1b~0/0", "/a~1b~0/0/optional", "/z"]
    assert result == reducer.operations(values, schemas)
    assert result[0]["deletedValueDigest"] == reducer.digest(reducer.canonical({"optional": 2}))


@pytest.mark.parametrize("ref", ["https://example.org/schema", "#/$defs/Loop"])
def test_unknown_or_cyclic_schema_reference_fails(reducer, ref):
    root = {"$defs": {"Loop": {"$ref": "#/$defs/Loop"}}}
    with pytest.raises(reducer.ReproductionFailure, match="schema reference"):
        reducer._schema_at({"$ref": ref}, root)


def test_resealing_updates_only_matching_root_reference(reducer):
    original = inputs(reducer)
    snapshot_op = next(item for item in reducer.operations(original, {key: {} for key in original})
                       if item["resource"] == "snapshot")
    candidate = reducer.delete(original, snapshot_op)
    assert candidate is not None
    assert original["snapshot"]["items"] == ["a", "b", "c"]
    assert candidate["snapshot"]["items"] == ["a", "b"]
    assert candidate["snapshot"]["digest"] != original["snapshot"]["digest"]
    assert candidate["plan"]["spec"]["snapshotRef"]["digest"] == candidate["snapshot"]["digest"]
    assert candidate["change"] == original["change"]
    for resource in candidate.values():
        projection = {key: value for key, value in resource.items() if key != "digest"}
        assert resource["digest"] == reducer.digest(reducer.canonical(projection)[:-1])
    original["plan"]["spec"]["snapshotRef"]["digest"] = "sha256:" + "0" * 64
    original["plan"] = reducer.seal(original["plan"])
    mismatch = reducer.delete(original, snapshot_op)
    assert mismatch["plan"]["spec"]["snapshotRef"]["digest"] == "sha256:" + "0" * 64


def test_plan_deletion_preserves_roots_and_does_not_repair_identifiers(reducer):
    original = inputs(reducer)
    operation = next(item for item in reducer.operations(original, {key: {} for key in original})
                     if item["resource"] == "plan")
    result = reducer.delete(original, operation)
    assert result["snapshot"] == original["snapshot"]
    assert result["change"] == original["change"]
    assert result["plan"]["spec"]["items"] == ["anchor"]
    assert result["plan"]["spec"]["snapshotRef"] == original["plan"]["spec"]["snapshotRef"]


def test_stale_pointer_and_changed_value_do_not_delete_another_item(reducer):
    original = inputs(reducer)
    operation = reducer.operations(original, {key: {} for key in original})[0]
    changed = copy.deepcopy(original)
    changed["snapshot"]["items"][-1] = "different"
    assert reducer.delete(changed, operation) is None
    changed["snapshot"]["items"] = []
    assert reducer.delete(changed, operation) is None


@pytest.mark.parametrize("side", ["python", "go"])
@pytest.mark.parametrize("mutation", ["parse", "accept", "other-reason", "extra-reason", "forbidden"])
def test_preservation_cannot_substitute_parse_or_different_rejection(reducer, side, mutation):
    result = observations()
    if mutation == "parse":
        result[side] = {"sutStatus": "ERROR", "error": {"code": "PARSE_ERROR"}}
    elif mutation == "accept":
        result[side]["result"]["verdict"] = "ACCEPT"
    elif mutation == "other-reason":
        result[side]["result"]["reasonCodes"] = ["OTHER"]
    elif mutation == "extra-reason":
        result[side]["result"]["reasonCodes"].append("OTHER")
    else:
        result[side]["result"]["reasonCodes"].append("FORBIDDEN")
    assert not reducer.preserves(result, incident(), policy())


def test_minimization_replays_real_deletion_chain_and_exhaustive_terminal_sweep(reducer):
    original = inputs(reducer)
    schemas = {key: {} for key in original}
    seen = []

    def observe(values):
        seen.append(copy.deepcopy(values))
        result = observations()
        if "anchor" not in values["plan"]["spec"]["items"]:
            result["python"] = {"sutStatus": "ERROR", "error": {"code": "PARSE_ERROR"}}
        return result

    minimized, trace = reducer.minimize(original, schemas, incident(), policy(), observe)
    assert minimized["snapshot"]["items"] == []
    assert minimized["change"]["items"] == []
    assert minimized["plan"]["spec"]["items"] == ["anchor"]
    assert len(seen) == len(trace["trials"]) + 1
    certificate = trace["certificate"]
    final_operations = reducer.operations(minimized, schemas)
    terminal = [trace["trials"][index] for index in certificate["terminalTrialIndexes"]]
    assert [trial["operation"] for trial in terminal] == final_operations
    assert all(not trial["retained"] for trial in terminal)
    assert all(trial["parentInputs"] == reducer.input_descriptor(minimized) for trial in terminal)
    current = original
    for trial in trace["trials"]:
        assert trial["parentInputs"] == reducer.input_descriptor(current)
        candidate = reducer.delete(current, trial["operation"])
        assert trial["candidateInputs"] == reducer.input_descriptor(candidate)
        if trial["retained"]:
            current = candidate
    assert current == minimized
    assert certificate["globalMinimumClaimed"] is False


@pytest.mark.parametrize("limit", ["attempts", "seconds"])
def test_budget_exhaustion_cannot_publish_a_minimality_claim(reducer, limit):
    original = inputs(reducer)
    budget = {"max_attempts": 0} if limit == "attempts" else {"max_seconds": -1}
    with pytest.raises(reducer.ReproductionFailure, match="budget exhausted"):
        reducer.minimize(original, {key: {} for key in original}, incident(), policy(),
                         lambda _: observations(), **budget)


def test_original_incident_must_reproduce_before_any_reduction(reducer):
    with pytest.raises(reducer.ReproductionFailure, match="original diagnostic"):
        reducer.minimize(inputs(reducer), {}, incident(), policy(), lambda _: {
            "python": {"sutStatus": "ERROR"}, "go": {"sutStatus": "ERROR"}})


def test_accepted_optional_parent_deletion_skips_stale_descendants_only(reducer):
    original = inputs(reducer)
    schemas = {"snapshot": {"properties": {"items": {"items": {}}}}, "change": {}, "plan": {}}

    def observe(values):
        result = observations()
        if "anchor" not in values["plan"]["spec"]["items"]:
            result["go"] = {"sutStatus": "ERROR", "error": {"code": "PARSE_ERROR"}}
        return result

    minimized, trace = reducer.minimize(original, schemas, incident(), policy(), observe)
    assert "items" not in minimized["snapshot"]
    assert trace["trials"][0]["operation"]["kind"] == "optional-field"
    assert trace["trials"][0]["retained"]
    assert trace["passes"][0]["enumerated"] > len(trace["passes"][0]["trialIndexes"])
    assert trace["passes"][-1]["enumerated"] == len(trace["passes"][-1]["trialIndexes"])


def test_go_build_excludes_vcs_and_ambient_flags_without_mutating_runner(reducer, monkeypatch):
    captured = {}
    original_allowlist = reducer.parity.ENVIRONMENT_ALLOWLIST.copy()
    monkeypatch.setenv("GOFLAGS", "-untrusted-ambient-option")

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(reducer.subprocess, "run", run)
    directory, command = reducer._build_go()
    try:
        assert "-buildvcs=false" in captured["command"]
        assert "-trimpath" in captured["command"]
        assert "GOFLAGS" not in captured["env"]
        assert Path(captured["env"]["GOCACHE"]).parent == Path(directory.name)
        assert captured["timeout"] == 60
        assert command == [str(Path(directory.name) / "oac-go-plan-verifier")]
        assert original_allowlist == reducer.parity.ENVIRONMENT_ALLOWLIST
        assert os.environ["GOFLAGS"] == "-untrusted-ambient-option"
    finally:
        directory.cleanup()


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_go_build_failure_cleans_its_temporary_directory(reducer, monkeypatch, failure):
    directories = []

    def run(command, **kwargs):
        directories.append(Path(kwargs["env"]["GOCACHE"]).parent)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        return SimpleNamespace(returncode=1, stderr="build failed")

    monkeypatch.setattr(reducer.subprocess, "run", run)
    with pytest.raises((subprocess.TimeoutExpired, reducer.ReproductionFailure)):
        reducer._build_go()
    assert directories and not directories[0].exists()


@pytest.mark.parametrize("mutation", ["input", "observation", "certificate", "missing", "extra"])
def test_exact_replay_rejects_changed_evidence_even_with_resealed_manifest(reducer, mutation):
    expected = {"input.json": b"original", "trace.json": b"observations and certificate",
                "manifest.json": reducer.canonical({"manifestDigest": "original"})}
    actual = dict(expected)
    if mutation == "missing":
        del actual["input.json"]
    elif mutation == "extra":
        actual["unlisted"] = b"extra"
    else:
        actual["input.json" if mutation == "input" else "trace.json"] = mutation.encode()
    actual["manifest.json"] = reducer.canonical({
        "manifestDigest": reducer.digest(reducer.canonical({key: reducer.descriptor(raw)
                                                           for key, raw in actual.items()}))})
    with pytest.raises(reducer.ReproductionFailure, match=r"differs|differ"):
        reducer.verify_tree(actual, expected)


def test_check_is_readonly_and_generation_refuses_existing_output(reducer, tmp_path, monkeypatch):
    directory = tmp_path / "repro"
    directory.mkdir()
    (directory / "evidence.json").write_bytes(b"frozen")
    (directory / "materials/src/oac").mkdir(parents=True)
    (directory / "materials/src/oac/contract.py").write_bytes(b"current source")
    source = {"materials/src/oac/contract.py": b"current source"}
    monkeypatch.setattr(reducer, "_source_materials", lambda: {"src/oac/contract.py": b"current source"})
    before = (directory / "evidence.json").stat().st_mtime_ns
    monkeypatch.setattr(reducer, "generate", lambda: {**source, "evidence.json": b"frozen"})
    assert reducer.main(["--check", "--output", str(directory)]) == 0
    assert (directory / "evidence.json").stat().st_mtime_ns == before
    assert reducer.main(["--output", str(directory)]) == 1
    monkeypatch.setattr(reducer, "generate", lambda: {**source, "evidence.json": b"changed"})
    assert reducer.main(["--check", "--output", str(directory)]) == 1
    assert (directory / "evidence.json").read_bytes() == b"frozen"
    assert (directory / "evidence.json").stat().st_mtime_ns == before


@pytest.mark.parametrize("mutation", ["bytes", "missing", "extra"])
def test_source_drift_fails_before_building_or_replaying(reducer, tmp_path, monkeypatch, capsys, mutation):
    directory = tmp_path / "repro"
    material = directory / "materials/src/oac/contract.py"
    material.parent.mkdir(parents=True)
    material.write_bytes(b"current source")
    if mutation == "bytes":
        material.write_bytes(b"previous source")
    elif mutation == "missing":
        material.unlink()
    else:
        extra = directory / "materials/src/oac/nested/extra.py"
        extra.parent.mkdir()
        extra.write_bytes(b"extra source")
    frozen = reducer._read_tree(directory)
    monkeypatch.setattr(reducer, "_source_materials", lambda: {"src/oac/contract.py": b"current source"})

    def unexpected_generation():
        pytest.fail("known source drift must fail before generating any adapter observations")

    monkeypatch.setattr(reducer, "generate", unexpected_generation)
    assert reducer.main(["--check", "--output", str(directory)]) == 1
    assert "source preflight failed before replay" in capsys.readouterr().err
    assert reducer._read_tree(directory) == frozen


def test_generation_failure_leaves_no_partial_lineage(reducer, tmp_path, monkeypatch):
    def fail():
        raise reducer.ReproductionFailure("budget exhausted")

    monkeypatch.setattr(reducer, "generate", fail)
    target = tmp_path / "absent"
    assert reducer.main(["--output", str(target)]) == 1
    assert not target.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_artifact_reads_reject_special_files_without_blocking(reducer, tmp_path, kind):
    target = tmp_path / "unsafe"
    if kind in ("symlink", "hardlink"):
        source = tmp_path / "source"
        source.write_text("material")
        if kind == "symlink":
            target.symlink_to(source)
        else:
            os.link(source, target)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.mkdir()
    with pytest.raises((reducer.ReproductionFailure, OSError)):
        reducer._read_regular(target.resolve() if kind == "directory" else target)


@pytest.mark.parametrize("coordinate", ["v0.1-repro-1", "v0.1-repro-2", "v0.1-repro-3", "v0.1-repro-4", "v0.1-repro-5"])
def test_frozen_outputs_have_real_rejections_and_complete_terminal_certificates(reducer, coordinate):
    directory = ROOT / "experiments/plan-verification-portability" / coordinate
    if not directory.exists():
        pytest.fail("the three real-adapter reproductions must be generated before acceptance")
    manifest = json.loads((directory / "manifest.json").read_bytes())
    reducer.parity._verify_detached(manifest, "manifestDigest", "reproduction manifest")
    files = reducer._read_tree(directory)
    assert set(files) == {*manifest["artifacts"], "manifest.json"}
    for name, item in manifest["artifacts"].items():
        assert item == reducer.descriptor(files[name])
    for incident_id, expected_digest in reducer.INCIDENTS.items():
        incident_value = json.loads(files[f"{incident_id}/original-incident.json"])
        assert incident_value["incidentDigest"] == expected_digest
        requirement = json.loads(files[f"{incident_id}/requirement.json"])
        trace = json.loads(files[f"{incident_id}/trace.json"])
        assert reducer.preserves(trace["originalObservations"], incident_value, requirement)
        assert reducer.preserves(trace["minimizedObservations"], incident_value, requirement)
        terminal = [trace["trials"][index] for index in trace["certificate"]["terminalTrialIndexes"]]
        assert len(terminal) == trace["certificate"]["remainingOneStepDeletions"]
        assert all(not trial["retained"] for trial in terminal)
        assert all(not reducer.preserves(trial["observations"], incident_value, requirement)
                   for trial in terminal)
        current = {key: json.loads(files[f"{incident_id}/original/{key}.json"])
                   for key in reducer.RESOURCES}
        for trial in trace["trials"]:
            assert trial["parentInputs"] == reducer.input_descriptor(current)
            candidate = reducer.delete(current, trial["operation"])
            assert candidate is not None
            assert trial["candidateInputs"] == reducer.input_descriptor(candidate)
            assert trial["retained"] == reducer.preserves(trial["observations"], incident_value, requirement)
            if trial["retained"]:
                current = candidate
        for key in reducer.RESOURCES:
            assert reducer.canonical(current[key]) == files[f"{incident_id}/minimized/{key}.json"]
        schemas = {
            "snapshot": json.loads(files["materials/schemas/OrganizationSnapshot.schema.json"]),
            "change": json.loads(files["materials/schemas/SemanticChangeSet.schema.json"]),
            "plan": json.loads(files["original-contracts/schemas/OrganizationPlan.schema.json"]),
        }
        assert [trial["operation"] for trial in terminal] == reducer.operations(current, schemas)


def test_successor_keeps_original_manifest_and_full_material_tree(reducer):
    previous, raw = reducer._predecessor()
    assert previous["coordinate"] == "v0.1-repro-4"
    assert previous["manifestDigest"] == reducer.PREDECESSOR_DIGEST
    assert reducer.digest(raw) == reducer.PREDECESSOR_RAW_DIGEST
    assert previous["verifiedArtifacts"] == 90
    assert previous["ancestors"][0]["coordinate"] == "v0.1-repro-3"
    assert previous["ancestors"][0]["verifiedArtifacts"] == 90
    assert previous["ancestors"][0]["ancestors"][0]["coordinate"] == "v0.1-repro-2"
    assert previous["ancestors"][0]["ancestors"][0]["verifiedArtifacts"] == 90
    assert previous["ancestors"][0]["ancestors"][0]["ancestors"][0]["coordinate"] == "v0.1-repro-1"
    assert previous["ancestors"][0]["ancestors"][0]["ancestors"][0]["verifiedArtifacts"] == 82
    assert reducer.OUTPUT.name == "v0.1-repro-5"


@pytest.mark.parametrize("mutation", ["manifest", "material", "missing", "extra", "resealed"])
def test_current_successor_cannot_launder_changed_predecessor(reducer, monkeypatch, mutation):
    files = reducer._read_tree(ROOT / reducer.PREDECESSOR)
    material = "materials/src/oac/models.py"
    if mutation == "manifest":
        files["manifest.json"] += b" "
    elif mutation == "missing":
        del files[material]
    elif mutation == "extra":
        files["unlisted.txt"] = b"not historical"
    else:
        files[material] += b"\n# changed history\n"
        if mutation == "resealed":
            manifest = json.loads(files["manifest.json"])
            manifest["artifacts"][material] = reducer.descriptor(files[material])
            manifest.pop("manifestDigest")
            manifest["manifestDigest"] = reducer.digest(reducer.rfc8785.dumps(manifest))
            files["manifest.json"] = reducer.canonical(manifest)
    monkeypatch.setattr(reducer, "_read_tree", lambda _: files)
    with pytest.raises(reducer.ReproductionFailure, match="predecessor"):
        reducer._predecessor()


def test_source_closure_includes_nested_current_modules(reducer, tmp_path, monkeypatch):
    (tmp_path / "src/oac/nested").mkdir(parents=True)
    (tmp_path / "src/oac/nested/current.py").write_text("value = 1\n")
    (tmp_path / "implementations/go-plan-verifier-v01-internal").mkdir(parents=True)
    monkeypatch.setattr(reducer, "ROOT", tmp_path)
    monkeypatch.setattr(reducer, "_read_regular", lambda path: str(path).encode())
    assert "src/oac/nested/current.py" in reducer._source_materials()


@pytest.mark.parametrize("mutation", ["manifest", "material", "missing", "extra", "resealed"])
def test_history_verification_reaches_older_ancestors(reducer, monkeypatch, mutation):
    read_tree = reducer._read_tree
    previous = ROOT / "experiments/plan-verification-portability/v0.1-repro-1"
    files = read_tree(previous)
    material = "materials/src/oac/models.py"
    if mutation == "manifest":
        files["manifest.json"] += b" "
    elif mutation == "missing":
        del files[material]
    elif mutation == "extra":
        files["unlisted"] = b"added"
    else:
        files[material] += b"changed"
        if mutation == "resealed":
            manifest = json.loads(files["manifest.json"])
            manifest["artifacts"][material] = reducer.descriptor(files[material])
            manifest.pop("manifestDigest")
            manifest["manifestDigest"] = reducer.digest(reducer.rfc8785.dumps(manifest))
            files["manifest.json"] = reducer.canonical(manifest)
    monkeypatch.setattr(reducer, "_read_tree", lambda path: files if path == previous else read_tree(path))
    with pytest.raises(reducer.ReproductionFailure, match="predecessor"):
        reducer._predecessor()


@pytest.mark.parametrize("path", [
    "/tmp/history/manifest.json", "experiments/plan-verification-portability/../manifest.json",
    "experiments/plan-verification-portability/x/../v0.1-repro-1/manifest.json",
    "experiments/plan-verification-portability/x\\y/manifest.json",
    "experiments/plan-verification-portability/x\x00y/manifest.json",
])
def test_history_manifest_paths_cannot_escape_namespace(reducer, monkeypatch, path):
    def unexpected_read(_):
        pytest.fail("an invalid manifest path must fail before reading any tree")

    monkeypatch.setattr(reducer, "_read_tree", unexpected_read)
    with pytest.raises(reducer.ReproductionFailure, match="predecessor manifest path"):
        reducer._published_history(path, "sha256:" + "0" * 64, "sha256:" + "0" * 64)


def test_history_directory_ancestors_cannot_be_symlinks(reducer, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "experiments").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(reducer, "ROOT", root)
    with pytest.raises(reducer.ReproductionFailure, match="predecessor directory"):
        reducer._predecessor()


@pytest.mark.parametrize("limit", ["cycle", "depth", "bytes"])
def test_history_bounds_fail_closed(reducer, limit):
    path = reducer.PREDECESSOR + "/manifest.json"
    kwargs = {
        "cycle": {"seen": (path,)},
        "depth": {"seen": ("older",) * reducer.MAX_HISTORY_DEPTH},
        "bytes": {"consumed_bytes": reducer.MAX_TREE_BYTES},
    }[limit]
    with pytest.raises(reducer.ReproductionFailure, match="predecessor history"):
        reducer._published_history(path, reducer.PREDECESSOR_DIGEST, reducer.PREDECESSOR_RAW_DIGEST, **kwargs)


def test_same_history_rule_verifies_three_synthetic_generations(reducer, tmp_path, monkeypatch):
    monkeypatch.setattr(reducer, "ROOT", tmp_path)
    previous = None
    previous_raw = None
    for index in range(3):
        coordinate = f"unit-control-{index}"
        relative = f"experiments/plan-verification-portability/{coordinate}/manifest.json"
        directory = (tmp_path / relative).parent
        directory.mkdir(parents=True)
        artifacts = {"unit-control.txt": b"synthetic unit test; not published evidence"}
        manifest = {"kind": "FixedPlanDisagreementReproductions", "coordinate": coordinate}
        if previous is not None:
            manifest["predecessor"] = previous
            artifacts["predecessor-manifest.json"] = previous_raw
        manifest["artifacts"] = {name: reducer.descriptor(raw) for name, raw in artifacts.items()}
        manifest["manifestDigest"] = reducer.digest(reducer.rfc8785.dumps(manifest))
        raw_manifest = reducer.canonical(manifest)
        for name, raw in {**artifacts, "manifest.json": raw_manifest}.items():
            (directory / name).write_bytes(raw)
        previous, previous_raw = reducer._published_history(
            relative, manifest["manifestDigest"], reducer.digest(raw_manifest)
        )
    assert previous["ancestors"][0]["ancestors"][0]["coordinate"] == "unit-control-0"
    attacked = json.loads(previous_raw)
    attacked["predecessor"]["verifiedArtifacts"] += 1
    attacked.pop("manifestDigest")
    attacked["manifestDigest"] = reducer.digest(reducer.rfc8785.dumps(attacked))
    raw = reducer.canonical(attacked)
    (directory / "manifest.json").write_bytes(raw)
    with pytest.raises(reducer.ReproductionFailure, match="ancestor record"):
        reducer._published_history(relative, attacked["manifestDigest"], reducer.digest(raw))
