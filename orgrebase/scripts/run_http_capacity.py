#!/usr/bin/env python3
"""Bounded, fixed-arrival business HTTP observations; no production SLO claim."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import ssl
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx

ENDPOINT = "/api/workspace/state"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def validate_target(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("CAPACITY_BASE_URL_INVALID") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("CAPACITY_BASE_URL_INVALID")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("CAPACITY_REMOTE_HTTPS_REQUIRED")
    return value.rstrip("/")


def validate_budget(rates: list[float], duration: float, concurrency: int, timeout: float, lag: float) -> None:
    if (not 1 <= len(rates) <= 12 or any(not math.isfinite(rate) or not 0 < rate <= 500 for rate in rates)
            or not math.isfinite(duration) or not 0 < duration <= 60 or len(rates) * duration > 600
            or not 1 <= concurrency <= 256 or not math.isfinite(timeout) or not 0 < timeout <= 30
            or not math.isfinite(lag) or not 0 <= lag <= 5
            or sum(math.ceil(rate * duration) for rate in rates) > 20_000):
        raise ValueError("CAPACITY_BUDGET_INVALID")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 3)


async def observe_request(client, *, target: str, token: str, timeout: float, origin: float,
                          arrival: float, index: int) -> dict:
    started = time.perf_counter()
    record = {"index": index, "arrival_offset_ms": round((arrival - origin) * 1000, 3),
              "start_offset_ms": round((started - origin) * 1000, 3),
              "schedule_delay_ms": round(max(0, started - arrival) * 1000, 3),
              "outcome": "transport_error", "http_status": None, "response_bytes": 0}
    try:
        async with asyncio.timeout(timeout):
            async with client.stream("GET", target + ENDPOINT,
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/json"}) as response:
                record["http_status"] = response.status_code
                parts = []
                async for part in response.aiter_bytes():
                    record["response_bytes"] += len(part)
                    if record["response_bytes"] > MAX_RESPONSE_BYTES:
                        record["outcome"] = "response_too_large"
                        break
                    parts.append(part)
                else:
                    if response.status_code != 200:
                        record["outcome"] = "http_error"
                    else:
                        try:
                            payload = json.loads(b"".join(parts))
                            valid = (isinstance(payload, dict)
                                     and payload.get("schema_version") == "orgrebase.workspace-state.v2"
                                     and payload.get("event_chain", {}).get("status") == "PASS")
                        except (ValueError, TypeError, AttributeError):
                            valid = False
                        record["outcome"] = "success" if valid else "invalid_business_response"
    except (TimeoutError, httpx.TimeoutException):
        record["outcome"] = "timeout"
    except (httpx.HTTPError, OSError):
        record["outcome"] = "transport_error"
    ended = time.perf_counter()
    record["request_latency_ms"] = round((ended - started) * 1000, 3)
    record["arrival_to_completion_ms"] = round((ended - arrival) * 1000, 3)
    return record


async def observe_level(client, *, target: str, token: str, rate: float, duration: float,
                        concurrency: int, timeout: float, max_schedule_lag: float) -> dict:
    origin = time.perf_counter()
    pending = set()
    records = []
    peak = 0
    planned = math.ceil(rate * duration)
    for index in range(planned):
        arrival = origin + index / rate
        await asyncio.sleep(max(0, arrival - time.perf_counter()))
        completed = {task for task in pending if task.done()}
        records.extend(task.result() for task in completed)
        pending.difference_update(completed)
        now = time.perf_counter()
        delay = max(0, now - arrival)
        outcome = "scheduler_drop" if delay > min(max_schedule_lag, 1 / rate) else "concurrency_drop" if len(pending) >= concurrency else None
        if outcome:
            records.append({"index": index, "arrival_offset_ms": round((arrival - origin) * 1000, 3),
                            "schedule_delay_ms": round(delay * 1000, 3), "outcome": outcome,
                            "http_status": None, "response_bytes": 0})
            continue
        pending.add(asyncio.create_task(observe_request(
            client, target=target, token=token, timeout=timeout, origin=origin, arrival=arrival, index=index)))
        peak = max(peak, len(pending))
    await asyncio.sleep(max(0, origin + duration - time.perf_counter()))
    if pending:
        records.extend(await asyncio.gather(*pending))
    elapsed = max(duration, time.perf_counter() - origin)
    records.sort(key=lambda item: item["index"])
    counts = Counter(item["outcome"] for item in records)
    sent = [item for item in records if "start_offset_ms" in item]
    latencies = [item["arrival_to_completion_ms"] for item in sent]
    assert len(records) == planned
    return {"offered_requests_per_second": rate, "arrival_window_seconds": duration,
            "effective_schedule_lag_limit_seconds": min(max_schedule_lag, 1 / rate),
            "elapsed_with_drain_seconds": round(elapsed, 6), "planned": planned, "sent": len(sent),
            "completed": len(sent), "outcomes": dict(sorted(counts.items())), "peak_in_flight": peak,
            "success_per_planned": round(counts["success"] / planned, 6),
            "observed_successes_per_second": round(counts["success"] / elapsed, 6),
            "http_status_counts": dict(sorted(Counter(str(item["http_status"]) for item in sent).items())),
            "arrival_latency_p50_ms": percentile(latencies, .50),
            "arrival_latency_p95_ms": percentile(latencies, .95),
            "arrival_latency_p99_ms": percentile(latencies, .99),
            "schedule_delay_p95_ms": percentile([item["schedule_delay_ms"] for item in records], .95),
            "samples": records}


async def run_observation(*, target: str, token: str, rates: list[float], duration: float,
                          concurrency: int, timeout: float, max_schedule_lag: float,
                          verify: ssl.SSLContext | bool = True, transport=None,
                          cooldown: float = 0, progress=None) -> dict:
    target = validate_target(target)
    validate_budget(rates, duration, concurrency, timeout, max_schedule_lag)
    if not token or "\n" in token or "\r" in token:
        raise ValueError("CAPACITY_ACCESS_TOKEN_REQUIRED")
    if not math.isfinite(cooldown) or not 0 <= cooldown <= 30:
        raise ValueError("CAPACITY_COOLDOWN_INVALID")
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    report = {"schema_version": "orgrebase.http-capacity-observation.v1", "endpoint": ENDPOINT,
            "status": "INCOMPLETE", "started_at": started_at, "ended_at": None,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "claim_boundary": "CONTROLLED_MEASUREMENT_NOT_PRODUCTION_CAPACITY_OR_SLO_CERTIFICATION",
            "production_ready_claimed": False, "sla_met_claimed": False,
            "authentication": "BEARER_REQUIRED_SERVER_VERIFICATION_NOT_INFERRED_BY_RUNNER",
            "database_backend": "NOT_INFERRED_FROM_HTTP", "arrival_model": "FIXED_RATE_NO_RETRY_NO_CATCHUP_BURST",
            "client_environment": {"python": platform.python_version(), "system": platform.system(),
                                   "machine": platform.machine()},
            "limits": {"max_in_flight": concurrency, "request_total_timeout_seconds": timeout,
                       "max_schedule_lag_seconds": max_schedule_lag, "max_response_bytes": MAX_RESPONSE_BYTES,
                       "cooldown_between_levels_seconds": cooldown},
            "plan": [{"rate": rate, "planned": math.ceil(rate * duration), "status": "NOT_STARTED"} for rate in rates],
            "levels": []}

    def checkpoint():
        report["measurement_seconds"] = round(time.perf_counter() - started, 6)
        if progress:
            progress(report)

    try:
        checkpoint()
        async with httpx.AsyncClient(verify=verify, trust_env=False, follow_redirects=False, timeout=timeout,
                                    transport=transport, limits=httpx.Limits(
                                        max_connections=concurrency, max_keepalive_connections=concurrency)) as client:
            for index, rate in enumerate(rates):
                report["plan"][index]["status"] = "IN_PROGRESS"
                checkpoint()
                level = await observe_level(client, target=target, token=token, rate=rate, duration=duration,
                                            concurrency=concurrency, timeout=timeout, max_schedule_lag=max_schedule_lag)
                report["levels"].append(level)
                report["plan"][index]["status"] = "COMPLETE"
                checkpoint()
                if index + 1 < len(rates):
                    await asyncio.sleep(cooldown)
        report["status"] = "COMPLETE"
    finally:
        for item in report["plan"]:
            if item["status"] == "IN_PROGRESS":
                item["status"] = "INCOMPLETE_COUNTS_UNKNOWN"
            elif item["status"] == "NOT_STARTED":
                item["not_sent"] = item["planned"]
        report["ended_at"] = datetime.now(UTC).isoformat()
        checkpoint()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rates", default="1,2,4,8,16,32")
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--max-in-flight", type=int, default=8)
    parser.add_argument("--request-timeout", type=float, default=5)
    parser.add_argument("--max-schedule-lag", type=float, default=.25)
    parser.add_argument("--cooldown-seconds", type=float, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        rates = [float(value) for value in args.rates.split(",")]
        target = validate_target(os.environ.get("ORGREBASE_CAPACITY_BASE_URL", ""))
        validate_budget(rates, args.seconds, args.max_in_flight, args.request_timeout, args.max_schedule_lag)
        ca = os.environ.get("ORGREBASE_CAPACITY_CA_BUNDLE")
        verify = ssl.create_default_context(cafile=ca) if ca else True
        # Reserve a real plan before any HTTP request; never overwrite a previous run.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            def checkpoint(value):
                output.seek(0)
                json.dump(value, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.truncate()
                output.flush()
                os.fsync(output.fileno())

            checkpoint({"schema_version": "orgrebase.http-capacity-observation.v1", "status": "NOT_RUN",
                        "created_at": datetime.now(UTC).isoformat(), "endpoint": ENDPOINT,
                        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        "plan": [{"rate": rate, "planned": math.ceil(rate * args.seconds), "status": "NOT_STARTED",
                                  "not_sent": math.ceil(rate * args.seconds)} for rate in rates], "levels": []})
            report = asyncio.run(run_observation(
                target=target, token=os.environ.get("ORGREBASE_CAPACITY_ACCESS_TOKEN", ""), rates=rates,
                duration=args.seconds, concurrency=args.max_in_flight, timeout=args.request_timeout,
                max_schedule_lag=args.max_schedule_lag, verify=verify, cooldown=args.cooldown_seconds,
                progress=checkpoint))
    except KeyboardInterrupt:
        print("CAPACITY_RUN_INTERRUPTED: completed levels remain in the incomplete report")
        return 130
    except (ValueError, OSError, RuntimeError):
        print("CAPACITY_RUN_FAILED: check configuration, credentials, CA and a new output path")
        return 2
    for level in report["levels"]:
        print(json.dumps({key: value for key, value in level.items() if key != "samples"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
