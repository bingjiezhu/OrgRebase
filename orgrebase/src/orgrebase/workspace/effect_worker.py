"""Execute approved, queued target commands with worker-owned credentials."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.clock import utc_datetime
from orgrebase.commit_gateway import CommitGateway, EffectError, EffectRequest
from orgrebase.dataverse_target import DataverseDraftTarget
from orgrebase.digest import sha256_digest
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.effects import (
    EffectWorkerConfig,
    _fresh,
    _load,
    _scope,
    _validate_proposal,
    load_effect_config,
    record_target_observation,
)

VerifyMember = Callable[[str, str, str], None]
TargetFactory = Callable[[Callable[[], None]], DataverseDraftTarget]


class _CommandTarget:
    def __init__(self, adapter: DataverseDraftTarget, action: str) -> None:
        self.adapter = adapter
        self.action = action

    def execute(self, effect: EffectRequest):
        return self.adapter.execute(effect)

    def query_effect(self, effect: EffectRequest):
        return (self.adapter.cancel_effect(effect) if self.action == "CANCEL"
                else self.adapter.query_effect(effect))


def process_effect_commands(workspace: Any, config: EffectWorkerConfig, *,
                            create_target: TargetFactory, verify_member: VerifyMember,
                            worker_id: str, max_commands: int = 20,
                            qualify_target: Callable[[DataverseDraftTarget], None] | None = None) -> dict[str, Any]:
    _scope(workspace, config)
    require_action(workspace, "execute")
    if isinstance(max_commands, bool) or not 1 <= max_commands <= 100:
        raise ValueError("EFFECT_COMMAND_BUDGET_INVALID")
    return {"workspace_id": config.workspace_id, "commands": [
        _process_command(workspace, config, pending, create_target=create_target,
                         verify_member=verify_member, worker_id=worker_id, qualify_target=qualify_target)
        for pending in workspace.store.pending_effects(limit=max_commands)
    ]}


def _process_command(workspace: Any, config: EffectWorkerConfig, pending: dict[str, Any], *,
                     create_target: TargetFactory, verify_member: VerifyMember,
                     worker_id: str, qualify_target: Callable[[DataverseDraftTarget], None] | None) -> dict[str, Any]:
    effect_id, revision, action = pending["effect_id"], pending["command_revision"], pending["requested_action"]
    error_code = None
    keep_pending = False
    try:
        effect = EffectRequest.model_validate(pending["request"])
        proposal_id = _load(workspace, "effect-binding", effect_id)["proposal_id"]
        proposal = _load(workspace, "proposal", proposal_id)
        approval = _load(workspace, "approval", proposal_id)
        command = _load(workspace, "command", f"{effect_id}:{revision}")
        io_action = "effect.reconcile"

        def check() -> None:
            require_action(workspace, "execute")
            _scope(workspace, config)
            _validate_proposal(workspace, config, proposal, fresh=io_action == "effect.execute")
            current = workspace.store.get_effect(effect_id)
            if (current is None or current["command_revision"] != revision
                    or current["requested_action"] != action or current["request_digest"] != effect.digest):
                raise EffectError("EFFECT_COMMAND_CHANGED")
            if (command["effect_id"] != effect_id or command["request_digest"] != effect.digest
                    or command["revision"] != revision or command["action"] != action):
                raise EffectError("EFFECT_COMMAND_BINDING_MISMATCH")
            _fresh(workspace, command["expires_at"])
            owner = approval["identity"]
            if (sha256_digest(approval) != effect.approval_digest
                    or approval["proposal_digest"] != sha256_digest(proposal)
                    or owner["actor_id"] != config.owner_id
                    or effect.target_key != proposal["target_key"]
                    or effect.expected_version != proposal["expected_version"]
                    or effect.action != proposal["action"] or effect.payload != {"changes": proposal["changes"]}):
                raise EffectError("EFFECT_APPROVAL_BINDING_MISMATCH")
            principal = request_principal.get()
            issuer = principal.issuer if principal else "controlled-local"
            command_actor = command["identity"]
            if command_actor["issuer"] != issuer or owner["issuer"] != issuer:
                raise EffectError("EFFECT_IDENTITY_ISSUER_MISMATCH")
            verify_member(command_actor["subject"], command_actor["actor_id"],
                          "approve" if action == "CANCEL" else "execute")
            check_scope = getattr(workspace, "authorize_workspace_subject", None)
            if check_scope is not None:
                check_scope(command_actor["subject"])
            if action == "CANCEL" and command_actor["actor_id"] != config.owner_id:
                raise EffectError("EFFECT_OWNER_REQUIRED")
            if io_action == "effect.execute":
                _fresh(workspace, approval["expires_at"])
                verify_member(owner["subject"], owner["actor_id"], "approve")
                if check_scope is not None:
                    check_scope(owner["subject"])

        def authorize(effect_request: EffectRequest, requested_action: str) -> None:
            nonlocal io_action
            if effect_request.digest != effect.digest:
                raise EffectError("EFFECT_REQUEST_BINDING_MISMATCH")
            io_action = requested_action
            check()

        with create_target(check) as adapter:
            if adapter.settings != config.target:
                raise EffectError("EFFECT_TARGET_CONFIGURATION_MISMATCH")
            if qualify_target is not None and (action == "CANCEL" or pending["state"] == "READY"):
                qualify_target(adapter)
            gateway = CommitGateway(workspace.store, tenant_id=config.target.tenant_id,
                                    worker_id=worker_id, authorize=authorize, clock=workspace.clock)
            gateway.run(effect, _CommandTarget(adapter, action), query_only=action in {"QUERY", "CANCEL"})
    except (AuthenticationError, EffectError, KeyError, ValueError) as error:
        error_code = error.code if isinstance(error, AuthenticationError) else str(error).split(":", 1)[0]
        if isinstance(error, KeyError):
            error_code = "EFFECT_AUTHORITY_RECORD_MISSING"
        keep_pending = error_code in {"EFFECT_IN_PROGRESS", "STALE_EFFECT_ATTEMPT", "EFFECT_COMMAND_CHANGED"}
    if not keep_pending:
        with workspace.store.transaction() as connection:
            current = workspace.store.get_effect(effect_id, connection=connection)
            now = utc_datetime(workspace.clock.now())
            if (current["state"] in {"DISPATCHING", "COMMIT_UNKNOWN"}
                    and (current["state"] == "DISPATCHING" or current["lease_owner"] is not None)
                    and current["command_revision"] == revision
                    and (current["lease_until"] is None or current["lease_until"] <= now.timestamp())):
                require_action(workspace, "execute")
                claim = workspace.store.claim_effect(
                    connection, effect_id=effect_id, worker_id=worker_id, now=now.timestamp(),
                    lease_seconds=60, allowed_states=("DISPATCHING", "COMMIT_UNKNOWN"),
                )
                if claim is not None:
                    # Expired authority cannot send a request, but a new fenced
                    # owner can expose the interrupted attempt for reconciliation.
                    workspace.store.update_effect(
                        connection, effect_id=effect_id, expected_state=current["state"],
                        state="COMMIT_UNKNOWN", updated_at=workspace.clock.now(),
                        result=current["result"], error_code="EFFECT_RECONCILIATION_REQUIRED",
                        expected_fence=claim["fence"],
                    )
                    workspace.store.release_effect_claim(connection, effect_id=effect_id,
                                                         worker_id=worker_id, fence=claim["fence"])
                    current = workspace.store.get_effect(effect_id, connection=connection)
            if current["command_revision"] != revision or (
                current["state"] not in {"CONFIRMED", "REJECTED"}
                and (current["requested_action"] != action or current["lease_owner"] is not None)
            ):
                # An unclaimed observer may not cancel another attempt, even if
                # its lease elapsed while that attempt was waiting on target I/O.
                keep_pending = current["requested_action"] is not None
            else:
                workspace.store.clear_effect_action(connection, effect_id=effect_id, expected_command_revision=revision)
                workspace.store.append_event(connection, "EXTERNAL_OPERATION_COMMAND_FINISHED", {
                    "effect_id": effect_id, "revision": revision, "action": action,
                    "state": current["state"], "error_code": error_code,
                })
    current = workspace.store.get_effect(effect_id)
    return {"effect_id": effect_id, "revision": revision, "action": action,
                    "state": current["state"], "error_code": error_code, "pending": current["requested_action"] is not None}


def run_effect_worker(config_path: Path, *, observe: bool = False, max_commands: int = 20) -> dict[str, Any]:
    from orgrebase.auth import JWTAuthenticator, authorize, request_authorization
    from orgrebase.runtime_config import DeploymentSettings, open_workspace

    config = load_effect_config(config_path)
    settings = DeploymentSettings.from_environment()
    if settings.mode != "production":
        raise EffectError("EFFECT_WORKER_REQUIRES_AUTHENTICATED_DEPLOYMENT")
    if config.organization_id is None:
        raise EffectError("TARGET_ORGANIZATION_ID_REQUIRED")
    settings = settings.for_workspace(config.workspace_id)
    if settings.effect_config is None or config_path.resolve() != settings.effect_config.resolve():
        raise EffectError("EFFECT_CONFIGURATION_PATH_MISMATCH")
    authenticator = JWTAuthenticator(settings.identity)
    principal = authenticator.authenticate(f"Bearer {os.environ.get(config.access_token_variable, '')}")
    authorize(principal, "execute", config.target.tenant_id)
    settings.authorize_workspace(principal.subject)

    def check_identity() -> None:
        renewed = authenticator.authenticate(f"Bearer {os.environ.get(config.access_token_variable, '')}")
        if (renewed.issuer, renewed.subject, renewed.tenant_id, renewed.actor_id) != (
                principal.issuer, principal.subject, principal.tenant_id, principal.actor_id):
            raise EffectError("EFFECT_WORKER_IDENTITY_CHANGED")
        authorize(renewed, "execute", config.target.tenant_id)
        settings.authorize_workspace(renewed.subject)
        if load_effect_config(config_path).digest != config.digest:
            raise EffectError("EFFECT_CONFIGURATION_CHANGED")
        request_principal.set(renewed)

    def create_target(check: Callable[[], None]) -> DataverseDraftTarget:
        def token() -> str:
            check()
            return os.environ.get(config.target_token_variable, "")
        return DataverseDraftTarget(config.target, token)

    workspace = open_workspace(settings)
    authorization_token = request_authorization.set(check_identity)
    principal_token = request_principal.set(principal)

    def qualify_target(target: DataverseDraftTarget) -> None:
        from orgrebase.dataverse_qualification import probe_target
        report = probe_target(target, organization_id=config.organization_id)
        with workspace.store.transaction() as connection:
            check_identity()
            workspace.store.save_artifact(connection, "target-metadata:" + report["report_digest"][7:],
                                          "application/vnd.orgrebase.target-metadata+json", report)
            workspace.store.append_event(connection, "TARGET_METADATA_CHECKED", {
                "target_key": config.target.target_key, "report_digest": report["report_digest"],
                "metadata_status": report["metadata_status"],
            })
        if report["metadata_status"] != "PASS":
            raise EffectError("TARGET_METADATA_QUALIFICATION_FAILED")

    try:
        _scope(workspace, config)
        if observe:
            with create_target(check_identity) as target:
                qualify_target(target)
                observation_id = utc_datetime(workspace.clock.now()).strftime("%Y%m%dT%H%M%S%fZ") + ":" + str(uuid4())
                value = record_target_observation(workspace, config, target, observation_id)
            return {"workspace_id": config.workspace_id, "observation_id": value["observation_id"],
                    "observation_digest": sha256_digest(value), "target_writes": 0}
        return process_effect_commands(workspace, config, create_target=create_target,
                                       verify_member=authenticator.verify_membership,
                                       worker_id=f"effect-worker:{uuid4()}", max_commands=max_commands,
                                       qualify_target=qualify_target)
    finally:
        request_principal.reset(principal_token)
        request_authorization.reset(authorization_token)
        workspace.close()
