#!/usr/bin/env python3
"""Deliberately permissive SUT used to prove the verdict oracle is active."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Mapping

import adapter
import rfc8785


def _claimed_digest(encoded: object) -> str:
    if not isinstance(encoded, str):
        return "sha256:" + "0" * 64
    try:
        value = json.loads(base64.b64decode(encoded, validate=True))
    except Exception:
        return "sha256:" + "0" * 64
    if isinstance(value, Mapping) and isinstance(value.get("digest"), str):
        return value["digest"]
    return "sha256:" + "0" * 64


def main() -> int:
    request = adapter.read_request()
    if not isinstance(request, Mapping):
        return 2
    request_id = request.get("requestId", "")
    operation = request.get("operation")
    if operation == "capabilities":
        response = {
            "protocolVersion": adapter.PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "COMPLETED",
            "result": adapter.capabilities(),
        }
    else:
        payload = request.get("payload", {})
        response = {
            "protocolVersion": adapter.PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "COMPLETED",
            "result": {
                "snapshotDigest": _claimed_digest(payload.get("snapshotBase64")),
                "changeDigest": _claimed_digest(payload.get("changeBase64")),
                "planDigest": _claimed_digest(payload.get("planBase64")),
                "verdict": "ACCEPT",
                "reasonCodes": [],
            },
        }
    sys.stdout.buffer.write(rfc8785.dumps(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
