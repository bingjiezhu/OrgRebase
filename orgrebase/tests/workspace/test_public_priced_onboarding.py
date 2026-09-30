"""Public priced template through the real local cookie authorization chain."""

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.local_role_session import LocalRoleSessionSettings
from orgrebase.runtime_config import QUOTE_DISCOUNT_MEMO_PROFILE, DeploymentSettings
from orgrebase.workspace.formation import quote_discount_memo_profile
from orgrebase.workspace.model_provider import (
    LiveHTTPModelProvider,
    LocalOllamaStructuredProvider,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.oac_quote_adaptation import OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import (
    initialize_enterprise_quote_pilot_draft,
    seal_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.task_intake import TaskIntakeCandidateReceipt


def test_public_priced_template_oac_intake_and_dual_change_without_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_model_call(*_args, **_kwargs):
        pytest.fail("This initial-facts walkthrough must not invoke a model")

    for provider in (LiveHTTPModelProvider, LocalOllamaStructuredProvider, VertexAIStructuredProvider):
        monkeypatch.setattr(provider, "generate_structured", forbidden_model_call)
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "required")
    monkeypatch.setenv("ORGREBASE_OAC_EXECUTION_MODE", "OFFLINE_LOCAL")
    monkeypatch.setenv("ORGREBASE_OAC_ROOT", str(Path(__file__).resolve().parents[3] / "oac-spec"))
    draft, pack = tmp_path / "draft", tmp_path / "pack"
    initialize_enterprise_quote_pilot_draft(draft, template_name="priced-quote")
    seal_enterprise_quote_pilot_pack(draft, pack)
    runtime = load_enterprise_quote_pilot_pack(pack)
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite", runtime_configuration=runtime,
        deliverable_set_profile=quote_discount_memo_profile(runtime), task_intake_required=True,
        competition_mode="off", review_duration_seconds=0,
    )
    origin = "http://127.0.0.1:8793"
    application = create_app(
        workspace_service=workspace,
        deployment_settings=DeploymentSettings(
            mode="local", deliverable_profile=QUOTE_DISCOUNT_MEMO_PROFILE,
            local_role_session=LocalRoleSessionSettings(origin),
        ),
    )
    session = {}
    try:
        with TestClient(application, base_url=origin, client=("127.0.0.1", 42000)) as client:
            def headers():
                result = {"Origin": origin, "Sec-Fetch-Site": "same-origin"}
                if session.get("csrf_token"):
                    result["X-CSRF-Token"] = session["csrf_token"]
                else:
                    result["X-OrgRebase-Local-Session"] = "initialize"
                return result

            def choose(actor_id):
                nonlocal session
                session = client.get("/api/session").json()
                response = client.post("/api/session/local-actor", json={"actor_id": actor_id}, headers=headers())
                assert response.status_code == 200, response.text
                session = response.json()

            def post(path, body=None):
                response = client.post(path, json=body or {}, headers=headers())
                assert response.status_code == 200, response.text
                return response.json()

            requester = workspace.profile.default_task.actor_id
            description = "Create an enterprise quote and its discount review memo using the admitted synthetic inputs."
            intake_input = {
                "prompt": description, "customer_id": workspace.profile.default_task.customer_id,
                "deliverable_kind": "QUOTE",
            }
            choose(requester)
            held = post("/api/workspace/task-intake/prepare", intake_input)
            assert held["status"] == "HOLD"
            assert client.get("/api/workspace/state").json()["quote"] is None
            choose(workspace.profile.governance.admission_authority_refs[0])
            client.get("/api/workspace/oac-adaptation")
            clock = [1000.0]
            application.state.oac_adaptation_service._wall_clock = lambda: clock[0]
            pending = post("/api/workspace/oac-adaptation/prepare", {"command_id": "public-priced-prepare"})
            admission = {
                "candidate_digest": pending["candidate_digest"],
                "owner_review_summary_digest": pending["owner_review_summary"]["digest"],
                "acknowledgements": list(OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS),
                "command_id": "public-priced-admit",
            }
            early = client.post("/api/workspace/oac-adaptation/approve", json=admission, headers=headers())
            assert early.status_code == 409
            clock[0] += 4
            ready = post("/api/workspace/oac-adaptation/approve", admission)
            assert ready["status"] == "READY_FOR_ORGREBASE"
            choose(requester)
            candidate = post("/api/workspace/task-intake/prepare", intake_input)
            assert candidate["status"] == "READY_FOR_CONFIRMATION", candidate["reason_codes"]
            approval = post("/api/workspace/task-intake/admit", {
                "candidate_receipt": candidate, "candidate_digest": candidate["digest"],
            })
            run_input = {
                "work_description": description, "candidate_receipt": candidate,
                "candidate_digest": candidate["digest"], "approval_receipt": approval,
                "approval_digest": approval["digest"],
            }
            before = workspace.store.connection.execute("SELECT COUNT(*) FROM object_versions").fetchone()[0]
            event_head = workspace.store.audit_head()
            workspace.deliverable_formation.fail_after = "first-deliverable"
            failed = client.post("/api/workspace/task-intake/run", json=run_input, headers=headers())
            assert failed.status_code == 409, failed.text
            assert workspace.store.connection.execute("SELECT COUNT(*) FROM object_versions").fetchone()[0] == before
            assert workspace.store.audit_head() == event_head
            failed_state = client.get("/api/workspace/state").json()
            assert failed_state["quote"] is None
            assert failed_state["workspace_gate"]["status"] == "READY_TO_FORM"
            assert workspace._task_intake_run_record() is None
            workspace.deliverable_formation.fail_after = None
            formed = post("/api/workspace/task-intake/run", run_input)
            assert formed["task_intake"]["status"] == "FORMATION_COMPLETED"
            assert client.get("/api/workspace/state").json()["workspace_gate"]["status"] == "CONSUMED_BY_QUOTE_FORMATION"
            assert {item.payload["deliverable_kind"] for item in workspace.current_deliverables()} == {"QUOTE", "DISCOUNT_MEMO"}
            assert workspace.current_quote().payload["pricing"]["total"] == "95.00"
            repeated = post("/api/workspace/task-intake/run", run_input)
            assert repeated["state"]["quote"]["digest"] == formed["state"]["quote"]["digest"]
            fields = client.get("/api/workspace/change-options").json()["fields"]
            field = next(item for item in fields if item["slot_id"] == "pricing_policy")
            event_id = "public-priced-discount"
            post("/api/workspace/change-proposals", {
                "event_id": event_id, "slot_id": field["slot_id"],
                "base_version": field["current"]["version"], "base_digest": field["current"]["digest"],
                "value": {**field["current"]["value"], "discount_bps": 1000,
                          "source_ref": "source:synthetic-pricing-policy@v2"},
                "source_ref": "source:synthetic-pricing-policy@v2",
            })
            preview = post(f"/api/workspace/preview/{event_id}")
            wrong_owner = client.post(
                f"/api/workspace/approve/{event_id}",
                json={"preview_digest": preview["preview_digest"], "actor_id": field["owner_id"]},
                headers=headers(),
            )
            assert wrong_owner.status_code == 403
            choose(field["owner_id"])
            source_approval = post(f"/api/workspace/approve/{event_id}", {"preview_digest": preview["preview_digest"]})
            assert workspace.current_quote().payload["pricing"]["total"] == "95.00"
            denied_apply = client.post(
                f"/api/workspace/apply/{event_id}",
                json={"approval_digest": source_approval["approval_digest"]}, headers=headers(),
            )
            assert denied_apply.status_code == 403
            for owner in {item.owner_id for item in workspace.deliverable_set_profile.members}:
                choose(owner)
                post(f"/api/workspace/approve/{event_id}/deliverable-set", {
                    "operation_id": "public-priced-output-decision",
                    "preview_digest": preview["preview_digest"], "decision": "APPROVED",
                })
            choose("human:local-change-executor")
            post(f"/api/workspace/apply/{event_id}", {"approval_digest": source_approval["approval_digest"]})
            assert workspace.current_quote().payload["pricing"]["total"] == "90.00"
            applied_head = workspace.store.audit_head()
            post(f"/api/workspace/apply/{event_id}", {"approval_digest": source_approval["approval_digest"]})
            assert workspace.store.audit_head() == applied_head
            choose(requester)
            exported = client.get("/api/workspace/export/deliverable-set")
            assert exported.status_code == 200, exported.text
            assert len(exported.json()["deliverables"]) == 2
            committed = {item.id: item.digest for item in workspace.current_deliverables()}
            completed_intake = workspace._task_intake_run_record()
            old_cookie = client.cookies.get("orgrebase_local_session")
    finally:
        workspace.close()

    reopened = WorkspaceService.reopen(
        tmp_path / "workspace.sqlite", runtime_configuration=runtime,
        deliverable_set_profile=quote_discount_memo_profile(runtime), task_intake_required=True,
        competition_mode="off", review_duration_seconds=0,
    )
    restarted = create_app(
        workspace_service=reopened,
        deployment_settings=DeploymentSettings(
            mode="local", deliverable_profile=QUOTE_DISCOUNT_MEMO_PROFILE,
            local_role_session=LocalRoleSessionSettings(origin),
        ),
    )
    try:
        with TestClient(restarted, base_url=origin, client=("127.0.0.1", 42000)) as client:
            client.cookies.set("orgrebase_local_session", old_cookie)
            assert client.get("/api/workspace/state").status_code == 401
            session = {}
            choose(requester)
            assert {item.id: item.digest for item in reopened.current_deliverables()} == committed
            assert reopened._task_intake_run_record() == completed_intake
            before = reopened.store.audit_head()
            changed_prompt = {**run_input, "work_description": description + " Alter the terms."}
            wrong_candidate_digest = {**run_input, "candidate_digest": "sha256:" + "0" * 64}
            wrong_approval_digest = {**run_input, "approval_digest": "sha256:" + "0" * 64}
            tampered_candidate = deepcopy(run_input)
            tampered_candidate["candidate_receipt"]["prompt_length"] += 1
            foreign = dict(candidate)
            foreign.pop("digest")
            foreign["workspace_instance_nonce"] = "sha256:" + "f" * 64
            foreign = TaskIntakeCandidateReceipt.model_validate(foreign).model_dump(mode="json")
            wrong_workspace = {**run_input, "candidate_receipt": foreign, "candidate_digest": foreign["digest"]}
            for invalid in (changed_prompt, wrong_candidate_digest, wrong_approval_digest, tampered_candidate, wrong_workspace):
                rejected = client.post("/api/workspace/task-intake/run", json=invalid, headers=headers())
                assert rejected.status_code == 409, rejected.text
                assert reopened.store.audit_head() == before
                assert {item.id: item.digest for item in reopened.current_deliverables()} == committed

            new_description = "Prepare an enterprise quote using these admitted pricing inputs."
            new_candidate = post("/api/workspace/task-intake/prepare", {**intake_input, "prompt": new_description})
            new_approval = post("/api/workspace/task-intake/admit", {
                "candidate_receipt": new_candidate, "candidate_digest": new_candidate["digest"],
            })
            different = {
                "work_description": new_description, "candidate_receipt": new_candidate,
                "candidate_digest": new_candidate["digest"], "approval_receipt": new_approval,
                "approval_digest": new_approval["digest"],
            }
            rejected = client.post("/api/workspace/task-intake/run", json=different, headers=headers())
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["detail"]["code"] == "EVIDENCE_INTEGRITY_FAILED"
            assert reopened.store.audit_head() == before

            choose(field["owner_id"])
            rejected = client.post("/api/workspace/task-intake/run", json={**run_input, "actor_id": requester}, headers=headers())
            assert rejected.status_code == 403, rejected.text
            choose(requester)

            local_sessions = restarted.state.local_role_sessions
            actors = local_sessions.actors()
            with monkeypatch.context() as membership_change:
                membership_change.setattr(local_sessions, "actors", lambda: [
                    {**item, "roles": [role for role in item["roles"] if role != "operator"]}
                    if item["actor_id"] == requester else item for item in actors
                ])
                rejected = client.post("/api/workspace/task-intake/run", json=run_input, headers=headers())
                assert rejected.status_code == 403, rejected.text
                assert reopened.store.audit_head() == before

            from orgrebase.workspace import oac_agent_adaptation

            implementation = oac_agent_adaptation.current_oac_admission_implementation()
            with monkeypatch.context() as policy_change:
                policy_change.setattr(
                    oac_agent_adaptation, "current_oac_admission_implementation",
                    lambda: {**implementation, "revision_digest": "sha256:" + "9" * 64},
                )
                rejected = client.post("/api/workspace/task-intake/run", json=run_input, headers=headers())
                assert rejected.status_code == 409, rejected.text
                assert reopened.store.audit_head() == before

            anchor_id = f"oac-context-binding:{completed_intake['oac_activation_binding_digest']}"
            anchor_media = "application/vnd.orgrebase.oac-context-binding+json"
            anchor = reopened.store.load_artifact(anchor_id, anchor_media)
            with reopened.store.transaction() as connection:
                connection.execute("DELETE FROM artifacts WHERE artifact_id = ?", (anchor_id,))
            try:
                rejected = client.post("/api/workspace/task-intake/run", json=run_input, headers=headers())
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["detail"]["code"] == "OAC_AGENTIC_RUNTIME_CONTEXT_TRUSTED_BINDING_REQUIRED"
                assert reopened.store.audit_head() == before
            finally:
                with reopened.store.transaction() as connection:
                    reopened.store.save_artifact(connection, anchor_id, anchor_media, anchor.payload)

            result = post("/api/workspace/task-intake/run", run_input)
            assert result["state"]["quote"]["digest"] == committed[reopened.quote_object_id]
            assert reopened.store.audit_head() == before
    finally:
        reopened.close()
