#!/usr/bin/env python3
"""Freeze one immutable AgentTeams live-run envelope from Kubernetes state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WORKERS = {
    "change-coordinator",
    "product-steward",
    "legal-steward",
    "gtm-steward",
    "skill-curator",
}
SAFE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


class FreezeError(RuntimeError):
    pass


def _qualified_deployment_source() -> dict[str, str]:
    """Require the active source to match this collector's reviewed deployment."""
    try:
        source = json.loads((ROOT / "agentteams/source-lock.json").read_text(encoding="utf-8"))
        reviewed = json.loads((ROOT / "configs/goai-agentteams-demo.json").read_text(encoding="utf-8"))["agentteams"]
        schema = json.loads((ROOT / "schemas/agentteams-live-run.schema.json").read_text(encoding="utf-8"))
        identity = schema["properties"]["agentteams"]["properties"]
        qualified = (reviewed["version"], reviewed["source_commit"])
        if (
            source["repository"] != "https://github.com/agentscope-ai/AgentTeams"
            or (source["tag"], source["commit"]) != qualified
            or (identity["version"]["const"], identity["source_commit"]["const"]) != qualified
        ):
            raise FreezeError("DEPLOYMENT_SOURCE_NOT_QUALIFIED")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FreezeError("DEPLOYMENT_SOURCE_NOT_QUALIFIED") from exc
    return {"version": qualified[0], "source_commit": qualified[1]}


def _run(*command: str) -> str:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "failed"
        raise FreezeError(f"command failed: {command[0]}: {detail}")
    return result.stdout


def _kubectl_json(context: str, namespace: str, *arguments: str) -> dict[str, Any]:
    raw = _run(
        "kubectl",
        "--context",
        context,
        "--namespace",
        namespace,
        *arguments,
        "-o",
        "json",
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FreezeError("kubectl did not return JSON") from error
    if not isinstance(value, dict):
        raise FreezeError("kubectl result is not an object")
    return value


def _single(items: list[dict[str, Any]], description: str) -> dict[str, Any]:
    if len(items) != 1:
        raise FreezeError(f"expected one {description}, found {len(items)}")
    return items[0]


def _container(pod: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    spec_matches = [
        item for item in pod.get("spec", {}).get("containers", []) if item.get("name") == "worker"
    ]
    status_matches = [
        item
        for item in pod.get("status", {}).get("containerStatuses", [])
        if item.get("name") == "worker"
    ]
    return _single(spec_matches, "worker container"), _single(
        status_matches, "worker container status"
    )


def _runtime_skill_digest(
    context: str,
    namespace: str,
    pod_name: str,
    worker_name: str,
    skill_name: str,
) -> str:
    if not SAFE_NAME.fullmatch(worker_name) or not SAFE_NAME.fullmatch(skill_name):
        raise FreezeError("unsafe Worker or Skill name")
    skill_path = f"/root/agentteams-fs/agents/{worker_name}/skills/{skill_name}/SKILL.md"
    raw = _run(
        "kubectl",
        "--context",
        context,
        "--namespace",
        namespace,
        "exec",
        pod_name,
        "--",
        "sha256sum",
        skill_path,
    )
    digest = raw.split(maxsplit=1)[0]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise FreezeError("runtime Skill digest is invalid")
    return f"sha256:{digest}"


def freeze(
    *,
    context: str,
    namespace: str,
    team_name: str,
    skill_name: str,
    skill_worker: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    qualified_source = _qualified_deployment_source()
    if not 60 <= ttl_seconds <= 7200:
        raise FreezeError("ttl must be between 60 and 7200 seconds")

    team = _kubectl_json(context, namespace, "get", "team", team_name)
    workers_document = _kubectl_json(context, namespace, "get", "workers.agentteams.io")
    pods_document = _kubectl_json(context, namespace, "get", "pods")
    workers = workers_document.get("items", [])
    pods = pods_document.get("items", [])
    if not isinstance(workers, list) or not isinstance(pods, list):
        raise FreezeError("Kubernetes list response is invalid")

    team_members = {
        item.get("name"): item.get("role")
        for item in team.get("spec", {}).get("workerMembers", [])
        if isinstance(item, dict)
    }
    selected_workers = [
        worker
        for worker in workers
        if worker.get("metadata", {}).get("name") in team_members
    ]
    runtime_names = {worker.get("spec", {}).get("workerName") for worker in selected_workers}
    if runtime_names != EXPECTED_WORKERS or len(selected_workers) != len(EXPECTED_WORKERS):
        raise FreezeError("Team does not contain the exact OrgRebase five-Worker set")

    worker_entries: list[dict[str, Any]] = []
    pod_entries: list[dict[str, Any]] = []
    pod_by_worker: dict[str, dict[str, Any]] = {}
    for worker in selected_workers:
        metadata = worker.get("metadata", {})
        spec = worker.get("spec", {})
        status = worker.get("status", {})
        resource_name = metadata.get("name")
        worker_name = spec.get("workerName")
        matching_pods = [
            pod
            for pod in pods
            if pod.get("metadata", {}).get("labels", {}).get("agentteams.io/worker")
            == resource_name
        ]
        pod = _single(matching_pods, f"runtime Pod for {resource_name}")
        pod_by_worker[worker_name] = pod
        container_spec, container_status = _container(pod)
        image = container_spec.get("image")
        image_id = container_status.get("imageID")
        if not isinstance(image, str) or image.endswith(":latest"):
            raise FreezeError(f"mutable or missing runtime image for {resource_name}")
        if not isinstance(image_id, str) or "@sha256:" not in image_id:
            raise FreezeError(f"immutable imageID missing for {resource_name}")

        worker_entries.append(
            {
                "namespace": namespace,
                "resource_name": resource_name,
                "uid": metadata.get("uid"),
                "generation": metadata.get("generation"),
                "worker_name": worker_name,
                "matrix_user_id": status.get("matrixUserID"),
                "role": team_members[resource_name],
                "model": spec.get("model"),
                "runtime": spec.get("runtime"),
                "image": spec.get("image") or None,
                "required_skills": sorted(spec.get("skills", [])),
            }
        )
        pod_entries.append(
            {
                "namespace": namespace,
                "name": pod.get("metadata", {}).get("name"),
                "uid": pod.get("metadata", {}).get("uid"),
                "worker_resource_name": resource_name,
                "container_name": "worker",
                "image": image,
                "image_id": image_id,
            }
        )

    skill_pod = pod_by_worker.get(skill_worker)
    if skill_pod is None:
        raise FreezeError("Skill Worker has no runtime Pod")
    skill_digest = _runtime_skill_digest(
        context,
        namespace,
        skill_pod["metadata"]["name"],
        skill_worker,
        skill_name,
    )

    issued_at_ms = int(time.time() * 1000)
    nonce = secrets.token_hex(32)
    run_suffix = hashlib.sha256(nonce.encode()).hexdigest()[:12]
    return {
        "schema_version": RUN_SCHEMA,
        "run_id": f"run:orgrebase:live:{issued_at_ms}:{run_suffix}",
        "nonce": nonce,
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": issued_at_ms + ttl_seconds * 1000,
        "agentteams": qualified_source,
        "team": {
            "namespace": namespace,
            "name": team.get("metadata", {}).get("name"),
            "uid": team.get("metadata", {}).get("uid"),
            "generation": team.get("metadata", {}).get("generation"),
            "room_id": team.get("status", {}).get("teamRoomID"),
        },
        "workers": sorted(worker_entries, key=lambda item: item["worker_name"]),
        "runtime_pods": sorted(pod_entries, key=lambda item: item["name"]),
        "skill": {
            "name": skill_name,
            "assigned_worker": skill_worker,
            "digest": skill_digest,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="kind-orgrebase-agentteams")
    parser.add_argument("--namespace", default="orgrebase-agentteams")
    parser.add_argument("--team", default="orgrebase-change-team")
    parser.add_argument("--skill", default="enterprise-launch-readiness")
    parser.add_argument("--skill-worker", default="skill-curator")
    parser.add_argument("--ttl-seconds", type=int, default=7200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        envelope = freeze(
            context=args.context,
            namespace=args.namespace,
            team_name=args.team,
            skill_name=args.skill,
            skill_worker=args.skill_worker,
            ttl_seconds=args.ttl_seconds,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(envelope, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (FreezeError, FileExistsError) as error:
        raise SystemExit(f"NOT_FROZEN: {error}") from error
    print(
        json.dumps(
            {
                "status": "FROZEN",
                "run_id": envelope["run_id"],
                "output": str(args.output),
                "worker_count": len(envelope["workers"]),
                "pod_count": len(envelope["runtime_pods"]),
                "expires_at_ms": envelope["expires_at_ms"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
