from __future__ import annotations

from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.service import OrgRebaseService
from orgrebase.workspace.service import WorkspaceService


def test_workspace_api_runs_complete_loop() -> None:
    legacy = OrgRebaseService()
    workspace = WorkspaceService()
    try:
        client = TestClient(create_app(legacy, workspace))
        response = client.post("/api/workspace/run")
        assert response.status_code == 200
        payload = response.json()
        assert payload["final_quote"]["version"] == "v3"
        assert payload["final_quote"]["payload"]["currency"] == "EUR"
    finally:
        workspace.close()


def test_workspace_quote_to_rebase_demo_endpoint() -> None:
    legacy = OrgRebaseService()
    workspace = WorkspaceService()
    try:
        client = TestClient(create_app(legacy, workspace))
        response = client.post("/api/demo/workspace/quote-to-rebase")
        assert response.status_code == 200
        payload = response.json()
        assert payload["final_quote"]["version"] == "v3"
        assert payload["final_quote"]["payload"]["launch_date"] == "2026-09-15"
        assert payload["final_quote"]["payload"]["currency"] == "EUR"
        status = client.get("/api/demo/workspace/agentteams-status")
        assert status.status_code == 200
        assert status.json()["status"] == "NOT_RUN"
        page = client.get("/")
        assert "workspace-button" in page.text
        assert "跑通报价闭环" in page.text
    finally:
        workspace.close()
