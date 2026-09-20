"""Pinned, process-isolated AgentTeams lifecycle for one change candidate round.

The existing advisory plan owns candidate work. This adapter materializes that
plan and a contract-review task in TeamHarness; it cannot approve or Apply.
Matrix and object-storage transport remain explicitly controlled-local.
"""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.process_policy import candidate_environment
from orgrebase.workspace.native_taskflow import (
    ControlledLocalMatrix,
    LifecycleJournal,
    NativeActionReceipt,
    SourceVerification,
    _create_mc_process_adapter,
    _environment,
    _invoke,
    _payload_status,
    _public_path_replacements,
    _require_accepted,
    _require_effective_check,
    _require_ok,
    _require_task_state,
    _source_verification_from_lock,
    _unwrap_call_tool,
    load_pinned_teamharness,
    verify_teamharness_checkout,
)

_SCHEMA = "orgrebase.change-agentteams-execution.v1"
_RUNTIME = "orgrebase://runtime-workspace"
_ROOM = "!change-advisory:controlled.local"
_REVIEW = "DETERMINISTIC_CONTRACT_REVIEW"
_MODE = "PINNED_MCP_SUBPROCESS_CONTROLLED_LOCAL_TRANSPORT"
NATIVE_PROGRESS_MEDIA = "application/vnd.orgrebase.change-agentteams-progress+json"


def _sealed(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": sha256_digest(value)}


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        char in "0123456789abcdef" for char in value[7:]
    )


@dataclass(frozen=True)
class ChangeAgentTeamsConfig:
    checkout: Path
    lock_path: Path
    evidence_root: Path

    @property
    def configuration_binding(self) -> dict[str, Any]:
        return {"mode": _MODE, "source": _source_verification_from_lock(self.lock_path)}

    def require_available(self) -> None:
        try:
            verify_teamharness_checkout(self.checkout, self.lock_path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_UNAVAILABLE") from exc


class NativeChangeExecutionError(IntegrityError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def native_tenant_id(workspace: Any) -> str:
    return workspace.store.tenant_id or workspace.profile.organization_id


def execution_binding(*, tenant_id: str, workspace_id: str, attempt_key: str,
                      request_digest: str, change_set: Any, preview: Any,
                      run_envelope: Any, plan: Any) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id, "workspace_id": workspace_id,
        "attempt_key": attempt_key, "request_digest": request_digest,
        "run_id": run_envelope.run_id, "nonce": run_envelope.nonce,
        "change_set_ref": f"{change_set.id}@{change_set.revision}",
        "change_set_digest": change_set.digest, "preview_digest": preview.digest,
        "revision_lock_digest": preview.revision_lock.digest, "plan_digest": plan.digest,
    }


def _tasks(plan: Any, project_id: str) -> list[dict[str, Any]]:
    ids = {task.id: f"{project_id}-t{index:03d}" for index, task in enumerate(plan.tasks, 1)}
    tasks = [{
        "logical_task_id": task.id, "task_id": ids[task.id], "actor_id": task.agent_name,
        "kind": "CANDIDATE", "delegation_digest": task.digest,
        "depends_on": [ids[item] for item in task.depends_on],
    } for task in plan.tasks]
    tasks.append({
        "logical_task_id": f"{plan.id}:contract-review", "task_id": f"{project_id}-review",
        "actor_id": "independent-contract-reviewer", "kind": _REVIEW,
        "delegation_digest": sha256_digest({"plan_digest": plan.digest, "review_kind": _REVIEW}),
        "depends_on": list(ids.values()),
    })
    return tasks


def _spec(binding: Mapping[str, Any], task: Mapping[str, Any]) -> str:
    return json.dumps({"binding": dict(binding), "task": dict(task),
                       "candidate_only": True, "target_writes": 0}, sort_keys=True, separators=(",", ":"))


def _request(action: str, payload: dict[str, Any], role: str | None = None) -> dict[str, Any]:
    return {"action": action, "workspaceDir": _RUNTIME, "payload": payload,
            **({"role": role} if role is not None else {})}


def _project_requests(project_id: str, tasks: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("project:create", "projectflow", _request("create_project", {
            "projectId": project_id, "title": "Change candidate and independent contract review"})),
        ("project:plan", "projectflow", _request("plan_dag", {"projectId": project_id, "tasks": [
            {"taskId": task["task_id"], "title": task["kind"],
             "assignedTo": f"@{task['actor_id']}:controlled.local", "dependsOn": task["depends_on"]}
            for task in tasks]})),
    ]


def _task_requests(binding: Mapping[str, Any], project_id: str, task: dict[str, Any],
                   result: Mapping[str, Any] | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    task_id = task["task_id"]
    if result is None:
        return [
            (f"{task_id}:delegate", "taskflow", _request("delegate_task", {
                "projectId": project_id, "taskId": task_id,
                "assignedTo": f"@{task['actor_id']}:controlled.local", "roomId": _ROOM,
                "spec": _spec(binding, task)}, "leader")),
            (f"{task_id}:ack", "taskflow", _request("ack_task", {"taskId": task_id}, "worker")),
        ]
    summary = json.dumps(dict(result), sort_keys=True, separators=(",", ":"))
    return [
        (f"{task_id}:submit", "taskflow", _request("submit_task", {
            "taskId": task_id, "status": "SUCCESS", "summary": summary, "deliverables": []}, "worker")),
        (f"{task_id}:check", "taskflow", _request("check_task", {"taskId": task_id}, "leader")),
        (f"{task_id}:accept", "projectflow", _request("accept_task_result", {
            "projectId": project_id, "taskId": task_id, "resultStatus": "SUCCESS", "accepted": True})),
    ]


def _check_response(request: Mapping[str, Any], payload: Mapping[str, Any],
                    *, spec: str | None, result: Mapping[str, Any] | None,
                    project_id: str | None = None) -> None:
    action = request["action"]
    _require_ok(payload, action)
    tool = "projectflow" if action in {"create_project", "plan_dag", "accept_task_result", "complete_project"} else "taskflow"
    if payload.get("tool") != tool or payload.get("action") != action:
        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESPONSE_MISMATCH")
    expected_project = request["payload"].get("projectId") or project_id
    if tool == "projectflow" and payload.get("project", {}).get("project_id") != expected_project:
        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_PROJECT_MISMATCH")
    states = {"delegate_task": "assigned", "ack_task": "in_progress", "submit_task": "submitted"}
    if action in states:
        _require_task_state(payload, states[action], action)
    if action in {*states, "check_task"}:
        expected_actor = f"@{json.loads(str(spec))['task']['actor_id']}:controlled.local"
        if payload.get("task", {}).get("task_id") != request["payload"]["taskId"]:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_TASK_MISMATCH")
        if expected_project and payload.get("task", {}).get("project_id") != expected_project:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_PROJECT_MISMATCH")
        if (payload.get("task", {}).get("assigned_to") != expected_actor
                or payload.get("task", {}).get("room_id") != _ROOM):
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_ASSIGNMENT_MISMATCH")
    if action == "delegate_task":
        notification = payload.get("notification", {})
        if (not notification.get("eventId") or notification.get("assignee") != expected_actor
                or notification.get("roomId") != _ROOM):
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_ASSIGNMENT_MISMATCH")
    if action == "ack_task" and payload.get("spec") != str(spec) + "\n":
        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_SPEC_MISMATCH")
    if action == "check_task":
        _require_effective_check(payload, action)
        expected = json.dumps(dict(result or {}), sort_keys=True, separators=(",", ":"))
        if payload.get("result", {}).get("summary") != expected:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_MISMATCH")
    if action == "accept_task_result":
        _require_accepted(payload, action)
        if payload.get("taskId") != request["payload"]["taskId"]:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_TASK_MISMATCH")
    if action == "complete_project" and payload.get("project", {}).get("status") != "completed":
        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_TERMINAL_MISSING")


def candidate_result(task: Any, handoff: Any, agent_run: Any) -> dict[str, Any]:
    return {"logical_task_id": task.id, "delegation_digest": task.digest,
            "handoff_digest": handoff.digest, "agent_run_digest": agent_run.digest,
            "candidate_only": True, "target_writes": 0}


def contract_review_result(advisory: Any) -> dict[str, Any]:
    value = advisory.model_dump(mode="json")
    value.pop("native_execution", None)
    return {"review_kind": _REVIEW, "verdict": "PASS", "reviewed_bundle_digest": sha256_digest(value),
            "reviewed_handoff_digests": [item.digest for item in advisory.handoffs],
            "candidate_only": True, "target_writes": 0}


def progress_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Public process evidence; no MCP bodies, local paths or candidate text."""
    actions = [{key: action.get(key) for key in ("sequence", "key", "tool", "action", "status", "ok", "digest")}
               for action in receipt.get("actions", [])]
    tasks = []
    for task in receipt.get("tasks", []):
        task_actions = [item for item in actions if str(item["key"]).startswith(task["task_id"] + ":")]
        last = task_actions[-1] if task_actions else None
        tasks.append({**task, "status": last["status"] if last else "planned",
                      "last_action": last["action"] if last else None,
                      "action_receipt_digests": [item["digest"] for item in task_actions]})
    reviews = [item for item in receipt.get("results", []) if item.get("review_kind") == _REVIEW]
    return {"schema_version": _SCHEMA, "view": "PROGRESS_SUMMARY",
            "status": receipt["status"], "receipt_digest": receipt["digest"],
            "binding": receipt["binding"], "project_id": receipt["project_id"],
            "native_agentteams_observed": receipt["native_agentteams_observed"],
            "evidence_class": receipt["evidence_class"], "distributed_execution": False,
            "candidate_only": True, "target_writes": 0, "tasks": tasks, "actions": actions,
            "review_kind": _REVIEW, "review": reviews[-1] if reviews else None}


def verified_native_bundle(workspace: Any, bundle: Any) -> dict[str, Any] | None:
    """Read a persisted preview's exact attempt and verify its native transcript."""
    from orgrebase.workspace.models import WorkspacePreviewBundle
    if isinstance(bundle, dict):
        bundle = WorkspacePreviewBundle.model_validate(bundle)
    receipt = bundle.advisory.native_execution
    if receipt is None:
        return None
    try:
        binding = receipt["binding"]
        reservation = workspace.store.get_idempotent(binding["attempt_key"], binding["request_digest"])
        if reservation is None or reservation["run_envelope"] != bundle.run_envelope.model_dump(mode="json"):
            raise ValueError("reservation")
        saved = workspace.store.load_artifact(binding["attempt_key"],
            "application/vnd.orgrebase.preview-candidate-attempt+json").payload
        if (saved["status"] != "COMPLETE" or saved["request_digest"] != binding["request_digest"]
                or saved["advisory"] != bundle.advisory.model_dump(mode="json")):
            raise ValueError("attempt result")
        expected = execution_binding(tenant_id=native_tenant_id(workspace), workspace_id=workspace.store.workspace_id,
            attempt_key=binding["attempt_key"], request_digest=binding["request_digest"],
            change_set=bundle.change_set, preview=bundle.preview, run_envelope=bundle.run_envelope,
            plan=bundle.advisory.orchestration_plan)
        verify_execution(receipt, plan=bundle.advisory.orchestration_plan, advisory=bundle.advisory,
                         expected_binding=expected, config=None)
        return progress_summary(receipt)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise IntegrityError("WORKSPACE_ADVISORY_AGENTTEAMS_EVIDENCE_INVALID") from exc


def verify_execution(receipt: Mapping[str, Any], *, plan: Any, advisory: Any,
                     expected_binding: Mapping[str, Any], config: ChangeAgentTeamsConfig | None) -> None:
    """Verify the exact native transcript, not a boolean or an action count."""
    try:
        body = {key: value for key, value in receipt.items() if key != "digest"}
        if receipt.get("digest") != sha256_digest(body) or receipt.get("schema_version") != _SCHEMA:
            raise ValueError("receipt")
        if receipt.get("binding") != dict(expected_binding):
            raise ValueError("binding")
        if not all(isinstance(value, str) and value for value in expected_binding.values()):
            raise ValueError("identity")
        if not all(_valid_digest(expected_binding[name]) for name in (
            "request_digest", "change_set_digest", "preview_digest", "revision_lock_digest", "plan_digest")):
            raise ValueError("digests")
        if (receipt.get("status") != "COMPLETED" or receipt.get("native_agentteams_observed") is not True
                or receipt.get("candidate_only") is not True or receipt.get("target_writes") != 0
                or receipt.get("mode") != _MODE or receipt.get("evidence_class") != "CONTROLLED_LOCAL_NATIVE_TASKFLOW"
                or receipt.get("distributed_execution") is not False):
            raise ValueError("boundary")
        if config is not None and receipt.get("source_verification") != config.configuration_binding["source"]:
            raise ValueError("source")
        SourceVerification.model_validate(receipt.get("source_verification"))
        project_id = "orgrebase-change-" + sha256_digest(dict(expected_binding))[7:31]
        tasks = _tasks(plan, project_id)
        if receipt.get("project_id") != project_id or receipt.get("tasks") != tasks:
            raise ValueError("plan")
        calls: list[tuple[str, str, dict[str, Any], dict[str, Any] | None, str | None]] = [
            (*item, None, None) for item in _project_requests(project_id, tasks)]
        results = [candidate_result(task, handoff, run) for task, handoff, run in
                   zip(plan.tasks, advisory.handoffs, advisory.agent_runs, strict=True)]
        results.append(contract_review_result(advisory))
        if receipt.get("results") != results:
            raise ValueError("results")
        for task, result in zip(tasks, results, strict=True):
            calls.extend((*item, result, _spec(expected_binding, task)) for item in
                         [*_task_requests(expected_binding, project_id, task),
                          *_task_requests(expected_binding, project_id, task, result)])
        calls.append(("project:complete", "projectflow", _request("complete_project", {"projectId": project_id}), None, None))
        actions, raw_actions = receipt["actions"], receipt["raw_actions"]
        if len(actions) != len(calls) or len(raw_actions) != len(calls):
            raise ValueError("action coverage")
        for sequence, (expected, action, raw) in enumerate(zip(calls, actions, raw_actions, strict=True), 1):
            key, tool, request, result, spec = expected
            NativeActionReceipt.model_validate(action)
            payload = _unwrap_call_tool(raw["response"])
            if (action["digest"] != sha256_digest({k: v for k, v in action.items() if k != "digest"})
                    or action["sequence"] != sequence or action["key"] != key or action["tool"] != tool
                    or action["action"] != request["action"] or action["ok"] is not True
                    or action["status"] != _payload_status(payload)
                    or raw["request"] != request or raw["key"] != key or raw["sequence"] != sequence
                    or raw["tool"] != tool or raw["action"] != request["action"]
                    or action["request_digest"] != sha256_digest(request)
                    or action["response_digest"] != sha256_digest(raw["response"])
                    or action["payload_digest"] != sha256_digest(payload)):
                raise ValueError("action binding")
            _check_response(request, payload, spec=spec, result=result, project_id=project_id)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise IntegrityError("WORKSPACE_ADVISORY_AGENTTEAMS_EVIDENCE_INVALID") from exc


class ChangeAdvisoryNativeSession:
    """One attempt, one isolated MCP host, no redispatch or implicit fallback."""

    def __init__(self, config: ChangeAgentTeamsConfig, *, binding: dict[str, Any], plan: Any,
                 deadline_monotonic: float, on_progress: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.config, self.binding, self.plan = config, binding, plan
        self.deadline, self.on_progress = deadline_monotonic, on_progress
        self.project_id = "orgrebase-change-" + sha256_digest(binding)[7:31]
        self.tasks = _tasks(plan, self.project_id)
        self.actions: list[dict[str, Any]] = []
        self.raw_actions: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.source: dict[str, Any] | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._owns_process_group = False
        self.output = Path(config.evidence_root) / self.project_id
        self.status = "RUNNING"

    def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        if time.monotonic() >= self.deadline or self.process is None:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN")
        try:
            assert self.process.stdin is not None and self.process.stdout is not None
            encoded = memoryview((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                while encoded:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN")
                    try:
                        written = os.write(self.process.stdin.fileno(), encoded[:65536])
                    except BlockingIOError:
                        continue
                    encoded = encoded[written:]
                selector.unregister(self.process.stdin)
                selector.register(self.process.stdout, selectors.EVENT_READ)
                received = bytearray()
                while b"\n" not in received:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN")
                    try:
                        chunk = os.read(self.process.stdout.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk or len(received) + len(chunk) > 4_194_304:
                        raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN")
                    received.extend(chunk)
            if time.monotonic() >= self.deadline:
                raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN")
            response = json.loads(received)
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_CALL_FAILED")
            return response
        except (OSError, ValueError) as exc:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN") from exc

    def snapshot(self) -> dict[str, Any]:
        return _sealed({"schema_version": _SCHEMA, "status": self.status,
            "mode": _MODE, "evidence_class": "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
            "native_agentteams_observed": self.status == "COMPLETED", "distributed_execution": False,
            "candidate_only": True, "target_writes": 0, "binding": self.binding,
            "project_id": self.project_id, "source_verification": self.source,
            "tasks": self.tasks, "results": self.results,
            "actions": self.actions, "raw_actions": self.raw_actions})

    def _call(self, item: tuple[str, str, dict[str, Any]], *, spec: str | None = None,
              result: Mapping[str, Any] | None = None) -> None:
        key, tool, request = item
        response = self._exchange({"key": key, "tool": tool, "request": request})
        _check_response(request, response["payload"], spec=spec, result=result, project_id=self.project_id)
        self.actions.append(response["action"])
        self.raw_actions.append(response["raw"])
        if self.on_progress:
            self.on_progress(self.snapshot())

    def start(self) -> None:
        try:
            self.output.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN") from exc
        home = self.output / "home"
        home.mkdir()
        env = candidate_environment(os.environ, home=home)
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([
            str(Path(__file__).resolve().parents[2]),
            *(str(Path(item).resolve()) for item in sys.path if item and Path(item).name == "site-packages"),
        ]))
        self.process = subprocess.Popen(
            [sys.executable, "-m", "orgrebase.workspace.change_agentteams", "--host"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True, start_new_session=True, env=env,
        )
        self._owns_process_group = True
        initialized = self._exchange({"checkout": str(self.config.checkout), "lock_path": str(self.config.lock_path),
            "output": str(self.output.resolve()), "members": [f"@{task['actor_id']}:controlled.local" for task in self.tasks]})
        self.source = initialized["source_verification"]
        if self.source != self.config.configuration_binding["source"]:
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_SOURCE_MISMATCH")
        for item in _project_requests(self.project_id, self.tasks):
            self._call(item)

    def begin_task(self, task: Any) -> None:
        mapped = next(item for item in self.tasks if item["logical_task_id"] == task.id)
        self._begin(mapped)

    def _begin(self, task: dict[str, Any]) -> None:
        for item in _task_requests(self.binding, self.project_id, task):
            self._call(item, spec=_spec(self.binding, task))

    def complete_task(self, task: Any, handoff: Any, agent_run: Any) -> None:
        mapped = next(item for item in self.tasks if item["logical_task_id"] == task.id)
        self._complete(mapped, candidate_result(task, handoff, agent_run))

    def _complete(self, task: dict[str, Any], result: dict[str, Any]) -> None:
        for item in _task_requests(self.binding, self.project_id, task, result):
            self._call(item, spec=_spec(self.binding, task), result=result)
        self.results.append(result)

    def begin_review(self) -> None:
        self._begin(self.tasks[-1])

    def finish(self, advisory: Any) -> dict[str, Any]:
        self._complete(self.tasks[-1], contract_review_result(advisory))
        self._call(("project:complete", "projectflow", _request("complete_project", {"projectId": self.project_id})))
        self.status = "COMPLETED"
        receipt = self.snapshot()
        verify_execution(receipt, plan=self.plan, advisory=advisory,
                         expected_binding=self.binding, config=self.config)
        if self.on_progress:
            self.on_progress(receipt)
        return receipt

    def _signal_owned_process_group(self, sig: int) -> bool:
        assert self.process is not None
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            return False
        except PermissionError as error:
            if sys.platform != "darwin" or self.process.poll() is None:
                raise
            # Darwin returns EPERM for a group containing only unreaped zombies.
            # After reaping our host, allow a brief wait for the group to vanish.
            # Probe only: do not resend a fatal signal to a possibly retired PGID.
            deadline = time.monotonic() + 0.2
            while True:
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    return False
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise error from None
                    time.sleep(0.01)
                else:
                    # A live/signalable group is not the vanished-group case.
                    raise error from None
        return True

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.stdin:
            with contextlib.suppress(OSError):
                self.process.stdin.close()
        try:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=2)
            if self._owns_process_group:
                if self._signal_owned_process_group(signal.SIGTERM):
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        self.process.wait(timeout=0.2)
                    self._signal_owned_process_group(signal.SIGKILL)
            elif self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=2)
            self._owns_process_group = False
        finally:
            if self.process.stdout:
                self.process.stdout.close()


def _host() -> None:
    def reply(value: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    init = json.loads(sys.stdin.readline())
    root = Path(init["output"])
    runtime = root / "runtime"
    runtime.mkdir()
    replacements = _public_path_replacements(checkout=init["checkout"], evidence_root=root, runtime_root=runtime)
    journal = LifecycleJournal(root / "action-journal.json", root / "raw-mcp", public_path_replacements=replacements)
    with contextlib.redirect_stdout(sys.stderr):
        module, source = load_pinned_teamharness(init["checkout"], init["lock_path"])
        mc_bin, mc_log = _create_mc_process_adapter(runtime)
    with ControlledLocalMatrix(tuple(init["members"])) as matrix, _environment({
        "PATH": os.pathsep.join((str(mc_bin), str(Path(sys.executable).parent), os.environ.get("PATH", ""))),
        "ORGREBASE_MC_PROCESS_LOG": str(mc_log), "ORGREBASE_PUBLIC_PATH_REPLACEMENTS": json.dumps(replacements),
        "AGENTTEAMS_WORKER_MATRIX_TOKEN": "controlled-local-token", "AGENTTEAMS_MATRIX_URL": matrix.url,
        "AGENTTEAMS_SHARED_STORAGE_PREFIX": "agentteams/shared", "MC_HOST_agentteams": "http://controlled:local@127.0.0.1:9000",
        "AGENTTEAMS_AGENT_ROLE": "leader",
    }):
        reply({"ok": True, "source_verification": source})
        for line in sys.stdin:
            message = json.loads(line)
            request = dict(message["request"])
            if message["tool"] not in {"projectflow", "taskflow"} or request.pop("workspaceDir") != _RUNTIME:
                raise ValueError("invalid host request")
            action = request.pop("action")
            request["workspaceDir"] = str(runtime)
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    payload, entry = _invoke(module, journal, key=message["key"], tool=message["tool"],
                                             action=action, arguments=request)
                raw = json.loads((root / entry["raw_ref"]).read_text(encoding="utf-8"))
                reply({"ok": True, "payload": payload, "action": entry, "raw": raw})
            except Exception:
                reply({"ok": False})
                return


if __name__ == "__main__":
    if sys.argv[1:] != ["--host"]:
        raise SystemExit("This module is an internal AgentTeams process host.")
    _host()
