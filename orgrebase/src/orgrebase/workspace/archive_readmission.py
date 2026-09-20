"""Verify one atomic recovery receipt without multiplying its Quote successor."""

from __future__ import annotations

from typing import Any

from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import RebaseApprovalSet, RebaseReceipt, VersionedObject
from orgrebase.wire import WIRE_SCHEME, protocol_digest
from orgrebase.workspace.approval_authority import approval_evidence_valid
from orgrebase.workspace.models import ChangeEvent, WorkspaceRebaseReceipt
from orgrebase.workspace.source_readmission import SourceReadmissionGroup


def _sealed(model, raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("digest"), str):
        raise ValueError("SOURCE_GROUP_ARCHIVE_DIGEST_MISSING")
    return model.model_validate(raw)


def group_archive_record(detail: dict[str, Any], *, run_id: str, quote_id: str,
                         event_metadata: list[dict[str, Any]]) -> dict[str, Any]:
    if detail.get("schema_version") != "orgrebase.source-readmission-group-detail.v1" or detail.get("state") != "APPLIED":
        raise ValueError("SOURCE_GROUP_ARCHIVE_STATE_INVALID")
    group = _sealed(SourceReadmissionGroup, detail["group"])
    outcome = detail["outcome"]
    approval_set = _sealed(RebaseApprovalSet, outcome["approval_set"])
    receipt = _sealed(RebaseReceipt, outcome["rebase_receipt"])
    workspace_receipt = _sealed(WorkspaceRebaseReceipt, outcome["workspace_rebase_receipt"])
    quote = _sealed(VersionedObject, outcome["quote"])
    pointer = _sealed(VersionedObject, outcome["graph_pointer"])
    events = tuple(_sealed(ChangeEvent, raw) for raw in detail["source_events"])
    metadata = {row["event_id"]: row for row in event_metadata}
    if (len(events) != len(group.events) or not 2 <= len(events) <= 3
            or len(group.source_specs) != len(events) or len(group.change_set.deltas) != len(events)
            or len({event.slot_id for event in events}) != len(events)
            or len({event.proposal.id for event in events}) != len(events)):
        raise ValueError("SOURCE_GROUP_ARCHIVE_MEMBERS_INVALID")
    for ref, event, spec in zip(group.events, events, group.source_specs, strict=True):
        item = metadata.get(ref.event_id, {})
        if (ref.event_id != event.event_id or ref.event_digest != event.digest
                or event.operation != "READMIT" or spec.operation != "READMIT"
                or event.organization_id != group.tenant_id or event.owner_id != spec.owner_id
                or event.proposal.id != spec.object_id or event.base_version != spec.base_version
                or event.proposal.version != spec.proposed_version
                or item.get("status") != "GROUP_APPLIED" or item.get("source_group_id") != group.id
                or item.get("event_digest") != event.digest or item.get("owner_id") != event.owner_id
                or item.get("slot_id") != event.slot_id):
            raise ValueError("SOURCE_GROUP_ARCHIVE_EVENT_BINDING_INVALID")
    deltas = {delta.object_id: delta for delta in group.change_set.deltas}
    for event in events:
        delta = deltas.get(event.proposal.id)
        if (delta is None or delta.base_version != event.base_version
                or delta.proposed_version != event.proposal.version or delta.admitted_by != event.owner_id
                or delta.proposed_value != event.proposal.payload.get("canonical_value")):
            raise ValueError("SOURCE_GROUP_ARCHIVE_DELTA_BINDING_INVALID")
    owners = detail["owners"]
    expected_owners = {event.owner_id for event in events}
    if (len(owners) != len(expected_owners) or {item["owner_id"] for item in owners} != expected_owners
            or len(approval_set.members) != len(expected_owners)
            or {member.owner_id for member in approval_set.members} != expected_owners):
        raise ValueError("SOURCE_GROUP_ARCHIVE_APPROVAL_COVERAGE_INVALID")
    approvals = []
    for member in approval_set.members:
        owner = next(item for item in owners if item["owner_id"] == member.owner_id)
        owner_events = tuple(event for event in events if event.owner_id == member.owner_id)
        source_ids = tuple(sorted(event.proposal.id for event in owner_events))
        approval = member.approval
        review = owner["review_evidence"]
        if (tuple(member.source_ids) != source_ids or tuple(owner["source_ids"]) != source_ids
                or owner["source_approval"] != member.model_dump(mode="json")
                or approval.change_set_digest != group.change_set.digest
                or approval.preview_digest != group.preview.digest
                or approval.minimal_rebase_certificate_digest != group.minimal_rebase_certificate.digest
                or tuple(approval.authority_scope) != tuple(sorted((group.change_set.id, *source_ids)))
                or utc_datetime(approval.approved_at) >= utc_datetime(approval.expires_at)
                or review.get("group_digest") != group.digest or review.get("preview_digest") != group.preview.digest
                or review.get("review_wait_satisfied") is not True
                or review.get("review_not_before_epoch_ms") != group.review_not_before_epoch_ms
                or type(review.get("approved_at_epoch_ms")) is not int
                or review["approved_at_epoch_ms"] < group.review_not_before_epoch_ms
                or set(owner["authorities"]) != {event.event_id for event in owner_events}):
            raise ValueError("SOURCE_GROUP_ARCHIVE_APPROVAL_BINDING_INVALID")
        for event in owner_events:
            authority = owner["authorities"][event.event_id]
            if authority is None:
                if approval.actor_id != member.owner_id:
                    raise ValueError("SOURCE_GROUP_ARCHIVE_DELEGATION_MISSING")
            elif not approval_evidence_valid(authority, event_id=event.event_id, event_digest=event.digest,
                                             owner_id=event.owner_id, run_id=run_id,
                                             approval=approval.model_dump(mode="json")):
                raise ValueError("SOURCE_GROUP_ARCHIVE_DELEGATION_INVALID")
        approvals.append({"owner_id": member.owner_id, "actor_id": approval.actor_id, "source_ids": list(source_ids),
                          "source_approval_digest": member.digest, "approval_digest": approval.digest,
                          "authorities": owner["authorities"], "review_evidence": review})
    transitions = [value for value in receipt.transitions if value.get("object_id") == quote_id]
    claims = {(item["object_id"], item["from"], item["to"]) for item in receipt.applied_claims}
    expected_claims = {(event.proposal.id, event.base_version, event.proposal.version) for event in events}
    change_set_ref = f"{group.change_set.id}@{group.change_set.revision}"
    if (group.execution_run_id != run_id or group.run_envelope.run_id != run_id or quote.id != quote_id
            or group.predecessor_ref.rsplit("@", 1)[0] != quote_id or outcome["group_id"] != group.id
            or outcome["group_digest"] != group.digest or receipt.status != "COMPLETED"
            or receipt.workflow_run_id != run_id or receipt.run_nonce != group.run_envelope.nonce
            or receipt.approval_set != approval_set or receipt.approval_digest != approval_set.digest
            or receipt.change_set_ref != change_set_ref or receipt.preview_ref != group.preview.id
            or receipt.minimal_rebase_certificate_digest != group.minimal_rebase_certificate.digest
            or receipt.revision_lock != group.preview.revision_lock
            or group.preview.change_set_ref != change_set_ref
            or group.minimal_rebase_certificate.change_set_digest != group.change_set.digest
            or group.minimal_rebase_certificate.preview_digest != group.preview.digest
            or workspace_receipt.status != "COMPLETED" or workspace_receipt.base_rebase_receipt_digest != receipt.digest
            or workspace_receipt.base_rebase_receipt_ref != receipt.id
            or quote.ref not in workspace_receipt.successor_object_refs
            or workspace_receipt.graph_pointer_ref != pointer.ref
            or pointer.payload.get("snapshot_digest") != workspace_receipt.successor_snapshot_digest
            or pointer.payload.get("snapshot_ref") != workspace_receipt.successor_snapshot_ref
            or claims != expected_claims or len(receipt.applied_claims) != len(expected_claims)
            or len(transitions) != 1 or transitions[0]["from"] != group.predecessor_ref
            or transitions[0]["to"] != quote.ref or transitions[0]["to_state"] != "CURRENT"):
        raise ValueError("SOURCE_GROUP_ARCHIVE_OUTCOME_BINDING_INVALID")
    proof = {key: detail[key] for key in ("schema_version", "group", "source_events", "owners", "outcome")}
    return {"kind": "source_readmission_group", "group_id": group.id, "group_digest": group.digest,
            "event_ids": [event.event_id for event in events], "owner_approvals": approvals,
            "preview_digest": group.preview.digest, "approval_digest": approval_set.digest,
            "rebase_receipt_digest": receipt.digest, "workspace_receipt_digest": workspace_receipt.digest,
            "predecessor_quote_ref": group.predecessor_ref, "successor_quote_version": quote.version,
            "successor_quote_digest": quote.digest, "human_approval_count": len(approvals),
            "group_evidence": proof, "group_evidence_digest": sha256_digest(proof),
            "group_evidence_wire": {"scheme": WIRE_SCHEME, "digest": protocol_digest(proof)}}
