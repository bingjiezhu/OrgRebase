"""Cross-connection Pattern head contention in the existing PostgreSQL store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import GovernedPatternService, ProposalBudget
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_pattern_evolution import prepare


def _service(store):
    return GovernedPatternService(
        store,
        corpus_authority="scripted:corpus-controller",
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
    )


def test_postgres_two_distinct_candidates_compete_for_one_exact_head(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern-concurrency")
    with (
        StateStore(config["runtime_dsn"], tenant_id="org:pattern-concurrency", migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id="org:pattern-concurrency", migrate=False) as second,
    ):
        a, b = _service(first), _service(second)
        proposal_a, boundary, corpus, suite = prepare(a)
        proposal_b = b.open_proposal(
            proposal_id="proposal:competing",
            corpus_ref=corpus,
            replay_ref=suite,
            skill_name="structured-domain-handoff",
            author_id="learner:bounded",
            budget=ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=100),
        )
        (candidate_a,) = a.propose(proposal_a, actor_id="learner:bounded", boundary=boundary)
        (candidate_b,) = b.propose(proposal_b, actor_id="learner:bounded", boundary=boundary)
        evaluation_a = a.evaluate(candidate_a, actor_id=a.evaluator_authority)
        evaluation_b = b.evaluate(candidate_b, actor_id=b.evaluator_authority)
        expected = a.registry.load("structured-domain-handoff").package_digest

        def admit(service, candidate_ref, evaluation_ref):
            try:
                return service.decide(
                    candidate_ref,
                    evaluation_ref,
                    actor_id=service.governance_authority,
                    verdict="ADMIT",
                    expected_candidate_digest=service._load(candidate_ref)["digest"],
                    expected_head_package_digest=expected,
                )
            except IntegrityError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(admit, a, candidate_a, evaluation_a)
            right = pool.submit(admit, b, candidate_b, evaluation_b)
            results = (left.result(), right.result())
        assert sum(result.startswith("pattern-evolution:decision:") for result in results) == 1
        assert any("PATTERN_GOVERNANCE_HEAD_CHANGED" in result for result in results)
        assert len(a._family("admission")) == 1
        head = a._current_stable_head("structured-domain-handoff")
        assert head is not None and head.state is ObjectState.CURRENT
        assert head.payload["generation"] == 1
        assert first.verify_event_chain()["status"] == "PASS"
