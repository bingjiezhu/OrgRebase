from __future__ import annotations

import json
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore
from orgrebase.workspace.models import ModelResponseReceipt
from orgrebase.workspace.oac_agent_adaptation import (
    OACAgentMappingReceipt,
    OACAgentModelObservation,
    OACAgentValidationCheck,
    OACAgentValidationReceipt,
    expected_oac_agent_candidate,
    run_oac_agent_adaptation,
)
from orgrebase.workspace.oac_agentic_runtime import (
    OACAgenticRuntime,
    OACAgenticRuntimeError,
)
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACAdaptationReviewGatePending,
    OACAdapterActivationBinding,
    OACAdapterCapsule,
    OACOwnerReviewGate,
    OACOwnerReviewSummary,
    OACQuoteAdaptationService,
    OACSourceAdmissionApproval,
)
from orgrebase.workspace.oac_shadow_execution import (
    OACBoundShadowExecutionReceipt,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples/enterprise-quote-pilot/evergreen"
LIVE_RECEIPT = ROOT / "evidence/oac-agentic-adaptation/latest/mapping-receipt.json"
OAC_ROOT = ROOT.parent / "oac-spec"


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _review_subject(view: dict[str, Any]) -> dict[str, object]:
    adaptation = view.get("adaptation") if "adaptation" in view else view
    assert isinstance(adaptation, dict)
    return {
        "owner_review_summary_digest": adaptation["owner_review_summary_digest"],
        "acknowledgements": OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    }


class _ContractModelProvider:
    """Inject provider-contract data; this fixture is not live model evidence."""

    def __init__(self, candidate):
        self.candidate = candidate

    def generate_structured(self, *, request, output_model):
        value = self.candidate.model_dump(mode="json")
        return ModelResponseReceipt(
            id=f"model-response:{request.request_id}:test", request_ref=request.request_id,
            request_digest=request.digest, status="VALID", value=value,
            output_digest=sha256_digest(value), provider_request_id="test-provider-response",
            provider="vertex-ai", model_id="gemini-3.7-flash", model_version="gemini-3.7-flash",
            schema_valid=True, input_tokens=1, output_tokens=1, latency_ms=0,
            finish_reason="STOP", seed_supported=False, completed_at="2026-08-28T00:00:00Z",
            evidence_class="LIVE_MODEL",
        )


@cache
def _current_mapping_payload() -> dict[str, Any]:
    """Run the pinned native lifecycle once; never relabel frozen v1.2.2 evidence."""

    pack = load_enterprise_quote_pilot_pack(PACK)
    with tempfile.TemporaryDirectory(prefix="orgrebase-oac-runtime-test-") as temporary:
        root = Path(temporary)
        adaptation = OACQuoteAdaptationService(
            store=StateStore(root / "oac.sqlite3"), profile=pack.profile,
            runtime=pack, oac_root=OAC_ROOT, wall_clock=_Clock(),
        )
        try:
            baseline, _ = adaptation.deterministic_mapping_baseline()
            candidate = expected_oac_agent_candidate(
                profile=pack.profile, runtime=pack, baseline_mappings=baseline,
                adaptation_run_id=adaptation.adaptation_run_id,
            )
            receipt = run_oac_agent_adaptation(
                checkout=default_agentteams_checkout(ROOT),
                lock_path=ROOT / "agentteams/teamharness-lock.json", output_dir=root / "mapping",
                profile=pack.profile, runtime=pack, baseline_mappings=baseline,
                adaptation_run_id=adaptation.adaptation_run_id,
                provider=_ContractModelProvider(candidate),
            )
            return receipt.model_dump(mode="json")
        finally:
            adaptation.store.close()


def _current_mapping_receipt() -> OACAgentMappingReceipt:
    return OACAgentMappingReceipt.model_validate(_current_mapping_payload())


def _historical_mapping_receipt() -> OACAgentMappingReceipt:
    return OACAgentMappingReceipt.model_validate(json.loads(LIVE_RECEIPT.read_text(encoding="utf-8")))


def _hold_receipt() -> OACAgentMappingReceipt:
    live = _current_mapping_receipt()
    observation_payload = live.model_observation.model_dump(mode="json", exclude={"digest"})
    observation_payload.update(
        {
            "status": "NOT_RUN",
            "evidence_class": "NOT_RUN",
            "provider_request_id": None,
            "output_digest": None,
            "error_code": "VERTEX_CREDENTIALS_MISSING",
            "finish_reason": None,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    observation = OACAgentModelObservation.model_validate(observation_payload)
    validation = OACAgentValidationReceipt(
        verdict="HOLD",
        checks=(
            OACAgentValidationCheck(
                check_id="live-provider-response",
                passed=False,
                reason_code="VERTEX_CREDENTIALS_MISSING",
            ),
        ),
        reason_codes=("VERTEX_CREDENTIALS_MISSING",),
    )
    return OACAgentMappingReceipt(
        status="HOLD",
        adaptation_run_id=live.adaptation_run_id,
        profile_digest=live.profile_digest,
        pack_digest=live.pack_digest,
        baseline_mapping_set_digest=live.baseline_mapping_set_digest,
        input_digest=live.input_digest,
        model_observation=observation,
        native_agentteams=live.native_agentteams,
        validation=validation,
    )


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _resign(model_type: Any, value: dict[str, Any], **updates: Any) -> Any:
    payload = json.loads(json.dumps(value, ensure_ascii=False))
    payload.pop("digest", None)
    payload.update(updates)
    return model_type.model_validate(payload)


class _AgentRunner:
    def __init__(self, receipt: OACAgentMappingReceipt) -> None:
        self.receipt = receipt
        self.calls = 0

    def __call__(self, **kwargs: Any) -> OACAgentMappingReceipt:
        self.calls += 1
        return self.receipt


class _ShadowRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.kwargs: dict[str, Any] | None = None

    def __call__(self, **kwargs: Any) -> OACBoundShadowExecutionReceipt:
        self.calls += 1
        self.kwargs = kwargs
        capsule = kwargs["adapter_capsule"]
        activation = kwargs["activation_binding"]
        approval = kwargs["approval"]
        context = kwargs["context_envelope"]
        mapping = kwargs["agent_mapping_receipt"]
        review_gate = approval["review_gate"]
        receipt = OACBoundShadowExecutionReceipt(
            adaptation_run_id=capsule["adaptation_run_id"],
            shadow_execution_run_id=activation["execution_run_id"],
            frozen_golden_run_id="run:frozen-golden:test",
            approval_digest=approval["digest"],
            approval_review_gate_digest=approval["review_gate_digest"],
            review_gate_digest=review_gate["digest"],
            review_duration_ms=review_gate["review_duration_ms"],
            approval_elapsed_since_not_before_ms=(
                approval["approved_at_epoch_ms"] - review_gate["not_before_epoch_ms"]
            ),
            adapter_capsule_digest=capsule["digest"],
            activation_binding_digest=activation["digest"],
            context_envelope_ref=f"{context['id']}@{context['version']}",
            context_envelope_digest=context["digest"],
            context_freshness_basis="LOGICAL_EVENT_TIME",
            context_freshness_checked_at_epoch_ms=1_786_752_000_000,
            context_freshness_checked_at="2026-08-15T00:00:00.000Z",
            agent_mapping_receipt_digest=mapping["digest"],
            profile_digest=capsule["profile_digest"],
            pack_digest=capsule["pack_digest"],
            pre_execution_binding_digest="sha256:" + "1" * 64,
            execution_envelope_digest="sha256:" + "2" * 64,
            competition_summary_digest="sha256:" + "3" * 64,
            agentteams_action_count=9,
            agentteams_action_digests=("sha256:" + "4" * 64,),
            tool_receipt_digest="sha256:" + "5" * 64,
            skill_package_digest="sha256:" + "6" * 64,
            skill_invocation_receipt_digest="sha256:" + "7" * 64,
            prepared_formation_digest="sha256:" + "8" * 64,
            controlled_agentteams_formation_receipt_digest="sha256:" + "9" * 64,
            agentteams_execution_plan_digest="sha256:" + "0" * 64,
            planned_domain_ids=("finance", "gtm", "legal", "product"),
            actual_agentteams_domain_ids=("finance", "gtm", "legal", "product"),
            topology_match=True,
            otlp_export_receipt_digests=(
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "sha256:" + "c" * 64,
            ),
            telemetry_query_receipt_digest="sha256:" + "d" * 64,
            telemetry_alert_receipt_digest="sha256:" + "e" * 64,
            same_run_layers=(
                "SOURCE",
                "CONTEXT",
                "AGENTTEAMS",
                "TOOL",
                "SKILL",
                "OTLP",
                "CANDIDATE",
            ),
            otlp_trace_layers=(
                "SOURCE",
                "AGENTTEAMS",
                "TOOL",
                "SKILL",
                "TERMINAL",
            ),
            frozen_golden_baseline={"run_id": "run:frozen-golden:test"},
            late_attempt_fencing_reference={
                "mode": "CONTROL_MECHANISM_REFERENCE_NOT_SAME_RUN",
                "canonical_target_writes": 0,
            },
            pack_file_manifest={"pack.json": "sha256:" + "f" * 64},
            output_manifest={},
        )
        _write(
            Path(kwargs["output_dir"]) / "receipt.json",
            receipt.model_dump(mode="json"),
        )
        return receipt


class _ShadowVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        receipt = json.loads((Path(kwargs["root"]) / "receipt.json").read_text(encoding="utf-8"))
        result = {
            "schema_version": "orgrebase.oac-bound-shadow-verification.v1",
            "status": "PASS",
            "shadow_execution_run_id": receipt["shadow_execution_run_id"],
            "failure_count": 0,
            "failures": [],
            "checked_output_file_count": 105,
            "checked_pack_file_count": 7,
            "canonical_target_writes": 0,
        }
        result["digest"] = sha256_digest(result)
        return result


def _runtime(
    tmp_path: Path,
    *,
    agent_runner: _AgentRunner,
    shadow_runner: _ShadowRunner | None = None,
    shadow_verifier: _ShadowVerifier | None = None,
    execution_mode: str = "LIVE_VERTEX",
    shadow_model_provider: str = "vertex-ai",
):
    pack = load_enterprise_quote_pilot_pack(PACK)
    clock = _Clock()
    adaptation = OACQuoteAdaptationService(
        store=StateStore(tmp_path / "oac.sqlite3"),
        profile=pack.profile,
        runtime=pack,
        oac_root=OAC_ROOT,
        wall_clock=clock,
        execution_run_id="run:oac-bound-shadow:agentic-test",
    )
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite3",
        runtime_configuration=pack,
    )
    coordinator = OACAgenticRuntime(
        runtime_root=tmp_path / "runtime",
        repo_root=ROOT,
        checkout=default_agentteams_checkout(ROOT),
        lock_path=ROOT / "agentteams/teamharness-lock.json",
        pack_path=PACK,
        frozen_golden_root=ROOT / "evidence/golden-competition/latest/pilot",
        adaptation_service=adaptation,
        workspace_service=workspace,
        agent_runner=agent_runner,
        shadow_runner=shadow_runner or _ShadowRunner(),
        shadow_verifier=shadow_verifier,
        execution_mode=execution_mode,
        shadow_model_provider=shadow_model_provider,
    )
    return coordinator, adaptation, workspace, clock


def test_execution_policy_defaults_to_live_vertex_for_direct_runtime_construction(
    tmp_path: Path,
) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(
        tmp_path,
        agent_runner=runner,
    )
    try:
        policy = coordinator.view()["execution_policy"]

        assert policy == {
            "mode": "LIVE_VERTEX",
            "mapping_source": "NEW_LIVE_VERTEX",
            "mapping_model_provider": "vertex-ai",
            "model_provider": "vertex-ai",
            "mapping_will_call_external_model": True,
            "shadow_will_call_external_model": True,
            "available_actions": ["AGENT_PREPARE"],
            "blocked_reasons": {"EXECUTE_SHADOW": ["OAC_AGENTIC_RUNTIME_LIVE_MAPPING_REQUIRED"]},
        }
        assert "project" not in json.dumps(policy).lower()
        assert "endpoint" not in json.dumps(policy).lower()
    finally:
        adaptation.store.close()
        workspace.close()


@pytest.mark.parametrize(
    ("execution_mode", "mapping_code", "shadow_code"),
    (
        (
            "FROZEN_REPLAY",
            "OAC_AGENTIC_RUNTIME_FROZEN_MAPPING_RECEIPT_REQUIRED",
            "OAC_AGENTIC_RUNTIME_FROZEN_SHADOW_RECEIPT_REQUIRED",
        ),
        (
            "OFFLINE_LOCAL",
            "OAC_AGENTIC_RUNTIME_NEW_VERTEX_MAPPING_BLOCKED",
            "OAC_AGENTIC_RUNTIME_NEW_VERTEX_SHADOW_BLOCKED",
        ),
    ),
)
def test_non_live_modes_block_new_vertex_calls_before_runner_dispatch(
    tmp_path: Path,
    execution_mode: str,
    mapping_code: str,
    shadow_code: str,
) -> None:
    agent_runner = _AgentRunner(_current_mapping_receipt())
    shadow_runner = _ShadowRunner()
    coordinator, adaptation, workspace, _clock = _runtime(
        tmp_path,
        agent_runner=agent_runner,
        shadow_runner=shadow_runner,
        execution_mode=execution_mode,
    )
    try:
        policy = coordinator.view()["execution_policy"]
        assert policy["mode"] == execution_mode
        assert policy["mapping_will_call_external_model"] is False
        assert policy["shadow_will_call_external_model"] is False
        assert policy["available_actions"] == (
            ["STRUCTURED_PREPARE"] if execution_mode == "OFFLINE_LOCAL" else []
        )
        assert policy["blocked_reasons"] == {
            "AGENT_PREPARE": [mapping_code],
            "EXECUTE_SHADOW": [shadow_code],
        }

        with pytest.raises(OACAgenticRuntimeError, match=f"^{mapping_code}$"):
            coordinator.agent_prepare(command_id="command:blocked@1")
        with pytest.raises(OACAgenticRuntimeError, match=f"^{shadow_code}$"):
            coordinator.execute_shadow()
        assert agent_runner.calls == 0
        assert shadow_runner.calls == 0
    finally:
        adaptation.store.close()
        workspace.close()


@pytest.mark.parametrize("provider", ["deepseek", "unqualified-cloud-provider"])
def test_offline_mode_rejects_nonlocal_shadow_before_any_runner(tmp_path: Path, provider: str) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    shadow = _ShadowRunner()
    coordinator, adaptation, workspace, _ = _runtime(
        tmp_path, agent_runner=runner, shadow_runner=shadow,
        execution_mode="OFFLINE_LOCAL", shadow_model_provider=provider,
    )
    try:
        policy = coordinator.view()["execution_policy"]
        assert policy["shadow_will_call_external_model"] is False
        assert policy["blocked_reasons"]["EXECUTE_SHADOW"] == [
            "OAC_AGENTIC_RUNTIME_NEW_NONLOCAL_SHADOW_BLOCKED"]
        with pytest.raises(OACAgenticRuntimeError, match=r"^OAC_AGENTIC_RUNTIME_NEW_NONLOCAL_SHADOW_BLOCKED$"):
            coordinator.execute_shadow()
        assert runner.calls == 0 and shadow.calls == 0
        assert not coordinator._shadow_path.exists()
    finally:
        adaptation.store.close()
        workspace.close()


def test_explicit_live_vertex_mode_discloses_deepseek_shadow_as_external(tmp_path: Path) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, _ = _runtime(
        tmp_path, agent_runner=runner, execution_mode="LIVE_VERTEX", shadow_model_provider="deepseek",
    )
    try:
        policy = coordinator.view()["execution_policy"]
        assert policy["mode"] == "LIVE_VERTEX" and policy["mapping_model_provider"] == "vertex-ai"
        assert policy["mapping_will_call_external_model"] is True
        assert policy["model_provider"] == "deepseek" and policy["shadow_will_call_external_model"] is True
        assert runner.calls == 0
    finally:
        adaptation.store.close()
        workspace.close()


def test_offline_structured_candidate_still_requires_exact_owner_review(tmp_path: Path) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path, agent_runner=runner, execution_mode="OFFLINE_LOCAL",
        shadow_model_provider="ollama-local",
    )
    try:
        initial = coordinator.view()
        assert initial["execution_policy"]["mapping_source"] == "STRUCTURED_MATERIALS"
        assert initial["execution_policy"]["mapping_model_provider"] == "none"
        pending = adaptation.prepare(command_id="command:structured-prepare@1")
        view = coordinator.view()
        assert view["status"] == "OWNER_REVIEW_PENDING"
        assert view["adaptation"]["candidate_mappings"] == pending["candidate_mappings"]
        assert len(view["adaptation"]["candidate_mappings"]) == 5
        assert view["adaptation"]["gaps"] == pending["gaps"]
        assert view["adaptation"]["approval_digest"] is None
        assert "STRUCTURED_PREPARE" not in view["execution_policy"]["available_actions"]
        assert "EXECUTE_SHADOW" not in view["execution_policy"]["available_actions"]
        with pytest.raises(OACAdaptationReviewGatePending):
            adaptation.approve(
                actor_id=pending["human_authority_ref"], candidate_digest=pending["candidate_digest"],
                command_id="command:structured-early-approve@1", **_review_subject(view),
            )
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["human_authority_ref"], candidate_digest=pending["candidate_digest"],
            command_id="command:structured-approve@1", **_review_subject(view),
        )
        assert coordinator.view()["adaptation"]["approval_digest"] == ready["approval_digest"]
        assert ready["canonical_target_writes"] == 0
        assert runner.calls == 0
        assert not coordinator._mapping_path.exists()
    finally:
        adaptation.store.close()
        workspace.close()


def test_agent_prepare_is_idempotent_and_never_repeats_live_provider(
    tmp_path: Path,
) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(tmp_path, agent_runner=runner)
    try:
        first = coordinator.agent_prepare(command_id="command:agent-prepare@1")
        second = coordinator.agent_prepare(command_id="command:agent-prepare@1")

        assert runner.calls == 1
        assert first == second
        assert first["agent_mapping"]["status"] == "VALIDATED_CANDIDATE"
        assert first["agent_mapping"]["provider_request_observed"] is True
        assert first["adaptation"]["status"] == "OWNER_REVIEW_PENDING"
        assert first["adaptation"]["review_remaining_ms"] == 4000
        assert first["adaptation"]["review_not_before"] is not None
        assert len(first["agent_mapping"]["accepted_mappings"]) == 5
        assert set(first["agent_mapping"]["accepted_mappings"][0]) == {
            "component_kind",
            "source_root_ref",
            "target_oac_paths",
            "declared_unknowns",
            "reason_codes",
            "output_digest",
        }
        assert first["context_residency"]["status"] == "WAITING_FOR_OWNER_APPROVAL"
        assert first["shadow_execution"]["status"] == "NOT_RUN"
    finally:
        adaptation.store.close()
        workspace.close()


def test_live_hold_is_replayed_without_deterministic_fallback(
    tmp_path: Path,
) -> None:
    runner = _AgentRunner(_hold_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(tmp_path, agent_runner=runner)
    try:
        first = coordinator.agent_prepare(command_id="command:agent-hold@1")
        second = coordinator.agent_prepare(command_id="command:agent-hold@1")

        assert runner.calls == 1
        assert first == second
        assert first["agent_mapping"]["status"] == "HOLD"
        assert first["agent_mapping"]["model_status"] == "NOT_RUN"
        assert first["adaptation"]["status"] == "PACK_OBSERVED"
        assert first["adaptation"]["deterministic_fallback_used"] is False
        with pytest.raises(
            OACAgenticRuntimeError,
            match="OAC_AGENTIC_RUNTIME_LIVE_MAPPING_REQUIRED",
        ):
            coordinator.execute_shadow()
    finally:
        adaptation.store.close()
        workspace.close()


def test_execute_shadow_compiles_actual_formation_context_after_owner_approval(
    tmp_path: Path,
) -> None:
    agent_runner = _AgentRunner(_current_mapping_receipt())
    shadow_runner = _ShadowRunner()
    shadow_verifier = _ShadowVerifier()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path,
        agent_runner=agent_runner,
        shadow_runner=shadow_runner,
        shadow_verifier=shadow_verifier,
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:agent-prepare@1")
        with pytest.raises(OACAdaptationReviewGatePending):
            adaptation.approve(
                actor_id=pending["adaptation"]["owner_ref"],
                candidate_digest=pending["adaptation"]["candidate_digest"],
                command_id="command:too-early@1",
                **_review_subject(pending),
            )
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:owner-approve@1",
            **_review_subject(pending),
        )
        ready_view = coordinator.view()
        lineage_proof = ready_view["adaptation"]["lineage_proof"]
        summary_digest = ready["owner_review_summary"]["digest"]
        assert lineage_proof == {
            "owner_review_summary_digest": summary_digest,
            "review_gate_owner_review_summary_digest": summary_digest,
            "approval_owner_review_summary_digest": summary_digest,
            "adapter_capsule_owner_review_summary_digest": summary_digest,
            "approval_digest": ready["approval"]["digest"],
            "adapter_capsule_digest": ready["adapter_capsule"]["digest"],
            "activation_binding_digest": ready["activation_binding"]["digest"],
        }

        first = coordinator.execute_shadow()
        second = coordinator.execute_shadow()

        assert shadow_runner.calls == 1
        assert shadow_verifier.calls == 1
        assert first == second
        assert first["context_residency"]["status"] == "READY"
        assert first["adaptation"]["review_gate_digest"] == (ready["review_gate"]["digest"])
        assert first["adaptation"]["review_duration_ms"] == 4000
        assert first["adaptation"]["approved_at"] == ready["approval"]["approved_at"]
        assert first["adaptation"]["approval_elapsed_since_not_before_ms"] == 0
        assert (
            first["context_residency"]["organizational_intent_digest"]
            == ready["adapter_capsule"]["organizational_demand_digest"]
        )
        assert first["adaptation"]["organizational_demand"] == {
            "objective": "produce one governed enterprise quote with zero external effects",
            "accountable_role_ref": "role:enterprise-quote-operator",
            "effect_ceiling": "zero_effect",
            "evidence_obligation_count": 5,
        }
        assert len(first["context_residency"]["resident_domain_contracts"]) == 4
        assert len(first["context_residency"]["task_projections"]) == 4
        assert first["context_residency"]["return_to_control_plane"] == {
            "candidate_only": True,
            "canonical_target_writes": 0,
            "authority": "ORGREBASE_CONTROL_PLANE",
        }
        assert first["shadow_execution"]["status"] == "COMPLETED"
        assert first["shadow_execution"]["same_run_layers"] == [
            "SOURCE",
            "CONTEXT",
            "AGENTTEAMS",
            "TOOL",
            "SKILL",
            "OTLP",
            "CANDIDATE",
        ]
        assert first["shadow_execution"]["canonical_target_writes"] == 0
        assert first["shadow_execution"]["agentteams_execution_plan_digest"] == ("sha256:" + "0" * 64)
        assert first["shadow_execution"]["planned_domain_ids"] == [
            "finance",
            "gtm",
            "legal",
            "product",
        ]
        assert first["shadow_execution"]["actual_agentteams_domain_ids"] == [
            "finance",
            "gtm",
            "legal",
            "product",
        ]
        assert first["shadow_execution"]["topology_match"] is True
        assert first["shadow_execution"]["independent_verification"] == {
            "status": "PASS",
            "checked_output_file_count": 105,
            "checked_pack_file_count": 7,
            "failure_count": 0,
            "digest": first["shadow_execution"]["independent_verification"]["digest"],
        }
        assert first["shadow_execution"]["context_freshness_basis"] == ("LOGICAL_EVENT_TIME")
        assert first["shadow_execution"]["context_freshness_checked_at"] == ("2026-08-15T00:00:00.000Z")
        assert shadow_runner.kwargs is not None
        assert shadow_runner.kwargs["model_provider"] == "vertex-ai"
        assert shadow_runner.kwargs["context_validation_time"] == (workspace.formation.clock.now())
        assert (
            shadow_runner.kwargs["agent_mapping_receipt"]["accepted_mapping_set_digest"]
            == shadow_runner.kwargs["adapter_capsule"]["mapping_set_digest"]
        )
        encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
        assert "/Users/" not in encoded
        assert "provider_request_id" not in encoded
        assert "credential" not in encoded.lower()
    finally:
        adaptation.store.close()
        workspace.close()


def test_approved_deterministic_oac_compiles_current_task_roots_without_agent_mapping(
    tmp_path: Path,
) -> None:
    """The approved contract is authoritative; model-assisted mapping is optional."""

    pack = load_enterprise_quote_pilot_pack(PACK)
    workspace = WorkspaceService(
        store_path=tmp_path / "workspace.sqlite3",
        runtime_configuration=pack,
    )
    clock = _Clock()
    adaptation = OACQuoteAdaptationService(
        store=StateStore(tmp_path / "oac.sqlite3"),
        profile=pack.profile,
        runtime=pack,
        oac_root=OAC_ROOT,
        wall_clock=clock,
        execution_run_id=workspace.effective_workflow_run_id,
    )
    coordinator = OACAgenticRuntime(
        runtime_root=tmp_path / "runtime",
        repo_root=ROOT,
        checkout=default_agentteams_checkout(ROOT),
        lock_path=ROOT / "agentteams/teamharness-lock.json",
        pack_path=PACK,
        frozen_golden_root=ROOT / "evidence/golden-competition/latest/pilot",
        adaptation_service=adaptation,
        workspace_service=workspace,
        execution_mode="OFFLINE_LOCAL",
        shadow_model_provider="ollama-local",
    )
    try:
        pending = adaptation.prepare(command_id="command:deterministic-prepare@1")
        assert not (tmp_path / "runtime/agent-mapping/mapping-receipt.json").exists()
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["human_authority_ref"],
            candidate_digest=pending["candidate_digest"],
            command_id="command:deterministic-approve@1",
            owner_review_summary_digest=pending["owner_review_summary"]["digest"],
            acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
        )

        binding_digest = ready["activation_binding"]["digest"]
        first_receipt, first_context = coordinator.formation_roots_for_current_task(
            expected_activation_binding_digest=binding_digest,
        )
        second_receipt, second_context = coordinator.formation_roots_for_current_task(
            expected_activation_binding_digest=binding_digest,
        )

        assert ready["status"] == "READY_FOR_ORGREBASE"
        assert not (tmp_path / "runtime/agent-mapping/mapping-receipt.json").exists()
        assert first_receipt == second_receipt
        assert first_context == second_context
        assert first_context.task_formation_decision_receipt_digest == first_receipt.digest
        assert first_context.admitted_organizational_intent_digest == (
            first_receipt.organizational_demand_digest
        )
        assert first_receipt.task_ref == workspace.profile.default_task.id
        assert first_context.task_ref == workspace.profile.default_task.id
        assert first_receipt.selected_domain_ids == tuple(
            item.domain_id for item in first_context.domain_bindings
        )
        assert first_receipt.candidate_only is True
        assert first_context.candidate_only is True
        assert first_receipt.canonical_target_writes == 0
        assert first_context.canonical_target_writes == 0
        assert (tmp_path / "runtime/context-residency/task-formation-decision-receipt.json").is_file()
        assert (tmp_path / "runtime/context-residency/context-envelope.json").is_file()
    finally:
        adaptation.store.close()
        workspace.close()


def test_frozen_mode_replays_retained_mapping_and_shadow_without_runner_calls(
    tmp_path: Path,
) -> None:
    agent_runner = _AgentRunner(_current_mapping_receipt())
    shadow_runner = _ShadowRunner()
    shadow_verifier = _ShadowVerifier()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path,
        agent_runner=agent_runner,
        shadow_runner=shadow_runner,
        shadow_verifier=shadow_verifier,
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:prepare@1")
        clock.advance(4)
        adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:approve@1",
            **_review_subject(pending),
        )
        completed = coordinator.execute_shadow()
        assert completed["status"] == "SHADOW_COMPLETED"
        assert (agent_runner.calls, shadow_runner.calls, shadow_verifier.calls) == (1, 1, 1)

        coordinator.execution_mode = "FROZEN_REPLAY"
        replayed_mapping = coordinator.agent_prepare(command_id="command:prepare@1")
        replayed_shadow = coordinator.execute_shadow()

        assert replayed_mapping == replayed_shadow
        assert replayed_shadow["execution_policy"] == {
            "mode": "FROZEN_REPLAY",
            "mapping_source": "RETAINED_VERTEX_RECEIPT",
            "mapping_model_provider": "vertex-ai",
            "model_provider": "vertex-ai",
            "mapping_will_call_external_model": False,
            "shadow_will_call_external_model": False,
            "available_actions": ["AGENT_PREPARE", "EXECUTE_SHADOW"],
            "blocked_reasons": {},
        }
        assert (agent_runner.calls, shadow_runner.calls, shadow_verifier.calls) == (1, 1, 1)
    finally:
        adaptation.store.close()
        workspace.close()


def test_shadow_verification_failure_never_writes_pass_or_reruns_shadow(
    tmp_path: Path,
) -> None:
    shadow_runner = _ShadowRunner()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path,
        agent_runner=_AgentRunner(_current_mapping_receipt()),
        shadow_runner=shadow_runner,
    )
    coordinator.shadow_verifier = lambda **_kwargs: {"status": "PASS"}
    try:
        pending = coordinator.agent_prepare(command_id="command:agent-prepare@1")
        clock.advance(4)
        adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:owner-approve@1",
            **_review_subject(pending),
        )
        for _attempt in range(2):
            with pytest.raises(
                OACAgenticRuntimeError,
                match="OAC_AGENTIC_RUNTIME_SHADOW_VERIFICATION_INVALID",
            ):
                coordinator.execute_shadow()
        assert shadow_runner.calls == 1
        assert (tmp_path / "runtime/shadow-execution/receipt.json").is_file()
        assert not (tmp_path / "runtime/shadow-execution/verification.json").exists()
    finally:
        adaptation.store.close()
        workspace.close()


def test_execute_shadow_rejects_unapproved_or_cross_candidate_state(
    tmp_path: Path,
) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(tmp_path, agent_runner=runner)
    try:
        coordinator.agent_prepare(command_id="command:agent-prepare@1")
        with pytest.raises(
            OACAgenticRuntimeError,
            match="OAC_AGENTIC_RUNTIME_OWNER_APPROVAL_REQUIRED",
        ):
            coordinator.execute_shadow()

        # A deterministic draft created first cannot be silently replaced by
        # the live candidate lineage in another coordinator runtime.
        other_root = tmp_path / "other"
        other_root.mkdir()
        pack = load_enterprise_quote_pilot_pack(PACK)
        other_service = OACQuoteAdaptationService(
            store=StateStore(other_root / "oac.sqlite3"),
            profile=pack.profile,
            runtime=pack,
            oac_root=OAC_ROOT,
            wall_clock=_Clock(),
        )
        try:
            other_service.prepare(command_id="command:deterministic-first@1")
            other_workspace = WorkspaceService(
                store_path=other_root / "workspace.sqlite3",
                runtime_configuration=pack,
            )
            try:
                other = OACAgenticRuntime(
                    runtime_root=other_root / "runtime",
                    repo_root=ROOT,
                    checkout=default_agentteams_checkout(ROOT),
                    lock_path=ROOT / "agentteams/teamharness-lock.json",
                    pack_path=PACK,
                    frozen_golden_root=ROOT / "evidence/golden-competition/latest/pilot",
                    adaptation_service=other_service,
                    workspace_service=other_workspace,
                    agent_runner=_AgentRunner(_current_mapping_receipt()),
                    shadow_runner=_ShadowRunner(),
                )
                with pytest.raises(
                    RuntimeError,
                    match="OAC_ADAPTATION_AGENT_MAPPING_COMMAND_CONFLICT",
                ):
                    other.agent_prepare(command_id="command:live-after-deterministic@1")
            finally:
                other_workspace.close()
        finally:
            other_service.store.close()
    finally:
        adaptation.store.close()
        workspace.close()


@pytest.mark.parametrize(
    "capsule_field",
    (
        "profile_digest",
        "pack_digest",
        "mapping_set_digest",
        "owner_review_summary_digest",
        "approval_digest",
    ),
)
def test_approved_lineage_rejects_resigned_capsule_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsule_field: str,
) -> None:
    shadow_runner = _ShadowRunner()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path,
        agent_runner=_AgentRunner(_current_mapping_receipt()),
        shadow_runner=shadow_runner,
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:prepare@1")
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:approve@1",
            **_review_subject(pending),
        )
        forged_capsule = _resign(
            OACAdapterCapsule,
            ready["adapter_capsule"],
            **{capsule_field: "sha256:" + "0" * 64},
        )
        binding_updates: dict[str, str] = {
            "adapter_capsule_digest": forged_capsule.digest,
        }
        if capsule_field in {"profile_digest", "pack_digest"}:
            binding_updates[capsule_field] = str(getattr(forged_capsule, capsule_field))
        forged_binding = _resign(
            OACAdapterActivationBinding,
            ready["activation_binding"],
            **binding_updates,
        )
        forged_view = {
            **ready,
            "adapter_capsule": forged_capsule.model_dump(mode="json"),
            "adapter_capsule_digest": forged_capsule.digest,
            "activation_binding": forged_binding.model_dump(mode="json"),
        }
        monkeypatch.setattr(adaptation, "view", lambda: forged_view)

        with pytest.raises(
            OACAgenticRuntimeError,
            match="OAC_AGENTIC_RUNTIME_APPROVED_LINEAGE_MISMATCH",
        ):
            coordinator.execute_shadow()
        assert shadow_runner.calls == 0
    finally:
        adaptation.store.close()
        workspace.close()


def test_approved_lineage_rejects_resigned_summary_with_different_decision_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow_runner = _ShadowRunner()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path,
        agent_runner=_AgentRunner(_current_mapping_receipt()),
        shadow_runner=shadow_runner,
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:prepare@1")
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:approve@1",
            **_review_subject(pending),
        )
        forged_summary = _resign(
            OACOwnerReviewSummary,
            ready["owner_review_summary"],
            decision_owner_ref="role:not-the-approver",
        )
        forged_gate = _resign(
            OACOwnerReviewGate,
            ready["review_gate"],
            owner_review_summary_digest=forged_summary.digest,
        )
        forged_approval = _resign(
            OACSourceAdmissionApproval,
            ready["approval"],
            owner_review_summary_digest=forged_summary.digest,
            review_gate_digest=forged_gate.digest,
            review_gate=forged_gate.model_dump(mode="json"),
        )
        forged_capsule = _resign(
            OACAdapterCapsule,
            ready["adapter_capsule"],
            owner_review_summary_digest=forged_summary.digest,
            approval_digest=forged_approval.digest,
        )
        forged_binding = _resign(
            OACAdapterActivationBinding,
            ready["activation_binding"],
            adapter_capsule_digest=forged_capsule.digest,
        )
        forged_view = {
            **ready,
            "owner_review_summary": forged_summary.model_dump(mode="json"),
            "review_gate": forged_gate.model_dump(mode="json"),
            "review_gate_digest": forged_gate.digest,
            "approval": forged_approval.model_dump(mode="json"),
            "approval_digest": forged_approval.digest,
            "adapter_capsule": forged_capsule.model_dump(mode="json"),
            "adapter_capsule_digest": forged_capsule.digest,
            "activation_binding": forged_binding.model_dump(mode="json"),
        }
        monkeypatch.setattr(adaptation, "view", lambda: forged_view)

        with pytest.raises(
            OACAgenticRuntimeError,
            match="OAC_AGENTIC_RUNTIME_APPROVED_LINEAGE_MISMATCH",
        ):
            coordinator.execute_shadow()
        assert shadow_runner.calls == 0
    finally:
        adaptation.store.close()
        workspace.close()


@pytest.mark.parametrize("execution_mode", ("LIVE_VERTEX", "FROZEN_REPLAY", "OFFLINE_LOCAL"))
def test_partial_runtime_directory_fails_closed_without_repeating_provider(
    tmp_path: Path, execution_mode: str,
) -> None:
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(
        tmp_path, agent_runner=runner, execution_mode=execution_mode,
    )
    try:
        partial = tmp_path / "runtime/agent-mapping"
        partial.mkdir(parents=True)
        _write(partial / "partial.json", {"status": "interrupted"})
        view = coordinator.view()
        assert view["status"] == "AGENT_MAPPING_HOLD"
        policy = view["execution_policy"]
        assert policy["mapping_source"] == "INCOMPLETE_MAPPING_EVIDENCE"
        assert policy["mapping_model_provider"] is None
        assert policy["mapping_will_call_external_model"] is False
        assert policy["available_actions"] == []
        assert policy["blocked_reasons"]["AGENT_PREPARE"] == [
            "OAC_AGENTIC_RUNTIME_MAPPING_EVIDENCE_INCOMPLETE",
        ]
        with pytest.raises(
            OACAgenticRuntimeError,
            match="OAC_AGENTIC_RUNTIME_MAPPING_EVIDENCE_INCOMPLETE",
        ):
            coordinator.agent_prepare(command_id="command:must-not-repeat@1")
        assert runner.calls == 0
    finally:
        adaptation.store.close()
        workspace.close()


def test_retained_candidate_with_missing_runtime_receipt_is_not_structured_materials(tmp_path):
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(tmp_path, agent_runner=runner)
    try:
        pending = coordinator.agent_prepare(command_id="command:retained-candidate@1")
        coordinator._mapping_path.rename(tmp_path / "missing-mapping-receipt.json")
        coordinator.execution_mode = "OFFLINE_LOCAL"
        view = coordinator.view()
        assert view["status"] == "AGENT_MAPPING_HOLD"
        assert view["adaptation"]["candidate_digest"] == pending["adaptation"]["candidate_digest"]
        assert view["execution_policy"]["mapping_source"] == "INCOMPLETE_MAPPING_EVIDENCE"
        assert view["execution_policy"]["mapping_model_provider"] is None
        assert view["execution_policy"]["available_actions"] == []
        assert runner.calls == 1
    finally:
        adaptation.store.close()
        workspace.close()


def test_product_view_rejects_self_consistent_but_cross_lineage_shadow_receipt(
    tmp_path: Path,
) -> None:
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path,
        agent_runner=_AgentRunner(_current_mapping_receipt()),
        shadow_runner=_ShadowRunner(),
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:prepare@1")
        clock.advance(4)
        adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:approve@1",
            **_review_subject(pending),
        )
        coordinator.execute_shadow()

        receipt_path = tmp_path / "runtime/shadow-execution/receipt.json"
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload.pop("digest")
        payload["agent_mapping_receipt_digest"] = "sha256:" + "0" * 64
        forged = OACBoundShadowExecutionReceipt.model_validate(payload)
        _write(receipt_path, forged.model_dump(mode="json"))

        with pytest.raises(
            OACAgenticRuntimeError,
            match="OAC_AGENTIC_RUNTIME_SHADOW_LINEAGE_MISMATCH",
        ):
            coordinator.view()
    finally:
        adaptation.store.close()
        workspace.close()


def test_completed_history_does_not_recompile_sources_after_rebase(tmp_path, monkeypatch):
    shadow = _ShadowRunner()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path, agent_runner=_AgentRunner(_current_mapping_receipt()), shadow_runner=shadow,
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:history-after-rebase@1")
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:approve-history-after-rebase@1", **_review_subject(pending),
        )
        completed = coordinator.execute_shadow()
        files = {path: path.read_bytes() for path in coordinator.runtime_root.rglob("*.json")}
        head = adaptation.store.audit_head()

        def stale_source(_task):
            raise ValueError("SOURCE_PREMISE_NOT_CURRENT")

        monkeypatch.setattr(workspace.formation, "prepare_quote", stale_source)
        assert coordinator.view() == completed
        assert coordinator.execute_shadow() == completed
        assert shadow.calls == 1
        with pytest.raises(ValueError, match="SOURCE_PREMISE_NOT_CURRENT"):
            coordinator._load_or_compile_context(
                organizational_intent_digest=ready["organizational_demand_digest"],
            )
        assert adaptation.store.audit_head() == head
        assert all(path.read_bytes() == raw for path, raw in files.items())

        # A correctly rehashed, unrelated context still cannot be projected
        # as the history of this approved organization.
        payload = json.loads(coordinator._context_path.read_text())
        payload.pop("digest")
        payload["admitted_organizational_intent_digest"] = "sha256:" + "0" * 64
        from orgrebase.workspace.context_residency import TaskAgentContextEnvelope
        forged = TaskAgentContextEnvelope.model_validate(payload)
        _write(coordinator._context_path, forged.model_dump(mode="json"))
        with pytest.raises(OACAgenticRuntimeError, match="OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH"):
            coordinator.view()
    finally:
        adaptation.store.close()
        workspace.close()


def _change_oac_implementation(monkeypatch, module="oac_agent_adaptation.py"):
    from orgrebase.workspace import oac_agent_adaptation

    if module == "agentteams-source":
        identity = oac_agent_adaptation._current_agentteams_mapping_identity()
        monkeypatch.setattr(oac_agent_adaptation, "_current_agentteams_mapping_identity", lambda: {
            **identity, "agentteams_version": "v999.0.0", "agentteams_commit": "f" * 40,
        })
        return
    original = Path.read_bytes
    target = Path(oac_agent_adaptation.__file__).parent / module

    def upgraded(path):
        value = original(path)
        return value + b"\n# changed admission implementation\n" if path == target else value

    monkeypatch.setattr(Path, "read_bytes", upgraded)


@pytest.mark.parametrize("module", ("oac_agent_adaptation.py", "oac_quote_adaptation.py", "oac_agentic_runtime.py", "agentteams-source"))
def test_pending_owner_review_requires_replanning_when_its_gate_implementation_changes(tmp_path, monkeypatch, module):
    runner = _AgentRunner(_current_mapping_receipt())
    coordinator, adaptation, workspace, clock = _runtime(tmp_path, agent_runner=runner)
    try:
        pending = coordinator.agent_prepare(command_id="command:implementation@1")
        original_digest = _current_mapping_receipt().digest
        clock.advance(4)
        _change_oac_implementation(monkeypatch, module)
        before = adaptation.store.audit_head()
        assert adaptation.view()["status"] == "OWNER_REVIEW_PENDING"
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"):
            adaptation.approve(
                actor_id=pending["adaptation"]["owner_ref"],
                candidate_digest=pending["adaptation"]["candidate_digest"],
                command_id="command:must-replan@1", **_review_subject(pending),
            )
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"):
            coordinator.agent_prepare(command_id="command:implementation@1")
        assert runner.calls == 1
        assert adaptation.store.audit_head() == before
        assert _current_mapping_receipt().digest == original_digest
    finally:
        adaptation.store.close()
        workspace.close()


def test_old_mapping_without_implementation_proof_cannot_open_new_owner_review(tmp_path):
    receipt = _current_mapping_receipt()
    runner = _AgentRunner(receipt)
    coordinator, adaptation, workspace, _clock = _runtime(tmp_path, agent_runner=runner)
    try:
        _write(coordinator._mapping_path, receipt.model_dump(mode="json"))
        assert coordinator.view()["agent_mapping"]["receipt_digest"] == receipt.digest
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING"):
            coordinator.agent_prepare(command_id="command:legacy-cannot-activate@1")
        assert runner.calls == 0
        assert adaptation.view()["status"] == "PACK_OBSERVED"
    finally:
        adaptation.store.close()
        workspace.close()


@pytest.mark.parametrize("module", ("oac_agent_adaptation.py", "agentteams-source"))
def test_activated_history_is_readable_but_cannot_authorize_new_execution_after_upgrade(tmp_path, monkeypatch, module):
    runner = _AgentRunner(_current_mapping_receipt())
    shadow = _ShadowRunner()
    coordinator, adaptation, workspace, clock = _runtime(tmp_path, agent_runner=runner, shadow_runner=shadow)
    try:
        pending = coordinator.agent_prepare(command_id="command:history@1")
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:approve-history@1", **_review_subject(pending),
        )
        complete = coordinator.execute_shadow()
        before = adaptation.store.audit_head()
        _change_oac_implementation(monkeypatch, module)
        assert adaptation.view()["approval_digest"] == ready["approval_digest"]
        assert coordinator.view()["shadow_execution"]["receipt_digest"] == complete["shadow_execution"]["receipt_digest"]
        assert coordinator.execute_shadow()["status"] == "SHADOW_COMPLETED"
        assert shadow.calls == 1 and runner.calls == 1
        binding = adaptation.activation_binding()
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"):
            adaptation.require_activation_binding(
                profile_digest=binding.profile_digest, pack_digest=binding.pack_digest,
                execution_run_id=binding.execution_run_id,
            )
        with pytest.raises(OACAgenticRuntimeError, match="OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"):
            coordinator._ready_formation_lineage(
                adaptation=adaptation.view(), mapping=coordinator._load_mapping_receipt(),
                expected_execution_run_id=binding.execution_run_id,
            )
        assert adaptation.store.audit_head() == before
    finally:
        adaptation.store.close()
        workspace.close()


def test_frozen_mapping_is_readable_without_loading_the_active_source(monkeypatch):
    from orgrebase.workspace import oac_agent_adaptation

    original = json.loads(LIVE_RECEIPT.read_text(encoding="utf-8"))

    def forbidden():
        pytest.fail("Parsing persisted evidence must not consult the active deployment")

    monkeypatch.setattr(oac_agent_adaptation, "load_teamharness_lock", forbidden)
    receipt = _historical_mapping_receipt()
    assert receipt.digest == original["digest"]
    assert receipt.native_agentteams.agentteams_version == "v1.2.2"
    assert receipt.revalidated().model_dump(mode="json") == original


def test_frozen_source_returned_by_new_runner_cannot_open_new_owner_review(tmp_path):
    runner = _AgentRunner(_historical_mapping_receipt())
    coordinator, adaptation, workspace, _clock = _runtime(tmp_path, agent_runner=runner)
    try:
        before = adaptation.store.audit_head()
        with pytest.raises(RuntimeError, match="OAC_AGENTTEAMS_SOURCE_REPLAN_REQUIRED"):
            coordinator.agent_prepare(command_id="command:stale-source@1")
        assert runner.calls == 1
        assert adaptation.view()["status"] == "PACK_OBSERVED"
        assert adaptation.store.audit_head() == before
        assert not coordinator._mapping_path.exists()
    finally:
        adaptation.store.close()
        workspace.close()


def test_legacy_pending_draft_without_binding_is_readable_but_not_approvable(tmp_path, monkeypatch):
    _coordinator, adaptation, workspace, clock = _runtime(tmp_path, agent_runner=_AgentRunner(_current_mapping_receipt()))
    try:
        pending = adaptation.prepare(command_id="command:deterministic@1")
        clock.advance(4)
        load = adaptation._load
        monkeypatch.setattr(adaptation, "_load", lambda kind, media: None if kind == "implementation-binding" else load(kind, media))
        assert adaptation.view()["candidate_digest"] == pending["candidate_digest"]
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING"):
            adaptation.approve(
                actor_id=pending["human_authority_ref"], candidate_digest=pending["candidate_digest"],
                command_id="command:legacy-approve@1",
                owner_review_summary_digest=pending["owner_review_summary"]["digest"],
                acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
            )
    finally:
        adaptation.store.close()
        workspace.close()


@pytest.fixture
def approved_context_runtime(tmp_path):
    pack = load_enterprise_quote_pilot_pack(PACK)
    workspace = WorkspaceService(store_path=tmp_path / "workspace.sqlite3", runtime_configuration=pack)
    clock = _Clock()
    adaptation = OACQuoteAdaptationService(
        store=StateStore(tmp_path / "oac.sqlite3"), profile=pack.profile,
        runtime=pack, oac_root=OAC_ROOT, wall_clock=clock,
        execution_run_id=workspace.effective_workflow_run_id,
    )
    coordinator = OACAgenticRuntime(
        runtime_root=tmp_path / "runtime", repo_root=ROOT,
        checkout=default_agentteams_checkout(ROOT), lock_path=ROOT / "agentteams/teamharness-lock.json",
        pack_path=PACK, frozen_golden_root=ROOT / "evidence/golden-competition/latest/pilot",
        adaptation_service=adaptation, workspace_service=workspace,
        execution_mode="OFFLINE_LOCAL", shadow_model_provider="ollama-local",
    )
    try:
        pending = adaptation.prepare(command_id="command:context-history-prepare@1")
        clock.advance(4)
        ready = adaptation.approve(
            actor_id=pending["human_authority_ref"], candidate_digest=pending["candidate_digest"],
            command_id="command:context-history-approve@1",
            owner_review_summary_digest=pending["owner_review_summary"]["digest"],
            acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
        )
        decision, context = coordinator.formation_roots_for_current_task(
            expected_activation_binding_digest=ready["activation_binding"]["digest"],
        )
        yield coordinator, adaptation, workspace, ready, decision, context
    finally:
        adaptation.store.close()
        workspace.close()


def _hide_context_anchor(monkeypatch, store):
    load = store.load_artifact

    def historical_load(artifact_id, expected_media_type=None):
        if artifact_id.startswith("oac-context-binding:"):
            raise KeyError(artifact_id)
        return load(artifact_id, expected_media_type)

    monkeypatch.setattr(store, "load_artifact", historical_load)


def test_compiled_context_history_rejects_resealed_projection_without_recompiling(
    approved_context_runtime, monkeypatch,
):
    from orgrebase.workspace.context_residency import TaskAgentContextEnvelope

    coordinator, adaptation, workspace, ready, _decision, context = approved_context_runtime
    before = tuple(adaptation.store.connection.iterdump())

    def current_source_changed(_task):
        pytest.fail("Historical reads must not recompile current sources")

    monkeypatch.setattr(workspace.formation, "prepare_quote", current_source_changed)
    assert coordinator.view()["context_residency"]["envelope_digest"] == context.digest
    assert tuple(adaptation.store.connection.iterdump()) == before

    payload = context.model_dump(mode="json", exclude={"digest"})
    payload["task_context_digest"] = "sha256:" + "a" * 64
    payload["domain_bindings"][0]["actor_projection_ref"] = "actor-context:other-workspace@v1"
    payload["domain_bindings"][0]["actor_projection_digest"] = "sha256:" + "b" * 64
    forged = TaskAgentContextEnvelope.model_validate(payload)
    _write(coordinator._context_path, forged.model_dump(mode="json"))
    with pytest.raises(OACAgenticRuntimeError, match="OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH"):
        coordinator.view()
    assert adaptation.view()["approval_digest"] == ready["approval_digest"]
    assert tuple(adaptation.store.connection.iterdump()) == before


def test_unanchored_context_preserves_approval_without_projecting_ready_or_writing(
    approved_context_runtime, monkeypatch,
):
    coordinator, adaptation, _workspace, ready, _decision, _context = approved_context_runtime
    _hide_context_anchor(monkeypatch, adaptation.store)
    before = tuple(adaptation.store.connection.iterdump())
    retained = {path: path.read_bytes() for path in coordinator.runtime_root.rglob("*.json")}
    view = coordinator.view()
    assert view["adaptation"]["approval_digest"] == ready["approval_digest"]
    assert view["context_residency"]["status"] == "EVIDENCE_UNANCHORED"
    assert view["context_residency"]["envelope_digest"] is None
    assert view["context_residency"]["task_projections"] == []
    assert tuple(adaptation.store.connection.iterdump()) == before
    assert all(path.read_bytes() == value for path, value in retained.items())


def test_legacy_consumed_context_uses_committed_roots_and_rejects_resealed_projection(
    approved_context_runtime, monkeypatch,
):
    from orgrebase.workspace.context_residency import TaskAgentContextEnvelope
    from orgrebase.workspace.service import GOLDEN_COMPETITION_ARTIFACT_ID, GOLDEN_COMPETITION_MEDIA_TYPE

    coordinator, adaptation, workspace, ready, decision, context = approved_context_runtime
    workspace.form_quote_with_dependency_evidence(
        oac_activation_binding=ready["activation_binding"],
        task_formation_decision_receipt=decision.model_dump(mode="json"),
        context_envelope=context.model_dump(mode="json"),
    )
    # An explicit historical-metadata fixture; this test runs no native workers.
    evidence = workspace._golden_compact_record({
        "status": "PASS", "correlation_id": workspace.workflow_correlation_id,
        "run_id": ready["activation_binding"]["execution_run_id"],
        "oac_agentteams_lineage": {
            "activation_binding_digest": ready["activation_binding"]["digest"],
            "task_formation_decision_receipt_digest": decision.digest,
            "task_agent_context_envelope_digest": context.digest,
        },
    })
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, GOLDEN_COMPETITION_ARTIFACT_ID, GOLDEN_COMPETITION_MEDIA_TYPE, evidence)
    _hide_context_anchor(monkeypatch, adaptation.store)
    before = tuple(workspace.store.connection.iterdump())

    def current_source_changed(_task):
        pytest.fail("Consumed history must use its committed roots")

    monkeypatch.setattr(workspace.formation, "prepare_quote", current_source_changed)
    assert coordinator.view()["context_residency"]["envelope_digest"] == context.digest
    payload = context.model_dump(mode="json", exclude={"digest"})
    payload["domain_bindings"][0]["actor_projection_digest"] = "sha256:" + "b" * 64
    forged = TaskAgentContextEnvelope.model_validate(payload)
    _write(coordinator._context_path, forged.model_dump(mode="json"))
    with pytest.raises(OACAgenticRuntimeError, match="OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH"):
        coordinator.view()
    assert tuple(workspace.store.connection.iterdump()) == before


@pytest.mark.parametrize("field", ("tenant_id", "workspace_id", "execution_run_id"))
def test_context_anchor_cannot_be_imported_from_another_scope(
    approved_context_runtime, tmp_path, monkeypatch, field,
):
    from orgrebase.domain import IntegrityError

    coordinator, adaptation, _workspace, _ready, _decision, context = approved_context_runtime
    binding = adaptation.activation_binding()
    artifact_id = f"oac-context-binding:{binding.digest}"
    anchor = adaptation.store.load_artifact(artifact_id)
    forged = {**anchor.payload, field: "different-scope"}
    with pytest.raises(IntegrityError, match="ARTIFACT_ID_CONFLICT"), adaptation.store.transaction() as connection:
        adaptation.store.save_artifact(connection, artifact_id, anchor.media_type, forged)
    with StateStore(tmp_path / "imported.sqlite3") as imported:
        with imported.transaction() as connection:
            imported.save_artifact(connection, artifact_id, anchor.media_type, forged)
        with monkeypatch.context() as patch:
            patch.setattr(adaptation, "store", imported)
            with pytest.raises(OACAgenticRuntimeError, match="OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH"):
                coordinator._context_has_trusted_binding(binding, context)


@pytest.mark.parametrize("view_mode", ("LIVE_VERTEX", "FROZEN_REPLAY", "OFFLINE_LOCAL"))
def test_unanchored_shadow_does_not_claim_verified_completion_or_replay(tmp_path, monkeypatch, view_mode):
    shadow = _ShadowRunner()
    coordinator, adaptation, workspace, clock = _runtime(
        tmp_path, agent_runner=_AgentRunner(_current_mapping_receipt()),
        shadow_runner=shadow, shadow_verifier=_ShadowVerifier(),
    )
    try:
        pending = coordinator.agent_prepare(command_id="command:unanchored-shadow-prepare@1")
        clock.advance(4)
        adaptation.approve(
            actor_id=pending["adaptation"]["owner_ref"],
            candidate_digest=pending["adaptation"]["candidate_digest"],
            command_id="command:unanchored-shadow-approve@1", **_review_subject(pending),
        )
        assert coordinator.execute_shadow()["shadow_execution"]["independent_verification"]["status"] == "PASS"
        _hide_context_anchor(monkeypatch, adaptation.store)
        retained = {path: path.read_bytes() for path in coordinator.runtime_root.rglob("*.json")}
        coordinator.execution_mode = view_mode
        view = coordinator.view()
        reasons = view["execution_policy"]["blocked_reasons"]["EXECUTE_SHADOW"]
        expected = ["OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_UNANCHORED"]
        if view_mode == "FROZEN_REPLAY":
            expected.insert(0, "OAC_AGENTIC_RUNTIME_FROZEN_SHADOW_RECEIPT_REQUIRED")
        elif view_mode == "OFFLINE_LOCAL":
            expected.insert(0, "OAC_AGENTIC_RUNTIME_NEW_VERTEX_SHADOW_BLOCKED")
        assert reasons == expected
        assert coordinator.view()["execution_policy"]["blocked_reasons"]["EXECUTE_SHADOW"] == expected
        assert view["status"] == "READY_FOR_SHADOW"
        assert view["context_residency"]["status"] == "EVIDENCE_UNANCHORED"
        assert view["shadow_execution"]["status"] == "EVIDENCE_INCOMPLETE"
        assert view["shadow_execution"]["independent_verification"]["status"] == "NOT_RUN"
        assert "EXECUTE_SHADOW" not in view["execution_policy"]["available_actions"]
        with pytest.raises(OACAgenticRuntimeError, match="OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_UNANCHORED"):
            coordinator.execute_shadow()
        assert shadow.calls == 1
        assert all(path.read_bytes() == value for path, value in retained.items())
    finally:
        adaptation.store.close()
        workspace.close()
