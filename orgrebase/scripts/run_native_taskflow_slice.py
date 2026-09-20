#!/usr/bin/env python3
"""Run the pinned AgentTeams native Taskflow controlled-local slice."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from orgrebase.workspace.native_taskflow import run_native_taskflow_slice

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checkout",
        default=os.getenv("AGENTTEAMS_CHECKOUT", ""),
        help="Pinned AgentTeams checkout (or AGENTTEAMS_CHECKOUT).",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--lock", default=str(ROOT / "agentteams/teamharness-lock.json")
    )
    parser.add_argument(
        "--run-id", default="run:orgrebase:controlled-native:quote-001"
    )
    args = parser.parse_args()
    if not args.checkout:
        parser.error("--checkout or AGENTTEAMS_CHECKOUT is required")
    receipt = run_native_taskflow_slice(
        checkout=args.checkout,
        output_dir=args.output,
        lock_path=args.lock,
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "evidence_class": receipt["evidence_class"],
                "claim_boundary": receipt["claim_boundary"],
                "oac_integration_status": receipt["oac_integration_status"],
                "run_id": receipt["run_id"],
                "receipt_digest": receipt["receipt_digest"],
                "external_promotion_status": receipt["external_promotion_status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
