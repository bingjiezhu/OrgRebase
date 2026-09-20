"""Task-scoped context and per-actor minimal disclosure projections."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from datetime import timedelta

from orgrebase.clock import timestamp, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, EvidenceClass, ObjectState, VersionedObject
from orgrebase.workspace.models import (
    ActorContextProjection,
    AdmissionDecision,
    AdmissionVerdict,
    AdmittedReference,
    ClaimCandidate,
    ClaimScope,
    CoalitionPlan,
    TaskContextManifest,
    TaskRequest,
    TaskTemplateVersion,
)
from orgrebase.workspace.read_dependencies import ReadDependencyValidator, require_read_dependency_validator
from orgrebase.workspace.templates import selected_capability_cards


def exclusion_reason_counts(projection: ActorContextProjection) -> dict[str, int]:
    """Summarize recorded exclusions without exposing source identifiers or text."""
    public_reasons = {
        "OTHER_AUTHORITY_DOMAIN",
        "MINIMAL_DISCLOSURE_DERIVATION_ONLY",
        "FORBIDDEN_OUTPUT_FIELD",
        "SENSITIVITY_CEILING_EXCEEDED",
        "NOT_REQUIRED_FOR_TASK",
    }
    counts = Counter(
        item.get("reason") if item.get("reason") in public_reasons else "OTHER"
        for item in projection.excluded
    )
    return dict(sorted(counts.items()))


class TaskContextCompiler:
    version = "workspace-task-context@2.1.0"

    def __init__(self, object_reader: Callable[[str, str | None], VersionedObject], *,
                 read_dependency_validator: ReadDependencyValidator | None = None) -> None:
        self.object_reader = object_reader
        self.read_dependency_validator = read_dependency_validator

    def _source_digest(self, candidate: ClaimCandidate) -> str:
        object_id, version = candidate.subject_ref.rsplit("@", 1)
        source = self.object_reader(object_id, version)
        if source.state not in {ObjectState.CURRENT, ObjectState.ACTIVE, ObjectState.CANARY}:
            raise ValueError("SOURCE_PREMISE_NOT_CURRENT")
        projected = source.ref if candidate.semantic_kind.value == "SKILL" else source.payload.get("canonical_value")
        if projected != candidate.value:
            raise ValueError("TASK_CONTEXT_SOURCE_VALUE_MISMATCH")
        return source.digest

    def compile(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        coalition: CoalitionPlan,
        decisions: tuple[AdmissionDecision, ...],
        candidates: tuple[ClaimCandidate, ...],
        now: str,
        revisions: Mapping[str, str],
        expires_at: str | None = None,
    ) -> tuple[TaskContextManifest, tuple[ActorContextProjection, ...]]:
        selected_cards = selected_capability_cards(coalition)
        context_expires_at = expires_at or timestamp(utc_datetime(now) + timedelta(seconds=900))
        if candidates:
            context_expires_at = min((context_expires_at, *(item.fresh_until for item in candidates)), key=utc_datetime)
        if utc_datetime(context_expires_at) <= utc_datetime(now):
            raise ValueError("TASK_CONTEXT_EXPIRY_NOT_IN_FUTURE")
        decision_by_ref = {item.candidate_ref: item for item in decisions}
        candidate_by_slot: dict[str, ClaimCandidate] = {}
        decision_ref_by_slot: dict[str, str] = {}
        for candidate in candidates:
            decision = decision_by_ref.get(candidate.digest)
            if decision is None or decision.verdict != AdmissionVerdict.ADMIT:
                continue
            if candidate.predicate in candidate_by_slot:
                raise ValueError(f"DUPLICATE_ADMITTED_SLOT:{candidate.predicate}")
            candidate_by_slot[candidate.predicate] = candidate
            decision_ref_by_slot[candidate.predicate] = decision.id
        slot_specs = {item.slot_id: item for item in template.slots}
        if any(
            item.slot_id not in slot_specs or slot_specs[item.slot_id].domain_id != item.domain_id
            for item in coalition.coverage
        ):
            raise ValueError("TASK_CONTEXT_DOMAIN_AUTHORITY_MISMATCH")
        if set(candidate_by_slot) != set(coalition.admitted_slot_ids):
            raise ValueError("TASK_CONTEXT_SLOT_SET_MISMATCH")
        premises = tuple(self.object_reader(*candidate.subject_ref.rsplit("@", 1))
                         for candidate in candidate_by_slot.values())
        require_read_dependency_validator(premises, now, self.read_dependency_validator)
        bindings = tuple(
            AdmittedReference(
                slot_id=slot_id,
                object_ref=candidate_by_slot[slot_id].subject_ref,
                object_digest=self._source_digest(candidate_by_slot[slot_id]),
                projection_schema_ref=candidate_by_slot[slot_id].value_schema_ref,
                projection=candidate_by_slot[slot_id].value,
                projection_digest=sha256_digest(candidate_by_slot[slot_id].value),
                relation=slot_specs[slot_id].relation,
                strength=slot_specs[slot_id].strength,
                semantic_kind=slot_specs[slot_id].semantic_kind,
                claim_scope=(
                    candidate_by_slot[slot_id].claim_scope
                    if candidate_by_slot[slot_id].claim_scope in {ClaimScope.ORGANIZATION, ClaimScope.TASK}
                    else None
                ),
                purpose=task.purpose,
                recipient_ids=tuple(sorted(candidate_by_slot[slot_id].recipients)),
                expires_at=candidate_by_slot[slot_id].fresh_until,
                admission_decision_ref=decision_ref_by_slot[slot_id],
            )
            for slot_id in sorted(candidate_by_slot)
        )
        context_id = f"task-context:{task.id.split(':')[-1]}"
        context_ref = f"{context_id}@v1"
        projections: list[ActorContextProjection] = []
        for domain, card in sorted(selected_cards.items()):
            if any(
                card.worker_id not in binding.recipient_ids
                for binding in bindings
                if slot_specs[binding.slot_id].domain_id == domain
            ):
                raise AuthorizationError(f"TASK_CONTEXT_WORKER_NOT_AUTHORIZED:{card.worker_id}")
            own_refs = tuple(
                binding.object_ref
                for binding in bindings
                if slot_specs[binding.slot_id].domain_id == domain
            )
            excluded = tuple(
                {
                    "object_ref": binding.object_ref,
                    "reason": "OTHER_AUTHORITY_DOMAIN",
                }
                for binding in bindings
                if slot_specs[binding.slot_id].domain_id != domain
            )
            projections.append(
                ActorContextProjection(
                    id=f"actor-context:{task.id.split(':')[-1]}:{domain}",
                    version="v2",
                    actor_id=card.worker_id,
                    task_context_ref=context_ref,
                    purpose=task.purpose,
                    included_refs=tuple(sorted(own_refs)),
                    excluded=excluded,
                    expires_at=context_expires_at,
                    evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
                )
            )
        projections.append(
            ActorContextProjection(
                id=f"actor-context:{task.id.split(':')[-1]}:renderer",
                version="v1",
                actor_id="workspace-renderer",
                task_context_ref=context_ref,
                purpose=task.purpose,
                included_refs=tuple(sorted(item.object_ref for item in bindings)),
                excluded=(),
                expires_at=context_expires_at,
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )
        )
        manifest = TaskContextManifest(
            source_digest_scheme="VERSIONED_OBJECT_SHA256_V1",
            id=context_id,
            version="v1",
            organization_id=task.organization_id,
            task_ref=task.id,
            template_ref=template.ref,
            coalition_plan_ref=coalition.id,
            revision_lock=dict(revisions),
            actor_projection_refs=tuple(item.ref for item in projections),
            slot_bindings=bindings,
            created_at=now,
            expires_at=context_expires_at,
        )
        return manifest, tuple(projections)

    @staticmethod
    def validate_used_premises(
        manifest: TaskContextManifest, premise_refs: tuple[str, ...]
    ) -> None:
        """Fail closed when a renderer cites a premise outside the compiled task context."""

        admitted = {item.object_ref for item in manifest.slot_bindings}
        unsupported = tuple(sorted(set(premise_refs) - admitted))
        if unsupported:
            raise AuthorizationError(
                "UNSUPPORTED_PREMISE:" + ",".join(unsupported)
            )
