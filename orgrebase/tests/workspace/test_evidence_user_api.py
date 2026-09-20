from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import IntegrityError, ToolInvocationReceipt
from orgrebase.workspace.evidence import (
    WorkspaceEvidenceBuilder,
    WorkspaceEvidenceExporter,
    verify_evidence_directory,
)
from orgrebase.workspace.models import ToolCalledEvent, UserWalkthroughRecord
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.user_validation import UserValidationRepository, UserValidationService


def test_complete_evidence_pack_builds_and_verifies(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    index = WorkspaceEvidenceBuilder(root).build()
    assert index.status == "PASS"
    assert len(index.entries) == 41
    result = verify_evidence_directory(root)
    assert result["status"] == "PASS"
    assert result["approval_mode"] == "EXPLICIT_OWNER_COMMAND"
    assert result["approval_input_mode"] == "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
    assert result["workspace_agentteams_live"] == "NOT_RUN"
    assert result["oac_runtime_bridge"] == "NOT_USED_IN_THIS_RUN"
    summary = json.loads((root / "workspace-demo.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "orgrebase.workspace-demo.v2"
    assert summary["workflow_run_id"] == summary["run_id"] == index.run_id
    assert summary["approval_mode"] == "EXPLICIT_OWNER_COMMAND"
    assert summary["approval_input_mode"] == "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
    assert summary["boundaries"]["agentteams"] == "NOT_RUN"
    assert summary["boundaries"]["oac_runtime_bridge"] == "NOT_USED_IN_THIS_RUN"
    assert summary["restart"]["closed_stage"] == "CURRENT"
    assert summary["restart"]["reopened_stage"] == "CURRENT"
    assert (
        summary["restart"]["state_digest_before_close"]
        == summary["restart"]["state_digest_after_reopen"]
    )
    assert summary["approval_commands"]["launch_date"]["actor_id"] == (
        "human:product-owner"
    )
    assert summary["approval_commands"]["currency"]["actor_id"] == (
        "human:finance-owner"
    )
    for kind, label in (("launch_date", "launch"), ("currency", "currency")):
        command = summary["approval_commands"][kind]
        assert command["input_mode"] == "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
        preview = json.loads(
            (root / f"rebase/{label}-preview.json").read_text(encoding="utf-8")
        )
        approval = json.loads(
            (root / f"rebase/{label}-approval.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (root / f"rebase/{label}-rebase-receipt.json").read_text(encoding="utf-8")
        )
        assert command["preview_digest"] == approval["preview_digest"] == preview["digest"]
        assert command["approval_digest"] == approval["digest"]
        assert receipt["approval_digest"] == approval["digest"]
        assert receipt["approval_actor_id"] == approval["actor_id"]
    assert set(summary["stage_run_ids"]) == {
        "formation",
        "dependency_evidence_tool",
        "launch_rebase",
        "currency_rebase",
    }
    assert summary["tool_evidence"]["invocation_receipt_ref"].startswith("tool-call:")
    assert summary["final_quote"]["version"] == "v3"
    assert summary["primary_evaluation_score"] == 100.0
    assert summary["skill_status"] == "CANARY"
    assert summary["agentteams"]["status"] == "NOT_RUN"


def test_evidence_verifier_rejects_tool_binding_tamper(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    WorkspaceEvidenceBuilder(root).build()
    target = root / "tool/dependency-evidence-called-event.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["result_digest"] = "sha256:" + "0" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IntegrityError, match="EVIDENCE_INDEX_VERIFICATION_FAILED"):
        verify_evidence_directory(root)


def test_evidence_verifier_rejects_file_tamper(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    WorkspaceEvidenceBuilder(root).build()
    target = root / "repeatability" / "final-quote.json"
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="EVIDENCE_INDEX_VERIFICATION_FAILED"):
        verify_evidence_directory(root)


def test_evidence_exporter_blocks_privacy_canary(tmp_path: Path) -> None:
    exporter = WorkspaceEvidenceExporter(tmp_path)
    with pytest.raises(IntegrityError, match="EVIDENCE_PRIVACY_CANARY_LEAK"):
        exporter.write_json(
            relative_path="bad.json",
            value={"value": "ORGREBASE_CANARY_SECRET_case"},
            evidence_class="LOCAL_DETERMINISTIC",
            claim_supported="nothing",
            verifier_command="false",
        )


def _walkthrough(index: int, *, finding: str = "clear dependency trace") -> UserWalkthroughRecord:
    return UserWalkthroughRecord(
        id=f"walkthrough:{index}",
        participant_role="enterprise-platform-lead",
        scenario_version="v1",
        consent_recorded=True,
        problem_understood=True,
        usefulness_rating=5,
        trace_value_rating=5,
        pilot_intent="CONDITIONAL_PILOT",
        redacted_findings=(finding,),
        critical_gaps=(),
        recorded_at="2026-08-16T00:00:00Z",
    )


def test_user_validation_is_not_run_without_real_records() -> None:
    summary = UserValidationService.summarize(())
    assert summary.status == "NOT_RUN"
    assert summary.evidence_class == "NOT_RUN"
    assert summary.digest.startswith("sha256:")


def test_five_consented_redacted_walkthroughs_meet_threshold() -> None:
    summary = UserValidationService.summarize(tuple(_walkthrough(i) for i in range(5)))
    assert summary.status == "PASS"
    assert summary.participant_count == 5
    assert summary.pilot_or_conditional_count == 5


def test_user_validation_repository_rejects_pii(tmp_path: Path) -> None:
    repository = UserValidationRepository(tmp_path / "walkthroughs.jsonl")
    with pytest.raises(ValueError, match="USER_VALIDATION_PII_NOT_REDACTED"):
        repository.record_session(_walkthrough(1, finding="email leaked"))


@pytest.mark.parametrize("invalid", ["consent", "pii"])
def test_user_validation_repository_checks_loaded_records(tmp_path: Path, invalid: str) -> None:
    path = tmp_path / "walkthroughs.jsonl"
    payload = _walkthrough(1).model_dump(mode="json", exclude={"digest"})
    if invalid == "consent":
        payload["consent_recorded"] = False
        reason = "USER_VALIDATION_CONSENT_REQUIRED"
    else:
        payload["redacted_findings"] = ["email leaked"]
        reason = "USER_VALIDATION_PII_NOT_REDACTED"
    path.write_text(UserWalkthroughRecord.model_validate(payload).model_dump_json() + "\n")
    with pytest.raises(ValueError, match=reason):
        UserValidationRepository(path).summarize()


def test_workspace_api_runs_end_to_end(tmp_path: Path) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "api.sqlite")
    app = create_app(workspace_service=workspace)
    with TestClient(app) as client:
        assert client.get("/api/workspace/state").status_code == 200
        form = client.post("/api/workspace/form")
        assert form.status_code == 200
        assert form.json()["state"]["quote"]["version"] == "v1"
        receipt = ToolInvocationReceipt.model_validate(
            form.json()["tool_evidence"]["invocation"]["receipt"]
        )
        called_event = ToolCalledEvent.model_validate(
            form.json()["tool_evidence"]["called_event"]
        )
        assert form.json()["tool_evidence"]["status"] == "SUCCEEDED"
        assert form.json()["tool_evidence"]["target_writes"] == 0
        assert called_event.invocation_receipt_ref == receipt.id
        preview = client.post("/api/workspace/preview/launch_date")
        assert preview.status_code == 200
        approval = client.post(
            "/api/workspace/approve/launch_date",
            json={
                "actor_id": "human:product-owner",
                "preview_digest": preview.json()["preview_digest"],
            },
        )
        assert approval.status_code == 200
        apply = client.post(
            "/api/workspace/apply/launch_date",
            json={"approval_digest": approval.json()["approval_digest"]},
        )
        assert apply.status_code == 200
        assert apply.json()["state"]["quote"]["payload"]["launch_date"] == "2026-09-15"
    workspace.close()
