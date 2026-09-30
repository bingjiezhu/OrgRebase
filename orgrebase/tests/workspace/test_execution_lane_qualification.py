"""Supported local-lane release checks; no network/model calls or kill claims."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from enterprise_pack_factory import make_enterprise_pack
from sqlalchemy import select

from orgrebase.database import idempotency_records
from orgrebase.domain import IntegrityError
from orgrebase.workspace import advisory as advisory_module
from orgrebase.workspace.advisory import AdvisoryGenerationError, WorkspaceChangeAdvisoryAdapter
from orgrebase.workspace.bounded_execution import BoundedExecutionError
from orgrebase.workspace.change_budget import dispatch_budget
from orgrebase.workspace.change_operations import prepare_pending_changes, run_change_preparation
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_advisory import contracts
from tests.workspace.test_change_budget import budget
from tests.workspace.test_change_proposals import command


def test_supported_local_advisory_serial_fallback_keeps_identical_candidate_results(fixture):
    inputs = contracts(fixture, (("rule:finance", "finance"), ("rule:legal", "legal")))
    serial = WorkspaceChangeAdvisoryAdapter(max_parallel_tasks=1).run(**inputs)
    parallel = WorkspaceChangeAdvisoryAdapter(max_parallel_tasks=2).run(**inputs)
    assert serial == parallel
    assert parallel["candidate_ingestion"].target_writes == 0


def test_supported_local_advisory_stops_successors_after_predecessor_failure(fixture, monkeypatch):
    inputs = contracts(fixture, (("rule:finance", "finance"), ("rule:legal", "legal")))
    adapter = WorkspaceChangeAdvisoryAdapter(max_parallel_tasks=2)
    original = adapter._payload
    observed = []
    lock = threading.Lock()

    def payload(task, *args, **kwargs):
        with lock:
            observed.append(task.authority_domain)
        if task.authority_domain == "finance":
            raise IntegrityError("INJECTED_CANDIDATE_FAILURE")
        return original(task, *args, **kwargs)

    monkeypatch.setattr(adapter, "_payload", payload)
    with pytest.raises(IntegrityError, match="INJECTED_CANDIDATE_FAILURE"):
        adapter.run(**inputs)
    assert "finance" in observed and "gtm" not in observed


@pytest.mark.parametrize("parallelism", [1, 2])
def test_supported_local_advisory_waits_for_owned_tasks_then_rejects_late_round(
    fixture, monkeypatch, parallelism,
):
    inputs = contracts(fixture, (("rule:finance", "finance"), ("rule:legal", "legal")))
    adapter = WorkspaceChangeAdvisoryAdapter(max_parallel_tasks=parallelism, max_elapsed_seconds=0.01)
    original = adapter._payload
    active = 0
    completed = []
    lock = threading.Lock()
    elapsed = [0.0]
    monkeypatch.setattr(advisory_module, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))

    def payload(task, *args, **kwargs):
        nonlocal active
        with lock:
            active += 1
        try:
            time.sleep(0.03)
            return original(task, *args, **kwargs)
        finally:
            with lock:
                active -= 1
                completed.append(task.authority_domain)
                elapsed[0] += 0.03

    monkeypatch.setattr(adapter, "_payload", payload)
    with pytest.raises(AdvisoryGenerationError, match="WORKSPACE_ADVISORY_BUDGET_EXHAUSTED"):
        adapter.run(**inputs)
    assert active == 0 and completed
    assert "gtm" not in completed


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_stop_and_serial_restart_preserve_unknown_and_prepare_only_new_work(
    tmp_path, monkeypatch, postgres_runtime, backend,
):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    options = {"store_path": tmp_path / "fallback.sqlite"}
    if backend == "postgresql":
        tenant_id = runtime.profile.organization_id
        database = postgres_runtime(tenant_id=tenant_id)
        options = {"store_path": database["runtime_dsn"], "store_tenant_id": tenant_id,
                   "store_migrate": False}
    monkeypatch.setenv("ORGREBASE_CHANGE_MAX_PARALLEL_TASKS", "2")
    policy = budget(max_active_attempts=2)
    service = WorkspaceService(**options, runtime_configuration=runtime, review_duration_seconds=0)
    try:
        service.form_quote()
        service._wall_clock_epoch_ms = lambda: 1_800_000_000_000
        assert service.advisory_factory.max_parallel_tasks == 2
        submit_change(service, command(service, event_id="old-unknown"))

        def uncertain(**_kwargs):
            raise BoundedExecutionError("BOUNDED_EXECUTION_CANCELLATION_UNCONFIRMED")

        service.advisory_factory.run = uncertain
        with dispatch_budget(policy), pytest.raises(BoundedExecutionError):
            service.preview_change("old-unknown")
        with service.store.read_connection() as connection:
            before = dict(service.store.execute(connection, select(idempotency_records).where(
                idempotency_records.c.key.startswith("workspace-preview-attempt:")
            )).fetchone())
    finally:
        service.close()

    disabled = tmp_path / "disabled-worker.json"
    disabled.write_text(json.dumps({
        "schema_version": "orgrebase.change-preparation.v1", "workspace_id": "default",
        "access_token_variable": "ORGREBASE_CHANGE_WORKER_TOKEN", "enabled": False,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="CHANGE_PREPARATION_DISABLED"):
        run_change_preparation(disabled)

    monkeypatch.setenv("ORGREBASE_CHANGE_MAX_PARALLEL_TASKS", "1")
    restarted = WorkspaceService(**options, runtime_configuration=runtime, review_duration_seconds=0)
    try:
        restarted._wall_clock_epoch_ms = lambda: 1_800_000_002_000
        assert restarted.advisory_factory.max_parallel_tasks == 1
        with dispatch_budget(policy), pytest.raises(
            IntegrityError, match="WORKSPACE_ADVISORY_INPUT_CHANGED_REQUIRE_NEW_EVENT"
        ):
            restarted.preview_change("old-unknown")
        submit_change(restarted, command(restarted, event_id="new-valid", value="New valid plan"))
        calls = []
        original = restarted.advisory_factory.run

        def observed(**kwargs):
            calls.append(kwargs["change_set"].id)
            return original(**kwargs)

        monkeypatch.setattr(restarted.advisory_factory, "run", observed)
        result = prepare_pending_changes(restarted, max_changes=1, dispatch_policy=policy)
        assert [(item["event_id"], item["result"]) for item in result["records"]] == [
            ("new-valid", "CANDIDATE_PREPARED")
        ]
        assert result["blocked_attempts"]["RESULT_UNKNOWN"] == 1
        assert len(calls) == 1
        with restarted.store.read_connection() as connection:
            after = dict(restarted.store.execute(connection, select(idempotency_records).where(
                idempotency_records.c.key == before["key"]
            )).fetchone())
        assert after == before
        assert restarted.store.connection.execute(
            "SELECT COUNT(*) FROM deployment_budget_reservations"
        ).fetchone()[0] == 2
    finally:
        restarted.close()
