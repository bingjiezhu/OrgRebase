"""Frozen price ceilings for one complete preview candidate attempt."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orgrebase.digest import sha256_digest


class ModelBudget(BaseModel):
    """Deployment-supplied ceilings, not a claim about an observed vendor bill.

    The input ceiling is the provider's full model context window. It must not
    be replaced by an unverified token estimate or an arbitrary smaller limit.
    Rates cover every applicable context and cache charge at the explicitly
    requested default service tier. Unknown prices are never filled in here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["orgrebase.model-budget.v1"]
    model_id: str = Field(min_length=1, max_length=128)
    context_window_tokens: int = Field(strict=True, gt=0, le=100_000_000)
    input_usd_per_million_ceiling: Decimal = Field(gt=0, max_digits=18, decimal_places=8)
    output_usd_per_million_ceiling: Decimal = Field(gt=0, max_digits=18, decimal_places=8)
    max_preview_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=8)
    price_source_ref: str = Field(min_length=1, max_length=2048)
    valid_until: str

    @field_validator("input_usd_per_million_ceiling", "output_usd_per_million_ceiling", "max_preview_usd", mode="before")
    @classmethod
    def decimal_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("MODEL_PRICE_REQUIRES_DECIMAL_STRING")
        return value

    @field_validator("valid_until")
    @classmethod
    def aware_expiry(cls, value: str) -> str:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError("MODEL_PRICE_EXPIRY_TIMEZONE_REQUIRED")
        return value

    @classmethod
    def from_file(cls, path: str | Path) -> ModelBudget:
        with Path(path).open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("MODEL_PRICE_CONTRACT_TOO_LARGE")

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("MODEL_PRICE_DUPLICATE_FIELD")
                result[key] = value
            return result

        return cls.model_validate(json.loads(raw, object_pairs_hook=unique))

    def require_current(self) -> None:
        if datetime.now(UTC) >= datetime.fromisoformat(self.valid_until.replace("Z", "+00:00")):
            raise ValueError("MODEL_PRICE_CONTRACT_EXPIRED")

    def reserve(self, *, model_id: str, calls: int, max_output_tokens: int) -> dict[str, object]:
        self.require_current()
        if model_id != self.model_id:
            raise ValueError("MODEL_PRICE_MODEL_MISMATCH")
        if calls <= 0 or not 0 < max_output_tokens <= self.context_window_tokens:
            raise ValueError("MODEL_PRICE_OUTPUT_LIMIT_INVALID")
        # USD per million tokens is numerically micro-USD per token. Round the
        # complete reservation up, and the authorized budget down, never float.
        reserved = int((calls * (
            self.context_window_tokens * self.input_usd_per_million_ceiling
            + max_output_tokens * self.output_usd_per_million_ceiling
        )).to_integral_value(rounding=ROUND_CEILING))
        limit = int(self.max_preview_usd * 1_000_000)
        if reserved > limit:
            raise ValueError("WORKSPACE_ADVISORY_COST_LIMIT_EXCEEDED")
        contract = self.model_dump(mode="json")
        return {
            "schema_version": "orgrebase.preview-cost-reservation.v1",
            "scope": "ONE_PREVIEW_ATTEMPT",
            "currency": "USD",
            "service_tier": "default",
            "contract": contract,
            "contract_digest": sha256_digest(contract),
            "reserved_microusd": reserved,
            "limit_microusd": limit,
            "calls": calls,
            "input_token_ceiling_per_call": self.context_window_tokens,
            "max_output_tokens_per_call": max_output_tokens,
        }
