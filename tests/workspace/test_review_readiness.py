from __future__ import annotations

import json
from pathlib import Path

from orgrebase.workspace.readiness import audit_repository


ROOT = Path(__file__).resolve().parents[2]


def test_review_readiness_passes_for_release_tree() -> None:
    report = audit_repository(ROOT)
    assert report["status"] == "PASS", [
        item for item in report["checks"] if item["status"] != "PASS"
    ]
    assert report["failure_count"] == 0
    assert set(report["criteria"]) == {
        "real_user_and_application_scenario",
        "agent_complete_task_closure",
        "core_function_verifiable_materials",
        "model_agent_architecture_tool_interfaces",
        "data_authorization_and_privacy_risk",
    }
    assert report["external_boundaries"] == {
        "workspace_agentteams_live": "NOT_RUN",
        "real_user_validation": "NOT_RUN",
        "real_enterprise_connectors": "NOT_RUN",
    }


def test_review_readiness_fails_closed_on_missing_evidence_binding(tmp_path: Path) -> None:
    source = json.loads(
        (ROOT / "configs/workspace/review-readiness.json").read_text(encoding="utf-8")
    )
    source["criteria"][0]["evidence_paths"].append("evidence/workspace/latest/missing.json")
    manifest = tmp_path / "review-readiness.json"
    manifest.write_text(json.dumps(source), encoding="utf-8")

    report = audit_repository(ROOT, manifest_path=manifest)
    assert report["status"] == "FAIL"
    assert any(
        item["id"] == "criterion.real_user_and_application_scenario.evidence_paths"
        and item["status"] == "FAIL"
        for item in report["checks"]
    )


def test_review_readiness_report_is_content_addressed() -> None:
    first = audit_repository(ROOT)
    second = audit_repository(ROOT)
    assert first == second
    assert first["digest"].startswith("sha256:")


def test_review_readiness_fails_closed_on_unverified_optional_license(tmp_path: Path) -> None:
    target_root = tmp_path / "repo"
    import shutil

    shutil.copytree(ROOT, target_root)
    license_path = target_root / "benchmark/orgworkbench/license-manifest.json"
    payload = json.loads(license_path.read_text(encoding="utf-8"))
    optional = next(
        item for item in payload["assets"] if item["usage"] != "CANONICAL_BENCHMARK"
    )
    optional.pop("license_source_url", None)
    license_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit_repository(target_root)
    assert report["status"] == "FAIL"
    assert any(
        item["id"] == "data.license_manifest" and item["status"] == "FAIL"
        for item in report["checks"]
    )
