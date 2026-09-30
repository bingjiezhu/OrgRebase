"""S05 v1 admission upgrade into the single Pattern v2 head.

These tests exercise the current binary's transaction fence. Operational
quiescence of binaries that predate the fence is a deployment prerequisite.
"""

from __future__ import annotations

from copy import deepcopy
from multiprocessing import get_context

import pytest

from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    PACKAGE_SNAPSHOT_MEDIA,
    GovernedPatternService,
    ProposalBudget,
    SkillBoundary,
    _identity,
    _record,
)
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_pattern_evolution import qualify

SKILL = "structured-domain-handoff"


def _service(store: StateStore) -> GovernedPatternService:
    return GovernedPatternService(
        store,
        corpus_authority="scripted:corpus-controller",
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
    )


def _legacy_record(record: dict, *, discard: tuple[str, ...] = (), **changes):
    body = {
        key: deepcopy(value)
        for key, value in record.items()
        if key not in {"schema_version", "kind", "digest", *discard}
    }
    body.update(changes)
    return _record(record["kind"], **body)


def _seed_one_legal_v1_head(
    service: GovernedPatternService, *, corrupt_release_history: bool = False,
    source_valid_to: str | None = None,
):
    # Generate a real qualified candidate/evaluation, then persist exactly the
    # v1 artifact and Source shapes that predate stable-head fields.
    candidate_ref, evaluation_ref, _, corpus_ref, suite_ref = qualify(service)
    candidate = _legacy_record(
        service._load(candidate_ref),
        discard=(
            "base_head_ref", "base_head_digest", "base_head_generation",
            "principal_binding", "content_bundle", "diagnostic_reason_map",
            "diagnostic_policy_digest",
        ),
    )
    legacy_candidate_ref = _identity(candidate)
    with service._transaction() as connection:
        service._save(connection, candidate)
    legacy_evaluation_ref = service.evaluate(
        legacy_candidate_ref, actor_id=service.evaluator_authority
    )
    evaluation = service._load(legacy_evaluation_ref, "evaluation")
    assert evaluation["verdict"] == "QUALIFIED"
    release_ledger, _ = service._replay_release(candidate, evaluation)
    decision = _record(
        "decision",
        candidate_ref=legacy_candidate_ref,
        evaluation_ref=legacy_evaluation_ref,
        verdict="ADMIT",
        actor_id=service.governance_authority,
        human_review_verified=False,
        authority_basis="CONFIGURED_CONTROLLER_PRINCIPAL",
        decided_at=service.clock(),
    )
    decision_ref = _identity(decision)
    package = service._overlay(candidate).load(SKILL)
    source = VersionedObject(
        id=f"admitted-pattern-skill:{candidate['digest'].removeprefix('sha256:')}",
        version="1", kind="Source", label=SKILL,
        domain="skill-governance", state=ObjectState.CURRENT,
        valid_to=source_valid_to,
        payload={
            "candidate_ref": legacy_candidate_ref,
            "decision_ref": decision_ref,
            "evaluation_ref": legacy_evaluation_ref,
            "release_history": (
                [] if corrupt_release_history else list(release_ledger.history)
            ),
            "package_digest": package.package_digest,
            "claim_boundary": "LOCAL_GOVERNED_CANDIDATE_NOT_ENTERPRISE_EFFECTIVENESS",
        },
        source_refs=(legacy_candidate_ref, decision_ref, legacy_evaluation_ref),
    )
    with service._transaction() as connection:
        service._save(connection, decision)
        service.store.insert_version(connection, source, make_current=True)
        service._save(
            connection,
            _record(
                "admission", candidate_ref=legacy_candidate_ref,
                source_ref=source.ref, source_digest=source.digest,
                decision_ref=decision_ref,
            ),
        )
    return source, package, (corpus_ref, suite_ref), (candidate_ref, evaluation_ref)


def _migrate(service: GovernedPatternService, source: VersionedObject, package_digest: str):
    return service.migrate_legacy_head(
        SKILL,
        actor_id=service.governance_authority,
        expected_source_ref=source.ref,
        expected_source_digest=source.digest,
        expected_package_digest=package_digest,
        maintenance_fence=lambda: None,
    )


def _migration_process(dsn, tenant_id, source_ref, source_digest, package_digest, gate, output):
    gate.wait(timeout=20)
    try:
        with StateStore(dsn, tenant_id=tenant_id, migrate=False) as store:
            service = _service(store)
            result = service.migrate_legacy_head(
                SKILL, actor_id=service.governance_authority,
                expected_source_ref=source_ref,
                expected_source_digest=source_digest,
                expected_package_digest=package_digest,
                maintenance_fence=lambda: None,
            )
            output.put(("MIGRATED", result))
    except IntegrityError as exc:
        output.put(("HOLD", str(exc)))


def test_legacy_migration_preserves_exact_records_and_fences_stale_candidate(tmp_path):
    with StateStore(tmp_path / "legacy.sqlite3") as store:
        service = _service(store)
        source, package, (corpus, suite), stale = _seed_one_legal_v1_head(service)
        historical = store.get_object(source.id, source.version)
        candidate_bytes = service._load(source.payload["candidate_ref"])

        with pytest.raises(IntegrityError, match="LEGACY_HEAD_MIGRATION_REQUIRED"):
            service._skill_head_capture(SKILL)
        with pytest.raises(IntegrityError, match="MAINTENANCE_FENCE_REQUIRED"):
            service.migrate_legacy_head(
                SKILL, actor_id=service.governance_authority,
                expected_source_ref=source.ref,
                expected_source_digest=source.digest,
                expected_package_digest=package.package_digest,
                maintenance_fence=None,  # type: ignore[arg-type]
            )
        head_ref = _migrate(service, source, package.package_digest)
        head = service._current_stable_head(SKILL)
        assert head is not None and head.ref == head_ref
        assert head.payload["transition_kind"] == "MIGRATE"
        assert head.payload["generation"] == 1
        assert head.payload["previous_head_ref"] == source.ref
        assert head.payload["effective_version_ref"] == source.ref
        assert head.payload["adoption_enabled"] is False
        assert service.current_skill_head_package_digest(SKILL) == package.package_digest
        assert store.get_object(source.id, source.version) == historical
        assert service._load(source.payload["candidate_ref"]) == candidate_bytes
        snapshot = store.load_artifact(
            head.payload["package_snapshot_ref"], PACKAGE_SNAPSHOT_MEDIA
        ).payload
        assert snapshot["digest"]

        with pytest.raises(IntegrityError, match="HEAD_ALREADY_EXISTS"):
            _migrate(service, source, package.package_digest)
        with pytest.raises(IntegrityError, match="HEAD_CHANGED"):
            old_candidate, old_evaluation = stale
            service.decide(
                old_candidate, old_evaluation,
                actor_id=service.governance_authority,
                verdict="ADMIT",
                expected_candidate_digest=service._load(old_candidate)["digest"],
                expected_head_package_digest=service.registry.load(SKILL).package_digest,
            )
        successor_proposal = service.open_proposal(
            proposal_id="proposal:after-v1-migration", corpus_ref=corpus,
            replay_ref=suite, skill_name=SKILL, author_id="learner:bounded",
            budget=ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=100),
        )
        successor_input = service._load(successor_proposal)
        assert successor_input["base_head_ref"] == head.ref
        assert successor_input["base_head_generation"] == 1
        assert successor_input["base_package_digest"] == package.package_digest
        boundary = dict(candidate_bytes["boundary"])
        boundary.pop("digest")
        boundary["rollback_package_digest"] = package.package_digest
        (successor,) = service.propose(
            successor_proposal, actor_id="learner:bounded",
            boundary=SkillBoundary(**boundary),
        )
        successor_evaluation = service.evaluate(
            successor, actor_id=service.evaluator_authority
        )
        service.decide(
            successor, successor_evaluation,
            actor_id=service.governance_authority,
            verdict="ADMIT",
            expected_candidate_digest=service._load(successor)["digest"],
            expected_head_package_digest=package.package_digest,
        )
        successor_head = service._current_stable_head(SKILL)
        assert successor_head is not None
        assert successor_head.payload["generation"] == 2
        assert successor_head.payload["previous_head_ref"] == head.ref
        assert successor_head.payload["effective_version_parent_ref"] == source.ref
        assert store.get_object(source.id, source.version).state is ObjectState.SUPERSEDED
        assert store.verify_event_chain()["status"] == "PASS"


def test_withdrawn_legacy_head_cannot_be_reborn_as_installed_genesis(tmp_path):
    with StateStore(tmp_path / "withdrawn.sqlite3") as store:
        service = _service(store)
        source, package, _, _ = _seed_one_legal_v1_head(service)
        with store.transaction() as connection:
            store.transition_current(connection, source.id, ObjectState.REQUALIFICATION_REQUIRED)
        with pytest.raises(IntegrityError, match="LEGACY_HEAD_AMBIGUOUS_OR_RETRACTED"):
            service._skill_head_capture(SKILL)
        with pytest.raises(IntegrityError, match="LEGACY_HEAD_AMBIGUOUS_OR_RETRACTED"):
            service.current_skill_head_package_digest(SKILL)
        with pytest.raises(IntegrityError, match="LEGACY_HEAD_RETRACTED"):
            _migrate(service, source, package.package_digest)
        assert service._current_stable_head(SKILL) is None


def test_ambiguous_legacy_current_and_fence_loss_leave_no_new_head(tmp_path):
    with StateStore(tmp_path / "ambiguous.sqlite3") as store:
        service = _service(store)
        source, package, _, _ = _seed_one_legal_v1_head(service)
        calls = 0

        def fence_lost_at_commit():
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise IntegrityError("MAINTENANCE_WINDOW_CLOSED")

        with pytest.raises(IntegrityError, match="MAINTENANCE_WINDOW_CLOSED"):
            service.migrate_legacy_head(
                SKILL, actor_id=service.governance_authority,
                expected_source_ref=source.ref,
                expected_source_digest=source.digest,
                expected_package_digest=package.package_digest,
                maintenance_fence=fence_lost_at_commit,
            )
        assert service._current_stable_head(SKILL) is None

        second = source.model_copy(update={
            "id": source.id + ":duplicate", "digest": "",
        })
        second = VersionedObject.model_validate(second.model_dump(mode="json"))
        with service._transaction() as connection:
            store.insert_version(connection, second, make_current=True)
            service._save(connection, _record(
                "admission", candidate_ref=source.payload["candidate_ref"],
                source_ref=second.ref, source_digest=second.digest,
                decision_ref=source.payload["decision_ref"],
            ))
        with pytest.raises(IntegrityError, match="MULTIPLE_CURRENT_SKILL_HEADS"):
            _migrate(service, source, package.package_digest)
        assert service._current_stable_head(SKILL) is None


def test_legacy_release_history_must_verify_before_migration(tmp_path):
    with StateStore(tmp_path / "bad-release.sqlite3") as store:
        service = _service(store)
        source, package, _, _ = _seed_one_legal_v1_head(
            service, corrupt_release_history=True
        )
        with pytest.raises(IntegrityError, match="SKILL_RESTORED_EVALUATION_NOT_QUALIFIED"):
            _migrate(service, source, package.package_digest)
        assert service._current_stable_head(SKILL) is None


def test_expired_legacy_source_holds_migration(tmp_path):
    with StateStore(tmp_path / "expired-source.sqlite3") as store:
        service = _service(store)
        source, package, _, _ = _seed_one_legal_v1_head(
            service, source_valid_to="2026-08-15T00:00:00Z"
        )
        with pytest.raises(IntegrityError, match="SOURCE_TEMPORAL_QUALIFICATION_HOLD"):
            _migrate(service, source, package.package_digest)
        assert service._current_stable_head(SKILL) is None


def test_postgres_legacy_migration_restart_preserves_current_head(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern-migration")
    with StateStore(
        config["runtime_dsn"], tenant_id="org:pattern-migration", migrate=False
    ) as first:
        service = _service(first)
        source, package, _, _ = _seed_one_legal_v1_head(service)
        expected = _migrate(service, source, package.package_digest)
    with StateStore(
        config["runtime_dsn"], tenant_id="org:pattern-migration", migrate=False
    ) as reopened:
        service = _service(reopened)
        assert service._current_stable_head(SKILL).ref == expected
        assert service._package_for_digest(SKILL, package.package_digest).package_digest
        assert reopened.get_object(source.id, source.version).digest == source.digest
        assert reopened.verify_event_chain()["status"] == "PASS"


def test_postgres_two_processes_compete_for_one_legacy_migration(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern-migration-processes")
    with StateStore(
        config["runtime_dsn"], tenant_id="org:pattern-migration-processes", migrate=False
    ) as first:
        source, package, _, _ = _seed_one_legal_v1_head(_service(first))
    context = get_context("spawn")
    gate, output = context.Event(), context.Queue()
    workers = [
        context.Process(
            target=_migration_process,
            args=(
                config["runtime_dsn"], "org:pattern-migration-processes",
                source.ref, source.digest, package.package_digest, gate, output,
            ),
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
    assert sorted(status for status, _ in results) == ["HOLD", "MIGRATED"]
    assert "HEAD_ALREADY_EXISTS" in next(value for status, value in results if status == "HOLD")
    with StateStore(
        config["runtime_dsn"], tenant_id="org:pattern-migration-processes", migrate=False
    ) as reopened:
        head = _service(reopened)._current_stable_head(SKILL)
        assert head is not None and head.payload["generation"] == 1
        assert reopened.get_object(source.id, source.version).digest == source.digest
        assert reopened.verify_event_chain()["status"] == "PASS"
