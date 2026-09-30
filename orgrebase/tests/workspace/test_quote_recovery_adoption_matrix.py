from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event

import pytest

from orgrebase.auth import (
    AuthenticationError,
    Principal,
    request_authorization,
    request_principal,
)
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    FeatureProfile,
    GovernedPatternService,
    ProposalBudget,
)
from orgrebase.workspace.pattern_governance import (
    PatternAdoptionPolicy,
    PrincipalPatternGovernance,
)
from orgrebase.workspace.quote_recovery_learning import (
    TARGET_SKILL,
    quote_recovery_content_bundle,
)
from orgrebase.workspace.skill_packages import (
    InvocationContext,
    SkillPackageRegistry,
    SkillReleaseLedger,
)
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    CORPUS,
    EVALUATOR,
    GOVERNOR,
    RUNTIME,
    TENANT,
    WORKSPACE,
    _boundary,
    _identity,
    _observed,
    _public,
    _replay,
    _scope,
)


def _verified_release(
    store: StateStore,
    *,
    dependency_state: dict[str, str] | None = None,
    before_commit=None,
):
    registry = SkillPackageRegistry()
    if dependency_state is None:
        dependency_state = dict(registry.load(TARGET_SKILL).manifest["dependencies"])
    service = GovernedPatternService(
        store,
        corpus_authority=CORPUS,
        evaluator_authority=EVALUATOR,
        governance_authority=GOVERNOR,
        registry=registry,
        prerequisite_resolver=lambda actor, ref: actor == RUNTIME,
        skill_dependency_resolver=lambda name: dict(dependency_state),
        before_invocation_result_commit=before_commit,
    )
    checks: list[str] = []
    controller = PrincipalPatternGovernance(service, _scope(service))
    with _identity(CORPUS, frozenset({"governor"}), checks):
        corpus = controller.freeze_corpus(
            FeatureProfile(
                profile_id="quote-evidence-recovery",
                revision="1",
                paths=("/candidate_bundle/domain",),
                transfer_scope="controlled adoption invalidation matrix",
            ),
            tuple(
                _observed(f"matrix-case:{index}", outcome)
                for index, outcome in enumerate(
                    ("SUPPORT", "SUPPORT", "COUNTEREXAMPLE")
                )
            ),
        )
    replay = service.freeze_replay(_replay(), actor_id=EVALUATOR)
    with _identity(AUTHOR, frozenset({"operator"}), checks):
        proposal = controller.open_proposal(
            proposal_id="proposal:matrix",
            corpus_ref=corpus,
            replay_ref=replay,
            skill_name=TARGET_SKILL,
            budget=ProposalBudget(
                max_cases=10,
                max_skill_invocations=100,
                max_seconds=300,
            ),
        )
        (candidate,) = controller.propose(
            proposal,
            boundary=_boundary(service),
            content_bundle=quote_recovery_content_bundle(registry).payload,
        )
    with _identity(EVALUATOR, frozenset({"governor"}), checks):
        evaluation = controller.evaluate(candidate)
    predecessor = registry.load(TARGET_SKILL).package_digest
    with _identity(GOVERNOR, frozenset({"governor"}), checks):
        controller.decide(
            candidate,
            evaluation,
            verdict="ADMIT",
            expected_candidate_digest=service._load(candidate)["digest"],
            expected_head_package_digest=predecessor,
            idempotency_key="matrix-decision",
        )
    enabled = PrincipalPatternGovernance(
        service,
        _scope(service),
        adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
    )
    return service, enabled, candidate, predecessor, checks, dependency_state


def _context(value, run_id: str) -> InvocationContext:
    value["run_id"] = run_id
    return InvocationContext(run_id, value["task_id"], value["delegation_id"], RUNTIME)


def _invoke(controller, candidate, value, context):
    return controller.invoke(
        candidate,
        value,
        context=context,
        knowledge_refs=("knowledge:quote-recovery-v1",),
        qualification_refs=("qualification:quote-recovery-review",),
    )


def test_dependency_drift_before_dispatch_and_after_consumer_never_commits_success(tmp_path):
    dependency_state: dict[str, str] = {}
    mode = {"late": False}

    def drift_after_consumer(_candidate_ref: str) -> None:
        if mode["late"]:
            dependency_state["runtime:unexpected@1"] = sha256_digest("late-drift")

    with StateStore(
        tmp_path / "dependency-drift.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        registry = SkillPackageRegistry()
        dependency_state.update(registry.load(TARGET_SKILL).manifest["dependencies"])
        service, controller, candidate, _, checks, _ = _verified_release(
            store,
            dependency_state=dependency_state,
            before_commit=drift_after_consumer,
        )
        expected_dependencies = dict(dependency_state)
        reads = 0

        def drift_during_reservation(_name: str):
            nonlocal reads
            reads += 1
            if reads == 1:
                return dict(expected_dependencies)
            return {
                **expected_dependencies,
                "runtime:unexpected@1": sha256_digest("capture-to-reserve-drift"),
            }

        service.skill_dependency_resolver = drift_during_reservation
        value = _public()
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="CAPTURE_INVALIDATED"),
        ):
            _invoke(controller, candidate, value, _context(value, "run:pre-drift"))
        assert reads >= 2
        assert service._family("invocation-reservation") == ()
        assert service._family("invocation-result") == ()

        dependency_state.clear()
        dependency_state.update(registry.load(TARGET_SKILL).manifest["dependencies"])
        service.skill_dependency_resolver = lambda _name: dict(dependency_state)
        mode["late"] = True
        value = _public()
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="CAPTURE_INVALIDATED"),
        ):
            _invoke(controller, candidate, value, _context(value, "run:late-drift"))
        assert len(service._family("invocation-reservation")) == 1
        assert service._family("invocation-result") == ()
        rejection = service._family("invocation-rejection")[0]
        assert rejection["run_id"] == "run:late-drift"
        assert rejection["target_writes"] == 0


def test_source_withdrawal_while_consumer_runs_rejects_late_result_and_restores_new_run(
    tmp_path,
):
    executed, release = Event(), Event()

    def pause_after_consumer(_candidate_ref: str) -> None:
        executed.set()
        assert release.wait(timeout=5)

    with StateStore(
        tmp_path / "source-withdrawal.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, predecessor, checks, _ = _verified_release(
            store,
            before_commit=pause_after_consumer,
        )
        value = _public()
        context = _context(value, "run:late-source-result")

        def invoke_late():
            try:
                with _identity(RUNTIME, frozenset({"reader"}), checks):
                    _invoke(controller, candidate, value, context)
            except (AuthorizationError, IntegrityError) as error:
                return str(error)
            return "UNEXPECTED_SUCCESS"

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke_late)
            assert executed.wait(timeout=5)
            with _identity(GOVERNOR, frozenset({"governor"}), checks):
                _, restoration = controller.withdraw_and_restore_predecessor(
                    candidate,
                    expected_predecessor_digest=predecessor,
                    reason="CANARY_QUALITY_DEGRADED",
                )
            release.set()
            assert future.result(timeout=5) == "PATTERN_INVOCATION_CAPTURE_INVALIDATED"

        assert service._family("invocation-result") == ()
        assert len(service._family("invocation-rejection")) == 1
        assert service.evidence_status(candidate) == "REQUALIFICATION_REQUIRED"
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(AuthorizationError, match="PRODUCTION_RELEASE_CHAIN_INVALID"),
        ):
            stopped = _public()
            _invoke(
                controller,
                candidate,
                stopped,
                _context(stopped, "run:stopped-canary"),
            )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="RESTORATION_REQUIRES_NEW_RUN"),
        ):
            controller.invoke_restored_predecessor(
                restoration,
                value,
                context=context,
            )
        restored = _public()
        restored_context = _context(restored, "run:restored-after-canary")
        service.before_invocation_result_commit = None
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            invocation = controller.invoke_restored_predecessor(
                restoration,
                restored,
                context=restored_context,
            )
        assert invocation.result["action"] == "HANDOFF"
        assert len(service._family("predecessor-invocation-result")) == 1
        assert service._family("invocation-result") == ()


def test_principal_revocation_after_consumer_rejects_result_but_new_authorized_run_works(
    tmp_path,
):
    state = {"authorized": True, "revoke_on_hook": True}

    def revoke_after_consumer(_candidate_ref: str) -> None:
        if state["revoke_on_hook"]:
            state["authorized"] = False

    with StateStore(
        tmp_path / "principal-revocation.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, _, _, _ = _verified_release(
            store,
            before_commit=revoke_after_consumer,
        )
        principal = Principal(
            "https://issuer.example",
            f"subject:{RUNTIME}",
            TENANT,
            RUNTIME,
            frozenset({"reader"}),
            4_000_000_000,
        )

        def reauthorize() -> None:
            if not state["authorized"]:
                raise AuthenticationError("PATTERN_TEST_MEMBERSHIP_REVOKED", 403)

        principal_token = request_principal.set(principal)
        authorization_token = request_authorization.set(reauthorize)
        try:
            value = _public()
            with pytest.raises(AuthenticationError, match="MEMBERSHIP_REVOKED"):
                _invoke(controller, candidate, value, _context(value, "run:revoked-late"))
        finally:
            request_authorization.reset(authorization_token)
            request_principal.reset(principal_token)
        assert service._family("invocation-result") == ()
        assert len(service._family("invocation-rejection")) == 1

        state.update(authorized=True, revoke_on_hook=False)
        value = _public()
        with _identity(RUNTIME, frozenset({"reader"}), []):
            invocation = _invoke(
                controller,
                candidate,
                value,
                _context(value, "run:new-authorized-after-revocation"),
            )
        assert invocation.result["action"] == "HANDOFF"
        assert len(service._family("invocation-result")) == 1


def test_predecessor_or_head_drift_stops_production_adoption(tmp_path, monkeypatch):
    with StateStore(
        tmp_path / "head-drift.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, _, checks, _ = _verified_release(store)
        admission = service._family("admission")[0]
        source_id = admission["source_ref"].rsplit("@", 1)[0]
        with store.transaction() as connection:
            store.transition_current(connection, source_id, ObjectState.SUPERSEDED)
        value = _public()
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(AuthorizationError, match="PRODUCTION_RELEASE_CHAIN_INVALID"),
        ):
            _invoke(controller, candidate, value, _context(value, "run:head-drift"))
        assert service._family("invocation-reservation") == ()

    with StateStore(
        tmp_path / "predecessor-drift.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, _, checks, _ = _verified_release(store)
        original_load = service.registry.load

        def drifted_load(name, **kwargs):
            package = original_load(name, **kwargs)
            if name == TARGET_SKILL:
                return replace(
                    package,
                    package_digest=sha256_digest("unexpected-predecessor"),
                )
            return package

        monkeypatch.setattr(service.registry, "load", drifted_load)
        value = _public()
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises((AuthorizationError, IntegrityError)),
        ):
            _invoke(controller, candidate, value, _context(value, "run:predecessor-drift"))
        assert service._family("invocation-reservation") == ()


def test_consumer_exception_persists_unknown_and_same_run_cannot_redispatch(
    tmp_path, monkeypatch
):
    with StateStore(
        tmp_path / "consumer-unknown.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, _, checks, _ = _verified_release(store)
        sentinels = (
            "Bear" + "er",
            "sk-" + "live-" + "SUPER_SECRET",
            "customer-" + "secret-value",
        )

        def result_unknown(*args, **kwargs):
            raise RuntimeError(" ".join(sentinels))

        monkeypatch.setattr(SkillReleaseLedger, "invoke", result_unknown)
        value = _public()
        context = _context(value, "run:consumer-result-unknown")
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="PATTERN_INVOCATION_RESULT_UNKNOWN"),
        ):
            _invoke(controller, candidate, value, context)
        terminal = service._family("invocation-terminal")
        assert len(terminal) == 1
        assert terminal[0]["state"] == "RESULT_UNKNOWN"
        assert terminal[0]["reason_code"] == "PATTERN_INVOCATION_RESULT_UNKNOWN"
        assert terminal[0]["run_id"] == context.run_id
        assert len(service._family("invocation-reservation")) == 1
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

        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="INVOCATION_ALREADY_RESERVED"),
        ):
            _invoke(controller, candidate, value, context)
        assert len(service._family("invocation-reservation")) == 1
        assert service._family("invocation-result") == ()


def test_two_actors_cannot_restore_the_same_run_twice(tmp_path):
    with StateStore(
        tmp_path / "restored-run-race.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, predecessor, checks, _ = _verified_release(store)
        with _identity(GOVERNOR, frozenset({"governor"}), checks):
            _, restoration = controller.withdraw_and_restore_predecessor(
                candidate,
                expected_predecessor_digest=predecessor,
                reason="CANARY_QUALITY_DEGRADED",
            )
        ready = Barrier(3)

        def invoke_as(actor: str):
            value = _public()
            value["run_id"] = "run:one-restoration"
            context = InvocationContext(
                value["run_id"], value["task_id"], value["delegation_id"], actor
            )
            ready.wait(timeout=5)
            try:
                with _identity(actor, frozenset({"reader"}), checks):
                    return controller.invoke_restored_predecessor(
                        restoration,
                        value,
                        context=context,
                    ).receipt["digest"]
            except IntegrityError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke_as, actor) for actor in ("runtime:a", "runtime:b")]
            ready.wait(timeout=5)
            results = [future.result(timeout=5) for future in futures]
        assert sum(value.startswith("sha256:") for value in results) == 1
        assert results.count("PATTERN_RESTORATION_REQUIRES_NEW_RUN") == 1
        assert len(service._family("predecessor-invocation-reservation")) == 1
        assert len(service._family("predecessor-invocation-result")) == 1


def test_restored_consumer_exception_persists_unknown_without_private_message(
    tmp_path, monkeypatch
):
    with StateStore(
        tmp_path / "restored-consumer-unknown.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, controller, candidate, predecessor, checks, _ = _verified_release(store)
        with _identity(GOVERNOR, frozenset({"governor"}), checks):
            _, restoration = controller.withdraw_and_restore_predecessor(
                candidate,
                expected_predecessor_digest=predecessor,
                reason="CANARY_QUALITY_DEGRADED",
            )

        def result_unknown(*args, **kwargs):
            raise RuntimeError("Bearer " + "restored-SUPER_SECRET private-value")

        monkeypatch.setattr(service.registry, "interpret_candidate", result_unknown)
        value = _public()
        value["run_id"] = "run:restored-result-unknown"
        context = InvocationContext(
            value["run_id"], value["task_id"], value["delegation_id"], RUNTIME
        )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="PATTERN_INVOCATION_RESULT_UNKNOWN"),
        ):
            controller.invoke_restored_predecessor(
                restoration,
                value,
                context=context,
            )
        terminal = service._family("predecessor-invocation-terminal")
        assert len(terminal) == 1
        assert terminal[0]["state"] == "RESULT_UNKNOWN"
        assert terminal[0]["reason_code"] == "PATTERN_INVOCATION_RESULT_UNKNOWN"
        assert service._family("predecessor-invocation-result") == ()
        persisted = canonical_json(
            [
                row[0]
                for row in store.connection.execute(
                    "SELECT payload_json FROM artifacts ORDER BY artifact_id"
                )
            ]
        )
        assert "restored-SUPER_SECRET" not in persisted
        assert "private-value" not in persisted

        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(IntegrityError, match="RESTORATION_REQUIRES_NEW_RUN"),
        ):
            controller.invoke_restored_predecessor(
                restoration,
                value,
                context=context,
            )
