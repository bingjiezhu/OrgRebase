from __future__ import annotations

import pytest

from orgrebase.auth import AuthenticationError
from orgrebase.clock import FrozenClock
from orgrebase.domain import IntegrityError
from orgrebase.workspace.experience_contracts import MemorySnapshot, RecallReceipt, RecallSelectionManifest
from orgrebase.workspace.experience_recall import (
    RECALL_MEDIA,
    SELECTION_MEDIA,
    SNAPSHOT_MEDIA,
    ExperienceRecallService,
)
from tests.workspace.test_experience_lessons import (
    PROFILE,
    _actor,
    _as,
    _body,
    _propose,
    _publish,
    _services,
)


def _published(tmp_path, count=1):
    workspace, lesson, support = _services(tmp_path)
    for number in range(count):
        candidate = _propose(
            lesson, support, operation=f"candidate:{number}",
            lesson_id=f"source-check-{number}", body=_body(),
        )
        _publish(lesson, candidate, operation=f"release:{number}")
    return workspace, lesson


def _recall(workspace, lesson, check=None):
    return ExperienceRecallService(
        workspace, profile_id=PROFILE, assessment_service=lesson.assessment_service,
        source_qualification_check=check or (lambda _item: None),
    )


def test_sparse_recall_keeps_complete_conditions_and_rechecks_at_consumption(tmp_path):
    workspace, lesson = _published(tmp_path)
    try:
        recall = _recall(workspace, lesson)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            snapshot = MemorySnapshot.model_validate(
                workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
            )
            assert snapshot.coverage == "COMPLETE" and len(snapshot.lesson_entries) == 1
            manifest_ref, receipt_ref = recall.select_for_case(
                operation_id="recall:one", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:one",
                context_digest="sha256:" + "1" * 64, case_id="finance:case",
                case_revision="r1", evaluation_arm="SPARSE_RECALL",
                query_text="检查 Finance 来源证据和解释",
                context_tags=("finance:source-review",), object_kinds=(),
                problem_codes=("FINANCE_SOURCE_REVIEW",),
            )
            manifest = RecallSelectionManifest.model_validate(
                workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
            )
            receipt = RecallReceipt.model_validate(
                workspace.store.load_artifact(receipt_ref, RECALL_MEDIA).payload,
            )
            assert len(manifest.selected_lessons) == 1
            assert receipt.claim_boundary == "RETRIEVED_ADVICE_NOT_MODEL_CONSUMPTION"
            frozen, advice = recall.validate_manifest_for_consumption(
                manifest_ref, snapshot_ref=snapshot_ref,
                purpose=PROFILE, recipient="workspace-advisory",
            )
            assert frozen.digest == manifest.digest
            assert "适用条件:" in advice and "不适用条件:" in advice
            assert "停止条件:" in advice and "来源失效时停止使用" in advice
            assert recall.select_for_case(
                operation_id="recall:one", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:one",
                context_digest="sha256:" + "1" * 64, case_id="finance:case",
                case_revision="r1", evaluation_arm="SPARSE_RECALL",
                query_text="检查 Finance 来源证据和解释",
                context_tags=("finance:source-review",), object_kinds=(),
                problem_codes=("FINANCE_SOURCE_REVIEW",),
            ) == (manifest_ref, receipt_ref)
            workspace.clock = FrozenClock("2026-11-01T00:00:00Z")
            with pytest.raises(IntegrityError, match="MEMORY_HOLD"):
                recall.validate_manifest_for_consumption(
                    manifest_ref, snapshot_ref=snapshot_ref,
                    purpose=PROFILE, recipient="workspace-advisory",
                )
    finally:
        workspace.store.close()


def test_apply_executor_can_revalidate_exact_frozen_selection_without_becoming_query_owner(tmp_path):
    workspace, lesson = _published(tmp_path)
    try:
        recall = _recall(workspace, lesson)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            manifest_ref, _ = recall.select_for_case(
                operation_id="recall:apply-bound", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:apply-bound",
                context_digest="sha256:" + "8" * 64,
                case_id="finance:apply-bound", case_revision="r1",
                evaluation_arm="ADOPTED", query_text="Finance 来源证据",
                context_tags=("finance:source-review",), object_kinds=(),
                problem_codes=("FINANCE_SOURCE_REVIEW",),
            )
        manifest = RecallSelectionManifest.model_validate(
            workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
        )
        with _as(_actor("executor", "executor")):
            checked, advice = recall.validate_frozen_manifest_for_approval(
                manifest_ref, snapshot_ref=snapshot_ref, purpose=PROFILE,
                recipient="workspace-advisory", expected_manifest_digest=manifest.digest,
            )
            assert checked.digest == manifest.digest
            assert "停止条件:" in advice
            with pytest.raises(IntegrityError, match="MANIFEST_BINDING_INVALID"):
                recall.validate_frozen_manifest_for_approval(
                    manifest_ref, snapshot_ref=snapshot_ref, purpose=PROFILE,
                    recipient="workspace-advisory",
                    expected_manifest_digest="sha256:" + "0" * 64,
                )
        with _as(_actor("reader", "reader")), pytest.raises(AuthenticationError, match="AUTH_ACTION_DENIED"):
            recall.validate_frozen_manifest_for_approval(
                manifest_ref, snapshot_ref=snapshot_ref, purpose=PROFILE,
                recipient="workspace-advisory", expected_manifest_digest=manifest.digest,
            )
    finally:
        workspace.store.close()


def test_typed_mismatch_does_not_load_lesson_and_partial_scan_is_honest(tmp_path):
    workspace, lesson = _published(tmp_path, count=2)
    try:
        recall = _recall(workspace, lesson)
        with _as(_actor("reader", "reader")):
            partial_ref = recall.build_snapshot(
                purpose=PROFILE, recipient="workspace-advisory", max_scanned=1,
            )
            partial = MemorySnapshot.model_validate(
                workspace.store.load_artifact(partial_ref, SNAPSHOT_MEDIA).payload,
            )
            assert partial.coverage == "PARTIAL_COVERAGE"
            assert partial.scanned_count == 1
            assert len(partial.lesson_entries) == 1
            complete_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            complete = MemorySnapshot.model_validate(
                workspace.store.load_artifact(complete_ref, SNAPSHOT_MEDIA).payload,
            )
            assert complete.coverage == "COMPLETE" and len(complete.lesson_entries) == 2
            manifest_ref, receipt_ref = recall.select_for_case(
                operation_id="recall:wrong-condition", snapshot_ref=complete_ref,
                task_id="task:finance-explanation", attempt_id="advisory:wrong",
                context_digest="sha256:" + "2" * 64, case_id="finance:case",
                case_revision="r1", evaluation_arm="SPARSE_RECALL",
                query_text="来源证据", context_tags=("finance:other",),
                object_kinds=(),
            )
            manifest = RecallSelectionManifest.model_validate(
                workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
            )
            receipt = RecallReceipt.model_validate(
                workspace.store.load_artifact(receipt_ref, RECALL_MEDIA).payload,
            )
            assert manifest.coverage == "COMPLETE" and manifest.selected_lessons == ()
            assert receipt.excluded_counts["TYPED_CONDITION_MISMATCH"] == 2
    finally:
        workspace.store.close()


def test_source_revocation_blocks_old_snapshot_without_replacing_lesson(tmp_path):
    workspace, lesson = _published(tmp_path)
    usable = {"yes": True}

    def current(_item):
        if not usable["yes"]:
            raise IntegrityError("SOURCE_REVOKED")

    try:
        recall = _recall(workspace, lesson, current)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            manifest_ref, receipt_ref = recall.select_for_case(
                operation_id="recall:revoked", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:revoked",
                context_digest="sha256:" + "3" * 64, case_id="finance:case",
                case_revision="r1", evaluation_arm="SPARSE_RECALL",
                query_text="来源证据", context_tags=("finance:source-review",),
                object_kinds=(),
            )
            usable["yes"] = False
            assert recall.select_for_case(
                operation_id="recall:revoked", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:revoked",
                context_digest="sha256:" + "3" * 64, case_id="finance:case",
                case_revision="r1", evaluation_arm="SPARSE_RECALL",
                query_text="来源证据", context_tags=("finance:source-review",),
                object_kinds=(),
            ) == (manifest_ref, receipt_ref)
            with pytest.raises(IntegrityError, match="MEMORY_HOLD"):
                recall.validate_manifest_for_consumption(
                    manifest_ref, snapshot_ref=snapshot_ref,
                    purpose=PROFILE, recipient="workspace-advisory",
                )
            with pytest.raises(IntegrityError, match="MEMORY_HOLD"):
                recall.select_for_case(
                    operation_id="recall:revoked-new", snapshot_ref=snapshot_ref,
                    task_id="task:finance-explanation", attempt_id="advisory:revoked-new",
                    context_digest="sha256:" + "4" * 64, case_id="finance:case",
                    case_revision="r1", evaluation_arm="SPARSE_RECALL",
                    query_text="来源证据", context_tags=("finance:source-review",),
                    object_kinds=(),
                )
    finally:
        workspace.store.close()


def test_more_than_one_page_is_ranked_deterministically_in_chinese_and_english(tmp_path):
    workspace, lesson = _published(tmp_path, count=105)
    try:
        recall = _recall(workspace, lesson)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(
                purpose=PROFILE, recipient="workspace-advisory", page_size=17,
                max_scanned=200,
            )
            snapshot = MemorySnapshot.model_validate(
                workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
            )
            assert snapshot.scanned_count == 105
            assert snapshot.coverage == "COMPLETE"
            for number, query in enumerate(("核对来源证据", "finance source review"), 1):
                manifest_ref, _ = recall.select_for_case(
                    operation_id=f"recall:many:{number}", snapshot_ref=snapshot_ref,
                    task_id="task:finance-explanation", attempt_id=f"advisory:many:{number}",
                    context_digest="sha256:" + str(number) * 64,
                    case_id="finance:many", case_revision="r1",
                    evaluation_arm="SPARSE_RECALL", query_text=query,
                    context_tags=("finance:source-review",), object_kinds=(),
                    problem_codes=("FINANCE_SOURCE_REVIEW",),
                )
                manifest = RecallSelectionManifest.model_validate(
                    workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
                )
                assert 1 <= len(manifest.selected_lessons) <= 3
                assert manifest.coverage == "COMPLETE"
                assert manifest.corpus_stats_digest.startswith("sha256:")
                _, advice = recall.validate_manifest_for_consumption(
                    manifest_ref, snapshot_ref=snapshot_ref,
                    purpose=PROFILE, recipient="workspace-advisory",
                )
                assert "停止条件:" in advice
    finally:
        workspace.store.close()


def test_small_memory_budget_excludes_whole_lesson_without_truncating_stop_condition(tmp_path):
    workspace, lesson = _published(tmp_path)
    try:
        recall = _recall(workspace, lesson)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            manifest_ref, receipt_ref = recall.select_for_case(
                operation_id="recall:small-budget", snapshot_ref=snapshot_ref,
                task_id="task:finance-explanation", attempt_id="advisory:small-budget",
                context_digest="sha256:" + "6" * 64,
                case_id="finance:small-budget", case_revision="r1",
                evaluation_arm="SPARSE_RECALL", query_text="来源证据",
                context_tags=("finance:source-review",), object_kinds=(),
                memory_reserved_bytes=120,
            )
            manifest = RecallSelectionManifest.model_validate(
                workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
            )
            receipt = RecallReceipt.model_validate(
                workspace.store.load_artifact(receipt_ref, RECALL_MEDIA).payload,
            )
            assert manifest.selected_lessons == ()
            assert manifest.advice_byte_count == 0
            assert receipt.excluded_counts["WHOLE_LESSON_BUDGET_EXCLUDED"] == 1
            assert recall.validate_manifest_for_consumption(
                manifest_ref, snapshot_ref=snapshot_ref,
                purpose=PROFILE, recipient="workspace-advisory",
            )[1] == ""
    finally:
        workspace.store.close()


def test_source_qualification_runs_outside_read_only_page_snapshot(tmp_path):
    workspace, lesson = _published(tmp_path)
    try:
        def qualifier(_item):
            assert not getattr(workspace.store, "_snapshot_transaction_read_only", False)

        recall = _recall(workspace, lesson, qualifier)
        with _as(_actor("reader", "reader")):
            snapshot_ref = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
            assert MemorySnapshot.model_validate(
                workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
            ).coverage == "COMPLETE"
    finally:
        workspace.store.close()
