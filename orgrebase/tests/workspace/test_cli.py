from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from orgrebase import cli
from orgrebase.service import OrgRebaseService


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["orgrebase", *args])
    cli.main()


def test_python_module_entrypoint_delegates_to_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "main", lambda: calls.append("called"))

    runpy.run_module("orgrebase.__main__", run_name="orgrebase.entrypoint_probe")
    assert calls == []

    runpy.run_module("orgrebase.__main__", run_name="__main__")
    assert calls == ["called"]


def test_cli_legacy_demo_summary_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = {
        "preview": {
            "counts": {"affected_hard": 2, "bounded_unaffected": 2, "unknown": 1}
        },
        "receipt": {
            "metrics": {
                "work_items_rebased": 2,
                "unauthorized_disclosures": 0,
                "false_invalidations": 0,
            }
        },
    }
    monkeypatch.setattr(cli, "run_demo", lambda _output: payload)
    _run_main(monkeypatch, "demo", "--output", str(tmp_path / "demo.json"))


def test_cli_verifiers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = OrgRebaseService()
    try:
        preview_bundle = service.preview()
        apply_bundle = service.apply()
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(
            json.dumps(apply_bundle["receipt"].model_dump(mode="json")), encoding="utf-8"
        )
        impact_path = tmp_path / "impact.json"
        impact_path.write_text(
            json.dumps(preview_bundle["preview"].certificates[0].model_dump(mode="json")),
            encoding="utf-8",
        )
        minimal_path = tmp_path / "minimal.json"
        minimal_path.write_text(
            json.dumps(
                preview_bundle["minimal_rebase_certificate"].model_dump(mode="json")
            ),
            encoding="utf-8",
        )
    finally:
        service.store.close()

    _run_main(monkeypatch, "verify", str(receipt_path))
    _run_main(monkeypatch, "verify-impact-certificate", str(impact_path))
    _run_main(monkeypatch, "verify-minimal-certificate", str(minimal_path))


def test_cli_workspace_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop_output = tmp_path / "loop.json"
    _run_main(
        monkeypatch,
        "workspace-loop",
        "--allow-scripted-approval",
        "--output",
        str(loop_output),
        "--store",
        str(tmp_path / "workspace.sqlite"),
    )
    assert loop_output.is_file()
    loop = json.loads(loop_output.read_text(encoding="utf-8"))
    assert loop["approval_mode"] == "EXPLICIT_OWNER_COMMAND"
    assert loop["approval_input_mode"] == "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
    assert loop["boundaries"]["agentteams"] == "NOT_RUN"
    assert loop["boundaries"]["oac_runtime_bridge"] == "NOT_USED_IN_THIS_RUN"

    evaluation_output = tmp_path / "evaluation.json"
    _run_main(monkeypatch, "workspace-evaluate", "--output", str(evaluation_output))
    assert json.loads(evaluation_output.read_text(encoding="utf-8"))["primary_score"] == 100.0

    skill_output = tmp_path / "skill.json"
    _run_main(monkeypatch, "workspace-skill", "--output", str(skill_output))
    assert json.loads(skill_output.read_text(encoding="utf-8"))["status"] == "CANARY"

    status_output = tmp_path / "agentteams.json"
    _run_main(monkeypatch, "workspace-agentteams", "--output", str(status_output))
    assert json.loads(status_output.read_text(encoding="utf-8"))["status"] in {
        "NOT_RUN",
        "LIVE_AGENTTEAMS",
    }

    user_output = tmp_path / "user-validation.json"
    _run_main(monkeypatch, "workspace-user-validation", "--output", str(user_output))
    assert json.loads(user_output.read_text(encoding="utf-8"))["status"] == "NOT_RUN"


def test_cli_workspace_loop_refuses_existing_store_without_touching_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "existing.sqlite"
    sentinel = b"KEEP-EXISTING-ENTERPRISE-STATE\n"
    store.write_bytes(sentinel)
    output = tmp_path / "loop.json"

    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            "workspace-loop",
            "--allow-scripted-approval",
            "--output",
            str(output),
            "--store",
            str(store),
        )

    assert exc.value.code == 2
    assert store.read_bytes() == sentinel
    assert not output.exists()


def test_cli_workspace_loop_rejects_output_store_path_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared_path = tmp_path / "shared.sqlite"

    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            "workspace-loop",
            "--allow-scripted-approval",
            "--output",
            str(shared_path),
            "--store",
            str(shared_path),
        )

    assert exc.value.code == 2
    assert not shared_path.exists()


def test_cli_workspace_loop_requires_scripted_approval_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "loop.json"
    store = tmp_path / "workspace.sqlite"

    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            "workspace-loop",
            "--output",
            str(output),
            "--store",
            str(store),
        )

    assert exc.value.code == 2
    assert not output.exists()
    assert not store.exists()


def test_cli_workspace_demo_and_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    _run_main(monkeypatch, "workspace-demo", "--output-dir", str(evidence_dir))
    assert (evidence_dir / "workspace-demo.json").is_file()
    summary = json.loads(
        (evidence_dir / "workspace-demo.json").read_text(encoding="utf-8")
    )
    assert summary["approval_mode"] == "EXPLICIT_OWNER_COMMAND"
    assert summary["approval_input_mode"] == "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
    assert summary["boundaries"]["agentteams"] == "NOT_RUN"
    assert summary["boundaries"]["oac_runtime_bridge"] == "NOT_USED_IN_THIS_RUN"

    _run_main(monkeypatch, "workspace-evidence", "--output-dir", str(evidence_dir), "--verify")


def test_cli_oac_runtime_admission_demo_and_offline_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_dir = tmp_path / "oac-bridge"
    _run_main(
        monkeypatch,
        "workspace-oac-admission-demo",
        "--output-dir",
        str(evidence_dir),
    )
    summary = json.loads((evidence_dir / "bridge-demo.json").read_text(encoding="utf-8"))
    assert summary["status"] == "LOCAL_RUNTIME_ADMISSION_PASS"
    assert [item["step_count"] for item in summary["cases"]] == [3, 4]
    assert summary["boundaries"]["target_writes"] == 0
    assert summary["boundaries"]["handler_execution"] == "NOT_RUN"
    _run_main(
        monkeypatch,
        "workspace-oac-admission-demo",
        "--output-dir",
        str(evidence_dir),
        "--verify",
    )


def test_cli_require_live_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from orgrebase.workspace.transport import agentteams_status

    if agentteams_status().get("evidence_class") == "LIVE_AGENTTEAMS":
        pytest.skip("live AgentTeams environment is available")
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            "workspace-agentteams-status",
            "--output",
            str(tmp_path / "status.json"),
            "--require-live",
        )
    assert exc.value.code == 2
