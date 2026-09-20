from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, request_authorization
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.experience import (
    EXPERIENCE_STEWARD_ID,
    ExperienceReviewGatePending,
    GovernedExperienceService,
)
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[2]
FROZEN_STATE = ROOT / "evidence/golden-competition/latest/pilot/state.json"


@dataclass
class MutableClock:
    now: float = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _golden_evidence() -> dict[str, object]:
    state = json.loads(FROZEN_STATE.read_text(encoding="utf-8"))
    return state["competition_evidence"]


def _decision_args(view: dict[str, object]) -> dict[str, str]:
    candidate = view["candidate"]
    evaluation = view["evaluation"]
    assert isinstance(candidate, dict) and isinstance(evaluation, dict)
    return {
        "run_id": str(view["run_id"]),
        "actor_id": EXPERIENCE_STEWARD_ID,
        "candidate_digest": str(candidate["digest"]),
        "evaluation_digest": str(evaluation["digest"]),
        "observed_skill_head_digest": str(view["current_skill_head_digest"]),
    }


def _logical_store_dump(workspace: WorkspaceService) -> str:
    """Stable logical fingerprint covering every persisted SQLite table."""

    return "\n".join(workspace.store.connection.iterdump())


def test_experience_decision_session_revocation_at_commit_rolls_back_release(tmp_path):
    clock = MutableClock()
    with StateStore(tmp_path / "revoked.sqlite") as store:
        service = GovernedExperienceService(store, wall_clock=clock)
        view = service.ensure_candidate(_golden_evidence())
        clock.now += 4
        before = tuple(store.connection.iterdump())
        checks = []

        def authorization():
            checks.append(True)
            if len(checks) == 2:
                raise AuthenticationError("AUTH_LOCAL_SESSION_REQUIRED")

        token = request_authorization.set(authorization)
        try:
            with pytest.raises(AuthenticationError, match="AUTH_LOCAL_SESSION_REQUIRED"):
                service.decide(decision="APPROVE", **_decision_args(view))
        finally:
            request_authorization.reset(token)
        assert len(checks) == 2
        assert tuple(store.connection.iterdump()) == before
        assert service.view(str(view["run_id"]))["status"] == "AWAITING_HUMAN_APPROVAL"


def test_finance_recovery_becomes_single_run_seed_with_eight_partition_gate(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "experience.sqlite3")
    clock = MutableClock()
    service = GovernedExperienceService(store, wall_clock=clock)
    evidence = _golden_evidence()

    before_events = store.verify_event_chain()["events"]
    view = service.ensure_candidate(evidence)

    assert view["status"] == "AWAITING_HUMAN_APPROVAL"
    assert view["owner_id"] == "human:skill-steward"
    assert view["candidate"]["outcome"] == "IMPROVE"
    assert view["candidate"]["maturity"] == "SINGLE_RUN_SEED"
    assert view["candidate"]["target_skill_name"] == "structured-domain-handoff"
    assert view["candidate"]["problem_pattern"] == (
        "FINANCE_REQUIRED_SLOT_MISSING:price_band"
    )
    assert view["candidate"]["model_assistance"] == (
        "NOT_RUN_BY_DESIGN_FOR_THIN_MVP"
    )
    assert view["candidate"]["candidate_only"] is True
    assert view["candidate"]["target_writes"] == 0
    evaluation = view["evaluation"]
    assert evaluation["verdict"] == "CANARY"
    assert len(evaluation["case_results"]) == 8
    assert {item["partition"] for item in evaluation["case_results"]} == {
        "REPLAY",
        "HELD_OUT",
        "NEGATIVE_TRANSFER",
        "PERMISSION",
        "INJECTION",
        "MALFORMED",
        "RESOURCE_OR_DEADLINE",
        "CANARY",
    }
    assert all(item["passed"] for item in evaluation["case_results"])
    assert view["review_gate"]["review_duration_ms"] == 4_000
    assert view["discoverable"] is False
    assert view["loadable"] is False
    assert view["callable"] is False
    assert store.verify_event_chain()["events"] == before_events

    for method in (
        service.discover_approved,
        service.load_approved,
        service.invoke_approved,
    ):
        with pytest.raises(AuthorizationError, match="NOT_APPROVED"):
            method(str(evidence["run_id"]))
    store.close()


def test_server_wait_approval_release_and_restart_are_exactly_bound(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experience-restart.sqlite3"
    clock = MutableClock()
    store = StateStore(path)
    service = GovernedExperienceService(store, wall_clock=clock)
    view = service.ensure_candidate(_golden_evidence())
    args = _decision_args(view)
    artifacts_before_early_click = store.connection.execute(
        "SELECT COUNT(*) FROM artifacts"
    ).fetchone()[0]

    with pytest.raises(ExperienceReviewGatePending) as pending:
        service.decide(decision="APPROVE", **args)
    assert pending.value.remaining_ms == 4_000
    assert (
        store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        == artifacts_before_early_click
    )

    clock.now += 4
    approved = service.decide(decision="APPROVE", **args)
    assert approved["status"] == "APPROVED_CANARY"
    assert approved["decision"]["actor_id"] == EXPERIENCE_STEWARD_ID
    assert approved["release"]["candidate_digest"] == args["candidate_digest"]
    assert approved["release"]["evaluation_digest"] == args["evaluation_digest"]
    assert approved["release"]["predecessor_package_digest"] == args[
        "observed_skill_head_digest"
    ]
    assert approved["release"]["release_state"] == "CANARY"
    assert approved["release"]["authorization_mode"] == "RELEASE"
    assert len(approved["release"]["release_history"]) == 3
    assert approved["release"]["dry_call"]["current_quote_consumed"] is False
    assert approved["release"]["dry_call"]["receipt"]["authorization_mode"] == (
        "RELEASE"
    )
    assert approved["release"]["dry_call"]["result"]["target_writes"] == 0
    discovered = service.discover_approved(args["run_id"])
    assert len(discovered) == 1
    assert discovered[0]["name"] == "structured-domain-handoff"
    assert discovered[0]["package_digest"] == approved["release"]["package_digest"]
    assert service.load_approved(args["run_id"]).package_digest == approved["release"][
        "package_digest"
    ]
    assert service.invoke_approved(args["run_id"])["receipt"][
        "authorization_mode"
    ] == "RELEASE"
    store.close()

    reopened_store = StateStore(path)
    reopened = GovernedExperienceService(reopened_store, wall_clock=clock)
    assert reopened.view(args["run_id"])["status"] == "APPROVED_CANARY"
    assert reopened.discover_approved(args["run_id"]) == discovered
    assert reopened.invoke_approved(args["run_id"])["receipt"][
        "release_receipt_digest"
    ] == approved["release"]["release_head_digest"]
    reopened_store.close()


def test_reject_wrong_actor_cross_run_head_drift_and_tampering_fail_closed(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = StateStore(tmp_path / "reject.sqlite3")
    service = GovernedExperienceService(store, wall_clock=clock)
    view = service.ensure_candidate(_golden_evidence())
    args = _decision_args(view)
    clock.now += 4

    with pytest.raises(AuthorizationError, match="STEWARD_MISMATCH"):
        service.decide(decision="APPROVE", **{**args, "actor_id": "agent:reviewer"})
    with pytest.raises(IntegrityError, match="DIGEST_MISMATCH"):
        service.decide(
            decision="APPROVE",
            **{**args, "candidate_digest": "sha256:" + "0" * 64},
        )
    with pytest.raises(IntegrityError, match="SKILL_HEAD_DRIFT"):
        service.decide(
            decision="APPROVE",
            **{**args, "observed_skill_head_digest": "sha256:" + "0" * 64},
        )
    with pytest.raises((RuntimeError, KeyError, IntegrityError)):
        service.decide(decision="APPROVE", **{**args, "run_id": "run:foreign"})

    rejected = service.decide(decision="REJECT", **args)
    assert rejected["status"] == "REJECTED"
    assert rejected["release"] is None
    assert rejected["discoverable"] is False
    with pytest.raises(AuthorizationError, match="NOT_APPROVED"):
        service.discover_approved(args["run_id"])
    with pytest.raises(RuntimeError, match="ALREADY_FINAL"):
        service.decide(decision="APPROVE", **args)
    assert service.decide(decision="REJECT", **args)["status"] == "REJECTED"
    store.close()


def test_no_candidate_is_valid_when_the_exact_recovery_pattern_is_absent(
    tmp_path: Path,
) -> None:
    evidence = deepcopy(_golden_evidence())
    evidence["agent_collaboration"]["reviewer"]["attempt_1"]["reason_codes"] = [
        "UNRELATED_REPLAN"
    ]
    store = StateStore(tmp_path / "no-candidate.sqlite3")
    service = GovernedExperienceService(store)

    view = service.ensure_candidate(evidence)

    assert view["status"] == "NO_CANDIDATE"
    assert view["candidate"]["outcome"] == "NO_CANDIDATE"
    assert view["evaluation"] is None
    assert view["review_gate"] is None
    assert view["discoverable"] is False
    store.close()


def test_dynamic_reviewer_identity_is_resolved_by_role_and_attempt(
    tmp_path: Path,
) -> None:
    evidence = deepcopy(_golden_evidence())
    reviewer_runs = [
        item
        for item in evidence["agent_collaboration"]["agent_runs"]
        if str(item.get("task_id", "")).endswith(("-reviewer-a1", "-reviewer-a2"))
    ]
    assert len(reviewer_runs) == 2
    for run in reviewer_runs:
        run["agent_name"] = "independent-reviewer"
        run["role"] = "REVIEWER"
        run["attempt"] = 1 if str(run["task_id"]).endswith("-reviewer-a1") else 2
        run["authority_domain"] = "review"
    store = StateStore(tmp_path / "dynamic-reviewer.sqlite3")
    service = GovernedExperienceService(store)

    view = service.ensure_candidate(evidence)

    assert view["status"] == "AWAITING_HUMAN_APPROVAL"
    assert view["candidate"]["outcome"] == "IMPROVE"
    store.close()


def test_semantic_reviewer_ambiguity_does_not_fall_back_to_task_name(
    tmp_path: Path,
) -> None:
    evidence = deepcopy(_golden_evidence())
    runs = evidence["agent_collaboration"]["agent_runs"]
    reviewer_a1 = next(
        item
        for item in runs
        if str(item.get("task_id", "")).endswith("-reviewer-a1")
    )
    reviewer_a2 = next(
        item
        for item in runs
        if str(item.get("task_id", "")).endswith("-reviewer-a2")
    )
    for attempt, run in ((1, reviewer_a1), (2, reviewer_a2)):
        run["agent_name"] = "independent-reviewer"
        run["role"] = "REVIEWER"
        run["attempt"] = attempt
        run["authority_domain"] = "review"
    duplicate = deepcopy(reviewer_a1)
    duplicate["task_id"] = f"{reviewer_a1['task_id']}-ambiguous"
    runs.append(duplicate)
    store = StateStore(tmp_path / "ambiguous-reviewer.sqlite3")
    service = GovernedExperienceService(store)

    view = service.ensure_candidate(evidence)

    assert view["status"] == "NO_CANDIDATE"
    assert view["candidate"]["reason_codes"] == [
        "ELIGIBILITY_FAILED:SOURCE_DIGEST_MISSING"
    ]
    store.close()


def test_workspace_starts_background_review_only_after_business_completion_and_api_decides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock()
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite3",
        wall_clock=clock,
    )
    evidence = _golden_evidence()
    workspace.form_quote()
    workspace._competition_evidence_record = lambda: evidence  # type: ignore[method-assign]

    before = _logical_store_dump(workspace)
    waiting = workspace.experience_view()
    assert waiting["status"] == "WAITING_FOR_PENDING_CHANGES"
    assert _logical_store_dump(workspace) == before

    monkeypatch.setattr(workspace, "_business_complete", lambda: True)
    with TestClient(create_app(workspace_service=workspace)) as client:
        before_terminal_get = _logical_store_dump(workspace)
        not_started = client.get("/api/workspace/experience")
        assert not_started.status_code == 200
        assert not_started.json()["status"] == "NOT_RUN"
        assert _logical_store_dump(workspace) == before_terminal_get

        # In production this hook is invoked only by the terminal Apply command.
        # The API GET remains a projection before and after that write transition.
        workspace._ensure_terminal_experience()
        before_candidate_get = _logical_store_dump(workspace)
        candidate = client.get("/api/workspace/experience")
        assert candidate.status_code == 200
        view = candidate.json()
        assert view["status"] == "AWAITING_HUMAN_APPROVAL"
        assert _logical_store_dump(workspace) == before_candidate_get
        request = {
            **_decision_args(view),
            "actor_id": EXPERIENCE_STEWARD_ID,
        }
        early = client.post("/api/workspace/experience/approve", json=request)
        assert early.status_code == 409
        assert early.json()["detail"]["code"] == (
            "EXPERIENCE_REVIEW_GATE_NOT_READY"
        )

        clock.now += 4
        approved = client.post("/api/workspace/experience/approve", json=request)
        assert approved.status_code == 200
        assert approved.json()["experience_governance"]["status"] == (
            "APPROVED_CANARY"
        )
        foreign = client.post(
            "/api/workspace/experience/reject",
            json={**request, "run_id": "run:foreign"},
        )
        assert foreign.status_code == 409
        exported = workspace.export_evidence()
        assert exported["experience_governance"]["status"] == "APPROVED_CANARY"
    workspace.close()


def test_terminal_apply_command_is_the_experience_generation_trigger(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "terminal-trigger.sqlite3")
    workspace.form_quote_with_dependency_evidence()

    launch_preview = workspace.preview_command("launch_date")
    launch_approval = workspace.approve_change(
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=launch_preview["preview_digest"],
    )
    workspace.apply_approved_change(
        "launch_date",
        approval_digest=launch_approval["approval_digest"],
    )
    currency_preview = workspace.preview_command("currency")
    currency_approval = workspace.approve_change(
        "currency",
        actor_id="human:finance-owner",
        preview_digest=currency_preview["preview_digest"],
    )
    evidence = _golden_evidence()
    workspace._competition_evidence_record = lambda: evidence  # type: ignore[method-assign]

    assert workspace.experience.view(str(evidence["run_id"]))["status"] == "NOT_RUN"
    workspace.apply_approved_change(
        "currency",
        approval_digest=currency_approval["approval_digest"],
    )
    assert workspace.state()["stage"] == "CURRENT"
    assert workspace.experience.view(str(evidence["run_id"]))["status"] == (
        "AWAITING_HUMAN_APPROVAL"
    )
    workspace.close()


def test_persisted_candidate_byte_tampering_is_detected(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tamper.sqlite3")
    service = GovernedExperienceService(store)
    evidence = _golden_evidence()
    service.ensure_candidate(evidence)
    artifact_id = next(
        row["artifact_id"]
        for row in store.connection.execute(
            "SELECT artifact_id FROM artifacts"
        ).fetchall()
        if row["artifact_id"].startswith("experience-candidate:")
    )
    store.connection.execute(
        "UPDATE artifacts SET payload_json=? WHERE artifact_id=?",
        ('{"forged":true}', artifact_id),
    )
    store.connection.commit()

    with pytest.raises(IntegrityError, match="ARTIFACT_DIGEST_MISMATCH"):
        service.view(str(evidence["run_id"]))
    store.close()
