from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orgrebase.workspace.readiness import audit_release_tree, audit_repository

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("relative", ["src/.DS_Store", "demo/console/__MACOSX/resource"])
def test_release_hygiene_rejects_metadata_at_any_depth(tmp_path, relative):
    assert audit_release_tree(tmp_path)["status"] == "PASS"
    junk = tmp_path / relative
    junk.parent.mkdir(parents=True)
    junk.write_bytes(b"metadata")
    report = audit_release_tree(tmp_path)
    assert report["status"] == "FAIL"
    assert relative in report["detail"]


def test_hygiene_only_cli_does_not_write_evidence(tmp_path):
    script = ROOT / "scripts/verify_review_readiness.py"
    command = [sys.executable, str(script), "--root", str(tmp_path), "--hygiene-only"]
    clean = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["status"] == "PASS"
    assert list(tmp_path.iterdir()) == []

    (tmp_path / ".DS_Store").write_bytes(b"metadata")
    dirty = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert dirty.returncode != 0
    assert json.loads(dirty.stdout)["status"] == "FAIL"
    assert [path.name for path in tmp_path.iterdir()] == [".DS_Store"]


@pytest.mark.parametrize("target", ["check", "test"])
def test_hygiene_failure_prevents_full_pytest(tmp_path, target):
    launcher = tmp_path / "uv"
    log = tmp_path / "commands.jsonl"
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['HYGIENE_COMMAND_LOG'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(7 if '--hygiene-only' in sys.argv else 0)\n"
    )
    launcher.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(ROOT / "Makefile"),
         "PYTHON=uv run python", target],
        cwd=tmp_path, capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
             "HYGIENE_COMMAND_LOG": str(log)},
    )
    assert result.returncode != 0
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        ["run", "python", "scripts/verify_review_readiness.py", "--hygiene-only"]
    ]


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

    ignored = shutil.ignore_patterns(
        ".git", ".venv", ".venv.*", ".runtime", ".tmp", "__pycache__",
        ".pytest_cache", ".ruff_cache", ".env", ".env.*",
    )
    shutil.copytree(ROOT, target_root, ignore=lambda path, names: ignored(path, names) - {".env.example"})
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
