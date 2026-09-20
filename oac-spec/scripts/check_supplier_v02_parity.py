#!/usr/bin/env python3
"""Fresh-process Python/Go differential harness for the Supplier v0.2 capsule."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import suppress
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPSULE = (
    ROOT / "experiments/supplier-v02-portability/v0.2-seed-2/capsule.json"
)
DEFAULT_CAPABILITY_SET = (
    ROOT / "ctk/capabilities/supplier-v02-seed-2.capability-set.json"
)
DEFAULT_RECIPE_SET = ROOT / "ctk/generators/supplier-v02-seed-2.recipes.json"
CASEGEN_SOURCE = ROOT / "scripts/supplier_v02_casegen.py"


def _load_casegen_module() -> Any:
    specification = importlib.util.spec_from_file_location(
        "_oac_supplier_v02_casegen",
        CASEGEN_SOURCE,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load the pinned Supplier case generator")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


_CASEGEN = _load_casegen_module()
CaseGenerationError = _CASEGEN.CaseGenerationError
admit_recipe_set = _CASEGEN.admit_recipe_set
generate_cases = _CASEGEN.generate_cases

PROTOCOL_VERSION = "oac.ctk.stdio/v2"
MAX_REQUEST_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 1_048_576
MAX_RESPONSE_JSON_DEPTH = 64
TIMEOUT_SECONDS = 5
FROZEN_CAPSULE_DIGEST = "sha256:6efc45109628314823f078256546d67aae690b1c18d97fddab38dc23be904e79"
_ENVIRONMENT_ALLOWLIST = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
}

_CAPSULE_FIELDS = {
    "apiVersion",
    "kind",
    "capsuleId",
    "protocolVersion",
    "profileId",
    "profileVersion",
    "digestAlgorithm",
    "contracts",
    "capabilitySet",
    "generator",
    "cases",
    "capsuleDigest",
}
_CASE_FIELDS = {"caseId", "snapshot", "change", "expected"}
_SOURCE_BOUND_FIELDS = {
    "path",
    "rawSha256",
    "sizeBytes",
    "sourcePath",
    "sourceRawSha256",
    "sourceSizeBytes",
    "transform",
}
_EXPECTED_FIELDS = {"path", "reportDigest", "rawSha256", "sizeBytes"}
_GENERATOR_DESCRIPTOR_FIELDS = {"recipeSet", "source"}
_CAPABILITY_SET_FIELDS = {
    "apiVersion",
    "kind",
    "capabilitySetId",
    "protocolVersion",
    "reportCoordinate",
    "tracks",
    "digestAlgorithm",
    "capabilitySetDigest",
}
_TRACK_FIELDS = {"role", "operation", "profileId", "profileVersion", "wireVersion"}


class HarnessFailure(RuntimeError):
    """A deterministic harness/admission failure before semantic comparison."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HarnessFailure(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise HarnessFailure(f"non-JSON numeric constant: {value}")


def _loads(raw: bytes) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HarnessFailure(f"invalid JSON: {exc}") from exc


def _enforce_json_depth(value: object, maximum: int, label: str) -> None:
    """Reject a decoded response whose container depth exceeds the wire limit."""

    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > maximum:
            raise HarnessFailure(f"{label} exceeds maxResponseJsonDepth")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _closed(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise HarnessFailure(f"{label} is not closed")


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _NOFOLLOW
_DIRECTORY_FLAGS = _FILE_FLAGS | getattr(os, "O_DIRECTORY", 0)


def _logical_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise HarnessFailure("artifact path must be a normalized non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or path.as_posix() != value:
        raise HarnessFailure(f"unsafe artifact path: {value}")
    return path


def _open_capsule_root(path: Path) -> int:
    if not _NOFOLLOW or os.open not in os.supports_dir_fd:
        raise HarnessFailure("secure fd-anchored capsule loading is unavailable")
    try:
        descriptor = os.open(path, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise HarnessFailure("capsule root must be a non-symlink directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise HarnessFailure("capsule root must be a directory")
    return descriptor


def _open_directory_at(parent: int, name: str, label: str) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as exc:
        raise HarnessFailure(f"{label} must be a non-symlink directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise HarnessFailure(f"{label} must be a directory")
    return descriptor


def _read_regular_once_at(root_descriptor: int, relative: object, label: str) -> bytes:
    path = _logical_path(relative)
    directory_descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(path.parts[:-1], start=1):
            child = _open_directory_at(
                directory_descriptor,
                component,
                f"directory {'/'.join(path.parts[:index])}",
            )
            os.close(directory_descriptor)
            directory_descriptor = child
        try:
            descriptor = os.open(path.parts[-1], _FILE_FLAGS, dir_fd=directory_descriptor)
        except OSError as exc:
            raise HarnessFailure(f"{label} must be a regular non-symlink file") from exc
    finally:
        os.close(directory_descriptor)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise HarnessFailure(f"{label} must be a single-link regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(status.st_size + 1)
        if len(raw) != status.st_size:
            raise HarnessFailure(f"{label} changed while loading")
        return raw
    finally:
        os.close(descriptor)


def _verify_raw_descriptor(
    root_descriptor: int, descriptor: Mapping[str, Any], label: str
) -> bytes:
    artifact_raw = _read_regular_once_at(root_descriptor, descriptor["path"], label)
    if descriptor["rawSha256"] != _digest(artifact_raw):
        raise HarnessFailure(f"{label} rawSha256 mismatch")
    if descriptor["sizeBytes"] != len(artifact_raw):
        raise HarnessFailure(f"{label} sizeBytes mismatch")
    return artifact_raw


def _verify_source_drift(descriptor: Mapping[str, Any], label: str) -> None:
    source_name = descriptor["sourcePath"]
    if not isinstance(source_name, str) or not source_name:
        raise HarnessFailure(f"{label} sourcePath must be a non-empty string")
    relative = Path(source_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise HarnessFailure(f"{label} sourcePath is unsafe")
    source_path = (ROOT / relative).resolve()
    if ROOT.resolve() not in source_path.parents or not source_path.is_file():
        raise HarnessFailure(f"{label} source is missing")
    source_raw = source_path.read_bytes()
    if descriptor["sourceRawSha256"] != _digest(source_raw):
        raise HarnessFailure(f"{label} source drift detected")
    if descriptor["sourceSizeBytes"] != len(source_raw):
        raise HarnessFailure(f"{label} source size drift detected")


def _verify_exact_copy_descriptor(
    root_descriptor: int,
    descriptor: object,
    label: str,
    *,
    verify_source_drift: bool,
) -> bytes:
    if not isinstance(descriptor, dict):
        raise HarnessFailure(f"{label} descriptor must be an object")
    _closed(descriptor, _SOURCE_BOUND_FIELDS, f"{label} descriptor")
    if descriptor["transform"] != "exact-copy/v1":
        raise HarnessFailure(f"{label} transform must be exact-copy/v1")
    copied = _verify_raw_descriptor(root_descriptor, descriptor, label)
    if (
        descriptor["rawSha256"] != descriptor["sourceRawSha256"]
        or descriptor["sizeBytes"] != descriptor["sourceSizeBytes"]
    ):
        raise HarnessFailure(f"{label} is not an exact source copy")
    if verify_source_drift:
        _verify_source_drift(descriptor, label)
    return copied


def _track_coordinate(track: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        track["role"],
        track["operation"],
        track["profileId"],
        track["profileVersion"],
        track["wireVersion"],
    )


def _admit_capability_set(raw: bytes) -> dict[str, Any]:
    value = _loads(raw)
    if not isinstance(value, dict):
        raise HarnessFailure("capability set must be an object")
    _closed(value, _CAPABILITY_SET_FIELDS, "capability set")
    report_coordinate = value["reportCoordinate"]
    if not isinstance(report_coordinate, dict):
        raise HarnessFailure("capability reportCoordinate must be an object")
    _closed(report_coordinate, {"profileId", "profileVersion"}, "reportCoordinate")
    if (
        value["apiVersion"] != "oac.ctk.capability-set/v0alpha1"
        or value["kind"] != "ProtocolCapabilitySet"
        or value["capabilitySetId"]
        != "oac.supplier.transfer.portability-capsule/v0.2-seed-2"
        or value["protocolVersion"] != PROTOCOL_VERSION
        or report_coordinate
        != {"profileId": "oac.supplier.transfer", "profileVersion": "v0.2"}
        or value["digestAlgorithm"] != "sha256-jcs-detached/v1"
    ):
        raise HarnessFailure("capability set coordinate is not the bounded seed-2 coordinate")
    projection = {
        key: item for key, item in value.items() if key != "capabilitySetDigest"
    }
    if value["capabilitySetDigest"] != _digest(rfc8785.dumps(projection)):
        raise HarnessFailure("capabilitySetDigest mismatch")
    if raw != rfc8785.dumps(value) + b"\n":
        raise HarnessFailure("capability set must be exact JCS plus newline")
    tracks = value["tracks"]
    if not isinstance(tracks, list) or not tracks:
        raise HarnessFailure("capability set tracks must be a non-empty array")
    coordinates: set[tuple[object, ...]] = set()
    operations: set[str] = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise HarnessFailure("capability set track must be an object")
        _closed(track, _TRACK_FIELDS, "capability set track")
        if (
            track["role"] != "semantic-kernel"
            or track["operation"] not in {"validateResource", "derive"}
            or track["wireVersion"] != PROTOCOL_VERSION
            or not isinstance(track["profileId"], str)
            or not track["profileId"].strip()
            or not isinstance(track["profileVersion"], str)
            or not track["profileVersion"].strip()
        ):
            raise HarnessFailure("capability set track coordinate is invalid")
        coordinate = _track_coordinate(track)
        if coordinate in coordinates:
            raise HarnessFailure("capability set track is duplicated")
        coordinates.add(coordinate)
        operations.add(track["operation"])
    if operations != {"validateResource", "derive"}:
        raise HarnessFailure("capability set must declare the seed-2 operation set")
    return value


def _admit_bound_seed2_artifacts(
    root_descriptor: int,
    manifest: Mapping[str, Any],
    *,
    verify_source_drift: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    capability_raw = _verify_exact_copy_descriptor(
        root_descriptor,
        manifest["capabilitySet"],
        "capability set",
        verify_source_drift=verify_source_drift,
    )
    capability_set = _admit_capability_set(capability_raw)

    generator = manifest["generator"]
    if not isinstance(generator, dict):
        raise HarnessFailure("generator descriptor must be an object")
    _closed(generator, _GENERATOR_DESCRIPTOR_FIELDS, "generator descriptor")
    recipe_raw = _verify_exact_copy_descriptor(
        root_descriptor,
        generator["recipeSet"],
        "generator recipe set",
        verify_source_drift=verify_source_drift,
    )
    source_raw = _verify_exact_copy_descriptor(
        root_descriptor,
        generator["source"],
        "generator source",
        verify_source_drift=verify_source_drift,
    )
    try:
        recipe_set = admit_recipe_set(recipe_raw)
    except CaseGenerationError as exc:
        raise HarnessFailure(str(exc)) from exc
    local_source = CASEGEN_SOURCE.read_bytes()
    if source_raw != local_source:
        raise HarnessFailure("executed generator source differs from the pinned artifact")
    return capability_set, recipe_set


def _load_bound_seed2_artifacts(
    capsule_path: Path, manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_descriptor = _open_capsule_root(capsule_path.parent)
    try:
        return _admit_bound_seed2_artifacts(
            root_descriptor,
            manifest,
            verify_source_drift=False,
        )
    finally:
        os.close(root_descriptor)


def load_capsule(
    path: Path = DEFAULT_CAPSULE,
    *,
    verify_source_drift: bool = False,
    expected_capsule_digest: str = FROZEN_CAPSULE_DIGEST,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root_descriptor = _open_capsule_root(path.parent)
    try:
        raw = _read_regular_once_at(root_descriptor, path.name, "capsule manifest")
        return _load_capsule_anchored(
            root_descriptor,
            raw,
            verify_source_drift=verify_source_drift,
            expected_capsule_digest=expected_capsule_digest,
        )
    finally:
        os.close(root_descriptor)


def _load_capsule_anchored(
    root_descriptor: int,
    raw: bytes,
    *,
    verify_source_drift: bool,
    expected_capsule_digest: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = _loads(raw)
    if not isinstance(value, dict):
        raise HarnessFailure("capsule must be an object")
    _closed(value, _CAPSULE_FIELDS, "capsule")
    if (
        value["apiVersion"] != "oac.portability.capsule/v0alpha1"
        or value["kind"] != "SupplierDerivationPortabilityCapsule"
        or value["capsuleId"] != "oac.supplier.transfer/v0.2/a1-seed-2"
        or value["protocolVersion"] != PROTOCOL_VERSION
        or value["profileId"] != "oac.supplier.transfer"
        or value["profileVersion"] != "v0.2"
        or value["digestAlgorithm"] != "sha256-jcs-detached/v1"
    ):
        raise HarnessFailure("capsule coordinate is not the frozen A1 coordinate")
    projection = {key: item for key, item in value.items() if key != "capsuleDigest"}
    if value["capsuleDigest"] != _digest(rfc8785.dumps(projection)):
        raise HarnessFailure("capsuleDigest mismatch")
    if value["capsuleDigest"] != expected_capsule_digest:
        raise HarnessFailure("capsuleDigest is not the runner trust anchor")
    if raw != rfc8785.dumps(value) + b"\n":
        raise HarnessFailure("capsule.json must be exact JCS plus newline")

    _admit_bound_seed2_artifacts(
        root_descriptor,
        value,
        verify_source_drift=verify_source_drift,
    )

    contracts = value["contracts"]
    if not isinstance(contracts, list) or not contracts:
        raise HarnessFailure("capsule contracts ledger must be a non-empty array")
    contract_paths: set[str] = set()
    source_paths: set[str] = set()
    for descriptor in contracts:
        if not isinstance(descriptor, dict):
            raise HarnessFailure("contract descriptor must be an object")
        _closed(descriptor, _SOURCE_BOUND_FIELDS, "contract descriptor")
        if descriptor["transform"] != "exact-copy/v1":
            raise HarnessFailure("contract transform must be exact-copy/v1")
        if descriptor["path"] in contract_paths or descriptor["sourcePath"] in source_paths:
            raise HarnessFailure("contract ledger paths must be unique")
        contract_paths.add(descriptor["path"])
        source_paths.add(descriptor["sourcePath"])
        copied = _verify_raw_descriptor(root_descriptor, descriptor, "contract")
        if verify_source_drift:
            _verify_source_drift(descriptor, "contract")
        if (
            descriptor["rawSha256"] != descriptor["sourceRawSha256"]
            or len(copied) != descriptor["sourceSizeBytes"]
        ):
            raise HarnessFailure("exact contract copy differs from its frozen source")

    raw_cases = value["cases"]
    if not isinstance(raw_cases, list) or len(raw_cases) != 3:
        raise HarnessFailure("capsule must contain exactly three frozen cases")
    case_ids: set[str] = set()
    admitted: list[dict[str, Any]] = []
    for item in raw_cases:
        if not isinstance(item, dict):
            raise HarnessFailure("capsule case must be an object")
        _closed(item, _CASE_FIELDS, "capsule case")
        case_id = item["caseId"]
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise HarnessFailure("caseId must be a unique non-empty string")
        case_ids.add(case_id)

        artifacts: dict[str, bytes] = {}
        for field in ("snapshot", "change"):
            descriptor = item[field]
            if not isinstance(descriptor, dict):
                raise HarnessFailure(f"{field} descriptor must be an object")
            _closed(descriptor, _SOURCE_BOUND_FIELDS, f"{field} descriptor")
            if descriptor["transform"] != "raw-map-reseal-jcs-newline/v1":
                raise HarnessFailure(f"{case_id} {field} transform mismatch")
            artifact_raw = _verify_raw_descriptor(root_descriptor, descriptor, f"{case_id} {field}")
            if verify_source_drift:
                _verify_source_drift(descriptor, f"{case_id} {field}")
            artifacts[field] = artifact_raw

        expected = item["expected"]
        if not isinstance(expected, dict):
            raise HarnessFailure("expected descriptor must be an object")
        _closed(expected, _EXPECTED_FIELDS, "expected descriptor")
        expected_raw = _verify_raw_descriptor(root_descriptor, expected, f"{case_id} expected")
        expected_report = _loads(expected_raw)
        if not isinstance(expected_report, dict):
            raise HarnessFailure("expected report must be an object")
        expected_jcs = rfc8785.dumps(expected_report)
        if expected_raw != expected_jcs + b"\n":
            raise HarnessFailure(f"{case_id} expected report is not exact JCS plus newline")
        if expected["reportDigest"] != _digest(expected_jcs):
            raise HarnessFailure(f"{case_id} reportDigest mismatch")
        admitted.append(
            {
                "caseId": case_id,
                "snapshot": artifacts["snapshot"],
                "change": artifacts["change"],
                "expectedJcs": expected_jcs,
                "expectedDigest": expected["reportDigest"],
            }
        )
    return value, admitted


def _pinned_report_validator(
    capsule_path: Path, manifest: Mapping[str, Any]
) -> Draft202012Validator:
    descriptor = next(
        (
            item
            for item in manifest["contracts"]
            if item.get("sourcePath") == "schemas/ProfileDerivationReport.schema.json"
        ),
        None,
    )
    if not isinstance(descriptor, dict):
        raise HarnessFailure("capsule omits the pinned ProfileDerivationReport schema")
    root_descriptor = _open_capsule_root(capsule_path.parent)
    try:
        raw = _verify_raw_descriptor(root_descriptor, descriptor, "report schema")
    finally:
        os.close(root_descriptor)
    schema = _loads(raw)
    if not isinstance(schema, dict):
        raise HarnessFailure("ProfileDerivationReport schema must be an object")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise HarnessFailure(f"invalid pinned ProfileDerivationReport schema: {exc}") from exc
    return Draft202012Validator(schema)


@cache
def _default_report_validator() -> Draft202012Validator:
    manifest, _ = load_capsule(DEFAULT_CAPSULE)
    return _pinned_report_validator(DEFAULT_CAPSULE, manifest)


def _derive_request(snapshot_raw: bytes, change_raw: bytes, request_id: str) -> dict[str, object]:
    """Construct the only data disclosed to a derive SUT."""

    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": "derive",
        "payload": {
            "snapshotBase64": base64.b64encode(snapshot_raw).decode("ascii"),
            "changeBase64": base64.b64encode(change_raw).decode("ascii"),
        },
    }


def _capabilities_request(request_id: str) -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": "capabilities",
        "payload": {},
    }


def _validate_request(expected_kind: str, raw_base64: str, request_id: str) -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "operation": "validateResource",
        "payload": {"expectedKind": expected_kind, "rawBase64": raw_base64},
    }


def _invoke(command: Sequence[str], request: Mapping[str, Any]) -> dict[str, Any]:
    request_raw = rfc8785.dumps(request) + b"\n"
    if len(request_raw) > MAX_REQUEST_BYTES:
        raise HarnessFailure("runner request exceeds maxRequestBytes")
    environment = {key: value for key, value in os.environ.items() if key in _ENVIRONMENT_ALLOWLIST}
    environment.update(
        {
            "NO_PROXY": "*",
            "no_proxy": "*",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    timed_out = False
    output_exceeded = False
    with (
        tempfile.TemporaryDirectory(prefix="oac-v02-sut-") as working_directory,
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                cwd=working_directory,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise HarnessFailure(f"adapter process failure: {exc}") from exc
        assert process.stdin is not None
        try:
            process.stdin.write(request_raw)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while process.poll() is None:
            if (
                _file_size(stdout_file) > MAX_OUTPUT_BYTES
                or _file_size(stderr_file) > MAX_OUTPUT_BYTES
            ):
                output_exceeded = True
                _kill_process_tree(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_process_tree(process)
                break
            time.sleep(0.005)
        _kill_process_tree(process)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stdout_size = _file_size(stdout_file)
        stderr_size = _file_size(stderr_file)
        stdout = _bounded_file_bytes(stdout_file, MAX_OUTPUT_BYTES)
        stderr = _bounded_file_bytes(stderr_file, MAX_OUTPUT_BYTES)
    if timed_out:
        raise HarnessFailure("adapter process exceeded adapterTimeoutMs")
    if output_exceeded or stdout_size > MAX_OUTPUT_BYTES or stderr_size > MAX_OUTPUT_BYTES:
        raise HarnessFailure("adapter output exceeds maxAdapterOutputBytes")
    if process.returncode != 0:
        diagnostic = stderr[:4096].decode("utf-8", errors="replace")
        raise HarnessFailure(f"adapter exited {process.returncode}: {diagnostic or 'no stderr'}")
    response = _loads(stdout)
    _enforce_json_depth(response, MAX_RESPONSE_JSON_DEPTH, "adapter response")
    if not isinstance(response, dict):
        raise HarnessFailure("adapter response must be an object")
    return response


def _file_size(stream: Any) -> int:
    return int(os.fstat(stream.fileno()).st_size)


def _bounded_file_bytes(stream: Any, maximum: int) -> bytes:
    stream.seek(0)
    return stream.read(maximum + 1)


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    process.kill()
        return
    if process.poll() is None:  # pragma: no cover - Windows-only fallback
        process.kill()


def _capabilities(
    command: Sequence[str],
    request_id: str,
    capability_set: Mapping[str, Any],
) -> dict[str, Any]:
    response = _invoke(command, _capabilities_request(request_id))
    _closed(response, {"protocolVersion", "requestId", "sutStatus", "result"}, "response")
    if (
        response["protocolVersion"] != PROTOCOL_VERSION
        or response["requestId"] != request_id
        or response["sutStatus"] != "COMPLETED"
        or not isinstance(response["result"], dict)
    ):
        raise HarnessFailure("capability response envelope mismatch")
    result = response["result"]
    _closed(
        result,
        {"implementationId", "implementationVersion", "adapterProtocolVersion", "tracks"},
        "capability result",
    )
    if result["adapterProtocolVersion"] != PROTOCOL_VERSION:
        raise HarnessFailure("capability adapterProtocolVersion mismatch")
    for key in ("implementationId", "implementationVersion"):
        if not isinstance(result[key], str) or not result[key].strip():
            raise HarnessFailure(f"capability {key} must be non-blank")
    required = {_track_coordinate(item) for item in capability_set["tracks"]}
    tracks = result["tracks"]
    if not isinstance(tracks, list):
        raise HarnessFailure("capability tracks must be an array")
    observed: set[tuple[object, ...]] = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise HarnessFailure("capability track must be an object")
        _closed(track, _TRACK_FIELDS, "capability track")
        coordinate = _track_coordinate(track)
        if coordinate in observed:
            raise HarnessFailure("capability track coordinate is duplicated")
        observed.add(coordinate)
    if observed != required:
        raise HarnessFailure("capability tracks do not equal the pinned capability set")
    return result


def _derive_observation(
    command: Sequence[str],
    snapshot: bytes,
    change: bytes,
    request_id: str,
    *,
    report_validator: Draft202012Validator | None = None,
) -> dict[str, Any]:
    snapshot_digest = _claimed_resource_digest(snapshot, "OrganizationSnapshot")
    change_digest = _claimed_resource_digest(change, "SemanticChangeSet")
    response = _invoke(command, _derive_request(snapshot, change, request_id))
    if (
        response.get("protocolVersion") != PROTOCOL_VERSION
        or response.get("requestId") != request_id
    ):
        raise HarnessFailure("derive response envelope coordinate mismatch")
    status = response.get("sutStatus")
    if status == "COMPLETED":
        _closed(response, {"protocolVersion", "requestId", "sutStatus", "result"}, "response")
        result = response["result"]
        if not isinstance(result, dict):
            raise HarnessFailure("derive result must be an object")
        _closed(result, {"report", "reportDigest"}, "derive result")
        report = result["report"]
        if not isinstance(report, dict):
            raise HarnessFailure("derive report must be an object")
        validator = report_validator or _default_report_validator()
        errors = sorted(
            validator.iter_errors(report),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            raise HarnessFailure(f"derive report violates pinned schema: {errors[0].message}")
        if (
            report.get("profileId") != "oac.supplier.transfer"
            or report.get("profileVersion") != "v0.2"
            or report.get("snapshotDigest") != snapshot_digest
            or report.get("changeDigest") != change_digest
        ):
            raise HarnessFailure("derive report does not bind the requested profile and raw roots")
        report_jcs = rfc8785.dumps(report)
        recomputed = _digest(report_jcs)
        if result["reportDigest"] != recomputed:
            raise HarnessFailure("SUT reportDigest differs from runner recomputation")
        return {"status": status, "jcs": report_jcs, "digest": recomputed, "response": response}
    if status in {"ERROR", "UNSUPPORTED", "RESOURCE_EXHAUSTED"}:
        _closed(response, {"protocolVersion", "requestId", "sutStatus", "error"}, "response")
        error = response["error"]
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            raise HarnessFailure("error response requires a stable code")
        if not set(error).issubset({"code", "detail"}):
            raise HarnessFailure("error response is not closed")
        error_jcs = rfc8785.dumps({"sutStatus": status, "errorCode": error["code"]})
        return {
            "status": status,
            "jcs": error_jcs,
            "digest": _digest(error_jcs),
            "response": response,
        }
    raise HarnessFailure("derive response has an invalid sutStatus")


def _claimed_resource_digest(raw: bytes, expected_kind: str) -> str:
    decoded = _decoded(raw)
    if decoded.get("kind") != expected_kind or not isinstance(decoded.get("digest"), str):
        raise HarnessFailure("derive harness input has an invalid sealed-resource envelope")
    claimed = decoded["digest"]
    projection = {key: item for key, item in decoded.items() if key != "digest"}
    if claimed != _digest(rfc8785.dumps(_binary64_projection(projection))):
        raise HarnessFailure("derive harness input has a raw-root digest mismatch")
    return claimed


def _reseal(value: dict[str, Any]) -> bytes:
    projection = {key: item for key, item in value.items() if key != "digest"}
    value["digest"] = _digest(rfc8785.dumps(_binary64_projection(projection)))
    return rfc8785.dumps(_binary64_projection(value))


def _binary64_projection(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise HarnessFailure("generated JSON number is outside finite binary64")
        return number
    if isinstance(value, list):
        return [_binary64_projection(item) for item in value]
    if isinstance(value, dict):
        return {key: _binary64_projection(item) for key, item in value.items()}
    raise HarnessFailure(f"generated value is outside JSON: {type(value).__name__}")


def _decoded(raw: bytes) -> dict[str, Any]:
    value = _loads(raw)
    if not isinstance(value, dict):
        raise HarnessFailure("generated resource must remain an object")
    return value


def _validate_observation(
    command: Sequence[str],
    *,
    expected_kind: str,
    raw_base64: str,
    request_id: str,
) -> dict[str, Any]:
    response = _invoke(command, _validate_request(expected_kind, raw_base64, request_id))
    if (
        response.get("protocolVersion") != PROTOCOL_VERSION
        or response.get("requestId") != request_id
    ):
        raise HarnessFailure("validateResource response envelope coordinate mismatch")
    status = response.get("sutStatus")
    if status == "COMPLETED":
        _closed(response, {"protocolVersion", "requestId", "sutStatus", "result"}, "response")
        result = response["result"]
        if not isinstance(result, dict):
            raise HarnessFailure("validateResource result must be an object")
        _closed(result, {"kind", "resourceId", "resourceDigest"}, "validateResource result")
        result_jcs = rfc8785.dumps(result)
        return {
            "status": status,
            "jcs": result_jcs,
            "digest": _digest(result_jcs),
            "response": response,
        }
    if status in {"ERROR", "UNSUPPORTED", "RESOURCE_EXHAUSTED"}:
        _closed(response, {"protocolVersion", "requestId", "sutStatus", "error"}, "response")
        error = response["error"]
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            raise HarnessFailure("validateResource error requires a stable code")
        if not set(error).issubset({"code", "detail"}):
            raise HarnessFailure("validateResource error response is not closed")
        result_jcs = rfc8785.dumps({"sutStatus": status, "errorCode": error["code"]})
        return {
            "status": status,
            "jcs": result_jcs,
            "digest": _digest(result_jcs),
            "response": response,
        }
    raise HarnessFailure("validateResource response has an invalid sutStatus")


def _success_validate_expectation(raw: bytes, expected_kind: str) -> dict[str, object]:
    decoded = _decoded(raw)
    metadata = decoded.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        raise HarnessFailure("validateResource fixture lacks metadata.id")
    return {
        "status": "COMPLETED",
        "result": {
            "kind": expected_kind,
            "resourceId": metadata["id"],
            "resourceDigest": decoded["digest"],
        },
    }


def _noncanonical_base64(raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    if encoded.endswith("=="):
        position = len(encoded) - 3
    elif encoded.endswith("="):
        position = len(encoded) - 2
    else:
        raise HarnessFailure("probe raw bytes require Base64 padding")
    index = alphabet.index(encoded[position])
    alternative = alphabet[index | 1]
    mutated = encoded[:position] + alternative + encoded[position + 1 :]
    if base64.b64decode(mutated, validate=True) != raw or mutated == encoded:
        raise HarnessFailure("failed to construct a noncanonical Base64 probe")
    return mutated


def build_validate_cases(frozen_cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return frozen-root and adversarial admission cases with runner-owned expectations."""

    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for frozen in frozen_cases:
        for field, kind in (
            ("snapshot", "OrganizationSnapshot"),
            ("change", "SemanticChangeSet"),
        ):
            raw = frozen[field]
            key = (kind, _digest(raw))
            if key in seen:
                continue
            seen.add(key)
            cases.append(
                {
                    "caseId": f"frozen-root:{len(cases) + 1}",
                    "class": "frozen",
                    "expectedKind": kind,
                    "rawBase64": base64.b64encode(raw).decode("ascii"),
                    "expect": _success_validate_expectation(raw, kind),
                }
            )

    base_raw = next(item["change"] for item in frozen_cases if item["caseId"] == "SC-009")
    base = _decoded(base_raw)

    def invalid_case(
        case_id: str,
        raw: bytes,
        code: str,
        *,
        raw_base64: str | None = None,
    ) -> dict[str, Any]:
        return {
            "caseId": case_id,
            "class": "probe",
            "expectedKind": "SemanticChangeSet",
            "rawBase64": raw_base64 or base64.b64encode(raw).decode("ascii"),
            "expect": {"status": "ERROR", "errorCode": code},
        }

    missing_envelope = dict(base)
    missing_envelope.pop("apiVersion")
    identifier_rewrite = json.loads(json.dumps(base))
    identifier_rewrite["metadata"]["id"] = f" {identifier_rewrite['metadata']['id']} "
    timestamp_alias = json.loads(json.dumps(base))
    timestamp_alias["metadata"]["createdAt"] = "2026-08-23T00:00:00.000000Z"
    timestamp_offset = json.loads(json.dumps(base))
    timestamp_offset["metadata"]["createdAt"] = "2026-08-23T00:00:00+00:00"
    timestamp_year_zero = json.loads(json.dumps(base))
    timestamp_year_zero["metadata"]["createdAt"] = "0000-01-01T00:00:00Z"
    timestamp_year_max = json.loads(json.dumps(base))
    timestamp_year_max["metadata"]["createdAt"] = "9999-12-31T23:59:59Z"
    root_mismatch = json.loads(json.dumps(base))
    root_mismatch["digest"] = "sha256:" + "0" * 64
    unknown_kind = json.loads(json.dumps(base))
    unknown_kind["kind"] = "UnknownKind"
    blank_identifier = json.loads(json.dumps(base))
    blank_identifier["metadata"]["id"] = "\u00a0"
    plain_business_string = json.loads(json.dumps(base))
    plain_business_string["spec"]["deltas"][0]["after"]["value"] = " judicial_restructuring "
    finite_2_53 = json.loads(json.dumps(base))
    finite_2_53["metadata"]["revision"] = 9_007_199_254_740_992
    finite_2_53_raw = _reseal(finite_2_53)

    cases.extend(
        [
            invalid_case(
                "probe:missing-envelope",
                _reseal(missing_envelope),
                "CORE_SCHEMA_INVALID",
            ),
            invalid_case(
                "probe:identifier-explicit-rewrite",
                _reseal(identifier_rewrite),
                "CANONICAL_ADMISSION_MISMATCH",
            ),
            invalid_case(
                "probe:timestamp-alias",
                _reseal(timestamp_alias),
                "CORE_SCHEMA_INVALID",
            ),
            invalid_case(
                "probe:timestamp-offset",
                _reseal(timestamp_offset),
                "CORE_SCHEMA_INVALID",
            ),
            invalid_case(
                "probe:timestamp-year-zero",
                _reseal(timestamp_year_zero),
                "CORE_SCHEMA_INVALID",
            ),
            {
                "caseId": "probe:timestamp-year-max",
                "class": "probe",
                "expectedKind": "SemanticChangeSet",
                "rawBase64": base64.b64encode(_reseal(timestamp_year_max)).decode("ascii"),
                "expect": _success_validate_expectation(
                    _reseal(timestamp_year_max), "SemanticChangeSet"
                ),
            },
            invalid_case(
                "probe:duplicate-key",
                b'{"kind":"SemanticChangeSet",' + base_raw.lstrip()[1:],
                "CORE_SCHEMA_INVALID",
            ),
            invalid_case(
                "probe:noncanonical-base64",
                base_raw,
                "CTK_INPUT_INVALID",
                raw_base64=_noncanonical_base64(base_raw),
            ),
            invalid_case(
                "probe:root-digest-mismatch",
                rfc8785.dumps(root_mismatch),
                "ROOT_DIGEST_MISMATCH",
            ),
            invalid_case(
                "probe:unknown-kind",
                _reseal(unknown_kind),
                "CORE_KIND_UNKNOWN",
            ),
            invalid_case(
                "probe:blank-identifier",
                _reseal(blank_identifier),
                "CORE_SCHEMA_INVALID",
            ),
            {
                "caseId": "probe:plain-business-string-whitespace",
                "class": "probe",
                "expectedKind": "SemanticChangeSet",
                "rawBase64": base64.b64encode(_reseal(plain_business_string)).decode("ascii"),
                "expect": _success_validate_expectation(
                    _reseal(plain_business_string), "SemanticChangeSet"
                ),
            },
            {
                "caseId": "probe:finite-2pow53",
                "class": "probe",
                "expectedKind": "SemanticChangeSet",
                "rawBase64": base64.b64encode(finite_2_53_raw).decode("ascii"),
                "expect": _success_validate_expectation(finite_2_53_raw, "SemanticChangeSet"),
            },
        ]
    )
    nan_raw = base_raw.replace(b'"revision":1', b'"revision":NaN', 1)
    cases.append(invalid_case("probe:nan-token", nan_raw, "CORE_SCHEMA_INVALID"))
    cases.append(invalid_case("probe:invalid-utf8", b"\xff", "CORE_SCHEMA_INVALID"))

    for token in (b"1e0", b"1.0", b"1.00e+0"):
        equivalent = base_raw.replace(b'"revision":1', b'"revision":' + token, 1)
        cases.append(
            {
                "caseId": f"probe:equivalent-number-{token.decode('ascii')}",
                "class": "probe",
                "expectedKind": "SemanticChangeSet",
                "rawBase64": base64.b64encode(equivalent).decode("ascii"),
                "expect": _success_validate_expectation(equivalent, "SemanticChangeSet"),
            }
        )

    for layers, status, code in (
        (62, "ERROR", "CORE_SCHEMA_INVALID"),
        (63, "RESOURCE_EXHAUSTED", "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"),
    ):
        deep = json.loads(json.dumps(base))
        nested: object = 0
        for _ in range(layers):
            nested = [nested]
        deep["extra"] = nested
        cases.append(
            {
                "caseId": f"probe:json-depth-{layers + 2}",
                "class": "probe",
                "expectedKind": "SemanticChangeSet",
                "rawBase64": base64.b64encode(_reseal(deep)).decode("ascii"),
                "expect": {"status": status, "errorCode": code},
            }
        )
    return cases


def generate_variants(
    frozen_cases: Sequence[Mapping[str, Any]],
    recipe_set: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility entry point backed only by the public pinned recipe artifact."""

    if recipe_set is None:
        try:
            recipe_set = admit_recipe_set(DEFAULT_RECIPE_SET.read_bytes())
        except (OSError, CaseGenerationError) as exc:
            raise HarnessFailure(f"cannot admit the public recipe set: {exc}") from exc
    try:
        return generate_cases(recipe_set, frozen_cases)
    except CaseGenerationError as exc:
        raise HarnessFailure(str(exc)) from exc


def _target_module_sources(command: Sequence[str], module_name: str) -> list[Path]:
    """Resolve a ``-m`` adapter through the target command, never this harness process.

    This distinction is material for isolated-wheel replay: resolving ``module_name`` with
    ``importlib.util.find_spec`` in the harness would silently bind the source checkout even while the
    SUT process executes the installed wheel from another virtual environment.
    """

    module_marker = list(command).index("-m")
    launcher = list(command[:module_marker])
    if not launcher:
        raise HarnessFailure("-m adapter has no target interpreter")
    probe = (
        "import importlib.util,json,sys;"
        "s=importlib.util.find_spec(sys.argv[1]);"
        "print(json.dumps({'origin':None if s is None else s.origin},separators=(',',':')))"
    )
    environment = {
        key: value for key, value in os.environ.items() if key in _ENVIRONMENT_ALLOWLIST
    }
    with tempfile.TemporaryDirectory(prefix="oac-module-probe-") as temporary:
        try:
            completed = subprocess.run(
                [*launcher, "-I", "-c", probe, module_name],
                cwd=temporary,
                env=environment,
                capture_output=True,
                check=False,
                timeout=TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessFailure(f"cannot resolve target adapter module: {module_name}") from exc
    if completed.returncode != 0 or len(completed.stdout) > 16_384:
        raise HarnessFailure(f"cannot resolve target adapter module: {module_name}")
    try:
        result = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessFailure(f"target module probe is not valid JSON: {module_name}") from exc
    origin_value = result.get("origin") if isinstance(result, dict) else None
    if not isinstance(origin_value, str) or not origin_value:
        raise HarnessFailure(f"cannot resolve target adapter module: {module_name}")
    origin = Path(origin_value).resolve()
    if not origin.is_file() or origin.suffix not in {".py", ".pyi"}:
        raise HarnessFailure(f"target adapter module is not file-backed Python source: {module_name}")
    package_directory = origin.parent
    sources = sorted(
        {
            path.resolve()
            for path in package_directory.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".pyi"}
            and "__pycache__" not in path.parts
        },
        key=lambda path: path.as_posix(),
    )
    if not sources:
        raise HarnessFailure(f"target adapter module closure is empty: {module_name}")
    return sources


def _command_digest(command: Sequence[str]) -> str:
    """Bind executable plus the target adapter/package closure, not this process's import."""

    materials: list[dict[str, str]] = []
    normalized_command: list[str] = []
    for index, argument in enumerate(command):
        candidate = Path(argument)
        if candidate.is_file():
            materials.append(
                {
                    "label": f"command-file:{index}:{candidate.name}",
                    "digest": _digest(candidate.read_bytes()),
                }
            )
            normalized_command.append(f"<file:{index}:{candidate.name}>")
        else:
            normalized_command.append(argument)

    if "-m" in command:
        module_index = list(command).index("-m") + 1
        if module_index >= len(command):
            raise HarnessFailure("-m requires a Python module name")
        module_name = command[module_index]
        sources = _target_module_sources(command, module_name)
        package_parent = sources[0].parent.parent
        for source in sources:
            materials.append(
                {
                    "label": f"module-closure:{source.relative_to(package_parent).as_posix()}",
                    "digest": _digest(source.read_bytes()),
                }
            )
    projection = {"command": normalized_command, "materials": materials}
    return _digest(rfc8785.dumps(projection))


def _closure_digest(paths: Sequence[Path], root: Path) -> str | None:
    regular = sorted(
        {path.resolve() for path in paths if path.is_file()},
        key=lambda item: item.as_posix(),
    )
    if not regular:
        return None
    projection = [
        {
            "path": (
                path.relative_to(root.resolve()).as_posix()
                if root.resolve() in path.parents
                else path.name
            ),
            "digest": _digest(path.read_bytes()),
        }
        for path in regular
    ]
    return _digest(rfc8785.dumps(projection))


def _source_closure_digest(
    command: Sequence[str], explicit_source_root: Path | None
) -> str | None:
    if explicit_source_root is not None and explicit_source_root.is_dir():
        is_go_closure = (explicit_source_root / "go.mod").is_file()

        def executable_source(path: Path) -> bool:
            relative = path.relative_to(explicit_source_root)
            if (
                not path.is_file()
                or path.name == ".DS_Store"
                or ".git" in path.parts
                or "__pycache__" in path.parts
                or any(part.startswith(".") for part in relative.parts)
                or "tests" in relative.parts
            ):
                return False
            if is_go_closure:
                return path.name in {"go.mod", "go.sum"} or (
                    path.suffix == ".go" and not path.name.endswith("_test.go")
                )
            return path.suffix in {".py", ".pyi"} and not (
                path.name.startswith("test_") or path.name.endswith("_test.py")
            )

        sources = [
            path
            for path in explicit_source_root.rglob("*")
            if executable_source(path)
        ]
        return _closure_digest(sources, explicit_source_root)
    if "-m" in command:
        module_index = list(command).index("-m") + 1
        if module_index >= len(command):
            return None
        specification = importlib.util.find_spec(command[module_index])
        if specification is None or specification.origin is None:
            return None
        origin = Path(specification.origin)
        return _closure_digest(list(origin.parent.rglob("*.py")), origin.parent.parent)
    script_sources = [Path(item) for item in command[1:] if item.endswith(".py")]
    if script_sources:
        return _closure_digest(script_sources, script_sources[0].parent)
    return None


def _runtime_language_evidence(command: Sequence[str]) -> tuple[object, dict[str, object]]:
    if not command:
        return None, {"language": None, "verified": False, "method": "no-command"}
    executable = Path(command[0])
    if not executable.is_file():
        return None, {
            "language": None,
            "verified": False,
            "method": "unresolved-executable",
        }
    environment = {
        key: value for key, value in os.environ.items() if key in _ENVIRONMENT_ALLOWLIST
    }
    try:
        version = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            check=False,
            env=environment,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        version = None
    if version is not None:
        version_raw = (version.stdout + version.stderr).strip()
        first_line = version_raw.splitlines()[0] if version_raw else b""
        if version.returncode == 0 and first_line.startswith((b"Python ", b"PyPy ")):
            runtime = {
                "descriptor": first_line.decode("utf-8", errors="replace"),
                "evidenceDigest": _digest(version_raw),
            }
            return runtime, {
                "language": "Python",
                "verified": True,
                "method": "bound-executable-version-probe",
            }
    try:
        go_version = subprocess.run(
            ["go", "version", "-m", str(executable)],
            capture_output=True,
            check=False,
            env=environment,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        go_version = None
    go_lines = go_version.stdout.splitlines() if go_version is not None else []
    if (
        go_version is not None
        and go_version.returncode == 0
        and go_lines
        and b": go1" in go_lines[0]
    ):
        _, separator, runtime_version = go_lines[0].partition(b": ")
        normalized_go_raw = b"<executable>" + separator + runtime_version + b"\n" + b"\n".join(
            go_lines[1:]
        )
        runtime = {
            "descriptor": runtime_version.decode("utf-8", errors="replace"),
            "evidenceDigest": _digest(normalized_go_raw),
        }
        return runtime, {
            "language": "Go",
            "verified": True,
            "method": "go-binary-build-info",
        }
    return None, {
        "language": None,
        "verified": False,
        "method": "runtime-not-verifiable",
    }


def _implementation_evidence(
    command: Sequence[str],
    capability: Mapping[str, Any],
    *,
    source_root: Path | None = None,
) -> dict[str, object]:
    runtime, language_evidence = _runtime_language_evidence(command)
    return {
        "implementationId": capability["implementationId"],
        "implementationVersion": capability["implementationVersion"],
        "commandDigest": _command_digest(command),
        "sourceClosureDigest": _source_closure_digest(command, source_root),
        "runtime": runtime,
        "languageEvidence": language_evidence,
    }


def _opaque_request_id(sequence: int) -> str:
    material = f"oac.seed2.request/{sequence}".encode()
    return f"req-{hashlib.sha256(material).hexdigest()[:24]}"


def _generator_runtime_evidence() -> dict[str, object]:
    try:
        dependency_version: str | None = importlib.metadata.version("rfc8785")
    except importlib.metadata.PackageNotFoundError:
        dependency_version = None
    return {
        "language": "Python",
        "runtimeImplementation": platform.python_implementation(),
        "runtimeVersion": platform.python_version(),
        "dependencies": [
            {
                "name": "rfc8785",
                "version": dependency_version,
            }
        ],
    }


def _observation(
    implementation_id: str,
    build_digest: str,
    result: Mapping[str, Any],
) -> dict[str, object]:
    projection: dict[str, object] = {
        "implementationId": implementation_id,
        "implementationBuildDigest": build_digest,
        "artifactDigests": {"operationObservation": result["digest"]},
    }
    return {**projection, "observationDigest": _digest(rfc8785.dumps(projection))}


def _disagreement(
    *,
    case_id: str,
    capsule_digest: str,
    left_id: str,
    left_build: str,
    left: Mapping[str, Any],
    right_id: str,
    right_build: str,
    right: Mapping[str, Any],
    generated_case: Mapping[str, Any] | None = None,
    generator_artifact_digest: str | None = None,
    stage: str = "CANONICAL_BYTES",
) -> dict[str, object]:
    observations = [
        _observation(left_id, left_build, left),
        _observation(right_id, right_build, right),
    ]
    discriminator = _digest(rfc8785.dumps(observations))[7:31]
    record: dict[str, object] = {
        "apiVersion": "oac.ctk.disagreement/v0.1",
        "disagreementId": f"disagreement:{discriminator}",
        "caseId": case_id,
        "bundleDigest": capsule_digest,
        "stage": stage,
        "observations": observations,
        "classification": "CONTESTED_SEMANTICS",
        "status": "OPEN",
        "owner": "oac-portability-triage",
        "affectedVersion": "oac.supplier.transfer/v0.2",
        "resolutionRef": None,
        "regressionCaseRef": None,
    }
    if generated_case is not None:
        if generator_artifact_digest is None:
            raise HarnessFailure("generated disagreement lacks generator artifact identity")
        record["generatedCase"] = {
            "generatorId": "oac.supplier-v02.recipe-generator",
            "generatorVersion": "v2",
            "generatorDigest": generator_artifact_digest,
            "seed": generated_case["publicSeed"],
            "originalInputDigest": generated_case["inputDigest"],
            "minimizedReproducerDigest": generated_case["inputDigest"],
            "minimizerVersion": "identity/v1",
        }
    return {**record, "digest": _digest(rfc8785.dumps(record))}


def _mutant_script(
    frozen_cases: Sequence[Mapping[str, Any]],
    capability_set: Mapping[str, Any] | None = None,
) -> str:
    if capability_set is None:
        capability_set = _admit_capability_set(DEFAULT_CAPABILITY_SET.read_bytes())
    table: dict[str, dict[str, Any]] = {}
    fallback: dict[str, Any] | None = None
    for item in frozen_cases:
        key = _digest(item["snapshot"])[7:] + ":" + _digest(item["change"])[7:]
        report = _loads(item["expectedJcs"])
        assert isinstance(report, dict)
        table[key] = report
        if fallback is None:
            fallback = report
    encoded_table = base64.b64encode(rfc8785.dumps(table)).decode("ascii")
    encoded_fallback = base64.b64encode(rfc8785.dumps(fallback)).decode("ascii")
    encoded_tracks = base64.b64encode(
        rfc8785.dumps(capability_set["tracks"])
    ).decode("ascii")
    return f"""import base64,hashlib,json,sys\nimport rfc8785\nPROTOCOL={PROTOCOL_VERSION!r}\nTABLE=json.loads(base64.b64decode({encoded_table!r}))\nFALLBACK=json.loads(base64.b64decode({encoded_fallback!r}))\nTRACKS=json.loads(base64.b64decode({encoded_tracks!r}))\nr=json.load(sys.stdin); op=r["operation"]\nif op=="capabilities":\n result={{"implementationId":"oac.mutant.canned-three","implementationVersion":"v1","adapterProtocolVersion":PROTOCOL,"tracks":TRACKS}}\nelse:\n p=r["payload"]; s=base64.b64decode(p["snapshotBase64"]); c=base64.b64decode(p["changeBase64"]); key=hashlib.sha256(s).hexdigest()+":"+hashlib.sha256(c).hexdigest(); report=TABLE.get(key,FALLBACK); raw=rfc8785.dumps(report); result={{"report":report,"reportDigest":"sha256:"+hashlib.sha256(raw).hexdigest()}}\nresponse={{"protocolVersion":PROTOCOL,"requestId":r["requestId"],"sutStatus":"COMPLETED","result":result}}\nsys.stdout.buffer.write(rfc8785.dumps(response)+b"\\n")\n"""


def _root_forgery_mutant_script(
    capability_set: Mapping[str, Any],
    frozen_cases: Sequence[Mapping[str, Any]],
    variant_results: Sequence[Mapping[str, Any]],
) -> str:
    table: dict[str, dict[str, Any]] = {}
    for item in frozen_cases:
        key = _digest(item["snapshot"])[7:] + ":" + _digest(item["change"])[7:]
        report = _loads(item["expectedJcs"])
        assert isinstance(report, dict)
        table[key] = {"status": "COMPLETED", "report": report}
    for item in variant_results:
        case = item["case"]
        reference = item["reference"]
        key = _digest(case["snapshot"])[7:] + ":" + _digest(case["change"])[7:]
        if reference["status"] == "COMPLETED":
            table[key] = {
                "status": "COMPLETED",
                "report": reference["response"]["result"]["report"],
            }
        else:
            table[key] = {
                "status": reference["status"],
                "errorCode": reference["response"]["error"]["code"],
            }
    encoded_tracks = base64.b64encode(
        rfc8785.dumps(capability_set["tracks"])
    ).decode("ascii")
    encoded_table = base64.b64encode(rfc8785.dumps(table)).decode("ascii")
    return f"""import base64,hashlib,json,sys\nimport rfc8785\nPROTOCOL={PROTOCOL_VERSION!r}\nTRACKS=json.loads(base64.b64decode({encoded_tracks!r}))\nTABLE=json.loads(base64.b64decode({encoded_table!r}))\nr=json.load(sys.stdin)\nif r["operation"]=="capabilities":\n response={{"protocolVersion":PROTOCOL,"requestId":r["requestId"],"sutStatus":"COMPLETED","result":{{"implementationId":"oac.mutant.root-unknown-forgery","implementationVersion":"v1","adapterProtocolVersion":PROTOCOL,"tracks":TRACKS}}}}\nelse:\n p=r["payload"]; snapshot_raw=base64.b64decode(p["snapshotBase64"]); change_raw=base64.b64decode(p["changeBase64"]); key=hashlib.sha256(snapshot_raw).hexdigest()+":"+hashlib.sha256(change_raw).hexdigest(); outcome=TABLE.get(key)\n if outcome is None:\n  response={{"protocolVersion":PROTOCOL,"requestId":r["requestId"],"sutStatus":"UNSUPPORTED","error":{{"code":"MUTANT_CASE_UNSUPPORTED"}}}}\n elif outcome["status"]!="COMPLETED":\n  response={{"protocolVersion":PROTOCOL,"requestId":r["requestId"],"sutStatus":outcome["status"],"error":{{"code":outcome["errorCode"]}}}}\n else:\n  report=outcome["report"]; snapshot=json.loads(snapshot_raw); change=json.loads(change_raw); subject=change["spec"]["subjectRef"]; status=next(n["admissionStatus"] for n in snapshot["spec"]["nodes"] if n["nodeId"]==subject)\n  if status in ("candidate","disputed"):\n   report["rootApplicabilityUnknown"]=False\n  raw=rfc8785.dumps(report); response={{"protocolVersion":PROTOCOL,"requestId":r["requestId"],"sutStatus":"COMPLETED","result":{{"report":report,"reportDigest":"sha256:"+hashlib.sha256(raw).hexdigest()}}}}\nsys.stdout.buffer.write(rfc8785.dumps(response)+b"\\n")\n"""


def run_parity(
    python_command: Sequence[str],
    go_command: Sequence[str],
    *,
    capsule_path: Path = DEFAULT_CAPSULE,
    mutant_directory: Path | None = None,
    python_source_root: Path | None = None,
    go_source_root: Path | None = None,
) -> dict[str, object]:
    capsule, frozen_cases = load_capsule(capsule_path)
    capability_set, recipe_set = _load_bound_seed2_artifacts(capsule_path, capsule)
    report_validator = _pinned_report_validator(capsule_path, capsule)
    for item in frozen_cases:
        expected_report = _loads(item["expectedJcs"])
        errors = sorted(
            report_validator.iter_errors(expected_report),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise HarnessFailure(
                f"frozen expected report violates pinned schema: {errors[0].message}"
            )
    variants = generate_variants(frozen_cases, recipe_set)
    validate_cases = build_validate_cases(frozen_cases)

    request_sequence = 0

    def next_request_id() -> str:
        nonlocal request_sequence
        request_sequence += 1
        return _opaque_request_id(request_sequence)

    python_caps = _capabilities(
        python_command,
        next_request_id(),
        capability_set,
    )
    go_caps = _capabilities(
        go_command,
        next_request_id(),
        capability_set,
    )
    python_id = python_caps["implementationId"]
    go_id = go_caps["implementationId"]
    if not isinstance(python_id, str) or not isinstance(go_id, str) or python_id == go_id:
        raise HarnessFailure("implementations require distinct non-empty implementationId values")
    implementation_evidence = [
        _implementation_evidence(
            python_command,
            python_caps,
            source_root=python_source_root,
        ),
        _implementation_evidence(
            go_command,
            go_caps,
            source_root=go_source_root,
        ),
    ]
    python_build = implementation_evidence[0]["commandDigest"]
    go_build = implementation_evidence[1]["commandDigest"]
    assert isinstance(python_build, str)
    assert isinstance(go_build, str)
    disagreements: list[dict[str, object]] = []
    observation_entries: list[dict[str, object]] = []
    validate_passed = 0
    derive_passed = 0

    for item in validate_cases:
        left = _validate_observation(
            python_command,
            expected_kind=item["expectedKind"],
            raw_base64=item["rawBase64"],
            request_id=next_request_id(),
        )
        right = _validate_observation(
            go_command,
            expected_kind=item["expectedKind"],
            raw_base64=item["rawBase64"],
            request_id=next_request_id(),
        )
        expectation = item["expect"]
        matches_expectation = left["status"] == right["status"] == expectation["status"]
        if expectation["status"] == "COMPLETED":
            matches_expectation = matches_expectation and (
                left["response"]["result"] == right["response"]["result"] == expectation["result"]
            )
        else:
            matches_expectation = matches_expectation and (
                left["response"]["error"]["code"]
                == right["response"]["error"]["code"]
                == expectation["errorCode"]
            )
        passed_case = bool(matches_expectation and left["jcs"] == right["jcs"])
        observation_entries.append(
            {
                "caseId": item["caseId"],
                "operation": "validateResource",
                "semanticClass": f"admission-{item['class']}",
                "inputDigest": _digest(
                    rfc8785.dumps(
                        {
                            "expectedKind": item["expectedKind"],
                            "rawBase64": item["rawBase64"],
                        }
                    )
                ),
                "leftStatus": left["status"],
                "rightStatus": right["status"],
                "leftObservationDigest": left["digest"],
                "rightObservationDigest": right["digest"],
                "passed": passed_case,
            }
        )
        if passed_case:
            validate_passed += 1
        else:
            disagreements.append(
                _disagreement(
                    case_id=item["caseId"],
                    capsule_digest=capsule["capsuleDigest"],
                    left_id=python_id,
                    left_build=python_build,
                    left=left,
                    right_id=go_id,
                    right_build=go_build,
                    right=right,
                    stage="SCHEMA",
                )
            )

    for item in frozen_cases:
        left = _derive_observation(
            python_command,
            item["snapshot"],
            item["change"],
            next_request_id(),
            report_validator=report_validator,
        )
        right = _derive_observation(
            go_command,
            item["snapshot"],
            item["change"],
            next_request_id(),
            report_validator=report_validator,
        )
        matches = (
            left["jcs"] == right["jcs"] == item["expectedJcs"]
            and left["digest"] == right["digest"] == item["expectedDigest"]
        )
        observation_entries.append(
            {
                "caseId": item["caseId"],
                "operation": "derive",
                "semanticClass": "frozen-baseline",
                "inputDigest": _digest(item["snapshot"] + b"\x00" + item["change"]),
                "leftStatus": left["status"],
                "rightStatus": right["status"],
                "leftObservationDigest": left["digest"],
                "rightObservationDigest": right["digest"],
                "passed": matches,
            }
        )
        if matches:
            derive_passed += 1
        else:
            disagreements.append(
                _disagreement(
                    case_id=item["caseId"],
                    capsule_digest=capsule["capsuleDigest"],
                    left_id=python_id,
                    left_build=python_build,
                    left=left,
                    right_id=go_id,
                    right_build=go_build,
                    right=right,
                )
            )

    variant_results: list[dict[str, Any]] = []
    for item in variants:
        left = _derive_observation(
            python_command,
            item["snapshot"],
            item["change"],
            next_request_id(),
            report_validator=report_validator,
        )
        right = _derive_observation(
            go_command,
            item["snapshot"],
            item["change"],
            next_request_id(),
            report_validator=report_validator,
        )
        variant_results.append({"case": item, "reference": left})
        matches = left["jcs"] == right["jcs"] and left["digest"] == right["digest"]
        observation_entries.append(
            {
                "caseId": item["caseId"],
                "operation": "derive",
                "semanticClass": item["semanticClass"],
                "inputDigest": item["inputDigest"],
                "leftStatus": left["status"],
                "rightStatus": right["status"],
                "leftObservationDigest": left["digest"],
                "rightObservationDigest": right["digest"],
                "passed": matches,
            }
        )
        if matches:
            derive_passed += 1
        else:
            disagreements.append(
                _disagreement(
                    case_id=item["caseId"],
                    capsule_digest=capsule["capsuleDigest"],
                    left_id=python_id,
                    left_build=python_build,
                    left=left,
                    right_id=go_id,
                    right_build=go_build,
                    right=right,
                    generated_case=item,
                    generator_artifact_digest=capsule["generator"]["source"][
                        "rawSha256"
                    ],
                )
            )

    mutant_root = mutant_directory or Path(tempfile.mkdtemp(prefix="oac-supplier-mutant-"))
    mutant_root.mkdir(parents=True, exist_ok=True)
    mutant_path = mutant_root / "canned_three_mutant.py"
    mutant_path.write_text(
        _mutant_script(frozen_cases, capability_set),
        encoding="utf-8",
    )
    mutant_artifact_digest = _digest(mutant_path.read_bytes())
    mutant_command = [sys.executable, str(mutant_path)]
    _capabilities(mutant_command, next_request_id(), capability_set)
    mutant_frozen_passed = 0
    for item in frozen_cases:
        observed = _derive_observation(
            mutant_command,
            item["snapshot"],
            item["change"],
            next_request_id(),
            report_validator=report_validator,
        )
        if observed["jcs"] == item["expectedJcs"]:
            mutant_frozen_passed += 1
    mutant_rejected_by: str | None = None
    for item in variant_results:
        case = item["case"]
        try:
            observed = _derive_observation(
                mutant_command,
                case["snapshot"],
                case["change"],
                next_request_id(),
                report_validator=report_validator,
            )
            rejected = observed["jcs"] != item["reference"]["jcs"]
        except HarnessFailure:
            rejected = True
        if rejected:
            mutant_rejected_by = case["caseId"]
            break
    mutant_rejected = mutant_frozen_passed == len(frozen_cases) and mutant_rejected_by is not None

    root_forgery_path = mutant_root / "root_unknown_forgery_mutant.py"
    root_forgery_path.write_text(
        _root_forgery_mutant_script(
            capability_set,
            frozen_cases,
            variant_results,
        ),
        encoding="utf-8",
    )
    root_forgery_artifact_digest = _digest(root_forgery_path.read_bytes())
    root_forgery_command = [sys.executable, str(root_forgery_path)]
    _capabilities(root_forgery_command, next_request_id(), capability_set)
    root_forgery_frozen_passed = 0
    for item in frozen_cases:
        observed = _derive_observation(
            root_forgery_command,
            item["snapshot"],
            item["change"],
            next_request_id(),
            report_validator=report_validator,
        )
        if observed["jcs"] == item["expectedJcs"]:
            root_forgery_frozen_passed += 1
    root_forgery_rejected_by: str | None = None
    for item in variant_results:
        case = item["case"]
        try:
            observed = _derive_observation(
                root_forgery_command,
                case["snapshot"],
                case["change"],
                next_request_id(),
                report_validator=report_validator,
            )
            rejected = observed["jcs"] != item["reference"]["jcs"]
        except HarnessFailure:
            rejected = True
        if rejected:
            root_forgery_rejected_by = case["caseId"]
            break
    root_forgery_rejected = (
        root_forgery_frozen_passed == len(frozen_cases)
        and root_forgery_rejected_by is not None
    )

    derive_total = len(frozen_cases) + len(variants)
    validate_frozen = sum(item["class"] == "frozen" for item in validate_cases)
    validate_probes = len(validate_cases) - validate_frozen
    validate_total = len(validate_cases)
    total = derive_total + validate_total
    passed = derive_passed + validate_passed
    observation_manifest: dict[str, object] = {
        "apiVersion": "oac.portability.observations/v0alpha1",
        "kind": "SupplierPortabilityObservationManifest",
        "capsuleDigest": capsule["capsuleDigest"],
        "capabilitySetDigest": capability_set["capabilitySetDigest"],
        "generatorArtifactDigest": capsule["generator"]["source"]["rawSha256"],
        "recipeSetDigest": recipe_set["recipeSetDigest"],
        "implementationMaterials": [
            {
                "implementationId": item["implementationId"],
                "implementationVersion": item["implementationVersion"],
                "commandDigest": item["commandDigest"],
                "sourceClosureDigest": item["sourceClosureDigest"],
            }
            for item in implementation_evidence
        ],
        "observations": observation_entries,
    }
    observation_manifest_digest = _digest(rfc8785.dumps(observation_manifest))
    class_totals = Counter(
        str(item["semanticClass"]) for item in observation_entries
    )
    class_passes = Counter(
        str(item["semanticClass"])
        for item in observation_entries
        if item["passed"] is True
    )
    semantic_class_stats = [
        {
            "semanticClass": semantic_class,
            "totalCases": class_totals[semantic_class],
            "passedCases": class_passes[semantic_class],
        }
        for semantic_class in sorted(class_totals)
    ]
    verified_languages = [
        item["languageEvidence"]["language"]
        for item in implementation_evidence
        if item["languageEvidence"]["verified"] is True
    ]
    cross_language_evidence = (
        len(verified_languages) == 2
        and len(set(verified_languages)) == 2
        and all(item["sourceClosureDigest"] is not None for item in implementation_evidence)
    )
    return {
        "apiVersion": "oac.portability.parity-summary/v0alpha1",
        "kind": "SupplierPortabilityParitySummary",
        "capsuleDigest": capsule["capsuleDigest"],
        "harnessArtifactDigest": _digest(Path(__file__).read_bytes()),
        "generatorArtifactDigest": capsule["generator"]["source"]["rawSha256"],
        "recipeSetArtifactDigest": capsule["generator"]["recipeSet"]["rawSha256"],
        "recipeSetDigest": recipe_set["recipeSetDigest"],
        "capabilitySetDigest": capability_set["capabilitySetDigest"],
        "profileId": "oac.supplier.transfer",
        "profileVersion": "v0.2",
        "boundedCapabilityId": capability_set["capabilitySetId"],
        "implementations": implementation_evidence,
        "generatorRuntimeEvidence": _generator_runtime_evidence(),
        "observationManifest": observation_manifest,
        "observationManifestDigest": observation_manifest_digest,
        "semanticClassStats": semantic_class_stats,
        "validateFrozenCases": validate_frozen,
        "validateProbeCases": validate_probes,
        "validateTotalCases": validate_total,
        "validatePassedCases": validate_passed,
        "deriveFrozenCases": len(frozen_cases),
        "deriveGeneratedCases": len(variants),
        "deriveTotalCases": derive_total,
        "derivePassedCases": derive_passed,
        "totalCases": total,
        "passedCases": passed,
        "mutants": [
            {
                "mutantId": "oac.mutant.canned-three",
                "artifactDigest": mutant_artifact_digest,
                "frozenCasesPassed": mutant_frozen_passed,
                "rejected": mutant_rejected,
                "rejectedBy": mutant_rejected_by,
                "conclusion": (
                    "canned-three-rejected"
                    if mutant_rejected
                    else "canned-three-not-rejected"
                ),
            },
            {
                "mutantId": "oac.mutant.root-unknown-forgery",
                "artifactDigest": root_forgery_artifact_digest,
                "frozenCasesPassed": root_forgery_frozen_passed,
                "rejected": root_forgery_rejected,
                "rejectedBy": root_forgery_rejected_by,
                "conclusion": (
                    "root-unknown-forgery-rejected"
                    if root_forgery_rejected
                    else "root-unknown-forgery-not-rejected"
                ),
            },
        ],
        "claim": {
            "established": (
                "internal-bounded-cross-language-two-implementation-parity"
                if cross_language_evidence
                else "internal-bounded-two-implementation-parity"
            ),
            "scope": "exact frozen and public generated seed-2 capsule cases only",
            "excludes": [
                "clean-room independence",
                "organizational independence",
                "complete OAC conformance",
                "enterprise correctness",
                "standard consensus",
            ],
        },
        "disagreements": disagreements,
        "status": (
            "PASS"
            if validate_passed == validate_total
            and derive_passed == derive_total
            and mutant_rejected
            and root_forgery_rejected
            else "FAIL"
        ),
    }


def _parse_command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise HarnessFailure("adapter command cannot be empty")
    return command


def _build_go(output: Path) -> list[str]:
    source = ROOT / "implementations/go-supplier-v02-internal"
    if not (source / "go.mod").is_file():
        raise HarnessFailure(
            "Go v2 source is absent; pass --go-command or implement the internal v0.2 adapter"
        )
    completed = subprocess.run(
        ["go", "build", "-trimpath", "-o", str(output), "."],
        cwd=source,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise HarnessFailure(completed.stderr.decode("utf-8", errors="replace"))
    return [str(output)]


def parity_summary_bytes(summary: Mapping[str, Any]) -> bytes:
    """Serialize one live parity result in the only accepted summary encoding."""

    return rfc8785.dumps(summary) + b"\n"


def check_stored_summary(summary: Mapping[str, Any], path: Path) -> bytes:
    """Require ``path`` to be the exact canonical bytes of ``summary``.

    The stable errors intentionally do not include the host path or parser details, so
    callers can use this as a deterministic release-evidence gate.
    """

    live_raw = parity_summary_bytes(summary)
    try:
        stored_raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise HarnessFailure("stored parity summary is missing") from exc
    except OSError as exc:
        raise HarnessFailure("stored parity summary cannot be read") from exc

    try:
        stored_value = _loads(stored_raw)
        canonical_stored_raw = rfc8785.dumps(stored_value) + b"\n"
    except (HarnessFailure, TypeError, ValueError) as exc:
        raise HarnessFailure(
            "stored parity summary is not exact JCS plus newline"
        ) from exc
    if stored_raw != canonical_stored_raw:
        raise HarnessFailure("stored parity summary is not exact JCS plus newline")
    if stored_raw != live_raw:
        raise HarnessFailure("stored parity summary differs from live parity summary")
    return live_raw


def portable_summary_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only host/runtime identities from a parity summary.

    This projection lets Linux CI compare semantic evidence with a Darwin capture without
    pretending that executable or runtime bytes are portable.  Source closures, public materials,
    every observation, mutants, dependency versions, claims, and capability identities remain
    exact.  Release evidence continues to use :func:`check_stored_summary` for byte identity.
    """

    projected = copy.deepcopy(dict(summary))
    try:
        implementations = projected["implementations"]
        observation_materials = projected["observationManifest"][
            "implementationMaterials"
        ]
        generator_runtime = projected["generatorRuntimeEvidence"]
        if (
            not isinstance(implementations, list)
            or not isinstance(observation_materials, list)
            or len(implementations) != 2
            or len(observation_materials) != 2
            or not isinstance(generator_runtime, dict)
        ):
            raise TypeError
        for implementation, material in zip(
            implementations, observation_materials, strict=True
        ):
            if not isinstance(implementation, dict) or not isinstance(material, dict):
                raise TypeError
            implementation_id = implementation["implementationId"]
            if material["implementationId"] != implementation_id:
                raise ValueError
            implementation["commandDigest"] = f"<host-command:{implementation_id}>"
            implementation["runtime"] = {
                "descriptor": f"<host-runtime:{implementation_id}>",
                "evidenceDigest": f"<host-runtime-digest:{implementation_id}>",
            }
            material["commandDigest"] = f"<host-command:{implementation_id}>"
        projected["observationManifestDigest"] = (
            "<host-normalized-observation-manifest-digest>"
        )
        generator_runtime["runtimeVersion"] = "<host-generator-runtime-version>"
    except (KeyError, TypeError, ValueError) as exc:
        raise HarnessFailure("parity summary cannot form the portable projection") from exc
    return projected


def check_stored_summary_portable(summary: Mapping[str, Any], path: Path) -> bytes:
    """Compare the exact semantic projection while allowing only declared host identities."""

    live_raw = parity_summary_bytes(summary)
    try:
        stored_raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise HarnessFailure("stored parity summary is missing") from exc
    except OSError as exc:
        raise HarnessFailure("stored parity summary cannot be read") from exc
    try:
        stored_value = _loads(stored_raw)
        canonical_stored_raw = rfc8785.dumps(stored_value) + b"\n"
    except (HarnessFailure, TypeError, ValueError) as exc:
        raise HarnessFailure(
            "stored parity summary is not exact JCS plus newline"
        ) from exc
    if stored_raw != canonical_stored_raw:
        raise HarnessFailure("stored parity summary is not exact JCS plus newline")
    if rfc8785.dumps(portable_summary_projection(summary)) != rfc8785.dumps(
        portable_summary_projection(stored_value)
    ):
        raise HarnessFailure(
            "stored parity summary differs from the live portable projection"
        )
    return live_raw


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capsule", type=Path, default=DEFAULT_CAPSULE)
    parser.add_argument(
        "--python-command", default=f"{shlex.quote(sys.executable)} -m oac.ctk_adapter_v2"
    )
    parser.add_argument("--go-command")
    summary_group = parser.add_mutually_exclusive_group()
    summary_group.add_argument(
        "--check-summary",
        type=Path,
        help="fail unless PATH is exact JCS+LF and byte-identical to the live summary",
    )
    summary_group.add_argument(
        "--check-summary-portable",
        type=Path,
        help="compare semantics while normalizing only declared host/runtime identities",
    )
    args = parser.parse_args(argv)
    try:
        python_command = _parse_command(args.python_command)
        with tempfile.TemporaryDirectory(prefix="oac-go-v02-") as temporary:
            built_internal_go = args.go_command is None
            go_command = (
                _parse_command(args.go_command)
                if args.go_command is not None
                else _build_go(Path(temporary) / "oac-go-supplier-v02")
            )
            summary = run_parity(
                python_command,
                go_command,
                capsule_path=args.capsule,
                mutant_directory=Path(temporary) / "mutant",
                python_source_root=ROOT / "src/oac",
                go_source_root=(
                    ROOT / "implementations/go-supplier-v02-internal"
                    if built_internal_go
                    else None
                ),
            )
        if args.check_summary is not None:
            summary_raw = check_stored_summary(summary, args.check_summary)
        elif args.check_summary_portable is not None:
            summary_raw = check_stored_summary_portable(
                summary, args.check_summary_portable
            )
        else:
            summary_raw = parity_summary_bytes(summary)
        sys.stdout.buffer.write(summary_raw)
        return 0 if summary["status"] == "PASS" else 1
    except HarnessFailure as exc:
        failure = {
            "apiVersion": "oac.portability.parity-summary/v0alpha1",
            "kind": "SupplierPortabilityParitySummary",
            "status": "HARNESS_ERROR",
            "error": {"code": "CTK_HARNESS_INVALID", "detail": str(exc)},
        }
        sys.stdout.buffer.write(rfc8785.dumps(failure) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
