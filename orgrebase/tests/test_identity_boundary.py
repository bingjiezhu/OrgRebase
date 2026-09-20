from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import (
    AuthenticationError,
    IdentitySettings,
    JWTAuthenticator,
    Principal,
    VerifiedJWKClient,
    authorize,
    current_authorization,
    request_action,
)
from orgrebase.clock import FrozenClock, SystemClock
from orgrebase.runtime_config import DeploymentSettings


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def identity(tmp_path: Path):
    path = tmp_path / "membership.json"
    path.write_text(json.dumps({
        "tenant_id": "org:test",
        "members": [{"subject": "person-1", "actor_id": "human:owner", "roles": ["administrator"]}],
    }))
    return IdentitySettings("https://issuer.example", "orgrebase-api", "https://issuer.example/jwks", "org:test", path)


def signed_token(key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://issuer.example", "aud": "orgrebase-api", "sub": "person-1",
        "iat": now, "exp": now + 300, "token_use": "access",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})


def configure_keys(monkeypatch, key):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key-1", use="sig", alg="RS256")
    monkeypatch.setattr(VerifiedJWKClient, "fetch_data", lambda self: {"keys": [jwk]})


def test_valid_signed_token_uses_server_membership(identity, key, monkeypatch):
    configure_keys(monkeypatch, key)
    principal = JWTAuthenticator(identity).authenticate("Bearer " + signed_token(key, roles=["root"], actor_id="attacker"))
    assert principal.actor_id == "human:owner"
    assert principal.roles == frozenset({"administrator"})
    assert principal.tenant_id == "org:test"


@pytest.mark.parametrize("mutation", [
    {"iss": "https://other.example"}, {"aud": "other-api"}, {"sub": "unknown"},
    {"exp": 1}, {"nbf": 4_000_000_000}, {"iat": 4_000_000_000},
    {"exp": 4_000_000_000}, {"token_use": "id"}, {"sub": ""}, {"iat": True},
])
def test_invalid_token_claims_fail_closed(identity, key, monkeypatch, mutation):
    configure_keys(monkeypatch, key)
    with pytest.raises(AuthenticationError):
        JWTAuthenticator(identity).authenticate("Bearer " + signed_token(key, **mutation))


def test_invalid_signature_and_algorithm_are_rejected(identity, key, monkeypatch):
    configure_keys(monkeypatch, key)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = [signed_token(wrong_key), jwt.encode({"sub": "person-1"}, "a" * 32, algorithm="HS256", headers={"kid": "key-1"})]
    for token in tokens:
        with pytest.raises(AuthenticationError, match="AUTH_TOKEN_INVALID"):
            JWTAuthenticator(identity).authenticate("Bearer " + token)


@pytest.mark.parametrize("value", [None, "", "Basic abc", "Bearer ", "Bearer " + "x" * 16_385])
def test_missing_or_malformed_credentials(identity, value):
    with pytest.raises(AuthenticationError):
        JWTAuthenticator(identity).authenticate(value)


def test_membership_revocation_is_reloaded_without_restart(identity, key, monkeypatch):
    configure_keys(monkeypatch, key)
    auth = JWTAuthenticator(identity)
    token = "Bearer " + signed_token(key)
    assert auth.authenticate(token).subject == "person-1"
    identity.membership_file.write_text(json.dumps({"tenant_id": "org:test", "members": []}))
    with pytest.raises(AuthenticationError, match="AUTH_MEMBERSHIP_DENIED"):
        auth.authenticate(token)


@pytest.mark.parametrize("mutation,expected", [
    ("remove", "AUTH_MEMBERSHIP_DENIED"),
    ("actor", "AUTH_IDENTITY_CHANGED"),
    ("role", "AUTH_ACTION_DENIED"),
])
def test_recorded_approver_requires_current_membership(identity, mutation, expected):
    verifier = JWTAuthenticator(identity)
    assert verifier.verify_membership("person-1", "human:owner", "approve") is None
    payload = json.loads(identity.membership_file.read_text())
    if mutation == "remove":
        payload["members"] = []
    elif mutation == "actor":
        payload["members"][0]["actor_id"] = "human:replacement"
    else:
        payload["members"][0]["roles"] = ["reader"]
    identity.membership_file.write_text(json.dumps(payload))
    with pytest.raises(AuthenticationError, match=expected):
        verifier.verify_membership("person-1", "human:owner", "approve")


@pytest.mark.parametrize("payload", [
    '{"tenant_id":"org:other","members":[]}',
    '{"tenant_id":"org:test","members":[],"members":[]}',
    '{"tenant_id":"org:test","members":[{"subject":"x","actor_id":"y","roles":["root"]}]}',
    '{"tenant_id":"org:test","members":[{"subject":"x","actor_id":"y","roles":["reader","reader"]}]}',
])
def test_invalid_membership_fails_closed(identity, payload):
    identity.membership_file.write_text(payload)
    with pytest.raises(AuthenticationError, match="AUTH_MEMBERSHIP_UNAVAILABLE"):
        JWTAuthenticator(identity)


def test_action_member_directory_reloads_policy_and_contains_no_tokens(identity):
    authenticator = JWTAuthenticator(identity)
    assert authenticator.members_for_action("approve") == ({"subject": "person-1", "actor_id": "human:owner"},)
    identity.membership_file.write_text(json.dumps({"tenant_id": "org:test", "members": [
        {"subject": "person-1", "actor_id": "human:owner", "roles": ["reader"]},
        {"subject": "person-2", "actor_id": "human:delegate", "roles": ["approver"]},
    ]}))
    assert authenticator.members_for_action("approve") == ({"subject": "person-2", "actor_id": "human:delegate"},)
    assert authenticator.members_for_action("execute") == ()


def test_cross_tenant_role_and_expiry_authorization():
    principal = Principal("issuer", "subject", "org:a", "actor", frozenset({"reader"}), int(time.time()) + 300)
    authorize(principal, "export", "org:a")
    for action, tenant in [("execute", "org:a"), ("read", "org:b")]:
        with pytest.raises(AuthenticationError):
            authorize(principal, action, tenant)
    with pytest.raises(AuthenticationError, match="AUTH_TOKEN_EXPIRED"):
        authorize(replace(principal, expires_at=0), "read", "org:a")


@pytest.mark.parametrize("method,path,action", [
    ("GET", "/api/workspace/export/evidence", "export"),
    ("GET", "/api/workspace/state", "read"),
    ("POST", "/api/workspace/approve/e-1", "approve"),
    ("POST", "/api/workspace/apply/e-1", "execute"),
    ("POST", "/api/workspace/skills/a/drafts", "govern"),
    ("POST", "/api/workspace/changes", "propose"),
    ("POST", "/api/workspace/changes/event-1/reject", "approve"),
    ("POST", "/api/workspace/changes/event-1/delegate", "approve"),
    ("POST", "/api/workspace/changes/event-1/revoke-delegation", "approve"),
    ("POST", "/api/workspace/changes/event-1/escalate", "propose"),
    ("POST", "/api/workspace/changes/event-1/return-for-evidence", "approve"),
    ("POST", "/api/workspace/changes/event-1/resume", "propose"),
    ("POST", "/api/workspace/changes/event-1/review-observation", "read"),
    ("GET", "/api/workspace/effect-options", "read"),
    ("GET", "/api/workspace/effect-proposals", "read"),
    ("HEAD", "/api/workspace/effect-proposals/e-1", "read"),
    ("POST", "/api/workspace/effect-proposals", "propose"),
    ("POST", "/api/workspace/effect-proposals/e-1/approve", "approve"),
    ("POST", "/api/workspace/effect-proposals/e-1/reject", "approve"),
    ("POST", "/api/workspace/effect-proposals/e-1/actions", "read"),
    ("GET", "/api/workspace/privacy/deletion-ledger", "privacy_manage"),
    ("DELETE", "/api/workspace/task-intake/work-description", "erase"),
    ("POST", "/api/receipts/verify", "read"),
])
def test_explicit_action_classification(method, path, action):
    assert request_action(method, path) == action


def production_settings(identity):
    return DeploymentSettings(
        mode="production", identity=identity, database_url="postgresql://127.0.0.1/isolated",
        enterprise_pack="/configured/pack", allowed_hosts=("localhost",),
        effect_config=Path("configured-effects.json"),
    )


class WorkspaceBoundary:
    """Only the API/identity boundary is under test; PostgreSQL has separate integration tests."""
    profile = SimpleNamespace(organization_id="org:test")
    clock = SystemClock()
    approval_identity_mode = "VERIFIED_PRINCIPAL_IDENTITY"
    runtime_configuration = SimpleNamespace(schema_version="orgrebase.enterprise-quote-pilot-pack.v2", enterprise_binding=object())
    competition_mode = "off"
    effective_workflow_run_id = "run:identity-boundary"
    effect_config_path = Path("configured-effects.json")
    store_path = "postgresql://localhost/isolated"
    store = SimpleNamespace(check_health=lambda: {"backend": "postgresql", "tenant_id": "org:test", "runtime_role_safe": True})

    def __init__(self):
        self.calls = []

    def state(self, *, history_limit=50):
        self.calls.append("read")
        return {"stage": "CURRENT"}

    def oac_activation_state(self):
        return {"status": "NOT_USED_IN_THIS_RUN"}

    def export_evidence(self):
        self.calls.append("export")
        return {"private": "evidence"}

    def approve_change(self, kind, *, actor_id, preview_digest):
        self.calls.append(("approve", actor_id))
        return {"actor_id": actor_id}

    def apply_approved_change(self, kind, *, approval_digest):
        current_authorization()()
        self.calls.append("apply")
        return {"status": "APPLIED"}


def test_production_http_reads_exports_approve_apply_all_require_identity(identity, key, monkeypatch):
    configure_keys(monkeypatch, key)
    workspace = WorkspaceBoundary()
    app = create_app(workspace_service=workspace, deployment_settings=production_settings(identity))
    with TestClient(app, base_url="http://localhost") as client:
        for method, path in [("GET", "/api/workspace/state"), ("GET", "/api/workspace/export/evidence"),
                             ("POST", "/api/workspace/approve/e-1"), ("POST", "/api/workspace/apply/e-1")]:
            result = client.request(method, path, json={}, headers={"X-OrgRebase-Actor": "human:owner"})
            assert result.status_code == 401
            assert result.headers["cache-control"] == "no-store"
            assert result.headers["www-authenticate"] == "Bearer"
        assert workspace.calls == []
        headers = {"Authorization": "Bearer " + signed_token(key), "X-OrgRebase-Actor": "attacker"}
        for credentials in ({}, headers):
            for path in ("/docs", "/redoc", "/openapi.json"):
                response = client.get(path, headers=credentials)
                assert response.status_code == 404
                assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/workspace/state", headers=headers).status_code == 200
        exported = client.get("/api/workspace/export/evidence", headers=headers)
        assert exported.status_code == 200
        assert exported.headers["cache-control"] == "no-store"
        assert "Authorization" in exported.headers["vary"]
        assert "X-OrgRebase-Workspace" in exported.headers["vary"]
        result = client.post("/api/workspace/approve/e-1", headers=headers, json={"actor_id": "attacker"})
        assert result.json()["actor_id"] == "human:owner"
        assert client.post("/api/workspace/apply/e-1", headers=headers, json={}).status_code == 200
        for path in ["/api/demo/run", "/api/workspace/reset", "/api/release-facts", "/", "/docs"]:
            assert client.post(path, headers=headers, json={}).status_code == 404
        for path in ["/api/workspace/skills", "/api/workspace/experience", "/api/workspace/oac-adaptation"]:
            assert client.get(path, headers=headers).status_code == 404
            assert client.post(path + "/approve", headers=headers, json={}).status_code == 404
        identity.membership_file.write_text(json.dumps({"tenant_id": "org:test", "members": []}))
        assert client.get("/api/workspace/export/evidence", headers=headers).status_code == 403


def test_revocation_after_request_authentication_is_checked_at_commit(identity, key, monkeypatch):
    configure_keys(monkeypatch, key)
    workspace = WorkspaceBoundary()
    def revoked_apply(kind, *, approval_digest):
        identity.membership_file.write_text(json.dumps({"tenant_id": "org:test", "members": []}))
        current_authorization()()
        workspace.calls.append("unexpected_write")
    workspace.apply_approved_change = revoked_apply
    app = create_app(workspace_service=workspace, deployment_settings=production_settings(identity))
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/workspace/apply/e-1", json={}, headers={"Authorization": "Bearer " + signed_token(key)})
        assert response.status_code == 403
        assert workspace.calls == []


@pytest.mark.parametrize("role,cancel_status,execute_status", [
    ("approver", 200, 403), ("executor", 403, 200), ("reader", 403, 403),
])
def test_effect_action_http_preserves_typed_action_authorization(identity, key, monkeypatch,
                                                                role, cancel_status, execute_status):
    from orgrebase.workspace import routes
    from orgrebase.workspace.change_proposals import require_action

    configure_keys(monkeypatch, key)
    identity.membership_file.write_text(json.dumps({
        "tenant_id": "org:test", "members": [{"subject": "person-1", "actor_id": "human:owner", "roles": [role]}],
    }))
    workspace = WorkspaceBoundary()
    monkeypatch.setattr(routes, "load_effect_config", lambda *args: None)

    def apply_action(workspace, config, proposal_id, payload):
        actor = require_action(workspace, "approve" if payload.action == "CANCEL" else "execute")
        return {"actor": actor, "action": payload.action}

    monkeypatch.setattr(routes, "request_effect_action", apply_action)
    app = create_app(workspace_service=workspace, deployment_settings=production_settings(identity))
    headers = {"Authorization": "Bearer " + signed_token(key)}
    with TestClient(app, base_url="http://localhost") as client:
        for action, expected in [("CANCEL", cancel_status), ("EXECUTE", execute_status), ("QUERY", execute_status)]:
            result = client.post("/api/workspace/effect-proposals/e-1/actions", headers=headers,
                                 json={"action": action, "request_digest": "sha256:" + "a" * 64})
            assert result.status_code == expected
            if expected == 200:
                assert result.json() == {"actor": "human:owner", "action": action}
        identity.membership_file.write_text(json.dumps({"tenant_id": "org:test", "members": []}))
        assert client.post("/api/workspace/effect-proposals/e-1/actions", headers=headers,
                           json={"action": "CANCEL", "request_digest": "sha256:" + "a" * 64}).status_code == 403


@pytest.mark.parametrize("action,role", [("EXECUTE", "executor"), ("QUERY", "executor"), ("CANCEL", "approver")])
def test_effect_action_refreshes_roles_after_request_before_typed_command(identity, key, monkeypatch, action, role):
    from orgrebase.workspace import routes
    from orgrebase.workspace.change_proposals import require_action

    configure_keys(monkeypatch, key)
    def membership(roles):
        identity.membership_file.write_text(json.dumps({
            "tenant_id": "org:test", "members": [{"subject": "person-1", "actor_id": "human:owner", "roles": roles}],
        }))
    membership([role])
    workspace = WorkspaceBoundary()
    monkeypatch.setattr(routes, "load_effect_config", lambda *args: None)
    def revoke_before_command(workspace, config, proposal_id, payload):
        membership(["reader"])
        require_action(workspace, "approve" if payload.action == "CANCEL" else "execute")
        workspace.calls.append("unexpected_command")
        return {}
    monkeypatch.setattr(routes, "request_effect_action", revoke_before_command)
    app = create_app(workspace_service=workspace, deployment_settings=production_settings(identity))
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/workspace/effect-proposals/e-1/actions",
                               headers={"Authorization": "Bearer " + signed_token(key)},
                               json={"action": action, "request_digest": "sha256:" + "a" * 64})
        assert response.status_code == 403 and response.json()["detail"]["code"] == "AUTH_ACTION_DENIED"
        assert workspace.calls == []


@pytest.mark.parametrize("field,value,code", [
    ("clock", FrozenClock("2026-01-01T00:00:00Z"), "PRODUCTION_SYSTEM_CLOCK_REQUIRED"),
    ("approval_identity_mode", "CONTROLLED_LOCAL_HEADER_IDENTITY", "PRODUCTION_VERIFIED_IDENTITY_REQUIRED"),
    ("runtime_configuration", None, "PRODUCTION_ADMITTED_PACK_REQUIRED"),
    ("profile", SimpleNamespace(organization_id="org:other"), "PRODUCTION_TENANT_PROFILE_MISMATCH"),
    ("store", SimpleNamespace(check_health=lambda: {"backend": "sqlite", "tenant_id": "org:test"}), "PRODUCTION_TENANT_DATABASE_REQUIRED"),
])
def test_production_rejects_unqualified_workspace(identity, field, value, code):
    workspace = WorkspaceBoundary()
    setattr(workspace, field, value)
    with pytest.raises(ValueError, match=code):
        create_app(workspace_service=workspace, deployment_settings=production_settings(identity))


def test_production_settings_require_identity_postgres_pack_hosts(identity):
    with pytest.raises(ValueError, match="PRODUCTION_IDENTITY_REQUIRED"):
        DeploymentSettings(mode="production")
    settings = production_settings(identity)
    for field, value in [("database_url", ":memory:"), ("database_url", "sqlite:///a.db"),
                         ("enterprise_pack", None), ("allowed_hosts", ("*",))]:
        with pytest.raises(ValueError):
            replace(settings, **{field: value})
    with pytest.raises(ValueError, match="PRODUCTION_DEMO_FORBIDDEN"):
        create_app(deployment_settings=settings, enable_legacy_demo=True)


def test_production_rejects_offline_governance_through_indirect_form_path(identity, monkeypatch):
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "required")
    with pytest.raises(ValueError, match="PRODUCTION_OFFLINE_GOVERNANCE_FORBIDDEN"):
        create_app(workspace_service=WorkspaceBoundary(), deployment_settings=production_settings(identity))


def test_local_embedding_with_identity_does_not_bypass_authentication(identity, key, monkeypatch):
    configure_keys(monkeypatch, key)
    workspace = WorkspaceBoundary()
    settings = replace(production_settings(identity), mode="local", browser_session=None)
    with TestClient(create_app(workspace_service=workspace, deployment_settings=settings),
                    base_url="http://localhost") as client:
        assert client.get("/api/workspace/state").status_code == 401
        assert client.get("/api/workspace/export/evidence").status_code == 401
        assert client.get("/docs").status_code == 404
        assert workspace.calls == []
        token = "Bearer " + signed_token(key)
        assert client.get("/api/workspace/state", headers={"Authorization": token}).status_code == 200
        assert workspace.calls == ["read"]
