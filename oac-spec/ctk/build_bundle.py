#!/usr/bin/env python3
"""Build or check deterministic CTK bundle artifact ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

import rfc8785

ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parent
DEFAULT_BUNDLE = ROOT / "bundles" / "phase-a-v0.1"
PHASE_A_SCHEMA_NAMES = (
    "CTKBundle.schema.json",
    "CapabilityStatement.schema.json",
    "ConformanceCase.schema.json",
    "ConformanceResourceProfile.schema.json",
    "DisagreementRecord.schema.json",
    "RequirementSet.schema.json",
    "RunResult.schema.json",
)

# A CTK bundle must identify the normative bytes it tests, not merely name a
# mutable draft.  These generated copies are part of the artifact ledger and
# make the directory independently inspectable after it leaves this checkout.
CONTRACT_SOURCES = {
    "contracts/standard/oac-conformance-v0.1.md": (
        REPOSITORY_ROOT / "standard" / "oac-conformance-v0.1.md"
    ),
    "contracts/standard/oac-derived-identifiers-v0.1.md": (
        REPOSITORY_ROOT / "standard" / "oac-derived-identifiers-v0.1.md"
    ),
    "contracts/protocol/stdio-v1.md": ROOT / "protocol" / "stdio-v1.md",
    **{
        f"contracts/schemas/{name}": ROOT / "schemas" / name
        for name in PHASE_A_SCHEMA_NAMES
    },
}


def _media_type(path: str) -> str:
    if path == "requirements.json":
        return "application/vnd.oac.ctk.requirement-set+json"
    if path == "resource-profile.json":
        return "application/vnd.oac.ctk.resource-profile+json"
    if path.startswith("cases/") and path.endswith(".json"):
        return "application/vnd.oac.ctk.case+json"
    if path.startswith("contracts/schemas/") and path.endswith(".schema.json"):
        return "application/schema+json"
    if path.startswith("contracts/") and path.endswith(".md"):
        return "text/markdown"
    return "application/octet-stream"


def sync_contracts(bundle: Path, *, check: bool) -> None:
    """Materialize or verify the exact contract bytes pinned by the bundle."""

    for relative, source in CONTRACT_SOURCES.items():
        _require_regular_file(source, label="contract source")
        expected = source.read_bytes()
        target = bundle / relative
        _ensure_child_directory(bundle, target.parent, create=not check)
        if check:
            _require_regular_file(target, label="bundled contract")
            if target.read_bytes() != expected:
                raise ValueError(f"bundled contract drift: {target}")
            continue
        if target.exists() or target.is_symlink():
            _require_regular_file(target, label="bundled contract")
        _atomic_write(target, expected)


def expected_manifest(bundle: Path, *, coordinate: tuple[str, ...] | None = None) -> bytes:
    artifacts: list[dict[str, object]] = []
    casefold_paths: set[str] = set()
    for path in sorted(bundle.rglob("*")):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode):
            raise ValueError(f"bundle entries must not be symlinks: {path}")
        if stat.S_ISDIR(status.st_mode):
            continue
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise ValueError(f"bundle entries must be single-link regular files: {path}")
        if path == bundle / "bundle.json":
            continue
        relative = path.relative_to(bundle).as_posix()
        folded = relative.casefold()
        if folded in casefold_paths:
            raise ValueError(f"case-fold-colliding bundle path: {relative}")
        casefold_paths.add(folded)
        raw = path.read_bytes()
        artifacts.append(
            {
                "path": relative,
                "mediaType": _media_type(relative),
                "size": len(raw),
                "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
            }
        )
    projection: dict[str, object] = {
        "bundleFormatVersion": "oac.ctk.bundle/v0alpha1",
        "suiteId": "oac-phase-a",
        "suiteVersion": "0.1.0",
        "standardVersion": "oac-conformance/v0.1-draft.1",
        "profileId": "oac.phase-a",
        "profileVersion": "v0.1",
        "requirementSetRef": "requirements.json",
        "resourceProfileRef": "resource-profile.json",
        "artifacts": artifacts,
        "extensions": {
            "dev.oac.independenceClass": "code-independent-phase-a",
            "dev.oac.claimCeiling": "not-full-conformance",
        },
    }
    if coordinate is not None:
        fields = ("bundleFormatVersion", "suiteId", "suiteVersion", "standardVersion", "profileId", "profileVersion")
        if len(coordinate) != len(fields):
            raise ValueError("bundle coordinate must have six fields")
        projection.update(zip(fields, coordinate, strict=True))
    manifest = dict(projection)
    manifest["bundleDigest"] = (
        f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    )
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _require_bundle_root(bundle: Path) -> None:
    try:
        status = bundle.lstat()
    except OSError as exc:
        raise ValueError(f"bundle root must already exist: {bundle}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise ValueError(f"bundle root must be a non-symlink directory: {bundle}")


def _ensure_child_directory(bundle: Path, directory: Path, *, create: bool) -> None:
    try:
        relative = directory.relative_to(bundle)
    except ValueError as exc:
        raise ValueError(f"target escapes bundle root: {directory}") from exc
    current = bundle
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise ValueError(f"unsafe bundle directory component: {part}")
        current = current / part
        try:
            status = current.lstat()
        except FileNotFoundError:
            if not create:
                raise ValueError(f"bundle directory is missing: {current}") from None
            current.mkdir()
            status = current.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise ValueError(f"bundle directory must not be a symlink: {current}")


def _require_regular_file(path: Path, *, label: str) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    if status.st_nlink != 1:
        raise ValueError(f"{label} must not be hard-linked: {path}")


def _atomic_write(target: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    bundle = Path(os.path.abspath(args.bundle))
    try:
        _require_bundle_root(bundle)
        sync_contracts(bundle, check=args.check)
        expected = expected_manifest(bundle)
        target = bundle / "bundle.json"
        if args.check:
            _require_regular_file(target, label="bundle manifest")
            if target.read_bytes() != expected:
                raise ValueError(f"bundle manifest drift: {target}")
        else:
            if target.exists() or target.is_symlink():
                _require_regular_file(target, label="bundle manifest")
            _atomic_write(target, expected)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
