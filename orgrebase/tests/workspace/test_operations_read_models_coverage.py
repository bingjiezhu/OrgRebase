from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import IntegrityError
from orgrebase.workspace.service import WorkspaceService


def _store_counts(service: WorkspaceService) -> tuple[int, int]:
    artifacts = service.store.connection.execute(
        "SELECT COUNT(*) FROM artifacts"
    ).fetchone()[0]
    return artifacts, service.store.verify_event_chain()["events"]


def _qualified_native_evidence() -> dict[str, Any]:
    return {
        "status": "PASS",
        "run_id": "run:test:agentteams-operations@v1",
        "evidence_class": "CONTROLLED_LOCAL_GOLDEN_COMPETITION",
        "summary_digest": "sha256:" + "a" * 64,
        "agentteams_action_count": 3,
        "project_terminal_state": "COMPLETED",
        "agent_collaboration": {
            "orchestration_plan": {"digest": "sha256:" + "b" * 64},
            "agent_runs": [{"id": "agent-run:product@a1"}],
            "authority": {"agent_target_writes": 0},
        },
    }


def _set_nested(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def test_missing_persisted_review_gate_blocks_resume_without_writes(
    tmp_path: Path,
) -> None:
    """A corrupt/legacy preview must never be projected as resumable approval."""

    service = WorkspaceService(store_path=tmp_path / "missing-gate.sqlite3")
    try:
        service.form_quote()
        state = service.preview_command("launch_date")["state"]
        changes = deepcopy(state["changes"])
        changes["launch_date"]["preview"]["review_gate"] = {
            "artifact_id": "review-gate:missing",
            "artifact_digest": "sha256:" + "0" * 64,
            "gate": None,
        }
        before = _store_counts(service)

        view = service._human_interrupts_view(changes)

        assert _store_counts(service) == before
        assert view["active_kind"] is None
        assert view["active_kinds"] == []
        blocked = view["by_change"]["launch_date"]
        assert blocked["status"] == "BLOCKED_MISSING_PERSISTED_GATE"
        assert blocked["owner_id"] is None
        assert blocked["workflow_run_id"] is None
        assert blocked["resume_action"] is None
        assert blocked["next_action"] is None
        assert blocked["outcome_artifact_digest"] is None
        assert blocked["read_model_target_writes"] == 0

        # Missing change envelopes are omitted rather than invented.
        empty = service._human_interrupts_view({})
        assert empty["by_change"] == {}
        assert empty["active_kinds"] == []
    finally:
        service.close()


def test_in_memory_interrupt_discloses_that_restart_recovery_is_unavailable() -> None:
    service = WorkspaceService()
    try:
        service.form_quote()
        state = service.preview_command("launch_date")["state"]
        interrupt = state["human_interrupts"]["by_change"]["launch_date"]

        assert interrupt["status"] == "WAITING_HUMAN"
        assert interrupt["restart_recoverable"] is False
        assert interrupt["resume_action"]["required_body_fields"] == [
            "actor_id",
            "preview_digest",
        ]
        assert interrupt["expiry"] == {
            "status": "NOT_CONFIGURED",
            "expires_at": None,
        }
    finally:
        service.close()


def test_complete_controlled_local_evidence_qualifies_only_formation_scope() -> None:
    service = WorkspaceService()
    try:
        evidence = _qualified_native_evidence()
        before = _store_counts(service)

        view = service._agentteams_operations_view(
            changes={},
            competition_evidence=evidence,
        )

        assert _store_counts(service) == before
        formation = view["formation_taskflow"]
        assert formation == {
            "scope": "INITIAL_QUOTE_FORMATION_ONLY",
            "participation_status": "CONTROLLED_LOCAL_NATIVE_OBSERVED",
            "native_taskflow_observed": True,
            "live_distributed_observed": False,
            "run_id": evidence["run_id"],
            "evidence_class": evidence["evidence_class"],
            "summary_digest": evidence["summary_digest"],
            "action_count": 3,
            "project_terminal_state": "COMPLETED",
            "agentteams_canonical_target_writes": 0,
        }
        assert view["change_set_advisories"] == {}
        assert view["authority"]["canonical_authority"] == (
            "ORGREBASE_STATESTORE_AND_REBASE_WORKFLOW"
        )
        assert view["read_model_target_writes"] == 0
    finally:
        service.close()


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    (
        ("status", "FAIL"),
        ("agentteams_action_count", True),
        ("agentteams_action_count", 0),
        ("project_terminal_state", "running"),
        ("agent_collaboration.orchestration_plan", None),
        ("agent_collaboration.agent_runs", []),
        ("agent_collaboration.authority.agent_target_writes", 1),
    ),
)
def test_incomplete_or_effectful_agentteams_evidence_fails_closed(
    path: str,
    invalid_value: Any,
) -> None:
    """No individual receipt field may promote an incomplete AT execution."""

    service = WorkspaceService()
    try:
        evidence = _qualified_native_evidence()
        _set_nested(evidence, path, invalid_value)

        formation = service._agentteams_operations_view(
            changes={},
            competition_evidence=evidence,
        )["formation_taskflow"]

        assert formation["participation_status"] == "UNQUALIFIED_EVIDENCE"
        assert formation["native_taskflow_observed"] is False
        assert formation["live_distributed_observed"] is False
        if path == "agentteams_action_count" and invalid_value is True:
            assert formation["action_count"] is None
    finally:
        service.close()


def test_malformed_agentteams_evidence_never_becomes_native_participation() -> None:
    service = WorkspaceService()
    try:
        malformed_inputs: tuple[Any, ...] = (
            "not-a-mapping",
            {
                "status": "PASS",
                "agentteams_action_count": 1,
                "project_terminal_state": "completed",
                "agent_collaboration": [],
            },
        )
        for evidence in malformed_inputs:
            formation = service._agentteams_operations_view(
                changes={},
                competition_evidence=evidence,
            )["formation_taskflow"]
            assert formation["participation_status"] == "UNQUALIFIED_EVIDENCE"
            assert formation["native_taskflow_observed"] is False
            assert formation["live_distributed_observed"] is False
    finally:
        service.close()


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    (
        ("bundle.advisory.orchestration_plan.evidence_class", "LIVE_AGENTTEAMS"),
        ("bundle.advisory.coordination_receipt.status", "FAIL"),
        (
            "bundle.advisory.ingestion_receipt.source_evidence_class",
            "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
        ),
        ("bundle.advisory.ingestion_receipt.target_writes", 1),
        ("bundle.advisory.agent_runs", []),
        ("bundle.advisory", None),
    ),
)
def test_change_advisory_requires_every_zero_write_local_boundary(
    tmp_path: Path,
    path: str,
    invalid_value: Any,
) -> None:
    """A local explanation cannot be relabelled as AT or remain qualified after drift."""

    service = WorkspaceService(store_path=tmp_path / "advisory.sqlite3")
    try:
        service.form_quote()
        state = service.preview_command("launch_date")["state"]
        changes = deepcopy(state["changes"])
        _set_nested(changes["launch_date"]["preview"], path, invalid_value)
        before = _store_counts(service)

        advisory = service._agentteams_operations_view(
            changes=changes,
            competition_evidence=None,
        )["change_set_advisories"]["launch_date"]

        assert _store_counts(service) == before
        assert advisory["participation_status"] == "UNQUALIFIED_ADVISORY"
        assert advisory["native_agentteams_observed"] is False
        assert advisory["live_agentteams_observed"] is False
    finally:
        service.close()


def test_agentteams_operations_endpoint_fails_closed_on_state_integrity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WorkspaceService(store_path=tmp_path / "api-error.sqlite3")

    def corrupt_state() -> dict[str, Any]:
        raise IntegrityError("WORKSPACE_AGENTTEAMS_EVIDENCE_CORRUPT")

    monkeypatch.setattr(service, "state", corrupt_state)
    try:
        with TestClient(create_app(workspace_service=service)) as client:
            response = client.get("/api/workspace/agentteams-operations")

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "EVIDENCE_INTEGRITY_FAILED",
            "message": "WORKSPACE_AGENTTEAMS_EVIDENCE_CORRUPT",
        }
    finally:
        service.close()
