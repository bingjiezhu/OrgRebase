from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

import rfc8785

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace.evolution_contracts import (
    AcceptedEvolutionSupport,
    EvolutionProfileSuccessor,
    EvolutionProofArtifact,
    EvolutionProposal,
    EvolutionSnapshotSuccessor,
    EvolutionSourceRevision,
    GovernanceDecision,
    GovernanceEvidenceMode,
    GovernedPromotion,
    HandlerContract,
    ProcedureContractCandidate,
    SourcePromotionReceipt,
    SourceRollbackReceipt,
)
from orgrebase.workspace.evolution_evidence import (
    CREATED_AT,
    _validate_evolution,
    build_promotion_source_admission,
    prepare_evolution_successors,
)
from orgrebase.workspace.oac_wire import OACBlackBoxCLI
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile

CANDIDATE_MEDIA_TYPE = "application/vnd.orgrebase.procedure-contract-candidate+json"
PROPOSAL_MEDIA_TYPE = "application/vnd.orgrebase.evolution-proposal+json"
GOVERNANCE_MEDIA_TYPE = "application/vnd.orgrebase.procedure-governance-decision+json"
EVOLUTION_SOURCE_MEDIA_TYPE = "application/vnd.orgrebase.evolution-source-revision+json"
PROFILE_SUCCESSOR_MEDIA_TYPE = "application/vnd.orgrebase.evolution-profile-successor+json"
SNAPSHOT_SUCCESSOR_MEDIA_TYPE = "application/vnd.orgrebase.evolution-snapshot-successor+json"
PROFILE_PAYLOAD_MEDIA_TYPE = "application/vnd.orgrebase.enterprise-seed-profile+json"
SNAPSHOT_PAYLOAD_MEDIA_TYPE = "application/vnd.oac.organization-snapshot+json"
PORTABLE_SOURCE_ADMISSION_MEDIA_TYPE = "application/vnd.oac.source-admission-receipt+json"
PROMOTION_MEDIA_TYPE = "application/vnd.orgrebase.source-promotion-receipt+json"
ROLLBACK_MEDIA_TYPE = "application/vnd.orgrebase.source-rollback-receipt+json"
PROOF_MEDIA_TYPE = "application/vnd.orgrebase.evolution-proof-artifact+json"
SOURCE_OBJECT_ID = "source:veracier:procedure-contracts"


def _metadata_id(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        raise IntegrityError("EVOLUTION_RESOURCE_METADATA_INVALID")
    return str(metadata["id"])


def _outcome_verdict(resource: dict[str, Any]) -> str:
    spec = resource.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("verdict"), str):
        raise IntegrityError("EVOLUTION_OUTCOME_SPEC_INVALID")
    return str(spec["verdict"])


def _paired_refs(resources: tuple[dict[str, Any], ...]) -> tuple[tuple[str, str], ...]:
    values = [(_metadata_id(item), str(item.get("digest"))) for item in resources]
    if any(not digest.startswith("sha256:") for _, digest in values):
        raise IntegrityError("EVOLUTION_OUTCOME_DIGEST_INVALID")
    return tuple(sorted(values, key=lambda item: item[0].encode()))


def _validated_proofs(
    candidate: ProcedureContractCandidate,
    replay: EvolutionProofArtifact,
    regression: EvolutionProofArtifact,
) -> tuple[EvolutionProofArtifact, EvolutionProofArtifact]:
    replay, regression = replay.revalidated(), regression.revalidated()
    if (replay.artifact_type, regression.artifact_type) != ("REPLAY_EVIDENCE", "REGRESSION_SUITE"):
        raise IntegrityError("EVOLUTION_PROOF_TYPE_INVALID")
    if {replay.subject_ref, regression.subject_ref} != {f"{candidate.candidate_id}@{candidate.digest}"}:
        raise IntegrityError("EVOLUTION_PROOF_SUBJECT_BINDING_INVALID")
    return replay, regression


class GovernedProcedureEvolution:
    def __init__(self, store: StateStore, cli: OACBlackBoxCLI) -> None:
        self.store = store
        self.cli = cli

    def _validate_snapshot(self, snapshot: dict[str, Any]) -> None:
        with tempfile.TemporaryDirectory(prefix="orgrebase-snapshot-successor-") as raw:
            path = Path(raw) / "snapshot.json"
            path.write_bytes(rfc8785.dumps(snapshot) + b"\n")
            self.cli.run("validate", str(path), "--verify-digest")

    @staticmethod
    def build_candidate(
        *,
        profile: EnterpriseSeedProfile,
        demand_digest: str,
        accepted: tuple[AcceptedEvolutionSupport, ...],
        rejected_outcomes: tuple[dict[str, Any], ...],
        handler_contracts: tuple[HandlerContract, ...],
        required_evidence_types: tuple[str, ...],
        created_at: str = "2026-08-26T00:03:00Z",
    ) -> ProcedureContractCandidate:
        profile = EnterpriseSeedProfile.model_validate(profile.model_dump(mode="json"))
        if len(accepted) < 2 or not rejected_outcomes:
            raise IntegrityError("EVOLUTION_SUPPORT_OR_COUNTEREXAMPLE_MISSING")
        if any(_outcome_verdict(item.outcome) != "ACCEPT" for item in accepted):
            raise IntegrityError("EVOLUTION_SUPPORT_OUTCOME_NOT_ACCEPT")
        if any(_outcome_verdict(item) != "REJECT" for item in rejected_outcomes):
            raise IntegrityError("EVOLUTION_COUNTEREXAMPLE_NOT_REJECT")
        topology_digests = {item.topology_digest for item in accepted}
        plan_digests = {item.execution.plan_digest for item in accepted}
        obligation_digests = {item.execution.obligation_contract_digest for item in accepted}
        runtime_owners = {item.execution.runtime_owner_id for item in accepted}
        if len(topology_digests) < 2 or len(plan_digests) < 2:
            raise IntegrityError("EVOLUTION_TOPOLOGY_DIVERSITY_MISSING")
        if len(obligation_digests) != 1 or len(runtime_owners) != 1:
            raise IntegrityError("EVOLUTION_SUPPORT_CONTRACT_MISMATCH")
        required_obligations = tuple(
            sorted({item.obligation_type for item in handler_contracts}, key=str.encode)
        )
        contracts = tuple(sorted(handler_contracts, key=lambda item: item.obligation_type.encode()))
        return ProcedureContractCandidate(
            candidate_id="procedure-candidate:veracier-sc008-controlled-process",
            predecessor_profile_ref=profile.ref,
            predecessor_profile_digest=profile.digest,
            demand_family="demand:SC-008",
            semantic_type="supplier.status",
            subject_type="supplier-alternative",
            source_preconditions=tuple(
                sorted(
                    {
                        f"profile-digest:{profile.digest}",
                        f"demand-digest:{demand_digest}",
                        "subject:alternative:acieries-savoie",
                        "effect-ceiling:zero-effect",
                    },
                    key=str.encode,
                )
            ),
            obligation_contract_digest=next(iter(obligation_digests)),
            required_obligation_types=required_obligations,
            required_evidence_types=tuple(sorted(set(required_evidence_types), key=str.encode)),
            handler_contracts=contracts,
            invariant_order_constraints=(),
            created_at=created_at,
        )

    @staticmethod
    def build_proposal(
        *,
        profile: EnterpriseSeedProfile,
        candidate: ProcedureContractCandidate,
        predecessor_source_ref: str,
        predecessor_source_digest: str,
        predecessor_snapshot: dict[str, Any],
        accepted: tuple[AcceptedEvolutionSupport, ...],
        rejected_outcomes: tuple[dict[str, Any], ...],
        replay_evidence: EvolutionProofArtifact,
        regression_evidence: EvolutionProofArtifact,
        declared_benefit: str,
        expires_at: str,
        proposal_author_id: str = "agent:procedure-candidate-builder",
        created_at: str = "2026-08-26T00:03:30Z",
        successor_published_at: str = "2026-08-26T00:05:00Z",
    ) -> EvolutionProposal:
        profile = EnterpriseSeedProfile.model_validate(profile.model_dump(mode="json"))
        candidate = candidate.revalidated()
        if (
            candidate.predecessor_profile_ref != profile.ref
            or candidate.predecessor_profile_digest != profile.digest
        ):
            raise IntegrityError("EVOLUTION_CANDIDATE_PROFILE_BINDING_MISMATCH")
        if len(accepted) < 2 or not rejected_outcomes:
            raise IntegrityError("EVOLUTION_SUPPORT_OR_COUNTEREXAMPLE_MISSING")
        if any(_outcome_verdict(item.outcome) != "ACCEPT" for item in accepted):
            raise IntegrityError("EVOLUTION_SUPPORT_OUTCOME_NOT_ACCEPT")
        if any(_outcome_verdict(item) != "REJECT" for item in rejected_outcomes):
            raise IntegrityError("EVOLUTION_COUNTEREXAMPLE_NOT_REJECT")
        support = _paired_refs(tuple(item.outcome for item in accepted))
        counterexamples = _paired_refs(rejected_outcomes)
        runtime_owners = {item.execution.runtime_owner_id for item in accepted}
        obligation_digests = {item.execution.obligation_contract_digest for item in accepted}
        topology_digests = {item.topology_digest for item in accepted}
        plan_digests = {item.execution.plan_digest for item in accepted}
        if len(topology_digests) < 2 or len(plan_digests) < 2:
            raise IntegrityError("EVOLUTION_TOPOLOGY_DIVERSITY_MISSING")
        if len(runtime_owners) != 1 or obligation_digests != {candidate.obligation_contract_digest}:
            raise IntegrityError("EVOLUTION_SUPPORT_CONTRACT_MISMATCH")
        replay_evidence, regression_evidence = _validated_proofs(
            candidate, replay_evidence, regression_evidence
        )
        snapshot_ref = _metadata_id(predecessor_snapshot)
        snapshot_digest = str(predecessor_snapshot.get("digest"))
        prepared = prepare_evolution_successors(
            profile=profile,
            predecessor_snapshot=predecessor_snapshot,
            predecessor_source_ref=predecessor_source_ref,
            predecessor_source_digest=predecessor_source_digest,
            candidate=candidate,
            published_at=successor_published_at,
        )
        return EvolutionProposal(
            proposal_id="evolution-proposal:veracier-sc008-controlled-process-v1",
            predecessor_source_ref=predecessor_source_ref,
            predecessor_source_digest=predecessor_source_digest,
            predecessor_profile_ref=profile.ref,
            predecessor_profile_digest=profile.digest,
            predecessor_snapshot_ref=snapshot_ref,
            predecessor_snapshot_digest=snapshot_digest,
            candidate_ref=candidate.candidate_id,
            candidate_digest=candidate.digest,
            prepared_successors=prepared,
            supporting_outcome_refs=tuple(ref for ref, _ in support),
            supporting_outcome_digests=tuple(digest for _, digest in support),
            counterexample_outcome_refs=tuple(ref for ref, _ in counterexamples),
            counterexample_outcome_digests=tuple(digest for _, digest in counterexamples),
            replay_evidence_ref=replay_evidence.artifact_id,
            replay_evidence_digest=replay_evidence.digest,
            regression_suite_ref=regression_evidence.artifact_id,
            regression_suite_digest=regression_evidence.digest,
            declared_benefit=declared_benefit,
            expires_at=expires_at,
            rollback_profile_ref=profile.ref,
            rollback_profile_digest=profile.digest,
            rollback_snapshot_ref=snapshot_ref,
            rollback_snapshot_digest=snapshot_digest,
            proposal_author_id=proposal_author_id,
            runtime_owner_id=next(iter(runtime_owners)),
            created_at=created_at,
        )

    @staticmethod
    def decide(
        proposal: EvolutionProposal,
        candidate: ProcedureContractCandidate,
        profile: EnterpriseSeedProfile,
        *,
        actor_id: str,
        actor_mode: GovernanceEvidenceMode,
        actor_authority_ref: str | None = None,
        verdict: Literal["ADMIT", "REJECT"] = "ADMIT",
        command_id: str = "promote-veracier-sc008-procedure-v1",
        decided_at: str = "2026-08-26T00:04:00Z",
    ) -> GovernanceDecision:
        proposal = proposal.revalidated()
        candidate = candidate.revalidated()
        profile = EnterpriseSeedProfile.model_validate(profile.model_dump(mode="json"))
        authority_ref = actor_authority_ref or actor_id
        if authority_ref not in profile.governance.admission_authority_refs:
            raise PermissionError("EVOLUTION_GOVERNANCE_AUTHORITY_MISMATCH")
        if proposal.expires_at <= decided_at:
            raise PermissionError("EVOLUTION_PROPOSAL_EXPIRED")
        if (proposal.candidate_ref, proposal.candidate_digest) != (candidate.candidate_id, candidate.digest):
            raise IntegrityError("GOVERNANCE_PROPOSAL_BINDING_MISMATCH")
        if (proposal.predecessor_profile_ref, proposal.predecessor_profile_digest) != (
            profile.ref,
            profile.digest,
        ):
            raise IntegrityError("GOVERNANCE_PREDECESSOR_BINDING_MISMATCH")
        return GovernanceDecision(
            decision_id=f"procedure-governance:{command_id}",
            proposal_ref=proposal.proposal_id,
            proposal_digest=proposal.digest,
            candidate_ref=candidate.candidate_id,
            candidate_digest=candidate.digest,
            predecessor_profile_ref=profile.ref,
            predecessor_profile_digest=profile.digest,
            actor_id=actor_id,
            actor_authority_ref=authority_ref,
            actor_mode=actor_mode,
            proposal_author_id=proposal.proposal_author_id,
            runtime_owner_id=proposal.runtime_owner_id,
            verdict=verdict,
            reason_codes=(
                "EXACT_PROPOSAL_AND_CANDIDATE_DIGESTS_REVIEWED",
                "SOURCE_AUTHORITY_SEPARATION_VERIFIED",
            ),
            decided_at=decided_at,
        )

    @staticmethod
    def _initial_source(
        profile: EnterpriseSeedProfile,
        snapshot_ref: str,
        snapshot_digest: str,
    ) -> EvolutionSourceRevision:
        return EvolutionSourceRevision(
            source_id=SOURCE_OBJECT_ID,
            revision="r1",
            profile_ref=profile.ref,
            profile_digest=profile.digest,
            snapshot_ref=snapshot_ref,
            snapshot_digest=snapshot_digest,
            admitted_at=CREATED_AT,
        )

    @staticmethod
    def _source_object(revision: EvolutionSourceRevision, state: ObjectState) -> VersionedObject:
        refs = (
            revision.profile_ref,
            revision.snapshot_ref,
            revision.predecessor_ref,
            revision.proposal_ref,
            revision.profile_successor_evidence_ref,
            revision.snapshot_successor_evidence_ref,
        )
        return VersionedObject(
            id=revision.source_id,
            version=revision.revision,
            kind="OAC_PROCEDURE_SOURCE",
            label="Veracier governed procedure source",
            domain="governance",
            state=state,
            payload={"source_revision": revision.model_dump(mode="json")},
            source_refs=tuple(ref for ref in refs if ref is not None),
        )

    def promote(
        self,
        *,
        profile: EnterpriseSeedProfile,
        predecessor_snapshot: dict[str, Any],
        candidate: ProcedureContractCandidate,
        proposal: EvolutionProposal,
        decision: GovernanceDecision,
        supporting_outcomes: tuple[dict[str, Any], ...],
        counterexample_outcomes: tuple[dict[str, Any], ...],
        replay_evidence: EvolutionProofArtifact,
        regression_evidence: EvolutionProofArtifact,
        promoted_at: str = "2026-08-26T00:05:00Z",
    ) -> GovernedPromotion:
        profile = EnterpriseSeedProfile.model_validate(profile.model_dump(mode="json"))
        candidate = candidate.revalidated()
        proposal = proposal.revalidated()
        decision = decision.revalidated()
        if decision.verdict != "ADMIT":
            raise PermissionError("EVOLUTION_GOVERNANCE_DECISION_NOT_ADMIT")
        if decision.actor_authority_ref not in profile.governance.admission_authority_refs:
            raise PermissionError("EVOLUTION_GOVERNANCE_AUTHORITY_MISMATCH")
        decision_binding = (
            decision.proposal_ref,
            decision.proposal_digest,
            decision.candidate_ref,
            decision.candidate_digest,
        )
        expected_decision = (proposal.proposal_id, proposal.digest, candidate.candidate_id, candidate.digest)
        if decision_binding != expected_decision:
            raise IntegrityError("EVOLUTION_GOVERNANCE_BINDING_MISMATCH")
        if (profile.ref, profile.digest) != (
            proposal.predecessor_profile_ref,
            proposal.predecessor_profile_digest,
        ):
            raise IntegrityError("EVOLUTION_PREDECESSOR_PROFILE_MISMATCH")
        if (_metadata_id(predecessor_snapshot), predecessor_snapshot.get("digest")) != (
            proposal.predecessor_snapshot_ref,
            proposal.predecessor_snapshot_digest,
        ):
            raise IntegrityError("EVOLUTION_PREDECESSOR_SNAPSHOT_MISMATCH")
        if proposal.expires_at <= promoted_at:
            raise PermissionError("EVOLUTION_PROPOSAL_EXPIRED")
        for outcome in (*supporting_outcomes, *counterexample_outcomes):
            _validate_evolution(self.cli, outcome)
        support = _paired_refs(supporting_outcomes)
        counterexamples = _paired_refs(counterexample_outcomes)
        expected_support = tuple(
            zip(proposal.supporting_outcome_refs, proposal.supporting_outcome_digests, strict=True)
        )
        expected_counterexamples = tuple(
            zip(proposal.counterexample_outcome_refs, proposal.counterexample_outcome_digests, strict=True)
        )
        if support != expected_support or counterexamples != expected_counterexamples:
            raise IntegrityError("EVOLUTION_OUTCOME_BINDING_MISMATCH")
        if any(_outcome_verdict(item) != "ACCEPT" for item in supporting_outcomes):
            raise IntegrityError("EVOLUTION_SUPPORT_OUTCOME_NOT_ACCEPT")
        if any(_outcome_verdict(item) != "REJECT" for item in counterexample_outcomes):
            raise IntegrityError("EVOLUTION_COUNTEREXAMPLE_NOT_REJECT")
        replay_evidence, regression_evidence = _validated_proofs(
            candidate, replay_evidence, regression_evidence
        )
        if (replay_evidence.artifact_id, replay_evidence.digest) != (
            proposal.replay_evidence_ref,
            proposal.replay_evidence_digest,
        ):
            raise IntegrityError("EVOLUTION_REPLAY_BINDING_MISMATCH")
        if (regression_evidence.artifact_id, regression_evidence.digest) != (
            proposal.regression_suite_ref,
            proposal.regression_suite_digest,
        ):
            raise IntegrityError("EVOLUTION_REGRESSION_BINDING_MISMATCH")
        initial = self._initial_source(
            profile, proposal.predecessor_snapshot_ref, proposal.predecessor_snapshot_digest
        )
        if (proposal.predecessor_source_ref, proposal.predecessor_source_digest) != (
            f"{SOURCE_OBJECT_ID}@r1",
            initial.digest,
        ):
            raise IntegrityError("EVOLUTION_PREDECESSOR_SOURCE_MISMATCH")

        prepared = proposal.prepared_successors.revalidated()
        expected_prepared = prepare_evolution_successors(
            profile=profile,
            predecessor_snapshot=predecessor_snapshot,
            predecessor_source_ref=proposal.predecessor_source_ref,
            predecessor_source_digest=proposal.predecessor_source_digest,
            candidate=candidate,
            published_at=promoted_at,
        )
        if prepared.digest != expected_prepared.digest:
            raise IntegrityError("EVOLUTION_PREPARED_SUCCESSOR_BYTES_MISMATCH")
        evolved_profile = prepared.profile_payload
        profile_successor = EvolutionProfileSuccessor(
            evidence_ref="profile-successor-evidence:veracier-procedure-r3",
            successor_ref=evolved_profile.ref,
            successor_digest=evolved_profile.digest,
            predecessor_ref=profile.ref,
            predecessor_digest=profile.digest,
            candidate_ref=candidate.candidate_id,
            candidate_digest=candidate.digest,
            proposal_ref=proposal.proposal_id,
            proposal_digest=proposal.digest,
            decision_ref=decision.decision_id,
            decision_digest=decision.digest,
            published_at=promoted_at,
            profile_payload=evolved_profile,
        )
        snapshot_payload = cast(dict[str, Any], json.loads(prepared.snapshot_payload_jcs))
        snapshot_metadata = cast(dict[str, Any], snapshot_payload["metadata"])
        self._validate_snapshot(snapshot_payload)
        snapshot_ref = f"{proposal.predecessor_snapshot_ref}@{snapshot_metadata['revision']}"
        snapshot_successor = EvolutionSnapshotSuccessor(
            evidence_ref="snapshot-successor-evidence:veracier-procedure-r3",
            successor_ref=snapshot_ref,
            successor_digest=str(snapshot_payload["digest"]),
            predecessor_ref=proposal.predecessor_snapshot_ref,
            predecessor_digest=proposal.predecessor_snapshot_digest,
            candidate_ref=candidate.candidate_id,
            candidate_digest=candidate.digest,
            proposal_ref=proposal.proposal_id,
            proposal_digest=proposal.digest,
            decision_ref=decision.decision_id,
            decision_digest=decision.digest,
            profile_successor_ref=evolved_profile.ref,
            profile_successor_digest=evolved_profile.digest,
            predecessor_source_refs=prepared.predecessor_source_refs,
            snapshot_payload_jcs=prepared.snapshot_payload_jcs,
            published_at=promoted_at,
        )

        portable = build_promotion_source_admission(
            candidate=candidate,
            governance_profile=profile,
            successor_profile=evolved_profile,
            snapshot_payload=snapshot_payload,
            proposal=proposal,
            decision=decision,
            support=support,
            counterexamples=counterexamples,
            replay_evidence=replay_evidence,
            predecessor_source_digest=initial.digest,
            created_at=promoted_at,
        )
        _validate_evolution(self.cli, portable)
        successor = EvolutionSourceRevision(
            source_id=SOURCE_OBJECT_ID,
            revision="r2",
            predecessor_ref=f"{SOURCE_OBJECT_ID}@r1",
            predecessor_digest=initial.digest,
            profile_ref=evolved_profile.ref,
            profile_digest=evolved_profile.digest,
            snapshot_ref=snapshot_ref,
            snapshot_digest=str(snapshot_payload["digest"]),
            profile_successor_evidence_ref=profile_successor.evidence_ref,
            profile_successor_evidence_digest=profile_successor.digest,
            snapshot_successor_evidence_ref=snapshot_successor.evidence_ref,
            snapshot_successor_evidence_digest=snapshot_successor.digest,
            proposal_ref=proposal.proposal_id,
            proposal_digest=proposal.digest,
            admitted_at=promoted_at,
        )
        promotion = SourcePromotionReceipt(
            receipt_id="source-promotion:veracier-procedure-r2",
            decision_ref=decision.decision_id,
            decision_digest=decision.digest,
            predecessor_ref=f"{SOURCE_OBJECT_ID}@r1",
            predecessor_digest=initial.digest,
            successor_ref=f"{SOURCE_OBJECT_ID}@r2",
            successor_digest=successor.digest,
            profile_successor_ref=evolved_profile.ref,
            profile_successor_digest=evolved_profile.digest,
            snapshot_successor_ref=snapshot_ref,
            snapshot_successor_digest=str(snapshot_payload["digest"]),
            profile_successor_evidence_ref=profile_successor.evidence_ref,
            profile_successor_evidence_digest=profile_successor.digest,
            snapshot_successor_evidence_ref=snapshot_successor.evidence_ref,
            snapshot_successor_evidence_digest=snapshot_successor.digest,
            portable_source_admission_ref=str(portable["metadata"]["id"]),
            portable_source_admission_digest=str(portable["digest"]),
            actor_mode=decision.actor_mode,
            human_review_status="EXPLICIT"
            if decision.actor_mode is GovernanceEvidenceMode.HUMAN_CLI_COMMAND
            else "NOT_RUN",
            promoted_at=promoted_at,
        )
        request_digest = sha256_digest(
            {
                "proposal_digest": proposal.digest,
                "decision_digest": decision.digest,
                "successor_digest": successor.digest,
                "portable_source_admission_digest": portable["digest"],
            }
        )
        key = "oac-evolution:promote:veracier-procedure-r2"
        existing = self.store.get_idempotent(key, request_digest)
        if existing is not None:
            return GovernedPromotion(
                portable_source_admission=cast(dict[str, Any], existing["portable"]),
                receipt=SourcePromotionReceipt.model_validate(existing["promotion"]),
                profile_successor=EvolutionProfileSuccessor.model_validate(existing["profile_successor"]),
                snapshot_successor=EvolutionSnapshotSuccessor.model_validate(existing["snapshot_successor"]),
                source_successor=EvolutionSourceRevision.model_validate(existing["source_successor"]),
            )
        with self.store.transaction() as connection:
            try:
                pointer = self.store.get_pointer(SOURCE_OBJECT_ID)
            except KeyError:
                self.store.insert_version(
                    connection,
                    self._source_object(initial, ObjectState.CURRENT),
                    make_current=True,
                )
                pointer = {"version": "r1"}
            if pointer["version"] != "r1":
                raise IntegrityError("EVOLUTION_PREDECESSOR_POINTER_DRIFT")
            self.store.insert_version(
                connection,
                self._source_object(successor, ObjectState.PROPOSED),
                make_current=False,
            )
            self.store.promote_version(connection, SOURCE_OBJECT_ID, "r1", "r2")
            artifacts = (
                (candidate.candidate_id, CANDIDATE_MEDIA_TYPE, candidate.model_dump(mode="json")),
                (proposal.proposal_id, PROPOSAL_MEDIA_TYPE, proposal.model_dump(mode="json")),
                (decision.decision_id, GOVERNANCE_MEDIA_TYPE, decision.model_dump(mode="json")),
                (profile.ref, PROFILE_PAYLOAD_MEDIA_TYPE, profile.model_dump(mode="json")),
                (
                    evolved_profile.ref,
                    PROFILE_PAYLOAD_MEDIA_TYPE,
                    evolved_profile.model_dump(mode="json"),
                ),
                (
                    snapshot_ref,
                    SNAPSHOT_PAYLOAD_MEDIA_TYPE,
                    snapshot_payload,
                ),
                (
                    profile_successor.evidence_ref,
                    PROFILE_SUCCESSOR_MEDIA_TYPE,
                    profile_successor.model_dump(mode="json"),
                ),
                (
                    snapshot_successor.evidence_ref,
                    SNAPSHOT_SUCCESSOR_MEDIA_TYPE,
                    snapshot_successor.model_dump(mode="json"),
                ),
                (f"{SOURCE_OBJECT_ID}@r1", EVOLUTION_SOURCE_MEDIA_TYPE, initial.model_dump(mode="json")),
                (f"{SOURCE_OBJECT_ID}@r2", EVOLUTION_SOURCE_MEDIA_TYPE, successor.model_dump(mode="json")),
                (str(portable["metadata"]["id"]), PORTABLE_SOURCE_ADMISSION_MEDIA_TYPE, portable),
                (
                    replay_evidence.artifact_id,
                    PROOF_MEDIA_TYPE,
                    replay_evidence.model_dump(mode="json"),
                ),
                (
                    regression_evidence.artifact_id,
                    PROOF_MEDIA_TYPE,
                    regression_evidence.model_dump(mode="json"),
                ),
                (promotion.receipt_id, PROMOTION_MEDIA_TYPE, promotion.model_dump(mode="json")),
            )
            for artifact_id, media_type, payload in artifacts:
                self.store.save_artifact(connection, artifact_id, media_type, payload)
            self.store.append_event(
                connection,
                "OAC_PROCEDURE_SOURCE_PROMOTED",
                {
                    "candidate_digest": candidate.digest,
                    "successor_digest": successor.digest,
                    "human_review_status": promotion.human_review_status,
                },
            )
            self.store.save_idempotent(
                connection,
                key,
                request_digest,
                {
                    "portable": portable,
                    "promotion": promotion.model_dump(mode="json"),
                    "profile_successor": profile_successor.model_dump(mode="json"),
                    "snapshot_successor": snapshot_successor.model_dump(mode="json"),
                    "source_successor": successor.model_dump(mode="json"),
                },
            )
        return GovernedPromotion(portable, promotion, profile_successor, snapshot_successor, successor)

    def rollback(
        self,
        *,
        actor_id: str,
        actor_mode: GovernanceEvidenceMode,
        actor_authority_ref: str,
        rolled_back_at: str = "2026-08-26T00:06:00Z",
    ) -> SourceRollbackReceipt:
        current = self.store.load_artifact(f"{SOURCE_OBJECT_ID}@r2", EVOLUTION_SOURCE_MEDIA_TYPE)
        restored = self.store.load_artifact(f"{SOURCE_OBJECT_ID}@r1", EVOLUTION_SOURCE_MEDIA_TYPE)
        promotion_artifact = self.store.load_artifact(
            "source-promotion:veracier-procedure-r2", PROMOTION_MEDIA_TYPE
        )
        current_revision = EvolutionSourceRevision.model_validate(current.payload)
        restored_revision = EvolutionSourceRevision.model_validate(restored.payload)
        promotion = SourcePromotionReceipt.model_validate(promotion_artifact.payload)
        evolved_profile = EnterpriseSeedProfile.model_validate(
            self.store.load_artifact(promotion.profile_successor_ref, PROFILE_PAYLOAD_MEDIA_TYPE).payload
        )
        restored_profile = EnterpriseSeedProfile.model_validate(
            self.store.load_artifact(restored_revision.profile_ref, PROFILE_PAYLOAD_MEDIA_TYPE).payload
        )
        proposal = EvolutionProposal.model_validate(
            self.store.load_artifact(str(current_revision.proposal_ref), PROPOSAL_MEDIA_TYPE).payload
        )
        allowed = set(evolved_profile.governance.admission_authority_refs) & set(
            restored_profile.governance.admission_authority_refs
        )
        if actor_authority_ref not in allowed:
            raise PermissionError("EVOLUTION_ROLLBACK_AUTHORITY_MISMATCH")
        snapshot_payload = self.store.load_artifact(
            promotion.snapshot_successor_ref, SNAPSHOT_PAYLOAD_MEDIA_TYPE
        ).payload
        self._validate_snapshot(snapshot_payload)
        profile_successor = EvolutionProfileSuccessor.model_validate(
            self.store.load_artifact(
                promotion.profile_successor_evidence_ref, PROFILE_SUCCESSOR_MEDIA_TYPE
            ).payload
        )
        snapshot_successor = EvolutionSnapshotSuccessor.model_validate(
            self.store.load_artifact(
                promotion.snapshot_successor_evidence_ref, SNAPSHOT_SUCCESSOR_MEDIA_TYPE
            ).payload
        )
        exact = (
            promotion.successor_digest == current_revision.digest
            and promotion.predecessor_digest == restored_revision.digest
            and current_revision.proposal_digest == proposal.digest
            and promotion.profile_successor_digest == evolved_profile.digest
            and promotion.snapshot_successor_digest == snapshot_payload.get("digest")
            and promotion.profile_successor_evidence_digest == profile_successor.digest
            and promotion.snapshot_successor_evidence_digest == snapshot_successor.digest
            and current_revision.profile_digest == evolved_profile.digest
            and restored_revision.profile_ref == restored_profile.ref
            and restored_revision.profile_digest == restored_profile.digest
            and current_revision.snapshot_digest == snapshot_payload.get("digest")
            and profile_successor.predecessor_ref == restored_profile.ref
            and profile_successor.predecessor_digest == restored_profile.digest
            and profile_successor.successor_digest == evolved_profile.digest
            and snapshot_successor.successor_digest == snapshot_payload.get("digest")
        )
        if not exact:
            raise IntegrityError("EVOLUTION_ROLLBACK_LINEAGE_MISMATCH")
        receipt = SourceRollbackReceipt(
            receipt_id="source-rollback:veracier-procedure-r2-to-r1",
            from_ref=f"{SOURCE_OBJECT_ID}@r2",
            from_digest=current_revision.digest,
            restored_ref=f"{SOURCE_OBJECT_ID}@r1",
            restored_digest=restored_revision.digest,
            promotion_receipt_ref=promotion.receipt_id,
            promotion_receipt_digest=promotion.digest,
            restored_profile_ref=restored_profile.ref,
            restored_profile_digest=restored_profile.digest,
            from_profile_ref=evolved_profile.ref,
            from_profile_digest=evolved_profile.digest,
            actor_id=actor_id,
            actor_authority_ref=actor_authority_ref,
            actor_mode=actor_mode,
            proposal_author_id=proposal.proposal_author_id,
            runtime_owner_id=proposal.runtime_owner_id,
            human_review_status=(
                "EXPLICIT" if actor_mode is GovernanceEvidenceMode.HUMAN_CLI_COMMAND else "NOT_RUN"
            ),
            rolled_back_at=rolled_back_at,
        )
        request_digest = sha256_digest(receipt.model_dump(mode="json"))
        key = "oac-evolution:rollback:veracier-procedure-r2-to-r1"
        existing = self.store.get_idempotent(key, request_digest)
        if existing is not None:
            return SourceRollbackReceipt.model_validate(existing)
        with self.store.transaction() as connection:
            pointer = self.store.get_pointer(SOURCE_OBJECT_ID)
            if pointer["version"] != "r2":
                raise IntegrityError("EVOLUTION_ROLLBACK_SOURCE_NOT_CURRENT")
            self.store.activate_version(
                connection,
                SOURCE_OBJECT_ID,
                "r2",
                "r1",
                base_state=ObjectState.SUPERSEDED,
                proposed_state=ObjectState.CURRENT,
            )
            self.store.save_artifact(
                connection, receipt.receipt_id, ROLLBACK_MEDIA_TYPE, receipt.model_dump(mode="json")
            )
            self.store.append_event(
                connection,
                "OAC_PROCEDURE_SOURCE_ROLLED_BACK",
                {
                    "from_digest": receipt.from_digest,
                    "restored_digest": receipt.restored_digest,
                },
            )
            self.store.save_idempotent(connection, key, request_digest, receipt.model_dump(mode="json"))
        return receipt
