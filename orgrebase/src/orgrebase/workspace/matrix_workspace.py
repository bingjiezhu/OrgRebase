"""Publish a minimal, same-run Workspace observation without changing business state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from orgrebase.auth import current_authorization
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.matrix_observation import (
    MatrixObservationError,
    load_matrix_config,
    public_observation,
    publish_observation,
    record_observation_failure,
)


def _configuration() -> tuple[Path, Path] | None:
    configured = os.environ.get("ORGREBASE_MATRIX_OBSERVER_CONFIG", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser().absolute()
    return path, path.parent / "observations"


def _workspace_key(workspace: Any) -> str:
    organization = quote(workspace.profile.organization_id, safe=":@._-")
    workspace_id = quote(workspace.store.workspace_id, safe=":@._-")
    return f"{organization}/{workspace_id}"


def _source_digest(state: dict[str, Any], formation: dict[str, Any]) -> str:
    return sha256_digest(
        {
            "summary_digest": formation["summary_digest"],
            "quote_digest": (state.get("quote") or {}).get("digest"),
            "stage": state["stage"],
            "business_complete": bool(state.get("business_complete")),
        }
    )


def observation_view(workspace: Any, state: dict[str, Any]) -> dict[str, Any]:
    configuration = _configuration()
    if configuration is None:
        return {"status": "NOT_CONFIGURED"}
    formation = state.get("agentteams_operations", {}).get("formation_taskflow", {})
    run_id = formation.get("run_id")
    if formation.get("native_taskflow_observed") is not True or not run_id:
        return {"status": "WAITING_FOR_RUN"}
    observation = public_observation(
        configuration[1],
        workspace_id=_workspace_key(workspace),
        run_id=run_id,
    )
    if observation.get("status") == "OBSERVED" and observation.get("source_digest") != _source_digest(
        state, formation
    ):
        return {
            key: "STALE" if key == "status" else value
            for key, value in observation.items()
            if key != "element_room_url"
        }
    return observation


def publish_workspace_observation(
    workspace: Any,
    *,
    actor_id: str,
    expected_run_id: str,
) -> dict[str, Any]:
    if actor_id != workspace.profile.default_task.actor_id:
        raise AuthorizationError("MATRIX_OBSERVATION_TASK_ACTOR_REQUIRED")
    configuration = _configuration()
    if configuration is None:
        raise MatrixObservationError("MATRIX_OBSERVATION_NOT_CONFIGURED")
    state = workspace.state()
    formation = state.get("agentteams_operations", {}).get("formation_taskflow", {})
    run_id = formation.get("run_id")
    if formation.get("native_taskflow_observed") is not True or not run_id:
        raise IntegrityError("MATRIX_OBSERVATION_VERIFIED_RUN_REQUIRED")
    if run_id != expected_run_id:
        raise IntegrityError("MATRIX_OBSERVATION_RUN_CHANGED")
    authorize = current_authorization()
    if authorize is not None:
        authorize()
    evidence = state["competition_evidence"]
    collaboration = evidence["agent_collaboration"]
    plan = collaboration["orchestration_plan"]
    quote = state.get("quote") or {}
    snapshot = {
        "native_project_id": plan["project_id"],
        "source_digest": _source_digest(state, formation),
        "action_count": formation["action_count"],
        "task_summaries": [
            {"task_id": item["task_id"], "domain": item["authority_domain"], "status": item["status"]}
            for item in collaboration["agent_runs"]
        ],
        "quote_revision": int(str(quote["version"]).removeprefix("v")) if quote else None,
        "quote_ref": f"{quote['id']}@{quote['version']}" if quote else None,
        "phase": "COMPLETED" if state.get("business_complete") else state["stage"],
    }
    workspace_key = _workspace_key(workspace)
    previous = public_observation(configuration[1], workspace_id=workspace_key, run_id=run_id)
    try:
        config = load_matrix_config(configuration[0])
        # Only deployment configuration supplies destinations; browser input supplies no URLs.
        workspace_url = os.environ.get("ORGREBASE_PUBLIC_WORKSPACE_URL", "").strip()
        if not workspace_url:
            raise MatrixObservationError("MATRIX_OBSERVATION_WORKSPACE_URL_REQUIRED")
        return publish_observation(
            config,
            configuration[1],
            workspace_id=workspace_key,
            run_id=run_id,
            snapshot=snapshot,
            workspace_url=workspace_url,
            authorize=authorize,
        )
    except MatrixObservationError as exc:
        record_observation_failure(
            configuration[1],
            workspace_id=workspace_key,
            run_id=run_id,
            expected_receipt_digest=previous.get("digest"),
            error_code=str(exc),
        )
        raise
