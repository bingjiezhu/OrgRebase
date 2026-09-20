from __future__ import annotations

from orgrebase.workspace.service import WorkspaceService


def test_successor_graph_survives_restart_and_second_change(
    persistent_workspace, apply_staged_change
) -> None:
    service, path = persistent_workspace
    service.form_quote()
    first = apply_staged_change(service, "launch_date")
    assert first["quote"].version == "v2"
    assert first["graph_pointer"].version == "v2"
    service.close()

    reopened = WorkspaceService.reopen(path)
    try:
        second = apply_staged_change(reopened, "currency")
        assert second["quote"].version == "v3"
        assert second["quote"].payload["launch_date"] == "2026-09-15"
        assert second["quote"].payload["currency"] == "EUR"
        assert second["graph_pointer"].version == "v3"
        assert reopened.current_snapshot().version == "v3"
        assert reopened.store.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()
