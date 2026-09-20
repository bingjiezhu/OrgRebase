from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import canonical_json
from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.evolution_contracts import (
    EvolutionProfileSuccessor,
    EvolutionProofArtifact,
    EvolutionProposal,
    EvolutionSnapshotSuccessor,
    EvolutionSourceRevision,
    ExecutionEvidence,
    GovernanceEvidenceMode,
    HandlerContract,
    ProcedureContractCandidate,
    SourcePromotionReceipt,
)
from orgrebase.workspace.evolution_evidence import (
    AdmittedLifecycleRoots,
    build_execution_approval,
    build_initial_lifecycle_roots,
    build_portable_outcome_certificate,
)
from orgrebase.workspace.evolution_runtime import ZeroEffectOACExecutor
from orgrebase.workspace.governed_evolution import (
    EVOLUTION_SOURCE_MEDIA_TYPE,
    PROFILE_PAYLOAD_MEDIA_TYPE,
    PROFILE_SUCCESSOR_MEDIA_TYPE,
    PROMOTION_MEDIA_TYPE,
    SNAPSHOT_PAYLOAD_MEDIA_TYPE,
    SNAPSHOT_SUCCESSOR_MEDIA_TYPE,
    SOURCE_OBJECT_ID,
    AcceptedEvolutionSupport,
    GovernedProcedureEvolution,
)
from orgrebase.workspace.models import OACRuntimeCapsule, RuntimeAdmissionReceipt
from orgrebase.workspace.oac_bridge import OACBlackBoxCLI, OACRuntimeAdmissionBridge
from orgrebase.workspace.outcome_assurance import (
    ControlledOutcomeObservationProducer,
    ControlledProcessOutcomeAssurance,
    controlled_reference_actual_values,
)
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile
from orgrebase.workspace.reference_profiles import supplier_sc008_source_aligned_profile

_REPLAY_REF = "replay:veracier-sc008-cross-topology"
_REGRESSION_REF = "regression-suite:veracier-sc008-controlled-process"
_DECLARED_BENEFIT = "Reuse one semantic procedure across independently compiled topologies."
_EXPIRES_AT = "2026-08-27T00:00:00Z"


@dataclass(frozen=True, slots=True)
class EvolutionFixtures:
    cli: OACBlackBoxCLI
    profile: EnterpriseSeedProfile
    roots: AdmittedLifecycleRoots
    capsules: dict[str, OACRuntimeCapsule]
    accepted: tuple[AcceptedEvolutionSupport, ...]
    rejected_outcome: dict[str, Any]
    handler_contracts: tuple[HandlerContract, ...]
    required_evidence_types: tuple[str, ...]
    candidate: ProcedureContractCandidate


def _admit(
    store: StateStore,
    capsule: OACRuntimeCapsule,
    *,
    command_id: str,
) -> tuple[RuntimeAdmissionReceipt, str]:
    bridge = OACRuntimeAdmissionBridge(store)
    preview = bridge.prepare(capsule)
    approval = bridge.approve(
        preview,
        actor_id=preview.runtime_owner_id,
        preview_digest=preview.digest,
        command_id=command_id,
        approved_at="2026-08-26T00:00:00Z",
    )
    return bridge.apply(capsule, preview, approval), preview.runtime_owner_id


def _execute_outcome(
    *,
    store: StateStore,
    cli: OACBlackBoxCLI,
    roots: AdmittedLifecycleRoots,
    capsule: OACRuntimeCapsule,
    admission: RuntimeAdmissionReceipt,
    runtime_owner_id: str,
    run_id: str,
    expired: bool = False,
) -> tuple[
    AcceptedEvolutionSupport,
    tuple[ExecutionEvidence, ...],
    dict[str, Any],
]:
    execution_approval = build_execution_approval(
        command_id=f"execute-{run_id}",
        capsule_digest=capsule.digest,
        runtime_admission_digest=admission.digest,
        source_admission=roots.oac_source_admission,
        demand=roots.demand,
        runtime_bundle_digest=capsule.runtime_bundle["digest"],
        runtime_owner_id=runtime_owner_id,
    )
    execution, evidence = ZeroEffectOACExecutor(store).execute(
        capsule,
        roots.demand,
        admission,
        execution_approval,
        source_admission=roots.oac_source_admission,
        run_id=run_id,
    )
    actual_values = controlled_reference_actual_values()
    if expired:
        actual_values["qualification-evidence-check"] = "qualification_record_expired"
    observation = ControlledOutcomeObservationProducer(store).record(
        execution,
        observation_id=f"outcome-observation:{run_id}",
        actual_values=actual_values,
    )
    _, decision = ControlledProcessOutcomeAssurance(store).evaluate(
        execution,
        evidence,
        observation,
        decision_id=f"outcome-decision:{run_id}",
    )
    outcome = build_portable_outcome_certificate(
        cli,
        capsule=capsule.model_dump(mode="json"),
        roots=roots,
        execution=execution,
        evidence=evidence,
        observation=observation,
        decision=decision,
    )
    return (
        AcceptedEvolutionSupport(
            outcome=outcome,
            execution=execution,
            topology_digest=capsule.runtime_bundle["spec"]["topologyDigest"],
        ),
        evidence,
        outcome,
    )


def _handler_contracts(
    accepted: tuple[AcceptedEvolutionSupport, ...],
    evidence_by_case: tuple[tuple[ExecutionEvidence, ...], ...],
) -> tuple[tuple[HandlerContract, ...], tuple[str, ...]]:
    evidence = {item.evidence_id: item for case_evidence in evidence_by_case for item in case_evidence}
    contracts: dict[str, HandlerContract] = {}
    for support in accepted:
        for step in support.execution.steps:
            for handler in step.handlers:
                contracts[handler.obligation_type] = HandlerContract(
                    obligation_type=handler.obligation_type,
                    handler_ref=handler.handler_ref,
                    handler_digest=handler.handler_digest,
                    capability_ref=handler.capability_ref,
                    evidence_types=tuple(
                        sorted(
                            {evidence[ref].evidence_type for ref in handler.evidence_refs},
                            key=str.encode,
                        )
                    ),
                )
    required_evidence = tuple(sorted({item.evidence_type for item in evidence.values()}, key=str.encode))
    return (
        tuple(sorted(contracts.values(), key=lambda item: item.obligation_type.encode())),
        required_evidence,
    )


@pytest.fixture(scope="module")
def evolution_fixtures(
    tmp_path_factory: pytest.TempPathFactory,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> EvolutionFixtures:
    capsules = current_oac_capsules
    cli = OACBlackBoxCLI()
    roots = build_initial_lifecycle_roots(cli, capsules["BASE"].snapshot, capsules["BASE"].change)
    profile = supplier_sc008_source_aligned_profile()
    store = StateStore(tmp_path_factory.mktemp("oac-governance-support") / "support.sqlite")
    try:
        admissions: dict[str, tuple[RuntimeAdmissionReceipt, str]] = {}
        accepted: list[AcceptedEvolutionSupport] = []
        accepted_evidence: list[tuple[ExecutionEvidence, ...]] = []
        for case in ("BASE", "SPLIT"):
            admissions[case] = _admit(store, capsules[case], command_id=f"support-{case.lower()}")
            support, evidence, outcome = _execute_outcome(
                store=store,
                cli=cli,
                roots=roots,
                capsule=capsules[case],
                admission=admissions[case][0],
                runtime_owner_id=admissions[case][1],
                run_id=f"governance-{case.lower()}",
            )
            assert outcome["spec"]["verdict"] == "ACCEPT"
            accepted.append(support)
            accepted_evidence.append(evidence)

        rejected_support, _, rejected_outcome = _execute_outcome(
            store=store,
            cli=cli,
            roots=roots,
            capsule=capsules["SPLIT"],
            admission=admissions["SPLIT"][0],
            runtime_owner_id=admissions["SPLIT"][1],
            run_id="governance-rejected",
            expired=True,
        )
        assert rejected_support.execution.status.value == "COMPLETED"
        assert rejected_outcome["spec"]["verdict"] == "REJECT"

        accepted_tuple = tuple(accepted)
        handler_contracts, required_evidence = _handler_contracts(
            accepted_tuple,
            tuple(accepted_evidence),
        )
        candidate = GovernedProcedureEvolution.build_candidate(
            profile=profile,
            demand_digest=roots.demand["digest"],
            accepted=accepted_tuple,
            rejected_outcomes=(rejected_outcome,),
            handler_contracts=handler_contracts,
            required_evidence_types=required_evidence,
        )
        return EvolutionFixtures(
            cli=cli,
            profile=profile,
            roots=roots,
            capsules=capsules,
            accepted=accepted_tuple,
            rejected_outcome=rejected_outcome,
            handler_contracts=handler_contracts,
            required_evidence_types=required_evidence,
            candidate=candidate,
        )
    finally:
        store.close()


@pytest.fixture
def evolution_store(tmp_path: Path) -> Iterator[StateStore]:
    store = StateStore(tmp_path / "oac-governance-successor.sqlite")
    try:
        yield store
    finally:
        store.close()


def _proposal(
    fixtures: EvolutionFixtures,
    *,
    candidate: ProcedureContractCandidate | None = None,
    accepted: tuple[AcceptedEvolutionSupport, ...] | None = None,
    rejected: tuple[dict[str, Any], ...] | None = None,
    predecessor_source_digest: str | None = None,
) -> EvolutionProposal:
    snapshot = fixtures.capsules["BASE"].snapshot
    selected_candidate = candidate or fixtures.candidate
    initial = GovernedProcedureEvolution._initial_source(
        fixtures.profile,
        snapshot["metadata"]["id"],
        snapshot["digest"],
    )
    subject_ref = f"{selected_candidate.candidate_id}@{selected_candidate.digest}"
    replay_evidence = EvolutionProofArtifact(
        artifact_id=_REPLAY_REF,
        artifact_type="REPLAY_EVIDENCE",
        subject_ref=subject_ref,
        source_refs=tuple(
            sorted(
                (f"{item.outcome['metadata']['id']}@{item.outcome['digest']}" for item in fixtures.accepted),
                key=str.encode,
            )
        ),
        assertions=("DISTINCT_TOPOLOGIES_SAME_OBLIGATION_CONTRACT",),
        created_at="2026-08-26T00:03:10Z",
    )
    regression_evidence = EvolutionProofArtifact(
        artifact_id=_REGRESSION_REF,
        artifact_type="REGRESSION_SUITE",
        subject_ref=subject_ref,
        source_refs=(f"{fixtures.rejected_outcome['metadata']['id']}@{fixtures.rejected_outcome['digest']}",),
        assertions=("COMPLETED_EXECUTION_CAN_BE_OUTCOME_REJECTED",),
        created_at="2026-08-26T00:03:11Z",
    )
    return GovernedProcedureEvolution.build_proposal(
        profile=fixtures.profile,
        candidate=selected_candidate,
        predecessor_source_ref=f"{SOURCE_OBJECT_ID}@r1",
        predecessor_source_digest=predecessor_source_digest or initial.digest,
        predecessor_snapshot=snapshot,
        accepted=fixtures.accepted if accepted is None else accepted,
        rejected_outcomes=(fixtures.rejected_outcome,) if rejected is None else rejected,
        replay_evidence=replay_evidence,
        regression_evidence=regression_evidence,
        declared_benefit=_DECLARED_BENEFIT,
        expires_at=_EXPIRES_AT,
    )


def _decision(
    fixtures: EvolutionFixtures,
    proposal: EvolutionProposal,
    *,
    candidate: ProcedureContractCandidate | None = None,
    actor_id: str = "human:veracier-shadow-owner",
    actor_mode: GovernanceEvidenceMode = GovernanceEvidenceMode.HUMAN_CLI_COMMAND,
    actor_authority_ref: str | None = None,
):
    return GovernedProcedureEvolution.decide(
        proposal,
        candidate or fixtures.candidate,
        fixtures.profile,
        actor_id=actor_id,
        actor_mode=actor_mode,
        actor_authority_ref=actor_authority_ref,
    )


def _promote(
    service: GovernedProcedureEvolution,
    fixtures: EvolutionFixtures,
    proposal: EvolutionProposal,
    decision: Any,
    *,
    candidate: ProcedureContractCandidate | None = None,
    supporting_outcomes: tuple[dict[str, Any], ...] | None = None,
    counterexample_outcomes: tuple[dict[str, Any], ...] | None = None,
):
    selected_candidate = candidate or fixtures.candidate
    subject_ref = f"{selected_candidate.candidate_id}@{selected_candidate.digest}"
    replay_evidence = EvolutionProofArtifact(
        artifact_id=_REPLAY_REF,
        artifact_type="REPLAY_EVIDENCE",
        subject_ref=subject_ref,
        source_refs=tuple(
            sorted(
                (f"{item.outcome['metadata']['id']}@{item.outcome['digest']}" for item in fixtures.accepted),
                key=str.encode,
            )
        ),
        assertions=("DISTINCT_TOPOLOGIES_SAME_OBLIGATION_CONTRACT",),
        created_at="2026-08-26T00:03:10Z",
    )
    regression_evidence = EvolutionProofArtifact(
        artifact_id=_REGRESSION_REF,
        artifact_type="REGRESSION_SUITE",
        subject_ref=subject_ref,
        source_refs=(f"{fixtures.rejected_outcome['metadata']['id']}@{fixtures.rejected_outcome['digest']}",),
        assertions=("COMPLETED_EXECUTION_CAN_BE_OUTCOME_REJECTED",),
        created_at="2026-08-26T00:03:11Z",
    )
    return service.promote(
        profile=fixtures.profile,
        predecessor_snapshot=fixtures.capsules["BASE"].snapshot,
        candidate=selected_candidate,
        proposal=proposal,
        decision=decision,
        supporting_outcomes=(
            tuple(item.outcome for item in fixtures.accepted)
            if supporting_outcomes is None
            else supporting_outcomes
        ),
        counterexample_outcomes=(
            (fixtures.rejected_outcome,) if counterexample_outcomes is None else counterexample_outcomes
        ),
        replay_evidence=replay_evidence,
        regression_evidence=regression_evidence,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_preconditions", "plan-digest:sha256:" + "1" * 64),
        ("invariant_order_constraints", "work-unit:a->work-unit:b"),
    ),
)
def test_procedure_candidate_rejects_fixed_plan_or_dag_leakage(
    evolution_fixtures: EvolutionFixtures,
    field: str,
    value: str,
) -> None:
    payload = evolution_fixtures.candidate.model_dump(mode="json")
    payload.pop("digest")
    payload[field] = sorted([*payload[field], value])

    with pytest.raises(ValueError, match="PROCEDURE_CANDIDATE_RUN_TOPOLOGY_LEAKAGE"):
        ProcedureContractCandidate.model_validate(payload)


def test_exact_governance_publishes_immutable_successors_and_rolls_back(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)
    profile_before = evolution_fixtures.profile.model_dump_json()
    snapshot_before = canonical_json(evolution_fixtures.capsules["BASE"].snapshot)
    proposal = _proposal(evolution_fixtures)
    decision = _decision(evolution_fixtures, proposal)

    promoted = _promote(service, evolution_fixtures, proposal, decision)
    replayed_promotion = _promote(service, evolution_fixtures, proposal, decision)

    snapshot_payload = json.loads(promoted.snapshot_successor.snapshot_payload_jcs)
    assert replayed_promotion.receipt.digest == promoted.receipt.digest
    assert promoted.receipt.profile_successor_digest == promoted.profile_successor.profile_payload.digest
    assert promoted.receipt.snapshot_successor_digest == snapshot_payload["digest"]
    assert promoted.receipt.successor_digest == promoted.source_successor.digest
    assert promoted.source_successor.profile_digest == promoted.profile_successor.profile_payload.digest
    assert promoted.source_successor.snapshot_digest == snapshot_payload["digest"]
    assert (
        promoted.receipt.profile_successor_evidence_digest
        == promoted.source_successor.profile_successor_evidence_digest
        == promoted.profile_successor.digest
    )
    assert (
        promoted.receipt.snapshot_successor_evidence_digest
        == promoted.source_successor.snapshot_successor_evidence_digest
        == promoted.snapshot_successor.digest
    )
    assert promoted.receipt.profile_successor_digest != promoted.profile_successor.digest
    assert promoted.receipt.snapshot_successor_digest != promoted.snapshot_successor.digest
    assert promoted.profile_successor.profile_payload.ref == promoted.profile_successor.successor_ref
    assert promoted.profile_successor.profile_payload.revision == "r3"
    assert snapshot_payload["metadata"]["revision"] == 3
    assert evolution_fixtures.profile.model_dump_json() == profile_before
    assert canonical_json(evolution_fixtures.capsules["BASE"].snapshot) == snapshot_before
    assert evolution_store.get_pointer(SOURCE_OBJECT_ID)["version"] == "r2"
    assert evolution_store.get_object(SOURCE_OBJECT_ID, "r1").state is ObjectState.SUPERSEDED
    assert evolution_store.get_object(SOURCE_OBJECT_ID, "r2").state is ObjectState.CURRENT
    assert (
        EnterpriseSeedProfile.model_validate(
            evolution_store.load_artifact(
                promoted.profile_successor.successor_ref, PROFILE_PAYLOAD_MEDIA_TYPE
            ).payload
        ).digest
        == promoted.profile_successor.profile_payload.digest
    )
    assert (
        evolution_store.load_artifact(
            promoted.snapshot_successor.successor_ref, SNAPSHOT_PAYLOAD_MEDIA_TYPE
        ).payload
        == snapshot_payload
    )
    assert (
        EvolutionProfileSuccessor.model_validate(
            evolution_store.load_artifact(
                promoted.profile_successor.evidence_ref, PROFILE_SUCCESSOR_MEDIA_TYPE
            ).payload
        ).digest
        == promoted.profile_successor.digest
    )
    assert (
        EvolutionSnapshotSuccessor.model_validate(
            evolution_store.load_artifact(
                promoted.snapshot_successor.evidence_ref, SNAPSHOT_SUCCESSOR_MEDIA_TYPE
            ).payload
        ).digest
        == promoted.snapshot_successor.digest
    )
    assert (
        EvolutionSourceRevision.model_validate(
            evolution_store.load_artifact(f"{SOURCE_OBJECT_ID}@r2", EVOLUTION_SOURCE_MEDIA_TYPE).payload
        ).digest
        == promoted.source_successor.digest
    )
    assert (
        SourcePromotionReceipt.model_validate(
            evolution_store.load_artifact(promoted.receipt.receipt_id, PROMOTION_MEDIA_TYPE).payload
        ).digest
        == promoted.receipt.digest
    )

    rollback = service.rollback(
        actor_id="human:veracier-shadow-owner",
        actor_mode=GovernanceEvidenceMode.HUMAN_CLI_COMMAND,
        actor_authority_ref="human:veracier-shadow-owner",
    )
    replayed_rollback = service.rollback(
        actor_id="human:veracier-shadow-owner",
        actor_mode=GovernanceEvidenceMode.HUMAN_CLI_COMMAND,
        actor_authority_ref="human:veracier-shadow-owner",
    )

    assert replayed_rollback.digest == rollback.digest
    assert rollback.from_digest == promoted.source_successor.digest
    assert rollback.promotion_receipt_digest == promoted.receipt.digest
    assert evolution_store.get_pointer(SOURCE_OBJECT_ID)["version"] == "r1"
    assert evolution_store.get_object(SOURCE_OBJECT_ID, "r1").state is ObjectState.CURRENT
    assert evolution_store.get_object(SOURCE_OBJECT_ID, "r2").state is ObjectState.SUPERSEDED
    assert [event["event_type"] for event in evolution_store.event_records()] == [
        "OAC_PROCEDURE_SOURCE_PROMOTED",
        "OAC_PROCEDURE_SOURCE_ROLLED_BACK",
    ]


@pytest.mark.parametrize("actor_field", ("proposal_author_id", "runtime_owner_id"))
def test_governance_rejects_self_or_runtime_owner_approval(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
    actor_field: str,
) -> None:
    proposal = _proposal(evolution_fixtures)

    with pytest.raises(ValueError, match="GOVERNANCE_SEPARATION_OF_DUTIES_VIOLATION"):
        _decision(
            evolution_fixtures,
            proposal,
            actor_id=str(getattr(proposal, actor_field)),
            actor_authority_ref="human:veracier-shadow-owner",
        )

    assert evolution_store.event_records() == ()


def test_governance_rejects_wrong_candidate_and_proposal_digests(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    proposal = _proposal(evolution_fixtures)
    candidate_payload = evolution_fixtures.candidate.model_dump(mode="json", exclude={"digest"})
    candidate_payload["created_at"] = "2026-08-26T00:03:01Z"
    other_candidate = ProcedureContractCandidate.model_validate(candidate_payload)
    with pytest.raises(IntegrityError, match="GOVERNANCE_PROPOSAL_BINDING_MISMATCH"):
        _decision(evolution_fixtures, proposal, candidate=other_candidate)

    decision = _decision(evolution_fixtures, proposal)
    proposal_payload = proposal.model_dump(mode="json", exclude={"digest"})
    proposal_payload["declared_benefit"] = "A different proposal identity."
    other_proposal = EvolutionProposal.model_validate(proposal_payload)
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)
    with pytest.raises(IntegrityError, match="EVOLUTION_GOVERNANCE_BINDING_MISMATCH"):
        _promote(service, evolution_fixtures, other_proposal, decision)
    assert evolution_store.event_records() == ()


def test_rejected_outcome_cannot_be_support_and_counterexample_cannot_be_omitted(
    evolution_fixtures: EvolutionFixtures,
    evolution_store: StateStore,
) -> None:
    rejected_support = replace(evolution_fixtures.accepted[0], outcome=evolution_fixtures.rejected_outcome)
    with pytest.raises(IntegrityError, match="EVOLUTION_SUPPORT_OUTCOME_NOT_ACCEPT"):
        _proposal(
            evolution_fixtures,
            accepted=(rejected_support, evolution_fixtures.accepted[1]),
        )
    with pytest.raises(IntegrityError, match="EVOLUTION_SUPPORT_OR_COUNTEREXAMPLE_MISSING"):
        _proposal(evolution_fixtures, rejected=())

    proposal = _proposal(evolution_fixtures)
    decision = _decision(evolution_fixtures, proposal)
    service = GovernedProcedureEvolution(evolution_store, evolution_fixtures.cli)
    with pytest.raises(IntegrityError, match="EVOLUTION_OUTCOME_BINDING_MISMATCH"):
        _promote(
            service,
            evolution_fixtures,
            proposal,
            decision,
            counterexample_outcomes=(),
        )
    assert evolution_store.event_records() == ()
