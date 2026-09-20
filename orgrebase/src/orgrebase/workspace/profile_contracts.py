"""Immutable enterprise Seed/Profile contracts and validator-owned policy.

This module contains only contract types, enums, constants, and their validators.
Construction of the shipped reference profiles and admission workflows live in
separate modules; :mod:`orgrebase.workspace.profile` remains the stable public
facade.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.domain import ContentAddressedModel
from orgrebase.workspace.models import TaskRequest

REFERENCE_HANDLER_PROFILE = "northstar-acme-quote-v1"
ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE = "enterprise-quote-pilot-v1"
PROFILE_SCHEMA_VERSION = "orgrebase.enterprise-seed-profile.v1"
RECEIPT_SCHEMA_VERSION = "orgrebase.enterprise-seed-admission-receipt.v1"

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class EnterpriseSeedAdmissionError(ValueError):
    """Stable, public failure code for a rejected Seed/Profile intake."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        message = code if not detail else f"{code}:{detail}"
        super().__init__(message)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SeedComponentKind(StrEnum):
    DOMAIN = "DOMAIN"
    KNOWLEDGE = "KNOWLEDGE"
    AUTHORITY = "AUTHORITY"
    CAPABILITY = "CAPABILITY"
    DEPENDENCY = "DEPENDENCY"


class SeedCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SeedGapKind(StrEnum):
    MISSING_SOURCE = "MISSING_SOURCE"
    INCOMPLETE_COVERAGE = "INCOMPLETE_COVERAGE"
    UNKNOWN_AUTHORITY = "UNKNOWN_AUTHORITY"
    UNVERIFIED_DEPENDENCY = "UNVERIFIED_DEPENDENCY"
    MISSING_OUTCOME_BASELINE = "MISSING_OUTCOME_BASELINE"


class ReadinessGate(StrEnum):
    SEED_READY = "SEED_READY"
    SHADOW_READY = "SHADOW_READY"
    REFERENCE_RUNTIME = "REFERENCE_RUNTIME"
    OUTCOME_ASSURANCE = "OUTCOME_ASSURANCE"


class EffectCeiling(StrEnum):
    ZERO_EXTERNAL_EFFECTS = "ZERO_EXTERNAL_EFFECTS"
    EXTERNAL_EFFECTS = "EXTERNAL_EFFECTS"


class SeedDataClass(StrEnum):
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"


class GrowthLevel(StrEnum):
    G0_DESCRIBE = "G0_DESCRIBE"
    G1_SELECT = "G1_SELECT"
    G2_BOUNDED_COMPOSE = "G2_BOUNDED_COMPOSE"


class AdmittedClaimCeiling(StrEnum):
    INTAKE_VALIDATED = "INTAKE_VALIDATED"
    REFERENCE_PROFILE_INTAKE = "REFERENCE_PROFILE_INTAKE"


class ProfileLimitationCode(StrEnum):
    SYNTHETIC_DATA_ONLY = "SYNTHETIC_DATA_ONLY"
    NO_EXTERNAL_CONNECTORS = "NO_EXTERNAL_CONNECTORS"
    NO_OUTCOME_ASSURANCE = "NO_OUTCOME_ASSURANCE"
    INTAKE_ONLY = "INTAKE_ONLY"
    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    OPEN_GAPS_RETAINED = "OPEN_GAPS_RETAINED"
    NO_EXTERNAL_ENTERPRISE_VALIDATION = "NO_EXTERNAL_ENTERPRISE_VALIDATION"


class RuntimeCompatibilityMode(StrEnum):
    REFERENCE_HANDLER = "REFERENCE_HANDLER"
    INTAKE_ONLY = "INTAKE_ONLY"


class AdmissionDimensionStatus(StrEnum):
    PASS = "PASS"
    HOLD = "HOLD"
    REJECT = "REJECT"


_GAP_ALLOWED_COMPONENTS: dict[SeedGapKind, frozenset[SeedComponentKind]] = {
    SeedGapKind.MISSING_SOURCE: frozenset(SeedComponentKind),
    SeedGapKind.INCOMPLETE_COVERAGE: frozenset(SeedComponentKind),
    SeedGapKind.UNKNOWN_AUTHORITY: frozenset({SeedComponentKind.AUTHORITY}),
    SeedGapKind.UNVERIFIED_DEPENDENCY: frozenset({SeedComponentKind.DEPENDENCY}),
    SeedGapKind.MISSING_OUTCOME_BASELINE: frozenset({SeedComponentKind.DEPENDENCY}),
}


def minimum_gap_gates(
    kind: SeedGapKind,
    component: SeedComponentKind,
) -> tuple[ReadinessGate, ...]:
    """Return validator-owned minimum blockers for a typed enterprise Gap."""

    if component not in _GAP_ALLOWED_COMPONENTS[kind]:
        raise ValueError("PROFILE_GAP_KIND_COMPONENT_MISMATCH")
    if kind == SeedGapKind.MISSING_SOURCE:
        minimum = {
            ReadinessGate.SEED_READY,
            ReadinessGate.SHADOW_READY,
            ReadinessGate.REFERENCE_RUNTIME,
        }
    elif kind == SeedGapKind.UNKNOWN_AUTHORITY:
        minimum = {
            ReadinessGate.SHADOW_READY,
            ReadinessGate.REFERENCE_RUNTIME,
            ReadinessGate.OUTCOME_ASSURANCE,
        }
    elif kind == SeedGapKind.UNVERIFIED_DEPENDENCY:
        minimum = {
            ReadinessGate.REFERENCE_RUNTIME,
            ReadinessGate.OUTCOME_ASSURANCE,
        }
    elif kind == SeedGapKind.MISSING_OUTCOME_BASELINE:
        minimum = {ReadinessGate.OUTCOME_ASSURANCE}
    else:
        minimum = {ReadinessGate.REFERENCE_RUNTIME}
        if component == SeedComponentKind.AUTHORITY:
            minimum.add(ReadinessGate.SHADOW_READY)
    return tuple(gate for gate in ReadinessGate if gate in minimum)


class SeedInputValue(FrozenModel):
    key: str = Field(min_length=1)
    # F1 deliberately keeps task literals scalar.  A nested JSON mapping would
    # remain mutable even when its Pydantic parent is frozen and would weaken the
    # profile digest boundary.
    value: str | int | float | bool | None


class DefaultTaskBinding(FrozenModel):
    id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    deliverable_kind: str = Field(min_length=1)
    requested_at: str = Field(min_length=1)
    template_ref: str = Field(min_length=1)
    customer_id: str | None = None
    idempotency_key: str = Field(min_length=1)
    input_values: tuple[SeedInputValue, ...] = ()

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        keys = tuple(item.key for item in self.input_values)
        if len(keys) != len(set(keys)):
            raise ValueError("PROFILE_DUPLICATE_TASK_INPUT_KEY")
        return self

    def to_task_request(self, organization_id: str) -> TaskRequest:
        return TaskRequest(
            id=self.id,
            organization_id=organization_id,
            actor_id=self.actor_id,
            purpose=self.purpose,
            deliverable_kind=self.deliverable_kind,
            requested_at=self.requested_at,
            template_ref=self.template_ref,
            input_values={item.key: item.value for item in self.input_values},
            customer_id=self.customer_id,
            idempotency_key=self.idempotency_key,
        )


class SeedSourceRoot(FrozenModel):
    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    declared_digest: str

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        if not _DIGEST_PATTERN.fullmatch(self.declared_digest):
            raise ValueError("PROFILE_SOURCE_ROOT_DIGEST_INVALID")
        return self


class SeedComponent(FrozenModel):
    kind: SeedComponentKind
    completeness: SeedCompleteness
    source_root_refs: tuple[str, ...]
    declared_digest: str | None = None

    @model_validator(mode="after")
    def validate_declaration(self) -> Self:
        if not self.source_root_refs:
            raise ValueError("PROFILE_COMPONENT_SOURCE_ROOT_MISSING")
        if self.declared_digest is not None and not _DIGEST_PATTERN.fullmatch(self.declared_digest):
            raise ValueError("PROFILE_COMPONENT_DIGEST_INVALID")
        if self.completeness == SeedCompleteness.COMPLETE and self.declared_digest is None:
            raise ValueError("PROFILE_COMPLETE_COMPONENT_DIGEST_MISSING")
        return self


class EnterpriseSeedGap(ContentAddressedModel):
    id: str = Field(min_length=1)
    kind: SeedGapKind
    component: SeedComponentKind
    path: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owner_ref: str = Field(min_length=1)
    resolution_gate_ref: str = Field(min_length=1)
    blocks: tuple[ReadinessGate, ...]
    status: Literal["OPEN"] = "OPEN"

    @model_validator(mode="after")
    def validate_blocks(self) -> Self:
        if not self.blocks:
            raise ValueError("PROFILE_GAP_BLOCKS_MISSING")
        if len(self.blocks) != len(set(self.blocks)):
            raise ValueError("PROFILE_GAP_DUPLICATE_GATE")
        minimum = set(minimum_gap_gates(self.kind, self.component))
        if not minimum.issubset(self.blocks):
            raise ValueError("PROFILE_GAP_BLOCKS_UNDERESTIMATED")
        return self


class GovernanceBinding(FrozenModel):
    admission_authority_refs: tuple[str, ...]
    owner_refs: tuple[str, ...]
    rejection_path_ref: str = Field(min_length=1)
    unknown_path_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if not self.admission_authority_refs or not self.owner_refs:
            raise ValueError("PROFILE_GOVERNANCE_PATH_MISSING")
        if len(self.admission_authority_refs) != len(set(self.admission_authority_refs)):
            raise ValueError("PROFILE_DUPLICATE_ADMISSION_AUTHORITY")
        if len(self.owner_refs) != len(set(self.owner_refs)):
            raise ValueError("PROFILE_DUPLICATE_GOVERNANCE_OWNER")
        return self


class ChangeFamilyBinding(FrozenModel):
    kind: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    base_version: str = Field(min_length=1)
    proposed_version: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class RuntimeCompatibilityDeclaration(FrozenModel):
    mode: RuntimeCompatibilityMode
    handler_profile: str | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.mode == RuntimeCompatibilityMode.REFERENCE_HANDLER:
            if not self.handler_profile:
                raise ValueError("PROFILE_HANDLER_DECLARATION_MISSING")
        elif self.handler_profile is not None:
            raise ValueError("PROFILE_INTAKE_ONLY_HANDLER_MUST_BE_NULL")
        return self


class EnterpriseSeedProfile(ContentAddressedModel):
    """Versioned, content-addressed enterprise intake package.

    All collections are tuples of frozen models; callers cannot mutate a nested
    mapping after the profile digest has been computed.
    """

    schema_version: Literal["orgrebase.enterprise-seed-profile.v1"] = PROFILE_SCHEMA_VERSION
    profile_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    synthetic: bool
    source_roots: tuple[SeedSourceRoot, ...]
    components: tuple[SeedComponent, ...]
    governance: GovernanceBinding
    default_task: DefaultTaskBinding
    change_family: tuple[ChangeFamilyBinding, ...] = ()
    gaps: tuple[EnterpriseSeedGap, ...] = ()
    effect_ceiling: EffectCeiling
    data_class: SeedDataClass
    requested_growth_level: GrowthLevel
    runtime_compatibility: RuntimeCompatibilityDeclaration
    declared_limitation_codes: tuple[ProfileLimitationCode, ...]

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if not self.source_roots:
            raise ValueError("PROFILE_SOURCE_ROOTS_MISSING")
        source_ids = tuple(item.id for item in self.source_roots)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("PROFILE_DUPLICATE_SOURCE_ROOT_ID")

        component_kinds = tuple(item.kind for item in self.components)
        required_components = set(SeedComponentKind)
        if len(component_kinds) != len(set(component_kinds)):
            raise ValueError("PROFILE_DUPLICATE_COMPONENT_KIND")
        if set(component_kinds) != required_components:
            raise ValueError("PROFILE_COMPONENT_SET_INCOMPLETE")
        source_id_set = set(source_ids)
        if any(
            source_ref not in source_id_set
            for component in self.components
            for source_ref in component.source_root_refs
        ):
            raise ValueError("PROFILE_COMPONENT_SOURCE_ROOT_UNKNOWN")

        change_kinds = tuple(item.kind for item in self.change_family)
        if len(change_kinds) != len(set(change_kinds)):
            raise ValueError("PROFILE_DUPLICATE_CHANGE_KIND")
        change_ids = tuple(item.change_id for item in self.change_family)
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("PROFILE_DUPLICATE_CHANGE_ID")
        missing_owners = {
            item.owner_id for item in self.change_family if item.owner_id not in self.governance.owner_refs
        }
        if missing_owners:
            raise ValueError("PROFILE_CHANGE_OWNER_NOT_GOVERNED")
        if self.default_task.actor_id not in self.governance.owner_refs:
            raise ValueError("PROFILE_TASK_ACTOR_NOT_GOVERNED")

        gap_ids = tuple(item.id for item in self.gaps)
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("PROFILE_DUPLICATE_GAP_ID")
        component_by_kind = {item.kind: item for item in self.components}
        incomplete_kinds = {
            item.kind for item in self.components if item.completeness != SeedCompleteness.COMPLETE
        }
        gap_kinds = {item.component for item in self.gaps}
        if incomplete_kinds - gap_kinds:
            raise ValueError("PROFILE_GAP_ERASURE_DETECTED")
        if any(
            component_by_kind[gap.component].completeness == SeedCompleteness.COMPLETE for gap in self.gaps
        ):
            raise ValueError("PROFILE_GAP_COMPONENT_NOT_INCOMPLETE")
        if any(gap.owner_ref not in self.governance.owner_refs for gap in self.gaps):
            raise ValueError("PROFILE_GAP_OWNER_NOT_GOVERNED")

        if self.effect_ceiling != EffectCeiling.ZERO_EXTERNAL_EFFECTS:
            raise ValueError("PROFILE_EFFECT_CEILING_INFLATION")
        if self.synthetic != (self.data_class == SeedDataClass.SYNTHETIC_FIXTURE):
            raise ValueError("PROFILE_SYNTHETIC_DATA_CLASS_MISMATCH")
        if (
            self.runtime_compatibility.mode == RuntimeCompatibilityMode.REFERENCE_HANDLER
            and self.requested_growth_level != GrowthLevel.G1_SELECT
        ):
            raise ValueError("PROFILE_REFERENCE_HANDLER_GROWTH_MISMATCH")
        if (
            self.runtime_compatibility.mode == RuntimeCompatibilityMode.INTAKE_ONLY
            and self.requested_growth_level != GrowthLevel.G0_DESCRIBE
        ):
            raise ValueError("PROFILE_INTAKE_ONLY_GROWTH_INFLATION")
        intake_limited = ProfileLimitationCode.INTAKE_ONLY in self.declared_limitation_codes
        if intake_limited != (self.runtime_compatibility.mode == RuntimeCompatibilityMode.INTAKE_ONLY):
            raise ValueError("PROFILE_INTAKE_LIMITATION_RUNTIME_MISMATCH")
        if not self.declared_limitation_codes:
            raise ValueError("PROFILE_LIMITATIONS_MISSING")
        if len(self.declared_limitation_codes) != len(set(self.declared_limitation_codes)):
            raise ValueError("PROFILE_DUPLICATE_LIMITATION")
        return self

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.revision}"

    def task_request(self) -> TaskRequest:
        return self.default_task.to_task_request(self.organization_id)

    def scenario_view(self) -> dict[str, object]:
        return {
            "id": self.scenario_id,
            "organization_id": self.organization_id,
            "customer_id": self.default_task.customer_id,
            "task_id": self.default_task.id,
            "synthetic": self.synthetic,
        }


class AdmissionDimension(FrozenModel):
    name: Literal[
        "PARSE",
        "SOURCE_BYTES",
        "SEED_READY",
        "SHADOW_INTAKE_ADMISSIBLE",
        "REFERENCE_RUNTIME",
    ]
    status: AdmissionDimensionStatus
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_reasons(self) -> Self:
        if not self.reason_codes:
            raise ValueError("PROFILE_ADMISSION_DIMENSION_REASONS_MISSING")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("PROFILE_ADMISSION_DIMENSION_REASON_DUPLICATE")
        return self


class AdmittedGapAction(FrozenModel):
    gap_ref: str = Field(min_length=1)
    gap_digest: str
    kind: SeedGapKind
    component: SeedComponentKind
    owner_ref: str = Field(min_length=1)
    effective_blocks: tuple[ReadinessGate, ...]
    minimum_blocks: tuple[ReadinessGate, ...]
    resolution_gate_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if not _DIGEST_PATTERN.fullmatch(self.gap_digest):
            raise ValueError("PROFILE_ADMITTED_GAP_DIGEST_INVALID")
        if not self.minimum_blocks or not self.effective_blocks:
            raise ValueError("PROFILE_ADMITTED_GAP_BLOCKS_MISSING")
        if len(self.minimum_blocks) != len(set(self.minimum_blocks)) or len(self.effective_blocks) != len(
            set(self.effective_blocks)
        ):
            raise ValueError("PROFILE_ADMITTED_GAP_BLOCKS_DUPLICATE")
        if not set(self.minimum_blocks).issubset(self.effective_blocks):
            raise ValueError("PROFILE_ADMITTED_GAP_POLICY_MISMATCH")
        return self


class EnterpriseSeedAdmissionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.enterprise-seed-admission-receipt.v1"] = RECEIPT_SCHEMA_VERSION
    profile_ref: str
    profile_digest: str
    source_admission_receipt_digest: str
    admitted_source_root_digests: tuple[str, ...]
    component_admission_digests: tuple[str, ...]
    source_profile_projection_digest: str
    parsed: Literal[True] = True
    seed_ready: bool
    shadow_intake_admissible: bool
    reference_runtime_compatible: bool
    dimensions: tuple[AdmissionDimension, ...]
    gap_actions: tuple[AdmittedGapAction, ...]
    admitted_claim_ceiling: AdmittedClaimCeiling
    verified_growth_level: GrowthLevel
    canonical_target_writes: Literal[0] = 0
    limitations: tuple[ProfileLimitationCode, ...]

    @model_validator(mode="after")
    def validate_receipt_consistency(self) -> Self:
        if not _DIGEST_PATTERN.fullmatch(self.profile_digest):
            raise ValueError("PROFILE_ADMISSION_DIGEST_INVALID")
        names = tuple(item.name for item in self.dimensions)
        expected_names = {
            "PARSE",
            "SOURCE_BYTES",
            "SEED_READY",
            "SHADOW_INTAKE_ADMISSIBLE",
            "REFERENCE_RUNTIME",
        }
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise ValueError("PROFILE_ADMISSION_DIMENSIONS_INVALID")
        if self.reference_runtime_compatible and not self.shadow_intake_admissible:
            raise ValueError("PROFILE_ADMISSION_RUNTIME_WITHOUT_SHADOW")
        if self.shadow_intake_admissible and not self.seed_ready:
            raise ValueError("PROFILE_ADMISSION_SHADOW_WITHOUT_SEED")
        dimensions = {item.name: item for item in self.dimensions}
        expected_statuses = {
            "PARSE": AdmissionDimensionStatus.PASS,
            "SOURCE_BYTES": AdmissionDimensionStatus.PASS,
            "SEED_READY": (
                AdmissionDimensionStatus.PASS if self.seed_ready else AdmissionDimensionStatus.HOLD
            ),
            "SHADOW_INTAKE_ADMISSIBLE": (
                AdmissionDimensionStatus.PASS
                if self.shadow_intake_admissible
                else AdmissionDimensionStatus.HOLD
            ),
            "REFERENCE_RUNTIME": (
                AdmissionDimensionStatus.PASS
                if self.reference_runtime_compatible
                else AdmissionDimensionStatus.REJECT
            ),
        }
        if any(
            dimensions[name].status != expected_status for name, expected_status in expected_statuses.items()
        ):
            raise ValueError("PROFILE_ADMISSION_DIMENSION_STATUS_MISMATCH")
        digest_values = (
            self.source_admission_receipt_digest,
            self.source_profile_projection_digest,
            *self.admitted_source_root_digests,
            *self.component_admission_digests,
        )
        if any(not _DIGEST_PATTERN.fullmatch(value) for value in digest_values):
            raise ValueError("PROFILE_ADMISSION_SOURCE_DIGEST_INVALID")
        expected_count = len(SeedComponentKind)
        if (
            len(self.admitted_source_root_digests) != expected_count
            or len(self.component_admission_digests) != expected_count
            or len(set(self.admitted_source_root_digests)) != expected_count
            or len(set(self.component_admission_digests)) != expected_count
        ):
            raise ValueError("PROFILE_ADMISSION_SOURCE_DIGEST_SET_INVALID")
        expected_claim = (
            AdmittedClaimCeiling.REFERENCE_PROFILE_INTAKE
            if self.reference_runtime_compatible
            else AdmittedClaimCeiling.INTAKE_VALIDATED
        )
        if self.admitted_claim_ceiling != expected_claim:
            raise ValueError("PROFILE_ADMISSION_CLAIM_INCONSISTENT")
        expected_growth = (
            GrowthLevel.G1_SELECT if self.reference_runtime_compatible else GrowthLevel.G0_DESCRIBE
        )
        if self.verified_growth_level != expected_growth:
            raise ValueError("PROFILE_ADMISSION_GROWTH_INCONSISTENT")
        gap_refs = tuple(action.gap_ref for action in self.gap_actions)
        if len(gap_refs) != len(set(gap_refs)):
            raise ValueError("PROFILE_ADMISSION_GAP_ACTION_DUPLICATE")
        if not self.limitations:
            raise ValueError("PROFILE_ADMISSION_LIMITATIONS_MISSING")
        if len(self.limitations) != len(set(self.limitations)):
            raise ValueError("PROFILE_ADMISSION_LIMITATIONS_DUPLICATE")
        return self
