"""Independent evaluator for the Enterprise Quote Operator value closure.

This module intentionally imports no ``orgrebase`` package.  It projects
retained evidence into four non-overlapping evidence classes and fails closed
on target-universe drift, zero denominators, cost-model gaps, or claim inflation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import rfc8785

BENCHMARK_VERSION = "QuoteValue-v0.1"
RECEIPT_SCHEMA_VERSION = "orgrebase.workspace-quote-value-receipt.v1"
EVIDENCE_CLASSES = {
    "SYNTHETIC_GOLD",
    "OBSERVED_LOCAL_PRODUCT",
    "MODELLED_COUNTERFACTUAL",
    "NOT_RUN",
}
DISPOSITIONS = (
    "PRESERVE_WITHIN_BOUNDARY",
    "HOLD_FOR_REVIEW",
    "REQUALIFY",
    "REBUILD",
)
COST_COMPONENTS = {"PRESERVE", "REVIEW", "REQUALIFY", "HOLD", "REBUILD"}
COST_MODEL_VERSION = "quote-action-cost@0.1.0"
COST_MODEL_UNIT = "NORMALIZED_ACTION_COST"
COST_MODEL_TARGET_SCOPE = "PER_EVALUATED_TARGET_DECISION"
EXPECTED_DISPOSITION_COMPONENTS = {
    "PRESERVE_WITHIN_BOUNDARY": {"PRESERVE"},
    "HOLD_FOR_REVIEW": {"REVIEW", "HOLD"},
    "REQUALIFY": {"REVIEW", "REQUALIFY"},
    "REBUILD": {"REBUILD"},
}
EXPECTED_METRICS = (
    "quote_cycle_elapsed_minutes",
    "policy_confirmation_active_minutes",
    "impact_exact_mismatch_case_rate",
    "first_pass_rework_rate",
    "unauthorized_access_success_rate",
    "hold_for_review_decision_rate",
    "full_rebuild_normalized_cost",
    "selective_rebase_normalized_cost",
    "selective_rebase_cost_saving_rate",
)
DECLARED_REVIEW_BURDEN_SCENARIOS = (
    "LOW_REVIEW_BURDEN",
    "HIGH_REVIEW_BURDEN",
)
STRESS_SCENARIOS = (
    *DECLARED_REVIEW_BURDEN_SCENARIOS,
    "BREAK_EVEN_REVIEW_BURDEN",
    "ADVERSE_REVIEW_BURDEN",
)
EXPECTED_SOURCE_IDS = (
    "benchmark_manifest",
    "metric_registry",
    "current_process_baseline",
    "value_cases",
    "action_cost_model",
    "evaluation_suite",
    "product_path_observations",
    "currency_vmrc",
    "launch_vmrc",
)

EXPECTED_PROCESS_STEPS = (
    "quote-step:intake",
    "quote-step:product-confirmation",
    "quote-step:legal-confirmation",
    "quote-step:finance-confirmation",
    "quote-step:composition",
    "quote-step:delivery-acceptance",
    "quote-step:change-impact",
    "quote-step:selective-update",
)

EXPECTED_PROCESS_SEMANTICS = {
    "quote-step:intake": {
        "responsible": ("Quote Operator",),
        "to_be_responsible": ("Quote Operator",),
        "accountable": "GTM Owner",
        "as_is": "CRM_OR_CPQ_REQUEST_QUEUE",
        "to_be": "CRM_OR_CPQ_REQUEST_ADAPTER",
    },
    "quote-step:product-confirmation": {
        "responsible": ("Product Owner",),
        "to_be_responsible": ("Product Agent",),
        "accountable": "Product Owner",
        "as_is": "PRODUCT_CATALOG_OR_RELEASE_SOURCE",
        "to_be": "PRODUCT_DOMAIN_READ_PROJECTION",
    },
    "quote-step:legal-confirmation": {
        "responsible": ("Legal Owner",),
        "to_be_responsible": ("Legal Agent",),
        "accountable": "Legal Owner",
        "as_is": "CLM_OR_RESTRICTED_LEGAL_SOURCE",
        "to_be": "PURPOSE_BOUND_LEGAL_READ_PROJECTION",
    },
    "quote-step:finance-confirmation": {
        "responsible": ("Finance Owner",),
        "to_be_responsible": ("Finance Agent",),
        "accountable": "Finance Owner",
        "as_is": "ERP_OR_PRICING_POLICY_SOURCE",
        "to_be": "FINANCE_DOMAIN_READ_PROJECTION",
    },
    "quote-step:composition": {
        "responsible": ("Quote Operator",),
        "to_be_responsible": ("GTM Agent",),
        "accountable": "Quote Operator",
        "as_is": "DOCUMENT_OR_CPQ_COMPOSER",
        "to_be": "ORGREBASE_WORKSPACE",
    },
    "quote-step:delivery-acceptance": {
        "responsible": ("Quote Operator",),
        "to_be_responsible": ("Quote Operator",),
        "accountable": "GTM Owner",
        "as_is": "CPQ_OR_DOCUMENT_OUTPUT",
        "to_be": "GOVERNED_DELIVERABLE_EXPORT",
    },
    "quote-step:change-impact": {
        "responsible": ("Changed-source Owner",),
        "to_be_responsible": ("Deterministic Control",),
        "accountable": "Changed-source Owner",
        "as_is": "MANUAL_CROSS_SYSTEM_CHANGE_REVIEW",
        "to_be": "ORGREBASE_DETERMINISTIC_CONTROL_PLANE",
    },
    "quote-step:selective-update": {
        "responsible": ("Exact Approval Owner",),
        "to_be_responsible": ("Bounded Runtime", "Canonical Writer"),
        "accountable": "Exact Approval Owner",
        "as_is": "MANUAL_DOCUMENT_AND_SYSTEM_REWORK",
        "to_be": "ORGREBASE_BOUNDED_RUNTIME_AND_CANONICAL_WRITER",
    },
}


class QuoteValueError(ValueError):
    """A deterministic, user-visible quote-value contract failure."""


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuoteValueError(f"INVALID_JSON:{path}") from exc
    if not isinstance(value, dict):
        raise QuoteValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


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


def _rfc8785_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _verify_claimed_content_digest(
    value: dict[str, Any],
    *,
    code: str,
    canonicalizer: str = "ORGREBASE_CANONICAL_JSON",
) -> str:
    claimed = value.get("digest")
    _require(
        isinstance(claimed, str)
        and claimed.startswith("sha256:")
        and len(claimed) == 71
        and claimed != "sha256:" + ("0" * 64),
        f"{code}_INVALID",
    )
    payload = dict(value)
    payload.pop("digest", None)
    expected = (
        _rfc8785_digest(payload)
        if canonicalizer == "RFC8785"
        else _object_digest(payload)
    )
    _require(claimed == expected, f"{code}_MISMATCH")
    return claimed


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise QuoteValueError(f"SOURCE_NOT_READABLE:{path}") from exc


def _round(value: float) -> float:
    return round(float(value), 6)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise QuoteValueError(code)


def _display_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_source_path(path_text: str, project_root: Path) -> Path:
    path = Path(path_text)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _validate_manifest(benchmark_root: Path) -> dict[str, Any]:
    manifest_path = benchmark_root / "MANIFEST.sha256"
    expected_paths = [
        "public/current-process-baseline.json",
        "public/value-cases.json",
        "evaluator/action-cost-model.json",
    ]
    failures: list[str] = []
    entries: list[dict[str, str]] = []
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise QuoteValueError("BENCHMARK_MANIFEST_MISSING") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(f"INVALID_MANIFEST_LINE:{line}")
            continue
        target = (benchmark_root / relative).resolve()
        try:
            target.relative_to(benchmark_root.resolve())
        except ValueError:
            failures.append(f"MANIFEST_PATH_ESCAPE:{relative}")
            continue
        observed = _file_digest(target).removeprefix("sha256:") if target.is_file() else ""
        if observed != expected:
            failures.append(f"MANIFEST_DIGEST_MISMATCH:{relative}")
        entries.append(
            {
                "path": relative,
                "expected_sha256": expected,
                "observed_sha256": observed,
            }
        )
    if [entry["path"] for entry in entries] != expected_paths:
        failures.append("MANIFEST_CORPUS_SET_INVALID")
    _require(not failures, ";".join(failures))
    return {
        "status": "PASS",
        "entry_count": len(entries),
        "manifest_sha256": _file_digest(manifest_path),
    }


def _validate_baseline(baseline: dict[str, Any]) -> dict[str, Any]:
    _require(
        baseline.get("schema_version")
        == "orgrebase.workspace-current-process-baseline.v1",
        "BASELINE_SCHEMA_VERSION_INVALID",
    )
    _require(baseline.get("primary_user") == "Enterprise Quote Operator", "PRIMARY_USER_DRIFT")
    _require(baseline.get("primary_deliverable") == "Enterprise Quote", "DELIVERABLE_DRIFT")
    steps = baseline.get("process_steps")
    _require(isinstance(steps, list) and len(steps) == 8, "PROCESS_STEP_SET_INVALID")
    step_ids: list[str] = []
    accountable: dict[str, str] = {}
    for step in steps:
        _require(isinstance(step, dict), "PROCESS_STEP_INVALID")
        step_id = step.get("id")
        owner = step.get("accountable")
        _require(isinstance(step_id, str) and step_id, "PROCESS_STEP_ID_INVALID")
        _require(isinstance(owner, str) and bool(owner.strip()), f"ACCOUNTABLE_INVALID:{step_id}")
        _require(not isinstance(owner, list), f"MULTIPLE_ACCOUNTABLE:{step_id}")
        for key in (
            "systems",
            "input",
            "output",
            "target_response",
            "responsible",
            "to_be_responsible",
            "consulted",
            "informed",
            "approval_condition",
            "failure_paths",
        ):
            _require(key in step, f"PROCESS_STEP_FIELD_MISSING:{step_id}:{key}")
        systems = step["systems"]
        semantics = EXPECTED_PROCESS_SEMANTICS.get(step_id)
        _require(semantics is not None, f"PROCESS_STEP_SEMANTICS_UNKNOWN:{step_id}")
        _require(
            tuple(step["responsible"]) == semantics["responsible"],
            f"RESPONSIBLE_ROLE_SEMANTIC_DRIFT:{step_id}",
        )
        _require(
            tuple(step["to_be_responsible"]) == semantics["to_be_responsible"],
            f"TO_BE_RESPONSIBLE_ROLE_SEMANTIC_DRIFT:{step_id}",
        )
        _require(
            owner == semantics["accountable"],
            f"ACCOUNTABLE_ROLE_SEMANTIC_DRIFT:{step_id}",
        )
        _require(
            isinstance(systems, dict)
            and systems.get("named_connector_status") == "NOT_RUN",
            f"NAMED_CONNECTOR_CLAIM_INVALID:{step_id}",
        )
        _require(
            systems.get("as_is_interface_class") == semantics["as_is"]
            and systems.get("to_be_interface_class") == semantics["to_be"],
            f"SYSTEM_CLASS_SEMANTIC_DRIFT:{step_id}",
        )
        target = step["target_response"]
        _require(
            isinstance(target, dict)
            and isinstance(target.get("value"), (int, float))
            and target["value"] > 0
            and target.get("basis") == "DESIGN_TARGET_NOT_OBSERVED_BASELINE",
            f"TARGET_RESPONSE_INVALID:{step_id}",
        )
        step_ids.append(step_id)
        accountable[step_id] = owner
    _require(len(step_ids) == len(set(step_ids)), "PROCESS_STEP_ID_DUPLICATE")
    _require(tuple(step_ids) == EXPECTED_PROCESS_STEPS, "PROCESS_STEP_SET_OR_ORDER_INVALID")
    _require(
        baseline.get("status") == "NOT_RUN" and baseline.get("evidence_class") == "NOT_RUN",
        "ENTERPRISE_BASELINE_CLAIM_INFLATION",
    )
    metrics = baseline.get("metrics")
    _require(isinstance(metrics, list) and len(metrics) == 3, "BASELINE_METRIC_SET_INVALID")
    for metric in metrics:
        _require(
            isinstance(metric, dict)
            and metric.get("status") == "NOT_RUN"
            and metric.get("value") is None
            and metric.get("numerator") is None
            and metric.get("denominator") is None,
            f"ENTERPRISE_BASELINE_METRIC_CLAIM_INFLATION:{metric.get('metric_id')}",
        )
    return {
        "step_count": len(steps),
        "accountable_assignments": accountable,
        "enterprise_observation_status": "NOT_RUN",
    }


def _validate_registry(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _require(
        registry.get("schema_version") == "orgrebase.quote-value-metric-registry.v1",
        "METRIC_REGISTRY_SCHEMA_VERSION_INVALID",
    )
    _require(registry.get("benchmark_version") == BENCHMARK_VERSION, "BENCHMARK_VERSION_DRIFT")
    _require(set(registry.get("evidence_classes", {})) == EVIDENCE_CLASSES, "EVIDENCE_CLASS_SET_INVALID")
    definitions = registry.get("metric_definitions")
    _require(isinstance(definitions, list), "METRIC_DEFINITIONS_INVALID")
    by_id: dict[str, dict[str, Any]] = {}
    for item in definitions:
        _require(isinstance(item, dict), "METRIC_DEFINITION_INVALID")
        metric_id = item.get("id")
        _require(isinstance(metric_id, str) and metric_id not in by_id, "METRIC_ID_DUPLICATE")
        _require(isinstance(item.get("formula"), str) and item["formula"], f"FORMULA_MISSING:{metric_id}")
        allowed = item.get("allowed_evidence_classes")
        _require(
            isinstance(allowed, list) and bool(allowed) and set(allowed) <= EVIDENCE_CLASSES,
            f"METRIC_EVIDENCE_CLASS_INVALID:{metric_id}",
        )
        by_id[metric_id] = item
    _require(tuple(by_id) == EXPECTED_METRICS, "METRIC_DEFINITION_SET_OR_ORDER_INVALID")
    forbidden = set(registry.get("claim_policy", {}).get("forbidden_claims", []))
    _require("ENTERPRISE_ROI" in forbidden, "ENTERPRISE_ROI_NOT_FORBIDDEN")
    return by_id


def _validate_cost_model(model: dict[str, Any]) -> None:
    _require(
        model.get("schema_version") == "orgrebase.workspace-quote-action-cost-model.v1",
        "COST_MODEL_SCHEMA_VERSION_INVALID",
    )
    _require(model.get("evidence_class") == "MODELLED_COUNTERFACTUAL", "COST_EVIDENCE_CLASS_INVALID")
    _require(model.get("model_version") == COST_MODEL_VERSION, "COST_MODEL_VERSION_INVALID")
    _require(model.get("unit") == COST_MODEL_UNIT, "COST_MODEL_UNIT_INVALID")
    _require(
        model.get("target_scope") == COST_MODEL_TARGET_SCOPE,
        "COST_MODEL_TARGET_SCOPE_INVALID",
    )
    components = model.get("components")
    _require(isinstance(components, dict) and set(components) == COST_COMPONENTS, "COST_COMPONENT_SET_INVALID")
    for name, value in components.items():
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0,
            f"COST_COMPONENT_MUST_BE_POSITIVE:{name}",
        )
    mapping = model.get("disposition_components")
    _require(isinstance(mapping, dict) and set(mapping) == set(DISPOSITIONS), "DISPOSITION_COST_MAP_INVALID")
    for disposition, required in EXPECTED_DISPOSITION_COMPONENTS.items():
        values = mapping.get(disposition)
        _require(
            isinstance(values, list) and set(values) == required and len(values) == len(required),
            f"DISPOSITION_COMPONENTS_INVALID:{disposition}",
        )
    scenarios = model.get("sensitivity_scenarios")
    _require(
        isinstance(scenarios, list) and len(scenarios) == len(STRESS_SCENARIOS),
        "SENSITIVITY_SCENARIO_SET_INVALID",
    )
    scenario_ids: list[str] = []
    for scenario in scenarios:
        _require(isinstance(scenario, dict), "SENSITIVITY_SCENARIO_INVALID")
        scenario_id = scenario.get("id")
        scenario_components = scenario.get("components")
        _require(isinstance(scenario_id, str) and scenario_id, "SENSITIVITY_SCENARIO_ID_INVALID")
        _require(
            isinstance(scenario_components, dict) and set(scenario_components) == COST_COMPONENTS,
            f"SENSITIVITY_COMPONENT_SET_INVALID:{scenario_id}",
        )
        for name, value in scenario_components.items():
            _require(
                isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0,
                f"SENSITIVITY_COST_MUST_BE_POSITIVE:{scenario_id}:{name}",
            )
        scenario_ids.append(scenario_id)
    _require(len(scenario_ids) == len(set(scenario_ids)), "SENSITIVITY_SCENARIO_ID_DUPLICATE")
    _require(tuple(scenario_ids) == STRESS_SCENARIOS, "SENSITIVITY_SCENARIO_SET_OR_ORDER_INVALID")


def _validate_product_observations(
    observations: dict[str, Any],
    value_cases: dict[str, Any],
) -> dict[str, Any]:
    _require(
        observations.get("schema_version") == "orgrebase.product-path-observations.v2",
        "PRODUCT_OBSERVATION_SCHEMA_INVALID",
    )
    _require(
        observations.get("benchmark_version")
        == "ProductPath-v0.3-task-intake-bound",
        "PRODUCT_OBSERVATION_BENCHMARK_VERSION_INVALID",
    )
    observation_digest = _verify_claimed_content_digest(
        observations,
        code="PRODUCT_OBSERVATION_DIGEST",
        canonicalizer="RFC8785",
    )
    execution = observations.get("execution")
    _require(isinstance(execution, dict), "PRODUCT_EXECUTION_RECORD_MISSING")
    _require(execution.get("runner_product_imports") == 0, "PRODUCT_RUNNER_IMPORT_BOUNDARY_FAILED")
    cases = observations.get("cases")
    _require(isinstance(cases, list), "PRODUCT_OBSERVATION_CASES_INVALID")
    by_id = {case.get("id"): case for case in cases if isinstance(case, dict)}
    required = value_cases.get("required_product_observation_cases")
    _require(isinstance(required, dict) and bool(required), "REQUIRED_PRODUCT_CASES_INVALID")
    for case_id, expected_facts in required.items():
        case = by_id.get(case_id)
        _require(isinstance(case, dict), f"PRODUCT_CASE_MISSING:{case_id}")
        _require(case.get("execution_failures") == [], f"PRODUCT_CASE_EXECUTION_FAILURE:{case_id}")
        facts = case.get("facts")
        _require(isinstance(facts, dict), f"PRODUCT_CASE_FACTS_INVALID:{case_id}")
        for key, expected in expected_facts.items():
            _require(facts.get(key) == expected, f"PRODUCT_CASE_FACT_MISMATCH:{case_id}:{key}")
    return {
        "mode": execution.get("mode"),
        "runner_product_imports": 0,
        "required_case_count": len(required),
        "observation_digest": observation_digest,
        "observation_digest_algorithm": "RFC8785_SHA256",
        "source_execution_replayed_by_quote_value": False,
    }


def _reference_report(evaluation_suite: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Select one explicit source contract without rewriting either envelope."""
    if set(evaluation_suite) == {"reference", "baselines", "ablations"}:
        _require(
            isinstance(evaluation_suite["baselines"], dict)
            and isinstance(evaluation_suite["ablations"], dict),
            "OWB_SUITE_STRUCTURE_INVALID",
        )
        reference = evaluation_suite["reference"]
        source_ref = "evaluation_suite:reference"
    elif set(evaluation_suite) == {
        "benchmark", "dataset_digest", "reports", "primary_status", "primary_score"
    }:
        reports = evaluation_suite["reports"]
        _require(isinstance(reports, dict), "OWB_SUITE_STRUCTURE_INVALID")
        reference = reports.get("orgrebase-workspace")
        source_ref = "evaluation_suite:reports:orgrebase-workspace"
    else:
        raise QuoteValueError("OWB_SUITE_STRUCTURE_INVALID")
    _require(
        isinstance(reference, dict) and reference.get("system_profile") == "orgrebase-workspace",
        "OWB_REFERENCE_PROFILE_INVALID",
    )
    _verify_claimed_content_digest(reference, code="OWB_REPORT_DIGEST")
    _require(reference.get("id") == "evaluation:owb-v1.1:orgrebase-workspace", "OWB_REPORT_ID_INVALID")
    _require(reference.get("benchmark_version") == "OWB v1.1", "OWB_BENCHMARK_VERSION_INVALID")
    _require(reference.get("mode") == "deterministic", "OWB_MODE_INVALID")
    _require(reference.get("status") == "PASS", "OWB_REPORT_STATUS_INVALID")
    if "reports" in evaluation_suite:
        benchmark = evaluation_suite["benchmark"]
        _require(
            isinstance(benchmark, dict) and benchmark.get("status") == "PASS"
            and benchmark.get("license_gate") == "PASS"
            and all(type(benchmark.get(key)) is int and benchmark[key] > 0
                    for key in ("case_count", "gold_count", "organization_count"))
            and benchmark["case_count"] == benchmark["gold_count"],
            "OWB_SUITE_BENCHMARK_INVALID",
        )
        case_refs = reference.get("case_receipt_refs")
        _require(isinstance(case_refs, list) and len(case_refs) == benchmark["case_count"],
                 "OWB_SUITE_CASE_COUNT_MISMATCH")
        dataset_digest = evaluation_suite["dataset_digest"]
        _require(isinstance(dataset_digest, str) and dataset_digest.startswith("sha256:")
                 and len(dataset_digest) == 71 and set(dataset_digest[7:]) <= set("0123456789abcdef")
                 and dataset_digest != "sha256:" + "0" * 64, "OWB_SUITE_DATASET_DIGEST_INVALID")
        score = evaluation_suite["primary_score"]
        core = reference.get("core_score")
        _require(isinstance(core, dict) and evaluation_suite["primary_status"] == reference["status"]
                 and type(score) in (int, float) and math.isfinite(score)
                 and score == core.get("weighted_score"), "OWB_SUITE_PRIMARY_SUMMARY_MISMATCH")
    return reference, source_ref


def _reference_metric(reference: dict[str, Any], metric_id: str) -> dict[str, Any]:
    metrics = reference.get("metrics")
    _require(isinstance(metrics, list), "OWB_REFERENCE_METRICS_INVALID")
    matches = [item for item in metrics if isinstance(item, dict) and item.get("metric_id") == metric_id]
    _require(len(matches) == 1, f"OWB_REFERENCE_METRIC_CARDINALITY:{metric_id}")
    metric = matches[0]
    numerator = metric.get("numerator")
    denominator = metric.get("denominator")
    _require(
        isinstance(numerator, (int, float))
        and not isinstance(numerator, bool)
        and math.isfinite(numerator)
        and isinstance(denominator, (int, float))
        and not isinstance(denominator, bool)
        and math.isfinite(denominator),
        f"OWB_METRIC_COUNTS_INVALID:{metric_id}",
    )
    _require(denominator > 0, f"ZERO_DENOMINATOR:{metric_id}")
    _require(0 <= numerator <= denominator, f"OWB_METRIC_RANGE_INVALID:{metric_id}")
    expected_value = _round(numerator / denominator)
    value = metric.get("value")
    _require(type(value) in (int, float) and math.isfinite(value), f"OWB_METRIC_VALUE_INVALID:{metric_id}")
    _require(_round(value) == expected_value, f"OWB_METRIC_VALUE_MISMATCH:{metric_id}")
    _require(metric.get("status") == "PASS", f"OWB_METRIC_STATUS_INVALID:{metric_id}")
    _require(expected_value == 1.0, f"OWB_METRIC_NOT_FULL_PASS:{metric_id}")
    return metric


def _validate_vmrc(
    vmrc: dict[str, Any],
    *,
    expected_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    _require(
        vmrc.get("schema_version") == "orgrebase.minimal-rebase-certificate.v1",
        f"VMRC_SCHEMA_INVALID:{expected_id}",
    )
    _require(vmrc.get("id") == expected_id, f"VMRC_ID_MISMATCH:{expected_id}")
    _verify_claimed_content_digest(vmrc, code=f"VMRC_DIGEST:{expected_id}")
    effects = vmrc.get("effects")
    _require(isinstance(effects, list) and bool(effects), f"VMRC_EFFECTS_INVALID:{expected_id}")
    target_ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    for effect in effects:
        _require(isinstance(effect, dict), f"VMRC_EFFECT_INVALID:{expected_id}")
        target_id = effect.get("target_id")
        disposition = effect.get("disposition")
        _require(isinstance(target_id, str) and target_id, f"VMRC_TARGET_INVALID:{expected_id}")
        _require(disposition in DISPOSITIONS, f"VMRC_DISPOSITION_INVALID:{expected_id}:{target_id}")
        _require(
            isinstance(effect.get("impact_certificate_digest"), str)
            and isinstance(effect.get("impact_result_digest"), str),
            f"VMRC_PROOF_BINDING_INVALID:{expected_id}:{target_id}",
        )
        target_ids.append(target_id)
        normalized.append({"target_id": target_id, "disposition": disposition})
    _require(len(target_ids) == len(set(target_ids)), f"VMRC_TARGET_DUPLICATE:{expected_id}")
    return sorted(target_ids), sorted(normalized, key=lambda item: item["target_id"])


def _action_counts(effects: list[dict[str, Any]]) -> dict[str, int]:
    observed = Counter(item["disposition"] for item in effects)
    return {disposition: int(observed.get(disposition, 0)) for disposition in DISPOSITIONS}


def _target_universe_digest(target_ids: list[str]) -> str:
    return _object_digest({"target_ids": sorted(target_ids)})


def _cost_model_digest(
    model: dict[str, Any],
    *,
    components: dict[str, float] | None = None,
    scenario_id: str | None = None,
) -> str:
    if components is None:
        return _object_digest(model)
    return _object_digest(
        {
            "model_version": model["model_version"],
            "scenario_id": scenario_id,
            "components": components,
            "disposition_components": model["disposition_components"],
            "unit": model["unit"],
            "target_scope": model["target_scope"],
        }
    )


def _disposition_cost(
    disposition: str,
    *,
    model: dict[str, Any],
    components: dict[str, float],
) -> float:
    return float(sum(components[name] for name in model["disposition_components"][disposition]))


def _cost_result(
    effects: list[dict[str, Any]],
    *,
    model: dict[str, Any],
    components: dict[str, float],
    target_universe_digest: str,
    cost_model_digest: str,
) -> dict[str, Any]:
    full = float(len(effects) * components["REBUILD"])
    selective = float(
        sum(
            _disposition_cost(effect["disposition"], model=model, components=components)
            for effect in effects
        )
    )
    _require(full > 0, "FULL_REBUILD_COST_ZERO")
    saving = full - selective
    return {
        "full_rebuild": _round(full),
        "selective_rebase": _round(selective),
        "saving": _round(saving),
        "saving_rate": _round(saving / full),
        "target_universe_digest": target_universe_digest,
        "cost_model_digest": cost_model_digest,
    }


def _source_entry(
    source_id: str,
    path: Path,
    evidence_class: str,
    *,
    project_root: Path,
) -> dict[str, str]:
    _require(evidence_class in EVIDENCE_CLASSES, f"SOURCE_EVIDENCE_CLASS_INVALID:{source_id}")
    return {
        "id": source_id,
        "path": _display_path(path, project_root),
        "file_sha256": _file_digest(path),
        "evidence_class": evidence_class,
    }


def _metric(
    metric_id: str,
    *,
    definitions: dict[str, dict[str, Any]],
    value: float | None,
    numerator: float | None,
    denominator: float | None,
    status: str,
    evidence_class: str,
    observation_window: str,
    source_refs: list[str],
    limitations: list[str],
) -> dict[str, Any]:
    definition = definitions[metric_id]
    _require(evidence_class in definition["allowed_evidence_classes"], f"EVIDENCE_CLASS_NOT_ALLOWED:{metric_id}")
    if status == "NOT_RUN":
        _require(
            evidence_class == "NOT_RUN"
            and value is None
            and numerator is None
            and denominator is None,
            f"NOT_RUN_METRIC_INVALID:{metric_id}",
        )
    else:
        _require(status == "CALCULATED" and evidence_class != "NOT_RUN", f"CALCULATED_METRIC_INVALID:{metric_id}")
        if denominator is not None:
            _require(denominator > 0, f"ZERO_DENOMINATOR:{metric_id}")
    return {
        "metric_id": metric_id,
        "formula": definition["formula"],
        "value": _round(value) if value is not None else None,
        "numerator": _round(numerator) if numerator is not None else None,
        "denominator": _round(denominator) if denominator is not None else None,
        "unit": definition["unit"],
        "status": status,
        "evidence_class": evidence_class,
        "observation_window": observation_window,
        "source_refs": source_refs,
        "limitations": limitations,
    }


def build_receipt(
    *,
    project_root: Path,
    benchmark_root: Path,
    metric_registry_path: Path,
    evaluation_suite_path: Path,
    product_observations_path: Path,
    currency_vmrc_path: Path,
    launch_vmrc_path: Path,
) -> dict[str, Any]:
    """Build a deterministic receipt from explicitly supplied evidence paths."""

    project_root = project_root.resolve()
    benchmark_root = benchmark_root.resolve()
    baseline_path = benchmark_root / "public" / "current-process-baseline.json"
    value_cases_path = benchmark_root / "public" / "value-cases.json"
    cost_model_path = benchmark_root / "evaluator" / "action-cost-model.json"
    manifest_path = benchmark_root / "MANIFEST.sha256"

    manifest_gate = _validate_manifest(benchmark_root)
    baseline = _json_object(baseline_path)
    baseline_gate = _validate_baseline(baseline)
    value_cases = _json_object(value_cases_path)
    _require(value_cases.get("benchmark_version") == BENCHMARK_VERSION, "VALUE_CASE_BENCHMARK_VERSION_INVALID")
    registry = _json_object(metric_registry_path)
    definitions = _validate_registry(registry)
    model = _json_object(cost_model_path)
    _validate_cost_model(model)
    evaluation_suite = _json_object(evaluation_suite_path)
    product_observations = _json_object(product_observations_path)
    product_gate = _validate_product_observations(product_observations, value_cases)
    reference, report_source_ref = _reference_report(evaluation_suite)
    hard_dependency = _reference_metric(reference, "hard_dependency_recall")
    unauthorized_block = _reference_metric(reference, "unauthorized_read_block_rate")

    vmrc_sources = {
        "currency_vmrc": _json_object(currency_vmrc_path),
        "launch_vmrc": _json_object(launch_vmrc_path),
    }
    vmrc_paths = {
        "currency_vmrc": currency_vmrc_path,
        "launch_vmrc": launch_vmrc_path,
    }
    case_specs = value_cases.get("rebase_cases")
    _require(isinstance(case_specs, list) and len(case_specs) == 2, "VALUE_CASE_REBASE_SET_INVALID")
    case_inputs: list[dict[str, Any]] = []
    shared_target_ids: list[str] | None = None
    for case_spec in case_specs:
        _require(isinstance(case_spec, dict), "VALUE_CASE_REBASE_CASE_INVALID")
        source_id = case_spec.get("source_id")
        _require(source_id in vmrc_sources, f"VALUE_CASE_SOURCE_INVALID:{source_id}")
        target_ids, effects = _validate_vmrc(
            vmrc_sources[source_id],
            expected_id=case_spec.get("expected_vmrc_id"),
        )
        if shared_target_ids is None:
            shared_target_ids = target_ids
        else:
            _require(target_ids == shared_target_ids, f"TARGET_UNIVERSE_DRIFT:{case_spec.get('case_id')}")
        case_inputs.append(
            {
                "case_id": case_spec["case_id"],
                "source_id": source_id,
                "vmrc": vmrc_sources[source_id],
                "vmrc_path": vmrc_paths[source_id],
                "effects": effects,
            }
        )
    _require(bool(shared_target_ids), "TARGET_UNIVERSE_EMPTY")
    target_ids = shared_target_ids or []
    universe_digest = _target_universe_digest(target_ids)
    base_model_digest = _cost_model_digest(model)
    base_components = model["components"]

    rebase_cases: list[dict[str, Any]] = []
    all_effects: list[dict[str, Any]] = []
    for item in case_inputs:
        cost = _cost_result(
            item["effects"],
            model=model,
            components=base_components,
            target_universe_digest=universe_digest,
            cost_model_digest=base_model_digest,
        )
        rebase_cases.append(
            {
                "case_id": item["case_id"],
                "vmrc_ref": item["vmrc"]["id"],
                "vmrc_artifact_digest": item["vmrc"]["digest"],
                "evidence_class": "OBSERVED_LOCAL_PRODUCT",
                "action_counts": _action_counts(item["effects"]),
                "cost": cost,
            }
        )
        all_effects.extend(item["effects"])

    aggregate_cost = _cost_result(
        all_effects,
        model=model,
        components=base_components,
        target_universe_digest=universe_digest,
        cost_model_digest=base_model_digest,
    )
    sensitivity_scenarios: list[dict[str, Any]] = []
    sensitivity_rates: list[float] = []
    for scenario in model["sensitivity_scenarios"]:
        scenario_digest = _cost_model_digest(
            model,
            components=scenario["components"],
            scenario_id=scenario["id"],
        )
        scenario_cost = _cost_result(
            all_effects,
            model=model,
            components=scenario["components"],
            target_universe_digest=universe_digest,
            cost_model_digest=scenario_digest,
        )
        sensitivity_scenarios.append(
            {
                "id": scenario["id"],
                "cost_model_digest": scenario_digest,
                "full_rebuild": scenario_cost["full_rebuild"],
                "selective_rebase": scenario_cost["selective_rebase"],
                "saving_rate": scenario_cost["saving_rate"],
            }
        )
        sensitivity_rates.append(scenario_cost["saving_rate"])
    _require(len(set(sensitivity_rates)) > 1, "SENSITIVITY_INTERVAL_DEGENERATE")

    scenario_by_id = {item["id"]: item for item in sensitivity_scenarios}
    declared_review_rates = [
        scenario_by_id[scenario_id]["saving_rate"]
        for scenario_id in DECLARED_REVIEW_BURDEN_SCENARIOS
    ]
    stress_rates = [scenario_by_id[scenario_id]["saving_rate"] for scenario_id in STRESS_SCENARIOS]
    _require(
        scenario_by_id["BREAK_EVEN_REVIEW_BURDEN"]["saving_rate"] == 0.0,
        "BREAK_EVEN_SCENARIO_NOT_BREAK_EVEN",
    )
    _require(
        scenario_by_id["ADVERSE_REVIEW_BURDEN"]["saving_rate"] < 0.0,
        "ADVERSE_SCENARIO_NOT_ADVERSE",
    )

    impact_mismatch_cases = float(
        hard_dependency["denominator"] - hard_dependency["numerator"]
    )
    unauthorized_success = float(unauthorized_block["denominator"] - unauthorized_block["numerator"])
    hold_count = float(sum(item["disposition"] == "HOLD_FOR_REVIEW" for item in all_effects))
    evaluated_count = float(len(all_effects))

    not_run_limit = ["No same-enterprise shadow observation window has been collected."]
    metrics_by_id = {
        "quote_cycle_elapsed_minutes": _metric(
            "quote_cycle_elapsed_minutes",
            definitions=definitions,
            value=None,
            numerator=None,
            denominator=None,
            status="NOT_RUN",
            evidence_class="NOT_RUN",
            observation_window="NO_ENTERPRISE_SHADOW_WINDOW",
            source_refs=["current_process_baseline"],
            limitations=not_run_limit,
        ),
        "policy_confirmation_active_minutes": _metric(
            "policy_confirmation_active_minutes",
            definitions=definitions,
            value=None,
            numerator=None,
            denominator=None,
            status="NOT_RUN",
            evidence_class="NOT_RUN",
            observation_window="NO_ENTERPRISE_SHADOW_WINDOW",
            source_refs=["current_process_baseline"],
            limitations=not_run_limit,
        ),
        "impact_exact_mismatch_case_rate": _metric(
            "impact_exact_mismatch_case_rate",
            definitions=definitions,
            value=impact_mismatch_cases / float(hard_dependency["denominator"]),
            numerator=impact_mismatch_cases,
            denominator=float(hard_dependency["denominator"]),
            status="CALCULATED",
            evidence_class="SYNTHETIC_GOLD",
            observation_window="RETAINED_OWB_V1_1_SYNTHETIC_CASES",
            source_refs=[f"{report_source_ref}:hard_dependency_recall"],
            limitations=[
                "The observation unit is one complete synthetic CHANGE case, not one affected target.",
                "This exact-impact mismatch proxy is not a field omission rate.",
            ],
        ),
        "first_pass_rework_rate": _metric(
            "first_pass_rework_rate",
            definitions=definitions,
            value=None,
            numerator=None,
            denominator=None,
            status="NOT_RUN",
            evidence_class="NOT_RUN",
            observation_window="NO_ENTERPRISE_SHADOW_WINDOW",
            source_refs=["current_process_baseline"],
            limitations=not_run_limit,
        ),
        "unauthorized_access_success_rate": _metric(
            "unauthorized_access_success_rate",
            definitions=definitions,
            value=unauthorized_success / float(unauthorized_block["denominator"]),
            numerator=unauthorized_success,
            denominator=float(unauthorized_block["denominator"]),
            status="CALCULATED",
            evidence_class="SYNTHETIC_GOLD",
            observation_window="RETAINED_OWB_V1_1_SYNTHETIC_CASES",
            source_refs=[f"{report_source_ref}:unauthorized_read_block_rate"],
            limitations=["Synthetic unauthorized-read attempts do not establish production IAM performance."],
        ),
        "hold_for_review_decision_rate": _metric(
            "hold_for_review_decision_rate",
            definitions=definitions,
            value=hold_count / evaluated_count,
            numerator=hold_count,
            denominator=evaluated_count,
            status="CALCULATED",
            evidence_class="OBSERVED_LOCAL_PRODUCT",
            observation_window="TWO_RETAINED_LOCAL_VMRC_CHANGE_CASES",
            source_refs=["currency_vmrc", "launch_vmrc"],
            limitations=[
                "The observation unit is one case-target decision, so the same target may occur in multiple change cases.",
                "HOLD_FOR_REVIEW is a local queue-pressure proxy, not a workforce escalation baseline.",
            ],
        ),
        "full_rebuild_normalized_cost": _metric(
            "full_rebuild_normalized_cost",
            definitions=definitions,
            value=aggregate_cost["full_rebuild"],
            numerator=None,
            denominator=None,
            status="CALCULATED",
            evidence_class="MODELLED_COUNTERFACTUAL",
            observation_window="TWO_RETAINED_LOCAL_VMRC_CHANGE_CASES",
            source_refs=["action_cost_model", "currency_vmrc", "launch_vmrc"],
            limitations=["Dimensionless model output; not currency, elapsed time or person-hours."],
        ),
        "selective_rebase_normalized_cost": _metric(
            "selective_rebase_normalized_cost",
            definitions=definitions,
            value=aggregate_cost["selective_rebase"],
            numerator=None,
            denominator=None,
            status="CALCULATED",
            evidence_class="MODELLED_COUNTERFACTUAL",
            observation_window="TWO_RETAINED_LOCAL_VMRC_CHANGE_CASES",
            source_refs=["action_cost_model", "currency_vmrc", "launch_vmrc"],
            limitations=["Observed dispositions are priced by an uncalibrated normalized cost model."],
        ),
        "selective_rebase_cost_saving_rate": _metric(
            "selective_rebase_cost_saving_rate",
            definitions=definitions,
            value=aggregate_cost["saving_rate"],
            numerator=aggregate_cost["saving"],
            denominator=aggregate_cost["full_rebuild"],
            status="CALCULATED",
            evidence_class="MODELLED_COUNTERFACTUAL",
            observation_window="TWO_RETAINED_LOCAL_VMRC_CHANGE_CASES",
            source_refs=["action_cost_model", "currency_vmrc", "launch_vmrc"],
            limitations=["Counterfactual normalized saving with sensitivity bounds; never enterprise ROI."],
        ),
    }

    source_evidence = [
        _source_entry("benchmark_manifest", manifest_path, "MODELLED_COUNTERFACTUAL", project_root=project_root),
        _source_entry("metric_registry", metric_registry_path, "MODELLED_COUNTERFACTUAL", project_root=project_root),
        _source_entry("current_process_baseline", baseline_path, "NOT_RUN", project_root=project_root),
        _source_entry("value_cases", value_cases_path, "MODELLED_COUNTERFACTUAL", project_root=project_root),
        _source_entry("action_cost_model", cost_model_path, "MODELLED_COUNTERFACTUAL", project_root=project_root),
        _source_entry("evaluation_suite", evaluation_suite_path, "SYNTHETIC_GOLD", project_root=project_root),
        _source_entry(
            "product_path_observations",
            product_observations_path,
            "OBSERVED_LOCAL_PRODUCT",
            project_root=project_root,
        ),
        _source_entry("currency_vmrc", currency_vmrc_path, "OBSERVED_LOCAL_PRODUCT", project_root=project_root),
        _source_entry("launch_vmrc", launch_vmrc_path, "OBSERVED_LOCAL_PRODUCT", project_root=project_root),
    ]
    _require(tuple(item["id"] for item in source_evidence) == EXPECTED_SOURCE_IDS, "SOURCE_SET_INVALID")

    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "id": "quote-value:northstar-acme@v0.1",
        "benchmark_version": BENCHMARK_VERSION,
        "assessed_at": value_cases["assessed_at"],
        "primary_user": "Enterprise Quote Operator",
        "primary_deliverable": "Enterprise Quote",
        "verification_status": "PASS",
        "evidence_ceiling": "SYNTHETIC_CONTROLLED_VALUE_PROOF",
        "claim_boundary": value_cases["claim_boundary"],
        "source_evidence": source_evidence,
        "input_gates": [
            {"id": "benchmark_manifest", "status": "PASS", "details": manifest_gate},
            {"id": "process_raci", "status": "PASS", "details": baseline_gate},
            {
                "id": "enterprise_process_observation",
                "status": "NOT_RUN",
                "details": {"baseline_status": "NOT_RUN", "named_enterprise": None},
            },
            {"id": "product_path_contract", "status": "PASS", "details": product_gate},
            {
                "id": "owb_reference_metrics",
                "status": "PASS",
                "details": {
                    "hard_dependency_recall": {
                        "numerator": hard_dependency["numerator"],
                        "denominator": hard_dependency["denominator"],
                        "observation_unit": "SYNTHETIC_CHANGE_CASE",
                    },
                    "unauthorized_read_block_rate": {
                        "numerator": unauthorized_block["numerator"],
                        "denominator": unauthorized_block["denominator"],
                        "observation_unit": "SYNTHETIC_SECURITY_CASE",
                    },
                    "report_digest": reference["digest"],
                    "report_digest_verified": True,
                    "case_receipts_replayed_by_quote_value": False,
                },
            },
            {
                "id": "vmrc_target_universe",
                "status": "PASS",
                "details": {"target_count": len(target_ids), "digest": universe_digest},
            },
        ],
        "target_universe": {
            "target_ids": target_ids,
            "target_count": len(target_ids),
            "digest": universe_digest,
        },
        "cost_model": {
            "model_version": model["model_version"],
            "unit": model["unit"],
            "digest": base_model_digest,
        },
        "rebase_cases": rebase_cases,
        "aggregate_cost": aggregate_cost,
        "sensitivity": {
            "scenarios": sensitivity_scenarios,
            "declared_review_burden_envelope": {
                "scenario_ids": list(DECLARED_REVIEW_BURDEN_SCENARIOS),
                "lower": _round(min(declared_review_rates)),
                "upper": _round(max(declared_review_rates)),
                "interpretation": "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL",
            },
            "stress_envelope": {
                "scenario_ids": list(STRESS_SCENARIOS),
                "lower": _round(min(stress_rates)),
                "upper": _round(max(stress_rates)),
                "interpretation": "DECLARED_SCENARIO_ENVELOPE_NOT_CONFIDENCE_INTERVAL",
            },
        },
        "metrics": [metrics_by_id[metric_id] for metric_id in EXPECTED_METRICS],
        "limitations": [
            "No real-enterprise quote-cycle, active-time, rework or ROI observation has been run.",
            "OWB outcomes are synthetic gold proxies; ProductPath and VMRC outcomes are controlled-local evidence.",
            "QuoteValue validates retained report and observation content addresses but does not rerun their source executions.",
            "Normalized action cost is a counterfactual model and requires enterprise calibration before ROI use.",
            "Sensitivity envelopes cover only declared scenarios and are not confidence intervals.",
        ],
        "digest": "",
    }
    digest_payload = dict(receipt)
    digest_payload.pop("digest")
    receipt["digest"] = _object_digest(digest_payload)
    return receipt


def verify_receipt(receipt: dict[str, Any], *, project_root: Path) -> list[str]:
    """Strictly verify a receipt, all source bytes, and exact recomputation."""

    failures: list[str] = []
    if not isinstance(receipt, dict):
        return ["RECEIPT_OBJECT_REQUIRED"]
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        failures.append("RECEIPT_SCHEMA_VERSION_INVALID")
    digest_payload = dict(receipt)
    observed_digest = digest_payload.pop("digest", None)
    try:
        expected_digest = _object_digest(digest_payload)
    except (TypeError, ValueError):
        return [*failures, "RECEIPT_CANONICALIZATION_FAILED"]
    if observed_digest != expected_digest:
        failures.append("RECEIPT_DIGEST_MISMATCH")
    sources = receipt.get("source_evidence")
    if not isinstance(sources, list):
        return [*failures, "SOURCE_EVIDENCE_INVALID"]
    source_map: dict[str, tuple[dict[str, Any], Path]] = {}
    source_blocked = False
    for item in sources:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("path"), str):
            failures.append("SOURCE_EVIDENCE_ENTRY_INVALID")
            source_blocked = True
            continue
        source_id = item["id"]
        if source_id in source_map:
            failures.append(f"SOURCE_EVIDENCE_DUPLICATE:{source_id}")
            source_blocked = True
            continue
        path = _resolve_source_path(item["path"], project_root)
        if not path.is_file():
            failures.append(f"SOURCE_FILE_MISSING:{source_id}")
            source_blocked = True
        else:
            try:
                if _file_digest(path) != item.get("file_sha256"):
                    failures.append(f"SOURCE_FILE_DIGEST_MISMATCH:{source_id}")
                    source_blocked = True
            except QuoteValueError:
                failures.append(f"SOURCE_FILE_NOT_READABLE:{source_id}")
                source_blocked = True
        source_map[source_id] = (item, path)
    if tuple(item.get("id") for item in sources if isinstance(item, dict)) != EXPECTED_SOURCE_IDS:
        failures.append("SOURCE_EVIDENCE_SET_OR_ORDER_INVALID")
        source_blocked = True
    if set(source_map) != set(EXPECTED_SOURCE_IDS):
        return failures
    if source_blocked:
        return failures
    try:
        expected = build_receipt(
            project_root=project_root,
            benchmark_root=source_map["benchmark_manifest"][1].parent,
            metric_registry_path=source_map["metric_registry"][1],
            evaluation_suite_path=source_map["evaluation_suite"][1],
            product_observations_path=source_map["product_path_observations"][1],
            currency_vmrc_path=source_map["currency_vmrc"][1],
            launch_vmrc_path=source_map["launch_vmrc"][1],
        )
    except QuoteValueError as exc:
        return [f"RECEIPT_RECOMPUTATION_FAILED:{exc}"]
    if receipt != expected:
        failures.append("RECEIPT_RECOMPUTATION_MISMATCH")
    return failures


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    project_root = args.project_root.resolve()
    receipt = build_receipt(
        project_root=project_root,
        benchmark_root=args.benchmark_root.resolve(),
        metric_registry_path=args.metric_registry.resolve(),
        evaluation_suite_path=args.evaluation_suite.resolve(),
        product_observations_path=args.product_observations.resolve(),
        currency_vmrc_path=args.currency_vmrc.resolve(),
        launch_vmrc_path=args.launch_vmrc.resolve(),
    )
    failures = verify_receipt(receipt, project_root=project_root)
    _require(not failures, ";".join(failures))
    output_dir = args.output_dir.resolve()
    receipt_path = output_dir / "quote-value-receipt.json"
    _write_json(receipt_path, receipt)
    verification = {
        "schema_version": "orgrebase.quote-value-verification.v1",
        "status": "PASS",
        "verification_mode": "PRODUCT_INDEPENDENT_DETERMINISTIC_REPLAY",
        "implementation_independence": "SHARED_VERIFY_RECEIPT_IMPLEMENTATION",
        "receipt_ref": receipt_path.name,
        "receipt_digest": receipt["digest"],
        "receipt_file_sha256": _file_digest(receipt_path),
        "evaluator_product_imports": 0,
        "verifier_product_imports": 0,
        "failures": [],
    }
    verification_path = output_dir / "verification.json"
    _write_json(verification_path, verification)
    index = {
        "schema_version": "orgrebase.quote-value-evidence-index.v1",
        "status": "PASS",
        "entries": [
            {"path": receipt_path.name, "file_sha256": _file_digest(receipt_path)},
            {"path": verification_path.name, "file_sha256": _file_digest(verification_path)},
        ],
        "digest": "",
    }
    index_payload = dict(index)
    index_payload.pop("digest")
    index["digest"] = _object_digest(index_payload)
    _write_json(output_dir / "evidence-index.json", index)
    return {
        "status": "PASS",
        "receipt": str(receipt_path),
        "receipt_digest": receipt["digest"],
        "evidence_ceiling": receipt["evidence_ceiling"],
        "modelled_saving_rate": next(
            metric["value"]
            for metric in receipt["metrics"]
            if metric["metric_id"] == "selective_rebase_cost_saving_rate"
        ),
        "declared_review_burden_envelope": receipt["sensitivity"][
            "declared_review_burden_envelope"
        ],
        "stress_envelope": receipt["sensitivity"]["stress_envelope"],
        "real_enterprise_roi": "NOT_RUN",
    }


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument("--benchmark-root", type=Path, default=root / "benchmark" / "quote-value-v0.1")
    parser.add_argument(
        "--metric-registry",
        type=Path,
        default=root / "configs" / "workspace" / "quote-value-metric-registry.json",
    )
    parser.add_argument(
        "--evaluation-suite",
        type=Path,
        default=root / "evidence" / "workspace" / "latest" / "evaluation-suite.json",
    )
    parser.add_argument(
        "--product-observations",
        type=Path,
        default=root / "evidence" / "workspace" / "latest" / "product-path-observations.json",
    )
    parser.add_argument(
        "--currency-vmrc",
        type=Path,
        default=root / "evidence" / "workspace" / "latest" / "rebase" / "currency-minimal-rebase-certificate.json",
    )
    parser.add_argument(
        "--launch-vmrc",
        type=Path,
        default=root / "evidence" / "workspace" / "latest" / "rebase" / "launch-minimal-rebase-certificate.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "evidence" / "quote-value" / "latest",
    )
    return parser


def main() -> int:
    try:
        result = run_benchmark(_parser().parse_args())
    except QuoteValueError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
