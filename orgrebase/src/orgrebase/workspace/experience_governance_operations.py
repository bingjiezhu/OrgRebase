"""Authenticated entrypoints for assessment, reviewed lessons and recall.

Actor assignment is deployment owned. Request bodies may choose a business
case or propose text, but cannot nominate the actor who signs its qualification.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_assessment import (
    ASSESSMENT_MEDIA,
    ExperienceAssessmentService,
    require_quote_case_current,
)
from orgrebase.workspace.experience_collection import OBSERVATION_MEDIA
from orgrebase.workspace.experience_contracts import (
    CaseAssessmentReceipt,
    CaseObservationV2,
    RecallReceipt,
    RecallSelectionManifest,
)
from orgrebase.workspace.experience_lessons import (
    CANDIDATE_MEDIA,
    ExperienceLessonService,
    LessonBody,
    LessonCandidate,
    LessonHeadExpectation,
    lesson_object_prefix,
)
from orgrebase.workspace.experience_recall import RECALL_MEDIA, SELECTION_MEDIA, ExperienceRecallService
from orgrebase.workspace.experience_source_hold import is_case_source_held
from orgrebase.workspace.skill_evolution_v2 import PROFILE_ID as FINANCE_PROFILE

_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_RUBRIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class ExperiencePhaseActors:
    collector: str
    author: str
    evaluator: str
    reviewer: str
    corpus: str
    rubric_version: str

    @classmethod
    def from_deployment(cls) -> ExperiencePhaseActors:
        values = {
            name: os.environ.get(f"ORGREBASE_EXPERIENCE_{name.upper()}_ACTOR_ID", "").strip()
            for name in ("collector", "author", "evaluator", "reviewer", "corpus")
        }
        rubric = os.environ.get("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "").strip()
        if (
            any(_ACTOR.fullmatch(value) is None for value in values.values())
            or len(set(values.values())) != len(values)
            or _RUBRIC.fullmatch(rubric) is None
        ):
            raise IntegrityError("EXPERIENCE_PHASE_AUTHORITY_UNCONFIGURED")
        return cls(**values, rubric_version=rubric)


def _assessment(workspace: Any, actors: ExperiencePhaseActors) -> ExperienceAssessmentService:
    return ExperienceAssessmentService(
        workspace, evaluator_actor_id=actors.evaluator,
        excluded_actor_ids=(actors.collector, actors.author, actors.reviewer, actors.corpus),
        rubric_version=actors.rubric_version,
        source_qualification_check=lambda case: require_quote_case_current(workspace, case),
    )


def _lessons(workspace: Any, actors: ExperiencePhaseActors) -> ExperienceLessonService:
    return ExperienceLessonService(
        workspace, author_actor_id=actors.author, reviewer_actor_id=actors.reviewer,
        assessment_service=_assessment(workspace, actors),
    )


def _recall(workspace: Any, actors: ExperiencePhaseActors) -> ExperienceRecallService:
    return ExperienceRecallService(
        workspace, profile_id=FINANCE_PROFILE,
        assessment_service=_assessment(workspace, actors),
    )


def case_assessment_projection(workspace: Any, case_ref: str) -> dict[str, Any]:
    """Read the independent assessment without exposing signed evidence or raw text."""

    require_action(workspace, "read")
    try:
        actors = ExperiencePhaseActors.from_deployment()
    except IntegrityError as exc:
        if str(exc) != "EXPERIENCE_PHASE_AUTHORITY_UNCONFIGURED":
            raise
        return {
            "status": "UNAVAILABLE", "coverage": "UNKNOWN",
            "assessment_count_observed": None,
        }
    projection = _assessment(workspace, actors).assessment_projection_for_reader(
        case_ref, FINANCE_PROFILE,
    )
    return {
        "status": projection["status"],
        "coverage": projection["coverage"],
        "assessment_count_observed": projection["assessment_count_observed"],
    }


class AssessExperienceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    case_ref: str = Field(min_length=1, max_length=256)
    verdict: Literal["SUPPORT", "COUNTEREXAMPLE", "UNKNOWN", "DISPUTED"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    evidence_refs: list[str] = Field(default_factory=list)
    independence_cluster_id: str | None = None


class ProposeExperienceLessonInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    lesson_id: str = Field(min_length=1, max_length=128)
    body: LessonBody
    support_assessment_refs: list[str] = Field(min_length=1)
    counter_assessment_refs: list[str] = Field(default_factory=list)


class DecideExperienceLessonInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"]
    candidate_ref: str | None = None
    expected_heads: list[LessonHeadExpectation] = Field(default_factory=list)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    body_bytes_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    declassified_exact_content: bool = False


class PrepareExperienceDeltaInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"]
    candidate_ref: str | None = None
    expected_heads: list[LessonHeadExpectation] = Field(default_factory=list)


class DecidePreparedExperienceDeltaInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    preparation_ref: str = Field(min_length=1, max_length=256)
    expected_diff_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    verdict: Literal["APPROVED", "REJECTED"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    body_bytes_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    declassified_exact_content: bool = False
    purpose: str = "workspace-change-explanation-v1"
    recipients: list[str] = Field(default_factory=lambda: ["workspace-advisory"])


def assess_experience(workspace: Any, command: AssessExperienceInput) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    assessment_ref = _assessment(workspace, actors).record_assessment(
        operation_id=command.operation_id, case_ref=command.case_ref,
        profile_id=FINANCE_PROFILE, verdict=command.verdict,
        reason_code=command.reason_code, evidence_refs=tuple(command.evidence_refs),
        independence_cluster_id=command.independence_cluster_id,
    )
    return {"assessment_ref": assessment_ref, "status": "OBSERVED_FOR_GOVERNED_REVIEW"}


def propose_experience_lesson(
    workspace: Any, command: ProposeExperienceLessonInput,
) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    candidate_ref = _lessons(workspace, actors).propose(
        operation_id=command.operation_id, profile_id=FINANCE_PROFILE,
        lesson_id=command.lesson_id, body=command.body,
        support_assessment_refs=tuple(command.support_assessment_refs),
        counter_assessment_refs=tuple(command.counter_assessment_refs),
    )
    return {"candidate_ref": candidate_ref, "status": "PRIVATE_CANDIDATE_NOT_PUBLISHED"}


def review_experience_lesson(workspace: Any, candidate_ref: str) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    return _lessons(workspace, actors).review_candidate(candidate_ref)


def candidate_experience_lessons(
    workspace: Any, *, after: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """Reviewer-only candidate metadata; private body stays in its own store."""

    actors = ExperiencePhaseActors.from_deployment()
    principal = request_principal.get()
    if principal is None:
        raise AuthenticationError("EXPERIENCE_REVIEWER_PRINCIPAL_REQUIRED")
    if principal.actor_id != actors.reviewer or principal.tenant_id != workspace.store.tenant_id:
        raise AuthorizationError("EXPERIENCE_REVIEWER_SCOPE_DENIED")
    require_action(workspace, "govern")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("EXPERIENCE_CANDIDATE_PAGE_LIMIT_INVALID")
    page = workspace.store.artifact_page(
        artifact_id_prefix="experience-lesson-candidate:", expected_media_type=CANDIDATE_MEDIA,
        after=after, limit=limit,
    )
    lessons = _lessons(workspace, actors)
    records = []
    for stored in page["items"]:
        candidate = LessonCandidate.model_validate(stored.payload).revalidated()
        if (
            stored.artifact_id != lessons._candidate_ref(candidate)
            or candidate.tenant_id != workspace.store.tenant_id
            or candidate.workspace_id != workspace.store.workspace_id
            or candidate.author_id != actors.author
        ):
            raise IntegrityError("EXPERIENCE_CANDIDATE_SCOPE_INVALID")
        if candidate.profile_id != FINANCE_PROFILE:
            continue
        evidence_count = len(candidate.support_assessment_refs) + len(candidate.counter_assessment_refs)
        if evidence_count > 32:
            evidence_status = "HOLD"
            evidence_reason = "EVIDENCE_SCAN_BUDGET_EXCEEDED"
        else:
            try:
                lessons._evidence(candidate)
                evidence_status = "CURRENT_QUALIFIED"
                evidence_reason = None
            except (IntegrityError, KeyError, ValueError, PermissionError):
                evidence_status = "HOLD"
                evidence_reason = "EVIDENCE_NOT_CURRENT"
        try:
            head = workspace.store.get_object(
                lesson_object_prefix(FINANCE_PROFILE) + candidate.lesson_id,
            )
        except KeyError:
            head = None
        private_body_status = workspace.private_records.record_status(candidate.private_body_ref)
        records.append({
            "candidate_ref": stored.artifact_id,
            "profile_id": candidate.profile_id,
            "lesson_id": candidate.lesson_id,
            "evidence_status": evidence_status,
            "evidence_reason_code": evidence_reason,
            "private_body_status": private_body_status,
            "review_status": (
                "READY_FOR_REVIEW" if evidence_status == "CURRENT_QUALIFIED"
                and private_body_status == "AVAILABLE" else "HOLD"
            ),
            "publication_status": (
                "CURRENT" if head is not None and head.payload.get("candidate_ref") == stored.artifact_id
                and head.payload.get("status") == "ADMITTED" else "NOT_CURRENT"
            ),
            "support_assessment_count": len(candidate.support_assessment_refs),
            "counter_assessment_count": len(candidate.counter_assessment_refs),
            "content_bytes_disclosed": 0,
        })
    return {
        "schema_version": "orgrebase.experience-candidate-page.v1",
        "items": records,
        "next_cursor": page["next_cursor"],
    }


def decide_experience_lesson(
    workspace: Any, command: DecideExperienceLessonInput,
) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    delta_ref = _lessons(workspace, actors).apply_delta(
        operation_id=command.operation_id, action=command.action,
        candidate_ref=command.candidate_ref, expected_heads=tuple(command.expected_heads),
        reason_code=command.reason_code, body_bytes_digest=command.body_bytes_digest,
        declassified_exact_content=command.declassified_exact_content,
        legacy_replay_only=True,
    )
    return {"delta_ref": delta_ref, "status": "LEGACY_EXACT_REPLAY_ONLY"}


def prepare_experience_delta(
    workspace: Any, command: PrepareExperienceDeltaInput,
) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    preparation_ref = _lessons(workspace, actors).prepare_delta(
        operation_id=command.operation_id, action=command.action,
        candidate_ref=command.candidate_ref,
        expected_heads=tuple(command.expected_heads),
    )
    return {"preparation_ref": preparation_ref, "status": "PREPARED_NOT_PUBLISHED"}


def review_prepared_experience_delta(workspace: Any, preparation_ref: str) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    return _lessons(workspace, actors).review_prepared_delta(preparation_ref)


def decide_prepared_experience_delta(
    workspace: Any, command: DecidePreparedExperienceDeltaInput,
) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    delta_ref = _lessons(workspace, actors).decide_prepared_delta(
        command.preparation_ref,
        expected_diff_bytes_digest=command.expected_diff_bytes_digest,
        verdict=command.verdict, reason_code=command.reason_code,
        body_bytes_digest=command.body_bytes_digest,
        declassified_exact_content=command.declassified_exact_content,
        purpose=command.purpose, recipients=tuple(command.recipients),
    )
    return {"delta_ref": delta_ref, "status": "GOVERNED_LESSON_DECISION"}


def read_experience_delta_diff(workspace: Any, delta_ref: str) -> dict[str, Any]:
    actors = ExperiencePhaseActors.from_deployment()
    return _lessons(workspace, actors).read_delta_diff(delta_ref)


def current_experience_lessons(
    workspace: Any, *, after: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    require_action(workspace, "read")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("EXPERIENCE_LESSON_PAGE_LIMIT_INVALID")
    recall = _reader_recall(workspace)
    page = workspace.store.current_object_page(
        object_id_prefix=lesson_object_prefix(FINANCE_PROFILE), after=after, limit=limit,
    )
    records = []
    for item in page["items"]:
        records.append(_lesson_projection(workspace, item, recall))
    return {
        "schema_version": "orgrebase.experience-lesson-page.v1",
        "items": records,
        "next_cursor": page["next_cursor"],
    }


def _reader_recall(workspace: Any) -> ExperienceRecallService | None:
    try:
        actors = ExperiencePhaseActors.from_deployment()
    except IntegrityError as error:
        if str(error) != "EXPERIENCE_PHASE_AUTHORITY_UNCONFIGURED":
            raise
        return None
    return _recall(workspace, actors)


def _lesson_projection(
    workspace: Any, item: Any, recall: ExperienceRecallService | None,
) -> dict[str, Any]:
    if item.kind != "ExperienceLesson" or item.payload.get("profile_id") != FINANCE_PROFILE:
        raise IntegrityError("EXPERIENCE_LESSON_SCOPE_INVALID")
    if recall is None:
        body, reason = None, "ASSESSMENT_CONFIGURATION_UNAVAILABLE"
    else:
        body, reason = recall._current_body(
            item, purpose=FINANCE_PROFILE, recipient="workspace-advisory",
        )
        if body is None and reason == "SOURCE_OR_CONTENT_INVALID":
            reason = _lesson_source_hold_reason(workspace, item)
    return {
        "head_ref": item.ref,
        "head_digest": item.digest,
        "lesson_id": item.payload.get("lesson_id"),
        "state": item.state.value,
        "maintenance_status": item.payload.get("status"),
        "maintenance_reason_code": item.payload.get("reason_code"),
        "status": "QUALIFIED_FOR_RECALL" if body is not None else "HOLD",
        "reason_code": reason,
        "problem_code": body.problem_code if body is not None else None,
        "support_cluster_count": (
            len(item.payload.get("support_cluster_ids", ())) if body is not None else None
        ),
        "content_bytes_disclosed": 0,
    }


def _lesson_source_hold_reason(workspace: Any, item: Any) -> str:
    """Distinguish safe source failures; never return private refs or text."""

    support_refs = item.payload.get("support_assessment_refs", ())
    if not isinstance(support_refs, (tuple, list)) or len(support_refs) > 16:
        return "SOURCE_OR_CONTENT_INVALID"
    try:
        for assessment_ref in support_refs:
            receipt = CaseAssessmentReceipt.model_validate(
                workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
            ).revalidated()
            case = CaseObservationV2.model_validate(
                workspace.store.load_artifact(receipt.case_ref, OBSERVATION_MEDIA).payload,
            ).revalidated()
            if (
                receipt.case_digest != case.digest
                or receipt.tenant_id != workspace.store.tenant_id
                or receipt.workspace_id != workspace.store.workspace_id
                or case.tenant_id != workspace.store.tenant_id
                or case.workspace_id != workspace.store.workspace_id
            ):
                return "SOURCE_OR_CONTENT_INVALID"
            private_status = workspace.private_records.record_status(case.private_episode_ref)
            if private_status == "DELETED":
                return "PRIVATE_EPISODE_DELETED"
            if private_status == "EXPIRED":
                return "PRIVATE_EPISODE_EXPIRED"
            if private_status != "AVAILABLE":
                return "PRIVATE_EPISODE_UNAVAILABLE"
            if is_case_source_held(workspace, case):
                return "SOURCE_INVALIDATED"
            try:
                require_quote_case_current(workspace, case)
            except (AttributeError, IntegrityError, KeyError, ValueError, PermissionError):
                return "SOURCE_QUALIFICATION_UNAVAILABLE"
    except (IntegrityError, KeyError, TypeError, ValueError, PermissionError):
        return "SOURCE_OR_CONTENT_INVALID"
    return "SOURCE_OR_CONTENT_INVALID"


def experience_lesson_detail(workspace: Any, lesson_id: str) -> dict[str, Any]:
    """Read only the current low-sensitivity head and its source case links."""

    require_action(workspace, "read")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", lesson_id) is None:
        raise ValueError("EXPERIENCE_LESSON_ID_INVALID")
    item = workspace.store.get_object(lesson_object_prefix(FINANCE_PROFILE) + lesson_id)
    projection = _lesson_projection(workspace, item, _reader_recall(workspace))
    if item.payload.get("lesson_id") != lesson_id:
        raise IntegrityError("EXPERIENCE_LESSON_ID_BINDING_INVALID")
    related_case_refs = set()
    assessment_refs = item.payload.get("assessment_refs", ()) if item.payload.get("status") == "ADMITTED" else ()
    related_case_coverage = "COMPLETE"
    if len(assessment_refs) > 32:
        related_case_coverage = "PARTIAL_COVERAGE"
        assessment_refs = assessment_refs[:32]
    if item.payload.get("status") == "ADMITTED":
        for assessment_ref in assessment_refs:
            receipt = CaseAssessmentReceipt.model_validate(
                workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
            ).revalidated()
            if (
                receipt.tenant_id != workspace.store.tenant_id
                or receipt.workspace_id != workspace.store.workspace_id
                or receipt.profile_id != FINANCE_PROFILE
            ):
                raise IntegrityError("EXPERIENCE_LESSON_ASSESSMENT_SCOPE_INVALID")
            related_case_refs.add(receipt.case_ref)
    recall_page = workspace.store.artifact_page(
        artifact_id_prefix="experience-recall-receipt:", expected_media_type=RECALL_MEDIA,
        limit=100,
    )
    recall_count = 0
    for row in recall_page["items"]:
        receipt = RecallReceipt.model_validate(row.payload).revalidated()
        if (
            row.artifact_id != "experience-recall-receipt:" + receipt.digest[7:] + "@v1"
            or receipt.tenant_id != workspace.store.tenant_id
            or receipt.workspace_id != workspace.store.workspace_id
        ):
            raise IntegrityError("EXPERIENCE_RECALL_RECEIPT_SCOPE_INVALID")
        if receipt.profile_id == FINANCE_PROFILE and any(
            entry.lesson_ref == item.ref for entry in receipt.selected_lessons
        ):
            manifest = RecallSelectionManifest.model_validate(
                workspace.store.load_artifact(receipt.selection_ref, SELECTION_MEDIA).payload,
            ).revalidated()
            if (
                receipt.selection_ref != "experience-recall-selection:" + manifest.digest[7:] + "@v1"
                or receipt.selection_digest != manifest.digest
                or receipt.selected_lessons != manifest.selected_lessons
                or receipt.snapshot_digest != manifest.snapshot_digest
            ):
                raise IntegrityError("EXPERIENCE_RECALL_ACTIVITY_BINDING_INVALID")
            recall_count += 1
    retrieval_coverage = "COMPLETE" if recall_page["next_cursor"] is None else "PARTIAL_COVERAGE"
    return {
        "schema_version": "orgrebase.experience-lesson-detail.v1",
        **projection,
        "related_case_refs": sorted(related_case_refs),
        "related_case_coverage": related_case_coverage,
        "parent_count": len(item.payload.get("parent_refs", ())),
        "retrieval_status": "RECALLED" if recall_count else (
            "NOT_OBSERVED" if retrieval_coverage == "COMPLETE" else "PARTIAL_COVERAGE"
        ),
        "retrieval_count_observed": recall_count,
        "retrieval_coverage": retrieval_coverage,
        "actual_use_status": "NOT_CHECKED",
    }
