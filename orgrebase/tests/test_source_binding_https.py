from __future__ import annotations

import json
import os
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
from test_effect_worker_https import signed_access
from test_source_worker import ORGANIZATION, RECORD, source_metadata

from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

pytest_plugins = ("test_browser_sessions",)


class HTTPSSource:
    def __init__(self, certificate, private, provider):
        self.value = "2031-01-01"
        self.version = 'W/"opaque-initial"'
        self.denied_record = False
        self.requests = []
        source = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, code, value):
                body = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                source.requests.append(self.path)
                try:
                    claims = jwt.decode(self.headers.get("Authorization", "").removeprefix("Bearer "),
                        provider.key.public_key(), algorithms=["RS256"], audience=source.origin,
                        issuer=provider.issuer, options={"require": ["sub", "exp", "iss", "aud"]})
                    if claims["sub"] != "source-reader":
                        raise ValueError("subject")
                except (jwt.PyJWTError, ValueError):
                    self.respond(401, {})
                    return
                path = urlsplit(self.path).path
                metadata = source_metadata(self.path)
                if metadata is not None:
                    self.respond(200, metadata)
                    return
                row = {"quoteid": RECORD, "@odata.etag": source.version, "new_launch_date": source.value}
                if path.endswith("/quotes"):
                    self.respond(200, {"@odata.deltaLink": source.origin + "/api/data/v9.2/quotes?$deltatoken=opaque-watermark",
                                       "value": [row] if "$deltatoken" not in self.path else []})
                elif path.endswith(f"/quotes({RECORD})"):
                    self.respond(404 if source.denied_record else 200, {} if source.denied_record else row)
                else:
                    self.respond(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.origin = f"https://localhost:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        if not self.thread.is_alive():
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        assert not self.thread.is_alive()


@pytest.fixture
def source_https(browser_deployment, tmp_path, request):
    application, provider, original = browser_deployment
    source = HTTPSSource(application.certificate, application.private, provider)
    request.addfinalizer(source.close)
    runtime = load_enterprise_quote_pilot_pack(application.settings.enterprise_pack)
    owner = next(item.owner_id for item in runtime.enterprise_binding.resources if item.slot_id == "launch_date")
    membership = json.loads(application.settings.identity.membership_file.read_bytes())
    membership["members"].extend([
        {"subject": "source-owner", "actor_id": owner, "roles": ["approver"]},
        {"subject": "source-worker", "actor_id": "service:source-worker", "roles": ["operator"]},
    ])
    application.settings.identity.membership_file.write_text(json.dumps(membership))
    config = {"source": {"connector_id": "source:https", "tenant_id": application.settings.identity.tenant_id,
        "instance_url": source.origin, "record_ids": [RECORD], "entity_set": "quotes",
        "ca_bundle": str(application.certificate)}, "organization_id": ORGANIZATION,
        "source_token_variable": "TEST_SOURCE_READ_TOKEN", "access_token_variable": "TEST_SOURCE_WORKER_TOKEN"}
    path = tmp_path / "source-config.json"
    path.write_text(json.dumps(config))
    application.settings = replace(application.settings, source_config=path)
    original.close()
    application.restart()
    settings = application.settings
    source_token = signed_access(provider, "source-reader", audience=source.origin)
    worker_token = signed_access(provider, "source-worker")
    environment = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONNOUSERSITE": "1",
        "ORGREBASE_DEPLOYMENT_MODE": "production", "ORGREBASE_WORKSPACE_DB": settings.database_url,
        "ORGREBASE_ENTERPRISE_PACK": settings.enterprise_pack, "ORGREBASE_ALLOWED_HOSTS": "localhost",
        "ORGREBASE_TENANT_ID": settings.identity.tenant_id, "ORGREBASE_AUTH_ISSUER": provider.issuer,
        "ORGREBASE_AUTH_AUDIENCE": provider.audience, "ORGREBASE_AUTH_JWKS_URL": provider.issuer + "/jwks",
        "ORGREBASE_AUTH_CA_BUNDLE": str(application.certificate),
        "ORGREBASE_AUTH_MEMBERSHIP_FILE": str(settings.identity.membership_file),
        "ORGREBASE_SOURCE_CONFIG": str(path), "ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED": "0",
        "ORGREBASE_OAC_ADAPTATION_MODE": "optional", "TEST_SOURCE_READ_TOKEN": source_token,
        "TEST_SOURCE_WORKER_TOKEN": worker_token, "HTTPS_PROXY": "http://127.0.0.1:1"}

    def cli(*arguments, expected_code=0):
        result = subprocess.run([sys.executable, "-W", "error", "-m", "orgrebase", "source-sync", "--config", str(path), *arguments],
            cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=45)
        assert result.returncode == expected_code, result.stdout + result.stderr
        assert source_token not in result.stdout + result.stderr and worker_token not in result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        return json.loads(result.stdout) if expected_code == 0 else result.stderr

    def headers(subject="operator"):
        return {"Authorization": "Bearer " + signed_access(provider, subject)}

    try:
        with httpx.Client(base_url=settings.browser_session.public_origin, trust_env=False,
            verify=ssl.create_default_context(cafile=str(application.certificate)), timeout=20) as client:
            assert client.post("/api/workspace/form", headers=headers()).status_code == 200
            discovery = cli("--discover")
            assert discovery["status"] == "MAPPING_REQUIRED"
            assert discovery["candidates"][0]["owner_id"] == owner
            assert "SOURCE_MAPPING_CONFIRMATION_REQUIRED" in cli(expected_code=2)
            # The failed attempt invalidated freshness; a fresh read is required
            # before anyone can approve a mapping.
            discovery = cli("--discover")
            proposal = client.post("/api/workspace/source-binding/proposals", headers=headers(), json={
                "inventory_digest": discovery["inventory_digest"], "generation_digest": discovery["generation_digest"],
                "mappings": [{"record_id": RECORD, "field": "new_launch_date", "slot_id": "launch_date"}]})
            assert proposal.status_code == 200, proposal.text
            digest = proposal.json()["proposal"]["proposal_digest"]
            confirm = client.post("/api/workspace/source-binding/proposals/" + digest[7:] + "/confirm",
                headers=headers("source-owner"), json={"proposal_digest": digest})
            assert confirm.status_code == 200 and confirm.json()["status"] == "CONFIRMED", confirm.text
            yield {"cli": cli, "client": client, "source": source, "headers": headers,
                   "application": application, "config": config, "path": path, "owner": owner}
    finally:
        source.close()


def test_real_https_cli_discovery_owner_confirmation_sync_and_same_rebase(source_https):
    data = source_https
    first = data["cli"]()
    assert first["coverage"]["status"] == "COMPLETE"
    assert first["coverage"]["readback"]["method"] == "CURRENT_POINT_READ"
    assert any(f"/quotes({RECORD})" in path for path in data["source"].requests)
    history = data["client"].get("/api/workspace/changes", headers=data["headers"]()).json()
    assert len(history["items"]) == 1
    event = history["items"][0]["event"]
    preview = data["client"].post("/api/workspace/preview/" + event["event_id"], headers=data["headers"]())
    assert preview.status_code == 200, preview.text
    time.sleep(4.05)
    approved = data["client"].post("/api/workspace/approve/" + event["event_id"], headers=data["headers"]("source-owner"),
                                  json={"preview_digest": preview.json()["preview_digest"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["actor_id"] == data["owner"]
    applied = data["client"].post("/api/workspace/apply/" + event["event_id"], headers=data["headers"](),
                                 json={"approval_digest": approved.json()["approval_digest"]})
    assert applied.status_code == 200, applied.text
    assert data["cli"]()["coverage"]["status"] == "COMPLETE"
    assert len(data["client"].get("/api/workspace/changes", headers=data["headers"]()).json()["items"]) == 1


def test_real_https_point_read_acl_loss_is_unknown_without_missing_record_claim(source_https):
    data = source_https
    previous = data["cli"]()["coverage"]
    data["source"].denied_record = True
    assert "SOURCE_ENDPOINT_UNAVAILABLE" in data["cli"](expected_code=2)
    view = data["client"].get("/api/workspace/source-binding", headers=data["headers"]()).json()
    assert view["coverage"]["status"] == "UNKNOWN"
    assert view["coverage"]["coverage_digest"] != previous["coverage_digest"]


def test_real_https_explicit_null_is_current_field_evidence_and_not_row_absence(source_https):
    data = source_https
    data["cli"]()
    data["source"].value = None
    data["source"].version = 'W/"opaque-null"'
    current = data["cli"]()["coverage"]
    from orgrebase.digest import sha256_digest
    assert current["status"] == "COMPLETE"
    assert current["records"][RECORD]["deleted"] is False
    assert current["records"][RECORD]["field_digests"]["new_launch_date"] == sha256_digest(None)
