"""Event-scoped owner delegation and durable approval provenance."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import (
    PRINCIPAL_IDENTITY_MODES,
    AuthenticationError,
    current_authorization,
    request_principal,
)
from orgrebase.clock import timestamp, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import Approval, AuthorizationError, FreshnessError, IntegrityError

_MEDIA = "application/vnd.orgrebase.workspace-authority+json"


class DelegationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    subject: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(min_length=1, max_length=256)
    event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    valid_seconds: int = Field(default=900, ge=30, le=3600)
    reason: str = Field(min_length=1, max_length=1000)


class CoordinationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=1000)


def _key(kind: str, event_id: str) -> str:
    return f"workspace-authority-{kind}:{event_id}@r1"


def _read(workspace: Any, kind: str, event_id: str, *,
          records: Mapping[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    if records is not None:
        return records.get(_key(kind, event_id))
    try:
        return workspace.store.load_artifact(_key(kind, event_id), _MEDIA).payload
    except KeyError:
        return None


def authority_records(workspace: Any, event_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Load only the authority records for one bounded history page."""
    stored = workspace.store.load_artifacts(
        [_key(kind, event_id) for event_id in event_ids
         for kind in ("delegation", "revocation", "escalation")], expected_media_type=_MEDIA,
    )
    return {key: artifact.payload for key, artifact in stored.items()}


def _save(workspace: Any, connection: Any, kind: str, event_id: str, value: dict[str, Any]) -> str:
    return workspace.store.save_artifact(connection, _key(kind, event_id), _MEDIA, value)


def identity_for_action(workspace: Any, action: str) -> dict[str, str]:
    from orgrebase.workspace.change_proposals import require_action

    actor = require_action(workspace, action)
    principal = request_principal.get()
    if principal is None:
        raise AuthenticationError("AUTH_VERIFIED_PRINCIPAL_REQUIRED")
    return {"issuer": principal.issuer, "subject": principal.subject, "actor_id": actor}


def verify_member(workspace: Any, identity: dict[str, str], action: str) -> None:
    verifier = getattr(workspace, "verify_membership", None)
    if verifier is None or identity["issuer"] != getattr(workspace, "identity_issuer", None):
        raise AuthenticationError("AUTH_MEMBER_VERIFICATION_UNAVAILABLE", 503)
    verifier(identity["subject"], identity["actor_id"], action)
    check_scope = getattr(workspace, "authorize_workspace_subject", None)
    if check_scope is not None:
        check_scope(identity["subject"])


def _event(workspace: Any, event_id: str, event_digest: str):
    event = workspace.changes.get(event_id)
    if event.digest != event_digest:
        raise IntegrityError("AUTHORITY_EVENT_DIGEST_MISMATCH")
    return event


def _validate_grant(workspace: Any, event_id: str, grant: dict[str, Any], *, at: str,
                    current: bool, records: Mapping[str, dict[str, Any]] | None = None) -> None:
    event = _event(workspace, event_id, grant["event_digest"])
    if (grant["owner"]["actor_id"] != event.owner_id
            or grant["tenant_id"] != workspace.profile.organization_id
            or grant["workspace_id"] != workspace.store.workspace_id
            or grant["execution_run_id"] != workspace.effective_workflow_run_id):
        raise AuthorizationError("DELEGATION_SCOPE_MISMATCH")
    moment = utc_datetime(at)
    if not utc_datetime(grant["issued_at"]) <= moment < utc_datetime(grant["expires_at"]):
        raise AuthorizationError("DELEGATION_EXPIRED")
    revoked = _read(workspace, "revocation", event_id, records=records)
    if revoked is not None:
        if revoked["grant_digest"] != sha256_digest(grant):
            raise IntegrityError("DELEGATION_REVOCATION_BINDING_INVALID")
        if current:
            raise AuthorizationError("DELEGATION_REVOKED")
    if current:
        from orgrebase.workspace.enterprise_binding import require_change_owner
        require_change_owner(workspace, event)
        verify_member(workspace, grant["owner"], "approve")
        verify_member(workspace, grant["delegate"], "approve")


def grant_delegation(workspace: Any, event_id: str, request: DelegationInput) -> dict[str, Any]:
    request = DelegationInput.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope, require_change_owner
        lock_binding_scope(workspace, connection)
        owner = identity_for_action(workspace, "approve")
        event = _event(workspace, event_id, request.event_digest)
        require_change_owner(workspace, event)
        if owner["actor_id"] != event.owner_id:
            raise AuthorizationError("DELEGATION_ORIGINAL_OWNER_REQUIRED")
        if not request.reason.strip() or request.actor_id == event.owner_id:
            raise IntegrityError("DELEGATION_RECIPIENT_OR_REASON_INVALID")
        delegate = {"issuer": owner["issuer"], "subject": request.subject, "actor_id": request.actor_id}
        verify_member(workspace, owner, "approve")
        verify_member(workspace, delegate, "approve")
        prior = _read(workspace, "delegation", event_id)
        command_digest = sha256_digest({"request": request.model_dump(), "owner": owner})
        if prior is not None:
            if prior["command_digest"] != command_digest:
                raise IntegrityError("DELEGATION_IMMUTABLE_REVISE_PROPOSAL")
            return authority_detail(workspace, event_id)
        if workspace._change_status(event_id) not in {"RECEIVED", "PREVIEWED", "GROUP_REVIEW"}:
            raise IntegrityError("DELEGATION_REQUIRES_UNAPPROVED_PROPOSAL")
        now = utc_datetime(workspace.clock.now())
        value = {"event_id": event_id, "event_digest": event.digest,
                 "tenant_id": workspace.profile.organization_id, "workspace_id": workspace.store.workspace_id,
                 "execution_run_id": workspace.effective_workflow_run_id,
                 "owner": owner, "delegate": delegate, "reason": request.reason,
                 "command_digest": command_digest, "actions": ["APPROVE", "REJECT"],
                 "issued_at": timestamp(now), "expires_at": timestamp(now + timedelta(seconds=request.valid_seconds))}
        digest = _save(workspace, connection, "delegation", event_id, value)
        workspace.store.append_event(connection, "WORKSPACE_APPROVAL_DELEGATED", {
            "event_id": event_id, "grant_digest": digest, "owner_id": event.owner_id,
            "delegate_id": delegate["actor_id"], "expires_at": value["expires_at"],
        })
    return authority_detail(workspace, event_id)


def revoke_delegation(workspace: Any, event_id: str, request: CoordinationInput) -> dict[str, Any]:
    request = CoordinationInput.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        owner = identity_for_action(workspace, "approve")
        event = _event(workspace, event_id, request.event_digest)
        if owner["actor_id"] != event.owner_id:
            raise AuthorizationError("DELEGATION_ORIGINAL_OWNER_REQUIRED")
        if not request.reason.strip():
            raise IntegrityError("DELEGATION_REASON_REQUIRED")
        grant = _read(workspace, "delegation", event_id)
        if grant is None:
            raise KeyError(event_id)
        if _read(workspace, "revocation", event_id) is None:
            value = {"grant_digest": sha256_digest(grant), "owner": owner,
                     "reason": request.reason, "revoked_at": workspace.clock.now()}
            _save(workspace, connection, "revocation", event_id, value)
            workspace.store.append_event(connection, "WORKSPACE_DELEGATION_REVOKED", {"event_id": event_id, **value})
    return authority_detail(workspace, event_id)


def escalate_change(workspace: Any, event_id: str, request: CoordinationInput) -> dict[str, Any]:
    request = CoordinationInput.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        requester = identity_for_action(workspace, "propose")
        event = _event(workspace, event_id, request.event_digest)
        if not request.reason.strip():
            raise IntegrityError("ESCALATION_REASON_REQUIRED")
        if _read(workspace, "escalation", event_id) is None:
            if workspace._change_status(event_id) not in {"RECEIVED", "PREVIEWED", "APPROVED", "EXPIRED", "GROUP_REVIEW"}:
                raise IntegrityError("ESCALATION_REQUIRES_OPEN_WORK")
            value = {"event_id": event_id, "event_digest": event.digest, "owner_id": event.owner_id,
                     "requested_by": requester, "reason": request.reason, "requested_at": workspace.clock.now(),
                     "status": "COORDINATION_REQUESTED", "authority_granted": False}
            _save(workspace, connection, "escalation", event_id, value)
            workspace.store.append_event(connection, "WORKSPACE_COORDINATION_REQUESTED", value)
    return authority_detail(workspace, event_id)


def authority_detail(workspace: Any, event_id: str, *, include_eligible_delegates: bool = True,
                     records: Mapping[str, dict[str, Any]] | None = None,
                     observed_at: str | None = None) -> dict[str, Any]:
    from orgrebase.workspace.change_proposals import _can, require_action

    require_action(workspace, "read")
    event = workspace.changes.get(event_id)
    grant = _read(workspace, "delegation", event_id, records=records)
    active, reason = False, None
    from orgrebase.workspace.enterprise_binding import require_change_owner
    try:
        require_change_owner(workspace, event)
    except FreshnessError as error:
        reason = str(error)
    if grant is not None and reason is None:
        try:
            _validate_grant(workspace, event_id, grant, at=observed_at or workspace.clock.now(), current=True, records=records)
            active = True
        except (AuthenticationError, AuthorizationError, FreshnessError) as error:
            reason = str(error)
    eligible = []
    principal = request_principal.get()
    directory = getattr(workspace, "members_for_action", None)
    if include_eligible_delegates and reason is None and principal is not None and principal.actor_id == event.owner_id and directory is not None and _can(workspace, "approve"):
        for member in directory("approve"):
            if member["actor_id"] == event.owner_id:
                continue
            identity = {"issuer": principal.issuer, **member}
            try:
                verify_member(workspace, identity, "approve")
            except AuthenticationError as error:
                if error.status_code != 403:
                    raise
                continue
            eligible.append({**member, "label": member["actor_id"]})
    authority = {"owner_id": event.owner_id, "active_owner_id": grant["delegate"]["actor_id"] if active else event.owner_id,
                 "delegation": grant, "delegation_active": active, "blocked_reason": reason,
                 "revocation": _read(workspace, "revocation", event_id, records=records), "escalation": _read(workspace, "escalation", event_id, records=records)}
    revision = sha256_digest({"event_digest": event.digest, "execution_run_id": workspace.effective_workflow_run_id,
                              "authority": authority})
    return {**authority, "revision": revision, "eligible_delegates": eligible}


def require_approval_actor(workspace: Any, event_id: str, actor_id: str) -> dict[str, Any] | None:
    check = current_authorization()
    if check is not None:
        check()
    event = workspace.changes.get(event_id)
    from orgrebase.workspace.enterprise_binding import require_change_owner
    require_change_owner(workspace, event)
    principal = request_principal.get()
    if principal is not None:
        from orgrebase.workspace.change_proposals import require_action
        if require_action(workspace, "approve") != actor_id:
            raise AuthorizationError("WORKSPACE_APPROVER_IDENTITY_MISMATCH")
    elif workspace.approval_identity_mode in PRINCIPAL_IDENTITY_MODES:
        raise AuthenticationError("AUTH_VERIFIED_PRINCIPAL_REQUIRED")
    if actor_id == event.owner_id:
        return None
    grant = _read(workspace, "delegation", event_id)
    if grant is None or grant["delegate"]["actor_id"] != actor_id or principal is None:
        raise AuthorizationError("WORKSPACE_APPROVER_MISMATCH")
    if grant["delegate"] != {"issuer": principal.issuer, "subject": principal.subject, "actor_id": actor_id}:
        raise AuthorizationError("DELEGATION_IDENTITY_MISMATCH")
    _validate_grant(workspace, event_id, grant, at=workspace.clock.now(), current=True)
    return grant


def record_approval_authority(workspace: Any, event_id: str, approval: Approval, connection: Any) -> str | None:
    grant = require_approval_actor(workspace, event_id, approval.actor_id)
    principal = request_principal.get()
    if principal is None:
        return None
    identity = identity_for_action(workspace, "approve")
    value = {"approval_digest": approval.digest, "event_digest": workspace.changes.get(event_id).digest,
             "identity": identity, "grant_digest": sha256_digest(grant) if grant else None}
    return _save(workspace, connection, "approval", event_id, value)


def verify_approval_authority(workspace: Any, event_id: str, approval: Approval, *, current: bool,
                              observed_at: str | None = None) -> None:
    event = workspace.changes.get(event_id)
    if current:
        from orgrebase.workspace.enterprise_binding import require_change_owner
        require_change_owner(workspace, event)
    provenance = _read(workspace, "approval", event_id)
    if provenance is None:
        if approval.actor_id != event.owner_id or (current and workspace.approval_identity_mode in PRINCIPAL_IDENTITY_MODES):
            raise AuthorizationError("WORKSPACE_APPROVAL_PROVENANCE_REQUIRED")
        return
    if (provenance["approval_digest"] != approval.digest or provenance["event_digest"] != event.digest
            or provenance["identity"]["actor_id"] != approval.actor_id):
        raise IntegrityError("WORKSPACE_APPROVAL_PROVENANCE_MISMATCH")
    if current:
        verify_member(workspace, provenance["identity"], "approve")
    if approval.actor_id != event.owner_id:
        grant = _read(workspace, "delegation", event_id)
        if grant is None or sha256_digest(grant) != provenance["grant_digest"] or grant["delegate"] != provenance["identity"]:
            raise IntegrityError("WORKSPACE_APPROVAL_DELEGATION_MISMATCH")
        _validate_grant(workspace, event_id, grant, at=(observed_at or workspace.clock.now()) if current else approval.approved_at,
                        current=current)
    elif provenance["grant_digest"] is not None:
        raise IntegrityError("WORKSPACE_OWNER_APPROVAL_DELEGATION_UNEXPECTED")


def approval_authority_evidence(workspace: Any, event_id: str, approval: Approval) -> dict[str, Any] | None:
    provenance = _read(workspace, "approval", event_id)
    if provenance is None:
        return None
    verify_approval_authority(workspace, event_id, approval, current=False)
    grant = _read(workspace, "delegation", event_id) if provenance["grant_digest"] else None
    return {"schema_version": "orgrebase.workspace-approval-authority.v1",
            "provenance": provenance, "provenance_digest": sha256_digest(provenance),
            "grant": grant, "grant_digest": sha256_digest(grant) if grant else None,
            "evidence_class": "VERIFIED_EVENT_SCOPED_APPROVAL_AUTHORITY"}


def approval_evidence_valid(evidence: Any, *, event_id: str, event_digest: str | None,
                            owner_id: str, run_id: str, approval: Mapping[str, Any]) -> bool:
    """Check exported identity relationships without granting present authority."""
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "schema_version", "provenance", "provenance_digest", "grant", "grant_digest", "evidence_class",
    }:
        return False
    if (evidence["schema_version"] != "orgrebase.workspace-approval-authority.v1"
            or evidence["evidence_class"] != "VERIFIED_EVENT_SCOPED_APPROVAL_AUTHORITY"):
        return False
    provenance = evidence["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != {"approval_digest", "event_digest", "identity", "grant_digest"}:
        return False
    identity = provenance["identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"issuer", "subject", "actor_id"}:
        return False
    if (any(not isinstance(value, str) or not value for value in identity.values())
            or provenance["approval_digest"] != approval.get("digest")
            or provenance["event_digest"] != event_digest or event_digest is None
            or identity["actor_id"] != approval.get("actor_id")
            or evidence["provenance_digest"] != sha256_digest(provenance)):
        return False
    grant = evidence["grant"]
    if grant is None:
        return identity["actor_id"] == owner_id and provenance["grant_digest"] is None and evidence["grant_digest"] is None
    if not isinstance(grant, Mapping):
        return False
    owner = grant.get("owner")
    if not isinstance(owner, Mapping) or set(owner) != {"issuer", "subject", "actor_id"}:
        return False
    if (identity["actor_id"] == owner_id or owner.get("actor_id") != owner_id
            or owner.get("issuer") != identity["issuer"] or not isinstance(owner.get("subject"), str)
            or not owner["subject"] or grant.get("delegate") != identity
            or grant.get("event_id") != event_id or grant.get("event_digest") != event_digest
            or grant.get("execution_run_id") != run_id or grant.get("actions") != ["APPROVE", "REJECT"]
            or evidence["grant_digest"] != sha256_digest(grant) or provenance["grant_digest"] != evidence["grant_digest"]):
        return False
    try:
        issued, expires = utc_datetime(grant["issued_at"]), utc_datetime(grant["expires_at"])
        return issued <= utc_datetime(approval["approved_at"]) < expires and 0 < (expires - issued).total_seconds() <= 3600
    except (KeyError, TypeError, ValueError):
        return False
