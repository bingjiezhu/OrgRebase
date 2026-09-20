from __future__ import annotations

from orgrebase.domain import RunEnvelope
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.models import CoalitionPlan
from orgrebase.workspace.transport import (
    LocalDeterministicTransport,
    WorkspaceTransportCompiler,
    agentteams_status,
)


def test_fixed_pool_transport_is_candidate_only_and_zero_write(workspace_service) -> None:
    prepared = workspace_service.formation.prepare_quote(
        WorkspaceFormationService.default_request()
    )
    coalition_write = next(
        item for item in prepared.artifact_writes if item.artifact_id.startswith("coalition:")
    )
    coalition = CoalitionPlan.model_validate(coalition_write.payload)
    projection_refs = {
        item.payload["actor_id"].removesuffix("-steward"): item.artifact_id
        for item in prepared.artifact_writes
        if item.media_type == "application/vnd.orgrebase.actor-context-projection+json"
        and item.payload["actor_id"].endswith("-steward")
    }
    envelope = RunEnvelope(
        run_id="run:transport@1",
        nonce="n" * 64,
        issued_at="2026-08-16T00:00:00Z",
        expires_at="2026-08-16T01:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class="LOCAL_DETERMINISTIC",
    )
    delegations = WorkspaceTransportCompiler().compile(
        plan=coalition,
        projection_refs_by_domain=projection_refs,
        run_envelope=envelope,
    )
    candidates, receipt = LocalDeterministicTransport().execute_delegations(
        plan=coalition,
        delegations=delegations,
        run_envelope=envelope,
    )
    assert len(delegations) == 4
    assert len(candidates) == 4
    assert all(item.candidate_only for item in delegations)
    assert receipt.target_writes == 0
    assert receipt.status == "PASS"


def test_live_transport_without_frozen_evidence_is_not_run(monkeypatch) -> None:
    monkeypatch.delenv("ORGREBASE_AGENTTEAMS_EVIDENCE", raising=False)
    status = agentteams_status()
    assert status["status"] == "NOT_RUN"
    assert status["evidence_class"] == "NOT_RUN"


def test_workspace_source_lock_matches_owned_contract_bytes() -> None:
    import json
    from pathlib import Path

    from orgrebase.workspace.source_lock import file_sha256, workspace_contract_digest

    root = Path(__file__).resolve().parents[2]
    lock = json.loads((root / "agentteams/workspace/source-lock.json").read_text())
    skill = root / "agentteams/workspace/skills/structured-domain-handoff/SKILL.md"
    assert lock["skill_name"] == "structured-domain-handoff"
    assert lock["skill_digest"] == file_sha256(skill)
    assert lock["workspace_contract_digest"] == workspace_contract_digest(
        team_asset=root / "agentteams/workspace/team.yaml",
        identities_dir=root / "agentteams/workspace/identities",
        skill_path=skill,
    )
