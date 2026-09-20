#!/usr/bin/env python3
"""Generate the controlled-local Source, Tool, OTLP and operations pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.controlled_local_evidence import run_controlled_local_evidence

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--delegation-id", required=True)
    parser.add_argument("--native-receipt-digest", required=True)
    parser.add_argument("--skill-package-digest", required=True)
    parser.add_argument("--skill-invocation-receipt-digest", required=True)
    parser.add_argument("--graph-digest", required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument(
        "--deployment-profile",
        type=Path,
        default=ROOT / "configs/deployment/controlled-local.json",
    )
    parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    parser.add_argument("--pyproject", type=Path, default=ROOT / "pyproject.toml")
    args = parser.parse_args()
    summary = run_controlled_local_evidence(
        output_dir=args.output_dir,
        run_id=args.run_id,
        task_id=args.task_id,
        delegation_id=args.delegation_id,
        native_receipt_digest=args.native_receipt_digest,
        skill_package_digest=args.skill_package_digest,
        skill_invocation_receipt_digest=args.skill_invocation_receipt_digest,
        graph_digest=args.graph_digest,
        artifact_paths=tuple(args.artifact),
        deployment_profile_path=args.deployment_profile,
        lock_path=args.lock,
        pyproject_path=args.pyproject,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
