"""Runtime-owned contracts for the bounded, authority-separated OAC evolution slice."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUN_TOPOLOGY_TOKENS = (
    "plan-digest",
    "plan_digest",
    "topology-digest",
    "topology_digest",
    "work-unit",
    "work_unit",
    "case-id",
    "case_id",
    "fixed-dag",
    "fixed_dag",
)
EVOLUTION_SOURCE_CHANGED_PATHS = (
    "/admitted_at",
    "/predecessor_digest",
    "/predecessor_ref",
    "/profile_digest",
    "/profile_ref",
    "/revision",
    "/snapshot_digest",
    "/snapshot_ref",
)
PROCEDURE_AUTHORITY_CONSTRAINTS = (
    "outcome-assurance!=runtime-execution",
    "outcome-assurance!=source-governance",
    "runtime-execution!=source-governance",
)


def _require_digest(value: str, code: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(code)


def _require_digests(values: tuple[str, ...], code: str) -> None:
    for value in values:
        _require_digest(value, code)


def _require_content_digests(record: Any, code: str) -> None:
    values = tuple(value for name, value in vars(record).items() if name.endswith("_digest"))
    _require_digests(values, code)


def _canonical_tuple(values: tuple[str, ...], code: str, *, allow_empty: bool = False) -> None:
    if (not values and not allow_empty) or values != tuple(sorted(set(values), key=str.encode)):
        raise ValueError(code)


class ExecutionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class OutcomeVerdict(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    PROVISIONAL = "PROVISIONAL"
    UNKNOWN = "UNKNOWN"


class GovernanceEvidenceMode(StrEnum):
    HUMAN_CLI_COMMAND = "HUMAN_CLI_COMMAND"
    SCRIPTED_GOVERNANCE_IDENTITY = "SCRIPTED_GOVERNANCE_IDENTITY"


def governance_actor_error(record: Any) -> str | None:
    if record.actor_id in {record.proposal_author_id, record.runtime_owner_id}:
        return "GOVERNANCE_SEPARATION_OF_DUTIES_VIOLATION"
    if record.actor_mode is GovernanceEvidenceMode.HUMAN_CLI_COMMAND:
        if not record.actor_id.startswith("human:"):
            return "GOVERNANCE_HUMAN_IDENTITY_REQUIRED"
        if record.actor_id != record.actor_authority_ref:
            return "GOVERNANCE_ACTOR_AUTHORITY_BINDING_MISMATCH"
    elif not record.actor_id.startswith(("scripted:", "automation:")):
        return "GOVERNANCE_SCRIPT_IDENTITY_MISLABELED_HUMAN"
    return None


class EvolutionProofArtifact(ContentAddressedModel):
    schema_version: Literal["orgrebase.evolution-proof-artifact.v1"] = "orgrebase.evolution-proof-artifact.v1"
    artifact_id: str = Field(min_length=1)
    artifact_type: Literal["REPLAY_EVIDENCE", "REGRESSION_SUITE"]
    subject_ref: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    assertions: tuple[str, ...] = Field(min_length=1)
    status: Literal["PASS"] = "PASS"
    created_at: str = Field(min_length=1)


class ExecutionEvidence(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-execution-evidence.v1"] = "orgrebase.oac-execution-evidence.v1"
    evidence_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    obligation_ref: str = Field(min_length=1)
    obligation_type: str = Field(min_length=1)
    work_unit_ref: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    plan_digest: str
    handler_ref: str = Field(min_length=1)
    handler_digest: str
    program_digest: str
    assertion: str = Field(min_length=1)
    observed_value: str = Field(min_length=1)
    source_refs: tuple[str, ...]
    producer_id: str = Field(min_length=1)
    produced_at: str = Field(min_length=1)
    external_effects: Literal["NONE"] = "NONE"
    target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        _require_digest(self.plan_digest, "EXECUTION_EVIDENCE_PLAN_DIGEST_INVALID")
        _require_digest(self.handler_digest, "EXECUTION_EVIDENCE_HANDLER_DIGEST_INVALID")
        _require_digest(self.program_digest, "EXECUTION_EVIDENCE_PROGRAM_DIGEST_INVALID")
        _canonical_tuple(self.source_refs, "EXECUTION_EVIDENCE_SOURCE_REFS_INVALID")
        return self


class ExecutedHandler(ContentAddressedModel):
    obligation_ref: str = Field(min_length=1)
    obligation_type: str = Field(min_length=1)
    handler_ref: str = Field(min_length=1)
    handler_digest: str
    program_digest: str
    capability_ref: str = Field(min_length=1)
    evidence_refs: tuple[str, ...]
    evidence_digests: tuple[str, ...]

    @model_validator(mode="after")
    def validate_handler(self) -> Self:
        _require_digest(self.handler_digest, "EXECUTED_HANDLER_DIGEST_INVALID")
        _require_digest(self.program_digest, "EXECUTED_PROGRAM_DIGEST_INVALID")
        _canonical_tuple(self.evidence_refs, "EXECUTED_HANDLER_EVIDENCE_REFS_INVALID")
        if len(self.evidence_refs) != len(self.evidence_digests):
            raise ValueError("EXECUTED_HANDLER_EVIDENCE_BINDING_INVALID")
        for digest in self.evidence_digests:
            _require_digest(digest, "EXECUTED_HANDLER_EVIDENCE_DIGEST_INVALID")
        return self


class ExecutionStepReceipt(ContentAddressedModel):
    step_index: int = Field(ge=0)
    work_unit_ref: str = Field(min_length=1)
    predecessor_refs: tuple[str, ...]
    handlers: tuple[ExecutedHandler, ...]
    evidence_refs: tuple[str, ...]
    status: ExecutionStatus
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_step(self) -> Self:
        _canonical_tuple(self.predecessor_refs, "EXECUTION_STEP_PREDECESSORS_INVALID", allow_empty=True)
        _canonical_tuple(self.evidence_refs, "EXECUTION_STEP_EVIDENCE_REFS_INVALID")
        projected = tuple(
            sorted(
                {ref for handler in self.handlers for ref in handler.evidence_refs},
                key=str.encode,
            )
        )
        if projected != self.evidence_refs:
            raise ValueError("EXECUTION_STEP_EVIDENCE_PROJECTION_MISMATCH")
        return self


class OACExecutionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-execution-receipt.v1"] = "orgrebase.oac-execution-receipt.v1"
    receipt_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    snapshot_ref: str = Field(min_length=1)
    snapshot_digest: str
    source_admission_ref: str = Field(min_length=1)
    source_admission_digest: str
    demand_ref: str = Field(min_length=1)
    demand_digest: str
    change_ref: str = Field(min_length=1)
    change_digest: str
    plan_ref: str = Field(min_length=1)
    plan_digest: str
    certificate_digest: str
    runtime_binding_digest: str
    runtime_bundle_digest: str
    root_closure_digest: str
    obligation_contract_digest: str
    evidence_projection_digest: str
    runtime_owner_id: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)
    executor_build: str = Field(min_length=1)
    status: ExecutionStatus
    steps: tuple[ExecutionStepReceipt, ...]
    evidence_refs: tuple[str, ...]
    evidence_digests: tuple[str, ...]
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    external_effects: Literal["NONE"] = "NONE"
    target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        _require_content_digests(self, "EXECUTION_RECEIPT_DIGEST_INVALID")
        if not self.steps:
            raise ValueError("EXECUTION_RECEIPT_STEPS_MISSING")
        if tuple(step.step_index for step in self.steps) != tuple(range(len(self.steps))):
            raise ValueError("EXECUTION_RECEIPT_STEP_ORDER_INVALID")
        if len({step.work_unit_ref for step in self.steps}) != len(self.steps):
            raise ValueError("EXECUTION_RECEIPT_DUPLICATE_WORK_UNIT")
        if self.status is ExecutionStatus.COMPLETED and any(
            step.status is not ExecutionStatus.COMPLETED for step in self.steps
        ):
            raise ValueError("EXECUTION_RECEIPT_INCOMPLETE_STEP")
        _canonical_tuple(self.evidence_refs, "EXECUTION_RECEIPT_EVIDENCE_REFS_INVALID")
        if len(self.evidence_refs) != len(self.evidence_digests):
            raise ValueError("EXECUTION_RECEIPT_EVIDENCE_BINDING_INVALID")
        projected = tuple(sorted({ref for step in self.steps for ref in step.evidence_refs}, key=str.encode))
        if projected != self.evidence_refs:
            raise ValueError("EXECUTION_RECEIPT_EVIDENCE_PROJECTION_INVALID")
        if self.evidence_projection_digest != sha256_digest(
            [
                {"ref": ref, "digest": digest}
                for ref, digest in zip(self.evidence_refs, self.evidence_digests, strict=True)
            ]
        ):
            raise ValueError("EXECUTION_RECEIPT_EVIDENCE_DIGEST_MISMATCH")
        return self


class OACExecutionApproval(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-execution-approval.v1"] = "orgrebase.oac-execution-approval.v1"
    approval_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    capsule_digest: str
    runtime_admission_digest: str
    source_admission_ref: str = Field(min_length=1)
    source_admission_digest: str
    demand_ref: str = Field(min_length=1)
    demand_digest: str
    runtime_bundle_digest: str
    program_policy_digest: str
    runtime_owner_id: str = Field(min_length=1)
    scope: Literal["ZERO_EFFECT_HANDLER_EXECUTION"] = "ZERO_EFFECT_HANDLER_EXECUTION"
    target_writes: Literal[0] = 0
    approved_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_approval(self) -> Self:
        approval_digests = (
            self.capsule_digest,
            self.runtime_admission_digest,
            self.source_admission_digest,
            self.demand_digest,
            self.runtime_bundle_digest,
            self.program_policy_digest,
        )
        _require_digests(approval_digests, "EXECUTION_APPROVAL_DIGEST_INVALID")
        if self.approval_id != f"oac-execution-approval:{self.command_id}":
            raise ValueError("EXECUTION_APPROVAL_ID_COMMAND_MISMATCH")
        return self


class OutcomeFact(ContentAddressedModel):
    fact_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    state: Literal["KNOWN", "UNKNOWN", "NOT_OBSERVABLE"]
    value: str | None
    evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_fact(self) -> Self:
        if (self.state == "KNOWN") != (self.value is not None):
            raise ValueError("OUTCOME_FACT_STATE_VALUE_MISMATCH")
        _canonical_tuple(
            self.evidence_refs,
            "OUTCOME_FACT_EVIDENCE_REFS_INVALID",
            allow_empty=self.state != "KNOWN",
        )
        return self


class OutcomeObservation(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-outcome-observation.v1"] = "orgrebase.oac-outcome-observation.v1"
    observation_id: str = Field(min_length=1)
    observation_profile: Literal["oac.supplier.sc008.synthetic-oracle/v1"]
    subject_ref: Literal["alternative:acieries-savoie"]
    demand_ref: Literal["demand:SC-008"]
    execution_receipt_ref: str = Field(min_length=1)
    execution_receipt_digest: str
    facts: tuple[OutcomeFact, ...]
    producer_id: str = Field(min_length=1)
    producer_authority_ref: str = Field(min_length=1)
    produced_at: str = Field(min_length=1)
    evidence_class: Literal["SYNTHETIC_CONTROLLED_OBSERVATION"] = "SYNTHETIC_CONTROLLED_OBSERVATION"

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        _require_digest(self.execution_receipt_digest, "OUTCOME_OBSERVATION_EXECUTION_DIGEST_INVALID")
        names = tuple(fact.name for fact in self.facts)
        if names != tuple(sorted(set(names), key=str.encode)):
            raise ValueError("OUTCOME_OBSERVATION_FACTS_INVALID")
        if any(fact.state == "KNOWN" and fact.evidence_refs != (self.observation_id,) for fact in self.facts):
            raise ValueError("OUTCOME_OBSERVATION_FACT_SOURCE_INVALID")
        return self


class OutcomeDimension(ContentAddressedModel):
    name: str = Field(min_length=1)
    verdict: Literal["PASS", "FAIL", "UNKNOWN"]
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_dimension(self) -> Self:
        _canonical_tuple(self.reason_codes, "OUTCOME_DIMENSION_REASONS_INVALID")
        _canonical_tuple(
            self.evidence_refs, "OUTCOME_DIMENSION_EVIDENCE_INVALID", allow_empty=self.verdict == "UNKNOWN"
        )
        return self


class LocalOutcomeDecision(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-outcome-decision.v1"] = "orgrebase.oac-outcome-decision.v1"
    decision_id: str = Field(min_length=1)
    observation_ref: str = Field(min_length=1)
    observation_digest: str
    execution_receipt_ref: str = Field(min_length=1)
    execution_receipt_digest: str
    verdict: OutcomeVerdict
    dimensions: tuple[OutcomeDimension, ...]
    reason_codes: tuple[str, ...]
    oracle_id: str = Field(min_length=1)
    oracle_build: str = Field(min_length=1)
    issuer_authority_ref: str = Field(min_length=1)
    issued_at: str = Field(min_length=1)
    target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        _require_digest(self.observation_digest, "OUTCOME_DECISION_OBSERVATION_DIGEST_INVALID")
        _require_digest(self.execution_receipt_digest, "OUTCOME_DECISION_EXECUTION_DIGEST_INVALID")
        _canonical_tuple(self.reason_codes, "OUTCOME_DECISION_REASONS_INVALID", allow_empty=True)
        verdicts = {dimension.verdict for dimension in self.dimensions}
        expected = (
            OutcomeVerdict.REJECT
            if "FAIL" in verdicts
            else OutcomeVerdict.UNKNOWN
            if "UNKNOWN" in verdicts
            else OutcomeVerdict.ACCEPT
        )
        if self.verdict is not expected:
            raise ValueError("OUTCOME_DECISION_VERDICT_DIMENSION_MISMATCH")
        return self


class HandlerContract(ContentAddressedModel):
    obligation_type: str = Field(min_length=1)
    handler_ref: str = Field(min_length=1)
    handler_digest: str
    capability_ref: str = Field(min_length=1)
    evidence_types: tuple[str, ...]

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _require_digest(self.handler_digest, "PROCEDURE_HANDLER_DIGEST_INVALID")
        _canonical_tuple(self.evidence_types, "PROCEDURE_HANDLER_EVIDENCE_INVALID")
        return self


class ProcedureContractCandidate(ContentAddressedModel):
    schema_version: Literal["orgrebase.procedure-contract-candidate.v1"] = (
        "orgrebase.procedure-contract-candidate.v1"
    )
    candidate_id: str = Field(min_length=1)
    version: Literal["v1"] = "v1"
    predecessor_profile_ref: str = Field(min_length=1)
    predecessor_profile_digest: str
    demand_family: Literal["demand:SC-008"]
    semantic_type: Literal["supplier.status"]
    subject_type: Literal["supplier-alternative"]
    source_preconditions: tuple[str, ...]
    obligation_contract_digest: str
    required_obligation_types: tuple[str, ...]
    required_evidence_types: tuple[str, ...]
    handler_contracts: tuple[HandlerContract, ...]
    invariant_order_constraints: tuple[str, ...]
    authority_constraints: tuple[str, str, str] = PROCEDURE_AUTHORITY_CONSTRAINTS
    transfer_ceiling: Literal["EXACT_SC008_SYNTHETIC_PROFILE_ONLY"] = "EXACT_SC008_SYNTHETIC_PROFILE_ONLY"
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        _require_digests(
            (self.predecessor_profile_digest, self.obligation_contract_digest),
            "PROCEDURE_CANDIDATE_DIGEST_INVALID",
        )
        canonical_fields = (
            (self.source_preconditions, "PROCEDURE_SOURCE_PRECONDITIONS_INVALID"),
            (self.required_obligation_types, "PROCEDURE_OBLIGATIONS_INVALID"),
            (self.required_evidence_types, "PROCEDURE_EVIDENCE_INVALID"),
        )
        for values, code in canonical_fields:
            _canonical_tuple(values, code)
        _canonical_tuple(self.invariant_order_constraints, "PROCEDURE_ORDER_INVALID", allow_empty=True)
        leaked = "\n".join((*self.source_preconditions, *self.invariant_order_constraints)).lower()
        if any(token in leaked for token in _RUN_TOPOLOGY_TOKENS):
            raise ValueError("PROCEDURE_CANDIDATE_RUN_TOPOLOGY_LEAKAGE")
        if self.authority_constraints != PROCEDURE_AUTHORITY_CONSTRAINTS:
            raise ValueError("PROCEDURE_CANDIDATE_AUTHORITY_CONSTRAINTS_INVALID")
        return self


def _validate_candidate_snapshot(record: Any) -> dict[str, Any]:
    payload = json.loads(record.snapshot_payload_jcs)
    if not isinstance(payload, dict):
        raise ValueError("EVOLUTION_SNAPSHOT_SUCCESSOR_PAYLOAD_INVALID")
    metadata = payload.get("metadata", {})
    spec = payload.get("spec", {})
    candidate_source = f"{record.candidate_ref}@{record.candidate_digest}"
    if tuple(metadata.get("sourceRefs", ())) != (*record.predecessor_source_refs, candidate_source):
        raise ValueError("EVOLUTION_SNAPSHOT_CANDIDATE_PROVENANCE_INVALID")
    expected_node = {
        "nodeId": record.candidate_ref,
        "nodeType": "procedure-contract",
        "domainRef": "domain:quality",
        "ownerRoleRef": "role:quality-qualification",
        "admissionStatus": "admitted",
    }
    covered = spec.get("completeness", {}).get("coveredNodeRefs", ())
    if spec.get("nodes", []).count(expected_node) != 1 or record.candidate_ref not in covered:
        raise ValueError("EVOLUTION_SNAPSHOT_CANDIDATE_FACT_MISSING")
    return payload


class PreparedEvolutionSuccessors(ContentAddressedModel):
    predecessor_source_refs: tuple[str, ...] = Field(min_length=1)
    candidate_ref: str = Field(min_length=1)
    candidate_digest: str
    profile_payload: EnterpriseSeedProfile
    snapshot_payload_jcs: str = Field(min_length=2)
    source_changed_paths: tuple[str, ...] = EVOLUTION_SOURCE_CHANGED_PATHS
    source_delta_digest: str
    published_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_preparation(self) -> Self:
        _require_content_digests(self, "EVOLUTION_PREPARED_SUCCESSOR_DIGEST_INVALID")
        compatibility = self.profile_payload.runtime_compatibility
        profile_exact = (
            self.profile_payload.ref == "profile:veracier-supplier-shadow@r3"
            and compatibility.mode.value == "REFERENCE_HANDLER"
            and compatibility.handler_profile == f"{self.candidate_ref}@{self.candidate_digest}"
            and self.profile_payload.requested_growth_level.value == "G1_SELECT"
        )
        if not profile_exact:
            raise ValueError("EVOLUTION_PREPARED_PROFILE_INVALID")
        snapshot = _validate_candidate_snapshot(self)
        if snapshot["metadata"].get("createdAt") != self.published_at:
            raise ValueError("EVOLUTION_PREPARED_PUBLISH_TIME_INVALID")
        if self.source_changed_paths != EVOLUTION_SOURCE_CHANGED_PATHS:
            raise ValueError("EVOLUTION_SOURCE_CHANGED_PATH_ALLOWLIST_INVALID")
        return self


class EvolutionProposal(ContentAddressedModel):
    schema_version: Literal["orgrebase.evolution-proposal.v1"] = "orgrebase.evolution-proposal.v1"
    proposal_id: str = Field(min_length=1)
    predecessor_source_ref: str = Field(min_length=1)
    predecessor_source_digest: str
    predecessor_profile_ref: str = Field(min_length=1)
    predecessor_profile_digest: str
    predecessor_snapshot_ref: str = Field(min_length=1)
    predecessor_snapshot_digest: str
    candidate_ref: str = Field(min_length=1)
    candidate_digest: str
    prepared_successors: PreparedEvolutionSuccessors
    supporting_outcome_refs: tuple[str, ...]
    supporting_outcome_digests: tuple[str, ...]
    counterexample_outcome_refs: tuple[str, ...]
    counterexample_outcome_digests: tuple[str, ...]
    replay_evidence_ref: str = Field(min_length=1)
    replay_evidence_digest: str
    regression_suite_ref: str = Field(min_length=1)
    regression_suite_digest: str
    declared_benefit: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    rollback_profile_ref: str = Field(min_length=1)
    rollback_profile_digest: str
    rollback_snapshot_ref: str = Field(min_length=1)
    rollback_snapshot_digest: str
    proposal_author_id: str = Field(min_length=1)
    runtime_owner_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        _require_content_digests(self, "EVOLUTION_PROPOSAL_DIGEST_INVALID")
        _require_digests(
            (*self.supporting_outcome_digests, *self.counterexample_outcome_digests),
            "EVOLUTION_PROPOSAL_DIGEST_INVALID",
        )
        _canonical_tuple(self.supporting_outcome_refs, "EVOLUTION_PROPOSAL_SUPPORT_MISSING")
        _canonical_tuple(self.counterexample_outcome_refs, "EVOLUTION_PROPOSAL_COUNTEREXAMPLE_MISSING")
        if len(self.supporting_outcome_refs) != len(self.supporting_outcome_digests):
            raise ValueError("EVOLUTION_PROPOSAL_SUPPORT_BINDING_INVALID")
        if len(self.counterexample_outcome_refs) != len(self.counterexample_outcome_digests):
            raise ValueError("EVOLUTION_PROPOSAL_COUNTEREXAMPLE_BINDING_INVALID")
        prepared = self.prepared_successors
        snapshot = _validate_candidate_snapshot(prepared)
        metadata = snapshot["metadata"]
        source_delta = {
            "revision": "r2",
            "predecessor_ref": self.predecessor_source_ref,
            "predecessor_digest": self.predecessor_source_digest,
            "profile_ref": prepared.profile_payload.ref,
            "profile_digest": prepared.profile_payload.digest,
            "snapshot_ref": f"{metadata['id']}@{metadata['revision']}",
            "snapshot_digest": snapshot.get("digest"),
            "admitted_at": prepared.published_at,
        }
        if (
            prepared.candidate_ref != self.candidate_ref
            or prepared.candidate_digest != self.candidate_digest
            or prepared.profile_payload.digest == self.predecessor_profile_digest
            or metadata.get("id") != self.predecessor_snapshot_ref
            or prepared.source_delta_digest != sha256_digest(source_delta)
        ):
            raise ValueError("EVOLUTION_PROPOSAL_PREPARED_SUCCESSOR_BINDING_INVALID")
        if (
            self.rollback_profile_ref != self.predecessor_profile_ref
            or self.rollback_profile_digest != self.predecessor_profile_digest
            or self.rollback_snapshot_ref != self.predecessor_snapshot_ref
            or self.rollback_snapshot_digest != self.predecessor_snapshot_digest
        ):
            raise ValueError("EVOLUTION_PROPOSAL_ROLLBACK_BINDING_INVALID")
        if self.proposal_author_id == self.runtime_owner_id:
            raise ValueError("EVOLUTION_PROPOSAL_AUTHOR_RUNTIME_OWNER_COLLISION")
        if self.expires_at <= self.created_at:
            raise ValueError("EVOLUTION_PROPOSAL_EXPIRY_INVALID")
        if not self.created_at < self.prepared_successors.published_at < self.expires_at:
            raise ValueError("EVOLUTION_PROPOSAL_SUCCESSOR_TIME_INVALID")
        return self


class GovernanceDecision(ContentAddressedModel):
    schema_version: Literal["orgrebase.procedure-governance-decision.v1"] = (
        "orgrebase.procedure-governance-decision.v1"
    )
    decision_id: str = Field(min_length=1)
    proposal_ref: str = Field(min_length=1)
    proposal_digest: str
    candidate_ref: str = Field(min_length=1)
    candidate_digest: str
    predecessor_profile_ref: str = Field(min_length=1)
    predecessor_profile_digest: str
    actor_id: str = Field(min_length=1)
    actor_authority_ref: str = Field(min_length=1)
    actor_mode: GovernanceEvidenceMode
    proposal_author_id: str = Field(min_length=1)
    runtime_owner_id: str = Field(min_length=1)
    verdict: Literal["ADMIT", "REJECT"]
    reason_codes: tuple[str, ...]
    decided_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        _require_content_digests(self, "GOVERNANCE_DECISION_DIGEST_INVALID")
        error = governance_actor_error(self)
        if error is not None:
            raise ValueError(error)
        _canonical_tuple(self.reason_codes, "GOVERNANCE_REASON_CODES_INVALID")
        return self


class _EvolutionSuccessor(ContentAddressedModel):
    evidence_ref: str = Field(min_length=1)
    successor_ref: str = Field(min_length=1)
    successor_digest: str
    predecessor_ref: str = Field(min_length=1)
    predecessor_digest: str
    candidate_ref: str = Field(min_length=1)
    candidate_digest: str
    proposal_ref: str = Field(min_length=1)
    proposal_digest: str
    decision_ref: str = Field(min_length=1)
    decision_digest: str
    published_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_successor(self) -> Self:
        _require_content_digests(self, "EVOLUTION_SUCCESSOR_DIGEST_INVALID")
        if self.successor_ref == self.predecessor_ref:
            raise ValueError("EVOLUTION_SUCCESSOR_NOOP")
        return self


class EvolutionProfileSuccessor(_EvolutionSuccessor):
    schema_version: Literal["orgrebase.evolution-profile-successor.v1"] = (
        "orgrebase.evolution-profile-successor.v1"
    )
    profile_payload: EnterpriseSeedProfile

    @model_validator(mode="after")
    def validate_profile_payload(self) -> Self:
        compatibility = self.profile_payload.runtime_compatibility
        expected_handler = f"{self.candidate_ref}@{self.candidate_digest}"
        if (
            self.profile_payload.ref != self.successor_ref
            or self.profile_payload.digest != self.successor_digest
            or self.profile_payload.digest == self.predecessor_digest
            or compatibility.mode.value != "REFERENCE_HANDLER"
            or compatibility.handler_profile != expected_handler
            or self.profile_payload.requested_growth_level.value != "G1_SELECT"
        ):
            raise ValueError("EVOLUTION_PROFILE_SUCCESSOR_PAYLOAD_INVALID")
        return self


class EvolutionSnapshotSuccessor(_EvolutionSuccessor):
    schema_version: Literal["orgrebase.evolution-snapshot-successor.v1"] = (
        "orgrebase.evolution-snapshot-successor.v1"
    )
    profile_successor_ref: str = Field(min_length=1)
    profile_successor_digest: str
    predecessor_source_refs: tuple[str, ...] = Field(min_length=1)
    snapshot_payload_jcs: str = Field(min_length=2)

    @model_validator(mode="after")
    def validate_profile_successor(self) -> Self:
        _require_content_digests(self, "EVOLUTION_SNAPSHOT_SUCCESSOR_DIGEST_INVALID")
        payload = _validate_candidate_snapshot(self)
        metadata = payload["metadata"]
        expected_ref = f"{metadata['id']}@{metadata['revision']}"
        if self.successor_ref != expected_ref or payload.get("digest") != self.successor_digest:
            raise ValueError("EVOLUTION_SNAPSHOT_SUCCESSOR_PAYLOAD_INVALID")
        return self


class EvolutionSourceRevision(ContentAddressedModel):
    schema_version: Literal["orgrebase.evolution-source-revision.v1"] = (
        "orgrebase.evolution-source-revision.v1"
    )
    source_id: Literal["source:veracier:procedure-contracts"]
    revision: str = Field(pattern=r"^r[0-9]+$")
    predecessor_ref: str | None = None
    predecessor_digest: str | None = None
    profile_ref: str = Field(min_length=1)
    profile_digest: str
    snapshot_ref: str = Field(min_length=1)
    snapshot_digest: str
    profile_successor_evidence_ref: str | None = None
    profile_successor_evidence_digest: str | None = None
    snapshot_successor_evidence_ref: str | None = None
    snapshot_successor_evidence_digest: str | None = None
    proposal_ref: str | None = None
    proposal_digest: str | None = None
    state: Literal["ADMITTED"] = "ADMITTED"
    admitted_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        _require_digest(self.profile_digest, "EVOLUTION_SOURCE_PROFILE_DIGEST_INVALID")
        _require_digest(self.snapshot_digest, "EVOLUTION_SOURCE_SNAPSHOT_DIGEST_INVALID")
        optional_roots = {
            "predecessor": "EVOLUTION_SOURCE_PREDECESSOR_INVALID",
            "proposal": "EVOLUTION_SOURCE_PROPOSAL_INVALID",
            "profile_successor_evidence": "EVOLUTION_SOURCE_PROFILE_EVIDENCE_INVALID",
            "snapshot_successor_evidence": "EVOLUTION_SOURCE_SNAPSHOT_EVIDENCE_INVALID",
        }
        for name, code in optional_roots.items():
            ref = getattr(self, f"{name}_ref")
            digest = getattr(self, f"{name}_digest")
            if (ref is None) != (digest is None):
                raise ValueError(code)
            if digest is not None:
                _require_digest(digest, code)
        if self.revision == "r1" and self.predecessor_ref is not None:
            raise ValueError("EVOLUTION_SOURCE_INITIAL_HAS_PREDECESSOR")
        if self.revision != "r1" and (
            self.predecessor_ref is None
            or self.proposal_ref is None
            or self.profile_successor_evidence_ref is None
            or self.snapshot_successor_evidence_ref is None
        ):
            raise ValueError("EVOLUTION_SOURCE_SUCCESSOR_ROOTS_MISSING")
        return self


class SourcePromotionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.source-promotion-receipt.v1"] = "orgrebase.source-promotion-receipt.v1"
    receipt_id: str = Field(min_length=1)
    decision_ref: str = Field(min_length=1)
    decision_digest: str
    predecessor_ref: str = Field(min_length=1)
    predecessor_digest: str
    successor_ref: str = Field(min_length=1)
    successor_digest: str
    profile_successor_ref: str = Field(min_length=1)
    profile_successor_digest: str
    snapshot_successor_ref: str = Field(min_length=1)
    snapshot_successor_digest: str
    profile_successor_evidence_ref: str = Field(min_length=1)
    profile_successor_evidence_digest: str
    snapshot_successor_evidence_ref: str = Field(min_length=1)
    snapshot_successor_evidence_digest: str
    portable_source_admission_ref: str = Field(min_length=1)
    portable_source_admission_digest: str
    actor_mode: GovernanceEvidenceMode
    human_review_status: Literal["EXPLICIT", "NOT_RUN"]
    status: Literal["PROMOTED"] = "PROMOTED"
    promoted_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_promotion(self) -> Self:
        _require_content_digests(self, "SOURCE_PROMOTION_DIGEST_INVALID")
        expected = "EXPLICIT" if self.actor_mode is GovernanceEvidenceMode.HUMAN_CLI_COMMAND else "NOT_RUN"
        if self.human_review_status != expected:
            raise ValueError("SOURCE_PROMOTION_HUMAN_STATUS_INVALID")
        return self


class SourceRollbackReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.source-rollback-receipt.v1"] = "orgrebase.source-rollback-receipt.v1"
    receipt_id: str = Field(min_length=1)
    from_ref: str = Field(min_length=1)
    from_digest: str
    restored_ref: str = Field(min_length=1)
    restored_digest: str
    promotion_receipt_ref: str = Field(min_length=1)
    promotion_receipt_digest: str
    restored_profile_ref: str = Field(min_length=1)
    restored_profile_digest: str
    from_profile_ref: str = Field(min_length=1)
    from_profile_digest: str
    actor_id: str = Field(min_length=1)
    actor_authority_ref: str = Field(min_length=1)
    actor_mode: GovernanceEvidenceMode
    proposal_author_id: str = Field(min_length=1)
    runtime_owner_id: str = Field(min_length=1)
    human_review_status: Literal["EXPLICIT", "NOT_RUN"]
    status: Literal["ROLLED_BACK"] = "ROLLED_BACK"
    rolled_back_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rollback(self) -> Self:
        _require_content_digests(self, "SOURCE_ROLLBACK_DIGEST_INVALID")
        if self.from_ref == self.restored_ref:
            raise ValueError("SOURCE_ROLLBACK_NOOP")
        error = governance_actor_error(self)
        if error is not None:
            raise ValueError(error)
        expected = "EXPLICIT" if self.actor_mode is GovernanceEvidenceMode.HUMAN_CLI_COMMAND else "NOT_RUN"
        if self.human_review_status != expected:
            raise ValueError("SOURCE_ROLLBACK_HUMAN_STATUS_INVALID")
        return self


@dataclass(frozen=True, slots=True)
class AcceptedEvolutionSupport:
    outcome: dict[str, Any]
    execution: OACExecutionReceipt
    topology_digest: str


@dataclass(frozen=True, slots=True)
class GovernedPromotion:
    portable_source_admission: dict[str, Any]
    receipt: SourcePromotionReceipt
    profile_successor: EvolutionProfileSuccessor
    snapshot_successor: EvolutionSnapshotSuccessor
    source_successor: EvolutionSourceRevision
