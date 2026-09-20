"""Deterministic Workspace AgentTeams source-lock computation.

The lock binds the reviewed AgentTeams release and runtime image separately from
OrgRebase-owned bytes: the fixed Worker/Team asset, identity contracts, the
structured-domain-handoff Skill, capability cards, and transport schemas.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.workspace.models import (
    CoalitionPlan,
    DomainDelegationTask,
    DomainTransportCandidate,
    DomainTransportReceipt,
)
from orgrebase.workspace.templates import default_capability_cards

SOURCE_LOCK_SCHEMA = "orgrebase.agentteams-workspace-source-lock.v1"
WORKSPACE_SKILL_NAME = "structured-domain-handoff"
WORKSPACE_SKILL_VERSION = "1.0.0"
WORKSPACE_TRANSPORT_COMPILER_VERSION = "workspace-transport-compiler@1.0.0"


def file_sha256(path: str | Path) -> str:
    """Return a SHA-256 digest over exact file bytes."""

    value = Path(path).read_bytes()
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _load_identity_payloads(directory: str | Path) -> tuple[dict[str, Any], ...]:
    values: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        values.append({"file": path.name, "payload": payload})
    if not values:
        raise ValueError("workspace AgentTeams identity directory is empty")
    return tuple(values)


def workspace_contract_payload(
    *,
    team_asset: str | Path,
    identities_dir: str | Path,
    skill_path: str | Path,
) -> dict[str, Any]:
    """Build the exact OrgRebase-owned transport contract committed by the lock."""

    schemas = {
        model.__name__: model.model_json_schema(mode="validation")
        for model in (
            CoalitionPlan,
            DomainDelegationTask,
            DomainTransportCandidate,
            DomainTransportReceipt,
        )
    }
    cards = tuple(
        sorted(
            (item.model_dump(mode="json") for item in default_capability_cards()),
            key=lambda item: str(item["domain_id"]),
        )
    )
    return {
        "schema_version": "orgrebase.workspace-transport-contract.v1",
        "transport_compiler_version": WORKSPACE_TRANSPORT_COMPILER_VERSION,
        "team_asset": {
            "path": "agentteams/workspace/team.yaml",
            "sha256": file_sha256(team_asset),
        },
        "identity_contracts": _load_identity_payloads(identities_dir),
        "capability_cards": cards,
        "transport_schemas": schemas,
        "skill": {
            "name": WORKSPACE_SKILL_NAME,
            "version": WORKSPACE_SKILL_VERSION,
            "path": (
                "agentteams/workspace/skills/structured-domain-handoff/SKILL.md"
            ),
            "sha256": file_sha256(skill_path),
        },
    }


def workspace_contract_digest(
    *,
    team_asset: str | Path,
    identities_dir: str | Path,
    skill_path: str | Path,
) -> str:
    return sha256_digest(
        workspace_contract_payload(
            team_asset=team_asset,
            identities_dir=identities_dir,
            skill_path=skill_path,
        )
    )


def update_source_lock_owned_digests(
    lock: dict[str, Any],
    *,
    team_asset: str | Path,
    identities_dir: str | Path,
    skill_path: str | Path,
) -> dict[str, Any]:
    """Return a copy with OrgRebase-owned digests deterministically refreshed."""

    if lock.get("schema_version") != SOURCE_LOCK_SCHEMA:
        raise ValueError("unexpected Workspace AgentTeams source-lock schema")
    result = dict(lock)
    result.update(
        {
            "skill_name": WORKSPACE_SKILL_NAME,
            "skill_version": WORKSPACE_SKILL_VERSION,
            "skill_path": (
                "agentteams/workspace/skills/structured-domain-handoff/SKILL.md"
            ),
            "skill_digest": file_sha256(skill_path),
            "workspace_contract_digest": workspace_contract_digest(
                team_asset=team_asset,
                identities_dir=identities_dir,
                skill_path=skill_path,
            ),
        }
    )
    return result
