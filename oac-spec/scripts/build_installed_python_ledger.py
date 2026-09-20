#!/usr/bin/env python3
"""Build a closed ledger for a wheel observed in an isolated Python environment.

The target interpreter performs the environment observation under ``-I`` from
an empty temporary working directory.  The resulting ledger is deliberately a
self-attested replay binding, not an operating-system or execution-provenance
attestation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path, PurePosixPath
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "ctk/schemas/InstalledPythonDistributionLedger.schema.json"
DEFAULT_SEMANTIC_SOURCE_ROOT = ROOT / "src/oac"
LIMITATIONS = (
    "python-standard-library-not-covered",
    "operating-system-not-covered",
    "cryptographic-execution-provenance-not-covered",
)
REPLAY_BINDING_CLASS = "self-attested-isolated-process-observation/v1"
COLLECTOR_MODE = "target-python--I-empty-cwd/v1"


class InstalledLedgerError(RuntimeError):
    """The wheel or observed installation cannot support an exact ledger."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _closure(value: object) -> str:
    return _digest(rfc8785.dumps(value))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InstalledLedgerError(f"duplicate JSON key in target observation: {key}")
        value[key] = item
    return value


def _normalized_distribution_name(value: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", value).lower()
    if not normalized or normalized.startswith("-") or normalized.endswith("-"):
        raise InstalledLedgerError(f"invalid distribution name: {value!r}")
    return normalized


def _validate_relative_path(value: str, *, label: str) -> tuple[str, ...]:
    if not value or value != unicodedata.normalize("NFC", value):
        raise InstalledLedgerError(f"non-canonical {label}: {value!r}")
    if "\\" in value or "\x00" in value or value.startswith("/") or value.endswith("/"):
        raise InstalledLedgerError(f"unsafe {label}: {value!r}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InstalledLedgerError(f"control character in {label}: {value!r}")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InstalledLedgerError(f"path escape in {label}: {value!r}")
    if ":" in parts[0] or value != "/".join(parts):
        raise InstalledLedgerError(f"non-normalized {label}: {value!r}")
    return parts


def _repository_relative(path: Path, *, label: str) -> str:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(ROOT.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise InstalledLedgerError(f"{label} escapes repository: {path}") from exc
    _validate_relative_path(relative, label=label)
    return relative


def _regular_file_descriptor(path: Path, relative_path: str) -> dict[str, object]:
    if path.is_symlink():
        raise InstalledLedgerError(f"symlink is not admissible: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise InstalledLedgerError(f"not a regular file: {path}")
    raw = resolved.read_bytes()
    return {
        "path": relative_path,
        "rawSha256": _digest(raw),
        "sizeBytes": len(raw),
    }


def _semantic_source_ledger(source_root: Path) -> dict[str, object]:
    source_root = source_root.resolve(strict=True)
    source_root_relative = _repository_relative(source_root, label="semantic source root")
    if not source_root.is_dir():
        raise InstalledLedgerError(f"semantic source root is not a directory: {source_root}")

    files: list[dict[str, object]] = []
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise InstalledLedgerError(f"symlink below semantic source root: {path}")
        if not path.is_file() or path.suffix not in {".py", ".pyi"}:
            continue
        relative_path = path.relative_to(source_root)
        relative = relative_path.as_posix()
        if "__pycache__" in relative_path.parts or any(
            part.startswith(".") for part in relative_path.parts
        ):
            continue
        _validate_relative_path(relative, label="semantic source path")
        files.append(_regular_file_descriptor(path, relative))
    if not files:
        raise InstalledLedgerError("semantic production source ledger is empty")
    return {
        "algorithm": "sha256-jcs-file-ledger/v1",
        "sourceRoot": source_root_relative,
        "files": files,
        "fileCount": len(files),
        "closureDigest": _closure(files),
    }


def _repository_inputs() -> dict[str, object]:
    pyproject = _regular_file_descriptor(ROOT / "pyproject.toml", "pyproject.toml")
    uv_lock = _regular_file_descriptor(ROOT / "uv.lock", "uv.lock")
    artifacts = [pyproject, uv_lock]
    return {
        "algorithm": "sha256-jcs-file-ledger/v1",
        "pyprojectToml": pyproject,
        "uvLock": uv_lock,
        "closureDigest": _closure(artifacts),
    }


def _read_wheel(wheel_path: Path) -> dict[str, Any]:
    if wheel_path.is_symlink():
        raise InstalledLedgerError(f"wheel path is a symlink: {wheel_path}")
    wheel_path = wheel_path.resolve(strict=True)
    if not wheel_path.is_file() or wheel_path.suffix != ".whl":
        raise InstalledLedgerError(f"not a wheel file: {wheel_path}")
    wheel_raw = wheel_path.read_bytes()

    entries: list[dict[str, object]] = []
    entry_raw: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    seen_casefolded_paths: set[str] = set()
    try:
        archive_context = zipfile.ZipFile(wheel_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstalledLedgerError(f"invalid wheel ZIP: {wheel_path}") from exc

    with archive_context as archive:
        infos = archive.infolist()
        if not infos:
            raise InstalledLedgerError("wheel contains no entries")
        for info in infos:
            name = info.filename
            _validate_relative_path(name, label="wheel entry path")
            casefolded = name.casefold()
            if name in seen_paths or casefolded in seen_casefolded_paths:
                raise InstalledLedgerError(f"duplicate or case-colliding wheel path: {name}")
            seen_paths.add(name)
            seen_casefolded_paths.add(casefolded)
            if info.is_dir():
                raise InstalledLedgerError(f"directory wheel entry is not canonical: {name}")
            if info.flag_bits & 0x1:
                raise InstalledLedgerError(f"encrypted wheel entry is not admissible: {name}")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type == stat.S_IFLNK:
                raise InstalledLedgerError(f"symlink wheel entry is not admissible: {name}")
            if file_type not in {0, stat.S_IFREG}:
                raise InstalledLedgerError(f"non-regular wheel entry is not admissible: {name}")
            try:
                raw = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise InstalledLedgerError(f"cannot read wheel entry: {name}") from exc
            if len(raw) != info.file_size:
                raise InstalledLedgerError(f"wheel entry size mismatch: {name}")
            entry_raw[name] = raw
            entries.append(
                {
                    "path": name,
                    "rawSha256": _digest(raw),
                    "sizeBytes": len(raw),
                    "compressedSizeBytes": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "compressionMethod": info.compress_type,
                    "generalPurposeFlagBits": info.flag_bits,
                    "externalAttributes": info.external_attr,
                }
            )

    entries.sort(key=lambda item: str(item["path"]))
    metadata_paths = [path for path in entry_raw if path.endswith(".dist-info/METADATA")]
    if len(metadata_paths) != 1:
        raise InstalledLedgerError("wheel must contain exactly one .dist-info/METADATA")
    metadata_path = metadata_paths[0]
    dist_info_root = metadata_path.removesuffix("/METADATA")
    record_path = f"{dist_info_root}/RECORD"
    wheel_metadata_path = f"{dist_info_root}/WHEEL"
    if record_path not in entry_raw or wheel_metadata_path not in entry_raw:
        raise InstalledLedgerError("wheel is missing its RECORD or WHEEL metadata")
    metadata = BytesParser(policy=compat32).parsebytes(entry_raw[metadata_path])
    distribution_name = metadata.get("Name")
    distribution_version = metadata.get("Version")
    if not isinstance(distribution_name, str) or not distribution_name.strip():
        raise InstalledLedgerError("wheel METADATA has no valid Name")
    if not isinstance(distribution_version, str) or not distribution_version.strip():
        raise InstalledLedgerError("wheel METADATA has no valid Version")

    return {
        "wheel": {
            "fileName": wheel_path.name,
            "distributionName": distribution_name,
            "normalizedDistributionName": _normalized_distribution_name(distribution_name),
            "version": distribution_version,
            "rawSha256": _digest(wheel_raw),
            "sizeBytes": len(wheel_raw),
            "recordPath": record_path,
            "entryCount": len(entries),
            "entries": entries,
            "entryPreimageDigest": _closure(entries),
        },
        "entryRaw": entry_raw,
    }


TARGET_PROBE = r'''import hashlib
import importlib.metadata as metadata
import json
import os
import pathlib
import platform
import re
import stat
import sys

def fail(message):
    raise RuntimeError(message)

def digest_file(path):
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            value.update(chunk)
    return "sha256:" + value.hexdigest(), size

def normalize_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()

prefix = pathlib.Path(sys.prefix).resolve(strict=True)
if not sys.flags.isolated:
    fail("target interpreter did not enter isolated mode")
if list(pathlib.Path.cwd().iterdir()):
    fail("target observation working directory is not empty")

def relative_under_prefix(candidate, label):
    absolute = pathlib.Path(os.path.abspath(candidate))
    if absolute.is_symlink():
        fail(label + " is a symlink: " + str(absolute))
    resolved = absolute.resolve(strict=True)
    try:
        relative_path = resolved.relative_to(prefix)
    except ValueError as exc:
        raise RuntimeError(label + " escapes target environment") from exc
    cursor = prefix
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            fail(label + " traverses a symlink: " + str(cursor))
    relative = relative_path.as_posix()
    if not relative or relative.startswith("../") or "\\" in relative:
        fail("non-canonical target environment path: " + relative)
    return relative, resolved

distributions = []
seen_distributions = set()
for distribution in metadata.distributions():
    name = distribution.metadata.get("Name")
    version = distribution.version
    if not isinstance(name, str) or not name.strip():
        fail("installed distribution has no Name")
    if not isinstance(version, str) or not version.strip():
        fail("installed distribution has no Version: " + name)
    normalized_name = normalize_name(name)
    distribution_root, root_path = relative_under_prefix(
        distribution.locate_file(""), "distribution root for " + name
    )
    if not root_path.is_dir():
        fail("distribution root is not a directory: " + name)
    private_metadata_path = getattr(distribution, "_path", None)
    if private_metadata_path is None:
        fail("installed distribution metadata path is unavailable: " + name)
    metadata_path, resolved_metadata_path = relative_under_prefix(
        private_metadata_path, "metadata path for " + name
    )
    if not resolved_metadata_path.is_dir():
        fail("distribution metadata path is not a directory: " + name)
    identity = (normalized_name, version, metadata_path)
    if identity in seen_distributions:
        fail("duplicate installed distribution identity: " + repr(identity))
    seen_distributions.add(identity)
    declared_files = distribution.files
    if declared_files is None:
        fail("installed distribution has no file inventory: " + name)
    files = []
    seen_files = set()
    for declared_file in declared_files:
        environment_path, resolved_file = relative_under_prefix(
            distribution.locate_file(declared_file), "installed file for " + name
        )
        if environment_path in seen_files:
            fail("duplicate installed file path for " + name + ": " + environment_path)
        seen_files.add(environment_path)
        if not resolved_file.is_file() or not stat.S_ISREG(resolved_file.stat().st_mode):
            fail("installed distribution entry is not a regular file: " + environment_path)
        raw_sha256, size_bytes = digest_file(resolved_file)
        files.append({
            "path": environment_path,
            "rawSha256": raw_sha256,
            "sizeBytes": size_bytes,
        })
    if not files:
        fail("installed distribution file inventory is empty: " + name)
    files.sort(key=lambda item: item["path"])
    distributions.append({
        "name": name,
        "normalizedName": normalized_name,
        "version": version,
        "installationRootPath": distribution_root,
        "metadataPath": metadata_path,
        "files": files,
    })

distributions.sort(key=lambda item: (
    item["normalizedName"], item["version"], item["metadataPath"]
))
executable = pathlib.Path(sys.executable).resolve(strict=True)
if not executable.is_file() or not stat.S_ISREG(executable.stat().st_mode):
    fail("target Python executable is not a regular file")
executable_digest, executable_size = digest_file(executable)
result = {
    "observation": {
        "isolatedFlag": bool(sys.flags.isolated),
        "safePathFlag": bool(getattr(sys.flags, "safe_path", False)),
        "workingDirectoryEntryCount": len(list(pathlib.Path.cwd().iterdir())),
    },
    "targetPython": {
        "executableRawSha256": executable_digest,
        "executableSizeBytes": executable_size,
        "version": platform.python_version(),
        "implementation": sys.implementation.name,
        "cacheTag": sys.implementation.cache_tag,
    },
    "distributions": distributions,
}
print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
'''


def _require_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InstalledLedgerError(f"{label} is not an object")
    return value


def _observe_target(python_path: Path) -> dict[str, Any]:
    if not python_path.exists():
        raise InstalledLedgerError(f"target Python does not exist: {python_path}")
    if not os.access(python_path, os.X_OK):
        raise InstalledLedgerError(f"target Python is not executable: {python_path}")
    sanitized_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
    }
    with tempfile.TemporaryDirectory(prefix="oac-installed-ledger-probe-") as temporary:
        working_directory = Path(temporary)
        if any(working_directory.iterdir()):
            raise InstalledLedgerError("temporary observation directory was not empty")
        try:
            result = subprocess.run(
                [str(python_path), "-I", "-c", TARGET_PROBE],
                cwd=working_directory,
                env=sanitized_environment,
                check=False,
                capture_output=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstalledLedgerError("target Python observation failed to execute") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise InstalledLedgerError(f"target Python observation failed: {detail}")
    try:
        value = json.loads(result.stdout, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InstalledLedgerError("target Python returned invalid observation JSON") from exc
    observation = _require_mapping(value, label="target observation")
    probe_state = _require_mapping(observation.get("observation"), label="probe state")
    if (
        probe_state.get("isolatedFlag") is not True
        or probe_state.get("safePathFlag") is not True
        or probe_state.get("workingDirectoryEntryCount") != 0
    ):
        raise InstalledLedgerError("target observation did not preserve -I and empty-cwd isolation")

    target_python = dict(_require_mapping(observation.get("targetPython"), label="target Python"))
    expected_target_fields = {
        "executableRawSha256",
        "executableSizeBytes",
        "version",
        "implementation",
        "cacheTag",
    }
    if set(target_python) != expected_target_fields:
        raise InstalledLedgerError("target Python descriptor is not closed")
    distributions_value = observation.get("distributions")
    if not isinstance(distributions_value, list) or not distributions_value:
        raise InstalledLedgerError("target environment distribution inventory is empty")

    distributions: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str, str]] = set()
    for raw_distribution in distributions_value:
        distribution = dict(
            _require_mapping(raw_distribution, label="installed distribution")
        )
        expected_fields = {
            "name",
            "normalizedName",
            "version",
            "installationRootPath",
            "metadataPath",
            "files",
        }
        if set(distribution) != expected_fields:
            raise InstalledLedgerError("installed distribution descriptor is not closed")
        for field in ("installationRootPath", "metadataPath"):
            value_path = distribution.get(field)
            if not isinstance(value_path, str):
                raise InstalledLedgerError(f"installed distribution {field} is invalid")
            _validate_relative_path(value_path, label=f"installed distribution {field}")
        name = distribution.get("name")
        normalized_name = distribution.get("normalizedName")
        version = distribution.get("version")
        metadata_path = distribution.get("metadataPath")
        if not all(isinstance(item, str) and item for item in (name, normalized_name, version)):
            raise InstalledLedgerError("installed distribution identity is invalid")
        if normalized_name != _normalized_distribution_name(name):
            raise InstalledLedgerError(f"installed normalized name drift: {name}")
        identity = (normalized_name, version, str(metadata_path))
        if identity in seen_identities:
            raise InstalledLedgerError(f"duplicate installed distribution: {identity}")
        seen_identities.add(identity)
        raw_files = distribution.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise InstalledLedgerError(f"installed file ledger is empty: {name}")
        files: list[dict[str, object]] = []
        seen_files: set[str] = set()
        for raw_file in raw_files:
            file_descriptor = dict(
                _require_mapping(raw_file, label="installed distribution file")
            )
            if set(file_descriptor) != {"path", "rawSha256", "sizeBytes"}:
                raise InstalledLedgerError("installed file descriptor is not closed")
            path_value = file_descriptor.get("path")
            if not isinstance(path_value, str):
                raise InstalledLedgerError("installed file path is invalid")
            _validate_relative_path(path_value, label="installed environment path")
            if path_value in seen_files:
                raise InstalledLedgerError(f"duplicate installed environment path: {path_value}")
            seen_files.add(path_value)
            files.append(file_descriptor)
        files.sort(key=lambda item: str(item["path"]))
        distribution["files"] = files
        distribution["fileCount"] = len(files)
        distribution["fileLedgerDigest"] = _closure(files)
        distributions.append(distribution)

    distributions.sort(
        key=lambda item: (
            str(item["normalizedName"]),
            str(item["version"]),
            str(item["metadataPath"]),
        )
    )
    return {
        "observation": {
            "collectorMode": COLLECTOR_MODE,
            "isolatedFlag": True,
            "safePathFlag": True,
            "workingDirectoryWasEmpty": True,
        },
        "targetPython": target_python,
        "environment": {
            "algorithm": "sha256-jcs-installed-distribution-ledger/v1",
            "distributionCount": len(distributions),
            "distributions": distributions,
            "environmentClosureDigest": _closure(distributions),
        },
    }


def _installed_payload(
    wheel: Mapping[str, Any], environment: Mapping[str, Any]
) -> dict[str, object]:
    raw_distributions = environment.get("distributions")
    if not isinstance(raw_distributions, list):
        raise InstalledLedgerError("environment distribution ledger is invalid")
    matching = [
        item
        for item in raw_distributions
        if isinstance(item, Mapping)
        and item.get("normalizedName") == wheel.get("normalizedDistributionName")
        and item.get("version") == wheel.get("version")
    ]
    if len(matching) != 1:
        raise InstalledLedgerError(
            "target environment must contain exactly one matching wheel distribution"
        )
    target_distribution = matching[0]
    installation_root = target_distribution.get("installationRootPath")
    raw_files = target_distribution.get("files")
    if not isinstance(installation_root, str) or not isinstance(raw_files, list):
        raise InstalledLedgerError("matching installed distribution is incomplete")
    installed_files = {
        item["path"]: item
        for item in raw_files
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    if len(installed_files) != len(raw_files):
        raise InstalledLedgerError("matching installed distribution has duplicate file paths")

    record_path = wheel.get("recordPath")
    raw_entries = wheel.get("entries")
    if not isinstance(record_path, str) or not isinstance(raw_entries, list):
        raise InstalledLedgerError("wheel entry ledger is incomplete")
    compared: list[dict[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            raise InstalledLedgerError("wheel entry descriptor is invalid")
        wheel_path = entry.get("path")
        if not isinstance(wheel_path, str):
            raise InstalledLedgerError("wheel entry path is invalid")
        if wheel_path == record_path:
            continue
        installed_path = PurePosixPath(installation_root, wheel_path).as_posix()
        _validate_relative_path(installed_path, label="installed payload path")
        installed = installed_files.get(installed_path)
        if installed is None:
            raise InstalledLedgerError(
                f"wheel entry is absent from installed distribution inventory: {wheel_path}"
            )
        wheel_digest = entry.get("rawSha256")
        installed_digest = installed.get("rawSha256")
        wheel_size = entry.get("sizeBytes")
        installed_size = installed.get("sizeBytes")
        if wheel_digest != installed_digest or wheel_size != installed_size:
            raise InstalledLedgerError(f"installed payload byte mismatch: {wheel_path}")
        compared.append(
            {
                "wheelPath": wheel_path,
                "installedPath": installed_path,
                "wheelRawSha256": wheel_digest,
                "installedRawSha256": installed_digest,
                "sizeBytes": wheel_size,
                "byteEqual": True,
            }
        )
    compared.sort(key=lambda item: str(item["wheelPath"]))
    if len(compared) != int(wheel.get("entryCount", 0)) - 1:
        raise InstalledLedgerError("installed payload comparison is not complete")
    return {
        "distributionName": wheel["distributionName"],
        "normalizedDistributionName": wheel["normalizedDistributionName"],
        "version": wheel["version"],
        "excludedComparisonPaths": [record_path],
        "exclusionReason": "pip-rewritten-record/v1",
        "files": compared,
        "matchedFileCount": len(compared),
        "payloadByteMatch": True,
        "payloadClosureDigest": _closure(compared),
    }


def _runtime_binding_projection(ledger: Mapping[str, Any]) -> dict[str, object]:
    wheel = _require_mapping(ledger["wheel"], label="wheel")
    installed_payload = _require_mapping(
        ledger["installedPayload"], label="installed payload"
    )
    environment = _require_mapping(ledger["installedEnvironment"], label="environment")
    repository = _require_mapping(ledger["repositoryInputs"], label="repository inputs")
    semantic = _require_mapping(ledger["semanticProduction"], label="semantic production")
    target_python = _require_mapping(ledger["targetPython"], label="target Python")
    return {
        "wheelRawSha256": wheel["rawSha256"],
        "wheelEntryPreimageDigest": wheel["entryPreimageDigest"],
        "installedPayloadClosureDigest": installed_payload["payloadClosureDigest"],
        "targetPython": dict(target_python),
        "environmentClosureDigest": environment["environmentClosureDigest"],
        "repositoryInputClosureDigest": repository["closureDigest"],
        "semanticProductionSourceClosureDigest": semantic["closureDigest"],
    }


def build_ledger(
    *,
    wheel_path: Path,
    python_path: Path,
    semantic_source_root: Path = DEFAULT_SEMANTIC_SOURCE_ROOT,
) -> dict[str, Any]:
    """Return a fully closed installed-distribution observation ledger."""

    wheel_result = _read_wheel(wheel_path)
    wheel = wheel_result["wheel"]
    target = _observe_target(python_path)
    repository = _repository_inputs()
    semantic = _semantic_source_ledger(semantic_source_root)
    installed_payload = _installed_payload(wheel, target["environment"])
    ledger: dict[str, Any] = {
        "apiVersion": "oac.installed-material-ledger/v0alpha2",
        "kind": "InstalledPythonDistributionLedger",
        "serialization": "rfc8785+jcs+lf/v1",
        "replayBindingClass": REPLAY_BINDING_CLASS,
        "observation": target["observation"],
        "wheel": wheel,
        "installedPayload": installed_payload,
        "targetPython": target["targetPython"],
        "installedEnvironment": target["environment"],
        "repositoryInputs": repository,
        "semanticProduction": semantic,
        "limitations": list(LIMITATIONS),
        "digestAlgorithm": "sha256-jcs-detached/v1",
    }
    ledger["runtimeBindingDigest"] = _closure(_runtime_binding_projection(ledger))
    ledger["digest"] = _closure(ledger)

    schema = json.loads(SCHEMA_PATH.read_bytes(), object_pairs_hook=_reject_duplicate_keys)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(ledger),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise InstalledLedgerError(f"ledger schema validation failed at {location}: {errors[0].message}")
    return ledger


def _write_output(raw: bytes, output: Path | None) -> None:
    if output is None:
        sys.stdout.buffer.write(raw)
        return
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path, help="wheel installed in target env")
    parser.add_argument(
        "--python", required=True, type=Path, help="target environment Python executable"
    )
    parser.add_argument(
        "--semantic-source-root",
        type=Path,
        default=DEFAULT_SEMANTIC_SOURCE_ROOT,
        help="repository production source root (default: src/oac)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write atomically to this path (default: exact JCS+LF on stdout)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        ledger = build_ledger(
            wheel_path=args.wheel,
            python_path=args.python,
            semantic_source_root=args.semantic_source_root,
        )
        _write_output(rfc8785.dumps(ledger) + b"\n", args.output)
    except (InstalledLedgerError, OSError, ValueError) as exc:
        print(f"installed ledger error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
