from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace import matrix_workspace as bridge


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("ORGREBASE_MATRIX_OBSERVER_CONFIG", str(tmp_path / "observer.json"))
    monkeypatch.setenv("ORGREBASE_PUBLIC_WORKSPACE_URL", "http://127.0.0.1:8015")
    state = {
        "stage": "CURRENT",
        "business_complete": True,
        "quote": {
            "id": "quote:blue",
            "version": "v3",
            "digest": "sha256:" + "2" * 64,
            "payload": {"confidential_price": "must not be published"},
        },
        "agentteams_operations": {
            "formation_taskflow": {
                "run_id": "run:one",
                "native_taskflow_observed": True,
                "summary_digest": "sha256:" + "1" * 64,
                "action_count": 40,
            }
        },
        "competition_evidence": {
            "agent_collaboration": {
                "orchestration_plan": {"project_id": "project:one"},
                "agent_runs": [{"task_id": "task:one", "authority_domain": "finance", "status": "PASS"}],
            }
        },
    }
    return SimpleNamespace(
        profile=SimpleNamespace(
            organization_id="org:one", default_task=SimpleNamespace(actor_id="actor:one")
        ),
        store=SimpleNamespace(workspace_id="work:one"),
        state=lambda: copy.deepcopy(state),
    )


def test_room_publication_uses_server_snapshot_and_excludes_business_payload(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "load_matrix_config", lambda _: object())
    monkeypatch.setattr(
        bridge, "publish_observation", lambda *args, **kwargs: calls.append(kwargs) or {"status": "OBSERVED"}
    )
    before = workspace.state()
    assert (
        bridge.publish_workspace_observation(workspace, actor_id="actor:one", expected_run_id="run:one")[
            "status"
        ]
        == "OBSERVED"
    )
    assert calls[0]["workspace_id"] == "org:one/work:one"
    assert calls[0]["snapshot"]["quote_revision"] == 3
    assert calls[0]["snapshot"]["quote_ref"] == "quote:blue@v3"
    assert "confidential_price" not in str(calls)
    assert workspace.state() == before


@pytest.mark.parametrize(
    "actor,run,error",
    [
        ("actor:other", "run:one", AuthorizationError),
        ("actor:one", "run:other", IntegrityError),
    ],
)
def test_wrong_actor_or_stale_run_cannot_load_credentials_or_publish(
    workspace, monkeypatch, actor, run, error
):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected requests must not reach the credential or publication boundary")

    monkeypatch.setattr(bridge, "load_matrix_config", forbidden)
    monkeypatch.setattr(bridge, "publish_observation", forbidden)
    with pytest.raises(error):
        bridge.publish_workspace_observation(workspace, actor_id=actor, expected_run_id=run)


@pytest.mark.parametrize("status_code", [401, 403])
def test_publication_rechecks_request_authorization_and_preserves_denial(
    workspace, monkeypatch, status_code,
):
    checks = []
    denied = AuthenticationError(
        "AUTH_TOKEN_EXPIRED" if status_code == 401 else "AUTH_MEMBERSHIP_DENIED",
        status_code,
    )

    def authorize():
        checks.append("checked")
        if len(checks) > 1:
            raise denied

    def publish(*_args, **kwargs):
        assert kwargs["authorize"] is authorize
        kwargs["authorize"]()
        pytest.fail("A revoked request must not report a publication success")

    monkeypatch.setattr(bridge, "current_authorization", lambda: authorize)
    monkeypatch.setattr(bridge, "load_matrix_config", lambda _: object())
    monkeypatch.setattr(bridge, "publish_observation", publish)
    monkeypatch.setattr(
        bridge, "record_observation_failure",
        lambda *_args, **_kwargs: pytest.fail("Identity denial is not a Matrix network failure"),
    )
    before = workspace.state()
    with pytest.raises(AuthenticationError) as caught:
        bridge.publish_workspace_observation(workspace, actor_id="actor:one", expected_run_id="run:one")
    assert caught.value is denied
    assert caught.value.status_code == status_code
    assert len(checks) == 2
    assert workspace.state() == before
    checks.clear()
    workspace.store_path = None
    workspace.approval_identity_mode = "CONTROLLED_LOCAL_HEADER_IDENTITY"
    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.post(
            "/api/workspace/agentteams-observation",
            json={"actor_id": "actor:one", "run_id": "run:one"},
            headers={"X-OrgReBase-Actor": "actor:one"},
        )
    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": denied.code}}
    assert len(checks) == 2
    assert workspace.state() == before


def test_empty_state_projection_is_read_only_without_loading_credentials(workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "load_matrix_config", lambda _: pytest.fail("Read path loaded credentials"))
    assert bridge.observation_view(workspace, {}) == {"status": "WAITING_FOR_RUN"}
    assert not list(tmp_path.iterdir())
    monkeypatch.delenv("ORGREBASE_MATRIX_OBSERVER_CONFIG")
    assert bridge.observation_view(workspace, workspace.state()) == {"status": "NOT_CONFIGURED"}


def test_changed_quote_marks_previous_observation_stale_without_io(workspace, monkeypatch):
    previous = {
        "status": "OBSERVED",
        "source_digest": "sha256:" + "0" * 64,
        "run_id": "run:one",
        "element_room_url": "http://127.0.0.1:18091/#/room/old",
    }
    monkeypatch.setattr(bridge, "public_observation", lambda *args, **kwargs: previous)
    monkeypatch.setattr(bridge, "load_matrix_config", lambda _: pytest.fail("GET read credentials"))
    state = workspace.state()
    assert bridge.observation_view(workspace, state) == {
        "status": "STALE",
        "source_digest": previous["source_digest"],
        "run_id": "run:one",
    }
    assert workspace.state() == state


@pytest.mark.parametrize("failure", ["private_mode", "missing_url"])
def test_configuration_failure_is_persisted_against_previous_receipt(workspace, monkeypatch, failure):
    previous = {"status": "OBSERVED", "digest": "sha256:" + "3" * 64}
    monkeypatch.setattr(bridge, "public_observation", lambda *args, **kwargs: previous)
    records = []
    monkeypatch.setattr(bridge, "record_observation_failure", lambda *args, **kwargs: records.append(kwargs))
    if failure == "private_mode":

        def invalid(_path):
            raise bridge.MatrixObservationError("MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE")

        monkeypatch.setattr(bridge, "load_matrix_config", invalid)
    else:
        monkeypatch.setattr(bridge, "load_matrix_config", lambda _: object())
        monkeypatch.delenv("ORGREBASE_PUBLIC_WORKSPACE_URL")
    monkeypatch.setattr(bridge, "publish_observation", lambda *args, **kwargs: pytest.fail("Unexpected send"))
    with pytest.raises(bridge.MatrixObservationError):
        bridge.publish_workspace_observation(workspace, actor_id="actor:one", expected_run_id="run:one")
    assert records[0]["expected_receipt_digest"] == previous["digest"]
    assert records[0]["workspace_id"] == "org:one/work:one"
    assert records[0]["run_id"] == "run:one"


def test_same_source_observation_remains_file_only(workspace, monkeypatch):
    state = workspace.state()
    source = sha256_digest(
        {
            "summary_digest": state["agentteams_operations"]["formation_taskflow"]["summary_digest"],
            "quote_digest": state["quote"]["digest"],
            "stage": state["stage"],
            "business_complete": bool(state.get("business_complete")),
        }
    )
    previous = {"status": "OBSERVED", "source_digest": source, "run_id": "run:one"}
    monkeypatch.setattr(bridge, "public_observation", lambda *args, **kwargs: previous)
    monkeypatch.setattr(bridge, "load_matrix_config", lambda _: pytest.fail("GET read credentials"))
    assert bridge.observation_view(workspace, state) == previous


def test_workspace_keys_do_not_collide_when_identifiers_contain_separator(workspace):
    workspace.profile.organization_id = "org:one/branch"
    first = bridge._workspace_key(workspace)
    workspace.profile.organization_id = "org:one"
    workspace.store.workspace_id = "branch/work:one"
    assert bridge._workspace_key(workspace) != first
