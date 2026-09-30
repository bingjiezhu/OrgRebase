"""Honest derivative closure receipts for erased private experience sources.

Current Recall already rechecks PrivateRecordStore before exposing lessons.
This ledger identifies known downstream locators for repair.  It never labels
unknown in-flight model inputs or offline checkpoints as purged.
"""

from __future__ import annotations

import re
from typing import Any

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_assessment import ASSESSMENT_MEDIA
from orgrebase.workspace.experience_collection import OBSERVATION_MEDIA
from orgrebase.workspace.experience_contracts import (
    CaseAssessmentReceipt,
    CaseObservationV2,
    MemoryInvalidationReceipt,
    MemorySnapshot,
    RecallSelectionManifest,
)
from orgrebase.workspace.experience_invalidation_detail import (
    DETAIL_MEDIA,
    ClosureAction,
    MemoryClosureDetail,
    closure_detail_ref,
)
from orgrebase.workspace.experience_lessons import CANDIDATE_MEDIA, LessonCandidate
from orgrebase.workspace.experience_recall import SELECTION_MEDIA, SNAPSHOT_MEDIA
from orgrebase.workspace.experience_source_hold import (
    SOURCE_HOLD_MEDIA,
    SourceInvalidationHold,
    load_source_hold,
    source_hold_ref,
)

INVALIDATION_MEDIA = "application/vnd.orgrebase.memory-invalidation+json"


class ExperienceInvalidationService:
    def __init__(self, workspace: Any, *, manager_actor_id: str, profile_ids: tuple[str, ...]) -> None:
        if not manager_actor_id or not profile_ids or len(set(profile_ids)) != len(profile_ids):
            raise ValueError("EXPERIENCE_INVALIDATION_SCOPE_REQUIRED")
        self.workspace = workspace
        self.manager_actor_id = manager_actor_id
        self.profile_ids = tuple(sorted(profile_ids))

    def _authorize(self) -> None:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("EXPERIENCE_INVALIDATION_PRINCIPAL_REQUIRED")
        if principal.actor_id != self.manager_actor_id or principal.tenant_id != self.workspace.store.tenant_id:
            raise AuthorizationError("EXPERIENCE_INVALIDATION_MANAGER_DENIED")
        require_action(self.workspace, "privacy_manage")

    def _artifacts(self, prefix: str, media_type: str, *, max_scanned: int) -> tuple[tuple[Any, ...], bool]:
        cursor = None
        rows = []
        while len(rows) < max_scanned:
            page = self.workspace.store.artifact_page(
                artifact_id_prefix=prefix, expected_media_type=media_type,
                after=cursor, limit=min(100, max_scanned - len(rows)),
            )
            rows.extend(page["items"])
            if page["next_cursor"] is None:
                return tuple(rows), True
            cursor = page["next_cursor"]
        return tuple(rows), False

    def _lesson_heads(self, *, max_scanned: int) -> tuple[tuple[Any, ...], bool]:
        rows = []
        cursor = None
        # A deleted episode can have derivatives in more than one profile.
        # An administrator-supplied profile subset must not make the closure
        # look complete while hiding another current head.
        while len(rows) < max_scanned:
            page = self.workspace.store.current_object_page(
                object_id_prefix="experience-lesson:",
                after=cursor, limit=min(100, max_scanned - len(rows)),
            )
            rows.extend(page["items"])
            if page["next_cursor"] is None:
                return tuple(rows), True
            cursor = page["next_cursor"]
        return tuple(rows), False

    def _depends_on_cases(self, lesson: Any, affected_cases: set[str]) -> bool:
        if lesson.kind != "ExperienceLesson" or lesson.payload.get("status") != "ADMITTED":
            return False
        for assessment_ref in lesson.payload.get("assessment_refs", ()):
            receipt = CaseAssessmentReceipt.model_validate(
                self.workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
            ).revalidated()
            if receipt.case_ref in affected_cases:
                return True
        return False

    def _candidate_depends_on_cases(
        self, candidate: LessonCandidate, affected_cases: set[str],
    ) -> bool:
        for assessment_ref in (
            *candidate.support_assessment_refs, *candidate.counter_assessment_refs,
        ):
            receipt = CaseAssessmentReceipt.model_validate(
                self.workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
            ).revalidated()
            if receipt.case_ref in affected_cases:
                return True
        return False

    @staticmethod
    def _action(
        layer: str, action: str, status: str, *, locators: tuple[str, ...] = (),
        reason: str, checked_at: str, evidence: object | None = None,
    ) -> ClosureAction:
        return ClosureAction(
            layer=layer, action=action, status=status,
            locator_refs=locators,
            evidence_digest=sha256_digest(evidence) if evidence is not None else None,
            reason_code=reason, checked_at=checked_at,
        )

    def read_closure_detail(self, receipt_ref: str) -> MemoryClosureDetail:
        """Read exact per-layer evidence as the privacy manager only."""

        self._authorize()
        receipt = self.verify_after_restore(receipt_ref)
        detail = MemoryClosureDetail.model_validate(
            self.workspace.store.load_artifact(
                closure_detail_ref(receipt.digest), DETAIL_MEDIA,
            ).payload,
        ).revalidated()
        if (
            detail.receipt_ref != receipt_ref
            or detail.receipt_digest != receipt.digest
            or detail.tenant_id != receipt.tenant_id
            or detail.workspace_id != receipt.workspace_id
            or detail.source_record_id != receipt.source_record_id
            or detail.closure_scan_complete != receipt.closure_scan_complete
        ):
            raise IntegrityError("EXPERIENCE_CLOSURE_DETAIL_BINDING_INVALID")
        return detail

    def record_source_invalidation(
        self, *, operation_id: str, source_record_id: str, max_scanned: int = 5000,
    ) -> str:
        self._authorize()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", operation_id):
            raise ValueError("EXPERIENCE_INVALIDATION_OPERATION_INVALID")
        if not source_record_id or type(max_scanned) is not int or not 1 <= max_scanned <= 5000:
            raise ValueError("EXPERIENCE_INVALIDATION_INPUT_INVALID")
        status = self.workspace.private_records.record_status(source_record_id)
        if status not in {"DELETED", "EXPIRED"}:
            raise IntegrityError("EXPERIENCE_SOURCE_NOT_INVALIDATED")
        command = {
            "operation_id": operation_id, "source_record_id": source_record_id,
            "status": status, "profile_ids": self.profile_ids,
            "max_scanned": max_scanned,
        }
        operation_key = "experience-invalidation:" + sha256_digest({
            "source_record_id": source_record_id, "operation_id": operation_id,
        })[7:]
        digest = sha256_digest(command)
        previous = self.workspace.store.get_idempotent(operation_key, digest)
        if previous is not None:
            self.read_closure_detail(previous["receipt_ref"])
            return previous["receipt_ref"]
        cases, cases_complete = self._artifacts(
            "experience-case:", OBSERVATION_MEDIA, max_scanned=max_scanned,
        )
        case_refs = []
        for row in cases:
            case = CaseObservationV2.model_validate(row.payload).revalidated()
            if case.private_episode_ref == source_record_id:
                case_refs.append(row.artifact_id)
        affected_cases = set(case_refs)
        candidates, candidates_complete = self._artifacts(
            "experience-lesson-candidate:", CANDIDATE_MEDIA, max_scanned=max_scanned,
        )
        candidate_refs = []
        candidate_private_refs = []
        candidate_private_statuses: dict[str, str] = {}
        for row in candidates:
            try:
                candidate = LessonCandidate.model_validate(row.payload).revalidated()
                if (
                    row.artifact_id != "experience-lesson-candidate:" + candidate.digest[7:] + "@v1"
                    or candidate.tenant_id != self.workspace.store.tenant_id
                    or candidate.workspace_id != self.workspace.store.workspace_id
                ):
                    raise IntegrityError("EXPERIENCE_CANDIDATE_BINDING_INVALID")
                if self._candidate_depends_on_cases(candidate, affected_cases):
                    candidate_refs.append(row.artifact_id)
                    candidate_private_refs.append(candidate.private_body_ref)
                    candidate_private_statuses[candidate.private_body_ref] = (
                        self.workspace.private_records.record_status(candidate.private_body_ref)
                    )
            except (KeyError, IntegrityError, ValueError):
                candidates_complete = False
        heads, lessons_complete = self._lesson_heads(max_scanned=max_scanned)
        lesson_ids = []
        for head in heads:
            try:
                if self._depends_on_cases(head, affected_cases):
                    lesson_ids.append(head.id)
            except (KeyError, IntegrityError, ValueError):
                lessons_complete = False
        affected_ids = set(lesson_ids)
        snapshots, snapshots_complete = self._artifacts(
            "experience-memory-snapshot:", SNAPSHOT_MEDIA, max_scanned=max_scanned,
        )
        snapshot_refs = []
        snapshot_digests = set()
        historical_entries_scanned = 0
        for row in snapshots:
            snapshot = MemorySnapshot.model_validate(row.payload).revalidated()
            touches_source = False
            for entry in snapshot.lesson_entries:
                historical_entries_scanned += 1
                if historical_entries_scanned > max_scanned:
                    snapshots_complete = False
                    break
                object_id, _, version = entry.lesson_ref.rpartition("@")
                try:
                    prior = self.workspace.store.get_object(object_id, version)
                    if self._depends_on_cases(prior, affected_cases):
                        affected_ids.add(object_id)
                        touches_source = True
                except (KeyError, IntegrityError, ValueError):
                    snapshots_complete = False
                    break
            if touches_source:
                snapshot_refs.append(row.artifact_id)
                snapshot_digests.add(snapshot.digest)
            if not snapshots_complete:
                break
        selections, selections_complete = self._artifacts(
            "experience-recall-selection:", SELECTION_MEDIA, max_scanned=max_scanned,
        )
        selection_refs = []
        for row in selections:
            manifest = RecallSelectionManifest.model_validate(row.payload).revalidated()
            if (
                manifest.snapshot_digest in snapshot_digests
                or any(entry.lesson_ref.rpartition("@")[0] in affected_ids for entry in manifest.selected_lessons)
            ):
                selection_refs.append(row.artifact_id)
        complete = all((
            cases_complete, candidates_complete, lessons_complete,
            snapshots_complete, selections_complete,
        ))
        # The on-demand sparse index and process-local ranking have no durable
        # private-text cache.  Published lesson bytes remain for audit and need
        # a separate reviewer RETRACT; offline checkpoints are not enumerable.
        layers = {
            "summary": "STOPPED" if complete and candidate_refs else "UNKNOWN" if not complete else "NOT_APPLICABLE",
            "lesson": "STOPPED" if complete and affected_ids else "UNKNOWN" if not complete else "NOT_APPLICABLE",
            "index": "NOT_APPLICABLE",
            "cache": "NOT_APPLICABLE",
            "inflight": "UNKNOWN",
            "checkpoint": "UNKNOWN",
        }
        if complete and affected_ids:
            for head in heads:
                if head.id not in affected_ids:
                    continue
                if head.payload.get("status") != "ADMITTED":
                    continue
                for assessment_ref in head.payload["support_assessment_refs"]:
                    assessment = CaseAssessmentReceipt.model_validate(
                        self.workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
                    )
                    if assessment.case_ref in affected_cases:
                        case = CaseObservationV2.model_validate(
                            self.workspace.store.load_artifact(assessment.case_ref, OBSERVATION_MEDIA).payload,
                        )
                        if self.workspace.private_records.record_status(case.private_episode_ref) == "AVAILABLE":
                            layers["lesson"] = "UNKNOWN"
        receipt = MemoryInvalidationReceipt(
            tenant_id=self.workspace.store.tenant_id,
            workspace_id=self.workspace.store.workspace_id,
            operation_id=operation_id, source_record_id=source_record_id,
            source_status=status,
            affected_case_refs=tuple(sorted(set(case_refs))),
            affected_lesson_ids=tuple(sorted(affected_ids)),
            affected_snapshot_refs=tuple(sorted(set(snapshot_refs))),
            affected_selection_refs=tuple(sorted(set(selection_refs))),
            closure_scan_complete=complete, closure_complete=False,
            layer_status=layers,
            unconfirmed_layers=tuple(sorted(key for key, value in layers.items() if value == "UNKNOWN")),
            manager_id=self.manager_actor_id, checked_at=self.workspace.clock.now(),
        )
        receipt_ref = "experience-memory-invalidation:" + receipt.digest[7:] + "@v1"
        checked_at = receipt.checked_at
        source_evidence = {
            "source_record_id": source_record_id,
            "source_status": status,
            "affected_case_refs": receipt.affected_case_refs,
            "closure_scan_complete": complete,
        }

        def stop_action(layer: str, locators: tuple[str, ...]) -> ClosureAction:
            if not complete:
                return self._action(
                    layer, "STOP_USE", "UNKNOWN", locators=locators,
                    reason="INCOMPLETE_DERIVATIVE_SCAN", checked_at=checked_at,
                )
            if not locators:
                return self._action(
                    layer, "STOP_USE", "NOT_APPLICABLE",
                    reason="NO_KNOWN_DERIVATIVE", checked_at=checked_at,
                )
            return self._action(
                layer, "STOP_USE", "CONFIRMED", locators=locators,
                reason="SOURCE_DISABLED_CURRENT_GUARD", checked_at=checked_at,
                evidence={**source_evidence, "locators": locators, "layer": layer},
            )

        candidate_locators = tuple(sorted(set(candidate_refs)))
        private_locators = tuple(sorted(set(candidate_private_refs)))
        lesson_locators = tuple(sorted(affected_ids))
        if not complete:
            candidate_purge_status = "UNKNOWN"
        elif not private_locators:
            candidate_purge_status = "NOT_APPLICABLE"
        elif all(candidate_private_statuses[ref] == "DELETED" for ref in private_locators):
            candidate_purge_status = "CONFIRMED"
        elif any(candidate_private_statuses[ref] == "MISSING" for ref in private_locators):
            candidate_purge_status = "UNKNOWN"
        else:
            candidate_purge_status = "PENDING"
        candidate_purge_evidence = (
            {"private_records": tuple(
                (ref, candidate_private_statuses[ref]) for ref in private_locators
            )}
            if candidate_purge_status == "CONFIRMED" else None
        )
        actions = (
            self._action("cache", "REBUILD", "NOT_APPLICABLE", reason="NO_DURABLE_CACHE", checked_at=checked_at),
            self._action("cache", "STOP_USE", "NOT_APPLICABLE", reason="NO_DURABLE_CACHE", checked_at=checked_at),
            self._action("checkpoint", "PURGE", "UNKNOWN", reason="EXTERNAL_CHECKPOINT_UNENUMERATED", checked_at=checked_at),
            self._action("checkpoint", "REBUILD", "UNKNOWN", reason="RESTORE_LEDGER_NOT_PROVEN", checked_at=checked_at),
            self._action("checkpoint", "STOP_USE", "UNKNOWN", reason="EXTERNAL_CHECKPOINT_UNENUMERATED", checked_at=checked_at),
            self._action("index", "REBUILD", "NOT_APPLICABLE", reason="ON_DEMAND_SPARSE_INDEX", checked_at=checked_at),
            self._action("index", "STOP_USE", "NOT_APPLICABLE", reason="ON_DEMAND_SPARSE_INDEX", checked_at=checked_at),
            self._action("inflight", "PURGE", "UNKNOWN", reason="EXTERNAL_MODEL_INPUT_UNCONFIRMED", checked_at=checked_at),
            self._action("inflight", "STOP_USE", "UNKNOWN", reason="LATE_RESULT_ISOLATION_UNCONFIRMED", checked_at=checked_at),
            self._action("lesson", "PURGE", "UNKNOWN" if not complete or lesson_locators else "NOT_APPLICABLE",
                         locators=lesson_locators, reason="IMMUTABLE_CONTENT_RETENTION_UNRESOLVED", checked_at=checked_at),
            stop_action("lesson", lesson_locators),
            self._action("summary", "PURGE", candidate_purge_status,
                         locators=private_locators, reason="PRIVATE_CANDIDATE_RETENTION_STATUS",
                         checked_at=checked_at, evidence=candidate_purge_evidence),
            stop_action("summary", candidate_locators),
        )
        detail = MemoryClosureDetail(
            tenant_id=receipt.tenant_id, workspace_id=receipt.workspace_id,
            source_record_id=source_record_id, receipt_ref=receipt_ref,
            receipt_digest=receipt.digest, closure_scan_complete=complete,
            affected_candidate_refs=candidate_locators,
            affected_private_candidate_refs=private_locators,
            actions=actions, checked_at=checked_at,
        )
        marker = SourceInvalidationHold(
            tenant_id=receipt.tenant_id, workspace_id=receipt.workspace_id,
            source_record_id=source_record_id,
        )
        marker_ref = source_hold_ref(
            tenant_id=receipt.tenant_id, workspace_id=receipt.workspace_id,
            source_record_id=source_record_id,
        )
        self._authorize()
        with self.workspace.store.transaction() as connection:
            self.workspace.store.require_before_commit(connection, self._authorize)
            self.workspace.store.require_before_commit(
                connection,
                lambda: self._require_source_still_invalidated(source_record_id),
            )
            prior = self.workspace.store.get_idempotent(operation_key, digest, connection=connection)
            if prior is not None:
                self.read_closure_detail(prior["receipt_ref"])
                return prior["receipt_ref"]
            self.workspace.store.save_artifact(
                connection, receipt_ref, INVALIDATION_MEDIA, receipt.model_dump(mode="json"),
            )
            self.workspace.store.save_artifact(
                connection, marker_ref, SOURCE_HOLD_MEDIA, marker.model_dump(mode="json"),
            )
            self.workspace.store.save_artifact(
                connection, closure_detail_ref(receipt.digest), DETAIL_MEDIA,
                detail.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, digest, {"receipt_ref": receipt_ref},
            )
            self.workspace.store.append_event(connection, "EXPERIENCE_MEMORY_INVALIDATION_RECORDED", {
                "receipt_ref": receipt_ref, "receipt_digest": receipt.digest,
                "source_status": status,
                "manager_id": self.manager_actor_id,
            })
        return receipt_ref

    def _require_source_still_invalidated(self, record_id: str) -> None:
        if self.workspace.private_records.record_status(record_id) not in {"DELETED", "EXPIRED"}:
            raise IntegrityError("EXPERIENCE_RESTORE_DELETION_LEDGER_REQUIRED")

    def verify_after_restore(self, receipt_ref: str) -> MemoryInvalidationReceipt:
        """A restored backup must first replay the original deletion ledger."""

        self._authorize()
        receipt = MemoryInvalidationReceipt.model_validate(
            self.workspace.store.load_artifact(receipt_ref, INVALIDATION_MEDIA).payload,
        ).revalidated()
        if (
            receipt_ref != "experience-memory-invalidation:" + receipt.digest[7:] + "@v1"
            or receipt.tenant_id != self.workspace.store.tenant_id
            or receipt.workspace_id != self.workspace.store.workspace_id
        ):
            raise IntegrityError("EXPERIENCE_INVALIDATION_RECEIPT_BINDING_INVALID")
        self._require_source_still_invalidated(receipt.source_record_id)
        if load_source_hold(self.workspace, receipt.source_record_id) is None:
            raise IntegrityError("EXPERIENCE_RESTORE_INVALIDATION_MARKER_REQUIRED")
        return receipt
