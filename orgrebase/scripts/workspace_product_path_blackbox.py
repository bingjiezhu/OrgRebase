"""Run a versioned ProductPath benchmark over real HTTP processes.

The runner never imports the OrgRebase product package.  It builds and unpacks
the wheel, launches Uvicorn from those unpacked bytes in a subprocess, and uses
only public HTTP endpoints plus a separate evaluator process.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmark" / "product-path-v0.3-task-intake-bound"
EVALUATOR_PATH = ROOT / "scripts" / "workspace_product_path_evaluator.py"
DEFAULT_OUTPUT = ROOT / "evidence" / "workspace" / "latest" / "product-path-blackbox.json"

TASK_INTAKE_PROMPT = "请为 Acme 准备企业版报价, 并按已批准的产品、法务和财务流程处理。"
TASK_INTAKE_ACTOR = "employee:sales-owner"
TASK_INTAKE_CUSTOMER = "customer:acme"
TASK_INTAKE_DELIVERABLE = "QUOTE"


def _protocol_revision(benchmark_version: str) -> int:
    return {
        "ProductPath-v0.1": 1,
        "ProductPath-v0.2-source-bound": 2,
        "ProductPath-v0.3-task-intake-bound": 2,
    }[benchmark_version]


class CaseExecutionError(RuntimeError):
    pass


def _rfc8785_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _wheel_inventory(path: Path) -> list[dict[str, Any]]:
    """Freeze every wheel member so the retained bytes are independently inspectable."""

    with zipfile.ZipFile(path) as archive:
        return [
            {
                "path": item.filename,
                "size_bytes": item.file_size,
                "sha256": f"sha256:{hashlib.sha256(archive.read(item)).hexdigest()}",
            }
            for item in sorted(archive.infolist(), key=lambda value: value.filename)
            if not item.is_dir()
        ]


def _product_import_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            count += sum(alias.name == "orgrebase" or alias.name.startswith("orgrebase.") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            count += module == "orgrebase" or module.startswith("orgrebase.")
    return count


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    value = detail.get("code") or detail.get("message") if isinstance(detail, dict) else detail
    if not isinstance(value, str):
        return None
    return value.split(":", 1)[0]


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    value = detail.get("message", "") if isinstance(detail, dict) else detail or ""
    return str(value)


def _error_reason_code(payload: Any) -> str | None:
    message = _error_message(payload)
    return message.split(":", 1)[0] if message else None


@dataclass(frozen=True)
class HttpResult:
    status: int
    payload: Any
    headers: dict[str, str]


@dataclass
class WheelServer:
    wheel_root: Path
    case_dir: Path
    database_path: Path
    process: subprocess.Popen[str] | None = None
    base_url: str = ""
    process_ids: list[int] = field(default_factory=list)
    _log_handle: Any = None

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("ORGREBASE_ENTERPRISE_PACK", None)
        environment.update(
            {
                "PYTHONPATH": str(self.wheel_root),
                "PYTHONNOUSERSITE": "1",
                "ORGREBASE_DEPLOYMENT_MODE": "local",
                "ORGREBASE_WORKSPACE_DB": str(self.database_path),
                "ORGREBASE_WORKSPACE_REVIEW_SECONDS": "4",
                "ORGREBASE_WORKSPACE_IDENTITY_MODE": "CONTROLLED_LOCAL_HEADER_IDENTITY",
                "ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED": "1",
                "ORGREBASE_OAC_ADAPTATION_MODE": "optional",
            }
        )
        return environment

    def start(self) -> None:
        if self.process is not None:
            raise CaseExecutionError("server is already running")
        port = _free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        log_path = self.case_dir / f"uvicorn-{len(self.process_ids) + 1}.log"
        self._log_handle = log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "orgrebase.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=self.case_dir,
            env=self._environment(),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.process_ids.append(self.process.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._close_log()
                raise CaseExecutionError(
                    f"uvicorn exited during startup with {self.process.returncode}: "
                    f"{log_path.read_text(encoding='utf-8')[-2000:]}"
                )
            try:
                result = self.request("GET", "/api/health", timeout=1)
            except (OSError, TimeoutError):
                time.sleep(0.05)
                continue
            if result.status == 200 and result.payload.get("status") == "ok":
                return
            time.sleep(0.05)
        self.stop()
        raise CaseExecutionError("uvicorn did not become healthy within 30 seconds")

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.process = None
        self._close_log()

    def restart(self) -> None:
        self.stop()
        self.start()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResult:
        body = None
        headers = {"Accept": "application/json", "Connection": "close"}
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                encoded = response.read()
                value = json.loads(encoded) if encoded else None
                return HttpResult(
                    status=response.status,
                    payload=value,
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            encoded = exc.read()
            try:
                value = json.loads(encoded) if encoded else None
            except json.JSONDecodeError:
                value = encoded.decode("utf-8", errors="replace")
            return HttpResult(
                status=exc.code,
                payload=value,
                headers={key.lower(): value for key, value in exc.headers.items()},
            )


def _require_ok(result: HttpResult, operation: str) -> dict[str, Any]:
    if result.status != 200 or not isinstance(result.payload, dict):
        raise CaseExecutionError(
            f"{operation} expected HTTP 200, observed {result.status}: {result.payload!r}"
        )
    return result.payload


def _state(server: WheelServer) -> dict[str, Any]:
    return _require_ok(server.request("GET", "/api/workspace/state"), "workspace state")


def _actor_headers(actor_id: str) -> dict[str, str]:
    return {"X-OrgRebase-Actor": actor_id}


def _task_intake_candidate(
    server: WheelServer,
    *,
    customer_id: str = TASK_INTAKE_CUSTOMER,
    prompt: str = TASK_INTAKE_PROMPT,
) -> HttpResult:
    return server.request(
        "POST",
        "/api/workspace/task-intake/prepare",
        {
            "prompt": prompt,
            "actor_id": TASK_INTAKE_ACTOR,
            "customer_id": customer_id,
            "deliverable_kind": TASK_INTAKE_DELIVERABLE,
        },
        extra_headers=_actor_headers(TASK_INTAKE_ACTOR),
    )


def _run_admitted_task_intake(server: WheelServer) -> dict[str, Any]:
    candidate = _require_ok(_task_intake_candidate(server), "prepare task intake")
    approval = _require_ok(
        server.request(
            "POST",
            "/api/workspace/task-intake/admit",
            {
                "actor_id": TASK_INTAKE_ACTOR,
                "candidate_receipt": candidate,
                "candidate_digest": candidate["digest"],
            },
            extra_headers=_actor_headers(TASK_INTAKE_ACTOR),
        ),
        "admit task intake",
    )
    return _require_ok(
        server.request(
            "POST",
            "/api/workspace/task-intake/run",
            {
                "actor_id": TASK_INTAKE_ACTOR,
                "candidate_receipt": candidate,
                "candidate_digest": candidate["digest"],
                "approval_receipt": approval,
                "approval_digest": approval["digest"],
                "work_description": TASK_INTAKE_PROMPT,
            },
            extra_headers=_actor_headers(TASK_INTAKE_ACTOR),
        ),
        "run admitted task intake",
    )


def _preview(server: WheelServer, kind: str) -> dict[str, Any]:
    return _require_ok(
        server.request("POST", f"/api/workspace/preview/{kind}"),
        f"preview {kind}",
    )


def _approve(
    server: WheelServer,
    kind: str,
    *,
    actor_id: str,
    preview_digest: str,
) -> dict[str, Any]:
    first = server.request(
        "POST",
        f"/api/workspace/approve/{kind}",
        {"actor_id": actor_id, "preview_digest": preview_digest},
        extra_headers=_actor_headers(actor_id),
    )
    if first.status == 409 and _error_code(first.payload) == "WORKSPACE_REVIEW_GATE_NOT_READY":
        detail = first.payload.get("detail", {}) if isinstance(first.payload, dict) else {}
        remaining_ms = detail.get("remaining_ms") if isinstance(detail, dict) else None
        if not isinstance(remaining_ms, int) or remaining_ms < 0:
            raise CaseExecutionError(f"approve {kind} returned an invalid review gate: {first.payload!r}")
        time.sleep((remaining_ms / 1000) + 0.05)
        first = server.request(
            "POST",
            f"/api/workspace/approve/{kind}",
            {"actor_id": actor_id, "preview_digest": preview_digest},
            extra_headers=_actor_headers(actor_id),
        )
    return _require_ok(first, f"approve {kind}")


def _apply(server: WheelServer, kind: str, *, approval_digest: str) -> dict[str, Any]:
    return _require_ok(
        server.request(
            "POST",
            f"/api/workspace/apply/{kind}",
            {"approval_digest": approval_digest},
        ),
        f"apply {kind}",
    )


def _advance_launch(server: WheelServer) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preview = _preview(server, "launch_date")
    approval = _approve(
        server,
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    outcome = _apply(
        server,
        "launch_date",
        approval_digest=approval["approval_digest"],
    )
    return preview, approval, outcome


def _advance_currency(
    server: WheelServer,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preview = _preview(server, "currency")
    approval = _approve(
        server,
        "currency",
        actor_id="human:finance-owner",
        preview_digest=preview["preview_digest"],
    )
    outcome = _apply(
        server,
        "currency",
        approval_digest=approval["approval_digest"],
    )
    return preview, approval, outcome


def _exports(server: WheelServer) -> tuple[HttpResult, HttpResult]:
    return (
        server.request("GET", "/api/workspace/export/quote"),
        server.request("GET", "/api/workspace/export/evidence"),
    )


def _case_001(server: WheelServer) -> tuple[dict[str, Any], dict[str, Any] | None]:
    formed = _run_admitted_task_intake(server)
    _advance_launch(server)
    before = _state(server)
    first_pid = server.process_ids[-1]
    server.restart()
    after = _state(server)
    _advance_currency(server)
    final = _state(server)
    quote_export, evidence_export = _exports(server)
    quote_payload = _require_ok(quote_export, "quote export")
    evidence_payload = _require_ok(evidence_export, "evidence export")
    tool = formed.get("tool_evidence", {})
    task_intake = final["task_intake"]
    candidate = task_intake["candidate_receipt"]
    confirmation = task_intake["confirmation_receipt"]
    approval_control = final["approval_control"]
    facts = {
        "final_stage": final["stage"],
        "quote_version": final["quote"]["version"],
        "launch_date": final["quote"]["payload"]["launch_date"],
        "currency": final["quote"]["payload"]["currency"],
        "restart_count": 1,
        "restart_stage_before": before["stage"],
        "restart_stage_after": after["stage"],
        "restart_process_changed": first_pid != server.process_ids[-1],
        "restart_state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "tool_status": tool.get("status"),
        "tool_target_writes": tool.get("target_writes"),
        "event_chain_status": final["event_chain"]["status"],
        "event_count": final["event_chain"]["events"],
        "event_chain_head": final["event_chain"]["head_digest"],
        "task_intake_candidate_status": candidate["status"],
        "task_intake_confirmation_status": confirmation["status"],
        "task_intake_run_status": task_intake["status"],
        "task_intake_same_run_id": (
            task_intake["run_id"]
            == candidate["intended_run_id"]
            == confirmation["intended_run_id"]
        ),
        "task_intake_same_workspace_nonce": (
            task_intake["workspace_instance_nonce"]
            == candidate["workspace_instance_nonce"]
            == confirmation["workspace_instance_nonce"]
        ),
        "task_intake_natural_language_authority": candidate[
            "natural_language_authority"
        ],
        "task_intake_canonical_target_writes": task_intake[
            "intake_canonical_target_writes"
        ],
        "review_duration_ms": approval_control["review_duration_ms"],
        "review_gate_store": approval_control["gate_store"],
        "review_gate_restart_enforced": approval_control["restart_enforced"],
        "identity_mode": approval_control["identity_mode"],
        "external_iam": approval_control["external_iam"],
        "process_ids": list(server.process_ids),
    }
    return facts, {"quote_export": quote_payload, "evidence_export": evidence_payload}


def _case_002(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    preview = _preview(server, "launch_date")
    before = _state(server)
    first_pid = server.process_ids[-1]
    server.restart()
    after = _state(server)
    approval = _approve(
        server,
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    outcome = _apply(
        server,
        "launch_date",
        approval_digest=approval["approval_digest"],
    )
    return {
        "restart_count": 1,
        "restart_stage_before": before["stage"],
        "restart_stage_after": after["stage"],
        "restart_process_changed": first_pid != server.process_ids[-1],
        "restart_state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "preview_digest_unchanged": (
            preview["preview_digest"] == after["latest_preview"]["preview_digest"]
        ),
        "resumed_final_stage": outcome["state"]["stage"],
        "process_ids": list(server.process_ids),
    }, None


def _case_003(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    preview = _preview(server, "launch_date")
    approval = _approve(
        server,
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    before = _state(server)
    first_pid = server.process_ids[-1]
    server.restart()
    after = _state(server)
    outcome = _apply(
        server,
        "launch_date",
        approval_digest=approval["approval_digest"],
    )
    return {
        "restart_count": 1,
        "restart_stage_before": before["stage"],
        "restart_stage_after": after["stage"],
        "restart_process_changed": first_pid != server.process_ids[-1],
        "restart_state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "approval_digest_unchanged": (
            approval["approval_digest"] == after["latest_approval"]["approval_digest"]
        ),
        "resumed_final_stage": outcome["state"]["stage"],
        "process_ids": list(server.process_ids),
    }, None


def _case_004(server: WheelServer) -> tuple[dict[str, Any], None]:
    first_form = _run_admitted_task_intake(server)
    after_first_form = _state(server)
    second_form = _run_admitted_task_intake(server)
    after_second_form = _state(server)
    first_preview = _preview(server, "launch_date")
    after_first_preview = _state(server)
    second_preview = _preview(server, "launch_date")
    after_second_preview = _state(server)
    first_approval = _approve(
        server,
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=first_preview["preview_digest"],
    )
    after_first_approval = _state(server)
    second_approval = _approve(
        server,
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=first_preview["preview_digest"],
    )
    after_second_approval = _state(server)
    first_apply = _apply(
        server,
        "launch_date",
        approval_digest=first_approval["approval_digest"],
    )
    after_first_apply = _state(server)
    second_apply = _apply(
        server,
        "launch_date",
        approval_digest=first_approval["approval_digest"],
    )
    after_second_apply = _state(server)
    return {
        "final_stage": after_second_apply["stage"],
        "form_same_result": (
            first_form["receipt"]["digest"] == second_form["receipt"]["digest"]
            and first_form["tool_evidence"]["invocation_artifact_digest"]
            == second_form["tool_evidence"]["invocation_artifact_digest"]
        ),
        "form_no_extra_events": (
            after_first_form["event_chain"] == after_second_form["event_chain"]
        ),
        "preview_same_result": (
            first_preview["artifact_digest"] == second_preview["artifact_digest"]
        ),
        "preview_no_extra_events": (
            after_first_preview["event_chain"] == after_second_preview["event_chain"]
        ),
        "approval_same_result": (
            first_approval["approval_digest"] == second_approval["approval_digest"]
        ),
        "approval_no_extra_events": (
            after_first_approval["event_chain"] == after_second_approval["event_chain"]
        ),
        "apply_same_result": (
            first_apply["artifact_digest"] == second_apply["artifact_digest"]
            and first_apply["state"]["quote"]["digest"]
            == second_apply["state"]["quote"]["digest"]
        ),
        "apply_no_extra_events": (
            after_first_apply["event_chain"] == after_second_apply["event_chain"]
        ),
    }, None


def _case_005(server: WheelServer) -> tuple[dict[str, Any], None]:
    before = _state(server)
    candidate = _require_ok(
        _task_intake_candidate(server, customer_id="customer:globex"),
        "prepare unsupported task intake",
    )
    rejected = server.request(
        "POST",
        "/api/workspace/task-intake/admit",
        {
            "actor_id": TASK_INTAKE_ACTOR,
            "candidate_receipt": candidate,
            "candidate_digest": candidate["digest"],
        },
        extra_headers=_actor_headers(TASK_INTAKE_ACTOR),
    )
    after = _state(server)
    return {
        "candidate_status": candidate["status"],
        "candidate_reason_codes": candidate["reason_codes"],
        "candidate_canonical_target_writes": candidate["canonical_target_writes"],
        "candidate_natural_language_authority": candidate[
            "natural_language_authority"
        ],
        "http_status": rejected.status,
        "error_code": _error_code(rejected.payload),
        "error_reason_code": _error_reason_code(rejected.payload),
        "state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "final_stage": after["stage"],
    }, None


def _case_006(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    before = _state(server)
    response = server.request("POST", "/api/workspace/preview/currency")
    after = _state(server)
    return {
        "http_status": response.status,
        "error_code": _error_code(response.payload),
        "state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "final_stage": after["stage"],
        "canonical_quote_unchanged": before["quote"] == after["quote"],
        "preview_event_id": response.payload.get("kind") if isinstance(response.payload, dict) else None,
    }, None


def _case_007(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    preview = _preview(server, "launch_date")
    before = _state(server)
    rejected = server.request(
        "POST",
        "/api/workspace/approve/launch_date",
        {
            "actor_id": "human:finance-owner",
            "preview_digest": preview["preview_digest"],
        },
        extra_headers=_actor_headers("human:finance-owner"),
    )
    after = _state(server)
    return {
        "http_status": rejected.status,
        "error_code": _error_code(rejected.payload),
        "message_contains_owner_mismatch": (
            "WORKSPACE_APPROVER_MISMATCH" in _error_message(rejected.payload)
        ),
        "state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "final_stage": after["stage"],
    }, None


def _case_008(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    _preview(server, "launch_date")
    before = _state(server)
    rejected = server.request(
        "POST",
        "/api/workspace/approve/launch_date",
        {
            "actor_id": "human:product-owner",
            "preview_digest": "sha256:" + "0" * 64,
        },
        extra_headers=_actor_headers("human:product-owner"),
    )
    after = _state(server)
    return {
        "http_status": rejected.status,
        "error_code": _error_code(rejected.payload),
        "state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "final_stage": after["stage"],
    }, None


def _case_009(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    preview = _preview(server, "launch_date")
    _approve(
        server,
        "launch_date",
        actor_id="human:product-owner",
        preview_digest=preview["preview_digest"],
    )
    before = _state(server)
    rejected = server.request(
        "POST",
        "/api/workspace/apply/launch_date",
        {"approval_digest": "sha256:" + "1" * 64},
    )
    after = _state(server)
    return {
        "http_status": rejected.status,
        "error_code": _error_code(rejected.payload),
        "state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "final_stage": after["stage"],
    }, None


def _case_010(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    before = _state(server)
    direct_form = server.request("POST", "/api/workspace/form")
    workspace_run = server.request("POST", "/api/workspace/run")
    demo_run = server.request("POST", "/api/demo/workspace/quote-to-rebase")
    after = _state(server)
    return {
        "direct_form_status": direct_form.status,
        "direct_form_code": _error_code(direct_form.payload),
        "workspace_run_status": workspace_run.status,
        "demo_run_status": demo_run.status,
        "workspace_run_code": _error_code(workspace_run.payload),
        "demo_run_code": _error_code(demo_run.payload),
        "state_unchanged": _rfc8785_digest(before) == _rfc8785_digest(after),
        "final_stage": after["stage"],
    }, None


def _case_011(server: WheelServer) -> tuple[dict[str, Any], dict[str, Any]]:
    _run_admitted_task_intake(server)
    _advance_launch(server)
    _advance_currency(server)
    quote_export, evidence_export = _exports(server)
    quote_payload = _require_ok(quote_export, "quote export")
    evidence_payload = _require_ok(evidence_export, "evidence export")
    return {
        "quote_export_status": quote_export.status,
        "evidence_export_status": evidence_export.status,
        "final_stage": evidence_payload["stage"],
        "quote_export_content_disposition": quote_export.headers.get("content-disposition"),
        "evidence_export_content_disposition": evidence_export.headers.get("content-disposition"),
    }, {"quote_export": quote_payload, "evidence_export": evidence_payload}


def _atomic_state_shape(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    stage = state.get("stage")
    quote = state.get("quote")
    approval = state.get("latest_approval")
    actions = state.get("actions")
    if not isinstance(quote, dict) or quote.get("version") != "v1" or not isinstance(actions, dict):
        return False
    if stage == "PREVIEWED":
        return approval is None and actions.get("next_operation") == "approve"
    if stage == "APPROVED":
        try:
            owner = approval["approval"]["actor_id"]
        except (KeyError, TypeError):
            return False
        return owner == "human:product-owner" and actions.get("next_operation") == "apply"
    return False


def _case_012(server: WheelServer) -> tuple[dict[str, Any], None]:
    _run_admitted_task_intake(server)
    preview = _preview(server, "launch_date")
    pending = server.request(
        "POST",
        "/api/workspace/approve/launch_date",
        {
            "actor_id": "human:product-owner",
            "preview_digest": preview["preview_digest"],
        },
        extra_headers=_actor_headers("human:product-owner"),
    )
    if pending.status != 409 or _error_code(pending.payload) != "WORKSPACE_REVIEW_GATE_NOT_READY":
        raise CaseExecutionError(f"concurrency case did not observe the review gate: {pending.payload!r}")
    detail = pending.payload.get("detail", {}) if isinstance(pending.payload, dict) else {}
    remaining_ms = detail.get("remaining_ms") if isinstance(detail, dict) else None
    if not isinstance(remaining_ms, int) or remaining_ms < 0:
        raise CaseExecutionError(f"concurrency review gate is invalid: {pending.payload!r}")
    time.sleep((remaining_ms / 1000) + 0.05)
    approval_count = 2
    read_count = 24

    def approve_request() -> HttpResult:
        return server.request(
            "POST",
            "/api/workspace/approve/launch_date",
            {
                "actor_id": "human:product-owner",
                "preview_digest": preview["preview_digest"],
            },
            extra_headers=_actor_headers("human:product-owner"),
        )

    def read_request() -> HttpResult:
        return server.request("GET", "/api/workspace/state")

    lock_connection = sqlite3.connect(server.database_path)
    lock_connection.execute("BEGIN IMMEDIATE")
    overlap_started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            approval_future = executor.submit(approve_request)
            time.sleep(0.5)
            approval_blocked = not approval_future.done()
            read_future = executor.submit(read_request)
            time.sleep(0.25)
            read_blocked = not read_future.done()
            overlap_forced = approval_blocked and read_blocked
            lock_connection.rollback()
            first_approval = approval_future.result(timeout=60)
            blocked_read = read_future.result(timeout=60)
            forced_overlap_ms = int((time.monotonic() - overlap_started) * 1000)
    finally:
        if lock_connection.in_transaction:
            lock_connection.rollback()
        lock_connection.close()
    second_approval = approve_request()
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        read_futures = [executor.submit(read_request) for _ in range(read_count - 1)]
        remaining_reads = [future.result(timeout=60) for future in read_futures]
    approvals = [first_approval, second_approval]
    reads = [blocked_read, *remaining_reads]
    final = _state(server)
    approval_control = final["approval_control"]
    approval_digests = {
        result.payload.get("approval_digest")
        for result in approvals
        if isinstance(result.payload, dict)
    }
    return {
        "review_gate_initial_http_status": pending.status,
        "review_gate_error_code": _error_code(pending.payload),
        "review_gate_remaining_ms_positive": remaining_ms > 0,
        "review_duration_ms": approval_control["review_duration_ms"],
        "review_gate_store": approval_control["gate_store"],
        "approval_requests": approval_count,
        "read_requests": read_count,
        "overlap_forced": overlap_forced,
        "approval_blocked": approval_blocked,
        "read_blocked": read_blocked,
        "writer_lock": "EXTERNAL_SQLITE_BEGIN_IMMEDIATE",
        "forced_overlap_ms": forced_overlap_ms,
        "all_http_status_200": all(
            result.status == 200 for result in [*approvals, *reads]
        ),
        "all_observed_states_atomic": all(
            _atomic_state_shape(result.payload) for result in reads
        ),
        "approval_results_idempotent": len(approval_digests) == 1,
        "observed_stages": sorted(
            {
                str(result.payload.get("stage"))
                for result in reads
                if isinstance(result.payload, dict)
            }
        ),
        "final_stage": final["stage"],
    }, None


CASE_FUNCTIONS = {
    "PP-001": _case_001,
    "PP-002": _case_002,
    "PP-003": _case_003,
    "PP-004": _case_004,
    "PP-005": _case_005,
    "PP-006": _case_006,
    "PP-007": _case_007,
    "PP-008": _case_008,
    "PP-009": _case_009,
    "PP-010": _case_010,
    "PP-011": _case_011,
    "PP-012": _case_012,
}


def _build_wheel(work_root: Path) -> tuple[Path, Path, str]:
    dist_dir = work_root / "dist"
    command = ["uv", "build", "--wheel", "--out-dir", str(dist_dir)]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"wheel build failed:\n{completed.stdout}\n{completed.stderr}")
    wheels = sorted(dist_dir.glob("orgrebase-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one OrgRebase wheel, found {len(wheels)}")
    wheel = wheels[0]
    unpacked = work_root / "wheel-unpacked"
    unpacked.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(unpacked)
    return wheel, unpacked, _sha256_file(wheel)


def _probe_wheel_module(wheel_root: Path, probe_root: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(wheel_root)
    environment["PYTHONNOUSERSITE"] = "1"
    command = (
        "import json; module=__import__('orgrebase'); "
        "print(json.dumps({'module_file': module.__file__, 'version': module.__version__}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=probe_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    module_path = Path(payload["module_file"]).resolve()
    wheel_path = wheel_root.resolve()
    try:
        relative = module_path.relative_to(wheel_path)
        loaded_from_wheel = True
    except ValueError:
        relative = module_path
        loaded_from_wheel = False
    return {
        "module_loaded_from_wheel": loaded_from_wheel,
        "module_relative_path": str(relative),
        "version": payload["version"],
    }


def run(*, output: Path, benchmark_root: Path = DEFAULT_BENCHMARK_ROOT) -> dict[str, Any]:
    output = output.resolve()
    benchmark_root = benchmark_root.resolve()
    public_cases_path = benchmark_root / "public" / "cases.json"
    gold_path = benchmark_root / "evaluator" / "gold.json"
    mutations_path = benchmark_root / "evaluator" / "mutations.json"
    manifest_path = benchmark_root / "MANIFEST.sha256"
    public_cases = json.loads(public_cases_path.read_text(encoding="utf-8"))
    benchmark_version = public_cases["benchmark_version"]
    revision = _protocol_revision(benchmark_version)
    evidence_root = output.parent
    observations_output = evidence_root / "product-path-observations.json"
    wheel_output = evidence_root / "product-path-wheel.whl"
    artifact_manifest_output = evidence_root / "product-path-artifacts.json"
    with tempfile.TemporaryDirectory(prefix="orgrebase-product-path-") as temporary:
        work_root = Path(temporary)
        wheel, wheel_root, wheel_digest = _build_wheel(work_root)
        probe = _probe_wheel_module(wheel_root, work_root)
        if not probe["module_loaded_from_wheel"]:
            raise RuntimeError(f"runtime module did not load from wheel: {probe}")
        observations: list[dict[str, Any]] = []
        oracle_surface: dict[str, Any] | None = None
        server_process_count = 0
        for case in public_cases["cases"]:
            case_id = case["id"]
            case_dir = work_root / "cases" / case_id.lower()
            case_dir.mkdir(parents=True)
            server = WheelServer(
                wheel_root=wheel_root,
                case_dir=case_dir,
                database_path=case_dir / "workspace.sqlite3",
            )
            execution_failures: list[str] = []
            facts: dict[str, Any] = {}
            surface: dict[str, Any] | None = None
            state_marker: str | None = None
            try:
                server.start()
                facts, surface = CASE_FUNCTIONS[case_id](server)
                state_marker = _state(server).get("schema_version")
            except Exception as exc:
                execution_failures.append(f"{type(exc).__name__}:{exc}")
            finally:
                server.stop()
                server_process_count += len(server.process_ids)
            observations.append(
                {
                    "id": case_id,
                    "workspace_state_schema_version": (
                        state_marker if not execution_failures else None
                    ),
                    "facts": facts,
                    "execution_failures": execution_failures,
                }
            )
            if case_id == "PP-011" and surface is not None:
                oracle_surface = surface
        observation_document = {
            "schema_version": f"orgrebase.product-path-observations.v{revision}",
            "benchmark_version": benchmark_version,
            "execution": {
                "mode": "BUILT_WHEEL_UNPACKED_HTTP_UVICORN",
                "runner_product_imports": _product_import_count(Path(__file__)),
                "wheel_filename": wheel.name,
                "wheel_sha256": wheel_digest,
                "wheel_size_bytes": wheel.stat().st_size,
                "wheel_member_inventory": _wheel_inventory(wheel),
                "unpacked_file_count": sum(path.is_file() for path in wheel_root.rglob("*")),
                "module_probe": probe,
                "database_isolation": "ONE_SQLITE_FILE_PER_CASE",
                "real_process_restarts": 3,
                "uvicorn_processes_started": server_process_count,
                "transport": "HTTP_127.0.0.1",
                "public_cases_sha256": _sha256_file(public_cases_path),
                "runner_sha256": _sha256_file(Path(__file__)),
            },
            "cases": observations,
            "oracle_surface": oracle_surface,
        }
        observation_document["digest"] = _rfc8785_digest(observation_document)
        observations_text = json.dumps(
            observation_document, ensure_ascii=False, indent=2
        ) + "\n"
        evidence_root.mkdir(parents=True, exist_ok=True)
        observations_output.write_text(observations_text, encoding="utf-8")
        shutil.copyfile(wheel, wheel_output)
        completed = subprocess.run(
            [
                sys.executable,
                str(EVALUATOR_PATH),
                "--observations",
                str(observations_output),
                "--gold",
                str(gold_path),
                "--mutations",
                str(mutations_path),
                "--manifest",
                str(manifest_path),
                "--artifact-manifest",
                str(artifact_manifest_output),
                "--project-root",
                str(ROOT),
                "--runner",
                str(Path(__file__)),
                "--wheel",
                str(wheel_output),
                "--output",
                str(output),
            ],
            cwd=work_root,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
        if completed.returncode != 0:
            detail = output.read_text(encoding="utf-8") if output.is_file() else ""
            raise RuntimeError(
                f"independent evaluator failed:\n{completed.stdout}\n{completed.stderr}\n{detail}"
            )
    return json.loads(output.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--benchmark-root", type=Path, default=DEFAULT_BENCHMARK_ROOT)
    args = parser.parse_args()
    report = run(output=args.output, benchmark_root=args.benchmark_root)
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": report["case_summary"],
                "mutations": report["mutation_summary"],
                "wheel": report["execution"].get("wheel_sha256"),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
