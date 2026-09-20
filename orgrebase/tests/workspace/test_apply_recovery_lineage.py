from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.domain import Approval, IntegrityError
from orgrebase.workspace.models import WorkspacePreviewBundle, WorkspaceRebaseReceipt
from orgrebase.workspace.service import WorkspaceService


def prepare(service: WorkspaceService, kind: str) -> tuple[WorkspacePreviewBundle, Approval]:
    preview = service.preview_change(kind)
    approved = service.approve_change(
        kind, actor_id=preview.change_spec.owner_id, preview_digest=preview.preview.digest
    )
    return preview, Approval.model_validate(approved["approval"])


@pytest.mark.parametrize("restart,advance", [(False, False), (True, False), (True, True)])
def test_committed_apply_recovers_its_own_successors(
    tmp_path: Path, restart: bool, advance: bool
) -> None:
    path = tmp_path / "recovery.sqlite"
    service = WorkspaceService(store_path=path)
    try:
        service.form_quote()
        bundle, approval = prepare(service, "launch_date")
        # The core transaction committed; the process lost its response before
        # persisting the product outcome. Subsequent requests may still proceed.
        committed = service._execute_apply(kind="launch_date", bundle=bundle, approval=approval)
        receipt = committed["workspace_rebase_receipt"]
        assert committed["quote"].ref == "work:quote_acme@v2"
        assert committed["quote"].ref in receipt.successor_object_refs
        assert committed["graph_pointer"].ref == receipt.graph_pointer_ref
        assert service._outcome_record("launch_date") is None
        if advance:
            _, next_approval = prepare(service, "currency")
            service.apply_approved_change("currency", approval_digest=next_approval.digest)
        if restart:
            service.close()
            service = WorkspaceService.reopen(path)
        current_quote = service.current_quote()
        current_pointer = service.current_graph_pointer()
        recovered = service.apply_approved_change("launch_date", approval_digest=approval.digest)
        outcome = recovered["outcome"]
        assert outcome["quote"]["version"] == "v2"
        assert outcome["quote"]["digest"] == committed["quote"].digest
        assert outcome["graph_pointer"]["version"] == "v2"
        assert outcome["graph_pointer"]["digest"] == committed["graph_pointer"].digest
        assert outcome["workspace_rebase_receipt"] == receipt.model_dump(mode="json")
        assert recovered["state"]["quote"]["version"] == ("v3" if advance else "v2")
        assert service.current_quote().digest == current_quote.digest
        assert service.current_graph_pointer().digest == current_pointer.digest

        events = service.store.verify_event_chain()
        retried = service.apply_approved_change("launch_date", approval_digest=approval.digest)
        assert retried["outcome"] == outcome
        assert service.store.verify_event_chain() == events
    finally:
        service.close()


@pytest.mark.parametrize("field", ["base_rebase_receipt_digest", "graph_pointer_ref"])
def test_success_projection_rejects_a_mismatched_workspace_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    service = WorkspaceService(store_path=tmp_path / "binding.sqlite")
    try:
        service.form_quote()
        _, approval = prepare(service, "launch_date")
        original = service._workspace_receipt

        def mismatched(**kwargs):
            receipt = original(**kwargs)
            payload = receipt.model_dump(mode="json", exclude={"digest"})
            payload[field] = (
                "sha256:" + "0" * 64
                if field == "base_rebase_receipt_digest"
                else f"{service.graph_pointer_id}@v1"
            )
            return WorkspaceRebaseReceipt.model_validate(payload)

        with monkeypatch.context() as fault:
            fault.setattr(service, "_workspace_receipt", mismatched)
            with pytest.raises(IntegrityError, match="WORKSPACE_APPLIED_SUCCESSOR_BINDING_INVALID"):
                service.apply_approved_change("launch_date", approval_digest=approval.digest)
        assert service._outcome_record("launch_date") is None
        assert service.current_quote().version == "v2"
        recovered = service.apply_approved_change("launch_date", approval_digest=approval.digest)
        assert recovered["outcome"]["quote"]["version"] == "v2"
    finally:
        service.close()
