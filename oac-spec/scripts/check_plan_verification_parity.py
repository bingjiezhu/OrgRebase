#!/usr/bin/env python3
"""Run the frozen plural fixed-Plan verification capsule through two SUTs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CAPSULE_PATH = (
    ROOT / "experiments/plan-verification-portability/v0.1-seed-1/capsule.json"
)
CAPSULE_DIGEST = "sha256:e9ca42382e0793fd108361c103d815c23cdb64385b6eccdac31dbe82676a17f5"
OUTPUT_PATH = CAPSULE_PATH.with_name("parity-summary.json")
PROTOCOL_VERSION = "oac.ctk.stdio/v3"
MAX_REQUEST_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 1_048_576
MAX_JSON_DEPTH = 64
TIMEOUT_SECONDS = 5.0
DESCRIPTOR_FIELDS = {
    "path",
    "rawSha256",
    "sizeBytes",
    "sourcePath",
    "sourceRawSha256",
    "sourceSizeBytes",
    "transform",
}
TRACK = {
    "role": "plan-verifier",
    "operation": "verifyPlan",
    "profileId": "oac.supplier.plan-verification.plural-capsule",
    "profileVersion": "v0.1-seed-1",
    "wireVersion": PROTOCOL_VERSION,
}
ENVIRONMENT_ALLOWLIST = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
}


class HarnessFailure(RuntimeError):
    """Deterministic harness, protocol, or policy failure."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _loads(raw: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise HarnessFailure(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise HarnessFailure(f"non-JSON numeric constant: {value}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HarnessFailure(f"invalid JSON: {exc}") from exc


def _closed(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise HarnessFailure(f"{label} fields are not closed")


def _depth(value: object) -> int:
    maximum = 1
    pending = [(value, 1)]
    while pending:
        current, level = pending.pop()
        maximum = max(maximum, level)
        if isinstance(current, Mapping):
            pending.extend((item, level + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, level + 1) for item in current)
    return maximum


def _safe_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise HarnessFailure("artifact path must be a normalized POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise HarnessFailure(f"unsafe artifact path: {value}")
    return path


def _read_bound(
    capsule_root: Path,
    descriptor: object,
    label: str,
    *,
    verify_source_drift: bool,
) -> bytes:
    if not isinstance(descriptor, dict):
        raise HarnessFailure(f"{label} descriptor must be an object")
    _closed(descriptor, DESCRIPTOR_FIELDS, f"{label} descriptor")
    relative = _safe_path(descriptor["path"])
    path = capsule_root.joinpath(*relative.parts)
    try:
        status = path.lstat()
    except OSError as exc:
        raise HarnessFailure(f"{label} is missing") from exc
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise HarnessFailure(f"{label} must be a single-link regular file")
    if any(parent.is_symlink() for parent in path.parents if parent != capsule_root.parent):
        raise HarnessFailure(f"{label} path traverses a symlink")
    raw = path.read_bytes()
    if len(raw) != descriptor["sizeBytes"] or _digest(raw) != descriptor["rawSha256"]:
        raise HarnessFailure(f"{label} bytes do not match the capsule ledger")
    if verify_source_drift:
        source_relative = _safe_path(descriptor["sourcePath"])
        source = ROOT.joinpath(*source_relative.parts)
        try:
            source_status = source.lstat()
        except OSError as exc:
            raise HarnessFailure(f"{label} source is missing") from exc
        if not stat.S_ISREG(source_status.st_mode):
            raise HarnessFailure(f"{label} source must be a regular file")
        source_raw = source.read_bytes()
        if (
            len(source_raw) != descriptor["sourceSizeBytes"]
            or _digest(source_raw) != descriptor["sourceRawSha256"]
        ):
            raise HarnessFailure(f"{label} source drift detected")
    return raw


def _verify_detached(value: Mapping[str, Any], field: str, label: str) -> None:
    projection = {key: item for key, item in value.items() if key != field}
    if value.get(field) != _digest(rfc8785.dumps(projection)):
        raise HarnessFailure(f"{label} detached digest mismatch")


def _load_capsule(*, verify_source_drift: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, bytes]]:
    raw = CAPSULE_PATH.read_bytes()
    capsule = _loads(raw)
    if not isinstance(capsule, dict):
        raise HarnessFailure("capsule must be an object")
    if raw != rfc8785.dumps(capsule) + b"\n":
        raise HarnessFailure("capsule must be exact JCS plus LF")
    _verify_detached(capsule, "capsuleDigest", "capsule")
    if capsule["capsuleDigest"] != CAPSULE_DIGEST:
        raise HarnessFailure("capsule digest does not equal the runner trust anchor")
    if (
        capsule.get("apiVersion") != "oac.plan-verification.capsule/v0alpha1"
        or capsule.get("kind") != "PlanVerificationPortabilityCapsule"
        or capsule.get("protocolVersion") != PROTOCOL_VERSION
        or capsule.get("relationCoordinate")
        != "oac.supplier.plan-verification.plural-capsule/v0.1-seed-1"
    ):
        raise HarnessFailure("capsule coordinate mismatch")
    root = CAPSULE_PATH.parent
    materials: dict[str, bytes] = {}
    for index, descriptor in enumerate(capsule["contracts"]):
        materials[f"contract:{index}"] = _read_bound(
            root,
            descriptor,
            f"contract {index}",
            verify_source_drift=verify_source_drift,
        )
    capability_raw = _read_bound(
        root,
        capsule["capabilitySet"],
        "capability set",
        verify_source_drift=verify_source_drift,
    )
    requirement_raw = _read_bound(
        root,
        capsule["requirementSet"],
        "RequirementSet",
        verify_source_drift=verify_source_drift,
    )
    capability, requirements = _loads(capability_raw), _loads(requirement_raw)
    if not isinstance(capability, dict) or not isinstance(requirements, dict):
        raise HarnessFailure("capability and RequirementSet must be objects")
    _verify_detached(capability, "capabilitySetDigest", "capability set")
    _verify_detached(requirements, "requirementSetDigest", "RequirementSet")
    if capability.get("tracks") != [TRACK]:
        raise HarnessFailure("capability set does not equal the frozen track")
    recipe_raw = _read_bound(
        root,
        capsule["generator"]["recipeSet"],
        "recipe set",
        verify_source_drift=verify_source_drift,
    )
    recipe = _loads(recipe_raw)
    if not isinstance(recipe, dict):
        raise HarnessFailure("recipe set must be an object")
    _verify_detached(recipe, "recipeSetDigest", "recipe set")
    _read_bound(
        root,
        capsule["generator"]["source"],
        "generator source",
        verify_source_drift=verify_source_drift,
    )
    _read_bound(
        root,
        capsule["generator"]["runtime"],
        "generator runtime",
        verify_source_drift=verify_source_drift,
    )
    requirement_ids = [item["caseId"] for item in requirements["cases"]]
    capsule_ids = [item["caseId"] for item in capsule["cases"]]
    recipe_ids = [item["caseId"] for item in recipe["cases"]]
    if requirement_ids != capsule_ids or capsule_ids != recipe_ids:
        raise HarnessFailure("capsule, recipe, and RequirementSet case order differs")
    for root_id, descriptors in capsule["rootSets"].items():
        materials[f"{root_id}:snapshot"] = _read_bound(
            root,
            descriptors["snapshot"],
            f"{root_id} snapshot",
            verify_source_drift=verify_source_drift,
        )
        materials[f"{root_id}:change"] = _read_bound(
            root,
            descriptors["change"],
            f"{root_id} change",
            verify_source_drift=verify_source_drift,
        )
    for case in capsule["cases"]:
        materials[f"plan:{case['caseId']}"] = _read_bound(
            root,
            case["plan"],
            f"{case['caseId']} plan",
            verify_source_drift=verify_source_drift,
        )
    plurality_raw = _read_bound(
        root,
        capsule["pluralityWitness"],
        "plurality witness",
        verify_source_drift=verify_source_drift,
    )
    _verify_plurality(_loads(plurality_raw), materials)
    return capsule, capability, requirements, materials


def _partition(plan: Mapping[str, Any]) -> list[list[str]]:
    return sorted(
        (sorted(item["obligationRefs"]) for item in plan["spec"]["workUnits"]),
        key=lambda item: tuple(item),
    )


def _obligation_order_reachability(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    work_obligations = {
        item["workUnitId"]: set(item["obligationRefs"])
        for item in plan["spec"]["workUnits"]
    }
    successors = {work_ref: set() for work_ref in work_obligations}
    for edge in plan["spec"]["happensBefore"]:
        if (
            edge["predecessorRef"] in successors
            and edge["successorRef"] in successors
        ):
            successors[edge["predecessorRef"]].add(edge["successorRef"])
    reachable: set[tuple[str, str]] = set()
    for start in successors:
        pending = list(successors[start])
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(successors[current])
        for end in seen:
            reachable.update(
                (left, right)
                for left in work_obligations[start]
                for right in work_obligations[end]
            )
    return [
        {
            "predecessorObligationRef": predecessor,
            "successorObligationRef": successor,
        }
        for predecessor, successor in sorted(reachable)
    ]


def _verify_plurality(value: object, materials: Mapping[str, bytes]) -> None:
    if not isinstance(value, dict) or value.get("kind") != "PluralityWitness":
        raise HarnessFailure("plurality witness is invalid")
    base = _loads(materials["plan:PV-POS-SC008-BASE"])
    split = _loads(materials["plan:PV-POS-SC008-SPLIT"])
    if not isinstance(base, dict) or not isinstance(split, dict):
        raise HarnessFailure("plural Plan bytes are not objects")
    if base["spec"]["obligations"] != split["spec"]["obligations"]:
        raise HarnessFailure("plural Plans changed their obligation contract")
    if _partition(base) == _partition(split):
        raise HarnessFailure("plural Plans are not materially partition-distinct")
    base_reachability = _obligation_order_reachability(base)
    split_reachability = _obligation_order_reachability(split)
    if (
        value.get("planADigest") != base["digest"]
        or value.get("planBDigest") != split["digest"]
        or value.get("planAObligationPartition") != _partition(base)
        or value.get("planBObligationPartition") != _partition(split)
        or value.get("planAInducedObligationOrderReachability") != base_reachability
        or value.get("planBInducedObligationOrderReachability") != split_reachability
        or value.get("sameInducedObligationOrderReachability")
        is not (base_reachability == split_reachability)
    ):
        raise HarnessFailure("plurality witness does not match frozen Plan bytes")


def _allowed_environment() -> dict[str, str]:
    result = {
        key: value for key, value in os.environ.items() if key in ENVIRONMENT_ALLOWLIST
    }
    result.update(
        {
            "NO_PROXY": "*",
            "no_proxy": "*",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return result


def _kill(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.poll() is None:  # pragma: no cover - Windows fallback
        process.kill()


def _invoke(command: Sequence[str], request: Mapping[str, Any]) -> dict[str, Any]:
    raw = rfc8785.dumps(request) + b"\n"
    if len(raw) > MAX_REQUEST_BYTES:
        raise HarnessFailure("request exceeds the frozen 1 MiB wire ceiling")
    with (
        tempfile.TemporaryDirectory(prefix="oac-plan-sut-") as working_directory,
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=working_directory,
                env=_allowed_environment(),
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise HarnessFailure(f"cannot start SUT: {exc}") from exc
        assert process.stdin is not None
        try:
            process.stdin.write(raw)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
        deadline = time.monotonic() + TIMEOUT_SECONDS
        exceeded = False
        timed_out = False
        while process.poll() is None:
            if (
                os.fstat(stdout_file.fileno()).st_size > MAX_OUTPUT_BYTES
                or os.fstat(stderr_file.fileno()).st_size > MAX_OUTPUT_BYTES
            ):
                exceeded = True
                _kill(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill(process)
                break
            time.sleep(0.005)
        _kill(process)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=1)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(MAX_OUTPUT_BYTES + 1)
        stderr = stderr_file.read(MAX_OUTPUT_BYTES + 1)
    if timed_out:
        raise HarnessFailure("SUT timed out")
    if exceeded or len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
        raise HarnessFailure("SUT output exceeded the frozen ceiling")
    if process.returncode != 0:
        raise HarnessFailure(
            f"SUT exited {process.returncode}: "
            f"{stderr[:2048].decode('utf-8', errors='replace')}"
        )
    response = _loads(stdout)
    if not isinstance(response, dict) or _depth(response) > MAX_JSON_DEPTH:
        raise HarnessFailure("SUT response is not a bounded object")
    return response


def _capability_request() -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "opaque-capability-request",
        "operation": "capabilities",
        "payload": {},
    }


def _check_capability(command: Sequence[str]) -> dict[str, Any]:
    response = _invoke(command, _capability_request())
    _closed(
        response,
        {"protocolVersion", "requestId", "sutStatus", "result"},
        "capability response",
    )
    result = response["result"]
    if not isinstance(result, dict):
        raise HarnessFailure("capability result must be an object")
    _closed(
        result,
        {
            "implementationId",
            "implementationVersion",
            "adapterProtocolVersion",
            "tracks",
        },
        "capability result",
    )
    if (
        response["protocolVersion"] != PROTOCOL_VERSION
        or response["requestId"] != "opaque-capability-request"
        or response["sutStatus"] != "COMPLETED"
        or result["adapterProtocolVersion"] != PROTOCOL_VERSION
        or result["tracks"] != [TRACK]
    ):
        raise HarnessFailure("SUT capability differs from the frozen set")
    if any(
        not isinstance(result[field], str) or not result[field].strip()
        for field in ("implementationId", "implementationVersion")
    ):
        raise HarnessFailure("SUT implementation identity is blank")
    return result


def _claimed_digest(raw: bytes, kind: str) -> str:
    value = _loads(raw)
    if not isinstance(value, dict) or value.get("kind") != kind:
        raise HarnessFailure(f"frozen {kind} bytes are invalid")
    digest = value.get("digest")
    if not isinstance(digest, str):
        raise HarnessFailure(f"frozen {kind} has no digest")
    return digest


def _request(
    snapshot: bytes,
    change: bytes,
    plan: bytes,
    *,
    forbidden_tokens: Sequence[str],
) -> tuple[dict[str, object], str]:
    token = hashlib.sha256(snapshot + b"\x00" + change + b"\x00" + plan).hexdigest()[:24]
    request = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": f"opaque-{token}",
        "operation": "verifyPlan",
        "payload": {
            "snapshotBase64": base64.b64encode(snapshot).decode("ascii"),
            "changeBase64": base64.b64encode(change).decode("ascii"),
            "planBase64": base64.b64encode(plan).decode("ascii"),
        },
    }
    disclosed = (snapshot + b"\n" + change + b"\n" + plan).decode(
        "utf-8", errors="ignore"
    ).lower()
    for forbidden in forbidden_tokens:
        if forbidden.lower() in disclosed:
            raise HarnessFailure(f"SUT material leaks harness metadata token {forbidden!r}")
    return request, _digest(rfc8785.dumps(request))


def _admit_response(
    response: dict[str, Any],
    request_id: str,
    input_digests: Mapping[str, str],
    validator: Draft202012Validator,
    registered_reason_codes: frozenset[str],
) -> dict[str, Any]:
    if response.get("protocolVersion") != PROTOCOL_VERSION or response.get(
        "requestId"
    ) != request_id:
        raise HarnessFailure("response envelope does not bind the request")
    status = response.get("sutStatus")
    if status == "COMPLETED":
        _closed(
            response,
            {"protocolVersion", "requestId", "sutStatus", "result"},
            "completed response",
        )
        result = response["result"]
        if not isinstance(result, dict):
            raise HarnessFailure("completed result must be an object")
        errors = list(validator.iter_errors(result))
        if errors:
            raise HarnessFailure(f"PlanVerificationResult schema failure: {errors[0].message}")
        if result["reasonCodes"] != sorted(set(result["reasonCodes"])):
            raise HarnessFailure("reasonCodes are not sorted and unique")
        unregistered = sorted(set(result["reasonCodes"]) - registered_reason_codes)
        if unregistered:
            raise HarnessFailure(
                f"result contains unregistered reasonCodes: {unregistered}"
            )
        if any(result[field] != input_digests[field] for field in input_digests):
            raise HarnessFailure("result digest does not bind admitted input")
        return {"sutStatus": status, "result": result}
    _closed(
        response,
        {"protocolVersion", "requestId", "sutStatus", "error"},
        "error response",
    )
    error = response["error"]
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        raise HarnessFailure("error response has no stable code")
    if set(error) not in ({"code"}, {"code", "detail"}):
        raise HarnessFailure("error response fields are not closed")
    return {"sutStatus": status, "error": {"code": error["code"]}}


def _score(observation: Mapping[str, Any], expectation: Mapping[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if observation["sutStatus"] != expectation["sutStatus"]:
        failures.append("sutStatus")
        return False, failures
    if expectation["sutStatus"] != "COMPLETED":
        if observation.get("error", {}).get("code") != expectation["errorCode"]:
            failures.append("errorCode")
        return not failures, failures
    result = observation["result"]
    if result["verdict"] != expectation["verdict"]:
        failures.append("verdict")
    reasons = set(result["reasonCodes"])
    if not set(expectation["requiredReasonCodes"]).issubset(reasons):
        failures.append("requiredReasonCodes")
    if set(expectation["forbiddenReasonCodes"]).intersection(reasons):
        failures.append("forbiddenReasonCodes")
    if expectation["verdict"] == "ACCEPT" and reasons:
        failures.append("acceptReasonCodesMustBeEmpty")
    return not failures, failures


def _schema_validator(capsule: Mapping[str, Any], materials: Mapping[str, bytes]) -> Draft202012Validator:
    descriptor_index = {
        item["sourcePath"]: index for index, item in enumerate(capsule["contracts"])
    }
    source = "ctk/schemas/PlanVerificationResult.schema.json"
    try:
        raw = materials[f"contract:{descriptor_index[source]}"]
    except KeyError as exc:
        raise HarnessFailure("capsule omits PlanVerificationResult schema") from exc
    schema = _loads(raw)
    if not isinstance(schema, dict):
        raise HarnessFailure("result schema is not an object")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _registered_reason_codes(
    capsule: Mapping[str, Any], materials: Mapping[str, bytes]
) -> frozenset[str]:
    source = "schemas/reason-code-registry.json"
    matches = [
        index
        for index, descriptor in enumerate(capsule["contracts"])
        if descriptor.get("sourcePath") == source
    ]
    if len(matches) != 1:
        raise HarnessFailure("capsule must bind exactly one reason-code registry")
    registry = _loads(materials[f"contract:{matches[0]}"])
    if not isinstance(registry, dict) or not isinstance(registry.get("codes"), list):
        raise HarnessFailure("reason-code registry is malformed")
    values = [
        item.get("code")
        for item in registry["codes"]
        if isinstance(item, dict)
    ]
    if (
        len(values) != len(registry["codes"])
        or any(not isinstance(item, str) or not item for item in values)
        or len(set(values)) != len(values)
    ):
        raise HarnessFailure("reason-code registry has blank or duplicate codes")
    return frozenset(values)


def _run_suite(
    command: Sequence[str],
    capsule: Mapping[str, Any],
    requirements: Mapping[str, Any],
    materials: Mapping[str, bytes],
    validator: Draft202012Validator,
    registered_reason_codes: frozenset[str],
    *,
    include_observations: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    capability = _check_capability(command)
    expectations = {item["caseId"]: item for item in requirements["cases"]}
    recipe_tokens = {
        item["caseId"]: (
            item["mutationClass"],
            item["plan"]["transform"],
            item["plan"]["sourcePath"],
        )
        for item in capsule["cases"]
    }
    observations: list[dict[str, Any]] = []
    passed = 0
    for case in capsule["cases"]:
        case_id, root_id = case["caseId"], case["rootSet"]
        snapshot = materials[f"{root_id}:snapshot"]
        change = materials[f"{root_id}:change"]
        plan = materials[f"plan:{case_id}"]
        request, request_digest = _request(
            snapshot,
            change,
            plan,
            forbidden_tokens=(case_id, case["mutationClass"], *recipe_tokens[case_id]),
        )
        input_digests = {
            "snapshotDigest": _claimed_digest(snapshot, "OrganizationSnapshot"),
            "changeDigest": _claimed_digest(change, "SemanticChangeSet"),
            "planDigest": _claimed_digest(plan, "OrganizationPlan"),
        }
        response = _invoke(command, request)
        admitted = _admit_response(
            response,
            request["requestId"],
            input_digests,
            validator,
            registered_reason_codes,
        )
        policy_pass, failures = _score(admitted, expectations[case_id])
        passed += int(policy_pass)
        item = {
            "caseId": case_id,
            "requestDigest": request_digest,
            "inputDigests": input_digests,
            "policy": "PASS" if policy_pass else "FAIL",
            "policyFailures": failures,
        }
        if include_observations:
            item["observation"] = admitted
        observations.append(item)
    return (
        {
            "capability": capability,
            "required": len(capsule["cases"]),
            "passed": passed,
            "failed": len(capsule["cases"]) - passed,
        },
        observations,
    )


def _detached_record(value: dict[str, Any], field: str) -> bytes:
    value[field] = _digest(rfc8785.dumps(value))
    return rfc8785.dumps(value) + b"\n"


def _classify_disagreements(
    python: Sequence[Mapping[str, Any]], go: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    exact = 0
    scored = 0
    for left, right in zip(python, go, strict=True):
        if left["caseId"] != right["caseId"]:
            raise HarnessFailure("implementation observation order differs")
        if left["observation"] == right["observation"]:
            exact += 1
            continue
        left_observation, right_observation = left["observation"], right["observation"]
        same_decision = (
            left["policy"] == right["policy"] == "PASS"
            and left_observation["sutStatus"] == right_observation["sutStatus"]
            and (
                left_observation["sutStatus"] != "COMPLETED"
                or left_observation["result"]["verdict"]
                == right_observation["result"]["verdict"]
            )
        )
        classification = (
            "PERMITTED_DIAGNOSTIC_VARIANCE" if same_decision else "INDETERMINATE"
        )
        scored += int(classification == "INDETERMINATE")
        records.append(
            {
                "caseId": left["caseId"],
                "classification": classification,
                "pythonObservation": left_observation,
                "goObservation": right_observation,
                "resolutionAuthority": "public-required-forbidden-reason-policy",
            }
        )
    return records, exact, scored


def _build_disagreements(
    records: Sequence[Mapping[str, Any]], *, write: bool
) -> list[dict[str, Any]]:
    directory = CAPSULE_PATH.parent / "disagreements"
    if write:
        directory.mkdir(parents=True, exist_ok=True)
    descriptors: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        disagreement_id = f"DIS-PV-{index:03d}"
        incident = {
            "apiVersion": "oac.ctk.disagreement/v0alpha1",
            "kind": "PlanVerificationDisagreement",
            "disagreementId": disagreement_id,
            "status": "RESOLVED"
            if record["classification"] == "PERMITTED_DIAGNOSTIC_VARIANCE"
            else "OPEN",
            **record,
            "majorityRule": "prohibited",
            "referenceWins": False,
        }
        incident_raw = _detached_record(incident, "incidentDigest")
        incident_path = directory / f"{disagreement_id}.incident.json"
        if write:
            incident_path.write_bytes(incident_raw)
        resolution = {
            "apiVersion": "oac.ctk.disagreement-resolution/v0alpha1",
            "kind": "PlanVerificationDisagreementResolution",
            "disagreementId": disagreement_id,
            "status": incident["status"],
            "normativeSource": "standard/oac-plan-verification-relation-v0.1.md#5-reason-policy",
            "decision": (
                "complete reason sets may differ when both satisfy the frozen required/forbidden policy"
                if incident["status"] == "RESOLVED"
                else "scored input remains INDETERMINATE until governed resolution"
            ),
            "regressionCaseId": record["caseId"],
            "referenceWins": False,
        }
        resolution_raw = _detached_record(resolution, "resolutionDigest")
        resolution_path = directory / f"{disagreement_id}.resolution.json"
        if write:
            resolution_path.write_bytes(resolution_raw)
        descriptors.append(
            {
                "disagreementId": disagreement_id,
                "classification": record["classification"],
                "incidentPath": incident_path.relative_to(ROOT).as_posix(),
                "incidentRawSha256": _digest(incident_raw),
                "resolutionPath": resolution_path.relative_to(ROOT).as_posix(),
                "resolutionRawSha256": _digest(resolution_raw),
            }
        )
    return descriptors


def _portable_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return the portable evidence projection used by the frozen manifest.

    This field set intentionally mirrors
    ``build_plan_verification_evidence._portable_projection``.  In particular,
    command paths, interpreter/binary bytes, and the resulting detached summary
    digest are host identities rather than portable semantic observations.
    """

    fields = (
        "capsuleDigest",
        "capabilitySetDigest",
        "requirementSetDigest",
        "protocolVersion",
        "requiredCases",
        "python",
        "go",
        "observations",
        "disagreements",
        "mutants",
        "oracleNonEvasion",
        "independence",
        "claim",
        "pythonObservations",
        "goObservations",
    )
    try:
        return {key: summary[key] for key in fields}
    except KeyError as exc:
        raise HarnessFailure(
            f"parity summary cannot form portable projection: missing {exc.args[0]}"
        ) from exc


def check_stored_summary_portable(
    summary: Mapping[str, Any], path: Path
) -> None:
    """Fail closed on portable semantic drift without writing the stored file."""

    try:
        stored_raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise HarnessFailure("stored parity summary is missing") from exc
    except OSError as exc:
        raise HarnessFailure("stored parity summary cannot be read") from exc
    stored = _loads(stored_raw)
    if not isinstance(stored, dict):
        raise HarnessFailure("stored parity summary must be an object")
    try:
        canonical = rfc8785.dumps(stored) + b"\n"
    except (TypeError, ValueError) as exc:
        raise HarnessFailure("stored parity summary is not canonical JSON") from exc
    if stored_raw != canonical:
        raise HarnessFailure("stored parity summary is not exact JCS plus LF")
    _verify_detached(stored, "summaryDigest", "stored parity summary")
    if rfc8785.dumps(_portable_projection(summary)) != rfc8785.dumps(
        _portable_projection(stored)
    ):
        raise HarnessFailure(
            "stored parity summary differs from the live portable projection"
        )


def _command_identity(command: Sequence[str]) -> dict[str, Any]:
    executable = Path(command[0])
    raw_digest = None
    if executable.is_file():
        raw_digest = _digest(executable.read_bytes())
    script_digest = None
    if len(command) > 1 and Path(command[1]).is_file():
        script_digest = _digest(Path(command[1]).read_bytes())
    displayed = list(command)
    if "oac-go-plan-build-" in executable.as_posix():
        displayed[0] = "<fresh-go-build>/oac-go-plan-verifier"
    else:
        try:
            executable.resolve().relative_to(ROOT.resolve())
        except (OSError, ValueError):
            displayed[0] = f"<external>/{executable.name}"
    for index in range(1, len(displayed)):
        candidate = Path(command[index])
        if not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(ROOT.resolve())
        except (OSError, ValueError):
            displayed[index] = f"<external>/{candidate.name}"
    return {
        "argv": displayed,
        "executableRawSha256": raw_digest,
        "entrypointRawSha256": script_digest,
    }


def _build_go() -> tuple[tempfile.TemporaryDirectory[str], list[str]]:
    temporary = tempfile.TemporaryDirectory(prefix="oac-go-plan-build-")
    binary = Path(temporary.name) / "oac-go-plan-verifier"
    build_environment = _allowed_environment()
    build_environment["GOCACHE"] = str(Path(temporary.name) / "go-build-cache")
    completed = subprocess.run(
        ["go", "build", "-trimpath", "-o", str(binary), "."],
        cwd=ROOT / "implementations/go-plan-verifier-v01-internal",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=build_environment,
    )
    if completed.returncode:
        temporary.cleanup()
        raise HarnessFailure(f"Go build failed: {completed.stderr}")
    return temporary, [str(binary)]


def run(args: argparse.Namespace) -> dict[str, Any]:
    read_only = args.check_summary_portable is not None
    capsule, capability_set, requirements, materials = _load_capsule(
        verify_source_drift=not args.skip_source_drift
    )
    validator = _schema_validator(capsule, materials)
    registered_reason_codes = _registered_reason_codes(capsule, materials)
    for expectation in requirements["cases"]:
        published = set(expectation.get("requiredReasonCodes", [])) | set(
            expectation.get("forbiddenReasonCodes", [])
        )
        if not published.issubset(registered_reason_codes):
            raise HarnessFailure("RequirementSet cites an unregistered reason code")
    python_command = shlex.split(args.python_command)
    go_temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.go_command:
        go_command = shlex.split(args.go_command)
    else:
        go_temporary, go_command = _build_go()
    try:
        python_summary, python_observations = _run_suite(
            python_command,
            capsule,
            requirements,
            materials,
            validator,
            registered_reason_codes,
            include_observations=True,
        )
        go_summary, go_observations = _run_suite(
            go_command,
            capsule,
            requirements,
            materials,
            validator,
            registered_reason_codes,
            include_observations=True,
        )
        differences, exact, scored = _classify_disagreements(
            python_observations, go_observations
        )
        disagreement_descriptors = _build_disagreements(
            differences, write=not read_only
        )
        mutants: list[dict[str, Any]] = []
        if not args.skip_mutants:
            for mutant_id, command_text, required_failure in (
                ("accept-all", args.accept_all_command, True),
                ("reason-erasure", args.reason_erasure_command, True),
                ("relation-rule-erasure", args.rule_erasure_command, True),
            ):
                mutant_summary, _ = _run_suite(
                    shlex.split(command_text),
                    capsule,
                    requirements,
                    materials,
                    validator,
                    registered_reason_codes,
                    include_observations=False,
                )
                killed = mutant_summary["failed"] > 0
                if required_failure and not killed:
                    raise HarnessFailure(f"{mutant_id} mutant survived all required cases")
                mutants.append(
                    {"mutantId": mutant_id, "killed": killed, **mutant_summary}
                )
        summary = {
            "apiVersion": "oac.plan-verification.parity/v0alpha1",
            "kind": "PlanVerificationParitySummary",
            "capsuleDigest": capsule["capsuleDigest"],
            "capabilitySetDigest": capability_set["capabilitySetDigest"],
            "requirementSetDigest": requirements["requirementSetDigest"],
            "protocolVersion": PROTOCOL_VERSION,
            "requiredCases": len(capsule["cases"]),
            "python": python_summary,
            "go": go_summary,
            "observations": {
                "exact": exact,
                "permittedDiagnosticVariance": len(differences) - scored,
                "scoredIndeterminate": scored,
            },
            "disagreements": disagreement_descriptors,
            "mutants": mutants,
            "commands": {
                "python": _command_identity(python_command),
                "go": _command_identity(go_command),
            },
            "oracleNonEvasion": {
                "payloadFields": [
                    "snapshotBase64",
                    "changeBase64",
                    "planBase64",
                ],
                "caseIdDisclosed": False,
                "caseIdDerivedIdentifierDisclosed": False,
                "expectationDisclosed": False,
                "mutationClassDisclosed": False,
                "referencePlanDisclosed": False,
                "publicCorpusFingerprintable": True,
            },
            "independence": {
                "python": "reference implementation",
                "go": "internal same-repository implementation",
                "organizationalIndependence": False,
            },
            "claim": "two internal black-box verifiers satisfy the selected plural Plan acceptance relation; no clean-room, complete Profile, enterprise, or behavioral-equivalence claim",
            "pythonObservations": python_observations,
            "goObservations": go_observations,
        }
        summary["summaryDigest"] = _digest(rfc8785.dumps(summary))
        if (
            python_summary["failed"]
            or go_summary["failed"]
            or scored
            or not all(item["killed"] for item in mutants)
        ):
            raise HarnessFailure("required Plan-verification gate did not close")
        if not read_only:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(rfc8785.dumps(summary) + b"\n")
        return summary
    finally:
        if go_temporary is not None:
            go_temporary.cleanup()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "--python-command",
        default=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'implementations/python-plan-verifier-reference/adapter.py'))}",
    )
    result.add_argument("--go-command")
    result.add_argument(
        "--accept-all-command",
        default=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'implementations/python-plan-verifier-reference/accept_all_mutant.py'))}",
    )
    result.add_argument(
        "--reason-erasure-command",
        default=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'implementations/python-plan-verifier-reference/reason_erasure_mutant.py'))}",
    )
    result.add_argument(
        "--rule-erasure-command",
        default=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'implementations/python-plan-verifier-reference/rule_erasure_mutant.py'))}",
    )
    result.add_argument("--skip-mutants", action="store_true")
    result.add_argument("--skip-source-drift", action="store_true")
    result.add_argument("--output", default=str(OUTPUT_PATH))
    result.add_argument(
        "--check-summary-portable",
        type=Path,
        help=(
            "read-only check against the portable semantic projection of a "
            "stored exact-JCS parity summary"
        ),
    )
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        summary = run(args)
        if args.check_summary_portable is not None:
            check_stored_summary_portable(summary, args.check_summary_portable)
    except (HarnessFailure, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"plan verification parity failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "capsuleDigest": summary["capsuleDigest"],
                "requiredCases": summary["requiredCases"],
                "pythonPassed": summary["python"]["passed"],
                "goPassed": summary["go"]["passed"],
                "exactObservations": summary["observations"]["exact"],
                "permittedDiagnosticVariance": summary["observations"][
                    "permittedDiagnosticVariance"
                ],
                "scoredIndeterminate": summary["observations"][
                    "scoredIndeterminate"
                ],
                "mutantsKilled": sum(item["killed"] for item in summary["mutants"]),
                "summaryDigest": summary["summaryDigest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
