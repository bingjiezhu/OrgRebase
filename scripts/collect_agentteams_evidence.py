"""Fail-closed collector for one live AgentTeams execution.

LIVE_AGENTTEAMS is emitted only when Kubernetes, Matrix, candidate artifacts, the
runtime-loaded Skill and a provider model-call export all bind to one fresh run
envelope. Natural-language transcripts and hand-authored resource summaries are
deliberately insufficient.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest

AGENTTEAMS_VERSION = "v1.2.2"
AGENTTEAMS_SOURCE_COMMIT = "849182af8e017168a5a200a87b1062142caf462d"
RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
MATRIX_PAYLOAD_SCHEMA = "orgrebase.matrix-run-event.v1"
ARTIFACT_MANIFEST_SCHEMA = "orgrebase.agentteams-artifact-manifest.v1"
MODEL_CALL_SCHEMA = "orgrebase.agentteams-model-call.v1"
RECEIPT_SCHEMA = "orgrebase.agentteams-live-evidence.v2"
MAX_RUN_WINDOW_MS = 2 * 60 * 60 * 1000
NONCE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
EXPECTED_WORKERS = {
    "change-coordinator",
    "product-steward",
    "legal-steward",
    "gtm-steward",
    "skill-curator",
}


class EvidenceError(RuntimeError):
    """Stable, machine-readable reason why a source bundle is not live evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise EvidenceError(code, message)


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(code, f"cannot read {path.name}: {exc}") from exc
    _fail(isinstance(value, dict), code, f"{path.name} must contain one JSON object")
    return value


def _jsonl(path: Path, *, code: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvidenceError(code, f"cannot read {path.name}: {exc}") from exc
    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(code, f"invalid JSON at {path.name}:{line_number}") from exc
        _fail(isinstance(item, dict), code, f"{path.name}:{line_number} is not an object")
        items.append(item)
    _fail(bool(items), code, f"{path.name} is empty")
    return items


def _source_bytes(path: Path, code: str) -> bytes:
    """Read exactly the bytes that will be parsed and bound into the receipt."""

    try:
        _fail(path.is_file() and not path.is_symlink(), code, f"unsafe source {path.name}")
        return path.read_bytes()
    except OSError as exc:
        raise EvidenceError(code, f"cannot read {path.name}: {exc}") from exc


def _json_bytes(data: bytes, path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(code, f"cannot parse {path.name}: {exc}") from exc
    _fail(isinstance(value, dict), code, f"{path.name} must contain one JSON object")
    return value


def _jsonl_bytes(data: bytes, path: Path, *, code: str) -> list[dict[str, Any]]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise EvidenceError(code, f"cannot decode {path.name}: {exc}") from exc
    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(code, f"invalid JSON at {path.name}:{line_number}") from exc
        _fail(isinstance(item, dict), code, f"{path.name}:{line_number} is not an object")
        items.append(item)
    _fail(bool(items), code, f"{path.name} is empty")
    return items


def _bytes_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _kubectl_json(resource: str) -> dict[str, Any]:
    result = subprocess.run(
        ["kubectl", "get", resource, "-A", "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise EvidenceError(
            "KUBERNETES_QUERY_FAILED",
            f"kubectl {resource} failed: {(result.stderr or '').strip()[:200]}",
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvidenceError("KUBERNETES_QUERY_FAILED", "kubectl returned invalid JSON") from exc
    _fail(isinstance(value, dict), "KUBERNETES_QUERY_FAILED", "kubectl result is not an object")
    return value


def _context() -> str:
    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode or not result.stdout.strip():
        raise EvidenceError("KUBERNETES_QUERY_FAILED", "kubectl current-context failed")
    return result.stdout.strip()


def _items(document: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    items = document.get("items")
    _fail(isinstance(items, list), "KUBERNETES_SCHEMA_INVALID", f"{kind}.items is missing")
    _fail(all(isinstance(item, dict) for item in items), "KUBERNETES_SCHEMA_INVALID", kind)
    return items


def _resource(
    items: list[dict[str, Any]], namespace: str, name: str, kind: str
) -> dict[str, Any]:
    matches = [
        item
        for item in items
        if item.get("metadata", {}).get("namespace", "default") == namespace
        and item.get("metadata", {}).get("name") == name
    ]
    _fail(
        len(matches) == 1,
        "KUBERNETES_IDENTITY_MISMATCH",
        f"expected one {kind} {namespace}/{name}",
    )
    return matches[0]


def _validate_envelope(envelope: dict[str, Any], now_ms: int) -> None:
    _fail(envelope.get("schema_version") == RUN_SCHEMA, "RUN_ENVELOPE_INVALID", "run schema")
    run_id = envelope.get("run_id")
    nonce = envelope.get("nonce")
    issued = envelope.get("issued_at_ms")
    expires = envelope.get("expires_at_ms")
    _fail(
        isinstance(run_id, str) and run_id.startswith("run:orgrebase:live:"),
        "RUN_ENVELOPE_INVALID",
        "run_id",
    )
    _fail(
        isinstance(nonce, str) and bool(NONCE_PATTERN.fullmatch(nonce)),
        "RUN_ENVELOPE_INVALID",
        "nonce",
    )
    _fail(
        isinstance(issued, int) and isinstance(expires, int),
        "RUN_ENVELOPE_INVALID",
        "time window",
    )
    _fail(
        issued < expires <= issued + MAX_RUN_WINDOW_MS,
        "RUN_ENVELOPE_INVALID",
        "run exceeds two-hour window",
    )
    _fail(
        issued <= now_ms <= expires,
        "RUN_ENVELOPE_EXPIRED",
        "collection is outside the run window",
    )
    lock = envelope.get("agentteams", {})
    _fail(
        lock.get("version") == AGENTTEAMS_VERSION
        and lock.get("source_commit") == AGENTTEAMS_SOURCE_COMMIT,
        "AGENTTEAMS_SOURCE_MISMATCH",
        "runtime is not bound to the audited AgentTeams source commit",
    )
    workers = envelope.get("workers")
    _fail(isinstance(workers, list), "RUN_ENVELOPE_INVALID", "workers")
    _fail(
        all(
            isinstance(item, dict)
            and isinstance(item.get("worker_name"), str)
            and item.get("role") in {"team_leader", "worker"}
            and isinstance(item.get("model"), str)
            and bool(item.get("model"))
            and isinstance(item.get("runtime"), str)
            and bool(item.get("runtime"))
            and (
                item.get("image") is None
                or (isinstance(item.get("image"), str) and bool(item.get("image")))
            )
            and isinstance(item.get("required_skills"), list)
            for item in workers
        ),
        "RUN_ENVELOPE_INVALID",
        "worker fields",
    )
    names = [item.get("worker_name") for item in workers if isinstance(item, dict)]
    _fail(
        set(names) == EXPECTED_WORKERS and len(names) == len(set(names)),
        "RUN_ENVELOPE_INVALID",
        "worker set",
    )
    roles = [item.get("role") for item in workers if isinstance(item, dict)]
    _fail(
        roles.count("team_leader") == 1,
        "RUN_ENVELOPE_INVALID",
        "exactly one leader is required",
    )
    _fail(isinstance(envelope.get("team"), dict), "RUN_ENVELOPE_INVALID", "team")
    _fail(
        isinstance(envelope.get("runtime_pods"), list),
        "RUN_ENVELOPE_INVALID",
        "runtime_pods",
    )
    _fail(
        all(
            isinstance(item, dict)
            and item.get("container_name") == "worker"
            and isinstance(item.get("image"), str)
            and bool(item.get("image"))
            and isinstance(item.get("image_id"), str)
            and bool(item.get("image_id"))
            for item in envelope["runtime_pods"]
        ),
        "RUN_ENVELOPE_INVALID",
        "runtime pod fields",
    )
    _fail(isinstance(envelope.get("skill"), dict), "RUN_ENVELOPE_INVALID", "skill")


def _worker_map(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["worker_name"]: item for item in envelope["workers"]}


def _validate_kubernetes(
    envelope: dict[str, Any],
    workers_doc: dict[str, Any],
    teams_doc: dict[str, Any],
    pods_doc: dict[str, Any],
) -> dict[str, Any]:
    worker_items = _items(workers_doc, "WorkerList")
    team_items = _items(teams_doc, "TeamList")
    pod_items = _items(pods_doc, "PodList")
    worker_evidence: list[dict[str, Any]] = []
    workers = _worker_map(envelope)

    for expected in workers.values():
        required = {
            "namespace",
            "resource_name",
            "uid",
            "generation",
            "worker_name",
            "matrix_user_id",
            "role",
            "model",
            "runtime",
            "image",
            "required_skills",
        }
        _fail(
            required <= set(expected),
            "RUN_ENVELOPE_INVALID",
            f"incomplete worker {expected.get('worker_name')}",
        )
        item = _resource(
            worker_items, expected["namespace"], expected["resource_name"], "Worker"
        )
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        _fail(
            metadata.get("uid") == expected["uid"],
            "KUBERNETES_IDENTITY_MISMATCH",
            "Worker UID",
        )
        _fail(
            metadata.get("generation") == expected["generation"],
            "KUBERNETES_GENERATION_DRIFT",
            "Worker generation",
        )
        _fail(
            spec.get("workerName") == expected["worker_name"],
            "KUBERNETES_SPEC_DRIFT",
            "workerName",
        )
        _fail(spec.get("model") == expected["model"], "KUBERNETES_SPEC_DRIFT", "model")
        _fail(
            spec.get("runtime") == expected["runtime"],
            "KUBERNETES_SPEC_DRIFT",
            "runtime",
        )
        declared_image = spec.get("image") or None
        _fail(
            declared_image == expected["image"],
            "KUBERNETES_SPEC_DRIFT",
            "declared Worker image",
        )
        if declared_image is not None:
            _fail(
                not declared_image.endswith(":latest"),
                "MUTABLE_RUNTIME_IMAGE",
                "Worker image uses :latest",
            )
        _fail(
            set(expected["required_skills"]) <= set(spec.get("skills", [])),
            "SKILL_NOT_ASSIGNED",
            "Worker.spec.skills",
        )
        _fail(
            status.get("observedGeneration") == metadata.get("generation"),
            "WORKER_NOT_RECONCILED",
            "observedGeneration",
        )
        _fail(status.get("phase") == "Running", "WORKER_NOT_READY", "Worker phase")
        _fail(
            isinstance(status.get("specHash"), str) and status["specHash"],
            "WORKER_NOT_READY",
            "specHash",
        )
        _fail(
            status.get("matrixUserID") == expected["matrix_user_id"],
            "MATRIX_SENDER_MISMATCH",
            "Worker Matrix identity",
        )
        worker_evidence.append(
            {
                "worker_name": expected["worker_name"],
                "resource_name": expected["resource_name"],
                "uid": metadata["uid"],
                "generation": metadata["generation"],
                "resource_version": metadata.get("resourceVersion"),
                "spec_hash": status["specHash"],
                "matrix_user_id": status["matrixUserID"],
            }
        )

    expected_team = envelope["team"]
    _fail(
        {"namespace", "name", "uid", "generation", "room_id"} <= set(expected_team),
        "RUN_ENVELOPE_INVALID",
        "incomplete team binding",
    )
    team = _resource(
        team_items, expected_team["namespace"], expected_team["name"], "Team"
    )
    team_metadata = team.get("metadata", {})
    team_spec = team.get("spec", {})
    team_status = team.get("status", {})
    _fail(
        team_metadata.get("uid") == expected_team["uid"],
        "KUBERNETES_IDENTITY_MISMATCH",
        "Team UID",
    )
    _fail(
        team_metadata.get("generation") == expected_team["generation"],
        "KUBERNETES_GENERATION_DRIFT",
        "Team generation",
    )
    expected_members = {(item["resource_name"], item["role"]) for item in workers.values()}
    actual_members = {
        (item.get("name"), item.get("role")) for item in team_spec.get("workerMembers", [])
    }
    _fail(
        team_spec.get("teamName") == expected_team["name"]
        and actual_members == expected_members,
        "KUBERNETES_SPEC_DRIFT",
        "Team members",
    )
    _fail(
        team_status.get("phase") == "Active" and team_status.get("leaderReady") is True,
        "TEAM_NOT_READY",
        "Team status",
    )
    _fail(
        team_status.get("readyWorkers", 0) >= 3,
        "TEAM_NOT_READY",
        "fewer than three ready Workers",
    )
    _fail(
        team_status.get("teamRoomID") == expected_team["room_id"],
        "MATRIX_ROOM_MISMATCH",
        "Team room",
    )
    status_members = team_status.get("members", [])
    _fail(isinstance(status_members, list), "TEAM_NOT_READY", "Team status members")
    for expected in workers.values():
        matches = [
            member
            for member in status_members
            if member.get("name") == expected["resource_name"]
        ]
        _fail(
            len(matches) == 1,
            "TEAM_NOT_READY",
            f"missing Team status member {expected['worker_name']}",
        )
        member = matches[0]
        _fail(
            member.get("runtimeName") == expected["worker_name"]
            and member.get("role") == expected["role"]
            and member.get("matrixUserID") == expected["matrix_user_id"]
            and member.get("observed") is True
            and member.get("ready") is True
            and member.get("phase") == "Running"
            and bool(member.get("specHash")),
            "TEAM_NOT_READY",
            f"unready or mismatched Team status member {expected['worker_name']}",
        )

    pod_evidence: list[dict[str, Any]] = []
    declared_pods = envelope["runtime_pods"]
    _fail(
        len(declared_pods) >= 3,
        "POD_IDENTITY_MISMATCH",
        "fewer than three declared runtime pods",
    )
    for expected in declared_pods:
        required = {
            "namespace",
            "name",
            "uid",
            "worker_resource_name",
            "container_name",
            "image",
            "image_id",
        }
        _fail(
            isinstance(expected, dict) and required <= set(expected),
            "RUN_ENVELOPE_INVALID",
            "pod binding",
        )
        pod = _resource(pod_items, expected["namespace"], expected["name"], "Pod")
        metadata = pod.get("metadata", {})
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        _fail(
            metadata.get("uid") == expected["uid"],
            "POD_IDENTITY_MISMATCH",
            "Pod UID",
        )
        _fail(
            metadata.get("labels", {}).get("agentteams.io/worker")
            == expected["worker_resource_name"],
            "POD_IDENTITY_MISMATCH",
            "controller Worker label",
        )
        _fail(status.get("phase") == "Running", "POD_NOT_READY", "Pod phase")
        conditions = status.get("conditions", [])
        _fail(
            any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in conditions
            ),
            "POD_NOT_READY",
            "Pod Ready condition",
        )
        spec_containers = [
            item
            for item in spec.get("containers", [])
            if item.get("name") == expected["container_name"]
        ]
        _fail(
            len(spec_containers) == 1
            and spec_containers[0].get("image") == expected["image"],
            "POD_IDENTITY_MISMATCH",
            "runtime container image",
        )
        _fail(
            not expected["image"].endswith(":latest"),
            "MUTABLE_RUNTIME_IMAGE",
            "runtime container uses :latest",
        )
        container_statuses = [
            item
            for item in status.get("containerStatuses", [])
            if item.get("name") == expected["container_name"]
        ]
        _fail(
            len(container_statuses) == 1
            and container_statuses[0].get("imageID") == expected["image_id"],
            "POD_IDENTITY_MISMATCH",
            "runtime container imageID",
        )
        _fail(
            "@sha256:" in expected["image_id"],
            "MUTABLE_RUNTIME_IMAGE",
            "missing immutable runtime imageID digest",
        )
        pod_evidence.append(
            {
                "name": expected["name"],
                "uid": expected["uid"],
                "worker_resource_name": expected["worker_resource_name"],
                "resource_version": metadata.get("resourceVersion"),
                "container_name": expected["container_name"],
                "image": expected["image"],
                "image_id": expected["image_id"],
            }
        )

    return {
        "team": {
            "name": expected_team["name"],
            "uid": team_metadata["uid"],
            "generation": team_metadata["generation"],
            "resource_version": team_metadata.get("resourceVersion"),
            "room_id": team_status["teamRoomID"],
        },
        "workers": sorted(worker_evidence, key=lambda item: item["worker_name"]),
        "pods": sorted(pod_evidence, key=lambda item: item["name"]),
    }


def _event_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    content = event.get("content")
    if not isinstance(content, dict):
        return None
    payload = content.get("orgrebase.run")
    return payload if isinstance(payload, dict) else None


def _validate_matrix(
    envelope: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    required = {"event_id", "room_id", "sender", "type", "origin_server_ts", "content"}
    _fail(
        all(required <= set(event) for event in events),
        "MATRIX_EXPORT_INVALID",
        "not a server event export",
    )
    _fail(
        all(
            isinstance(event["event_id"], str)
            and event["event_id"].startswith("$")
            and isinstance(event["room_id"], str)
            and event["room_id"].startswith("!")
            and isinstance(event["sender"], str)
            and event["sender"].startswith("@")
            and isinstance(event["type"], str)
            and isinstance(event["origin_server_ts"], int)
            and isinstance(event["content"], dict)
            for event in events
        ),
        "MATRIX_EXPORT_INVALID",
        "invalid server event fields",
    )
    event_ids = [event["event_id"] for event in events]
    _fail(
        len(event_ids) == len(set(event_ids)),
        "MATRIX_EXPORT_INVALID",
        "duplicate event_id",
    )
    workers = _worker_map(envelope)
    by_sender = {item["matrix_user_id"]: item for item in workers.values()}
    room_id = envelope["team"]["room_id"]
    issued, expires = envelope["issued_at_ms"], envelope["expires_at_ms"]
    structured: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in events:
        payload = _event_payload(event)
        if payload is None:
            continue
        _fail(
            event["type"] == "m.room.message",
            "MATRIX_EXPORT_INVALID",
            "structured run payload must be a room message",
        )
        _fail(
            event["room_id"] == room_id,
            "MATRIX_ROOM_MISMATCH",
            "structured event is outside Team room",
        )
        _fail(
            issued <= event["origin_server_ts"] <= expires,
            "MATRIX_EVENT_OUTSIDE_RUN",
            event["event_id"],
        )
        _fail(
            payload.get("schema_version") == MATRIX_PAYLOAD_SCHEMA
            and payload.get("run_id") == envelope["run_id"]
            and payload.get("nonce") == envelope["nonce"],
            "MATRIX_RUN_MISMATCH",
            event["event_id"],
        )
        _fail(
            event["sender"] in by_sender,
            "MATRIX_SENDER_MISMATCH",
            event["sender"],
        )
        _fail(
            payload.get("worker_name") == by_sender[event["sender"]]["worker_name"],
            "MATRIX_SENDER_MISMATCH",
            "payload worker",
        )
        structured.append((event, payload))
    _fail(
        bool(structured),
        "MATRIX_STRUCTURED_EVENTS_MISSING",
        "natural-language bodies are not evidence",
    )

    def joined(sender: str, timestamp: int) -> bool:
        memberships = [
            event
            for event in events
            if event.get("type") == "m.room.member"
            and event.get("room_id") == room_id
            and event.get("state_key") == sender
            and isinstance(event.get("origin_server_ts"), int)
            and event["origin_server_ts"] < timestamp
        ]
        if not memberships:
            return False
        latest_timestamp = max(item["origin_server_ts"] for item in memberships)
        latest_states = {
            item.get("content", {}).get("membership")
            for item in memberships
            if item["origin_server_ts"] == latest_timestamp
        }
        return latest_states == {"join"}

    for event, _payload in structured:
        _fail(
            joined(event["sender"], event["origin_server_ts"]),
            "MATRIX_MEMBERSHIP_INVALID",
            f"{event['sender']} was not unambiguously joined for {event['event_id']}",
        )

    leader = next(item for item in workers.values() if item["role"] == "team_leader")
    delegations = [
        (event, payload)
        for event, payload in structured
        if payload.get("event_kind") == "LEADER_DELEGATION"
        and event["sender"] == leader["matrix_user_id"]
    ]
    _fail(
        bool(delegations),
        "LEADER_DELEGATION_MISSING",
        "no structured leader delegation",
    )
    delegation, delegation_payload = min(
        delegations, key=lambda item: item[0]["origin_server_ts"]
    )
    orchestration_plan_digest = delegation_payload.get("orchestration_plan_digest")
    _fail(
        isinstance(orchestration_plan_digest, str)
        and bool(DIGEST_PATTERN.fullmatch(orchestration_plan_digest)),
        "ORCHESTRATION_BINDING_INVALID",
        "leader delegation has no valid orchestration plan digest",
    )
    candidates: list[dict[str, Any]] = []
    for event, payload in structured:
        if payload.get("event_kind") != "WORKER_CANDIDATE":
            continue
        _fail(
            event["origin_server_ts"] > delegation["origin_server_ts"],
            "MATRIX_EVENT_ORDER_INVALID",
            event["event_id"],
        )
        _fail(
            payload.get("candidate_only") is True,
            "CANDIDATE_AUTHORITY_INVALID",
            event["event_id"],
        )
        _fail(
            isinstance(payload.get("artifact_ref"), str) and payload["artifact_ref"],
            "MATRIX_EXPORT_INVALID",
            "artifact_ref",
        )
        _fail(
            isinstance(payload.get("artifact_digest"), str),
            "MATRIX_EXPORT_INVALID",
            "artifact_digest",
        )
        input_refs = payload.get("input_refs")
        _fail(
            payload.get("orchestration_plan_digest") == orchestration_plan_digest
            and isinstance(payload.get("delegation_task_digest"), str)
            and bool(DIGEST_PATTERN.fullmatch(payload["delegation_task_digest"]))
            and isinstance(input_refs, list)
            and bool(input_refs)
            and len(input_refs) == len(set(input_refs))
            and all(
                isinstance(item, str) and bool(DIGEST_PATTERN.fullmatch(item))
                for item in input_refs
            ),
            "ORCHESTRATION_BINDING_INVALID",
            event["event_id"],
        )
        candidates.append({"event": event, "payload": payload})
    candidate_senders = {item["event"]["sender"] for item in candidates}
    _fail(
        len(candidate_senders) >= 3,
        "INSUFFICIENT_DISTINCT_WORKERS",
        "need three structured Worker candidates",
    )
    return (
        {
            "room_id": room_id,
            "leader_delegation_event_id": delegation["event_id"],
            "orchestration_plan_digest": orchestration_plan_digest,
            "candidate_event_ids": sorted(
                item["event"]["event_id"] for item in candidates
            ),
            "candidate_senders": sorted(candidate_senders),
            "candidate_task_bindings": sorted(
                (
                    {
                        "worker_name": item["payload"]["worker_name"],
                        "delegation_task_digest": item["payload"][
                            "delegation_task_digest"
                        ],
                        "input_refs": item["payload"]["input_refs"],
                    }
                    for item in candidates
                ),
                key=lambda item: item["worker_name"],
            ),
        },
        candidates,
    )


def _safe_artifact(root: Path, relative: str) -> Path:
    _fail(
        bool(relative) and not Path(relative).is_absolute(),
        "ARTIFACT_PATH_INVALID",
        relative,
    )
    root = root.resolve()
    path = (root / relative).resolve()
    _fail(
        path.is_relative_to(root) and path.is_file() and not path.is_symlink(),
        "ARTIFACT_PATH_INVALID",
        relative,
    )
    return path


def _validate_artifacts(
    envelope: dict[str, Any],
    manifest: dict[str, Any],
    candidates: list[dict[str, Any]],
    events: list[dict[str, Any]],
    artifact_root: Path,
) -> dict[str, Any]:
    _fail(
        manifest.get("schema_version") == ARTIFACT_MANIFEST_SCHEMA,
        "ARTIFACT_MANIFEST_INVALID",
        "schema",
    )
    _fail(
        manifest.get("run_id") == envelope["run_id"]
        and manifest.get("nonce") == envelope["nonce"],
        "ARTIFACT_RUN_MISMATCH",
        "manifest",
    )
    artifacts = manifest.get("artifacts")
    _fail(
        isinstance(artifacts, list) and artifacts,
        "ARTIFACT_MANIFEST_INVALID",
        "artifacts",
    )
    refs: dict[str, dict[str, Any]] = {}
    workers = _worker_map(envelope)
    for item in artifacts:
        required = {
            "ref",
            "path",
            "digest",
            "schema_version",
            "producer_worker",
            "producer_matrix_user_id",
            "candidate_only",
        }
        _fail(
            isinstance(item, dict) and required <= set(item),
            "ARTIFACT_MANIFEST_INVALID",
            "artifact entry",
        )
        _fail(
            item["ref"] not in refs,
            "ARTIFACT_MANIFEST_INVALID",
            "duplicate artifact ref",
        )
        worker = workers.get(item["producer_worker"])
        _fail(
            worker is not None
            and worker["matrix_user_id"] == item["producer_matrix_user_id"],
            "ARTIFACT_PRODUCER_MISMATCH",
            item["ref"],
        )
        _fail(
            item["candidate_only"] is True,
            "CANDIDATE_AUTHORITY_INVALID",
            item["ref"],
        )
        path = _safe_artifact(artifact_root, item["path"])
        _fail(
            _file_digest(path) == item["digest"],
            "ARTIFACT_DIGEST_MISMATCH",
            item["ref"],
        )
        refs[item["ref"]] = item
    for candidate in candidates:
        event, payload = candidate["event"], candidate["payload"]
        item = refs.get(payload["artifact_ref"])
        _fail(
            item is not None,
            "ARTIFACT_REFERENCE_MISSING",
            payload["artifact_ref"],
        )
        _fail(
            item["digest"] == payload["artifact_digest"],
            "ARTIFACT_DIGEST_MISMATCH",
            item["ref"],
        )
        _fail(
            item["producer_worker"] == payload["worker_name"]
            and item["producer_matrix_user_id"] == event["sender"],
            "ARTIFACT_PRODUCER_MISMATCH",
            item["ref"],
        )
        candidate_document = _json(
            _safe_artifact(artifact_root, item["path"]),
            "ARTIFACT_MANIFEST_INVALID",
        )
        _fail(
            candidate_document.get("orchestration_plan_digest")
            == payload.get("orchestration_plan_digest")
            and candidate_document.get("delegation_task_digest")
            == payload.get("delegation_task_digest")
            and candidate_document.get("input_refs") == payload.get("input_refs"),
            "ORCHESTRATION_BINDING_INVALID",
            item["ref"],
        )

    skill = manifest.get("skill_runtime_evidence")
    expected_skill = envelope["skill"]
    required_skill = {
        "skill_name",
        "assigned_worker",
        "worker_matrix_user_id",
        "skill_path",
        "skill_digest",
        "loaded_event_id",
    }
    _fail(
        isinstance(skill, dict) and required_skill <= set(skill),
        "SKILL_RUNTIME_EVIDENCE_MISSING",
        "skill_runtime_evidence",
    )
    _fail(
        skill["skill_name"] == expected_skill.get("name")
        and skill["assigned_worker"] == expected_skill.get("assigned_worker")
        and skill["skill_digest"] == expected_skill.get("digest"),
        "SKILL_DIGEST_MISMATCH",
        "run/manifest Skill binding",
    )
    assigned = workers.get(skill["assigned_worker"])
    _fail(
        assigned is not None
        and skill["worker_matrix_user_id"] == assigned["matrix_user_id"],
        "SKILL_RUNTIME_EVIDENCE_MISSING",
        "Skill worker",
    )
    skill_path = _safe_artifact(artifact_root, skill["skill_path"])
    _fail(
        _file_digest(skill_path) == skill["skill_digest"],
        "SKILL_DIGEST_MISMATCH",
        "runtime Skill bytes",
    )
    loaded_events = [
        event
        for event in events
        if event.get("event_id") == skill["loaded_event_id"]
        and event.get("sender") == skill["worker_matrix_user_id"]
        and (_event_payload(event) or {}).get("event_kind") == "SKILL_LOADED"
        and (_event_payload(event) or {}).get("skill_name") == skill["skill_name"]
        and (_event_payload(event) or {}).get("skill_digest") == skill["skill_digest"]
        and (_event_payload(event) or {}).get("run_id") == envelope["run_id"]
        and (_event_payload(event) or {}).get("nonce") == envelope["nonce"]
    ]
    _fail(
        len(loaded_events) == 1,
        "SKILL_RUNTIME_EVIDENCE_MISSING",
        "structured SKILL_LOADED event",
    )
    return {
        "candidate_artifacts": sorted(
            (
                {"ref": item["ref"], "digest": item["digest"]}
                for item in refs.values()
            ),
            key=lambda item: item["ref"],
        ),
        "skill": {
            "name": skill["skill_name"],
            "digest": skill["skill_digest"],
            "assigned_worker": skill["assigned_worker"],
            "loaded_event_id": skill["loaded_event_id"],
        },
    }


def _validate_model_calls(
    envelope: dict[str, Any],
    calls: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    workers = _worker_map(envelope)
    candidate_bindings = {
        (
            item["payload"]["worker_name"],
            item["payload"]["artifact_ref"],
            item["payload"]["artifact_digest"],
        ): item["event"]["origin_server_ts"]
        for item in candidates
    }
    valid: list[dict[str, Any]] = []
    for call in calls:
        if call.get("schema_version") != MODEL_CALL_SCHEMA:
            continue
        if (
            call.get("run_id") != envelope["run_id"]
            or call.get("nonce") != envelope["nonce"]
        ):
            continue
        if call.get("status") != "SUCCEEDED" or call.get("worker_name") not in workers:
            continue
        request_id = call.get("provider_request_id")
        model = call.get("model")
        timestamp = call.get("timestamp_ms")
        worker = workers.get(call.get("worker_name"))
        candidate_timestamp = candidate_bindings.get(
            (
                call.get("worker_name"),
                call.get("artifact_ref"),
                call.get("artifact_digest"),
            )
        )
        if (
            not isinstance(request_id, str)
            or not request_id
            or not isinstance(model, str)
            or not model
            or worker is None
            or model != worker["model"]
            or candidate_timestamp is None
        ):
            continue
        if (
            not isinstance(timestamp, int)
            or not envelope["issued_at_ms"] <= timestamp <= envelope["expires_at_ms"]
            or timestamp > candidate_timestamp
        ):
            continue
        valid.append(call)
    valid_workers = {item["worker_name"] for item in valid}
    valid_bindings = {
        (item["worker_name"], item["artifact_ref"], item["artifact_digest"])
        for item in valid
    }
    candidate_workers = {item[0] for item in candidate_bindings}
    _fail(
        len(candidate_workers) >= 3 and set(candidate_bindings) <= valid_bindings,
        "MODEL_CALL_EVIDENCE_MISSING",
        "every candidate Worker needs a bound successful provider request",
    )
    request_ids = [item["provider_request_id"] for item in valid]
    _fail(
        len(request_ids) == len(set(request_ids)),
        "MODEL_CALL_EVIDENCE_INVALID",
        "provider request IDs must be unique",
    )
    return {
        "successful_calls": len(valid),
        "successful_workers": sorted(valid_workers),
        "provider_request_ids": sorted(request_ids),
        "models": sorted({item["model"] for item in valid}),
        "candidate_bindings": sorted(
            (
                {
                    "worker_name": item["worker_name"],
                    "artifact_ref": item["artifact_ref"],
                    "artifact_digest": item["artifact_digest"],
                    "provider_request_id": item["provider_request_id"],
                }
                for item in valid
            ),
            key=lambda item: item["worker_name"],
        ),
    }


def _record_nonce(
    ledger_path: Path, run_id: str, nonce: str, bundle_digest: str
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if ledger_path.exists():
            ledger = _json(ledger_path, "NONCE_LEDGER_INVALID")
            _fail(
                ledger.get("schema_version")
                == "orgrebase.agentteams-nonce-ledger.v1",
                "NONCE_LEDGER_INVALID",
                "schema",
            )
        else:
            ledger = {
                "schema_version": "orgrebase.agentteams-nonce-ledger.v1",
                "entries": {},
            }
        entries = ledger.get("entries")
        _fail(isinstance(entries, dict), "NONCE_LEDGER_INVALID", "entries")
        existing = entries.get(nonce)
        if existing is not None:
            _fail(
                existing == {"run_id": run_id, "source_bundle_digest": bundle_digest},
                "NONCE_REPLAY_DETECTED",
                "nonce was already bound to different source evidence",
            )
            return
        entries[nonce] = {"run_id": run_id, "source_bundle_digest": bundle_digest}
        encoded = json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=ledger_path.parent,
                prefix=f".{ledger_path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, ledger_path)
            directory_fd = os.open(ledger_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def collect(
    *,
    run_envelope_path: Path,
    matrix_events_path: Path,
    artifact_manifest_path: Path,
    model_calls_path: Path,
    artifact_root: Path,
    nonce_ledger_path: Path,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Validate and bind every source needed for a LIVE_AGENTTEAMS claim."""

    source_bytes = {
        "run_envelope": _source_bytes(run_envelope_path, "RUN_ENVELOPE_INVALID"),
        "matrix_export": _source_bytes(matrix_events_path, "MATRIX_EXPORT_INVALID"),
        "artifact_manifest": _source_bytes(
            artifact_manifest_path, "ARTIFACT_MANIFEST_INVALID"
        ),
        "model_call_export": _source_bytes(
            model_calls_path, "MODEL_CALL_EXPORT_INVALID"
        ),
    }
    envelope = _json_bytes(
        source_bytes["run_envelope"], run_envelope_path, "RUN_ENVELOPE_INVALID"
    )
    manifest = _json_bytes(
        source_bytes["artifact_manifest"],
        artifact_manifest_path,
        "ARTIFACT_MANIFEST_INVALID",
    )
    events = _jsonl_bytes(
        source_bytes["matrix_export"], matrix_events_path, code="MATRIX_EXPORT_INVALID"
    )
    model_calls = _jsonl_bytes(
        source_bytes["model_call_export"],
        model_calls_path,
        code="MODEL_CALL_EXPORT_INVALID",
    )
    effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
    _validate_envelope(envelope, effective_now)
    workers_doc = _kubectl_json("workers.agentteams.io")
    teams_doc = _kubectl_json("teams.agentteams.io")
    pods_doc = _kubectl_json("pods")
    kubernetes = _validate_kubernetes(
        envelope, workers_doc, teams_doc, pods_doc
    )
    matrix, candidates = _validate_matrix(envelope, events)
    artifacts = _validate_artifacts(
        envelope, manifest, candidates, events, artifact_root
    )
    models = _validate_model_calls(envelope, model_calls, candidates)

    source_digests = {
        name: _bytes_digest(data) for name, data in source_bytes.items()
    } | {
        "kubernetes_snapshot": sha256_digest(
            {"workers": workers_doc, "teams": teams_doc, "pods": pods_doc}
        ),
    }
    bundle_digest = sha256_digest(source_digests)
    _validate_envelope(
        envelope, now_ms if now_ms is not None else int(time.time() * 1000)
    )
    _record_nonce(
        nonce_ledger_path,
        envelope["run_id"],
        envelope["nonce"],
        bundle_digest,
    )
    evidence_core = {
        "run_id": envelope["run_id"],
        "nonce": envelope["nonce"],
        "agentteams": {
            "version": AGENTTEAMS_VERSION,
            "source_commit": AGENTTEAMS_SOURCE_COMMIT,
        },
        "kubernetes_context": _context(),
        "kubernetes": kubernetes,
        "matrix": matrix,
        "artifacts": artifacts,
        "model_calls": models,
        "source_digests": source_digests,
        "source_bundle_digest": bundle_digest,
    }
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "PASS",
        "evidence_class": "LIVE_AGENTTEAMS",
        "source": (
            "direct Kubernetes API + Matrix server export + byte-addressed artifacts "
            "+ provider export"
        ),
        "evidence": evidence_core,
        "receipt_digest": sha256_digest(evidence_core),
        "claim_boundary": (
            "Proves one observed AgentTeams execution and its evidence bindings; it "
            "does not prove production accuracy, ROI, or long-term reliability."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-envelope", type=Path, required=True)
    parser.add_argument("--matrix-events", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--model-calls", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--nonce-ledger", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/agentteams/live-receipt.json"),
    )
    args = parser.parse_args()
    try:
        report = collect(
            run_envelope_path=args.run_envelope,
            matrix_events_path=args.matrix_events,
            artifact_manifest_path=args.artifact_manifest,
            model_calls_path=args.model_calls,
            artifact_root=args.artifact_root,
            nonce_ledger_path=args.nonce_ledger,
        )
    except (EvidenceError, OSError, ValueError) as exc:
        report = {
            "schema_version": RECEIPT_SCHEMA,
            "status": "NOT_RUN",
            "evidence_class": "NOT_RUN",
            "error_code": getattr(exc, "code", "COLLECTION_FAILED"),
            "reason": str(exc),
            "claim_boundary": "No live claim may be made from this artifact.",
        }
        exit_code = 2
    else:
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
