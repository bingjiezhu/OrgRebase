#!/usr/bin/env python3
"""Build one content-addressed, claim-bounded semifinal MVP evidence manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_RUN_ID = "run:orgrebase:semifinal-closure:quote-001"


class ManifestBuildError(RuntimeError):
    """Raised when a source pack cannot support the declared manifest."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ManifestBuildError(code)


def _record_ok(value: Any) -> bool:
    return isinstance(value, dict) and value.get("digest") == _digest(
        {key: item for key, item in value.items() if key != "digest"}
    )


def _display_path(path: Path, checkout_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(checkout_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _verify_indexed_pack(
    *,
    evidence_id: str,
    root: Path,
    checkout_root: Path,
    maturity: str,
    expected_class: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    summary_path = root / "summary.json"
    index_path = root / "evidence-index.json"
    _require(summary_path.is_file() and index_path.is_file(), f"{evidence_id}:FILES")
    summary = _load(summary_path)
    index = _load(index_path)
    _require(_record_ok(summary), f"{evidence_id}:SUMMARY_DIGEST")
    _require(_record_ok(index), f"{evidence_id}:INDEX_DIGEST")
    _require(summary.get("status") == "PASS", f"{evidence_id}:STATUS")
    _require(summary.get("evidence_class") == expected_class, f"{evidence_id}:CLASS")

    expected_entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != index_path
    ]
    _require(index.get("entries") == expected_entries, f"{evidence_id}:INDEX_ENTRIES")
    _require(index.get("entry_count") == len(expected_entries), f"{evidence_id}:INDEX_COUNT")
    _require(index.get("pack_digest") == _digest(expected_entries), f"{evidence_id}:PACK_DIGEST")

    descriptor = {
        "evidence_id": evidence_id,
        "source_type": "INDEXED_EVIDENCE_PACK",
        "path": _display_path(root, checkout_root),
        "status": "PASS",
        "maturity": maturity,
        "claim_boundary": summary["claim_boundary"],
        "run_id": summary["run_id"],
        "evidence_class": summary["evidence_class"],
        "content_address": {
            "summary_file_sha256": _file_digest(summary_path),
            "summary_digest": summary["digest"],
            "index_file_sha256": _file_digest(index_path),
            "index_digest": index["digest"],
            "pack_digest": index["pack_digest"],
            "entry_count": index["entry_count"],
        },
    }
    return descriptor, summary


def _public_descriptor(
    receipt_path: Path,
    *,
    checkout_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _load(receipt_path)
    _require(_record_ok(receipt), "public-process:RECEIPT_DIGEST")
    _require(receipt.get("status") == "PASS", "public-process:STATUS")
    _require(
        receipt.get("evidence_class") == "PUBLIC_SOURCE_DERIVED_SYNTHETIC"
        and receipt.get("claim_ceiling") == "MECHANISM_VALIDATION_ONLY"
        and receipt.get("claim_boundary")
        == "NOT_OBSERVED_ENTERPRISE_SHADOW_NOT_REALIZED_ROI_NOT_PRODUCTION_SLA",
        "public-process:CLAIM_BOUNDARY",
    )
    strategy = next(
        (
            item
            for item in receipt.get("strategies", [])
            if item.get("strategy_id") == "OAC_TYPED_WITH_UNKNOWN"
        ),
        None,
    )
    _require(isinstance(strategy, dict), "public-process:OAC_STRATEGY")
    metrics = strategy["metrics"]
    _require(
        metrics["precision"]["value"] == 1.0
        and metrics["recall"]["value"] == 1.0
        and metrics["scope_reduction_rate"]["value"] == 0.363636
        and metrics["unsafe_false_unaffected_rate"]["value"] == 0.0,
        "public-process:MECHANISM_METRICS",
    )
    _require(
        all(item.get("status") == "NOT_RUN" for item in receipt["enterprise_value_metrics"]),
        "public-process:ENTERPRISE_METRIC_BOUNDARY",
    )

    input_closure = receipt["input_closure"]
    benchmark_root = checkout_root / input_closure["benchmark_root"]
    raw_files = {
        relative: benchmark_root / relative
        for relative in input_closure["manifest_file_sha256"]
    }
    mapping_path = checkout_root / input_closure["mapping_path"]
    for relative, path in raw_files.items():
        _require(
            path.is_file()
            and _file_digest(path).removeprefix("sha256:")
            == input_closure["manifest_file_sha256"][relative],
            f"public-process:INPUT_FILE:{relative}",
        )
    _require(mapping_path.is_file(), "public-process:MAPPING_FILE")
    projection = _load(benchmark_root / "projection/object-centric-projection.json")
    overlay = _load(benchmark_root / "overlay/policy-change-ground-truth.json")
    dataset_manifest = _load(benchmark_root / "dataset-manifest.json")
    license_manifest = _load(benchmark_root / "LICENSES.json")
    mapping = _load(mapping_path)
    _require(
        _record_ok(projection)
        and projection["digest"] == input_closure["projection_digest"]
        and _record_ok(overlay)
        and overlay["digest"] == input_closure["overlay_digest"]
        and _record_ok(dataset_manifest)
        and dataset_manifest["digest"] == input_closure["dataset_manifest_digest"]
        and _record_ok(license_manifest)
        and license_manifest["digest"] == input_closure["license_manifest_digest"]
        and _record_ok(mapping)
        and mapping["digest"] == input_closure["mapping_digest"],
        "public-process:INPUT_RECORD_BINDINGS",
    )
    input_files = {
        _display_path(path, checkout_root): _file_digest(path)
        for path in sorted([*raw_files.values(), mapping_path])
    }
    descriptor = {
        "evidence_id": "public-process",
        "source_type": "PUBLIC_MECHANISM_BENCHMARK_RECEIPT",
        "path": _display_path(receipt_path, checkout_root),
        "status": "PASS",
        "maturity": "PUBLIC_SOURCE_DERIVED_SYNTHETIC_MECHANISM_VALIDATION",
        "claim_boundary": receipt["claim_boundary"],
        "run_id": receipt["run_id"],
        "evidence_class": receipt["evidence_class"],
        "content_address": {
            "receipt_file_sha256": _file_digest(receipt_path),
            "receipt_digest": receipt["digest"],
            "input_file_sha256": input_files,
        },
    }
    return descriptor, receipt


def _evidence_ref(evidence_id: str, json_pointer: str) -> dict[str, str]:
    return {"evidence_id": evidence_id, "json_pointer": json_pointer}


def _matrix(
    *,
    quote_value: dict[str, Any],
    public_receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    metric_by_id = {item["metric_id"]: item for item in quote_value["metrics"]}
    oac_strategy = next(
        item
        for item in public_receipt["strategies"]
        if item["strategy_id"] == "OAC_TYPED_WITH_UNKNOWN"
    )

    def row(
        recommendation_id: str,
        recommendation: str,
        result: str,
        maturity: str,
        claim_boundary: str,
        *refs: tuple[str, str],
    ) -> dict[str, Any]:
        return {
            "recommendation_id": recommendation_id,
            "recommendation": recommendation,
            "result": result,
            "maturity": maturity,
            "claim_boundary": claim_boundary,
            "evidence_refs": [_evidence_ref(*ref) for ref in refs],
        }

    return [
        row(
            "SCENE_PRIMARY_USER",
            "Converge on one primary user and deliverable.",
            "VALIDATED",
            "DESIGN_CONTRACT",
            f"Primary user is {quote_value['primary_user']}; deliverable is {quote_value['primary_deliverable']}.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/primary_user"),
        ),
        row(
            "SCENE_PROCESS_RACI_SYSTEMS_AND_TIMING",
            "Describe the quote operator, Product, Legal, Finance and GTM flow, systems, steps, time limits and approvals.",
            "VALIDATED",
            "REFERENCE_PROCESS_DESIGN",
            "Eight-step RACI, interface classes, design targets and failure paths are specified; named enterprise systems and observed timings remain NOT_RUN.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/input_gates/1"),
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/input_gates/2"),
        ),
        row(
            "VALUE_QUOTE_CYCLE_TIME",
            "Measure quote-cycle elapsed time.",
            "NOT_RUN",
            "NOT_RUN",
            metric_by_id["quote_cycle_elapsed_minutes"]["limitations"][0],
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/metrics/0"),
        ),
        row(
            "VALUE_POLICY_CONFIRMATION_TIME",
            "Measure active policy-confirmation work time.",
            "NOT_RUN",
            "NOT_RUN",
            metric_by_id["policy_confirmation_active_minutes"]["limitations"][0],
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/metrics/1"),
        ),
        row(
            "VALUE_IMPACT_OMISSION_PROXY",
            "Measure missed change impacts.",
            "VALIDATED",
            "SYNTHETIC_GOLD_PROXY",
            "0/72 synthetic cases had any exact-impact mismatch; this is not an enterprise field-omission rate.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/metrics/2"),
        ),
        row(
            "VALUE_FIRST_PASS_REWORK",
            "Measure first-pass quote rework rate.",
            "NOT_RUN",
            "NOT_RUN",
            metric_by_id["first_pass_rework_rate"]["limitations"][0],
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/metrics/3"),
        ),
        row(
            "VALUE_UNAUTHORIZED_ACCESS",
            "Measure unauthorized-access success rate.",
            "VALIDATED",
            "SYNTHETIC_GOLD_PROXY",
            "0/32 synthetic unauthorized reads succeeded; this does not establish production IAM performance.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/metrics/4"),
        ),
        row(
            "VALUE_MANUAL_ESCALATION",
            "Measure manual escalation rate.",
            "VALIDATED",
            "OBSERVED_LOCAL_PRODUCT_PROXY",
            "2/8 local case-target decisions were HOLD_FOR_REVIEW; this is not an enterprise workforce escalation baseline.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/metrics/5"),
        ),
        row(
            "VALUE_SELECTIVE_REBASE_COST",
            "Compare selective Rebase with full rebuild cost.",
            "VALIDATED",
            "MODELLED_COUNTERFACTUAL",
            f"Normalized model saving is {quote_value['aggregate_cost']['saving_rate']:.5f}; the declared scenario envelope is 0.35-0.59875, not time, money or realized ROI.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/aggregate_cost"),
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/sensitivity"),
        ),
        row(
            "VALUE_REAL_ENTERPRISE_ROI",
            "Validate realized value on real enterprise quotes.",
            "NOT_RUN",
            "NOT_RUN",
            "No same-enterprise shadow window, calibrated labor model or realized ROI observation exists.",
            ("semifinal-closure", "/quote-value/quote-value-receipt.json#/claim_boundary"),
        ),
        row(
            "AGENTTEAMS_NATIVE_LIFECYCLE",
            "Use one run_id for native task creation, delegation, acceptance, handoff, result acceptance and terminal processing.",
            "VALIDATED",
            "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
            "Pinned upstream in-process call_tool path: 34 actions and 6 task bindings; not live worker handoff.",
            ("semifinal-closure", "/agentteams/lifecycle-receipt.json"),
        ),
        row(
            "AGENTTEAMS_AUTHORITY_BOUNDARY",
            "Define authority between AgentTeams Task and the canonical control plane.",
            "VALIDATED",
            "CONTROLLED_LOCAL_GOVERNANCE",
            "AgentTeams is candidate-only with zero canonical writes; StateStore Rebase Workflow alone owns canonical writes.",
            ("semifinal-governed", "/summary.json#/agentteams_authority"),
            ("semifinal-governed", "/summary.json#/canonical_authority"),
        ),
        row(
            "AGENTTEAMS_CONFLICT_REASSIGN_FENCING",
            "Specify conflict arbitration, reassignment and late-result fencing.",
            "VALIDATED",
            "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
            "The native lifecycle contains duplicate delegation, cancellation, reassignment and rejected late submission; no distributed race was run.",
            ("semifinal-closure", "/agentteams/lifecycle-receipt.json#/actions"),
        ),
        row(
            "GOVERNED_APPROVAL_AND_APPLY",
            "Bind same-run approval, apply and terminal state.",
            "VALIDATED",
            "CONTROLLED_LOCAL_SCRIPTED_APPROVAL",
            "Two exact owner commands produced deterministic same-run canonical Quote v3 writes; no external human UI validation was run.",
            ("semifinal-governed", "/summary.json#/approval_input_mode"),
            ("semifinal-governed", "/summary.json#/terminal_state"),
        ),
        row(
            "EXTERNAL_HUMAN_APPROVAL",
            "Validate approval by a real external human in the enterprise workflow.",
            "NOT_RUN",
            "NOT_RUN",
            "The retained approval evidence uses controlled-local scripted owner commands.",
            ("semifinal-governed", "/summary.json#/external_human_validation"),
        ),
        row(
            "KILLED_PROCESS_DURABLE_RESUME",
            "Recover after actual process termination.",
            "VALIDATED",
            "CONTROLLED_LOCAL_SIGKILL_RECOVERY",
            "Two worker processes were actually SIGKILLed and recovered from durable SQLite state in the same run.",
            ("semifinal-governed", "/runtime/process-recovery.json"),
        ),
        row(
            "RETRY_ADOPT_RECOVERY_SEMANTICS",
            "Define retry and adopt-committed recovery semantics.",
            "VALIDATED",
            "CONTROLLED_LOCAL_DURABLE_RECOVERY",
            "RETRY_INTENT_RECONCILED and ADOPT_COMMITTED dispositions are retained; distributed recovery was not run.",
            ("semifinal-governed", "/summary.json#/runtime_recovery_dispositions"),
        ),
        row(
            "FAILED_CANONICAL_WRITE_COMPENSATION",
            "Execute compensation after a partially failed canonical business write.",
            "NOT_RUN",
            "NOT_RUN",
            "Crash recovery is proven, but an induced partially failed canonical business write and compensating transaction are not retained.",
            ("semifinal-governed", "/summary.json#/claim_boundary"),
        ),
        row(
            "LIVE_DISTRIBUTED_AGENTTEAMS",
            "Run AgentTeams with live distributed workers.",
            "NOT_RUN",
            "NOT_RUN",
            "The retained path is pinned upstream and in-process, not live distributed worker execution.",
            ("semifinal-governed", "/summary.json#/live_distributed_agentteams"),
        ),
        row(
            "SKILL_LIFECYCLE_PARITY",
            "Give all three Skills equivalent evaluation, admission, release and rollback-decision lifecycle evidence.",
            "VALIDATED",
            "RUN_LOCAL_INSTALLED_WHEEL_REQUALIFICATION",
            "All three packages have run-local evaluation/release/rollback-decision receipts; external statistical evaluation is NOT_RUN.",
            ("semifinal-closure", "/skills/summary.json#/packages"),
        ),
        row(
            "QUOTE_SKILL_DISCOVERY_LOAD_INVOKE",
            "Submit a discoverable, loadable and callable quote-compose artifact with receipts.",
            "VALIDATED",
            "CONTROLLED_LOCAL_INSTALLED_WHEEL",
            "Three packages were discovered from an isolated installed wheel and quote-compose was invoked on the same candidate run.",
            ("semifinal-closure", "/skills/distribution/installed-wheel-receipt.json"),
            ("semifinal-closure", "/skills/quote-compose/invocation-receipt.json"),
        ),
        row(
            "EXECUTABLE_PREDECESSOR_ROLLBACK",
            "Load and invoke an exact predecessor Skill release.",
            "VALIDATED",
            "CONTROLLED_LOCAL_EXECUTABLE_RELEASE_ROLLBACK",
            "The exact retained direct predecessor was made effective and invoked candidate-only with target_writes=0; this is not production rollout.",
            ("skill-predecessor-rollback", "/rollback-execution-receipt.json"),
        ),
        row(
            "AT_SKILL_TOOL_OTLP_CHAIN",
            "Bind AgentTeams, Skill, Tool and telemetry evidence on one run.",
            "VALIDATED",
            "CONTROLLED_LOCAL_INTEGRATED_OPERATIONS",
            "One Source-to-AgentTeams-to-Tool-to-Skill-to-terminal causal chain is retained with canonical_target_writes=0.",
            ("semifinal-closure", "/operations/summary.json#/causal_chain"),
        ),
        row(
            "OTLP_QUERY_RETENTION_ALERTS",
            "Provide OTLP fields, retained queries, alerts and retention evidence.",
            "VALIDATED",
            "CONTROLLED_LOCAL_OPERATIONS_SMOKE",
            "Local traces, logs and metrics were exported over OTLP/HTTP and queried, retained and alerted in SQLite; no production backend was run.",
            ("semifinal-closure", "/operations/summary.json"),
        ),
        row(
            "CAPACITY_DEPLOYMENT_BACKUP_SBOM_CONTRIBUTING",
            "Provide capacity, deployment, backup/restore, SBOM and contribution artifacts.",
            "VALIDATED",
            "MVP_CONTROLLED_LOCAL_OPERATIONS",
            "MVP smoke artifacts and a content-addressed contribution guide exist; they do not establish production HA, DR or SLA.",
            ("semifinal-closure", "/operations/operations/capacity-smoke-receipt.json"),
            ("semifinal-closure", "/operations/operations/deployment-profile.json"),
            ("semifinal-closure", "/operations/operations/backup-restore-receipt.json"),
            ("semifinal-closure", "/operations/operations/sbom.cdx.json"),
            ("supporting-artifacts", "/CONTRIBUTING.md"),
        ),
        row(
            "REAL_ENTERPRISE_CONNECTORS_AND_QUOTES",
            "Run real enterprise connectors against real quote data.",
            "NOT_RUN",
            "NOT_RUN",
            "HTTP Source and Tool are controlled-local fixtures; no named enterprise connector or real quote extract was run.",
            ("semifinal-closure", "/operations/summary.json#/external_enterprise_connectors"),
        ),
        row(
            "PUBLIC_PROCESS_MECHANISM_BENCHMARK",
            "Use publicly reproducible data to test selective impact scope.",
            "VALIDATED",
            "PUBLIC_SOURCE_DERIVED_SYNTHETIC_MECHANISM_VALIDATION",
            f"OAC Typed+Unknown precision={oac_strategy['metrics']['precision']['value']:.1f}, recall={oac_strategy['metrics']['recall']['value']:.1f}, scope reduction={oac_strategy['metrics']['scope_reduction_rate']['value']:.6f}; Ground Truth is experimenter-injected and not enterprise ROI.",
            ("public-process", "/strategies/2/metrics"),
            ("public-process", "/limitations"),
        ),
        row(
            "PRODUCTION_SLA_HA_DR_MULTITENANCY",
            "Validate production SLA, high availability, disaster recovery and multitenancy.",
            "NOT_RUN",
            "NOT_RUN",
            "No production workload, multi-node failover, regional disaster recovery or tenant-isolation trial was run.",
            ("semifinal-governed", "/summary.json#/production_ha_dr_sla_multitenancy"),
        ),
    ]


def build_manifest(
    *,
    checkout_root: Path = ROOT,
    closure_root: Path | None = None,
    governed_root: Path | None = None,
    public_receipt_path: Path | None = None,
    rollback_root: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    checkout_root = checkout_root.resolve()
    closure_root = (closure_root or checkout_root / "evidence/semifinal-closure/latest").resolve()
    governed_root = (governed_root or checkout_root / "evidence/semifinal-governed/latest").resolve()
    public_receipt_path = (
        public_receipt_path
        or checkout_root / "evidence/public-process/latest/public-process-bridge-receipt.json"
    ).resolve()
    rollback_root = (
        rollback_root or checkout_root / "evidence/skill-predecessor-rollback/latest"
    ).resolve()

    closure_descriptor, closure = _verify_indexed_pack(
        evidence_id="semifinal-closure",
        root=closure_root,
        checkout_root=checkout_root,
        maturity="CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
        expected_class="CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
    )
    governed_descriptor, governed = _verify_indexed_pack(
        evidence_id="semifinal-governed",
        root=governed_root,
        checkout_root=checkout_root,
        maturity="CONTROLLED_LOCAL_GOVERNED_WRITE_AND_SIGKILL_RECOVERY",
        expected_class="CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY",
    )
    rollback_descriptor, rollback = _verify_indexed_pack(
        evidence_id="skill-predecessor-rollback",
        root=rollback_root,
        checkout_root=checkout_root,
        maturity="CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK",
        expected_class="CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK",
    )
    public_descriptor, public_receipt = _public_descriptor(
        public_receipt_path,
        checkout_root=checkout_root,
    )

    _require(
        closure["run_id"] == governed["run_id"] == rollback["run_id"] == PRIMARY_RUN_ID,
        "CONTROLLED_EVIDENCE_RUN_BINDING",
    )
    _require(
        governed["parent_candidate_summary_digest"] == closure["digest"]
        and governed["parent_terminal_state"] == closure["terminal_state"] == "CANDIDATE_ACCEPTED",
        "GOVERNED_PARENT_BINDING",
    )
    _require(
        rollback["from_package_digest"] == closure["skill_package_digest"]
        and rollback["target_writes"] == 0
        and rollback["candidate_only"] is True
        and rollback["restoration_status"] == "EXECUTED_AND_INVOKED"
        and rollback["lineage_status"] == "DIRECT_DECLARED_PREDECESSOR",
        "ROLLBACK_PARENT_BINDING",
    )
    _require(
        closure["canonical_target_writes"] == 0
        and governed["proposal_plane_target_writes"] == 0
        and governed["agentteams_authority"] == "CANDIDATE_ONLY_ZERO_CANONICAL_WRITES"
        and governed["canonical_authority"] == "STATESTORE_REBASE_WORKFLOW_ONLY",
        "AUTHORITY_BOUNDARY",
    )
    rollback_receipt = _load(rollback_root / "rollback-execution-receipt.json")
    _require(
        _record_ok(rollback_receipt)
        and rollback_receipt.get("actor_id") == "authority:skill-registry"
        and rollback_receipt.get("run_id") == PRIMARY_RUN_ID,
        "ROLLBACK_AUTHORITY",
    )

    quote_value = _load(closure_root / "quote-value/quote-value-receipt.json")
    _require(_record_ok(quote_value) and quote_value.get("verification_status") == "PASS", "QUOTE_VALUE")
    process_baseline_path = checkout_root / "benchmark/quote-value-v0.1/public/current-process-baseline.json"
    process_source = next(
        item for item in quote_value["source_evidence"] if item["id"] == "current_process_baseline"
    )
    _require(
        process_baseline_path.is_file()
        and _file_digest(process_baseline_path) == process_source["file_sha256"],
        "PROCESS_BASELINE_BINDING",
    )
    process_baseline = _load(process_baseline_path)
    _require(
        process_baseline.get("primary_user") == "Enterprise Quote Operator"
        and process_baseline.get("status") == "NOT_RUN"
        and len(process_baseline.get("process_steps", [])) == 8
        and all(
            step.get("systems", {}).get("named_connector_status") == "NOT_RUN"
            and step.get("target_response", {}).get("basis") == "DESIGN_TARGET_NOT_OBSERVED_BASELINE"
            and step.get("responsible")
            and step.get("to_be_responsible")
            and step.get("accountable")
            for step in process_baseline["process_steps"]
        ),
        "PROCESS_BASELINE_BOUNDARY",
    )

    contributing_path = checkout_root / "CONTRIBUTING.md"
    _require(contributing_path.is_file(), "CONTRIBUTING_FILE")
    supporting_artifacts = {
        "process_baseline": {
            "path": _display_path(process_baseline_path, checkout_root),
            "file_sha256": _file_digest(process_baseline_path),
            "maturity": "REFERENCE_PROCESS_DESIGN",
            "claim_boundary": "Named connector and enterprise observation status remain NOT_RUN.",
        },
        "contributing_guide": {
            "path": _display_path(contributing_path, checkout_root),
            "file_sha256": _file_digest(contributing_path),
            "maturity": "DOCUMENTED_MVP_ENGINEERING",
            "claim_boundary": "Documentation presence does not establish production operations.",
        },
    }

    completion_matrix = _matrix(quote_value=quote_value, public_receipt=public_receipt)
    validated = sum(item["result"] == "VALIDATED" for item in completion_matrix)
    not_run = sum(item["result"] == "NOT_RUN" for item in completion_matrix)
    body: dict[str, Any] = {
        "schema_version": "orgrebase.semifinal-mvp-evidence-manifest.v1",
        "status": "PASS",
        "manifest_meaning": "PASS_MEANS_EVIDENCE_INVENTORY_INTEGRITY_NOT_PRODUCTION_READINESS",
        "maturity": "SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION",
        "production_ready": False,
        "primary_run_id": PRIMARY_RUN_ID,
        "claim_boundary": (
            "The manifest unifies controlled-local execution, scripted governance, a public-source-derived "
            "synthetic mechanism benchmark, and executable Skill rollback. It does not establish real-enterprise "
            "value, live distributed AgentTeams, external-human acceptance, or production SLA/HA/DR/multitenancy."
        ),
        "evidence_packs": [
            closure_descriptor,
            governed_descriptor,
            public_descriptor,
            rollback_descriptor,
        ],
        "supporting_artifacts": supporting_artifacts,
        "cross_pack_bindings": {
            "controlled_local_same_run_evidence_ids": [
                "semifinal-closure",
                "semifinal-governed",
                "skill-predecessor-rollback",
            ],
            "controlled_local_run_id": PRIMARY_RUN_ID,
            "public_process_join_mode": "INDEPENDENT_MECHANISM_BENCHMARK_NOT_SAME_RUN",
            "public_process_run_id": public_receipt["run_id"],
            "candidate_summary_digest": closure["digest"],
            "governed_parent_candidate_summary_digest": governed[
                "parent_candidate_summary_digest"
            ],
            "current_quote_skill_package_digest": closure["skill_package_digest"],
            "rollback_from_package_digest": rollback["from_package_digest"],
            "rollback_effective_predecessor_digest": rollback["effective_package_digest"],
        },
        "authority_boundaries": {
            "agentteams": "CANDIDATE_ONLY_ZERO_CANONICAL_WRITES",
            "canonical_business_state": "STATESTORE_REBASE_WORKFLOW_ONLY",
            "canonical_approval_input": "CONTROLLED_LOCAL_SCRIPTED_COMMAND_NOT_EXTERNAL_HUMAN",
            "skill_release_rollback": "AUTHORITY_SKILL_REGISTRY_ONLY",
            "public_benchmark": "MECHANISM_VALIDATION_ONLY_NO_BUSINESS_AUTHORITY",
        },
        "completion_totals": {
            "recommendation_count": len(completion_matrix),
            "validated_count": validated,
            "not_run_count": not_run,
        },
        "completion_matrix": completion_matrix,
        "promotion_blockers": [
            "REAL_ENTERPRISE_CONNECTORS_AND_QUOTES",
            "VALUE_QUOTE_CYCLE_TIME",
            "VALUE_POLICY_CONFIRMATION_TIME",
            "VALUE_FIRST_PASS_REWORK",
            "VALUE_REAL_ENTERPRISE_ROI",
            "EXTERNAL_HUMAN_APPROVAL",
            "FAILED_CANONICAL_WRITE_COMPENSATION",
            "LIVE_DISTRIBUTED_AGENTTEAMS",
            "PRODUCTION_SLA_HA_DR_MULTITENANCY",
        ],
    }
    summary = {**body, "digest": _digest(body)}
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence/semifinal-mvp/latest/summary.json",
    )
    parser.add_argument(
        "--closure-root",
        type=Path,
        default=ROOT / "evidence/semifinal-closure/latest",
    )
    parser.add_argument(
        "--governed-root",
        type=Path,
        default=ROOT / "evidence/semifinal-governed/latest",
    )
    parser.add_argument(
        "--public-receipt",
        type=Path,
        default=ROOT / "evidence/public-process/latest/public-process-bridge-receipt.json",
    )
    parser.add_argument(
        "--rollback-root",
        type=Path,
        default=ROOT / "evidence/skill-predecessor-rollback/latest",
    )
    args = parser.parse_args()
    summary = build_manifest(
        closure_root=args.closure_root,
        governed_root=args.governed_root,
        public_receipt_path=args.public_receipt,
        rollback_root=args.rollback_root,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "digest": summary["digest"],
                "output": str(args.output),
                "completion_totals": summary["completion_totals"],
                "production_ready": summary["production_ready"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
