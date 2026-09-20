"""Money controls use Fraction/integer rounding independently of Decimal."""

from fractions import Fraction

import pytest
from pydantic import ValidationError

from orgrebase.digest import sha256_digest
from orgrebase.workspace.pricing import (
    PricedQuote,
    PricingPolicy,
    QuoteBasket,
    QuoteLineInput,
    calculate_quote,
)


def line(line_id="a", *, quantity=1, price="12.345"):
    return QuoteLineInput(line_id=line_id, sku="SKU-1", description="Observed item", quantity=quantity, unit_price=price)


def basket(*items, currency="GBP"):
    return QuoteBasket(currency=currency, items=items or (line(),), source_ref="source:observed-basket")


def policy(discount=0, tax=0):
    return PricingPolicy(discount_bps=discount, tax_bps=tax, tax_label="CONTROLLED DEMO TAX", source_ref="policy:explicit")


def integer_round(value: Fraction) -> int:
    whole, remainder = divmod(value.numerator, value.denominator)
    return whole + (2 * remainder >= value.denominator)


def independent_total(items, *, minor_units, discount, tax):
    scale = 10**minor_units
    subtotal = sum(integer_round(Fraction(item.unit_price) * item.quantity * scale) for item in items)
    net = subtotal - integer_round(Fraction(subtotal * discount, 10000))
    return net + integer_round(Fraction(net * tax, 10000))


def test_known_gbp_quote_rounding_sequence_and_provenance():
    source = basket(line(quantity=2), line("b", quantity=3, price="0.335"))
    rules = policy(1000, 2000)
    result = calculate_quote(source, rules, currency="GBP")
    assert [item.line_total for item in result.lines] == ["24.69", "1.01"]
    assert (result.subtotal, result.discount_amount, result.net_amount, result.tax_amount, result.total) == (
        "25.70", "2.57", "23.13", "4.63", "27.76")
    assert Fraction(result.total) * 100 == independent_total(source.items, minor_units=2, discount=1000, tax=2000)
    assert result.basket_digest == sha256_digest(source.model_dump(mode="json"))
    assert result.policy_digest == sha256_digest(rules.model_dump(mode="json"))
    assert calculate_quote(source, rules, currency="GBP") == result


def test_priced_output_round_trips_through_stored_json_dictionary():
    result = calculate_quote(basket(), policy(1000, 2000), currency="GBP")
    stored_payload = result.model_dump(mode="json")
    assert isinstance(stored_payload["lines"], list)
    assert PricedQuote.model_validate(stored_payload) == result


@pytest.mark.parametrize(("currency", "price", "expected"), [
    ("GBP", "0.005", "0.01"), ("GBP", "0.004999999999999999", "0.00"),
    ("GBP", "0.28999999999999998", "0.29"), ("JPY", "0.5", "1"),
    ("JPY", "0.499999999999999999", "0"), ("KWD", "1.2345", "1.235"),
    ("USD", "0", "0.00"), ("EUR", "3.14", "3.14"), ("CNY", "0.005", "0.01"),
])
def test_currency_minor_units_and_half_boundaries(currency, price, expected):
    source = basket(line(price=price), currency=currency)
    result = calculate_quote(source, policy(), currency=currency)
    assert result.total == expected
    assert result.lines[0].unit_price == price
    assert Fraction(result.total) * 10**result.minor_units == independent_total(
        source.items, minor_units=result.minor_units, discount=0, tax=0)


@pytest.mark.parametrize("discount", [0, 10000])
def test_zero_and_full_discount_with_maximum_values(discount):
    source = basket(line(quantity=1_000_000, price="999999999999.999999999999999999"))
    result = calculate_quote(source, policy(discount, 10000), currency="GBP")
    assert Fraction(result.total) * 100 == independent_total(source.items, minor_units=2, discount=discount, tax=10000)
    if discount == 10000:
        assert result.net_amount == result.tax_amount == result.total == "0.00"


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-0.01", 0.1, "1e999", "1.0000000000000000001", "1000000000000", " 1.00"])
def test_invalid_prices_fail_before_arithmetic(price):
    with pytest.raises(ValidationError):
        line(price=price)


@pytest.mark.parametrize("quantity", [-1, 0, 1.0, True, 1_000_001])
def test_quantity_requires_bounded_strict_positive_integer(quantity):
    with pytest.raises(ValidationError):
        line(quantity=quantity)


def test_duplicate_ids_currency_policy_and_input_immutability():
    with pytest.raises(ValidationError, match="unique"):
        basket(line(), line())
    with pytest.raises(ValidationError):
        basket(currency="AUD")
    with pytest.raises(ValidationError):
        QuoteBasket(currency="GBP", items=(), source_ref="source:test")
    with pytest.raises(ValidationError):
        policy(-1, 0)
    with pytest.raises(ValidationError):
        policy(0, 10001)
    with pytest.raises(ValidationError):
        PricingPolicy(tax_label=" ", source_ref="policy:test")
    source = basket()
    with pytest.raises(ValueError, match="currencies must match"):
        calculate_quote(source, policy(), currency="USD")
    with pytest.raises(ValueError, match="Unsupported"):
        calculate_quote(source, policy(), currency="AUD")
    with pytest.raises(ValidationError):
        source.currency = "USD"
