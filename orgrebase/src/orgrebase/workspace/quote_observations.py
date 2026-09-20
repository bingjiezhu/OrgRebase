"""Strict enterprise-quote shadow observation admission.

The module defines the data boundary required to replace QuoteValue's explicit
``NOT_RUN`` enterprise fields.  It deliberately accepts de-identified event
records only, never writes canonical business state, and never upgrades a
synthetic fixture into enterprise evidence by label alone.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
PROFILE_ID = "ENTERPRISE_QUOTE_OPERATOR_V1"
OBSERVATION_SCHEMA_VERSION = "orgrebase.workspace-quote-process-observation-record.v1"
RECEIPT_SCHEMA_VERSION = "orgrebase.workspace-quote-shadow-admission-receipt.v1"

STEP_IDS = (
    "quote-step:intake",
    "quote-step:product-confirmation",
    "quote-step:legal-confirmation",
    "quote-step:finance-confirmation",
    "quote-step:composition",
    "quote-step:delivery-acceptance",
    "quote-step:change-impact",
    "quote-step:selective-update",
)

CORE_HUMAN_ROLES = frozenset({"Quote Operator", "Product Owner", "Legal Owner", "Finance Owner", "GTM Owner"})

EXPECTED_ROLE_CLASSES = {
    "Quote Operator": "HUMAN_OPERATOR",
    "Product Owner": "HUMAN_OWNER",
    "Legal Owner": "HUMAN_OWNER",
    "Finance Owner": "HUMAN_OWNER",
    "GTM Owner": "HUMAN_OWNER",
    "Product Agent": "WORKER_AGENT",
    "Legal Agent": "WORKER_AGENT",
    "Finance Agent": "WORKER_AGENT",
    "GTM Agent": "WORKER_AGENT",
    "Deterministic Control": "CONTROL_PLANE",
    "Bounded Runtime": "BOUNDED_RUNTIME",
    "Canonical Writer": "CANONICAL_WRITER",
}

RESPONSIBLE_BY_STEP = {
    "quote-step:intake": ("Quote Operator",),
    "quote-step:product-confirmation": ("Product Agent",),
    "quote-step:legal-confirmation": ("Legal Agent",),
    "quote-step:finance-confirmation": ("Finance Agent",),
    "quote-step:composition": ("GTM Agent",),
    "quote-step:delivery-acceptance": ("Quote Operator",),
    "quote-step:change-impact": ("Deterministic Control",),
    "quote-step:selective-update": ("Bounded Runtime", "Canonical Writer"),
}

ACCOUNTABLE_BY_STEP = {
    "quote-step:intake": frozenset({"GTM Owner"}),
    "quote-step:product-confirmation": frozenset({"Product Owner"}),
    "quote-step:legal-confirmation": frozenset({"Legal Owner"}),
    "quote-step:finance-confirmation": frozenset({"Finance Owner"}),
    "quote-step:composition": frozenset({"Quote Operator"}),
    "quote-step:delivery-acceptance": frozenset({"GTM Owner"}),
    "quote-step:change-impact": frozenset({"Product Owner", "Legal Owner", "Finance Owner", "GTM Owner"}),
    "quote-step:selective-update": frozenset({"Product Owner", "Legal Owner", "Finance Owner", "GTM Owner"}),
}

SYSTEM_BY_STEP = {
    "quote-step:intake": "CRM_OR_CPQ_REQUEST_QUEUE",
    "quote-step:product-confirmation": "PRODUCT_CATALOG_OR_RELEASE_SOURCE",
    "quote-step:legal-confirmation": "CLM_OR_RESTRICTED_LEGAL_SOURCE",
    "quote-step:finance-confirmation": "ERP_OR_PRICING_POLICY_SOURCE",
    "quote-step:composition": "DOCUMENT_OR_CPQ_COMPOSER",
    "quote-step:delivery-acceptance": "CPQ_OR_DOCUMENT_OUTPUT",
    "quote-step:change-impact": "MANUAL_CROSS_SYSTEM_CHANGE_REVIEW",
    "quote-step:selective-update": "MANUAL_DOCUMENT_AND_SYSTEM_REWORK",
}

METRIC_IDS = (
    "quote_cycle_elapsed_minutes",
    "policy_confirmation_active_minutes",
    "adjudicated_impact_omission_rate",
    "first_pass_rework_rate",
    "unauthorized_access_success_rate",
    "manual_escalation_case_rate",
    "realized_selective_rebase_cost_saving_rate",
)

METRIC_FORMULAS = {
    "quote_cycle_elapsed_minutes": "sum(delivery_accepted_at-request_admitted_at)/accepted_quotes",
    "policy_confirmation_active_minutes": "sum(policy_owner_active_interval_minutes)/accepted_quotes",
    "adjudicated_impact_omission_rate": "expected_affected_targets_not_identified/adjudicated_expected_affected_targets",
    "first_pass_rework_rate": "accepted_quotes_requiring_correction/accepted_quotes",
    "unauthorized_access_success_rate": "unauthorized_attempts_succeeded/unauthorized_attempts",
    "manual_escalation_case_rate": "admitted_quotes_with_manual_escalation/admitted_quotes",
    "realized_selective_rebase_cost_saving_rate": "sum(observed_matched_full_comparator-selective_actual)/sum(observed_matched_full_comparator)",
}

METRIC_UNITS = {
    "quote_cycle_elapsed_minutes": "MINUTE_PER_ACCEPTED_QUOTE",
    "policy_confirmation_active_minutes": "MINUTE_PER_ACCEPTED_QUOTE",
    "adjudicated_impact_omission_rate": "RATIO",
    "first_pass_rework_rate": "RATIO",
    "unauthorized_access_success_rate": "RATIO",
    "manual_escalation_case_rate": "RATIO",
    "realized_selective_rebase_cost_saving_rate": "RATIO",
}


class QuoteShadowAdmissionError(ValueError):
    """Stable failure code for rejected observation bytes."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}:{detail}")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class ShadowEvidenceClass(StrEnum):
    NOT_RUN = "NOT_RUN"
    SYNTHETIC_CONTRACT_FIXTURE = "SYNTHETIC_CONTRACT_FIXTURE"
    OBSERVED_ENTERPRISE_SHADOW = "OBSERVED_ENTERPRISE_SHADOW"


class SourceProvenance(StrEnum):
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    ENTERPRISE_EXTRACT = "ENTERPRISE_EXTRACT"


class SourceKind(StrEnum):
    WORKFLOW_EVENT_LOG = "WORKFLOW_EVENT_LOG"
    ACTIVE_TIME_LOG = "ACTIVE_TIME_LOG"
    ACCESS_EVENT_LOG = "ACCESS_EVENT_LOG"
    IMPACT_ADJUDICATION_LOG = "IMPACT_ADJUDICATION_LOG"
    COST_LEDGER = "COST_LEDGER"


class RoleClass(StrEnum):
    HUMAN_OPERATOR = "HUMAN_OPERATOR"
    HUMAN_OWNER = "HUMAN_OWNER"
    WORKER_AGENT = "WORKER_AGENT"
    CONTROL_PLANE = "CONTROL_PLANE"
    BOUNDED_RUNTIME = "BOUNDED_RUNTIME"
    CANONICAL_WRITER = "CANONICAL_WRITER"


class ObservationWindow(FrozenModel):
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        values = (self.started_at, self.ended_at, self.timezone)
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("OBSERVATION_WINDOW_PARTIAL")
        assert self.started_at is not None and self.ended_at is not None
        if self.ended_at <= self.started_at:
            raise ValueError("OBSERVATION_WINDOW_ORDER_INVALID")
        return self


class RoleEntry(FrozenModel):
    role_id: str = Field(min_length=1)
    role_class: RoleClass


class RoleRegistry(FrozenModel):
    version: Literal["enterprise-quote-roles@1.0.0"]
    roles: tuple[RoleEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_registry(self) -> Self:
        observed = {item.role_id: item.role_class.value for item in self.roles}
        if len(observed) != len(self.roles):
            raise ValueError("ROLE_REGISTRY_DUPLICATE")
        if observed != EXPECTED_ROLE_CLASSES:
            raise ValueError("ROLE_REGISTRY_SEMANTIC_DRIFT")
        return self


class SystemClassRegistry(FrozenModel):
    version: Literal["enterprise-quote-systems@1.0.0"]
    classes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_registry(self) -> Self:
        if self.classes != tuple(SYSTEM_BY_STEP.values()):
            raise ValueError("SYSTEM_CLASS_REGISTRY_SEMANTIC_DRIFT")
        return self


class SourceFileRef(FrozenModel):
    id: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")
    path: str = Field(min_length=1)
    file_sha256: str = Field(pattern=DIGEST_PATTERN)
    kind: SourceKind
    provenance: SourceProvenance
    complete_for_window: Literal[True]


class ApprovalDecision(FrozenModel):
    outcome: Literal["APPROVED"]
    approver_role: str = Field(min_length=1)
    decided_at: AwareDatetime


class StepExecution(FrozenModel):
    step_id: str = Field(pattern=r"^quote-step:[a-z0-9-]+$")
    system_class: str = Field(min_length=1)
    system_instance_ref: str = Field(pattern=DIGEST_PATTERN)
    responsible_roles: tuple[str, ...] = Field(min_length=1)
    accountable_role: str = Field(min_length=1)
    queued_at: AwareDatetime
    started_at: AwareDatetime
    completed_at: AwareDatetime
    accepted_at: AwareDatetime
    approval: ApprovalDecision
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if not (self.queued_at <= self.started_at <= self.completed_at <= self.accepted_at):
            raise ValueError("STEP_TIMELINE_ORDER_INVALID")
        if not (self.started_at <= self.approval.decided_at <= self.accepted_at):
            raise ValueError("STEP_APPROVAL_TIME_INVALID")
        if self.approval.approver_role != self.accountable_role:
            raise ValueError("STEP_APPROVER_NOT_ACCOUNTABLE")
        return self


class ActiveInterval(FrozenModel):
    owner_role: str = Field(min_length=1)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.ended_at <= self.started_at:
            raise ValueError("ACTIVE_INTERVAL_ORDER_INVALID")
        if self.owner_role not in {"Product Owner", "Legal Owner", "Finance Owner"}:
            raise ValueError("ACTIVE_INTERVAL_OWNER_INVALID")
        return self


class ImpactAdjudication(FrozenModel):
    change_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    expected_affected: bool
    identified_affected: bool
    adjudicator_role: str = Field(min_length=1)
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")


class DeliveryCorrection(FrozenModel):
    required: bool
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")


class AccessAttempt(FrozenModel):
    actor_role: str = Field(min_length=1)
    authorized: bool
    succeeded: bool
    occurred_at: AwareDatetime
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")


class ManualEscalation(FrozenModel):
    reason: str = Field(min_length=1)
    owner_role: str = Field(min_length=1)
    opened_at: AwareDatetime
    closed_at: AwareDatetime
    terminal_status: Literal["RESOLVED", "REJECTED"]
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.closed_at < self.opened_at:
            raise ValueError("ESCALATION_TIMELINE_ORDER_INVALID")
        if self.owner_role not in CORE_HUMAN_ROLES:
            raise ValueError("ESCALATION_OWNER_INVALID")
        return self


class SelectiveRebaseCostObservation(FrozenModel):
    full_rebuild_comparator_cost: float = Field(gt=0)
    selective_rebase_actual_cost: float = Field(ge=0)
    unit: str = Field(min_length=1)
    comparator_basis: Literal["OBSERVED_MATCHED_COMPARATOR", "MODELLED_COUNTERFACTUAL"]
    source_ref: str = Field(pattern=r"^source:[a-z0-9][a-z0-9._-]*$")


class QuoteObservation(FrozenModel):
    quote_id: str = Field(min_length=1)
    request_admitted_at: AwareDatetime
    delivery_accepted_at: AwareDatetime
    step_executions: tuple[StepExecution, ...] = Field(min_length=8, max_length=8)
    policy_owner_active_intervals: tuple[ActiveInterval, ...] = ()
    impact_adjudications: tuple[ImpactAdjudication, ...] = ()
    delivery_correction: DeliveryCorrection
    access_attempts: tuple[AccessAttempt, ...] = ()
    manual_escalations: tuple[ManualEscalation, ...] = ()
    selective_rebase_cost: SelectiveRebaseCostObservation | None = None

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if self.delivery_accepted_at <= self.request_admitted_at:
            raise ValueError("QUOTE_CYCLE_ORDER_INVALID")
        if tuple(item.step_id for item in self.step_executions) != STEP_IDS:
            raise ValueError("PROCESS_STEP_SET_OR_ORDER_INVALID")
        if self.request_admitted_at != self.step_executions[0].accepted_at:
            raise ValueError("QUOTE_ADMISSION_NOT_BOUND_TO_INTAKE_ACCEPTANCE")
        if self.delivery_accepted_at != self.step_executions[5].accepted_at:
            raise ValueError("QUOTE_DELIVERY_NOT_BOUND_TO_DELIVERY_ACCEPTANCE")
        covered_roles: set[str] = set()
        for item in self.step_executions:
            if item.system_class != SYSTEM_BY_STEP[item.step_id]:
                raise ValueError(f"SYSTEM_CLASS_INVALID:{item.step_id}")
            if item.responsible_roles != RESPONSIBLE_BY_STEP[item.step_id]:
                raise ValueError(f"RESPONSIBLE_ROLE_INVALID:{item.step_id}")
            if item.accountable_role not in ACCOUNTABLE_BY_STEP[item.step_id]:
                raise ValueError(f"ACCOUNTABLE_ROLE_INVALID:{item.step_id}")
            covered_roles.update(item.responsible_roles)
            covered_roles.add(item.accountable_role)
        if not covered_roles >= CORE_HUMAN_ROLES:
            raise ValueError("FIVE_PARTY_ROLE_COVERAGE_INCOMPLETE")
        keys = tuple((item.change_id, item.target_id) for item in self.impact_adjudications)
        if len(keys) != len(set(keys)):
            raise ValueError("IMPACT_ADJUDICATION_DUPLICATE")
        return self


class QuoteProcessObservationRecord(ContentAddressedModel):
    schema_version: Literal["orgrebase.workspace-quote-process-observation-record.v1"]
    id: str = Field(min_length=1)
    prepared_at: AwareDatetime
    process_profile: Literal["ENTERPRISE_QUOTE_OPERATOR_V1"]
    evidence_class: ShadowEvidenceClass
    enterprise_ref: str | None = None
    observation_window: ObservationWindow
    role_registry: RoleRegistry
    system_class_registry: SystemClassRegistry
    source_file_refs: tuple[SourceFileRef, ...] = ()
    quote_observations: tuple[QuoteObservation, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_semantics(self) -> Self:
        source_ids = tuple(item.id for item in self.source_file_refs)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("SOURCE_ID_DUPLICATE")
        source_paths = tuple(item.path for item in self.source_file_refs)
        if len(source_paths) != len(set(source_paths)):
            raise ValueError("SOURCE_PATH_DUPLICATE")

        if self.evidence_class == ShadowEvidenceClass.NOT_RUN:
            if self.enterprise_ref is not None or self.source_file_refs or self.quote_observations:
                raise ValueError("NOT_RUN_RECORD_CONTAINS_OBSERVATIONS")
            if any(
                value is not None
                for value in (
                    self.observation_window.started_at,
                    self.observation_window.ended_at,
                    self.observation_window.timezone,
                )
            ):
                raise ValueError("NOT_RUN_RECORD_CONTAINS_WINDOW")
            return self

        if not isinstance(self.enterprise_ref, str) or not _valid_digest(self.enterprise_ref):
            raise ValueError("ENTERPRISE_REF_INVALID")
        if (
            self.observation_window.started_at is None
            or self.observation_window.ended_at is None
            or self.observation_window.timezone is None
        ):
            raise ValueError("OBSERVATION_WINDOW_REQUIRED")
        if not self.source_file_refs or not self.quote_observations:
            raise ValueError("OBSERVATION_EVENTS_REQUIRED")
        expected_provenance = (
            SourceProvenance.SYNTHETIC_FIXTURE
            if self.evidence_class == ShadowEvidenceClass.SYNTHETIC_CONTRACT_FIXTURE
            else SourceProvenance.ENTERPRISE_EXTRACT
        )
        if any(item.provenance != expected_provenance for item in self.source_file_refs):
            raise ValueError("EVIDENCE_CLASS_SOURCE_PROVENANCE_MISMATCH")

        sources = {item.id: item for item in self.source_file_refs}
        used_sources: set[str] = set()
        for quote in self.quote_observations:
            self._validate_quote_window_and_sources(quote, sources, used_sources)
        if used_sources != set(sources):
            raise ValueError("DECLARED_SOURCE_UNUSED")
        return self

    def _validate_quote_window_and_sources(
        self,
        quote: QuoteObservation,
        sources: dict[str, SourceFileRef],
        used_sources: set[str],
    ) -> None:
        started_at = self.observation_window.started_at
        ended_at = self.observation_window.ended_at
        assert started_at is not None and ended_at is not None

        def require_time(value: datetime, code: str) -> None:
            if not started_at <= value <= ended_at:
                raise ValueError(code)

        require_time(quote.request_admitted_at, "QUOTE_ADMISSION_OUTSIDE_WINDOW")
        require_time(quote.delivery_accepted_at, "QUOTE_ACCEPTANCE_OUTSIDE_WINDOW")

        def require_source(source_id: str, expected_kind: SourceKind) -> None:
            source = sources.get(source_id)
            if source is None:
                raise ValueError(f"EVENT_SOURCE_UNDECLARED:{source_id}")
            if source.kind != expected_kind:
                raise ValueError(f"EVENT_SOURCE_KIND_INVALID:{source_id}")
            used_sources.add(source_id)

        for step in quote.step_executions:
            for value in (
                step.queued_at,
                step.started_at,
                step.completed_at,
                step.accepted_at,
                step.approval.decided_at,
            ):
                require_time(value, "STEP_EVENT_OUTSIDE_WINDOW")
            require_source(step.source_ref, SourceKind.WORKFLOW_EVENT_LOG)
        require_source(quote.delivery_correction.source_ref, SourceKind.WORKFLOW_EVENT_LOG)
        for interval in quote.policy_owner_active_intervals:
            require_time(interval.started_at, "ACTIVE_INTERVAL_OUTSIDE_WINDOW")
            require_time(interval.ended_at, "ACTIVE_INTERVAL_OUTSIDE_WINDOW")
            require_source(interval.source_ref, SourceKind.ACTIVE_TIME_LOG)
        for item in quote.impact_adjudications:
            if item.adjudicator_role not in CORE_HUMAN_ROLES:
                raise ValueError("IMPACT_ADJUDICATOR_INVALID")
            require_source(item.source_ref, SourceKind.IMPACT_ADJUDICATION_LOG)
        for attempt in quote.access_attempts:
            if attempt.actor_role not in EXPECTED_ROLE_CLASSES:
                raise ValueError("ACCESS_ACTOR_ROLE_INVALID")
            require_time(attempt.occurred_at, "ACCESS_EVENT_OUTSIDE_WINDOW")
            require_source(attempt.source_ref, SourceKind.ACCESS_EVENT_LOG)
        for escalation in quote.manual_escalations:
            require_time(escalation.opened_at, "ESCALATION_OUTSIDE_WINDOW")
            require_time(escalation.closed_at, "ESCALATION_OUTSIDE_WINDOW")
            require_source(escalation.source_ref, SourceKind.WORKFLOW_EVENT_LOG)
        if quote.selective_rebase_cost is not None:
            require_source(quote.selective_rebase_cost.source_ref, SourceKind.COST_LEDGER)


class MetricResult(FrozenModel):
    metric_id: str
    formula: str
    value: float | None
    numerator: float | None
    denominator: float | None
    unit: str
    status: Literal["CALCULATED", "NOT_RUN"]
    evidence_class: ShadowEvidenceClass
    observation_window: str
    source_refs: tuple[str, ...]
    limitation: str

    @model_validator(mode="after")
    def validate_metric(self) -> Self:
        if self.status == "NOT_RUN":
            if any(value is not None for value in (self.value, self.numerator, self.denominator)):
                raise ValueError("NOT_RUN_METRIC_HAS_VALUE")
            if self.evidence_class != ShadowEvidenceClass.NOT_RUN:
                raise ValueError("NOT_RUN_METRIC_EVIDENCE_CLASS_INVALID")
        else:
            if self.evidence_class == ShadowEvidenceClass.NOT_RUN:
                raise ValueError("CALCULATED_METRIC_EVIDENCE_CLASS_INVALID")
            if self.value is None or self.numerator is None or self.denominator is None:
                raise ValueError("CALCULATED_METRIC_VALUE_MISSING")
            if self.denominator <= 0:
                raise ValueError("CALCULATED_METRIC_ZERO_DENOMINATOR")
        return self


class SourceVerification(FrozenModel):
    id: str
    path: str
    file_sha256: str = Field(pattern=DIGEST_PATTERN)
    kind: SourceKind
    provenance: SourceProvenance
    complete_for_window: Literal[True]


class AdmissionGate(FrozenModel):
    id: str
    status: Literal["PASS", "NOT_RUN"]
    detail: str


class QuoteShadowAdmissionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.workspace-quote-shadow-admission-receipt.v1"]
    id: str
    assessed_at: AwareDatetime
    observation_record_id: str
    observation_record_digest: str = Field(pattern=DIGEST_PATTERN)
    admission_verdict: Literal["ADMIT"]
    measurement_status: Literal["CALCULATED", "NOT_RUN"]
    evidence_ceiling: ShadowEvidenceClass
    enterprise_ref: str | None
    observation_window: ObservationWindow
    gates: tuple[AdmissionGate, ...]
    source_evidence: tuple[SourceVerification, ...]
    metrics: tuple[MetricResult, ...] = Field(min_length=7, max_length=7)
    limitations: tuple[str, ...] = Field(min_length=1)


def _valid_digest(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and value[7:] != "0" * 64


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def stable_observation_record_ref(
    path: Path,
    *,
    project_root: Path,
    record_digest: str,
) -> str:
    """Return a publication-safe logical ref without leaking an absolute path."""

    resolved = path.resolve()
    try:
        relative = resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return f"content://{record_digest}"
    benchmark_prefix = "benchmark/quote-value-v0.2-shadow/"
    if relative.startswith(benchmark_prefix):
        return "benchmark://quote-value-v0.2-shadow/" + relative.removeprefix(benchmark_prefix)
    return f"project://{relative}"


def _resolve_source(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise QuoteShadowAdmissionError("SOURCE_PATH_ESCAPE", relative) from exc
    if not candidate.is_file():
        raise QuoteShadowAdmissionError("SOURCE_FILE_MISSING", relative)
    return candidate


def _verify_sources(
    record: QuoteProcessObservationRecord,
    source_root: Path,
) -> tuple[SourceVerification, ...]:
    verified: list[SourceVerification] = []
    for source in record.source_file_refs:
        path = _resolve_source(source_root, source.path)
        observed = _file_digest(path)
        if observed != source.file_sha256:
            raise QuoteShadowAdmissionError("SOURCE_DIGEST_MISMATCH", source.id)
        verified.append(
            SourceVerification(
                id=source.id,
                path=source.path,
                file_sha256=observed,
                kind=source.kind,
                provenance=source.provenance,
                complete_for_window=True,
            )
        )
    return tuple(verified)


def _minutes(started_at: datetime, ended_at: datetime) -> float:
    return (ended_at - started_at).total_seconds() / 60.0


def _window_label(window: ObservationWindow) -> str:
    if window.started_at is None or window.ended_at is None:
        return "NO_ENTERPRISE_SHADOW_WINDOW"
    return f"{window.started_at.isoformat()}..{window.ended_at.isoformat()}"


def _metric(
    metric_id: str,
    *,
    evidence_class: ShadowEvidenceClass,
    window: ObservationWindow,
    numerator: float | None,
    denominator: float | None,
    source_refs: set[str],
    limitation: str,
) -> MetricResult:
    if numerator is None or denominator is None or denominator <= 0:
        return MetricResult(
            metric_id=metric_id,
            formula=METRIC_FORMULAS[metric_id],
            value=None,
            numerator=None,
            denominator=None,
            unit=METRIC_UNITS[metric_id],
            status="NOT_RUN",
            evidence_class=ShadowEvidenceClass.NOT_RUN,
            observation_window=_window_label(window),
            source_refs=(),
            limitation=limitation,
        )
    value = numerator / denominator
    return MetricResult(
        metric_id=metric_id,
        formula=METRIC_FORMULAS[metric_id],
        value=round(value, 6),
        numerator=round(numerator, 6),
        denominator=round(denominator, 6),
        unit=METRIC_UNITS[metric_id],
        status="CALCULATED",
        evidence_class=evidence_class,
        observation_window=_window_label(window),
        source_refs=tuple(sorted(source_refs, key=str.encode)),
        limitation=limitation,
    )


def _project_metrics(record: QuoteProcessObservationRecord) -> tuple[MetricResult, ...]:
    quotes = record.quote_observations
    if not quotes:
        return tuple(
            _metric(
                metric_id,
                evidence_class=record.evidence_class,
                window=record.observation_window,
                numerator=None,
                denominator=None,
                source_refs=set(),
                limitation="No admitted observation events are available.",
            )
            for metric_id in METRIC_IDS
        )

    workflow_refs = {step.source_ref for quote in quotes for step in quote.step_executions}
    workflow_refs.update(quote.delivery_correction.source_ref for quote in quotes)
    accepted_quotes = float(len(quotes))
    cycle_minutes = sum(_minutes(quote.request_admitted_at, quote.delivery_accepted_at) for quote in quotes)
    active_intervals = [item for quote in quotes for item in quote.policy_owner_active_intervals]
    active_minutes = sum(_minutes(item.started_at, item.ended_at) for item in active_intervals)
    adjudications = [item for quote in quotes for item in quote.impact_adjudications]
    expected = [item for item in adjudications if item.expected_affected]
    omissions = sum(not item.identified_affected for item in expected)
    unauthorized = [item for quote in quotes for item in quote.access_attempts if not item.authorized]
    cost_items = [quote.selective_rebase_cost for quote in quotes]
    eligible_cost = (
        all(item is not None for item in cost_items)
        and all(
            item is not None and item.comparator_basis == "OBSERVED_MATCHED_COMPARATOR" for item in cost_items
        )
        and len({item.unit for item in cost_items if item is not None}) == 1
    )
    if eligible_cost:
        typed_costs = [item for item in cost_items if item is not None]
        full_cost = sum(item.full_rebuild_comparator_cost for item in typed_costs)
        saving = sum(
            item.full_rebuild_comparator_cost - item.selective_rebase_actual_cost for item in typed_costs
        )
        cost_sources = {item.source_ref for item in typed_costs}
    else:
        full_cost = None
        saving = None
        cost_sources = set()

    metrics = (
        _metric(
            "quote_cycle_elapsed_minutes",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=cycle_minutes,
            denominator=accepted_quotes,
            source_refs=workflow_refs,
            limitation="Mean elapsed cycle for admitted quotes in this bounded observation window.",
        ),
        _metric(
            "policy_confirmation_active_minutes",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=active_minutes if active_intervals else None,
            denominator=accepted_quotes if active_intervals else None,
            source_refs={item.source_ref for item in active_intervals},
            limitation="Mean owner active time; wall-clock waiting is excluded.",
        ),
        _metric(
            "adjudicated_impact_omission_rate",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=float(omissions) if expected else None,
            denominator=float(len(expected)) if expected else None,
            source_refs={item.source_ref for item in expected},
            limitation="Only owner-adjudicated expected affected targets form the denominator.",
        ),
        _metric(
            "first_pass_rework_rate",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=float(sum(quote.delivery_correction.required for quote in quotes)),
            denominator=accepted_quotes,
            source_refs={quote.delivery_correction.source_ref for quote in quotes},
            limitation="Correction is measured after first delivery acceptance inside the same window.",
        ),
        _metric(
            "unauthorized_access_success_rate",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=float(sum(item.succeeded for item in unauthorized)) if unauthorized else None,
            denominator=float(len(unauthorized)) if unauthorized else None,
            source_refs={item.source_ref for item in unauthorized},
            limitation="No unauthorized attempt means NOT_RUN, never an inferred zero success rate.",
        ),
        _metric(
            "manual_escalation_case_rate",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=float(sum(bool(quote.manual_escalations) for quote in quotes)),
            denominator=accepted_quotes,
            source_refs=workflow_refs,
            limitation="One quote contributes at most once even if it contains multiple escalations.",
        ),
        _metric(
            "realized_selective_rebase_cost_saving_rate",
            evidence_class=record.evidence_class,
            window=record.observation_window,
            numerator=saving,
            denominator=full_cost,
            source_refs=cost_sources,
            limitation=(
                "Calculated only from complete same-unit OBSERVED_MATCHED_COMPARATOR records; "
                "it is not enterprise ROI."
            ),
        ),
    )
    if tuple(item.metric_id for item in metrics) != METRIC_IDS:
        raise QuoteShadowAdmissionError("METRIC_SET_OR_ORDER_INVALID")
    return metrics


def admit_quote_observation(
    record: QuoteProcessObservationRecord,
    *,
    source_root: Path,
) -> QuoteShadowAdmissionReceipt:
    """Admit one strict observation record and calculate only supported metrics."""

    record = QuoteProcessObservationRecord.model_validate(record.model_dump(mode="json"))
    source_evidence = _verify_sources(record, source_root)
    metrics = _project_metrics(record)
    calculated = sum(item.status == "CALCULATED" for item in metrics)
    gates = (
        AdmissionGate(id="profile_role_semantics", status="PASS", detail="EXACT_V1_REGISTRY"),
        AdmissionGate(id="profile_system_semantics", status="PASS", detail="EXACT_V1_TAXONOMY"),
        AdmissionGate(
            id="observation_window",
            status="NOT_RUN" if record.evidence_class == ShadowEvidenceClass.NOT_RUN else "PASS",
            detail=_window_label(record.observation_window),
        ),
        AdmissionGate(
            id="content_addressed_sources",
            status="NOT_RUN" if not source_evidence else "PASS",
            detail=f"VERIFIED_FILES:{len(source_evidence)}",
        ),
        AdmissionGate(
            id="metric_projection",
            status="NOT_RUN" if calculated == 0 else "PASS",
            detail=f"CALCULATED:{calculated};NOT_RUN:{len(metrics) - calculated}",
        ),
    )
    limitations = list(record.limitations)
    if record.evidence_class == ShadowEvidenceClass.NOT_RUN:
        limitations.append("Real enterprise quote shadow observation remains NOT_RUN.")
    elif record.evidence_class == ShadowEvidenceClass.SYNTHETIC_CONTRACT_FIXTURE:
        limitations.append(
            "All values are deterministic synthetic contract-fixture values, not enterprise observations."
        )
    else:
        limitations.append("Observed shadow metrics are not causal improvement, ROI or production SLA proof.")
    return QuoteShadowAdmissionReceipt(
        schema_version=RECEIPT_SCHEMA_VERSION,
        id=f"quote-shadow-admission:{record.id}",
        assessed_at=record.prepared_at,
        observation_record_id=record.id,
        observation_record_digest=record.digest,
        admission_verdict="ADMIT",
        measurement_status="CALCULATED" if calculated else "NOT_RUN",
        evidence_ceiling=record.evidence_class,
        enterprise_ref=record.enterprise_ref,
        observation_window=record.observation_window,
        gates=gates,
        source_evidence=source_evidence,
        metrics=metrics,
        limitations=tuple(limitations),
    )


def verify_quote_shadow_receipt(
    record: QuoteProcessObservationRecord,
    receipt: QuoteShadowAdmissionReceipt,
    *,
    source_root: Path,
) -> tuple[str, ...]:
    """Replay source verification and require byte-equivalent receipt semantics."""

    failures: list[str] = []
    try:
        record = QuoteProcessObservationRecord.model_validate(record.model_dump(mode="json"))
        receipt = QuoteShadowAdmissionReceipt.model_validate(receipt.model_dump(mode="json"))
        expected = admit_quote_observation(record, source_root=source_root)
    except (OSError, ValueError) as exc:
        return (f"REPLAY_FAILED:{exc}",)
    if receipt != expected:
        failures.append("RECEIPT_RECOMPUTATION_MISMATCH")
    if receipt.digest != sha256_digest(receipt.digest_payload()):
        failures.append("RECEIPT_DIGEST_MISMATCH")
    return tuple(failures)


__all__ = [
    "METRIC_IDS",
    "QuoteProcessObservationRecord",
    "QuoteShadowAdmissionError",
    "QuoteShadowAdmissionReceipt",
    "ShadowEvidenceClass",
    "admit_quote_observation",
    "stable_observation_record_ref",
    "verify_quote_shadow_receipt",
]
