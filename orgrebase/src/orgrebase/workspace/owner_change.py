"""Owner previews and explicitly enabled, jointly authorized succession."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, ContentAddressedModel, FreshnessError, IntegrityError
from orgrebase.impact import ImpactEngine
from orgrebase.workspace.approval_authority import identity_for_action, verify_member
from orgrebase.workspace.enterprise_binding import (
    activate_binding,
    binding_revision,
    current_binding,
    lock_binding_scope,
)
from orgrebase.workspace.models import EnterpriseBinding, EnterpriseResourceBinding

_PAGE_LIMIT = 100
_MIGRATION_MEDIA = "application/vnd.orgrebase.owner-migration+json"


class OwnerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issuer: str = Field(min_length=1, max_length=1024)
    subject: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(min_length=1, max_length=256)


class OwnerChangePreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    slot_id: str = Field(min_length=1, max_length=128)
    expected_binding_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    proposed_owner: OwnerIdentity
    reason: str = Field(min_length=1, max_length=1000)


class ReviewObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    digest: str
    label: str
    kind: str
    state: str


class PendingOwnerReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_digest: str
    source_object_id: str
    recorded_owner_id: str
    current_status: str
    required_review: Literal["REPLAN_AND_REAUTHORIZE_IF_MIGRATION_APPROVED"] = (
        "REPLAN_AND_REAUTHORIZE_IF_MIGRATION_APPROVED"
    )


class UnresolvedOwnerEffect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effect_id: str
    target_key: str
    request_digest: str
    state: Literal["READY", "DISPATCHING", "COMMIT_UNKNOWN"]
    association: Literal["WORKSPACE_SCOPE_UNCONFIRMED"] = "WORKSPACE_SCOPE_UNCONFIRMED"
    required_review: Literal["REVIEW_EXISTING_INTENT", "RECONCILE_ORIGINAL_EFFECT_NO_RESEND"]
    updated_at: str
    dispatch_authorized: Literal[False] = False


class ReviewCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["COMPLETE_WITHIN_RECORDED_SCOPE", "UNKNOWN"]
    inspected_count: int
    returned_count: int
    next_cursor: int | str | None = None
    reason: str


class OwnerChangePreview(ContentAddressedModel):
    schema_version: Literal["orgrebase.owner-change-preview.v1"] = "orgrebase.owner-change-preview.v1"
    organization_id: str
    workspace_id: str
    requester: OwnerIdentity
    request_digest: str
    evaluated_at: str
    reason: str
    binding_digest: str
    snapshot_ref: str
    snapshot_digest: str
    resource: EnterpriseResourceBinding
    source: ReviewObject
    proposed_owner: OwnerIdentity
    proposed_binding: EnterpriseBinding
    proposed_owner_in_declared_owner_refs: bool
    dependent_work: tuple[ReviewObject, ...]
    dependency_coverage: ReviewCoverage
    pending_events: tuple[PendingOwnerReview, ...]
    pending_event_coverage: ReviewCoverage
    unresolved_effects: tuple[UnresolvedOwnerEffect, ...]
    effect_coverage: ReviewCoverage
    activation_allowed: Literal[False] = False
    database_writes: Literal[0] = 0
    migration_policy_status: Literal["ORGANIZATION_AUTHORIZATION_POLICY_REQUIRED"] = (
        "ORGANIZATION_AUTHORIZATION_POLICY_REQUIRED"
    )


def _object_review(value: Any) -> ReviewObject:
    return ReviewObject(ref=value.ref, digest=value.digest, label=value.label,
                        kind=value.kind, state=value.state.value)


def preview_owner_change(workspace: Any, request: OwnerChangePreviewInput) -> OwnerChangePreview:
    """Inspect one hypothetical replacement; never persist or grant migration authority."""
    request = OwnerChangePreviewInput.model_validate(request.model_dump(mode="json"))
    with workspace._command_lock, workspace.store.read_snapshot():
        requester = identity_for_action(workspace, "propose")
        # Succession needs an independently verified workspace member, including
        # trusted local callers. A role alone is not workspace membership.
        if not callable(getattr(workspace, "authorize_workspace_subject", None)):
            raise AuthenticationError("AUTH_MEMBER_VERIFICATION_UNAVAILABLE", 503)
        verify_member(workspace, requester, "propose")
        verify_member(workspace, request.proposed_owner.model_dump(), "approve")

        binding = workspace.enterprise_binding.revalidated()
        persisted = current_binding(workspace)
        if persisted.digest != binding.digest or request.expected_binding_digest != binding.digest:
            raise IntegrityError("OWNER_CHANGE_BINDING_CHANGED")
        resource = next((item for item in binding.resources if item.slot_id == request.slot_id), None)
        if resource is None:
            raise IntegrityError("OWNER_CHANGE_RESOURCE_UNKNOWN")
        if resource.owner_id == request.proposed_owner.actor_id:
            raise IntegrityError("OWNER_CHANGE_OWNER_UNCHANGED")
        try:
            snapshot = workspace.current_snapshot().revalidated()
        except KeyError as exc:
            raise IntegrityError("OWNER_CHANGE_SNAPSHOT_REQUIRED") from exc
        pointer = workspace.current_graph_pointer()
        if (request.expected_snapshot_digest != snapshot.digest
                or pointer.payload["snapshot_digest"] != snapshot.digest
                or snapshot.organization_id != binding.organization_id):
            raise IntegrityError("OWNER_CHANGE_SNAPSHOT_CHANGED")
        source = workspace.store.get_object(resource.object_id)
        if not any(item.object_ref == source.ref and item.digest == source.digest
                   for item in snapshot.object_digests):
            raise IntegrityError("OWNER_CHANGE_SOURCE_CHANGED")

        fixture = workspace.snapshot_builder.to_enterprise_fixture(
            snapshot=snapshot, artifact_reader=workspace.store.load_artifact,
            object_reader=workspace.store.get_object, agents=workspace.legacy.agents,
            evaluation_cases=workspace.legacy.evaluation_cases,
            context_profiles=workspace.context_profiles, change={},
        )

        closure = ImpactEngine(fixture).dependency_closure((resource.object_id,))
        affected = set(closure["affected_refs"])
        objects = {item.ref: item for item in fixture.objects}
        work = tuple(objects[ref] for ref in snapshot.target_refs
                     if objects[ref].id in affected and objects[ref].id != resource.object_id)
        work_truncated = len(work) > _PAGE_LIMIT
        dependency_complete = closure["complete"] and not work_truncated

        page = workspace.changes.page(limit=_PAGE_LIMIT)
        pending = []
        for event in page["items"]:
            if event.proposal.id not in affected:
                continue
            status = workspace._change_status(event.event_id)
            if status in {"APPLIED", "GROUP_APPLIED", "REJECTED"}:
                continue
            pending.append(PendingOwnerReview(
                event_id=event.event_id, event_digest=event.digest,
                source_object_id=event.proposal.id, recorded_owner_id=event.owner_id,
                current_status=status,
            ))
        effects = workspace.store.list_effects(
            states=("READY", "DISPATCHING", "COMMIT_UNKNOWN"), limit=_PAGE_LIMIT,
        )
        proposed = EnterpriseBinding.model_validate({
            **binding.model_dump(mode="json", exclude={"digest"}),
            "resources": [
                {**item.model_dump(), "owner_id": request.proposed_owner.actor_id}
                if item.slot_id == resource.slot_id else item.model_dump()
                for item in binding.resources
            ],
        })
        return OwnerChangePreview(
            organization_id=binding.organization_id, workspace_id=workspace.store.workspace_id,
            requester=OwnerIdentity.model_validate(requester), request_digest=sha256_digest(request),
            evaluated_at=workspace.clock.now(), reason=request.reason,
            binding_digest=binding.digest, snapshot_ref=snapshot.ref, snapshot_digest=snapshot.digest,
            resource=resource, source=_object_review(source), proposed_owner=request.proposed_owner,
            proposed_binding=proposed,
            proposed_owner_in_declared_owner_refs=(
                request.proposed_owner.actor_id in workspace.profile.governance.owner_refs
            ),
            dependent_work=tuple(_object_review(item) for item in work[:_PAGE_LIMIT]),
            dependency_coverage=ReviewCoverage(
                status="COMPLETE_WITHIN_RECORDED_SCOPE" if dependency_complete else "UNKNOWN",
                inspected_count=len(snapshot.target_refs), returned_count=min(len(work), _PAGE_LIMIT),
                reason=("Recorded admitted dependencies only; no automatic invalidation or rebuild."
                        if dependency_complete else "Graph proof or output limit leaves dependencies unknown."),
            ),
            pending_events=tuple(pending),
            pending_event_coverage=ReviewCoverage(
                status=("COMPLETE_WITHIN_RECORDED_SCOPE"
                        if page["next_cursor"] is None and closure["complete"] else "UNKNOWN"),
                inspected_count=len(page["items"]), returned_count=len(pending),
                next_cursor=page["next_cursor"],
                reason="Only related events in the inspected journal page; existing authority is unchanged.",
            ),
            unresolved_effects=tuple(UnresolvedOwnerEffect(
                effect_id=item["effect_id"], target_key=item["target_key"],
                request_digest=item["request_digest"], state=item["state"], updated_at=item["updated_at"],
                required_review=("REVIEW_EXISTING_INTENT" if item["state"] == "READY"
                                 else "RECONCILE_ORIGINAL_EFFECT_NO_RESEND"),
            ) for item in effects["items"]),
            effect_coverage=ReviewCoverage(
                status="UNKNOWN" if effects["next_cursor"] is not None else "COMPLETE_WITHIN_RECORDED_SCOPE",
                inspected_count=len(effects["items"]), returned_count=len(effects["items"]),
                next_cursor=effects["next_cursor"],
                reason="Workspace unresolved effects; association with this resource is unconfirmed.",
            ),
        )


class OwnerChangeProposalInput(OwnerChangePreviewInput):
    proposal_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class OwnerChangeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    proposal_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class OwnerMigrationProposal(ContentAddressedModel):
    schema_version: Literal["orgrebase.owner-migration-proposal.v1"] = "orgrebase.owner-migration-proposal.v1"
    proposal_id: str
    policy: Literal["mutual-consent-v1"] = "mutual-consent-v1"
    request_digest: str
    preview: OwnerChangePreview
    binding_revision: str
    policy_digest: str
    expires_at: str


def _migration_key(kind: str, proposal_id: str) -> str:
    return f"owner-migration-{kind}:{proposal_id}@r1"


def _migration_read(workspace: Any, kind: str, proposal_id: str) -> dict[str, Any] | None:
    try:
        return workspace.store.load_artifact(_migration_key(kind, proposal_id), _MIGRATION_MEDIA).payload
    except KeyError:
        return None


def _policy(workspace: Any) -> str:
    if getattr(workspace, "owner_change_policy", "disabled") != "mutual-consent-v1":
        raise AuthorizationError("OWNER_CHANGE_POLICY_DISABLED")
    return sha256_digest({"policy": "mutual-consent-v1", "purpose": "organization_owner_change",
        "tenant_id": workspace.store.tenant_id, "workspace_id": workspace.store.workspace_id,
        "profile_digest": workspace.profile_digest, "confirmation_seconds": 3600,
        "authority": "current-owner-authorizes-and-distinct-successor-accepts"})


def _proposal(workspace: Any, proposal_id: str) -> OwnerMigrationProposal:
    payload = _migration_read(workspace, "proposal", proposal_id)
    if payload is None:
        raise KeyError(proposal_id)
    proposal = OwnerMigrationProposal.model_validate(payload)
    if (proposal.proposal_id != proposal_id or proposal.preview.workspace_id != workspace.store.workspace_id
            or proposal.preview.organization_id != workspace.profile.organization_id):
        raise IntegrityError("OWNER_CHANGE_PROPOSAL_SCOPE_INVALID")
    return proposal


def _live_migration(workspace: Any, proposal: OwnerMigrationProposal) -> None:
    lock_binding_scope(workspace)
    if proposal.policy_digest != _policy(workspace):
        raise FreshnessError("OWNER_CHANGE_POLICY_CHANGED")
    if utc_datetime(workspace.clock.now()) >= utc_datetime(proposal.expires_at):
        raise FreshnessError("OWNER_CHANGE_PROPOSAL_EXPIRED")
    if (binding_revision(workspace) != proposal.binding_revision
            or current_binding(workspace).digest != proposal.preview.binding_digest):
        raise FreshnessError("OWNER_CHANGE_BINDING_CHANGED")
    snapshot = workspace.current_snapshot()
    if (snapshot.digest != proposal.preview.snapshot_digest
            or workspace.current_graph_pointer().payload["snapshot_digest"] != snapshot.digest):
        raise FreshnessError("OWNER_CHANGE_SNAPSHOT_CHANGED")
    source = workspace.store.get_object(proposal.preview.resource.object_id)
    if (source.ref, source.digest, source.state.value) != (
            proposal.preview.source.ref, proposal.preview.source.digest, proposal.preview.source.state):
        raise FreshnessError("OWNER_CHANGE_SOURCE_CHANGED")
    if not callable(getattr(workspace, "authorize_workspace_subject", None)):
        raise AuthorizationError("OWNER_CHANGE_WORKSPACE_MEMBERSHIP_REQUIRED")
    verify_member(workspace, proposal.preview.proposed_owner.model_dump(), "approve")


def _participant_kind(proposal: OwnerMigrationProposal, identity: dict[str, str]) -> str:
    successor = proposal.preview.proposed_owner.model_dump()
    if identity == successor:
        return "acceptance"
    if identity["actor_id"] == proposal.preview.resource.owner_id:
        if (identity["issuer"], identity["subject"]) == (successor["issuer"], successor["subject"]):
            raise AuthorizationError("OWNER_CHANGE_INDEPENDENT_IDENTITIES_REQUIRED")
        return "authorization"
    raise AuthorizationError("OWNER_CHANGE_PARTICIPANT_REQUIRED")


def _verify_confirmations(workspace: Any, proposal: OwnerMigrationProposal,
                          authorization: dict[str, Any], acceptance: dict[str, Any]) -> None:
    old, new = authorization["identity"], acceptance["identity"]
    if (old["actor_id"] != proposal.preview.resource.owner_id
            or new != proposal.preview.proposed_owner.model_dump()
            or (old["issuer"], old["subject"]) == (new["issuer"], new["subject"])):
        raise AuthorizationError("OWNER_CHANGE_CONFIRMATION_IDENTITY_INVALID")
    for receipt in (authorization, acceptance):
        if (receipt["proposal_digest"] != proposal.digest or receipt["policy_digest"] != proposal.policy_digest
                or receipt["purpose"] != "organization_owner_change"):
            raise IntegrityError("OWNER_CHANGE_CONFIRMATION_BINDING_INVALID")
        verify_member(workspace, receipt["identity"], "approve")


def propose_owner_change(workspace: Any, request: OwnerChangeProposalInput) -> dict[str, Any]:
    request = OwnerChangeProposalInput.model_validate(request.model_dump(mode="json"))
    with workspace._command_lock, workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        identity = identity_for_action(workspace, "propose")
        policy = _policy(workspace)
        request_digest = sha256_digest({"request": request, "identity": identity})
        prior = _migration_read(workspace, "proposal", request.proposal_id)
        if prior is not None:
            if _proposal(workspace, request.proposal_id).request_digest != request_digest:
                raise IntegrityError("OWNER_CHANGE_PROPOSAL_ID_CONFLICT")
        else:
            preview = preview_owner_change(workspace, OwnerChangePreviewInput.model_validate(
                request.model_dump(mode="json", exclude={"proposal_id"})))
            proposal = OwnerMigrationProposal(proposal_id=request.proposal_id, request_digest=request_digest,
                preview=preview, binding_revision=binding_revision(workspace), policy_digest=policy,
                expires_at=(utc_datetime(workspace.clock.now()) + timedelta(hours=1)).isoformat())
            workspace.store.save_artifact(connection, _migration_key("proposal", request.proposal_id),
                _MIGRATION_MEDIA, proposal.model_dump(mode="json"))
            workspace.store.append_event(connection, "OWNER_MIGRATION_PROPOSED", {
                "proposal_id": request.proposal_id, "proposal_digest": proposal.digest})
    return owner_change_detail(workspace, request.proposal_id)


def confirm_owner_change(workspace: Any, proposal_id: str, command: OwnerChangeCommand) -> dict[str, Any]:
    command = OwnerChangeCommand.model_validate(command.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        identity = identity_for_action(workspace, "approve")
        verify_member(workspace, identity, "approve")
        proposal = _proposal(workspace, proposal_id)
        if command.proposal_digest != proposal.digest:
            raise IntegrityError("OWNER_CHANGE_PROPOSAL_DIGEST_MISMATCH")
        _live_migration(workspace, proposal)
        kind = _participant_kind(proposal, identity)
        receipt = {"proposal_digest": proposal.digest, "policy_digest": proposal.policy_digest,
                   "purpose": "organization_owner_change", "identity": identity,
                   "confirmed_at": workspace.clock.now()}
        prior = _migration_read(workspace, kind, proposal_id)
        if prior is not None:
            if prior["identity"] != identity or prior["proposal_digest"] != proposal.digest:
                raise IntegrityError("OWNER_CHANGE_CONFIRMATION_CONFLICT")
        else:
            workspace.store.save_artifact(connection, _migration_key(kind, proposal_id), _MIGRATION_MEDIA, receipt)
            workspace.store.append_event(connection, "OWNER_MIGRATION_CONFIRMED", {
                "proposal_id": proposal_id, "confirmation": kind, "receipt_digest": sha256_digest(receipt)})
    return owner_change_detail(workspace, proposal_id)


def activate_owner_change(workspace: Any, proposal_id: str, command: OwnerChangeCommand) -> dict[str, Any]:
    command = OwnerChangeCommand.model_validate(command.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        identity = identity_for_action(workspace, "approve")
        verify_member(workspace, identity, "approve")
        proposal = _proposal(workspace, proposal_id)
        if command.proposal_digest != proposal.digest:
            raise IntegrityError("OWNER_CHANGE_PROPOSAL_DIGEST_MISMATCH")
        authorization = _migration_read(workspace, "authorization", proposal_id)
        acceptance = _migration_read(workspace, "acceptance", proposal_id)
        if authorization is None or acceptance is None:
            raise AuthorizationError("OWNER_CHANGE_TWO_CONFIRMATIONS_REQUIRED")
        if identity not in (authorization["identity"], acceptance["identity"]):
            raise AuthorizationError("OWNER_CHANGE_PARTICIPANT_REQUIRED")
        if _migration_read(workspace, "activation", proposal_id) is None:
            _live_migration(workspace, proposal)
            _verify_confirmations(workspace, proposal, authorization, acceptance)
            activation = {"proposal_digest": proposal.digest, "policy_digest": proposal.policy_digest,
                "previous_revision": proposal.binding_revision, "binding_digest": proposal.preview.proposed_binding.digest,
                "authorization_digest": sha256_digest(authorization), "acceptance_digest": sha256_digest(acceptance),
                "activated_by": identity, "activated_at": workspace.clock.now()}
            version = activate_binding(workspace, connection, binding=proposal.preview.proposed_binding,
                expected_revision=proposal.binding_revision, slot_id=proposal.preview.resource.slot_id,
                migration_digest=sha256_digest(activation))
            workspace.store.save_artifact(connection, _migration_key("activation", proposal_id), _MIGRATION_MEDIA, activation)
            workspace.store.append_event(connection, "OWNER_MIGRATION_ACTIVATED", {
                "proposal_id": proposal_id, "receipt_digest": sha256_digest(activation), "binding_ref": version.ref})
    return owner_change_detail(workspace, proposal_id)


def owner_change_detail(workspace: Any, proposal_id: str) -> dict[str, Any]:
    from orgrebase.workspace.change_proposals import require_action

    require_action(workspace, "read")
    with workspace._command_lock, workspace.store.read_snapshot():
        proposal = _proposal(workspace, proposal_id)
        authorization = _migration_read(workspace, "authorization", proposal_id)
        acceptance = _migration_read(workspace, "acceptance", proposal_id)
        activation = _migration_read(workspace, "activation", proposal_id)
        blocked = None
        actions: list[str] = []
        role = None
        principal = request_principal.get()
        identity = ({"issuer": principal.issuer, "subject": principal.subject, "actor_id": principal.actor_id}
                    if principal is not None else None)
        if activation is None:
            try:
                _live_migration(workspace, proposal)
                if authorization is not None and acceptance is not None:
                    _verify_confirmations(workspace, proposal, authorization, acceptance)
            except (AuthenticationError, AuthorizationError, FreshnessError, IntegrityError) as error:
                blocked = error.code if isinstance(error, AuthenticationError) else str(error).partition(":")[0]
            if blocked is None:
                try:
                    viewer = identity_for_action(workspace, "approve")
                    verify_member(workspace, viewer, "approve")
                    role = _participant_kind(proposal, viewer)
                    receipt = authorization if role == "authorization" else acceptance
                    if receipt is None:
                        actions.append("CONFIRM_" + role.upper())
                    if (authorization is not None and acceptance is not None
                            and viewer in (authorization["identity"], acceptance["identity"])):
                        actions.append("ACTIVATE")
                except (AuthenticationError, AuthorizationError):
                    pass
        return {"proposal": proposal.model_dump(mode="json"), "proposal_digest": proposal.digest,
                "authorization": authorization, "acceptance": acceptance, "activation": activation,
                "status": "APPLIED" if activation else "REPLAN_REQUIRED" if blocked else "PENDING_CONFIRMATION",
                "blocked_reason": blocked, "current_binding_digest": current_binding(workspace).digest,
                "allowed_actions": actions, "confirmation_role": role, "actor_identity": identity,
                "external_organization_policy_acceptance": "NOT_VERIFIED"}


def list_owner_changes(workspace: Any, *, after: str | None = None, limit: int = 20) -> dict[str, Any]:
    from orgrebase.workspace.change_proposals import require_action

    require_action(workspace, "read")
    with workspace._command_lock, workspace.store.read_snapshot():
        page = workspace.store.artifact_page(
            artifact_id_prefix="owner-migration-proposal:", after=after, limit=limit)
        return {"items": [owner_change_detail(workspace, item.payload["proposal_id"]) for item in page["items"]],
                "next_cursor": page["next_cursor"]}


def owner_change_options(workspace: Any) -> dict[str, Any]:
    from orgrebase.workspace.change_proposals import require_action

    require_action(workspace, "read")
    with workspace._command_lock, workspace.store.read_snapshot():
        binding = current_binding(workspace)
        blocked = None
        eligible = []
        try:
            snapshot_digest = workspace.current_snapshot().digest
        except KeyError:
            snapshot_digest = None
        try:
            _policy(workspace)
            requester = identity_for_action(workspace, "propose")
            verify_member(workspace, requester, "propose")
            directory = getattr(workspace, "members_for_action", None)
            if not callable(directory) or not callable(getattr(workspace, "authorize_workspace_subject", None)):
                raise AuthenticationError("AUTH_MEMBER_VERIFICATION_UNAVAILABLE", 503)
            if snapshot_digest is None:
                raise IntegrityError("OWNER_CHANGE_SNAPSHOT_REQUIRED")
            for member in directory("approve"):
                identity = {"issuer": requester["issuer"], "subject": member["subject"], "actor_id": member["actor_id"]}
                try:
                    verify_member(workspace, identity, "approve")
                except AuthenticationError as error:
                    if error.status_code != 403:
                        raise
                    continue
                eligible.append(identity)
            if not eligible:
                raise AuthorizationError("OWNER_CHANGE_ELIGIBLE_OWNER_REQUIRED")
        except (AuthenticationError, AuthorizationError, IntegrityError) as error:
            blocked = error.code if isinstance(error, AuthenticationError) else str(error).partition(":")[0]
            eligible = []
        labels = {"launch_date": "上线日期", "currency": "报价币种", "product_plan": "产品方案"}
        return {"policy": getattr(workspace, "owner_change_policy", "disabled"),
                "can_propose": blocked is None, "blocked_reason": blocked,
                "enterprise_binding_digest": binding.digest, "snapshot_digest": snapshot_digest,
                "resources": [{**resource.model_dump(), "label": labels.get(resource.slot_id, resource.slot_id)}
                              for resource in binding.resources], "eligible_owners": eligible}
