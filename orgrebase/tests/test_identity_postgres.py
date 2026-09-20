from __future__ import annotations

import io
import json
import time
from urllib.error import HTTPError

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import IdentitySettings, VerifiedJWKClient
from orgrebase.clock import SystemClock
from orgrebase.domain import VersionedObject
from orgrebase.operations import workspace_command
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

pytest_plugins = ("test_postgres_store",)


def test_production_api_real_postgres_signed_identity_and_restart(postgres_runtime, tmp_path, monkeypatch):
    pack = make_enterprise_pack(tmp_path)
    runtime = load_enterprise_quote_pilot_pack(pack)
    profile = runtime.profile
    postgres_dsn = postgres_runtime(tenant_id=profile.organization_id)["runtime_dsn"]
    owner = next(item.owner_id for item in runtime.enterprise_binding.resources if item.slot_id == "launch_date")
    membership = tmp_path / "membership.json"
    membership.write_text(json.dumps({
        "tenant_id": profile.organization_id,
        "members": [
            {"subject": "operator", "actor_id": profile.default_task.actor_id, "roles": ["administrator"]},
            {"subject": "approver", "actor_id": owner, "roles": ["approver"]},
            {"subject": "other-operator", "actor_id": "human:other-operator", "roles": ["administrator"]},
        ],
    }))
    identity = IdentitySettings("https://issuer.example", "orgrebase-api", "https://issuer.example/jwks", profile.organization_id, membership)
    settings = DeploymentSettings(mode="production", identity=identity, database_url=postgres_dsn,
                                  enterprise_pack=str(pack), allowed_hosts=("localhost",))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key-1", alg="RS256", use="sig")
    monkeypatch.setattr(VerifiedJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    monkeypatch.setenv("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "0")
    def headers(subject):
        now = int(time.time())
        token = jwt.encode({"iss": identity.issuer, "aud": identity.audience, "sub": subject,
                            "iat": now, "exp": now + 300, "token_use": "access"},
                           key, algorithm="RS256", headers={"kid": "key-1"})
        return {"Authorization": "Bearer " + token}
    with TestClient(create_app(deployment_settings=settings), base_url="http://localhost") as client:
        class APITransport:
            def open(self, request, timeout):
                response = client.request(request.method, request.full_url, headers=dict(request.header_items()),
                                          content=request.data)
                if response.status_code >= 400:
                    raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO())
                return io.BytesIO(response.content)

        monkeypatch.setattr("orgrebase.operations.build_opener", lambda *args: APITransport())

        def command(subject, action, **kwargs):
            monkeypatch.setenv("ORGREBASE_ACCESS_TOKEN", headers(subject)["Authorization"][7:])
            return workspace_command("http://localhost", action, **kwargs)

        assert client.get("/api/workspace/state").status_code == 401
        assert client.post("/api/workspace/form", headers=headers("other-operator")).status_code == 403
        formed = client.post("/api/workspace/form", headers=headers("operator"))
        assert formed.status_code == 200, formed.text
        assert client.get("/api/workspace/changes", headers=headers("operator")).json()["items"] == []
        base = next(item for item in runtime.seed_objects if item.id == "claim:product.launch_date")
        proposal = VersionedObject.model_validate({
            **base.model_dump(mode="json"), "version": "v99", "state": "PROPOSED", "digest": "",
            "payload": {**base.payload, "canonical_value": "2030-01-01"},
            "source_refs": ["test:observed-source-event:1"], "valid_from": SystemClock().now(),
        })
        event = ChangeEvent(event_id="observed-1", organization_id=profile.organization_id,
                            slot_id="launch_date", owner_id=owner, base_version=base.version,
                            base_digest=base.digest, proposal=proposal, occurred_at=SystemClock().now())
        registered = command("operator", "register", payload=event.model_dump(mode="json"))
        assert registered["event_id"] == event.event_id
        preview = command("operator", "preview", event_id=event.event_id)
        time.sleep(4.05)
        approved = client.post("/api/workspace/approve/observed-1", headers=headers("approver"),
                               json={"actor_id": "attacker", "preview_digest": preview["preview_digest"]})
        assert approved.status_code == 200, approved.text
        assert approved.json()["approval"]["actor_id"] == owner
        assert command("approver", "approve", event_id=event.event_id,
                       digest=preview["preview_digest"])["approval_digest"] == approved.json()["approval_digest"]
        command("operator", "apply", event_id=event.event_id, digest=approved.json()["approval_digest"])
        archive = client.get("/api/workspace/run-archive", headers=headers("operator"))
        assert archive.status_code == 200, archive.text
        assert archive.json()["status"] == "ARCHIVED", archive.text
        assert archive.json()["schema_version"] == "orgrebase.workspace-current-run-archive-view.v2"
        assert archive.json()["record"]["selective_rebase_receipts"][0]["kind"] == "observed-1"
        revised = VersionedObject.model_validate({
            **proposal.model_dump(mode="json"), "digest": "", "version": "v100",
            "payload": {**proposal.payload, "canonical_value": "2031-01-01"},
        })
        rejected_event = ChangeEvent(
            event_id="observed-reject", organization_id=profile.organization_id, slot_id="launch_date",
            owner_id=owner, base_version=proposal.version, base_digest=proposal.digest,
            proposal=revised, occurred_at=SystemClock().now(),
        )
        command("operator", "register", payload=rejected_event.model_dump(mode="json"))
        rejection = command("approver", "reject", event_id=rejected_event.event_id, reason="Confirm source with owner")
        assert rejection["rejection"]["actor_id"] == owner
        assert rejection["rejection"]["status"] == "REJECTED"
        assert client.get("/readyz").json() == {"status": "ready", "service": "orgrebase", "version": "0.4.0"}
        before = client.get("/api/workspace/state", headers=headers("operator")).json()
    with TestClient(create_app(deployment_settings=settings), base_url="http://localhost") as client:
        after = client.get("/api/workspace/state", headers=headers("operator"))
        assert after.status_code == 200, after.text
        assert after.json()["stage"] == before["stage"]
        assert after.json()["event_chain"] == before["event_chain"]
