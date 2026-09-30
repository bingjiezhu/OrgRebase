"""Small authenticated Workspace entrypoints for governed experience.

The collector's service identity is deployment owned; a request cannot choose
an actor, workspace, case label or corpus qualification for itself.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_assessment import require_quote_case_current
from orgrebase.workspace.experience_collection import (
    OBSERVATION_MEDIA,
    ExperienceCollector,
)
from orgrebase.workspace.experience_contracts import CaseObservationV2
from orgrebase.workspace.experience_governance_operations import case_assessment_projection
from orgrebase.workspace.experience_source_hold import is_case_source_held
from orgrebase.workspace.skill_evolution_v2 import PROFILE_ID as FINANCE_PROFILE


class CollectExperienceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    limit: int = Field(default=50, ge=1, le=100)


class RevisitExperienceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    origin_event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )


def _collector(workspace: Any) -> ExperienceCollector:
    require_action(workspace, "read")
    enabled = os.environ.get("ORGREBASE_EXPERIENCE_COLLECTOR_ENABLED", "0").strip()
    if enabled not in {"0", "1"}:
        raise IntegrityError("EXPERIENCE_COLLECTOR_CONFIGURATION_INVALID")
    actor = os.environ.get("ORGREBASE_EXPERIENCE_COLLECTOR_ACTOR_ID", "").strip()
    if enabled == "1" and not actor:
        raise IntegrityError("EXPERIENCE_COLLECTOR_ACTOR_UNCONFIGURED")
    return ExperienceCollector(
        workspace, collector_actor_id=actor or "disabled:collector", enabled=enabled == "1",
    )


def collect_experience(workspace: Any, command: CollectExperienceInput) -> dict[str, Any]:
    collector = _collector(workspace)
    principal = request_principal.get()
    actor = principal.actor_id if principal is not None else "controlled-local"
    worker_id = "experience-worker:" + sha256_digest({
        "tenant": workspace.profile.organization_id,
        "workspace": workspace.store.workspace_id,
        "actor": actor,
    })[7:]
    result = collector.collect(worker_id=worker_id, limit=command.limit)
    return {
        "schema_version": "orgrebase.experience-collect-command.v1",
        "status": result.status,
        "cursor_sequence": result.cursor_sequence,
        "observed": result.observed,
        "quarantined": result.quarantined,
        "skipped": result.skipped,
        "has_more": result.has_more,
        "admitted_lessons": 0,
        "skill_versions_published": 0,
        "business_writes": 0,
    }


def list_experience_cases(
    workspace: Any, *, after: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    require_action(workspace, "read")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("EXPERIENCE_CASE_PAGE_LIMIT_INVALID")
    page = workspace.store.artifact_page(
        artifact_id_prefix="experience-case:", after=after, limit=limit,
        expected_media_type=OBSERVATION_MEDIA,
    )
    records = []
    for stored in page["items"]:
        case = _verified_case(workspace, stored.artifact_id, stored.payload)
        assessment = case_assessment_projection(workspace, stored.artifact_id)
        source_state, source_reason = _source_state(workspace, case)
        records.append({
            "case_ref": stored.artifact_id,
            "case_digest": case.digest,
            "profile_id": case.profile_id,
            "execution_outcome": case.execution_outcome,
            "assessment": assessment["status"],
            "assessment_profile_id": FINANCE_PROFILE,
            "assessment_coverage": assessment["coverage"],
            "assessment_count_observed": assessment["assessment_count_observed"],
            "observation_assessment": case.learning_assessment,
            "observation_status": case.observation_status,
            "cluster_status": case.cluster_status,
            "request_observed": case.request_digest is not None,
            "resume_observed": case.resume_digest is not None,
            "outcome_observed": case.outcome_artifact_digest is not None,
            "source_state": source_state,
            "source_reason_code": source_reason,
        })
    return {
        "schema_version": "orgrebase.experience-case-page.v1",
        "items": records,
        "next_cursor": page["next_cursor"],
    }


def _verified_case(workspace: Any, case_ref: str, payload: Any) -> CaseObservationV2:
    case = CaseObservationV2.model_validate(payload).revalidated()
    if (
        case.tenant_id != workspace.store.tenant_id
        or case.workspace_id != workspace.store.workspace_id
        or case_ref != f"experience-case:{case.case_id}:{case.revision[7:]}@v2"
        or not case.private_episode_ref
        or not case.private_episode_digest
    ):
        raise IntegrityError("EXPERIENCE_CASE_SCOPE_INVALID")
    return case


def _source_state(workspace: Any, case: CaseObservationV2) -> tuple[str, str | None]:
    private_status = workspace.private_records.record_status(case.private_episode_ref)
    if private_status != "AVAILABLE":
        return "HOLD", {
            "DELETED": "PRIVATE_EPISODE_DELETED",
            "EXPIRED": "PRIVATE_EPISODE_EXPIRED",
            "MISSING": "PRIVATE_EPISODE_UNAVAILABLE",
        }.get(private_status, "PRIVATE_EPISODE_UNAVAILABLE")
    if is_case_source_held(workspace, case):
        return "HOLD", "SOURCE_INVALIDATED"
    try:
        require_quote_case_current(workspace, case)
    except (AttributeError, IntegrityError, KeyError, ValueError, PermissionError):
        return "HOLD", "SOURCE_QUALIFICATION_UNAVAILABLE"
    return "CURRENT", None


def experience_case_detail(workspace: Any, case_ref: str) -> dict[str, Any]:
    """Low-sensitivity, current-source readback; never expose episode text."""

    require_action(workspace, "read")
    if not case_ref.startswith("experience-case:") or len(case_ref) > 256:
        raise ValueError("EXPERIENCE_CASE_REF_INVALID")
    stored = workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA)
    case = _verified_case(workspace, case_ref, stored.payload)
    assessment = case_assessment_projection(workspace, case_ref)
    source_state, source_reason = _source_state(workspace, case)
    return {
        "schema_version": "orgrebase.experience-case-detail.v1",
        "case_ref": case_ref,
        "case_digest": case.digest,
        "profile_id": case.profile_id,
        "business_event_id": case.business_event_id,
        "business_event_digest": case.business_event_digest,
        "origin_event_count": len(case.origin_event_refs),
        "execution_outcome": case.execution_outcome,
        "observation_status": case.observation_status,
        "observation_reason_code": case.reason_code,
        "cluster_status": case.cluster_status,
        "source_state": source_state,
        "source_reason_code": source_reason,
        "request_observed": case.request_digest is not None,
        "resume_observed": case.resume_digest is not None,
        "outcome_observed": case.outcome_artifact_digest is not None,
        "assessment_status": assessment["status"],
        "assessment_profile_id": FINANCE_PROFILE,
        "assessment_coverage": assessment["coverage"],
        "assessment_count_observed": assessment["assessment_count_observed"],
        "actual_use_status": "NOT_CHECKED",
        "private_content_disclosed": False,
    }


def revisit_experience(workspace: Any, command: RevisitExperienceInput) -> dict[str, Any]:
    record = _collector(workspace).revisit_quarantined(
        origin_event_digest=command.origin_event_digest,
        attempt_id=command.attempt_id,
    )
    return {
        "schema_version": "orgrebase.experience-revisit-view.v1",
        "status": record["status"],
        "origin_event_digest": record["origin_event_digest"],
        "case_ref": record.get("case_ref"),
        "case_digest": record.get("case_digest"),
        "reason_code": record.get("reason_code"),
    }
