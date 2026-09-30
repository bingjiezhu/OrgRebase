from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import export_public_release as release


def _write(root: Path, relative: str, value: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    for relative in release.GITHUB_ROOT_FILES:
        _write(root, relative, "Formal public root.\n")
    _write(root, "orgrebase/src/runtime.py", "VALUE = 1\n")
    _write(root, "orgrebase/docs/ARCHITECTURE.md", "# Architecture\n")
    _write(root, "orgrebase/pyproject.toml", '[project]\nversion = "0.5.0b2"\n')
    _write(root, "orgrebase/.github/workflows/ci.yml", "name: CI\n")
    _write(root, "oac-spec/src/oac/runtime.py", "VALUE = 2\n")
    _write(root, "oac-spec/standard/contract.md", "# Standard\n")
    executable = _write(root, "orgrebase/scripts/entry.py", "print(1)\n")
    executable.chmod(0o755)
    return root


def test_export_and_check_preserve_exact_source_bytes_and_executable_bits(
    workspace: Path, tmp_path: Path,
) -> None:
    target = tmp_path / "iteration"
    result = release.export_release(workspace, target)
    verified = release.check_release(workspace, target)
    assert result == verified
    assert result["exactByteMatch"] is True
    manifest_text = (target / release.MANIFEST_NAME).read_text()
    assert str(workspace) not in manifest_text
    assert str(target) not in manifest_text
    manifest = json.loads(manifest_text)
    assert manifest["policy"]["contentTransformations"] == []
    assert manifest["uploadTrees"] == ["github-root", "orgrebase", "oac-spec"]
    assert manifest["fileCount"] == len(release.GITHUB_ROOT_FILES) + 7
    for item in manifest["files"]:
        assert (target / item["path"]).read_bytes() == (workspace / item["origin"]).read_bytes()
        assert bool((target / item["path"]).stat().st_mode & 0o111) == item["executable"]
    assert (target / "orgrebase/scripts/entry.py").stat().st_mode & 0o111
    second = tmp_path / "next-iteration"
    release.export_release(workspace, second)
    assert (target / release.MANIFEST_NAME).read_bytes() == (second / release.MANIFEST_NAME).read_bytes()


@pytest.mark.parametrize("relative", [
    "orgrebase/src/runtime.py",
    "orgrebase/docs/ARCHITECTURE.md",
    "orgrebase/pyproject.toml",
    "orgrebase/.github/workflows/ci.yml",
    "github-root/README.md",
    "github-root/.github/workflows/ci.yml",
    "oac-spec/src/oac/runtime.py",
])
def test_public_code_documents_metadata_and_ci_cannot_drift(
    workspace: Path, tmp_path: Path, relative: str,
) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    (target / relative).write_text("Changed only in public tree.\n")
    with pytest.raises(release.snapshot.SnapshotError, match="PUBLIC_RELEASE_TREE_MISMATCH"):
        release.check_release(workspace, target)


@pytest.mark.parametrize("mutation", ["missing", "extra", "executable"])
def test_missing_extra_and_changed_permissions_fail(
    workspace: Path, tmp_path: Path, mutation: str,
) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    if mutation == "missing":
        # Rename within the fixture instead of deleting a user's file.
        (target / "orgrebase/src/runtime.py").rename(tmp_path / "retained.py")
    elif mutation == "extra":
        _write(target, "github-root/overlay.md", "Untracked addition.\n")
    else:
        (target / "orgrebase/scripts/entry.py").chmod(0o644)
    with pytest.raises(release.snapshot.SnapshotError, match="PUBLIC_RELEASE_TREE_MISMATCH"):
        release.check_release(workspace, target)


@pytest.mark.parametrize("relative", [
    "orgrebase/src/runtime.py", "orgrebase/docs/ARCHITECTURE.md", "README.md",
])
def test_later_source_change_requires_a_new_iteration(
    workspace: Path, tmp_path: Path, relative: str,
) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    old_manifest = (target / release.MANIFEST_NAME).read_bytes()
    _write(workspace, relative, "New canonical source.\n")
    with pytest.raises(release.snapshot.SnapshotError, match="CURRENT_SOURCE_MISMATCH"):
        release.check_release(workspace, target)
    assert (target / release.MANIFEST_NAME).read_bytes() == old_manifest
    next_target = tmp_path / "next-iteration"
    release.export_release(workspace, next_target)
    release.check_release(workspace, next_target)


def test_newly_admitted_source_file_is_not_silently_omitted(workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    _write(workspace, "orgrebase/src/new_runtime.py", "VALUE = 3\n")
    with pytest.raises(release.snapshot.SnapshotError, match="CURRENT_SOURCE_MISMATCH"):
        release.check_release(workspace, target)


def test_private_runtime_and_work_material_are_excluded_with_reasons(
    workspace: Path, tmp_path: Path,
) -> None:
    excluded = {
        "spec/private.md": "internal_planning_and_acceptance_evidence",
        "orgrebase/.env": "not_in_release_allowlist",
        "orgrebase/src/local.sqlite3": "generated_or_database_file",
        "orgrebase/src/.cache/result.json": "generated_or_cache_directory",
        "orgrebase/docs/specs/research.md": "internal_or_future_documentation",
        "orgrebase/docs/REVIEW-READINESS.md": "internal_planning_or_review_document",
        "orgrebase/evidence/agentteams/private-sessions/session.json": "historical_archive_not_in_runtime_profile",
        ".github/workflows/internal.yml": "outside_formal_github_root_allowlist",
    }
    for relative in excluded:
        _write(workspace, relative, "Private fixture.\n")
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    manifest = json.loads((target / release.MANIFEST_NAME).read_text())
    assert len(manifest["files"]) == len(release.GITHUB_ROOT_FILES) + 7
    observed = manifest["excluded"]
    for relative, reason in excluded.items():
        assert any(
            item["reason"] == reason
            and (relative == item["origin"] or relative.startswith(item["origin"] + "/"))
            for item in observed
        )
    # Internal work can continue without manufacturing public source drift.
    _write(workspace, "spec/private.md", "Updated internal note.\n")
    release.check_release(workspace, target)


@pytest.mark.parametrize("location", ["orgrebase/iteration", "oac-spec/iteration", ".github/iteration"])
def test_output_inside_a_product_or_formal_root_tree_is_rejected(
    workspace: Path, location: str,
) -> None:
    with pytest.raises(release.snapshot.SnapshotError, match="OUTPUT_OVERLAPS_SOURCE"):
        release.export_release(workspace, workspace / location)


def test_existing_output_is_never_overwritten(workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    before = (target / release.MANIFEST_NAME).read_bytes()
    with pytest.raises(release.snapshot.SnapshotError, match="OUTPUT_ALREADY_EXISTS"):
        release.export_release(workspace, target)
    assert (target / release.MANIFEST_NAME).read_bytes() == before


def test_symlink_output_parent_and_source_are_rejected(workspace: Path, tmp_path: Path) -> None:
    link = tmp_path / "linked"
    link.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(release.snapshot.SnapshotError, match="PATH_IS_SYMLINK"):
        release.export_release(workspace, link / "new-iteration")
    with pytest.raises(release.snapshot.SnapshotError, match="PATH_IS_SYMLINK"):
        release.export_release(link, tmp_path / "iteration")
    source = workspace / "orgrebase/src/runtime.py"
    retained = tmp_path / "retained-runtime.py"
    source.rename(retained)
    source.symlink_to(retained)
    with pytest.raises(release.snapshot.SnapshotError, match="SYMLINK_REJECTED"):
        release.export_release(workspace, tmp_path / "iteration")


def test_destination_symlink_fails_current_check(workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    source = target / "orgrebase/src/runtime.py"
    source.rename(tmp_path / "retained.py")
    source.symlink_to(workspace / "orgrebase/src/runtime.py")
    with pytest.raises(release.snapshot.SnapshotError, match="INVENTORY_SYMLINK_REJECTED"):
        release.check_release(workspace, target)


def test_missing_formal_root_and_hidden_copy_transform_fail(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    formal = workspace / "NOTICE.md"
    formal.rename(tmp_path / "retained-notice.md")
    with pytest.raises(release.snapshot.SnapshotError, match="PUBLIC_ROOT_FILE_MISSING"):
        release.export_release(workspace, tmp_path / "missing-root")
    _write(workspace, "NOTICE.md", "Formal public root.\n")
    original = release.snapshot._copy_component

    def transforming_copy(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        if args[2] == "orgrebase":
            target = args[1]
            assert isinstance(target, Path)
            (target / "src/runtime.py").write_text("Rewritten during export.\n")
        return result

    monkeypatch.setattr(release.snapshot, "_copy_component", transforming_copy)
    target = tmp_path / "transformed"
    with pytest.raises(release.snapshot.SnapshotError, match="PUBLIC_SOURCE_BYTES_DIFFER"):
        release.export_release(workspace, target)
    assert not target.exists()


def test_manifest_tampering_cannot_authorize_an_overlay(workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "iteration"
    release.export_release(workspace, target)
    manifest_path = target / release.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["policy"]["contentTransformations"] = ["replace README"]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(release.snapshot.SnapshotError, match="CURRENT_SOURCE_MISMATCH"):
        release.check_release(workspace, target)


@pytest.mark.parametrize("sensitive", ["credential", "machine-path"])
def test_admitted_sensitive_content_fails_before_creating_an_iteration(
    workspace: Path, tmp_path: Path, sensitive: str,
) -> None:
    if sensitive == "credential":
        body = "Authorization: " + "Bearer " + "private-fixture-value"
        error = "CREDENTIAL_PATTERN"
    else:
        body = "/" + "Users/" + "fixture-owner/private/input.json"
        error = "MACHINE_LOCAL_PATH"
    _write(workspace, "orgrebase/docs/ARCHITECTURE.md", body + "\n")
    target = tmp_path / "sensitive-iteration"
    with pytest.raises(release.snapshot.SnapshotError, match=error):
        release.export_release(workspace, target)
    assert not target.exists()


def test_source_addition_during_export_is_detected_before_publishing(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = release.snapshot._copy_component
    calls = 0

    def concurrent_addition(*args: object, **kwargs: object) -> object:
        nonlocal calls
        result = original(*args, **kwargs)
        if args[2] == "orgrebase":
            calls += 1
            if calls == 1:
                _write(workspace, "orgrebase/src/late_runtime.py", "VALUE = 4\n")
        return result

    monkeypatch.setattr(release.snapshot, "_copy_component", concurrent_addition)
    target = tmp_path / "concurrent-iteration"
    with pytest.raises(release.snapshot.SnapshotError, match="CURRENT_SOURCE_MISMATCH"):
        release.export_release(workspace, target)
    assert not target.exists()


def test_an_iteration_created_during_export_is_never_replaced(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = release.snapshot._copy_component
    target = tmp_path / "race-iteration"

    def concurrent_output(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        if args[2] == "orgrebase" and not target.exists():
            _write(target, "retained.txt", "Another export owns this directory.\n")
        return result

    monkeypatch.setattr(release.snapshot, "_copy_component", concurrent_output)
    with pytest.raises(release.snapshot.SnapshotError, match="OUTPUT_ALREADY_EXISTS"):
        release.export_release(workspace, target)
    assert (target / "retained.txt").read_text() == "Another export owns this directory.\n"
    assert not (target / release.MANIFEST_NAME).exists()


def test_cli_runs_from_a_workspace_root_and_rejects_drift(workspace: Path, tmp_path: Path) -> None:
    script = Path(release.__file__).resolve()
    target = workspace / "可执行代码版本迭代" / "new"
    build = subprocess.run(
        [sys.executable, str(script), "build", "--workspace", str(workspace), "--output", str(target)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stderr
    assert json.loads(build.stdout)["exactByteMatch"] is True
    check = subprocess.run(
        [sys.executable, str(script), "check", "--workspace", str(workspace), "--release", str(target)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert check.returncode == 0, check.stderr
    _write(workspace, "orgrebase/docs/ARCHITECTURE.md", "Later public change.\n")
    failed = subprocess.run(
        [sys.executable, str(script), "check", "--workspace", str(workspace), "--release", str(target)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert failed.returncode == 2
    assert "CURRENT_SOURCE_MISMATCH" in failed.stderr
