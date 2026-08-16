from __future__ import annotations

import pytest

from orgrebase.workspace.benchmark import (
    BaselineSystem,
    OWBBenchmarkRepository,
    ReferenceWorkspaceBenchmarkSUT,
    run_owb_evaluation,
)


def test_orgworkbench_has_192_cases_and_strict_license_gate() -> None:
    repository = OWBBenchmarkRepository()
    result = repository.verify()
    assert result == {
        "status": "PASS",
        "case_count": 192,
        "gold_count": 192,
        "organization_count": 12,
        "license_gate": "PASS",
    }
    assert any(item.case_type == "FORMATION" for item in repository.cases())
    assert any(item.case_type == "SECURITY" for item in repository.cases())


def test_evaluator_gold_is_not_publicly_accessible() -> None:
    repository = OWBBenchmarkRepository()
    with pytest.raises(PermissionError, match="EVALUATOR_GOLD_ACCESS_DENIED"):
        repository.gold(repository.cases()[0].case_id, evaluator_token=object())


def test_reference_profile_passes_all_hard_gates_at_100() -> None:
    result = run_owb_evaluation()
    assert result["primary_status"] == "PASS"
    assert result["primary_score"] == 100.0
    report = result["reports"]["orgrebase-workspace"]
    assert all(item["status"] == "PASS" for item in report["hard_gate_results"])


def test_baselines_and_ablations_demonstrate_mechanism_value() -> None:
    result = run_owb_evaluation()["reports"]
    assert result["safe-single-agent-admitted-context"]["core_score"]["weighted_score"] < 100
    assert result["natural-language-multi-agent"]["core_score"]["weighted_score"] < 100
    assert result["broadcast-invalidate-all"]["status"] == "FAIL"
    assert result["no-unknown"]["status"] == "FAIL"
    assert result["no-successor-promotion"]["status"] == "FAIL"
    assert result["non-exact-skill-digest-negative-control"]["status"] == "FAIL"


def test_baseline_receipt_recomputes_its_content_digest() -> None:
    repository = OWBBenchmarkRepository()
    reference = ReferenceWorkspaceBenchmarkSUT(repository)
    case = next(item for item in repository.cases() if item.case_type == "FORMATION")
    receipt = BaselineSystem(reference, "safe-single-agent-admitted-context").run_case(case)
    assert receipt.digest.startswith("sha256:")
    payload = receipt.model_dump(mode="json")
    assert type(receipt).model_validate(payload).digest == receipt.digest
