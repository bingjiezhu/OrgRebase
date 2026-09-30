"""One bounded, scheduler-owned operations pass; no notification transport.

This source-checkout deployment helper composes the existing authenticated API
and worker CLIs.  It is deliberately not a second business state machine.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import select
import signal
import ssl
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
_WORKSPACE = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_MAX_RESPONSE_BYTES = 262_144
_MAX_CONFIG_BYTES = 65_536
_MAX_STATE_BYTES = 4096
_MAX_CA_BUNDLE_BYTES = 1_048_576


class CycleError(Exception):
    """A stable, content-free failure code for deployment monitoring."""


class CycleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.enterprise-operations-cycle.v1"]
    origin: str
    workspace_id: str = Field(pattern=_WORKSPACE)
    read_token_variable: str = Field(pattern=_ENV_NAME)
    admin_token_variable: str | None = Field(default=None, pattern=_ENV_NAME)
    ca_bundle: str | None = None
    source_config: str | None = None
    change_config: str | None = None
    effect_config: str | None = None
    effect_mode: Literal["disabled", "observe", "dispatch"] = "disabled"
    purge_private: bool = False
    max_source_pages: int = Field(default=10, ge=1, le=100)
    max_effect_commands: int = Field(default=20, ge=1, le=100)
    max_purge_batches: int = Field(default=10, ge=1, le=10)
    max_event_pages: int = Field(default=5, ge=1, le=20)
    max_unresolved_changes: int = Field(default=100, ge=0, le=1_000_000)
    max_ready_effects: int = Field(default=100, ge=0, le=1_000_000)
    max_unknown_age_seconds: int = Field(default=60, ge=0, le=604_800)
    max_cycle_gap_seconds: int = Field(default=900, ge=30, le=604_800)

    @model_validator(mode="after")
    def check_scope(self) -> CycleConfig:
        parsed = urlsplit(self.origin)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("OPERATIONS_HTTPS_ORIGIN_REQUIRED")
        if self.effect_mode != "disabled" and self.effect_config is None:
            raise ValueError("OPERATIONS_EFFECT_CONFIG_REQUIRED")
        if self.purge_private and self.admin_token_variable is None:
            raise ValueError("OPERATIONS_ADMIN_TOKEN_VARIABLE_REQUIRED")
        if self.ca_bundle is not None and not Path(self.ca_bundle).is_absolute():
            raise ValueError("OPERATIONS_CA_BUNDLE_INVALID")
        for value in (self.source_config, self.change_config, self.effect_config):
            if value is not None and (not Path(value).is_absolute() or not Path(value).is_file()):
                raise ValueError("OPERATIONS_WORKER_CONFIG_INVALID")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CycleError("OPERATIONS_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _read_private_json(path: Path, *, limit: int) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > limit:
                raise CycleError("OPERATIONS_FILE_UNSAFE")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise CycleError("OPERATIONS_FILE_UNSAFE")
            value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise CycleError("OPERATIONS_JSON_OBJECT_REQUIRED")
        return value
    except CycleError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CycleError("OPERATIONS_FILE_INVALID") from exc


def load_config(path: Path) -> CycleConfig:
    try:
        return CycleConfig.model_validate(_read_private_json(path, limit=_MAX_CONFIG_BYTES))
    except ValueError as exc:
        raise CycleError("OPERATIONS_CONFIG_INVALID") from exc


def _tls_verify(config: CycleConfig) -> bool | ssl.SSLContext:
    if config.ca_bundle is None:
        return True
    try:
        descriptor = os.open(config.ca_bundle, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                    or not 0 < info.st_size <= _MAX_CA_BUNDLE_BYTES):
                raise CycleError("OPERATIONS_CA_BUNDLE_UNAVAILABLE")
            # Load the already checked descriptor, preserving the no-symlink
            # boundary if the configured pathname is replaced concurrently.
            return ssl.create_default_context(cafile=f"/dev/fd/{stream.fileno()}")
    except OSError as exc:
        raise CycleError("OPERATIONS_CA_BUNDLE_UNAVAILABLE") from exc


def _token(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value or len(value) > 16_384 or any(char.isspace() for char in value):
        raise CycleError("OPERATIONS_CREDENTIAL_UNAVAILABLE")
    return value


def _http_json(client: httpx.Client, config: CycleConfig, method: str, path: str,
               token_variable: str) -> dict[str, Any]:
    # Read the variable on each request, allowing the secret manager to rotate
    # between calls. The server alone verifies the signature and membership.
    headers = {"Authorization": "Bearer " + _token(token_variable),
               "X-OrgRebase-Workspace": config.workspace_id}
    try:
        with client.stream(method, config.origin.rstrip("/") + path, headers=headers) as response:
            if response.status_code != 200:
                raise CycleError(f"OPERATIONS_HTTP_{response.status_code}")
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise CycleError("OPERATIONS_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            value = json.loads(b"".join(chunks), object_pairs_hook=_unique_object)
    except CycleError:
        raise
    except (httpx.HTTPError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CycleError("OPERATIONS_REQUEST_UNAVAILABLE") from exc
    if not isinstance(value, dict):
        raise CycleError("OPERATIONS_RESPONSE_INVALID")
    return value


def _invoke_worker(args: list[str]) -> dict[str, Any]:
    # A timed-out child may have committed work. Kill its entire process group
    # and report UNKNOWN; never automatically issue the command again.
    try:
        process = subprocess.Popen([sys.executable, "-m", "orgrebase", *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError as exc:
        raise CycleError("OPERATIONS_WORKER_START_FAILED") from exc
    output = bytearray()
    deadline = time.monotonic() + 120
    try:
        assert process.stdout is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise CycleError("OPERATIONS_WORKER_OUTCOME_UNKNOWN")
            chunk = os.read(process.stdout.fileno(), 65_536)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > _MAX_RESPONSE_BYTES:
                raise CycleError("OPERATIONS_WORKER_RESULT_TOO_LARGE")
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (CycleError, subprocess.TimeoutExpired) as exc:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise CycleError("OPERATIONS_WORKER_OUTCOME_UNKNOWN") from exc
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
    if process.returncode != 0:
        raise CycleError("OPERATIONS_WORKER_FAILED")
    try:
        value = json.loads(output, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CycleError("OPERATIONS_WORKER_RESULT_INVALID") from exc
    if not isinstance(value, dict):
        raise CycleError("OPERATIONS_WORKER_RESULT_INVALID")
    return value


def _worker_summary(kind: str, value: dict[str, Any], *,
                    workspace_id: str) -> tuple[dict[str, Any], list[str]]:
    if (kind != "source" and value.get("workspace_id") != workspace_id) or (
        kind == "source" and value.get("workspace_id", workspace_id) != workspace_id
    ):
        raise CycleError("OPERATIONS_WORKER_WORKSPACE_MISMATCH")
    alerts: list[str] = []
    summary: dict[str, Any] = {"kind": kind}
    if kind == "source":
        status = value.get("status")
        if status not in {"SYNCED", "MORE_PAGES_PENDING"} or value.get("external_writes") != 0:
            raise CycleError("OPERATIONS_SOURCE_RESULT_INVALID")
        summary["status"] = status
        if status == "MORE_PAGES_PENDING":
            alerts.append("SOURCE_PAGE_BACKLOG")
    elif kind == "change":
        if value.get("target_writes") != 0 or value.get("mode") not in {
            "VERTEX_V3_CANDIDATE_ONLY", "LOCAL_DETERMINISTIC_CANDIDATE_ONLY",
        } or type(value.get("processed")) is not int:
            raise CycleError("OPERATIONS_CHANGE_RESULT_INVALID")
        summary["processed"] = value["processed"]
        summary["blocked_attempts"] = value.get("blocked_attempts", {})
        if any(record.get("result") == "BLOCKED" for record in value.get("records", [])):
            alerts.append("CHANGE_ATTEMPT_BLOCKED")
    elif kind == "effect":
        commands = value.get("commands")
        if not isinstance(commands, list) or len(commands) > 100:
            raise CycleError("OPERATIONS_EFFECT_RESULT_INVALID")
        summary["commands_observed"] = len(commands)
        if any(not isinstance(item, dict) or item.get("pending") or item.get("error_code")
               or item.get("state") == "COMMIT_UNKNOWN" for item in commands):
            alerts.append("EFFECT_COMMAND_REQUIRES_RECONCILIATION")
    elif kind == "effect-observe":
        if value.get("target_writes") != 0 or not value.get("observation_digest"):
            raise CycleError("OPERATIONS_EFFECT_OBSERVATION_INVALID")
        summary["status"] = "OBSERVED_READ_ONLY_TARGET"
    return summary, alerts


def _validate_operations(value: dict[str, Any], *, after: int) -> tuple[int, int | None]:
    if value.get("schema_version") != "orgrebase.workspace-operations.v1":
        raise CycleError("OPERATIONS_SCHEMA_INVALID")
    page = value.get("event_page")
    checkpoint = value.get("checkpoint")
    if not isinstance(page, dict) or not isinstance(checkpoint, dict):
        raise CycleError("OPERATIONS_PAGE_INVALID")
    through, next_cursor = page.get("through"), page.get("next_cursor")
    if (page.get("after") != after or type(through) is not int or through < after
            or type(checkpoint.get("sequence_no")) is not int
            or checkpoint["sequence_no"] < through
            or (next_cursor is not None and (type(next_cursor) is not int
                                             or next_cursor <= after or next_cursor != through))):
        raise CycleError("OPERATIONS_PAGE_INVALID")
    return through, next_cursor


def _collect_operations(config: CycleConfig, *, cursor: int,
                        request: Callable[[str, str, str], dict[str, Any]]) -> tuple[dict[str, Any], int, bool]:
    latest: dict[str, Any] = {}
    for _ in range(config.max_event_pages):
        latest = request("GET", f"/api/workspace/operations?after={cursor}&limit=100",
                         config.read_token_variable)
        through, next_cursor = _validate_operations(latest, after=cursor)
        cursor = through
        if next_cursor is None:
            return latest, cursor, False
    return latest, cursor, True


def _alerts(config: CycleConfig, operations: dict[str, Any], *,
            event_backlog: bool, missed_previous_cycle: bool) -> list[str]:
    counts = operations.get("effect_counts")
    source = operations.get("source")
    if (not isinstance(counts, dict) or not isinstance(source, dict)
            or type(operations.get("unresolved_changes")) is not int
            or not isinstance(operations.get("alerts"), list)):
        raise CycleError("OPERATIONS_RESPONSE_INVALID")
    alerts = {item.get("code") for item in operations["alerts"] if isinstance(item, dict)}
    if not all(isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code)
               for code in alerts):
        raise CycleError("OPERATIONS_ALERT_INVALID")
    if operations["unresolved_changes"] > config.max_unresolved_changes:
        alerts.add("CHANGE_BACKLOG_HIGH")
    if (type(counts.get("READY")) is not int or counts["READY"] < 0
            or type(counts.get("COMMIT_UNKNOWN")) is not int or counts["COMMIT_UNKNOWN"] < 0):
        raise CycleError("OPERATIONS_EFFECT_COUNTS_INVALID")
    if counts["READY"] > config.max_ready_effects:
        alerts.add("EFFECT_BACKLOG_HIGH")
    if counts["COMMIT_UNKNOWN"]:
        alerts.add("EXTERNAL_OUTCOME_UNKNOWN")
    age = operations.get("unknown_oldest_observed_age_seconds")
    if age is not None and (type(age) is not int or age < 0):
        raise CycleError("OPERATIONS_UNKNOWN_AGE_INVALID")
    if age is not None and age > config.max_unknown_age_seconds:
        alerts.add("EXTERNAL_OUTCOME_UNKNOWN_AGED")
    if config.source_config and source.get("status") != "COMPLETE":
        alerts.add("SOURCE_COVERAGE_NOT_COMPLETE")
    if event_backlog:
        alerts.add("OPERATIONS_EVENT_BACKLOG_PARTIAL")
    if missed_previous_cycle:
        alerts.add("OPERATIONS_PREVIOUS_CYCLE_LATE")
    return sorted(alerts)


def run_cycle(config: CycleConfig, *, mode: Literal["observe", "run"],
              previous: dict[str, Any] | None = None,
              allow_target_dispatch: bool = False,
              request: Callable[[str, str, str], dict[str, Any]],
              invoke: Callable[[list[str]], dict[str, Any]] = _invoke_worker,
              now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode == "run" and config.effect_mode == "dispatch" and not allow_target_dispatch:
        raise CycleError("OPERATIONS_TARGET_DISPATCH_NOT_AUTHORIZED")
    if mode == "run":
        for path in (config.source_config, config.change_config,
                     config.effect_config if config.effect_mode != "disabled" else None):
            if path is not None and _read_private_json(Path(path), limit=_MAX_CONFIG_BYTES).get(
                "workspace_id"
            ) != config.workspace_id:
                raise CycleError("OPERATIONS_WORKER_CONFIG_WORKSPACE_MISMATCH")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise CycleError("OPERATIONS_CLOCK_INVALID")
    previous = previous or {}
    if previous and (previous.get("schema_version") != "orgrebase.enterprise-operations-state.v1"
                     or previous.get("workspace_id") != config.workspace_id
                     or type(previous.get("event_cursor")) is not int
                     or previous["event_cursor"] < 0):
        raise CycleError("OPERATIONS_STATE_INVALID")
    cursor = previous.get("event_cursor", 0)
    missed = False
    if previous:
        try:
            stamp = datetime.fromisoformat(previous["observed_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or (stamp - now).total_seconds() > 60:
                raise ValueError("OPERATIONS_STATE_CLOCK_INVALID")
            missed = (now - stamp).total_seconds() > config.max_cycle_gap_seconds
        except (KeyError, TypeError, ValueError) as exc:
            raise CycleError("OPERATIONS_STATE_INVALID") from exc
    # Preflight current API identity and baseline before any worker command.
    _, cursor, before_backlog = _collect_operations(config, cursor=cursor, request=request)
    stages: list[dict[str, Any]] = []
    stage_alerts: list[str] = []
    purged = 0
    purge_backlog = False
    if mode == "run":
        stage_configs = (("source", config.source_config,
                          ["source-sync", "--config", config.source_config or "", "--max-pages", str(config.max_source_pages)]),
                         ("change", config.change_config,
                          ["change-worker", "--config", config.change_config or ""]))
        for kind, configured, command in stage_configs:
            if configured:
                try:
                    summary, alerts = _worker_summary(kind, invoke(command),
                                                      workspace_id=config.workspace_id)
                except CycleError as exc:
                    raise CycleError(f"OPERATIONS_{kind.upper()}_STAGE_{exc}") from exc
                stages.append(summary)
                stage_alerts.extend(alerts)
        if config.effect_mode != "disabled":
            command = ["effect-worker", "--config", config.effect_config or "",
                       "--max-commands", str(config.max_effect_commands)]
            if config.effect_mode == "observe":
                command.append("--observe")
            try:
                summary, alerts = _worker_summary("effect-observe" if config.effect_mode == "observe" else "effect",
                                                  invoke(command), workspace_id=config.workspace_id)
            except CycleError as exc:
                raise CycleError(f"OPERATIONS_EFFECT_STAGE_{exc}") from exc
            stages.append(summary)
            stage_alerts.extend(alerts)
        if config.purge_private:
            for _ in range(config.max_purge_batches):
                try:
                    result = request("POST", "/api/workspace/privacy/purge", config.admin_token_variable or "")
                except CycleError as exc:
                    raise CycleError(f"OPERATIONS_PRIVATE_PURGE_OUTCOME_UNKNOWN_{exc}") from exc
                deleted = result.get("deleted")
                if type(deleted) is not int or not 0 <= deleted <= 1000 or result.get("limit") != 1000:
                    raise CycleError("OPERATIONS_PURGE_RESPONSE_INVALID")
                purged += deleted
                if deleted < 1000:
                    break
            else:
                purge_backlog = True
                stage_alerts.append("PRIVATE_PURGE_BACKLOG_PARTIAL")
    after, cursor, after_backlog = _collect_operations(config, cursor=cursor, request=request)
    alerts = sorted(set(_alerts(config, after, event_backlog=after_backlog,
                                missed_previous_cycle=missed) + stage_alerts))
    receipt = {"schema_version": "orgrebase.enterprise-operations-cycle-receipt.v1",
               "workspace_id": config.workspace_id, "mode": mode.upper(),
               "observed_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
               "checkpoint_sequence": after["checkpoint"]["sequence_no"],
               "event_cursor": cursor, "preflight_event_backlog_partial": before_backlog,
               "event_backlog_partial": after_backlog, "stages": stages,
               "private_records_purged": purged, "private_purge_backlog_partial": purge_backlog,
               "unresolved_changes": after["unresolved_changes"],
               "effect_counts": {key: after["effect_counts"][key] for key in
                                 ("READY", "DISPATCHING", "COMMIT_UNKNOWN", "CONFIRMED", "REJECTED")},
               "source_status": after["source"]["status"], "alerts": alerts,
               "notification_delivery": "NOT_ATTEMPTED_CAPTURE_WITH_EXISTING_MONITORING",
               "customer_qualification": "NOT_ESTABLISHED_BY_THIS_CYCLE"}
    state = {"schema_version": "orgrebase.enterprise-operations-state.v1",
             "workspace_id": config.workspace_id, "observed_at": receipt["observed_at"],
             "event_cursor": cursor, "checkpoint_sequence": receipt["checkpoint_sequence"]}
    return receipt, state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    # The deployment owns the parent directory. Atomic replace avoids torn
    # cursor/heartbeat updates; neither state nor stdout stores secret values.
    if not path.is_absolute() or not path.parent.is_dir():
        raise CycleError("OPERATIONS_STATE_PATH_INVALID")
    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--mode", choices=("observe", "run"), default="observe")
    parser.add_argument("--allow-target-dispatch", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        state_path = args.state_file
        if not state_path.is_absolute() or not state_path.parent.is_dir():
            raise CycleError("OPERATIONS_STATE_PATH_INVALID")
        lock_path = state_path.with_name(state_path.name + ".lock")
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise CycleError("OPERATIONS_LOCK_FILE_UNAVAILABLE") from exc
        with os.fdopen(descriptor, "rb"):
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise CycleError("OPERATIONS_LOCK_FILE_UNSAFE")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CycleError("OPERATIONS_CYCLE_ALREADY_RUNNING") from exc
            previous = _read_private_json(state_path, limit=_MAX_STATE_BYTES) if state_path.exists() else None
            with httpx.Client(verify=_tls_verify(config), timeout=10,
                              trust_env=False, follow_redirects=False) as client:
                def request(method: str, path: str, variable: str) -> dict[str, Any]:
                    return _http_json(client, config, method, path, variable)

                receipt, state = run_cycle(config, mode=args.mode, previous=previous,
                                           allow_target_dispatch=args.allow_target_dispatch,
                                           request=request)
            try:
                _write_state(state_path, state)
            except OSError as exc:
                raise CycleError("OPERATIONS_STATE_WRITE_FAILED") from exc
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        if receipt["alerts"]:
            raise SystemExit(3)
    except CycleError as exc:
        print(json.dumps({"schema_version": "orgrebase.enterprise-operations-cycle-error.v1",
                          "error_code": str(exc), "status": "UNKNOWN_OR_INCOMPLETE",
                          "notification_delivery": "NOT_ATTEMPTED"}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
