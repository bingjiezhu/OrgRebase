"""Actual S05 Quote handoff use and post-use feedback, without causal claims.

The existing S05 operation intent and Pattern invocation are the authorities.
This module only verifies and projects them, then lets an independent assessor
anchor one later business event for assessment. It never releases a Skill,
labels a Lesson, or changes the original Quote/Apply state.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import AuthenticationError, authorize, current_authorization, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace import quote_pattern_bridge
from orgrebase.workspace.quote_recovery_operations import (
    OPERATION_INTENT_MEDIA_TYPE,
    _operation_storage,
    _read_service,
    read_quote_recovery_operation,
)

USE_MEDIA = "application/vnd.orgrebase.quote-recovery-actual-use.v1+json"
FEEDBACK_MEDIA = "application/vnd.orgrebase.quote-recovery-followup.v1+json"


class QuoteRecoveryFollowupCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    use_ref: str = Field(pattern=r"^quote-recovery-actual-use:[0-9a-f]{64}$")
    followup_event_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    reviewer_verdict: Literal["REVIEWABLE", "NEEDS_REWORK", "UNKNOWN"]
    reason_code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _read_actor(workspace: Any) -> str:
    principal = request_principal.get()
    check = current_authorization()
    if principal is None or check is None:
        raise AuthenticationError("QUOTE_USE_CURRENT_AUTHORIZATION_REQUIRED")
    authorize(principal, "read", workspace.profile.organization_id)
    check()
    return principal.actor_id


def _assessor(workspace: Any, expected_actor_id: str, use: dict[str, Any]) -> str:
    principal = request_principal.get()
    check = current_authorization()
    if principal is None or check is None:
        raise AuthenticationError("QUOTE_FEEDBACK_CURRENT_AUTHORIZATION_REQUIRED")
    authorize(principal, "govern", workspace.profile.organization_id)
    check()
    if (
        not expected_actor_id or principal.actor_id != expected_actor_id
        or principal.actor_id in {use["runtime_actor_id"], use["candidate_author_actor_id"]}
    ):
        raise AuthorizationError("QUOTE_FEEDBACK_INDEPENDENT_ASSESSOR_REQUIRED")
    return principal.actor_id


def _case_cluster(workspace: Any, event_id: str) -> str:
    event = workspace.changes.get(event_id)
    return "quote-recovery-source-cluster:" + sha256_digest({
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "object_id": event.proposal.id,
        "source_refs": sorted(event.proposal.source_refs),
    })[7:]


def read_quote_recovery_actual_use(workspace: Any, operation_key: str) -> dict[str, Any]:
    """Verify the canonical S05 operation and actual restricted resource use."""
    _read_actor(workspace)
    operation = read_quote_recovery_operation(workspace, operation_key)
    _, intent_ref = _operation_storage(workspace, operation_key)
    intent_artifact = workspace.store.load_artifact(intent_ref, OPERATION_INTENT_MEDIA_TYPE)
    intent = intent_artifact.payload
    if (
        intent.get("operation_key") != operation_key
        or intent.get("event_id") != operation["event_id"]
        or intent.get("candidate_ref") != operation["candidate_ref"]
        or intent.get("input_digest") != operation["input_digest"]
        or intent.get("case_digest") != operation["case_digest"]
    ):
        raise IntegrityError("QUOTE_USE_OPERATION_BINDING_INVALID")
    result = {
        "schema_version": "orgrebase.quote-recovery-actual-use-projection.v1",
        "operation_key": operation_key,
        "operation_intent_ref": intent_ref,
        "operation_intent_digest": intent_artifact.payload_digest,
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": "workspace-quote-evidence-recovery-v1",
        "event_id": intent["event_id"],
        "case_digest": intent["case_digest"],
        "case_cluster_id": _case_cluster(workspace, intent["event_id"]),
        "cluster_status": "PROVISIONAL_NOT_INDEPENDENCE_PROOF",
        "case_outcome_at_selection": intent["case_outcome"],
        "candidate_ref": intent["candidate_ref"],
        "run_id": intent["run_id"],
        "input_digest": intent["input_digest"],
        "policy_revision": intent["policy_revision"],
        "policy_digest": intent["policy_digest"],
        "execution_mode": "S05_ADOPTED_ACTUAL_USE",
        "operation_status": operation["status"],
        "actual_use_status": "NOT_CONFIRMED",
        "runtime_actor_id": None,
        "candidate_author_actor_id": None,
        "invocation_result_ref": None,
        "invocation_receipt_digest": None,
        "package_digest": None,
        "output_digest": None,
        "action": None,
        "loaded_resource_digests": [],
        "consumed_resource_digests": [],
        "original_apply_attribution": "PRE_USE_CONTEXT_NEVER_SKILL_EFFECT",
        "business_improvement_status": "NOT_EVALUATED",
        "target_writes": 0,
    }
    if operation["status"] != "CONSUMED":
        _read_actor(workspace)
        return result
    service = _read_service(workspace)
    rows = [
        row for row in service._family("invocation-result")
        if row.get("receipt", {}).get("digest") == operation["invocation_receipt_digest"]
    ]
    if len(rows) != 1:
        raise IntegrityError("QUOTE_USE_INVOCATION_RESULT_AMBIGUOUS")
    observed = rows[0]
    invocation_ref = (
        "pattern-evolution:invocation-result:"
        + observed["digest"].removeprefix("sha256:")
    )
    reservation = service._load(observed["reservation_ref"], "invocation-reservation")
    candidate = service._load(intent["candidate_ref"], "skill-candidate")
    receipt = observed["receipt"]
    trace = receipt.get("candidate_content")
    loaded = trace.get("loaded_resource_digests") if isinstance(trace, dict) else None
    consumed = trace.get("consumed_resource_digests") if isinstance(trace, dict) else None
    if (
        reservation.get("candidate_ref") != intent["candidate_ref"]
        or reservation.get("run_id") != intent["run_id"]
        or reservation.get("input_digest") != intent["input_digest"]
        or reservation.get("actor_id") != receipt.get("actor_id")
        or reservation.get("package_digest") != receipt.get("package_digest")
        or reservation.get("source_digest") != observed.get("source_digest")
        or reservation.get("capture_digest") != observed.get("capture_digest")
        or reservation.get("dependency_digest") != observed.get("dependency_digest")
        or observed.get("result", {}).get("action") != operation["action"]
        or sha256_digest(observed.get("result")) != operation["output_digest"]
        or receipt.get("digest") != operation["invocation_receipt_digest"]
        or receipt.get("digest") != sha256_digest({
            key: value for key, value in receipt.items() if key != "digest"
        })
        or receipt.get("authorization_mode") != "RELEASE"
        or receipt.get("candidate_only") is not True
        or receipt.get("target_writes") != 0
        or receipt.get("input_digest") != intent["input_digest"]
        or receipt.get("output_digest") != operation["output_digest"]
        or not isinstance(candidate.get("content_bundle"), dict)
        or not isinstance(trace, dict)
        or trace.get("bundle_digest") != candidate["content_bundle"]["digest"]
        or trace.get("target_writes") != 0
        or not isinstance(loaded, list)
        or not isinstance(consumed, list)
        or not consumed
        or not set(consumed).issubset(loaded)
        or receipt.get("package_digest") != operation["package_digest"]
    ):
        raise IntegrityError("QUOTE_USE_ACTUAL_CONSUMPTION_BINDING_INVALID")
    result.update({
        "actual_use_status": "CONFIRMED_RESTRICTED_CONSUMER",
        "runtime_actor_id": receipt["actor_id"],
        "candidate_author_actor_id": candidate["author_id"],
        "invocation_result_ref": invocation_ref,
        "invocation_receipt_digest": receipt["digest"],
        "package_digest": receipt["package_digest"],
        "output_digest": receipt["output_digest"],
        "action": observed["result"]["action"],
        "loaded_resource_digests": loaded,
        "consumed_resource_digests": consumed,
    })
    _read_actor(workspace)
    return result


def _use_ref(workspace: Any, operation_key: str) -> str:
    return "quote-recovery-actual-use:" + sha256_digest({
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "operation_key": operation_key,
    })[7:]


def record_quote_recovery_actual_use(
    workspace: Any, operation_key: str, *, assessor_actor_id: str,
) -> dict[str, Any]:
    """Anchor one verified actual use before a later follow-up may count."""
    projection = read_quote_recovery_actual_use(workspace, operation_key)
    if projection["actual_use_status"] != "CONFIRMED_RESTRICTED_CONSUMER":
        raise IntegrityError("QUOTE_USE_ACTUAL_CONSUMPTION_REQUIRED")
    assessor = _assessor(workspace, assessor_actor_id, projection)
    use_ref = _use_ref(workspace, operation_key)
    body = {
        "schema_version": "orgrebase.quote-recovery-actual-use.v1",
        "use_ref": use_ref,
        "projection": projection,
        "projection_digest": sha256_digest(projection),
        "assessor_actor_id": assessor,
        "claim_ceiling": "ACTUAL_RESOURCE_USE_ONLY_NO_BUSINESS_IMPROVEMENT_CLAIM",
        "target_writes": 0,
    }
    with workspace.store.transaction() as connection:
        check = current_authorization()
        assert check is not None
        def revalidate_use() -> None:
            _assessor(workspace, assessor_actor_id, projection)
            check()
            if read_quote_recovery_actual_use(workspace, operation_key) != projection:
                raise IntegrityError("QUOTE_USE_SOURCE_CHANGED")

        workspace.store.require_before_commit(
            connection, revalidate_use,
        )
        try:
            prior = workspace.store.load_artifact(use_ref, USE_MEDIA).payload
        except KeyError:
            prior = None
        if prior is not None:
            if prior != body:
                raise IntegrityError("QUOTE_USE_ANCHOR_CONFLICT")
            return body
        workspace.store.save_artifact(connection, use_ref, USE_MEDIA, body)
        workspace.store.append_event(connection, "QUOTE_RECOVERY_ACTUAL_USE_OBSERVED", {
            "use_ref": use_ref, "use_digest": sha256_digest(body),
            "operation_key": operation_key,
            "target_writes": 0,
        })
    return body


def _event_match(workspace: Any, event_type: str, field: str, value: str) -> dict[str, Any] | None:
    cursor = 0
    seen = 0
    matches = []
    while True:
        page = workspace.store.event_page(after=cursor, limit=500, event_types=(event_type,))
        for event in page["items"]:
            seen += 1
            if seen > 10_000:
                raise IntegrityError("QUOTE_FEEDBACK_EVENT_SCAN_UNBOUNDED")
            if event["payload"].get(field) == value:
                matches.append(event)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    if len(matches) > 1:
        raise IntegrityError("QUOTE_FEEDBACK_EVENT_IDENTITY_AMBIGUOUS")
    return matches[0] if matches else None


def record_quote_recovery_followup(
    workspace: Any, command: QuoteRecoveryFollowupCommand,
    *, assessor_actor_id: str,
) -> dict[str, Any]:
    """Bind one post-use event and independent opinion, never a success label."""
    command = QuoteRecoveryFollowupCommand.model_validate(command.model_dump(mode="json"))
    _read_actor(workspace)
    use = workspace.store.load_artifact(command.use_ref, USE_MEDIA).payload
    projection = use.get("projection")
    if (
        use.get("schema_version") != "orgrebase.quote-recovery-actual-use.v1"
        or use.get("use_ref") != command.use_ref
        or not isinstance(projection, dict)
        or use.get("projection_digest") != sha256_digest(projection)
        or projection.get("actual_use_status") != "CONFIRMED_RESTRICTED_CONSUMER"
        or projection.get("tenant_id") != workspace.profile.organization_id
        or projection.get("workspace_id") != workspace.store.workspace_id
    ):
        raise IntegrityError("QUOTE_FEEDBACK_USE_INVALID")
    operation_key = projection.get("operation_key")
    try:
        current_use = (
            read_quote_recovery_actual_use(workspace, operation_key)
            if isinstance(operation_key, str) else None
        )
    except KeyError as exc:
        raise IntegrityError("QUOTE_FEEDBACK_ACTUAL_USE_REVALIDATION_FAILED") from exc
    if command.use_ref != _use_ref(workspace, operation_key) or current_use != projection:
        raise IntegrityError("QUOTE_FEEDBACK_ACTUAL_USE_REVALIDATION_FAILED")
    assessor = _assessor(workspace, assessor_actor_id, projection)
    if command.followup_event_id == projection["event_id"]:
        raise IntegrityError("QUOTE_FEEDBACK_ORIGINAL_APPLY_NOT_EFFECT")
    anchor = _event_match(
        workspace, "QUOTE_RECOVERY_ACTUAL_USE_OBSERVED", "use_ref", command.use_ref,
    )
    if anchor is None or anchor["payload"].get("use_digest") != sha256_digest(use):
        raise IntegrityError("QUOTE_FEEDBACK_USE_ANCHOR_MISSING")
    anchor_sequence = anchor["sequence_no"]
    followup = workspace.changes.get(command.followup_event_id)
    outcome_event = _event_match(
        workspace, "WORKSPACE_CHANGE_OUTCOME_RECORDED", "kind", command.followup_event_id,
    )
    outcome_sequence = outcome_event["sequence_no"] if outcome_event is not None else None
    if outcome_sequence is not None and outcome_sequence <= anchor_sequence:
        raise IntegrityError("QUOTE_FEEDBACK_OUTCOME_PRECEDES_USE")
    detail = quote_pattern_bridge.change_detail(workspace, command.followup_event_id)
    outcome = (detail.get("outcome") or {}).get("artifact_digest")
    if outcome_sequence is None or outcome is None:
        raise IntegrityError("QUOTE_FEEDBACK_FOLLOWUP_OUTCOME_NOT_RECORDED")
    if (
        detail.get("status") != "APPLIED"
        or not isinstance(outcome, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", outcome) is None
    ):
        raise IntegrityError("QUOTE_FEEDBACK_OUTCOME_BINDING_INVALID")
    from orgrebase.workspace.service import WORKSPACE_OUTCOME_MEDIA_TYPE

    recorded = workspace.store.load_artifact(
        outcome_event["payload"]["artifact_id"], WORKSPACE_OUTCOME_MEDIA_TYPE,
    )
    if (
        recorded.payload_digest != outcome
        or outcome_event["payload"].get("artifact_digest") != outcome
    ):
        raise IntegrityError("QUOTE_FEEDBACK_OUTCOME_BINDING_INVALID")
    status = "POST_USE_OUTCOME_OBSERVED"
    original = workspace.changes.get(projection["event_id"])
    relatedness = (
        "SAME_BUSINESS_OBJECT"
        if followup.proposal.id == original.proposal.id
        else "UNVERIFIED_DIFFERENT_OBJECT"
    )
    feedback_ref = "quote-recovery-followup:" + command.use_ref.rsplit(":", 1)[1]
    body = {
        "schema_version": "orgrebase.quote-recovery-followup.v1",
        "feedback_ref": feedback_ref,
        "use_ref": command.use_ref,
        "use_digest": sha256_digest(use),
        "case_cluster_id": projection["case_cluster_id"],
        "cluster_status": projection["cluster_status"],
        "original_event_id": projection["event_id"],
        "original_outcome_timing": "PRE_USE_NOT_ATTRIBUTABLE",
        "followup_event_id": command.followup_event_id,
        "followup_event_digest": followup.digest,
        "relatedness": relatedness,
        "followup_outcome_artifact_digest": outcome,
        "use_sequence_no": anchor_sequence,
        "followup_outcome_sequence_no": outcome_sequence,
        "followup_status": status,
        "reviewer_verdict": command.reviewer_verdict,
        "reason_code": command.reason_code,
        "assessor_actor_id": assessor,
        "quality_status": "PENDING_INDEPENDENT_ASSESSMENT",
        "effect_status": "NO_EFFECT_CLAIM",
        "qualification_status": "NOT_QUALIFIED",
        "target_writes": 0,
    }
    with workspace.store.transaction() as connection:
        check = current_authorization()
        assert check is not None
        def revalidate_feedback() -> None:
            _assessor(workspace, assessor_actor_id, projection)
            check()
            if read_quote_recovery_actual_use(workspace, operation_key) != projection:
                raise IntegrityError("QUOTE_FEEDBACK_ACTUAL_USE_REVALIDATION_FAILED")
            current_anchor = _event_match(
                workspace, "QUOTE_RECOVERY_ACTUAL_USE_OBSERVED", "use_ref", command.use_ref,
            )
            current_outcome = _event_match(
                workspace, "WORKSPACE_CHANGE_OUTCOME_RECORDED", "kind", command.followup_event_id,
            )
            if (
                current_anchor != anchor
                or current_outcome != outcome_event
                or workspace.changes.get(command.followup_event_id).digest != followup.digest
            ):
                raise IntegrityError("QUOTE_FEEDBACK_SOURCE_CHANGED")

        workspace.store.require_before_commit(
            connection, revalidate_feedback,
        )
        try:
            prior = workspace.store.load_artifact(feedback_ref, FEEDBACK_MEDIA).payload
        except KeyError:
            prior = None
        if prior is not None:
            if prior != body:
                raise IntegrityError("QUOTE_FEEDBACK_CONFLICT_OR_SECOND_SAMPLE")
            return body
        workspace.store.save_artifact(connection, feedback_ref, FEEDBACK_MEDIA, body)
        workspace.store.append_event(connection, "QUOTE_RECOVERY_FOLLOWUP_OBSERVED", {
            "feedback_ref": feedback_ref, "feedback_digest": sha256_digest(body),
            "use_ref": command.use_ref,
            "followup_status": status, "target_writes": 0,
        })
    return body


__all__ = (
    "QuoteRecoveryFollowupCommand", "read_quote_recovery_actual_use",
    "record_quote_recovery_actual_use", "record_quote_recovery_followup",
)
