"""Focused boundary tests; source baskets here are explicitly synthetic test fixtures."""

import hashlib
import importlib.util
import itertools
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_public_quote_replay.py"
_SPEC = importlib.util.spec_from_file_location("public_quote_replay", _SCRIPT)
assert _SPEC and _SPEC.loader
replay = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(replay)


def invoice():
    return {"invoice_id": "100001", "customer_id": "10000", "country": "United Kingdom",
            "invoice_date_serial": "40513.35", "lines": [
                {"source_row": 2, "invoice_no": "100001", "stock_code": "10001", "description": "SYNTHETIC TEST PRODUCT",
                 "quantity": 3, "unit_price_decimal": "0.125", "invoice_date_serial": "40513.35",
                 "customer_id": "10000", "country": "United Kingdom"},
                {"source_row": 3, "invoice_no": "100001", "stock_code": "10002", "description": "SYNTHETIC TEST PRODUCT",
                 "quantity": 2, "unit_price_decimal": "2.50", "invoice_date_serial": "40513.35",
                 "customer_id": "10000", "country": "United Kingdom"}],
            "derived": {"total_quantity": 5, "exact_subtotal_gbp": "5.375", "display_subtotal_gbp": "5.38",
                        "unit_price_min_gbp": "0.125", "unit_price_max_gbp": "2.50"}}


def test_governed_replay_uses_observed_fields_and_rejects_unauthorized_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic")
    root = tmp_path / "output"
    case = replay.run_case(root, invoice(), {"kind": "SYNTHETIC_TEST_ONLY"}, "sha256:" + "0" * 64)
    assert case["status"] == "PASS" and case["initial_formation_status"] == "COMPLETED"
    assert case["external_oracle"]["exact_subtotal_gbp"] == "5.375"
    assert case["currency_unchanged"] == "GBP" and case["basket_unchanged"]
    assert case["runtime_price_calculation"] == "DETERMINISTIC_QUOTE_RENDERER"
    assert case["before_pricing"]["subtotal"] == "5.38"
    assert case["before_pricing"]["tax_amount"] == "1.08"
    assert case["before_pricing"]["total"] == "6.46"
    assert case["after_pricing"]["discount_amount"] == "0.54"
    assert case["after_pricing"]["net_amount"] == "4.84"
    assert case["after_pricing"]["tax_amount"] == "0.97"
    assert case["after_pricing"]["total"] == "5.81"
    assert case["after_pricing"]["basket_digest"] == case["before_pricing"]["basket_digest"]
    assert case["after_pricing"]["policy_digest"] != case["before_pricing"]["policy_digest"]
    assert case["before_pricing"]["lines"] == case["after_pricing"]["lines"]
    assert case["pricing_verification"]["after"]["status"] == "PASS"
    assert case["negative_checks"]["unapproved_apply"]["status"] == "REFUSED"
    assert case["negative_checks"]["wrong_owner_approval"]["status"] == "REFUSED"
    assert case["negative_checks"]["rejection_preserves_quote"]["status"] == "PASS"
    knowledge = json.loads((root / "pack/components/knowledge.json").read_text())
    assert all(value["raw_private_value"] is None for value in knowledge["projection"]["source_values"])
    assert case["source_digest"] in (root / "field-attribution.json").read_text()
    assert case["synthetic_organization"] is True
    phases = case["measurement"]["phases"]
    assert {item["phase"] for item in phases} >= {"form_quote", "preview_change", "approve_change", "apply_change"}
    assert all(item["status"] == "PASS" and type(item["elapsed_ns"]) is int and item["elapsed_ns"] >= 0
               for item in phases)


@pytest.mark.parametrize("field", ["subtotal", "discount_amount", "net_amount", "tax_amount", "total"])
def test_pricing_oracle_rejects_tampered_runtime_amounts(field):
    original = invoice()
    basket = {"source_ref": "synthetic:test-basket", "items": []}
    policy = replay.controlled_pricing_policy("synthetic:test-policy", discount_bps=1000)
    actual = replay.pricing_oracle(original, discount_bps=1000, tax_bps=2000)
    actual.update(basket_source_ref=basket["source_ref"], policy_source_ref=policy["source_ref"],
                  basket_digest=replay.sha256_digest(basket), policy_digest=replay.sha256_digest(policy))
    actual[field] = "123.45"
    with pytest.raises(AssertionError, match=f"RUNTIME_PRICING_ORACLE_MISMATCH:{field}"):
        replay.verify_pricing(actual, original, basket, policy)


def test_demo_prepares_priced_pack_without_forming_or_approving(tmp_path):
    sample = tmp_path / "synthetic-sample.json"
    # Metadata fixture only. The runner explicitly does not authenticate source XLSX.
    sample.write_text(json.dumps({"schema_version": "1.0", "rows_scanned": 541909,
        "source": replay.SOURCE_PINS, "invoice_population": {"sampled_invoices": 1, "sampled_rows": 2},
        "invoices": [invoice()]}))
    target = tmp_path / "demo"
    result = replay.prepare_demo(sample, target, "100001")
    assert result["status"] == "PREPARED"
    assert result["formation"] == result["approval"] == "NOT_RUN"
    assert result["source_verification"].startswith("NOT_RUN")
    assert not (target / "demo-workspace.sqlite").exists()
    assert not (target / "workspace.sqlite").exists()
    runtime = replay.load_enterprise_quote_pilot_pack(result["pack_path"])
    assert runtime.profile.default_task.template_ref == "template:enterprise_quote@v2"
    assert runtime.source_values["pricing_policy"].value["discount_bps"] == 0
    assert "ORGREBASE_ENTERPRISE_PACK=" in result["start_command"]


def test_replay_rejects_inconsistent_or_ineligible_source_arithmetic():
    valid = invoice()
    valid["derived"]["exact_subtotal_gbp"] = "5.38"
    with pytest.raises(ValueError, match="SAMPLE_ORACLE_MISMATCH"):
        replay.basket_oracle(valid)
    invalid = invoice()
    invalid["lines"][0]["quantity"] = -3
    with pytest.raises(ValueError, match="POSITIVE_OBSERVED"):
        replay.basket_oracle(invalid)
    inconsistent = invoice()
    inconsistent["lines"][0]["country"] = "Japan"
    with pytest.raises(ValueError, match="INVOICE_LINE_IDENTITY_MISMATCH"):
        replay.basket_oracle(inconsistent)
    duplicate = invoice()
    duplicate["lines"].append({**duplicate["lines"][0], "source_row": 4})
    with pytest.raises(ValueError, match="DUPLICATE_SOURCE_OBSERVATION"):
        replay.basket_oracle(duplicate)


def test_replay_refuses_existing_outputs_and_live_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "openai-responses")
    with pytest.raises(ValueError, match="LOCAL_DETERMINISTIC"):
        replay.run_replay(tmp_path / "missing.json", tmp_path / "new")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic")
    with pytest.raises(FileExistsError, match="OUTPUT_MUST_BE_NEW"):
        replay.run_replay(tmp_path / "missing.json", tmp_path)
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"source": {"kind": "SYNTHETIC_TEST_ONLY"}, "invoices": [invoice()]}))
    with pytest.raises(ValueError, match="PUBLIC_SOURCE_PIN_OR_NATURE_MISMATCH"):
        replay.run_replay(sample, tmp_path / "new")


def measurement_sample(path):
    invoices = []
    for index in range(3):
        item = invoice()
        item["invoice_id"] = str(100001 + index)
        for line in item["lines"]:
            line["invoice_no"] = item["invoice_id"]
            line["source_row"] += index * 2
        invoices.append(item)
    path.write_text(json.dumps({"schema_version": "1.0", "rows_scanned": 541909,
        "source": replay.SOURCE_PINS, "invoice_population": {"sampled_invoices": 3, "sampled_rows": 6},
        "invoices": invoices}))
    return path


@pytest.mark.parametrize("invoice_id", ["../escape", r"..\escape", "100001/../../escape"])
def test_sample_path_components_are_rejected_before_any_output(tmp_path, monkeypatch, invoice_id):
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic")
    sample = measurement_sample(tmp_path / "synthetic-sample.json")
    payload = json.loads(sample.read_text())
    payload["invoices"][0]["invoice_id"] = invoice_id
    for line in payload["invoices"][0]["lines"]:
        line["invoice_no"] = invoice_id
    sample.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="ELIGIBLE_INVOICE_ID_REQUIRED"):
        replay.run_replay(sample, tmp_path / "output")
    assert set(tmp_path.iterdir()) == {sample}, "invalid IDs must not create a run directory or escape it"


@pytest.mark.parametrize("interrupted", [False, True])
def test_plan_precedes_work_and_failed_or_interrupted_cases_remain_in_denominator(tmp_path, monkeypatch, interrupted):
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic")
    sample = measurement_sample(tmp_path / "synthetic-sample.json")
    output = tmp_path / "measured"
    ticks = itertools.count(100, 10)
    monkeypatch.setattr(replay.time, "perf_counter_ns", lambda: next(ticks))
    attempted = []
    plan_bytes = None

    def controlled_case(root, item, source, digest, *, measurement):
        nonlocal plan_bytes
        current_plan = (output / "run-plan.json").read_bytes()
        if plan_bytes is None:
            plan_bytes = current_plan
            initial = json.loads((output / "report.json").read_text())
            assert len(initial["cases"]) == 3
            assert initial["measurement_summary"]["outcomes"] == {"PASS": 0, "FAILED": 0, "INCOMPLETE": 3}
        assert current_plan == plan_bytes, "the workload cannot be changed after seeing results"
        attempted.append(item["invoice_id"])
        with replay._phase(measurement, "form_quote"):
            if len(attempted) == 2:
                if interrupted:
                    raise KeyboardInterrupt("PRIVATE_PATH:/secret/customer.json")
                raise RuntimeError("PRIVATE_PATH:/secret/customer.json token=PRIVATE_TOKEN")
        return {"invoice_id": item["invoice_id"], "status": "PASS", "negative_check_count": 3,
                "before_pricing": {"currency": "GBP", "total": "6.4600"},
                "after_pricing": {"currency": "GBP", "total": "5.8100"}}

    monkeypatch.setattr(replay, "run_case", controlled_case)
    report = replay.run_replay(sample, output)
    assert (output / "run-plan.json").read_bytes() == plan_bytes
    assert report["run_plan_digest"] == "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    plan = json.loads(plan_bytes)
    assert hashlib.sha256((output / plan["runner_source"]).read_bytes()).hexdigest() == plan["runner_sha256"]
    assert report == json.loads((output / "report.json").read_text())
    summary = report["measurement_summary"]
    assert summary["planned_cases"] == 3
    if interrupted:
        assert attempted == ["100001", "100002"]
        assert report["status"] == "INCOMPLETE"
        assert summary["outcomes"] == {"PASS": 1, "FAILED": 0, "INCOMPLETE": 2}
        assert report["cases"][2]["execution_state"] == "NOT_STARTED"
        assert "measurement" not in report["cases"][2], "unstarted cases cannot acquire zero timings"
        assert summary["phases"]["form_quote"]["outcomes"]["INCOMPLETE"] == 1
    else:
        assert attempted == ["100001", "100002", "100003"], "a failed case does not hide later cases"
        assert report["status"] == "FAIL"
        assert summary["outcomes"] == {"PASS": 2, "FAILED": 1, "INCOMPLETE": 0}
        assert summary["phases"]["form_quote"]["outcomes"]["FAILED"] == 1
    assert summary["attempted_cases"] == len(attempted) == report["executed_invoice_count"]
    assert summary["all_attempted_elapsed"]["count"] == len(attempted)
    assert summary["qualified_completion_rate"] == str(replay.Decimal(summary["outcomes"]["PASS"]) / 3)
    for case in report["cases"][:len(attempted)]:
        assert case["measurement"]["elapsed_ns"] > 0
        assert json.loads((output / case["evidence_directory"] / "case-report.json").read_text()) == case
    for path in output.rglob("*"):
        if path.is_file():
            assert b"PRIVATE_PATH" not in path.read_bytes()
            assert b"PRIVATE_TOKEN" not in path.read_bytes()
    page = (output / "index.html").read_text()
    assert "全部预登记样本" in page
    assert page.count("6.4600 → 5.8100") == summary["outcomes"]["PASS"], "keep verified decimal strings verbatim"
    assert page.count('<td class="nowrap">未验证</td>') == 3 - summary["outcomes"]["PASS"]
    assert report["measurement"]["human_labor"] == report["measurement"]["monetary_cost"] == "NOT_MEASURED"


@pytest.mark.parametrize("status,interrupted,exit_code", [("FAIL", False, 1), ("INCOMPLETE", True, 130)])
def test_failed_measurement_cli_exits_nonzero_after_report_is_available(monkeypatch, tmp_path, status, interrupted, exit_code):
    output = tmp_path / "output"
    output.mkdir()
    retained = {"status": status, "executed_invoice_count": 1, "measurement": {"interrupted": interrupted}}
    replay.write_json(output / "report.json", retained)
    monkeypatch.setattr(replay, "run_replay", lambda *args, **kwargs: retained)
    monkeypatch.setattr(replay.sys, "argv", ["run_public_quote_replay.py", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        replay.main()
    assert error.value.code == exit_code
    assert json.loads((output / "report.json").read_text()) == retained
