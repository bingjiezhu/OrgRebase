"""Validation of immutable, ledger-addressed CTK bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .published import SUCCESSOR_BUNDLE_DIGEST, SUCCESSOR_COORDINATE

_BOOTSTRAP_MAX_MANIFEST_BYTES = 1_048_576
_BOOTSTRAP_MAX_ARTIFACTS = 512
_BOOTSTRAP_MAX_BUNDLE_BYTES = 67_108_864
_BOOTSTRAP_MAX_JSON_DEPTH = 64
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

_FROZEN_COORDINATE = (
    "oac.ctk.bundle/v0alpha1",
    "oac-phase-a",
    "0.1.0",
    "oac-conformance/v0.1-draft.1",
    "oac.phase-a",
    "v0.1",
)
# This trust anchor is intentionally outside the content-addressed bundle to
# avoid self-reference.  It is updated only when the frozen suite is rebuilt
# and reviewed.  A future generic runner needs a signed/versioned registry.
_FROZEN_BUNDLE_DIGEST = "sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526"

_SCHEMA_ARTIFACTS = {
    "CTKBundle": "contracts/schemas/CTKBundle.schema.json",
    "RequirementSet": "contracts/schemas/RequirementSet.schema.json",
    "ConformanceResourceProfile": ("contracts/schemas/ConformanceResourceProfile.schema.json"),
    "ConformanceCase": "contracts/schemas/ConformanceCase.schema.json",
}


class BundleError(ValueError):
    """The harness cannot trust the supplied CTK bundle."""


@dataclass(frozen=True, slots=True)
class Bundle:
    root: Path
    manifest: dict[str, Any]
    requirement_set: dict[str, Any]
    resource_profile: dict[str, Any]
    cases: tuple[dict[str, Any], ...]
    artifact_digests: dict[str, str]
    result_schema: dict[str, Any] | None = None

    @property
    def digest(self) -> str:
        return str(self.manifest["bundleDigest"])

    @property
    def profile_coordinate(self) -> tuple[str, str]:
        return (str(self.manifest["profileId"]), str(self.manifest["profileVersion"]))


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BundleError(f"duplicate object key: {key}")
        value[key] = item
    return value


def _json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_duplicates,
            parse_constant=_reject_non_json_constant,
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise BundleError(f"invalid JSON artifact {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleError(f"JSON artifact must be an object: {label}")
    if _json_depth(value) > _BOOTSTRAP_MAX_JSON_DEPTH:
        raise BundleError(f"JSON artifact exceeds bootstrap depth: {label}")
    try:
        rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError, TypeError, ValueError) as exc:
        raise BundleError(f"JSON artifact is not RFC 8785 I-JSON: {label}") from exc
    return value


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _NOFOLLOW
_DIRECTORY_FLAGS = _FILE_FLAGS | getattr(os, "O_DIRECTORY", 0)


def _open_bundle_root(path: Path) -> int:
    if not _NOFOLLOW or os.open not in os.supports_dir_fd:
        raise BundleError("secure fd-anchored bundle loading is unavailable")
    try:
        descriptor = os.open(path, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise BundleError("bundle root must be a non-symlink directory") from exc
    status = os.fstat(descriptor)
    if not stat.S_ISDIR(status.st_mode):
        os.close(descriptor)
        raise BundleError("bundle root must be a non-symlink directory")
    return descriptor


def _open_directory_at(parent_descriptor: int, name: str, *, label: str) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise BundleError(f"{label} must be a non-symlink directory") from exc
    status = os.fstat(descriptor)
    if not stat.S_ISDIR(status.st_mode):
        os.close(descriptor)
        raise BundleError(f"{label} must be a non-symlink directory")
    return descriptor


def _read_regular_once_at(
    root_descriptor: int,
    path: PurePosixPath,
    *,
    label: str,
    maximum_bytes: int | None = None,
) -> tuple[bytes, os.stat_result]:
    directory_descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(path.parts[:-1], start=1):
            child_descriptor = _open_directory_at(
                directory_descriptor,
                component,
                label=f"directory {'/'.join(path.parts[:index])}",
            )
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
        try:
            descriptor = os.open(path.parts[-1], _FILE_FLAGS, dir_fd=directory_descriptor)
        except OSError as exc:
            raise BundleError(f"{label} must be a regular non-symlink file: {path}") from exc
    finally:
        os.close(directory_descriptor)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise BundleError(f"{label} must be a regular non-symlink file")
        if status.st_nlink != 1:
            raise BundleError(f"hard-linked {label} is forbidden")
        if maximum_bytes is not None and status.st_size > maximum_bytes:
            raise BundleError(f"{label} exceeds the bootstrap byte ceiling")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(status.st_size + 1)
        if len(raw) != status.st_size:
            raise BundleError(f"{label} changed while loading")
        return raw, status
    finally:
        os.close(descriptor)


def _physical_inventory(root_descriptor: int) -> set[str]:
    inventory: set[str] = set()
    pending: list[tuple[int, PurePosixPath]] = [(os.dup(root_descriptor), PurePosixPath())]
    try:
        while pending:
            directory_descriptor, prefix = pending.pop()
            try:
                try:
                    entries = list(os.scandir(directory_descriptor))
                except OSError as exc:
                    raise BundleError(f"cannot enumerate bundle directory: {prefix}") from exc
                for entry in entries:
                    relative_path = prefix / entry.name
                    relative = relative_path.as_posix()
                    try:
                        if entry.is_symlink():
                            raise BundleError(f"bundle entries must not be symlinks: {relative}")
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(
                                (
                                    _open_directory_at(
                                        directory_descriptor,
                                        entry.name,
                                        label=f"bundle directory {relative}",
                                    ),
                                    relative_path,
                                )
                            )
                        elif entry.is_file(follow_symlinks=False):
                            inventory.add(relative)
                        else:
                            raise BundleError(
                                f"bundle contains a non-regular physical entry: {relative}"
                            )
                    except OSError as exc:
                        raise BundleError(f"cannot inspect bundle entry: {relative}") from exc
            finally:
                os.close(directory_descriptor)
    finally:
        for descriptor, _ in pending:
            os.close(descriptor)
    return inventory


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _logical_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise BundleError("artifact path must be a non-empty string")
    if "\\" in value or "\x00" in value:
        raise BundleError(f"artifact path uses a non-portable separator: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise BundleError(f"unsafe artifact path: {value}")
    if path.as_posix() != value:
        raise BundleError(f"artifact path is not normalized: {value}")
    return path


def load_bundle(root: Path) -> Bundle:
    supplied_root = Path(root)
    root_descriptor = _open_bundle_root(supplied_root)
    try:
        return _load_bundle_anchored(Path(os.path.abspath(supplied_root)), root_descriptor)
    finally:
        os.close(root_descriptor)


def _load_bundle_anchored(root: Path, root_descriptor: int) -> Bundle:
    manifest_raw, _ = _read_regular_once_at(
        root_descriptor,
        PurePosixPath("bundle.json"),
        label="bundle manifest",
        maximum_bytes=_BOOTSTRAP_MAX_MANIFEST_BYTES,
    )
    manifest = _json_bytes(manifest_raw, label="bundle.json")
    required_manifest_fields = {
        "bundleFormatVersion",
        "suiteId",
        "suiteVersion",
        "standardVersion",
        "profileId",
        "profileVersion",
        "requirementSetRef",
        "resourceProfileRef",
        "artifacts",
        "extensions",
        "bundleDigest",
    }
    if set(manifest) != required_manifest_fields:
        raise BundleError("bundle manifest fields do not match the frozen format")
    if manifest["bundleFormatVersion"] not in {"oac.ctk.bundle/v0alpha1", "oac.ctk.bundle/v0alpha2"}:
        raise BundleError("unsupported bundleFormatVersion")
    coordinate = tuple(
        manifest[key]
        for key in (
            "bundleFormatVersion",
            "suiteId",
            "suiteVersion",
            "standardVersion",
            "profileId",
            "profileVersion",
        )
    )
    if coordinate not in {_FROZEN_COORDINATE, SUCCESSOR_COORDINATE}:
        raise BundleError(f"unsupported frozen suite coordinate: {coordinate!r}")
    if not isinstance(manifest["extensions"], dict):
        raise BundleError("extensions must be an object")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise BundleError("artifacts must be a non-empty array")
    if len(artifacts) > _BOOTSTRAP_MAX_ARTIFACTS:
        raise BundleError("artifact ledger exceeds the bootstrap file ceiling")

    # Reject symlinked directories and special files before opening any
    # ledger path, so an intermediate component cannot redirect the read.
    actual_files = _physical_inventory(root_descriptor)

    ledger_paths: set[str] = set()
    casefold_paths: set[str] = set()
    artifact_digests: dict[str, str] = {}
    artifact_media_types: dict[str, str] = {}
    artifact_bytes: dict[str, bytes] = {}
    artifact_sizes: dict[str, int] = {}
    case_paths: list[str] = []
    observed_bundle_bytes = 0
    for entry in artifacts:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "mediaType",
            "size",
            "digest",
        }:
            raise BundleError("artifact ledger entry has invalid fields")
        logical = _logical_path(entry["path"])
        logical_text = logical.as_posix()
        if logical_text in ledger_paths:
            raise BundleError(f"duplicate artifact path: {logical_text}")
        folded = logical_text.casefold()
        if folded in casefold_paths:
            raise BundleError(f"case-fold-colliding artifact path: {logical_text}")
        ledger_paths.add(logical_text)
        casefold_paths.add(folded)
        raw, artifact_stat = _read_regular_once_at(
            root_descriptor, logical, label=f"artifact {logical_text}"
        )
        size = entry["size"]
        digest = entry["digest"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BundleError(f"artifact size is invalid: {logical_text}")
        if size != artifact_stat.st_size:
            raise BundleError(f"artifact size mismatch: {logical_text}")
        observed_bundle_bytes += size
        if observed_bundle_bytes > _BOOTSTRAP_MAX_BUNDLE_BYTES:
            raise BundleError("bundle exceeds the bootstrap byte ceiling")
        if len(raw) != size:
            raise BundleError(f"artifact changed while loading: {logical_text}")
        actual_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if digest != actual_digest:
            raise BundleError(f"artifact digest mismatch: {logical_text}")
        if not isinstance(entry["mediaType"], str) or not entry["mediaType"]:
            raise BundleError(f"artifact mediaType is invalid: {logical_text}")
        artifact_digests[logical_text] = actual_digest
        artifact_media_types[logical_text] = entry["mediaType"]
        artifact_bytes[logical_text] = raw
        artifact_sizes[logical_text] = size
        if entry["mediaType"] == "application/vnd.oac.ctk.case+json":
            case_paths.append(logical_text)

    expected_files = ledger_paths | {"bundle.json"}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unlisted = sorted(actual_files - expected_files)
        raise BundleError(f"bundle file inventory mismatch; missing={missing}, unlisted={unlisted}")

    projection = dict(manifest)
    claimed_digest = projection.pop("bundleDigest")
    try:
        actual_bundle_digest = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError) as exc:
        raise BundleError(f"manifest is not canonicalizable I-JSON: {exc}") from exc
    if claimed_digest != actual_bundle_digest:
        raise BundleError("bundleDigest does not match the manifest ledger projection")
    requirement_ref = _logical_path(manifest["requirementSetRef"]).as_posix()
    resource_ref = _logical_path(manifest["resourceProfileRef"]).as_posix()
    if requirement_ref not in ledger_paths or resource_ref not in ledger_paths:
        raise BundleError("requirement/resource profile refs must resolve in the artifact ledger")
    if (
        artifact_media_types[requirement_ref] != "application/vnd.oac.ctk.requirement-set+json"
        or artifact_media_types[resource_ref] != "application/vnd.oac.ctk.resource-profile+json"
    ):
        raise BundleError("requirement/resource profile refs have invalid media types")
    requirement_set = _json_bytes(artifact_bytes[requirement_ref], label=requirement_ref)
    resource_profile = _json_bytes(artifact_bytes[resource_ref], label=resource_ref)
    cases = tuple(_json_bytes(artifact_bytes[path], label=path) for path in sorted(case_paths))
    trusted_digest = _FROZEN_BUNDLE_DIGEST if coordinate == _FROZEN_COORDINATE else SUCCESSOR_BUNDLE_DIGEST
    if claimed_digest != trusted_digest:
        raise BundleError("bundle digest is not the trust anchor for the supported frozen suite")
    _validate_published_schemas(
        artifact_bytes=artifact_bytes,
        artifact_media_types=artifact_media_types,
        manifest=manifest,
        requirement_set=requirement_set,
        resource_profile=resource_profile,
        cases=cases,
    )
    limits = resource_profile.get("limits")
    if not isinstance(limits, dict):
        raise BundleError("resource profile limits must be an object")
    _bounded("bundleFiles", len(ledger_paths), limits, "maxBundleFiles")
    _bounded(
        "bundleBytes",
        sum(artifact_sizes.values()),
        limits,
        "maxBundleBytes",
    )
    for path in case_paths:
        _bounded("caseBytes", artifact_sizes[path], limits, "maxCaseBytes")
    _validate_requirement_coverage(
        requirement_set,
        cases,
        standard_version=manifest["standardVersion"],
        profile_version=manifest["profileVersion"],
    )
    return Bundle(
        root=root,
        manifest=manifest,
        requirement_set=requirement_set,
        resource_profile=resource_profile,
        cases=cases,
        artifact_digests=artifact_digests,
        result_schema=_json_bytes(artifact_bytes["contracts/schemas/RunResult.schema.json"], label="RunResult schema"),
    )


def _validate_published_schemas(
    *,
    artifact_bytes: dict[str, bytes],
    artifact_media_types: dict[str, str],
    manifest: dict[str, Any],
    requirement_set: dict[str, Any],
    resource_profile: dict[str, Any],
    cases: tuple[dict[str, Any], ...],
) -> None:
    instances: dict[str, tuple[dict[str, Any], ...]] = {
        "CTKBundle": (manifest,),
        "RequirementSet": (requirement_set,),
        "ConformanceResourceProfile": (resource_profile,),
        "ConformanceCase": cases,
    }
    for schema_name, path in _SCHEMA_ARTIFACTS.items():
        raw = artifact_bytes.get(path)
        if raw is None or artifact_media_types.get(path) != "application/schema+json":
            raise BundleError(f"required trusted schema artifact is missing: {path}")
        schema = _json_bytes(raw, label=path)
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            for instance in instances[schema_name]:
                validator.validate(instance)
        except SchemaError as exc:
            raise BundleError(f"invalid trusted {schema_name} schema: {exc.message}") from exc
        except ValidationError as exc:
            location = "/".join(str(item) for item in exc.absolute_path) or "<root>"
            raise BundleError(
                f"{schema_name} schema admission failed at {location}: {exc.message}"
            ) from exc


def _validate_requirement_coverage(
    requirement_set: dict[str, Any],
    cases: tuple[dict[str, Any], ...],
    *,
    standard_version: object,
    profile_version: object,
) -> None:
    required_fields = {
        "requirementSetId",
        "standardVersion",
        "profileVersion",
        "required",
        "notScored",
    }
    if set(requirement_set) != required_fields:
        raise BundleError("RequirementSet fields do not match the frozen format")
    required = requirement_set["required"]
    not_scored = requirement_set["notScored"]
    if not isinstance(required, list) or not all(
        isinstance(item, str) and item for item in required
    ):
        raise BundleError("RequirementSet.required must be an array of case IDs")
    if len(required) != len(set(required)):
        raise BundleError("RequirementSet.required contains duplicate case IDs")
    if not required:
        raise BundleError("Phase A RequirementSet.required must not be empty")
    if not isinstance(not_scored, list) or not all(isinstance(item, dict) for item in not_scored):
        raise BundleError("RequirementSet.notScored must be an array of records")
    if requirement_set["standardVersion"] != standard_version:
        raise BundleError("RequirementSet.standardVersion does not match the bundle")
    if requirement_set["profileVersion"] != profile_version:
        raise BundleError("RequirementSet.profileVersion does not match the bundle")
    unscored_ids: list[str] = []
    for item in not_scored:
        if set(item) != {"caseId", "reason"}:
            raise BundleError("RequirementSet.notScored record fields are invalid")
        case_id = item["caseId"]
        if not isinstance(case_id, str) or not case_id:
            raise BundleError("RequirementSet.notScored caseId is invalid")
        if item["reason"] not in {"extension", "added_after_release", "pending"}:
            raise BundleError("RequirementSet.notScored reason is invalid")
        unscored_ids.append(case_id)
    if len(unscored_ids) != len(set(unscored_ids)):
        raise BundleError("RequirementSet.notScored contains duplicate case IDs")
    case_ids: list[str] = []
    for case in cases:
        case_id = case.get("caseId")
        if not isinstance(case_id, str) or not case_id:
            raise BundleError("caseId must be a non-empty string")
        case_ids.append(case_id)
    if len(case_ids) != len(set(case_ids)):
        raise BundleError("case IDs must be unique")
    scored = set(required)
    unscored = set(unscored_ids)
    if scored & unscored:
        raise BundleError("a case cannot be both required and not-scored")
    if scored | unscored != set(case_ids):
        raise BundleError("RequirementSet must classify every and only bundled case")


def _bounded(label: str, observed: int, limits: dict[str, Any], key: str) -> None:
    maximum = limits.get(key)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise BundleError(f"resource profile {key} must be a positive integer")
    if observed > maximum:
        raise BundleError(f"{label} exceeds resource profile: {observed} > {maximum}")


def _json_depth(value: object) -> int:
    maximum = 1
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return maximum
