#!/usr/bin/env python3
"""Run the pinned AgentTeams + Vertex candidate-only OAC intake slice."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.store import StateStore
from orgrebase.workspace.oac_agent_adaptation import (
    reseal_oac_agent_adaptation_evidence,
    run_oac_agent_adaptation,
)
from orgrebase.workspace.oac_quote_adaptation import OACQuoteAdaptationService
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
        default=ROOT / "evidence/oac-agentic-adaptation/latest",
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        default=Path(os.getenv("AGENTTEAMS_CHECKOUT", str(default_agentteams_checkout(ROOT)))),
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
        "--vertex-project",
        default=None,
        help="Optional Vertex project override; the value itself is never persisted.",
    )
    args = parser.parse_args()
    runtime = load_enterprise_quote_pilot_pack(args.pack)
    output = args.output.expanduser().resolve()
    with (
        tempfile.TemporaryDirectory(prefix="orgrebase-oac-agent-store-") as raw,
        StateStore(Path(raw) / "adaptation.sqlite3") as store,
    ):
        service = OACQuoteAdaptationService(
            store=store,
            profile=runtime.profile,
            runtime=runtime,
            oac_root=args.oac_root,
        )
        baseline, _gaps = service.deterministic_mapping_baseline()
        receipt = run_oac_agent_adaptation(
            checkout=args.checkout,
            lock_path=args.lock,
            output_dir=output,
            profile=runtime.profile,
            runtime=runtime,
            baseline_mappings=baseline,
            adaptation_run_id=service.adaptation_run_id,
            vertex_project=args.vertex_project,
        )
        if receipt.status == "VALIDATED_CANDIDATE":
            draft = service.prepare(
                command_id="command:oac-agent-adaptation:prepare@v1",
                agent_mapping_receipt=receipt.model_dump(mode="json"),
            )
            lane = "LIVE_AGENT_CANDIDATE_TO_OWNER_REVIEW"
        else:
            # The reproducible baseline remains useful, but this is a
            # distinct lane and never changes the live Agent receipt.
            draft = service.prepare(command_id="command:oac-deterministic-baseline:prepare@v1")
            lane = "LIVE_AGENT_HOLD_WITH_SEPARATE_DETERMINISTIC_BASELINE"
        _write(output / "quote-adaptation-draft.json", draft)
    manifest = reseal_oac_agent_adaptation_evidence(output)
    print(
        json.dumps(
            {
                "status": receipt.status,
                "lane": lane,
                "adaptation_run_id": receipt.adaptation_run_id,
                "receipt_digest": receipt.digest,
                "model_status": receipt.model_observation.status,
                "model_id": receipt.model_observation.model_id,
                "provider_request_id_present": bool(receipt.model_observation.provider_request_id),
                "native_actions": list(receipt.native_agentteams.action_sequence),
                "draft_status": draft["status"],
                "review_remaining_ms": draft.get("review_remaining_ms"),
                "canonical_target_writes": receipt.canonical_target_writes,
                "manifest_digest": manifest["digest"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    # HOLD is an honest, successfully produced evidence state (for example
    # when credentials are absent); only producer exceptions are process
    # failures.  Automation must inspect the printed status rather than infer
    # a live model call from the exit code.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
