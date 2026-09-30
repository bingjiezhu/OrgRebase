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
from orgrebase.digest import canonical_json, sha256_digest
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


class DiscountMemoTaskLiterals(FrozenModel):
    owner: str
    customer_id: str
    quote_object_id: str


class ResolvedDiscountMemoInputs(FrozenModel):
    currency: str
    quote_basket: QuoteBasket
    pricing_policy: PricingPolicy


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


class DiscountMemoComparison(FrozenModel):
    before_discount_bps: int
    after_discount_bps: int
    before_discount_amount: str
    after_discount_amount: str
    discount_amount_delta: str
    before_net_amount: str
    after_net_amount: str
    net_amount_delta: str
    before_total: str
    after_total: str
    total_delta: str


class DiscountMemoPayload(FrozenModel):
    owner: str
    deliverable_kind: Literal["DISCOUNT_MEMO"] = "DISCOUNT_MEMO"
    customer_id: str
    quote_object_id: str
    pricing: PricedQuote
    pricing_basis_digest: str
    previous_pricing: PricedQuote | None = None
    comparison: DiscountMemoComparison | None = None
    reason_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "NOT_A_POLICY_APPROVAL",
        "NO_FX_CONVERSION",
        "NO_EXTERNAL_EFFECT",
    )
    last_price_change_set_ref: str | None = None
    last_price_change_set_digest: str | None = None
    rebased_from: str | None = None
    context_manifest: str | None = None

    @model_validator(mode="after")
    def exact_change_pair(self):
        if (self.last_price_change_set_ref is None) != (self.last_price_change_set_digest is None):
            raise ValueError("DISCOUNT_MEMO_CHANGE_BINDING_INCOMPLETE")
        changed = self.last_price_change_set_ref is not None
        if changed != (self.previous_pricing is not None) or changed != (self.comparison is not None):
            raise ValueError("DISCOUNT_MEMO_COMPARISON_BINDING_INCOMPLETE")
        if changed:
            if self.reason_refs != (self.last_price_change_set_ref,):
                raise ValueError("DISCOUNT_MEMO_REASON_BINDING_INVALID")
            comparison = self.comparison
            previous = self.previous_pricing
            if comparison is None or previous is None:  # pragma: no cover - guarded above
                raise ValueError("DISCOUNT_MEMO_COMPARISON_BINDING_INCOMPLETE")
            if (
                comparison.before_discount_bps != previous.discount_rate_bps
                or comparison.after_discount_bps != self.pricing.discount_rate_bps
                or comparison.before_discount_amount != previous.discount_amount
                or comparison.after_discount_amount != self.pricing.discount_amount
                or comparison.before_net_amount != previous.net_amount
                or comparison.after_net_amount != self.pricing.net_amount
                or comparison.before_total != previous.total
                or comparison.after_total != self.pricing.total
            ):
                raise ValueError("DISCOUNT_MEMO_COMPARISON_MISMATCH")
        elif self.reason_refs:
            raise ValueError("DISCOUNT_MEMO_REASON_WITHOUT_CHANGE")
        expected = sha256_digest(
            {
                "quote_object_id": self.quote_object_id,
                "currency": self.pricing.currency,
                "basket_digest": self.pricing.basket_digest,
                "policy_digest": self.pricing.policy_digest,
            }
        )
        if self.pricing_basis_digest != expected:
            raise ValueError("DISCOUNT_MEMO_PRICING_BASIS_MISMATCH")
        return self


class DeliverableMemberBinding(ContentAddressedModel):
    object_id: str
    deliverable_kind: Literal["QUOTE", "DISCOUNT_MEMO"]
    template_ref: str
    template_digest: str
    output_schema_ref: str
    adapter_ref: str
    adapter_digest: str
    owner_id: str
    approval_scopes: tuple[str, ...]

    @model_validator(mode="after")
    def exact_scopes(self):
        if not self.approval_scopes or self.approval_scopes != tuple(sorted(set(self.approval_scopes))):
            raise ValueError("DELIVERABLE_MEMBER_APPROVAL_SCOPES_INVALID")
        expected_adapter = sha256_digest(
            {
                "adapter_ref": self.adapter_ref,
                "template_digest": self.template_digest,
                "output_schema_ref": self.output_schema_ref,
            }
        )
        if self.adapter_digest != expected_adapter:
            raise ValueError("DELIVERABLE_MEMBER_ADAPTER_DIGEST_INVALID")
        return self


class DeliverableSetProfile(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-set-profile.v1"] = "orgrebase.deliverable-set-profile.v1"
    id: str
    revision: str
    organization_id: str
    base_profile_digest: str
    pack_digest: str
    runtime_revision: str
    members: tuple[DeliverableMemberBinding, ...]
    external_effects: Literal["DISABLED"] = "DISABLED"

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.revision}"

    @model_validator(mode="after")
    def bounded_quote_memo_profile(self):
        if self.members != tuple(sorted(self.members, key=lambda item: item.object_id)):
            raise ValueError("DELIVERABLE_SET_MEMBER_ORDER_INVALID")
        if len(self.members) != 2 or {item.deliverable_kind for item in self.members} != {
            "QUOTE",
            "DISCOUNT_MEMO",
        }:
            raise ValueError("DELIVERABLE_SET_PROFILE_REQUIRES_QUOTE_AND_MEMO")
        if len({item.object_id for item in self.members}) != 2:
            raise ValueError("DELIVERABLE_SET_MEMBER_ID_DUPLICATE")
        expected_templates = {
            "QUOTE": "template:enterprise_quote@v2",
            "DISCOUNT_MEMO": "template:discount_exception_memo@v2",
        }
        if any(item.template_ref != expected_templates[item.deliverable_kind] for item in self.members):
            raise ValueError("DELIVERABLE_SET_TEMPLATE_UNSUPPORTED")
        return self


class DeliverableSetBinding(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-set-binding.v1"] = "orgrebase.deliverable-set-binding.v1"
    id: str
    version: str
    workspace_id: str
    profile_ref: str
    profile_digest: str
    base_profile_digest: str
    pack_digest: str
    runtime_revision: str
    members: tuple[DeliverableMemberBinding, ...]
    formed_at: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


class DeliverableCandidateMember(ContentAddressedModel):
    object_id: str
    deliverable_kind: Literal["QUOTE", "DISCOUNT_MEMO"]
    disposition: Literal["REBUILD", "PRESERVE_WITHIN_BOUNDARY", "HOLD_FOR_REVIEW"]
    predecessor_ref: str
    predecessor_digest: str
    candidate_ref: str
    candidate_payload_digest: str
    evidence_mode: Literal[
        "CANDIDATE", "PREDECESSOR_PRESERVED", "PREDECESSOR_UNRESOLVED"
    ]
    review_projection_ref: str
    review_projection_digest: str
    context_ref: str
    context_digest: str
    trace_ref: str
    trace_digest: str
    coverage_ref: str
    coverage_digest: str
    manifest_ref: str
    manifest_digest: str
    owner_id: str
    required_scopes: tuple[str, ...]
    reason_code: str

    @model_validator(mode="after")
    def evidence_semantics(self):
        expected = {
            "REBUILD": "CANDIDATE",
            "PRESERVE_WITHIN_BOUNDARY": "PREDECESSOR_PRESERVED",
            "HOLD_FOR_REVIEW": "PREDECESSOR_UNRESOLVED",
        }[self.disposition]
        if self.evidence_mode != expected:
            raise ValueError("DELIVERABLE_CANDIDATE_EVIDENCE_MODE_INVALID")
        if self.disposition == "REBUILD" and not self.required_scopes:
            raise ValueError("DELIVERABLE_CANDIDATE_APPROVAL_SCOPE_MISSING")
        if self.disposition != "REBUILD" and self.required_scopes:
            raise ValueError("DELIVERABLE_PRESERVE_APPROVAL_SCOPE_FORBIDDEN")
        return self


class DeliverableReviewProjection(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-review-projection.v1"] = (
        "orgrebase.deliverable-review-projection.v1"
    )
    id: str
    candidate_ref: str
    candidate_payload_digest: str
    deliverable_kind: Literal["QUOTE", "DISCOUNT_MEMO"]
    owner_id: str
    change_set_digest: str
    binding_digest: str
    safe_payload: dict[str, JsonValue]
    forbidden_fields_checked: tuple[str, ...]
    external_effects: Literal["DISABLED"] = "DISABLED"


class DeliverableCandidateSet(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-candidate-set.v1"] = (
        "orgrebase.deliverable-candidate-set.v1"
    )
    id: str
    change_set_ref: str
    change_set_digest: str
    preview_digest: str
    binding_ref: str
    binding_digest: str
    runtime_revision: str
    snapshot_ref: str
    snapshot_digest: str
    members: tuple[DeliverableCandidateMember, ...]
    state: Literal["READY", "UNKNOWN"]

    @model_validator(mode="after")
    def exact_candidate_members(self):
        if self.members != tuple(sorted(self.members, key=lambda item: item.object_id)):
            raise ValueError("DELIVERABLE_CANDIDATE_MEMBER_ORDER_INVALID")
        if len(self.members) != 2 or {item.deliverable_kind for item in self.members} != {
            "QUOTE",
            "DISCOUNT_MEMO",
        }:
            raise ValueError("DELIVERABLE_CANDIDATE_SET_INCOMPLETE")
        expected_state = (
            "UNKNOWN" if any(item.disposition == "HOLD_FOR_REVIEW" for item in self.members) else "READY"
        )
        if self.state != expected_state:
            raise ValueError("DELIVERABLE_CANDIDATE_STATE_INVALID")
        return self


class PreparedDeliverableCandidateSet(ContentAddressedModel):
    candidate_set: DeliverableCandidateSet
    artifact_writes: tuple[ArtifactWrite, ...]

    @model_validator(mode="after")
    def unique_artifacts(self):
        ids = tuple(item.artifact_id for item in self.artifact_writes)
        if len(ids) != len(set(ids)):
            raise ValueError("DELIVERABLE_CANDIDATE_ARTIFACT_DUPLICATE")
        return self


class DeliverableApprovalDecision(ContentAddressedModel):
    operation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    command_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    owner_id: str
    actor_id: str
    identity_issuer: str
    identity_subject: str
    identity_mode: str
    candidate_set_digest: str
    authority_revision: str
    scopes: tuple[str, ...]
    decision: Literal["APPROVED", "REJECTED"]
    approved_at: str
    expires_at: str
    method: str

    @model_serializer(mode="wrap")
    def serialize_operation_binding(self, handler):
        data = handler(self)
        if self.operation_id is None:
            data.pop("operation_id", None)
            data.pop("command_digest", None)
        return data

    @model_validator(mode="after")
    def exact_scope_order(self):
        if not self.scopes or self.scopes != tuple(sorted(set(self.scopes))):
            raise ValueError("DELIVERABLE_APPROVAL_SCOPE_INVALID")
        if (self.operation_id is None) != (self.command_digest is None):
            raise ValueError("DELIVERABLE_APPROVAL_OPERATION_BINDING_INCOMPLETE")
        return self


class DeliverableApprovalSet(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-approval-set.v1"] = "orgrebase.deliverable-approval-set.v1"
    id: str
    candidate_set_digest: str
    decisions: tuple[DeliverableApprovalDecision, ...]
    status: Literal["COMPLETE", "INCOMPLETE", "REJECTED"]

    @model_validator(mode="after")
    def unique_decision_owners(self):
        owners = tuple(item.owner_id for item in self.decisions)
        if owners != tuple(sorted(set(owners))):
            raise ValueError("DELIVERABLE_APPROVAL_OWNER_SET_INVALID")
        if any(item.candidate_set_digest != self.candidate_set_digest for item in self.decisions):
            raise ValueError("DELIVERABLE_APPROVAL_CANDIDATE_MISMATCH")
        if any(item.decision == "REJECTED" for item in self.decisions):
            expected = "REJECTED"
        else:
            expected = "COMPLETE" if self.decisions else "INCOMPLETE"
        if self.status == "REJECTED" and expected != "REJECTED":
            raise ValueError("DELIVERABLE_APPROVAL_STATUS_INVALID")
        return self


class DeliverableApplyMember(FrozenModel):
    object_id: str
    deliverable_kind: Literal["QUOTE", "DISCOUNT_MEMO"]
    disposition: Literal["REBUILD", "PRESERVE_WITHIN_BOUNDARY"]
    predecessor_ref: str
    result_ref: str
    result_digest: str
    reason_code: str


class DeliverableSetApplyReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-set-apply-receipt.v1"] = (
        "orgrebase.deliverable-set-apply-receipt.v1"
    )
    id: str
    base_rebase_receipt_ref: str
    base_rebase_receipt_digest: str
    binding_ref: str
    binding_digest: str
    candidate_set_digest: str
    approval_set_digest: str
    members: tuple[DeliverableApplyMember, ...]
    graph_pointer_ref: str
    snapshot_ref: str
    snapshot_digest: str
    status: Literal["COMPLETED"] = "COMPLETED"
    external_effects: Literal["DISABLED"] = "DISABLED"
    committed_at: str


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


class DeliverableSetFormationReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.deliverable-set-formation-receipt.v1"] = (
        "orgrebase.deliverable-set-formation-receipt.v1"
    )
    id: str
    binding_ref: str
    binding_digest: str
    task_receipt_refs: tuple[str, ...]
    task_receipt_digests: tuple[str, ...]
    deliverable_refs: tuple[str, ...]
    deliverable_digests: tuple[str, ...]
    graph_snapshot_ref: str
    graph_snapshot_digest: str
    graph_pointer_ref: str
    committed_at: str
    status: Literal["COMPLETED"] = "COMPLETED"
    external_effects: Literal["DISABLED"] = "DISABLED"


class PreparedDeliverableSetFormation(ContentAddressedModel):
    request_set_digest: str
    idempotency_key: str
    profile: DeliverableSetProfile
    binding: DeliverableSetBinding
    quote_prepared: PreparedFormationBundle
    deliverables: tuple[VersionedObject, ...]
    graph_pointer: VersionedObject
    artifact_writes: tuple[ArtifactWrite, ...]
    task_receipts: tuple[TaskReceipt, ...]
    formation_receipt: DeliverableSetFormationReceipt
    event_type: Literal["WORKSPACE_DELIVERABLE_SET_COMMITTED"] = "WORKSPACE_DELIVERABLE_SET_COMMITTED"
    event_payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def exact_formation_members(self):
        if self.deliverables != tuple(sorted(self.deliverables, key=lambda item: item.id)):
            raise ValueError("DELIVERABLE_FORMATION_MEMBER_ORDER_INVALID")
        if len(self.deliverables) != 2 or {
            item.payload.get("deliverable_kind") for item in self.deliverables
        } != {"QUOTE", "DISCOUNT_MEMO"}:
            raise ValueError("DELIVERABLE_FORMATION_SET_INCOMPLETE")
        if len(self.task_receipts) != 2:
            raise ValueError("DELIVERABLE_FORMATION_RECEIPT_SET_INCOMPLETE")
        return self


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
    schema_version: Literal["orgrebase.workspace-rebase-receipt.v2"] | None = None
    id: str
    base_rebase_receipt_ref: str
    base_rebase_receipt_digest: str
    successor_object_refs: tuple[str, ...]
    successor_trace_refs: tuple[str, ...]
    successor_manifest_refs: tuple[str, ...]
    successor_snapshot_ref: str
    successor_snapshot_digest: str
    graph_pointer_ref: str
    deliverable_set_receipt_ref: str | None = None
    deliverable_set_receipt_digest: str | None = None
    status: Literal["COMPLETED"] = "COMPLETED"
    committed_at: str

    @model_serializer(mode="wrap")
    def serialize_deliverable_set(self, handler):
        data = handler(self)
        if self.schema_version is None:
            data.pop("schema_version", None)
        if self.deliverable_set_receipt_ref is None:
            data.pop("deliverable_set_receipt_ref", None)
            data.pop("deliverable_set_receipt_digest", None)
        return data

    @model_validator(mode="after")
    def deliverable_set_pair(self):
        if (self.deliverable_set_receipt_ref is None) != (self.deliverable_set_receipt_digest is None):
            raise ValueError("WORKSPACE_DELIVERABLE_SET_RECEIPT_BINDING_INCOMPLETE")
        if (self.schema_version is None) != (self.deliverable_set_receipt_ref is None):
            raise ValueError("WORKSPACE_REBASE_RECEIPT_SCHEMA_INVALID")
        return self


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


class ModelRequestV3(ContentAddressedModel):
    """Vertex-native candidate request; V1/V2 records retain their original contracts."""

    contract_version: Literal["3"] = "3"
    provider: Literal["vertex-ai"] = "vertex-ai"
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    task_ref: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    object_ids: tuple[str, ...] = Field(min_length=1)
    input_projections: tuple[ModelInputProjection, ...] = Field(min_length=1)
    projection_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_template_ref: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    prompt_template_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_id: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    max_output_tokens: int = Field(gt=0, le=32768)
    thinking_level: Literal["LOW"] = "LOW"
    attempt: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_inputs(self) -> ModelRequestV3:
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
        projections = [item.model_dump(mode="json") for item in self.input_projections]
        if self.projection_digest != sha256_digest(projections):
            raise ValueError("MODEL_INPUT_PROJECTION_DIGEST_MISMATCH")
        return self


class ModelResponseReceiptV3(ContentAddressedModel):
    """Vertex-native outcome. Absent provider usage remains unknown, never zero."""

    contract_version: Literal["3"] = "3"
    id: str = Field(min_length=1)
    request_ref: str = Field(min_length=1)
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal[
        "VALID",
        "ABSTAIN",
        "SCHEMA_ERROR",
        "PROVIDER_ERROR",
        "NOT_RUN",
        "INCOMPLETE",
        "CANCELLED",
    ]
    dispatch_state: Literal["NOT_SENT", "SENT_UNKNOWN", "RESPONSE_RECEIVED"]
    value: JsonValue | None = None
    output_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    provider_request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.:/-]{0,255}$",
    )
    provider: Literal["vertex-ai"] = "vertex-ai"
    requested_model_id: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    observed_model_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.:/-]{0,255}$",
    )
    projection_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    wire_schema_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    body_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    schema_valid: bool = False
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    thinking_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    error_code: str | None = None
    observed_at: str
    evidence_class: Literal["LIVE_MODEL", "MODEL_ATTEMPT", "NOT_RUN"]

    @model_validator(mode="after")
    def validate_outcome(self) -> ModelResponseReceiptV3:
        observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("MODEL_OBSERVATION_TIMEZONE_MISSING")
        usage = (
            self.input_tokens,
            self.output_tokens,
            self.thinking_tokens,
            self.total_tokens,
            self.cached_tokens,
        )
        if self.dispatch_state == "NOT_SENT":
            if self.evidence_class != "NOT_RUN" or self.provider_request_id or self.observed_model_id:
                raise ValueError("MODEL_NOT_SENT_OBSERVATION_MISMATCH")
            if self.status != "NOT_RUN" or any(value is not None for value in usage):
                raise ValueError("MODEL_NOT_SENT_STATUS_MISMATCH")
        elif self.evidence_class == "NOT_RUN" or self.status == "NOT_RUN":
            raise ValueError("MODEL_DISPATCHED_ATTEMPT_CANNOT_BE_NOT_RUN")
        if self.dispatch_state == "SENT_UNKNOWN" and (
            self.evidence_class != "MODEL_ATTEMPT"
            or self.provider_request_id
            or self.observed_model_id
            or any(value is not None for value in usage)
        ):
            raise ValueError("MODEL_UNKNOWN_DISPATCH_OBSERVATION_MISMATCH")
        if self.status == "VALID":
            if not self.schema_valid or self.value is None or self.output_digest != sha256_digest(self.value):
                raise ValueError("MODEL_VALID_RESPONSE_CONTENT_MISMATCH")
            if not self.provider_request_id or self.observed_model_id != self.requested_model_id:
                raise ValueError("MODEL_VALID_RESPONSE_OBSERVATION_MISSING")
            if not all((self.wire_schema_digest, self.prompt_digest, self.body_digest)):
                raise ValueError("MODEL_VALID_RESPONSE_WIRE_BINDING_MISSING")
            if (
                self.error_code
                or self.evidence_class != "LIVE_MODEL"
                or self.dispatch_state != "RESPONSE_RECEIVED"
            ):
                raise ValueError("MODEL_VALID_RESPONSE_STATUS_MISMATCH")
        elif self.schema_valid or self.value is not None or self.output_digest is not None:
            raise ValueError("MODEL_FAILED_RESPONSE_CONTAINS_CANDIDATE")
        return self


class ModelAdviceLessonV4(FrozenModel):
    """Identity of a selected lesson; full authorized text is in advice_text."""

    ref: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ModelAdviceContextV4(ContentAddressedModel):
    """Frozen content selected by another authority, never a release grant."""

    profile: Literal["workspace-change-explanation-v1"] = "workspace-change-explanation-v1"
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    run_nonce: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    case_revision: str = Field(min_length=1)
    query_ref: str = Field(min_length=1)
    query_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_mode: Literal["EVALUATION_ONLY", "SHADOW_ONLY", "ADOPTED"]
    head_ref: str = Field(min_length=1)
    head_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    head_generation: int = Field(ge=0)
    package_ref: str = Field(min_length=1)
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protected_kernel_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    compiler_version: str = Field(min_length=1)
    instruction_ref: Literal["instructions/change-explanation.md"] = "instructions/change-explanation.md"
    instruction_text: str = Field(min_length=1)
    instruction_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reference_ref: Literal["references/change-explanation.md"] = "references/change-explanation.md"
    reference_text: str = Field(min_length=1)
    reference_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    memory_snapshot_ref: str = Field(min_length=1)
    memory_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    recall_manifest_ref: str = Field(min_length=1)
    recall_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selection_mode: Literal["NO_MEMORY", "RECALL_EMPTY", "RECALLED"]
    recall_coverage: Literal["EMPTY", "COMPLETE", "PARTIAL_COVERAGE"]
    lessons: tuple[ModelAdviceLessonV4, ...] = Field(max_length=3)
    advice_text: str
    advice_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    advice_byte_count: int = Field(ge=0, le=4096)
    memory_reserved_bytes: int = Field(default=4096, ge=0, le=8192)
    guidance_budget_bytes: int = Field(default=4096, ge=0, le=8192)

    @model_validator(mode="after")
    def validate_advice(self) -> ModelAdviceContextV4:
        from orgrebase.workspace.experience_contracts import exact_bytes_digest

        if (
            self.instruction_digest != exact_bytes_digest(self.instruction_text.encode("utf-8"))
            or self.reference_digest != exact_bytes_digest(self.reference_text.encode("utf-8"))
        ):
            raise ValueError("MODEL_ADVICE_RESOURCE_DIGEST_MISMATCH")
        if self.memory_reserved_bytes + self.guidance_budget_bytes > 8192:
            raise ValueError("MODEL_ADVICE_TOTAL_BUDGET_EXCEEDED")
        if (self.selection_mode == "RECALLED") != bool(self.lessons):
            raise ValueError("MODEL_ADVICE_SELECTION_MISMATCH")
        if self.selection_mode == "NO_MEMORY" and self.recall_coverage != "EMPTY":
            raise ValueError("MODEL_ADVICE_EMPTY_SELECTION_COVERAGE_INVALID")
        if len({item.ref for item in self.lessons}) != len(self.lessons):
            raise ValueError("MODEL_ADVICE_LESSON_DUPLICATED")
        memory = self.advice_text.encode("utf-8")
        if self.advice_byte_count != len(memory) or self.advice_bytes_digest != exact_bytes_digest(memory):
            raise ValueError("MODEL_ADVICE_BYTES_DIGEST_MISMATCH")
        if self.selection_mode != "RECALLED" and memory:
            raise ValueError("MODEL_ADVICE_EMPTY_SELECTION_HAS_TEXT")
        guidance = {
            "instruction_ref": self.instruction_ref,
            "instruction_text": self.instruction_text,
            "reference_ref": self.reference_ref,
            "reference_text": self.reference_text,
        }
        memory_wire = (
            canonical_json({
                "lessons": [item.model_dump(mode="json") for item in self.lessons],
                "advice_text": self.advice_text,
            }).encode("utf-8")
            if self.lessons else b""
        )
        if len(memory_wire) > self.memory_reserved_bytes:
            raise ValueError("MODEL_ADVICE_MEMORY_BUDGET_EXCEEDED")
        if len(canonical_json(guidance).encode("utf-8")) > self.guidance_budget_bytes:
            raise ValueError("MODEL_ADVICE_GUIDANCE_BUDGET_EXCEEDED")
        return self


class ModelRequestV4(ContentAddressedModel):
    """Finance candidate request with separate enterprise facts and advice."""

    contract_version: Literal["4"] = "4"
    provider: Literal["vertex-ai"]
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    task_ref: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    domain_id: Literal["finance"] = "finance"
    object_ids: tuple[str, ...] = Field(min_length=1)
    business_input_projections: tuple[ModelInputProjection, ...] = Field(min_length=1)
    business_projection_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    advice_context: ModelAdviceContextV4
    advice_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_template_ref: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    prompt_template_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_id: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    max_output_tokens: int = Field(gt=0, le=32768)
    thinking_level: Literal["LOW"] = "LOW"
    attempt: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_inputs(self) -> ModelRequestV4:
        if self.prompt_template_digest != sha256_digest(self.prompt_template):
            raise ValueError("MODEL_PROMPT_TEMPLATE_DIGEST_MISMATCH")
        if self.advice_context.tenant_id != self.tenant_id or self.advice_context.workspace_id != self.workspace_id:
            raise ValueError("MODEL_ADVICE_SCOPE_MISMATCH")
        if self.advice_digest != self.advice_context.digest:
            raise ValueError("MODEL_ADVICE_DIGEST_MISMATCH")
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("MODEL_REQUEST_OBJECTS_DUPLICATED")
        refs = [item.ref for item in self.business_input_projections]
        if len(refs) != len(set(refs)):
            raise ValueError("MODEL_INPUT_REFS_DUPLICATED")
        if any(item.domain_id != self.domain_id for item in self.business_input_projections):
            raise ValueError("MODEL_INPUT_DOMAIN_MISMATCH")
        if {oid for item in self.business_input_projections for oid in item.object_ids} != set(self.object_ids):
            raise ValueError("MODEL_INPUT_SCOPE_MISMATCH")
        projections = [item.model_dump(mode="json") for item in self.business_input_projections]
        if self.business_projection_digest != sha256_digest(projections):
            raise ValueError("MODEL_BUSINESS_PROJECTION_DIGEST_MISMATCH")
        return self


class ModelResponseReceiptV4(ModelResponseReceiptV3):
    """Observed V4 wire bindings; old V3 receipts keep their old bytes."""

    contract_version: Literal["4"] = "4"
    provider: Literal["vertex-ai"]
    business_projection_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    advice_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    business_wire_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    advice_wire_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_v4(self) -> ModelResponseReceiptV4:
        if self.projection_digest != self.business_projection_digest:
            raise ValueError("MODEL_V4_BUSINESS_PROJECTION_DIGEST_MISMATCH")
        if self.status == "VALID" and not all((self.business_wire_digest, self.advice_wire_digest)):
            raise ValueError("MODEL_VALID_RESPONSE_V4_WIRE_BINDING_MISSING")
        return self


CandidateModelRequest = Annotated[ModelRequestV2 | ModelRequestV3 | ModelRequestV4, Field(discriminator="contract_version")]
CandidateModelResponseReceipt = Annotated[
    ModelResponseReceiptV2 | ModelResponseReceiptV3 | ModelResponseReceiptV4,
    Field(discriminator="contract_version"),
]


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
