"""Independent evidence pricing oracle: source-bound amounts cannot be re-sealed away."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location("golden_quote_pricing", ROOT / "scripts/verify_golden_pilot_evidence.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def case(verifier):
    basket = {"currency": "USD", "source_ref": "source:basket@v1", "items": [
        {"line_id": "a", "sku": "A", "description": "Three precise units", "quantity": 3, "unit_price": "0.335"},
        {"line_id": "b", "sku": "B", "description": "Original decimal lexeme", "quantity": 1,
         "unit_price": "2.6750000000000003"},
    ]}
    # Defaults must be included in the policy digest, as in the declared model.
    policy = {"discount_bps": 500, "tax_bps": 2000, "tax_label": "Controlled test tax",
              "source_ref": "source:policy@v1"}
    normalized_policy = {**policy, "tax_mode": "EXCLUSIVE", "rounding": "HALF_UP"}
    pricing = {
        "currency": "USD", "minor_units": 2,
        "lines": [{**basket["items"][0], "line_total": "1.01"}, {**basket["items"][1], "line_total": "2.68"}],
        "subtotal": "3.69", "discount_rate_bps": 500, "discount_amount": "0.18", "net_amount": "3.51",
        "tax_rate_bps": 2000, "tax_label": "Controlled test tax", "tax_amount": "0.70", "total": "4.21",
        "basket_source_ref": basket["source_ref"], "policy_source_ref": policy["source_ref"],
        "basket_digest": verifier._digest(basket), "policy_digest": verifier._digest(normalized_policy),
        "rounding_description": "HALF_UP to currency minor units: each quantity times unit price first; "
                                "sum rounded lines; round subtotal times discount_bps / 10000 once; "
                                "subtract discount; round net times tax_bps / 10000 once; add net and tax.",
    }
    return {"currency": "USD", "pricing": pricing}, {"currency": "USD", "quote_basket": basket, "pricing_policy": policy}


def test_exact_decimal_staged_rounding_is_source_bound(verifier, case) -> None:
    quote, candidates = case
    assert verifier._priced_quote_failures(quote, candidates) == []


@pytest.mark.parametrize("field,value", [
    ("total", "4.22"), ("tax_amount", "0.71"), ("subtotal", "3.68"),
    ("discount_amount", "0.19"), ("net_amount", "3.50"),
    ("discount_rate_bps", 501), ("tax_rate_bps", 2001),
    ("policy_source_ref", "source:foreign-policy"), ("basket_source_ref", "source:foreign-basket"),
    ("basket_digest", "sha256:" + "f" * 64), ("policy_digest", "sha256:" + "f" * 64),
    ("currency", "GBP"), ("minor_units", 3), ("minor_units", 2.0),
    ("tax_label", "Invented tax"), ("rounding_description", "Different rounding"),
])
def test_resealed_output_cannot_hide_wrong_amount_or_binding(verifier, case, field, value) -> None:
    quote, candidates = case
    quote["pricing"][field] = value
    # A caller may re-seal every surrounding artifact: the numeric/source oracle
    # still compares the actual content, rather than trusting a new digest.
    assert verifier._digest(quote).startswith("sha256:")
    assert verifier._priced_quote_failures(quote, candidates) == ["FORMATION_PRICING_CAUSALITY"]


@pytest.mark.parametrize("field,value", [
    ("line_id", "foreign"), ("quantity", 4), ("quantity", 3.0), ("unit_price", "0.334"),
    ("line_total", "1.00"), ("sku", "other"),
])
def test_line_evidence_is_exact(verifier, case, field, value) -> None:
    quote, candidates = case
    quote["pricing"]["lines"][0][field] = value
    assert verifier._priced_quote_failures(quote, candidates) == ["FORMATION_PRICING_CAUSALITY"]


@pytest.mark.parametrize("missing", ["quote_basket", "pricing_policy", "pricing"])
def test_incomplete_priced_claim_is_rejected(verifier, case, missing) -> None:
    quote, candidates = case
    (quote if missing == "pricing" else candidates).pop(missing)
    assert verifier._priced_quote_failures(quote, candidates) == ["FORMATION_PRICING_INPUT_INCOMPLETE"]


def test_unpriced_historical_evidence_does_not_acquire_a_pricing_requirement(verifier) -> None:
    assert verifier._priced_quote_failures({"currency": "USD", "price_band": "standard"}, {"currency": "USD"}) == []


@pytest.mark.parametrize("mutation", ["float-price", "bool-quantity", "float-rate", "mixed-currency", "duplicate-line", "unknown-field"])
def test_malformed_source_values_are_rejected(verifier, case, mutation) -> None:
    quote, candidates = case
    basket, policy = candidates["quote_basket"], candidates["pricing_policy"]
    if mutation == "float-price":
        basket["items"][0]["unit_price"] = 0.335
    elif mutation == "bool-quantity":
        basket["items"][0]["quantity"] = True
    elif mutation == "float-rate":
        policy["discount_bps"] = 500.0
    elif mutation == "mixed-currency":
        basket["currency"] = "GBP"
    elif mutation == "duplicate-line":
        basket["items"].append(deepcopy(basket["items"][0]))
    else:
        policy["caller_total"] = "4.21"
    assert verifier._priced_quote_failures(quote, candidates) == ["FORMATION_PRICING_INPUT_INVALID"]


@pytest.mark.parametrize("currency,price,total,minor_units", [("JPY", "0.5", "1", 0), ("KWD", "0.0005", "0.001", 3)])
def test_currency_minor_units_are_applied_before_aggregation(verifier, case, currency, price, total, minor_units) -> None:
    quote, candidates = case
    basket, policy = candidates["quote_basket"], candidates["pricing_policy"]
    basket["currency"] = quote["currency"] = candidates["currency"] = currency
    basket["items"] = [{**basket["items"][0], "quantity": 1, "unit_price": price}]
    policy["discount_bps"] = policy["tax_bps"] = 0
    zero = "0" if minor_units == 0 else "0.000"
    quote["pricing"].update(currency=currency, minor_units=minor_units,
        lines=[{**basket["items"][0], "line_total": total}], subtotal=total,
        discount_rate_bps=0, discount_amount=zero, net_amount=total, tax_rate_bps=0,
        tax_amount=zero, total=total, basket_digest=verifier._digest(basket),
        policy_digest=verifier._digest({**policy, "tax_mode": "EXCLUSIVE", "rounding": "HALF_UP"}))
    assert verifier._priced_quote_failures(quote, candidates) == []
