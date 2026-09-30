"""Read-only Finance V4 resource delivery and low-trust feedback projection.

This reads the existing immutable Selection, Use and Preview records. It never
dispatches a model, changes a qualification, or creates another feedback
ledger. A wire binding proves delivery to the provider interface, not that a
model attended to a resource or that a business result improved.
"""

from __future__ import annotations

import json
from typing import Any

from orgrebase.auth import AuthenticationError, authorize, current_authorization, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.advisory import DomainAdvisoryCandidate
from orgrebase.workspace.experience_contracts import (
    MemorySnapshot,
    RecallSelectionManifest,
    exact_bytes_digest,
)
from orgrebase.workspace.finance_adoption import (
    USE_MEDIA,
    FinanceSelectionReceipt,
    _event_attempt_guard,
    _read_selection,
    use_payload,
    use_ref,
)
from orgrebase.workspace.models import ModelRequestV4, ModelResponseReceiptV4, WorkspacePreviewBundle
from orgrebase.workspace.preview_execution import _attempt_key
from orgrebase.workspace.skill_evolution_v2 import (
    CONTENT_MEDIA,
    INSTRUCTION_PATH,
    REFERENCE_PATH,
    SkillContentBundleV2,
)
from orgrebase.workspace.vertex_candidate import build_vertex_advice_body, vertex_advice_wire_digests

_REVIEW_MEDIA = "application/vnd.orgrebase.review-observation+json"
_EVALUATION_SNAPSHOT_MEDIA = "application/vnd.orgrebase.memory-snapshot.v1+json"
_EVALUATION_MANIFEST_MEDIA = "application/vnd.orgrebase.recall-selection-manifest.v1+json"
_ADOPTED_SNAPSHOT_MEDIA = "application/vnd.orgrebase.memory-snapshot+json"
_ADOPTED_MANIFEST_MEDIA = "application/vnd.orgrebase.recall-selection-manifest+json"


def _reader(workspace: Any) -> None:
    principal = request_principal.get()
    check = current_authorization()
    if principal is None or check is None:
        raise AuthenticationError("FINANCE_USE_READ_AUTHORIZATION_REQUIRED")
    authorize(principal, "read", workspace.profile.organization_id)
    check()


def _selection(workspace: Any, event_id: str) -> list[tuple[int, FinanceSelectionReceipt]]:
    from orgrebase.workspace.change_recovery import REQUEST_MEDIA, _prefix, requests, resume_record

    def selected_round(
        number: int, recovery_digest: str | None,
        request: dict[str, Any] | None,
    ) -> FinanceSelectionReceipt | None:
        command = f"change:{event_id}:recovery:{number}" if number else f"change:{event_id}"
        guard_key, guard_digest = _event_attempt_guard(
            workspace, event_id, recovery_request=request,
        )
        guard = workspace.store.get_idempotent(guard_key, guard_digest)
        if guard is None:
            return None
        attempt_key = guard.get("attempt_key")
        if not isinstance(attempt_key, str) or not attempt_key:
            raise IntegrityError("FINANCE_USE_EVENT_ATTEMPT_BINDING_INVALID")
        value = _read_selection(workspace, attempt_key)
        if value is None:
            raise IntegrityError("FINANCE_USE_SELECTION_BINDING_INVALID")
        if (
            value.event_id != event_id
            or value.attempt_key != attempt_key
            or value.tenant_id != workspace.profile.organization_id
            or value.workspace_id != workspace.store.workspace_id
            or _attempt_key(command, value.run_id, value.run_nonce) != attempt_key
            or (request is not None and value.run_id != request["run_id"])
        ):
            raise IntegrityError("FINANCE_USE_SELECTION_SCOPE_INVALID")
        if value.recovery_digest != recovery_digest:
            raise IntegrityError("FINANCE_USE_RECOVERY_BINDING_INVALID")
        return value

    first = selected_round(0, None, None)
    if first is None:
        return []

    # Bound only this event's request family before calling the recovery
    # service's full chain validator. Other cases' selections never enter the
    # read path, even in a workspace with a large adoption history.
    prefix = _prefix(event_id) + "request:"
    cursor = None
    count = 0
    while True:
        page = workspace.store.artifact_page(
            artifact_id_prefix=prefix, after=cursor, limit=500,
            expected_media_type=REQUEST_MEDIA,
        )
        count += len(page["items"])
        if count > 1000 or (count == 1000 and page["next_cursor"] is not None):
            raise IntegrityError("FINANCE_USE_RECOVERY_HISTORY_UNBOUNDED")
        cursor = page["next_cursor"]
        if cursor is None:
            break
    rounds = requests(workspace, event_id)
    if len(rounds) != count:
        raise IntegrityError("FINANCE_USE_RECOVERY_HISTORY_CHANGED")
    selected = [(0, first)]
    for request in rounds:
        number = request["round"]
        resume = resume_record(workspace, event_id, request)
        value = selected_round(
            number, resume["digest"] if resume is not None else None, request,
        )
        if value is not None:
            if resume is None:
                raise IntegrityError("FINANCE_USE_RECOVERY_BINDING_INVALID")
            selected.append((number, value))
    return selected


def _finance_handoff(bundle: WorkspacePreviewBundle, selection: FinanceSelectionReceipt):
    matches = [item for item in bundle.advisory.handoffs if item.task_id == selection.task_id]
    if len(matches) != 1 or not isinstance(matches[0].payload.get("model_advisory"), dict):
        raise IntegrityError("FINANCE_USE_V4_HANDOFF_MISSING")
    generated = matches[0].payload["model_advisory"]
    request = ModelRequestV4.model_validate(generated["request"]).revalidated()
    receipt = ModelResponseReceiptV4.model_validate(generated["receipt"]).revalidated()
    return request, receipt


def _resource_delivery(
    workspace: Any, selection: FinanceSelectionReceipt,
    request: ModelRequestV4, receipt: ModelResponseReceiptV4,
) -> dict[str, Any]:
    advice = request.advice_context
    snapshot_media, manifest_media = (
        (_ADOPTED_SNAPSHOT_MEDIA, _ADOPTED_MANIFEST_MEDIA)
        if advice.execution_mode == "ADOPTED"
        else (_EVALUATION_SNAPSHOT_MEDIA, _EVALUATION_MANIFEST_MEDIA)
    )
    content = SkillContentBundleV2.from_payload(
        workspace.store.load_artifact(advice.package_ref, CONTENT_MEDIA).payload,
    )
    snapshot = MemorySnapshot.model_validate(
        workspace.store.load_artifact(selection.snapshot_ref, snapshot_media).payload,
    ).revalidated()
    manifest = RecallSelectionManifest.model_validate(
        workspace.store.load_artifact(selection.manifest_ref, manifest_media).payload,
    ).revalidated()
    body = build_vertex_advice_body(request, DomainAdvisoryCandidate)
    wire = vertex_advice_wire_digests(request, DomainAdvisoryCandidate)
    user = json.loads(body["contents"][0]["parts"][0]["text"])
    untrusted = user["UNTRUSTED_ADVICE"]
    guidance, memory = untrusted["guidance"], untrusted["memory"]
    lesson_refs = [
        {"ref": item.ref, "revision": item.revision, "content_digest": item.content_digest}
        for item in advice.lessons
    ]
    selected_refs = [
        {"ref": item.lesson_ref, "revision": item.lesson_revision,
         "content_digest": item.content_digest}
        for item in manifest.selected_lessons
    ]
    if (
        advice.package_ref != "skill-content-v2:" + content.digest[7:]
        or content.package_digest != selection.package_digest
        or content.resource_digests[INSTRUCTION_PATH] != advice.instruction_digest
        or content.resource_digests[REFERENCE_PATH] != advice.reference_digest
        or content.instruction_bytes != advice.instruction_text.encode("utf-8")
        or content.reference_bytes != advice.reference_text.encode("utf-8")
        or advice.memory_snapshot_ref != selection.snapshot_ref
        or advice.memory_snapshot_digest != selection.snapshot_digest
        or snapshot.digest != selection.snapshot_digest
        or manifest.digest != selection.manifest_digest
        or manifest.snapshot_digest != snapshot.digest
        or advice.recall_manifest_ref != selection.manifest_ref
        or advice.recall_manifest_digest != selection.manifest_digest
        or (manifest.selected_lessons and any(
            item not in snapshot.lesson_entries for item in manifest.selected_lessons
        ))
        or lesson_refs != selected_refs
        or memory["lessons"] != lesson_refs
        or memory["advice_text"] != advice.advice_text
        or guidance != {
            "instruction_ref": advice.instruction_ref,
            "instruction_text": advice.instruction_text,
            "reference_ref": advice.reference_ref,
            "reference_text": advice.reference_text,
        }
        or exact_bytes_digest(guidance["instruction_text"].encode("utf-8")) != advice.instruction_digest
        or exact_bytes_digest(guidance["reference_text"].encode("utf-8")) != advice.reference_digest
        or exact_bytes_digest(memory["advice_text"].encode("utf-8")) != advice.advice_bytes_digest
        or manifest.advice_bytes_digest != advice.advice_bytes_digest
        or manifest.advice_byte_count != advice.advice_byte_count
        or receipt.body_digest != wire["body_digest"]
        or receipt.prompt_digest != wire["prompt_digest"]
        or receipt.wire_schema_digest != wire["wire_schema_digest"]
        or receipt.business_wire_digest != wire["business_wire_digest"]
        or receipt.advice_wire_digest != wire["advice_wire_digest"]
    ):
        raise IntegrityError("FINANCE_USE_RESOURCE_WIRE_BINDING_INVALID")
    return {
        "instruction": {
            "ref": advice.instruction_ref, "content_digest": advice.instruction_digest,
            "loaded_exact_bytes": True, "included_in_wire": True,
        },
        "reference": {
            "ref": advice.reference_ref, "content_digest": advice.reference_digest,
            "loaded_exact_bytes": True, "included_in_wire": True,
        },
        "lessons": [
            {**item, "reference_and_digest_in_wire": True,
             "individual_text_contribution": "UNPROVEN"}
            for item in lesson_refs
        ],
        "aggregate_advice": {
            "content_digest": advice.advice_bytes_digest,
            "byte_count": advice.advice_byte_count,
            "included_in_wire": bool(advice.advice_text),
            "individual_lesson_text_attribution": "UNPROVEN",
        },
        "selection_mode": advice.selection_mode,
        "compiler_version": advice.compiler_version,
        "consumer_version": content.payload["consumer_id"],
        "wire_body_digest": receipt.body_digest,
        "system_prompt_digest": receipt.prompt_digest,
        "wire_schema_digest": receipt.wire_schema_digest,
        "business_wire_digest": receipt.business_wire_digest,
        "advice_wire_digest": receipt.advice_wire_digest,
    }


def _feedback(
    workspace: Any, event_id: str, preview_digest: str,
    preview_artifact_id: str,
) -> dict[str, Any]:
    cursor = 0
    examined = 0
    preview_event = None
    later: list[dict[str, Any]] = []
    same_event_previews: list[dict[str, Any]] = []
    while True:
        page = workspace.store.event_page(
            after=cursor, limit=500, subject_key=event_id,
            event_types=(
                "WORKSPACE_CHANGE_PREVIEWED", "WORKSPACE_REVIEW_OBSERVATION_RECORDED",
                "WORKSPACE_CHANGE_APPROVED", "WORKSPACE_CHANGE_OUTCOME_RECORDED",
            ),
        )
        for event in page["items"]:
            examined += 1
            if examined > 10_000:
                raise IntegrityError("FINANCE_USE_FEEDBACK_SCAN_UNBOUNDED")
            payload = event["payload"]
            if event["event_type"] == "WORKSPACE_CHANGE_PREVIEWED":
                if payload.get("kind") != event_id:
                    raise IntegrityError("FINANCE_USE_FEEDBACK_SUBJECT_INVALID")
                same_event_previews.append(event)
                if payload.get("artifact_id") == preview_artifact_id:
                    if preview_event is not None or payload.get("preview_digest") != preview_digest:
                        raise IntegrityError("FINANCE_USE_PREVIEW_EVENT_AMBIGUOUS")
                    preview_event = event
            else:
                field = "event_id" if event["event_type"] == "WORKSPACE_REVIEW_OBSERVATION_RECORDED" else "kind"
                if payload.get(field) != event_id:
                    raise IntegrityError("FINANCE_USE_FEEDBACK_SUBJECT_INVALID")
                later.append(event)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    if preview_event is None:
        raise IntegrityError("FINANCE_USE_PREVIEW_EVENT_MISSING")
    next_preview = min((item["sequence_no"] for item in same_event_previews
                        if item["sequence_no"] > preview_event["sequence_no"]), default=None)
    reviews: list[dict[str, Any]] = []
    approval_seen = False
    outcome_seen = False
    for event in later:
        if (event["sequence_no"] <= preview_event["sequence_no"]
                or (next_preview is not None and event["sequence_no"] >= next_preview)):
            continue
        payload = event["payload"]
        if event["event_type"] == "WORKSPACE_REVIEW_OBSERVATION_RECORDED":
            if payload.get("event_id") != event_id:
                continue
            stored = workspace.store.load_artifact(payload["observation_ref"], _REVIEW_MEDIA)
            row = stored.payload
            if (
                stored.payload_digest != payload.get("observation_digest")
                or row.get("event_id") != event_id
                or row.get("preview_digest") != preview_digest
                or row.get("measurement") != "CLIENT_REPORTED_ACTIVE_REVIEW_TIME"
            ):
                raise IntegrityError("FINANCE_USE_REVIEW_BINDING_INVALID")
            reviews.append({
                "observation_ref": stored.artifact_id,
                "observation_digest": stored.payload_digest,
                "actor_id": row["actor_id"], "action": row["action"],
                "outcome": row["outcome"], "evidence_class": "CLIENT_REPORTED_ONLY",
            })
        elif event["event_type"] == "WORKSPACE_CHANGE_APPROVED":
            approval_seen |= payload.get("kind") == event_id
        elif event["event_type"] == "WORKSPACE_CHANGE_OUTCOME_RECORDED":
            outcome_seen |= payload.get("kind") == event_id
    if len(reviews) > 50:
        raise IntegrityError("FINANCE_USE_REVIEW_OBSERVATIONS_UNBOUNDED")
    return {
        "review_observations": reviews,
        "review_coverage": "CLIENT_REPORTED_ONLY" if reviews else "NONE_OBSERVED",
        "approval_after_use": approval_seen,
        "outcome_after_use": outcome_seen,
        "business_effect_status": "INSUFFICIENT_EVIDENCE",
        "attribution": "AFTER_USE_NOT_CAUSAL",
    }


def read_finance_use_projection(
    workspace: Any, event_id: str, *, attempt_key: str | None = None,
) -> dict[str, Any]:
    """Project a historical Finance use without reselecting, sending or writing."""
    def finish(value: dict[str, Any]) -> dict[str, Any]:
        _reader(workspace)
        return value

    _reader(workspace)
    history = _selection(workspace, event_id)
    if not history:
        return finish({"status": "NO_ADOPTED_SELECTION", "event_id": event_id,
                       "target_writes": 0, "business_effect_status": "INSUFFICIENT_EVIDENCE"})
    matches = [(round_number, value) for round_number, value in history
               if attempt_key is None or value.attempt_key == attempt_key]
    if not matches:
        raise IntegrityError("FINANCE_USE_ATTEMPT_NOT_FOUND")
    round_number, selection = matches[-1]
    preview_artifact_id = (
        f"workspace-preview:{event_id}@recovery-{round_number}"
        if round_number else f"workspace-preview:{event_id}@r1"
    )
    base = {
        "schema_version": "orgrebase.finance-use-delivery-projection.v1",
        "status": "USE_UNKNOWN", "event_id": event_id,
        "selection_ref": selection.ref, "selection_digest": selection.digest,
        "use_ref": use_ref(selection), "execution_mode": "ADOPTED",
        "case_cluster_digest": selection.cluster_digest,
        "attempt_key": selection.attempt_key,
        "recovery_round": round_number,
        "attempt_history": [{"recovery_round": number, "attempt_key": value.attempt_key,
                             "selection_ref": value.ref, "selection_digest": value.digest}
                            for number, value in history],
        "head_ref": selection.head_ref, "package_digest": selection.package_digest,
        "target_writes": 0, "business_effect_status": "INSUFFICIENT_EVIDENCE",
    }
    try:
        stored_use = workspace.store.load_artifact(use_ref(selection), USE_MEDIA)
    except KeyError:
        return finish(base)
    saved = stored_use.payload
    if (
        saved.get("selection_ref") != selection.ref
        or saved.get("selection_digest") != selection.digest
        or saved.get("execution_mode") != "ADOPTED"
        or saved.get("target_writes") != 0
    ):
        raise IntegrityError("FINANCE_USE_SELECTION_BINDING_INVALID")
    if saved.get("finance_receipt_digest") is None:
        if saved.get("status") not in {"FAILED", "RESULT_UNKNOWN", "IN_PROGRESS"}:
            raise IntegrityError("FINANCE_USE_FAILURE_STATUS_INVALID")
        return finish({**base, "status": saved["status"], "resource_delivery": None,
                       "feedback": None, "provider_observation": "NOT_CONFIRMED"})
    from orgrebase.workspace.service import WORKSPACE_PREVIEW_MEDIA_TYPE
    try:
        stored_preview = workspace.store.load_artifact(
            preview_artifact_id, WORKSPACE_PREVIEW_MEDIA_TYPE,
        )
    except KeyError as error:
        raise IntegrityError("FINANCE_USE_PREVIEW_MISSING") from error
    bundle = WorkspacePreviewBundle.model_validate(stored_preview.payload)
    if (
        bundle.preview.digest != selection.preview_digest
        or bundle.change_set.digest != selection.change_set_digest
        or bundle.run_envelope.run_id != selection.run_id
        or bundle.run_envelope.nonce != selection.run_nonce
    ):
        raise IntegrityError("FINANCE_USE_PREVIEW_SELECTION_MISMATCH")
    expected = use_payload(workspace, selection, bundle.advisory)
    if saved != expected or stored_use.payload_digest != sha256_digest(expected):
        raise IntegrityError("FINANCE_USE_SAVED_RESULT_CHANGED")
    request, receipt = _finance_handoff(bundle, selection)
    advice = request.advice_context
    if (
        request.digest != selection.finance_request_digest
        or request.tenant_id != selection.tenant_id
        or request.workspace_id != selection.workspace_id
        or request.run_id != selection.run_id
        or request.nonce != selection.run_nonce
        or request.task_ref != selection.task_id
        or advice.operation_id != selection.attempt_key
        or advice.case_revision != selection.case_revision
        or advice.execution_mode != "ADOPTED"
        or receipt.status != "VALID"
        or receipt.dispatch_state != "RESPONSE_RECEIVED"
        or receipt.request_digest != request.digest
        or receipt.provider_request_id != saved["provider_request_id"]
    ):
        raise IntegrityError("FINANCE_USE_V4_BINDING_INVALID")
    candidate = DomainAdvisoryCandidate.model_validate(receipt.value)
    if (
        candidate.domain_id != "finance"
        or sorted(candidate.object_ids) != sorted(request.object_ids)
        or sorted(candidate.source_refs) != sorted(
            item.ref for item in request.business_input_projections
        )
        or sha256_digest(receipt.value) != saved["candidate_digest"]
    ):
        raise IntegrityError("FINANCE_USE_CANDIDATE_SCOPE_INVALID")
    delivery = _resource_delivery(workspace, selection, request, receipt)
    feedback = _feedback(workspace, event_id, selection.preview_digest, preview_artifact_id)
    return finish({
        **base,
        "status": "PROVIDER_RESPONSE_RECEIVED",
        "finance_request_digest": request.digest,
        "finance_receipt_digest": receipt.digest,
        "candidate_digest": saved["candidate_digest"],
        "provider_request_id": receipt.provider_request_id,
        "token_usage_status": saved["usage_status"],
        "resource_delivery": delivery,
        "feedback": feedback,
        "claim_ceiling": "WIRE_DELIVERY_AND_CANDIDATE_ONLY_NOT_MODEL_ATTENTION_OR_EFFECT",
    })


__all__ = ("read_finance_use_projection",)
