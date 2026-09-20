#!/usr/bin/env python3
"""Stage, run, or collect four prepared Core Worker tasks through OpenClaw.

This helper never reads credentials and never writes canonical OrgRebase state.  It
invokes each already-bound Worker pod directly, so its evidence proves independent
provider execution but not Matrix-inbound delegation; Matrix events are published and
collected by the separate identity-bound publication/evidence scripts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

BUNDLE_SCHEMA = "orgrebase.agentteams-task-bundle.v1"
RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
CANDIDATE_SCHEMA = "orgrebase.candidate-result.v1"
SPECIALISTS = (
    "product-steward",
    "legal-steward",
    "gtm-steward",
    "skill-curator",
)
REQUIRED_PROHIBITIONS = {
    "no_canonical_write",
    "no_approval",
    "no_apply",
    "no_cross_domain_authority",
}
PRIMARY_OUTPUT_KIND = {
    "product-steward": "ClaimDeltaCandidate",
    "legal-steward": "MinimalClaimCandidate",
    "gtm-steward": "ImpactCandidate",
    "skill-curator": "SkillPatchCandidate",
}
REQUIRED_CANDIDATE_KEYS = {
    "candidate_only",
    "candidate_schema_digest",
    "delegation_task_digest",
    "input_refs",
    "nonce",
    "orchestration_plan_digest",
    "output",
    "prohibited_actions_respected",
    "run_envelope_digest",
    "run_id",
    "schema_version",
    "task_projection_digest",
    "tool_receipt_refs",
    "uncertainty",
    "worker_name",
}
SAFE_ATTEMPT = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")
SESSION_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://orgrebase.dev/agentteams/openclaw-session"
)
FAILED_STATUSES = {
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
    "timed_out",
    "timeout",
}


class DispatchError(RuntimeError):
    """Prepared bytes, runtime identity, or Worker output failed closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _value_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _bytes_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DispatchError(f"expected JSON object: {path}")
    return value


def _safe_bundle_file(bundle: Path, relative: str) -> Path:
    path = (bundle / relative).resolve()
    if not path.is_relative_to(bundle.resolve()) or not path.is_file() or path.is_symlink():
        raise DispatchError(f"unsafe or missing prepared file: {relative}")
    return path


def _run(
    command: list[str], *, timeout: int = 30, allow_failure: bool = False
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode and not allow_failure:
        # Command output can contain provider diagnostics.  Keep it out of the
        # terminal/error channel; direct OpenClaw stdout is written only to the
        # private per-attempt artifact below.
        raise DispatchError(
            f"{command[0]} command failed with exit code {result.returncode}"
        )
    return result


def _selected_workers(requested: list[str] | None) -> tuple[str, ...]:
    if requested is None:
        return SPECIALISTS
    if len(requested) != len(set(requested)):
        raise DispatchError("--worker values must be unique")
    return tuple(requested)


def _session_id(*, run_id: str, worker_name: str, attempt: str) -> str:
    """Return an OpenClaw-safe UUID bound to this exact logical attempt."""

    name = json.dumps(
        {
            "attempt": attempt,
            "run_id": run_id,
            "worker_name": worker_name,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(SESSION_NAMESPACE, name))


def _openclaw_failure_code(stdout: str) -> str | None:
    """Classify a machine-readable OpenClaw response without echoing its text."""

    try:
        response = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return "INVALID_OPENCLAW_JSON"
    if not isinstance(response, dict):
        return "INVALID_OPENCLAW_RESPONSE"
    result = response.get("result")
    if not isinstance(result, dict):
        return "MISSING_OPENCLAW_RESULT"
    for status in (response.get("status"), result.get("status")):
        if isinstance(status, str) and status.casefold() in FAILED_STATUSES:
            return "OPENCLAW_FAILED_STATUS"
    if response.get("error") or result.get("error"):
        return "OPENCLAW_ERROR_FIELD"
    payloads = result.get("payloads")
    if not isinstance(payloads, list) or not payloads:
        return "MISSING_OPENCLAW_PAYLOAD"
    saw_text = False
    for payload in payloads:
        if not isinstance(payload, dict):
            return "INVALID_OPENCLAW_PAYLOAD"
        if payload.get("isError") is True or payload.get("error"):
            return "OPENCLAW_ERROR_PAYLOAD"
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        saw_text = True
        normalized = " ".join(text.casefold().split())
        if "network error" in normalized:
            return "OPENCLAW_NETWORK_ERROR"
        if "no response generated" in normalized or "no reply from agent" in normalized:
            return "OPENCLAW_NO_RESPONSE"
        if "agent failed before reply" in normalized:
            return "OPENCLAW_AGENT_FAILED"
    if not saw_text:
        return "MISSING_OPENCLAW_TEXT"
    return None


def _kubectl_prefix(context: str, namespace: str) -> list[str]:
    return ["kubectl", "--context", context, "--namespace", namespace]


def _validated_bundle(bundle: Path, now_ms: int) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]
]:
    manifest = _object(_safe_bundle_file(bundle, "manifest.json"))
    envelope = _object(_safe_bundle_file(bundle, "run-envelope.json"))
    if manifest.get("schema_version") != BUNDLE_SCHEMA:
        raise DispatchError("prepared bundle schema mismatch")
    if envelope.get("schema_version") != RUN_SCHEMA:
        raise DispatchError("run envelope schema mismatch")
    manifest_payload = {key: value for key, value in manifest.items() if key != "digest"}
    if manifest.get("digest") != _value_digest(manifest_payload):
        raise DispatchError("prepared bundle manifest digest mismatch")
    if (
        manifest.get("run_id") != envelope.get("run_id")
        or manifest.get("nonce") != envelope.get("nonce")
        or manifest.get("source_commitments", {}).get("run_envelope_digest")
        != _value_digest(envelope)
    ):
        raise DispatchError("prepared bundle does not bind its run envelope")
    issued = envelope.get("issued_at_ms")
    expires = envelope.get("expires_at_ms")
    if not isinstance(issued, int) or not isinstance(expires, int) or not issued <= now_ms <= expires:
        raise DispatchError("fresh run envelope is not currently valid")
    schema_path = _safe_bundle_file(bundle, "candidate-result.schema.json")
    if (
        _bytes_digest(schema_path.read_bytes())
        != manifest.get("source_commitments", {}).get("candidate_schema_digest")
    ):
        raise DispatchError("candidate Schema bytes drifted")

    worker_entries = manifest.get("workers")
    if not isinstance(worker_entries, list):
        raise DispatchError("prepared manifest has no Worker entries")
    entries = {
        item.get("worker_name"): item
        for item in worker_entries
        if isinstance(item, dict) and isinstance(item.get("worker_name"), str)
    }
    if tuple(entries) != SPECIALISTS or len(entries) != len(worker_entries):
        raise DispatchError("prepared manifest does not bind the ordered four specialists")
    for worker_name, entry in entries.items():
        projection_path = _safe_bundle_file(bundle, str(entry.get("projection_path", "")))
        prompt_path = _safe_bundle_file(bundle, str(entry.get("prompt_path", "")))
        projection = _object(projection_path)
        projection_payload = {
            key: value for key, value in projection.items() if key != "digest"
        }
        if (
            projection.get("worker_name") != worker_name
            or projection.get("run_id") != envelope.get("run_id")
            or projection.get("nonce") != envelope.get("nonce")
            or projection.get("digest") != _value_digest(projection_payload)
            or projection.get("digest") != entry.get("projection_digest")
            or _bytes_digest(projection_path.read_bytes())
            != entry.get("projection_artifact_digest")
            or _bytes_digest(prompt_path.read_bytes()) != entry.get("prompt_digest")
        ):
            raise DispatchError(f"prepared Worker bytes drifted: {worker_name}")
        bindings = projection.get("bindings", {})
        if (
            bindings.get("delegation_task_digest") != entry.get("delegation_task_digest")
            or bindings.get("input_refs") != entry.get("input_refs")
        ):
            raise DispatchError(f"prepared Worker binding drifted: {worker_name}")
    return manifest, envelope, entries


def _runtime_pods(
    envelope: dict[str, Any], workers: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    resources = {
        item.get("worker_name"): item
        for item in envelope.get("workers", [])
        if isinstance(item, dict)
    }
    pods_by_resource = {
        item.get("worker_resource_name"): item
        for item in envelope.get("runtime_pods", [])
        if isinstance(item, dict)
    }
    result: dict[str, dict[str, Any]] = {}
    for worker_name in workers:
        resource = resources.get(worker_name)
        pod = pods_by_resource.get(resource.get("resource_name") if resource else None)
        if not isinstance(pod, dict):
            raise DispatchError(f"run envelope has no runtime Pod: {worker_name}")
        result[worker_name] = pod
    return result


def _verify_live_pod(
    *, context: str, namespace: str, expected: dict[str, Any]
) -> None:
    raw = _run(
        [
            *_kubectl_prefix(context, namespace),
            "get",
            "pod",
            str(expected["name"]),
            "-o",
            "json",
        ]
    ).stdout
    try:
        pod = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DispatchError("kubectl returned invalid Pod JSON") from exc
    statuses = [
        item
        for item in pod.get("status", {}).get("containerStatuses", [])
        if item.get("name") == "worker"
    ]
    ready = any(item.get("ready") is True for item in statuses)
    image_ids = {item.get("imageID") for item in statuses}
    if (
        pod.get("metadata", {}).get("uid") != expected.get("uid")
        or not ready
        or expected.get("image_id") not in image_ids
    ):
        raise DispatchError(f"runtime Pod identity/readiness drifted: {expected.get('name')}")


def _remote_digest(
    *, context: str, namespace: str, pod: str, path: str
) -> str:
    result = _run(
        [
            *_kubectl_prefix(context, namespace),
            "exec",
            pod,
            "-c",
            "worker",
            "--",
            "sha256sum",
            path,
        ]
    )
    value = result.stdout.split(maxsplit=1)[0]
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise DispatchError(f"invalid remote digest: {pod}")
    return f"sha256:{value}"


def stage(
    *,
    bundle: Path,
    entries: dict[str, dict[str, Any]],
    envelope: dict[str, Any],
    context: str,
    namespace: str,
    workers: tuple[str, ...] = SPECIALISTS,
) -> None:
    pods = _runtime_pods(envelope, workers)
    schema_path = _safe_bundle_file(bundle, "candidate-result.schema.json")
    run_path = _safe_bundle_file(bundle, "run-envelope.json")
    for worker_name in workers:
        entry = entries[worker_name]
        expected_pod = pods[worker_name]
        _verify_live_pod(context=context, namespace=namespace, expected=expected_pod)
        projection_path = _safe_bundle_file(bundle, entry["projection_path"])
        prompt_path = _safe_bundle_file(bundle, entry["prompt_path"])
        projection = _object(projection_path)
        runtime = projection["runtime_paths"]
        runtime_dir = str(PurePosixPath(runtime["projection"]).parent)
        prefix = _kubectl_prefix(context, namespace)
        pod = str(expected_pod["name"])
        _run([*prefix, "exec", pod, "-c", "worker", "--", "mkdir", "-p", runtime_dir])
        exists = _run(
            [*prefix, "exec", pod, "-c", "worker", "--", "test", "-e", runtime["result"]],
            allow_failure=True,
        )
        if exists.returncode == 0:
            raise DispatchError(f"refusing to reuse existing remote result: {worker_name}")
        copies = (
            (projection_path, runtime["projection"]),
            (prompt_path, runtime["prompt"]),
            (schema_path, runtime["candidate_schema"]),
            (run_path, runtime["run_envelope"]),
        )
        for local, remote in copies:
            _run(
                [
                    *prefix,
                    "cp",
                    str(local),
                    f"{pod}:{remote}",
                    "-c",
                    "worker",
                ],
                timeout=60,
            )
            if _remote_digest(
                context=context, namespace=namespace, pod=pod, path=remote
            ) != _bytes_digest(local.read_bytes()):
                raise DispatchError(f"staged bytes differ: {worker_name}/{local.name}")


def _execute_one(
    *,
    bundle: Path,
    entry: dict[str, Any],
    pod: dict[str, Any],
    context: str,
    namespace: str,
    attempt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    worker_name = entry["worker_name"]
    projection_path = _safe_bundle_file(bundle, entry["projection_path"])
    prompt_path = _safe_bundle_file(bundle, entry["prompt_path"])
    schema_path = _safe_bundle_file(bundle, "candidate-result.schema.json")
    run_path = _safe_bundle_file(bundle, "run-envelope.json")
    projection = _object(projection_path)
    runtime = projection["runtime_paths"]
    prompt = prompt_path.read_text(encoding="utf-8")
    for local, remote in (
        (projection_path, runtime["projection"]),
        (prompt_path, runtime["prompt"]),
        (schema_path, runtime["candidate_schema"]),
        (run_path, runtime["run_envelope"]),
    ):
        if _remote_digest(
            context=context,
            namespace=namespace,
            pod=str(pod["name"]),
            path=remote,
        ) != _bytes_digest(local.read_bytes()):
            raise DispatchError(f"remote prepared bytes drifted: {worker_name}/{local.name}")
    prefix = _kubectl_prefix(context, namespace)
    remote_result = str(runtime["result"])
    preexisting_result = _run(
        [
            *prefix,
            "exec",
            str(pod["name"]),
            "-c",
            "worker",
            "--",
            "test",
            "-e",
            remote_result,
        ],
        allow_failure=True,
    )
    if preexisting_result.returncode == 0:
        raise DispatchError(f"refusing to reuse existing remote result: {worker_name}")
    session_id = _session_id(
        run_id=str(projection["run_id"]),
        worker_name=str(worker_name),
        attempt=attempt,
    )
    started_ms = int(time.time() * 1000)
    result = _run(
        [
            *prefix,
            "exec",
            str(pod["name"]),
            "-c",
            "worker",
            "--",
            "openclaw",
            "agent",
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            "high",
            "--timeout",
            str(timeout_seconds),
            "--json",
        ],
        timeout=timeout_seconds + 60,
        allow_failure=True,
    )
    remote_result_check = _run(
        [
            *prefix,
            "exec",
            str(pod["name"]),
            "-c",
            "worker",
            "--",
            "test",
            "-s",
            remote_result,
        ],
        allow_failure=True,
    )
    failure_codes: list[str] = []
    if result.returncode != 0:
        failure_codes.append("OPENCLAW_NONZERO_EXIT")
    payload_failure = _openclaw_failure_code(result.stdout)
    if payload_failure is not None:
        failure_codes.append(payload_failure)
    if remote_result_check.returncode != 0:
        failure_codes.append("REMOTE_RESULT_MISSING_OR_EMPTY")
    stderr_bytes = result.stderr.encode("utf-8", errors="replace")
    return {
        "schema_version": "orgrebase.agentteams-direct-dispatch-attempt.v1",
        "transport": "DIRECT_OPENCLAW_GATEWAY_NOT_MATRIX_INBOUND",
        "worker_name": worker_name,
        "pod_name": pod["name"],
        "session_id": session_id,
        "started_at_ms": started_ms,
        "finished_at_ms": int(time.time() * 1000),
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr_present": bool(result.stderr),
        "stderr_digest": _bytes_digest(stderr_bytes) if stderr_bytes else None,
        "outcome": "FAILED" if failure_codes else "SUCCEEDED",
        "failure_codes": failure_codes,
    }


def execute(
    *,
    bundle: Path,
    entries: dict[str, dict[str, Any]],
    envelope: dict[str, Any],
    context: str,
    namespace: str,
    attempt: str,
    timeout_seconds: int,
    workers: tuple[str, ...] = SPECIALISTS,
) -> None:
    pods = _runtime_pods(envelope, workers)
    log_root = bundle / "dispatch" / attempt
    existing_logs = [
        worker_name
        for worker_name in workers
        if (log_root / f"{worker_name}.json").exists()
    ]
    if existing_logs:
        raise DispatchError(
            f"refusing to reuse dispatch attempt for: {', '.join(existing_logs)}"
        )
    log_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_root.chmod(0o700)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
        futures = {
            worker_name: executor.submit(
                _execute_one,
                bundle=bundle,
                entry=entries[worker_name],
                pod=pods[worker_name],
                context=context,
                namespace=namespace,
                attempt=attempt,
                timeout_seconds=timeout_seconds,
            )
            for worker_name in workers
        }
        results: dict[str, dict[str, Any]] = {}
        for worker_name, future in futures.items():
            try:
                results[worker_name] = future.result()
            except (OSError, subprocess.TimeoutExpired, DispatchError) as exc:
                results[worker_name] = {
                    "schema_version": "orgrebase.agentteams-direct-dispatch-attempt.v1",
                    "transport": "DIRECT_OPENCLAW_GATEWAY_NOT_MATRIX_INBOUND",
                    "worker_name": worker_name,
                    "outcome": "FAILED",
                    "failure_codes": ["DISPATCH_EXCEPTION"],
                    "safe_error_type": type(exc).__name__,
                }
    for worker_name, result in results.items():
        log_path = log_root / f"{worker_name}.json"
        log_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        log_path.chmod(0o600)
    failed = [
        name for name, result in results.items() if result.get("outcome") != "SUCCEEDED"
    ]
    if failed:
        codes = sorted(
            {
                str(code)
                for name in failed
                for code in results[name].get("failure_codes", ["UNKNOWN_FAILURE"])
            }
        )
        raise DispatchError(
            f"Worker execution failed closed: {', '.join(failed)} "
            f"({', '.join(codes)})"
        )


def _validate_candidate(
    candidate: dict[str, Any], projection: dict[str, Any], worker_name: str
) -> None:
    missing_keys = REQUIRED_CANDIDATE_KEYS.difference(candidate)
    if missing_keys:
        raise DispatchError(
            f"candidate required keys missing: {', '.join(sorted(missing_keys))}"
        )
    primary_kind = PRIMARY_OUTPUT_KIND.get(worker_name)
    if primary_kind is None:
        raise DispatchError(f"candidate Worker is unsupported: {worker_name}")
    bindings = projection["bindings"]
    if (
        candidate.get("schema_version") != CANDIDATE_SCHEMA
        or candidate.get("run_id") != projection.get("run_id")
        or candidate.get("nonce") != projection.get("nonce")
        or candidate.get("worker_name") != worker_name
        or candidate.get("candidate_only") is not True
        or candidate.get("run_envelope_digest") != bindings["run_envelope_digest"]
        or candidate.get("task_projection_digest") != projection["digest"]
        or candidate.get("candidate_schema_digest") != bindings["candidate_schema_digest"]
        or candidate.get("orchestration_plan_digest")
        != bindings["orchestration_plan_digest"]
        or candidate.get("delegation_task_digest") != bindings["delegation_task_digest"]
        or candidate.get("input_refs") != bindings["input_refs"]
        or not REQUIRED_PROHIBITIONS.issubset(
            set(candidate.get("prohibited_actions_respected", []))
        )
        or not isinstance(candidate.get("output"), dict)
        or not candidate["output"]
    ):
        raise DispatchError(f"candidate envelope/binding invalid: {worker_name}")
    if set(candidate["output"]) != {primary_kind}:
        raise DispatchError(f"candidate primary output kind invalid: {worker_name}")
    tool_refs = candidate.get("tool_receipt_refs")
    expected_tool_refs = (
        [f"tool:read-projection@{projection['digest']}"]
        if worker_name == "gtm-steward"
        else []
    )
    if tool_refs != expected_tool_refs:
        raise DispatchError(f"candidate tool receipt binding invalid: {worker_name}")
    if worker_name == "gtm-steward":
        impact = candidate.get("output", {}).get("ImpactCandidate")
        if not isinstance(impact, dict) or not impact or any(
            not isinstance(item, dict)
            or item.get("tool_receipt_ref") != expected_tool_refs[0]
            for item in impact.values()
        ):
            raise DispatchError("GTM impact entries do not bind the exact read receipt")


def collect(
    *,
    bundle: Path,
    entries: dict[str, dict[str, Any]],
    envelope: dict[str, Any],
    context: str,
    namespace: str,
    workers: tuple[str, ...] = SPECIALISTS,
) -> None:
    pods = _runtime_pods(envelope, workers)
    for worker_name in workers:
        entry = entries[worker_name]
        projection_path = _safe_bundle_file(bundle, entry["projection_path"])
        projection = _object(projection_path)
        destination = (bundle / entry["result_bundle_path"]).resolve()
        if not destination.is_relative_to(bundle.resolve()) or destination.exists():
            raise DispatchError(f"unsafe or existing candidate destination: {worker_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            _run(
                [
                    *_kubectl_prefix(context, namespace),
                    "cp",
                    f"{pods[worker_name]['name']}:{projection['runtime_paths']['result']}",
                    str(destination),
                    "-c",
                    "worker",
                ],
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired, DispatchError) as exc:
            destination.unlink(missing_ok=True)
            raise DispatchError(f"candidate copy failed closed: {worker_name}") from exc
        if (
            not destination.is_file()
            or destination.is_symlink()
            or destination.stat().st_size == 0
        ):
            destination.unlink(missing_ok=True)
            raise DispatchError(f"candidate copy missing or empty: {worker_name}")
        try:
            _validate_candidate(_object(destination), projection, worker_name)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--context", default="kind-orgrebase-agentteams")
    parser.add_argument("--namespace")
    parser.add_argument("--action", choices=("stage", "run", "collect", "all"), required=True)
    parser.add_argument(
        "--worker",
        action="append",
        choices=SPECIALISTS,
        help="select one Worker; repeat for a subset (default: all four)",
    )
    parser.add_argument("--attempt", default="a1")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    if not SAFE_ATTEMPT.fullmatch(args.attempt):
        raise SystemExit("--attempt must be a safe lowercase identifier")
    if not 60 <= args.timeout_seconds <= 1800:
        raise SystemExit("--timeout-seconds must be between 60 and 1800")
    try:
        workers = _selected_workers(args.worker)
        bundle = args.bundle_dir.resolve()
        manifest, envelope, entries = _validated_bundle(bundle, int(time.time() * 1000))
        namespace = args.namespace or str(envelope.get("team", {}).get("namespace", ""))
        if not namespace:
            raise DispatchError("namespace is missing")
        if args.action in {"stage", "all"}:
            stage(
                bundle=bundle,
                entries=entries,
                envelope=envelope,
                context=args.context,
                namespace=namespace,
                workers=workers,
            )
        if args.action in {"run", "all"}:
            execute(
                bundle=bundle,
                entries=entries,
                envelope=envelope,
                context=args.context,
                namespace=namespace,
                attempt=args.attempt,
                timeout_seconds=args.timeout_seconds,
                workers=workers,
            )
        if args.action in {"collect", "all"}:
            collect(
                bundle=bundle,
                entries=entries,
                envelope=envelope,
                context=args.context,
                namespace=namespace,
                workers=workers,
            )
    except (OSError, subprocess.TimeoutExpired, DispatchError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "status": args.action.upper(),
                "run_id": manifest["run_id"],
                "workers": len(workers),
                "worker_names": list(workers),
                "transport": "DIRECT_OPENCLAW_GATEWAY_NOT_MATRIX_INBOUND",
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
