"""Deterministic quote arithmetic for explicitly supplied prices and tax policy.

Prices have one basket currency. This module supplies no exchange rates, tax-law
decisions, shipping fees, tier pricing, or inferred commercial policy.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orgrebase.digest import sha256_digest

CURRENCY_MINOR_UNITS = MappingProxyType({"GBP": 2, "USD": 2, "EUR": 2, "CNY": 2, "JPY": 0, "KWD": 3})
NonemptyText = Annotated[str, Field(min_length=1, max_length=2000)]
Currency = Literal["GBP", "USD", "EUR", "CNY", "JPY", "KWD"]
UNIT_PRICE_PATTERN = re.compile(r"(?:0|[0-9]{1,12})(?:\.[0-9]{1,18})?\Z")
ROUNDING_DESCRIPTION = (
    "HALF_UP to currency minor units: each quantity times unit price first; "
    "sum rounded lines; round subtotal times discount_bps / 10000 once; "
    "subtract discount; round net times tax_bps / 10000 once; add net and tax."
)


class PricingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def reject_blank_text(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Text must not be blank")
        return value


class QuoteLineInput(PricingModel):
    line_id: NonemptyText
    sku: NonemptyText
    description: NonemptyText
    quantity: Annotated[int, Field(ge=1, le=1_000_000)]
    unit_price: Annotated[str, Field(min_length=1, max_length=31)]

    @field_validator("unit_price")
    @classmethod
    def validate_unit_price(cls, value: str) -> str:
        # Preserve decimal precision; never ingest prices through float.
        if not UNIT_PRICE_PATTERN.fullmatch(value):
            raise ValueError("Unit price must be a nonnegative plain decimal with at most 12 integer and 18 fractional digits")
        return value


class QuoteBasket(PricingModel):
    currency: Currency
    items: Annotated[tuple[QuoteLineInput, ...], Field(min_length=1, max_length=200)]
    source_ref: NonemptyText

    @model_validator(mode="after")
    def unique_line_ids(self) -> QuoteBasket:
        if len({item.line_id for item in self.items}) != len(self.items):
            raise ValueError("Quote line_id values must be unique")
        return self


class PricingPolicy(PricingModel):
    discount_bps: Annotated[int, Field(ge=0, le=10000)] = 0
    tax_bps: Annotated[int, Field(ge=0, le=10000)] = 0
    tax_label: NonemptyText
    tax_mode: Literal["EXCLUSIVE"] = "EXCLUSIVE"
    rounding: Literal["HALF_UP"] = "HALF_UP"
    source_ref: NonemptyText


class PricedQuoteLine(PricingModel):
    line_id: str
    sku: str
    description: str
    quantity: int
    unit_price: str
    line_total: str


class PricedQuote(PricingModel):
    currency: Currency
    minor_units: int
    lines: Annotated[tuple[PricedQuoteLine, ...], Field(strict=False)]
    subtotal: str
    discount_rate_bps: int
    discount_amount: str
    net_amount: str
    tax_rate_bps: int
    tax_label: str
    tax_amount: str
    total: str
    rounding_description: str
    basket_source_ref: str
    policy_source_ref: str
    basket_digest: str
    policy_digest: str


def calculate_quote(basket: QuoteBasket, policy: PricingPolicy, *, currency: str) -> PricedQuote:
    """Calculate an exclusive-tax quote using only validated explicit inputs."""
    if currency not in CURRENCY_MINOR_UNITS:
        raise ValueError(f"Unsupported quote currency: {currency}")
    if basket.currency != currency:
        raise ValueError("Basket and quote currencies must match; no FX conversion is performed")
    minor_units = CURRENCY_MINOR_UNITS[currency]
    quantum = Decimal(1).scaleb(-minor_units)
    with localcontext() as context:
        # The input bounds require fewer than 50 significant digits even before
        # rounding; 80 therefore preserves every permitted multiplication/sum.
        context.prec = 80
        lines = []
        subtotal = Decimal(0)
        for item in basket.items:
            amount = (Decimal(item.unit_price) * item.quantity).quantize(quantum, rounding=ROUND_HALF_UP)
            subtotal += amount
            lines.append(PricedQuoteLine(
                line_id=item.line_id, sku=item.sku, description=item.description,
                quantity=item.quantity, unit_price=item.unit_price,
                line_total=format(amount, f".{minor_units}f"),
            ))
        discount = (subtotal * policy.discount_bps / 10000).quantize(quantum, rounding=ROUND_HALF_UP)
        net = subtotal - discount
        tax = (net * policy.tax_bps / 10000).quantize(quantum, rounding=ROUND_HALF_UP)
        total = net + tax
        return PricedQuote(
            currency=basket.currency, minor_units=minor_units, lines=tuple(lines),
            subtotal=format(subtotal, f".{minor_units}f"),
            discount_rate_bps=policy.discount_bps, discount_amount=format(discount, f".{minor_units}f"),
            net_amount=format(net, f".{minor_units}f"),
            tax_rate_bps=policy.tax_bps, tax_label=policy.tax_label,
            tax_amount=format(tax, f".{minor_units}f"), total=format(total, f".{minor_units}f"),
            rounding_description=ROUNDING_DESCRIPTION,
            basket_source_ref=basket.source_ref, policy_source_ref=policy.source_ref,
            basket_digest=sha256_digest(basket.model_dump(mode="json")),
            policy_digest=sha256_digest(policy.model_dump(mode="json")),
        )
