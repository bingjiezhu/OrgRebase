"""FastAPI surface and zero-build-dependency Change Console."""

from __future__ import annotations

import json
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from orgrebase import __version__
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError
from orgrebase.service import OrgRebaseService
from orgrebase.workspace.service import WorkspaceService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_CONSOLE_DIR = Path(__file__).resolve().parent / "static"
CONSOLE_DIR = PACKAGED_CONSOLE_DIR if PACKAGED_CONSOLE_DIR.is_dir() else PROJECT_ROOT / "demo" / "console"
SOURCE_RELEASE_FACTS_PATH = PROJECT_ROOT / "evidence" / "release-facts.json"
PACKAGED_RELEASE_FACTS_PATH = Path(__file__).resolve().parent / "_assets/evidence/release-facts.json"
RELEASE_FACTS_PATH = (
    SOURCE_RELEASE_FACTS_PATH
    if SOURCE_RELEASE_FACTS_PATH.is_file()
    else PACKAGED_RELEASE_FACTS_PATH
)


def create_app(
    service: OrgRebaseService | None = None,
    workspace_service: WorkspaceService | None = None,
) -> FastAPI:
    runtime = service or OrgRebaseService()
    workspace_runtime = workspace_service or WorkspaceService()
    application = FastAPI(
        title="OrgRebase",
        version=__version__,
        description="Deterministic enterprise change-consistency runtime",
    )
    application.state.service = runtime
    application.state.workspace_service = workspace_runtime
    application.mount("/assets", StaticFiles(directory=CONSOLE_DIR), name="assets")

    @application.get("/", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(CONSOLE_DIR / "index.html")

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "orgrebase",
            "version": __version__,
            "profile": "LOCAL_DETERMINISTIC",
        }

    @application.get("/api/demo/state")
    def state() -> dict[str, Any]:
        return runtime.current_view()

    @application.get("/api/release-facts")
    def release_facts() -> dict[str, Any]:
        if not RELEASE_FACTS_PATH.is_file():
            raise HTTPException(status_code=503, detail="RELEASE_FACTS_UNAVAILABLE")
        value = json.loads(RELEASE_FACTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise HTTPException(status_code=503, detail="RELEASE_FACTS_INVALID")
        return value

    @application.post("/api/demo/reset")
    def reset() -> dict[str, Any]:
        return runtime.reset()

    @application.post("/api/demo/preview")
    def preview() -> dict[str, Any]:
        return runtime.preview()

    @application.post("/api/demo/apply")
    def apply() -> dict[str, Any]:
        try:
            return runtime.apply()
        except FreshnessError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc

    @application.post("/api/demo/run")
    def run() -> dict[str, Any]:
        return runtime.run_demo()

    @application.post("/api/demo/workspace/quote-to-rebase")
    def workspace_quote_to_rebase() -> dict[str, Any]:
        from orgrebase.workspace.service import WorkspaceService

        with tempfile.TemporaryDirectory(prefix="orgrebase-workspace-api-") as directory:
            workspace = WorkspaceService(store_path=Path(directory) / "workspace.db")
            try:
                return workspace.run_local_loop()
            finally:
                # Persistent local-loop execution closes its original handle before
                # restart; in-memory/error paths still need explicit cleanup.
                with suppress(Exception):
                    workspace.close()

    @application.get("/api/demo/workspace/agentteams-status")
    def workspace_agentteams_status() -> dict[str, Any]:
        from orgrebase.workspace.transport import agentteams_status

        return agentteams_status()

    @application.get("/api/workspace/state")
    def workspace_state() -> dict[str, Any]:
        return workspace_runtime.state()

    @application.post("/api/workspace/form")
    def workspace_form(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from orgrebase.workspace.models import TaskRequest

        try:
            request = TaskRequest.model_validate(payload) if payload else None
            receipt = workspace_runtime.form_quote(request)
            return {"receipt": receipt, "state": workspace_runtime.state()}
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": getattr(exc, "code", str(exc)), "message": str(exc)},
            ) from exc

    @application.post("/api/workspace/preview/{change_kind}")
    def workspace_preview(change_kind: str) -> dict[str, Any]:
        try:
            return {"bundle": workspace_runtime.preview_change(change_kind)}
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": getattr(exc, "code", str(exc)), "message": str(exc)},
            ) from exc

    @application.post("/api/workspace/apply/{change_kind}")
    def workspace_apply(change_kind: str) -> dict[str, Any]:
        try:
            return workspace_runtime.apply_change(change_kind)
        except (FreshnessError, IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": getattr(exc, "code", str(exc)), "message": str(exc)},
            ) from exc

    @application.post("/api/workspace/run")
    def workspace_run() -> dict[str, Any]:
        try:
            return workspace_runtime.run_local_loop()
        except (FreshnessError, IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": getattr(exc, "code", str(exc)), "message": str(exc)},
            ) from exc

    @application.post("/api/demo/conflict")
    def conflict_drill() -> dict[str, Any]:
        return runtime.conflict_drill()

    @application.post("/api/demo/failure")
    def failure_drill() -> dict[str, Any]:
        return runtime.freshness_failure_drill()

    @application.post("/api/demo/rollback")
    def rollback() -> dict[str, Any]:
        try:
            return runtime.rollback()
        except FreshnessError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc

    @application.get("/api/demo/observability")
    def observability() -> dict[str, Any]:
        return runtime.observability()

    @application.get("/api/tools/v1/dependency-evidence/contract")
    def dependency_tool_contract() -> dict[str, Any]:
        return runtime.tool_contract()

    @application.get("/api/tools/v1/git-artifact/contract")
    def git_tool_contract() -> dict[str, Any]:
        return runtime.git_tool_contract()

    @application.post("/api/tools/v1/dependency-evidence")
    def dependency_tool(
        payload: dict[str, Any],
        x_orgrebase_actor: str = Header(alias="X-OrgRebase-Actor"),
    ) -> dict[str, Any]:
        raw_targets = payload.get("target_ids", ())
        if not isinstance(raw_targets, list):
            raise HTTPException(status_code=400, detail="INVALID_TARGET")
        try:
            return runtime.invoke_dependency_tool(
                actor_id=x_orgrebase_actor,
                target_ids=tuple(raw_targets),
                graph_revision=str(payload.get("graph_revision", "")),
                idempotency_key=str(payload.get("idempotency_key", "")),
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail={"code": exc.code, "message": str(exc)}) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)}) from exc

    @application.get("/api/demo/no-semantic-delta")
    def no_semantic_delta() -> dict[str, Any]:
        return runtime.no_semantic_delta()

    @application.post("/api/receipts/verify")
    def verify_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_receipt(payload)
        except IntegrityError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    @application.post("/api/impact-certificates/verify")
    def verify_impact_certificate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_impact_certificate(payload)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @application.post("/api/minimal-rebase-certificates/verify")
    def verify_minimal_rebase_certificate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_minimal_rebase_certificate(payload)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @application.post("/api/receipts/rollback/verify")
    def verify_rollback_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_rollback_receipt(payload)
        except IntegrityError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    return application


app = create_app()
