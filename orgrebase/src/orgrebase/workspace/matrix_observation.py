"""Publish verified Workspace observations to Matrix without task or business authority.

Element displays messages from one service account. These messages describe an
existing run; they do not claim Matrix delivery to, or execution by, a Worker.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest

BOUNDARY = "OBSERVATION_NOT_WORKER_TRANSPORT"
SCHEMA = "orgrebase.matrix-observation.v1"
ROOM_STATE = "orgrebase.observation"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[@!][^\s:]+:[^\s/?#]+\Z")


class MatrixObservationError(RuntimeError):
    """A stable, non-secret observation failure that must not affect business state."""


def _url(value: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        raise MatrixObservationError("MATRIX_OBSERVATION_URL_INVALID")
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise MatrixObservationError("MATRIX_OBSERVATION_URL_INVALID") from exc
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or "\\" in value
    ):
        raise MatrixObservationError("MATRIX_OBSERVATION_URL_INVALID")
    return value.rstrip("/")


def _text(value: Any, *, limit: int = 240) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise MatrixObservationError("MATRIX_OBSERVATION_FIELD_INVALID")
    return value


@dataclass(frozen=True)
class MatrixObservationConfig:
    homeserver_url: str
    element_url: str
    access_token: str = field(repr=False)
    user_id: str
    allowed_workspace_ids: tuple[str, ...]
    viewer_user_id: str | None = None

    def __post_init__(self) -> None:
        scope = self.allowed_workspace_ids
        if (
            not isinstance(scope, (tuple, list))
            or not scope
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 240
                or any(ord(char) < 32 for char in item)
                for item in scope
            )
            or len(set(scope)) != len(scope)
        ):
            raise MatrixObservationError("MATRIX_OBSERVATION_WORKSPACE_SCOPE_INVALID")
        object.__setattr__(self, "allowed_workspace_ids", tuple(scope))
        object.__setattr__(self, "homeserver_url", _url(self.homeserver_url))
        object.__setattr__(self, "element_url", _url(self.element_url))
        if (
            not isinstance(self.access_token, str)
            or not self.access_token
            or any(char.isspace() for char in self.access_token)
        ):
            raise MatrixObservationError("MATRIX_OBSERVATION_TOKEN_MISSING")
        if not isinstance(self.user_id, str):
            raise MatrixObservationError("MATRIX_OBSERVATION_IDENTITY_INVALID")
        for identity in (self.user_id, self.viewer_user_id):
            if identity is not None and (
                not isinstance(identity, str)
                or not _IDENTITY.fullmatch(identity)
                or not identity.startswith("@")
            ):
                raise MatrixObservationError("MATRIX_OBSERVATION_IDENTITY_INVALID")


def load_matrix_config(path: str | Path) -> MatrixObservationConfig:
    """Load an operator-owned private file; credentials never enter public receipts."""
    source = Path(path)
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_mode & 0o077:
            raise MatrixObservationError("MATRIX_OBSERVATION_CONFIG_NOT_PRIVATE")
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) - {
            "homeserver_url",
            "element_url",
            "access_token",
            "user_id",
            "allowed_workspace_ids",
            "viewer_user_id",
        }:
            raise MatrixObservationError("MATRIX_OBSERVATION_CONFIG_INVALID")
        return MatrixObservationConfig(**raw)
    except (OSError, ValueError, TypeError) as exc:
        raise MatrixObservationError("MATRIX_OBSERVATION_CONFIG_INVALID") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _request(
    config: MatrixObservationConfig, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    request = urllib.request.Request(
        config.homeserver_url + "/_matrix/client/v3" + path,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method=method,
        headers={"Authorization": f"Bearer {config.access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise MatrixObservationError("MATRIX_OBSERVATION_RESPONSE_TOO_LARGE")
            result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Neither response bodies nor request URLs/tokens enter a public error.
        raise MatrixObservationError(f"MATRIX_OBSERVATION_HTTP_{exc.code}") from None
    except (OSError, ValueError) as exc:
        raise MatrixObservationError("MATRIX_OBSERVATION_NETWORK_FAILED") from exc
    if not isinstance(result, dict):
        raise MatrixObservationError("MATRIX_OBSERVATION_RESPONSE_INVALID")
    return result


def _snapshot(raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "native_project_id",
        "source_digest",
        "action_count",
        "task_summaries",
        "quote_revision",
        "quote_ref",
        "phase",
    }
    if not isinstance(raw, Mapping) or set(raw) != allowed:
        raise MatrixObservationError("MATRIX_OBSERVATION_SNAPSHOT_INVALID")
    result = dict(raw)
    for name in ("native_project_id", "phase"):
        result[name] = _text(result[name])
    if not isinstance(result["source_digest"], str) or not _DIGEST.fullmatch(result["source_digest"]):
        raise MatrixObservationError("MATRIX_OBSERVATION_SOURCE_DIGEST_INVALID")
    if type(result["action_count"]) is not int or not 0 <= result["action_count"] <= 100_000:
        raise MatrixObservationError("MATRIX_OBSERVATION_ACTION_COUNT_INVALID")
    if result["quote_ref"] is not None:
        result["quote_ref"] = _text(result["quote_ref"])
    revision = result["quote_revision"]
    if revision is not None and (type(revision) is not int or not 0 <= revision <= 100_000):
        raise MatrixObservationError("MATRIX_OBSERVATION_QUOTE_REVISION_INVALID")
    tasks = result["task_summaries"]
    if not isinstance(tasks, list) or len(tasks) > 100:
        raise MatrixObservationError("MATRIX_OBSERVATION_TASKS_INVALID")
    result["task_summaries"] = []
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"task_id", "domain", "status"}:
            raise MatrixObservationError("MATRIX_OBSERVATION_TASKS_INVALID")
        result["task_summaries"].append({key: _text(value) for key, value in task.items()})
    return result


def _scope(directory: str | Path, workspace_id: str, run_id: str) -> Path:
    _text(workspace_id)
    _text(run_id)
    root = Path(directory)
    scoped = root / sha256_digest([workspace_id, run_id]).split(":", 1)[1]
    if root.is_symlink() or scoped.is_symlink():
        raise MatrixObservationError("MATRIX_OBSERVATION_STORAGE_INVALID")
    return scoped


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise MatrixObservationError("MATRIX_OBSERVATION_STORAGE_INVALID")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MatrixObservationError("MATRIX_OBSERVATION_STORAGE_INVALID")
    return raw


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.is_symlink():
        raise MatrixObservationError("MATRIX_OBSERVATION_STORAGE_INVALID")
    descriptor, name = tempfile.mkstemp(prefix=".observation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "digest": sha256_digest(payload)}


def _verify_seal(payload: dict[str, Any]) -> None:
    if payload.get("digest") != sha256_digest(
        {key: value for key, value in payload.items() if key != "digest"}
    ):
        raise MatrixObservationError("MATRIX_OBSERVATION_RECEIPT_INVALID")


def _ensure_room(
    config: MatrixObservationConfig,
    expected: dict[str, Any],
    *,
    authorize: Callable[[], None] | None = None,
) -> str:
    power_levels = {
        "users": {config.user_id: 100},
        "users_default": 0,
        "events": {},
        "events_default": 50,
        "state_default": 100,
        "invite": 100,
        "kick": 100,
        "ban": 100,
        "redact": 100,
    }
    if config.viewer_user_id:
        power_levels["users"][config.viewer_user_id] = 0
    alias_local = "orgrebase-observation-" + sha256_digest(expected).split(":", 1)[1][:40]
    alias = f"#{alias_local}:{config.user_id.split(':', 1)[1]}"
    directory_path = "/directory/room/" + urllib.parse.quote(alias, safe="")
    try:
        resolved = _request(config, "GET", directory_path)
    except MatrixObservationError as exc:
        if str(exc) != "MATRIX_OBSERVATION_HTTP_404":
            raise
        try:
            if authorize is not None:
                authorize()
            _request(
                config,
                "POST",
                "/createRoom",
                {
                    "room_alias_name": alias_local,
                    "name": "OrgRebase · " + expected["run_id"][-60:],
                    "topic": "同任务运行观察。发布者为 OrgRebase 服务。消息不代表 Worker 通过聊天室执行。",
                    "visibility": "private",
                    "preset": "private_chat",
                    "creation_content": {"m.federate": False},
                    "power_level_content_override": power_levels,
                    "invite": [config.viewer_user_id] if config.viewer_user_id else [],
                    "initial_state": [
                        {"type": ROOM_STATE, "state_key": "", "content": expected},
                        {
                            "type": "m.room.guest_access",
                            "state_key": "",
                            "content": {"guest_access": "forbidden"},
                        },
                    ],
                },
            )
        except MatrixObservationError as creation_error:
            # A timeout may happen after creation. Resolve the stable alias rather
            # than creating another room or guessing that creation succeeded.
            try:
                resolved = _request(config, "GET", directory_path)
            except MatrixObservationError:
                raise creation_error from None
        else:
            resolved = _request(config, "GET", directory_path)
    room_id = resolved.get("room_id")
    if not isinstance(room_id, str) or not room_id.startswith("!") or not _IDENTITY.fullmatch(room_id):
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_INVALID")
    room = "/rooms/" + urllib.parse.quote(room_id, safe="")
    if _request(config, "GET", room + "/state/" + ROOM_STATE + "/") != expected:
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_BINDING_MISMATCH")
    if _request(config, "GET", room + "/state/m.room.join_rules/").get("join_rule") != "invite":
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_NOT_PRIVATE")
    if _request(config, "GET", room + "/state/m.room.create/").get("m.federate") is not False:
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_FEDERATED")
    if _request(config, "GET", room + "/state/m.room.guest_access/").get("guest_access") != "forbidden":
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_GUEST_ACCESS")
    if (
        _request(config, "GET", room + "/state/m.room.history_visibility/").get("history_visibility")
        != "shared"
    ):
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_HISTORY_INVALID")
    members = _request(config, "GET", room + "/members").get("chunk", [])
    if not isinstance(members, list):
        raise MatrixObservationError("MATRIX_OBSERVATION_MEMBERS_INVALID")
    identities = {}
    for member in members:
        if (
            not isinstance(member, dict)
            or not isinstance(member.get("state_key"), str)
            or not isinstance(member.get("content"), dict)
            or member["state_key"] in identities
        ):
            raise MatrixObservationError("MATRIX_OBSERVATION_MEMBERS_INVALID")
        identities[member["state_key"]] = member["content"].get("membership")
    allowed_members = {config.user_id, config.viewer_user_id}
    if any(
        identity not in allowed_members and membership not in {"leave", "ban"}
        for identity, membership in identities.items()
    ):
        raise MatrixObservationError("MATRIX_OBSERVATION_UNEXPECTED_MEMBER")
    if identities.get(config.user_id) != "join":
        raise MatrixObservationError("MATRIX_OBSERVATION_PUBLISHER_NOT_JOINED")
    if config.viewer_user_id and identities.get(config.viewer_user_id) not in {"invite", "join"}:
        raise MatrixObservationError("MATRIX_OBSERVATION_VIEWER_NOT_INVITED")
    observed_levels = _request(config, "GET", room + "/state/m.room.power_levels/")
    if any(observed_levels.get(key) != value for key, value in power_levels.items()) or observed_levels.get(
        "events", {}
    ):
        raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_PERMISSIONS_INVALID")
    return room_id


def _task_summary_text(task: Mapping[str, str]) -> str:
    roles = {
        "product": "产品",
        "legal": "法务",
        "finance": "财务",
        "gtm": "市场与销售",
        "coordination": "任务协调",
        "review": "独立复核",
        "reviewer": "独立复核",
    }
    statuses = {
        "PASS": "通过",
        "TRUSTED_COMPLETE": "任务验收通过",
        "ABSTAIN": "证据不足 · 暂不出结论",
        "REPLAN_REQUIRED": "需要重新安排",
        "COMPLETED": "任务已完成",
        "FAILED": "任务失败",
        "RUNNING": "执行中",
        "PENDING": "待执行",
    }
    domain, status = task["domain"], task["status"]
    role = f"{roles[domain]} ({domain})" if domain in roles else domain
    result = f"{statuses[status]} ({status})" if status in statuses else status
    return f"{role} · {result}\n  任务: {task['task_id']}"


def publish_observation(
    config: MatrixObservationConfig,
    directory: str | Path,
    *,
    workspace_id: str,
    run_id: str,
    snapshot: Mapping[str, Any],
    workspace_url: str,
    authorize: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Publish a server-selected snapshot and confirm the exact homeserver event."""
    if workspace_id not in config.allowed_workspace_ids:
        raise MatrixObservationError("MATRIX_OBSERVATION_WORKSPACE_FORBIDDEN")
    previous = public_observation(directory, workspace_id=workspace_id, run_id=run_id)
    try:
        selected = _snapshot(snapshot)
        workspace_url = _url(workspace_url)
    except MatrixObservationError as exc:
        record_observation_failure(
            directory,
            workspace_id=workspace_id,
            run_id=run_id,
            expected_receipt_digest=previous.get("digest"),
            error_code=str(exc),
        )
        raise
    root = _scope(directory, workspace_id, run_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root / ".lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if authorize is not None:
            authorize()
        expected = {
            "schema_version": SCHEMA,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "native_project_id": selected["native_project_id"],
            "publisher_user_id": config.user_id,
            "homeserver_url": config.homeserver_url,
            "element_url": config.element_url,
            "claim_boundary": BOUNDARY,
            "canonical_writes": 0,
        }
        binding_path = root / "binding.json"
        if binding_path.exists():
            previous = _read(binding_path)
            _verify_seal(previous)
            if any(previous.get(key) != value for key, value in expected.items()):
                raise MatrixObservationError("MATRIX_OBSERVATION_RUN_BINDING_MISMATCH")
        publisher = _request(config, "GET", "/account/whoami")
        if publisher.get("user_id") != config.user_id:
            raise MatrixObservationError("MATRIX_OBSERVATION_SENDER_MISMATCH")
        if publisher.get("is_guest", False) is not False:
            raise MatrixObservationError("MATRIX_OBSERVATION_PUBLISHER_IS_GUEST")
        room_id = _ensure_room(config, expected, authorize=authorize)
        binding = _sealed({**expected, "room_id": room_id})
        if binding_path.exists() and _read(binding_path) != binding:
            raise MatrixObservationError("MATRIX_OBSERVATION_ROOM_CHANGED")
        _write(binding_path, binding)
        observation = {
            "schema_version": SCHEMA,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "publisher_user_id": config.user_id,
            "snapshot": selected,
            "workspace_url": workspace_url,
            "claim_boundary": BOUNDARY,
            "canonical_writes": 0,
        }
        content = {
            "msgtype": "m.text",
            "body": (
                f"OrgRebase 运行观察 · {run_id}\n"
                f"阶段: {selected['phase']}\n"
                f"初始形成阶段已记录 AgentTeams 操作: {selected['action_count']}\n"
                f"任务项目: {selected['native_project_id']}\n"
                f"当前成果: {selected['quote_ref'] or '尚未形成'}\n"
                + "\n".join(
                    _task_summary_text(task)
                    for task in selected["task_summaries"]
                )
                + f"\n业务工作区: {workspace_url}\n"
                "后续成果版本变化不代表重新运行上述初始形成操作。\n"
                "以上由 OrgRebase 服务发布。聊天室观察不代表 Worker 通过 Matrix 执行。消息不授予审批权。"
            ),
            "orgrebase.observation": observation,
        }
        transaction_id = "orgrebase_observation_" + sha256_digest(content).split(":", 1)[1]
        room = "/rooms/" + urllib.parse.quote(room_id, safe="")
        if authorize is not None:
            authorize()
        sent = _request(config, "PUT", room + "/send/m.room.message/" + transaction_id, content)
        event_id = sent.get("event_id")
        if not isinstance(event_id, str) or not event_id.startswith("$"):
            raise MatrixObservationError("MATRIX_OBSERVATION_EVENT_INVALID")
        event = _request(config, "GET", room + "/event/" + urllib.parse.quote(event_id, safe=""))
        if (
            event.get("event_id") != event_id
            or event.get("sender") != config.user_id
            or event.get("type") != "m.room.message"
            or event.get("content") != content
            or event.get("room_id", room_id) != room_id
        ):
            raise MatrixObservationError("MATRIX_OBSERVATION_EVENT_READBACK_MISMATCH")
        receipt = _sealed(
            {
                "schema_version": SCHEMA,
                "status": "OBSERVED",
                "workspace_id": workspace_id,
                "run_id": run_id,
                "binding_digest": binding["digest"],
                "room_id": room_id,
                "event_id": event_id,
                "publisher_user_id": config.user_id,
                "observation_digest": sha256_digest(observation),
                "source_digest": selected["source_digest"],
                "snapshot": selected,
                "observed_at": datetime.now(UTC).isoformat(),
                "claim_boundary": BOUNDARY,
                "canonical_writes": 0,
            }
        )
        _write(root / "observation.json", receipt)
        return public_observation(directory, workspace_id=workspace_id, run_id=run_id)
    except MatrixObservationError as exc:
        _write(
            root / "observation.json",
            _sealed(
                {
                    "schema_version": SCHEMA,
                    "status": "UNAVAILABLE",
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "error_code": str(exc),
                    "claim_boundary": BOUNDARY,
                    "canonical_writes": 0,
                }
            ),
        )
        raise
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def record_observation_failure(
    directory: str | Path,
    *,
    workspace_id: str,
    run_id: str,
    expected_receipt_digest: str | None,
    error_code: str,
) -> None:
    """Invalidate one failed attempt without overwriting a newer publication.

    The caller must authenticate the task actor and exact run first. Explicit
    workspace-scope rejection never writes an observation record.
    """
    if error_code == "MATRIX_OBSERVATION_WORKSPACE_FORBIDDEN":
        return
    if not re.fullmatch(r"MATRIX_OBSERVATION_[A-Z0-9_]+", error_code):
        error_code = "MATRIX_OBSERVATION_PUBLICATION_FAILED"
    root = _scope(directory, workspace_id, run_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        path = root / "observation.json"
        if path.exists():
            current = _read(path)
            _verify_seal(current)
            if current.get("digest") != expected_receipt_digest:
                return
        elif expected_receipt_digest is not None:
            return
        _write(
            path,
            _sealed(
                {
                    "schema_version": SCHEMA,
                    "status": "UNAVAILABLE",
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "error_code": error_code,
                    "claim_boundary": BOUNDARY,
                    "canonical_writes": 0,
                }
            ),
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def public_observation(directory: str | Path, *, workspace_id: str, run_id: str) -> dict[str, Any]:
    """Read a previously verified observation; never create files or contact Matrix."""
    base = {
        "schema_version": SCHEMA,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "status": "NOT_PUBLISHED",
        "claim_boundary": BOUNDARY,
        "canonical_writes": 0,
    }
    try:
        root = _scope(directory, workspace_id, run_id)
        if not (root / "observation.json").exists():
            return base
        receipt = _read(root / "observation.json")
        _verify_seal(receipt)
        if any(
            receipt.get(key) != base[key]
            for key in ("schema_version", "workspace_id", "run_id", "claim_boundary", "canonical_writes")
        ):
            raise MatrixObservationError("MATRIX_OBSERVATION_RECEIPT_INVALID")
        if receipt.get("status") == "UNAVAILABLE":
            return {**base, "status": "UNAVAILABLE", "error_code": receipt.get("error_code")}
        binding = _read(root / "binding.json")
        _verify_seal(binding)
        if (
            receipt.get("status") != "OBSERVED"
            or receipt.get("binding_digest") != binding["digest"]
            or any(
                receipt.get(key) != binding.get(key)
                for key in (
                    "workspace_id",
                    "run_id",
                    "room_id",
                    "publisher_user_id",
                    "claim_boundary",
                    "canonical_writes",
                )
            )
            or receipt.get("snapshot", {}).get("native_project_id") != binding.get("native_project_id")
        ):
            raise MatrixObservationError("MATRIX_OBSERVATION_RECEIPT_INVALID")
        _snapshot(receipt["snapshot"])
        element_url = _url(binding["element_url"])
        return {
            **receipt,
            "element_room_url": element_url + "/#/room/" + urllib.parse.quote(binding["room_id"], safe=""),
        }
    except (OSError, ValueError, KeyError, TypeError, MatrixObservationError):
        return {**base, "status": "UNAVAILABLE", "error_code": "MATRIX_OBSERVATION_RECEIPT_INVALID"}
