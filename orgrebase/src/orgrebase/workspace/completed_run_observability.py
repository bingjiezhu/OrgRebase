"""Post-terminal OTLP projection from one trusted Workspace state snapshot.

``WorkspaceService.state()`` remains responsible for canonical storage and raw
event-chain validation. This bounded cross-view projection is not an
independent replacement, realtime instrumentation, or a production backend.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.controlled_local import CONTROLLED_TIMING_CLASS, build_joint_otlp
from orgrebase.workspace.history_codec import business_is_complete

SCHEMA_VERSION = "orgrebase.workspace-completed-run-observability-view.v1"
EVIDENCE_CLASS = "CONTROLLED_LOCAL_POST_TERMINAL_PROJECTION"
CLAIM_BOUNDARY = "TRUSTED_WORKSPACE_STATE_POST_TERMINAL_PROJECTION_NOT_REALTIME_NOT_PRODUCTION_OR_SLA"
LAYER_ORDER = ("SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "APPROVAL", "APPLY", "TERMINAL")
LAYER_STATUSES = {
    # SOURCE means sealed lineage admission, not a realtime enterprise connector.
    "SOURCE": "PASS",
    "AGENTTEAMS": "COMPLETED",
    "TOOL": "SUCCEEDED",
    "SKILL": "CANARY",
    "APPROVAL": "PASS",
    "APPLY": "COMPLETED",
    "TERMINAL": "COMPLETED",
}
_EVENT_TYPES = (
    "WORKSPACE_SEED_LOADED", "OAC_QUOTE_ADAPTATION_PREPARED",
    "OAC_QUOTE_ADAPTER_ADMITTED", "OAC_ADAPTER_ACTIVATION_CONSUMED",
    "WORKSPACE_TASK_COMMITTED", "GOLDEN_COMPETITION_ACCEPTED",
    "WORKSPACE_TASK_INTAKE_BOUND", "TOOL_INVOKED",
    "WORKSPACE_CHANGE_PREVIEWED", "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED", "WORKSPACE_CHANGE_OUTCOME_RECORDED",
    "WORKSPACE_CHANGE_PREVIEWED", "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED", "WORKSPACE_CHANGE_OUTCOME_RECORDED",
)
_PROJECT_RE = re.compile(r"orgrebase-golden-evergreen-quote-[0-9a-f]{8}\Z")
_PACKAGE_RE = re.compile(r"skill-package:enterprise-quote-compose@\d+\.\d+\.\d+\Z")
_SOURCE_SKILL_RE = re.compile(r"skill:enterprise-quote-compose@\d+\.\d+\Z")

def _map(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrityError(code)
    return value


def _digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise IntegrityError(code)
    return value


def _sealed(value: Any, code: str) -> Mapping[str, Any]:
    record = _map(value, code)
    digest = _digest(record.get("digest"), code)
    body = {key: item for key, item in record.items() if key != "digest"}
    if digest != sha256_digest(body):
        raise IntegrityError(code)
    return record


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "digest": sha256_digest(payload)}


def _base(run_id: str | None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "INVALID",
        "completion_binding": None,
        "otlp": None,
        "projection_receipt": None,
        "failures": [],
        "projection_target_writes": 0,
        "evidence_class": EVIDENCE_CLASS,
        "claim_ceiling": EVIDENCE_CLASS,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def _event_binding(state: Mapping[str, Any]) -> dict[str, Any]:
    chain = _map(state.get("event_chain"), "EVENT_CHAIN_MISSING")
    records = chain.get("records")
    if not isinstance(records, list) or chain.get("status") != "PASS":
        raise IntegrityError("EVENT_CHAIN_INVALID")
    observed_types = tuple(
        item.get("event_type") for item in records if isinstance(item, Mapping)
    )
    if observed_types != _EVENT_TYPES:
        raise IntegrityError("EVENT_TYPE_SEQUENCE_INVALID")
    previous = "sha256:" + "0" * 64
    for sequence, item in enumerate(records, start=1):
        record = _map(item, "EVENT_RECORD_INVALID")
        if record.get("sequence_no") != sequence or record.get("previous_digest") != previous:
            raise IntegrityError("EVENT_CHAIN_LINK_INVALID")
        previous = _digest(record.get("event_digest"), "EVENT_DIGEST_INVALID")
    if chain.get("events") != len(records) or chain.get("head_digest") != previous:
        raise IntegrityError("EVENT_CHAIN_HEAD_INVALID")
    scopes = _map(state.get("event_scopes"), "EVENT_SCOPES_MISSING")
    quote_scope = _map(scopes.get("quote_business"), "QUOTE_EVENT_SCOPE_MISSING")
    if not (
        quote_scope.get("status") == "PASS"
        and quote_scope.get("events") == 12
        and quote_scope.get("first_sequence_no") == 5
        and quote_scope.get("last_sequence_no") == 16
        and quote_scope.get("head_digest") == previous
    ):
        raise IntegrityError("QUOTE_EVENT_SCOPE_INVALID")
    return {
        "event_count": len(records),
        "event_head_digest": previous,
        "event_sequence_digest": sha256_digest(records),
        "quote_event_count": 12,
    }


def _lineage_binding(
    state: Mapping[str, Any], run_id: str
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    lineage = _sealed(state.get("enterprise_data_lineage"), "ENTERPRISE_LINEAGE_INVALID")
    source = _map(lineage.get("source"), "ENTERPRISE_SOURCE_MISSING")
    if not (
        lineage.get("status") == "READY"
        and lineage.get("run_id") == run_id
        and lineage.get("read_model_target_writes") == 0
        and lineage.get("data_class") == "SYNTHETIC_FIXTURE"
        and source.get("status") == "ADMITTED_AND_MATCHED"
    ):
        raise IntegrityError("ENTERPRISE_SOURCE_NOT_ADMITTED_AND_MATCHED")
    source_skills = [
        item
        for item in source.get("values", [])
        if isinstance(item, Mapping) and item.get("slot_id") == "quote_compose_skill"
    ]
    if len(source_skills) != 1 or not _SOURCE_SKILL_RE.fullmatch(
        str(source_skills[0].get("value"))
    ):
        raise IntegrityError("SOURCE_QUOTE_COMPOSE_SKILL_INVALID")
    return lineage, {
        "lineage_digest": lineage["digest"],
        "source_data_class": lineage["data_class"],
        "source_status": source["status"],
        "source_pack_digest": _digest(
            source.get("pack_digest"), "SOURCE_PACK_DIGEST_INVALID"
        ),
        "source_skill_ref": source_skills[0]["value"],
    }


def _runtime_binding(
    state: Mapping[str, Any], run_id: str, source: Mapping[str, Any]
) -> dict[str, Any]:
    evidence = _sealed(state.get("competition_evidence"), "AGENTTEAMS_EVIDENCE_INVALID")
    collaboration = _sealed(evidence.get("agent_collaboration"), "COLLABORATION_INVALID")
    plan = _sealed(collaboration.get("orchestration_plan"), "AGENTTEAMS_PLAN_INVALID")
    project_id, tasks = plan.get("project_id"), plan.get("tasks")
    valid_tasks = (
        isinstance(tasks, list)
        and bool(tasks)
        and isinstance(tasks[0], Mapping)
        and tasks[0].get("id") == project_id
        and all(
            isinstance(item, Mapping)
            and item.get("candidate_only") is True
            and item.get("target_writes") == 0
            for item in tasks
        )
        and source["source_pack_digest"] in tasks[0].get("input_refs", [])
    )
    if not (
        evidence.get("status") == "PASS"
        and evidence.get("run_id") == run_id
        and evidence.get("project_terminal_state") == "completed"
        and plan.get("run_id") == run_id
        and plan.get("status") == "COMPLETED"
        and isinstance(project_id, str)
        and _PROJECT_RE.fullmatch(project_id)
        and valid_tasks
    ):
        raise IntegrityError("AGENTTEAMS_CURRENT_RUN_BINDING_INVALID")
    tool = _map(collaboration.get("tool"), "TOOL_RECEIPT_MISSING")
    tool_digest = _digest(tool.get("receipt_digest"), "TOOL_RECEIPT_DIGEST_INVALID")
    revisions = plan.get("plan_revisions")
    if not (
        tool.get("status") == "SUCCEEDED"
        and tool.get("target_writes") == 0
        and isinstance(revisions, list)
        and revisions
        and isinstance(revisions[-1], Mapping)
        and revisions[-1].get("tool_receipt_digest") == tool_digest
    ):
        raise IntegrityError("TOOL_CURRENT_PLAN_BINDING_INVALID")
    skill = _map(collaboration.get("skill"), "SKILL_RECEIPT_MISSING")
    package_id = skill.get("package_id")
    source_name = str(source["source_skill_ref"]).removeprefix("skill:").split("@", 1)[0]
    package_name = str(package_id).removeprefix("skill-package:").split("@", 1)[0]
    if not (
        isinstance(package_id, str)
        and _PACKAGE_RE.fullmatch(package_id)
        and package_name == source_name
        and skill.get("status") == "SUCCESS"
        and skill.get("authorization_mode") == "RELEASE"
        and skill.get("release_state") == "CANARY"
        and skill.get("target_writes") == 0
    ):
        raise IntegrityError("QUOTE_COMPOSE_SKILL_BINDING_INVALID")
    execution = _map(state.get("execution"), "EXECUTION_MISSING")
    oac_lineage = _map(execution.get("oac_agentteams_lineage"), "OAC_AT_LINEAGE_MISSING")
    if not (
        oac_lineage.get("status") == "OAC_BOUND_EXECUTION_PLAN_REALIZED"
        and evidence.get("agentteams_execution_plan_digest")
        == oac_lineage.get("agentteams_execution_plan_digest")
    ):
        raise IntegrityError("OAC_AGENTTEAMS_LINEAGE_INVALID")
    return {
        "agentteams_receipt_digest": evidence["digest"],
        "agentteams_project_id": project_id,
        "agentteams_plan_digest": plan["digest"],
        "collaboration_digest": collaboration["digest"],
        "tool_receipt_digest": tool_digest,
        "skill_package_id": package_id,
        "skill_package_digest": _digest(
            skill.get("package_digest"), "SKILL_PACKAGE_DIGEST_INVALID"
        ),
        "skill_receipt_digest": _digest(
            skill.get("receipt_digest"), "SKILL_RECEIPT_DIGEST_INVALID"
        ),
    }


def _business_binding(
    state: Mapping[str, Any], archive: Mapping[str, Any], lineage: Mapping[str, Any]
) -> dict[str, Any]:
    record = _map(archive.get("record"), "ARCHIVE_RECORD_MISSING")
    if not (
        archive.get("status") == "ARCHIVED"
        and archive.get("failures") == []
        and record.get("run_id") == archive.get("run_id")
        and record.get("canonical_authority") == "ORGREBASE_CONTROL_PLANE"
        and record.get("human_approval_count") == 2
    ):
        raise IntegrityError("ARCHIVE_GATE_INVALID")
    receipts, changes = record.get("selective_rebase_receipts"), lineage.get("changes")
    if not (
        isinstance(receipts, list)
        and isinstance(changes, list)
        and len(receipts) == len(changes) == 2
    ):
        raise IntegrityError("BUSINESS_CHANGE_SET_INVALID")
    summaries = []
    for receipt, change, (kind, version) in zip(
        receipts, changes, (("launch_date", "v2"), ("currency", "v3")), strict=True
    ):
        if not (
            isinstance(receipt, Mapping)
            and isinstance(change, Mapping)
            and receipt.get("kind") == change.get("kind") == kind
            and receipt.get("owner_id") == change.get("owner_id")
            and receipt.get("preview_digest") == change.get("preview_digest")
            and receipt.get("rebase_receipt_digest") == change.get("receipt_digest")
            and receipt.get("workspace_receipt_digest")
            == change.get("workspace_receipt_digest")
            and receipt.get("successor_quote_version") == version
            and isinstance(change.get("successor_ref"), str)
            and change["successor_ref"].endswith(f"@{version}")
            and change.get("status") == change.get("receipt_status") == "COMPLETED"
        ):
            raise IntegrityError("BUSINESS_CHANGE_BINDING_INVALID")
        summaries.append(dict(receipt))
    quote = _map(state.get("quote"), "FINAL_QUOTE_MISSING")
    quote_ref = f"{quote.get('id')}@{quote.get('version')}"
    archive_quote = _map(record.get("quote"), "ARCHIVE_QUOTE_MISSING")
    versions = _map(lineage.get("quotes"), "LINEAGE_QUOTES_MISSING").get("versions")
    current = versions[-1] if isinstance(versions, list) and versions else {}
    if not isinstance(current, Mapping):
        raise IntegrityError("LINEAGE_CURRENT_QUOTE_INVALID")
    if not (
        quote_ref.endswith("@v3")
        and archive_quote.get("ref") == current.get("ref") == quote_ref
        and archive_quote.get("digest") == current.get("digest") == quote.get("digest")
        and current.get("version") == "v3"
        and current.get("state") == "CURRENT"
        and current.get("payload") == quote.get("payload")
    ):
        raise IntegrityError("FINAL_QUOTE_LINEAGE_BINDING_INVALID")
    return {
        "approval_apply_summaries": summaries,
        "final_quote_ref": quote_ref,
        "final_quote_digest": _digest(
            quote.get("digest"), "FINAL_QUOTE_DIGEST_INVALID"
        ),
        "final_quote_payload_digest": sha256_digest(quote["payload"]),
    }


def _completion_binding(
    state: Mapping[str, Any], archive: Mapping[str, Any]
) -> dict[str, Any]:
    execution = _map(state.get("execution"), "EXECUTION_MISSING")
    run_id = execution.get("run_id")
    if (
        not business_is_complete(state)
        or not isinstance(run_id, str)
        or archive.get("run_id") != run_id
    ):
        raise IntegrityError("CURRENT_TERMINAL_RUN_INVALID")
    intake = _map(state.get("task_intake"), "TASK_INTAKE_MISSING")
    candidate = _map(intake.get("candidate_receipt"), "TASK_CANDIDATE_MISSING")
    task = _map(candidate.get("task_request"), "TASK_REQUEST_MISSING")
    if intake.get("run_id") != run_id or intake.get("status") != "FORMATION_COMPLETED":
        raise IntegrityError("TASK_INTAKE_RUN_BINDING_INVALID")
    organization_id = task.get("organization_id")
    if not isinstance(organization_id, str) or not organization_id:
        raise IntegrityError("TASK_ORGANIZATION_ID_INVALID")
    lineage, source = _lineage_binding(state, run_id)
    payload = {
        "schema_version": "orgrebase.workspace-completion-binding.v1",
        "run_id": run_id,
        "organization_id": organization_id,
        **source,
        **_runtime_binding(state, run_id, source),
        **_business_binding(state, archive, lineage),
        **_event_binding(state),
        "projection_target_writes": 0,
        "evidence_class": EVIDENCE_CLASS,
        "claim_boundary": CLAIM_BOUNDARY,
    }
    return _seal(payload)


def _projection(binding: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    package = str(binding["skill_package_id"]).removeprefix("skill-package:")
    skill_name, skill_version = package.split("@", 1)
    otlp = build_joint_otlp(
        run_id=str(binding["run_id"]),
        organization_id=str(binding["organization_id"]),
        receipt_digest=str(binding["digest"]),
        task_id=str(binding["agentteams_project_id"]),
        skill_digest=str(binding["skill_package_digest"]),
        layers=LAYER_STATUSES,
        correlation={
            "orgrebase.agentteams.project.id": str(binding["agentteams_project_id"]),
            "orgrebase.skill.name": skill_name,
            "orgrebase.skill.version": skill_version,
            "orgrebase.skill.invocation.receipt.digest": str(binding["skill_receipt_digest"]),
            "orgrebase.tool.receipt.digest": str(binding["tool_receipt_digest"]),
            "orgrebase.enterprise.lineage.digest": str(binding["lineage_digest"]),
            "orgrebase.final.quote.digest": str(binding["final_quote_digest"]),
            "orgrebase.target.write.count": 0,
        },
        evidence_class=EVIDENCE_CLASS,
    )
    receipt = _seal(
        {
            "schema_version": "orgrebase.workspace-observability-projection-receipt.v1",
            "status": "PASS",
            "run_id": binding["run_id"],
            "completion_binding_digest": binding["digest"],
            "otlp_digest": sha256_digest(otlp),
            "layer_order": list(LAYER_ORDER),
            "layer_statuses": dict(LAYER_STATUSES),
            "timing_class": CONTROLLED_TIMING_CLASS,
            "projection_target_writes": 0,
            "evidence_class": EVIDENCE_CLASS,
            "claim_boundary": CLAIM_BOUNDARY,
            "realtime_observation_claimed": False,
            "production_backend_claimed": False,
            "production_sla_claimed": False,
        }
    )
    return otlp, receipt


def build_completed_run_observability_from_trusted_state(
    trusted_state: Mapping[str, Any], trusted_archive: Mapping[str, Any]
) -> dict[str, Any]:
    """Build from one already validated ``WorkspaceService.state()`` snapshot."""

    if trusted_state.get("schema_version") == "orgrebase.workspace-state.v2":
        from orgrebase.workspace.current_completion_observability import (
            build_current_completion_observability,
        )
        return build_current_completion_observability(trusted_state, trusted_archive)

    execution = trusted_state.get("execution") if isinstance(trusted_state, Mapping) else None
    run_id = execution.get("run_id") if isinstance(execution, Mapping) else None
    base = _base(run_id if isinstance(run_id, str) else None)
    if not business_is_complete(trusted_state) and trusted_archive.get("status") == "PENDING":
        return {**base, "status": "PENDING"}
    try:
        binding = _completion_binding(trusted_state, trusted_archive)
        otlp, receipt = _projection(binding)
    except (AttributeError, IntegrityError, KeyError, TypeError, ValueError) as exc:
        return {**base, "failures": [str(exc)]}
    return {
        **base,
        "status": "PROJECTED",
        "completion_binding": binding,
        "otlp": otlp,
        "projection_receipt": receipt,
    }


def verify_completed_run_observability_against_trusted_state(
    trusted_state: Mapping[str, Any],
    trusted_archive: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild from trusted inputs and require exact candidate equality."""

    expected = build_completed_run_observability_from_trusted_state(
        trusted_state, trusted_archive
    )
    if expected.get("status") != "PROJECTED" or dict(candidate) != expected:
        raise IntegrityError("COMPLETED_RUN_OBSERVABILITY_TRUSTED_REBUILD_MISMATCH")
    return dict(candidate)
