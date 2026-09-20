"""Public domain contracts. Every state-changing service consumes these models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from orgrebase.digest import content_payload, sha256_digest


class EvidenceClass(StrEnum):
    LIVE_AGENTTEAMS = "LIVE_AGENTTEAMS"
    CONTROLLED_LOCAL_AGENTTEAMS = "CONTROLLED_LOCAL_AGENTTEAMS"
    LOCAL_REAL_TOOL = "LOCAL_REAL_TOOL"
    LOCAL_DETERMINISTIC = "LOCAL_DETERMINISTIC"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    PASS_STATIC = "PASS_STATIC"
    NOT_RUN = "NOT_RUN"


class SemanticClassification(StrEnum):
    NO_SEMANTIC_DELTA = "NO_SEMANTIC_DELTA"
    SEMANTIC_DELTA = "SEMANTIC_DELTA"
    TRUST_REVALIDATION = "TRUST_REVALIDATION"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"


class DependencyStrength(StrEnum):
    HARD = "HARD"
    REVIEW = "REVIEW"
    INFORMATIONAL = "INFORMATIONAL"


class CoverageBasis(StrEnum):
    RUNTIME_OBSERVED = "RUNTIME_OBSERVED"
    OWNER_DECLARED_COMPLETE = "OWNER_DECLARED_COMPLETE"
    CONTRACT_DECLARED = "CONTRACT_DECLARED"
    IMPORTED_VERIFIED = "IMPORTED_VERIFIED"
    AGENT_INFERRED = "AGENT_INFERRED"
    UNKNOWN_COVERAGE = "UNKNOWN_COVERAGE"


class EdgeStatus(StrEnum):
    ADMITTED = "ADMITTED"
    PROPOSED_EDGE = "PROPOSED_EDGE"
    DISPUTED = "DISPUTED"
    RETRACTED = "RETRACTED"


class ManifestCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class ImpactClassification(StrEnum):
    AFFECTED_HARD = "AFFECTED_HARD"
    AFFECTED_REVIEW = "AFFECTED_REVIEW"
    AFFECTED_INFORMATIONAL = "AFFECTED_INFORMATIONAL"
    UNAFFECTED_WITHIN_DECLARED_BOUNDARY = "UNAFFECTED_WITHIN_DECLARED_BOUNDARY"
    UNKNOWN = "UNKNOWN"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    REQUALIFICATION_REQUIRED = "REQUALIFICATION_REQUIRED"


class ImpactCertificateType(StrEnum):
    POSITIVE_WITNESS = "POSITIVE_WITNESS"
    BOUNDED_NON_IMPACT = "BOUNDED_NON_IMPACT"
    UNCERTAINTY_WITNESS = "UNCERTAINTY_WITNESS"


class EffectDisposition(StrEnum):
    REBUILD = "REBUILD"
    PRESERVE_WITHIN_BOUNDARY = "PRESERVE_WITHIN_BOUNDARY"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    REQUALIFY = "REQUALIFY"


class ObjectState(StrEnum):
    PROPOSED = "PROPOSED"
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    STALE = "STALE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REBASE_REQUIRED = "REBASE_REQUIRED"
    ROLLED_BACK_PENDING_REBASE = "ROLLED_BACK_PENDING_REBASE"
    REQUALIFICATION_REQUIRED = "REQUALIFICATION_REQUIRED"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"


class ContentAddressedModel(BaseModel):
    # ``frozen`` prevents attribute assignment but does not make nested JSON
    # containers immutable.  Always revalidate existing instances when they
    # cross another Pydantic boundary, and expose an explicit deep round-trip
    # for command/store boundaries that receive an already-built instance.
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    digest: str = ""

    def digest_payload(self) -> dict[str, Any]:
        return content_payload(self)

    @model_validator(mode="after")
    def set_or_verify_digest(self) -> Self:
        expected = sha256_digest(self.digest_payload())
        if self.digest and self.digest != expected:
            raise ValueError("content digest mismatch")
        object.__setattr__(self, "digest", expected)
        return self

    def revalidated(self) -> Self:
        """Deeply reparse current JSON bytes and recheck the cached digest.

        Pydantic's frozen models are shallow: a caller can still mutate a
        nested ``dict`` or manufacture a stale instance with ``model_copy``.
        Trust boundaries must use this method before relying on identity,
        authority, idempotency, or persisted digest fields.
        """

        return type(self).model_validate(self.model_dump(mode="json"))


class VersionedObject(ContentAddressedModel):
    id: str
    version: str
    kind: str
    label: str
    domain: str
    state: ObjectState
    payload: dict[str, Any] = Field(default_factory=dict)
    source_refs: tuple[str, ...] = ()
    valid_from: str = "2026-08-14T00:00:00Z"
    valid_to: str | None = None
    sensitivity: str = "INTERNAL"
    allowed_purposes: tuple[str, ...] = ("change_rebase",)
    coverage_complete: bool = False
    coverage_basis: tuple[CoverageBasis, ...] = ()

    def digest_payload(self) -> dict[str, Any]:
        payload = content_payload(self)
        payload.pop("state", None)
        return payload

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class DependencyEdge(ContentAddressedModel):
    id: str
    source_id: str
    target_id: str
    relation: str
    strength: DependencyStrength
    coverage_basis: CoverageBasis
    status: EdgeStatus
    provenance_refs: tuple[str, ...] = ()


class DependencyRequirementSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: str
    edge_id: str
    source_id: str
    relation: str
    strength: DependencyStrength
    coverage_basis: CoverageBasis
    provenance_refs: tuple[str, ...]


class DependencyManifest(ContentAddressedModel):
    id: str
    version: str
    target_id: str
    target_version: str
    issuer_id: str
    authority_domain: str
    completeness: ManifestCompleteness
    requirement_slots: tuple[DependencyRequirementSlot, ...]
    provenance_refs: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class ObjectDelta(ContentAddressedModel):
    object_id: str
    base_version: str
    proposed_version: str
    base_value: Any
    proposed_value: Any
    changed_fields: tuple[str, ...]
    semantic_classification: SemanticClassification
    admitted_by: str | None = None


class ChangeSetRevision(ContentAddressedModel):
    id: str
    revision: str
    state: str
    owner_id: str
    purpose: str
    scope: tuple[str, ...]
    deltas: tuple[ObjectDelta, ...]


class RevisionLock(ContentAddressedModel):
    change_set_revision: str
    change_set_digest: str
    graph_revision: str
    policy_revision: str
    skill_registry_revision: str
    runtime_registry_revision: str
    evaluation_scope_digest: str


class PathStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    source_id: str
    target_id: str
    relation: str
    strength: DependencyStrength
    coverage_basis: CoverageBasis
    provenance_refs: tuple[str, ...]


class ImpactResult(ContentAddressedModel):
    object_id: str
    label: str
    classification: ImpactClassification
    reason_code: str
    proof_path: tuple[PathStep, ...] = ()
    boundary: dict[str, Any] | None = None
    missing_evidence: tuple[str, ...] = ()


class ImpactCertificate(ContentAddressedModel):
    id: str
    schema_version: str = "orgrebase.impact-certificate.v1"
    certificate_type: ImpactCertificateType
    subject_id: str
    classification: ImpactClassification
    reason_code: str
    change_set_digest: str
    result_digest: str
    revision_lock_digest: str
    traversal_commitment: dict[str, Any]
    claim_boundary: str
    verifier_version: str = "orgrebase.impact-certificate-verifier@1.0.0"
    evidence_class: EvidenceClass = EvidenceClass.LOCAL_DETERMINISTIC


class MinimalEffect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str
    disposition: EffectDisposition
    impact_result_digest: str
    impact_certificate_digest: str


class MinimalRebaseCertificate(ContentAddressedModel):
    id: str
    schema_version: str = "orgrebase.minimal-rebase-certificate.v1"
    change_set_digest: str
    preview_digest: str
    revision_lock_digest: str
    impact_certificate_set_digest: str
    effects: tuple[MinimalEffect, ...]
    minimality_invariants: tuple[str, ...]
    claim_boundary: str
    verifier_version: str = "orgrebase.minimal-rebase-verifier@1.0.0"
    evidence_class: EvidenceClass = EvidenceClass.LOCAL_DETERMINISTIC


class ImpactPreview(ContentAddressedModel):
    id: str
    change_set_ref: str
    state: str
    algorithm_version: str
    revision_lock: RevisionLock
    results: tuple[ImpactResult, ...]
    certificates: tuple[ImpactCertificate, ...] = ()
    counts: dict[str, int]
    evidence_class: EvidenceClass


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_ref: str
    label: str
    disposition: str
    reason_code: str
    payload: dict[str, Any] | None = None


class ContextManifest(ContentAddressedModel):
    id: str
    version: str
    actor_id: str
    target_object_id: str
    purpose: str
    graph_revision: str
    policy_revision: str
    authorization_revision: str
    expires_at: str
    included: tuple[ContextItem, ...]
    excluded: tuple[ContextItem, ...]
    evidence_class: EvidenceClass


class AgentIdentity(ContentAddressedModel):
    name: str
    role: str
    capabilities: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    dependencies: tuple[str, ...]
    decision_boundary: tuple[str, ...]
    trace: tuple[str, ...]
    authority_domain: str


class RunEnvelope(ContentAddressedModel):
    run_id: str
    nonce: str
    issued_at: str
    expires_at: str
    mode: str
    evidence_class: EvidenceClass


class DelegationTask(ContentAddressedModel):
    """One least-authority unit compiled for a declared Agent identity."""

    id: str
    agent_name: str
    authority_domain: str
    purpose: str
    depends_on: tuple[str, ...] = ()
    input_refs: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    allowed_output_kinds: tuple[str, ...]
    context_scope: tuple[str, ...]
    failure_disposition: str
    candidate_only: bool = True


class OrchestrationPlan(ContentAddressedModel):
    """A proof-carrying task DAG; Agents cannot rewrite or extend it at runtime."""

    id: str
    schema_version: str = "orgrebase.orchestration-plan.v1"
    change_set_digest: str
    preview_digest: str
    revision_lock_digest: str
    tasks: tuple[DelegationTask, ...]
    invariants: tuple[str, ...]
    evidence_class: EvidenceClass


class CompilationReceipt(ContentAddressedModel):
    """Prove compile() read disk contracts without embedding them in the plan."""

    id: str
    schema_version: str = "orgrebase.orchestration-compilation.v1"
    task_intents_digest: str
    identity_digests: tuple[dict[str, str], ...]
    orchestration_plan_digest: str
    change_set_digest: str
    preview_digest: str
    source_evidence_class: EvidenceClass
    claim_boundary: str


class StructuredHandoff(ContentAddressedModel):
    id: str
    schema_version: str
    task_id: str
    change_set_id: str
    graph_revision: str
    workflow_run_id: str
    run_nonce: str
    orchestration_plan_digest: str
    delegation_task_digest: str
    input_refs: tuple[str, ...]
    from_agent: str
    to_agent: str
    payload: dict[str, Any]
    candidate_only: bool = True


class AgentRun(ContentAddressedModel):
    id: str
    agent_name: str
    task_id: str
    workflow_run_id: str
    run_nonce: str
    status: str
    model_version: str
    runtime_profile_version: str
    context_manifest_version: str
    skill_versions: tuple[str, ...]
    policy_versions: tuple[str, ...]
    tool_versions: tuple[str, ...]
    input_digest: str
    output_digest: str
    parent_run_id: str | None = None
    predecessor_run_ids: tuple[str, ...] = ()
    trace_id: str
    evidence_class: EvidenceClass


class CoordinationReceipt(ContentAddressedModel):
    """Independent proof that execution stayed inside the compiled Agent DAG."""

    id: str
    schema_version: str = "orgrebase.coordination-receipt.v1"
    orchestration_plan_digest: str
    workflow_run_id: str
    run_nonce: str
    handoff_digests: tuple[str, ...]
    agent_run_digests: tuple[str, ...]
    checked_invariants: tuple[str, ...]
    status: str
    evidence_class: EvidenceClass


class AgentCandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_ref: str
    artifact_digest: str
    producer_worker: str
    decision: str
    reason_codes: tuple[str, ...]
    admitted_effects: tuple[dict[str, Any], ...] = ()


class AgentCandidateIngestionReceipt(ContentAddressedModel):
    id: str
    schema_version: str = "orgrebase.agent-candidate-ingestion.v1"
    live_receipt_digest: str
    run_id: str
    nonce: str
    change_set_digest: str
    preview_digest: str
    orchestration_plan_digest: str
    decisions: tuple[AgentCandidateDecision, ...]
    admitted_candidate_digests: tuple[str, ...]
    rejected_candidate_digests: tuple[str, ...]
    target_writes: int
    source_evidence_class: EvidenceClass
    verifier_evidence_class: EvidenceClass
    claim_boundary: str


class ToolContract(ContentAddressedModel):
    id: str
    name: str
    version: str
    purpose: str
    endpoint: str
    method: str
    auth: dict[str, Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_codes: tuple[str, ...]
    permissions: dict[str, Any]
    retry_policy: dict[str, Any]
    idempotency: dict[str, Any]
    audit_fields: tuple[str, ...]
    degradation: dict[str, Any]
    mcp_migration: dict[str, Any]


class ToolInvocationReceipt(ContentAddressedModel):
    id: str
    tool_ref: str
    actor_id: str
    workflow_run_id: str
    run_nonce: str
    request_digest: str
    result_digest: str
    status: str
    started_at: str
    completed_at: str
    audit_event_digest: str
    error_code: str | None = None
    external_operation_id: str | None = None
    before_state_digest: str | None = None
    after_state_digest: str | None = None
    compensation_ref: str | None = None
    evidence_class: EvidenceClass


class ConflictCandidate(ContentAddressedModel):
    id: str
    claim_key: str
    proposed_value: Any
    authority_domain: str
    source_ref: str
    submitted_by: str


class ConflictResolutionReceipt(ContentAddressedModel):
    id: str
    claim_key: str
    workflow_run_id: str
    run_nonce: str
    status: str
    policy_revision: str
    candidates: tuple[ConflictCandidate, ...]
    admitted_candidate_id: str
    rejected: tuple[dict[str, Any], ...]
    rule: str
    target_writes: int
    evidence_class: EvidenceClass


class Approval(ContentAddressedModel):
    id: str
    actor_id: str
    change_set_digest: str
    preview_digest: str
    minimal_rebase_certificate_digest: str
    authorization_revision: str
    authority_scope: tuple[str, ...]
    approved_at: str
    expires_at: str
    method: str


class SourceApproval(ContentAddressedModel):
    owner_id: str
    source_ids: tuple[str, ...]
    approval: Approval


class RebaseApprovalSet(ContentAddressedModel):
    schema_version: Literal["orgrebase.rebase-approval-set.v1"] = "orgrebase.rebase-approval-set.v1"
    id: str
    members: tuple[SourceApproval, ...]

    @model_validator(mode="after")
    def exact_members(self) -> Self:
        owners = tuple(member.owner_id for member in self.members)
        sources = tuple(source for member in self.members for source in member.source_ids)
        if not 1 <= len(owners) <= 3 or owners != tuple(sorted(set(owners))):
            raise ValueError("APPROVAL_SET_OWNERS_INVALID")
        if not 1 <= len(sources) <= 3 or len(sources) != len(set(sources)):
            raise ValueError("APPROVAL_SET_SOURCE_COVERAGE_INVALID")
        if any(not member.source_ids or member.source_ids != tuple(sorted(member.source_ids)) for member in self.members):
            raise ValueError("APPROVAL_SET_SOURCE_ORDER_INVALID")
        return self


class FailureReceipt(ContentAddressedModel):
    id: str
    operation: str
    workflow_run_id: str
    run_nonce: str
    status: str
    error_code: str
    expected_revisions: dict[str, str]
    actual_revisions: dict[str, str]
    target_writes: int
    before_state_digest: str
    after_state_digest: str
    evidence_class: EvidenceClass


class RollbackApproval(ContentAddressedModel):
    id: str
    actor_id: str
    workflow_run_id: str
    run_nonce: str
    actor_role: str
    authority_scope: tuple[str, ...]
    rebase_receipt_digest: str
    rollback_plan_digest: str
    apply_approval_digest: str
    apply_approver_id: str
    reason: str
    approved_at: str
    method: str


class RollbackPlan(ContentAddressedModel):
    id: str
    rebase_receipt_digest: str
    workflow_run_id: str
    run_nonce: str
    authoritative_claim_ref: str
    expected_current_state: dict[str, dict[str, str]]
    actions: tuple[dict[str, Any], ...]
    preserved_states: tuple[dict[str, Any], ...]
    target_status: str


class RollbackReceipt(ContentAddressedModel):
    id: str
    status: str
    compensates_rebase_receipt: str
    workflow_run_id: str
    run_nonce: str
    rollback_plan_digest: str
    approval_ref: str
    apply_approval_ref: str
    authoritative_claim_ref: str
    authoritative_claim_unchanged: bool
    transitions: tuple[dict[str, Any], ...]
    preserved_states: tuple[dict[str, Any], ...]
    compensation_results: tuple[dict[str, Any], ...]
    metrics: dict[str, int]
    event_chain_head: str
    evidence_class: EvidenceClass


class CompensationSagaReceipt(ContentAddressedModel):
    id: str
    status: str
    workflow_run_id: str
    run_nonce: str
    rollback_receipt_digest: str
    rollback_approval_digest: str
    git_patch_receipt_digest: str
    attempt: int
    internal_compensation: dict[str, Any]
    external_compensation: dict[str, Any]
    residual_effects: tuple[dict[str, Any], ...]
    next_action: str | None
    evidence_class: EvidenceClass


class SkillActionCandidate(ContentAddressedModel):
    """Side-effect-free, exactly bound output of a governed Skill invocation."""

    schema_version: str = "orgrebase.skill-action.v1"
    preview_digest: str
    object_id: str
    action: str
    reason_code: str
    adapter_version: str
    contract_digest: str
    candidate_only: bool = True
    evidence_class: EvidenceClass = EvidenceClass.LOCAL_DETERMINISTIC


class SkillVersionScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    total: int
    passed: int
    accuracy: float
    missed_hard_dependencies: int
    false_invalidations: int
    unauthorized_disclosures: int
    negative_transfer_failures: int
    outcome: str
    case_results: tuple[dict[str, Any], ...]


class QualificationReport(ContentAddressedModel):
    id: str
    suite_version: str
    skill_contract_digest: str
    evaluation_set_digest: str
    candidate_adapter_version: str
    premise_lock: dict[str, str]
    scores: tuple[SkillVersionScore, ...]
    previous_state: ObjectState
    candidate_state: ObjectState
    release_scope: tuple[str, ...]
    evidence_class: EvidenceClass


class RebaseReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.rebase-receipt.v2"] | None = None
    approval_set: RebaseApprovalSet | None = None

    @model_serializer(mode="wrap")
    def serialize_approval_set(self, handler):
        data = handler(self)
        if self.approval_set is None:
            data.pop("approval_set", None)
        if self.schema_version is None:
            data.pop("schema_version", None)
        return data

    @model_validator(mode="after")
    def approval_scheme(self):
        if (self.schema_version is None) != (self.approval_set is None):
            raise ValueError("REBASE_RECEIPT_APPROVAL_SCHEME_INVALID")
        if self.approval_set is not None and self.approval_actor_id is not None:
            raise ValueError("REBASE_RECEIPT_GROUP_ACTOR_INVALID")
        return self

    id: str
    status: str
    workflow_run_id: str
    run_nonce: str
    change_set_ref: str
    preview_ref: str
    minimal_rebase_certificate_digest: str
    approval_ref: str
    approval_digest: str
    approval_actor_id: str | None
    applied_claims: tuple[dict[str, Any], ...]
    transitions: tuple[dict[str, Any], ...]
    bounded_unaffected: tuple[dict[str, Any], ...]
    unknown: tuple[dict[str, Any], ...]
    context_manifests: tuple[ContextManifest, ...]
    runtime_dependencies: tuple[dict[str, Any], ...]
    agent_runs: tuple[AgentRun, ...]
    acknowledgements: tuple[dict[str, Any], ...]
    qualification_report: QualificationReport
    metrics: dict[str, int]
    revision_lock: RevisionLock
    evidence_class: EvidenceClass
    candidate_ingestion_digest: str
    compilation_receipt_digest: str
    live_ingestion_digest: str | None = None


class FreshnessError(RuntimeError):
    code = "PREVIEW_EXPIRED"


class AuthorizationError(RuntimeError):
    code = "AUTHZ_DENIED"


class IntegrityError(RuntimeError):
    code = "EVIDENCE_INTEGRITY_FAILED"


class EvaluationSecurityError(RuntimeError):
    code = "EVALUATION_BOUNDARY_VIOLATION"
