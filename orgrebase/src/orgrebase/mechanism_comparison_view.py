"""Read-only projection of the frozen OWB synthetic reference-implementation suite."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest

SUITE_PATH = "evidence/workspace/latest/evaluation-suite.json"
REGISTRY_PATH = "configs/workspace/metric-registry.json"
# OWB v1.1 registry retained in the source-bound product-path observation manifest.
REGISTRY_SHA256 = "sha256:94246a230fe19e6991523fbb4f73c45cf3afc2a480a1adca7d8aaae4b83d5a8a"
BASELINES = (
    "safe-single-agent-admitted-context",
    "unified-raw-context-stress",
    "natural-language-multi-agent",
    "broadcast-invalidate-all",
)
ABLATIONS = (
    "no-reference-monitor",
    "no-context-projection",
    "no-manifest-bijection",
    "no-certificate",
    "no-successor-promotion",
    "no-unknown",
    "no-skill-requalification",
    "non-exact-skill-digest-negative-control",
)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("INCONSISTENT_REFERENCE_SUITE")


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _digest_valid(value: dict[str, Any]) -> bool:
    return value.get("digest") == sha256_digest({k: v for k, v in value.items() if k != "digest"})


def _read(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    _require(isinstance(value, dict))
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _report_row(report: dict[str, Any], profile: str, kind: str, registry: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(report, dict) and _digest_valid(report))
    _require(
        report.get("system_profile") == profile
        and report.get("id") == f"evaluation:owb-v1.1:{profile}"
        and report.get("benchmark_version") == "OWB v1.1"
        and report.get("mode") == "deterministic"
    )
    refs = report.get("case_receipt_refs")
    expected_cases = {
        *(f"F-{i:02}-{j:02}" for i in range(12) for j in range(4)),
        *(f"C-{i:02}-{j:02}" for i in range(12) for j in range(6)),
        *(f"R-{i:02}-{j}" for i in range(12) for j in (1, 2)),
        *(f"A-{i:02}-{j:02}" for i in range(4) for j in range(8)),
        *(f"S-{i:02}" for i in range(16)),
    }
    _require(
        isinstance(refs, list)
        and len(refs) == 192
        and all(isinstance(ref, str) for ref in refs)
        and len(set(refs)) == 192
    )
    _require(set(refs) == {f"case-run:{case}:{profile}" for case in expected_cases})
    metrics = report.get("metrics")
    _require(isinstance(metrics, list) and all(isinstance(m, dict) for m in metrics))
    by_metric = {m["metric_id"]: m for m in metrics}
    required = {m for d in registry["owb_core_score"]["dimensions"] for m in d["components"]}
    _require(len(by_metric) == len(metrics) and set(by_metric) == required)
    for metric in metrics:
        value, numerator, denominator = (metric.get(k) for k in ("value", "numerator", "denominator"))
        _require(all(_number(v) for v in (value, numerator, denominator)))
        _require(0 <= value <= 1 and denominator > 0 and 0 <= numerator <= denominator)
        _require(value == round(numerator / denominator, 6))
        _require(metric.get("status") == ("PASS" if value == 1 else "FAIL"))
    gates = report.get("hard_gate_results")
    _require(isinstance(gates, list) and all(isinstance(g, dict) for g in gates))
    by_gate = {g["metric_id"]: g for g in gates}
    _require(len(by_gate) == len(gates) and set(by_gate) == {g["id"] for g in registry["hard_gates"]})
    failed = []
    for definition in registry["hard_gates"]:
        gate = by_gate[definition["id"]]
        value = gate.get("value")
        _require(_number(value) and value >= 0)
        expected_status = "PASS" if value == definition["value"] else "FAIL"
        _require(gate.get("status") == expected_status)
        metric_id = {
            "certificate_tamper_rejection": "tamper_rejection_rate",
            "transaction_atomicity": "atomicity_restart_score",
        }.get(definition["id"], definition["id"])
        if metric_id in by_metric:
            _require(round(value, 6) == by_metric[metric_id]["value"])
        if expected_status == "FAIL":
            failed.append(definition["id"])
    core = report.get("core_score")
    _require(isinstance(core, dict) and _digest_valid(core))
    _require(core.get("benchmark_version") == "OWB v1.1" and core.get("system_profile") == profile)
    dimensions = {}
    weighted = 0.0
    for dimension in registry["owb_core_score"]["dimensions"]:
        score = (
            100
            * sum(
                points * by_metric[name]["numerator"] / by_metric[name]["denominator"]
                for name, points in dimension["components"].items()
            )
            / sum(dimension["components"].values())
        )
        dimensions[dimension["id"]] = round(score, 4)
        weighted += dimension["weight"] * score / 100
    weighted = round(weighted, 4)
    threshold = registry["owb_core_score"]["pass_threshold"]
    _require(core.get("component_scores") == dimensions and _number(core.get("weighted_score")))
    _require(core["weighted_score"] == weighted and core.get("pass_threshold") == threshold)
    status = "PASS" if weighted >= threshold and not failed else "FAIL"
    _require(report.get("status") == core.get("status") == status)
    return {
        "profile": profile,
        "kind": kind,
        "score": core["weighted_score"],
        "threshold": threshold,
        "hard_gate_status": "FAIL" if failed else "PASS",
        "failed_gates": failed,
        "status": status,
        "case_count": len(refs),
        "report_digest": report["digest"],
    }


def mechanism_comparison_view(project_root: Path, quote_value: dict[str, Any]) -> dict[str, Any]:
    """Caller must first validate the closure index and its quote-value binding.

    This optional subview never changes the parent archive or a current business run.
    No benchmark is executed and the suite's fixed timestamp is not a wall-clock fact.
    """
    unavailable = {"status": "UNAVAILABLE", "rows": [], "reason": "REFERENCE_SUITE_UNAVAILABLE_OR_INVALID"}
    try:
        sources = [s for s in quote_value["source_evidence"] if s.get("id") == "evaluation_suite"]
        _require(
            len(sources) == 1
            and sources[0].get("path") == SUITE_PATH
            and sources[0].get("evidence_class") == "SYNTHETIC_GOLD"
        )
        suite, digest = _read(project_root / SUITE_PATH)
        _require(digest == sources[0].get("file_sha256"))
        registry, registry_digest = _read(project_root / REGISTRY_PATH)
        _require(registry_digest == REGISTRY_SHA256)
        _require(set(suite) == {"reference", "baselines", "ablations"})
        _require(set(suite["baselines"]) == set(BASELINES) and set(suite["ablations"]) == set(ABLATIONS))
        rows = [_report_row(suite["reference"], "orgrebase-workspace", "reference", registry)]
        for group, profiles in (("baselines", BASELINES), ("ablations", ABLATIONS)):
            rows.extend(_report_row(suite[group][profile], profile, group, registry) for profile in profiles)
        return {
            "status": "PASS",
            "rows": rows,
            "source_digest": digest,
            "source_path": SUITE_PATH,
            "quote_value_digest": quote_value["digest"],
            "registry_digest": registry_digest,
            "benchmark_version": "OWB v1.1",
            "case_count_per_profile": 192,
            "implementation": "ReferenceWorkspaceBenchmarkSUT",
            "evidence_class": "SYNTHETIC_GOLD",
            "current_task_run": False,
            "packaged_product_comparison": False,
            "live_model_comparison": False,
            "time_basis": "FIXED_BENCHMARK_TIMESTAMP_NOT_WALL_CLOCK",
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return unavailable
