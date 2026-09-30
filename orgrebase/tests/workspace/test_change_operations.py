from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, request_authorization
from orgrebase.domain import IntegrityError
from orgrebase.workspace.advisory import AdvisoryGenerationError
from orgrebase.workspace.bounded_execution import BoundedExecutionError
from orgrebase.workspace.change_operations import (
    change_work_items,
    load_change_preparation_config,
    prepare_pending_changes,
)
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace


def test_work_items_are_bounded_authority_projections_without_source_values(workspace):
    submit_change(workspace, command(workspace, value="PRIVATE_NEW_PLAN"))

    result = change_work_items(workspace, limit=1)

    assert result["schema_version"] == "orgrebase.change-work-items.v1"
    assert result["authority"] == "CURRENT_SERVER_AUTHORIZATION_PROJECTION"
    assert result["notification_delivery"] == "NOT_IMPLIED_BY_WORK_ITEM"
    assert result["canonical_writes"] == result["target_writes"] == 0
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["event_id"] == "edit-1"
    assert item["status"] == "RECEIVED"
    assert item["active_owner_id"] == workspace.change_owner["edit-1"]
    assert item["allowed_actions"] == ["PREVIEW", "REJECT"]
    assert "PRIVATE_NEW_PLAN" not in str(result)


def test_dry_run_does_not_reserve_or_dispatch_candidate_work(workspace):
    submit_change(workspace, command(workspace))

    result = prepare_pending_changes(workspace, max_changes=1, dry_run=True)

    assert result["records"][0]["result"] == "ELIGIBLE_NOT_DISPATCHED"
    assert workspace._preview_record("edit-1") is None
    assert result["canonical_writes"] == result["target_writes"] == 0


def test_worker_prepares_candidate_once_without_approving_or_applying(workspace):
    submit_change(workspace, command(workspace))
    before = workspace.current_quote()

    first = prepare_pending_changes(workspace, max_changes=1)
    second = prepare_pending_changes(workspace, max_changes=1)

    assert first["records"][0]["result"] == "CANDIDATE_PREPARED"
    assert first["records"][0]["preview_digest"] == workspace._preview_record("edit-1")["preview_digest"]
    assert second["records"] == []
    assert workspace._approval_record("edit-1") is None
    assert workspace.current_quote() == before


def test_worker_accepts_out_of_order_preparation_without_replaying_existing_attempt(workspace):
    submit_change(workspace, command(workspace, event_id="edit-1"))
    submit_change(
        workspace,
        command(workspace, event_id="edit-2", value="Second independent proposal"),
    )
    workspace.preview_change("edit-2")

    result = prepare_pending_changes(workspace, max_changes=2)
    repeated = prepare_pending_changes(workspace, max_changes=2)

    assert [item["event_id"] for item in result["records"]] == ["edit-1"]
    assert repeated["records"] == []


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_terminal_attempt_does_not_starve_later_work_after_restart(
    workspace, unknown, backend, postgres_runtime,
):
    options = {"store_path": workspace.store.path}
    if backend == "postgresql":
        tenant_id = workspace.profile.organization_id
        database = postgres_runtime(tenant_id=tenant_id)
        options = {"store_path": database["runtime_dsn"], "store_tenant_id": tenant_id,
                   "store_migrate": False}
        workspace = WorkspaceService(**options, runtime_configuration=workspace.runtime_configuration,
                                     clock=workspace.clock, review_duration_seconds=0)
        workspace.form_quote()
    for event_id in ("edit-1", "edit-2"):
        submit_change(workspace, command(workspace, event_id=event_id, value=event_id))
    original = workspace.advisory_factory.run

    def fail(**_kwargs):
        if unknown:
            raise BoundedExecutionError("BOUNDED_EXECUTION_CANCELLATION_UNCONFIRMED")
        raise RuntimeError("INJECTED_GENERATION_FAILURE")

    workspace.advisory_factory.run = fail
    with pytest.raises(RuntimeError):
        workspace.preview_change("edit-1")
    workspace.advisory_factory.run = original
    configuration, clock = workspace.runtime_configuration, workspace.clock
    workspace.close()
    restarted = WorkspaceService(**options, runtime_configuration=configuration,
                                 clock=clock, review_duration_seconds=0)
    try:
        result = prepare_pending_changes(restarted, max_changes=1)
        assert [(item["event_id"], item["result"]) for item in result["records"]] == [
            ("edit-2", "CANDIDATE_PREPARED")
        ]
        state = "RESULT_UNKNOWN" if unknown else "FAILED"
        assert result["blocked_attempts"][state] == 1
        assert restarted._preview_record("edit-1") is None
        repeated = prepare_pending_changes(restarted, max_changes=1)
        assert repeated["records"] == []
        assert repeated["blocked_attempts"][state] == 1
    finally:
        restarted.close()


def test_known_generation_failure_is_durable_and_does_not_abort_other_work(workspace, monkeypatch):
    for event_id in ("edit-1", "edit-2"):
        submit_change(workspace, command(workspace, event_id=event_id, value=event_id))
    original = workspace.advisory_factory.run
    calls = []

    def run(**kwargs):
        calls.append(kwargs["change_set"].id)
        if len(calls) == 1:
            raise AdvisoryGenerationError("WORKSPACE_ADVISORY_PROVIDER_RESULT_UNAVAILABLE")
        return original(**kwargs)

    monkeypatch.setattr(workspace.advisory_factory, "run", run)
    result = prepare_pending_changes(workspace, max_changes=2)
    assert [(item["event_id"], item["result"]) for item in result["records"]] == [
        ("edit-1", "BLOCKED"), ("edit-2", "CANDIDATE_PREPARED")
    ]
    assert result["records"][0]["reason_code"] == "WORKSPACE_ADVISORY_PROVIDER_RESULT_UNAVAILABLE"
    assert len(calls) == 2
    assert prepare_pending_changes(workspace, max_changes=1)["records"] == []
    assert len(calls) == 2


@pytest.mark.parametrize("error", [
    AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403),
    IntegrityError("WORKSPACE_ADVISORY_ATTEMPT_BINDING_INVALID"),
    IntegrityError("WORKSPACE_ADVISORY_DEPLOYMENT_RESERVATION_CONFLICT"),
    AdvisoryGenerationError("WORKSPACE_ADVISORY_PROVIDER_RESULT_UNAVAILABLE"),
])
def test_unpersisted_or_global_failure_stops_worker(workspace, monkeypatch, error):
    for event_id in ("edit-1", "edit-2"):
        submit_change(workspace, command(workspace, event_id=event_id, value=event_id))
    visited = []

    def fail(event_id):
        visited.append(event_id)
        raise error

    monkeypatch.setattr(workspace, "preview_change", fail)
    with pytest.raises(type(error), match=str(error)):
        prepare_pending_changes(workspace, max_changes=2)
    assert visited == ["edit-1"]
    assert workspace._preview_record("edit-2") is None


def test_worker_rechecks_authorization_before_candidate_dispatch(workspace):
    submit_change(workspace, command(workspace))
    calls = 0

    def revoked() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

    token = request_authorization.set(revoked)
    try:
        with pytest.raises(AuthenticationError, match="AUTH_MEMBERSHIP_DENIED"):
            prepare_pending_changes(workspace, max_changes=1)
    finally:
        request_authorization.reset(token)
    assert workspace._preview_record("edit-1") is None
    assert workspace.current_quote().payload["product_plan"] != "Enterprise Plus"


def test_change_work_item_route_enforces_page_budget(workspace):
    submit_change(workspace, command(workspace))
    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/change-work-items?limit=1")
        assert response.status_code == 200
        assert response.json()["items"][0]["event_id"] == "edit-1"
        assert client.get("/api/workspace/change-work-items?limit=101").status_code == 422


def test_change_preparation_config_is_private_bounded_and_content_addressed(tmp_path):
    path = tmp_path / "change-worker.json"
    path.write_text(
        '{"schema_version":"orgrebase.change-preparation.v1","workspace_id":"default",'
        '"access_token_variable":"ORGREBASE_CHANGE_WORKER_TOKEN","enabled":true,'
        '"provider_mode":"local-deterministic","max_changes_per_run":7}',
        encoding="utf-8",
    )
    config = load_change_preparation_config(path)
    assert config.enabled and config.max_changes_per_run == 7
    assert config.digest.startswith("sha256:")

    path.chmod(0o666)
    with pytest.raises(ValueError, match="CHANGE_PREPARATION_CONFIG_INVALID"):
        load_change_preparation_config(path)
