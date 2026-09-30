"""Later independently assessed revisions cannot be silently omitted."""

from __future__ import annotations

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_recovery import RESUME_MEDIA, _prefix
from orgrebase.workspace.experience_collection import OBSERVATION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import CaseObservationV2, MemorySnapshot
from orgrebase.workspace.experience_lessons import ExperienceLessonService, LessonBody
from orgrebase.workspace.experience_recall import SNAPSHOT_MEDIA, ExperienceRecallService
from tests.workspace.test_experience_assessment import _corpus, _evaluator, _observed_case
from tests.workspace.test_experience_collection import _principal, _workspace
from tests.workspace.test_experience_delta_v2 import _services_at
from tests.workspace.test_experience_lessons import PROFILE, _actor, _as, _body, _propose, _publish, _services
from tests.workspace.test_pattern_v2_corpus import _controller


def _next_resume_case(workspace, first_case):
    event = workspace.changes.event
    resume = workspace.store.load_artifact(
        _prefix(event.event_id) + "resume:0001", RESUME_MEDIA,
    ).payload
    workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_SUPPLIED", {
        "event_id": event.event_id, "round": 1,
        "recovery_digest": resume["digest"], "actor_id": "owner:product",
    })
    with _as(_principal()):
        ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        ).collect(worker_id="worker:revision-selection")
    cases = []
    for row in workspace.store.list_artifacts(
        artifact_id_prefix="experience-case:", expected_media_type=OBSERVATION_MEDIA,
    ):
        case = CaseObservationV2.model_validate(row.payload).revalidated()
        if case.case_id == first_case.case_id and case.revision != first_case.revision:
            cases.append((row.artifact_id, case))
    assert len(cases) == 1
    assert cases[0][1].independence_cluster_id == first_case.independence_cluster_id
    return cases[0]


def test_later_counterexample_holds_old_support_corpus_lesson_and_recall(tmp_path):
    workspace, lessons, old_support = _services(tmp_path)
    try:
        with _as(_evaluator()):
            first_case, first_receipt, _ = lessons.assessment_service.read_assessment(old_support)
        candidate = _propose(
            lessons, old_support, operation="candidate:before-counter",
            lesson_id="before-counter", body=_body(),
        )
        _publish(lessons, candidate, operation="release:before-counter")
        next_ref, next_case = _next_resume_case(workspace, first_case)
        with _as(_evaluator()):
            counter = lessons.assessment_service.record_assessment(
                operation_id="assess:later-counter", case_ref=next_ref,
                profile_id=PROFILE, verdict="COUNTEREXAMPLE",
                reason_code="INDEPENDENT_RUBRIC_FAIL",
                evidence_refs=(next_case.business_event_digest,),
                independence_cluster_id=next_case.independence_cluster_id,
            )
        with _as(_actor("reader", "reader")):
            old_projection = lessons.assessment_service.assessment_projection_for_reader(
                first_receipt.case_ref, PROFILE,
            )
            assert old_projection["status"] == "HOLD"
            assert lessons.assessment_service.evidence_verdict_for_reader(old_support) is None
        controller = _controller(workspace)
        with _as(_corpus()), pytest.raises(IntegrityError, match="REVISION"):
            controller.freeze_corpus_v2(
                profile_id=PROFILE, assessment_refs=(old_support,),
                assessment_service=lessons.assessment_service,
            )
        stale = _propose(
            lessons, old_support, operation="candidate:after-counter",
            lesson_id="after-counter", body=_body(),
        )
        with _as(_actor("reviewer", "governor")), pytest.raises(IntegrityError, match="REVISION"):
            lessons.review_candidate(stale)
        with _as(_actor("reader", "reader")):
            recall = ExperienceRecallService(
                workspace, profile_id=PROFILE,
                assessment_service=lessons.assessment_service,
            )
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            snapshot = MemorySnapshot.model_validate(
                workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
            )
            assert snapshot.coverage == "EMPTY"
        with _as(_corpus()):
            corpus_ref = controller.freeze_corpus_v2(
                profile_id=PROFILE, assessment_refs=(counter,),
                assessment_service=lessons.assessment_service,
            )
            current = controller.revalidate_corpus_v2(
                corpus_ref, assessment_service=lessons.assessment_service,
            )
        assert current["independent_cluster_membership"]["COUNTEREXAMPLE"] == [
            next_case.independence_cluster_id,
        ]
        assert current["revision_selection_policy"]
        assert current["cases"][0]["revision_selection_digest"]
    finally:
        workspace.store.close()


def test_independent_later_repair_keeps_counter_history_and_requires_scoped_review(tmp_path):
    workspace, first_ref, first_case = _observed_case(tmp_path)
    try:
        from orgrebase.workspace.experience_assessment import ExperienceAssessmentService

        assessment = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1", source_qualification_check=lambda _case: None,
        )
        with _as(_evaluator()):
            old_counter = assessment.record_assessment(
                operation_id="assess:old-counter", case_ref=first_ref, profile_id=PROFILE,
                verdict="COUNTEREXAMPLE", reason_code="INDEPENDENT_RUBRIC_FAIL",
                evidence_refs=(first_case.business_event_digest,),
                independence_cluster_id=first_case.independence_cluster_id,
            )
        latest_ref, latest = _next_resume_case(workspace, first_case)
        assert latest.resume_digest is not None
        with _as(_evaluator()):
            repaired = assessment.record_assessment(
                operation_id="assess:repaired", case_ref=latest_ref, profile_id=PROFILE,
                verdict="SUPPORT", reason_code="REPAIR_VERIFIED",
                evidence_refs=(latest.resume_digest,),
                independence_cluster_id=latest.independence_cluster_id,
            )
        controller = _controller(workspace)
        with _as(_corpus()):
            corpus_ref = controller.freeze_corpus_v2(
                profile_id=PROFILE, assessment_refs=(repaired,), assessment_service=assessment,
            )
            corpus = controller.revalidate_corpus_v2(corpus_ref, assessment_service=assessment)
        assert corpus["independent_cluster_membership"]["SUPPORT"] == [latest.independence_cluster_id]
        assert old_counter in corpus["cases"][0]["revision_selection_excluded_refs"]
        body = LessonBody.model_validate({
            **_body().model_dump(mode="json", exclude={"digest"}),
            "applicability": ["仅在后续补证已独立复核时适用。"],
            "contraindications": ["旧失败条件仍存在时不得使用。", "来源失效时不得使用。"],
        })
        lessons = ExperienceLessonService(
            workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
            assessment_service=assessment,
        )
        missing_history = _propose(
            lessons, repaired, operation="candidate:missing-counter",
            lesson_id="missing-counter", body=body,
        )
        with _as(_actor("reviewer", "governor")), pytest.raises(
            IntegrityError, match="COUNTER_HISTORY",
        ):
            lessons.review_candidate(missing_history)
        with _as(_actor("author", "operator")):
            candidate = lessons.propose(
                operation_id="candidate:repaired", profile_id=PROFILE,
                lesson_id="repaired-with-conditions", body=body,
                support_assessment_refs=(repaired,), counter_assessment_refs=(old_counter,),
            )
        with _as(_actor("reviewer", "governor")):
            review = lessons.review_candidate(candidate)
            assert old_counter in review["counter_assessment_refs"]
            with pytest.raises(IntegrityError, match="REPAIR_REVIEW_REQUIRED"):
                lessons.apply_delta(
                    operation_id="release:unscoped", action="ADD", candidate_ref=candidate,
                    expected_heads=(), reason_code="EXACT_CONTENT_REVIEWED",
                    body_bytes_digest=review["body_bytes_digest"], declassified_exact_content=True,
                    purpose=PROFILE,
                )
            lessons.apply_delta(
                operation_id="release:repaired", action="ADD", candidate_ref=candidate,
                expected_heads=(), reason_code="CONDITION_SCOPED_REPAIR_REVIEWED",
                body_bytes_digest=review["body_bytes_digest"], declassified_exact_content=True,
                purpose=PROFILE,
            )
        with _as(_actor("reader", "reader")):
            recall = ExperienceRecallService(
                workspace, profile_id=PROFILE, assessment_service=assessment,
            )
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            snapshot = MemorySnapshot.model_validate(
                workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
            )
            assert len(snapshot.lesson_entries) == 1
    finally:
        workspace.store.close()


def test_postgres_cross_revision_corpus_reopens_with_exact_member_selection(postgres_dsn):
    workspace, lessons, old_support = _services_at(postgres_dsn)
    try:
        with _as(_evaluator()):
            first_case, _, _ = lessons.assessment_service.read_assessment(old_support)
        latest_ref, latest = _next_resume_case(workspace, first_case)
        with _as(_evaluator()):
            counter = lessons.assessment_service.record_assessment(
                operation_id="assess:pg-later-counter", case_ref=latest_ref,
                profile_id=PROFILE, verdict="COUNTEREXAMPLE",
                reason_code="INDEPENDENT_RUBRIC_FAIL",
                evidence_refs=(latest.business_event_digest,),
                independence_cluster_id=latest.independence_cluster_id,
            )
        controller = _controller(workspace)
        with _as(_corpus()):
            with pytest.raises(IntegrityError, match="REVISION"):
                controller.freeze_corpus_v2(
                    profile_id=PROFILE, assessment_refs=(old_support,),
                    assessment_service=lessons.assessment_service,
                )
            corpus_ref = controller.freeze_corpus_v2(
                profile_id=PROFILE, assessment_refs=(counter,),
                assessment_service=lessons.assessment_service,
            )
            before = controller.revalidate_corpus_v2(
                corpus_ref, assessment_service=lessons.assessment_service,
            )
    finally:
        workspace.store.close()
    reopened, _, _, _ = _workspace(postgres_dsn)
    try:
        from orgrebase.workspace.experience_assessment import ExperienceAssessmentService

        assessment = ExperienceAssessmentService(
            reopened, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1", source_qualification_check=lambda _case: None,
        )
        controller = _controller(reopened)
        with _as(_corpus()):
            after = controller.revalidate_corpus_v2(
                corpus_ref, assessment_service=assessment,
            )
        assert after["digest"] == before["digest"]
        assert after["cases"][0]["revision_selection_member_digest"]
        assert old_support in after["cases"][0]["revision_selection_members"][0]["assessment_refs"]
    finally:
        reopened.store.close()
