"""Fail-closed read model over the retained Spec 053 evidence lane.

This module deliberately projects frozen evidence; it is not a runtime adapter and
has no write capability. The retained Spec 053 run must never be presented as the
current Spec 054 Pack Pilot run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.mechanism_comparison_view import mechanism_comparison_view

SCHEMA_VERSION = "orgrebase.semifinal-evidence-view.v2"
PUBLIC_REAL_PROCESS_SCHEMA_VERSION = "orgrebase.public-real-process-validation-view.v1"
EVIDENCE_LANE = "RETAINED_SPEC_053_MECHANISM"
RETAINED_RUN_ID = "run:orgrebase:semifinal-closure:quote-001"
SUPPORTED_PUBLIC_VERTEX_MODEL_IDS = frozenset(
    {"gemini-3.7-flash", "gemini-3.8-flash"}
)
SKILL_NAMES = (
    "enterprise-launch-readiness",
    "enterprise-quote-compose",
    "structured-domain-handoff",
)

INDEXED_EVIDENCE_PATHS = {
    "quote_value": "quote-value/quote-value-receipt.json",
    "skill_discovery": "skills/discovery.json",
    "wheel_runtime": "skills/distribution/installed-wheel-receipt.json",
    "deployment_profile": "operations/operations/deployment-profile.json",
    "sbom": "operations/operations/sbom.cdx.json",
    "otlp_traces": "operations/observability/traces.otlp.json",
    "negative_alert_probe": "operations/observability/negative-alert-probe.json",
    "privacy_probe": "operations/observability/privacy-probe.json",
    "privacy_rejection": "operations/observability/privacy-rejection.json",
}


def _load(path: Path, expected: type[dict[str, Any]] | type[list[Any]]) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected):
        expected_name = "JSON_OBJECT" if expected is dict else "JSON_ARRAY"
        raise ValueError(f"{expected_name}_REQUIRED:{path.name}")
    return value


def _record_valid(value: Mapping[str, Any], digest_key: str = "digest") -> bool:
    body = {key: item for key, item in value.items() if key != digest_key}
    return value.get(digest_key) == sha256_digest(body)


def _file_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _attribute_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("key"), str):
            keys.add(value["key"])
        for child in value.values():
            keys.update(_attribute_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_attribute_keys(child))
    return keys


def _chain_valid(events: Sequence[Mapping[str, Any]]) -> bool:
    previous: str | None = None
    for index, event in enumerate(events, start=1):
        if not (
            _record_valid(event)
            and event.get("event_index") == index
            and event.get("previous_receipt_digest") == previous
        ):
            return False
        previous = event.get("digest")
    return True


def _public_real_process_base(status: str) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_REAL_PROCESS_SCHEMA_VERSION,
        "status": status,
        "read_model_target_writes": 0,
        "generated": status == "PASS",
    }


def _public_real_oac_adaptation_view(project_root: Path) -> dict[str, Any]:
    """Project the isolated BPI-to-OAC add-on without importing its producer."""

    root = project_root / "evidence/oac-public-real-process/latest"
    paths = {
        "receipt": root / "adaptation-receipt.json",
        "manifest": root / "manifest.json",
        "actions": root / "action-journal.json",
        "mapping": root / "mapping-candidate.json",
        "demand": root / "organizational-demand.json",
        "context": root / "context-capsule.json",
        "fallback": root / "deterministic-fallback-contract.json",
    }
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        return {
            "status": "NOT_PRODUCED",
            "read_model_target_writes": 0,
            "missing": missing,
        }
    try:
        receipt = _load(paths["receipt"], dict)
        manifest = _load(paths["manifest"], dict)
        actions = _load(paths["actions"], list)
        mapping = _load(paths["mapping"], dict)
        demand = _load(paths["demand"], dict)
        context = _load(paths["context"], dict)
        fallback = _load(paths["fallback"], dict)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "FAIL",
            "read_model_target_writes": 0,
            "failures": [f"LOAD:{type(exc).__name__}"],
        }

    failures: list[str] = []
    for name, value in (
        ("receipt", receipt),
        ("manifest", manifest),
        ("demand", demand),
        ("context", context),
        ("fallback", fallback),
    ):
        if not _record_valid(value):
            failures.append(f"DIGEST:{name}")
    if (
        not isinstance(actions, list)
        or not actions
        or not all(isinstance(item, dict) and _record_valid(item) for item in actions)
    ):
        failures.append("AGENTTEAMS_ACTION_DIGESTS")

    entries = manifest.get("entries", [])
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if (
        manifest.get("status") != "CLOSED_WORLD"
        or manifest.get("entry_count") != len(entries)
        or {item.get("path") for item in entries if isinstance(item, dict)} != actual_paths
        or any(
            not isinstance(item, dict)
            or not (root / str(item.get("path"))).is_file()
            or item.get("sha256") != _file_sha256(root / str(item.get("path")))
            or item.get("bytes") != (root / str(item.get("path"))).stat().st_size
            for item in entries
        )
    ):
        failures.append("CLOSED_WORLD_MANIFEST")

    model = receipt.get("model_attempt", {})
    lifecycle = receipt.get("agentteams", {})
    gate = receipt.get("review_gate", {})
    admission = receipt.get("human_admission", {})
    admitted = admission.get("admission", {})
    early = admission.get("early_attempt", {})
    typed = receipt.get("typed_scope_execution", {})
    unknowns = receipt.get("unknown_dimensions", {})
    expected_actions = [
        "create_project",
        "plan_dag",
        "ready_nodes",
        "delegate_task",
        "ack_task",
        "submit_task",
        "check_task",
        "accept_task_result",
        "complete_project",
    ]
    expected_unknown = {
        "status": "UNKNOWN",
        "execution_disposition": "HOLD",
        "human_supplement_required": True,
        "inferred_from_anonymous_log": False,
    }
    expected_limitations = [
        "TASK_DEFINED_GROUND_TRUTH_NOT_HUMAN_CAUSAL_ANNOTATION",
        "NOT_ENTERPRISE_QUOTE_DATA_OR_QUOTE_ROI",
        "NOT_ARBITRARY_ENTERPRISE_ADAPTATION",
        "NOT_PRODUCTION_DEPLOYMENT",
        "NOT_EXTERNAL_HUMAN_ACCEPTANCE",
    ]
    action_digests = [item.get("digest") for item in actions if isinstance(item, Mapping)]
    deterministic_checks = receipt.get("deterministic_validation", {}).get("checks", {})
    if not (
        receipt.get("status") == "PASS"
        and receipt.get("claim_ceiling") == "PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION"
        and receipt.get("candidate_only") is True
        and receipt.get("canonical_target_writes") == 0
        and receipt.get("adaptation_run_id") != receipt.get("execution_run_id")
        and receipt.get("mapping_candidate_digest") == sha256_digest(mapping)
        and receipt.get("organizational_demand_digest") == demand.get("digest")
        and receipt.get("context_capsule_digest") == context.get("digest")
        and lifecycle.get("action_sequence") == expected_actions
        and lifecycle.get("action_receipt_digests") == action_digests
        and len(actions) == len(expected_actions)
        and lifecycle.get("terminal_state") == "completed"
        and lifecycle.get("run_id") == receipt.get("adaptation_run_id")
        and lifecycle.get("candidate_only") is True
        and lifecycle.get("canonical_target_writes") == 0
        and lifecycle.get("claim_boundary") == "PINNED_IN_PROCESS_AGENTTEAMS_NOT_LIVE_DISTRIBUTED_WORKER"
        and model.get("status") == "VALID"
        and model.get("evidence_class") == "LIVE_MODEL"
        and model.get("provider") == "vertex-ai"
        and model.get("model_id") in SUPPORTED_PUBLIC_VERTEX_MODEL_IDS
        and model.get("provider_request_id_present") is True
        and model.get("selected") is True
        and model.get("fallback_contract_digest") == fallback.get("digest")
        and receipt.get("selected_mapping_lane") == "LIVE_VERTEX_AGENT_CANDIDATE"
        and receipt.get("deterministic_validation", {}).get("verdict") == "PASS"
        and isinstance(deterministic_checks, Mapping)
        and deterministic_checks
        and all(value is True for value in deterministic_checks.values())
        and set(unknowns)
        == {
            "ORGANIZATION_VALUES",
            "REAL_RESPONSIBLE_OWNERS",
            "PERMISSIONS",
            "APPROVAL_AUTHORITIES",
        }
        and all(value == expected_unknown for value in unknowns.values())
        and gate.get("adaptation_run_id") == receipt.get("adaptation_run_id")
        and gate.get("review_duration_ms") == 4000
        and gate.get("canonical_target_writes") == 0
        and early.get("status") == "REJECTED_TOO_EARLY"
        and early.get("remaining_ms") == 4000
        and admitted.get("status") == "ADMITTED"
        and admitted.get("elapsed_ms", 0) >= 4000
        and admitted.get("actor_id") == gate.get("owner_ref")
        and admitted.get("gate_digest") == gate.get("digest")
        and admitted.get("candidate_only") is True
        and admitted.get("canonical_target_writes") == 0
        and admitted.get("interaction_evidence")
        == "CONTROLLED_LOCAL_SCRIPTED_OWNER_COMMAND_NOT_EXTERNAL_HUMAN"
        and typed.get("adaptation_run_id") == receipt.get("adaptation_run_id")
        and typed.get("execution_run_id") == receipt.get("execution_run_id")
        and typed.get("organizational_demand_digest") == demand.get("digest")
        and typed.get("context_capsule_digest") == context.get("digest")
        and typed.get("ground_truth_class") == "TASK_DEFINED_QUERY_GROUND_TRUTH"
        and typed.get("query_count") == 128
        and typed.get("recall") == 1.0
        and typed.get("unsafe_false_unaffected_rate") == 0.0
        and typed.get("canonical_target_writes") == 0
        and fallback.get("status") == "AVAILABLE_NOT_SELECTED"
        and receipt.get("limitations") == expected_limitations
    ):
        failures.append("ADAPTATION_SEMANTICS")
    if failures:
        return {
            "status": "FAIL",
            "read_model_target_writes": 0,
            "failures": sorted(set(failures)),
        }
    return {
        "status": "PASS",
        "claim_ceiling": receipt["claim_ceiling"],
        "adaptation_run_id": receipt["adaptation_run_id"],
        "execution_run_id": receipt["execution_run_id"],
        "model": {
            "status": model["status"],
            "evidence_class": model["evidence_class"],
            "provider": model["provider"],
            "model_id": model["model_id"],
            "provider_request_observed": model["provider_request_id_present"],
        },
        "agentteams": {
            "action_count": len(actions),
            "action_sequence": expected_actions,
            "terminal_state": lifecycle["terminal_state"],
            "evidence_class": lifecycle["evidence_class"],
        },
        "mapping": {
            "lane": receipt["selected_mapping_lane"],
            "deterministic_verdict": receipt["deterministic_validation"]["verdict"],
            "unknown_count": len(unknowns),
            "unknown_dimensions": sorted(unknowns),
            "unknown_disposition": "HOLD_FOR_HUMAN_SUPPLEMENT",
        },
        "admission": {
            "review_duration_ms": gate["review_duration_ms"],
            "early_attempt": early["status"],
            "elapsed_ms": admitted["elapsed_ms"],
            "mode": admitted["interaction_evidence"],
            "external_human_identity_proven": False,
        },
        "execution": {
            "query_count": typed["query_count"],
            "recall": typed["recall"],
            "unsafe_false_unaffected_rate": typed["unsafe_false_unaffected_rate"],
            "canonical_target_writes": typed["canonical_target_writes"],
        },
        "verification": {
            "closed_world_manifest_digest": manifest["digest"],
            "entry_count": manifest["entry_count"],
            "adaptation_receipt_digest": receipt["digest"],
        },
        "limitations": receipt["limitations"],
        "read_model_target_writes": 0,
    }


def public_real_process_validation_view(project_root: Path) -> dict[str, Any]:
    """Project the independently replayed BPI 2019 lane without write authority.

    This lane is intentionally separate from the Golden quote run and from the
    retained AgentTeams mechanism run.  Every displayed fact is loaded from the
    retained public-data projection, its signed benchmark receipt, or the
    official dataset/license manifests.  Missing or inconsistent evidence is
    never replaced with built-in demo values.
    """

    benchmark_root = project_root / "benchmark/quote-value-v0.4-bpi-real-process"
    evidence_root = project_root / "evidence/public-real-process/latest"
    paths: dict[str, tuple[Path, type[Any]]] = {
        "summary": (evidence_root / "summary.json", dict),
        "receipt": (
            evidence_root / "bpi2019-real-process-benchmark-receipt.json",
            dict,
        ),
        "verification": (
            evidence_root / "bpi2019-real-process-verification.json",
            dict,
        ),
        "dataset_manifest": (benchmark_root / "dataset-manifest.json", dict),
        "license_manifest": (benchmark_root / "LICENSES.json", dict),
        "projection": (
            benchmark_root / "projection/bpi2019-real-process-projection.json",
            dict,
        ),
    }
    missing = sorted(name for name, (path, _) in paths.items() if not path.is_file())
    if missing:
        result = _public_real_process_base("UNAVAILABLE")
        result["missing"] = missing
        return result
    try:
        records = {name: _load(path, expected) for name, (path, expected) in paths.items()}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        result = _public_real_process_base("FAIL")
        result["failures"] = [type(exc).__name__]
        return result

    failures: list[str] = []
    for name, record in records.items():
        if not _record_valid(record):
            failures.append(f"DIGEST:{name}")

    summary = records["summary"]
    receipt = records["receipt"]
    verification = records["verification"]
    dataset = records["dataset_manifest"]
    license_manifest = records["license_manifest"]
    projection = records["projection"]
    input_closure = receipt.get("input_closure", {})
    artifact = dataset.get("artifact", {})
    official_counts = dataset.get("official_counts", {})

    if not (
        summary.get("status") == receipt.get("status") == verification.get("status") == "PASS"
        and summary.get("run_id") == receipt.get("run_id")
        and summary.get("receipt_digest") == receipt.get("digest")
        and verification.get("receipt_digest") == receipt.get("digest")
        and verification.get("projection_digest") == projection.get("digest")
        and input_closure.get("projection_digest") == projection.get("digest")
        and input_closure.get("dataset_manifest_digest") == dataset.get("digest")
        and input_closure.get("license_manifest_digest") == license_manifest.get("digest")
        and dataset.get("projection_digest") == projection.get("digest")
        and dataset.get("license_manifest_digest") == license_manifest.get("digest")
    ):
        failures.append("RECEIPT_BINDING")

    manifest_files = input_closure.get("manifest_file_sha256", {})
    if not (
        manifest_files.get("dataset-manifest.json")
        == _file_sha256(paths["dataset_manifest"][0]).removeprefix("sha256:")
        and manifest_files.get("LICENSES.json")
        == _file_sha256(paths["license_manifest"][0]).removeprefix("sha256:")
        and manifest_files.get("projection/bpi2019-real-process-projection.json")
        == _file_sha256(paths["projection"][0]).removeprefix("sha256:")
    ):
        failures.append("FILE_BINDING")

    if not (
        dataset.get("dataset_id") == "bpi-challenge-2019-4tu-12715853"
        and dataset.get("dataset_kind") == "REAL_ANONYMIZED_ENTERPRISE_PURCHASE_TO_PAY_EVENT_LOG"
        and dataset.get("organization_profile") == "LARGE_MULTINATIONAL_COATINGS_AND_PAINT_COMPANY"
        and isinstance(dataset.get("source_description"), str)
        and dataset.get("source_description")
        and official_counts
        == {
            "purchase_order_items": 251734,
            "events": 1595923,
            "activity_types": 42,
            "anonymous_identities": 627,
        }
        and dataset.get("license") == "CC-BY-4.0"
        and artifact.get("raw_bytes_in_repository") is False
        and isinstance(artifact.get("sha256"), str)
        and artifact.get("sha256", "").startswith("sha256:")
    ):
        failures.append("SOURCE_METADATA")

    scenario = receipt.get("scenario", {})
    selection = projection.get("selection", {})
    summary_strategies = {
        item.get("strategy_id"): item for item in summary.get("strategies", []) if isinstance(item, dict)
    }
    receipt_strategies = {
        item.get("strategy_id"): item for item in receipt.get("strategies", []) if isinstance(item, dict)
    }
    expected_strategy_ids = {
        "VENDOR_BROADCAST",
        "PURCHASE_DOCUMENT_SCOPE",
        "OAC_TYPED_ITEM_SCOPE",
    }
    if not (
        summary.get("query_count") == scenario.get("query_count") == 128
        and summary.get("unique_observed_event_count") == scenario.get("observed_event_count") == 45227
        and scenario.get("change_event_type") == "Change Price"
        and scenario.get("window_days") == 30
        and verification.get("queries_replayed") == 128
        and verification.get("strategies_replayed") == 3
        and verification.get("failures") == []
        and selection.get("eligible_queries") == 8318
        and selection.get("selected_queries") == 128
        and selection.get("outcome_blind_ordering") is True
        and receipt.get("query_ground_truth_evidence_class") == "TASK_DEFINED_QUERY_GROUND_TRUTH"
        and set(summary_strategies) == set(receipt_strategies) == expected_strategy_ids
    ):
        failures.append("SCENARIO_CONTRACT")

    expected_scopes = {
        "VENDOR_BROADCAST": (66549, 0.0),
        "PURCHASE_DOCUMENT_SCOPE": (3415, 0.948684),
        "OAC_TYPED_ITEM_SCOPE": (201, 0.99698),
    }
    for strategy_id, (expected_scope, expected_reduction) in expected_scopes.items():
        compact = summary_strategies.get(strategy_id, {})
        retained = receipt_strategies.get(strategy_id, {})
        counts = retained.get("counts", {})
        metrics = retained.get("metrics", {})
        if not (
            compact.get("action_scope_query_event_pairs") == expected_scope
            and counts.get("conservative_action_scope") == expected_scope
            and compact.get("safe_scope_reduction_vs_vendor_broadcast") == expected_reduction
            and metrics.get("safe_scope_reduction_vs_vendor_broadcast", {}).get("value") == expected_reduction
            and compact.get("recall") == metrics.get("recall", {}).get("value") == 1.0
            and compact.get("unsafe_false_unaffected_rate")
            == metrics.get("unsafe_false_unaffected_rate", {}).get("value")
            == 0.0
            and compact.get("lineage_closure_rate")
            == metrics.get("lineage_closure_rate", {}).get("value")
            == 1.0
            and counts.get("false_negative") == 0
        ):
            failures.append(f"STRATEGY:{strategy_id}")

    if failures:
        result = _public_real_process_base("FAIL")
        result["failures"] = sorted(set(failures))
        return result

    vendor = summary_strategies["VENDOR_BROADCAST"]
    document = summary_strategies["PURCHASE_DOCUMENT_SCOPE"]
    oac = summary_strategies["OAC_TYPED_ITEM_SCOPE"]
    result = _public_real_process_base("PASS")
    result.update(
        {
            "run_id": summary["run_id"],
            "archive_status": "FROZEN_HISTORICAL_VALIDATION",
            "archive_snapshot_at": None,
            "archive_time_basis": "CONTENT_ADDRESSED_EVIDENCE_NO_TRUSTED_COMPLETION_TIME",
            # Use the independent verifier receipt, not only the input projection,
            # as the content-addressed identity of this frozen validation lane.
            "archive_identity_digest": verification["digest"],
            "current_task_run": False,
            "dataset": {
                "title": dataset["title"],
                "source_nature_zh": summary["source_nature_zh"],
                "source_nature_en": summary["source_nature_en"],
                "purchase_order_item_traces": official_counts["purchase_order_items"],
                "events": official_counts["events"],
                "activity_types": official_counts["activity_types"],
                "anonymous_identities": official_counts["anonymous_identities"],
            },
            "scenario": {
                "task_id": scenario["task_id"],
                "change_event_type": scenario["change_event_type"],
                "query_count": scenario["query_count"],
                "window_days": scenario["window_days"],
                "unique_observed_event_count": summary["unique_observed_event_count"],
                "counting_unit": summary["counting_unit"],
                "eligible_query_count": selection["eligible_queries"],
                "selection_ordering": selection["ordering"],
                "outcome_blind_ordering": selection["outcome_blind_ordering"],
                "ground_truth_evidence_class": receipt["query_ground_truth_evidence_class"],
            },
            "action_scopes": {
                "vendor_broadcast": {
                    "action_scope": vendor["action_scope_query_event_pairs"],
                    "safe_reduction": vendor["safe_scope_reduction_vs_vendor_broadcast"],
                },
                "purchase_document": {
                    "action_scope": document["action_scope_query_event_pairs"],
                    "safe_reduction": document["safe_scope_reduction_vs_vendor_broadcast"],
                },
                "oac_typed_item": {
                    "action_scope": oac["action_scope_query_event_pairs"],
                    "safe_reduction": oac["safe_scope_reduction_vs_vendor_broadcast"],
                },
            },
            "assurance": {
                "query_contract_recall": oac["recall"],
                "unsafe_false_unaffected": receipt_strategies["OAC_TYPED_ITEM_SCOPE"]["counts"][
                    "false_negative"
                ],
                "unsafe_false_unaffected_rate": oac["unsafe_false_unaffected_rate"],
                "lineage_closure_rate": oac["lineage_closure_rate"],
            },
            "source": {
                "official_record": dataset["official_record"],
                "doi": dataset["doi"],
                "license": dataset["license"],
                "organization_profile": dataset["organization_profile"],
                "source_description": dataset["source_description"],
                "raw_sha256": artifact["sha256"],
                "raw_md5": artifact["md5"],
                "raw_bytes_in_repository": artifact["raw_bytes_in_repository"],
            },
            "verification": {
                "status": verification["status"],
                "mode": verification["verification_mode"],
                "queries_replayed": verification["queries_replayed"],
                "strategies_replayed": verification["strategies_replayed"],
                "benchmark_receipt_digest": receipt["digest"],
                "replay_receipt_digest": verification["digest"],
                "projection_digest": projection["digest"],
                "summary_digest": summary["digest"],
            },
            "claim_boundary": summary["claim_boundary"],
            "boundary_zh": summary["boundary_zh"],
            "boundary_en": summary["boundary_en"],
            "agentic_adaptation": _public_real_oac_adaptation_view(project_root),
        }
    )
    return result


def _base(status: str, claim_boundary: str) -> dict[str, Any]:
    """Return a lane-labelled envelope that cannot imply write authority."""

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "verification_status": status,
        "completion_status": "NOT_ASSESSED",
        "production_readiness": False,
        "evidence_lane": EVIDENCE_LANE,
        "title": "Independent mechanism evidence (separate run-scoped lane)",
        "read_model_target_writes": 0,
        "current_pack_pilot_run": False,
        "pack_pilot_binding": "NOT_SAME_RUN",
        "claim_boundary": claim_boundary,
    }


def _fail_closed(failures: Sequence[str]) -> dict[str, Any]:
    result = _base("FAIL", "EVIDENCE_VIEW_FAIL_CLOSED;NO_EVIDENCE_PROJECTION")
    result["failures"] = sorted(set(failures))
    return result


def _formation_taskflow_view(project_root: Path) -> dict[str, Any]:
    """Project the independent two-domain Formation -> AgentTeams proof.

    This proof belongs to a separate retained run.  A broken or absent pack does
    not invalidate the older semifinal evidence lane, but it must never yield a
    partial topology projection.
    """

    root = project_root / "evidence/formation-taskflow/latest"
    paths: dict[str, tuple[Path, type[Any]]] = {
        "plan": (root / "inputs/execution-plan.json", dict),
        "formation": (root / "inputs/formation-receipt.json", dict),
        "context": (root / "inputs/context-envelope.json", dict),
        "coalition": (root / "inputs/coalition-plan.json", dict),
        "receipt": (root / "probe-receipt.json", dict),
        "verification": (root / "verification.json", dict),
        "actions": (root / "action-journal.json", list),
    }

    missing = sorted(name for name, (path, _) in paths.items() if not path.is_file())
    if missing:
        return {
            "status": "NOT_PRODUCED",
            "read_model_target_writes": 0,
            "missing": missing,
        }
    try:
        records = {name: _load(path, expected) for name, (path, expected) in paths.items()}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "FAIL",
            "read_model_target_writes": 0,
            "failures": [f"LOAD:{type(exc).__name__}"],
        }

    plan = records["plan"]
    formation = records["formation"]
    context = records["context"]
    coalition = records["coalition"]
    receipt = records["receipt"]
    verification = records["verification"]
    actions = records["actions"]
    failures: list[str] = []

    for name, record in (
        ("plan", plan),
        ("formation", formation),
        ("context", context),
        ("coalition", coalition),
        ("receipt", receipt),
        ("verification", verification),
    ):
        if not _record_valid(record):
            failures.append(f"DIGEST:{name}")

    plan_tasks = plan.get("tasks", [])
    if not isinstance(plan_tasks, list) or not all(
        isinstance(item, dict) and _record_valid(item) for item in plan_tasks
    ):
        failures.append("PLAN_TASK_DIGESTS")
        plan_tasks = []
    if not isinstance(actions, list) or not all(
        isinstance(item, dict) and _record_valid(item) for item in actions
    ):
        failures.append("AGENTTEAMS_ACTION_DIGESTS")
        actions = []

    selected = plan.get("selected_domain_ids", [])
    selected_domains = (
        selected
        if isinstance(selected, list) and all(isinstance(item, str) and item for item in selected)
        else []
    )
    domain_tasks = [item for item in plan_tasks if item.get("task_kind") == "DOMAIN"]
    reviewer_tasks = [item for item in plan_tasks if item.get("task_kind") == "REVIEWER_BARRIER"]
    task_ids = [item.get("task_id") for item in plan_tasks]
    domain_task_ids = [item.get("task_id") for item in domain_tasks]
    domain_ids = [item.get("domain_id") for item in domain_tasks]
    reviewer = reviewer_tasks[0] if len(reviewer_tasks) == 1 else {}
    if not (
        plan.get("schema_version") == "orgrebase.agentteams-execution-plan.v1"
        and plan.get("candidate_only") is True
        and plan.get("canonical_target_writes") == 0
        and len(selected_domains) == 2
        and selected_domains == sorted(set(selected_domains))
        and len(domain_tasks) == len(selected_domains)
        and domain_ids == selected_domains
        and len(reviewer_tasks) == 1
        and len(plan_tasks) == len(selected_domains) + 1
        and len(task_ids) == len(set(task_ids))
        and reviewer.get("depends_on") == domain_task_ids
        and reviewer.get("domain_id") == "reviewer"
        and all(
            item.get("candidate_only") is True
            and item.get("canonical_target_writes") == 0
            and item.get("effect_ceiling") == "ZERO_EXTERNAL_EFFECTS"
            and item.get("formation_receipt_digest") == formation.get("digest")
            and item.get("context_envelope_digest") == context.get("digest")
            and isinstance(item.get("input_schema_digest"), str)
            and isinstance(item.get("output_schema_digest"), str)
            for item in plan_tasks
        )
    ):
        failures.append("EXECUTION_PLAN_SEMANTICS")

    context_bindings = context.get("domain_bindings", [])
    bindings_by_domain = {
        item.get("domain_id"): item
        for item in context_bindings
        if isinstance(item, dict) and isinstance(item.get("domain_id"), str)
    }
    if not (
        len(bindings_by_domain) == len(selected_domains)
        and sorted(bindings_by_domain) == selected_domains
        and all(
            bindings_by_domain.get(item.get("domain_id"), {}).get("capability_card_digest")
            == item.get("capability_card_digest")
            and bindings_by_domain.get(item.get("domain_id"), {}).get("actor_projection_digest")
            == item.get("actor_projection_digest")
            and item.get("capability_card_ref")
            and item.get("actor_projection_ref")
            and item.get("assignee_actor_id")
            for item in domain_tasks
        )
    ):
        failures.append("DOMAIN_BINDINGS")

    coalition_domains = sorted(
        {
            item.get("domain_id")
            for item in coalition.get("coverage", [])
            if isinstance(item, dict) and isinstance(item.get("domain_id"), str)
        }
    )
    if not (
        formation.get("digest") == plan.get("formation_receipt_digest")
        and formation.get("selected_domain_ids") == selected_domains
        and formation.get("unknown_obligation_resource_ids") == []
        and formation.get("coalition_plan_digest") == coalition.get("digest")
        and formation.get("candidate_only") is True
        and formation.get("canonical_target_writes") == 0
        and formation.get("effect_ceiling") == "ZERO_EXTERNAL_EFFECTS"
        and context.get("digest") == plan.get("context_envelope_digest")
        and context.get("task_formation_decision_receipt_digest") == formation.get("digest")
        and context.get("coalition_plan_digest") == coalition.get("digest")
        and context.get("candidate_only") is True
        and context.get("canonical_target_writes") == 0
        and context.get("effect_ceiling") == "ZERO_EXTERNAL_EFFECTS"
        and coalition.get("digest") == plan.get("coalition_plan_digest")
        and coalition_domains == selected_domains
        and formation.get("task_ref") == context.get("task_ref") == coalition.get("task_ref")
    ):
        failures.append("FORMATION_LINEAGE")

    task_receipts = receipt.get("task_receipts", [])
    task_receipts_by_id = {
        item.get("task_id"): item
        for item in task_receipts
        if isinstance(item, dict) and isinstance(item.get("task_id"), str)
    }
    if not (
        isinstance(task_receipts, list)
        and len(task_receipts_by_id) == len(plan_tasks)
        and all(_record_valid(item) for item in task_receipts_by_id.values())
        and all(
            task_receipts_by_id.get(item.get("task_id"), {}).get("execution_task_digest")
            == item.get("digest")
            and task_receipts_by_id.get(item.get("task_id"), {}).get("task_kind") == item.get("task_kind")
            and task_receipts_by_id.get(item.get("task_id"), {}).get("domain_id") == item.get("domain_id")
            and task_receipts_by_id.get(item.get("task_id"), {}).get("assignee_actor_id")
            == item.get("assignee_actor_id")
            and task_receipts_by_id.get(item.get("task_id"), {}).get("depends_on") == item.get("depends_on")
            and task_receipts_by_id.get(item.get("task_id"), {}).get("terminal_status") == "completed"
            and task_receipts_by_id.get(item.get("task_id"), {}).get("candidate_only") is True
            and task_receipts_by_id.get(item.get("task_id"), {}).get("canonical_target_writes") == 0
            for item in plan_tasks
        )
    ):
        failures.append("TASK_RECEIPT_BINDINGS")

    action_digests = [item.get("digest") for item in actions]
    if not (
        receipt.get("schema_version") == "orgrebase.formation-taskflow-probe-receipt.v1"
        and receipt.get("execution_plan_digest") == plan.get("digest")
        and receipt.get("formation_receipt_digest") == formation.get("digest")
        and receipt.get("context_envelope_digest") == context.get("digest")
        and receipt.get("coalition_plan_digest") == coalition.get("digest")
        and receipt.get("selected_domain_ids") == selected_domains
        and receipt.get("expected_task_ids")
        == receipt.get("planned_task_ids")
        == receipt.get("actual_terminal_task_ids")
        == task_ids
        and receipt.get("reviewer_task_id") == reviewer.get("task_id")
        and receipt.get("agentteams_action_count") == len(actions) == 20
        and receipt.get("agentteams_action_digests") == action_digests
        and [item.get("sequence") for item in actions] == list(range(1, len(actions) + 1))
        and all(item.get("ok") is True for item in actions)
        and receipt.get("project_terminal_state") == "completed"
        and receipt.get("candidate_status") == "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
        and receipt.get("candidate_only") is True
        and receipt.get("canonical_target_writes") == 0
    ):
        failures.append("PROBE_RECEIPT_SEMANTICS")

    if not (
        verification.get("status") == "PASS"
        and verification.get("run_id") == receipt.get("run_id")
        and verification.get("execution_plan_digest") == plan.get("digest")
        and verification.get("verified_receipt_digest") == receipt.get("digest")
        and verification.get("selected_domain_ids")
        == verification.get("planned_domain_ids")
        == verification.get("actual_domain_ids")
        == selected_domains
        and verification.get("topology_match") is True
        and verification.get("domain_task_count") == len(domain_tasks) == 2
        and verification.get("reviewer_task_count") == len(reviewer_tasks) == 1
        and verification.get("task_binding_count") == len(plan_tasks) == 3
        and verification.get("actual_task_ids") == task_ids
        and verification.get("agentteams_actions") == len(actions)
        and verification.get("project_terminal_state") == "completed"
        and verification.get("candidate_only") is True
        and verification.get("canonical_target_writes") == 0
        and verification.get("evidence_class") == receipt.get("evidence_class")
        and verification.get("claim_boundary") == receipt.get("claim_boundary")
        and verification.get("verification_strength") == "PINNED_CHECKOUT_REPLAY"
    ):
        failures.append("INDEPENDENT_VERIFICATION_SEMANTICS")

    if failures:
        return {
            "status": "FAIL",
            "read_model_target_writes": 0,
            "failures": sorted(set(failures)),
        }

    return {
        "status": "PASS",
        "source": "RETAINED_TWO_DOMAIN_FORMATION_TASKFLOW",
        "run_id": receipt["run_id"],
        "planned_domain_ids": selected_domains,
        "actual_agentteams_domain_ids": verification["actual_domain_ids"],
        "planned_domain_count": len(selected_domains),
        "actual_agentteams_domain_count": len(verification["actual_domain_ids"]),
        "topology_match": True,
        "domain_tasks": [
            {
                "task_id": item["task_id"],
                "domain_id": item["domain_id"],
                "assignee_actor_id": item["assignee_actor_id"],
                "digest": item["digest"],
                "terminal_status": task_receipts_by_id[item["task_id"]]["terminal_status"],
            }
            for item in domain_tasks
        ],
        "reviewer_barrier": {
            "task_id": reviewer["task_id"],
            "assignee_actor_id": reviewer["assignee_actor_id"],
            "depends_on": reviewer["depends_on"],
            "digest": reviewer["digest"],
            "terminal_status": task_receipts_by_id[reviewer["task_id"]]["terminal_status"],
        },
        "agentteams_action_count": len(actions),
        "task_binding_count": len(plan_tasks),
        "project_terminal_state": receipt["project_terminal_state"],
        "execution_plan_digest": plan["digest"],
        "formation_receipt_digest": formation["digest"],
        "context_envelope_digest": context["digest"],
        "coalition_plan_digest": coalition["digest"],
        "receipt_digest": receipt["digest"],
        "verification_digest": verification["digest"],
        "verification_strength": verification["verification_strength"],
        "candidate_only": True,
        "canonical_target_writes": 0,
        "claim_boundary": verification["claim_boundary"],
        "read_model_target_writes": 0,
    }


def _compensation_mechanisms_view(project_root: Path) -> dict[str, Any]:
    """Project independent local compensation proofs without merging runs."""

    evidence = project_root / "evidence/latest"
    paths = {
        "failure": evidence / "failure-receipt.json",
        "rollback": evidence / "rollback-evidence.json",
        "git": evidence / "git-tool-evidence.json",
    }
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        return {
            "status": "UNAVAILABLE",
            "missing": missing,
            "claim_boundary": "INDEPENDENT_MECHANISM_EVIDENCE_NOT_SAME_GOLDEN_RUN",
            "read_model_target_writes": 0,
        }
    try:
        failure = _load(paths["failure"], dict)
        rollback = _load(paths["rollback"], dict)
        git = _load(paths["git"], dict)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {
            "status": "FAIL_CLOSED",
            "claim_boundary": "INDEPENDENT_MECHANISM_EVIDENCE_INVALID",
            "read_model_target_writes": 0,
        }

    rollback_receipt = rollback.get("receipt")
    patch_receipt = git.get("patch", {}).get("receipt")
    compensation_receipt = git.get("compensation", {}).get("receipt")
    saga = git.get("compensation_saga")
    verification = git.get("verification")
    valid = (
        _record_valid(failure)
        and failure.get("status") == "REJECTED_BEFORE_WRITE"
        and failure.get("target_writes") == 0
        and failure.get("before_state_digest") == failure.get("after_state_digest")
        and isinstance(rollback_receipt, dict)
        and _record_valid(rollback_receipt)
        and rollback_receipt.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and rollback_receipt.get("authoritative_claim_unchanged") is True
        and all(
            item.get("status") == "SUCCEEDED" for item in rollback_receipt.get("compensation_results", [])
        )
        and isinstance(patch_receipt, dict)
        and _record_valid(patch_receipt)
        and patch_receipt.get("status") == "SUCCEEDED"
        and isinstance(compensation_receipt, dict)
        and _record_valid(compensation_receipt)
        and compensation_receipt.get("status") == "SUCCEEDED"
        and isinstance(saga, dict)
        and _record_valid(saga)
        and saga.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and saga.get("residual_effects") == []
        and isinstance(verification, dict)
        and verification.get("status") == "PASS"
        and verification.get("worktree_clean") is True
        and git.get("evidence_boundary") == "LOCAL_REAL_TOOL"
    )
    if not valid:
        return {
            "status": "FAIL_CLOSED",
            "claim_boundary": "INDEPENDENT_MECHANISM_EVIDENCE_INVALID",
            "read_model_target_writes": 0,
        }
    return {
        "status": "PASS",
        "canonical_transaction": "VALIDATED_CONTROLLED_LOCAL_ATOMIC_ROLLBACK",
        "downstream_state_compensation": ("VALIDATED_LOCAL_DETERMINISTIC_SEPARATE_RUN"),
        "reversible_external_git": "VALIDATED_LOCAL_REAL_TOOL_SEPARATE_RUN",
        "real_enterprise_connector": "NOT_RUN",
        "git_residual_effect_count": 0,
        "failure_receipt_digest": failure["digest"],
        "rollback_receipt_digest": rollback_receipt["digest"],
        "git_patch_receipt_digest": patch_receipt["digest"],
        "git_compensation_receipt_digest": compensation_receipt["digest"],
        "git_saga_receipt_digest": saga["digest"],
        "claim_boundary": (
            "INDEPENDENT_LOCAL_MECHANISM_EVIDENCE_NOT_SAME_GOLDEN_RUN_NOT_REAL_ENTERPRISE_CONNECTOR"
        ),
        "read_model_target_writes": 0,
    }


def _paths(project_root: Path) -> dict[str, tuple[Path, type[Any]]]:
    closure = project_root / "evidence/semifinal-closure/latest"
    governed = project_root / "evidence/semifinal-governed/latest"
    paths: dict[str, tuple[Path, type[Any]]] = {
        "candidate": (closure / "summary.json", dict),
        "lifecycle": (closure / "agentteams/lifecycle-receipt.json", dict),
        "governed": (governed / "summary.json", dict),
        "recovery": (governed / "runtime/process-recovery.json", dict),
        "public": (
            project_root / "evidence/public-process/latest/public-process-bridge-receipt.json",
            dict,
        ),
        "rollback": (
            project_root / "evidence/skill-predecessor-rollback/latest/summary.json",
            dict,
        ),
        "operations_summary": (closure / "operations/summary.json", dict),
        "alert": (
            closure / "operations/observability/alert-receipt.json",
            dict,
        ),
        "negative_alert_probe": (
            closure / "operations/observability/negative-alert-probe.json",
            dict,
        ),
        "privacy_probe": (
            closure / "operations/observability/privacy-probe.json",
            dict,
        ),
        "privacy_rejection": (
            closure / "operations/observability/privacy-rejection.json",
            dict,
        ),
        "retention": (
            closure / "operations/observability/retention-receipt.json",
            dict,
        ),
        "query": (
            closure / "operations/observability/query-receipt.json",
            dict,
        ),
        "capacity": (
            closure / "operations/operations/capacity-smoke-receipt.json",
            dict,
        ),
        "backup": (
            closure / "operations/operations/backup-restore-receipt.json",
            dict,
        ),
        "source_receipt": (closure / "operations/source/receipt.json", dict),
        "tool_receipt": (closure / "operations/tool/receipt.json", dict),
        "skill_invocation": (
            closure / "skills/quote-compose/invocation-receipt.json",
            dict,
        ),
        "otlp_exports": (
            closure / "operations/observability/export-receipts.json",
            list,
        ),
        "skills_summary": (closure / "skills/summary.json", dict),
        "closure_index": (closure / "evidence-index.json", dict),
        "quote_value": (closure / "quote-value/quote-value-receipt.json", dict),
        "process_baseline": (
            project_root / "benchmark/quote-value-v0.1/public/current-process-baseline.json",
            dict,
        ),
        "skill_discovery": (closure / "skills/discovery.json", list),
        "wheel_runtime": (
            closure / "skills/distribution/installed-wheel-receipt.json",
            dict,
        ),
        "deployment_profile": (
            closure / "operations/operations/deployment-profile.json",
            dict,
        ),
        "sbom": (closure / "operations/operations/sbom.cdx.json", dict),
        "otlp_traces": (
            closure / "operations/observability/traces.otlp.json",
            dict,
        ),
        "semifinal_mvp": (
            project_root / "evidence/semifinal-mvp/latest/summary.json",
            dict,
        ),
    }
    for skill_name in SKILL_NAMES:
        paths[f"skill_evaluation:{skill_name}"] = (
            closure / f"skills/evaluations/{skill_name}.json",
            dict,
        )
        paths[f"skill_requalification:{skill_name}"] = (
            closure / f"skills/requalifications/evaluations/{skill_name}.json",
            dict,
        )
        paths[f"skill_releases:{skill_name}"] = (
            closure / f"skills/releases/{skill_name}.json",
            list,
        )
        paths[f"skill_rollback_probe:{skill_name}"] = (
            closure / f"skills/rollback-decision-probes/{skill_name}.json",
            list,
        )
    return paths


def _validate_records(records: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    for name in (
        "candidate",
        "governed",
        "recovery",
        "public",
        "rollback",
        "operations_summary",
        "alert",
        "negative_alert_probe",
        "retention",
        "query",
        "capacity",
        "backup",
        "source_receipt",
        "tool_receipt",
        "skill_invocation",
        "skills_summary",
        "closure_index",
        "quote_value",
        "wheel_runtime",
        "semifinal_mvp",
    ):
        if not _record_valid(records[name]):
            failures.append(f"{name.upper()}_DIGEST")

    lifecycle = records["lifecycle"]
    if not _record_valid(lifecycle, "receipt_digest"):
        failures.append("LIFECYCLE_DIGEST")
    actions = lifecycle.get("actions")
    if not isinstance(actions, list) or not all(
        isinstance(action, dict) and _record_valid(action) for action in actions
    ):
        failures.append("AGENTTEAMS_ACTION_DIGESTS")

    exports = records["otlp_exports"]
    if not all(isinstance(receipt, dict) and _record_valid(receipt) for receipt in exports):
        failures.append("OTLP_EXPORT_DIGESTS")

    for skill_name in SKILL_NAMES:
        evaluation = records[f"skill_evaluation:{skill_name}"]
        requalification = records[f"skill_requalification:{skill_name}"]
        releases = records[f"skill_releases:{skill_name}"]
        rollback_probe = records[f"skill_rollback_probe:{skill_name}"]
        if not _record_valid(evaluation):
            failures.append(f"SKILL_EVALUATION_DIGEST:{skill_name}")
        if not _record_valid(requalification):
            failures.append(f"SKILL_REQUALIFICATION_DIGEST:{skill_name}")
        if not all(isinstance(event, dict) for event in releases) or not _chain_valid(releases):
            failures.append(f"SKILL_RELEASE_CHAIN:{skill_name}")
        if not all(isinstance(event, dict) for event in rollback_probe) or not _chain_valid(rollback_probe):
            failures.append(f"SKILL_ROLLBACK_CHAIN:{skill_name}")
    return failures


def _validate_file_bindings(
    paths: Mapping[str, tuple[Path, type[Any]]], records: Mapping[str, Any]
) -> list[str]:
    failures: list[str] = []
    closure_root = paths["closure_index"][0].parent.resolve()
    closure_index = records["closure_index"]
    entries = closure_index.get("entries", [])
    if not isinstance(entries, list):
        return ["CLOSURE_INDEX_ENTRIES"]
    index_entries = {
        item.get("path"): item for item in entries if isinstance(item, dict)
    }
    observed_paths = {
        path.relative_to(closure_root).as_posix()
        for path in closure_root.rglob("*")
        if path.is_file() and path != paths["closure_index"][0]
    }
    indexed_paths = {
        str(item.get("path")) for item in entries if isinstance(item, dict)
    }
    if not (
        indexed_paths == observed_paths
        and closure_index.get("entry_count") == len(observed_paths)
        and len(entries) == len(observed_paths)
    ):
        failures.append("CLOSURE_INDEX_CLOSURE")
    if closure_index.get("pack_digest") != sha256_digest(entries):
        failures.append("CLOSURE_INDEX_PACK_DIGEST")
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            failures.append("CLOSURE_INDEX_ENTRY")
            continue
        relative_path = item["path"]
        indexed_path = (closure_root / relative_path).resolve()
        try:
            indexed_path.relative_to(closure_root)
        except ValueError:
            failures.append(f"CLOSURE_INDEX_PATH:{relative_path}")
            continue
        if not (
            indexed_path.is_file()
            and item.get("sha256") == _file_sha256(indexed_path)
            and item.get("bytes") == indexed_path.stat().st_size
        ):
            failures.append(f"CLOSURE_INDEX_FILE_BINDING:{relative_path}")

    for name, relative_path in INDEXED_EVIDENCE_PATHS.items():
        entry = index_entries.get(relative_path)
        path = paths[name][0]
        if not isinstance(entry, dict) or entry.get("sha256") != _file_sha256(path):
            failures.append(f"CLOSURE_INDEX_BINDING:{name}")

    baseline_source = next(
        (
            item
            for item in records["quote_value"].get("source_evidence", [])
            if isinstance(item, dict) and item.get("id") == "current_process_baseline"
        ),
        None,
    )
    if not isinstance(baseline_source, dict) or baseline_source.get("file_sha256") != _file_sha256(
        paths["process_baseline"][0]
    ):
        failures.append("QUOTE_VALUE_BASELINE_BINDING")
    return failures


def _validate_semantics(records: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    candidate = records["candidate"]
    lifecycle = records["lifecycle"]
    governed = records["governed"]
    recovery = records["recovery"]
    public = records["public"]
    rollback = records["rollback"]
    operations = records["operations_summary"]
    skills_summary = records["skills_summary"]
    quote_value = records["quote_value"]
    process_baseline = records["process_baseline"]
    discovery = records["skill_discovery"]
    wheel_runtime = records["wheel_runtime"]
    deployment = records["deployment_profile"]
    deployment_retention = deployment.get("retention", {})
    sbom = records["sbom"]
    trace_keys = _attribute_keys(records["otlp_traces"])
    privacy_probe = records["privacy_probe"]
    privacy_rejection = records["privacy_rejection"]
    semifinal_mvp = records["semifinal_mvp"]
    run_id = candidate.get("run_id")

    if not (
        run_id == RETAINED_RUN_ID
        and candidate.get("status") == "PASS"
        and candidate.get("terminal_state") == "CANDIDATE_ACCEPTED"
        and candidate.get("canonical_target_writes") == 0
        and candidate.get("production_readiness") is False
    ):
        failures.append("CANDIDATE_SEMANTICS")

    actions = lifecycle.get("actions", [])
    bindings = lifecycle.get("bindings", [])
    decisions = lifecycle.get("control_decisions", [])
    action_sequences = [item.get("sequence") for item in actions if isinstance(item, dict)]
    action_keys = [item.get("key") for item in actions if isinstance(item, dict)]
    if not (
        lifecycle.get("run_id") == run_id
        and lifecycle.get("project_id") == "orgrebase-native-quote-001"
        and lifecycle.get("project_terminal_state") == "completed"
        and lifecycle.get("canonical_target_writes") == 0
        and len(actions) == 34
        and action_sequences == list(range(1, 35))
        and len(set(action_keys)) == 34
        and [(item.get("sequence"), item.get("ok")) for item in actions if not item.get("ok")]
        == [(28, False)]
        and len(bindings) == 6
        and len(decisions) == 7
    ):
        failures.append("AGENTTEAMS_LIFECYCLE")
    if not all(
        isinstance(binding, dict)
        and binding.get("run_id") == run_id
        and binding.get("project_id") == lifecycle.get("project_id")
        and binding.get("candidate_only") is True
        and binding.get("target_writes") == 0
        for binding in bindings
    ):
        failures.append("AGENTTEAMS_BINDINGS")
    rejected = [
        (decision.get("task_id"), decision.get("reason_codes"))
        for decision in decisions
        if isinstance(decision, dict) and decision.get("verdict") == "REJECT"
    ]
    if not (
        all(
            isinstance(decision, dict)
            and decision.get("run_id") == run_id
            and decision.get("candidate_only") is True
            and decision.get("target_writes") == 0
            for decision in decisions
        )
        and rejected
        == [
            ("orgrebase-native-quote-001-legal-a1", ["RESULT_DIGEST_MISMATCH"]),
            (
                "orgrebase-native-quote-001-product-reassign-a1",
                ["STALE_ATTEMPT_FENCED"],
            ),
        ]
    ):
        failures.append("AGENTTEAMS_CONTROL_DECISIONS")

    if not (
        governed.get("status") == "PASS"
        and governed.get("run_id") == run_id
        and governed.get("parent_candidate_summary_digest") == candidate.get("digest")
        and governed.get("parent_terminal_state") == candidate.get("terminal_state")
        and governed.get("terminal_state") == "GOVERNED_APPLIED"
        and governed.get("final_quote_ref") == "work:quote_acme@v3"
        and governed.get("canonical_target_writes") == 6
        and governed.get("proposal_plane_target_writes") == 0
        and governed.get("canonical_authority") == "STATESTORE_REBASE_WORKFLOW_ONLY"
        and governed.get("production_readiness") is False
    ):
        failures.append("GOVERNED_SEMANTICS")

    kill_receipts = recovery.get("kill_receipts", [])
    if not (
        recovery.get("run_id") == run_id
        and recovery.get("sigkill_count") == 2
        and recovery.get("real_process_restart_count") == 2
        and recovery.get("recovery_dispositions") == ["RETRY_INTENT", "ADOPT_COMMITTED"]
        and len(kill_receipts) == 2
        and [item.get("phase") for item in kill_receipts] == ["prepare", "launch"]
        and all(
            isinstance(item, dict)
            and _record_valid(item)
            and item.get("run_id") == run_id
            and item.get("signal") == "SIGKILL"
            and item.get("returncode") == -9
            for item in kill_receipts
        )
    ):
        failures.append("PROCESS_RECOVERY")

    if not (
        rollback.get("status") == "PASS"
        and rollback.get("run_id") == run_id
        and rollback.get("restoration_status") == "EXECUTED_AND_INVOKED"
        and rollback.get("target_writes") == 0
        and rollback.get("candidate_only") is True
    ):
        failures.append("SKILL_ROLLBACK")

    strategy = next(
        (
            item
            for item in public.get("strategies", [])
            if isinstance(item, dict) and item.get("strategy_id") == "OAC_TYPED_WITH_UNKNOWN"
        ),
        {},
    )
    metrics = strategy.get("metrics", {}) if isinstance(strategy, dict) else {}
    scope = metrics.get("scope_reduction_rate", {}) if isinstance(metrics, dict) else {}
    unsafe = metrics.get("unsafe_false_unaffected_rate", {}) if isinstance(metrics, dict) else {}
    if not (
        public.get("status") == "PASS"
        and public.get("claim_ceiling") == "MECHANISM_VALIDATION_ONLY"
        and scope.get("value") == 0.363636
        and unsafe.get("value") == 0.0
    ):
        failures.append("PUBLIC_MECHANISM")

    quote_metrics = {
        item.get("metric_id"): item for item in quote_value.get("metrics", []) if isinstance(item, dict)
    }
    expected_metric_ids = {
        "quote_cycle_elapsed_minutes",
        "policy_confirmation_active_minutes",
        "impact_exact_mismatch_case_rate",
        "first_pass_rework_rate",
        "unauthorized_access_success_rate",
        "hold_for_review_decision_rate",
        "full_rebuild_normalized_cost",
        "selective_rebase_normalized_cost",
        "selective_rebase_cost_saving_rate",
    }
    if not (
        process_baseline.get("primary_user") == "Enterprise Quote Operator"
        and process_baseline.get("status") == "NOT_RUN"
        and len(process_baseline.get("process_steps", [])) == 8
        and all(
            isinstance(step, dict)
            and len(step.get("responsible", [])) >= 1
            and len(step.get("to_be_responsible", [])) >= 1
            and isinstance(step.get("accountable"), str)
            and step.get("systems", {}).get("named_connector_status") == "NOT_RUN"
            and step.get("target_response", {}).get("basis") == "DESIGN_TARGET_NOT_OBSERVED_BASELINE"
            for step in process_baseline.get("process_steps", [])
        )
        and quote_value.get("verification_status") == "PASS"
        and quote_value.get("primary_user") == "Enterprise Quote Operator"
        and quote_value.get("evidence_ceiling") == "SYNTHETIC_CONTROLLED_VALUE_PROOF"
        and set(quote_metrics) == expected_metric_ids
        and quote_value.get("aggregate_cost", {}).get("full_rebuild") == 8.0
        and quote_value.get("aggregate_cost", {}).get("selective_rebase") == 4.05
        and quote_value.get("aggregate_cost", {}).get("saving_rate") == 0.49375
    ):
        failures.append("QUOTE_VALUE_SEMANTICS")

    completion_matrix = semifinal_mvp.get("completion_matrix", [])
    completion_totals = semifinal_mvp.get("completion_totals", {})
    validated_count = sum(
        isinstance(item, dict) and item.get("result") == "VALIDATED" for item in completion_matrix
    )
    not_run_count = sum(
        isinstance(item, dict) and item.get("result") == "NOT_RUN" for item in completion_matrix
    )
    if not (
        semifinal_mvp.get("status") == "PASS"
        and semifinal_mvp.get("production_ready") is False
        and len(completion_matrix) == 28
        and len({item.get("recommendation_id") for item in completion_matrix if isinstance(item, dict)}) == 28
        and validated_count == 19
        and not_run_count == 9
        and completion_totals
        == {
            "recommendation_count": 28,
            "validated_count": 19,
            "not_run_count": 9,
        }
        and all(
            isinstance(item, dict)
            and item.get("result") in {"VALIDATED", "NOT_RUN"}
            and isinstance(item.get("maturity"), str)
            and isinstance(item.get("claim_boundary"), str)
            and item.get("evidence_refs")
            and all(
                isinstance(ref, dict)
                and isinstance(ref.get("evidence_id"), str)
                and isinstance(ref.get("json_pointer"), str)
                for ref in item.get("evidence_refs", [])
            )
            for item in completion_matrix
        )
    ):
        failures.append("OFFICIAL_COMPLETION_MATRIX")

    receipt_bindings = {
        "alert": "alert_receipt_digest",
        "retention": "retention_receipt_digest",
        "query": "telemetry_query_receipt_digest",
        "capacity": "capacity_receipt_digest",
        "backup": "backup_restore_receipt_digest",
    }
    if not (
        operations.get("status") == "PASS"
        and operations.get("run_id") == run_id
        and operations.get("canonical_target_writes") == 0
        and operations.get("production_readiness") is False
        and all(
            operations.get(summary_key) == records[name].get("digest")
            for name, summary_key in receipt_bindings.items()
        )
    ):
        failures.append("OPERATIONS_BINDINGS")
    alert = records["alert"]
    negative_alert_probe = records["negative_alert_probe"]
    retention = records["retention"]
    query = records["query"]
    capacity = records["capacity"]
    backup = records["backup"]
    if not (
        alert.get("run_id") == run_id
        and alert.get("status") == "PASS"
        and alert.get("alerts") == []
        and set(alert.get("observed_layers", [])) == set(alert.get("required_layers", []))
        and negative_alert_probe.get("run_id") == f"{run_id}:negative-alert-probe"
        and negative_alert_probe.get("status") == "ALERT"
        and negative_alert_probe.get("observed_layers") == ["TOOL"]
        and len(negative_alert_probe.get("alerts", [])) == 5
        and all(
            isinstance(item, dict) and item.get("severity") == "CRITICAL"
            for item in negative_alert_probe.get("alerts", [])
        )
        and retention.get("canonical_business_evidence_deleted") is False
        and query.get("query", {}).get("run_id") == run_id
        and query.get("count") == 3
        and capacity.get("successes") == capacity.get("sample_count") == 25
        and capacity.get("failures") == 0
        and capacity.get("production_ready_claimed") is False
        and capacity.get("sla_met_claimed") is False
        and backup.get("status") == "PASS"
        and backup.get("source_digest") == backup.get("restored_digest")
        and backup.get("source_event_head") == backup.get("restored_event_head")
        and backup.get("production_sla_claimed") is False
    ):
        failures.append("OPERATIONS_SEMANTICS")

    required_trace_keys = {
        "deployment.environment.name",
        "service.name",
        "orgrebase.workflow.run_id",
        "orgrebase.agentteams.task.id",
        "orgrebase.skill.name",
        "orgrebase.tool.receipt.digest",
        "orgrebase.receipt.digest",
        "orgrebase.target.write.count",
    }
    if not (
        deployment.get("evidence_class") == "OBSERVED_CONTROLLED_LOCAL"
        and deployment.get("production_ready") is False
        and set(deployment.get("external_connectors", {}).values()) == {"NOT_RUN"}
        and deployment.get("recovery", {}).get("geographic_failover") == "NOT_RUN"
        and deployment.get("capacity", {}).get("sample_count") == 25
        and isinstance(deployment_retention.get("indexed_telemetry_seconds"), int)
        and deployment_retention.get("indexed_telemetry_seconds", 0) > 0
        and isinstance(
            deployment_retention.get("rejected_payload_diagnostic_seconds"),
            int,
        )
        and deployment_retention.get("rejected_payload_diagnostic_seconds", 0) > 0
        and deployment_retention.get("canonical_business_evidence_affected") is False
        and deployment_retention.get("rejected_raw_payload_persisted") is False
        and sbom.get("bomFormat") == "CycloneDX"
        and sbom.get("metadata", {}).get("component", {}).get("version") == "0.4.0"
        and len(sbom.get("components", [])) == 33
        and required_trace_keys <= trace_keys
    ):
        failures.append("ENTERPRISE_ENGINEERING_SEMANTICS")

    if not (
        privacy_probe.get("status") == "PASS"
        and privacy_probe.get("exception_class") == "IntegrityError"
        and privacy_probe.get("reason_code") == "PRIVACY_PAYLOAD_REJECTED"
        and privacy_probe.get("raw_payload_retained") is False
        and privacy_rejection.get("run_id") == f"{run_id}:privacy-probe"
        and privacy_rejection.get("signal") == "traces"
        and privacy_rejection.get("reason_code") == "OTLP_RESTRICTED_FIELD:secret"
        and privacy_rejection.get("raw_payload_retained") is False
    ):
        failures.append("PRIVACY_REJECTION_SEMANTICS")

    source = records["source_receipt"]
    tool = records["tool_receipt"]
    invocation = records["skill_invocation"]
    exports = records["otlp_exports"]
    if not (
        source.get("run_id") == run_id
        and source.get("connector_kind") == "SOURCE"
        and source.get("status") == "SUCCEEDED"
        and source.get("target_writes") == 0
        and source.get("digest") == candidate.get("source_receipt_digest")
        and tool.get("run_id") == run_id
        and tool.get("connector_kind") == "TOOL"
        and tool.get("status") == "SUCCEEDED"
        and tool.get("target_writes") == 0
        and tool.get("digest") == candidate.get("tool_receipt_digest")
        and invocation.get("run_id") == run_id
        and invocation.get("outcome") == "SUCCESS"
        and invocation.get("candidate_only") is True
        and invocation.get("target_writes") == 0
        and invocation.get("digest") == candidate.get("skill_invocation_receipt_digest")
        and len(exports) == 3
        and {item.get("operation") for item in exports} == {"EXPORT_TRACES", "EXPORT_LOGS", "EXPORT_METRICS"}
        and all(
            item.get("run_id") == run_id
            and item.get("connector_kind") == "OTLP"
            and item.get("status") == "SUCCEEDED"
            and item.get("http_status") == 200
            and item.get("target_writes") == 0
            for item in exports
        )
        and set(query.get("matched_payload_digests", [])) == {item.get("request_digest") for item in exports}
    ):
        failures.append("EVIDENCE_SPINE")

    expected_release_transitions = [
        ("DRAFT", "EVALUATED"),
        ("EVALUATED", "SHADOW"),
        ("SHADOW", "CANARY"),
        ("CANARY", "REQUALIFICATION_REQUIRED"),
        ("REQUALIFICATION_REQUIRED", "EVALUATED"),
        ("EVALUATED", "SHADOW"),
        ("SHADOW", "CANARY"),
    ]
    expected_rollback_transitions = [
        ("DRAFT", "EVALUATED"),
        ("EVALUATED", "SHADOW"),
        ("SHADOW", "CANARY"),
        ("CANARY", "REQUALIFICATION_REQUIRED"),
        ("REQUALIFICATION_REQUIRED", "ROLLBACK_DECISION_RECORDED"),
    ]
    summary_maps = (
        "packages",
        "evaluation_receipts",
        "release_heads",
        "requalification_evaluation_receipts",
        "rollback_decision_receipts",
    )
    if not (
        skills_summary.get("status") == "PASS"
        and skills_summary.get("run_id") == run_id
        and skills_summary.get("target_writes") == 0
        and all(set(skills_summary.get(name, {})) == set(SKILL_NAMES) for name in summary_maps)
    ):
        failures.append("SKILL_SUMMARY")
    discovery_by_name = {item.get("name"): item for item in discovery if isinstance(item, dict)}
    if not (
        set(discovery_by_name) == set(SKILL_NAMES)
        and wheel_runtime.get("resource_mode") == "INSTALLED_WHEEL"
        and wheel_runtime.get("packages_discovered") == 3
        and all(
            discovery_by_name[name].get("resource_mode") == "INSTALLED_WHEEL"
            and discovery_by_name[name].get("package_digest") == skills_summary.get("packages", {}).get(name)
            for name in SKILL_NAMES
        )
    ):
        failures.append("SKILL_DISCOVERY")
    for skill_name in SKILL_NAMES:
        evaluation = records[f"skill_evaluation:{skill_name}"]
        requalification = records[f"skill_requalification:{skill_name}"]
        releases = records[f"skill_releases:{skill_name}"]
        rollback_probe = records[f"skill_rollback_probe:{skill_name}"]
        release_transitions = [(event.get("from_state"), event.get("to_state")) for event in releases]
        rollback_transitions = [(event.get("from_state"), event.get("to_state")) for event in rollback_probe]
        if not (
            evaluation.get("verdict") == "CANARY"
            and len(evaluation.get("case_results", [])) == 8
            and all(item.get("passed") is True for item in evaluation.get("case_results", []))
            and all(item.get("passed") is True for item in evaluation.get("gate_results", []))
            and evaluation.get("digest") == skills_summary.get("evaluation_receipts", {}).get(skill_name)
            and requalification.get("verdict") == "CANARY"
            and len(requalification.get("case_results", [])) == 8
            and all(item.get("passed") is True for item in requalification.get("case_results", []))
            and all(item.get("passed") is True for item in requalification.get("gate_results", []))
            and requalification.get("digest")
            == skills_summary.get("requalification_evaluation_receipts", {}).get(skill_name)
            and release_transitions == expected_release_transitions
            and releases[-1].get("digest") == skills_summary.get("release_heads", {}).get(skill_name)
            and rollback_transitions == expected_rollback_transitions
            and rollback_probe[-1].get("digest")
            == skills_summary.get("rollback_decision_receipts", {}).get(skill_name)
            and rollback_probe[-1].get("restoration_status") == "NOT_RUN"
        ):
            failures.append(f"SKILL_LIFECYCLE:{skill_name}")
    return failures


def _retained_semifinal_evidence_view(project_root: Path) -> dict[str, Any]:
    """Project a visualization-ready, read-only view of retained evidence."""

    paths = _paths(project_root)
    missing = sorted(name for name, (path, _) in paths.items() if not path.is_file())
    if missing:
        result = _base(
            "UNAVAILABLE",
            "RETAINED_CHECKOUT_EVIDENCE_NOT_AVAILABLE;NO_EVIDENCE_PROJECTION",
        )
        result["missing"] = missing
        return result
    try:
        records = {name: _load(path, expected) for name, (path, expected) in paths.items()}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _fail_closed([type(exc).__name__])

    failures = _validate_records(records)
    if not failures:
        failures.extend(_validate_file_bindings(paths, records))
    if not failures:
        failures.extend(_validate_semantics(records))
    if failures:
        return _fail_closed(failures)

    candidate = records["candidate"]
    lifecycle = records["lifecycle"]
    governed = records["governed"]
    recovery = records["recovery"]
    rollback = records["rollback"]
    operations = records["operations_summary"]
    alert = records["alert"]
    negative_alert_probe = records["negative_alert_probe"]
    retention = records["retention"]
    query = records["query"]
    capacity = records["capacity"]
    backup = records["backup"]
    source = records["source_receipt"]
    tool = records["tool_receipt"]
    invocation = records["skill_invocation"]
    exports = records["otlp_exports"]
    skills_summary = records["skills_summary"]
    quote_value = records["quote_value"]
    process_baseline = records["process_baseline"]
    compensation = _compensation_mechanisms_view(project_root)
    discovery_by_name = {item["name"]: item for item in records["skill_discovery"]}
    wheel_runtime = records["wheel_runtime"]
    deployment = records["deployment_profile"]
    deployment_retention = deployment.get("retention", {})
    sbom = records["sbom"]
    trace_keys = _attribute_keys(records["otlp_traces"])
    closure_index = records["closure_index"]
    privacy_probe = records["privacy_probe"]
    privacy_rejection = records["privacy_rejection"]
    formation_taskflow = _formation_taskflow_view(project_root)
    run_id = candidate["run_id"]
    actions = [
        {
            "sequence": item["sequence"],
            "key": item["key"],
            "tool": item["tool"],
            "action": item["action"],
            "status": item["status"],
            "ok": item["ok"],
            "digest": item["digest"],
        }
        for item in lifecycle["actions"]
    ]
    bindings = [
        {
            "task_id": item["task_id"],
            "domain": item["domain"],
            "assignee": item["assignee"],
            "attempt": item["attempt"],
            "status": item["status"],
            "candidate_only": item["candidate_only"],
            "target_writes": item["target_writes"],
            "predecessor_task_id": item["predecessor_task_id"],
            "control_decision_ref": item["control_decision_ref"],
            "context_projection_digest": item["context_projection_digest"],
        }
        for item in lifecycle["bindings"]
    ]
    control_exceptions = [
        {
            "task_id": item["task_id"],
            "attempt": item["attempt"],
            "verdict": item["verdict"],
            "reason_codes": item["reason_codes"],
        }
        for item in lifecycle["control_decisions"]
        if item["verdict"] == "REJECT"
    ]

    skill_views = []
    for skill_name in SKILL_NAMES:
        evaluation = records[f"skill_evaluation:{skill_name}"]
        requalification = records[f"skill_requalification:{skill_name}"]
        releases = records[f"skill_releases:{skill_name}"]
        rollback_probe = records[f"skill_rollback_probe:{skill_name}"]
        discovery = discovery_by_name[skill_name]
        skill_views.append(
            {
                "name": skill_name,
                "version": discovery["version"],
                "package_id": discovery["package_id"],
                "entry_point": discovery["entry_point"],
                "resource_mode": discovery["resource_mode"],
                "discovered": True,
                "package_digest": skills_summary["packages"][skill_name],
                "verdict": evaluation["verdict"],
                "evaluation_receipt_digest": evaluation["digest"],
                "case_count": len(evaluation["case_results"]),
                "evaluation_partitions": [item["partition"] for item in evaluation["case_results"]],
                "gates_passed": sum(item["passed"] is True for item in evaluation["gate_results"]),
                "gate_count": len(evaluation["gate_results"]),
                "lifecycle": [item["to_state"] for item in releases],
                "release_head": releases[-1]["digest"],
                "requalification_verdict": requalification["verdict"],
                "requalification_receipt_digest": skills_summary["requalification_evaluation_receipts"][
                    skill_name
                ],
                "rollback_decision_receipt_digest": rollback_probe[-1]["digest"],
                "rollback_state": rollback_probe[-1]["to_state"],
                "rollback_mode": (
                    "EXECUTED_AND_INVOKED" if skill_name == "enterprise-quote-compose" else "DECISION_ONLY"
                ),
                "invocation": (
                    {
                        "status": invocation["outcome"],
                        "receipt_digest": invocation["digest"],
                        "installed_wheel_receipt_digest": wheel_runtime["digest"],
                    }
                    if skill_name == "enterprise-quote-compose"
                    else {"status": "NOT_INVOKED_IN_RETAINED_CHAIN"}
                ),
                "target_writes": 0,
            }
        )

    quote_metrics = {item["metric_id"]: item for item in quote_value["metrics"]}

    def projected_metric(metric_id: str) -> dict[str, Any]:
        metric = quote_metrics[metric_id]
        evidence_class = metric["evidence_class"]
        maturity = (
            "NOT_RUN"
            if evidence_class == "NOT_RUN"
            else "MODELLED"
            if evidence_class == "MODELLED_COUNTERFACTUAL"
            else "VALIDATED_PROXY"
        )
        return {
            "metric_id": metric_id,
            "status": metric["status"],
            "maturity": maturity,
            "evidence_class": evidence_class,
            "value": metric["value"],
            "numerator": metric["numerator"],
            "denominator": metric["denominator"],
            "unit": metric["unit"],
            "limitations": metric["limitations"],
        }

    value_metrics = [
        projected_metric(metric_id)
        for metric_id in (
            "quote_cycle_elapsed_minutes",
            "policy_confirmation_active_minutes",
            "impact_exact_mismatch_case_rate",
            "first_pass_rework_rate",
            "unauthorized_access_success_rate",
            "hold_for_review_decision_rate",
        )
    ]
    saving_metric = projected_metric("selective_rebase_cost_saving_rate")
    saving_metric["full_rebuild_normalized_cost"] = quote_value["aggregate_cost"]["full_rebuild"]
    saving_metric["selective_rebase_normalized_cost"] = quote_value["aggregate_cost"]["selective_rebase"]
    value_metrics.append(saving_metric)

    result = _base(
        "PASS",
        (
            "CONTROLLED_LOCAL_RETAINED_SPEC_053_RUN;EACH_EVIDENCE_LANE_IS_"
            "SEPARATELY_RUN_SCOPED;NOT_CURRENT_PACK_PILOT;NOT_REAL_ENTERPRISE_"
            "OR_PRODUCTION"
        ),
    )
    result.update(
        {
            "completion_status": "CONTROLLED_LOCAL_MVP_WITH_DECLARED_NOT_RUN",
            "mechanism_comparison": mechanism_comparison_view(project_root, quote_value),
            "run_id": run_id,
            "archive_status": "FROZEN_HISTORICAL_VALIDATION",
            "archive_snapshot_at": None,
            "archive_time_basis": "CONTENT_ADDRESSED_EVIDENCE_NO_TRUSTED_COMPLETION_TIME",
            "archive_identity_digest": closure_index["pack_digest"],
            "current_task_run": False,
            "evidence_index": {
                "schema_version": closure_index["schema_version"],
                "status": "PASS",
                "archive_status": "FROZEN_HISTORICAL_VALIDATION",
                "run_id": run_id,
                "current_task_run": False,
                "entry_count": closure_index["entry_count"],
                "index_digest": closure_index["digest"],
                "pack_digest": closure_index["pack_digest"],
                "evidence_classes": sorted(
                    {
                        source["evidence_class"],
                        lifecycle["evidence_class"],
                        tool["evidence_class"],
                        "CONTROLLED_LOCAL_INSTALLED_WHEEL",
                        exports[0]["evidence_class"],
                        candidate["evidence_class"],
                        governed["evidence_class"],
                    }
                ),
                "verifier_status": "PASS",
                "privacy": {
                    "canary_status": privacy_probe["status"],
                    "canary_reason_code": privacy_probe["reason_code"],
                    "rejection_reason_code": privacy_rejection["reason_code"],
                    "raw_payload_retained": False,
                    "sensitive_values_disclosed": False,
                },
                "target_writes": 0,
            },
            "agent_collaboration": {
                "formation_taskflow": formation_taskflow,
                "project_id": lifecycle["project_id"],
                "terminal_state": lifecycle["project_terminal_state"],
                "action_count": len(actions),
                "binding_count": len(bindings),
                "actions": actions,
                "bindings": bindings,
                "control_exceptions": control_exceptions,
                "lifecycle": [
                    {"stage": "CREATE", "evidence": "create_project"},
                    {"stage": "DELEGATE", "evidence": "delegate_task"},
                    {"stage": "ACK", "evidence": "ack_task"},
                    {
                        "stage": "HANDOFF_BINDING",
                        "evidence": "context_projection_digest",
                    },
                    {"stage": "SUBMIT", "evidence": "submit_task"},
                    {"stage": "CHECK", "evidence": "check_task"},
                    {"stage": "ACCEPT", "evidence": "accept_task_result"},
                    {"stage": "COMPLETE", "evidence": "complete_project"},
                ],
                "authority": {
                    "agentteams_task": "SCHEDULING_AND_CANDIDATE_FACTS_ONLY",
                    "canonical_state": governed["canonical_authority"],
                    "candidate_target_writes": 0,
                },
                "exception_semantics": {
                    "result_conflict": "REJECTED",
                    "reassignment": "EXECUTED",
                    "late_result_fencing": "FENCED",
                    "canonical_transaction_atomic_rollback": compensation.get(
                        "canonical_transaction", "NOT_OBSERVED"
                    ),
                    "downstream_state_compensation": compensation.get(
                        "downstream_state_compensation", "NOT_OBSERVED"
                    ),
                    "reversible_external_git_compensation": compensation.get(
                        "reversible_external_git", "NOT_OBSERVED"
                    ),
                    "real_enterprise_connector_compensation": compensation.get(
                        "real_enterprise_connector", "NOT_RUN"
                    ),
                },
                "compensation_evidence": compensation,
                "run_separation": {
                    "active_pack_pilot": "DIFFERENT_RUN",
                    "retained_governed_successor": "SAME_RETAINED_RUN",
                },
                "governed_successor": {
                    "terminal_state": governed["terminal_state"],
                    "final_quote_ref": governed["final_quote_ref"],
                    "approval_input_mode": governed["approval_input_mode"],
                    "canonical_target_writes": governed["canonical_target_writes"],
                },
            },
            "value_and_responsibility": {
                "primary_user": process_baseline["primary_user"],
                "primary_deliverable": process_baseline["primary_deliverable"],
                "process_status": process_baseline["status"],
                "process_steps": [
                    {
                        "id": step["id"],
                        "label": step["label"],
                        "as_is_interface_class": step["systems"]["as_is_interface_class"],
                        "named_connector_status": step["systems"]["named_connector_status"],
                        "responsible": step["responsible"],
                        "to_be_responsible": step["to_be_responsible"],
                        "accountable": step["accountable"],
                        "target_response": step["target_response"],
                    }
                    for step in process_baseline["process_steps"]
                ],
                "metrics": value_metrics,
                "cost_model": {
                    "full_rebuild": quote_value["aggregate_cost"]["full_rebuild"],
                    "selective_rebase": quote_value["aggregate_cost"]["selective_rebase"],
                    "saving_rate": quote_value["aggregate_cost"]["saving_rate"],
                    "unit": quote_value["cost_model"]["unit"],
                    "evidence_class": "MODELLED_COUNTERFACTUAL",
                    "enterprise_roi": "NOT_RUN",
                    "declared_scenario_envelope": quote_value["sensitivity"][
                        "declared_review_burden_envelope"
                    ],
                    "stress_envelope": quote_value["sensitivity"]["stress_envelope"],
                    "receipt_digest": quote_value["digest"],
                },
                "claim_ceiling": quote_value["evidence_ceiling"],
            },
            "evidence_spine": [
                {
                    "order": 1,
                    "stage": "SOURCE",
                    "status": source["status"],
                    "evidence_class": source["evidence_class"],
                    "receipt_digest": source["digest"],
                    "target_writes": source["target_writes"],
                },
                {
                    "order": 2,
                    "stage": "AGENTTEAMS",
                    "status": lifecycle["project_terminal_state"],
                    "evidence_class": lifecycle["evidence_class"],
                    "receipt_digest": lifecycle["receipt_digest"],
                    "target_writes": lifecycle["canonical_target_writes"],
                },
                {
                    "order": 3,
                    "stage": "TOOL",
                    "status": tool["status"],
                    "evidence_class": tool["evidence_class"],
                    "receipt_digest": tool["digest"],
                    "target_writes": tool["target_writes"],
                },
                {
                    "order": 4,
                    "stage": "SKILL",
                    "status": invocation["outcome"],
                    "evidence_class": "CONTROLLED_LOCAL_INSTALLED_WHEEL",
                    "receipt_digest": invocation["digest"],
                    "target_writes": invocation["target_writes"],
                },
                {
                    "order": 5,
                    "stage": "OTLP",
                    "status": "3_SIGNALS_EXPORTED_AND_QUERIED",
                    "evidence_class": exports[0]["evidence_class"],
                    "receipt_digest": query["digest"],
                    "receipt_digests": [item["digest"] for item in exports],
                    "signals": ["traces", "logs", "metrics"],
                    "target_writes": 0,
                },
                {
                    "order": 6,
                    "stage": "CANDIDATE",
                    "status": candidate["terminal_state"],
                    "evidence_class": candidate["evidence_class"],
                    "receipt_digest": candidate["digest"],
                    "target_writes": candidate["canonical_target_writes"],
                },
                {
                    "order": 7,
                    "stage": "GOVERNED_APPLY",
                    "status": governed["terminal_state"],
                    "evidence_class": governed["evidence_class"],
                    "receipt_digest": governed["digest"],
                    "target_writes": governed["canonical_target_writes"],
                    "authority": governed["canonical_authority"],
                },
            ],
            "operations": {
                "connectors": {
                    "source": {
                        "status": source["status"],
                        "protocol": source["endpoint_class"],
                        "evidence_class": source["evidence_class"],
                        "http_status": source["http_status"],
                        "receipt_digest": source["digest"],
                    },
                    "tool": {
                        "status": tool["status"],
                        "protocol": tool["endpoint_class"],
                        "evidence_class": tool["evidence_class"],
                        "http_status": tool["http_status"],
                        "receipt_digest": tool["digest"],
                    },
                    "enterprise": deployment["external_connectors"],
                },
                "otlp": {
                    "signals": ["traces", "logs", "metrics"],
                    "query_status": "PASS",
                    "key_fields": sorted(
                        key
                        for key in trace_keys
                        if key
                        in {
                            "deployment.environment.name",
                            "service.name",
                            "service.version",
                            "orgrebase.workflow.run_id",
                            "orgrebase.agentteams.task.id",
                            "orgrebase.skill.name",
                            "orgrebase.tool.receipt.digest",
                            "orgrebase.receipt.digest",
                            "orgrebase.target.write.count",
                        }
                    ),
                },
                "enterprise_readiness": {
                    "deployment_profile": deployment["profile_id"],
                    "deployment_evidence_class": deployment["evidence_class"],
                    "production_ready": deployment["production_ready"],
                    "contractual_sla": deployment["capacity"]["contractual_sla"],
                    "geographic_failover": deployment["recovery"]["geographic_failover"],
                    "sbom_format": sbom["bomFormat"],
                    "artifact_version": sbom["metadata"]["component"]["version"],
                    "sbom_component_count": len(sbom["components"]),
                    "contributing_guide": (
                        "PRESENT_IN_REPOSITORY" if (project_root / "CONTRIBUTING.md").is_file() else "NOT_RUN"
                    ),
                },
                "alert": {
                    "status": alert["status"],
                    "alerts": len(alert["alerts"]),
                    "observed_layers": alert["observed_layers"],
                    "receipt_digest": alert["digest"],
                    "negative_probe": {
                        "status": negative_alert_probe["status"],
                        "alerts": len(negative_alert_probe["alerts"]),
                        "severity": "CRITICAL",
                        "run_id": negative_alert_probe["run_id"],
                        "receipt_digest": negative_alert_probe["digest"],
                    },
                },
                "retention": {
                    "retained_count": retention["retained_count"],
                    "deleted_count": len(retention["deleted_ingestion_ids"]),
                    "retained_rejection_count": retention["retained_rejection_count"],
                    "deleted_rejection_count": len(retention["deleted_rejection_ids"]),
                    "indexed_telemetry_seconds": deployment_retention["indexed_telemetry_seconds"],
                    "rejected_payload_diagnostic_seconds": deployment_retention[
                        "rejected_payload_diagnostic_seconds"
                    ],
                    "rejected_raw_payload_persisted": deployment_retention["rejected_raw_payload_persisted"],
                    "canonical_business_evidence_affected": deployment_retention[
                        "canonical_business_evidence_affected"
                    ],
                    "canonical_business_evidence_deleted": retention["canonical_business_evidence_deleted"],
                    "receipt_digest": retention["digest"],
                },
                "query": {
                    "count": query["count"],
                    "run_id": query["query"]["run_id"],
                    "receipt_digest": query["digest"],
                },
                "capacity": {
                    "successes": capacity["successes"],
                    "failures": capacity["failures"],
                    "p50_ms": capacity["latency_p50_ms"],
                    "p95_ms": capacity["latency_p95_ms"],
                    "production_ready_claimed": capacity["production_ready_claimed"],
                    "sla_met_claimed": capacity["sla_met_claimed"],
                    "receipt_digest": capacity["digest"],
                },
                "backup": {
                    "status": backup["status"],
                    "artifact_count": backup["artifact_count"],
                    "digest_match": backup["source_digest"] == backup["restored_digest"],
                    "production_sla_claimed": backup["production_sla_claimed"],
                    "receipt_digest": backup["digest"],
                },
            },
            "skills": skill_views,
            "recovery": {
                "sigkill_count": recovery["sigkill_count"],
                "real_process_restart_count": recovery["real_process_restart_count"],
                "dispositions": recovery["recovery_dispositions"],
                "events": [
                    {
                        "phase": item["phase"],
                        "signal": item["signal"],
                        "returncode": item["returncode"],
                        "disposition": recovery["recovery_dispositions"][index],
                        "receipt_digest": item["digest"],
                    }
                    for index, item in enumerate(recovery["kill_receipts"])
                ],
            },
            "exceptions": {
                "agentteams": control_exceptions,
                "not_run": [
                    "EXTERNAL_HUMAN_VALIDATION",
                    "REAL_ENTERPRISE_CONNECTOR_AND_VALUE",
                    "LIVE_DISTRIBUTED_AGENTTEAMS",
                    "PRODUCTION_HA_DR_SLA_MULTITENANCY",
                    "CROSS_ENTERPRISE_GENERALIZATION",
                    "STATISTICALLY_MEANINGFUL_EXTERNAL_SKILL_EVALUATION",
                    "TRAJECTORY_INDUCTION",
                ],
                "boundaries": [
                    "SPEC_053_RETAINED_MECHANISM_NOT_SPEC_054_PACK_PILOT",
                    "SCRIPTED_LOCAL_APPROVAL_NOT_EXTERNAL_HUMAN_VALIDATION",
                    "CONTROLLED_LOCAL_CAPACITY_AND_BACKUP_NOT_PRODUCTION_SLA_OR_DR",
                ],
            },
            # Backward-compatible compact summaries consumed by the existing console.
            "candidate_chain": {
                "terminal_state": candidate["terminal_state"],
                "native_agentteams_actions": len(actions),
                "task_bindings": len(bindings),
                "domain_agents": candidate["coalition_member_count"],
                "proposal_plane_target_writes": candidate["canonical_target_writes"],
            },
            "governed_apply": {
                "terminal_state": governed["terminal_state"],
                "final_quote_ref": governed["final_quote_ref"],
                "canonical_target_writes": governed["canonical_target_writes"],
                "approval_input_mode": governed["approval_input_mode"],
                "canonical_authority": governed["canonical_authority"],
            },
            "skill_rollback": {
                "from_package_digest": rollback["from_package_digest"],
                "effective_package_digest": rollback["effective_package_digest"],
                "restoration_status": rollback["restoration_status"],
                "target_writes": rollback["target_writes"],
            },
            "not_run": [
                "EXTERNAL_HUMAN_VALIDATION",
                "REAL_ENTERPRISE_CONNECTOR_AND_VALUE",
                "LIVE_DISTRIBUTED_AGENTTEAMS",
                "PRODUCTION_HA_DR_SLA_MULTITENANCY",
            ],
            "operations_summary_digest": operations["digest"],
        }
    )
    return result


def semifinal_evidence_view(project_root: Path) -> dict[str, Any]:
    from orgrebase.archive_revalidation import archive_revalidation_view

    retained = _retained_semifinal_evidence_view(project_root)
    return {**retained, "archive_revalidation": archive_revalidation_view(project_root)}
