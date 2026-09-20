"""Independently replay and verify the BPI 2019 real-process benchmark.

The verifier deliberately imports neither OrgRebase product code nor the
benchmark evaluator.  It reconstructs query truth and all metrics directly
from the sealed retained projection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECTION_SCHEMA = "orgrebase.workspace-public-real-process-projection.v1"
RECEIPT_SCHEMA = "orgrebase.workspace-public-real-process-benchmark-receipt.v1"
VERIFICATION_SCHEMA = "orgrebase.workspace-public-real-process-verification.v1"
BENCHMARK_VERSION = "QuoteValue-v0.4-bpi-real-process"
EVIDENCE_CLASS = "PUBLIC_REAL_PROCESS_RULE_DERIVED_MECHANISM_VALIDATION"
SOURCE_SHA256 = "sha256:af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"
ORGANIZATION_PROFILE = "LARGE_MULTINATIONAL_COATINGS_AND_PAINT_COMPANY"
EXPECTED_STRATEGIES = ("VENDOR_BROADCAST", "PURCHASE_DOCUMENT_SCOPE", "OAC_TYPED_ITEM_SCOPE")
FORBIDDEN_QUERY_LABEL_KEYS = {
    "ground_truth",
    "affected",
    "unaffected",
    "classification",
    "label",
    "expected_affected",
    "expected_unaffected",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _object_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sealed(value: dict[str, Any], code: str, failures: list[str]) -> None:
    claimed = value.get("digest")
    payload = dict(value)
    payload.pop("digest", None)
    if not isinstance(claimed, str) or claimed != _object_digest(payload):
        failures.append(f"{code}_DIGEST_MISMATCH")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _contains_forbidden_label(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN_QUERY_LABEL_KEYS.intersection(value)) or any(
            _contains_forbidden_label(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_label(child) for child in value)
    return False


def _selection_digest(query: dict[str, Any]) -> str:
    return _object_digest(
        {
            "source_sha256": SOURCE_SHA256,
            "task_contract": "BPI2019_CHANGE_PRICE_30D_DOWNSTREAM_V1",
            "case_ref": query.get("case_ref"),
            "change_event_ref": query.get("change_event", {}).get("event_ref"),
            "change_occurred_at": query.get("change_event", {}).get("occurred_at"),
        }
    )


def _provider_obligation(query: dict[str, Any]) -> dict[str, Any]:
    category = query.get("item_category")
    base = {
        "query_id": query.get("query_id"),
        "source_attribute": "Item Category",
        "observed_value": category,
        "evidence_class": "PROVIDER_DOCUMENTED_RULE_DERIVED",
    }
    if isinstance(category, str) and category.startswith("3-way match"):
        return {
            **base,
            "status": "PASS",
            "rule_id": "THREE_WAY_MATCH",
            "required_evidence_families": ["INVOICE_RECEIPT", "GOODS_OR_SERVICE_RECEIPT"],
            "reason": None,
        }
    if category == "2-way match":
        return {
            **base,
            "status": "PASS",
            "rule_id": "TWO_WAY_MATCH",
            "required_evidence_families": ["INVOICE_RECEIPT"],
            "reason": None,
        }
    return {
        **base,
        "status": "ABSTAIN",
        "rule_id": None,
        "required_evidence_families": [],
        "reason": "ITEM_CATEGORY_OUTSIDE_DOCUMENTED_MATCHING_RULE_SET",
    }


def _query_context(
    query: dict[str, Any], event_map: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None]:
    required = ("vendor_ref", "purchase_document_ref", "item_ref", "case_ref")
    identity_error = any(not isinstance(query.get(key), str) or not query[key] for key in required)
    change_at = _parse_time(query.get("change_event", {}).get("occurred_at"))
    window_end = _parse_time(query.get("window_end"))
    refs = query.get("candidate_event_refs")
    if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
        return [], "CANDIDATE_REFS_INVALID"
    candidates = [event_map[ref] for ref in refs if ref in event_map]
    if len(candidates) != len(refs):
        return candidates, "CANDIDATE_EVENT_UNKNOWN"
    if identity_error:
        return candidates, "REQUIRED_QUERY_IDENTITY_MISSING"
    if query.get("selection_digest") != _selection_digest(query):
        return candidates, "SELECTION_DIGEST_MISMATCH"
    if change_at is None or window_end is None or window_end - change_at != timedelta(days=30):
        return candidates, "QUERY_TIME_INVALID"
    for event in candidates:
        occurred = _parse_time(event.get("occurred_at") if event else None)
        if (
            occurred is None
            or event.get("vendor_ref") != query["vendor_ref"]
            or not (change_at < occurred <= window_end)
            or not event.get("purchase_document_ref")
            or not event.get("item_ref")
        ):
            return [], "CANDIDATE_LINEAGE_OR_SCOPE_INVALID"
    return candidates, None


def _metric(numerator: int, denominator: int, formula: str) -> dict[str, Any]:
    if denominator <= 0:
        raise ValueError(f"METRIC_DENOMINATOR_ZERO:{formula}")
    return {
        "value": round(numerator / denominator, 6),
        "numerator": numerator,
        "denominator": denominator,
        "formula": formula,
    }


def _recompute_strategy(
    strategy_id: str,
    queries: list[dict[str, Any]],
    event_map: dict[str, dict[str, Any]],
    vendor_action_count: int | None,
) -> dict[str, Any]:
    totals = {
        "true_positive": 0,
        "false_positive": 0,
        "true_negative": 0,
        "false_negative": 0,
        "unknown_ground_truth": 0,
        "conservative_action_scope": 0,
        "candidate_events": 0,
        "ground_truth_events": 0,
        "lineage_closed_events": 0,
        "abstained_queries": 0,
    }
    scenarios: list[dict[str, Any]] = []
    for query in queries:
        candidates, error = _query_context(query, event_map)
        universe = {row["event_ref"] for row in candidates}
        ground_truth = {
            row["event_ref"]
            for row in candidates
            if row["purchase_document_ref"] == query.get("purchase_document_ref")
            and row["item_ref"] == query.get("item_ref")
        }
        if error:
            selected: set[str] = set()
            unknown = universe
            status, reason = "ABSTAIN", error
        else:
            unknown = set()
            status, reason = "PASS", None
            if strategy_id == "VENDOR_BROADCAST":
                selected = universe
            elif strategy_id == "PURCHASE_DOCUMENT_SCOPE":
                selected = {
                    row["event_ref"]
                    for row in candidates
                    if row["purchase_document_ref"] == query["purchase_document_ref"]
                }
            elif strategy_id == "OAC_TYPED_ITEM_SCOPE":
                selected = {
                    row["event_ref"]
                    for row in candidates
                    if row["purchase_document_ref"] == query["purchase_document_ref"]
                    and row["item_ref"] == query["item_ref"]
                }
            else:
                raise ValueError(f"STRATEGY_UNSUPPORTED:{strategy_id}")
        unaffected = universe - selected - unknown
        tp = len(selected & ground_truth)
        fp = len(selected - ground_truth)
        fn = len(unaffected & ground_truth)
        unknown_gt = len(unknown & ground_truth)
        tn = len(unaffected - ground_truth)
        totals["true_positive"] += tp
        totals["false_positive"] += fp
        totals["true_negative"] += tn
        totals["false_negative"] += fn
        totals["unknown_ground_truth"] += unknown_gt
        totals["conservative_action_scope"] += len(selected | unknown)
        totals["candidate_events"] += len(universe)
        totals["ground_truth_events"] += len(ground_truth)
        totals["lineage_closed_events"] += sum(
            1
            for event in candidates
            if event.get("lineage", {}).get("source_sha256") == SOURCE_SHA256
            and isinstance(event.get("lineage", {}).get("trace_index"), int)
            and isinstance(event.get("lineage", {}).get("event_index"), int)
        )
        totals["abstained_queries"] += int(status == "ABSTAIN")
        scenarios.append(
            {
                "query_id": query.get("query_id"),
                "status": status,
                "reason": reason,
                "query_ground_truth": sorted(ground_truth),
                "query_ground_truth_evidence_class": "TASK_DEFINED_QUERY_GROUND_TRUTH",
                "affected_event_refs": sorted(selected),
                "unknown_event_refs": sorted(unknown),
                "unaffected_event_refs": sorted(unaffected),
                "counts": {
                    "true_positive": tp,
                    "false_positive": fp,
                    "true_negative": tn,
                    "false_negative": fn,
                    "unknown_ground_truth": unknown_gt,
                },
            }
        )
    precision_denominator = totals["true_positive"] + totals["false_positive"]
    recall_denominator = totals["ground_truth_events"]
    negative_denominator = totals["candidate_events"] - totals["ground_truth_events"]
    f1_denominator = (
        2 * totals["true_positive"]
        + totals["false_positive"]
        + totals["false_negative"]
        + totals["unknown_ground_truth"]
    )
    vendor_scope = totals["conservative_action_scope"] if vendor_action_count is None else vendor_action_count
    return {
        "strategy_id": strategy_id,
        "scenarios": scenarios,
        "counts": totals,
        "metrics": {
            "precision": _metric(totals["true_positive"], precision_denominator, "TP/(TP+FP)"),
            "recall": _metric(totals["true_positive"], recall_denominator, "TP/(TP+FN+UNKNOWN_GT)"),
            "f1": _metric(
                2 * totals["true_positive"], f1_denominator, "2TP/(2TP+FP+FN+UNKNOWN_GT)"
            ),
            "false_invalidation_rate": _metric(
                totals["false_positive"], negative_denominator, "FP/(FP+TN)"
            ),
            "safe_scope_reduction_vs_vendor_broadcast": _metric(
                vendor_scope - totals["conservative_action_scope"],
                vendor_scope,
                "(VENDOR_ACTION_SCOPE-CONSERVATIVE_ACTION_SCOPE)/VENDOR_ACTION_SCOPE",
            ),
            "unsafe_false_unaffected_rate": _metric(
                totals["false_negative"], recall_denominator, "FN/GROUND_TRUTH_EVENTS"
            ),
            "lineage_closure_rate": _metric(
                totals["lineage_closed_events"], totals["candidate_events"], "CLOSED_LINEAGE/CANDIDATE_EVENTS"
            ),
            "abstention_rate": _metric(
                totals["abstained_queries"], len(queries), "ABSTAINED_QUERIES/QUERIES"
            ),
        },
    }


def verify(receipt: dict[str, Any], project_root: Path) -> list[str]:
    failures: list[str] = []
    benchmark_root = project_root / "benchmark" / "quote-value-v0.4-bpi-real-process"
    try:
        projection = _load(benchmark_root / "projection" / "bpi2019-real-process-projection.json")
        dataset = _load(benchmark_root / "dataset-manifest.json")
        licenses = _load(benchmark_root / "LICENSES.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"BENCHMARK_LOAD_FAILED:{exc}"]
    _sealed(receipt, "RECEIPT", failures)
    _sealed(projection, "PROJECTION", failures)
    _sealed(dataset, "DATASET_MANIFEST", failures)
    _sealed(licenses, "LICENSE_MANIFEST", failures)

    expected_files = {
        "LICENSES.json",
        "dataset-manifest.json",
        "projection/bpi2019-real-process-projection.json",
    }
    manifest_hashes: dict[str, str] = {}
    try:
        lines = (benchmark_root / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
        for line in lines:
            digest, relative = line.split("  ", 1)
            if relative not in expected_files:
                failures.append(f"BENCHMARK_MANIFEST_PATH_UNEXPECTED:{relative}")
                continue
            actual = _file_digest(benchmark_root / relative)
            manifest_hashes[relative] = actual
            if digest != actual:
                failures.append(f"BENCHMARK_DIGEST_MISMATCH:{relative}")
    except (OSError, ValueError) as exc:
        failures.append(f"BENCHMARK_MANIFEST_INVALID:{exc}")
    if set(manifest_hashes) != expected_files:
        failures.append("BENCHMARK_MANIFEST_CLOSURE_MISMATCH")

    if projection.get("schema_version") != PROJECTION_SCHEMA:
        failures.append("PROJECTION_SCHEMA_UNSUPPORTED")
    if projection.get("data_domain") != "PURCHASE_TO_PAY":
        failures.append("DATA_DOMAIN_MUST_BE_P2P")
    if projection.get("evidence_class") != "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG":
        failures.append("REAL_SOURCE_EVIDENCE_CLASS_INVALID")
    if projection.get("claim_ceiling") != EVIDENCE_CLASS:
        failures.append("CLAIM_CEILING_INVALID")
    if projection.get("upstream", {}).get("sha256") != SOURCE_SHA256:
        failures.append("UPSTREAM_SHA256_DRIFT")
    if projection.get("upstream", {}).get("license") != "CC-BY-4.0":
        failures.append("UPSTREAM_LICENSE_DRIFT")
    task = projection.get("task_contract", {})
    if task.get("window_days") != 30:
        failures.append("QUERY_WINDOW_DRIFT")
    if task.get("query_ground_truth_available_to_strategy") is not False:
        failures.append("GROUND_TRUTH_INPUT_LEAKAGE")
    selection = projection.get("selection", {})
    expected_selection_counts = {
        "traces_scanned": 251_734,
        "events_scanned": 1_595_923,
        "activity_types_observed": 42,
        "anonymous_identities_observed": 627,
        "invalid_event_timestamps": 0,
    }
    if any(selection.get(key) != value for key, value in expected_selection_counts.items()):
        failures.append("SOURCE_SCAN_COUNTS_DRIFT")
    if selection.get("outcome_blind_ordering") is not True:
        failures.append("OUTCOME_BLIND_ORDERING_REQUIRED")
    queries = projection.get("queries", [])
    events = projection.get("observed_events", [])
    if not isinstance(queries, list) or len(queries) != 128:
        failures.append("QUERY_COUNT_DRIFT")
        return failures
    if any(_contains_forbidden_label(query) for query in queries):
        failures.append("QUERY_OUTPUT_LABEL_LEAKAGE")
    if [(row.get("selection_digest"), row.get("case_ref")) for row in queries] != sorted(
        (row.get("selection_digest"), row.get("case_ref")) for row in queries
    ):
        failures.append("QUERY_SELECTION_ORDER_DRIFT")
    if any(query.get("selection_digest") != _selection_digest(query) for query in queries):
        failures.append("QUERY_SELECTION_DIGEST_MISMATCH")
    event_map = {row.get("event_ref"): row for row in events if isinstance(row, dict)}
    if len(event_map) != len(events):
        failures.append("DUPLICATE_OBSERVED_EVENT_REF")
    for event in events:
        if event.get("evidence_class") != "OBSERVED_DOWNSTREAM_EVENT_PROXY":
            failures.append("DOWNSTREAM_EVIDENCE_CLASS_INVALID")
            break
        lineage = event.get("lineage", {})
        if (
            lineage.get("source_sha256") != SOURCE_SHA256
            or not isinstance(lineage.get("trace_index"), int)
            or not isinstance(lineage.get("event_index"), int)
        ):
            failures.append("EVENT_LINEAGE_INVALID")
            break
    query_errors = [error for query in queries if (error := _query_context(query, event_map)[1])]
    if query_errors:
        failures.append(f"QUERY_SEMANTICS_INVALID:{query_errors[0]}")

    if dataset.get("artifact", {}).get("sha256") != SOURCE_SHA256:
        failures.append("DATASET_SOURCE_SHA256_DRIFT")
    if dataset.get("organization_profile") != ORGANIZATION_PROFILE:
        failures.append("DATASET_ORGANIZATION_PROFILE_DRIFT")
    if dataset.get("artifact", {}).get("raw_bytes_in_repository") is not False:
        failures.append("RAW_SOURCE_REDISTRIBUTION_FORBIDDEN")
    if dataset.get("license") != "CC-BY-4.0":
        failures.append("DATASET_LICENSE_DRIFT")
    if dataset.get("projection_digest") != projection.get("digest"):
        failures.append("DATASET_PROJECTION_DIGEST_MISMATCH")
    if licenses.get("upstream", {}).get("spdx") != "CC-BY-4.0":
        failures.append("LICENSE_MANIFEST_DRIFT")
    if dataset.get("license_manifest_digest") != licenses.get("digest"):
        failures.append("DATASET_LICENSE_MANIFEST_DIGEST_MISMATCH")

    closure = receipt.get("input_closure", {})
    if closure.get("projection_digest") != projection.get("digest"):
        failures.append("RECEIPT_PROJECTION_DIGEST_MISMATCH")
    if closure.get("dataset_manifest_digest") != dataset.get("digest"):
        failures.append("RECEIPT_DATASET_DIGEST_MISMATCH")
    if closure.get("license_manifest_digest") != licenses.get("digest"):
        failures.append("RECEIPT_LICENSE_DIGEST_MISMATCH")
    if closure.get("manifest_file_sha256") != manifest_hashes:
        failures.append("RECEIPT_MANIFEST_HASHES_MISMATCH")

    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        failures.append("RECEIPT_SCHEMA_UNSUPPORTED")
    if receipt.get("benchmark_version") != BENCHMARK_VERSION:
        failures.append("BENCHMARK_VERSION_DRIFT")
    if receipt.get("data_domain") != "PURCHASE_TO_PAY":
        failures.append("RECEIPT_DOMAIN_MUST_BE_P2P")
    if receipt.get("evidence_class") != EVIDENCE_CLASS:
        failures.append("RECEIPT_EVIDENCE_CLASS_INVALID")
    if receipt.get("source_evidence_class") != "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG":
        failures.append("SOURCE_EVIDENCE_CLASS_INVALID")
    if receipt.get("query_ground_truth_evidence_class") != "TASK_DEFINED_QUERY_GROUND_TRUTH":
        failures.append("QUERY_GROUND_TRUTH_CLASS_INVALID")
    if receipt.get("outcome_proxy_evidence_class") != "OBSERVED_DOWNSTREAM_EVENT_PROXY":
        failures.append("OUTCOME_PROXY_CLASS_INVALID")
    not_run = receipt.get("not_run", {})
    if not_run != {
        "real_enterprise_quote_data": "NOT_RUN",
        "real_enterprise_quote_roi": "NOT_RUN",
        "production_deployment": "NOT_RUN",
    }:
        failures.append("CLAIM_PROMOTION_FORBIDDEN")
    expected_obligations = [_provider_obligation(query) for query in queries]
    if receipt.get("provider_rule_obligations") != expected_obligations:
        failures.append("PROVIDER_RULE_OBLIGATION_RECOMPUTATION_MISMATCH")

    if not query_errors and events:
        vendor = _recompute_strategy("VENDOR_BROADCAST", queries, event_map, None)
        vendor_scope = vendor["counts"]["conservative_action_scope"]
        expected = [
            vendor,
            _recompute_strategy("PURCHASE_DOCUMENT_SCOPE", queries, event_map, vendor_scope),
            _recompute_strategy("OAC_TYPED_ITEM_SCOPE", queries, event_map, vendor_scope),
        ]
        if receipt.get("strategies") != expected:
            failures.append("STRATEGY_RECOMPUTATION_MISMATCH")
        if tuple(row.get("strategy_id") for row in receipt.get("strategies", [])) != EXPECTED_STRATEGIES:
            failures.append("STRATEGY_SET_DRIFT")
        typed = expected[-1]
        if typed["metrics"]["recall"]["value"] != 1.0:
            failures.append("OAC_RECALL_GATE_FAILED")
        if typed["metrics"]["unsafe_false_unaffected_rate"]["value"] != 0.0:
            failures.append("OAC_UNSAFE_FALSE_UNAFFECTED_GATE_FAILED")
        if typed["metrics"]["safe_scope_reduction_vs_vendor_broadcast"]["value"] <= 0.0:
            failures.append("OAC_SCOPE_REDUCTION_GATE_FAILED")
    return sorted(set(failures))


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=root
        / "evidence"
        / "public-real-process"
        / "latest"
        / "bpi2019-real-process-benchmark-receipt.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root
        / "evidence"
        / "public-real-process"
        / "latest"
        / "bpi2019-real-process-verification.json",
    )
    parser.add_argument("--retained-output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = _load(args.receipt.resolve())
        failures = verify(receipt, args.project_root.resolve())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        receipt = {}
        failures = [f"VERIFICATION_INPUT_INVALID:{exc}"]
    result = {
        "schema_version": VERIFICATION_SCHEMA,
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "INDEPENDENT_OFFLINE_DETERMINISTIC_REPLAY",
        "implementation_independence": "NO_EVALUATOR_OR_PRODUCT_IMPORTS",
        "receipt_digest": receipt.get("digest"),
        "projection_digest": receipt.get("input_closure", {}).get("projection_digest"),
        "queries_replayed": receipt.get("scenario", {}).get("query_count", 0),
        "strategies_replayed": len(receipt.get("strategies", [])),
        "failures": failures,
    }
    result["digest"] = _object_digest(result)
    outputs = [args.output.resolve()]
    if args.retained_output:
        outputs.append(args.retained_output.resolve())
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
