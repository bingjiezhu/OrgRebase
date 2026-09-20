"""Reference black-box adapter for the independent CTK stdio protocol."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from typing import Any

import rfc8785

from .applicability import strong_kleene_all, strong_kleene_any
from .canonical import OACValidationError
from .closure_micro import derive_micro_closure
from .identifiers import (
    coverage_obligation_identifier,
    evaluation_identifier,
    impact_path_identifier,
    instance_identifier,
)
from .json_types import JsonValue
from .models import ApplicabilityResult
from .resource_profile import SUPPLIER_RESOURCE_PROFILE, ResourceProfileExceeded
from .text import has_oac_non_whitespace
from .witness import format_witness_ref, parse_witness_ref

PROTOCOL_VERSION = "oac.ctk.stdio/v1"
OPERATIONS = (
    "canonicalize",
    "deriveIdentifier",
    "witness",
    "strongKleene",
    "closureMicro",
    "resourceCheck",
)

_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]*(?::[A-Za-z][A-Za-z0-9_]*)?")


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise OACValidationError("CORE_SCHEMA_INVALID", f"duplicate object key: {key}")
        value[key] = item
    return value


def _canonicalize(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    _closed_fields(payload, {"rawBase64"})
    raw_value = payload.get("rawBase64")
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError("rawBase64 must be a non-empty string")
    try:
        raw = base64.b64decode(raw_value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("rawBase64 is invalid") from exc
    try:
        # RFC 8785 serializes the parsed IEEE-754 binary64 value, not the
        # source token's arbitrary-precision spelling.  Parsing both JSON
        # integer and fraction tokens as float mirrors ECMAScript JSON.parse
        # and admits the finite Appendix B vectors, including 2**53.
        decoded = json.loads(
            raw,
            object_pairs_hook=_duplicates,
            parse_int=float,
            parse_float=float,
            parse_constant=_reject_raw_json_constant,
        )
    except OACValidationError:
        raise
    except RecursionError as exc:
        raise ResourceProfileExceeded(
            "jsonDepth", None, SUPPLIER_RESOURCE_PROFILE.max_json_depth
        ) from exc
    except UnicodeDecodeError as exc:
        raise OACValidationError("NON_I_JSON", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", str(exc)) from exc
    depth = _json_depth(decoded)
    if depth > SUPPLIER_RESOURCE_PROFILE.max_json_depth:
        raise ResourceProfileExceeded("jsonDepth", depth, SUPPLIER_RESOURCE_PROFILE.max_json_depth)
    try:
        canonical = rfc8785.dumps(decoded)
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError) as exc:
        raise OACValidationError("NON_I_JSON", str(exc)) from exc
    return {
        "canonicalBase64": base64.b64encode(canonical).decode("ascii"),
        "digest": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
    }


def _derived_identifier(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    _closed_fields(payload, {"identifierKind", "fields"})
    kind = payload.get("identifierKind")
    fields = payload.get("fields")
    if not isinstance(kind, str) or not isinstance(fields, Mapping):
        raise ValueError("identifierKind and fields are required")
    if kind == "evaluation":
        _closed_fields(
            fields,
            {
                "snapshotDigest",
                "changeDigest",
                "changeSubjectRef",
                "sourceRef",
                "predicateVersion",
                "result",
                "reasonCodes",
                "witnessRefs",
            },
        )
        identifier = evaluation_identifier(
            snapshot_digest=_optional_digest(fields, "snapshotDigest"),
            change_digest=_optional_digest(fields, "changeDigest"),
            change_subject_ref=_string(fields, "changeSubjectRef"),
            source_ref=_string(fields, "sourceRef"),
            predicate_version=_string(fields, "predicateVersion"),
            result=_enum(fields, "result", {"TRUE", "FALSE", "UNKNOWN"}),
            reason_codes=_reason_codes(fields, "reasonCodes"),
            witness_refs=_string_set(fields, "witnessRefs"),
        )
    elif kind == "path":
        _closed_fields(
            fields,
            {
                "snapshotDigest",
                "changeDigest",
                "targetRef",
                "state",
                "edgeRefs",
                "ruleRefs",
                "evaluationRefs",
                "dutyRefs",
                "origin",
                "truncated",
            },
        )
        truncated = fields.get("truncated")
        if not isinstance(truncated, bool):
            raise ValueError("truncated must be boolean")
        identifier = impact_path_identifier(
            snapshot_digest=_optional_digest(fields, "snapshotDigest"),
            change_digest=_optional_digest(fields, "changeDigest"),
            target_ref=_string(fields, "targetRef"),
            state=_enum(
                fields,
                "state",
                {"affected", "unknown", "unaffected_proven", "out_of_declared_scope"},
            ),
            edge_refs=_string_set(fields, "edgeRefs"),
            rule_refs=_string_set(fields, "ruleRefs"),
            evaluation_refs=_string_set(fields, "evaluationRefs"),
            duty_refs=_string_set(fields, "dutyRefs"),
            origin=_string(fields, "origin"),
            truncated=truncated,
        )
    elif kind == "obligation":
        _closed_fields(
            fields,
            {
                "snapshotDigest",
                "changeDigest",
                "origin",
                "targetRef",
                "requiredRoleRef",
                "obligationType",
                "resolutionState",
                "pathRefs",
            },
        )
        identifier = coverage_obligation_identifier(
            snapshot_digest=_optional_digest(fields, "snapshotDigest"),
            change_digest=_optional_digest(fields, "changeDigest"),
            origin=_string(fields, "origin"),
            target_ref=_string(fields, "targetRef"),
            required_role_ref=_string(fields, "requiredRoleRef"),
            obligation_type=_string(fields, "obligationType"),
            resolution_state=_enum(fields, "resolutionState", {"affected", "unknown"}),
            path_refs=_string_set(fields, "pathRefs"),
        )
    elif kind in {"role-instance", "work-unit", "plan-decision"}:
        _closed_fields(fields, {"snapshotDigest", "changeDigest", "body"})
        body = fields.get("body")
        if not isinstance(body, Mapping):
            raise ValueError("instance identifier body must be an object")
        _validate_instance_body(kind, body)
        identifier = instance_identifier(
            kind,  # type: ignore[arg-type]
            snapshot_digest=_digest(fields, "snapshotDigest"),
            change_digest=_digest(fields, "changeDigest"),
            body=body,
        )
    else:
        raise ValueError(f"unsupported identifier kind: {kind}")
    return {"identifier": identifier}


def _witness(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    mode = payload.get("mode")
    if mode == "encode":
        _closed_fields(payload, {"mode", "resourceId", "pointer"})
        resource_id = _string(payload, "resourceId")
        pointer = _string_allow_empty(payload, "pointer")
        return {"witnessRef": format_witness_ref(resource_id, pointer)}
    if mode == "decode":
        _closed_fields(payload, {"mode", "witnessRef"})
        resource_id, pointer = parse_witness_ref(_string(payload, "witnessRef"))
        return {"resourceId": resource_id, "pointer": pointer}
    raise ValueError("witness mode must be encode or decode")


def _strong_kleene(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    _closed_fields(payload, {"operator", "values"})
    values = tuple(ApplicabilityResult(item) for item in _strings(payload, "values"))
    operator = payload.get("operator")
    if operator == "all":
        result = strong_kleene_all(values)
    elif operator == "any":
        result = strong_kleene_any(values)
    else:
        raise ValueError("operator must be all or any")
    return {"result": result.value}


def _resource_check(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    if not payload:
        raise ValueError("resourceCheck requires at least one observed dimension")
    limits = SUPPLIER_RESOURCE_PROFILE.as_json()["limits"]
    assert isinstance(limits, Mapping)
    for key, observed in payload.items():
        if key not in limits:
            raise ValueError(f"unknown resource dimension: {key}")
        maximum = limits[key]
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        assert isinstance(maximum, int)
        if observed > maximum:
            raise ResourceProfileExceeded(key, observed, maximum)
    return {"withinProfile": True, "profileId": SUPPLIER_RESOURCE_PROFILE.profile_id}


def _capabilities() -> dict[str, JsonValue]:
    return {
        "implementationId": "oac.reference.python",
        "implementationVersion": "0.3.0a0",
        "adapterProtocolVersion": PROTOCOL_VERSION,
        "tracks": [
            {
                "role": "semantic-kernel",
                "operation": operation,
                "profileId": "oac.phase-a",
                "profileVersion": "v0.1",
                "wireVersion": PROTOCOL_VERSION,
            }
            for operation in OPERATIONS
        ],
    }


def _dispatch(operation: str, payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    if operation == "capabilities":
        _closed_fields(payload, set())
        return _capabilities()
    if operation == "canonicalize":
        return _canonicalize(payload)
    if operation == "deriveIdentifier":
        return _derived_identifier(payload)
    if operation == "witness":
        return _witness(payload)
    if operation == "strongKleene":
        return _strong_kleene(payload)
    if operation == "closureMicro":
        return derive_micro_closure(payload)
    if operation == "resourceCheck":
        return _resource_check(payload)
    raise NotImplementedError(operation)


def handle(request: object) -> dict[str, JsonValue]:
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    if set(request) != {"protocolVersion", "requestId", "operation", "payload"}:
        raise ValueError("request fields do not match the stdio-v1 envelope")
    if request["protocolVersion"] != PROTOCOL_VERSION:
        raise ValueError("unsupported protocolVersion")
    request_id = _string(request, "requestId")
    operation = _string(request, "operation")
    payload = request["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    try:
        request_depth = _json_depth(request)
        if request_depth > SUPPLIER_RESOURCE_PROFILE.max_json_depth:
            raise ResourceProfileExceeded(
                "jsonDepth",
                request_depth,
                SUPPLIER_RESOURCE_PROFILE.max_json_depth,
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
    except OACValidationError as exc:
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


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if (
        not isinstance(item, str)
        or not item
        or len(item) > 4096
        or not has_oac_non_whitespace(item)
    ):
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _string_allow_empty(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or len(item) > 4096:
        raise ValueError(f"{key} must be a string")
    return item


def _optional_digest(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    return _digest(value, key)


def _digest(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or _SHA256_DIGEST.fullmatch(item) is None:
        raise ValueError(f"{key} must be a lowercase sha256 digest")
    return item


def _enum(value: Mapping[str, Any], key: str, choices: set[str]) -> str:
    item = value.get(key)
    if not isinstance(item, str) or item not in choices:
        raise ValueError(f"{key} is outside the frozen enum")
    return item


def _strings(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    item = value.get(key)
    if not isinstance(item, list) or not all(isinstance(element, str) for element in item):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(item)


def _string_set(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = _strings(value, key)
    if len(items) != len(set(items)) or any(
        not item or len(item) > 4096 or not has_oac_non_whitespace(item) for item in items
    ):
        raise ValueError(f"{key} must be a unique array of non-empty strings")
    return items


def _reason_codes(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = _string_set(value, key)
    if any(len(item) > 512 or _REASON_CODE.fullmatch(item) is None for item in items):
        raise ValueError(f"{key} contains an invalid reason code")
    return items


def _closed_fields(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("fields do not match the frozen operation input")


def _validate_instance_body(kind: str, body: Mapping[str, Any]) -> None:
    if kind == "role-instance":
        _closed_fields(body, {"roleDefinitionRef", "principalRef", "obligationRefs"})
        _string(body, "roleDefinitionRef")
        _string(body, "principalRef")
        _string_set(body, "obligationRefs")
        return
    if kind == "work-unit":
        _closed_fields(
            body,
            {"roleInstanceRefs", "accountableRoleInstanceRef", "obligationRefs"},
        )
        _string_set(body, "roleInstanceRefs")
        _string(body, "accountableRoleInstanceRef")
        _string_set(body, "obligationRefs")
        return
    _closed_fields(body, {"subjectRef", "inputClass", "disposition", "reasonCodes"})
    _string(body, "subjectRef")
    _string(body, "inputClass")
    _enum(body, "disposition", {"included", "excluded", "unresolved"})
    _reason_codes(body, "reasonCodes")


def _reject_raw_json_constant(value: str) -> None:
    raise OACValidationError("CORE_SCHEMA_INVALID", f"non-JSON numeric constant: {value}")


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


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
