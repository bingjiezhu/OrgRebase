"""Frozen v2 Finance input reaches evaluator and governor without role borrowing."""

from __future__ import annotations

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.finance_skill_qualification import (
    CONTENT_RELEASE_MEDIA,
    QUALIFICATION_MEDIA,
    FinanceSkillQualificationService,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_finance_experiment_input import (
    AUTHOR,
    EVALUATOR,
    GOVERNOR,
    _setup,
)
from tests.workspace.test_finance_explanation_operations import _as


def _deployment_actors(monkeypatch):
    for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
        monkeypatch.setenv(
            f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID",
            f"actor:experience-{phase}",
        )
    monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "finance-v1")


def _candidate(workspace):
    event_id, parent, family_ref, inputs, candidates = _setup(workspace)
    instruction = parent.bundle.instruction_text + "Check current source coverage.\n"
    with _as(workspace, AUTHOR, "operator"):
        input_ref = inputs.freeze_development_case(
            operation_id="input:qualification-chain",
            family_ref=family_ref, event_id=event_id,
        )
        candidate_ref = candidates.propose_instruction(
            operation_id="candidate:qualification-chain",
            instruction_text=instruction, experiment_input_ref=input_ref,
        )
    bundle = candidates.head.prepare_instruction_patch(
        instruction, expected_head_ref=parent.head_ref,
        expected_head_digest=parent.head_digest,
        expected_generation=parent.generation,
        expected_package_digest=parent.package_digest,
    )
    return parent, inputs, candidates, candidate_ref, input_ref, bundle


def test_real_service_resolves_input_for_distinct_evaluator_and_governor(
    workspace, monkeypatch,
):
    parent, inputs, candidates, candidate_ref, input_ref, bundle = _candidate(workspace)
    _deployment_actors(monkeypatch)
    qualification = FinanceSkillQualificationService(
        candidates.head, workspace=workspace,
        evaluator_actor_id=EVALUATOR,
        experiment_operator_actor_id=AUTHOR,
    )
    with _as(workspace, EVALUATOR, "governor"):
        proof = qualification._candidate_proof(
            candidate_ref=candidate_ref, candidate=bundle,
            author_actor_id=AUTHOR,
        )
        assert proof["experiment_input_ref"] == input_ref
        assert proof["experiment_input_digest"] == inputs.require_current(
            input_ref, evaluator=True,
        ).digest
        assert proof["experiment_input_scope"] == "CONTROLLED_DEVELOPMENT_INPUT_ONLY"
        with pytest.raises(AuthorizationError, match="GOVERNOR_REQUIRED"):
            inputs.require_current_for_governor(input_ref)
        with pytest.raises(IntegrityError, match="REAL_SEALED_INPUT_NOT_QUALIFIED"):
            qualification.issue_qualification(
                suite_ref="finance-sealed-suite:not-issued",
                candidate=bundle, pairs=(), author_actor_id=AUTHOR,
                candidate_proposal_ref=candidate_ref,
            )
    with _as(workspace, GOVERNOR, "governor"):
        governor_proof = qualification._candidate_proof(
            candidate_ref=candidate_ref, candidate=bundle,
            author_actor_id=AUTHOR,
        )
        assert governor_proof["experiment_input_digest"] == proof["experiment_input_digest"]
        assert inputs.require_current_for_governor(input_ref).digest == proof[
            "experiment_input_digest"
        ]
        with pytest.raises(AuthorizationError, match="PHASE_ACTOR_REQUIRED"):
            candidates.verify_for_qualification(candidate_ref, candidate=bundle)
        assert candidates.verify_for_qualification(
            candidate_ref, candidate=bundle, phase="GOVERNOR",
        )["experiment_input_ref"] == input_ref
    assert candidates.head.resolve().head_ref == parent.head_ref
    assert candidates.head.resolve().qualification_status == "UNQUALIFIED"
    assert not workspace.store.list_artifacts(
        artifact_id_prefix="finance-skill-qualification:",
        expected_media_type=QUALIFICATION_MEDIA,
    )
    assert not workspace.store.list_artifacts(
        artifact_id_prefix="finance-content-release:",
        expected_media_type=CONTENT_RELEASE_MEDIA,
    )


def test_v2_without_deployment_input_authority_fails_closed(workspace, monkeypatch):
    parent, _, candidates, candidate_ref, _, bundle = _candidate(workspace)
    for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
        monkeypatch.delenv(f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", raising=False)
    monkeypatch.delenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", raising=False)
    qualification = FinanceSkillQualificationService(
        candidates.head, workspace=workspace,
        evaluator_actor_id=EVALUATOR, experiment_operator_actor_id=AUTHOR,
    )
    with (
        _as(workspace, EVALUATOR, "governor"),
        pytest.raises(IntegrityError, match="EXPERIENCE_PHASE_AUTHORITY_UNCONFIGURED"),
    ):
        qualification._candidate_proof(
            candidate_ref=candidate_ref, candidate=bundle,
            author_actor_id=AUTHOR,
        )
    assert candidates.head.resolve().head_ref == parent.head_ref
    assert candidates.head.resolve().qualification_status == "UNQUALIFIED"


def test_current_query_loss_holds_both_evaluator_and_governor(
    workspace, monkeypatch,
):
    _, inputs, candidates, candidate_ref, input_ref, bundle = _candidate(workspace)
    _deployment_actors(monkeypatch)
    qualification = FinanceSkillQualificationService(
        candidates.head, workspace=workspace,
        evaluator_actor_id=EVALUATOR, experiment_operator_actor_id=AUTHOR,
    )
    with _as(workspace, AUTHOR, "operator"):
        frozen = inputs.require_current(input_ref)
        assert workspace.private_records.erase(frozen.query_ref, actor_id=AUTHOR)
    for actor in (EVALUATOR, GOVERNOR):
        with (
            _as(workspace, actor, "governor"),
            pytest.raises(IntegrityError, match="EXPERIENCE_MEMORY_HOLD"),
        ):
            qualification._candidate_proof(
                candidate_ref=candidate_ref, candidate=bundle,
                author_actor_id=AUTHOR,
            )


def test_governor_review_and_promotion_hold_controlled_input_before_sealed_reads(
    workspace, monkeypatch,
):
    parent, _, candidates, candidate_ref, _, bundle = _candidate(workspace)
    _deployment_actors(monkeypatch)
    qualification = FinanceSkillQualificationService(
        candidates.head, workspace=workspace,
        evaluator_actor_id=EVALUATOR, experiment_operator_actor_id=AUTHOR,
    )
    qualification_ref = "finance-skill-qualification:controlled-not-issued"
    release_ref = "finance-content-release:controlled-not-issued"
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(
            connection, qualification_ref, QUALIFICATION_MEDIA,
            {"candidate_proposal_ref": candidate_ref, "author_actor_id": AUTHOR},
        )
        workspace.store.save_artifact(
            connection, release_ref, CONTENT_RELEASE_MEDIA, {"status": "NOT_ISSUED"},
        )
    with _as(workspace, GOVERNOR, "governor"):
        with pytest.raises(IntegrityError, match="REAL_SEALED_INPUT_NOT_QUALIFIED"):
            qualification.review_content(
                candidate=bundle, qualification_ref=qualification_ref,
                author_actor_id=AUTHOR, reason_code="CONTROLLED_REVIEW",
            )
        with pytest.raises(IntegrityError, match="REAL_SEALED_INPUT_NOT_QUALIFIED"):
            qualification.promote(
                candidate=bundle, qualification_ref=qualification_ref,
                content_release_ref=release_ref,
                expected_head_ref=parent.head_ref,
                expected_head_digest=parent.head_digest,
                expected_generation=parent.generation,
                expected_package_digest=parent.package_digest,
            )
    assert candidates.head.resolve().head_ref == parent.head_ref
    assert candidates.head.resolve().qualification_status == "UNQUALIFIED"


def test_postgres_restart_resolves_same_v2_input_for_both_phases(
    postgres_runtime, monkeypatch, tmp_path,
):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    config = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = {
        "runtime_configuration": runtime,
        "store_path": config["runtime_dsn"],
        "store_tenant_id": runtime.profile.organization_id,
        "store_migrate": False,
        "review_duration_seconds": 0,
    }
    first = WorkspaceService(**kwargs)
    try:
        first.form_quote()
        parent, _, _, candidate_ref, input_ref, bundle = _candidate(first)
    finally:
        first.close()
    _deployment_actors(monkeypatch)
    reopened = WorkspaceService(**kwargs)
    try:
        head = FinanceSkillHeadService(
            reopened.store, tenant_id=reopened.profile.organization_id,
        )
        qualification = FinanceSkillQualificationService(
            head, workspace=reopened,
            evaluator_actor_id=EVALUATOR, experiment_operator_actor_id=AUTHOR,
        )
        with _as(reopened, EVALUATOR, "governor"):
            evaluator_proof = qualification._candidate_proof(
                candidate_ref=candidate_ref, candidate=bundle,
                author_actor_id=AUTHOR,
            )
        with _as(reopened, GOVERNOR, "governor"):
            governor_proof = qualification._candidate_proof(
                candidate_ref=candidate_ref, candidate=bundle,
                author_actor_id=AUTHOR,
            )
        assert evaluator_proof["experiment_input_ref"] == input_ref
        assert governor_proof["experiment_input_digest"] == evaluator_proof[
            "experiment_input_digest"
        ]
        assert head.resolve().head_ref == parent.head_ref
        assert head.resolve().qualification_status == "UNQUALIFIED"
        assert reopened.store.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()
