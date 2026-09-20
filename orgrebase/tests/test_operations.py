from __future__ import annotations

import io
import json
import secrets
import sys
from urllib.error import HTTPError

import pytest

from orgrebase.operations import OperationError, workspace_command


@pytest.mark.parametrize(("action", "digest", "expected"), [
    ("preview", None, {}),
    ("approve", "sha256:preview", {"preview_digest": "sha256:preview"}),
    ("apply", "sha256:approval", {"approval_digest": "sha256:approval"}),
    ("reject", None, {"reason": "source needs review"}),
])
@pytest.mark.parametrize("workspace_id", [None, "renewals"])
def test_command_transmits_exact_api_contract_and_verified_identity(monkeypatch, action, digest, expected, workspace_id):
    requests = []

    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"status":"received"}')

    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: Transport())
    token = secrets.token_urlsafe(24)
    monkeypatch.setenv("ORGREBASE_ACCESS_TOKEN", token)
    assert workspace_command("https://api.example", action, event_id="source:123", digest=digest,
                             reason="source needs review" if action == "reject" else None,
                             workspace_id=workspace_id) == {
        "status": "received"
    }
    request = requests[0]
    expected_path = "changes/source%3A123/reject" if action == "reject" else f"{action}/source%3A123"
    assert request.full_url == f"https://api.example/api/workspace/{expected_path}"
    assert request.method == "POST"
    assert json.loads(request.data) == expected
    assert request.get_header("Authorization") == "Bearer " + token
    assert request.get_header("X-orgrebase-workspace") == workspace_id
    assert "actor_id" not in json.loads(request.data)


@pytest.mark.parametrize("url", [
    "http://remote.example", "https://user:password@example.com",
    "https://example.com/other", "https://example.com?redirect=secret",
])
def test_unsafe_api_origin_is_rejected_before_network(monkeypatch, url):
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: pytest.fail("network used"))
    with pytest.raises(OperationError, match="API_HTTPS_OR_LOCALHOST_REQUIRED"):
        workspace_command(url, "state")


def test_api_rejection_does_not_expose_response_secrets(monkeypatch):
    class Transport:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 403, "private token", {}, io.BytesIO(b"private body"))

    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: Transport())
    with pytest.raises(OperationError) as error:
        workspace_command("http://127.0.0.1:8080", "state")
    assert str(error.value) == "API_REQUEST_REJECTED:403"


def test_missing_approval_digest_never_dispatches(monkeypatch):
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: pytest.fail("network used"))
    with pytest.raises(OperationError, match="EXACT_PROPOSAL_DIGEST_REQUIRED"):
        workspace_command("https://api.example", "apply", event_id="one")


@pytest.mark.parametrize("workspace_id", ["", "../default", " renewals", "renewals\n", "a" * 129, "报价", 7])
def test_invalid_workspace_never_dispatches(monkeypatch, workspace_id):
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: pytest.fail("network used"))
    with pytest.raises(OperationError, match="WORKSPACE_ID_INVALID"):
        workspace_command("https://api.example", "state", workspace_id=workspace_id)


@pytest.mark.parametrize("workspace_id", [None, "renewals"])
@pytest.mark.parametrize("action", ["state", "register"])
def test_cli_carries_workspace_scope_to_api(monkeypatch, capsys, tmp_path, workspace_id, action):
    from orgrebase.cli import main

    requests = []

    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"status":"received"}')

    argv = ["orgrebase", "workspace", action, "--url", "https://api.example"]
    if workspace_id is not None:
        argv += ["--workspace", workspace_id]
    if action == "register":
        event = tmp_path / "change.json"
        event.write_text('{"event_id":"event:renewal"}')
        argv += ["--input", str(event)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: Transport())
    main()
    assert json.loads(capsys.readouterr().out) == {"status": "received"}
    assert len(requests) == 1
    request = requests[0]
    assert request.get_header("X-orgrebase-workspace") == workspace_id
    if action == "register":
        assert request.full_url == "https://api.example/api/workspace/changes"
        assert request.method == "POST"
        assert json.loads(request.data) == {"event_id": "event:renewal"}
    else:
        assert request.full_url == "https://api.example/api/workspace/state"
        assert request.method == "GET" and request.data is None
