from __future__ import annotations

import pytest

from orgrebase.domain import Approval


@pytest.mark.parametrize("point", ["prepared", "graph-pointer"])
def test_successor_failure_rolls_back_quote_and_graph(workspace_service, point: str) -> None:
    workspace_service.form_quote()
    preview = workspace_service.preview_change("launch_date")
    approved = workspace_service.approve_change(
        "launch_date", actor_id=preview.change_spec.owner_id, preview_digest=preview.preview.digest)
    before_quote = workspace_service.current_quote()
    before_pointer = workspace_service.current_graph_pointer()
    before_chain = workspace_service.store.verify_event_chain()
    with pytest.raises(RuntimeError, match="INJECTED_WORKSPACE_SUCCESSOR_FAILURE"):
        workspace_service._execute_apply(
            kind="launch_date",
            bundle=preview,
            approval=Approval.model_validate(approved["approval"]),
            fail_after=point,
        )
    assert workspace_service.current_quote().digest == before_quote.digest
    assert workspace_service.current_graph_pointer().digest == before_pointer.digest
    assert workspace_service.store.verify_event_chain() == before_chain
    assert workspace_service.store.get_object("claim:product.launch_date").version == "v7"
