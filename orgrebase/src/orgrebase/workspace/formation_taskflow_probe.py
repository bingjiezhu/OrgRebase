"""Execute one sealed two-domain topology through pinned AgentTeams.

The probe is deliberately a transport/lifecycle proof, not a second business
state machine.  Its only topology input is an already-sealed
``AgentTeamsExecutionPlan``.  It materializes exactly the Product and Legal
domain tasks plus the plan's reviewer barrier, exercises the native
TeamHarness lifecycle, and stops at a zero-write candidate receipt.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from orgrebase.domain import ContentAddressedModel
from orgrebase.workspace.agentteams_execution_plan import (
    AgentTeamsExecutionPlan,
    AgentTeamsExecutionTask,
)
from orgrebase.workspace.native_taskflow import (
    PATH_DISCLOSURE_STATUS,
    RAW_MCP_REPRESENTATION,
    ControlledLocalMatrix,
    LifecycleJournal,
    _create_mc_process_adapter,
    _environment,
    _invoke,
    _public_path_replacements,
    _read_public_mc_invocations,
    _require_accepted,
    _require_effective_check,
    _require_ok,
    _require_task_state,
    _scan_public_evidence_paths,
    _write_json,
    digest_bytes,
    load_pinned_teamharness,
)

EVIDENCE_CLASS = "CONTROLLED_LOCAL_FORMATION_COMPILED_AGENTTEAMS"
CLAIM_BOUNDARY = "PINNED_IN_PROCESS_TEAMHARNESS_DYNAMIC_TOPOLOGY_NOT_LIVE_WORKER_OR_BUSINESS_ADMISSION"
TWO_DOMAIN_SET = ("legal", "product")
_TASK_ACTIONS = ("delegate_task", "ack_task", "submit_task", "check_task", "accept_task_result")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class FormationTaskflowProbeError(RuntimeError):
    """Stable fail-closed error raised by the dynamic-topology probe."""


class FormationTaskflowTaskReceipt(ContentAddressedModel):
    """Exact native lifecycle binding for one planned task."""

    schema_version: Literal["orgrebase.formation-taskflow-task-receipt.v1"] = (
        "orgrebase.formation-taskflow-task-receipt.v1"
    )
    task_id: str = Field(min_length=1)
    task_kind: Literal["DOMAIN", "REVIEWER_BARRIER"]
    domain_id: str = Field(min_length=1)
    assignee_actor_id: str = Field(min_length=1)
    transport_assignee: str = Field(pattern=r"^@[^:]+:[^:]+$")
    depends_on: tuple[str, ...]
    execution_task_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_spec_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    action_digests: dict[str, str] = Field(min_length=5, max_length=5)
    terminal_status: Literal["completed"] = "completed"
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_task_receipt(self) -> Self:
        if set(self.action_digests) != set(_TASK_ACTIONS) or any(
            not _DIGEST_PATTERN.fullmatch(value) for value in self.action_digests.values()
        ):
            raise ValueError("task action digest chain is incomplete or unordered")
        if self.task_kind == "DOMAIN":
            if self.domain_id not in TWO_DOMAIN_SET or self.depends_on:
                raise ValueError("domain task receipt is outside the exact two-domain plan")
        elif self.domain_id != "reviewer" or not self.depends_on:
            raise ValueError("reviewer receipt must be one dependency barrier")
        return self


class FormationTaskflowProbeReceipt(ContentAddressedModel):
    """Candidate-only proof that the native task set equals one sealed plan."""

    schema_version: Literal["orgrebase.formation-taskflow-probe-receipt.v1"] = (
        "orgrebase.formation-taskflow-probe-receipt.v1"
    )
    evidence_class: Literal["CONTROLLED_LOCAL_FORMATION_COMPILED_AGENTTEAMS"] = EVIDENCE_CLASS
    claim_boundary: Literal[
        "PINNED_IN_PROCESS_TEAMHARNESS_DYNAMIC_TOPOLOGY_NOT_LIVE_WORKER_OR_BUSINESS_ADMISSION"
    ] = CLAIM_BOUNDARY
    path_disclosure_status: Literal["PUBLIC_PATH_REDACTED"] = PATH_DISCLOSURE_STATUS
    raw_mcp_representation: Literal["SEMANTIC_MCP_WRAPPER_PUBLIC_PATH_REDACTED"] = RAW_MCP_REPRESENTATION
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source_verification: dict[str, Any]
    execution_plan_id: str = Field(min_length=1)
    execution_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    formation_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_envelope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    coalition_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selected_domain_ids: tuple[str, ...] = Field(min_length=2, max_length=2)
    expected_task_ids: tuple[str, ...] = Field(min_length=3, max_length=3)
    planned_task_ids: tuple[str, ...] = Field(min_length=3, max_length=3)
    actual_terminal_task_ids: tuple[str, ...] = Field(min_length=3, max_length=3)
    reviewer_task_id: str = Field(min_length=1)
    task_receipts: tuple[FormationTaskflowTaskReceipt, ...] = Field(min_length=3, max_length=3)
    agentteams_action_digests: tuple[str, ...] = Field(min_length=1)
    agentteams_action_count: int = Field(ge=1)
    matrix_transport: dict[str, Any]
    process_storage_adapter: dict[str, Any]
    project_terminal_state: Literal["completed"] = "completed"
    candidate_status: Literal["CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"] = (
        "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
    )
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_exact_topology(self) -> Self:
        if self.selected_domain_ids != TWO_DOMAIN_SET:
            raise ValueError("probe requires the exact legal/product domain set")
        task_ids = tuple(item.task_id for item in self.task_receipts)
        if not (self.expected_task_ids == self.planned_task_ids == self.actual_terminal_task_ids == task_ids):
            raise ValueError("planned, observed and terminal task sets diverge")
        domains = tuple(item.domain_id for item in self.task_receipts if item.task_kind == "DOMAIN")
        reviewers = tuple(item for item in self.task_receipts if item.task_kind == "REVIEWER_BARRIER")
        if (
            domains != self.selected_domain_ids
            or len(reviewers) != 1
            or reviewers[0].task_id != self.reviewer_task_id
            or reviewers[0].depends_on
            != tuple(item.task_id for item in self.task_receipts if item.task_kind == "DOMAIN")
        ):
            raise ValueError("domain tasks and reviewer barrier do not match the plan")
        if (
            self.agentteams_action_count != len(self.agentteams_action_digests)
            or len(set(self.agentteams_action_digests)) != self.agentteams_action_count
            or any(not _DIGEST_PATTERN.fullmatch(value) for value in self.agentteams_action_digests)
        ):
            raise ValueError("AgentTeams action digest set is invalid")
        receipt_action_digests = {
            value for item in self.task_receipts for value in item.action_digests.values()
        }
        if not receipt_action_digests.issubset(set(self.agentteams_action_digests)):
            raise ValueError("task lifecycle action is not bound to the native journal")
        return self


def _transport_assignee(actor_id: str) -> str:
    localpart = re.sub(r"[^a-zA-Z0-9._=+/\-]+", "-", actor_id).strip("-")
    if not localpart:
        raise FormationTaskflowProbeError("FORMATION_TASKFLOW_ASSIGNEE_INVALID")
    return f"@{localpart}:controlled.local"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _revalidated_plan(plan: AgentTeamsExecutionPlan) -> AgentTeamsExecutionPlan:
    try:
        selected = AgentTeamsExecutionPlan.model_validate(plan.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise FormationTaskflowProbeError("FORMATION_TASKFLOW_PLAN_INVALID") from exc
    if (
        selected.selected_domain_ids != TWO_DOMAIN_SET
        or tuple(item.domain_id for item in selected.tasks[:-1]) != TWO_DOMAIN_SET
        or selected.tasks[-1].task_kind != "REVIEWER_BARRIER"
        or selected.tasks[-1].depends_on != tuple(item.task_id for item in selected.tasks[:-1])
        or not selected.candidate_only
        or selected.canonical_target_writes != 0
    ):
        raise FormationTaskflowProbeError("FORMATION_TASKFLOW_EXACT_TWO_DOMAIN_PLAN_REQUIRED")
    return selected


def _task_spec(
    *,
    plan: AgentTeamsExecutionPlan,
    task: AgentTeamsExecutionTask,
    run_id: str,
    project_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.formation-taskflow-native-spec.v1",
        "run_id": run_id,
        "project_id": project_id,
        "execution_plan_id": plan.id,
        "execution_plan_digest": plan.digest,
        "execution_task": task.model_dump(mode="json"),
        "effect_ceiling": "ZERO_EXTERNAL_EFFECTS",
        "candidate_only": True,
        "canonical_target_writes": 0,
    }


def _candidate_result(
    *,
    plan: AgentTeamsExecutionPlan,
    task: AgentTeamsExecutionTask,
    run_id: str,
    project_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "orgrebase.formation-taskflow-candidate.v1",
        "run_id": run_id,
        "project_id": project_id,
        "task_id": task.task_id,
        "task_kind": task.task_kind,
        "domain_id": task.domain_id,
        "execution_plan_digest": plan.digest,
        "execution_task_digest": task.digest,
        "candidate_only": True,
        "canonical_target_writes": 0,
    }
    if task.task_kind == "DOMAIN":
        payload["candidate_status"] = "DOMAIN_CANDIDATE_PREPARED"
    else:
        payload.update(
            {
                "reviewed_task_ids": list(task.depends_on),
                "verdict_candidate": "PASS",
                "control_plane_admission_required": True,
            }
        )
    return payload


def _execute_task(
    *,
    module: Any,
    journal: LifecycleJournal,
    runtime: Path,
    room_id: str,
    run_id: str,
    project_id: str,
    plan: AgentTeamsExecutionPlan,
    task: AgentTeamsExecutionTask,
    transport_assignee: str,
) -> FormationTaskflowTaskReceipt:
    spec = _task_spec(
        plan=plan,
        task=task,
        run_id=run_id,
        project_id=project_id,
    )
    # This is the worker-side trust boundary in the controlled probe: parse the
    # exact sealed task again before delegation and bind its full bytes in spec.
    try:
        AgentTeamsExecutionTask.model_validate(spec["execution_task"])
    except ValueError as exc:
        raise FormationTaskflowProbeError(
            f"FORMATION_TASKFLOW_EXECUTION_TASK_INVALID:{task.task_id}"
        ) from exc
    spec_text = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True)
    result = _candidate_result(
        plan=plan,
        task=task,
        run_id=run_id,
        project_id=project_id,
    )
    result_text = _canonical_bytes(result).decode("utf-8")
    action_digests: dict[str, str] = {}
    common = {"workspaceDir": str(runtime), "payload": {"taskId": task.task_id}}

    payload, entry = _invoke(
        module,
        journal,
        key=f"{task.task_id}:delegate",
        tool="taskflow",
        action="delegate_task",
        arguments={
            "role": "leader",
            "workspaceDir": str(runtime),
            "payload": {
                "projectId": project_id,
                "taskId": task.task_id,
                "assignedTo": transport_assignee,
                "roomId": room_id,
                "spec": spec_text,
            },
        },
    )
    _require_task_state(payload, "assigned", f"{task.task_id}:delegate")
    notification = payload.get("notification")
    if (
        payload.get("synced") is not True
        or not isinstance(notification, Mapping)
        or notification.get("sent") is not True
        or not notification.get("eventId")
    ):
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_DELEGATION_INCOMPLETE:{task.task_id}")
    action_digests["delegate_task"] = entry["digest"]

    payload, entry = _invoke(
        module,
        journal,
        key=f"{task.task_id}:ack",
        tool="taskflow",
        action="ack_task",
        arguments={"role": "worker", **common},
    )
    _require_task_state(payload, "in_progress", f"{task.task_id}:ack")
    if (
        payload.get("pulled") is not True
        or payload.get("synced") is not True
        or payload.get("spec") != spec_text + "\n"
    ):
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_CONTEXT_HANDOFF_INVALID:{task.task_id}")
    try:
        acknowledged = json.loads(str(payload["spec"]))
        acknowledged_task = AgentTeamsExecutionTask.model_validate(acknowledged["execution_task"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_ACK_SPEC_INVALID:{task.task_id}") from exc
    if (
        acknowledged.get("execution_plan_digest") != plan.digest
        or acknowledged_task.digest != task.digest
        or acknowledged_task.task_id != task.task_id
    ):
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_ACK_BINDING_MISMATCH:{task.task_id}")
    action_digests["ack_task"] = entry["digest"]

    payload, entry = _invoke(
        module,
        journal,
        key=f"{task.task_id}:submit",
        tool="taskflow",
        action="submit_task",
        arguments={
            "role": "worker",
            "workspaceDir": str(runtime),
            "payload": {
                "taskId": task.task_id,
                "status": "SUCCESS",
                "summary": result_text,
                "deliverables": [],
            },
        },
    )
    _require_task_state(payload, "submitted", f"{task.task_id}:submit")
    if payload.get("synced") is not True:
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_SUBMIT_SYNC_FAILED:{task.task_id}")
    action_digests["submit_task"] = entry["digest"]

    payload, entry = _invoke(
        module,
        journal,
        key=f"{task.task_id}:check",
        tool="taskflow",
        action="check_task",
        arguments={"role": "leader", **common},
    )
    _require_effective_check(payload, f"{task.task_id}:check")
    observed = str(payload.get("result", {}).get("summary") or "")
    if observed != result_text:
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_RESULT_HANDOFF_MISMATCH:{task.task_id}")
    try:
        observed_result = json.loads(observed)
    except json.JSONDecodeError as exc:
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_RESULT_INVALID:{task.task_id}") from exc
    if (
        observed_result != result
        or observed_result.get("candidate_only") is not True
        or observed_result.get("canonical_target_writes") != 0
    ):
        raise FormationTaskflowProbeError(f"FORMATION_TASKFLOW_CANDIDATE_BOUNDARY_INVALID:{task.task_id}")
    action_digests["check_task"] = entry["digest"]

    payload, entry = _invoke(
        module,
        journal,
        key=f"{task.task_id}:accept",
        tool="projectflow",
        action="accept_task_result",
        arguments={
            "workspaceDir": str(runtime),
            "payload": {
                "projectId": project_id,
                "taskId": task.task_id,
                "resultStatus": "SUCCESS",
                "accepted": True,
            },
        },
    )
    _require_accepted(payload, f"{task.task_id}:accept")
    action_digests["accept_task_result"] = entry["digest"]

    return FormationTaskflowTaskReceipt(
        task_id=task.task_id,
        task_kind=task.task_kind,
        domain_id=task.domain_id,
        assignee_actor_id=task.assignee_actor_id,
        transport_assignee=transport_assignee,
        depends_on=task.depends_on,
        execution_task_digest=task.digest,
        task_spec_digest=digest_bytes(spec_text.encode("utf-8")),
        result_digest=digest_bytes(result_text.encode("utf-8")),
        action_digests=action_digests,
    )


def run_formation_taskflow_probe(
    *,
    plan: AgentTeamsExecutionPlan,
    checkout: str | Path,
    lock_path: str | Path,
    output_dir: str | Path,
    run_id: str | None = None,
) -> FormationTaskflowProbeReceipt:
    """Materialize exactly Product, Legal and Reviewer from one sealed plan."""

    selected = _revalidated_plan(plan)
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FormationTaskflowProbeError("FORMATION_TASKFLOW_OUTPUT_NOT_EMPTY")
    runtime = out / "runtime"
    runtime.mkdir()
    run_id = run_id or f"run:orgrebase:formation-taskflow:{selected.digest[7:19]}"
    project_id = f"orgrebase-formation-{selected.digest[7:19]}"
    room_id = "!orgrebase-formation:controlled.local"
    assignees = {item.task_id: _transport_assignee(item.assignee_actor_id) for item in selected.tasks}
    task_nodes = [
        {
            "taskId": item.task_id,
            "title": (
                f"{item.domain_id.title()} domain candidate"
                if item.task_kind == "DOMAIN"
                else "Independent reviewer barrier"
            ),
            "assignedTo": assignees[item.task_id],
            "dependsOn": list(item.depends_on),
        }
        for item in selected.tasks
    ]
    expected_task_ids = tuple(item.task_id for item in selected.tasks)
    path_replacements = _public_path_replacements(
        checkout=checkout,
        evidence_root=out,
        runtime_root=runtime,
    )
    module, source_verification = load_pinned_teamharness(checkout, lock_path)
    journal = LifecycleJournal(
        out / "action-journal.json",
        out / "raw-mcp",
        public_path_replacements=path_replacements,
    )
    mc_bin, mc_log = _create_mc_process_adapter(runtime)
    task_receipts: list[FormationTaskflowTaskReceipt] = []
    planned_task_ids: tuple[str, ...] = ()
    actual_terminal_task_ids: tuple[str, ...] = ()
    project_terminal_state = ""

    with (
        ControlledLocalMatrix(tuple(assignees.values())) as matrix,
        _environment(
            {
                "PATH": str(mc_bin) + os.pathsep + os.environ.get("PATH", ""),
                "ORGREBASE_MC_PROCESS_LOG": str(mc_log),
                "ORGREBASE_PUBLIC_PATH_REPLACEMENTS": json.dumps(path_replacements, ensure_ascii=False),
                "AGENTTEAMS_MATRIX_URL": matrix.url,
                "AGENTTEAMS_WORKER_MATRIX_TOKEN": "controlled-local-token",
                "AGENTTEAMS_SHARED_STORAGE_PREFIX": "agentteams/shared",
                "MC_HOST_agentteams": "http://controlled:local@127.0.0.1:9000",
                "AGENTTEAMS_AGENT_ROLE": "leader",
            }
        ),
    ):
        payload, _ = _invoke(
            module,
            journal,
            key="project:create",
            tool="projectflow",
            action="create_project",
            arguments={
                "workspaceDir": str(runtime),
                "payload": {
                    "projectId": project_id,
                    "title": "Formation-compiled two-domain topology probe",
                },
            },
        )
        _require_ok(payload, "project:create")
        if payload.get("project", {}).get("status") != "active":
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_PROJECT_CREATE_STATE")

        payload, _ = _invoke(
            module,
            journal,
            key="project:plan",
            tool="projectflow",
            action="plan_dag",
            arguments={
                "workspaceDir": str(runtime),
                "payload": {"projectId": project_id, "tasks": task_nodes},
            },
        )
        _require_ok(payload, "project:plan")
        native_tasks = payload.get("project", {}).get("tasks", [])
        planned_task_ids = tuple(item.get("task_id") for item in native_tasks if isinstance(item, Mapping))
        if planned_task_ids != expected_task_ids:
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_NATIVE_PLAN_MISMATCH")

        payload, _ = _invoke(
            module,
            journal,
            key="project:ready-domains",
            tool="projectflow",
            action="ready_nodes",
            arguments={"workspaceDir": str(runtime), "payload": {"projectId": project_id}},
        )
        _require_ok(payload, "project:ready-domains")
        ready_domain_ids = tuple(
            item.get("task_id") for item in payload.get("readyNodes", []) if isinstance(item, Mapping)
        )
        expected_domain_ids = tuple(item.task_id for item in selected.tasks[:-1])
        if ready_domain_ids != expected_domain_ids:
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_DOMAIN_READY_SET_MISMATCH")

        for task in selected.tasks[:-1]:
            task_receipts.append(
                _execute_task(
                    module=module,
                    journal=journal,
                    runtime=runtime,
                    room_id=room_id,
                    run_id=run_id,
                    project_id=project_id,
                    plan=selected,
                    task=task,
                    transport_assignee=assignees[task.task_id],
                )
            )

        reviewer = selected.tasks[-1]
        payload, _ = _invoke(
            module,
            journal,
            key="project:ready-reviewer",
            tool="projectflow",
            action="ready_nodes",
            arguments={"workspaceDir": str(runtime), "payload": {"projectId": project_id}},
        )
        _require_ok(payload, "project:ready-reviewer")
        ready_reviewer_ids = tuple(
            item.get("task_id") for item in payload.get("readyNodes", []) if isinstance(item, Mapping)
        )
        if ready_reviewer_ids != (reviewer.task_id,):
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_REVIEWER_BARRIER_NOT_READY")
        task_receipts.append(
            _execute_task(
                module=module,
                journal=journal,
                runtime=runtime,
                room_id=room_id,
                run_id=run_id,
                project_id=project_id,
                plan=selected,
                task=reviewer,
                transport_assignee=assignees[reviewer.task_id],
            )
        )

        payload, _ = _invoke(
            module,
            journal,
            key="project:complete",
            tool="projectflow",
            action="complete_project",
            arguments={"workspaceDir": str(runtime), "payload": {"projectId": project_id}},
        )
        _require_ok(payload, "project:complete")
        project = payload.get("project")
        if not isinstance(project, Mapping):
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_TERMINAL_PROJECT_MISSING")
        project_terminal_state = str(project.get("status") or "")
        terminal_tasks = project.get("tasks")
        if not isinstance(terminal_tasks, list):
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_TERMINAL_TASKS_MISSING")
        actual_terminal_task_ids = tuple(
            item.get("task_id") for item in terminal_tasks if isinstance(item, Mapping)
        )
        if (
            project_terminal_state != "completed"
            or actual_terminal_task_ids != expected_task_ids
            or any(
                not isinstance(item, Mapping) or item.get("status") != "completed" for item in terminal_tasks
            )
        ):
            raise FormationTaskflowProbeError("FORMATION_TASKFLOW_TERMINAL_SET_MISMATCH")
        matrix_summary = {
            "mode": "CONTROLLED_LOCAL_HTTP_STUB",
            "unique_assignment_events": len(matrix.state.events),
            "request_count": matrix.state.request_count,
        }

    mc_invocations = _read_public_mc_invocations(mc_log)
    _write_json(out / "inputs" / "execution-plan.json", selected.model_dump(mode="json"))
    _write_json(out / "source-verification.json", source_verification)
    for item in task_receipts:
        _write_json(out / "task-receipts" / f"{item.task_id}.json", item.model_dump(mode="json"))
    for entry in journal.actions:
        safe_key = entry["key"].replace(":", "-")
        _write_json(out / "actions" / f"{entry['sequence']:03d}-{safe_key}.json", entry)
    receipt = FormationTaskflowProbeReceipt(
        run_id=run_id,
        project_id=project_id,
        source_verification=source_verification,
        execution_plan_id=selected.id,
        execution_plan_digest=selected.digest,
        formation_receipt_digest=selected.formation_receipt_digest,
        context_envelope_digest=selected.context_envelope_digest,
        coalition_plan_digest=selected.coalition_plan_digest,
        selected_domain_ids=selected.selected_domain_ids,
        expected_task_ids=expected_task_ids,
        planned_task_ids=planned_task_ids,
        actual_terminal_task_ids=actual_terminal_task_ids,
        reviewer_task_id=selected.tasks[-1].task_id,
        task_receipts=tuple(task_receipts),
        agentteams_action_digests=tuple(item["digest"] for item in journal.actions),
        agentteams_action_count=len(journal.actions),
        matrix_transport=matrix_summary,
        process_storage_adapter={
            "mode": "CONTROLLED_LOCAL_MC_PROCESS_ADAPTER",
            "process_invocations": len(mc_invocations),
            "invocation_log_ref": "runtime/mc-process-invocations.jsonl",
            "path_disclosure_status": PATH_DISCLOSURE_STATUS,
        },
        project_terminal_state=project_terminal_state,
    )
    _write_json(out / "probe-receipt.json", receipt.model_dump(mode="json"))
    _scan_public_evidence_paths(out, checkout=checkout)
    return receipt


__all__ = (
    "CLAIM_BOUNDARY",
    "EVIDENCE_CLASS",
    "TWO_DOMAIN_SET",
    "FormationTaskflowProbeError",
    "FormationTaskflowProbeReceipt",
    "FormationTaskflowTaskReceipt",
    "run_formation_taskflow_probe",
)
