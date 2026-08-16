"""Generate a no-write control-plane decision receipt from live candidate artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from orgrebase.agentteams_ingest import AgentTeamsCandidateIngestor
from orgrebase.impact import build_change_set
from orgrebase.service import OrgRebaseService


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-receipt", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    service = OrgRebaseService()
    change_set = build_change_set(service.fixture)
    preview = service.impact_engine.preview(change_set)
    orchestration_plan = service.collaboration_adapter.orchestration_compiler.compile(
        change_set, preview
    )
    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=_object(args.live_receipt),
        artifact_manifest=_object(args.artifact_manifest),
        artifact_root=args.artifact_root,
        change_set=change_set,
        preview=preview,
        orchestration_plan=orchestration_plan,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "receipt_digest": receipt.digest,
                "admitted": len(receipt.admitted_candidate_digests),
                "rejected": len(receipt.rejected_candidate_digests),
                "target_writes": receipt.target_writes,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
