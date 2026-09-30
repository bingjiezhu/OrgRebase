from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests import postgres_support

ROOT = Path(__file__).resolve().parents[1]


def test_core_target_keeps_bounded_invariants_and_isolated_runtime_probe() -> None:
    result = subprocess.run(
        ["make", "-n", "check-core", f"PYTHON={sys.executable}"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    )
    commands = result.stdout.splitlines()
    assert any("scripts/verify_packaged_runtime_assets.py" in command for command in commands)
    test_command = next(command for command in commands if "-m pytest" in command)
    selectors = [token for token in shlex.split(test_command) if token.startswith("tests/")]
    assert selectors, "Core must select concrete test modules"
    for selector in selectors:
        path = selector.split("::", 1)[0]
        assert (ROOT / path).is_file(), f"Core test selector has no source file: {selector}"
    for module in (
        "test_impact.py",
        "test_runtime_version.py",
        "test_state_snapshot.py",
        "test_workflow_and_store.py",
        "test_commit_gateway.py",
        "test_skills.py",
        "test_change_operations.py",
        "test_change_budget.py",
        "test_change_notifications.py",
        "test_onboarding_status.py",
        "test_onboarding_draft_recovery.py",
        "test_bounded_execution.py",
        "test_vertex_candidate_contract.py",
        "test_runtime_revision.py",
        "test_deliverable_set.py",
        "test_quote_pattern_bridge.py",
        "test_quote_recovery_governed_learning.py",
        "test_quote_recovery_adoption_matrix.py",
        "test_quote_recovery_operations.py",
    ):
        assert module in test_command
    assert "--cov" not in test_command  # This subset must not claim whole-product coverage.
    assert "OAC_ROOT" not in result.stdout and "oac-spec" not in result.stdout
    full = subprocess.run(
        ["make", "-n", "check", f"PYTHON={sys.executable}"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    )
    assert "-m pytest -W error --cov=orgrebase" in full.stdout


def test_enterprise_boundary_target_requires_postgres_and_keeps_selected_scope() -> None:
    default = subprocess.run(
        [
            "make",
            "-n",
            "check-enterprise-boundaries",
            f"PYTHON={sys.executable}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "test_quote_recovery_operations_postgres.py" in default.stdout
    result = subprocess.run(
        [
            "make",
            "-n",
            "check-enterprise-boundaries",
            f"PYTHON={sys.executable}",
            "ENTERPRISE_BOUNDARY_TESTS=tests/test_source_bindings.py tests/test_effect_operations.py",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ORGREBASE_REQUIRE_POSTGRES_TESTS=1" in result.stdout
    assert "tests/test_source_bindings.py tests/test_effect_operations.py" in result.stdout
    assert "--cov" not in result.stdout


def test_core_target_stops_at_failed_hygiene_instead_of_reporting_a_pass(tmp_path: Path) -> None:
    marker = tmp_path / "commands.txt"
    probe = tmp_path / "failing_python.py"
    probe.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a') as stream:\n"
        "    stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(17)\n"
    )
    result = subprocess.run(
        ["make", "check-core", f"PYTHON={sys.executable} {probe}"], cwd=ROOT,
        check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert marker.read_text().splitlines() == ["scripts/verify_review_readiness.py --hygiene-only"]


def test_full_check_requires_postgres_even_if_callers_disable_it(tmp_path: Path) -> None:
    marker = tmp_path / "commands.jsonl"
    probe = tmp_path / "record_python.py"
    probe.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps({'args': sys.argv[1:], 'postgres': os.environ.get('ORGREBASE_REQUIRE_POSTGRES_TESTS')}) + '\\n')\n"
    )
    subprocess.run(
        ["make", "check", f"PYTHON={sys.executable} {probe}"], cwd=ROOT,
        env={**os.environ, "ORGREBASE_REQUIRE_POSTGRES_TESTS": "0"},
        check=True, capture_output=True, text=True,
    )
    commands = [json.loads(line) for line in marker.read_text().splitlines()]
    pytest_command = next(item for item in commands if item["args"][:2] == ["-m", "pytest"])
    assert pytest_command["postgres"] == "1"


def test_required_postgres_tools_fail_instead_of_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGREBASE_REQUIRE_POSTGRES_TESTS", "1")
    monkeypatch.setattr(postgres_support.shutil, "which", lambda _: None)
    with pytest.raises(pytest.fail.Exception, match="PostgreSQL initdb and pg_ctl are required"):
        next(postgres_support.postgres_cluster.__wrapped__())
