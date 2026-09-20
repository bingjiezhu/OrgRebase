#!/usr/bin/env python3
"""Validate fixed-pool AgentTeams assets and report the explicit live state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

from orgrebase.digest import sha256_digest
from orgrebase.workspace.source_lock import (
    file_sha256,
    workspace_contract_digest,
)
from orgrebase.workspace.transport import WORKER_BY_DOMAIN, agentteams_status

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def static_check(
    asset: Path,
    source_lock_path: Path,
    identities_dir: Path,
    skill_path: Path,
) -> dict[str, object]:
    documents = tuple(item for item in yaml.safe_load_all(asset.read_text(encoding="utf-8")) if item)
    worker_docs = tuple(item for item in documents if item.get("kind") == "Worker")
    workers = {str(item.get("spec", {}).get("workerName")) for item in worker_docs}
    team = next((item for item in documents if item.get("kind") == "Team"), None)
    expected = set(WORKER_BY_DOMAIN.values())
    failures: list[str] = []
    if any(item.get("apiVersion") != "agentteams.io/v1beta1" for item in documents):
        failures.append("STALE_CRD_API_VERSION")
    if workers != expected or len(worker_docs) != len(expected):
        failures.append("WORKER_POOL_MISMATCH")
    if team is None:
        failures.append("TEAM_MISSING")
    else:
        members = team.get("spec", {}).get("workerMembers", [])
        if {item.get("name") for item in members} != {f"orgrebase-{name}" for name in expected}:
            failures.append("TEAM_MEMBERSHIP_MISMATCH")
        if sum(item.get("role") == "team_leader" for item in members) != 1:
            failures.append("TEAM_LEADER_CARDINALITY_INVALID")

    lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    worker_image = str(lock.get("runtime_images", {}).get("worker", ""))
    if lock.get("agentteams_version") != "v1.2.2":
        failures.append("AGENTTEAMS_VERSION_NOT_PINNED")
    if not _COMMIT.fullmatch(str(lock.get("source_commit", ""))):
        failures.append("AGENTTEAMS_COMMIT_NOT_PINNED")
    if "@sha256:" not in worker_image or ":latest" in worker_image:
        failures.append("WORKER_IMAGE_NOT_IMMUTABLE")
    if not _DIGEST.fullmatch(str(lock.get("skill_digest", ""))):
        failures.append("SKILL_DIGEST_INVALID")
    if not _DIGEST.fullmatch(str(lock.get("workspace_contract_digest", ""))):
        failures.append("WORKSPACE_DIGEST_INVALID")

    identity_files = sorted(identities_dir.glob("*.json"))
    identities = {json.loads(path.read_text(encoding="utf-8"))["worker_id"] for path in identity_files}
    if identities != expected:
        failures.append("IDENTITY_CONTRACT_SET_MISMATCH")

    try:
        expected_skill_digest = file_sha256(skill_path)
        expected_contract_digest = workspace_contract_digest(
            team_asset=asset,
            identities_dir=identities_dir,
            skill_path=skill_path,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        expected_skill_digest = None
        expected_contract_digest = None
        failures.append("WORKSPACE_CONTRACT_RECOMPUTATION_FAILED")
    else:
        if lock.get("skill_name") != "structured-domain-handoff":
            failures.append("WORKSPACE_SKILL_NAME_MISMATCH")
        if lock.get("skill_path") != skill_path.as_posix():
            failures.append("WORKSPACE_SKILL_PATH_MISMATCH")
        if lock.get("skill_digest") != expected_skill_digest:
            failures.append("WORKSPACE_SKILL_DIGEST_MISMATCH")
        if lock.get("workspace_contract_digest") != expected_contract_digest:
            failures.append("WORKSPACE_CONTRACT_DIGEST_MISMATCH")

    return {
        "status": "PASS" if not failures else "FAIL",
        "workers": sorted(workers),
        "source_lock_digest": sha256_digest(lock),
        "skill_digest": expected_skill_digest,
        "computed_transport_contract_digest": expected_contract_digest,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, default=Path("agentteams/workspace/team.yaml"))
    parser.add_argument(
        "--source-lock",
        type=Path,
        default=Path("agentteams/workspace/source-lock.json"),
    )
    parser.add_argument(
        "--identities",
        type=Path,
        default=Path("agentteams/workspace/identities"),
    )
    parser.add_argument(
        "--skill",
        type=Path,
        default=Path(
            "agentteams/workspace/skills/structured-domain-handoff/SKILL.md"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/workspace/latest/agentteams-check.json"),
    )
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    static = static_check(args.asset, args.source_lock, args.identities, args.skill)
    live = agentteams_status(source_lock_path=args.source_lock)
    result = {"static": static, "live": live, "target_writes": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if static["status"] != "PASS" or (
        args.require_live and live.get("evidence_class") != "LIVE_AGENTTEAMS"
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
