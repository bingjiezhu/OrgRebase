"""Publish one identity-bound OrgRebase run event from an AgentTeams Worker.

The script is intended to run inside the Worker pod.  It reads the Worker's
Matrix token from the runtime environment, verifies the homeserver identity,
binds the event to a frozen run envelope and derives artifact digests from the
exact bytes on disk.  Tokens are never accepted on the command line or logged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
EVENT_SCHEMA = "orgrebase.matrix-run-event.v1"
EVENT_KINDS = {"LEADER_DELEGATION", "WORKER_CANDIDATE", "SKILL_LOADED"}
TOKEN_ENV = "AGENTTEAMS_WORKER_MATRIX_TOKEN"
WORKER_ENV = "AGENTTEAMS_WORKER_NAME"
MATRIX_URL_ENV = "AGENTTEAMS_MATRIX_URL"
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublicationError(RuntimeError):
    """A safe, user-facing publication failure."""


def _json_file(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise PublicationError(f"unsafe or missing file: {path}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"invalid JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"expected one JSON object: {path.name}")
    return value


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _workers(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = envelope.get("workers")
    if not isinstance(items, list):
        raise PublicationError("run envelope has no Worker bindings")
    workers = {
        item.get("worker_name"): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("worker_name"), str)
    }
    if len(workers) != len(items):
        raise PublicationError("run envelope has ambiguous Worker bindings")
    return workers


def build_event_content(
    *,
    envelope: dict[str, Any],
    worker_name: str,
    event_kind: str,
    artifact_path: Path | None = None,
    artifact_ref: str | None = None,
    skill_path: Path | None = None,
    skill_name: str | None = None,
    orchestration_plan_digest: str | None = None,
) -> dict[str, Any]:
    """Build a validated Matrix content object without performing network I/O."""

    if envelope.get("schema_version") != RUN_SCHEMA:
        raise PublicationError("run envelope schema mismatch")
    if event_kind not in EVENT_KINDS:
        raise PublicationError(f"unsupported event kind: {event_kind}")
    if not isinstance(envelope.get("run_id"), str) or not isinstance(
        envelope.get("nonce"), str
    ):
        raise PublicationError("run envelope binding is incomplete")
    workers = _workers(envelope)
    worker = workers.get(worker_name)
    if worker is None:
        raise PublicationError(f"Worker is not bound to this run: {worker_name}")
    if event_kind == "LEADER_DELEGATION" and worker.get("role") != "team_leader":
        raise PublicationError("only the bound team leader may publish delegation")

    payload: dict[str, Any] = {
        "schema_version": EVENT_SCHEMA,
        "run_id": envelope["run_id"],
        "nonce": envelope["nonce"],
        "worker_name": worker_name,
        "event_kind": event_kind,
    }
    if event_kind == "LEADER_DELEGATION":
        if not isinstance(orchestration_plan_digest, str) or not DIGEST_PATTERN.fullmatch(
            orchestration_plan_digest
        ):
            raise PublicationError("delegation requires a valid orchestration plan digest")
        payload["orchestration_plan_digest"] = orchestration_plan_digest
    elif event_kind == "WORKER_CANDIDATE":
        if artifact_path is None or not artifact_ref:
            raise PublicationError("candidate publication requires artifact path and ref")
        artifact = _json_file(artifact_path)
        if (
            artifact.get("run_id") != envelope["run_id"]
            or artifact.get("nonce") != envelope["nonce"]
            or artifact.get("worker_name") != worker_name
            or artifact.get("candidate_only") is not True
        ):
            raise PublicationError("candidate artifact is not bound to this Worker run")
        plan_digest = artifact.get("orchestration_plan_digest")
        task_digest = artifact.get("delegation_task_digest")
        input_refs = artifact.get("input_refs")
        if (
            not isinstance(plan_digest, str)
            or not DIGEST_PATTERN.fullmatch(plan_digest)
            or not isinstance(task_digest, str)
            or not DIGEST_PATTERN.fullmatch(task_digest)
            or not isinstance(input_refs, list)
            or not input_refs
            or len(input_refs) != len(set(input_refs))
            or any(
                not isinstance(item, str) or not DIGEST_PATTERN.fullmatch(item)
                for item in input_refs
            )
        ):
            raise PublicationError("candidate lacks proof-carrying orchestration bindings")
        payload |= {
            "artifact_ref": artifact_ref,
            "artifact_digest": _digest(artifact_path),
            "candidate_only": True,
            "orchestration_plan_digest": plan_digest,
            "delegation_task_digest": task_digest,
            "input_refs": input_refs,
        }
    elif event_kind == "SKILL_LOADED":
        expected_skill = envelope.get("skill")
        if not isinstance(expected_skill, dict):
            raise PublicationError("run envelope has no Skill binding")
        if skill_path is None or not skill_name:
            raise PublicationError("Skill publication requires skill path and name")
        skill_digest = _digest(skill_path)
        if (
            expected_skill.get("assigned_worker") != worker_name
            or expected_skill.get("name") != skill_name
            or expected_skill.get("digest") != skill_digest
        ):
            raise PublicationError("runtime Skill bytes do not match the run envelope")
        payload |= {"skill_name": skill_name, "skill_digest": skill_digest}

    body = " ".join(f"{key}={value}" for key, value in payload.items())
    return {"msgtype": "m.text", "body": body, "orgrebase.run": payload}


def _request_json(
    *, url: str, token: str, method: str = "GET", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            value = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PublicationError("Matrix request failed") from exc
    if not isinstance(value, dict):
        raise PublicationError("Matrix returned a non-object response")
    return value


def publish(
    *,
    envelope_path: Path,
    worker_name: str,
    event_kind: str,
    matrix_url: str,
    token: str,
    artifact_path: Path | None = None,
    artifact_ref: str | None = None,
    skill_path: Path | None = None,
    skill_name: str | None = None,
    orchestration_plan_digest: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    envelope = _json_file(envelope_path)
    effective_now = int(time.time() * 1000) if now_ms is None else now_ms
    issued_at = envelope.get("issued_at_ms")
    expires_at = envelope.get("expires_at_ms")
    if not (
        isinstance(issued_at, int)
        and isinstance(expires_at, int)
        and issued_at <= effective_now <= expires_at
    ):
        raise PublicationError("run envelope is not currently valid")
    runtime_worker = os.environ.get(WORKER_ENV)
    if runtime_worker and runtime_worker != worker_name:
        raise PublicationError("runtime Worker identity does not match --worker-name")
    workers = _workers(envelope)
    expected = workers.get(worker_name)
    if expected is None:
        raise PublicationError("Worker is not bound to this run")

    base_url = matrix_url.rstrip("/")
    whoami = _request_json(
        url=f"{base_url}/_matrix/client/v3/account/whoami", token=token
    )
    if whoami.get("user_id") != expected.get("matrix_user_id"):
        raise PublicationError("Matrix token does not belong to the bound Worker")
    content = build_event_content(
        envelope=envelope,
        worker_name=worker_name,
        event_kind=event_kind,
        artifact_path=artifact_path,
        artifact_ref=artifact_ref,
        skill_path=skill_path,
        skill_name=skill_name,
        orchestration_plan_digest=orchestration_plan_digest,
    )
    room_id = envelope.get("team", {}).get("room_id")
    if not isinstance(room_id, str) or not room_id.startswith("!"):
        raise PublicationError("run envelope has no valid Team room")
    transaction_digest = hashlib.sha256(
        json.dumps(content["orgrebase.run"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    encoded_room = urllib.parse.quote(room_id, safe="")
    result = _request_json(
        url=(
            f"{base_url}/_matrix/client/v3/rooms/{encoded_room}/send/"
            f"m.room.message/orgrebase_{transaction_digest}"
        ),
        token=token,
        method="PUT",
        payload=content,
    )
    event_id = result.get("event_id")
    if not isinstance(event_id, str) or not event_id.startswith("$"):
        raise PublicationError("Matrix did not return a server event ID")
    payload = content["orgrebase.run"]
    return {
        "event_id": event_id,
        "sender": expected["matrix_user_id"],
        "event_kind": event_kind,
        "artifact_digest": payload.get("artifact_digest"),
        "skill_digest": payload.get("skill_digest"),
        "orchestration_plan_digest": payload.get("orchestration_plan_digest"),
        "delegation_task_digest": payload.get("delegation_task_digest"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-envelope", type=Path, required=True)
    parser.add_argument("--worker-name", required=True)
    parser.add_argument("--event-kind", choices=sorted(EVENT_KINDS), required=True)
    parser.add_argument("--artifact-path", type=Path)
    parser.add_argument("--artifact-ref")
    parser.add_argument("--skill-path", type=Path)
    parser.add_argument("--skill-name")
    parser.add_argument("--orchestration-plan-digest")
    parser.add_argument("--matrix-url", default=os.environ.get(MATRIX_URL_ENV))
    args = parser.parse_args()
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"missing runtime credential environment: {TOKEN_ENV}")
    if not args.matrix_url:
        raise SystemExit(f"missing Matrix URL: pass --matrix-url or set {MATRIX_URL_ENV}")
    try:
        result = publish(
            envelope_path=args.run_envelope,
            worker_name=args.worker_name,
            event_kind=args.event_kind,
            matrix_url=args.matrix_url,
            token=token,
            artifact_path=args.artifact_path,
            artifact_ref=args.artifact_ref,
            skill_path=args.skill_path,
            skill_name=args.skill_name,
            orchestration_plan_digest=args.orchestration_plan_digest,
        )
    except PublicationError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
