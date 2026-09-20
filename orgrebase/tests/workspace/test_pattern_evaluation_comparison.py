from __future__ import annotations

from copy import deepcopy

import pytest

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import (
    OBSERVATION_MEDIA,
    _observation_id,
)
from orgrebase.workspace.skill_packages import SkillPackageEvaluator, SkillPackageRegistry
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_pattern_evolution import prepare, qualify
from tests.workspace.test_pattern_evolution import setup as setup
from tests.workspace.test_pattern_evolution_postgres import service as postgres_service
from tests.workspace.test_skill_package_lifecycle import FIXED_TIME, _cases


def comparison(service, evaluation):
    return service.evaluation_comparison(evaluation, actor_id=service.governance_authority)


def test_observations_are_exact_issued_results_and_defensive_copies():
    registry = SkillPackageRegistry()
    evaluator = SkillPackageEvaluator(registry)
    cases = _cases("structured-domain-handoff", registry)
    receipt = evaluator.evaluate("structured-domain-handoff", cases, evaluated_at=FIXED_TIME)
    before = canonical_json(receipt)
    rows = evaluator.invocation_observations(receipt)
    assert len(rows) == len(cases)
    for row, case, outcome in zip(rows, cases, receipt["case_results"], strict=True):
        assert row["input_digest"] == sha256_digest(case.public_input)
        assert sha256_digest(row["result"]) == outcome["candidate_output_digest"]
    rows[0]["result"]["action"] = "FORGED"
    assert evaluator.invocation_observations(receipt)[0]["result"]["action"] != "FORGED"
    assert canonical_json(receipt) == before
    with pytest.raises(IntegrityError, match="RECEIPT_NOT_ISSUED"):
        evaluator.invocation_observations(deepcopy(receipt))


def test_same_behavior_keeps_all_cases_without_extra_invocations_or_read_writes(setup, monkeypatch):
    service, store, _ = setup
    calls = []
    invoke = SkillPackageRegistry.invoke_for_evaluation

    def counted(registry, *args, **kwargs):
        calls.append(kwargs["expected_package_digest"])
        return invoke(registry, *args, **kwargs)

    monkeypatch.setattr(SkillPackageRegistry, "invoke_for_evaluation", counted)
    _, evaluation, _, _, _ = qualify(service)
    assert len(calls) == 16  # The existing eight candidate and eight prior invocations.
    before = store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    result = comparison(service, evaluation)
    assert len(calls) == 16
    assert store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == before
    assert store.connection.execute("SELECT COUNT(*) FROM current_pointers").fetchone()[0] == 0
    assert result["behavior_delta"] == "NO_BEHAVIOR_DELTA"
    assert result["case_count"] == result["observed_case_count"] == 8
    assert result["unknown_case_count"] == result["changed_case_count"] == 0
    assert {row["baseline_action"] for row in result["cases"]} == {"HANDOFF", "DENY", "ABSTAIN"}
    assert result["change_sources"]["applicability_changed"] is True
    assert result["change_sources"]["program_changed"] is False
    assert result["change_sources"]["changed_resources"] == []
    with pytest.raises(AuthorizationError, match="CONTROLLER_AUTHORITY_DENIED"):
        service.evaluation_comparison(evaluation, actor_id="learner:bounded")


def test_actual_applicability_change_is_visible_even_when_program_and_resources_match(setup):
    service, _, _ = setup
    proposal, boundary, _, _ = prepare(service, paths=("/candidate_bundle/region",))
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    evaluation = service.evaluate(candidate, actor_id=service.evaluator_authority)
    assert service._load(evaluation)["verdict"] == "REJECTED"
    result = comparison(service, evaluation)
    rows = {row["case_ref"]: row for row in result["cases"]}
    assert result["behavior_delta"] == "OBSERVED_CHANGE"
    assert result["case_count"] == 8 and result["changed_case_count"] == 1
    changed = rows["replay:NEGATIVE_TRANSFER"]
    assert (changed["baseline_action"], changed["candidate_action"]) == ("HANDOFF", "ABSTAIN")
    assert changed["action_changed"] is changed["behavior_changed"] is True
    assert result["change_sources"]["program_changed"] is False
    assert result["change_sources"]["changed_resources"] == []
    assert result["change_sources"]["applicability_changed"] is True


def test_legacy_evaluation_without_observations_stays_unknown_and_never_reexecutes(setup, monkeypatch):
    service, store, _ = setup
    save = store.save_artifact

    def legacy_save(connection, artifact_id, media_type, payload):
        if media_type != OBSERVATION_MEDIA:
            return save(connection, artifact_id, media_type, payload)
        return sha256_digest(payload)

    monkeypatch.setattr(store, "save_artifact", legacy_save)
    _, evaluation, _, _, _ = qualify(service)

    def forbidden(*args, **kwargs):
        raise AssertionError("A historical diagnostic must not execute or write")

    monkeypatch.setattr(SkillPackageRegistry, "invoke_for_evaluation", forbidden)
    monkeypatch.setattr(store, "save_artifact", forbidden)
    result = comparison(service, evaluation)
    assert result["behavior_delta"] == "UNKNOWN"
    assert result["case_count"] == result["unknown_case_count"] == 8
    assert result["observed_case_count"] == 0 and result["changed_case_count"] is None
    assert result["observation_receipt_digest"] is result["change_sources"] is None
    assert all(
        row["baseline_action"] is row["candidate_action"] is row["behavior_changed"] is None
        for row in result["cases"]
    )


@pytest.mark.parametrize("mutation", ["input", "result", "package", "receipt", "case_set", "structure"])
def test_resealed_observation_substitution_cannot_override_original_receipts(setup, monkeypatch, mutation):
    service, store, _ = setup
    _, evaluation_ref, _, _, _ = qualify(service)
    evaluation = service._load(evaluation_ref)
    artifact_id = _observation_id(evaluation)
    original = store.load_artifact

    def substituted(key, expected_media_type=None):
        stored = original(key, expected_media_type)
        if key != artifact_id:
            return stored
        body = deepcopy(stored.payload)
        if mutation == "input":
            body["candidate"]["cases"][0]["input_digest"] = sha256_digest({"wrong": "input"})
        elif mutation == "result":
            body["baseline"]["cases"][0]["result"]["action"] = "FORGED"
        elif mutation == "package":
            body["baseline"]["manifest"] = body["candidate"]["manifest"]
        elif mutation == "receipt":
            body["baseline_receipt_digest"] = body["candidate_receipt_digest"]
        elif mutation == "case_set":
            body["candidate"]["cases"].pop()
        else:
            body["candidate"]["cases"][0]["result"] = None
        body["digest"] = sha256_digest({key: value for key, value in body.items() if key != "digest"})
        return stored.model_copy(update={"payload": body})

    monkeypatch.setattr(store, "load_artifact", substituted)
    with pytest.raises(IntegrityError, match="PATTERN_EVALUATION_OBSERVATION_"):
        comparison(service, evaluation_ref)


def test_observation_save_failure_rolls_back_evaluation_with_existing_charge_retained(setup, monkeypatch):
    service, store, _ = setup
    proposal, boundary, _, _ = prepare(service)
    (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    save = store.save_artifact

    def failing_save(connection, artifact_id, media_type, payload):
        if media_type == OBSERVATION_MEDIA:
            raise RuntimeError("CONTROLLED_OBSERVATION_WRITE_FAILURE")
        return save(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(store, "save_artifact", failing_save)
    with pytest.raises(RuntimeError, match="CONTROLLED_OBSERVATION_WRITE_FAILURE"):
        service.evaluate(candidate, actor_id=service.evaluator_authority)
    assert service._family("evaluation") == ()
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE media_type = ?", (OBSERVATION_MEDIA,)
        ).fetchone()[0]
        == 0
    )
    assert sum(row["invocations"] for row in service._usage(proposal)) == 16


def test_postgres_observations_survive_reopen_and_remain_scoped(postgres_runtime):
    config = postgres_runtime(tenant_id="org:comparison")
    with StateStore(config["runtime_dsn"], tenant_id="org:comparison", migrate=False) as store:
        store.register_workspace(
            "other",
            profile_digest=sha256_digest({"other": 1}),
            pack_digest=None,
            quote_object_id="quote:other",
            created_at=FIXED_TIME,
        )
        service = postgres_service(store)
        _, evaluation, _, _, _ = qualify(service)
        expected = comparison(service, evaluation)
    with StateStore(config["runtime_dsn"], tenant_id="org:comparison", migrate=False) as store:
        assert comparison(postgres_service(store), evaluation) == expected
    with (
        StateStore(
            config["runtime_dsn"], tenant_id="org:comparison", workspace_id="other", migrate=False
        ) as store,
        pytest.raises(KeyError, match="ARTIFACT_NOT_FOUND"),
    ):
        comparison(postgres_service(store), evaluation)
