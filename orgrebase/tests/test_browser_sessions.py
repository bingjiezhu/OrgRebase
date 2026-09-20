from __future__ import annotations

import base64
import json
import secrets
import socket
import ssl
import threading
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx2 as httpx
import pytest
import uvicorn
from browser_oidc_provider import LocalOIDCProvider, tls_files
from enterprise_pack_factory import make_enterprise_pack
from sqlalchemy import select, update

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, IdentitySettings, current_authorization
from orgrebase.browser_auth import LOGIN_COOKIE, SESSION_COOKIE, BrowserSessions, BrowserSessionSettings
from orgrebase.browser_session_store import BrowserSessionStore, opaque_digest
from orgrebase.database import browser_sessions, execute_core, oidc_login_transactions
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.store import StateStore
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

pytest_plugins = ("test_postgres_store",)


class BrowserApplication:
    def __init__(self, settings, certificate, private):
        self.settings, self.certificate, self.private = settings, certificate, private
        self.application = None
        self.start()

    def start(self):
        self.application = create_app(deployment_settings=self.settings)
        port = urlsplit(self.settings.browser_session.public_origin).port
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        self.server = uvicorn.Server(uvicorn.Config(self.application, host="127.0.0.1", port=port,
                                                   ssl_certfile=str(self.certificate), ssl_keyfile=str(self.private),
                                                   access_log=False, log_level="error", ws="none", loop="asyncio"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [listener]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                raise AssertionError("HTTPS application did not start")
            time.sleep(0.01)

    def close(self):
        self.server.should_exit = True
        self.thread.join(10)
        assert not self.thread.is_alive()

    def restart(self):
        self.close()
        self.start()


@pytest.fixture
def browser_deployment(tmp_path, postgres_runtime, monkeypatch, request):
    certificate, private = tls_files(tmp_path)
    provider = LocalOIDCProvider(certificate, private)
    pack = make_enterprise_pack(tmp_path)
    runtime = load_enterprise_quote_pilot_pack(pack)
    database = postgres_runtime(tenant_id=runtime.profile.organization_id)
    membership = tmp_path / "membership.json"
    membership.write_text(json.dumps({"tenant_id": runtime.profile.organization_id,
                                      "members": [{"subject": "operator", "actor_id": runtime.profile.default_task.actor_id,
                                                   "roles": ["administrator"]}]}))
    client_secret = tmp_path / "oidc-secret"
    client_secret.write_text(provider.client_secret)
    client_secret.chmod(0o600)
    session_key = tmp_path / "session-key"
    session_key.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    session_key.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"https://localhost:{port}"
    browser = BrowserSessionSettings(provider.client_id, client_secret, session_key, origin)
    identity = IdentitySettings(provider.issuer, provider.audience, provider.issuer + "/jwks",
                                runtime.profile.organization_id, membership, ca_bundle=certificate)
    provider.redirect_uri = browser.redirect_uri
    settings = DeploymentSettings(mode="production", identity=identity, database_url=database["runtime_dsn"],
                                  enterprise_pack=str(pack), allowed_hosts=("localhost",), browser_session=browser)
    if getattr(request, "param", None) == "multiworkspace":
        catalog = tmp_path / "workspace-catalog.json"
        catalog.write_text(json.dumps({"workspaces": [
            {"workspace_id": "default", "label": "报价甲", "enterprise_pack": str(pack), "allowed_subjects": ["operator"]},
            {"workspace_id": "quote-b", "label": "报价乙", "enterprise_pack": str(pack), "allowed_subjects": ["operator", "second-user"]},
        ]}))
        with StateStore(database["migration_dsn"], tenant_id=identity.tenant_id, migrate=False) as operator:
            operator.register_workspace("quote-b", profile_digest=runtime.profile.digest, pack_digest=runtime.pack_digest,
                                        quote_object_id=runtime.quote_object_id, created_at="2026-09-09T00:00:00Z")
        settings = replace(settings, workspace_catalog=catalog)
    monkeypatch.setenv("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "0")
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    application = BrowserApplication(settings, certificate, private)
    try:
        with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(certificate)),
                          trust_env=False, follow_redirects=False, timeout=10) as client:
            yield application, provider, client
    finally:
        application.close()
        provider.close()


def begin(client):
    response = client.get("/api/session/login")
    assert response.status_code == 302, response.text
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    authorization = client.get(response.headers["location"])
    assert authorization.status_code == 302, authorization.text
    return authorization.headers["location"]


def login(client):
    callback = client.get(begin(client))
    assert callback.status_code == 303, callback.text
    session = client.get("/api/session")
    assert session.status_code == 200, session.text
    assert session.json()["authenticated"] is True
    return session.json()


def csrf_headers(application, session):
    return {"Origin": application.settings.browser_session.public_origin, "X-CSRF-Token": session["csrf_token"]}


def test_real_https_code_pkce_session_csrf_form_and_logout(browser_deployment):
    application, provider, client = browser_deployment
    initial = client.get("/api/session")
    assert initial.json()["mode"] == "oidc" and initial.json()["authenticated"] is False
    assert initial.headers["cache-control"] == "no-store"
    console = client.get("/")
    assert console.status_code == 200
    assert console.headers["cache-control"] == "no-cache"
    assert console.headers["referrer-policy"] == "no-referrer"
    assert console.headers["x-frame-options"] == "DENY"
    assert console.headers["x-content-type-options"] == "nosniff"
    unauthorized = client.get("/api/workspace/state")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["x-content-type-options"] == "nosniff"
    session = login(client)
    assert session["principal"]["tenant_id"] == application.settings.identity.tenant_id
    assert session["principal"]["roles"] == ["administrator"]
    assert provider.token_requests == 1
    assert not any(token in json.dumps(session) for token in provider.tokens)
    assert client.get("/api/workspace/state").status_code == 200
    assert client.post("/api/workspace/form").status_code == 403
    wrong = {**csrf_headers(application, session), "Origin": "https://attacker.example"}
    assert client.post("/api/workspace/form", headers=wrong).status_code == 403
    formed = client.post("/api/workspace/form", headers=csrf_headers(application, session))
    assert formed.status_code == 200, formed.text
    repository = application.application.state.browser_sessions.repository
    with repository.store.read_connection() as connection:
        row = execute_core(connection, select(browser_sessions)).fetchone()
        assert not any(token in row["payload_ciphertext"] for token in provider.tokens)
        assert execute_core(connection, select(oidc_login_transactions)).fetchone() is None
    cookie = client.cookies.get(SESSION_COOKIE)
    assert client.post("/api/session/logout").status_code == 403
    logged_out = client.post("/api/session/logout", headers=csrf_headers(application, session))
    assert logged_out.status_code == 200 and logged_out.json()["authenticated"] is False
    replay = client.get("/api/workspace/state", headers={"Cookie": f"{SESSION_COOKIE}={cookie}"})
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"
    with repository.store.read_connection() as connection:
        assert execute_core(connection, select(browser_sessions.c.payload_ciphertext)).fetchone()[0] is None


def test_session_survives_process_restart_and_reloads_membership(browser_deployment):
    application, _, client = browser_deployment
    before = login(client)
    cookies = client.cookies
    client.close()
    application.restart()
    with httpx.Client(base_url=application.settings.browser_session.public_origin, cookies=cookies,
                      verify=ssl.create_default_context(cafile=str(application.certificate)), trust_env=False) as restarted:
        after = restarted.get("/api/session")
        assert after.status_code == 200 and after.json() == before
        application.settings.identity.membership_file.write_text(json.dumps({
            "tenant_id": application.settings.identity.tenant_id, "members": [],
        }))
        assert restarted.get("/api/workspace/state").status_code == 403


def test_session_cannot_transfer_to_a_different_registered_client(browser_deployment):
    application, _, client = browser_deployment
    login(client)
    existing = application.application.state.browser_sessions
    changed = BrowserSessions(replace(existing.settings, client_id="different-registered-client"),
                              existing.authenticator, existing.repository)
    with pytest.raises(AuthenticationError, match="AUTH_SESSION_INVALID"):
        changed.authenticate(client.cookies.get(SESSION_COOKIE))


@pytest.mark.parametrize("attack", ["state", "browser", "replay", "issuer", "duplicate", "return_to"])
def test_callback_binding_and_redirect_attacks_fail_closed(browser_deployment, attack):
    _, provider, client = browser_deployment
    if attack == "return_to":
        assert client.get("/api/session/login?return_to=https://attacker.example").status_code == 400
        assert provider.token_requests == 0
        return
    callback = begin(client)
    parsed = urlsplit(callback)
    params = {key: value[0] for key, value in parse_qs(parsed.query).items()}
    if attack == "state":
        params["state"] = secrets.token_urlsafe(32)
    elif attack == "browser":
        client.cookies.delete(LOGIN_COOKIE)
    elif attack == "issuer":
        params["iss"] = "https://attacker.example"
    elif attack == "replay":
        assert client.get(callback).status_code == 303
    query = urlencode(params) + ("&state=duplicate" if attack == "duplicate" else "")
    response = client.get(urlunsplit(parsed._replace(query=query)))
    assert response.status_code in {400, 401}, response.text
    assert provider.token_requests == (1 if attack == "replay" else 0)


@pytest.mark.parametrize("overrides", [
    {"nonce": "wrong"}, {"sub": "other-person"}, {"aud": "other-client"}, {"azp": "other-client"},
    {"at_hash": "wrong"}, {"exp": 1}, {"exp": True}, {"iat": 1},
])
def test_signed_id_token_still_requires_exact_browser_binding(browser_deployment, overrides):
    _, provider, client = browser_deployment
    provider.id_overrides = overrides
    response = client.get(begin(client))
    assert response.status_code == 401, response.text
    assert client.get("/api/session").json()["authenticated"] is False


def test_backchannel_logout_invalidates_live_session_and_rejects_replay(browser_deployment):
    application, provider, client = browser_deployment
    login(client)
    token = provider.logout_token()
    result = client.post("/api/session/backchannel-logout", data={"logout_token": token})
    assert result.status_code == 200, result.text
    assert client.post("/api/session/backchannel-logout", data={"logout_token": token}).status_code == 401
    state = client.get("/api/workspace/state")
    assert state.status_code == 401 and state.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"
    assert application.application.state.browser_sessions is not None


def test_logout_between_request_and_commit_denies_the_business_write(browser_deployment, monkeypatch):
    application, _, client = browser_deployment
    session = login(client)
    cookie = client.cookies.get(SESSION_COOKIE)
    workspace = application.application.state.workspace_service
    commit = workspace.formation.commit_quote

    def revoke_then_commit(*args, **kwargs):
        assert current_authorization() is not None
        with StateStore(application.settings.database_url, tenant_id=application.settings.identity.tenant_id,
                        migrate=False) as other_connection:
            BrowserSessionStore(other_connection).revoke_session(opaque_digest(cookie), now=int(time.time()))
        return commit(*args, **kwargs)

    monkeypatch.setattr(workspace.formation, "commit_quote", revoke_then_commit)
    response = client.post("/api/workspace/form", headers=csrf_headers(application, session))
    assert response.status_code == 401, response.text
    assert workspace.state()["quote"] is None


def test_expiry_key_replacement_and_credential_ambiguity_fail_closed(browser_deployment):
    application, provider, client = browser_deployment
    login(client)
    ambiguous = client.get("/api/workspace/state", headers={"Authorization": "Bearer " + provider.tokens[0]})
    assert ambiguous.status_code == 401
    login(client)
    application.settings.browser_session.encryption_key_file.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    assert client.get("/api/workspace/state").status_code == 401
    session = login(client)
    repository = application.application.state.browser_sessions.repository
    with repository.store.transaction() as connection:
        execute_core(connection, update(browser_sessions).values(expires_at=int(time.time()) - 1, created_at=int(time.time()) - 2))
    assert client.get("/api/workspace/state", headers=csrf_headers(application, session)).status_code == 401


@pytest.mark.parametrize("attack", ["wrong_signing_key", "redirect_jwks", "redirect_token"])
def test_provider_signature_and_http_redirects_cannot_replace_trusted_endpoints(browser_deployment, attack):
    _, provider, client = browser_deployment
    setattr(provider, attack, True)
    response = client.get(begin(client))
    assert response.status_code == 401, response.text
    assert provider.unexpected_requests == 0
    assert client.get("/api/session").json()["authenticated"] is False


@pytest.mark.parametrize("metadata", [
    {"issuer": "https://other.example"}, {"jwks_uri": "https://other.example/jwks"},
    {"code_challenge_methods_supported": ["plain"]}, {"token_endpoint": "http://localhost/token"},
    {"authorization_endpoint": "https://other.example/authorize"},
])
def test_discovery_requires_exact_issuer_jwks_s256_and_trusted_https_endpoints(browser_deployment, metadata):
    _, provider, client = browser_deployment
    provider.metadata_overrides = metadata
    response = client.get("/api/session/login")
    assert response.status_code == 503, response.text
    assert provider.token_requests == 0


@pytest.mark.parametrize("overrides", [
    {"nonce": "not-a-logout-token"}, {"aud": "other-client"}, {"iat": 1},
    {"events": {"unrelated": {}}}, {"sub": None, "sid": None},
])
def test_signed_invalid_logout_does_not_revoke_browser(browser_deployment, overrides):
    _, provider, client = browser_deployment
    login(client)
    response = client.post("/api/session/backchannel-logout", data={"logout_token": provider.logout_token(**overrides)})
    assert response.status_code == 401, response.text
    assert client.get("/api/workspace/state").status_code == 200


def test_fixed_origin_and_csrf_do_not_trust_forwarded_host_or_ambient_proxy(browser_deployment, monkeypatch):
    application, _, client = browser_deployment
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert client.get("/api/session/login", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    session = login(client)
    forged = client.get("/api/session", headers={"Host": "attacker.example", "X-Forwarded-Host": "localhost"})
    assert forged.status_code in {400, 403}
    read = client.get("/api/session", headers={"X-Forwarded-Host": "attacker.example"})
    assert read.status_code == 200 and read.json()["principal"] == session["principal"]
    headers = {**csrf_headers(application, session), "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/workspace/form", headers=headers).status_code == 403


@pytest.mark.parametrize("origin", ["http://service.example", "https://service.example/path", "https://service.example?x=1", "https://user@service.example"])
def test_browser_origin_must_be_fixed_https_origin(tmp_path, origin):
    with pytest.raises(ValueError):
        BrowserSessionSettings("client", tmp_path / "secret", tmp_path / "key", origin)


def test_private_key_permissions_and_absence_of_identity_are_startup_errors(tmp_path):
    key, secret = tmp_path / "key", tmp_path / "secret"
    key.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    secret.write_text(secrets.token_urlsafe(32))
    key.chmod(0o644)
    secret.chmod(0o600)
    settings = BrowserSessionSettings("client", secret, key, "https://localhost")
    with pytest.raises(AuthenticationError, match="AUTH_PRIVATE_CONFIGURATION_UNAVAILABLE"):
        settings.validate_private_files()
    with pytest.raises(ValueError, match="AUTH_BROWSER_IDENTITY_REQUIRED"):
        DeploymentSettings(mode="local", browser_session=settings)
    with pytest.raises(ValueError, match="AUTH_OIDC_SCOPES_INVALID"):
        replace(settings, scopes=("openid", "offline_access"))
