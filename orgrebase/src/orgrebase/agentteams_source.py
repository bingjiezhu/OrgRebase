"""Resolve the active AgentTeams source identity and its release artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from orgrebase.resource_paths import PROJECT_ROOT, runtime_asset_path


@dataclass(frozen=True)
class AgentTeamsSource:
    repository: str
    tag: str
    commit: str
    crd_api_version: str

    def require_teamharness_identity(self, lock: Mapping[str, Any]) -> None:
        if (lock.get("upstream"), lock.get("tag"), lock.get("commit")) != (
            self.repository, self.tag, self.commit
        ):
            raise ValueError("AGENTTEAMS_SOURCE_IDENTITY_MISMATCH")


def load_agentteams_source(project_root: Path | None = None) -> AgentTeamsSource:
    path = (
        Path(project_root) / "agentteams/source-lock.json"
        if project_root is not None
        else runtime_asset_path("agentteams/source-lock.json")
    )
    lock = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(lock, dict)
        or set(lock) != {"schema_version", "repository", "tag", "commit", "crd_api_version"}
        or lock.get("schema_version") != "orgrebase.agentteams-source-lock.v1"
        or lock.get("repository") != "https://github.com/agentscope-ai/AgentTeams"
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(lock.get("tag", "")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(lock.get("commit", "")))
        or lock.get("crd_api_version") != "agentteams.io/v1beta1"
    ):
        raise ValueError("AGENTTEAMS_SOURCE_LOCK_INVALID")
    return AgentTeamsSource(**{key: value for key, value in lock.items() if key != "schema_version"})


def load_teamharness_lock(project_root: Path | None = None) -> dict[str, Any]:
    path = (
        Path(project_root) / "agentteams/teamharness-lock.json"
        if project_root is not None
        else runtime_asset_path("agentteams/teamharness-lock.json")
    )
    lock = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("schema_version") != "orgrebase.teamharness-source-lock.v1":
        raise ValueError("TEAMHARNESS_SOURCE_LOCK_INVALID")
    load_agentteams_source(project_root).require_teamharness_identity(lock)
    return lock


def packaged_agentteams_bundle(project_root: Path | None = None) -> Path:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    descriptor = load_teamharness_lock(root).get("offline_bundle")
    if not isinstance(descriptor, dict):
        raise ValueError("AGENTTEAMS_BUNDLE_DESCRIPTOR_INVALID")
    relative = descriptor.get("path")
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("AGENTTEAMS_BUNDLE_PATH_INVALID")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts or parts.parts[:2] != ("vendor", "agentteams"):
        raise ValueError("AGENTTEAMS_BUNDLE_PATH_ESCAPE")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("AGENTTEAMS_BUNDLE_PATH_ESCAPE")
    return path


def default_agentteams_checkout(project_root: Path | None = None) -> Path:
    return (Path(project_root) if project_root is not None else PROJECT_ROOT) / ".tmp/agentteams"
