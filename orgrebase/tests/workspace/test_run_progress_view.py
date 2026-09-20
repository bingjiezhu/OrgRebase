from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import (
    _same_run_candidate_output_view,
    _workspace_current_run_archive_view,
    _workspace_run_progress_view,
    create_app,
)
from orgrebase.digest import sha256_digest
from orgrebase.workspace.native_taskflow import LifecycleJournal
from orgrebase.workspace.service import WorkspaceService

RUN_ID = "run:test:observed-progress"
ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PILOT = ROOT / "evidence" / "golden-competition" / "latest" / "pilot"


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "digest": sha256_digest(payload)}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class _WorkspaceStub:
    def __init__(self, root: Path, state: dict[str, Any] | None = None) -> None:
        self.competition_evidence_root = root
        self.effective_workflow_run_id = RUN_ID
        self.store_path = ":memory:"
        self._state = state
        self.state_calls = 0

    def state(self, *, history_limit: int | None = None) -> dict[str, Any]:
        self.state_calls += 1
        if self._state is None:
            raise AssertionError("canonical state must not be read while Formation is running")
        return self._state

    def oac_activation_state(self) -> dict[str, Any]:
        return {"status": "NOT_USED_IN_THIS_RUN"}


def _write_live_run(
    root: Path, *, task_id: str = "task-17", role: str = "REVIEWER", spec_run_id: str = RUN_ID,
) -> None:
    run_root = root / "run-00001"
    _write(
        run_root / "execution-envelope.json",
        _sealed({"schema_version": "test", "run_id": RUN_ID}),
    )
    action_names = [
        ("create_project", "project:create"),
        ("delegate_task", f"{task_id}:delegate"),
        ("ack_task", f"{task_id}:ack"),
        ("submit_task", f"{task_id}:submit"),
        ("check_task", f"{task_id}:check"),
        ("accept_task_result", f"{task_id}:accept"),
        ("complete_project", "project:complete"),
    ]
    journal = LifecycleJournal(run_root / "agentteams/action-journal.json")
    for action, key in action_names:
        tool = "projectflow" if action in {"create_project", "complete_project", "accept_task_result"} else "taskflow"
        parameters: dict[str, Any] = {"projectId": "project-17", "taskId": task_id}
        payload: dict[str, Any] = {
            "tool": tool, "action": action, "ok": True,
        }
        if action in {"create_project", "complete_project"}:
            payload["project"] = {
                "project_id": "project-17",
                "status": "completed" if action == "complete_project" else "active",
            }
        elif action == "delegate_task":
            parameters.update({
                "assignedTo": "@review-agent:controlled.local",
                "spec": json.dumps({
                    "schema_version": "orgrebase.golden-agentteams-task-spec.v1",
                    "run_id": spec_run_id, "project_id": "project-17", "task_id": task_id,
                    "role": role, "assignee": "@review-agent:controlled.local",
                    "candidate_only": True, "target_writes": 0,
                }),
            })
            payload.update({
                "task": {"task_id": task_id, "project_id": "project-17", "status": "assigned"},
                "synced": True, "notification": {"sent": True, "eventId": "$assignment"},
            })
        elif action == "accept_task_result":
            parameters.update({"resultStatus": "SUCCESS", "accepted": True})
            payload.update({
                "accepted": True, "nodeStatus": "completed", "taskId": task_id,
                "project": {"project_id": "project-17"},
            })
        journal.record(
            key=key, tool=tool, action=action,
            arguments={"action": action, "workspaceDir": "orgrebase://runtime-workspace", "payload": parameters},
            response={"content": [{"type": "text", "text": json.dumps(payload)}]},
        )
    _write(
        run_root / "tool/invocation.json",
        {
            "receipt": _sealed(
                {
                    "run_id": RUN_ID,
                    "status": "SUCCEEDED",
                    "target_writes": 0,
                }
            )
        },
    )
    _write(
        run_root / "skill/receipt.json",
        _sealed(
            {
                "run_id": RUN_ID,
                "outcome": "SUCCESS",
                "target_writes": 0,
            }
        ),
    )


def test_run_progress_endpoint_is_read_only_and_waiting_before_task(tmp_path: Path) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite3")
    before = workspace.state()
    try:
        with TestClient(create_app(workspace_service=workspace)) as client:
            response = client.get("/api/workspace/run-progress")
        after = workspace.state()
    finally:
        workspace.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "orgrebase.workspace-run-progress-view.v1"
    assert payload["status"] == "WAITING"
    assert payload["canonical_state_observed"] is True
    assert payload["canonical_stage_authority"] == "ORGREBASE_CONTROL_PLANE"
    assert all(item["status"] == "WAITING" for item in payload["milestones"])
    assert after == before


def test_running_progress_reads_only_persisted_same_run_actions(tmp_path: Path) -> None:
    _write_live_run(tmp_path)
    workspace = _WorkspaceStub(tmp_path)

    view = _workspace_run_progress_view(
        workspace,
        {
            "status": "RUNNING",
            "run_id": RUN_ID,
            "stage": "AGENTTEAMS_EXECUTION",
            "started_at": "2026-08-31T00:00:00Z",
        },
    )

    assert workspace.state_calls == 0
    assert view["status"] == "RUNNING"
    assert view["evidence_class"] == "LIVE_THIS_RUN_CONTROLLED_LOCAL"
    assert view["canonical_state_observed"] is False
    assert view["action_count"] == 7
    milestones = {item["id"]: item for item in view["milestones"]}
    for milestone_id in (
        "TASK_INTAKE",
        "PROJECT_CREATED",
        "DELEGATED",
        "ACKNOWLEDGED",
        "CONTEXT_AND_RESULT_HANDOFF",
        "SUPPLEMENTAL_TOOL_EVIDENCE",
        "SKILL_INVOKED",
        "REVIEWER_ACCEPTED",
        "AGENTTEAMS_TERMINAL",
    ):
        assert milestones[milestone_id]["status"] == "OBSERVED"
    assert milestones["HUMAN_APPROVALS"]["status"] == "WAITING"
    assert milestones["CANONICAL_BUSINESS_TERMINAL"]["status"] == "WAITING"


@pytest.mark.parametrize("role,spec_run_id", [
    ("DOMAIN_WORKER", RUN_ID), ("REVIEWER", "run:other"),
])
def test_running_reviewer_accept_requires_explicit_same_run_role(
    tmp_path: Path, role: str, spec_run_id: str,
) -> None:
    _write_live_run(tmp_path, task_id="reviewer-finance-worker", role=role, spec_run_id=spec_run_id)
    workspace = _WorkspaceStub(tmp_path)
    view = _workspace_run_progress_view(workspace, {"status": "RUNNING", "run_id": RUN_ID})
    milestone = next(item for item in view["milestones"] if item["id"] == "REVIEWER_ACCEPTED")
    assert milestone["status"] == "WAITING"
    assert workspace.state_calls == 0


@pytest.mark.parametrize("mutation", [
    "missing", "request_digest", "response_digest", "raw_schema", "other_project",
    "response_task", "unsafe_ref", "outside_symlink",
])
def test_running_reviewer_accept_rejects_unbound_native_evidence(tmp_path: Path, mutation: str) -> None:
    _write_live_run(tmp_path)
    agentteams = tmp_path / "run-00001/agentteams"
    journal = agentteams / "action-journal.json"
    actions = json.loads(journal.read_text())
    index = 1 if mutation in {"missing", "request_digest", "unsafe_ref", "outside_symlink"} else 5
    action = actions[index]
    path = agentteams / action["raw_ref"]
    raw = json.loads(path.read_text())
    if mutation == "missing":
        path.rename(path.with_suffix(".unavailable"))
    elif mutation == "request_digest":
        raw["request"]["payload"]["spec"] = "{}"
        _write(path, raw)
    elif mutation == "response_digest":
        raw["response"] = {"content": [{"type": "text", "text": "{}"}]}
        _write(path, raw)
    elif mutation == "unsafe_ref":
        action["raw_ref"] = "../another-run/delegate.json"
        actions[index] = _sealed({key: value for key, value in action.items() if key != "digest"})
        _write(journal, actions)
    elif mutation == "outside_symlink":
        outside = tmp_path / "another-run/delegate.json"
        outside.parent.mkdir()
        path.rename(outside)
        path.symlink_to(outside)
    else:
        response = json.loads(raw["response"]["content"][0]["text"])
        if mutation == "raw_schema":
            raw["schema_version"] = "unrecognized"
        elif mutation == "other_project":
            raw["request"]["payload"]["projectId"] = "another-project"
            response["project"]["project_id"] = "another-project"
        elif mutation == "response_task":
            response["taskId"] = "another-task"
        raw["response"]["content"][0]["text"] = json.dumps(response)
        action.update({
            "request_digest": sha256_digest(raw["request"]),
            "response_digest": sha256_digest(raw["response"]),
            "payload_digest": sha256_digest(response),
        })
        actions[index] = _sealed({key: value for key, value in action.items() if key != "digest"})
        _write(path, raw)
        _write(journal, actions)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    workspace = _WorkspaceStub(tmp_path)
    view = _workspace_run_progress_view(workspace, {"status": "RUNNING", "run_id": RUN_ID})
    milestone = next(item for item in view["milestones"] if item["id"] == "REVIEWER_ACCEPTED")
    assert milestone["status"] == "WAITING"
    assert workspace.state_calls == 0
    assert {path: path.read_bytes() for path in tmp_path.rglob("*.json")} == before


@pytest.mark.parametrize("role,plan_run_id,expected", [
    ("REVIEWER", RUN_ID, "OBSERVED"),
    ("DOMAIN_WORKER", RUN_ID, "WAITING"),
    ("REVIEWER", "run:other", "WAITING"),
])
def test_stored_reviewer_accept_uses_sealed_same_run_plan_roles(
    tmp_path: Path, role: str, plan_run_id: str, expected: str,
) -> None:
    _write_live_run(tmp_path)
    actions = json.loads((tmp_path / "run-00001/agentteams/action-journal.json").read_text())
    state = {
        "execution": {"run_id": RUN_ID},
        "competition_evidence": {
            "run_id": RUN_ID,
            "agent_collaboration": {
                "actions": actions,
                "orchestration_plan": _sealed({
                    "run_id": plan_run_id, "project_id": "project-17",
                    "tasks": [_sealed({"id": "task-17", "role": role})],
                }),
            },
        },
    }
    view = _workspace_run_progress_view(_WorkspaceStub(tmp_path / "no-live-journal", state))
    milestone = next(item for item in view["milestones"] if item["id"] == "REVIEWER_ACCEPTED")
    assert milestone["status"] == expected


def test_running_reviewer_accept_reads_retained_runner_evidence(tmp_path: Path) -> None:
    source = GOLDEN_PILOT / "golden-run"
    destination = tmp_path / "run-00001"
    shutil.copytree(source / "agentteams", destination / "agentteams")
    shutil.copyfile(source / "execution-envelope.json", destination / "execution-envelope.json")
    run_id = json.loads((destination / "execution-envelope.json").read_text())["run_id"]
    workspace = _WorkspaceStub(tmp_path)
    view = _workspace_run_progress_view(workspace, {"status": "RUNNING", "run_id": run_id})
    milestone = next(item for item in view["milestones"] if item["id"] == "REVIEWER_ACCEPTED")
    assert milestone["status"] == "OBSERVED"
    assert workspace.state_calls == 0


def test_completed_progress_uses_control_plane_for_human_and_business_terminal(
    tmp_path: Path,
) -> None:
    state = {
        "stage": "QUOTE_V3",
        "execution": {"run_id": RUN_ID},
        "changes": {
            kind: {
                "approval": {
                    "approval_digest": f"sha256:{suffix * 64}",
                    "approval": {"digest": f"sha256:{suffix * 64}"},
                }
            }
            for kind, suffix in (("launch_date", "a"), ("currency", "b"))
        },
    }
    workspace = _WorkspaceStub(tmp_path, state)

    view = _workspace_run_progress_view(
        workspace,
        {"status": "ACTIVE", "run_id": RUN_ID, "stage": "QUOTE_V1"},
    )

    assert workspace.state_calls == 1
    assert view["status"] == "COMPLETED"
    assert view["canonical_state_observed"] is True
    milestones = {item["id"]: item for item in view["milestones"]}
    assert milestones["HUMAN_APPROVALS"] == {
        "id": "HUMAN_APPROVALS",
        "status": "OBSERVED",
        "observed_count": 2,
        "expected_count": 2,
        "evidence_digest": None,
    }
    assert milestones["CANONICAL_BUSINESS_TERMINAL"]["status"] == "OBSERVED"


def test_current_run_archive_opens_only_for_exact_terminal_business_run() -> None:
    state = json.loads((GOLDEN_PILOT / "state.json").read_text(encoding="utf-8"))
    workspace = _WorkspaceStub(GOLDEN_PILOT / "golden-run", state)

    view = _workspace_current_run_archive_view(workspace)

    assert view["schema_version"] == "orgrebase.workspace-current-run-archive-view.v1"
    assert view["status"] == "ARCHIVED"
    assert view["run_id"] == state["execution"]["run_id"]
    assert view["record"]["same_run_as_current_task"] is True
    assert view["record"]["terminal_status"] == "COMPLETED"
    assert view["record"]["quote"]["ref"].endswith("@v3")
    assert view["record"]["human_approval_count"] == 2
    assert [item["kind"] for item in view["record"]["selective_rebase_receipts"]] == [
        "launch_date",
        "currency",
    ]
    assert view["record"]["canonical_authority"] == "ORGREBASE_CONTROL_PLANE"
    assert view["failures"] == []


def test_current_run_archive_fails_closed_before_terminal_or_on_cross_run_receipt() -> None:
    state = json.loads((GOLDEN_PILOT / "state.json").read_text(encoding="utf-8"))
    pending = json.loads(json.dumps(state))
    pending["stage"] = "QUOTE_V2"
    pending_view = _workspace_current_run_archive_view(
        _WorkspaceStub(GOLDEN_PILOT / "golden-run", pending)
    )
    assert pending_view["status"] == "PENDING"
    assert pending_view["record"] is None

    tampered = json.loads(json.dumps(state))
    tampered["changes"]["currency"]["outcome"]["outcome"]["rebase_receipt"][
        "workflow_run_id"
    ] = "run:other"
    invalid_view = _workspace_current_run_archive_view(
        _WorkspaceStub(GOLDEN_PILOT / "golden-run", tampered)
    )
    assert invalid_view["status"] == "INVALID"
    assert invalid_view["record"] is None
    assert "CURRENCY_COMPLETION_BINDING_INVALID" in invalid_view["failures"]


def test_tampered_partial_journal_is_not_presented_as_progress(tmp_path: Path) -> None:
    _write_live_run(tmp_path)
    journal = tmp_path / "run-00001/agentteams/action-journal.json"
    actions = json.loads(journal.read_text(encoding="utf-8"))
    actions[1]["action"] = "complete_project"
    _write(journal, actions)
    workspace = _WorkspaceStub(tmp_path)

    view = _workspace_run_progress_view(
        workspace,
        {"status": "RUNNING", "run_id": RUN_ID, "stage": "AGENTTEAMS_EXECUTION"},
    )

    assert view["action_count"] == 0
    milestones = {item["id"]: item for item in view["milestones"]}
    for milestone_id in (
        "PROJECT_CREATED",
        "DELEGATED",
        "ACKNOWLEDGED",
        "CONTEXT_AND_RESULT_HANDOFF",
        "REVIEWER_ACCEPTED",
        "AGENTTEAMS_TERMINAL",
    ):
        assert milestones[milestone_id]["status"] == "WAITING"
    # Independently sealed same-run Tool and Skill receipts remain observable;
    # a damaged AgentTeams journal does not erase or fabricate those facts.
    assert milestones["SUPPLEMENTAL_TOOL_EVIDENCE"]["status"] == "OBSERVED"
    assert milestones["SKILL_INVOKED"]["status"] == "OBSERVED"


def test_progress_action_details_are_minimal_and_bound_to_the_run(tmp_path: Path) -> None:
    _write_live_run(tmp_path)
    workspace = _WorkspaceStub(tmp_path)
    journal = tmp_path / "run-00001/agentteams/action-journal.json"
    actions = json.loads(journal.read_text(encoding="utf-8"))
    actions[0] = _sealed({
        **{key: value for key, value in actions[0].items() if key != "digest"},
        "raw_ref": "/private/enterprise/customer.json",
        "request_body": "confidential customer request",
    })
    actions[1] = _sealed({
        **{key: value for key, value in actions[1].items() if key != "digest"},
        "key": '<img src=x onerror="alert(1)">',
    })
    _write(journal, actions)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    view = _workspace_run_progress_view(workspace, {"status": "RUNNING", "run_id": RUN_ID})
    assert len(view["actions"]) == view["action_count"] == 7
    assert set(view["actions"][0]) == {"sequence", "tool", "action", "key", "status"}
    assert view["actions"][0] == {
        "sequence": 1, "tool": "projectflow", "action": "create_project",
        "key": "project:create", "status": "active",
    }
    assert view["actions"][1]["key"] is None
    public = json.dumps(view)
    assert "customer.json" not in public and "confidential" not in public and "<img" not in public
    assert {path: path.read_bytes() for path in tmp_path.rglob("*.json")} == before

    other = _workspace_run_progress_view(workspace, {"status": "RUNNING", "run_id": "run:other"})
    assert other["actions"] == [] and other["action_count"] == 0
    assert other["reviewer_model_usage"] == []
    stored = _WorkspaceStub(tmp_path / "no-live-journal", {
        "execution": {"run_id": RUN_ID},
        "competition_evidence": {"run_id": RUN_ID, "agent_collaboration": {"actions": actions}},
    })
    restored = _workspace_run_progress_view(stored)
    assert restored["action_count"] == 7
    assert restored["actions"] == view["actions"]


def _reviewer_usage_state(**changes: Any) -> dict[str, Any]:
    task_id = "project:reviewer-a1"
    attempt = _sealed({
        "schema_version": "orgrebase.golden-model-attempt-evidence.v2",
        "run_id": RUN_ID, "task_id": task_id, "phase": 1,
        "provider": "ollama-local", "requested_model_id": "qwen2.5:3b",
        "input_tokens": 0, "output_tokens": 0, "latency_ms": 23,
        "model_attempt_observation_digest": sha256_digest({"observation": "test"}),
        "observation_persistence": "DURABLE",
        "usage": {"status": "PARTIAL", "basis": "provider_response", "input_tokens": 0, "output_tokens": None},
        **changes,
    })
    return {
        "stage": "QUOTE_V1", "execution": {"run_id": RUN_ID},
        "competition_evidence": {
            "run_id": RUN_ID,
            "agent_collaboration": {
                "reviewer": {"model_attempts": [attempt]},
                "orchestration_plan": {"tasks": [{"id": task_id, "attempt": 1, "role": "REVIEWER"}]},
            },
        },
    }


def test_progress_usage_preserves_unknown_and_measured_zero(tmp_path: Path) -> None:
    state = _reviewer_usage_state()
    before = deepcopy(state)
    view = _workspace_run_progress_view(_WorkspaceStub(tmp_path, state))
    assert view["reviewer_model_usage"] == [{
        "task_id": "project:reviewer-a1", "phase": 1, "provider": "ollama-local",
        "model_id": "qwen2.5:3b", "input_tokens": 0, "output_tokens": None,
        "latency_ms": 23, "usage_source": "provider_response", "latency_source": "client_receipt",
    }]
    assert state == before
    assert list(tmp_path.iterdir()) == []
    # Running progress never acquires the busy canonical-state lock just to show usage.
    running = _WorkspaceStub(tmp_path)
    assert _workspace_run_progress_view(running, {"status": "RUNNING"})["reviewer_model_usage"] == []
    assert running.state_calls == 0


@pytest.mark.parametrize("changes", [
    {"schema_version": "orgrebase.golden-model-attempt-evidence.v1"},
    {"observation_persistence": "WRITE_FAILED"},
    {"model_attempt_observation_digest": None},
])
def test_progress_usage_does_not_promote_legacy_or_unbound_metrics(tmp_path: Path, changes: dict) -> None:
    view = _workspace_run_progress_view(_WorkspaceStub(tmp_path, _reviewer_usage_state(**changes)))
    row = view["reviewer_model_usage"][0]
    assert row["input_tokens"] is row["output_tokens"] is row["latency_ms"] is None
    assert row["usage_source"] == row["latency_source"] == "unavailable"


@pytest.mark.parametrize("changes", [
    {"run_id": "run:other"}, {"task_id": "project:finance-a1"}, {"phase": 2},
])
def test_progress_usage_rejects_other_run_task_or_phase(tmp_path: Path, changes: dict) -> None:
    state = _reviewer_usage_state(**changes)
    assert _workspace_run_progress_view(_WorkspaceStub(tmp_path, state))["reviewer_model_usage"] == []


def test_progress_usage_rejects_tampering_and_cross_run_collection(tmp_path: Path) -> None:
    state = _reviewer_usage_state()
    state["competition_evidence"]["agent_collaboration"]["reviewer"]["model_attempts"][0]["latency_ms"] = 999
    assert _workspace_run_progress_view(_WorkspaceStub(tmp_path, state))["reviewer_model_usage"] == []
    state = _reviewer_usage_state()
    state["competition_evidence"]["run_id"] = "run:other"
    assert _workspace_run_progress_view(_WorkspaceStub(tmp_path, state))["reviewer_model_usage"] == []


def test_progress_usage_invalid_values_and_private_metadata_stay_unknown(tmp_path: Path) -> None:
    state = _reviewer_usage_state(
        provider="/private/provider-token", requested_model_id="<svg onload=alert(1)>",
        latency_ms=-3,
        usage={"status": "PARTIAL", "basis": "provider_response", "input_tokens": True, "output_tokens": -1},
    )
    row = _workspace_run_progress_view(_WorkspaceStub(tmp_path, state))["reviewer_model_usage"][0]
    assert row["provider"] is row["model_id"] is None
    assert row["input_tokens"] is row["output_tokens"] is row["latency_ms"] is None
    assert row["usage_source"] == row["latency_source"] == "unavailable"


def _golden_candidate_output_view(root: Path | None = None) -> dict[str, Any]:
    state = json.loads((GOLDEN_PILOT / "state.json").read_text(encoding="utf-8"))
    workspace = _WorkspaceStub(root or GOLDEN_PILOT / "golden-run", state)
    return _same_run_candidate_output_view(workspace, state)


def test_same_run_candidate_outputs_are_digest_bound_not_baseline_values() -> None:
    view = _golden_candidate_output_view()

    assert view["status"] == "PASS"
    assert view["candidate_only"] is True
    assert view["target_writes"] == 0
    assert len(view["outputs"]) == 7
    assert all(item["candidate_only"] is True for item in view["outputs"])
    assert all(item["target_writes"] == 0 for item in view["outputs"])

    by_actor: dict[str, list[dict[str, Any]]] = {}
    for item in view["outputs"]:
        by_actor.setdefault(item["actor_id"], []).append(item)

    product = by_actor["product-steward"][0]
    assert {item["predicate"] for item in product["candidate_outputs"]} == {
        "product_plan",
        "launch_date",
        "data_residency",
    }
    legal = by_actor["legal-steward"][0]
    assert [(item["predicate"], item["value"]) for item in legal["candidate_outputs"]] == [
        ("notice_required", True)
    ]
    assert legal["candidate_outputs"][0]["transformation_ref"] == "transform:legal-minimal-disclosure@v1"

    finance = sorted(by_actor["finance-steward"], key=lambda item: item["attempt"])
    assert finance[0]["status"] == "ABSTAIN"
    assert finance[0]["missing_fields"] == ["price_band"]
    assert [item["predicate"] for item in finance[0]["candidate_outputs"]] == ["currency"]
    assert finance[1]["status"] == "PASS"
    assert [item["predicate"] for item in finance[1]["candidate_outputs"]] == [
        "currency",
        "price_band",
    ]

    gtm = by_actor["gtm-steward"][0]
    gtm_values = {item["predicate"]: item["value"] for item in gtm["candidate_outputs"]}
    assert gtm_values == {
        "partner_terms": "legal-review",
        "quote_compose_skill": "skill:enterprise-quote-compose@1.0",
    }

    reviewers = sorted(by_actor["independent-reviewer"], key=lambda item: item["attempt"])
    assert reviewers[0]["decision"]["verdict"] == "REPLAN"
    assert reviewers[0]["decision"]["missing_domains"] == ["finance"]
    assert reviewers[1]["decision"]["verdict"] == "PASS"
    assert reviewers[1]["decision"]["missing_domains"] == []
    assert {item["executor_ref"] for item in reviewers} == {"reviewer:quote-coalition"}


def test_tampered_candidate_output_fails_closed_without_partial_values(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "golden-run"
    evidence_root.mkdir()
    shutil.copy2(
        GOLDEN_PILOT / "golden-run" / "execution-envelope.json",
        evidence_root / "execution-envelope.json",
    )
    shutil.copy2(
        GOLDEN_PILOT / "golden-run" / "process-receipts.json",
        evidence_root / "process-receipts.json",
    )
    shutil.copytree(
        GOLDEN_PILOT / "golden-run" / "process-outputs",
        evidence_root / "process-outputs",
    )
    product_output = next((evidence_root / "process-outputs").glob("*-product-a1.json"))
    tampered = json.loads(product_output.read_text(encoding="utf-8"))
    tampered["claim_candidates"][0]["value"] = "tampered-baseline-like-value"
    _write(product_output, tampered)

    view = _golden_candidate_output_view(evidence_root)

    assert view["status"] == "FAIL"
    assert view["outputs"] == []
    assert any(failure.startswith("OUTPUT_TASK_HANDOFF_BINDING:") for failure in view["failures"])


def test_workspace_state_endpoint_adds_read_only_candidate_projection() -> None:
    state = json.loads((GOLDEN_PILOT / "state.json").read_text(encoding="utf-8"))
    original_state = deepcopy(state)
    workspace = _WorkspaceStub(GOLDEN_PILOT / "golden-run", state)

    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_candidate_outputs"]["status"] == "PASS"
    assert payload["agent_candidate_outputs"]["run_id"] == state["execution"]["run_id"]
    assert workspace.state_calls == 1
    assert state == original_state
