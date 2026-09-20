"""Fail-closed OAC pre-deployment adaptation for the Enterprise Quote Profile.

The module owns no business state and no Agent runtime.  It turns one already
admitted Enterprise Quote Pack into candidate mappings, validates the portable
OAC Source/Demand wire through the public sibling CLI, waits for an exact
owner-labelled admission, and emits an OrgRebase-owned adapter capsule.  The
service enforces actor authority and elapsed review time; the caller owns proof
that the actor label came from a real human identity.  All persisted objects
live in the existing :class:`StateStore` artifact/event ledger.
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import current_authorization
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, ContentAddressedModel, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.oac_quote_parity import (
    QuoteFormationParityReceipt,
    build_quote_formation_parity_receipt,
    load_golden_summary,
    verify_quote_formation_parity,
)
from orgrebase.workspace.oac_wire import OACBlackBoxCLI
from orgrebase.workspace.pilot import EnterpriseQuotePilotRuntime
from orgrebase.workspace.profile_contracts import (
    EnterpriseSeedProfile,
    RuntimeCompatibilityMode,
    SeedCompleteness,
    SeedComponentKind,
    SeedDataClass,
)
from orgrebase.workspace.templates import default_capability_cards

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GOLDEN_ROOT = PROJECT_ROOT / "evidence" / "golden-competition" / "latest" / "pilot"

_DRAFT_MEDIA = "application/vnd.orgrebase.oac-quote-adaptation-draft+json"
_APPROVAL_MEDIA = "application/vnd.orgrebase.oac-quote-adaptation-approval+json"
_CAPSULE_MEDIA = "application/vnd.orgrebase.oac-quote-adapter-capsule+json"
_BINDING_MEDIA = "application/vnd.orgrebase.oac-quote-activation-binding+json"
_AGENT_MAPPING_MEDIA = "application/vnd.orgrebase.oac-agent-mapping-receipt+json"
_DEPENDENCY_PROJECTION_MEDIA = "application/vnd.orgrebase.oac-dependency-projection-receipt+json"
_OAC_RESOURCE_MEDIA = "application/vnd.oac.resource+json"
_EFFECT_CEILING = "ZERO_EXTERNAL_EFFECTS"
_ARTIFACT_REVISION = "r2"
OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS = (
    "REVIEWED_SOURCE_TO_CONTRACT_SUMMARY",
    "ACCEPTED_DECLARED_UNKNOWNS",
    "UNDERSTAND_NO_BUSINESS_APPROVAL",
)
_DEPENDENCY_RELATION_PROJECTION: dict[
    str,
    tuple[str, str, str, str],
] = {
    # Workspace relation -> OAC relation, admission, required coverage, strength.
    # These are portable organizational facts only.  They do not copy the
    # Workspace impact graph or grant OAC runtime authority.
    "REQUIRES_POLICY": (
        "business_dependency",
        "admitted",
        "OWNER_DECLARED_COMPLETE",
        "HARD",
    ),
    "ASSUMES": (
        "operational_dependency",
        "candidate",
        "AGENT_INFERRED",
        "REVIEW",
    ),
    "USES_MESSAGE": (
        "traceability",
        "admitted",
        "CONTRACT_DECLARED",
        "INFORMATIONAL",
    ),
}
_DEPENDENCY_MAPPING_RULE_SET_DIGEST = sha256_digest(
    {
        "profile": "orgrebase.enterprise-quote-dependency-projection/v1",
        "relation_projection": _DEPENDENCY_RELATION_PROJECTION,
        "invariants": [
            "EXACT_WORKSPACE_ENDPOINTS",
            "STABLE_EDGE_ORDER",
            "UNKNOWN_RELATION_FAIL_CLOSED",
            "NO_IMPACT_RULE_PROJECTION",
            "ZERO_EXTERNAL_EFFECTS",
        ],
    }
)
_RULE_SET_DIGEST = sha256_digest(
    {
        "profile": "orgrebase.enterprise-quote-source-admission/v2",
        "dependency_mapping_rule_set_digest": _DEPENDENCY_MAPPING_RULE_SET_DIGEST,
        "rules": [
            "FIVE_ROOTS_EXACT",
            "EXACT_DEPENDENCY_PROJECTION_REQUIRED",
            "UNKNOWN_PRESERVED",
            "PROPOSER_AUTHORITY_REVIEWER_SEPARATED",
            "ZERO_EXTERNAL_EFFECTS",
        ],
    }
)

_TARGET_PATHS: dict[SeedComponentKind, tuple[str, ...]] = {
    SeedComponentKind.DOMAIN: (
        "OrganizationSnapshot.spec.nodes",
        "OrganizationalDemand.spec.subjectRefs",
    ),
    SeedComponentKind.KNOWLEDGE: ("OrganizationalDemand.spec.evidenceObligationRefs",),
    SeedComponentKind.AUTHORITY: (
        "OrganizationSnapshot.spec.roleDefinitions",
        "OrganizationSnapshot.spec.principals",
        "SourceAdmissionReceipt.spec.decisionAuthorityRef",
    ),
    SeedComponentKind.CAPABILITY: ("OrganizationSnapshot.spec.roleDefinitions[].responsibilityTypes",),
    SeedComponentKind.DEPENDENCY: (
        "OrganizationSnapshot.spec.dependencyEdges",
        "OrganizationalDemand.spec.evidenceObligationRefs",
    ),
}


def quote_adaptation_rule_set_digest() -> str:
    """Public content identity of the deterministic OAC source-admission rules."""

    return _RULE_SET_DIGEST


class OACAdaptationReviewGatePending(RuntimeError):
    """Machine-readable conflict raised before the four-second gate elapses."""

    code = "OAC_ADAPTATION_REVIEW_GATE_NOT_READY"

    def __init__(self, *, remaining_ms: int, not_before: str) -> None:
        self.remaining_ms = remaining_ms
        self.not_before = not_before
        super().__init__(f"{self.code}:remaining_ms={remaining_ms},not_before={not_before}")


class CandidateSemanticMapping(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-candidate-semantic-mapping.v1"] = (
        "orgrebase.oac-candidate-semantic-mapping.v1"
    )
    adaptation_run_id: str = Field(min_length=1)
    component_kind: SeedComponentKind
    source_root_ref: str = Field(min_length=1)
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    target_oac_paths: tuple[str, ...] = Field(min_length=1)
    declared_unknowns: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = _EFFECT_CEILING

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        if len(self.target_oac_paths) != len(set(self.target_oac_paths)):
            raise ValueError("OAC_ADAPTATION_TARGET_PATH_DUPLICATE")
        if len(self.declared_unknowns) != len(set(self.declared_unknowns)):
            raise ValueError("OAC_ADAPTATION_UNKNOWN_DUPLICATE")
        if bool(self.declared_unknowns) != bool(self.reason_codes):
            raise ValueError("OAC_ADAPTATION_UNKNOWN_REASON_BINDING_INVALID")
        return self


class OACAdaptationGap(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-adaptation-gap.v1"] = "orgrebase.oac-adaptation-gap.v1"
    component_kind: SeedComponentKind
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    source_ref: str = Field(min_length=1)
    owner_ref: str = Field(min_length=1)
    resolution_gate_ref: str = Field(min_length=1)
    status: Literal["OPEN"] = "OPEN"
    canonical_target_writes: Literal[0] = 0


class OACDependencyProjectionBinding(ContentAddressedModel):
    """One exact Workspace edge to portable OAC edge content binding."""

    schema_version: Literal["orgrebase.oac-dependency-projection-binding.v1"] = (
        "orgrebase.oac-dependency-projection-binding.v1"
    )
    workspace_edge_id: str = Field(min_length=1)
    workspace_edge_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    oac_edge_id: str = Field(min_length=1)
    oac_edge_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class OACDependencyProjectionReceipt(ContentAddressedModel):
    """Content-addressed proof of the bounded dependency projection.

    The receipt binds exact Enterprise Pack bytes to the already compiled
    Workspace universe and then to a portable OAC edge set.  It deliberately
    carries no impact result and grants no canonical-write authority.
    """

    schema_version: Literal["orgrebase.oac-dependency-projection-receipt.v1"] = (
        "orgrebase.oac-dependency-projection-receipt.v1"
    )
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_dependency_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mapping_rule_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_universe_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_edge_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    organization_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    oac_edge_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    edge_count: int = Field(gt=0)
    bindings: tuple[OACDependencyProjectionBinding, ...] = Field(min_length=1)
    verdict: Literal["PASS"] = "PASS"
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.mapping_rule_set_digest != _DEPENDENCY_MAPPING_RULE_SET_DIGEST:
            raise ValueError("OAC_DEPENDENCY_PROJECTION_RULE_SET_MISMATCH")
        if self.edge_count != len(self.bindings):
            raise ValueError("OAC_DEPENDENCY_PROJECTION_EDGE_COUNT_MISMATCH")
        expected_order = tuple(
            sorted(
                self.bindings,
                key=lambda item: (item.workspace_edge_id, item.oac_edge_id),
            )
        )
        if self.bindings != expected_order:
            raise ValueError("OAC_DEPENDENCY_PROJECTION_BINDING_ORDER_INVALID")
        workspace_ids = tuple(item.workspace_edge_id for item in self.bindings)
        oac_ids = tuple(item.oac_edge_id for item in self.bindings)
        if len(workspace_ids) != len(set(workspace_ids)) or len(oac_ids) != len(set(oac_ids)):
            raise ValueError("OAC_DEPENDENCY_PROJECTION_EDGE_DUPLICATE")
        if self.workspace_edge_set_digest != sha256_digest(
            [item.workspace_edge_digest for item in self.bindings]
        ):
            raise ValueError("OAC_DEPENDENCY_PROJECTION_WORKSPACE_SET_MISMATCH")
        if self.oac_edge_set_digest != sha256_digest([item.oac_edge_digest for item in self.bindings]):
            raise ValueError("OAC_DEPENDENCY_PROJECTION_OAC_SET_MISMATCH")
        return self


class OACOwnerReviewComponent(BaseModel):
    """One safe, business-facing slice of the source-to-contract review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_kind: SeedComponentKind
    completeness: str = Field(min_length=1)
    source_root_ref: str = Field(min_length=1)
    observed_item_count: int = Field(ge=0)
    sample_items: tuple[str, ...] = ()
    contract_effects: tuple[str, ...] = Field(min_length=1)
    responsible_refs: tuple[str, ...] = ()
    declared_unknowns: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class OACOwnerReviewSummary(ContentAddressedModel):
    """Human-readable approval subject derived from the exact admitted Pack.

    The summary is deliberately a safe projection: it carries no local paths,
    raw private values, provider identifiers, or authority to mutate business
    state.  Its digest is bound into the time gate, approval, adapter capsule,
    and therefore the later Workspace activation/consumption chain.
    """

    schema_version: Literal["orgrebase.oac-owner-review-summary.v1"] = "orgrebase.oac-owner-review-summary.v1"
    adaptation_run_id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    profile_ref: str = Field(min_length=1)
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_ref: str = Field(min_length=1)
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    data_class: SeedDataClass
    synthetic: bool
    evidence_label: Literal[
        "CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT",
        "PUBLIC_DATA_INPUT",
        "ENTERPRISE_INTERNAL_DECLARED_INPUT",
        "ENTERPRISE_CONFIDENTIAL_DECLARED_INPUT",
    ]
    input_format: Literal["FIVE_CLASS_JSON_ENTERPRISE_PACK"] = "FIVE_CLASS_JSON_ENTERPRISE_PACK"
    task_scope: dict[str, str]
    domain_ids: tuple[str, ...]
    fact_count: int = Field(ge=0)
    authority_refs: tuple[str, ...]
    capability_refs: tuple[str, ...]
    dependency_target_count: int = Field(ge=0)
    dependency_edge_count: int = Field(ge=0)
    dependency_owner_refs: tuple[str, ...]
    change_kinds: tuple[str, ...]
    component_reviews: tuple[OACOwnerReviewComponent, ...] = Field(
        min_length=5,
        max_length=5,
    )
    candidate_mapping_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    declared_unknowns: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    decision_owner_ref: str = Field(min_length=1)
    proposer_reviewer_separated: Literal[True] = True
    approval_effects: tuple[str, ...] = (
        "ACTIVATE_EXACT_ORGANIZATION_CONTRACT",
        "ALLOW_EXACT_WORKSPACE_FORMATION_GATE",
    )
    non_effects: tuple[str, ...] = (
        "NO_QUOTE_BUSINESS_APPROVAL",
        "NO_EXTERNAL_SYSTEM_WRITE",
        "NO_AGENT_AUTHORITY_EXPANSION",
    )
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = _EFFECT_CEILING
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if tuple(item.component_kind for item in self.component_reviews) != tuple(SeedComponentKind):
            raise ValueError("OAC_OWNER_REVIEW_COMPONENT_ORDER_INVALID")
        expected_label = {
            SeedDataClass.SYNTHETIC_FIXTURE: "CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT",
            SeedDataClass.PUBLIC: "PUBLIC_DATA_INPUT",
            SeedDataClass.INTERNAL: "ENTERPRISE_INTERNAL_DECLARED_INPUT",
            SeedDataClass.CONFIDENTIAL: "ENTERPRISE_CONFIDENTIAL_DECLARED_INPUT",
        }[self.data_class]
        if self.synthetic != (self.data_class is SeedDataClass.SYNTHETIC_FIXTURE):
            raise ValueError("OAC_OWNER_REVIEW_SYNTHETIC_CLASS_MISMATCH")
        if self.evidence_label != expected_label:
            raise ValueError("OAC_OWNER_REVIEW_DATA_CLASS_MISMATCH")
        if len(self.domain_ids) != len(set(self.domain_ids)):
            raise ValueError("OAC_OWNER_REVIEW_DOMAIN_DUPLICATE")
        if len(self.authority_refs) != len(set(self.authority_refs)):
            raise ValueError("OAC_OWNER_REVIEW_AUTHORITY_DUPLICATE")
        if len(self.capability_refs) != len(set(self.capability_refs)):
            raise ValueError("OAC_OWNER_REVIEW_CAPABILITY_DUPLICATE")
        if not self.dependency_owner_refs or len(self.dependency_owner_refs) != len(
            set(self.dependency_owner_refs)
        ):
            raise ValueError("OAC_OWNER_REVIEW_DEPENDENCY_OWNER_INVALID")
        return self


class OACOwnerReviewGate(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-owner-review-gate.v1"] = "orgrebase.oac-owner-review-gate.v1"
    adaptation_run_id: str = Field(min_length=1)
    mapping_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    organization_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    organizational_demand_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    owner_review_summary_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    owner_ref: str = Field(min_length=1)
    review_duration_ms: int = Field(ge=4000)
    prepared_at_epoch_ms: int = Field(ge=0)
    not_before_epoch_ms: int = Field(ge=0)
    prepared_at: str = Field(min_length=1)
    not_before: str = Field(min_length=1)
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        if self.not_before_epoch_ms - self.prepared_at_epoch_ms != self.review_duration_ms:
            raise ValueError("OAC_ADAPTATION_REVIEW_DURATION_INVALID")
        return self


class OACAdaptationDraft(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-quote-adaptation-draft.v1"] = (
        "orgrebase.oac-quote-adaptation-draft.v1"
    )
    adaptation_epoch: Literal["DEPENDENCY_BOUND_R2"] = "DEPENDENCY_BOUND_R2"
    rule_set_digest: str = Field(
        default=_RULE_SET_DIGEST,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    adaptation_run_id: str = Field(min_length=1)
    execution_run_id: str | None = Field(
        default=None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )
    status: Literal["HOLD", "OWNER_REVIEW_PENDING"]
    profile_ref: str = Field(min_length=1)
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    human_authority_ref: str = Field(min_length=1)
    candidate_mappings: tuple[CandidateSemanticMapping, ...] = Field(
        min_length=5,
        max_length=5,
    )
    mapping_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gaps: tuple[OACAdaptationGap, ...] = ()
    organization_snapshot: dict[str, Any] | None = None
    organizational_demand: dict[str, Any] | None = None
    dependency_projection_receipt: OACDependencyProjectionReceipt | None = None
    oac_public_validation: dict[str, Any] | None = None
    owner_review_summary: OACOwnerReviewSummary | None = None
    review_gate: OACOwnerReviewGate | None = None
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = _EFFECT_CEILING
    canonical_target_writes: Literal[0] = 0
    oac_plan_produced: Literal[False] = False
    oac_plan_certificate_produced: Literal[False] = False
    oac_runtime_invoked: Literal[False] = False

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        if self.rule_set_digest != _RULE_SET_DIGEST:
            raise ValueError("OAC_ADAPTATION_RULE_SET_DIGEST_MISMATCH")
        if tuple(item.component_kind for item in self.candidate_mappings) != tuple(SeedComponentKind):
            raise ValueError("OAC_ADAPTATION_MAPPING_ORDER_INVALID")
        expected = sha256_digest([item.digest for item in self.candidate_mappings])
        if self.mapping_set_digest != expected:
            raise ValueError("OAC_ADAPTATION_MAPPING_SET_DIGEST_MISMATCH")
        if self.status == "HOLD":
            if not self.gaps or any(
                value is not None
                for value in (
                    self.organization_snapshot,
                    self.organizational_demand,
                    self.dependency_projection_receipt,
                    self.oac_public_validation,
                    self.owner_review_summary,
                    self.review_gate,
                )
            ):
                raise ValueError("OAC_ADAPTATION_HOLD_SHAPE_INVALID")
        elif (
            self.gaps
            or self.pack_digest is None
            or self.organization_snapshot is None
            or self.organizational_demand is None
            or self.dependency_projection_receipt is None
            or self.oac_public_validation is None
            or self.owner_review_summary is None
            or self.review_gate is None
        ):
            raise ValueError("OAC_ADAPTATION_REVIEW_SHAPE_INVALID")
        if self.owner_review_summary is not None and (
            self.owner_review_summary.adaptation_run_id != self.adaptation_run_id
            or self.owner_review_summary.profile_digest != self.profile_digest
            or self.owner_review_summary.pack_digest != self.pack_digest
            or self.owner_review_summary.candidate_mapping_set_digest != self.mapping_set_digest
            or self.owner_review_summary.decision_owner_ref != self.human_authority_ref
            or self.review_gate is None
            or self.review_gate.adaptation_run_id != self.adaptation_run_id
            or self.review_gate.mapping_set_digest != self.mapping_set_digest
            or self.review_gate.owner_review_summary_digest != self.owner_review_summary.digest
            or self.review_gate.organization_snapshot_digest
            != (self.organization_snapshot or {}).get("digest")
            or self.review_gate.organizational_demand_digest
            != (self.organizational_demand or {}).get("digest")
            or self.review_gate.profile_digest != self.profile_digest
            or self.review_gate.pack_digest != self.pack_digest
            or self.review_gate.owner_ref != self.human_authority_ref
        ):
            raise ValueError("OAC_ADAPTATION_OWNER_REVIEW_SUMMARY_BINDING_INVALID")
        if self.dependency_projection_receipt is not None:
            snapshot = self.organization_snapshot or {}
            snapshot_spec = snapshot.get("spec") or {}
            if (
                self.dependency_projection_receipt.profile_digest != self.profile_digest
                or self.dependency_projection_receipt.pack_digest != self.pack_digest
                or self.dependency_projection_receipt.organization_snapshot_digest != snapshot.get("digest")
                or self.dependency_projection_receipt.edge_count
                != len(snapshot_spec.get("dependencyEdges") or ())
                or not snapshot_spec.get("dependencyEdges")
                or snapshot_spec.get("impactRules") != []
            ):
                raise ValueError("OAC_ADAPTATION_DEPENDENCY_PROJECTION_BINDING_INVALID")
        return self


class OACSourceAdmissionApproval(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-source-admission-approval.v1"] = (
        "orgrebase.oac-source-admission-approval.v1"
    )
    adaptation_run_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    candidate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    owner_review_summary_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    acknowledgements: tuple[str, ...] = Field(min_length=3, max_length=3)
    review_gate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_gate: OACOwnerReviewGate
    approved_at_epoch_ms: int = Field(ge=0)
    approved_at: str = Field(min_length=1)
    source_admission_receipt: dict[str, Any]
    public_validation: dict[str, Any]
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_review_gate(self) -> Self:
        gate = self.review_gate
        if (
            self.review_gate_digest != gate.digest
            or self.adaptation_run_id != gate.adaptation_run_id
            or self.candidate_digest != gate.mapping_set_digest
            or self.actor_id != gate.owner_ref
            or self.approved_at_epoch_ms < gate.not_before_epoch_ms
            or self.owner_review_summary_digest != gate.owner_review_summary_digest
        ):
            raise ValueError("OAC_ADAPTATION_APPROVAL_REVIEW_GATE_INVALID")
        if self.acknowledgements != OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS:
            raise ValueError("OAC_ADAPTATION_OWNER_ACKNOWLEDGEMENTS_INVALID")
        return self


class OACAdapterCapsule(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-quote-adapter-capsule.v1"] = (
        "orgrebase.oac-quote-adapter-capsule.v1"
    )
    status: Literal["READY_FOR_ORGREBASE"] = "READY_FOR_ORGREBASE"
    adaptation_run_id: str = Field(min_length=1)
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mapping_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    owner_review_summary_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    organization_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    organizational_demand_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dependency_projection_receipt_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    source_admission_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approval_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    quote_formation_parity_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = _EFFECT_CEILING
    canonical_target_writes: Literal[0] = 0
    oac_plan_produced: Literal[False] = False
    oac_plan_certificate_produced: Literal[False] = False
    oac_runtime_invoked: Literal[False] = False
    formation_authority: Literal["ORGREBASE_CONTROL_PLANE"] = "ORGREBASE_CONTROL_PLANE"
    claim: Literal["OAC_SOURCE_DEMAND_ADMITTED_AND_ORGREBASE_FORMATION_PARITY"] = (
        "OAC_SOURCE_DEMAND_ADMITTED_AND_ORGREBASE_FORMATION_PARITY"
    )


class OACAdapterActivationBinding(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-adapter-activation-binding.v1"] = (
        "orgrebase.oac-adapter-activation-binding.v1"
    )
    adaptation_run_id: str = Field(min_length=1)
    adapter_capsule_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_run_id: str = Field(min_length=1)
    binding_timing: Literal["PRE_EXECUTION_EXACT_BINDING"] = "PRE_EXECUTION_EXACT_BINDING"
    canonical_target_writes: Literal[0] = 0


def _epoch_ms_timestamp(epoch_ms: int) -> str:
    return (
        datetime.fromtimestamp(epoch_ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _resource_ref(resource: Mapping[str, Any]) -> dict[str, Any]:
    metadata = resource["metadata"]
    return {
        "apiVersion": resource["apiVersion"],
        "kind": resource["kind"],
        "namespace": metadata["namespace"],
        "resourceId": metadata["id"],
        "revision": metadata["revision"],
        "digest": resource["digest"],
    }


def _synthetic_ref(kind: str, resource_id: str, namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "kind": kind,
        "namespace": namespace,
        "resourceId": resource_id,
        "revision": 1,
        "digest": sha256_digest(
            {"kind": kind, "namespace": namespace, "resource_id": resource_id, "revision": 1}
        ),
    }


def _write_resource(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _seal_and_validate(
    cli: OACBlackBoxCLI,
    path: Path,
    resource: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    selected = dict(resource)
    selected["digest"] = None
    _write_resource(path, selected)
    digest_result = cli.run("digest", str(path))
    digest = digest_result.get("digest")
    if not isinstance(digest, str):
        raise IntegrityError("OAC_ADAPTATION_PUBLIC_DIGEST_MISSING")
    selected["digest"] = digest
    _write_resource(path, selected)
    cli.run("validate", str(path), "--verify-digest")
    return selected, (
        {"command": f"digest:{selected['kind']}", "status": "PASS"},
        {"command": f"validate:{selected['kind']}:verify-digest", "status": "PASS"},
    )


class OACQuoteAdaptationService:
    """Small façade shared by HTTP, CLI, evidence runners, and tests."""

    def __init__(
        self,
        *,
        store: StateStore,
        profile: EnterpriseSeedProfile,
        runtime: EnterpriseQuotePilotRuntime | None = None,
        oac_root: str | Path | None = None,
        golden_root: str | Path = DEFAULT_GOLDEN_ROOT,
        review_duration_seconds: float = 4.0,
        wall_clock: Callable[[], float] = time.time,
        execution_run_id: str | None = None,
    ) -> None:
        if review_duration_seconds < 4:
            raise ValueError("OAC_ADAPTATION_REVIEW_DURATION_UNDER_FOUR_SECONDS")
        self.store = store
        self.profile = profile.revalidated()
        self.runtime = runtime
        self.oac_root = oac_root
        self.golden_root = Path(golden_root)
        self.review_duration_ms = math.ceil(review_duration_seconds * 1000)
        self._wall_clock = wall_clock
        key_source = runtime.pack_digest if runtime is not None else self.profile.digest
        self._key = key_source.split(":", 1)[-1][:20]
        requested_execution_run_id = (
            execution_run_id.strip()
            if isinstance(execution_run_id, str) and execution_run_id.strip()
            else None
        )
        if execution_run_id is not None and requested_execution_run_id is None:
            raise ValueError("OAC_ADAPTATION_EXECUTION_RUN_ID_REQUIRED")
        persisted_execution_run_ids: set[str] = set()
        for kind, media_type in (
            ("adaptation-draft", _DRAFT_MEDIA),
            ("activation-binding", _BINDING_MEDIA),
        ):
            persisted = self._load(kind, media_type)
            persisted_id = (persisted or {}).get("execution_run_id")
            if isinstance(persisted_id, str) and persisted_id.strip():
                persisted_execution_run_ids.add(persisted_id)
        if len(persisted_execution_run_ids) > 1:
            raise IntegrityError("OAC_ADAPTATION_PERSISTED_EXECUTION_ID_CONFLICT")
        persisted_execution_run_id = next(iter(persisted_execution_run_ids), None)
        if (
            requested_execution_run_id is not None
            and persisted_execution_run_id is not None
            and requested_execution_run_id != persisted_execution_run_id
        ):
            raise RuntimeError("OAC_ADAPTATION_EXECUTION_ID_COMMAND_CONFLICT")
        self.execution_run_id = (
            persisted_execution_run_id or requested_execution_run_id or f"run:orgrebase:oac-bound:{uuid4()}"
        )

    @property
    def human_authority_ref(self) -> str:
        return self.profile.governance.admission_authority_refs[0]

    @property
    def adaptation_run_id(self) -> str:
        organization = self.profile.organization_id.split(":")[-1]
        # Keep the logical adaptation run stable so an already produced Agent
        # candidate remains replayable.  The new rule-set digest and r2
        # artifact identity distinguish the dependency-bound admission epoch.
        return f"run:oac-adaptation:{organization}:{self._key}@v1"

    def _artifact_id(self, kind: str) -> str:
        return f"oac-quote-{kind}:{self._key}@{_ARTIFACT_REVISION}"

    def _load(self, kind: str, media_type: str) -> dict[str, Any] | None:
        try:
            return self.store.load_artifact(self._artifact_id(kind), media_type).payload
        except KeyError:
            return None

    def _now_epoch_ms(self) -> int:
        observed = float(self._wall_clock())
        if not math.isfinite(observed):
            raise RuntimeError("OAC_ADAPTATION_WALL_CLOCK_INVALID")
        return math.floor(observed * 1000)

    def _mappings_and_gaps(
        self,
    ) -> tuple[tuple[CandidateSemanticMapping, ...], tuple[OACAdaptationGap, ...]]:
        roots = {item.id: item for item in self.profile.source_roots}
        components = {item.kind: item for item in self.profile.components}
        observations = (
            {item.component_kind: item for item in self.runtime.source_admission.root_observations}
            if self.runtime is not None
            else {}
        )
        profile_gaps: dict[SeedComponentKind, list[Any]] = {item: [] for item in SeedComponentKind}
        for gap in self.profile.gaps:
            profile_gaps[gap.component].append(gap)

        mappings: list[CandidateSemanticMapping] = []
        gaps: list[OACAdaptationGap] = []
        for kind in SeedComponentKind:
            component = components[kind]
            source = roots[component.source_root_refs[0]]
            observation = observations.get(kind)
            unknowns = [gap.path for gap in profile_gaps[kind]]
            reasons = [gap.kind.value for gap in profile_gaps[kind]]
            if component.completeness is not SeedCompleteness.COMPLETE:
                unknowns.append(f"component.{kind.value.lower()}.completeness")
                reasons.append(f"{kind.value}_COMPONENT_{component.completeness.value}")
            if self.runtime is None:
                unknowns.append(f"component.{kind.value.lower()}.exact_pack_bytes")
                reasons.append("EXACT_ENTERPRISE_PACK_NOT_ADMITTED")
            unknowns = list(dict.fromkeys(unknowns))
            reasons = list(dict.fromkeys(reasons))
            mapping = CandidateSemanticMapping(
                adaptation_run_id=self.adaptation_run_id,
                component_kind=kind,
                source_root_ref=f"{source.id}@{source.revision}",
                source_digest=(
                    observation.observed_digest if observation is not None else source.declared_digest
                ),
                producer=f"deterministic:oac-{kind.value.lower()}-mapper@v1",
                task_id=f"task:oac-map-{kind.value.lower()}@attempt-1",
                target_oac_paths=_TARGET_PATHS[kind],
                declared_unknowns=tuple(unknowns),
                reason_codes=tuple(reasons),
            )
            mappings.append(mapping)
            for index, reason in enumerate(reasons, start=1):
                matching = next(
                    (item for item in profile_gaps[kind] if item.kind.value == reason),
                    None,
                )
                gaps.append(
                    OACAdaptationGap(
                        component_kind=kind,
                        reason_code=reason,
                        source_ref=mapping.source_root_ref,
                        owner_ref=(matching.owner_ref if matching is not None else self.human_authority_ref),
                        resolution_gate_ref=(
                            matching.resolution_gate_ref
                            if matching is not None
                            else f"gate:oac-adaptation:{kind.value.lower()}:{index}@r1"
                        ),
                    )
                )
        return tuple(mappings), tuple(gaps)

    def deterministic_mapping_baseline(
        self,
    ) -> tuple[tuple[CandidateSemanticMapping, ...], tuple[OACAdaptationGap, ...]]:
        """Expose the exact keyless safety baseline without labelling it live Agent.

        Agent-assisted intake must be checked against the same mappings and
        Unknown set used by the existing deterministic path.  Returning a
        revalidated immutable copy keeps the baseline one-source rather than
        duplicating its authority rules in the provider prompt.
        """

        mappings, gaps = self._mappings_and_gaps()
        return (
            tuple(item.revalidated() for item in mappings),
            tuple(item.revalidated() for item in gaps),
        )

    def _dependency_projection_parts(
        self,
    ) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        tuple[OACDependencyProjectionBinding, ...],
        tuple[str, ...],
        str,
        str,
    ]:
        """Project the exact Pack dependency slice without becoming impact truth."""

        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("OAC_DEPENDENCY_PROJECTION_EXACT_PACK_REQUIRED")
        universe = runtime.universe.revalidated()
        workspace_edges = tuple(sorted(universe.edges, key=lambda item: item.id))
        if not workspace_edges:
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_EDGE_SET_EMPTY")
        observed_relations = tuple(item.relation for item in workspace_edges)
        if set(observed_relations) != set(_DEPENDENCY_RELATION_PROJECTION) or len(observed_relations) != len(
            _DEPENDENCY_RELATION_PROJECTION
        ):
            unknown = sorted(set(observed_relations) - set(_DEPENDENCY_RELATION_PROJECTION))
            code = (
                "OAC_DEPENDENCY_PROJECTION_RELATION_UNSUPPORTED:" + ",".join(unknown)
                if unknown
                else "OAC_DEPENDENCY_PROJECTION_RELATION_SET_INCOMPLETE"
            )
            raise IntegrityError(code)

        dependency_observations = tuple(
            item.revalidated()
            for item in runtime.source_admission.root_observations
            if item.component_kind is SeedComponentKind.DEPENDENCY
        )
        if len(dependency_observations) != 1:
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_SOURCE_ROOT_INVALID")
        source_dependency_digest = dependency_observations[0].observed_digest

        object_by_ref = {item.ref: item for item in universe.current_objects}
        if len(object_by_ref) != len(universe.current_objects):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_OBJECT_REF_DUPLICATE")
        manifest_by_target = {item.target_id: item.revalidated() for item in universe.imported_manifests}
        if len(manifest_by_target) != len(universe.imported_manifests):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_MANIFEST_TARGET_DUPLICATE")

        endpoint_by_id: dict[str, dict[str, Any]] = {}
        target_owner_refs: set[str] = set()
        oac_edges: list[dict[str, Any]] = []
        bindings: list[OACDependencyProjectionBinding] = []
        observed_target_ids: set[str] = set()
        node_types = {
            "ClaimVersion": "enterprise-claim",
            "PolicyVersion": "enterprise-policy",
            "WorkItemVersion": "enterprise-work-item",
        }
        for workspace_edge in workspace_edges:
            relation_projection = _DEPENDENCY_RELATION_PROJECTION.get(workspace_edge.relation)
            if relation_projection is None:
                raise IntegrityError(
                    "OAC_DEPENDENCY_PROJECTION_RELATION_UNSUPPORTED:" + workspace_edge.relation
                )
            (
                oac_relation,
                admission_status,
                required_coverage,
                required_strength,
            ) = relation_projection
            if (
                workspace_edge.coverage_basis.value != required_coverage
                or workspace_edge.strength.value != required_strength
            ):
                raise IntegrityError(
                    "OAC_DEPENDENCY_PROJECTION_RELATION_SEMANTICS_MISMATCH:" + workspace_edge.id
                )
            source = object_by_ref.get(workspace_edge.provider_ref)
            target = object_by_ref.get(workspace_edge.consumer_ref)
            if source is None or target is None:
                raise IntegrityError("OAC_DEPENDENCY_PROJECTION_ENDPOINT_MISSING:" + workspace_edge.id)
            if source.digest != workspace_edge.provider_digest:
                raise IntegrityError(
                    "OAC_DEPENDENCY_PROJECTION_PROVIDER_DIGEST_MISMATCH:" + workspace_edge.id
                )
            manifest = manifest_by_target.get(target.id)
            target_owner = target.payload.get("owner")
            if (
                manifest is None
                or not isinstance(target_owner, str)
                or not target_owner
                or manifest.issuer_id != target_owner
            ):
                raise IntegrityError("OAC_DEPENDENCY_PROJECTION_TARGET_OWNER_MISMATCH:" + target.id)
            observed_target_ids.add(target.id)
            target_owner_refs.add(target_owner)
            for endpoint in (source, target):
                node_type = node_types.get(endpoint.kind)
                if node_type is None:
                    raise IntegrityError("OAC_DEPENDENCY_PROJECTION_NODE_KIND_UNSUPPORTED:" + endpoint.kind)
                node = {
                    "nodeId": endpoint.id,
                    "nodeType": node_type,
                    "domainRef": f"domain:{endpoint.domain}",
                    "ownerRoleRef": "role:enterprise-contract-owner",
                    "admissionStatus": "admitted",
                }
                existing = endpoint_by_id.setdefault(endpoint.id, node)
                if existing != node:
                    raise IntegrityError("OAC_DEPENDENCY_PROJECTION_ENDPOINT_ID_CONFLICT:" + endpoint.id)
            oac_edge = {
                "edgeId": "edge:oac-quote:" + workspace_edge.id.removeprefix("edge:pilot:"),
                "sourceRef": source.id,
                "targetRef": target.id,
                "relationType": oac_relation,
                "transferPredicate": {
                    "predicateVersion": "oac.supplier.applicability/v0.2",
                    "semanticType": "enterprise_quote.source_change",
                    "afterState": "known",
                    "afterValues": ["changed"],
                    "subjectSelector": {"subjectRefs": [source.id]},
                    "relationTypes": [oac_relation],
                },
                "admissionStatus": admission_status,
            }
            oac_edges.append(oac_edge)
            bindings.append(
                OACDependencyProjectionBinding(
                    workspace_edge_id=workspace_edge.id,
                    workspace_edge_digest=workspace_edge.digest,
                    oac_edge_id=oac_edge["edgeId"],
                    oac_edge_digest=sha256_digest(oac_edge),
                )
            )
        if tuple(sorted(observed_target_ids)) != tuple(sorted(universe.target_ids)):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_TARGET_SET_MISMATCH")
        return (
            tuple(endpoint_by_id[key] for key in sorted(endpoint_by_id)),
            tuple(sorted(oac_edges, key=lambda item: str(item["edgeId"]))),
            tuple(
                sorted(
                    bindings,
                    key=lambda item: (item.workspace_edge_id, item.oac_edge_id),
                )
            ),
            tuple(sorted(target_owner_refs)),
            source_dependency_digest,
            universe.digest,
        )

    def _dependency_projection_receipt(
        self,
        snapshot: Mapping[str, Any],
    ) -> OACDependencyProjectionReceipt:
        (
            endpoint_nodes,
            expected_edges,
            bindings,
            _owner_refs,
            source_dependency_digest,
            universe_digest,
        ) = self._dependency_projection_parts()
        spec = snapshot.get("spec")
        if not isinstance(spec, Mapping):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_SNAPSHOT_SPEC_MISSING")
        snapshot_nodes = spec.get("nodes")
        snapshot_edges = spec.get("dependencyEdges")
        completeness = spec.get("completeness")
        if (
            not isinstance(snapshot_nodes, list)
            or tuple(snapshot_edges or ()) != expected_edges
            or spec.get("impactRules") != []
            or not isinstance(completeness, Mapping)
        ):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_SNAPSHOT_SHAPE_MISMATCH")
        node_by_id = {
            item.get("nodeId"): item
            for item in snapshot_nodes
            if isinstance(item, Mapping) and isinstance(item.get("nodeId"), str)
        }
        if any(node_by_id.get(item["nodeId"]) != item for item in endpoint_nodes):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_ENDPOINT_NODE_MISMATCH")
        covered_nodes = tuple(completeness.get("coveredNodeRefs") or ())
        expected_covered_nodes = tuple(sorted(node_by_id))
        covered_relations = tuple(completeness.get("coveredRelationTypes") or ())
        expected_relations = tuple(sorted({str(item["relationType"]) for item in expected_edges}))
        if covered_nodes != expected_covered_nodes or covered_relations != expected_relations:
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_COMPLETENESS_MISMATCH")
        snapshot_digest = snapshot.get("digest")
        if not isinstance(snapshot_digest, str):
            raise IntegrityError("OAC_DEPENDENCY_PROJECTION_SNAPSHOT_DIGEST_MISSING")
        return OACDependencyProjectionReceipt(
            profile_digest=self.profile.digest,
            pack_digest=(self.runtime.pack_digest if self.runtime is not None else ""),
            source_dependency_digest=source_dependency_digest,
            mapping_rule_set_digest=_DEPENDENCY_MAPPING_RULE_SET_DIGEST,
            workspace_universe_digest=universe_digest,
            workspace_edge_set_digest=sha256_digest([item.workspace_edge_digest for item in bindings]),
            organization_snapshot_digest=snapshot_digest,
            oac_edge_set_digest=sha256_digest([item.oac_edge_digest for item in bindings]),
            edge_count=len(bindings),
            bindings=bindings,
        )

    def _owner_review_summary(
        self,
        *,
        mappings: tuple[CandidateSemanticMapping, ...],
        mapping_set_digest: str,
        snapshot: Mapping[str, Any],
        dependency_projection_receipt: OACDependencyProjectionReceipt,
    ) -> OACOwnerReviewSummary:
        """Compile a safe approval subject from the exact admitted runtime.

        This projection intentionally describes both what is admitted by OAC
        and what remains an OrgRebase runtime projection.  It does not claim
        that the Agent authored the Pack semantics, and it never reads
        ``raw_private_value`` or exposes ``pack_root``.
        """

        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("OAC_OWNER_REVIEW_EXACT_PACK_REQUIRED")
        values = tuple(sorted(runtime.source_values.values(), key=lambda item: item.slot_id))
        domain_ids = tuple(sorted({item.domain_id for item in values}))
        source_authorities = {item.authority_ref for item in values}
        authority_refs = tuple(
            sorted(
                source_authorities
                | set(self.profile.governance.admission_authority_refs)
                | set(self.profile.governance.owner_refs)
            )
        )
        capability_refs = tuple(sorted(item.ref for item in default_capability_cards(self.profile.default_task.template_ref)))
        change_kinds = tuple(item.kind for item in self.profile.change_family)
        (
            _endpoint_nodes,
            projected_edges,
            _bindings,
            dependency_owner_refs,
            _source_dependency_digest,
            _universe_digest,
        ) = self._dependency_projection_parts()
        snapshot_edges = tuple((snapshot.get("spec") or {}).get("dependencyEdges") or ())
        if snapshot_edges != projected_edges:
            raise IntegrityError("OAC_OWNER_REVIEW_DEPENDENCY_SNAPSHOT_MISMATCH")
        dependency_target_refs = tuple(sorted({str(item["targetRef"]) for item in snapshot_edges}))
        if dependency_projection_receipt.edge_count != len(snapshot_edges):
            raise IntegrityError("OAC_OWNER_REVIEW_DEPENDENCY_RECEIPT_MISMATCH")
        components = {item.kind: item for item in self.profile.components}
        roots = {item.id: item for item in self.profile.source_roots}
        mapping_by_kind = {item.component_kind: item for item in mappings}

        component_reviews: list[OACOwnerReviewComponent] = []
        for kind in SeedComponentKind:
            component = components[kind]
            source = roots[component.source_root_refs[0]]
            mapping = mapping_by_kind[kind]
            if kind is SeedComponentKind.DOMAIN:
                count = len(domain_ids) + 1
                samples = (
                    f"task={self.profile.default_task.purpose}",
                    *(f"domain={item}" for item in domain_ids),
                )
                effects = ("DEFINE_TASK_AND_DOMAIN_SCOPE",)
                owners = (self.profile.default_task.actor_id,)
            elif kind is SeedComponentKind.KNOWLEDGE:
                count = len(values)
                # Human review exposes only the governed field category and its
                # sensitivity.  The value itself belongs to the admitted Pack
                # and must never be copied into an unauthenticated status view.
                samples = tuple(f"field:{item.slot_id}:{item.sensitivity}" for item in values[:3])
                effects = ("BIND_EVIDENCE_OBLIGATIONS_AND_SAFE_FACTS",)
                owners = tuple(sorted(source_authorities))
            elif kind is SeedComponentKind.AUTHORITY:
                count = len(authority_refs)
                samples = authority_refs[:3]
                effects = ("BIND_ACTORS_OWNERS_AND_APPROVAL_BOUNDARIES",)
                owners = tuple(self.profile.governance.admission_authority_refs)
            elif kind is SeedComponentKind.CAPABILITY:
                count = len(capability_refs)
                samples = (self.profile.default_task.template_ref, *capability_refs[:2])
                effects = ("BIND_TEMPLATE_AND_DOMAIN_CAPABILITY_SCOPE",)
                owners = tuple(sorted(source_authorities))
            else:
                count = len(snapshot_edges)
                samples = dependency_target_refs[:3]
                effects = ("BIND_PORTABLE_ORGANIZATIONAL_DEPENDENCY_AND_HANDOFF_SCOPE",)
                owners = dependency_owner_refs
            component_reviews.append(
                OACOwnerReviewComponent(
                    component_kind=kind,
                    completeness=component.completeness.value,
                    source_root_ref=f"{source.id}@{source.revision}",
                    observed_item_count=count,
                    sample_items=samples,
                    contract_effects=effects,
                    responsible_refs=owners,
                    declared_unknowns=mapping.declared_unknowns,
                    reason_codes=mapping.reason_codes,
                )
            )

        declared_unknowns = tuple(
            dict.fromkeys(unknown for mapping in mappings for unknown in mapping.declared_unknowns)
        )
        reason_codes = tuple(dict.fromkeys(reason for mapping in mappings for reason in mapping.reason_codes))
        return OACOwnerReviewSummary(
            adaptation_run_id=self.adaptation_run_id,
            organization_id=self.profile.organization_id,
            profile_ref=self.profile.ref,
            profile_digest=self.profile.digest,
            pack_ref=f"{runtime.pack_id}@{runtime.pack_revision}",
            pack_digest=runtime.pack_digest,
            data_class=self.profile.data_class.value,
            synthetic=self.profile.synthetic,
            evidence_label={
                SeedDataClass.SYNTHETIC_FIXTURE: ("CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT"),
                SeedDataClass.PUBLIC: "PUBLIC_DATA_INPUT",
                SeedDataClass.INTERNAL: "ENTERPRISE_INTERNAL_DECLARED_INPUT",
                SeedDataClass.CONFIDENTIAL: ("ENTERPRISE_CONFIDENTIAL_DECLARED_INPUT"),
            }[self.profile.data_class],
            task_scope={
                "task_type": self.profile.default_task.purpose,
                "actor_id": self.profile.default_task.actor_id,
                "customer_id": self.profile.default_task.customer_id,
                "deliverable_kind": self.profile.default_task.deliverable_kind,
                "template_ref": self.profile.default_task.template_ref,
            },
            domain_ids=domain_ids,
            fact_count=len(values),
            authority_refs=authority_refs,
            capability_refs=capability_refs,
            dependency_target_count=len(dependency_target_refs),
            dependency_edge_count=len(snapshot_edges),
            dependency_owner_refs=dependency_owner_refs,
            change_kinds=change_kinds,
            component_reviews=tuple(component_reviews),
            candidate_mapping_set_digest=mapping_set_digest,
            declared_unknowns=declared_unknowns,
            reason_codes=reason_codes,
            decision_owner_ref=self.human_authority_ref,
        )

    def _base_metadata(self, *, resource_id: str, source_refs: list[str]) -> dict[str, Any]:
        namespace = f"oac.orgrebase.{self.profile.organization_id.split(':')[-1]}"
        return {
            "id": resource_id,
            "namespace": namespace,
            "revision": 1,
            "ownerRef": "role:enterprise-contract-owner",
            "governanceRef": "policy:orgrebase-oac-enterprise-adaptation@v1",
            "createdAt": self.profile.default_task.requested_at,
            "effectiveFrom": None,
            "effectiveTo": None,
            "sourceRefs": source_refs,
        }

    def _snapshot_wire(self, mappings: tuple[CandidateSemanticMapping, ...]) -> dict[str, Any]:
        namespace = f"oac.orgrebase.{self.profile.organization_id.split(':')[-1]}"
        role_owner = "role:enterprise-contract-owner"
        role_operator = "role:enterprise-quote-operator"
        role_mapper = "role:oac-candidate-mapper"
        role_reviewer = "role:oac-admission-reviewer"
        root_nodes = [
            {
                "nodeId": f"source-root:{item.component_kind.value.lower()}",
                "nodeType": "enterprise-contract-root",
                "domainRef": "domain:enterprise-quote",
                "ownerRoleRef": role_owner,
                "admissionStatus": "candidate",
            }
            for item in mappings
        ]
        (
            endpoint_nodes,
            dependency_edges,
            _bindings,
            _owner_refs,
            _source_dependency_digest,
            _universe_digest,
        ) = self._dependency_projection_parts()
        nodes = sorted(
            [*root_nodes, *endpoint_nodes],
            key=lambda item: str(item["nodeId"]),
        )
        return {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "OrganizationSnapshot",
            "metadata": self._base_metadata(
                resource_id=f"snapshot:{self.profile.scenario_id}:quote-adaptation",
                source_refs=[item.source_root_ref for item in mappings],
            ),
            "spec": {
                "nodes": nodes,
                "roleDefinitions": [
                    {
                        "roleId": role_owner,
                        "domainRef": "domain:enterprise-quote",
                        "mission": "admit exact enterprise contract sources without business writes",
                        "responsibilityTypes": ["source-admission", "unknown-resolution"],
                        "requiredQualifications": ["qualification:enterprise-contract-owner"],
                        "effectCeiling": "zero_effect",
                        "admissionStatus": "admitted",
                    },
                    {
                        "roleId": role_operator,
                        "domainRef": "domain:enterprise-quote",
                        "mission": "request one governed enterprise quote",
                        "responsibilityTypes": ["quote-request"],
                        "requiredQualifications": [],
                        "effectCeiling": "zero_effect",
                        "admissionStatus": "admitted",
                    },
                    {
                        "roleId": role_mapper,
                        "domainRef": "domain:enterprise-quote",
                        "mission": "produce candidate semantic mappings only",
                        "responsibilityTypes": ["candidate-mapping"],
                        "requiredQualifications": [],
                        "effectCeiling": "zero_effect",
                        "admissionStatus": "admitted",
                    },
                    {
                        "roleId": role_reviewer,
                        "domainRef": "domain:enterprise-quote",
                        "mission": "review source admission independently from the proposer",
                        "responsibilityTypes": ["source-review"],
                        "requiredQualifications": [],
                        "effectCeiling": "zero_effect",
                        "admissionStatus": "admitted",
                    },
                ],
                "principals": [
                    {
                        "principalId": self.profile.default_task.actor_id,
                        "principalType": "human",
                        "eligibleRoleRefs": [role_operator],
                        "qualificationRefs": [],
                        "status": "active",
                        "admissionStatus": "admitted",
                    },
                    {
                        "principalId": self.human_authority_ref,
                        "principalType": "human",
                        "eligibleRoleRefs": [role_owner],
                        "qualificationRefs": ["qualification:enterprise-contract-owner"],
                        "status": "active",
                        "admissionStatus": "admitted",
                    },
                    {
                        "principalId": "principal:oac-adaptation-mapper",
                        "principalType": "agent",
                        "eligibleRoleRefs": [role_mapper],
                        "qualificationRefs": [],
                        "status": "active",
                        "admissionStatus": "admitted",
                    },
                    {
                        "principalId": "principal:oac-adaptation-reviewer",
                        "principalType": "service",
                        "eligibleRoleRefs": [role_reviewer],
                        "qualificationRefs": [],
                        "status": "active",
                        "admissionStatus": "admitted",
                    },
                ],
                "dependencyEdges": list(dependency_edges),
                "impactRules": [],
                "separationConstraints": [
                    {
                        "constraintId": "separation:oac-mapper-reviewer",
                        "leftRoleRef": role_mapper,
                        "rightRoleRef": role_reviewer,
                    }
                ],
                "completeness": {
                    "status": "complete",
                    "coveredNodeRefs": sorted(str(item["nodeId"]) for item in nodes),
                    "coveredRelationTypes": sorted({str(item["relationType"]) for item in dependency_edges}),
                    "maxDepth": 2,
                    "discoveryRoleRef": role_owner,
                    "knownGaps": [],
                },
            },
            "digest": None,
            "_namespace": namespace,
        }

    def _demand_wire(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        namespace = str(snapshot["metadata"]["namespace"])
        task_id = self.profile.default_task.id
        subject_id = f"subject:{self.profile.scenario_id}"
        evidence_refs = tuple(
            _synthetic_ref("EvidenceObligation", f"obligation:quote:{name}", namespace)
            for name in ("product", "legal", "finance", "gtm", "independent-review")
        )
        return {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "OrganizationalDemand",
            "metadata": self._base_metadata(
                resource_id=f"demand:{self.profile.scenario_id}:enterprise-quote",
                source_refs=[str(snapshot["metadata"]["id"]), subject_id, task_id],
            ),
            "spec": {
                "snapshotRef": _resource_ref(snapshot),
                "requesterPrincipalRef": self.profile.default_task.actor_id,
                "accountableRoleRef": "role:enterprise-quote-operator",
                "objective": "produce one governed enterprise quote with zero external effects",
                "subjectRefs": [_synthetic_ref("OrganizationSubject", subject_id, namespace)],
                "triggerRefs": [_synthetic_ref("TaskRequest", task_id, namespace)],
                "desiredOutcomeRefs": [
                    _synthetic_ref("OutcomeCriterion", "criterion:quote-ready-for-owner-review", namespace)
                ],
                "evidenceObligationRefs": list(evidence_refs),
                "constraintRefs": [],
                "priority": 1,
                "effectCeiling": "zero_effect",
            },
            "digest": None,
        }

    def _build_source_and_demand(
        self,
        mappings: tuple[CandidateSemanticMapping, ...],
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        OACDependencyProjectionReceipt,
    ]:
        cli = OACBlackBoxCLI(self.oac_root)
        commands: list[dict[str, str]] = []
        with tempfile.TemporaryDirectory(prefix="orgrebase-oac-quote-") as raw:
            root = Path(raw)
            snapshot_raw = self._snapshot_wire(mappings)
            snapshot_raw.pop("_namespace", None)
            snapshot, observed = _seal_and_validate(cli, root / "snapshot.json", snapshot_raw)
            commands.extend(observed)
            dependency_projection_receipt = self._dependency_projection_receipt(snapshot)
            demand_raw = self._demand_wire(snapshot)
            demand, observed = _seal_and_validate(cli, root / "demand.json", demand_raw)
            commands.extend(observed)
            cli.run(
                "validate-evolution",
                str(root / "demand.json"),
                "--snapshot",
                str(root / "snapshot.json"),
            )
            commands.append({"command": "validate-evolution:OrganizationalDemand", "status": "PASS"})
        validation = {
            "schema_version": "orgrebase.oac-public-validation-receipt.v1",
            "status": "PASS",
            "oac_cli_version": cli.version,
            "oac_source_fingerprint": cli.source_fingerprint,
            "execution_runtime": cli.execution_runtime,
            "commands": commands,
            "organization_snapshot_digest": snapshot["digest"],
            "organizational_demand_digest": demand["digest"],
            "dependency_projection_receipt_digest": (dependency_projection_receipt.digest),
            "effect_ceiling": _EFFECT_CEILING,
            "canonical_target_writes": 0,
        }
        validation["digest"] = sha256_digest(validation)
        return snapshot, demand, validation, dependency_projection_receipt

    def _require_dependency_projection(
        self,
        draft: OACAdaptationDraft,
    ) -> OACDependencyProjectionReceipt:
        snapshot = draft.organization_snapshot
        retained = self._load(
            "dependency-projection",
            _DEPENDENCY_PROJECTION_MEDIA,
        )
        if snapshot is None or draft.dependency_projection_receipt is None or retained is None:
            raise RuntimeError("OAC_DEPENDENCY_PROJECTION_LINEAGE_REQUIRED")
        persisted = OACDependencyProjectionReceipt.model_validate(retained)
        expected = self._dependency_projection_receipt(snapshot)
        validation = draft.oac_public_validation or {}
        if (
            persisted != draft.dependency_projection_receipt
            or persisted != expected
            or validation.get("dependency_projection_receipt_digest") != expected.digest
        ):
            raise RuntimeError("OAC_DEPENDENCY_PROJECTION_BINDING_MISMATCH")
        return expected

    def _source_admission_wire(
        self,
        draft: OACAdaptationDraft,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if draft.organization_snapshot is None:
            raise IntegrityError("OAC_ADAPTATION_SNAPSHOT_REQUIRED")
        snapshot = draft.organization_snapshot
        namespace = str(snapshot["metadata"]["namespace"])
        subject_ref = _resource_ref(snapshot)
        profile_ref = _synthetic_ref(
            "IntakeProfile",
            f"profile:orgrebase-enterprise-quote:{self.profile.revision}",
            namespace,
        )
        proposer = _synthetic_ref("Principal", "principal:oac-adaptation-mapper", namespace)
        authority = _synthetic_ref("Principal", self.human_authority_ref, namespace)
        reviewer = _synthetic_ref("Principal", "principal:oac-adaptation-reviewer", namespace)
        wire = {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "SourceAdmissionReceipt",
            "metadata": self._base_metadata(
                resource_id=f"receipt:{self.profile.scenario_id}:source-admission",
                source_refs=[
                    str(snapshot["metadata"]["id"]),
                    str(profile_ref["resourceId"]),
                    str(authority["resourceId"]),
                ],
            ),
            "spec": {
                "admissionPurpose": "enterprise_intake",
                "subjectRefs": [subject_ref],
                "intakeManifestDigest": draft.mapping_set_digest,
                "intakeProfileRef": profile_ref,
                "ruleSetDigest": _RULE_SET_DIGEST,
                "proposerRefs": [proposer],
                "decisionAuthorityRef": authority,
                "reviewerRefs": [reviewer],
                "verdict": "ADMITTED",
                "admittedSubjectRefs": [subject_ref],
                "unresolvedRefs": [],
                "reasonCodes": [],
                "predecessorSourceRoot": None,
                "evolutionRoot": None,
                "candidateRef": None,
                "governanceDecisionRef": None,
            },
            "digest": None,
        }
        cli = OACBlackBoxCLI(self.oac_root)
        with tempfile.TemporaryDirectory(prefix="orgrebase-oac-source-admission-") as raw:
            path = Path(raw) / "source-admission.json"
            receipt, commands = _seal_and_validate(cli, path, wire)
            cli.run("validate-evolution", str(path))
        validation = {
            "schema_version": "orgrebase.oac-source-admission-public-validation.v1",
            "status": "PASS",
            "oac_cli_version": cli.version,
            "oac_source_fingerprint": cli.source_fingerprint,
            "execution_runtime": cli.execution_runtime,
            "commands": [
                *commands,
                {"command": "validate-evolution:SourceAdmissionReceipt", "status": "PASS"},
            ],
            "source_admission_receipt_digest": receipt["digest"],
            "canonical_target_writes": 0,
        }
        validation["digest"] = sha256_digest(validation)
        return receipt, validation

    def view(self) -> dict[str, Any]:
        draft_payload = self._load("adaptation-draft", _DRAFT_MEDIA)
        if draft_payload is None:
            return {
                "schema_version": "orgrebase.oac-quote-adaptation-view.v1",
                "status": "PACK_OBSERVED",
                "adaptation_run_id": self.adaptation_run_id,
                "profile_digest": self.profile.digest,
                "pack_digest": self.runtime.pack_digest if self.runtime is not None else None,
                "human_authority_ref": self.human_authority_ref,
                "candidate_mappings": [],
                "gaps": [],
                "effect_ceiling": _EFFECT_CEILING,
                "canonical_target_writes": 0,
                "oac_plan_produced": False,
                "oac_plan_certificate_produced": False,
                "oac_runtime_invoked": False,
            }
        draft = OACAdaptationDraft.model_validate(draft_payload)
        result: dict[str, Any] = {
            **draft.model_dump(mode="json"),
            "candidate_digest": draft.mapping_set_digest,
            "agent_mapping": None,
            "organization_snapshot_digest": (draft.organization_snapshot or {}).get("digest"),
            "organizational_demand_digest": (draft.organizational_demand or {}).get("digest"),
            "review_gate_digest": draft.review_gate.digest if draft.review_gate else None,
        }
        if draft.review_gate is not None:
            result["review_remaining_ms"] = max(
                0,
                draft.review_gate.not_before_epoch_ms - self._now_epoch_ms(),
            )
            result["review_not_before"] = draft.review_gate.not_before
        approval_payload = self._load("adaptation-approval", _APPROVAL_MEDIA)
        capsule_payload = self._load("adapter-capsule", _CAPSULE_MEDIA)
        binding_payload = self._load("activation-binding", _BINDING_MEDIA)
        parity_payload = self._load("formation-parity", _OAC_RESOURCE_MEDIA)
        agent_mapping_payload = self._load("agent-mapping-receipt", _AGENT_MAPPING_MEDIA)
        if agent_mapping_payload is not None:
            result["agent_mapping"] = {
                "status": agent_mapping_payload.get("status"),
                "mapping_mode": agent_mapping_payload.get("mapping_mode"),
                "receipt_digest": agent_mapping_payload.get("digest"),
                "model_observation_digest": (agent_mapping_payload.get("model_observation") or {}).get(
                    "digest"
                ),
                "native_agentteams_digest": (agent_mapping_payload.get("native_agentteams") or {}).get(
                    "digest"
                ),
                "accepted_mapping_set_digest": agent_mapping_payload.get("accepted_mapping_set_digest"),
                "candidate_only": agent_mapping_payload.get("candidate_only"),
                "canonical_target_writes": agent_mapping_payload.get("canonical_target_writes"),
            }
        if approval_payload and capsule_payload and binding_payload and parity_payload:
            approval = OACSourceAdmissionApproval.model_validate(approval_payload)
            capsule = OACAdapterCapsule.model_validate(capsule_payload)
            binding = OACAdapterActivationBinding.model_validate(binding_payload)
            parity = verify_quote_formation_parity(parity_payload)
            if draft.pack_digest is None:
                raise IntegrityError("OAC_ADAPTATION_READY_DRAFT_PACK_MISSING")
            self.require_activation_binding(
                profile_digest=draft.profile_digest,
                pack_digest=draft.pack_digest,
                execution_run_id=binding.execution_run_id,
                for_execution=False,
            )
            result.update(
                {
                    "status": "READY_FOR_ORGREBASE",
                    "approval": approval.model_dump(mode="json"),
                    "approval_digest": approval.digest,
                    "source_admission_receipt_digest": approval.source_admission_receipt["digest"],
                    "adapter_capsule": capsule.model_dump(mode="json"),
                    "adapter_capsule_digest": capsule.digest,
                    "activation_binding": binding.model_dump(mode="json"),
                    "execution_run_id": binding.execution_run_id,
                    "quote_formation_parity_receipt": parity.model_dump(mode="json"),
                    "formation_parity_status": parity.verdict,
                }
            )
        return result

    def _implementation_binding(self, draft: OACAdaptationDraft, implementation: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "orgrebase.oac-adaptation-implementation-binding.v1",
            "tenant_id": self.store.tenant_id, "workspace_id": self.store.workspace_id,
            "adaptation_run_id": draft.adaptation_run_id, "draft_digest": draft.digest,
            "mapping_set_digest": draft.mapping_set_digest, "rule_set_digest": draft.rule_set_digest,
            "implementation": dict(implementation),
        }

    def _require_current_implementation(self, draft: OACAdaptationDraft) -> None:
        from orgrebase.workspace.oac_agent_adaptation import (
            _IMPLEMENTATION_MEDIA,
            current_oac_admission_implementation,
        )

        saved = self._load("implementation-binding", _IMPLEMENTATION_MEDIA)
        if saved is None:
            raise RuntimeError("OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING")
        expected = self._implementation_binding(draft, current_oac_admission_implementation())
        if saved != expected:
            raise RuntimeError("OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED")

    def prepare(
        self,
        *,
        command_id: str,
        agent_mapping_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not command_id:
            raise ValueError("OAC_ADAPTATION_COMMAND_ID_REQUIRED")
        from orgrebase.workspace.oac_agent_adaptation import (
            _IMPLEMENTATION_MEDIA,
            current_oac_admission_implementation,
        )

        implementation = current_oac_admission_implementation()
        existing = self._load("adaptation-draft", _DRAFT_MEDIA)
        if existing is not None:
            draft = OACAdaptationDraft.model_validate(existing)
            if self._load("adaptation-approval", _APPROVAL_MEDIA) is None:
                self._require_current_implementation(draft)
            if agent_mapping_receipt is not None:
                retained = self._load("agent-mapping-receipt", _AGENT_MAPPING_MEDIA)
                if retained is None or retained.get("digest") != agent_mapping_receipt.get("digest"):
                    raise RuntimeError("OAC_ADAPTATION_AGENT_MAPPING_COMMAND_CONFLICT")
            return self.view()
        mappings, gaps = self.deterministic_mapping_baseline()
        verified_agent_mapping: dict[str, Any] | None = None
        if agent_mapping_receipt is not None:
            if self.runtime is None:
                raise RuntimeError("OAC_ADAPTATION_AGENT_MAPPING_EXACT_PACK_REQUIRED")
            # Lazy import avoids making the deterministic baseline depend on an
            # Agent/provider implementation at module-import time.
            from orgrebase.workspace.oac_agent_adaptation import (
                require_current_agent_mapping_source,
                require_oac_mapping_implementation,
                require_verified_agent_mapping_receipt,
            )

            baseline_mapping_set_digest = sha256_digest([item.digest for item in mappings])
            verified = require_verified_agent_mapping_receipt(
                agent_mapping_receipt,
                adaptation_run_id=self.adaptation_run_id,
                profile_digest=self.profile.digest,
                pack_digest=self.runtime.pack_digest,
                baseline_mapping_set_digest=baseline_mapping_set_digest,
            )
            require_oac_mapping_implementation(self.store, verified)
            require_current_agent_mapping_source(verified)
            mappings = tuple(item.revalidated() for item in verified.accepted_mappings)
            verified_agent_mapping = verified.model_dump(mode="json")
        mapping_set_digest = sha256_digest([item.digest for item in mappings])
        if (
            gaps
            or self.runtime is None
            or (self.profile.runtime_compatibility.mode is not RuntimeCompatibilityMode.REFERENCE_HANDLER)
        ):
            draft = OACAdaptationDraft(
                adaptation_run_id=self.adaptation_run_id,
                execution_run_id=self.execution_run_id,
                status="HOLD",
                profile_ref=self.profile.ref,
                profile_digest=self.profile.digest,
                pack_digest=self.runtime.pack_digest if self.runtime is not None else None,
                human_authority_ref=self.human_authority_ref,
                candidate_mappings=mappings,
                mapping_set_digest=mapping_set_digest,
                gaps=gaps,
            )
        else:
            (
                snapshot,
                demand,
                validation,
                dependency_projection_receipt,
            ) = self._build_source_and_demand(mappings)
            owner_review_summary = self._owner_review_summary(
                mappings=mappings,
                mapping_set_digest=mapping_set_digest,
                snapshot=snapshot,
                dependency_projection_receipt=dependency_projection_receipt,
            )
            prepared_at = self._now_epoch_ms()
            not_before = prepared_at + self.review_duration_ms
            gate = OACOwnerReviewGate(
                adaptation_run_id=self.adaptation_run_id,
                mapping_set_digest=mapping_set_digest,
                organization_snapshot_digest=str(snapshot["digest"]),
                organizational_demand_digest=str(demand["digest"]),
                owner_review_summary_digest=owner_review_summary.digest,
                profile_digest=self.profile.digest,
                pack_digest=self.runtime.pack_digest,
                owner_ref=self.human_authority_ref,
                review_duration_ms=self.review_duration_ms,
                prepared_at_epoch_ms=prepared_at,
                not_before_epoch_ms=not_before,
                prepared_at=_epoch_ms_timestamp(prepared_at),
                not_before=_epoch_ms_timestamp(not_before),
            )
            draft = OACAdaptationDraft(
                adaptation_run_id=self.adaptation_run_id,
                execution_run_id=self.execution_run_id,
                status="OWNER_REVIEW_PENDING",
                profile_ref=self.profile.ref,
                profile_digest=self.profile.digest,
                pack_digest=self.runtime.pack_digest,
                human_authority_ref=self.human_authority_ref,
                candidate_mappings=mappings,
                mapping_set_digest=mapping_set_digest,
                organization_snapshot=snapshot,
                organizational_demand=demand,
                dependency_projection_receipt=dependency_projection_receipt,
                oac_public_validation=validation,
                owner_review_summary=owner_review_summary,
                review_gate=gate,
            )
        if implementation != current_oac_admission_implementation():
            raise RuntimeError("OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED")
        with self.store.transaction() as connection:
            authorization = current_authorization()
            if authorization is not None:
                authorization()
                self.store.require_before_commit(connection, authorization)
            self.store.save_artifact(
                connection, self._artifact_id("implementation-binding"), _IMPLEMENTATION_MEDIA,
                self._implementation_binding(draft, implementation),
            )
            if verified_agent_mapping is not None:
                self.store.save_artifact(
                    connection,
                    self._artifact_id("agent-mapping-receipt"),
                    _AGENT_MAPPING_MEDIA,
                    verified_agent_mapping,
                )
            if draft.dependency_projection_receipt is not None:
                self.store.save_artifact(
                    connection,
                    self._artifact_id("dependency-projection"),
                    _DEPENDENCY_PROJECTION_MEDIA,
                    draft.dependency_projection_receipt.model_dump(mode="json"),
                )
            self.store.save_artifact(
                connection,
                self._artifact_id("adaptation-draft"),
                _DRAFT_MEDIA,
                draft.model_dump(mode="json"),
            )
            self.store.append_event(
                connection,
                "OAC_QUOTE_ADAPTATION_PREPARED",
                {
                    "adaptation_run_id": self.adaptation_run_id,
                    "command_id": command_id,
                    "status": draft.status,
                    "draft_digest": draft.digest,
                    "mapping_set_digest": mapping_set_digest,
                    "dependency_projection_receipt_digest": (
                        draft.dependency_projection_receipt.digest
                        if draft.dependency_projection_receipt is not None
                        else None
                    ),
                    "owner_review_summary_digest": (
                        draft.owner_review_summary.digest if draft.owner_review_summary is not None else None
                    ),
                    "agent_mapping_receipt_digest": (
                        verified_agent_mapping.get("digest") if verified_agent_mapping is not None else None
                    ),
                    "gap_count": len(gaps),
                    "canonical_target_writes": 0,
                },
            )
        return self.view()

    def approve(
        self,
        *,
        actor_id: str,
        candidate_digest: str,
        command_id: str,
        owner_review_summary_digest: str,
        acknowledgements: tuple[str, ...],
    ) -> dict[str, Any]:
        draft_payload = self._load("adaptation-draft", _DRAFT_MEDIA)
        if draft_payload is None:
            raise RuntimeError("OAC_ADAPTATION_PREPARE_REQUIRED")
        draft = OACAdaptationDraft.model_validate(draft_payload)
        if draft.status == "HOLD":
            raise RuntimeError("OAC_ADAPTATION_HOLD_NOT_APPROVABLE")
        if draft.human_authority_ref != self.human_authority_ref:
            raise IntegrityError("OAC_ADAPTATION_PERSISTED_OWNER_BINDING_INVALID")
        if actor_id != draft.human_authority_ref:
            raise AuthorizationError(
                f"OAC_ADAPTATION_OWNER_MISMATCH:expected={draft.human_authority_ref},actual={actor_id}"
            )
        if candidate_digest != draft.mapping_set_digest:
            raise RuntimeError("OAC_ADAPTATION_CANDIDATE_DIGEST_MISMATCH")
        review_summary = draft.owner_review_summary
        if review_summary is None:
            raise IntegrityError("OAC_ADAPTATION_OWNER_REVIEW_SUMMARY_MISSING")
        if owner_review_summary_digest != review_summary.digest:
            raise RuntimeError("OAC_ADAPTATION_OWNER_REVIEW_SUMMARY_MISMATCH")
        if acknowledgements != OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS:
            raise RuntimeError("OAC_ADAPTATION_OWNER_ACKNOWLEDGEMENTS_REQUIRED")
        if not command_id:
            raise ValueError("OAC_ADAPTATION_COMMAND_ID_REQUIRED")
        existing = self._load("adaptation-approval", _APPROVAL_MEDIA)
        if existing is not None:
            approval = OACSourceAdmissionApproval.model_validate(existing)
            if (
                approval.actor_id != actor_id
                or approval.candidate_digest != candidate_digest
                or approval.owner_review_summary_digest != owner_review_summary_digest
                or approval.acknowledgements != acknowledgements
            ):
                raise RuntimeError("OAC_ADAPTATION_APPROVAL_COMMAND_CONFLICT")
            return self.view()
        self._require_current_implementation(draft)
        gate = draft.review_gate
        if gate is None:
            raise IntegrityError("OAC_ADAPTATION_REVIEW_GATE_MISSING")
        now = self._now_epoch_ms()
        if now < gate.not_before_epoch_ms:
            raise OACAdaptationReviewGatePending(
                remaining_ms=gate.not_before_epoch_ms - now,
                not_before=gate.not_before,
            )
        if self.runtime is None or draft.pack_digest != self.runtime.pack_digest:
            raise RuntimeError("OAC_ADAPTATION_PACK_BINDING_STALE")
        if draft.profile_digest != self.profile.digest:
            raise RuntimeError("OAC_ADAPTATION_PROFILE_BINDING_STALE")

        dependency_projection = self._require_dependency_projection(draft)
        source_admission, public_validation = self._source_admission_wire(draft)
        parity = build_quote_formation_parity_receipt(
            adaptation_run_id=draft.adaptation_run_id,
            adaptation_draft_digest=draft.digest,
            mapping_set_digest=draft.mapping_set_digest,
            profile_digest=draft.profile_digest,
            pack_digest=draft.pack_digest,
            golden_summary=load_golden_summary(self.golden_root),
        )
        approval = OACSourceAdmissionApproval(
            adaptation_run_id=draft.adaptation_run_id,
            command_id=command_id,
            actor_id=actor_id,
            candidate_digest=candidate_digest,
            owner_review_summary_digest=owner_review_summary_digest,
            acknowledgements=acknowledgements,
            review_gate_digest=gate.digest,
            review_gate=gate,
            approved_at_epoch_ms=now,
            approved_at=_epoch_ms_timestamp(now),
            source_admission_receipt=source_admission,
            public_validation=public_validation,
        )
        capsule = OACAdapterCapsule(
            adaptation_run_id=draft.adaptation_run_id,
            profile_digest=draft.profile_digest,
            pack_digest=draft.pack_digest,
            mapping_set_digest=draft.mapping_set_digest,
            owner_review_summary_digest=owner_review_summary_digest,
            organization_snapshot_digest=str(draft.organization_snapshot["digest"]),
            organizational_demand_digest=str(draft.organizational_demand["digest"]),
            dependency_projection_receipt_digest=dependency_projection.digest,
            source_admission_receipt_digest=str(source_admission["digest"]),
            approval_digest=approval.digest,
            quote_formation_parity_digest=parity.digest,
        )
        binding = OACAdapterActivationBinding(
            adaptation_run_id=draft.adaptation_run_id,
            adapter_capsule_digest=capsule.digest,
            profile_digest=draft.profile_digest,
            pack_digest=draft.pack_digest,
            execution_run_id=draft.execution_run_id or self.execution_run_id,
        )
        writes = (
            ("adaptation-approval", _APPROVAL_MEDIA, approval.model_dump(mode="json")),
            ("source-admission", _OAC_RESOURCE_MEDIA, source_admission),
            ("formation-parity", _OAC_RESOURCE_MEDIA, parity.model_dump(mode="json")),
            ("adapter-capsule", _CAPSULE_MEDIA, capsule.model_dump(mode="json")),
            ("activation-binding", _BINDING_MEDIA, binding.model_dump(mode="json")),
        )
        with self.store.transaction() as connection:
            authorization = current_authorization()
            if authorization is not None:
                authorization()
                self.store.require_before_commit(connection, authorization)
            for kind, media_type, payload in writes:
                self.store.save_artifact(
                    connection,
                    self._artifact_id(kind),
                    media_type,
                    payload,
                )
            self.store.append_event(
                connection,
                "OAC_QUOTE_ADAPTER_ADMITTED",
                {
                    "adaptation_run_id": draft.adaptation_run_id,
                    "execution_run_id": binding.execution_run_id,
                    "actor_id": actor_id,
                    "owner_review_summary_digest": owner_review_summary_digest,
                    "acknowledgements": list(acknowledgements),
                    "approval_digest": approval.digest,
                    "source_admission_receipt_digest": source_admission["digest"],
                    "quote_formation_parity_digest": parity.digest,
                    "dependency_projection_receipt_digest": (dependency_projection.digest),
                    "adapter_capsule_digest": capsule.digest,
                    "activation_binding_digest": binding.digest,
                    "canonical_target_writes": 0,
                },
            )
        return self.view()

    def verify_parity(self) -> QuoteFormationParityReceipt:
        payload = self._load("formation-parity", _OAC_RESOURCE_MEDIA)
        if payload is None:
            raise RuntimeError("OAC_ADAPTATION_PARITY_NOT_AVAILABLE")
        return verify_quote_formation_parity(payload)

    def activation_binding(self) -> OACAdapterActivationBinding:
        payload = self._load("activation-binding", _BINDING_MEDIA)
        if payload is None:
            raise RuntimeError("OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED")
        return OACAdapterActivationBinding.model_validate(payload)

    def require_activation_binding(
        self,
        *,
        profile_digest: str,
        pack_digest: str,
        execution_run_id: str,
        for_execution: bool = True,
    ) -> OACAdapterActivationBinding:
        binding = self.activation_binding()
        required_payloads = {
            "draft": self._load("adaptation-draft", _DRAFT_MEDIA),
            "approval": self._load("adaptation-approval", _APPROVAL_MEDIA),
            "capsule": self._load("adapter-capsule", _CAPSULE_MEDIA),
            "source": self._load("source-admission", _OAC_RESOURCE_MEDIA),
            "parity": self._load("formation-parity", _OAC_RESOURCE_MEDIA),
            "dependency_projection": self._load(
                "dependency-projection",
                _DEPENDENCY_PROJECTION_MEDIA,
            ),
        }
        missing = tuple(name for name, payload in required_payloads.items() if payload is None)
        if missing:
            raise RuntimeError("OAC_ADAPTATION_ACTIVATION_LINEAGE_REQUIRED:" + ",".join(missing))
        draft_payload = required_payloads["draft"]
        approval_payload = required_payloads["approval"]
        capsule_payload = required_payloads["capsule"]
        source_payload = required_payloads["source"]
        parity_payload = required_payloads["parity"]
        dependency_projection_payload = required_payloads["dependency_projection"]
        assert draft_payload is not None
        assert approval_payload is not None
        assert capsule_payload is not None
        assert source_payload is not None
        assert parity_payload is not None
        assert dependency_projection_payload is not None
        draft = OACAdaptationDraft.model_validate(draft_payload)
        if for_execution:
            self._require_current_implementation(draft)
        approval = OACSourceAdmissionApproval.model_validate(approval_payload)
        capsule = OACAdapterCapsule.model_validate(capsule_payload)
        parity = verify_quote_formation_parity(parity_payload)
        dependency_projection = OACDependencyProjectionReceipt.model_validate(dependency_projection_payload)
        summary = draft.owner_review_summary
        gate = draft.review_gate
        snapshot = draft.organization_snapshot
        demand = draft.organizational_demand
        source_digest = source_payload.get("digest")
        if (
            draft.status != "OWNER_REVIEW_PENDING"
            or summary is None
            or gate is None
            or snapshot is None
            or demand is None
            or not isinstance(source_digest, str)
        ):
            raise RuntimeError("OAC_ADAPTATION_ACTIVATION_LINEAGE_INCOMPLETE")
        expected_dependency_projection = self._require_dependency_projection(draft)
        expected_summary = self._owner_review_summary(
            mappings=draft.candidate_mappings,
            mapping_set_digest=draft.mapping_set_digest,
            snapshot=snapshot,
            dependency_projection_receipt=expected_dependency_projection,
        )
        if (
            draft.profile_digest != profile_digest
            or draft.pack_digest != pack_digest
            or draft.execution_run_id != execution_run_id
            or summary.adaptation_run_id != draft.adaptation_run_id
            or summary != expected_summary
            or summary.profile_digest != draft.profile_digest
            or summary.pack_digest != draft.pack_digest
            or summary.candidate_mapping_set_digest != draft.mapping_set_digest
            or summary.decision_owner_ref != draft.human_authority_ref
            or gate.adaptation_run_id != draft.adaptation_run_id
            or gate.mapping_set_digest != draft.mapping_set_digest
            or gate.owner_review_summary_digest != summary.digest
            or gate.organization_snapshot_digest != snapshot.get("digest")
            or gate.organizational_demand_digest != demand.get("digest")
            or gate.profile_digest != draft.profile_digest
            or gate.pack_digest != draft.pack_digest
            or gate.owner_ref != draft.human_authority_ref
            or approval.adaptation_run_id != draft.adaptation_run_id
            or approval.candidate_digest != draft.mapping_set_digest
            or approval.owner_review_summary_digest != summary.digest
            or approval.acknowledgements != OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS
            or approval.review_gate_digest != gate.digest
            or approval.review_gate != gate
            or approval.actor_id != summary.decision_owner_ref
            or approval.source_admission_receipt != source_payload
            or approval.public_validation.get("source_admission_receipt_digest") != source_digest
            or capsule.adaptation_run_id != draft.adaptation_run_id
            or capsule.mapping_set_digest != draft.mapping_set_digest
            or capsule.owner_review_summary_digest != summary.digest
            or capsule.organization_snapshot_digest != snapshot.get("digest")
            or capsule.organizational_demand_digest != demand.get("digest")
            or capsule.dependency_projection_receipt_digest != dependency_projection.digest
            or capsule.source_admission_receipt_digest != source_digest
            or capsule.approval_digest != approval.digest
            or capsule.quote_formation_parity_digest != parity.digest
            or parity.adaptation_run_id != draft.adaptation_run_id
            or parity.adaptation_draft_digest != draft.digest
            or parity.mapping_set_digest != draft.mapping_set_digest
            or parity.profile_digest != draft.profile_digest
            or parity.pack_digest != draft.pack_digest
            or binding.adaptation_run_id != draft.adaptation_run_id
            or binding.adapter_capsule_digest != capsule.digest
            or binding.profile_digest != profile_digest
            or capsule.profile_digest != profile_digest
            or binding.pack_digest != pack_digest
            or capsule.pack_digest != pack_digest
            or binding.execution_run_id != execution_run_id
            or draft.dependency_projection_receipt != dependency_projection
            or dependency_projection != expected_dependency_projection
        ):
            raise RuntimeError("OAC_ADAPTATION_ACTIVATION_BINDING_MISMATCH")
        return binding


__all__ = (
    "OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS",
    "CandidateSemanticMapping",
    "OACAdaptationDraft",
    "OACAdaptationGap",
    "OACAdaptationReviewGatePending",
    "OACAdapterActivationBinding",
    "OACAdapterCapsule",
    "OACDependencyProjectionBinding",
    "OACDependencyProjectionReceipt",
    "OACOwnerReviewComponent",
    "OACOwnerReviewGate",
    "OACOwnerReviewSummary",
    "OACQuoteAdaptationService",
    "OACSourceAdmissionApproval",
    "quote_adaptation_rule_set_digest",
)
