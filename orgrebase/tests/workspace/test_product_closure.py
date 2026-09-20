from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.digest import sha256_digest
from orgrebase.domain import Approval, EvidenceClass, ToolInvocationReceipt
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.models import ToolCalledEvent, WorkspacePreviewBundle
from orgrebase.workspace.service import (
    DEPENDENCY_TOOL_CALLED_EVENT_ID,
    DEPENDENCY_TOOL_RECEIPT_ID,
    WorkspaceService,
)


def _form_preview_approve_apply(
    client: TestClient,
    *,
    kind: str,
    actor_id: str,
) -> tuple[dict, dict, dict]:
    preview = client.post(f"/api/workspace/preview/{kind}")
    assert preview.status_code == 200
    preview_payload = preview.json()
    approval = client.post(
        f"/api/workspace/approve/{kind}",
        json={
            "actor_id": actor_id,
            "preview_digest": preview_payload["preview_digest"],
        },
    )
    assert approval.status_code == 200
    approval_payload = approval.json()
    outcome = client.post(
        f"/api/workspace/apply/{kind}",
        json={"approval_digest": approval_payload["approval_digest"]},
    )
    assert outcome.status_code == 200
    return preview_payload, approval_payload, outcome.json()


def test_explicit_workspace_flow_survives_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "product.sqlite"
    workspace = WorkspaceService(store_path=path)
    with TestClient(create_app(workspace_service=workspace)) as client:
        initial = client.get("/api/workspace/state").json()
        assert initial["stage"] == "EMPTY"
        assert initial["boundaries"] == {
            "execution_profile": "LOCAL_DETERMINISTIC",
            "agentteams": "NOT_RUN",
            "external_enterprise_systems": "NOT_RUN",
            "external_user_validation": "NOT_RUN",
            "oac_runtime_bridge": "NOT_USED_IN_THIS_RUN",
            "data_profile": "SYNTHETIC_FIXTURE",
        }

        formed = client.post("/api/workspace/form")
        assert formed.status_code == 200
        formed_payload = formed.json()
        assert formed_payload["state"]["stage"] == "CURRENT"
        assert formed_payload["state"]["coalition"]["plan_ref"]
        assert formed_payload["tool_evidence"]["status"] == "SUCCEEDED"
        assert formed_payload["tool_evidence"]["target_writes"] == 0
        tool_receipt = ToolInvocationReceipt.model_validate(
            formed_payload["tool_invocation"]["receipt"]
        )
        called_event = ToolCalledEvent.model_validate(
            formed_payload["tool_called_event"]
        )
        assert tool_receipt.evidence_class == EvidenceClass.LOCAL_REAL_TOOL
        assert called_event.invocation_receipt_ref == tool_receipt.id
        assert called_event.result_digest == tool_receipt.result_digest

        launch_preview, launch_approval, launch_outcome = _form_preview_approve_apply(
            client,
            kind="launch_date",
            actor_id="human:product-owner",
        )
        assert launch_preview["state"]["stage"] == "PREVIEWED"
        assert launch_approval["state"]["stage"] == "APPROVED"
        assert launch_outcome["state"]["stage"] == "CURRENT"
        quote_v2 = launch_outcome["state"]["quote"]
        assert quote_v2["payload"]["launch_date"] == "2026-09-15"
        assert quote_v2["payload"]["currency"] == "USD"

        # Browser refresh reads the same canonical state; it does not replay work.
        refreshed = client.get("/api/workspace/state").json()
        assert refreshed["stage"] == "CURRENT"
        assert refreshed["quote"]["digest"] == quote_v2["digest"]
    workspace.close()

    reopened = WorkspaceService.reopen(path)
    with TestClient(create_app(workspace_service=reopened)) as client:
        resumed = client.get("/api/workspace/state").json()
        assert resumed["stage"] == "CURRENT"
        assert resumed["latest_outcome"]["kind"] == "launch_date"
        assert resumed["dependency_evidence_tool"]["status"] == "SUCCEEDED"

        currency_preview, currency_approval, currency_outcome = (
            _form_preview_approve_apply(
                client,
                kind="currency",
                actor_id="human:finance-owner",
            )
        )
        assert currency_preview["state"]["stage"] == "PREVIEWED"
        assert currency_approval["state"]["stage"] == "APPROVED"
        final_state = currency_outcome["state"]
        assert final_state["stage"] == "CURRENT"
        assert final_state["quote"]["payload"]["launch_date"] == "2026-09-15"
        assert final_state["quote"]["payload"]["currency"] == "EUR"
        assert final_state["graph_snapshot"]["version"] == "v3"
        assert final_state["event_chain"]["status"] == "PASS"
        assert final_state["actions"]["next_operation"] is None
    reopened.close()


def test_commands_are_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "idempotent.sqlite")
    with TestClient(create_app(workspace_service=workspace)) as client:
        first_form = client.post("/api/workspace/form")
        events_after_first_form = workspace.store.verify_event_chain()["events"]
        repeated_form = client.post("/api/workspace/form")
        assert repeated_form.json()["receipt"]["digest"] == first_form.json()["receipt"]["digest"]
        assert (
            repeated_form.json()["tool_evidence"]["invocation_artifact_digest"]
            == first_form.json()["tool_evidence"]["invocation_artifact_digest"]
        )
        assert workspace.store.verify_event_chain()["events"] == events_after_first_form

        unknown = client.post("/api/workspace/preview/unregistered-event")
        assert unknown.status_code == 409
        assert workspace.store.verify_event_chain()["events"] == events_after_first_form

        first_preview = client.post("/api/workspace/preview/launch_date").json()
        repeated_preview = client.post("/api/workspace/preview/launch_date").json()
        assert repeated_preview["artifact_digest"] == first_preview["artifact_digest"]

        denied = client.post(
            "/api/workspace/approve/launch_date",
            json={
                "actor_id": "human:finance-owner",
                "preview_digest": first_preview["preview_digest"],
            },
        )
        assert denied.status_code == 403
        assert "WORKSPACE_APPROVER_MISMATCH" in denied.json()["detail"]["message"]

        stale_preview = client.post(
            "/api/workspace/approve/launch_date",
            json={
                "actor_id": "human:product-owner",
                "preview_digest": "sha256:" + "0" * 64,
            },
        )
        assert stale_preview.status_code == 409
        assert "WORKSPACE_PREVIEW_DIGEST_MISMATCH" in stale_preview.json()["detail"]["message"]

        approval_request = {
            "actor_id": "human:product-owner",
            "preview_digest": first_preview["preview_digest"],
        }
        first_approval = client.post(
            "/api/workspace/approve/launch_date", json=approval_request
        ).json()
        assert first_approval["artifact_id"] == first_approval["approval"]["id"]
        repeated_approval = client.post(
            "/api/workspace/approve/launch_date", json=approval_request
        ).json()
        assert repeated_approval["approval_digest"] == first_approval["approval_digest"]

        stale_approval = client.post(
            "/api/workspace/apply/launch_date",
            json={"approval_digest": "sha256:" + "1" * 64},
        )
        assert stale_approval.status_code == 409
        assert "WORKSPACE_APPROVAL_DIGEST_MISMATCH" in stale_approval.json()["detail"]["message"]

        apply_request = {"approval_digest": first_approval["approval_digest"]}
        first_apply = client.post("/api/workspace/apply/launch_date", json=apply_request).json()
        repeated_apply = client.post("/api/workspace/apply/launch_date", json=apply_request).json()
        assert repeated_apply["artifact_digest"] == first_apply["artifact_digest"]
        assert repeated_apply["state"]["quote"]["digest"] == first_apply["state"]["quote"]["digest"]
        approval_ref = first_apply["outcome"]["rebase_receipt"]["approval_ref"]
        persisted_approval = workspace.store.load_artifact(approval_ref)
        assert persisted_approval.payload["digest"] == first_approval["approval_digest"]
    workspace.close()


def _canonical_store_rows(workspace: WorkspaceService) -> dict[str, tuple[tuple, ...]]:
    tables = (
        "object_versions",
        "version_states",
        "current_pointers",
        "domain_events",
        "idempotency_records",
        "artifacts",
    )
    return {
        table: tuple(
            tuple(row)
            for row in workspace.store.connection.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        )
        for table in tables
    }


@pytest.mark.parametrize(
    ("failed_artifact_id", "error_code"),
    (
        ("task-receipt:quote_acme@v1", "INJECTED_FORMATION_COMMIT_FAILURE"),
        (DEPENDENCY_TOOL_RECEIPT_ID, "INJECTED_TOOL_COMMIT_FAILURE"),
        (DEPENDENCY_TOOL_CALLED_EVENT_ID, "INJECTED_TOOL_EVENT_COMMIT_FAILURE"),
    ),
)
def test_form_tool_and_called_event_commit_atomically_then_retry(
    tmp_path: Path,
    monkeypatch,
    failed_artifact_id: str,
    error_code: str,
) -> None:
    workspace = WorkspaceService(store_path=tmp_path / f"{error_code}.sqlite")
    baseline = _canonical_store_rows(workspace)
    before_chain = workspace.store.verify_event_chain()
    original_save = workspace.store.save_artifact

    with monkeypatch.context() as fault:
        def fail_selected_artifact(connection, artifact_id, media_type, payload):
            if artifact_id == failed_artifact_id:
                raise RuntimeError(error_code)
            return original_save(connection, artifact_id, media_type, payload)

        fault.setattr(workspace.store, "save_artifact", fail_selected_artifact)
        with pytest.raises(RuntimeError, match=error_code):
            workspace.form_quote_with_dependency_evidence()

    assert _canonical_store_rows(workspace) == baseline
    assert workspace.state()["stage"] == "EMPTY"
    assert workspace.state()["dependency_evidence_tool"]["status"] == "NOT_RUN"
    assert workspace.store.verify_event_chain() == before_chain

    committed = workspace.form_quote_with_dependency_evidence()
    assert committed["state"]["stage"] == "CURRENT"
    assert committed["tool_evidence"]["status"] == "SUCCEEDED"
    assert workspace.store.verify_event_chain()["events"] == before_chain["events"] + 2
    successful_rows = _canonical_store_rows(workspace)

    replayed = workspace.form_quote_with_dependency_evidence()
    assert replayed["receipt"].digest == committed["receipt"].digest
    assert _canonical_store_rows(workspace) == successful_rows

    conflicting_payload = WorkspaceFormationService.default_request().model_dump(
        mode="json", exclude={"digest"}
    )
    conflicting_payload["customer_id"] = "customer:conflicting-bytes"
    conflicting_request = type(WorkspaceFormationService.default_request()).model_validate(
        conflicting_payload
    )
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        workspace.form_quote_with_dependency_evidence(conflicting_request)
    assert _canonical_store_rows(workspace) == successful_rows
    workspace.close()


def test_preview_and_approval_are_each_resumable_stages(tmp_path: Path) -> None:
    path = tmp_path / "intermediate-stages.sqlite"
    workspace = WorkspaceService(store_path=path)
    workspace.form_quote()
    preview = workspace.preview_command("launch_date")
    assert preview["state"]["stage"] == "PREVIEWED"
    workspace.close()

    after_preview = WorkspaceService.reopen(path)
    approval = after_preview.approve_change(
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    assert approval["state"]["stage"] == "APPROVED"
    after_preview.close()

    after_approval = WorkspaceService.reopen(path)
    try:
        state = after_approval.state()
        assert state["stage"] == "APPROVED"
        assert state["latest_preview"]["artifact_digest"] == preview["artifact_digest"]
        assert state["latest_approval"]["approval_digest"] == approval["approval_digest"]
        applied = after_approval.apply_approved_change(
            "launch_date",
            approval_digest=approval["approval_digest"],
        )
        assert applied["state"]["stage"] == "CURRENT"
    finally:
        after_approval.close()


def test_only_frozen_northstar_acme_scenario_is_accepted(tmp_path: Path) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "scenario.sqlite")
    request = WorkspaceFormationService.default_request()
    altered = request.model_dump(mode="json", exclude={"digest"})
    altered["id"] = "task:quote_globex"
    altered["customer_id"] = "customer:globex"
    altered["idempotency_key"] = "workspace:form:quote-globex@v1"
    with TestClient(create_app(workspace_service=workspace)) as client:
        rejected = client.post("/api/workspace/form", json=altered)
        assert rejected.status_code == 409
        assert "UNSUPPORTED_SYNTHETIC_SCENARIO" in rejected.json()["detail"]["message"]
        state = client.get("/api/workspace/state").json()
        assert state["stage"] == "EMPTY"
        assert state["quote"] is None
    workspace.close()


def test_apply_outcome_is_recovered_after_commit_before_response_crash(tmp_path: Path) -> None:
    path = tmp_path / "crash-recovery.sqlite"
    workspace = WorkspaceService(store_path=path)
    workspace.form_quote()
    preview = workspace.preview_command("launch_date")
    approved = workspace.approve_change(
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    bundle = WorkspacePreviewBundle.model_validate(preview["bundle"])
    approval = Approval.model_validate(approved["approval"])
    # Simulate a process dying after the atomic Rebase commit but before the
    # product-layer outcome artifact and HTTP response were recorded.
    workspace._execute_apply(
        kind="launch_date",
        bundle=bundle,
        approval=approval,
    )
    workspace.close()

    reopened = WorkspaceService.reopen(path)
    try:
        recovered = reopened.apply_approved_change(
            "launch_date",
            approval_digest=approval.digest,
        )
        assert recovered["state"]["stage"] == "CURRENT"
        assert recovered["outcome"]["approval_digest"] == approval.digest
        assert reopened._outcome_record("launch_date") is not None
    finally:
        reopened.close()


def test_exports_are_downloadable_and_self_verifying_then_maintenance_reset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_ALLOW_DESTRUCTIVE_RESET", "1")
    workspace = WorkspaceService(store_path=tmp_path / "export.sqlite")
    with TestClient(create_app(workspace_service=workspace)) as client:
        client.post("/api/workspace/form")
        quote = client.get("/api/workspace/export/quote")
        assert quote.status_code == 200
        assert quote.headers["content-disposition"].endswith('"orgrebase-quote.json"')
        quote_payload = quote.json()
        quote_digest = quote_payload.pop("digest")
        assert sha256_digest(quote_payload) == quote_digest

        evidence = client.get("/api/workspace/export/evidence")
        assert evidence.status_code == 200
        assert evidence.headers["content-disposition"].endswith('"orgrebase-evidence.json"')
        evidence_payload = evidence.json()
        evidence_digest = evidence_payload.pop("digest")
        assert sha256_digest(evidence_payload) == evidence_digest
        assert evidence_payload["event_chain"]["status"] == "PASS"
        assert evidence_payload["event_scopes"]["quote_business"]["status"] == "PASS"
        assert evidence_payload["event_scopes"]["workspace_global"]["head_digest"] == (
            evidence_payload["event_chain"]["head_digest"]
        )
        tool_evidence = evidence_payload["dependency_evidence_tool"]
        receipt = ToolInvocationReceipt.model_validate(
            tool_evidence["invocation"]["receipt"]
        )
        called_event = ToolCalledEvent.model_validate(tool_evidence["called_event"])
        assert tool_evidence["status"] == "SUCCEEDED"
        assert tool_evidence["target_writes"] == 0
        assert called_event.invocation_receipt_ref == receipt.id
        assert called_event.request_digest == receipt.request_digest
        assert called_event.result_digest == receipt.result_digest

        reset = client.post("/api/workspace/reset")
        assert reset.status_code == 200
        assert reset.json()["state"]["stage"] == "EMPTY"
        assert reset.json()["state"]["quote"] is None
        assert client.get("/api/workspace/export/quote").status_code == 409
        assert client.get("/api/workspace/export/evidence").status_code == 409
    workspace.close()


def test_default_workspace_store_uses_environment_and_closes_with_lifespan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "configured" / "workspace.sqlite3"
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(path))
    application = create_app()
    with TestClient(application) as client:
        assert application.state.workspace_store_path is None
        assert application.state.workspace_service is None
        assert client.get("/api/workspace/state").json()["stage"] == "EMPTY"
        direct = client.post("/api/workspace/form")
        assert direct.status_code == 410
        assert direct.json()["detail"]["code"] == "WORKSPACE_TASK_INTAKE_REQUIRED"
        assert application.state.workspace_store_path == str(path)
    assert application.state.workspace_service.store._closed is True

    reopened = WorkspaceService.reopen(path)
    try:
        assert reopened.state()["stage"] == "EMPTY"
    finally:
        reopened.close()


def test_state_waits_for_an_inflight_approval_transaction(tmp_path: Path, monkeypatch) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "concurrent-read.sqlite")
    workspace.form_quote()
    preview = workspace.preview_command("launch_date")
    entered_write = Event()
    release_write = Event()
    original_save = workspace.store.save_artifact

    def paused_save(connection, artifact_id, media_type, payload):
        if artifact_id == workspace._approval_artifact_id("launch_date"):
            entered_write.set()
            assert release_write.wait(timeout=5)
        return original_save(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(workspace.store, "save_artifact", paused_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        approving = executor.submit(
            workspace.approve_change,
            "launch_date",
            actor_id="human:product-owner",
            preview_digest=preview["preview_digest"],
        )
        assert entered_write.wait(timeout=5)
        reading = executor.submit(workspace.state)
        assert not reading.done()
        release_write.set()
        assert approving.result(timeout=5)["state"]["stage"] == "APPROVED"
        assert reading.result(timeout=5)["stage"] == "APPROVED"
    workspace.close()
