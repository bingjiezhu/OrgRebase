#!/usr/bin/env python3
"""Verify a frozen Workspace AgentTeams evidence document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.live_evidence import WorkspaceAgentTeamsEvidenceVerifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument(
        "--source-lock",
        type=Path,
        default=Path("agentteams/workspace/source-lock.json"),
    )
    args = parser.parse_args()
    payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    source_lock = json.loads(args.source_lock.read_text(encoding="utf-8"))
    result = WorkspaceAgentTeamsEvidenceVerifier().verify_embedded(
        payload=payload, source_lock=source_lock
    )
    rendered = {
        key: (value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
        for key, value in result.items()
        if key not in {"transport_candidates"}
    }
    rendered["transport_candidate_count"] = len(result["transport_candidates"])
    print(json.dumps(rendered, ensure_ascii=False, indent=2, default=list))


if __name__ == "__main__":
    main()
