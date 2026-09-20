"""Redacted user-walkthrough recording and thresholded validation summary."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from orgrebase.workspace.models import UserValidationSummary, UserWalkthroughRecord


class UserValidationRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_session(self, record: UserWalkthroughRecord) -> None:
        if not record.consent_recorded:
            raise ValueError("USER_VALIDATION_CONSENT_REQUIRED")
        forbidden = ("email", "phone", "name:", "company:")
        rendered = json.dumps(record.model_dump(mode="json"), ensure_ascii=False).lower()
        if any(term in rendered for term in forbidden):
            raise ValueError("USER_VALIDATION_PII_NOT_REDACTED")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def records(self) -> tuple[UserWalkthroughRecord, ...]:
        if not self.path.is_file():
            return ()
        return tuple(
            UserWalkthroughRecord.model_validate(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    def summarize(self) -> UserValidationSummary:
        return UserValidationService.summarize(self.records())


def synthetic_walkthrough_summary() -> UserValidationSummary:
    """Build the zero-participant validation summary used by local demos."""
    return UserValidationSummary(
        id="user-validation:workspace-synthetic@v1",
        participant_count=0,
        problem_comprehension_rate=0.0,
        median_usefulness=0.0,
        median_trace_value=0.0,
        pilot_or_conditional_count=0,
        repeated_critical_gaps=(),
        status="NOT_RUN",
        evidence_class="SYNTHETIC_FIXTURE",
        limitations=(
            "Synthetic scenario checks cannot be represented as USER_STUDY evidence.",
            "Collect at least five consented, redacted enterprise-role walkthroughs.",
        ),
    )


class UserValidationService:
    """Stateless façade used by API/CLI and release evidence."""

    @staticmethod
    def summarize(records: tuple[UserWalkthroughRecord, ...]) -> UserValidationSummary:
        if not records:
            payload = synthetic_walkthrough_summary().model_dump(
                mode="json", exclude={"digest"}
            )
            payload.update(
                {"id": "user-validation:workspace@v1", "evidence_class": "NOT_RUN"}
            )
            return UserValidationSummary.model_validate(payload)
        for record in records:
            if not record.consent_recorded:
                raise ValueError("USER_VALIDATION_CONSENT_REQUIRED")
            rendered = json.dumps(record.model_dump(mode="json"), ensure_ascii=False).lower()
            if any(term in rendered for term in ("email", "phone", "name:", "company:")):
                raise ValueError("USER_VALIDATION_PII_NOT_REDACTED")
        participant_count = len(records)
        comprehension = sum(item.problem_understood for item in records) / participant_count
        usefulness = float(statistics.median(item.usefulness_rating for item in records))
        trace_value = float(statistics.median(item.trace_value_rating for item in records))
        pilot = sum(item.pilot_intent in {"PILOT", "CONDITIONAL_PILOT"} for item in records)
        gap_counts: dict[str, int] = {}
        for item in records:
            for gap in set(item.critical_gaps):
                gap_counts[gap] = gap_counts.get(gap, 0) + 1
        repeated = tuple(sorted(gap for gap, count in gap_counts.items() if count >= 2))
        passed = (
            participant_count >= 5
            and comprehension >= 0.8
            and usefulness >= 4.0
            and trace_value >= 4.0
            and pilot >= 3
            and not repeated
        )
        return UserValidationSummary(
            id="user-validation:workspace@v1",
            participant_count=participant_count,
            problem_comprehension_rate=round(comprehension, 4),
            median_usefulness=usefulness,
            median_trace_value=trace_value,
            pilot_or_conditional_count=pilot,
            repeated_critical_gaps=repeated,
            status="PASS" if passed else "FAIL",
            evidence_class="USER_STUDY",
            limitations=(
                "Small qualitative walkthrough sample; not a production ROI study.",
                "Only participant roles and redacted findings are retained.",
            ),
        )
