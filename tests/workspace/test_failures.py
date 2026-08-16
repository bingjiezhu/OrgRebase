from __future__ import annotations

import pytest


@pytest.mark.parametrize("point", ["prepared", "graph-pointer"])
def test_successor_failure_rolls_back_quote_and_graph(workspace_service, point: str) -> None:
    workspace_service.form_quote()
    before_quote = workspace_service.current_quote()
    before_pointer = workspace_service.current_graph_pointer()
    with pytest.raises(RuntimeError, match="INJECTED_WORKSPACE_SUCCESSOR_FAILURE"):
        workspace_service.apply_change("launch_date", fail_after=point)
    assert workspace_service.current_quote().digest == before_quote.digest
    assert workspace_service.current_graph_pointer().digest == before_pointer.digest
    assert workspace_service.store.get_object("claim:product.launch_date").version == "v7"
