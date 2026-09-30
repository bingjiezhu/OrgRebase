from __future__ import annotations

import json
import ssl
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from browser_oidc_provider import tls_files

from scripts import run_enterprise_operations_cycle as cycle

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def config(tmp_path: Path, **overrides) -> cycle.CycleConfig:
    data = {
        "schema_version": "orgrebase.enterprise-operations-cycle.v1",
        "origin": "https://enterprise.example",
        "workspace_id": "quote-1",
        "read_token_variable": "OPS_READ_TOKEN",
    }
    data.update(overrides)
    return cycle.CycleConfig.model_validate(data)


def operations(after: int, *, through: int | None = None, next_cursor=None,
               unknown: int = 0, unresolved: int = 0, ready: int = 0,
               source: str = "COMPLETE") -> dict:
    through = after if through is None else through
    return {
        "schema_version": "orgrebase.workspace-operations.v1",
        "checkpoint": {"sequence_no": max(through, 20), "head_digest": "sha256:" + "a" * 64},
        "event_page": {"after": after, "through": through, "next_cursor": next_cursor,
                       "count": max(0, through - after), "limit": 100},
        "effect_counts": {"READY": ready, "DISPATCHING": 0, "COMMIT_UNKNOWN": unknown,
                          "CONFIRMED": 0, "REJECTED": 0},
        "unknown_oldest_observed_age_seconds": 70 if unknown else None,
        "unresolved_changes": unresolved,
        "source": {"status": source},
        "alerts": ([{"code": "EXTERNAL_OUTCOME_UNKNOWN"}] if unknown else []),
    }


def test_observe_page_chain_and_alerts_without_worker_or_purge(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"workspace_id":"quote-1"}')
    settings = config(tmp_path, source_config=str(source), max_event_pages=1,
                      max_unresolved_changes=2, max_ready_effects=1)
    calls = []

    def request(method, path, variable):
        calls.append((method, path, variable))
        after = int(path.split("after=")[1].split("&")[0])
        return operations(after, through=after + 1, next_cursor=after + 1,
                          unknown=1, unresolved=3, ready=2, source="UNKNOWN")

    old = {"schema_version": "orgrebase.enterprise-operations-state.v1", "workspace_id": "quote-1",
           "event_cursor": 0, "observed_at": "2026-09-28T11:00:00Z"}
    receipt, state = cycle.run_cycle(settings, mode="observe", previous=old,
                                     request=request,
                                     invoke=lambda _: pytest.fail("worker invoked"), now=NOW)
    assert calls == [
        ("GET", "/api/workspace/operations?after=0&limit=100", "OPS_READ_TOKEN"),
        ("GET", "/api/workspace/operations?after=1&limit=100", "OPS_READ_TOKEN"),
    ]
    assert receipt["stages"] == [] and receipt["private_records_purged"] == 0
    assert receipt["effect_counts"]["COMMIT_UNKNOWN"] == 1
    assert {"EXTERNAL_OUTCOME_UNKNOWN", "EXTERNAL_OUTCOME_UNKNOWN_AGED",
            "CHANGE_BACKLOG_HIGH", "EFFECT_BACKLOG_HIGH", "SOURCE_COVERAGE_NOT_COMPLETE",
            "OPERATIONS_EVENT_BACKLOG_PARTIAL", "OPERATIONS_PREVIOUS_CYCLE_LATE"} <= set(receipt["alerts"])
    assert state["event_cursor"] == 2
    assert receipt["notification_delivery"] == "NOT_ATTEMPTED_CAPTURE_WITH_EXISTING_MONITORING"


def test_run_uses_existing_worker_commands_and_bounded_private_sweep(tmp_path):
    paths = {}
    for name in ("source", "change", "effect"):
        path = tmp_path / f"{name}.json"
        path.write_text('{"workspace_id":"quote-1"}')
        paths[name] = str(path)
    settings = config(tmp_path, source_config=paths["source"], change_config=paths["change"],
                      effect_config=paths["effect"], effect_mode="observe", purge_private=True,
                      admin_token_variable="OPS_ADMIN_TOKEN", max_purge_batches=2)
    commands = []
    requests = []
    purges = iter((1000, 3))

    def invoke(command):
        commands.append(command)
        if command[0] == "source-sync":
            return {"workspace_id": "quote-1", "status": "SYNCED", "external_writes": 0,
                    "secret": "not-for-receipt"}
        if command[0] == "change-worker":
            return {"workspace_id": "quote-1", "mode": "LOCAL_DETERMINISTIC_CANDIDATE_ONLY", "processed": 1,
                    "target_writes": 0, "records": []}
        return {"workspace_id": "quote-1", "target_writes": 0,
                "observation_digest": "sha256:" + "b" * 64}

    def request(method, path, variable):
        requests.append((method, path, variable))
        if method == "POST":
            return {"deleted": next(purges), "limit": 1000}
        after = int(path.split("after=")[1].split("&")[0])
        return operations(after)

    receipt, _ = cycle.run_cycle(settings, mode="run", request=request,
                                 invoke=invoke, now=NOW)
    assert [item[0] for item in commands] == ["source-sync", "change-worker", "effect-worker"]
    assert commands[2][-1] == "--observe"
    assert [item[2] for item in requests if item[0] == "POST"] == ["OPS_ADMIN_TOKEN"] * 2
    assert receipt["private_records_purged"] == 1003
    assert receipt["alerts"] == []
    assert "not-for-receipt" not in json.dumps(receipt)


def test_target_dispatch_requires_two_explicit_choices(tmp_path):
    effect = tmp_path / "effect.json"
    effect.write_text('{"workspace_id":"quote-1"}')
    settings = config(tmp_path, effect_config=str(effect), effect_mode="dispatch")
    with pytest.raises(cycle.CycleError, match="OPERATIONS_TARGET_DISPATCH_NOT_AUTHORIZED"):
        cycle.run_cycle(settings, mode="run", request=lambda *_: pytest.fail("HTTP used"), now=NOW)
    commands = []

    def invoke(command):
        commands.append(command)
        return {"workspace_id": "quote-1",
                "commands": [{"state": "COMMIT_UNKNOWN", "error_code": None, "pending": True}]}

    receipt, _ = cycle.run_cycle(settings, mode="run", allow_target_dispatch=True,
                                 request=lambda _, path, __: operations(int(path.split("after=")[1].split("&")[0])),
                                 invoke=invoke, now=NOW)
    assert commands == [["effect-worker", "--config", str(effect), "--max-commands", "20"]]
    assert "EFFECT_COMMAND_REQUIRES_RECONCILIATION" in receipt["alerts"]


def test_worker_scope_mismatch_is_rejected_before_api_or_dispatch(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"workspace_id":"another-workspace"}')
    settings = config(tmp_path, source_config=str(source))
    with pytest.raises(cycle.CycleError, match="OPERATIONS_WORKER_CONFIG_WORKSPACE_MISMATCH"):
        cycle.run_cycle(settings, mode="run", request=lambda *_: pytest.fail("HTTP used"),
                        invoke=lambda _: pytest.fail("worker invoked"), now=NOW)


def test_full_purge_budget_is_explicit_backlog_and_response_loss_is_unknown(tmp_path):
    settings = config(tmp_path, admin_token_variable="OPS_ADMIN_TOKEN", purge_private=True,
                      max_purge_batches=2)
    calls = []

    def request(method, path, variable):
        calls.append(method)
        if method == "POST":
            return {"deleted": 1000, "limit": 1000}
        return operations(int(path.split("after=")[1].split("&")[0]))

    receipt, _ = cycle.run_cycle(settings, mode="run", request=request, now=NOW)
    assert calls == ["GET", "POST", "POST", "GET"]
    assert receipt["private_records_purged"] == 2000
    assert receipt["private_purge_backlog_partial"] is True
    assert "PRIVATE_PURGE_BACKLOG_PARTIAL" in receipt["alerts"]

    def lost(method, path, variable):
        if method == "POST":
            raise cycle.CycleError("OPERATIONS_REQUEST_UNAVAILABLE")
        return operations(int(path.split("after=")[1].split("&")[0]))

    with pytest.raises(cycle.CycleError, match="OPERATIONS_PRIVATE_PURGE_OUTCOME_UNKNOWN"):
        cycle.run_cycle(settings, mode="run", request=lost, now=NOW)


def test_invalid_event_cursor_never_advances_state(tmp_path):
    settings = config(tmp_path)

    def regress(_, path, __):
        after = int(path.split("after=")[1].split("&")[0])
        return operations(after, through=after + 1, next_cursor=after)

    with pytest.raises(cycle.CycleError, match="OPERATIONS_PAGE_INVALID"):
        cycle.run_cycle(settings, mode="observe", request=regress, now=NOW)


def test_current_token_is_refetched_and_http_denial_is_never_logged(tmp_path, monkeypatch):
    settings = config(tmp_path)
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer old-secret":
            return httpx.Response(401, json={"detail": "old-secret"})
        return httpx.Response(200, json=operations(0))

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=10,
                      trust_env=False, follow_redirects=False) as client:
        monkeypatch.setenv("OPS_READ_TOKEN", "old-secret")
        with pytest.raises(cycle.CycleError, match="OPERATIONS_HTTP_401") as failure:
            cycle._http_json(client, settings, "GET", "/api/workspace/operations?after=0&limit=100",
                             settings.read_token_variable)
        assert "old-secret" not in str(failure.value)
        monkeypatch.setenv("OPS_READ_TOKEN", "new-secret")
        assert cycle._http_json(client, settings, "GET",
                                "/api/workspace/operations?after=0&limit=100",
                                settings.read_token_variable)["schema_version"] == "orgrebase.workspace-operations.v1"
    assert seen == ["Bearer old-secret", "Bearer new-secret"]


def test_private_config_rejects_http_origin_symlink_and_duplicate_keys(tmp_path):
    path = tmp_path / "ops.json"
    payload = {"schema_version": "orgrebase.enterprise-operations-cycle.v1",
               "origin": "http://enterprise.example", "workspace_id": "quote-1",
               "read_token_variable": "OPS_READ_TOKEN"}
    path.write_text(json.dumps(payload))
    with pytest.raises(cycle.CycleError, match="OPERATIONS_CONFIG_INVALID"):
        cycle.load_config(path)
    payload["origin"] = "https://enterprise.example"
    path.write_text(json.dumps(payload))
    assert cycle.load_config(path).workspace_id == "quote-1"
    path.write_text('{"schema_version":"orgrebase.enterprise-operations-cycle.v1",'
                    '"origin":"https://enterprise.example","origin":"https://evil.example",'
                    '"workspace_id":"quote-1","read_token_variable":"OPS_READ_TOKEN"}')
    with pytest.raises(cycle.CycleError, match="OPERATIONS_JSON_DUPLICATE_KEY"):
        cycle.load_config(path)
    symlink = tmp_path / "config-link.json"
    symlink.symlink_to(path)
    with pytest.raises(cycle.CycleError, match="OPERATIONS_FILE_INVALID"):
        cycle.load_config(symlink)


def test_state_is_atomic_and_does_not_persist_secret(tmp_path):
    state_file = tmp_path / "state.json"
    state = {"schema_version": "orgrebase.enterprise-operations-state.v1", "workspace_id": "quote-1",
             "event_cursor": 8, "observed_at": "2026-09-28T12:00:00Z"}
    cycle._write_state(state_file, state)
    assert cycle._read_private_json(state_file, limit=4096) == state
    assert state_file.stat().st_mode & 0o077 == 0
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_observe_publishes_machine_receipt_and_exit_three_without_secret(tmp_path, monkeypatch, capsys):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "schema_version": "orgrebase.enterprise-operations-cycle.v1",
        "origin": "https://enterprise.example", "workspace_id": "quote-1",
        "read_token_variable": "OPS_READ_TOKEN",
    }))
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("OPS_READ_TOKEN", "top-secret-value")
    real_client = httpx.Client
    client = real_client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json=operations(int(request.url.params["after"]), unknown=1)
    )), timeout=10, trust_env=False, follow_redirects=False)
    monkeypatch.setattr(cycle.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(sys, "argv", ["ops-cycle", "--config", str(config_file),
                                      "--state-file", str(state_file), "--mode", "observe"])
    with pytest.raises(SystemExit) as result:
        cycle.main()
    assert result.value.code == 3
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert "EXTERNAL_OUTCOME_UNKNOWN" in receipt["alerts"]
    assert receipt["notification_delivery"] == "NOT_ATTEMPTED_CAPTURE_WITH_EXISTING_MONITORING"
    assert "top-secret-value" not in captured.out + captured.err + state_file.read_text()
    assert cycle._read_private_json(state_file, limit=4096)["event_cursor"] == 0


@pytest.fixture
def operations_https(tmp_path):
    servers = []

    def start(*, wrong_hostname=False):
        root = tmp_path / f"https-{len(servers)}"
        root.mkdir()
        certificate, private = tls_files(root)
        if wrong_hostname:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization

            original = x509.load_pem_x509_certificate(certificate.read_bytes())
            key = serialization.load_pem_private_key(private.read_bytes(), password=None)
            builder = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.issuer)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(original.not_valid_before_utc).not_valid_after(original.not_valid_after_utc))
            for extension in original.extensions:
                value = (x509.SubjectAlternativeName([x509.DNSName("wrong-host.invalid")])
                         if isinstance(extension.value, x509.SubjectAlternativeName) else extension.value)
                builder = builder.add_extension(value, critical=extension.critical)
            certificate.write_bytes(builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
        observed = {"reads": 0, "writes": 0}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                observed["reads"] += 1
                query = parse_qs(urlsplit(self.path).query)
                after = int(query["after"][0])
                raw = json.dumps(operations(after, through=max(after, 1))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                observed["writes"] += 1
                self.send_error(405)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"https://localhost:{server.server_port}", certificate, observed

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def test_cli_observe_accepts_explicit_private_ca_with_certificate_and_hostname_verification(
    tmp_path, operations_https, monkeypatch, capsys,
):
    origin, certificate, observed = operations_https()
    settings = config(tmp_path, origin=origin, ca_bundle=str(certificate))
    context = cycle._tls_verify(settings)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    config_file = tmp_path / "ops-config.json"
    config_file.write_text(json.dumps(settings.model_dump()))
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("OPS_READ_TOKEN", "local-ops-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(cycle, "_invoke_worker", lambda _: pytest.fail("observe invoked a worker"))
    monkeypatch.setattr(sys, "argv", ["ops-cycle", "--config", str(config_file),
                                      "--state-file", str(state_file), "--mode", "observe"])
    cycle.main()
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert not captured.err
    assert receipt["stages"] == [] and receipt["private_records_purged"] == 0
    assert observed == {"reads": 2, "writes": 0}
    assert cycle._read_private_json(state_file, limit=4096)["event_cursor"] == 1
    assert "local-ops-secret" not in captured.out + state_file.read_text()
    assert str(certificate) not in captured.out


@pytest.mark.parametrize("problem, expected", [
    ("default-trust", "OPERATIONS_REQUEST_UNAVAILABLE"),
    ("wrong-ca", "OPERATIONS_REQUEST_UNAVAILABLE"),
    ("wrong-hostname", "OPERATIONS_REQUEST_UNAVAILABLE"),
    ("missing-file", "OPERATIONS_CA_BUNDLE_UNAVAILABLE"),
    ("symlink", "OPERATIONS_CA_BUNDLE_UNAVAILABLE"),
    ("invalid-pem", "OPERATIONS_CA_BUNDLE_UNAVAILABLE"),
    ("directory", "OPERATIONS_CA_BUNDLE_UNAVAILABLE"),
    ("unsafe-writable", "OPERATIONS_CA_BUNDLE_UNAVAILABLE"),
    ("relative-path", "OPERATIONS_CONFIG_INVALID"),
    ("disabled-verification", "OPERATIONS_CONFIG_INVALID"),
])
def test_cli_tls_rejections_preserve_existing_cursor_and_never_issue_business_requests(
    tmp_path, operations_https, monkeypatch, capsys, problem, expected,
):
    origin, certificate, observed = operations_https(wrong_hostname=problem == "wrong-hostname")
    payload = config(tmp_path, origin=origin).model_dump()
    payload["ca_bundle"] = str(certificate)
    if problem == "default-trust":
        payload.pop("ca_bundle")
    elif problem == "wrong-ca":
        other = tmp_path / "other-ca"
        other.mkdir()
        payload["ca_bundle"] = str(tls_files(other)[0])
    elif problem == "missing-file":
        payload["ca_bundle"] = str(tmp_path / "missing-ca.pem")
    elif problem == "symlink":
        link = tmp_path / "ca-link.pem"
        link.symlink_to(certificate)
        payload["ca_bundle"] = str(link)
    elif problem == "invalid-pem":
        certificate.write_text("not a PEM certificate")
    elif problem == "directory":
        payload["ca_bundle"] = str(tmp_path)
    elif problem == "unsafe-writable":
        certificate.chmod(0o666)
    elif problem == "relative-path":
        payload["ca_bundle"] = "ca.pem"
    elif problem == "disabled-verification":
        payload["ca_bundle"] = False
    config_file = tmp_path / "ops-config.json"
    config_file.write_text(json.dumps(payload))
    state_file = tmp_path / "state.json"
    previous = {"schema_version": "orgrebase.enterprise-operations-state.v1", "workspace_id": "quote-1",
                "event_cursor": 7, "observed_at": "2026-09-28T12:00:00Z"}
    cycle._write_state(state_file, previous)
    original_state = state_file.read_bytes()
    monkeypatch.setenv("OPS_READ_TOKEN", "local-ops-secret")
    # Ambient trust must not override the explicit server-owned configuration.
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setattr(cycle, "_invoke_worker", lambda _: pytest.fail("TLS rejection invoked a worker"))
    monkeypatch.setattr(sys, "argv", ["ops-cycle", "--config", str(config_file),
                                      "--state-file", str(state_file), "--mode", "observe"])
    with pytest.raises(SystemExit) as rejected:
        cycle.main()
    assert rejected.value.code == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error_code"] == expected
    assert observed == {"reads": 0, "writes": 0}
    assert state_file.read_bytes() == original_state
    assert "local-ops-secret" not in captured.err and str(tmp_path) not in captured.err
