from __future__ import annotations

from orgrebase.workspace.service import WorkspaceService


def test_successor_graph_survives_restart_and_second_change(persistent_workspace) -> None:
    service, path = persistent_workspace
    service.form_quote()
    first = service.apply_change("launch_date")
    assert first["quote"].version == "v2"
    assert first["graph_pointer"].version == "v2"
    service.close()

    reopened = WorkspaceService.reopen(path)
    try:
        second = reopened.apply_change("currency")
        assert second["quote"].version == "v3"
        assert second["quote"].payload["launch_date"] == "2026-09-15"
        assert second["quote"].payload["currency"] == "EUR"
        assert second["graph_pointer"].version == "v3"
        assert reopened.current_snapshot().version == "v3"
        assert reopened.store.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()
