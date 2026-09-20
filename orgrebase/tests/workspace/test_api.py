from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import sleep

import pytest
from fastapi.testclient import TestClient

import orgrebase.api as api_module
from orgrebase.api import create_app
from orgrebase.digest import sha256_digest
from orgrebase.service import OrgRebaseService
from orgrebase.workspace.service import WorkspaceService


def _write_golden_status_pack(root: Path, *, run_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest_body = {
        "schema_version": "orgrebase.golden-pilot-evidence-manifest.v1",
        "status": "PASS",
        "run_id": run_id,
        "files": {
            "entry_count": 101,
            "pack_digest": "sha256:" + "a" * 64,
            "entries": [],
        },
    }
    manifest = {**manifest_body, "digest": sha256_digest(manifest_body)}
    verification = {
        "schema_version": "orgrebase.golden-pilot-verification.v1",
        "status": "PASS",
        "failures": [],
        "run_id": run_id,
        "entry_count": 101,
        "pack_digest": "sha256:" + "a" * 64,
        "product_imports": 0,
        "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "verification.json").write_text(
        json.dumps(verification), encoding="utf-8"
    )


def test_api_rejects_untrusted_host_and_sanitizes_internal_errors() -> None:
    with TestClient(create_app()) as client:
        rejected = client.get("/api/health", headers={"Host": "attacker.example"})

    assert rejected.status_code == 400
    host_path = "/" + "Us" + "ers/private/customer.stderr"
    detail = api_module._workspace_error(
        RuntimeError(f"OAC_CLI_FAILED:validate:{host_path}")
    )
    assert detail == {"code": "OAC_CLI_FAILED", "message": "OAC_CLI_FAILED"}
    assert host_path not in json.dumps(detail)


def test_golden_evidence_status_requires_exact_current_run_binding(tmp_path: Path) -> None:
    frozen_run = "run:golden-competition:exact"
    _write_golden_status_pack(tmp_path, run_id=frozen_run)

    current = api_module._golden_evidence_status(
        tmp_path,
        current_run_id=frozen_run,
    )
    stale = api_module._golden_evidence_status(
        tmp_path,
        current_run_id="run:golden-competition:other",
    )

    assert current == {
        "schema_version": "orgrebase.golden-evidence-status.v1",
        "status": "PASS",
        "current_run_id": frozen_run,
        "frozen_run_id": frozen_run,
        "same_run": True,
        "entry_count": 101,
        "pack_digest": "sha256:" + "a" * 64,
        "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
        "claim_boundary": (
            "READ_ONLY_FROZEN_EVIDENCE_STATUS_NOT_CANONICAL_BUSINESS_TRUTH"
        ),
    }
    assert stale["status"] == "STALE"
    assert stale["same_run"] is False
    assert stale["frozen_run_id"] == frozen_run


def test_golden_evidence_status_fails_closed_on_manifest_drift(tmp_path: Path) -> None:
    _write_golden_status_pack(tmp_path, run_id="run:golden-competition:exact")
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["entry_count"] = 999
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    status = api_module._golden_evidence_status(
        tmp_path,
        current_run_id="run:golden-competition:exact",
    )
    assert status["status"] == "FAIL"
    assert status["same_run"] is False
    assert status["entry_count"] is None


def test_golden_status_endpoint_uses_the_explicit_active_runtime_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_run = "run:golden-competition:runtime-mirror"
    mirror = tmp_path / "runtime-mirror"
    _write_golden_status_pack(mirror, run_id=frozen_run)
    monkeypatch.setenv("ORGREBASE_GOLDEN_EVIDENCE_ROOT", str(mirror))
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite3",
        workflow_run_id=frozen_run,
    )

    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/golden-evidence/status")

    workspace.close()
    assert response.status_code == 200
    assert response.json()["status"] == "PASS"
    assert response.json()["same_run"] is True
    assert response.json()["entry_count"] == 101


def test_operating_model_endpoint_is_declared_read_only_and_run_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = (
        api_module.PROJECT_ROOT
        / "benchmark"
        / "quote-value-v0.1"
        / "public"
        / "current-process-baseline.json"
    )
    isolated_baseline = tmp_path / "current-process-baseline.json"
    isolated_baseline.write_bytes(baseline.read_bytes())
    monkeypatch.setattr(
        api_module,
        "runtime_asset_path",
        lambda relative: isolated_baseline,
    )
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite3")
    before = workspace.state()

    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/operating-model")

    after = workspace.state()
    workspace.close()
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "orgrebase.workspace-operating-model-view.v1"
    assert payload["status"] == "PASS"
    assert payload["source"] == "DECLARED_ENTERPRISE_QUOTE_OPERATING_MODEL"
    assert payload["read_model_target_writes"] == 0
    assert payload["claim_boundary"] == (
        "DECLARED_REFERENCE_PROCESS_AND_DESIGN_TARGETS_NOT_CURRENT_RUN_RESULTS"
    )
    value = payload["value_and_responsibility"]
    assert value["primary_user"] == "Enterprise Quote Operator"
    assert value["primary_deliverable"] == "Enterprise Quote"
    assert value["process_status"] == "NOT_RUN"
    assert tuple(step["id"] for step in value["process_steps"]) == (
        api_module.OPERATING_MODEL_STEP_IDS
    )
    assert all(step["responsible"] for step in value["process_steps"])
    assert all(step["accountable"] for step in value["process_steps"])
    assert all(
        step["named_connector_status"] == "NOT_RUN"
        and step["target_response"]["basis"]
        == "DESIGN_TARGET_NOT_OBSERVED_BASELINE"
        for step in value["process_steps"]
    )
    assert not ({"run_id", "metrics", "cost_model"} & payload.keys())
    assert not ({"run_id", "metrics", "cost_model"} & value.keys())
    assert after == before


def test_operating_model_endpoint_fails_closed_on_step_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        api_module.PROJECT_ROOT
        / "benchmark"
        / "quote-value-v0.1"
        / "public"
        / "current-process-baseline.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["process_steps"].pop()
    drifted = tmp_path / "drifted.json"
    drifted.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(api_module, "runtime_asset_path", lambda relative: drifted)
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite3")

    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/operating-model")

    workspace.close()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "OPERATING_MODEL_UNAVAILABLE"


def test_agentteams_operations_endpoint_separates_native_formation_from_change_advisory(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceService(store_path=tmp_path / "operations.sqlite3")
    workspace.form_quote()
    workspace.preview_command("launch_date")
    before_artifacts = workspace.store.connection.execute(
        "SELECT COUNT(*) FROM artifacts"
    ).fetchone()[0]
    before_events = workspace.store.verify_event_chain()["events"]

    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/agentteams-operations")

    after_artifacts = workspace.store.connection.execute(
        "SELECT COUNT(*) FROM artifacts"
    ).fetchone()[0]
    after_events = workspace.store.verify_event_chain()["events"]
    workspace.close()

    assert response.status_code == 200
    view = response.json()
    assert view["schema_version"] == "orgrebase.agentteams-operations-view.v1"
    assert view["surface_contract"] == {
        "customer_surface": "ORGREBASE_WORKSPACE",
        "agentteams_element_role": "BACK_OFFICE_TASK_OPERATIONS_AND_EVIDENCE",
        "element_embedding": "NOT_USED",
        "read_model_endpoint": "/api/workspace/agentteams-operations",
        "element_connection": "EXTERNAL_CONFIGURATION_REQUIRED",
    }
    formation = view["formation_taskflow"]
    assert formation["participation_status"] == "NOT_RUN"
    assert formation["native_taskflow_observed"] is False
    assert formation["live_distributed_observed"] is False

    change = view["change_set_advisories"]["launch_date"]
    assert change["participation_status"] == "LOCAL_DETERMINISTIC_ADVISORY"
    assert change["native_agentteams_observed"] is False
    assert change["live_agentteams_observed"] is False
    assert change["evidence_class"] == "LOCAL_DETERMINISTIC"
    assert change["role_projection_count"] == 3
    assert change["skill_invocation_status"] == "NOT_OBSERVED"
    assert change["tool_invocation_status"] == "NOT_OBSERVED"
    assert change["target_writes"] == 0
    assert before_artifacts == after_artifacts
    assert before_events == after_events


def test_default_workspace_runtime_initializes_once_under_concurrent_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(tmp_path / "startup.sqlite3"))
    original_factory = api_module._default_workspace_service
    factory_lock = Lock()
    factory_calls = 0

    def slow_factory(settings=None) -> WorkspaceService:
        nonlocal factory_calls
        with factory_lock:
            factory_calls += 1
        sleep(0.05)
        return original_factory(settings)

    monkeypatch.setattr(api_module, "_default_workspace_service", slow_factory)
    with (
        TestClient(api_module.create_app()) as client,
        ThreadPoolExecutor(max_workers=8) as pool,
    ):
        responses = list(pool.map(lambda _: client.get("/readyz"), range(8)))

    assert factory_calls == 1
    assert [response.status_code for response in responses] == [200] * 8
    assert all(response.json()["status"] == "ready" for response in responses)


@pytest.mark.parametrize(
    "endpoint",
    (
        "/api/workspace/run",
        "/api/demo/workspace/quote-to-rebase",
    ),
)
def test_one_shot_workspace_routes_fail_closed_without_state_or_approval_writes(
    tmp_path: Path, endpoint: str
) -> None:
    legacy = OrgRebaseService()
    workspace = WorkspaceService(store_path=tmp_path / "fail-closed.sqlite")
    try:
        client = TestClient(create_app(legacy, workspace, enable_legacy_demo=True))
        before_state = workspace.state()
        before_events = workspace.store.event_records()
        before_artifacts = workspace.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts"
        ).fetchone()[0]
        before_objects = workspace.store.connection.execute(
            "SELECT COUNT(*) FROM object_versions"
        ).fetchone()[0]

        response = client.post(endpoint)

        assert response.status_code == 410
        detail = response.json()["detail"]
        assert detail == {
            "code": "WORKSPACE_STAGED_COMMANDS_REQUIRED",
            "message": (
                "one-shot Workspace execution is retired because it cannot represent "
                "an external Runtime Owner decision"
            ),
            "next_endpoints": [
                "POST /api/workspace/task-intake/prepare",
                "POST /api/workspace/task-intake/admit",
                "POST /api/workspace/task-intake/run",
                "POST /api/workspace/preview/{change_kind}",
                "POST /api/workspace/approve/{change_kind}",
                "POST /api/workspace/apply/{change_kind}",
            ],
            "target_writes": 0,
        }
        assert workspace.state() == before_state
        assert workspace.store.event_records() == before_events
        assert workspace.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts"
        ).fetchone()[0] == before_artifacts
        assert workspace.store.connection.execute(
            "SELECT COUNT(*) FROM object_versions"
        ).fetchone()[0] == before_objects
        assert workspace._approval_record("launch_date") is None
        assert workspace._approval_record("currency") is None
    finally:
        workspace.close()


def test_workspace_console_and_agentteams_status_remain_available() -> None:
    legacy = OrgRebaseService()
    workspace = WorkspaceService()
    try:
        client = TestClient(create_app(legacy, workspace, enable_legacy_demo=True))
        status = client.get("/api/demo/workspace/agentteams-status")
        assert status.status_code == 200
        assert status.json()["status"] == "NOT_RUN"
        page = client.get("/")
        assert 'id="primary-action"' in page.text
        assert "企业工作持续演化引擎" in page.text
    finally:
        workspace.close()


def test_workspace_destructive_reset_is_disabled_by_default_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORGREBASE_ALLOW_DESTRUCTIVE_RESET", raising=False)
    workspace = WorkspaceService(store_path=tmp_path / "reset-disabled.sqlite")
    try:
        client = TestClient(create_app(workspace_service=workspace))
        assert client.post("/api/workspace/form").status_code == 200
        before = workspace.state()
        response = client.post("/api/workspace/reset")

        assert response.status_code == 403
        assert response.json()["detail"] == {
            "code": "WORKSPACE_DESTRUCTIVE_RESET_DISABLED",
            "message": (
                "workspace reset is a maintenance-only destructive operation; "
                "set ORGREBASE_ALLOW_DESTRUCTIVE_RESET=1 explicitly"
            ),
            "target_writes": 0,
        }
        assert workspace.state() == before
    finally:
        workspace.close()
