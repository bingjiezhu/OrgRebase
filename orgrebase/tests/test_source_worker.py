from __future__ import annotations

import io
import json
import secrets
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.error import HTTPError

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import (
    AuthenticationError,
    IdentitySettings,
    VerifiedJWKClient,
    request_authorization,
    request_principal,
)
from orgrebase.domain import ObjectState
from orgrebase.runtime_config import DeploymentSettings, open_workspace
from orgrebase.workspace.dataverse import SourceError, SourceRateLimited
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.source_worker import run_source_sync

ORGANIZATION = "00000000-0000-0000-0000-000000000987"

pytest_plugins = ("test_postgres_store",)

RECORD = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def deployment(postgres_runtime, tmp_path, monkeypatch):
    pack = make_enterprise_pack(tmp_path)
    runtime = load_enterprise_quote_pilot_pack(pack)
    postgres_dsn = postgres_runtime(tenant_id=runtime.profile.organization_id)["runtime_dsn"]
    membership = tmp_path / "members.json"
    members = {
        "tenant_id": runtime.profile.organization_id,
        "members": [
            {"subject": "operator", "actor_id": runtime.profile.default_task.actor_id,
             "roles": ["administrator"]},
            {"subject": "replacement", "actor_id": "worker:replacement", "roles": ["administrator"]},
        ],
    }
    owner = next(item.owner_id for item in runtime.enterprise_binding.resources if item.slot_id == "launch_date")
    members["members"].append({"subject": "owner", "actor_id": owner, "roles": ["approver"]})
    membership.write_text(json.dumps(members))
    identity = IdentitySettings("https://issuer.example", "orgrebase-api", "https://issuer.example/jwks",
                                runtime.profile.organization_id, membership)
    settings = DeploymentSettings(mode="production", identity=identity, database_url=postgres_dsn,
                                  enterprise_pack=str(pack), allowed_hosts=("localhost",))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="worker-key", alg="RS256", use="sig")
    monkeypatch.setattr(VerifiedJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    monkeypatch.setattr(DeploymentSettings, "from_environment", classmethod(lambda cls: settings))
    monkeypatch.setenv("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "0")

    def access_token(subject="operator"):
        now = int(time.time())
        return jwt.encode({"iss": identity.issuer, "aud": identity.audience, "sub": subject,
                           "iat": now, "exp": now + 300, "token_use": "access"},
                          key, algorithm="RS256", headers={"kid": "worker-key"})

    monkeypatch.setenv("TEST_WORKER_ACCESS", access_token())
    source_token = secrets.token_urlsafe(32)
    monkeypatch.setenv("TEST_SOURCE_ACCESS", source_token)
    with TestClient(create_app(deployment_settings=settings), base_url="http://localhost") as client:
        response = client.post("/api/workspace/form", headers={"Authorization": "Bearer " + access_token()})
        assert response.status_code == 200, response.text
    config = {
        "source": {"connector_id": "source:worker", "tenant_id": identity.tenant_id,
                   "instance_url": "https://company.crm.dynamics.com", "record_ids": [RECORD],
                   "entity_set": "quotes"},
        "organization_id": ORGANIZATION,
        "source_token_variable": "TEST_SOURCE_ACCESS", "access_token_variable": "TEST_WORKER_ACCESS",
    }
    path = tmp_path / "source.json"
    path.write_text(json.dumps(config))
    settings = replace(settings, source_config=path)
    deployment = SimpleNamespace(settings=settings, config=config, path=path, membership=membership,
                           members=members, access_token=access_token, source_token=source_token)
    install_source(monkeypatch, deployment)
    discovery = run_source_sync(path, discover=True)
    with TestClient(create_app(deployment_settings=settings), base_url="http://localhost") as client:
        proposal = client.post("/api/workspace/source-binding/proposals", headers={"Authorization": "Bearer " + access_token()},
            json={"inventory_digest": discovery["inventory_digest"], "generation_digest": discovery["generation_digest"],
                  "mappings": [{"record_id": RECORD, "field": "new_launch_date", "slot_id": "launch_date"}]} )
        assert proposal.status_code == 200, proposal.text
        digest = proposal.json()["proposal"]["proposal_digest"]
        confirmed = client.post("/api/workspace/source-binding/proposals/" + digest[7:] + "/confirm",
            headers={"Authorization": "Bearer " + access_token("owner")}, json={"proposal_digest": digest})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "CONFIRMED", confirmed.text
    deployment.connector_id = config["source"]["connector_id"] + ":" + digest[7:39]
    return deployment



def source_metadata(url):
    if url.endswith("/WhoAmI"):
        return {"OrganizationId": ORGANIZATION}
    if "/EntityDefinitions" not in url:
        return None
    if "/Attributes" not in url:
        return {"LogicalName": "quote", "MetadataId": ORGANIZATION, "EntitySetName": "quotes",
                "PrimaryIdAttribute": "quoteid", "TableType": "Standard", "DataProviderId": None,
                "DataSourceId": None, "ChangeTrackingEnabled": True}
    return {"value": [{"LogicalName": "new_launch_date", "AttributeType": "String", "IsValidForRead": True,
                      "DisplayName": {"UserLocalizedLabel": {"Label": "Launch date"}}}]}

def install_source(monkeypatch, deployment, *, after_fetch=lambda: None):
    requests = []
    endpoint = deployment.config["source"]["instance_url"] + "/api/data/v9.2/quotes"

    class SourceTransport:
        def open(self, request, timeout):
            metadata = source_metadata(request.full_url)
            if metadata is not None:
                response = io.BytesIO(json.dumps(metadata).encode())
                response.status = 200
                return response
            requests.append(request.full_url)
            assert request.get_header("Authorization") == "Bearer " + deployment.source_token
            assert request.get_method() == "GET"
            assert 0 < timeout <= 30
            after_fetch()
            value = {
                "@odata.nextLink" if len(requests) == 1 else "@odata.deltaLink":
                    endpoint + ("?$skiptoken=page-2" if len(requests) == 1 else "?$deltatoken=watermark-1"),
                "value": [{"quoteid": RECORD, "@odata.etag": 'W/"opaque-a"',
                           "new_launch_date": "2030-01-01"}] if len(requests) == 1 else [],
            }
            if "/quotes(" in request.full_url:
                value = {"quoteid": RECORD, "@odata.etag": 'W/"opaque-a"', "new_launch_date": "2030-01-01"}
            response = io.BytesIO(json.dumps(value).encode())
            response.status = 200
            return response

    monkeypatch.setattr("orgrebase.workspace.dataverse.build_opener", lambda *args: SourceTransport())
    return requests


def test_worker_restarts_from_committed_cursor_without_replacing_canonical_facts(deployment, monkeypatch):
    requests = install_source(monkeypatch, deployment)
    before_principal = request_principal.get()
    before_authorization = request_authorization.get()
    first = run_source_sync(deployment.path, max_pages=1)
    assert first["status"] == "MORE_PAGES_PENDING"
    assert first["pages"][0]["records_admitted"] == 1
    assert first["external_writes"] == 0
    assert request_principal.get() is before_principal
    assert request_authorization.get() is before_authorization
    second = run_source_sync(deployment.path, max_pages=2)
    assert second["status"] == "SYNCED"
    assert len(requests) == 3 and requests[1].endswith("?$skiptoken=page-2")
    assert "/quotes(" in requests[2]
    service = open_workspace(deployment.settings)
    try:
        events = service.changes.all()
        assert len(events) == 1
        event = events[0]
        assert event.slot_id == "launch_date" and event.proposal.payload["canonical_value"] == "2030-01-01"
        current = service.store.get_object(event.proposal.id)
        assert current.digest == event.base_digest and current.version == event.base_version
        assert current.payload["canonical_value"] != "2030-01-01"
        assert service.store.get_source_checkpoint(deployment.connector_id)["cursor"].endswith("?$deltatoken=watermark-1")
    finally:
        service.close()


@pytest.mark.parametrize("failure", ["different_subject", "revoked_member"])
def test_worker_rechecks_identity_before_page_admission_and_cleans_context(deployment, monkeypatch, failure):
    def revoke():
        if failure == "different_subject":
            monkeypatch.setenv("TEST_WORKER_ACCESS", deployment.access_token("replacement"))
        else:
            deployment.membership.write_text(json.dumps({**deployment.members, "members": []}))

    requests = install_source(monkeypatch, deployment, after_fetch=revoke)
    principal, authorization = request_principal.get(), request_authorization.get()
    expected = (SourceError, "SOURCE_WORKER_IDENTITY_CHANGED") if failure == "different_subject" else (
        AuthenticationError, "AUTH_MEMBERSHIP_DENIED",
    )
    with pytest.raises(expected[0], match=expected[1]):
        run_source_sync(deployment.path)
    assert len(requests) == 1
    assert request_principal.get() is principal and request_authorization.get() is authorization
    service = open_workspace(deployment.settings)
    try:
        assert service.changes.all() == ()
        assert (service.store.get_source_checkpoint(deployment.connector_id) or {}).get("cursor") is None
    finally:
        service.close()


@pytest.mark.parametrize("status", [403, 429])
def test_worker_distinguishes_lost_source_permission_from_backpressure(deployment, monkeypatch, status):
    class RejectedSource:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, status, "rejected", {"Retry-After": "15"}, io.BytesIO())

    monkeypatch.setattr("orgrebase.workspace.dataverse.build_opener", lambda *args: RejectedSource())
    expected = (SourceError, "SOURCE_PERMISSION_DENIED") if status == 403 else (SourceRateLimited, "SOURCE_RATE_LIMITED")
    with pytest.raises(expected[0], match=expected[1]):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    try:
        source = service.store.get_object("claim:product.launch_date")
        if status == 403:
            assert source.state == ObjectState.STALE
            assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
            assert service.changes.gaps()[0]["slot_id"] == "launch_date"
        else:
            assert source.state == ObjectState.CURRENT
            assert service.current_quote().state == ObjectState.CURRENT
            assert service.changes.gaps() == ()
        assert service.changes.all() == ()
        assert (service.store.get_source_checkpoint(deployment.connector_id) or {}).get("cursor") is None
    finally:
        service.close()


def test_worker_accepts_renewed_token_only_for_the_same_principal(deployment, monkeypatch):
    install_source(monkeypatch, deployment, after_fetch=lambda: monkeypatch.setenv(
        "TEST_WORKER_ACCESS", deployment.access_token(),
    ))
    assert run_source_sync(deployment.path)["status"] == "SYNCED"


@pytest.mark.parametrize("budget", [0, 101])
def test_worker_rejects_invalid_page_budget_before_opening_config(tmp_path, budget):
    with pytest.raises(ValueError, match="SOURCE_PAGE_BUDGET_INVALID"):
        run_source_sync(tmp_path / "does-not-exist.json", max_pages=budget)


@pytest.mark.parametrize("config", [[], {}, {"source": {}, "mappings": [],
    "source_token_variable": "TOKEN;read", "access_token_variable": "ACCESS"}])
def test_worker_rejects_invalid_configuration_before_database_access(tmp_path, config):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="SOURCE_CONFIG_INVALID"):
        run_source_sync(path)
