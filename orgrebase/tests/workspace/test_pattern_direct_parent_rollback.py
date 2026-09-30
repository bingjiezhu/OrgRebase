"""Exact version-parent restoration and ABA fencing for Pattern heads."""

from __future__ import annotations

from multiprocessing import get_context

import pytest

from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    GovernedPatternService,
    ProposalBudget,
    SkillBoundary,
)
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_pattern_evolution import decide, qualify

SKILL = "structured-domain-handoff"


def _service(store: StateStore) -> GovernedPatternService:
    return GovernedPatternService(
        store,
        corpus_authority="scripted:corpus-controller",
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
    )


def _successor(service, *, proposal_id, corpus_ref, replay_ref, parent_candidate):
    prior = service._load(parent_candidate, "skill-candidate")
    current_digest = service.current_skill_head_package_digest(SKILL)
    proposal = service.open_proposal(
        proposal_id=proposal_id, corpus_ref=corpus_ref,
        replay_ref=replay_ref, skill_name=SKILL,
        author_id="learner:bounded",
        budget=ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=100),
    )
    boundary = {
        key: value for key, value in prior["boundary"].items() if key != "digest"
    }
    boundary["rollback_package_digest"] = current_digest
    (candidate,) = service.propose(
        proposal, actor_id="learner:bounded", boundary=SkillBoundary(**boundary)
    )
    evaluation = service.evaluate(candidate, actor_id=service.evaluator_authority)
    return candidate, evaluation


def _rollback(service, head):
    return service.restore_direct_parent(
        SKILL, actor_id=service.governance_authority,
        reason="controlled regression", expected_head_ref=head.ref,
        expected_head_digest=head.digest,
        expected_generation=head.payload["generation"],
        expected_package_digest=head.payload["package_digest"],
        qualification_check=lambda: None,
    )


def _rollback_process(dsn, tenant_id, expected, gate, output):
    gate.wait(timeout=20)
    try:
        with StateStore(dsn, tenant_id=tenant_id, migrate=False) as store:
            service = _service(store)
            result = service.restore_direct_parent(
                SKILL, actor_id=service.governance_authority,
                reason="cross-process regression",
                expected_head_ref=expected[0], expected_head_digest=expected[1],
                expected_generation=expected[2], expected_package_digest=expected[3],
                qualification_check=lambda: None,
            )
            output.put(("ROLLED_BACK", result))
    except IntegrityError as exc:
        output.put(("HOLD", str(exc)))


def _expected(head):
    return (
        head.ref, head.digest, head.payload["generation"], head.payload["package_digest"]
    )


def test_two_direct_parent_rollbacks_keep_version_lineage_and_reject_aba(tmp_path):
    with StateStore(tmp_path / "multirollback.sqlite3") as store:
        service = _service(store)
        installed_digest = service.registry.load(SKILL).package_digest
        first, first_evaluation, _, corpus, suite = qualify(service)
        decide(service, first, first_evaluation)
        first_head = service._current_stable_head(SKILL)
        assert first_head is not None and first_head.payload["generation"] == 1
        first_digest = first_head.payload["package_digest"]
        second, second_evaluation = _successor(
            service, proposal_id="proposal:second", corpus_ref=corpus,
            replay_ref=suite, parent_candidate=first,
        )
        # Keep a peer decision from g1. Its package digest equals the one
        # restored below, but its captured head revision must remain stale.
        stale, stale_evaluation = _successor(
            service, proposal_id="proposal:stale", corpus_ref=corpus,
            replay_ref=suite, parent_candidate=first,
        )
        decide(service, second, second_evaluation)
        second_head = service._current_stable_head(SKILL)
        assert second_head is not None and second_head.payload["generation"] == 2

        with pytest.raises(IntegrityError, match="CURRENT_NOT_WITHDRAWN"):
            _rollback(service, second_head)
        service.retract(second, actor_id=service.governance_authority, reason="bad successor")
        _rollback(service, second_head)
        first_rollback = service._current_stable_head(SKILL)
        assert first_rollback is not None
        assert first_rollback.payload["generation"] == 3
        assert first_rollback.payload["transition_kind"] == "ROLLBACK"
        assert first_rollback.payload["package_digest"] == first_digest
        assert first_rollback.payload["effective_version_ref"] == first_head.payload["effective_version_ref"]
        assert first_rollback.payload["effective_version_parent_ref"] == first_head.payload["effective_version_parent_ref"]
        assert first_rollback.payload["adoption_enabled"] is False
        with pytest.raises(IntegrityError, match="ROLLBACK_REQUALIFICATION_REQUIRED"):
            decide(service, stale, stale_evaluation)
        with pytest.raises(IntegrityError, match="STALE_HEAD"):
            _rollback(service, second_head)

        service.retract(first, actor_id=service.governance_authority, reason="older version bad too")
        _rollback(service, first_rollback)
        installed_rollback = service._current_stable_head(SKILL)
        assert installed_rollback is not None
        assert installed_rollback.payload["generation"] == 4
        assert installed_rollback.payload["package_digest"] == installed_digest
        assert installed_rollback.payload["effective_version_parent_ref"] is None
        assert installed_rollback.payload["adoption_enabled"] is False
        assert store.get_object(first_head.id, first_head.version).state is ObjectState.SUPERSEDED
        assert store.get_object(second_head.id, second_head.version).state is ObjectState.SUPERSEDED
        with pytest.raises(IntegrityError, match="NO_DIRECT_PARENT"):
            _rollback(service, installed_rollback)
        assert store.verify_event_chain()["status"] == "PASS"


def test_retracted_direct_parent_holds_without_partial_head(tmp_path):
    with StateStore(tmp_path / "invalid-parent.sqlite3") as store:
        service = _service(store)
        first, first_evaluation, _, corpus, suite = qualify(service)
        decide(service, first, first_evaluation)
        second, second_evaluation = _successor(
            service, proposal_id="proposal:second-invalid-parent",
            corpus_ref=corpus, replay_ref=suite, parent_candidate=first,
        )
        decide(service, second, second_evaluation)
        head = service._current_stable_head(SKILL)
        assert head is not None
        service.retract(second, actor_id=service.governance_authority, reason="bad successor")
        service.retract(first, actor_id=service.governance_authority, reason="bad parent")
        with pytest.raises(IntegrityError, match="ROLLBACK_VERSION_INVALID"):
            _rollback(service, head)
        assert service._current_stable_head(SKILL).ref == head.ref
        assert store.verify_event_chain()["status"] == "PASS"


def test_postgres_cross_process_rollback_competition_and_restart(postgres_runtime):
    tenant = "org:pattern-multigeneration-rollback"
    config = postgres_runtime(tenant_id=tenant)
    with StateStore(config["runtime_dsn"], tenant_id=tenant, migrate=False) as store:
        service = _service(store)
        first, first_evaluation, _, corpus, suite = qualify(service)
        decide(service, first, first_evaluation)
        first_head = service._current_stable_head(SKILL)
        second, second_evaluation = _successor(
            service, proposal_id="proposal:second-pg", corpus_ref=corpus,
            replay_ref=suite, parent_candidate=first,
        )
        decide(service, second, second_evaluation)
        second_head = service._current_stable_head(SKILL)
        assert first_head is not None and second_head is not None
        service.retract(second, actor_id=service.governance_authority, reason="bad successor")

    context = get_context("spawn")
    gate, output = context.Event(), context.Queue()
    workers = [
        context.Process(
            target=_rollback_process,
            args=(config["runtime_dsn"], tenant, _expected(second_head), gate, output),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    gate.set()
    results = [output.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    assert sorted(status for status, _ in results) == ["HOLD", "ROLLED_BACK"]
    assert "STALE_HEAD" in next(value for status, value in results if status == "HOLD")

    with StateStore(config["runtime_dsn"], tenant_id=tenant, migrate=False) as reopened:
        service = _service(reopened)
        restored = service._current_stable_head(SKILL)
        assert restored is not None and restored.payload["generation"] == 3
        assert restored.payload["package_digest"] == first_head.payload["package_digest"]
        assert restored.payload["effective_version_parent_ref"] == first_head.payload["effective_version_parent_ref"]
        service.retract(first, actor_id=service.governance_authority, reason="older version bad")
        _rollback(service, restored)
        final = service._current_stable_head(SKILL)
        assert final is not None and final.payload["generation"] == 4
        assert final.payload["package_digest"] == service.registry.load(SKILL).package_digest
        assert reopened.verify_event_chain()["status"] == "PASS"
