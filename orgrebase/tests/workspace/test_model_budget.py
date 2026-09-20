from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from orgrebase.workspace.model_budget import ModelBudget


def budget(**updates) -> ModelBudget:
    return ModelBudget.model_validate({
        "schema_version": "orgrebase.model-budget.v1",
        "model_id": "gpt-6-astra",
        "context_window_tokens": 100_000,
        "input_usd_per_million_ceiling": "1.25",
        "output_usd_per_million_ceiling": "5",
        "max_preview_usd": "10",
        "price_source_ref": "test:synthetic-price-contract-not-a-vendor-price",
        "valid_until": "2099-01-01T00:00:00Z",
        **updates,
    })


def test_entire_round_reserves_full_context_and_output_including_reasoning():
    reservation = budget().reserve(model_id="gpt-6-astra", calls=5, max_output_tokens=2048)
    assert reservation["reserved_microusd"] == 676200
    assert reservation["scope"] == "ONE_PREVIEW_ATTEMPT"
    assert reservation["input_token_ceiling_per_call"] == 100_000
    assert reservation["max_output_tokens_per_call"] == 2048
    assert reservation["service_tier"] == "default"


def test_fractional_reservation_rounds_up_and_budget_rounds_down():
    values = dict(context_window_tokens=123, input_usd_per_million_ceiling="0.2",
                  output_usd_per_million_ceiling="1.2", max_preview_usd="0.000172")
    assert budget(**values).reserve(model_id="gpt-6-astra", calls=2,
                                    max_output_tokens=51)["reserved_microusd"] == 172
    values["max_preview_usd"] = "0.00017199"
    with pytest.raises(ValueError, match="COST_LIMIT_EXCEEDED"):
        budget(**values).reserve(model_id="gpt-6-astra", calls=2, max_output_tokens=51)


@pytest.mark.parametrize("updates", [
    {"input_usd_per_million_ceiling": 0.2}, {"max_preview_usd": "NaN"},
    {"output_usd_per_million_ceiling": "Infinity"}, {"max_preview_usd": "-1"},
    {"context_window_tokens": True}, {"valid_until": "2099-01-01T00:00:00"},
])
def test_ambiguous_or_invalid_price_contract_is_rejected(updates):
    with pytest.raises(ValidationError):
        budget(**updates)


def test_model_and_expiry_are_not_implicitly_updated():
    with pytest.raises(ValueError, match="MODEL_PRICE_MODEL_MISMATCH"):
        budget().reserve(model_id="different-model", calls=1, max_output_tokens=10)
    with pytest.raises(ValueError, match="MODEL_PRICE_CONTRACT_EXPIRED"):
        budget(valid_until="2000-01-01T00:00:00Z").reserve(
            model_id="gpt-6-astra", calls=1, max_output_tokens=10)


def test_price_file_is_bounded_and_rejects_duplicate_fields(tmp_path):
    path = tmp_path / "price.json"
    path.write_text(budget().model_dump_json())
    assert ModelBudget.from_file(path) == budget()
    path.write_text('{"model_id":"one","model_id":"two"}')
    with pytest.raises(ValueError, match="MODEL_PRICE_DUPLICATE_FIELD"):
        ModelBudget.from_file(path)
    path.write_text(json.dumps({"padding": "x" * 17000}))
    with pytest.raises(ValueError, match="MODEL_PRICE_CONTRACT_TOO_LARGE"):
        ModelBudget.from_file(path)
