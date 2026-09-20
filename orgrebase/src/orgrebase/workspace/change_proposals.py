"""Translate business edits into the existing immutable change command."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import (
    PRINCIPAL_IDENTITY_MODES,
    AuthenticationError,
    authorize,
    current_authorization,
    request_principal,
)
from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace.pricing import PricingPolicy, QuoteBasket
from orgrebase.workspace.read_dependencies import ReadDependencies

SUBMISSION_MEDIA_TYPE = "application/vnd.orgrebase.change-submission+json"


class ChangeProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    slot_id: str = Field(min_length=1, max_length=128)
    base_version: str = Field(min_length=1, max_length=256)
    base_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    value: str | dict[str, Any]
    source_ref: str = Field(min_length=1, max_length=1024)
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    operation: Literal["UPDATE", "READMIT"] = "UPDATE"
    revises_event_id: str | None = Field(default=None, min_length=1, max_length=160)
    read_dependencies: ReadDependencies | None = None

    @model_validator(mode="after")
    def validate_value(self) -> ChangeProposalInput:
        if self.slot_id in {"quote_basket", "pricing_policy"}:
            if not isinstance(self.value, dict):
                raise ValueError("CHANGE_PROPOSAL_STRUCTURED_VALUE_REQUIRED")
            raw = json.dumps(self.value, ensure_ascii=False, allow_nan=False)
            if len(raw.encode("utf-8")) > 65_536:
                raise ValueError("CHANGE_PROPOSAL_VALUE_TOO_LARGE")
            model = QuoteBasket if self.slot_id == "quote_basket" else PricingPolicy
            model.model_validate_json(raw)
        elif not isinstance(self.value, str) or not 1 <= len(self.value) <= 2000:
            raise ValueError("CHANGE_PROPOSAL_STRING_VALUE_REQUIRED")
        return self


def require_action(workspace: Any, action: str) -> str:
    check = current_authorization()
    if check is not None:
        check()
    principal = request_principal.get()
    if principal is not None:
        authorize(principal, action, workspace.profile.organization_id)
        return principal.actor_id
    if workspace.approval_identity_mode in PRINCIPAL_IDENTITY_MODES:
        raise AuthenticationError("AUTH_BEARER_REQUIRED")
    return "operator:controlled-local"


def _can(workspace: Any, action: str) -> bool:
    try:
        require_action(workspace, action)
    except AuthenticationError:
        return False
    return True


def _submission_id(event_id: str) -> str:
    return f"workspace-change-submission:{event_id}@r1"


def submission(workspace: Any, event_id: str) -> dict[str, Any] | None:
    try:
        return workspace.store.load_artifact(_submission_id(event_id), SUBMISSION_MEDIA_TYPE).payload
    except KeyError:
        return None


def _source_operations(workspace: Any, slot_id: str, current: VersionedObject) -> tuple[list[str], str | None]:
    if current.valid_to is not None and utc_datetime(current.valid_to) <= utc_datetime(workspace.clock.now()):
        return [], "SOURCE_VALIDITY_EXPIRED"
    if current.state in {ObjectState.CURRENT, ObjectState.ACTIVE}:
        operations = ["UPDATE"]
    elif current.state in {ObjectState.STALE, ObjectState.QUARANTINED}:
        operations = ["READMIT"]
    else:
        operations = []
    if (workspace.domain_pack.template_ref == "template:enterprise_quote@v2"
            and slot_id == "currency" and operations == ["UPDATE"]):
        # Currency and basket must move together; the current command
        # applies one slot. Same-value source readmission remains valid.
        return [], "PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE"
    return operations, None


def change_options(workspace: Any) -> dict[str, Any]:
    require_action(workspace, "read")
    labels = {"launch_date": "上线日期", "currency": "报价币种", "product_plan": "产品方案",
              "pricing_policy": "折扣与税率", "quote_basket": "报价明细"}
    value_kinds = {"launch_date": "date", "pricing_policy": "pricing_policy", "quote_basket": "json"}
    fields = []
    with workspace._command_lock, workspace.store.read_snapshot():
        for binding in workspace.enterprise_binding.resources:
            if binding.slot_id not in workspace.domain_pack.mutable_slots:
                continue
            current = workspace.store.get_object(binding.object_id)
            operations, blocked_reason = _source_operations(workspace, binding.slot_id, current)
            fields.append({
                "slot_id": binding.slot_id,
                "domain_id": binding.domain_id,
                "label": labels.get(binding.slot_id, current.label),
                "value_kind": value_kinds.get(binding.slot_id, "text"),
                "current": {"version": current.version, "digest": current.digest,
                            "value": current.payload.get("canonical_value"), "state": current.state.value},
                "owner_id": binding.owner_id,
                "source_refs": list(current.source_refs),
                "allowed_operations": operations if _can(workspace, "propose") else [],
                "blocked_reason": blocked_reason,
            })
        try:
            workspace.current_graph_pointer()
        except KeyError:
            snapshot_digest = None
        else:
            snapshot_digest = workspace.current_snapshot().digest
        return {
            "execution_run_id": workspace.effective_workflow_run_id,
            "enterprise_binding_digest": workspace.enterprise_binding.digest,
            "snapshot_digest": snapshot_digest,
            "fields": fields,
        }


def submit_change(workspace: Any, request: ChangeProposalInput) -> dict[str, Any]:
    request = ChangeProposalInput.model_validate(request.model_dump())
    actor_id = require_action(workspace, "propose")
    if (isinstance(request.value, str) and not request.value.strip()) or not request.source_ref.strip() or any(
        ord(character) < 32 for character in request.source_ref
    ):
        raise IntegrityError("CHANGE_PROPOSAL_VALUE_AND_SOURCE_REQUIRED")
    command = request.model_dump(mode="json")
    if request.read_dependencies is None:
        command.pop("read_dependencies")
    command_digest = sha256_digest({"command": command, "actor_id": actor_id})
    with workspace._command_lock:
        with workspace.store.transaction() as connection:
            from orgrebase.workspace.enterprise_binding import lock_binding_scope
            lock_binding_scope(workspace, connection)
            require_action(workspace, "propose")
            prior = submission(workspace, request.event_id)
            if prior is not None:
                if prior["command_digest"] != command_digest:
                    raise IntegrityError("CHANGE_EVENT_ID_CONFLICT")
                event = workspace.changes.get(request.event_id)
                if event.digest != prior["event_digest"]:
                    raise IntegrityError("CHANGE_SUBMISSION_BINDING_INVALID")
            else:
                binding = next((item for item in workspace.enterprise_binding.resources
                                if item.slot_id == request.slot_id), None)
                if binding is None:
                    raise IntegrityError("CHANGE_EVENT_SLOT_UNSUPPORTED")
                current = workspace.store.get_object(binding.object_id)
                if (current.version, current.digest) != (request.base_version, request.base_digest):
                    raise FreshnessError("CHANGE_EVENT_BASE_STALE")
                if current.valid_to is not None and utc_datetime(current.valid_to) <= utc_datetime(workspace.clock.now()):
                    raise FreshnessError("SOURCE_VALIDITY_EXPIRED")
                if request.revises_event_id is not None:
                    previous = workspace.changes.get(request.revises_event_id)
                    if previous.slot_id != request.slot_id or previous.event_id == request.event_id:
                        raise IntegrityError("CHANGE_REVISION_SCOPE_MISMATCH")
                    if workspace._change_status(previous.event_id) not in {"REJECTED", "STALE", "EXPIRED"}:
                        raise IntegrityError("CHANGE_REVISION_REQUIRES_CLOSED_PROPOSAL")
                identity = sha256_digest({"run": workspace.effective_workflow_run_id,
                                          "event": request.event_id})[7:31]
                proposed_payload = {**current.payload, "canonical_value": request.value,
                    **({"read_dependencies": command["read_dependencies"]} if request.read_dependencies is not None else {})}
                # A human-supplied source replaces the connector observation for
                # this value; the old observation remains on its original version.
                proposed_payload.pop("source_observation_ref", None)
                proposed = VersionedObject.model_validate({
                    **current.model_dump(mode="json", exclude={"digest"}),
                    "version": f"proposal-{identity}", "state": ObjectState.PROPOSED,
                    "payload": proposed_payload,
                    "source_refs": [request.source_ref],
                    "valid_from": workspace.clock.now(),
                })
                event = ChangeEvent(
                    event_id=request.event_id, organization_id=workspace.profile.organization_id,
                    slot_id=request.slot_id, owner_id=binding.owner_id,
                    base_version=current.version, base_digest=current.digest, proposal=proposed,
                    occurred_at=workspace.clock.now(), operation=request.operation,
                )
                workspace.register_change(event, connection=connection)
                receipt = {"command": command, "command_digest": command_digest,
                           "event_digest": event.digest, "actor_id": actor_id,
                           "submitted_at": workspace.clock.now(),
                           "execution_run_id": workspace.effective_workflow_run_id}
                workspace.store.save_artifact(connection, _submission_id(event.event_id),
                                              SUBMISSION_MEDIA_TYPE, receipt)
                workspace.store.append_event(connection, "WORKSPACE_CHANGE_SUBMITTED", {
                    "event_id": event.event_id, "event_digest": event.digest,
                    "submission_digest": sha256_digest(receipt),
                    "revises_event_id": request.revises_event_id,
                })
        workspace.changes.refresh()
        return {"execution_run_id": workspace.effective_workflow_run_id,
                "event_id": event.event_id, "event_digest": event.digest,
                "event": event.model_dump(mode="json")}


def change_detail(workspace: Any, event_id: str) -> dict[str, Any]:
    from orgrebase.workspace.approval_authority import authority_detail, require_approval_actor
    from orgrebase.workspace.change_recovery import attempt_command, recovery_detail
    from orgrebase.workspace.preview_execution import read_attempt_summary

    require_action(workspace, "read")
    with workspace._command_lock:
        workspace.changes.refresh()
        event = workspace.changes.get(event_id)
        status, status_reason = workspace._change_status_with_reason(event_id)
        from orgrebase.workspace.runtime_revision import preview_runtime_status
        from orgrebase.workspace.source_readmission import membership
        group = membership(workspace, event_id)
        principal = request_principal.get()
        owner = principal is None
        if principal is not None:
            try:
                require_approval_actor(workspace, event_id, principal.actor_id)
                owner = True
            except (AuthenticationError, AuthorizationError, FreshnessError):
                owner = False
        actions = []
        if status in {"RECEIVED", "PREVIEWED", "APPROVED"} and _can(workspace, "propose"):
            actions.append("PREVIEW")
        if status in {"REJECTED", "STALE", "EXPIRED"} and _can(workspace, "propose"):
            binding = next((item for item in workspace.enterprise_binding.resources if item.slot_id == event.slot_id), None)
            if binding is not None and event.slot_id in workspace.domain_pack.mutable_slots:
                operations, _ = _source_operations(workspace, event.slot_id, workspace.store.get_object(binding.object_id))
                if operations:
                    actions.append("REVISE")
        if group is None and status not in {"REJECTED", "APPLIED", "RECOVERY_REQUIRED"} and owner and _can(workspace, "approve"):
            actions.append("REJECT")
        if status == "PREVIEWED" and owner and _can(workspace, "approve"):
            actions.append("APPROVE")
        if status in {"APPROVED", "RECOVERY_REQUIRED"} and _can(workspace, "execute"):
            actions.append("APPLY")
        authority = authority_detail(workspace, event_id)
        if status != "RECOVERY_REQUIRED" and principal is not None and principal.actor_id == event.owner_id and _can(workspace, "approve"):
            if authority["delegation"] is None and status in {"RECEIVED", "PREVIEWED", "GROUP_REVIEW"}:
                actions.append("DELEGATE")
            if authority["delegation"] is not None and authority["revocation"] is None:
                actions.append("REVOKE_DELEGATION")
        if (principal is not None and authority["escalation"] is None and _can(workspace, "propose")
                and status in {"RECEIVED", "PREVIEWED", "APPROVED", "EXPIRED"}):
            actions.append("ESCALATE")
        record = submission(workspace, event_id)
        approval = workspace._approval_record(event_id)
        preview = workspace._preview_record(event_id)
        runtime = preview_runtime_status(workspace, event_id, preview["preview_digest"],
                                         historical=status in {"APPLIED", "RECOVERY_REQUIRED"}) if preview else None
        return {
            "execution_run_id": workspace.effective_workflow_run_id,
            "source_group_id": group["group_id"] if group else None,
            "runtime_compatibility": runtime,
            "event": event.model_dump(mode="json"), "status": status,
            "status_reason": status_reason,
            "rejection": workspace.changes.rejection(event_id),
            "revises_event_id": record["command"].get("revises_event_id") if record else None,
            "reason": record["command"].get("reason") if record else None,
            "authority": authority, "active_owner_id": authority["active_owner_id"],
            "allowed_actions": actions,
            "preview": preview,
            "advisory_attempt": read_attempt_summary(workspace, command=attempt_command(workspace, event_id)),
            "recovery": recovery_detail(workspace, event_id, observed=(status, preview)),
            "approval": approval,
            "approval_expiry": approval["approval"]["expires_at"] if approval else None,
            "outcome": workspace._outcome_record(event_id),
        }


class ReviewObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    observation_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    preview_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    active_ms: int = Field(ge=0, le=86_400_000)
    action: Literal["PREVIEW", "APPROVE", "APPLY", "REJECT", "REVISE"]
    outcome: Literal["SUCCESS", "ERROR", "CANCELLED"]
    reason_code: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Z0-9_:-]+$")


def record_review(workspace: Any, event_id: str, request: ReviewObservationInput) -> dict[str, Any]:
    request = ReviewObservationInput.model_validate(request.model_dump())
    actor_id = require_action(workspace, "read")
    with workspace._command_lock, workspace.store.transaction() as connection:
        require_action(workspace, "read")
        event = workspace.changes.get(event_id)
        preview = workspace._preview_record(event_id)
        if preview is None or preview["bundle"]["preview"]["digest"] != request.preview_digest:
            raise IntegrityError("REVIEW_OBSERVATION_PREVIEW_MISMATCH")
        payload = {**request.model_dump(mode="json"), "event_id": event_id,
                   "event_digest": event.digest, "actor_id": actor_id,
                   "execution_run_id": workspace.effective_workflow_run_id,
                   "measurement": "CLIENT_REPORTED_ACTIVE_REVIEW_TIME"}
        identity = sha256_digest({"actor": actor_id, "id": request.observation_id})[7:]
        artifact_id = f"workspace-review-observation:{identity}@r1"
        try:
            previous = workspace.store.load_artifact(artifact_id, "application/vnd.orgrebase.review-observation+json")
        except KeyError:
            previous = None
        digest = workspace.store.save_artifact(
            connection, artifact_id,
            "application/vnd.orgrebase.review-observation+json", payload,
        )
        if previous is None:
            workspace.store.append_event(connection, "WORKSPACE_REVIEW_OBSERVATION_RECORDED", {
                "event_id": event_id, "observation_ref": artifact_id, "observation_digest": digest,
                "received_at": workspace.clock.now(), "active_ms": request.active_ms,
                "action": request.action, "outcome": request.outcome,
                "measurement": payload["measurement"],
            })
        return {"observation_digest": digest, "measurement": payload["measurement"],
                "canonical_writes": 0, "execution_run_id": workspace.effective_workflow_run_id}
