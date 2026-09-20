"""Single-request HTTP worker. Stdin and stdout are private, bounded pipes."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx2 as httpx

ENDPOINT = "https://api.openai.com/v1/responses"


def exchange(
    *, body: bytes, key: str | None, timeout_seconds: float,
    max_response_bytes: int, observe: Callable[[dict[str, Any]], None],
    transport: httpx.BaseTransport | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """The sole HTTP implementation, also used by injected protocol fixtures."""
    deadline = time.monotonic() + timeout_seconds
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds,
                          follow_redirects=False, trust_env=False) as client:
            observe({"stage": "SENT_UNKNOWN"})
            with client.stream("POST", ENDPOINT, content=body, headers={
                **({"Authorization": f"Bearer {key}"} if key else {}),
                "Content-Type": "application/json", "Accept": "application/json",
                "Accept-Encoding": "identity",
            }) as response:
                request_id = response.headers.get("x-request-id")
                if not (isinstance(request_id, str) and 0 < len(request_id) <= 256
                        and all(char.isprintable() for char in request_id)):
                    request_id = None
                observe({"stage": "RESPONSE_RECEIVED", "request_id": request_id})
                if is_cancelled():
                    return {"error_code": "OPENAI_CANCELLED_AFTER_SEND"}
                if time.monotonic() >= deadline:
                    return {"error_code": "OPENAI_DEADLINE_EXCEEDED"}
                if response.status_code != 200:
                    return {"error_code": f"OPENAI_HTTP_{response.status_code}"}
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return {"error_code": "OPENAI_RESPONSE_ENCODING_UNSUPPORTED"}
                length = response.headers.get("content-length", "")
                if length.isdecimal() and int(length) > max_response_bytes:
                    return {"error_code": "OPENAI_RESPONSE_TOO_LARGE"}
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if is_cancelled():
                        return {"error_code": "OPENAI_CANCELLED_AFTER_SEND"}
                    if time.monotonic() >= deadline:
                        return {"error_code": "OPENAI_DEADLINE_EXCEEDED"}
                    raw.extend(chunk)
                    if len(raw) > max_response_bytes:
                        return {"error_code": "OPENAI_RESPONSE_TOO_LARGE"}
                if is_cancelled():
                    return {"error_code": "OPENAI_CANCELLED_AFTER_SEND"}
                if time.monotonic() >= deadline:
                    return {"error_code": "OPENAI_DEADLINE_EXCEEDED"}
        return {"body_base64": base64.b64encode(raw).decode("ascii")}
    except httpx.TimeoutException:
        return {"error_code": "OPENAI_TIMEOUT"}
    except (httpx.HTTPError, OSError):
        return {"error_code": "OPENAI_TRANSPORT_ERROR"}


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    # The parent validates the model contract. This process receives only wire
    # bytes and one credential, never a StateStore, fixture loader, or DB handle.
    raw = sys.stdin.buffer.read(2_200_001)
    if len(raw) > 2_200_000:
        raise ValueError("OPENAI_WORKER_INPUT_TOO_LARGE")
    request = json.loads(raw)
    body = base64.b64decode(request["body_base64"], validate=True)
    if len(body) > 1_048_576 or not 0 < request["max_response_bytes"] <= 4_194_304:
        raise ValueError("OPENAI_WORKER_BYTE_LIMIT_INVALID")
    if not 0 < request["timeout_seconds"] <= 60:
        raise ValueError("OPENAI_WORKER_TIMEOUT_INVALID")
    remaining = request["deadline_monotonic"] - time.monotonic()
    if remaining <= 0:
        _emit({"stage": "FINISHED", "error_code": "OPENAI_DEADLINE_EXCEEDED"})
        return
    # This independent stop also bounds an orphaned worker if the parent dies.
    # It has no database authority and never dispatches a second request.
    watchdog = threading.Timer(min(remaining, request["timeout_seconds"]), os._exit, args=(124,))
    watchdog.daemon = True
    watchdog.start()
    try:
        result = exchange(body=body, key=request["key"], timeout_seconds=min(remaining, request["timeout_seconds"]),
                          max_response_bytes=request["max_response_bytes"], observe=_emit)
    finally:
        watchdog.cancel()
    _emit({"stage": "FINISHED", **result})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No traceback or remote body can enter public process logs.
        _emit({"stage": "FINISHED", "error_code": "OPENAI_WORKER_FAILED"})
        raise SystemExit(1) from None
