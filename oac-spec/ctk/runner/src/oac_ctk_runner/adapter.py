"""One-request/one-process black-box adapter invocation."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import rfc8785

from .json_types import JsonValue

PROTOCOL_VERSION = "oac.ctk.stdio/v1"
_ENVIRONMENT_ALLOWLIST = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
}


@dataclass(frozen=True, slots=True)
class AdapterObservation:
    sut_status: str
    response: dict[str, Any] | None
    stage: str
    elapsed_ms: int
    stderr: str
    error_code: str | None = None


def invoke(
    command: tuple[str, ...],
    *,
    operation: str,
    payload: dict[str, Any],
    timeout_ms: int,
    max_output_bytes: int,
    max_json_depth: int,
    max_request_bytes: int = 1_048_576,
) -> AdapterObservation:
    request: dict[str, JsonValue] = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "request-1",
        "operation": operation,
        "payload": payload,
    }
    raw_request = rfc8785.dumps(request) + b"\n"
    if len(raw_request) > max_request_bytes:
        return AdapterObservation(
            sut_status="RESOURCE_EXHAUSTED",
            response=None,
            stage="RESOURCE_EXHAUSTION",
            elapsed_ms=0,
            stderr="",
            error_code="CONFORMANCE_RESOURCE_PROFILE_EXCEEDED",
        )
    environment = {key: value for key, value in os.environ.items() if key in _ENVIRONMENT_ALLOWLIST}
    environment["NO_PROXY"] = "*"
    environment["no_proxy"] = "*"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    started = time.monotonic_ns()
    with (
        tempfile.TemporaryDirectory(prefix="oac-ctk-sut-") as working_directory,
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                cwd=working_directory,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            elapsed = (time.monotonic_ns() - started) // 1_000_000
            return AdapterObservation(
                sut_status="CRASH",
                response=None,
                stage="ADAPTER",
                elapsed_ms=int(elapsed),
                stderr=str(exc),
                error_code="ADAPTER_START_FAILED",
            )
        assert process.stdin is not None
        os.set_blocking(process.stdin.fileno(), False)
        remaining = memoryview(raw_request)
        deadline = started / 1_000_000_000 + (timeout_ms / 1000)
        timed_out = False
        output_exceeded = False
        while process.poll() is None:
            if (
                _file_size(stdout_file) > max_output_bytes
                or _file_size(stderr_file) > max_output_bytes
            ):
                output_exceeded = True
                _kill_process_tree(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_process_tree(process)
                break
            if remaining:
                try:
                    written = os.write(process.stdin.fileno(), remaining[:65_536])
                    remaining = remaining[written:]
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    remaining = remaining[len(remaining):]
                if not remaining:
                    process.stdin.close()
            time.sleep(0.005)
        _kill_process_tree(process)
        process.stdin.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stdout_size = _file_size(stdout_file)
        stderr_size = _file_size(stderr_file)
        stdout = _bounded_file_bytes(stdout_file, max_output_bytes)
        stderr = _bounded_file_text(stderr_file, max_output_bytes)
    elapsed = (time.monotonic_ns() - started) // 1_000_000
    if timed_out:
        return AdapterObservation(
            sut_status="TIMEOUT",
            response=None,
            stage="ADAPTER",
            elapsed_ms=int(elapsed),
            stderr=stderr,
            error_code="ADAPTER_TIMEOUT",
        )
    if output_exceeded or stdout_size > max_output_bytes or stderr_size > max_output_bytes:
        return AdapterObservation(
            sut_status="RESOURCE_EXHAUSTED",
            response=None,
            stage="RESOURCE_EXHAUSTION",
            elapsed_ms=int(elapsed),
            stderr=stderr,
            error_code="CONFORMANCE_RESOURCE_PROFILE_EXCEEDED",
        )
    if process.returncode != 0:
        return AdapterObservation(
            sut_status="CRASH",
            response=None,
            stage="ADAPTER",
            elapsed_ms=int(elapsed),
            stderr=stderr,
            error_code="ADAPTER_NONZERO_EXIT",
        )
    try:
        response = json.loads(
            stdout,
            object_pairs_hook=_duplicates,
            parse_constant=_reject_non_json_constant,
        )
        if _json_depth(response) > max_json_depth:
            raise ValueError("adapter response exceeds the JSON depth ceiling")
        rfc8785.dumps(response)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        rfc8785.CanonicalizationError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return AdapterObservation(
            sut_status="ERROR",
            response=None,
            stage="PARSE",
            elapsed_ms=int(elapsed),
            stderr=stderr,
            error_code=f"ADAPTER_RESPONSE_INVALID:{type(exc).__name__}",
        )
    if not isinstance(response, dict):
        return AdapterObservation(
            sut_status="ERROR",
            response=None,
            stage="PARSE",
            elapsed_ms=int(elapsed),
            stderr=stderr,
            error_code="ADAPTER_RESPONSE_NOT_OBJECT",
        )
    return response_observation(response, elapsed_ms=int(elapsed), stderr=stderr)


def response_observation(response: dict[str, Any], *, elapsed_ms: int, stderr: str) -> AdapterObservation:
    """Admit a decoded response consistently for live and retained observations."""
    envelope_error = _validate_envelope(response)
    if envelope_error is not None:
        return AdapterObservation(
            sut_status="ERROR",
            response=response,
            stage="SCHEMA",
            elapsed_ms=elapsed_ms,
            stderr=stderr,
            error_code=envelope_error,
        )
    return AdapterObservation(
        sut_status=str(response["sutStatus"]),
        response=response,
        stage="VERDICT",
        elapsed_ms=elapsed_ms,
        stderr=stderr,
    )


def _validate_envelope(response: dict[str, Any]) -> str | None:
    if response.get("protocolVersion") != PROTOCOL_VERSION:
        return "ADAPTER_PROTOCOL_VERSION_MISMATCH"
    if response.get("requestId") != "request-1":
        return "ADAPTER_REQUEST_ID_MISMATCH"
    status = response.get("sutStatus")
    if status not in {"COMPLETED", "UNSUPPORTED", "RESOURCE_EXHAUSTED", "ERROR"}:
        return "ADAPTER_SUT_STATUS_INVALID"
    expected = {"protocolVersion", "requestId", "sutStatus", "result"}
    if status != "COMPLETED":
        expected = {"protocolVersion", "requestId", "sutStatus", "error"}
    if set(response) != expected:
        return "ADAPTER_RESPONSE_FIELDS_INVALID"
    if status == "COMPLETED" and not isinstance(response.get("result"), dict):
        return "ADAPTER_RESULT_INVALID"
    if status != "COMPLETED":
        error = response.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            return "ADAPTER_ERROR_INVALID"
    return None


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate adapter response key: {key}")
        value[key] = item
    return value


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _json_depth(value: object) -> int:
    maximum = 1
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return maximum


def _file_size(stream: Any) -> int:
    return int(os.fstat(stream.fileno()).st_size)


def _bounded_file_bytes(stream: Any, limit: int) -> bytes:
    stream.seek(0)
    return stream.read(limit + 1)


def _bounded_file_text(stream: Any, limit: int) -> str:
    return _bounded_file_bytes(stream, limit).decode("utf-8", errors="replace")


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Best-effort cleanup of the isolated adapter process session."""

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            # The child may exit between poll and killpg, or the host may
            # refuse the group operation. Still reap/kill the owned process.
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    process.kill()
        return
    if process.poll() is None:  # pragma: no cover - exercised on Windows runners
        process.kill()
