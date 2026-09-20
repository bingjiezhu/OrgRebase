"""Paired business observations, including unsuccessful work and all measured costs."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orgrebase.digest import sha256_digest

COST_CATEGORIES = frozenset({"model", "tools", "human_review", "rework", "operations", "onboarding"})


class ObservedCost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_ref: str = Field(min_length=1)
    category: Literal["model", "tools", "human_review", "rework", "operations", "onboarding"]
    amount: str

    @field_validator("amount")
    @classmethod
    def decimal_amount(cls, value: str) -> str:
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?", value):
            raise ValueError("COST_DECIMAL_ENCODING_INVALID")
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("COST_DECIMAL_REQUIRED") from error
        if not parsed.is_finite() or parsed < 0:
            raise ValueError("COST_FINITE_NONNEGATIVE_REQUIRED")
        return value


class ProcessObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_ref: str = Field(min_length=1)
    outcome: Literal["QUALIFIED", "REJECTED", "UNKNOWN", "FAILED", "INCOMPLETE", "HUMAN_HANDOFF"]
    result_evidence_ref: str | None = None
    unexpected_mutations: int = Field(default=0, ge=0)
    costs: tuple[ObservedCost, ...]

    @model_validator(mode="after")
    def complete_accounting(self) -> ProcessObservation:
        if {item.category for item in self.costs} != COST_CATEGORIES:
            raise ValueError("ALL_COST_CATEGORIES_REQUIRED_INCLUDING_EXPLICIT_ZEROS")
        references = [item.observation_ref for item in self.costs]
        if len(set(references)) != len(references):
            raise ValueError("DUPLICATE_COST_OBSERVATION")
        if self.outcome == "QUALIFIED" and not self.result_evidence_ref:
            raise ValueError("QUALIFIED_RESULT_EVIDENCE_REQUIRED")
        if self.outcome == "QUALIFIED" and self.unexpected_mutations:
            raise ValueError("UNEXPECTED_MUTATION_CANNOT_BE_QUALIFIED")
        return self


class PairedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    evidence_class: Literal["SYNTHETIC", "CONTROLLED_LOCAL", "REAL_ENTERPRISE"]
    independent_annotation_ref: str | None = None
    baseline: ProcessObservation
    product: ProcessObservation


def business_report(observations: tuple[PairedObservation, ...]) -> dict:
    """Report measurements without converting declarative labels into proof of provenance."""
    if not observations:
        raise ValueError("BUSINESS_OBSERVATIONS_REQUIRED")
    observations = tuple(PairedObservation.model_validate(item.model_dump(mode="json")) for item in observations)
    case_keys = [(item.organization_id, item.case_id) for item in observations]
    if len(set(case_keys)) != len(case_keys):
        raise ValueError("DUPLICATE_BUSINESS_CASE")
    currencies = {item.currency for item in observations}
    if len(currencies) != 1:
        raise ValueError("CROSS_CURRENCY_AGGREGATION_REQUIRES_EXPLICIT_CONVERSION")
    references = [cost.observation_ref for item in observations
                  for run in (item.baseline, item.product) for cost in run.costs]
    if len(set(references)) != len(references):
        raise ValueError("COST_OBSERVATION_COUNTED_MORE_THAN_ONCE")

    def summarize(attribute: str) -> dict:
        runs = [getattr(item, attribute) for item in observations]
        statuses = Counter(run.outcome for run in runs)
        qualified = statuses["QUALIFIED"]
        costs = {category: sum((Decimal(cost.amount) for run in runs for cost in run.costs
                                if cost.category == category), Decimal(0))
                 for category in sorted(COST_CATEGORIES)}
        total = sum(costs.values(), Decimal(0))
        return {
            "cases": len(runs), "outcomes": dict(sorted(statuses.items())),
            "qualified_results": qualified,
            "qualified_completion_rate": str(Decimal(qualified) / Decimal(len(runs))),
            "unexpected_mutations": sum(run.unexpected_mutations for run in runs),
            "cost_by_category": {key: str(value) for key, value in costs.items()},
            "total_cost": str(total),
            "cost_per_qualified_result": str(total / qualified) if qualified else None,
        }

    baseline, product = summarize("baseline"), summarize("product")
    difference = Decimal(baseline["total_cost"]) - Decimal(product["total_cost"])
    baseline_cost = Decimal(baseline["total_cost"])
    clusters = {(item.organization_id, item.cluster_id) for item in observations}
    reported_live = all(item.evidence_class == "REAL_ENTERPRISE" for item in observations)
    annotations = all(item.independent_annotation_ref for item in observations)
    no_regression = (
        product["qualified_results"] >= baseline["qualified_results"]
        and product["unexpected_mutations"] <= baseline["unexpected_mutations"]
    )
    return {
        "schema_version": "orgrebase.business-observation-report.v1",
        "input_digest": sha256_digest([item.model_dump(mode="json") for item in observations]),
        "currency": next(iter(currencies)), "baseline": baseline, "product": product,
        "observed_cost_difference": str(difference),
        "observed_cost_difference_ratio": str(difference / baseline_cost) if baseline_cost else None,
        "independent_clusters": len(clusters),
        "organizations": len({item.organization_id for item in observations}),
        "periods": len({item.period for item in observations}),
        "independent_annotation_refs_present": annotations,
        "provenance_status": "REQUIRES_INDEPENDENT_EVIDENCE_REVIEW",
        "statistical_significance": "NOT_ESTABLISHED",
        "enterprise_roi_proven": False,
        "decision": (
            "REDUCE_SCOPE_AND_INVESTIGATE" if difference <= 0 or not no_regression else
            "REVIEW_REAL_EVIDENCE" if reported_live and annotations else
            "COLLECT_REAL_BASELINE_AND_ANNOTATIONS"
        ),
    }
