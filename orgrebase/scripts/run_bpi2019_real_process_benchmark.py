"""Evaluate three scope contracts on the retained real BPI 2019 projection.

This evaluator imports no OrgRebase product module. Query Ground Truth is
reconstructed only after each strategy returns, from the exact purchase
document + item relation in the observed event slice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BENCHMARK_VERSION = "QuoteValue-v0.4-bpi-real-process"
PROJECTION_SCHEMA = "orgrebase.workspace-public-real-process-projection.v1"
RECEIPT_SCHEMA = "orgrebase.workspace-public-real-process-benchmark-receipt.v1"
EVIDENCE_CLASS = "PUBLIC_REAL_PROCESS_RULE_DERIVED_MECHANISM_VALIDATION"
SOURCE_SHA256 = "sha256:af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"
ORGANIZATION_PROFILE = "LARGE_MULTINATIONAL_COATINGS_AND_PAINT_COMPANY"
STRATEGY_IDS = ("VENDOR_BROADCAST", "PURCHASE_DOCUMENT_SCOPE", "OAC_TYPED_ITEM_SCOPE")
FORBIDDEN_QUERY_LABEL_KEYS = {
    "ground_truth",
    "affected",
    "unaffected",
    "classification",
    "label",
    "expected_affected",
    "expected_unaffected",
}


class RealProcessBenchmarkError(ValueError):
    """Stable fail-closed benchmark contract error."""


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


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RealProcessBenchmarkError(code)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealProcessBenchmarkError(f"INVALID_JSON:{path}") from exc
    _require(isinstance(value, dict), f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _verify_sealed(value: dict[str, Any], code: str) -> str:
    claimed = value.get("digest")
    _require(
        isinstance(claimed, str) and claimed.startswith("sha256:") and len(claimed) == 71,
        f"{code}_DIGEST_INVALID",
    )
    payload = dict(value)
    payload.pop("digest", None)
    _require(claimed == _object_digest(payload), f"{code}_DIGEST_MISMATCH")
    return claimed


def _verify_manifest(benchmark_root: Path) -> dict[str, str]:
    expected = {
        "LICENSES.json",
        "dataset-manifest.json",
        "projection/bpi2019-real-process-projection.json",
    }
    observed: dict[str, str] = {}
    try:
        lines = (benchmark_root / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RealProcessBenchmarkError("BENCHMARK_MANIFEST_MISSING") from exc
    for line in lines:
        if not line:
            continue
        parts = line.split("  ", 1)
        _require(len(parts) == 2, "BENCHMARK_MANIFEST_LINE_INVALID")
        digest, relative = parts
        _require(relative in expected, f"BENCHMARK_MANIFEST_PATH_UNEXPECTED:{relative}")
        target = (benchmark_root / relative).resolve()
        try:
            target.relative_to(benchmark_root.resolve())
        except ValueError as exc:
            raise RealProcessBenchmarkError(f"BENCHMARK_PATH_ESCAPE:{relative}") from exc
        _require(target.is_file(), f"BENCHMARK_FILE_MISSING:{relative}")
        actual = _file_digest(target)
        _require(actual == digest, f"BENCHMARK_DIGEST_MISMATCH:{relative}")
        observed[relative] = actual
    _require(set(observed) == expected, "BENCHMARK_MANIFEST_CLOSURE_MISMATCH")
    return observed


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _contains_forbidden_label(value: Any) -> bool:
    if isinstance(value, dict):
        if FORBIDDEN_QUERY_LABEL_KEYS.intersection(value):
            return True
        return any(_contains_forbidden_label(child) for child in value.values())
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


def _validate_projection(projection: dict[str, Any]) -> None:
    _verify_sealed(projection, "PROJECTION")
    _require(projection.get("schema_version") == PROJECTION_SCHEMA, "PROJECTION_SCHEMA_UNSUPPORTED")
    _require(projection.get("data_domain") == "PURCHASE_TO_PAY", "DATA_DOMAIN_MUST_BE_P2P")
    _require(
        projection.get("evidence_class") == "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
        "REAL_SOURCE_EVIDENCE_CLASS_INVALID",
    )
    _require(projection.get("claim_ceiling") == EVIDENCE_CLASS, "CLAIM_CEILING_INVALID")
    _require("QUOTE" not in projection.get("data_domain", ""), "REAL_P2P_RELABELLED_AS_QUOTE")
    upstream = projection.get("upstream", {})
    _require(upstream.get("status") == "PASS", "UPSTREAM_SOURCE_NOT_VERIFIED")
    _require(upstream.get("sha256") == SOURCE_SHA256, "UPSTREAM_SHA256_DRIFT")
    _require(upstream.get("license") == "CC-BY-4.0", "UPSTREAM_LICENSE_DRIFT")
    task = projection.get("task_contract", {})
    _require(task.get("window_days") == 30, "QUERY_WINDOW_DRIFT")
    _require(task.get("change_event_type") == "Change Price", "CHANGE_EVENT_TYPE_DRIFT")
    _require(task.get("query_ground_truth_available_to_strategy") is False, "GROUND_TRUTH_INPUT_LEAKAGE")
    _require(
        task.get("query_ground_truth")
        == "SAME_PURCHASE_DOCUMENT_AND_ITEM_EVENTS_IN_CANDIDATE_UNIVERSE",
        "QUERY_GROUND_TRUTH_CONTRACT_DRIFT",
    )
    _require(
        projection.get("provenance_legend")
        == [
            "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
            "TASK_DEFINED_QUERY_GROUND_TRUTH",
            "OBSERVED_DOWNSTREAM_EVENT_PROXY",
            "PROVIDER_DOCUMENTED_RULE_DERIVED",
        ],
        "PROVENANCE_LEGEND_DRIFT",
    )
    queries = projection.get("queries")
    events = projection.get("observed_events")
    _require(isinstance(queries, list) and len(queries) == 128, "QUERY_COUNT_DRIFT")
    _require(isinstance(events, list) and events, "OBSERVED_EVENTS_MISSING")
    _require(not any(_contains_forbidden_label(query) for query in queries), "QUERY_OUTPUT_LABEL_LEAKAGE")
    _require(
        [(row.get("selection_digest"), row.get("case_ref")) for row in queries]
        == sorted((row.get("selection_digest"), row.get("case_ref")) for row in queries),
        "QUERY_SELECTION_ORDER_DRIFT",
    )
    _require(
        all(row.get("selection_digest") == _selection_digest(row) for row in queries),
        "QUERY_SELECTION_DIGEST_MISMATCH",
    )
    selection = projection.get("selection", {})
    _require(selection.get("traces_scanned") == 251_734, "SOURCE_TRACE_COUNT_DRIFT")
    _require(selection.get("events_scanned") == 1_595_923, "SOURCE_EVENT_COUNT_DRIFT")
    _require(selection.get("activity_types_observed") == 42, "SOURCE_ACTIVITY_COUNT_DRIFT")
    _require(selection.get("anonymous_identities_observed") == 627, "SOURCE_IDENTITY_COUNT_DRIFT")
    _require(selection.get("invalid_event_timestamps") == 0, "SOURCE_TIMESTAMP_QUALITY_DRIFT")
    _require(selection.get("outcome_blind_ordering") is True, "OUTCOME_BLIND_ORDERING_REQUIRED")
    event_refs = [row.get("event_ref") for row in events]
    _require(len(event_refs) == len(set(event_refs)), "DUPLICATE_OBSERVED_EVENT_REF")
    known_events = set(event_refs)
    for event in events:
        _require(
            event.get("evidence_class") == "OBSERVED_DOWNSTREAM_EVENT_PROXY",
            "DOWNSTREAM_EVIDENCE_CLASS_INVALID",
        )
        lineage = event.get("lineage", {})
        _require(lineage.get("source_sha256") == SOURCE_SHA256, "EVENT_LINEAGE_SOURCE_DRIFT")
        _require(isinstance(lineage.get("trace_index"), int), "EVENT_LINEAGE_TRACE_MISSING")
        _require(isinstance(lineage.get("event_index"), int), "EVENT_LINEAGE_EVENT_MISSING")
    for query in queries:
        _require(query.get("candidate_event_refs"), f"QUERY_CANDIDATES_MISSING:{query.get('query_id')}")
        _require(
            set(query["candidate_event_refs"]).issubset(known_events),
            f"QUERY_CANDIDATE_UNKNOWN:{query.get('query_id')}",
        )
        change = query.get("change_event", {})
        _require(change.get("event_type") == "Change Price", "QUERY_CHANGE_EVENT_INVALID")
        _require(
            change.get("evidence_class") == "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
            "CHANGE_EVIDENCE_CLASS_INVALID",
        )
        lineage = change.get("lineage", {})
        _require(lineage.get("source_sha256") == SOURCE_SHA256, "CHANGE_LINEAGE_SOURCE_DRIFT")


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


def _strategy_selection(
    strategy_id: str,
    query: dict[str, Any],
    candidates: list[dict[str, Any]],
    error: str | None,
) -> tuple[set[str], set[str], str, str | None]:
    universe = {row["event_ref"] for row in candidates}
    if error:
        return set(), universe, "ABSTAIN", error
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
        raise RealProcessBenchmarkError(f"STRATEGY_UNSUPPORTED:{strategy_id}")
    return selected, set(), "PASS", None


def _metric(numerator: int, denominator: int, formula: str) -> dict[str, Any]:
    _require(denominator > 0, f"METRIC_DENOMINATOR_ZERO:{formula}")
    return {
        "value": round(numerator / denominator, 6),
        "numerator": numerator,
        "denominator": denominator,
        "formula": formula,
    }


def _evaluate_strategy(
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
        selected, unknown, status, reason = _strategy_selection(
            strategy_id, query, candidates, error
        )
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
    metrics = {
        "precision": _metric(totals["true_positive"], precision_denominator, "TP/(TP+FP)"),
        "recall": _metric(totals["true_positive"], recall_denominator, "TP/(TP+FN+UNKNOWN_GT)"),
        "f1": _metric(
            2 * totals["true_positive"],
            f1_denominator,
            "2TP/(2TP+FP+FN+UNKNOWN_GT)",
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
    }
    return {
        "strategy_id": strategy_id,
        "scenarios": scenarios,
        "counts": totals,
        "metrics": metrics,
    }


def build_receipt(*, benchmark_root: Path) -> dict[str, Any]:
    manifest_hashes = _verify_manifest(benchmark_root)
    projection = _load(benchmark_root / "projection" / "bpi2019-real-process-projection.json")
    dataset_manifest = _load(benchmark_root / "dataset-manifest.json")
    license_manifest = _load(benchmark_root / "LICENSES.json")
    _validate_projection(projection)
    _verify_sealed(dataset_manifest, "DATASET_MANIFEST")
    _verify_sealed(license_manifest, "LICENSE_MANIFEST")
    _require(
        dataset_manifest.get("projection_digest") == projection.get("digest"),
        "DATASET_PROJECTION_DIGEST_MISMATCH",
    )
    _require(dataset_manifest.get("license") == "CC-BY-4.0", "DATASET_LICENSE_DRIFT")
    _require(
        dataset_manifest.get("organization_profile") == ORGANIZATION_PROFILE,
        "DATASET_ORGANIZATION_PROFILE_DRIFT",
    )
    _require(
        dataset_manifest.get("artifact", {}).get("sha256") == SOURCE_SHA256,
        "DATASET_SOURCE_SHA256_DRIFT",
    )
    _require(
        dataset_manifest.get("artifact", {}).get("raw_bytes_in_repository") is False,
        "RAW_SOURCE_REDISTRIBUTION_FORBIDDEN",
    )
    _require(
        license_manifest.get("upstream", {}).get("spdx") == "CC-BY-4.0",
        "LICENSE_MANIFEST_DRIFT",
    )
    _require(
        dataset_manifest.get("license_manifest_digest") == license_manifest.get("digest"),
        "DATASET_LICENSE_MANIFEST_DIGEST_MISMATCH",
    )
    event_map = {row["event_ref"]: row for row in projection["observed_events"]}
    vendor = _evaluate_strategy("VENDOR_BROADCAST", projection["queries"], event_map, None)
    vendor_action_count = vendor["counts"]["conservative_action_scope"]
    strategies = [
        vendor,
        _evaluate_strategy(
            "PURCHASE_DOCUMENT_SCOPE", projection["queries"], event_map, vendor_action_count
        ),
        _evaluate_strategy("OAC_TYPED_ITEM_SCOPE", projection["queries"], event_map, vendor_action_count),
    ]
    typed = strategies[-1]
    obligations = [_provider_obligation(query) for query in projection["queries"]]
    _require(typed["metrics"]["recall"]["value"] == 1.0, "OAC_RECALL_GATE_FAILED")
    _require(
        typed["metrics"]["unsafe_false_unaffected_rate"]["value"] == 0.0,
        "OAC_UNSAFE_FALSE_UNAFFECTED_GATE_FAILED",
    )
    _require(
        typed["metrics"]["safe_scope_reduction_vs_vendor_broadcast"]["value"] > 0.0,
        "OAC_SCOPE_REDUCTION_GATE_FAILED",
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "benchmark_version": BENCHMARK_VERSION,
        "run_id": f"run:bpi2019-real-process:{projection['digest'].split(':', 1)[1][:16]}",
        "status": "PASS",
        "data_domain": "PURCHASE_TO_PAY",
        "evidence_class": EVIDENCE_CLASS,
        "source_evidence_class": "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
        "query_ground_truth_evidence_class": "TASK_DEFINED_QUERY_GROUND_TRUTH",
        "outcome_proxy_evidence_class": "OBSERVED_DOWNSTREAM_EVENT_PROXY",
        "claim_boundary": "NOT_REAL_QUOTE_DATA_NOT_CAUSAL_GROUND_TRUTH_NOT_REALIZED_ROI_NOT_PRODUCTION",
        "input_closure": {
            "projection_digest": projection["digest"],
            "dataset_manifest_digest": dataset_manifest["digest"],
            "license_manifest_digest": license_manifest["digest"],
            "manifest_file_sha256": manifest_hashes,
        },
        "scenario": {
            "task_id": projection["task_contract"]["task_id"],
            "query_count": len(projection["queries"]),
            "observed_event_count": len(projection["observed_events"]),
            "window_days": projection["task_contract"]["window_days"],
            "change_event_type": "Change Price",
            "ground_truth_available_to_strategy": False,
            "counting_unit": "QUERY_EVENT_PAIR",
        },
        "strategies": strategies,
        "provider_rule_obligations": obligations,
        "not_run": {
            "real_enterprise_quote_data": "NOT_RUN",
            "real_enterprise_quote_roi": "NOT_RUN",
            "production_deployment": "NOT_RUN",
        },
        "limitations": [
            "The source is a real anonymized P2P log, not an enterprise quote dataset.",
            "Query Ground Truth is a deterministic same-document-and-item lookup, not expert causal annotation.",
            "Observed downstream events are process proxies, not business success, labor, cost, or ROI outcomes.",
        ],
    }
    return {**receipt, "digest": _object_digest(receipt)}


def build_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return the compact, stable projection consumed by UI and submission views."""

    strategies = []
    for row in receipt["strategies"]:
        metrics = row["metrics"]
        strategies.append(
            {
                "strategy_id": row["strategy_id"],
                "action_scope_query_event_pairs": row["counts"]["conservative_action_scope"],
                "precision": metrics["precision"]["value"],
                "recall": metrics["recall"]["value"],
                "safe_scope_reduction_vs_vendor_broadcast": metrics[
                    "safe_scope_reduction_vs_vendor_broadcast"
                ]["value"],
                "unsafe_false_unaffected_rate": metrics["unsafe_false_unaffected_rate"]["value"],
                "lineage_closure_rate": metrics["lineage_closure_rate"]["value"],
                "abstention_rate": metrics["abstention_rate"]["value"],
            }
        )
    summary = {
        "schema_version": "orgrebase.workspace-public-real-process-summary.v1",
        "status": receipt["status"],
        "run_id": receipt["run_id"],
        "title_zh": "真实公开企业流程验证",
        "title_en": "Real Public Enterprise Process Validation",
        "data_domain": "PURCHASE_TO_PAY",
        "source_nature_zh": "大型跨国涂料企业的匿名真实 SAP 采购到付款事件日志",
        "source_nature_en": (
            "anonymized real SAP purchase-to-pay event log from a large multinational "
            "coatings and paints company"
        ),
        "query_count": receipt["scenario"]["query_count"],
        "unique_observed_event_count": receipt["scenario"]["observed_event_count"],
        "counting_unit": "QUERY_EVENT_PAIR",
        "strategies": strategies,
        "provider_rule_obligations": {
            "mapped_queries": sum(row["status"] == "PASS" for row in receipt["provider_rule_obligations"]),
            "abstained_queries": sum(
                row["status"] == "ABSTAIN" for row in receipt["provider_rule_obligations"]
            ),
            "evidence_class": "PROVIDER_DOCUMENTED_RULE_DERIVED",
        },
        "claim_boundary": receipt["claim_boundary"],
        "boundary_zh": (
            "验证真实流程上的规则化范围选择与血缘。它不代表真实报价数据、因果发现、企业 ROI 或生产上线。"
        ),
        "boundary_en": (
            "Validates rule-defined scope selection and lineage on a real process; not real quote "
            "data, causal discovery, enterprise ROI, or production deployment."
        ),
        "not_run": receipt["not_run"],
        "receipt_digest": receipt["digest"],
    }
    return {**summary, "digest": _object_digest(summary)}


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=root / "benchmark" / "quote-value-v0.4-bpi-real-process",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root
        / "evidence"
        / "public-real-process"
        / "latest"
        / "bpi2019-real-process-benchmark-receipt.json",
    )
    parser.add_argument("--retained-output", type=Path)
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=root / "evidence" / "public-real-process" / "latest" / "summary.json",
    )
    parser.add_argument("--retained-summary-output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = build_receipt(benchmark_root=args.benchmark_root.resolve())
        outputs = [args.output.resolve()]
        if args.retained_output:
            outputs.append(args.retained_output.resolve())
        for output in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = build_summary(receipt)
        summary_outputs = [args.summary_output.resolve()]
        if args.retained_summary_output:
            summary_outputs.append(args.retained_summary_output.resolve())
        for output in summary_outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, RealProcessBenchmarkError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
