from __future__ import annotations

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.experience_assessment import (
    ExperienceAssessmentService,
    require_quote_case_current,
)
from orgrebase.workspace.experience_collection import (
    COLLECTION_MEDIA,
    OBSERVATION_MEDIA,
    ExperienceCollector,
)
from orgrebase.workspace.experience_contracts import CaseObservationV2
from tests.workspace.test_experience_collection import _principal, _workspace


def _observed_case(tmp_path):
    workspace, event, request, _ = _workspace(tmp_path / "assessment.sqlite")
    workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
        "event_id": event.event_id, "round": 1,
        "recovery_digest": request["digest"], "actor_id": "owner:product",
    })
    token = request_principal.set(_principal())
    try:
        result = ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        ).collect(worker_id="worker:one")
        assert result.observed == 1
    finally:
        request_principal.reset(token)
    collection = workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    )[0].payload
    case_ref = collection["case_ref"]
    case = CaseObservationV2.model_validate(
        workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
    )
    return workspace, case_ref, case


def _evaluator():
    return Principal(
        issuer="local:test", subject="evaluator-service", tenant_id="org:test",
        actor_id="actor:evaluator", roles=frozenset({"governor"}),
        expires_at=4_102_444_800,
    )


def _corpus():
    return Principal(
        issuer="local:test", subject="corpus-service", tenant_id="org:test",
        actor_id="actor:corpus", roles=frozenset({"governor"}),
        expires_at=4_102_444_800,
    )


def test_independent_receipts_do_not_infer_skill_success_from_business_outcome(tmp_path):
    workspace, case_ref, case = _observed_case(tmp_path)
    try:
        service = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author"),
            rubric_version="finance-explanation-v1",
            source_qualification_check=lambda _case: None,
        )
        token = request_principal.set(_evaluator())
        try:
            assert service.assessment_summary_for_reader(
                case_ref=case_ref, profile_id="workspace-change-explanation-v1",
            )["status"] == "UNASSESSED"
            unknown = service.record_assessment(
                operation_id="assess:unknown", case_ref=case_ref,
                profile_id="workspace-change-explanation-v1", verdict="UNKNOWN",
                reason_code="NOT_EVALUATED", evidence_refs=(),
            )
            _, first, proof = service.read_assessment(unknown)
            assert first.verdict == "UNKNOWN" and proof is None
            assert service.assessment_summary_for_reader(
                case_ref=case_ref, profile_id="workspace-change-explanation-v1",
            )["status"] == "UNKNOWN"
            assert service.assessment_projection_for_reader(
                case_ref, "workspace-change-explanation-v1",
            )["coverage"] == "COMPLETE"
            support = service.record_assessment(
                operation_id="assess:support", case_ref=case_ref,
                profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                reason_code="INDEPENDENT_RUBRIC_PASS",
                evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            )
            assert service.record_assessment(
                operation_id="assess:support", case_ref=case_ref,
                profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                reason_code="INDEPENDENT_RUBRIC_PASS",
                evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            ) == support
            _, assessed, proof = service.read_assessment(support)
            assert assessed.verdict == "SUPPORT" and proof is not None
            assert proof.independence_cluster_id == case.independence_cluster_id
            assert assessed.profile_id == "workspace-change-explanation-v1"
            assert case.learning_assessment == "UNASSESSED"
            with pytest.raises(IntegrityError, match="EVIDENCE_UNBOUND"):
                service.record_assessment(
                    operation_id="assess:forged-digest", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                    reason_code="FORGED_EVIDENCE",
                    evidence_refs=("sha256:" + "0" * 64,),
                    independence_cluster_id=case.independence_cluster_id,
                )
            with pytest.raises(IntegrityError, match="CLUSTER_PROOF_REQUIRED"):
                service.record_assessment(
                    operation_id="assess:split-cluster", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                    reason_code="FORGED_INDEPENDENCE",
                    evidence_refs=(case.business_event_digest,),
                    independence_cluster_id=case.independence_cluster_id + ":split",
                )
            with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
                service.record_assessment(
                    operation_id="assess:support", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1",
                    verdict="COUNTEREXAMPLE", reason_code="CONFLICTING_JUDGMENT",
                    evidence_refs=(case.business_event_digest,),
                    independence_cluster_id=case.independence_cluster_id,
                )
            assert service.assessment_conflicts(case_ref=case_ref,
                profile_id="workspace-change-explanation-v1") == tuple(sorted((unknown, support)))
            assert service.assessment_summary_for_reader(
                case_ref=case_ref, profile_id="workspace-change-explanation-v1",
            ) == {"status": "DISPUTED", "assessment_count": 2}
            partial = service.assessment_projection_for_reader(
                case_ref, "workspace-change-explanation-v1", max_records=1,
            )
            assert partial["status"] == "PARTIAL"
            assert partial["coverage"] == "PARTIAL_COVERAGE"
            assert partial["assessment_count_observed"] == 1
        finally:
            request_principal.reset(token)
        token = request_principal.set(_corpus())
        try:
            with pytest.raises(IntegrityError, match="ASSESSMENT_DISPUTED"):
                service.verify_assessment_for_corpus(
                    support, authorized_corpus_actor_id="actor:corpus",
                )
            with pytest.raises(Exception, match="EVALUATOR_SCOPE_DENIED"):
                service.record_assessment(
                    operation_id="assess:corpus-forbidden", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1", verdict="UNKNOWN",
                    reason_code="NOT_EVALUATED", evidence_refs=(),
                )
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_collector_cannot_sign_assessment_and_missing_source_check_blocks_support(tmp_path):
    workspace, case_ref, case = _observed_case(tmp_path)
    try:
        with pytest.raises(ValueError, match="SEPARATION_REQUIRED"):
            ExperienceAssessmentService(
                workspace, evaluator_actor_id="actor:collector",
                excluded_actor_ids=("actor:collector",), rubric_version="finance-v1",
            )
        service = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector",), rubric_version="finance-v1",
        )
        token = request_principal.set(_principal())
        try:
            with pytest.raises(Exception, match="EVALUATOR_SCOPE_DENIED"):
                service.record_assessment(
                    operation_id="assess:one", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1", verdict="UNKNOWN",
                    reason_code="NOT_EVALUATED", evidence_refs=(),
                )
        finally:
            request_principal.reset(token)
        token = request_principal.set(_evaluator())
        try:
            with pytest.raises(IntegrityError, match="SOURCE_QUALIFICATION_REQUIRED"):
                service.record_assessment(
                    operation_id="assess:support", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                    reason_code="INDEPENDENT_RUBRIC_PASS",
                    evidence_refs=(case.business_event_digest,),
                    independence_cluster_id=case.independence_cluster_id,
                )
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_current_business_source_resolver_blocks_stale_recovery_evidence(tmp_path):
    workspace, _, case = _observed_case(tmp_path)
    try:
        event = workspace.changes.event
        current = VersionedObject.model_validate({
            **event.proposal.model_dump(mode="json", exclude={"digest"}),
            "state": "CURRENT",
        })
        with workspace.store.transaction() as connection:
            workspace.store.create_current_if_absent(connection, current)
        token = request_principal.set(_evaluator())
        try:
            # A source-observation artifact alone cannot replace the current
            # connector binding/coverage and ACL qualification.
            with pytest.raises(IntegrityError, match="CONTROLLED_SOURCE_NOT_CURRENT"):
                require_quote_case_current(workspace, case)
            with workspace.store.transaction() as connection:
                workspace.store.transition_current(connection, current.id, ObjectState.STALE)
            with pytest.raises(IntegrityError, match="SOURCE_NOT_CURRENT"):
                require_quote_case_current(workspace, case)
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_human_source_ref_is_undetermined_and_cannot_sign_positive_assessment(tmp_path):
    workspace, event, request, _ = _workspace(
        tmp_path / "human-source.sqlite", source_backed=False,
    )
    try:
        workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        token = request_principal.set(_principal())
        try:
            ExperienceCollector(
                workspace, collector_actor_id="actor:collector", enabled=True,
            ).collect(worker_id="worker:human")
        finally:
            request_principal.reset(token)
        row = workspace.store.list_artifacts(
            artifact_id_prefix="experience-collection:",
        )[0]
        case_ref = row.payload["case_ref"]
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref).payload,
        )
        assert case.cluster_status == "UNDETERMINED"
        assert case.independence_cluster_id is None
        assessment = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector",), rubric_version="finance-v1",
            source_qualification_check=lambda _case: None,
        )
        token = request_principal.set(_evaluator())
        try:
            with pytest.raises(IntegrityError, match="CLUSTER_PROOF_REQUIRED"):
                assessment.record_assessment(
                    operation_id="assess:human-fake-source", case_ref=case_ref,
                    profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                    reason_code="HUMAN_SOURCE_UNQUALIFIED",
                    evidence_refs=(case.business_event_digest,),
                    independence_cluster_id="cluster:forged",
                )
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_rubric_upgrade_holds_old_support_without_erasing_history(tmp_path):
    workspace, case_ref, case = _observed_case(tmp_path)
    try:
        original = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author"),
            rubric_version="finance-v1", source_qualification_check=lambda _case: None,
        )
        token = request_principal.set(_evaluator())
        try:
            historical = original.record_assessment(
                operation_id="assess:old-rubric", case_ref=case_ref,
                profile_id="workspace-change-explanation-v1", verdict="SUPPORT",
                reason_code="RUBRIC_PASS", evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            )
            assert original.read_assessment(historical)[1].verdict == "SUPPORT"
        finally:
            request_principal.reset(token)
        upgraded = ExperienceAssessmentService(
            workspace, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:corpus"),
            rubric_version="finance-v2", source_qualification_check=lambda _case: None,
        )
        token = request_principal.set(_corpus())
        try:
            with pytest.raises(IntegrityError, match="ASSESSMENT_BINDING_INVALID"):
                upgraded.verify_assessment_for_corpus(
                    historical, authorized_corpus_actor_id="actor:corpus",
                )
        finally:
            request_principal.reset(token)
        token = request_principal.set(_principal())
        try:
            projection = upgraded.assessment_projection_for_reader(
                case_ref, "workspace-change-explanation-v1",
            )
            assert projection["status"] == "HOLD" and projection["coverage"] == "COMPLETE"
            assert upgraded.evidence_current_for_reader(historical) is False
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()
