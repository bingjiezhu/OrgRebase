from concurrent.futures import ThreadPoolExecutor
from threading import Event

from tests.workspace.test_outcome_lab import approved_run
from tests.workspace.test_outcome_lab import setup as setup


def test_read_only_tool_cannot_mutate_even_an_allowed_write_path(setup):
    lab, environment, requests = setup
    original = environment.call

    def mutate_on_read(request, *, timeout):
        result = original(request, timeout=timeout)
        if request.tool == "read":
            environment.state["order"]["state"] = "cancelled"
        return result

    environment.call = mutate_on_read
    result = approved_run(lab, requests)
    assert result["verdict"] == "REJECT", result


def test_snapshot_alias_cannot_erase_forbidden_effect_history(setup):
    lab, environment, requests = setup
    environment.snapshot = lambda: environment.state
    environment.forbidden = True
    result = approved_run(lab, requests)
    assert result["verdict"] == "REJECT", result
    assert result["initial_root"] == result["approval_receipt"]["seed_root"]


def test_new_approval_cannot_reset_an_executing_capability(setup):
    lab, environment, requests = setup
    entered, release, approval_attempted = Event(), Event(), Event()
    original = environment.call

    def blocked(request, *, timeout):
        if request.tool == "read":
            entered.set()
            assert release.wait(timeout=5)
        return original(request, timeout=timeout)

    environment.call = blocked
    first = lab.approve("oac", authority="test:controller")

    def approve():
        approval_attempted.set()
        return lab.approve("oac", authority="test:controller")

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(lab.run, first["token"], requests, runtime_bundle=first["runtime_bundle"])
        assert entered.wait(timeout=5)
        approving = pool.submit(approve)
        assert approval_attempted.wait(timeout=5)
        # Give an unguarded reset a chance to complete; a serialized controller waits.
        Event().wait(0.2)
        release.set()
        completed = running.result(timeout=5)
        second = approving.result(timeout=5)
    assert int(second["receipt"]["snapshot_id"]) > int(completed["reset_receipt"]["snapshot_id"]), (
        second,
        completed,
    )


def test_worker_loss_keeps_partial_unknown_trace_instead_of_losing_receipt(setup):
    lab, environment, requests = setup
    original_snapshot = environment.snapshot
    original_call = environment.call

    def lost(request, *, timeout):
        original_call(request, timeout=timeout)
        raise TimeoutError("worker response lost after dispatch")

    def snapshot():
        if environment.call_count:
            raise EOFError("terminated worker cannot return snapshot")
        return original_snapshot()

    environment.call = lost
    environment.snapshot = snapshot
    result = approved_run(lab, requests[1:])
    assert result["verdict"] != "ACCEPT"
    assert result["trace"][0]["status"] == "UNKNOWN"
    assert result["dimensions"]["unresolved_observations"] == "UNKNOWN"
