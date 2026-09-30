"""Bounded, auditable action detail for one experience invalidation scan.

This supplements the stable v1 MemoryInvalidationReceipt.  It records what
was actually checked for each derivative layer without turning an unknown
external effect into a deletion claim.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.experience_assessment import ASSESSMENT_MEDIA
from orgrebase.workspace.experience_collection import OBSERVATION_MEDIA
from orgrebase.workspace.experience_contracts import CaseAssessmentReceipt, CaseObservationV2
from orgrebase.workspace.experience_source_hold import require_source_not_held

DETAIL_MEDIA = "application/vnd.orgrebase.memory-invalidation-detail+json"
Layer = Literal["summary", "lesson", "index", "cache", "inflight", "checkpoint"]
Action = Literal["STOP_USE", "PURGE", "REBUILD"]
Status = Literal["CONFIRMED", "PENDING", "UNKNOWN", "NOT_APPLICABLE"]


class ClosureAction(ContentAddressedModel):
    layer: Layer
    action: Action
    status: Status
    locator_refs: tuple[str, ...] = ()
    evidence_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    checked_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action(self):
        if tuple(sorted(set(self.locator_refs))) != self.locator_refs:
            raise ValueError("EXPERIENCE_CLOSURE_LOCATORS_NOT_CANONICAL")
        if self.status == "CONFIRMED" and self.evidence_digest is None:
            raise ValueError("EXPERIENCE_CLOSURE_CONFIRMATION_WITHOUT_EVIDENCE")
        if self.status == "NOT_APPLICABLE" and self.locator_refs:
            raise ValueError("EXPERIENCE_CLOSURE_APPLICABILITY_CONFLICT")
        return self


class MemoryClosureDetail(ContentAddressedModel):
    schema_version: Literal["orgrebase.memory-invalidation-detail.v1"] = (
        "orgrebase.memory-invalidation-detail.v1"
    )
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    receipt_ref: str = Field(min_length=1)
    receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    closure_scan_complete: bool
    affected_candidate_refs: tuple[str, ...]
    affected_private_candidate_refs: tuple[str, ...]
    actions: tuple[ClosureAction, ...] = Field(min_length=1)
    checked_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_detail(self):
        for refs in (self.affected_candidate_refs, self.affected_private_candidate_refs):
            if tuple(sorted(set(refs))) != refs:
                raise ValueError("EXPERIENCE_CLOSURE_LOCATORS_NOT_CANONICAL")
        pairs = tuple((item.layer, item.action) for item in self.actions)
        if pairs != tuple(sorted(set(pairs))):
            raise ValueError("EXPERIENCE_CLOSURE_ACTIONS_NOT_CANONICAL")
        if {item.layer for item in self.actions} != {
            "summary", "lesson", "index", "cache", "inflight", "checkpoint",
        }:
            raise ValueError("EXPERIENCE_CLOSURE_LAYERS_INCOMPLETE")
        return self


def closure_detail_ref(receipt_digest: str) -> str:
    return "experience-memory-invalidation-detail:" + receipt_digest[7:] + "@v1"


def require_assessment_source_not_held(workspace, assessment_ref: str) -> None:
    """Check the tombstone after the independent assessment has qualified."""

    receipt = CaseAssessmentReceipt.model_validate(
        workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
    ).revalidated()
    case = CaseObservationV2.model_validate(
        workspace.store.load_artifact(receipt.case_ref, OBSERVATION_MEDIA).payload,
    ).revalidated()
    if (
        receipt.case_digest != case.digest
        or receipt.tenant_id != workspace.store.tenant_id
        or receipt.workspace_id != workspace.store.workspace_id
        or case.tenant_id != workspace.store.tenant_id
        or case.workspace_id != workspace.store.workspace_id
    ):
        raise IntegrityError("EXPERIENCE_ASSESSMENT_SOURCE_BINDING_INVALID")
    require_source_not_held(workspace, case.private_episode_ref)
