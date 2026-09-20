#!/usr/bin/env python3
"""Independently verify retained controlled-local native Taskflow evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from orgrebase.workspace.native_taskflow import verify_native_taskflow_evidence

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument(
        "--checkout",
        default=os.getenv("AGENTTEAMS_CHECKOUT", ""),
        help=(
            "Optional pinned AgentTeams checkout for fresh source replay; without it, "
            "verification is retained-lock replay only."
        ),
    )
    parser.add_argument(
        "--lock", default=str(ROOT / "agentteams/teamharness-lock.json")
    )
    args = parser.parse_args()
    result = verify_native_taskflow_evidence(
        evidence_dir=args.evidence,
        checkout=args.checkout or None,
        lock_path=args.lock,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
