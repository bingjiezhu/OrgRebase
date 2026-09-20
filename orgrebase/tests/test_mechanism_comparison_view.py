from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.mechanism_comparison_view import (
    ABLATIONS,
    BASELINES,
    REGISTRY_PATH,
    SUITE_PATH,
    mechanism_comparison_view,
)
from orgrebase.semifinal_view import semifinal_evidence_view

ROOT = Path(__file__).resolve().parents[1]
QUOTE = "evidence/semifinal-closure/latest/quote-value/quote-value-receipt.json"


@pytest.fixture
def archive(tmp_path):
    # Exercise the retained contract using an explicit temporary fixture. The
    # current evaluator exports a different envelope and is not this archive.
    reports = json.loads((ROOT / SUITE_PATH).read_text())["reports"]
    suite = {"reference": reports["orgrebase-workspace"],
             "baselines": {name: reports[name] for name in BASELINES},
             "ablations": {name: reports[name] for name in ABLATIONS}}
    destination = tmp_path / SUITE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(suite))
    registry = tmp_path / REGISTRY_PATH
    registry.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / REGISTRY_PATH, registry)
    quote = json.loads((ROOT / QUOTE).read_text())
    source = next(s for s in quote["source_evidence"] if s["id"] == "evaluation_suite")
    source["file_sha256"] = "sha256:" + hashlib.sha256(destination.read_bytes()).hexdigest()
    _resign(quote)
    return tmp_path, quote


def _resign(value):
    value["digest"] = sha256_digest({k: v for k, v in value.items() if k != "digest"})


def _store(archive, suite, *, bind=True):
    root, quote = archive
    path = root / SUITE_PATH
    path.write_text(json.dumps(suite))
    if bind:
        source = next(s for s in quote["source_evidence"] if s["id"] == "evaluation_suite")
        source["file_sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return mechanism_comparison_view(root, quote)


def test_reference_comparison_retains_every_profile_and_hard_gate_failure(archive):
    comparison = mechanism_comparison_view(*archive)
    assert comparison["status"] == "PASS"
    assert len(comparison["rows"]) == 13
    assert sum(r["kind"] == "baselines" for r in comparison["rows"]) == 4
    assert sum(r["kind"] == "ablations" for r in comparison["rows"]) == 8
    control = comparison["rows"][-1]
    assert control["score"] == 97 and control["hard_gate_status"] == control["status"] == "FAIL"
    assert control["failed_gates"] == ["exact_skill_digest_execution"]
    assert comparison["current_task_run"] is False
    assert comparison["packaged_product_comparison"] is False
    assert comparison["live_model_comparison"] is False
    assert "completed_at" not in comparison


def test_current_suite_cannot_replace_the_different_historical_input_bytes():
    quote = json.loads((ROOT / QUOTE).read_text())
    expected = next(s["file_sha256"] for s in quote["source_evidence"] if s["id"] == "evaluation_suite")
    current = "sha256:" + hashlib.sha256((ROOT / SUITE_PATH).read_bytes()).hexdigest()
    assert current != expected
    comparison = mechanism_comparison_view(ROOT, quote)
    assert comparison["status"] == "UNAVAILABLE" and comparison["rows"] == []


@pytest.mark.parametrize("relative", [SUITE_PATH, REGISTRY_PATH])
def test_missing_optional_file_returns_no_numbers(archive, relative):
    root, quote = archive
    (root / relative).unlink()
    view = mechanism_comparison_view(root, quote)
    assert view["status"] == "UNAVAILABLE" and view["rows"] == []


def test_suite_byte_binding_rejects_even_resigned_report(archive):
    root, _quote = archive
    suite = json.loads((root / SUITE_PATH).read_text())
    suite["reference"]["completed_at"] = "2099-01-01T00:00:00Z"
    _resign(suite["reference"])
    assert _store(archive, suite, bind=False)["rows"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_profile",
        "extra_profile",
        "duplicate_case",
        "wrong_case",
        "missing_case",
        "version",
        "core_digest",
        "report_digest",
        "unknown_number",
        "bool_score",
        "threshold",
        "score",
        "gate_status",
        "overall_status",
        "duplicate_metric",
        "denominator",
        "missing_gate",
        "gate_metric_disagrees",
        "profile_mismatch",
        "mode",
    ],
)
def test_bound_but_inconsistent_suite_fails_closed(archive, mutation):
    root, _quote = archive
    suite = json.loads((root / SUITE_PATH).read_text())
    report = suite["reference"]
    if mutation == "missing_profile":
        suite["baselines"].pop(next(iter(suite["baselines"])))
    elif mutation == "extra_profile":
        suite["ablations"]["unexpected"] = report
    elif mutation == "duplicate_case":
        report["case_receipt_refs"][-1] = report["case_receipt_refs"][0]
    elif mutation == "wrong_case":
        report["case_receipt_refs"][0] = "case-run:unknown:orgrebase-workspace"
    elif mutation == "missing_case":
        report["case_receipt_refs"].pop()
    elif mutation == "version":
        report["benchmark_version"] = "OWB v99"
    elif mutation == "core_digest":
        report["core_score"]["digest"] = "sha256:" + "0" * 64
    elif mutation == "unknown_number":
        report["metrics"][0]["value"] = "UNKNOWN"
    elif mutation == "bool_score":
        report["core_score"]["weighted_score"] = True
    elif mutation == "threshold":
        report["core_score"]["pass_threshold"] = 0
    elif mutation == "score":
        report["core_score"]["weighted_score"] = 99
    elif mutation == "gate_status":
        report["hard_gate_results"][0]["status"] = "FAIL"
    elif mutation == "overall_status":
        report["status"] = report["core_score"]["status"] = "FAIL"
    elif mutation == "duplicate_metric":
        report["metrics"].append(report["metrics"][0])
    elif mutation == "denominator":
        report["metrics"][0]["denominator"] = 0
    elif mutation == "missing_gate":
        report["hard_gate_results"].pop()
    elif mutation == "gate_metric_disagrees":
        report["hard_gate_results"][0].update(value=0, status="FAIL")
    elif mutation == "profile_mismatch":
        report["core_score"]["system_profile"] = "other"
    elif mutation == "mode":
        report["mode"] = "live"
    if mutation != "core_digest":
        _resign(report["core_score"])
    _resign(report)
    if mutation == "report_digest":
        report["digest"] = "sha256:" + "0" * 64
    result = _store(archive, suite)
    assert result["status"] == "UNAVAILABLE"
    assert result["rows"] == []


def test_invalid_registry_cannot_redefine_pass(archive):
    root, quote = archive
    registry = root / REGISTRY_PATH
    value = json.loads(registry.read_text())
    value["owb_core_score"]["pass_threshold"] = 0
    registry.write_text(json.dumps(value))
    assert mechanism_comparison_view(root, quote)["rows"] == []


def test_duplicate_source_binding_is_ambiguous(archive):
    root, quote = archive
    quote["source_evidence"].append(
        next(s for s in quote["source_evidence"] if s["id"] == "evaluation_suite")
    )
    assert mechanism_comparison_view(root, quote)["rows"] == []


def test_optional_suite_failure_does_not_close_parent_archive(monkeypatch):
    from orgrebase import mechanism_comparison_view as module

    original = module._read

    def missing_suite(path):
        if str(path).endswith(SUITE_PATH):
            raise FileNotFoundError(path)
        return original(path)

    monkeypatch.setattr(module, "_read", missing_suite)
    view = semifinal_evidence_view(ROOT)
    assert view["status"] == "PASS"
    assert view["mechanism_comparison"]["status"] == "UNAVAILABLE"
    assert view["mechanism_comparison"]["rows"] == []
