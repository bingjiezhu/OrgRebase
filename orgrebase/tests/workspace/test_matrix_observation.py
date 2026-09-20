from __future__ import annotations

import copy
import dataclasses
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from orgrebase.auth import AuthenticationError
from orgrebase.digest import sha256_digest
from orgrebase.workspace.matrix_observation import (
    BOUNDARY,
    MatrixObservationConfig,
    MatrixObservationError,
    load_matrix_config,
    public_observation,
    publish_observation,
    record_observation_failure,
)


@pytest.fixture
def homeserver():
    """An explicit HTTP protocol fixture, not evidence of a deployed Matrix server."""
    state = SimpleNamespace(
        aliases={},
        rooms={},
        events={},
        creates=0,
        puts=0,
        whoami="@observer:local.test",
        bad_sender=False,
        fail_send=False,
        wrong_room=False,
        ambiguous_create=False,
        requests=0,
        extra_members=[],
        guest=False,
        room_guest=False,
        federated=False,
        writable_viewer=False,
    )

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body, status=200):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def handle_request(self):
            state.requests += 1
            assert self.headers["Authorization"] == "Bearer private-test-token"
            path = urllib.parse.unquote(self.path.removeprefix("/_matrix/client/v3"))
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length)) if length else None
            if path == "/account/whoami":
                return self.reply({"user_id": state.whoami, "is_guest": state.guest})
            if path.startswith("/directory/room/"):
                room_id = state.aliases.get(path.removeprefix("/directory/room/"))
                return self.reply({"room_id": room_id}, 200 if room_id else 404)
            if path == "/createRoom":
                alias = "#" + payload["room_alias_name"] + ":local.test"
                if alias in state.aliases:
                    return self.reply({}, 409)
                state.creates += 1
                room_id = f"!room{state.creates}:local.test"
                state.aliases[alias] = room_id
                state.rooms[room_id] = payload
                return self.reply({"room_id": room_id}, 500 if state.ambiguous_create else 200)
            if path.startswith("/rooms/"):
                room_id, operation = path.removeprefix("/rooms/").split("/", 1)
                room = state.rooms[room_id]
                if operation == "state/orgrebase.observation/":
                    binding = copy.deepcopy(room["initial_state"][0]["content"])
                    if state.wrong_room:
                        binding["run_id"] = "run:other"
                    return self.reply(binding)
                if operation == "state/m.room.join_rules/":
                    return self.reply({"join_rule": "invite"})
                if operation == "state/m.room.create/":
                    return self.reply({"m.federate": True} if state.federated else room["creation_content"])
                if operation == "state/m.room.guest_access/":
                    return self.reply({"guest_access": "can_join" if state.room_guest else "forbidden"})
                if operation == "state/m.room.power_levels/":
                    levels = copy.deepcopy(room["power_level_content_override"])
                    if state.writable_viewer:
                        levels["events_default"] = 0
                    return self.reply(levels)
                if operation == "state/m.room.history_visibility/":
                    return self.reply({"history_visibility": "shared"})
                if operation == "members":
                    return self.reply(
                        {
                            "chunk": [
                                {"state_key": "@observer:local.test", "content": {"membership": "join"}},
                                *[
                                    {"state_key": user, "content": {"membership": "invite"}}
                                    for user in room["invite"]
                                ],
                                *state.extra_members,
                            ]
                        }
                    )
                if operation.startswith("send/m.room.message/"):
                    state.puts += 1
                    if state.fail_send:
                        return self.reply({"error": "private server details"}, 503)
                    transaction = operation.rsplit("/", 1)[-1]
                    event_id = "$" + transaction
                    state.events.setdefault(
                        event_id,
                        {
                            "event_id": event_id,
                            "sender": "@observer:local.test",
                            "room_id": room_id,
                            "type": "m.room.message",
                            "content": payload,
                        },
                    )
                    return self.reply({"event_id": event_id})
                if operation.startswith("event/"):
                    event = copy.deepcopy(state.events[operation.removeprefix("event/")])
                    if state.bad_sender:
                        event["sender"] = "@another:local.test"
                    return self.reply(event)
            return self.reply({}, 404)

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state.config = MatrixObservationConfig(
        homeserver_url=f"http://127.0.0.1:{server.server_port}",
        element_url="http://127.0.0.1:28080/element",
        access_token="private-test-token",
        user_id="@observer:local.test",
        allowed_workspace_ids=("workspace:one",),
        viewer_user_id="@viewer:local.test",
    )
    try:
        yield state
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def snapshot(**updates):
    return {
        "native_project_id": "project:quote-one",
        "source_digest": "sha256:" + "a" * 64,
        "action_count": 40,
        "task_summaries": [
            {"task_id": "finance:attempt2", "domain": "finance", "status": "PASS"},
        ],
        "quote_revision": 1,
        "quote_ref": "quote:one@v1",
        "phase": "COMPLETED",
        **updates,
    }


def publish(server, directory, **kwargs):
    return publish_observation(
        server.config,
        directory,
        workspace_id="workspace:one",
        run_id="run:one",
        snapshot=snapshot(**kwargs),
        workspace_url="http://127.0.0.1:8014",
    )


def test_real_http_send_readback_and_retries_keep_one_room_and_event(homeserver, tmp_path):
    first = publish(homeserver, tmp_path)
    second = publish(homeserver, tmp_path)
    assert first["status"] == second["status"] == "OBSERVED"
    assert first["event_id"] == second["event_id"]
    assert homeserver.creates == len(homeserver.events) == 1
    assert homeserver.puts == 2
    content = homeserver.events[first["event_id"]]["content"]
    assert "财务 (finance) · 通过 (PASS)" in content["body"]
    assert "任务: finance:attempt2" in content["body"]
    assert content["orgrebase.observation"]["snapshot"] == snapshot()
    assert first["claim_boundary"] == BOUNDARY
    assert first["canonical_writes"] == 0
    assert first["element_room_url"].startswith("http://127.0.0.1:28080/element/#/room/%21room1")
    assert "private-test-token" not in json.dumps(first)
    assert "private-test-token" not in repr(homeserver.config)
    assert not any("private-test-token" in p.read_text() for p in tmp_path.rglob("*.json"))


def test_task_observation_preserves_attempts_and_unknown_states(homeserver, tmp_path):
    tasks = [
        {"task_id": "finance:attempt1", "domain": "finance", "status": "ABSTAIN"},
        {"task_id": "finance:attempt2", "domain": "finance", "status": "PASS"},
        {"task_id": "review:attempt2", "domain": "review", "status": "TRUSTED_COMPLETE"},
        {"task_id": "custom:attempt1", "domain": "custom", "status": "CUSTOM_STATUS"},
    ]
    receipt = publish(homeserver, tmp_path, task_summaries=tasks)
    content = homeserver.events[receipt["event_id"]]["content"]
    assert "证据不足 · 暂不出结论 (ABSTAIN)" in content["body"]
    assert "custom · CUSTOM_STATUS" in content["body"]
    assert "独立复核 (review) · 任务验收通过 (TRUSTED_COMPLETE)" in content["body"]
    assert all(task["task_id"] in content["body"] for task in tasks)
    assert content["orgrebase.observation"]["snapshot"]["task_summaries"] == tasks


def test_changed_snapshot_is_new_event_in_same_bound_room(homeserver, tmp_path):
    before = publish(homeserver, tmp_path)
    after = publish(homeserver, tmp_path, quote_revision=2, quote_ref="quote:one@v2")
    assert before["room_id"] == after["room_id"]
    assert before["event_id"] != after["event_id"]
    assert homeserver.creates == 1 and len(homeserver.events) == 2


def test_ambiguous_creation_resolves_alias_without_duplicate(homeserver, tmp_path):
    homeserver.ambiguous_create = True
    assert publish(homeserver, tmp_path)["status"] == "OBSERVED"
    assert publish(homeserver, tmp_path)["status"] == "OBSERVED"
    assert homeserver.creates == 1


@pytest.mark.parametrize("boundary", ["lock", "create", "send"])
@pytest.mark.parametrize("status_code", [401, 403])
def test_authorization_is_current_after_waiting_and_before_external_effects(
    homeserver, tmp_path, boundary, status_code,
):
    denied = AuthenticationError(
        "AUTH_TOKEN_EXPIRED" if status_code == 401 else "AUTH_MEMBERSHIP_DENIED",
        status_code,
    )
    checked_at = []

    def authorize():
        checked_at.append((homeserver.requests, homeserver.creates, homeserver.puts))
        if (
            boundary == "lock"
            or (boundary == "create" and homeserver.requests >= 2)
            or (boundary == "send" and homeserver.creates > 0)
        ):
            raise denied

    with pytest.raises(AuthenticationError) as caught:
        publish_observation(
            homeserver.config,
            tmp_path,
            workspace_id="workspace:one",
            run_id="run:one",
            snapshot=snapshot(),
            workspace_url="http://127.0.0.1:8014",
            authorize=authorize,
        )
    assert caught.value is denied
    assert caught.value.status_code == status_code
    assert len(checked_at) == {"lock": 1, "create": 2, "send": 3}[boundary]
    assert homeserver.creates == (1 if boundary == "send" else 0)
    assert homeserver.puts == len(homeserver.events) == 0
    if boundary == "lock":
        assert homeserver.requests == 0
    current = public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")
    assert current["status"] == "NOT_PUBLISHED"
    assert "element_room_url" not in current
    # Denial releases the file lock. A newly authorized request can reuse the
    # already-created room without borrowing a publication success.
    assert publish(homeserver, tmp_path)["status"] == "OBSERVED"
    assert homeserver.creates == homeserver.puts == 1


@pytest.mark.parametrize(
    "fault,code",
    [
        ("whoami", "MATRIX_OBSERVATION_SENDER_MISMATCH"),
        ("bad_sender", "MATRIX_OBSERVATION_EVENT_READBACK_MISMATCH"),
        ("wrong_room", "MATRIX_OBSERVATION_ROOM_BINDING_MISMATCH"),
        ("fail_send", "MATRIX_OBSERVATION_HTTP_503"),
    ],
)
def test_unverified_network_results_never_publish_success(homeserver, tmp_path, fault, code):
    setattr(homeserver, fault, "@wrong:local.test" if fault == "whoami" else True)
    with pytest.raises(MatrixObservationError, match=code):
        publish(homeserver, tmp_path)
    current = public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")
    assert current["status"] == "UNAVAILABLE"
    assert "element_room_url" not in current
    assert "private server details" not in json.dumps(current)


def test_new_failure_does_not_keep_old_success_as_current(homeserver, tmp_path):
    publish(homeserver, tmp_path)
    homeserver.fail_send = True
    with pytest.raises(MatrixObservationError):
        publish(homeserver, tmp_path, quote_revision=2)
    assert (
        public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")["status"]
        == "UNAVAILABLE"
    )


def test_public_reads_are_file_only_and_refuse_cross_run(homeserver, tmp_path):
    missing = tmp_path / "not-created"
    assert (
        public_observation(missing, workspace_id="workspace:one", run_id="run:one")["status"]
        == "NOT_PUBLISHED"
    )
    assert not missing.exists()
    publish(homeserver, tmp_path)
    request_count = homeserver.requests
    assert (
        public_observation(tmp_path, workspace_id="workspace:one", run_id="run:two")["status"]
        == "NOT_PUBLISHED"
    )
    receipt = next(tmp_path.rglob("observation.json"))
    data = json.loads(receipt.read_text())
    data["run_id"] = "run:two"
    data["digest"] = sha256_digest({k: v for k, v in data.items() if k != "digest"})
    receipt.write_text(json.dumps(data))
    assert (
        public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")["status"]
        == "UNAVAILABLE"
    )
    assert homeserver.requests == request_count


def test_run_cannot_switch_native_project(homeserver, tmp_path):
    publish(homeserver, tmp_path)
    with pytest.raises(MatrixObservationError, match="RUN_BINDING_MISMATCH"):
        publish(homeserver, tmp_path, native_project_id="project:another")
    assert homeserver.creates == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://observer:secret@example.com",
        "javascript:alert(1)",
        "https://example.com?access_token=secret",
        "https://example.com/#/room/other",
    ],
)
def test_config_rejects_unsafe_urls(url):
    with pytest.raises(MatrixObservationError, match="URL_INVALID"):
        MatrixObservationConfig(
            url, "http://localhost:28080/element", "token", "@observer:local.test", ("workspace:one",)
        )


def test_config_requires_private_file_and_token(tmp_path):
    file = tmp_path / "matrix.json"
    file.write_text(
        json.dumps(
            {
                "homeserver_url": "http://localhost:28080/matrix",
                "element_url": "http://localhost:28080/element",
                "access_token": "",
                "user_id": "@observer:local.test",
                "allowed_workspace_ids": ["workspace:one"],
            }
        )
    )
    file.chmod(0o644)
    with pytest.raises(MatrixObservationError, match="CONFIG_NOT_PRIVATE"):
        load_matrix_config(file)
    file.chmod(0o600)
    with pytest.raises(MatrixObservationError, match="TOKEN_MISSING"):
        load_matrix_config(file)


def test_arbitrary_snapshot_fields_are_rejected_before_network(homeserver, tmp_path):
    before = homeserver.requests
    with pytest.raises(MatrixObservationError, match="SNAPSHOT_INVALID"):
        publish(homeserver, tmp_path, raw_prompt="must not be published")
    assert homeserver.requests == before


def test_public_receipt_symlink_is_rejected(homeserver, tmp_path):
    publish(homeserver, tmp_path)
    receipt = next(tmp_path.rglob("observation.json"))
    moved = receipt.with_name("original.json")
    receipt.rename(moved)
    receipt.symlink_to(moved)
    assert (
        public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")["status"]
        == "UNAVAILABLE"
    )


def test_cross_workspace_publication_is_rejected_before_io(homeserver, tmp_path):
    directory = tmp_path / "must-not-be-created"
    requests = homeserver.requests
    with pytest.raises(MatrixObservationError, match="WORKSPACE_FORBIDDEN"):
        publish_observation(
            homeserver.config,
            directory,
            workspace_id="another-tenant:workspace",
            run_id="run:one",
            snapshot=snapshot(),
            workspace_url="http://127.0.0.1:8014",
        )
    assert not directory.exists()
    assert homeserver.requests == requests
    assert homeserver.creates == homeserver.puts == 0


@pytest.mark.parametrize("scope", [[], (), "workspace:one", [""], [" "], ["workspace:one", "workspace:one"]])
def test_configuration_scope_must_be_explicit_and_nonempty(scope):
    with pytest.raises(MatrixObservationError, match="WORKSPACE_SCOPE_INVALID"):
        MatrixObservationConfig(
            "http://127.0.0.1:28080",
            "http://127.0.0.1:28088",
            "token",
            "@observer:local.test",
            scope,
        )


@pytest.mark.parametrize("membership", ["join", "invite", "knock"])
def test_unexpected_room_audience_prevents_publication(homeserver, tmp_path, membership):
    homeserver.extra_members = [
        {"state_key": "@unexpected:local.test", "content": {"membership": membership}}
    ]
    with pytest.raises(MatrixObservationError, match="UNEXPECTED_MEMBER"):
        publish(homeserver, tmp_path)
    assert homeserver.puts == 0


@pytest.mark.parametrize(
    "fault,code",
    [
        ("guest", "PUBLISHER_IS_GUEST"),
        ("room_guest", "ROOM_GUEST_ACCESS"),
        ("federated", "ROOM_FEDERATED"),
    ],
)
def test_guest_or_federated_room_cannot_receive_observation(homeserver, tmp_path, fault, code):
    setattr(homeserver, fault, True)
    with pytest.raises(MatrixObservationError, match=code):
        publish(homeserver, tmp_path)
    assert homeserver.puts == 0


def test_viewer_write_permission_is_rejected(homeserver, tmp_path):
    homeserver.writable_viewer = True
    with pytest.raises(MatrixObservationError, match="ROOM_PERMISSIONS_INVALID"):
        publish(homeserver, tmp_path)
    assert homeserver.puts == 0


def test_preflight_failure_cannot_overwrite_newer_success(homeserver, tmp_path):
    old = publish(homeserver, tmp_path)
    newer = publish(homeserver, tmp_path, quote_revision=2, quote_ref="quote:one@v2")
    record_observation_failure(
        tmp_path,
        workspace_id="workspace:one",
        run_id="run:one",
        expected_receipt_digest=old["digest"],
        error_code="MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE",
    )
    assert public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one") == newer


def test_preflight_failure_invalidates_its_unchanged_previous_receipt(homeserver, tmp_path):
    old = publish(homeserver, tmp_path)
    record_observation_failure(
        tmp_path,
        workspace_id="workspace:one",
        run_id="run:one",
        expected_receipt_digest=old["digest"],
        error_code="MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE",
    )
    assert (
        public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")["status"]
        == "UNAVAILABLE"
    )


def test_invalid_workspace_url_invalidates_previous_observation(homeserver, tmp_path):
    publish(homeserver, tmp_path)
    with pytest.raises(MatrixObservationError, match="URL_INVALID"):
        publish_observation(
            homeserver.config,
            tmp_path,
            workspace_id="workspace:one",
            run_id="run:one",
            snapshot=snapshot(),
            workspace_url="https://example.test?access_token=secret",
        )
    assert (
        public_observation(tmp_path, workspace_id="workspace:one", run_id="run:one")["status"]
        == "UNAVAILABLE"
    )


def test_workspace_get_does_not_restore_success_after_private_config_failure(
    homeserver, tmp_path, monkeypatch
):
    from orgrebase.workspace import matrix_workspace as bridge

    config = dataclasses.replace(homeserver.config, allowed_workspace_ids=("org:one/default",))
    path = tmp_path / "observer.json"
    path.write_text(json.dumps(dataclasses.asdict(config)))
    path.chmod(0o600)
    monkeypatch.setenv("ORGREBASE_MATRIX_OBSERVER_CONFIG", str(path))
    monkeypatch.setenv("ORGREBASE_PUBLIC_WORKSPACE_URL", "http://127.0.0.1:8014")
    state = {
        "stage": "CURRENT",
        "business_complete": True,
        "quote": {"id": "quote:one", "version": "v3", "digest": "sha256:" + "b" * 64},
        "agentteams_operations": {
            "formation_taskflow": {
                "run_id": "run:one",
                "native_taskflow_observed": True,
                "summary_digest": "sha256:" + "a" * 64,
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
    workspace = SimpleNamespace(
        profile=SimpleNamespace(
            organization_id="org:one", default_task=SimpleNamespace(actor_id="actor:one")
        ),
        store=SimpleNamespace(workspace_id="default"),
        state=lambda: copy.deepcopy(state),
    )
    assert (
        bridge.publish_workspace_observation(workspace, actor_id="actor:one", expected_run_id="run:one")[
            "status"
        ]
        == "OBSERVED"
    )
    path.chmod(0o644)
    with pytest.raises(MatrixObservationError, match="CONFIG_NOT_PRIVATE"):
        bridge.publish_workspace_observation(workspace, actor_id="actor:one", expected_run_id="run:one")
    path.chmod(0o600)
    monkeypatch.setattr(bridge, "load_matrix_config", lambda _: pytest.fail("GET read credentials"))
    assert bridge.observation_view(workspace, state)["status"] == "UNAVAILABLE"
