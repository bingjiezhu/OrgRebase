from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import FreshnessError
from orgrebase.service import OrgRebaseService

ROOT = Path(__file__).resolve().parents[1]


def test_console_and_health_are_served() -> None:
    service = OrgRebaseService()
    with TestClient(create_app(service)) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        response = client.get("/")
        assert response.status_code == 200
        assert "OrgRebase" in response.text
        assert client.get("/assets/app.js").status_code == 200
        facts = client.get("/api/release-facts")
        assert facts.status_code == 200
        assert (
            facts.json()["core_change_advisory_live_agentteams"]["evidence_class"]
            == "LIVE_AGENTTEAMS"
        )
        assert facts.json()["workspace"]["agentteams_live"] == "NOT_RUN"
    service.store.close()


def test_demo_api_runs_end_to_end_and_resets() -> None:
    service = OrgRebaseService()
    with TestClient(create_app(service)) as client:
        preview = client.post("/api/demo/preview").json()
        assert preview["preview"]["counts"]["affected_hard"] == 2
        applied = client.post("/api/demo/apply").json()
        assert applied["receipt"]["status"] == "COMPLETED"
        assert client.get("/api/demo/state").json()["claim:product.launch_date"]["version"] == "v8"
        reset = client.post("/api/demo/reset").json()
        assert reset["claim:product.launch_date"]["version"] == "v7"
        full = client.post("/api/demo/run").json()
        assert full["event_chain"]["status"] == "PASS"
        assert full["evidence_boundaries"]["agentteams_multi_worker_e2e"] == "NOT_RUN"
    service.store.close()


def test_receipt_verification_api_rejects_tamper() -> None:
    service = OrgRebaseService()
    receipt = service.apply()["receipt"].model_dump(mode="json")
    with TestClient(create_app(service)) as client:
        assert client.post("/api/receipts/verify", json=receipt).json()["status"] == "PASS"
        receipt["status"] = "FABRICATED"
        response = client.post("/api/receipts/verify", json=receipt)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "EVIDENCE_INTEGRITY_FAILED"
    service.store.close()


def test_impact_certificate_api_independently_recomputes_claim() -> None:
    service = OrgRebaseService()
    certificate = service.preview()["preview"].certificates[0].model_dump(mode="json")
    with TestClient(create_app(service)) as client:
        verified = client.post("/api/impact-certificates/verify", json=certificate)
        assert verified.status_code == 200
        assert verified.json()["status"] == "PASS"
        certificate["claim_boundary"] = "Everything is safe."
        rejected = client.post("/api/impact-certificates/verify", json=certificate)
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "EVIDENCE_INTEGRITY_FAILED"
    service.store.close()


def test_minimal_rebase_certificate_api_is_executable() -> None:
    service = OrgRebaseService()
    certificate = service.preview()["minimal_rebase_certificate"].model_dump(mode="json")
    with TestClient(create_app(service)) as client:
        verified = client.post(
            "/api/minimal-rebase-certificates/verify", json=certificate
        )
        assert verified.status_code == 200
        assert "no_missing_rebuild" in verified.json()["checked"]
        certificate["effects"][0]["disposition"] = "PRESERVE_WITHIN_BOUNDARY"
        certificate["digest"] = ""
        rejected = client.post(
            "/api/minimal-rebase-certificates/verify", json=certificate
        )
        assert rejected.status_code == 422
        assert "MINIMALITY_MISSING_REBUILD" in rejected.json()["detail"]["message"]
    service.store.close()


def test_auxiliary_demo_and_tool_error_surfaces() -> None:
    service = OrgRebaseService()
    with TestClient(create_app(service)) as client:
        assert client.post("/api/demo/conflict").status_code == 200
        assert client.post("/api/demo/failure").status_code == 200
        assert client.post("/api/demo/rollback").status_code == 200
        assert client.get("/api/demo/observability").status_code == 200
        assert client.get("/api/demo/no-semantic-delta").status_code == 200
        denied = client.post(
            "/api/tools/v1/dependency-evidence",
            headers={"X-OrgRebase-Actor": "attacker"},
            json={"target_ids": ["work:sales_quote_a"]},
        )
        assert denied.status_code == 403
        invalid = client.post(
            "/api/tools/v1/dependency-evidence",
            headers={"X-OrgRebase-Actor": "gtm-steward"},
            json={
                "target_ids": [],
                "graph_revision": service.fixture.revisions["graph"],
                "idempotency_key": "invalid",
            },
        )
        assert invalid.status_code == 409
        assert client.post("/api/receipts/rollback/verify", json={}).status_code == 422
    service.store.close()


def test_apply_and_rollback_api_translate_freshness_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OrgRebaseService()

    def expired() -> dict[str, object]:
        raise FreshnessError("injected drift")

    monkeypatch.setattr(service, "apply", expired)
    monkeypatch.setattr(service, "rollback", expired)
    with TestClient(create_app(service)) as client:
        assert client.post("/api/demo/apply").status_code == 409
        assert client.post("/api/demo/rollback").status_code == 409
    service.store.close()


def test_agentteams_manifest_has_five_workers_and_one_leader() -> None:
    documents = tuple(yaml.safe_load_all((ROOT / "agentteams" / "team.yaml").read_text()))
    workers = [item for item in documents if item["kind"] == "Worker"]
    team = next(item for item in documents if item["kind"] == "Team")
    assert len(workers) == 5
    assert {item["apiVersion"] for item in documents} == {"agentteams.io/v1beta1"}
    assert sum(member["role"] == "team_leader" for member in team["spec"]["workerMembers"]) == 1
    assert {worker["spec"]["model"] for worker in workers} == {
        "google/gemini-3.1-flash-lite"
    }


def test_skill_contract_digest_is_declared_and_side_effect_free() -> None:
    contract = json.loads((ROOT / "skills/enterprise-launch-readiness/contract.json").read_text())
    assert contract["content_digest"].startswith("sha256:")
    assert contract["distribution"]["license"] == "PolyForm-Noncommercial-1.0.0"
    assert contract["permissions"]["side_effects"] == []
    assert contract["release"]["canary_policy"]["organization"] == "org:northstar"
