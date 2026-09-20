"""Responsibility projections retain the authority of their exact proposal."""

from orgrebase.workspace.owner_change import activate_owner_change
from tests.workspace.test_owner_migration import confirmed, pending
from tests.workspace.test_owner_migration import migration_workspace as migration_workspace


def test_migrated_owner_requires_replan_without_relabelling_original_proposal(migration_workspace):
    workspace, principal, *_ = migration_workspace
    with principal("operator"):
        pending(workspace, "old-event")
    original_owner = workspace.changes.get("old-event").owner_id
    command = confirmed(workspace, principal)
    with principal("successor"):
        activate_owner_change(workspace, "handover", command)
        row = workspace.change_history(limit=1)["items"][0]
    assert row["status"] == "EXPIRED"
    assert row["responsibility"] == {
        "owner_id": original_owner,
        "delegate_id": None,
        "blocked_reason": "CHANGE_OWNER_REPLAN_REQUIRED",
        "decision_actor_id": None,
    }
