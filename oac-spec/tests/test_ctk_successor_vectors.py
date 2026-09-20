from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator

from oac.canonical import parse_resource, seal_resource
from oac.compiler import compile_supplier_change
from oac.ctk_adapter import handle
from oac.identifiers import instance_identifier
from oac.resource_profile import (
    ResourceProfileExceeded,
    enforce_evaluation_budget,
    enforce_plan_output,
    enforce_supplier_inputs,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ctk/runner/src"))

from oac_ctk_runner.adapter import invoke  # noqa: E402

MODULE_PATH = ROOT / "ctk/successor_vectors.py"
MODULE_SPEC = importlib.util.spec_from_file_location("successor_vectors", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
VECTORS = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(VECTORS)
CASES = VECTORS.successor_cases()


def _request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": "oac.ctk.stdio/v1",
        "requestId": "request-1",
        "operation": operation,
        "payload": payload,
    }


def _by_id(case_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(case for case in CASES if case["caseId"] == case_id))


def _assert_response(case: dict[str, Any], response: dict[str, Any]) -> None:
    assert response["sutStatus"] == case["expect"]["sutStatus"], case["caseId"]
    if "result" in case["expect"]:
        assert response["result"] == case["expect"]["result"], case["caseId"]
        assert "error" not in response
    else:
        assert response["error"]["code"] == case["expect"]["errorCode"], case["caseId"]
        assert "result" not in response, "exhaustion must never admit a partial closure"


def test_successor_vectors_have_bounded_explicit_coverage_and_no_sut_dependency() -> None:
    assert len(CASES) == 107
    assert len({case["caseId"] for case in CASES}) == len(CASES)
    assert all(case["caseId"].startswith("S-") for case in CASES)
    assert Counter(case["operation"] for case in CASES) == {
        "deriveIdentifier": 18,
        "witness": 48,
        "resourceCheck": 36,
        "canonicalize": 2,
        "closureMicro": 3,
    }
    for node in ast.walk(ast.parse(MODULE_PATH.read_text())):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module is not None and node.module.split(".")[0] in sys.stdlib_module_names
    first = VECTORS.successor_cases()
    first[0]["input"]["fields"]["body"]["obligationRefs"].append("mutation")
    assert VECTORS.successor_cases() == CASES


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["caseId"])
def test_successor_case_is_admissible_and_matches_reference(case: dict[str, Any]) -> None:
    schema = json.loads((ROOT / "ctk/schemas/ConformanceCase.schema.json").read_bytes())
    Draft202012Validator(schema).validate(case)
    assert len((json.dumps(case, sort_keys=True, indent=2) + "\n").encode()) <= 1_048_576
    _assert_response(case, handle(_request(case["operation"], case["input"])))


def test_instance_vectors_pin_formula_constants_and_preserve_order_distinctions() -> None:
    golden = {
        "WORK-UNIT": "b0c6fb76b43efea346da3f626f0c2da48afcabf77ab606d5b8fe6ece4cfb0d0b",
        "PLAN-DECISION": "c2879d8ec8a0298d2451645f8caf91c40b1639e34e2f4aa87072c6785320513c",
    }
    for kind, digest in golden.items():
        selected = [case for case in CASES if case["caseId"].startswith(f"S-ID-{kind}-")]
        identifiers = {case["caseId"]: case["expect"]["result"]["identifier"] for case in selected}
        base = identifiers[f"S-ID-{kind}-BASE"]
        assert base == f"urn:oac:id:sha256:v1:{kind.lower()}:{digest}"
        assert identifiers.pop(f"S-ID-{kind}-KEY-ORDER") == base
        assert len(set(identifiers.values())) == len(identifiers)
        for case in selected:
            fields = case["input"]["fields"]
            preimage = {
                "body": fields["body"],
                "kind": kind.lower(),
                "roots": {key: fields[key] for key in ("snapshotDigest", "changeDigest")},
                "scheme": "oac.id/sha256-rfc8785/v1",
            }
            expected = (
                "urn:oac:id:sha256:v1:"
                + kind.lower()
                + ":"
                + hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()
            )
            assert case["expect"]["result"]["identifier"] == expected


def test_equal_generic_body_is_separated_by_kind_not_just_urn_prefix() -> None:
    # The generic helper can hash an equal body; the typed wire operations
    # deliberately cannot accept another kind's closed body shape.
    digests = set()
    for kind in ("role-instance", "work-unit", "plan-decision"):
        preimage = {
            "scheme": "oac.id/sha256-rfc8785/v1",
            "kind": kind,
            "roots": VECTORS.ROOTS,
            "body": {},
        }
        expected = hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()
        actual = instance_identifier(
            kind,
            snapshot_digest=VECTORS.ROOTS["snapshotDigest"],
            change_digest=VECTORS.ROOTS["changeDigest"],
            body={},
        )
        assert actual == f"urn:oac:id:sha256:v1:{kind}:{expected}"
        digests.add(expected)
    assert len(digests) == 3


def test_every_published_counter_has_exact_and_one_over_cases() -> None:
    published = json.loads(
        (ROOT / "profiles/supplier-change/conformance-resource-profile-v1.json").read_bytes()
    )
    assert published["limits"] == VECTORS.RESOURCE_LIMITS
    assert len(published["limits"]) == 18
    for dimension, maximum in published["limits"].items():
        exact = _by_id(f"S-RESOURCE-{dimension}-EXACT")
        over = _by_id(f"S-RESOURCE-{dimension}-OVER")
        assert exact["input"] == {dimension: maximum}
        assert over["input"] == {dimension: maximum + 1}
        assert exact["expect"]["sutStatus"] == "COMPLETED"
        assert over["expect"] == {
            "sutStatus": "RESOURCE_EXHAUSTED",
            "errorCode": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED",
        }


@pytest.mark.parametrize("dimension", ("nodes", "edges", "maxDepth", "maxPathPrefixes"))
def test_actual_closure_rejects_oversized_structures_before_any_result(dimension: str) -> None:
    # One-over inputs are intentionally direct regressions: the published
    # ConformanceCase input schema rejects them before any SUT invocation.
    payload = _by_id("S-ENFORCE-STRUCTURAL-EXACT")["input"]
    if dimension == "nodes":
        payload[dimension].append({"id": "n-over", "admission": "admitted"})
    elif dimension == "edges":
        payload[dimension].append({**payload[dimension][0], "id": "e-over"})
    else:
        payload[dimension] += 1
    response = handle(_request("closureMicro", payload))
    assert response["sutStatus"] == "RESOURCE_EXHAUSTED"
    assert response["error"]["code"] == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"
    assert "result" not in response


@pytest.mark.parametrize(
    ("exact", "over", "dimension", "maximum"),
    (
        ((0,) * 2_048, (0,) * 2_049, "evaluations", 2_048),
        ((256,), (257,), "witnessRefsPerEvaluation", 256),
        ((256,) * 32, (256,) * 32 + (1,), "totalWitnessRefs", 8_192),
    ),
)
def test_evaluation_budget_enforces_actual_aggregate_counts(
    exact: tuple[int, ...],
    over: tuple[int, ...],
    dimension: str,
    maximum: int,
) -> None:
    enforce_evaluation_budget(exact)
    with pytest.raises(ResourceProfileExceeded) as caught:
        enforce_evaluation_budget(over)
    assert (caught.value.dimension, caught.value.observed, caught.value.maximum) == (
        dimension,
        maximum + 1,
        maximum,
    )


def _roots() -> tuple[Any, Any]:
    inputs = ROOT / "profiles/supplier-change/inputs"
    return (
        parse_resource(
            (inputs / "veracier-proc01-contextual.snapshot.json").read_bytes(), verify_digest=True
        ),
        parse_resource((inputs / "SC-008.change.json").read_bytes(), verify_digest=True),
    )


@pytest.mark.parametrize(
    ("field", "dimension", "maximum"),
    (
        ("nodes", "nodes", 256),
        ("dependency_edges", "edges", 1_024),
        ("impact_rules", "rules", 512),
        ("unknown_transition_duties", "duties", 512),
    ),
)
def test_supplier_input_gate_counts_materialized_collections(
    field: str,
    dimension: str,
    maximum: int,
) -> None:
    snapshot, change = _roots()
    sample = getattr(snapshot.spec, field)[0]
    # Resource admission precedes semantic closure/coherence. Repeated entries
    # are not claimed to be a valid domain graph, but still consume resources.
    exact = snapshot.model_copy(
        update={"spec": snapshot.spec.model_copy(update={field: (sample,) * maximum})}
    )
    enforce_supplier_inputs(exact, change)
    over = snapshot.model_copy(
        update={"spec": snapshot.spec.model_copy(update={field: (sample,) * (maximum + 1)})}
    )
    with pytest.raises(ResourceProfileExceeded) as caught:
        compile_supplier_change(seal_resource(over), change)
    assert (caught.value.dimension, caught.value.observed, caught.value.maximum) == (
        dimension,
        maximum + 1,
        maximum,
    )


def _resource_with_size(resource: Any, size: int) -> Any:
    # These fixtures contain only ASCII and no fractional numbers, so the
    # independent compact JSON length equals the detached JCS projection.
    value = resource.model_dump(mode="json", by_alias=True)
    value.pop("digest", None)
    refs = (
        value["spec"]["workUnits"][0]["evidenceOutputs"]
        if resource.kind == "OrganizationPlan"
        else value["metadata"]["sourceRefs"]
    )
    base_size = len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("ascii")
    )
    remaining = size - base_size
    while remaining > 515:
        length = min(512, remaining - 7)
        refs.append("x" * length)
        remaining -= length + 3
    refs.append("y" * (remaining - 3))
    assert (
        len(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "ascii"
            )
        )
        == size
    )
    return type(resource).model_validate_json(json.dumps(value))


@pytest.mark.parametrize("kind", ("snapshot", "change", "plan"))
def test_actual_serialized_resources_obey_byte_ceiling(kind: str) -> None:
    snapshot, change = _roots()
    resource = {"snapshot": snapshot, "change": change}.get(kind)
    if kind == "plan":
        resource = compile_supplier_change(snapshot, change)
    maximum = 16_777_216 if kind == "plan" else 8_388_608
    exact = _resource_with_size(resource, maximum)
    over = _resource_with_size(resource, maximum + 1)

    def enforce(candidate: Any) -> None:
        if kind == "plan":
            enforce_plan_output(candidate)
        else:
            enforce_supplier_inputs(
                candidate if kind == "snapshot" else snapshot,
                candidate if kind == "change" else change,
            )

    enforce(exact)
    with pytest.raises(ResourceProfileExceeded) as caught:
        enforce(over)
    assert (caught.value.dimension, caught.value.observed, caught.value.maximum) == (
        f"{kind}Bytes",
        maximum + 1,
        maximum,
    )


def test_adapter_reads_no_more_than_published_wire_request_ceiling() -> None:
    # Whitespace padding tests actual wire bytes without an invalid operation
    # or an oversized semantic output obscuring request admission.
    raw = json.dumps(_request("resourceCheck", {"maxNodes": 256})).encode()
    raw += b" " * (1_048_576 - len(raw))
    command = [
        sys.executable,
        "-c",
        f"import sys;sys.path.insert(0,{str(ROOT / 'src')!r});from oac.ctk_adapter import main;raise SystemExit(main())",
    ]
    exact = subprocess.run(command, input=raw, capture_output=True, timeout=10, check=False)
    assert exact.returncode == 0
    assert json.loads(exact.stdout)["sutStatus"] == "COMPLETED"
    over = subprocess.run(command, input=raw + b" ", capture_output=True, timeout=10, check=False)
    assert over.returncode == 2
    assert over.stdout == b""
    assert b"maxRequestBytes" in over.stderr


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
@pytest.mark.parametrize("extra", (0, 1))
def test_runner_measures_real_output_stream_bytes(stream: str, extra: int) -> None:
    response = json.dumps(
        {
            "protocolVersion": "oac.ctk.stdio/v1",
            "requestId": "request-1",
            "sutStatus": "COMPLETED",
            "result": {},
        },
        separators=(",", ":"),
    )
    size = 1_048_576 + extra
    if stream == "stdout":
        code = f"import sys;sys.stdout.write({response!r}+' '*({size}-{len(response)}))"
    else:
        code = f"import sys;sys.stdout.write({response!r});sys.stderr.write('x'*{size})"
    observation = invoke(
        (sys.executable, "-c", code),
        operation="capabilities",
        payload={},
        timeout_ms=5_000,
        max_output_bytes=1_048_576,
        max_json_depth=64,
    )
    assert observation.sut_status == ("COMPLETED" if extra == 0 else "RESOURCE_EXHAUSTED")
    if extra:
        assert observation.response is None
        assert observation.error_code == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"


def test_runner_timeout_is_not_a_partial_domain_verdict() -> None:
    observation = invoke(
        (sys.executable, "-c", "import time;time.sleep(10)"),
        operation="capabilities",
        payload={},
        timeout_ms=5_000,
        max_output_bytes=1_048_576,
        max_json_depth=64,
    )
    assert observation.sut_status == "TIMEOUT"
    assert observation.error_code == "ADAPTER_TIMEOUT"
    assert observation.response is None


@pytest.mark.skipif(os.name != "posix", reason="owned process-group cleanup requires POSIX")
def test_runner_deadline_includes_blocked_stdin_write(tmp_path: Path) -> None:
    child_pid = tmp_path / "adapter.pid"
    adapter_code = (
        "import os,time;from pathlib import Path;"
        f"Path({str(child_pid)!r}).write_text(str(os.getpid()));time.sleep(30)"
    )
    runner_code = (
        "import json,sys;"
        f"sys.path.insert(0,{str(ROOT / 'ctk/runner/src')!r});"
        "from oac_ctk_runner.adapter import invoke;"
        f"r=invoke((sys.executable,'-c',{adapter_code!r}),"
        "operation='canonicalize',payload={'rawBase64':'A'*900000},"
        "timeout_ms=200,max_output_bytes=1048576,max_json_depth=64);"
        "print(json.dumps({'status':r.sut_status,'code':r.error_code,'response':r.response}))"
    )
    try:
        try:
            completed = subprocess.run(
                (sys.executable, "-c", runner_code),
                capture_output=True,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("runner blocked before enforcing its 200 ms stdin-write deadline")
        assert completed.returncode == 0, completed.stderr.decode()
        assert json.loads(completed.stdout) == {
            "status": "TIMEOUT",
            "code": "ADAPTER_TIMEOUT",
            "response": None,
        }
    finally:
        # Also reap the owned SUT if an older runner stalls and the outer
        # watchdog has to kill its parent process.
        if child_pid.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(child_pid.read_text()), signal.SIGKILL)
