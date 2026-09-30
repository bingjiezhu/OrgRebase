"""Evidence bridge from one Quote recovery case into governed Pattern input."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import change_detail, require_action
from orgrebase.workspace.change_recovery import REQUEST_MEDIA, _prefix, requests, resume_record
from orgrebase.workspace.experience_contracts import CaseObservationV2, PrivateEpisode
from orgrebase.workspace.pattern_evolution import CaseObservation

PROFILE_ID = "workspace-quote-evidence-recovery-v1"

_OBSERVED_EVENTS = frozenset({
    "WORKSPACE_CHANGE_EVIDENCE_REQUESTED",
    "WORKSPACE_CHANGE_EVIDENCE_SUPPLIED",
    "WORKSPACE_CHANGE_REJECTED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
})


def _historical_requests(
    workspace: Any, event_id: str, event_digest: str, *, through_round: int,
) -> list[dict[str, Any]]:
    """Validate the original round chain without rebinding it to a newer run."""

    if type(through_round) is not int or not 1 <= through_round <= 32:
        raise IntegrityError("EXPERIENCE_RECOVERY_ROUND_LIMIT")
    records = []
    previous = None
    for number in range(1, through_round + 1):
        record = workspace.store.load_artifact(
            _prefix(event_id) + f"request:{number:04d}", REQUEST_MEDIA,
        ).payload
        if (
            record.get("digest") != sha256_digest({k: v for k, v in record.items() if k != "digest"})
            or record.get("event_id") != event_id
            or record.get("event_digest") != event_digest
            or record.get("workspace_id") != workspace.store.workspace_id
            or record.get("round") != number
            or record.get("previous_request_digest") != previous
            or not isinstance(record.get("run_id"), str)
            or not record["run_id"]
        ):
            raise IntegrityError("EXPERIENCE_RECOVERY_REQUEST_CHAIN_INVALID")
        previous = record["digest"]
        records.append(record)
    return records


def quote_recovery_observation_v2(
    workspace: Any, source_event: dict[str, Any],
) -> tuple[CaseObservationV2, dict[str, Any]]:
    """Observe one exact recovery event, including a still-pending round.

    This is deliberately separate from the strict v1 corpus certificate.  A
    historical REQUESTED event remains pending even when a later event has
    produced an Apply outcome.  The returned episode body must be written only
    to PrivateRecordStore; the case carries whitelisted refs and digests.
    """

    require_action(workspace, "read")
    event_type = source_event.get("event_type")
    if event_type not in _OBSERVED_EVENTS:
        raise IntegrityError("EXPERIENCE_EVENT_TYPE_UNSUPPORTED")
    payload = source_event.get("payload")
    if not isinstance(payload, dict):
        raise IntegrityError("EXPERIENCE_EVENT_PAYLOAD_INVALID")
    event_id = payload.get("kind") if event_type == "WORKSPACE_CHANGE_OUTCOME_RECORDED" else payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise IntegrityError("EXPERIENCE_BUSINESS_EVENT_ID_MISSING")
    business_event = workspace.changes.get(event_id)
    round_number = payload.get("round")
    if event_type in {"WORKSPACE_CHANGE_EVIDENCE_REQUESTED", "WORKSPACE_CHANGE_EVIDENCE_SUPPLIED"}:
        if type(round_number) is not int or round_number < 1:
            raise IntegrityError("EXPERIENCE_RECOVERY_ROUND_INVALID")
    else:
        prior_page = workspace.store.event_page(
            after=source_event["sequence_no"], limit=1, descending=True,
            event_types=("WORKSPACE_CHANGE_EVIDENCE_REQUESTED",), subject_key=event_id,
        )
        if not prior_page["items"]:
            raise IntegrityError("EXPERIENCE_RECOVERY_REQUEST_NOT_FOUND")
        prior = prior_page["items"][0]
        round_number = prior["payload"].get("round")
        if type(round_number) is not int or round_number < 1:
            raise IntegrityError("EXPERIENCE_RECOVERY_ROUND_INVALID")
    history = _historical_requests(
        workspace, event_id, business_event.digest, through_round=round_number,
    )
    request = history[-1]
    if event_type == "WORKSPACE_CHANGE_EVIDENCE_REQUESTED" and request["digest"] != payload.get("recovery_digest"):
        raise IntegrityError("EXPERIENCE_RECOVERY_REQUEST_BINDING_MISMATCH")
    if request["event_digest"] != business_event.digest:
        raise IntegrityError("EXPERIENCE_BUSINESS_EVENT_BINDING_MISMATCH")
    resumed = None
    if event_type in {"WORKSPACE_CHANGE_EVIDENCE_SUPPLIED", "WORKSPACE_CHANGE_OUTCOME_RECORDED", "WORKSPACE_CHANGE_REJECTED"}:
        resumed = resume_record(workspace, event_id, request)
    if event_type == "WORKSPACE_CHANGE_EVIDENCE_SUPPLIED" and (
        resumed is None or resumed["digest"] != payload.get("recovery_digest")
    ):
        raise IntegrityError("EXPERIENCE_RECOVERY_RESUME_BINDING_MISMATCH")
    outcome_digest = None
    status = "PENDING"
    if event_type == "WORKSPACE_CHANGE_OUTCOME_RECORDED":
        if resumed is None:
            raise IntegrityError("EXPERIENCE_RECOVERY_RESUME_NOT_FOUND")
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or artifact_id != f"workspace-outcome:{event_id}@r1":
            raise IntegrityError("EXPERIENCE_OUTCOME_ARTIFACT_ID_INVALID")
        from orgrebase.workspace.service import WORKSPACE_OUTCOME_MEDIA_TYPE

        outcome = workspace.store.load_artifact(artifact_id, WORKSPACE_OUTCOME_MEDIA_TYPE)
        if (
            outcome.payload_digest != payload.get("artifact_digest")
            or outcome.payload.get("kind") != event_id
            or outcome.payload.get("approval_digest") != payload.get("approval_digest")
        ):
            raise IntegrityError("EXPERIENCE_OUTCOME_BINDING_MISMATCH")
        outcome_digest = outcome.payload_digest
        status = "APPLIED"
    elif event_type == "WORKSPACE_CHANGE_REJECTED":
        if payload.get("event_digest") != business_event.digest or payload.get("status") != "REJECTED":
            raise IntegrityError("EXPERIENCE_REJECTION_BINDING_MISMATCH")
        status = "REJECTED"
    observation_ref = business_event.proposal.payload.get("source_observation_ref")
    source_identity = None
    if (
        business_event.proposal.version.startswith("source-")
        and isinstance(observation_ref, str)
        and re.fullmatch(r"source-observation:[0-9a-f]{64}", observation_ref)
    ):
        observation = workspace.store.load_artifact(observation_ref, "application/json").payload
        candidate_source = observation.get("source_ref")
        if (
            not isinstance(candidate_source, str)
            or candidate_source not in business_event.proposal.source_refs
            or observation.get("page_ref") not in business_event.proposal.source_refs
            or observation.get("value_digest") != sha256_digest(
                business_event.proposal.payload.get("canonical_value")
            )
        ):
            raise IntegrityError("EXPERIENCE_SOURCE_OBSERVATION_BINDING_INVALID")
        source_identity = candidate_source
    cluster_id = (
        "quote-source:" + sha256_digest({
            "tenant_id": workspace.store.tenant_id,
            "workspace_id": workspace.store.workspace_id,
            "object_id": business_event.proposal.id,
            "source_identity": source_identity,
        })[7:]
        if source_identity else None
    )
    evidence = {
        "event_digest": source_event["event_digest"],
        "business_event_digest": business_event.digest,
        "request_digest": request["digest"],
        "resume_digest": resumed["digest"] if resumed is not None else None,
        "outcome_artifact_digest": outcome_digest,
        "execution_outcome": status,
        "round": round_number,
    }
    case_id = "quote-recovery:" + sha256_digest({
        "tenant_id": workspace.store.tenant_id,
        "workspace_id": workspace.store.workspace_id,
        "business_event_id": event_id,
    })[7:]
    revision = sha256_digest(evidence)
    episode = PrivateEpisode(
        case_id=case_id, case_revision=revision,
        request=request, resume=resumed, source_event=source_event,
    ).model_dump(mode="json")
    return (
        CaseObservationV2(
            tenant_id=workspace.store.tenant_id,
            workspace_id=workspace.store.workspace_id,
            profile_id=PROFILE_ID,
            case_id=case_id,
            revision=revision,
            independence_cluster_id=cluster_id,
            cluster_status="PROVISIONAL" if cluster_id else "UNDETERMINED",
            origin_event_refs=(source_event["event_digest"],),
            business_event_id=event_id,
            business_event_digest=business_event.digest,
            run_id=request.get("run_id"),
            task_id=request.get("task_id"),
            attempt_id=(resumed or request).get("operation_id"),
            request_digest=request["digest"],
            resume_digest=resumed["digest"] if resumed is not None else None,
            outcome_artifact_digest=outcome_digest,
            execution_outcome=status,
        ),
        episode,
    )


def _case_material(workspace: Any, event_id: str) -> dict[str, Any]:
    require_action(workspace, "read")
    event = workspace.changes.get(event_id)
    recovery_requests = requests(workspace, event_id)
    if not recovery_requests:
        raise IntegrityError("QUOTE_PATTERN_RECOVERY_CASE_REQUIRED")
    request = recovery_requests[-1]
    resumed = resume_record(workspace, event_id, request)
    if resumed is None:
        raise IntegrityError("QUOTE_PATTERN_RESUME_CASE_REQUIRED")
    detail = change_detail(workspace, event_id)
    outcome_record = detail.get("outcome")
    outcome = (
        "SUPPORT"
        if detail["status"] == "APPLIED" and outcome_record is not None
        else "UNKNOWN"
    )
    public_input = {
        "profile_id": PROFILE_ID,
        "slot_id": event.slot_id,
        "domain_id": event.proposal.domain,
        "failure_kind": "EVIDENCE_REQUIRED",
        "recovery_round": request["round"],
        "required_evidence_count": len(request["required_evidence_refs"]),
        "candidate_only": outcome != "SUPPORT",
        "run_id": workspace.effective_workflow_run_id,
        "task_id": f"quote-recovery:{event_id}",
        "delegation_id": f"quote-recovery:{event.proposal.domain}",
        "delegation_task_digest": event.digest,
        "context_projection_digest": request["digest"],
        "candidate_bundle": {
            "domain": event.proposal.domain,
            "profile_id": PROFILE_ID,
            "evidence": {
                "request_digest": request["digest"],
                "resume_digest": resumed["digest"],
                "outcome_artifact_digest": (outcome_record or {}).get("artifact_digest"),
            },
        },
    }
    evidence = {
        "event_digest": event.digest,
        "request_digest": request["digest"],
        "resume_digest": resumed["digest"],
        "preview_digest": (detail.get("preview") or {}).get("preview_digest"),
        "approval_digest": (detail.get("approval") or {}).get("approval_digest"),
        "outcome_artifact_digest": (outcome_record or {}).get("artifact_digest"),
        "status": detail["status"],
    }
    case_id = "quote-recovery:" + sha256_digest(
        {
            "tenant_id": workspace.store.tenant_id,
            "workspace_id": workspace.store.workspace_id,
            "event_id": event_id,
        }
    ).removeprefix("sha256:")
    revision = sha256_digest(evidence)
    return {
        "case_id": case_id,
        "revision": revision,
        "public_input": public_input,
        "outcome": outcome,
        "evidence": evidence,
    }


def build_quote_recovery_case(
    workspace: Any, event_id: str, *, corpus_authority: str
) -> CaseObservation:
    """Return an unadmitted case candidate backed by current business receipts."""

    if not isinstance(corpus_authority, str) or not corpus_authority.strip():
        raise ValueError("QUOTE_PATTERN_CORPUS_AUTHORITY_REQUIRED")
    material = _case_material(workspace, event_id)
    certificate = {
        "schema_version": "orgrebase.quote-pattern-case.v1",
        "kind": "quote-evidence-recovery-case-candidate",
        "profile_id": PROFILE_ID,
        "case_id": material["case_id"],
        "case_revision": material["revision"],
        "input_digest": sha256_digest(material["public_input"]),
        "outcome": material["outcome"],
        "issuer": corpus_authority,
        "evidence": material["evidence"],
        "corpus_admitted": False,
        "claim_scope": "WORKSPACE_CASE_CANDIDATE_NOT_SKILL_EFFECTIVENESS",
    }
    certificate["digest"] = sha256_digest(certificate)
    return CaseObservation(
        case_id=material["case_id"],
        revision=material["revision"],
        public_input=material["public_input"],
        outcome=material["outcome"],
        certificate=certificate,
    )


def quote_recovery_consumer_input(case: CaseObservation) -> dict[str, Any]:
    """Project a verified case onto the exact structured-handoff input contract."""

    case = case.revalidated()
    if (
        case.certificate.get("kind") != "quote-evidence-recovery-case-candidate"
        or case.certificate.get("profile_id") != PROFILE_ID
        or case.public_input.get("profile_id") != PROFILE_ID
    ):
        raise IntegrityError("QUOTE_PATTERN_CASE_PROFILE_UNSUPPORTED")
    fields = (
        "run_id",
        "task_id",
        "delegation_id",
        "delegation_task_digest",
        "context_projection_digest",
        "candidate_bundle",
    )
    try:
        projection = {name: case.public_input[name] for name in fields}
    except KeyError as exc:
        raise IntegrityError("QUOTE_PATTERN_CONSUMER_PROJECTION_INCOMPLETE") from exc
    evidence = projection.get("candidate_bundle", {}).get("evidence")
    certificate_evidence = case.certificate.get("evidence", {})
    if (
        not isinstance(evidence, dict)
        or projection["candidate_bundle"].get("profile_id") != PROFILE_ID
        or projection.get("delegation_task_digest")
        != certificate_evidence.get("event_digest")
        or projection.get("context_projection_digest")
        != certificate_evidence.get("request_digest")
        or evidence.get("request_digest") != certificate_evidence.get("request_digest")
        or evidence.get("resume_digest") != certificate_evidence.get("resume_digest")
        or evidence.get("outcome_artifact_digest")
        != certificate_evidence.get("outcome_artifact_digest")
    ):
        raise IntegrityError("QUOTE_PATTERN_CONSUMER_PROJECTION_BINDING_MISMATCH")
    return projection


def make_quote_recovery_case_resolver(
    workspace: Any,
) -> Callable[[CaseObservation], tuple[str, ...]]:
    """Re-read the exact event/recovery/outcome before corpus admission."""

    def resolve(case: CaseObservation) -> tuple[str, ...]:
        case = case.revalidated()
        certificate = case.certificate
        if (
            certificate.get("schema_version") != "orgrebase.quote-pattern-case.v1"
            or certificate.get("kind") != "quote-evidence-recovery-case-candidate"
            or certificate.get("profile_id") != PROFILE_ID
        ):
            raise IntegrityError("QUOTE_PATTERN_CASE_PROFILE_UNSUPPORTED")
        event_id = None
        for candidate in workspace.changes.pending_ids() + workspace.change_order:
            material = None
            try:
                material = _case_material(workspace, candidate)
            except (IntegrityError, KeyError):
                continue
            if material["case_id"] == case.case_id:
                event_id = candidate
                break
        if event_id is None:
            raise IntegrityError("QUOTE_PATTERN_CASE_SOURCE_NOT_FOUND")
        material = _case_material(workspace, event_id)
        if (
            material["revision"] != case.revision
            or material["public_input"] != case.public_input
            or material["outcome"] != case.outcome
            or material["evidence"] != certificate.get("evidence")
        ):
            raise IntegrityError("QUOTE_PATTERN_CASE_EVIDENCE_CHANGED")
        return tuple(
            value
            for key, value in sorted(material["evidence"].items())
            if key.endswith("_digest") and isinstance(value, str)
        )

    return resolve
