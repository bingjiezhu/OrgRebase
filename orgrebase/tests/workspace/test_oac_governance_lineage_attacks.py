from __future__ import annotations

import json
from typing import Any

import pytest
import rfc8785

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.evolution_contracts import (
    EvolutionSnapshotSuccessor,
    EvolutionSourceRevision,
    GovernanceDecision,
    GovernanceEvidenceMode,
    ProcedureContractCandidate,
)
from orgrebase.workspace.evolution_evidence import _seal
from orgrebase.workspace.governed_evolution import (
    EVOLUTION_SOURCE_MEDIA_TYPE,
    SOURCE_OBJECT_ID,
    GovernedProcedureEvolution,
)
from tests.workspace.test_oac_governance_successors import (
    EvolutionFixtures,
    _decision,
    _promote,
    _proposal,
    evolution_fixtures,
    evolution_store,
)

_IMPORTED_FIXTURES = (evolution_fixtures, evolution_store)


def test_promotion_rejects_wrong_predecessor_source_digest(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    proposal = _proposal(
        evolution_fixtures,
        predecessor_source_digest="sha256:" + "9" * 64,
    )
    decision = _decision(evolution_fixtures, proposal)
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)

    with pytest.raises(IntegrityError, match="EVOLUTION_PREDECESSOR_SOURCE_MISMATCH"):
        _promote(service, evolution_fixtures, proposal, decision)

    assert evolution_store.event_records() == ()
    with pytest.raises(KeyError):
        evolution_store.get_pointer(SOURCE_OBJECT_ID)


def test_rollback_rejects_tampered_source_successor_digest(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)
    proposal = _proposal(evolution_fixtures)
    decision = _decision(evolution_fixtures, proposal)
    promoted = _promote(service, evolution_fixtures, proposal, decision)
    artifact = evolution_store.load_artifact(f"{SOURCE_OBJECT_ID}@r2", EVOLUTION_SOURCE_MEDIA_TYPE)
    payload: dict[str, Any] = dict(artifact.payload)
    payload.pop("digest")
    payload["snapshot_digest"] = "sha256:" + "8" * 64
    forged = EvolutionSourceRevision.model_validate(payload).model_dump(mode="json")
    evolution_store.connection.execute(
        "UPDATE artifacts SET payload_json=?, payload_digest=? WHERE artifact_id=?",
        (
            canonical_json(forged),
            sha256_digest(forged),
            f"{SOURCE_OBJECT_ID}@r2",
        ),
    )
    evolution_store.connection.commit()

    with pytest.raises(IntegrityError, match="EVOLUTION_ROLLBACK_LINEAGE_MISMATCH"):
        service.rollback(
            actor_id="human:veracier-shadow-owner",
            actor_mode=GovernanceEvidenceMode.HUMAN_CLI_COMMAND,
            actor_authority_ref="human:veracier-shadow-owner",
        )

    assert evolution_store.get_pointer(SOURCE_OBJECT_ID)["version"] == "r2"
    assert promoted.receipt.successor_digest != forged["digest"]
    assert [event["event_type"] for event in evolution_store.event_records()] == [
        "OAC_PROCEDURE_SOURCE_PROMOTED"
    ]


def test_scripted_governance_rejects_human_identity_label(
    evolution_fixtures: EvolutionFixtures,
) -> None:
    proposal = _proposal(evolution_fixtures)

    with pytest.raises(
        ValueError,
        match="GOVERNANCE_SCRIPT_IDENTITY_MISLABELED_HUMAN",
    ):
        _decision(
            evolution_fixtures,
            proposal,
            actor_id="human:veracier-shadow-owner",
            actor_mode=GovernanceEvidenceMode.SCRIPTED_GOVERNANCE_IDENTITY,
        )


def test_scripted_governance_is_never_labeled_human_review(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    proposal = _proposal(evolution_fixtures)
    decision = _decision(
        evolution_fixtures,
        proposal,
        actor_id="automation:veracier-governance-bot",
        actor_mode=GovernanceEvidenceMode.SCRIPTED_GOVERNANCE_IDENTITY,
        actor_authority_ref="human:veracier-shadow-owner",
    )
    promoted = _promote(
        GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli),
        evolution_fixtures,
        proposal,
        decision,
    )

    assert promoted.receipt.actor_mode is GovernanceEvidenceMode.SCRIPTED_GOVERNANCE_IDENTITY
    assert promoted.receipt.human_review_status == "NOT_RUN"
    assert promoted.portable_source_admission["metadata"]["ownerRef"] == ("human:veracier-shadow-owner")


def test_promotion_rechecks_actor_authority_against_predecessor_profile(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    proposal = _proposal(evolution_fixtures)
    payload = _decision(evolution_fixtures, proposal).model_dump(mode="json", exclude={"digest"})
    payload.update(
        actor_id="human:forged-outsider",
        actor_authority_ref="human:forged-outsider",
    )
    forged = GovernanceDecision.model_validate(payload)
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)

    with pytest.raises(PermissionError, match="EVOLUTION_GOVERNANCE_AUTHORITY_MISMATCH"):
        _promote(service, evolution_fixtures, proposal, forged)

    assert evolution_store.event_records() == ()
    with pytest.raises(KeyError):
        evolution_store.get_pointer(SOURCE_OBJECT_ID)


def test_rollback_requires_authority_retained_by_both_profiles(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)
    proposal = _proposal(evolution_fixtures)
    promoted = _promote(service, evolution_fixtures, proposal, _decision(evolution_fixtures, proposal))

    with pytest.raises(PermissionError, match="EVOLUTION_ROLLBACK_AUTHORITY_MISMATCH"):
        service.rollback(
            actor_id="human:forged-outsider",
            actor_mode=GovernanceEvidenceMode.HUMAN_CLI_COMMAND,
            actor_authority_ref="human:forged-outsider",
        )

    assert evolution_store.get_pointer(SOURCE_OBJECT_ID)["version"] == "r2"
    assert promoted.source_successor.revision == "r2"
    assert [item["event_type"] for item in evolution_store.event_records()] == [
        "OAC_PROCEDURE_SOURCE_PROMOTED"
    ]


def test_snapshot_successor_rejects_removed_candidate_semantic_fact(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)
    proposal = _proposal(evolution_fixtures)
    promoted = _promote(service, evolution_fixtures, proposal, _decision(evolution_fixtures, proposal))
    successor = promoted.snapshot_successor
    snapshot = json.loads(successor.snapshot_payload_jcs)
    snapshot["spec"]["nodes"] = [
        item for item in snapshot["spec"]["nodes"] if item["nodeId"] != successor.candidate_ref
    ]
    snapshot["spec"]["completeness"]["coveredNodeRefs"].remove(successor.candidate_ref)
    snapshot = _seal(snapshot)
    payload = successor.model_dump(mode="json", exclude={"digest"})
    payload["successor_digest"] = snapshot["digest"]
    payload["snapshot_payload_jcs"] = rfc8785.dumps(snapshot).decode("utf-8")

    with pytest.raises(ValueError, match="EVOLUTION_SNAPSHOT_CANDIDATE_FACT_MISSING"):
        EvolutionSnapshotSuccessor.model_validate(payload)


def test_candidate_rejects_authority_constraint_substitution(
    evolution_fixtures: EvolutionFixtures,
) -> None:
    payload = evolution_fixtures.candidate.model_dump(mode="json", exclude={"digest"})
    payload["authority_constraints"] = [
        "outcome-assurance!=runtime-execution",
        "outcome-assurance!=source-governance",
        "runtime-execution!=plan-digest:forged",
    ]

    with pytest.raises(ValueError, match="PROCEDURE_CANDIDATE_AUTHORITY_CONSTRAINTS_INVALID"):
        ProcedureContractCandidate.model_validate(payload)
