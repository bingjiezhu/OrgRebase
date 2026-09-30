"""Real PostgreSQL lesson CAS, independent assessment and source invalidation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from orgrebase.auth import request_principal
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.experience_assessment import (
    ExperienceAssessmentService,
    require_quote_case_current,
)
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, OBSERVATION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import CaseObservationV2, MemorySnapshot
from orgrebase.workspace.experience_lessons import ExperienceLessonService, lesson_object_prefix
from orgrebase.workspace.experience_recall import SNAPSHOT_MEDIA, ExperienceRecallService
from tests.postgres_support import postgres_dsn as postgres_dsn
from tests.workspace.test_experience_assessment import _evaluator
from tests.workspace.test_experience_collection import _principal, _workspace
from tests.workspace.test_experience_lessons import PROFILE, _actor, _as, _body, _propose, _publish


def _fixture_source_current(workspace, case):
    """Mechanism fixture only; production uses SourceBinding coverage."""

    current = workspace.store.get_object(workspace.changes.get(case.business_event_id).proposal.id)
    if current.state is not ObjectState.CURRENT:
        raise IntegrityError("TEST_SOURCE_STALE")


def test_postgres_lesson_head_survives_restart_and_source_stale_blocks_recall(postgres_dsn):
    workspace, event, request, _ = _workspace(postgres_dsn)
    try:
        workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        token = request_principal.set(_principal())
        try:
            ExperienceCollector(
                workspace, collector_actor_id="actor:collector", enabled=True,
            ).collect(worker_id="worker:collector")
        finally:
            request_principal.reset(token)
        case_ref = workspace.store.list_artifacts(
            artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
        )[0].payload["case_ref"]
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
        )
        current_source = VersionedObject.model_validate({
            **event.proposal.model_dump(mode="json", exclude={"digest"}),
            "state": "CURRENT",
        })
        with workspace.store.transaction() as connection:
            workspace.store.create_current_if_absent(connection, current_source)
        with _as(_evaluator()), pytest.raises(IntegrityError, match="CONTROLLED_SOURCE_NOT_CURRENT"):
            require_quote_case_current(workspace, case)
        assessment = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1",
            source_qualification_check=lambda selected: _fixture_source_current(workspace, selected),
        )
        with _as(_evaluator()):
            support_ref = assessment.record_assessment(
                operation_id="assess:pg", case_ref=case_ref, profile_id=PROFILE,
                verdict="SUPPORT", reason_code="RUBRIC_PASS",
                evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            )
        lessons = ExperienceLessonService(
            workspace, author_actor_id="actor:author",
            reviewer_actor_id="actor:reviewer", assessment_service=assessment,
        )
        initial = _propose(
            lessons, support_ref, operation="candidate:pg-1",
            lesson_id="pg-source-check", body=_body(),
        )
        _publish(lessons, initial, operation="release:pg-1")
        with _as(_actor("reviewer", "governor")):
            head = lessons.head_expectation(PROFILE, "pg-source-check")
        next_candidate = _propose(
            lessons, support_ref, operation="candidate:pg-2", lesson_id="pg-source-check",
            body=_body(steps=("核对当前业务来源和证据范围。", "再确认解释覆盖。")),
        )
        _publish(
            lessons, next_candidate, operation="release:pg-2",
            action="REVISE", heads=(head,),
        )
        assert workspace.store.get_object(lesson_object_prefix(PROFILE) + "pg-source-check").version == "r2"
    finally:
        workspace.store.close()
    reopened, _, _, _ = _workspace(postgres_dsn)
    try:
        assert reopened.store.get_object(lesson_object_prefix(PROFILE) + "pg-source-check").version == "r2"
        assessment = ExperienceAssessmentService(
            reopened, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1",
            source_qualification_check=lambda selected: _fixture_source_current(reopened, selected),
        )
        recall = ExperienceRecallService(reopened, profile_id=PROFILE, assessment_service=assessment)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            assert MemorySnapshot.model_validate(
                reopened.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
            ).coverage == "COMPLETE"
        with reopened.store.transaction() as connection:
            reopened.store.transition_current(connection, current_source.id, ObjectState.STALE)
        with _as(_actor("reader", "reader")):
            after = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            assert MemorySnapshot.model_validate(
                reopened.store.load_artifact(after, SNAPSHOT_MEDIA).payload,
            ).coverage == "EMPTY"
    finally:
        reopened.store.close()


def test_postgres_competing_multihead_delta_is_atomic_and_losing_approval_stays_stale(postgres_dsn):
    workspace, event, request, _ = _workspace(postgres_dsn)
    peer = None
    reopened = None
    try:
        workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        token = request_principal.set(_principal())
        try:
            ExperienceCollector(
                workspace, collector_actor_id="actor:collector", enabled=True,
            ).collect(worker_id="worker:multihead")
        finally:
            request_principal.reset(token)
        case_ref = workspace.store.list_artifacts(
            artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
        )[0].payload["case_ref"]
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
        )
        current_source = VersionedObject.model_validate({
            **event.proposal.model_dump(mode="json", exclude={"digest"}),
            "state": "CURRENT",
        })
        with workspace.store.transaction() as connection:
            workspace.store.create_current_if_absent(connection, current_source)

        def service(selected_workspace):
            assessment = ExperienceAssessmentService(
                selected_workspace, evaluator_actor_id="actor:evaluator",
                excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
                rubric_version="finance-v1",
                source_qualification_check=lambda selected: _fixture_source_current(
                    selected_workspace, selected,
                ),
            )
            return ExperienceLessonService(
                selected_workspace, author_actor_id="actor:author",
                reviewer_actor_id="actor:reviewer", assessment_service=assessment,
            )

        first_service = service(workspace)
        with _as(_evaluator()):
            support_ref = first_service.assessment_service.record_assessment(
                operation_id="assess:multihead", case_ref=case_ref, profile_id=PROFILE,
                verdict="SUPPORT", reason_code="RUBRIC_PASS",
                evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            )
        for number in (1, 2):
            candidate = _propose(
                first_service, support_ref, operation=f"candidate:multihead-{number}",
                lesson_id=f"pg-parent-{number}", body=_body(),
            )
            _publish(first_service, candidate, operation=f"release:multihead-{number}")
        with _as(_actor("reviewer", "governor")):
            heads = tuple(sorted((
                first_service.head_expectation(PROFILE, "pg-parent-1"),
                first_service.head_expectation(PROFILE, "pg-parent-2"),
            ), key=lambda value: value.object_id))
        merged_candidate = _propose(
            first_service, support_ref, operation="candidate:multihead-merge",
            lesson_id="pg-merged", body=_body(steps=(
                "核对当前业务来源和证据范围。", "确认多个来源规则均适用。",
            )),
        )
        revised_candidate = _propose(
            first_service, support_ref, operation="candidate:multihead-revise",
            lesson_id="pg-parent-2", body=_body(steps=(
                "核对当前业务来源和证据范围。", "再次确认解释范围。",
            )),
        )
        peer, _, _, _ = _workspace(postgres_dsn)
        second_service = service(peer)
        barrier = Barrier(2)

        def attempt(selected_service, candidate, operation, action, selected_heads):
            barrier.wait(timeout=10)
            try:
                return "OK", _publish(
                    selected_service, candidate, operation=operation,
                    action=action, heads=selected_heads,
                )
            except (IntegrityError, RuntimeError) as error:
                return "STALE", str(error)

        with ThreadPoolExecutor(max_workers=2) as executor:
            merge = executor.submit(
                attempt, first_service, merged_candidate, "release:multihead-merge",
                "SUPERSEDE", heads,
            )
            revise = executor.submit(
                attempt, second_service, revised_candidate, "release:multihead-revise",
                "REVISE", (heads[1],),
            )
            results = (merge.result(timeout=30), revise.result(timeout=30))
        assert sorted(item[0] for item in results) == ["OK", "STALE"]
        assert "STALE_BASE" in results[0][1] or "STALE_BASE" in results[1][1]

        parent_1 = workspace.store.get_object(heads[0].object_id)
        parent_2 = workspace.store.get_object(heads[1].object_id)
        if results[0][0] == "OK":
            merged = workspace.store.get_object(lesson_object_prefix(PROFILE) + "pg-merged")
            assert merged.payload["parent_refs"] == [
                head.object_id + "@" + head.version for head in heads
            ]
            assert parent_1.payload["status"] == parent_2.payload["status"] == "SUPERSEDED"
        else:
            assert parent_1.payload["status"] == "ADMITTED" and parent_1.version == "r1"
            assert parent_2.payload["status"] == "ADMITTED" and parent_2.version == "r2"
            with pytest.raises(KeyError):
                workspace.store.get_object(lesson_object_prefix(PROFILE) + "pg-merged")
        # The losing command has no durable receipt and an exact retry cannot
        # resurrect a review made against an earlier pointer revision.
        losing = (
            (first_service, merged_candidate, "release:multihead-merge", "SUPERSEDE", heads)
            if results[0][0] == "STALE" else
            (second_service, revised_candidate, "release:multihead-revise", "REVISE", (heads[1],))
        )
        with pytest.raises(IntegrityError, match="STALE_BASE"):
            _publish(losing[0], losing[1], operation=losing[2], action=losing[3], heads=losing[4])
    finally:
        workspace.store.close()
        if peer is not None:
            peer.store.close()
    reopened, _, _, _ = _workspace(postgres_dsn)
    try:
        assert reopened.store.get_object(heads[0].object_id).version == parent_1.version
        assert reopened.store.get_object(heads[1].object_id).version == parent_2.version
    finally:
        reopened.store.close()
