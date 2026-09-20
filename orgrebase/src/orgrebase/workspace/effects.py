"""Exact external-operation proposals backed by the shared effect ledger."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orgrebase.auth import request_principal
from orgrebase.clock import timestamp, utc_datetime
from orgrebase.commit_gateway import EffectError, EffectRequest
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.digest import sha256_digest
from orgrebase.domain import ObjectState
from orgrebase.workspace.change_proposals import _can, require_action
from orgrebase.workspace.enterprise_binding import binding_context, binding_revision, lock_binding_scope
from orgrebase.workspace.runtime_revision import effect_runtime_revision

_MEDIA = "application/vnd.orgrebase.external-operation+json"
_PROPOSALS = "external-proposal:"


class EffectWorkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    target: DataverseDraftTargetSettings
    workspace_id: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    owner_id: str = Field(min_length=1, max_length=256)
    organization_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    target_token_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    access_token_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    approval_seconds: int = Field(default=900, ge=30, le=900)
    observation_seconds: int = Field(default=300, ge=30, le=900)

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


def load_effect_config(path: Path | None = None) -> EffectWorkerConfig:
    selected = path or Path(os.environ.get("ORGREBASE_EFFECT_CONFIG", ""))
    if not selected.is_file() or selected.is_symlink() or selected.stat().st_size > 65_536:
        raise EffectError("EFFECT_CONFIGURATION_UNAVAILABLE")
    try:
        return EffectWorkerConfig.model_validate_json(selected.read_bytes())
    except ValidationError:
        raise EffectError("EFFECT_CONFIGURATION_INVALID") from None


class EffectProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    observation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    changes: dict[str, str]
    reason: str = Field(min_length=1, max_length=1000)


class EffectApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EffectActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action: Literal["EXECUTE", "QUERY", "CANCEL"]
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def _key(kind: str, identity: str) -> str:
    return f"external-{kind}:{identity}@r1"


def _load(workspace: Any, kind: str, identity: str) -> dict[str, Any]:
    return workspace.store.load_artifact(_key(kind, identity), _MEDIA).payload


def _optional(workspace: Any, kind: str, identity: str) -> dict[str, Any] | None:
    try:
        return _load(workspace, kind, identity)
    except KeyError:
        return None


def _save(workspace: Any, connection: Any, kind: str, identity: str, value: dict[str, Any]) -> str:
    return workspace.store.save_artifact(connection, _key(kind, identity), _MEDIA, value)


def _scope(workspace: Any, config: EffectWorkerConfig) -> None:
    if (workspace.profile.organization_id != config.target.tenant_id
            or workspace.store.workspace_id != config.workspace_id):
        raise EffectError("EFFECT_WORKSPACE_BINDING_MISMATCH")


def _identity(workspace: Any, action: str) -> dict[str, str]:
    actor = require_action(workspace, action)
    principal = request_principal.get()
    return {"actor_id": actor, "issuer": principal.issuer if principal else "controlled-local",
            "subject": principal.subject if principal else actor}


def _owner(workspace: Any, config: EffectWorkerConfig) -> dict[str, str]:
    identity = _identity(workspace, "approve")
    if identity["actor_id"] != config.owner_id:
        raise EffectError("EFFECT_OWNER_REQUIRED")
    return identity


def _fresh(workspace: Any, expires_at: str) -> None:
    if utc_datetime(expires_at) <= utc_datetime(workspace.clock.now()):
        raise EffectError("EFFECT_AUTHORITY_EXPIRED")


def _require_applicable_quote(workspace: Any, quote: Any, *,
                              read_source_gaps: Callable[[], Any] | None = None) -> None:
    """Content identity survives source invalidation; execution authority does not."""
    if quote.state != ObjectState.CURRENT:
        raise EffectError("EFFECT_SOURCE_QUOTE_NOT_CURRENT")
    if (read_source_gaps or workspace.changes.gaps)():
        raise EffectError("EFFECT_SOURCE_READMISSION_REQUIRED")


def record_target_observation(workspace: Any, config: EffectWorkerConfig,
                              adapter: DataverseDraftTarget, observation_id: str) -> dict[str, Any]:
    """Worker-only observation; no target credential is read by the web layer."""
    _scope(workspace, config)
    actor = _identity(workspace, "execute")
    if adapter.settings != config.target:
        raise EffectError("EFFECT_TARGET_CONFIGURATION_MISMATCH")
    draft = adapter.draft()
    now = utc_datetime(workspace.clock.now())
    value = {"observation_id": observation_id, "configuration_digest": config.digest,
             "workspace_id": config.workspace_id, "tenant_id": config.target.tenant_id,
             "observed_at": timestamp(now), "expires_at": timestamp(now + timedelta(seconds=config.observation_seconds)),
             "observer": actor, **draft}
    with workspace.store.transaction() as connection:
        require_action(workspace, "execute")
        _save(workspace, connection, "observation", observation_id, value)
        workspace.store.append_event(connection, "TARGET_DRAFT_OBSERVED", {
            "observation_id": observation_id, "observation_digest": sha256_digest(value),
        })
    return value


def effect_options(workspace: Any, config: EffectWorkerConfig, *, after: str | None = None,
                   limit: int = 20) -> dict[str, Any]:
    require_action(workspace, "read")
    _scope(workspace, config)
    page = workspace.store.artifact_page(artifact_id_prefix="external-observation:",
                                        after=after, limit=limit, expected_media_type=_MEDIA, descending=True)
    now = utc_datetime(workspace.clock.now())
    return {"target_key": config.target.target_key, "action": DataverseDraftTarget.action,
            "owner_id": config.owner_id, "fields": {"name": {"max_length": 300}, "description": {"max_length": 2000}},
            "can_propose": _can(workspace, "propose"),
            "observations": [{**item.payload, "current": item.payload["configuration_digest"] == config.digest
                              and utc_datetime(item.payload["expires_at"]) > now} for item in page["items"]],
            "next_cursor": page["next_cursor"]}


def propose_effect(workspace: Any, config: EffectWorkerConfig, request: EffectProposalInput) -> dict[str, Any]:
    request = EffectProposalInput.model_validate(request.model_dump())
    _scope(workspace, config)
    identity = _identity(workspace, "propose")
    if not request.reason.strip():
        raise EffectError("EFFECT_REASON_REQUIRED")
    command = {"input": request.model_dump(mode="json"), "identity": identity}
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        require_action(workspace, "propose")
        prior = _optional(workspace, "proposal", request.proposal_id)
        if prior is not None:
            if prior["command_digest"] != sha256_digest(command):
                raise EffectError("EFFECT_PROPOSAL_ID_CONFLICT")
            return effect_detail(workspace, config, request.proposal_id)
        observation = _load(workspace, "observation", request.observation_id)
        if observation["configuration_digest"] != config.digest:
            raise EffectError("EFFECT_TARGET_CONFIGURATION_MISMATCH")
        _fresh(workspace, observation["expires_at"])
        quote = workspace.current_quote()
        _require_applicable_quote(workspace, quote)
        effect_id = "effect:" + sha256_digest({"tenant": config.target.tenant_id,
                    "workspace": config.workspace_id, "proposal": request.proposal_id})[7:]
        template = EffectRequest(effect_id=effect_id, tenant_id=config.target.tenant_id,
                                 target_key=config.target.target_key, action=DataverseDraftTarget.action,
                                 expected_version=observation["version"], approval_digest="sha256:" + "0" * 64,
                                 payload={"changes": request.changes})
        DataverseDraftTarget(config.target, lambda: "").validate_request(template)
        if all(observation["fields"][key] == value for key, value in request.changes.items()):
            raise EffectError("TARGET_NO_SEMANTIC_DELTA")
        now = utc_datetime(workspace.clock.now())
        value = {"proposal_id": request.proposal_id, "command_digest": sha256_digest(command),
                 "runtime": effect_runtime_revision(),
                 "organization_binding_revision": binding_revision(workspace),
                 "configuration_digest": config.digest, "observation_id": request.observation_id,
                 "observation_digest": sha256_digest(observation), "effect_id": effect_id,
                 "tenant_id": config.target.tenant_id, "workspace_id": config.workspace_id,
                 "execution_run_id": workspace.effective_workflow_run_id,
                 "quote_ref": quote.ref, "quote_digest": quote.digest,
                 "target_key": config.target.target_key, "action": template.action,
                 "expected_version": observation["version"], "changes": request.changes,
                 "previous_values": {key: observation["fields"][key] for key in request.changes},
                 "reason": request.reason, "owner_id": config.owner_id, "proposer": identity,
                 "created_at": timestamp(now), "expires_at": timestamp(now + timedelta(seconds=config.approval_seconds))}
        _save(workspace, connection, "proposal", request.proposal_id, value)
        workspace.store.append_event(connection, "EXTERNAL_OPERATION_PROPOSED", {
            "proposal_id": request.proposal_id, "proposal_digest": sha256_digest(value), "effect_id": effect_id,
        })
    return effect_detail(workspace, config, request.proposal_id)


def _validate_proposal(workspace: Any, config: EffectWorkerConfig, proposal: dict[str, Any], *, fresh: bool,
                       read_quote: Callable[[], Any] | None = None,
                       read_source_gaps: Callable[[], Any] | None = None,
                       read_runtime: Callable[[], dict[str, Any]] | None = None,
                       read_binding: Callable[[], dict[str, str]] | None = None) -> None:
    _scope(workspace, config)
    if (proposal["configuration_digest"] != config.digest or proposal["owner_id"] != config.owner_id
            or proposal["workspace_id"] != config.workspace_id or proposal["tenant_id"] != config.target.tenant_id):
        raise EffectError("EFFECT_PROPOSAL_BINDING_MISMATCH")
    if fresh:
        lock_binding_scope(workspace)
        organization = (read_binding or (lambda: binding_context(workspace)))()
        expected_binding = proposal.get("organization_binding_revision", organization["seed_digest"])
        if expected_binding != organization["revision"]:
            raise EffectError("EFFECT_OWNER_BINDING_REPLAN_REQUIRED")
        if proposal.get("runtime") != (read_runtime or effect_runtime_revision)():
            raise EffectError("EFFECT_RUNTIME_REPLAN_REQUIRED")
        _fresh(workspace, proposal["expires_at"])
        quote = (read_quote or workspace.current_quote)()
        if (quote.ref != proposal["quote_ref"] or quote.digest != proposal["quote_digest"]
                or workspace.effective_workflow_run_id != proposal["execution_run_id"]):
            raise EffectError("EFFECT_SOURCE_QUOTE_CHANGED")
        _require_applicable_quote(workspace, quote, read_source_gaps=read_source_gaps)


def approve_effect(workspace: Any, config: EffectWorkerConfig, proposal_id: str,
                   request: EffectApprovalInput) -> dict[str, Any]:
    request = EffectApprovalInput.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        owner = _owner(workspace, config)
        proposal = _load(workspace, "proposal", proposal_id)
        _validate_proposal(workspace, config, proposal, fresh=True)
        if sha256_digest(proposal) != request.proposal_digest:
            raise EffectError("EFFECT_PROPOSAL_DIGEST_MISMATCH")
        if _optional(workspace, "rejection", proposal_id) is not None:
            raise EffectError("EFFECT_PROPOSAL_REJECTED")
        if _optional(workspace, "approval", proposal_id) is None:
            approval = {"proposal_id": proposal_id, "proposal_digest": request.proposal_digest,
                        "identity": owner, "approved_at": workspace.clock.now(), "expires_at": proposal["expires_at"]}
            approval_digest = _save(workspace, connection, "approval", proposal_id, approval)
            effect = EffectRequest(effect_id=proposal["effect_id"], tenant_id=proposal["tenant_id"],
                                   target_key=proposal["target_key"], action=proposal["action"],
                                   expected_version=proposal["expected_version"], approval_digest=approval_digest,
                                   payload={"changes": proposal["changes"]})
            workspace.store.put_effect(connection, effect_id=effect.effect_id, target_key=effect.barrier_key,
                                       request_digest=effect.digest, request=effect.model_dump(mode="json"),
                                       created_at=workspace.clock.now())
            _save(workspace, connection, "effect-binding", effect.effect_id, {"proposal_id": proposal_id})
            workspace.store.append_event(connection, "EXTERNAL_OPERATION_APPROVED", {
                "effect_id": effect.effect_id, "approval_digest": approval_digest, "request_digest": effect.digest,
            })
    return effect_detail(workspace, config, proposal_id)


def reject_effect(workspace: Any, config: EffectWorkerConfig, proposal_id: str,
                  request: EffectApprovalInput) -> dict[str, Any]:
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        owner = _owner(workspace, config)
        proposal = _load(workspace, "proposal", proposal_id)
        _validate_proposal(workspace, config, proposal, fresh=False)
        if sha256_digest(proposal) != request.proposal_digest:
            raise EffectError("EFFECT_PROPOSAL_DIGEST_MISMATCH")
        if _optional(workspace, "approval", proposal_id) is not None:
            raise EffectError("EFFECT_APPROVED_PROPOSAL_REJECTION_FORBIDDEN")
        prior = _optional(workspace, "rejection", proposal_id)
        if prior is None:
            _save(workspace, connection, "rejection", proposal_id, {
                "proposal_digest": request.proposal_digest, "identity": owner, "rejected_at": workspace.clock.now(),
            })
            workspace.store.append_event(connection, "EXTERNAL_OPERATION_REJECTED", {"proposal_id": proposal_id})
    return effect_detail(workspace, config, proposal_id)


def effect_detail(workspace: Any, config: EffectWorkerConfig, proposal_id: str) -> dict[str, Any]:
    require_action(workspace, "read")
    _scope(workspace, config)
    proposal = _load(workspace, "proposal", proposal_id)
    approval = _optional(workspace, "approval", proposal_id)
    rejection = _optional(workspace, "rejection", proposal_id)
    record = workspace.store.get_effect(proposal["effect_id"])
    return _effect_detail(workspace, config, proposal, approval, rejection, record,
                          read_quote=workspace.current_quote, read_source_gaps=workspace.changes.gaps,
                          read_runtime=effect_runtime_revision)


def _effect_detail(workspace: Any, config: EffectWorkerConfig, proposal: dict[str, Any],
                   approval: dict[str, Any] | None, rejection: dict[str, Any] | None,
                   record: dict[str, Any] | None, *, read_quote: Callable[[], Any],
                   read_source_gaps: Callable[[], Any],
                   read_runtime: Callable[[], dict[str, Any]],
                   read_binding: Callable[[], dict[str, str]] | None = None) -> dict[str, Any]:
    require_action(workspace, "read")
    state = record["state"] if record else "REJECTED" if rejection else "PENDING_APPROVAL"
    blocked_reason = None
    try:
        _validate_proposal(workspace, config, proposal, fresh=state in {"READY", "PENDING_APPROVAL"},
                           read_quote=read_quote, read_source_gaps=read_source_gaps, read_runtime=read_runtime,
                           read_binding=read_binding)
    except EffectError as error:
        blocked_reason = str(error)
    principal = request_principal.get()
    owner = (principal.actor_id if principal else "operator:controlled-local") == config.owner_id
    actions = []
    if state == "PENDING_APPROVAL" and not blocked_reason and owner and _can(workspace, "approve"):
        actions.extend(["APPROVE", "REJECT"])
    if record and not record["requested_action"]:
        if state == "READY" and not blocked_reason and _can(workspace, "execute"):
            actions.append("EXECUTE")
        if state == "COMMIT_UNKNOWN" and not blocked_reason:
            if _can(workspace, "execute"):
                actions.append("QUERY")
            if owner and _can(workspace, "approve"):
                actions.append("CANCEL")
    if record and state == "READY" and owner and _can(workspace, "approve"):
        actions.append("CANCEL")
    return {"proposal": proposal, "proposal_digest": sha256_digest(proposal), "state": state,
            "approval": approval, "rejection": rejection, "allowed_actions": actions,
            "blocked_reason": blocked_reason, "effect": record}


def list_effect_proposals(workspace: Any, config: EffectWorkerConfig, *, after: str | None = None,
                         limit: int = 30) -> dict[str, Any]:
    require_action(workspace, "read")
    _scope(workspace, config)
    page = workspace.store.artifact_page(artifact_id_prefix=_PROPOSALS, after=after, limit=limit, expected_media_type=_MEDIA)
    proposals = [item.payload for item in page["items"]]
    if any(item.artifact_id != _key("proposal", item.payload["proposal_id"]) for item in page["items"]):
        raise EffectError("EFFECT_PROPOSAL_ID_MISMATCH")
    related = {key: artifact.payload for key, artifact in workspace.store.load_artifacts(
        [_key(kind, proposal["proposal_id"]) for proposal in proposals for kind in ("approval", "rejection")],
        _MEDIA,
    ).items()}
    records = workspace.store.get_effects([proposal["effect_id"] for proposal in proposals])
    # A list is a read view; authority is checked again by each submitted command.
    read_quote, read_runtime = cache(workspace.current_quote), cache(effect_runtime_revision)
    read_source_gaps = cache(workspace.changes.gaps)
    read_binding = cache(lambda: binding_context(workspace))
    return {"items": [_effect_detail(
                workspace, config, proposal,
                related.get(_key("approval", proposal["proposal_id"])),
                related.get(_key("rejection", proposal["proposal_id"])),
                records.get(proposal["effect_id"]), read_quote=read_quote,
                read_source_gaps=read_source_gaps, read_runtime=read_runtime, read_binding=read_binding,
            ) for proposal in proposals],
            "next_cursor": page["next_cursor"]}


def request_effect_action(workspace: Any, config: EffectWorkerConfig, proposal_id: str,
                          request: EffectActionInput) -> dict[str, Any]:
    request = EffectActionInput.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        actor = _owner(workspace, config) if request.action == "CANCEL" else _identity(workspace, "execute")
        proposal = _load(workspace, "proposal", proposal_id)
        _validate_proposal(workspace, config, proposal, fresh=request.action == "EXECUTE")
        effect = workspace.store.get_effect(proposal["effect_id"], connection=connection)
        if effect is None or effect["request_digest"] != request.request_digest:
            raise EffectError("EFFECT_REQUEST_BINDING_MISMATCH")
        if request.action == "CANCEL" and effect["state"] == "READY":
            workspace.store.update_effect(
                connection, effect_id=effect["effect_id"], expected_state="READY", state="REJECTED",
                updated_at=workspace.clock.now(), error_code="EFFECT_CANCELLED_BEFORE_DISPATCH",
                result={"target_writes": 0, "not_dispatched": True, "cancelled_by": actor["actor_id"]},
            )
            if workspace.store.get_target_barrier(effect["target_key"], connection=connection) == effect["effect_id"]:
                workspace.store.release_target_barrier(connection, target_key=effect["target_key"], effect_id=effect["effect_id"])
            workspace.store.append_event(connection, "EXTERNAL_OPERATION_CANCELLED_BEFORE_DISPATCH", {
                "effect_id": effect["effect_id"], "request_digest": request.request_digest, "identity": actor,
            })
            return effect_detail(workspace, config, proposal_id)
        queued = workspace.store.request_effect_action(connection, effect_id=effect["effect_id"],
                    request_digest=request.request_digest, action=request.action,
                    expected_state="READY" if request.action == "EXECUTE" else "COMMIT_UNKNOWN")
        command_id = f"{effect['effect_id']}:{queued['command_revision']}"
        prior = _optional(workspace, "command", command_id)
        if prior is not None:
            if prior["identity"] != actor:
                raise EffectError("EFFECT_COMMAND_IDENTITY_CONFLICT")
        else:
            now = utc_datetime(workspace.clock.now())
            _save(workspace, connection, "command", command_id, {
                "effect_id": effect["effect_id"], "request_digest": request.request_digest,
                "action": request.action, "revision": queued["command_revision"], "identity": actor,
                "issued_at": timestamp(now), "expires_at": timestamp(now + timedelta(minutes=5)),
            })
            workspace.store.append_event(connection, "EXTERNAL_OPERATION_QUEUED", {
                "effect_id": effect["effect_id"], "action": request.action, "revision": queued["command_revision"],
            })
    return effect_detail(workspace, config, proposal_id)
