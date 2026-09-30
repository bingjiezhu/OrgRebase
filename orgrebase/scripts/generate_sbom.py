#!/usr/bin/env python3
"""Generate a deterministic CycloneDX SBOM bound to the lock and artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.parse import quote


class LicenseEvidenceError(ValueError):
    """Explicit license metadata has an unusable or conflicting identity."""


class LockEvidenceError(ValueError):
    """The lock does not identify one unambiguous target for a dependency."""


GRAPH_PROFILE = "FULL_LOCK_POTENTIAL_V1"


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _license_expression(value: Any) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value.strip():
        return None, "LICENSE_EXPRESSION_MISSING"
    if any(character in value for character in "\r\n"):
        return None, "LICENSE_EXPRESSION_INVALID"
    try:
        from packaging.licenses import (
            InvalidLicenseExpression,
            canonicalize_license_expression,
        )
    except ImportError:
        # The SBOM can still enumerate exact artifacts without claiming a license
        # was verified by an unavailable optional build-time SPDX validator.
        return None, "SPDX_VALIDATOR_UNAVAILABLE"
    try:
        return str(canonicalize_license_expression(value)), "SPDX_EXPRESSION"
    except InvalidLicenseExpression:
        return None, "LICENSE_EXPRESSION_INVALID"


def _license_evidence(paths: tuple[Path, ...]) -> list[dict[str, Any]]:
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        if not path.is_file():
            raise LicenseEvidenceError(f"SBOM_LICENSE_METADATA_MISSING:{path.name}")
        raw = path.read_bytes()
        metadata = BytesParser(policy=policy.default).parsebytes(raw)
        names = metadata.get_all("Name", [])
        versions = metadata.get_all("Version", [])
        if (
            metadata.defects
            or len(names) != 1
            or len(versions) != 1
            or not re.fullmatch(r"[A-Za-z0-9]+(?:[A-Za-z0-9._-]*[A-Za-z0-9])?", str(names[0]))
            or not str(versions[0]).strip()
        ):
            raise LicenseEvidenceError(f"SBOM_METADATA_IDENTITY_INVALID:{path.name}")
        name = _canonical_name(str(names[0]))
        version = str(versions[0])
        expressions = metadata.get_all("License-Expression", [])
        if len(expressions) > 1:
            expression, reason = None, "LICENSE_EXPRESSION_AMBIGUOUS"
        else:
            expression, reason = _license_expression(str(expressions[0]) if expressions else None)
        fact = {
            "name": name,
            "version": version,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "license_expression": expression,
            "reason": reason,
        }
        key = (name, version)
        retained = by_identity.get(key)
        if retained is not None and retained != fact:
            raise LicenseEvidenceError(f"SBOM_LICENSE_METADATA_CONFLICT:{name}@{version}")
        by_identity[key] = fact
    return sorted(by_identity.values(), key=lambda item: (item["name"], item["version"]))


def _component_license(
    expression: str | None,
    *,
    reason: str,
    evidence_kind: str | None = None,
    evidence_sha256: str | None = None,
) -> dict[str, Any]:
    properties = [
        {"name": "orgrebase.license-status", "value": "DECLARED_SPDX" if expression else "UNKNOWN"},
        {"name": "orgrebase.license-status-reason", "value": reason},
    ]
    if evidence_kind is not None:
        properties.append({"name": "orgrebase.license-evidence.kind", "value": evidence_kind})
    if evidence_sha256 is not None:
        properties.append({"name": "orgrebase.license-evidence.sha256", "value": evidence_sha256})
    result: dict[str, Any] = {"properties": properties}
    if expression is not None:
        result["licenses"] = [{"expression": expression}]
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{quote(name, safe='')}@{quote(version, safe='')}"


def _lock_graph(packages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Retain all locked branches without asserting any platform installed them.

    CycloneDX's dependency array is a conservative union. Each component also
    retains the corresponding lock edges, including optional groups, selected
    extras, markers and explicit version/source selectors.
    """

    by_name: dict[str, list[dict[str, Any]]] = {}
    refs: set[str] = set()
    for package in packages:
        name = _canonical_name(str(package["name"]))
        ref = _purl(name, str(package["version"]))
        if ref in refs:
            raise LockEvidenceError(f"SBOM_LOCK_IDENTITY_AMBIGUOUS:{name}@{package['version']}")
        refs.add(ref)
        by_name.setdefault(name, []).append(package)

    def resolve(edge: dict[str, Any]) -> str:
        name = _canonical_name(str(edge["name"]))
        matches = by_name.get(name, [])
        for selector in ("version", "source"):
            if selector in edge:
                matches = [package for package in matches if package.get(selector) == edge[selector]]
        if len(matches) != 1:
            reason = "MISSING" if not matches else "AMBIGUOUS"
            raise LockEvidenceError(f"SBOM_LOCK_DEPENDENCY_{reason}:{name}")
        return _purl(name, str(matches[0]["version"]))

    graph: dict[str, list[dict[str, Any]]] = {}
    for package in packages:
        ref = _purl(_canonical_name(str(package["name"])), str(package["version"]))
        edges: list[dict[str, Any]] = []
        groups = [("dependencies", None, package.get("dependencies", []))]
        for kind in ("optional-dependencies", "dev-dependencies"):
            groups.extend((kind, group, values) for group, values in sorted(package.get(kind, {}).items()))
        for kind, group, values in groups:
            for edge in values:
                fact = {"ref": resolve(edge), "kind": kind, "lockEdge": edge}
                if group is not None:
                    fact["group"] = group
                edges.append(fact)
        graph[ref] = sorted(edges, key=lambda edge: json.dumps(edge, sort_keys=True, separators=(",", ":")))
    return graph


def _graph_properties(package: dict[str, Any], edges: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"name": "orgrebase.lock-dependency-edges", "value": json.dumps(edges, sort_keys=True, separators=(",", ":"))},
        {
            "name": "orgrebase.lock-resolution-markers",
            "value": json.dumps(package.get("resolution-markers", []), sort_keys=True, separators=(",", ":")),
        },
    ]


def build_sbom(
    *,
    lock_path: Path,
    pyproject_path: Path,
    artifacts: tuple[Path, ...],
    license_metadata_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    packages = sorted(
        lock.get("package", []),
        key=lambda item: (str(item["name"]).encode(), str(item["version"]).encode()),
    )
    graph = _lock_graph(packages)
    artifact_facts = tuple(
        sorted(
            ((path.name, _sha256(path), path.stat().st_size) for path in artifacts),
            key=lambda item: item[0].encode(),
        )
    )
    lock_hash = _sha256(lock_path)
    pyproject_hash = _sha256(pyproject_path)
    license_facts = _license_evidence(license_metadata_paths)
    metadata_by_identity = {(item["name"], item["version"]): item for item in license_facts}
    root_expression, root_license_reason = _license_expression(project.get("license"))
    identity = json.dumps(
        {
            "artifacts": artifact_facts,
            "lock": lock_hash,
            "pyproject": pyproject_hash,
            "license_metadata": license_facts,
            "root_license": {"expression": root_expression, "reason": root_license_reason},
            "dependency_graph_profile": GRAPH_PROFILE,
            "dependency_graph": graph,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    root_ref = _purl(_canonical_name(str(project["name"])), str(project["version"]))
    roots = [package for package in packages if (
        _canonical_name(str(package["name"])) == _canonical_name(str(project["name"]))
        and str(package["version"]) == str(project["version"])
    )]
    if len(roots) != 1:
        raise LockEvidenceError("SBOM_LOCK_PROJECT_IDENTITY_MISMATCH")
    components: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    for package in packages:
        name = _canonical_name(str(package["name"]))
        version = str(package["version"])
        ref = _purl(name, version)
        if ref == root_ref:
            continue
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
        }
        license_fact = metadata_by_identity.get((_canonical_name(name), version))
        if license_fact is None:
            component.update(_component_license(None, reason="EXACT_VERSION_METADATA_NOT_SUPPLIED"))
        else:
            component.update(
                _component_license(
                    license_fact["license_expression"],
                    reason=license_fact["reason"],
                    evidence_kind="PYTHON_CORE_METADATA",
                    evidence_sha256=license_fact["sha256"],
                )
            )
        component["properties"].extend(_graph_properties(package, graph[ref]))
        sdist = package.get("sdist")
        if isinstance(sdist, dict) and str(sdist.get("hash", "")).startswith("sha256:"):
            component["hashes"] = [{"alg": "SHA-256", "content": str(sdist["hash"]).split(":", 1)[1]}]
        components.append(component)
        child_refs = sorted({edge["ref"] for edge in graph[ref]})
        dependencies.append({"ref": ref, "dependsOn": child_refs})

    artifact_properties = [
        {"name": f"orgrebase.artifact.{name}.sha256", "value": digest}
        for name, digest, _size in artifact_facts
    ] + [
        {"name": f"orgrebase.artifact.{name}.bytes", "value": str(size)}
        for name, _digest, size in artifact_facts
    ]
    root_license = _component_license(
        root_expression,
        reason=root_license_reason,
        evidence_kind="PYPROJECT",
        evidence_sha256=pyproject_hash,
    )
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
                **{key: value for key, value in root_license.items() if key != "properties"},
                "properties": [
                    {"name": "orgrebase.uv-lock.sha256", "value": lock_hash},
                    {"name": "orgrebase.pyproject.sha256", "value": pyproject_hash},
                    *artifact_properties,
                    *root_license["properties"],
                    *_graph_properties(roots[0], graph[root_ref]),
                    {
                        "name": "orgrebase.license-metadata.inputs",
                        "value": json.dumps(license_facts, sort_keys=True, separators=(",", ":")),
                    },
                ],
            }
        },
        "components": components,
        "dependencies": [{"ref": root_ref, "dependsOn": sorted({edge["ref"] for edge in graph[root_ref]})}, *dependencies],
        "properties": [
            {"name": "orgrebase.evidence.class", "value": "OBSERVED_CONTROLLED_LOCAL"},
            {"name": "orgrebase.vulnerability-absence-claimed", "value": "false"},
            {"name": "orgrebase.dependency-graph.profile", "value": GRAPH_PROFILE},
            {"name": "orgrebase.dependency-graph.installed-runtime-claimed", "value": "false"},
        ],
    }


def _encoded(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument(
        "--license-metadata",
        type=Path,
        action="append",
        default=[],
        help="Explicit dist-info METADATA for an exact locked Name/Version (repeatable).",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path in (args.lock, args.pyproject, *args.artifact, *args.license_metadata):
        if not path.is_file():
            raise SystemExit(f"SBOM_INPUT_MISSING:{path}")
    try:
        encoded = _encoded(
            build_sbom(
                lock_path=args.lock,
                pyproject_path=args.pyproject,
                artifacts=tuple(args.artifact),
                license_metadata_paths=tuple(args.license_metadata),
            )
        )
    except (LicenseEvidenceError, LockEvidenceError) as exc:
        raise SystemExit(str(exc)) from exc
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
