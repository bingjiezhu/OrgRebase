"""Bounded source recovery groups executed by the ordinary RebaseWorkflow."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.certificates import build_minimal_rebase_certificate
from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    ChangeSetRevision,
    ContentAddressedModel,
    FreshnessError,
    ImpactPreview,
    IntegrityError,
    MinimalRebaseCertificate,
    ObjectState,
    RebaseApprovalSet,
    RebaseReceipt,
    RunEnvelope,
    SourceApproval,
)
from orgrebase.impact import ImpactEngine
from orgrebase.workspace.approval_authority import (
    approval_authority_evidence,
    record_approval_authority,
    require_approval_actor,
    verify_approval_authority,
)
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.models import VerifiedAdvisoryBundle, WorkspaceChangeSpec
from orgrebase.workspace.preview_execution import (
    execute_attempt,
    read_attempt_summary,
    require_attempt_sources,
    reserve_attempt,
)
from orgrebase.workspace.read_dependencies import validate_source_observations
from orgrebase.workspace.runtime_revision import (
    bind_preview_runtime,
    require_preview_runtime,
    workspace_revision,
)

MEDIA = "application/vnd.orgrebase.source-readmission-group+json"
PREFIX = "workspace-source-readmission:"


class SourceEventRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(min_length=1, max_length=160)
    event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SourceReadmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    group_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    events: tuple[SourceEventRef, ...] = Field(min_length=2, max_length=3)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_events(self):
        if len({item.event_id for item in self.events}) != len(self.events) or not self.reason.strip():
            raise ValueError("SOURCE_GROUP_MEMBERS_INVALID")
        return self


class SourceReadmissionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    group_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    preview_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    owner_id: str | None = None
    actor_id: str | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=1000)


class SourceReadmissionGroup(ContentAddressedModel):
    schema_version: Literal["orgrebase.source-readmission-group.v1"] = "orgrebase.source-readmission-group.v1"
    id: str
    tenant_id: str
    workspace_id: str
    execution_run_id: str
    profile_digest: str
    events: tuple[SourceEventRef, ...] = Field(min_length=2, max_length=3)
    reason: str
    source_specs: tuple[WorkspaceChangeSpec, ...] = Field(min_length=2, max_length=3)
    snapshot_ref: str
    snapshot_digest: str
    predecessor_ref: str
    predecessor_digest: str
    predecessor_state: ObjectState
    change_set: ChangeSetRevision
    preview: ImpactPreview
    minimal_rebase_certificate: MinimalRebaseCertificate
    run_envelope: RunEnvelope
    advisory: VerifiedAdvisoryBundle
    review_not_before_epoch_ms: int
    review_duration_ms: int


def _key(kind: str, identity: str) -> str:
    return f"{PREFIX}{kind}:" + sha256_digest(identity)[7:]


def _read(workspace: Any, kind: str, identity: str) -> dict[str, Any] | None:
    try:
        return workspace.store.load_artifact(_key(kind, identity), MEDIA).payload
    except KeyError:
        return None


def _write(workspace: Any, connection: Any, kind: str, identity: str, value: dict[str, Any]) -> tuple[str, str]:
    key = _key(kind, identity)
    return key, workspace.store.save_artifact(connection, key, MEDIA, value)


def membership(workspace: Any, event_id: str) -> dict[str, Any] | None:
    return _read(workspace, "member", event_id)


def load_group(workspace: Any, group_id: str) -> SourceReadmissionGroup:
    value = _read(workspace, "group", group_id)
    if value is None:
        raise KeyError(group_id)
    group = SourceReadmissionGroup.model_validate(value)
    if (group.id != group_id or group.tenant_id != workspace.profile.organization_id
            or group.workspace_id != workspace.store.workspace_id
            or group.execution_run_id != workspace.effective_workflow_run_id
            or group.profile_digest != workspace.profile_digest):
        raise IntegrityError("SOURCE_GROUP_SCOPE_MISMATCH")
    if len(group.events) != len(group.source_specs) or len({spec.object_id for spec in group.source_specs}) != len(group.events):
        raise IntegrityError("SOURCE_GROUP_MEMBER_SET_INVALID")
    anchor = workspace.changes.journal("WORKSPACE_SOURCE_GROUP_PREVIEWED", subject_key=group.id, limit=1, descending=True)
    expected = {"kind": group.id, "group_id": group.id, "group_digest": group.digest,
                "artifact_id": _key("group", group.id), "artifact_digest": sha256_digest(value),
                "preview_digest": group.preview.digest, "event_ids": [ref.event_id for ref in group.events]}
    if len(anchor) != 1 or anchor[0]["payload"] != expected:
        raise IntegrityError("SOURCE_GROUP_PREVIEW_EVENT_BINDING_INVALID")
    for ref, spec in zip(group.events, group.source_specs, strict=True):
        event = workspace.changes.get(ref.event_id)
        if (event.operation != "READMIT" or spec.operation != "READMIT"
                or (spec.object_id, spec.owner_id, spec.base_version, spec.proposed_version) !=
                   (event.proposal.id, event.owner_id, event.base_version, event.proposal.version)
                or (spec.expected_snapshot_ref, spec.expected_snapshot_digest) != (group.snapshot_ref, group.snapshot_digest)):
            raise IntegrityError("SOURCE_GROUP_SOURCE_SPEC_INVALID")
        if event.digest != ref.event_digest or membership(workspace, ref.event_id) != {
            "group_id": group.id, "group_digest": group.digest, "event_digest": event.digest,
        }:
            raise IntegrityError("SOURCE_GROUP_MEMBER_BINDING_INVALID")
    return group


def _fixture(workspace: Any, group: SourceReadmissionGroup):
    fixture = workspace._fixture_for_change(group.source_specs[0])
    proposals = tuple(workspace.store.get_object(spec.object_id, spec.proposed_version) for spec in group.source_specs)
    refs = {item.ref for item in fixture.objects}
    return fixture.model_copy(update={"objects": (*fixture.objects, *(item for item in proposals if item.ref not in refs))})


def _rejection(workspace: Any, group: SourceReadmissionGroup) -> dict[str, Any] | None:
    value = _read(workspace, "rejection", group.id)
    if value is None:
        return None
    anchor = workspace.changes.journal("WORKSPACE_SOURCE_GROUP_REJECTED", subject_key=group.id, limit=1, descending=True)
    expected = {"schema_version": "orgrebase.source-readmission-group-rejection.v1", "kind": group.id,
                "group_id": group.id, "group_digest": group.digest,
                "event_ids": [item.event_id for item in group.events],
                "artifact_id": _key("rejection", group.id), "artifact_digest": sha256_digest(value)}
    if len(anchor) != 1 or anchor[0]["payload"] != expected:
        raise IntegrityError("SOURCE_GROUP_REJECTION_EVENT_BINDING_INVALID")
    return value


def _outcome(workspace: Any, group: SourceReadmissionGroup) -> dict[str, Any] | None:
    value = _read(workspace, "outcome", group.id)
    if value is None:
        return None
    receipt = RebaseReceipt.model_validate(value["rebase_receipt"])
    anchor = workspace.changes.journal("WORKSPACE_SOURCE_GROUP_APPLIED", subject_key=group.id, limit=1, descending=True)
    expected = {"schema_version": "orgrebase.source-readmission-group-outcome.v1",
                "kind": group.id, "group_id": group.id, "group_digest": group.digest,
                "event_ids": [item.event_id for item in group.events],
                "artifact_id": _key("outcome", group.id), "artifact_digest": sha256_digest(value),
                "rebase_receipt_ref": receipt.id, "rebase_receipt_digest": receipt.digest,
                "quote_ref": f"{value['quote']['id']}@{value['quote']['version']}"}
    if len(anchor) != 1 or anchor[0]["payload"] != expected:
        raise IntegrityError("SOURCE_GROUP_OUTCOME_EVENT_BINDING_INVALID")
    actual = workspace.store.load_artifact(receipt.id, "application/vnd.orgrebase.rebase-receipt+json").payload
    if (actual != receipt.model_dump(mode="json") or value["group_digest"] != group.digest
            or receipt.revision_lock.change_set_digest != group.change_set.digest
            or receipt.approval_set is None or receipt.approval_set.model_dump(mode="json") != value["approval_set"]):
        raise IntegrityError("SOURCE_GROUP_COMMITTED_RECEIPT_BINDING_INVALID")
    request_digest = sha256_digest({"change_set": group.change_set.digest, "preview": group.preview.digest,
                                   "minimal_rebase_certificate": group.minimal_rebase_certificate.digest,
                                   "approval": receipt.approval_set.digest})
    if workspace.store.get_idempotent(f"workspace:source-group:{group.id}@r1", request_digest) != actual:
        raise IntegrityError("SOURCE_GROUP_COMMIT_IDEMPOTENCY_BINDING_INVALID")
    return value


def _owners(group: SourceReadmissionGroup) -> dict[str, tuple[str, ...]]:
    return {owner: tuple(sorted(spec.object_id for spec in group.source_specs if spec.owner_id == owner))
            for owner in sorted({spec.owner_id for spec in group.source_specs})}


def _owner_events(group: SourceReadmissionGroup, owner_id: str) -> tuple[str, ...]:
    return tuple(ref.event_id for ref, spec in zip(group.events, group.source_specs, strict=True) if spec.owner_id == owner_id)


def _approval_key(group_id: str, owner_id: str) -> str:
    return group_id + ":" + owner_id


def _approval_record(workspace: Any, group: SourceReadmissionGroup, owner_id: str) -> dict[str, Any] | None:
    identity = _approval_key(group.id, owner_id)
    value = _read(workspace, "approval", identity)
    if value is None:
        return None
    anchor = workspace.changes.journal("WORKSPACE_SOURCE_GROUP_APPROVED", subject_key=identity, limit=1, descending=True)
    approval = SourceApproval.model_validate(value["source_approval"])
    expected = {"group_id": group.id, "group_digest": group.digest, "owner_id": owner_id,
                "artifact_id": _key("approval", identity), "artifact_digest": sha256_digest(value),
                "approval_digest": approval.approval.digest, "event_ids": list(_owner_events(group, owner_id)),
                "authority_digests": value["authority_digests"], "kind": identity}
    if len(anchor) != 1 or anchor[0]["payload"] != expected:
        raise IntegrityError("SOURCE_GROUP_APPROVAL_EVENT_BINDING_INVALID")
    authorities = {event_id: approval_authority_evidence(workspace, event_id, approval.approval)
                   for event_id in _owner_events(group, owner_id)}
    if value["authority_digests"] != {event_id: authority["provenance_digest"] if authority else None
                                       for event_id, authority in authorities.items()}:
        raise IntegrityError("SOURCE_GROUP_AUTHORITY_EVENT_BINDING_INVALID")
    return value


def _approvals(workspace: Any, group: SourceReadmissionGroup) -> tuple[SourceApproval, ...]:
    values = (_approval_record(workspace, group, owner) for owner in _owners(group))
    return tuple(SourceApproval.model_validate(value["source_approval"]) for value in values if value is not None)


def _live(workspace: Any, group: SourceReadmissionGroup, *, approvals: bool = False) -> None:
    from orgrebase.workspace.enterprise_binding import lock_binding_scope, require_change_owner
    lock_binding_scope(workspace)
    for member in group.events:
        require_change_owner(workspace, workspace.changes.get(member.event_id))
    require_preview_runtime(workspace, group.id, group.preview.digest)
    if _rejection(workspace, group) is not None:
        raise IntegrityError("SOURCE_GROUP_REJECTED")
    now = utc_datetime(workspace.clock.now())
    if now >= utc_datetime(group.run_envelope.expires_at):
        raise FreshnessError("SOURCE_GROUP_PREVIEW_EXPIRED")
    snapshot, quote = workspace.current_snapshot(), workspace.current_quote()
    if (quote.state != group.predecessor_state
            or (snapshot.ref, snapshot.digest) != (group.snapshot_ref, group.snapshot_digest)
            or (quote.ref, quote.digest) != (group.predecessor_ref, group.predecessor_digest)):
        raise FreshnessError("SOURCE_GROUP_BASE_CHANGED")
    # This is the same source validation used again inside the committing kernel.
    workflow = workspace._create_rebase_workflow(_fixture(workspace, group), authorize_commit=lambda: None)
    workflow._assert_current_sources(group.change_set)
    validate_source_observations(workspace, (workspace.changes.get(ref.event_id).proposal
                                          for ref in group.events), workspace.clock.now())
    for ref in group.events:
        event = workspace.changes.get(ref.event_id)
        current = workspace.store.get_object(event.proposal.id)
        if (current.version, current.digest) != (event.base_version, event.base_digest):
            raise FreshnessError("SOURCE_GROUP_SOURCE_BASE_CHANGED")
        if (utc_datetime(event.proposal.valid_from) > now
                or (event.proposal.valid_to is not None and utc_datetime(event.proposal.valid_to) <= now)):
            raise FreshnessError("SOURCE_GROUP_SOURCE_VALIDITY_INVALID")
    _authority(workspace, group, complete=approvals)


def _authority(workspace: Any, group: SourceReadmissionGroup, *, complete: bool) -> None:
    require_preview_runtime(workspace, group.id, group.preview.digest)
    if _rejection(workspace, group) is not None:
        raise IntegrityError("SOURCE_GROUP_REJECTED")
    now = utc_datetime(workspace.clock.now())
    if now >= utc_datetime(group.run_envelope.expires_at):
        raise FreshnessError("SOURCE_GROUP_PREVIEW_EXPIRED")
    members = _approvals(workspace, group)
    if complete and {member.owner_id for member in members} != set(_owners(group)):
        raise IntegrityError("SOURCE_GROUP_APPROVALS_INCOMPLETE")
    for member in members:
        saved = _read(workspace, "approval", _approval_key(group.id, member.owner_id))
        review = saved.get("review_evidence", {})
        if (review.get("group_digest") != group.digest or review.get("preview_digest") != group.preview.digest
                or review.get("review_not_before_epoch_ms") != group.review_not_before_epoch_ms
                or review.get("review_wait_satisfied") is not True
                or type(review.get("approved_at_epoch_ms")) is not int
                or review["approved_at_epoch_ms"] < group.review_not_before_epoch_ms):
            raise IntegrityError("SOURCE_GROUP_REVIEW_EVIDENCE_INVALID")
        if (member.source_ids != _owners(group).get(member.owner_id)
                or member.approval.change_set_digest != group.change_set.digest
                or member.approval.preview_digest != group.preview.digest
                or member.approval.minimal_rebase_certificate_digest != group.minimal_rebase_certificate.digest):
            raise IntegrityError("SOURCE_GROUP_APPROVAL_BINDING_INVALID")
        if not utc_datetime(member.approval.approved_at) <= now < utc_datetime(member.approval.expires_at):
            raise FreshnessError("SOURCE_GROUP_APPROVAL_EXPIRED")
        for event_id in _owner_events(group, member.owner_id):
            verify_approval_authority(workspace, event_id, member.approval, current=True)


def member_status(workspace: Any, event_id: str) -> str | None:
    linked = membership(workspace, event_id)
    if linked is None:
        return None
    group = load_group(workspace, linked["group_id"])
    if _outcome(workspace, group) is not None:
        return "GROUP_APPLIED"
    if _rejection(workspace, group) is not None:
        return "REJECTED"
    try:
        _live(workspace, group)
    except (IntegrityError, FreshnessError, AuthenticationError, AuthorizationError, RuntimeError):
        return "EXPIRED"
    return "GROUP_REVIEW"


def _group_events(workspace: Any, ordered: tuple[SourceEventRef, ...]):
    events = tuple(workspace.changes.get(ref.event_id) for ref in ordered)
    if (any(event.digest != ref.event_digest or event.operation != "READMIT"
            for event, ref in zip(events, ordered, strict=True))
            or len({event.slot_id for event in events}) != len(events)
            or any(event.slot_id not in workspace.domain_pack.mutable_slots for event in events)):
        raise IntegrityError("SOURCE_GROUP_REQUIRES_DISTINCT_READMISSIONS")
    if any(membership(workspace, event.event_id) is not None
           or workspace._change_status(event.event_id) != "RECEIVED" for event in events):
        raise IntegrityError("SOURCE_GROUP_MEMBER_UNAVAILABLE")
    return events


def _group_fixture(workspace: Any, specs: tuple[WorkspaceChangeSpec, ...], events: tuple):
    fixture = workspace._fixture_for_change(specs[0])
    existing = {item.ref for item in fixture.objects}
    return fixture.model_copy(update={"objects": (*fixture.objects,
        *(event.proposal for event in events if event.proposal.ref not in existing))})


def create_group(workspace: Any, request: SourceReadmissionInput) -> dict[str, Any]:
    request = SourceReadmissionInput.model_validate(request.model_dump(mode="json"))
    require_action(workspace, "propose")
    ordered = tuple(sorted(request.events, key=lambda item: item.event_id))
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        require_action(workspace, "propose")
        workspace.changes.refresh()
        prior = _read(workspace, "group", request.group_id)
        if prior is not None:
            group = load_group(workspace, request.group_id)
            if group.events != ordered or group.reason != request.reason:
                raise IntegrityError("SOURCE_GROUP_ID_CONFLICT")
            return group_detail(workspace, group.id)
        if workspace._formation_record() is None:
            raise IntegrityError("WORKSPACE_QUOTE_NOT_FORMED")
        events = _group_events(workspace, ordered)
        specs = tuple(workspace.change_spec(event.event_id) for event in events)
        fixture = _group_fixture(workspace, specs, events)
        singles = tuple(workspace.change_builder.build(fixture=fixture, spec=spec) for spec in specs)
        change_set = ChangeSetRevision(
            id=f"changeset:workspace-source-group:{request.group_id}", revision="r1",
            state="READMISSION_GROUP_ADMITTED", owner_id="authority:source-owners",
            purpose=singles[0].purpose, scope=singles[0].scope,
            deltas=tuple(sorted((single.deltas[0] for single in singles), key=lambda delta: delta.object_id)),
        )
        preview = ImpactEngine(fixture).preview(change_set)
        minimal = build_minimal_rebase_certificate(change_set, preview)
        quote = workspace.current_quote()
        validate_source_observations(workspace, (event.proposal for event in events), workspace.clock.now())
        captured_runtime = workspace_revision(workspace)
        adapter, verifier = workspace.advisory_factory, workspace.advisory_verifier
        attempt = reserve_attempt(workspace, connection,
            command=f"source-group:{request.group_id}",
            fixture=fixture, change_set=change_set, preview=preview, envelope=workspace._run_envelope(specs[0]),
            request_binding={"reason": request.reason, "events": [ref.model_dump(mode="json") for ref in ordered]})
    advisory = execute_attempt(workspace, attempt, fixture=fixture, change_set=change_set, preview=preview,
                               adapter=adapter, verifier=verifier)
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        require_action(workspace, "propose")
        workspace.changes.refresh()
        workspace.store.get_idempotent(attempt.key, attempt.request_digest, connection=connection)
        if workspace_revision(workspace) != captured_runtime:
            raise IntegrityError("WORKSPACE_ADVISORY_RUNTIME_CHANGED")
        prior = _read(workspace, "group", request.group_id)
        if prior is not None:
            group = load_group(workspace, request.group_id)
            if group.events != ordered or group.reason != request.reason:
                raise IntegrityError("SOURCE_GROUP_ID_CONFLICT")
            return group_detail(workspace, group.id)
        events = _group_events(workspace, ordered)
        current_fixture = _group_fixture(workspace, specs, events)
        if (workspace.current_quote().digest != quote.digest
                or sha256_digest(current_fixture.model_dump(mode="json")) != sha256_digest(fixture.model_dump(mode="json"))):
            raise IntegrityError("WORKSPACE_ADVISORY_INPUT_CHANGED")
        if utc_datetime(workspace.clock.now()) >= utc_datetime(attempt.envelope.expires_at):
            raise IntegrityError("SOURCE_GROUP_PREVIEW_EXPIRED")
        validate_source_observations(workspace, (event.proposal for event in events), workspace.clock.now())
        require_attempt_sources(workspace, attempt)
        group = SourceReadmissionGroup(
            id=request.group_id, tenant_id=workspace.profile.organization_id, workspace_id=workspace.store.workspace_id,
            execution_run_id=workspace.effective_workflow_run_id, profile_digest=workspace.profile_digest,
            events=ordered, reason=request.reason, source_specs=specs,
            snapshot_ref=specs[0].expected_snapshot_ref, snapshot_digest=specs[0].expected_snapshot_digest,
            predecessor_ref=quote.ref, predecessor_digest=quote.digest, predecessor_state=quote.state,
            change_set=change_set, preview=preview, minimal_rebase_certificate=minimal,
            run_envelope=attempt.envelope, advisory=advisory,
            review_not_before_epoch_ms=workspace._wall_clock_epoch_ms() + workspace.review_duration_ms,
            review_duration_ms=workspace.review_duration_ms,
        )
        ref, digest = _write(workspace, connection, "group", group.id, group.model_dump(mode="json"))
        for event in events:
            _write(workspace, connection, "member", event.event_id, {
                "group_id": group.id, "group_digest": group.digest, "event_digest": event.digest,
            })
        bind_preview_runtime(workspace, connection, group.id, group.preview.digest)
        workspace.store.append_event(connection, "WORKSPACE_SOURCE_GROUP_PREVIEWED", {
            "kind": group.id, "group_id": group.id, "group_digest": group.digest, "artifact_id": ref,
            "artifact_digest": digest, "preview_digest": preview.digest,
            "event_ids": [event.event_id for event in events],
        })
    return group_detail(workspace, group.id)


def _command(workspace: Any, group_id: str, command: SourceReadmissionCommand, action: str) -> tuple[SourceReadmissionGroup, str]:
    command = SourceReadmissionCommand.model_validate(command.model_dump())
    actor = require_action(workspace, action)
    group = load_group(workspace, group_id)
    if command.group_digest != group.digest or command.preview_digest != group.preview.digest:
        raise IntegrityError("SOURCE_GROUP_COMMAND_BINDING_INVALID")
    if request_principal.get() is None:
        actor = command.actor_id or actor
    elif command.actor_id is not None and command.actor_id != actor:
        raise AuthorizationError("SOURCE_GROUP_ACTOR_MISMATCH")
    return group, actor


def approve_group(workspace: Any, group_id: str, command: SourceReadmissionCommand) -> dict[str, Any]:
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        group, actor = _command(workspace, group_id, command, "approve")
        owner = command.owner_id
        if owner not in _owners(group):
            raise AuthorizationError("SOURCE_GROUP_OWNER_SCOPE_REQUIRED")
        _live(workspace, group)
        for event_id in _owner_events(group, owner):
            require_approval_actor(workspace, event_id, actor)
        observed = workspace._wall_clock_epoch_ms()
        if observed < group.review_not_before_epoch_ms:
            raise IntegrityError("SOURCE_GROUP_REVIEW_WAIT_REQUIRED")
        existing = _read(workspace, "approval", _approval_key(group.id, owner))
        if existing is not None:
            if existing["source_approval"]["approval"]["actor_id"] != actor:
                raise IntegrityError("SOURCE_GROUP_APPROVAL_ACTOR_CONFLICT")
            return group_detail(workspace, group.id)
        workflow = workspace._create_rebase_workflow(_fixture(workspace, group), authorize_commit=lambda: None)
        approval = workflow.approve(group.change_set, group.preview, group.minimal_rebase_certificate,
                                    actor_id=actor, owner_id=owner)
        member = SourceApproval(owner_id=owner, source_ids=_owners(group)[owner], approval=approval)
        authorities = {}
        for event_id in _owner_events(group, owner):
            authorities[event_id] = record_approval_authority(workspace, event_id, approval, connection)
        value = {"schema_version": "orgrebase.source-group-approval.v1", "group_digest": group.digest,
                 "source_approval": member.model_dump(mode="json"), "authority_digests": authorities,
                 "review_evidence": {"review_not_before_epoch_ms": group.review_not_before_epoch_ms,
                                     "approved_at_epoch_ms": observed, "review_wait_satisfied": True,
                                     "group_digest": group.digest, "preview_digest": group.preview.digest}}
        ref, digest = _write(workspace, connection, "approval", _approval_key(group.id, owner), value)
        workspace.store.append_event(connection, "WORKSPACE_SOURCE_GROUP_APPROVED", {
            "kind": _approval_key(group.id, owner),
            "group_id": group.id, "group_digest": group.digest, "owner_id": owner,
            "artifact_id": ref, "artifact_digest": digest, "approval_digest": approval.digest,
            "event_ids": list(_owner_events(group, owner)), "authority_digests": authorities,
        })
    return group_detail(workspace, group.id)


class _GroupCompletion:
    def __init__(self, extension: Any, workspace: Any, group: SourceReadmissionGroup, approval_set: RebaseApprovalSet):
        self.extension, self.workspace, self.group, self.approval_set = extension, workspace, group, approval_set

    def commit(self, connection: Any, **kwargs: Any):
        successor = self.extension.commit(connection, **kwargs)
        if successor is None:
            raise IntegrityError("SOURCE_GROUP_QUOTE_SUCCESSOR_REQUIRED")
        workspace, group, receipt = self.workspace, self.group, kwargs["base_receipt"]
        value = {"schema_version": "orgrebase.source-readmission-group-outcome.v1",
                 "group_id": group.id, "group_digest": group.digest,
                 "approval_set": self.approval_set.model_dump(mode="json"),
                 "rebase_receipt": receipt.model_dump(mode="json"),
                 "workspace_rebase_receipt": successor.model_dump(mode="json"),
                 "quote": workspace.current_quote().model_dump(mode="json"),
                 "graph_pointer": workspace.current_graph_pointer().model_dump(mode="json")}
        ref, digest = _write(workspace, connection, "outcome", group.id, value)
        workspace.store.append_event(connection, "WORKSPACE_SOURCE_GROUP_APPLIED", {
            "schema_version": value["schema_version"], "kind": group.id, "group_id": group.id, "group_digest": group.digest,
            "event_ids": [item.event_id for item in group.events], "artifact_id": ref, "artifact_digest": digest,
            "rebase_receipt_ref": receipt.id, "rebase_receipt_digest": receipt.digest,
            "quote_ref": workspace.current_quote().ref,
        })
        return successor


def apply_group(workspace: Any, group_id: str, command: SourceReadmissionCommand, *, fail_after: str | None = None) -> dict[str, Any]:
    with workspace._command_lock:
        group, _ = _command(workspace, group_id, command, "execute")
        if _outcome(workspace, group) is not None:
            return group_detail(workspace, group.id)
        def authorize() -> None:
            require_action(workspace, "execute")
            if _outcome(workspace, group) is None:
                _live(workspace, group, approvals=True)
        try:
            authorize()
            approval_set = RebaseApprovalSet(id=f"source-group-approval:{group.id}@r1", members=_approvals(workspace, group))
            fixture = _fixture(workspace, group)
            def authorize_completion() -> None:
                require_action(workspace, "execute")
                _authority(workspace, group, complete=True)
            workflow = workspace._create_rebase_workflow(fixture, authorize_commit=authorize,
                authorize_completion=authorize_completion, fail_after=fail_after)
            workflow.apply_extension = _GroupCompletion(workflow.apply_extension, workspace, group, approval_set)
            workflow.apply(change_set=group.change_set, preview=group.preview,
                           minimal_rebase_certificate=group.minimal_rebase_certificate, approval=approval_set,
                           collaboration=workspace._collaboration(group), run_envelope=group.run_envelope,
                           current_revisions=fixture.revisions, idempotency_key=f"workspace:source-group:{group.id}@r1")
        except (FreshnessError, IntegrityError, RuntimeError):
            require_action(workspace, "execute")
            if _outcome(workspace, group) is None:
                raise
        workspace.changes.refresh()
        return group_detail(workspace, group.id)


def reject_group(workspace: Any, group_id: str, command: SourceReadmissionCommand) -> dict[str, Any]:
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        group, actor = _command(workspace, group_id, command, "approve")
        if _outcome(workspace, group) is not None:
            raise IntegrityError("SOURCE_GROUP_ALREADY_APPLIED")
        if command.owner_id not in _owners(group) or not command.reason or not command.reason.strip():
            raise IntegrityError("SOURCE_GROUP_REJECTION_SCOPE_AND_REASON_REQUIRED")
        for event_id in _owner_events(group, command.owner_id):
            require_approval_actor(workspace, event_id, actor)
        value = {"schema_version": "orgrebase.source-readmission-group-rejection.v1",
                 "group_id": group.id, "group_digest": group.digest, "owner_id": command.owner_id,
                 "actor_id": actor, "reason": command.reason, "rejected_at": workspace.clock.now()}
        prior = _rejection(workspace, group)
        if prior is not None:
            if any(prior[key] != value[key] for key in ("owner_id", "actor_id", "reason")):
                raise IntegrityError("SOURCE_GROUP_REJECTION_CONFLICT")
        else:
            ref, digest = _write(workspace, connection, "rejection", group.id, value)
            workspace.store.append_event(connection, "WORKSPACE_SOURCE_GROUP_REJECTED", {
                "schema_version": value["schema_version"], "kind": group.id, "group_id": group.id, "group_digest": group.digest,
                "event_ids": [item.event_id for item in group.events], "artifact_id": ref, "artifact_digest": digest,
            })
    workspace.changes.refresh()
    return group_detail(workspace, group.id)


def group_attempt_detail(workspace: Any, group_id: str) -> dict[str, Any]:
    """Read an existing attempt even when no group was admitted."""
    require_action(workspace, "read")
    with workspace._command_lock:
        summary = read_attempt_summary(workspace, command=f"source-group:{group_id}")
        if summary is None:
            raise KeyError(group_id)
        return {"group_id": group_id, "advisory_attempt": summary}


def group_detail(workspace: Any, group_id: str) -> dict[str, Any]:
    require_action(workspace, "read")
    group = load_group(workspace, group_id)
    outcome, rejection = _outcome(workspace, group), _rejection(workspace, group)
    members = _approvals(workspace, group)
    blocked = None
    state = "APPLIED" if outcome else "REJECTED" if rejection else "REVIEW"
    if not outcome and not rejection:
        try:
            _live(workspace, group)
            if group.preview.state != "READY":
                blocked = "SOURCE_GROUP_IMPACT_NOT_READY"
            elif len(members) == len(_owners(group)):
                state = "APPROVED"
        except (IntegrityError, FreshnessError, AuthenticationError, AuthorizationError, RuntimeError) as error:
            state, blocked = "EXPIRED", str(error).split(":", 1)[0]
    principal = request_principal.get()
    owner_views = []
    for owner, source_ids in _owners(group).items():
        allowed = []
        if state in {"REVIEW", "APPROVED", "EXPIRED"}:
            try:
                require_action(workspace, "approve")
                for event_id in _owner_events(group, owner):
                    require_approval_actor(workspace, event_id, principal.actor_id if principal else owner)
                allowed = ["REJECT"]
                if blocked is None and owner not in {member.owner_id for member in members}:
                    allowed.append("APPROVE")
            except (AuthenticationError, AuthorizationError, FreshnessError):
                pass
        member = next((item for item in members if item.owner_id == owner), None)
        owner_views.append({"owner_id": owner, "source_ids": list(source_ids), "allowed_actions": allowed,
                            "source_approval": member.model_dump(mode="json") if member else None,
                            "review_evidence": _read(workspace, "approval", _approval_key(group.id, owner))["review_evidence"] if member else None,
                            "authorities": {event_id: approval_authority_evidence(workspace, event_id, member.approval)
                                            for event_id in _owner_events(group, owner)} if member else {}})
    actions = []
    if state == "APPROVED" and blocked is None:
        try:
            require_action(workspace, "execute")
            actions = ["APPLY"]
        except (AuthenticationError, AuthorizationError):
            pass
    return {"schema_version": "orgrebase.source-readmission-group-detail.v1",
            "group": group.model_dump(mode="json"),
            "source_events": [workspace.changes.get(ref.event_id).model_dump(mode="json") for ref in group.events],
            "state": state, "blocked_reason": blocked,
            "owners": owner_views, "allowed_actions": actions, "outcome": outcome, "rejection": rejection,
            "review_remaining_ms": max(0, group.review_not_before_epoch_ms - workspace._wall_clock_epoch_ms())}


def list_groups(workspace: Any, *, after: str | None = None, limit: int = 20) -> dict[str, Any]:
    require_action(workspace, "read")
    page = workspace.store.artifact_page(artifact_id_prefix=PREFIX + "group:", after=after, limit=limit)
    return {"items": [group_detail(workspace, item.payload["id"]) for item in page["items"]],
            "next_cursor": page["next_cursor"]}


def completion_groups(workspace: Any) -> list[dict[str, Any]]:
    """Explicit export operation; normal group lists remain keyset paginated."""
    results, after = [], None
    while True:
        page = list_groups(workspace, after=after, limit=100)
        results.extend(page["items"])
        after = page["next_cursor"]
        if after is None:
            return results


def group_options(workspace: Any, *, limit: int = 30) -> dict[str, Any]:
    require_action(workspace, "read")
    snapshot = workspace.current_snapshot()
    candidates = []
    for event_id in workspace.changes.eligible_ids(now=workspace.clock.now(), snapshot_ref=snapshot.ref, snapshot_digest=snapshot.digest):
        event = workspace.changes.get(event_id)
        if event.operation != "READMIT" or workspace._change_status(event_id) != "RECEIVED":
            continue
        candidates.append({"event_id": event.event_id, "event_digest": event.digest, "slot_id": event.slot_id,
                           "owner_id": event.owner_id, "base_version": event.base_version,
                           "value": event.proposal.payload["canonical_value"]})
        if len(candidates) >= limit:
            break
    try:
        require_action(workspace, "propose")
        allowed = ["CREATE"]
    except (AuthenticationError, AuthorizationError):
        allowed = []
    return {"schema_version": "orgrebase.source-readmission-options.v1", "candidates": candidates,
            "allowed_actions": allowed, "maximum_sources": 3, "execution_run_id": workspace.effective_workflow_run_id}
