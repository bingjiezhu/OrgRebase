from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    CaseObservation,
    FeatureProfile,
    GovernedPatternService,
    ProposalBudget,
    ReplayCase,
    SkillBoundary,
    _record,
)
from orgrebase.workspace.skill_packages import PARTITIONS, InvocationContext


@dataclass
class Clock:
    now: float = 1800000000.0

    def __call__(self):
        return self.now


def public(**updates):
    value = {
        "run_id": "run:pattern-eval",
        "task_id": "task:handoff",
        "delegation_id": "delegation:finance",
        "delegation_task_digest": sha256_digest({"task": "handoff"}),
        "context_projection_digest": sha256_digest({"context": "finance"}),
        "candidate_bundle": {"domain": "finance", "region": "EU", "claims": [{"ref": "claim:price"}]},
    }
    value.update(updates)
    return value


def observed(case_id, outcome):
    value = public()
    cert = _record(
        "outcome-observation",
        case_id=case_id,
        case_revision="1",
        input_digest=sha256_digest(value),
        outcome=outcome,
        issuer="scripted:corpus-controller",
        evidence_scope="SYNTHETIC_LOCAL",
    )
    return CaseObservation(
        case_id=case_id, revision="1", public_input=value, outcome=outcome, certificate=cert
    )


def replay_cases():
    values = []
    for part in PARTITIONS:
        value, expected, role, pair = public(), "HANDOFF", "REGRESSION", None
        if part == "HELD_OUT":
            role = "HELD_OUT"
            value["candidate_bundle"]["claims"] = [{"ref": "claim:tax-held-out"}]
        elif part == "NEGATIVE_TRANSFER":
            value["candidate_bundle"]["region"] = "US"
            role, pair = "COUNTERFACTUAL", "replay:REPLAY"
        elif part == "PERMISSION":
            value["permission_expansion"], expected = True, "DENY"
        elif part == "INJECTION":
            value["prompt_injection"], expected = True, "ABSTAIN"
        elif part == "MALFORMED":
            value.pop("candidate_bundle")
            expected = "ABSTAIN"
        elif part == "RESOURCE_OR_DEADLINE":
            value["deadline_expired"], expected = True, "ABSTAIN"
        elif part == "CANARY":
            role = "PRIOR_VERSION"
        values.append(
            ReplayCase(
                case_id=f"replay:{part}",
                partition=part,
                role=role,
                public_input=value,
                expected_action=expected,
                counterfactual_of=pair,
            )
        )
    return tuple(values)


@pytest.fixture
def setup(tmp_path):
    store = StateStore(tmp_path / "pattern.sqlite3")
    clock = Clock()
    service = GovernedPatternService(
        store,
        corpus_authority="scripted:corpus-controller",
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
        clock=clock,
        prerequisite_resolver=lambda actor, ref: actor == "scripted:qualified-reviewer",
    )
    yield service, store, clock
    store.close()


def prepare(service, *, paths=("/candidate_bundle/domain",), budget=None, cases=None):
    profile = FeatureProfile(
        profile_id="finance-handoff",
        revision="1",
        paths=paths,
        transfer_scope="declared handoff semantic fields only",
    )
    corpus = service.freeze_corpus(
        profile,
        cases
        or tuple(
            observed(f"case:{i}", outcome)
            for i, outcome in enumerate(("SUPPORT", "SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN"))
        ),
        actor_id="scripted:corpus-controller",
    )
    suite = service.freeze_replay(replay_cases(), actor_id="scripted:replay-controller")
    proposal = service.open_proposal(
        proposal_id="proposal:1",
        corpus_ref=corpus,
        replay_ref=suite,
        skill_name="structured-domain-handoff",
        author_id="learner:bounded",
        budget=budget or ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=100),
    )
    package = service.registry.load("structured-domain-handoff")
    boundary = SkillBoundary(
        preconditions=("declared domain matches",),
        required_knowledge=("knowledge:handoff-v1",),
        required_qualifications=("qualification:handoff-review",),
        evidence_duties=("exact task and delegation digest",),
        rollback_package_digest=package.package_digest,
        known_failure_envelope=("missing provenance abstains", "permission expansion denies"),
    )
    return proposal, boundary, corpus, suite


def qualify(service):
    proposal, boundary, corpus, suite = prepare(service)
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    evaluation = service.evaluate(candidate, actor_id="scripted:replay-controller")
    return candidate, evaluation, proposal, corpus, suite


def decide(service, candidate, evaluation):
    return service.decide(
        candidate,
        evaluation,
        actor_id="scripted:skill-governance",
        verdict="ADMIT",
        expected_candidate_digest=service._load(candidate)["digest"],
    )


def test_declared_grouping_retains_complete_support_counterexample_null_unknown(setup):
    service, store, _ = setup
    candidate, evaluation, _, corpus_ref, suite_ref = qualify(service)
    record = service._load(candidate)
    pattern = service._load(record["pattern_ref"])
    assert pattern["feature_values"] == {"/candidate_bundle/domain": "finance"}
    assert {key: len(value) for key, value in pattern["membership"].items()} == {
        "SUPPORT": 2,
        "COUNTEREXAMPLE": 1,
        "NULL": 1,
        "UNKNOWN": 1,
    }
    assert len(pattern["case_refs"]) == len(pattern["certificate_refs"]) == 5
    assert pattern["corpus_ref"] == corpus_ref
    assert record["replay_ref"] == suite_ref
    assert record["status"] == "CANDIDATE" and record["executable"] is False
    assert pattern["similarity_edges"] == 0
    assert service._load(evaluation)["verdict"] == "QUALIFIED"
    assert store.connection.execute("SELECT COUNT(*) FROM current_pointers").fetchone()[0] == 0


def test_candidate_executes_real_inputs_and_counterfactual_rejects_spurious_region(setup):
    service, _, _ = setup
    proposal, boundary, _, _ = prepare(service, paths=("/candidate_bundle/region",))
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    evaluation = service._load(service.evaluate(candidate, actor_id="scripted:replay-controller"))
    current = {item["case_ref"]: item for item in evaluation["current"]["case_results"]}
    prior = {item["case_ref"]: item for item in evaluation["prior"]["case_results"]}
    assert current["replay:NEGATIVE_TRANSFER"]["passed"] is False
    assert prior["replay:NEGATIVE_TRANSFER"]["passed"] is True
    assert evaluation["verdict"] == "REJECTED"
    with pytest.raises(IntegrityError, match="NOT_QUALIFIED"):
        decide(service, candidate, service.evaluate(candidate, actor_id="scripted:replay-controller"))


def test_governance_source_and_existing_ledger_invocation_survive_restart(setup):
    service, store, clock = setup
    candidate, evaluation, _, _, _ = qualify(service)
    decision_ref = decide(service, candidate, evaluation)
    decision = service._load(decision_ref)
    assert decision["human_review_verified"] is False
    (admission,) = service._family("admission")
    source_id, version = admission["source_ref"].rsplit("@", 1)
    source = store.get_object(source_id, version)
    assert source.kind == "Source" and source.state is ObjectState.CURRENT
    assert source.digest == admission["source_digest"]
    assert [item["to_state"] for item in source.payload["release_history"]] == [
        "EVALUATED",
        "SHADOW",
        "CANARY",
    ]
    path = store.path
    store.close()
    reopened = StateStore(path)
    try:
        resumed = GovernedPatternService(
            reopened,
            corpus_authority=service.corpus_authority,
            evaluator_authority=service.evaluator_authority,
            governance_authority=service.governance_authority,
            clock=clock,
            prerequisite_resolver=service.prerequisite_resolver,
        )
        value = public()
        invocation = resumed.invoke(
            candidate,
            value,
            context=InvocationContext(
                value["run_id"], value["task_id"], value["delegation_id"], "scripted:qualified-reviewer"
            ),
            knowledge_refs=("knowledge:handoff-v1",),
            qualification_refs=("qualification:handoff-review",),
        )
        assert invocation.result["action"] == "HANDOFF"
        assert invocation.receipt["authorization_mode"] == "RELEASE"
        assert invocation.result["target_writes"] == 0
        assert source.digest == reopened.get_object(source_id).digest
        assert reopened.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()


@pytest.mark.parametrize("wrong", ["learner:bounded", "scripted:replay-controller", "untrusted:caller"])
def test_separate_governance_cannot_be_forged(setup, wrong):
    service, _, _ = setup
    candidate, evaluation, _, _, _ = qualify(service)
    with pytest.raises(AuthorizationError, match="AUTHORITY_DENIED"):
        service.decide(
            candidate,
            evaluation,
            actor_id=wrong,
            verdict="ADMIT",
            expected_candidate_digest=service._load(candidate)["digest"],
        )


def test_candidate_exact_digest_and_replay_suite_are_frozen(setup):
    service, _, _ = setup
    candidate, evaluation, _, _, suite = qualify(service)
    before = service._load(suite)
    with pytest.raises(AuthorizationError, match="BINDING_MISMATCH"):
        service.decide(
            candidate,
            evaluation,
            actor_id=service.governance_authority,
            verdict="ADMIT",
            expected_candidate_digest=sha256_digest({"other": "candidate"}),
        )
    assert service._load(suite) == before
    with pytest.raises(AuthorizationError):
        service.freeze_replay(replay_cases(), actor_id="learner:bounded")


def test_case_certificate_tamper_and_corpus_authority_fail_closed(setup):
    service, _, _ = setup
    case = observed("case:1", "SUPPORT")
    value = case.model_dump(mode="json")
    value["public_input"]["permission_expansion"] = True
    with pytest.raises(ValueError):
        CaseObservation.model_validate(value)
    with pytest.raises(AuthorizationError):
        service.freeze_corpus(
            FeatureProfile(profile_id="p", revision="1", paths=("/task_id",), transfer_scope="local"),
            (case,),
            actor_id="learner:bounded",
        )


def test_prior_null_and_counterexample_cannot_be_removed_from_frozen_corpus(setup):
    service, _, _ = setup
    proposal, boundary, corpus, _ = prepare(service)
    original = service._load(corpus)
    snapshot = deepcopy(original)
    snapshot["cases"].pop()
    with pytest.raises(IntegrityError, match="DIGEST_MISMATCH"), service.store.transaction() as connection:
        service._save(connection, snapshot)
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    assert service._load(service._load(candidate)["pattern_ref"])["corpus_ref"] == corpus
    assert service._load(corpus) == original


def test_missing_counterexample_cannot_earn_admission(setup):
    service, _, _ = setup
    proposal, boundary, _, _ = prepare(service, cases=(observed("a", "SUPPORT"), observed("b", "SUPPORT")))
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    evaluation = service.evaluate(candidate, actor_id=service.evaluator_authority)
    assert service._load(evaluation)["verdict"] == "REJECTED"


@pytest.mark.parametrize("dimension", ["cases", "calls", "time"])
def test_budget_exhaustion_records_real_usage_and_preserves_current_truth(setup, dimension):
    service, store, clock = setup
    budget = ProposalBudget(
        max_cases=1 if dimension == "cases" else 10,
        max_skill_invocations=1 if dimension == "calls" else 100,
        max_seconds=1,
    )
    proposal, boundary, _, _ = prepare(service, budget=budget)
    if dimension == "time":
        clock.now += 2
    with pytest.raises(IntegrityError, match="BUDGET_EXHAUSTED"):
        (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        service.evaluate(candidate, actor_id=service.evaluator_authority)
    assert service._terminal(proposal)["status"] == "EXHAUSTED"
    if dimension == "calls":
        assert sum(item["invocations"] for item in service._usage(proposal)) == 1
    assert store.connection.execute("SELECT COUNT(*) FROM current_pointers").fetchone()[0] == 0


def test_abandon_records_reason_without_source_mutation(setup):
    service, store, _ = setup
    proposal, boundary, _, _ = prepare(service)
    ref = service.abandon(proposal, actor_id="learner:bounded", reason="insufficient transfer support")
    assert service._load(ref)["status"] == "ABANDONED"
    with pytest.raises(IntegrityError, match="TERMINAL"):
        service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    assert store.connection.execute("SELECT COUNT(*) FROM current_pointers").fetchone()[0] == 0


def test_case_retraction_propagates_through_skill_source_plan_and_compatibility(setup):
    service, store, _ = setup
    candidate, evaluation, _, corpus, _ = qualify(service)
    decide(service, candidate, evaluation)
    (admission,) = service._family("admission")
    plan = service.register_successor(
        admission["source_ref"],
        kind="plan",
        payload=_record("plan-proof", valid=True),
        actor_id=service.governance_authority,
    )
    claim = service.register_successor(
        plan,
        kind="compatibility",
        payload=_record("compatibility-proof", scope="local"),
        actor_id=service.governance_authority,
    )
    case_ref = service._load(corpus)["cases"][0]["case"]["certificate"]["digest"]
    receipt = service._load(
        service.retract(case_ref, actor_id=service.governance_authority, reason="oracle retracted")
    )
    assert {candidate, admission["source_ref"], plan, claim}.issubset(receipt["affected_refs"])
    assert service.evidence_status(claim) == "REQUALIFICATION_REQUIRED"
    source_id = admission["source_ref"].rsplit("@", 1)[0]
    assert store.get_object(source_id).state is ObjectState.REQUALIFICATION_REQUIRED
    value = public()
    with pytest.raises(AuthorizationError, match="SOURCE_RETRACTED"):
        service.invoke(
            candidate,
            value,
            context=InvocationContext(
                value["run_id"], value["task_id"], value["delegation_id"], "scripted:qualified-reviewer"
            ),
            knowledge_refs=("knowledge:handoff-v1",),
            qualification_refs=("qualification:handoff-review",),
        )


def test_candidate_boundary_requires_rollback_and_no_new_tools(setup):
    service, _, _ = setup
    proposal, boundary, _, _ = prepare(service)
    invalid = boundary.model_dump(mode="json")
    invalid.pop("digest")
    invalid["allowed_tools"] = ["unadmitted:write"]
    with pytest.raises(IntegrityError, match="BOUNDARY_WIDENED"):
        service.propose(proposal, actor_id="learner:bounded", boundary=SkillBoundary.model_validate(invalid))


def test_undeclared_metadata_does_not_change_semantic_cluster(setup):
    service, _, _ = setup
    proposal, boundary, _, _ = prepare(service)
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    pattern = service._load(service._load(candidate)["pattern_ref"])
    assert set(pattern["feature_values"]) == {"/candidate_bundle/domain"}
    assert "region" not in str(pattern["feature_values"])


def test_retraction_before_review_cannot_be_bypassed_by_new_source(setup):
    service, _, _ = setup
    candidate, evaluation, _, corpus, _ = qualify(service)
    ref = service._load(corpus)["cases"][0]["case"]["digest"]
    service.retract(ref, actor_id=service.governance_authority, reason="source case invalid")
    with pytest.raises(IntegrityError, match="EVIDENCE_RETRACTED"):
        decide(service, candidate, evaluation)
    assert service._family("admission") == ()


def test_untrusted_qualification_strings_do_not_authorize_invocation(setup):
    service, _, _ = setup
    candidate, evaluation, _, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    value = public()
    with pytest.raises(AuthorizationError, match="PREREQUISITES_MISSING"):
        service.invoke(
            candidate,
            value,
            context=InvocationContext(
                value["run_id"], value["task_id"], value["delegation_id"], "learner:bounded"
            ),
            knowledge_refs=("knowledge:handoff-v1",),
            qualification_refs=("qualification:handoff-review",),
        )


def test_retracted_source_cannot_issue_fresh_successor_claims(setup):
    service, _, _ = setup
    candidate, evaluation, _, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    source = service._family("admission")[0]["source_ref"]
    service.retract(source, actor_id=service.governance_authority, reason="retracted")
    with pytest.raises(IntegrityError, match="PARENT_RETRACTED"):
        service.register_successor(
            source,
            kind="plan",
            payload=_record("plan-proof", valid=True),
            actor_id=service.governance_authority,
        )


def test_replay_without_counterfactual_and_training_overlap_are_rejected(setup):
    service, _, _ = setup
    cases = list(replay_cases())
    counter = next(i for i, item in enumerate(cases) if item.role == "COUNTERFACTUAL")
    raw = cases[counter].model_dump(mode="json")
    raw.pop("digest")
    raw["public_input"] = public()
    cases[counter] = ReplayCase.model_validate(raw)
    with pytest.raises(IntegrityError, match="COUNTERFACTUAL_NOT_CHANGED"):
        service.freeze_replay(tuple(cases), actor_id=service.evaluator_authority)
    with pytest.raises(IntegrityError, match="TRAINING_REPLAY_OVERLAP"):
        prepare(service, cases=(observed("replay:HELD_OUT", "SUPPORT"),))


def test_abandoned_proposal_cannot_be_admitted_after_qualification(setup):
    service, store, _ = setup
    candidate, evaluation, proposal, _, _ = qualify(service)
    service.abandon(proposal, actor_id="learner:bounded", reason="drop candidate")
    with pytest.raises(IntegrityError, match="TERMINAL"):
        decide(service, candidate, evaluation)
    assert store.connection.execute("SELECT COUNT(*) FROM current_pointers").fetchone()[0] == 0


def test_original_package_is_unchanged_after_candidate_lifecycle(setup):
    service, _, _ = setup
    before = service.registry.load("structured-domain-handoff")
    candidate, evaluation, _, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    after = service.registry.load("structured-domain-handoff")
    assert (after.package_digest, after.resource_digests, after.program) == (
        before.package_digest,
        before.resource_digests,
        before.program,
    )


def test_retracted_case_cannot_generate_a_fresh_candidate(setup):
    service, _, _ = setup
    proposal, boundary, corpus, _ = prepare(service)
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    case_ref = service._load(corpus)["cases"][0]["case"]["digest"]
    service.retract(case_ref, actor_id=service.governance_authority, reason="invalid source")
    changed = boundary.model_dump(mode="json")
    changed.pop("digest")
    changed["preconditions"] = ["fresh wording must not launder retracted support"]
    with pytest.raises(IntegrityError, match="RETRACTED"):
        service.propose(proposal, actor_id="learner:bounded", boundary=SkillBoundary.model_validate(changed))
    assert len(service._family("skill-candidate")) == 1
    assert service.evidence_status(candidate) == "REQUALIFICATION_REQUIRED"


def test_admitted_skill_does_not_replay_or_spend_expired_proposal_budget(setup, monkeypatch):
    service, _, clock = setup
    candidate, evaluation, proposal, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    usage = service._usage(proposal)
    clock.now += 1000
    from orgrebase.workspace.skill_packages import SkillPackageEvaluator

    def forbidden(*args, **kwargs):
        raise AssertionError("admitted invocation must not rerun candidate evaluation")

    monkeypatch.setattr(SkillPackageEvaluator, "evaluate", forbidden)
    value = public()
    invocation = service.invoke(
        candidate,
        value,
        context=InvocationContext(
            value["run_id"], value["task_id"], value["delegation_id"], "scripted:qualified-reviewer"
        ),
        knowledge_refs=("knowledge:handoff-v1",),
        qualification_refs=("qualification:handoff-review",),
    )
    assert invocation.result["action"] == "HANDOFF"
    assert service._usage(proposal) == usage


def test_admission_finalizes_proposal_and_forbids_abandonment(setup):
    service, store, _ = setup
    candidate, evaluation, proposal, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    before = store.state_snapshot([service._family("admission")[0]["source_ref"].rsplit("@", 1)[0]])
    with pytest.raises(IntegrityError, match="TERMINAL"):
        service.abandon(proposal, actor_id="learner:bounded", reason="cannot undo an admitted source")
    assert service._terminal(proposal)["status"] == "ADMITTED"
    assert store.state_snapshot(list(before)) == before


def test_restored_history_requires_the_exact_canonical_admitted_source(setup):
    from orgrebase.workspace.skill_packages import SkillPackageEvaluator, SkillReleaseLedger

    service, store, _ = setup
    candidate, evaluation, _, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    record = service._load(candidate)
    source_ref = service._family("admission")[0]["source_ref"]
    source = store.get_object(source_ref.rsplit("@", 1)[0])
    overlay = service._overlay(record)
    history = deepcopy(source.payload["release_history"])
    history[-1]["reason_codes"] = ["forged but resealed"]
    history[-1]["digest"] = sha256_digest(
        {key: value for key, value in history[-1].items() if key != "digest"}
    )
    ledger = SkillReleaseLedger(overlay, SkillPackageEvaluator(overlay))
    with pytest.raises(IntegrityError, match="CANONICAL_ADMISSION_MISMATCH"):
        ledger._restore_verified_history(
            record["skill_name"],
            service._load(evaluation)["current"],
            history,
            store=store,
            source_ref=source_ref,
            governance_authority=service.governance_authority,
            evaluator_authority=service.evaluator_authority,
        )
    assert ledger.head(record["skill_name"]) is None
    assert ledger.history == ()


def test_invocation_budget_is_independent_and_records_actual_release_calls(setup):
    from orgrebase.workspace.pattern_evolution import SkillInvocationBudget

    service, _, clock = setup
    candidate, evaluation, proposal, _, _ = qualify(service)
    decide(service, candidate, evaluation)
    service.invocation_budget = SkillInvocationBudget(max_invocations=1, max_seconds=10)
    clock.now += 1000
    usage_before = service._usage(proposal)
    value = public()
    arguments = {
        "context": InvocationContext(
            value["run_id"], value["task_id"], value["delegation_id"], "scripted:qualified-reviewer"
        ),
        "knowledge_refs": ("knowledge:handoff-v1",),
        "qualification_refs": ("qualification:handoff-review",),
    }
    assert service.invoke(candidate, value, **arguments).result["action"] == "HANDOFF"
    with pytest.raises(IntegrityError, match="INVOCATION_BUDGET_EXHAUSTED"):
        service.invoke(candidate, value, **arguments)
    assert len(service._family("invocation-reservation")) == len(service._family("invocation-result")) == 1
    assert len(service._family("invocation-denial")) == 1
    assert service._usage(proposal) == usage_before
    assert service._terminal(proposal)["status"] == "ADMITTED"


def test_incomplete_retraction_closure_does_not_mark_any_source_as_safe(setup, monkeypatch):
    from orgrebase.impact import ImpactEngine

    service, store, _ = setup
    candidate, evaluation, _, corpus, _ = qualify(service)
    decide(service, candidate, evaluation)
    source = service._family("admission")[0]["source_ref"]
    case_ref = service._load(corpus)["cases"][0]["case"]["digest"]
    monkeypatch.setattr(ImpactEngine, "max_depth", 1)
    with pytest.raises(IntegrityError, match="CLOSURE_INCOMPLETE"):
        service.retract(case_ref, actor_id=service.governance_authority, reason="retraction")
    assert service._family("retraction") == ()
    assert store.get_object(source.rsplit("@", 1)[0]).state is ObjectState.CURRENT


def test_held_out_cannot_reuse_training_input_under_another_case_id(setup):
    service, _, _ = setup
    profile = FeatureProfile(
        profile_id="finance", revision="1", paths=("/candidate_bundle/domain",), transfer_scope="local"
    )
    corpus = service.freeze_corpus(
        profile, (observed("train:one", "SUPPORT"),), actor_id=service.corpus_authority
    )
    cases = []
    for case in replay_cases():
        if case.role == "HELD_OUT":
            body = case.model_dump(mode="json")
            body.pop("digest")
            body["public_input"] = public()
            case = ReplayCase.model_validate(body)
        cases.append(case)
    suite = service.freeze_replay(tuple(cases), actor_id=service.evaluator_authority)
    with pytest.raises(IntegrityError, match="HELD_OUT_INPUT_LEAKAGE"):
        service.open_proposal(
            proposal_id="attempt:leak",
            corpus_ref=corpus,
            replay_ref=suite,
            skill_name="structured-domain-handoff",
            author_id="learner:bounded",
            budget=ProposalBudget(max_cases=10, max_skill_invocations=30, max_seconds=100),
        )
