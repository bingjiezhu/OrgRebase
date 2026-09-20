from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx
import jwt
import pytest
from test_dataverse_qualification import ORGANIZATION_ID, MetadataProtocol
from test_dataverse_target import DataverseProtocolServer

from orgrebase.dataverse_target import DataverseDraftTargetSettings
from orgrebase.workspace.effects import EffectWorkerConfig

pytest_plugins = ("test_browser_sessions",)


class HTTPSDataverse:
    """Controlled protocol fixture; it does not establish real Dataverse behavior."""

    def __init__(self, certificate, private, provider):
        self.protocol = DataverseProtocolServer()
        self.metadata = MetadataProtocol()
        self.requests = []
        self.lock = threading.RLock()
        self.response_loss = False
        self.defer_before_commit = False
        self.pending_batch = None
        self.redirect = False
        self.batch_count = 0
        self.unauthorized = 0
        target = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status, body=b"", headers=None):
                self.send_response(status)
                for key, value in (headers or {}).items():
                    if key.lower() not in {"content-length", "connection", "transfer-encoding"}:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                parsed = urlsplit(self.path)
                target.requests.append((self.command, parsed.path))
                try:
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    claims = jwt.decode(token, provider.key.public_key(), algorithms=["RS256"],
                                        issuer=provider.issuer, audience=target.origin,
                                        options={"require": ["iss", "aud", "exp", "sub"]})
                    if claims["sub"] != "target-worker":
                        raise ValueError("target subject")
                except (jwt.PyJWTError, ValueError):
                    target.unauthorized += 1
                    self.respond(401)
                    return
                if target.redirect:
                    self.respond(302, headers={"Location": target.origin + "/unexpected"})
                    return
                if self.command == "GET" and ("/EntityDefinitions" in parsed.path or parsed.path.endswith("/WhoAmI")):
                    self.respond(200, json.dumps(target.metadata.respond(parsed.path, parsed.query)).encode(),
                                 {"Content-Type": "application/json"})
                    return
                with target.lock:
                    if self.command == "GET" and "/quotes(" in parsed.path:
                        self.respond(200, json.dumps(target.protocol.quote).encode(), {"Content-Type": "application/json"})
                        return
                    if self.command == "GET" and "/orgrebase_effectreceipts(" in parsed.path:
                        identity = parsed.path.rsplit("(", 1)[1][:-1]
                        row = target.protocol.receipts.get(identity)
                        self.respond(200 if row else 404, json.dumps(row).encode() if row else b"",
                                     {"Content-Type": "application/json"})
                        return
                    body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if self.command == "POST" and parsed.path.endswith("/orgrebase_effectreceipts"):
                        row = json.loads(body)
                        identity = row["orgrebase_effectreceiptid"]
                        if identity in target.protocol.receipts:
                            self.respond(409)
                        else:
                            target.protocol.receipts[identity] = row
                            self.respond(204)
                        return
                    if self.command == "POST" and parsed.path.endswith("/$batch"):
                        target.batch_count += 1
                        request = httpx.Request("POST", target.origin + self.path, content=body,
                                                headers={"Content-Type": self.headers["Content-Type"]})
                        if target.defer_before_commit:
                            target.pending_batch = request
                        else:
                            response = target.protocol.batch(request)
                        if target.defer_before_commit or target.response_loss:
                            self.connection.shutdown(socket.SHUT_RDWR)
                            self.connection.close()
                            return
                        self.respond(response.status_code, response.content, dict(response.headers))
                        return
                self.respond(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.origin = f"https://localhost:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def release_pending(self):
        with self.lock:
            assert self.pending_batch is not None
            return self.protocol.batch(self.pending_batch)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        assert not self.thread.is_alive()


def signed_access(provider, subject, *, audience=None, **claims):
    now = int(time.time())
    return jwt.encode({"iss": provider.issuer, "aud": audience or provider.audience, "sub": subject,
                       "iat": now, "exp": now + 600, "token_use": "access", **claims},
                      provider.key, algorithm="RS256", headers={"kid": "oidc-key"})


@pytest.fixture
def effect_https(browser_deployment, tmp_path):
    application, provider, original_client = browser_deployment
    target = HTTPSDataverse(application.certificate, application.private, provider)
    worker_token = signed_access(provider, "worker")
    target_token = signed_access(provider, "target-worker", audience=target.origin)
    config = EffectWorkerConfig(target=DataverseDraftTargetSettings(
        tenant_id=application.settings.identity.tenant_id, instance_url=target.origin,
        quote_id=target.protocol.quote["quoteid"], ca_bundle=str(application.certificate)),
        organization_id=ORGANIZATION_ID, owner_id="human:target-owner", target_token_variable="TEST_DATAVERSE_TARGET_ACCESS",
        access_token_variable="TEST_ORGREBASE_WORKER_ACCESS")
    config_path = tmp_path / "effect-worker.json"
    config_path.write_text(config.model_dump_json())
    membership = json.loads(application.settings.identity.membership_file.read_bytes())
    membership["members"].extend([
        {"subject": "owner", "actor_id": config.owner_id, "roles": ["approver"]},
        {"subject": "executor", "actor_id": "service:executor", "roles": ["executor"]},
        {"subject": "worker", "actor_id": "service:worker", "roles": ["executor"]},
    ])
    application.settings.identity.membership_file.write_text(json.dumps(membership))
    application.settings = replace(application.settings, effect_config=config_path)
    original_client.close()
    application.restart()
    origin = application.settings.browser_session.public_origin
    settings = application.settings
    environment = {
        "PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
        "ORGREBASE_DEPLOYMENT_MODE": "production", "ORGREBASE_WORKSPACE_DB": settings.database_url,
        "ORGREBASE_ENTERPRISE_PACK": settings.enterprise_pack, "ORGREBASE_ALLOWED_HOSTS": "localhost",
        "ORGREBASE_TENANT_ID": settings.identity.tenant_id, "ORGREBASE_AUTH_ISSUER": provider.issuer,
        "ORGREBASE_AUTH_AUDIENCE": provider.audience, "ORGREBASE_AUTH_JWKS_URL": provider.issuer + "/jwks",
        "ORGREBASE_AUTH_CA_BUNDLE": str(application.certificate),
        "ORGREBASE_AUTH_MEMBERSHIP_FILE": str(settings.identity.membership_file),
        "ORGREBASE_EFFECT_CONFIG": str(config_path), "ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED": "0",
        "ORGREBASE_OAC_ADAPTATION_MODE": "optional",
        "TEST_ORGREBASE_WORKER_ACCESS": worker_token, "TEST_DATAVERSE_TARGET_ACCESS": target_token,
    }
    project = Path(__file__).resolve().parents[1]

    def cli(*arguments, extra_env=None, expected_code=0):
        result = subprocess.run([sys.executable, "-W", "error", "-m", "orgrebase", *arguments],
                                cwd=project, env={**environment, **(extra_env or {})},
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == expected_code, result.stdout + result.stderr
        assert worker_token not in result.stdout + result.stderr and target_token not in result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        return json.loads(result.stdout)

    def headers(subject):
        return {"Authorization": "Bearer " + signed_access(provider, subject)}

    try:
        with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(application.certificate)),
                          trust_env=False, timeout=20) as client:
            yield {"application": application, "provider": provider, "target": target, "client": client,
                   "config": config, "config_path": config_path, "cli": cli, "headers": headers,
                   "environment": environment, "tmp_path": tmp_path}
    finally:
        target.close()


def prepare_effect(data, proposal_id="proposal-one"):
    cli, client, headers = data["cli"], data["client"], data["headers"]
    formed = client.post("/api/workspace/form", headers=headers("operator"))
    assert formed.status_code == 200, formed.text
    observed = cli("effect-worker", "--config", str(data["config_path"]), "--observe")
    assert observed["target_writes"] == 0 and data["target"].protocol.writes == 0
    options = client.get("/api/workspace/effect-options", headers=headers("operator")).json()
    assert options["observations"][0]["observation_id"] == observed["observation_id"]
    response = client.post("/api/workspace/effect-proposals", headers=headers("operator"),
                           json={"proposal_id": proposal_id, "observation_id": observed["observation_id"],
                                 "changes": {"description": "Approved effect from the authenticated application"},
                                 "reason": "Owner-approved draft metadata correction"})
    assert response.status_code == 200, response.text
    proposal = response.json()
    rejected = client.post(f"/api/workspace/effect-proposals/{proposal_id}/approve", headers=headers("executor"),
                           json={"proposal_digest": proposal["proposal_digest"]})
    assert rejected.status_code == 403
    approved = client.post(f"/api/workspace/effect-proposals/{proposal_id}/approve", headers=headers("owner"),
                           json={"proposal_digest": proposal["proposal_digest"]})
    assert approved.status_code == 200, approved.text
    effect = approved.json()["effect"]
    queue = client.post(f"/api/workspace/effect-proposals/{proposal_id}/actions", headers=headers("executor"),
                        json={"action": "EXECUTE", "request_digest": effect["request_digest"]})
    assert queue.status_code == 200, queue.text
    assert data["target"].protocol.writes == 0 and data["target"].batch_count == 0
    return effect


def queue_action(data, effect, action, subject="executor"):
    response = data["client"].post("/api/workspace/effect-proposals/proposal-one/actions",
                                   headers=data["headers"](subject),
                                   json={"action": action, "request_digest": effect["request_digest"]})
    assert response.status_code == 200, response.text


def test_real_https_jwt_pg_cli_observe_propose_approve_queue_and_worker(effect_https):
    data = effect_https
    effect = prepare_effect(data)
    result = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert result["commands"][0]["state"] == "CONFIRMED"
    assert data["target"].protocol.writes == 1 and data["target"].batch_count == 1
    assert data["target"].protocol.quote["description"] == effect["request"]["payload"]["changes"]["description"]
    repeated = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert repeated["commands"] == [] and data["target"].batch_count == 1
    state = data["client"].get("/api/workspace/effect-proposals/proposal-one", headers=data["headers"]("owner")).json()
    assert state["state"] == "CONFIRMED"
    assert state["approval"]["identity"]["actor_id"] == "human:target-owner"
    assert state["approval"]["identity"]["subject"] == "owner"


def test_real_https_lost_response_reconciles_in_new_cli_process_without_another_batch(effect_https):
    data = effect_https
    effect = prepare_effect(data)
    data["target"].response_loss = True
    first = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert first["commands"][0]["state"] == "COMMIT_UNKNOWN" and data["target"].protocol.writes == 1
    queue_action(data, effect, "QUERY")
    second = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert second["commands"][0]["state"] == "CONFIRMED"
    assert data["target"].protocol.writes == data["target"].batch_count == 1


def test_real_https_cancel_fences_a_delayed_batch_through_same_worker(effect_https):
    data = effect_https
    effect = prepare_effect(data)
    data["target"].defer_before_commit = True
    first = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert first["commands"][0]["state"] == "COMMIT_UNKNOWN" and data["target"].protocol.writes == 0
    queue_action(data, effect, "QUERY")
    query = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert query["commands"][0]["state"] == "COMMIT_UNKNOWN" and data["target"].batch_count == 1
    queue_action(data, effect, "CANCEL", "owner")
    cancelled = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert cancelled["commands"][0]["state"] == "REJECTED"
    assert data["target"].release_pending().status_code == 409
    assert data["target"].protocol.writes == 0 and data["target"].batch_count == 1


def test_real_https_target_scope_revocation_blocks_dispatch_before_network_write(effect_https):
    data = effect_https
    prepare_effect(data)
    membership_file = data["application"].settings.identity.membership_file
    membership = json.loads(membership_file.read_bytes())
    next(item for item in membership["members"] if item["subject"] == "owner")["roles"] = ["reader"]
    membership_file.write_text(json.dumps(membership))
    result = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert result["commands"][0]["state"] == "READY"
    assert result["commands"][0]["error_code"] == "AUTH_ACTION_DENIED"
    assert data["target"].batch_count == data["target"].protocol.writes == 0


@pytest.mark.parametrize("contradiction", ["elastic", "organization"])
def test_real_https_metadata_contradiction_blocks_first_worker_dispatch(effect_https, contradiction):
    data = effect_https
    prepare_effect(data)
    if contradiction == "elastic":
        data["target"].metadata.entities["orgrebase_effectreceipt"]["entity"]["TableType"] = "Elastic"
    else:
        data["target"].metadata.organization_id = "00000000-0000-0000-0000-000000000986"
    result = data["cli"]("effect-worker", "--config", str(data["config_path"]))
    assert result["commands"][0]["state"] == "READY"
    assert data["target"].batch_count == data["target"].protocol.writes == 0
    assert result["commands"][0]["error_code"] == "TARGET_METADATA_QUALIFICATION_FAILED"


def test_real_https_qualification_cli_is_read_only_and_never_certifies_live_safety(effect_https):
    data = effect_https
    report = data["cli"]("dataverse-target", "probe", "--config", str(data["config_path"]),
                         "--organization-id", ORGANIZATION_ID, "--token-variable", "TEST_DATAVERSE_TARGET_ACCESS")
    assert report["metadata_status"] == "PASS" and report["production_ready"] is False
    assert all(method == "GET" for method, path in data["target"].requests)
    data["target"].metadata.entities["orgrebase_effectreceipt"]["entity"]["TableType"] = "Elastic"
    report = data["cli"]("dataverse-target", "probe", "--config", str(data["config_path"]),
                         "--organization-id", ORGANIZATION_ID, "--token-variable", "TEST_DATAVERSE_TARGET_ACCESS",
                         expected_code=1)
    assert report["metadata_status"] == "FAIL" and data["target"].protocol.writes == 0
