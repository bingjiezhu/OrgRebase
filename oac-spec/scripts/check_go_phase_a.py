#!/usr/bin/env python3
"""Build and black-box-check the internally authored Go Phase A adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
GO_IMPLEMENTATION = ROOT / "implementations/go-phase-a"
RUNNER_SOURCE = ROOT / "ctk/runner/src"
BUNDLE = ROOT / "ctk/bundles/phase-a-v0.1"


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _canonical_payload(raw: bytes) -> dict[str, str]:
    return {"rawBase64": base64.b64encode(raw).decode("ascii")}


def _probe_signature(observation: Any) -> tuple[object, object, object]:
    status = observation.sut_status
    response = observation.response
    harness_error = observation.error_code
    if not isinstance(response, dict):
        return status, None, harness_error
    error = response.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    return status, response.get("result"), error_code


def _check_direct_request_ceiling(command: tuple[str, ...]) -> None:
    base = (
        b'{"protocolVersion":"oac.ctk.stdio/v1","requestId":"request-1",'
        b'"operation":"capabilities","payload":{}}'
    )
    ceiling = 1_048_576
    exact = base + (b" " * (ceiling - len(base)))
    over = exact + b" "
    accepted = subprocess.run(
        command,
        cwd=ROOT,
        input=exact,
        capture_output=True,
        check=False,
    )
    rejected = subprocess.run(
        command,
        cwd=ROOT,
        input=over,
        capture_output=True,
        check=False,
    )
    if accepted.returncode != 0 or not accepted.stdout:
        raise AssertionError(f"exact maxRequestBytes was rejected by {command!r}")
    if rejected.returncode != 2 or rejected.stdout:
        raise AssertionError(f"over maxRequestBytes was not fail-closed by {command!r}")


def main() -> int:
    go = shutil.which("go")
    if go is None:
        raise SystemExit("Go is required for the Phase A differential gate")
    _run([go, "test", "-count=1", "./..."], cwd=GO_IMPLEMENTATION)
    _run([go, "vet", "./..."], cwd=GO_IMPLEMENTATION)

    sys.path.insert(0, str(RUNNER_SOURCE))
    from oac_ctk_runner.adapter import invoke
    from oac_ctk_runner.bundle import load_bundle
    from oac_ctk_runner.runner import run_bundle

    with tempfile.TemporaryDirectory(prefix="oac-go-phase-a-build-") as directory:
        binary = Path(directory) / "oac-go-phase-a"
        _run([go, "build", "-trimpath", "-o", str(binary), "."], cwd=GO_IMPLEMENTATION)
        binary_digest = f"sha256:{hashlib.sha256(binary.read_bytes()).hexdigest()}"
        result = run_bundle(load_bundle(BUNDLE), (str(binary),))

        python_command = (sys.executable, "-m", "oac.ctk_adapter")
        go_command = (str(binary),)
        _check_direct_request_ceiling(python_command)
        _check_direct_request_ceiling(go_command)
        probes: list[tuple[str, str, dict[str, object], tuple[object, object, object]]] = [
            (
                "rfc8785-2^53",
                "canonicalize",
                _canonical_payload(b"9007199254740992"),
                (
                    "COMPLETED",
                    {
                        "canonicalBase64": "OTAwNzE5OTI1NDc0MDk5Mg==",
                        "digest": "sha256:c681da39d7273a6a24c15c9cac3a75526ff2ecf8ba4ee60346a0c70c8163bdb2",
                    },
                    None,
                ),
            ),
            (
                "rfc8785-exponent",
                "canonicalize",
                _canonical_payload(b"1e30"),
                (
                    "COMPLETED",
                    {
                        "canonicalBase64": "MWUrMzA=",
                        "digest": "sha256:7412d94bdf30adfa71080e057185e1a8de86e2e99a8350b011df8ac41ed5a6e3",
                    },
                    None,
                ),
            ),
            (
                "canonicalize-shape",
                "canonicalize",
                {"rawBase64": "e30=", "extra": True},
                ("ERROR", None, "CTK_INPUT_INVALID"),
            ),
            (
                "canonicalize-grammar",
                "canonicalize",
                _canonical_payload(b"NaN"),
                ("ERROR", None, "CORE_SCHEMA_INVALID"),
            ),
            (
                "canonicalize-i-json",
                "canonicalize",
                _canonical_payload(b'"\xff"'),
                ("ERROR", None, "NON_I_JSON"),
            ),
            (
                "identifier-schema",
                "deriveIdentifier",
                {"identifierKind": "evaluation", "fields": {}},
                ("ERROR", None, "CTK_INPUT_INVALID"),
            ),
            (
                "witness-shape",
                "witness",
                {"mode": "unknown"},
                ("ERROR", None, "CTK_INPUT_INVALID"),
            ),
            (
                "witness-grammar",
                "witness",
                {"mode": "decode", "witnessRef": "urn:a%2fb#/bad~2token"},
                ("ERROR", None, "APPLICABILITY_WITNESS_MISMATCH"),
            ),
            (
                "witness-white-space",
                "witness",
                {"mode": "encode", "resourceId": "\u3000", "pointer": ""},
                ("ERROR", None, "CTK_INPUT_INVALID"),
            ),
            (
                "witness-white-space-decode",
                "witness",
                {"mode": "decode", "witnessRef": "%E3%80%80#"},
                ("ERROR", None, "APPLICABILITY_WITNESS_MISMATCH"),
            ),
            (
                "witness-control-discriminator",
                "witness",
                {"mode": "encode", "resourceId": "\u001c", "pointer": ""},
                ("COMPLETED", {"witnessRef": "%1C#"}, None),
            ),
            (
                "witness-bom-discriminator",
                "witness",
                {"mode": "encode", "resourceId": "\ufeff", "pointer": ""},
                ("COMPLETED", {"witnessRef": "%EF%BB%BF#"}, None),
            ),
            (
                "kleene-schema",
                "strongKleene",
                {"operator": "all", "values": ["INVALID"]},
                ("ERROR", None, "CTK_INPUT_INVALID"),
            ),
            (
                "resource-empty",
                "resourceCheck",
                {},
                ("ERROR", None, "CTK_INPUT_INVALID"),
            ),
        ]
        for name, operation, payload, expected in probes:
            signatures = []
            for command in (python_command, go_command):
                observation = invoke(
                    command,
                    operation=operation,
                    payload=payload,
                    timeout_ms=5_000,
                    max_output_bytes=1_048_576,
                    max_json_depth=64,
                    max_request_bytes=1_048_576,
                )
                signatures.append(_probe_signature(observation))
            if signatures != [expected, expected]:
                raise AssertionError(
                    f"differential probe {name} failed: {signatures!r} != {expected!r}"
                )

    schema = json.loads((ROOT / "ctk/schemas/RunResult.schema.json").read_bytes())
    Draft202012Validator(schema).validate(result)
    if not result["requiredPassed"]:
        sys.stderr.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 1
    print(
        json.dumps(
            {
                "bundleDigest": result["bundleDigest"],
                "implementationId": result["capabilityStatement"]["implementationId"],
                "binaryDigest": binary_digest,
                "summary": result["summary"],
                "differentialProbes": len(probes),
                "requestBoundaryProbes": 2,
                "claimCeiling": "internally-authored cross-language differential seed",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
