from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from orgrebase.agentteams_source import load_agentteams_source, packaged_agentteams_bundle
from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore
from orgrebase.workspace.models import ModelResponseReceipt
from orgrebase.workspace.oac_agent_adaptation import (
    AGENT_PRINCIPAL,
    OACAgentMappingCandidate,
    OACAgentTeamsMappingLifecycle,
    expected_oac_agent_candidate,
    require_current_agent_mapping_source,
    run_oac_agent_adaptation,
    validate_oac_agent_candidate,
)
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    CandidateSemanticMapping,
    OACAdaptationReviewGatePending,
    OACQuoteAdaptationService,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from scripts.verify_oac_agent_adaptation import (
    VerificationError,
    _verify_live_model_observation,
)

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples/enterprise-quote-pilot/evergreen"
LOCK = ROOT / "agentteams/teamharness-lock.json"
BUNDLE = packaged_agentteams_bundle(ROOT)
COMMIT = load_agentteams_source(ROOT).commit
OAC_ROOT = ROOT.parent / "oac-spec"


def _live_model_observation(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "provider": "vertex-ai",
        "model_id": "gemini-3.8-flash",
        "model_version": "gemini-3.8-flash",
        "status": "VALID",
        "evidence_class": "LIVE_MODEL",
        "provider_request_id": "vertex-response-oac-mapper-1",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("model_id", ("gemini-3.7-flash", "gemini-3.8-flash"))
def test_independent_verifier_accepts_supported_consistent_vertex_model(
    model_id: str,
) -> None:
    _verify_live_model_observation(
        _live_model_observation(model_id=model_id, model_version=model_id)
    )


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
def test_independent_verifier_rejects_tampered_live_model_observation(
    mutation: dict[str, Any],
    error: str,
) -> None:
    with pytest.raises(VerificationError, match=f"^{error}$"):
        _verify_live_model_observation(_live_model_observation(**mutation))


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture(scope="module")
def runtime():
    return load_enterprise_quote_pilot_pack(PACK)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("oac-agentteams-checkout") / "checkout"
    subprocess.run(
        ["git", "clone", "--no-checkout", str(BUNDLE), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "set-url",
            "origin",
            "https://github.com/agentscope-ai/AgentTeams",
        ],
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


def _service(tmp_path: Path, runtime, clock: _Clock | None = None):
    service = OACQuoteAdaptationService(
        store=StateStore(tmp_path / "adaptation.sqlite3"),
        profile=runtime.profile,
        runtime=runtime,
        oac_root=OAC_ROOT,
        wall_clock=clock or _Clock(),
    )
    baseline, _ = service.deterministic_mapping_baseline()
    return service, baseline


class _LiveProvider:
    def __init__(self, candidate: OACAgentMappingCandidate, *, secret: str = "") -> None:
        self.candidate = candidate
        self.secret = secret

    def generate_structured(self, *, request, output_model):
        value = self.candidate.model_dump(mode="json")
        return ModelResponseReceipt(
            id=f"model-response:{request.request_id}:attempt-0",
            request_ref=request.request_id,
            request_digest=request.digest,
            status="VALID",
            value=value,
            output_digest=sha256_digest(value),
            provider_request_id="vertex-response-oac-mapper-1",
            provider="vertex-ai",
            model_id="gemini-3.7-flash",
            model_version="gemini-3.7-flash",
            schema_valid=True,
            input_tokens=101,
            output_tokens=53,
            latency_ms=12,
            finish_reason="STOP",
            seed_supported=False,
            completed_at="2026-08-28T00:00:00Z",
            evidence_class="LIVE_MODEL",
        )


class _FailedProvider:
    def __init__(self, status: str, error_code: str) -> None:
        self.status = status
        self.error_code = error_code

    def generate_structured(self, *, request, output_model):
        return ModelResponseReceipt(
            id=f"model-response:{request.request_id}:attempt-0",
            request_ref=request.request_id,
            request_digest=request.digest,
            status=self.status,
            provider="vertex-ai",
            model_id="gemini-3.7-flash",
            model_version="gemini-3.7-flash",
            schema_valid=False,
            error_code=self.error_code,
            completed_at="2026-08-28T00:00:00Z",
            evidence_class="NOT_RUN",
        )


def _candidate(runtime, baseline, run_id: str) -> OACAgentMappingCandidate:
    return expected_oac_agent_candidate(
        profile=runtime.profile,
        runtime=runtime,
        baseline_mappings=baseline,
        adaptation_run_id=run_id,
    )


def _mutate_candidate(
    candidate: OACAgentMappingCandidate,
    update: dict[str, Any],
) -> OACAgentMappingCandidate:
    value = candidate.model_dump(mode="json")
    value.update(update)
    return OACAgentMappingCandidate.model_validate(value)


def test_checked_in_provider_and_receipt_schemas_match_runtime_models() -> None:
    candidate = json.loads(
        (ROOT / "schemas/workspace-o-a-c-agent-mapping-candidate.schema.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (ROOT / "schemas/workspace-o-a-c-agent-mapping-receipt.schema.json").read_text(encoding="utf-8")
    )
    from orgrebase.workspace.oac_agent_adaptation import OACAgentMappingReceipt

    assert candidate == OACAgentMappingCandidate.model_json_schema(mode="validation")
    assert receipt == OACAgentMappingReceipt.model_json_schema(mode="validation")


def test_live_candidate_runs_ack_before_provider_and_enters_existing_four_second_gate(
    tmp_path: Path,
    checkout: Path,
    runtime,
) -> None:
    clock = _Clock()
    service, baseline = _service(tmp_path, runtime, clock)
    try:
        candidate = _candidate(runtime, baseline, service.adaptation_run_id)
        evidence = tmp_path / "evidence"
        receipt = run_oac_agent_adaptation(
            checkout=checkout,
            lock_path=LOCK,
            output_dir=evidence,
            profile=runtime.profile,
            runtime=runtime,
            baseline_mappings=baseline,
            adaptation_run_id=service.adaptation_run_id,
            provider=_LiveProvider(candidate),
        )
        assert receipt.status == "VALIDATED_CANDIDATE"
        assert receipt.native_agentteams.agentteams_version == load_agentteams_source(ROOT).tag
        assert receipt.native_agentteams.agentteams_commit == COMMIT
        for field, value in (("agentteams_version", "v0.0.0"), ("agentteams_commit", "0" * 40),
                             ("source_lock_digest", "sha256:" + "0" * 64)):
            altered = receipt.native_agentteams.model_dump(mode="json", exclude={"digest"})
            altered[field] = value
            lifecycle = OACAgentTeamsMappingLifecycle.model_validate(altered)
            # A self-consistent historical record remains readable, but its
            # source identity cannot authorize a new admission.
            with pytest.raises(RuntimeError, match="OAC_AGENTTEAMS_SOURCE_REPLAN_REQUIRED"):
                require_current_agent_mapping_source(receipt.model_copy(update={"native_agentteams": lifecycle}))
        attempts = json.loads((evidence / "model-attempt-observations.json").read_text())
        assert attempts["coverage"] == "INCOMPLETE"
        assert attempts["records"] == []  # injected legacy provider is not usage evidence
        assert receipt.model_observation.evidence_class == "LIVE_MODEL"
        assert receipt.model_observation.output_digest == sha256_digest(candidate.model_dump(mode="json"))
        assert receipt.native_agentteams.action_sequence == (
            "create_project",
            "plan_dag",
            "ready_nodes",
            "delegate_task",
            "ack_task",
            "submit_task",
            "check_task",
            "accept_task_result",
            "complete_project",
        )
        ack_sequence = next(
            item["sequence"] for item in receipt.native_agentteams.actions if item["action"] == "ack_task"
        )
        assert ack_sequence == 5
        from orgrebase.workspace.oac_agent_adaptation import (
            bind_oac_mapping_implementation,
            current_oac_admission_implementation,
        )

        bind_oac_mapping_implementation(service.store, receipt, implementation=current_oac_admission_implementation())
        draft = service.prepare(
            command_id="command:test:agent-prepare@1",
            agent_mapping_receipt=receipt.model_dump(mode="json"),
        )
        assert draft["status"] == "OWNER_REVIEW_PENDING"
        assert draft["review_remaining_ms"] == 4_000
        assert draft["agent_mapping"]["receipt_digest"] == receipt.digest
        assert draft["mapping_set_digest"] == receipt.accepted_mapping_set_digest
        with pytest.raises(
            OACAdaptationReviewGatePending,
            match="OAC_ADAPTATION_REVIEW_GATE_NOT_READY",
        ):
            service.approve(
                actor_id=draft["human_authority_ref"],
                candidate_digest=draft["candidate_digest"],
                command_id="command:test:too-early@1",
                owner_review_summary_digest=draft["owner_review_summary"]["digest"],
                acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
            )
    finally:
        service.store.close()


@pytest.mark.parametrize(
    ("status", "error_code"),
    (
        ("NOT_RUN", "VERTEX_CREDENTIALS_MISSING"),
        ("PROVIDER_ERROR", "VERTEX_HTTP_ERROR:503"),
        ("SCHEMA_ERROR", "VERTEX_SCHEMA_MISMATCH:ValidationError"),
    ),
)
def test_provider_or_schema_failure_completes_native_task_but_holds_mapping(
    tmp_path: Path,
    checkout: Path,
    runtime,
    status: str,
    error_code: str,
) -> None:
    service, baseline = _service(tmp_path, runtime)
    try:
        receipt = run_oac_agent_adaptation(
            checkout=checkout,
            lock_path=LOCK,
            output_dir=tmp_path / "evidence",
            profile=runtime.profile,
            runtime=runtime,
            baseline_mappings=baseline,
            adaptation_run_id=service.adaptation_run_id,
            provider=_FailedProvider(status, error_code),
        )
        assert receipt.status == "HOLD"
        assert receipt.accepted_mappings == ()
        assert receipt.model_observation.status == status
        assert receipt.validation.reason_codes == (error_code,)
        assert receipt.native_agentteams.project_terminal_state == "completed"
        with pytest.raises(RuntimeError, match="OAC_AGENT_MAPPING_RECEIPT_NOT_VALIDATED"):
            service.prepare(
                command_id="command:test:must-hold@1",
                agent_mapping_receipt=receipt.model_dump(mode="json"),
            )
    finally:
        service.store.close()


def test_path_injection_authority_expansion_and_self_approval_are_held(
    tmp_path: Path,
    runtime,
) -> None:
    service, baseline = _service(tmp_path, runtime)
    try:
        candidate = _candidate(runtime, baseline, service.adaptation_run_id)
        base = candidate.model_dump(mode="json")
        cases: list[tuple[dict[str, Any], str]] = []
        path_injection = json.loads(json.dumps(base))
        path_injection["component_mappings"][0]["target_oac_paths"].append(
            "../../OrganizationSnapshot.spec.principals"
        )
        cases.append((path_injection, "TARGET_PATH_NOT_ALLOWED"))
        authority_expansion = json.loads(json.dumps(base))
        authority_expansion["component_mappings"][0]["authority_refs"] = ["principal:unadmitted-superuser"]
        cases.append((authority_expansion, "AUTHORITY_EXPANSION"))
        self_approval = json.loads(json.dumps(base))
        self_approval["requested_approval_authority_ref"] = AGENT_PRINCIPAL
        cases.append((self_approval, "SELF_APPROVAL_FORBIDDEN"))
        for payload, code in cases:
            selected = OACAgentMappingCandidate.model_validate(payload)
            validation, accepted = validate_oac_agent_candidate(
                candidate=selected,
                profile=runtime.profile,
                runtime=runtime,
                baseline_mappings=baseline,
                adaptation_run_id=service.adaptation_run_id,
                task_id="task:oac-agent-mapper@a1",
                provider_response_digest="sha256:" + "1" * 64,
            )
            assert validation.verdict == "HOLD"
            assert code in validation.reason_codes
            assert accepted == ()
    finally:
        service.store.close()


def test_unknown_erasure_is_held_even_when_provider_schema_is_valid(
    tmp_path: Path,
    runtime,
) -> None:
    service, baseline = _service(tmp_path, runtime)
    try:
        first = baseline[0]
        payload = first.model_dump(mode="json")
        payload.pop("digest")
        payload["declared_unknowns"] = ["domain.owner"]
        payload["reason_codes"] = ["UNKNOWN_AUTHORITY"]
        unknown_baseline = (
            CandidateSemanticMapping.model_validate(payload),
            *baseline[1:],
        )
        candidate = _candidate(
            runtime,
            unknown_baseline,
            service.adaptation_run_id,
        )
        raw = candidate.model_dump(mode="json")
        raw["component_mappings"][0]["declared_unknowns"] = []
        raw["component_mappings"][0]["reason_codes"] = []
        erased = OACAgentMappingCandidate.model_validate(raw)
        validation, accepted = validate_oac_agent_candidate(
            candidate=erased,
            profile=runtime.profile,
            runtime=runtime,
            baseline_mappings=unknown_baseline,
            adaptation_run_id=service.adaptation_run_id,
            task_id="task:oac-agent-mapper@a1",
            provider_response_digest="sha256:" + "2" * 64,
        )
        assert validation.verdict == "HOLD"
        assert "UNKNOWN_ERASURE" in validation.reason_codes
        assert accepted == ()
    finally:
        service.store.close()


def test_credential_canary_is_never_persisted(
    tmp_path: Path,
    checkout: Path,
    runtime,
) -> None:
    service, baseline = _service(tmp_path, runtime)
    canary = "AIza-secret-canary-must-never-persist"
    try:
        candidate = _candidate(runtime, baseline, service.adaptation_run_id)
        evidence = tmp_path / "evidence"
        run_oac_agent_adaptation(
            checkout=checkout,
            lock_path=LOCK,
            output_dir=evidence,
            profile=runtime.profile,
            runtime=runtime,
            baseline_mappings=baseline,
            adaptation_run_id=service.adaptation_run_id,
            provider=_LiveProvider(candidate, secret=canary),
            forbidden_secret_values=(canary,),
        )
        assert all(
            canary.encode("utf-8") not in path.read_bytes() for path in evidence.rglob("*") if path.is_file()
        )
    finally:
        service.store.close()
