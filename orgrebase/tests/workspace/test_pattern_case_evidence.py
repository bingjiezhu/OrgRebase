"""Trusted retained evidence joins the existing certificate dependency path."""

from __future__ import annotations

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    CaseObservation,
    FeatureProfile,
    GovernedPatternService,
    ProposalBudget,
    _record,
)
from tests.workspace.test_pattern_evolution import observed, prepare


def external_case(case_id="case:retained", outcome="SUPPORT"):
    base = observed(case_id, outcome)
    certificate = {
        key: value
        for key, value in base.certificate.items()
        if key not in {"digest", "kind", "schema_version"}
    }
    certificate.update(
        profile_id="test:retained-evidence-controller", evidence_scope="SYNTHETIC_LOCAL_SERVICE_CONTRACT_ONLY"
    )
    return CaseObservation(
        case_id=base.case_id,
        revision=base.revision,
        public_input=base.public_input,
        outcome=base.outcome,
        certificate=_record("retail-outcome-case-candidate", **certificate),
    )


def service(store, resolver=None):
    return GovernedPatternService(
        store,
        corpus_authority="scripted:corpus-controller",
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
        case_evidence_resolver=resolver,
    )


def freeze(controller, case):
    profile = FeatureProfile(
        profile_id="test:retained-evidence",
        revision="1",
        paths=("/candidate_bundle/domain",),
        transfer_scope="scripted contract regression only",
    )
    return controller.freeze_corpus(profile, (case,), actor_id="scripted:corpus-controller")


def test_retained_case_cannot_self_assert_external_certificate_authority(tmp_path):
    with StateStore(tmp_path / "missing-resolver.sqlite3") as store:
        controller = service(store)
        with pytest.raises(AuthorizationError, match="PATTERN_CASE_EVIDENCE_RESOLVER_REQUIRED"):
            freeze(controller, external_case())
        assert controller._family("corpus") == ()


@pytest.mark.parametrize(
    "resolved",
    (
        None,
        True,
        (),
        ("unverified",),
        [sha256_digest("external")],
        (sha256_digest("external"), sha256_digest("external")),
    ),
)
def test_resolver_must_return_exact_distinct_verified_certificate_digests(tmp_path, resolved):
    with StateStore(tmp_path / "invalid-resolution.sqlite3") as store:
        controller = service(store, lambda case: resolved)
        with pytest.raises(IntegrityError, match="PATTERN_CASE_RESOLVED_CERTIFICATES_INVALID"):
            freeze(controller, external_case())
        assert controller._family("corpus") == ()


def test_case_own_certificate_cannot_masquerade_as_external_outcome(tmp_path):
    with StateStore(tmp_path / "own-digest.sqlite3") as store:
        controller = service(store, lambda case: (case.certificate["digest"],))
        with pytest.raises(IntegrityError, match="PATTERN_CASE_RESOLVED_CERTIFICATES_INVALID"):
            freeze(controller, external_case())


def test_failed_external_verification_does_not_admit_corpus(tmp_path):
    def reject(case):
        raise IntegrityError("TRUSTED_EVIDENCE_SCOPE_OR_PAYLOAD_MISMATCH")

    with StateStore(tmp_path / "rejected-evidence.sqlite3") as store:
        controller = service(store, reject)
        with pytest.raises(IntegrityError, match="TRUSTED_EVIDENCE_SCOPE_OR_PAYLOAD_MISMATCH"):
            freeze(controller, external_case())
        assert controller._family("corpus") == ()


def test_external_outcome_retraction_reaches_admitted_source_plan_claim_and_late_proposal(tmp_path):
    cases = tuple(
        external_case(f"case:{i}", outcome)
        for i, outcome in enumerate(("SUPPORT", "SUPPORT", "COUNTEREXAMPLE"))
    )
    resolved = {
        case.digest: tuple(sha256_digest(["verified-external-outcome", case.case_id, n]) for n in (0, 1))
        for case in cases
    }
    calls = []

    def resolve(case):
        calls.append(case.digest)
        return resolved[case.digest]

    with StateStore(tmp_path / "typed-outcome-closure.sqlite3") as store:
        controller = service(store, resolve)
        proposal, boundary, corpus_ref, suite_ref = prepare(controller, cases=cases)
        assert calls == [case.digest for case in cases]
        corpus = controller._load(corpus_ref)
        for row, case in zip(corpus["cases"], cases, strict=True):
            assert set(row["certificate_refs"]) == {case.certificate["digest"], *resolved[case.digest]}
        (candidate,) = controller.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        record = controller._load(candidate)
        pattern = controller._load(record["pattern_ref"])
        assert len(pattern["case_refs"]) == 3 and len(pattern["certificate_refs"]) == 9
        evaluation_ref = controller.evaluate(candidate, actor_id="scripted:replay-controller")
        assert controller._load(evaluation_ref)["verdict"] == "QUALIFIED"
        controller.decide(
            candidate,
            evaluation_ref,
            actor_id="scripted:skill-governance",
            verdict="ADMIT",
            expected_candidate_digest=record["digest"],
        )
        (admission,) = controller._family("admission")
        plan = controller.register_successor(
            admission["source_ref"],
            kind="plan",
            payload=_record("test-plan", test_scope="SYNTHETIC_LOCAL"),
            actor_id="scripted:skill-governance",
        )
        claim = controller.register_successor(
            plan,
            kind="compatibility",
            payload=_record("test-claim", test_scope="SYNTHETIC_LOCAL"),
            actor_id="scripted:skill-governance",
        )
        original_outcome = resolved[cases[0].digest][0]
        withdrawal = controller._load(
            controller.retract(
                original_outcome,
                actor_id="scripted:skill-governance",
                reason="Withdraw exact externally verified outcome",
            )
        )
        assert {
            original_outcome,
            record["pattern_ref"],
            candidate,
            admission["source_ref"],
            plan,
            claim,
        } <= set(withdrawal["affected_refs"])
        assert controller.evidence_status(candidate) == "REQUALIFICATION_REQUIRED"
        second = controller.open_proposal(
            proposal_id="proposal:after-withdrawal",
            corpus_ref=corpus_ref,
            replay_ref=suite_ref,
            skill_name="structured-domain-handoff",
            author_id="learner:bounded",
            budget=ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=100),
        )
        with pytest.raises(IntegrityError, match="PATTERN_DEPENDENCY_PROVIDER_RETRACTED"):
            controller.propose(second, actor_id="learner:bounded", boundary=boundary)
        assert len(controller._family("skill-candidate")) == 1


def test_external_certificate_withdrawal_between_freeze_and_propose_blocks_late_use(tmp_path):
    case = external_case()
    certificate_ref = sha256_digest("test:verified-frozen-outcome")
    with StateStore(tmp_path / "frozen-before-proposal.sqlite3") as store:
        controller = service(store, lambda candidate: (certificate_ref,))
        proposal, boundary, corpus_ref, _ = prepare(controller, cases=(case,))
        withdrawal = controller._load(
            controller.retract(
                certificate_ref,
                actor_id="scripted:skill-governance",
                reason="Withdraw source before candidate generation",
            )
        )
        assert {certificate_ref, case.digest} <= set(withdrawal["affected_refs"])
        with pytest.raises(IntegrityError, match="PATTERN_DEPENDENCY_PROVIDER_RETRACTED"):
            controller.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        assert controller._family("pattern") == () and controller._family("skill-candidate") == ()
        with pytest.raises(IntegrityError, match="PATTERN_DEPENDENCY_PROVIDER_RETRACTED"):
            freeze(controller, case)
        assert len(controller._family("corpus")) == 1
        assert controller._load(corpus_ref)["cases"][0]["case"]["digest"] == case.digest


@pytest.mark.parametrize("timing", ("before", "first-invocation", "after-prior-evaluation"))
def test_retraction_cannot_create_fresh_formal_evaluation(tmp_path, monkeypatch, timing):
    from orgrebase.workspace.pattern_evolution import SkillCandidateOverlayRegistry, SkillPackageEvaluator

    cases = tuple(
        external_case(f"case:{i}", outcome)
        for i, outcome in enumerate(("SUPPORT", "SUPPORT", "COUNTEREXAMPLE"))
    )
    external = {case.digest: (sha256_digest(["evaluated-outcome", case.case_id]),) for case in cases}
    with StateStore(tmp_path / "evaluation-retracted.sqlite3") as store:
        controller = service(store, lambda case: external[case.digest])
        proposal, boundary, _, _ = prepare(controller, cases=cases)
        (candidate,) = controller.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        calls = []

        def withdraw():
            controller.retract(
                external[cases[0].digest][0],
                actor_id="scripted:skill-governance",
                reason="Evidence withdrawn during qualification",
            )

        if timing == "before":
            withdraw()
            original = SkillPackageEvaluator.evaluate

            def evaluate(*args, **kwargs):
                calls.append("evaluate")
                return original(*args, **kwargs)

            monkeypatch.setattr(SkillPackageEvaluator, "evaluate", evaluate)
        elif timing == "first-invocation":
            original = SkillCandidateOverlayRegistry.invoke_for_evaluation

            def invoke(*args, **kwargs):
                result = original(*args, **kwargs)
                calls.append("invoke")
                if len(calls) == 1:
                    withdraw()
                return result

            monkeypatch.setattr(SkillCandidateOverlayRegistry, "invoke_for_evaluation", invoke)
        else:
            original = SkillPackageEvaluator.evaluate

            def evaluate(*args, **kwargs):
                result = original(*args, **kwargs)
                calls.append("evaluate")
                if len(calls) == 2:
                    withdraw()
                return result

            monkeypatch.setattr(SkillPackageEvaluator, "evaluate", evaluate)
        with pytest.raises(IntegrityError, match="PATTERN_CANDIDATE_EVIDENCE_RETRACTED"):
            controller.evaluate(candidate, actor_id="scripted:replay-controller")
        assert len(calls) == {"before": 0, "first-invocation": 1, "after-prior-evaluation": 2}[timing]
        assert controller._family("evaluation") == ()


def test_withdrawal_during_admission_replay_stops_before_new_source(tmp_path, monkeypatch):
    from orgrebase.workspace.pattern_evolution import SkillCandidateOverlayRegistry

    cases = tuple(
        external_case(f"case:{i}", outcome)
        for i, outcome in enumerate(("SUPPORT", "SUPPORT", "COUNTEREXAMPLE"))
    )
    external = {case.digest: (sha256_digest(["release-outcome", case.case_id]),) for case in cases}
    with StateStore(tmp_path / "admission-replay-retracted.sqlite3") as store:
        controller = service(store, lambda case: external[case.digest])
        proposal, boundary, _, _ = prepare(controller, cases=cases)
        (candidate,) = controller.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        evaluation_ref = controller.evaluate(candidate, actor_id="scripted:replay-controller")
        original = SkillCandidateOverlayRegistry.invoke_for_evaluation
        calls = []

        def invoke(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append("invoke")
            if len(calls) == 1:
                controller.retract(
                    external[cases[0].digest][0],
                    actor_id="scripted:skill-governance",
                    reason="Withdraw evidence during release replay",
                )
            return result

        monkeypatch.setattr(SkillCandidateOverlayRegistry, "invoke_for_evaluation", invoke)
        with pytest.raises(IntegrityError, match="PATTERN_CANDIDATE_EVIDENCE_RETRACTED"):
            controller.decide(
                candidate,
                evaluation_ref,
                actor_id="scripted:skill-governance",
                verdict="ADMIT",
                expected_candidate_digest=controller._load(candidate)["digest"],
            )
        assert len(calls) == 1
        assert controller._family("admission") == ()
        assert controller._family("decision") == ()
