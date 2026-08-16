"""OrgRebase Workspace: enterprise work build and recovery runtime."""

from orgrebase.workspace.models import TaskRequest, WorkspaceChangeSpec

__all__ = ["TaskRequest", "WorkspaceChangeSpec", "WorkspaceService"]


def __getattr__(name: str):
    if name == "WorkspaceService":
        from orgrebase.workspace.service import WorkspaceService

        return WorkspaceService
    raise AttributeError(name)
