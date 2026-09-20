from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import orgrebase.api as api_module
import orgrebase.workspace.oac_agentic_runtime as oac_agentic_runtime_module
from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.api import create_app
from orgrebase.workspace.oac_agentic_runtime import OACAgenticRuntime
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"
OAC_ROOT = ROOT.parent / "oac-spec"


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture(scope="module")
def enterprise_runtime():
    assert (OAC_ROOT / "pyproject.toml").is_file()
    return load_enterprise_quote_pilot_pack(PACK)


def _canonical_object_count(workspace: WorkspaceService) -> int:
    row = workspace.store.connection.execute("SELECT COUNT(*) FROM object_versions").fetchone()
    assert row is not None
    return int(row[0])


def _sqlite_state_counts(store_path: Path) -> dict[str, int]:
    with sqlite3.connect(store_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "domain_events",
                "artifacts",
                "object_versions",
                "current_pointers",
            )
        }


def _configure_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ROOT", str(OAC_ROOT))
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", mode)
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_REVIEW_SECONDS", "4")


@pytest.mark.parametrize("mode", ["optional", "off", "required"])
def test_workspace_state_projects_same_run_deployment_gate_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enterprise_runtime, mode: str,
) -> None:
    _configure_api(monkeypatch, mode=mode)
    store_path = tmp_path / "state-gate.sqlite3"
    workspace = WorkspaceService(store_path=store_path, runtime_configuration=enterprise_runtime)
    try:
        with TestClient(create_app(workspace_service=workspace)) as client:
            before = _sqlite_state_counts(store_path)
            state = client.get("/api/workspace/state")
            assert state.status_code == 200, state.text
            gate = state.json()["workspace_gate"]
            assert gate == {
                "schema_version": "orgrebase.workspace-oac-gate.v1",
                "mode": mode,
                "requires_oac_admission": mode == "required",
                "form_allowed": mode != "required",
                "status": "BLOCKED_PENDING_OAC" if mode == "required" else "READY_TO_FORM",
                "execution_run_id": state.json()["execution"]["run_id"],
                "activation_binding_digest": None,
                "consumption_receipt_digest": None,
                "reason_code": "OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED" if mode == "required" else None,
                "canonical_target_writes": 0,
            }
            assert _sqlite_state_counts(store_path) == before
            if mode == "required":
                blocked = client.post("/api/workspace/form")
                assert blocked.status_code == 409
                assert blocked.json()["detail"]["message"] == "OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED"
                assert _sqlite_state_counts(store_path) == before
    finally:
        workspace.close()


@pytest.mark.parametrize(
    ("model_provider", "expected"),
    (("vertex-ai", "LIVE_VERTEX"), ("ollama-local", "OFFLINE_LOCAL")),
)
def test_api_infers_oac_execution_mode_from_model_provider(
    monkeypatch: pytest.MonkeyPatch,
    model_provider: str,
    expected: str,
) -> None:
    monkeypatch.delenv("ORGREBASE_OAC_EXECUTION_MODE", raising=False)
    assert api_module._oac_execution_mode(model_provider=model_provider) == expected


def test_explicit_oac_execution_mode_overrides_provider_and_rejects_unknowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_EXECUTION_MODE", "frozen_replay")
    assert api_module._oac_execution_mode(model_provider="ollama-local") == "FROZEN_REPLAY"

    monkeypatch.setenv("ORGREBASE_OAC_EXECUTION_MODE", "contains-secret-value")
    with pytest.raises(RuntimeError, match=r"^ORGREBASE_OAC_EXECUTION_MODE_INVALID$"):
        api_module._oac_execution_mode(model_provider="vertex-ai")


@pytest.mark.parametrize(
    ("model_provider", "expected_mode"),
    (("vertex-ai", "LIVE_VERTEX"), ("ollama-local", "OFFLINE_LOCAL")),
)
def test_api_passes_inferred_policy_and_exact_provider_to_oac_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
    model_provider: str,
    expected_mode: str,
) -> None:
    _configure_api(monkeypatch, mode="optional")
    monkeypatch.delenv("ORGREBASE_OAC_EXECUTION_MODE", raising=False)
    captured: dict[str, object] = {}

    class _RuntimeProbe:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def view(self) -> dict[str, object]:
            return {
                "execution_policy": {
                    "mode": captured["execution_mode"],
                    "model_provider": captured["shadow_model_provider"],
                }
            }

    monkeypatch.setattr(
        oac_agentic_runtime_module,
        "OACAgenticRuntime",
        _RuntimeProbe,
    )
    workspace = WorkspaceService(
        store_path=tmp_path / f"{model_provider}.sqlite3",
        runtime_configuration=enterprise_runtime,
        competition_model_provider=model_provider,
        competition_ollama_endpoint=("http://127.0.0.1:11434" if model_provider == "ollama-local" else None),
    )
    application = create_app(workspace_service=workspace)
    try:
        with TestClient(application) as client:
            response = client.get("/api/workspace/oac-adaptation/agentic")

        assert response.status_code == 200
        assert response.json()["execution_policy"] == {
            "mode": expected_mode,
            "model_provider": model_provider,
        }
        assert captured["execution_mode"] == expected_mode
        assert captured["shadow_model_provider"] == model_provider
        assert captured["ollama_endpoint"] == (
            "http://127.0.0.1:11434" if model_provider == "ollama-local" else None
        )
    finally:
        workspace.close()


def test_api_offline_override_rejects_new_vertex_mapping_and_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    _configure_api(monkeypatch, mode="optional")
    monkeypatch.setenv("ORGREBASE_OAC_EXECUTION_MODE", "OFFLINE_LOCAL")
    monkeypatch.setenv(
        "ORGREBASE_OAC_AGENTIC_RUNTIME_ROOT",
        str(tmp_path / "oac-runtime"),
    )
    workspace = WorkspaceService(
        store_path=tmp_path / "offline-vertex.sqlite3",
        runtime_configuration=enterprise_runtime,
        competition_model_provider="vertex-ai",
    )
    application = create_app(workspace_service=workspace)
    try:
        with TestClient(application) as client:
            view = client.get("/api/workspace/oac-adaptation/agentic")
            mapping = client.post(
                "/api/workspace/oac-adaptation/agent-prepare",
                json={"command_id": "command:offline-block@1"},
            )
            shadow = client.post(
                "/api/workspace/oac-adaptation/execute-shadow",
                json={},
            )

        assert view.status_code == 200
        assert view.json()["execution_policy"]["mode"] == "OFFLINE_LOCAL"
        assert mapping.status_code == 409
        assert mapping.json()["detail"]["code"] == ("OAC_AGENTIC_RUNTIME_NEW_VERTEX_MAPPING_BLOCKED")
        assert shadow.status_code == 409
        assert shadow.json()["detail"]["code"] == ("OAC_AGENTIC_RUNTIME_NEW_VERTEX_SHADOW_BLOCKED")
        encoded = json.dumps([view.json(), mapping.json(), shadow.json()]).lower()
        assert "credential" not in encoded
        assert "127.0.0.1" not in encoded
        assert not (tmp_path / "oac-runtime/agent-mapping/mapping-receipt.json").exists()
        assert not (tmp_path / "oac-runtime/shadow-execution/receipt.json").exists()
    finally:
        workspace.close()


def _prepare_with_clock(
    client: TestClient,
    application,
    *,
    clock: _Clock,
    command_id: str,
) -> dict[str, object]:
    initial = client.get("/api/workspace/oac-adaptation")
    assert initial.status_code == 200
    assert initial.json()["status"] == "PACK_OBSERVED"
    assert initial.json()["canonical_target_writes"] == 0

    adaptation = application.state.oac_adaptation_service
    assert adaptation is not None
    adaptation._wall_clock = clock
    prepared = client.post(
        "/api/workspace/oac-adaptation/prepare",
        json={"command_id": command_id},
    )
    assert prepared.status_code == 200
    return prepared.json()


def _approval_payload(prepared: dict[str, object], *, command_id: str) -> dict[str, object]:
    summary = prepared["owner_review_summary"]
    assert isinstance(summary, dict)
    return {
        "actor_id": prepared["human_authority_ref"],
        "candidate_digest": prepared["candidate_digest"],
        "owner_review_summary_digest": summary["digest"],
        "acknowledgements": list(OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS),
        "command_id": command_id,
    }


def test_api_keeps_oac_adaptation_candidate_only_until_exact_human_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    _configure_api(monkeypatch, mode="optional")
    workspace = WorkspaceService(
        store_path=tmp_path / "candidate-only.sqlite3",
        runtime_configuration=enterprise_runtime,
    )
    application = create_app(workspace_service=workspace)
    clock = _Clock()
    try:
        with TestClient(application) as client:
            canonical_before = _canonical_object_count(workspace)
            prepared = _prepare_with_clock(
                client,
                application,
                clock=clock,
                command_id="command:api:prepare@1",
            )
            assert prepared["status"] == "OWNER_REVIEW_PENDING"
            assert prepared["review_remaining_ms"] == 4_000
            assert prepared["canonical_target_writes"] == 0
            assert _canonical_object_count(workspace) == canonical_before

            approval = _approval_payload(
                prepared,
                command_id="command:api:approve@1",
            )
            early = client.post(
                "/api/workspace/oac-adaptation/approve",
                json=approval,
            )
            assert early.status_code == 409
            assert early.json()["detail"]["message"].startswith("OAC_ADAPTATION_REVIEW_GATE_NOT_READY")
            assert early.json()["detail"]["remaining_ms"] == 4_000
            assert _canonical_object_count(workspace) == canonical_before

            wrong_owner = client.post(
                "/api/workspace/oac-adaptation/approve",
                json={**approval, "actor_id": "principal:oac-adaptation-mapper"},
            )
            assert wrong_owner.status_code == 403
            assert "OAC_ADAPTATION_OWNER_MISMATCH" in wrong_owner.json()["detail"]["message"]

            wrong_digest = client.post(
                "/api/workspace/oac-adaptation/approve",
                json={**approval, "candidate_digest": "sha256:" + "0" * 64},
            )
            assert wrong_digest.status_code == 409
            assert wrong_digest.json()["detail"]["message"] == ("OAC_ADAPTATION_CANDIDATE_DIGEST_MISMATCH")
            assert _canonical_object_count(workspace) == canonical_before

            clock.advance(4)
            admitted = client.post(
                "/api/workspace/oac-adaptation/approve",
                json=approval,
            )
            assert admitted.status_code == 200
            ready = admitted.json()
            assert ready["status"] == "READY_FOR_ORGREBASE"
            assert ready["formation_parity_status"] == "PASS"
            assert ready["canonical_target_writes"] == 0
            assert ready["activation_binding"]["execution_run_id"].startswith("run:orgrebase:oac-bound:")
            assert ready["activation_binding"]["execution_run_id"] != (workspace.effective_workflow_run_id)
            assert _canonical_object_count(workspace) == canonical_before
    finally:
        workspace.close()


def test_required_mode_form_accepts_only_exact_profile_pack_and_run_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    _configure_api(monkeypatch, mode="required")
    workspace = WorkspaceService(
        store_path=tmp_path / "required-binding.sqlite3",
        runtime_configuration=enterprise_runtime,
    )
    application = create_app(workspace_service=workspace)
    clock = _Clock()
    try:
        with TestClient(application) as client:
            initial_gate = client.get("/api/workspace/oac-adaptation/agentic")
            assert initial_gate.status_code == 200
            assert initial_gate.json()["workspace_gate"] == {
                "schema_version": "orgrebase.workspace-oac-gate.v1",
                "mode": "required",
                "requires_oac_admission": True,
                "form_allowed": False,
                "status": "BLOCKED_PENDING_OAC",
                "execution_run_id": workspace.effective_workflow_run_id,
                "activation_binding_digest": None,
                "consumption_receipt_digest": None,
                "reason_code": "OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED",
                "canonical_target_writes": 0,
            }
            missing = client.post("/api/workspace/form")
            assert missing.status_code == 409
            assert missing.json()["detail"]["message"] == ("OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED")

            prepared = _prepare_with_clock(
                client,
                application,
                clock=clock,
                command_id="command:required:prepare@1",
            )
            clock.advance(4)
            admitted = client.post(
                "/api/workspace/oac-adaptation/approve",
                json=_approval_payload(
                    prepared,
                    command_id="command:required:approve@1",
                ),
            )
            assert admitted.status_code == 200
            assert admitted.json()["status"] == "READY_FOR_ORGREBASE"
            admitted_gate = client.get("/api/workspace/oac-adaptation/agentic")
            assert admitted_gate.status_code == 200
            assert admitted_gate.json()["workspace_gate"]["form_allowed"] is True
            assert admitted_gate.json()["workspace_gate"]["status"] == "READY_TO_FORM"

            original_profile_digest = workspace.profile_digest
            workspace.profile_digest = "sha256:" + "1" * 64
            wrong_profile = client.post("/api/workspace/form")
            workspace.profile_digest = original_profile_digest

            original_runtime = workspace.runtime_configuration
            workspace.runtime_configuration = replace(
                enterprise_runtime,
                pack_digest="sha256:" + "2" * 64,
            )
            wrong_pack = client.post("/api/workspace/form")
            workspace.runtime_configuration = original_runtime

            original_run_id = workspace.effective_workflow_run_id
            workspace.effective_workflow_run_id = "run:cross-binding:other@v1"
            wrong_run = client.post("/api/workspace/form")
            workspace.effective_workflow_run_id = original_run_id

            for response in (wrong_profile, wrong_pack, wrong_run):
                assert response.status_code == 409
                assert response.json()["detail"]["message"] == ("OAC_ADAPTATION_ACTIVATION_BINDING_MISMATCH")

            exact = client.post("/api/workspace/form")
            assert exact.status_code == 200
            exact_payload = exact.json()
            assert exact_payload["state"]["execution"]["run_id"] == (
                workspace.effective_workflow_run_id
            )
            activation = exact_payload["state"]["execution"]["oac_activation"]
            assert activation["status"] == "CONSUMED_BY_QUOTE_FORMATION"
            assert activation["execution_run_id"] == workspace.effective_workflow_run_id
            assert activation["adaptation_run_id"] == admitted.json()["adaptation_run_id"]
            assert activation["activation_binding_digest"] == admitted.json()[
                "activation_binding"
            ]["digest"]
            assert activation["adapter_capsule_digest"] == admitted.json()[
                "adapter_capsule"
            ]["digest"]
            assert activation["canonical_target_writes"] == 0
            assert exact_payload["state"]["event_scopes"]["layout"] == (
                "OAC_PREFIX_QUOTE_SUFFIX"
            )
            event_scopes = exact_payload["state"]["event_scopes"]
            assert event_scopes["quote_business"]["status"] == "PASS"
            assert event_scopes["quote_business"]["definition"] == (
                "CONTIGUOUS_NON_OAC_SUFFIX"
            )
            assert event_scopes["quote_business"]["start_anchor_digest"] == (
                event_scopes["oac_adaptation"]["head_digest"]
            )
            assert event_scopes["relationship"]["oac_events_are_prefix"] is True
            assert event_scopes["relationship"]["quote_is_contiguous_suffix"] is True
            event_types = [
                item["event_type"]
                for item in exact_payload["state"]["event_chain"]["records"]
            ]
            assert event_types.index("OAC_ADAPTER_ACTIVATION_CONSUMED") < event_types.index(
                "WORKSPACE_TASK_COMMITTED"
            )

            evidence = client.get("/api/workspace/export/evidence")
            assert evidence.status_code == 200
            assert evidence.json()["oac_activation_consumption"] == activation

            consumed_gate = client.get("/api/workspace/oac-adaptation/agentic")
            assert consumed_gate.status_code == 200
            gate = consumed_gate.json()["workspace_gate"]
            assert gate["form_allowed"] is True
            assert gate["status"] == "CONSUMED_BY_QUOTE_FORMATION"
            assert gate["execution_run_id"] == workspace.effective_workflow_run_id
            assert gate["consumption_receipt_digest"] == activation[
                "consumption_receipt_digest"
            ]

            repeated = client.post("/api/workspace/form")
            assert repeated.status_code == 200
            assert repeated.json()["oac_activation"] == activation
            assert [
                item["event_type"]
                for item in repeated.json()["state"]["event_chain"]["records"]
            ].count("OAC_ADAPTER_ACTIVATION_CONSUMED") == 1

            from orgrebase.workspace import oac_agent_adaptation

            implementation = oac_agent_adaptation.current_oac_admission_implementation()
            with monkeypatch.context() as upgraded:
                upgraded.setattr(
                    oac_agent_adaptation, "current_oac_admission_implementation",
                    lambda: {**implementation, "revision_digest": "sha256:" + "0" * 64},
                )
                before = workspace.store.audit_head()
                history = client.get("/api/workspace/state")
                assert history.status_code == 200
                historical_gate = history.json()["workspace_gate"]
                assert historical_gate["status"] == "CONSUMED_BY_QUOTE_FORMATION"
                assert historical_gate["form_allowed"] is False
                assert historical_gate["activation_binding_digest"] == gate["activation_binding_digest"]
                assert historical_gate["consumption_receipt_digest"] == gate["consumption_receipt_digest"]
                assert historical_gate["reason_code"] == "OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"
                projected = client.get("/api/workspace/oac-adaptation/agentic")
                assert projected.status_code == 200
                assert projected.json()["workspace_gate"] == historical_gate
                blocked = client.post("/api/workspace/form")
                assert blocked.status_code == 409
                assert blocked.json()["detail"]["message"] == "OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"
                assert workspace.store.audit_head() == before

        workspace.close()
        reopened = WorkspaceService(
            store_path=tmp_path / "required-binding.sqlite3",
            runtime_configuration=enterprise_runtime,
        )
        try:
            recovered = reopened.state()["execution"]["oac_activation"]
            assert recovered == activation
            assert reopened.state()["event_scopes"]["layout"] == (
                "OAC_PREFIX_QUOTE_SUFFIX"
            )
        finally:
            reopened.close()
    finally:
        workspace.close()


def test_required_task_intake_run_fetches_server_owned_oac_roots_for_same_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    """The employee request path must not bypass or self-report OAC roots."""

    _configure_api(monkeypatch, mode="required")
    workspace = WorkspaceService(
        store_path=tmp_path / "required-task-intake.sqlite3",
        runtime_configuration=enterprise_runtime,
        task_intake_required=True,
    )

    class _FormationRootProbe:
        def __init__(self) -> None:
            self.roots = None
            self.calls = 0
            self.observed_run_ids: list[str] = []

        def formation_roots_for_current_task(
            self,
            *,
            expected_activation_binding_digest: str,
        ):
            self.calls += 1
            self.observed_run_ids.append(workspace.effective_workflow_run_id)
            assert self.roots is not None
            assert expected_activation_binding_digest == admitted.json()[
                "activation_binding"
            ]["digest"]
            return self.roots

        def view(self) -> dict[str, object]:
            return {
                "schema_version": "orgrebase.oac-agentic-runtime-view.v1",
                "status": "READY_FOR_SHADOW",
            }

    probe = _FormationRootProbe()
    application = create_app(
        workspace_service=workspace,
        oac_agentic_runtime=probe,
    )
    clock = _Clock()
    captured: dict[str, object] = {}
    try:
        with TestClient(application) as client:
            prepared = _prepare_with_clock(
                client,
                application,
                clock=clock,
                command_id="command:required-intake:prepare@1",
            )
            clock.advance(4)
            admitted = client.post(
                "/api/workspace/oac-adaptation/approve",
                json=_approval_payload(
                    prepared,
                    command_id="command:required-intake:approve@1",
                ),
            )
            assert admitted.status_code == 200

            roots_runtime = OACAgenticRuntime(
                runtime_root=tmp_path / "oac-formation-roots",
                repo_root=ROOT,
                checkout=default_agentteams_checkout(ROOT),
                lock_path=ROOT / "agentteams/teamharness-lock.json",
                pack_path=PACK,
                frozen_golden_root=ROOT / "evidence/golden-competition/latest/pilot",
                adaptation_service=application.state.oac_adaptation_service,
                workspace_service=workspace,
                execution_mode="OFFLINE_LOCAL",
                shadow_model_provider="ollama-local",
            )
            probe.roots = roots_runtime.formation_roots_for_current_task(
                expected_activation_binding_digest=admitted.json()[
                    "activation_binding"
                ]["digest"],
            )
            expected_formation, expected_context = probe.roots

            def capture_formation(request, **kwargs):
                captured["request"] = request
                captured.update(kwargs)
                return {
                    "task_intake": {"status": "FORMATION_COMPLETED"},
                    "state": {"stage": "CURRENT"},
                }

            monkeypatch.setattr(
                workspace,
                "form_quote_with_dependency_evidence",
                capture_formation,
            )
            task = enterprise_runtime.profile.default_task
            work_description = (
                "请生成一份符合当前产品、法务、财务与市场规则的企业报价。"
            )
            candidate_response = client.post(
                "/api/workspace/task-intake/prepare",
                json={
                    "prompt": work_description,
                    "actor_id": task.actor_id,
                    "customer_id": task.customer_id,
                    "deliverable_kind": task.deliverable_kind,
                },
            )
            assert candidate_response.status_code == 200
            candidate = candidate_response.json()
            assert candidate["status"] == "READY_FOR_CONFIRMATION"
            approval_response = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": task.actor_id,
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
            )
            assert approval_response.status_code == 200
            approval = approval_response.json()
            run_response = client.post(
                "/api/workspace/task-intake/run",
                json={
                    "actor_id": task.actor_id,
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                    "approval_receipt": approval,
                    "approval_digest": approval["digest"],
                    "work_description": work_description,
                },
            )

        assert run_response.status_code == 200
        assert probe.calls == 1
        assert probe.observed_run_ids == [workspace.effective_workflow_run_id]
        assert candidate["intended_run_id"] == workspace.effective_workflow_run_id
        assert approval["intended_run_id"] == workspace.effective_workflow_run_id
        activation = captured["oac_activation_binding"]
        assert isinstance(activation, dict)
        assert activation["execution_run_id"] == workspace.effective_workflow_run_id
        assert captured["task_formation_decision_receipt"] == (
            expected_formation.model_dump(mode="json")
        )
        assert captured["context_envelope"] == expected_context.model_dump(mode="json")
        assert captured["task_intake_candidate"] == candidate
        assert captured["task_intake_approval"] == approval
    finally:
        workspace.close()


def test_required_oac_api_closes_v1_to_v3_lineage_and_state_get_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    _configure_api(monkeypatch, mode="required")
    store_path = tmp_path / "required-full-lineage.sqlite3"
    workspace = WorkspaceService(
        store_path=store_path,
        runtime_configuration=enterprise_runtime,
    )
    application = create_app(workspace_service=workspace)
    clock = _Clock()
    try:
        with TestClient(application) as client:
            prepared = _prepare_with_clock(
                client,
                application,
                clock=clock,
                command_id="command:required-full-lineage:prepare@1",
            )
            clock.advance(4)
            admitted = client.post(
                "/api/workspace/oac-adaptation/approve",
                json=_approval_payload(
                    prepared,
                    command_id="command:required-full-lineage:approve@1",
                ),
            )
            assert admitted.status_code == 200
            assert admitted.json()["status"] == "READY_FOR_ORGREBASE"

            formed = client.post("/api/workspace/form")
            assert formed.status_code == 200
            assert formed.json()["state"]["stage"] == "CURRENT"

            for kind, expected_stage in (
                ("launch_date", "CURRENT"),
                ("currency", "CURRENT"),
            ):
                preview = client.post(f"/api/workspace/preview/{kind}")
                assert preview.status_code == 200
                preview_payload = preview.json()
                approved = client.post(
                    f"/api/workspace/approve/{kind}",
                    json={
                        "actor_id": enterprise_runtime.change_owners[kind],
                        "preview_digest": preview_payload["preview_digest"],
                    },
                )
                assert approved.status_code == 200
                applied = client.post(
                    f"/api/workspace/apply/{kind}",
                    json={"approval_digest": approved.json()["approval_digest"]},
                )
                assert applied.status_code == 200
                assert applied.json()["state"]["stage"] == expected_stage

            counts_before_get = _sqlite_state_counts(store_path)
            final_response = client.get("/api/workspace/state")
            assert final_response.status_code == 200
            counts_after_get = _sqlite_state_counts(store_path)
            assert counts_after_get == counts_before_get

            final_state = final_response.json()
            assert final_state["stage"] == "CURRENT"
            lineage = final_state["enterprise_data_lineage"]
            assert lineage["status"] == "READY"
            assert lineage["read_model_target_writes"] == 0
            assert lineage["source"]["status"] == "ADMITTED_AND_MATCHED"
            assert len(lineage["source"]["values"]) == 9
            assert lineage["oac"]["source_admission_verdict"] == "ADMITTED"
            assert lineage["oac"]["runtime_projection_verdict"] == "MATCH"
            assert lineage["oac"]["activation_status"] == (
                "CONSUMED_BY_QUOTE_FORMATION"
            )
            bindings = lineage["oac"]["component_bindings"]
            assert len(bindings) == 5
            assert all(
                item["source_admission_verdict"] == "ADMITTED"
                and item["runtime_projection_status"] == "MATCH"
                for item in bindings
            )
            assert lineage["context"]["slot_count"] == 8
            assert lineage["context"]["actor_count"] == 5
            assert lineage["context"]["domain_actor_count"] == 4
            assert [
                item["version"] for item in lineage["quotes"]["versions"]
            ] == ["v1", "v2", "v3"]
            assert len(lineage["changes"]) == 2
            assert all(
                item["status"] == "COMPLETED"
                and item["approval_status"] == "APPROVED"
                and item["receipt_status"] == "COMPLETED"
                and item["workspace_receipt_status"] == "COMPLETED"
                for item in lineage["changes"]
            )
    finally:
        workspace.close()


def test_required_mode_rolls_back_activation_consumption_with_quote_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    _configure_api(monkeypatch, mode="required")
    workspace = WorkspaceService(
        store_path=tmp_path / "required-atomicity.sqlite3",
        runtime_configuration=enterprise_runtime,
    )
    application = create_app(workspace_service=workspace)
    clock = _Clock()
    try:
        with TestClient(application) as client:
            prepared = _prepare_with_clock(
                client,
                application,
                clock=clock,
                command_id="command:required-atomicity:prepare@1",
            )
            clock.advance(4)
            admitted = client.post(
                "/api/workspace/oac-adaptation/approve",
                json=_approval_payload(
                    prepared,
                    command_id="command:required-atomicity:approve@1",
                ),
            )
            assert admitted.status_code == 200

            def fail_commit(*_args: object, **_kwargs: object) -> None:
                raise RuntimeError("TEST_FORMATION_COMMIT_FAILED")

            monkeypatch.setattr(workspace.formation, "commit_quote", fail_commit)
            failed = client.post("/api/workspace/form")
            assert failed.status_code == 409
            assert failed.json()["detail"]["message"] == "TEST_FORMATION_COMMIT_FAILED"

        state = workspace.state()
        assert state["quote"] is None
        assert state["execution"]["oac_activation"]["status"] == "NOT_USED_IN_THIS_RUN"
        assert state["event_scopes"]["quote_business"]["events"] == 0
        event_types = {
            item["event_type"] for item in state["event_chain"]["records"]
        }
        assert "OAC_ADAPTER_ACTIVATION_CONSUMED" not in event_types
        assert "WORKSPACE_TASK_COMMITTED" not in event_types
    finally:
        workspace.close()


class _AgenticRuntimeStub:
    def __init__(self) -> None:
        self.status = "NOT_STARTED"
        self.command_ids: list[str] = []
        self.shadow_calls = 0

    def view(self) -> dict[str, object]:
        return {
            "schema_version": "orgrebase.oac-agentic-runtime-view.v1",
            "status": self.status,
            "adaptation": {"canonical_target_writes": 0},
            "agent_mapping": {"canonical_target_writes": 0},
            "context_residency": {"return_to_control_plane": {"canonical_target_writes": 0}},
            "shadow_execution": {"canonical_target_writes": 0},
        }

    def agent_prepare(self, *, command_id: str) -> dict[str, object]:
        self.command_ids.append(command_id)
        self.status = "OWNER_REVIEW_PENDING"
        return self.view()

    def execute_shadow(self) -> dict[str, object]:
        self.shadow_calls += 1
        self.status = "SHADOW_COMPLETED"
        return self.view()


def test_agentic_oac_endpoints_keep_the_legacy_contract_and_delegate_to_one_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enterprise_runtime,
) -> None:
    _configure_api(monkeypatch, mode="optional")
    workspace = WorkspaceService(
        store_path=tmp_path / "agentic-api.sqlite3",
        runtime_configuration=enterprise_runtime,
    )
    agentic = _AgenticRuntimeStub()
    application = create_app(
        workspace_service=workspace,
        oac_agentic_runtime=agentic,
    )
    try:
        with TestClient(application) as client:
            legacy = client.get("/api/workspace/oac-adaptation")
            product = client.get("/api/workspace/oac-adaptation/agentic")
            prepared = client.post(
                "/api/workspace/oac-adaptation/agent-prepare",
                json={"command_id": "command:agentic-api:prepare@1"},
            )
            shadow = client.post(
                "/api/workspace/oac-adaptation/execute-shadow",
                json={},
            )

        assert legacy.status_code == 200
        assert legacy.json()["status"] == "PACK_OBSERVED"
        assert product.json()["status"] == "NOT_STARTED"
        assert product.json()["workspace_gate"]["mode"] == "optional"
        assert product.json()["workspace_gate"]["requires_oac_admission"] is False
        assert product.json()["workspace_gate"]["form_allowed"] is True
        assert product.json()["workspace_gate"]["status"] == "READY_TO_FORM"
        assert prepared.json()["status"] == "OWNER_REVIEW_PENDING"
        assert shadow.json()["status"] == "SHADOW_COMPLETED"
        assert agentic.command_ids == ["command:agentic-api:prepare@1"]
        assert agentic.shadow_calls == 1
        assert shadow.json()["shadow_execution"]["canonical_target_writes"] == 0
    finally:
        workspace.close()
