from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.generate_sbom import build_sbom

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
