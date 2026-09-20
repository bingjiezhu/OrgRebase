#!/usr/bin/env python3
"""Mechanically reseal one distributable Skill package and its registry entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.workspace.skill_packages import SkillPackageRegistry

RESOURCE_PATHS = {
    "skill": "SKILL.md",
    "reference_zh_cn": "references/zh-CN.md",
    "reference_en": "references/en.md",
    "contract": "contract.json",
    "program": "program.json",
    "input_schema": "input.schema.json",
    "output_schema": "output.schema.json",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _raw_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def reseal_skill_package(
    root: str | Path,
    name: str,
    *,
    bump_version: str | None = None,
) -> dict[str, Any]:
    checkout = Path(root).resolve()
    registry_path = checkout / "configs" / "workspace" / "skill-registry.json"
    registry = _load(registry_path)
    entries = registry.get("packages")
    if not isinstance(entries, list):
        raise ValueError("skill registry packages must be a list")
    matches = [item for item in entries if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one registry entry for {name}")
    entry = matches[0]
    package_root = checkout / "skills" / str(entry["path"])
    manifest_path = package_root / "package.json"
    manifest = _load(manifest_path)
    old_digest = str(manifest.get("manifest_digest", ""))
    if bump_version is not None:
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", bump_version) is None:
            raise ValueError("bump_version must be semantic major.minor.patch")
        if bump_version == manifest.get("version"):
            raise ValueError("bump_version must differ from current package version")
        manifest["version"] = bump_version
        manifest["package_id"] = f"skill-package:{name}@{bump_version}"
        manifest["release_artifact"]["id"] = f"skill-release:{name}@{bump_version}"
        manifest["release_artifact"]["predecessor_package_digest"] = old_digest
        skill_path = package_root / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")
        skill_text, count = re.subn(
            r"(?m)^  version: [0-9]+\.[0-9]+\.[0-9]+$",
            f"  version: {bump_version}",
            skill_text,
            count=1,
        )
        if count != 1:
            raise ValueError("SKILL.md metadata must declare exactly one semantic version")
        skill_path.write_text(skill_text, encoding="utf-8")
        contract_path = package_root / "contract.json"
        contract = _load(contract_path)
        contract["version"] = ".".join(bump_version.split(".")[:2])
        contract["content_digest"] = sha256_digest(
            {key: value for key, value in contract.items() if key != "content_digest"}
        )
        _write(contract_path, contract)

    program_path = package_root / "program.json"
    program = _load(program_path)
    program["digest"] = sha256_digest(
        {key: value for key, value in program.items() if key != "digest"}
    )
    _write(program_path, program)
    contract_path = package_root / "contract.json"
    contract = _load(contract_path)
    contract["content_digest"] = sha256_digest(
        {key: value for key, value in contract.items() if key != "content_digest"}
    )
    _write(contract_path, contract)

    resources = manifest.get("resources")
    if not isinstance(resources, dict) or set(resources) != set(RESOURCE_PATHS):
        raise ValueError("manifest must declare the exact seven v2 resources")
    for resource_name, expected_path in RESOURCE_PATHS.items():
        declaration = resources.get(resource_name)
        if not isinstance(declaration, dict) or declaration.get("path") != expected_path:
            raise ValueError(f"invalid resource declaration: {resource_name}")
        declaration["sha256"] = _raw_digest(package_root / expected_path)
    input_schema = _load(package_root / RESOURCE_PATHS["input_schema"])
    output_schema = _load(package_root / RESOURCE_PATHS["output_schema"])
    if (
        input_schema.get("$id") != manifest.get("input_schema_ref")
        or output_schema.get("$id") != manifest.get("output_schema_ref")
    ):
        raise ValueError("packaged Schema $id must equal manifest Schema ref")
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("manifest dependencies must be an object")
    dependencies[str(manifest["input_schema_ref"])] = resources["input_schema"]["sha256"]
    dependencies[str(manifest["output_schema_ref"])] = resources["output_schema"]["sha256"]
    manifest["program_content_digest"] = program["digest"]
    manifest["manifest_digest"] = sha256_digest(
        {key: value for key, value in manifest.items() if key != "manifest_digest"}
    )
    _write(manifest_path, manifest)
    entry["version"] = manifest["version"]
    entry["manifest_digest"] = manifest["manifest_digest"]
    _write(registry_path, registry)
    loaded = SkillPackageRegistry(checkout).load(
        name, expected_package_digest=manifest["manifest_digest"]
    )
    return {
        "status": "PASS",
        "name": name,
        "version": loaded.version,
        "previous_manifest_digest": old_digest,
        "manifest_digest": loaded.package_digest,
        "resource_digests": loaded.resource_digests,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--name", required=True)
    parser.add_argument("--bump-version")
    args = parser.parse_args()
    print(
        json.dumps(
            reseal_skill_package(args.root, args.name, bump_version=args.bump_version),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
