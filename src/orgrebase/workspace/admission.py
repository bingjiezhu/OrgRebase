"""Deterministic authority, scope, purpose and freshness admission."""

from __future__ import annotations

from orgrebase.workspace.models import (
    AdmissionDecision,
    AdmissionVerdict,
    ClaimCandidate,
    CoalitionPlan,
    TaskRequest,
    TaskTemplateVersion,
)


class AdmissionController:
    version = "workspace-admission@1.0.0"

    def admit(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        coalition: CoalitionPlan,
        candidates: tuple[ClaimCandidate, ...],
        now: str,
        policy_revision: str,
    ) -> tuple[AdmissionDecision, ...]:
        slots = {item.slot_id: item for item in template.slots}
        admitted_slots = set(coalition.admitted_slot_ids)
        seen: set[str] = set()
        decisions: list[AdmissionDecision] = []
        for candidate in sorted(candidates, key=lambda item: (item.predicate, item.candidate_id)):
            slot = slots.get(candidate.predicate)
            checks = {
                "slot_allowed": slot is not None and candidate.predicate in admitted_slots,
                "authority_domain": slot is not None and candidate.issuer_domain_id == slot.domain_id,
                "schema": slot is not None and candidate.value_schema_ref == slot.value_schema_ref,
                "fresh": candidate.temporal_state == "CURRENT" and candidate.fresh_until > now,
                "purpose": candidate.purpose == task.purpose
                and (slot is not None and task.purpose in slot.allowed_purposes),
                "recipient": "workspace-renderer" in candidate.recipients,
                "scope": (
                    candidate.claim_scope.value == "ORGANIZATION"
                    or candidate.task_ref == task.id
                ),
                "unique_slot": candidate.predicate not in seen,
            }
            seen.add(candidate.predicate)
            verdict = AdmissionVerdict.ADMIT if all(checks.values()) else AdmissionVerdict.REJECT
            reasons = tuple(sorted(key.upper() for key, passed in checks.items() if not passed)) or (
                "ALL_CHECKS_PASS",
            )
            decisions.append(
                AdmissionDecision(
                    id=f"admission:{candidate.candidate_id.split(':', 1)[1]}",
                    candidate_ref=candidate.digest,
                    verdict=verdict,
                    reason_codes=reasons,
                    checks=checks,
                    policy_revision=policy_revision,
                    decided_at=now,
                )
            )
        admitted = {
            candidate.predicate
            for candidate, decision in zip(
                sorted(candidates, key=lambda item: (item.predicate, item.candidate_id)),
                decisions,
                strict=True,
            )
            if decision.verdict == AdmissionVerdict.ADMIT
        }
        missing = admitted_slots - admitted
        if missing:
            raise RuntimeError("REQUIRED_ADMISSION_MISSING:" + ",".join(sorted(missing)))
        return tuple(decisions)
