from __future__ import annotations

import pytest
from pydantic import ValidationError

from orgrebase.business_evaluation import (
    COST_CATEGORIES,
    PairedObservation,
    business_report,
)


def observation(case: str, baseline: str = "10", product: str = "5", outcome: str = "QUALIFIED") -> PairedObservation:
    def run(kind: str, amount: str, status: str) -> dict:
        return {
            "run_ref": f"run:{case}:{kind}", "outcome": status,
            "result_evidence_ref": f"result:{case}:{kind}" if status == "QUALIFIED" else None,
            "costs": [{"observation_ref": f"cost:{case}:{kind}:{category}",
                       "category": category, "amount": amount if category == "human_review" else "0"}
                      for category in sorted(COST_CATEGORIES)],
        }
    return PairedObservation(
        case_id=case, organization_id="one", cluster_id="one-correlated-change",
        period="2026-09", currency="USD", evidence_class="CONTROLLED_LOCAL",
        baseline=run("baseline", baseline, "QUALIFIED"), product=run("product", product, outcome),
    )


def test_failure_costs_and_denominator_are_not_removed_from_report() -> None:
    report = business_report((observation("one"), observation("two", product="7", outcome="UNKNOWN")))
    assert report["product"]["total_cost"] == "12"
    assert report["product"]["qualified_completion_rate"] == "0.5"
    assert report["product"]["cost_per_qualified_result"] == "12"
    assert report["independent_clusters"] == 1
    assert report["decision"] == "REDUCE_SCOPE_AND_INVESTIGATE"
    assert report["enterprise_roi_proven"] is False


def test_exact_decimal_costs_and_negative_savings_are_reported() -> None:
    report = business_report((observation("one", baseline="0.10", product="0.30"),))
    assert report["observed_cost_difference"] == "-0.20"
    assert report["decision"] == "REDUCE_SCOPE_AND_INVESTIGATE"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "not-a-number"])
def test_invalid_costs_are_not_coerced_to_zero(amount: str) -> None:
    with pytest.raises(ValidationError):
        observation("one", product=amount)


def test_missing_cost_category_prevents_a_partial_roi_claim() -> None:
    payload = observation("one").model_dump(mode="json")
    payload["product"]["costs"] = payload["product"]["costs"][:-1]
    with pytest.raises(ValidationError, match="ALL_COST_CATEGORIES_REQUIRED"):
        PairedObservation.model_validate(payload)


def test_duplicate_cases_and_shared_expense_refs_are_rejected() -> None:
    first = observation("one")
    with pytest.raises(ValueError, match="DUPLICATE_BUSINESS_CASE"):
        business_report((first, first))
    second = observation("two").model_dump(mode="json")
    second["product"]["costs"][0]["observation_ref"] = first.product.costs[0].observation_ref
    with pytest.raises(ValueError, match="COUNTED_MORE_THAN_ONCE"):
        business_report((first, PairedObservation.model_validate(second)))


def test_no_qualified_results_has_no_invented_unit_cost() -> None:
    report = business_report((observation("one", outcome="FAILED"),))
    assert report["product"]["cost_per_qualified_result"] is None
    assert report["statistical_significance"] == "NOT_ESTABLISHED"
