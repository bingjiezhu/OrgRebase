"""Fresh-process reference adapter for the raw-resource CTK stdio-v2 protocol.

This module deliberately does not reuse the stdio-v1 dispatcher.  Version 2
owns raw ``SealedResource/v1`` admission and exposes only the two portability
tracks frozen by the Supplier v0.2 A1 capsule.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections.abc import Mapping
from typing import Any

import rfc8785

from .canonical import OACValidationError
from .derivation import profile_derivation_report
from .json_types import JsonValue
from .resource_profile import SUPPLIER_RESOURCE_PROFILE, ResourceProfileExceeded
from .sealed import _decode_raw_object, admit_sealed_resource
from .supplier import ProfileError, derive_supplier_contract_from_admitted

PROTOCOL_VERSION = "oac.ctk.stdio/v2"
IMPLEMENTATION_ID = "oac.reference.python.supplier-v02"
IMPLEMENTATION_VERSION = "0.3.0a0"
_EXPECTED_KINDS = frozenset({"OrganizationSnapshot", "SemanticChangeSet", "OrganizationPlan"})


def _closed_fields(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("fields do not match the frozen operation input")


def _non_empty_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _decode_resource(payload: Mapping[str, Any], key: str, expected_kind: str) -> Any:
    encoded = _non_empty_string(payload, key)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{key} is not canonical Base64") from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError(f"{key} is not canonical Base64")
    if len(raw) > SUPPLIER_RESOURCE_PROFILE.max_resource_bytes:
        raise ResourceProfileExceeded(
            "resourceBytes", len(raw), SUPPLIER_RESOURCE_PROFILE.max_resource_bytes
        )
    if expected_kind == "OrganizationPlan":
        decoded = _decode_raw_object(
            raw, max_json_depth=SUPPLIER_RESOURCE_PROFILE.max_json_depth
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


def _capabilities() -> dict[str, JsonValue]:
    return {
        "implementationId": IMPLEMENTATION_ID,
        "implementationVersion": IMPLEMENTATION_VERSION,
        "adapterProtocolVersion": PROTOCOL_VERSION,
        "tracks": [
            {
                "role": "semantic-kernel",
                "operation": "validateResource",
                "profileId": "oac.core.sealed-resource",
                "profileVersion": "v1",
                "wireVersion": PROTOCOL_VERSION,
            },
            {
                "role": "semantic-kernel",
                "operation": "derive",
                "profileId": "oac.supplier.transfer.portability-capsule",
                "profileVersion": "v0.2-seed-2",
                "wireVersion": PROTOCOL_VERSION,
            },
        ],
    }


def _validate_resource(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    _closed_fields(payload, {"expectedKind", "rawBase64"})
    expected_kind = _non_empty_string(payload, "expectedKind")
    if expected_kind not in _EXPECTED_KINDS:
        raise ValueError("expectedKind is outside the frozen v2 enum")
    admitted = _decode_resource(payload, "rawBase64", expected_kind)
    metadata = admitted.decoded.get("metadata")
    if not isinstance(metadata, Mapping) or not isinstance(metadata.get("id"), str):
        # This should already have been rejected by sealed admission.  Keeping
        # the guard here prevents a permissive future admission implementation
        # from manufacturing a protocol result.
        raise OACValidationError("CORE_SCHEMA_INVALID", "metadata.id is required")
    return {
        "kind": expected_kind,
        "resourceId": metadata["id"],
        "resourceDigest": admitted.resource_digest,
    }


def _derive(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    _closed_fields(payload, {"snapshotBase64", "changeBase64"})
    snapshot = _decode_resource(payload, "snapshotBase64", "OrganizationSnapshot")
    change = _decode_resource(payload, "changeBase64", "SemanticChangeSet")
    derived = derive_supplier_contract_from_admitted(snapshot, change)
    report_model = profile_derivation_report(
        snapshot.resource,
        change.resource,
        derived=derived,
    )
    report = report_model.model_dump(mode="json", by_alias=True)
    report_bytes = rfc8785.dumps(report)
    return {
        "report": report,
        "reportDigest": f"sha256:{hashlib.sha256(report_bytes).hexdigest()}",
    }


def _dispatch(operation: str, payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    if operation == "capabilities":
        _closed_fields(payload, set())
        return _capabilities()
    if operation == "validateResource":
        return _validate_resource(payload)
    if operation == "derive":
        return _derive(payload)
    raise NotImplementedError(operation)


def handle(request: object) -> dict[str, JsonValue]:
    """Handle one already-decoded stdio-v2 request."""

    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    if set(request) != {"protocolVersion", "requestId", "operation", "payload"}:
        raise ValueError("request fields do not match the stdio-v2 envelope")
    if request["protocolVersion"] != PROTOCOL_VERSION:
        raise ValueError("unsupported protocolVersion")
    request_id = _non_empty_string(request, "requestId")
    operation = _non_empty_string(request, "operation")
    payload = request["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    try:
        request_depth = _json_depth(request)
        if request_depth > SUPPLIER_RESOURCE_PROFILE.max_json_depth:
            raise ResourceProfileExceeded(
                "jsonDepth", request_depth, SUPPLIER_RESOURCE_PROFILE.max_json_depth
            )
        result = _dispatch(operation, payload)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "COMPLETED",
            "result": result,
        }
    except NotImplementedError:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "sutStatus": "UNSUPPORTED",
            "error": {"code": "OPERATION_UNSUPPORTED"},
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


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


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


def main() -> int:
    try:
        raw_request = sys.stdin.buffer.read(SUPPLIER_RESOURCE_PROFILE.max_request_bytes + 1)
        if len(raw_request) > SUPPLIER_RESOURCE_PROFILE.max_request_bytes:
            raise ValueError("adapter request exceeds maxRequestBytes")
        request = json.loads(
            raw_request,
            object_pairs_hook=_duplicates,
            parse_constant=_reject_non_json_constant,
        )
        # The request envelope itself remains inside the finite I-JSON domain.
        if _json_depth(request) <= SUPPLIER_RESOURCE_PROFILE.max_json_depth:
            rfc8785.dumps(request)
        response = handle(request)
        sys.stdout.buffer.write(rfc8785.dumps(response) + b"\n")
        return 0
    except RecursionError:
        sys.stderr.write("adapter failure: request nesting exceeds parser capacity\n")
        return 2
    except Exception as exc:  # pragma: no cover - process-level last resort
        sys.stderr.write(f"adapter failure: {type(exc).__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
