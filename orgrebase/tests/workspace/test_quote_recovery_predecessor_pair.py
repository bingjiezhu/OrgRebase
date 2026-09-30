"""N+2 P0 evaluation consumes the exact N+1 parent on one frozen input."""

from __future__ import annotations

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    GovernedPatternService,
    ProposalBudget,
    SkillBoundary,
    _EffectiveSkillRegistry,
)
from orgrebase.workspace.quote_recovery_learning import TARGET_SKILL, quote_recovery_content_bundle
from orgrebase.workspace.quote_recovery_use import (
    INVOCATION_MEDIA,
    evaluate_quote_recovery_candidate,
)
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    EVALUATOR,
    GOVERNOR,
    TENANT,
    WORKSPACE,
    _identity,
    _prepare,
    _service,
)
from tests.workspace.test_quote_recovery_operations import _recovery_workspace
from tests.workspace.test_quote_recovery_use import _candidate, _command, _policy


def test_unpublished_nplus1_pairs_with_exact_installed_parent(tmp_path, monkeypatch):
    with StateStore(
        tmp_path / "nplus1-pair.sqlite3", tenant_id=TENANT, workspace_id=WORKSPACE
    ) as store:
        service, candidate = _candidate(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "pair-policy.json", candidate)
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            paired = evaluate_quote_recovery_candidate(
                workspace, _command(event.event_id, candidate), config_path=policy
            )
        installed = service.registry.load(TARGET_SKILL)
        assert paired["base_head_generation"] == 0
        assert paired["base_head_ref"] == f"installed:{TARGET_SKILL}@{installed.version}"
        assert paired["baseline_package_digest"] == installed.package_digest
        assert paired["baseline_action"] == paired["action"] == "HANDOFF"
        assert paired["pair_gate_status"] == "REJECTED_NO_BEHAVIOR_DELTA"
        assert paired["baseline_invocation_receipt_digest"]
        assert paired["candidate_invocation_receipt_digest"]
        assert paired["interpreter_invocations"] == 2
        assert paired["qualification_status"] == "NOT_QUALIFIED"
        assert not service._family("admission")


def test_unknown_baseline_attempt_is_not_redispatched_with_new_id(tmp_path, monkeypatch):
    with StateStore(
        tmp_path / "unknown-baseline.sqlite3", tenant_id=TENANT, workspace_id=WORKSPACE
    ) as store:
        _, candidate = _candidate(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "pair-policy.json", candidate)
        command = _command(event.event_id, candidate)

        def lost_baseline_result(*_args, **_kwargs):
            raise RuntimeError("simulated interpreter result loss")

        monkeypatch.setattr(
            _EffectiveSkillRegistry, "invoke_for_evaluation", lost_baseline_result
        )
        with _identity(EVALUATOR, frozenset({"governor"}), []):
            first = evaluate_quote_recovery_candidate(workspace, command, config_path=policy)
            assert first["status"] == "RESULT_UNKNOWN"
            assert first["interpreter_invocations"] == 1
            assert evaluate_quote_recovery_candidate(
                workspace, command, config_path=policy
            ) == first
            with pytest.raises(IntegrityError, match="PREDECESSOR_NOT_TERMINAL_OR_NEW"):
                evaluate_quote_recovery_candidate(
                    workspace,
                    _command(
                        event.event_id, candidate,
                        operation="p0-evaluation-bypass", attempt="attempt-bypass",
                        previous=first["selection_ref"],
                    ),
                    config_path=policy,
                )
        assert store.verify_event_chain()["status"] == "PASS"


def test_parent_drift_at_selection_commit_prevents_both_invocations(tmp_path, monkeypatch):
    with StateStore(
        tmp_path / "parent-drift.sqlite3", tenant_id=TENANT, workspace_id=WORKSPACE
    ) as store:
        _, candidate = _candidate(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "pair-policy.json", candidate)
        original = GovernedPatternService._require_candidate_head
        checks = 0

        def head_changes(self, record):
            nonlocal checks
            checks += 1
            if checks >= 2:
                raise IntegrityError("CONTROLLED_PARENT_DRIFT")
            return original(self, record)

        monkeypatch.setattr(GovernedPatternService, "_require_candidate_head", head_changes)
        with (
            _identity(EVALUATOR, frozenset({"governor"}), []),
            pytest.raises(IntegrityError, match="CONTROLLED_PARENT_DRIFT"),
        ):
            evaluate_quote_recovery_candidate(
                workspace, _command(event.event_id, candidate), config_path=policy
            )
        assert checks >= 2
        assert not store.list_artifacts(artifact_id_prefix="quote-recovery-eval-selection:")
        assert not store.list_artifacts(artifact_id_prefix="quote-recovery-eval-use:")


def _assert_current_parent_pair(store, monkeypatch, tmp_path):
    service = _service(store)
    proposal, boundary = _prepare(service)
    first_bundle = quote_recovery_content_bundle(service.registry)
    (first,) = service.propose(
        proposal, actor_id=AUTHOR, boundary=boundary,
        content_bundle=first_bundle.payload,
    )
    first_evaluation = service.evaluate(first, actor_id=EVALUATOR)
    assert service._load(first_evaluation)["verdict"] == "QUALIFIED"
    service.decide(
        first, first_evaluation, actor_id=GOVERNOR, verdict="ADMIT",
        expected_candidate_digest=service._load(first)["digest"],
        expected_head_package_digest=service._load(first)["base_package_digest"],
    )
    current = service._current_stable_head(TARGET_SKILL)
    assert current is not None and current.payload["generation"] == 1
    prior_proposal = service._load(proposal)
    second_proposal = service.open_proposal(
        proposal_id="proposal:quote-recovery:nplus2",
        corpus_ref=prior_proposal["corpus_ref"],
        replay_ref=prior_proposal["replay_ref"],
        skill_name=TARGET_SKILL, author_id=AUTHOR,
        budget=ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=300),
    )
    parent_package = service._package_for_digest(
        TARGET_SKILL, current.payload["package_digest"]
    )
    second_bundle = quote_recovery_content_bundle(
        _EffectiveSkillRegistry(service.registry, parent_package)
    )
    boundary_data = boundary.model_dump(mode="json", exclude={"digest"})
    boundary_data["rollback_package_digest"] = parent_package.package_digest
    (second,) = service.propose(
        second_proposal, actor_id=AUTHOR,
        boundary=SkillBoundary(**boundary_data),
        content_bundle=second_bundle.payload,
    )
    second_evaluation = service.evaluate(second, actor_id=EVALUATOR)
    assert service._load(second_evaluation)["business_oracle"]["status"] == "NO_BEHAVIOR_DELTA"
    assert service._load(second_evaluation)["verdict"] == "REJECTED"
    with pytest.raises(IntegrityError, match="NOT_QUALIFIED"):
        service.decide(
            second, second_evaluation, actor_id=GOVERNOR, verdict="ADMIT",
            expected_candidate_digest=service._load(second)["digest"],
            expected_head_package_digest=parent_package.package_digest,
        )
    assert service._current_stable_head(TARGET_SKILL).ref == current.ref

    workspace, event, _ = _recovery_workspace(store, monkeypatch)
    policy = _policy(tmp_path / "pair-policy.json", second)
    command = _command(event.event_id, second)
    with _identity(EVALUATOR, frozenset({"governor"}), []):
        paired = evaluate_quote_recovery_candidate(workspace, command, config_path=policy)
        assert evaluate_quote_recovery_candidate(workspace, command, config_path=policy) == paired
    assert paired["status"] == "CONSUMED"
    assert paired["base_head_ref"] == current.ref
    assert paired["base_head_digest"] == current.digest
    assert paired["base_head_generation"] == 1
    assert paired["baseline_package_digest"] == parent_package.package_digest
    assert paired["baseline_action"] == paired["action"] == "HANDOFF"
    assert paired["pair_gate_status"] == "REJECTED_NO_BEHAVIOR_DELTA"
    assert paired["behavior_delta_status"] == "NO_BEHAVIOR_DELTA"
    assert paired["paired_input_digest"] == paired["input_digest"]
    assert paired["baseline_invocation_receipt_digest"]
    assert paired["candidate_invocation_receipt_digest"]
    assert paired["baseline_consumed_resource_digests"]
    assert paired["consumed_resource_digests"]
    assert paired["interpreter_invocations"] == 2
    assert paired["model_invocations"] == paired["model_cost_usd"] == 0
    assert paired["qualification_status"] == "NOT_QUALIFIED"
    assert paired["quality_status"] == "NOT_EVALUATED"
    assert paired["business_effect"] == "NONE_POST_APPLY_DIAGNOSTIC_ONLY"
    assert len(service._family("admission")) == 1
    assert store.verify_event_chain()["status"] == "PASS"
    return paired


def test_sqlite_nplus2_paired_with_exact_nplus1_parent(tmp_path, monkeypatch):
    path = tmp_path / "nplus2-pair.sqlite3"
    with StateStore(
        path, tenant_id=TENANT, workspace_id=WORKSPACE
    ) as store:
        paired = _assert_current_parent_pair(store, monkeypatch, tmp_path)
    with StateStore(path, tenant_id=TENANT, workspace_id=WORKSPACE) as reopened:
        for ref, digest in (
            (paired["baseline_invocation_ref"], paired["baseline_invocation_receipt_digest"]),
            (paired["candidate_invocation_ref"], paired["candidate_invocation_receipt_digest"]),
        ):
            receipt = reopened.load_artifact(ref, INVOCATION_MEDIA).payload
            assert receipt["digest"] == digest
            assert receipt["input_digest"] == paired["input_digest"]
        assert reopened.verify_event_chain()["status"] == "PASS"


def test_postgres_nplus2_paired_with_exact_nplus1_parent(
    postgres_runtime, monkeypatch, tmp_path,
):
    config = postgres_runtime(tenant_id=TENANT)
    with StateStore(
        config["runtime_dsn"], tenant_id=TENANT, workspace_id=WORKSPACE, migrate=False
    ) as store:
        paired = _assert_current_parent_pair(store, monkeypatch, tmp_path)
    with StateStore(
        config["runtime_dsn"], tenant_id=TENANT, workspace_id=WORKSPACE, migrate=False
    ) as reopened:
        baseline = reopened.load_artifact(paired["baseline_invocation_ref"], INVOCATION_MEDIA).payload
        candidate = reopened.load_artifact(paired["candidate_invocation_ref"], INVOCATION_MEDIA).payload
        assert baseline["input_digest"] == candidate["input_digest"] == paired["input_digest"]
        assert baseline["package_digest"] == paired["baseline_package_digest"]
        assert candidate["package_digest"] == paired["package_digest"]
        assert reopened.verify_event_chain()["status"] == "PASS"
