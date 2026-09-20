from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from oac.canonical import parse_resource
from oac.models import OrganizationPlan, OrganizationSnapshot, SemanticChangeSet, Verdict
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generated_paths() -> list[Path]:
    paths = [
        *sorted((ROOT / "profiles" / "supplier-change" / "cases").glob("SC-*.json")),
        *sorted((ROOT / "profiles" / "supplier-change" / "witnesses").glob("*.json")),
        ROOT
        / "profiles"
        / "supplier-change"
        / "inputs"
        / "veracier-proc01-contextual.snapshot.json",
        ROOT
        / "profiles"
        / "supplier-change"
        / "inputs"
        / "veracier-proc01-truncated-contextual.snapshot.json",
        *sorted((ROOT / "tck" / "fixtures").glob("**/*.json")),
        ROOT / "tck" / "mutations" / "manifest.json",
        ROOT / "benchmark" / "matched-inputs.json",
        *sorted((ROOT / "benchmark" / "baselines").glob("*.json")),
    ]
    return sorted(set(paths))


def test_tck_manifest_has_no_dangling_fixture_references() -> None:
    manifest = _json(ROOT / "tck" / "manifest.json")
    assert manifest["apiVersion"] == "oac.dev/tck/v0alpha1"
    assert len(manifest["cases"]) >= 20  # type: ignore[arg-type]
    for case in manifest["cases"]:  # type: ignore[union-attr]
        keys = (
            ("resourceRef",)
            if case["operation"] == "validate"
            else ("snapshotRef", "changeRef", "planRef")
        )
        for key in keys:
            path = ROOT / case[key]
            assert path.is_file(), f"{case['id']} -> {key}: {path}"


def test_default_tck_command_passes_every_case() -> None:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "oac.cli", "tck"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["failed"] == 0
    assert result["passed"] == len(result["results"])
    assert result["passed"] == len(_json(ROOT / "tck" / "manifest.json")["cases"])


def test_fixture_generator_is_byte_repeatable() -> None:
    before = {str(path.relative_to(ROOT)): _sha256(path) for path in _generated_paths()}
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "tck/build_fixtures.py"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    after = {str(path.relative_to(ROOT)): _sha256(path) for path in _generated_paths()}
    assert after == before


def test_registered_mutations_reach_detection_threshold() -> None:
    manifest = _json(ROOT / "tck" / "mutations" / "manifest.json")
    records = manifest["records"]
    detected = 0
    for record in records:  # type: ignore[union-attr]
        snapshot = parse_resource((ROOT / record["snapshotRef"]).read_bytes(), verify_digest=True)
        change = parse_resource((ROOT / record["changeRef"]).read_bytes(), verify_digest=True)
        plan = parse_resource((ROOT / record["planRef"]).read_bytes(), verify_digest=True)
        assert isinstance(snapshot, OrganizationSnapshot)
        assert isinstance(change, SemanticChangeSet)
        assert isinstance(plan, OrganizationPlan)
        certificate = verify_plan(snapshot, change, plan)
        expected = set(record["expectedReasonCodes"])
        assert certificate.spec.verdict is Verdict.REJECT
        assert expected.issubset(certificate.spec.reason_codes)
        detected += 1

    assert detected == manifest["registeredMutationCount"]
    assert detected >= 8
    assert detected / len(records) >= manifest["targetDetectionThreshold"]  # type: ignore[arg-type,operator]


def test_source_tree_contains_no_downloaded_public_data_payload() -> None:
    forbidden_suffixes = {".pdf", ".parquet", ".tar", ".gz"}
    payloads = [
        path
        for root in (ROOT / "profiles", ROOT / "benchmark", ROOT / "tck")
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes
    ]
    assert payloads == []
