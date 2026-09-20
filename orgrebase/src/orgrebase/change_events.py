"""Immutable source change contract shared by workflow and storage projections."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from orgrebase.domain import ContentAddressedModel, ObjectState, VersionedObject


class ChangeEvent(ContentAddressedModel):
    """One immutable source proposal, conditional on an exact current version."""

    event_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    organization_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    base_version: str = Field(min_length=1)
    base_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    proposal: VersionedObject
    occurred_at: str
    purpose: Literal["change_rebase"] = "change_rebase"
    operation: Literal["UPDATE", "READMIT"] = "UPDATE"

    @model_validator(mode="after")
    def validate_proposal(self) -> ChangeEvent:
        observed = datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("CHANGE_EVENT_TIMEZONE_REQUIRED")
        effective = datetime.fromisoformat(self.proposal.valid_from.replace("Z", "+00:00"))
        if effective.tzinfo is None:
            raise ValueError("CHANGE_EVENT_EFFECTIVE_TIMEZONE_REQUIRED")
        if self.proposal.valid_to is not None:
            expires = datetime.fromisoformat(self.proposal.valid_to.replace("Z", "+00:00"))
            if expires.tzinfo is None or expires <= effective:
                raise ValueError("CHANGE_EVENT_EFFECTIVE_INTERVAL_INVALID")
        if self.proposal.state != ObjectState.PROPOSED:
            raise ValueError("CHANGE_EVENT_PROPOSAL_STATE_INVALID")
        if self.proposal.version == self.base_version:
            raise ValueError("CHANGE_EVENT_NEW_VERSION_REQUIRED")
        if not self.proposal.source_refs:
            raise ValueError("CHANGE_EVENT_SOURCE_REQUIRED")
        return self

