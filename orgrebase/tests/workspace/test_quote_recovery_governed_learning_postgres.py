from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from orgrebase.digest import canonical_json
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import FeatureProfile, ProposalBudget
from orgrebase.workspace.pattern_governance import (
    PatternAdoptionPolicy,
    PrincipalPatternGovernance,
)
from orgrebase.workspace.quote_recovery_learning import (
    TARGET_SKILL,
    quote_recovery_content_bundle,
)
from orgrebase.workspace.skill_packages import InvocationContext, SkillReleaseLedger
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_quote_recovery_adoption_matrix import (
    _context,
    _invoke,
    _verified_release,
)
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    CORPUS,
    EVALUATOR,
    GOVERNOR,
    RUNTIME,
    TENANT,
    _boundary,
    _identity,
    _observed,
    _public,
    _replay,
    _scope,
    _service,
)


def test_quote_recovery_content_has_one_postgres_head_and_recovers_in_a_new_connection(
    postgres_runtime,
):
    config = postgres_runtime(tenant_id=TENANT)
    with (
        StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as first_store,
        StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as second_store,
    ):
        first, second = _service(first_store), _service(second_store)
        bundle = quote_recovery_content_bundle(first.registry)
        controllers = (
            PrincipalPatternGovernance(first, _scope(first)),
            PrincipalPatternGovernance(second, _scope(second)),
        )
        checks: list[str] = []
        with _identity(CORPUS, frozenset({"governor"}), checks):
            corpus = controllers[0].freeze_corpus(
                FeatureProfile(
                    profile_id="quote-evidence-recovery",
                    revision="1",
                    paths=("/candidate_bundle/domain",),
                    transfer_scope="one quote evidence-recovery handoff profile",
                ),
                tuple(
                    _observed(f"pg-case:{index}", outcome)
                    for index, outcome in enumerate(
                        ("SUPPORT", "SUPPORT", "COUNTEREXAMPLE")
                    )
                ),
            )
        replay = first.freeze_replay(_replay(), actor_id=EVALUATOR)
        with _identity(AUTHOR, frozenset({"operator"}), checks):
            proposal = controllers[0].open_proposal(
                proposal_id="proposal:postgres-verified",
                corpus_ref=corpus,
                replay_ref=replay,
                skill_name=TARGET_SKILL,
                budget=ProposalBudget(
                    max_cases=10, max_skill_invocations=100, max_seconds=300
                ),
            )
            (candidate,) = controllers[0].propose(
                proposal,
                boundary=_boundary(first),
                content_bundle=bundle.payload,
            )
        with _identity(EVALUATOR, frozenset({"governor"}), checks):
            evaluation = controllers[0].evaluate(candidate)
        base = first.registry.load(TARGET_SKILL).package_digest
        candidate_digest = first._load(candidate)["digest"]
        ready = Barrier(2)

        def admit(controller):
            ready.wait(timeout=5)
            try:
                with _identity(GOVERNOR, frozenset({"governor"}), checks):
                    return controller.decide(
                        candidate,
                        evaluation,
                        verdict="ADMIT",
                        expected_candidate_digest=candidate_digest,
                        expected_head_package_digest=base,
                        idempotency_key="postgres-quote-recovery-001",
                    )
            except (AuthorizationError, IntegrityError) as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, controllers))
        decision_refs = {
            value for value in results if value.startswith("pattern-evolution:decision:")
        }
        assert len(decision_refs) == 1
        assert all(
            value.startswith("pattern-evolution:decision:")
            or value
            in {
                "PATTERN_GOVERNANCE_ALREADY_FINAL",
                "PATTERN_PROPOSAL_TERMINAL",
                "PATTERN_GOVERNANCE_HEAD_CHANGED",
            }
            for value in results
        )
        assert len(first._family("decision")) == len(first._family("admission")) == 1

    with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as reopened:
        resumed = _service(reopened)
        controller = PrincipalPatternGovernance(
            resumed,
            _scope(resumed),
            adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
        )
        value = _public()
        value["run_id"] = "run:postgres-reopened"
        with _identity(RUNTIME, frozenset({"reader"}), []):
            invocation = controller.invoke(
                candidate,
                value,
                context=InvocationContext(
                    value["run_id"], value["task_id"], value["delegation_id"], RUNTIME
                ),
                knowledge_refs=("knowledge:quote-recovery-v1",),
                qualification_refs=("qualification:quote-recovery-review",),
            )
        assert invocation.result["action"] == "HANDOFF"
        assert invocation.receipt["candidate_content"]["bundle_digest"] == bundle.digest
        assert len(resumed._family("invocation-result")) == 1
        assert reopened.verify_event_chain()["status"] == "PASS"


def test_postgres_late_result_is_rejected_after_source_withdrawal_and_new_run_restores(
    postgres_runtime,
):
    config = postgres_runtime(tenant_id=TENANT)
    executed, release = Event(), Event()

    def pause(_candidate_ref: str) -> None:
        executed.set()
        assert release.wait(timeout=5)

    with (
        StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as first_store,
        StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as second_store,
    ):
        first, first_controller, candidate, predecessor, checks, _ = _verified_release(
            first_store,
            before_commit=pause,
        )
        second = _service(second_store)
        second_controller = PrincipalPatternGovernance(
            second,
            _scope(second),
            adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
        )
        value = _public()
        context = _context(value, "run:pg-late-result")

        def invoke_late():
            try:
                with _identity(RUNTIME, frozenset({"reader"}), checks):
                    _invoke(first_controller, candidate, value, context)
            except (AuthorizationError, IntegrityError) as error:
                return str(error)
            return "UNEXPECTED_SUCCESS"

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke_late)
            assert executed.wait(timeout=5)
            with _identity(GOVERNOR, frozenset({"governor"}), checks):
                _, restoration = second_controller.withdraw_and_restore_predecessor(
                    candidate,
                    expected_predecessor_digest=predecessor,
                    reason="CANARY_QUALITY_DEGRADED",
                )
            release.set()
            assert future.result(timeout=5) == "PATTERN_INVOCATION_CAPTURE_INVALIDATED"
        assert first._family("invocation-result") == ()
        assert len(first._family("invocation-rejection")) == 1

        restored = _public()
        restored_context = _context(restored, "run:pg-restored-new")
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            invocation = second_controller.invoke_restored_predecessor(
                restoration,
                restored,
                context=restored_context,
            )
        assert invocation.result["action"] == "HANDOFF"
        assert len(second._family("predecessor-invocation-result")) == 1

        controllers = (
            PrincipalPatternGovernance(first, _scope(first)),
            second_controller,
        )
        ready = Barrier(3)

        def restore_same_run(arguments):
            actor, active = arguments
            value = _public()
            value["run_id"] = "run:pg-one-restoration"
            context = InvocationContext(
                value["run_id"], value["task_id"], value["delegation_id"], actor
            )
            ready.wait(timeout=5)
            try:
                with _identity(actor, frozenset({"reader"}), checks):
                    return active.invoke_restored_predecessor(
                        restoration,
                        value,
                        context=context,
                    ).receipt["digest"]
            except IntegrityError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(restore_same_run, arguments)
                for arguments in zip(
                    ("runtime:pg-a", "runtime:pg-b"), controllers, strict=True
                )
            ]
            ready.wait(timeout=5)
            results = [future.result(timeout=5) for future in futures]
        assert sum(value.startswith("sha256:") for value in results) == 1
        assert results.count("PATTERN_RESTORATION_REQUIRES_NEW_RUN") == 1

    with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as restarted:
        resumed = _service(restarted)
        controller = PrincipalPatternGovernance(resumed, _scope(resumed))
        value = _public()
        value["run_id"] = "run:pg-one-restoration"
        context = InvocationContext(
            value["run_id"], value["task_id"], value["delegation_id"], "runtime:pg-c"
        )
        with (
            _identity("runtime:pg-c", frozenset({"reader"}), []),
            pytest.raises(IntegrityError, match="RESTORATION_REQUIRES_NEW_RUN"),
        ):
            controller.invoke_restored_predecessor(
                restoration,
                value,
                context=context,
            )


def test_postgres_consumer_unknown_survives_restart_and_same_run_is_not_redispatched(
    postgres_runtime, monkeypatch
):
    config = postgres_runtime(tenant_id=TENANT)
    with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
        service, controller, candidate, _, checks, _ = _verified_release(store)

        sentinels = (
            "Bear" + "er",
            "pg-" + "SUPER_SECRET",
            "customer-" + "secret-value",
        )

        def result_unknown(*args, **kwargs):
            raise RuntimeError(" ".join(sentinels))

        monkeypatch.setattr(SkillReleaseLedger, "invoke", result_unknown)
        value = _public()
        context = _context(value, "run:pg-result-unknown")
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="PATTERN_INVOCATION_RESULT_UNKNOWN"),
        ):
            _invoke(controller, candidate, value, context)
        assert service._family("invocation-terminal")[0]["state"] == "RESULT_UNKNOWN"
        assert service._family("invocation-result") == ()
        persisted = canonical_json(
            [
                row[0]
                for row in store.connection.execute(
                    "SELECT payload_json FROM artifacts ORDER BY artifact_id"
                )
            ]
        )
        for sentinel in sentinels:
            assert sentinel not in persisted

    monkeypatch.undo()
    with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as reopened:
        service = _service(reopened)
        controller = PrincipalPatternGovernance(
            service,
            _scope(service),
            adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
        )
        value = _public()
        context = _context(value, "run:pg-result-unknown")
        with (
            _identity(RUNTIME, frozenset({"reader"}), []),
            pytest.raises(IntegrityError, match="INVOCATION_ALREADY_RESERVED"),
        ):
            _invoke(controller, candidate, value, context)
        assert service._family("invocation-result") == ()
