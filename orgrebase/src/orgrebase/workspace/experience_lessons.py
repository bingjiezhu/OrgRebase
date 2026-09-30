"""Reviewed procedural lessons on the existing versioned-object authority.

Unreviewed text is private.  A distinct governor admits exact reviewed bytes
through the store's current-pointer CAS; proposals have no recall authority.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Literal

from pydantic import Field, model_validator

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.clock import timestamp, utc_datetime
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    ContentAddressedModel,
    IntegrityError,
    ObjectState,
    VersionedObject,
)
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_assessment import (
    REVISION_SELECTION_MEDIA,
    ExperienceAssessmentService,
)
from orgrebase.workspace.experience_contracts import LessonRevisionPayload, exact_bytes_digest
from orgrebase.workspace.experience_source_hold import require_source_not_held

CANDIDATE_MEDIA = "application/vnd.orgrebase.experience-lesson-candidate+json"
DELTA_MEDIA = "application/vnd.orgrebase.experience-lesson-delta+json"
DELTA_PREPARATION_MEDIA = "application/vnd.orgrebase.experience-lesson-delta-preparation.v2+json"
DELTA_DECISION_MEDIA = "application/vnd.orgrebase.experience-lesson-delta-decision.v2+json"
DELTA_V2_MEDIA = "application/vnd.orgrebase.experience-lesson-delta.v2+json"
PUBLICATION_MEDIA = "application/vnd.orgrebase.experience-content-publication+json"
_LESSON_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"
_OPERATION_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"


class LessonBody(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-body.v1"] = "orgrebase.lesson-body.v1"
    kind: Literal["PROCEDURAL_ADVICE", "FAILURE_WARNING"]
    problem_code: str = Field(min_length=3, max_length=96, pattern=r"^[A-Z][A-Z0-9_]+$")
    applicability_tags: tuple[str, ...] = Field(min_length=1, max_length=8)
    contraindication_tags: tuple[str, ...] = ()
    dependency_object_kinds: tuple[str, ...] = ()
    applicability: tuple[str, ...] = Field(min_length=1, max_length=8)
    contraindications: tuple[str, ...] = Field(min_length=1, max_length=8)
    steps: tuple[str, ...] = Field(min_length=1, max_length=8)
    stop_conditions: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_body(self):
        for section in (self.applicability, self.contraindications, self.steps, self.stop_conditions):
            if any(not 1 <= len(value.strip()) <= 400 for value in section):
                raise ValueError("EXPERIENCE_LESSON_TEXT_INVALID")
            if len(section) != len(set(section)):
                raise ValueError("EXPERIENCE_LESSON_TEXT_DUPLICATED")
        for section in (
            self.applicability_tags, self.contraindication_tags, self.dependency_object_kinds,
        ):
            if len(section) != len(set(section)) or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value) for value in section
            ):
                raise ValueError("EXPERIENCE_LESSON_TAG_INVALID")
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > 3072:
            raise ValueError("EXPERIENCE_LESSON_BODY_TOO_LARGE")
        return self


class LessonCandidate(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-candidate.v1"] = "orgrebase.lesson-candidate.v1"
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    lesson_id: str = Field(pattern=_LESSON_ID)
    author_id: str = Field(min_length=1)
    private_body_ref: str = Field(min_length=1)
    body_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    body_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    support_assessment_refs: tuple[str, ...] = Field(min_length=1)
    counter_assessment_refs: tuple[str, ...] = ()
    created_at: str = Field(min_length=1)
    operation_id: str = Field(pattern=_OPERATION_ID)

    @model_validator(mode="after")
    def validate_evidence(self):
        if (
            tuple(sorted(set(self.support_assessment_refs))) != self.support_assessment_refs
            or tuple(sorted(set(self.counter_assessment_refs))) != self.counter_assessment_refs
            or set(self.support_assessment_refs) & set(self.counter_assessment_refs)
        ):
            raise ValueError("EXPERIENCE_LESSON_EVIDENCE_SET_INVALID")
        return self


class LessonHeadExpectation(ContentAddressedModel):
    object_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    object_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pointer_revision: int = Field(ge=1)


class ContentPublicationDecision(ContentAddressedModel):
    schema_version: Literal["orgrebase.experience-content-publication.v1"] = (
        "orgrebase.experience-content-publication.v1"
    )
    candidate_ref: str = Field(min_length=1)
    body_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    declassified_exact_content: Literal[True]
    purpose: str = Field(min_length=1)
    recipients: tuple[str, ...] = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    decided_at: str = Field(min_length=1)


class LessonDeltaReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-delta.v1"] = "orgrebase.lesson-delta.v1"
    operation_id: str = Field(pattern=_OPERATION_ID)
    action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"]
    candidate_ref: str | None
    prior_head_refs: tuple[str, ...]
    current_head_ref: str | None
    publication_ref: str | None
    reviewer_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    decided_at: str = Field(min_length=1)


class LessonDeltaPreparationV2(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-delta-preparation.v2"] = (
        "orgrebase.lesson-delta-preparation.v2"
    )
    operation_id: str = Field(pattern=_OPERATION_ID)
    action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"]
    candidate_ref: str | None
    expected_heads: tuple[LessonHeadExpectation, ...] = Field(max_length=5)
    before_body_bytes_digests: tuple[str | None, ...]
    after_body_bytes_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    diff_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    private_diff_ref: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_preparation(self):
        if (
            len(self.before_body_bytes_digests) != len(self.expected_heads)
            or tuple(sorted(item.object_id for item in self.expected_heads))
            != tuple(item.object_id for item in self.expected_heads)
            or (self.action in {"ADD", "REVISE", "SUPERSEDE"}) != (self.candidate_ref is not None)
            or (self.candidate_ref is not None) != (self.after_body_bytes_digest is not None)
            or any(value is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                   for value in self.before_body_bytes_digests)
        ):
            raise ValueError("EXPERIENCE_DELTA_V2_HEAD_SET_INVALID")
        return self


class LessonDeltaDecisionV2(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-delta-decision.v2"] = (
        "orgrebase.lesson-delta-decision.v2"
    )
    preparation_ref: str = Field(min_length=1)
    preparation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    diff_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    verdict: Literal["APPROVED", "REJECTED"]
    reviewer_id: str = Field(min_length=1)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    body_bytes_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    purpose: str = Field(min_length=1)
    recipients: tuple[str, ...] = Field(min_length=1)
    decided_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.verdict == "REJECTED" and self.body_bytes_digest is not None:
            raise ValueError("EXPERIENCE_DELTA_V2_REJECTION_CONTENT_INVALID")
        return self


class LessonDeltaReceiptV2(ContentAddressedModel):
    schema_version: Literal["orgrebase.lesson-delta.v2"] = "orgrebase.lesson-delta.v2"
    operation_id: str = Field(pattern=_OPERATION_ID)
    action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"]
    status: Literal["APPLIED", "NOOP", "REJECTED"]
    preparation_ref: str = Field(min_length=1)
    preparation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_ref: str = Field(min_length=1)
    decision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_heads: tuple[LessonHeadExpectation, ...] = Field(max_length=5)
    before_body_bytes_digests: tuple[str | None, ...]
    after_body_bytes_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    diff_bytes_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    private_diff_ref: str = Field(min_length=1)
    candidate_ref: str | None
    prior_head_refs: tuple[str, ...]
    current_head_ref: str | None
    publication_ref: str | None
    reviewer_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    decided_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_receipt(self):
        expected_refs = tuple(item.object_id + "@" + item.version for item in self.expected_heads)
        if (
            len(self.before_body_bytes_digests) != len(self.expected_heads)
            or self.prior_head_refs != expected_refs
            or any(value is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                   for value in self.before_body_bytes_digests)
            or (self.status == "NOOP" and (self.action != "NOOP" or self.current_head_ref is not None))
            or (self.status == "REJECTED" and self.current_head_ref is not None)
            or (self.status == "APPLIED" and self.action == "NOOP")
        ):
            raise ValueError("EXPERIENCE_DELTA_V2_RECEIPT_BINDING_INVALID")
        return self


def lesson_object_prefix(profile_id: str) -> str:
    return "experience-lesson:" + sha256_digest(profile_id)[7:23] + ":"


class ExperienceLessonService:
    def __init__(
        self, workspace: Any, *, author_actor_id: str, reviewer_actor_id: str,
        assessment_service: ExperienceAssessmentService,
        current_source_check: Callable[[str], None] | None = None,
    ) -> None:
        if not author_actor_id or not reviewer_actor_id or author_actor_id == reviewer_actor_id:
            raise ValueError("EXPERIENCE_LESSON_AUTHORITY_SEPARATION_REQUIRED")
        self.workspace = workspace
        self.author_actor_id = author_actor_id
        self.reviewer_actor_id = reviewer_actor_id
        self.assessment_service = assessment_service
        self.current_source_check = current_source_check

    def _authorize(self, actor_id: str, action: str) -> None:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("EXPERIENCE_LESSON_PRINCIPAL_REQUIRED")
        if principal.actor_id != actor_id or principal.tenant_id != self.workspace.store.tenant_id:
            raise AuthorizationError("EXPERIENCE_LESSON_ACTOR_DENIED")
        require_action(self.workspace, action)

    @staticmethod
    def _candidate_ref(candidate: LessonCandidate) -> str:
        return "experience-lesson-candidate:" + candidate.digest[7:] + "@v1"

    def propose(
        self, *, operation_id: str, profile_id: str, lesson_id: str,
        body: LessonBody, support_assessment_refs: tuple[str, ...],
        counter_assessment_refs: tuple[str, ...] = (),
    ) -> str:
        self._authorize(self.author_actor_id, "propose")
        if not re.fullmatch(_OPERATION_ID, operation_id) or not re.fullmatch(_LESSON_ID, lesson_id):
            raise ValueError("EXPERIENCE_LESSON_ID_INVALID")
        if not profile_id or not support_assessment_refs:
            raise ValueError("EXPERIENCE_LESSON_SCOPE_AND_SUPPORT_REQUIRED")
        if len(set(support_assessment_refs)) != len(support_assessment_refs) or len(set(counter_assessment_refs)) != len(counter_assessment_refs):
            raise ValueError("EXPERIENCE_LESSON_EVIDENCE_DUPLICATED")
        selected = LessonBody.model_validate(body.model_dump(mode="json")).revalidated()
        body_bytes = canonical_json(selected.model_dump(mode="json")).encode("utf-8")
        command = {
            "operation_id": operation_id, "profile_id": profile_id,
            "lesson_id": lesson_id, "body_digest": selected.digest,
            "support_assessment_refs": support_assessment_refs,
            "counter_assessment_refs": counter_assessment_refs,
            "author_id": self.author_actor_id,
        }
        operation_key = "experience-lesson-propose:" + sha256_digest({
            "profile_id": profile_id, "operation_id": operation_id,
        })[7:]
        with self.workspace.store.transaction() as connection:
            self._authorize(self.author_actor_id, "propose")
            self.workspace.store.require_before_commit(
                connection, lambda: self._authorize(self.author_actor_id, "propose"),
            )
            prior = self.workspace.store.get_idempotent(
                operation_key, sha256_digest(command), connection=connection,
            )
            if prior is not None:
                return prior["candidate_ref"]
            private_ref = "private-lesson-candidate:" + sha256_digest(command)[7:] + "@v1"
            self.workspace.private_records.write(
                connection, record_id=private_ref,
                scope_ref=f"experience-lesson:{profile_id}:{lesson_id}",
                owner_id=self.author_actor_id, payload={"body": selected.model_dump(mode="json")},
            )
            candidate = LessonCandidate(
                tenant_id=self.workspace.store.tenant_id,
                workspace_id=self.workspace.store.workspace_id,
                profile_id=profile_id, lesson_id=lesson_id,
                author_id=self.author_actor_id, private_body_ref=private_ref,
                body_digest=selected.digest,
                body_bytes_digest=exact_bytes_digest(body_bytes),
                support_assessment_refs=tuple(sorted(support_assessment_refs)),
                counter_assessment_refs=tuple(sorted(counter_assessment_refs)),
                created_at=self.workspace.clock.now(), operation_id=operation_id,
            )
            candidate_ref = self._candidate_ref(candidate)
            self.workspace.store.save_artifact(
                connection, candidate_ref, CANDIDATE_MEDIA, candidate.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, sha256_digest(command), {"candidate_ref": candidate_ref},
            )
            self.workspace.store.append_event(connection, "EXPERIENCE_LESSON_PROPOSED", {
                "candidate_ref": candidate_ref,
                "candidate_digest": candidate.digest,
                "profile_id": profile_id, "author_id": self.author_actor_id,
            })
        return candidate_ref

    def _candidate_body(self, candidate_ref: str) -> tuple[LessonCandidate, LessonBody]:
        candidate = LessonCandidate.model_validate(
            self.workspace.store.load_artifact(candidate_ref, CANDIDATE_MEDIA).payload,
        ).revalidated()
        if (
            candidate_ref != self._candidate_ref(candidate)
            or candidate.tenant_id != self.workspace.store.tenant_id
            or candidate.workspace_id != self.workspace.store.workspace_id
            or candidate.author_id != self.author_actor_id
        ):
            raise IntegrityError("EXPERIENCE_LESSON_CANDIDATE_BINDING_INVALID")
        private = self.workspace.private_records.read_owned(
            candidate.private_body_ref, owner_id=candidate.author_id,
            scope_ref=f"experience-lesson:{candidate.profile_id}:{candidate.lesson_id}",
        )
        if private is None:
            raise IntegrityError("EXPERIENCE_LESSON_PRIVATE_BODY_UNAVAILABLE")
        body = LessonBody.model_validate(private["body"]).revalidated()
        if (
            body.digest != candidate.body_digest
            or exact_bytes_digest(canonical_json(body.model_dump(mode="json")).encode("utf-8"))
            != candidate.body_bytes_digest
        ):
            raise IntegrityError("EXPERIENCE_LESSON_BODY_BINDING_INVALID")
        return candidate, body

    def review_candidate(self, candidate_ref: str) -> dict[str, Any]:
        """Exact private bytes for the designated current reviewer only.

        This is an authorized read for a publication decision.  It creates no
        canonical artifact and does not itself declassify or admit the text.
        """

        self._authorize(self.reviewer_actor_id, "govern")
        candidate, body = self._candidate_body(candidate_ref)
        clusters, evidence, repair_required, selections = self._evidence(candidate)
        self._authorize(self.reviewer_actor_id, "govern")
        return {
            "schema_version": "orgrebase.lesson-review-preview.v1",
            "candidate_ref": candidate_ref,
            "candidate_digest": candidate.digest,
            "profile_id": candidate.profile_id,
            "lesson_id": candidate.lesson_id,
            "body": body.model_dump(mode="json"),
            "body_bytes_digest": candidate.body_bytes_digest,
            "support_assessment_refs": list(candidate.support_assessment_refs),
            "counter_assessment_refs": list(candidate.counter_assessment_refs),
            "qualified_cluster_ids": list(clusters),
            "evidence_refs": list(evidence),
            "revision_selection_policy": "latest-observed-case-and-independent-repair.v1",
            "revision_selection_digests": [item["digest"] for item in selections],
            "historical_counter_refs": sorted({
                ref for item in selections for ref in item["historical_counter_refs"]
            }),
            "repair_review_required": repair_required,
            "status": "REVIEW_ONLY_NOT_PUBLISHED",
        }

    def _evidence(
        self, candidate: LessonCandidate,
    ) -> tuple[tuple[str, ...], tuple[str, ...], bool, tuple[dict[str, Any], ...]]:
        clusters = set()
        basis_owner: dict[str, str] = {}
        selections = []
        required_historical_counters = set()
        for ref in candidate.support_assessment_refs:
            case, receipt, proof = self.assessment_service.verify_assessment_for_governed_use(
                ref, authorized_actor_id=self.reviewer_actor_id,
            )
            require_source_not_held(self.workspace, case.private_episode_ref)
            if receipt.profile_id != candidate.profile_id or receipt.verdict != "SUPPORT" or proof is None:
                raise IntegrityError("EXPERIENCE_LESSON_SUPPORT_NOT_QUALIFIED")
            if self.current_source_check is not None:
                self.current_source_check(ref)
            selection = self.assessment_service.revision_selection_for_governed_use(
                ref, authorized_actor_id=self.reviewer_actor_id,
            )
            selections.append(selection)
            required_historical_counters.update(selection["historical_counter_refs"])
            clusters.add(proof.independence_cluster_id)
            for basis in proof.basis_ref_digests:
                previous = basis_owner.setdefault(basis, proof.independence_cluster_id)
                if previous != proof.independence_cluster_id:
                    raise IntegrityError("EXPERIENCE_CLUSTER_BASIS_OVERLAP")
        if not required_historical_counters <= set(candidate.counter_assessment_refs):
            raise IntegrityError("EXPERIENCE_REVISION_COUNTER_HISTORY_REQUIRED")
        for ref in candidate.counter_assessment_refs:
            case, receipt, proof = self.assessment_service.verify_historical_counter_for_governed_use(
                ref, authorized_actor_id=self.reviewer_actor_id,
            )
            require_source_not_held(self.workspace, case.private_episode_ref)
            if receipt.profile_id != candidate.profile_id or receipt.verdict != "COUNTEREXAMPLE" or proof is None:
                raise IntegrityError("EXPERIENCE_LESSON_COUNTER_NOT_QUALIFIED")
            if self.current_source_check is not None:
                self.current_source_check(ref)
            if ref not in required_historical_counters:
                self.assessment_service.revision_selection_for_governed_use(
                    ref, authorized_actor_id=self.reviewer_actor_id,
                )
        return tuple(sorted(clusters)), tuple(sorted(
            set(candidate.support_assessment_refs + candidate.counter_assessment_refs)
        )), bool(required_historical_counters), tuple(selections)

    def head_expectation(self, profile_id: str, lesson_id: str) -> LessonHeadExpectation:
        self._authorize(self.reviewer_actor_id, "govern")
        pointer = self.workspace.store.get_pointer(lesson_object_prefix(profile_id) + lesson_id)
        return LessonHeadExpectation(
            object_id=pointer["object_id"], version=pointer["version"],
            object_digest=pointer["payload_digest"], pointer_revision=pointer["revision"],
        )

    def _current(self, expected: LessonHeadExpectation) -> VersionedObject:
        pointer = self.workspace.store.get_pointer(expected.object_id)
        if (
            pointer["version"] != expected.version
            or pointer["payload_digest"] != expected.object_digest
            or pointer["revision"] != expected.pointer_revision
        ):
            raise IntegrityError("EXPERIENCE_LESSON_STALE_BASE")
        item = self.workspace.store.get_object(expected.object_id)
        if item.digest != expected.object_digest or item.kind != "ExperienceLesson" or item.state is not ObjectState.CURRENT:
            raise IntegrityError("EXPERIENCE_LESSON_HEAD_INVALID")
        return item

    @staticmethod
    def _exact_diff(
        action: str, candidate_ref: str | None,
        current: tuple[VersionedObject, ...], body: LessonBody | None,
    ) -> dict[str, Any]:
        after = body.model_dump(mode="json") if body is not None else None
        before = tuple({
            "head_ref": item.ref,
            "body_bytes_digest": item.payload.get("body_bytes_digest"),
            "body": item.payload.get("body"),
        } for item in current)
        changes = []
        for prior in before or ({"head_ref": None, "body": None},):
            old_body = prior["body"] or {}
            new_body = after or {}
            for field in sorted(set(old_body) | set(new_body)):
                if old_body.get(field) != new_body.get(field):
                    changes.append({
                        "head_ref": prior["head_ref"], "field": field,
                        "before": old_body.get(field), "after": new_body.get(field),
                    })
        return {
            "schema_version": "orgrebase.lesson-delta-exact-diff.v2",
            "action": action, "candidate_ref": candidate_ref,
            "before": before, "after": after, "changes": changes,
        }

    @staticmethod
    def _diff_digest(diff: dict[str, Any]) -> str:
        return exact_bytes_digest(canonical_json(diff).encode("utf-8"))

    @staticmethod
    def _preparation_ref(preparation: LessonDeltaPreparationV2) -> str:
        return "experience-lesson-delta-preparation:" + preparation.digest[7:] + "@v2"

    def _prepared_context(
        self, preparation_ref: str,
    ) -> tuple[LessonDeltaPreparationV2, dict[str, Any], LessonCandidate | None, LessonBody | None]:
        preparation = LessonDeltaPreparationV2.model_validate(
            self.workspace.store.load_artifact(preparation_ref, DELTA_PREPARATION_MEDIA).payload,
        ).revalidated()
        if preparation_ref != self._preparation_ref(preparation) or preparation.author_id != self.author_actor_id:
            raise IntegrityError("EXPERIENCE_DELTA_V2_PREPARATION_BINDING_INVALID")
        private = self.workspace.private_records.read_owned(
            preparation.private_diff_ref, owner_id=self.reviewer_actor_id,
            scope_ref=preparation_ref,
        )
        if private is None or not isinstance(private.get("diff"), dict):
            raise IntegrityError("EXPERIENCE_DELTA_V2_PRIVATE_DIFF_UNAVAILABLE")
        diff = private["diff"]
        if self._diff_digest(diff) != preparation.diff_bytes_digest:
            raise IntegrityError("EXPERIENCE_DELTA_V2_DIFF_CHANGED")
        current = tuple(self._current(head) for head in preparation.expected_heads)
        if tuple(item.payload.get("body_bytes_digest") for item in current) != preparation.before_body_bytes_digests:
            raise IntegrityError("EXPERIENCE_DELTA_V2_PARENT_BODY_CHANGED")
        if preparation.candidate_ref is not None:
            candidate, body = self._candidate_body(preparation.candidate_ref)
            if candidate.body_bytes_digest != preparation.after_body_bytes_digest:
                raise IntegrityError("EXPERIENCE_DELTA_V2_CANDIDATE_CHANGED")
        else:
            candidate = body = None
            if preparation.after_body_bytes_digest is not None:
                raise IntegrityError("EXPERIENCE_DELTA_V2_CANDIDATE_CHANGED")
        if self._diff_digest(self._exact_diff(
            preparation.action, preparation.candidate_ref, current, body,
        )) != preparation.diff_bytes_digest:
            raise IntegrityError("EXPERIENCE_DELTA_V2_DIFF_CHANGED")
        return preparation, diff, candidate, body

    def prepare_delta(
        self, *, operation_id: str,
        action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"],
        candidate_ref: str | None,
        expected_heads: tuple[LessonHeadExpectation, ...],
    ) -> str:
        """Author submits exact inputs; only the reviewer can see the text diff."""

        self._authorize(self.author_actor_id, "propose")
        if re.fullmatch(_OPERATION_ID, operation_id) is None:
            raise ValueError("EXPERIENCE_DELTA_V2_OPERATION_INVALID")
        heads = tuple(item.revalidated() for item in expected_heads)
        if (
            len({item.object_id for item in heads}) != len(heads)
            or len(heads) > 5
            or tuple(item.object_id for item in heads) != tuple(sorted(item.object_id for item in heads))
            or (action in {"ADD", "NOOP"} and heads)
            or (action in {"REVISE", "RETRACT"} and len(heads) != 1)
            or (action == "SUPERSEDE" and len(heads) < 2)
            or (action in {"ADD", "REVISE", "SUPERSEDE"}) != (candidate_ref is not None)
        ):
            raise IntegrityError("EXPERIENCE_DELTA_V2_INPUT_INVALID")
        command = {
            "operation_id": operation_id, "action": action,
            "candidate_ref": candidate_ref,
            "expected_heads": [item.model_dump(mode="json") for item in heads],
            "author_id": self.author_actor_id,
        }
        command_digest = sha256_digest(command)
        operation_key = "experience-delta-prepare:" + sha256_digest({
            "author_id": self.author_actor_id, "operation_id": operation_id,
        })[7:]
        prior = self.workspace.store.get_idempotent(operation_key, command_digest)
        if prior is not None:
            return prior["preparation_ref"]
        current = tuple(self._current(item) for item in heads)
        if candidate_ref is not None:
            candidate, body = self._candidate_body(candidate_ref)
        else:
            candidate = body = None
        diff = self._exact_diff(action, candidate_ref, current, body)
        preparation = LessonDeltaPreparationV2(
            operation_id=operation_id, action=action, candidate_ref=candidate_ref,
            expected_heads=heads,
            before_body_bytes_digests=tuple(item.payload.get("body_bytes_digest") for item in current),
            after_body_bytes_digest=candidate.body_bytes_digest if candidate else None,
            diff_bytes_digest=self._diff_digest(diff),
            private_diff_ref="private-experience-delta:" + command_digest[7:] + "@v2",
            author_id=self.author_actor_id, created_at=self.workspace.clock.now(),
        )
        preparation_ref = self._preparation_ref(preparation)
        with self.workspace.store.transaction() as connection:
            self._authorize(self.author_actor_id, "propose")
            self.workspace.store.require_before_commit(
                connection, lambda: self._authorize(self.author_actor_id, "propose"),
            )
            prior = self.workspace.store.get_idempotent(operation_key, command_digest, connection=connection)
            if prior is not None:
                return prior["preparation_ref"]
            if tuple(self._current(item) for item in heads) != current:
                raise IntegrityError("EXPERIENCE_DELTA_V2_PARENT_CHANGED")
            self.workspace.private_records.write(
                connection, record_id=preparation.private_diff_ref,
                owner_id=self.reviewer_actor_id, scope_ref=preparation_ref,
                payload={"diff": diff},
            )
            self.workspace.store.save_artifact(
                connection, preparation_ref, DELTA_PREPARATION_MEDIA,
                preparation.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, command_digest, {"preparation_ref": preparation_ref},
            )
        return preparation_ref

    def review_prepared_delta(self, preparation_ref: str) -> dict[str, Any]:
        self._authorize(self.reviewer_actor_id, "govern")
        preparation, diff, candidate, _ = self._prepared_context(preparation_ref)
        if candidate is not None:
            _, _, repair_required, selections = self._evidence(candidate)
        else:
            repair_required, selections = False, ()
        self._authorize(self.reviewer_actor_id, "govern")
        return {
            "schema_version": "orgrebase.lesson-delta-review.v2",
            "preparation_ref": preparation_ref,
            "preparation_digest": preparation.digest,
            "diff_bytes_digest": preparation.diff_bytes_digest,
            "expected_heads": [item.model_dump(mode="json") for item in preparation.expected_heads],
            "before_body_bytes_digests": list(preparation.before_body_bytes_digests),
            "after_body_bytes_digest": preparation.after_body_bytes_digest,
            "exact_diff": diff,
            "repair_review_required": repair_required,
            "revision_selection_digests": [item["digest"] for item in selections],
            "status": "REVIEW_ONLY_NOT_PUBLISHED",
        }

    def decide_prepared_delta(
        self, preparation_ref: str, *, expected_diff_bytes_digest: str,
        verdict: Literal["APPROVED", "REJECTED"], reason_code: str,
        body_bytes_digest: str | None = None,
        declassified_exact_content: bool = False,
        purpose: str = "workspace-change-explanation-v1",
        recipients: tuple[str, ...] = ("workspace-advisory",),
    ) -> str:
        self._authorize(self.reviewer_actor_id, "govern")
        preparation = LessonDeltaPreparationV2.model_validate(
            self.workspace.store.load_artifact(preparation_ref, DELTA_PREPARATION_MEDIA).payload,
        ).revalidated()
        if (
            preparation_ref != self._preparation_ref(preparation)
            or expected_diff_bytes_digest != preparation.diff_bytes_digest
        ):
            raise IntegrityError("EXPERIENCE_DELTA_V2_DECISION_INPUT_CHANGED")
        if verdict == "APPROVED":
            return self.apply_delta(
                operation_id=preparation.operation_id, action=preparation.action,
                candidate_ref=preparation.candidate_ref,
                expected_heads=preparation.expected_heads,
                reason_code=reason_code, body_bytes_digest=body_bytes_digest,
                declassified_exact_content=declassified_exact_content,
                purpose=purpose, recipients=recipients,
                prepared_ref=preparation_ref,
                expected_diff_bytes_digest=expected_diff_bytes_digest,
            )
        if (
            re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", reason_code) is None
            or body_bytes_digest is not None or declassified_exact_content
        ):
            raise IntegrityError("EXPERIENCE_DELTA_V2_REJECTION_INVALID")
        command = {
            "preparation_ref": preparation_ref,
            "preparation_digest": preparation.digest,
            "diff_bytes_digest": preparation.diff_bytes_digest,
            "verdict": "REJECTED", "reason_code": reason_code,
            "purpose": purpose, "recipients": recipients,
            "reviewer_id": self.reviewer_actor_id,
        }
        command_digest = sha256_digest(command)
        operation_key = "experience-lesson-delta:" + sha256_digest({
            "reviewer_id": self.reviewer_actor_id,
            "operation_id": preparation.operation_id,
        })[7:]
        prior = self.workspace.store.get_idempotent(operation_key, command_digest)
        if prior is not None:
            return prior["delta_ref"]
        # A rejection records no new head, even if the prepared base is stale.
        decision = LessonDeltaDecisionV2(
            preparation_ref=preparation_ref, preparation_digest=preparation.digest,
            diff_bytes_digest=preparation.diff_bytes_digest,
            verdict="REJECTED", reviewer_id=self.reviewer_actor_id,
            reason_code=reason_code, body_bytes_digest=None,
            purpose=purpose, recipients=recipients, decided_at=self.workspace.clock.now(),
        )
        decision_ref = "experience-lesson-delta-decision:" + decision.digest[7:] + "@v2"
        delta = LessonDeltaReceiptV2(
            operation_id=preparation.operation_id, action=preparation.action,
            status="REJECTED", preparation_ref=preparation_ref,
            preparation_digest=preparation.digest,
            decision_ref=decision_ref, decision_digest=decision.digest,
            expected_heads=preparation.expected_heads,
            before_body_bytes_digests=preparation.before_body_bytes_digests,
            after_body_bytes_digest=preparation.after_body_bytes_digest,
            diff_bytes_digest=preparation.diff_bytes_digest,
            private_diff_ref=preparation.private_diff_ref,
            candidate_ref=preparation.candidate_ref,
            prior_head_refs=tuple(
                item.object_id + "@" + item.version for item in preparation.expected_heads
            ),
            current_head_ref=None, publication_ref=None,
            reviewer_id=self.reviewer_actor_id,
            reason_code=reason_code, decided_at=decision.decided_at,
        )
        delta_ref = "experience-lesson-delta:" + delta.digest[7:] + "@v2"
        with self.workspace.store.transaction() as connection:
            self._authorize(self.reviewer_actor_id, "govern")
            self.workspace.store.require_before_commit(
                connection, lambda: self._authorize(self.reviewer_actor_id, "govern"),
            )
            prior = self.workspace.store.get_idempotent(
                operation_key, command_digest, connection=connection,
            )
            if prior is not None:
                return prior["delta_ref"]
            self.workspace.store.save_artifact(
                connection, decision_ref, DELTA_DECISION_MEDIA,
                decision.model_dump(mode="json"),
            )
            self.workspace.store.save_artifact(
                connection, delta_ref, DELTA_V2_MEDIA,
                delta.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, command_digest, {"delta_ref": delta_ref},
            )
            self.workspace.store.append_event(connection, "EXPERIENCE_LESSON_DELTA_REJECTED", {
                "delta_ref": delta_ref, "delta_digest": delta.digest,
                "reviewer_id": self.reviewer_actor_id,
            })
        return delta_ref

    def read_delta_diff(self, delta_ref: str) -> dict[str, Any]:
        """Reviewer-only historical exact diff; v1 stays explicitly unknown."""

        self._authorize(self.reviewer_actor_id, "govern")
        if delta_ref.endswith("@v1"):
            legacy = LessonDeltaReceipt.model_validate(
                self.workspace.store.load_artifact(delta_ref, DELTA_MEDIA).payload,
            ).revalidated()
            if delta_ref != "experience-lesson-delta:" + legacy.digest[7:] + "@v1":
                raise IntegrityError("EXPERIENCE_DELTA_V1_BINDING_INVALID")
            self._authorize(self.reviewer_actor_id, "govern")
            return {
                "schema_version": "orgrebase.lesson-delta-read.v2",
                "delta_ref": delta_ref, "status": "HISTORICAL_V1",
                "action": legacy.action, "prior_head_refs": list(legacy.prior_head_refs),
                "current_head_ref": legacy.current_head_ref,
                "diff_status": "DIFF_NOT_RECORDED", "exact_diff": None,
            }
        receipt = LessonDeltaReceiptV2.model_validate(
            self.workspace.store.load_artifact(delta_ref, DELTA_V2_MEDIA).payload,
        ).revalidated()
        if delta_ref != "experience-lesson-delta:" + receipt.digest[7:] + "@v2":
            raise IntegrityError("EXPERIENCE_DELTA_V2_BINDING_INVALID")
        preparation = LessonDeltaPreparationV2.model_validate(
            self.workspace.store.load_artifact(
                receipt.preparation_ref, DELTA_PREPARATION_MEDIA,
            ).payload,
        ).revalidated()
        decision = LessonDeltaDecisionV2.model_validate(
            self.workspace.store.load_artifact(receipt.decision_ref, DELTA_DECISION_MEDIA).payload,
        ).revalidated()
        if (
            self._preparation_ref(preparation) != receipt.preparation_ref
            or preparation.digest != receipt.preparation_digest
            or preparation.diff_bytes_digest != receipt.diff_bytes_digest
            or preparation.private_diff_ref != receipt.private_diff_ref
            or preparation.action != receipt.action
            or preparation.candidate_ref != receipt.candidate_ref
            or preparation.expected_heads != receipt.expected_heads
            or preparation.before_body_bytes_digests != receipt.before_body_bytes_digests
            or preparation.after_body_bytes_digest != receipt.after_body_bytes_digest
            or decision.digest != receipt.decision_digest
            or receipt.decision_ref != "experience-lesson-delta-decision:" + decision.digest[7:] + "@v2"
            or decision.preparation_ref != receipt.preparation_ref
            or decision.preparation_digest != receipt.preparation_digest
            or decision.diff_bytes_digest != receipt.diff_bytes_digest
            or decision.verdict != ("REJECTED" if receipt.status == "REJECTED" else "APPROVED")
            or decision.reviewer_id != self.reviewer_actor_id
            or decision.reason_code != receipt.reason_code
            or decision.decided_at != receipt.decided_at
            or (decision.verdict == "APPROVED"
                and decision.body_bytes_digest != receipt.after_body_bytes_digest)
        ):
            raise IntegrityError("EXPERIENCE_DELTA_V2_DECISION_BINDING_INVALID")
        private = self.workspace.private_records.read_owned(
            receipt.private_diff_ref, owner_id=self.reviewer_actor_id,
            scope_ref=receipt.preparation_ref,
        )
        diff = private.get("diff") if private is not None else None
        if diff is not None and self._diff_digest(diff) != receipt.diff_bytes_digest:
            raise IntegrityError("EXPERIENCE_DELTA_V2_DIFF_CHANGED")
        self._authorize(self.reviewer_actor_id, "govern")
        return {
            "schema_version": "orgrebase.lesson-delta-read.v2",
            "delta_ref": delta_ref, "status": receipt.status,
            "action": receipt.action,
            "expected_heads": [item.model_dump(mode="json") for item in receipt.expected_heads],
            "before_body_bytes_digests": list(receipt.before_body_bytes_digests),
            "after_body_bytes_digest": receipt.after_body_bytes_digest,
            "decision_ref": receipt.decision_ref,
            "decision_digest": receipt.decision_digest,
            "diff_bytes_digest": receipt.diff_bytes_digest,
            "diff_status": "EXACT" if diff is not None else "PRIVATE_DIFF_UNAVAILABLE",
            "exact_diff": diff,
        }

    @staticmethod
    def _preserves(parent: VersionedObject, body: LessonBody, evidence: tuple[str, ...]) -> None:
        LessonRevisionPayload.model_validate(parent.payload).revalidated()
        prior = LessonBody.model_validate(parent.payload["body"])
        if (
            parent.payload.get("status") != "ADMITTED"
            or prior.kind != body.kind
            or prior.problem_code != body.problem_code
            or any(not set(getattr(prior, name)) <= set(getattr(body, name))
                   for name in (
                       "applicability", "contraindications", "stop_conditions",
                       "applicability_tags", "contraindication_tags", "dependency_object_kinds",
                   ))
            or not set(parent.payload["assessment_refs"]) <= set(evidence)
        ):
            raise IntegrityError("EXPERIENCE_LESSON_PARENT_SAFETY_LOST")

    def apply_delta(
        self, *, operation_id: str,
        action: Literal["ADD", "REVISE", "SUPERSEDE", "RETRACT", "NOOP"],
        candidate_ref: str | None, expected_heads: tuple[LessonHeadExpectation, ...],
        reason_code: str, body_bytes_digest: str | None = None,
        declassified_exact_content: bool = False,
        purpose: str = "workspace-change-explanation-v1",
        recipients: tuple[str, ...] = ("workspace-advisory",),
        prepared_ref: str | None = None,
        expected_diff_bytes_digest: str | None = None,
        legacy_replay_only: bool = False,
    ) -> str:
        self._authorize(self.reviewer_actor_id, "govern")
        if not re.fullmatch(_OPERATION_ID, operation_id) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", reason_code):
            raise ValueError("EXPERIENCE_LESSON_DELTA_INPUT_INVALID")
        selected_heads = tuple(head.revalidated() for head in expected_heads)
        if tuple(sorted(head.object_id for head in selected_heads)) != tuple(head.object_id for head in selected_heads):
            raise ValueError("EXPERIENCE_LESSON_HEAD_ORDER_INVALID")
        if len({head.object_id for head in selected_heads}) != len(selected_heads):
            raise ValueError("EXPERIENCE_LESSON_HEAD_DUPLICATED")
        if (
            (action in {"ADD", "NOOP"} and selected_heads)
            or (action in {"REVISE", "RETRACT"} and len(selected_heads) != 1)
            or (action == "SUPERSEDE" and len(selected_heads) < 2)
        ):
            raise IntegrityError("EXPERIENCE_LESSON_DELTA_HEAD_SET_INVALID")
        if prepared_ref is None and expected_diff_bytes_digest is not None:
            raise IntegrityError("EXPERIENCE_DELTA_V2_PREPARATION_REQUIRED")
        command = {
            "operation_id": operation_id, "action": action, "candidate_ref": candidate_ref,
            "expected_heads": [head.model_dump(mode="json") for head in selected_heads],
            "reason_code": reason_code, "body_bytes_digest": body_bytes_digest,
            "declassified_exact_content": declassified_exact_content,
            "purpose": purpose, "recipients": recipients,
            "reviewer_id": self.reviewer_actor_id,
            **({"prepared_ref": prepared_ref, "expected_diff_bytes_digest": expected_diff_bytes_digest}
               if prepared_ref is not None else {}),
        }
        operation_key = "experience-lesson-delta:" + sha256_digest({
            "reviewer_id": self.reviewer_actor_id, "operation_id": operation_id,
        })[7:]
        command_digest = sha256_digest(command)
        previous = self.workspace.store.get_idempotent(operation_key, command_digest)
        if previous is not None:
            if legacy_replay_only and not previous["delta_ref"].endswith("@v1"):
                raise IntegrityError("EXPERIENCE_LEGACY_DELTA_REPLAY_ONLY")
            return previous["delta_ref"]
        if legacy_replay_only:
            raise IntegrityError("EXPERIENCE_DELTA_V2_PREPARATION_REQUIRED")
        preparation = None
        if prepared_ref is not None:
            preparation, _, _, _ = self._prepared_context(prepared_ref)
            if (
                preparation.operation_id != operation_id
                or preparation.action != action
                or preparation.candidate_ref != candidate_ref
                or preparation.expected_heads != selected_heads
                or preparation.diff_bytes_digest != expected_diff_bytes_digest
            ):
                raise IntegrityError("EXPERIENCE_DELTA_V2_DECISION_INPUT_CHANGED")
        if action in {"ADD", "REVISE", "SUPERSEDE"}:
            if not candidate_ref or not declassified_exact_content or not body_bytes_digest:
                raise IntegrityError("EXPERIENCE_CONTENT_PUBLICATION_REQUIRED")
            candidate, body = self._candidate_body(candidate_ref)
            if candidate.body_bytes_digest != body_bytes_digest:
                raise IntegrityError("EXPERIENCE_CONTENT_BYTES_CHANGED")
            if purpose != candidate.profile_id or not recipients or len(set(recipients)) != len(recipients):
                raise IntegrityError("EXPERIENCE_CONTENT_RECIPIENT_SCOPE_INVALID")
            clusters, evidence, repair_required, selections = self._evidence(candidate)
            if not clusters:
                raise IntegrityError("EXPERIENCE_LESSON_SUPPORT_REQUIRED")
            if repair_required and reason_code != "CONDITION_SCOPED_REPAIR_REVIEWED":
                raise IntegrityError("EXPERIENCE_REVISION_REPAIR_REVIEW_REQUIRED")
        else:
            if candidate_ref is not None or body_bytes_digest is not None or declassified_exact_content:
                raise IntegrityError("EXPERIENCE_CONTENT_NOT_APPLICABLE")
            candidate = body = None
            clusters = evidence = ()
            selections = ()
        with self.workspace.store.transaction() as connection:
            self._authorize(self.reviewer_actor_id, "govern")
            self.workspace.store.require_before_commit(
                connection, lambda: self._authorize(self.reviewer_actor_id, "govern"),
            )
            prior = self.workspace.store.get_idempotent(operation_key, command_digest, connection=connection)
            if prior is not None:
                return prior["delta_ref"]
            if preparation is not None:
                fresh_preparation, _, _, _ = self._prepared_context(prepared_ref)
                if fresh_preparation.digest != preparation.digest:
                    raise IntegrityError("EXPERIENCE_DELTA_V2_PREPARATION_CHANGED")
            if candidate is not None:
                _, _, _, fresh_selections = self._evidence(candidate)
                if fresh_selections != selections:
                    raise IntegrityError("EXPERIENCE_REVISION_SELECTION_CHANGED")
                self.workspace.store.require_before_commit(
                    connection, lambda: self._require_revision_selection_current(candidate, selections),
                )
            current = tuple(self._current(head) for head in selected_heads)
            publication_ref = None
            next_ref = None
            decided_at = self.workspace.clock.now()
            delta_decision = None
            decision_ref = None
            if preparation is not None:
                delta_decision = LessonDeltaDecisionV2(
                    preparation_ref=prepared_ref, preparation_digest=preparation.digest,
                    diff_bytes_digest=preparation.diff_bytes_digest,
                    verdict="APPROVED", reviewer_id=self.reviewer_actor_id,
                    reason_code=reason_code, body_bytes_digest=body_bytes_digest,
                    purpose=purpose, recipients=recipients, decided_at=decided_at,
                )
                decision_ref = "experience-lesson-delta-decision:" + delta_decision.digest[7:] + "@v2"
                self.workspace.store.save_artifact(
                    connection, decision_ref, DELTA_DECISION_MEDIA,
                    delta_decision.model_dump(mode="json"),
                )
            if candidate is not None and body is not None:
                if action == "REVISE":
                    if selected_heads[0].object_id != lesson_object_prefix(candidate.profile_id) + candidate.lesson_id:
                        raise IntegrityError("EXPERIENCE_LESSON_PARENT_ID_MISMATCH")
                    self._preserves(current[0], body, evidence)
                if action == "SUPERSEDE":
                    if any(item.payload.get("profile_id") != candidate.profile_id for item in current):
                        raise IntegrityError("EXPERIENCE_LESSON_PROFILE_MISMATCH")
                    bases = {
                        (tuple(item.payload["body"]["applicability"]),
                         tuple(item.payload["body"]["contraindications"]),
                         tuple(item.payload["body"]["applicability_tags"]),
                         tuple(item.payload["body"]["contraindication_tags"])) for item in current
                    }
                    if len(bases) != 1:
                        raise IntegrityError("EXPERIENCE_CONFLICTING_LESSONS_NOT_MERGEABLE")
                    for item in current:
                        self._preserves(item, body, evidence)
                publication_decision = ContentPublicationDecision(
                    candidate_ref=candidate_ref, body_bytes_digest=body_bytes_digest,
                    declassified_exact_content=True, purpose=purpose,
                    recipients=recipients, reviewer_id=self.reviewer_actor_id,
                    reason_code=reason_code, decided_at=self.workspace.clock.now(),
                )
                publication_ref = "experience-content-publication:" + publication_decision.digest[7:] + "@v1"
                self.workspace.store.save_artifact(
                    connection, publication_ref, PUBLICATION_MEDIA,
                    publication_decision.model_dump(mode="json"),
                )
                selection_refs = []
                for selection in selections:
                    selection_ref = "experience-revision-selection:" + selection["digest"][7:] + "@v1"
                    self.workspace.store.save_artifact(
                        connection, selection_ref, REVISION_SELECTION_MEDIA,
                        selection,
                    )
                    selection_refs.append(selection_ref)
                target_id = lesson_object_prefix(candidate.profile_id) + candidate.lesson_id
                version = "r1" if action in {"ADD", "SUPERSEDE"} else f"r{int(current[0].version[1:]) + 1}"
                published = VersionedObject(
                    id=target_id, version=version, kind="ExperienceLesson",
                    label=f"Reviewed {body.kind.lower()} {body.problem_code}",
                    domain="workspace-experience",
                    state=ObjectState.CURRENT if action in {"ADD", "SUPERSEDE"} else ObjectState.PROPOSED,
                    payload=LessonRevisionPayload(
                        status="ADMITTED", profile_id=candidate.profile_id,
                        lesson_id=candidate.lesson_id,
                        body=body.model_dump(mode="json"),
                        body_bytes_digest=body_bytes_digest,
                        publication_ref=publication_ref,
                        publication_digest=publication_decision.digest,
                        candidate_ref=candidate_ref,
                        assessment_refs=evidence,
                        support_assessment_refs=candidate.support_assessment_refs,
                        counter_assessment_refs=candidate.counter_assessment_refs,
                        support_cluster_ids=clusters,
                        recipients=recipients, purpose=purpose,
                        parent_refs=tuple(item.ref for item in current),
                    ).model_dump(mode="json", exclude_none=True),
                    source_refs=tuple(sorted({
                        candidate_ref, publication_ref, *evidence, *selection_refs,
                        *((decision_ref,) if decision_ref else ()),
                    })),
                    valid_from=self.workspace.clock.now(),
                    valid_to=timestamp(utc_datetime(self.workspace.clock.now()) + timedelta(days=30)),
                    allowed_purposes=("experience_recall",),
                )
                if action in {"ADD", "SUPERSEDE"}:
                    self.workspace.store.create_current_if_absent(connection, published)
                else:
                    self.workspace.store.insert_version(connection, published, make_current=False)
                    self.workspace.store.promote_version(connection, target_id, current[0].version, version)
                next_ref = published.ref
            if action in {"RETRACT", "SUPERSEDE"}:
                for item in current:
                    if item.payload.get("status") != "ADMITTED":
                        raise IntegrityError("EXPERIENCE_LESSON_NOT_ADMITTED")
                    tombstone = VersionedObject(
                        id=item.id, version=f"r{int(item.version[1:]) + 1}",
                        kind="ExperienceLesson", label=item.label,
                        domain="workspace-experience", state=ObjectState.PROPOSED,
                        payload=LessonRevisionPayload(
                            status="RETRACTED" if action == "RETRACT" else "SUPERSEDED",
                            profile_id=item.payload["profile_id"],
                            lesson_id=item.payload["lesson_id"],
                            parent_refs=(item.ref,), successor_ref=next_ref,
                            reason_code=reason_code,
                        ).model_dump(mode="json", exclude_none=True),
                        source_refs=item.source_refs,
                        valid_from=self.workspace.clock.now(),
                        allowed_purposes=("experience_recall",),
                    )
                    self.workspace.store.insert_version(connection, tombstone, make_current=False)
                    self.workspace.store.promote_version(connection, item.id, item.version, tombstone.version)
                if action == "RETRACT":
                    next_ref = None
            if preparation is None:
                delta = LessonDeltaReceipt(
                    operation_id=operation_id, action=action,
                    candidate_ref=candidate_ref,
                    prior_head_refs=tuple(item.ref for item in current),
                    current_head_ref=next_ref, publication_ref=publication_ref,
                    reviewer_id=self.reviewer_actor_id,
                    reason_code=reason_code, decided_at=decided_at,
                )
                delta_ref = "experience-lesson-delta:" + delta.digest[7:] + "@v1"
                delta_media = DELTA_MEDIA
            else:
                assert delta_decision is not None and decision_ref is not None
                delta = LessonDeltaReceiptV2(
                    operation_id=operation_id, action=action,
                    status="NOOP" if action == "NOOP" else "APPLIED",
                    preparation_ref=prepared_ref, preparation_digest=preparation.digest,
                    decision_ref=decision_ref, decision_digest=delta_decision.digest,
                    expected_heads=selected_heads,
                    before_body_bytes_digests=preparation.before_body_bytes_digests,
                    after_body_bytes_digest=preparation.after_body_bytes_digest,
                    diff_bytes_digest=preparation.diff_bytes_digest,
                    private_diff_ref=preparation.private_diff_ref,
                    candidate_ref=candidate_ref,
                    prior_head_refs=tuple(item.ref for item in current),
                    current_head_ref=next_ref, publication_ref=publication_ref,
                    reviewer_id=self.reviewer_actor_id,
                    reason_code=reason_code, decided_at=decided_at,
                )
                delta_ref = "experience-lesson-delta:" + delta.digest[7:] + "@v2"
                delta_media = DELTA_V2_MEDIA
            self.workspace.store.save_artifact(
                connection, delta_ref, delta_media, delta.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, command_digest, {"delta_ref": delta_ref},
            )
            self.workspace.store.append_event(connection, "EXPERIENCE_LESSON_DELTA_APPLIED", {
                "delta_ref": delta_ref, "delta_digest": delta.digest,
                "action": action, "current_head_ref": next_ref,
                "reviewer_id": self.reviewer_actor_id,
            })
        return delta_ref

    def _require_revision_selection_current(
        self, candidate: LessonCandidate, expected: tuple[dict[str, Any], ...],
    ) -> None:
        if self._evidence(candidate)[3] != expected:
            raise IntegrityError("EXPERIENCE_REVISION_SELECTION_CHANGED")
