"""Pinned input verification and immutable experiment directories."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

PINS = Path(__file__).with_name("pins")
PROFILE = "oac.retail.cancellation-review/v0.1"
MANIFEST = "artifact-manifest.json"
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("PUBLIC_LAB_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    def invalid(value: str) -> None:
        raise ValueError("PUBLIC_LAB_NONFINITE_JSON:" + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid)


def load(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("PUBLIC_LAB_MATERIAL_INVALID:" + str(path))
    return decode(path.read_bytes())


def write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(encode(value))


def file_identity(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("PUBLIC_LAB_FILE_INVALID:" + str(path))
    return {"sha256": sha(path.read_bytes()).removeprefix("sha256:"), "bytes": path.stat().st_size}


def verify_source(root: Path) -> dict[str, Any]:
    """The shipped manifest, not a candidate-provided manifest, owns source identity."""
    pin = load(PINS / "tau-source.json")
    if (
        pin["repo"] != "https://github.com/sierra-research/tau2-bench"
        or pin["revision"] != "672227c6b6676edc20d57ea53b7000262aae77b9"
    ):
        raise ValueError("PUBLIC_LAB_SOURCE_COORDINATE_INVALID")
    if len(pin["files"]) != 270 or "LICENSE" not in pin["files"]:
        raise ValueError("PUBLIC_LAB_SOURCE_CLOSURE_INVALID")
    for name, identity in pin["files"].items():
        safe_path(root, name)
        if file_identity(root / name) != identity:
            raise ValueError("PUBLIC_LAB_SOURCE_FILE_MISMATCH:" + name)
    if "MIT License" not in (root / "LICENSE").read_text():
        raise ValueError("PUBLIC_LAB_LICENSE_MISMATCH")
    return {
        "repository": pin["repo"],
        "revision": pin["revision"],
        "manifest_sha256": sha((PINS / "tau-source.json").read_bytes()),
        "archive_sha256": pin["archive_sha256"],
        "files_verified": len(pin["files"]),
        "license": "MIT",
        "license_sha256": "sha256:" + pin["files"]["LICENSE"]["sha256"],
    }


def safe_path(root: Path, name: str) -> Path:
    part = PurePosixPath(name)
    if (
        part.is_absolute()
        or not part.parts
        or part.as_posix() != name
        or any(p in (".", "..") for p in part.parts)
        or "\\" in name
    ):
        raise ValueError("PUBLIC_LAB_UNSAFE_RELATIVE_PATH")
    value = root / name
    for index in range(1, len(part.parts) + 1):
        if root.joinpath(*part.parts[:index]).is_symlink():
            raise ValueError("PUBLIC_LAB_SYMLINK_INPUT")
    value.resolve().relative_to(root.resolve())
    return value


def inventory(root: Path) -> dict[str, Any]:
    paths = [p for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    result = {}
    total = 0
    for path in sorted(paths):
        name = path.relative_to(root).as_posix()
        if name == MANIFEST:
            continue
        safe_path(root, name)
        item = file_identity(path)
        total += item["bytes"]
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("PUBLIC_LAB_BUNDLE_TOO_LARGE")
        result[name] = item
    return result


def seal_directory(root: Path, kind: str) -> str:
    write(
        root / MANIFEST,
        {"schema_version": "orgrebase.public-outcome-artifacts.v1", "kind": kind, "files": inventory(root)},
    )
    return sha((root / MANIFEST).read_bytes())


def verify_directory(root: Path, expected_root: str, kind: str) -> dict[str, Any]:
    manifest = load(root / MANIFEST)
    if sha((root / MANIFEST).read_bytes()) != expected_root:
        raise ValueError("PUBLIC_LAB_ARTIFACT_ROOT_MISMATCH")
    if (
        manifest.get("schema_version") != "orgrebase.public-outcome-artifacts.v1"
        or manifest.get("kind") != kind
    ):
        raise ValueError("PUBLIC_LAB_ARTIFACT_SCHEMA_MISMATCH")
    if set(manifest) != {"schema_version", "kind", "files"} or encode(manifest["files"]) != encode(
        inventory(root)
    ):
        raise ValueError("PUBLIC_LAB_ARTIFACT_CONTENT_MISMATCH")
    return manifest
