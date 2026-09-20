"""OrgRebase Workspace: enterprise work build and recovery runtime."""

from orgrebase.workspace.models import TaskRequest, WorkspaceChangeSpec

__all__ = [
    "EnterpriseSeedAdmissionReceipt",
    "EnterpriseSeedGap",
    "EnterpriseSeedProfile",
    "EnterpriseSeedRuntimeProjectionReceipt",
    "EnterpriseSeedSourceAdmissionReceipt",
    "OACRuntimeAdmissionBridge",
    "TaskRequest",
    "WorkspaceChangeSpec",
    "WorkspaceLiveFormationService",
    "WorkspaceService",
    "admit_enterprise_seed_profile",
    "admit_enterprise_seed_sources",
    "northstar_acme_quote_profile",
]


def __getattr__(name: str):
    if name == "WorkspaceService":
        from orgrebase.workspace.service import WorkspaceService

        return WorkspaceService
    if name == "WorkspaceLiveFormationService":
        from orgrebase.workspace.live_formation import WorkspaceLiveFormationService

        return WorkspaceLiveFormationService
    if name == "OACRuntimeAdmissionBridge":
        from orgrebase.workspace.oac_bridge import OACRuntimeAdmissionBridge

        return OACRuntimeAdmissionBridge
    if name in {
        "EnterpriseSeedAdmissionReceipt",
        "EnterpriseSeedGap",
        "EnterpriseSeedProfile",
        "admit_enterprise_seed_profile",
        "northstar_acme_quote_profile",
    }:
        from orgrebase.workspace import profile

        return getattr(profile, name)
    if name in {
        "EnterpriseSeedRuntimeProjectionReceipt",
        "EnterpriseSeedSourceAdmissionReceipt",
        "admit_enterprise_seed_sources",
    }:
        from orgrebase.workspace import source_admission

        return getattr(source_admission, name)
    raise AttributeError(name)
