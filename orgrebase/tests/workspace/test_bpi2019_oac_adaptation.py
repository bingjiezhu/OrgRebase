from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from orgrebase.agentteams_source import load_agentteams_source, packaged_agentteams_bundle
from orgrebase.workspace.bpi_oac_adaptation import (
    CLAIM_CEILING,
    BPIOACMappingCandidate,
    _load_config,
    _verify_benchmark_closure,
    expected_mapping_candidate,
    finalize_bpi_oac_execution,
    prepare_bpi_oac_adaptation,
    validate_mapping_candidate,
)
from orgrebase.workspace.models import ModelResponseReceipt
from scripts.verify_bpi2019_oac_adaptation import _verify_model_attempt

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark/quote-value-v0.4-bpi-real-process"
CONFIG = ROOT / "configs/oac/bpi2019-p2p-adaptation-v1.json"
LOCK = ROOT / "agentteams/teamharness-lock.json"
BUNDLE = packaged_agentteams_bundle(ROOT)
COMMIT = load_agentteams_source(ROOT).commit


def _live_model_attempt(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "provider": "vertex-ai",
        "model_id": "gemini-3.8-flash",
        "model_version": "gemini-3.8-flash",
        "status": "VALID",
        "evidence_class": "LIVE_MODEL",
        "provider_request_id": "vertex-response-bpi-mapper-1",
        "provider_request_id_present": True,
        "selected": True,
        "fallback_is_separate": False,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("model_id", ("gemini-3.7-flash", "gemini-3.8-flash"))
def test_independent_verifier_accepts_supported_consistent_vertex_model(
    model_id: str,
) -> None:
    failures: list[str] = []
    _verify_model_attempt(
        _live_model_attempt(model_id=model_id, model_version=model_id),
        selected_mapping_lane="LIVE_VERTEX_AGENT_CANDIDATE",
        failures=failures,
    )
    assert failures == []


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ({"provider": "vertex-proxy"}, "LIVE_MODEL_PROVIDER_INVALID"),
        ({"model_id": "gemini-3.8-flash-preview"}, "LIVE_MODEL_ID_UNSUPPORTED"),
        ({"model_version": "gemini-3.7-flash"}, "LIVE_MODEL_VERSION_MISMATCH"),
        ({"status": "SCHEMA_ERROR"}, "LIVE_MODEL_STATUS_INVALID"),
        ({"evidence_class": "NOT_RUN"}, "LIVE_MODEL_EVIDENCE_CLASS_INVALID"),
        ({"provider_request_id": ""}, "LIVE_MODEL_PROVIDER_REQUEST_ID_INVALID"),
    ),
)
def test_independent_verifier_rejects_tampered_live_model_attempt(
    mutation: dict[str, object],
    error: str,
) -> None:
    failures: list[str] = []
    _verify_model_attempt(
        _live_model_attempt(**mutation),
        selected_mapping_lane="LIVE_VERTEX_AGENT_CANDIDATE",
        failures=failures,
    )
    assert error in failures


def test_independent_verifier_rejects_pre_call_fallback_misreported_as_live() -> None:
    failures: list[str] = []
    _verify_model_attempt(
        {
            **_live_model_attempt(),
            "status": "NOT_RUN",
            "selected": False,
            "fallback_is_separate": True,
        },
        selected_mapping_lane="CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK",
        failures=failures,
    )
    assert "FALLBACK_LIVE_EVIDENCE_MISREPORTED" in failures


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bpi-oac-agentteams") / "checkout"
    subprocess.run(
        ["git", "clone", "--no-checkout", str(BUNDLE), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "remote", "set-url", "origin", "https://github.com/agentscope-ai/AgentTeams"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "checkout", "--detach", COMMIT],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


class _NotRunVertex:
    def generate_structured(self, *, request, output_model):
        return ModelResponseReceipt(
            id=f"model-response:{request.request_id}:attempt-0",
            request_ref=request.request_id,
            request_digest=request.digest,
            status="NOT_RUN",
            provider="vertex-ai",
            model_id="gemini-3.7-flash",
            model_version="gemini-3.7-flash",
            schema_valid=False,
            error_code="VERTEX_CREDENTIALS_MISSING",
            completed_at="2026-08-28T00:00:00Z",
            evidence_class="NOT_RUN",
        )


def _inputs():
    dataset, projection = _verify_benchmark_closure(BENCHMARK)
    return dataset, projection, _load_config(CONFIG, dataset, projection)


def test_public_real_input_and_config_are_digest_bound() -> None:
    dataset, projection, config = _inputs()
    assert dataset["projection_digest"] == projection["digest"]
    assert config["dataset_manifest_digest"] == dataset["digest"]
    assert config["projection_digest"] == projection["digest"]
    assert config["claim_ceiling"] == CLAIM_CEILING
    assert len(projection["queries"]) == 128


def test_validator_holds_hallucinated_authority_and_values() -> None:
    _dataset, _projection, config = _inputs()
    expected = expected_mapping_candidate(config=config, adaptation_run_id="run:test")
    payload = expected.model_dump(mode="json")
    payload["organization_values"] = ["move fast"]
    payload["real_responsible_owners"] = ["user_247"]
    payload["permissions"] = ["approve_purchase_order"]
    payload["approval_authorities"] = ["user_247"]
    candidate = BPIOACMappingCandidate.model_validate(payload)
    result = validate_mapping_candidate(candidate=candidate, expected=expected)
    assert result["verdict"] == "HOLD"
    assert set(result["failures"]) >= {
        "organization_values_unknown",
        "real_responsible_owners_unknown",
        "permissions_unknown",
        "approval_authorities_unknown",
    }


def test_agentteams_fallback_gate_binding_and_128_case_replay(tmp_path: Path, checkout: Path) -> None:
    evidence = tmp_path / "evidence"
    prepared = prepare_bpi_oac_adaptation(
        benchmark_root=BENCHMARK,
        config_path=CONFIG,
        checkout=checkout,
        lock_path=LOCK,
        output_dir=evidence,
        provider=_NotRunVertex(),
        now_ms=1_000_000,
    )
    assert prepared.selected_mapping_lane == "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK"
    assert prepared.model_attempt["status"] == "NOT_RUN"
    assert prepared.model_attempt["model_attempt_observations"]["coverage"] == "INCOMPLETE"
    assert prepared.model_attempt["model_attempt_observations"]["records"] == []
    assert prepared.model_attempt["fallback_is_separate"] is True
    fallback = json.loads((evidence / "deterministic-fallback-contract.json").read_text(encoding="utf-8"))
    assert fallback["status"] == "SELECTED"
    assert prepared.model_attempt["fallback_contract_digest"] == fallback["digest"]
    assert prepared.agentteams["action_sequence"] == [
        "create_project",
        "plan_dag",
        "ready_nodes",
        "delegate_task",
        "ack_task",
        "submit_task",
        "check_task",
        "accept_task_result",
        "complete_project",
    ]
    assert prepared.human_admission["early_attempt"]["status"] == "REJECTED_TOO_EARLY"
    owner_review = json.loads((evidence / "owner-review-summary.json").read_text(encoding="utf-8"))
    assert prepared.review_gate.owner_review_summary_digest == owner_review["digest"]
    assert owner_review["data_class"] == "PUBLIC_ANONYMIZED_ENTERPRISE_EVENT_LOG"
    assert owner_review["declared_unknowns"] == [
        "ORGANIZATION_VALUES",
        "REAL_RESPONSIBLE_OWNERS",
        "PERMISSIONS",
        "APPROVAL_AUTHORITIES",
    ]
    assert prepared.human_admission["admission"]["elapsed_ms"] == 4000
    assert (
        prepared.human_admission["admission"]["interaction_evidence"]
        == "CONTROLLED_LOCAL_SCRIPTED_OWNER_COMMAND_NOT_EXTERNAL_HUMAN"
    )
    final = finalize_bpi_oac_execution(
        adaptation_receipt=prepared,
        execution_receipt_path=ROOT
        / "evidence/public-real-process/latest/bpi2019-real-process-benchmark-receipt.json",
        independent_verification_path=ROOT
        / "evidence/public-real-process/latest/bpi2019-real-process-verification.json",
        output_path=evidence / "adaptation-receipt.json",
    )
    assert final.adaptation_run_id != final.execution_run_id
    assert final.typed_scope_execution is not None
    assert final.typed_scope_execution["query_count"] == 128
    assert final.typed_scope_execution["ground_truth_class"] == "TASK_DEFINED_QUERY_GROUND_TRUTH"
    assert final.canonical_target_writes == 0
    schema = json.loads(
        (ROOT / "schemas/workspace-oac-public-real-process-binding.schema.json").read_text(encoding="utf-8")
    )
    payload = final.model_dump(mode="json")
    assert set(schema["required"]) == set(payload)
    assert schema["properties"]["claim_ceiling"]["const"] == payload["claim_ceiling"]
    assert schema["properties"]["canonical_target_writes"]["const"] == 0
