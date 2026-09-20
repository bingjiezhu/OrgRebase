from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from scripts.run_competition_value_benchmark import (
    BENCHMARK_STATUS,
    MINIMUM_TRIAL_COUNT,
    CompetitionValueBenchmarkError,
    run_benchmark,
)


@pytest.fixture(scope="module")
def measured_receipt(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, Path]:
    working_root = tmp_path_factory.mktemp("competition-value")
    receipt = run_benchmark(
        trial_count=MINIMUM_TRIAL_COUNT,
        working_root=working_root,
    )
    return receipt, working_root


def test_seven_actual_selective_trials_are_bound_to_one_pack_and_seed(
    measured_receipt: tuple[dict, Path],
) -> None:
    receipt, working_root = measured_receipt
    selective = receipt["strategies"]["selective_rebase"]
    trials = selective["trials"]

    assert receipt["status"] == BENCHMARK_STATUS
    assert receipt["verification_status"] == "PASS"
    assert receipt["evidence_class"] == "CONTROLLED_SYNTHETIC_LOCAL_MEASUREMENT"
    assert receipt["input_binding"]["trial_count"] == MINIMUM_TRIAL_COUNT
    assert receipt["input_binding"]["same_pack_and_seed_across_trials"] is True
    assert len(trials) == MINIMUM_TRIAL_COUNT
    assert len(list(working_root.glob("selective-trial-*.sqlite3"))) == MINIMUM_TRIAL_COUNT

    assert {item["pack_digest"] for item in trials} == {
        receipt["input_binding"]["pack_digest"]
    }
    assert {item["profile_digest"] for item in trials} == {
        receipt["input_binding"]["profile_digest"]
    }
    assert {tuple(item["target_ids"]) for item in trials} == {
        (
            "work:finance-approval",
            "work:launch-readiness-review",
            "work:partner-brief",
            "work:quote-blue-harbor",
        )
    }
    for trial in trials:
        payload = dict(trial)
        digest = payload.pop("digest")
        assert digest == sha256_digest(payload)
        assert trial["rebuilt_target_count"] == 1
        assert trial["rebuilt_target_ids"] == ["work:quote-blue-harbor"]
        assert trial["preserved_proof_count"] == 2
        assert trial["preserved_target_ids"] == [
            "work:finance-approval",
            "work:launch-readiness-review",
        ]
        assert trial["worker_invocation_count"] == 4
        assert trial["worker_domains"] == ["finance", "gtm", "legal", "product"]
        assert trial["advisory_agent_run_count"] == 3
        assert trial["tool_invocation_count"] == 1
        assert trial["skill_package_invocation_count"] == 0
        assert trial["human_touchpoint_count"] == 1
        assert trial["human_touchpoint_mode"] == "AUTOMATION_HARNESS_DRIVEN_OWNER_COMMAND"
        assert trial["verified_external_human_status"] == "NOT_RUN"
        assert trial["selective_operation_wall_time_ms"] > 0
        assert trial["wall_time_ms"] >= trial["selective_operation_wall_time_ms"]

    summary = selective["summary"]
    assert summary["rebuilt_target_count"] == {
        "status": "MEASURED",
        "sample_count": MINIMUM_TRIAL_COUNT,
        "unit": "TARGET",
        "min": 1.0,
        "median": 1.0,
        "max": 1.0,
    }
    assert summary["preserved_proof_count"]["median"] == 2.0
    assert summary["worker_invocation_count"]["median"] == 4.0
    assert summary["tool_invocation_count"]["median"] == 1.0
    assert summary["skill_package_invocation_count"]["median"] == 0.0
    assert summary["human_touchpoint_count"]["median"] == 1.0
    for field in ("selective_operation_wall_time_ms", "wall_time_ms"):
        assert summary[field]["min"] <= summary[field]["median"] <= summary[field]["max"]


def test_full_rebuild_and_roi_remain_not_run_instead_of_modelled_as_measurement(
    measured_receipt: tuple[dict, Path],
) -> None:
    receipt, _ = measured_receipt
    full = receipt["strategies"]["full_rebuild"]
    comparison = receipt["comparison"]

    assert full["execution_status"] == "NOT_RUN"
    assert full["required_target_count"] == 4
    assert "NO_SEMANTICALLY_EQUIVALENT_FULL_REBUILD_EXECUTOR" in full["reason"]
    assert all(item["status"] == "NOT_RUN" for item in full["metrics"].values())
    assert comparison["status"] == "NOT_RUN"
    assert comparison["saving_rate"]["status"] == "NOT_RUN"
    assert comparison["enterprise_roi"]["status"] == "NOT_RUN"
    assert "CONTROLLED_SYNTHETIC_MEASURED_BENCHMARK_ONLY" in receipt["claim_boundary"]

    payload = dict(receipt)
    digest = payload.pop("digest")
    assert digest == sha256_digest(payload)


def test_benchmark_refuses_fewer_than_seven_trials(tmp_path: Path) -> None:
    with pytest.raises(
        CompetitionValueBenchmarkError,
        match="AT_LEAST_SEVEN_TRIALS_REQUIRED",
    ):
        run_benchmark(trial_count=MINIMUM_TRIAL_COUNT - 1, working_root=tmp_path)
