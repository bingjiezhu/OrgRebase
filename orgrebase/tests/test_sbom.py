from __future__ import annotations

import builtins
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_sbom
from scripts.generate_sbom import LicenseEvidenceError, LockEvidenceError, build_sbom

ROOT = Path(__file__).resolve().parents[1]


def test_cyclonedx_sbom_is_deterministic_and_artifact_bound(tmp_path: Path) -> None:
    artifact = tmp_path / "orgrebase-0.3.0-test.whl"
    artifact.write_bytes(b"controlled-local-wheel")
    first = build_sbom(
        lock_path=ROOT / "uv.lock",
        pyproject_path=ROOT / "pyproject.toml",
        artifacts=(artifact,),
    )
    second = build_sbom(
        lock_path=ROOT / "uv.lock",
        pyproject_path=ROOT / "pyproject.toml",
        artifacts=(artifact,),
    )
    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.5"
    assert first["components"]
    assert first["properties"] == [
        {"name": "orgrebase.evidence.class", "value": "OBSERVED_CONTROLLED_LOCAL"},
        {"name": "orgrebase.vulnerability-absence-claimed", "value": "false"},
        {"name": "orgrebase.dependency-graph.profile", "value": "FULL_LOCK_POTENTIAL_V1"},
        {"name": "orgrebase.dependency-graph.installed-runtime-claimed", "value": "false"},
    ]
    properties = {item["name"]: item["value"] for item in first["metadata"]["component"]["properties"]}
    assert properties[f"orgrebase.artifact.{artifact.name}.sha256"] == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()


def test_sbom_cli_write_then_check(tmp_path: Path) -> None:
    artifact = tmp_path / "source.tar.gz"
    artifact.write_bytes(b"source")
    output = tmp_path / "sbom.cdx.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "generate_sbom.py"),
        "--lock",
        str(ROOT / "uv.lock"),
        "--pyproject",
        str(ROOT / "pyproject.toml"),
        "--artifact",
        str(artifact),
        "--output",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([*command, "--check"], check=True, capture_output=True, text=True)
    assert json.loads(output.read_text(encoding="utf-8"))["serialNumber"].startswith("urn:uuid:")


def _license_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "orgrebase"\nversion = "1.0.0"\n'
        'dependencies = [{name = "dependency-name"}]\n'
        '[[package]]\nname = "dependency-name"\nversion = "2.0.0"\n',
        encoding="utf-8",
    )
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "orgrebase"\nversion = "1.0.0"\nlicense = "Apache-2.0"\n',
        encoding="utf-8",
    )
    artifact = tmp_path / "wheel.whl"
    artifact.write_bytes(b"controlled local artifact")
    return lock, project, artifact


def _metadata(tmp_path: Path, *, version: str = "2.0.0", license_headers: str = "") -> Path:
    path = tmp_path / "METADATA"
    path.write_text(
        f"Metadata-Version: 2.4\nName: dependency_name\nVersion: {version}\n{license_headers}\n",
        encoding="utf-8",
    )
    return path


def _build_with_licenses(tmp_path: Path, *metadata_paths: Path) -> dict:
    lock, project, artifact = _license_inputs(tmp_path)
    return build_sbom(
        lock_path=lock,
        pyproject_path=project,
        artifacts=(artifact,),
        license_metadata_paths=metadata_paths,
    )


def _properties(component: dict) -> dict[str, str]:
    return {item["name"]: item["value"] for item in component["properties"]}


def test_spdx_expression_uses_explicit_exact_version_metadata(tmp_path: Path) -> None:
    path = _metadata(tmp_path, license_headers="License-Expression: mit OR Apache-2.0\n")
    sbom = _build_with_licenses(tmp_path, path)
    root = sbom["metadata"]["component"]
    dependency = sbom["components"][0]
    assert root["licenses"] == [{"expression": "Apache-2.0"}]
    assert dependency["licenses"] == [{"expression": "MIT OR Apache-2.0"}]
    assert _properties(dependency)["orgrebase.license-status"] == "DECLARED_SPDX"
    assert _properties(dependency)["orgrebase.license-evidence.sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    assert str(tmp_path) not in json.dumps(sbom)


@pytest.mark.parametrize(
    ("headers", "reason"),
    (
        ("", "LICENSE_EXPRESSION_MISSING"),
        ("License: MIT License\n", "LICENSE_EXPRESSION_MISSING"),
        ("Classifier: License :: OSI Approved :: MIT License\n", "LICENSE_EXPRESSION_MISSING"),
        ("License-Expression: Invented-License\n", "LICENSE_EXPRESSION_INVALID"),
        ("License-Expression: MIT AND\n", "LICENSE_EXPRESSION_INVALID"),
        (
            "License-Expression: MIT\nLicense-Expression: Apache-2.0\n",
            "LICENSE_EXPRESSION_AMBIGUOUS",
        ),
    ),
)
def test_missing_invalid_or_ambiguous_license_is_explicit_unknown(
    tmp_path: Path, headers: str, reason: str
) -> None:
    path = _metadata(tmp_path, license_headers=headers)
    dependency = _build_with_licenses(tmp_path, path)["components"][0]
    assert "licenses" not in dependency
    assert _properties(dependency)["orgrebase.license-status"] == "UNKNOWN"
    assert _properties(dependency)["orgrebase.license-status-reason"] == reason
    assert "orgrebase.license-evidence.sha256" in _properties(dependency)


def test_metadata_from_other_version_cannot_license_locked_version(tmp_path: Path) -> None:
    path = _metadata(tmp_path, version="2.0.1", license_headers="License-Expression: MIT\n")
    sbom = _build_with_licenses(tmp_path, path)
    dependency = sbom["components"][0]
    assert "licenses" not in dependency
    assert _properties(dependency)["orgrebase.license-status"] == "UNKNOWN"
    assert _properties(dependency)["orgrebase.license-status-reason"] == "EXACT_VERSION_METADATA_NOT_SUPPLIED"
    retained_inputs = json.loads(_properties(sbom["metadata"]["component"])["orgrebase.license-metadata.inputs"])
    assert retained_inputs[0]["version"] == "2.0.1"


def test_license_evidence_bytes_are_part_of_deterministic_identity(tmp_path: Path) -> None:
    path = _metadata(tmp_path, license_headers="License-Expression: MIT\n")
    first = _build_with_licenses(tmp_path, path)
    assert first == _build_with_licenses(tmp_path, path, path)
    path.write_bytes(path.read_bytes() + b"Retained metadata description\n")
    second = _build_with_licenses(tmp_path, path)
    assert first["serialNumber"] != second["serialNumber"]
    assert first["components"][0]["licenses"] == second["components"][0]["licenses"]


def test_conflicting_metadata_for_same_name_and_version_is_rejected(tmp_path: Path) -> None:
    path = _metadata(tmp_path, license_headers="License-Expression: MIT\n")
    conflicting = tmp_path / "second-metadata"
    conflicting.write_bytes(path.read_bytes().replace(b"MIT", b"Apache-2.0"))
    with pytest.raises(LicenseEvidenceError, match=r"SBOM_LICENSE_METADATA_CONFLICT:dependency-name@2\.0\.0"):
        _build_with_licenses(tmp_path, path, conflicting)


@pytest.mark.parametrize("headers", ("Version: 2.0.0\n", "Name: dependency-name\n", "Name: x\nName: y\nVersion: 2.0.0\n"))
def test_metadata_without_unambiguous_identity_is_rejected(tmp_path: Path, headers: str) -> None:
    path = tmp_path / "METADATA"
    path.write_text(headers + "License-Expression: MIT\n\n", encoding="utf-8")
    with pytest.raises(LicenseEvidenceError, match="SBOM_METADATA_IDENTITY_INVALID"):
        _build_with_licenses(tmp_path, path)


def test_sbom_cli_repeatable_metadata_and_drift_check(tmp_path: Path) -> None:
    lock, project, artifact = _license_inputs(tmp_path)
    path = _metadata(tmp_path, license_headers="License-Expression: MIT\n")
    output = tmp_path / "sbom.cdx.json"
    command = [
        sys.executable, str(ROOT / "scripts" / "generate_sbom.py"),
        "--lock", str(lock), "--pyproject", str(project),
        "--artifact", str(artifact), "--output", str(output),
        "--license-metadata", str(path), "--license-metadata", str(path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([*command, "--check"], check=True, capture_output=True, text=True)
    assert json.loads(output.read_bytes())["components"][0]["licenses"] == [{"expression": "MIT"}]
    path.write_bytes(path.read_bytes().replace(b"MIT", b"Apache-2.0"))
    checked = subprocess.run([*command, "--check"], check=False, capture_output=True, text=True)
    assert checked.returncode != 0
    assert "SBOM_DRIFT" in checked.stderr


def test_absent_optional_spdx_validator_is_unknown_and_changes_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _metadata(tmp_path, license_headers="License-Expression: MIT\n")
    validated = _build_with_licenses(tmp_path, path)
    original_import = builtins.__import__

    def without_spdx_validator(name, *args, **kwargs):
        if name == "packaging.licenses":
            raise ImportError("optional SPDX validator not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_spdx_validator)
    without_validator = _build_with_licenses(tmp_path, path)
    assert without_validator["serialNumber"] != validated["serialNumber"]
    for component in (without_validator["metadata"]["component"], *without_validator["components"]):
        assert "licenses" not in component
        assert _properties(component)["orgrebase.license-status"] == "UNKNOWN"
        assert _properties(component)["orgrebase.license-status-reason"] == "SPDX_VALIDATOR_UNAVAILABLE"


def _graph_inputs(tmp_path: Path, dependency: str) -> tuple[Path, Path, Path]:
    lock, project, artifact = _license_inputs(tmp_path)
    lock.write_text(
        '[[package]]\nname = "orgrebase"\nversion = "1.0.0"\n'
        f'dependencies = [{dependency}]\n'
        '[package.optional-dependencies]\ndev = [{name = "tool"}]\n'
        '[[package]]\nname = "dependency-name"\nversion = "1.0.0"\n'
        'source = {registry = "https://first.example/simple"}\n'
        '[[package]]\nname = "dependency-name"\nversion = "2.0.0"\n'
        'source = {registry = "https://second.example/simple"}\n'
        '[package.optional-dependencies]\n'
        'feature = [{name = "leaf", marker = "sys_platform == \'linux\'"}]\n'
        '[package.dev-dependencies]\ncheck = [{name = "tool"}]\n'
        '[[package]]\nname = "leaf"\nversion = "3.0.0"\n'
        'resolution-markers = ["sys_platform == \'linux\'"]\n'
        '[[package]]\nname = "tool"\nversion = "4.0.0"\n',
        encoding="utf-8",
    )
    return lock, project, artifact


@pytest.mark.parametrize("selector", (
    'version = "2.0.0"',
    'source = {registry = "https://second.example/simple"}',
))
def test_full_lock_graph_resolves_exact_targets_and_preserves_optional_edges(
    tmp_path: Path, selector: str,
) -> None:
    lock, project, artifact = _graph_inputs(
        tmp_path, f'{{name = "dependency_name", {selector}, extra = ["feature"]}}'
    )
    sbom = build_sbom(lock_path=lock, pyproject_path=project, artifacts=(artifact,))
    graph = {node["ref"]: node["dependsOn"] for node in sbom["dependencies"]}
    assert graph["pkg:pypi/orgrebase@1.0.0"] == [
        "pkg:pypi/dependency-name@2.0.0", "pkg:pypi/tool@4.0.0",
    ]
    assert graph["pkg:pypi/dependency-name@1.0.0"] == []
    assert graph["pkg:pypi/dependency-name@2.0.0"] == ["pkg:pypi/leaf@3.0.0", "pkg:pypi/tool@4.0.0"]
    root_edges = json.loads(_properties(sbom["metadata"]["component"])["orgrebase.lock-dependency-edges"])
    selected = next(edge for edge in root_edges if edge["kind"] == "dependencies")
    assert selected["lockEdge"]["extra"] == ["feature"]
    components = {component["bom-ref"]: component for component in sbom["components"]}
    edges = json.loads(_properties(components["pkg:pypi/dependency-name@2.0.0"])["orgrebase.lock-dependency-edges"])
    optional = next(edge for edge in edges if edge["kind"] == "optional-dependencies")
    assert optional["group"] == "feature"
    assert optional["lockEdge"]["marker"] == "sys_platform == 'linux'"
    assert json.loads(_properties(components["pkg:pypi/leaf@3.0.0"])["orgrebase.lock-resolution-markers"]) == [
        "sys_platform == 'linux'",
    ]


@pytest.mark.parametrize(("dependency", "error"), (
    ('{name = "dependency-name"}', "SBOM_LOCK_DEPENDENCY_AMBIGUOUS"),
    ('{name = "dependency-name", version = "9.0.0"}', "SBOM_LOCK_DEPENDENCY_MISSING"),
    ('{name = "absent"}', "SBOM_LOCK_DEPENDENCY_MISSING"),
))
def test_lock_graph_rejects_ambiguous_or_missing_targets(tmp_path: Path, dependency: str, error: str) -> None:
    lock, project, artifact = _graph_inputs(tmp_path, dependency)
    with pytest.raises(LockEvidenceError, match=error):
        build_sbom(lock_path=lock, pyproject_path=project, artifacts=(artifact,))


def test_lock_graph_rejects_duplicate_component_identity(tmp_path: Path) -> None:
    lock, project, artifact = _license_inputs(tmp_path)
    with lock.open("a") as handle:
        handle.write('[[package]]\nname = "dependency_name"\nversion = "2.0.0"\nsource = {registry = "https://second.example/simple"}\n')
    with pytest.raises(LockEvidenceError, match="SBOM_LOCK_IDENTITY_AMBIGUOUS"):
        build_sbom(lock_path=lock, pyproject_path=project, artifacts=(artifact,))


def test_actual_lock_graph_includes_binary_and_standard_extras(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"graph scope probe")
    sbom = build_sbom(lock_path=ROOT / "uv.lock", pyproject_path=ROOT / "pyproject.toml", artifacts=(artifact,))
    graph = {node["ref"].split("@")[0]: node["dependsOn"] for node in sbom["dependencies"]}
    assert any(ref.startswith("pkg:pypi/psycopg-binary@") for ref in graph["pkg:pypi/psycopg"])
    uvicorn_names = {ref.split("@")[0] for ref in graph["pkg:pypi/uvicorn"]}
    assert {"pkg:pypi/httptools", "pkg:pypi/uvloop", "pkg:pypi/watchfiles", "pkg:pypi/websockets"} <= uvicorn_names
    root_edges = json.loads(_properties(sbom["metadata"]["component"])["orgrebase.lock-dependency-edges"])
    psycopg = next(edge for edge in root_edges if edge["lockEdge"]["name"] == "psycopg")
    assert psycopg["lockEdge"]["extra"] == ["binary"]


def test_graph_policy_and_edges_are_bound_to_deterministic_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    lock, project, artifact = _graph_inputs(tmp_path, '{name = "dependency-name", version = "2.0.0"}')
    first = build_sbom(lock_path=lock, pyproject_path=project, artifacts=(artifact,))
    monkeypatch.setattr(generate_sbom, "GRAPH_PROFILE", "FULL_LOCK_POTENTIAL_NEXT")
    revised_policy = build_sbom(lock_path=lock, pyproject_path=project, artifacts=(artifact,))
    assert revised_policy["serialNumber"] != first["serialNumber"]
    monkeypatch.setattr(generate_sbom, "GRAPH_PROFILE", "FULL_LOCK_POTENTIAL_V1")
    original_graph = generate_sbom._lock_graph

    def revised_graph(packages):
        graph = original_graph(packages)
        graph["pkg:pypi/orgrebase@1.0.0"] = []
        return graph

    monkeypatch.setattr(generate_sbom, "_lock_graph", revised_graph)
    revised_edges = build_sbom(lock_path=lock, pyproject_path=project, artifacts=(artifact,))
    assert revised_edges["serialNumber"] != first["serialNumber"]


def test_license_metadata_input_order_does_not_change_graph_or_identity(tmp_path: Path) -> None:
    path = _metadata(tmp_path, license_headers="License-Expression: MIT\n")
    other = tmp_path / "OTHER-METADATA"
    other.write_bytes(path.read_bytes().replace(b"dependency_name", b"other-dependency"))
    assert _build_with_licenses(tmp_path, path, other) == _build_with_licenses(tmp_path, other, path)
