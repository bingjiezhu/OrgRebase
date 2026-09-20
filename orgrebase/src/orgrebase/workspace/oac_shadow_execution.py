"""Exact-bound, candidate-only OAC shadow execution.

This module deliberately sits above the OAC adaptation contracts and below no
product service.  It validates an already approved adapter capsule, adds one
immutable context-envelope binding, invokes the existing controlled-local
competition runner with a fresh execution run ID, and projects that *actual*
run through a loopback OTLP/HTTP receiver backed by SQLite.

The shadow run prepares a candidate Formation bundle only.  It never calls the
canonical commit or promotion paths.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from orgrebase.digest import sha256_digest, verify_content_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.competition_run import run_golden_competition
from orgrebase.workspace.controlled_local import (
    CAUSAL_LAYER_ORDER,
    OtlpHTTPExporter,
    OtlpHTTPReceiver,
    TelemetryStore,
    build_joint_otlp,
    evaluate_run_alerts,
)
from orgrebase.workspace.oac_agent_adaptation import OACAgentMappingReceipt
from orgrebase.workspace.oac_quote_adaptation import (
    OACAdapterActivationBinding,
    OACAdapterCapsule,
    OACSourceAdmissionApproval,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

OAC_SHADOW_EVIDENCE_CLASS = "CONTROLLED_LOCAL_OAC_BOUND_SHADOW"
OAC_SHADOW_CLAIM_BOUNDARY = (
    "OAC_SOURCE_DEMAND_AND_CONTEXT_BOUND_ORGREBASE_CANDIDATE_EXECUTION_"
    "NOT_OAC_PLAN_NOT_CANONICAL_APPLY_NOT_PRODUCTION"
)
_DIGEST_PREFIX = "sha256:"
_FROZEN_BASELINE_FILES = (
    "golden-run/summary.json",
    "manifest.json",
    "verification.json",
)
_SAME_RUN_PROOF_LAYERS = (
    "SOURCE",
    "CONTEXT",
    "AGENTTEAMS",
    "TOOL",
    "SKILL",
    "OTLP",
    "CANDIDATE",
)


class OACShadowExecutionError(RuntimeError):
    """Stable fail-closed error for the OAC-bound shadow slice."""


class OACShadowPreExecutionBinding(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-shadow-pre-execution-binding.v1"] = (
        "orgrebase.oac-shadow-pre-execution-binding.v1"
    )
    binding_timing: Literal["PRE_EXECUTION_EXACT_BINDING"] = "PRE_EXECUTION_EXACT_BINDING"
    adaptation_run_id: str = Field(min_length=1)
    shadow_execution_run_id: str = Field(min_length=1)
    frozen_golden_run_id: str = Field(min_length=1)
    approval_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_gate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    adapter_capsule_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    activation_binding_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_file_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_envelope_ref: str = Field(min_length=1)
    context_envelope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_freshness_basis: Literal["WALL_CLOCK", "LOGICAL_EVENT_TIME"]
    context_freshness_checked_at_epoch_ms: int = Field(ge=0)
    context_freshness_checked_at: str = Field(min_length=1)
    agent_mapping_receipt_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = "ZERO_EXTERNAL_EFFECTS"
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0


class OACBoundShadowExecutionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-bound-shadow-execution-receipt.v1"] = (
        "orgrebase.oac-bound-shadow-execution-receipt.v1"
    )
    status: Literal["PASS"] = "PASS"
    evidence_class: Literal["CONTROLLED_LOCAL_OAC_BOUND_SHADOW"] = OAC_SHADOW_EVIDENCE_CLASS
    claim_boundary: Literal[
        "OAC_SOURCE_DEMAND_AND_CONTEXT_BOUND_ORGREBASE_CANDIDATE_EXECUTION_NOT_OAC_PLAN_NOT_CANONICAL_APPLY_NOT_PRODUCTION"
    ] = OAC_SHADOW_CLAIM_BOUNDARY
    adaptation_run_id: str = Field(min_length=1)
    shadow_execution_run_id: str = Field(min_length=1)
    frozen_golden_run_id: str = Field(min_length=1)
    approval_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approval_review_gate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_gate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_duration_ms: int = Field(ge=4000)
    approval_elapsed_since_not_before_ms: int = Field(ge=0)
    adapter_capsule_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    activation_binding_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_envelope_ref: str = Field(min_length=1)
    context_envelope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_freshness_basis: Literal["WALL_CLOCK", "LOGICAL_EVENT_TIME"]
    context_freshness_checked_at_epoch_ms: int = Field(ge=0)
    context_freshness_checked_at: str = Field(min_length=1)
    agent_mapping_receipt_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pack_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pre_execution_binding_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_envelope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    competition_summary_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    agentteams_action_count: int = Field(gt=0)
    agentteams_action_digests: tuple[str, ...] = Field(min_length=1)
    tool_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    skill_package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    skill_invocation_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prepared_formation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    controlled_agentteams_formation_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    agentteams_execution_plan_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    planned_domain_ids: tuple[str, ...] = ()
    actual_agentteams_domain_ids: tuple[str, ...] = ()
    topology_match: bool | None = None
    otlp_export_receipt_digests: tuple[str, str, str]
    telemetry_query_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    telemetry_alert_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    telemetry_ingestion_count: Literal[3] = 3
    same_run_layers: tuple[
        Literal[
            "SOURCE",
            "CONTEXT",
            "AGENTTEAMS",
            "TOOL",
            "SKILL",
            "OTLP",
            "CANDIDATE",
        ],
        ...,
    ]
    otlp_trace_layers: tuple[Literal["SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "TERMINAL"], ...]
    shadow_terminal_status: Literal["CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"] = (
        "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
    )
    frozen_golden_baseline: dict[str, Any]
    late_attempt_fencing_reference: dict[str, Any]
    pack_file_manifest: dict[str, str]
    output_manifest: dict[str, str]
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0
    oac_plan_produced: Literal[False] = False
    oac_plan_certificate_produced: Literal[False] = False
    oac_runtime_invoked: Literal[False] = False
    production_claimed: Literal[False] = False

    @model_validator(mode="after")
    def verify_runtime_topology_binding(self) -> OACBoundShadowExecutionReceipt:
        if self.agentteams_execution_plan_digest is None:
            if (
                self.planned_domain_ids
                or self.actual_agentteams_domain_ids
                or self.topology_match is not None
            ):
                raise ValueError("OAC_SHADOW_PLAN_TOPOLOGY_BINDING_INCOMPLETE")
            return self
        if (
            self.planned_domain_ids != tuple(sorted(set(self.planned_domain_ids)))
            or self.actual_agentteams_domain_ids != tuple(sorted(set(self.actual_agentteams_domain_ids)))
            or self.planned_domain_ids != self.actual_agentteams_domain_ids
            or self.topology_match is not True
        ):
            raise ValueError("OAC_SHADOW_PLAN_TOPOLOGY_BINDING_INVALID")
        return self


def _raw_file_digest(path: Path) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OACShadowExecutionError(f"OAC_SHADOW_JSON_LOAD_FAILED:{path.name}") from exc
    if not isinstance(value, dict):
        raise OACShadowExecutionError(f"OAC_SHADOW_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _json_object(value: Mapping[str, Any] | BaseModel | str | Path) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        selected: Any = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        selected = dict(value)
    else:
        selected = _read_object(Path(value).expanduser().resolve())
    return json.loads(json.dumps(selected, ensure_ascii=False))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OACShadowExecutionError(code)
    try:
        selected = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OACShadowExecutionError(code) from exc
    if selected.tzinfo is None or selected.utcoffset() is None:
        raise OACShadowExecutionError(code)
    return selected.astimezone(UTC)


def _epoch_ms(value: datetime) -> int:
    return math.floor(value.timestamp() * 1000)


def _freshness_observation(
    context: Mapping[str, Any],
    *,
    context_validation_time: str | None,
    wall_clock: Callable[[], float],
) -> dict[str, Any]:
    created_at = _parse_timestamp(context.get("created_at"), "OAC_SHADOW_CONTEXT_CREATED_AT_INVALID")
    expires_at = _parse_timestamp(context.get("expires_at"), "OAC_SHADOW_CONTEXT_EXPIRES_AT_INVALID")
    if context_validation_time is None:
        observed = float(wall_clock())
        if not math.isfinite(observed):
            raise OACShadowExecutionError("OAC_SHADOW_CONTEXT_WALL_CLOCK_INVALID")
        checked_at = datetime.fromtimestamp(observed, UTC)
        basis = "WALL_CLOCK"
    else:
        checked_at = _parse_timestamp(
            context_validation_time,
            "OAC_SHADOW_CONTEXT_VALIDATION_TIME_INVALID",
        )
        basis = "LOGICAL_EVENT_TIME"
    if checked_at < created_at:
        raise OACShadowExecutionError("OAC_SHADOW_CONTEXT_NOT_YET_VALID")
    if checked_at >= expires_at:
        raise OACShadowExecutionError("OAC_SHADOW_CONTEXT_EXPIRED")
    checked_epoch_ms = _epoch_ms(checked_at)
    return {
        "basis": basis,
        "checked_at_epoch_ms": checked_epoch_ms,
        "checked_at": (
            datetime.fromtimestamp(checked_epoch_ms / 1000, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        ),
    }


def _require_content_addressed(value: Mapping[str, Any], code: str) -> str:
    digest = value.get("digest")
    if not isinstance(digest, str) or not verify_content_digest(value, digest):
        raise OACShadowExecutionError(code)
    return digest


def _directory_manifest(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise OACShadowExecutionError("OAC_SHADOW_PACK_DIRECTORY_REQUIRED")
    return {
        path.relative_to(root).as_posix(): _raw_file_digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _frozen_golden_baseline(root: Path) -> dict[str, Any]:
    files = {relative: _raw_file_digest(root / relative) for relative in _FROZEN_BASELINE_FILES}
    summary = _read_object(root / "golden-run" / "summary.json")
    summary_digest = _require_content_addressed(summary, "OAC_SHADOW_FROZEN_GOLDEN_SUMMARY_DIGEST_INVALID")
    verification = _read_object(root / "verification.json")
    return {
        "root_role": "FROZEN_GOLDEN_REFERENCE_NOT_SHADOW_RUNTIME",
        "run_id": summary.get("run_id"),
        "summary_content_digest": summary_digest,
        "verification_entry_count": verification.get("entry_count"),
        "files": files,
    }


def _late_attempt_fencing_reference(path: Path, shadow_run_id: str) -> dict[str, Any]:
    value = _read_object(path)
    decisions = value.get("control_decisions")
    if not isinstance(decisions, list):
        raise OACShadowExecutionError("OAC_SHADOW_FENCING_DECISIONS_REQUIRED")
    fenced = next(
        (
            item
            for item in decisions
            if isinstance(item, Mapping)
            and item.get("verdict") == "REJECT"
            and "STALE_ATTEMPT_FENCED" in item.get("reason_codes", [])
            and item.get("target_writes") == 0
        ),
        None,
    )
    if fenced is None or value.get("run_id") == shadow_run_id:
        raise OACShadowExecutionError("OAC_SHADOW_FENCING_REFERENCE_INVALID")
    return {
        "mode": "CONTROL_MECHANISM_REFERENCE_NOT_SAME_RUN",
        "source_run_id": value.get("run_id"),
        "file_digest": _raw_file_digest(path),
        "decision_digest": fenced.get("decision_digest"),
        "reason_code": "STALE_ATTEMPT_FENCED",
        "canonical_target_writes": 0,
    }


def _validate_agent_mapping_receipt(
    value: dict[str, Any] | None,
    *,
    adaptation_run_id: str,
    profile_digest: str,
    pack_digest: str,
    expected_mapping_set_digest: str,
) -> str | None:
    if value is None:
        return None
    try:
        selected = OACAgentMappingReceipt.model_validate(value)
    except ValueError as exc:
        raise OACShadowExecutionError("OAC_SHADOW_AGENT_MAPPING_RECEIPT_DIGEST_INVALID") from exc
    observation = selected.model_observation
    if (
        selected.status != "VALIDATED_CANDIDATE"
        or selected.mapping_mode != "LIVE_AGENT_ATTEMPT"
        or selected.adaptation_run_id != adaptation_run_id
        or selected.profile_digest != profile_digest
        or selected.pack_digest != pack_digest
        or selected.accepted_mapping_set_digest != expected_mapping_set_digest
        or selected.candidate_only is not True
        or selected.canonical_target_writes != 0
        or selected.effect_ceiling != "ZERO_EXTERNAL_EFFECTS"
        or selected.human_approval_granted is not False
        or selected.oac_source_admitted is not False
        or observation.evidence_class != "LIVE_MODEL"
        or not observation.provider_request_id
        or len(selected.accepted_mappings) != 5
    ):
        raise OACShadowExecutionError("OAC_SHADOW_AGENT_MAPPING_RECEIPT_INVALID")
    return selected.digest


def _validate_shadow_runtime(
    root: Path,
    *,
    run_id: str,
    context_digest: str,
) -> dict[str, Any]:
    summary = _read_object(root / "summary.json")
    if (
        _require_content_addressed(summary, "OAC_SHADOW_COMPETITION_SUMMARY_DIGEST_INVALID")
        != summary.get("digest")
        or summary.get("status") != "PASS"
        or summary.get("run_id") != run_id
        or summary.get("context_envelope_digest") != context_digest
        or summary.get("canonical_target_writes") != 0
        or summary.get("external_promotion_status") != "NOT_RUN"
        or summary.get("project_terminal_state") != "completed"
    ):
        raise OACShadowExecutionError("OAC_SHADOW_COMPETITION_BOUNDARY_INVALID")
    envelope = _read_object(root / "execution-envelope.json")
    if (
        _require_content_addressed(envelope, "OAC_SHADOW_EXECUTION_ENVELOPE_DIGEST_INVALID")
        != summary.get("execution_envelope_digest")
        or envelope.get("run_id") != run_id
        or envelope.get("context_envelope_digest") != context_digest
    ):
        raise OACShadowExecutionError("OAC_SHADOW_EXECUTION_ENVELOPE_BINDING_INVALID")
    bindings = summary.get("task_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise OACShadowExecutionError("OAC_SHADOW_TASK_BINDINGS_REQUIRED")
    plan_digest = summary.get("agentteams_execution_plan_digest")
    plan_bound = plan_digest is not None
    if plan_bound and (
        not isinstance(plan_digest, str)
        or summary.get("topology_match") is not True
        or summary.get("planned_domain_ids") != summary.get("actual_agentteams_domain_ids")
        or not isinstance(summary.get("planned_domain_ids"), list)
        or summary.get("planned_domain_ids") != sorted(set(summary["planned_domain_ids"]))
    ):
        raise OACShadowExecutionError("OAC_SHADOW_AGENTTEAMS_TOPOLOGY_INVALID")
    for binding in bindings:
        if (
            not isinstance(binding, Mapping)
            or binding.get("run_id") != run_id
            or binding.get("context_envelope_digest") != context_digest
            or binding.get("candidate_only") is not True
            or binding.get("target_writes") != 0
            or (
                plan_bound
                and (
                    binding.get("agentteams_execution_plan_digest") != plan_digest
                    or not binding.get("logical_plan_task_id")
                    or not binding.get("logical_plan_task_digest")
                    or not binding.get("formation_receipt_id")
                    or not binding.get("formation_receipt_digest")
                )
            )
        ):
            raise OACShadowExecutionError("OAC_SHADOW_TASK_BINDING_INVALID")
    try:
        process_receipts = json.loads((root / "process-receipts.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OACShadowExecutionError("OAC_SHADOW_PROCESS_RECEIPTS_LOAD_FAILED") from exc
    if not isinstance(process_receipts, list) or not process_receipts:
        raise OACShadowExecutionError("OAC_SHADOW_PROCESS_RECEIPTS_REQUIRED")
    for process_receipt in process_receipts:
        if (
            not isinstance(process_receipt, Mapping)
            or process_receipt.get("run_id") != run_id
            or process_receipt.get("context_envelope_digest") != context_digest
            or process_receipt.get("canonical_target_writes") != 0
            or (
                plan_bound
                and (
                    process_receipt.get("agentteams_execution_plan_digest") != plan_digest
                    or not process_receipt.get("logical_plan_task_digest")
                    or not process_receipt.get("formation_receipt_digest")
                )
            )
            or not verify_content_digest(process_receipt, str(process_receipt.get("digest")))
        ):
            raise OACShadowExecutionError("OAC_SHADOW_PROCESS_CONTEXT_BINDING_INVALID")
    tool = _read_object(root / "tool" / "invocation.json")
    tool_receipt = tool.get("receipt")
    if (
        tool.get("run_id") != run_id
        or tool.get("candidate_only") is not True
        or tool.get("target_writes") != 0
        or not isinstance(tool_receipt, Mapping)
        or tool_receipt.get("run_id") != run_id
        or tool_receipt.get("digest") != summary.get("tool_receipt_digest")
        or not verify_content_digest(tool_receipt, str(tool_receipt.get("digest")))
        or tool_receipt.get("status") != "SUCCEEDED"
        or tool_receipt.get("target_writes") != 0
    ):
        raise OACShadowExecutionError("OAC_SHADOW_TOOL_BINDING_INVALID")
    skill = _read_object(root / "skill" / "receipt.json")
    if (
        skill.get("run_id") != run_id
        or skill.get("digest") != summary.get("skill_invocation_receipt_digest")
        or not verify_content_digest(skill, str(skill.get("digest")))
        or skill.get("target_writes") != 0
        or skill.get("authorization_mode") != "RELEASE"
    ):
        raise OACShadowExecutionError("OAC_SHADOW_SKILL_BINDING_INVALID")
    prepared = _read_object(root / "prepared-formation-bundle.json")
    if prepared.get("digest") != summary.get("prepared_formation_digest") or not verify_content_digest(
        prepared, str(prepared.get("digest"))
    ):
        raise OACShadowExecutionError("OAC_SHADOW_FORMATION_BINDING_INVALID")
    return {
        "summary": summary,
        "execution_envelope": envelope,
        "tool": tool,
        "skill": skill,
        "prepared": prepared,
    }


def _emit_same_run_otlp(
    output: Path,
    *,
    runtime_pack: Any,
    pre_binding: OACShadowPreExecutionBinding,
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    summary = runtime["summary"]
    tool = runtime["tool"]
    skill = runtime["skill"]
    prepared = runtime["prepared"]
    run_id = pre_binding.shadow_execution_run_id
    tool_result_digest = sha256_digest(tool["result"])
    bundle = build_joint_otlp(
        run_id=run_id,
        organization_id=runtime_pack.profile.organization_id,
        receipt_digest=pre_binding.digest,
        task_id=str(skill["task_id"]),
        skill_digest=str(skill["package_digest"]),
        layers={
            "SOURCE": "PASS",
            "AGENTTEAMS": "COMPLETED",
            "TOOL": str(tool["receipt"]["status"]),
            "SKILL": str(summary["skill_release_state"]),
            "TERMINAL": "COMPLETED",
        },
        correlation={
            "orgrebase.workflow.nonce": str(summary["nonce"]),
            "orgrebase.agentteams.project.id": str(summary["project_id"]),
            "orgrebase.agentteams.retry.count": 1,
            "orgrebase.agentteams.reassign.count": 0,
            "orgrebase.skill.name": "enterprise-quote-compose",
            "orgrebase.skill.version": str(skill["package_id"]).rsplit("@", 1)[-1],
            "orgrebase.skill.invocation.receipt.digest": str(skill["digest"]),
            "orgrebase.tool.receipt.digest": str(tool["receipt"]["digest"]),
            "orgrebase.tool.result.digest": tool_result_digest,
            "orgrebase.formation.digest": str(prepared["digest"]),
            "orgrebase.oac.adapter_capsule.digest": pre_binding.adapter_capsule_digest,
            "orgrebase.oac.activation_binding.digest": pre_binding.activation_binding_digest,
            "orgrebase.context.envelope.digest": pre_binding.context_envelope_digest,
            "orgrebase.shadow.terminal.status": ("CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"),
            "orgrebase.target.write.count": 0,
        },
        evidence_class=OAC_SHADOW_EVIDENCE_CLASS,
    )
    observability = output / "observability"
    observability.mkdir(parents=True, exist_ok=True)
    store = TelemetryStore(observability / "telemetry.sqlite3")
    export_receipts: list[dict[str, Any]] = []
    try:
        with OtlpHTTPReceiver(store) as receiver:
            exporter = OtlpHTTPExporter(receiver.base_url, token=receiver.token)
            for signal in ("traces", "logs", "metrics"):
                payload = bundle[signal]
                _write_json(observability / f"{signal}.otlp.json", payload)
                receipt = exporter.export(
                    run_id=run_id,
                    signal=signal,
                    payload=payload,
                )
                export_receipts.append(receipt.model_dump(mode="json"))
        records, query_receipt = store.query(run_id=run_id)
        alert_receipt = evaluate_run_alerts(
            store,
            run_id=run_id,
            required_layers=CAUSAL_LAYER_ORDER,
        )
    finally:
        store.close()
    if len(records) != 3 or alert_receipt.status != "PASS":
        raise OACShadowExecutionError("OAC_SHADOW_OTLP_SAME_RUN_QUERY_FAILED")
    _write_json(observability / "export-receipts.json", export_receipts)
    _write_json(
        observability / "query-receipt.json",
        query_receipt.model_dump(mode="json"),
    )
    _write_json(
        observability / "alert-receipt.json",
        alert_receipt.model_dump(mode="json"),
    )
    return {
        "export_receipts": export_receipts,
        "query_receipt": query_receipt.model_dump(mode="json"),
        "alert_receipt": alert_receipt.model_dump(mode="json"),
        "ingestion_count": len(records),
    }


def run_oac_bound_shadow_execution(
    *,
    repo_root: str | Path,
    output_dir: str | Path,
    checkout: str | Path,
    lock_path: str | Path,
    pack_path: str | Path,
    frozen_golden_root: str | Path,
    adapter_capsule: Mapping[str, Any] | BaseModel | str | Path,
    activation_binding: Mapping[str, Any] | BaseModel | str | Path,
    approval: Mapping[str, Any] | BaseModel | str | Path,
    context_envelope: Mapping[str, Any] | BaseModel | str | Path,
    task_formation_decision_receipt: (Mapping[str, Any] | BaseModel | str | Path | None) = None,
    context_validation_time: str | None = None,
    agent_mapping_receipt: Mapping[str, Any] | BaseModel | str | Path | None = None,
    late_attempt_fencing_receipt: str | Path | None = None,
    model_provider: str = "ollama-local",
    ollama_endpoint: str | None = None,
    vertex_project: str | None = None,
    competition_runner: Callable[..., dict[str, Any]] = run_golden_competition,
    wall_clock: Callable[[], float] = time.time,
) -> OACBoundShadowExecutionReceipt:
    """Run one exact-bound OAC shadow without touching canonical state."""

    root = Path(repo_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise OACShadowExecutionError("OAC_SHADOW_OUTPUT_DIR_NOT_EMPTY")
    pack_root = Path(pack_path).expanduser().resolve()
    golden_root = Path(frozen_golden_root).expanduser().resolve()
    fencing_path = (
        Path(
            late_attempt_fencing_receipt
            or root / "evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json"
        )
        .expanduser()
        .resolve()
    )

    capsule_payload = _json_object(adapter_capsule)
    activation_payload = _json_object(activation_binding)
    approval_payload = _json_object(approval)
    context_payload = _json_object(context_envelope)
    formation_decision_payload = (
        _json_object(task_formation_decision_receipt) if task_formation_decision_receipt is not None else None
    )
    mapping_payload = _json_object(agent_mapping_receipt) if agent_mapping_receipt is not None else None
    try:
        capsule = OACAdapterCapsule.model_validate(capsule_payload)
        activation = OACAdapterActivationBinding.model_validate(activation_payload)
        approved = OACSourceAdmissionApproval.model_validate(approval_payload)
    except (ValueError, IntegrityError) as exc:
        raise OACShadowExecutionError("OAC_SHADOW_ADAPTATION_CONTRACT_INVALID") from exc
    review_gate = approved.review_gate
    if review_gate is None:
        raise OACShadowExecutionError("OAC_SHADOW_REVIEW_GATE_REQUIRED")
    raw_review_gate = approval_payload.get("review_gate")
    if (
        not isinstance(raw_review_gate, Mapping)
        or dict(raw_review_gate) != review_gate.model_dump(mode="json")
        or approved.review_gate_digest != review_gate.digest
        or approved.approved_at_epoch_ms < review_gate.not_before_epoch_ms
    ):
        raise OACShadowExecutionError("OAC_SHADOW_REVIEW_GATE_BINDING_INVALID")
    if (
        _epoch_ms(
            _parse_timestamp(
                review_gate.prepared_at,
                "OAC_SHADOW_REVIEW_GATE_PREPARED_AT_INVALID",
            )
        )
        != review_gate.prepared_at_epoch_ms
        or _epoch_ms(
            _parse_timestamp(
                review_gate.not_before,
                "OAC_SHADOW_REVIEW_GATE_NOT_BEFORE_INVALID",
            )
        )
        != review_gate.not_before_epoch_ms
        or _epoch_ms(
            _parse_timestamp(
                approved.approved_at,
                "OAC_SHADOW_APPROVED_AT_INVALID",
            )
        )
        != approved.approved_at_epoch_ms
    ):
        raise OACShadowExecutionError("OAC_SHADOW_REVIEW_GATE_TIME_INVALID")
    context_digest = _require_content_addressed(context_payload, "OAC_SHADOW_CONTEXT_ENVELOPE_DIGEST_INVALID")
    if (
        context_payload.get("effect_ceiling") != "ZERO_EXTERNAL_EFFECTS"
        or context_payload.get("candidate_only") is not True
        or context_payload.get("canonical_target_writes") != 0
    ):
        raise OACShadowExecutionError("OAC_SHADOW_CONTEXT_BOUNDARY_INVALID")
    if formation_decision_payload is not None:
        formation_decision_digest = _require_content_addressed(
            formation_decision_payload,
            "OAC_SHADOW_FORMATION_DECISION_DIGEST_INVALID",
        )
        if (
            context_payload.get("task_formation_decision_receipt_digest") != formation_decision_digest
            or formation_decision_payload.get("candidate_only") is not True
            or formation_decision_payload.get("canonical_target_writes") != 0
            or formation_decision_payload.get("effect_ceiling") != "ZERO_EXTERNAL_EFFECTS"
        ):
            raise OACShadowExecutionError("OAC_SHADOW_FORMATION_DECISION_BOUNDARY_INVALID")
    freshness = _freshness_observation(
        context_payload,
        context_validation_time=context_validation_time,
        wall_clock=wall_clock,
    )
    context_ref = str(
        context_payload.get("ref")
        or (
            f"{context_payload.get('id')}@{context_payload.get('version')}"
            if context_payload.get("id") and context_payload.get("version")
            else context_payload.get("id")
        )
        or ""
    )
    if not context_ref:
        raise OACShadowExecutionError("OAC_SHADOW_CONTEXT_REF_REQUIRED")

    runtime_pack = load_enterprise_quote_pilot_pack(pack_root)
    pack_files = _directory_manifest(pack_root)
    frozen_before = _frozen_golden_baseline(golden_root)
    golden_run_id = str(frozen_before.get("run_id") or "")
    shadow_run_id = activation.execution_run_id
    if (
        capsule.adaptation_run_id != activation.adaptation_run_id
        or capsule.adaptation_run_id != approved.adaptation_run_id
        or capsule.approval_digest != approved.digest
        or capsule.mapping_set_digest != approved.candidate_digest
        or capsule.owner_review_summary_digest
        != approved.owner_review_summary_digest
        or review_gate.adaptation_run_id != capsule.adaptation_run_id
        or review_gate.mapping_set_digest != capsule.mapping_set_digest
        or review_gate.owner_review_summary_digest
        != capsule.owner_review_summary_digest
        or review_gate.organization_snapshot_digest != capsule.organization_snapshot_digest
        or review_gate.organizational_demand_digest != capsule.organizational_demand_digest
        or review_gate.profile_digest != capsule.profile_digest
        or review_gate.pack_digest != capsule.pack_digest
        or review_gate.owner_ref != approved.actor_id
        or review_gate.canonical_target_writes != 0
        or activation.adapter_capsule_digest != capsule.digest
        or capsule.profile_digest != activation.profile_digest
        or capsule.profile_digest != runtime_pack.profile.digest
        or capsule.pack_digest != activation.pack_digest
        or capsule.pack_digest != runtime_pack.pack_digest
        or approved.canonical_target_writes != 0
        or capsule.canonical_target_writes != 0
        or activation.canonical_target_writes != 0
        or (
            formation_decision_payload is not None
            and (
                formation_decision_payload.get("organizational_demand_digest")
                != capsule.organizational_demand_digest
                or formation_decision_payload.get("organization_snapshot_digest")
                != capsule.organization_snapshot_digest
            )
        )
        or not golden_run_id
        or shadow_run_id in {capsule.adaptation_run_id, golden_run_id}
    ):
        raise OACShadowExecutionError("OAC_SHADOW_PRE_EXECUTION_BINDING_INVALID")
    mapping_digest = _validate_agent_mapping_receipt(
        mapping_payload,
        adaptation_run_id=capsule.adaptation_run_id,
        profile_digest=capsule.profile_digest,
        pack_digest=capsule.pack_digest,
        expected_mapping_set_digest=capsule.mapping_set_digest,
    )
    pre_binding = OACShadowPreExecutionBinding(
        adaptation_run_id=capsule.adaptation_run_id,
        shadow_execution_run_id=shadow_run_id,
        frozen_golden_run_id=golden_run_id,
        approval_digest=approved.digest,
        review_gate_digest=review_gate.digest,
        adapter_capsule_digest=capsule.digest,
        activation_binding_digest=activation.digest,
        profile_digest=capsule.profile_digest,
        pack_digest=capsule.pack_digest,
        pack_file_manifest_digest=sha256_digest(pack_files),
        context_envelope_ref=context_ref,
        context_envelope_digest=context_digest,
        context_freshness_basis=freshness["basis"],
        context_freshness_checked_at_epoch_ms=freshness["checked_at_epoch_ms"],
        context_freshness_checked_at=freshness["checked_at"],
        agent_mapping_receipt_digest=mapping_digest,
    )
    _write_json(output / "inputs" / "approval.json", approval_payload)
    _write_json(output / "inputs" / "review-gate.json", raw_review_gate)
    _write_json(output / "inputs" / "adapter-capsule.json", capsule_payload)
    _write_json(output / "inputs" / "activation-binding.json", activation_payload)
    _write_json(output / "inputs" / "context-envelope.json", context_payload)
    if formation_decision_payload is not None:
        _write_json(
            output / "inputs" / "task-formation-decision-receipt.json",
            formation_decision_payload,
        )
    if mapping_payload is not None:
        _write_json(output / "inputs" / "agent-mapping-receipt.json", mapping_payload)
    _write_json(
        output / "pre-execution-binding.json",
        pre_binding.model_dump(mode="json"),
    )

    shadow_root = output / "shadow-run"
    runner_summary = competition_runner(
        repo_root=root,
        output_dir=shadow_root,
        checkout=checkout,
        lock_path=lock_path,
        pack_path=pack_root,
        model_provider=model_provider,
        ollama_endpoint=ollama_endpoint,
        vertex_project=vertex_project,
        execution_run_id=shadow_run_id,
        context_envelope_digest=context_digest,
        **(
            {
                "task_formation_decision_receipt": formation_decision_payload,
                "context_envelope": context_payload,
            }
            if formation_decision_payload is not None
            else {}
        ),
    )
    if runner_summary.get("status") != "PASS":
        raise OACShadowExecutionError(f"OAC_SHADOW_COMPETITION_NOT_PASS:{runner_summary.get('status')}")
    if formation_decision_payload is not None and (
        not runner_summary.get("agentteams_execution_plan_digest")
        or runner_summary.get("topology_match") is not True
    ):
        raise OACShadowExecutionError("OAC_SHADOW_EXECUTION_PLAN_NOT_BOUND")
    runtime = _validate_shadow_runtime(
        shadow_root,
        run_id=shadow_run_id,
        context_digest=context_digest,
    )
    if runtime["summary"] != runner_summary:
        raise OACShadowExecutionError("OAC_SHADOW_RUNNER_SUMMARY_FILE_MISMATCH")
    otlp = _emit_same_run_otlp(
        output,
        runtime_pack=runtime_pack,
        pre_binding=pre_binding,
        runtime=runtime,
    )
    frozen_after = _frozen_golden_baseline(golden_root)
    if frozen_after != frozen_before:
        raise OACShadowExecutionError("OAC_SHADOW_FROZEN_GOLDEN_MUTATED")
    fencing_reference = _late_attempt_fencing_reference(fencing_path, shadow_run_id)
    summary = runtime["summary"]
    skill = runtime["skill"]
    output_manifest = {
        path.relative_to(output).as_posix(): _raw_file_digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "receipt.json"
    }
    receipt = OACBoundShadowExecutionReceipt(
        adaptation_run_id=capsule.adaptation_run_id,
        shadow_execution_run_id=shadow_run_id,
        frozen_golden_run_id=golden_run_id,
        approval_digest=approved.digest,
        approval_review_gate_digest=approved.review_gate_digest,
        review_gate_digest=review_gate.digest,
        review_duration_ms=review_gate.review_duration_ms,
        approval_elapsed_since_not_before_ms=(
            approved.approved_at_epoch_ms - review_gate.not_before_epoch_ms
        ),
        adapter_capsule_digest=capsule.digest,
        activation_binding_digest=activation.digest,
        context_envelope_ref=context_ref,
        context_envelope_digest=context_digest,
        context_freshness_basis=freshness["basis"],
        context_freshness_checked_at_epoch_ms=freshness["checked_at_epoch_ms"],
        context_freshness_checked_at=freshness["checked_at"],
        agent_mapping_receipt_digest=mapping_digest,
        profile_digest=capsule.profile_digest,
        pack_digest=capsule.pack_digest,
        pre_execution_binding_digest=pre_binding.digest,
        execution_envelope_digest=summary["execution_envelope_digest"],
        competition_summary_digest=summary["digest"],
        agentteams_action_count=summary["agentteams_action_count"],
        agentteams_action_digests=tuple(summary["agentteams_action_digests"]),
        tool_receipt_digest=summary["tool_receipt_digest"],
        skill_package_digest=skill["package_digest"],
        skill_invocation_receipt_digest=skill["digest"],
        prepared_formation_digest=summary["prepared_formation_digest"],
        controlled_agentteams_formation_receipt_digest=summary[
            "controlled_agentteams_formation_receipt_digest"
        ],
        agentteams_execution_plan_digest=summary.get("agentteams_execution_plan_digest"),
        planned_domain_ids=tuple(summary.get("planned_domain_ids") or ()),
        actual_agentteams_domain_ids=tuple(summary.get("actual_agentteams_domain_ids") or ()),
        topology_match=summary.get("topology_match"),
        otlp_export_receipt_digests=tuple(item["digest"] for item in otlp["export_receipts"]),
        telemetry_query_receipt_digest=otlp["query_receipt"]["digest"],
        telemetry_alert_receipt_digest=otlp["alert_receipt"]["digest"],
        same_run_layers=_SAME_RUN_PROOF_LAYERS,
        otlp_trace_layers=CAUSAL_LAYER_ORDER,
        frozen_golden_baseline=frozen_before,
        late_attempt_fencing_reference=fencing_reference,
        pack_file_manifest=pack_files,
        output_manifest=output_manifest,
    )
    _write_json(output / "receipt.json", receipt.model_dump(mode="json"))
    return receipt


__all__ = (
    "OAC_SHADOW_CLAIM_BOUNDARY",
    "OAC_SHADOW_EVIDENCE_CLASS",
    "OACBoundShadowExecutionReceipt",
    "OACShadowExecutionError",
    "OACShadowPreExecutionBinding",
    "run_oac_bound_shadow_execution",
)
