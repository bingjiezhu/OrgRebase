#!/usr/bin/env python3
"""Run the single-Pack Golden Competition Runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.workspace.competition_run import run_golden_competition

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--checkout",
        default=os.getenv(
            "AGENTTEAMS_CHECKOUT", str(default_agentteams_checkout(ROOT))
        ),
    )
    parser.add_argument(
        "--lock", default=str(ROOT / "agentteams" / "teamharness-lock.json")
    )
    parser.add_argument(
        "--pack",
        default=str(ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"),
    )
    parser.add_argument(
        "--model-provider",
        choices=("ollama-local", "vertex-ai", "deepseek"),
        default="ollama-local",
        help="Reviewer advisory provider; default remains the local offline path.",
    )
    parser.add_argument("--ollama-endpoint", default=None)
    parser.add_argument(
        "--vertex-project",
        default=None,
        help=(
            "Vertex project ID only (not a credential). Credentials are resolved "
            "from ORGREBASE_VERTEX_ACCESS_TOKEN, ORGREBASE_VERTEX_API_KEY, or ADC."
        ),
    )
    args = parser.parse_args()
    receipt = run_golden_competition(
        repo_root=ROOT,
        output_dir=args.output,
        checkout=args.checkout,
        lock_path=args.lock,
        pack_path=args.pack,
        model_provider=args.model_provider,
        ollama_endpoint=args.ollama_endpoint,
        vertex_project=args.vertex_project,
    )
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "run_id": receipt["run_id"],
                "digest": receipt["digest"],
                "model_provider": receipt.get("model_provider", args.model_provider),
                "canonical_target_writes": receipt["canonical_target_writes"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
