from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from orgrebase.domain import Approval
from orgrebase.workspace.models import OACRuntimeCapsule, WorkspacePreviewBundle
from orgrebase.workspace.oac_bridge import OACBlackBoxCLI
from orgrebase.workspace.service import WorkspaceService


@pytest.fixture
def workspace_db(tmp_path: Path) -> Path:
    return tmp_path / "workspace.sqlite"


@pytest.fixture
def workspace_service(workspace_db: Path) -> Iterator[WorkspaceService]:
    service = WorkspaceService(store_path=workspace_db)
    try:
        yield service
    finally:
        with suppress(Exception):
            service.close()


@pytest.fixture
def formed_service(workspace_service: WorkspaceService) -> WorkspaceService:
    workspace_service.form_quote()
    return workspace_service


@pytest.fixture
def apply_staged_change():
    def apply(service: WorkspaceService, kind: str) -> dict[str, Any]:
        preview_record = service.preview_command(kind)
        bundle = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
        approval_record = service.approve_change(
            kind,
            actor_id=bundle.change_spec.owner_id,
            preview_digest=bundle.preview.digest,
        )
        approval = Approval.model_validate(approval_record["approval"])
        service.apply_approved_change(kind, approval_digest=approval.digest)
        result = service._recover_apply_result(bundle=bundle, approval=approval)
        assert result is not None
        return result

    return apply


@pytest.fixture
def persistent_workspace(tmp_path: Path) -> Iterator[tuple[WorkspaceService, Path]]:
    path = tmp_path / "persistent-workspace.sqlite"
    service = WorkspaceService(store_path=path)
    try:
        yield service, path
    finally:
        with suppress(Exception):
            service.close()


@pytest.fixture(scope="module")
def current_oac_capsules() -> dict[str, OACRuntimeCapsule]:
    """Build current admission inputs through the public CLI without rewriting archives."""

    cli = OACBlackBoxCLI()
    return {case: cli.build_capsule(case) for case in ("BASE", "SPLIT")}
