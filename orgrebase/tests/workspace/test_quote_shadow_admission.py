from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from orgrebase.workspace.quote_observations import (
    METRIC_IDS,
    QuoteProcessObservationRecord,
    QuoteShadowAdmissionError,
    QuoteShadowAdmissionReceipt,
    ShadowEvidenceClass,
    admit_quote_observation,
    verify_quote_shadow_receipt,
)
from scripts.run_quote_shadow_admission import run as run_shadow_admission
from scripts.verify_quote_shadow_admission import verify_files

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmark" / "quote-value-v0.2-shadow"
SYNTHETIC_RECORD = BENCHMARK_ROOT / "synthetic" / "observation-record.json"
NOT_RUN_RECORD = BENCHMARK_ROOT / "public" / "not-run-template.json"


def _raw(path: Path = SYNTHETIC_RECORD) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _record(value: dict | None = None) -> QuoteProcessObservationRecord:
    return QuoteProcessObservationRecord.model_validate(value or _raw())


def _metric(receipt: QuoteShadowAdmissionReceipt, metric_id: str):
    return next(item for item in receipt.metrics if item.metric_id == metric_id)


def test_not_run_template_admits_without_inventing_values() -> None:
    record = _record(_raw(NOT_RUN_RECORD))
    receipt = admit_quote_observation(record, source_root=BENCHMARK_ROOT)

    assert receipt.admission_verdict == "ADMIT"
    assert receipt.measurement_status == "NOT_RUN"
    assert receipt.evidence_ceiling == ShadowEvidenceClass.NOT_RUN
    assert tuple(item.metric_id for item in receipt.metrics) == METRIC_IDS
    assert all(item.status == "NOT_RUN" for item in receipt.metrics)
    assert all(item.value is None for item in receipt.metrics)
    assert verify_quote_shadow_receipt(record, receipt, source_root=BENCHMARK_ROOT) == ()


def test_synthetic_contract_fixture_calculates_seven_bounded_metrics() -> None:
    record = _record()
    receipt = admit_quote_observation(record, source_root=BENCHMARK_ROOT)

    assert receipt.measurement_status == "CALCULATED"
    assert receipt.evidence_ceiling == ShadowEvidenceClass.SYNTHETIC_CONTRACT_FIXTURE
    assert tuple(item.metric_id for item in receipt.metrics) == METRIC_IDS
    assert all(item.status == "CALCULATED" for item in receipt.metrics)
    assert {item.metric_id: item.value for item in receipt.metrics} == {
        "quote_cycle_elapsed_minutes": 355.0,
        "policy_confirmation_active_minutes": 225.0,
        "adjudicated_impact_omission_rate": 0.5,
        "first_pass_rework_rate": 1.0,
        "unauthorized_access_success_rate": 0.5,
        "manual_escalation_case_rate": 1.0,
        "realized_selective_rebase_cost_saving_rate": 0.6,
    }
    assert all(
        item.evidence_class == ShadowEvidenceClass.SYNTHETIC_CONTRACT_FIXTURE for item in receipt.metrics
    )
    assert "synthetic contract-fixture" in receipt.limitations[-1]


def test_static_json_schemas_match_runtime_contracts() -> None:
    observation = json.loads(
        (ROOT / "schemas/workspace-quote-process-observation-record.schema.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (ROOT / "schemas/workspace-quote-shadow-admission-receipt.schema.json").read_text(encoding="utf-8")
    )
    runtime_observation = QuoteProcessObservationRecord.model_json_schema(mode="validation")
    runtime_receipt = QuoteShadowAdmissionReceipt.model_json_schema(mode="validation")
    assert {
        key: value for key, value in observation.items() if key not in {"$schema", "$id"}
    } == runtime_observation
    assert {key: value for key, value in receipt.items() if key not in {"$schema", "$id"}} == runtime_receipt


def test_relabelling_synthetic_sources_as_enterprise_shadow_fails() -> None:
    value = _raw()
    value["evidence_class"] = "OBSERVED_ENTERPRISE_SHADOW"

    with pytest.raises(ValidationError, match="EVIDENCE_CLASS_SOURCE_PROVENANCE_MISMATCH"):
        _record(value)


def test_unknown_role_and_wrong_registered_accountable_both_fail() -> None:
    unknown = _raw()
    unknown["role_registry"]["roles"][1]["role_id"] = "Unregistered Banana Owner"
    with pytest.raises(ValidationError, match="ROLE_REGISTRY_SEMANTIC_DRIFT"):
        _record(unknown)

    wrong = _raw()
    wrong["quote_observations"][0]["step_executions"][1]["accountable_role"] = "GTM Owner"
    wrong["quote_observations"][0]["step_executions"][1]["approval"]["approver_role"] = "GTM Owner"
    with pytest.raises(ValidationError, match="ACCOUNTABLE_ROLE_INVALID:quote-step:product-confirmation"):
        _record(wrong)


def test_invented_system_class_and_missing_five_party_coverage_fail() -> None:
    invented = _raw()
    invented["system_class_registry"]["classes"][1] = "TELEPATHY"
    invented["quote_observations"][0]["step_executions"][1]["system_class"] = "TELEPATHY"
    with pytest.raises(ValidationError, match="SYSTEM_CLASS_REGISTRY_SEMANTIC_DRIFT"):
        _record(invented)

    missing = _raw()
    missing["quote_observations"][0]["step_executions"][3]["accountable_role"] = "GTM Owner"
    missing["quote_observations"][0]["step_executions"][3]["approval"]["approver_role"] = "GTM Owner"
    with pytest.raises(ValidationError, match="ACCOUNTABLE_ROLE_INVALID:quote-step:finance-confirmation"):
        _record(missing)


def test_forged_source_digest_and_path_escape_fail_closed() -> None:
    forged = _raw()
    forged["source_file_refs"][0]["file_sha256"] = "sha256:" + "1" * 64
    with pytest.raises(QuoteShadowAdmissionError, match="SOURCE_DIGEST_MISMATCH:source:workflow"):
        admit_quote_observation(_record(forged), source_root=BENCHMARK_ROOT)

    escaped = _raw()
    escaped["source_file_refs"][0]["path"] = "../quote-value-v0.1/README.md"
    with pytest.raises(QuoteShadowAdmissionError, match="SOURCE_PATH_ESCAPE"):
        admit_quote_observation(_record(escaped), source_root=BENCHMARK_ROOT)


def test_out_of_window_event_and_wrong_source_kind_fail() -> None:
    outside = _raw()
    outside["quote_observations"][0]["access_attempts"][0]["occurred_at"] = "2026-07-01T19:00:00Z"
    with pytest.raises(ValidationError, match="ACCESS_EVENT_OUTSIDE_WINDOW"):
        _record(outside)

    wrong_kind = _raw()
    wrong_kind["source_file_refs"][2]["kind"] = "WORKFLOW_EVENT_LOG"
    with pytest.raises(ValidationError, match="EVENT_SOURCE_KIND_INVALID:source:access"):
        _record(wrong_kind)


def test_zero_denominator_is_not_run_not_zero() -> None:
    value = _raw()
    value["source_file_refs"] = [item for item in value["source_file_refs"] if item["id"] != "source:access"]
    value["quote_observations"][0]["access_attempts"] = []
    receipt = admit_quote_observation(_record(value), source_root=BENCHMARK_ROOT)

    unauthorized = _metric(receipt, "unauthorized_access_success_rate")
    assert unauthorized.status == "NOT_RUN"
    assert unauthorized.value is None
    assert unauthorized.numerator is None
    assert unauthorized.denominator is None
    assert unauthorized.evidence_class == ShadowEvidenceClass.NOT_RUN


def test_modelled_cost_cannot_be_promoted_to_realized_saving() -> None:
    value = _raw()
    value["quote_observations"][0]["selective_rebase_cost"]["comparator_basis"] = "MODELLED_COUNTERFACTUAL"
    receipt = admit_quote_observation(_record(value), source_root=BENCHMARK_ROOT)

    saving = _metric(receipt, "realized_selective_rebase_cost_saving_rate")
    assert saving.status == "NOT_RUN"
    assert saving.evidence_class == ShadowEvidenceClass.NOT_RUN


def test_receipt_replay_rejects_semantic_mutation() -> None:
    record = _record()
    receipt = admit_quote_observation(record, source_root=BENCHMARK_ROOT)
    mutated = receipt.model_dump(mode="json")
    mutated["metrics"][0]["value"] = 1.0
    mutated["digest"] = ""
    mutated_receipt = QuoteShadowAdmissionReceipt.model_validate(mutated)

    assert verify_quote_shadow_receipt(
        record,
        mutated_receipt,
        source_root=BENCHMARK_ROOT,
    ) == ("RECEIPT_RECOMPUTATION_MISMATCH",)


def test_retained_verification_uses_stable_logical_record_ref(tmp_path: Path) -> None:
    output_dir = tmp_path / "evidence"
    result = run_shadow_admission(
        argparse.Namespace(
            record=SYNTHETIC_RECORD,
            source_root=BENCHMARK_ROOT,
            output_dir=output_dir,
            project_root=ROOT,
        )
    )
    assert result["receipt"].startswith("content://")
    metadata = json.loads((output_dir / "verification.json").read_text(encoding="utf-8"))
    assert metadata["record_ref"] == ("benchmark://quote-value-v0.2-shadow/synthetic/observation-record.json")
    assert "/Users/" not in json.dumps(metadata)
    assert (
        verify_files(
            argparse.Namespace(
                record=SYNTHETIC_RECORD,
                source_root=BENCHMARK_ROOT,
                receipt=output_dir / "quote-shadow-admission-receipt.json",
                verification=output_dir / "verification.json",
                project_root=ROOT,
            )
        )
        == ()
    )


def test_manifest_addresses_all_fixture_inputs() -> None:
    entries = []
    for line in (BENCHMARK_ROOT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        entries.append(relative)
        path = BENCHMARK_ROOT / relative
        import hashlib

        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert entries == [
        "public/not-run-template.json",
        "synthetic/observation-record.json",
        "synthetic/sources/access-events.json",
        "synthetic/sources/active-time.json",
        "synthetic/sources/cost-ledger.json",
        "synthetic/sources/impact-adjudications.json",
        "synthetic/sources/workflow-events.json",
    ]
