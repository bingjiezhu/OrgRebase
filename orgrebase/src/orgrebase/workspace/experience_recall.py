"""Bounded sparse recall of current, reviewed procedural lessons.

The StateStore current pointer is the only lesson head.  Snapshots and search
indexes are disposable inputs for comparison; each read and later consumption
rechecks the pointer, source qualification, scope, expiry and publication.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from typing import Any, Literal

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.clock import utc_datetime
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_assessment import (
    REVISION_SELECTION_MEDIA,
    ExperienceAssessmentService,
)
from orgrebase.workspace.experience_contracts import (
    LessonRevisionPayload,
    LessonSnapshotEntry,
    MemorySnapshot,
    RecallReceipt,
    RecallSelectionManifest,
    empty_recall_selection_manifest,
    exact_bytes_digest,
)
from orgrebase.workspace.experience_invalidation_detail import require_assessment_source_not_held
from orgrebase.workspace.experience_lessons import (
    PUBLICATION_MEDIA,
    ContentPublicationDecision,
    LessonBody,
    lesson_object_prefix,
)

SNAPSHOT_MEDIA = "application/vnd.orgrebase.memory-snapshot+json"
SELECTION_MEDIA = "application/vnd.orgrebase.recall-selection-manifest+json"
RECALL_MEDIA = "application/vnd.orgrebase.recall-receipt+json"
RETRIEVAL_VERSION = "current-object-bm25-typed-v1"
TOKENIZER_VERSION = "unicode-word-cjk-bigram-v1"
RANKER_VERSION = "bm25-k1-1.2-b-0.75-v1"
_WORD = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def _tokens(value: str) -> tuple[str, ...]:
    terms = []
    for match in _WORD.findall(value.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            terms.extend(match[index:index + 2] for index in range(max(1, len(match) - 1)))
        else:
            terms.append(match)
            if "_" in match:
                terms.extend(part for part in match.split("_") if part)
    return tuple(terms)


def _score(
    query: tuple[str, ...], document: tuple[str, ...], *, corpus_size: int,
    document_frequency: Counter[str], average_length: float,
) -> float:
    if not query or not document:
        return 0.0
    frequency = Counter(document)
    score = 0.0
    for term in set(query):
        if term not in frequency:
            continue
        occurrences = document_frequency[term]
        idf = math.log(1 + (corpus_size - occurrences + 0.5) / (occurrences + 0.5))
        tf = frequency[term]
        score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(document) / max(1, average_length)))
    return score


def _advice(lessons: tuple[tuple[VersionedObject, LessonBody], ...]) -> str:
    sections = []
    for number, (_, body) in enumerate(lessons, 1):
        sections.append("\n".join((
            f"经验 {number} · {body.problem_code}",
            "适用条件: " + "; ".join(body.applicability),
            "不适用条件: " + "; ".join(body.contraindications),
            "步骤: " + "; ".join(body.steps),
            "停止条件: " + "; ".join(body.stop_conditions),
        )))
    return "\n\n".join(sections)


def _wire_size(lessons: tuple[tuple[VersionedObject, LessonBody], ...], advice_text: str) -> int:
    if not lessons and not advice_text:
        return 0
    envelope = {
        "lessons": [{
            "ref": item.ref, "revision": int(item.version[1:]),
            "content_digest": item.payload["body_bytes_digest"],
        } for item, _ in lessons],
        "advice_text": advice_text,
    }
    return len(canonical_json(envelope).encode("utf-8"))


class ExperienceRecallService:
    def __init__(
        self, workspace: Any, *, profile_id: str,
        assessment_service: ExperienceAssessmentService,
        source_qualification_check: Callable[[VersionedObject], None] | None = None,
        compiler_version: str = "advisory-v4",
        budget_version: str = "experience-budget-v1",
    ) -> None:
        if not profile_id or not compiler_version or not budget_version:
            raise ValueError("EXPERIENCE_RECALL_PROFILE_REQUIRED")
        self.workspace = workspace
        self.profile_id = profile_id
        self.assessment_service = assessment_service
        self.source_qualification_check = source_qualification_check
        self.compiler_version = compiler_version
        self.budget_version = budget_version

    def _authorize(self) -> str:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("EXPERIENCE_RECALL_PRINCIPAL_REQUIRED")
        if (
            principal.tenant_id != self.workspace.profile.organization_id
            or self.workspace.store.tenant_id not in {None, principal.tenant_id}
        ):
            raise AuthorizationError("EXPERIENCE_RECALL_SCOPE_DENIED")
        require_action(self.workspace, "read")
        return principal.actor_id

    def _current_body(
        self, item: VersionedObject, *, purpose: str, recipient: str,
    ) -> tuple[LessonBody | None, str | None]:
        if (
            item.kind != "ExperienceLesson" or item.state is not ObjectState.CURRENT
            or item.payload.get("status") != "ADMITTED"
        ):
            return None, "NOT_ADMITTED"
        if item.payload.get("profile_id") != self.profile_id:
            return None, "PROFILE_MISMATCH"
        if item.payload.get("purpose") != purpose or recipient not in item.payload.get("recipients", ()):
            return None, "PURPOSE_OR_RECIPIENT_DENIED"
        now = utc_datetime(self.workspace.clock.now())
        if utc_datetime(item.valid_from) > now or (
            item.valid_to is not None and now >= utc_datetime(item.valid_to)
        ):
            return None, "LESSON_EXPIRED"
        try:
            LessonRevisionPayload.model_validate(item.payload).revalidated()
            body = LessonBody.model_validate(item.payload["body"]).revalidated()
            if exact_bytes_digest(canonical_json(body.model_dump(mode="json")).encode("utf-8")) != item.payload["body_bytes_digest"]:
                raise IntegrityError("EXPERIENCE_LESSON_CONTENT_MISMATCH")
            decision = ContentPublicationDecision.model_validate(
                self.workspace.store.load_artifact(item.payload["publication_ref"], PUBLICATION_MEDIA).payload,
            ).revalidated()
            if (
                decision.digest != item.payload["publication_digest"]
                or decision.body_bytes_digest != item.payload["body_bytes_digest"]
                or decision.purpose != purpose
                or recipient not in decision.recipients
                or decision.candidate_ref != item.payload["candidate_ref"]
            ):
                raise IntegrityError("EXPERIENCE_LESSON_PUBLICATION_INVALID")
            if not item.payload.get("support_assessment_refs"):
                raise IntegrityError("EXPERIENCE_LESSON_SUPPORT_MISSING")
            expected_selection_refs = set()
            for ref in item.payload["support_assessment_refs"]:
                if self.assessment_service.evidence_verdict_for_reader(ref) != "SUPPORT":
                    raise IntegrityError("EXPERIENCE_LESSON_SUPPORT_EXPIRED")
                selection = self.assessment_service.revision_selection_for_reader(ref)
                selection_ref = "experience-revision-selection:" + selection["digest"][7:] + "@v1"
                stored_selection = self.workspace.store.load_artifact(
                    selection_ref, REVISION_SELECTION_MEDIA,
                )
                if sha256_digest(stored_selection.payload) != sha256_digest(selection):
                    raise IntegrityError("EXPERIENCE_REVISION_SELECTION_CHANGED")
                expected_selection_refs.add(selection_ref)
                require_assessment_source_not_held(self.workspace, ref)
            if {
                ref for ref in item.source_refs if ref.startswith("experience-revision-selection:")
            } != expected_selection_refs:
                raise IntegrityError("EXPERIENCE_REVISION_SELECTION_NOT_FROZEN")
            for ref in item.payload.get("counter_assessment_refs", ()):
                if not self.assessment_service.historical_counter_valid_for_reader(
                    ref,
                    support_assessment_refs=tuple(item.payload["support_assessment_refs"]),
                ):
                    raise IntegrityError("EXPERIENCE_LESSON_COUNTER_EXPIRED")
                require_assessment_source_not_held(self.workspace, ref)
            if self.source_qualification_check is not None:
                self.source_qualification_check(item)
            return body, None
        except (IntegrityError, KeyError, TypeError, ValueError, PermissionError):
            return None, "SOURCE_OR_CONTENT_INVALID"

    @staticmethod
    def _entry(item: VersionedObject) -> LessonSnapshotEntry:
        return LessonSnapshotEntry(
            lesson_ref=item.ref, lesson_revision=int(item.version[1:]),
            content_digest=item.payload["body_bytes_digest"],
            qualification_digest=sha256_digest({
                "publication": item.payload["publication_digest"],
                "assessment_refs": item.payload["assessment_refs"],
            }),
            cluster_digest=sha256_digest(item.payload["support_cluster_ids"]),
            dependency_digest=sha256_digest(item.source_refs),
        )

    def _enumerate_current_objects(
        self, *, max_scanned: int, page_size: int,
    ) -> tuple[list[VersionedObject], int, str | None, bool]:
        cursor = None
        scanned = 0
        objects: list[VersionedObject] = []
        has_more = False
        while scanned < max_scanned:
            page = self.workspace.store.current_object_page(
                object_id_prefix=lesson_object_prefix(self.profile_id),
                after=cursor, limit=min(page_size, max_scanned - scanned),
            )
            items = page["items"]
            for item in items:
                scanned += 1
                objects.append(item)
            if page["next_cursor"] is None:
                has_more = False
                cursor = items[-1].id if items else cursor
                break
            cursor = page["next_cursor"]
            has_more = True
        return objects, scanned, cursor, has_more

    def build_snapshot(
        self, *, purpose: str, recipient: str,
        max_scanned: int = 500, page_size: int = 100,
    ) -> str:
        """Freeze a bounded candidate set; PARTIAL never means empty whole DB."""

        self._authorize()
        if not purpose or not recipient or type(max_scanned) is not int or not 1 <= max_scanned <= 5000:
            raise ValueError("EXPERIENCE_SNAPSHOT_BOUND_INVALID")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("EXPERIENCE_SNAPSHOT_PAGE_SIZE_INVALID")
        with self.workspace.store.read_snapshot():
            objects, scanned, cursor, has_more = self._enumerate_current_objects(
                max_scanned=max_scanned, page_size=page_size,
            )
            event_head = self.workspace.store.audit_head()
        entries: list[LessonSnapshotEntry] = []
        excluded: Counter[str] = Counter()
        for item in objects:
            body, reason = self._current_body(item, purpose=purpose, recipient=recipient)
            if body is None:
                excluded[reason or "UNKNOWN"] += 1
            else:
                entries.append(self._entry(item))
        entries.sort(key=lambda item: item.lesson_ref)
        coverage = "PARTIAL_COVERAGE" if has_more else ("COMPLETE" if entries else "EMPTY")
        proof = sha256_digest({
            "tenant_id": self.workspace.profile.organization_id,
            "workspace_id": self.workspace.store.workspace_id,
            "profile_id": self.profile_id, "prefix": lesson_object_prefix(self.profile_id),
            "scanned": scanned, "excluded_counts": dict(sorted(excluded.items())),
            "end_cursor": cursor, "complete": not has_more,
            "event_head_digest": event_head["head_digest"],
        }) if coverage == "EMPTY" else None
        snapshot = MemorySnapshot(
            tenant_id=self.workspace.profile.organization_id,
            workspace_id=self.workspace.store.workspace_id,
            profile_id=self.profile_id,
            lesson_entries=tuple(entries), coverage=coverage,
            empty_reason="QUALIFIED_SET_EMPTY" if coverage == "EMPTY" else None,
            coverage_proof_digest=proof,
            retrieval_version=RETRIEVAL_VERSION,
            index_revision="state-current-pointer-v1:" + event_head["head_digest"],
            tokenizer_version=TOKENIZER_VERSION,
            ranker_version=RANKER_VERSION,
            compiler_version=self.compiler_version,
            budget_version=self.budget_version,
            built_at=self.workspace.clock.now(),
            event_head_digest=event_head["head_digest"],
            event_sequence_no=event_head["sequence_no"],
            scanned_count=scanned,
            excluded_counts=dict(sorted(excluded.items())),
            scan_end_cursor=cursor,
        )
        for entry in snapshot.lesson_entries:
            self._frozen_lesson(entry, purpose=purpose, recipient=recipient)
        snapshot_ref = "experience-memory-snapshot:" + snapshot.digest[7:] + "@v1"
        with self.workspace.store.transaction() as connection:
            self.workspace.store.require_before_commit(connection, self._authorize)
            self.workspace.store.save_artifact(
                connection, snapshot_ref, SNAPSHOT_MEDIA, snapshot.model_dump(mode="json"),
            )
        return snapshot_ref

    def _snapshot(self, snapshot_ref: str) -> MemorySnapshot:
        snapshot = MemorySnapshot.model_validate(
            self.workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
        ).revalidated()
        if (
            snapshot_ref != "experience-memory-snapshot:" + snapshot.digest[7:] + "@v1"
            or snapshot.tenant_id != self.workspace.profile.organization_id
            or snapshot.workspace_id != self.workspace.store.workspace_id
            or snapshot.profile_id != self.profile_id
            or snapshot.compiler_version != self.compiler_version
            or snapshot.budget_version != self.budget_version
        ):
            raise IntegrityError("EXPERIENCE_SNAPSHOT_BINDING_INVALID")
        return snapshot

    def _frozen_lesson(
        self, entry: LessonSnapshotEntry, *, purpose: str, recipient: str,
    ) -> tuple[VersionedObject, LessonBody]:
        object_id, _, version = entry.lesson_ref.rpartition("@")
        try:
            current = self.workspace.store.get_object(object_id)
        except KeyError as exc:
            raise IntegrityError("EXPERIENCE_MEMORY_HOLD") from exc
        body, _ = self._current_body(current, purpose=purpose, recipient=recipient)
        if (
            body is None or current.version != version
            or current.ref != entry.lesson_ref
            or self._entry(current).digest != entry.digest
        ):
            raise IntegrityError("EXPERIENCE_MEMORY_HOLD")
        return current, body

    @staticmethod
    def _typed_match(
        body: LessonBody, *, context_tags: frozenset[str],
        object_kinds: frozenset[str], problem_codes: frozenset[str],
    ) -> bool:
        return (
            set(body.applicability_tags) <= context_tags
            and not set(body.contraindication_tags) & context_tags
            and set(body.dependency_object_kinds) <= object_kinds
            and (not problem_codes or body.problem_code in problem_codes)
        )

    def select_for_case(
        self, *, operation_id: str, snapshot_ref: str,
        task_id: str, attempt_id: str, context_digest: str,
        case_id: str, case_revision: str,
        evaluation_arm: str, query_text: str,
        context_tags: tuple[str, ...], object_kinds: tuple[str, ...],
        problem_codes: tuple[str, ...] = (),
        purpose: str = "workspace-change-explanation-v1",
        recipient: str = "workspace-advisory",
        memory_reserved_bytes: int = 4096,
    ) -> tuple[str, str]:
        """Freeze one task's whole-lesson selection and a retrieval-only receipt."""

        actor_id = self._authorize()
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", operation_id)
            or not task_id or not attempt_id or not case_id or not case_revision or not evaluation_arm
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", context_digest)
            or not 1 <= len(query_text.encode("utf-8")) <= 4096
            or type(memory_reserved_bytes) is not int
            or not 1 <= memory_reserved_bytes <= 4096
        ):
            raise ValueError("EXPERIENCE_RECALL_INPUT_INVALID")
        if self.workspace.private_records.retention_seconds == 0:
            raise IntegrityError("EXPERIENCE_RECALL_PRIVATE_QUERY_RETENTION_DISABLED")
        snapshot = self._snapshot(snapshot_ref)
        command = {
            "operation_id": operation_id, "snapshot_ref": snapshot_ref,
            "snapshot_digest": snapshot.digest,
            "task_id": task_id, "attempt_id": attempt_id,
            "context_digest": context_digest,
            "case_id": case_id, "case_revision": case_revision,
            "evaluation_arm": evaluation_arm, "query_digest": sha256_digest(query_text),
            "context_tags": tuple(sorted(set(context_tags))),
            "object_kinds": tuple(sorted(set(object_kinds))),
            "problem_codes": tuple(sorted(set(problem_codes))),
            "purpose": purpose, "recipient": recipient,
            "memory_reserved_bytes": memory_reserved_bytes, "actor_id": actor_id,
        }
        command_digest = sha256_digest(command)
        operation_key = "experience-recall:" + sha256_digest({
            "tenant_id": self.workspace.profile.organization_id,
            "workspace_id": self.workspace.store.workspace_id,
            "operation_id": operation_id,
        })[7:]
        prior = self.workspace.store.get_idempotent(operation_key, command_digest)
        if prior is not None:
            return prior["selection_ref"], prior["recall_ref"]
        query_payload = {
            "query_text": query_text, "case_id": case_id,
            "case_revision": case_revision, "task_id": task_id,
        }
        query_ref = "private-recall-query:" + command_digest[7:] + "@v1"
        query_digest = sha256_digest(query_payload)
        eligible = []
        excluded: Counter[str] = Counter(snapshot.excluded_counts)
        tags = frozenset(context_tags)
        kinds = frozenset(object_kinds)
        codes = frozenset(problem_codes)
        for entry in snapshot.lesson_entries:
            item, body = self._frozen_lesson(entry, purpose=purpose, recipient=recipient)
            if not self._typed_match(body, context_tags=tags, object_kinds=kinds, problem_codes=codes):
                excluded["TYPED_CONDITION_MISMATCH"] += 1
                continue
            eligible.append((item, body))
        query_terms = _tokens(query_text)
        documents = tuple(_tokens(" ".join((
            body.problem_code, *body.applicability, *body.contraindications,
            *body.steps, *body.stop_conditions,
        ))) for _, body in eligible)
        document_frequency: Counter[str] = Counter(
            term for item in documents for term in set(item)
        )
        average_length = sum(len(item) for item in documents) / max(1, len(documents))
        corpus_stats_digest = sha256_digest({
            "document_count": len(documents),
            "document_lengths": [len(item) for item in documents],
            "document_frequencies": sorted(document_frequency.items()),
            "query_terms_digest": sha256_digest(query_terms),
        })
        ranked = sorted((
            (_score(
                query_terms, terms, corpus_size=len(documents),
                document_frequency=document_frequency,
                average_length=average_length,
            ), item.ref, item, body)
            for (item, body), terms in zip(eligible, documents, strict=True)
        ), key=lambda row: (-row[0], row[1]))
        selected: list[tuple[VersionedObject, LessonBody]] = []
        for score, _, item, body in ranked:
            if score <= 0 and body.problem_code not in codes:
                excluded["NO_SPARSE_OR_EXACT_MATCH"] += 1
                continue
            if len(selected) >= 3:
                excluded["TOP_K_EXCLUDED"] += 1
                continue
            proposed = tuple((*selected, (item, body)))
            proposed_advice = _advice(proposed)
            if (
                len(proposed_advice.encode("utf-8")) > 4096
                or _wire_size(proposed, proposed_advice) > memory_reserved_bytes
            ):
                excluded["WHOLE_LESSON_BUDGET_EXCLUDED"] += 1
                continue
            selected.append((item, body))
        chosen = tuple(selected)
        advice_text = _advice(chosen)
        advice_bytes = advice_text.encode("utf-8")
        wire_bytes = _wire_size(chosen, advice_text)
        selected_entries = tuple(self._entry(item) for item, _ in chosen)
        if snapshot.coverage == "EMPTY":
            manifest = empty_recall_selection_manifest(
                snapshot, case_id=case_id, case_revision=case_revision,
                evaluation_arm=evaluation_arm, query_ref=query_ref,
                query_digest=query_digest,
                task_id=task_id, attempt_id=attempt_id,
                context_digest=context_digest,
                memory_reserved_bytes=memory_reserved_bytes,
            )
            manifest = RecallSelectionManifest.model_validate({
                **manifest.model_dump(mode="json", exclude={"digest"}),
                "query_owner_id": actor_id,
            })
        else:
            manifest = RecallSelectionManifest(
                tenant_id=snapshot.tenant_id, workspace_id=snapshot.workspace_id,
                profile_id=snapshot.profile_id, case_id=case_id,
                case_revision=case_revision, evaluation_arm=evaluation_arm,
                task_id=task_id, attempt_id=attempt_id,
                context_digest=context_digest,
                snapshot_digest=snapshot.digest,
                snapshot_lesson_count=len(snapshot.lesson_entries),
                query_ref=query_ref, query_digest=query_digest,
                query_owner_id=actor_id,
                selected_lessons=selected_entries,
                advice_bytes_digest=exact_bytes_digest(advice_bytes),
                advice_byte_count=len(advice_bytes),
                coverage=snapshot.coverage,
                candidate_count=len(snapshot.lesson_entries),
                eligible_count=len(eligible),
                retrieval_version=snapshot.retrieval_version,
                index_revision=snapshot.index_revision,
                tokenizer_version=snapshot.tokenizer_version,
                ranker_version=snapshot.ranker_version,
                corpus_stats_digest=corpus_stats_digest,
                compiler_version=snapshot.compiler_version,
                budget_version=snapshot.budget_version,
                memory_reserved_bytes=memory_reserved_bytes,
            )
        manifest_ref = "experience-recall-selection:" + manifest.digest[7:] + "@v1"
        receipt = RecallReceipt(
            tenant_id=snapshot.tenant_id, workspace_id=snapshot.workspace_id,
            profile_id=snapshot.profile_id, operation_id=operation_id,
            task_id=task_id, attempt_id=attempt_id,
            context_digest=context_digest,
            case_id=case_id, case_revision=case_revision,
            snapshot_ref=snapshot_ref, snapshot_digest=snapshot.digest,
            selection_ref=manifest_ref, selection_digest=manifest.digest,
            selected_lessons=selected_entries,
            excluded_counts=dict(sorted(excluded.items())),
            scanned_count=snapshot.scanned_count,
            coverage=snapshot.coverage,
            advice_bytes_digest=manifest.advice_bytes_digest,
            advice_byte_count=manifest.advice_byte_count,
            memory_wire_byte_count=wire_bytes,
        )
        receipt_ref = "experience-recall-receipt:" + receipt.digest[7:] + "@v1"
        self._authorize()
        with self.workspace.store.transaction() as connection:
            self.workspace.store.require_before_commit(connection, self._authorize)
            previous = self.workspace.store.get_idempotent(
                operation_key, command_digest, connection=connection,
            )
            if previous is not None:
                return previous["selection_ref"], previous["recall_ref"]
            for entry in snapshot.lesson_entries:
                self._frozen_lesson(entry, purpose=purpose, recipient=recipient)
            self.workspace.private_records.write(
                connection, record_id=query_ref, owner_id=actor_id,
                scope_ref=f"experience-recall:{case_id}:{case_revision}",
                payload=query_payload,
            )
            self.workspace.store.save_artifact(
                connection, manifest_ref, SELECTION_MEDIA, manifest.model_dump(mode="json"),
            )
            self.workspace.store.save_artifact(
                connection, receipt_ref, RECALL_MEDIA, receipt.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, command_digest,
                {"selection_ref": manifest_ref, "recall_ref": receipt_ref},
            )
        return manifest_ref, receipt_ref

    def validate_manifest_for_consumption(
        self, manifest_ref: str, *, snapshot_ref: str,
        purpose: str, recipient: str,
    ) -> tuple[RecallSelectionManifest, str]:
        """Rebuild exact ADVICE; invalidation holds the entire frozen case."""

        actor_id = self._authorize()
        return self._validate_frozen_manifest(
            manifest_ref, snapshot_ref=snapshot_ref, purpose=purpose,
            recipient=recipient, query_owner_id=actor_id,
        )

    def validate_frozen_manifest_for_approval(
        self, manifest_ref: str, *, snapshot_ref: str,
        purpose: str, recipient: str,
        expected_manifest_digest: str,
        action: Literal["approve", "execute"] = "execute",
    ) -> tuple[RecallSelectionManifest, str]:
        """Recheck an already selected manifest for a distinct Apply executor.

        The owner is recovered from the immutable manifest, never nominated by
        the executor. The private query and lesson text stay inside the verifier;
        this method must not be exposed as a response-producing route.
        """

        self._authorize()
        require_action(self.workspace, action)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_manifest_digest):
            raise IntegrityError("EXPERIENCE_APPROVAL_SELECTION_BINDING_REQUIRED")
        return self._validate_frozen_manifest(
            manifest_ref, snapshot_ref=snapshot_ref, purpose=purpose,
            recipient=recipient, query_owner_id=None,
            expected_manifest_digest=expected_manifest_digest,
        )

    def validate_frozen_manifest_for_evaluation(
        self, manifest_ref: str, *, snapshot_ref: str,
        purpose: str, recipient: str, expected_manifest_digest: str,
    ) -> tuple[RecallSelectionManifest, str]:
        """Recheck a frozen development selection under an independent evaluator.

        The evaluator cannot nominate the query owner or replace one lesson;
        this grants no business approve/execute action and exposes no route.
        """

        self._authorize()
        require_action(self.workspace, "govern")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_manifest_digest):
            raise IntegrityError("EXPERIENCE_EVALUATION_SELECTION_BINDING_REQUIRED")
        return self._validate_frozen_manifest(
            manifest_ref, snapshot_ref=snapshot_ref, purpose=purpose,
            recipient=recipient, query_owner_id=None,
            expected_manifest_digest=expected_manifest_digest,
        )

    def _validate_frozen_manifest(
        self, manifest_ref: str, *, snapshot_ref: str,
        purpose: str, recipient: str, query_owner_id: str | None,
        expected_manifest_digest: str | None = None,
    ) -> tuple[RecallSelectionManifest, str]:
        snapshot = self._snapshot(snapshot_ref)
        manifest = RecallSelectionManifest.model_validate(
            self.workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
        ).revalidated()
        if (
            manifest_ref != "experience-recall-selection:" + manifest.digest[7:] + "@v1"
            or manifest.snapshot_digest != snapshot.digest
            or manifest.tenant_id != snapshot.tenant_id
            or manifest.workspace_id != snapshot.workspace_id
            or manifest.profile_id != snapshot.profile_id
            or not manifest.query_owner_id
            or (query_owner_id is not None and manifest.query_owner_id != query_owner_id)
            or (expected_manifest_digest is not None and manifest.digest != expected_manifest_digest)
            or manifest.snapshot_lesson_count != len(snapshot.lesson_entries)
            or manifest.retrieval_version != snapshot.retrieval_version
            or manifest.index_revision != snapshot.index_revision
            or manifest.tokenizer_version != snapshot.tokenizer_version
            or manifest.ranker_version != snapshot.ranker_version
            or manifest.compiler_version != snapshot.compiler_version
            or manifest.budget_version != snapshot.budget_version
        ):
            raise IntegrityError("EXPERIENCE_MANIFEST_BINDING_INVALID")
        query = self.workspace.private_records.read_owned(
            manifest.query_ref, owner_id=manifest.query_owner_id,
            scope_ref=f"experience-recall:{manifest.case_id}:{manifest.case_revision}",
        )
        if query is None or sha256_digest(query) != manifest.query_digest:
            raise IntegrityError("EXPERIENCE_MEMORY_HOLD")
        # Recheck the entire frozen set; even an unselected lesson becoming
        # invalid changes the experiment's input distribution.
        frozen = {
            entry.lesson_ref: self._frozen_lesson(entry, purpose=purpose, recipient=recipient)
            for entry in snapshot.lesson_entries
        }
        if any(entry not in snapshot.lesson_entries for entry in manifest.selected_lessons):
            raise IntegrityError("EXPERIENCE_MANIFEST_SELECTION_NOT_IN_SNAPSHOT")
        selected = tuple(frozen[entry.lesson_ref] for entry in manifest.selected_lessons)
        advice_text = _advice(selected)
        if (
            exact_bytes_digest(advice_text.encode("utf-8")) != manifest.advice_bytes_digest
            or len(advice_text.encode("utf-8")) != manifest.advice_byte_count
            or _wire_size(selected, advice_text) > manifest.memory_reserved_bytes
        ):
            raise IntegrityError("EXPERIENCE_MANIFEST_ADVICE_CHANGED")
        return manifest, advice_text
