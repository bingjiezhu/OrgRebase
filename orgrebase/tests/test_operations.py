from __future__ import annotations

import io
import json
import secrets
import sys
from urllib.error import HTTPError
from urllib.parse import quote

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


@pytest.mark.parametrize(("action", "record_ref", "after", "expected_path"), [
    ("experience-cases", None, "experience-case:cursor@v2", "/api/workspace/experience-cases?limit=7&after=experience-case%3Acursor%40v2"),
    ("experience-lessons", None, None, "/api/workspace/experience-lessons?limit=7"),
    ("experience-candidates", None, None, "/api/workspace/experience-lessons/candidates?limit=7"),
    ("experience-case", "experience-case:one@v2", None,
     "/api/workspace/experience-cases/" + quote("experience-case:one@v2", safe="")),
    ("experience-lesson", "finance-source-check", None,
     "/api/workspace/experience-lessons/heads/finance-source-check"),
])
def test_experience_read_cli_uses_same_authenticated_workspace_transport(
    monkeypatch, action, record_ref, after, expected_path,
):
    requests = []

    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"schema_version":"read-only"}')

    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: Transport())
    monkeypatch.setenv("ORGREBASE_ACCESS_TOKEN", "test")
    assert workspace_command(
        "https://api.example", action, record_ref=record_ref,
        after=after, limit=7, workspace_id="renewals",
    ) == {"schema_version": "read-only"}
    assert len(requests) == 1
    sent = requests[0]
    assert sent.full_url == "https://api.example" + expected_path
    assert sent.method == "GET" and sent.data is None
    assert sent.get_header("Authorization") == "Bearer test"
    assert sent.get_header("X-orgrebase-workspace") == "renewals"


@pytest.mark.parametrize(("action", "kwargs", "error"), [
    ("experience-case", {}, "EXPERIENCE_CASE_REF_REQUIRED"),
    ("experience-lesson", {"record_ref": "../other"}, "EXPERIENCE_LESSON_ID_REQUIRED"),
    ("experience-cases", {"limit": 0}, "EXPERIENCE_PAGE_LIMIT_INVALID"),
    ("experience-lessons", {"after": ""}, "EXPERIENCE_PAGE_CURSOR_INVALID"),
    ("experience-cases", {"payload": {}}, "EXPERIENCE_READ_ARGUMENT_INVALID"),
])
def test_experience_read_cli_rejects_invalid_input_before_network(monkeypatch, action, kwargs, error):
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: pytest.fail("network used"))
    with pytest.raises(OperationError, match=error):
        workspace_command("https://api.example", action, **kwargs)


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


def test_cli_experience_detail_reads_exact_case_without_private_local_files(monkeypatch, capsys):
    from orgrebase.cli import main

    requests = []

    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"schema_version":"orgrebase.experience-case-detail.v1"}')

    monkeypatch.setattr(sys, "argv", [
        "orgrebase", "workspace", "experience-case",
        "--url", "https://api.example", "--workspace", "renewals",
        "--ref", "experience-case:one@v2",
    ])
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: Transport())
    main()
    assert json.loads(capsys.readouterr().out)["schema_version"] == "orgrebase.experience-case-detail.v1"
    assert requests[0].full_url == (
        "https://api.example/api/workspace/experience-cases/experience-case%3Aone%40v2"
    )
    assert requests[0].get_header("X-orgrebase-workspace") == "renewals"


def test_cli_experience_read_rejects_input_file_before_opening_or_network(monkeypatch, tmp_path, capsys):
    from orgrebase.cli import main

    payload = tmp_path / "private.json"
    payload.write_text('{"raw_secret":"DO_NOT_READ"}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "orgrebase", "workspace", "experience-cases", "--input", str(payload),
    ])
    monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: pytest.fail("network used"))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "EXPERIENCE_READ_INPUT_FORBIDDEN" in capsys.readouterr().err
