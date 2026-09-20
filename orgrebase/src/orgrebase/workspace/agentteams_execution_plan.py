"""Compile admitted formation and context identities into an AgentTeams DAG.

This module is deliberately a pure, deterministic boundary.  It does not
create an AgentTeams project or mutate runtime state.  Instead it seals the
exact domain-task set, least-authority context bindings, schema contracts, and
one independent-review barrier that a transport adapter is allowed to create.

The formation receipt explains *why* a coalition exists.  The context envelope
limits *what* each domain worker may see.  This execution plan connects those
two independently admitted artifacts without allowing AgentTeams to become a
business-truth or canonical-write authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.context_residency import TaskAgentContextEnvelope
from orgrebase.workspace.demand_formation import TaskFormationDecisionReceipt
from orgrebase.workspace.models import (
    ActorContextProjection,
    CoalitionPlan,
    DomainCapabilityCardVersion,
)

_ERROR = "AGENTTEAMS_EXECUTION_PLAN_INVALID"
_COMPILER_VERSION = "agentteams-execution-plan-compiler@1.0.0"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVIEWER_OUTPUT_SCHEMA_REF = "schema:orgrebase.agentteams-review-candidate@v1"
_REVIEWER_OUTPUT_SCHEMA_DIGEST = sha256_digest(
    {
        "schema_ref": _REVIEWER_OUTPUT_SCHEMA_REF,
        "kind": "independent-review-candidate",
        "required": (
            "reviewed_task_ids",
            "verdict_candidate",
            "reason_codes",
            "candidate_only",
            "canonical_target_writes",
        ),
        "authority": {
            "candidate_only": True,
            "canonical_target_writes": 0,
            "control_plane_admission_required": True,
        },
    }
)


class AgentTeamsExecutionTask(ContentAddressedModel):
    """One exact task the AgentTeams transport may materialize."""

    task_id: str = Field(min_length=1)
    task_kind: Literal["DOMAIN", "REVIEWER_BARRIER"]
    domain_id: str = Field(min_length=1)
    assignee_actor_id: str = Field(min_length=1)
    depends_on: tuple[str, ...]
    formation_receipt_id: str = Field(min_length=1)
    formation_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_envelope_ref: str = Field(min_length=1)
    context_envelope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capability_card_ref: str | None = None
    capability_card_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    actor_projection_ref: str | None = None
    actor_projection_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    input_schema_refs: tuple[str, ...] = Field(min_length=1)
    input_schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_schema_refs: tuple[str, ...] = Field(min_length=1)
    output_schema_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = "ZERO_EXTERNAL_EFFECTS"
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_task_shape(self) -> Self:
        for values in (self.depends_on, self.input_schema_refs, self.output_schema_refs):
            if values != tuple(sorted(set(values))):
                raise ValueError("execution task sets must be unique and sorted")
        if self.task_id in self.depends_on:
            raise ValueError("execution task cannot depend on itself")
        domain_bindings = (
            self.capability_card_ref,
            self.capability_card_digest,
            self.actor_projection_ref,
            self.actor_projection_digest,
        )
        if self.task_kind == "DOMAIN":
            if self.depends_on or any(value is None for value in domain_bindings):
                raise ValueError("domain task must bind one card and actor projection")
        elif self.domain_id != "reviewer" or any(value is not None for value in domain_bindings):
            raise ValueError("reviewer barrier must not impersonate a domain authority")
        return self


class AgentTeamsExecutionPlan(ContentAddressedModel):
    """Sealed domain-task set plus a reviewer barrier; never runtime truth."""

    schema_version: Literal["orgrebase.agentteams-execution-plan.v1"] = (
        "orgrebase.agentteams-execution-plan.v1"
    )
    id: str = Field(min_length=1)
    task_ref: str = Field(min_length=1)
    task_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    formation_receipt_id: str = Field(min_length=1)
    formation_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_envelope_ref: str = Field(min_length=1)
    context_envelope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    coalition_plan_ref: str = Field(min_length=1)
    coalition_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selected_domain_ids: tuple[str, ...] = Field(min_length=1)
    tasks: tuple[AgentTeamsExecutionTask, ...] = Field(min_length=2)
    revision_lock: dict[str, str] = Field(min_length=1)
    compiler_version: Literal["agentteams-execution-plan-compiler@1.0.0"] = _COMPILER_VERSION
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = "ZERO_EXTERNAL_EFFECTS"
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_plan_shape(self) -> Self:
        if self.selected_domain_ids != tuple(sorted(set(self.selected_domain_ids))):
            raise ValueError("selected domains must be unique and sorted")
        if tuple(self.revision_lock) != tuple(sorted(self.revision_lock)) or any(
            not key.strip() or not value.strip() for key, value in self.revision_lock.items()
        ):
            raise ValueError("revision lock must be non-empty and sorted")
        task_ids = tuple(item.task_id for item in self.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("execution task IDs must be unique")
        domain_tasks = tuple(item for item in self.tasks if item.task_kind == "DOMAIN")
        reviewers = tuple(item for item in self.tasks if item.task_kind == "REVIEWER_BARRIER")
        if (
            tuple(item.domain_id for item in domain_tasks) != self.selected_domain_ids
            or len(domain_tasks) != len(self.selected_domain_ids)
            or len(reviewers) != 1
            or self.tasks != (*domain_tasks, reviewers[0])
            or reviewers[0].depends_on != tuple(item.task_id for item in domain_tasks)
        ):
            raise ValueError("execution task set must equal selected domains plus reviewer barrier")
        for item in self.tasks:
            if (
                item.formation_receipt_id != self.formation_receipt_id
                or item.formation_receipt_digest != self.formation_receipt_digest
                or item.context_envelope_ref != self.context_envelope_ref
                or item.context_envelope_digest != self.context_envelope_digest
            ):
                raise ValueError("execution task lineage must match plan lineage")
        return self


def _fail(reason: str) -> None:
    raise IntegrityError(f"{_ERROR}:{reason}")


def _revalidate[ModelT: ContentAddressedModel](
    value: ModelT,
    model_type: type[ModelT],
    reason: str,
) -> ModelT:
    try:
        return model_type.model_validate(value.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrityError(f"{_ERROR}:{reason}") from exc


def _task_id(*, task_ref: str, role: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")[:32] or "task"
    suffix = sha256_digest({"task_ref": task_ref, "role": role})[7:19]
    return f"at-{readable}-{suffix}"


def _schema_set_digest(
    refs: tuple[str, ...],
    schema_digests: Mapping[str, str],
    *,
    reason: str,
) -> str:
    if not refs or len(refs) != len(set(refs)):
        _fail(reason)
    ordered = tuple(sorted(refs))
    bindings: list[dict[str, str]] = []
    for ref in ordered:
        digest = schema_digests.get(ref)
        if not ref.strip() or not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            _fail(reason)
        bindings.append({"ref": ref, "digest": digest})
    return sha256_digest(bindings)


def _compile(
    *,
    formation_receipt: TaskFormationDecisionReceipt,
    context_envelope: TaskAgentContextEnvelope,
    coalition: CoalitionPlan,
    capability_cards: tuple[DomainCapabilityCardVersion, ...],
    actor_projections: tuple[ActorContextProjection, ...],
    schema_digests: Mapping[str, str],
) -> AgentTeamsExecutionPlan:
    formation_receipt = _revalidate(
        formation_receipt,
        TaskFormationDecisionReceipt,
        "FORMATION_RECEIPT_MODEL_INVALID",
    )
    context_envelope = _revalidate(
        context_envelope,
        TaskAgentContextEnvelope,
        "CONTEXT_ENVELOPE_MODEL_INVALID",
    )
    coalition = _revalidate(coalition, CoalitionPlan, "COALITION_MODEL_INVALID")
    cards = tuple(
        _revalidate(item, DomainCapabilityCardVersion, "CAPABILITY_CARD_MODEL_INVALID")
        for item in capability_cards
    )
    projections = tuple(
        _revalidate(item, ActorContextProjection, "ACTOR_PROJECTION_MODEL_INVALID")
        for item in actor_projections
    )

    if (
        formation_receipt.task_ref != context_envelope.task_ref
        or formation_receipt.task_ref != coalition.task_ref
        or formation_receipt.task_digest != context_envelope.task_digest
    ):
        _fail("TASK_LINEAGE_MISMATCH")
    if (
        formation_receipt.coalition_plan_ref != coalition.id
        or formation_receipt.coalition_plan_digest != coalition.digest
        or context_envelope.coalition_plan_ref != coalition.id
        or context_envelope.coalition_plan_digest != coalition.digest
    ):
        _fail("COALITION_LINEAGE_MISMATCH")
    if context_envelope.task_formation_decision_receipt_digest != formation_receipt.digest:
        _fail("FORMATION_CONTEXT_LINEAGE_MISMATCH")
    if (
        context_envelope.admitted_organizational_intent_digest
        != formation_receipt.organizational_demand_digest
    ):
        _fail("ORGANIZATIONAL_INTENT_LINEAGE_MISMATCH")
    if context_envelope.revision_lock != coalition.revision_lock:
        _fail("REVISION_LOCK_MISMATCH")
    if (
        formation_receipt.effect_ceiling != "ZERO_EXTERNAL_EFFECTS"
        or context_envelope.effect_ceiling != "ZERO_EXTERNAL_EFFECTS"
        or not formation_receipt.candidate_only
        or not context_envelope.candidate_only
        or formation_receipt.canonical_target_writes != 0
        or context_envelope.canonical_target_writes != 0
    ):
        _fail("EFFECT_CEILING_EXPANSION")
    if formation_receipt.unknown_obligation_resource_ids:
        _fail("UNKNOWN_FORMATION_OBLIGATION")
    review_controls = tuple(
        item
        for item in formation_receipt.obligation_bindings
        if item.status == "MAPPED_CONTROL"
        and item.requirement_refs == ("control-requirement:independent-review",)
        and item.domain_ids == ("reviewer",)
    )
    if len(review_controls) != 1:
        _fail("INDEPENDENT_REVIEW_CONTROL_MISSING")

    selected_domains = formation_receipt.selected_domain_ids
    if not selected_domains or selected_domains != tuple(sorted(set(selected_domains))):
        _fail("SELECTED_DOMAIN_SET_INVALID")
    envelope_domains = tuple(item.domain_id for item in context_envelope.domain_bindings)
    if selected_domains != coalition.selected_domain_ids or selected_domains != envelope_domains:
        _fail("SELECTED_DOMAIN_SET_MISMATCH")

    card_by_ref: dict[str, DomainCapabilityCardVersion] = {}
    for card in cards:
        if card.ref in card_by_ref:
            _fail(f"DUPLICATE_CAPABILITY_CARD:{card.ref}")
        card_by_ref[card.ref] = card
    try:
        selected_cards = tuple(card_by_ref[ref] for ref in coalition.selected_card_refs)
    except KeyError as exc:
        _fail("SELECTED_CAPABILITY_CARD_MISSING")
        raise AssertionError from exc
    card_by_domain = {item.domain_id: item for item in selected_cards}
    if len(card_by_domain) != len(selected_cards) or set(card_by_domain) != set(selected_domains):
        _fail("CAPABILITY_DOMAIN_SET_MISMATCH")
    if (
        tuple(sorted(item.ref for item in selected_cards)) != formation_receipt.selected_card_refs
        or tuple(sorted(item.digest for item in selected_cards)) != formation_receipt.selected_card_digests
    ):
        _fail("FORMATION_CAPABILITY_CARD_SUBSTITUTION")

    binding_by_domain = {item.domain_id: item for item in context_envelope.domain_bindings}
    if len(binding_by_domain) != len(context_envelope.domain_bindings):
        _fail("DUPLICATE_CONTEXT_DOMAIN_BINDING")
    projection_by_ref: dict[str, ActorContextProjection] = {}
    for projection in projections:
        if projection.ref in projection_by_ref:
            _fail(f"DUPLICATE_ACTOR_PROJECTION:{projection.ref}")
        projection_by_ref[projection.ref] = projection

    domain_tasks: list[AgentTeamsExecutionTask] = []
    for domain_id in selected_domains:
        card = card_by_domain[domain_id]
        binding = binding_by_domain[domain_id]
        if binding.capability_card_ref != card.ref or binding.capability_card_digest != card.digest:
            _fail(f"CONTEXT_CAPABILITY_CARD_SUBSTITUTION:{domain_id}")
        projection = projection_by_ref.get(binding.actor_projection_ref)
        if projection is None:
            _fail(f"ACTOR_PROJECTION_MISSING:{domain_id}")
        if (
            binding.actor_projection_digest != projection.digest
            or projection.actor_id != card.worker_id
            or projection.task_context_ref != context_envelope.task_context_ref
            or projection.purpose not in card.allowed_purposes
        ):
            _fail(f"CROSS_LINEAGE_ACTOR_PROJECTION:{domain_id}")
        input_refs = tuple(sorted(card.accepted_input_schema_refs))
        output_refs = tuple(sorted(card.output_schema_refs))
        domain_tasks.append(
            AgentTeamsExecutionTask(
                task_id=_task_id(task_ref=formation_receipt.task_ref, role=f"domain-{domain_id}"),
                task_kind="DOMAIN",
                domain_id=domain_id,
                assignee_actor_id=card.worker_id,
                depends_on=(),
                formation_receipt_id=formation_receipt.id,
                formation_receipt_digest=formation_receipt.digest,
                context_envelope_ref=context_envelope.ref,
                context_envelope_digest=context_envelope.digest,
                capability_card_ref=card.ref,
                capability_card_digest=card.digest,
                actor_projection_ref=projection.ref,
                actor_projection_digest=projection.digest,
                input_schema_refs=input_refs,
                input_schema_digest=_schema_set_digest(
                    input_refs,
                    schema_digests,
                    reason=f"INPUT_SCHEMA_BINDING_INVALID:{domain_id}",
                ),
                output_schema_refs=output_refs,
                output_schema_digest=_schema_set_digest(
                    output_refs,
                    schema_digests,
                    reason=f"OUTPUT_SCHEMA_BINDING_INVALID:{domain_id}",
                ),
            )
        )

    reviewer_dependencies = tuple(item.task_id for item in domain_tasks)
    reviewer_input_refs = tuple(sorted(f"agentteams-output:{item.task_id}" for item in domain_tasks))
    reviewer_input_digest = sha256_digest(
        [
            {
                "task_id": item.task_id,
                "domain_id": item.domain_id,
                "output_schema_digest": item.output_schema_digest,
            }
            for item in domain_tasks
        ]
    )
    reviewer = AgentTeamsExecutionTask(
        task_id=_task_id(task_ref=formation_receipt.task_ref, role="reviewer-barrier"),
        task_kind="REVIEWER_BARRIER",
        domain_id="reviewer",
        assignee_actor_id="independent-reviewer",
        depends_on=reviewer_dependencies,
        formation_receipt_id=formation_receipt.id,
        formation_receipt_digest=formation_receipt.digest,
        context_envelope_ref=context_envelope.ref,
        context_envelope_digest=context_envelope.digest,
        input_schema_refs=reviewer_input_refs,
        input_schema_digest=reviewer_input_digest,
        output_schema_refs=(_REVIEWER_OUTPUT_SCHEMA_REF,),
        output_schema_digest=_REVIEWER_OUTPUT_SCHEMA_DIGEST,
    )
    return AgentTeamsExecutionPlan(
        id=f"agentteams-execution-plan:{formation_receipt.task_ref.split(':')[-1]}",
        task_ref=formation_receipt.task_ref,
        task_digest=formation_receipt.task_digest,
        formation_receipt_id=formation_receipt.id,
        formation_receipt_digest=formation_receipt.digest,
        context_envelope_ref=context_envelope.ref,
        context_envelope_digest=context_envelope.digest,
        coalition_plan_ref=coalition.id,
        coalition_plan_digest=coalition.digest,
        selected_domain_ids=selected_domains,
        tasks=(*domain_tasks, reviewer),
        revision_lock=dict(sorted(coalition.revision_lock.items())),
    )


class AgentTeamsExecutionPlanVerifier:
    """Same-implementation deterministic recomputation.

    A supplied plan never selects its own task set.  This catches mutated plan
    bytes, but it is not compiler-independent validation because both paths use
    the same normative compiler implementation.
    """

    version = "agentteams-execution-plan-verifier@1.0.0"

    @classmethod
    def verify(
        cls,
        *,
        plan: AgentTeamsExecutionPlan,
        formation_receipt: TaskFormationDecisionReceipt,
        context_envelope: TaskAgentContextEnvelope,
        coalition: CoalitionPlan,
        capability_cards: tuple[DomainCapabilityCardVersion, ...],
        actor_projections: tuple[ActorContextProjection, ...],
        schema_digests: Mapping[str, str],
    ) -> AgentTeamsExecutionPlan:
        selected = _revalidate(plan, AgentTeamsExecutionPlan, "PLAN_MODEL_INVALID")
        expected = _compile(
            formation_receipt=formation_receipt,
            context_envelope=context_envelope,
            coalition=coalition,
            capability_cards=capability_cards,
            actor_projections=actor_projections,
            schema_digests=schema_digests,
        )
        if selected.digest != expected.digest or selected != expected:
            _fail("INDEPENDENT_RECOMPUTATION_MISMATCH")
        return selected


def compile_agentteams_execution_plan(**kwargs: Any) -> AgentTeamsExecutionPlan:
    """Compile and deterministically recompute one transport-neutral task DAG."""

    plan = _compile(**kwargs)
    return AgentTeamsExecutionPlanVerifier.verify(plan=plan, **kwargs)


def verify_agentteams_execution_plan(**kwargs: Any) -> AgentTeamsExecutionPlan:
    return AgentTeamsExecutionPlanVerifier.verify(**kwargs)


__all__ = (
    "AgentTeamsExecutionPlan",
    "AgentTeamsExecutionPlanVerifier",
    "AgentTeamsExecutionTask",
    "compile_agentteams_execution_plan",
    "verify_agentteams_execution_plan",
)
