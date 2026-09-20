from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.workspace.models import WorkspacePreviewBundle
from orgrebase.workspace.service import (
    CONTROLLED_LOCAL_HEADER_IDENTITY,
    WorkspaceReviewGatePending,
    WorkspaceService,
)


@dataclass
class MutableClock:
    now: float

    def __call__(self) -> float:
        return self.now


def test_review_gate_is_preview_bound_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "review-gate.sqlite3"
    clock = MutableClock(1_800_000_000.0)
    service = WorkspaceService(
        store_path=path,
        review_duration_seconds=4,
        wall_clock=clock,
    )
    service.form_quote()
    preview = service.preview_command("launch_date")
    bundle = WorkspacePreviewBundle.model_validate(preview["bundle"])
    gate_record = preview["review_gate"]
    gate = gate_record["gate"]

    assert gate["review_duration_ms"] == 4_000
    assert gate["not_before_epoch_ms"] - gate["previewed_at_epoch_ms"] == 4_000
    assert gate["preview_digest"] == preview["preview_digest"]
    assert gate["preview_artifact_digest"] == preview["artifact_digest"]
    assert gate["owner_id"] == bundle.change_spec.owner_id
    assert gate["workflow_run_id"] == bundle.run_envelope.run_id
    assert gate["run_nonce"] == bundle.run_envelope.nonce

    interrupt = preview["state"]["human_interrupts"]
    assert interrupt["active_kind"] == "launch_date"
    waiting = interrupt["by_change"]["launch_date"]
    assert waiting["status"] == "WAITING_HUMAN"
    assert waiting["owner_id"] == bundle.change_spec.owner_id
    assert waiting["workflow_run_id"] == bundle.run_envelope.run_id
    assert waiting["gate_artifact_digest"] == gate_record["artifact_digest"]
    assert waiting["preview_digest"] == bundle.preview.digest
    assert waiting["review_not_before"] == gate["not_before"]
    assert waiting["resume_action"] == {
        "operation": "APPROVE_CHANGE",
        "method": "POST",
        "endpoint": "/api/workspace/approve/launch_date",
        "required_body_fields": ["actor_id", "preview_digest"],
        "required_header_fields": [],
    }
    assert waiting["expiry"] == {
        "status": "NOT_CONFIGURED",
        "expires_at": None,
    }
    assert "remaining_ms" not in waiting

    counts_before = {
        "artifacts": service.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
        "events": service.store.verify_event_chain()["events"],
    }
    with pytest.raises(WorkspaceReviewGatePending) as pending:
        service.approve_change(
            "launch_date",
            actor_id=bundle.change_spec.owner_id,
            preview_digest=bundle.preview.digest,
        )
    assert pending.value.code == "WORKSPACE_REVIEW_GATE_NOT_READY"
    assert pending.value.remaining_ms == 4_000
    assert (
        service.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        == counts_before["artifacts"]
    )
    assert service.store.verify_event_chain()["events"] == counts_before["events"]
    service.close()

    # Reopening with a zero-duration process configuration must not weaken the
    # already persisted four-second gate.
    clock.now += 2
    reopened = WorkspaceService.reopen(
        path,
        review_duration_seconds=0,
        wall_clock=clock,
    )
    try:
        reopened_waiting = reopened.state()["human_interrupts"]["by_change"][
            "launch_date"
        ]
        assert reopened_waiting["status"] == "WAITING_HUMAN"
        assert reopened_waiting["restart_recoverable"] is True
        assert reopened_waiting["gate_artifact_digest"] == gate_record["artifact_digest"]
        with pytest.raises(WorkspaceReviewGatePending) as restarted_pending:
            reopened.approve_change(
                "launch_date",
                actor_id=bundle.change_spec.owner_id,
                preview_digest=bundle.preview.digest,
            )
        assert restarted_pending.value.remaining_ms == 2_000

        clock.now += 2
        approved = reopened.approve_change(
            "launch_date",
            actor_id=bundle.change_spec.owner_id,
            preview_digest=bundle.preview.digest,
        )
        assert approved["approval"]["actor_id"] == bundle.change_spec.owner_id
        assert approved["state"]["stage"] == "APPROVED"
        review_evidence = approved["approval_review_evidence"]
        assert review_evidence["review_wait_satisfied"] is True
        assert review_evidence["review_duration_ms"] == 4_000
        assert review_evidence["approval_observed_at_epoch_ms"] == gate["not_before_epoch_ms"]
        assert (
            review_evidence["approval_observed_at_epoch_ms"]
            >= review_evidence["review_not_before_epoch_ms"]
        )
        assert review_evidence["event_digest"].startswith("sha256:")
        resumed = approved["state"]["human_interrupts"]["by_change"][
            "launch_date"
        ]
        assert resumed["status"] == "RESUMED"
        assert resumed["resume_action"] is None
        assert resumed["next_action"]["operation"] == "APPLY_APPROVED_CHANGE"
        assert resumed["next_action"]["binding_value"] == approved["approval_digest"]
        persisted_gate = approved["state"]["changes"]["launch_date"]["preview"]["review_gate"]
        assert persisted_gate["artifact_digest"] == gate_record["artifact_digest"]
        applied = reopened.apply_approved_change(
            "launch_date",
            approval_digest=approved["approval_digest"],
        )
        completed = applied["state"]["human_interrupts"]["by_change"][
            "launch_date"
        ]
        assert completed["status"] == "COMPLETED"
        assert completed["resume_action"] is None
        assert completed["next_action"] is None
        assert completed["outcome_artifact_digest"] == applied["artifact_digest"]
        assert (
            completed["gate_artifact_digest"]
            == persisted_gate["artifact_digest"]
            == gate_record["artifact_digest"]
        )
    finally:
        reopened.close()


def test_api_reports_stable_pending_conflict_with_remaining_millis(
    tmp_path: Path,
) -> None:
    clock = MutableClock(1_800_000_100.0)
    service = WorkspaceService(
        store_path=tmp_path / "pending-api.sqlite3",
        review_duration_seconds=4,
        wall_clock=clock,
    )
    with TestClient(create_app(workspace_service=service)) as client:
        assert client.post("/api/workspace/form").status_code == 200
        preview = client.post("/api/workspace/preview/launch_date").json()
        rejected = client.post(
            "/api/workspace/approve/launch_date",
            json={
                "actor_id": "human:product-owner",
                "preview_digest": preview["preview_digest"],
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == {
            "code": "WORKSPACE_REVIEW_GATE_NOT_READY",
            "message": "WORKSPACE_REVIEW_GATE_NOT_READY",
            "remaining_ms": 4_000,
            "not_before": preview["review_gate"]["gate"]["not_before"],
        }

        clock.now += 4
        assert (
            client.post(
                "/api/workspace/approve/launch_date",
                json={
                    "actor_id": "human:product-owner",
                    "preview_digest": preview["preview_digest"],
                },
            ).status_code
            == 200
        )
    service.close()


def test_controlled_identity_uses_header_and_never_body_actor(tmp_path: Path) -> None:
    service = WorkspaceService(
        store_path=tmp_path / "header-identity.sqlite3",
        approval_identity_mode=CONTROLLED_LOCAL_HEADER_IDENTITY,
    )
    with TestClient(create_app(workspace_service=service)) as client:
        client.post("/api/workspace/form")
        preview = client.post("/api/workspace/preview/launch_date").json()
        resume = preview["state"]["human_interrupts"]["by_change"][
            "launch_date"
        ]["resume_action"]
        assert resume["required_body_fields"] == ["preview_digest"]
        assert resume["required_header_fields"] == ["X-OrgRebase-Actor"]
        request = {
            "actor_id": "human:product-owner",
            "preview_digest": preview["preview_digest"],
        }

        missing = client.post("/api/workspace/approve/launch_date", json=request)
        assert missing.status_code == 403
        assert missing.json()["detail"] == {
            "code": "WORKSPACE_ACTOR_HEADER_REQUIRED",
            "message": ("X-OrgRebase-Actor is required by the controlled-local identity mode"),
            "identity_mode": "CONTROLLED_LOCAL_HEADER_IDENTITY",
            "identity_claim_boundary": ("CONTROLLED_LOCAL_HEADER_IDENTITY_NOT_EXTERNAL_IAM"),
            "target_writes": 0,
        }

        wrong_header = client.post(
            "/api/workspace/approve/launch_date",
            json=request,
            headers={"X-OrgRebase-Actor": "human:finance-owner"},
        )
        assert wrong_header.status_code == 403
        assert wrong_header.json()["detail"] == {
            "code": "AUTHZ_DENIED",
            "message": "WORKSPACE_APPROVER_MISMATCH",
        }

        # The self-reported body actor is wrong, but the configured mode uses
        # only the admitted local header actor.
        request["actor_id"] = "human:finance-owner"
        approved = client.post(
            "/api/workspace/approve/launch_date",
            json=request,
            headers={"X-OrgRebase-Actor": "human:product-owner"},
        )
        assert approved.status_code == 200
        assert approved.json()["approval"]["actor_id"] == "human:product-owner"
        control = approved.json()["state"]["approval_control"]
        assert control["identity_mode"] == "CONTROLLED_LOCAL_HEADER_IDENTITY"
        assert control["external_iam"] == "NOT_RUN"
    service.close()


def test_environment_enables_review_and_controlled_header_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(tmp_path / "env.sqlite3"))
    monkeypatch.setenv("ORGREBASE_WORKSPACE_REVIEW_SECONDS", "4")
    monkeypatch.setenv(
        "ORGREBASE_WORKSPACE_IDENTITY_MODE",
        "CONTROLLED_LOCAL_HEADER_IDENTITY",
    )
    monkeypatch.delenv("ORGREBASE_ENTERPRISE_PACK", raising=False)
    application = create_app()
    with TestClient(application) as client:
        state = client.get("/api/workspace/state").json()
        assert state["approval_control"] == {
            "schema_version": "orgrebase.workspace-approval-control.v1",
            "review_duration_ms": 4_000,
            "gate_store": "FILE_BACKED_SQLITE_ARTIFACT",
            "restart_enforced": True,
            "identity_mode": "CONTROLLED_LOCAL_HEADER_IDENTITY",
            "identity_claim_boundary": ("CONTROLLED_LOCAL_HEADER_IDENTITY_NOT_EXTERNAL_IAM"),
            "external_iam": "NOT_RUN",
        }


def test_zero_duration_default_preserves_immediate_approval(tmp_path: Path) -> None:
    service = WorkspaceService(store_path=tmp_path / "compat.sqlite3")
    service.form_quote()
    preview = service.preview_command("launch_date")
    gate = preview["review_gate"]["gate"]
    assert gate["review_duration_ms"] == 0
    assert gate["previewed_at_epoch_ms"] == 0
    assert gate["not_before_epoch_ms"] == 0
    approved = service.approve_change(
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    assert approved["state"]["stage"] == "APPROVED"
    assert approved["approval_review_evidence"] is None
    service.close()
