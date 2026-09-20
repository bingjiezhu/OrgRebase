from __future__ import annotations

import json
from threading import RLock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_effect_operations import approve_and_queue
from test_effect_operations import workflow as workflow

from orgrebase.api import _change_approval_count, create_app
from orgrebase.clock import FrozenClock
from orgrebase.store import StateStore
from orgrebase.workspace.change_proposals import ReviewObservationInput, record_review, submit_change
from orgrebase.workspace.operations_view import operations_snapshot
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace


def test_operational_unknown_backlog_is_exact_and_content_free(workflow):
    workspace, server, _, _, _, work, _ = workflow
    approve_and_queue(workflow)
    server.lose_response = True
    assert work()["commands"][0]["state"] == "COMMIT_UNKNOWN"
    before = workspace.store.audit_head()
    report = operations_snapshot(workspace)
    assert report["effect_counts"]["COMMIT_UNKNOWN"] == 1
    assert report["effect_counts"]["CONFIRMED"] == 0
    assert report["alerts"][0]["code"] == "EXTERNAL_OUTCOME_UNKNOWN"
    assert report["unknown_age_sample_complete"]
    assert report["unknown_oldest_observed_age_seconds"] == 0
    assert "Owner-approved revised draft" not in json.dumps(report)
    assert "user:owner" not in json.dumps(report["metrics"])
    assert workspace.store.audit_head() == before and report["target_writes"] == 0


def test_review_observation_is_idempotent_feed_data_and_never_independent_labor_proof(workspace):
    submit_change(workspace, command(workspace))
    preview = workspace.preview_change("edit-1")
    checkpoint = workspace.store.audit_head()["sequence_no"]
    before_quote = workspace.current_quote().digest
    observation = ReviewObservationInput(observation_id="active:one", preview_digest=preview.preview.digest,
                                         active_ms=2300, action="PREVIEW", outcome="SUCCESS")
    first = record_review(workspace, "edit-1", observation)
    assert record_review(workspace, "edit-1", observation) == first
    report = operations_snapshot(workspace, after=checkpoint, limit=1)
    assert len(report["review_observations"]) == 1
    assert report["review_observations"][0]["active_ms"] == 2300
    assert report["review_observations"][0]["event_id"] == "edit-1"
    assert report["review_observations"][0]["action"] == "PREVIEW"
    assert report["review_observations"][0]["outcome"] == "SUCCESS"
    assert report["review_observations"][0]["observation_digest"] == first["observation_digest"]
    assert report["event_page"]["through"] == checkpoint + 1
    assert report["measurement_boundaries"]["active_review"] == "CLIENT_REPORTED_NOT_INDEPENDENT_LABOR_MEASUREMENT"
    assert workspace._approval_record("edit-1") is None
    assert workspace.current_quote().digest == before_quote
    assert operations_snapshot(workspace, after=checkpoint + 1)["review_observations"] == []


def test_current_progress_counts_arbitrary_change_ids_without_a_two_approval_target():
    def approved(identity):
        return {"approval": {"approval_digest": identity, "approval": {"digest": identity}}}
    state = {"schema_version": "orgrebase.workspace-state.v2", "changes": {
        "new-product-plan": approved("sha256:a"), "source:generated": approved("sha256:b"),
        "third-change": approved("sha256:c"),
    }}
    assert _change_approval_count(state) == 3


def test_operations_api_enforces_page_budget_and_emits_json_wire(workspace):
    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/operations?limit=1")
        assert response.status_code == 200
        assert response.json()["event_page"]["count"] == 1
        assert response.json()["source"]["status"] == "NOT_CONFIGURED"
        assert response.headers["OrgRebase-Wire-Scheme"] == "orgrebase.jcs-safe-number.v1"
        assert client.get("/api/workspace/operations?limit=101").status_code == 422


@pytest.mark.parametrize("after,limit", [(-1, 1), (0, 101), (True, 1)])
def test_operations_direct_calls_cannot_bypass_page_budget(workspace, after, limit):
    with pytest.raises(ValueError):
        operations_snapshot(workspace, after=after, limit=limit)


def test_operations_reads_pending_and_checkpoint_from_shared_database(workspace, tmp_path):
    observer = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite",
        runtime_configuration=workspace.runtime_configuration,
        review_duration_seconds=0,
    )
    try:
        assert operations_snapshot(observer)["unresolved_changes"] == 0
        submit_change(workspace, command(workspace))
        assert observer.changes.pending_count == 0
        report = operations_snapshot(observer)
        assert report["unresolved_changes"] == 1
        assert report["checkpoint"] == workspace.store.audit_head()
        assert report["metrics"]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"][0]["asInt"] == "1"
        workspace.reject_change("edit-1", actor_id=workspace.change_owner["edit-1"], reason="Withdrawn by owner")
        report = operations_snapshot(observer)
        assert report["unresolved_changes"] == 0
        assert report["checkpoint"] == workspace.store.audit_head()
        assert observer.changes.pending_count == 0
    finally:
        observer.close()


def test_operations_single_snapshot_keeps_postgres_workspaces_isolated(postgres_runtime):
    database = postgres_runtime(tenant_id="org:operations")
    arguments = {"tenant_id": "org:operations", "migrate": False}
    stamp = "2026-09-09T00:00:00Z"
    with StateStore(database["runtime_dsn"], **arguments) as first:
        first.bind_workspace(profile_digest="sha256:" + "1" * 64, pack_digest=None, quote_object_id="quote:a")
        first.register_workspace("quote-b", profile_digest="sha256:" + "1" * 64,
                                 pack_digest=None, quote_object_id="quote:b", created_at=stamp)
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as second:
            for store, effect in ((first, "effect-a"), (second, "effect-b")):
                with store.transaction() as connection:
                    store.append_event(connection, "OBSERVED", {"workspace": store.workspace_id})
                    store.put_effect(connection, effect_id=effect, target_key=effect,
                                     request_digest=effect, request={}, created_at=stamp)
            with second.transaction() as connection:
                second.update_effect(connection, effect_id="effect-b", expected_state="READY",
                                     state="COMMIT_UNKNOWN", updated_at=stamp)
                for index in range(100):
                    effect = f"effect-b:{index}"
                    second.put_effect(connection, effect_id=effect, target_key=effect,
                                      request_digest=effect, request={}, created_at=stamp)
                    second.update_effect(connection, effect_id=effect, expected_state="READY",
                                         state="COMMIT_UNKNOWN", updated_at=stamp)
            for store, expected in ((first, "READY"), (second, "COMMIT_UNKNOWN")):
                view = SimpleNamespace(store=store, clock=FrozenClock("2026-09-10T00:00:00Z"),
                                       _command_lock=RLock(), approval_identity_mode="BODY_ACTOR_COMPATIBILITY")
                report = operations_snapshot(view)
                count = 101 if store is second else 1
                assert report["effect_counts"][expected] == count
                assert sum(report["effect_counts"].values()) == count
                assert report["unknown_age_sample_limit"] is None
                assert report["unknown_age_sample_complete"] is True
                assert report["checkpoint"] == store.audit_head()
                assert report["event_page"]["count"] == 1
                assert report["event_page"]["next_cursor"] is None
                assert report["unknown_oldest_observed_age_seconds"] == (86400 if store is second else None)
