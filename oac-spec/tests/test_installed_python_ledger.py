from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import warnings
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/build_installed_python_ledger.py"
SCHEMA_PATH = ROOT / "ctk/schemas/InstalledPythonDistributionLedger.schema.json"
LEDGER_PATH = (
    ROOT
    / "experiments/supplier-v02-portability/v0.2-seed-2/replay"
    / "installed-python-ledger.json"
)


def _load_module(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


BUILDER = _load_module(BUILDER_PATH, "_test_build_installed_python_ledger")


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _closure(value: object) -> str:
    return _digest(rfc8785.dumps(value))


def _ledger() -> dict[str, Any]:
    value = json.loads(LEDGER_PATH.read_bytes())
    assert isinstance(value, dict)
    return value


def _artifact(path: Path, relative_path: str) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": relative_path,
        "rawSha256": _digest(raw),
        "sizeBytes": len(raw),
    }


def _object_schemas(
    value: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if value.get("type") == "object":
            yield path, value
        for key, item in value.items():
            yield from _object_schemas(item, (*path, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            yield from _object_schemas(item, (*path, index))


def test_schema_is_valid_closed_and_accepts_the_persisted_v0alpha2_ledger() -> None:
    schema = json.loads(SCHEMA_PATH.read_bytes())
    Draft202012Validator.check_schema(schema)

    object_schemas = list(_object_schemas(schema))
    assert object_schemas
    assert [
        path
        for path, object_schema in object_schemas
        if object_schema.get("additionalProperties") is not False
    ] == []

    validator = Draft202012Validator(schema)
    ledger = _ledger()
    assert ledger["apiVersion"] == "oac.installed-material-ledger/v0alpha2"
    assert list(validator.iter_errors(ledger)) == []

    extended = copy.deepcopy(ledger)
    extended["installedEnvironment"]["distributions"][0]["unbound"] = True
    assert list(validator.iter_errors(extended))


def test_ledger_is_exact_jcs_lf_with_detached_and_runtime_bindings() -> None:
    ledger = _ledger()
    assert LEDGER_PATH.read_bytes() == rfc8785.dumps(ledger) + b"\n"

    detached = {key: item for key, item in ledger.items() if key != "digest"}
    assert ledger["digestAlgorithm"] == "sha256-jcs-detached/v1"
    assert ledger["digest"] == _closure(detached)

    runtime_projection = BUILDER._runtime_binding_projection(ledger)
    assert set(runtime_projection) == {
        "wheelRawSha256",
        "wheelEntryPreimageDigest",
        "installedPayloadClosureDigest",
        "targetPython",
        "environmentClosureDigest",
        "repositoryInputClosureDigest",
        "semanticProductionSourceClosureDigest",
    }
    assert ledger["runtimeBindingDigest"] == _closure(runtime_projection)


def test_wheel_payload_and_environment_counts_closures_order_and_uniqueness() -> None:
    ledger = _ledger()
    wheel = ledger["wheel"]
    entries = wheel["entries"]
    entry_paths = [entry["path"] for entry in entries]
    assert wheel["entryCount"] == len(entries) == 116
    assert entry_paths == sorted(entry_paths)
    assert len(entry_paths) == len(set(entry_paths)) == len(set(map(str.casefold, entry_paths)))
    assert wheel["entryPreimageDigest"] == _closure(entries)

    payload = ledger["installedPayload"]
    payload_files = payload["files"]
    wheel_paths = [item["wheelPath"] for item in payload_files]
    installed_paths = [item["installedPath"] for item in payload_files]
    assert payload["matchedFileCount"] == len(payload_files) == 115
    assert payload["payloadByteMatch"] is True
    assert wheel_paths == sorted(wheel_paths)
    assert len(wheel_paths) == len(set(wheel_paths))
    assert len(installed_paths) == len(set(installed_paths))
    assert payload["payloadClosureDigest"] == _closure(payload_files)
    assert set(entry_paths) - set(wheel_paths) == set(payload["excludedComparisonPaths"])

    environment = ledger["installedEnvironment"]
    distributions = environment["distributions"]
    identities = [
        (item["normalizedName"], item["version"], item["metadataPath"])
        for item in distributions
    ]
    assert environment["distributionCount"] == len(distributions) == 13
    assert identities == sorted(identities)
    assert len(identities) == len(set(identities))
    assert environment["environmentClosureDigest"] == _closure(distributions)

    all_installed_paths: list[str] = []
    for distribution in distributions:
        files = distribution["files"]
        paths = [item["path"] for item in files]
        assert distribution["fileCount"] == len(files)
        assert paths == sorted(paths)
        assert len(paths) == len(set(paths))
        assert distribution["fileLedgerDigest"] == _closure(files)
        all_installed_paths.extend(paths)
    assert len(all_installed_paths) == 442
    assert len(all_installed_paths) == len(set(all_installed_paths))


def test_every_payload_entry_cross_binds_wheel_and_installed_bytes() -> None:
    ledger = _ledger()
    wheel_entries = {item["path"]: item for item in ledger["wheel"]["entries"]}
    installed_files = {
        item["path"]: item
        for distribution in ledger["installedEnvironment"]["distributions"]
        for item in distribution["files"]
    }

    for comparison in ledger["installedPayload"]["files"]:
        wheel = wheel_entries[comparison["wheelPath"]]
        installed = installed_files[comparison["installedPath"]]
        assert comparison["byteEqual"] is True
        assert comparison["wheelRawSha256"] == wheel["rawSha256"]
        assert comparison["installedRawSha256"] == installed["rawSha256"]
        assert comparison["wheelRawSha256"] == comparison["installedRawSha256"]
        assert comparison["sizeBytes"] == wheel["sizeBytes"] == installed["sizeBytes"]


def test_historical_repository_and_semantic_ledgers_are_internally_closed() -> None:
    """The seed-2 ledger binds the captured build, not a later repository HEAD.

    A successor source tree is allowed to add modules without rewriting this
    versioned ledger.  Current-source equality belongs to a new evidence
    coordinate; this test keeps the historical descriptors, closures, and
    ordering independently auditable.
    """

    ledger = _ledger()
    repository = ledger["repositoryInputs"]
    repository_files = [repository["pyprojectToml"], repository["uvLock"]]
    assert [item["path"] for item in repository_files] == ["pyproject.toml", "uv.lock"]
    assert all(item["rawSha256"].startswith("sha256:") for item in repository_files)
    assert all(item["sizeBytes"] > 0 for item in repository_files)
    assert repository["closureDigest"] == _closure(repository_files)

    semantic = ledger["semanticProduction"]
    semantic_files = semantic["files"]
    semantic_paths = [item["path"] for item in semantic_files]
    assert semantic["sourceRoot"] == "src/oac"
    assert semantic_paths == sorted(semantic_paths)
    assert len(semantic_paths) == len(set(semantic_paths))
    assert all(Path(path).suffix in {".py", ".pyi"} for path in semantic_paths)
    assert all(item["rawSha256"].startswith("sha256:") for item in semantic_files)
    assert all(item["sizeBytes"] > 0 for item in semantic_files)
    assert semantic["fileCount"] == len(semantic_files) == 20
    assert semantic["closureDigest"] == _closure(semantic_files)


def test_semantic_source_ledger_does_not_reject_hidden_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / ".hidden-parent" / "repository"
    source_root = repository / "src/oac"
    source_root.mkdir(parents=True)
    visible_source = source_root / "module.py"
    visible_source.write_text("VALUE = 1\n", encoding="utf-8")
    hidden_source = source_root / ".internal.py"
    hidden_source.write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(BUILDER, "ROOT", repository)

    ledger = BUILDER._semantic_source_ledger(source_root)

    assert ledger["sourceRoot"] == "src/oac"
    assert ledger["fileCount"] == 1
    assert ledger["files"] == [_artifact(visible_source, "module.py")]


def test_oac_contract_distribution_binds_each_production_source_to_wheel_and_install() -> None:
    ledger = _ledger()
    matching_distributions = [
        item
        for item in ledger["installedEnvironment"]["distributions"]
        if item["normalizedName"] == "oac-contract"
    ]
    assert len(matching_distributions) == 1
    distribution = matching_distributions[0]
    assert distribution["version"] == ledger["wheel"]["version"]

    wheel_entries = {item["path"]: item for item in ledger["wheel"]["entries"]}
    installed_files = {item["path"]: item for item in distribution["files"]}
    payload_files = {
        item["wheelPath"]: item for item in ledger["installedPayload"]["files"]
    }
    installation_root = distribution["installationRootPath"]

    for source in ledger["semanticProduction"]["files"]:
        assert Path(source["path"]).suffix in {".py", ".pyi"}
        wheel_path = f"oac/{source['path']}"
        installed_path = f"{installation_root}/{wheel_path}"
        wheel = wheel_entries[wheel_path]
        installed = installed_files[installed_path]
        comparison = payload_files[wheel_path]
        assert (source["rawSha256"], source["sizeBytes"]) == (
            wheel["rawSha256"],
            wheel["sizeBytes"],
        )
        assert (source["rawSha256"], source["sizeBytes"]) == (
            installed["rawSha256"],
            installed["sizeBytes"],
        )
        assert comparison["installedPath"] == installed_path


@pytest.mark.parametrize(
    "unsafe_path",
    ["../escape", "inside/../../escape", "/absolute", "windows\\escape", "C:/escape"],
)
def test_relative_path_helper_rejects_escape_and_noncanonical_paths(unsafe_path: str) -> None:
    with pytest.raises(BUILDER.InstalledLedgerError):
        BUILDER._validate_relative_path(unsafe_path, label="test path")


def test_wheel_reader_rejects_path_escape_duplicate_symlink_and_directory_entries(
    tmp_path: Path,
) -> None:
    path_escape = tmp_path / "path-escape.whl"
    with zipfile.ZipFile(path_escape, "w") as archive:
        archive.writestr("../escape", b"escape")

    duplicate = tmp_path / "duplicate.whl"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("duplicate.txt", b"first")
            archive.writestr("duplicate.txt", b"second")

    symlink = tmp_path / "symlink.whl"
    symlink_info = zipfile.ZipInfo("link")
    symlink_info.create_system = 3
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr(symlink_info, b"target")

    directory = tmp_path / "directory.whl"
    directory_info = zipfile.ZipInfo("directory/")
    directory_info.create_system = 3
    directory_info.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
    with zipfile.ZipFile(directory, "w") as archive:
        archive.writestr(directory_info, b"")

    for candidate in (path_escape, duplicate, symlink, directory):
        with pytest.raises(BUILDER.InstalledLedgerError):
            BUILDER._read_wheel(candidate)
