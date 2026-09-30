"""Requirement-set scoring without importing a tested implementation."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import Any

import rfc8785

from .adapter import AdapterObservation, invoke
from .bundle import Bundle
from .json_types import JsonValue

_OPERATIONS = {
    "canonicalize",
    "deriveIdentifier",
    "witness",
    "strongKleene",
    "closureMicro",
    "resourceCheck",
}
_RUNNER_MAX_TIMEOUT_MS = 30_000
_RUNNER_MAX_OUTPUT_BYTES = 8_388_608
_RUNNER_MAX_REQUEST_BYTES = 8_388_608
_RUNNER_MAX_JSON_DEPTH = 128


def _digest(value: JsonValue) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


def run_bundle(bundle: Bundle, command: tuple[str, ...], *, build_inputs: tuple[Path, ...] = ()) -> dict[str, Any]:
    if not command:
        raise ValueError("adapter command cannot be empty")
    successor = bundle.manifest["bundleFormatVersion"] == "oac.ctk.bundle/v0alpha2"
    from .evidence import enrich_result, observe_build, verify_result
    from .probes import run_probe
    build = observe_build(command, build_inputs) if successor else None
    observations: dict[str, AdapterObservation] = {}
    limits = bundle.resource_profile.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("resource profile limits must be an object")
    timeout_ms = _integer(limits, "adapterTimeoutMs", ceiling=_RUNNER_MAX_TIMEOUT_MS)
    max_output_bytes = _integer(limits, "maxAdapterOutputBytes", ceiling=_RUNNER_MAX_OUTPUT_BYTES)
    max_request_bytes = _integer(limits, "maxRequestBytes", ceiling=_RUNNER_MAX_REQUEST_BYTES)
    max_json_depth = _integer(limits, "maxJsonDepth", ceiling=_RUNNER_MAX_JSON_DEPTH)

    capability_observation = invoke(
        command,
        operation="capabilities",
        payload={},
        timeout_ms=timeout_ms,
        max_output_bytes=max_output_bytes,
        max_json_depth=max_json_depth,
        max_request_bytes=max_request_bytes,
    )
    capability_statement = _capability_statement(capability_observation)
    capability_valid = "observation" not in capability_statement
    profile_id, profile_version = bundle.profile_coordinate
    declared_operations = _declared_operations(
        capability_statement,
        profile_id=profile_id,
        profile_version=profile_version,
    )
    required = set(bundle.requirement_set["required"])
    not_scored = {item["caseId"] for item in bundle.requirement_set["notScored"]}
    results: list[dict[str, Any]] = []
    for case in sorted(bundle.cases, key=lambda item: str(item["caseId"])):
        case_id = str(case["caseId"])
        prerequisite = _case_preflight(case, not_scored, declared_operations, successor=successor)
        if prerequisite is not None:
            results.append(prerequisite)
            continue
        if successor and case.get("testTarget") == "HARNESS":
            result, observation = run_probe(bundle, case)
            results.append(result)
            observations[case_id] = observation
            continue
        operation, payload, expectation = case["operation"], case["input"], case["expect"]
        observation = invoke(
            command,
            operation=operation,
            payload=payload,
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
            max_json_depth=max_json_depth,
            max_request_bytes=max_request_bytes,
        )
        results.append(_score(case_id, expectation, observation))
        observations[case_id] = observation

    required_results = [item for item in results if item["caseId"] in required]
    passed = (
        capability_observation.sut_status == "COMPLETED"
        and capability_valid
        and len(required_results) == len(required)
        and all(item["caseOutcome"] == "PASS" for item in required_results)
    )
    environment = {
        "system": platform.system(),
        "machine": platform.machine(),
        "pythonImplementation": platform.python_implementation(),
        "pythonVersion": platform.python_version(),
    }
    result = {
        "runResultVersion": "oac.ctk.run-result/v0alpha1",
        "bundleDigest": bundle.digest,
        "requirementSetDigest": bundle.artifact_digests[str(bundle.manifest["requirementSetRef"])],
        "capabilityStatement": capability_statement,
        "capabilityStatementDigest": _digest(capability_statement),
        "environment": environment,
        "environmentDigest": _digest(environment),
        "requiredPassed": passed,
        "summary": {
            "required": len(required),
            "passed": sum(
                item["caseOutcome"] == "PASS" and item["caseId"] in required for item in results
            ),
            "failed": sum(
                item["caseOutcome"] in {"FAIL", "HARNESS_ERROR"} and item["caseId"] in required
                for item in results
            ),
            "notScored": len(not_scored),
        },
        "caseResults": results,
    }
    if successor:
        if observe_build(command, build_inputs) != build:
            raise ValueError("BUILD_INPUT_CHANGED_DURING_RUN")
        assert build is not None
        enriched = enrich_result(result, bundle, build, observations)
        verify_result(enriched, bundle)
        return enriched
    return result


def _capability_statement(observation: AdapterObservation) -> dict[str, Any]:
    if observation.response is None or observation.sut_status != "COMPLETED":
        return {
            "implementationId": "unavailable",
            "implementationVersion": "unavailable",
            "adapterProtocolVersion": "oac.ctk.stdio/v1",
            "tracks": [],
            "observation": {
                "sutStatus": observation.sut_status,
                "errorCode": observation.error_code,
            },
        }
    result = observation.response.get("result")
    error_code = _validate_capability_statement(result)
    if error_code is not None:
        return _invalid_capability_statement(error_code)
    assert isinstance(result, dict)
    return result


def _invalid_capability_statement(error_code: str) -> dict[str, Any]:
    return {
        "implementationId": "invalid",
        "implementationVersion": "invalid",
        "adapterProtocolVersion": "oac.ctk.stdio/v1",
        "tracks": [],
        "observation": {"sutStatus": "ERROR", "errorCode": error_code},
    }


def _validate_capability_statement(value: object) -> str | None:
    if not isinstance(value, dict):
        return "CAPABILITY_STATEMENT_NOT_OBJECT"
    required = {
        "implementationId",
        "implementationVersion",
        "adapterProtocolVersion",
        "tracks",
    }
    if not required.issubset(value) or not set(value).issubset(required | {"extensions"}):
        return "CAPABILITY_STATEMENT_FIELDS_INVALID"
    for key in ("implementationId", "implementationVersion"):
        if not _non_empty_string(value[key]):
            return "CAPABILITY_STATEMENT_IDENTITY_INVALID"
    if value["adapterProtocolVersion"] != "oac.ctk.stdio/v1":
        return "CAPABILITY_PROTOCOL_VERSION_INVALID"
    if "extensions" in value and not _valid_extensions(value["extensions"]):
        return "CAPABILITY_EXTENSIONS_INVALID"
    tracks = value["tracks"]
    if not isinstance(tracks, list):
        return "CAPABILITY_TRACKS_INVALID"
    coordinates: set[tuple[str, ...]] = set()
    track_fields = {"role", "operation", "profileId", "profileVersion", "wireVersion"}
    for track in tracks:
        if not isinstance(track, dict):
            return "CAPABILITY_TRACK_INVALID"
        if not track_fields.issubset(track) or not set(track).issubset(
            track_fields | {"extensions"}
        ):
            return "CAPABILITY_TRACK_FIELDS_INVALID"
        if (
            track["role"] != "semantic-kernel"
            or not isinstance(track["operation"], str)
            or track["operation"] not in _OPERATIONS
        ):
            return "CAPABILITY_TRACK_COORDINATE_INVALID"
        for key in ("profileId", "profileVersion"):
            if not _non_empty_string(track[key]):
                return "CAPABILITY_TRACK_COORDINATE_INVALID"
        if track["wireVersion"] != "oac.ctk.stdio/v1":
            return "CAPABILITY_TRACK_COORDINATE_INVALID"
        if "extensions" in track and not _valid_extensions(track["extensions"]):
            return "CAPABILITY_TRACK_EXTENSIONS_INVALID"
        coordinate = tuple(str(track[key]) for key in sorted(track_fields))
        if coordinate in coordinates:
            return "CAPABILITY_TRACK_DUPLICATE"
        coordinates.add(coordinate)
    return None


def _non_empty_string(value: object, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and any(not _is_oac_whitespace(character) for character in value)
    )


def _is_oac_whitespace(character: str) -> bool:
    """Frozen Unicode White_Space set; never defer to runtime predicates."""

    code_point = ord(character)
    return (
        0x0009 <= code_point <= 0x000D
        or code_point in {0x0020, 0x0085, 0x00A0, 0x1680}
        or 0x2000 <= code_point <= 0x200A
        or code_point in {0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
    )


def _valid_extensions(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and 1 <= len(key) <= 255 for key in value
    )


def _declared_operations(
    statement: dict[str, Any], *, profile_id: str, profile_version: str
) -> set[str]:
    tracks = statement.get("tracks")
    if not isinstance(tracks, list):
        return set()
    return {
        str(track["operation"])
        for track in tracks
        if isinstance(track, dict)
        and track.get("role") == "semantic-kernel"
        and track.get("profileId") == profile_id
        and track.get("profileVersion") == profile_version
        and track.get("wireVersion") == "oac.ctk.stdio/v1"
        and isinstance(track.get("operation"), str)
    }


def _case_preflight(case: dict[str, Any], not_scored: set[str], declared_operations: set[str],
                    *, successor: bool) -> dict[str, Any] | None:
    case_id = str(case["caseId"])
    if case_id in not_scored:
        return {"caseId": case_id, "caseOutcome": "NOT_SCORED", "sutStatus": "UNSUPPORTED",
                "stage": "CAPABILITY", "reasonCodes": ["CASE_NOT_SCORED"],
                "domainVerdict": None, "elapsedMs": 0}
    if successor and case.get("testTarget") == "HARNESS":
        return None
    operation = case.get("operation")
    if not isinstance(operation, str) or not operation:
        return _harness_error(case_id, "CASE_OPERATION_INVALID")
    if operation not in declared_operations:
        return {"caseId": case_id, "caseOutcome": "FAIL", "sutStatus": "UNSUPPORTED",
                "stage": "CAPABILITY", "reasonCodes": ["REQUIRED_CAPABILITY_MISSING"],
                "domainVerdict": None, "elapsedMs": 0}
    if not isinstance(case.get("input"), dict) or not isinstance(case.get("expect"), dict):
        return _harness_error(case_id, "CASE_SHAPE_INVALID")
    return None


def score_recorded_case(bundle: Bundle, case: dict[str, Any], capability: dict[str, Any],
                        observation: AdapterObservation) -> dict[str, Any]:
    """Replay the same admission and scoring rules against recorded observations."""
    from .probes import score_probe
    profile_id, profile_version = bundle.profile_coordinate
    prerequisite = _case_preflight(case,
        {item["caseId"] for item in bundle.requirement_set["notScored"]},
        _declared_operations(capability, profile_id=profile_id, profile_version=profile_version),
        successor=True)
    if prerequisite is not None:
        expected = AdapterObservation(prerequisite["sutStatus"], None, prerequisite["stage"],
            0, "", prerequisite["reasonCodes"][0] if prerequisite["reasonCodes"] else None)
        if observation != expected:
            raise ValueError("SUCCESSOR_RESULT_PREREQUISITE_OBSERVATION_INVALID")
        return prerequisite
    if case["testTarget"] == "HARNESS":
        return score_probe(case, observation)
    return _score(case["caseId"], case["expect"], observation)


def _score(
    case_id: str, expectation: dict[str, Any], observation: AdapterObservation
) -> dict[str, Any]:
    allowed_expectation_fields = {
        "sutStatus",
        "result",
        "errorCode",
        "domainVerdict",
        "requiredReasonCodes",
        "forbiddenReasonCodes",
    }
    if not set(expectation).issubset(allowed_expectation_fields):
        return _harness_error(case_id, "EXPECTATION_FIELDS_INVALID")
    expected_status = expectation.get("sutStatus")
    if expected_status not in {"COMPLETED", "UNSUPPORTED", "RESOURCE_EXHAUSTED", "ERROR"}:
        return _harness_error(case_id, "EXPECTATION_STATUS_INVALID")
    if expected_status == "COMPLETED":
        if not isinstance(expectation.get("result"), dict) or "errorCode" in expectation:
            return _harness_error(case_id, "EXPECTATION_RESULT_INVALID")
    elif not isinstance(expectation.get("errorCode"), str) or set(expectation) != {
        "sutStatus",
        "errorCode",
    }:
        return _harness_error(case_id, "EXPECTATION_ERROR_INVALID")
    reasons: list[str] = []
    if observation.sut_status != expected_status:
        reasons.append("SUT_STATUS_MISMATCH")
    response = observation.response or {}
    raw_result = response.get("result")
    raw_error = response.get("error")
    result = raw_result if isinstance(raw_result, dict) else {}
    error = raw_error if isinstance(raw_error, dict) else {}
    if "result" in expectation and result != expectation["result"]:
        reasons.append("RESULT_MISMATCH")
    if "errorCode" in expectation and error.get("code") != expectation["errorCode"]:
        reasons.append("ERROR_CODE_MISMATCH")
    raw_domain_verdict = result.get("domainVerdict")
    if raw_domain_verdict is not None and (
        not isinstance(raw_domain_verdict, str)
        or raw_domain_verdict not in {"ACCEPT", "REJECT", "PROVISIONAL", "UNKNOWN"}
    ):
        reasons.append("ADAPTER_DOMAIN_VERDICT_INVALID")
        domain_verdict = None
    else:
        domain_verdict = raw_domain_verdict
    if "domainVerdict" in expectation and domain_verdict != expectation["domainVerdict"]:
        reasons.append("DOMAIN_VERDICT_MISMATCH")
    raw_actual_reasons = result.get("reasonCodes", [])
    if (
        not isinstance(raw_actual_reasons, list)
        or not all(isinstance(item, str) for item in raw_actual_reasons)
        or len(raw_actual_reasons) != len(set(raw_actual_reasons))
    ):
        reasons.append("ADAPTER_RESULT_REASON_CODES_INVALID")
        actual_reasons: set[str] = set()
    else:
        actual_reasons = set(raw_actual_reasons)
    required_reasons = expectation.get("requiredReasonCodes", [])
    forbidden_reasons = expectation.get("forbiddenReasonCodes", [])
    if (
        not isinstance(required_reasons, list)
        or not all(isinstance(item, str) for item in required_reasons)
        or len(required_reasons) != len(set(required_reasons))
        or not isinstance(forbidden_reasons, list)
        or not all(isinstance(item, str) for item in forbidden_reasons)
        or len(forbidden_reasons) != len(set(forbidden_reasons))
    ):
        return _harness_error(case_id, "EXPECTATION_REASON_POLICY_INVALID")
    if not set(required_reasons).issubset(actual_reasons):
        reasons.append("REQUIRED_REASON_MISSING")
    if set(forbidden_reasons) & actual_reasons:
        reasons.append("FORBIDDEN_REASON_PRESENT")
    if observation.error_code:
        reasons.append(observation.error_code)
    return {
        "caseId": case_id,
        "caseOutcome": "FAIL" if reasons else "PASS",
        "sutStatus": observation.sut_status,
        "stage": observation.stage,
        "reasonCodes": sorted(set(reasons)),
        "domainVerdict": domain_verdict,
        "elapsedMs": observation.elapsed_ms,
    }


def _harness_error(case_id: str, code: str) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "caseOutcome": "HARNESS_ERROR",
        "sutStatus": "ERROR",
        "stage": "HARNESS",
        "reasonCodes": [code],
        "domainVerdict": None,
        "elapsedMs": 0,
    }


def _integer(value: dict[str, Any], key: str, *, ceiling: int) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"resource profile {key} must be a positive integer")
    if item > ceiling:
        raise ValueError(f"resource profile {key} exceeds the runner-owned ceiling")
    return item
