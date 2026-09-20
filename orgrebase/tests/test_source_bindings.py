from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_source_worker import RECORD, install_source, source_metadata

from orgrebase.api import create_app
from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.domain import ObjectState
from orgrebase.runtime_config import open_workspace
from orgrebase.workspace.dataverse import DataverseReader, SourceError
from orgrebase.workspace.source_bindings import (
    binding_view,
    load_source_config,
    read_inventory,
    source_coverage,
)
from orgrebase.workspace.source_worker import run_source_sync

pytest_plugins = ("test_source_worker",)


def client_for(deployment):
    return TestClient(create_app(deployment_settings=deployment.settings), base_url="http://localhost")


def headers(deployment, subject="operator"):
    return {"Authorization": "Bearer " + deployment.access_token(subject)}


def test_confirmed_mapping_is_owned_and_revocation_cannot_be_overwritten(deployment):
    with client_for(deployment) as client:
        view = client.get("/api/workspace/source-binding", headers=headers(deployment, "owner")).json()
        assert view["status"] == "CONFIRMED" and view["allowed_actions"] == ["REVOKE"]
        assert view["candidates"][0]["candidate_only"] is True
        digest = view["proposal"]["proposal_digest"]
        path = "/api/workspace/source-binding/proposals/" + digest[7:]
        wrong = client.post(path + "/revoke", headers=headers(deployment), json={"proposal_digest": digest})
        assert wrong.status_code == 403 and wrong.json()["detail"]["code"] == "SOURCE_ORIGINAL_OWNER_REQUIRED"
        revoked = client.post(path + "/revoke", headers=headers(deployment, "owner"), json={"proposal_digest": digest})
        assert revoked.status_code == 200 and revoked.json()["proposal"]["decisions"][0]["status"] == "REVOKED"
        retried = client.post(path + "/confirm", headers=headers(deployment, "owner"), json={"proposal_digest": digest})
        assert retried.status_code == 409 and retried.json()["detail"]["code"] == "SOURCE_REVOKED_PROPOSAL_REQUIRES_REVISION"
    with pytest.raises(SourceError, match="SOURCE_MAPPING_CONFIRMATION_REQUIRED"):
        run_source_sync(deployment.path)


@pytest.mark.parametrize("mutation", ["record", "slot", "field", "transform", "inventory", "generation", "owner"])
def test_client_cannot_manufacture_mapping_scope_semantics_or_authority(deployment, mutation):
    with client_for(deployment) as client:
        view = client.get("/api/workspace/source-binding", headers=headers(deployment)).json()
        mapping = {"record_id": RECORD, "field": "new_launch_date", "slot_id": "launch_date", "transform": "identity"}
        command = {"inventory_digest": view["inventory_digest"], "generation_digest": view["generation_digest"], "mappings": [mapping]}
        if mutation in {"inventory", "generation"}:
            command[mutation + "_digest"] = "sha256:" + "0" * 64
        elif mutation == "owner":
            mapping["owner_id"] = "human:attacker"
        else:
            mapping[{"record": "record_id", "slot": "slot_id", "field": "field", "transform": "transform"}[mutation]] = {
                "record": "00000000-0000-0000-0000-000000000999", "slot": "invented", "field": "secret_field", "transform": "date_only",
            }[mutation]
        result = client.post("/api/workspace/source-binding/proposals", headers=headers(deployment), json=command)
        assert result.status_code in {409, 422}, result.text
        after = client.get("/api/workspace/source-binding", headers=headers(deployment)).json()
        assert after["proposal"]["proposal_digest"] == view["proposal"]["proposal_digest"]
        injected = client.post("/api/workspace/source-binding/inventory", headers=headers(deployment), json={"fields": []})
        assert injected.status_code in {404, 405}


def test_metadata_drift_invalidates_old_confirmation_even_if_schema_later_returns(deployment, monkeypatch):
    prior = source_metadata
    def changed(url):
        result = prior(url)
        if result and "value" in result:
            result["value"][0]["DisplayName"]["UserLocalizedLabel"]["Label"] = "Changed semantic label"
        return result
    monkeypatch.setattr("test_source_worker.source_metadata", changed)
    with pytest.raises(SourceError, match="SOURCE_MAPPING_RECONFIRMATION_REQUIRED"):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    try:
        assert service.store.get_object("claim:product.launch_date").state == ObjectState.STALE
    finally:
        service.close()
    monkeypatch.setattr("test_source_worker.source_metadata", prior)
    with pytest.raises(SourceError, match="SOURCE_MAPPING_RECONFIRMATION_REQUIRED"):
        run_source_sync(deployment.path)
    with client_for(deployment) as client:
        result = client.get("/api/workspace/source-binding", headers=headers(deployment)).json()
        assert result["status"] == "MAPPING_REQUIRED" and result["proposal"] is None


@pytest.mark.parametrize("mode", ["role_removed", "actor_rebound", "scope_removed"])
def test_confirmed_owner_must_keep_current_role_actor_and_workspace_scope(deployment, monkeypatch, mode):
    membership = json.loads(deployment.membership.read_bytes())
    owner = next(item for item in membership["members"] if item["subject"] == "owner")
    if mode == "role_removed":
        owner["roles"] = ["reader"]
    elif mode == "actor_rebound":
        owner["actor_id"] = "human:replacement-owner"
    else:
        from orgrebase.auth import AuthenticationError
        original = deployment.settings.authorize_workspace
        def scope(self, subject):
            if subject == "owner":
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)
            original(subject)
        monkeypatch.setattr(type(deployment.settings), "authorize_workspace", scope)
    deployment.membership.write_text(json.dumps(membership))
    with pytest.raises(SourceError, match="SOURCE_MAPPING_CONFIRMATION_REQUIRED"):
        run_source_sync(deployment.path)
    with client_for(deployment) as client:
        view = client.get("/api/workspace/source-binding", headers=headers(deployment)).json()
        assert view["coverage"]["status"] == "UNKNOWN"


def test_coverage_comes_from_committed_pages_and_stable_watermark(deployment, monkeypatch):
    install_source(monkeypatch, deployment)
    first = run_source_sync(deployment.path, max_pages=1)
    assert first["coverage"]["status"] == "UNKNOWN"
    assert first["coverage"]["reasons"] == ["SOURCE_PAGINATION_INCOMPLETE"]
    second = run_source_sync(deployment.path)
    coverage = second["coverage"]
    assert coverage["status"] == "COMPLETE" and coverage["record_ids"] == [RECORD]
    assert coverage["records"][RECORD]["revision"] == 'W/"opaque-a"'
    assert "2030-01-01" not in json.dumps(coverage)
    with client_for(deployment) as client:
        before = client.get("/api/workspace/source-binding", headers=headers(deployment)).json()["coverage"]
        after = client.get("/api/workspace/source-binding", headers=headers(deployment)).json()["coverage"]
        assert before == after == coverage


def test_expired_inventory_and_coverage_fail_closed_without_another_fetch(deployment, monkeypatch):
    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path)["coverage"]["status"] == "COMPLETE"
    service = open_workspace(deployment.settings)
    try:
        from orgrebase.auth import JWTAuthenticator, request_principal
        principal = JWTAuthenticator(deployment.settings.identity).authenticate(headers(deployment)["Authorization"])
        token = request_principal.set(principal)
        try:
            config = load_source_config(deployment.path)
            current = source_coverage(service, config)
            original_clock = service.clock
            service.clock = FrozenClock(timestamp(utc_datetime(current["observed_at"]) - timedelta(seconds=60)))
            assert source_coverage(service, config)["status"] == "UNKNOWN"
            service.clock = original_clock
            assert source_coverage(service, config, connection=object())["reasons"] == ["SOURCE_FOREIGN_CONNECTION"]
            expired = source_coverage(service, config, now=timestamp(utc_datetime(current["expires_at"]) + timedelta(seconds=1)))
            assert expired["status"] == "UNKNOWN" and "SOURCE_COVERAGE_EXPIRED" in expired["reasons"]
            service.clock = FrozenClock(timestamp(utc_datetime(current["expires_at"]) + timedelta(seconds=1)))
            view = binding_view(service, config)
            assert "PROPOSE" not in view["allowed_actions"] and view["coverage"]["status"] == "UNKNOWN"
        finally:
            request_principal.reset(token)
    finally:
        service.close()


def test_production_rejects_arbitrary_config_path_and_old_unconfirmed_mapping_config(deployment, tmp_path):
    other = tmp_path / "copy.json"
    other.write_bytes(deployment.path.read_bytes())
    with pytest.raises(SourceError, match="SOURCE_CONFIGURATION_PATH_MISMATCH"):
        run_source_sync(other)
    value = json.loads(other.read_bytes())
    value["mappings"] = [{"record_id": RECORD, "field": "new_launch_date", "slot_id": "launch_date"}]
    other.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="SOURCE_CONFIG_INVALID"):
        load_source_config(other)
    other.write_text('{"source":{},"source":{}}')
    with pytest.raises(ValueError, match="SOURCE_CONFIG_INVALID"):
        load_source_config(other)


def test_missing_configuration_differs_from_invalid_configured_path(tmp_path):
    with pytest.raises(ValueError, match=r"^SOURCE_CONFIG_ABSENT$"):
        load_source_config(None)
    with pytest.raises(ValueError, match=r"^SOURCE_CONFIG_INVALID$"):
        load_source_config(tmp_path / "not-created.json")


def test_owner_can_revoke_even_when_source_inventory_is_unavailable(deployment):
    from orgrebase.workspace.source_bindings import mark_unavailable
    service = open_workspace(deployment.settings)
    try:
        mark_unavailable(service, load_source_config(deployment.path), "SOURCE_PERMISSION_DENIED")
    finally:
        service.close()
    with client_for(deployment) as client:
        view = client.get("/api/workspace/source-binding", headers=headers(deployment, "owner")).json()
        assert "REVOKE" in view["allowed_actions"] and "CONFIRM" not in view["allowed_actions"]
        digest = view["proposal"]["proposal_digest"]
        response = client.post("/api/workspace/source-binding/proposals/" + digest[7:] + "/revoke",
                               headers=headers(deployment, "owner"), json={"proposal_digest": digest})
        assert response.status_code == 200 and response.json()["proposal"]["decisions"][0]["status"] == "REVOKED"


@pytest.mark.parametrize("type_name,behavior,allowed", [
    ("DateTime", "DateOnly", ["date_only"]), ("DateTime", "UserLocal", []),
    ("DateTime", "TimeZoneIndependent", []), ("Lookup", None, []),
])
def test_semantic_candidates_require_explicit_supported_wire_type(deployment, type_name, behavior, allowed):
    config = load_source_config(deployment.path)
    reader = DataverseReader(config.source.reader_settings(("quoteid",)), lambda: "not-used")
    def metadata(path):
        if path.endswith("/WhoAmI"):
            return {"OrganizationId": config.organization_id}
        if "DateTimeAttributeMetadata" in path:
            return {"value": [{"LogicalName": "new_launch_date", "DateTimeBehavior": {"Value": behavior}}]}
        document = source_metadata(path)
        if "value" in document:
            document["value"][0]["AttributeType"] = type_name
        return document
    reader.metadata = metadata
    assert read_inventory(reader, config)["fields"][0]["transforms"] == allowed
