#!/usr/bin/env python3
"""Generate a deterministic CycloneDX SBOM bound to the lock and artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{quote(name, safe='')}@{quote(version, safe='')}"


def build_sbom(*, lock_path: Path, pyproject_path: Path, artifacts: tuple[Path, ...]) -> dict[str, Any]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    packages = sorted(
        lock.get("package", []),
        key=lambda item: (str(item["name"]).encode(), str(item["version"]).encode()),
    )
    artifact_facts = tuple(
        sorted(
            ((path.name, _sha256(path), path.stat().st_size) for path in artifacts),
            key=lambda item: item[0].encode(),
        )
    )
    lock_hash = _sha256(lock_path)
    pyproject_hash = _sha256(pyproject_path)
    identity = json.dumps(
        {"artifacts": artifact_facts, "lock": lock_hash, "pyproject": pyproject_hash},
        sort_keys=True,
        separators=(",", ":"),
    )
    root_ref = _purl(str(project["name"]), str(project["version"]))
    components: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    root_dependencies: list[str] = []
    for package in packages:
        name = str(package["name"])
        version = str(package["version"])
        ref = _purl(name, version)
        if name == project["name"]:
            root_dependencies = sorted(
                {
                    _purl(str(item["name"]), str(next(p["version"] for p in packages if p["name"] == item["name"])))
                    for item in package.get("dependencies", [])
                    if any(p["name"] == item["name"] for p in packages)
                }
            )
            continue
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
        }
        sdist = package.get("sdist")
        if isinstance(sdist, dict) and str(sdist.get("hash", "")).startswith("sha256:"):
            component["hashes"] = [{"alg": "SHA-256", "content": str(sdist["hash"]).split(":", 1)[1]}]
        components.append(component)
        child_refs = sorted(
            {
                _purl(str(item["name"]), str(next(p["version"] for p in packages if p["name"] == item["name"])))
                for item in package.get("dependencies", [])
                if any(p["name"] == item["name"] for p in packages)
            }
        )
        dependencies.append({"ref": ref, "dependsOn": child_refs})

    artifact_properties = [
        {"name": f"orgrebase.artifact.{name}.sha256", "value": digest}
        for name, digest, _size in artifact_facts
    ] + [
        {"name": f"orgrebase.artifact.{name}.bytes", "value": str(size)}
        for name, _digest, size in artifact_facts
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, identity)}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": str(project["name"]),
                "version": str(project["version"]),
                "purl": root_ref,
                "properties": [
                    {"name": "orgrebase.uv-lock.sha256", "value": lock_hash},
                    {"name": "orgrebase.pyproject.sha256", "value": pyproject_hash},
                    *artifact_properties,
                ],
            }
        },
        "components": components,
        "dependencies": [{"ref": root_ref, "dependsOn": root_dependencies}, *dependencies],
        "properties": [
            {"name": "orgrebase.evidence.class", "value": "OBSERVED_CONTROLLED_LOCAL"},
            {"name": "orgrebase.vulnerability-absence-claimed", "value": "false"},
        ],
    }


def _encoded(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path in (args.lock, args.pyproject, *args.artifact):
        if not path.is_file():
            raise SystemExit(f"SBOM_INPUT_MISSING:{path}")
    encoded = _encoded(
        build_sbom(
            lock_path=args.lock,
            pyproject_path=args.pyproject,
            artifacts=tuple(args.artifact),
        )
    )
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != encoded:
            raise SystemExit("SBOM_DRIFT")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": "CHECK" if args.check else "WRITE",
                "output": args.output.as_posix(),
                "components": len(json.loads(encoded)["components"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
