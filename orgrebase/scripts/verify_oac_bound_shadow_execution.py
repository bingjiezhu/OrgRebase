#!/usr/bin/env python3
"""Independently verify an OAC-bound shadow pack using the standard library.

The verifier intentionally imports no OrgRebase service, runner, Pydantic
contract, or product API.  It recomputes JSON/file digests and cross-checks the
persisted adaptation, context, AgentTeams, Tool, Skill, Formation and OTLP
artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_OTLP_TRACE_LAYERS = ("SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "TERMINAL")
_SAME_RUN_PROOF_LAYERS = (
    "SOURCE",
    "CONTEXT",
    "AGENTTEAMS",
    "TOOL",
    "SKILL",
    "OTLP",
    "CANDIDATE",
)
_INPUT_FILES = {
    "approval": "inputs/approval.json",
    "review_gate": "inputs/review-gate.json",
    "capsule": "inputs/adapter-capsule.json",
    "activation": "inputs/activation-binding.json",
    "context": "inputs/context-envelope.json",
}
_FROZEN_FILES = (
    "golden-run/summary.json",
    "manifest.json",
    "verification.json",
)
_OWNER_REVIEW_ACKNOWLEDGEMENTS = (
    "REVIEWED_SOURCE_TO_CONTRACT_SUMMARY",
    "ACCEPTED_DECLARED_UNKNOWNS",
    "UNDERSTAND_NO_BUSINESS_APPROVAL",
)


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


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(path.as_posix())
    return value


def _load_any(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sealed(value: dict[str, Any]) -> bool:
    return value.get("digest") == _digest(
        {key: item for key, item in value.items() if key != "digest"}
    )


def _epoch_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        selected = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if selected.tzinfo is None or selected.utcoffset() is None:
        return None
    return int(selected.astimezone(UTC).timestamp() * 1000)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _attributes(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in _walk(value):
        encoded = node.get("attributes")
        if not isinstance(encoded, list):
            continue
        selected: dict[str, Any] = {}
        for item in encoded:
            if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
                continue
            scalar = next(
                (
                    item["value"][key]
                    for key in (
                        "stringValue",
                        "intValue",
                        "boolValue",
                        "doubleValue",
                    )
                    if key in item["value"]
                ),
                None,
            )
            if isinstance(item.get("key"), str) and scalar is not None:
                selected[item["key"]] = scalar
        if selected:
            result.append(selected)
    return result


def verify_oac_bound_shadow_execution(
    *,
    root: str | Path,
    pack: str | Path,
    frozen_golden: str | Path,
    fencing_reference: str | Path,
) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    pack_root = Path(pack).expanduser().resolve()
    golden_root = Path(frozen_golden).expanduser().resolve()
    fencing_path = Path(fencing_reference).expanduser().resolve()
    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    try:
        receipt = _load(base / "receipt.json")
        pre = _load(base / "pre-execution-binding.json")
        inputs = {name: _load(base / relative) for name, relative in _INPUT_FILES.items()}
        summary = _load(base / "shadow-run/summary.json")
        envelope = _load(base / "shadow-run/execution-envelope.json")
        actions = _load_any(base / "shadow-run/agentteams/action-journal.json")
        process_receipts = _load_any(base / "shadow-run/process-receipts.json")
        tool = _load(base / "shadow-run/tool/invocation.json")
        skill = _load(base / "shadow-run/skill/receipt.json")
        prepared = _load(base / "shadow-run/prepared-formation-bundle.json")
        exports = _load_any(base / "observability/export-receipts.json")
        query = _load(base / "observability/query-receipt.json")
        alert = _load(base / "observability/alert-receipt.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "orgrebase.oac-bound-shadow-verification.v1",
            "status": "FAIL",
            "failures": [f"LOAD_FAILED:{type(exc).__name__}"],
        }

    if not _sealed(receipt):
        fail("RECEIPT_DIGEST_INVALID")
    if not _sealed(pre) or receipt.get("pre_execution_binding_digest") != pre.get("digest"):
        fail("PRE_EXECUTION_BINDING_DIGEST_INVALID")
    for name, value in inputs.items():
        if not _sealed(value):
            fail(f"{name.upper()}_DIGEST_INVALID")
    mapping_path = base / "inputs/agent-mapping-receipt.json"
    mapping = _load(mapping_path) if mapping_path.is_file() else None
    if mapping is not None and not _sealed(mapping):
        fail("AGENT_MAPPING_RECEIPT_DIGEST_INVALID")
    formation_decision_path = base / "inputs/task-formation-decision-receipt.json"
    formation_decision = (
        _load(formation_decision_path)
        if formation_decision_path.is_file()
        else None
    )
    if formation_decision is not None and not _sealed(formation_decision):
        fail("FORMATION_DECISION_DIGEST_INVALID")

    approval = inputs["approval"]
    review_gate = inputs["review_gate"]
    capsule = inputs["capsule"]
    activation = inputs["activation"]
    context = inputs["context"]
    context_ref = str(
        context.get("ref")
        or (
            f"{context.get('id')}@{context.get('version')}"
            if context.get("id") and context.get("version")
            else context.get("id")
        )
        or ""
    )
    shadow_run_id = receipt.get("shadow_execution_run_id")
    golden_run_id = receipt.get("frozen_golden_run_id")
    adaptation_run_id = receipt.get("adaptation_run_id")
    if (
        not isinstance(shadow_run_id, str)
        or shadow_run_id in {golden_run_id, adaptation_run_id}
        or activation.get("execution_run_id") != shadow_run_id
        or pre.get("shadow_execution_run_id") != shadow_run_id
        or summary.get("run_id") != shadow_run_id
        or envelope.get("run_id") != shadow_run_id
    ):
        fail("SHADOW_RUN_SUBSTITUTION")
    if (
        capsule.get("adaptation_run_id") != adaptation_run_id
        or activation.get("adaptation_run_id") != adaptation_run_id
        or approval.get("adaptation_run_id") != adaptation_run_id
        or pre.get("adaptation_run_id") != adaptation_run_id
    ):
        fail("ADAPTATION_RUN_SUBSTITUTION")
    if (
        capsule.get("digest") != receipt.get("adapter_capsule_digest")
        or capsule.get("digest") != activation.get("adapter_capsule_digest")
        or capsule.get("digest") != pre.get("adapter_capsule_digest")
    ):
        fail("CAPSULE_BINDING_MISMATCH")
    if (
        activation.get("digest") != receipt.get("activation_binding_digest")
        or activation.get("digest") != pre.get("activation_binding_digest")
    ):
        fail("ACTIVATION_BINDING_MISMATCH")
    if (
        approval.get("digest") != receipt.get("approval_digest")
        or approval.get("digest") != capsule.get("approval_digest")
        or approval.get("digest") != pre.get("approval_digest")
        or approval.get("candidate_digest") != capsule.get("mapping_set_digest")
        or approval.get("owner_review_summary_digest")
        != capsule.get("owner_review_summary_digest")
        or tuple(approval.get("acknowledgements", ()))
        != _OWNER_REVIEW_ACKNOWLEDGEMENTS
    ):
        fail("APPROVAL_BINDING_MISMATCH")
    if (
        approval.get("review_gate") != review_gate
        or approval.get("review_gate_digest") != review_gate.get("digest")
        or receipt.get("approval_review_gate_digest") != review_gate.get("digest")
        or receipt.get("review_gate_digest") != review_gate.get("digest")
        or pre.get("review_gate_digest") != review_gate.get("digest")
        or review_gate.get("adaptation_run_id") != adaptation_run_id
        or review_gate.get("mapping_set_digest") != capsule.get("mapping_set_digest")
        or review_gate.get("owner_review_summary_digest")
        != capsule.get("owner_review_summary_digest")
        or review_gate.get("organization_snapshot_digest")
        != capsule.get("organization_snapshot_digest")
        or review_gate.get("organizational_demand_digest")
        != capsule.get("organizational_demand_digest")
        or review_gate.get("profile_digest") != capsule.get("profile_digest")
        or review_gate.get("pack_digest") != capsule.get("pack_digest")
        or review_gate.get("owner_ref") != approval.get("actor_id")
        or review_gate.get("canonical_target_writes") != 0
    ):
        fail("REVIEW_GATE_BINDING_INVALID")
    prepared_at_ms = review_gate.get("prepared_at_epoch_ms")
    not_before_ms = review_gate.get("not_before_epoch_ms")
    approved_at_ms = approval.get("approved_at_epoch_ms")
    review_duration_ms = review_gate.get("review_duration_ms")
    if (
        not isinstance(prepared_at_ms, int)
        or not isinstance(not_before_ms, int)
        or not isinstance(approved_at_ms, int)
        or not isinstance(review_duration_ms, int)
        or review_duration_ms < 4000
        or not_before_ms - prepared_at_ms != review_duration_ms
        or approved_at_ms < not_before_ms
        or _epoch_ms(review_gate.get("prepared_at")) != prepared_at_ms
        or _epoch_ms(review_gate.get("not_before")) != not_before_ms
        or _epoch_ms(approval.get("approved_at")) != approved_at_ms
        or receipt.get("review_duration_ms") != review_duration_ms
        or receipt.get("approval_elapsed_since_not_before_ms")
        != approved_at_ms - not_before_ms
    ):
        fail("REVIEW_GATE_TIME_INVALID")
    if (
        context.get("digest") != receipt.get("context_envelope_digest")
        or context.get("digest") != pre.get("context_envelope_digest")
        or context_ref != receipt.get("context_envelope_ref")
        or context_ref != pre.get("context_envelope_ref")
        or context.get("effect_ceiling") != "ZERO_EXTERNAL_EFFECTS"
        or context.get("candidate_only") is not True
        or context.get("canonical_target_writes") != 0
    ):
        fail("CONTEXT_SUBSTITUTION")
    formation_digest = context.get("task_formation_decision_receipt_digest")
    if (formation_digest is not None or mapping is not None) and (
        not isinstance(formation_decision, dict)
        or (
            formation_digest != formation_decision.get("digest")
            or formation_decision.get("organizational_demand_digest")
            != capsule.get("organizational_demand_digest")
            or formation_decision.get("organization_snapshot_digest")
            != capsule.get("organization_snapshot_digest")
            or formation_decision.get("task_ref") != context.get("task_ref")
            or formation_decision.get("coalition_plan_digest")
            != context.get("coalition_plan_digest")
            or formation_decision.get("candidate_only") is not True
            or formation_decision.get("canonical_target_writes") != 0
            or formation_decision.get("effect_ceiling") != "ZERO_EXTERNAL_EFFECTS"
            or not isinstance(formation_decision.get("obligation_bindings"), list)
            or not formation_decision.get("obligation_bindings")
        )
    ):
        fail("FORMATION_DECISION_BINDING_INVALID")
    context_created_at_ms = _epoch_ms(context.get("created_at"))
    context_expires_at_ms = _epoch_ms(context.get("expires_at"))
    context_checked_at_ms = receipt.get("context_freshness_checked_at_epoch_ms")
    freshness_basis = receipt.get("context_freshness_basis")
    if (
        freshness_basis not in {"WALL_CLOCK", "LOGICAL_EVENT_TIME"}
        or freshness_basis != pre.get("context_freshness_basis")
        or not isinstance(context_checked_at_ms, int)
        or context_checked_at_ms
        != pre.get("context_freshness_checked_at_epoch_ms")
        or context_checked_at_ms
        != _epoch_ms(receipt.get("context_freshness_checked_at"))
        or receipt.get("context_freshness_checked_at")
        != pre.get("context_freshness_checked_at")
        or not isinstance(context_created_at_ms, int)
        or not isinstance(context_expires_at_ms, int)
        or context_checked_at_ms < context_created_at_ms
        or context_checked_at_ms >= context_expires_at_ms
    ):
        fail("CONTEXT_FRESHNESS_INVALID")
    mapping_digest = mapping.get("digest") if mapping is not None else None
    if (
        receipt.get("agent_mapping_receipt_digest") != mapping_digest
        or pre.get("agent_mapping_receipt_digest") != mapping_digest
    ):
        fail("AGENT_MAPPING_BINDING_MISMATCH")
    if mapping is not None:
        accepted_mappings = mapping.get("accepted_mappings")
        accepted_digests = [
            item.get("digest")
            for item in accepted_mappings or []
            if isinstance(item, dict) and _sealed(item)
        ]
        observation = mapping.get("model_observation") or {}
        if (
            mapping.get("status") != "VALIDATED_CANDIDATE"
            or mapping.get("mapping_mode") != "LIVE_AGENT_ATTEMPT"
            or mapping.get("adaptation_run_id") != adaptation_run_id
            or mapping.get("profile_digest") != receipt.get("profile_digest")
            or mapping.get("pack_digest") != receipt.get("pack_digest")
            or mapping.get("candidate_only") is not True
            or mapping.get("canonical_target_writes") != 0
            or mapping.get("human_approval_granted") is not False
            or mapping.get("oac_source_admitted") is not False
            or len(accepted_digests) != 5
            or mapping.get("accepted_mapping_set_digest") != _digest(accepted_digests)
            or mapping.get("accepted_mapping_set_digest")
            != capsule.get("mapping_set_digest")
            or not isinstance(observation, dict)
            or observation.get("status") != "VALID"
            or observation.get("evidence_class") != "LIVE_MODEL"
            or not observation.get("provider_request_id")
        ):
            fail("AGENT_MAPPING_AUTHORITY_BINDING_INVALID")

    profile_path = pack_root / "profile.json"
    try:
        profile = _load(profile_path)
    except (OSError, ValueError, json.JSONDecodeError):
        profile = {}
        fail("PACK_PROFILE_LOAD_FAILED")
    if not _sealed(profile):
        fail("PACK_PROFILE_DIGEST_INVALID")
    profile_values = {
        receipt.get("profile_digest"),
        pre.get("profile_digest"),
        capsule.get("profile_digest"),
        activation.get("profile_digest"),
        profile.get("digest"),
    }
    if len(profile_values) != 1:
        fail("PROFILE_SUBSTITUTION")
    pack_values = {
        receipt.get("pack_digest"),
        pre.get("pack_digest"),
        capsule.get("pack_digest"),
        activation.get("pack_digest"),
        envelope.get("pack_digest"),
        (summary.get("enterprise_pack") or {}).get("pack_digest"),
    }
    if len(pack_values) != 1:
        fail("PACK_SUBSTITUTION")
    expected_pack_files = receipt.get("pack_file_manifest")
    if not isinstance(expected_pack_files, dict):
        fail("PACK_FILE_MANIFEST_MISSING")
        expected_pack_files = {}
    actual_pack_files = {
        path.relative_to(pack_root).as_posix(): _file_digest(path)
        for path in sorted(pack_root.rglob("*"))
        if path.is_file()
    }
    if expected_pack_files != actual_pack_files:
        fail("PACK_FILE_SUBSTITUTION")
    if pre.get("pack_file_manifest_digest") != _digest(actual_pack_files):
        fail("PACK_FILE_MANIFEST_DIGEST_INVALID")

    if not _sealed(summary):
        fail("COMPETITION_SUMMARY_DIGEST_INVALID")
    if (
        receipt.get("competition_summary_digest") != summary.get("digest")
        or summary.get("status") != "PASS"
        or summary.get("context_envelope_digest") != context.get("digest")
        or summary.get("canonical_target_writes") != 0
        or summary.get("external_promotion_status") != "NOT_RUN"
        or summary.get("project_terminal_state") != "completed"
    ):
        fail("COMPETITION_SUMMARY_BOUNDARY_INVALID")
    if not _sealed(envelope) or (
        envelope.get("digest") != receipt.get("execution_envelope_digest")
        or envelope.get("digest") != summary.get("execution_envelope_digest")
        or envelope.get("context_envelope_digest") != context.get("digest")
    ):
        fail("EXECUTION_ENVELOPE_BINDING_INVALID")

    if not isinstance(actions, list) or not isinstance(process_receipts, list):
        fail("AGENTTEAMS_EVIDENCE_SHAPE")
        actions = []
        process_receipts = []
    action_by_digest: dict[str, dict[str, Any]] = {}
    action_digests: list[str] = []
    for sequence, action in enumerate(actions, start=1):
        if (
            not isinstance(action, dict)
            or action.get("sequence") != sequence
            or not _sealed(action)
            or not isinstance(action.get("digest"), str)
        ):
            fail("AGENTTEAMS_ACTION_INVALID")
            continue
        action_by_digest[action["digest"]] = action
        action_digests.append(action["digest"])
    if (
        receipt.get("agentteams_action_count") != len(actions)
        or summary.get("agentteams_action_count") != len(actions)
        or receipt.get("agentteams_action_digests") != action_digests
        or summary.get("agentteams_action_digests") != action_digests
        or not actions
        or actions[-1].get("action") != "complete_project"
    ):
        fail("AGENTTEAMS_ACTION_CHAIN_INVALID")
    lifecycle = (
        ("delegate_action_digest", "delegate_task"),
        ("ack_action_digest", "ack_task"),
        ("submit_action_digest", "submit_task"),
        ("check_action_digest", "check_task"),
        ("accept_action_digest", "accept_task_result"),
    )
    bindings = summary.get("task_bindings")
    if not isinstance(bindings, list) or not bindings:
        fail("TASK_BINDINGS_REQUIRED")
        bindings = []
    for binding in bindings:
        if not isinstance(binding, dict):
            fail("TASK_BINDING_SHAPE")
            continue
        task_id = binding.get("task_id")
        try:
            frozen_binding = _load(
                base / "shadow-run/agentteams/bindings" / f"{task_id}.json"
            )
        except (OSError, ValueError, json.JSONDecodeError):
            frozen_binding = {}
        if (
            binding != frozen_binding
            or not _sealed(binding)
            or binding.get("run_id") != shadow_run_id
            or binding.get("context_envelope_digest") != context.get("digest")
            or binding.get("candidate_only") is not True
            or binding.get("target_writes") != 0
        ):
            fail("TASK_BINDING_INVALID")
        observed: list[int] = []
        for field, expected_action in lifecycle:
            action = action_by_digest.get(str(binding.get(field)))
            if action is None or action.get("action") != expected_action:
                fail("TASK_LIFECYCLE_INVALID")
                continue
            observed.append(int(action.get("sequence", 0)))
        if len(observed) != len(lifecycle) or observed != sorted(observed):
            fail("TASK_LIFECYCLE_ORDER_INVALID")
    for process in process_receipts:
        if (
            not isinstance(process, dict)
            or not _sealed(process)
            or process.get("run_id") != shadow_run_id
            or process.get("context_envelope_digest") != context.get("digest")
            or process.get("canonical_target_writes") != 0
        ):
            fail("PROCESS_RECEIPT_INVALID")
            continue
        ack = action_by_digest.get(str(process.get("started_after_ack_action_digest")))
        if ack is None or ack.get("action") != "ack_task":
            fail("PROCESS_ACK_BINDING_INVALID")

    tool_receipt = tool.get("receipt")
    if (
        tool.get("run_id") != shadow_run_id
        or tool.get("candidate_only") is not True
        or tool.get("target_writes") != 0
        or not isinstance(tool_receipt, dict)
        or not _sealed(tool_receipt)
        or tool_receipt.get("run_id") != shadow_run_id
        or tool_receipt.get("status") != "SUCCEEDED"
        or tool_receipt.get("target_writes") != 0
        or tool_receipt.get("digest") != receipt.get("tool_receipt_digest")
        or tool_receipt.get("digest") != summary.get("tool_receipt_digest")
    ):
        fail("TOOL_BINDING_INVALID")
    if (
        not _sealed(skill)
        or skill.get("run_id") != shadow_run_id
        or skill.get("authorization_mode") != "RELEASE"
        or skill.get("target_writes") != 0
        or skill.get("digest") != receipt.get("skill_invocation_receipt_digest")
        or skill.get("digest") != summary.get("skill_invocation_receipt_digest")
        or skill.get("package_digest") != receipt.get("skill_package_digest")
    ):
        fail("SKILL_BINDING_INVALID")
    formation_digest = summary.get("prepared_formation_digest")
    formation_receipt_digest = summary.get(
        "controlled_agentteams_formation_receipt_digest"
    )
    formation_writes = prepared.get("artifact_writes")
    formation_receipts = [
        item.get("payload")
        for item in formation_writes or []
        if isinstance(item, dict)
        and item.get("media_type")
        == "application/vnd.orgrebase.controlled-agentteams-formation-receipt+json"
    ]
    if (
        not _sealed(prepared)
        or prepared.get("digest") != formation_digest
        or formation_digest != receipt.get("prepared_formation_digest")
        or receipt.get("controlled_agentteams_formation_receipt_digest")
        != formation_receipt_digest
        or len(formation_receipts) != 1
        or not isinstance(formation_receipts[0], dict)
        or not _sealed(formation_receipts[0])
        or formation_receipts[0].get("digest") != formation_receipt_digest
        or formation_receipts[0].get("run_id") != shadow_run_id
        or formation_receipts[0].get("canonical_target_writes") != 0
    ):
        fail("FORMATION_BINDING_INVALID")

    if not isinstance(exports, list) or len(exports) != 3:
        fail("OTLP_EXPORT_RECEIPTS_INVALID")
        exports = []
    export_by_signal: dict[str, dict[str, Any]] = {}
    for export in exports:
        operation = str(export.get("operation") or "") if isinstance(export, dict) else ""
        signal = operation.removeprefix("EXPORT_").lower()
        if (
            not isinstance(export, dict)
            or not _sealed(export)
            or export.get("run_id") != shadow_run_id
            or export.get("status") != "SUCCEEDED"
            or export.get("http_status") != 200
            or export.get("target_writes") != 0
            or signal not in {"traces", "logs", "metrics"}
        ):
            fail("OTLP_EXPORT_RECEIPT_INVALID")
            continue
        export_by_signal[signal] = export
    observed_layers: set[str] = set()
    observed_binding_values: set[str] = set()
    trace_ids: set[str] = set()
    for signal in ("traces", "logs", "metrics"):
        try:
            payload = _load(base / f"observability/{signal}.otlp.json")
        except (OSError, ValueError, json.JSONDecodeError):
            fail("OTLP_PAYLOAD_LOAD_FAILED")
            continue
        export = export_by_signal.get(signal, {})
        if export.get("request_digest") != _digest(payload):
            fail("OTLP_PAYLOAD_EXPORT_BINDING_INVALID")
        for node in _walk(payload):
            trace_id = node.get("traceId")
            if isinstance(trace_id, str) and trace_id:
                trace_ids.add(trace_id)
        for attrs in _attributes(payload):
            layer = attrs.get("orgrebase.chain.layer")
            if isinstance(layer, str):
                observed_layers.add(layer)
            observed_binding_values.update(str(item) for item in attrs.values())
    required_otlp_bindings = {
        str(shadow_run_id),
        str(receipt.get("adapter_capsule_digest")),
        str(receipt.get("activation_binding_digest")),
        str(receipt.get("context_envelope_digest")),
        str(receipt.get("tool_receipt_digest")),
        str(receipt.get("skill_invocation_receipt_digest")),
        str(receipt.get("prepared_formation_digest")),
    }
    if (
        observed_layers != set(_OTLP_TRACE_LAYERS)
        or len(trace_ids) != 1
        or not required_otlp_bindings.issubset(observed_binding_values)
        or receipt.get("same_run_layers") != list(_SAME_RUN_PROOF_LAYERS)
        or receipt.get("otlp_trace_layers") != list(_OTLP_TRACE_LAYERS)
    ):
        fail("OTLP_SAME_RUN_CAUSAL_BINDING_INVALID")
    if (
        not _sealed(query)
        or query.get("query", {}).get("run_id") != shadow_run_id
        or query.get("count") != 3
        or set(query.get("matched_payload_digests", []))
        != {item.get("request_digest") for item in exports}
        or query.get("digest") != receipt.get("telemetry_query_receipt_digest")
    ):
        fail("OTLP_QUERY_RECEIPT_INVALID")
    if (
        not _sealed(alert)
        or alert.get("run_id") != shadow_run_id
        or alert.get("status") != "PASS"
        or alert.get("alerts") != []
        or alert.get("required_layers") != list(_OTLP_TRACE_LAYERS)
        or set(alert.get("observed_layers", [])) != set(_OTLP_TRACE_LAYERS)
        or alert.get("digest") != receipt.get("telemetry_alert_receipt_digest")
    ):
        fail("OTLP_ALERT_RECEIPT_INVALID")
    try:
        connection = sqlite3.connect(base / "observability/telemetry.sqlite3")
        rows = connection.execute(
            "SELECT signal,payload_digest FROM otlp_ingestions ORDER BY signal"
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        rows = []
    if (
        len(rows) != 3
        or {row[0] for row in rows} != {"traces", "logs", "metrics"}
        or {row[1] for row in rows}
        != {item.get("request_digest") for item in exports}
        or receipt.get("telemetry_ingestion_count") != 3
    ):
        fail("OTLP_SQLITE_BACKEND_INVALID")

    baseline = receipt.get("frozen_golden_baseline")
    if not isinstance(baseline, dict):
        fail("FROZEN_GOLDEN_BASELINE_MISSING")
        baseline = {}
    try:
        current_golden_summary = _load(golden_root / "golden-run/summary.json")
        golden_files = {
            relative: _file_digest(golden_root / relative)
            for relative in _FROZEN_FILES
        }
    except (OSError, ValueError, json.JSONDecodeError):
        current_golden_summary = {}
        golden_files = {}
    if (
        not _sealed(current_golden_summary)
        or baseline.get("files") != golden_files
        or baseline.get("run_id") != current_golden_summary.get("run_id")
        or baseline.get("run_id") != golden_run_id
        or baseline.get("summary_content_digest")
        != current_golden_summary.get("digest")
    ):
        fail("FROZEN_GOLDEN_BASELINE_CHANGED")

    try:
        fencing = _load(fencing_path)
    except (OSError, ValueError, json.JSONDecodeError):
        fencing = {}
    reference = receipt.get("late_attempt_fencing_reference")
    decisions = fencing.get("control_decisions") or []
    fenced = next(
        (
            item
            for item in decisions
            if isinstance(item, dict)
            and item.get("decision_digest")
            == (reference or {}).get("decision_digest")
            and item.get("verdict") == "REJECT"
            and item.get("target_writes") == 0
            and "STALE_ATTEMPT_FENCED" in item.get("reason_codes", [])
        ),
        None,
    )
    if (
        not isinstance(reference, dict)
        or reference.get("mode") != "CONTROL_MECHANISM_REFERENCE_NOT_SAME_RUN"
        or reference.get("source_run_id") != fencing.get("run_id")
        or reference.get("source_run_id") == shadow_run_id
        or reference.get("file_digest") != _file_digest(fencing_path)
        or fenced is None
    ):
        fail("LATE_ATTEMPT_FENCING_REFERENCE_INVALID")

    manifest = receipt.get("output_manifest")
    actual_manifest = {
        path.relative_to(base).as_posix(): _file_digest(path)
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.name not in {"receipt.json", "verification.json"}
    }
    if not isinstance(manifest, dict) or manifest != actual_manifest:
        fail("OUTPUT_MANIFEST_INVALID")
    if (
        receipt.get("candidate_only") is not True
        or receipt.get("canonical_target_writes") != 0
        or receipt.get("shadow_terminal_status")
        != "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
        or receipt.get("oac_plan_produced") is not False
        or receipt.get("oac_plan_certificate_produced") is not False
        or receipt.get("oac_runtime_invoked") is not False
        or receipt.get("production_claimed") is not False
    ):
        fail("CLAIM_BOUNDARY_INVALID")
    for value in (
        receipt,
        pre,
        inputs,
        summary,
        envelope,
        tool,
        skill,
        prepared,
    ):
        serialized = json.dumps(value, ensure_ascii=False)
        if "ORGREBASE_CANARY_SECRET_" in serialized or ("/" + "Users/") in serialized:
            fail("SECRET_OR_MACHINE_PATH_DISCLOSURE")
            break

    result = {
        "schema_version": "orgrebase.oac-bound-shadow-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "shadow_execution_run_id": shadow_run_id,
        "failure_count": len(failures),
        "failures": failures,
        "checked_output_file_count": len(actual_manifest),
        "checked_pack_file_count": len(actual_pack_files),
        "canonical_target_writes": receipt.get("canonical_target_writes"),
    }
    result["digest"] = _digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--frozen-golden", required=True)
    parser.add_argument("--fencing-reference", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = verify_oac_bound_shadow_execution(
        root=args.root,
        pack=args.pack,
        frozen_golden=args.frozen_golden,
        fencing_reference=args.fencing_reference,
    )
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
