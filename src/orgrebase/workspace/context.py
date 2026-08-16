"""Task-scoped context and per-actor minimal disclosure projections."""

from __future__ import annotations

from collections.abc import Mapping

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, EvidenceClass
from orgrebase.workspace.models import (
    ActorContextProjection,
    AdmittedReference,
    AdmissionDecision,
    AdmissionVerdict,
    ClaimCandidate,
    ClaimScope,
    CoalitionPlan,
    TaskContextManifest,
    TaskRequest,
    TaskTemplateVersion,
)


class TaskContextCompiler:
    version = "workspace-task-context@1.0.0"

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
    ) -> tuple[TaskContextManifest, tuple[ActorContextProjection, ...]]:
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
        if set(candidate_by_slot) != set(coalition.admitted_slot_ids):
            raise ValueError("TASK_CONTEXT_SLOT_SET_MISMATCH")
        bindings = tuple(
            AdmittedReference(
                slot_id=slot_id,
                object_ref=candidate_by_slot[slot_id].subject_ref,
                object_digest=sha256_digest(
                    {
                        "subject_ref": candidate_by_slot[slot_id].subject_ref,
                        "value": candidate_by_slot[slot_id].value,
                    }
                ),
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
        selected_domains = tuple(sorted({item.domain_id for item in coalition.coverage}))
        projections: list[ActorContextProjection] = []
        worker_by_domain = {
            "product": "product-steward",
            "legal": "legal-steward",
            "finance": "finance-steward",
            "gtm": "gtm-steward",
        }
        for domain in selected_domains:
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
                    actor_id=worker_by_domain[domain],
                    task_context_ref=context_ref,
                    purpose=task.purpose,
                    included_refs=tuple(sorted(own_refs)),
                    excluded=excluded,
                    expires_at="2026-08-16T23:59:59Z",
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
                excluded=(
                    {
                        "object_ref": "source:legal.customer-contract@v4",
                        "reason": "MINIMAL_DISCLOSURE_DERIVATION_ONLY",
                    },
                    {
                        "object_ref": "policy:finance.internal_cost_floor@v1",
                        "reason": "FORBIDDEN_OUTPUT_FIELD",
                    },
                ),
                expires_at="2026-08-16T23:59:59Z",
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )
        )
        manifest = TaskContextManifest(
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
            expires_at="2026-08-16T23:59:59Z",
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
