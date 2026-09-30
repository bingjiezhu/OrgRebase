"""One controlled development input binds Finance head, case and recall."""

from __future__ import annotations

from dataclasses import replace
from functools import partial

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService, require_quote_case_current
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, OBSERVATION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import (
    CaseObservationV2,
    MemorySnapshot,
    RecallSelectionManifest,
)
from orgrebase.workspace.experience_lessons import ExperienceLessonService
from orgrebase.workspace.experience_recall import ExperienceRecallService
from orgrebase.workspace.finance_experiment import (
    REVIEWED_FINANCE_RUBRIC_DIGEST,
    FinanceExperimentService,
    derive_finance_case_cluster,
)
from orgrebase.workspace.finance_experiment_input import (
    INPUT_MEDIA,
    FinanceExperimentInputService,
)
from orgrebase.workspace.finance_skill_candidates import CANDIDATE_MEDIA, FinanceSkillCandidateService
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_experience_lessons import _body
from tests.workspace.test_experience_source_qualification import (
    SLOT,
    _sync_qualified_source,
)
from tests.workspace.test_experience_source_qualification import (
    _actor as _experience_actor,
)
from tests.workspace.test_experience_source_qualification import (
    source_workspace as source_workspace,
)
from tests.workspace.test_finance_explanation_operations import _as, _prepare

AUTHOR = "actor:finance-operator"
EVALUATOR = "actor:finance-input-evaluator"
GOVERNOR = "actor:finance-governor"


def _setup(workspace, *, assessment_override=None):
    event_id, head = _prepare(workspace)
    head_service = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id,
    )
    experiment = FinanceExperimentService(
        workspace.store, tenant_id=workspace.profile.organization_id,
        evaluator_actor_id=EVALUATOR, operator_actor_id=AUTHOR,
        workspace=workspace, release_governor_actor_id=GOVERNOR,
    )
    derived = derive_finance_case_cluster(workspace, event_id)
    clusters = (derived["cluster_id"], "cluster:validation", "cluster:heldout")
    cases = {
        event_id: {
            "case_revision_digest": derived["case_revision_digest"],
            "independence_cluster_id": derived["cluster_id"],
            "split": "DEVELOPMENT", "source_change_key": derived["source_change_key"],
            "time_ordinal": 1,
        },
        "case:validation": {
            "case_revision_digest": sha256_digest("validation"),
            "independence_cluster_id": clusters[1],
            "split": "VALIDATION", "source_change_key": "source:validation",
            "time_ordinal": 2,
        },
        "case:heldout": {
            "case_revision_digest": sha256_digest("heldout"),
            "independence_cluster_id": clusters[2],
            "split": "SEALED_HOLDOUT", "source_change_key": "source:heldout",
            "time_ordinal": 3,
        },
    }
    with _as(workspace, EVALUATOR, "governor"):
        family_ref = experiment.freeze_family(
            family_id="family:input-one", parent_head_ref=head.head_ref,
            parent_head_digest=head.head_digest, cases=cases,
            seeds=(11, 23, 37), orders={
                "CHRONOLOGICAL": clusters,
                "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
                "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
            }, rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
            max_queries=9, max_reserved_calls=18, max_reserved_microusd=90_000,
        )
    assessment = assessment_override or ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:experience-evaluator",
        excluded_actor_ids=(AUTHOR, EVALUATOR, GOVERNOR),
        rubric_version="finance-v1",
        source_qualification_check=lambda case: require_quote_case_current(workspace, case),
    )
    recall = ExperienceRecallService(
        workspace, profile_id="workspace-change-explanation-v1",
        assessment_service=assessment,
    )
    inputs = FinanceExperimentInputService(
        workspace, head=head_service, experiment=experiment, recall=recall,
    )
    candidates = FinanceSkillCandidateService(
        workspace, head_service, experiment_input=inputs,
    )
    return event_id, head, family_ref, inputs, candidates


def test_one_dev_input_freezes_empty_recall_and_binds_private_author_candidate(workspace):
    event_id, head, family_ref, inputs, candidates = _setup(workspace)
    with _as(workspace, AUTHOR, "operator"):
        input_ref = inputs.freeze_development_case(
            operation_id="input:one", family_ref=family_ref, event_id=event_id,
        )
        assert inputs.freeze_development_case(
            operation_id="input:one", family_ref=family_ref, event_id=event_id,
        ) == input_ref
        frozen = inputs.require_current(input_ref)
        instruction = head.bundle.instruction_text + "Check current Finance source coverage.\n"
        candidate_ref = candidates.propose_instruction(
            operation_id="candidate:input-one", instruction_text=instruction,
            experiment_input_ref=input_ref,
        )
    assert frozen.head_ref == head.head_ref
    assert frozen.package_digest == head.package_digest
    assert frozen.reference_digest == head.bundle.resource_digests["references/change-explanation.md"]
    assert frozen.memory_reserved_bytes == frozen.guidance_budget_bytes == 4096
    assert frozen.context_tags == ("finance",)
    assert frozen.evidence_scope == "CONTROLLED_DEVELOPMENT_INPUT_ONLY"
    assert frozen.qualification_status == "NOT_QUALIFIED"
    assert workspace.store.load_artifact(input_ref, INPUT_MEDIA).payload["digest"] == frozen.digest
    with _as(workspace, EVALUATOR, "governor"):
        assert inputs.require_current(input_ref, evaluator=True).digest == frozen.digest
        candidate = candidates.head.prepare_instruction_patch(
            instruction, expected_head_ref=head.head_ref,
            expected_head_digest=head.head_digest,
            expected_generation=head.generation,
            expected_package_digest=head.package_digest,
        )
        proof = candidates.verify_for_qualification(candidate_ref, candidate=candidate)
        assert proof["author_actor_id"] == AUTHOR
    assert head.generation == 0 and head.qualification_status == "UNQUALIFIED"
    assert workspace.store.verify_event_chain()["status"] == "PASS"


def test_frozen_input_rejects_cross_actor_query_loss_and_changed_instruction(workspace):
    event_id, head, family_ref, inputs, candidates = _setup(workspace)
    with _as(workspace, AUTHOR, "operator"):
        input_ref = inputs.freeze_development_case(
            operation_id="input:one", family_ref=family_ref, event_id=event_id,
        )
        frozen = inputs.require_current(input_ref)
        with pytest.raises(IntegrityError, match="FROZEN_INPUT_OR_BUDGET_INVALID"):
            candidates.propose_instruction(
                operation_id="candidate:too-long",
                instruction_text="A" * (frozen.instruction_available_bytes + 1),
                experiment_input_ref=input_ref,
            )
        instruction = head.bundle.instruction_text + "Review current source.\n"
        candidate_ref = candidates.propose_instruction(
            operation_id="candidate:same-key", instruction_text=instruction,
            experiment_input_ref=input_ref,
        )
        with pytest.raises(IntegrityError, match="OPERATION_CONFLICT"):
            candidates.propose_instruction(
                operation_id="candidate:same-key",
                instruction_text=instruction + " Changed.\n",
                experiment_input_ref=input_ref,
            )
    with _as(workspace, "actor:other-author", "operator"), pytest.raises(
        AuthorizationError, match="PHASE_ACTOR_REQUIRED",
    ):
        inputs.require_current(input_ref)
    with _as(workspace, AUTHOR, "operator"), pytest.raises(
        Exception, match="AUTH_ACTION_DENIED",
    ):
        inputs.require_current(input_ref, evaluator=True)
    with _as(workspace, AUTHOR, "operator"):
        assert workspace.private_records.erase(
            frozen.query_ref, actor_id=AUTHOR,
        )
        with pytest.raises(IntegrityError, match="EXPERIENCE_MEMORY_HOLD"):
            inputs.require_current(input_ref)
    with _as(workspace, EVALUATOR, "governor"), pytest.raises(
        IntegrityError, match="EXPERIENCE_MEMORY_HOLD",
    ):
        inputs.require_current(input_ref, evaluator=True)
    assert workspace.store.load_artifact(candidate_ref, CANDIDATE_MEDIA).payload[
        "experiment_input_ref"
    ] == input_ref


def test_parent_or_case_drift_holds_frozen_input(workspace, monkeypatch):
    event_id, _, family_ref, inputs, _ = _setup(workspace)
    with _as(workspace, AUTHOR, "operator"):
        input_ref = inputs.freeze_development_case(
            operation_id="input:one", family_ref=family_ref, event_id=event_id,
        )
        original = inputs.head.resolve
        monkeypatch.setattr(
            inputs.head, "resolve",
            lambda: replace(original(), head_digest=sha256_digest("advanced-head")),
        )
        with pytest.raises(IntegrityError, match=r"FAMILY_OR_HEAD_INVALID|STALE_BASE_OR_CASE"):
            inputs.require_current(input_ref)
        monkeypatch.setattr(inputs.head, "resolve", original)
        with pytest.raises(IntegrityError, match="FAMILY_OR_HEAD_INVALID"):
            inputs.freeze_development_case(
                operation_id="input:sealed", family_ref=family_ref,
                event_id="case:heldout",
            )


def test_incomplete_freeze_holds_same_operation_and_new_cluster_operation(workspace, monkeypatch):
    event_id, _, family_ref, inputs, _ = _setup(workspace)

    def interrupted_snapshot(**_kwargs):
        raise IntegrityError("CONTROLLED_SNAPSHOT_INTERRUPTED")

    monkeypatch.setattr(inputs.recall, "build_snapshot", interrupted_snapshot)
    with _as(workspace, AUTHOR, "operator"):
        with pytest.raises(IntegrityError, match="CONTROLLED_SNAPSHOT_INTERRUPTED"):
            inputs.freeze_development_case(
                operation_id="input:lost", family_ref=family_ref, event_id=event_id,
            )
        with pytest.raises(IntegrityError, match="INCOMPLETE_OR_ALREADY_FROZEN"):
            inputs.freeze_development_case(
                operation_id="input:lost", family_ref=family_ref, event_id=event_id,
            )
        with pytest.raises(IntegrityError, match="CLUSTER_ALREADY_FROZEN_OR_UNKNOWN"):
            inputs.freeze_development_case(
                operation_id="input:new-id", family_ref=family_ref, event_id=event_id,
            )


def test_postgres_reopen_revalidates_exact_development_input(postgres_runtime, tmp_path):
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
        event_id, _, family_ref, inputs, _ = _setup(first)
        with _as(first, AUTHOR, "operator"):
            input_ref = inputs.freeze_development_case(
                operation_id="input:pg-restart", family_ref=family_ref, event_id=event_id,
            )
            frozen = inputs.require_current(input_ref)
    finally:
        first.close()
    reopened = WorkspaceService(**kwargs)
    try:
        head_service = FinanceSkillHeadService(
            reopened.store, tenant_id=reopened.profile.organization_id,
        )
        experiment = FinanceExperimentService(
            reopened.store, tenant_id=reopened.profile.organization_id,
            evaluator_actor_id=EVALUATOR, operator_actor_id=AUTHOR,
            workspace=reopened, release_governor_actor_id=GOVERNOR,
        )
        assessment = ExperienceAssessmentService(
            reopened, evaluator_actor_id="actor:experience-evaluator",
            excluded_actor_ids=(AUTHOR, EVALUATOR, GOVERNOR),
            rubric_version="finance-v1",
            source_qualification_check=lambda case: require_quote_case_current(reopened, case),
        )
        recall = ExperienceRecallService(
            reopened, profile_id="workspace-change-explanation-v1",
            assessment_service=assessment,
        )
        inputs = FinanceExperimentInputService(
            reopened, head=head_service, experiment=experiment, recall=recall,
        )
        with _as(reopened, AUTHOR, "operator"):
            assert inputs.require_current(input_ref).digest == frozen.digest
        with _as(reopened, EVALUATOR, "governor"):
            assert inputs.require_current(input_ref, evaluator=True).digest == frozen.digest
        assert reopened.store.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()


def test_one_source_qualified_lesson_is_frozen_then_source_revision_holds(
    source_workspace, tmp_path,
):
    workspace, principal, config, mapping = source_workspace
    event, synchronizer, reader, row = _sync_qualified_source(
        workspace, principal, config, mapping, tmp_path,
    )
    with principal("operator"):
        workspace.preview_change(event.event_id)
    with principal(SLOT):
        return_for_evidence(workspace, event.event_id, return_command(workspace, event.event_id))
        ready = resume_change(workspace, event.event_id, resume_command(workspace, event.event_id))
        preview = workspace._preview_record(event.event_id)
        approval = workspace.approve_change(
            event.event_id, actor_id=event.owner_id,
            preview_digest=preview["preview_digest"],
            recovery_digest=ready["recovery_digest"],
        )
        workspace.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
    with _experience_actor(workspace, "collector", "reader"):
        collector = ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        )
        for _ in range(20):
            if collector.collect(worker_id="input-lesson-reader", limit=500).status == "IDLE":
                break
        else:
            pytest.fail("collector did not reach the current event head")
    cases = []
    for row_ref in workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    ):
        case_ref = row_ref.payload.get("case_ref")
        if case_ref is None:
            continue
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
        )
        if (
            case.business_event_id == event.event_id
            and case.resume_digest is not None
            and case.execution_outcome == "APPLIED"
        ):
            cases.append((case_ref, case))
    assert cases
    case_ref, case = max(
        cases,
        key=lambda pair: workspace.store.event_by_digest(
            pair[1].origin_event_refs[-1]
        )["sequence_no"],
    )
    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
        rubric_version="finance-explanation-controlled-v1",
        source_qualification_check=partial(require_quote_case_current, workspace),
    )
    with _experience_actor(workspace, "evaluator", "governor"):
        support_ref = assessment.record_assessment(
            operation_id="assess:input-lesson", case_ref=case_ref,
            profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
            reason_code="INDEPENDENT_SOURCE_RUBRIC_PASS",
            evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
    lessons = ExperienceLessonService(
        workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
        assessment_service=assessment,
    )
    with _experience_actor(workspace, "author", "operator"):
        lesson_candidate = lessons.propose(
            operation_id="candidate:input-lesson",
            profile_id="workspace-change-explanation-v1",
            lesson_id="qualified-input-lesson", body=_body(),
            support_assessment_refs=(support_ref,),
        )
    with _experience_actor(workspace, "reviewer", "governor"):
        reviewed = lessons.review_candidate(lesson_candidate)
        lessons.apply_delta(
            operation_id="release:input-lesson", action="ADD",
            candidate_ref=lesson_candidate, expected_heads=(),
            reason_code="EXACT_CONTENT_REVIEWED",
            body_bytes_digest=reviewed["body_bytes_digest"],
            declassified_exact_content=True,
            purpose="workspace-change-explanation-v1",
            recipients=("workspace-advisory",),
        )
    _, _, family_ref, inputs, _ = _setup(workspace, assessment_override=assessment)
    with _as(workspace, AUTHOR, "operator"):
        input_ref = inputs.freeze_development_case(
            operation_id="input:one-lesson", family_ref=family_ref,
            event_id="edit-1",
        )
        frozen = inputs.require_current(input_ref)
    with _as(workspace, EVALUATOR, "governor"):
        assert inputs.require_current(input_ref, evaluator=True).digest == frozen.digest
    manifest = RecallSelectionManifest.model_validate(
        workspace.store.load_artifact(frozen.manifest_ref).payload,
    )
    snapshot = MemorySnapshot.model_validate(
        workspace.store.load_artifact(frozen.snapshot_ref).payload,
    )
    assert len(snapshot.lesson_entries) == 1
    assert frozen.context_tags == ("finance",)
    assert manifest.selected_lessons == ()
    replacement = {**row, "@odata.etag": 'W/"source-new-revision"'}
    reader.fetch = lambda _cursor: reader.parse_page({
        "@odata.deltaLink": reader.settings.endpoint + "?$deltatoken=source-new-revision",
        "value": [replacement],
    })
    reader.read_record = lambda _identity: reader._live_record(replacement)
    with principal("operator"):
        synchronizer.sync_page()
    with _as(workspace, AUTHOR, "operator"), pytest.raises(
        IntegrityError, match="EXPERIENCE_MEMORY_HOLD",
    ):
        inputs.require_current(input_ref)
