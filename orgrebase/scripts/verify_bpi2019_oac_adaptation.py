#!/usr/bin/env python3
"""Independently verify the frozen BPI 2019 OAC adaptation add-on.

This verifier intentionally imports neither OrgRebase product modules nor the
adaptation producer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

CLAIM = "PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION"
SUPPORTED_VERTEX_MODEL_IDS = frozenset(
    {"gemini-3.7-flash", "gemini-3.8-flash"}
)
UNKNOWN = {
    "ORGANIZATION_VALUES",
    "REAL_RESPONSIBLE_OWNERS",
    "PERMISSIONS",
    "APPROVAL_AUTHORITIES",
}
ACTIONS = [
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


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sealed(value: dict[str, Any], failures: list[str], code: str) -> None:
    payload = dict(value)
    claimed = payload.pop("digest", None)
    if claimed != _digest(payload):
        failures.append(f"{code}_DIGEST_MISMATCH")


def _verify_model_attempt(
    model: dict[str, Any],
    *,
    selected_mapping_lane: Any,
    failures: list[str],
) -> None:
    live_selected = selected_mapping_lane == "LIVE_VERTEX_AGENT_CANDIDATE"
    fallback_selected = (
        selected_mapping_lane == "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK"
    )
    request_id = model.get("provider_request_id")
    request_id_present = isinstance(request_id, str) and bool(request_id.strip())

    if model.get("provider_request_id_present") is not request_id_present:
        failures.append("MODEL_PROVIDER_REQUEST_ID_BINDING_INVALID")

    if live_selected:
        if model.get("provider") != "vertex-ai":
            failures.append("LIVE_MODEL_PROVIDER_INVALID")
        model_id = model.get("model_id")
        if model_id not in SUPPORTED_VERTEX_MODEL_IDS:
            failures.append("LIVE_MODEL_ID_UNSUPPORTED")
        if model.get("model_version") != model_id:
            failures.append("LIVE_MODEL_VERSION_MISMATCH")
        if model.get("status") != "VALID":
            failures.append("LIVE_MODEL_STATUS_INVALID")
        if model.get("evidence_class") != "LIVE_MODEL":
            failures.append("LIVE_MODEL_EVIDENCE_CLASS_INVALID")
        if not request_id_present:
            failures.append("LIVE_MODEL_PROVIDER_REQUEST_ID_INVALID")
        if model.get("selected") is not True or model.get("fallback_is_separate") is not False:
            failures.append("LIVE_MODEL_SELECTION_INVALID")
        return

    if fallback_selected:
        if model.get("selected") is not False or model.get("fallback_is_separate") is not True:
            failures.append("FALLBACK_NOT_SEPARATELY_RECORDED")
        # A failed provider response may truthfully be LIVE_MODEL only when a
        # concrete provider response ID and a supported, consistent Vertex
        # model identity were observed. A pre-call fallback must not borrow a
        # live evidence label or request ID.
        if model.get("evidence_class") == "LIVE_MODEL":
            model_id = model.get("model_id")
            if (
                model.get("provider") != "vertex-ai"
                or model_id not in SUPPORTED_VERTEX_MODEL_IDS
                or model.get("model_version") != model_id
                or not request_id_present
                or model.get("status") not in {"VALID", "SCHEMA_ERROR"}
            ):
                failures.append("FALLBACK_LIVE_EVIDENCE_MISREPORTED")
        elif request_id_present or model.get("provider_request_id_present") is True:
            failures.append("FALLBACK_LIVE_EVIDENCE_MISREPORTED")


def verify(root: Path, benchmark: Path, config_path: Path) -> list[str]:
    failures: list[str] = []
    receipt = _load(root / "adaptation-receipt.json")
    config = _load(config_path)
    dataset = _load(benchmark / "dataset-manifest.json")
    projection = _load(benchmark / "projection/bpi2019-real-process-projection.json")
    execution = _load(root / "typed-scope-execution/bpi2019-real-process-benchmark-receipt.json")
    independent = _load(root / "typed-scope-execution/bpi2019-real-process-verification.json")
    fallback = _load(root / "deterministic-fallback-contract.json")
    owner_review = _load(root / "owner-review-summary.json")
    manifest = _load(root / "manifest.json")
    for value, code in (
        (receipt, "ADAPTATION"),
        (config, "CONFIG"),
        (dataset, "DATASET"),
        (projection, "PROJECTION"),
        (execution, "EXECUTION"),
        (independent, "INDEPENDENT_VERIFICATION"),
        (fallback, "DETERMINISTIC_FALLBACK_CONTRACT"),
        (owner_review, "OWNER_REVIEW_SUMMARY"),
        (manifest, "MANIFEST"),
    ):
        _sealed(value, failures, code)
    if receipt.get("status") != "PASS" or receipt.get("claim_ceiling") != CLAIM:
        failures.append("ADAPTATION_STATUS_OR_CLAIM_INVALID")
    if receipt.get("adaptation_run_id") == receipt.get("execution_run_id"):
        failures.append("ADAPTATION_EXECUTION_RUNS_NOT_SEPARATE")
    if (
        receipt.get("dataset_manifest_digest") != dataset.get("digest")
        or receipt.get("projection_digest") != projection.get("digest")
        or receipt.get("config_digest") != config.get("digest")
    ):
        failures.append("INPUT_DIGEST_BINDING_MISMATCH")
    lifecycle = receipt.get("agentteams", {})
    if lifecycle.get("action_sequence") != ACTIONS or lifecycle.get("terminal_state") != "completed":
        failures.append("AGENTTEAMS_LIFECYCLE_INCOMPLETE")
    if lifecycle.get("run_id") != receipt.get("adaptation_run_id"):
        failures.append("AGENTTEAMS_RUN_BINDING_MISMATCH")
    if receipt.get("selected_mapping_lane") not in {
        "LIVE_VERTEX_AGENT_CANDIDATE",
        "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK",
    }:
        failures.append("MAPPING_LANE_INVALID")
    model = receipt.get("model_attempt", {})
    if not isinstance(model, dict):
        failures.append("MODEL_ATTEMPT_INVALID")
        model = {}
    _verify_model_attempt(
        model,
        selected_mapping_lane=receipt.get("selected_mapping_lane"),
        failures=failures,
    )
    if (
        model.get("fallback_contract_digest") != fallback.get("digest")
        or fallback.get("evidence_class") != "LOCAL_DETERMINISTIC"
        or fallback.get("candidate_only") is not True
        or fallback.get("canonical_target_writes") != 0
        or fallback.get("status")
        != (
            "SELECTED"
            if receipt.get("selected_mapping_lane") == "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK"
            else "AVAILABLE_NOT_SELECTED"
        )
    ):
        failures.append("DETERMINISTIC_FALLBACK_CONTRACT_INVALID")
    validation = receipt.get("deterministic_validation", {})
    if validation.get("verdict") != "PASS" or validation.get("failures") != []:
        failures.append("DETERMINISTIC_MAPPING_VALIDATION_FAILED")
    unknowns = receipt.get("unknown_dimensions", {})
    if set(unknowns) != UNKNOWN or any(
        value
        != {
            "status": "UNKNOWN",
            "execution_disposition": "HOLD",
            "human_supplement_required": True,
            "inferred_from_anonymous_log": False,
        }
        for value in unknowns.values()
    ):
        failures.append("UNKNOWN_ORGANIZATIONAL_FACTS_NOT_PRESERVED")
    gate = receipt.get("review_gate", {})
    admission = receipt.get("human_admission", {})
    if (
        gate.get("owner_review_summary_digest") != owner_review.get("digest")
        or owner_review.get("adaptation_run_id") != receipt.get("adaptation_run_id")
        or owner_review.get("data_class") != "PUBLIC_ANONYMIZED_ENTERPRISE_EVENT_LOG"
        or owner_review.get("dataset_manifest_digest") != receipt.get("dataset_manifest_digest")
        or owner_review.get("projection_digest") != receipt.get("projection_digest")
        or set(owner_review.get("declared_unknowns", [])) != UNKNOWN
        or owner_review.get("canonical_target_writes") != 0
        or gate.get("review_duration_ms") != 4000
        or gate.get("not_before_epoch_ms", 0) - gate.get("prepared_at_epoch_ms", 0) != 4000
        or admission.get("early_attempt", {}).get("status") != "REJECTED_TOO_EARLY"
        or admission.get("admission", {}).get("status") != "ADMITTED"
        or admission.get("admission", {}).get("interaction_evidence")
        != "CONTROLLED_LOCAL_SCRIPTED_OWNER_COMMAND_NOT_EXTERNAL_HUMAN"
        or admission.get("admission", {}).get("elapsed_ms", 0) < 4000
    ):
        failures.append("FOUR_SECOND_HUMAN_GATE_INVALID")
    binding = receipt.get("typed_scope_execution", {})
    if not all(
        binding.get(key) == receipt.get(key)
        for key in (
            "adaptation_run_id",
            "execution_run_id",
            "mapping_set_digest",
            "context_capsule_digest",
            "organizational_demand_digest",
        )
    ):
        failures.append("EXECUTION_BINDING_DIGEST_MISMATCH")
    typed = next(
        (row for row in execution.get("strategies", []) if row.get("strategy_id") == "OAC_TYPED_ITEM_SCOPE"),
        {},
    )
    if (
        execution.get("scenario", {}).get("query_count") != 128
        or independent.get("status") != "PASS"
        or independent.get("receipt_digest") != execution.get("digest")
        or independent.get("queries_replayed") != 128
        or typed.get("metrics", {}).get("recall", {}).get("value") != 1.0
        or typed.get("metrics", {}).get("unsafe_false_unaffected_rate", {}).get("value") != 0.0
    ):
        failures.append("TYPED_SCOPE_INDEPENDENT_REPLAY_INVALID")
    if receipt.get("canonical_target_writes") != 0 or receipt.get("candidate_only") is not True:
        failures.append("AUTHORITY_CEILING_EXPANDED")
    expected_limits = {
        "TASK_DEFINED_GROUND_TRUTH_NOT_HUMAN_CAUSAL_ANNOTATION",
        "NOT_ENTERPRISE_QUOTE_DATA_OR_QUOTE_ROI",
        "NOT_ARBITRARY_ENTERPRISE_ADAPTATION",
        "NOT_PRODUCTION_DEPLOYMENT",
        "NOT_EXTERNAL_HUMAN_ACCEPTANCE",
    }
    if set(receipt.get("limitations", [])) != expected_limits:
        failures.append("CLAIM_LIMITATIONS_INCOMPLETE")
    entries = manifest.get("entries", [])
    expected_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if {item.get("path") for item in entries} != expected_paths:
        failures.append("MANIFEST_CLOSURE_MISMATCH")
    for item in entries:
        path = root / str(item.get("path"))
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if item.get("sha256") != actual or item.get("bytes") != path.stat().st_size:
            failures.append(f"MANIFEST_ENTRY_DRIFT:{item.get('path')}")
    return sorted(set(failures))


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=project / "evidence/oac-public-real-process/latest")
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=project / "benchmark/quote-value-v0.4-bpi-real-process",
    )
    parser.add_argument("--config", type=Path, default=project / "configs/oac/bpi2019-p2p-adaptation-v1.json")
    args = parser.parse_args()
    model_evidence: dict[str, Any] = {}
    try:
        failures = verify(args.evidence.resolve(), args.benchmark.resolve(), args.config.resolve())
        receipt = _load(args.evidence.resolve() / "adaptation-receipt.json")
        model = receipt.get("model_attempt")
        if isinstance(model, dict):
            model_evidence = {
                "provider": model.get("provider"),
                "model_id": model.get("model_id"),
                "model_version": model.get("model_version"),
                "status": model.get("status"),
                "evidence_class": model.get("evidence_class"),
                "provider_request_id_present": bool(model.get("provider_request_id")),
                "selected_mapping_lane": receipt.get("selected_mapping_lane"),
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures = [f"VERIFICATION_INPUT_INVALID:{exc}"]
    result = {
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "INDEPENDENT_STDLIB_CLOSED_WORLD_REPLAY",
        "product_imports": 0,
        "failures": failures,
        "model_evidence": model_evidence,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
