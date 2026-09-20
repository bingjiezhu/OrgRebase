"""Run the offline Public Process Data Bridge mechanism benchmark.

This evaluator intentionally imports no ``orgrebase`` product module. Public
OCEL observations and the experimenter-injected causal overlay stay in a
separate evidence class from Enterprise Shadow observations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any

BENCHMARK_VERSION = "QuoteValue-v0.3-public-process"
RECEIPT_SCHEMA = "orgrebase.workspace-public-process-bridge-receipt.v1"
ALLOWED_ORIGINS = {"OBSERVED", "DERIVED", "EXPERIMENTER_INJECTED", "MISSING"}
STRATEGY_IDS = ("BROADCAST_ALL", "NAIVE_GRAPH", "OAC_TYPED_WITH_UNKNOWN")
ENTERPRISE_METRICS = (
    "quote_cycle_elapsed_minutes",
    "policy_confirmation_active_minutes",
    "adjudicated_impact_omission_rate",
    "first_pass_rework_rate",
    "unauthorized_access_success_rate",
    "manual_escalation_case_rate",
    "realized_selective_rebase_cost_saving_rate",
)


class PublicProcessBridgeError(ValueError):
    """Stable fail-closed contract error."""


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
        raise PublicProcessBridgeError(code)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicProcessBridgeError(f"INVALID_JSON:{path}") from exc
    _require(isinstance(value, dict), f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _verify_sealed(value: dict[str, Any], code: str) -> str:
    claimed = value.get("digest")
    _require(
        isinstance(claimed, str)
        and claimed.startswith("sha256:")
        and len(claimed) == 71
        and claimed != "sha256:" + "0" * 64,
        f"{code}_DIGEST_INVALID",
    )
    payload = dict(value)
    payload.pop("digest", None)
    _require(claimed == _object_digest(payload), f"{code}_DIGEST_MISMATCH")
    return claimed


def _verify_manifest(benchmark_root: Path) -> dict[str, str]:
    path = benchmark_root / "MANIFEST.sha256"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PublicProcessBridgeError("BENCHMARK_MANIFEST_MISSING") from exc
    expected_paths = {
        "LICENSES.json",
        "dataset-manifest.json",
        "overlay/policy-change-ground-truth.json",
        "projection/object-centric-projection.json",
    }
    observed: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        _require(len(parts) == 2, "BENCHMARK_MANIFEST_LINE_INVALID")
        digest, relative = parts
        _require(relative in expected_paths, f"BENCHMARK_MANIFEST_PATH_UNEXPECTED:{relative}")
        target = (benchmark_root / relative).resolve()
        try:
            target.relative_to(benchmark_root.resolve())
        except ValueError as exc:
            raise PublicProcessBridgeError(f"BENCHMARK_PATH_ESCAPE:{relative}") from exc
        _require(target.is_file(), f"BENCHMARK_FILE_MISSING:{relative}")
        actual = _file_digest(target)
        _require(digest == actual, f"BENCHMARK_DIGEST_MISMATCH:{relative}")
        observed[relative] = actual
    _require(set(observed) == expected_paths, "BENCHMARK_MANIFEST_CLOSURE_MISMATCH")
    return observed


def _validate_projection(projection: dict[str, Any]) -> None:
    _verify_sealed(projection, "PROJECTION")
    _require(
        projection.get("schema_version") == "orgrebase.workspace-public-process-projection.v1",
        "PROJECTION_SCHEMA_UNSUPPORTED",
    )
    _require(
        projection.get("evidence_class") == "PUBLIC_PROCESS_OBSERVED_DERIVED",
        "PUBLIC_EVIDENCE_CLASS_INVALID",
    )
    _require("ENTERPRISE_SHADOW" not in projection.get("evidence_class", ""), "SHADOW_PROMOTION_FORBIDDEN")
    _require(
        set(projection.get("provenance_legend", [])) == ALLOWED_ORIGINS,
        "PROVENANCE_LEGEND_DRIFT",
    )
    upstream = projection.get("upstream", {})
    _require(upstream.get("status") == "PASS", "UPSTREAM_SOURCE_NOT_VERIFIED")
    _require(upstream.get("doi") == "10.5281/zenodo.8337464", "UPSTREAM_DOI_DRIFT")
    _require(
        upstream.get("sha256") == "sha256:a71ee17ea394fad6fa6ac3c5e661c309cd8ca362cde838bf38fee62496bf6f40",
        "UPSTREAM_SHA256_DRIFT",
    )
    table_names = (
        "objects",
        "events",
        "event_object_relations",
        "object_object_relations",
        "object_attribute_history",
    )
    _require(
        all(isinstance(projection.get(name), list) for name in table_names), "OBJECT_CENTRIC_TABLE_MISSING"
    )
    _require("cases" not in projection and "case_id" not in projection, "FLATTENED_CASE_LOG_FORBIDDEN")
    counts = projection.get("table_counts", {})
    _require(
        all(counts.get(name) == len(projection[name]) for name in table_names),
        "TABLE_COUNT_MISMATCH",
    )
    object_refs = [row.get("object_ref") for row in projection["objects"]]
    _require(len(object_refs) == len(set(object_refs)), "DUPLICATE_OBJECT_REF")
    known_objects = set(object_refs)
    event_refs = [row.get("event_ref") for row in projection["events"]]
    _require(len(event_refs) == len(set(event_refs)), "DUPLICATE_EVENT_REF")
    known_events = set(event_refs)
    for table_name in table_names:
        for row in projection[table_name]:
            _require(row.get("origin") in ALLOWED_ORIGINS, f"ORIGIN_INVALID:{table_name}")
    for row in projection["object_object_relations"]:
        _require(row.get("source_ref") in known_objects, "RELATION_SOURCE_UNKNOWN")
        _require(row.get("target_ref") in known_objects, "RELATION_TARGET_UNKNOWN")
        _require(isinstance(row.get("qualifier"), str) and row["qualifier"], "RELATION_QUALIFIER_MISSING")
    for row in projection["event_object_relations"]:
        _require(row.get("event_ref") in known_events, "EVENT_RELATION_EVENT_UNKNOWN")
        _require(row.get("object_ref") in known_objects, "EVENT_RELATION_OBJECT_UNKNOWN")
    open_orders = projection.get("derived_views", {}).get("open_order_refs", [])
    _require(len(open_orders) == 11 and len(set(open_orders)) == 11, "OPEN_ORDER_UNIVERSE_DRIFT")


def _validate_overlay(overlay: dict[str, Any], projection: dict[str, Any]) -> None:
    _verify_sealed(overlay, "OVERLAY")
    _require(
        overlay.get("schema_version") == "orgrebase.workspace-public-causal-overlay.v1",
        "OVERLAY_SCHEMA_UNSUPPORTED",
    )
    _require(
        overlay.get("evidence_class") == "PUBLIC_SOURCE_DERIVED_SYNTHETIC",
        "OVERLAY_EVIDENCE_CLASS_INVALID",
    )
    _require("ENTERPRISE_SHADOW" not in overlay.get("evidence_class", ""), "SHADOW_PROMOTION_FORBIDDEN")
    _require(overlay.get("projection_digest") == projection.get("digest"), "OVERLAY_PROJECTION_MISMATCH")
    policy = overlay.get("policy_change", {})
    _require(policy.get("source_attribute_origin") == "OBSERVED", "POLICY_SOURCE_ORIGIN_INVALID")
    _require(
        policy.get("policy_interpretation_origin") == "EXPERIMENTER_INJECTED",
        "POLICY_INTERPRETATION_MUST_BE_INJECTED",
    )
    truth = overlay.get("ground_truth", {})
    _require(truth.get("origin") == "EXPERIMENTER_INJECTED", "GROUND_TRUTH_ORIGIN_INVALID")
    universe = overlay.get("target_universe", [])
    affected = truth.get("expected_affected", [])
    unaffected = truth.get("expected_unaffected", [])
    _require(len(universe) == len(set(universe)) == 11, "TARGET_UNIVERSE_INVALID")
    _require(set(affected).isdisjoint(unaffected), "GROUND_TRUTH_OVERLAP")
    _require(set(affected) | set(unaffected) == set(universe), "GROUND_TRUTH_NOT_TOTAL")
    _require(len(affected) == 5, "GROUND_TRUTH_AFFECTED_COUNT_DRIFT")
    claims = overlay.get("dependency_claims", [])
    _require(len(claims) == len(universe), "DEPENDENCY_CLAIM_COVERAGE_MISMATCH")
    _require({row.get("target_ref") for row in claims} == set(universe), "DEPENDENCY_TARGET_DRIFT")
    allowed_pairs = {
        ("ACTUAL_READ", "EXPERIMENTER_INJECTED"),
        ("EXPLICIT_NON_DEPENDENCY", "EXPERIMENTER_INJECTED"),
        ("UNKNOWN", "MISSING"),
    }
    for row in claims:
        _require("classification" not in row, "DEPENDENCY_OUTPUT_LABEL_LEAKAGE")
        key = (row.get("dependency_kind"), row.get("origin"))
        _require(key in allowed_pairs, f"DEPENDENCY_SEMANTICS_INVALID:{row.get('target_ref')}")
        if row["dependency_kind"] == "ACTUAL_READ":
            _require(row["target_ref"] in affected, "INJECTED_ACTUAL_READ_TRUTH_MISMATCH")
        if row["dependency_kind"] == "EXPLICIT_NON_DEPENDENCY":
            _require(row["target_ref"] in unaffected, "INJECTED_NON_DEPENDENCY_TRUTH_MISMATCH")


def _validate_mapping(mapping: dict[str, Any]) -> None:
    _verify_sealed(mapping, "MAPPING")
    _require(
        mapping.get("schema_version") == "orgrebase.workspace-public-process-mapping.v1",
        "MAPPING_SCHEMA_UNSUPPORTED",
    )
    _require(
        mapping.get("evidence_class") == "PUBLIC_PROCESS_OBSERVED_DERIVED", "MAPPING_EVIDENCE_CLASS_INVALID"
    )
    _require(mapping.get("missing_semantics") == "UNKNOWN", "MAPPING_MISSING_MUST_BE_UNKNOWN")
    rules = {row.get("source_qualifier"): row for row in mapping.get("relation_rules", [])}
    for qualifier in ("is a", "comprises"):
        _require(rules.get(qualifier, {}).get("causal_traversal") is True, f"CAUSAL_RULE_MISSING:{qualifier}")
        _require(
            rules[qualifier].get("dependency_kind") == "INFERRED", f"CAUSAL_RULE_KIND_INVALID:{qualifier}"
        )
    for qualifier in ("places", "primarySalesRep", "secondarySalesRep"):
        _require(
            rules.get(qualifier, {}).get("causal_traversal") is False, f"CONTEXT_RULE_UNSAFE:{qualifier}"
        )
    forbidden = set(mapping.get("forbidden_promotions", []))
    _require("OBSERVED_ENTERPRISE_SHADOW" in forbidden, "SHADOW_PROMOTION_GUARD_MISSING")
    _require("REALIZED_ENTERPRISE_ROI" in forbidden, "ROI_PROMOTION_GUARD_MISSING")


def _naive_graph_targets(projection: dict[str, Any], source_ref: str, universe: set[str]) -> set[str]:
    graph: dict[str, set[str]] = {}
    for row in projection["object_object_relations"]:
        source = row["source_ref"]
        target = row["target_ref"]
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set()).add(source)
    seen = {source_ref}
    queue = deque([source_ref])
    while queue:
        current = queue.popleft()
        for candidate in graph.get(current, set()):
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    return seen & universe


def _metric(numerator: int, denominator: int, formula: str) -> dict[str, Any]:
    _require(denominator > 0, f"ZERO_DENOMINATOR:{formula}")
    return {
        "value": round(numerator / denominator, 6),
        "numerator": numerator,
        "denominator": denominator,
        "formula": formula,
    }


def _score_strategy(
    strategy_id: str,
    *,
    affected: set[str],
    unknown: set[str],
    universe: set[str],
    truth: set[str],
) -> dict[str, Any]:
    classified_unaffected = universe - affected - unknown
    true_unaffected = universe - truth
    tp = len(affected & truth)
    fp = len(affected & true_unaffected)
    fn = len(classified_unaffected & truth)
    tn = len(classified_unaffected & true_unaffected)
    action_scope = affected | unknown
    return {
        "strategy_id": strategy_id,
        "classifications": {
            "affected": sorted(affected),
            "unknown": sorted(unknown),
            "unaffected": sorted(classified_unaffected),
        },
        "counts": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "conservative_action_scope": len(action_scope),
        },
        "metrics": {
            "precision": _metric(tp, tp + fp, "TP/(TP+FP)"),
            "recall": _metric(tp, len(truth), "TP/ACTUAL_AFFECTED"),
            "false_invalidation_rate": _metric(fp, len(true_unaffected), "FP/ACTUAL_UNAFFECTED"),
            "scope_reduction_rate": _metric(
                len(universe) - len(action_scope),
                len(universe),
                "(TARGET_UNIVERSE-CONSERVATIVE_ACTION_SCOPE)/TARGET_UNIVERSE",
            ),
            "unsafe_false_unaffected_rate": _metric(
                fn,
                len(truth),
                "ACTUAL_AFFECTED_CLASSIFIED_UNAFFECTED/ACTUAL_AFFECTED",
            ),
        },
    }


def build_receipt(
    *,
    project_root: Path,
    benchmark_root: Path,
    mapping_path: Path,
) -> dict[str, Any]:
    manifest_entries = _verify_manifest(benchmark_root)
    dataset_manifest = _load(benchmark_root / "dataset-manifest.json")
    licenses = _load(benchmark_root / "LICENSES.json")
    projection = _load(benchmark_root / "projection" / "object-centric-projection.json")
    overlay = _load(benchmark_root / "overlay" / "policy-change-ground-truth.json")
    mapping = _load(mapping_path)
    _verify_sealed(dataset_manifest, "DATASET_MANIFEST")
    _verify_sealed(licenses, "LICENSE_MANIFEST")
    _validate_projection(projection)
    _validate_overlay(overlay, projection)
    _validate_mapping(mapping)
    _require(
        dataset_manifest.get("projection_digest") == projection["digest"],
        "DATASET_PROJECTION_DIGEST_MISMATCH",
    )
    _require(dataset_manifest.get("overlay_digest") == overlay["digest"], "DATASET_OVERLAY_DIGEST_MISMATCH")
    _require(dataset_manifest.get("mapping_digest") == mapping["digest"], "DATASET_MAPPING_DIGEST_MISMATCH")
    _require(licenses.get("upstream", {}).get("spdx") == "CC-BY-4.0", "UPSTREAM_LICENSE_INVALID")

    universe = set(overlay["target_universe"])
    truth = set(overlay["ground_truth"]["expected_affected"])
    source_ref = overlay["policy_change"]["changed_object_ref"]
    broadcast = set(universe)
    naive = _naive_graph_targets(projection, source_ref, universe)
    typed_affected = {
        row["target_ref"]
        for row in overlay["dependency_claims"]
        if row["dependency_kind"] in {"ACTUAL_READ", "DECLARED", "INFERRED"}
    }
    typed_unknown = {
        row["target_ref"] for row in overlay["dependency_claims"] if row["dependency_kind"] == "UNKNOWN"
    }
    strategies = [
        _score_strategy(
            "BROADCAST_ALL",
            affected=broadcast,
            unknown=set(),
            universe=universe,
            truth=truth,
        ),
        _score_strategy(
            "NAIVE_GRAPH",
            affected=naive,
            unknown=set(),
            universe=universe,
            truth=truth,
        ),
        _score_strategy(
            "OAC_TYPED_WITH_UNKNOWN",
            affected=typed_affected,
            unknown=typed_unknown,
            universe=universe,
            truth=truth,
        ),
    ]
    _require(tuple(row["strategy_id"] for row in strategies) == STRATEGY_IDS, "STRATEGY_SET_DRIFT")
    oac = strategies[-1]
    _require(oac["metrics"]["recall"]["value"] == 1.0, "OAC_RECALL_GATE_FAILED")
    _require(oac["metrics"]["unsafe_false_unaffected_rate"]["value"] == 0.0, "OAC_UNSAFE_GATE_FAILED")
    _require(oac["metrics"]["scope_reduction_rate"]["value"] > 0, "OAC_SCOPE_GATE_FAILED")

    def relative(path: Path) -> str:
        return path.resolve().relative_to(project_root.resolve()).as_posix()

    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "benchmark_version": BENCHMARK_VERSION,
        "run_id": "public-process-ocel-om-2023-08-01-v1",
        "status": "PASS",
        "evidence_class": "PUBLIC_SOURCE_DERIVED_SYNTHETIC",
        "claim_ceiling": "MECHANISM_VALIDATION_ONLY",
        "claim_boundary": "NOT_OBSERVED_ENTERPRISE_SHADOW_NOT_REALIZED_ROI_NOT_PRODUCTION_SLA",
        "input_closure": {
            "benchmark_root": relative(benchmark_root),
            "projection_path": relative(benchmark_root / "projection" / "object-centric-projection.json"),
            "projection_digest": projection["digest"],
            "overlay_path": relative(benchmark_root / "overlay" / "policy-change-ground-truth.json"),
            "overlay_digest": overlay["digest"],
            "mapping_path": relative(mapping_path),
            "mapping_digest": mapping["digest"],
            "dataset_manifest_digest": dataset_manifest["digest"],
            "license_manifest_digest": licenses["digest"],
            "manifest_file_sha256": manifest_entries,
        },
        "scenario": {
            "scenario_id": overlay["scenario_id"],
            "target_count": len(universe),
            "ground_truth_affected_count": len(truth),
            "ground_truth_origin": overlay["ground_truth"]["origin"],
            "dependency_kind_counts": {
                kind: sum(1 for row in overlay["dependency_claims"] if row["dependency_kind"] == kind)
                for kind in ("ACTUAL_READ", "EXPLICIT_NON_DEPENDENCY", "UNKNOWN")
            },
        },
        "strategies": strategies,
        "enterprise_value_metrics": [
            {
                "metric_id": metric_id,
                "status": "NOT_RUN",
                "value": None,
                "evidence_class": "NOT_RUN",
                "reason": "PUBLIC_SIMULATION_DOES_NOT_OBSERVE_ENTERPRISE_VALUE",
            }
            for metric_id in ENTERPRISE_METRICS
        ],
        "limitations": [
            "The OCEL log is an artificial public simulation, not an enterprise quote extract.",
            "Causal Ground Truth and policy interpretation are experimenter-injected over observed relations.",
            "Scope reduction counts conservative action scope; it is not labor-time or money saved.",
            "Two targets are intentionally UNKNOWN and remain inside conservative action scope.",
        ],
    }
    return {**payload, "digest": _object_digest(payload)}


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=root / "benchmark" / "quote-value-v0.3-public-process",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=root / "configs" / "workspace" / "public-process-mappings" / "order-management-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "evidence" / "public-process" / "latest" / "public-process-bridge-receipt.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = build_receipt(
            project_root=args.project_root.resolve(),
            benchmark_root=args.benchmark_root.resolve(),
            mapping_path=args.mapping.resolve(),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, PublicProcessBridgeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
