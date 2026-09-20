from __future__ import annotations

import pytest

from orgrebase.workspace.models import UserWalkthroughRecord
from orgrebase.workspace.user_validation import UserValidationService


def test_no_records_is_honest_not_run() -> None:
    summary = UserValidationService.summarize(())
    assert summary.status == "NOT_RUN"
    assert summary.participant_count == 0
    assert summary.evidence_class == "NOT_RUN"


def test_consented_walkthroughs_are_aggregated_without_raw_identity() -> None:
    records = tuple(
        UserWalkthroughRecord(
            id=f"walkthrough:{index}",
            participant_role="sales-operations",
            scenario_version="workspace-demo@v1",
            consent_recorded=True,
            problem_understood=True,
            usefulness_rating=5,
            trace_value_rating=4,
            pilot_intent="CONDITIONAL_PILOT",
            redacted_findings=("Trace makes stale premise visible",),
            critical_gaps=(),
            recorded_at=f"2026-08-16T00:0{index}:00Z",
        )
        for index in range(5)
    )
    summary = UserValidationService.summarize(records)
    assert summary.status == "PASS"
    assert summary.participant_count == 5
    assert summary.repeated_critical_gaps == ()


def test_unconsented_record_is_rejected() -> None:
    record = UserWalkthroughRecord(
        id="walkthrough:bad",
        participant_role="unknown",
        scenario_version="v1",
        consent_recorded=False,
        problem_understood=False,
        usefulness_rating=1,
        trace_value_rating=1,
        pilot_intent="NOT_ASKED",
        redacted_findings=(),
        critical_gaps=(),
        recorded_at="2026-08-16T00:00:00Z",
    )
    with pytest.raises(ValueError, match="CONSENT"):
        UserValidationService.summarize((record,))
