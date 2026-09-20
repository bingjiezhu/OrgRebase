from __future__ import annotations

from pathlib import Path

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, ToolInvocationReceipt
from orgrebase.tools import DEPENDENCY_EVIDENCE_CONTRACT
from orgrebase.workspace.models import ToolCalledEvent
from orgrebase.workspace.service import (
    TOOL_CALLED_EVENT_MEDIA_TYPE,
    TOOL_INVOCATION_MEDIA_TYPE,
    WORKSPACE_RUN_ID,
    WorkspaceService,
)


def test_main_workspace_run_persists_bound_tool_receipt_and_event(tmp_path: Path) -> None:
    database = tmp_path / "tool-trace.sqlite"
    result = WorkspaceService.run_explicit_local_product_loop(
        database,
        allow_scripted_approval=True,
    )
    service = WorkspaceService.reopen(database)
    try:
        assert result["workflow_run_id"] == result["run_id"] == WORKSPACE_RUN_ID
        assert result["stage_run_ids"] == {
            "formation": "run:workspace:quote-acme@v1",
            "dependency_evidence_tool": WORKSPACE_RUN_ID,
            "launch_rebase": WORKSPACE_RUN_ID,
            "currency_rebase": WORKSPACE_RUN_ID,
        }

        invocation = result["tool_invocation"]
        receipt = ToolInvocationReceipt.model_validate(invocation["receipt"])
        event = ToolCalledEvent.model_validate(result["tool_called_event"])
        expected_tool_ref = (
            f"{DEPENDENCY_EVIDENCE_CONTRACT.id}@{DEPENDENCY_EVIDENCE_CONTRACT.version}"
        )
        assert receipt.workflow_run_id == event.run_id == WORKSPACE_RUN_ID
        assert receipt.evidence_class == EvidenceClass.LOCAL_REAL_TOOL
        assert event.invocation_receipt_ref == receipt.id
        assert event.tool_ref == receipt.tool_ref == expected_tool_ref
        assert event.request_digest == receipt.request_digest
        assert event.result_digest == receipt.result_digest == sha256_digest(invocation["result"])
        assert receipt.started_at >= result["formation"].committed_at
        events = service.store.event_records()
        tool_event = next(item for item in events if item["event_digest"] == receipt.audit_event_digest)
        formation_event = next(item for item in events if item["event_type"] == "WORKSPACE_TASK_COMMITTED")
        assert tool_event["sequence_no"] > formation_event["sequence_no"]
        assert service.store.load_artifact(
            receipt.id, TOOL_INVOCATION_MEDIA_TYPE
        ).payload == invocation
        assert service.store.load_artifact(
            event.event_id, TOOL_CALLED_EVENT_MEDIA_TYPE
        ).payload == event.model_dump(mode="json")
    finally:
        service.close()


def test_dependency_tool_output_schema_matches_response_wrapper(tmp_path: Path) -> None:
    result = WorkspaceService.run_explicit_local_product_loop(
        tmp_path / "tool-schema.sqlite",
        allow_scripted_approval=True,
    )["tool_invocation"]
    schema = DEPENDENCY_EVIDENCE_CONTRACT.output_schema
    assert set(schema["required"]) <= set(result)
    assert set(schema["properties"]["result"]["required"]) <= set(result["result"])
    assert set(schema["properties"]["receipt"]["required"]) <= set(result["receipt"])
    assert set(schema["properties"]["contract"]["required"]) <= set(result["contract"])
