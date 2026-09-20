from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.domain import Approval, ObjectState
from orgrebase.workspace.service import WorkspaceService


def _classifications(bundle) -> dict[str, str]:
    return {item.object_id: item.classification.value for item in bundle.preview.results}


def _effects(bundle) -> dict[str, str]:
    return {item.target_id: item.disposition.value for item in bundle.minimal_rebase_certificate.effects}


def test_launch_preview_uses_runtime_quote_dependency(formed_service) -> None:
    bundle = formed_service.preview_change("launch_date")
    assert _classifications(bundle) == {
        "skill:enterprise-launch-readiness": "REQUALIFICATION_REQUIRED",
        "work:finance_analysis_d": "UNAFFECTED_WITHIN_DECLARED_BOUNDARY",
        "work:partner_brief_e": "UNKNOWN",
        "work:quote_acme": "AFFECTED_HARD",
    }
    assert _effects(bundle) == {
        "skill:enterprise-launch-readiness": "REQUALIFY",
        "work:finance_analysis_d": "PRESERVE_WITHIN_BOUNDARY",
        "work:partner_brief_e": "HOLD_FOR_REVIEW",
        "work:quote_acme": "REBUILD",
    }


def test_first_rebase_updates_only_launch_date_and_promotes_graph(
    formed_service, apply_staged_change
) -> None:
    before = formed_service.current_quote()
    finance_before = formed_service.store.get_object("work:finance_analysis_d")
    result = apply_staged_change(formed_service, "launch_date")
    after = result["quote"]
    assert after.version == "v2"
    assert after.payload["launch_date"] == "2026-09-15"
    assert after.payload["currency"] == before.payload["currency"]
    for field in (
        "owner",
        "customer_id",
        "product_plan",
        "data_residency",
        "notice_required",
        "price_band",
        "partner_terms_code",
    ):
        assert after.payload[field] == before.payload[field]
    assert result["graph_pointer"].version == "v2"
    finance_after = formed_service.store.get_object("work:finance_analysis_d")
    assert finance_after.version == finance_before.version
    assert finance_after.payload == finance_before.payload
    assert formed_service.store.get_object("work:partner_brief_e").state == ObjectState.REVIEW_REQUIRED


def test_restart_then_second_change_produces_quote_v3_and_graph_v3(workspace_db: Path) -> None:
    output = WorkspaceService.run_explicit_local_product_loop(
        workspace_db,
        allow_scripted_approval=True,
    )
    quote = output["final_quote"]
    pointer = output["final_graph_pointer"]
    assert quote.version == "v3"
    assert quote.payload["launch_date"] == "2026-09-15"
    assert quote.payload["currency"] == "EUR"
    assert pointer.version == "v3"
    assert pointer.payload["snapshot_ref"] == "workspace-graph:northstar@v3"
    assert output["event_chain"]["status"] == "PASS"

    reopened = WorkspaceService.reopen(workspace_db)
    try:
        assert reopened.current_quote().digest == quote.digest
        assert reopened.current_graph_pointer().digest == pointer.digest
    finally:
        reopened.close()


def test_successor_graph_contains_current_quote_manifest_and_trace(
    formed_service, apply_staged_change
) -> None:
    launch = apply_staged_change(formed_service, "launch_date")
    receipt = launch["workspace_rebase_receipt"]
    assert receipt is not None
    assert receipt.successor_object_refs == ("work:quote_acme@v2",)
    assert receipt.successor_trace_refs == ("work-trace:quote_acme@v2",)
    assert receipt.successor_manifest_refs == ("runtime-dependency:quote_acme@v2",)
    snapshot = formed_service.current_snapshot()
    assert "work:quote_acme@v2" in snapshot.object_refs
    assert "runtime-dependency:quote_acme@v2" in snapshot.manifest_refs


@pytest.mark.parametrize("fail_after", ["prepared", "graph-pointer"])
def test_rebase_extension_failure_is_zero_write(formed_service, fail_after: str) -> None:
    preview = formed_service.preview_change("launch_date")
    approved = formed_service.approve_change(
        "launch_date", actor_id=preview.change_spec.owner_id, preview_digest=preview.preview.digest)
    before_quote = formed_service.current_quote()
    before_pointer = formed_service.current_graph_pointer()
    before_claim = formed_service.store.get_object("claim:product.launch_date")
    before_events = formed_service.store.verify_event_chain()["events"]
    with pytest.raises(RuntimeError, match="INJECTED_WORKSPACE_SUCCESSOR_FAILURE"):
        formed_service._execute_apply(
            kind="launch_date",
            bundle=preview,
            approval=Approval.model_validate(approved["approval"]),
            fail_after=fail_after,
        )
    assert formed_service.current_quote().digest == before_quote.digest
    assert formed_service.current_graph_pointer().digest == before_pointer.digest
    assert formed_service.store.get_object("claim:product.launch_date").digest == before_claim.digest
    assert formed_service.store.verify_event_chain()["events"] == before_events
    with pytest.raises(KeyError):
        formed_service.store.get_object("work:quote_acme", "v2")
