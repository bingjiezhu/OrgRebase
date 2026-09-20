#!/usr/bin/env python3
"""Replay the Plan-verification capsule against an isolated wheel and Go build."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/plan-verification-portability/v0.1-seed-1"
SOURCE_SUMMARY = EXPERIMENT / "parity-summary.json"
INSTALLED_SUMMARY = EXPERIMENT / "replay/installed-parity-summary.json"
LEDGER_PATH = EXPERIMENT / "replay/installed-material-ledger.json"
HARNESS = ROOT / "scripts/check_plan_verification_parity.py"
PYTHON_ADAPTER_DIR = ROOT / "implementations/python-plan-verifier-reference"
GO_SOURCE = ROOT / "implementations/go-plan-verifier-v01-internal"


class ReplayFailure(RuntimeError):
    """The isolated replay did not preserve the frozen observation."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode:
        raise ReplayFailure(
            f"command failed ({completed.returncode}): {shlex.join(command)}\n"
            f"{completed.stderr[-4000:]}"
        )
    return completed.stdout


def _portable_projection(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "capsuleDigest": summary["capsuleDigest"],
        "capabilitySetDigest": summary["capabilitySetDigest"],
        "requirementSetDigest": summary["requirementSetDigest"],
        "protocolVersion": summary["protocolVersion"],
        "requiredCases": summary["requiredCases"],
        "python": summary["python"],
        "go": summary["go"],
        "observations": summary["observations"],
        "disagreements": summary["disagreements"],
        "mutants": summary["mutants"],
        "oracleNonEvasion": summary["oracleNonEvasion"],
        "independence": summary["independence"],
        "claim": summary["claim"],
        "pythonObservations": summary["pythonObservations"],
        "goObservations": summary["goObservations"],
    }


def _probe_installed(python: Path, working_directory: Path) -> dict[str, Any]:
    code = r"""
import hashlib, importlib.metadata, json
from pathlib import Path
import oac
module = Path(oac.__file__).resolve()
print(json.dumps({
  "modulePathClass": "isolated-venv-site-packages" if "site-packages" in module.as_posix() else "unexpected",
  "moduleRawSha256": "sha256:" + hashlib.sha256(module.read_bytes()).hexdigest(),
  "distributions": {name: importlib.metadata.version(name) for name in ("oac-contract", "pydantic", "rfc8785")},
}, sort_keys=True))
"""
    output = _run(
        [str(python), "-I", "-c", code],
        cwd=working_directory,
    )
    value = json.loads(output)
    if value.get("modulePathClass") != "isolated-venv-site-packages":
        raise ReplayFailure("Python SUT did not import the isolated wheel")
    return value


def replay() -> dict[str, Any]:
    uv = shutil.which("uv")
    go = shutil.which("go")
    if not uv or not go:
        raise ReplayFailure("uv and go are required for installed replay")
    if not SOURCE_SUMMARY.is_file():
        raise ReplayFailure("source parity summary is missing")
    source_summary = json.loads(SOURCE_SUMMARY.read_bytes())
    with tempfile.TemporaryDirectory(prefix="oac-plan-installed-") as temporary_name:
        temporary = Path(temporary_name)
        wheel_directory = temporary / "wheel"
        wheel_directory.mkdir()
        _run(
            [uv, "build", "--wheel", "--out-dir", str(wheel_directory)],
            cwd=ROOT,
        )
        wheels = sorted(wheel_directory.glob("*.whl"))
        if len(wheels) != 1:
            raise ReplayFailure("wheel build did not produce exactly one artifact")
        wheel = wheels[0]
        environment = temporary / "venv"
        _run(
            [uv, "venv", "--python", sys.executable, str(environment)],
            cwd=temporary,
        )
        installed_python = environment / "bin" / "python"
        if os.name == "nt":  # pragma: no cover - Windows fallback
            installed_python = environment / "Scripts" / "python.exe"
        _run(
            [uv, "pip", "install", "--python", str(installed_python), str(wheel)],
            cwd=temporary,
        )
        probe = _probe_installed(installed_python, temporary)

        go_binary = temporary / "oac-go-plan-verifier"
        build_environment = dict(os.environ)
        build_environment["GOCACHE"] = str(temporary / "go-build-cache")
        _run(
            [go, "build", "-trimpath", "-o", str(go_binary), "."],
            cwd=GO_SOURCE,
            env=build_environment,
        )
        INSTALLED_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(HARNESS),
            "--python-command",
            shlex.join([str(installed_python), str(PYTHON_ADAPTER_DIR / "adapter.py")]),
            "--go-command",
            shlex.join([str(go_binary)]),
            "--accept-all-command",
            shlex.join(
                [str(installed_python), str(PYTHON_ADAPTER_DIR / "accept_all_mutant.py")]
            ),
            "--reason-erasure-command",
            shlex.join(
                [
                    str(installed_python),
                    str(PYTHON_ADAPTER_DIR / "reason_erasure_mutant.py"),
                ]
            ),
            "--rule-erasure-command",
            shlex.join(
                [
                    str(installed_python),
                    str(PYTHON_ADAPTER_DIR / "rule_erasure_mutant.py"),
                ]
            ),
            "--skip-source-drift",
            "--output",
            str(INSTALLED_SUMMARY),
        ]
        _run(command, cwd=ROOT)
        installed_summary = json.loads(INSTALLED_SUMMARY.read_bytes())
        source_projection = _portable_projection(source_summary)
        installed_projection = _portable_projection(installed_summary)
        if source_projection != installed_projection:
            raise ReplayFailure("source and isolated-wheel portable observations differ")
        portable_digest = _digest(rfc8785.dumps(source_projection))
        adapter_sources = []
        for name in (
            "adapter.py",
            "accept_all_mutant.py",
            "reason_erasure_mutant.py",
            "rule_erasure_mutant.py",
        ):
            path = PYTHON_ADAPTER_DIR / name
            raw = path.read_bytes()
            adapter_sources.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "rawSha256": _digest(raw),
                    "sizeBytes": len(raw),
                }
            )
        ledger = {
            "apiVersion": "oac.plan-verification.installed-ledger/v0alpha1",
            "kind": "InstalledPlanVerificationLedger",
            "capsuleDigest": source_summary["capsuleDigest"],
            "sourceSummaryDigest": source_summary["summaryDigest"],
            "installedSummaryDigest": installed_summary["summaryDigest"],
            "portableProjectionDigest": portable_digest,
            "wheel": {
                "filename": wheel.name,
                "rawSha256": _digest(wheel.read_bytes()),
                "sizeBytes": wheel.stat().st_size,
            },
            "installedPython": probe,
            "pythonInterpreter": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "rawSha256": _digest(installed_python.resolve().read_bytes()),
            },
            "pythonAdapterSources": adapter_sources,
            "goBinary": {
                "rawSha256": _digest(go_binary.read_bytes()),
                "sizeBytes": go_binary.stat().st_size,
            },
            "observed": {
                "pythonPassed": installed_summary["python"]["passed"],
                "goPassed": installed_summary["go"]["passed"],
                "requiredCases": installed_summary["requiredCases"],
                "mutantsKilled": sum(
                    item["killed"] for item in installed_summary["mutants"]
                ),
                "scoredIndeterminate": installed_summary["observations"][
                    "scoredIndeterminate"
                ],
            },
            "provenanceClass": "self-attested isolated-process replay",
            "exclusions": [
                "not cryptographic execution provenance",
                "not clean-archive reproduction",
                "not organizational independence",
                "not complete Supplier conformance",
                "not enterprise validation",
            ],
        }
        ledger["ledgerDigest"] = _digest(rfc8785.dumps(ledger))
        LEDGER_PATH.write_bytes(rfc8785.dumps(ledger) + b"\n")
        return ledger


def main() -> int:
    try:
        ledger = replay()
    except (ReplayFailure, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"installed Plan-verification replay failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ledgerDigest": ledger["ledgerDigest"],
                "portableProjectionDigest": ledger["portableProjectionDigest"],
                **ledger["observed"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
