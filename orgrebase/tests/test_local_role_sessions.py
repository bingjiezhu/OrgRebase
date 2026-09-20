from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event

import pytest
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import CONTROLLED_LOCAL_SESSION_IDENTITY, AuthenticationError, current_authorization
from orgrebase.local_role_session import LOCAL_SESSION_COOKIE, LOCAL_SESSION_ISSUER, LocalRoleSessionSettings
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

ORIGIN = "http://127.0.0.1:8782"


@pytest.fixture
def local_application(tmp_path):
    with closing(WorkspaceService(store_path=tmp_path / "workspace.sqlite", review_duration_seconds=0,
                                  runtime_configuration=load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path)))) as workspace:
        settings = DeploymentSettings(mode="local", local_role_session=LocalRoleSessionSettings(ORIGIN))
        app = create_app(workspace_service=workspace, deployment_settings=settings)
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42000)) as client:
            yield client, app, workspace


def csrf(session):
    headers = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    if session["csrf_token"]:
        headers["X-CSRF-Token"] = session["csrf_token"]
    else:
        headers["X-OrgRebase-Local-Session"] = "initialize"
    return headers


def choose(client, actor_id):
    current = client.get("/api/session").json()
    result = client.post("/api/session/local-actor", json={"actor_id": actor_id}, headers=csrf(current))
    assert result.status_code == 200, result.text
    return result.json()


def actor(session, role, *, label=None):
    return next(item["actor_id"] for item in session["actors"] if role in item["roles"]
                and (label is None or item["label"] == label))


def test_explicit_session_has_no_default_authority_and_rejects_header_impersonation(local_application):
    client, _, workspace = local_application
    session = client.get("/api/session")
    assert session.status_code == 200
    assert "set-cookie" not in session.headers
    assert client.cookies.get(LOCAL_SESSION_COOKIE) is None
    state = session.json()
    assert state["identity_source"] == "controlled-local-session"
    assert state["authentication_required"] and not state["authenticated"]
    assert state["principal"] is None
    assert state["csrf_token"] is None and state["expires_at"] is None
    assert len({item["label"] for item in state["actors"]}) == len(state["actors"])
    for item in state["actors"]:
        if item["actor_id"] in {"human:evergreen-legal-owner", "human:evergreen-gtm-owner"}:
            assert item["actor_id"] in item["label"]  # No domain-to-owner binding is declared for these actors.
    assert workspace.approval_identity_mode == CONTROLLED_LOCAL_SESSION_IDENTITY
    assert client.get("/").status_code == 200
    for path in ("/api/workspace/state", "/api/workspace/export/evidence", "/api/release-facts"):
        assert client.get(path, headers={"X-OrgRebase-Actor": "human:evergreen-finance-owner"}).status_code == 401
    with pytest.raises(AuthenticationError):
        require_action(workspace, "approve")


def test_cookie_rotation_csrf_membership_and_separate_browser_sessions(local_application):
    client, app, _ = local_application
    state = client.get("/api/session").json()
    business = actor(state, "operator")
    for headers in ({}, {**csrf(state), "Origin": "https://attacker.example"}):
        assert client.post("/api/session/local-actor", json={"actor_id": business}, headers=headers).status_code == 403
    assert client.post("/api/session/local-actor", json={"actor_id": "attacker"}, headers=csrf(state)).status_code == 403
    assert client.post("/api/session/local-actor", json={"actor_id": business, "roles": ["administrator"]},
                       headers=csrf(state)).status_code == 400
    business_session = choose(client, business)
    old_cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
    business_session = choose(client, business)
    assert business_session["principal"]["roles"] == ["operator"]
    assert business_session["principal"]["issuer"] == LOCAL_SESSION_ISSUER
    with pytest.raises(AuthenticationError):
        app.state.local_role_sessions.authenticate(old_cookie)
    with pytest.raises(AuthenticationError):
        app.state.local_role_sessions.create(business, previous=old_cookie)
    business_cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42001)) as second:
        second_state = second.get("/api/session").json()
        choose(second, actor(second_state, "approver", label="财务负责人"))
        assert client.get("/api/session").json()["principal"]["actor_id"] == business
        assert app.state.local_role_sessions.authenticate(business_cookie)[0].actor_id == business
    result = client.post("/api/session/logout", headers=csrf(business_session))
    assert result.status_code == 200 and not result.json()["authenticated"]
    assert client.get("/api/workspace/state").status_code == 401
    with pytest.raises(AuthenticationError):
        app.state.local_role_sessions.authenticate(business_cookie)


def test_business_proposes_finance_approves_executor_applies_and_owner_cannot_be_forged(local_application):
    client, _, _workspace = local_application
    initial = client.get("/api/session").json()
    business = choose(client, actor(initial, "operator"))
    formed = client.post("/api/workspace/form", headers=csrf(business))
    assert formed.status_code == 200, formed.text
    fields = client.get("/api/workspace/change-options").json()["fields"]
    field = next(item for item in fields if item["slot_id"] == "currency")
    proposal = {"event_id": "role-currency", "slot_id": "currency", "value": "GBP",
                "source_ref": "source:finance-reviewed@r2", "base_version": field["current"]["version"],
                "base_digest": field["current"]["digest"]}
    proposed = client.post("/api/workspace/change-proposals", json=proposal, headers=csrf(business))
    assert proposed.status_code == 200, proposed.text
    preview = client.post("/api/workspace/preview/role-currency", headers=csrf(business))
    assert preview.status_code == 200, preview.text
    preview_digest = preview.json()["preview_digest"]
    forged = {**csrf(business), "X-OrgRebase-Actor": field["owner_id"]}
    assert client.post("/api/workspace/approve/role-currency", json={"actor_id": field["owner_id"],
                       "preview_digest": preview_digest}, headers=forged).status_code == 403
    product = choose(client, actor(initial, "approver", label="产品负责人"))
    wrong_owner = client.post("/api/workspace/approve/role-currency", json={"actor_id": field["owner_id"],
                             "preview_digest": preview_digest}, headers={**csrf(product), "X-OrgRebase-Actor": field["owner_id"]})
    assert wrong_owner.status_code == 403, wrong_owner.text
    finance = choose(client, field["owner_id"])
    assert client.post("/api/workspace/change-proposals", json=proposal, headers=csrf(finance)).status_code == 403
    approved = client.post("/api/workspace/approve/role-currency", json={"actor_id": "ignored:body",
                           "preview_digest": preview_digest}, headers={**csrf(finance), "X-OrgRebase-Actor": "ignored:header"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["actor_id"] == field["owner_id"]
    body = {"approval_digest": approved.json()["approval_digest"]}
    assert client.post("/api/workspace/apply/role-currency", json=body, headers=csrf(finance)).status_code == 403
    executor = choose(client, actor(initial, "executor"))
    assert client.post("/api/workspace/approve/role-currency", json={}, headers=csrf(executor)).status_code == 403
    applied = client.post("/api/workspace/apply/role-currency", json=body, headers=csrf(executor))
    assert applied.status_code == 200, applied.text
    state = client.get("/api/workspace/state").json()
    assert state["approval_control"]["identity_claim_boundary"] == "CONTROLLED_LOCAL_SESSION_NOT_EXTERNAL_IAM"
    assert applied.json()["outcome"]["quote"]["payload"]["currency"] == "GBP"


def test_governance_and_task_routes_enforce_the_current_role(local_application):
    client, _, workspace = local_application
    initial = client.get("/api/session").json()
    business = choose(client, actor(initial, "operator"))
    for path in ("/api/workspace/oac-adaptation/agent-prepare", "/api/workspace/oac-adaptation/approve",
                 "/api/workspace/experience/approve", "/api/workspace/skills/enterprise-quote-compose/validation"):
        result = client.post(path, json={"actor_id": workspace.profile.governance.admission_authority_refs[0]},
                             headers=csrf(business))
        assert result.status_code == 403 and result.json()["detail"]["code"] == "AUTH_ACTION_DENIED"
    governor = choose(client, actor(initial, "governor"))
    for path in ("/api/workspace/task-intake/prepare", "/api/workspace/task-intake/admit", "/api/workspace/task-intake/run",
                 "/api/workspace/approve/any", "/api/workspace/apply/any"):
        result = client.post(path, json={}, headers=csrf(governor))
        assert result.status_code == 403 and result.json()["detail"]["code"] == "AUTH_ACTION_DENIED"


def test_business_session_can_prepare_confirm_and_run_a_task_without_execute_permission(local_application, monkeypatch):
    client, _, workspace = local_application
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    workspace.task_intake_required = True
    initial = client.get("/api/session").json()
    business = choose(client, actor(initial, "operator"))
    task = workspace.profile.default_task
    prompt = "Prepare the enterprise quote using the approved product, legal, and finance information."
    prepared = client.post("/api/workspace/task-intake/prepare", headers=csrf(business),
                           json={"prompt": prompt, "actor_id": "ignored:body", "customer_id": task.customer_id,
                                 "deliverable_kind": "QUOTE"})
    assert prepared.status_code == 200, prepared.text
    candidate = prepared.json()
    assert candidate["status"] == "READY_FOR_CONFIRMATION"
    assert candidate["actor_id"] == task.actor_id
    confirmed = client.post("/api/workspace/task-intake/admit", headers=csrf(business),
                            json={"candidate_receipt": candidate, "candidate_digest": candidate["digest"]})
    assert confirmed.status_code == 200, confirmed.text
    ran = client.post("/api/workspace/task-intake/run", headers=csrf(business),
                      json={"candidate_receipt": candidate, "candidate_digest": candidate["digest"],
                            "approval_receipt": confirmed.json(), "approval_digest": confirmed.json()["digest"],
                            "work_description": prompt})
    assert ran.status_code == 200, ran.text
    assert workspace.current_quote().state.value == "CURRENT"
    assert client.post("/api/workspace/apply/any", json={}, headers=csrf(business)).status_code == 403


def test_session_change_after_request_authentication_is_rechecked_before_commit(local_application, monkeypatch):
    client, app, workspace = local_application
    initial = client.get("/api/session").json()
    selected = choose(client, actor(initial, "executor"))
    cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
    writes = []

    def apply(*args, **kwargs):
        app.state.local_role_sessions.logout(cookie)
        current_authorization()()
        writes.append("applied")

    monkeypatch.setattr(workspace, "apply_approved_change", apply)
    result = client.post("/api/workspace/apply/any", json={}, headers=csrf(selected))
    assert result.status_code == 401
    # A delayed response from an old request must not erase a newer browser cookie.
    assert "set-cookie" not in result.headers
    assert writes == []


@pytest.mark.parametrize("revoked", [False, True])
def test_delayed_session_read_cannot_replace_a_rotated_role_cookie(local_application, monkeypatch, revoked):
    import orgrebase.browser_auth_http as http

    client, app, _ = local_application
    initial = client.get("/api/session").json()
    business = choose(client, actor(initial, "operator"))
    finance_id = actor(initial, "approver", label="财务负责人")
    if revoked:
        app.state.local_role_sessions.logout(client.cookies.get(LOCAL_SESSION_COOKIE))
    ready, release = Event(), Event()
    response_class = http.JSONResponse

    def delayed_response(content, *args, **kwargs):
        response = response_class(content, *args, **kwargs)
        if (content.get("principal") or {}).get("actor_id") == (None if revoked else business["principal"]["actor_id"]):
            ready.set()
            assert release.wait(5), "the old session read was not released"
        return response

    monkeypatch.setattr(http, "JSONResponse", delayed_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old_read = pool.submit(client.get, "/api/session")
        try:
            assert ready.wait(5), "the old session read did not reach the response boundary"
            switched = client.post("/api/session/local-actor", json={"actor_id": finance_id},
                                   headers=csrf(initial if revoked else business))
            assert switched.status_code == 200, switched.text
            selected_cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
        finally:
            release.set()
        old_response = old_read.result(timeout=5)
    assert old_response.status_code == 200
    assert "set-cookie" not in old_response.headers
    assert client.cookies.get(LOCAL_SESSION_COOKIE) == selected_cookie
    assert client.get("/api/session").json()["principal"]["actor_id"] == finance_id
    assert client.get("/api/workspace/state").status_code == 200


def test_local_initialization_requires_exact_origin_and_explicit_header(local_application):
    client, app, _ = local_application
    initial = client.get("/api/session").json()
    body = {"actor_id": actor(initial, "operator")}
    for headers in (
        {"Origin": ORIGIN},
        {"X-OrgRebase-Local-Session": "initialize"},
        {**csrf(initial), "Origin": "http://localhost:8782"},
        {**csrf(initial), "Sec-Fetch-Site": "cross-site"},
        {**csrf(initial), "Sec-Fetch-Site": "same-site"},
        {**csrf(initial), "X-OrgRebase-Local-Session": "true"},
    ):
        denied = client.post("/api/session/local-actor", json=body, headers=headers)
        assert denied.status_code == 403, denied.text
        assert "set-cookie" not in denied.headers
    assert app.state.local_role_sessions._sessions == {}
    selected = client.post("/api/session/local-actor", json=body, headers=csrf(initial))
    assert selected.status_code == 200
    assert "HttpOnly" in selected.headers["set-cookie"] and "SameSite=strict" in selected.headers["set-cookie"]
    cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
    # Initialization is never an alternative to CSRF for an existing session.
    denied = client.post("/api/session/local-actor", json=body, headers=csrf(initial))
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "AUTH_CSRF_DENIED"
    assert client.cookies.get(LOCAL_SESSION_COOKIE) == cookie
    assert client.get("/api/session").json() == selected.json()


def test_expired_role_session_recovers_only_through_explicit_selection(local_application):
    client, app, _ = local_application
    initial = client.get("/api/session").json()
    business = choose(client, actor(initial, "operator"))
    sessions = app.state.local_role_sessions
    for key, session in sessions._sessions.items():
        sessions._sessions[key] = replace(session, expires_at=0)
    assert client.get("/api/workspace/state").status_code == 401
    stale_switch = client.post("/api/session/local-actor", json={"actor_id": business["principal"]["actor_id"]},
                               headers=csrf(business))
    assert stale_switch.status_code == 401 and "set-cookie" not in stale_switch.headers
    read = client.get("/api/session")
    assert read.status_code == 200 and "set-cookie" not in read.headers
    assert read.json()["principal"] is None and read.json()["csrf_token"] is None
    recovered = choose(client, actor(initial, "approver", label="财务负责人"))
    assert recovered["authenticated"] and recovered["principal"]["roles"] == ["approver"]
    assert client.get("/api/workspace/state").status_code == 200


def test_membership_check_survives_two_milliseconds_across_integer_second(local_application, monkeypatch):
    import orgrebase.local_role_session as local

    _, app, _ = local_application
    sessions = app.state.local_role_sessions
    finance = next(item["actor_id"] for item in sessions.actors() if item["label"] == "财务负责人")
    original_actor = sessions._actor
    now = [1000.999]

    def resolve_actor(actor_id):
        value = original_actor(actor_id)
        now[0] = 1001.001
        return value

    monkeypatch.setattr(local.time, "time", lambda: now[0])
    monkeypatch.setattr(sessions, "_actor", resolve_actor)
    sessions.verify_membership(finance, finance, "approve")
    assert now[0] == 1001.001
    assert sessions._sessions == {}


@pytest.mark.parametrize("change,code", [
    ("removed", "AUTH_LOCAL_ACTOR_DENIED"),
    ("role_revoked", "AUTH_ACTION_DENIED"),
    ("identity_changed", "AUTH_IDENTITY_CHANGED"),
    ("issuer_changed", "AUTH_MEMBER_VERIFICATION_UNAVAILABLE"),
])
def test_recorded_local_approver_rechecks_current_membership(local_application, monkeypatch, change, code):
    from orgrebase.workspace.approval_authority import verify_member

    _, app, workspace = local_application
    sessions = app.state.local_role_sessions
    directory = sessions.actors()
    finance = next(item["actor_id"] for item in directory if item["label"] == "财务负责人")
    identity = {"issuer": LOCAL_SESSION_ISSUER, "subject": finance, "actor_id": finance}
    verify_member(workspace, identity, "approve")
    if change == "removed":
        monkeypatch.setattr(sessions, "actors", lambda: tuple(item for item in directory if item["actor_id"] != finance))
    elif change == "role_revoked":
        monkeypatch.setattr(sessions, "actors", lambda: tuple(
            {**item, "roles": ["reader"]} if item["actor_id"] == finance else item for item in directory
        ))
    elif change == "identity_changed":
        identity["subject"] = "different:subject"
    else:
        identity["issuer"] = "urn:untrusted-issuer"
    with pytest.raises(AuthenticationError, match=code):
        verify_member(workspace, identity, "approve")
    assert sessions._sessions == {}


def test_membership_check_does_not_renew_expired_session_or_principal(local_application, monkeypatch):
    import orgrebase.local_role_session as local
    from orgrebase.auth import authorize

    _, app, workspace = local_application
    sessions = app.state.local_role_sessions
    finance = next(item["actor_id"] for item in sessions.actors() if item["label"] == "财务负责人")
    cookie = sessions.create(finance)
    principal, _, expires_at = sessions.authenticate(cookie)
    monkeypatch.setattr(local.time, "time", lambda: expires_at + 0.001)
    sessions.verify_membership(finance, finance, "approve")
    with pytest.raises(AuthenticationError, match="AUTH_LOCAL_SESSION_REQUIRED"):
        sessions.authenticate(cookie)
    with pytest.raises(AuthenticationError, match="AUTH_TOKEN_EXPIRED"):
        authorize(principal, "approve", workspace.profile.organization_id)
    assert sessions._sessions == {}


def test_fixed_origin_and_network_boundary_do_not_trust_proxy_headers(local_application):
    client, app, _ = local_application
    assert client.get("/api/session", headers={"Host": "localhost:8782"}).status_code == 403
    with TestClient(app, base_url=ORIGIN, client=("203.0.113.2", 42001)) as remote:
        result = remote.get("/api/session", headers={"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"})
        assert result.status_code == 403
    session = client.get("/api/session").json()
    session = choose(client, actor(session, "operator"))
    assert client.get("/api/workspace/state", headers={"Authorization": "Bearer injected"}).status_code == 401


@pytest.mark.parametrize("origin", ["http://0.0.0.0:8782", "http://example.com:8782", "http://127.0.0.1:8782/",
                                   "http://user@127.0.0.1:8782", "http://127.0.0.1:8782?x=1", "http://127.0.0.1"])
def test_invalid_local_origin_is_rejected(origin):
    with pytest.raises(ValueError, match="AUTH_LOCAL_SESSION_LOOPBACK_ORIGIN_REQUIRED"):
        LocalRoleSessionSettings(origin)


def test_local_session_requires_explicit_local_deployment_and_no_oidc(monkeypatch):
    local = LocalRoleSessionSettings(ORIGIN)
    with pytest.raises(ValueError, match="AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN"):
        DeploymentSettings(mode="production", local_role_session=local)
    with pytest.raises(ValueError, match="AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN"):
        DeploymentSettings(mode="local", local_role_session=local, identity=object())
    monkeypatch.setenv("ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN", ORIGIN)
    assert DeploymentSettings.from_environment().local_role_session == local
    monkeypatch.setenv("ORGREBASE_DEPLOYMENT_MODE", "production")
    with pytest.raises(ValueError, match="AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN"):
        DeploymentSettings.from_environment()


def test_disabled_local_sessions_do_not_offer_role_switching(local_application):
    _, _, workspace = local_application
    settings = DeploymentSettings(mode="local")
    app = create_app(workspace_service=workspace, deployment_settings=settings)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42002)) as client:
        assert client.post("/api/session/local-actor", json={"actor_id": "anything"}).status_code == 404
        assert "actors" not in client.get("/api/session").json()


def test_pilot_cli_honors_local_role_session_opt_in(tmp_path, monkeypatch):
    import uvicorn

    from orgrebase.cli import main

    pack = make_enterprise_pack(tmp_path)
    monkeypatch.setenv("ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN", ORIGIN)
    monkeypatch.setattr(sys, "argv", ["orgrebase", "enterprise-pilot-start", "--pack", str(pack),
                                     "--store", str(tmp_path / "cli.sqlite"), "--host", "127.0.0.1",
                                     "--port", "8782", "--competition-mode", "off"])
    applications = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: applications.append(app))
    main()
    assert len(applications) == 1
    app = applications[0]
    try:
        assert app.state.local_role_sessions.settings.public_origin == ORIGIN
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42000)) as client:
            session = client.get("/api/session").json()
            assert session["authentication_required"] and not session["authenticated"]
            assert session["identity_source"] == "controlled-local-session"
            assert client.get("/api/workspace/state").status_code == 401
    finally:
        app.state.workspace_service.close()


@pytest.mark.parametrize("deployment,origin,code", [
    ("production", ORIGIN, "AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN"),
    ("local", "http://127.0.0.1:9999", "AUTH_LOCAL_SESSION_LISTENER_ORIGIN_MISMATCH"),
])
def test_pilot_cli_rejects_role_session_misconfiguration_before_loading_pack(monkeypatch, deployment, origin, code):
    from orgrebase.cli import main

    monkeypatch.setenv("ORGREBASE_DEPLOYMENT_MODE", deployment)
    monkeypatch.setenv("ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN", origin)
    monkeypatch.setattr(sys, "argv", ["orgrebase", "enterprise-pilot-start", "--pack", "/not-loaded",
                                     "--store", "/not-created", "--port", "8782", "--competition-mode", "off"])
    with pytest.raises(ValueError, match=code):
        main()
