from __future__ import annotations

import json
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx2 as httpx
import pytest
from test_browser_sessions import csrf_headers, login

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError
from orgrebase.browser_auth import SESSION_COOKIE
from orgrebase.domain import IntegrityError, VersionedObject
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace_catalog import load_catalog

pytest_plugins = ("test_browser_sessions",)


@pytest.mark.parametrize("payload", [
    '{"workspaces":[],"workspaces":[]}',
    '{"workspaces":[]}',
    '{"workspaces":[{"workspace_id":"../x","label":"x","enterprise_pack":"pack","allowed_subjects":["person"]}]}',
    '{"workspaces":[{"workspace_id":"a","label":"x","enterprise_pack":"pack","allowed_subjects":["*"]}]}',
    '{"workspaces":[{"workspace_id":"a","label":"x","enterprise_pack":"pack","allowed_subjects":["person","person"]}]}',
    '{"workspaces":[{"workspace_id":"a","label":"x","enterprise_pack":"pack","allowed_subjects":["person"],"database_url":"database"}]}',
])
def test_catalog_rejects_ambiguous_or_browser_supplied_configuration(tmp_path, payload):
    path = tmp_path / "catalog.json"
    path.write_text(payload)
    with pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_CATALOG_UNAVAILABLE"):
        load_catalog(path)


def test_catalog_normalizes_paths_and_rejects_mutable_or_symlink_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"workspaces": [{"workspace_id": "default", "label": "报价",
                                              "enterprise_pack": "pack", "source_config": "source.json", "allowed_subjects": ["person"]}]}))
    assert load_catalog(path).entry("default").enterprise_pack == str(tmp_path / "pack")
    assert load_catalog(path).entry("default").source_config == str(tmp_path / "source.json")
    symlink = tmp_path / "link.json"
    symlink.symlink_to(path)
    with pytest.raises(AuthenticationError):
        load_catalog(symlink)
    path.chmod(0o666)
    with pytest.raises(AuthenticationError):
        load_catalog(path)
    with pytest.raises(ValueError, match="WORKSPACE_ID_INVALID"):
        DeploymentSettings(workspace_id="../invalid")


def test_empty_subject_policy_denies_everyone_without_removing_workspace(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"workspaces": [{"workspace_id": "default", "label": "报价",
                                              "enterprise_pack": "pack", "allowed_subjects": []}]}))
    assert load_catalog(path).entry("default").allowed_subjects == []


def _headers(application, session, workspace):
    return {**csrf_headers(application, session), "X-OrgRebase-Workspace": workspace}


def _change(workspace, event_id):
    base = next(item for item in workspace.runtime_configuration.seed_objects if item.id == "claim:product.launch_date")
    now = workspace.clock.now()
    proposal = VersionedObject.model_validate({
        **base.model_dump(mode="json"), "version": "v99", "state": "PROPOSED", "digest": "",
        "payload": {**base.payload, "canonical_value": "2030-01-01"},
        "source_refs": ["test:observed-source-event:1"], "valid_from": now,
    })
    owner = next(item.owner_id for item in workspace.enterprise_binding.resources if item.slot_id == "launch_date")
    return ChangeEvent(event_id=event_id, organization_id=workspace.profile.organization_id, slot_id="launch_date",
                       owner_id=owner, base_version=base.version, base_digest=base.digest, proposal=proposal, occurred_at=now)


@pytest.mark.parametrize("browser_deployment", ["multiworkspace"], indirect=True)
def test_authenticated_child_error_has_scoped_safe_incident(browser_deployment, monkeypatch, caplog):
    application, _, client = browser_deployment
    login(client)
    child = application.application.state.workspace_applications["quote-b"].state.workspace_service

    def fail(**_kwargs):
        raise ValueError("private-customer-record contains secret-token")

    monkeypatch.setattr(child, "state", fail)
    response = client.get("/api/workspace/state", headers={"X-OrgRebase-Workspace": "quote-b"})
    assert response.status_code == 409
    incident = response.json()["detail"]["incident_id"]
    assert response.headers["X-OrgRebase-Incident"] == incident
    assert incident in caplog.text
    assert "private-customer-record" not in caplog.text + response.text
    assert "secret-token" not in caplog.text + response.text
    unaffected = client.get("/api/workspace/state")
    assert unaffected.status_code == 200
    assert "X-OrgRebase-Incident" not in unaffected.headers


@pytest.mark.parametrize("browser_deployment", ["multiworkspace"], indirect=True)
def test_real_https_workspaces_isolate_concurrent_form_events_and_restart(browser_deployment, monkeypatch):
    application, _, client = browser_deployment
    assert client.get("/api/workspaces").status_code == 401
    assert client.get("/api/workspace/state", headers={"X-OrgRebase-Workspace": "unknown"}).status_code == 401
    session = login(client)
    listing = client.get("/api/workspaces").json()
    assert listing == {"items": [{"workspace_id": "default", "label": "报价甲"},
                                  {"workspace_id": "quote-b", "label": "报价乙"}], "default_workspace_id": "default"}
    applications = {"default": application.application, **application.application.state.workspace_applications}
    assert applications["default"].state.workspace_run_progress is not applications["quote-b"].state.workspace_run_progress
    rendezvous = threading.Barrier(2)
    for app in applications.values():
        formation = app.state.workspace_service.formation
        original = formation.prepare_quote

        def prepare(*args, operation=original, **kwargs):
            rendezvous.wait(timeout=10)
            return operation(*args, **kwargs)

        monkeypatch.setattr(formation, "prepare_quote", prepare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, "/api/workspace/form", headers=_headers(application, session, workspace))
                    for workspace in applications]
        for request in requests:
            result = request.result(timeout=20)
            assert result.status_code == 200, result.text
    for workspace_id, app in applications.items():
        workspace = app.state.workspace_service
        assert workspace.store.check_health()["workspace_id"] == workspace_id
        event = _change(workspace, "event-" + workspace_id)
        response = client.post("/api/workspace/changes", headers=_headers(application, session, workspace_id),
                               json=event.model_dump(mode="json"))
        assert response.status_code == 200, response.text
    for workspace_id in applications:
        headers = _headers(application, session, workspace_id)
        state = client.get("/api/workspace/state", headers=headers)
        assert state.status_code == 200 and state.json()["workspace_id"] == workspace_id
        events = client.get("/api/workspace/changes", headers=headers).json()["items"]
        assert [item["event"]["event_id"] for item in events] == ["event-" + workspace_id]
    preview = client.post("/api/workspace/preview/event-default", headers=_headers(application, session, "default"))
    assert preview.status_code == 200, preview.text
    cross = client.post("/api/workspace/approve/event-default", headers=_headers(application, session, "quote-b"),
                        json={"preview_digest": preview.json()["preview_digest"]})
    assert cross.status_code in {404, 409}
    exported = client.get("/api/workspace/export/evidence", headers=_headers(application, session, "quote-b"))
    assert exported.status_code == 200 and "event-default" not in exported.text
    cookies = client.cookies
    client.close()
    application.restart()
    with httpx.Client(base_url=application.settings.browser_session.public_origin, cookies=cookies,
                      verify=ssl.create_default_context(cafile=str(application.certificate)), trust_env=False) as restarted:
        assert restarted.get("/api/session").json()["authenticated"] is True
        for workspace_id in applications:
            events = restarted.get("/api/workspace/changes", headers={"X-OrgRebase-Workspace": workspace_id}).json()["items"]
            assert [item["event"]["event_id"] for item in events] == ["event-" + workspace_id]
        old_cookie = restarted.cookies.get(SESSION_COOKIE)
        result = restarted.post("/api/session/logout", headers=csrf_headers(application, session))
        assert result.status_code == 200
        for workspace_id in applications:
            replay = restarted.get("/api/workspace/state", headers={"X-OrgRebase-Workspace": workspace_id,
                                                                    "Cookie": f"{SESSION_COOKIE}={old_cookie}"})
            assert replay.status_code == 401 and replay.json()["detail"]["code"] == "AUTH_SESSION_REVOKED"


@pytest.mark.parametrize("browser_deployment", ["multiworkspace"], indirect=True)
def test_workspace_scope_revocation_is_reloaded_at_commit_and_listing(browser_deployment, monkeypatch):
    application, _, client = browser_deployment
    session = login(client)
    settings = application.settings
    selected = settings.for_workspace("quote-b")
    assert selected.workspace_id == "quote-b" and selected.database_url == settings.database_url
    selected.authorize_workspace("operator")
    workspace = application.application.state.workspace_applications["quote-b"].state.workspace_service
    original = workspace.formation.commit_quote

    def revoke_then_commit(*args, **kwargs):
        catalog = json.loads(settings.workspace_catalog.read_bytes())
        catalog["workspaces"][1]["allowed_subjects"] = ["second-user"]
        settings.workspace_catalog.write_text(json.dumps(catalog))
        return original(*args, **kwargs)

    monkeypatch.setattr(workspace.formation, "commit_quote", revoke_then_commit)
    response = client.post("/api/workspace/form", headers=_headers(application, session, "quote-b"))
    assert response.status_code == 403, response.text
    assert workspace.state()["quote"] is None
    assert client.get("/api/workspaces").json()["items"] == [{"workspace_id": "default", "label": "报价甲"}]
    for identity in ["quote-b", "unknown", "../default"]:
        denied = client.get("/api/workspace/state", headers={"X-OrgRebase-Workspace": identity})
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "AUTH_WORKSPACE_DENIED"
    assert client.get("/api/workspace/state", headers=[("X-OrgRebase-Workspace", "default"),
                                                       ("X-OrgRebase-Workspace", "quote-b")]).status_code == 403
    with pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_DENIED"):
        selected.authorize_workspace("operator")
    assert client.get("/api/workspace/state").status_code == 200
    assert client.get("/api/session").json()["authenticated"] is True


@pytest.mark.parametrize("browser_deployment", ["multiworkspace"], indirect=True)
@pytest.mark.parametrize("configuration", ["effect_config", "source_config"])
def test_running_workspace_configuration_cannot_be_rebound_by_catalog_edit(browser_deployment, configuration):
    application, _, client = browser_deployment
    login(client)
    settings = application.settings.for_workspace("quote-b")
    catalog = json.loads(settings.workspace_catalog.read_bytes())
    catalog["workspaces"][1][configuration] = "new-config.json"
    settings.workspace_catalog.write_text(json.dumps(catalog))
    response = client.get("/api/workspace/state", headers={"X-OrgRebase-Workspace": "quote-b"})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "AUTH_WORKSPACE_CONFIGURATION_CHANGED"
    with pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_CONFIGURATION_CHANGED"):
        settings.authorize_workspace("operator")
    assert client.get("/api/workspace/state").status_code == 200
    with pytest.raises(AuthenticationError, match="AUTH_WORKSPACE_DENIED"):
        settings.for_workspace("unconfigured")


@pytest.mark.parametrize("browser_deployment", ["multiworkspace"], indirect=True)
def test_user_without_default_scope_can_log_in_and_list_only_their_workspace(browser_deployment):
    application, provider, client = browser_deployment
    membership = json.loads(application.settings.identity.membership_file.read_bytes())
    membership["members"].append({"subject": "second-user", "actor_id": "human:second", "roles": ["reader"]})
    application.settings.identity.membership_file.write_text(json.dumps(membership))
    provider.id_overrides["sub"] = "second-user"
    provider.access_overrides["sub"] = "second-user"
    session = login(client)
    assert session["principal"]["actor_id"] == "human:second"
    assert client.get("/api/workspaces").json() == {
        "items": [{"workspace_id": "quote-b", "label": "报价乙"}], "default_workspace_id": "quote-b",
    }
    assert client.get("/api/workspace/state").status_code == 403
    allowed = client.get("/api/workspace/state", headers={"X-OrgRebase-Workspace": "quote-b"})
    assert allowed.status_code == 200 and allowed.json()["workspace_id"] == "quote-b"
    assert client.post("/api/workspace/form", headers=_headers(application, session, "quote-b")).status_code == 403
    assert client.post("/api/session/logout", headers=csrf_headers(application, session)).status_code == 200


@pytest.mark.parametrize("browser_deployment", ["multiworkspace"], indirect=True)
def test_catalog_does_not_provision_unregistered_workspaces(browser_deployment):
    application, _, _ = browser_deployment
    settings = application.settings
    catalog = json.loads(settings.workspace_catalog.read_bytes())
    catalog["workspaces"].append({**catalog["workspaces"][0], "workspace_id": "not-provisioned"})
    settings.workspace_catalog.write_text(json.dumps(catalog))
    with pytest.raises(IntegrityError, match="STATE_STORE_WORKSPACE_NOT_REGISTERED"):
        create_app(deployment_settings=settings)
    with pytest.raises(KeyError, match="WORKSPACE_NOT_REGISTERED"):
        application.application.state.workspace_service.store.get_workspace("not-provisioned")
