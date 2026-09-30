from __future__ import annotations

import time

from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import Principal, request_action, request_principal
from orgrebase.workspace.experience_operations import list_experience_cases
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_experience_collection import _principal as collector_principal
from tests.workspace.test_experience_collection import _workspace as observed_workspace


def test_reader_collector_entry_is_default_off_and_cannot_publish(tmp_path, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    tenant_id = runtime.profile.organization_id
    workspace = WorkspaceService(
        store_path=tmp_path / "experience.sqlite", store_tenant_id=tenant_id,
        runtime_configuration=runtime, review_duration_seconds=0,
    )
    workspace.form_quote()
    principal = Principal(
        issuer="https://issuer.example", subject="experience-reader",
        tenant_id=tenant_id, actor_id="reader:experience",
        roles=frozenset({"reader"}), expires_at=int(time.time()) + 3600,
    )
    token = request_principal.set(principal)
    try:
        assert request_action("POST", "/api/workspace/experience-collector/collect") == "read"
        assert request_action("POST", "/api/workspace/experience/DECIDE") == "govern"
        with TestClient(create_app(workspace_service=workspace)) as client:
            path = "/api/workspace/experience-collector/collect"
            disabled = client.post(path, json={"limit": 10})
            assert disabled.status_code == 200, disabled.text
            assert disabled.json()["status"] == "DISABLED"
            assert workspace.store.get_source_checkpoint(
                "experience-collector:workspace-quote-evidence-recovery-v1:v1"
            ) is None

            monkeypatch.setenv("ORGREBASE_EXPERIENCE_COLLECTOR_ENABLED", "1")
            monkeypatch.setenv("ORGREBASE_EXPERIENCE_COLLECTOR_ACTOR_ID", principal.actor_id)
            first = client.post(path, json={"limit": 50})
            assert first.status_code == 200, first.text
            assert first.json()["status"] in {"COMPLETE", "PARTIAL", "IDLE"}
            assert first.json()["admitted_lessons"] == 0
            assert first.json()["skill_versions_published"] == 0
            assert first.json()["business_writes"] == 0
            cases = client.get("/api/workspace/experience-cases")
            assert cases.status_code == 200 and cases.json()["items"] == []
            assert cases.headers["cache-control"] == "no-store"
            assert client.get("/api/workspace/experience-lessons").json()["items"] == []
            assert client.get("/api/workspace/experience-lessons/candidates").status_code == 409
            assert client.get("/api/workspace/experience-cases/experience-case:missing").status_code == 404

            foreign = request_principal.set(Principal(
                issuer="https://issuer.example", subject="foreign-reader",
                tenant_id="foreign:tenant", actor_id="reader:foreign",
                roles=frozenset({"reader"}), expires_at=int(time.time()) + 3600,
            ))
            try:
                assert client.get("/api/workspace/experience-cases").status_code == 403
                assert client.get("/api/workspace/experience-cases/experience-case:missing").status_code == 403
                assert client.get("/api/workspace/experience-lessons").status_code == 403
            finally:
                request_principal.reset(foreign)

            monkeypatch.setenv("ORGREBASE_EXPERIENCE_COLLECTOR_ACTOR_ID", "reader:other")
            denied = client.post(path, json={"limit": 50})
            assert denied.status_code == 403
    finally:
        request_principal.reset(token)
        workspace.close()


def test_case_page_projects_only_low_sensitivity_observation_metadata(tmp_path):
    workspace, event, request, _ = observed_workspace(tmp_path / "experience.sqlite")
    workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
        "event_id": event.event_id,
        "round": 1,
        "recovery_digest": request["digest"],
        "actor_id": "owner:product",
    })
    token = request_principal.set(collector_principal())
    try:
        from orgrebase.workspace.experience_collection import ExperienceCollector

        collector = ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        )
        assert collector.collect(worker_id="worker:one").observed == 1
        page = list_experience_cases(workspace, limit=1)
        assert len(page["items"]) == 1
        assert page["items"][0]["assessment"] == "UNAVAILABLE"
        assert page["items"][0]["assessment_coverage"] == "UNKNOWN"
        assert page["items"][0]["observation_assessment"] == "UNASSESSED"
        assert page["items"][0]["request_observed"] is True
        assert "private business value" not in str(page)
        assert "Private source review note" not in str(page)
    finally:
        request_principal.reset(token)
        workspace.store.close()


def test_case_page_reads_signed_assessment_without_promoting_observation(tmp_path, monkeypatch):
    workspace, event, request, _ = observed_workspace(tmp_path / "assessed.sqlite")
    workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
        "event_id": event.event_id, "round": 1,
        "recovery_digest": request["digest"], "actor_id": "owner:product",
    })
    for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
        monkeypatch.setenv(f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", f"actor:{phase}")
    monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "finance-v1")
    token = request_principal.set(collector_principal())
    try:
        from orgrebase.workspace.experience_collection import ExperienceCollector

        assert ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        ).collect(worker_id="worker:assessed").observed == 1
        case_ref = list_experience_cases(workspace)["items"][0]["case_ref"]
    finally:
        request_principal.reset(token)
    evaluator = Principal(
        issuer="local:test", subject="evaluator", tenant_id=workspace.store.tenant_id,
        actor_id="actor:evaluator", roles=frozenset({"governor"}),
        expires_at=int(time.time()) + 3600,
    )
    token = request_principal.set(evaluator)
    try:
        from orgrebase.workspace.experience_governance_operations import (
            AssessExperienceInput,
            assess_experience,
        )

        receipt = assess_experience(workspace, AssessExperienceInput(
            operation_id="assessment:one", case_ref=case_ref, verdict="UNKNOWN",
            reason_code="NOT_EVALUATED",
        ))
        assert receipt["assessment_ref"].startswith("experience-assessment:")
        page = list_experience_cases(workspace)["items"][0]
        assert page["assessment"] == "UNKNOWN"
        assert page["assessment_coverage"] == "COMPLETE"
        assert page["assessment_count_observed"] == 1
        assert page["observation_assessment"] == "UNASSESSED"
        assert receipt["assessment_ref"] not in str(page)
    finally:
        request_principal.reset(token)
        workspace.store.close()
