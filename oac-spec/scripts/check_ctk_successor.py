#!/usr/bin/env python3
"""Run one frozen successor suite through the existing Python and internal Go adapters."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ctk/runner/src"))

from oac_ctk_runner.bundle import load_bundle  # noqa: E402
from oac_ctk_runner.evidence import digest, verify_result  # noqa: E402
from oac_ctk_runner.runner import run_bundle  # noqa: E402


def _semantic(result: dict[str, Any]) -> dict[str, Any]:
    return {item["caseId"]: {"sutStatus": item["sutStatus"], "response": item["decodedResponse"],
                            "stage": item["stage"], "errorCode": item["errorCode"]}
            for item in result["diagnostics"] if item["testTarget"] == "SUT"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    go = shutil.which("go")
    if go is None:
        parser.error("Go is required for the internal differential gate")
    bundle = load_bundle(ROOT / "ctk/bundles/foundation-boundaries-v0.2")
    with tempfile.TemporaryDirectory(prefix="ctk-successor-adapters-") as temporary:
        binary = Path(temporary) / "oac-go-phase-a"
        implementation = ROOT / "implementations/go-phase-a"
        subprocess.run([go, "build", "-trimpath", "-buildvcs=false", "-o", str(binary), "."],
                       cwd=implementation, check=True)
        python = run_bundle(bundle, (sys.executable, "-m", "oac.ctk_adapter"),
                            build_inputs=tuple(sorted((ROOT / "src/oac").glob("*.py"))))
        internal_go = run_bundle(bundle, (str(binary),),
                                 build_inputs=(binary, *sorted(implementation.glob("*.go")), implementation / "go.mod"))
    for result in (python, internal_go):
        verify_result(result, bundle)
    left, right = _semantic(python), _semantic(internal_go)
    disagreements = [{"caseId": key, "python": left[key], "internalGo": right[key],
                      "reproducerDigest": digest(next(case for case in bundle.cases if case["caseId"] == key))}
                     for key in sorted(left) if left[key] != right[key]]
    summary = {"schemaVersion": "oac.ctk.internal-differential/v0.2", "bundleDigest": bundle.digest,
               "requirementSetDigest": python["requirementSetDigest"], "resourceProfileDigest": python["resourceProfileDigest"],
               "requiredPassed": python["requiredPassed"] and internal_go["requiredPassed"] and not disagreements,
               "python": python["targetSummary"], "internalGo": internal_go["targetSummary"],
               "sutComparisonCount": len(left), "differenceCount": len(disagreements),
               "differences": disagreements, "semanticProjectionDigest": digest(left),
               "claimBoundary": "INTERNALLY_AUTHORED_DIFFERENTIAL_NOT_INDEPENDENT_ORGANIZATION"}
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, value in (("python", python), ("internal-go", internal_go), ("summary", summary)):
            destination = args.output_dir / (name + ".json")
            with destination.open("x", encoding="utf-8") as output:
                output.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["requiredPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
