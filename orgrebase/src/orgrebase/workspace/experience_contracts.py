"""Versioned, content-addressed contracts for governed workspace experience.

These records carry refs and digests, never raw business evidence or unreviewed
lesson text.  A snapshot freezes comparison inputs; it does not grant a future
read of a revoked or expired source.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel

Digest = str
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
MEMORY_SCHEMA = "orgrebase.memory-snapshot.v1"
SELECTION_SCHEMA = "orgrebase.recall-selection-manifest.v1"
RECALL_SCHEMA = "orgrebase.recall-receipt.v1"
INVALIDATION_SCHEMA = "orgrebase.memory-invalidation.v1"
PRIVATE_EPISODE_SCHEMA = "orgrebase.private-quote-episode.v1"
LESSON_REVISION_SCHEMA = "orgrebase.lesson-revision.v1"
CASE_SCHEMA = "orgrebase.case-observation.v2"
ASSESSMENT_SCHEMA = "orgrebase.case-assessment.v1"
CLUSTER_PROOF_SCHEMA = "orgrebase.independence-cluster-proof.v1"
DEFAULT_MEMORY_RESERVATION = 4096


def exact_bytes_digest(content: bytes) -> Digest:
    """Digest the actual UTF-8 content bytes, not their JSON representation."""

    return "sha256:" + hashlib.sha256(content).hexdigest()


class CaseObservationV2(ContentAddressedModel):
    schema_version: Literal["orgrebase.case-observation.v2"] = CASE_SCHEMA
    resolver_version: Literal["quote-recovery-observation.v1"] = "quote-recovery-observation.v1"
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    revision: Digest = Field(pattern=_DIGEST_PATTERN)
    independence_cluster_id: str | None = None
    cluster_status: Literal["PROVEN", "PROVISIONAL", "UNDETERMINED"]
    cluster_evidence_ref: str | None = None
    origin_event_refs: tuple[Digest, ...] = Field(min_length=1)
    business_event_id: str = Field(min_length=1)
    business_event_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    run_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    request_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    resume_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    outcome_artifact_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    execution_outcome: Literal["APPLIED", "REJECTED", "PENDING", "UNKNOWN"]
    learning_assessment: Literal["UNASSESSED"] = "UNASSESSED"
    observation_status: Literal["OBSERVED", "QUARANTINED"] = "OBSERVED"
    reason_code: str | None = None
    private_episode_ref: str | None = None
    private_episode_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_observation(self):
        if self.cluster_status == "PROVEN" and (
            not self.independence_cluster_id or not self.cluster_evidence_ref
        ):
            raise ValueError("EXPERIENCE_CLUSTER_PROOF_REQUIRED")
        if self.cluster_status == "UNDETERMINED" and self.independence_cluster_id is not None:
            raise ValueError("EXPERIENCE_CLUSTER_UNDETERMINED")
        if self.cluster_status == "PROVISIONAL" and not self.independence_cluster_id:
            raise ValueError("EXPERIENCE_PROVISIONAL_CLUSTER_ID_REQUIRED")
        if self.observation_status == "QUARANTINED" and not self.reason_code:
            raise ValueError("EXPERIENCE_QUARANTINE_REASON_REQUIRED")
        if bool(self.private_episode_ref) != bool(self.private_episode_digest):
            raise ValueError("EXPERIENCE_PRIVATE_EPISODE_BINDING_REQUIRED")
        if len(self.origin_event_refs) != len(set(self.origin_event_refs)):
            raise ValueError("EXPERIENCE_EVENT_REFS_DUPLICATED")
        if any(
            len(ref) != 71 or not ref.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in ref[7:])
            for ref in self.origin_event_refs
        ):
            raise ValueError("EXPERIENCE_EVENT_REF_INVALID")
        return self


class PrivateEpisode(ContentAddressedModel):
    """Raw trajectory shape for PrivateRecordStore only, never corpus artifacts."""

    schema_version: Literal["orgrebase.private-quote-episode.v1"] = PRIVATE_EPISODE_SCHEMA
    case_id: str = Field(min_length=1)
    case_revision: Digest = Field(pattern=_DIGEST_PATTERN)
    request: dict[str, Any]
    resume: dict[str, Any] | None
    source_event: dict[str, Any]

    @model_validator(mode="after")
    def validate_episode(self):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(self.source_event.get("event_digest"))) is None:
            raise ValueError("EXPERIENCE_PRIVATE_EVENT_REF_REQUIRED")
        return self


class CaseAssessmentReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.case-assessment.v1"] = ASSESSMENT_SCHEMA
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    case_ref: str = Field(min_length=1)
    case_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    independence_cluster_id: str | None = None
    verdict: Literal["SUPPORT", "COUNTEREXAMPLE", "UNKNOWN", "DISPUTED"]
    rubric_version: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    evidence_refs: tuple[Digest, ...]
    evaluator_id: str = Field(min_length=1)
    evaluated_at: str = Field(min_length=1)
    cluster_proof_ref: str | None = None
    cluster_proof_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    operation_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment(self):
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            raise ValueError("EXPERIENCE_ASSESSMENT_EVIDENCE_SET_INVALID")
        if self.verdict in {"SUPPORT", "COUNTEREXAMPLE"} and (
            not self.independence_cluster_id
            or not self.cluster_proof_ref
            or not self.cluster_proof_digest
            or not self.evidence_refs
        ):
            raise ValueError("EXPERIENCE_ASSESSMENT_PROOF_REQUIRED")
        if bool(self.cluster_proof_ref) != bool(self.cluster_proof_digest):
            raise ValueError("EXPERIENCE_CLUSTER_PROOF_BINDING_REQUIRED")
        return self


class IndependenceClusterProof(ContentAddressedModel):
    schema_version: Literal["orgrebase.independence-cluster-proof.v1"] = CLUSTER_PROOF_SCHEMA
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    case_ref: str = Field(min_length=1)
    case_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    independence_cluster_id: str = Field(min_length=1)
    basis_ref_digests: tuple[Digest, ...] = Field(min_length=1)
    issuer_id: str = Field(min_length=1)
    issued_at: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_basis(self):
        if tuple(sorted(set(self.basis_ref_digests))) != self.basis_ref_digests:
            raise ValueError("EXPERIENCE_CLUSTER_BASIS_NOT_CANONICAL")
        return self


class LessonSnapshotEntry(ContentAddressedModel):
    lesson_ref: str = Field(min_length=1)
    lesson_revision: int = Field(ge=1)
    content_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    qualification_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    cluster_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    dependency_digest: Digest = Field(pattern=_DIGEST_PATTERN)


class LessonRevisionPayload(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-revision.v1"] = LESSON_REVISION_SCHEMA
    status: Literal["ADMITTED", "RETRACTED", "SUPERSEDED"]
    profile_id: str = Field(min_length=1)
    lesson_id: str = Field(min_length=1)
    parent_refs: tuple[str, ...]
    body: dict[str, Any] | None = None
    body_bytes_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    publication_ref: str | None = None
    publication_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    candidate_ref: str | None = None
    assessment_refs: tuple[str, ...] = ()
    support_assessment_refs: tuple[str, ...] = ()
    counter_assessment_refs: tuple[str, ...] = ()
    support_cluster_ids: tuple[str, ...] = ()
    recipients: tuple[str, ...] = ()
    purpose: str | None = None
    successor_ref: str | None = None
    reason_code: str | None = None

    @model_validator(mode="after")
    def validate_revision(self):
        if self.status == "ADMITTED":
            if not all((
                self.body, self.body_bytes_digest, self.publication_ref,
                self.publication_digest, self.candidate_ref,
                self.assessment_refs, self.support_assessment_refs,
                self.support_cluster_ids, self.recipients, self.purpose,
            )):
                raise ValueError("EXPERIENCE_LESSON_ADMISSION_BINDING_REQUIRED")
            if self.successor_ref is not None or self.reason_code is not None:
                raise ValueError("EXPERIENCE_LESSON_ADMITTED_TOMBSTONE_FIELDS")
            if (
                tuple(sorted(set((*self.support_assessment_refs, *self.counter_assessment_refs))))
                != self.assessment_refs
                or tuple(sorted(set(self.support_cluster_ids))) != self.support_cluster_ids
            ):
                raise ValueError("EXPERIENCE_LESSON_EVIDENCE_UNION_INVALID")
        elif self.body is not None or self.body_bytes_digest is not None or self.publication_ref is not None:
            raise ValueError("EXPERIENCE_LESSON_TOMBSTONE_TEXT_FORBIDDEN")
        elif not self.parent_refs or not self.reason_code:
            raise ValueError("EXPERIENCE_LESSON_TOMBSTONE_REASON_REQUIRED")
        return self


class MemorySnapshot(ContentAddressedModel):
    schema_version: Literal["orgrebase.memory-snapshot.v1"] = MEMORY_SCHEMA
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    lesson_entries: tuple[LessonSnapshotEntry, ...]
    coverage: Literal["EMPTY", "COMPLETE", "PARTIAL_COVERAGE"]
    empty_reason: Literal["NO_MEMORY_BASELINE", "QUALIFIED_SET_EMPTY"] | None = None
    coverage_proof_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    retrieval_version: str = Field(min_length=1)
    index_revision: str = Field(default="NONE", min_length=1)
    tokenizer_version: str = Field(default="NONE", min_length=1)
    ranker_version: str = Field(default="NONE", min_length=1)
    compiler_version: str = Field(min_length=1)
    budget_version: str = Field(min_length=1)
    built_at: str = Field(default="NO_MEMORY_BASELINE", min_length=1)
    event_head_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    event_sequence_no: int | None = Field(default=None, ge=0)
    scanned_count: int = Field(default=0, ge=0)
    excluded_counts: dict[str, int] = Field(default_factory=dict)
    scan_end_cursor: str | None = None

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.coverage == "EMPTY":
            if self.lesson_entries or self.empty_reason is None:
                raise ValueError("EXPERIENCE_EMPTY_SNAPSHOT_INVALID")
            if self.empty_reason == "QUALIFIED_SET_EMPTY" and self.coverage_proof_digest is None:
                raise ValueError("EXPERIENCE_EMPTY_SNAPSHOT_PROOF_REQUIRED")
            if self.empty_reason == "NO_MEMORY_BASELINE" and (
                self.scanned_count or self.excluded_counts or self.scan_end_cursor is not None
            ):
                raise ValueError("EXPERIENCE_NO_MEMORY_BASELINE_SCAN_INVALID")
        elif (self.coverage == "COMPLETE" and not self.lesson_entries) or self.empty_reason is not None:
            raise ValueError("EXPERIENCE_SNAPSHOT_COVERAGE_INVALID")
        refs = [entry.lesson_ref for entry in self.lesson_entries]
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise ValueError("EXPERIENCE_SNAPSHOT_ORDER_INVALID")
        if any(value < 0 for value in self.excluded_counts.values()):
            raise ValueError("EXPERIENCE_SNAPSHOT_EXCLUSION_COUNT_INVALID")
        return self


class RecallSelectionManifest(ContentAddressedModel):
    schema_version: Literal["orgrebase.recall-selection-manifest.v1"] = SELECTION_SCHEMA
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    case_revision: str = Field(min_length=1)
    evaluation_arm: str = Field(min_length=1)
    task_id: str | None = None
    attempt_id: str | None = None
    context_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    snapshot_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    snapshot_lesson_count: int = Field(ge=0)
    query_ref: str = Field(min_length=1)
    query_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    query_owner_id: str | None = None
    selected_lessons: tuple[LessonSnapshotEntry, ...]
    advice_bytes_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    advice_byte_count: int = Field(ge=0, le=4096)
    coverage: Literal["EMPTY", "COMPLETE", "PARTIAL_COVERAGE"]
    empty_reason: Literal["NO_MEMORY_BASELINE", "QUALIFIED_SET_EMPTY"] | None = None
    candidate_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    retrieval_version: str = Field(min_length=1)
    index_revision: str = Field(min_length=1)
    tokenizer_version: str = Field(min_length=1)
    ranker_version: str = Field(min_length=1)
    corpus_stats_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    compiler_version: str = Field(min_length=1)
    budget_version: str = Field(min_length=1)
    memory_reserved_bytes: int = Field(ge=0, le=8192)

    @model_validator(mode="after")
    def validate_selection(self):
        if len(self.selected_lessons) > 3:
            raise ValueError("EXPERIENCE_SELECTION_TOP_K_EXCEEDED")
        if self.advice_byte_count > self.memory_reserved_bytes:
            raise ValueError("EXPERIENCE_SELECTION_RESERVATION_EXCEEDED")
        if self.coverage == "EMPTY" and (
            self.snapshot_lesson_count != 0
            or self.selected_lessons
            or self.candidate_count != 0
            or self.eligible_count != 0
            or self.advice_byte_count != 0
            or self.advice_bytes_digest != exact_bytes_digest(b"")
            or self.empty_reason is None
        ):
            raise ValueError("EXPERIENCE_EMPTY_SELECTION_INVALID")
        if self.coverage == "COMPLETE" and self.snapshot_lesson_count == 0:
            raise ValueError("EXPERIENCE_EMPTY_SELECTION_COVERAGE_INVALID")
        if self.coverage != "EMPTY" and self.empty_reason is not None:
            raise ValueError("EXPERIENCE_SELECTION_EMPTY_REASON_INVALID")
        if self.eligible_count > self.candidate_count:
            raise ValueError("EXPERIENCE_SELECTION_COUNTS_INVALID")
        return self


class RecallReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.recall-receipt.v1"] = RECALL_SCHEMA
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    attempt_id: str | None = None
    context_digest: Digest | None = Field(default=None, pattern=_DIGEST_PATTERN)
    case_id: str = Field(min_length=1)
    case_revision: str = Field(min_length=1)
    snapshot_ref: str = Field(min_length=1)
    snapshot_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    selection_ref: str = Field(min_length=1)
    selection_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    selected_lessons: tuple[LessonSnapshotEntry, ...]
    excluded_counts: dict[str, int]
    scanned_count: int = Field(ge=0)
    coverage: Literal["EMPTY", "COMPLETE", "PARTIAL_COVERAGE"]
    advice_bytes_digest: Digest = Field(pattern=_DIGEST_PATTERN)
    advice_byte_count: int = Field(ge=0, le=4096)
    memory_wire_byte_count: int = Field(ge=0, le=8192)
    claim_boundary: Literal["RETRIEVED_ADVICE_NOT_MODEL_CONSUMPTION"] = (
        "RETRIEVED_ADVICE_NOT_MODEL_CONSUMPTION"
    )

    @model_validator(mode="after")
    def validate_counts(self):
        if any(value < 0 for value in self.excluded_counts.values()):
            raise ValueError("EXPERIENCE_RECALL_EXCLUSION_COUNT_INVALID")
        return self


class MemoryInvalidationReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.memory-invalidation.v1"] = INVALIDATION_SCHEMA
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_status: Literal["DELETED", "EXPIRED"]
    affected_case_refs: tuple[str, ...]
    affected_lesson_ids: tuple[str, ...]
    affected_snapshot_refs: tuple[str, ...]
    affected_selection_refs: tuple[str, ...]
    closure_scan_complete: bool
    closure_complete: bool = False
    layer_status: dict[str, Literal["STOPPED", "PURGED", "REBUILT", "NOT_APPLICABLE", "UNKNOWN"]]
    unconfirmed_layers: tuple[str, ...]
    claim_boundary: Literal["SOURCE_DISABLED_DERIVATIVE_PURGE_UNCONFIRMED"] = (
        "SOURCE_DISABLED_DERIVATIVE_PURGE_UNCONFIRMED"
    )
    manager_id: str = Field(min_length=1)
    checked_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_closure(self):
        expected_layers = {"summary", "lesson", "index", "cache", "inflight", "checkpoint"}
        if set(self.layer_status) != expected_layers:
            raise ValueError("EXPERIENCE_INVALIDATION_LAYERS_INCOMPLETE")
        if tuple(sorted(key for key, value in self.layer_status.items() if value == "UNKNOWN")) != self.unconfirmed_layers:
            raise ValueError("EXPERIENCE_INVALIDATION_UNKNOWN_LAYERS_INVALID")
        if self.closure_complete and (
            not self.closure_scan_complete or self.unconfirmed_layers
            or self.layer_status["lesson"] not in {"PURGED", "NOT_APPLICABLE"}
        ):
            raise ValueError("EXPERIENCE_INVALIDATION_COMPLETION_UNPROVEN")
        for refs in (
            self.affected_case_refs, self.affected_lesson_ids,
            self.affected_snapshot_refs, self.affected_selection_refs,
        ):
            if tuple(sorted(set(refs))) != refs:
                raise ValueError("EXPERIENCE_INVALIDATION_LOCATORS_NOT_CANONICAL")
        return self


def empty_memory_snapshot(
    *, tenant_id: str, workspace_id: str, profile_id: str,
    retrieval_version: str, compiler_version: str, budget_version: str,
) -> MemorySnapshot:
    """An explicit qualified zero-item input for the static Finance baseline."""

    return MemorySnapshot(
        tenant_id=tenant_id, workspace_id=workspace_id, profile_id=profile_id,
        lesson_entries=(), coverage="EMPTY", empty_reason="NO_MEMORY_BASELINE",
        retrieval_version=retrieval_version,
        compiler_version=compiler_version, budget_version=budget_version,
    )


def empty_recall_selection_manifest(
    snapshot: MemorySnapshot, *, case_id: str, case_revision: str,
    evaluation_arm: str, query_ref: str, query_digest: Digest,
    task_id: str | None = None, attempt_id: str | None = None,
    context_digest: Digest | None = None,
    memory_reserved_bytes: int = DEFAULT_MEMORY_RESERVATION,
) -> RecallSelectionManifest:
    """Bind a real case/query to a zero-lesson snapshot without implying search."""

    selected = snapshot.revalidated()
    if selected.coverage != "EMPTY" or selected.lesson_entries:
        raise ValueError("EXPERIENCE_EMPTY_SNAPSHOT_REQUIRED")
    return RecallSelectionManifest(
        tenant_id=selected.tenant_id, workspace_id=selected.workspace_id,
        profile_id=selected.profile_id, case_id=case_id,
        case_revision=case_revision, evaluation_arm=evaluation_arm,
        task_id=task_id, attempt_id=attempt_id, context_digest=context_digest,
        snapshot_digest=selected.digest, snapshot_lesson_count=0,
        query_ref=query_ref, query_digest=query_digest, selected_lessons=(),
        advice_bytes_digest=exact_bytes_digest(b""), advice_byte_count=0,
        coverage="EMPTY", empty_reason=selected.empty_reason,
        candidate_count=0, eligible_count=0,
        retrieval_version=selected.retrieval_version, index_revision=selected.index_revision,
        tokenizer_version=selected.tokenizer_version, ranker_version=selected.ranker_version,
        corpus_stats_digest=sha256_digest({"doc_count": 0}),
        compiler_version=selected.compiler_version, budget_version=selected.budget_version,
        memory_reserved_bytes=memory_reserved_bytes,
    )
