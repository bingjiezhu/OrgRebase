#!/usr/bin/env python3
"""Repair existing Skill seals or author an explicitly versioned successor.

Same-version repairs retain the package's original licensing metadata. A new
version carries the current project grant as a verified notice attachment,
separate from interpreter resources. This tool creates no release authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.workspace.skill_packages import _PROJECT_SKILL_LICENSE, SkillPackageRegistry

RESOURCE_PATHS = {
    "skill": "SKILL.md",
    "reference_zh_cn": "references/zh-CN.md",
    "reference_en": "references/en.md",
    "contract": "contract.json",
    "program": "program.json",
    "input_schema": "input.schema.json",
    "output_schema": "output.schema.json",
}


def _object(raw: bytes, path: Path) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _encoded(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _raw_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def reseal_skill_package(
    root: str | Path,
    name: str,
    *,
    bump_version: str | None = None,
) -> dict[str, Any]:
    """Reseal a prepared authoring tree; retained historical trees stay frozen."""

    checkout = Path(root).resolve()
    inputs: dict[Path, bytes] = {}
    planned: dict[Path, bytes] = {}

    def read(path: Path) -> bytes:
        if path not in inputs:
            inputs[path] = path.read_bytes()
        return inputs[path]

    registry_path = checkout / "configs" / "workspace" / "skill-registry.json"
    registry = _object(read(registry_path), registry_path)
    entries = registry.get("packages")
    if not isinstance(entries, list):
        raise ValueError("skill registry packages must be a list")
    matches = [item for item in entries if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one registry entry for {name}")
    entry = matches[0]
    registry_reader = SkillPackageRegistry(checkout)
    manifest_path = registry_reader._package_resource(entry, "package.json")
    package_root = manifest_path.parent
    manifest = _object(read(manifest_path), manifest_path)
    old_digest = str(manifest.get("manifest_digest", ""))
    resources = manifest.get("resources")
    if not isinstance(resources, dict) or set(resources) != set(RESOURCE_PATHS):
        raise ValueError("manifest must declare the exact seven v2 resources")
    resource_bytes: dict[str, bytes] = {}
    for resource_name, expected_path in RESOURCE_PATHS.items():
        declaration = resources.get(resource_name)
        if not isinstance(declaration, dict) or declaration.get("path") != expected_path:
            raise ValueError(f"invalid resource declaration: {resource_name}")
        resource_bytes[resource_name] = read(package_root / expected_path)
    program_path = package_root / "program.json"
    contract_path = package_root / "contract.json"
    program = _object(resource_bytes["program"], program_path)
    contract = _object(resource_bytes["contract"], contract_path)
    input_schema = _object(resource_bytes["input_schema"], package_root / RESOURCE_PATHS["input_schema"])
    output_schema = _object(resource_bytes["output_schema"], package_root / RESOURCE_PATHS["output_schema"])
    if (
        input_schema.get("$id") != manifest.get("input_schema_ref")
        or output_schema.get("$id") != manifest.get("output_schema_ref")
    ):
        raise ValueError("packaged Schema $id must equal manifest Schema ref")
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("manifest dependencies must be an object")
    if bump_version is not None:
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", bump_version) is None:
            raise ValueError("bump_version must be semantic major.minor.patch")
        if bump_version == manifest.get("version"):
            raise ValueError("bump_version must differ from current package version")
        # Validate the complete current notice before writing any package file.
        # Existing same-version repairs do not reinterpret their old license.
        license_files = registry_reader._project_license_files()
        distribution = contract.get("distribution")
        if not isinstance(distribution, dict):
            raise ValueError("new Skill version must declare distribution metadata")
        distribution["license"] = _PROJECT_SKILL_LICENSE
        manifest["license"] = _PROJECT_SKILL_LICENSE
        manifest["license_files"] = license_files
        manifest["version"] = bump_version
        manifest["package_id"] = f"skill-package:{name}@{bump_version}"
        manifest["release_artifact"]["id"] = f"skill-release:{name}@{bump_version}"
        manifest["release_artifact"]["predecessor_package_digest"] = old_digest
        skill_path = package_root / "SKILL.md"
        skill_text = resource_bytes["skill"].decode("utf-8")
        skill_text, count = re.subn(
            r"(?m)^  version: [0-9]+\.[0-9]+\.[0-9]+$",
            f"  version: {bump_version}",
            skill_text,
            count=1,
        )
        if count != 1:
            raise ValueError("SKILL.md metadata must declare exactly one semantic version")
        resource_bytes["skill"] = skill_text.encode("utf-8")
        planned[skill_path] = resource_bytes["skill"]
        contract["version"] = ".".join(bump_version.split(".")[:2])

    program["digest"] = sha256_digest(
        {key: value for key, value in program.items() if key != "digest"}
    )
    resource_bytes["program"] = planned[program_path] = _encoded(program)
    contract["content_digest"] = sha256_digest(
        {key: value for key, value in contract.items() if key != "content_digest"}
    )
    resource_bytes["contract"] = planned[contract_path] = _encoded(contract)

    for resource_name in RESOURCE_PATHS:
        resources[resource_name]["sha256"] = _raw_digest(resource_bytes[resource_name])
    dependencies[str(manifest["input_schema_ref"])] = resources["input_schema"]["sha256"]
    dependencies[str(manifest["output_schema_ref"])] = resources["output_schema"]["sha256"]
    manifest["program_content_digest"] = program["digest"]
    manifest["manifest_digest"] = sha256_digest(
        {key: value for key, value in manifest.items() if key != "manifest_digest"}
    )
    planned[manifest_path] = _encoded(manifest)
    entry["version"] = manifest["version"]
    entry["manifest_digest"] = manifest["manifest_digest"]
    planned[registry_path] = _encoded(registry)

    class PlannedRegistry(SkillPackageRegistry):
        """Exercise the real loader against planned bytes without writing them."""

        @staticmethod
        def _read_bytes(resource: Any) -> bytes:
            path = Path(resource)
            if path in planned:
                return planned[path]
            if path in inputs:
                return inputs[path]
            return SkillPackageRegistry._read_bytes(resource)

    PlannedRegistry(checkout).load(name, expected_package_digest=manifest["manifest_digest"])
    if any(path.read_bytes() != original for path, original in inputs.items()):
        raise ValueError("Skill authoring input changed during preflight")
    # The complete package and registry have passed the normal loader before
    # touching the authoring tree. This is a local authoring operation, not a
    # crash-atomic publication or an authorization to change retained history.
    for path, raw in planned.items():
        if raw != inputs[path]:
            path.write_bytes(raw)
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
    parser.add_argument(
        "--bump-version",
        help="Author a new semantic version with the current Apache notice; omit for exact repair.",
    )
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
