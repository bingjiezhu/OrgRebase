from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmark" / "quote-value-v0.1"
REGISTRY = ROOT / "configs" / "workspace" / "quote-value-metric-registry.json"
EVALUATION_SUITE = ROOT / "evidence" / "workspace" / "latest" / "evaluation-suite.json"
PRODUCT_OBSERVATIONS = (
    ROOT / "evidence" / "workspace" / "latest" / "product-path-observations.json"
)
CURRENCY_VMRC = (
    ROOT
    / "evidence"
    / "workspace"
    / "latest"
    / "rebase"
    / "currency-minimal-rebase-certificate.json"
)
LAUNCH_VMRC = (
    ROOT
    / "evidence"
    / "workspace"
    / "latest"
    / "rebase"
    / "launch-minimal-rebase-certificate.json"
)


def _load_evaluator() -> ModuleType:
    path = ROOT / "scripts" / "run_quote_value_benchmark.py"
    spec = importlib.util.spec_from_file_location("quote_value_evaluator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(evaluator: ModuleType, **overrides: Path) -> dict:
    values = {
        "project_root": ROOT,
        "benchmark_root": BENCHMARK_ROOT,
        "metric_registry_path": REGISTRY,
        "evaluation_suite_path": EVALUATION_SUITE,
        "product_observations_path": PRODUCT_OBSERVATIONS,
        "currency_vmrc_path": CURRENCY_VMRC,
        "launch_vmrc_path": LAUNCH_VMRC,
    }
    values.update(overrides)
    return evaluator.build_receipt(**values)


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _rewrite_manifest(benchmark_root: Path) -> None:
    relative_paths = [
        "public/current-process-baseline.json",
        "public/value-cases.json",
        "evaluator/action-cost-model.json",
    ]
    lines = [
        f"{hashlib.sha256((benchmark_root / relative).read_bytes()).hexdigest()}  {relative}"
        for relative in relative_paths
    ]
    (benchmark_root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _metric(receipt: dict, metric_id: str) -> dict:
    return next(item for item in receipt["metrics"] if item["metric_id"] == metric_id)


def _redigest(evaluator: ModuleType, receipt: dict) -> None:
    payload = copy.deepcopy(receipt)
    payload.pop("digest")
    receipt["digest"] = evaluator._object_digest(payload)


def _redigest_content_address(evaluator: ModuleType, value: dict) -> None:
    payload = copy.deepcopy(value)
    payload.pop("digest")
    value["digest"] = evaluator._object_digest(payload)


def test_quote_value_receipt_separates_evidence_classes_and_not_run_metrics() -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)

    assert receipt["verification_status"] == "PASS"
    assert receipt["evidence_ceiling"] == "SYNTHETIC_CONTROLLED_VALUE_PROOF"
    assert receipt["primary_user"] == "Enterprise Quote Operator"
    assert receipt["target_universe"]["target_count"] == 4
    assert len(receipt["rebase_cases"]) == 2

    for metric_id in (
        "quote_cycle_elapsed_minutes",
        "policy_confirmation_active_minutes",
        "first_pass_rework_rate",
    ):
        metric = _metric(receipt, metric_id)
        assert metric["status"] == "NOT_RUN"
        assert metric["evidence_class"] == "NOT_RUN"
        assert metric["value"] is None
        assert metric["numerator"] is None
        assert metric["denominator"] is None

    mismatch = _metric(receipt, "impact_exact_mismatch_case_rate")
    assert mismatch == {
        **mismatch,
        "value": 0.0,
        "numerator": 0.0,
        "denominator": 72.0,
        "status": "CALCULATED",
        "evidence_class": "SYNTHETIC_GOLD",
    }
    assert "complete synthetic CHANGE case" in mismatch["limitations"][0]
    unauthorized = _metric(receipt, "unauthorized_access_success_rate")
    assert unauthorized["value"] == 0.0
    assert unauthorized["numerator"] == 0.0
    assert unauthorized["denominator"] == 32.0
    assert unauthorized["evidence_class"] == "SYNTHETIC_GOLD"
    hold_decisions = _metric(receipt, "hold_for_review_decision_rate")
    assert hold_decisions["value"] == 0.25
    assert hold_decisions["numerator"] == 2.0
    assert hold_decisions["denominator"] == 8.0
    assert hold_decisions["evidence_class"] == "OBSERVED_LOCAL_PRODUCT"
    assert "case-target decision" in hold_decisions["limitations"][0]


def test_normalized_cost_includes_review_requalify_hold_and_sensitivity() -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)
    cost_model = json.loads(
        (BENCHMARK_ROOT / "evaluator" / "action-cost-model.json").read_text(encoding="utf-8")
    )

    assert set(cost_model["components"]) == {
        "PRESERVE",
        "REVIEW",
        "REQUALIFY",
        "HOLD",
        "REBUILD",
    }
    assert all(value > 0 for value in cost_model["components"].values())
    assert set(cost_model["disposition_components"]["HOLD_FOR_REVIEW"]) == {
        "REVIEW",
        "HOLD",
    }
    assert set(cost_model["disposition_components"]["REQUALIFY"]) == {
        "REVIEW",
        "REQUALIFY",
    }
    assert receipt["aggregate_cost"]["full_rebuild"] == 8.0
    assert receipt["aggregate_cost"]["selective_rebase"] == 4.05
    assert receipt["aggregate_cost"]["saving_rate"] == 0.49375
    assert receipt["sensitivity"]["declared_review_burden_envelope"] == {
        "scenario_ids": ["LOW_REVIEW_BURDEN", "HIGH_REVIEW_BURDEN"],
        "lower": 0.35,
        "upper": 0.59875,
        "interpretation": "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL",
    }
    assert receipt["sensitivity"]["stress_envelope"] == {
        "scenario_ids": [
            "LOW_REVIEW_BURDEN",
            "HIGH_REVIEW_BURDEN",
            "BREAK_EVEN_REVIEW_BURDEN",
            "ADVERSE_REVIEW_BURDEN",
        ],
        "lower": -0.1,
        "upper": 0.59875,
        "interpretation": "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL",
    }
    scenarios = {item["id"]: item for item in receipt["sensitivity"]["scenarios"]}
    assert scenarios["BREAK_EVEN_REVIEW_BURDEN"]["saving_rate"] == 0.0
    assert scenarios["ADVERSE_REVIEW_BURDEN"]["saving_rate"] == -0.1
    saving = _metric(receipt, "selective_rebase_cost_saving_rate")
    assert saving["evidence_class"] == "MODELLED_COUNTERFACTUAL"
    assert "never enterprise ROI" in saving["limitations"][0]
    assert all("75%" not in json.dumps(item) for item in receipt["metrics"])


def test_receipt_binds_same_target_universe_and_cost_model() -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)
    universe = receipt["target_universe"]["digest"]
    cost_model = receipt["cost_model"]["digest"]

    assert all(item["cost"]["target_universe_digest"] == universe for item in receipt["rebase_cases"])
    assert all(item["cost"]["cost_model_digest"] == cost_model for item in receipt["rebase_cases"])
    assert receipt["aggregate_cost"]["target_universe_digest"] == universe
    assert receipt["aggregate_cost"]["cost_model_digest"] == cost_model
    assert evaluator.verify_receipt(receipt, project_root=ROOT) == []


def test_evaluator_and_verifier_do_not_import_product_modules() -> None:
    for relative in (
        "scripts/run_quote_value_benchmark.py",
        "scripts/verify_quote_value_evidence.py",
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        product_imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                product_imports.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "orgrebase" or alias.name.startswith("orgrebase.")
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "orgrebase" or module.startswith("orgrebase."):
                    product_imports.append(module)
        assert product_imports == []


def test_evaluator_rejects_zero_denominator(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    suite = json.loads(EVALUATION_SUITE.read_text(encoding="utf-8"))
    metric = next(
        item for item in suite["reports"]["orgrebase-workspace"]["metrics"] if item["metric_id"] == "hard_dependency_recall"
    )
    metric["numerator"] = 0
    metric["denominator"] = 0
    metric["value"] = 0
    _redigest_content_address(evaluator, suite["reports"]["orgrebase-workspace"])
    path = _write_json(tmp_path / "evaluation-suite.json", suite)

    with pytest.raises(evaluator.QuoteValueError, match="ZERO_DENOMINATOR:hard_dependency_recall"):
        _build(evaluator, evaluation_suite_path=path)


def test_evaluator_rejects_missing_or_zero_review_cost(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    copied = tmp_path / "quote-value-v0.1"
    shutil.copytree(BENCHMARK_ROOT, copied)
    model_path = copied / "evaluator" / "action-cost-model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["components"]["REVIEW"] = 0
    _write_json(model_path, model)
    _rewrite_manifest(copied)

    with pytest.raises(evaluator.QuoteValueError, match="COST_COMPONENT_MUST_BE_POSITIVE:REVIEW"):
        _build(evaluator, benchmark_root=copied)


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("model_version", "quote-action-cost@999.0.0", "COST_MODEL_VERSION_INVALID"),
        ("unit", "USD", "COST_MODEL_UNIT_INVALID"),
        ("target_scope", "PER_QUOTE", "COST_MODEL_TARGET_SCOPE_INVALID"),
    ],
)
def test_evaluator_rejects_cost_model_contract_drift(
    tmp_path: Path,
    field: str,
    value: str,
    error_code: str,
) -> None:
    evaluator = _load_evaluator()
    copied = tmp_path / "quote-value-v0.1"
    shutil.copytree(BENCHMARK_ROOT, copied)
    model_path = copied / "evaluator" / "action-cost-model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model[field] = value
    _write_json(model_path, model)
    _rewrite_manifest(copied)

    with pytest.raises(evaluator.QuoteValueError, match=error_code):
        _build(evaluator, benchmark_root=copied)


def test_evaluator_rejects_forged_vmrc_content_digest(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    vmrc = json.loads(CURRENCY_VMRC.read_text(encoding="utf-8"))
    vmrc["digest"] = "sha256:" + ("0" * 64)
    path = _write_json(tmp_path / "currency-vmrc.json", vmrc)

    with pytest.raises(evaluator.QuoteValueError, match=r"VMRC_DIGEST:.*_INVALID"):
        _build(evaluator, currency_vmrc_path=path)


def test_evaluator_rejects_stale_owb_report_digest(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    suite = json.loads(EVALUATION_SUITE.read_text(encoding="utf-8"))
    metric = next(
        item for item in suite["reports"]["orgrebase-workspace"]["metrics"] if item["metric_id"] == "hard_dependency_recall"
    )
    metric["numerator"] = 71
    metric["value"] = round(71 / 72, 6)
    path = _write_json(tmp_path / "evaluation-suite.json", suite)

    with pytest.raises(evaluator.QuoteValueError, match="OWB_REPORT_DIGEST_MISMATCH"):
        _build(evaluator, evaluation_suite_path=path)


def test_evaluator_rejects_stale_product_observation_digest(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    observations = json.loads(PRODUCT_OBSERVATIONS.read_text(encoding="utf-8"))
    pp001 = next(item for item in observations["cases"] if item["id"] == "PP-001")
    pp001["facts"]["quote_version"] = "forged"
    path = _write_json(tmp_path / "product-path-observations.json", observations)

    with pytest.raises(evaluator.QuoteValueError, match="PRODUCT_OBSERVATION_DIGEST_MISMATCH"):
        _build(evaluator, product_observations_path=path)


def test_evaluator_rejects_predecessor_product_path_with_same_schema(
    tmp_path: Path,
) -> None:
    evaluator = _load_evaluator()
    observations = json.loads(PRODUCT_OBSERVATIONS.read_text(encoding="utf-8"))
    observations["benchmark_version"] = "ProductPath-v0.2-source-bound"
    path = _write_json(tmp_path / "product-path-observations.json", observations)

    with pytest.raises(
        evaluator.QuoteValueError,
        match="PRODUCT_OBSERVATION_BENCHMARK_VERSION_INVALID",
    ):
        _build(evaluator, product_observations_path=path)


def test_evaluator_rejects_target_universe_drift(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    launch = json.loads(LAUNCH_VMRC.read_text(encoding="utf-8"))
    launch["effects"] = launch["effects"][:-1]
    _redigest_content_address(evaluator, launch)
    path = _write_json(tmp_path / "launch-vmrc.json", launch)

    with pytest.raises(evaluator.QuoteValueError, match="TARGET_UNIVERSE_DRIFT:QV-LAUNCH-DATE-CHANGE"):
        _build(evaluator, launch_vmrc_path=path)


def test_evaluator_rejects_double_accountable_assignment(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    copied = tmp_path / "quote-value-v0.1"
    shutil.copytree(BENCHMARK_ROOT, copied)
    baseline_path = copied / "public" / "current-process-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["process_steps"][0]["accountable"] = ["GTM Owner", "Quote Operator"]
    _write_json(baseline_path, baseline)
    _rewrite_manifest(copied)

    with pytest.raises(evaluator.QuoteValueError, match="ACCOUNTABLE_INVALID:quote-step:intake"):
        _build(evaluator, benchmark_root=copied)


def test_evaluator_rejects_unregistered_accountable_semantics(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    copied = tmp_path / "quote-value-v0.1"
    shutil.copytree(BENCHMARK_ROOT, copied)
    baseline_path = copied / "public" / "current-process-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["process_steps"][1]["accountable"] = "Unregistered Banana Owner"
    _write_json(baseline_path, baseline)
    _rewrite_manifest(copied)

    with pytest.raises(
        evaluator.QuoteValueError,
        match="ACCOUNTABLE_ROLE_SEMANTIC_DRIFT:quote-step:product-confirmation",
    ):
        _build(evaluator, benchmark_root=copied)


def test_evaluator_rejects_invented_system_class_semantics(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    copied = tmp_path / "quote-value-v0.1"
    shutil.copytree(BENCHMARK_ROOT, copied)
    baseline_path = copied / "public" / "current-process-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["process_steps"][1]["systems"]["as_is_interface_class"] = "TELEPATHY"
    _write_json(baseline_path, baseline)
    _rewrite_manifest(copied)

    with pytest.raises(
        evaluator.QuoteValueError,
        match="SYSTEM_CLASS_SEMANTIC_DRIFT:quote-step:product-confirmation",
    ):
        _build(evaluator, benchmark_root=copied)


def test_strict_verifier_rejects_claim_ceiling_inflation() -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)
    mutated = copy.deepcopy(receipt)
    mutated["evidence_ceiling"] = "ENTERPRISE_ROI"
    _redigest(evaluator, mutated)

    failures = evaluator.verify_receipt(mutated, project_root=ROOT)

    assert "RECEIPT_DIGEST_MISMATCH" not in failures
    assert "RECEIPT_RECOMPUTATION_MISMATCH" in failures


def test_strict_verifier_rejects_input_digest_drift(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)
    copied = tmp_path / "evaluation-suite.json"
    copied.write_bytes(EVALUATION_SUITE.read_bytes() + b"\n")
    mutated = copy.deepcopy(receipt)
    source = next(item for item in mutated["source_evidence"] if item["id"] == "evaluation_suite")
    source["path"] = str(copied)

    failures = evaluator.verify_receipt(mutated, project_root=ROOT)

    assert "RECEIPT_DIGEST_MISMATCH" in failures
    assert "SOURCE_FILE_DIGEST_MISMATCH:evaluation_suite" in failures


def test_strict_verifier_rejects_not_run_metric_inflation() -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)
    mutated = copy.deepcopy(receipt)
    cycle = _metric(mutated, "quote_cycle_elapsed_minutes")
    cycle.update(
        {
            "value": 5.0,
            "numerator": 5.0,
            "denominator": 1.0,
            "status": "CALCULATED",
            "evidence_class": "OBSERVED_LOCAL_PRODUCT",
        }
    )
    _redigest(evaluator, mutated)

    failures = evaluator.verify_receipt(mutated, project_root=ROOT)

    assert "RECEIPT_DIGEST_MISMATCH" not in failures
    assert "RECEIPT_RECOMPUTATION_MISMATCH" in failures


def test_strict_verifier_rejects_redigested_target_universe_and_sensitivity_drift() -> None:
    evaluator = _load_evaluator()
    receipt = _build(evaluator)

    target_mutation = copy.deepcopy(receipt)
    target_mutation["target_universe"]["target_ids"] = target_mutation["target_universe"][
        "target_ids"
    ][:-1]
    target_mutation["target_universe"]["target_count"] = 3
    _redigest(evaluator, target_mutation)
    assert evaluator.verify_receipt(target_mutation, project_root=ROOT) == [
        "RECEIPT_RECOMPUTATION_MISMATCH"
    ]

    sensitivity_mutation = copy.deepcopy(receipt)
    sensitivity_mutation["sensitivity"]["stress_envelope"]["lower"] = -0.99
    _redigest(evaluator, sensitivity_mutation)
    assert evaluator.verify_receipt(sensitivity_mutation, project_root=ROOT) == [
        "RECEIPT_RECOMPUTATION_MISMATCH"
    ]


def _suite_for_contract(contract: str) -> tuple[dict, dict, str]:
    suite = json.loads(EVALUATION_SUITE.read_text(encoding="utf-8"))
    reports = suite["reports"]
    report = reports["orgrebase-workspace"]
    if contract == "legacy":
        baseline_ids = {
            "safe-single-agent-admitted-context", "unified-raw-context-stress",
            "natural-language-multi-agent", "broadcast-invalidate-all",
        }
        suite = {
            "reference": report,
            "baselines": {key: value for key, value in reports.items() if key in baseline_ids},
            "ablations": {key: value for key, value in reports.items()
                          if key not in baseline_ids and key != "orgrebase-workspace"},
        }
        source = "evaluation_suite:reference"
    else:
        source = "evaluation_suite:reports:orgrebase-workspace"
    return suite, report, source


@pytest.mark.parametrize("contract", ["legacy", "current"])
def test_explicit_suite_contract_keeps_exact_source_bytes_and_report_location(
    tmp_path: Path, contract: str,
) -> None:
    evaluator = _load_evaluator()
    suite, report, source_ref = _suite_for_contract(contract)
    path = _write_json(tmp_path / "evaluation-suite.json", suite)
    source_bytes = path.read_bytes()
    receipt = _build(evaluator, evaluation_suite_path=path)
    source = next(item for item in receipt["source_evidence"] if item["id"] == "evaluation_suite")
    assert source["file_sha256"] == "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    assert path.read_bytes() == source_bytes
    gate = next(item for item in receipt["input_gates"] if item["id"] == "owb_reference_metrics")
    assert gate["details"]["report_digest"] == report["digest"]
    assert _metric(receipt, "impact_exact_mismatch_case_rate")["source_refs"] == [
        f"{source_ref}:hard_dependency_recall"
    ]
    assert _metric(receipt, "unauthorized_access_success_rate")["source_refs"] == [
        f"{source_ref}:unauthorized_read_block_rate"
    ]
    assert evaluator.verify_receipt(receipt, project_root=ROOT) == []
    path.write_bytes(source_bytes + b"\n")
    assert evaluator.verify_receipt(receipt, project_root=ROOT) == [
        "SOURCE_FILE_DIGEST_MISMATCH:evaluation_suite"
    ]


@pytest.mark.parametrize("contract", ["legacy", "current"])
@pytest.mark.parametrize(("field", "value", "error"), [
    ("id", "evaluation:another-report", "OWB_REPORT_ID_INVALID"),
    ("system_profile", "safe-single-agent-admitted-context", "OWB_REFERENCE_PROFILE_INVALID"),
    ("benchmark_version", "OWB v99", "OWB_BENCHMARK_VERSION_INVALID"),
    ("mode", "live", "OWB_MODE_INVALID"),
    ("status", "FAIL", "OWB_REPORT_STATUS_INVALID"),
])
def test_both_suite_contracts_reject_redigested_wrong_report_identity(
    tmp_path: Path, contract: str, field: str, value: str, error: str,
) -> None:
    evaluator = _load_evaluator()
    suite, report, _ = _suite_for_contract(contract)
    report[field] = value
    _redigest_content_address(evaluator, report)
    with pytest.raises(evaluator.QuoteValueError, match=error):
        _build(evaluator, evaluation_suite_path=_write_json(tmp_path / "suite.json", suite))


@pytest.mark.parametrize("contract", ["legacy", "current"])
@pytest.mark.parametrize(("mutation", "error"), [
    ("boolean", "OWB_METRIC_VALUE_INVALID"),
    ("string", "OWB_METRIC_VALUE_INVALID"),
    ("value", "OWB_METRIC_VALUE_MISMATCH"),
    ("count", "OWB_METRIC_COUNTS_INVALID"),
    ("not_full_pass", "OWB_METRIC_NOT_FULL_PASS"),
    ("duplicate", "OWB_REFERENCE_METRIC_CARDINALITY"),
    ("missing", "OWB_REFERENCE_METRIC_CARDINALITY"),
])
def test_both_suite_contracts_reject_redigested_invalid_selected_metrics(
    tmp_path: Path, contract: str, mutation: str, error: str,
) -> None:
    evaluator = _load_evaluator()
    suite, report, _ = _suite_for_contract(contract)
    metric = next(item for item in report["metrics"] if item["metric_id"] == "hard_dependency_recall")
    if mutation == "boolean":
        metric["value"] = True
    elif mutation == "string":
        metric["value"] = "1.0"
    elif mutation == "value":
        metric["value"] = 0.0
    elif mutation == "count":
        metric["numerator"] = True
    elif mutation == "not_full_pass":
        metric["numerator"] = 71
        metric["value"] = round(71 / 72, 6)
    elif mutation == "duplicate":
        report["metrics"].append(copy.deepcopy(metric))
    elif mutation == "missing":
        report["metrics"].remove(metric)
    _redigest_content_address(evaluator, report)
    with pytest.raises(evaluator.QuoteValueError, match=error):
        _build(evaluator, evaluation_suite_path=_write_json(tmp_path / "suite.json", suite))


@pytest.mark.parametrize(("mutation", "error"), [
    ("mixed", "OWB_SUITE_STRUCTURE_INVALID"),
    ("unknown", "OWB_SUITE_STRUCTURE_INVALID"),
    ("missing_field", "OWB_SUITE_STRUCTURE_INVALID"),
    ("reports_list", "OWB_SUITE_STRUCTURE_INVALID"),
    ("missing_primary", "OWB_REFERENCE_PROFILE_INVALID"),
    ("benchmark", "OWB_SUITE_BENCHMARK_INVALID"),
    ("license", "OWB_SUITE_BENCHMARK_INVALID"),
    ("gold_count", "OWB_SUITE_BENCHMARK_INVALID"),
    ("case_count", "OWB_SUITE_CASE_COUNT_MISMATCH"),
    ("dataset_digest", "OWB_SUITE_DATASET_DIGEST_INVALID"),
    ("primary_status", "OWB_SUITE_PRIMARY_SUMMARY_MISMATCH"),
    ("primary_score", "OWB_SUITE_PRIMARY_SUMMARY_MISMATCH"),
])
def test_current_suite_rejects_mixed_or_inconsistent_envelopes(
    tmp_path: Path, mutation: str, error: str,
) -> None:
    evaluator = _load_evaluator()
    suite, report, _ = _suite_for_contract("current")
    if mutation == "mixed":
        suite["reference"] = report
    elif mutation == "unknown":
        suite = {"evaluations": suite["reports"]}
    elif mutation == "missing_field":
        suite.pop("dataset_digest")
    elif mutation == "reports_list":
        suite["reports"] = list(suite["reports"].values())
    elif mutation == "missing_primary":
        suite["reports"].pop("orgrebase-workspace")
    elif mutation == "benchmark":
        suite["benchmark"]["status"] = "FAIL"
    elif mutation == "license":
        suite["benchmark"]["license_gate"] = "FAIL"
    elif mutation == "gold_count":
        suite["benchmark"]["gold_count"] -= 1
    elif mutation == "case_count":
        suite["benchmark"]["case_count"] -= 1
        suite["benchmark"]["gold_count"] -= 1
    elif mutation == "dataset_digest":
        suite["dataset_digest"] = "sha256:" + "z" * 64
    elif mutation == "primary_status":
        suite["primary_status"] = "FAIL"
    elif mutation == "primary_score":
        suite["primary_score"] = 99.0
    with pytest.raises(evaluator.QuoteValueError, match=error):
        _build(evaluator, evaluation_suite_path=_write_json(tmp_path / "suite.json", suite))


def test_new_source_cannot_relabel_legacy_receipt_even_with_same_report_digest(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    receipts = {}
    for contract in ("legacy", "current"):
        suite, _, _ = _suite_for_contract(contract)
        path = _write_json(tmp_path / f"{contract}.json", suite)
        receipts[contract] = _build(evaluator, evaluation_suite_path=path)
    old, new = receipts["legacy"], receipts["current"]
    old_gate = next(item for item in old["input_gates"] if item["id"] == "owb_reference_metrics")
    new_gate = next(item for item in new["input_gates"] if item["id"] == "owb_reference_metrics")
    assert old_gate == new_gate
    new_source = next(item for item in new["source_evidence"] if item["id"] == "evaluation_suite")
    old_source = next(item for item in old["source_evidence"] if item["id"] == "evaluation_suite")
    assert old_source["file_sha256"] != new_source["file_sha256"]
    old_source.update(new_source)
    _redigest(evaluator, old)
    assert evaluator.verify_receipt(old, project_root=ROOT) == ["RECEIPT_RECOMPUTATION_MISMATCH"]
