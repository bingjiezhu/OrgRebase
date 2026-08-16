#!/usr/bin/env python3
"""Freeze Workspace AgentTeams live evidence or emit an explicit NOT_RUN record.

A real deployment can export one self-contained JSON document containing the
CoalitionPlan, exact delegation tasks, run envelope, K8s identities, Matrix
membership/events, candidate artifact digests, and provider call identifiers.
This script verifies that document and writes a stable PASS envelope. Without an
input document it writes an explicit NOT_RUN record rather than fabricating live
fields.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.live_evidence import WorkspaceAgentTeamsEvidenceVerifier


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def collect(
    *,
    input_path: Path | None,
    source_lock_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    source_lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path is None or not input_path.is_file():
        result = {
            "schema_version": "orgrebase.workspace-agentteams-live.v1",
            "status": "NOT_RUN",
            "evidence_class": "NOT_RUN",
            "source_lock_digest": sha256_digest(source_lock),
            "target_writes": 0,
            "missing_prerequisites": [
                "Workspace Kubernetes Team/Worker export",
                "Workspace Matrix membership/event export",
                "Workspace candidate artifact bytes and digests",
                "Workspace provider request IDs",
            ],
        }
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    verification = WorkspaceAgentTeamsEvidenceVerifier().verify_embedded(
        payload=payload, source_lock=source_lock
    )
    result = dict(payload)
    result["verification"] = _jsonable(verification)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--source-lock",
        type=Path,
        default=Path("agentteams/workspace/source-lock.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/workspace/agentteams/live-evidence.json"),
    )
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    try:
        result = collect(
            input_path=args.input,
            source_lock_path=args.source_lock,
            output_path=args.output,
        )
    except (IntegrityError, ValueError, KeyError) as exc:
        result = {
            "schema_version": "orgrebase.workspace-agentteams-live.v1",
            "status": "REJECTED",
            "evidence_class": "NOT_RUN",
            "target_writes": 0,
            "error_code": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_jsonable))
    if args.require_live and result.get("evidence_class") != "LIVE_AGENTTEAMS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
