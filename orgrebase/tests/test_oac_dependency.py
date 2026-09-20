from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from orgrebase.workspace.oac_wire import build_oac_public_source_manifest, locate_oac_root
from scripts.verify_oac_dependency import verify_dependency


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(root: Path) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Dependency Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Dependency fixture")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, str]:
    source = locate_oac_root()
    manifest, _ = build_oac_public_source_manifest(source)
    for entry in manifest["files"]:
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / entry["path"], target)
    _git(tmp_path, "init", "-q")
    return tmp_path, _commit(tmp_path)


def test_clean_checkout_must_match_both_git_commit_and_admitted_source(checkout) -> None:
    root, revision = checkout
    result = verify_dependency(root, revision=revision)
    assert result["status"] == "PASS"
    assert result["verified_git_revision"] == revision

    path = root / "src/oac/sealed.py"
    path.write_bytes(path.read_bytes() + b"\n# changed implementation\n")
    with pytest.raises(ValueError, match="OAC_DEPENDENCY_CHECKOUT_DIRTY"):
        verify_dependency(root, revision=revision)
    replacement = _commit(root)
    with pytest.raises(ValueError, match="OAC_DEPENDENCY_COMMIT_MISMATCH"):
        verify_dependency(root, revision=revision)
    with pytest.raises(ValueError, match="OAC_DEPENDENCY_NOT_ADMITTED"):
        verify_dependency(root, revision=replacement)


@pytest.mark.parametrize("revision", ["", "main", "v0.3.0a0", "abc123"])
def test_ci_dependency_rejects_mutable_or_incomplete_revision(checkout, revision) -> None:
    root, _ = checkout
    with pytest.raises(ValueError, match="OAC_DEPENDENCY_COMMIT_REQUIRED"):
        verify_dependency(root, revision=revision)


def test_local_source_verification_does_not_claim_a_clean_git_revision() -> None:
    result = verify_dependency(locate_oac_root())
    assert result["status"] == "PASS"
    assert result["verified_git_revision"] is None
