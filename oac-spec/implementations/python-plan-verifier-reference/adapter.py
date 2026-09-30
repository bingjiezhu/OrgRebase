#!/usr/bin/env python3
"""Reference black-box adapter for OAC fixed-Plan verification stdio-v3."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Mapping
from typing import Any

import rfc8785

from oac.canonical import OACValidationError
from oac.resource_profile import SUPPLIER_RESOURCE_PROFILE, ResourceProfileExceeded
from oac.sealed import _decode_raw_object, admit_sealed_resource
from oac.supplier import ProfileError
from oac.verifier import verify_plan_from_admitted

PROTOCOL_VERSION = "oac.ctk.stdio/v3"
IMPLEMENTATION_ID = "oac.reference.python.plan-verifier"
IMPLEMENTATION_VERSION = "0.1.0-seed1"


def _closed(value: Mapping[str, Any], fields: set[str]) -> None:
    if set(value) != fields:
        raise ValueError("object fields do not match the frozen protocol")


def _non_empty(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _decode(payload: Mapping[str, Any], key: str, expected_kind: str) -> Any:
    encoded = _non_empty(payload, key)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} is not canonical Base64") from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError(f"{key} is not canonical Base64")
    if len(raw) > SUPPLIER_RESOURCE_PROFILE.max_resource_bytes:
        raise ResourceProfileExceeded(
            "resourceBytes", len(raw), SUPPLIER_RESOURCE_PROFILE.max_resource_bytes
        )
    if expected_kind == "OrganizationPlan":
        decoded = _decode_raw_object(raw)
        depth = _json_depth(decoded)
        if depth > SUPPLIER_RESOURCE_PROFILE.max_json_depth:
            raise ResourceProfileExceeded(
                "jsonDepth", depth, SUPPLIER_RESOURCE_PROFILE.max_json_depth
            )
        spec = decoded.get("spec")
        if isinstance(spec, dict) and "profileBinding" in spec:
            raise OACValidationError(
                "CORE_SCHEMA_INVALID",
                "profileBinding is outside the frozen Supplier wire schema",
            )
    admitted = admit_sealed_resource(
        raw,
        expected_kind,
        max_json_depth=SUPPLIER_RESOURCE_PROFILE.max_json_depth,
    )
    return admitted


def capabilities() -> dict[str, object]:
    return {
        "implementationId": IMPLEMENTATION_ID,
        "implementationVersion": IMPLEMENTATION_VERSION,
        "adapterProtocolVersion": PROTOCOL_VERSION,
        "tracks": [
            {
                "role": "plan-verifier",
                "operation": "verifyPlan",
                "profileId": "oac.supplier.plan-verification.plural-capsule",
                "profileVersion": "v0.1-seed-1",
                "wireVersion": PROTOCOL_VERSION,
            }
        ],
    }


def _verify(payload: Mapping[str, Any]) -> dict[str, object]:
    _closed(payload, {"snapshotBase64", "changeBase64", "planBase64"})
    snapshot = _decode(payload, "snapshotBase64", "OrganizationSnapshot")
    change = _decode(payload, "changeBase64", "SemanticChangeSet")
    plan = _decode(payload, "planBase64", "OrganizationPlan")
    certificate = verify_plan_from_admitted(snapshot, change, plan)
    return {
        "snapshotDigest": snapshot.resource_digest,
        "changeDigest": change.resource_digest,
        "planDigest": plan.resource_digest,
        "verdict": certificate.spec.verdict.value,
        "reasonCodes": list(certificate.spec.reason_codes),
    }


def _json_depth(value: object) -> int:
    maximum = 1
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(current, Mapping):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return maximum


def handle(request: object) -> dict[str, object]:
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    _closed(request, {"protocolVersion", "requestId", "operation", "payload"})
    if request["protocolVersion"] != PROTOCOL_VERSION:
        raise ValueError("unsupported protocolVersion")
    request_id = _non_empty(request, "requestId")
    operation = _non_empty(request, "operation")
    payload = request["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    try:
        depth = _json_depth(request)
        if depth > SUPPLIER_RESOURCE_PROFILE.max_json_depth:
            raise ResourceProfileExceeded(
                "jsonDepth", depth, SUPPLIER_RESOURCE_PROFILE.max_json_depth
            )
        if operation == "capabilities":
            _closed(payload, set())
            result = capabilities()
        elif operation == "verifyPlan":
            result = _verify(payload)
        else:
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": request_id,
                "sutStatus": "UNSUPPORTED",
                "error": {"code": "OPERATION_UNSUPPORTED"},
            }
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "COMPLETED",
            "result": result,
        }
    except ResourceProfileExceeded as exc:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "RESOURCE_EXHAUSTED",
            "error": {"code": exc.reason_code},
        }
    except (OACValidationError, ProfileError) as exc:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "ERROR",
            "error": {"code": exc.reason_code},
        }
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "ERROR",
            "error": {"code": "CTK_INPUT_INVALID", "detail": str(exc)},
        }


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def read_request() -> object:
    raw = sys.stdin.buffer.read(SUPPLIER_RESOURCE_PROFILE.max_request_bytes + 1)
    if len(raw) > SUPPLIER_RESOURCE_PROFILE.max_request_bytes:
        raise ValueError("request exceeds maxRequestBytes")
    request = json.loads(
        raw,
        object_pairs_hook=_duplicates,
        parse_constant=_reject_constant,
    )
    rfc8785.dumps(request)
    return request


def main() -> int:
    try:
        response = handle(read_request())
        sys.stdout.buffer.write(rfc8785.dumps(response) + b"\n")
        return 0
    except Exception as exc:  # pragma: no cover - process boundary
        print(f"adapter failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
