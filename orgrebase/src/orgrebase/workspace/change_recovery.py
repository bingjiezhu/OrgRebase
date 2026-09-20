"""Append-only, owner-requested evidence rounds for the same business ChangeSet.

Recovery changes candidate execution, never the proposal, its owner or Apply
authority. Each round has a new preview/gate and a separately reserved AT run.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError, ObjectState
from orgrebase.impact import ImpactEngine
from orgrebase.workspace.advisory import WorkspaceApplyAdvisoryVerifier
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.templates import selected_capability_cards

REQUEST_MEDIA = "application/vnd.orgrebase.change-evidence-request+json"
RESUME_MEDIA = "application/vnd.orgrebase.change-evidence-resume+json"


class ReturnForEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    expected_context_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_id: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1000)
    required_evidence_refs: list[str] = Field(min_length=1, max_length=16)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    ref: str = Field(min_length=1, max_length=512)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ResumeChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    recovery_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executor_id: str = Field(min_length=1, max_length=256)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=16)


def _prefix(event_id: str) -> str:
    return "workspace-change-recovery:" + sha256_digest(event_id)[7:] + ":"


def _sealed(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "digest": sha256_digest(body)}


def _checked(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("digest") != sha256_digest({k: v for k, v in payload.items() if k != "digest"}):
        raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_INVALID")
    return payload


def requests(workspace: Any, event_id: str) -> list[dict[str, Any]]:
    records = [_checked(item.payload) for item in workspace.store.list_artifacts(
        artifact_id_prefix=_prefix(event_id) + "request:", expected_media_type=REQUEST_MEDIA)]
    records.sort(key=lambda item: item["round"])
    event = workspace.changes.get(event_id) if records else None
    previous = None
    for number, record in enumerate(records, 1):
        if (record["event_id"] != event_id or record["event_digest"] != event.digest
                or record["round"] != number or record["previous_request_digest"] != previous
                or record["workspace_id"] != workspace.store.workspace_id
                or record["run_id"] != workspace.effective_workflow_run_id):
            raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_INVALID")
        previous = record["digest"]
    return records


def latest_request(workspace: Any, event_id: str) -> dict[str, Any] | None:
    # Polling needs the current round, not a replay of every evidence round.
    # The existing event subject index selects it; exact reads retain both the
    # artifact integrity checks and its binding to the recorded request event.
    with workspace.store.read_snapshot():
        page = workspace.store.event_page(event_types=("WORKSPACE_CHANGE_EVIDENCE_REQUESTED",),
            subject_key=event_id, descending=True, limit=1)
        if not page["items"]:
            return None
        recorded = page["items"][0]["payload"]
        try:
            number = recorded["round"]
            if type(number) is not int or number < 1:
                raise ValueError("invalid recovery round")
            record = _checked(workspace.store.load_artifact(
                _prefix(event_id) + f"request:{number:04d}", REQUEST_MEDIA).payload)
            event = workspace.changes.get(event_id)
            if (record["event_id"] != event_id or recorded["event_id"] != event_id
                    or record["event_digest"] != event.digest or record["round"] != number
                    or record["digest"] != recorded["recovery_digest"]
                    or record["requested_by"] != recorded["actor_id"]
                    or record["workspace_id"] != workspace.store.workspace_id
                    or record["run_id"] != workspace.effective_workflow_run_id):
                raise ValueError("recovery request binding differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_INVALID") from exc
        return record


def resume_record(workspace: Any, event_id: str, request: dict[str, Any] | None = None) -> dict[str, Any] | None:
    request = request or latest_request(workspace, event_id)
    if request is None:
        return None
    try:
        record = _checked(workspace.store.load_artifact(
            _prefix(event_id) + f"resume:{request['round']:04d}", RESUME_MEDIA).payload)
    except KeyError:
        return None
    if record["request_digest"] != request["digest"] or record["event_digest"] != request["event_digest"]:
        raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_INVALID")
    return record


def round_suffix(workspace: Any, event_id: str) -> str:
    request = latest_request(workspace, event_id)
    return f"@recovery-{request['round']}" if request else "@r1"


def attempt_command(workspace: Any, event_id: str) -> str:
    request = latest_request(workspace, event_id)
    return f"change:{event_id}:recovery:{request['round']}" if request else f"change:{event_id}"


def _capture(workspace: Any, event_id: str):
    spec = workspace.change_spec(event_id)
    fixture = workspace._fixture_for_change(spec)
    change_set = workspace.change_builder.build(fixture=fixture, spec=spec)
    preview = ImpactEngine(fixture).preview(change_set)
    plan, _ = workspace.advisory_factory.compile(fixture=fixture, change_set=change_set, preview=preview)
    return fixture, change_set, preview, plan


def _catalog(workspace: Any, fixture: Any, plan: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from orgrebase.workspace.formation import MEDIA
    from orgrebase.workspace.models import CoalitionPlan
    formation = workspace._formation_record()
    if formation is None:
        raise IntegrityError("CHANGE_RECOVERY_FORMATION_REQUIRED")
    coalition = CoalitionPlan.model_validate(workspace.store.load_artifact(
        formation.coalition_plan_ref, MEDIA["coalition"]).payload)
    cards = {card.worker_id: card for card in selected_capability_cards(coalition).values()}
    tasks, evidence = [], {}
    for task in plan.tasks:
        # Only an admitted domain capability can receive supplementary source
        # material. The coordinator and independent reviewer cannot be replaced.
        card = cards.get(task.agent_name)
        if card is None or task.allowed_output_kinds != ("SemanticExplanation",):
            continue
        if card.domain_id != task.authority_domain:
            raise IntegrityError("CHANGE_RECOVERY_EXECUTOR_DOMAIN_MISMATCH")
        capability_binding = {"capability_ref": card.ref, "capability_digest": card.digest,
            "coalition_ref": coalition.id, "coalition_digest": coalition.digest}
        allowed = []
        for object_id in task.context_scope:
            source = fixture.object(object_id)
            if source.domain != task.authority_domain:
                raise IntegrityError("CHANGE_RECOVERY_DOMAIN_MISMATCH")
            if source.state not in {ObjectState.CURRENT, ObjectState.ACTIVE}:
                raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_NOT_CURRENT")
            evidence[source.ref] = source
            allowed.append(source.ref)
        tasks.append({"task_id": task.id, "actor_id": task.agent_name, "domain_id": task.authority_domain,
            "label": task.authority_domain, "label_en": task.authority_domain,
            "evidence_refs": allowed, "allowed_executors": [
                {"executor_id": card.worker_id, "label": card.worker_id, "label_en": card.worker_id,
                 **capability_binding, "instance_kind": "PRIMARY"},
                {"executor_id": card.worker_id + "-backup", "label": card.worker_id + "（替补实例）",  # noqa: RUF001
                 "label_en": card.worker_id + " (replacement instance)", **capability_binding,
                 "instance_kind": "REPLACEMENT"},
            ]})
    return tasks, evidence


def _owner(workspace: Any, event_id: str) -> str:
    from orgrebase.workspace.approval_authority import require_approval_actor
    actor = require_action(workspace, "approve")
    if request_principal.get() is None:
        actor = workspace.changes.get(event_id).owner_id
    require_approval_actor(workspace, event_id, actor)
    return actor


def _require_pending(workspace: Any, event_id: str) -> None:
    workspace._require_ungrouped(event_id)
    if workspace._approval_record(event_id) is not None or workspace._outcome_record(event_id) is not None:
        raise IntegrityError("CHANGE_RECOVERY_ALREADY_APPROVED")
    if workspace._change_status(event_id) not in {"RECEIVED", "PREVIEWED", "EVIDENCE_REQUIRED"}:
        raise IntegrityError("CHANGE_RECOVERY_NOT_PENDING")
    if workspace.advisory_factory.native_config is None:
        raise IntegrityError("CHANGE_RECOVERY_NATIVE_EXECUTION_REQUIRED")


def recovery_detail(workspace: Any, event_id: str, *, observed: tuple[str, Any] | None = None) -> dict[str, Any]:
    from orgrebase.workspace.preview_execution import read_attempt_summary
    require_action(workspace, "read")
    event = workspace.changes.get(event_id)
    records = requests(workspace, event_id)
    current = records[-1] if records else None
    resumed = resume_record(workspace, event_id, current)
    status, saved_preview = observed if observed is not None else (
        workspace._change_status(event_id), workspace._preview_record(event_id))
    can_capture = status in {"RECEIVED", "PREVIEWED", "EVIDENCE_REQUIRED"} and workspace._formation_record() is not None
    if can_capture:
        fixture, change_set, preview, plan = _capture(workspace, event_id)
        tasks, evidence = _catalog(workspace, fixture, plan)
        change_digest, preview_digest = change_set.digest, preview.digest
    else:
        # Applied/retired evidence must remain readable after its predecessor
        # disappears from the current graph. Never rebuild history on a GET.
        tasks, evidence = [], {}
        bundle = (saved_preview or {}).get("bundle", {})
        change_digest = bundle.get("change_set", {}).get("digest")
        preview_digest = bundle.get("preview", {}).get("digest")
    attempt = read_attempt_summary(workspace, command=attempt_command(workspace, event_id))
    native = (saved_preview or {}).get("native_execution") or (attempt or {}).get("native_execution")
    context = {"event_id": event_id, "event_digest": event.digest,
        "change_set_digest": change_digest, "preview_digest": preview_digest,
        "previous_preview_artifact_id": (saved_preview or {}).get("artifact_id"),
        "previous_preview_artifact_digest": (saved_preview or {}).get("artifact_digest"),
        "previous_native_receipt_digest": (native or {}).get("receipt_digest"),
        "recovery_digest": (resumed or current or {}).get("digest"),
        "attempt_state": (attempt or {}).get("state")}
    state = "NONE"
    if current:
        state = ("READY_FOR_REVIEW" if saved_preview else "FAILED" if (attempt or {}).get("state") in
                 {"FAILED", "RESULT_UNKNOWN"} else "RESUMING" if resumed else "NEEDS_EVIDENCE")
    uncertain = ((attempt or {}).get("state") in {"IN_PROGRESS", "RESULT_UNKNOWN"}
        or (native or {}).get("status") == "RESULT_UNKNOWN"
        or ((attempt or {}).get("native_execution") or {}).get("status") == "RESULT_UNKNOWN"
        or any(item.get("dispatch_state") == "SENT_UNKNOWN"
               for item in (attempt or {}).get("receipt_summaries", [])))
    actions = []
    try:
        if status not in {"RECEIVED", "PREVIEWED", "EVIDENCE_REQUIRED"} or workspace.advisory_factory.native_config is None:
            raise IntegrityError("CHANGE_RECOVERY_NOT_PENDING")
        if not uncertain:
            try:
                _owner(workspace, event_id)
                if saved_preview or (attempt or {}).get("state") == "FAILED":
                    actions.append("RETURN_FOR_EVIDENCE")
            except (AuthenticationError, AuthorizationError, FreshnessError):
                pass
        if current and not resumed:
            try:
                require_action(workspace, "propose")
                actions.append("RESUME")
            except (AuthenticationError, AuthorizationError):
                pass
    except (IntegrityError, FreshnessError):
        pass
    return {"state": state, "round": current["round"] if current else 0,
        "context_digest": sha256_digest(context), "recovery_digest": (resumed or current or {}).get("digest"),
        "owner_id": event.owner_id, "requested_by": (current or {}).get("requested_by"),
        "submitted_by": (resumed or {}).get("submitted_by"),
        "reason": (current or {}).get("reason"), "task_id": (current or {}).get("task_id"),
        "required_evidence_refs": (current or {}).get("required_evidence_refs", []),
        "tasks": tasks, "evidence_options": [{"ref": item.ref, "digest": item.digest,
            "domain_id": item.domain, "label": item.id, "label_en": item.id} for item in evidence.values()],
        "preserved_context": current["preserved_context"] if current else context, "current_context": context,
        "history": [{"round": item["round"], "digest": item["digest"], "requested_by": item["requested_by"],
            "reason": item["reason"], "task_id": item["task_id"],
            "resume_digest": (resume_record(workspace, event_id, item) or {}).get("digest")}
            for item in records], "allowed_actions": actions,
        "executor": resumed["executor"] if resumed else None,
        "consumed_evidence": [{"ref": item["ref"], "digest": item["digest"]} for item in resumed["evidence"]] if resumed else [],
        "execution_uncertain": uncertain, "candidate_only": True, "target_writes": 0,
        "execution_boundary": "REGISTERED_CAPABILITY_INSTANCES_SHARED_PROVIDER_CONTROLLED_LOCAL_AT"}


def return_for_evidence(workspace: Any, event_id: str, request: ReturnForEvidenceInput) -> dict[str, Any]:
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        actor = _owner(workspace, event_id)
        command_digest = sha256_digest({"command": request.model_dump(mode="json"), "actor": actor})
        previous = requests(workspace, event_id)
        for record in previous:
            if record["operation_id"] == request.operation_id:
                if record["command_digest"] != command_digest:
                    raise IntegrityError("CHANGE_RECOVERY_COMMAND_CONFLICT")
                return recovery_detail(workspace, event_id)
        _require_pending(workspace, event_id)
        if not request.reason.strip():
            raise IntegrityError("CHANGE_RECOVERY_REASON_REQUIRED")
        detail = recovery_detail(workspace, event_id)
        if request.expected_context_digest != detail["context_digest"]:
            raise IntegrityError("CHANGE_RECOVERY_CONTEXT_CHANGED")
        if "RETURN_FOR_EVIDENCE" not in detail["allowed_actions"]:
            raise IntegrityError("CHANGE_RECOVERY_RETURN_NOT_AVAILABLE")
        task = next((task for task in detail["tasks"] if task["task_id"] == request.task_id), None)
        refs = request.required_evidence_refs
        if task is None or len(set(refs)) != len(refs) or not set(refs) <= set(task["evidence_refs"]):
            raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_SCOPE")
        record = _sealed({"schema_version": "orgrebase.change-evidence-request.v1", "event_id": event_id,
            "event_digest": workspace.changes.get(event_id).digest, "workspace_id": workspace.store.workspace_id,
            "run_id": workspace.effective_workflow_run_id, "round": len(previous) + 1,
            "previous_request_digest": previous[-1]["digest"] if previous else None,
            "operation_id": request.operation_id, "command_digest": command_digest,
            "requested_by": actor, "reason": request.reason, "task_id": request.task_id,
            "domain_id": task["domain_id"], "required_evidence_refs": refs,
            "preserved_context": detail["current_context"],
            "created_at": workspace.clock.now()})
        workspace.store.save_artifact(connection, _prefix(event_id) + f"request:{record['round']:04d}", REQUEST_MEDIA, record)
        workspace.store.append_event(connection, "WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event_id, "round": record["round"], "recovery_digest": record["digest"], "actor_id": actor})
    return recovery_detail(workspace, event_id)


def require_recovery_inputs(workspace: Any, event_id: str) -> dict[str, Any] | None:
    request = latest_request(workspace, event_id)
    if request is None:
        return None
    record = resume_record(workspace, event_id, request)
    if record is None:
        raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_REQUIRED")
    fixture, change_set, preview, plan = _capture(workspace, event_id)
    tasks, evidence = _catalog(workspace, fixture, plan)
    if (change_set.digest != request["preserved_context"]["change_set_digest"]
            or preview.digest != request["preserved_context"]["preview_digest"]):
        raise IntegrityError("CHANGE_RECOVERY_CONTEXT_CHANGED")
    task = next((item for item in tasks if item["task_id"] == request["task_id"]), None)
    if task is None or record["executor"] not in task["allowed_executors"]:
        raise IntegrityError("CHANGE_RECOVERY_EXECUTOR_NOT_ADMITTED")
    for item in record["evidence"]:
        source = evidence.get(item["ref"])
        if (source is None or source.digest != item["digest"] or item["ref"] not in task["evidence_refs"]
                or source.model_dump(mode="json") != item["object"]):
            raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_CHANGED")
    if {item["ref"] for item in record["evidence"]} != set(request["required_evidence_refs"]):
        raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_REQUIRED")
    return record


def recovery_adapter(workspace: Any, event_id: str):
    context = require_recovery_inputs(workspace, event_id)
    if context is None:
        return workspace.advisory_factory, workspace.advisory_verifier
    adapter = copy.copy(workspace.advisory_factory)
    adapter.recovery_context = context
    return adapter, WorkspaceApplyAdvisoryVerifier(adapter, clock=workspace.clock.now)


def _previous_candidate(workspace: Any, request: dict[str, Any]) -> dict[str, Any] | None:
    """Retain the selected domain's previous checked output, never another domain's context."""
    context = request["preserved_context"]
    artifact_id = context.get("previous_preview_artifact_id")
    if not artifact_id:
        # A failed attempt has no admitted preview. Its request chain still
        # identifies the previous immutable, successful domain candidate.
        earlier = requests(workspace, request["event_id"])
        previous = next((item for item in earlier if item["round"] == request["round"] - 1), None)
        return _previous_candidate(workspace, {**previous, "task_id": request["task_id"],
            "domain_id": request["domain_id"]}) if previous else None
    from orgrebase.workspace.models import WorkspacePreviewBundle
    from orgrebase.workspace.service import WORKSPACE_PREVIEW_MEDIA_TYPE
    artifact = workspace.store.load_artifact(artifact_id, WORKSPACE_PREVIEW_MEDIA_TYPE)
    if artifact.payload_digest != context["previous_preview_artifact_digest"]:
        raise IntegrityError("CHANGE_RECOVERY_PREVIOUS_PREVIEW_CHANGED")
    bundle = WorkspacePreviewBundle.model_validate(artifact.payload)
    if bundle.change_set.digest != context["change_set_digest"]:
        raise IntegrityError("CHANGE_RECOVERY_PREVIOUS_PREVIEW_CHANGED")
    handoff = next((item for item in bundle.advisory.handoffs if item.task_id == request["task_id"]), None)
    task = next((item for item in bundle.advisory.orchestration_plan.tasks if item.id == request["task_id"]), None)
    if handoff is None or task is None or task.authority_domain != request["domain_id"]:
        raise IntegrityError("CHANGE_RECOVERY_PREVIOUS_TASK_MISMATCH")
    return handoff.model_dump(mode="json")


def resume_change(workspace: Any, event_id: str, command: ResumeChangeInput) -> dict[str, Any]:
    with workspace._command_lock, workspace.store.transaction() as connection:
        from orgrebase.workspace.enterprise_binding import lock_binding_scope
        lock_binding_scope(workspace, connection)
        actor = require_action(workspace, "propose")
        request = latest_request(workspace, event_id)
        if request is None or request["digest"] != command.recovery_digest:
            raise IntegrityError("CHANGE_RECOVERY_DIGEST_MISMATCH")
        digest = sha256_digest({"command": command.model_dump(mode="json"), "actor": actor})
        old = resume_record(workspace, event_id, request)
        if old is not None:
            if old["command_digest"] != digest:
                raise IntegrityError("CHANGE_RECOVERY_COMMAND_CONFLICT")
        else:
            _require_pending(workspace, event_id)
            fixture, _, _, plan = _capture(workspace, event_id)
            tasks, sources = _catalog(workspace, fixture, plan)
            task = next((item for item in tasks if item["task_id"] == request["task_id"]), None)
            if task is None:
                raise IntegrityError("CHANGE_RECOVERY_EXECUTOR_NOT_ADMITTED")
            executor = next((item for item in task["allowed_executors"] if item["executor_id"] == command.executor_id), None)
            if executor is None:
                raise IntegrityError("CHANGE_RECOVERY_EXECUTOR_NOT_ADMITTED")
            if (len({item.ref for item in command.evidence}) != len(command.evidence)
                    or {item.ref for item in command.evidence} != set(request["required_evidence_refs"])):
                raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_REQUIRED")
            evidence = []
            for item in command.evidence:
                source = sources.get(item.ref)
                if source is None or source.digest != item.digest or item.ref not in task["evidence_refs"]:
                    raise IntegrityError("CHANGE_RECOVERY_EVIDENCE_CHANGED")
                evidence.append({"ref": source.ref, "digest": source.digest, "object": source.model_dump(mode="json")})
            record = _sealed({"schema_version": "orgrebase.change-evidence-resume.v1",
                "event_id": event_id, "event_digest": request["event_digest"], "round": request["round"],
                "request_digest": request["digest"], "operation_id": command.operation_id,
                "command_digest": digest, "submitted_by": actor, "reason": request["reason"],
                "task_id": request["task_id"], "domain_id": request["domain_id"],
                "preserved_context": request["preserved_context"], "executor": executor,
                "evidence": evidence, "previous_candidate": _previous_candidate(workspace, request),
                "submitted_at": workspace.clock.now()})
            workspace.store.save_artifact(connection, _prefix(event_id) + f"resume:{request['round']:04d}", RESUME_MEDIA, record)
            workspace.store.append_event(connection, "WORKSPACE_CHANGE_EVIDENCE_SUPPLIED", {
                "event_id": event_id, "round": request["round"], "recovery_digest": record["digest"],
                "actor_id": actor, "executor_id": executor["executor_id"]})
    workspace.preview_change(event_id)
    return recovery_detail(workspace, event_id)


def require_recovery_approval(workspace: Any, event_id: str, digest: str | None) -> None:
    request = latest_request(workspace, event_id)
    if request is not None:
        context = require_recovery_inputs(workspace, event_id)
        if digest != context["digest"]:
            raise IntegrityError("CHANGE_RECOVERY_APPROVAL_BINDING_REQUIRED")
