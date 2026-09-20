#!/usr/bin/env python3
"""Independently verify the unified semifinal MVP evidence manifest with stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_RUN_ID = "run:orgrebase:semifinal-closure:quote-001"
EXPECTED_MATRIX_DIGEST = "sha256:ea588ceff8693d69f621511fef24dc7a1406bef65901a4eba92706de46248556"
RETAINED_MANIFEST_DIGEST = "sha256:2bc3801525ab207ef143e92399bc2bd40deca268782cfdd829b003174796ebb0"
RETAINED_CONTRIBUTING_DIGEST = "sha256:0fd5dade4140645d243b04fb5e8116dabb8d96a8cd4b90378cbb78c6259dae25"
RETAINED_CONTRIBUTING_PATH = "evidence/semifinal-mvp/supporting/CONTRIBUTING.md"
EXPECTED_AUTHORITY = {
    "agentteams": "CANDIDATE_ONLY_ZERO_CANONICAL_WRITES",
    "canonical_business_state": "STATESTORE_REBASE_WORKFLOW_ONLY",
    "canonical_approval_input": "CONTROLLED_LOCAL_SCRIPTED_COMMAND_NOT_EXTERNAL_HUMAN",
    "skill_release_rollback": "AUTHORITY_SKILL_REGISTRY_ONLY",
    "public_benchmark": "MECHANISM_VALIDATION_ONLY_NO_BUSINESS_AUTHORITY",
}
EXPECTED_PROMOTION_BLOCKERS = [
    "REAL_ENTERPRISE_CONNECTORS_AND_QUOTES",
    "VALUE_QUOTE_CYCLE_TIME",
    "VALUE_POLICY_CONFIRMATION_TIME",
    "VALUE_FIRST_PASS_REWORK",
    "VALUE_REAL_ENTERPRISE_ROI",
    "EXTERNAL_HUMAN_APPROVAL",
    "FAILED_CANONICAL_WRITE_COMPENSATION",
    "LIVE_DISTRIBUTED_AGENTTEAMS",
    "PRODUCTION_SLA_HA_DR_MULTITENANCY",
]
EXPECTED_KEYS = {
    "schema_version",
    "status",
    "manifest_meaning",
    "maturity",
    "production_ready",
    "primary_run_id",
    "claim_boundary",
    "evidence_packs",
    "supporting_artifacts",
    "cross_pack_bindings",
    "authority_boundaries",
    "completion_totals",
    "completion_matrix",
    "promotion_blockers",
    "digest",
}


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


def _indexed_source(
    *,
    evidence_id: str,
    root: Path,
    checkout_root: Path,
    maturity: str,
    expected_class: str,
    failures: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    summary_path = root / "summary.json"
    index_path = root / "evidence-index.json"
    if not summary_path.is_file() or not index_path.is_file():
        failures.append(f"SOURCE_FILES:{evidence_id}")
        return {}, {}
    summary = _load(summary_path)
    index = _load(index_path)
    if not _record_ok(summary):
        failures.append(f"SOURCE_SUMMARY_DIGEST:{evidence_id}")
    if not _record_ok(index):
        failures.append(f"SOURCE_INDEX_DIGEST:{evidence_id}")
    expected_entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != index_path
    ]
    if index.get("entries") != expected_entries:
        failures.append(f"SOURCE_INDEX_ENTRIES:{evidence_id}")
    if index.get("entry_count") != len(expected_entries):
        failures.append(f"SOURCE_INDEX_COUNT:{evidence_id}")
    if index.get("pack_digest") != _digest(expected_entries):
        failures.append(f"SOURCE_INDEX_PACK_DIGEST:{evidence_id}")
    if summary.get("status") != "PASS" or summary.get("evidence_class") != expected_class:
        failures.append(f"SOURCE_CLAIM_CLASS:{evidence_id}")
    descriptor = {
        "evidence_id": evidence_id,
        "source_type": "INDEXED_EVIDENCE_PACK",
        "path": _display_path(root, checkout_root),
        "status": "PASS",
        "maturity": maturity,
        "claim_boundary": summary.get("claim_boundary"),
        "run_id": summary.get("run_id"),
        "evidence_class": summary.get("evidence_class"),
        "content_address": {
            "summary_file_sha256": _file_digest(summary_path),
            "summary_digest": summary.get("digest"),
            "index_file_sha256": _file_digest(index_path),
            "index_digest": index.get("digest"),
            "pack_digest": index.get("pack_digest"),
            "entry_count": index.get("entry_count"),
        },
    }
    return descriptor, summary


def _public_source(
    receipt_path: Path,
    *,
    checkout_root: Path,
    failures: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not receipt_path.is_file():
        failures.append("PUBLIC_RECEIPT_FILE")
        return {}, {}
    receipt = _load(receipt_path)
    if not _record_ok(receipt):
        failures.append("PUBLIC_RECEIPT_DIGEST")
    if (
        receipt.get("status") != "PASS"
        or receipt.get("evidence_class") != "PUBLIC_SOURCE_DERIVED_SYNTHETIC"
        or receipt.get("claim_ceiling") != "MECHANISM_VALIDATION_ONLY"
        or receipt.get("claim_boundary")
        != "NOT_OBSERVED_ENTERPRISE_SHADOW_NOT_REALIZED_ROI_NOT_PRODUCTION_SLA"
    ):
        failures.append("PUBLIC_CLAIM_BOUNDARY")
    strategy = next(
        (
            item
            for item in receipt.get("strategies", [])
            if isinstance(item, dict) and item.get("strategy_id") == "OAC_TYPED_WITH_UNKNOWN"
        ),
        {},
    )
    metrics = strategy.get("metrics", {})
    if (
        metrics.get("precision", {}).get("value") != 1.0
        or metrics.get("recall", {}).get("value") != 1.0
        or metrics.get("scope_reduction_rate", {}).get("value") != 0.363636
        or metrics.get("unsafe_false_unaffected_rate", {}).get("value") != 0.0
    ):
        failures.append("PUBLIC_MECHANISM_METRICS")
    if not all(
        isinstance(item, dict) and item.get("status") == "NOT_RUN"
        for item in receipt.get("enterprise_value_metrics", [])
    ) or len(receipt.get("enterprise_value_metrics", [])) != 7:
        failures.append("PUBLIC_ENTERPRISE_METRIC_BOUNDARY")

    closure = receipt.get("input_closure", {})
    benchmark_root = checkout_root / str(closure.get("benchmark_root", ""))
    listed = closure.get("manifest_file_sha256", {})
    raw_files: dict[str, Path] = {}
    if not isinstance(listed, dict):
        failures.append("PUBLIC_INPUT_FILE_LIST")
        listed = {}
    for relative, digest in listed.items():
        path = benchmark_root / relative
        raw_files[relative] = path
        if not path.is_file() or _file_digest(path).removeprefix("sha256:") != digest:
            failures.append(f"PUBLIC_INPUT_FILE:{relative}")
    mapping_path = checkout_root / str(closure.get("mapping_path", ""))
    if not mapping_path.is_file():
        failures.append("PUBLIC_MAPPING_FILE")

    records = {
        "projection_digest": benchmark_root / "projection/object-centric-projection.json",
        "overlay_digest": benchmark_root / "overlay/policy-change-ground-truth.json",
        "dataset_manifest_digest": benchmark_root / "dataset-manifest.json",
        "license_manifest_digest": benchmark_root / "LICENSES.json",
        "mapping_digest": mapping_path,
    }
    for key, path in records.items():
        if not path.is_file():
            failures.append(f"PUBLIC_INPUT_RECORD:{key}")
            continue
        record = _load(path)
        if not _record_ok(record) or record.get("digest") != closure.get(key):
            failures.append(f"PUBLIC_INPUT_RECORD_BINDING:{key}")
    input_files = {
        _display_path(path, checkout_root): _file_digest(path)
        for path in sorted([*raw_files.values(), mapping_path])
        if path.is_file()
    }
    descriptor = {
        "evidence_id": "public-process",
        "source_type": "PUBLIC_MECHANISM_BENCHMARK_RECEIPT",
        "path": _display_path(receipt_path, checkout_root),
        "status": "PASS",
        "maturity": "PUBLIC_SOURCE_DERIVED_SYNTHETIC_MECHANISM_VALIDATION",
        "claim_boundary": receipt.get("claim_boundary"),
        "run_id": receipt.get("run_id"),
        "evidence_class": receipt.get("evidence_class"),
        "content_address": {
            "receipt_file_sha256": _file_digest(receipt_path),
            "receipt_digest": receipt.get("digest"),
            "input_file_sha256": input_files,
        },
    }
    return descriptor, receipt


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def verify(
    manifest_root: Path,
    *,
    checkout_root: Path = ROOT,
    closure_root: Path | None = None,
    governed_root: Path | None = None,
    public_receipt_path: Path | None = None,
    rollback_root: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
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
    manifest_path = manifest_root / "summary.json"
    if not manifest_path.is_file():
        failures.append("SUMMARY_FILE")
        summary: dict[str, Any] = {}
    else:
        loaded = _load(manifest_path)
        summary = loaded if isinstance(loaded, dict) else {}
        if not isinstance(loaded, dict):
            failures.append("SUMMARY_OBJECT")
    if summary and not _record_ok(summary):
        failures.append("SUMMARY_DIGEST")
    if set(summary) != EXPECTED_KEYS:
        failures.append("SUMMARY_KEYS")

    closure_descriptor, closure = _indexed_source(
        evidence_id="semifinal-closure",
        root=closure_root,
        checkout_root=checkout_root,
        maturity="CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
        expected_class="CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
        failures=failures,
    )
    governed_descriptor, governed = _indexed_source(
        evidence_id="semifinal-governed",
        root=governed_root,
        checkout_root=checkout_root,
        maturity="CONTROLLED_LOCAL_GOVERNED_WRITE_AND_SIGKILL_RECOVERY",
        expected_class="CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY",
        failures=failures,
    )
    public_descriptor, public_receipt = _public_source(
        public_receipt_path,
        checkout_root=checkout_root,
        failures=failures,
    )
    rollback_descriptor, rollback = _indexed_source(
        evidence_id="skill-predecessor-rollback",
        root=rollback_root,
        checkout_root=checkout_root,
        maturity="CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK",
        expected_class="CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK",
        failures=failures,
    )
    expected_descriptors = [
        closure_descriptor,
        governed_descriptor,
        public_descriptor,
        rollback_descriptor,
    ]
    declared_descriptors = summary.get("evidence_packs", [])
    if not isinstance(declared_descriptors, list) or len(declared_descriptors) != 4:
        failures.append("EVIDENCE_PACK_SET")
    else:
        for declared, expected in zip(declared_descriptors, expected_descriptors, strict=True):
            evidence_id = expected.get("evidence_id", "unknown")
            if not isinstance(declared, dict) or declared.get("evidence_id") != evidence_id:
                failures.append(f"EVIDENCE_PACK_ORDER:{evidence_id}")
                continue
            declared_address = declared.get("content_address", {})
            expected_address = expected.get("content_address", {})
            if declared_address.get("pack_digest") != expected_address.get("pack_digest"):
                failures.append(f"SOURCE_PACK_DIGEST_BINDING:{evidence_id}")
            if declared != expected:
                failures.append(f"EVIDENCE_PACK_DESCRIPTOR:{evidence_id}")

    controlled_runs = {closure.get("run_id"), governed.get("run_id"), rollback.get("run_id")}
    if controlled_runs != {PRIMARY_RUN_ID}:
        failures.append("SOURCE_CONTROLLED_RUN_BINDING")
    if (
        governed.get("parent_candidate_summary_digest") != closure.get("digest")
        or governed.get("parent_terminal_state") != closure.get("terminal_state")
        or closure.get("terminal_state") != "CANDIDATE_ACCEPTED"
    ):
        failures.append("SOURCE_GOVERNED_PARENT_BINDING")
    if (
        rollback.get("from_package_digest") != closure.get("skill_package_digest")
        or rollback.get("candidate_only") is not True
        or rollback.get("target_writes") != 0
        or rollback.get("restoration_status") != "EXECUTED_AND_INVOKED"
        or rollback.get("lineage_status") != "DIRECT_DECLARED_PREDECESSOR"
    ):
        failures.append("SOURCE_ROLLBACK_BINDING")
    if (
        closure.get("canonical_target_writes") != 0
        or governed.get("proposal_plane_target_writes") != 0
        or governed.get("agentteams_authority") != "CANDIDATE_ONLY_ZERO_CANONICAL_WRITES"
        or governed.get("canonical_authority") != "STATESTORE_REBASE_WORKFLOW_ONLY"
    ):
        failures.append("SOURCE_AUTHORITY_BOUNDARY")
    rollback_receipt_path = rollback_root / "rollback-execution-receipt.json"
    if rollback_receipt_path.is_file():
        rollback_receipt = _load(rollback_receipt_path)
        if (
            not _record_ok(rollback_receipt)
            or rollback_receipt.get("actor_id") != "authority:skill-registry"
            or rollback_receipt.get("run_id") != PRIMARY_RUN_ID
        ):
            failures.append("SOURCE_ROLLBACK_AUTHORITY")
    else:
        failures.append("SOURCE_ROLLBACK_RECEIPT")

    expected_cross = {
        "controlled_local_same_run_evidence_ids": [
            "semifinal-closure",
            "semifinal-governed",
            "skill-predecessor-rollback",
        ],
        "controlled_local_run_id": PRIMARY_RUN_ID,
        "public_process_join_mode": "INDEPENDENT_MECHANISM_BENCHMARK_NOT_SAME_RUN",
        "public_process_run_id": public_receipt.get("run_id"),
        "candidate_summary_digest": closure.get("digest"),
        "governed_parent_candidate_summary_digest": governed.get(
            "parent_candidate_summary_digest"
        ),
        "current_quote_skill_package_digest": closure.get("skill_package_digest"),
        "rollback_from_package_digest": rollback.get("from_package_digest"),
        "rollback_effective_predecessor_digest": rollback.get("effective_package_digest"),
    }
    if summary.get("cross_pack_bindings") != expected_cross:
        failures.append("CONTROLLED_RUN_BINDING")
    if summary.get("primary_run_id") != PRIMARY_RUN_ID:
        failures.append("PRIMARY_RUN_BINDING")
    if summary.get("authority_boundaries") != EXPECTED_AUTHORITY:
        failures.append("AUTHORITY_BOUNDARY")

    process_path = checkout_root / "benchmark/quote-value-v0.1/public/current-process-baseline.json"
    contributing_path = checkout_root / "CONTRIBUTING.md"
    contributing_source = contributing_path
    retained_contributing = (
        summary.get("digest") == RETAINED_MANIFEST_DIGEST
        and _record_ok(summary)
        and summary.get("supporting_artifacts", {}).get("contributing_guide", {}).get("file_sha256")
        == RETAINED_CONTRIBUTING_DIGEST
        and (
            not contributing_path.is_file()
            or _file_digest(contributing_path) != RETAINED_CONTRIBUTING_DIGEST
        )
    )
    # Only this sealed historical inventory may resolve its original guide bytes.
    # Fresh manifests bind the current guide; the logical path remains unchanged.
    if retained_contributing:
        contributing_source = checkout_root / RETAINED_CONTRIBUTING_PATH
    quote_value_path = closure_root / "quote-value/quote-value-receipt.json"
    if not quote_value_path.is_file() or not process_path.is_file() or not contributing_source.is_file():
        failures.append("SUPPORTING_SOURCE_FILES")
        expected_supporting: dict[str, Any] = {}
    else:
        quote_value = _load(quote_value_path)
        if not _record_ok(quote_value) or quote_value.get("verification_status") != "PASS":
            failures.append("SOURCE_QUOTE_VALUE")
        source_ref = next(
            (
                item
                for item in quote_value.get("source_evidence", [])
                if item.get("id") == "current_process_baseline"
            ),
            {},
        )
        process = _load(process_path)
        if (
            _file_digest(process_path) != source_ref.get("file_sha256")
            or process.get("status") != "NOT_RUN"
            or process.get("primary_user") != "Enterprise Quote Operator"
            or len(process.get("process_steps", [])) != 8
            or not all(
                step.get("systems", {}).get("named_connector_status") == "NOT_RUN"
                and step.get("target_response", {}).get("basis")
                == "DESIGN_TARGET_NOT_OBSERVED_BASELINE"
                and step.get("responsible")
                and step.get("to_be_responsible")
                and step.get("accountable")
                for step in process.get("process_steps", [])
            )
        ):
            failures.append("SOURCE_PROCESS_BASELINE_BOUNDARY")
        expected_supporting = {
            "process_baseline": {
                "path": _display_path(process_path, checkout_root),
                "file_sha256": _file_digest(process_path),
                "maturity": "REFERENCE_PROCESS_DESIGN",
                "claim_boundary": "Named connector and enterprise observation status remain NOT_RUN.",
            },
            "contributing_guide": {
                "path": _display_path(contributing_path, checkout_root),
                "file_sha256": _file_digest(contributing_source),
                "maturity": "DOCUMENTED_MVP_ENGINEERING",
                "claim_boundary": "Documentation presence does not establish production operations.",
            },
        }
    if summary.get("supporting_artifacts") != expected_supporting:
        failures.append("SUPPORTING_ARTIFACT_BINDING")

    if (
        summary.get("schema_version") != "orgrebase.semifinal-mvp-evidence-manifest.v1"
        or summary.get("status") != "PASS"
        or summary.get("manifest_meaning")
        != "PASS_MEANS_EVIDENCE_INVENTORY_INTEGRITY_NOT_PRODUCTION_READINESS"
        or summary.get("maturity") != "SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION"
        or summary.get("production_ready") is not False
        or summary.get("claim_boundary")
        != (
            "The manifest unifies controlled-local execution, scripted governance, a public-source-derived "
            "synthetic mechanism benchmark, and executable Skill rollback. It does not establish real-enterprise "
            "value, live distributed AgentTeams, external-human acceptance, or production SLA/HA/DR/multitenancy."
        )
    ):
        failures.append("MANIFEST_CLAIM_CEILING")

    matrix = summary.get("completion_matrix", [])
    if not isinstance(matrix, list) or _digest(matrix) != EXPECTED_MATRIX_DIGEST:
        failures.append("MATRIX_POLICY_DIGEST")
    if isinstance(matrix, list):
        ids = [item.get("recommendation_id") for item in matrix if isinstance(item, dict)]
        if len(ids) != len(matrix) or len(set(ids)) != len(ids):
            failures.append("MATRIX_IDS")
        if any(
            set(item) != {
                "recommendation_id",
                "recommendation",
                "result",
                "maturity",
                "claim_boundary",
                "evidence_refs",
            }
            or item.get("result") not in {"VALIDATED", "NOT_RUN"}
            or not item.get("claim_boundary")
            or not item.get("evidence_refs")
            for item in matrix
            if isinstance(item, dict)
        ):
            failures.append("MATRIX_ROW_CONTRACT")
        validated = sum(
            isinstance(item, dict) and item.get("result") == "VALIDATED" for item in matrix
        )
        not_run = sum(
            isinstance(item, dict) and item.get("result") == "NOT_RUN" for item in matrix
        )
        expected_totals = {
            "recommendation_count": len(matrix),
            "validated_count": validated,
            "not_run_count": not_run,
        }
        if summary.get("completion_totals") != expected_totals or expected_totals != {
            "recommendation_count": 28,
            "validated_count": 19,
            "not_run_count": 9,
        }:
            failures.append("COMPLETION_TOTALS")
        not_run_ids = {
            item["recommendation_id"]
            for item in matrix
            if isinstance(item, dict) and item.get("result") == "NOT_RUN"
        }
        if not_run_ids != set(EXPECTED_PROMOTION_BLOCKERS):
            failures.append("NOT_RUN_PROMOTION_BOUNDARY")
    if summary.get("promotion_blockers") != EXPECTED_PROMOTION_BLOCKERS:
        failures.append("PROMOTION_BLOCKERS")

    failures = list(dict.fromkeys(failures))
    body: dict[str, Any] = {
        "schema_version": "orgrebase.semifinal-mvp-evidence-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "manifest_digest": summary.get("digest"),
        "source_pack_count": 4,
        "contributing_guide_binding": {
            "logical_path": "CONTRIBUTING.md",
            "verified_source_path": _display_path(contributing_source, checkout_root),
            "scope": "RETAINED_HISTORICAL_GUIDE" if retained_contributing else "CURRENT_GUIDE",
            "certifies_current_guide": not retained_contributing and not failures,
        },
        "checked_claim_ceiling": "SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION",
        "production_ready": False,
        "failure_count": len(failures),
        "failures": failures,
    }
    verification = {**body, "digest": _digest(body)}
    if output_path is not None:
        _write(output_path, verification)
    return verification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest_root",
        nargs="?",
        type=Path,
        default=ROOT / "evidence/semifinal-mvp/latest",
    )
    parser.add_argument("--closure-root", type=Path)
    parser.add_argument("--governed-root", type=Path)
    parser.add_argument("--public-receipt", type=Path)
    parser.add_argument("--rollback-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.manifest_root / "verification.json"
    verification = verify(
        args.manifest_root,
        closure_root=args.closure_root,
        governed_root=args.governed_root,
        public_receipt_path=args.public_receipt,
        rollback_root=args.rollback_root,
        output_path=output,
    )
    print(json.dumps(verification, ensure_ascii=False, sort_keys=True))
    return 0 if verification["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
