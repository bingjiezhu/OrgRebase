#!/usr/bin/env python3
"""Run one sealed Product/Legal formation topology through TeamHarness."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.workspace.agentteams_execution_plan import AgentTeamsExecutionPlan
from orgrebase.workspace.formation_taskflow_probe import run_formation_taskflow_probe

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--checkout",
        default=os.getenv(
            "AGENTTEAMS_CHECKOUT",
            str(default_agentteams_checkout(ROOT)),
        ),
    )
    parser.add_argument(
        "--lock",
        default=str(ROOT / "agentteams/teamharness-lock.json"),
    )
    args = parser.parse_args()
    plan = AgentTeamsExecutionPlan.model_validate(json.loads(Path(args.plan).read_text(encoding="utf-8")))
    receipt = run_formation_taskflow_probe(
        plan=plan,
        checkout=args.checkout,
        lock_path=args.lock,
        output_dir=args.output,
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "evidence_class": receipt.evidence_class,
                "run_id": receipt.run_id,
                "execution_plan_digest": receipt.execution_plan_digest,
                "selected_domain_ids": receipt.selected_domain_ids,
                "actual_task_ids": receipt.actual_terminal_task_ids,
                "agentteams_actions": receipt.agentteams_action_count,
                "project_terminal_state": receipt.project_terminal_state,
                "candidate_only": receipt.candidate_only,
                "canonical_target_writes": receipt.canonical_target_writes,
                "receipt_digest": receipt.digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
