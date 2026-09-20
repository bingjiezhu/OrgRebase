"""Cross-layer runtime contracts shared by Core and Workspace.

This module is deliberately independent of :mod:`orgrebase.workspace`.  Core
storage and workflow code may depend on these small value objects without
reversing the documented Core -> Workspace dependency direction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from orgrebase.digest import canonical_json, sha256_digest


class RuntimeContract(BaseModel):
    """Immutable, closed value object used at runtime layer boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StoredArtifact(RuntimeContract):
    artifact_id: str
    media_type: str
    payload: dict[str, JsonValue]
    payload_digest: str


class ArtifactWrite(RuntimeContract):
    artifact_id: str
    media_type: str
    payload: dict[str, JsonValue]
    payload_digest: str


class WorkflowIdentity(RuntimeContract):
    namespace: str
    approval_prefix: str
    receipt_prefix: str
    coordination_prefix: str
    idempotency_prefix: str
    extension_receipt_prefix: str


def prepare_artifact_write(
    artifact_id: str,
    media_type: str,
    payload: BaseModel | Mapping[str, Any],
) -> ArtifactWrite:
    """Freeze a model or mapping into one detached, digest-bound artifact write."""

    if isinstance(payload, BaseModel):
        raw = payload.model_dump(mode="json")
    elif isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raise TypeError("artifact payload must be a Pydantic model or mapping")
    frozen = json.loads(canonical_json(raw))
    if not isinstance(frozen, dict):  # pragma: no cover - Mapping guarantees an object
        raise TypeError("artifact payload must encode one JSON object")
    return ArtifactWrite(
        artifact_id=artifact_id,
        media_type=media_type,
        payload=frozen,
        payload_digest=sha256_digest(frozen),
    )
