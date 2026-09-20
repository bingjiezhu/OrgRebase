from __future__ import annotations

import json
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.benchmark import (
    BaselineSystem,
    OWBBenchmarkRepository,
    OWBEvaluator,
    ReferenceWorkspaceBenchmarkSUT,
    run_owb_evaluation,
)
from orgrebase.workspace.models import CaseRunReceipt


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


class _ReceiptMutationSystem:
    """Preserve valid content digests so tests exercise evaluator decisions."""

    def __init__(self, base, mutation) -> None:
        self.base = base
        self.mutation = mutation

    def run_case(self, case, *, mode="deterministic") -> CaseRunReceipt:
        receipt = self.base.run_case(case, mode=mode)
        payload = receipt.model_dump(mode="json", exclude={"digest"})
        self.mutation(payload, case)
        payload["output_digest"] = sha256_digest(payload["public_output"])
        return CaseRunReceipt.model_validate(payload)


@pytest.mark.parametrize("case_ids", [(), ("case-that-does-not-exist",)])
def test_empty_case_selection_is_rejected(case_ids) -> None:
    repository = OWBBenchmarkRepository()
    evaluator = OWBEvaluator(repository)
    with pytest.raises(IntegrityError, match="BENCHMARK_NO_CASES_SELECTED"):
        evaluator.evaluate_system(
            ReferenceWorkspaceBenchmarkSUT(repository), profile="empty", case_ids=case_ids
        )


@pytest.mark.parametrize("status", ["NOT_RUN", "ERROR", "FAIL"])
@pytest.mark.parametrize("all_receipts", [False, True])
def test_failed_or_unexecuted_receipts_cannot_pass_with_correct_output(status, all_receipts) -> None:
    repository = OWBBenchmarkRepository()
    first_case = repository.cases()[0].case_id

    def mutate(payload, case) -> None:
        if all_receipts or case.case_id == first_case:
            payload["status"] = status

    report = OWBEvaluator(repository).evaluate_system(
        _ReceiptMutationSystem(ReferenceWorkspaceBenchmarkSUT(repository), mutate),
        profile="invalid-run-status",
    )
    assert len(report.case_receipt_refs) == 192
    # This isolates execution eligibility from output correctness and thresholds.
    assert report.core_score.weighted_score == 100.0
    assert all(gate.status == "PASS" for gate in report.hard_gate_results)
    assert report.status == report.core_score.status == "FAIL"


@pytest.mark.parametrize("target_writes", [1, -1, None, "0", False])
def test_correct_security_error_code_requires_explicit_zero_target_writes(target_writes) -> None:
    repository = OWBBenchmarkRepository()
    security_case = next(case for case in repository.cases() if case.case_type == "SECURITY")

    def mutate(payload, case) -> None:
        if case.case_id == security_case.case_id:
            if target_writes is None:
                payload["public_output"].pop("target_writes")
            else:
                payload["public_output"]["target_writes"] = target_writes

    report = OWBEvaluator(repository).evaluate_system(
        _ReceiptMutationSystem(ReferenceWorkspaceBenchmarkSUT(repository), mutate),
        profile="security-write-counterexample",
    )
    gates = {gate.metric_id: gate for gate in report.hard_gate_results}
    # One incorrect case among 32 is enough to fail each applicable hard gate.
    assert gates["unauthorized_read_block_rate"].value == 31 / 32
    assert gates["certificate_tamper_rejection"].value == 31 / 32
    assert report.status == report.core_score.status == "FAIL"


def test_valid_default_matrix_remains_identical_to_retained_reports() -> None:
    archive = Path(__file__).resolve().parents[2] / "evidence/workspace/latest/evaluation-suite.json"
    payload = json.loads(archive.read_text(encoding="utf-8"))
    expected = payload["reports"]
    actual = run_owb_evaluation()["reports"]
    assert len(actual) == 13
    assert actual == expected
