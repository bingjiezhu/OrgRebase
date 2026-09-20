"""HTTP views and owner decisions; source credentials stay in the worker."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from orgrebase.workspace.routes import _respond
from orgrebase.workspace.source_bindings import (
    SourceBindingConfirmation,
    SourceBindingProposal,
    binding_view,
    confirm_binding,
    load_source_config,
    propose_binding,
)


def source_binding_router(get_workspace: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/workspace/source-binding")

    def configured():
        workspace = get_workspace()
        return workspace, load_source_config(getattr(workspace, "source_config_path", None))

    @router.get("")
    def view():
        return _respond(lambda: binding_view(*configured()))

    @router.post("/proposals")
    def propose(request: SourceBindingProposal):
        return _respond(lambda: propose_binding(*configured(), request))

    @router.post("/proposals/{proposal_id}/confirm")
    def confirm(proposal_id: str, request: SourceBindingConfirmation):
        return _respond(lambda: confirm_binding(*configured(), proposal_id, request))

    @router.post("/proposals/{proposal_id}/revoke")
    def revoke(proposal_id: str, request: SourceBindingConfirmation):
        return _respond(lambda: confirm_binding(*configured(), proposal_id, request, revoke=True))

    return router
