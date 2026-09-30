"""Small dependency-aware executor for already-admitted candidate tasks."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait


class BoundedExecutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def execute_ready_tasks[T, R](
    tasks: Sequence[T],
    *,
    task_id: Callable[[T], str],
    dependencies: Callable[[T], Sequence[str]],
    execute: Callable[[T], R],
    max_parallel: int,
    deadline_monotonic: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    cancel: Callable[[T], None] | None = None,
    cancellation_confirmed: Callable[[T], bool] | None = None,
    cancellation_grace_seconds: float = 1.0,
    result_is_current: Callable[[T, R], bool] | None = None,
) -> tuple[R, ...]:
    """Run ready tasks concurrently and return results in declared task order.

    This utility owns only worker futures.  Database transactions, native
    sessions, candidate admission, and deterministic result reduction remain in
    the caller.  The immutable task list is validated before any task starts.

    A deadline is accepted only for a cooperatively cancellable worker lane. The
    caller must signal cancellation and confirm that each running worker released
    its resources inside ``cancellation_grace_seconds``. Merely discarding a
    future never qualifies as cancellation. A current-result callback can bind a
    result to an external fence immediately before reduction.
    """

    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or not 1 <= max_parallel <= 32:
        raise ValueError("BOUNDED_EXECUTION_PARALLELISM_INVALID")
    if deadline_monotonic is not None and not math.isfinite(deadline_monotonic):
        raise ValueError("BOUNDED_EXECUTION_DEADLINE_INVALID")
    if (
        not math.isfinite(cancellation_grace_seconds)
        or not 0 < cancellation_grace_seconds <= 30
    ):
        raise ValueError("BOUNDED_EXECUTION_CANCELLATION_GRACE_INVALID")
    identities = tuple(task_id(task) for task in tasks)
    if any(not isinstance(identity, str) or not identity for identity in identities):
        raise ValueError("BOUNDED_EXECUTION_TASK_ID_INVALID")
    if len(identities) != len(set(identities)):
        raise ValueError("BOUNDED_EXECUTION_DUPLICATE_TASK")
    threaded = max_parallel > 1 or deadline_monotonic is not None
    if threaded and (deadline_monotonic is not None or result_is_current is not None) and (
        cancel is None or cancellation_confirmed is None
    ):
        raise ValueError("BOUNDED_EXECUTION_CANCELLATION_PROTOCOL_REQUIRED")
    task_by_id = dict(zip(identities, tasks, strict=True))
    dependency_map: dict[str, tuple[str, ...]] = {}
    for identity, task in zip(identities, tasks, strict=True):
        refs = tuple(dependencies(task))
        if (
            len(refs) != len(set(refs))
            or identity in refs
            or any(ref not in task_by_id for ref in refs)
        ):
            raise ValueError("BOUNDED_EXECUTION_DEPENDENCY_INVALID")
        dependency_map[identity] = refs
    # Reject a cycle before any worker can produce a side effect or observation.
    remaining = {identity: set(refs) for identity, refs in dependency_map.items()}
    resolved: set[str] = set()
    while remaining:
        ready = {identity for identity, refs in remaining.items() if refs <= resolved}
        if not ready:
            raise ValueError("BOUNDED_EXECUTION_DEPENDENCY_CYCLE")
        resolved.update(ready)
        for identity in ready:
            remaining.pop(identity)

    if not threaded:
        results: dict[str, R] = {}
        pending = set(identities)
        while pending:
            if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
                raise BoundedExecutionError("BOUNDED_EXECUTION_DEADLINE_EXCEEDED")
            ready = next(
                (
                    identity
                    for identity in identities
                    if identity in pending and set(dependency_map[identity]) <= set(results)
                ),
                None,
            )
            if ready is None:  # pragma: no cover - graph prevalidated
                raise BoundedExecutionError("BOUNDED_EXECUTION_STALLED")
            result = execute(task_by_id[ready])
            if result_is_current is not None and not result_is_current(task_by_id[ready], result):
                raise BoundedExecutionError("BOUNDED_EXECUTION_STALE_RESULT")
            results[ready] = result
            pending.remove(ready)
        return tuple(results[identity] for identity in identities)

    results: dict[str, R] = {}
    submitted: set[str] = set()
    running: dict[Future[R], str] = {}
    completed: set[str] = set()
    pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="orgrebase-candidate")
    safe_shutdown = False

    def cancel_and_confirm() -> None:
        nonlocal safe_shutdown
        assert cancel is not None and cancellation_confirmed is not None
        cancellation_failed = False
        for future, identity in tuple(running.items()):
            future.cancel()
            try:
                cancel(task_by_id[identity])
            except BaseException:
                cancellation_failed = True
        grace_deadline = time.monotonic() + cancellation_grace_seconds
        while not cancellation_failed:
            pending = []
            for future, identity in running.items():
                if future.cancelled():
                    continue
                try:
                    released = future.done() and cancellation_confirmed(task_by_id[identity])
                except BaseException:
                    cancellation_failed = True
                    break
                if not released:
                    pending.append((future, identity))
            if not pending:
                if not cancellation_failed:
                    safe_shutdown = True
                    return
                break
            remaining_grace = grace_deadline - time.monotonic()
            if remaining_grace <= 0:
                break
            wait(
                tuple(future for future, _identity in pending),
                timeout=min(remaining_grace, 0.01),
                return_when=FIRST_COMPLETED,
            )
        raise BoundedExecutionError("BOUNDED_EXECUTION_CANCELLATION_UNCONFIRMED")

    try:
        while len(completed) < len(identities):
            if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
                cancel_and_confirm()
                raise BoundedExecutionError("BOUNDED_EXECUTION_DEADLINE_EXCEEDED")
            capacity = max_parallel - len(running)
            ready = [
                identity
                for identity in identities
                if identity not in submitted and set(dependency_map[identity]) <= completed
            ]
            for identity in ready[:capacity]:
                future = pool.submit(execute, task_by_id[identity])
                submitted.add(identity)
                running[future] = identity
            if not running:
                raise BoundedExecutionError("BOUNDED_EXECUTION_STALLED")
            timeout = None
            if deadline_monotonic is not None:
                timeout = max(0.0, deadline_monotonic - monotonic())
            done, _ = wait(tuple(running), timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                cancel_and_confirm()
                raise BoundedExecutionError("BOUNDED_EXECUTION_DEADLINE_EXCEEDED")
            for future in done:
                identity = running[future]
                try:
                    result = future.result()
                except BaseException:
                    if deadline_monotonic is not None or result_is_current is not None:
                        cancel_and_confirm()
                    else:
                        for pending in running:
                            pending.cancel()
                        safe_shutdown = True
                    raise
                if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
                    cancel_and_confirm()
                    raise BoundedExecutionError("BOUNDED_EXECUTION_LATE_RESULT")
                if result_is_current is not None and not result_is_current(task_by_id[identity], result):
                    cancel_and_confirm()
                    raise BoundedExecutionError("BOUNDED_EXECUTION_STALE_RESULT")
                running.pop(future)
                results[identity] = result
                completed.add(identity)
        safe_shutdown = True
        return tuple(results[identity] for identity in identities)
    finally:
        pool.shutdown(wait=safe_shutdown, cancel_futures=True)
