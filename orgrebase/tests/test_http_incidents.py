from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from orgrebase.api import (
    MAX_REQUEST_BODY_BYTES,
    LoginRateLimiter,
    RequestBodyDeadlineMiddleware,
    create_app,
)
from orgrebase.http_errors import public_error, public_http_error, request_incident_scope
from orgrebase.workspace_catalog import WorkspaceDispatcher


def _failing_app():
    app = create_app()

    @app.get("/api/workspace/incident/{item_id}")
    def handled(item_id: str):
        raise HTTPException(status_code=409, detail=public_http_error(ValueError(f"secret {item_id}")))

    @app.get("/incident-unhandled/{item_id}")
    def unhandled(item_id: str):
        raise RuntimeError(f"private record {item_id}")

    @app.get("/incident-known")
    def known():
        raise HTTPException(status_code=409, detail=public_http_error(ValueError("KNOWN_REJECTION: secret")))

    return app


def test_non_http_error_projection_does_not_create_diagnostics(caplog):
    with caplog.at_level(logging.ERROR, logger="orgrebase.http"):
        assert public_error(ValueError("private content")) == {
            "code": "WORKSPACE_REQUEST_FAILED", "message": "WORKSPACE_REQUEST_FAILED",
        }
        with request_incident_scope() as incident:
            assert public_error(ValueError("private stored error")) == {
                "code": "WORKSPACE_REQUEST_FAILED", "message": "WORKSPACE_REQUEST_FAILED",
            }
            assert incident.incident_id is None
    assert not caplog.records


def test_handled_failure_links_response_to_safe_route_template_log(caplog):
    with caplog.at_level(logging.ERROR, logger="orgrebase.http"), TestClient(_failing_app()) as client:
        response = client.get("/api/workspace/incident/customer-private?token=private-token",
                              headers={"X-OrgRebase-Incident": "attacker-supplied"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        incident = detail["incident_id"]
        assert re.fullmatch(r"[0-9a-f]{32}", incident)
        assert response.headers["X-OrgRebase-Incident"] == incident
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/health").headers.get("X-OrgRebase-Incident") is None
    records = [r for r in caplog.records if r.name == "orgrebase.http"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert incident in message and "exception_type=ValueError" in message
    assert "route=/api/workspace/incident/{item_id}" in message
    assert all(secret not in message + response.text for secret in
               ("customer-private", "private-token", "attacker-supplied"))
    assert records[0].exc_info is None


def test_unhandled_failure_is_sanitized_and_keeps_response_policy(caplog):
    with caplog.at_level(logging.ERROR, logger="orgrebase.http"), TestClient(_failing_app()) as client:
        response = client.get("/incident-unhandled/private-customer")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "WORKSPACE_REQUEST_FAILED"
    assert response.headers["X-OrgRebase-Incident"] == response.json()["detail"]["incident_id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private-customer" not in caplog.text + response.text
    assert "exception_type=RuntimeError" in caplog.text


@pytest.mark.parametrize("custom_code", [False, True])
def test_unhandled_uppercase_text_or_code_is_never_a_public_code(caplog, custom_code):
    app = create_app()

    @app.get("/unhandled")
    def fail():
        error = RuntimeError("PRIVATE_API_TOKEN")
        if custom_code:
            error.code = "CUSTOMER_SECRET_CODE"
        raise error

    with caplog.at_level(logging.ERROR, logger="orgrebase.http"), TestClient(app) as client:
        response = client.get("/unhandled")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "WORKSPACE_REQUEST_FAILED"
    assert "PRIVATE_API_TOKEN" not in caplog.text + response.text
    assert "CUSTOMER_SECRET_CODE" not in caplog.text + response.text


def test_known_rejection_remains_a_stable_business_error(caplog):
    with caplog.at_level(logging.ERROR, logger="orgrebase.http"), TestClient(_failing_app()) as client:
        response = client.get("/incident-known")
    assert response.json() == {"detail": {"code": "KNOWN_REJECTION", "message": "KNOWN_REJECTION"}}
    assert "X-OrgRebase-Incident" not in response.headers
    assert not [r for r in caplog.records if r.name == "orgrebase.http"]


def test_concurrent_child_requests_have_independent_incidents(caplog):
    default, child = _failing_app(), _failing_app()
    default.add_middleware(WorkspaceDispatcher, applications={"other": child}, default_workspace_id="default")
    with caplog.at_level(logging.ERROR, logger="orgrebase.http"), TestClient(default) as client:
        def request(index):
            return client.get(f"/api/workspace/incident/private-{index}",
                              headers={"X-OrgRebase-Workspace": "other" if index % 2 else "default"})

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(request, range(8)))
    identifiers = {r.json()["detail"]["incident_id"] for r in responses}
    assert len(identifiers) == 8
    records = [r for r in caplog.records if r.name == "orgrebase.http"]
    assert len(records) == 8
    assert all(sum(identifier in r.getMessage() for r in records) == 1 for identifier in identifiers)
    assert "private-" not in caplog.text


def test_request_body_limit_counts_actual_chunks_before_endpoint_execution():
    app = create_app()
    calls = []
    events = []
    app.state.security_events._sink = events.append

    @app.post("/api/workspace/body-limit-probe")
    async def probe(request: Request):
        body = await request.body()
        calls.append(len(body))
        return {"size": len(body)}

    @app.post("/api/workspace/no-body-write-probe")
    def no_body_write():
        calls.append("MUTATED")
        return {"ok": True}

    with TestClient(app) as client:
        accepted = client.post(
            "/api/workspace/body-limit-probe", content=b"a" * MAX_REQUEST_BODY_BYTES
        )
        rejected = client.post(
            "/api/workspace/body-limit-probe",
            content=b"b" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={"Content-Length": "1"},
        )
        chunked = client.post(
            "/api/workspace/body-limit-probe",
            content=(b"c" * 131_072 for _ in range(9)),
        )
        no_read_declared = client.post(
            "/api/workspace/no-body-write-probe", content=b"x",
            headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)},
        )
        no_read_chunked = client.post(
            "/api/workspace/no-body-write-probe",
            content=(b"d" * 131_072 for _ in range(9)),
        )
    assert accepted.status_code == 200 and accepted.json()["size"] == MAX_REQUEST_BODY_BYTES
    assert rejected.status_code == 413 and chunked.status_code == 413
    assert no_read_declared.status_code == 413 and no_read_chunked.status_code == 413
    assert no_read_declared.json()["detail"]["code"] == "REQUEST_BODY_TOO_LARGE"
    assert calls == [MAX_REQUEST_BODY_BYTES]
    assert rejected.headers["cache-control"] == "no-store"
    assert len(events) == 4
    assert all(event["reason"] == "REQUEST_BODY_TOO_LARGE" for event in events)


def test_request_body_deadline_is_total_across_receive_chunks():
    entered = []

    async def inner(scope, receive, send):
        entered.append(True)
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    request_count = 0

    async def receive():
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return {"type": "http.request", "body": b"a", "more_body": True}
        await asyncio.sleep(0.05)
        return {"type": "http.request", "body": b"b", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    middleware = RequestBodyDeadlineMiddleware(inner, timeout_seconds=0.01)
    asyncio.run(middleware({"type": "http", "method": "POST", "path": "/"}, receive, send))
    assert entered == []
    assert next(message["status"] for message in sent if message["type"] == "http.response.start") == 408
    assert b"REQUEST_BODY_DEADLINE_EXCEEDED" in b"".join(
        message.get("body", b"") for message in sent
    )


def test_disconnected_body_never_reaches_business_code_and_replay_preserves_disconnect():
    async def run_case(messages, inner):
        sent = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        middleware = RequestBodyDeadlineMiddleware(inner, timeout_seconds=1)
        await middleware(
            {"type": "http", "method": "POST", "path": "/", "headers": []}, receive, send
        )
        return sent

    called = []

    async def forbidden_inner(_scope, _receive, _send):
        called.append(True)

    sent = asyncio.run(run_case([
        {"type": "http.request", "body": b"{", "more_body": True},
        {"type": "http.disconnect"},
    ], forbidden_inner))
    assert called == []
    assert next(item["status"] for item in sent if item["type"] == "http.response.start") == 400
    assert b"REQUEST_BODY_INCOMPLETE" in b"".join(item.get("body", b"") for item in sent)

    observed = []

    async def replay_inner(_scope, receive, send):
        observed.extend([await receive(), await receive()])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    asyncio.run(run_case([
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ], replay_inner))
    assert observed[0] == {"type": "http.request", "body": b"{}", "more_body": False}
    assert observed[1] == {"type": "http.disconnect"}


def test_login_limiter_has_fixed_capacity_and_recovers_after_window():
    clock = [0.0]
    limiter = LoginRateLimiter(limit=3, window_seconds=10, monotonic=lambda: clock[0])
    assert [limiter.admit() for _ in range(4)] == [True, True, True, False]
    assert len(limiter._events) == 3
    clock[0] = 11
    assert limiter.admit()
    assert len(limiter._events) == 1
