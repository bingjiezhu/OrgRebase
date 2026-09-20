from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, IntegrityError, RunEnvelope
from orgrebase.workspace.live_evidence import WorkspaceAgentTeamsEvidenceVerifier
from orgrebase.workspace.models import CoalitionPlan, TaskContextManifest
from orgrebase.workspace.transport import (
    LiveAgentTeamsTransport,
    WorkspaceTransportCompiler,
    agentteams_status,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_LOCK_PATH = ROOT / "agentteams/workspace/source-lock.json"


def _live_fixture(formed_service):
    task_receipt = formed_service.store.load_artifact("task-receipt:quote_acme@v1").payload
    plan = CoalitionPlan.model_validate(
        formed_service.store.load_artifact(task_receipt["coalition_plan_ref"]).payload
    )
    manifest = TaskContextManifest.model_validate(
        formed_service.store.load_artifact(task_receipt["context_manifest_ref"]).payload
    )
    projection_by_domain = {
        projection.actor_id.removesuffix("-steward"): projection.ref
        for projection in (
            __import__(
                "orgrebase.workspace.models", fromlist=["ActorContextProjection"]
            ).ActorContextProjection.model_validate(
                formed_service.store.load_artifact(ref).payload
            )
            for ref in manifest.actor_projection_refs
        )
        if projection.actor_id.endswith("-steward")
    }
    envelope = RunEnvelope(
        run_id="run:workspace:agentteams-live@1",
        nonce="a" * 64,
        issued_at="2026-08-15T00:00:00Z",
        expires_at="2026-08-15T01:00:00Z",
        mode="LIVE_AGENTTEAMS",
        evidence_class=EvidenceClass.LIVE_AGENTTEAMS,
    )
    delegations = WorkspaceTransportCompiler().compile(
        plan=plan,
        projection_refs_by_domain=projection_by_domain,
        run_envelope=envelope,
    )
    source_lock = json.loads(SOURCE_LOCK_PATH.read_text(encoding="utf-8"))
    room_id = "!workspace-room:example.invalid"
    workers = []
    events = []
    candidates = []
    providers = []
    for index, task in enumerate(delegations, start=1):
        matrix_user_id = f"@{task.worker_id}:example.invalid"
        provider_request_id = f"provider-request-{index}"
        candidate_payload = {
            "worker_id": task.worker_id,
            "delegation_task_digest": task.digest,
            "candidate_only": True,
            "run_id": envelope.run_id,
            "nonce": envelope.nonce,
        }
        candidate_digest = sha256_digest(candidate_payload)
        workers.append(
            {
                "worker_id": task.worker_id,
                "domain_id": task.domain_id,
                "uid": f"uid-{index}",
                "generation": 1,
                "matrix_user_id": matrix_user_id,
                "image_id": source_lock["runtime_images"]["worker"],
                "skill_digests": [source_lock["skill_digest"]],
                "workspace_digest": source_lock["workspace_contract_digest"],
            }
        )
        candidates.append(
            {
                "worker_id": task.worker_id,
                "ref": f"candidate:{task.worker_id}@1",
                "digest": candidate_digest,
                "bytes_digest": candidate_digest,
                "delegation_task_digest": task.digest,
                "candidate_only": True,
                "provider_request_id": provider_request_id,
                "run_id": envelope.run_id,
                "nonce": envelope.nonce,
            }
        )
        providers.append(
            {
                "worker_id": task.worker_id,
                "provider_request_id": provider_request_id,
                "model": "google/gemini-3.1-flash-lite",
                "candidate_digest": candidate_digest,
                "run_id": envelope.run_id,
                "nonce": envelope.nonce,
            }
        )
        events.append(
            {
                "worker_id": task.worker_id,
                "sender": matrix_user_id,
                "membership": "join",
                "event_id": f"$event-{index}",
                "room_id": room_id,
                "run_id": envelope.run_id,
                "nonce": envelope.nonce,
                "delegation_task_digest": task.digest,
                "candidate_artifact_digest": candidate_digest,
                "timestamp_ms": 1786770000000 + index,
            }
        )
    payload = {
        "schema_version": "orgrebase.workspace-agentteams-live.v1",
        "status": "LIVE_AGENTTEAMS",
        "evidence_class": "LIVE_AGENTTEAMS",
        "source_lock_digest": sha256_digest(source_lock),
        "run_envelope": envelope.model_dump(mode="json"),
        "coalition_plan": plan.model_dump(mode="json"),
        "coalition_plan_digest": plan.digest,
        "delegation_tasks": [item.model_dump(mode="json") for item in delegations],
        "team": {
            "name": "orgrebase-workspace-team",
            "uid": "team-uid-1",
            "generation": 1,
            "room_id": room_id,
        },
        "workers": workers,
        "matrix_events": events,
        "candidate_artifacts": candidates,
        "provider_calls": providers,
        "target_writes": 0,
    }
    return payload, source_lock, plan, delegations, envelope


def test_valid_live_evidence_correlates_every_plane(formed_service, tmp_path, monkeypatch) -> None:
    payload, source_lock, plan, delegations, envelope = _live_fixture(formed_service)
    result = WorkspaceAgentTeamsEvidenceVerifier().verify(
        payload=payload,
        source_lock=source_lock,
        plan=plan,
        delegations=delegations,
        run_envelope=envelope,
    )
    assert result["evidence_class"] == "LIVE_AGENTTEAMS"
    assert result["worker_count"] == 4
    assert result["transport_receipt"].target_writes == 0

    path = tmp_path / "live.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("ORGREBASE_AGENTTEAMS_EVIDENCE", str(path))
    candidates, receipt = LiveAgentTeamsTransport(
        source_lock_path=SOURCE_LOCK_PATH
    ).execute_delegations(
        plan=plan, delegations=delegations, run_envelope=envelope
    )
    assert len(candidates) == 4
    assert receipt.evidence_class == EvidenceClass.LIVE_AGENTTEAMS
    assert agentteams_status(source_lock_path=SOURCE_LOCK_PATH)["status"] == "LIVE_AGENTTEAMS"


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("spoof_sender", "MATRIX_BINDING_INVALID"),
        ("text_only_worker_mention", "MATRIX_BINDING_INVALID"),
        ("leave_membership", "MATRIX_BINDING_INVALID"),
        ("nonce_replay", "CANDIDATE_BINDING_INVALID"),
        ("artifact_mismatch", "CANDIDATE_BINDING_INVALID"),
        ("missing_skill", "WORKER_BINDING_INVALID"),
        ("missing_provider", "CORRELATION_SET_MISMATCH"),
        ("latest_tag", "WORKER_IMAGE_NOT_IMMUTABLE"),
        ("cross_run_splice", "PROVIDER_BINDING_INVALID"),
    ],
)
def test_live_evidence_red_team_rejects_splice_and_spoof(
    formed_service, mutation: str, error: str
) -> None:
    payload, source_lock, plan, delegations, envelope = _live_fixture(formed_service)
    payload = copy.deepcopy(payload)
    source_lock = copy.deepcopy(source_lock)
    if mutation == "spoof_sender":
        payload["matrix_events"][0]["sender"] = "@attacker:example.invalid"
    elif mutation == "text_only_worker_mention":
        payload["matrix_events"][0]["sender"] = "@attacker:example.invalid"
        payload["matrix_events"][0]["body"] = payload["workers"][0]["worker_id"]
    elif mutation == "leave_membership":
        payload["matrix_events"][0]["membership"] = "leave"
    elif mutation == "nonce_replay":
        payload["candidate_artifacts"][0]["nonce"] = "b" * 64
    elif mutation == "artifact_mismatch":
        payload["candidate_artifacts"][0]["bytes_digest"] = sha256_digest("tampered")
    elif mutation == "missing_skill":
        payload["workers"][0]["skill_digests"] = []
    elif mutation == "missing_provider":
        payload["provider_calls"].pop()
    elif mutation == "latest_tag":
        source_lock["runtime_images"]["worker"] = "registry.example/worker:latest"
        payload["source_lock_digest"] = sha256_digest(source_lock)
    elif mutation == "cross_run_splice":
        payload["provider_calls"][0]["run_id"] = "run:other"
    with pytest.raises(IntegrityError, match=error):
        WorkspaceAgentTeamsEvidenceVerifier().verify(
            payload=payload,
            source_lock=source_lock,
            plan=plan,
            delegations=delegations,
            run_envelope=envelope,
        )
