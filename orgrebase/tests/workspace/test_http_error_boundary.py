from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orgrebase.auth import AuthenticationError
from orgrebase.domain import AuthorizationError
from orgrebase.workspace import routes


def request_failing_operation(monkeypatch, error):
    def fail(workspace):
        raise error

    monkeypatch.setattr(routes, "change_options", fail)
    app = FastAPI()
    app.include_router(routes.change_router(lambda: object()))
    with TestClient(app) as client:
        return client.get("/api/workspace/change-options")


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ValueError("/private/customer/quote.json contains confidential draft content"), 409, "WORKSPACE_REQUEST_FAILED"),
        (RuntimeError("customer quote failed: confidential draft content"), 409, "WORKSPACE_REQUEST_FAILED"),
        (AuthorizationError("actor from /private/customer/members.json is not allowed"), 403, "AUTHZ_DENIED"),
    ],
)
def test_http_errors_hide_unclassified_paths_and_content(monkeypatch, error, status, code):
    response = request_failing_operation(monkeypatch, error)
    assert response.status_code == status
    assert response.json() == {"detail": {
        "code": code, "message": code,
    }}


def test_http_errors_preserve_known_code_and_review_timing(monkeypatch):
    error = ValueError("WORKSPACE_REVIEW_GATE_NOT_READY: /private/customer/draft.json")
    error.remaining_ms = 4000
    error.not_before = "2026-09-12T01:00:00Z"
    response = request_failing_operation(monkeypatch, error)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "WORKSPACE_REVIEW_GATE_NOT_READY",
        "message": "WORKSPACE_REVIEW_GATE_NOT_READY",
        "remaining_ms": 4000,
        "not_before": "2026-09-12T01:00:00Z",
    }


def test_http_errors_preserve_safe_profile_reason(monkeypatch):
    error = ValueError("PROFILE_VALIDATION_FAILED: /private/customer/profile.json PROFILE_OWNER_REQUIRED")
    response = request_failing_operation(monkeypatch, error)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PROFILE_VALIDATION_FAILED",
        "message": "PROFILE_VALIDATION_FAILED:PROFILE_OWNER_REQUIRED",
    }


def test_http_authentication_error_remains_collapsed(monkeypatch):
    response = request_failing_operation(monkeypatch, AuthenticationError("AUTH_WORKSPACE_DENIED", 403))
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "AUTH_WORKSPACE_DENIED"}}
