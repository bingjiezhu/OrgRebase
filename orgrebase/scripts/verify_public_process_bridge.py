"""Independent deterministic verifier for the public-process bridge receipt.

The verifier deliberately does not import the evaluator or OrgRebase product
modules. It re-derives graph reachability, typed classifications, all required
metrics, source hashes and claim-boundary gates from the retained inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any

EXPECTED_STRATEGIES = ("BROADCAST_ALL", "NAIVE_GRAPH", "OAC_TYPED_WITH_UNKNOWN")
EXPECTED_ENTERPRISE_METRICS = {
    "quote_cycle_elapsed_minutes",
    "policy_confirmation_active_minutes",
    "adjudicated_impact_omission_rate",
    "first_pass_rework_rate",
    "unauthorized_access_success_rate",
    "manual_escalation_case_rate",
    "realized_selective_rebase_cost_saving_rate",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_object(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sealed(value: dict[str, Any], label: str, failures: list[str]) -> None:
    claimed = value.get("digest")
    payload = dict(value)
    payload.pop("digest", None)
    if claimed != _digest_object(payload):
        failures.append(f"{label}_DIGEST_MISMATCH")


def _rate(numerator: int, denominator: int, formula: str) -> dict[str, Any]:
    if denominator <= 0:
        raise ValueError(f"ZERO_DENOMINATOR:{formula}")
    return {
        "value": round(numerator / denominator, 6),
        "numerator": numerator,
        "denominator": denominator,
        "formula": formula,
    }


def _expected_score(
    strategy_id: str,
    predicted: set[str],
    unknown: set[str],
    universe: set[str],
    truth: set[str],
) -> dict[str, Any]:
    unaffected = universe - predicted - unknown
    actual_unaffected = universe - truth
    tp = len(predicted & truth)
    fp = len(predicted & actual_unaffected)
    fn = len(unaffected & truth)
    tn = len(unaffected & actual_unaffected)
    action_scope = predicted | unknown
    return {
        "strategy_id": strategy_id,
        "classifications": {
            "affected": sorted(predicted),
            "unknown": sorted(unknown),
            "unaffected": sorted(unaffected),
        },
        "counts": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "conservative_action_scope": len(action_scope),
        },
        "metrics": {
            "precision": _rate(tp, tp + fp, "TP/(TP+FP)"),
            "recall": _rate(tp, len(truth), "TP/ACTUAL_AFFECTED"),
            "false_invalidation_rate": _rate(fp, len(actual_unaffected), "FP/ACTUAL_UNAFFECTED"),
            "scope_reduction_rate": _rate(
                len(universe) - len(action_scope),
                len(universe),
                "(TARGET_UNIVERSE-CONSERVATIVE_ACTION_SCOPE)/TARGET_UNIVERSE",
            ),
            "unsafe_false_unaffected_rate": _rate(
                fn,
                len(truth),
                "ACTUAL_AFFECTED_CLASSIFIED_UNAFFECTED/ACTUAL_AFFECTED",
            ),
        },
    }


def verify(receipt: dict[str, Any], project_root: Path) -> list[str]:
    failures: list[str] = []
    _sealed(receipt, "RECEIPT", failures)
    if receipt.get("schema_version") != "orgrebase.workspace-public-process-bridge-receipt.v1":
        failures.append("RECEIPT_SCHEMA_UNSUPPORTED")
    if receipt.get("evidence_class") != "PUBLIC_SOURCE_DERIVED_SYNTHETIC":
        failures.append("RECEIPT_EVIDENCE_CLASS_INVALID")
    if "ENTERPRISE_SHADOW" in receipt.get("evidence_class", ""):
        failures.append("SHADOW_PROMOTION_FORBIDDEN")
    if receipt.get("claim_ceiling") != "MECHANISM_VALIDATION_ONLY":
        failures.append("CLAIM_CEILING_INVALID")

    closure = receipt.get("input_closure", {})
    expected_paths = {
        "projection_path": "benchmark/quote-value-v0.3-public-process/projection/object-centric-projection.json",
        "overlay_path": "benchmark/quote-value-v0.3-public-process/overlay/policy-change-ground-truth.json",
        "mapping_path": "configs/workspace/public-process-mappings/order-management-v1.json",
    }
    loaded: dict[str, dict[str, Any]] = {}
    for key, expected in expected_paths.items():
        relative = closure.get(key)
        if relative != expected:
            failures.append(f"INPUT_PATH_DRIFT:{key}")
            continue
        target = (project_root / relative).resolve()
        try:
            target.relative_to(project_root.resolve())
        except ValueError:
            failures.append(f"INPUT_PATH_ESCAPE:{key}")
            continue
        try:
            loaded[key] = _load(target)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            failures.append(f"INPUT_LOAD_FAILED:{key}:{exc}")

    if set(loaded) != set(expected_paths):
        return failures
    projection = loaded["projection_path"]
    overlay = loaded["overlay_path"]
    mapping = loaded["mapping_path"]
    benchmark_root = project_root / "benchmark" / "quote-value-v0.3-public-process"
    try:
        dataset_manifest = _load(benchmark_root / "dataset-manifest.json")
        license_manifest = _load(benchmark_root / "LICENSES.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        failures.append(f"PUBLIC_MANIFEST_LOAD_FAILED:{exc}")
        return failures
    for name, value in (("PROJECTION", projection), ("OVERLAY", overlay), ("MAPPING", mapping)):
        _sealed(value, name, failures)
    _sealed(dataset_manifest, "DATASET_MANIFEST", failures)
    _sealed(license_manifest, "LICENSE_MANIFEST", failures)
    if closure.get("projection_digest") != projection.get("digest"):
        failures.append("PROJECTION_REF_MISMATCH")
    if closure.get("overlay_digest") != overlay.get("digest"):
        failures.append("OVERLAY_REF_MISMATCH")
    if closure.get("mapping_digest") != mapping.get("digest"):
        failures.append("MAPPING_REF_MISMATCH")
    if closure.get("dataset_manifest_digest") != dataset_manifest.get("digest"):
        failures.append("DATASET_MANIFEST_REF_MISMATCH")
    if closure.get("license_manifest_digest") != license_manifest.get("digest"):
        failures.append("LICENSE_MANIFEST_REF_MISMATCH")
    if dataset_manifest.get("artifact", {}).get("raw_bytes_in_repository") is not False:
        failures.append("RAW_SOURCE_REDISTRIBUTION_BOUNDARY_INVALID")
    if dataset_manifest.get("artifact", {}).get("sha256") != (
        "sha256:a71ee17ea394fad6fa6ac3c5e661c309cd8ca362cde838bf38fee62496bf6f40"
    ):
        failures.append("UPSTREAM_SOURCE_SHA256_DRIFT")
    if license_manifest.get("upstream", {}).get("spdx") != "CC-BY-4.0":
        failures.append("UPSTREAM_LICENSE_DRIFT")
    if overlay.get("projection_digest") != projection.get("digest"):
        failures.append("OVERLAY_PROJECTION_MISMATCH")
    if projection.get("evidence_class") != "PUBLIC_PROCESS_OBSERVED_DERIVED":
        failures.append("PROJECTION_EVIDENCE_CLASS_INVALID")
    if overlay.get("evidence_class") != "PUBLIC_SOURCE_DERIVED_SYNTHETIC":
        failures.append("OVERLAY_EVIDENCE_CLASS_INVALID")
    if overlay.get("ground_truth", {}).get("origin") != "EXPERIMENTER_INJECTED":
        failures.append("GROUND_TRUTH_ORIGIN_INVALID")
    if overlay.get("policy_change", {}).get("policy_interpretation_origin") != "EXPERIMENTER_INJECTED":
        failures.append("POLICY_INTERPRETATION_ORIGIN_INVALID")
    if mapping.get("missing_semantics") != "UNKNOWN":
        failures.append("MISSING_SEMANTICS_INVALID")

    manifest_entries = closure.get("manifest_file_sha256", {})
    for relative, claimed in manifest_entries.items():
        target = (benchmark_root / relative).resolve()
        try:
            target.relative_to(benchmark_root.resolve())
            observed = _digest_file(target)
        except (OSError, ValueError):
            failures.append(f"MANIFEST_INPUT_INVALID:{relative}")
            continue
        if observed != claimed:
            failures.append(f"MANIFEST_INPUT_DIGEST_MISMATCH:{relative}")
    if set(manifest_entries) != {
        "LICENSES.json",
        "dataset-manifest.json",
        "overlay/policy-change-ground-truth.json",
        "projection/object-centric-projection.json",
    }:
        failures.append("MANIFEST_INPUT_CLOSURE_MISMATCH")

    required_tables = {
        "objects",
        "events",
        "event_object_relations",
        "object_object_relations",
        "object_attribute_history",
    }
    if not required_tables.issubset(projection):
        failures.append("OBJECT_CENTRIC_TABLE_MISSING")
        return failures
    if "cases" in projection or "case_id" in projection:
        failures.append("FLATTENED_CASE_LOG_FORBIDDEN")

    universe = set(overlay.get("target_universe", []))
    truth = set(overlay.get("ground_truth", {}).get("expected_affected", []))
    expected_unaffected = set(overlay.get("ground_truth", {}).get("expected_unaffected", []))
    if len(universe) != 11 or len(truth) != 5 or truth | expected_unaffected != universe:
        failures.append("GROUND_TRUTH_PARTITION_INVALID")
        return failures

    graph: dict[str, set[str]] = {}
    for row in projection["object_object_relations"]:
        source = row.get("source_ref")
        target = row.get("target_ref")
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set()).add(source)
    changed = overlay.get("policy_change", {}).get("changed_object_ref")
    seen = {changed}
    queue = deque([changed])
    while queue:
        current = queue.popleft()
        for candidate in graph.get(current, set()):
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    naive = seen & universe
    claims = overlay.get("dependency_claims", [])
    typed = {
        row.get("target_ref")
        for row in claims
        if row.get("dependency_kind") in {"ACTUAL_READ", "DECLARED", "INFERRED"}
    }
    unknown = {row.get("target_ref") for row in claims if row.get("dependency_kind") == "UNKNOWN"}
    for row in claims:
        if "classification" in row:
            failures.append(f"DEPENDENCY_OUTPUT_LABEL_LEAKAGE:{row.get('target_ref')}")
        pair = (row.get("dependency_kind"), row.get("origin"))
        if pair not in {
            ("ACTUAL_READ", "EXPERIMENTER_INJECTED"),
            ("EXPLICIT_NON_DEPENDENCY", "EXPERIMENTER_INJECTED"),
            ("UNKNOWN", "MISSING"),
        }:
            failures.append(f"DEPENDENCY_SEMANTICS_INVALID:{row.get('target_ref')}")

    expected_strategies = [
        _expected_score("BROADCAST_ALL", universe, set(), universe, truth),
        _expected_score("NAIVE_GRAPH", naive, set(), universe, truth),
        _expected_score("OAC_TYPED_WITH_UNKNOWN", typed, unknown, universe, truth),
    ]
    if receipt.get("strategies") != expected_strategies:
        failures.append("STRATEGY_RECOMPUTATION_MISMATCH")
    if tuple(row.get("strategy_id") for row in receipt.get("strategies", [])) != EXPECTED_STRATEGIES:
        failures.append("STRATEGY_SET_DRIFT")

    value_metrics = receipt.get("enterprise_value_metrics", [])
    if {row.get("metric_id") for row in value_metrics} != EXPECTED_ENTERPRISE_METRICS:
        failures.append("ENTERPRISE_METRIC_SET_DRIFT")
    if any(
        row.get("status") != "NOT_RUN"
        or row.get("value") is not None
        or row.get("evidence_class") != "NOT_RUN"
        for row in value_metrics
    ):
        failures.append("PUBLIC_DATA_ENTERPRISE_VALUE_PROMOTION")
    return failures


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=root / "evidence" / "public-process" / "latest" / "public-process-bridge-receipt.json",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = _load(args.receipt)
        failures = verify(receipt, args.project_root.resolve())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        receipt = {}
        failures = [f"RECEIPT_LOAD_FAILED:{exc}"]
    result = {
        "schema_version": "orgrebase.workspace-public-process-bridge-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "INDEPENDENT_OFFLINE_DETERMINISTIC_REPLAY",
        "implementation_independence": "NO_EVALUATOR_OR_PRODUCT_IMPORTS",
        "receipt_digest": receipt.get("digest"),
        "failures": failures,
    }
    result["digest"] = _digest_object(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
