"""Controlled-local bridge to the pinned upstream AgentTeams Task lifecycle.

This module deliberately proves the adapter boundary, not a live enterprise
deployment.  Every lifecycle transition is executed by the upstream
``plugins/teamharness/mcp/server.py::call_tool`` function.  The local Matrix
HTTP server and ``mc`` executable are explicit transport/process adapters used
only to make that native code path reproducible without external credentials.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from orgrebase.agentteams_source import load_teamharness_lock
from orgrebase.resource_paths import PROJECT_ROOT

EVIDENCE_CLASS = "CONTROLLED_LOCAL_NATIVE_TASKFLOW"
EXTERNAL_PROMOTION_STATUS = "NOT_RUN"
DOMAINS = ("product", "legal", "finance", "gtm")
PATH_DISCLOSURE_STATUS = "PUBLIC_PATH_REDACTED"
PUBLIC_CHECKOUT_REF = "agentteams://pinned-checkout"
PUBLIC_EVIDENCE_ROOT_REF = "orgrebase://evidence-root"
PUBLIC_RUNTIME_ROOT_REF = "orgrebase://runtime-workspace"
RAW_MCP_REPRESENTATION = "SEMANTIC_MCP_WRAPPER_PUBLIC_PATH_REDACTED"

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Domain = Literal["product", "legal", "finance", "gtm"]
NativeTool = Literal["projectflow", "taskflow"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NativeStateObservation(_StrictModel):
    sequence: int = Field(ge=1)
    action: str = Field(min_length=1)
    action_receipt_digest: Digest
    native_status: str = Field(min_length=1)
    semantic_ok: bool


class NativeActionReceipt(_StrictModel):
    sequence: int = Field(ge=1)
    key: str = Field(min_length=1)
    tool: NativeTool
    action: str = Field(min_length=1)
    request_digest: Digest
    response_digest: Digest
    payload_digest: Digest
    raw_ref: str = Field(pattern=r"^raw-mcp/[0-9]{3}-[A-Za-z0-9._-]+\.json$")
    ok: bool
    status: str | None
    error: str | None
    digest: Digest


class AgentTeamsTaskBinding(_StrictModel):
    schema_version: Literal["orgrebase.workspace-agentteams-task-binding.v1"]
    evidence_class: Literal["CONTROLLED_LOCAL_NATIVE_TASKFLOW"]
    run_id: str = Field(min_length=1)
    nonce: Hex64
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    domain: Domain
    attempt: int = Field(ge=1)
    plan_revision: int = Field(ge=1)
    predecessor_task_id: str | None
    predecessor_attempt: int | None = Field(default=None, ge=1)
    fencing_token: Digest
    attempt_ref: Digest
    active_attempt_ref: Digest
    assignee: str = Field(pattern=r"^@[^:]+:[^:]+$")
    room_id: str = Field(pattern=r"^![^:]+:[^:]+$")
    assignment_event_id: Annotated[str, StringConstraints(pattern=r"^\$.+")] | None
    candidate_only: Literal[True]
    target_writes: Literal[0]
    source_lock_digest: Digest
    plan_digest: Digest
    delegation_digest: Digest
    spec_digest: Digest
    context_projection_digest: Digest
    expected_result_digest: Digest
    observed_result_digest: Digest | None
    deliverable_digest: Digest
    status: Literal[
        "planned",
        "assigned",
        "in_progress",
        "submitted",
        "completed",
        "cancelled",
        "revision",
        "blocked",
    ]
    observed_state_sequence: list[NativeStateObservation] = Field(min_length=1)
    action_receipt_digests: list[Digest] = Field(min_length=1)
    admission_binding_digest: Digest | None
    control_decision_ref: Digest | None

    @field_validator("action_receipt_digests")
    @classmethod
    def _unique_action_receipts(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("action_receipt_digests must be unique")
        return value


class AgentTeamsControlDecision(_StrictModel):
    schema_version: Literal["orgrebase.workspace-agentteams-control-decision.v1"]
    run_id: str = Field(min_length=1)
    nonce: Hex64
    project_id: str
    task_id: str
    domain: Domain
    attempt: int = Field(ge=1)
    expected_binding_digest: Digest
    observed_binding_digest: Digest
    active_attempt_ref: Digest
    active_fencing_token: Digest
    observed_result_digest: Digest
    verdict: Literal["ADMIT", "REJECT"]
    reason_codes: list[str] = Field(min_length=1)
    candidate_only: Literal[True]
    target_writes: Literal[0]
    decision_digest: Digest


class SourceVerification(_StrictModel):
    checkout: Literal["agentteams://pinned-checkout"]
    origin: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_lock_digest: Digest
    files: dict[str, Digest]
    path_disclosure_status: Literal["PUBLIC_PATH_REDACTED"]
    status: Literal["PASS"]


class PlanRevisionReceipt(_StrictModel):
    revision: int = Field(ge=1)
    plan_digest: Digest
    task_ids: list[str] = Field(min_length=1)
    action_receipt_digest: Digest


class MatrixTransportReceipt(_StrictModel):
    mode: Literal["CONTROLLED_LOCAL_HTTP_STUB"]
    unique_assignment_events: int = Field(ge=1)
    request_count: int = Field(ge=1)


class ProcessStorageReceipt(_StrictModel):
    mode: Literal["CONTROLLED_LOCAL_MC_PROCESS_ADAPTER"]
    process_invocations: int = Field(ge=1)
    invocation_log_ref: Literal["runtime/mc-process-invocations.jsonl"]
    path_disclosure_status: Literal["PUBLIC_PATH_REDACTED"]


class AgentTeamsLifecycleReceipt(_StrictModel):
    schema_version: Literal["orgrebase.workspace-agentteams-lifecycle-receipt.v1"]
    evidence_class: Literal["CONTROLLED_LOCAL_NATIVE_TASKFLOW"]
    claim_boundary: Literal["PINNED_IN_PROCESS_CALL_TOOL_NOT_LIVE_WORKER_HANDOFF"]
    oac_integration_status: Literal["NOT_INTEGRATED"]
    path_disclosure_status: Literal["PUBLIC_PATH_REDACTED"]
    raw_mcp_representation: Literal[
        "SEMANTIC_MCP_WRAPPER_PUBLIC_PATH_REDACTED"
    ]
    run_id: str = Field(min_length=1)
    nonce: Hex64
    project_id: str
    source_verification: SourceVerification
    plan_digest: Digest
    terminal_plan_digest: Digest
    plan_revisions: list[PlanRevisionReceipt] = Field(min_length=2)
    bindings: list[AgentTeamsTaskBinding] = Field(min_length=6)
    actions: list[NativeActionReceipt] = Field(min_length=1)
    control_decisions: list[AgentTeamsControlDecision] = Field(min_length=7)
    matrix_transport: MatrixTransportReceipt
    process_storage_adapter: ProcessStorageReceipt
    project_terminal_state: Literal["completed"]
    canonical_target_writes: Literal[0]
    external_promotion_status: Literal["NOT_RUN"]
    receipt_digest: Digest


class NativeTaskflowError(RuntimeError):
    """Fail-closed native bridge error."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return digest_bytes(_canonical_bytes(value))


def _public_path_replacements(
    *, checkout: str | Path, evidence_root: str | Path, runtime_root: str | Path
) -> dict[str, str]:
    replacements = {
        str(Path(runtime_root).expanduser().resolve()): PUBLIC_RUNTIME_ROOT_REF,
        str(Path(evidence_root).expanduser().resolve()): PUBLIC_EVIDENCE_ROOT_REF,
        str(Path(checkout).expanduser().resolve()): PUBLIC_CHECKOUT_REF,
    }
    return dict(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))


def _redact_public_paths(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for actual, logical in replacements.items():
            redacted = redacted.replace(actual, logical)
        return redacted
    if isinstance(value, Mapping):
        return {
            str(_redact_public_paths(key, replacements)): _redact_public_paths(
                item, replacements
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_public_paths(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_paths(item, replacements) for item in value)
    return value


def _path_disclosure_category(text: str, *, checkout: str | Path | None = None) -> str | None:
    markers = {
        "/" + "Users/": "MACOS_USER_PATH",
        "/private/tmp": "MACOS_PRIVATE_TMP",
        "/tmp/": "UNIX_TMP_PATH",
        "/var/folders/": "MACOS_RANDOM_STAGING_PATH",
        "/home/": "UNIX_HOME_PATH",
        "/root/": "UNIX_ROOT_HOME_PATH",
        "/Volumes/": "MACOS_VOLUME_PATH",
        "/.semifinal-stage-": "SEMIFINAL_RANDOM_STAGING_PATH",
        ".semifinal-stage-": "SEMIFINAL_RANDOM_STAGING_PATH",
    }
    for marker, category in markers.items():
        if marker in text:
            return category
    if checkout is not None:
        resolved_checkout = str(Path(checkout).expanduser().resolve())
        if resolved_checkout and resolved_checkout in text:
            return "CHECKOUT_ABSOLUTE_PATH"
    if re.search(r"[A-Za-z]:[\\/](?:Users|Temp|tmp)[\\/]", text):
        return "WINDOWS_HOST_PATH"
    if re.search(r'(?:^|[:,\[]\s*)"/(?!/)', text):
        return "JSON_ABSOLUTE_PATH_VALUE"
    return None


def _assert_public_value_path_safe(value: Any) -> None:
    category = _path_disclosure_category(
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    if category is not None:
        raise NativeTaskflowError(f"PUBLIC_EVIDENCE_PATH_REDACTION_FAILED:{category}")


def _scan_public_evidence_paths(
    root: Path, *, checkout: str | Path | None = None
) -> None:
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if ".semifinal-stage-" in relative:
            raise NativeTaskflowError(
                "NATIVE_EVIDENCE_PUBLIC_PATH_DISCLOSURE:RELATIVE_STAGING_PATH"
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            payload = path.read_bytes()
            binary_markers = [
                b"/" + b"Users/",
                b"/private/tmp",
                b"/.semifinal-stage-",
            ]
            if checkout is not None:
                binary_markers.append(
                    str(Path(checkout).expanduser().resolve()).encode("utf-8")
                )
            if any(marker and marker in payload for marker in binary_markers):
                raise NativeTaskflowError(
                    f"NATIVE_EVIDENCE_PUBLIC_PATH_DISCLOSURE:{relative}:BINARY"
                ) from None
            continue
        category = _path_disclosure_category(text, checkout=checkout)
        if category is not None:
            raise NativeTaskflowError(
                f"NATIVE_EVIDENCE_PUBLIC_PATH_DISCLOSURE:{relative}:{category}"
            )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise NativeTaskflowError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _payload_status(payload: Mapping[str, Any]) -> str | None:
    node_status = str(payload.get("nodeStatus") or "").strip()
    if node_status:
        return node_status
    task = payload.get("task")
    if isinstance(task, Mapping):
        task_status = str(task.get("status") or "").strip()
        if task_status:
            return task_status
    project = payload.get("project")
    if isinstance(project, Mapping):
        project_status = str(project.get("status") or "").strip()
        if project_status:
            return project_status
    if payload.get("ok") is False:
        return "error"
    return None


def _source_verification_from_lock(lock_path: str | Path) -> dict[str, Any]:
    path = Path(lock_path).expanduser().resolve()
    if path == (PROJECT_ROOT / "agentteams/teamharness-lock.json").resolve():
        try:
            lock = load_teamharness_lock()
        except ValueError as exc:
            raise NativeTaskflowError(str(exc)) from exc
    else:
        lock = _read_json(path)
    files = lock.get("source_files")
    origin = lock.get("upstream")
    commit = lock.get("commit")
    if (
        not isinstance(files, dict)
        or len(files) != 3
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in files.items())
        or not isinstance(origin, str)
        or not isinstance(commit, str)
    ):
        raise NativeTaskflowError("TEAMHARNESS_SOURCE_LOCK_INVALID")
    return SourceVerification.model_validate(
        {
            "checkout": PUBLIC_CHECKOUT_REF,
            "origin": origin,
            "commit": commit,
            "source_lock_digest": digest_json(lock),
            "files": dict(sorted(files.items())),
            "path_disclosure_status": PATH_DISCLOSURE_STATUS,
            "status": "PASS",
        }
    ).model_dump(mode="json")


def verify_teamharness_checkout(
    checkout: str | Path, lock_path: str | Path
) -> dict[str, Any]:
    root = Path(checkout).expanduser().resolve()
    lock_file = Path(lock_path).expanduser().resolve()
    lock = _read_json(lock_file)
    public_verification = _source_verification_from_lock(lock_file)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
        origin = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeTaskflowError("TEAMHARNESS_GIT_CHECK_FAILED") from exc
    if commit != lock.get("commit"):
        raise NativeTaskflowError(
            f"TEAMHARNESS_COMMIT_MISMATCH:{commit}:{lock.get('commit')}"
        )
    normalized_origin = origin.removesuffix(".git").lower()
    normalized_expected_origin = str(lock.get("upstream") or "").removesuffix(".git").lower()
    if normalized_origin != normalized_expected_origin:
        raise NativeTaskflowError(
            f"TEAMHARNESS_ORIGIN_MISMATCH:{origin}:{lock.get('upstream')}"
        )
    expected_files = lock.get("source_files")
    if not isinstance(expected_files, dict) or len(expected_files) != 3:
        raise NativeTaskflowError("TEAMHARNESS_SOURCE_LOCK_INVALID")
    observed: dict[str, str] = {}
    for relative, expected in sorted(expected_files.items()):
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise NativeTaskflowError(f"TEAMHARNESS_SOURCE_MISSING:{relative}")
        observed[relative] = digest_bytes(path.read_bytes())
        if observed[relative] != expected:
            raise NativeTaskflowError(f"TEAMHARNESS_SOURCE_DRIFT:{relative}")
    if observed != public_verification["files"]:
        raise NativeTaskflowError("TEAMHARNESS_SOURCE_OBSERVATION_MISMATCH")
    return public_verification


def load_pinned_teamharness(
    checkout: str | Path, lock_path: str | Path
) -> tuple[ModuleType, dict[str, Any]]:
    verification = verify_teamharness_checkout(checkout, lock_path)
    mcp_dir = Path(checkout).expanduser().resolve() / "plugins/teamharness/mcp"
    server_path = mcp_dir / "server.py"
    module_name = "_orgrebase_pinned_teamharness_" + hashlib.sha256(
        str(server_path).encode("utf-8")
    ).hexdigest()[:12]
    previous = {name: sys.modules.get(name) for name in ("message_tool", "roomflow_tool")}
    for name in previous:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(mcp_dir))
    try:
        spec = importlib.util.spec_from_file_location(module_name, server_path)
        if spec is None or spec.loader is None:
            raise NativeTaskflowError("TEAMHARNESS_MODULE_SPEC_FAILED")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "call_tool", None)):
            raise NativeTaskflowError("TEAMHARNESS_CALL_TOOL_MISSING")
    finally:
        sys.path.remove(str(mcp_dir))
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module, verification


def _unwrap_call_tool(response: dict[str, Any]) -> dict[str, Any]:
    try:
        text = response["content"][0]["text"]
        payload = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise NativeTaskflowError("TEAMHARNESS_MCP_RESPONSE_INVALID") from exc
    if not isinstance(payload, dict):
        raise NativeTaskflowError("TEAMHARNESS_MCP_PAYLOAD_INVALID")
    return payload


class LifecycleJournal:
    """Append-only, digest-bound action journal with idempotent key reuse."""

    def __init__(
        self,
        path: str | Path,
        raw_dir: str | Path | None = None,
        *,
        public_path_replacements: Mapping[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.raw_dir = Path(raw_dir) if raw_dir is not None else self.path.parent / "raw-mcp"
        self.public_path_replacements = dict(public_path_replacements or {})
        if self.path.is_file():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise NativeTaskflowError("JOURNAL_LIST_REQUIRED")
            self.actions = [
                NativeActionReceipt.model_validate(item).model_dump(mode="json")
                for item in raw
            ]
        else:
            self.actions: list[dict[str, Any]] = []

    def record(
        self,
        *,
        key: str,
        tool: str,
        action: str,
        arguments: Mapping[str, Any],
        response: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        public_arguments = _redact_public_paths(
            dict(arguments), self.public_path_replacements
        )
        public_response = _redact_public_paths(
            dict(response), self.public_path_replacements
        )
        semantic_payload = _redact_public_paths(
            dict(payload) if payload is not None else _unwrap_call_tool(dict(response)),
            self.public_path_replacements,
        )
        _assert_public_value_path_safe(public_arguments)
        _assert_public_value_path_safe(public_response)
        _assert_public_value_path_safe(semantic_payload)
        request_digest = digest_json(public_arguments)
        response_digest = digest_json(public_response)
        payload_digest = digest_json(semantic_payload)
        for existing in self.actions:
            if existing["key"] != key:
                continue
            if (
                existing["request_digest"] != request_digest
                or existing["response_digest"] != response_digest
                or existing["payload_digest"] != payload_digest
            ):
                raise NativeTaskflowError(f"JOURNAL_IDEMPOTENCY_CONFLICT:{key}")
            return existing
        sequence = len(self.actions) + 1
        safe_key = "".join(char if char.isalnum() or char in "._-" else "-" for char in key)
        key_suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
        raw_name = f"{sequence:03d}-{safe_key}-{key_suffix}.json"
        try:
            raw_ref = str((self.raw_dir / raw_name).relative_to(self.path.parent))
        except ValueError as exc:
            raise NativeTaskflowError("RAW_MCP_DIR_OUTSIDE_EVIDENCE_ROOT") from exc
        base = {
            "sequence": sequence,
            "key": key,
            "tool": tool,
            "action": action,
            "request_digest": request_digest,
            "response_digest": response_digest,
            "payload_digest": payload_digest,
            "raw_ref": raw_ref,
            "ok": semantic_payload.get("ok") is True,
            "status": _payload_status(semantic_payload),
            "error": (
                str(semantic_payload.get("error"))
                if semantic_payload.get("error") is not None
                else None
            ),
        }
        entry = NativeActionReceipt.model_validate(
            {**base, "digest": digest_json(base)}
        ).model_dump(mode="json")
        _write_json(
            self.raw_dir / raw_name,
            {
                "schema_version": "orgrebase.teamharness-raw-mcp-action.v1",
                "sequence": sequence,
                "key": key,
                "tool": tool,
                "action": action,
                "path_disclosure_status": PATH_DISCLOSURE_STATUS,
                "representation": RAW_MCP_REPRESENTATION,
                "request": public_arguments,
                "response": public_response,
            },
        )
        self.actions.append(entry)
        _write_json(self.path, self.actions)
        return entry


class _MatrixState:
    def __init__(self, members: tuple[str, ...]) -> None:
        self.members = members
        self.events: dict[str, str] = {}
        self.request_count = 0
        self.lock = threading.Lock()


class ControlledLocalMatrix:
    """Small real HTTP endpoint implementing the two Matrix calls used here."""

    def __init__(self, members: tuple[str, ...]) -> None:
        self.state = _MatrixState(members)
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, payload: Any) -> None:
                body = _canonical_bytes(payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                with state.lock:
                    state.request_count += 1
                if self.path.endswith("/members"):
                    self._reply(
                        {
                            "chunk": [
                                {"state_key": member, "content": {"membership": "join"}}
                                for member in state.members
                            ]
                        }
                    )
                    return
                self._reply({})

            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with state.lock:
                    state.request_count += 1
                    event_id = state.events.setdefault(
                        self.path,
                        "$controlled-" + hashlib.sha256(self.path.encode() + body).hexdigest()[:24],
                    )
                self._reply({"event_id": event_id})

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> ControlledLocalMatrix:
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _create_mc_process_adapter(root: Path) -> tuple[Path, Path]:
    bin_dir = root / "adapter-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = root / "mc-process-invocations.jsonl"
    executable = bin_dir / "mc"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "p=os.environ.get('ORGREBASE_MC_PROCESS_LOG')\n"
        "replacements=json.loads(os.environ.get('ORGREBASE_PUBLIC_PATH_REPLACEMENTS','{}'))\n"
        "args=list(sys.argv[1:])\n"
        "for actual, logical in replacements.items():\n"
        "  args=[item.replace(actual, logical) for item in args]\n"
        "if p:\n"
        "  with open(p, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(args, separators=(',', ':'))+'\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, log_path


@contextlib.contextmanager
def _environment(overrides: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _candidate(run_id: str, nonce: str, domain: str, attempt: int) -> bytes:
    return _canonical_bytes(
        {
            "schema_version": "orgrebase.native-domain-candidate.v1",
            "run_id": run_id,
            "nonce": nonce,
            "domain": domain,
            "attempt": attempt,
            "candidate_only": True,
            "target_writes": 0,
            "output": {"recommendation": f"{domain}-quote-input-admitted"},
        }
    )


def admit_candidate(
    binding: Mapping[str, Any],
    observed_result: bytes,
    *,
    observed_binding: Mapping[str, Any],
    active_fencing_token: str,
    active_attempt_ref: str,
) -> dict[str, Any]:
    expected = AgentTeamsTaskBinding.model_validate(dict(binding)).model_dump(mode="json")
    observed_result_digest = digest_bytes(observed_result)
    reasons: list[str] = []
    if observed_result_digest != expected["expected_result_digest"]:
        reasons.append("RESULT_DIGEST_MISMATCH")
    observed_fields = (
        "run_id",
        "nonce",
        "project_id",
        "plan_digest",
        "plan_revision",
        "delegation_digest",
        "task_id",
        "domain",
        "assignee",
        "room_id",
        "context_projection_digest",
        "source_lock_digest",
        "attempt",
        "fencing_token",
        "candidate_only",
        "target_writes",
    )
    for field_name in observed_fields:
        if observed_binding.get(field_name) != expected[field_name]:
            reasons.append(f"BINDING_{field_name.upper()}_MISMATCH")
    if expected["fencing_token"] != active_fencing_token:
        reasons.append("STALE_ATTEMPT_FENCED")
    if expected["active_attempt_ref"] != active_attempt_ref:
        reasons.append("STALE_ATTEMPT_REF")
    verdict = "REJECT" if reasons else "ADMIT"
    base = {
        "schema_version": "orgrebase.workspace-agentteams-control-decision.v1",
        "run_id": expected["run_id"],
        "nonce": expected["nonce"],
        "project_id": expected["project_id"],
        "task_id": expected["task_id"],
        "domain": expected["domain"],
        "attempt": expected["attempt"],
        "expected_binding_digest": digest_json(expected),
        "observed_binding_digest": digest_json(dict(observed_binding)),
        "active_attempt_ref": active_attempt_ref,
        "active_fencing_token": active_fencing_token,
        "observed_result_digest": observed_result_digest,
        "verdict": verdict,
        "reason_codes": reasons or ["EXACT_RESULT_BINDING_AND_ACTIVE_ATTEMPT"],
        "candidate_only": True,
        "target_writes": 0,
    }
    return AgentTeamsControlDecision.model_validate(
        {**base, "decision_digest": digest_json(base)}
    ).model_dump(mode="json")


def _binding(
    *,
    run_id: str,
    nonce: str,
    project_id: str,
    domain: str,
    attempt: int,
    source_lock_digest: str,
    plan_digest: str,
    plan_revision: int,
    task_id: str,
    assignee: str,
    room_id: str,
    expected_result: bytes,
    predecessor_task_id: str | None = None,
    predecessor_attempt: int | None = None,
) -> tuple[dict[str, Any], str]:
    context = {
        "run_id": run_id,
        "nonce": nonce,
        "domain": domain,
        "purpose": "enterprise_quote",
        "included_refs": [f"domain:{domain}:approved-projection"],
        "excluded_raw_cross_domain_sources": True,
    }
    delegation = {
        "run_id": run_id,
        "nonce": nonce,
        "project_id": project_id,
        "task_id": task_id,
        "domain": domain,
        "attempt": attempt,
        "plan_revision": plan_revision,
        "assignee": assignee,
        "room_id": room_id,
        "candidate_only": True,
        "target_writes": 0,
        "plan_digest": plan_digest,
        "context_projection_digest": digest_json(context),
    }
    delegation_digest = digest_json(delegation)
    fencing_token = digest_json(
        {"run_id": run_id, "nonce": nonce, "task_id": task_id, "attempt": attempt}
    )
    attempt_ref = digest_json(
        {
            "run_id": run_id,
            "nonce": nonce,
            "project_id": project_id,
            "task_id": task_id,
            "attempt": attempt,
            "fencing_token": fencing_token,
        }
    )
    spec_payload = {
        "schema_version": "orgrebase.agentteams-native-task-spec.v1",
        **delegation,
        "source_lock_digest": source_lock_digest,
        "delegation_digest": delegation_digest,
        "fencing_token": fencing_token,
        "attempt_ref": attempt_ref,
        "predecessor_task_id": predecessor_task_id,
        "predecessor_attempt": predecessor_attempt,
        "expected_result_digest": digest_bytes(expected_result),
    }
    spec_text = json.dumps(spec_payload, ensure_ascii=False, sort_keys=True, indent=2)
    binding = {
            "schema_version": "orgrebase.workspace-agentteams-task-binding.v1",
            "evidence_class": EVIDENCE_CLASS,
            "run_id": run_id,
            "nonce": nonce,
            "project_id": project_id,
            "task_id": task_id,
            "domain": domain,
            "attempt": attempt,
            "plan_revision": plan_revision,
            "predecessor_task_id": predecessor_task_id,
            "predecessor_attempt": predecessor_attempt,
            "fencing_token": fencing_token,
            "attempt_ref": attempt_ref,
            "active_attempt_ref": attempt_ref,
            "assignee": assignee,
            "room_id": room_id,
            "assignment_event_id": None,
            "candidate_only": True,
            "target_writes": 0,
            "source_lock_digest": source_lock_digest,
            "plan_digest": plan_digest,
            "delegation_digest": delegation_digest,
            "spec_digest": digest_bytes(spec_text.encode("utf-8")),
            "context_projection_digest": digest_json(context),
            "expected_result_digest": digest_bytes(expected_result),
            "observed_result_digest": None,
            "deliverable_digest": digest_json([]),
            "status": "planned",
            "observed_state_sequence": [],
            "action_receipt_digests": [],
            "admission_binding_digest": None,
            "control_decision_ref": None,
        }
    return binding, spec_text


def _observe_binding_state(
    binding: dict[str, Any],
    entry: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    status: str | None = None,
) -> None:
    native_status = status or _payload_status(payload)
    if not native_status:
        raise NativeTaskflowError(f"NATIVE_STATUS_MISSING:{entry.get('key')}")
    binding["action_receipt_digests"].append(entry["digest"])
    binding["observed_state_sequence"].append(
        {
            "sequence": len(binding["observed_state_sequence"]) + 1,
            "action": entry["action"],
            "action_receipt_digest": entry["digest"],
            "native_status": native_status,
            "semantic_ok": payload.get("ok") is True,
        }
    )
    if payload.get("ok") is True and native_status in {
        "planned",
        "assigned",
        "in_progress",
        "submitted",
        "completed",
        "cancelled",
        "revision",
        "blocked",
    }:
        binding["status"] = native_status


def _observed_binding_from_spec_task(
    expected_spec: str, task: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        spec = json.loads(expected_spec)
    except json.JSONDecodeError as exc:  # pragma: no cover - locally generated invariant
        raise NativeTaskflowError("TASK_SPEC_JSON_INVALID") from exc
    return {
        "run_id": spec.get("run_id"),
        "nonce": spec.get("nonce"),
        "project_id": task.get("project_id"),
        "plan_digest": spec.get("plan_digest"),
        "plan_revision": spec.get("plan_revision"),
        "delegation_digest": spec.get("delegation_digest"),
        "task_id": task.get("task_id"),
        "domain": spec.get("domain"),
        "assignee": task.get("assigned_to"),
        "room_id": task.get("room_id"),
        "context_projection_digest": spec.get("context_projection_digest"),
        "source_lock_digest": spec.get("source_lock_digest"),
        "attempt": spec.get("attempt"),
        "fencing_token": spec.get("fencing_token"),
        "candidate_only": spec.get("candidate_only"),
        "target_writes": spec.get("target_writes"),
    }


def _observed_binding_from_ack(
    binding: Mapping[str, Any], expected_spec: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    returned_spec = payload.get("spec")
    if returned_spec != expected_spec + "\n":
        raise NativeTaskflowError(f"ACK_SPEC_ROUNDTRIP_MISMATCH:{binding.get('task_id')}")
    task = payload.get("task")
    if not isinstance(task, Mapping):
        raise NativeTaskflowError("ACK_TASK_OBJECT_REQUIRED")
    return _observed_binding_from_spec_task(expected_spec, task)


def _require_task_state(payload: Mapping[str, Any], expected: str, key: str) -> None:
    _require_ok(payload, key)
    task = payload.get("task")
    if not isinstance(task, Mapping) or task.get("status") != expected:
        raise NativeTaskflowError(f"NATIVE_TASK_STATE_MISMATCH:{key}:{expected}")


def _require_effective_check(payload: Mapping[str, Any], key: str) -> None:
    _require_task_state(payload, "submitted", key)
    if payload.get("effective") is not True or payload.get("validationErrors") != []:
        raise NativeTaskflowError(f"NATIVE_CHECK_NOT_EFFECTIVE:{key}")


def _require_accepted(payload: Mapping[str, Any], key: str) -> None:
    _require_ok(payload, key)
    if payload.get("accepted") is not True or payload.get("nodeStatus") != "completed":
        raise NativeTaskflowError(f"NATIVE_ACCEPT_NOT_COMPLETED:{key}")


def _invoke(
    module: ModuleType,
    journal: LifecycleJournal,
    *,
    key: str,
    tool: str,
    action: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {"action": action, **arguments}
    wrapper = module.call_tool(tool, request)
    payload = _unwrap_call_tool(wrapper)
    entry = journal.record(
        key=key,
        tool=tool,
        action=action,
        arguments=request,
        response=wrapper,
        payload=payload,
    )
    return payload, entry


def _require_ok(payload: Mapping[str, Any], key: str) -> None:
    if payload.get("ok") is not True:
        raise NativeTaskflowError(f"UPSTREAM_ACTION_FAILED:{key}:{payload.get('error')}")


def _read_public_mc_invocations(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    invocations: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NativeTaskflowError("MC_INVOCATION_LOG_INVALID") from exc
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise NativeTaskflowError("MC_INVOCATION_LOG_INVALID")
        _assert_public_value_path_safe(value)
        invocations.append(value)
    return invocations


def run_native_taskflow_slice(
    *,
    checkout: str | Path,
    output_dir: str | Path,
    lock_path: str | Path,
    run_id: str = "run:orgrebase:controlled-native:quote-001",
    nonce: str | None = None,
) -> dict[str, Any]:
    """Run four native Domain tasks plus a native cancel/reassign probe."""

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise NativeTaskflowError("OUTPUT_EVIDENCE_DIR_NOT_EMPTY")
    runtime = out / "runtime"
    runtime.mkdir(parents=True)
    path_replacements = _public_path_replacements(
        checkout=checkout, evidence_root=out, runtime_root=runtime
    )
    nonce = nonce or hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    if len(nonce) != 64 or any(char not in "0123456789abcdef" for char in nonce):
        raise NativeTaskflowError("NONCE_MUST_BE_64_LOWER_HEX")
    module, source_verification = load_pinned_teamharness(checkout, lock_path)
    journal = LifecycleJournal(
        out / "action-journal.json",
        out / "raw-mcp",
        public_path_replacements=path_replacements,
    )
    project_id = "orgrebase-native-quote-001"
    room_id = "!orgrebase-native:controlled.local"
    assignees = {domain: f"@{domain}-steward:controlled.local" for domain in DOMAINS}
    members = tuple(assignees.values())
    task_nodes = [
        {
            "taskId": f"{project_id}-{domain}-a1",
            "title": f"{domain.title()} quote input",
            "assignedTo": assignees[domain],
            "dependsOn": [],
        }
        for domain in DOMAINS
    ]
    probe_a1 = {
        "taskId": f"{project_id}-product-reassign-a1",
        "title": "Product reassignment fencing probe",
        "assignedTo": assignees["product"],
        "dependsOn": [],
    }
    initial_nodes = [*task_nodes, probe_a1]
    plan_digest = digest_json(
        {
            "run_id": run_id,
            "nonce": nonce,
            "project_id": project_id,
            "revision": 1,
            "tasks": initial_nodes,
        }
    )
    bindings: list[dict[str, Any]] = []
    specs: dict[str, str] = {}
    results: dict[str, bytes] = {}
    for domain in DOMAINS:
        task_id = f"{project_id}-{domain}-a1"
        result = _candidate(run_id, nonce, domain, 1)
        binding, spec = _binding(
            run_id=run_id,
            nonce=nonce,
            project_id=project_id,
            domain=domain,
            attempt=1,
            source_lock_digest=source_verification["source_lock_digest"],
            plan_digest=plan_digest,
            plan_revision=1,
            task_id=task_id,
            assignee=assignees[domain],
            room_id=room_id,
            expected_result=result,
        )
        bindings.append(binding)
        specs[task_id] = spec
        results[task_id] = result
    probe_result = _candidate(run_id, nonce, "product", 1)
    probe_binding, probe_spec = _binding(
        run_id=run_id,
        nonce=nonce,
        project_id=project_id,
        domain="product",
        attempt=1,
        source_lock_digest=source_verification["source_lock_digest"],
        plan_digest=plan_digest,
        plan_revision=1,
        task_id=probe_a1["taskId"],
        assignee=assignees["product"],
        room_id=room_id,
        expected_result=probe_result,
    )
    specs[probe_a1["taskId"]] = probe_spec
    results[probe_a1["taskId"]] = probe_result
    bindings.append(probe_binding)

    mc_bin, mc_log = _create_mc_process_adapter(runtime)
    control_decisions: list[dict[str, Any]] = []
    observed_bindings: dict[str, dict[str, Any]] = {}
    plan_revisions: list[dict[str, Any]] = []

    with ControlledLocalMatrix(members) as matrix, _environment(
        {
            "PATH": str(mc_bin) + os.pathsep + os.environ.get("PATH", ""),
            "ORGREBASE_MC_PROCESS_LOG": str(mc_log),
            "ORGREBASE_PUBLIC_PATH_REPLACEMENTS": json.dumps(
                path_replacements, ensure_ascii=False
            ),
            "AGENTTEAMS_MATRIX_URL": matrix.url,
            "AGENTTEAMS_WORKER_MATRIX_TOKEN": "controlled-local-token",
            "AGENTTEAMS_SHARED_STORAGE_PREFIX": "agentteams/shared",
            "MC_HOST_agentteams": "http://controlled:local@127.0.0.1:9000",
            "AGENTTEAMS_AGENT_ROLE": "leader",
        }
    ):
        payload, _ = _invoke(
            module,
            journal,
            key="project:create",
            tool="projectflow",
            action="create_project",
            arguments={
                "workspaceDir": str(runtime),
                "payload": {"projectId": project_id, "title": "OrgRebase controlled quote"},
            },
        )
        _require_ok(payload, "project:create")
        if payload.get("project", {}).get("status") != "active":
            raise NativeTaskflowError("NATIVE_PROJECT_CREATE_STATE_MISMATCH")
        payload, plan_entry = _invoke(
            module,
            journal,
            key="project:plan",
            tool="projectflow",
            action="plan_dag",
            arguments={
                "workspaceDir": str(runtime),
                "payload": {"projectId": project_id, "tasks": initial_nodes},
            },
        )
        _require_ok(payload, "project:plan")
        planned_ids = {
            item.get("task_id") for item in payload.get("project", {}).get("tasks", [])
        }
        if planned_ids != {item["taskId"] for item in initial_nodes}:
            raise NativeTaskflowError("NATIVE_INITIAL_PLAN_MISMATCH")
        plan_revisions.append(
            {
                "revision": 1,
                "plan_digest": plan_digest,
                "task_ids": [item["taskId"] for item in initial_nodes],
                "action_receipt_digest": plan_entry["digest"],
            }
        )
        payload, _ = _invoke(
            module,
            journal,
            key="project:ready",
            tool="projectflow",
            action="ready_nodes",
            arguments={"workspaceDir": str(runtime), "payload": {"projectId": project_id}},
        )
        _require_ok(payload, "project:ready")
        ready_ids = {item.get("task_id") for item in payload.get("readyNodes", [])}
        if ready_ids != {item["taskId"] for item in initial_nodes}:
            raise NativeTaskflowError("NATIVE_READY_NODES_MISMATCH")

        for domain in DOMAINS:
            task_id = f"{project_id}-{domain}-a1"
            binding = next(item for item in bindings if item["task_id"] == task_id)
            common = {"workspaceDir": str(runtime), "payload": {"taskId": task_id}}
            payload, entry = _invoke(
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
                        "assignedTo": assignees[domain],
                        "roomId": room_id,
                        "spec": specs[task_id],
                    },
                },
            )
            _require_task_state(payload, "assigned", f"{task_id}:delegate")
            notification = payload.get("notification")
            if (
                payload.get("synced") is not True
                or not isinstance(notification, Mapping)
                or notification.get("sent") is not True
                or not notification.get("eventId")
            ):
                raise NativeTaskflowError(f"NATIVE_DELEGATION_INCOMPLETE:{task_id}")
            binding["assignment_event_id"] = str(notification["eventId"])
            _observe_binding_state(binding, entry, payload)
            if domain == "product":
                duplicate, duplicate_entry = _invoke(
                    module,
                    journal,
                    key=f"{task_id}:delegate-duplicate",
                    tool="taskflow",
                    action="delegate_task",
                    arguments={
                        "role": "leader",
                        "workspaceDir": str(runtime),
                        "payload": {
                            "projectId": project_id,
                            "taskId": task_id,
                            "assignedTo": assignees[domain],
                            "roomId": room_id,
                            "spec": specs[task_id],
                        },
                    },
                )
                _require_task_state(
                    duplicate, "assigned", f"{task_id}:delegate-duplicate"
                )
                if duplicate.get("notification", {}).get("eventId") != payload.get(
                    "notification", {}
                ).get("eventId"):
                    raise NativeTaskflowError("DUPLICATE_DELEGATION_EVENT_MISMATCH")
                if duplicate.get("notification", {}).get("reused") is not True:
                    raise NativeTaskflowError("DUPLICATE_DELEGATION_NOT_REUSED")
                _observe_binding_state(binding, duplicate_entry, duplicate)
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:ack",
                tool="taskflow",
                action="ack_task",
                arguments={"role": "worker", **common},
            )
            _require_task_state(payload, "in_progress", f"{task_id}:ack")
            if payload.get("pulled") is not True or payload.get("synced") is not True:
                raise NativeTaskflowError(f"NATIVE_ACK_TRANSFER_INCOMPLETE:{task_id}")
            observed_bindings[task_id] = _observed_binding_from_ack(
                binding, specs[task_id], payload
            )
            _observe_binding_state(binding, entry, payload)
            payload, entry = _invoke(
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
                        "summary": results[task_id].decode("utf-8"),
                        "deliverables": [],
                    },
                },
            )
            _require_task_state(payload, "submitted", f"{task_id}:submit")
            if payload.get("synced") is not True:
                raise NativeTaskflowError(f"NATIVE_SUBMIT_SYNC_FAILED:{task_id}")
            _observe_binding_state(binding, entry, payload)
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:check",
                tool="taskflow",
                action="check_task",
                arguments={"role": "leader", **common},
            )
            _require_effective_check(payload, f"{task_id}:check")
            _observe_binding_state(binding, entry, payload)
            observed = str(payload.get("result", {}).get("summary") or "").encode("utf-8")
            binding["observed_result_digest"] = digest_bytes(observed)
            deliverables = payload.get("result", {}).get("deliverables")
            binding["deliverable_digest"] = digest_json(deliverables)
            admission_binding_digest = digest_json(binding)
            exact = admit_candidate(
                binding,
                observed,
                observed_binding=observed_bindings[task_id],
                active_fencing_token=binding["fencing_token"],
                active_attempt_ref=binding["active_attempt_ref"],
            )
            control_decisions.append(exact)
            if exact["verdict"] != "ADMIT":
                raise NativeTaskflowError(f"CONTROL_PLANE_REJECTED_EXACT_RESULT:{task_id}")
            if domain == "legal":
                mutation = admit_candidate(
                    binding,
                    observed + b" ",
                    observed_binding=observed_bindings[task_id],
                    active_fencing_token=binding["fencing_token"],
                    active_attempt_ref=binding["active_attempt_ref"],
                )
                control_decisions.append(mutation)
                if mutation["verdict"] != "REJECT" or mutation["target_writes"] != 0:
                    raise NativeTaskflowError("MUTATION_REJECTION_FAILED")
            binding["admission_binding_digest"] = admission_binding_digest
            if exact["expected_binding_digest"] != admission_binding_digest:
                raise NativeTaskflowError("ADMISSION_BINDING_DIGEST_MISMATCH")
            binding["control_decision_ref"] = exact["decision_digest"]
            payload, entry = _invoke(
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
            _observe_binding_state(binding, entry, payload)

        # Native cancellation/reassignment plus bridge fencing.  The cancelled
        # attempt is intentionally never admitted.
        old_id = probe_a1["taskId"]
        old_binding = next(item for item in bindings if item["task_id"] == old_id)
        payload, entry = _invoke(
            module,
            journal,
            key=f"{old_id}:delegate",
            tool="taskflow",
            action="delegate_task",
            arguments={
                "role": "leader",
                "workspaceDir": str(runtime),
                "payload": {
                    "projectId": project_id,
                    "taskId": old_id,
                    "assignedTo": assignees["product"],
                    "roomId": room_id,
                    "spec": specs[old_id],
                },
            },
        )
        _require_task_state(payload, "assigned", f"{old_id}:delegate")
        old_notification = payload.get("notification")
        if (
            payload.get("synced") is not True
            or not isinstance(old_notification, Mapping)
            or old_notification.get("sent") is not True
            or not old_notification.get("eventId")
        ):
            raise NativeTaskflowError("NATIVE_OLD_ATTEMPT_DELEGATION_INCOMPLETE")
        old_binding["assignment_event_id"] = str(old_notification["eventId"])
        old_task = payload.get("task")
        if not isinstance(old_task, Mapping):
            raise NativeTaskflowError("NATIVE_OLD_ATTEMPT_TASK_MISSING")
        observed_bindings[old_id] = _observed_binding_from_spec_task(
            specs[old_id], old_task
        )
        _observe_binding_state(old_binding, entry, payload)
        replacement_id = f"{project_id}-product-reassign-a2"
        payload, entry = _invoke(
            module,
            journal,
            key=f"{old_id}:cancel",
            tool="taskflow",
            action="cancel_task",
            arguments={
                "role": "leader",
                "workspaceDir": str(runtime),
                "payload": {
                    "taskId": old_id,
                    "reason": "controlled reassignment probe",
                    "replacementTaskId": replacement_id,
                },
            },
        )
        _require_task_state(payload, "cancelled", f"{old_id}:cancel")
        if payload.get("synced") is not True:
            raise NativeTaskflowError("NATIVE_CANCEL_SYNC_FAILED")
        _observe_binding_state(old_binding, entry, payload)

        replacement_node = {
            "taskId": replacement_id,
            "title": "Product reassignment fencing probe attempt 2",
            "assignedTo": assignees["product"],
            "dependsOn": [],
        }
        successor_nodes = [*initial_nodes, replacement_node]
        successor_plan_digest = digest_json(
            {
                "run_id": run_id,
                "nonce": nonce,
                "project_id": project_id,
                "revision": 2,
                "tasks": successor_nodes,
            }
        )
        payload, replan_entry = _invoke(
            module,
            journal,
            key="project:replan-reassignment",
            tool="projectflow",
            action="plan_dag",
            arguments={
                "workspaceDir": str(runtime),
                "payload": {"projectId": project_id, "tasks": successor_nodes},
            },
        )
        _require_ok(payload, "project:replan-reassignment")
        replanned_ids = {
            item.get("task_id") for item in payload.get("project", {}).get("tasks", [])
        }
        if replanned_ids != {item["taskId"] for item in successor_nodes}:
            raise NativeTaskflowError("NATIVE_SUCCESSOR_PLAN_MISMATCH")
        plan_revisions.append(
            {
                "revision": 2,
                "plan_digest": successor_plan_digest,
                "task_ids": [item["taskId"] for item in successor_nodes],
                "action_receipt_digest": replan_entry["digest"],
            }
        )
        replacement_result = _candidate(run_id, nonce, "product", 2)
        replacement_binding, replacement_spec = _binding(
            run_id=run_id,
            nonce=nonce,
            project_id=project_id,
            domain="product",
            attempt=2,
            source_lock_digest=source_verification["source_lock_digest"],
            plan_digest=successor_plan_digest,
            plan_revision=2,
            task_id=replacement_id,
            assignee=assignees["product"],
            room_id=room_id,
            expected_result=replacement_result,
            predecessor_task_id=old_id,
            predecessor_attempt=1,
        )
        bindings.append(replacement_binding)
        old_binding["active_attempt_ref"] = replacement_binding["attempt_ref"]
        stale_binding_digest = digest_json(old_binding)
        stale = admit_candidate(
            old_binding,
            results[old_id],
            observed_binding=observed_bindings[old_id],
            active_fencing_token=replacement_binding["fencing_token"],
            active_attempt_ref=replacement_binding["attempt_ref"],
        )
        if (
            stale["verdict"] != "REJECT"
            or "STALE_ATTEMPT_FENCED" not in stale["reason_codes"]
            or stale["active_fencing_token"] != replacement_binding["fencing_token"]
        ):
            raise NativeTaskflowError("ACTUAL_REPLACEMENT_FENCE_FAILED")
        old_binding["admission_binding_digest"] = stale_binding_digest
        old_binding["control_decision_ref"] = stale["decision_digest"]
        control_decisions.append(stale)
        late_payload, late_entry = _invoke(
            module,
            journal,
            key=f"{old_id}:late-submit",
            tool="taskflow",
            action="submit_task",
            arguments={
                "role": "worker",
                "workspaceDir": str(runtime),
                "payload": {
                    "taskId": old_id,
                    "status": "SUCCESS",
                    "summary": results[old_id].decode("utf-8"),
                    "deliverables": [],
                },
            },
        )
        if late_payload.get("ok") is not False or "terminal task: cancelled" not in str(
            late_payload.get("error")
        ):
            raise NativeTaskflowError("UPSTREAM_LATE_RESULT_NOT_REJECTED")
        _observe_binding_state(old_binding, late_entry, late_payload, status="cancelled")
        for action, role in (
            ("delegate_task", "leader"),
            ("ack_task", "worker"),
            ("submit_task", "worker"),
            ("check_task", "leader"),
        ):
            arguments: dict[str, Any] = {
                "role": role,
                "workspaceDir": str(runtime),
                "payload": {"taskId": replacement_id},
            }
            if action == "delegate_task":
                arguments["payload"].update(
                    {
                        "projectId": project_id,
                        "assignedTo": assignees["product"],
                        "roomId": room_id,
                        "spec": replacement_spec,
                    }
                )
            elif action == "submit_task":
                arguments["payload"].update(
                    {
                        "status": "SUCCESS",
                        "summary": replacement_result.decode("utf-8"),
                        "deliverables": [],
                    }
                )
            payload, entry = _invoke(
                module,
                journal,
                key=f"{replacement_id}:{action}",
                tool="taskflow",
                action=action,
                arguments=arguments,
            )
            if action == "delegate_task":
                _require_task_state(payload, "assigned", f"{replacement_id}:{action}")
                replacement_notification = payload.get("notification")
                if (
                    payload.get("synced") is not True
                    or not isinstance(replacement_notification, Mapping)
                    or replacement_notification.get("sent") is not True
                    or not replacement_notification.get("eventId")
                ):
                    raise NativeTaskflowError("NATIVE_REPLACEMENT_DELEGATION_INCOMPLETE")
                replacement_binding["assignment_event_id"] = str(
                    replacement_notification["eventId"]
                )
                _observe_binding_state(replacement_binding, entry, payload)
            elif action == "ack_task":
                _require_task_state(payload, "in_progress", f"{replacement_id}:{action}")
                if payload.get("pulled") is not True or payload.get("synced") is not True:
                    raise NativeTaskflowError("NATIVE_REPLACEMENT_ACK_INCOMPLETE")
                observed_bindings[replacement_id] = _observed_binding_from_ack(
                    replacement_binding, replacement_spec, payload
                )
                _observe_binding_state(replacement_binding, entry, payload)
            elif action == "submit_task":
                _require_task_state(payload, "submitted", f"{replacement_id}:{action}")
                if payload.get("synced") is not True:
                    raise NativeTaskflowError("NATIVE_REPLACEMENT_SUBMIT_SYNC_FAILED")
                _observe_binding_state(replacement_binding, entry, payload)
            else:
                _require_effective_check(payload, f"{replacement_id}:{action}")
                _observe_binding_state(replacement_binding, entry, payload)
                observed = str(payload.get("result", {}).get("summary") or "").encode("utf-8")
                replacement_binding["observed_result_digest"] = digest_bytes(observed)
                replacement_binding["deliverable_digest"] = digest_json(
                    payload.get("result", {}).get("deliverables")
                )
                replacement_admission_digest = digest_json(replacement_binding)
                decision = admit_candidate(
                    replacement_binding,
                    observed,
                    observed_binding=observed_bindings[replacement_id],
                    active_fencing_token=replacement_binding["fencing_token"],
                    active_attempt_ref=replacement_binding["active_attempt_ref"],
                )
                control_decisions.append(decision)
                if decision["verdict"] != "ADMIT":
                    raise NativeTaskflowError("REPLACEMENT_ADMISSION_REJECTED")
                replacement_binding["admission_binding_digest"] = (
                    replacement_admission_digest
                )
                if decision["expected_binding_digest"] != replacement_admission_digest:
                    raise NativeTaskflowError("REPLACEMENT_BINDING_DIGEST_MISMATCH")
                replacement_binding["control_decision_ref"] = decision[
                    "decision_digest"
                ]
        payload, entry = _invoke(
            module,
            journal,
            key=f"{replacement_id}:accept",
            tool="projectflow",
            action="accept_task_result",
            arguments={
                "workspaceDir": str(runtime),
                "payload": {
                    "projectId": project_id,
                    "taskId": replacement_id,
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                },
            },
        )
        _require_accepted(payload, f"{replacement_id}:accept")
        _observe_binding_state(replacement_binding, entry, payload)
        payload, _ = _invoke(
            module,
            journal,
            key="project:complete",
            tool="projectflow",
            action="complete_project",
            arguments={"workspaceDir": str(runtime), "payload": {"projectId": project_id}},
        )
        _require_ok(payload, "project:complete")
        project_terminal_state = str(payload.get("project", {}).get("status") or "")
        terminal_tasks = {
            item.get("task_id"): item.get("status")
            for item in payload.get("project", {}).get("tasks", [])
        }
        expected_terminal_tasks = {
            **{f"{project_id}-{domain}-a1": "completed" for domain in DOMAINS},
            old_id: "cancelled",
            replacement_id: "completed",
        }
        if terminal_tasks != expected_terminal_tasks:
            raise NativeTaskflowError("NATIVE_PROJECT_TERMINAL_TASKS_MISMATCH")
        matrix_summary = {
            "mode": "CONTROLLED_LOCAL_HTTP_STUB",
            "unique_assignment_events": len(matrix.state.events),
            "request_count": matrix.state.request_count,
        }

    mc_invocations = _read_public_mc_invocations(mc_log)
    bindings = [
        AgentTeamsTaskBinding.model_validate(binding).model_dump(mode="json")
        for binding in bindings
    ]
    for binding in bindings:
        _write_json(out / "bindings" / f"{binding['task_id']}.json", binding)
    for entry in journal.actions:
        _write_json(out / "actions" / f"{entry['sequence']:03d}-{entry['key'].replace(':', '-')}.json", entry)
    receipt_base = {
        "schema_version": "orgrebase.workspace-agentteams-lifecycle-receipt.v1",
        "evidence_class": EVIDENCE_CLASS,
        "claim_boundary": "PINNED_IN_PROCESS_CALL_TOOL_NOT_LIVE_WORKER_HANDOFF",
        "oac_integration_status": "NOT_INTEGRATED",
        "path_disclosure_status": PATH_DISCLOSURE_STATUS,
        "raw_mcp_representation": RAW_MCP_REPRESENTATION,
        "run_id": run_id,
        "nonce": nonce,
        "project_id": project_id,
        "source_verification": source_verification,
        "plan_digest": plan_digest,
        "terminal_plan_digest": successor_plan_digest,
        "plan_revisions": plan_revisions,
        "bindings": bindings,
        "actions": journal.actions,
        "control_decisions": control_decisions,
        "matrix_transport": matrix_summary,
        "process_storage_adapter": {
            "mode": "CONTROLLED_LOCAL_MC_PROCESS_ADAPTER",
            "process_invocations": len(mc_invocations),
            "invocation_log_ref": "runtime/mc-process-invocations.jsonl",
            "path_disclosure_status": PATH_DISCLOSURE_STATUS,
        },
        "project_terminal_state": project_terminal_state,
        "canonical_target_writes": 0,
        "external_promotion_status": EXTERNAL_PROMOTION_STATUS,
    }
    receipt = AgentTeamsLifecycleReceipt.model_validate(
        {**receipt_base, "receipt_digest": digest_json(receipt_base)}
    ).model_dump(mode="json")
    _write_json(out / "source-verification.json", source_verification)
    _write_json(out / "lifecycle-receipt.json", receipt)
    _scan_public_evidence_paths(out, checkout=checkout)
    return receipt


def _action_semantic_failures(
    action: Mapping[str, Any], raw: Mapping[str, Any], payload: Mapping[str, Any]
) -> list[str]:
    failures: list[str] = []
    key = str(action.get("key") or "")
    tool = str(action.get("tool") or "")
    native_action = str(action.get("action") or "")
    request = raw.get("request")
    response = raw.get("response")
    expected_error = (
        str(payload.get("error")) if payload.get("error") is not None else None
    )
    if (
        raw.get("schema_version") != "orgrebase.teamharness-raw-mcp-action.v1"
        or raw.get("path_disclosure_status") != PATH_DISCLOSURE_STATUS
        or raw.get("representation") != RAW_MCP_REPRESENTATION
        or raw.get("tool") != tool
        or raw.get("action") != native_action
        or not isinstance(request, Mapping)
        or request.get("action") != native_action
        or not isinstance(response, Mapping)
    ):
        failures.append("RAW_TOOL_ACTION")
    if payload.get("tool") != tool or payload.get("action") != native_action:
        failures.append("PAYLOAD_TOOL_ACTION")
    if (
        action.get("ok") is not (payload.get("ok") is True)
        or action.get("status") != _payload_status(payload)
        or action.get("error") != expected_error
        or action.get("payload_digest") != digest_json(dict(payload))
    ):
        failures.append("ACTION_SEMANTIC_SUMMARY")
    is_late_submit = key.endswith(":late-submit")
    if is_late_submit:
        if payload.get("ok") is not False or "terminal task: cancelled" not in str(
            payload.get("error")
        ):
            failures.append("LATE_SUBMIT_NOT_REJECTED")
        return failures
    if payload.get("ok") is not True:
        failures.append("POSITIVE_ACTION_NOT_OK")
        return failures
    if native_action == "create_project":
        if payload.get("project", {}).get("status") != "active":
            failures.append("CREATE_PROJECT_STATE")
    elif native_action == "delegate_task":
        notification = payload.get("notification")
        if (
            payload.get("task", {}).get("status") != "assigned"
            or payload.get("synced") is not True
            or not isinstance(notification, Mapping)
            or notification.get("sent") is not True
            or not notification.get("eventId")
        ):
            failures.append("DELEGATE_STATE")
        if key.endswith(":delegate-duplicate") and (
            not isinstance(notification, Mapping)
            or notification.get("reused") is not True
        ):
            failures.append("DUPLICATE_NOT_REUSED")
    elif native_action == "ack_task":
        if (
            payload.get("task", {}).get("status") != "in_progress"
            or payload.get("pulled") is not True
            or payload.get("synced") is not True
            or not isinstance(payload.get("spec"), str)
        ):
            failures.append("ACK_STATE")
    elif native_action == "submit_task":
        if (
            payload.get("task", {}).get("status") != "submitted"
            or payload.get("synced") is not True
        ):
            failures.append("SUBMIT_STATE")
    elif native_action == "check_task":
        if (
            payload.get("task", {}).get("status") != "submitted"
            or payload.get("effective") is not True
            or payload.get("validationErrors") != []
        ):
            failures.append("CHECK_STATE")
    elif native_action == "accept_task_result":
        if payload.get("accepted") is not True or payload.get("nodeStatus") != "completed":
            failures.append("ACCEPT_STATE")
    elif native_action == "cancel_task":
        if (
            payload.get("task", {}).get("status") != "cancelled"
            or payload.get("synced") is not True
        ):
            failures.append("CANCEL_STATE")
    elif (
        native_action == "complete_project"
        and payload.get("project", {}).get("status") != "completed"
    ):
        failures.append("COMPLETE_PROJECT_STATE")
    return failures


def verify_native_taskflow_evidence(
    *,
    evidence_dir: str | Path,
    lock_path: str | Path,
    checkout: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(evidence_dir).expanduser().resolve()
    _scan_public_evidence_paths(root, checkout=checkout)
    raw_receipt = _read_json(root / "lifecycle-receipt.json")
    try:
        receipt = AgentTeamsLifecycleReceipt.model_validate(raw_receipt).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise NativeTaskflowError("NATIVE_RECEIPT_CONTRACT_INVALID") from exc
    failures: list[str] = []
    base = dict(receipt)
    supplied_receipt_digest = base.pop("receipt_digest", None)
    if supplied_receipt_digest != digest_json(base):
        failures.append("RECEIPT_DIGEST")
    if checkout is None:
        current_source = _source_verification_from_lock(lock_path)
        verification_strength = "RETAINED_LOCK_REPLAY"
    else:
        current_source = verify_teamharness_checkout(checkout, lock_path)
        verification_strength = "PINNED_CHECKOUT_REPLAY"
    if current_source != receipt.get("source_verification"):
        failures.append("SOURCE_VERIFICATION")
    try:
        standalone_source = _read_json(root / "source-verification.json")
    except (OSError, json.JSONDecodeError, NativeTaskflowError):
        failures.append("SOURCE_VERIFICATION_ARTIFACT")
    else:
        if standalone_source != receipt.get("source_verification"):
            failures.append("SOURCE_VERIFICATION_ARTIFACT")
    actions = receipt["actions"]
    action_digests: set[str] = set()
    action_evidence: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    required = {
        ("projectflow", "create_project"),
        ("projectflow", "plan_dag"),
        ("projectflow", "accept_task_result"),
        ("projectflow", "complete_project"),
        ("taskflow", "delegate_task"),
        ("taskflow", "ack_task"),
        ("taskflow", "submit_task"),
        ("taskflow", "check_task"),
        ("taskflow", "cancel_task"),
    }
    seen: set[tuple[str, str]] = set()
    for index, action in enumerate(actions, start=1):
        action_base = dict(action)
        supplied = action_base.pop("digest", None)
        if action.get("sequence") != index or supplied != digest_json(action_base):
            failures.append(f"ACTION:{index}")
        if isinstance(supplied, str):
            action_digests.add(supplied)
        raw_ref = str(action.get("raw_ref") or "")
        raw_path = (root / raw_ref).resolve()
        if root not in raw_path.parents or not raw_path.is_file():
            failures.append(f"RAW_MCP_MISSING:{index}")
            continue
        try:
            raw = _read_json(raw_path)
            response = raw.get("response")
            if not isinstance(response, dict):
                raise NativeTaskflowError("RAW_MCP_RESPONSE_OBJECT_REQUIRED")
            payload = _unwrap_call_tool(response)
        except (OSError, json.JSONDecodeError, NativeTaskflowError):
            failures.append(f"RAW_MCP_INVALID:{index}")
            continue
        if (
            raw.get("sequence") != index
            or raw.get("key") != action.get("key")
            or digest_json(raw.get("request")) != action.get("request_digest")
            or digest_json(raw.get("response")) != action.get("response_digest")
        ):
            failures.append(f"RAW_MCP_BINDING:{index}")
        for semantic_failure in _action_semantic_failures(action, raw, payload):
            failures.append(f"{semantic_failure}:{index}")
        if isinstance(supplied, str):
            action_evidence[supplied] = (action, raw, payload)
        seen.add((str(action.get("tool")), str(action.get("action"))))
    if not required.issubset(seen):
        failures.append("REQUIRED_NATIVE_ACTIONS")

    revision_by_number = {item["revision"]: item for item in receipt["plan_revisions"]}
    if set(revision_by_number) != {1, 2}:
        failures.append("PLAN_REVISIONS")
    for revision, plan_receipt in sorted(revision_by_number.items()):
        evidence = action_evidence.get(plan_receipt["action_receipt_digest"])
        if evidence is None:
            failures.append(f"PLAN_ACTION_MISSING:{revision}")
            continue
        action, raw, _payload = evidence
        request = raw.get("request") if isinstance(raw.get("request"), dict) else {}
        request_payload = (
            request.get("payload") if isinstance(request.get("payload"), dict) else {}
        )
        tasks = request_payload.get("tasks")
        if not isinstance(tasks, list):
            failures.append(f"PLAN_TASKS_INVALID:{revision}")
            continue
        expected_plan_digest = digest_json(
            {
                "run_id": receipt["run_id"],
                "nonce": receipt["nonce"],
                "project_id": receipt["project_id"],
                "revision": revision,
                "tasks": tasks,
            }
        )
        if (
            action.get("action") != "plan_dag"
            or request_payload.get("projectId") != receipt["project_id"]
            or plan_receipt["plan_digest"] != expected_plan_digest
            or plan_receipt["task_ids"]
            != [item.get("taskId") for item in tasks if isinstance(item, dict)]
        ):
            failures.append(f"PLAN_BINDING:{revision}")
    if revision_by_number.get(1, {}).get("plan_digest") != receipt["plan_digest"]:
        failures.append("INITIAL_PLAN_DIGEST")
    if revision_by_number.get(2, {}).get("plan_digest") != receipt["terminal_plan_digest"]:
        failures.append("TERMINAL_PLAN_DIGEST")

    decisions = receipt["control_decisions"]
    decisions_by_digest: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        decision_base = dict(decision)
        supplied = decision_base.pop("decision_digest")
        if supplied != digest_json(decision_base):
            failures.append(f"CONTROL_DECISION_DIGEST:{decision['task_id']}")
        decisions_by_digest[supplied] = decision

    bindings = receipt["bindings"]
    binding_ids = [item["task_id"] for item in bindings]
    if len(binding_ids) != len(set(binding_ids)):
        failures.append("DUPLICATE_BINDING_TASK_ID")
    observed_binding_digests: dict[str, str] = {}
    completed_domains = {
        item["domain"]
        for item in bindings
        if item["attempt"] == 1
        and item["status"] == "completed"
        and item["observed_result_digest"] == item["expected_result_digest"]
    }
    if completed_domains != set(DOMAINS):
        failures.append("FOUR_DOMAIN_RESULTS")
    for binding in bindings:
        task_id = binding["task_id"]
        expected_plan = revision_by_number.get(binding["plan_revision"], {}).get(
            "plan_digest"
        )
        if (
            binding["run_id"] != receipt["run_id"]
            or binding["nonce"] != receipt["nonce"]
            or binding["project_id"] != receipt["project_id"]
            or binding["source_lock_digest"]
            != receipt["source_verification"]["source_lock_digest"]
            or binding["plan_digest"] != expected_plan
            or not set(binding["action_receipt_digests"]).issubset(action_digests)
        ):
            failures.append(f"BINDING_ROOT:{task_id}")
        linked = [
            action_evidence[digest]
            for digest in binding["action_receipt_digests"]
            if digest in action_evidence
        ]
        delegates = [item for item in linked if item[0]["action"] == "delegate_task"]
        if not delegates:
            failures.append(f"BINDING_DELEGATE_MISSING:{task_id}")
            continue
        _delegate_action, delegate_raw, delegate_payload = delegates[0]
        delegate_request = delegate_raw.get("request", {})
        delegate_request_payload = delegate_request.get("payload", {})
        spec_text = delegate_request_payload.get("spec")
        if (
            delegate_request_payload.get("taskId") != task_id
            or delegate_request_payload.get("projectId") != binding["project_id"]
            or delegate_request_payload.get("assignedTo") != binding["assignee"]
            or delegate_request_payload.get("roomId") != binding["room_id"]
            or not isinstance(spec_text, str)
            or digest_bytes(str(spec_text).encode("utf-8")) != binding["spec_digest"]
        ):
            failures.append(f"DELEGATE_BINDING:{task_id}")
            continue
        delegate_task = delegate_payload.get("task")
        if not isinstance(delegate_task, Mapping):
            failures.append(f"DELEGATE_TASK_OBJECT:{task_id}")
            continue
        observed_binding = _observed_binding_from_spec_task(spec_text, delegate_task)
        observed_binding_digests[task_id] = digest_json(observed_binding)
        event_id = delegate_payload.get("notification", {}).get("eventId")
        if binding["assignment_event_id"] != event_id:
            failures.append(f"ASSIGNMENT_EVENT_BINDING:{task_id}")
        observations = binding["observed_state_sequence"]
        if [item["sequence"] for item in observations] != list(
            range(1, len(observations) + 1)
        ):
            failures.append(f"OBSERVATION_SEQUENCE:{task_id}")
        for observation in observations:
            evidence = action_evidence.get(observation["action_receipt_digest"])
            if evidence is None:
                failures.append(f"OBSERVATION_ACTION_MISSING:{task_id}")
                continue
            action, _raw, payload = evidence
            allowed_late_override = action["key"].endswith(":late-submit") and (
                observation["native_status"] == "cancelled"
                and observation["semantic_ok"] is False
            )
            if (
                observation["action"] != action["action"]
                or observation["semantic_ok"] != action["ok"]
                or (
                    observation["native_status"] != _payload_status(payload)
                    and not allowed_late_override
                )
            ):
                failures.append(f"OBSERVATION_SEMANTICS:{task_id}")
        decision = decisions_by_digest.get(binding["control_decision_ref"] or "")
        if (
            decision is None
            or decision["task_id"] != task_id
            or decision["expected_binding_digest"]
            != binding["admission_binding_digest"]
            or decision["observed_binding_digest"]
            != observed_binding_digests.get(task_id)
        ):
            failures.append(f"CONTROL_DECISION_BINDING:{task_id}")
        if binding["status"] == "completed":
            acks = [item for item in linked if item[0]["action"] == "ack_task"]
            checks = [item for item in linked if item[0]["action"] == "check_task"]
            accepts = [
                item for item in linked if item[0]["action"] == "accept_task_result"
            ]
            if len(acks) != 1 or len(checks) != 1 or len(accepts) != 1:
                failures.append(f"COMPLETED_LIFECYCLE:{task_id}")
                continue
            if acks[0][2].get("spec") != spec_text + "\n":
                failures.append(f"ACK_SPEC_ROUNDTRIP:{task_id}")
            result = checks[0][2].get("result", {})
            summary = str(result.get("summary") or "").encode("utf-8")
            if (
                digest_bytes(summary) != binding["observed_result_digest"]
                or binding["observed_result_digest"] != binding["expected_result_digest"]
                or digest_json(result.get("deliverables"))
                != binding["deliverable_digest"]
                or decision is None
                or decision["verdict"] != "ADMIT"
            ):
                failures.append(f"COMPLETED_RESULT:{task_id}")
        elif binding["status"] == "cancelled":
            cancels = [item for item in linked if item[0]["action"] == "cancel_task"]
            late = [item for item in linked if item[0]["key"].endswith(":late-submit")]
            if len(cancels) != 1 or len(late) != 1 or late[0][2].get("ok") is not False:
                failures.append(f"CANCELLED_LIFECYCLE:{task_id}")
        else:
            failures.append(f"NON_TERMINAL_BINDING:{task_id}")

    product_id = f"{receipt['project_id']}-product-a1"
    product_binding = next(
        (item for item in bindings if item["task_id"] == product_id), None
    )
    if product_binding is None:
        failures.append("PRODUCT_BINDING_MISSING")
    else:
        product_delegates = [
            action_evidence[digest]
            for digest in product_binding["action_receipt_digests"]
            if digest in action_evidence
            and action_evidence[digest][0]["action"] == "delegate_task"
        ]
        event_ids = {
            item[2].get("notification", {}).get("eventId") for item in product_delegates
        }
        if (
            len(product_delegates) != 2
            or len(event_ids) != 1
            or product_delegates[1][2].get("notification", {}).get("reused") is not True
        ):
            failures.append("DUPLICATE_DELEGATION")

    old_binding = next(
        (item for item in bindings if item["status"] == "cancelled"), None
    )
    replacement_binding = next(
        (item for item in bindings if item.get("predecessor_task_id")), None
    )
    if old_binding is None or replacement_binding is None:
        failures.append("REASSIGNMENT_BINDINGS")
    else:
        stale_decision = decisions_by_digest.get(old_binding["control_decision_ref"] or "")
        if (
            replacement_binding["predecessor_task_id"] != old_binding["task_id"]
            or replacement_binding["predecessor_attempt"] != old_binding["attempt"]
            or replacement_binding["plan_revision"] != 2
            or replacement_binding["plan_digest"] != receipt["terminal_plan_digest"]
            or old_binding["active_attempt_ref"] != replacement_binding["attempt_ref"]
            or stale_decision is None
            or stale_decision["active_fencing_token"]
            != replacement_binding["fencing_token"]
            or stale_decision["active_attempt_ref"] != replacement_binding["attempt_ref"]
            or "STALE_ATTEMPT_FENCED" not in stale_decision["reason_codes"]
        ):
            failures.append("REASSIGNMENT_FENCE")

    if not any(
        item.get("verdict") == "REJECT"
        and "RESULT_DIGEST_MISMATCH" in item.get("reason_codes", [])
        and item.get("target_writes") == 0
        for item in decisions
    ):
        failures.append("MUTATION_REJECT")
    if not any(
        item.get("verdict") == "REJECT"
        and "STALE_ATTEMPT_FENCED" in item.get("reason_codes", [])
        for item in decisions
    ):
        failures.append("LATE_ATTEMPT_REJECT")
    complete_evidence = [
        item for item in action_evidence.values() if item[0]["action"] == "complete_project"
    ]
    if len(complete_evidence) != 1:
        failures.append("PROJECT_TERMINAL_ACTION")
    else:
        terminal_tasks = {
            item.get("task_id"): item.get("status")
            for item in complete_evidence[0][2].get("project", {}).get("tasks", [])
        }
        expected_terminal = {
            item["task_id"]: item["status"] for item in receipt["bindings"]
        }
        if terminal_tasks != expected_terminal:
            failures.append("PROJECT_TERMINAL_TASKS")
    assignment_events = {
        binding["assignment_event_id"]
        for binding in bindings
        if binding["assignment_event_id"] is not None
    }
    if receipt["matrix_transport"]["unique_assignment_events"] != len(
        assignment_events
    ):
        failures.append("MATRIX_ASSIGNMENT_EVENT_COUNT")
    process_storage = receipt["process_storage_adapter"]
    mc_ref = str(process_storage["invocation_log_ref"])
    mc_path = (root / mc_ref).resolve()
    if root not in mc_path.parents:
        failures.append("MC_INVOCATION_LOG_REF")
    else:
        try:
            mc_invocations = _read_public_mc_invocations(mc_path)
        except (OSError, UnicodeDecodeError, NativeTaskflowError):
            failures.append("MC_INVOCATION_LOG_INVALID")
        else:
            if len(mc_invocations) != process_storage["process_invocations"]:
                failures.append("MC_INVOCATION_COUNT")
    if failures:
        raise NativeTaskflowError("NATIVE_EVIDENCE_VERIFY_FAILED:" + ",".join(failures))
    return {
        "status": "PASS",
        "evidence_class": EVIDENCE_CLASS,
        "claim_boundary": receipt["claim_boundary"],
        "oac_integration_status": receipt["oac_integration_status"],
        "path_disclosure_status": receipt["path_disclosure_status"],
        "verification_strength": verification_strength,
        "run_id": receipt["run_id"],
        "receipt_digest": receipt["receipt_digest"],
        "actions": len(actions),
        "bindings": len(bindings),
        "completed_domains": sorted(completed_domains),
        "external_promotion_status": EXTERNAL_PROMOTION_STATUS,
    }
