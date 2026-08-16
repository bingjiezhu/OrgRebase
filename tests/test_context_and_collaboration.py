from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from orgrebase.collaboration import CollaborationAdapter, OrchestrationCompiler
from orgrebase.context import ContextCompiler
from orgrebase.domain import AuthorizationError, EvidenceClass, IntegrityError, RunEnvelope
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set
from orgrebase.store import StateStore


def run_envelope() -> RunEnvelope:
    return RunEnvelope(
        run_id="run:test:collaboration@1",
        nonce="b" * 64,
        issued_at="2026-08-14T00:00:00Z",
        expires_at="2026-08-14T01:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )


@pytest.fixture
def compiler(fixture: EnterpriseFixture) -> ContextCompiler:
    store = StateStore()
    store.load_fixture(fixture)
    yield ContextCompiler(fixture, store)
    store.close()


def test_context_is_role_specific_and_minimal(compiler: ContextCompiler) -> None:
    sales = compiler.compile("sales-agent")
    support = compiler.compile("support-agent")
    sales_refs = {item.object_ref for item in sales.included}
    support_refs = {item.object_ref for item in support.included}
    assert sales.digest != support.digest
    assert "claim:product.enterprise_plan@v4" in sales_refs
    assert "claim:gtm.support_message@v2" not in sales_refs
    assert "claim:gtm.support_message@v2" in support_refs
    assert "claim:product.enterprise_plan@v4" not in support_refs


def test_restricted_source_is_explicitly_excluded(compiler: ContextCompiler) -> None:
    sales = compiler.compile("sales-agent")
    restricted = next(item for item in sales.excluded if item.object_ref.startswith("source:legal"))
    derived = next(item for item in sales.included if item.object_ref.startswith("claim:legal"))
    assert restricted.reason_code == "PURPOSE_NOT_ALLOWED"
    assert restricted.payload is None
    assert derived.payload["minimal_disclosure"] is True
    assert "raw_text" not in derived.payload


def test_context_fails_closed_for_unknown_actor_or_premise(compiler: ContextCompiler) -> None:
    with pytest.raises(AuthorizationError, match="unknown context profile"):
        compiler.compile("intruder")
    manifest = compiler.compile("sales-agent")
    compiler.validate_used_premises(manifest, ("claim:product.launch_date@v7",))
    with pytest.raises(AuthorizationError, match="UNSUPPORTED_PREMISE"):
        compiler.validate_used_premises(manifest, ("source:legal.customer-contract@v4",))


def test_five_agents_are_complete_candidate_only_identities(fixture: EnterpriseFixture) -> None:
    adapter = CollaborationAdapter(fixture)
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    collaboration = adapter.run(change_set, preview, run_envelope())
    assert collaboration["identity_validation"]["status"] == "PASS"
    assert collaboration["identity_validation"]["agent_count"] == 5
    assert len(collaboration["handoffs"]) == 5
    assert len(collaboration["agent_runs"]) == 5
    assert all(handoff.candidate_only for handoff in collaboration["handoffs"])
    assert all(
        {change_set.digest, preview.digest, preview.revision_lock.digest}
        == set(task.input_refs)
        for task in collaboration["orchestration_plan"].tasks
    )
    task_by_id = {task.id: task for task in collaboration["orchestration_plan"].tasks}
    assert all(
        tuple(handoff.payload["input_refs"]) == task_by_id[handoff.task_id].input_refs
        for handoff in collaboration["handoffs"]
    )
    assert collaboration["coordination_receipt"].status == "PASS"
    assert collaboration["coordination_receipt"].orchestration_plan_digest == collaboration[
        "orchestration_plan"
    ].digest
    ingestion = collaboration["candidate_ingestion"]
    assert ingestion.target_writes == 0
    assert len(ingestion.decisions) == 5
    assert not ingestion.rejected_candidate_digests
    assert all(not item.admitted_effects for item in ingestion.decisions)
    compilation = collaboration["compilation_receipt"]
    assert compilation.orchestration_plan_digest == collaboration["orchestration_plan"].digest
    assert compilation.task_intents_digest
    assert len(compilation.identity_digests) == 5
    assert collaboration["live_runtime"]["status"] == "NOT_RUN"


def test_agent_requests_cannot_become_canonical_state(fixture: EnterpriseFixture) -> None:
    adapter = CollaborationAdapter(fixture)
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    collaboration = adapter.run(change_set, preview, run_envelope())
    assert adapter.prove_agents_cannot_decide_state(collaboration, preview)

    legal = next(item for item in collaboration["handoffs"] if item.from_agent == "legal-steward")
    forged = legal.model_copy(
        update={
            "payload": {**legal.payload, "impact_candidate": "AFFECTED_HARD"},
            "digest": "",
        }
    )
    forged_collaboration = {
        **collaboration,
        "handoffs": tuple(
            forged if item.from_agent == "legal-steward" else item
            for item in collaboration["handoffs"]
        ),
    }
    assert not adapter.prove_agents_cannot_decide_state(forged_collaboration, preview)


def test_orchestration_compiler_rejects_undeclared_capability(
    fixture: EnterpriseFixture,
) -> None:
    adapter = CollaborationAdapter(fixture)
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    plan = adapter.orchestration_compiler.compile(change_set, preview)
    mutated_task = plan.tasks[1].model_copy(
        update={"required_capabilities": ("write canonical state",), "digest": ""}
    )
    mutated_plan = plan.model_copy(
        update={"tasks": (plan.tasks[0], mutated_task, *plan.tasks[2:]), "digest": ""}
    )
    with pytest.raises(IntegrityError, match="undeclared Agent capability"):
        adapter.orchestration_compiler.verify_plan(mutated_plan)


def test_orchestration_execution_rejects_duplicate_or_extra_handoffs(
    fixture: EnterpriseFixture,
) -> None:
    adapter = CollaborationAdapter(fixture)
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    collaboration = adapter.run(change_set, preview, run_envelope())
    compiler = OrchestrationCompiler(fixture)
    extra = collaboration["handoffs"][0]
    with pytest.raises(IntegrityError, match="exactly cover"):
        compiler.verify_execution(
            plan=collaboration["orchestration_plan"],
            handoffs=(*collaboration["handoffs"], extra),
            runs=collaboration["agent_runs"],
            run_envelope=run_envelope(),
        )


FROZEN_LIVE_PLAN_DIGEST = (
    "sha256:2869a6106aa4b032b4c12370f6e437b3561fc6f4d05c1cd0145bc3bf5738f85d"
)


def test_file_backed_compile_keeps_frozen_live_plan_digest(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    plan = OrchestrationCompiler(fixture).compile(change_set, preview)
    assert plan.digest == FROZEN_LIVE_PLAN_DIGEST


def test_disk_identity_drift_fails_closed(
    fixture: EnterpriseFixture, tmp_path: Path
) -> None:
    source = Path(__file__).resolve().parents[1] / "agentteams" / "identities"
    dest = tmp_path / "identities"
    shutil.copytree(source, dest)
    legal = json.loads((dest / "legal-steward.json").read_text(encoding="utf-8"))
    legal["capabilities"] = ["write canonical state"]
    (dest / "legal-steward.json").write_text(json.dumps(legal), encoding="utf-8")
    with pytest.raises(IntegrityError, match="drifted from disk contract"):
        OrchestrationCompiler(fixture, identities_dir=dest)


def test_task_intent_undeclared_capability_fails_closed(
    fixture: EnterpriseFixture, tmp_path: Path
) -> None:
    source = Path(__file__).resolve().parents[1] / "orchestration" / "task-intents.json"
    dest = tmp_path / "task-intents.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["intents"][1]["required_capabilities"] = ["write canonical state"]
    dest.write_text(json.dumps(payload), encoding="utf-8")
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    compiler = OrchestrationCompiler(fixture, intents_path=dest)
    with pytest.raises(IntegrityError, match="undeclared Agent capability"):
        compiler.compile(change_set, preview)
