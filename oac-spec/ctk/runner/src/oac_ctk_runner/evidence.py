"""Bounded run evidence and unresolved disagreements; no execution-attestation claims."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .adapter import PROTOCOL_VERSION, AdapterObservation, response_observation
from .bundle import Bundle
from .contracts import RESULT_VERSION
from .json_types import JsonValue


def digest(value: JsonValue) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _material(path: Path, name: str) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError("BUILD_INPUT_NOT_BOUNDED_REGULAR_FILE") from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > 134_217_728:
            raise ValueError("BUILD_INPUT_NOT_BOUNDED_REGULAR_FILE")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(134_217_729)
        after = os.fstat(descriptor)
        if len(raw) != status.st_size or any(getattr(after, key) != getattr(status, key) for key in
                ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")):
            raise ValueError("BUILD_INPUT_CHANGED_WHILE_READING")
    finally:
        os.close(descriptor)
    return {"name": name, "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}


def observe_build(command: tuple[str, ...], inputs: tuple[Path, ...]) -> dict[str, Any]:
    if not inputs or len(inputs) > 512:
        raise ValueError("SUCCESSOR_BUILD_INPUTS_REQUIRED")
    executable = Path(shutil.which(command[0]) or command[0]).resolve(strict=True)
    names = [str(path.absolute()) for path in inputs]
    if len(names) != len(set(names)):
        raise ValueError("BUILD_INPUTS_DUPLICATE")
    return {"evidenceClass": "SELF_ATTESTED_LOCAL_MATERIAL_OBSERVATION",
            "commandDigest": digest(list(command)), "executable": _material(executable, executable.name),
            "sutInputs": [_material(path, f"{index}:{path.name}") for index, path in enumerate(inputs)],
            "runnerInputs": [_material(path, path.name) for path in sorted(Path(__file__).parent.glob("*.py"))]}


def _request(case: dict[str, Any]) -> JsonValue:
    if case["testTarget"] == "HARNESS":
        return {"probe": case["input"]["probe"]}
    return {"protocolVersion": PROTOCOL_VERSION, "requestId": "request-1",
            "operation": case["operation"], "payload": case["input"]}


def enrich_result(result: dict[str, Any], bundle: Bundle, build: dict[str, Any],
                  observations: dict[str, AdapterObservation]) -> dict[str, Any]:
    result["runResultVersion"] = RESULT_VERSION
    result["resourceProfileDigest"] = bundle.artifact_digests[bundle.manifest["resourceProfileRef"]]
    result["implementationBuild"] = build
    result["implementationBuildDigest"] = digest(build)
    result["claimBoundary"] = "BOUNDED_CTK_NOT_ORGANIZATIONAL_INDEPENDENCE_OR_EXECUTION_ATTESTATION"
    diagnostics, disagreements = [], []
    cases = {case["caseId"]: case for case in bundle.cases}
    for case_result in result["caseResults"]:
        case = cases[case_result["caseId"]]
        target = case["testTarget"]
        case_result["testTarget"] = target
        observed = observations.get(case["caseId"], AdapterObservation(
            case_result["sutStatus"], None, case_result["stage"], 0, "",
            case_result["reasonCodes"][0] if case_result["reasonCodes"] else None))
        diagnostic = {"caseId": case["caseId"], "testTarget": target,
            "requestKind": "HARNESS_RECIPE" if target == "HARNESS" else "STDIO_REQUEST",
            "requestDigest": digest(_request(case)), "decodedResponse": observed.response, "stderrText": observed.stderr,
            "responseDigest": digest(observed.response) if observed.response is not None else None,
            "stderrTextDigest": "sha256:" + hashlib.sha256(observed.stderr.encode("utf-8")).hexdigest(),
            "sutStatus": observed.sut_status, "stage": observed.stage,
            "errorCode": observed.error_code, "elapsedMs": observed.elapsed_ms}
        diagnostics.append(diagnostic)
        if case_result["caseOutcome"] not in {"FAIL", "HARNESS_ERROR", "INDETERMINATE"}:
            continue
        if target == "SUT":
            case_result["caseOutcome"] = "INDETERMINATE"
            case_result["domainVerdict"] = None
        expected: dict[str, JsonValue] = {"implementationId": "published-requirement-set", "implementationBuildDigest": bundle.digest,
                    "artifactDigests": {"expectation": digest(case["expect"]), "input": digest(case["input"])}}
        actual = {"implementationId": result["capabilityStatement"]["implementationId"],
                  "implementationBuildDigest": result["implementationBuildDigest"],
                  "artifactDigests": {"diagnostic": digest(diagnostic), "caseResult": digest(case_result)}}
        for item in (expected, actual):
            item["observationDigest"] = digest(item)
        record = {"apiVersion": "oac.ctk.disagreement/v0.2",
            "disagreementId": "disagreement:" + digest([bundle.digest, case["caseId"], actual])[7:],
            "caseId": case["caseId"], "bundleDigest": bundle.digest,
            "stage": "ADAPTER" if observed.stage == "CAPABILITY" else observed.stage,
            "observations": [expected, actual], "classification": "UNCLASSIFIED", "status": "OPEN",
            "owner": "UNASSIGNED_REVIEW", "affectedVersion": bundle.manifest["suiteVersion"],
            "resolutionRef": None, "regressionCaseRef": None}
        record["digest"] = digest(record)
        disagreements.append(record)
    result["diagnostics"] = diagnostics
    result["diagnosticsDigest"] = digest(diagnostics)
    result["disagreements"] = disagreements
    result["disagreementsDigest"] = digest(disagreements)
    required = set(bundle.requirement_set["required"])
    result["summary"]["failed"] = sum(item["caseId"] in required and item["caseOutcome"] != "PASS"
                                       for item in result["caseResults"])
    result["targetSummary"] = {target: {
        "required": sum(item["caseId"] in required and item["testTarget"] == target for item in result["caseResults"]),
        "passed": sum(item["caseId"] in required and item["testTarget"] == target and item["caseOutcome"] == "PASS" for item in result["caseResults"]),
        "failed": sum(item["caseId"] in required and item["testTarget"] == target and item["caseOutcome"] != "PASS" for item in result["caseResults"]),
    } for target in ("SUT", "HARNESS")}
    result["resultDigest"] = digest(result)
    return result


def verify_result(result: dict[str, Any], bundle: Bundle) -> None:
    """Verify the successor ledger bindings, without claiming to re-execute its observations."""
    from .runner import score_recorded_case

    if bundle.result_schema is None:
        raise ValueError("SUCCESSOR_RESULT_SCHEMA_MISSING")
    try:
        Draft202012Validator(bundle.result_schema).validate(result)
    except ValidationError as error:
        raise ValueError("SUCCESSOR_RESULT_SCHEMA_INVALID") from error
    if result["bundleDigest"] != bundle.digest or any(result[key] != bundle.artifact_digests[bundle.manifest[ref]]
            for key, ref in (("resourceProfileDigest", "resourceProfileRef"),
                             ("requirementSetDigest", "requirementSetRef"))):
        raise ValueError("SUCCESSOR_RESULT_BUNDLE_BINDING_INVALID")
    for field in ("implementationBuild", "capabilityStatement", "environment", "diagnostics", "disagreements"):
        if result[field + "Digest"] != digest(result[field]):
            raise ValueError("SUCCESSOR_RESULT_COMPONENT_DIGEST_INVALID")
    if result["resultDigest"] != digest({key: value for key, value in result.items() if key != "resultDigest"}):
        raise ValueError("SUCCESSOR_RESULT_DIGEST_INVALID")
    cases = {case["caseId"]: case for case in bundle.cases}
    rows = {row["caseId"]: row for row in result["caseResults"]}
    diagnostics = {row["caseId"]: row for row in result["diagnostics"]}
    if (len(rows) != len(result["caseResults"]) or len(diagnostics) != len(result["diagnostics"])
            or set(rows) != set(cases) or set(diagnostics) != set(cases)):
        raise ValueError("SUCCESSOR_RESULT_CASE_COVERAGE_INVALID")
    for case_id, case in cases.items():
        diagnostic = diagnostics[case_id]
        if (rows[case_id]["testTarget"] != case["testTarget"] or diagnostic["testTarget"] != case["testTarget"]
                or diagnostic["requestKind"] != ("HARNESS_RECIPE" if case["testTarget"] == "HARNESS" else "STDIO_REQUEST")
                or diagnostic["requestDigest"] != digest(_request(case))
                or diagnostic["responseDigest"] != (digest(diagnostic["decodedResponse"]) if diagnostic["decodedResponse"] is not None else None)
                or diagnostic["stderrTextDigest"] != "sha256:" + hashlib.sha256(diagnostic["stderrText"].encode("utf-8")).hexdigest()):
            raise ValueError("SUCCESSOR_RESULT_CASE_BINDING_INVALID")
        observed = AdapterObservation(diagnostic["sutStatus"], diagnostic["decodedResponse"],
            diagnostic["stage"], diagnostic["elapsedMs"], diagnostic["stderrText"], diagnostic["errorCode"])
        if observed.response is not None and response_observation(observed.response,
                elapsed_ms=observed.elapsed_ms, stderr=observed.stderr) != observed:
            raise ValueError("SUCCESSOR_RESULT_RESPONSE_ADMISSION_INVALID")
        scored = score_recorded_case(bundle, case, result["capabilityStatement"], observed)
        if case["testTarget"] == "SUT" and scored["caseOutcome"] in {"FAIL", "HARNESS_ERROR", "INDETERMINATE"}:
            scored["caseOutcome"] = "INDETERMINATE"
            scored["domainVerdict"] = None
        scored["testTarget"] = case["testTarget"]
        if rows[case_id] != scored:
            raise ValueError("SUCCESSOR_RESULT_CASE_SCORE_INVALID")
    required = set(bundle.requirement_set["required"])
    passed = sum(rows[key]["caseOutcome"] == "PASS" for key in required)
    summary = {"required": len(required), "passed": passed, "failed": len(required) - passed,
               "notScored": len(bundle.requirement_set["notScored"])}
    targets = {target: {"required": sum(rows[key]["testTarget"] == target for key in required),
        "passed": sum(rows[key]["testTarget"] == target and rows[key]["caseOutcome"] == "PASS" for key in required),
        "failed": sum(rows[key]["testTarget"] == target and rows[key]["caseOutcome"] != "PASS" for key in required)}
        for target in ("SUT", "HARNESS")}
    if result["summary"] != summary or result["targetSummary"] != targets or result["requiredPassed"] != (
            passed == len(required) and "observation" not in result["capabilityStatement"]):
        raise ValueError("SUCCESSOR_RESULT_SUMMARY_INVALID")
    disagreement_cases = []
    for record in result["disagreements"]:
        case_id = record["caseId"]
        if case_id not in cases:
            raise ValueError("SUCCESSOR_DISAGREEMENT_CASE_INVALID")
        expected, actual = record["observations"]
        if (record["bundleDigest"] != bundle.digest or record["affectedVersion"] != bundle.manifest["suiteVersion"]
                or record["stage"] != ("ADAPTER" if diagnostics[case_id]["stage"] == "CAPABILITY" else diagnostics[case_id]["stage"])
                or record["classification"] != "UNCLASSIFIED" or record["status"] != "OPEN"
                or record["owner"] != "UNASSIGNED_REVIEW" or record["resolutionRef"] is not None
                or record["regressionCaseRef"] is not None
                or expected["implementationId"] != "published-requirement-set"
                or expected["implementationBuildDigest"] != bundle.digest
                or expected["artifactDigests"] != {"expectation": digest(cases[case_id]["expect"]), "input": digest(cases[case_id]["input"])}
                or actual["implementationId"] != result["capabilityStatement"]["implementationId"]
                or actual["implementationBuildDigest"] != result["implementationBuildDigest"]
                or actual["artifactDigests"] != {"diagnostic": digest(diagnostics[case_id]), "caseResult": digest(rows[case_id])}
                or record["disagreementId"] != "disagreement:" + digest([bundle.digest, case_id, actual])[7:]):
            raise ValueError("SUCCESSOR_DISAGREEMENT_BINDING_INVALID")
        for item in (expected, actual):
            if item["observationDigest"] != digest({key: value for key, value in item.items() if key != "observationDigest"}):
                raise ValueError("SUCCESSOR_DISAGREEMENT_DIGEST_INVALID")
        if record["digest"] != digest({key: value for key, value in record.items() if key != "digest"}):
            raise ValueError("SUCCESSOR_DISAGREEMENT_DIGEST_INVALID")
        disagreement_cases.append(case_id)
    if sorted(disagreement_cases) != sorted(key for key, row in rows.items() if row["caseOutcome"] in {"FAIL", "INDETERMINATE", "HARNESS_ERROR"}):
        raise ValueError("SUCCESSOR_DISAGREEMENT_COVERAGE_INVALID")
