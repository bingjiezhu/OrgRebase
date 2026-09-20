#!/usr/bin/env python3
"""Run one exact-bound OAC candidate shadow execution."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.workspace.oac_shadow_execution import (
    run_oac_bound_shadow_execution,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--adapter-capsule", required=True)
    parser.add_argument("--activation-binding", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--context-envelope", required=True)
    parser.add_argument("--agent-mapping-receipt")
    parser.add_argument(
        "--pack",
        default=str(ROOT / "examples/enterprise-quote-pilot/evergreen"),
    )
    parser.add_argument(
        "--frozen-golden",
        default=str(ROOT / "evidence/golden-competition/latest/pilot"),
    )
    parser.add_argument(
        "--fencing-reference",
        default=str(
            ROOT
            / "evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json"
        ),
    )
    parser.add_argument(
        "--checkout",
        default=os.getenv(
            "AGENTTEAMS_CHECKOUT", str(default_agentteams_checkout(ROOT))
        ),
    )
    parser.add_argument(
        "--lock", default=str(ROOT / "agentteams/teamharness-lock.json")
    )
    parser.add_argument(
        "--model-provider",
        choices=("ollama-local", "vertex-ai", "deepseek"),
        default="ollama-local",
    )
    parser.add_argument("--ollama-endpoint")
    parser.add_argument("--vertex-project")
    args = parser.parse_args()
    receipt = run_oac_bound_shadow_execution(
        repo_root=ROOT,
        output_dir=args.output,
        checkout=args.checkout,
        lock_path=args.lock,
        pack_path=args.pack,
        frozen_golden_root=args.frozen_golden,
        adapter_capsule=args.adapter_capsule,
        activation_binding=args.activation_binding,
        approval=args.approval,
        context_envelope=args.context_envelope,
        agent_mapping_receipt=args.agent_mapping_receipt,
        late_attempt_fencing_receipt=args.fencing_reference,
        model_provider=args.model_provider,
        ollama_endpoint=args.ollama_endpoint,
        vertex_project=args.vertex_project,
    )
    print(
        json.dumps(
            {
                "status": receipt.status,
                "shadow_execution_run_id": receipt.shadow_execution_run_id,
                "digest": receipt.digest,
                "same_run_layers": receipt.same_run_layers,
                "canonical_target_writes": receipt.canonical_target_writes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
