"""Build an AgentTeams artifact manifest from exact frozen candidate bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
MANIFEST_SCHEMA = "orgrebase.agentteams-artifact-manifest.v1"
CANDIDATE_SCHEMA = "orgrebase.candidate-result.v1"


class ManifestError(RuntimeError):
    """Candidate or Skill bytes do not match the frozen run."""


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"expected JSON object: {path}")
    return value


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def build_manifest(
    *,
    envelope: dict[str, Any],
    artifact_root: Path,
    skill_path: Path,
    skill_event_id: str,
) -> dict[str, Any]:
    if envelope.get("schema_version") != RUN_SCHEMA:
        raise ManifestError("run envelope schema mismatch")
    workers = {
        item.get("worker_name"): item
        for item in envelope.get("workers", [])
        if isinstance(item, dict) and isinstance(item.get("worker_name"), str)
    }
    if len(workers) != 5:
        raise ManifestError("run envelope does not bind exactly five Workers")
    artifacts: list[dict[str, Any]] = []
    for worker_name, worker in sorted(workers.items()):
        if worker.get("role") == "team_leader":
            continue
        relative = Path(worker_name) / "result.json"
        path = artifact_root / relative
        candidate = _object(path)
        if (
            candidate.get("schema_version") != CANDIDATE_SCHEMA
            or candidate.get("run_id") != envelope.get("run_id")
            or candidate.get("nonce") != envelope.get("nonce")
            or candidate.get("worker_name") != worker_name
            or candidate.get("candidate_only") is not True
        ):
            raise ManifestError(f"candidate is not bound to the frozen run: {worker_name}")
        artifacts.append(
            {
                "ref": f"result:{worker_name}",
                "path": relative.as_posix(),
                "digest": _digest(path),
                "schema_version": CANDIDATE_SCHEMA,
                "producer_worker": worker_name,
                "producer_matrix_user_id": worker["matrix_user_id"],
                "candidate_only": True,
            }
        )

    skill = envelope.get("skill")
    if not isinstance(skill, dict):
        raise ManifestError("run envelope has no Skill binding")
    full_skill_path = artifact_root / skill_path
    if _digest(full_skill_path) != skill.get("digest"):
        raise ManifestError("runtime Skill bytes differ from the frozen run")
    assigned_worker = workers.get(skill.get("assigned_worker"))
    if assigned_worker is None:
        raise ManifestError("Skill Worker is not bound to the frozen run")
    if not skill_event_id.startswith("$"):
        raise ManifestError("invalid Skill-loaded Matrix event id")
    return {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": envelope["run_id"],
        "nonce": envelope["nonce"],
        "artifacts": artifacts,
        "skill_runtime_evidence": {
            "skill_name": skill["name"],
            "assigned_worker": skill["assigned_worker"],
            "worker_matrix_user_id": assigned_worker["matrix_user_id"],
            "skill_path": skill_path.as_posix(),
            "skill_digest": skill["digest"],
            "loaded_event_id": skill_event_id,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-envelope", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--skill-path", type=Path, required=True)
    parser.add_argument("--skill-event-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_manifest(
            envelope=_object(args.run_envelope),
            artifact_root=args.artifact_root,
            skill_path=args.skill_path,
            skill_event_id=args.skill_event_id,
        )
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except ManifestError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {"artifacts": len(manifest["artifacts"]), "output": str(args.output)}
        )
    )


if __name__ == "__main__":
    main()
