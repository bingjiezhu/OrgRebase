#!/usr/bin/env python3
"""Write or verify the closed-world SHA-256 manifest for the 02-code bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "SOURCE-MANIFEST.sha256"
EXCLUDED_TOP_LEVEL = {"release-builds"}
BANNED_COMPONENTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "__MACOSX",
    "__pycache__",
    "dist",
    "node_modules",
}
BANNED_NAMES = {".coverage", ".DS_Store", ".nonce-ledger.json.lock", "nonce-ledger.json"}
BANNED_PREFIXES = (
    Path("orgrebase/evidence/agentteams/live-sources"),
    Path("orgrebase/evidence/latest/git-tool-repo"),
)


class ManifestError(ValueError):
    """The release tree or manifest violates the closed-world contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_banned(relative: Path) -> bool:
    return (
        relative.name in BANNED_NAMES
        or any(part in BANNED_COMPONENTS for part in relative.parts)
        or any(relative == prefix or prefix in relative.parents for prefix in BANNED_PREFIXES)
        or relative.suffix in {".pyc", ".pyo", ".sqlite", ".sqlite3"}
    )


def release_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    banned: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            banned.append(path.relative_to(root).as_posix())
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == MANIFEST_NAME or relative.parts[0] in EXCLUDED_TOP_LEVEL:
            continue
        if _is_banned(relative):
            banned.append(relative.as_posix())
            continue
        files.append(relative)
    if banned:
        raise ManifestError("BANNED_RELEASE_PATHS:" + ",".join(sorted(banned)))
    return tuple(files)


def write_manifest(root: Path) -> int:
    files = release_files(root)
    content = "".join(f"{_sha256(root / path)}  {path.as_posix()}\n" for path in files)
    (root / MANIFEST_NAME).write_text(content, encoding="utf-8")
    return len(files)


def _read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise ManifestError(f"MALFORMED_LINE:{line_number}") from exc
        candidate = Path(relative)
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or candidate.is_absolute()
            or ".." in candidate.parts
            or relative in entries
        ):
            raise ManifestError(f"INVALID_ENTRY:{line_number}")
        entries[relative] = digest
    return entries


def verify_manifest(root: Path) -> int:
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise ManifestError("MANIFEST_MISSING")
    expected = _read_manifest(manifest)
    actual_files = release_files(root)
    actual_names = {path.as_posix() for path in actual_files}
    expected_names = set(expected)
    if missing := sorted(expected_names - actual_names):
        raise ManifestError("MISSING_FILES:" + ",".join(missing))
    if extras := sorted(actual_names - expected_names):
        raise ManifestError("UNLISTED_FILES:" + ",".join(extras))
    drift = sorted(
        name for name, digest in expected.items() if _sha256(root / name) != digest
    )
    if drift:
        raise ManifestError("HASH_DRIFT:" + ",".join(drift))
    return len(expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        count = write_manifest(root) if args.write else verify_manifest(root)
    except ManifestError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": "PASS", "files": count, "mode": "write" if args.write else "verify"}))


if __name__ == "__main__":
    main()
