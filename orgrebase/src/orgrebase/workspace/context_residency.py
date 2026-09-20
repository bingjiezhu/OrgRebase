"""Task-agent context residency without copying enterprise business values.

The task agent receives one content-addressed envelope.  The envelope contains
only references and digests for the admitted task, minimal coalition, selected
domain capability cards, task context, and least-authority actor projections.
It never embeds source values, claims, policies, credentials, or canonical
write authority.

``TaskAgentContextEnvelopeVerifier`` is intentionally independent from the
builder.  A runtime must resolve the referenced objects from its trusted store
and verify them again immediately before delegation.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.models import (
    ActorContextProjection,
    CoalitionPlan,
    DomainCapabilityCardVersion,
    HealthStatus,
    TaskContextManifest,
    TaskRequest,
)

_ERROR = "TASK_AGENT_CONTEXT_ENVELOPE_INVALID"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DomainContextResidencyBinding(_FrozenModel):
    """Digest-only binding from one authority domain to its task projection."""

    domain_id: str
    capability_card_ref: str
    capability_card_digest: str
    actor_projection_ref: str
    actor_projection_digest: str

    @model_validator(mode="after")
    def validate_digests(self) -> Self:
        for value in (self.capability_card_digest, self.actor_projection_digest):
            if not _DIGEST_PATTERN.fullmatch(value):
                raise ValueError("context residency binding digest is invalid")
        return self


class TaskAgentContextEnvelope(ContentAddressedModel):
    """Minimum context identity the task agent may use for one coalition.

    Business values remain in the admitted task context and actor projections.
    The envelope only binds their identities, a separately admitted
    organizational-intent digest, and a fail-closed effect ceiling.
    """

    schema_version: Literal["orgrebase.task-agent-context-envelope.v1"] = (
        "orgrebase.task-agent-context-envelope.v1"
    )
    id: str
    version: Literal["v1"] = "v1"
    task_ref: str
    task_digest: str
    admitted_organizational_intent_digest: str
    task_formation_decision_receipt_digest: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    coalition_plan_ref: str
    coalition_plan_digest: str
    task_context_ref: str
    task_context_digest: str
    domain_bindings: tuple[DomainContextResidencyBinding, ...]
    revision_lock: dict[str, str]
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = "ZERO_EXTERNAL_EFFECTS"
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0
    created_at: str
    expires_at: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        digest_values = (
            self.task_digest,
            self.admitted_organizational_intent_digest,
            self.coalition_plan_digest,
            self.task_context_digest,
        )
        if any(not _DIGEST_PATTERN.fullmatch(value) for value in digest_values):
            raise ValueError("task-agent context digest is invalid")
        if self.task_formation_decision_receipt_digest is not None and not (
            _DIGEST_PATTERN.fullmatch(self.task_formation_decision_receipt_digest)
        ):
            raise ValueError("task formation decision receipt digest is invalid")
        domains = tuple(item.domain_id for item in self.domain_bindings)
        if not domains or domains != tuple(sorted(domains)) or len(domains) != len(set(domains)):
            raise ValueError("task-agent context domains must be non-empty, unique, and sorted")
        if not self.revision_lock or any(
            not key.strip() or not value.strip() for key, value in self.revision_lock.items()
        ):
            raise ValueError("task-agent context revision lock is invalid")
        return self


def _fail(reason: str) -> None:
    raise IntegrityError(f"{_ERROR}:{reason}")


def _revalidate[ModelT: BaseModel](
    value: ModelT,
    model_type: type[ModelT],
    reason: str,
) -> ModelT:
    try:
        return model_type.model_validate(value.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrityError(f"{_ERROR}:{reason}") from exc


def _timestamp(value: str, reason: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrityError(f"{_ERROR}:{reason}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(reason)
    return parsed


def _binding_expiry(
    *,
    manifest: TaskContextManifest,
    projections: tuple[ActorContextProjection, ...],
    selected_cards: tuple[DomainCapabilityCardVersion, ...],
) -> str:
    candidates = [manifest.expires_at]
    candidates.extend(item.expires_at for item in projections)
    candidates.extend(item.expires_at for item in manifest.slot_bindings)
    candidates.extend(item.valid_to for item in selected_cards if item.valid_to is not None)
    return min(candidates, key=lambda value: _timestamp(value, "EXPIRY_TIMESTAMP_INVALID"))


class TaskAgentContextEnvelopeVerifier:
    """Resolve-and-verify boundary for a task-agent context envelope."""

    version = "task-agent-context-envelope-verifier@1.0.0"

    @classmethod
    def verify(
        cls,
        *,
        envelope: TaskAgentContextEnvelope,
        task: TaskRequest,
        coalition: CoalitionPlan,
        capability_cards: tuple[DomainCapabilityCardVersion, ...],
        task_context: TaskContextManifest,
        actor_projections: tuple[ActorContextProjection, ...],
        expected_admitted_organizational_intent_digest: str,
        expected_task_formation_decision_receipt_digest: str | None = None,
        now: str,
    ) -> TaskAgentContextEnvelope:
        envelope = _revalidate(envelope, TaskAgentContextEnvelope, "ENVELOPE_MODEL_INVALID")
        task = _revalidate(task, TaskRequest, "TASK_MODEL_INVALID")
        coalition = _revalidate(coalition, CoalitionPlan, "COALITION_MODEL_INVALID")
        task_context = _revalidate(
            task_context,
            TaskContextManifest,
            "TASK_CONTEXT_MODEL_INVALID",
        )
        cards = tuple(
            _revalidate(item, DomainCapabilityCardVersion, "CAPABILITY_CARD_MODEL_INVALID")
            for item in capability_cards
        )
        projections = tuple(
            _revalidate(item, ActorContextProjection, "ACTOR_PROJECTION_MODEL_INVALID")
            for item in actor_projections
        )

        if not _DIGEST_PATTERN.fullmatch(expected_admitted_organizational_intent_digest):
            _fail("EXPECTED_INTENT_DIGEST_INVALID")
        if envelope.admitted_organizational_intent_digest != expected_admitted_organizational_intent_digest:
            _fail("ORGANIZATIONAL_INTENT_SUBSTITUTION")
        if envelope.task_formation_decision_receipt_digest != expected_task_formation_decision_receipt_digest:
            _fail("TASK_FORMATION_DECISION_RECEIPT_SUBSTITUTION")
        expected_id = f"task-agent-context:{task.id.split(':')[-1]}"
        if envelope.id != expected_id or envelope.task_ref != task.id or envelope.task_digest != task.digest:
            _fail("TASK_BINDING_MISMATCH")
        if (
            coalition.task_ref != task.id
            or coalition.template_ref != task.template_ref
            or envelope.coalition_plan_ref != coalition.id
            or envelope.coalition_plan_digest != coalition.digest
        ):
            _fail("COALITION_BINDING_MISMATCH")
        if (
            task_context.organization_id != task.organization_id
            or task_context.task_ref != task.id
            or task_context.template_ref != task.template_ref
            or task_context.coalition_plan_ref != coalition.id
            or envelope.task_context_ref != task_context.ref
            or envelope.task_context_digest != task_context.digest
        ):
            _fail("TASK_CONTEXT_SUBSTITUTION")
        if not (envelope.revision_lock == coalition.revision_lock == task_context.revision_lock):
            _fail("REVISION_LOCK_MISMATCH")

        selected_refs = set(coalition.selected_card_refs)
        card_by_ref: dict[str, DomainCapabilityCardVersion] = {}
        for card in cards:
            if card.ref in card_by_ref:
                _fail(f"DUPLICATE_CAPABILITY_CARD:{card.ref}")
            card_by_ref[card.ref] = card
        if not selected_refs or not selected_refs.issubset(card_by_ref):
            _fail("SELECTED_CAPABILITY_CARD_MISSING")
        selected_cards = tuple(card_by_ref[ref] for ref in sorted(selected_refs))

        coverage_domains = {item.domain_id for item in coalition.coverage}
        if {item.domain_id for item in selected_cards} != coverage_domains or len(selected_cards) != len(
            coverage_domains
        ):
            _fail("CAPABILITY_DOMAIN_SET_MISMATCH")
        binding_by_domain = {item.domain_id: item for item in envelope.domain_bindings}
        if set(binding_by_domain) != coverage_domains:
            _fail("ENVELOPE_DOMAIN_SET_MISMATCH")

        projection_by_ref: dict[str, ActorContextProjection] = {}
        for projection in projections:
            if projection.ref in projection_by_ref:
                _fail(f"DUPLICATE_ACTOR_PROJECTION:{projection.ref}")
            projection_by_ref[projection.ref] = projection

        domain_by_slot = {item.slot_id: item.domain_id for item in coalition.coverage}
        admitted_ref_domains: dict[str, set[str]] = {}
        for admitted in task_context.slot_bindings:
            domain = domain_by_slot.get(admitted.slot_id)
            if domain is None:
                _fail(f"CONTEXT_SLOT_OUTSIDE_COALITION:{admitted.slot_id}")
            admitted_ref_domains.setdefault(admitted.object_ref, set()).add(domain)
            if admitted.purpose != task.purpose:
                _fail(f"ADMITTED_PURPOSE_EXPANSION:{admitted.slot_id}")

        now_value = _timestamp(now, "NOW_TIMESTAMP_INVALID")
        created_at = _timestamp(envelope.created_at, "CREATED_AT_TIMESTAMP_INVALID")
        if created_at > now_value:
            _fail("ENVELOPE_CREATED_IN_FUTURE")

        bound_projections: list[ActorContextProjection] = []
        for card in selected_cards:
            binding = binding_by_domain[card.domain_id]
            if binding.capability_card_ref != card.ref or binding.capability_card_digest != card.digest:
                _fail(f"CAPABILITY_CARD_SUBSTITUTION:{card.domain_id}")
            if card.health_status != HealthStatus.ACTIVE:
                _fail(f"CAPABILITY_CARD_INACTIVE:{card.domain_id}")
            if task.purpose not in card.allowed_purposes:
                _fail(f"PURPOSE_PERMISSION_EXPANSION:{card.domain_id}")
            if _timestamp(card.valid_from, "CARD_VALID_FROM_INVALID") > now_value:
                _fail(f"CAPABILITY_CARD_NOT_YET_VALID:{card.domain_id}")
            if card.valid_to is not None and _timestamp(card.valid_to, "CARD_VALID_TO_INVALID") <= now_value:
                _fail(f"CAPABILITY_CARD_EXPIRED:{card.domain_id}")

            projection = projection_by_ref.get(binding.actor_projection_ref)
            if projection is None:
                _fail(f"ACTOR_PROJECTION_MISSING:{card.domain_id}")
            if binding.actor_projection_digest != projection.digest:
                _fail(f"ACTOR_PROJECTION_SUBSTITUTION:{card.domain_id}")
            if projection.ref not in task_context.actor_projection_refs:
                _fail(f"ACTOR_PROJECTION_NOT_ADMITTED:{card.domain_id}")
            if (
                projection.actor_id != card.worker_id
                or projection.task_context_ref != task_context.ref
                or projection.purpose != task.purpose
            ):
                _fail(f"ACTOR_PROJECTION_PERMISSION_EXPANSION:{card.domain_id}")

            expected_refs = {
                admitted.object_ref
                for admitted in task_context.slot_bindings
                if domain_by_slot[admitted.slot_id] == card.domain_id
            }
            actual_refs = set(projection.included_refs)
            if len(actual_refs) != len(projection.included_refs) or actual_refs != expected_refs:
                _fail(f"CROSS_DOMAIN_PROJECTION:{card.domain_id}")
            for object_ref in actual_refs:
                if admitted_ref_domains.get(object_ref) != {card.domain_id}:
                    _fail(f"AMBIGUOUS_PROJECTION_AUTHORITY:{card.domain_id}:{object_ref}")
            for admitted in task_context.slot_bindings:
                if admitted.object_ref in actual_refs and projection.actor_id not in admitted.recipient_ids:
                    _fail(f"RECIPIENT_PERMISSION_EXPANSION:{card.domain_id}")
            if _timestamp(projection.expires_at, "PROJECTION_EXPIRY_INVALID") <= now_value:
                _fail(f"ACTOR_PROJECTION_EXPIRED:{card.domain_id}")
            if _timestamp(projection.expires_at, "PROJECTION_EXPIRY_INVALID") > _timestamp(
                task_context.expires_at,
                "TASK_CONTEXT_EXPIRY_INVALID",
            ):
                _fail(f"PROJECTION_OUTLIVES_TASK_CONTEXT:{card.domain_id}")
            bound_projections.append(projection)

        if _timestamp(task_context.created_at, "TASK_CONTEXT_CREATED_AT_INVALID") > now_value:
            _fail("TASK_CONTEXT_CREATED_IN_FUTURE")
        if _timestamp(task_context.expires_at, "TASK_CONTEXT_EXPIRY_INVALID") <= now_value:
            _fail("TASK_CONTEXT_EXPIRED")
        for admitted in task_context.slot_bindings:
            if _timestamp(admitted.expires_at, "ADMITTED_REFERENCE_EXPIRY_INVALID") <= now_value:
                _fail(f"ADMITTED_REFERENCE_EXPIRED:{admitted.slot_id}")

        expected_expiry = _binding_expiry(
            manifest=task_context,
            projections=tuple(bound_projections),
            selected_cards=selected_cards,
        )
        if envelope.expires_at != expected_expiry:
            _fail("ENVELOPE_EXPIRY_EXPANSION")
        if _timestamp(envelope.expires_at, "ENVELOPE_EXPIRY_INVALID") <= now_value:
            _fail("ENVELOPE_EXPIRED")
        if (
            envelope.effect_ceiling != "ZERO_EXTERNAL_EFFECTS"
            or not envelope.candidate_only
            or envelope.canonical_target_writes != 0
        ):
            _fail("EFFECT_CEILING_EXPANSION")
        return envelope


class TaskAgentContextEnvelopeBuilder:
    """Compile only identity bindings; the verifier owns all authority checks."""

    version = "task-agent-context-envelope-builder@1.0.0"

    @classmethod
    def build(
        cls,
        *,
        task: TaskRequest,
        coalition: CoalitionPlan,
        capability_cards: tuple[DomainCapabilityCardVersion, ...],
        task_context: TaskContextManifest,
        actor_projections: tuple[ActorContextProjection, ...],
        admitted_organizational_intent_digest: str,
        task_formation_decision_receipt_digest: str | None = None,
        now: str,
    ) -> TaskAgentContextEnvelope:
        selected_refs = set(coalition.selected_card_refs)
        selected_cards = tuple(
            sorted(
                (item for item in capability_cards if item.ref in selected_refs),
                key=lambda item: item.domain_id,
            )
        )
        projection_by_actor = {item.actor_id: item for item in actor_projections}
        bound_projections = tuple(
            projection_by_actor[item.worker_id]
            for item in selected_cards
            if item.worker_id in projection_by_actor
        )
        bindings = tuple(
            DomainContextResidencyBinding(
                domain_id=card.domain_id,
                capability_card_ref=card.ref,
                capability_card_digest=card.digest,
                actor_projection_ref=projection_by_actor[card.worker_id].ref,
                actor_projection_digest=projection_by_actor[card.worker_id].digest,
            )
            for card in selected_cards
            if card.worker_id in projection_by_actor
        )
        envelope = TaskAgentContextEnvelope(
            id=f"task-agent-context:{task.id.split(':')[-1]}",
            task_ref=task.id,
            task_digest=task.digest,
            admitted_organizational_intent_digest=admitted_organizational_intent_digest,
            task_formation_decision_receipt_digest=(task_formation_decision_receipt_digest),
            coalition_plan_ref=coalition.id,
            coalition_plan_digest=coalition.digest,
            task_context_ref=task_context.ref,
            task_context_digest=task_context.digest,
            domain_bindings=bindings,
            revision_lock=dict(coalition.revision_lock),
            created_at=now,
            expires_at=_binding_expiry(
                manifest=task_context,
                projections=bound_projections,
                selected_cards=selected_cards,
            ),
        )
        return TaskAgentContextEnvelopeVerifier.verify(
            envelope=envelope,
            task=task,
            coalition=coalition,
            capability_cards=capability_cards,
            task_context=task_context,
            actor_projections=actor_projections,
            expected_admitted_organizational_intent_digest=(admitted_organizational_intent_digest),
            expected_task_formation_decision_receipt_digest=(task_formation_decision_receipt_digest),
            now=now,
        )


def build_task_agent_context_envelope(**kwargs: object) -> TaskAgentContextEnvelope:
    """Functional entry point for runtimes that do not keep builder instances."""

    return TaskAgentContextEnvelopeBuilder.build(**kwargs)  # type: ignore[arg-type]


def verify_task_agent_context_envelope(**kwargs: object) -> TaskAgentContextEnvelope:
    """Functional entry point for pre-delegation trust boundaries."""

    return TaskAgentContextEnvelopeVerifier.verify(**kwargs)  # type: ignore[arg-type]
