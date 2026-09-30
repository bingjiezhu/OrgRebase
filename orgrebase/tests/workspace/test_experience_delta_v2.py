"""Exact, private LessonDelta review and atomic V2 decision receipts."""

from __future__ import annotations

import pytest

from orgrebase.auth import request_authorization
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, OBSERVATION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import CaseObservationV2
from orgrebase.workspace.experience_lessons import (
    DELTA_PREPARATION_MEDIA,
    ExperienceLessonService,
    LessonDeltaReceiptV2,
    lesson_object_prefix,
)
from tests.workspace.test_experience_assessment import _evaluator
from tests.workspace.test_experience_collection import _principal, _workspace
from tests.workspace.test_experience_lessons import (
    PROFILE,
    _actor,
    _as,
    _body,
    _propose,
    _publish,
    _services,
)


def _services_at(path):
    workspace, event, request, _ = _workspace(path)
    workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
        "event_id": event.event_id, "round": 1,
        "recovery_digest": request["digest"], "actor_id": "owner:product",
    })
    with _as(_principal()):
        ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        ).collect(worker_id="worker:delta-v2")
    case_ref = workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    )[0].payload["case_ref"]
    case = CaseObservationV2.model_validate(
        workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
    )
    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
        rubric_version="finance-v1", source_qualification_check=lambda _case: None,
    )
    with _as(_evaluator()):
        support = assessment.record_assessment(
            operation_id="assess:delta-v2", case_ref=case_ref, profile_id=PROFILE,
            verdict="SUPPORT", reason_code="RUBRIC_PASS",
            evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
    return workspace, ExperienceLessonService(
        workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
        assessment_service=assessment,
    ), support


def test_v2_exact_add_review_replay_restart_and_legacy_read(tmp_path):
    workspace, lessons, support = _services(tmp_path)
    try:
        candidate = _propose(
            lessons, support, operation="candidate:v2-add",
            lesson_id="v2-add", body=_body(),
        )
        with _as(_actor("author", "operator")):
            preparation_ref = lessons.prepare_delta(
                operation_id="delta:v2-add", action="ADD", candidate_ref=candidate,
                expected_heads=(),
            )
            assert lessons.prepare_delta(
                operation_id="delta:v2-add", action="ADD", candidate_ref=candidate,
                expected_heads=(),
            ) == preparation_ref
            with pytest.raises(Exception, match="ACTOR_DENIED"):
                lessons.review_prepared_delta(preparation_ref)
            with pytest.raises(Exception, match="ACTOR_DENIED"):
                lessons.read_delta_diff("experience-lesson-delta:unissued@v2")
        preparation_payload = workspace.store.load_artifact(
            preparation_ref, DELTA_PREPARATION_MEDIA,
        ).payload
        assert "核对当前业务来源" not in str(preparation_payload)
        different = _propose(
            lessons, support, operation="candidate:v2-different",
            lesson_id="v2-add", body=_body(steps=("改用不同的流程。",)),
        )
        with _as(_actor("author", "operator")), pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
            lessons.prepare_delta(
                operation_id="delta:v2-add", action="ADD", candidate_ref=different,
                expected_heads=(),
            )
        with _as(_actor("reviewer", "governor")):
            preview = lessons.review_prepared_delta(preparation_ref)
            assert preview["exact_diff"]["before"] == []
            assert preview["exact_diff"]["after"]["steps"] == ["核对当前业务来源和证据范围。"]
            assert {item["field"] for item in preview["exact_diff"]["changes"]} >= {
                "applicability", "contraindications", "steps", "stop_conditions",
            }
            receipt_ref = lessons.decide_prepared_delta(
                preparation_ref, expected_diff_bytes_digest=preview["diff_bytes_digest"],
                verdict="APPROVED", reason_code="EXACT_CONTENT_REVIEWED",
                body_bytes_digest=preview["after_body_bytes_digest"],
                declassified_exact_content=True, purpose=PROFILE,
            )
            assert receipt_ref.endswith("@v2")
            assert lessons.decide_prepared_delta(
                preparation_ref, expected_diff_bytes_digest=preview["diff_bytes_digest"],
                verdict="APPROVED", reason_code="EXACT_CONTENT_REVIEWED",
                body_bytes_digest=preview["after_body_bytes_digest"],
                declassified_exact_content=True, purpose=PROFILE,
            ) == receipt_ref
            readback = lessons.read_delta_diff(receipt_ref)
            assert readback["diff_status"] == "EXACT"
            assert readback["exact_diff"] == preview["exact_diff"]
            current = workspace.store.get_object(lesson_object_prefix(PROFILE) + "v2-add")
            assert readback["decision_ref"] in current.source_refs
            with pytest.raises(IntegrityError, match="DECISION_INPUT_CHANGED"):
                lessons.decide_prepared_delta(
                    preparation_ref, expected_diff_bytes_digest="sha256:" + "0" * 64,
                    verdict="APPROVED", reason_code="EXACT_CONTENT_REVIEWED",
                    body_bytes_digest=preview["after_body_bytes_digest"],
                    declassified_exact_content=True, purpose=PROFILE,
                )
        with _as(_actor("author", "operator")):
            assert lessons.prepare_delta(
                operation_id="delta:v2-add", action="ADD", candidate_ref=candidate,
                expected_heads=(),
            ) == preparation_ref
        legacy_candidate = _propose(
            lessons, support, operation="candidate:v1-add",
            lesson_id="v1-add", body=_body(),
        )
        legacy_ref = _publish(lessons, legacy_candidate, operation="release:v1-add")
        with _as(_actor("reviewer", "governor")):
            assert lessons.read_delta_diff(legacy_ref)["diff_status"] == "DIFF_NOT_RECORDED"
    finally:
        workspace.store.close()
    reopened, _, _, _ = _workspace(tmp_path / "assessment.sqlite")
    try:
        assessment = ExperienceAssessmentService(
            reopened, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1", source_qualification_check=lambda _case: None,
        )
        lessons = ExperienceLessonService(
            reopened, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
            assessment_service=assessment,
        )
        with _as(_actor("reviewer", "governor")):
            assert lessons.read_delta_diff(receipt_ref)["exact_diff"] == readback["exact_diff"]
    finally:
        reopened.store.close()


def test_v2_reject_and_noop_do_not_create_heads(tmp_path):
    workspace, lessons, support = _services(tmp_path)
    try:
        candidate = _propose(
            lessons, support, operation="candidate:v2-reject",
            lesson_id="v2-rejected", body=_body(),
        )
        with _as(_actor("author", "operator")):
            rejected_preparation = lessons.prepare_delta(
                operation_id="delta:v2-rejected", action="ADD",
                candidate_ref=candidate, expected_heads=(),
            )
            noop_preparation = lessons.prepare_delta(
                operation_id="delta:v2-noop", action="NOOP",
                candidate_ref=None, expected_heads=(),
            )
        before = workspace.store.current_object_page(
            object_id_prefix="experience-lesson:",
        )["items"]
        with _as(_actor("reviewer", "governor")):
            rejected_review = lessons.review_prepared_delta(rejected_preparation)
            rejected_ref = lessons.decide_prepared_delta(
                rejected_preparation,
                expected_diff_bytes_digest=rejected_review["diff_bytes_digest"],
                verdict="REJECTED", reason_code="INSUFFICIENT_EVIDENCE",
            )
            assert lessons.read_delta_diff(rejected_ref)["status"] == "REJECTED"
            noop_review = lessons.review_prepared_delta(noop_preparation)
            noop_ref = lessons.decide_prepared_delta(
                noop_preparation, expected_diff_bytes_digest=noop_review["diff_bytes_digest"],
                verdict="APPROVED", reason_code="NO_ACCEPTABLE_CANDIDATE",
            )
            assert lessons.read_delta_diff(noop_ref)["status"] == "NOOP"
        assert workspace.store.current_object_page(
            object_id_prefix="experience-lesson:",
        )["items"] == before
        assert LessonDeltaReceiptV2.model_validate(
            workspace.store.load_artifact(noop_ref).payload,
        ).status == "NOOP"
    finally:
        workspace.store.close()


def test_v2_private_diff_rechecks_authorization_after_read(tmp_path, monkeypatch):
    workspace, lessons, support = _services(tmp_path)
    try:
        candidate = _propose(
            lessons, support, operation="candidate:late-revoke",
            lesson_id="late-revoke", body=_body(),
        )
        with _as(_actor("author", "operator")):
            preparation = lessons.prepare_delta(
                operation_id="delta:late-revoke", action="ADD",
                candidate_ref=candidate, expected_heads=(),
            )
        with _as(_actor("reviewer", "governor")):
            reviewed = lessons.review_prepared_delta(preparation)
            delta_ref = lessons.decide_prepared_delta(
                preparation, expected_diff_bytes_digest=reviewed["diff_bytes_digest"],
                verdict="REJECTED", reason_code="INSUFFICIENT_EVIDENCE",
            )
            original = workspace.private_records.read_owned

            def deny_after_private_read(*args, **kwargs):
                result = original(*args, **kwargs)
                active["allowed"] = False
                return result

            for read in (
                lambda: lessons.review_prepared_delta(preparation),
                lambda: lessons.read_delta_diff(delta_ref),
            ):
                active = {"allowed": True}

                def check(state=active):
                    if not state["allowed"]:
                        raise AuthorizationError("TEST_PRIVATE_DIFF_REVOKED")

                token = request_authorization.set(check)
                try:
                    with monkeypatch.context() as scoped:
                        scoped.setattr(workspace.private_records, "read_owned", deny_after_private_read)
                        with pytest.raises(AuthorizationError, match="TEST_PRIVATE_DIFF_REVOKED"):
                            read()
                finally:
                    request_authorization.reset(token)
    finally:
        workspace.store.close()


def test_v2_retract_records_exact_removed_body_without_returning_a_head(tmp_path):
    workspace, lessons, support = _services(tmp_path)
    try:
        candidate = _propose(
            lessons, support, operation="candidate:before-retract",
            lesson_id="v2-retract", body=_body(),
        )
        _publish(lessons, candidate, operation="release:before-retract")
        with _as(_actor("reviewer", "governor")):
            head = lessons.head_expectation(PROFILE, "v2-retract")
        with _as(_actor("author", "operator")):
            preparation_ref = lessons.prepare_delta(
                operation_id="delta:v2-retract", action="RETRACT",
                candidate_ref=None, expected_heads=(head,),
            )
        with _as(_actor("reviewer", "governor")):
            reviewed = lessons.review_prepared_delta(preparation_ref)
            assert reviewed["exact_diff"]["before"][0]["body"]["steps"] == [
                "核对当前业务来源和证据范围。",
            ]
            assert reviewed["exact_diff"]["after"] is None
            receipt_ref = lessons.decide_prepared_delta(
                preparation_ref, expected_diff_bytes_digest=reviewed["diff_bytes_digest"],
                verdict="APPROVED", reason_code="SOURCE_REVOKED",
            )
            readback = lessons.read_delta_diff(receipt_ref)
            assert readback["status"] == "APPLIED" and readback["exact_diff"]["after"] is None
        current = workspace.store.get_object(head.object_id)
        assert current.payload["status"] == "RETRACTED"
        assert "body" not in current.payload
    finally:
        workspace.store.close()


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
def test_v2_revision_multihead_exact_diff_and_one_stale_parent_rolls_back(
    backend, request, tmp_path,
):
    path = tmp_path / "delta-v2.sqlite" if backend == "sqlite" else request.getfixturevalue("postgres_dsn")
    workspace, lessons, support = _services_at(path)
    try:
        for name in ("parent-a", "parent-b", "parent-c", "parent-d"):
            candidate = _propose(
                lessons, support, operation=f"candidate:{name}",
                lesson_id=name, body=_body(),
            )
            _publish(lessons, candidate, operation=f"release:{name}")
        with _as(_actor("reviewer", "governor")):
            head_a = lessons.head_expectation(PROFILE, "parent-a")
        revised = _propose(
            lessons, support, operation="candidate:revised-a", lesson_id="parent-a",
            body=_body(steps=("核对当前业务来源和证据范围。", "再核对解释与当前条件。")),
        )
        with _as(_actor("author", "operator")):
            revise_preparation = lessons.prepare_delta(
                operation_id="delta:revise-a", action="REVISE",
                candidate_ref=revised, expected_heads=(head_a,),
            )
        with _as(_actor("reviewer", "governor")):
            revise_review = lessons.review_prepared_delta(revise_preparation)
            assert revise_review["expected_heads"][0]["object_digest"] == head_a.object_digest
            assert revise_review["exact_diff"]["before"][0]["body"]["steps"] == [
                "核对当前业务来源和证据范围。",
            ]
            revise_ref = lessons.decide_prepared_delta(
                revise_preparation,
                expected_diff_bytes_digest=revise_review["diff_bytes_digest"],
                verdict="APPROVED", reason_code="EXACT_CONTENT_REVIEWED",
                body_bytes_digest=revise_review["after_body_bytes_digest"],
                declassified_exact_content=True, purpose=PROFILE,
            )
            revised_head = lessons.head_expectation(PROFILE, "parent-a")
            head_b = lessons.head_expectation(PROFILE, "parent-b")
            head_c = lessons.head_expectation(PROFILE, "parent-c")
            head_d = lessons.head_expectation(PROFILE, "parent-d")
        assert revised_head.version == "r2"
        merge = _propose(
            lessons, support, operation="candidate:merge-ab", lesson_id="merged-ab",
            body=_body(steps=("核对当前业务来源和证据范围。", "再核对解释与当前条件。")),
        )
        with _as(_actor("author", "operator")):
            merge_preparation = lessons.prepare_delta(
                operation_id="delta:merge-ab", action="SUPERSEDE", candidate_ref=merge,
                expected_heads=(revised_head, head_b),
            )
        with _as(_actor("reviewer", "governor")):
            merge_review = lessons.review_prepared_delta(merge_preparation)
            assert len(merge_review["exact_diff"]["before"]) == 2
            merge_ref = lessons.decide_prepared_delta(
                merge_preparation, expected_diff_bytes_digest=merge_review["diff_bytes_digest"],
                verdict="APPROVED", reason_code="EXACT_CONTENT_REVIEWED",
                body_bytes_digest=merge_review["after_body_bytes_digest"],
                declassified_exact_content=True, purpose=PROFILE,
            )
            assert lessons.read_delta_diff(merge_ref)["diff_status"] == "EXACT"
        merged = workspace.store.get_object(revised_head.object_id.rsplit(":", 1)[0] + ":merged-ab")
        assert merged.payload["parent_refs"] == [
            revised_head.object_id + "@" + revised_head.version,
            head_b.object_id + "@" + head_b.version,
        ]
        stale_merge = _propose(
            lessons, support, operation="candidate:merge-cd", lesson_id="merged-cd",
            body=_body(),
        )
        with _as(_actor("author", "operator")):
            stale_preparation = lessons.prepare_delta(
                operation_id="delta:merge-cd", action="SUPERSEDE",
                candidate_ref=stale_merge, expected_heads=(head_c, head_d),
            )
        with _as(_actor("reviewer", "governor")):
            stale_review = lessons.review_prepared_delta(stale_preparation)
        advanced = _propose(
            lessons, support, operation="candidate:advance-d", lesson_id="parent-d",
            body=_body(steps=("核对当前业务来源和证据范围。", "先行复核条件。")),
        )
        _publish(lessons, advanced, operation="release:advance-d", action="REVISE", heads=(head_d,))
        before = workspace.store.get_object(head_c.object_id)
        with _as(_actor("reviewer", "governor")), pytest.raises(IntegrityError, match="STALE_BASE"):
            lessons.decide_prepared_delta(
                stale_preparation, expected_diff_bytes_digest=stale_review["diff_bytes_digest"],
                verdict="APPROVED", reason_code="EXACT_CONTENT_REVIEWED",
                body_bytes_digest=stale_review["after_body_bytes_digest"],
                declassified_exact_content=True, purpose=PROFILE,
            )
        assert workspace.store.get_object(head_c.object_id) == before
    finally:
        workspace.store.close()
    reopened, _, _, _ = _workspace(path)
    try:
        assessment = ExperienceAssessmentService(
            reopened, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
            rubric_version="finance-v1", source_qualification_check=lambda _case: None,
        )
        readback = ExperienceLessonService(
            reopened, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
            assessment_service=assessment,
        )
        with _as(_actor("reviewer", "governor")):
            assert readback.read_delta_diff(revise_ref)["diff_status"] == "EXACT"
            assert len(readback.read_delta_diff(merge_ref)["exact_diff"]["before"]) == 2
    finally:
        reopened.store.close()
