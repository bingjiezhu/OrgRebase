from __future__ import annotations

import json

import pytest

from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.models import RuntimeDependencyManifest, TraceCoverageReceipt, WorkTrace


def test_quote_does_not_exist_before_formation(workspace_service) -> None:
    assert workspace_service.state()["quote"] is None
    assert workspace_service.state()["graph_pointer"] is None


def test_atomic_formation_creates_quote_trace_manifest_and_snapshot(workspace_service) -> None:
    receipt = workspace_service.form_quote()
    quote = workspace_service.current_quote()
    assert quote.version == "v1"
    assert quote.payload["launch_date"] == "2026-09-01"
    assert quote.payload["currency"] == "USD"
    trace = WorkTrace.model_validate(workspace_service.store.load_artifact(receipt.trace_ref).payload)
    coverage = TraceCoverageReceipt.model_validate(
        workspace_service.store.load_artifact(receipt.coverage_receipt_ref).payload
    )
    manifest = RuntimeDependencyManifest.model_validate(
        workspace_service.store.load_artifact(receipt.dependency_manifest_ref).payload
    )
    assert len([event for event in trace.events if event.event_type == "REFERENCE_RESOLVED"]) == 8
    assert coverage.status == "PASS"
    assert len(manifest.entries) == 8
    assert {entry.slot_id for entry in manifest.entries} == set(coverage.observed_slots)
    pointer = workspace_service.current_graph_pointer()
    assert pointer.version == "v1"
    assert pointer.payload["snapshot_ref"] == receipt.graph_snapshot_ref


def test_formation_is_idempotent(workspace_service) -> None:
    first = workspace_service.form_quote()
    second = workspace_service.form_quote()
    assert second.digest == first.digest
    assert workspace_service.current_quote().version == "v1"
    assert workspace_service.store.get_pointer("work:quote_acme")["revision"] == 1


def test_same_idempotency_key_with_different_request_is_rejected(workspace_service) -> None:
    request = WorkspaceFormationService.default_request()
    workspace_service.form_quote(request)
    payload = request.model_dump(mode="json", exclude={"digest"})
    payload["customer_id"] = "customer:other"
    changed = type(request).model_validate(payload)
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        workspace_service.form_quote(changed)


def test_formation_failure_rolls_back_all_business_writes(workspace_service, monkeypatch) -> None:
    original = workspace_service.store.save_artifact
    calls = {"count": 0}

    def fail_after_some(connection, artifact_id, media_type, payload):
        calls["count"] += 1
        if calls["count"] == 5:
            raise RuntimeError("INJECTED_FORMATION_FAILURE")
        return original(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(workspace_service.store, "save_artifact", fail_after_some)
    with pytest.raises(RuntimeError, match="INJECTED_FORMATION_FAILURE"):
        workspace_service.form_quote()
    with pytest.raises(KeyError):
        workspace_service.store.get_object("work:quote_acme")
    with pytest.raises(KeyError):
        workspace_service.store.get_object("graph:workspace")
    assert not workspace_service.store.artifact_exists("work-trace:quote_acme@v1")
    assert workspace_service.store.verify_event_chain()["events"] == 1


def test_trace_payload_has_no_canary_or_restricted_source(formed_service) -> None:
    trace = formed_service.store.load_artifact("work-trace:quote_acme@v1").payload
    rendered = json.dumps(trace, ensure_ascii=False)
    assert "ORGREBASE_CANARY_SECRET_" not in rendered
    assert "SYNTHETIC RESTRICTED SOURCE" not in rendered
