from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="Source distributions omit GitHub workflows")


def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def test_core_ci_runs_from_orgrebase_without_oac_variables_or_secrets() -> None:
    core = jobs()["core"]
    assert not core.get("if") and not core.get("needs") and not core.get("env")
    assert core["defaults"]["run"]["working-directory"] == "orgrebase"
    assert any(step.get("run") == "make check-core" for step in core["steps"])
    for step in core["steps"]:
        assert "secrets." not in str(step)
        if step.get("uses", "").startswith("actions/checkout@"):
            assert not step.get("with", {}).get("repository")
            assert not step.get("with", {}).get("path")
            assert step["with"]["persist-credentials"] is False


def test_full_check_uses_sibling_oac_tree() -> None:
    check = jobs()["check"]
    assert check["needs"] == "core" or check["needs"] == ["core"]
    assert "workflow_dispatch" in check["if"]
    assert check["defaults"]["run"]["working-directory"] == "orgrebase"
    assert check["env"]["ORGREBASE_OAC_ROOT"] == "${{ github.workspace }}/oac-spec"
    assert any("verify_oac_dependency.py" in str(step.get("run", "")) for step in check["steps"])
    for step in check["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert not step.get("with", {}).get("repository")
            assert not step.get("with", {}).get("path")
            assert step["with"]["persist-credentials"] is False


def test_skipped_or_failed_full_check_cannot_qualify_a_release_candidate() -> None:
    candidate = jobs()["release-candidate"]
    assert set(candidate["needs"]) == {"core", "check"}
    assert "needs.core.result == 'success'" in candidate["if"]
    assert "needs.check.result == 'success'" in candidate["if"]
    assert "github.event_name == 'workflow_dispatch'" in candidate["if"]
    assert "github.ref == 'refs/heads/main'" in candidate["if"]
    assert candidate["defaults"]["run"]["working-directory"] == "orgrebase"
