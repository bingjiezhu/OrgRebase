"""HTTP translations for business editing and operational detail views."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from orgrebase.auth import AuthenticationError
from orgrebase.commit_gateway import EffectError
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError
from orgrebase.http_errors import public_http_error as public_error
from orgrebase.workspace.approval_authority import (
    CoordinationInput,
    DelegationInput,
    escalate_change,
    grant_delegation,
    revoke_delegation,
)
from orgrebase.workspace.change_proposals import (
    ChangeProposalInput,
    ReviewObservationInput,
    change_detail,
    change_options,
    record_review,
    require_action,
    submit_change,
)
from orgrebase.workspace.change_recovery import (
    ResumeChangeInput,
    ReturnForEvidenceInput,
    recovery_detail,
    resume_change,
    return_for_evidence,
)
from orgrebase.workspace.effects import (
    EffectActionInput,
    EffectApprovalInput,
    EffectProposalInput,
    approve_effect,
    effect_detail,
    effect_options,
    list_effect_proposals,
    load_effect_config,
    propose_effect,
    reject_effect,
    request_effect_action,
)
from orgrebase.workspace.owner_change import (
    OwnerChangeCommand,
    OwnerChangePreviewInput,
    OwnerChangeProposalInput,
    activate_owner_change,
    confirm_owner_change,
    list_owner_changes,
    owner_change_detail,
    owner_change_options,
    preview_owner_change,
    propose_owner_change,
)
from orgrebase.workspace.read_dependencies import ReadQuery, capture_read_witness, dependency_payload
from orgrebase.workspace.source_readmission import (
    SourceReadmissionCommand,
    SourceReadmissionInput,
    apply_group,
    approve_group,
    create_group,
    group_attempt_detail,
    group_detail,
    group_options,
    list_groups,
    reject_group,
)


def _respond(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except AuthenticationError as error:
        raise HTTPException(error.status_code, detail={"code": error.code}) from None
    except AuthorizationError as error:
        raise HTTPException(403, detail=public_error(error, message_as_code=True)) from None
    except KeyError:
        raise HTTPException(404, detail={"code": "WORKSPACE_RECORD_NOT_FOUND"}) from None
    except (IntegrityError, FreshnessError, ValueError, RuntimeError) as error:
        raise HTTPException(409, detail=public_error(error, message_as_code=True)) from None


def change_router(get_workspace: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/workspace")

    @router.post("/read-witnesses")
    def read_witness(payload: ReadQuery) -> dict[str, Any]:
        def capture():
            workspace = get_workspace()
            with workspace._command_lock, workspace.store.transaction() as connection:
                require_action(workspace, "propose")
                witness = capture_read_witness(workspace, payload, connection=connection)
                return {"witness": witness.model_dump(mode="json"), "read_dependencies": dependency_payload(witness)}
        return _respond(capture)

    def configured_target():
        path = getattr(get_workspace(), "effect_config_path", None)
        if path is None:
            raise EffectError("EFFECT_CONFIGURATION_UNAVAILABLE")
        return load_effect_config(path)

    @router.get("/source-readmission-options")
    def source_group_options() -> dict[str, Any]:
        return _respond(lambda: group_options(get_workspace()))

    @router.get("/source-readmission-groups")
    def source_groups(after: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: list_groups(get_workspace(), after=after, limit=limit))

    @router.post("/source-readmission-groups")
    def source_group_create(payload: SourceReadmissionInput) -> dict[str, Any]:
        return _respond(lambda: create_group(get_workspace(), payload))

    @router.get("/source-readmission-groups/{group_id}")
    def source_group_read(group_id: str) -> dict[str, Any]:
        return _respond(lambda: group_detail(get_workspace(), group_id))

    @router.get("/source-readmission-groups/{group_id}/attempt")
    def source_group_attempt_read(group_id: str) -> dict[str, Any]:
        return _respond(lambda: group_attempt_detail(get_workspace(), group_id))

    @router.post("/source-readmission-groups/{group_id}/approve")
    def source_group_approve(group_id: str, payload: SourceReadmissionCommand) -> dict[str, Any]:
        return _respond(lambda: approve_group(get_workspace(), group_id, payload))

    @router.post("/source-readmission-groups/{group_id}/reject")
    def source_group_reject(group_id: str, payload: SourceReadmissionCommand) -> dict[str, Any]:
        return _respond(lambda: reject_group(get_workspace(), group_id, payload))

    @router.post("/source-readmission-groups/{group_id}/apply")
    def source_group_apply(group_id: str, payload: SourceReadmissionCommand) -> dict[str, Any]:
        return _respond(lambda: apply_group(get_workspace(), group_id, payload))

    @router.get("/change-options")
    def options() -> dict[str, Any]:
        return _respond(lambda: change_options(get_workspace()))

    @router.post("/change-proposals")
    def propose(payload: ChangeProposalInput) -> dict[str, Any]:
        return _respond(lambda: submit_change(get_workspace(), payload))

    @router.get("/organization/owner-change-options")
    def owner_options() -> dict[str, Any]:
        return _respond(lambda: owner_change_options(get_workspace()))

    @router.get("/organization/owner-changes")
    def owner_list(after: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: list_owner_changes(get_workspace(), after=after, limit=limit))

    @router.post("/organization/owner-changes")
    def owner_proposal(payload: OwnerChangeProposalInput) -> dict[str, Any]:
        return _respond(lambda: propose_owner_change(get_workspace(), payload))

    @router.get("/organization/owner-changes/{proposal_id}")
    def owner_detail(proposal_id: str) -> dict[str, Any]:
        return _respond(lambda: owner_change_detail(get_workspace(), proposal_id))

    @router.post("/organization/owner-changes/{proposal_id}/confirm")
    def owner_confirm(proposal_id: str, payload: OwnerChangeCommand) -> dict[str, Any]:
        return _respond(lambda: confirm_owner_change(get_workspace(), proposal_id, payload))

    @router.post("/organization/owner-changes/{proposal_id}/activate")
    def owner_activate(proposal_id: str, payload: OwnerChangeCommand) -> dict[str, Any]:
        return _respond(lambda: activate_owner_change(get_workspace(), proposal_id, payload))

    @router.post("/organization/owner-change-preview")
    def owner_change_preview(payload: OwnerChangePreviewInput) -> dict[str, Any]:
        return _respond(lambda: preview_owner_change(get_workspace(), payload).model_dump(mode="json"))

    @router.get("/changes/{event_id}")
    def detail(event_id: str) -> dict[str, Any]:
        return _respond(lambda: change_detail(get_workspace(), event_id))

    @router.post("/changes/{event_id}/review-observation")
    def observe(event_id: str, payload: ReviewObservationInput) -> dict[str, Any]:
        return _respond(lambda: record_review(get_workspace(), event_id, payload))

    @router.get("/changes/{event_id}/recovery")
    def recovery(event_id: str) -> dict[str, Any]:
        return _respond(lambda: recovery_detail(get_workspace(), event_id))

    @router.post("/changes/{event_id}/return-for-evidence")
    def return_evidence(event_id: str, payload: ReturnForEvidenceInput) -> dict[str, Any]:
        return _respond(lambda: return_for_evidence(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/resume")
    def resume(event_id: str, payload: ResumeChangeInput) -> dict[str, Any]:
        return _respond(lambda: resume_change(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/delegate")
    def delegate(event_id: str, payload: DelegationInput) -> dict[str, Any]:
        return _respond(lambda: grant_delegation(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/revoke-delegation")
    def revoke(event_id: str, payload: CoordinationInput) -> dict[str, Any]:
        return _respond(lambda: revoke_delegation(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/escalate")
    def escalate(event_id: str, payload: CoordinationInput) -> dict[str, Any]:
        return _respond(lambda: escalate_change(get_workspace(), event_id, payload))

    @router.get("/effect-options")
    def target_options(after: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: effect_options(get_workspace(), configured_target(), after=after, limit=limit))

    @router.get("/effect-proposals")
    def effects(after: str | None = None, limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: list_effect_proposals(get_workspace(), configured_target(), after=after, limit=limit))

    @router.post("/effect-proposals")
    def effect_propose(payload: EffectProposalInput) -> dict[str, Any]:
        return _respond(lambda: propose_effect(get_workspace(), configured_target(), payload))

    @router.get("/effect-proposals/{proposal_id}")
    def effect_read(proposal_id: str) -> dict[str, Any]:
        return _respond(lambda: effect_detail(get_workspace(), configured_target(), proposal_id))

    @router.post("/effect-proposals/{proposal_id}/approve")
    def effect_approve(proposal_id: str, payload: EffectApprovalInput) -> dict[str, Any]:
        return _respond(lambda: approve_effect(get_workspace(), configured_target(), proposal_id, payload))

    @router.post("/effect-proposals/{proposal_id}/reject")
    def effect_reject(proposal_id: str, payload: EffectApprovalInput) -> dict[str, Any]:
        return _respond(lambda: reject_effect(get_workspace(), configured_target(), proposal_id, payload))

    @router.post("/effect-proposals/{proposal_id}/actions")
    def effect_action(proposal_id: str, payload: EffectActionInput) -> dict[str, Any]:
        return _respond(lambda: request_effect_action(get_workspace(), configured_target(), proposal_id, payload))

    return router
