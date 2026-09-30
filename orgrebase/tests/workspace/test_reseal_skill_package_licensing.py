"""Version authoring carries current notices; exact repairs keep old metadata."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.skill_packages import (
    EXPECTED_ENTRY_POINTS,
    SkillPackageRegistry,
    _json_schema_valid,
    load_skill_package_snapshot,
    skill_license_files,
    skill_package_snapshot,
)
from scripts.reseal_skill_packages import reseal_skill_package

ROOT = Path(__file__).resolve().parents[2]
LEGACY_LICENSE = "PolyForm-Noncommercial-1.0.0"


def _copy_tree(path: Path, *, include_license: bool = True) -> Path:
    shutil.copytree(ROOT / "configs/workspace", path / "configs/workspace")
    shutil.copytree(ROOT / "skills", path / "skills")
    if include_license:
        shutil.copyfile(ROOT / "LICENSE", path / "LICENSE")
    return path


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("name", sorted(EXPECTED_ENTRY_POINTS))
def test_same_version_repair_keeps_original_license_and_exact_bytes(tmp_path, name):
    root = _copy_tree(tmp_path / "repair", include_license=False)
    before = _hashes(root)
    original = SkillPackageRegistry(root).load(name)
    receipt = reseal_skill_package(root, name)
    repaired = SkillPackageRegistry(root).load(name)
    assert receipt["status"] == "PASS"
    assert repaired.package_digest == original.package_digest
    assert repaired.manifest["license"] == LEGACY_LICENSE
    assert repaired.contract["distribution"]["license"] == LEGACY_LICENSE
    assert skill_license_files(repaired) == {}
    assert _hashes(root) == before


@pytest.mark.parametrize("name", sorted(EXPECTED_ENTRY_POINTS))
def test_new_version_reseal_carries_verified_apache_notice_without_execution_resource(tmp_path, name):
    root = _copy_tree(tmp_path / "authoring")
    original_inputs = _hashes(ROOT / "skills")
    original = SkillPackageRegistry(root).load(name)
    original_snapshot = skill_package_snapshot(original)
    receipt = reseal_skill_package(root, name, bump_version="9.0.0")
    current = SkillPackageRegistry(root).load(name)
    assert receipt["status"] == "PASS"
    assert current.version == "9.0.0"
    assert current.manifest["license"] == "Apache-2.0"
    assert current.contract["distribution"]["license"] == "Apache-2.0"
    assert current.manifest["release_artifact"]["predecessor_package_digest"] == original.package_digest
    assert skill_license_files(current) == {"LICENSE": (root / "LICENSE").read_bytes()}
    assert len(current.manifest["resources"]) == len(current.raw_resource_bytes) == 7
    assert "license" not in current.resource_digests
    for resource in ("program", "input_schema", "output_schema", "reference_zh_cn", "reference_en"):
        assert current.raw_resource_bytes[resource] == original.raw_resource_bytes[resource]
    assert current.manifest["permissions"] == original.manifest["permissions"]
    assert current.manifest["release_artifact"]["source_candidate_executable"] is False
    assert current.package_digest != original.package_digest
    assert load_skill_package_snapshot(skill_package_snapshot(current)).manifest == current.manifest
    assert load_skill_package_snapshot(original_snapshot).manifest == original.manifest
    schema = json.loads((ROOT / "schemas/skill-package-manifest-v2.schema.json").read_bytes())
    assert _json_schema_valid(current.manifest, schema)
    assert set(schema["required"]) <= set(current.manifest)
    assert set(current.manifest) <= set(schema["properties"])
    assert _json_schema_valid(current.manifest["license"], schema["properties"]["license"])
    assert _json_schema_valid(current.manifest["license_files"], {
        **schema["properties"]["license_files"], "$defs": schema["$defs"],
    })
    assert _hashes(ROOT / "skills") == original_inputs


@pytest.mark.parametrize("invalid_notice", [None, b"Apache License 2.0 (truncated)"])
def test_invalid_notice_rejects_new_version_before_any_package_write(tmp_path, invalid_notice):
    root = _copy_tree(tmp_path / "invalid", include_license=False)
    if invalid_notice is not None:
        (root / "LICENSE").write_bytes(invalid_notice)
    before = _hashes(root)
    error = "SKILL_PACKAGE_RESOURCE_MISSING" if invalid_notice is None else "SKILL_LICENSE_FILE_INVALID"
    with pytest.raises(IntegrityError, match=error):
        reseal_skill_package(root, "structured-domain-handoff", bump_version="9.0.0")
    assert _hashes(root) == before


def test_same_version_repair_of_new_apache_version_retains_notice_and_digest(tmp_path):
    root = _copy_tree(tmp_path / "new-repair")
    name = "structured-domain-handoff"
    reseal_skill_package(root, name, bump_version="9.0.0")
    before = _hashes(root)
    package = SkillPackageRegistry(root).load(name)
    reseal_skill_package(root, name)
    repaired = SkillPackageRegistry(root).load(name)
    assert repaired.package_digest == package.package_digest
    assert skill_license_files(repaired) == skill_license_files(package)
    assert _hashes(root) == before


def test_registry_rejects_self_resealed_apache_manifest_with_notice_removed(tmp_path):
    root = _copy_tree(tmp_path / "missing-apache-notice")
    name = "structured-domain-handoff"
    reseal_skill_package(root, name, bump_version="9.0.0")
    manifest_path = root / "skills" / name / "package.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest.pop("license_files")
    manifest["manifest_digest"] = sha256_digest({
        key: value for key, value in manifest.items() if key != "manifest_digest"
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry_path = root / "configs/workspace/skill-registry.json"
    registry = json.loads(registry_path.read_bytes())
    next(entry for entry in registry["packages"] if entry["name"] == name)["manifest_digest"] = (
        manifest["manifest_digest"]
    )
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(IntegrityError, match="SKILL_LICENSE_FILE_INVALID"):
        SkillPackageRegistry(root).load(name)
    before = _hashes(root)
    with pytest.raises(IntegrityError, match="SKILL_LICENSE_FILE_INVALID"):
        reseal_skill_package(root, name)
    assert _hashes(root) == before


def test_version_authoring_cli_emits_exact_installed_registry_receipt(tmp_path):
    root = _copy_tree(tmp_path / "cli-authoring")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/reseal_skill_packages.py"),
         "--root", str(root), "--name", "structured-domain-handoff", "--bump-version", "9.0.0"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    package = SkillPackageRegistry(root).load(
        "structured-domain-handoff", expected_package_digest=receipt["manifest_digest"],
    )
    assert receipt["status"] == "PASS"
    assert package.version == receipt["version"] == "9.0.0"
    assert package.manifest["license"] == "Apache-2.0"
    assert skill_license_files(package) == {"LICENSE": (ROOT / "LICENSE").read_bytes()}


@pytest.mark.parametrize("mutation", ["resource-set", "schema-id", "network-schema", "permissions"])
@pytest.mark.parametrize("bump", [False, True])
def test_rejected_authoring_cli_leaves_every_input_byte_unchanged(tmp_path, mutation, bump):
    root = _copy_tree(tmp_path / "rejected-authoring")
    name = "structured-domain-handoff"
    package_root = root / "skills" / name
    target = package_root / ("input.schema.json" if "schema" in mutation else "package.json")
    value = json.loads(target.read_bytes())
    if mutation == "resource-set":
        value["resources"].pop("program")
    elif mutation == "schema-id":
        value["$id"] = "urn:invalid:input-schema"
    elif mutation == "network-schema":
        value["properties"]["candidate_bundle"] = {"$ref": "https://invalid.example/schema.json"}
    else:
        value["permissions"]["allowed_tools"] = ["unreviewed-tool"]
    target.write_text(json.dumps(value), encoding="utf-8")
    before = _hashes(root)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/reseal_skill_packages.py"),
         "--root", str(root), "--name", name,
         *(["--bump-version", "9.0.0"] if bump else [])],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert _hashes(root) == before
