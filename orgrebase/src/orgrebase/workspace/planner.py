"""Deterministic coalition planning with one eligible provider per required domain."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime

from orgrebase.digest import sha256_digest
from orgrebase.domain import ObjectState
from orgrebase.workspace.models import (
    CoalitionCoverage,
    CoalitionPlan,
    DomainCapabilityCardVersion,
    HealthStatus,
    TaskRequest,
    TaskRequirementCandidate,
    TaskTemplateVersion,
)

# These are the current Domain executor's transport schemas, not the business
# value schemas of individual slots or the final deliverable's payload schema.
_INPUT_SCHEMA_REF = "schema:workspace.domain-delegation@v1"
_OUTPUT_SCHEMA_REF = "schema:workspace.domain-candidate-bundle@v1"


def _timestamp(value: str, *, error_code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(error_code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error_code)
    return parsed


def _validate_planning_problem(
    *,
    task: TaskRequest,
    template: TaskTemplateVersion,
    candidates: tuple[TaskRequirementCandidate, ...],
    cards: tuple[DomainCapabilityCardVersion, ...],
) -> tuple[datetime, dict[str, set[str]]]:
    if task.template_ref != template.ref:
        raise ValueError("TASK_TEMPLATE_REF_MISMATCH")
    if task.deliverable_kind != template.deliverable_kind:
        raise ValueError("TASK_TEMPLATE_DELIVERABLE_MISMATCH")
    if template.state != ObjectState.ACTIVE:
        raise ValueError("TASK_TEMPLATE_NOT_ACTIVE")
    requested_at = _timestamp(task.requested_at, error_code="INVALID_TASK_REQUESTED_AT")
    if not candidates:
        raise ValueError("NO_ADMITTED_REQUIREMENT_SLOTS")
    slot_specs = {item.slot_id: item for item in template.slots}
    seen_slots: set[str] = set()
    slots_by_domain: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        if candidate.task_ref != task.id:
            raise ValueError(f"REQUIREMENT_TASK_MISMATCH:{candidate.slot_id}")
        spec = slot_specs.get(candidate.slot_id)
        if spec is None:
            raise ValueError(f"EXTRA_REQUIREMENT_NOT_ALLOWED:{candidate.slot_id}")
        if candidate.proposed_domain_id != spec.domain_id:
            raise ValueError(f"REQUIREMENT_DOMAIN_MISMATCH:{candidate.slot_id}")
        if task.purpose not in spec.allowed_purposes:
            raise ValueError(f"REQUIREMENT_PURPOSE_MISMATCH:{candidate.slot_id}")
        if candidate.slot_id in seen_slots:
            raise ValueError(f"DUPLICATE_REQUIREMENT_SLOT:{candidate.slot_id}")
        seen_slots.add(candidate.slot_id)
        slots_by_domain[spec.domain_id].add(candidate.slot_id)
    missing = {item.slot_id for item in template.slots if item.required} - seen_slots
    if missing:
        raise ValueError(f"MISSING_REQUIRED_SLOTS:{','.join(sorted(missing))}")
    if len({card.ref for card in cards}) != len(cards):
        raise ValueError("CAPABILITY_CATALOG_DUPLICATE_REF")
    return requested_at, slots_by_domain


def _current_card(card: DomainCapabilityCardVersion, requested_at: datetime) -> bool:
    code = f"INVALID_PROVIDER_VALIDITY:{card.ref}"
    valid_from = _timestamp(card.valid_from, error_code=code)
    valid_to = _timestamp(card.valid_to, error_code=code) if card.valid_to is not None else None
    if valid_to is not None and valid_to <= valid_from:
        raise ValueError(code)
    # The end is exclusive. Planning uses the recorded request time for replay;
    # this is neither live execution authorization nor approval-expiry checking.
    return valid_from <= requested_at and (valid_to is None or requested_at < valid_to)


class CoalitionPlanner:
    version = "workspace-coalition-domain-binding@2.0.0"
    # A historical declaration can be read only when a verifier also recomputes
    # every binding under the current constraints. No old planning algorithm runs.
    supported_plan_versions = frozenset({version, "workspace-coalition-exhaustive@1.0.0"})

    def plan(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        candidates: tuple[TaskRequirementCandidate, ...],
        cards: tuple[DomainCapabilityCardVersion, ...],
        revision_lock: Mapping[str, str],
    ) -> CoalitionPlan:
        requested_at, slots_by_domain = _validate_planning_problem(
            task=task, template=template, candidates=candidates, cards=cards
        )
        eligible_by_domain: dict[str, list[DomainCapabilityCardVersion]] = defaultdict(list)
        for card in sorted(cards, key=lambda item: item.ref):
            slots = slots_by_domain.get(card.domain_id)
            if (
                not slots
                or not slots.intersection(card.supported_slot_ids)
                or card.health_status != HealthStatus.ACTIVE
                or task.purpose not in card.allowed_purposes
                or _INPUT_SCHEMA_REF not in card.accepted_input_schema_refs
                or _OUTPUT_SCHEMA_REF not in card.output_schema_refs
            ):
                continue
            if _current_card(card, requested_at):
                eligible_by_domain[card.domain_id].append(card)

        selected: list[DomainCapabilityCardVersion] = []
        bindings: list[CoalitionCoverage] = []
        for domain_id, slots in sorted(slots_by_domain.items()):
            providers = eligible_by_domain[domain_id]
            if not providers:
                raise RuntimeError(f"NO_PROVIDER:{domain_id}")
            if len(providers) != 1:
                raise RuntimeError(f"AMBIGUOUS_PROVIDER:{domain_id}")
            card = providers[0]
            if not slots.issubset(card.supported_slot_ids):
                raise RuntimeError(f"NO_PROVIDER:{domain_id}")
            selected.append(card)
            bindings.extend(
                CoalitionCoverage(slot_id=slot_id, card_ref=card.ref, domain_id=domain_id)
                for slot_id in sorted(slots)
            )

        coverage = tuple(sorted(bindings, key=lambda item: item.slot_id))
        candidate_slots = tuple(item.slot_id for item in coverage)
        selected_refs = tuple(sorted(item.ref for item in selected))
        # One card per domain is both sufficient and necessary in this policy.
        # Keep the existing cost summary, without pretending to rank ambiguous
        # providers or solve cross-domain set cover.
        key = (
            len(selected),
            sum(item.declared_cost for item in selected),
            tuple(sorted(slots_by_domain)),
        )
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
