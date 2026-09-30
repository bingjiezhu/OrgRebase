"""Immutable local invalidation tombstone for experience source reads.

The marker is deliberately independent of the assessment service so every
consumer can check it without circular authority dependencies. A restored
backup that predates the marker still requires an external deletion ledger
before exposure; no in-database marker can prove that ledger's freshness.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.experience_contracts import CaseObservationV2

SOURCE_HOLD_MEDIA = "application/vnd.orgrebase.experience-source-hold+json"


class SourceInvalidationHold(ContentAddressedModel):
    schema_version: Literal["orgrebase.experience-source-hold.v1"] = (
        "orgrebase.experience-source-hold.v1"
    )
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    reason_code: Literal["PRIVATE_SOURCE_INVALIDATED"] = "PRIVATE_SOURCE_INVALIDATED"


def source_hold_ref(*, tenant_id: str, workspace_id: str, source_record_id: str) -> str:
    return "experience-source-hold:" + sha256_digest({
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "source_record_id": source_record_id,
    })[7:] + "@v1"


def load_source_hold(workspace: Any, source_record_id: str) -> SourceInvalidationHold | None:
    ref = source_hold_ref(
        tenant_id=workspace.store.tenant_id,
        workspace_id=workspace.store.workspace_id,
        source_record_id=source_record_id,
    )
    try:
        row = workspace.store.load_artifact(ref, SOURCE_HOLD_MEDIA)
    except KeyError:
        return None
    marker = SourceInvalidationHold.model_validate(row.payload).revalidated()
    if (
        marker.tenant_id != workspace.store.tenant_id
        or marker.workspace_id != workspace.store.workspace_id
        or marker.source_record_id != source_record_id
    ):
        raise IntegrityError("EXPERIENCE_SOURCE_HOLD_BINDING_INVALID")
    return marker


def is_case_source_held(workspace: Any, case: CaseObservationV2) -> bool:
    selected = CaseObservationV2.model_validate(case.model_dump(mode="json")).revalidated()
    if (
        selected.tenant_id != workspace.store.tenant_id
        or selected.workspace_id != workspace.store.workspace_id
    ):
        raise IntegrityError("EXPERIENCE_CASE_SOURCE_SCOPE_INVALID")
    return load_source_hold(workspace, selected.private_episode_ref) is not None


def require_source_not_held(workspace: Any, source_record_id: str) -> None:
    if load_source_hold(workspace, source_record_id) is not None:
        raise IntegrityError("EXPERIENCE_SOURCE_INVALIDATED")
