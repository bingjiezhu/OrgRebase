"""One controlled-local, candidate-only Golden Competition execution.

Each invocation receives a unique execution run ID and retains a stable Pack
correlation ID.  It
executes the pinned AgentTeams TeamHarness lifecycle, starts independent domain
and reviewer subprocesses only after ACK, exercises a real replan/tool recovery,
invokes the reviewed quote Skill, and prepares a typed Formation bundle.  The
standalone runtime never commits it; the Enterprise Pilot may admit and commit
that exact bundle through the deterministic control plane.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.process_policy import candidate_environment
from orgrebase.runtime_contracts import prepare_artifact_write
from orgrebase.workspace.agentteams_execution_plan import (
    AgentTeamsExecutionPlan,
    AgentTeamsExecutionTask,
    compile_agentteams_execution_plan,
)
from orgrebase.workspace.competition_worker import (
    DOMAINS,
    MODEL_PROVIDERS,
    WORKERS,
    deterministic_review,
    reviewer_model_id,
)
from orgrebase.workspace.context_residency import TaskAgentContextEnvelope
from orgrebase.workspace.controlled_agentteams_formation import (
    CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE,
    ControlledAgentTeamsFormationReceipt,
)
from orgrebase.workspace.controlled_local import (
    ControlledEnterpriseClient,
    ControlledEnterpriseServer,
)
from orgrebase.workspace.demand_formation import TaskFormationDecisionReceipt
from orgrebase.workspace.domain_agents import LocalSourceValue
from orgrebase.workspace.formation import (
    MEDIA,
    PROFILE_BINDING_ARTIFACT_ID,
    PROFILE_BINDING_MEDIA_TYPE,
)
from orgrebase.workspace.formation_integrity import verify_prepared_formation
from orgrebase.workspace.model_observations import ModelAttemptObservation, ModelUsage
from orgrebase.workspace.model_provider import VERTEX_MODEL_ID, VertexAIStructuredProvider
from orgrebase.workspace.models import (
    ActorContextProjection,
    ClaimCandidate,
    DomainCandidateBundle,
    DomainDelegationTask,
    SemanticKind,
)
from orgrebase.workspace.native_taskflow import (
    ControlledLocalMatrix,
    LifecycleJournal,
    _create_mc_process_adapter,
    _environment,
    _invoke,
    _public_path_replacements,
    _require_accepted,
    _require_effective_check,
    _require_ok,
    _require_task_state,
    _write_json,
    load_pinned_teamharness,
)
from orgrebase.workspace.pilot import (
    enterprise_quote_pilot_run_id,
    load_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.quote_skill_qualification import CASES, qualification_premise
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_packages import (
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillEvaluationCase,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)
from orgrebase.workspace.templates import default_capability_cards

EVIDENCE_CLASS = "CONTROLLED_LOCAL_GOLDEN_COMPETITION"
CLAIM_BOUNDARY = (
    "PINNED_IN_PROCESS_TEAMHARNESS_WITH_INDEPENDENT_LOCAL_WORKER_PROCESSES_NOT_DISTRIBUTED_PRODUCTION"
)


def _quote_execution_schema_digests() -> dict[str, str]:
    return {
        "schema:workspace.domain-delegation@v1": sha256_digest(
            DomainDelegationTask.model_json_schema(mode="validation")
        ),
        "schema:workspace.domain-candidate-bundle@v1": sha256_digest(
            DomainCandidateBundle.model_json_schema(mode="validation")
        ),
    }


def _compile_bound_execution_plan(
    *,
    formation_receipt: Mapping[str, Any] | TaskFormationDecisionReceipt | None,
    context_envelope: Mapping[str, Any] | TaskAgentContextEnvelope | None,
    coalition: Any,
    actor_projections: tuple[ActorContextProjection, ...],
) -> AgentTeamsExecutionPlan | None:
    """Independently compile a plan only when both admitted roots are present."""

    if formation_receipt is None and context_envelope is None:
        return None
    if formation_receipt is None or context_envelope is None:
        raise GoldenCompetitionError("GOLDEN_OAC_EXECUTION_ROOTS_INCOMPLETE")
    try:
        selected_receipt = TaskFormationDecisionReceipt.model_validate(formation_receipt)
        selected_envelope = TaskAgentContextEnvelope.model_validate(context_envelope)
        return compile_agentteams_execution_plan(
            formation_receipt=selected_receipt,
            context_envelope=selected_envelope,
            coalition=coalition,
            capability_cards=default_capability_cards(coalition.template_ref),
            actor_projections=actor_projections,
            schema_digests=_quote_execution_schema_digests(),
        )
    except (TypeError, ValueError, IntegrityError) as exc:
        raise GoldenCompetitionError("GOLDEN_OAC_EXECUTION_PLAN_INVALID") from exc


def _resolve_bound_actor_projections(
    *,
    service: WorkspaceService,
    request: Any,
    context_envelope: Mapping[str, Any] | TaskAgentContextEnvelope,
) -> tuple[ActorContextProjection, ...]:
    """Recompute and select the exact projection payloads sealed by the envelope."""

    try:
        envelope = TaskAgentContextEnvelope.model_validate(context_envelope)
        prepared = service.formation.prepare_quote(request)
        indexed = {
            (projection.ref, projection.digest): projection
            for projection in (
                ActorContextProjection.model_validate(item.payload)
                for item in prepared.artifact_writes
                if item.media_type == MEDIA["projection"]
            )
        }
        selected = tuple(
            indexed[(binding.actor_projection_ref, binding.actor_projection_digest)]
            for binding in envelope.domain_bindings
        )
    except (KeyError, TypeError, ValueError, IntegrityError) as exc:
        raise GoldenCompetitionError("GOLDEN_CONTEXT_ACTOR_PROJECTION_RECOMPUTATION_INVALID") from exc
    if tuple(item.actor_id for item in selected) != tuple(
        default.worker_id
        for binding in envelope.domain_bindings
        for default in default_capability_cards(request.template_ref)
        if default.ref == binding.capability_card_ref
    ):
        raise GoldenCompetitionError("GOLDEN_CONTEXT_ACTOR_PROJECTION_ORDER_INVALID")
    return selected


def _runtime_plan_binding(
    plan: AgentTeamsExecutionPlan | None,
    task: AgentTeamsExecutionTask | None,
) -> dict[str, Any]:
    if plan is None and task is None:
        return {}
    if plan is None or task is None or task not in plan.tasks:
        raise GoldenCompetitionError("GOLDEN_RUNTIME_PLAN_TASK_BINDING_INVALID")
    return {
        "agentteams_execution_plan_digest": plan.digest,
        "context_envelope_digest": plan.context_envelope_digest,
        "logical_plan_task_id": task.task_id,
        "logical_plan_task_digest": task.digest,
        "formation_receipt_id": task.formation_receipt_id,
        "formation_receipt_digest": task.formation_receipt_digest,
        "sealed_plan_task": task.model_dump(mode="json"),
    }


def _require_runtime_result_plan_binding(
    result: Mapping[str, Any],
    *,
    plan: AgentTeamsExecutionPlan | None,
    task: AgentTeamsExecutionTask | None,
) -> None:
    if plan is None and task is None:
        return
    expected = _runtime_plan_binding(plan, task)
    for field in (
        "agentteams_execution_plan_digest",
        "context_envelope_digest",
        "logical_plan_task_id",
        "logical_plan_task_digest",
        "formation_receipt_id",
        "formation_receipt_digest",
    ):
        if result.get(field) != expected[field]:
            raise GoldenCompetitionError(f"GOLDEN_RUNTIME_RESULT_PLAN_BINDING_MISMATCH:{field}")


def _quote_skill_evaluation_cases(
    *,
    run_id: str,
    public_input: Mapping[str, Any],
) -> tuple[SkillEvaluationCase, ...]:
    """Derive caller-owned release cases from this run's exact admitted roots."""

    base = dict(public_input)

    def with_updates(**updates: Any) -> dict[str, Any]:
        return {**base, **updates}

    malformed = dict(base)
    malformed.pop("dependency_result_digest")
    substituted_domains = dict(base["domain_result_digests"])
    substituted_domains["legal"] = substituted_domains["product"]
    inputs = {
        "replay": with_updates(),
        "held_out": with_updates(skill_partition="held_out"),
        "negative_transfer": with_updates(skill_partition="negative_transfer"),
        "permission": with_updates(permission_expansion=True),
        "injection": with_updates(prompt_injection=True),
        "malformed": malformed,
        "resource_or_deadline": with_updates(deadline_expired=True),
        "canary": with_updates(skill_partition="canary"),
        "domain_substitution": with_updates(
            skill_partition="replay", domain_result_digests=substituted_domains
        ),
    }
    return tuple(
        SkillEvaluationCase(
            case_id=f"{run_id}:quote-compose:{name}",
            partition=partition,
            public_input=inputs[name],
            expected_action=expected_action,
        )
        for name, partition, expected_action in CASES
    )


DOMAIN_OUTPUT_SCHEMA = {
    "schema_version": "orgrebase.golden-domain-result.v1",
    "required": [
        "run_id",
        "task_id",
        "domain",
        "attempt",
        "status",
        "projection_digest",
        "candidate_only",
        "target_writes",
    ],
}
REVIEW_OUTPUT_SCHEMA = {
    "schema_version": "orgrebase.golden-reviewer-result.v1",
    "required": [
        "run_id",
        "task_id",
        "phase",
        "status",
        "model_receipt",
        "decision",
        "candidate_only",
        "target_writes",
    ],
}


class GoldenCompetitionError(RuntimeError):
    """Stable fail-closed Golden Runtime error."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GoldenCompetitionError("GOLDEN_JSON_OBJECT_REQUIRED")
    return value


def _record(value: Mapping[str, Any]) -> dict[str, Any]:
    body = json.loads(json.dumps(dict(value), ensure_ascii=False))
    return {**body, "digest": sha256_digest(body)}


def _reseal(value: dict[str, Any]) -> None:
    value.pop("digest", None)
    value["digest"] = sha256_digest(value)


def _source_payload(source: Any) -> dict[str, Any]:
    projected = LocalSourceValue(
        slot_id=source.slot_id,
        object_ref=source.object_ref,
        domain_id=source.domain_id,
        value=source.value,
        semantic_kind=source.semantic_kind,
        authority_ref=source.authority_ref,
        source_id=source.source_id,
        source_version=source.source_version,
        sensitivity=source.sensitivity,
        raw_private_value=None,
    )
    return {
        "slot_id": projected.slot_id,
        "object_ref": projected.object_ref,
        "domain_id": projected.domain_id,
        "value": projected.value,
        "semantic_kind": projected.semantic_kind.value,
        "authority_ref": projected.authority_ref,
        "source_id": projected.source_id,
        "source_version": projected.source_version,
        "sensitivity": projected.sensitivity,
        "source_digest": projected.source_digest,
    }


def _source_value(payload: Mapping[str, Any]) -> LocalSourceValue:
    source = LocalSourceValue(
        slot_id=str(payload["slot_id"]),
        object_ref=str(payload["object_ref"]),
        domain_id=str(payload["domain_id"]),
        value=payload["value"],
        semantic_kind=SemanticKind(str(payload["semantic_kind"])),
        authority_ref=str(payload["authority_ref"]),
        source_id=str(payload["source_id"]),
        source_version=str(payload["source_version"]),
        sensitivity=str(payload.get("sensitivity") or "INTERNAL"),
        raw_private_value=None,
    )
    if payload.get("source_digest") != source.source_digest:
        raise GoldenCompetitionError("GOLDEN_HTTP_SOURCE_DIGEST_MISMATCH")
    return source


def _projection_without(projection: ActorContextProjection, *, object_ref: str) -> ActorContextProjection:
    payload = projection.model_dump(mode="json", exclude={"digest"})
    payload["version"] = "v1-finance-abstain"
    payload["included_refs"] = [item for item in projection.included_refs if item != object_ref]
    payload["excluded"] = [
        *projection.excluded,
        {"object_ref": object_ref, "reason": "SOURCE_FIELD_MISSING_AT_ATTEMPT_1"},
    ]
    return ActorContextProjection.model_validate(payload)


def _projection_with_tool(projection: ActorContextProjection, *, object_ref: str) -> ActorContextProjection:
    payload = projection.model_dump(mode="json", exclude={"digest"})
    payload["version"] = "v2-tool-supplemented"
    payload["included_refs"] = sorted(set(projection.included_refs) | {object_ref})
    payload["excluded"] = [item for item in projection.excluded if item.get("object_ref") != object_ref]
    return ActorContextProjection.model_validate(payload)


def _child_input(
    *,
    run_id: str,
    correlation_id: str,
    task_id: str,
    domain: str,
    attempt: int,
    request: Any,
    template: Any,
    coalition: Any,
    projection: ActorContextProjection,
    source_values: Mapping[str, Any],
    observed_at: str,
    tool_receipt_digest: str | None = None,
    tool_result_digest: str | None = None,
    context_envelope_digest: str | None = None,
    execution_plan: AgentTeamsExecutionPlan | None = None,
    logical_plan_task: AgentTeamsExecutionTask | None = None,
    admitted_projection: ActorContextProjection | None = None,
) -> dict[str, Any]:
    included_refs = set(projection.included_refs)
    domain_sources = {
        slot: _source_payload(value)
        for slot, value in sorted(source_values.items())
        if value.domain_id == domain and value.object_ref in included_refs
    }
    payload = {
        "schema_version": "orgrebase.golden-domain-worker-input.v1",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "domain": domain,
        "attempt": attempt,
        "request": request.model_dump(mode="json"),
        "template": template.model_dump(mode="json"),
        "coalition": coalition.model_dump(mode="json"),
        "projection": projection.model_dump(mode="json"),
        "source_values": domain_sources,
        "observed_at": observed_at,
        "supplemental_tool_receipt_digest": tool_receipt_digest,
        "supplemental_tool_result_digest": tool_result_digest,
        "candidate_only": True,
        "target_writes": 0,
        **_runtime_plan_binding(execution_plan, logical_plan_task),
    }
    if context_envelope_digest is not None:
        payload["context_envelope_digest"] = context_envelope_digest
    if logical_plan_task is not None:
        if (
            admitted_projection is None
            or logical_plan_task.actor_projection_ref != admitted_projection.ref
            or logical_plan_task.actor_projection_digest != admitted_projection.digest
        ):
            raise GoldenCompetitionError("GOLDEN_ADMITTED_ACTOR_PROJECTION_BINDING_INVALID")
        payload["admitted_actor_projection"] = admitted_projection.model_dump(mode="json")
    return payload


def _trusted_source_bindings(child_input: Mapping[str, Any]) -> dict[str, Any]:
    sources = child_input.get("source_values")
    if not isinstance(sources, Mapping):
        raise GoldenCompetitionError("GOLDEN_MANAGER_SOURCE_BINDINGS_MISSING")
    return {
        str(slot): {
            "object_ref": value["object_ref"],
            "value_digest": sha256_digest(value["value"]),
            "semantic_kind": value["semantic_kind"],
            "authority_ref": value["authority_ref"],
            "source_id": value["source_id"],
            "source_version": value["source_version"],
            "source_digest": value["source_digest"],
            "sensitivity": value["sensitivity"],
        }
        for slot, value in sorted(sources.items())
        if isinstance(value, Mapping)
    }


def _require_reviewed_formation_inputs(
    reviewer_input: Mapping[str, Any],
    selected_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return the exact result set admitted by the deterministic Reviewer.

    Formation is a separate authority boundary. It must never assemble from a
    mutable result mapping that merely resembles the bytes the Reviewer saw.
    This guard compares every selected result by content digest, reruns the
    deterministic verifier, and returns a detached JSON round-trip of the
    reviewed bytes for downstream assembly.
    """

    reviewed = reviewer_input.get("domain_results")
    if not isinstance(reviewed, list):
        raise GoldenCompetitionError("GOLDEN_REVIEWED_RESULT_SET_MISSING")
    reviewed_by_domain: dict[str, dict[str, Any]] = {}
    for item in reviewed:
        if not isinstance(item, Mapping):
            raise GoldenCompetitionError("GOLDEN_REVIEWED_RESULT_INVALID")
        domain = str(item.get("domain") or "")
        if domain not in DOMAINS or domain in reviewed_by_domain:
            raise GoldenCompetitionError("GOLDEN_REVIEWED_RESULT_CARDINALITY_INVALID")
        reviewed_by_domain[domain] = json.loads(json.dumps(dict(item), ensure_ascii=False))
    if set(reviewed_by_domain) != set(DOMAINS) or set(selected_results) != set(DOMAINS):
        raise GoldenCompetitionError("GOLDEN_REVIEWED_RESULT_DOMAIN_SET_MISMATCH")
    if any(
        sha256_digest(reviewed_by_domain[domain]) != sha256_digest(selected_results[domain])
        for domain in DOMAINS
    ):
        raise GoldenCompetitionError("GOLDEN_REVIEWED_RESULT_SET_CHANGED")
    decision = deterministic_review(dict(reviewer_input))
    if decision.verdict != "PASS":
        raise GoldenCompetitionError("GOLDEN_FORMATION_REVIEW_GATE_REJECTED")
    return reviewed_by_domain


def _task_spec(
    *,
    run_id: str,
    correlation_id: str,
    project_id: str,
    task_id: str,
    role: str,
    assignee: str,
    input_ref: str,
    input_digest: str,
    output_schema: Mapping[str, Any],
    attempt: int,
    projection_digest: str | None,
    context_envelope_digest: str | None = None,
    execution_plan: AgentTeamsExecutionPlan | None = None,
    logical_plan_task: AgentTeamsExecutionTask | None = None,
) -> str:
    payload = {
        "schema_version": "orgrebase.golden-agentteams-task-spec.v1",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "project_id": project_id,
        "task_id": task_id,
        "role": role,
        "assignee": assignee,
        "attempt": attempt,
        "input_ref": input_ref,
        "input_digest": input_digest,
        "projection_digest": projection_digest,
        "output_schema_digest": sha256_digest(output_schema),
        "result_binding_mode": "OBSERVED_AFTER_ACK_NO_EXPECTED_RESULT_DIGEST",
        "candidate_only": True,
        "target_writes": 0,
        **_runtime_plan_binding(execution_plan, logical_plan_task),
    }
    if context_envelope_digest is not None:
        payload["context_envelope_digest"] = context_envelope_digest
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _delegate_ack(
    module: Any,
    journal: LifecycleJournal,
    *,
    runtime: Path,
    project_id: str,
    task_id: str,
    assignee: str,
    room_id: str,
    spec: str,
    binding: dict[str, Any],
) -> None:
    payload, delegate = _invoke(
        module,
        journal,
        key=f"{task_id}:delegate",
        tool="taskflow",
        action="delegate_task",
        arguments={
            "role": "leader",
            "workspaceDir": str(runtime),
            "payload": {
                "projectId": project_id,
                "taskId": task_id,
                "assignedTo": assignee,
                "roomId": room_id,
                "spec": spec,
            },
        },
    )
    _require_task_state(payload, "assigned", f"{task_id}:delegate")
    notification = payload.get("notification")
    if not isinstance(notification, Mapping) or not notification.get("eventId"):
        raise GoldenCompetitionError(f"GOLDEN_DELEGATION_EVENT_MISSING:{task_id}")
    payload, ack = _invoke(
        module,
        journal,
        key=f"{task_id}:ack",
        tool="taskflow",
        action="ack_task",
        arguments={
            "role": "worker",
            "workspaceDir": str(runtime),
            "payload": {"taskId": task_id},
        },
    )
    _require_task_state(payload, "in_progress", f"{task_id}:ack")
    if payload.get("spec") != spec + "\n":
        raise GoldenCompetitionError(f"GOLDEN_ACK_SPEC_MISMATCH:{task_id}")
    binding.update(
        {
            "assignment_event_id": notification["eventId"],
            "delegate_action_digest": delegate["digest"],
            "ack_action_digest": ack["digest"],
            "status": "ACKED",
        }
    )
    _reseal(binding)


def _run_child(
    *,
    repo_root: Path,
    mode: str,
    input_path: Path,
    output_path: Path,
    ack_action_digest: str,
    timeout_seconds: float = 60,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started_at = _utc_now()
    child_input = _read(input_path)
    selected_provider = str(child_input.get("model_provider") or "ollama-local") if mode == "reviewer" else None
    vertex_environment = {}
    if selected_provider == "vertex-ai":
        vertex_environment = VertexAIStructuredProvider(
            prompt_payload={}, model_id=str(child_input.get("model_id") or VERTEX_MODEL_ID),
        ).candidate_environment_values()
    with tempfile.TemporaryDirectory(prefix="orgrebase-candidate-") as private_home:
        environment = candidate_environment(os.environ, home=Path(private_home), model_provider=selected_provider)
        if vertex_environment:
            environment.pop("ORGREBASE_VERTEX_ACCESS_TOKEN", None)
            environment.pop("ORGREBASE_VERTEX_API_KEY", None)
            environment.update(vertex_environment)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "orgrebase.workspace.competition_worker",
                mode,
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            close_fds=True,
            env=environment,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds + (5 if selected_provider == "vertex-ai" else 0))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise GoldenCompetitionError(f"GOLDEN_CHILD_TIMEOUT:{mode}") from exc
        if process.returncode != 0 or not output_path.is_file():
            raise GoldenCompetitionError(f"GOLDEN_CHILD_FAILED:{mode}:{process.returncode}")
    result = _read(output_path)
    receipt_payload = {
        "schema_version": "orgrebase.golden-subprocess-receipt.v1",
        "mode": mode,
        "process_id": process.pid,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "exit_code": process.returncode,
        "input_digest": sha256_digest(child_input),
        "output_digest": sha256_digest(result),
        "task_id": result.get("task_id"),
        "run_id": result.get("run_id"),
        "correlation_id": result.get("correlation_id"),
        "domain": result.get("domain"),
        "attempt": result.get("attempt") or result.get("phase"),
        "started_after_ack_action_digest": ack_action_digest,
        "stdout_digest": sha256_digest(stdout),
        "stderr_digest": sha256_digest(stderr),
        "independent_process": True,
        "canonical_target_writes": 0,
    }
    context_envelope_digest = child_input.get("context_envelope_digest")
    if context_envelope_digest is not None:
        receipt_payload["context_envelope_digest"] = context_envelope_digest
    for field in (
        "agentteams_execution_plan_digest",
        "logical_plan_task_id",
        "logical_plan_task_digest",
        "formation_receipt_id",
        "formation_receipt_digest",
    ):
        if field in child_input:
            receipt_payload[field] = child_input[field]
    receipt = _record(receipt_payload)
    return result, receipt


def _submit_check_accept(
    module: Any,
    journal: LifecycleJournal,
    *,
    runtime: Path,
    project_id: str,
    task_id: str,
    result: Mapping[str, Any],
    binding: dict[str, Any],
) -> None:
    summary = json.dumps(dict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload, submit = _invoke(
        module,
        journal,
        key=f"{task_id}:submit",
        tool="taskflow",
        action="submit_task",
        arguments={
            "role": "worker",
            "workspaceDir": str(runtime),
            "payload": {
                "taskId": task_id,
                "status": "SUCCESS",
                "summary": summary,
                "deliverables": [],
            },
        },
    )
    _require_task_state(payload, "submitted", f"{task_id}:submit")
    payload, check = _invoke(
        module,
        journal,
        key=f"{task_id}:check",
        tool="taskflow",
        action="check_task",
        arguments={
            "role": "leader",
            "workspaceDir": str(runtime),
            "payload": {"taskId": task_id},
        },
    )
    _require_effective_check(payload, f"{task_id}:check")
    observed = str(payload.get("result", {}).get("summary") or "")
    if observed != summary:
        raise GoldenCompetitionError(f"GOLDEN_RESULT_ROUNDTRIP_MISMATCH:{task_id}")
    payload, accept = _invoke(
        module,
        journal,
        key=f"{task_id}:accept",
        tool="projectflow",
        action="accept_task_result",
        arguments={
            "workspaceDir": str(runtime),
            "payload": {
                "projectId": project_id,
                "taskId": task_id,
                "resultStatus": "SUCCESS",
                "accepted": True,
            },
        },
    )
    _require_accepted(payload, f"{task_id}:accept")
    binding.update(
        {
            "observed_result_digest": sha256_digest(result),
            "submit_action_digest": submit["digest"],
            "check_action_digest": check["digest"],
            "accept_action_digest": accept["digest"],
            "status": "COMPLETED",
        }
    )
    _reseal(binding)


def _required_slots(coalition: Any) -> dict[str, list[str]]:
    result = {domain: [] for domain in DOMAINS}
    for item in coalition.coverage:
        result[item.domain_id].append(item.slot_id)
    return {domain: sorted(slots) for domain, slots in result.items()}


def _reviewer_model_attempt_evidence(
    *,
    model_provider: str,
    reviewer_input: Mapping[str, Any],
    reviewer_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one advisory model receipt to the exact Reviewer input bytes."""

    receipt = reviewer_result.get("model_receipt")
    runtime_binding = reviewer_result.get("model_runtime_binding")
    reviewer_input_digest = sha256_digest(reviewer_input)
    if (
        not isinstance(receipt, Mapping)
        or not isinstance(runtime_binding, Mapping)
        or reviewer_result.get("run_id") != reviewer_input.get("run_id")
        or reviewer_result.get("task_id") != reviewer_input.get("task_id")
        or reviewer_result.get("phase") != reviewer_input.get("phase")
        or reviewer_result.get("input_digest") != reviewer_input_digest
        or reviewer_result.get("model_provider") != model_provider
        or reviewer_result.get("candidate_only") is not True
        or reviewer_result.get("target_writes") != 0
        or reviewer_result.get("model_authority") != "ADVISORY_ONLY_DETERMINISTIC_REVIEWER_AUTHORITATIVE"
        or receipt.get("provider") != model_provider
        or receipt.get("status") != "VALID"
        or receipt.get("schema_valid") is not True
    ):
        raise GoldenCompetitionError("GOLDEN_REVIEWER_MODEL_BINDING_INVALID")
    collection = reviewer_result.get("model_attempt_observations")
    observation = None
    if isinstance(collection, Mapping) and collection.get("records"):
        try:
            records = [ModelAttemptObservation.model_validate(item) for item in collection["records"]]
            matching = [item for item in records if item.response_receipt_digest == receipt["digest"]]
            if len(matching) != 1:
                raise ValueError("one observation required")
            observation = matching[0]
            if model_provider == "vertex-ai":
                if (not 1 <= len(records) <= 3 or records[-1] != observation
                        or len({item.dispatch_id for item in records}) != len(records)
                        or any(item.request_digest != receipt["request_digest"]
                               or item.run_ref != reviewer_input["run_id"]
                               or item.task_ref != reviewer_input["task_id"] for item in records)
                        or any(item.response_state != "PROVIDER_ERROR" or item.error_code != "VERTEX_RATE_LIMITED"
                               or item.dispatch_state != "RESPONSE_RECEIVED" for item in records[:-1])):
                    raise ValueError("invalid Vertex transport attempt chain")
                retry = runtime_binding.get("transport_retry")
                if retry is not None and (not isinstance(retry, dict)
                        or not isinstance(retry.get("attempts"), list)
                        or any(not isinstance(item, dict) for item in retry["attempts"])
                        or retry.get("provider_attempts") != len(records)
                        or [item.get("dispatch_id") for item in retry["attempts"]] != [item.dispatch_id for item in records]):
                    raise ValueError("Vertex retry observation coverage")
            if (
                observation.phase != "RESULT"
                or observation.dispatch_state != "RESPONSE_RECEIVED"
                or observation.request_digest != receipt["request_digest"]
                or observation.run_ref != reviewer_input["run_id"]
                or observation.task_ref != reviewer_input["task_id"]
                or observation.provider != model_provider
                or observation.requested_model != receipt["model_id"]
            ):
                raise ValueError("observation binding")
        except (ValueError, TypeError, KeyError):
            raise GoldenCompetitionError("GOLDEN_REVIEWER_USAGE_BINDING_INVALID") from None
    usage = observation.usage if observation else ModelUsage(status="UNAVAILABLE", basis="unavailable")
    evidence = _record(
        {
            "schema_version": "orgrebase.golden-model-attempt-evidence.v2",
            "run_id": reviewer_input["run_id"],
            "task_id": reviewer_input["task_id"],
            "phase": reviewer_input["phase"],
            "reviewer_input_digest": reviewer_input_digest,
            "reviewer_prompt_payload_digest": reviewer_result["prompt_payload_digest"],
            "model_request_digest": receipt["request_digest"],
            "model_response_receipt_digest": receipt["digest"],
            "provider": model_provider,
            "provider_request_id": receipt.get("provider_request_id"),
            "requested_model_id": receipt["model_id"],
            "observed_model_version": runtime_binding.get("observed_model_version", receipt["model_version"]),
            "request_payload_digest": runtime_binding.get("request_payload_digest"),
            "response_observation_digest": runtime_binding.get("response_observation_digest"),
            "output_digest": receipt.get("output_digest"),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "usage": usage.model_dump(mode="json"),
            "model_attempt_observation_digest": observation.digest if observation else None,
            "dispatch_id": observation.dispatch_id if observation else None,
            "observation_persistence": observation.persistence if observation else "UNAVAILABLE",
            "legacy_receipt_usage_is_cost_evidence": False,
            "provider_attempt_count": len(collection.get("records", [])) if isinstance(collection, Mapping) else 0,
            "failed_provider_attempt_count": sum(item.get("response_state") != "VALID" for item in collection.get("records", [])) if isinstance(collection, Mapping) else 0,
            "latency_ms": receipt["latency_ms"],
            "finish_reason": receipt.get("finish_reason"),
            "thinking_level": runtime_binding.get("thinking_level"),
            "thinking_tokens": usage.thinking_tokens,
            "total_tokens": usage.total_tokens,
            "max_output_tokens": runtime_binding.get("max_output_tokens"),
            "advisory_accepted": reviewer_result["model_advisory_accepted"],
            "advisory_disposition": reviewer_result["model_advisory_disposition_reason"],
            "model_authority": reviewer_result["model_authority"],
            "candidate_only": True,
            "target_writes": 0,
        }
    )
    if model_provider == "vertex-ai" and (
        not evidence["provider_request_id"]
        or evidence["requested_model_id"] != VERTEX_MODEL_ID
        or evidence["observed_model_version"] != VERTEX_MODEL_ID
        or not evidence["request_payload_digest"]
        or not evidence["response_observation_digest"]
    ):
        raise GoldenCompetitionError("GOLDEN_VERTEX_MODEL_EVIDENCE_INCOMPLETE")
    if model_provider == "deepseek" and (
        not evidence["provider_request_id"] or evidence["requested_model_id"] != "deepseek-flash"
        or not evidence["observed_model_version"] or not evidence["request_payload_digest"]
        or not evidence["response_observation_digest"]
    ):
        raise GoldenCompetitionError("GOLDEN_DEEPSEEK_MODEL_EVIDENCE_INCOMPLETE")
    return evidence


def run_golden_competition(
    *,
    repo_root: str | Path,
    output_dir: str | Path,
    checkout: str | Path,
    lock_path: str | Path,
    pack_path: str | Path,
    model_provider: str = "ollama-local",
    ollama_endpoint: str | None = None,
    vertex_project: str | None = None,
    execution_run_id: str | None = None,
    context_envelope_digest: str | None = None,
    task_formation_decision_receipt: (Mapping[str, Any] | TaskFormationDecisionReceipt | None) = None,
    context_envelope: Mapping[str, Any] | TaskAgentContextEnvelope | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise GoldenCompetitionError("GOLDEN_OUTPUT_DIR_NOT_EMPTY")
    if model_provider not in MODEL_PROVIDERS:
        raise GoldenCompetitionError("GOLDEN_MODEL_PROVIDER_INVALID")
    runtime_pack = load_enterprise_quote_pilot_pack(pack_path)
    correlation_id = enterprise_quote_pilot_run_id(runtime_pack)
    run_id = execution_run_id or f"run:golden-competition:{uuid4()}"
    if not isinstance(run_id, str) or not run_id.strip():
        raise GoldenCompetitionError("GOLDEN_EXECUTION_RUN_ID_REQUIRED")
    if context_envelope_digest is not None and (
        not isinstance(context_envelope_digest, str)
        or len(context_envelope_digest) != 71
        or not context_envelope_digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in context_envelope_digest[7:])
    ):
        raise GoldenCompetitionError("GOLDEN_CONTEXT_ENVELOPE_DIGEST_INVALID")
    nonce = sha256_digest({"run_id": run_id, "pack": runtime_pack.pack_digest}).split(":", 1)[1]
    project_suffix = sha256_digest(run_id).split(":", 1)[1][:8]
    project_id = f"orgrebase-golden-evergreen-quote-{project_suffix}"
    execution_envelope_payload = {
        "schema_version": "orgrebase.golden-execution-envelope.v1",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "pack_digest": runtime_pack.pack_digest,
        "model_provider": model_provider,
        "started_at": _utc_now(),
        "nonce": nonce,
    }
    if context_envelope_digest is not None:
        execution_envelope_payload["context_envelope_digest"] = context_envelope_digest
    execution_envelope = _record(execution_envelope_payload)
    _write_json(output / "execution-envelope.json", execution_envelope)
    room_id = "!orgrebase-golden:controlled.local"
    reviewer = "@quote-reviewer:controlled.local"
    assignees = {domain: f"@{WORKERS[domain]}:controlled.local" for domain in DOMAINS}
    module, source_verification = load_pinned_teamharness(checkout, lock_path)
    native_runtime = output / "runtime" / "agentteams"
    native_runtime.mkdir(parents=True)
    replacements = _public_path_replacements(
        checkout=checkout, evidence_root=output, runtime_root=native_runtime
    )
    journal = LifecycleJournal(
        output / "agentteams" / "action-journal.json",
        output / "agentteams" / "raw-mcp",
        public_path_replacements=replacements,
    )
    mc_bin, mc_log = _create_mc_process_adapter(output / "runtime")
    service = WorkspaceService(
        store_path=":memory:",
        workflow_run_id=run_id,
        runtime_configuration=runtime_pack,
    )
    request = runtime_pack.profile.task_request()
    template, _requirements, interpretation, revision_lock, coalition = (
        service.formation.compile_quote_contracts(request)
    )
    base_projections = service.formation.domain_registry.source_projections(
        task=request,
        template=template,
        plan=coalition,
        now=service.formation.clock.now(),
    )
    plan_actor_projections = (
        _resolve_bound_actor_projections(
            service=service,
            request=request,
            context_envelope=context_envelope,
        )
        if task_formation_decision_receipt is not None and context_envelope is not None
        else base_projections
    )
    projections = {
        next(domain for domain, worker in WORKERS.items() if worker == item.actor_id): item
        for item in base_projections
    }
    admitted_projections = {
        next(domain for domain, worker in WORKERS.items() if worker == item.actor_id): item
        for item in plan_actor_projections
    }
    execution_plan = _compile_bound_execution_plan(
        formation_receipt=task_formation_decision_receipt,
        context_envelope=context_envelope,
        coalition=coalition,
        actor_projections=plan_actor_projections,
    )
    if execution_plan is not None:
        if (
            context_envelope_digest is not None
            and execution_plan.context_envelope_digest != context_envelope_digest
        ):
            raise GoldenCompetitionError("GOLDEN_CONTEXT_ENVELOPE_DIGEST_MISMATCH")
        context_envelope_digest = execution_plan.context_envelope_digest
        execution_envelope_payload["context_envelope_digest"] = execution_plan.context_envelope_digest
        execution_envelope_payload["agentteams_execution_plan_digest"] = execution_plan.digest
        execution_envelope_payload["task_formation_decision_receipt_digest"] = (
            execution_plan.formation_receipt_digest
        )
        execution_envelope = _record(execution_envelope_payload)
        _write_json(output / "execution-envelope.json", execution_envelope)
        _write_json(
            output / "agentteams" / "execution-plan.json",
            execution_plan.model_dump(mode="json"),
        )
        admitted_formation_root = TaskFormationDecisionReceipt.model_validate(
            task_formation_decision_receipt
        )
        admitted_context_root = TaskAgentContextEnvelope.model_validate(context_envelope)
        _write_json(
            output / "inputs" / "task-formation-decision-receipt.json",
            admitted_formation_root.model_dump(mode="json"),
        )
        _write_json(
            output / "inputs" / "task-agent-context-envelope.json",
            admitted_context_root.model_dump(mode="json"),
        )
    logical_domain_tasks = (
        tuple(item for item in execution_plan.tasks if item.task_kind == "DOMAIN")
        if execution_plan is not None
        else ()
    )
    logical_reviewer_task = (
        next(item for item in execution_plan.tasks if item.task_kind == "REVIEWER_BARRIER")
        if execution_plan is not None
        else None
    )
    runtime_domains = (
        tuple(item.domain_id for item in logical_domain_tasks) if execution_plan is not None else DOMAINS
    )
    if set(runtime_domains) != set(DOMAINS):
        raise GoldenCompetitionError("GOLDEN_QUOTE_REQUIRED_DOMAIN_TOPOLOGY_MISMATCH")
    logical_task_by_domain = {item.domain_id: item for item in logical_domain_tasks}
    price_ref = runtime_pack.source_values["price_band"].object_ref
    enterprise_server = ControlledEnterpriseServer(
        dependencies=(price_ref,),
        dependency_source_values={"price_band": _source_payload(runtime_pack.source_values["price_band"])},
    )
    finance_a1_projection = _projection_without(projections["finance"], object_ref=price_ref)
    if execution_plan is not None:
        assignees = {
            item.domain_id: f"@{item.assignee_actor_id}:controlled.local" for item in logical_domain_tasks
        }
        reviewer = f"@{logical_reviewer_task.assignee_actor_id}:controlled.local"
    runtime_task_id_by_logical = {
        item.task_id: f"{project_id}-{item.domain_id}-a1" for item in logical_domain_tasks
    }
    initial_nodes = []
    for domain in runtime_domains:
        logical_task = logical_task_by_domain.get(domain)
        initial_nodes.append(
            {
                "taskId": f"{project_id}-{domain}-a1",
                "title": f"{domain.title()} candidate generation",
                "assignedTo": assignees[domain],
                "dependsOn": (
                    [runtime_task_id_by_logical[item] for item in logical_task.depends_on]
                    if logical_task is not None
                    else []
                ),
            }
        )
    reviewer_a1 = f"{project_id}-reviewer-a1"
    initial_nodes.append(
        {
            "taskId": reviewer_a1,
            "title": "Coalition slot and provenance review attempt 1",
            "assignedTo": reviewer,
            "dependsOn": (
                [runtime_task_id_by_logical[item] for item in logical_reviewer_task.depends_on]
                if logical_reviewer_task is not None
                else [f"{project_id}-{domain}-a1" for domain in runtime_domains]
            ),
        }
    )
    actual_agentteams_domain_ids = tuple(
        str(item["taskId"]).removeprefix(f"{project_id}-").removesuffix("-a1")
        for item in initial_nodes
        if item["taskId"] != reviewer_a1
    )
    planned_domain_ids = execution_plan.selected_domain_ids if execution_plan is not None else runtime_domains
    topology_match = planned_domain_ids == actual_agentteams_domain_ids
    if execution_plan is not None and not topology_match:
        raise GoldenCompetitionError("GOLDEN_AGENTTEAMS_TOPOLOGY_MISMATCH")
    bindings: list[dict[str, Any]] = []
    process_receipts: list[dict[str, Any]] = []
    domain_results: dict[str, dict[str, Any]] = {}
    plan_revisions: list[dict[str, Any]] = []
    members = (*assignees.values(), reviewer)
    env = {
        "PATH": str(mc_bin) + os.pathsep + os.environ.get("PATH", ""),
        "ORGREBASE_MC_PROCESS_LOG": str(mc_log),
        "ORGREBASE_PUBLIC_PATH_REPLACEMENTS": json.dumps(replacements),
        "AGENTTEAMS_WORKER_MATRIX_TOKEN": "controlled-local-token",
        "AGENTTEAMS_SHARED_STORAGE_PREFIX": "agentteams/shared",
        "MC_HOST_agentteams": "http://controlled:local@127.0.0.1:9000",
        "AGENTTEAMS_AGENT_ROLE": "leader",
    }
    if vertex_project:
        # Project routing is process-local configuration, not persisted model
        # evidence. The reviewer subprocess resolves it from this environment.
        env["ORGREBASE_VERTEX_PROJECT_ID"] = vertex_project
    try:
        with (
            ControlledLocalMatrix(members) as matrix,
            enterprise_server,
            _environment({**env, "AGENTTEAMS_MATRIX_URL": matrix.url}),
        ):
            payload, _ = _invoke(
                module,
                journal,
                key="project:create",
                tool="projectflow",
                action="create_project",
                arguments={
                    "workspaceDir": str(native_runtime),
                    "payload": {
                        "projectId": project_id,
                        "title": "Evergreen enterprise quote golden run",
                    },
                },
            )
            _require_ok(payload, "project:create")
            payload, plan_action = _invoke(
                module,
                journal,
                key="project:plan-r1",
                tool="projectflow",
                action="plan_dag",
                arguments={
                    "workspaceDir": str(native_runtime),
                    "payload": {"projectId": project_id, "tasks": initial_nodes},
                },
            )
            _require_ok(payload, "project:plan-r1")
            plan_revisions.append(
                _record(
                    {
                        "revision": 1,
                        "task_ids": [item["taskId"] for item in initial_nodes],
                        "action_digest": plan_action["digest"],
                    }
                )
            )
            payload, _ = _invoke(
                module,
                journal,
                key="project:ready-r1",
                tool="projectflow",
                action="ready_nodes",
                arguments={
                    "workspaceDir": str(native_runtime),
                    "payload": {"projectId": project_id},
                },
            )
            _require_ok(payload, "project:ready-r1")
            for domain in runtime_domains:
                task_id = f"{project_id}-{domain}-a1"
                logical_plan_task = logical_task_by_domain.get(domain)
                projection = finance_a1_projection if domain == "finance" else projections[domain]
                child = _child_input(
                    run_id=run_id,
                    correlation_id=correlation_id,
                    task_id=task_id,
                    domain=domain,
                    attempt=1,
                    request=request,
                    template=template,
                    coalition=coalition,
                    projection=projection,
                    source_values=runtime_pack.source_values,
                    observed_at=service.formation.clock.now(),
                    context_envelope_digest=context_envelope_digest,
                    execution_plan=execution_plan,
                    logical_plan_task=logical_plan_task,
                    admitted_projection=(
                        admitted_projections[domain] if logical_plan_task is not None else None
                    ),
                )
                input_ref = f"process-inputs/{task_id}.json"
                input_path = output / input_ref
                _write_json(input_path, child)
                spec = _task_spec(
                    run_id=run_id,
                    correlation_id=correlation_id,
                    project_id=project_id,
                    task_id=task_id,
                    role="DOMAIN_WORKER",
                    assignee=assignees[domain],
                    input_ref=input_ref,
                    input_digest=sha256_digest(child),
                    output_schema=DOMAIN_OUTPUT_SCHEMA,
                    attempt=1,
                    projection_digest=projection.digest,
                    context_envelope_digest=context_envelope_digest,
                    execution_plan=execution_plan,
                    logical_plan_task=logical_plan_task,
                )
                binding_payload = {
                    "schema_version": "orgrebase.golden-task-binding.v1",
                    "run_id": run_id,
                    "correlation_id": correlation_id,
                    "project_id": project_id,
                    "task_id": task_id,
                    "role": "DOMAIN_WORKER",
                    "domain": domain,
                    "attempt": 1,
                    "assignee": assignees[domain],
                    "input_digest": sha256_digest(child),
                    "projection_digest": projection.digest,
                    "source_bindings": _trusted_source_bindings(child),
                    "task_purpose": request.purpose,
                    "output_schema_digest": sha256_digest(DOMAIN_OUTPUT_SCHEMA),
                    "expected_result_digest_present": False,
                    "candidate_only": True,
                    "target_writes": 0,
                    "status": "PLANNED",
                    **_runtime_plan_binding(execution_plan, logical_plan_task),
                }
                if context_envelope_digest is not None:
                    binding_payload["context_envelope_digest"] = context_envelope_digest
                binding = _record(binding_payload)
                _delegate_ack(
                    module,
                    journal,
                    runtime=native_runtime,
                    project_id=project_id,
                    task_id=task_id,
                    assignee=assignees[domain],
                    room_id=room_id,
                    spec=spec,
                    binding=binding,
                )
                result, process_receipt = _run_child(
                    repo_root=root,
                    mode="worker",
                    input_path=input_path,
                    output_path=output / "process-outputs" / f"{task_id}.json",
                    ack_action_digest=binding["ack_action_digest"],
                )
                _require_runtime_result_plan_binding(
                    result,
                    plan=execution_plan,
                    task=logical_plan_task,
                )
                process_receipts.append(process_receipt)
                _submit_check_accept(
                    module,
                    journal,
                    runtime=native_runtime,
                    project_id=project_id,
                    task_id=task_id,
                    result=result,
                    binding=binding,
                )
                bindings.append(binding)
                domain_results[domain] = result
            if domain_results["finance"]["status"] != "ABSTAIN" or domain_results["finance"][
                "missing_fields"
            ] != ["price_band"]:
                raise GoldenCompetitionError("FINANCE_ATTEMPT_1_MUST_ABSTAIN_PRICE_BAND")

            required_slots = _required_slots(coalition)

            def review_payload(
                *,
                phase: int,
                task_id: str,
                selected: Mapping[str, dict[str, Any]],
                expected_task_bindings: Mapping[str, Mapping[str, Any]],
                tool_digest: str | None,
                tool_result_digest: str | None,
            ) -> dict[str, Any]:
                expected = {}
                for domain in selected:
                    binding = expected_task_bindings.get(domain)
                    if binding is None:
                        raise GoldenCompetitionError(f"GOLDEN_REVIEW_MANAGER_BINDING_MISSING:{domain}")
                    expected[domain] = {
                        "run_id": run_id,
                        "correlation_id": correlation_id,
                        "task_id": binding["task_id"],
                        "attempt": binding["attempt"],
                        "worker_id": WORKERS[domain],
                        "input_digest": binding["input_digest"],
                        "projection_digest": binding["projection_digest"],
                        "source_bindings": binding["source_bindings"],
                        "task_purpose": binding["task_purpose"],
                        "tool_receipt_digest": tool_digest if domain == "finance" else None,
                        "tool_result_digest": (tool_result_digest if domain == "finance" else None),
                        **{
                            field: binding[field]
                            for field in (
                                "agentteams_execution_plan_digest",
                                "logical_plan_task_id",
                                "logical_plan_task_digest",
                                "formation_receipt_id",
                                "formation_receipt_digest",
                            )
                            if field in binding
                        },
                    }
                return {
                    "schema_version": "orgrebase.golden-reviewer-input.v1",
                    "run_id": run_id,
                    "correlation_id": correlation_id,
                    "task_id": task_id,
                    "phase": phase,
                    "domain_results": [selected[domain] for domain in runtime_domains],
                    "required_slots": required_slots,
                    "expected_bindings": expected,
                    "model_provider": model_provider,
                    "model_id": reviewer_model_id(model_provider),
                    "ollama_endpoint": ollama_endpoint,
                    "candidate_only": True,
                    "target_writes": 0,
                    **_runtime_plan_binding(
                        execution_plan,
                        logical_reviewer_task,
                    ),
                    **(
                        {"context_envelope_digest": context_envelope_digest}
                        if context_envelope_digest is not None
                        else {}
                    ),
                }

            reviewer_input_1 = review_payload(
                phase=1,
                task_id=reviewer_a1,
                selected=domain_results,
                expected_task_bindings={
                    item["domain"]: item for item in bindings if item["role"] == "DOMAIN_WORKER"
                },
                tool_digest=None,
                tool_result_digest=None,
            )
            reviewer_input_path_1 = output / "process-inputs" / f"{reviewer_a1}.json"
            _write_json(reviewer_input_path_1, reviewer_input_1)
            reviewer_spec_1 = _task_spec(
                run_id=run_id,
                correlation_id=correlation_id,
                project_id=project_id,
                task_id=reviewer_a1,
                role="REVIEWER",
                assignee=reviewer,
                input_ref=f"process-inputs/{reviewer_a1}.json",
                input_digest=sha256_digest(reviewer_input_1),
                output_schema=REVIEW_OUTPUT_SCHEMA,
                attempt=1,
                projection_digest=None,
                context_envelope_digest=context_envelope_digest,
                execution_plan=execution_plan,
                logical_plan_task=logical_reviewer_task,
            )
            review_binding_payload_1 = {
                "schema_version": "orgrebase.golden-task-binding.v1",
                "run_id": run_id,
                "correlation_id": correlation_id,
                "project_id": project_id,
                "task_id": reviewer_a1,
                "role": "REVIEWER",
                "domain": "coalition",
                "attempt": 1,
                "assignee": reviewer,
                "input_digest": sha256_digest(reviewer_input_1),
                "projection_digest": None,
                "output_schema_digest": sha256_digest(REVIEW_OUTPUT_SCHEMA),
                "expected_result_digest_present": False,
                "candidate_only": True,
                "target_writes": 0,
                "status": "PLANNED",
                **_runtime_plan_binding(
                    execution_plan,
                    logical_reviewer_task,
                ),
            }
            if context_envelope_digest is not None:
                review_binding_payload_1["context_envelope_digest"] = context_envelope_digest
            review_binding_1 = _record(review_binding_payload_1)
            _delegate_ack(
                module,
                journal,
                runtime=native_runtime,
                project_id=project_id,
                task_id=reviewer_a1,
                assignee=reviewer,
                room_id=room_id,
                spec=reviewer_spec_1,
                binding=review_binding_1,
            )
            reviewer_result_1, process_receipt = _run_child(
                repo_root=root,
                mode="reviewer",
                input_path=reviewer_input_path_1,
                output_path=output / "process-outputs" / f"{reviewer_a1}.json",
                ack_action_digest=review_binding_1["ack_action_digest"],
            )
            _require_runtime_result_plan_binding(
                reviewer_result_1,
                plan=execution_plan,
                task=logical_reviewer_task,
            )
            process_receipts.append(process_receipt)
            if reviewer_result_1.get("status") == "NOT_RUN":
                not_run = _record(
                    {
                        "schema_version": "orgrebase.golden-competition-summary.v1",
                        "status": "NOT_RUN",
                        "reason": (
                            "EXACT_VERTEX_AI_REVIEWER_NOT_AVAILABLE"
                            if model_provider == "vertex-ai"
                            else "DEEPSEEK_REVIEWER_NOT_AVAILABLE" if model_provider == "deepseek"
                            else "EXACT_LOCAL_OLLAMA_REVIEWER_NOT_AVAILABLE"
                        ),
                        "model_provider": model_provider,
                        "run_id": run_id,
                        "correlation_id": correlation_id,
                        "reviewer_result": reviewer_result_1,
                        "canonical_target_writes": 0,
                    }
                )
                _write_json(output / "summary.json", not_run)
                return not_run
            if reviewer_result_1["decision"]["verdict"] != "REPLAN":
                raise GoldenCompetitionError("REVIEWER_ATTEMPT_1_MUST_REPLAN")
            _submit_check_accept(
                module,
                journal,
                runtime=native_runtime,
                project_id=project_id,
                task_id=reviewer_a1,
                result=reviewer_result_1,
                binding=review_binding_1,
            )
            bindings.append(review_binding_1)

            tool_client = ControlledEnterpriseClient(
                enterprise_server.base_url, token=enterprise_server.token
            )
            tool_result, observed_tool_receipt = tool_client.call_dependency_tool(
                run_id=run_id,
                task_id=f"{project_id}-finance-recovery-tool",
                target_id="work:finance-approval",
                graph_digest=coalition.digest,
            )
            tool_receipt = observed_tool_receipt.model_dump(mode="json")
            tool_result_digest = sha256_digest(tool_result)
            tool_invocation = {
                "schema_version": "orgrebase.golden-http-tool-invocation.v1",
                "run_id": run_id,
                "result": tool_result,
                "receipt": tool_receipt,
                "candidate_only": True,
                "target_writes": 0,
            }
            if (
                tool_receipt["status"] != "SUCCEEDED"
                or price_ref not in tool_result["dependencies"]
                or not isinstance(tool_result.get("source_values"), dict)
                or "price_band" not in tool_result["source_values"]
            ):
                raise GoldenCompetitionError("FINANCE_TOOL_SUPPLEMENT_NOT_PROVEN")
            _write_json(output / "tool" / "invocation.json", tool_invocation)
            finance_projection_2 = _projection_with_tool(finance_a1_projection, object_ref=price_ref)
            finance_source_values = dict(runtime_pack.source_values)
            finance_source_values["price_band"] = _source_value(tool_result["source_values"]["price_band"])
            finance_a2 = f"{project_id}-finance-a2"
            reviewer_a2 = f"{project_id}-reviewer-a2"
            finance_logical_task = logical_task_by_domain.get("finance")
            reviewer_retry_dependencies = [
                (finance_a2 if domain == "finance" else f"{project_id}-{domain}-a1")
                for domain in runtime_domains
            ]
            successor_nodes = [
                *initial_nodes,
                {
                    "taskId": finance_a2,
                    "title": "Finance candidate generation after Tool recovery",
                    "assignedTo": assignees["finance"],
                    "dependsOn": [reviewer_a1],
                },
                {
                    "taskId": reviewer_a2,
                    "title": "Coalition slot and provenance review attempt 2",
                    "assignedTo": reviewer,
                    "dependsOn": reviewer_retry_dependencies,
                },
            ]
            payload, replan_action = _invoke(
                module,
                journal,
                key="project:plan-r2",
                tool="projectflow",
                action="plan_dag",
                arguments={
                    "workspaceDir": str(native_runtime),
                    "payload": {"projectId": project_id, "tasks": successor_nodes},
                },
            )
            _require_ok(payload, "project:plan-r2")
            plan_revisions.append(
                _record(
                    {
                        "revision": 2,
                        "task_ids": [item["taskId"] for item in successor_nodes],
                        "action_digest": replan_action["digest"],
                        "reason": "FINANCE_REQUIRED_SLOT_MISSING",
                        "tool_receipt_digest": tool_receipt["digest"],
                    }
                )
            )
            finance_input_2 = _child_input(
                run_id=run_id,
                correlation_id=correlation_id,
                task_id=finance_a2,
                domain="finance",
                attempt=2,
                request=request,
                template=template,
                coalition=coalition,
                projection=finance_projection_2,
                source_values=finance_source_values,
                observed_at=service.formation.clock.now(),
                tool_receipt_digest=tool_receipt["digest"],
                tool_result_digest=tool_result_digest,
                context_envelope_digest=context_envelope_digest,
                execution_plan=execution_plan,
                logical_plan_task=finance_logical_task,
                admitted_projection=(
                    admitted_projections["finance"] if finance_logical_task is not None else None
                ),
            )
            finance_input_path_2 = output / "process-inputs" / f"{finance_a2}.json"
            _write_json(finance_input_path_2, finance_input_2)
            finance_spec_2 = _task_spec(
                run_id=run_id,
                correlation_id=correlation_id,
                project_id=project_id,
                task_id=finance_a2,
                role="DOMAIN_WORKER",
                assignee=assignees["finance"],
                input_ref=f"process-inputs/{finance_a2}.json",
                input_digest=sha256_digest(finance_input_2),
                output_schema=DOMAIN_OUTPUT_SCHEMA,
                attempt=2,
                projection_digest=finance_projection_2.digest,
                context_envelope_digest=context_envelope_digest,
                execution_plan=execution_plan,
                logical_plan_task=finance_logical_task,
            )
            finance_binding_payload_2 = {
                "schema_version": "orgrebase.golden-task-binding.v1",
                "run_id": run_id,
                "correlation_id": correlation_id,
                "project_id": project_id,
                "task_id": finance_a2,
                "role": "DOMAIN_WORKER",
                "domain": "finance",
                "attempt": 2,
                "assignee": assignees["finance"],
                "input_digest": sha256_digest(finance_input_2),
                "projection_digest": finance_projection_2.digest,
                "source_bindings": _trusted_source_bindings(finance_input_2),
                "task_purpose": request.purpose,
                "output_schema_digest": sha256_digest(DOMAIN_OUTPUT_SCHEMA),
                "expected_result_digest_present": False,
                "predecessor_task_id": f"{project_id}-finance-a1",
                "tool_receipt_digest": tool_receipt["digest"],
                "tool_result_digest": tool_result_digest,
                "candidate_only": True,
                "target_writes": 0,
                "status": "PLANNED",
                **_runtime_plan_binding(
                    execution_plan,
                    finance_logical_task,
                ),
            }
            if context_envelope_digest is not None:
                finance_binding_payload_2["context_envelope_digest"] = context_envelope_digest
            finance_binding_2 = _record(finance_binding_payload_2)
            _delegate_ack(
                module,
                journal,
                runtime=native_runtime,
                project_id=project_id,
                task_id=finance_a2,
                assignee=assignees["finance"],
                room_id=room_id,
                spec=finance_spec_2,
                binding=finance_binding_2,
            )
            finance_result_2, process_receipt = _run_child(
                repo_root=root,
                mode="worker",
                input_path=finance_input_path_2,
                output_path=output / "process-outputs" / f"{finance_a2}.json",
                ack_action_digest=finance_binding_2["ack_action_digest"],
            )
            _require_runtime_result_plan_binding(
                finance_result_2,
                plan=execution_plan,
                task=finance_logical_task,
            )
            process_receipts.append(process_receipt)
            if finance_result_2["status"] != "PASS":
                raise GoldenCompetitionError("FINANCE_ATTEMPT_2_MUST_PASS")
            _submit_check_accept(
                module,
                journal,
                runtime=native_runtime,
                project_id=project_id,
                task_id=finance_a2,
                result=finance_result_2,
                binding=finance_binding_2,
            )
            bindings.append(finance_binding_2)
            final_results = {**domain_results, "finance": finance_result_2}
            reviewer_input_2 = review_payload(
                phase=2,
                task_id=reviewer_a2,
                selected=final_results,
                expected_task_bindings={
                    **{item["domain"]: item for item in bindings if item["role"] == "DOMAIN_WORKER"},
                    "finance": finance_binding_2,
                },
                tool_digest=tool_receipt["digest"],
                tool_result_digest=tool_result_digest,
            )
            reviewer_input_path_2 = output / "process-inputs" / f"{reviewer_a2}.json"
            _write_json(reviewer_input_path_2, reviewer_input_2)
            reviewer_spec_2 = _task_spec(
                run_id=run_id,
                correlation_id=correlation_id,
                project_id=project_id,
                task_id=reviewer_a2,
                role="REVIEWER",
                assignee=reviewer,
                input_ref=f"process-inputs/{reviewer_a2}.json",
                input_digest=sha256_digest(reviewer_input_2),
                output_schema=REVIEW_OUTPUT_SCHEMA,
                attempt=2,
                projection_digest=None,
                context_envelope_digest=context_envelope_digest,
                execution_plan=execution_plan,
                logical_plan_task=logical_reviewer_task,
            )
            review_binding_payload_2 = {
                "schema_version": "orgrebase.golden-task-binding.v1",
                "run_id": run_id,
                "correlation_id": correlation_id,
                "project_id": project_id,
                "task_id": reviewer_a2,
                "role": "REVIEWER",
                "domain": "coalition",
                "attempt": 2,
                "assignee": reviewer,
                "input_digest": sha256_digest(reviewer_input_2),
                "projection_digest": None,
                "output_schema_digest": sha256_digest(REVIEW_OUTPUT_SCHEMA),
                "expected_result_digest_present": False,
                "candidate_only": True,
                "target_writes": 0,
                "status": "PLANNED",
                **_runtime_plan_binding(
                    execution_plan,
                    logical_reviewer_task,
                ),
            }
            if context_envelope_digest is not None:
                review_binding_payload_2["context_envelope_digest"] = context_envelope_digest
            review_binding_2 = _record(review_binding_payload_2)
            _delegate_ack(
                module,
                journal,
                runtime=native_runtime,
                project_id=project_id,
                task_id=reviewer_a2,
                assignee=reviewer,
                room_id=room_id,
                spec=reviewer_spec_2,
                binding=review_binding_2,
            )
            reviewer_result_2, process_receipt = _run_child(
                repo_root=root,
                mode="reviewer",
                input_path=reviewer_input_path_2,
                output_path=output / "process-outputs" / f"{reviewer_a2}.json",
                ack_action_digest=review_binding_2["ack_action_digest"],
            )
            _require_runtime_result_plan_binding(
                reviewer_result_2,
                plan=execution_plan,
                task=logical_reviewer_task,
            )
            process_receipts.append(process_receipt)
            if reviewer_result_2.get("status") == "NOT_RUN":
                not_run = _record(
                    {
                        "schema_version": "orgrebase.golden-competition-summary.v1",
                        "status": "NOT_RUN",
                        "reason": (
                            "EXACT_VERTEX_AI_REVIEWER_NOT_AVAILABLE"
                            if model_provider == "vertex-ai"
                            else "DEEPSEEK_REVIEWER_NOT_AVAILABLE" if model_provider == "deepseek"
                            else "EXACT_LOCAL_OLLAMA_REVIEWER_NOT_AVAILABLE"
                        ),
                        "model_provider": model_provider,
                        "run_id": run_id,
                        "correlation_id": correlation_id,
                        "reviewer_result": reviewer_result_2,
                        "canonical_target_writes": 0,
                    }
                )
                _write_json(output / "summary.json", not_run)
                return not_run
            if reviewer_result_2["decision"]["verdict"] != "PASS":
                raise GoldenCompetitionError("REVIEWER_ATTEMPT_2_MUST_PASS")
            reviewer_model_attempts = [
                _reviewer_model_attempt_evidence(
                    model_provider=model_provider,
                    reviewer_input=reviewer_input_1,
                    reviewer_result=reviewer_result_1,
                ),
                _reviewer_model_attempt_evidence(
                    model_provider=model_provider,
                    reviewer_input=reviewer_input_2,
                    reviewer_result=reviewer_result_2,
                ),
            ]
            if (
                model_provider in {"vertex-ai", "deepseek"}
                and len({item["provider_request_id"] for item in reviewer_model_attempts}) != 2
            ):
                raise GoldenCompetitionError(
                    "GOLDEN_DEEPSEEK_RESPONSE_IDS_MUST_BE_UNIQUE" if model_provider == "deepseek"
                    else "GOLDEN_VERTEX_RESPONSE_IDS_MUST_BE_UNIQUE"
                )
            _submit_check_accept(
                module,
                journal,
                runtime=native_runtime,
                project_id=project_id,
                task_id=reviewer_a2,
                result=reviewer_result_2,
                binding=review_binding_2,
            )
            bindings.append(review_binding_2)

            reviewed_results = _require_reviewed_formation_inputs(reviewer_input_2, final_results)

            domain_result_digests = {
                domain: sha256_digest(reviewed_results[domain]) for domain in runtime_domains
            }
            coalition_result_digest = sha256_digest(
                {
                    "run_id": run_id,
                    "coalition_digest": coalition.digest,
                    "reviewer_result_digest": sha256_digest(reviewer_result_2),
                    "domain_result_digests": domain_result_digests,
                }
            )
            skill_registry = SkillPackageRegistry(root)
            quote_skill = skill_registry.load("enterprise-quote-compose")
            skill_input = {
                "skill_partition": "replay",
                "candidate_program_digest_required": quote_skill.manifest["program_content_digest"],
                "dependency_tool_receipt_digest": tool_receipt["digest"],
                "dependency_result_digest": sha256_digest(tool_invocation["result"]),
                "coalition_result_binding_digest": coalition_result_digest,
                "domain_result_digests": domain_result_digests,
            }
            skill_evaluator = SkillPackageEvaluator(skill_registry)
            skill_evaluation = skill_evaluator.evaluate(
                "enterprise-quote-compose",
                _quote_skill_evaluation_cases(
                    run_id=run_id,
                    public_input=skill_input,
                ),
                evaluated_at="2026-08-27T00:01:00Z",
                premise_lock=qualification_premise(quote_skill),
            )
            if skill_evaluation.get("verdict") != "CANARY":
                raise GoldenCompetitionError("QUOTE_SKILL_EVALUATION_NOT_CANARY")
            skill_release_ledger = SkillReleaseLedger(skill_registry, skill_evaluator)
            for release_state, released_at in (
                ("EVALUATED", "2026-08-27T00:01:10Z"),
                ("SHADOW", "2026-08-27T00:01:20Z"),
                ("CANARY", "2026-08-27T00:01:30Z"),
            ):
                skill_release_ledger.transition(
                    "enterprise-quote-compose",
                    skill_evaluation,
                    to_state=release_state,
                    actor_id=SKILL_REGISTRY_AUTHORITY,
                    reason_codes=(f"GOLDEN_RUN_QUALIFIED_FOR_{release_state}",),
                    created_at=released_at,
                )
            skill_release_history = list(skill_release_ledger.history)
            skill_release_head = skill_release_ledger.head("enterprise-quote-compose")
            if skill_release_head is None or skill_release_head.get("to_state") != "CANARY":
                raise GoldenCompetitionError("QUOTE_SKILL_RELEASE_HEAD_NOT_CANARY")
            skill_invocation = skill_release_ledger.invoke(
                "enterprise-quote-compose",
                skill_input,
                context=InvocationContext(
                    run_id=run_id,
                    task_id=request.id,
                    delegation_id=review_binding_2["digest"],
                    actor_id="worker:gtm-steward",
                ),
                observed_dependencies=quote_skill.manifest["dependencies"],
                created_at="2026-08-27T00:02:00Z",
            )
            if (
                skill_invocation.result.get("action") != "APPLY_QUOTE"
                or skill_invocation.receipt.get("authorization_mode") != "RELEASE"
                or skill_invocation.receipt.get("release_receipt_digest") != skill_release_head["digest"]
            ):
                raise GoldenCompetitionError("QUOTE_SKILL_DID_NOT_PRODUCE_APPLY_CANDIDATE")
            _write_json(output / "skill" / "package.json", quote_skill.manifest)
            _write_json(output / "skill" / "evaluation.json", skill_evaluation)
            _write_json(output / "skill" / "release-ledger.json", skill_release_history)
            _write_json(output / "skill" / "input.json", skill_input)
            _write_json(output / "skill" / "receipt.json", skill_invocation.receipt)
            _write_json(output / "skill" / "result.json", skill_invocation.result)

            payload, _ = _invoke(
                module,
                journal,
                key="project:complete",
                tool="projectflow",
                action="complete_project",
                arguments={
                    "workspaceDir": str(native_runtime),
                    "payload": {"projectId": project_id},
                },
            )
            _require_ok(payload, "project:complete")
            if payload.get("project", {}).get("status") != "completed":
                raise GoldenCompetitionError("GOLDEN_PROJECT_NOT_COMPLETED")

            candidates: list[ClaimCandidate] = []
            bundles: list[DomainCandidateBundle] = []
            for domain in runtime_domains:
                result = reviewed_results[domain]
                candidates.extend(ClaimCandidate.model_validate(item) for item in result["claim_candidates"])
                bundles.append(DomainCandidateBundle.model_validate(result["candidate_bundle"]))
            final_projections = tuple(
                finance_projection_2 if domain == "finance" else projections[domain]
                for domain in runtime_domains
            )
            final_bindings = {
                domain: next(
                    item
                    for item in bindings
                    if item.get("role") == "DOMAIN_WORKER"
                    and item.get("task_id") == reviewed_results[domain]["task_id"]
                )
                for domain in runtime_domains
            }
            observed_initial_domain_ids = tuple(
                str(item["domain"])
                for item in bindings
                if item.get("role") == "DOMAIN_WORKER" and item.get("attempt") == 1
            )
            if observed_initial_domain_ids != actual_agentteams_domain_ids:
                raise GoldenCompetitionError("GOLDEN_AGENTTEAMS_ACTUAL_DOMAIN_BINDING_MISMATCH")
            actual_agentteams_domain_ids = observed_initial_domain_ids
            topology_match = planned_domain_ids == actual_agentteams_domain_ids
            if execution_plan is not None and not topology_match:
                raise GoldenCompetitionError("GOLDEN_AGENTTEAMS_TOPOLOGY_MISMATCH")
            controlled_formation = ControlledAgentTeamsFormationReceipt(
                id=f"controlled-agentteams-formation:{project_id}@v1",
                run_id=run_id,
                correlation_id=correlation_id,
                project_id=project_id,
                source_projections=final_projections,
                task_binding_digests=tuple(sorted(str(item["digest"]) for item in bindings)),
                domain_result_digests=domain_result_digests,
                manager_source_bindings={
                    domain: final_bindings[domain]["source_bindings"] for domain in runtime_domains
                },
                task_purpose=request.purpose,
                claim_candidate_digests=tuple(sorted(item.digest for item in candidates)),
                domain_bundle_digests=tuple(sorted(item.digest for item in bundles)),
                reviewer_input_digest=sha256_digest(reviewer_input_2),
                reviewer_result_digest=sha256_digest(reviewer_result_2),
                tool_receipt_digest=tool_receipt["digest"],
                tool_result_digest=tool_result_digest,
                skill_invocation_receipt_digest=skill_invocation.receipt["digest"],
                source_verification_digest=sha256_digest(source_verification),
                agentteams_action_digests=tuple(str(item["digest"]) for item in journal.actions),
                agentteams_action_count=len(journal.actions),
                agentteams_execution_plan_digest=(
                    execution_plan.digest if execution_plan is not None else None
                ),
                planned_domain_ids=(planned_domain_ids if execution_plan is not None else ()),
                actual_agentteams_domain_ids=(
                    actual_agentteams_domain_ids if execution_plan is not None else ()
                ),
                topology_match=(topology_match if execution_plan is not None else None),
                verified_at=service.formation.clock.now(),
                candidate_target_writes=0,
                canonical_target_writes=0,
                claim_boundary=CLAIM_BOUNDARY,
            )
            prepared = service.formation.prepare_quote_from_candidates(
                request=request,
                template=template,
                interpretation=interpretation,
                revision_lock=revision_lock,
                coalition=coalition,
                source_projections=final_projections,
                claim_candidates=tuple(candidates),
                bundles=tuple(bundles),
                run_id=run_id,
                additional_artifact_writes=(
                    prepare_artifact_write(
                        controlled_formation.id,
                        CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE,
                        controlled_formation.model_dump(mode="json"),
                    ),
                ),
                event_metadata={
                    "execution_mode": "CONTROLLED_LOCAL_AGENTTEAMS",
                    "controlled_agentteams_formation_receipt_digest": (controlled_formation.digest),
                    "candidate_target_writes": 0,
                    "golden_competition_run_id": run_id,
                    "golden_competition_correlation_id": correlation_id,
                    "agentteams_project_id": project_id,
                    "reviewer_result_digest": sha256_digest(reviewer_result_2),
                    "tool_receipt_digest": tool_receipt["digest"],
                    "skill_invocation_receipt_digest": skill_invocation.receipt["digest"],
                    "skill_authorization_mode": "RELEASE",
                    "skill_evaluation_receipt_digest": skill_evaluation["digest"],
                    "skill_release_receipt_digest": skill_release_head["digest"],
                    "skill_release_state": skill_release_head["to_state"],
                    "canonical_target_writes": 0,
                    **(
                        {
                            "agentteams_execution_plan_digest": execution_plan.digest,
                            "task_formation_decision_receipt_digest": (
                                execution_plan.formation_receipt_digest
                            ),
                            "planned_domain_ids": list(planned_domain_ids),
                            "actual_agentteams_domain_ids": list(actual_agentteams_domain_ids),
                            "topology_match": topology_match,
                            "context_freshness_basis": "LOGICAL_EVENT_TIME",
                        }
                        if execution_plan is not None
                        else {}
                    ),
                    **(
                        {"context_envelope_digest": context_envelope_digest}
                        if context_envelope_digest is not None
                        else {}
                    ),
                },
            )
            verify_prepared_formation(
                service.formation,
                prepared,
                media=MEDIA,
                profile_binding_artifact_id=PROFILE_BINDING_ARTIFACT_ID,
                profile_binding_media_type=PROFILE_BINDING_MEDIA_TYPE,
                snapshot_target_ids=service.formation.snapshot_target_ids,
                snapshot_scope_roots=service.formation.snapshot_scope_roots,
            )
            _write_json(
                output / "prepared-formation-bundle.json",
                prepared.model_dump(mode="json"),
            )
            matrix_receipt = {
                "mode": "CONTROLLED_LOCAL_HTTP_STUB",
                "request_count": matrix.state.request_count,
                "unique_assignment_events": len(matrix.state.events),
            }
    finally:
        service.close()

    for binding in bindings:
        _write_json(output / "agentteams" / "bindings" / f"{binding['task_id']}.json", binding)
    _write_json(output / "process-receipts.json", process_receipts)
    action_by_digest = {item["digest"]: item for item in journal.actions}
    manager_process_id = os.getpid()
    domain_processes = [item for item in process_receipts if item["mode"] == "worker"]
    reviewer_processes = [item for item in process_receipts if item["mode"] == "reviewer"]
    observed_process_ids = [int(item["process_id"]) for item in process_receipts]
    if (
        len(domain_processes) != 5
        or len(reviewer_processes) != 2
        or len(set(observed_process_ids)) != len(observed_process_ids)
        or manager_process_id in observed_process_ids
        or any(
            item["started_after_ack_action_digest"] not in action_by_digest
            or action_by_digest[item["started_after_ack_action_digest"]]["action"] != "ack_task"
            for item in process_receipts
        )
    ):
        raise GoldenCompetitionError("GOLDEN_SUBPROCESS_OR_ACK_ORDER_PROOF_INVALID")
    receipt_payload = {
        "schema_version": "orgrebase.golden-competition-summary.v1",
        "status": "PASS",
        "evidence_class": EVIDENCE_CLASS,
        "claim_boundary": CLAIM_BOUNDARY,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "execution_envelope_digest": execution_envelope["digest"],
        "nonce": nonce,
        "project_id": project_id,
        "enterprise_pack": {
            "pack_id": runtime_pack.pack_id,
            "pack_revision": runtime_pack.pack_revision,
            "pack_digest": runtime_pack.pack_digest,
            "synthetic": True,
        },
        "model_provider": model_provider,
        "source_verification": source_verification,
        "plan_revisions": plan_revisions,
        **(
            {
                "agentteams_execution_plan_digest": execution_plan.digest,
                "task_formation_decision_receipt_digest": (
                    execution_plan.formation_receipt_digest
                ),
                "planned_domain_ids": list(planned_domain_ids),
                "actual_agentteams_domain_ids": list(actual_agentteams_domain_ids),
                "topology_match": topology_match,
                "context_freshness_basis": "LOGICAL_EVENT_TIME",
            }
            if execution_plan is not None
            else {}
        ),
        "task_bindings": bindings,
        "agentteams_action_count": len(journal.actions),
        "agentteams_action_digests": [item["digest"] for item in journal.actions],
        "matrix_transport": matrix_receipt,
        "manager_process_id": manager_process_id,
        "independent_domain_worker_processes": len(domain_processes),
        "independent_domain_worker_process_ids": [item["process_id"] for item in domain_processes],
        "independent_reviewer_processes": len(reviewer_processes),
        "independent_reviewer_process_ids": [item["process_id"] for item in reviewer_processes],
        "process_receipt_digests": [item["digest"] for item in process_receipts],
        "finance_attempt_1": {
            "status": domain_results["finance"]["status"],
            "missing_fields": domain_results["finance"]["missing_fields"],
        },
        "reviewer_attempt_1": reviewer_result_1["decision"],
        "reviewer_attempt_1_model_advisory_accepted": reviewer_result_1["model_advisory_accepted"],
        "reviewer_attempt_1_model_advisory_disposition": reviewer_result_1[
            "model_advisory_disposition_reason"
        ],
        "tool_receipt_digest": tool_receipt["digest"],
        "finance_attempt_2": {
            "status": finance_result_2["status"],
            "tool_receipt_digest": finance_result_2["supplemental_tool_receipt_digest"],
        },
        "reviewer_attempt_2": reviewer_result_2["decision"],
        "reviewer_attempt_2_model_advisory_accepted": reviewer_result_2["model_advisory_accepted"],
        "reviewer_attempt_2_model_advisory_disposition": reviewer_result_2[
            "model_advisory_disposition_reason"
        ],
        "reviewer_model_evidence_class": reviewer_result_2["model_receipt"]["evidence_class"],
        "reviewer_model_claim_boundary": reviewer_result_2["model_runtime_binding"]["claim_boundary"],
        "reviewer_model_attempts": reviewer_model_attempts,
        "skill_invocation_receipt_digest": skill_invocation.receipt["digest"],
        "skill_authorization_mode": skill_invocation.receipt["authorization_mode"],
        "skill_evaluation_receipt_digest": skill_evaluation["digest"],
        "skill_evaluation_verdict": skill_evaluation["verdict"],
        "skill_evaluation_partition_count": len({case["partition"] for case in skill_evaluation["case_results"]}),
        "skill_evaluation_case_count": len(skill_evaluation["case_results"]),
        "skill_release_receipt_digest": skill_release_head["digest"],
        "skill_release_state": skill_release_head["to_state"],
        "skill_release_transition_count": len(skill_release_history),
        "skill_action": skill_invocation.result["action"],
        "prepared_formation_digest": prepared.digest,
        "prepared_formation_integrity": "PASS",
        "controlled_agentteams_formation_receipt_digest": (controlled_formation.digest),
        "prepared_quote_ref": prepared.deliverable.ref,
        "prepared_quote_payload": prepared.deliverable.payload,
        "canonical_target_writes": 0,
        "external_promotion_status": "NOT_RUN",
        "project_terminal_state": "completed",
    }
    if context_envelope_digest is not None:
        receipt_payload["context_envelope_digest"] = context_envelope_digest
    receipt = _record(receipt_payload)
    _write_json(output / "summary.json", receipt)
    return receipt


__all__ = [
    "CLAIM_BOUNDARY",
    "EVIDENCE_CLASS",
    "GoldenCompetitionError",
    "_require_reviewed_formation_inputs",
    "run_golden_competition",
]
