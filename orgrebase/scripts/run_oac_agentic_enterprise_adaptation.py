#!/usr/bin/env python3
"""Run the complete live OAC intake -> actor-labelled gate -> bound shadow proof.

This is the reproducible evidence entrypoint for the Agent-assisted enterprise
adaptation path.  It never falls back to the deterministic mapper, never makes
a gate decision before the four-second server deadline, and never executes a
canonical business write.  The scripted decision proves the gate and actor
label only; it is not evidence that a real human clicked an approval control.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.workspace.oac_agentic_runtime import OACAgenticRuntime
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACQuoteAdaptationService,
)
from orgrebase.workspace.oac_shadow_execution import run_oac_bound_shadow_execution
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pack",
        type=Path,
        default=ROOT / "examples/enterprise-quote-pilot/evergreen",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence/oac-bound-shadow/latest",
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        default=Path(
            os.getenv(
                "AGENTTEAMS_CHECKOUT",
                str(default_agentteams_checkout(ROOT)),
            )
        ),
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=ROOT / "agentteams/teamharness-lock.json",
    )
    parser.add_argument(
        "--oac-root",
        type=Path,
        default=ROOT.parent / "oac-spec",
    )
    parser.add_argument(
        "--frozen-golden",
        type=Path,
        default=ROOT / "evidence/golden-competition/latest/pilot",
    )
    parser.add_argument(
        "--fencing-reference",
        type=Path,
        default=(
            ROOT
            / "evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json"
        ),
    )
    parser.add_argument("--vertex-project")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise SystemExit("OAC_AGENTIC_OUTPUT_MUST_NOT_EXIST")
    output.parent.mkdir(parents=True, exist_ok=True)

    runtime = load_enterprise_quote_pilot_pack(args.pack)
    with tempfile.TemporaryDirectory(prefix="orgrebase-oac-agentic-") as raw_runtime_root:
        runtime_root = Path(raw_runtime_root)
        workspace = WorkspaceService(
            store_path=runtime_root / "workspace.sqlite3",
            runtime_configuration=runtime,
            competition_mode="golden",
            competition_checkout=args.checkout,
            competition_lock_path=args.lock,
            competition_model_provider="vertex-ai",
            competition_vertex_project=args.vertex_project,
        )
        try:
            adaptation = OACQuoteAdaptationService(
                store=workspace.store,
                profile=workspace.profile,
                runtime=workspace.runtime_configuration,
                oac_root=args.oac_root,
                golden_root=args.frozen_golden,
                review_duration_seconds=4,
            )
            coordinator = OACAgenticRuntime(
                runtime_root=runtime_root / "coordinator",
                repo_root=ROOT,
                checkout=args.checkout,
                lock_path=args.lock,
                pack_path=args.pack,
                frozen_golden_root=args.frozen_golden,
                adaptation_service=adaptation,
                workspace_service=workspace,
                shadow_model_provider="vertex-ai",
                vertex_project=args.vertex_project,
                late_attempt_fencing_receipt=args.fencing_reference,
                shadow_runner=run_oac_bound_shadow_execution,
            )
            prepared = coordinator.agent_prepare(
                command_id="command:oac-agentic-full-chain:prepare@v1"
            )
            if prepared["status"] != "OWNER_REVIEW_PENDING":
                mapping = prepared.get("agent_mapping") or {}
                raise SystemExit(
                    "OAC_AGENTIC_LIVE_MAPPING_NOT_VALIDATED:"
                    f"{mapping.get('model_status')}:{mapping.get('status')}"
                )
            review_remaining_ms = int(
                (prepared.get("adaptation") or {}).get("review_remaining_ms") or 0
            )
            time.sleep(max(4.0, review_remaining_ms / 1000 + 0.05))
            candidate = prepared["adaptation"]
            adaptation.approve(
                actor_id=str(candidate["owner_ref"]),
                candidate_digest=str(candidate["candidate_digest"]),
                command_id="command:oac-agentic-full-chain:actor-labelled-gate@v1",
                owner_review_summary_digest=str(
                    candidate["owner_review_summary_digest"]
                ),
                acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
            )
            completed = coordinator.execute_shadow()
            if completed["status"] != "SHADOW_COMPLETED":
                raise SystemExit("OAC_AGENTIC_SHADOW_NOT_COMPLETED")
            shadow_source = runtime_root / "coordinator" / "shadow-execution"
            os.replace(shadow_source, output)
        finally:
            workspace.close()

    verification_path = output / "verification.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_oac_bound_shadow_execution.py"),
            "--root",
            str(output),
            "--pack",
            str(args.pack),
            "--frozen-golden",
            str(args.frozen_golden),
            "--fencing-reference",
            str(args.fencing_reference),
            "--output",
            str(verification_path),
        ],
        cwd=ROOT,
        check=True,
        timeout=60,
    )
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": verification["status"],
                "adaptation_run_id": receipt["adaptation_run_id"],
                "shadow_execution_run_id": receipt["shadow_execution_run_id"],
                "same_run_layers": receipt["same_run_layers"],
                "live_agent_mapping_bound": bool(
                    receipt.get("agent_mapping_receipt_digest")
                ),
                "actor_labelled_gate_decision_bound": bool(
                    receipt.get("approval_digest")
                ),
                "human_identity_proof_claimed": False,
                "canonical_target_writes": receipt["canonical_target_writes"],
                "verification_digest": verification["digest"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
