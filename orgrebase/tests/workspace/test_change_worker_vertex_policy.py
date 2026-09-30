from __future__ import annotations

import json
import time
import urllib.request
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from orgrebase.auth import Principal, request_authorization, request_principal
from orgrebase.workspace.advisory import (
    WorkspaceApplyAdvisoryVerifier,
    WorkspaceChangeAdvisoryAdapter,
)
from orgrebase.workspace.change_budget import DeploymentDispatchBudget
from orgrebase.workspace.change_operations import (
    ChangePreparationConfig,
    prepare_pending_changes,
    run_change_preparation,
)
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_vertex_candidate_contract import NOW, _provider, _Response


def _dispatch_budget() -> dict[str, object]:
    return {
        "schema_version": "orgrebase.deployment-dispatch-budget.v1",
        "deployment_scope": "change-worker:example",
        "period_seconds": 3600,
        "max_reserved_microusd": 1_000_000,
        "max_reserved_calls": 12,
        "max_dispatches_per_period": 4,
        "max_queue_reservations_per_period": 4,
        "max_active_attempts": 1,
    }


def _worker_config(**changes: object) -> dict[str, object]:
    return {
        "schema_version": "orgrebase.change-preparation.v2",
        "workspace_id": "default",
        "access_token_variable": "TEST_CHANGE_WORKER_TOKEN",
        "enabled": True,
        "provider_mode": "vertex-ai",
        "dispatch_budget": _dispatch_budget(),
        **changes,
    }


def _price_contract() -> dict[str, object]:
    return {
        "schema_version": "orgrebase.model-budget.v1",
        "model_id": VERTEX_CANDIDATE_MODEL_ID,
        "context_window_tokens": 1_000_000,
        "input_usd_per_million_ceiling": "0.00000001",
        "output_usd_per_million_ceiling": "0.00000001",
        "max_preview_usd": "100.00000000",
        "price_source_ref": "test://worker-price-ceiling",
        "valid_until": "2030-01-01T00:00:00Z",
    }


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"schema_version": "orgrebase.change-preparation.v1"}, "VERTEX_REQUIRES_V2"),
        ({"dispatch_budget": None}, "VERTEX_DISPATCH_BUDGET_REQUIRED"),
        ({"dispatch_budget": {**_dispatch_budget(), "max_reserved_calls": 0}}, "VERTEX_DISPATCH_BUDGET_INVALID"),
    ],
)
def test_vertex_worker_config_requires_versioned_positive_deployment_budget(changes, error):
    with pytest.raises(ValidationError, match=error):
        ChangePreparationConfig.model_validate(_worker_config(**changes))


def test_vertex_worker_requires_exact_selected_provider_before_workspace_open(tmp_path, monkeypatch):
    config = tmp_path / "worker.json"
    config.write_text(json.dumps(_worker_config()))
    monkeypatch.setenv("TEST_CHANGE_WORKER_TOKEN", "private-test-token")
    monkeypatch.delenv("ORGREBASE_CHANGE_MODEL_PROVIDER", raising=False)

    class Settings:
        mode = "production"

        def for_workspace(self, _workspace_id):
            return self

    monkeypatch.setattr(
        "orgrebase.runtime_config.DeploymentSettings.from_environment",
        classmethod(lambda cls: Settings()),
    )
    with pytest.raises(ValueError, match="CHANGE_PREPARATION_PROVIDER_MISMATCH"):
        run_change_preparation(config)


def test_vertex_worker_binds_one_private_policy_and_cleans_authorization(
    tmp_path, monkeypatch,
):
    config = tmp_path / "worker.json"
    config.write_text(json.dumps(_worker_config()))
    price = tmp_path / "price.json"
    price.write_text(json.dumps(_price_contract()))
    monkeypatch.setenv("TEST_CHANGE_WORKER_TOKEN", "private-test-token")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "vertex-ai")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_BUDGET_PATH", str(price))

    class Settings:
        mode = "production"
        identity = SimpleNamespace(tenant_id="tenant:worker")

        def for_workspace(self, workspace_id):
            assert workspace_id == "default"
            return self

        def authorize_workspace(self, subject):
            assert subject == "worker-subject"

    principal = SimpleNamespace(
        issuer="issuer", subject="worker-subject", tenant_id="tenant:worker",
        actor_id="actor:worker",
    )
    workspace = SimpleNamespace(
        advisory_factory=SimpleNamespace(uses_vertex_v3=True),
        close=lambda: None,
    )
    monkeypatch.setattr(
        "orgrebase.runtime_config.DeploymentSettings.from_environment",
        classmethod(lambda cls: Settings()),
    )
    monkeypatch.setattr("orgrebase.workspace.change_operations.JWTAuthenticator", lambda _settings:
                        SimpleNamespace(authenticate=lambda token: principal))
    monkeypatch.setattr("orgrebase.workspace.change_operations.authorize", lambda *_args: None)
    monkeypatch.setattr("orgrebase.runtime_config.open_workspace", lambda _settings: workspace)
    observed = []

    def prepare(_workspace, *, max_changes, dry_run, dispatch_policy):
        assert _workspace is workspace
        assert max_changes == 20 and not dry_run
        assert request_principal.get() is principal
        assert request_authorization.get() is not None
        observed.append(dispatch_policy.digest)
        return {"mode": "VERTEX_V3_CANDIDATE_ONLY", "records": []}

    monkeypatch.setattr("orgrebase.workspace.change_operations.prepare_pending_changes", prepare)
    previous_principal = request_principal.get()
    previous_authorization = request_authorization.get()
    result = run_change_preparation(config)
    assert result["mode"] == "VERTEX_V3_CANDIDATE_ONLY"
    assert result["workspace_id"] == "default"
    assert len(observed) == 1
    assert request_principal.get() is previous_principal
    assert request_authorization.get() is previous_authorization


def _install_fake_vertex_advisory(workspace, monkeypatch):
    observed_requests = []

    def send(request: urllib.request.Request, *, timeout: float) -> _Response:
        assert timeout > 0
        body = json.loads(request.data)
        user_input = json.loads(body["contents"][0]["parts"][0]["text"])
        binding = user_input["binding"]
        observed_requests.append(binding["domain_id"])
        candidate = {
            "domain_id": binding["domain_id"],
            "object_ids": binding["object_ids"],
            "source_refs": [item["ref"] for item in user_input["input_projections"]],
            "explanation": "Checked candidate only; no business action was taken.",
        }
        return _Response({
            "responseId": f"worker-vertex-{len(observed_requests)}",
            "modelVersion": VERTEX_CANDIDATE_MODEL_ID,
            "createTime": NOW,
            "candidates": [{
                "content": {"parts": [{"text": json.dumps(candidate, separators=(",", ":"))}]},
                "finishReason": "STOP",
            }],
        })

    monkeypatch.setattr(urllib.request, "urlopen", send)
    provider = _provider()
    adapter = WorkspaceChangeAdvisoryAdapter(
        quote_object_id=workspace.quote_object_id,
        provider=provider,
        tenant_id=workspace.profile.organization_id,
        workspace_id=workspace.store.workspace_id,
        model_id=VERTEX_CANDIDATE_MODEL_ID,
    )
    workspace.advisory_factory = adapter
    workspace.advisory_verifier = WorkspaceApplyAdvisoryVerifier(
        adapter, clock=lambda: workspace.clock.now()
    )
    return observed_requests


def test_candidate_worker_reuses_preview_identity_with_vertex_v3_protocol(
    workspace, monkeypatch,
):
    """An HTTP substitute exercises the real preview, budget and verifier path."""
    observed_requests = _install_fake_vertex_advisory(workspace, monkeypatch)
    submit_change(workspace, command(workspace))
    before = workspace.current_quote().digest
    first = prepare_pending_changes(
        workspace, max_changes=1,
        dispatch_policy=DeploymentDispatchBudget.model_validate(_dispatch_budget()),
    )
    assert first["schema_version"] == "orgrebase.change-preparation-run.v2"
    assert first["mode"] == "VERTEX_V3_CANDIDATE_ONLY"
    assert first["records"][0]["result"] == "CANDIDATE_PREPARED"
    assert observed_requests
    assert workspace._preview_record("edit-1") is not None
    assert workspace._approval_record("edit-1") is None
    assert workspace.current_quote().digest == before
    assert prepare_pending_changes(workspace, max_changes=1)["records"] == []


def test_versioned_worker_command_uses_authenticated_vertex_preview_once(
    workspace, tmp_path, monkeypatch,
):
    """The CLI orchestration retains one preview identity across a fresh pass."""
    observed_requests = _install_fake_vertex_advisory(workspace, monkeypatch)
    submit_change(workspace, command(workspace))
    before = workspace.current_quote().digest
    config = tmp_path / "worker.json"
    config.write_text(json.dumps(_worker_config()))
    price = tmp_path / "price.json"
    price.write_text(json.dumps(_price_contract()))
    monkeypatch.setenv("TEST_CHANGE_WORKER_TOKEN", "private-test-token")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "vertex-ai")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_BUDGET_PATH", str(price))
    principal = Principal(
        issuer="https://issuer.example", subject="worker-subject",
        tenant_id=workspace.profile.organization_id, actor_id="actor:worker",
        roles=frozenset({"administrator"}), expires_at=int(time.time()) + 3600,
    )

    class Settings:
        mode = "production"
        identity = SimpleNamespace(tenant_id=workspace.profile.organization_id)

        def for_workspace(self, workspace_id):
            assert workspace_id == workspace.store.workspace_id
            return self

        def authorize_workspace(self, subject):
            assert subject == principal.subject

    class WorkspaceHandle:
        def __getattr__(self, name):
            return getattr(workspace, name)

        def close(self):
            pass

    monkeypatch.setattr(
        "orgrebase.runtime_config.DeploymentSettings.from_environment",
        classmethod(lambda cls: Settings()),
    )
    monkeypatch.setattr("orgrebase.workspace.change_operations.JWTAuthenticator", lambda _identity:
                        SimpleNamespace(authenticate=lambda _token: principal))
    monkeypatch.setattr("orgrebase.runtime_config.open_workspace", lambda _settings: WorkspaceHandle())
    first = run_change_preparation(config)
    second = run_change_preparation(config)
    assert first["schema_version"] == "orgrebase.change-preparation-run.v2"
    assert first["mode"] == "VERTEX_V3_CANDIDATE_ONLY"
    assert first["records"][0]["result"] == "CANDIDATE_PREPARED"
    assert second["records"] == []
    assert observed_requests
    assert workspace.current_quote().digest == before
    assert workspace._approval_record("edit-1") is None
    assert request_principal.get() is None
    assert request_authorization.get() is None
