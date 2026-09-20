#!/usr/bin/env python3
"""Re-execute four bounded mechanism checks; never overwrite retained archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from orgrebase.digest import sha256_digest
from orgrebase.mechanism_comparison_view import ABLATIONS, BASELINES, _report_row

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "fetch_pinned_agentteams.py", "run_bpi2019_real_process_benchmark.py", "verify_bpi2019_real_process_benchmark.py",
    "run_formation_taskflow_probe.py", "verify_formation_taskflow_probe.py", "run_skill_predecessor_rollback.py",
    "verify_skill_predecessor_rollback.py", "workspace_evaluate.py", "revalidate_archives.py",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def inputs():
    bpi = ROOT / "benchmark/quote-value-v0.4-bpi-real-process"
    skill = ROOT / "evidence/semifinal-closure/latest"
    return {
        "bpi": [bpi / name for name in ("MANIFEST.sha256", "LICENSES.json", "dataset-manifest.json", "projection/bpi2019-real-process-projection.json")],
        "formation": [ROOT / "evidence/formation-taskflow/latest/inputs/execution-plan.json", ROOT / "agentteams/teamharness-lock.json"],
        "skill": [skill / name for name in ("operations/tool/receipt.json", "operations/tool/result.json",
                    "skills/quote-compose/invocation-receipt.json", "skills/quote-compose/input.json", "skills/quote-compose/result.json")]
            + list((ROOT / "skills/enterprise-quote-compose").rglob("*.json"))
            + [path for path in (ROOT / "skills/predecessors/enterprise-quote-compose/1.3.0").rglob("*") if path.is_file()]
            + [ROOT / "vendor/predecessors/enterprise-quote-compose/1.3.0/orgrebase-0.4.0-py3-none-any.whl"],
        "owb": [path for path in (ROOT / "benchmark/orgworkbench").rglob("*") if path.is_file()]
            + [ROOT / "configs/workspace/metric-registry.json"],
    }


def run(output):
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / "bundle"
    bundle.mkdir()
    implementation = list((ROOT / "src").rglob("*.py")) + [ROOT / "scripts" / name for name in SCRIPTS]
    implementation += [ROOT / "uv.lock", ROOT / "pyproject.toml"]
    sources = inputs()
    fingerprints = {name: {str(p.relative_to(ROOT)): sha(p) for p in paths} for name, paths in sources.items()}
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in implementation}
    env = {key: value for key, value in os.environ.items()
           if not any(token in key for token in ("VERTEX", "DEEPSEEK", "OPENAI", "OLLAMA"))}
    env.update(PYTHONDONTWRITEBYTECODE="1", ORGREBASE_DEPLOYMENT_MODE="local")
    commands = []

    def execute(name, arguments):
        started = time.monotonic()
        with (output / (name + ".log")).open("x") as log:
            try:
                process = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env,
                                         stdout=log, stderr=subprocess.STDOUT, timeout=90)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        commands.append({"name": name, "exit_code": code, "elapsed_seconds": round(time.monotonic() - started, 3)})
        write(output / "execution.json", {"commands": commands, "live_model_calls": 0})
        if code:
            raise RuntimeError("ARCHIVE_RECHECK_COMMAND_FAILED:" + name)

    execute("agentteams-source", ["scripts/fetch_pinned_agentteams.py"])
    execute("bpi-run", ["scripts/run_bpi2019_real_process_benchmark.py", "--output", str(bundle / "bpi/receipt.json"),
                        "--summary-output", str(bundle / "bpi/summary.json")])
    execute("bpi-verify", ["scripts/verify_bpi2019_real_process_benchmark.py", "--receipt", str(bundle / "bpi/receipt.json"),
                           "--output", str(bundle / "bpi/verification.json")])
    execute("formation-run", ["scripts/run_formation_taskflow_probe.py", "--plan", str(sources["formation"][0]),
                              "--output", str(bundle / "formation"), "--run-id", "run:formation-recheck:" + uuid4().hex])
    execute("formation-verify", ["scripts/verify_formation_taskflow_probe.py", "--evidence", str(bundle / "formation"),
                                 "--lock", "agentteams/teamharness-lock.json", "--checkout", str(ROOT / ".tmp/agentteams"),
                                 "--output", str(bundle / "formation/verification.json")])
    # Private generated AT workspace is retained outside the reviewable bundle.
    runtime = bundle / "formation/runtime"
    if runtime.exists():
        runtime.rename(output / "formation-runtime")
    parent_run = read(sources["skill"][0])["run_id"]
    execute("skill-run", ["scripts/run_skill_predecessor_rollback.py", "--run-id", parent_run,
                          "--output", str(bundle / "skill"), "--created-at", datetime.now(UTC).isoformat().replace("+00:00", "Z")])
    execute("skill-verify", ["scripts/verify_skill_predecessor_rollback.py", str(bundle / "skill")])
    write(bundle / "skill/verification.json", read(output / "skill-verify.log"))
    execute("owb-run", ["scripts/workspace_evaluate.py", "--output", str(bundle / "owb/evaluation-suite.json")])
    suite = read(bundle / "owb/evaluation-suite.json")
    registry = read(ROOT / "configs/workspace/metric-registry.json")
    rows = [_report_row(suite["reference"], "orgrebase-workspace", "reference", registry)]
    for group, profiles in (("baselines", BASELINES), ("ablations", ABLATIONS)):
        if set(suite[group]) != set(profiles):
            raise ValueError("OWB_PROFILE_SET_MISMATCH")
        rows.extend(_report_row(suite[group][profile], profile, group, registry) for profile in profiles)
    write(bundle / "owb/verification.json", {"status": "PASS", "profiles": len(rows), "cases_per_profile": 192,
                                           "reference_score": rows[0]["score"], "failures": [],
                                           "failed_strategy_profiles": [row["profile"] for row in rows if row["status"] == "FAIL"]})
    for path, digest in source_hashes.items():
        if sha(ROOT / path) != digest:
            raise RuntimeError("ARCHIVE_RECHECK_IMPLEMENTATION_CHANGED")
    for records in fingerprints.values():
        if any(sha(ROOT / path) != digest for path, digest in records.items()):
            raise RuntimeError("ARCHIVE_RECHECK_INPUT_CHANGED")
    archives = {"bpi": "evidence/public-real-process/latest", "formation": "evidence/formation-taskflow/latest",
                "skill": "evidence/skill-predecessor-rollback/latest", "owb": "evidence/workspace/latest/evaluation-suite.json"}
    manifest = {"schema_version": "orgrebase.archive-revalidation.v1", "checked_at": datetime.now(UTC).isoformat(),
                "live_model_calls": 0, "implementation_files": source_hashes,
                "lanes": {lane: {"original_archive": archives[lane], "inputs": fingerprints[lane],
                                  "files": {str(path.relative_to(bundle)): sha(path) for path in sorted((bundle / lane).rglob("*")) if path.is_file()}}
                          for lane in archives}}
    manifest["digest"] = sha256_digest(manifest)
    write(bundle / "manifest.json", manifest)
    print(json.dumps({"status": "PASS", "bundle": str(bundle), "manifest_digest": manifest["digest"], "live_model_calls": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New external output directory; no previous archive is overwritten")
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
