from __future__ import annotations

import pytest
from sqlalchemy import update

from orgrebase.auth import request_principal
from orgrebase.database import private_records
from orgrebase.domain import IntegrityError
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import (
    CaseObservationV2,
    MemoryInvalidationReceipt,
    MemorySnapshot,
)
from orgrebase.workspace.experience_invalidation import (
    INVALIDATION_MEDIA,
    ExperienceInvalidationService,
)
from orgrebase.workspace.experience_invalidation_detail import (
    DETAIL_MEDIA,
    closure_detail_ref,
)
from orgrebase.workspace.experience_lessons import (
    CANDIDATE_MEDIA,
    ExperienceLessonService,
    lesson_object_prefix,
)
from orgrebase.workspace.experience_recall import SNAPSHOT_MEDIA, ExperienceRecallService
from orgrebase.workspace.experience_source_hold import SOURCE_HOLD_MEDIA, source_hold_ref
from tests.workspace.test_experience_assessment import _evaluator
from tests.workspace.test_experience_collection import _principal, _workspace
from tests.workspace.test_experience_lessons import PROFILE, _actor, _as, _body, _propose, _publish
from tests.workspace.test_experience_recall import _published


def test_private_source_deletion_stops_recall_and_records_unconfirmed_closure(tmp_path, monkeypatch):
    workspace, lesson = _published(tmp_path)
    try:
        recall = ExperienceRecallService(
            workspace, profile_id=PROFILE,
            assessment_service=lesson.assessment_service,
            source_qualification_check=lambda _item: None,
        )
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            manifest_ref, _ = recall.select_for_case(
                operation_id="recall:before-delete", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:one",
                context_digest="sha256:" + "5" * 64,
                case_id="finance:case", case_revision="r1",
                evaluation_arm="SPARSE_RECALL", query_text="来源证据",
                context_tags=("finance:source-review",), object_kinds=(),
            )
        case_row = workspace.store.list_artifacts(artifact_id_prefix="experience-case:")[0]
        case = CaseObservationV2.model_validate(case_row.payload)
        assert workspace.private_records.erase(
            case.private_episode_ref, actor_id="actor:collector",
        ) is True
        with _as(_actor("reader", "reader")), pytest.raises(IntegrityError, match="MEMORY_HOLD"):
            recall.validate_manifest_for_consumption(
                manifest_ref, snapshot_ref=snapshot_ref,
                purpose=PROFILE, recipient="workspace-advisory",
            )
        invalidation = ExperienceInvalidationService(
            workspace, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _as(_actor("privacy", "administrator")):
            receipt_ref = invalidation.record_source_invalidation(
                operation_id="invalidate:one", source_record_id=case.private_episode_ref,
            )
            assert invalidation.record_source_invalidation(
                operation_id="invalidate:one", source_record_id=case.private_episode_ref,
            ) == receipt_ref
            receipt = MemoryInvalidationReceipt.model_validate(
                workspace.store.load_artifact(receipt_ref, INVALIDATION_MEDIA).payload,
            )
            assert receipt.closure_scan_complete is True
            assert receipt.closure_complete is False
            assert case_row.artifact_id in receipt.affected_case_refs
            assert receipt.affected_lesson_ids
            assert snapshot_ref in receipt.affected_snapshot_refs
            assert manifest_ref in receipt.affected_selection_refs
            assert receipt.layer_status["lesson"] == "STOPPED"
            assert receipt.layer_status["inflight"] == "UNKNOWN"
            assert receipt.layer_status["checkpoint"] == "UNKNOWN"
            assert receipt.claim_boundary == "SOURCE_DISABLED_DERIVATIVE_PURGE_UNCONFIRMED"
            detail = invalidation.read_closure_detail(receipt_ref)
            assert detail.receipt_digest == receipt.digest
            assert detail.affected_candidate_refs
            assert detail.affected_private_candidate_refs
            statuses = {(item.layer, item.action): item.status for item in detail.actions}
            assert statuses[("summary", "STOP_USE")] == "CONFIRMED"
            assert statuses[("summary", "PURGE")] == "PENDING"
            assert statuses[("lesson", "STOP_USE")] == "CONFIRMED"
            assert statuses[("lesson", "PURGE")] == "UNKNOWN"
            assert statuses[("inflight", "STOP_USE")] == "UNKNOWN"
            assert statuses[("checkpoint", "REBUILD")] == "UNKNOWN"
            assert workspace.store.load_artifact(
                closure_detail_ref(receipt.digest), DETAIL_MEDIA,
            ).payload["digest"] == detail.digest
            assert workspace.store.load_artifact(source_hold_ref(
                tenant_id=workspace.store.tenant_id,
                workspace_id=workspace.store.workspace_id,
                source_record_id=case.private_episode_ref,
            ), SOURCE_HOLD_MEDIA).payload["source_record_id"] == case.private_episode_ref
            assert invalidation.verify_after_restore(receipt_ref).digest == receipt.digest
            original_status = workspace.private_records.record_status
            monkeypatch.setattr(
                workspace.private_records, "record_status", lambda _record_id: "AVAILABLE",
            )
            with pytest.raises(IntegrityError, match="RESTORE_DELETION_LEDGER_REQUIRED"):
                invalidation.verify_after_restore(receipt_ref)
            monkeypatch.setattr(workspace.private_records, "record_status", original_status)
    finally:
        workspace.store.close()


def test_closure_scans_all_profiles_and_unpublished_private_candidates(tmp_path):
    workspace, lesson = _published(tmp_path)
    try:
        case_ref = workspace.store.list_artifacts(artifact_id_prefix="experience-case:")[0].artifact_id
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref).payload,
        )
        other_profile = "workspace-operations-advice-v1"
        with _as(_evaluator()):
            support_ref = lesson.assessment_service.record_assessment(
                operation_id="assess:other-profile", case_ref=case_ref,
                profile_id=other_profile, verdict="SUPPORT", reason_code="RUBRIC_PASS",
                evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            )
        with _as(_actor("author", "operator")):
            other_candidate = lesson.propose(
                operation_id="candidate:other-profile", profile_id=other_profile,
                lesson_id="other-profile-lesson", body=_body(),
                support_assessment_refs=(support_ref,),
            )
            unreviewed_candidate = lesson.propose(
                operation_id="candidate:other-unreviewed", profile_id=other_profile,
                lesson_id="other-unreviewed-lesson", body=_body(),
                support_assessment_refs=(support_ref,),
            )
        selected = workspace.store.load_artifact(other_candidate, CANDIDATE_MEDIA).payload
        with _as(_actor("reviewer", "governor")):
            lesson.apply_delta(
                operation_id="release:other-profile", action="ADD",
                candidate_ref=other_candidate, expected_heads=(),
                reason_code="EXACT_CONTENT_REVIEWED",
                body_bytes_digest=selected["body_bytes_digest"],
                declassified_exact_content=True, purpose=other_profile,
                recipients=("workspace-advisory",),
            )
        workspace.private_records.erase(case.private_episode_ref, actor_id="actor:collector")
        invalidation = ExperienceInvalidationService(
            workspace, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _as(_actor("privacy", "administrator")):
            receipt_ref = invalidation.record_source_invalidation(
                operation_id="invalidate:all-profiles", source_record_id=case.private_episode_ref,
            )
            receipt = invalidation.verify_after_restore(receipt_ref)
            detail = invalidation.read_closure_detail(receipt_ref)
        assert lesson_object_prefix(other_profile) + "other-profile-lesson" in receipt.affected_lesson_ids
        assert set((other_candidate, unreviewed_candidate)) <= set(detail.affected_candidate_refs)
        assert set(detail.affected_private_candidate_refs)
        with _as(_actor("reviewer", "governor")), pytest.raises(IntegrityError):
            lesson.review_candidate(unreviewed_candidate)
    finally:
        workspace.store.close()


def test_bounded_incomplete_closure_stays_unknown_until_new_scan(tmp_path):
    workspace, _ = _published(tmp_path, count=3)
    try:
        case = CaseObservationV2.model_validate(
            workspace.store.list_artifacts(artifact_id_prefix="experience-case:")[0].payload,
        )
        workspace.private_records.erase(case.private_episode_ref, actor_id="actor:collector")
        invalidation = ExperienceInvalidationService(
            workspace, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _as(_actor("privacy", "administrator")):
            partial_ref = invalidation.record_source_invalidation(
                operation_id="invalidate:bounded", source_record_id=case.private_episode_ref,
                max_scanned=1,
            )
            partial = invalidation.verify_after_restore(partial_ref)
            partial_detail = invalidation.read_closure_detail(partial_ref)
            assert partial.closure_scan_complete is False
            assert partial.layer_status["summary"] == "UNKNOWN"
            assert partial.layer_status["lesson"] == "UNKNOWN"
            assert all(
                item.status == "UNKNOWN" for item in partial_detail.actions
                if item.action == "STOP_USE" and item.layer in {"summary", "lesson"}
            )
            full_ref = invalidation.record_source_invalidation(
                operation_id="invalidate:rescan", source_record_id=case.private_episode_ref,
            )
            full = invalidation.verify_after_restore(full_ref)
            assert full.closure_scan_complete is True
            assert len(full.affected_lesson_ids) == 3
            assert full.closure_complete is False
            assert invalidation.verify_after_restore(partial_ref).closure_scan_complete is False
    finally:
        workspace.store.close()


def _published_at(path):
    """Deterministic mechanism fixture; SourceBinding positive path has its own test."""

    workspace, event, request, _ = _workspace(path)
    workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
        "event_id": event.event_id, "round": 1,
        "recovery_digest": request["digest"], "actor_id": "owner:product",
    })
    token = request_principal.set(_principal())
    try:
        ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        ).collect(worker_id="worker:restore")
    finally:
        request_principal.reset(token)
    case_ref = workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    )[0].payload["case_ref"]
    case = CaseObservationV2.model_validate(workspace.store.load_artifact(case_ref).payload)
    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
        rubric_version="finance-v1", source_qualification_check=lambda _case: None,
    )
    with _as(_evaluator()):
        support_ref = assessment.record_assessment(
            operation_id="assess:restore", case_ref=case_ref, profile_id=PROFILE,
            verdict="SUPPORT", reason_code="RUBRIC_PASS",
            evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
    lesson = ExperienceLessonService(
        workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
        assessment_service=assessment,
    )
    candidate = _propose(
        lesson, support_ref, operation="candidate:restore",
        lesson_id="restore-source", body=_body(),
    )
    _publish(lesson, candidate, operation="release:restore")
    return workspace, lesson, case


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
def test_partial_backup_restore_requires_deletion_ledger_and_tombstone_blocks_recall(
    backend, request, tmp_path,
):
    path = (
        tmp_path / "experience-restore.sqlite" if backend == "sqlite"
        else request.getfixturevalue("postgres_dsn")
    )
    workspace, lesson, case = _published_at(path)
    try:
        recall = ExperienceRecallService(
            workspace, profile_id=PROFILE, assessment_service=lesson.assessment_service,
        )
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            manifest_ref, _ = recall.select_for_case(
                operation_id="recall:restore", snapshot_ref=snapshot_ref,
                task_id="task:restore", attempt_id="advisory:restore",
                context_digest="sha256:" + "3" * 64,
                case_id="finance:restore", case_revision="r1",
                evaluation_arm="SPARSE_RECALL", query_text="来源证据",
                context_tags=("finance:source-review",), object_kinds=(),
            )
        with workspace.store.read_connection() as connection:
            before = workspace.private_records._row(connection, case.private_episode_ref)
            old_content = before["content_json"]
        assert workspace.private_records.erase(
            case.private_episode_ref, actor_id="actor:collector",
        )
        deletion_ledger = workspace.private_records.deletion_ledger()
        invalidation = ExperienceInvalidationService(
            workspace, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _as(_actor("privacy", "administrator")):
            receipt_ref = invalidation.record_source_invalidation(
                operation_id="invalidate:restore", source_record_id=case.private_episode_ref,
            )
        # Controlled partial restore: stale backup puts source plaintext back
        # while the canonical invalidation artifact survived. No customer
        # backup or external optimizer checkpoint is claimed here.
        with workspace.store.transaction() as connection:
            workspace.store.execute(
                connection,
                update(private_records).where(
                    private_records.c.record_id == case.private_episode_ref,
                ).values(content_json=old_content, deleted_at=None, deletion_reason=None),
            )
        assert workspace.private_records.record_status(case.private_episode_ref) == "AVAILABLE"
    finally:
        workspace.store.close()
    reopened, _, _, _ = _workspace(path)
    try:
        assessment = ExperienceAssessmentService(
            reopened, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1", source_qualification_check=lambda _case: None,
        )
        recall = ExperienceRecallService(
            reopened, profile_id=PROFILE, assessment_service=assessment,
        )
        invalidation = ExperienceInvalidationService(
            reopened, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _as(_actor("privacy", "administrator")), pytest.raises(
            IntegrityError, match="RESTORE_DELETION_LEDGER_REQUIRED",
        ):
            invalidation.verify_after_restore(receipt_ref)
        with _as(_actor("reader", "reader")):
            restored_case_ref = reopened.store.list_artifacts(
                artifact_id_prefix="experience-case:",
            )[0].artifact_id
            assert assessment.assessment_projection_for_reader(
                restored_case_ref, PROFILE,
            )["status"] == "HOLD"
            snapshot_after = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            assert MemorySnapshot.model_validate(
                reopened.store.load_artifact(snapshot_after, SNAPSHOT_MEDIA).payload,
            ).coverage == "EMPTY"
            with pytest.raises(IntegrityError, match="MEMORY_HOLD"):
                recall.validate_manifest_for_consumption(
                    manifest_ref, snapshot_ref=snapshot_ref,
                    purpose=PROFILE, recipient="workspace-advisory",
                )
        reopened_lesson = ExperienceLessonService(
            reopened, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
            assessment_service=assessment,
        )
        candidate_ref = reopened.store.get_object(
            lesson_object_prefix(PROFILE) + "restore-source",
        ).payload["candidate_ref"]
        support_ref = reopened.store.get_object(
            lesson_object_prefix(PROFILE) + "restore-source",
        ).payload["support_assessment_refs"][0]
        with _as(_actor("reviewer", "governor")), pytest.raises(IntegrityError):
            assessment.verify_assessment_for_governed_use(
                support_ref, authorized_actor_id="actor:reviewer",
            )
        with _as(_actor("reviewer", "governor")), pytest.raises(IntegrityError):
            reopened_lesson.review_candidate(candidate_ref)
        assert reopened.private_records.reapply_deletions(deletion_ledger) >= 1
        with _as(_actor("privacy", "administrator")):
            assert invalidation.verify_after_restore(receipt_ref).source_status == "DELETED"
    finally:
        reopened.store.close()


def test_invalidation_finds_historical_snapshot_after_lesson_retraction(tmp_path):
    workspace, lesson = _published(tmp_path)
    try:
        recall = ExperienceRecallService(
            workspace, profile_id=PROFILE, assessment_service=lesson.assessment_service,
        )
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
        with _as(_actor("reviewer", "governor")):
            head = lesson.head_expectation(PROFILE, "source-check-0")
            lesson.apply_delta(
                operation_id="retract:before-delete", action="RETRACT",
                candidate_ref=None, expected_heads=(head,), reason_code="REVIEW_DUE",
            )
        case = CaseObservationV2.model_validate(
            workspace.store.list_artifacts(artifact_id_prefix="experience-case:")[0].payload,
        )
        workspace.private_records.erase(case.private_episode_ref, actor_id="actor:collector")
        invalidation = ExperienceInvalidationService(
            workspace, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _as(_actor("privacy", "administrator")):
            ref = invalidation.record_source_invalidation(
                operation_id="invalidate:historical", source_record_id=case.private_episode_ref,
            )
            receipt = MemoryInvalidationReceipt.model_validate(
                workspace.store.load_artifact(ref, INVALIDATION_MEDIA).payload,
            )
            assert snapshot_ref in receipt.affected_snapshot_refs
            assert head.object_id in receipt.affected_lesson_ids
            assert receipt.layer_status["lesson"] == "STOPPED"
    finally:
        workspace.store.close()
