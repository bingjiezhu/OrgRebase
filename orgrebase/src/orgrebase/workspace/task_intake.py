"""Stateless, candidate-only intake for the one admitted Workspace task.

Natural language is useful for describing work, but it is neither an
authority source nor a business-fact source.  This module therefore binds an
employee prompt only to the exact default :class:`TaskRequest` already sealed
by the active Enterprise Seed Profile.  Candidate preparation never persists,
forms a Quote, or writes a canonical target.  After exact human confirmation,
the control plane atomically persists the public-safe digest-bound run receipt
and a separate actor-readable private source-text record with the existing
Quote Formation transaction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, ContentAddressedModel, IntegrityError
from orgrebase.workspace.models import TaskRequest, TaskTemplateVersion
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MIN_PROMPT_CHARACTERS = 8
MAX_WORK_DESCRIPTION_CHARACTERS = 500
MAX_WORK_DESCRIPTION_UTF8_BYTES = 2048

_AUTHORITY_OVERRIDE_MARKERS = (
    "绕过审批",
    "跳过审批",
    "无需审批",
    "不用审批",
    "自动批准",
    "忽略权限",
    "绕过权限",
    "直接生产",
    "直接上线",
    "直接发布",
    "直接写入",
)
_AUTHORITY_OVERRIDE_PATTERNS = (
    re.compile(
        r"\b(?:bypass|skip|ignore)\s+(?:all\s+)?(?:human\s+)?"
        r"(?:approval|authorization|permission)s?\b"
    ),
    re.compile(r"\bauto(?:matically)?[- ]?approve\b"),
    re.compile(
        r"\b(?:deploy|publish|write)\s+(?:directly\s+)?(?:to\s+)?production\b"
    ),
)

# This MVP admits exactly one task kind: an enterprise quote.  These markers
# are deliberately lexical and conservative: they classify task kind only.
# They never extract customer names, amounts, dates, policies, or authority
# from natural language.
_ENTERPRISE_QUOTE_INTENT_PATTERNS = (
    re.compile(
        r"(?:准备|制作|创建|生成|编制|发起|处理|更新|调整|修改|审核|复核|完成|提供|出具|提交|重做|重算|"
        r"做(?:一份|个)?)"
        r".{0,80}(?:企业|商务|销售|客户|采购|产品)?(?:版)?报价(?:单|方案)?"
    ),
    re.compile(
        r"(?:企业|商务|销售|客户|采购|产品)(?:版)?报价(?:单|方案)?"
        r".{0,80}(?:准备|制作|创建|生成|编制|处理|更新|调整|修改|审核|复核|需求|请求|任务)"
    ),
    re.compile(r"(?:请|需要|希望)(?:为|给).{1,80}报价(?:单|方案)?"),
    re.compile(
        r"\b(?:enterprise|commercial|sales|customer|client|pricing|price)\s+"
        r"(?:quote|quotation|pricing\s+proposal)\b"
    ),
    re.compile(
        r"\b(?:prepare|create|generate|draft|produce|revise|update|review|"
        r"approve|issue|submit|request)\s+(?:an?\s+|the\s+)?"
        r"(?:enterprise\s+|commercial\s+|sales\s+|customer\s+|client\s+|"
        r"price\s+|pricing\s+)?(?:quote|quotation|pricing\s+proposal)\b"
    ),
)
_ENTERPRISE_QUOTE_NEGATION_PATTERNS = (
    re.compile(
        r"(?:不要|无需|不需要|禁止|拒绝|取消|并非|不是|无关|忽略)"
        r".{0,16}报价"
    ),
    re.compile(r"报价.{0,12}(?:取消|无关|不需要|不是任务|不是请求)"),
    re.compile(
        r"\b(?:do\s+not|don't|never|no\s+need\s+to|without|ignore|cancel)"
        r"\b.{0,48}\b(?:quote|quotation|pricing\s+proposal)\b"
    ),
    re.compile(
        r"\b(?:not\s+(?:an?\s+|the\s+)?)"
        r"(?:enterprise\s+|commercial\s+|sales\s+|customer\s+|client\s+|"
        r"price\s+|pricing\s+)?(?:quote|quotation|pricing\s+proposal)\b"
    ),
)


class TaskIntakeCandidateReceipt(ContentAddressedModel):
    """Content-addressed proposal; it carries no formation authority."""

    schema_version: Literal["orgrebase.workspace-task-intake-candidate.v1"] = (
        "orgrebase.workspace-task-intake-candidate.v1"
    )
    status: Literal["READY_FOR_CONFIRMATION", "HOLD"]
    prompt_digest: str = Field(pattern=_DIGEST_PATTERN)
    prompt_length: int = Field(ge=0)
    intended_run_id: str = Field(min_length=1)
    workspace_instance_nonce: str = Field(pattern=_DIGEST_PATTERN)
    actor_id: str
    customer_id: str | None
    deliverable_kind: str
    task_request: TaskRequest
    task_digest: str = Field(pattern=_DIGEST_PATTERN)
    profile_ref: str = Field(min_length=1)
    profile_digest: str = Field(pattern=_DIGEST_PATTERN)
    template_ref: str = Field(min_length=1)
    template_digest: str = Field(pattern=_DIGEST_PATTERN)
    oac_activation_binding_digest: str | None = Field(
        default=None,
        pattern=_DIGEST_PATTERN,
    )
    unknowns: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    candidate_only: Literal[True] = True
    natural_language_authority: Literal[False] = False
    canonical_target_writes: Literal[0] = 0
    claim_boundary: Literal[
        "EXACT_PROFILE_DEFAULT_TASK_CANDIDATE_NOT_QUOTE_OR_BUSINESS_APPROVAL"
    ] = "EXACT_PROFILE_DEFAULT_TASK_CANDIDATE_NOT_QUOTE_OR_BUSINESS_APPROVAL"

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.task_digest != self.task_request.digest:
            raise ValueError("TASK_INTAKE_TASK_DIGEST_MISMATCH")
        if len(self.unknowns) != len(set(self.unknowns)) or len(
            self.reason_codes
        ) != len(set(self.reason_codes)):
            raise ValueError("TASK_INTAKE_REASON_SET_INVALID")
        if self.status == "READY_FOR_CONFIRMATION":
            if self.unknowns or self.reason_codes:
                raise ValueError("TASK_INTAKE_READY_SHAPE_INVALID")
        elif not self.reason_codes:
            raise ValueError("TASK_INTAKE_HOLD_REASON_REQUIRED")
        return self


class TaskIntakeApprovalReceipt(ContentAddressedModel):
    """Human confirmation of one exact candidate, still without state writes."""

    schema_version: Literal["orgrebase.workspace-task-intake-approval.v1"] = (
        "orgrebase.workspace-task-intake-approval.v1"
    )
    status: Literal["ADMITTED_FOR_FORMATION"] = "ADMITTED_FOR_FORMATION"
    actor_id: str = Field(min_length=1)
    intended_run_id: str = Field(min_length=1)
    workspace_instance_nonce: str = Field(pattern=_DIGEST_PATTERN)
    candidate_digest: str = Field(pattern=_DIGEST_PATTERN)
    task_digest: str = Field(pattern=_DIGEST_PATTERN)
    profile_digest: str = Field(pattern=_DIGEST_PATTERN)
    template_digest: str = Field(pattern=_DIGEST_PATTERN)
    oac_activation_binding_digest: str | None = Field(
        default=None,
        pattern=_DIGEST_PATTERN,
    )
    admission_effect: Literal["MAY_REQUEST_EXISTING_FORMATION_ENDPOINT"] = (
        "MAY_REQUEST_EXISTING_FORMATION_ENDPOINT"
    )
    quote_formed: Literal[False] = False
    persisted: Literal[False] = False
    canonical_target_writes: Literal[0] = 0
    claim_boundary: Literal[
        "TASK_CANDIDATE_CONFIRMED_NOT_QUOTE_FORMED_OR_BUSINESS_APPROVED"
    ] = "TASK_CANDIDATE_CONFIRMED_NOT_QUOTE_FORMED_OR_BUSINESS_APPROVED"


class TaskIntakeRunSummary(ContentAddressedModel):
    """Persisted binding between intake proof and control-plane Formation.

    The raw prompt is intentionally absent.  This receipt is committed in the
    same transaction as Quote formation and its read-only dependency Tool, so a
    refresh can recover the human-started causal chain without turning natural
    language into business truth or write authority.
    """

    schema_version: Literal["orgrebase.workspace-task-intake-run-receipt.v1"] = (
        "orgrebase.workspace-task-intake-run-receipt.v1"
    )
    status: Literal["FORMATION_COMPLETED"] = "FORMATION_COMPLETED"
    run_id: str = Field(min_length=1)
    workspace_instance_nonce: str = Field(pattern=_DIGEST_PATTERN)
    actor_id: str = Field(min_length=1)
    prompt_digest: str = Field(pattern=_DIGEST_PATTERN)
    prompt_length: int = Field(ge=0)
    candidate_digest: str = Field(pattern=_DIGEST_PATTERN)
    approval_digest: str = Field(pattern=_DIGEST_PATTERN)
    task_digest: str = Field(pattern=_DIGEST_PATTERN)
    formation_receipt_digest: str = Field(pattern=_DIGEST_PATTERN)
    quote_ref: str = Field(min_length=1)
    oac_activation_binding_digest: str | None = Field(
        default=None,
        pattern=_DIGEST_PATTERN,
    )
    candidate_receipt: TaskIntakeCandidateReceipt
    confirmation_receipt: TaskIntakeApprovalReceipt
    intake_persisted: Literal[True] = True
    intake_canonical_target_writes: Literal[0] = 0
    formation_authority: Literal["ORGREBASE_CONTROL_PLANE"] = (
        "ORGREBASE_CONTROL_PLANE"
    )
    claim_boundary: Literal[
        "INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE"
    ] = "INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE"

    @model_validator(mode="after")
    def validate_receipt_bindings(self) -> Self:
        candidate = self.candidate_receipt
        confirmation = self.confirmation_receipt
        if (
            self.run_id != candidate.intended_run_id
            or self.run_id != confirmation.intended_run_id
            or self.workspace_instance_nonce != candidate.workspace_instance_nonce
            or self.workspace_instance_nonce != confirmation.workspace_instance_nonce
            or self.actor_id != candidate.actor_id
            or self.actor_id != confirmation.actor_id
            or self.prompt_digest != candidate.prompt_digest
            or self.prompt_length != candidate.prompt_length
            or self.candidate_digest != candidate.digest
            or self.approval_digest != confirmation.digest
            or self.task_digest != candidate.task_digest
            or confirmation.candidate_digest != candidate.digest
            or confirmation.task_digest != candidate.task_digest
            or self.oac_activation_binding_digest
            != candidate.oac_activation_binding_digest
            or confirmation.oac_activation_binding_digest
            != candidate.oac_activation_binding_digest
        ):
            raise ValueError("TASK_INTAKE_RUN_RECEIPT_BINDING_MISMATCH")
        return self


class TaskIntakeWorkDescriptionRecord(ContentAddressedModel):
    """Private, actor-readable source text bound to one completed Formation.

    This record deliberately lives outside the public run receipt.  It is an
    operator convenience and audit input, not a fact source or authority
    source.  The Workspace service stores it in the same SQLite transaction as
    Formation and exposes it only through an identity-checked endpoint.
    """

    schema_version: Literal[
        "orgrebase.workspace-task-intake-work-description-record.v1"
    ] = "orgrebase.workspace-task-intake-work-description-record.v1"
    status: Literal["PRIVATE_RECORD_RETAINED"] = "PRIVATE_RECORD_RETAINED"
    run_id: str = Field(min_length=1)
    workspace_instance_nonce: str = Field(pattern=_DIGEST_PATTERN)
    actor_id: str = Field(min_length=1)
    work_description: str = Field(
        min_length=_MIN_PROMPT_CHARACTERS,
        max_length=MAX_WORK_DESCRIPTION_CHARACTERS,
    )
    prompt_digest: str = Field(pattern=_DIGEST_PATTERN)
    prompt_length: int = Field(ge=_MIN_PROMPT_CHARACTERS, le=MAX_WORK_DESCRIPTION_CHARACTERS)
    prompt_utf8_bytes: int = Field(ge=1, le=MAX_WORK_DESCRIPTION_UTF8_BYTES)
    candidate_digest: str = Field(pattern=_DIGEST_PATTERN)
    approval_digest: str = Field(pattern=_DIGEST_PATTERN)
    formation_receipt_digest: str = Field(pattern=_DIGEST_PATTERN)
    quote_ref: str = Field(min_length=1)
    semantic_use: Literal["TASK_INTENT_ONLY"] = "TASK_INTENT_ONLY"
    contributes_business_facts: Literal[False] = False
    grants_authority: Literal[False] = False
    canonical_target_writes: Literal[0] = 0
    visibility: Literal["AUTHORIZED_TASK_ACTOR_ONLY"] = (
        "AUTHORIZED_TASK_ACTOR_ONLY"
    )
    included_in_state: Literal[False] = False
    included_in_public_evidence: Literal[False] = False
    included_in_events_or_otlp: Literal[False] = False
    claim_boundary: Literal[
        "PRIVATE_WORK_DESCRIPTION_NOT_BUSINESS_FACT_OR_WRITE_AUTHORITY"
    ] = "PRIVATE_WORK_DESCRIPTION_NOT_BUSINESS_FACT_OR_WRITE_AUTHORITY"

    @model_validator(mode="after")
    def validate_private_description(self) -> Self:
        utf8_length = len(self.work_description.encode("utf-8"))
        if (
            self.prompt_digest != sha256_digest(self.work_description)
            or self.prompt_length != len(self.work_description)
            or self.prompt_utf8_bytes != utf8_length
            or utf8_length > MAX_WORK_DESCRIPTION_UTF8_BYTES
        ):
            raise ValueError("TASK_INTAKE_WORK_DESCRIPTION_BINDING_MISMATCH")
        return self


def validate_task_intake_work_description(value: str) -> str:
    """Validate bounded private text without normalizing or rewriting it."""

    if not isinstance(value, str):
        raise IntegrityError("TASK_INTAKE_WORK_DESCRIPTION_REQUIRED")
    if len(value) > MAX_WORK_DESCRIPTION_CHARACTERS:
        raise IntegrityError("TASK_INTAKE_WORK_DESCRIPTION_CHARACTER_LIMIT_EXCEEDED")
    if len(value.encode("utf-8")) > MAX_WORK_DESCRIPTION_UTF8_BYTES:
        raise IntegrityError("TASK_INTAKE_WORK_DESCRIPTION_UTF8_LIMIT_EXCEEDED")
    return value


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _requests_authority_override(prompt: str) -> bool:
    normalized = re.sub(r"\s+", " ", prompt.casefold()).strip()
    return any(marker in normalized for marker in _AUTHORITY_OVERRIDE_MARKERS) or any(
        pattern.search(normalized) is not None
        for pattern in _AUTHORITY_OVERRIDE_PATTERNS
    )


def _matches_enterprise_quote_intent(prompt: str) -> bool:
    """Classify only whether the prompt requests the sole admitted task kind."""

    normalized = re.sub(r"\s+", " ", prompt.casefold()).strip()
    if any(pattern.search(normalized) for pattern in _ENTERPRISE_QUOTE_NEGATION_PATTERNS):
        return False
    return any(
        pattern.search(normalized) is not None
        for pattern in _ENTERPRISE_QUOTE_INTENT_PATTERNS
    )


def _exact_profile_contract(
    *,
    profile: EnterpriseSeedProfile,
    template: TaskTemplateVersion,
) -> tuple[EnterpriseSeedProfile, TaskTemplateVersion, TaskRequest]:
    try:
        selected_profile = profile.revalidated()
        selected_template = template.revalidated()
        request = selected_profile.task_request().revalidated()
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrityError("TASK_INTAKE_PROFILE_CONTRACT_INVALID") from exc
    if (
        request.template_ref != selected_template.ref
        or request.deliverable_kind != selected_template.deliverable_kind
    ):
        raise IntegrityError("TASK_INTAKE_TEMPLATE_BINDING_MISMATCH")
    return selected_profile, selected_template, request


def prepare_task_intake_candidate(
    *,
    prompt: str,
    actor_id: str,
    customer_id: str | None,
    deliverable_kind: str,
    profile: EnterpriseSeedProfile,
    template: TaskTemplateVersion,
    intended_run_id: str,
    workspace_instance_nonce: str,
    oac_activation_binding_digest: str | None = None,
    oac_required: bool = False,
    oac_reason_code: str | None = None,
) -> TaskIntakeCandidateReceipt:
    """Bind a matching quote intent without interpreting new business facts."""

    prompt = validate_task_intake_work_description(prompt)

    selected_profile, selected_template, request = _exact_profile_contract(
        profile=profile,
        template=template,
    )
    if oac_activation_binding_digest is not None and not re.fullmatch(
        _DIGEST_PATTERN,
        oac_activation_binding_digest,
    ):
        raise IntegrityError("TASK_INTAKE_OAC_BINDING_DIGEST_INVALID")
    if not intended_run_id.strip():
        raise IntegrityError("TASK_INTAKE_RUN_ID_REQUIRED")
    if not re.fullmatch(_DIGEST_PATTERN, workspace_instance_nonce):
        raise IntegrityError("TASK_INTAKE_WORKSPACE_INSTANCE_NONCE_INVALID")

    reasons: list[str] = []
    unknowns: list[str] = []
    if len(prompt.strip()) < _MIN_PROMPT_CHARACTERS:
        _append_once(reasons, "PROMPT_REQUIRED_OR_TOO_SHORT")
        _append_once(unknowns, "task_intent")
    elif not _matches_enterprise_quote_intent(prompt):
        _append_once(reasons, "PROMPT_TASK_INTENT_MISMATCH")
        _append_once(unknowns, "task_intent")
    if actor_id != request.actor_id:
        _append_once(reasons, "TASK_ACTOR_OUT_OF_PROFILE_SCOPE")
        _append_once(unknowns, "admitted_task_actor")
    if customer_id != request.customer_id:
        _append_once(reasons, "CUSTOMER_OUT_OF_PROFILE_SCOPE")
        _append_once(unknowns, "admitted_customer")
    if deliverable_kind != request.deliverable_kind:
        _append_once(reasons, "DELIVERABLE_KIND_OUT_OF_PROFILE_SCOPE")
        _append_once(unknowns, "admitted_deliverable_kind")
    if _requests_authority_override(prompt):
        _append_once(reasons, "PROMPT_AUTHORITY_OVERRIDE_REQUESTED")
        _append_once(unknowns, "requested_authority")
    if oac_required and oac_activation_binding_digest is None:
        _append_once(
            reasons,
            oac_reason_code or "OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED",
        )
        _append_once(unknowns, "oac_activation_binding")

    return TaskIntakeCandidateReceipt(
        status="HOLD" if reasons else "READY_FOR_CONFIRMATION",
        prompt_digest=sha256_digest(prompt),
        prompt_length=len(prompt),
        intended_run_id=intended_run_id,
        workspace_instance_nonce=workspace_instance_nonce,
        actor_id=actor_id,
        customer_id=customer_id,
        deliverable_kind=deliverable_kind,
        task_request=request,
        task_digest=request.digest,
        profile_ref=selected_profile.ref,
        profile_digest=selected_profile.digest,
        template_ref=selected_template.ref,
        template_digest=selected_template.digest,
        oac_activation_binding_digest=oac_activation_binding_digest,
        unknowns=tuple(unknowns),
        reason_codes=tuple(reasons),
    )


def verify_task_intake_candidate(
    value: Mapping[str, Any] | TaskIntakeCandidateReceipt,
    *,
    profile: EnterpriseSeedProfile,
    template: TaskTemplateVersion,
    expected_run_id: str,
    expected_workspace_instance_nonce: str,
    expected_oac_activation_binding_digest: str | None,
    oac_required: bool,
) -> TaskIntakeCandidateReceipt:
    """Independently reparse and bind a candidate to the current exact profile."""

    try:
        candidate = TaskIntakeCandidateReceipt.model_validate(
            value.model_dump(mode="json")
            if isinstance(value, TaskIntakeCandidateReceipt)
            else dict(value)
        )
    except (TypeError, ValueError) as exc:
        raise IntegrityError("TASK_INTAKE_CANDIDATE_INVALID") from exc
    selected_profile, selected_template, request = _exact_profile_contract(
        profile=profile,
        template=template,
    )
    if (
        candidate.profile_ref != selected_profile.ref
        or candidate.profile_digest != selected_profile.digest
        or candidate.template_ref != selected_template.ref
        or candidate.template_digest != selected_template.digest
        or candidate.task_digest != request.digest
        or candidate.task_request.model_dump(mode="json")
        != request.model_dump(mode="json")
    ):
        raise IntegrityError("TASK_INTAKE_CANDIDATE_PROFILE_BINDING_MISMATCH")
    if candidate.intended_run_id != expected_run_id:
        raise IntegrityError("TASK_INTAKE_CANDIDATE_RUN_BINDING_MISMATCH")
    if candidate.workspace_instance_nonce != expected_workspace_instance_nonce:
        raise IntegrityError("TASK_INTAKE_CANDIDATE_WORKSPACE_BINDING_MISMATCH")
    if (
        candidate.oac_activation_binding_digest
        != expected_oac_activation_binding_digest
    ):
        raise IntegrityError("TASK_INTAKE_CANDIDATE_OAC_BINDING_MISMATCH")
    if oac_required and expected_oac_activation_binding_digest is None:
        raise IntegrityError("TASK_INTAKE_OAC_ACTIVATION_BINDING_REQUIRED")
    if candidate.status == "READY_FOR_CONFIRMATION" and (
        candidate.actor_id != request.actor_id
        or candidate.customer_id != request.customer_id
        or candidate.deliverable_kind != request.deliverable_kind
    ):
        raise IntegrityError("TASK_INTAKE_READY_SCOPE_MISMATCH")
    return candidate


def admit_task_intake_candidate(
    value: Mapping[str, Any] | TaskIntakeCandidateReceipt,
    *,
    candidate_digest: str,
    actor_id: str,
    profile: EnterpriseSeedProfile,
    template: TaskTemplateVersion,
    expected_run_id: str,
    expected_workspace_instance_nonce: str,
    expected_oac_activation_binding_digest: str | None,
    oac_required: bool,
) -> TaskIntakeApprovalReceipt:
    """Confirm an exact READY candidate without persisting or forming a Quote."""

    candidate = verify_task_intake_candidate(
        value,
        profile=profile,
        template=template,
        expected_run_id=expected_run_id,
        expected_workspace_instance_nonce=expected_workspace_instance_nonce,
        expected_oac_activation_binding_digest=expected_oac_activation_binding_digest,
        oac_required=oac_required,
    )
    if candidate_digest != candidate.digest:
        raise IntegrityError("TASK_INTAKE_CANDIDATE_DIGEST_MISMATCH")
    if actor_id != candidate.actor_id or actor_id != candidate.task_request.actor_id:
        raise AuthorizationError(
            f"TASK_INTAKE_ACTOR_DENIED:expected={candidate.task_request.actor_id},actual={actor_id}"
        )
    if candidate.status != "READY_FOR_CONFIRMATION":
        raise IntegrityError("TASK_INTAKE_CANDIDATE_NOT_READY")
    return TaskIntakeApprovalReceipt(
        actor_id=actor_id,
        intended_run_id=candidate.intended_run_id,
        workspace_instance_nonce=candidate.workspace_instance_nonce,
        candidate_digest=candidate.digest,
        task_digest=candidate.task_digest,
        profile_digest=candidate.profile_digest,
        template_digest=candidate.template_digest,
        oac_activation_binding_digest=candidate.oac_activation_binding_digest,
    )


def verify_task_intake_approval(
    value: Mapping[str, Any] | TaskIntakeApprovalReceipt,
    *,
    candidate: Mapping[str, Any] | TaskIntakeCandidateReceipt,
    actor_id: str,
    profile: EnterpriseSeedProfile,
    template: TaskTemplateVersion,
    expected_run_id: str,
    expected_workspace_instance_nonce: str,
    expected_oac_activation_binding_digest: str | None,
    oac_required: bool,
) -> TaskIntakeApprovalReceipt:
    """Independently verify an approval and its exact candidate binding."""

    selected_candidate = verify_task_intake_candidate(
        candidate,
        profile=profile,
        template=template,
        expected_run_id=expected_run_id,
        expected_workspace_instance_nonce=expected_workspace_instance_nonce,
        expected_oac_activation_binding_digest=expected_oac_activation_binding_digest,
        oac_required=oac_required,
    )
    try:
        approval = TaskIntakeApprovalReceipt.model_validate(
            value.model_dump(mode="json")
            if isinstance(value, TaskIntakeApprovalReceipt)
            else dict(value)
        )
    except (TypeError, ValueError) as exc:
        raise IntegrityError("TASK_INTAKE_APPROVAL_INVALID") from exc
    if (
        selected_candidate.status != "READY_FOR_CONFIRMATION"
        or approval.actor_id != actor_id
        or actor_id != selected_candidate.actor_id
        or approval.intended_run_id != expected_run_id
        or approval.workspace_instance_nonce != expected_workspace_instance_nonce
        or approval.candidate_digest != selected_candidate.digest
        or approval.task_digest != selected_candidate.task_digest
        or approval.profile_digest != selected_candidate.profile_digest
        or approval.template_digest != selected_candidate.template_digest
        or approval.oac_activation_binding_digest
        != selected_candidate.oac_activation_binding_digest
    ):
        raise IntegrityError("TASK_INTAKE_APPROVAL_BINDING_MISMATCH")
    return approval


__all__ = (
    "MAX_WORK_DESCRIPTION_CHARACTERS",
    "MAX_WORK_DESCRIPTION_UTF8_BYTES",
    "TaskIntakeApprovalReceipt",
    "TaskIntakeCandidateReceipt",
    "TaskIntakeRunSummary",
    "TaskIntakeWorkDescriptionRecord",
    "admit_task_intake_candidate",
    "prepare_task_intake_candidate",
    "validate_task_intake_work_description",
    "verify_task_intake_approval",
    "verify_task_intake_candidate",
)
