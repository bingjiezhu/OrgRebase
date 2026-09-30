from __future__ import annotations

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace.experience_contracts import (
    CaseAssessmentReceipt,
    CaseObservationV2,
    MemorySnapshot,
    RecallSelectionManifest,
    empty_memory_snapshot,
    empty_recall_selection_manifest,
    exact_bytes_digest,
)


def _empty():
    snapshot = empty_memory_snapshot(
        tenant_id="org:one", workspace_id="default",
        profile_id="workspace-change-explanation-v1",
        retrieval_version="bm25-v1", compiler_version="advisory-v4",
        budget_version="experience-budget-v1",
    )
    manifest = empty_recall_selection_manifest(
        snapshot, case_id="change:one", case_revision="1",
        evaluation_arm="NO_MEMORY", query_ref="private-query:one",
        query_digest=sha256_digest({"query": "one"}),
    )
    return snapshot, manifest


def test_empty_memory_is_explicit_content_addressed_and_not_partial():
    snapshot, manifest = _empty()
    assert snapshot.coverage == manifest.coverage == "EMPTY"
    assert snapshot.empty_reason == manifest.empty_reason == "NO_MEMORY_BASELINE"
    assert snapshot.coverage_proof_digest is None
    assert snapshot.lesson_entries == manifest.selected_lessons == ()
    assert manifest.snapshot_digest == snapshot.digest
    assert manifest.advice_bytes_digest == exact_bytes_digest(b"")
    assert manifest.advice_byte_count == 0
    assert manifest.memory_reserved_bytes == 4096
    assert MemorySnapshot.model_validate(snapshot.model_dump()).digest == snapshot.digest
    assert RecallSelectionManifest.model_validate(manifest.model_dump()).digest == manifest.digest
    with pytest.raises(ValueError, match="EXPERIENCE_EMPTY_SELECTION_INVALID"):
        RecallSelectionManifest.model_validate({
            **manifest.model_dump(exclude={"digest"}),
            "candidate_count": 1,
        })
    with pytest.raises(ValueError, match="EXPERIENCE_SELECTION_EMPTY_REASON_INVALID"):
        RecallSelectionManifest.model_validate({
            **manifest.model_dump(exclude={"digest"}),
            "coverage": "PARTIAL_COVERAGE",
        })
    partial = RecallSelectionManifest.model_validate({
        **manifest.model_dump(exclude={"digest"}),
        "coverage": "PARTIAL_COVERAGE", "empty_reason": None,
    })
    assert partial.coverage == "PARTIAL_COVERAGE"
    with pytest.raises(ValueError, match="content digest mismatch"):
        RecallSelectionManifest.model_validate({
            **manifest.model_dump(), "case_revision": "different",
        })
    with pytest.raises(ValueError, match="EXPERIENCE_EMPTY_SNAPSHOT_PROOF_REQUIRED"):
        MemorySnapshot.model_validate({
            **snapshot.model_dump(exclude={"digest"}),
            "empty_reason": "QUALIFIED_SET_EMPTY",
        })


def test_observation_and_assessment_keep_business_outcome_separate():
    digest = sha256_digest("source")
    case = CaseObservationV2(
        tenant_id="org:one", workspace_id="default",
        profile_id="workspace-quote-evidence-recovery-v1",
        case_id="quote:one", revision=sha256_digest("revision"),
        independence_cluster_id="cluster:one", cluster_status="PROVEN",
        cluster_evidence_ref="cluster-proof:one",
        origin_event_refs=(digest,), business_event_id="change:one",
        business_event_digest=digest, request_digest=digest,
        execution_outcome="APPLIED",
    )
    assert case.learning_assessment == "UNASSESSED"
    assessment = CaseAssessmentReceipt(
        tenant_id=case.tenant_id, workspace_id=case.workspace_id,
        profile_id="workspace-change-explanation-v1",
        case_ref="case:one", case_digest=case.digest,
        independence_cluster_id=case.independence_cluster_id,
        verdict="DISPUTED", rubric_version="finance-v1",
        reason_code="CONFLICTING_EVIDENCE", evidence_refs=(digest,),
        evaluator_id="actor:independent-evaluator",
        evaluated_at="2026-09-28T00:00:00Z",
        operation_id="assess:one",
    )
    assert assessment.verdict == "DISPUTED"
    assert case.execution_outcome == "APPLIED"
    with pytest.raises(ValueError, match="EXPERIENCE_CLUSTER_PROOF_REQUIRED"):
        CaseObservationV2.model_validate({
            **case.model_dump(exclude={"digest"}), "cluster_evidence_ref": None,
        })
