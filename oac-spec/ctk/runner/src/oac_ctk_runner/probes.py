"""Frozen harness probes exercise the same loader and adapter boundary as SUT runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .adapter import AdapterObservation, invoke
from .bundle import Bundle, BundleError, _json_bytes, load_bundle
from .contracts import HARNESS_PROBES

_BUNDLE_ERRORS = {
    "traversal": "unsafe artifact path", "absolute": "unsafe artifact path",
    "backslash": "non-portable separator", "nul": "non-portable separator",
    "duplicate": "duplicate artifact path", "casefold": "case-fold-colliding artifact path",
    "missing": "regular non-symlink file", "unlisted": "inventory mismatch",
    "digest": "artifact digest mismatch", "size": "artifact size mismatch",
    "symlink": "symlink", "hardlink": "hard-linked", "fifo": "regular",
}
_RESPONSES = {
    "malformed": ("sys.stdout.buffer.write(b'{')", "ERROR", "PARSE", "ADAPTER_RESPONSE_INVALID:JSONDecodeError"),
    "duplicate-key": ("print('{\"x\":1,\"x\":2}')", "ERROR", "PARSE", "ADAPTER_RESPONSE_INVALID:ValueError"),
    "invalid-utf8": ("sys.stdout.buffer.write(b'\\xff')", "ERROR", "PARSE", "ADAPTER_RESPONSE_INVALID:UnicodeDecodeError"),
    "wrong-request": ("print(json.dumps({'protocolVersion':'oac.ctk.stdio/v1','requestId':'other','sutStatus':'COMPLETED','result':{}}))", "ERROR", "SCHEMA", "ADAPTER_REQUEST_ID_MISMATCH"),
    "partial-exhaustion": ("print(json.dumps({'protocolVersion':'oac.ctk.stdio/v1','requestId':'request-1','sutStatus':'RESOURCE_EXHAUSTED','error':{'code':'CONFORMANCE_RESOURCE_PROFILE_EXCEEDED'},'result':{}}))", "ERROR", "SCHEMA", "ADAPTER_RESPONSE_FIELDS_INVALID"),
    "timeout": ("time.sleep(2)", "TIMEOUT", "ADAPTER", "ADAPTER_TIMEOUT"),
    "no-input-read": ("time.sleep(2)", "TIMEOUT", "ADAPTER", "ADAPTER_TIMEOUT"),
    "crash": ("sys.exit(7)", "CRASH", "ADAPTER", "ADAPTER_NONZERO_EXIT"),
    "output-overflow": ("sys.stdout.write('x'*129)", "RESOURCE_EXHAUSTED", "RESOURCE_EXHAUSTION", "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"),
}


def _bundle_probe(bundle: Bundle, attack: str) -> AdapterObservation:
    started = time.monotonic_ns()
    with tempfile.TemporaryDirectory(prefix="ctk-bundle-probe-") as directory:
        target = Path(directory) / "bundle"
        shutil.copytree(bundle.root, target, symlinks=True)
        # A pre-existing fault cannot masquerade as the requested attack.
        actual = set()
        for path in target.rglob("*"):
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise BundleError("HARNESS_PROBE_BASELINE_CHANGED")
            actual.add(path.relative_to(target).as_posix())
        if actual != {"bundle.json", *bundle.artifact_digests}:
            raise BundleError("HARNESS_PROBE_BASELINE_CHANGED")
        if _json_bytes((target / "bundle.json").read_bytes(), label="HARNESS_PROBE_BASELINE") != bundle.manifest or any(
            "sha256:" + hashlib.sha256((target / path).read_bytes()).hexdigest() != expected
            for path, expected in bundle.artifact_digests.items()
        ):
            raise BundleError("HARNESS_PROBE_BASELINE_CHANGED")
        manifest_path = target / "bundle.json"
        manifest = json.loads(manifest_path.read_bytes())
        entry = manifest["artifacts"][0]
        artifact = target / entry["path"]
        if attack in {"traversal", "absolute", "backslash", "nul"}:
            entry["path"] = {"traversal": "../outside", "absolute": "/outside",
                             "backslash": "bad\\path", "nul": "bad\0path"}[attack]
        elif attack in {"duplicate", "casefold"}:
            copied = dict(entry)
            if attack == "casefold":
                copied["path"] = entry["path"].upper()
            manifest["artifacts"].insert(1, copied)
        elif attack == "missing":
            artifact.unlink()
        elif attack == "unlisted":
            (target / "unlisted").write_bytes(b"not in ledger")
        elif attack == "digest":
            entry["digest"] = "sha256:" + "0" * 64
        elif attack == "size":
            entry["size"] += 1
        elif attack == "symlink":
            artifact.unlink()
            artifact.symlink_to(manifest_path)
        elif attack == "hardlink":
            os.link(artifact, target / "hardlink")
        elif attack == "fifo":
            os.mkfifo(target / "fifo")
        else:
            raise ValueError("HARNESS_PROBE_UNKNOWN")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        try:
            load_bundle(target)
        except BundleError as error:
            elapsed = (time.monotonic_ns() - started) // 1_000_000
            return AdapterObservation(
                "ERROR", None, "HARNESS", elapsed, "", "BUNDLE_REJECTED:" + str(error))
    return AdapterObservation("COMPLETED", None, "HARNESS", 0, "", None)


def run_probe(bundle: Bundle, case: dict[str, Any]) -> tuple[dict[str, Any], AdapterObservation]:
    probe = case["input"]["probe"]
    if probe not in HARNESS_PROBES or case["expect"] != {"harnessStatus": "BOUNDARY_ENFORCED"}:
        raise ValueError("HARNESS_PROBE_CONTRACT_INVALID")
    if probe.startswith("bundle-"):
        observation = _bundle_probe(bundle, probe.removeprefix("bundle-"))
    else:
        code = _RESPONSES[probe.removeprefix("response-")][0]
        no_read = probe == "response-no-input-read"
        prefix = "import sys,json,time;" + ("" if no_read else "sys.stdin.readline();")
        observation = invoke((sys.executable, "-I", "-c", prefix + code),
            operation="canonicalize", payload={"rawJson": "x" * 262_144 if no_read else "{}"},
            timeout_ms=50 if probe in {"response-timeout", "response-no-input-read"} else 5000,
            max_output_bytes=128 if probe == "response-output-overflow" else 4096,
            max_json_depth=64, max_request_bytes=1_048_576)
    return score_probe(case, observation), observation


def score_probe(case: dict[str, Any], observation: AdapterObservation) -> dict[str, Any]:
    """Score the frozen probe contract in both live execution and report verification."""
    probe = case["input"]["probe"]
    if probe not in HARNESS_PROBES or case["expect"] != {"harnessStatus": "BOUNDARY_ENFORCED"}:
        raise ValueError("HARNESS_PROBE_CONTRACT_INVALID")
    if probe.startswith("bundle-"):
        expected = _BUNDLE_ERRORS[probe.removeprefix("bundle-")]
        passed = (observation.sut_status == "ERROR" and observation.stage == "HARNESS"
                  and observation.response is None and observation.error_code is not None
                  and observation.error_code.startswith("BUNDLE_REJECTED:")
                  and expected in observation.error_code.removeprefix("BUNDLE_REJECTED:"))
    else:
        _, status, stage, error = _RESPONSES[probe.removeprefix("response-")]
        passed = (observation.sut_status, observation.stage, observation.error_code) == (status, stage, error)
    return {"caseId": case["caseId"], "testTarget": "HARNESS",
            "caseOutcome": "PASS" if passed else "HARNESS_ERROR",
            "sutStatus": "COMPLETED" if passed else "ERROR", "stage": "HARNESS",
            "reasonCodes": [] if passed else ["HARNESS_BOUNDARY_NOT_ENFORCED"],
            "domainVerdict": None, "elapsedMs": observation.elapsed_ms}
