from __future__ import annotations

import copy
import inspect
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from textwrap import dedent
from uuid import UUID

import pytest

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.runtime_contracts import prepare_artifact_write
from orgrebase.workspace.agentteams_execution_plan import AgentTeamsExecutionTask
from orgrebase.workspace.competition_run import (
    GoldenCompetitionError,
    _child_input,
    _projection_with_tool,
    _projection_without,
    _quote_skill_evaluation_cases,
    _require_reviewed_formation_inputs,
    run_golden_competition,
)
from orgrebase.workspace.competition_worker import (
    OLLAMA_MODEL_DIGEST,
    deterministic_review,
    produce_domain_result,
)
from orgrebase.workspace.controlled_agentteams_formation import (
    CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE,
    ControlledAgentTeamsFormationReceipt,
)
from orgrebase.workspace.models import (
    ClaimCandidate,
    DomainCandidateBundle,
    PreparedFormationBundle,
)
from orgrebase.workspace.pilot import (
    enterprise_quote_pilot_run_id,
    load_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.quote_skill_qualification import qualification_premise
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_packages import SkillPackageEvaluator, SkillPackageRegistry

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples/enterprise-quote-pilot/evergreen"
CHECKOUT = default_agentteams_checkout(ROOT)
LOCK = ROOT / "agentteams/teamharness-lock.json"


@pytest.mark.parametrize("remove_distinct_domain_guard", [False, True])
def test_quote_skill_qualification_detects_cross_domain_substitution(
    monkeypatch, remove_distinct_domain_guard: bool,
) -> None:
    registry = SkillPackageRegistry(ROOT)
    package = registry.load("enterprise-quote-compose")
    inputs = {
        "skill_partition": "replay",
        "candidate_program_digest_required": package.program["digest"],
        "dependency_tool_receipt_digest": sha256_digest({"tool": "qualification"}),
        "dependency_result_digest": sha256_digest({"result": "qualification"}),
        "coalition_result_binding_digest": sha256_digest({"coalition": "qualification"}),
        "domain_result_digests": {
            domain: sha256_digest({"domain": domain}) for domain in ("product", "legal", "finance", "gtm")
        },
    }
    if remove_distinct_domain_guard:
        method = SkillPackageRegistry._execute_quote
        source = dedent(inspect.getsource(method))
        guard = "or len(set(domain_results.values())) != 4"
        assert source.count(guard) == 1
        namespace = {}
        exec(compile(source.replace(guard, "or False"), "<missing-domain-guard>", "exec"),
             method.__globals__, namespace)
        monkeypatch.setattr(SkillPackageRegistry, "_execute_quote", namespace[method.__name__])
    cases = _quote_skill_evaluation_cases(run_id="qualification-test", public_input=inputs)
    evaluation = SkillPackageEvaluator(registry).evaluate(
        package.name, cases, premise_lock=qualification_premise(package))
    assert len(cases) == 9
    assert len(set(inputs["domain_result_digests"].values())) == 4
    substitution = cases[-1]
    assert substitution.partition == "MALFORMED" and substitution.expected_action == "ABSTAIN"
    assert substitution.public_input["skill_partition"] == "replay"
    assert substitution.public_input["domain_result_digests"]["legal"] == inputs["domain_result_digests"]["product"]
    assert all(case["passed"] for case in evaluation["case_results"][:-1])
    assert evaluation["verdict"] == ("QUARANTINED" if remove_distinct_domain_guard else "CANARY")
    assert evaluation["case_results"][-1]["passed"] is not remove_distinct_domain_guard


def _ollama_exact_model_available() -> bool:
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


@pytest.fixture(scope="module")
def golden_pack(tmp_path_factory: pytest.TempPathFactory):
    if os.environ.get("ORGREBASE_TEST_LIVE_OLLAMA") != "1":
        pytest.skip("live local inference requires explicit ORGREBASE_TEST_LIVE_OLLAMA=1")
    if not CHECKOUT.is_dir() or not _ollama_exact_model_available():
        pytest.skip("pinned AgentTeams checkout or exact local qwen2.5:3b unavailable")
    output = tmp_path_factory.mktemp("golden-competition")
    receipt = run_golden_competition(
        repo_root=ROOT,
        output_dir=output,
        checkout=CHECKOUT,
        lock_path=LOCK,
        pack_path=PACK,
    )
    return output, receipt


def _finance_inputs() -> tuple[dict, dict]:
    runtime = load_enterprise_quote_pilot_pack(PACK)
    run_id = enterprise_quote_pilot_run_id(runtime)
    service = WorkspaceService(
        store_path=":memory:",
        workflow_run_id=run_id,
        runtime_configuration=runtime,
    )
    try:
        request = runtime.profile.task_request()
        template, _, _, _, coalition = service.formation.compile_quote_contracts(request)
        projections = service.formation.domain_registry.source_projections(
            task=request,
            template=template,
            plan=coalition,
            now=service.formation.clock.now(),
        )
        finance = next(item for item in projections if item.actor_id == "finance-steward")
        price_ref = runtime.source_values["price_band"].object_ref
        attempt_1_projection = _projection_without(finance, object_ref=price_ref)
        attempt_2_projection = _projection_with_tool(attempt_1_projection, object_ref=price_ref)
        attempt_1 = _child_input(
            run_id=run_id,
            correlation_id=run_id,
            task_id="task:finance-a1",
            domain="finance",
            attempt=1,
            request=request,
            template=template,
            coalition=coalition,
            projection=attempt_1_projection,
            source_values=runtime.source_values,
            observed_at=service.formation.clock.now(),
        )
        attempt_2 = _child_input(
            run_id=run_id,
            correlation_id=run_id,
            task_id="task:finance-a2",
            domain="finance",
            attempt=2,
            request=request,
            template=template,
            coalition=coalition,
            projection=attempt_2_projection,
            source_values=runtime.source_values,
            observed_at=service.formation.clock.now(),
            tool_receipt_digest="sha256:" + "a" * 64,
        )
    finally:
        service.close()
    return attempt_1, attempt_2


def test_worker_derives_partial_abstain_then_complete_candidate_at_runtime() -> None:
    attempt_1, attempt_2 = _finance_inputs()
    first = produce_domain_result(attempt_1)
    second = produce_domain_result(attempt_2)

    assert first["status"] == "ABSTAIN"
    assert first["missing_fields"] == ["price_band"]
    assert [item["predicate"] for item in first["claim_candidates"]] == ["currency"]
    assert second["status"] == "PASS"
    assert sorted(item["predicate"] for item in second["claim_candidates"]) == [
        "currency",
        "price_band",
    ]
    assert second["supplemental_tool_receipt_digest"] == "sha256:" + "a" * 64


def test_finance_attempt_one_contains_no_excluded_answer_or_private_floor() -> None:
    attempt_1, _attempt_2 = _finance_inputs()
    serialized = json.dumps(attempt_1, ensure_ascii=False, sort_keys=True)

    assert set(attempt_1["source_values"]) == {"currency"}
    assert "price_band" not in attempt_1["source_values"]
    assert "strategic" not in serialized
    assert "85000" not in serialized
    assert "raw_private_value" not in serialized


def _bind_worker_input_to_logical_plan_task(value: dict) -> tuple[dict, AgentTeamsExecutionTask]:
    projection = value["projection"]
    task = AgentTeamsExecutionTask(
        task_id="at-domain-finance-test000000",
        task_kind="DOMAIN",
        domain_id="finance",
        assignee_actor_id="finance-steward",
        depends_on=(),
        formation_receipt_id="formation:test",
        formation_receipt_digest=sha256_digest({"formation": "test"}),
        context_envelope_ref="context:test@v1",
        context_envelope_digest=sha256_digest({"context": "test"}),
        capability_card_ref="capability-card:finance@v1",
        capability_card_digest=sha256_digest({"card": "finance"}),
        actor_projection_ref=f"{projection['id']}@{projection['version']}",
        actor_projection_digest=projection["digest"],
        input_schema_refs=("schema:input@test",),
        input_schema_digest=sha256_digest({"schema": "input"}),
        output_schema_refs=("schema:output@test",),
        output_schema_digest=sha256_digest({"schema": "output"}),
    )
    selected = copy.deepcopy(value)
    selected.update(
        {
            "context_envelope_digest": task.context_envelope_digest,
            "agentteams_execution_plan_digest": sha256_digest({"plan": "test"}),
            "logical_plan_task_id": task.task_id,
            "logical_plan_task_digest": task.digest,
            "formation_receipt_id": task.formation_receipt_id,
            "formation_receipt_digest": task.formation_receipt_digest,
            "sealed_plan_task": task.model_dump(mode="json"),
            "admitted_actor_projection": projection,
        }
    )
    return selected, task


def test_worker_echoes_sealed_plan_lineage_and_rejects_assignee_substitution() -> None:
    attempt_1, _attempt_2 = _finance_inputs()
    bound, task = _bind_worker_input_to_logical_plan_task(attempt_1)

    result = produce_domain_result(bound)
    assert result["agentteams_execution_plan_digest"] == bound["agentteams_execution_plan_digest"]
    assert result["logical_plan_task_digest"] == task.digest
    assert result["formation_receipt_digest"] == task.formation_receipt_digest

    substituted = copy.deepcopy(bound)
    sealed = substituted["sealed_plan_task"]
    sealed["assignee_actor_id"] = "legal-steward"
    sealed.pop("digest", None)
    sealed["digest"] = sha256_digest(sealed)
    substituted["logical_plan_task_digest"] = sealed["digest"]
    with pytest.raises(ValueError, match="COMPETITION_WORKER_PLAN_BINDING_INVALID"):
        produce_domain_result(substituted)


def test_controlled_agentteams_formation_schema_matches_model() -> None:
    checked_in = json.loads(
        (ROOT / "schemas/workspace-controlled-agent-teams-formation-receipt.schema.json").read_text(
            encoding="utf-8"
        )
    )
    generated = ControlledAgentTeamsFormationReceipt.model_json_schema(mode="validation")
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    generated["$id"] = (
        "https://orgrebase.local/schemas/workspace-controlled-agent-teams-formation-receipt.schema.json"
    )
    assert checked_in == generated


def test_golden_run_replans_and_prepares_uncommitted_pack_quote(golden_pack) -> None:
    output, receipt = golden_pack
    assert receipt["status"] == "PASS"
    assert receipt["correlation_id"] == enterprise_quote_pilot_run_id(load_enterprise_quote_pilot_pack(PACK))
    assert receipt["run_id"].startswith("run:golden-competition:")
    UUID(receipt["run_id"].removeprefix("run:golden-competition:"))
    envelope = json.loads((output / "execution-envelope.json").read_text(encoding="utf-8"))
    assert envelope["run_id"] == receipt["run_id"]
    assert envelope["correlation_id"] == receipt["correlation_id"]
    assert envelope["digest"] == receipt["execution_envelope_digest"]
    assert all(
        item["run_id"] == receipt["run_id"] and item["correlation_id"] == receipt["correlation_id"]
        for item in receipt["task_bindings"]
    )
    assert receipt["finance_attempt_1"] == {
        "status": "ABSTAIN",
        "missing_fields": ["price_band"],
    }
    assert receipt["reviewer_attempt_1"]["verdict"] == "REPLAN"
    assert receipt["finance_attempt_2"]["status"] == "PASS"
    assert receipt["reviewer_attempt_2"]["verdict"] == "PASS"
    assert receipt["skill_action"] == "APPLY_QUOTE"
    assert receipt["skill_authorization_mode"] == "RELEASE"
    assert receipt["skill_evaluation_verdict"] == "CANARY"
    assert receipt["skill_evaluation_partition_count"] == 8
    assert receipt["skill_evaluation_case_count"] == 9
    assert receipt["skill_release_state"] == "CANARY"
    assert receipt["skill_release_transition_count"] == 3
    assert receipt["prepared_formation_integrity"] == "PASS"
    assert receipt["canonical_target_writes"] == 0
    assert receipt["project_terminal_state"] == "completed"
    assert receipt["model_provider"] == "ollama-local"
    assert len(receipt["reviewer_model_attempts"]) == 2
    assert all(
        item["run_id"] == receipt["run_id"]
        and item["provider"] == "ollama-local"
        and item["model_authority"] == "ADVISORY_ONLY_DETERMINISTIC_REVIEWER_AUTHORITATIVE"
        and item["candidate_only"] is True
        and item["target_writes"] == 0
        for item in receipt["reviewer_model_attempts"]
    )
    assert receipt["prepared_quote_ref"] == "work:quote-blue-harbor@v1"
    assert all(item["expected_result_digest_present"] is False for item in receipt["task_bindings"])
    PreparedFormationBundle.model_validate(
        json.loads((output / "prepared-formation-bundle.json").read_text(encoding="utf-8"))
    )


def test_golden_quote_skill_is_release_authorized_by_current_canary_head(
    golden_pack,
) -> None:
    output, receipt = golden_pack
    package = json.loads((output / "skill/package.json").read_text(encoding="utf-8"))
    evaluation = json.loads((output / "skill/evaluation.json").read_text(encoding="utf-8"))
    ledger = json.loads((output / "skill/release-ledger.json").read_text(encoding="utf-8"))
    invocation = json.loads((output / "skill/receipt.json").read_text(encoding="utf-8"))

    assert package["name"] == "enterprise-quote-compose"
    assert evaluation["verdict"] == "CANARY"
    assert len(evaluation["case_results"]) == 9
    assert all(item["passed"] for item in evaluation["case_results"])
    assert all(item["passed"] for item in evaluation["gate_results"])
    assert [(item["from_state"], item["to_state"]) for item in ledger] == [
        ("DRAFT", "EVALUATED"),
        ("EVALUATED", "SHADOW"),
        ("SHADOW", "CANARY"),
    ]
    assert invocation["authorization_mode"] == "RELEASE"
    assert invocation["release_receipt_digest"] == ledger[-1]["digest"]
    assert invocation["release_receipt_digest"] == receipt["skill_release_receipt_digest"]
    assert invocation["target_writes"] == 0


def test_controlled_agentteams_prepared_bundle_passes_canonical_commit_gate(
    golden_pack,
) -> None:
    output, receipt = golden_pack
    prepared = PreparedFormationBundle.model_validate(
        json.loads((output / "prepared-formation-bundle.json").read_text(encoding="utf-8"))
    )
    runtime = load_enterprise_quote_pilot_pack(PACK)
    service = WorkspaceService(
        store_path=":memory:",
        workflow_run_id=receipt["run_id"],
        runtime_configuration=runtime,
    )
    try:
        committed = service.formation.commit_quote(prepared)
        assert committed.deliverable_ref == receipt["prepared_quote_ref"]
        assert service.current_quote().ref == receipt["prepared_quote_ref"]
    finally:
        service.close()


def test_canonical_commit_rejects_resealed_manager_binding_tamper(golden_pack) -> None:
    output, receipt = golden_pack
    prepared = PreparedFormationBundle.model_validate(
        json.loads((output / "prepared-formation-bundle.json").read_text(encoding="utf-8"))
    )
    receipt_write = next(
        item
        for item in prepared.artifact_writes
        if item.media_type == CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE
    )
    controlled_payload = copy.deepcopy(receipt_write.payload)
    controlled_payload.pop("digest", None)
    product_bindings = controlled_payload["manager_source_bindings"]["product"]
    first_slot = sorted(product_bindings)[0]
    product_bindings[first_slot]["value_digest"] = "sha256:" + "0" * 64
    controlled = ControlledAgentTeamsFormationReceipt.model_validate(controlled_payload)
    replacement = prepare_artifact_write(
        controlled.id,
        CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE,
        controlled.model_dump(mode="json"),
    )
    tampered_payload = prepared.model_dump(mode="json", exclude={"digest"})
    tampered_payload["artifact_writes"] = [
        replacement.model_dump(mode="json")
        if item.media_type == CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE
        else item.model_dump(mode="json")
        for item in prepared.artifact_writes
    ]
    tampered_payload["event_payload"]["controlled_agentteams_formation_receipt_digest"] = controlled.digest
    tampered = PreparedFormationBundle.model_validate(tampered_payload)

    service = WorkspaceService(
        store_path=":memory:",
        workflow_run_id=receipt["run_id"],
        runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
    )
    try:
        with pytest.raises(
            IntegrityError,
            match="CONTROLLED_AGENTTEAMS_CANDIDATE_BINDING_INVALID",
        ):
            service.formation.commit_quote(tampered)
    finally:
        service.close()


def test_each_observed_child_process_is_post_ack_and_not_the_manager(golden_pack) -> None:
    output, receipt = golden_pack
    processes = json.loads((output / "process-receipts.json").read_text(encoding="utf-8"))
    actions = {
        item["digest"]: item
        for item in json.loads((output / "agentteams/action-journal.json").read_text(encoding="utf-8"))
    }
    assert len(processes) == 7
    assert len({item["process_id"] for item in processes}) == 7
    assert all(item["process_id"] != receipt["manager_process_id"] for item in processes)
    assert all(item["independent_process"] is True for item in processes)
    assert all(actions[item["started_after_ack_action_digest"]]["action"] == "ack_task" for item in processes)
    assert len([item for item in processes if item["mode"] == "worker"]) == 5
    assert len([item for item in processes if item["mode"] == "reviewer"]) == 2


def test_http_tool_and_local_ollama_are_exactly_bound(golden_pack) -> None:
    output, receipt = golden_pack
    tool = json.loads((output / "tool/invocation.json").read_text(encoding="utf-8"))
    reviewer = json.loads(
        (output / "process-outputs" / f"{receipt['project_id']}-reviewer-a2.json").read_text(encoding="utf-8")
    )
    assert tool["receipt"]["endpoint_class"] == "LOOPBACK_TCP"
    assert tool["receipt"]["evidence_class"] == "CONTROLLED_LOCAL_REAL_HTTP"
    assert tool["receipt"]["run_id"] == receipt["run_id"]
    assert tool["receipt"]["target_writes"] == 0
    assert set(tool["result"]["source_values"]) == {"price_band"}
    finance_input = json.loads(
        (output / "process-inputs" / f"{receipt['project_id']}-finance-a2.json").read_text(encoding="utf-8")
    )
    assert finance_input["source_values"]["price_band"] == tool["result"]["source_values"]["price_band"]
    assert finance_input["supplemental_tool_result_digest"] == sha256_digest(tool["result"])
    assert reviewer["model_receipt"]["evidence_class"] == "LOCAL_OLLAMA_MODEL"
    assert reviewer["model_receipt"]["provider_request_id"] is None
    binding = reviewer["model_runtime_binding"]
    assert binding["observed_model_digest"] == OLLAMA_MODEL_DIGEST
    assert binding["claim_boundary"] == "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER"
    assert binding["request_id_source"] == "CLIENT_DERIVED_REQUEST_DIGEST"
    assert binding["client_request_id"].startswith("ollama-client:")
    assert binding["response_observation_digest"].startswith("sha256:")


def test_reviewer_rejects_candidate_provenance_mutation(golden_pack) -> None:
    output, receipt = golden_pack
    reviewer_input = json.loads(
        (output / "process-inputs" / f"{receipt['project_id']}-reviewer-a2.json").read_text(encoding="utf-8")
    )
    assert deterministic_review(reviewer_input).verdict == "PASS"
    mutated = copy.deepcopy(reviewer_input)
    product = next(item for item in mutated["domain_results"] if item["domain"] == "product")
    product["claim_candidates"][0]["source_refs"][0]["source_digest"] = "sha256:" + "0" * 64
    decision = deterministic_review(mutated)
    assert decision.verdict == "REPLAN"
    assert "product" in decision.missing_domains
    assert any("CANDIDATE_SCHEMA_INVALID:product" in code for code in decision.reason_codes)


def test_reviewer_uses_manager_bindings_not_worker_self_attestation(golden_pack) -> None:
    output, receipt = golden_pack
    reviewer_input = json.loads(
        (output / "process-inputs" / f"{receipt['project_id']}-reviewer-a2.json").read_text(encoding="utf-8")
    )
    product = next(item for item in reviewer_input["domain_results"] if item["domain"] == "product")

    projection_mutation = copy.deepcopy(reviewer_input)
    mutated_product = next(
        item for item in projection_mutation["domain_results"] if item["domain"] == "product"
    )
    mutated_product["projection_digest"] = "sha256:" + "0" * 64
    projection_decision = deterministic_review(projection_mutation)
    assert projection_decision.verdict == "REPLAN"
    assert "BINDING_PROJECTION_DIGEST_MISMATCH:product" in (projection_decision.reason_codes)

    task_mutation = copy.deepcopy(reviewer_input)
    mutated_product = next(item for item in task_mutation["domain_results"] if item["domain"] == "product")
    mutated_product["task_id"] = "malicious-task"
    bundle = dict(mutated_product["candidate_bundle"])
    bundle.pop("digest", None)
    bundle["delegation_task_ref"] = "malicious-task"
    mutated_product["candidate_bundle"] = bundle
    task_decision = deterministic_review(task_mutation)
    assert task_decision.verdict == "REPLAN"
    assert "BINDING_TASK_ID_MISMATCH:product" in task_decision.reason_codes

    # The trusted expectation never came from the mutable Worker result.
    assert reviewer_input["expected_bindings"]["product"]["task_id"] != ("malicious-task")
    assert product["task_id"] == reviewer_input["expected_bindings"]["product"]["task_id"]


def test_reviewer_rejects_resealed_candidate_value_mutation(golden_pack) -> None:
    output, receipt = golden_pack
    reviewer_input = json.loads(
        (output / "process-inputs" / f"{receipt['project_id']}-reviewer-a2.json").read_text(encoding="utf-8")
    )
    mutated = copy.deepcopy(reviewer_input)
    product = next(item for item in mutated["domain_results"] if item["domain"] == "product")
    candidate_payload = dict(product["claim_candidates"][0])
    candidate_payload.pop("digest", None)
    candidate_payload["value"] = "2099-01-01"
    candidate = ClaimCandidate.model_validate(candidate_payload)
    mutated_predicate = candidate.predicate
    product["claim_candidates"][0] = candidate.model_dump(mode="json")
    candidate_digests = [item["digest"] for item in product["claim_candidates"]]
    bundle_payload = dict(product["candidate_bundle"])
    bundle_payload.pop("digest", None)
    bundle_payload["candidate_refs"] = candidate_digests
    bundle_payload["candidate_set_digest"] = sha256_digest(sorted(candidate_digests))
    product["candidate_bundle"] = DomainCandidateBundle.model_validate(bundle_payload).model_dump(mode="json")

    decision = deterministic_review(mutated)
    assert decision.verdict == "REPLAN"
    assert f"CANDIDATE_PROVENANCE_MISMATCH:product:{mutated_predicate}" in (decision.reason_codes)
    assert "BUNDLE_BINDING_MISMATCH:product" not in decision.reason_codes


def test_formation_guard_accepts_only_exact_reviewed_result_bytes(golden_pack) -> None:
    output, receipt = golden_pack
    reviewer_input = json.loads(
        (output / "process-inputs" / f"{receipt['project_id']}-reviewer-a2.json").read_text(encoding="utf-8")
    )
    selected = {item["domain"]: item for item in reviewer_input["domain_results"]}
    reviewed = _require_reviewed_formation_inputs(reviewer_input, selected)
    assert set(reviewed) == {"product", "legal", "finance", "gtm"}

    changed = copy.deepcopy(selected)
    changed["product"]["reason_codes"] = ["MUTATED_AFTER_REVIEW"]
    with pytest.raises(GoldenCompetitionError, match="GOLDEN_REVIEWED_RESULT_SET_CHANGED"):
        _require_reviewed_formation_inputs(reviewer_input, changed)


def test_missing_ollama_fails_closed_without_tool_or_formation(tmp_path: Path) -> None:
    if not CHECKOUT.is_dir():
        pytest.skip("pinned AgentTeams checkout unavailable")
    output = tmp_path / "not-run"
    receipt = run_golden_competition(
        repo_root=ROOT,
        output_dir=output,
        checkout=CHECKOUT,
        lock_path=LOCK,
        pack_path=PACK,
        ollama_endpoint="http://127.0.0.1:1",
    )
    assert receipt["status"] == "NOT_RUN"
    assert receipt["canonical_target_writes"] == 0
    assert not (output / "tool").exists()
    assert not (output / "prepared-formation-bundle.json").exists()
    reviewer = receipt["reviewer_result"]
    assert reviewer["model_receipt"]["status"] == "NOT_RUN"
    assert reviewer["model_receipt"]["evidence_class"] == "NOT_RUN"


def test_golden_rejects_unknown_model_provider_before_runtime(tmp_path: Path) -> None:
    with pytest.raises(GoldenCompetitionError, match="GOLDEN_MODEL_PROVIDER_INVALID"):
        run_golden_competition(
            repo_root=ROOT,
            output_dir=tmp_path / "invalid-provider",
            checkout=CHECKOUT,
            lock_path=LOCK,
            pack_path=PACK,
            model_provider="implicit-fallback",
        )
