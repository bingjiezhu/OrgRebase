from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.data_adapters.fetch_bpi_challenge_2019 import (
    DOI,
    EXPECTED_MD5,
    EXPECTED_SHA256,
    EXPECTED_SIZE,
    verify_source,
)
from scripts.run_bpi2019_real_process_benchmark import (
    RealProcessBenchmarkError,
    _object_digest,
    _query_context,
    _strategy_selection,
    _validate_projection,
    build_receipt,
    build_summary,
)
from scripts.verify_bpi2019_real_process_benchmark import verify

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark" / "quote-value-v0.4-bpi-real-process"
PROJECTION = BENCHMARK / "projection" / "bpi2019-real-process-projection.json"
RETAINED = BENCHMARK / "retained" / "bpi2019-real-process-benchmark-receipt.json"
RETAINED_SUMMARY = BENCHMARK / "retained" / "summary.json"
RETAINED_VERIFICATION = BENCHMARK / "retained" / "bpi2019-real-process-verification.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _seal(value: dict) -> dict:
    value = copy.deepcopy(value)
    value.pop("digest", None)
    value["digest"] = _object_digest(value)
    return value


def _rewrite_manifest(benchmark: Path) -> dict[str, str]:
    relatives = (
        "LICENSES.json",
        "dataset-manifest.json",
        "projection/bpi2019-real-process-projection.json",
    )
    hashes = {
        relative: hashlib.sha256((benchmark / relative).read_bytes()).hexdigest()
        for relative in relatives
    }
    (benchmark / "MANIFEST.sha256").write_text(
        "".join(f"{digest}  {relative}\n" for relative, digest in hashes.items()),
        encoding="utf-8",
    )
    return hashes


def _publish_mutated_projection(benchmark: Path, projection: dict) -> tuple[dict, dict[str, str]]:
    projection = _seal(projection)
    _write(benchmark / "projection" / "bpi2019-real-process-projection.json", projection)
    dataset = _load(benchmark / "dataset-manifest.json")
    dataset["projection_digest"] = projection["digest"]
    dataset = _seal(dataset)
    _write(benchmark / "dataset-manifest.json", dataset)
    return dataset, _rewrite_manifest(benchmark)


def _receipt_for_mutated_bundle(
    benchmark: Path, projection: dict, dataset: dict, manifest_hashes: dict[str, str]
) -> dict:
    receipt = _load(RETAINED)
    licenses = _load(benchmark / "LICENSES.json")
    receipt["input_closure"] = {
        "projection_digest": projection["digest"],
        "dataset_manifest_digest": dataset["digest"],
        "license_manifest_digest": licenses["digest"],
        "manifest_file_sha256": manifest_hashes,
    }
    return _seal(receipt)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def test_exact_official_source_metadata_is_pinned_and_raw_xes_is_absent() -> None:
    dataset = _load(BENCHMARK / "dataset-manifest.json")

    assert DOI == "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
    assert EXPECTED_SIZE == 728_558_522
    assert EXPECTED_MD5 == "4eb909242351193a61e1c15b9c3cc814"
    assert EXPECTED_SHA256 == "af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"
    assert dataset["dataset_kind"] == "REAL_ANONYMIZED_ENTERPRISE_PURCHASE_TO_PAY_EVENT_LOG"
    assert dataset["organization_profile"] == "LARGE_MULTINATIONAL_COATINGS_AND_PAINT_COMPANY"
    assert dataset["artifact"]["raw_bytes_in_repository"] is False
    assert dataset["license"] == "CC-BY-4.0"
    assert not list(BENCHMARK.rglob("*.xes"))


def test_offline_receipt_and_compact_summary_replay_exactly() -> None:
    built = build_receipt(benchmark_root=BENCHMARK)
    retained = _load(RETAINED)
    summary = _load(RETAINED_SUMMARY)
    verification = _load(RETAINED_VERIFICATION)

    assert built == retained
    assert build_summary(built) == summary
    assert verify(retained, ROOT) == []
    assert verification["status"] == "PASS"
    assert verification["queries_replayed"] == 128
    assert verification["implementation_independence"] == "NO_EVALUATOR_OR_PRODUCT_IMPORTS"


def test_real_projection_has_fixed_source_lineage_and_no_manual_labels() -> None:
    projection = _load(PROJECTION)
    serialized = json.dumps(projection, ensure_ascii=False).upper()

    assert projection["selection"] == {
        "traces_scanned": 251734,
        "events_scanned": 1595923,
        "activity_types_observed": 42,
        "anonymous_identities_observed": 627,
        "missing_identity_sentinel_events": 399090,
        "invalid_event_timestamps": 0,
        "change_queries_with_real_downstream_targets": 8463,
        "eligible_queries": 8318,
        "selected_queries": 128,
        "maximum_queries": 128,
        "ordering": "SHA256_PINNED_SOURCE_TASK_CASE_AND_LAST_CHANGE",
        "outcome_blind_ordering": True,
    }
    assert projection["task_contract"]["query_ground_truth_available_to_strategy"] is False
    assert len(projection["queries"]) == 128
    assert len(projection["observed_events"]) == 45227
    assert "SYNTHETIC" not in serialized
    for query in projection["queries"]:
        assert not {
            "ground_truth",
            "affected",
            "unaffected",
            "classification",
            "label",
            "expected_affected",
            "expected_unaffected",
        }.intersection(query)
    _validate_projection(projection)


def test_three_strategies_publish_exact_query_event_scope_and_safe_boundaries() -> None:
    receipt = _load(RETAINED)
    strategies = {row["strategy_id"]: row for row in receipt["strategies"]}

    assert receipt["scenario"]["counting_unit"] == "QUERY_EVENT_PAIR"
    assert receipt["scenario"]["ground_truth_available_to_strategy"] is False
    assert receipt["evidence_class"] == "PUBLIC_REAL_PROCESS_RULE_DERIVED_MECHANISM_VALIDATION"
    assert {
        strategy_id: row["counts"]["conservative_action_scope"]
        for strategy_id, row in strategies.items()
    } == {
        "VENDOR_BROADCAST": 66549,
        "PURCHASE_DOCUMENT_SCOPE": 3415,
        "OAC_TYPED_ITEM_SCOPE": 201,
    }
    assert strategies["PURCHASE_DOCUMENT_SCOPE"]["metrics"][
        "safe_scope_reduction_vs_vendor_broadcast"
    ]["value"] == 0.948684
    typed = strategies["OAC_TYPED_ITEM_SCOPE"]
    assert typed["metrics"]["safe_scope_reduction_vs_vendor_broadcast"]["value"] == 0.99698
    assert typed["metrics"]["precision"]["value"] == 1.0
    assert typed["metrics"]["recall"]["value"] == 1.0
    assert typed["metrics"]["unsafe_false_unaffected_rate"]["value"] == 0.0
    assert typed["metrics"]["lineage_closure_rate"]["value"] == 1.0
    assert receipt["not_run"] == {
        "real_enterprise_quote_data": "NOT_RUN",
        "real_enterprise_quote_roi": "NOT_RUN",
        "production_deployment": "NOT_RUN",
    }
    obligation_counts = {
        status: sum(row["status"] == status for row in receipt["provider_rule_obligations"])
        for status in ("PASS", "ABSTAIN")
    }
    assert obligation_counts == {"PASS": 127, "ABSTAIN": 1}
    assert all(
        row["evidence_class"] == "PROVIDER_DOCUMENTED_RULE_DERIVED"
        for row in receipt["provider_rule_obligations"]
    )


def test_evaluator_and_independent_verifier_do_not_import_product_or_each_other() -> None:
    evaluator_imports = _imports(ROOT / "scripts" / "run_bpi2019_real_process_benchmark.py")
    verifier_imports = _imports(ROOT / "scripts" / "verify_bpi2019_real_process_benchmark.py")

    for imports in (evaluator_imports, verifier_imports):
        assert not any(name == "orgrebase" or name.startswith("orgrebase.") for name in imports)
    assert "scripts.run_bpi2019_real_process_benchmark" not in verifier_imports


def test_missing_item_identity_abstains_and_never_becomes_unaffected() -> None:
    projection = _load(PROJECTION)
    event_map = {row["event_ref"]: row for row in projection["observed_events"]}
    query = copy.deepcopy(projection["queries"][0])
    query.pop("item_ref")

    candidates, error = _query_context(query, event_map)
    selected, unknown, status, reason = _strategy_selection(
        "OAC_TYPED_ITEM_SCOPE", query, candidates, error
    )

    assert status == "ABSTAIN"
    assert reason == "REQUIRED_QUERY_IDENTITY_MISSING"
    assert selected == set()
    assert unknown == set(query["candidate_event_refs"])


def test_output_label_leakage_is_rejected_even_after_resealing() -> None:
    projection = _load(PROJECTION)
    projection["queries"][0]["ground_truth"] = projection["queries"][0]["candidate_event_refs"][:1]
    projection = _seal(projection)

    with pytest.raises(RealProcessBenchmarkError, match="QUERY_OUTPUT_LABEL_LEAKAGE"):
        _validate_projection(projection)


def test_source_digest_drift_is_rejected_even_after_resealing() -> None:
    projection = _load(PROJECTION)
    projection["upstream"]["sha256"] = "sha256:" + "0" * 64
    projection = _seal(projection)

    with pytest.raises(RealProcessBenchmarkError, match="UPSTREAM_SHA256_DRIFT"):
        _validate_projection(projection)


@pytest.mark.parametrize("mutation", ["case_ref", "window_end", "delete_item"])
def test_independent_verifier_rejects_semantic_query_mutation(
    tmp_path: Path, mutation: str
) -> None:
    copied = tmp_path / "benchmark" / "quote-value-v0.4-bpi-real-process"
    shutil.copytree(BENCHMARK, copied)
    projection = _load(copied / "projection" / "bpi2019-real-process-projection.json")
    if mutation == "case_ref":
        projection["queries"][0]["case_ref"] += "-tampered"
    elif mutation == "window_end":
        current = datetime.fromisoformat(projection["queries"][0]["window_end"].replace("Z", "+00:00"))
        projection["queries"][0]["window_end"] = (
            (current + timedelta(days=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
    else:
        projection["queries"][0].pop("item_ref")
    dataset, manifest_hashes = _publish_mutated_projection(copied, projection)
    projection = _load(copied / "projection" / "bpi2019-real-process-projection.json")
    receipt = _receipt_for_mutated_bundle(copied, projection, dataset, manifest_hashes)

    failures = verify(receipt, tmp_path)
    assert any(failure.startswith("QUERY_") for failure in failures)


def test_claim_promotion_and_metric_forgery_fail_after_receipt_reseal() -> None:
    receipt = _load(RETAINED)
    receipt["data_domain"] = "ENTERPRISE_QUOTE"
    receipt["evidence_class"] = "OBSERVED_ENTERPRISE_SHADOW"
    receipt["not_run"]["real_enterprise_quote_roi"] = "OBSERVED"
    receipt["strategies"][-1]["metrics"]["safe_scope_reduction_vs_vendor_broadcast"][
        "value"
    ] = 1.0
    receipt = _seal(receipt)

    failures = verify(receipt, ROOT)
    assert "RECEIPT_DOMAIN_MUST_BE_P2P" in failures
    assert "RECEIPT_EVIDENCE_CLASS_INVALID" in failures
    assert "CLAIM_PROMOTION_FORBIDDEN" in failures
    assert "STRATEGY_RECOMPUTATION_MISMATCH" in failures


def test_wrong_source_bytes_fail_before_xml_parsing(tmp_path: Path) -> None:
    fake = tmp_path / "BPI_Challenge_2019.xes"
    fake.write_bytes(b"not-the-pinned-bpi-source")

    with pytest.raises(ValueError, match="SOURCE_SIZE_MISMATCH"):
        verify_source(fake)
