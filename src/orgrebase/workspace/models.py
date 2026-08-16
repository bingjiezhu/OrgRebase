"""Strict contracts for the OrgRebase Workspace build-and-recovery loop.

The existing :mod:`orgrebase.domain` models remain the canonical contracts for the
legacy change-consistency demo.  This module adds a versioned workspace layer
without changing legacy serialization or frozen plan digests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentCandidateIngestionReceipt,
    AgentRun,
    ChangeSetRevision,
    CompilationReceipt,
    ContentAddressedModel,
    CoordinationReceipt,
    CoverageBasis,
    DependencyManifest,
    DependencyStrength,
    EvidenceClass,
    ImpactPreview,
    ManifestCompleteness,
    MinimalRebaseCertificate,
    ObjectState,
    OrchestrationPlan,
    RunEnvelope,
    StructuredHandoff,
    VersionedObject,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SemanticKind(StrEnum):
    CLAIM = "CLAIM"
    POLICY = "POLICY"
    SKILL = "SKILL"
    TOOL = "TOOL"
    LITERAL = "LITERAL"


class ClaimScope(StrEnum):
    ORGANIZATION = "ORGANIZATION"
    TASK = "TASK"


class ExtraRequirementPolicy(StrEnum):
    REJECT = "REJECT"
    REVIEW = "REVIEW"


class CandidateSource(StrEnum):
    USER = "USER"
    AGENT = "AGENT"
    TEMPLATE = "TEMPLATE"


class AdmissionVerdict(StrEnum):
    ADMIT = "ADMIT"
    REJECT = "REJECT"
    HOLD = "HOLD"


class HealthStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    INACTIVE = "INACTIVE"


class CoverageStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class SkillCandidateStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CANARY = "CANARY"
    QUARANTINED = "QUARANTINED"


ExtendedEvidenceClass = EvidenceClass | Literal["DESIGN", "LIVE_MODEL", "USER_STUDY"]


class TaskRequest(ContentAddressedModel):
    id: str
    organization_id: str
    actor_id: str
    purpose: str
    deliverable_kind: str
    requested_at: str
    template_ref: str
    input_values: dict[str, JsonValue] = Field(default_factory=dict)
    customer_id: str | None = None
    idempotency_key: str


class RequirementSlotSpec(ContentAddressedModel):
    slot_id: str
    semantic_kind: SemanticKind
    domain_id: str
    value_schema_ref: str
    required: bool
    relation: str
    strength: DependencyStrength
    claim_scope: ClaimScope | None = None
    freshness_seconds: int | None = Field(default=None, ge=0)
    sensitivity_ceiling: str = "INTERNAL"
    allowed_purposes: tuple[str, ...]
    allowed_recipients: tuple[str, ...]
    output_field_paths: tuple[str, ...] = ()


class TaskTemplateVersion(ContentAddressedModel):
    id: str
    version: str
    deliverable_kind: str
    slots: tuple[RequirementSlotSpec, ...]
    extra_requirement_policy: ExtraRequirementPolicy
    renderer_id: str
    renderer_version: str
    preservation_fields: tuple[str, ...]
    forbidden_fields: tuple[str, ...]
    output_schema_ref: str
    allowed_tool_ids: tuple[str, ...] = ()
    state: ObjectState = ObjectState.ACTIVE

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def validate_slots(self) -> TaskTemplateVersion:
        slot_ids = [item.slot_id for item in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("template slot IDs must be unique")
        return self


class TaskRequirementCandidate(ContentAddressedModel):
    task_ref: str
    slot_id: str
    proposed_domain_id: str
    rationale: str
    source: CandidateSource
    candidate_sequence: int = Field(ge=0)
    requested_value_hint: JsonValue | None = None


class TemplateCandidate(ContentAddressedModel):
    task_ref: str
    template_ref: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[str, ...]
    candidate_only: Literal[True] = True


class TaskTemplateCatalogSummary(ContentAddressedModel):
    id: str
    template_refs: tuple[str, ...]
    deliverable_kinds: tuple[str, ...]
    catalog_digest: str


class TaskInterpretationReceipt(ContentAddressedModel):
    id: str
    task_ref: str
    template_ref: str
    candidate_refs: tuple[str, ...]
    candidate_set_digest: str
    interpreter_version: str
    candidate_only: Literal[True] = True
    evidence_class: ExtendedEvidenceClass


class DomainCapabilityCardVersion(ContentAddressedModel):
    id: str
    version: str
    domain_id: str
    worker_id: str
    authority_refs: tuple[str, ...]
    capabilities: tuple[str, ...]
    supported_slot_ids: tuple[str, ...]
    accepted_input_schema_refs: tuple[str, ...]
    output_schema_refs: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...] = ()
    resource_refs: tuple[str, ...] = ()
    allowed_purposes: tuple[str, ...]
    disclosure_policy_ref: str
    freshness_sla_seconds: int = Field(ge=0)
    declared_cost: int = Field(ge=0)
    health_status: HealthStatus
    valid_from: str
    valid_to: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class CoalitionCoverage(FrozenModel):
    slot_id: str
    card_ref: str
    domain_id: str


class CoalitionPlan(ContentAddressedModel):
    id: str
    task_ref: str
    template_ref: str
    admitted_slot_ids: tuple[str, ...]
    selected_card_refs: tuple[str, ...]
    coverage: tuple[CoalitionCoverage, ...]
    total_declared_cost: int
    tie_break_tuple: tuple[int, int, tuple[str, ...]]
    planner_version: str
    revision_lock: dict[str, str]
    input_digest: str

    @property
    def selected_domain_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.domain_id for item in self.coverage}))

    @model_validator(mode="after")
    def validate_order_and_coverage(self) -> CoalitionPlan:
        if self.selected_card_refs != tuple(sorted(self.selected_card_refs)):
            raise ValueError("selected cards must be lexical sorted")
        if tuple(item.slot_id for item in self.coverage) != tuple(
            sorted(item.slot_id for item in self.coverage)
        ):
            raise ValueError("coverage must be sorted by slot ID")
        if set(self.admitted_slot_ids) != {item.slot_id for item in self.coverage}:
            raise ValueError("coalition coverage must exactly cover admitted slots")
        return self


class EvidenceSourceRef(ContentAddressedModel):
    source_id: str
    source_version: str
    source_digest: str
    locator_class: str
    observed_at: str
    valid_from: str
    valid_to: str | None = None
    media_type: str | None = None


class ClaimCandidate(ContentAddressedModel):
    candidate_id: str
    semantic_kind: SemanticKind
    subject_ref: str
    predicate: str
    value: JsonValue
    value_schema_ref: str
    issuer_domain_id: str
    authority_ref: str
    source_refs: tuple[EvidenceSourceRef, ...]
    governance_state: Literal["PROPOSED"] = "PROPOSED"
    temporal_state: str
    sensitivity: str
    claim_scope: ClaimScope
    task_ref: str | None = None
    purpose: str
    recipients: tuple[str, ...]
    fresh_until: str
    derived_from: tuple[str, ...] = ()
    transformation_ref: str | None = None
    conflict_keys: tuple[str, ...] = ()


class DomainCandidateBundle(ContentAddressedModel):
    id: str
    task_ref: str
    template_ref: str
    coalition_plan_ref: str
    domain_id: str
    worker_id: str
    delegation_task_ref: str | None = None
    candidate_refs: tuple[str, ...]
    candidate_set_digest: str
    candidate_only: Literal[True] = True
    transport_mode: Literal["LOCAL_DETERMINISTIC", "LIVE_AGENTTEAMS"]
    evidence_class: EvidenceClass


class AdmissionDecision(ContentAddressedModel):
    id: str
    candidate_ref: str
    verdict: AdmissionVerdict
    reason_codes: tuple[str, ...]
    checks: dict[str, bool]
    policy_revision: str
    decided_at: str


class AdmittedReference(ContentAddressedModel):
    slot_id: str
    object_ref: str
    object_digest: str
    projection_schema_ref: str
    projection: JsonValue
    projection_digest: str
    relation: str
    strength: DependencyStrength
    semantic_kind: SemanticKind
    claim_scope: ClaimScope | None
    purpose: str
    recipient_ids: tuple[str, ...]
    expires_at: str
    admission_decision_ref: str

    @model_validator(mode="after")
    def verify_projection(self) -> AdmittedReference:
        if sha256_digest(self.projection) != self.projection_digest:
            raise ValueError("projection digest mismatch")
        return self


class ActorContextProjection(ContentAddressedModel):
    id: str
    version: str
    actor_id: str
    task_context_ref: str
    purpose: str
    included_refs: tuple[str, ...]
    excluded: tuple[dict[str, str], ...]
    expires_at: str
    evidence_class: EvidenceClass

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class TaskContextManifest(ContentAddressedModel):
    id: str
    version: str
    organization_id: str
    task_ref: str
    template_ref: str
    coalition_plan_ref: str
    revision_lock: dict[str, str]
    actor_projection_refs: tuple[str, ...]
    slot_bindings: tuple[AdmittedReference, ...]
    created_at: str
    expires_at: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class DomainReadRequest(ContentAddressedModel):
    request_id: str
    task_ref: str
    actor_id: str
    domain_id: str
    slot_ids: tuple[str, ...]
    purpose: str
    projection_ref: str


class DomainReadProjection(ContentAddressedModel):
    id: str
    request_ref: str
    domain_id: str
    actor_id: str
    values: dict[str, JsonValue]
    source_refs: tuple[str, ...]
    excluded_fields: tuple[str, ...]
    evidence_class: EvidenceClass


class ReferenceResolvedEvent(ContentAddressedModel):
    event_type: Literal["REFERENCE_RESOLVED"] = "REFERENCE_RESOLVED"
    event_id: str
    task_ref: str
    run_id: str
    sequence: int
    slot_id: str
    provider_ref: str
    provider_digest: str
    semantic_kind: SemanticKind
    relation: str
    strength: DependencyStrength
    projection_digest: str
    input_digest: str
    occurred_at: str
    idempotency_key: str
    previous_event_digest: str


class OutputProducedEvent(ContentAddressedModel):
    event_type: Literal["OUTPUT_PRODUCED"] = "OUTPUT_PRODUCED"
    event_id: str
    task_ref: str
    run_id: str
    sequence: int
    output_ref: str
    output_digest: str
    occurred_at: str
    idempotency_key: str
    previous_event_digest: str


class ToolCalledEvent(ContentAddressedModel):
    event_type: Literal["TOOL_CALLED"] = "TOOL_CALLED"
    event_id: str
    task_ref: str
    run_id: str
    sequence: int
    tool_ref: str
    invocation_receipt_ref: str
    request_digest: str
    result_digest: str
    occurred_at: str
    idempotency_key: str
    previous_event_digest: str


ExecutionEvent = Annotated[
    ReferenceResolvedEvent | OutputProducedEvent | ToolCalledEvent,
    Field(discriminator="event_type"),
]


class OutputFieldLineage(FrozenModel):
    field_path: str
    source_slot_ids: tuple[str, ...] = ()
    source_kind: Literal["SLOT", "TASK_LITERAL", "DETERMINISTIC_COMPUTED"]


class WorkTrace(ContentAddressedModel):
    id: str
    version: str
    organization_id: str
    task_ref: str
    run_id: str
    template_ref: str
    coalition_plan_ref: str
    context_manifest_ref: str
    events: tuple[ExecutionEvent, ...]
    head_event_digest: str
    renderer_ref: str
    started_at: str
    completed_at: str
    output_ref: str
    output_digest: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def verify_event_chain(self) -> WorkTrace:
        previous = "sha256:" + "0" * 64
        for expected, event in enumerate(self.events, 1):
            if event.sequence != expected or event.previous_event_digest != previous:
                raise ValueError("work trace event chain mismatch")
            previous = event.digest
        if self.events and self.head_event_digest != self.events[-1].digest:
            raise ValueError("work trace head mismatch")
        if not self.events and self.head_event_digest != previous:
            raise ValueError("empty work trace head mismatch")
        return self


class TraceCoverageReceipt(ContentAddressedModel):
    id: str
    trace_ref: str
    template_ref: str
    expected_required_slots: tuple[str, ...]
    allowed_optional_slots: tuple[str, ...]
    observed_slots: tuple[str, ...]
    missing_slots: tuple[str, ...]
    unexpected_slots: tuple[str, ...]
    unmediated_channels: tuple[str, ...]
    output_field_lineage: tuple[OutputFieldLineage, ...]
    status: CoverageStatus
    verifier_version: str


class RuntimeDependencyEntry(FrozenModel):
    consumer_ref: str
    provider_ref: str
    provider_digest: str
    relation: str
    strength: DependencyStrength
    source_event_ref: str
    source_event_digest: str
    slot_id: str
    valid_from: str
    valid_to: str | None = None


class RuntimeDependencyManifest(ContentAddressedModel):
    id: str
    version: str
    consumer_ref: str
    task_ref: str
    trace_ref: str
    coverage_receipt_ref: str
    issuer_id: str
    authority_domain: str
    completeness: ManifestCompleteness
    entries: tuple[RuntimeDependencyEntry, ...]
    provenance_refs: tuple[str, ...]
    revision_lock: dict[str, str]
    compiled_at: str
    compiler_version: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class QuoteTaskLiterals(FrozenModel):
    owner: str
    customer_id: str


class ResolvedQuoteInputs(FrozenModel):
    product_plan: str
    launch_date: str
    data_residency: str
    notice_required: bool
    price_band: str
    currency: str
    partner_terms_code: str
    quote_compose_skill_ref: str


class QuotePayload(FrozenModel):
    owner: str
    deliverable_kind: Literal["QUOTE"] = "QUOTE"
    customer_id: str
    product_plan: str
    launch_date: str
    data_residency: str
    notice_required: bool
    price_band: str
    currency: str
    partner_terms_code: str
    rebased_from: str | None = None
    rebase_change_set: str | None = None
    context_manifest: str | None = None


class TaskReceipt(ContentAddressedModel):
    id: str
    task_ref: str
    template_ref: str
    coalition_plan_ref: str
    admission_decision_refs: tuple[str, ...]
    context_manifest_ref: str
    trace_ref: str
    coverage_receipt_ref: str
    dependency_manifest_ref: str
    deliverable_ref: str
    graph_snapshot_ref: str
    revision_lock: dict[str, str]
    status: Literal["COMPLETED"] = "COMPLETED"
    committed_at: str


class WorkspaceGraphEdge(ContentAddressedModel):
    id: str
    provider_ref: str
    provider_digest: str
    consumer_ref: str
    relation: str
    strength: DependencyStrength
    source_manifest_ref: str
    source_evidence_ref: str
    source_trace_event_ref: str | None = None
    coverage_basis: CoverageBasis
    valid_from: str
    valid_to: str | None = None


class ObjectDigestBinding(FrozenModel):
    object_ref: str
    object_id: str
    version: str
    kind: str
    digest: str
    storage_class: Literal["OBJECT_VERSION", "ARTIFACT_PROJECTION"]


class WorkspaceUniverse(ContentAddressedModel):
    id: str
    revision: str
    organization_id: str
    universe_id: str
    current_objects: tuple[VersionedObject, ...]
    task_artifact_projections: tuple[VersionedObject, ...]
    edges: tuple[WorkspaceGraphEdge, ...]
    runtime_manifests: tuple[RuntimeDependencyManifest, ...]
    imported_manifests: tuple[DependencyManifest, ...]
    target_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    completeness_basis: tuple[CoverageBasis, ...]
    built_at: str


class WorkspaceGraphSnapshot(ContentAddressedModel):
    id: str
    version: str
    organization_id: str
    graph_namespace: str
    universe_id: str
    universe_digest: str
    base_revision: str
    scope_roots: tuple[str, ...]
    object_refs: tuple[str, ...]
    object_digests: tuple[ObjectDigestBinding, ...]
    edges: tuple[WorkspaceGraphEdge, ...]
    manifest_refs: tuple[str, ...]
    target_refs: tuple[str, ...]
    revisions: dict[str, str]
    edge_set_digest: str
    built_at: str
    builder_version: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def verify_sorted_and_edge_digest(self) -> WorkspaceGraphSnapshot:
        if self.object_refs != tuple(sorted(self.object_refs)):
            raise ValueError("snapshot object refs must be sorted")
        if self.manifest_refs != tuple(sorted(self.manifest_refs)):
            raise ValueError("snapshot manifest refs must be sorted")
        if self.target_refs != tuple(sorted(self.target_refs)):
            raise ValueError("snapshot targets must be sorted")
        expected = sha256_digest([item.model_dump(mode="json") for item in self.edges])
        if self.edge_set_digest != expected:
            raise ValueError("workspace edge-set digest mismatch")
        return self


class GraphPointerPayload(FrozenModel):
    snapshot_ref: str
    snapshot_digest: str
    graph_namespace: str
    universe_id: str
    base_revision: str
    promoted_at: str


class StoredArtifact(FrozenModel):
    artifact_id: str
    media_type: str
    payload: dict[str, JsonValue]
    payload_digest: str


class ArtifactWrite(FrozenModel):
    artifact_id: str
    media_type: str
    payload: dict[str, JsonValue]
    payload_digest: str


class PreparedFormationBundle(FrozenModel):
    request_digest: str
    idempotency_key: str
    deliverable: VersionedObject
    graph_pointer: VersionedObject
    artifact_writes: tuple[ArtifactWrite, ...]
    task_receipt: TaskReceipt
    event_type: Literal["WORKSPACE_TASK_COMMITTED"] = "WORKSPACE_TASK_COMMITTED"
    event_payload: dict[str, JsonValue]


class PreparedSuccessorEvidence(FrozenModel):
    successor_objects: tuple[VersionedObject, ...]
    successor_artifact_writes: tuple[ArtifactWrite, ...]
    graph_pointer: VersionedObject
    successor_snapshot_ref: str
    successor_snapshot_digest: str


class WorkflowIdentity(FrozenModel):
    namespace: str
    approval_prefix: str
    receipt_prefix: str
    coordination_prefix: str
    idempotency_prefix: str
    extension_receipt_prefix: str


class WorkspaceChangeSpec(ContentAddressedModel):
    id: str
    revision: str
    owner_id: str
    purpose: str
    object_id: str
    base_version: str
    proposed_version: str
    expected_snapshot_ref: str
    expected_snapshot_digest: str
    idempotency_key: str


class VerifiedAdvisoryBundle(FrozenModel):
    orchestration_plan: OrchestrationPlan
    compilation_receipt: CompilationReceipt
    coordination_receipt: CoordinationReceipt
    ingestion_receipt: AgentCandidateIngestionReceipt
    handoffs: tuple[StructuredHandoff, ...]
    agent_runs: tuple[AgentRun, ...]


class WorkspacePreviewBundle(FrozenModel):
    change_spec: WorkspaceChangeSpec
    snapshot_ref: str
    snapshot_digest: str
    change_set: ChangeSetRevision
    preview: ImpactPreview
    minimal_rebase_certificate: MinimalRebaseCertificate
    advisory: VerifiedAdvisoryBundle
    run_envelope: RunEnvelope


class WorkspaceRebaseReceipt(ContentAddressedModel):
    id: str
    base_rebase_receipt_ref: str
    base_rebase_receipt_digest: str
    successor_object_refs: tuple[str, ...]
    successor_trace_refs: tuple[str, ...]
    successor_manifest_refs: tuple[str, ...]
    successor_snapshot_ref: str
    successor_snapshot_digest: str
    graph_pointer_ref: str
    status: Literal["COMPLETED"] = "COMPLETED"
    committed_at: str


class SkillOperation(FrozenModel):
    operation: str
    input_fields: tuple[str, ...]
    output_field: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class DeclarativeSkillProgram(ContentAddressedModel):
    schema_version: Literal["orgrebase.declarative-skill.v1"] = "orgrebase.declarative-skill.v1"
    operations: tuple[SkillOperation, ...]
    allowed_tool_ids: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()


class SkillCandidateArtifact(ContentAddressedModel):
    id: str
    version: str
    source_task_refs: tuple[str, ...]
    source_trace_refs: tuple[str, ...]
    candidate_program_ref: str
    candidate_program_digest: str
    input_schema_ref: str
    output_schema_ref: str
    applicable_template_refs: tuple[str, ...]
    required_slot_ids: tuple[str, ...]
    output_lineage_rules: tuple[OutputFieldLineage, ...]
    allowed_tool_ids: tuple[str, ...]
    risk_class: str
    executable: Literal[False] = False
    status: Literal["CANDIDATE"] = "CANDIDATE"
    created_by: str
    created_at: str


class SkillEvaluationCase(ContentAddressedModel):
    id: str
    partition: Literal[
        "REPLAY",
        "HELD_OUT",
        "NEGATIVE_TRANSFER",
        "PERMISSION",
        "INJECTION",
        "MALFORMED",
        "RESOURCE",
        "CANARY",
    ]
    public_input: dict[str, JsonValue]
    expected_output: dict[str, JsonValue]
    template_ref: str


class SkillCaseResult(FrozenModel):
    case_ref: str
    partition: str
    baseline_output_digest: str
    candidate_output_digest: str
    passed: bool
    repaired_baseline_failure: bool
    regressed_baseline_success: bool
    reason_codes: tuple[str, ...]


class SkillGateResult(FrozenModel):
    gate_id: str
    passed: bool
    observed: JsonValue
    threshold: JsonValue
    reason_code: str


class SkillEvaluationReceipt(ContentAddressedModel):
    id: str
    candidate_ref: str
    candidate_digest: str
    candidate_program_digest: str
    evaluation_suite_digest: str
    case_results: tuple[SkillCaseResult, ...]
    gate_results: tuple[SkillGateResult, ...]
    verdict: Literal["CANARY", "QUARANTINED"]
    premise_lock: dict[str, str]
    evaluated_at: str


class DomainDelegationTask(ContentAddressedModel):
    id: str
    task_ref: str
    template_ref: str
    coalition_plan_ref: str
    domain_id: str
    worker_id: str
    slot_ids: tuple[str, ...]
    actor_context_projection_ref: str
    allowed_output_schema_refs: tuple[str, ...]
    run_id: str
    nonce: str
    deadline_at: str
    candidate_only: Literal[True] = True


class DomainTransportCandidate(ContentAddressedModel):
    delegation_task_ref: str
    worker_id: str
    domain_id: str
    candidate_bundle_ref: str
    candidate_bundle_digest: str
    room_id: str | None = None
    event_id: str | None = None
    provider_request_id: str | None = None
    evidence_class: EvidenceClass


class DomainTransportReceipt(ContentAddressedModel):
    id: str
    task_ref: str
    coalition_plan_ref: str
    delegation_task_digests: tuple[str, ...]
    candidate_digests: tuple[str, ...]
    selected_worker_ids: tuple[str, ...]
    run_id: str
    nonce: str
    target_writes: Literal[0] = 0
    status: Literal["PASS", "REJECTED"]
    evidence_class: EvidenceClass


class ModelRequest(ContentAddressedModel):
    request_id: str
    run_id: str
    task_ref: str
    actor_id: str
    purpose: str
    schema_name: str
    schema_digest: str
    context_refs: tuple[str, ...]
    input_refs: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    provider: str
    model_id: str
    model_version: str
    prompt_template_ref: str
    prompt_template_digest: str
    temperature: float
    seed: int | None
    max_output_tokens: int = Field(gt=0)
    attempt: int = Field(ge=0, le=2)


class ModelResponseReceipt(ContentAddressedModel):
    id: str
    request_ref: str
    request_digest: str
    status: Literal["VALID", "ABSTAIN", "SCHEMA_ERROR", "PROVIDER_ERROR", "NOT_RUN"]
    value: JsonValue | None = None
    output_digest: str | None = None
    provider_request_id: str | None = None
    provider: str
    model_id: str
    model_version: str
    schema_valid: bool
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    seed_supported: bool | None = None
    error_code: str | None = None
    completed_at: str
    evidence_class: ExtendedEvidenceClass


class DatasetLicenseRecord(ContentAddressedModel):
    id: str
    name: str
    version: str
    origin_url: str | None
    license_spdx: str
    attribution: str | None
    redistribution: Literal["BUNDLED", "DOWNLOAD_ONLY", "NOT_ALLOWED"]
    pii_status: Literal["NONE_SYNTHETIC", "PUBLIC_DOCUMENT", "POTENTIAL_PII", "RESTRICTED"]
    allowed_purposes: tuple[str, ...]
    retention_policy: str
    content_digest: str | None
    fetched_at: str | None


class BenchmarkCase(ContentAddressedModel):
    schema_version: str
    case_id: str
    case_type: Literal["FORMATION", "CHANGE", "SECURITY", "REPEATABILITY", "SKILL"]
    evidence_class: ExtendedEvidenceClass
    organization_id: str
    organization_ref: str
    partition: str
    public_input: dict[str, JsonValue]
    seed: int


class BenchmarkGold(ContentAddressedModel):
    schema_version: str
    case_id: str
    expected_template: str | None
    expected_coalition: tuple[str, ...]
    expected_admitted_refs: tuple[str, ...]
    expected_output: JsonValue | None
    expected_edges: tuple[dict[str, JsonValue], ...]
    expected_impact: JsonValue | None
    expected_error_code: str | None
    forbidden_values: tuple[str, ...]


class EvaluationMetric(FrozenModel):
    metric_id: str
    value: float | int | str
    status: Literal["PASS", "FAIL", "NOT_APPLICABLE"]
    numerator: float | int | None = None
    denominator: float | int | None = None
    case_refs: tuple[str, ...] = ()


class CaseRunReceipt(ContentAddressedModel):
    id: str
    case_ref: str
    system_profile: str
    mode: str
    status: Literal["PASS", "FAIL", "ABSTAIN", "ERROR", "NOT_RUN"]
    public_output: JsonValue
    output_digest: str
    artifact_refs: tuple[str, ...]
    evidence_class: ExtendedEvidenceClass
    run_id: str
    completed_at: str


class HumanReviewRecord(ContentAddressedModel):
    id: str
    case_ref: str
    reviewer_id: str
    verdict: Literal["PASS", "FAIL", "UNCERTAIN"]
    reason_codes: tuple[str, ...]
    recorded_at: str
    evidence_class: Literal["USER_STUDY"] = "USER_STUDY"


class OWBCoreScore(ContentAddressedModel):
    benchmark_version: str
    system_profile: str
    component_scores: dict[str, float]
    weighted_score: float = Field(ge=0.0, le=100.0)
    pass_threshold: float = Field(default=90.0, ge=0.0, le=100.0)
    status: Literal["PASS", "FAIL"]


class LiveAgentCandidateScore(ContentAddressedModel):
    benchmark_version: str
    model_profile: str
    repetitions: int = Field(ge=1)
    metric_values: dict[str, float]
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    evidence_class: ExtendedEvidenceClass


class EvaluationReport(ContentAddressedModel):
    id: str
    benchmark_version: str
    system_profile: str
    mode: str
    case_receipt_refs: tuple[str, ...]
    metrics: tuple[EvaluationMetric, ...]
    hard_gate_results: tuple[EvaluationMetric, ...]
    core_score: OWBCoreScore | None = None
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    evidence_refs: tuple[str, ...]
    completed_at: str


class EvidenceIndexEntry(ContentAddressedModel):
    artifact_ref: str
    media_type: str
    sha256: str
    evidence_class: ExtendedEvidenceClass
    claim_supported: str
    verifier_command: str
    negative_test_ids: tuple[str, ...]
    contains_sensitive_data: bool


class EvidenceIndex(ContentAddressedModel):
    id: str
    run_id: str
    entries: tuple[EvidenceIndexEntry, ...]
    event_chain_head: str
    status: Literal["PASS", "FAIL"]
    generated_at: str


class UserWalkthroughRecord(ContentAddressedModel):
    id: str
    participant_role: str
    scenario_version: str
    consent_recorded: bool
    problem_understood: bool
    usefulness_rating: int = Field(ge=1, le=5)
    trace_value_rating: int = Field(ge=1, le=5)
    pilot_intent: Literal["PILOT", "CONDITIONAL_PILOT", "NO_PILOT", "NOT_ASKED"]
    redacted_findings: tuple[str, ...]
    critical_gaps: tuple[str, ...]
    recorded_at: str
    evidence_class: Literal["USER_STUDY"] = "USER_STUDY"


class UserValidationSummary(ContentAddressedModel):
    id: str
    participant_count: int = Field(ge=0)
    problem_comprehension_rate: float = Field(ge=0.0, le=1.0)
    median_usefulness: float = Field(ge=0.0, le=5.0)
    median_trace_value: float = Field(ge=0.0, le=5.0)
    pilot_or_conditional_count: int = Field(ge=0)
    repeated_critical_gaps: tuple[str, ...]
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    evidence_class: ExtendedEvidenceClass
    limitations: tuple[str, ...]



# Backward-compatible aliases used by older v4/v5 documents.
StructuredModelRequest = ModelRequest
StructuredModelResponse = ModelResponseReceipt
DatasetAssetRecord = DatasetLicenseRecord
MetricResult = EvaluationMetric
EvaluationRun = EvaluationReport
