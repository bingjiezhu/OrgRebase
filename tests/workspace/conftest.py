from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

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
        try:
            service.close()
        except Exception:
            pass


@pytest.fixture
def formed_service(workspace_service: WorkspaceService) -> WorkspaceService:
    workspace_service.form_quote()
    return workspace_service


@pytest.fixture
def persistent_workspace(tmp_path: Path) -> Iterator[tuple[WorkspaceService, Path]]:
    path = tmp_path / "persistent-workspace.sqlite"
    service = WorkspaceService(store_path=path)
    try:
        yield service, path
    finally:
        try:
            service.close()
        except Exception:
            pass
