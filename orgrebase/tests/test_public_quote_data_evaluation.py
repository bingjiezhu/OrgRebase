"""Source truth and the evaluation denominator cannot be replaced by PASS labels."""
import hashlib
import json
from copy import deepcopy
from fractions import Fraction

import pytest

from scripts.evaluate_public_quote_data import (
    annotate_replays,
    evaluate_prices,
    selected_sample,
    verify_replay,
    verify_retained_sample,
)
from scripts.run_public_quote_replay import SOURCE_PINS, quote_basket_input, run_case
from tests.test_public_quote_replay import invoice


def test_scientific_source_notation_is_expanded_exactly_without_repairing_source():
    original = invoice()
    original["lines"][0]["unit_price_decimal"] = "7.0000000000000007E-2"
    untouched = deepcopy(original)
    basket = quote_basket_input(original, "source:synthetic-boundary-test")
    assert basket["items"][0]["unit_price"] == "0.070000000000000007"
    assert Fraction(basket["items"][0]["unit_price"]) == Fraction(original["lines"][0]["unit_price_decimal"])
    assert original == untouched
    result = evaluate_prices([original])
    assert result["oracle_status"] == "PASS"
    assert result["priced_invoices"] == 1
    assert result["results"][0]["expanded_scientific_prices"][0]["original"] == "7.0000000000000007E-2"


def test_resource_limits_are_visible_in_the_denominator():
    valid = invoice()
    large = deepcopy(valid)
    large["invoice_id"] = "100002"
    large["lines"] = [{**valid["lines"][0], "source_row": n + 2} for n in range(201)]
    result = evaluate_prices([valid, large])
    assert result["eligible_invoices"] == 2 and result["priced_invoices"] == 1
    assert result["price_calculations"] == 2
    assert result["unsupported_invoices"][0]["invoice_id"] == "100002"
    assert result["unsupported_invoices"][0]["reasons"][0]["type"] == "too_long"
    assert evaluate_prices([large])["oracle_status"] == "NOT_RUN"


def test_source_comparison_rejects_plausible_but_rewritten_invoice(tmp_path):
    population = {"schema_version": "1.0", "source": SOURCE_PINS,
                  "rows_scanned": 541909, "quality_counts": {}, "excluded_examples": {},
                  "invoice_population": {"sampled_invoices": 1, "sampled_rows": 2},
                  "invoices": [invoice()]}
    edited = deepcopy(population)
    edited["invoices"][0]["lines"][0]["description"] = "REWRITTEN DESCRIPTION"
    path = tmp_path / "sample.json"
    raw = json.dumps(edited).encode()
    path.write_bytes(raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sample": {"sha256": hashlib.sha256(raw).hexdigest()}}))
    # An updated local digest and still-correct arithmetic cannot replace source equality.
    with pytest.raises(ValueError, match="ORIGINAL_ROWS_OR_SELECTION"):
        verify_retained_sample(population, path, manifest)


def test_additional_set_keeps_its_own_sample_denominator_without_mutating_population():
    population = {"invoices": [invoice()], "eligibility": {"sample_size": 99},
                  "invoice_population": {"sampled_invoices": 99, "sampled_rows": 199}}
    result = selected_sample(population, population["invoices"], "explicit test selection")
    assert result["invoice_population"]["sampled_invoices"] == 1
    assert result["invoice_population"]["sampled_rows"] == 2
    assert population["invoice_population"]["sampled_invoices"] == 99
    assert population["eligibility"]["sample_size"] == 99


def test_saved_replay_recheck_reads_evidence_instead_of_trusting_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic")
    original = invoice()
    case = run_case(tmp_path / "case-001", original, {"kind": "SYNTHETIC_TEST_ONLY"}, "sha256:" + "0" * 64)
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"cases": [case]}))
    sample = {"invoices": [original]}
    assert verify_replay(path, sample)["quotes"] == 2

    # Even with PASS unchanged, an edited summary must match the stored quote.
    edited = deepcopy(case)
    edited["after_pricing"]["total"] = "1.00"
    path.write_text(json.dumps({"cases": [edited]}))
    with pytest.raises(ValueError, match="REPORTED_PRICE_MISMATCH"):
        verify_replay(path, sample)

    # Rehashing an altered attachment does not authenticate its original rows.
    source_path = tmp_path / "case-001/source-transaction.json"
    source = json.loads(source_path.read_text())
    source["invoice"]["lines"][0]["description"] = "CHANGED AFTER RUN"
    raw = json.dumps(source).encode()
    source_path.chmod(0o600)  # Deliberate tampering of this synthetic test artifact.
    source_path.write_bytes(raw)
    case["source_digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    path.write_text(json.dumps({"cases": [case]}))
    with pytest.raises(ValueError, match="SOURCE_ROWS_MISMATCH"):
        verify_replay(path, sample)


def test_annotating_a_run_does_not_authenticate_an_edited_evaluation_sample(tmp_path):
    sample = {"schema_version": "1.0", "source": SOURCE_PINS, "rows_scanned": 541909,
              "invoice_population": {"sampled_invoices": 1, "sampled_rows": 2}, "invoices": [invoice()]}
    raw = json.dumps(sample).encode()
    report = {"evaluation_samples": {"extension-sample.json": {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()}}}
    (tmp_path / "report.json").write_text(json.dumps(report))
    sample["invoices"][0]["lines"][0]["description"] = "EDITED BEFORE REPLAY"
    (tmp_path / "extension-sample.json").write_text(json.dumps(sample))
    with pytest.raises(ValueError, match="SAMPLE_CHANGED_SINCE_SOURCE_REVIEW"):
        annotate_replays(tmp_path, extension_replay=tmp_path / "replay.json")
