from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import rfc8785

ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts/check_evolution_minimum_evidence.py"
MANIFEST_PATH = ROOT / "experiments/evolution-minimum/v0.1-seed-8/evidence-manifest.json"


def _load_module(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


CHECKER = _load_module(CHECKER_PATH, "_test_evolution_minimum_evidence")


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_bytes())
    assert isinstance(value, dict)
    return value


def test_versioned_evolution_evidence_is_current_closed_and_claim_limited() -> None:
    manifest = _manifest()
    assert MANIFEST_PATH.read_bytes() == rfc8785.dumps(manifest) + b"\n"
    detached = {key: item for key, item in manifest.items() if key != "digest"}
    assert manifest["digest"] == _digest(rfc8785.dumps(detached))
    assert CHECKER.check() == manifest
    assert manifest["coreKinds"] == [
        "OrganizationalDemand",
        "OutcomeCertificate",
        "SourceAdmissionReceipt",
    ]
    assert "ExecutionReceipt" in manifest["extensionRefKinds"]
    assert manifest["verification"]["executionProvenanceEmbedded"] is False
    assert manifest["claim"]["ceiling"] == (
        "single-reference-implementation-source-and-root-vector-closure"
    )


def test_resealed_evolution_evidence_tamper_is_rejected(tmp_path: Path) -> None:
    tampered = copy.deepcopy(_manifest())
    tampered["claim"]["ceiling"] = "enterprise-proven"
    tampered["digest"] = _digest(
        rfc8785.dumps({key: item for key, item in tampered.items() if key != "digest"})
    )
    path = tmp_path / "tampered.json"
    path.write_bytes(rfc8785.dumps(tampered) + b"\n")
    with pytest.raises(
        CHECKER.EvolutionEvidenceError,
        match="does not equal its versioned materials",
    ):
        CHECKER.check(path)


def test_evolution_module_stays_within_the_five_hundred_nonblank_line_gate() -> None:
    lines = (ROOT / "src/oac/evolution.py").read_text(encoding="utf-8").splitlines()
    assert sum(bool(line.strip()) for line in lines) <= 500


def test_previous_coordinate_is_preserved_but_cannot_be_current() -> None:
    old = ROOT / "experiments/evolution-minimum/v0.1-seed-7/evidence-manifest.json"
    with pytest.raises(
        CHECKER.EvolutionEvidenceError, match="does not equal its versioned materials"
    ):
        CHECKER.check(old)
    manifest = CHECKER.check()
    assert manifest["apiVersion"] == "oac.evolution.evidence/v0alpha3"
    assert manifest["coordinate"] == "oac.evolution.minimum/v0.1-seed-8"
    assert manifest["predecessor"]["manifest"]["rawSha256"] == _digest(old.read_bytes())
    assert manifest["predecessor"]["verificationClass"] == "EXACT_HISTORICAL_MATERIAL_PRESERVATION"

    assert len(manifest["predecessor"]["materials"]) == 60
    assert manifest["predecessor"]["capturedManifest"]["rawSha256"] == _digest(old.read_bytes())
    ancestors = manifest["predecessor"]["ancestors"]
    assert len(ancestors) == 6
    assert len(ancestors[0]["materials"]) == 56
    assert len(ancestors[1]["materials"]) == 54
    assert len(ancestors[2]["materials"]) == 53
    assert len(ancestors[3]["materials"]) == 49
    assert len(ancestors[4]["materials"]) == 41
    assert len(ancestors[5]["materials"]) == 21
    assert (
        ancestors[5]["manifest"]["path"]
        == "experiments/evolution-minimum/v0.1-seed-1/evidence-manifest.json"
    )


def test_default_provenance_correction_keeps_positive_and_strict_profile_controls_bound() -> None:
    entries = {entry["path"] for entry in CHECKER.check()["contractClosure"]["entries"]}
    assert {
        "tests/test_cli_evolution_admission.py",
        "tests/test_disposable_outcome_profile.py",
        "tests/fixtures/evolution/default-shared-observation.json",
        "tests/fixtures/evolution/README.md",
        "specs/009-proof-carrying-evolution-minimum-profile/default-provenance-compatibility.md",
    } <= entries


def test_seed7_manifest_and_original_guide_bodies_are_preserved() -> None:
    old = ROOT / "experiments/evolution-minimum/v0.1-seed-7/evidence-manifest.json"
    raw = old.read_bytes()
    assert _digest(raw) == (
        "sha256:54eac0dddf79651d41ea3a64beaceca3564f72c796389badd09967a4c019daf9"
    )
    assert (CHECKER.EVIDENCE_ROOT / "predecessor-manifest.json").read_bytes() == raw
    historical = json.loads(raw)
    entries = {entry["path"]: entry for entry in historical["contractClosure"]["entries"]}
    for path in (
        "docs/validation/EVOLUTION-SEED3-PUBLICATION.md",
        "docs/validation/HISTORICAL-EVIDENCE-VERSIONING.md",
    ):
        captured = (CHECKER.EVIDENCE_ROOT / "predecessor-materials" / path).read_bytes()
        assert _digest(captured) == entries[path]["rawSha256"]
        assert len(captured) == entries[path]["sizeBytes"]
        assert captured != (ROOT / path).read_bytes()


def _sandbox_checker(tmp_path: Path) -> ModuleType:
    copied = tmp_path / "repository"
    shutil.copytree(
        ROOT,
        copied,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".coverage",
            "dist",
            "build",
        ),
    )
    return _load_module(
        copied / "scripts/check_evolution_minimum_evidence.py", "_sandbox_evolution_evidence"
    )


@pytest.mark.parametrize("path", ("src/oac/added_module.py", "src/oac/new_package/added_module.py"))
def test_current_closure_cannot_exclude_a_new_python_module(tmp_path: Path, path: str) -> None:
    checker = _sandbox_checker(tmp_path)
    added = checker.ROOT / path
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text('"""An additional module must change current source identity."""\n')
    assert path in {entry["path"] for entry in checker.build_manifest()["sourceClosure"]["entries"]}
    with pytest.raises(
        checker.EvolutionEvidenceError, match="does not equal its versioned materials"
    ):
        checker.check()


@pytest.mark.parametrize(
    "path,message",
    (
        (
            "experiments/evolution-minimum/v0.1-seed-8/predecessor-materials/src/oac/models.py",
            "historical predecessor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-7/predecessor-materials/src/oac/models.py",
            "historical ancestor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-6/predecessor-materials/src/oac/models.py",
            "historical ancestor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-5/predecessor-materials/src/oac/models.py",
            "historical ancestor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-4/predecessor-materials/src/oac/models.py",
            "historical ancestor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-3/predecessor-materials/src/oac/models.py",
            "historical ancestor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-2/predecessor-materials/src/oac/models.py",
            "historical ancestor material mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-8/predecessor-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-7/predecessor-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-7/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-6/predecessor-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-6/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-5/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-4/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-3/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-2/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
        (
            "experiments/evolution-minimum/v0.1-seed-1/evidence-manifest.json",
            "historical predecessor identity mismatch",
        ),
    ),
)
def test_all_predecessor_generations_remain_byte_bound(
    tmp_path: Path, path: str, message: str
) -> None:
    checker = _sandbox_checker(tmp_path)
    target = checker.ROOT / path
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(checker.EvolutionEvidenceError, match=message):
        checker.check()


def test_predecessor_material_hardlinks_are_rejected(tmp_path: Path) -> None:
    checker = _sandbox_checker(tmp_path)
    original = checker.EVIDENCE_ROOT / "predecessor-materials/src/oac/models.py"
    (checker.ROOT / "hardlinked-material.py").hardlink_to(original)
    with pytest.raises(checker.EvolutionEvidenceError, match="single-link file"):
        checker.check()


def test_publish_is_once_only_and_preserves_old_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = _sandbox_checker(tmp_path)
    checker.MANIFEST_PATH.unlink()  # Remove only a copy of the newly generated test coordinate.
    old_paths = [
        checker.ROOT / f"experiments/evolution-minimum/v0.1-seed-{seed}/evidence-manifest.json"
        for seed in (1, 2, 3, 4, 5, 6, 7)
    ]
    originals = [path.read_bytes() for path in old_paths]
    monkeypatch.setattr(sys, "argv", ["check-evolution", "--publish-new"])
    assert checker.main() == 0
    published = checker.MANIFEST_PATH.read_bytes()
    assert checker.main() == 2
    assert checker.MANIFEST_PATH.read_bytes() == published
    assert [path.read_bytes() for path in old_paths] == originals


def test_failed_predecessor_validation_never_creates_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = _sandbox_checker(tmp_path)
    checker.MANIFEST_PATH.unlink()
    (checker.EVIDENCE_ROOT / "predecessor-manifest.json").write_bytes(b"{}")
    monkeypatch.setattr(sys, "argv", ["check-evolution", "--publish-new"])
    assert checker.main() == 2
    assert not checker.MANIFEST_PATH.exists()


@pytest.mark.parametrize(
    "path",
    (
        "docs/validation/EVOLUTION-SEED3-PUBLICATION.md",
        "docs/validation/HISTORICAL-EVIDENCE-VERSIONING.md",
    ),
)
@pytest.mark.parametrize("fault", ("missing", "modified"))
def test_invalid_historical_guide_capture_cannot_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, fault: str
) -> None:
    checker = _sandbox_checker(tmp_path)
    checker.MANIFEST_PATH.unlink()
    original = checker.ROOT / "experiments/evolution-minimum/v0.1-seed-7/evidence-manifest.json"
    original_bytes = original.read_bytes()
    captured = checker.EVIDENCE_ROOT / "predecessor-materials" / path
    if fault == "missing":
        captured.unlink()
    else:
        captured.write_bytes(captured.read_bytes() + b" ")
    monkeypatch.setattr(sys, "argv", ["check-evolution", "--publish-new"])
    assert checker.main() == 2
    assert not checker.MANIFEST_PATH.exists()
    assert original.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "path",
    (
        "docs/validation/EVOLUTION-SEED3-PUBLICATION.md",
        "docs/validation/HISTORICAL-EVIDENCE-VERSIONING.md",
    ),
)
def test_current_guide_change_requires_a_new_coordinate(tmp_path: Path, path: str) -> None:
    checker = _sandbox_checker(tmp_path)
    published = checker.MANIFEST_PATH.read_bytes()
    predecessor = checker.EVIDENCE_ROOT / "predecessor-materials" / path
    original_body = predecessor.read_bytes()
    current = checker.ROOT / path
    current.write_bytes(current.read_bytes() + b"\nCurrent guide changed.\n")
    with pytest.raises(
        checker.EvolutionEvidenceError, match="does not equal its versioned materials"
    ):
        checker.check()
    assert checker.MANIFEST_PATH.read_bytes() == published
    assert predecessor.read_bytes() == original_body
