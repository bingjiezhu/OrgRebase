from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from orgrebase import cli
from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.competition_worker import OLLAMA_MODEL_DIGEST
from orgrebase.workspace.oac_agentic_runtime import OACAgenticRuntime
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACAdapterActivationBinding,
    OACQuoteAdaptationService,
)
from orgrebase.workspace.pilot import (
    enterprise_quote_pilot_run_id,
    load_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.service import (
    GOLDEN_COMPETITION_ARTIFACT_ID,
    GOLDEN_COMPETITION_EVENT_TYPE,
    GOLDEN_COMPETITION_MEDIA_TYPE,
    WorkspaceService,
)

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"
CHECKOUT = default_agentteams_checkout(ROOT)
LOCK = ROOT / "agentteams" / "teamharness-lock.json"
OAC_ROOT = ROOT.parent / "oac-spec"


class _ReviewClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _exact_local_runtime_available() -> bool:
    if not CHECKOUT.is_dir():
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ):
        return False
    return any(
        item.get("name") == "qwen2.5:3b" and item.get("digest") == OLLAMA_MODEL_DIGEST
        for item in payload.get("models", [])
        if isinstance(item, dict)
    )


def _service(
    base: Path,
    *,
    mode: str,
    runner: Any | None = None,
) -> WorkspaceService:
    runtime = load_enterprise_quote_pilot_pack(PACK)
    return WorkspaceService(
        store_path=base / "workspace.sqlite3",
        workflow_run_id=enterprise_quote_pilot_run_id(runtime),
        runtime_configuration=runtime,
        competition_mode=mode,
        competition_evidence_root=base / "golden",
        competition_pack_path=PACK,
        competition_checkout=CHECKOUT,
        competition_lock_path=LOCK,
        competition_runner=runner,
    )


def _activation_binding(service: WorkspaceService) -> OACAdapterActivationBinding:
    runtime = service.runtime_configuration
    assert runtime is not None
    return OACAdapterActivationBinding(
        adaptation_run_id="run:oac-adaptation:test@v1",
        adapter_capsule_digest="sha256:" + "a" * 64,
        profile_digest=service.profile_digest,
        pack_digest=runtime.pack_digest,
        execution_run_id=service.effective_workflow_run_id,
    )


def _oac_execution_roots() -> tuple[dict[str, str], dict[str, str]]:
    """Minimal opaque roots for tests that replace the Golden owner itself."""

    return (
        {"digest": sha256_digest({"root": "formation"})},
        {"digest": sha256_digest({"root": "context"})},
    )


def _approved_deterministic_oac_roots(
    service: WorkspaceService,
    base: Path,
) -> tuple[
    OACQuoteAdaptationService,
    OACAdapterActivationBinding,
    Any,
    Any,
]:
    runtime = service.runtime_configuration
    assert runtime is not None
    clock = _ReviewClock()
    adaptation = OACQuoteAdaptationService(
        store=StateStore(base / "oac.sqlite3"),
        profile=runtime.profile,
        runtime=runtime,
        oac_root=OAC_ROOT,
        wall_clock=clock,
        execution_run_id=service.effective_workflow_run_id,
    )
    coordinator = OACAgenticRuntime(
        runtime_root=base / "oac-runtime",
        repo_root=ROOT,
        checkout=CHECKOUT,
        lock_path=LOCK,
        pack_path=PACK,
        frozen_golden_root=ROOT / "evidence/golden-competition/latest/pilot",
        adaptation_service=adaptation,
        workspace_service=service,
        execution_mode="OFFLINE_LOCAL",
        shadow_model_provider="ollama-local",
    )
    pending = adaptation.prepare(command_id="command:golden-oac:prepare@1")
    clock.advance(4)
    ready = adaptation.approve(
        actor_id=pending["human_authority_ref"],
        candidate_digest=pending["candidate_digest"],
        command_id="command:golden-oac:approve@1",
        owner_review_summary_digest=pending["owner_review_summary"]["digest"],
        acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    )
    binding = OACAdapterActivationBinding.model_validate(ready["activation_binding"])
    formation, context = coordinator.formation_roots_for_current_task(
        expected_activation_binding_digest=binding.digest,
    )
    return adaptation, binding, formation, context


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _reseal_json(value: dict[str, Any]) -> None:
    value.pop("digest", None)
    value["digest"] = sha256_digest(value)


def _stub_golden_prepare(service: WorkspaceService, request):
    prepared = service.formation.prepare_quote(
        request,
        run_id=service.effective_workflow_run_id,
    )
    evidence = service._golden_compact_record(
        {
            "schema_version": "orgrebase.workspace-golden-competition-evidence.v1",
            "status": "PASS",
            "run_id": service.effective_workflow_run_id,
            "correlation_id": service.workflow_correlation_id,
            "evidence_class": "CONTROLLED_LOCAL_TEST_DOUBLE",
            "claim_boundary": "TEST_DOUBLE_NOT_AGENTTEAMS_EVIDENCE",
            "summary_digest": sha256_digest(
                {"run_id": service.effective_workflow_run_id}
            ),
            "prepared_formation_digest": prepared.digest,
            "prepared_quote_ref": prepared.deliverable.ref,
            "oac_agentteams_lineage": {
                "status": "OAC_BOUND_EXECUTION_PLAN_REALIZED",
                "task_formation_decision_receipt_digest": sha256_digest(
                    {"root": "formation"}
                ),
                "task_agent_context_envelope_digest": sha256_digest(
                    {"root": "context"}
                ),
                "agentteams_execution_plan_digest": sha256_digest(
                    {"root": "plan"}
                ),
                "candidate_only": True,
                "canonical_target_writes": 0,
            },
            "agent_collaboration": {
                "agent_runs": [],
                "reviewer": {
                    "attempt_2": {"verdict": "PASS"},
                    "model_evidence_class": "CONTROLLED_LOCAL_TEST_DOUBLE",
                    "target_writes": 0,
                },
                "tool": {"status": "SUCCEEDED", "target_writes": 0},
                "skill": {"status": "PASS", "target_writes": 0},
            },
            "canonical_target_writes_before_control_commit": 0,
        }
    )
    return prepared, evidence


def test_workspace_forwards_explicit_vertex_provider_without_credentials(
    tmp_path: Path,
) -> None:
    observed: dict[str, Any] = {}

    def runner(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        raise RuntimeError("STOP_AFTER_ARGUMENT_CAPTURE")

    runtime = load_enterprise_quote_pilot_pack(PACK)
    service = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite3",
        workflow_run_id=enterprise_quote_pilot_run_id(runtime),
        runtime_configuration=runtime,
        competition_mode="golden",
        competition_evidence_root=tmp_path / "golden",
        competition_pack_path=PACK,
        competition_checkout=CHECKOUT,
        competition_lock_path=LOCK,
        competition_model_provider="vertex-ai",
        competition_vertex_project="example-project-12345",
        competition_runner=runner,
    )
    reserved_run_id = service.effective_workflow_run_id
    correlation_id = service.workflow_correlation_id
    try:
        with pytest.raises(RuntimeError, match="STOP_AFTER_ARGUMENT_CAPTURE"):
            service.form_quote_with_dependency_evidence()
    finally:
        service.close()

    assert observed["model_provider"] == "vertex-ai"
    assert observed["vertex_project"] == "example-project-12345"
    assert observed["ollama_endpoint"] is None
    assert observed["execution_run_id"] == reserved_run_id
    assert reserved_run_id != correlation_id
    assert not ({"access_token", "api_key", "credential"} & set(observed))


def test_workspace_rejects_unknown_competition_model_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="WORKSPACE_COMPETITION_MODEL_PROVIDER_INVALID"):
        WorkspaceService(
            store_path=tmp_path / "workspace.sqlite3",
            competition_model_provider="implicit-fallback",
        )


def test_oac_activation_binding_without_execution_roots_fails_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, mode="golden")
    binding = _activation_binding(service)
    try:
        with pytest.raises(
            IntegrityError,
            match=r"^WORKSPACE_OAC_EXECUTION_ROOTS_REQUIRED$",
        ):
            service.form_quote_with_dependency_evidence(
                oac_activation_binding=binding.model_dump(mode="json"),
            )
        assert service.state()["quote"] is None
        assert service.state()["competition_evidence"] is None
    finally:
        service.close()


@pytest.mark.parametrize("missing_root", ("formation", "context"))
def test_partial_oac_execution_root_pair_fails_closed_before_golden_dispatch(
    tmp_path: Path,
    missing_root: str,
) -> None:
    dispatches = 0

    def must_not_dispatch(**_: Any) -> dict[str, Any]:
        nonlocal dispatches
        dispatches += 1
        raise AssertionError("partial OAC roots must fail before Golden dispatch")

    service = _service(tmp_path, mode="golden", runner=must_not_dispatch)
    binding = _activation_binding(service)
    formation_root, context_root = _oac_execution_roots()
    try:
        with pytest.raises(
            IntegrityError,
            match=r"^WORKSPACE_OAC_EXECUTION_ROOTS_INCOMPLETE$",
        ):
            service.form_quote_with_dependency_evidence(
                oac_activation_binding=binding.model_dump(mode="json"),
                task_formation_decision_receipt=(
                    None if missing_root == "formation" else formation_root
                ),
                context_envelope=(
                    None if missing_root == "context" else context_root
                ),
            )
        assert dispatches == 0
        assert service.state()["quote"] is None
        assert service.state()["competition_evidence"] is None
    finally:
        service.close()


def test_required_oac_consumption_uses_reserved_golden_execution_run_and_reopens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, mode="golden")
    reserved_run_id = service.effective_workflow_run_id
    binding = _activation_binding(service)
    monkeypatch.setattr(
        service,
        "_prepare_golden_competition",
        lambda request, **_roots: _stub_golden_prepare(service, request),
    )
    formation_root, context_root = _oac_execution_roots()
    try:
        formed = service.form_quote_with_dependency_evidence(
            oac_activation_binding=binding.model_dump(mode="json"),
            task_formation_decision_receipt=formation_root,
            context_envelope=context_root,
        )
        activation = formed["state"]["execution"]["oac_activation"]

        assert formed["formation_run_id"] == reserved_run_id
        assert formed["state"]["competition_evidence"]["run_id"] == reserved_run_id
        assert activation["status"] == "CONSUMED_BY_QUOTE_FORMATION"
        assert activation["execution_run_id"] == reserved_run_id
        assert activation["activation_binding_digest"] == binding.digest
        assert formed["tool_invocation"]["receipt"]["workflow_run_id"] == (
            reserved_run_id
        )
    finally:
        service.close()

    def must_not_rerun(**_: Any) -> dict[str, Any]:
        raise AssertionError("restart must reuse the persisted Golden execution")

    reopened = _service(tmp_path, mode="golden", runner=must_not_rerun)
    try:
        state = reopened.state()
        assert state["execution"]["run_id"] == reserved_run_id
        assert state["execution"]["oac_activation"]["execution_run_id"] == (
            reserved_run_id
        )
        assert state["quote"]["version"] == "v1"
    finally:
        reopened.close()


def test_failed_required_oac_golden_commit_rolls_back_consumption_and_keeps_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, mode="golden")
    reserved_run_id = service.effective_workflow_run_id
    binding = _activation_binding(service)
    monkeypatch.setattr(
        service,
        "_prepare_golden_competition",
        lambda request, **_roots: _stub_golden_prepare(service, request),
    )
    formation_root, context_root = _oac_execution_roots()

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("TEST_GOLDEN_FORMATION_COMMIT_FAILED")

    monkeypatch.setattr(service.formation, "commit_quote", fail_commit)
    try:
        with pytest.raises(
            RuntimeError,
            match="TEST_GOLDEN_FORMATION_COMMIT_FAILED",
        ):
            service.form_quote_with_dependency_evidence(
                oac_activation_binding=binding.model_dump(mode="json"),
                task_formation_decision_receipt=formation_root,
                context_envelope=context_root,
            )

        state = service.state()
        assert service.effective_workflow_run_id == reserved_run_id
        assert state["quote"] is None
        assert state["competition_evidence"] is None
        assert state["execution"]["oac_activation"]["status"] == (
            "NOT_USED_IN_THIS_RUN"
        )
        assert not service.store.artifact_exists(GOLDEN_COMPETITION_ARTIFACT_ID)
        event_types = {
            item["event_type"] for item in service.store.event_envelopes()
        }
        assert "OAC_ADAPTER_ACTIVATION_CONSUMED" not in event_types
        assert GOLDEN_COMPETITION_EVENT_TYPE not in event_types
        assert "WORKSPACE_TASK_COMMITTED" not in event_types
    finally:
        service.close()


def test_execution_ledger_projects_observed_reviewer_provider_without_stale_model_copy(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(store_path=tmp_path / "workspace.sqlite3")
    run_id = service.effective_workflow_run_id
    try:
        activity = service._execution_activity(
            formation=None,
            dependency_tool={},
            changes={},
            competition_evidence={
                "run_id": run_id,
                "summary_digest": "sha256:summary",
                "agent_collaboration": {
                    "agent_runs": [
                        {
                            "task_id": "dynamic-plan-reviewer-a1",
                            "agent_name": "independent-reviewer",
                            "role": "REVIEWER",
                            "attempt": 1,
                            "authority_domain": "review",
                            "status": "TRUSTED_COMPLETE",
                            "target_writes": 0,
                        },
                        {
                            "task_id": "dynamic-plan-reviewer-a2",
                            "agent_name": "independent-reviewer",
                            "role": "REVIEWER",
                            "attempt": 2,
                            "authority_domain": "review",
                            "status": "TRUSTED_COMPLETE",
                            "target_writes": 0,
                        },
                    ],
                    "reviewer": {
                        "attempt_2": {"verdict": "PASS"},
                        "model_evidence_class": "LIVE_MODEL",
                        "model_provider": "vertex-ai",
                        "model_version": "gemini-3.7-flash",
                        "target_writes": 0,
                    },
                    "tool": {},
                    "skill": {},
                },
            },
        )
    finally:
        service.close()

    reviewer_row = next(
        row
        for row in activity["rows"]
        if row["action"] == "REPLAN_THEN_ACCEPT_EXACT_PROVENANCE"
    )
    assert reviewer_row["tool_or_skill"] == (
        "vertex-ai:gemini-3.7-flash advisory + deterministic verifier"
    )
    assert "qwen" not in reviewer_row["tool_or_skill"]
    reviewer_rows = [
        row
        for row in activity["rows"]
        if row["plane"] == "REVIEWER"
    ]
    assert len(reviewer_rows) == 3
    assert {row["actor_or_domain"] for row in reviewer_rows} == {
        "independent-reviewer"
    }


@pytest.mark.skipif(
    not _exact_local_runtime_available(),
    reason="exact pinned AgentTeams checkout and qwen2.5:3b are required",
)
def test_golden_pilot_commits_exact_candidate_run_and_recovers_from_sqlite(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, mode="golden")
    adaptation, activation_binding, formation_root, context_root = (
        _approved_deterministic_oac_roots(service, tmp_path)
    )
    try:
        formed = service.form_quote_with_dependency_evidence(
            oac_activation_binding=activation_binding.model_dump(mode="json"),
            task_formation_decision_receipt=formation_root.model_dump(mode="json"),
            context_envelope=context_root.model_dump(mode="json"),
        )
        state = formed["state"]
        evidence = state["competition_evidence"]
        run_id = evidence["run_id"]
        correlation_id = evidence["correlation_id"]

        assert state["quote"]["version"] == "v1"
        expected_execution = {
            "schema_version": "orgrebase.workspace-execution-view.v1",
            "run_id": run_id,
            "scenario_id": state["scenario"].get("id"),
            "mode": "CONTROLLED_LOCAL_NATIVE_TASKFLOW_EXECUTED",
            "candidate_runtime": "PINNED_AGENTTEAMS_WITH_INDEPENDENT_LOCAL_PROCESSES",
            "canonical_authority": "OrgRebase StateStore and RebaseWorkflow",
            "agentteams": "CONTROLLED_LOCAL_NATIVE_TASKFLOW_EXECUTED",
            "oac_admission": "CONSUMED_BY_QUOTE_FORMATION",
            "external_writes": "DISABLED",
        }
        assert {
            key: state["execution"][key] for key in expected_execution
        } == expected_execution
        assert state["execution"]["oac_activation"]["status"] == (
            "CONSUMED_BY_QUOTE_FORMATION"
        )
        assert state["execution"]["oac_activation"]["execution_run_id"] == run_id
        assert state["execution"]["oac_activation"]["activation_binding_digest"] == (
            activation_binding.digest
        )
        operations = state["agentteams_operations"]
        formation_taskflow = operations["formation_taskflow"]
        assert formation_taskflow["participation_status"] == (
            "CONTROLLED_LOCAL_NATIVE_OBSERVED"
        )
        assert formation_taskflow["native_taskflow_observed"] is True
        assert formation_taskflow["live_distributed_observed"] is False
        assert formation_taskflow["run_id"] == evidence["run_id"]
        assert formation_taskflow["summary_digest"] == evidence["summary_digest"]
        assert formation_taskflow["action_count"] == evidence["agentteams_action_count"]
        assert formation_taskflow["project_terminal_state"] == "completed"
        assert run_id.startswith("run:golden-competition:")
        assert run_id != correlation_id
        assert correlation_id == enterprise_quote_pilot_run_id(load_enterprise_quote_pilot_pack(PACK))
        assert formed["formation_run_id"] == run_id
        quote_ref = f"{state['quote']['id']}@{state['quote']['version']}"
        assert evidence["prepared_quote_ref"] == quote_ref
        assert evidence["canonical_target_writes_before_control_commit"] == 0
        authority = evidence["agent_collaboration"]["authority"]
        assert {
            authority["candidate_target_writes"],
            authority["agent_target_writes"],
            authority["reviewer_target_writes"],
            authority["tool_target_writes"],
            authority["skill_target_writes"],
        } == {0}
        assert authority["formation_quote_target_writes"] == 1
        assert len(evidence["agent_collaboration"]["orchestration_plan"]["tasks"]) == 8
        assert len(evidence["agent_collaboration"]["agent_runs"]) == 8
        assert all(item["target_writes"] == 0 for item in evidence["agent_collaboration"]["agent_runs"])
        reviewer_runs = [
            item
            for item in evidence["agent_collaboration"]["agent_runs"]
            if item["role"] == "REVIEWER"
        ]
        assert len(reviewer_runs) == 2
        assert {item["agent_name"] for item in reviewer_runs} == {
            "independent-reviewer"
        }
        assert {item["attempt"] for item in reviewer_runs} == {1, 2}
        assert {item["authority_domain"] for item in reviewer_runs} == {"review"}
        for run in reviewer_runs:
            assert set(run["model_advisory"]) == {"verdict", "missing_domains"}
            assert set(run["deterministic_review"]) == {"verdict", "missing_domains"}
            assert run["model_advisory"]["verdict"] in {"PASS", "REPLAN"}
            assert set(run["model_advisory"]["missing_domains"]) <= {
                "product", "legal", "finance", "gtm",
            }
            assert run["deterministic_review"] == (
                {"verdict": "REPLAN", "missing_domains": ["finance"]}
                if run["attempt"] == 1 else {"verdict": "PASS", "missing_domains": []}
            )
        assert all(
            item["model_version"]
            == "qwen2.5:3b@ollama-manifest:357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
            for item in reviewer_runs
        )
        assert {item["model_evidence_class"] for item in reviewer_runs} == {"LOCAL_OLLAMA_MODEL"}
        assert {item["model_claim_boundary"] for item in reviewer_runs} == {
            "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER"
        }
        skill = evidence["agent_collaboration"]["skill"]
        assert skill["authorization_mode"] == "RELEASE"
        assert skill["release_state"] == "CANARY"
        assert skill["evaluation_verdict"] == "CANARY"
        assert skill["evaluation_partition_count"] == 8
        assert skill["evaluation_case_count"] == 9
        assert skill["qualification_suite_revision"] == "orgrebase.quote-skill-qualification.v2"
        assert skill["release_transition_count"] == 3
        assert skill["release_receipt_digest"].startswith("sha256:")

        output = tmp_path / "golden" / evidence["output_run"]
        summary = _read_json(output / "summary.json")
        plan = _read_json(output / "agentteams/execution-plan.json")
        process_receipts = json.loads(
            (output / "process-receipts.json").read_text(encoding="utf-8")
        )
        prepared = _read_json(output / "prepared-formation-bundle.json")
        formation_digest = formation_root.digest
        context_digest = context_root.digest
        plan_digest = plan["digest"]
        expected_roots = {
            "task_formation_decision_receipt_digest": formation_digest,
            "context_envelope_digest": context_digest,
            "agentteams_execution_plan_digest": plan_digest,
        }
        assert {
            key: summary[key] for key in expected_roots
        } == expected_roots
        assert {
            key: evidence[key] for key in expected_roots
        } == expected_roots
        lineage = evidence["oac_agentteams_lineage"]
        assert lineage["status"] == "OAC_BOUND_EXECUTION_PLAN_REALIZED"
        assert lineage["activation_binding_digest"] == activation_binding.digest
        assert lineage["task_formation_decision_receipt_digest"] == formation_digest
        assert lineage["task_agent_context_envelope_digest"] == context_digest
        assert lineage["agentteams_execution_plan_digest"] == plan_digest
        assert lineage["planned_domain_ids"] == list(formation_root.selected_domain_ids)
        assert lineage["actual_agentteams_domain_ids"] == list(
            formation_root.selected_domain_ids
        )
        assert lineage["topology_match"] is True
        assert lineage["context_freshness_basis"] == "LOGICAL_EVENT_TIME"
        assert lineage["candidate_only"] is True
        assert lineage["canonical_target_writes"] == 0
        assert plan["formation_receipt_digest"] == formation_digest
        assert plan["context_envelope_digest"] == context_digest
        assert plan["selected_domain_ids"] == list(formation_root.selected_domain_ids)
        assert len(summary["task_bindings"]) == 7
        assert len(process_receipts) == 7
        for binding in summary["task_bindings"]:
            assert binding["formation_receipt_digest"] == formation_digest
            assert binding["context_envelope_digest"] == context_digest
            assert binding["agentteams_execution_plan_digest"] == plan_digest
            result = _read_json(
                output / "process-outputs" / f"{binding['task_id']}.json"
            )
            assert result["formation_receipt_digest"] == formation_digest
            assert result["context_envelope_digest"] == context_digest
            assert result["agentteams_execution_plan_digest"] == plan_digest
        for process_receipt in process_receipts:
            assert process_receipt["formation_receipt_digest"] == formation_digest
            assert process_receipt["context_envelope_digest"] == context_digest
            assert process_receipt["agentteams_execution_plan_digest"] == plan_digest
        assert prepared["event_payload"][
            "task_formation_decision_receipt_digest"
        ] == formation_digest
        assert prepared["event_payload"]["context_envelope_digest"] == context_digest
        assert prepared["event_payload"][
            "agentteams_execution_plan_digest"
        ] == plan_digest
        assert _read_json(
            output / "inputs/task-formation-decision-receipt.json"
        )["digest"] == formation_digest
        assert _read_json(output / "inputs/task-agent-context-envelope.json")[
            "digest"
        ] == context_digest

        expected_failures = {
            "summary": "WORKSPACE_GOLDEN_OAC_EXECUTION_ROOT_BINDING_INVALID",
            "binding": "WORKSPACE_GOLDEN_TASK_OAC_BINDING_INVALID",
            "process": "WORKSPACE_GOLDEN_PROCESS_OAC_BINDING_INVALID",
            "result": "WORKSPACE_GOLDEN_PROCESS_OUTPUT_OAC_BINDING_INVALID",
            "prepared": "WORKSPACE_GOLDEN_PREPARED_OAC_BINDING_INVALID",
        }
        fake_digest = "sha256:" + "f" * 64

        def tampered_replay(case: str):
            def runner(**kwargs: Any) -> dict[str, Any]:
                replay = Path(kwargs["output_dir"])
                shutil.copytree(output, replay, dirs_exist_ok=True)
                replay_summary = _read_json(replay / "summary.json")
                if case == "summary":
                    replay_summary["context_envelope_digest"] = fake_digest
                elif case == "binding":
                    binding = replay_summary["task_bindings"][0]
                    binding["formation_receipt_digest"] = fake_digest
                    _reseal_json(binding)
                    _write_json(
                        replay / "agentteams/bindings" / f"{binding['task_id']}.json",
                        binding,
                    )
                elif case == "process":
                    receipts = json.loads(
                        (replay / "process-receipts.json").read_text(encoding="utf-8")
                    )
                    old_digest = receipts[0]["digest"]
                    receipts[0]["context_envelope_digest"] = fake_digest
                    _reseal_json(receipts[0])
                    replay_summary["process_receipt_digests"] = [
                        receipts[0]["digest"] if item == old_digest else item
                        for item in replay_summary["process_receipt_digests"]
                    ]
                    _write_json(replay / "process-receipts.json", receipts)
                elif case == "result":
                    binding = replay_summary["task_bindings"][0]
                    task_id = binding["task_id"]
                    result_path = replay / "process-outputs" / f"{task_id}.json"
                    result = _read_json(result_path)
                    result["formation_receipt_digest"] = fake_digest
                    _write_json(result_path, result)
                    new_result_digest = sha256_digest(result)
                    binding["observed_result_digest"] = new_result_digest
                    _reseal_json(binding)
                    _write_json(
                        replay / "agentteams/bindings" / f"{task_id}.json",
                        binding,
                    )
                    receipts = json.loads(
                        (replay / "process-receipts.json").read_text(encoding="utf-8")
                    )
                    process_receipt = next(
                        item for item in receipts if item["task_id"] == task_id
                    )
                    old_digest = process_receipt["digest"]
                    process_receipt["output_digest"] = new_result_digest
                    _reseal_json(process_receipt)
                    replay_summary["process_receipt_digests"] = [
                        process_receipt["digest"] if item == old_digest else item
                        for item in replay_summary["process_receipt_digests"]
                    ]
                    _write_json(replay / "process-receipts.json", receipts)
                elif case == "prepared":
                    prepared_path = replay / "prepared-formation-bundle.json"
                    replay_prepared = _read_json(prepared_path)
                    replay_prepared["event_payload"]["context_envelope_digest"] = (
                        fake_digest
                    )
                    _reseal_json(replay_prepared)
                    replay_summary["prepared_formation_digest"] = replay_prepared[
                        "digest"
                    ]
                    _write_json(prepared_path, replay_prepared)
                else:  # pragma: no cover - the closed matrix above owns cases
                    raise AssertionError(case)
                _reseal_json(replay_summary)
                _write_json(replay / "summary.json", replay_summary)
                return replay_summary

            return runner

        original_runner = service._competition_runner
        original_evidence_root = service.competition_evidence_root
        service.competition_evidence_root = tmp_path / "tampered-replays"
        for case, expected_error in expected_failures.items():
            service._competition_runner = tampered_replay(case)
            with pytest.raises(RuntimeError, match=rf"^{expected_error}$"):
                service._prepare_golden_competition(
                    service.profile.task_request(),
                    task_formation_decision_receipt=(
                        formation_root.model_dump(mode="json")
                    ),
                    context_envelope=context_root.model_dump(mode="json"),
                )
            assert service.state()["competition_evidence"] == evidence
            assert service.state()["quote"]["version"] == "v1"
        service._competition_runner = original_runner
        service.competition_evidence_root = original_evidence_root

        persisted = service.store.load_artifact(
            GOLDEN_COMPETITION_ARTIFACT_ID,
            GOLDEN_COMPETITION_MEDIA_TYPE,
        )
        assert persisted.payload == evidence
        event_types = [item["event_type"] for item in service.store.event_envelopes()]
        assert GOLDEN_COMPETITION_EVENT_TYPE in event_types
        golden_event = next(
            item
            for item in service.store.event_envelopes()
            if item["event_type"] == GOLDEN_COMPETITION_EVENT_TYPE
        )["payload"]
        assert golden_event["oac_activation_binding_digest"] == activation_binding.digest
        assert golden_event["task_formation_decision_receipt_digest"] == (
            formation_digest
        )
        assert golden_event["task_agent_context_envelope_digest"] == context_digest
        assert golden_event["agentteams_execution_plan_digest"] == plan_digest
        assert golden_event["topology_match"] is True
        assert service.export_evidence()["competition_evidence"] == evidence

        preview = service.preview_change("launch_date")
        assert preview.run_envelope.run_id == run_id
        launch_advisory = service.state()["agentteams_operations"][
            "change_set_advisories"
        ]["launch_date"]
        assert launch_advisory["participation_status"] == (
            "CONTROLLED_LOCAL_NATIVE_OBSERVED"
        )
        assert launch_advisory["native_agentteams_observed"] is True
        assert launch_advisory["live_agentteams_observed"] is False
        native_change = launch_advisory["native_execution"]
        assert native_change["status"] == "COMPLETED"
        assert native_change["binding"]["change_set_digest"] == preview.change_set.digest
        assert launch_advisory["run_id"] == preview.run_envelope.run_id
        assert launch_advisory["target_writes"] == 0
        assert service.current_quote().version == "v1"
        launch_approval = service.approve_change(
            "launch_date",
            actor_id=service.change_owner["launch_date"],
            preview_digest=preview.preview.digest,
        )
        launch_outcome = service.apply_approved_change(
            "launch_date",
            approval_digest=launch_approval["approval_digest"],
        )
        assert launch_outcome["outcome"]["rebase_receipt"]["workflow_run_id"] == run_id
        currency_preview = service.preview_change("currency")
        assert currency_preview.run_envelope.run_id == run_id
        currency_approval = service.approve_change(
            "currency",
            actor_id=service.change_owner["currency"],
            preview_digest=currency_preview.preview.digest,
        )
        currency_outcome = service.apply_approved_change(
            "currency",
            approval_digest=currency_approval["approval_digest"],
        )
        assert currency_outcome["outcome"]["rebase_receipt"]["workflow_run_id"] == run_id
        assert service.state()["quote"]["version"] == "v3"
    finally:
        adaptation.store.close()
        service.close()

    def must_not_rerun(**_: Any) -> dict[str, Any]:
        raise AssertionError("restart must read persisted Golden evidence")

    reopened = _service(tmp_path, mode="golden", runner=must_not_rerun)
    try:
        state = reopened.state()
        assert state["competition_evidence"]["run_id"] == run_id
        assert state["execution"]["run_id"] == run_id
        assert state["quote"]["version"] == "v3"
    finally:
        reopened.close()

    resetter = _service(tmp_path, mode="golden")
    try:
        reset_state = resetter.reset()["state"]
        assert reset_state["quote"] is None
        assert reset_state["competition_evidence"] is None
        rerun = resetter.form_quote_with_dependency_evidence()["state"]
        assert rerun["competition_evidence"]["run_id"] != run_id
        assert sorted(path.name for path in (tmp_path / "golden").iterdir()) == [
            "change-taskflows",
            "run-00001",
            "run-00002",
        ]
    finally:
        resetter.close()


def test_competition_off_preserves_legacy_not_run_boundary(tmp_path: Path) -> None:
    service = _service(tmp_path, mode="off")
    try:
        result = service.form_quote_with_dependency_evidence()
        state = result["state"]
        assert state["quote"]["version"] == "v1"
        assert state["execution"]["mode"] == "LOCAL_DETERMINISTIC"
        assert state["execution"]["candidate_runtime"] == ("DETERMINISTIC_DOMAIN_PROVIDERS")
        assert state["execution"]["agentteams"] == "NOT_RUN"
        assert "competition_evidence" not in state
        assert "competition_evidence" not in service.export_evidence()
    finally:
        service.close()


def test_golden_not_run_fails_closed_without_forming_quote(tmp_path: Path) -> None:
    runtime = load_enterprise_quote_pilot_pack(PACK)
    correlation_id = enterprise_quote_pilot_run_id(runtime)

    def missing_ollama_runner(**kwargs: Any) -> dict[str, Any]:
        output = Path(kwargs["output_dir"])
        body = {
            "schema_version": "orgrebase.golden-competition-summary.v1",
            "status": "NOT_RUN",
            "reason": "EXACT_LOCAL_OLLAMA_REVIEWER_NOT_AVAILABLE",
            "run_id": "run:golden-competition:missing-ollama",
            "correlation_id": correlation_id,
            "canonical_target_writes": 0,
        }
        summary = {**body, "digest": sha256_digest(body)}
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary

    service = _service(tmp_path, mode="golden", runner=missing_ollama_runner)
    try:
        with pytest.raises(
            RuntimeError,
            match="WORKSPACE_GOLDEN_COMPETITION_NOT_PASS:EXACT_LOCAL_OLLAMA",
        ):
            service.form_quote_with_dependency_evidence()
        assert service.state()["quote"] is None
        assert service.state()["competition_evidence"] is None
        assert not service.store.artifact_exists(GOLDEN_COMPETITION_ARTIFACT_ID)
        assert GOLDEN_COMPETITION_EVENT_TYPE not in {
            item["event_type"] for item in service.store.event_envelopes()
        }
    finally:
        service.close()


def test_enterprise_pilot_cli_and_wrapper_default_to_golden(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import uvicorn

    import orgrebase.api as api_module
    import orgrebase.workspace.service as service_module

    captured: dict[str, Any] = {}

    class CapturingWorkspaceService:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.approval_identity_mode = kwargs["approval_identity_mode"]

    monkeypatch.setattr(service_module, "WorkspaceService", CapturingWorkspaceService)
    monkeypatch.setattr(api_module, "create_app", lambda **_: object())
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orgrebase",
            "enterprise-pilot-start",
            "--pack",
            str(PACK),
            "--store",
            str(tmp_path / "workspace.sqlite3"),
        ],
    )

    cli.main()

    assert captured["competition_mode"] == "golden"
    assert captured["competition_evidence_root"] == tmp_path / "golden-competition"
    assert captured["competition_pack_path"] == PACK
    wrapper = (ROOT / "run-enterprise-pilot.sh").read_text(encoding="utf-8")
    assert 'competition_mode="golden"' in wrapper
    assert '--competition-mode "$competition_mode"' in wrapper
