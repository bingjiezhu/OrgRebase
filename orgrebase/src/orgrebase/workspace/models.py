"""Strict contracts for the OrgRebase Workspace build-and-recovery loop.

The existing :mod:`orgrebase.domain` models remain the canonical contracts for the
legacy change-consistency demo.  This module adds a versioned workspace layer
without changing legacy serialization or frozen plan digests.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer, model_validator

from orgrebase.change_events import ChangeEvent as ChangeEvent
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
from orgrebase.runtime_contracts import ArtifactWrite as ArtifactWrite
from orgrebase.runtime_contracts import StoredArtifact as StoredArtifact
from orgrebase.runtime_contracts import WorkflowIdentity as WorkflowIdentity
from orgrebase.workspace.pricing import PricedQuote, PricingPolicy, QuoteBasket


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


ExtendedEvidenceClass = EvidenceClass | Literal[
    "DESIGN", "LIVE_MODEL", "LOCAL_OLLAMA_MODEL", "USER_STUDY"
]


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
    transport_mode: Literal[
        "LOCAL_DETERMINISTIC",
        "CONTROLLED_LOCAL_AGENTTEAMS",
        "LIVE_AGENTTEAMS",
    ]
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
    source_digest_scheme: Literal["VERSIONED_OBJECT_SHA256_V1"] | None = None

    @model_serializer(mode="wrap")
    def serialize_source_scheme(self, handler):
        data = handler(self)
        if self.source_digest_scheme is None:
            data.pop("source_digest_scheme", None)
        return data

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
    source_digest_scheme: Literal["VERSIONED_OBJECT_SHA256_V1"] | None = None

    @model_serializer(mode="wrap")
    def serialize_source_scheme(self, handler):
        data = handler(self)
        if self.source_digest_scheme is None:
            data.pop("source_digest_scheme", None)
        return data

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
    quote_basket: QuoteBasket | None = None
    pricing_policy: PricingPolicy | None = None

    @model_validator(mode="after")
    def paired_pricing_inputs(self):
        if (self.quote_basket is None) != (self.pricing_policy is None):
            raise ValueError("QUOTE_PRICING_INPUT_PAIR_REQUIRED")
        return self


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
    pricing: PricedQuote | None = None
    rebased_from: str | None = None
    rebase_change_set: str | None = None
    context_manifest: str | None = None

    @model_serializer(mode="wrap")
    def serialize_pricing(self, handler):
        data = handler(self)
        if self.pricing is None:
            data.pop("pricing", None)
        return data


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


class OACActivationConsumptionReceipt(ContentAddressedModel):
    """Exact OAC activation consumed by one Quote v1 formation transaction.

    This receipt records a precondition accepted by the deterministic control
    plane.  It does not grant business approval and carries no canonical write
    authority of its own.
    """

    schema_version: Literal["orgrebase.oac-activation-consumption-receipt.v1"] = (
        "orgrebase.oac-activation-consumption-receipt.v1"
    )
    status: Literal["CONSUMED_BY_QUOTE_FORMATION"] = (
        "CONSUMED_BY_QUOTE_FORMATION"
    )
    adaptation_run_id: str = Field(min_length=1)
    activation_binding_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    adapter_capsule_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_run_id: str = Field(min_length=1)
    task_request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    formation_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    quote_ref: str = Field(min_length=1)
    consumed_at: str = Field(min_length=1)
    consumption_phase: Literal["QUOTE_V1_ATOMIC_PRECONDITION"] = (
        "QUOTE_V1_ATOMIC_PRECONDITION"
    )
    binding_effect: Literal["QUOTE_FORMATION_PRECONDITION_ONLY"] = (
        "QUOTE_FORMATION_PRECONDITION_ONLY"
    )
    canonical_write_authority: Literal["ORGREBASE_CONTROL_PLANE"] = (
        "ORGREBASE_CONTROL_PLANE"
    )
    canonical_target_writes: Literal[0] = 0
    claim_ceiling: Literal[
        "OAC_BINDING_CONSUMED_NOT_BUSINESS_APPROVAL_OR_PRODUCTION_PROOF"
    ] = "OAC_BINDING_CONSUMED_NOT_BUSINESS_APPROVAL_OR_PRODUCTION_PROOF"


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


class PreparedFormationBundle(ContentAddressedModel):
    request_digest: str
    idempotency_key: str
    deliverable: VersionedObject
    graph_pointer: VersionedObject
    artifact_writes: tuple[ArtifactWrite, ...]
    task_receipt: TaskReceipt
    event_type: Literal["WORKSPACE_TASK_COMMITTED"] = "WORKSPACE_TASK_COMMITTED"
    event_payload: dict[str, JsonValue]


class PreparedSuccessorEvidence(ContentAddressedModel):
    successor_objects: tuple[VersionedObject, ...]
    successor_artifact_writes: tuple[ArtifactWrite, ...]
    graph_pointer: VersionedObject
    successor_snapshot_ref: str
    successor_snapshot_digest: str


class DomainPack(ContentAddressedModel):
    """Reusable business contract without enterprise identities or future values."""

    id: str
    revision: str
    template_ref: str
    mutable_slots: tuple[str, ...]

    @classmethod
    def enterprise_quote(cls, template_ref: str) -> DomainPack:
        priced = template_ref == "template:enterprise_quote@v2"
        return cls(id="domain:enterprise-quote", revision="r2" if priced else "r1", template_ref=template_ref,
                   mutable_slots=("launch_date", "currency", "product_plan")
                   + (("quote_basket", "pricing_policy") if priced else ()))


class EnterpriseResourceBinding(FrozenModel):
    slot_id: str
    object_id: str
    domain_id: str
    owner_id: str


class EnterpriseBinding(ContentAddressedModel):
    organization_id: str
    quote_object_id: str
    domain_pack_digest: str
    resources: tuple[EnterpriseResourceBinding, ...]

    @model_validator(mode="after")
    def unique_resources(self) -> EnterpriseBinding:
        for attribute in ("slot_id", "object_id"):
            values = [getattr(item, attribute) for item in self.resources]
            if len(values) != len(set(values)):
                raise ValueError("ENTERPRISE_RESOURCE_BINDING_AMBIGUOUS")
        return self


class WorkspaceChangeSpec(ContentAddressedModel):
    operation: Literal["UPDATE", "READMIT"] = "UPDATE"

    @model_serializer(mode="wrap")
    def serialize_operation(self, handler):
        data = handler(self)
        if self.operation == "UPDATE":
            data.pop("operation", None)
        return data

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
    native_execution: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def serialize_native_execution(self, handler):
        data = handler(self)
        if self.native_execution is None:
            data.pop("native_execution", None)
        return data


class QuotePricingComparison(FrozenModel):
    predecessor_ref: str
    predecessor_digest: str
    proposal_digest: str
    snapshot_digest: str
    before: PricedQuote
    after: PricedQuote


class WorkspacePreviewBundle(FrozenModel):
    change_spec: WorkspaceChangeSpec
    snapshot_ref: str
    snapshot_digest: str
    change_set: ChangeSetRevision
    preview: ImpactPreview
    minimal_rebase_certificate: MinimalRebaseCertificate
    advisory: VerifiedAdvisoryBundle
    run_envelope: RunEnvelope
    pricing_comparison: QuotePricingComparison | None = None

    @model_serializer(mode="wrap")
    def serialize_pricing_comparison(self, handler):
        data = handler(self)
        if self.pricing_comparison is None:
            data.pop("pricing_comparison", None)
        return data


class WorkspaceApprovalBinding(ContentAddressedModel):
    """Immutable authority/freshness envelope for one persisted Approval."""

    schema_version: Literal["orgrebase.workspace-approval-binding.v1"] = (
        "orgrebase.workspace-approval-binding.v1"
    )
    id: str
    approval_ref: str
    approval_digest: str
    change_kind: str
    workflow_run_id: str
    run_nonce: str
    pack_digest: str | None
    profile_digest: str
    predecessor_ref: str
    predecessor_digest: str
    change_set_digest: str
    preview_digest: str
    minimal_rebase_certificate_digest: str
    owner_id: str
    target_writes: Literal[0] = 0


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


class ModelInputProjection(ContentAddressedModel):
    """Already-admitted input material; a digest alone is not model context."""

    ref: str = Field(min_length=1)
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    domain_id: str = Field(min_length=1)
    object_ids: tuple[str, ...]
    content: JsonValue
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content(self) -> ModelInputProjection:
        if self.content is None or self.content_digest != sha256_digest(self.content):
            raise ValueError("MODEL_INPUT_CONTENT_DIGEST_MISMATCH")
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("MODEL_INPUT_OBJECTS_DUPLICATED")
        return self


class ModelRequestV2(ContentAddressedModel):
    """Explicit native protocol request, separate from frozen V1 records."""

    contract_version: Literal["2"] = "2"
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    task_ref: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    object_ids: tuple[str, ...]
    input_projections: tuple[ModelInputProjection, ...] = Field(min_length=1)
    schema_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_template_ref: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    prompt_template_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider: Literal["openai-responses"] = "openai-responses"
    model_id: str = Field(min_length=1, max_length=128)
    expected_model_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_output_tokens: int = Field(gt=0, le=32768)
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    attempt: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_inputs(self) -> ModelRequestV2:
        if self.prompt_template_digest != sha256_digest(self.prompt_template):
            raise ValueError("MODEL_PROMPT_TEMPLATE_DIGEST_MISMATCH")
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("MODEL_REQUEST_OBJECTS_DUPLICATED")
        refs = [item.ref for item in self.input_projections]
        if len(refs) != len(set(refs)):
            raise ValueError("MODEL_INPUT_REFS_DUPLICATED")
        if any(item.domain_id != self.domain_id for item in self.input_projections):
            raise ValueError("MODEL_INPUT_DOMAIN_MISMATCH")
        if {oid for item in self.input_projections for oid in item.object_ids} != set(self.object_ids):
            raise ValueError("MODEL_INPUT_SCOPE_MISMATCH")
        return self


class ModelResponseReceiptV2(ContentAddressedModel):
    """Observed protocol outcome. Missing usage or model metadata remains unknown."""

    contract_version: Literal["2"] = "2"
    id: str
    request_ref: str
    request_digest: str
    status: Literal["VALID", "ABSTAIN", "SCHEMA_ERROR", "PROVIDER_ERROR", "NOT_RUN", "INCOMPLETE", "CANCELLED"]
    dispatch_state: Literal["NOT_SENT", "SENT_UNKNOWN", "RESPONSE_RECEIVED"]
    value: JsonValue | None = None
    output_digest: str | None = None
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    provider: Literal["openai-responses"] = "openai-responses"
    requested_model_id: str
    observed_model_id: str | None = None
    schema_digest: str
    wire_schema_digest: str | None = None
    prompt_digest: str | None = None
    body_digest: str | None = None
    schema_valid: bool = False
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    error_code: str | None = None
    observed_at: str
    evidence_class: Literal["LIVE_MODEL", "MODEL_ATTEMPT", "NOT_RUN"]

    @model_validator(mode="after")
    def validate_outcome(self) -> ModelResponseReceiptV2:
        observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("MODEL_OBSERVATION_TIMEZONE_MISSING")
        if self.dispatch_state == "NOT_SENT":
            if self.evidence_class != "NOT_RUN" or self.provider_request_id or self.provider_response_id:
                raise ValueError("MODEL_NOT_SENT_OBSERVATION_MISMATCH")
        elif self.evidence_class == "NOT_RUN" or self.status == "NOT_RUN":
            raise ValueError("MODEL_DISPATCHED_ATTEMPT_CANNOT_BE_NOT_RUN")
        if self.dispatch_state == "SENT_UNKNOWN" and (
            self.evidence_class != "MODEL_ATTEMPT" or self.provider_request_id or self.provider_response_id
        ):
            raise ValueError("MODEL_UNKNOWN_DISPATCH_OBSERVATION_MISMATCH")
        if self.dispatch_state != "RESPONSE_RECEIVED" and (
            self.observed_model_id or self.input_tokens is not None or self.output_tokens is not None
        ):
            raise ValueError("MODEL_UNOBSERVED_USAGE_OR_MODEL")
        if self.status == "VALID":
            if not self.schema_valid or self.value is None or self.output_digest != sha256_digest(self.value):
                raise ValueError("MODEL_VALID_RESPONSE_CONTENT_MISMATCH")
            if not self.observed_model_id or not self.provider_request_id:
                raise ValueError("MODEL_VALID_RESPONSE_OBSERVATION_MISSING")
            if self.error_code or self.evidence_class != "LIVE_MODEL" or self.dispatch_state != "RESPONSE_RECEIVED":
                raise ValueError("MODEL_VALID_RESPONSE_STATUS_MISMATCH")
        elif self.schema_valid or self.value is not None or self.output_digest is not None:
            raise ValueError("MODEL_FAILED_RESPONSE_CONTAINS_CANDIDATE")
        return self


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


class OACRuntimeCapsule(ContentAddressedModel):
    """Closed wire-level input required for one local OAC Runtime admission."""

    schema_version: Literal["orgrebase.oac-runtime-capsule.v1"] = (
        "orgrebase.oac-runtime-capsule.v1"
    )
    case_id: str
    plan_source: Literal[
        "OAC_REFERENCE_COMPILER",
        "OAC_PUBLIC_VERIFIER_ACCEPTED_FIXTURE",
    ]
    oac_source_manifest: dict[str, JsonValue]
    oac_source_fingerprint: str
    snapshot: dict[str, JsonValue]
    change: dict[str, JsonValue]
    plan: dict[str, JsonValue]
    plan_certificate: dict[str, JsonValue]
    runtime_binding: dict[str, JsonValue]
    runtime_bundle: dict[str, JsonValue]
    runtime_lowering_receipt: dict[str, JsonValue]
    oac_cli_version: str

    @model_validator(mode="after")
    def require_complete_capsule(self) -> OACRuntimeCapsule:
        expected = (
            (self.snapshot, "OrganizationSnapshot"),
            (self.change, "SemanticChangeSet"),
            (self.plan, "OrganizationPlan"),
            (self.plan_certificate, "PlanCertificate"),
            (self.runtime_binding, "RuntimeBinding"),
            (self.runtime_bundle, "ZeroEffectRuntimeBundle"),
            (self.runtime_lowering_receipt, "RuntimeLoweringReceipt"),
        )
        if any(resource.get("kind") != kind for resource, kind in expected):
            raise ValueError("OAC_RUNTIME_CAPSULE_KIND_SET_INVALID")
        return self


class OACFormationPreview(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-formation-preview.v1"] = (
        "orgrebase.oac-formation-preview.v1"
    )
    id: str
    capsule_digest: str
    policy_digest: str
    plan_digest: str
    certificate_digest: str
    runtime_binding_digest: str
    runtime_bundle_digest: str
    lowering_receipt_digest: str
    topology_digest: str
    obligation_contract_digest: str
    work_unit_refs: tuple[str, ...]
    step_count: int = Field(ge=1)
    runtime_owner_id: str
    status: Literal["READY_FOR_RUNTIME_OWNER_APPROVAL"] = (
        "READY_FOR_RUNTIME_OWNER_APPROVAL"
    )
    target_writes: Literal[0] = 0
    handler_execution: Literal["NOT_RUN"] = "NOT_RUN"
    agent_execution: Literal["NOT_RUN"] = "NOT_RUN"
    outcome_certificate: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    real_enterprise: Literal["NOT_RUN"] = "NOT_RUN"


class OACRuntimeApproval(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-runtime-approval.v1"] = (
        "orgrebase.oac-runtime-approval.v1"
    )
    id: str
    preview_ref: str
    preview_digest: str
    capsule_digest: str
    runtime_binding_digest: str
    runtime_owner_id: str
    command_id: str
    approved_at: str
    scope: Literal["LOCAL_FORMATION_CONTROL_PLANE_ONLY"] = (
        "LOCAL_FORMATION_CONTROL_PLANE_ONLY"
    )
    target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def require_canonical_runtime_owner_command(self) -> OACRuntimeApproval:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.command_id) is None:
            raise ValueError("OAC_RUNTIME_APPROVAL_COMMAND_ID_INVALID")
        if self.id != f"oac-runtime-approval:{self.command_id}":
            raise ValueError("OAC_RUNTIME_APPROVAL_ID_COMMAND_MISMATCH")
        if (
            re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",
                self.approved_at,
            )
            is None
        ):
            raise ValueError("OAC_RUNTIME_APPROVAL_TIME_INVALID")
        try:
            datetime.fromisoformat(self.approved_at.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise ValueError("OAC_RUNTIME_APPROVAL_TIME_INVALID") from exc
        return self


class OACFormationControlRecord(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-formation-control-record.v1"] = (
        "orgrebase.oac-formation-control-record.v1"
    )
    id: str
    version: Literal["v1"] = "v1"
    capsule_digest: str
    preview_digest: str
    approval_digest: str
    plan_digest: str
    runtime_binding_digest: str
    runtime_bundle_digest: str
    topology_digest: str
    work_unit_refs: tuple[str, ...]
    state: Literal["ACTIVE"] = "ACTIVE"
    activated_at: str
    effect_scope: Literal["LOCAL_CONTROL_PLANE"] = "LOCAL_CONTROL_PLANE"
    target_writes: Literal[0] = 0
    handler_execution: Literal["NOT_RUN"] = "NOT_RUN"
    agent_execution: Literal["NOT_RUN"] = "NOT_RUN"


class RuntimeAdmissionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-runtime-admission-receipt.v1"] = (
        "orgrebase.oac-runtime-admission-receipt.v1"
    )
    id: str
    capsule_digest: str
    preview_digest: str
    approval_digest: str
    formation_ref: str
    formation_digest: str
    plan_digest: str
    certificate_digest: str
    runtime_binding_digest: str
    runtime_bundle_digest: str
    lowering_receipt_digest: str
    policy_digest: str
    checked: tuple[str, ...]
    status: Literal["LOCAL_RUNTIME_ADMISSION_PASS"] = "LOCAL_RUNTIME_ADMISSION_PASS"
    admitted_at: str
    target_writes: Literal[0] = 0
    runtime_control_plane_writes: Literal[1] = 1
    lowering_status: Literal["PRODUCED"] = "PRODUCED"
    lowering_runtime_invoked: Literal[False] = False
    handler_execution: Literal["NOT_RUN"] = "NOT_RUN"
    agent_execution: Literal["NOT_RUN"] = "NOT_RUN"
    outcome_certificate: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    real_enterprise: Literal["NOT_RUN"] = "NOT_RUN"



# Backward-compatible aliases used by older v4/v5 documents.
StructuredModelRequest = ModelRequest
StructuredModelResponse = ModelResponseReceipt
DatasetAssetRecord = DatasetLicenseRecord
MetricResult = EvaluationMetric
EvaluationRun = EvaluationReport
