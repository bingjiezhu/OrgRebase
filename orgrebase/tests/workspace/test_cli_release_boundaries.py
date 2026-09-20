from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from orgrebase import cli, goai_agentteams

ROOT = Path(__file__).resolve().parents[2]
PILOT_PACK = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"
GOLDEN_ROOT = ROOT / "evidence" / "golden-competition" / "latest" / "pilot"
OAC_ROOT = ROOT.parent / "oac-spec"


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["orgrebase", *args])
    cli.main()


def test_legacy_demo_exports_one_self_verifying_evidence_directory(tmp_path: Path) -> None:
    output = tmp_path / "evidence" / "demo.json"

    payload = cli.run_demo(output)

    assert payload["receipt"]["status"] == "COMPLETED"
    expected = {
        "benchmark.json",
        "conflict-receipt.json",
        "demo.json",
        "failure-receipt.json",
        "git-tool-evidence.json",
        "impact-certificates.json",
        "logs.otlp.json",
        "manifest.json",
        "metrics.otlp.json",
        "minimal-rebase-certificate.json",
        "observability.json",
        "preview.json",
        "proof-pack.json",
        "rebase-receipt.json",
        "rollback-evidence.json",
        "rollback-receipt.json",
        "traces.otlp.json",
    }
    assert expected <= {path.name for path in output.parent.iterdir()}
    manifest = json.loads((output.parent / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["artifacts"]) == expected - {"manifest.json"}
    assert manifest["capabilities"]["agentteams_multi_worker_e2e"] == {
        "status": "NOT_RUN",
        "evidence_class": "NOT_RUN",
        "artifact": None,
    }
    proof = json.loads((output.parent / "proof-pack.json").read_text(encoding="utf-8"))
    assert proof["claim_boundary"].startswith("This pack proves closed-world")


def test_agentteams_demo_cli_reports_framework_and_orgrebase_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = {
        "agent_collaboration": {
            "agent_count": 5,
            "structured_handoffs": 4,
            "coordination_status": "PASS",
        },
        "tool_call": {
            "tool_refs": ["tool:dependency-evidence@v1"],
            "statuses": ["SUCCEEDED"],
            "receipt_digests": ["sha256:" + "1" * 64],
        },
        "output": {
            "rebase_status": "COMPLETED",
            "work_items_rebased": 2,
            "skill_candidate_state": "CANARY",
        },
        "exception_and_recovery": {
            "authority_conflict": "REJECTED",
            "expired_preview": {"status": "REJECTED", "error_code": "PREVIEW_EXPIRED"},
            "rollback": {"status": "COMPLETED"},
        },
        "run_classification": {
            "semantic_acceptance": "PASS",
            "autonomous_collaboration": False,
            "current_workspace_live": "NOT_RUN",
        },
        "historical_agentteams_transport": {
            "evidence_class": "HISTORICAL_LIVE_AGENTTEAMS",
            "worker_count": 5,
            "provider_call_count": 5,
        },
        "fresh_core_agentteams_evidence": {
            "evidence_class": "CONTROLLED_LOCAL",
            "successful_provider_executions": 5,
            "semantic_ingestion": {"advisory_accepted": 4, "evaluated": 5, "target_writes": 0},
            "current_workspace_live": "NOT_RUN",
            "oac_runtime_bridge": "NOT_RUN",
        },
    }
    monkeypatch.setattr(goai_agentteams, "load_run_contract", lambda *_args: ({}, {}))
    monkeypatch.setattr(goai_agentteams, "build_run_summary", lambda **_kwargs: summary)
    monkeypatch.setattr(
        goai_agentteams,
        "verify_run_directory",
        lambda _output: {"status": "PASS"},
    )
    monkeypatch.setattr(cli, "run_demo", lambda _output: {"status": "stubbed"})
    output_dir = tmp_path / "agentteams"

    _run_main(
        monkeypatch,
        "agentteams-demo",
        "--output-dir",
        str(output_dir),
    )

    assert json.loads((output_dir / "run-summary.json").read_text(encoding="utf-8")) == summary
    stdout = capsys.readouterr().out
    assert "Deterministic reference: 5 fixture Agent runs" in stdout
    assert "Open boundary: Workspace live NOT_RUN" in stdout


@pytest.mark.parametrize("status, expected_exit", (("PASS", None), ("FAIL", 2)))
def test_agentteams_verify_cli_preserves_fail_closed_exit_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    expected_exit: int | None,
) -> None:
    monkeypatch.setattr(
        goai_agentteams,
        "verify_run_directory",
        lambda _output: {"status": status},
    )
    if expected_exit is None:
        _run_main(monkeypatch, "agentteams-verify", "--output-dir", str(tmp_path))
        return
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, "agentteams-verify", "--output-dir", str(tmp_path))
    assert exc.value.code == expected_exit


def test_oac_adaptation_prepare_uses_same_workspace_run_and_closes_store(
    tmp_path: Path,
) -> None:
    result = cli._enterprise_oac_adapt(
        pack=PILOT_PACK,
        store=tmp_path / "adaptation.sqlite",
        oac_root=OAC_ROOT,
        golden_root=GOLDEN_ROOT,
        review_seconds=4.0,
        prepare_command_id="test-oac-adaptation-prepare@r1",
        approve_as=None,
        approval_command_id="unused",
    )

    assert result["status"] == "OWNER_REVIEW_PENDING"
    assert result["effect_ceiling"] == "ZERO_EXTERNAL_EFFECTS"
    assert result["canonical_target_writes"] == 0


def test_oac_adaptation_cli_rejects_a_bypassed_review_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            "enterprise-oac-adapt",
            "--pack",
            str(PILOT_PACK),
            "--store",
            str(tmp_path / "adaptation.sqlite"),
            "--oac-root",
            str(OAC_ROOT),
            "--golden-root",
            str(GOLDEN_ROOT),
            "--review-seconds",
            "0",
        )

    assert exc.value.code == 2
    assert "OAC_ADAPTATION_REVIEW_DURATION_UNDER_FOUR_SECONDS" in capsys.readouterr().err
