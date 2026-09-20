"""Bind paused work to the implementation that produced its review evidence."""

from __future__ import annotations

import hashlib
import json
import platform
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from orgrebase.digest import canonical_json, sha256_digest

_MEDIA = "application/vnd.orgrebase.runtime-compatibility+json"
_MODULES = (
    "auth.py", "clock.py", "fixture.py", "skills.py", "change_projection.py",
    "domain.py", "change_events.py", "impact.py", "certificates.py", "workflow.py", "digest.py", "context.py",
    "runtime_contracts.py", "workspace/rebuild.py", "workspace/execution.py", "workspace/graph.py",
    "workspace/context.py", "workspace/admission.py", "workspace/models.py", "workspace/templates.py", "workspace/pricing.py",
    "workspace/advisory.py", "workspace/source_bindings.py", "workspace/dataverse.py",
    "workspace/model_provider.py", "workspace/model_observations.py", "workspace/openai_responses.py", "workspace/ports.py",
    "workspace/model_budget.py", "workspace/openai_http_worker.py",
    "workspace/enterprise_binding.py", "workspace/owner_change.py", "workspace/source_worker.py",
    "workspace/transport.py", "workspace/preview_execution.py",
    "workspace/change_agentteams.py", "workspace/change_recovery.py", "workspace/native_taskflow.py", "agentteams_source.py",
    "workspace/runtime_revision.py",
    "workspace/source_readmission.py", "workspace/read_dependencies.py", "workspace/approval_authority.py",
    "workspace/service.py", "workspace/formation.py", "workspace/formation_integrity.py",
    "workspace/changes.py", "workspace/change_proposals.py", "store.py", "database.py", "local_storage.py",
)


@lru_cache(maxsize=1)
def _revision_bytes() -> str:
    package = Path(__file__).resolve().parents[1]
    files = {name: "sha256:" + hashlib.sha256((package / name).read_bytes()).hexdigest() for name in _MODULES}
    body = {"schema_version": "orgrebase.runtime-compatibility.v1",
            "workflow_contract": "orgrebase.quote-rebase.atomic-source-set.v2",
            "content_digest_scheme": "orgrebase.python-json.v1",
            "handler_contract": "orgrebase.quote-handler.delta-set.v2",
            "implementation": files,
            "python_version": platform.python_version(),
            "dependencies": {name: metadata.version(name) for name in ("pydantic", "rfc8785", "sqlalchemy", "psycopg", "httpx2")}}
    return canonical_json({**body, "revision_digest": sha256_digest(body)})


def current_revision() -> dict[str, Any]:
    return json.loads(_revision_bytes())


def workspace_revision(workspace: Any) -> dict[str, Any]:
    """Bind the selected candidate protocol and parameters as well as code."""
    current = current_revision()
    adapter = getattr(workspace, "advisory_factory", None)
    configuration = getattr(adapter, "configuration_binding", None)
    if configuration is None:
        return current
    body = {key: value for key, value in current.items() if key != "revision_digest"}
    body["advisory_configuration"] = configuration
    return {**body, "revision_digest": sha256_digest(body)}


def _key(event_id: str, preview_digest: str) -> str:
    return "workspace-runtime-binding:" + sha256_digest({"event_id": event_id, "preview_digest": preview_digest})[7:]


def bind_preview_runtime(workspace: Any, connection: Any, event_id: str, preview_digest: str) -> str:
    body = {"schema_version": "orgrebase.paused-work-runtime-binding.v1", "event_id": event_id,
            "preview_digest": preview_digest, "workspace_id": workspace.store.workspace_id,
            "tenant_id": workspace.store.tenant_id, "run_id": workspace.effective_workflow_run_id,
            "profile_digest": workspace.profile_digest, "runtime": workspace_revision(workspace)}
    return workspace.store.save_artifact(connection, _key(event_id, preview_digest), _MEDIA, body)


def preview_runtime_status(workspace: Any, event_id: str, preview_digest: str, *, historical: bool = False) -> dict[str, Any]:
    current = None if historical else workspace_revision(workspace)
    current_digest = current["revision_digest"] if current is not None else None
    try:
        saved = workspace.store.load_artifact(_key(event_id, preview_digest), _MEDIA).payload
    except KeyError:
        return {"decision": "READ_ONLY_HISTORICAL" if historical else "REPLAN_REQUIRED", "reason": "RUNTIME_BINDING_MISSING",
                "current_revision": current_digest, "previous_revision": None}
    expected_scope = {"schema_version": "orgrebase.paused-work-runtime-binding.v1", "event_id": event_id,
                      "preview_digest": preview_digest, "workspace_id": workspace.store.workspace_id,
                      "tenant_id": workspace.store.tenant_id, "run_id": workspace.effective_workflow_run_id,
                      "profile_digest": workspace.profile_digest}
    if any(saved.get(key) != value for key, value in expected_scope.items()):
        return {"decision": "READ_ONLY_HISTORICAL" if historical else "REPLAN_REQUIRED", "reason": "RUNTIME_BINDING_SCOPE_CHANGED",
                "current_revision": current_digest,
                "previous_revision": saved.get("runtime", {}).get("revision_digest")}
    if historical:
        return {"decision": "READ_ONLY_HISTORICAL", "reason": "COMMITTED_RUNTIME_BINDING",
                "current_revision": None, "previous_revision": saved.get("runtime", {}).get("revision_digest")}
    matches = saved.get("runtime") == current
    return {"decision": "CONTINUE" if matches else "REPLAN_REQUIRED",
            "reason": "EXACT_RUNTIME_MATCH" if matches else "RUNTIME_IMPLEMENTATION_CHANGED",
            "current_revision": current_digest,
            "previous_revision": saved.get("runtime", {}).get("revision_digest")}


def require_preview_runtime(workspace: Any, event_id: str, preview_digest: str) -> None:
    status = preview_runtime_status(workspace, event_id, preview_digest)
    if status["decision"] != "CONTINUE":
        raise RuntimeError("WORKSPACE_RUNTIME_REPLAN_REQUIRED:" + status["reason"])


def effect_runtime_revision() -> dict[str, Any]:
    """First dispatch uses this exact effect handler; reconciliation stays available."""
    package = Path(__file__).resolve().parents[1]
    body = {"schema_version": "orgrebase.effect-runtime-compatibility.v1",
            "action": "dataverse.quote.update_draft_metadata",
            "receipt_contract": "orgrebase.dataverse-standard-table-atomic-receipt.v1",
            "python_version": platform.python_version(),
            "dependencies": {name: metadata.version(name) for name in ("pydantic", "httpx2")},
            "implementation": {name: "sha256:" + hashlib.sha256((package / name).read_bytes()).hexdigest()
                               for name in ("commit_gateway.py", "effect_identity.py", "dataverse_target.py", "workspace/effects.py",
                                            "workspace/effect_worker.py", "workspace/enterprise_binding.py")}}
    return {**body, "revision_digest": sha256_digest(body)}
