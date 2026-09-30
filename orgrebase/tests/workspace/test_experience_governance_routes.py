from __future__ import annotations

import time

from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import Principal, request_action, request_principal
from orgrebase.workspace.experience_governance_operations import ExperiencePhaseActors, _lessons
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


def test_experience_phase_routes_keep_author_and_reviewer_separate(tmp_path, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    workspace = WorkspaceService(
        store_path=tmp_path / "experience-governance.sqlite",
        store_tenant_id=runtime.profile.organization_id,
        runtime_configuration=runtime, review_duration_seconds=0,
    )
    workspace.form_quote()
    for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
        monkeypatch.setenv(
            f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", f"actor:{phase}",
        )
    monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "source-assessment-v1")

    def actor(phase: str, role: str) -> Principal:
        return Principal(
            issuer="https://issuer.example", subject=phase,
            tenant_id=runtime.profile.organization_id, actor_id=f"actor:{phase}",
            roles=frozenset({role}), expires_at=int(time.time()) + 3600,
        )

    try:
        assert request_action("POST", "/api/workspace/experience-assessments") == "govern"
        assert request_action("POST", "/api/workspace/experience-lessons/decisions") == "govern"
        with TestClient(create_app(workspace_service=workspace)) as client:
            reader_token = request_principal.set(actor("collector", "reader"))
            try:
                empty = client.get("/api/workspace/experience-lessons")
                assert empty.status_code == 200, empty.text
                assert empty.json()["items"] == []
                assert empty.headers["cache-control"] == "no-store"
                assert client.get("/api/workspace/experience-lessons/candidates").status_code == 403
                assert client.get("/api/workspace/experience-lessons/heads/unknown").status_code == 404
                assert client.post("/api/workspace/experience-assessments", json={
                    "operation_id": "assess:forbidden", "case_ref": "case:unknown",
                    "verdict": "UNKNOWN", "reason_code": "NOT_EVALUATED",
                }).status_code == 403
            finally:
                request_principal.reset(reader_token)

            author_token = request_principal.set(actor("author", "operator"))
            try:
                proposed = client.post("/api/workspace/experience-lessons/candidates", json={
                    "operation_id": "candidate:one", "lesson_id": "finance-review",
                    "body": {
                        "kind": "PROCEDURAL_ADVICE",
                        "problem_code": "FINANCE_SOURCE_REVIEW",
                        "applicability_tags": ["finance:source-review"],
                        "applicability": ["Current evidence is incomplete."],
                        "contraindications": ["Do not use after source withdrawal."],
                        "steps": ["Ask the current owner to review the source."],
                        "stop_conditions": ["Stop if the source is not current."],
                    },
                    "support_assessment_refs": ["assessment:unqualified"],
                })
                assert proposed.status_code == 200, proposed.text
                assert proposed.json()["status"] == "PRIVATE_CANDIDATE_NOT_PUBLISHED"
                candidate = proposed.json()["candidate_ref"]
                assert client.get(f"/api/workspace/experience-lessons/candidates/{candidate}").status_code == 403
                assert client.get("/api/workspace/experience-lessons/candidates").status_code == 403
            finally:
                request_principal.reset(author_token)

            reviewer_token = request_principal.set(actor("reviewer", "governor"))
            try:
                review = client.get(f"/api/workspace/experience-lessons/candidates/{candidate}")
                assert review.status_code == 404
                assert "Current evidence is incomplete." not in review.text
                candidate_page = client.get("/api/workspace/experience-lessons/candidates")
                assert candidate_page.status_code == 200, candidate_page.text
                assert candidate_page.headers["cache-control"] == "no-store"
                assert candidate_page.json()["items"] == [{
                    "candidate_ref": candidate,
                    "profile_id": "workspace-change-explanation-v1",
                    "lesson_id": "finance-review",
                    "evidence_status": "HOLD",
                    "evidence_reason_code": "EVIDENCE_NOT_CURRENT",
                    "private_body_status": "AVAILABLE",
                    "review_status": "HOLD",
                    "publication_status": "NOT_CURRENT",
                    "support_assessment_count": 1,
                    "counter_assessment_count": 0,
                    "content_bytes_disclosed": 0,
                }]
                assert "Current evidence is incomplete." not in candidate_page.text
                assert client.get("/api/workspace/experience-lessons").json()["items"] == []
            finally:
                request_principal.reset(reviewer_token)
    finally:
        workspace.close()


def test_delta_v2_http_keeps_author_prepare_and_reviewer_diff_separate(tmp_path, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    workspace = WorkspaceService(
        store_path=tmp_path / "experience-delta-v2-http.sqlite",
        store_tenant_id=runtime.profile.organization_id,
        runtime_configuration=runtime, review_duration_seconds=0,
    )
    workspace.form_quote()
    for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
        monkeypatch.setenv(f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", f"actor:{phase}")
    monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "source-assessment-v1")

    def actor(phase: str, role: str) -> Principal:
        return Principal(
            issuer="https://issuer.example", subject=phase,
            tenant_id=runtime.profile.organization_id, actor_id=f"actor:{phase}",
            roles=frozenset({role}), expires_at=int(time.time()) + 3600,
        )

    try:
        assert request_action("POST", "/api/workspace/experience-lessons/deltas/prepare") == "propose"
        assert request_action("POST", "/api/workspace/experience-lessons/deltas/decisions") == "govern"
        with TestClient(create_app(workspace_service=workspace)) as client:
            author = request_principal.set(actor("author", "operator"))
            try:
                prepared = client.post("/api/workspace/experience-lessons/deltas/prepare", json={
                    "operation_id": "delta:http-noop", "action": "NOOP",
                    "candidate_ref": None, "expected_heads": [],
                })
                assert prepared.status_code == 200, prepared.text
                preparation_ref = prepared.json()["preparation_ref"]
                path = f"/api/workspace/experience-lessons/deltas/preparations/{preparation_ref}"
                assert client.get(path).status_code == 403
            finally:
                request_principal.reset(author)

            reviewer = request_principal.set(actor("reviewer", "governor"))
            try:
                reviewed = client.get(path)
                assert reviewed.status_code == 200, reviewed.text
                assert reviewed.headers["cache-control"] == "no-store"
                assert reviewed.json()["status"] == "REVIEW_ONLY_NOT_PUBLISHED"
                decision = {
                    "preparation_ref": preparation_ref,
                    "expected_diff_bytes_digest": reviewed.json()["diff_bytes_digest"],
                    "verdict": "REJECTED", "reason_code": "UNSUPPORTED_CHANGE",
                }
                decided = client.post("/api/workspace/experience-lessons/deltas/decisions", json=decision)
                assert decided.status_code == 200, decided.text
                assert client.post("/api/workspace/experience-lessons/deltas/decisions", json=decision).json() == decided.json()
                delta_path = f"/api/workspace/experience-lessons/deltas/{decided.json()['delta_ref']}"
                historical = client.get(delta_path)
                assert historical.status_code == 200, historical.text
                assert historical.headers["cache-control"] == "no-store"
                assert historical.json()["status"] == "REJECTED"
                assert historical.json()["diff_status"] == "EXACT"
                legacy_body = {
                    "operation_id": "delta:http-legacy-replay", "action": "NOOP",
                    "candidate_ref": None, "expected_heads": [],
                    "reason_code": "NO_ACCEPTABLE_CANDIDATE",
                }
                old_path = "/api/workspace/experience-lessons/decisions"
                blocked = client.post(old_path, json=legacy_body)
                assert blocked.status_code == 409, blocked.text
                assert blocked.json()["detail"]["code"] == "EXPERIENCE_DELTA_V2_PREPARATION_REQUIRED"
                old_ref = _lessons(
                    workspace, ExperiencePhaseActors.from_deployment(),
                ).apply_delta(
                    operation_id="delta:http-legacy-replay", action="NOOP",
                    candidate_ref=None, expected_heads=(),
                    reason_code="NO_ACCEPTABLE_CANDIDATE",
                )
                replay = client.post(old_path, json=legacy_body)
                assert replay.status_code == 200, replay.text
                assert replay.json() == {"delta_ref": old_ref, "status": "LEGACY_EXACT_REPLAY_ONLY"}
                changed = client.post(old_path, json={
                    **legacy_body, "reason_code": "DIFFERENT_REVIEW_REASON",
                })
                assert changed.status_code == 409
            finally:
                request_principal.reset(reviewer)
            author = request_principal.set(actor("author", "operator"))
            try:
                assert client.get(delta_path).status_code == 403
            finally:
                request_principal.reset(author)
    finally:
        workspace.close()
