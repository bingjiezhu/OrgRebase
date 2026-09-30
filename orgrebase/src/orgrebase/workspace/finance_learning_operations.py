"""Authenticated Finance Skill governance and evaluation entrypoints.

The head and content remain owned by the existing Pattern/StateStore boundary.
This module only connects that boundary to the current Workspace Principal.
"""

from __future__ import annotations

from typing import Any

from orgrebase.auth import request_principal
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.skill_evolution_v2 import (
    FinanceSkillHeadService,
    FinanceSkillResolution,
    SkillContentBundleV2,
)


def _head_view(value: FinanceSkillResolution) -> dict[str, Any]:
    return {
        "profile_id": "workspace-change-explanation-v1",
        "head_ref": value.head_ref,
        "head_digest": value.head_digest,
        "generation": value.generation,
        "package_digest": value.package_digest,
        "resource_digests": value.bundle.resource_digests,
        "qualification_status": value.qualification_status,
        "adoption_enabled": value.adoption_enabled,
        "consumer_contract": "workspace-change-advisory@4.0.0",
    }


def finance_skill_head(workspace: Any) -> dict[str, Any]:
    require_action(workspace, "read")
    return _head_view(FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id,
    ).resolve())


def bootstrap_finance_skill(workspace: Any) -> dict[str, Any]:
    actor_id = require_action(workspace, "govern")
    service = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id,
    )
    try:
        return _head_view(service.bootstrap())
    except IntegrityError as error:
        if str(error) != "CURRENT_POINTER_ALREADY_EXISTS":
            raise
        # A lost bootstrap response can be read back only by its exact
        # authenticated governor. A later generation is never a replay.
        require_action(workspace, "govern")
        principal = request_principal.get()
        current = service.resolve()
        source = workspace.store.get_object(service.head_id)
        if (
            principal is None
            or principal.actor_id != actor_id
            or source.payload.get("actor_id") != actor_id
            or current.generation != 0
            or current.bundle.digest != SkillContentBundleV2.static_baseline(service.policy).digest
        ):
            raise error
        return _head_view(current)
