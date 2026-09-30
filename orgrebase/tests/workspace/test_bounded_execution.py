from __future__ import annotations

import threading
import time
from contextlib import closing

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.workspace.advisory import WorkspaceChangeAdvisoryAdapter
from orgrebase.workspace.bounded_execution import BoundedExecutionError, execute_ready_tasks
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_advisory import contracts


def test_ready_executor_overlaps_independent_tasks_and_reduces_in_declared_order():
    lock = threading.Lock()
    active = 0
    maximum = 0
    intervals: dict[str, tuple[float, float]] = {}

    def run(task: tuple[str, float]) -> str:
        nonlocal active, maximum
        identity, delay = task
        started = time.monotonic()
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(delay)
            return identity
        finally:
            ended = time.monotonic()
            with lock:
                active -= 1
                intervals[identity] = (started, ended)

    tasks = (("first", 0.06), ("second", 0.01), ("review", 0.0))
    results = execute_ready_tasks(
        tasks,
        task_id=lambda task: task[0],
        dependencies=lambda task: ("first", "second") if task[0] == "review" else (),
        execute=run,
        max_parallel=2,
    )

    assert results == ("first", "second", "review")
    assert maximum == 2
    assert intervals["first"][0] < intervals["second"][1]
    assert intervals["second"][0] < intervals["first"][1]
    assert intervals["review"][0] >= max(intervals["first"][1], intervals["second"][1])


def test_executor_rejects_invalid_graph_before_running_any_task():
    called = []
    tasks = (("a", ("b",)), ("b", ("a",)))
    with pytest.raises(ValueError, match="BOUNDED_EXECUTION_DEPENDENCY_CYCLE"):
        execute_ready_tasks(
            tasks,
            task_id=lambda task: task[0],
            dependencies=lambda task: task[1],
            execute=lambda task: called.append(task),
            max_parallel=2,
        )
    assert called == []


def test_serial_executor_accepts_a_valid_graph_declared_out_of_topological_order():
    tasks = (("review", ("domain",)), ("domain", ()))
    observed = []
    result = execute_ready_tasks(
        tasks,
        task_id=lambda task: task[0],
        dependencies=lambda task: task[1],
        execute=lambda task: observed.append(task[0]) or task[0],
        max_parallel=1,
    )
    assert observed == ["domain", "review"]
    assert result == ("review", "domain")


def test_deadline_rejects_an_unqualified_cancellation_lane_before_dispatch():
    called = []
    with pytest.raises(ValueError, match="BOUNDED_EXECUTION_CANCELLATION_PROTOCOL_REQUIRED"):
        execute_ready_tasks(
            (("task", ()),),
            task_id=lambda task: task[0],
            dependencies=lambda task: task[1],
            execute=lambda task: called.append(task),
            max_parallel=1,
            deadline_monotonic=time.monotonic() + 1,
        )
    assert called == []


def test_deadline_signals_and_confirms_cooperative_resource_release():
    tasks = (("first", ()), ("second", ()))
    cancelled = {task[0]: threading.Event() for task in tasks}
    stopped = {task[0]: threading.Event() for task in tasks}

    def run(task):
        try:
            while not cancelled[task[0]].wait(0.005):
                pass
        finally:
            stopped[task[0]].set()
        return task[0]

    started = time.monotonic()
    with pytest.raises(BoundedExecutionError, match="BOUNDED_EXECUTION_DEADLINE_EXCEEDED"):
        execute_ready_tasks(
            tasks,
            task_id=lambda task: task[0],
            dependencies=lambda task: task[1],
            execute=run,
            max_parallel=2,
            deadline_monotonic=time.monotonic() + 0.03,
            cancel=lambda task: cancelled[task[0]].set(),
            cancellation_confirmed=lambda task: stopped[task[0]].is_set(),
            cancellation_grace_seconds=0.2,
        )
    assert time.monotonic() - started < 0.5
    assert all(event.is_set() for event in stopped.values())


def test_unconfirmed_cancellation_fails_qualification_within_bounded_grace():
    release = threading.Event()
    stopped = threading.Event()

    def run(_task):
        try:
            release.wait(0.5)
        finally:
            stopped.set()
        return "late"

    started = time.monotonic()
    try:
        with pytest.raises(
            BoundedExecutionError,
            match="BOUNDED_EXECUTION_CANCELLATION_UNCONFIRMED",
        ):
            execute_ready_tasks(
                (("task", ()),),
                task_id=lambda task: task[0],
                dependencies=lambda task: task[1],
                execute=run,
                max_parallel=1,
                deadline_monotonic=time.monotonic() + 0.01,
                cancel=lambda _task: None,
                cancellation_confirmed=lambda _task: False,
                cancellation_grace_seconds=0.02,
            )
        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        assert stopped.wait(0.5)


def test_stale_result_is_rejected_before_declared_order_reduction():
    stopped = threading.Event()

    def run(task):
        stopped.set()
        return task[0]

    with pytest.raises(BoundedExecutionError, match="BOUNDED_EXECUTION_STALE_RESULT"):
        execute_ready_tasks(
            (("task", ()),),
            task_id=lambda task: task[0],
            dependencies=lambda task: task[1],
            execute=run,
            max_parallel=2,
            result_is_current=lambda _task, _result: False,
            cancel=lambda _task: None,
            cancellation_confirmed=lambda _task: stopped.is_set(),
        )


def test_workspace_formation_uses_configured_bounded_parallelism(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_FORMATION_MAX_PARALLEL_TASKS", "2")
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with closing(
        WorkspaceService(
            store_path=tmp_path / "parallel.sqlite",
            runtime_configuration=runtime,
            review_duration_seconds=0,
        )
    ) as workspace:
        lock = threading.Lock()
        active = 0
        maximum = 0
        for provider in workspace.formation.domain_registry._providers.values():
            original = provider.produce

            def observed(*args, _original=original, **kwargs):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    time.sleep(0.03)
                    return _original(*args, **kwargs)
                finally:
                    with lock:
                        active -= 1

            monkeypatch.setattr(provider, "produce", observed)

        receipt = workspace.form_quote()

        assert receipt.deliverable_ref == workspace.current_quote().ref
        assert maximum == 2


def test_local_change_advisory_overlaps_only_ready_domain_tasks(fixture, monkeypatch):
    inputs = contracts(
        fixture,
        (("rule:finance", "finance"), ("rule:legal", "legal")),
    )
    adapter = WorkspaceChangeAdvisoryAdapter(max_parallel_tasks=2)
    original = adapter._payload
    lock = threading.Lock()
    intervals: dict[str, tuple[float, float]] = {}

    def observed(task, *args, **kwargs):
        started = time.monotonic()
        if task.authority_domain in {"finance", "legal"}:
            time.sleep(0.04)
        result = original(task, *args, **kwargs)
        with lock:
            intervals[task.authority_domain] = (started, time.monotonic())
        return result

    monkeypatch.setattr(adapter, "_payload", observed)
    result = adapter.run(**inputs)

    assert intervals["finance"][0] < intervals["legal"][1]
    assert intervals["legal"][0] < intervals["finance"][1]
    assert intervals["gtm"][0] >= max(intervals["finance"][1], intervals["legal"][1])
    assert [item.task_id for item in result["handoffs"]] == [
        task.id for task in result["orchestration_plan"].tasks
    ]
    assert result["candidate_ingestion"].target_writes == 0


@pytest.mark.parametrize("value", ["0", "5", "invalid"])
def test_workspace_rejects_invalid_formation_parallelism(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ORGREBASE_FORMATION_MAX_PARALLEL_TASKS", value)
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with pytest.raises(ValueError, match="FORMATION_PARALLELISM_INVALID"):
        WorkspaceService(
            store_path=tmp_path / f"invalid-{value}.sqlite",
            runtime_configuration=runtime,
        )


@pytest.mark.parametrize("value", ["0", "5", "invalid"])
def test_workspace_rejects_invalid_advisory_parallelism(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ORGREBASE_CHANGE_MAX_PARALLEL_TASKS", value)
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with pytest.raises(ValueError, match="WORKSPACE_ADVISORY_PARALLELISM_INVALID"):
        WorkspaceService(
            store_path=tmp_path / f"invalid-advisory-{value}.sqlite",
            runtime_configuration=runtime,
        )


def test_model_provider_parallelism_requires_a_separately_qualified_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_CHANGE_MAX_PARALLEL_TASKS", "2")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "openai-responses")
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with pytest.raises(ValueError, match="WORKSPACE_ADVISORY_PARALLEL_PROVIDER_UNQUALIFIED"):
        WorkspaceService(
            store_path=tmp_path / "unqualified-provider-parallel.sqlite",
            runtime_configuration=runtime,
        )
