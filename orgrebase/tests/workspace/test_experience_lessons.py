from __future__ import annotations

from contextlib import contextmanager

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService
from orgrebase.workspace.experience_lessons import (
    CANDIDATE_MEDIA,
    ExperienceLessonService,
    LessonBody,
    LessonHeadExpectation,
    lesson_object_prefix,
)
from tests.workspace.test_experience_assessment import _evaluator, _observed_case

PROFILE = "workspace-change-explanation-v1"


def _actor(name, role):
    return Principal(
        issuer="local:test", subject=name, tenant_id="org:test",
        actor_id=f"actor:{name}", roles=frozenset({role}), expires_at=4_102_444_800,
    )


@contextmanager
def _as(principal):
    token = request_principal.set(principal)
    try:
        yield
    finally:
        request_principal.reset(token)


def _body(*, steps=("核对当前业务来源和证据范围。",), stop=("来源失效时停止使用。",)):
    return LessonBody(
        kind="PROCEDURAL_ADVICE", problem_code="FINANCE_SOURCE_REVIEW",
        applicability_tags=("finance:source-review",),
        applicability=("Finance 解释候选存在来源覆盖缺口。",),
        contraindications=("不适用于没有当前来源资格的事实。",),
        steps=steps, stop_conditions=stop,
    )


def _services(tmp_path):
    workspace, case_ref, case = _observed_case(tmp_path)
    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
        rubric_version="finance-v1", source_qualification_check=lambda _case: None,
    )
    with _as(_evaluator()):
        support = assessment.record_assessment(
            operation_id="assess:support", case_ref=case_ref,
            profile_id=PROFILE, verdict="SUPPORT",
            reason_code="RUBRIC_PASS", evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
    lesson = ExperienceLessonService(
        workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
        assessment_service=assessment, current_source_check=lambda _ref: None,
    )
    return workspace, lesson, support


def _propose(lesson, support, *, operation, lesson_id, body):
    with _as(_actor("author", "operator")):
        return lesson.propose(
            operation_id=operation, profile_id=PROFILE, lesson_id=lesson_id,
            body=body, support_assessment_refs=(support,),
        )


def _publish(lesson, candidate_ref, *, operation, action="ADD", heads=()):
    candidate, _ = lesson._candidate_body(candidate_ref)
    with _as(_actor("reviewer", "governor")):
        return lesson.apply_delta(
            operation_id=operation, action=action, candidate_ref=candidate_ref,
            expected_heads=heads, reason_code="EXACT_CONTENT_REVIEWED",
            body_bytes_digest=candidate.body_bytes_digest,
            declassified_exact_content=True, purpose=PROFILE,
            recipients=("workspace-advisory",),
        )


def test_private_candidate_requires_exact_review_before_current_lesson(tmp_path):
    workspace, lesson, support = _services(tmp_path)
    try:
        body = _body()
        candidate_ref = _propose(lesson, support, operation="candidate:one", lesson_id="source-check", body=body)
        candidate = workspace.store.load_artifact(candidate_ref, CANDIDATE_MEDIA).payload
        assert "核对当前业务来源" not in str(candidate)
        with _as(_actor("reader", "reader")), pytest.raises(AuthorizationError, match="ACTOR_DENIED"):
            lesson.review_candidate(candidate_ref)
        with _as(_actor("author", "operator")), pytest.raises(AuthorizationError, match="ACTOR_DENIED"):
            lesson.review_candidate(candidate_ref)
        before = workspace.store.count_records()
        with _as(_actor("reviewer", "governor")):
            preview = lesson.review_candidate(candidate_ref)
        assert preview["body"] == body.model_dump(mode="json")
        assert preview["body_bytes_digest"] == candidate["body_bytes_digest"]
        assert preview["status"] == "REVIEW_ONLY_NOT_PUBLISHED"
        assert workspace.store.count_records() == before
        with pytest.raises(KeyError):
            workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-check")
        with _as(_actor("reviewer", "governor")), pytest.raises(
            IntegrityError, match="CONTENT_PUBLICATION_REQUIRED",
        ):
            lesson.apply_delta(
                operation_id="release:without-content", action="ADD",
                candidate_ref=candidate_ref, expected_heads=(),
                reason_code="EXACT_CONTENT_REVIEWED",
            )
        release = _publish(lesson, candidate_ref, operation="release:one")
        assert _publish(lesson, candidate_ref, operation="release:one") == release
        with _as(_actor("reviewer", "governor")), pytest.raises(
            RuntimeError, match="IDEMPOTENCY_CONFLICT",
        ):
            lesson.apply_delta(
                operation_id="release:one", action="ADD", candidate_ref=candidate_ref,
                expected_heads=(), reason_code="DIFFERENT_REVIEW_REASON",
                body_bytes_digest=candidate["body_bytes_digest"],
                declassified_exact_content=True, purpose=PROFILE,
                recipients=("workspace-advisory",),
            )
        current = workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-check")
        assert current.state is ObjectState.CURRENT
        assert current.payload["status"] == "ADMITTED"
        assert current.payload["body"]["stop_conditions"] == ["来源失效时停止使用。"]
        assert current.payload["support_cluster_ids"]
    finally:
        workspace.store.close()


def test_noop_does_not_create_a_current_lesson(tmp_path):
    workspace, lesson, _ = _services(tmp_path)
    try:
        with _as(_actor("reviewer", "governor")):
            first = lesson.apply_delta(
                operation_id="maintenance:noop", action="NOOP", candidate_ref=None,
                expected_heads=(), reason_code="NO_ACCEPTABLE_CANDIDATE",
            )
            assert lesson.apply_delta(
                operation_id="maintenance:noop", action="NOOP", candidate_ref=None,
                expected_heads=(), reason_code="NO_ACCEPTABLE_CANDIDATE",
            ) == first
        assert workspace.store.current_object_page(
            object_id_prefix=lesson_object_prefix(PROFILE),
        )["items"] == ()
    finally:
        workspace.store.close()


def test_revision_cas_preserves_stop_conditions_and_retraction_stops_current_use(tmp_path):
    workspace, lesson, support = _services(tmp_path)
    try:
        original = _propose(lesson, support, operation="candidate:one", lesson_id="source-check", body=_body())
        _publish(lesson, original, operation="release:one")
        with _as(_actor("reviewer", "governor")):
            old_head = lesson.head_expectation(PROFILE, "source-check")
        unsafe = _propose(
            lesson, support, operation="candidate:unsafe", lesson_id="source-check",
            body=_body(steps=("改写当前事实。",), stop=("检查完毕。",)),
        )
        with pytest.raises(IntegrityError, match="PARENT_SAFETY_LOST"):
            _publish(lesson, unsafe, operation="release:unsafe", action="REVISE", heads=(old_head,))
        improved = _propose(
            lesson, support, operation="candidate:two", lesson_id="source-check",
            body=_body(steps=("核对当前业务来源和证据范围。", "再检查解释是否可操作。")),
        )
        _publish(lesson, improved, operation="release:two", action="REVISE", heads=(old_head,))
        with pytest.raises(IntegrityError, match="STALE_BASE"):
            _publish(lesson, improved, operation="release:stale", action="REVISE", heads=(old_head,))
        current = workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-check")
        assert current.version == "r2" and current.payload["status"] == "ADMITTED"
        with _as(_actor("reviewer", "governor")):
            head = lesson.head_expectation(PROFILE, "source-check")
            lesson.apply_delta(
                operation_id="retract:one", action="RETRACT", candidate_ref=None,
                expected_heads=(head,), reason_code="SOURCE_REVOKED",
            )
        tombstone = workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-check")
        assert tombstone.version == "r3" and tombstone.payload["status"] == "RETRACTED"
        assert "body" not in tombstone.payload
    finally:
        workspace.store.close()


def test_supersede_two_compatible_heads_atomically_and_keep_parent_refs(tmp_path):
    workspace, lesson, support = _services(tmp_path)
    try:
        for number in (1, 2):
            candidate = _propose(
                lesson, support, operation=f"candidate:{number}",
                lesson_id=f"source-check-{number}", body=_body(),
            )
            _publish(lesson, candidate, operation=f"release:{number}")
        with _as(_actor("reviewer", "governor")):
            heads = tuple(sorted((
                lesson.head_expectation(PROFILE, "source-check-1"),
                lesson.head_expectation(PROFILE, "source-check-2"),
            ), key=lambda item: item.object_id))
        consolidated = _propose(
            lesson, support, operation="candidate:merged", lesson_id="source-check-merged",
            body=_body(steps=("核对当前业务来源和证据范围。", "再记录停止条件。")),
        )
        stale = LessonHeadExpectation(
            object_id=heads[1].object_id, version=heads[1].version,
            object_digest=heads[1].object_digest,
            pointer_revision=heads[1].pointer_revision + 1,
        )
        with pytest.raises(IntegrityError, match="STALE_BASE"):
            _publish(
                lesson, consolidated, operation="release:stale-merge",
                action="SUPERSEDE", heads=(heads[0], stale),
            )
        with pytest.raises(KeyError):
            workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-check-merged")
        assert all(workspace.store.get_object(head.object_id).payload["status"] == "ADMITTED" for head in heads)
        _publish(lesson, consolidated, operation="release:merged", action="SUPERSEDE", heads=heads)
        next_head = workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-check-merged")
        assert next_head.payload["parent_refs"] == [head.object_id + "@" + head.version for head in heads]
        for number in (1, 2):
            prior = workspace.store.get_object(lesson_object_prefix(PROFILE) + f"source-check-{number}")
            assert prior.payload["status"] == "SUPERSEDED"
            assert prior.payload["successor_ref"] == next_head.ref
    finally:
        workspace.store.close()
