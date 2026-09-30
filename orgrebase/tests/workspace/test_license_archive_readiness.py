"""License compatibility checks requiring the full retained workspace archive."""

from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.workspace import readiness

ROOT = Path(__file__).resolve().parents[2]
LEGACY_LICENSE = "PolyForm-Noncommercial-1.0.0"


@pytest.mark.parametrize("license_id, expected", [
    (LEGACY_LICENSE, "PASS"), ("Apache-2.0", "PASS"), ("Unreviewed-License", "FAIL"),
])
def test_readiness_accepts_current_and_sealed_license_metadata(monkeypatch, license_id, expected):
    original_load = readiness._load_json

    def load(path):
        value = original_load(path)
        if path == ROOT / "benchmark/orgworkbench/dataset-manifest.json":
            value["license"] = license_id
        elif path == ROOT / "benchmark/orgworkbench/license-manifest.json":
            next(item for item in value["assets"]
                 if item["usage"] == "CANONICAL_BENCHMARK")["license_spdx"] = license_id
        return value

    monkeypatch.setattr(readiness, "_load_json", load)
    checks = []
    readiness._evidence_checks(ROOT, checks)
    result = {item["id"]: item["status"] for item in checks}
    assert result["data.synthetic_benchmark"] == expected
    assert result["data.license_manifest"] == expected
