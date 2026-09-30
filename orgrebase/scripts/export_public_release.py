#!/usr/bin/env python3
"""Export a public iteration from canonical source and check exact source parity.

The existing GitHub snapshot policy selects product files. No file contents,
version strings or licenses are rewritten by this command. SOURCE-MANIFEST.json
belongs beside the three upload trees, not inside a product or GitHub root.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import build_source_snapshot as snapshot
except ModuleNotFoundError:  # Direct invocation from the workspace root.
    import build_source_snapshot as snapshot

SCHEMA_VERSION = "orgrebase.public-source-parity.v1"
MANIFEST_NAME = "SOURCE-MANIFEST.json"
COMPONENTS = ("orgrebase", "oac-spec")
UPLOAD_TREES = ("github-root", *COMPONENTS)
GITHUB_ROOT_FILES = (
    ".gitignore",
    ".python-version",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/reuse.yml",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".github/workflows/docs.yml",
    "CODE_OF_CONDUCT.md",
    "COMMERCIAL-LICENSE.md",
    "COMMUNITY.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "LICENSES.md",
    "Makefile",
    "NOTICE.md",
    "README.md",
    "README.zh-CN.md",
    "SECURITY.md",
)


def _path_without_symlinks(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise snapshot.SnapshotError("PUBLIC_RELEASE_PATH_IS_SYMLINK")
    return absolute


def _workspace(path: Path) -> Path:
    root = _path_without_symlinks(path)
    if not root.is_dir():
        raise snapshot.SnapshotError("PUBLIC_RELEASE_WORKSPACE_NOT_DIRECTORY")
    for component in COMPONENTS:
        snapshot._validate_root(root / component, component.upper())
    return root


def _destination(workspace: Path, path: Path, *, creating: bool) -> Path:
    destination = _path_without_symlinks(path)
    for source in (workspace / "orgrebase", workspace / "oac-spec", workspace / ".github"):
        if destination == source or source in destination.parents or destination in source.parents:
            raise snapshot.SnapshotError("PUBLIC_RELEASE_OUTPUT_OVERLAPS_SOURCE")
    if creating and destination.exists():
        raise snapshot.SnapshotError("PUBLIC_RELEASE_OUTPUT_ALREADY_EXISTS")
    if not creating and not destination.is_dir():
        raise snapshot.SnapshotError("PUBLIC_RELEASE_NOT_DIRECTORY")
    return destination


def _scan_public_file(relative: str, raw: bytes) -> None:
    credential_errors, _ = snapshot._credential_scan(relative, raw)
    machine_errors, _, _ = snapshot._machine_local_path_scan(relative, raw)
    errors = [*credential_errors, *machine_errors]
    if errors:
        raise snapshot.SnapshotError(";".join(errors))
    snapshot._scan_nested_archive(relative, raw)


def _copy_root(workspace: Path, target: Path) -> None:
    target.mkdir(mode=0o755)
    for relative in GITHUB_ROOT_FILES:
        snapshot._validate_relative_path(PurePosixPath(relative))
        source = _path_without_symlinks(workspace / relative)
        if not source.is_file():
            raise snapshot.SnapshotError(f"PUBLIC_ROOT_FILE_MISSING:{relative}")
        before = source.stat()
        raw = source.read_bytes()
        after = source.stat()
        if not stat.S_ISREG(after.st_mode) or (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode
        ):
            raise snapshot.SnapshotError(f"PUBLIC_SOURCE_CHANGED_DURING_READ:{relative}")
        _scan_public_file(relative, raw)
        snapshot._write_regular_file(target / relative, raw, executable=bool(before.st_mode & 0o111))


def _root_exclusions(workspace: Path) -> list[dict[str, str]]:
    exclusions: list[dict[str, str]] = []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir(), key=lambda path: snapshot._sort_key(path.name)):
            relative = child.relative_to(workspace).as_posix()
            if relative in {*COMPONENTS, *GITHUB_ROOT_FILES}:
                continue
            if any(path.startswith(relative + "/") for path in GITHUB_ROOT_FILES):
                if child.is_symlink() or not child.is_dir():
                    raise snapshot.SnapshotError(f"PUBLIC_ROOT_DIRECTORY_UNSAFE:{relative}")
                visit(child)
                continue
            reason = {
                "spec": "internal_planning_and_acceptance_evidence",
                "可执行代码版本迭代": "archived_release_artifacts",
                ".git": "private_git_history_and_configuration",
                ".codex": "internal_agent_work_material",
                ".claude": "internal_agent_work_material",
            }.get(relative, "outside_formal_github_root_allowlist")
            exclusions.append({"origin": relative, "reason": reason})

    visit(workspace)
    return exclusions


def _upload_inventory(release: Path) -> tuple[dict[str, Any], ...]:
    files: list[dict[str, Any]] = []
    for component in UPLOAD_TREES:
        root = release / component
        if root.is_symlink() or not root.is_dir():
            raise snapshot.SnapshotError(f"PUBLIC_UPLOAD_TREE_MISSING_OR_UNSAFE:{component}")
        for entry in snapshot._inventory(root):
            files.append({**entry, "path": f"{component}/{entry['path']}"})
    return tuple(sorted(files, key=lambda entry: snapshot._sort_key(entry["path"])))


def _origin(path: str) -> str:
    return path.removeprefix("github-root/")


def _assert_origin_bytes(workspace: Path, inventory: tuple[dict[str, Any], ...]) -> None:
    for entry in inventory:
        origin = _origin(entry["path"])
        source = _path_without_symlinks(workspace / origin)
        if not source.is_file() or (
            snapshot._sha256_file(source) != entry["sha256"]
            or bool(source.stat().st_mode & 0o111) != entry["executable"]
        ):
            raise snapshot.SnapshotError(f"PUBLIC_SOURCE_BYTES_DIFFER:{origin}")


def _prepare(workspace: Path, target: Path) -> dict[str, Any]:
    target.mkdir(mode=0o755)
    _copy_root(workspace, target / "github-root")
    excluded: list[dict[str, str]] = []
    for component in COMPONENTS:
        result = snapshot._copy_component(
            workspace / component, target / component, component, profile="github"
        )
        excluded.extend(
            {"origin": f"{component}/{entry['path']}", "reason": entry["reason"]}
            for entry in result.excluded
        )
    excluded.extend(_root_exclusions(workspace))
    inventory = _upload_inventory(target)
    _assert_origin_bytes(workspace, inventory)
    for entry in inventory:
        _scan_public_file(_origin(entry["path"]), (target / entry["path"]).read_bytes())
    files = [{**entry, "origin": _origin(entry["path"])} for entry in inventory]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "policy": {
            "profile": "github",
            "rootFiles": list(GITHUB_ROOT_FILES),
            "productAllowlist": snapshot._release_allowlist_payload("github"),
            "contentTransformations": [],
        },
        "uploadTrees": list(UPLOAD_TREES),
        "files": files,
        "fileCount": len(files),
        "inventorySha256": snapshot._inventory_digest(files),
        "excluded": sorted(excluded, key=lambda entry: snapshot._sort_key(entry["origin"])),
        "claimBoundary": "Exact source bytes and executable bits for the admitted public files; not runtime, customer or production qualification.",
    }


def _assert_current_manifest(recorded: Any, current: dict[str, Any]) -> None:
    if not isinstance(recorded, dict) or set(recorded) != set(current):
        raise snapshot.SnapshotError("PUBLIC_SOURCE_MANIFEST_INVALID")
    # Exclusions describe the frozen source workspace. Cache or internal notes
    # may change later without changing the admitted public product.
    exclusions = recorded.get("excluded")
    if not isinstance(exclusions, list) or any(
        not isinstance(entry, dict)
        or set(entry) != {"origin", "reason"}
        or not isinstance(entry["origin"], str)
        or not isinstance(entry["reason"], str)
        or not entry["reason"]
        for entry in exclusions
    ):
        raise snapshot.SnapshotError("PUBLIC_SOURCE_MANIFEST_EXCLUSIONS_INVALID")
    for entry in exclusions:
        snapshot._validate_relative_path(PurePosixPath(entry["origin"]))
    frozen = json.dumps({key: value for key, value in recorded.items() if key != "excluded"},
                        sort_keys=True, separators=(",", ":"))
    actual = json.dumps({key: value for key, value in current.items() if key != "excluded"},
                        sort_keys=True, separators=(",", ":"))
    if frozen != actual:
        raise snapshot.SnapshotError("PUBLIC_SOURCE_MANIFEST_CURRENT_SOURCE_MISMATCH")


def export_release(workspace_root: Path, output: Path) -> dict[str, Any]:
    workspace = _workspace(workspace_root)
    destination = _destination(workspace, output, creating=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _path_without_symlinks(destination.parent)
    with tempfile.TemporaryDirectory(prefix=".public-release-", dir=destination.parent) as temporary:
        temporary_root = Path(temporary)
        prepared = temporary_root / "prepared"
        manifest = _prepare(workspace, prepared)
        current = _prepare(workspace, temporary_root / "current")
        _assert_current_manifest(manifest, current)
        (prepared / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _destination(workspace, destination, creating=True)
        # Exclusive creation prevents an existing iteration from being replaced,
        # including if it appeared while source validation was running.
        destination.mkdir(mode=0o755)
        for name in (*UPLOAD_TREES, MANIFEST_NAME):
            (prepared / name).rename(destination / name)
    return {"status": "PASS", "schemaVersion": SCHEMA_VERSION, "fileCount": manifest["fileCount"],
            "inventorySha256": manifest["inventorySha256"], "exactByteMatch": True}


def check_release(workspace_root: Path, release: Path) -> dict[str, Any]:
    workspace = _workspace(workspace_root)
    destination = _destination(workspace, release, creating=False)
    manifest_path = destination / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise snapshot.SnapshotError("PUBLIC_SOURCE_MANIFEST_MISSING_OR_UNSAFE")
    try:
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise snapshot.SnapshotError("PUBLIC_SOURCE_MANIFEST_INVALID") from exc
    with tempfile.TemporaryDirectory(prefix="orgrebase-public-parity-") as temporary:
        prepared = Path(temporary) / "current"
        current = _prepare(workspace, prepared)
        _assert_current_manifest(recorded, current)
        snapshot._compare_inventories(
            _upload_inventory(prepared), _upload_inventory(destination),
            reason="PUBLIC_RELEASE_TREE_MISMATCH",
        )
    return {"status": "PASS", "schemaVersion": SCHEMA_VERSION, "fileCount": current["fileCount"],
            "inventorySha256": current["inventorySha256"], "exactByteMatch": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--workspace", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--workspace", type=Path, required=True)
    check.add_argument("--release", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (export_release(args.workspace, args.output) if args.command == "build"
                  else check_release(args.workspace, args.release))
    except (snapshot.SnapshotError, OSError) as exc:
        print(f"PUBLIC_RELEASE_ERROR:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
