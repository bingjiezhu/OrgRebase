"""Synthetic enterprise fixture loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from orgrebase.digest import sha256_digest
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.domain import (
    AgentIdentity,
    DependencyEdge,
    DependencyManifest,
    VersionedObject,
)


class EnterpriseFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    organization_id: str
    revisions: dict[str, str]
    change: dict[str, Any]
    objects: tuple[VersionedObject, ...]
    dependencies: tuple[DependencyEdge, ...]
    dependency_manifests: tuple[DependencyManifest, ...]
    impact_targets: tuple[str, ...]
    context_profiles: dict[str, dict[str, Any]]
    agents: tuple[AgentIdentity, ...]
    evaluation_cases: tuple[dict[str, Any], ...]

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))

    def object_versions(self, object_id: str) -> tuple[VersionedObject, ...]:
        return tuple(item for item in self.objects if item.id == object_id)

    def object(self, object_id: str, version: str | None = None) -> VersionedObject:
        candidates = self.object_versions(object_id)
        if version is not None:
            candidates = tuple(item for item in candidates if item.version == version)
        else:
            current = tuple(item for item in candidates if item.state.value in {"CURRENT", "ACTIVE", "CANARY"})
            candidates = current or candidates
        if len(candidates) != 1:
            raise KeyError(f"expected one object for {object_id}@{version or 'current'}")
        return candidates[0]

    def dependency_manifest(self, target_id: str) -> DependencyManifest:
        target = self.object(target_id)
        candidates = tuple(
            item
            for item in self.dependency_manifests
            if item.target_id == target_id and item.target_version == target.version
        )
        if len(candidates) != 1:
            raise KeyError(
                f"expected one DependencyManifest for {target_id}@{target.version}"
            )
        return candidates[0]


def default_fixture_path() -> Path:
    return runtime_asset_path("fixtures/canonical-enterprise.json")


def load_fixture(path: str | Path | None = None) -> EnterpriseFixture:
    fixture_path = Path(path) if path else default_fixture_path()
    with fixture_path.open(encoding="utf-8") as handle:
        return EnterpriseFixture.model_validate(json.load(handle))
