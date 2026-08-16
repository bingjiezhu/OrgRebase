"""Deterministic minimal-cover coalition planning."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations

from orgrebase.digest import sha256_digest
from orgrebase.workspace.models import (
    CoalitionCoverage,
    CoalitionPlan,
    DomainCapabilityCardVersion,
    HealthStatus,
    TaskRequest,
    TaskRequirementCandidate,
    TaskTemplateVersion,
)


class CoalitionPlanner:
    version = "workspace-coalition-exhaustive@1.0.0"

    def plan(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        candidates: tuple[TaskRequirementCandidate, ...],
        cards: tuple[DomainCapabilityCardVersion, ...],
        revision_lock: Mapping[str, str],
    ) -> CoalitionPlan:
        slot_specs = {item.slot_id: item for item in template.slots}
        candidate_slots = tuple(sorted({item.slot_id for item in candidates}))
        if not candidate_slots:
            raise ValueError("NO_ADMITTED_REQUIREMENT_SLOTS")
        for candidate in candidates:
            spec = slot_specs.get(candidate.slot_id)
            if spec is None:
                raise ValueError(f"EXTRA_REQUIREMENT_NOT_ALLOWED:{candidate.slot_id}")
            if candidate.proposed_domain_id != spec.domain_id:
                raise ValueError(f"REQUIREMENT_DOMAIN_MISMATCH:{candidate.slot_id}")
        healthy = tuple(
            sorted(
                (
                    card
                    for card in cards
                    if card.health_status == HealthStatus.ACTIVE
                    and task.purpose in card.allowed_purposes
                ),
                key=lambda item: item.domain_id,
            )
        )
        legal: list[tuple[tuple[int, int, tuple[str, ...]], tuple[DomainCapabilityCardVersion, ...]]] = []
        for size in range(1, len(healthy) + 1):
            for subset in combinations(healthy, size):
                supported = set().union(*(set(card.supported_slot_ids) for card in subset))
                if set(candidate_slots).issubset(supported):
                    key = (
                        len(subset),
                        sum(item.declared_cost for item in subset),
                        tuple(sorted(item.domain_id for item in subset)),
                    )
                    legal.append((key, subset))
        if not legal:
            raise RuntimeError("NO_LEGAL_COALITION")
        key, selected = min(legal, key=lambda item: item[0])
        card_by_domain = {item.domain_id: item for item in selected}
        coverage = tuple(
            CoalitionCoverage(
                slot_id=slot_id,
                card_ref=card_by_domain[slot_specs[slot_id].domain_id].ref,
                domain_id=slot_specs[slot_id].domain_id,
            )
            for slot_id in candidate_slots
        )
        selected_refs = tuple(sorted(item.ref for item in selected))
        input_digest = sha256_digest(
            {
                "task": task.digest,
                "template": template.digest,
                "candidates": sorted(item.digest for item in candidates),
                "cards": sorted(item.digest for item in cards),
                "revision_lock": dict(sorted(revision_lock.items())),
            }
        )
        return CoalitionPlan(
            id=f"coalition:{task.id.split(':')[-1]}@v1",
            task_ref=task.id,
            template_ref=template.ref,
            admitted_slot_ids=candidate_slots,
            selected_card_refs=selected_refs,
            coverage=coverage,
            total_declared_cost=key[1],
            tie_break_tuple=key,
            planner_version=self.version,
            revision_lock=dict(revision_lock),
            input_digest=input_digest,
        )
