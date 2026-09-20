from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import httpx2 as httpx
import pytest

SPEC = importlib.util.spec_from_file_location(
    "http_capacity_runner", Path(__file__).resolve().parents[1] / "scripts/run_http_capacity.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
GOOD = {"schema_version": "orgrebase.workspace-state.v2", "event_chain": {"status": "PASS"}}


def run(handler, **options):
    return asyncio.run(runner.run_observation(
        target="https://private-capacity.example", token="private-bearer-token", rates=[10],
        duration=.6, concurrency=2, timeout=.25, max_schedule_lag=.1,
        transport=httpx.MockTransport(handler), **options,
    ))


def test_fixed_arrivals_keep_http_failures_invalid_payloads_and_timeouts_in_denominator():
    calls = []

    async def handler(request):
        calls.append(request)
        index = len(calls)
        if index == 1:
            return httpx.Response(200, json=GOOD)
        if index == 2:
            return httpx.Response(503, text="private server error and private-bearer-token")
        if index == 3:
            return httpx.Response(200, json={"status": "healthy"})
        if index == 4:
            raise httpx.ConnectError("https://private-capacity.example private-bearer-token")
        await asyncio.sleep(1)
        return httpx.Response(200, json=GOOD)

    report = run(handler)
    assert report["status"] == "COMPLETE"
    assert report["started_at"] <= report["ended_at"]
    assert len(report["runner_sha256"]) == 64
    level = report["levels"][0]
    assert level["planned"] == 6 == sum(level["outcomes"].values())
    assert level["outcomes"] == {"http_error": 1, "invalid_business_response": 1,
                                 "success": 1, "timeout": 2, "transport_error": 1}
    assert level["sent"] == level["completed"] == 6
    assert level["success_per_planned"] == round(1 / 6, 6)
    assert level["peak_in_flight"] <= 2
    assert all(request.url.path == runner.ENDPOINT for request in calls)
    assert all(request.headers["authorization"] == "Bearer private-bearer-token" for request in calls)
    encoded = json.dumps(report)
    for secret in ("private-capacity", "private-bearer", "private server error"):
        assert secret not in encoded
    assert report["database_backend"] == "NOT_INFERRED_FROM_HTTP"
    assert report["production_ready_claimed"] is report["sla_met_claimed"] is False


def test_overload_drops_unsent_arrivals_without_queueing_or_replaying():
    active = peak = requests = 0

    async def handler(request):
        nonlocal active, peak, requests
        requests += 1
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.04)
            return httpx.Response(200, json=GOOD)
        finally:
            active -= 1

    report = asyncio.run(runner.run_observation(
        target="http://127.0.0.1:12345", token="secret", rates=[200], duration=.08,
        concurrency=1, timeout=.1, max_schedule_lag=.02, transport=httpx.MockTransport(handler)))
    level = report["levels"][0]
    assert level["planned"] == 16 == sum(level["outcomes"].values())
    assert level["outcomes"]["concurrency_drop"] > 0
    assert level["sent"] == requests == level["outcomes"]["success"]
    assert peak == level["peak_in_flight"] == 1
    assert level["elapsed_with_drain_seconds"] < .3


def test_scheduler_delay_drops_missed_arrivals_and_reports_every_slot():
    first = True

    async def handler(request):
        nonlocal first
        if first:
            first = False
            time.sleep(.06)  # Deliberately stall this client scheduler, not the service.
        return httpx.Response(200, json=GOOD)

    report = asyncio.run(runner.run_observation(
        target="http://127.0.0.1", token="secret", rates=[100], duration=.1,
        concurrency=2, timeout=.2, max_schedule_lag=.02, transport=httpx.MockTransport(handler)))
    level = report["levels"][0]
    assert level["planned"] == len(level["samples"]) == 10
    assert level["outcomes"]["scheduler_drop"] >= 4
    assert sum(level["outcomes"].values()) == 10
    assert any(sample["schedule_delay_ms"] >= 30 for sample in level["samples"])


@pytest.mark.parametrize("target", ["http://remote.example", "https://user:secret@example.com",
                                    "https://example.com?token=secret", "https://example.com/path",
                                    "https://example.com#private", "https://example.com:bad"])
def test_target_rejects_credentials_paths_and_unverified_remote_http(target):
    with pytest.raises(ValueError, match="CAPACITY_"):
        runner.validate_target(target)


def test_report_is_exclusive_and_runner_never_prints_environment_secrets(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "report.json"
    destination.write_text("keep existing observation")
    monkeypatch.setenv("ORGREBASE_CAPACITY_BASE_URL", "https://private.example")
    monkeypatch.setenv("ORGREBASE_CAPACITY_ACCESS_TOKEN", "private-token")

    async def observe(**kwargs):
        raise AssertionError("An existing report must refuse the run before sending any request")

    monkeypatch.setattr(runner, "run_observation", observe)
    assert runner.main(["--output", str(destination)]) == 2
    assert destination.read_text() == "keep existing observation"
    assert "private" not in capsys.readouterr().out


def test_interrupted_observation_checkpoints_completed_levels_and_unknown_active_counts():
    calls = 0
    saved = []

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private unreported interruption")
        return httpx.Response(200, json=GOOD)

    with pytest.raises(RuntimeError):
        asyncio.run(runner.run_observation(
            target="http://127.0.0.1", token="secret", rates=[10, 10, 10], duration=.05,
            concurrency=1, timeout=.1, max_schedule_lag=.05,
            transport=httpx.MockTransport(handler), progress=lambda value: saved.append(json.loads(json.dumps(value)))))
    last = saved[-1]
    assert last["status"] == "INCOMPLETE"
    assert len(last["levels"]) == 1 and last["levels"][0]["outcomes"] == {"success": 1}
    assert [item["status"] for item in last["plan"]] == ["COMPLETE", "INCOMPLETE_COUNTS_UNKNOWN", "NOT_STARTED"]
    assert last["plan"][2]["not_sent"] == 1
    assert "not_sent" not in last["plan"][1]
    assert last["ended_at"] is not None
    assert "private unreported" not in json.dumps(saved)


def test_main_reserves_nonempty_not_run_plan_before_configuration_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_CAPACITY_BASE_URL", "http://127.0.0.1")
    monkeypatch.delenv("ORGREBASE_CAPACITY_ACCESS_TOKEN", raising=False)
    destination = tmp_path / "incomplete.json"
    assert runner.main(["--rates", "1", "--output", str(destination)]) == 2
    recorded = json.loads(destination.read_text())
    assert recorded["status"] == "NOT_RUN" and recorded["levels"] == []
    assert recorded["plan"][0]["not_sent"] == recorded["plan"][0]["planned"] == 5
    assert destination.stat().st_mode & 0o777 == 0o600


def test_invalid_unbounded_budgets_are_rejected():
    for arguments in (([float("nan")], 1, 1, 1, .1), ([1], 61, 1, 1, .1),
                      ([500] * 12, 60, 1, 1, .1), ([1], 1, 0, 1, .1), ([1], 1, 1, 31, .1)):
        with pytest.raises(ValueError, match="CAPACITY_BUDGET_INVALID"):
            runner.validate_budget(*arguments)
