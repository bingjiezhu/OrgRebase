"""Deterministic OrganizationalDemand-to-task formation receipt.

An Agent may propose an OAC ``OrganizationalDemand`` or candidate mapping, but
it does not decide which workers receive a task.  This module recompiles that
decision from admitted, content-addressed inputs and emits one independently
verifiable receipt.  Unknown obligations remain explicit instead of being
guessed into a nearby authority domain.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.models import (
    CandidateSource,
    CoalitionPlan,
    DomainCapabilityCardVersion,
    TaskRequest,
    TaskRequirementCandidate,
    TaskTemplateVersion,
)
from orgrebase.workspace.planner import CoalitionPlanner

_ERROR = "TASK_FORMATION_DECISION_RECEIPT_INVALID"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMPILER_VERSION = "organizational-demand-formation-compiler@1.0.0"
_CONTROL_REVIEW_REQUIREMENT = "control-requirement:independent-review"
_CONTROL_REVIEW_DOMAIN = "reviewer"


class EvidenceObligationFormationBinding(ContentAddressedModel):
    """One explicit causal edge from an OAC obligation to executable coverage."""

    evidence_obligation_ref: dict[str, Any]
    evidence_obligation_resource_id: str = Field(min_length=1)
    evidence_obligation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["MAPPED_DOMAIN", "MAPPED_CONTROL", "UNKNOWN"]
    requirement_refs: tuple[str, ...]
    domain_ids: tuple[str, ...]
    capability_card_refs: tuple[str, ...]
    capability_card_digests: tuple[str, ...]
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        ordered = (
            self.requirement_refs,
            self.domain_ids,
            self.capability_card_refs,
            self.capability_card_digests,
            self.reason_codes,
        )
        if any(values != tuple(sorted(set(values))) for values in ordered):
            raise ValueError("obligation binding values must be unique and sorted")
        if (
            self.evidence_obligation_ref.get("resourceId") != (self.evidence_obligation_resource_id)
            or self.evidence_obligation_ref.get("digest") != self.evidence_obligation_digest
        ):
            raise ValueError("obligation reference binding mismatch")
        if self.status == "MAPPED_DOMAIN":
            if (
                not self.requirement_refs
                or len(self.domain_ids) != 1
                or len(self.capability_card_refs) != 1
                or len(self.capability_card_digests) != 1
            ):
                raise ValueError("domain obligation binding is incomplete")
        elif self.status == "MAPPED_CONTROL":
            if (
                self.requirement_refs != (_CONTROL_REVIEW_REQUIREMENT,)
                or self.domain_ids != (_CONTROL_REVIEW_DOMAIN,)
                or self.capability_card_refs
                or self.capability_card_digests
            ):
                raise ValueError("control obligation must not impersonate a domain card")
        elif any(
            (
                self.requirement_refs,
                self.domain_ids,
                self.capability_card_refs,
                self.capability_card_digests,
            )
        ):
            raise ValueError("unknown obligation must not gain inferred authority")
        return self


class TaskFormationDecisionReceipt(ContentAddressedModel):
    """Candidate-only, deterministic explanation of one minimum coalition."""

    schema_version: Literal["orgrebase.task-formation-decision-receipt.v1"] = (
        "orgrebase.task-formation-decision-receipt.v1"
    )
    id: str = Field(min_length=1)
    organization_snapshot_ref: str = Field(min_length=1)
    organization_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    organizational_demand_ref: str = Field(min_length=1)
    organizational_demand_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_ref: str = Field(min_length=1)
    task_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    template_ref: str = Field(min_length=1)
    template_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    coalition_plan_ref: str = Field(min_length=1)
    coalition_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capability_catalog_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_digests: dict[str, str] = Field(min_length=1)
    objective: str = Field(min_length=1)
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = "ZERO_EXTERNAL_EFFECTS"
    obligation_bindings: tuple[EvidenceObligationFormationBinding, ...] = Field(min_length=1)
    unknown_obligation_resource_ids: tuple[str, ...]
    selected_domain_ids: tuple[str, ...] = Field(min_length=1)
    selected_card_refs: tuple[str, ...] = Field(min_length=1)
    selected_card_digests: tuple[str, ...] = Field(min_length=1)
    requirement_obligation_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    compiler_version: Literal["organizational-demand-formation-compiler@1.0.0"] = _COMPILER_VERSION
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_receipt_shape(self) -> Self:
        for values in (
            self.unknown_obligation_resource_ids,
            self.selected_domain_ids,
            self.selected_card_refs,
            self.selected_card_digests,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("formation decision values must be unique and sorted")
        resource_ids = tuple(item.evidence_obligation_resource_id for item in self.obligation_bindings)
        if resource_ids != tuple(sorted(set(resource_ids))):
            raise ValueError("obligation bindings must be unique and sorted")
        expected_unknowns = tuple(
            item.evidence_obligation_resource_id
            for item in self.obligation_bindings
            if item.status == "UNKNOWN"
        )
        if self.unknown_obligation_resource_ids != expected_unknowns:
            raise ValueError("unknown obligation set mismatch")
        if self.requirement_obligation_set_digest != sha256_digest(
            [item.digest for item in self.obligation_bindings]
        ):
            raise ValueError("requirement obligation set digest mismatch")
        if tuple(self.policy_digests) != tuple(sorted(self.policy_digests)) or any(
            not key.strip() or not _DIGEST_PATTERN.fullmatch(value)
            for key, value in self.policy_digests.items()
        ):
            raise ValueError("policy digest map is invalid")
        return self


def demand_formation_policy_digest() -> str:
    """Digest of the small, portable mapping policy implemented below."""

    return sha256_digest(
        {
            "version": _COMPILER_VERSION,
            "rules": [
                "OPAQUE_OBLIGATION_REFS_ARE_NOT_GUESSED",
                "RESOURCE_ID_FINAL_SEGMENT_MAY_SELECT_EXACT_DOMAIN",
                "INDEPENDENT_REVIEW_IS_A_CONTROL_REQUIREMENT_NOT_A_DOMAIN_CARD",
                "COALITION_AND_CAPABILITY_COVERAGE_MUST_RECOMPUTE_EXACTLY",
                "UNKNOWN_IS_PRESERVED",
                "ZERO_EXTERNAL_EFFECTS",
            ],
        }
    )


def capability_catalog_digest(
    capability_cards: tuple[DomainCapabilityCardVersion, ...],
) -> str:
    """Content identity of the full discovered capability catalog."""

    ordered = sorted(
        ({"ref": item.ref, "digest": item.digest} for item in capability_cards),
        key=lambda item: item["ref"],
    )
    return sha256_digest(ordered)


def _fail(reason: str) -> None:
    raise IntegrityError(f"{_ERROR}:{reason}")


def _resource_ref(value: Mapping[str, Any], *, kind: str) -> str:
    metadata = value.get("metadata")
    if value.get("kind") != kind or not isinstance(metadata, Mapping):
        _fail(f"{kind.upper()}_RESOURCE_INVALID")
    resource_id = metadata.get("id")
    revision = metadata.get("revision")
    if not isinstance(resource_id, str) or not resource_id or not isinstance(revision, int):
        _fail(f"{kind.upper()}_RESOURCE_INVALID")
    return f"{resource_id}@r{revision}"


def _verified_resource_digest(value: Mapping[str, Any], *, code: str) -> str:
    """Bind an OAC digest already admitted by the public OAC trust boundary.

    OAC uses its own registered-model RFC 8785 projection, which is deliberately
    not reimplemented by this OrgRebase compiler.  The public OAC validator owns
    that admission; this deterministic recomputation binds the exact admitted digest
    and recomputes every downstream formation edge from the supplied resource.
    """

    digest = value.get("digest")
    if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
        _fail(code)
    return digest


def _compile(
    *,
    organization_snapshot: Mapping[str, Any],
    organizational_demand: Mapping[str, Any],
    task: TaskRequest,
    template: TaskTemplateVersion,
    coalition: CoalitionPlan,
    capability_cards: tuple[DomainCapabilityCardVersion, ...],
    authority_policy_digests: Mapping[str, str],
) -> TaskFormationDecisionReceipt:
    snapshot = dict(organization_snapshot)
    demand = dict(organizational_demand)
    snapshot_digest = _verified_resource_digest(snapshot, code="ORGANIZATION_SNAPSHOT_DIGEST_INVALID")
    demand_digest = _verified_resource_digest(demand, code="ORGANIZATIONAL_DEMAND_DIGEST_INVALID")
    snapshot_ref = _resource_ref(snapshot, kind="OrganizationSnapshot")
    demand_ref = _resource_ref(demand, kind="OrganizationalDemand")
    spec = demand.get("spec")
    if not isinstance(spec, Mapping):
        _fail("ORGANIZATIONAL_DEMAND_SPEC_INVALID")
    snapshot_binding = spec.get("snapshotRef")
    if (
        not isinstance(snapshot_binding, Mapping)
        or snapshot_binding.get("digest") != snapshot_digest
        or snapshot_binding.get("resourceId") != (snapshot.get("metadata") or {}).get("id")
    ):
        _fail("ORGANIZATION_SNAPSHOT_SUBSTITUTION")
    trigger_refs = spec.get("triggerRefs")
    if not isinstance(trigger_refs, list) or not any(
        isinstance(item, Mapping)
        and item.get("resourceId") == task.id
        and isinstance(item.get("digest"), str)
        and _DIGEST_PATTERN.fullmatch(item["digest"])
        for item in trigger_refs
    ):
        _fail("TASK_TRIGGER_BINDING_MISSING")
    objective = spec.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        _fail("OBJECTIVE_REQUIRED")
    if spec.get("effectCeiling") != "zero_effect":
        _fail("EFFECT_CEILING_EXPANSION")
    if (
        task.template_ref != template.ref
        or coalition.task_ref != task.id
        or coalition.template_ref != template.ref
        or coalition.admitted_slot_ids != tuple(sorted(item.slot_id for item in template.slots))
    ):
        _fail("TASK_TEMPLATE_COALITION_BINDING_MISMATCH")

    slot_by_id = {item.slot_id: item for item in template.slots}
    recomputed_coalition = CoalitionPlanner().plan(
        task=task,
        template=template,
        candidates=tuple(
            TaskRequirementCandidate(
                task_ref=task.id,
                slot_id=slot_id,
                proposed_domain_id=slot_by_id[slot_id].domain_id,
                rationale="DETERMINISTIC_FORMATION_RECEIPT_RECOMPUTATION",
                source=CandidateSource.TEMPLATE,
                candidate_sequence=index,
            )
            for index, slot_id in enumerate(coalition.admitted_slot_ids, start=1)
        ),
        cards=capability_cards,
        revision_lock=coalition.revision_lock,
    )
    if (
        coalition.planner_version not in CoalitionPlanner.supported_plan_versions
        or coalition.selected_card_refs != recomputed_coalition.selected_card_refs
        or coalition.coverage != recomputed_coalition.coverage
        or coalition.total_declared_cost != recomputed_coalition.total_declared_cost
        or coalition.tie_break_tuple != recomputed_coalition.tie_break_tuple
    ):
        _fail("COALITION_NOT_MINIMUM_RECOMPUTED_COVER")

    card_by_ref = {item.ref: item for item in capability_cards}
    if len(card_by_ref) != len(capability_cards):
        _fail("CAPABILITY_CATALOG_DUPLICATE_REF")
    try:
        selected_cards = tuple(card_by_ref[ref] for ref in coalition.selected_card_refs)
    except KeyError as exc:
        _fail("SELECTED_CAPABILITY_CARD_MISSING")
        raise AssertionError from exc
    selected_card_by_domain = {item.domain_id: item for item in selected_cards}
    if len(selected_card_by_domain) != len(selected_cards):
        _fail("SELECTED_CAPABILITY_DOMAIN_DUPLICATE")
    template_slot_by_id = {item.slot_id: item for item in template.slots}
    coverage_by_domain: dict[str, list[str]] = {}
    for coverage in coalition.coverage:
        slot = template_slot_by_id.get(coverage.slot_id)
        card = card_by_ref.get(coverage.card_ref)
        if (
            slot is None
            or card is None
            or coverage.domain_id != slot.domain_id
            or coverage.domain_id != card.domain_id
            or coverage.slot_id not in card.supported_slot_ids
        ):
            _fail(f"COALITION_COVERAGE_INVALID:{coverage.slot_id}")
        coverage_by_domain.setdefault(coverage.domain_id, []).append(coverage.slot_id)
    if set(coverage_by_domain) != set(selected_card_by_domain):
        _fail("SELECTED_DOMAIN_COVERAGE_MISMATCH")

    obligations = spec.get("evidenceObligationRefs")
    if not isinstance(obligations, list) or not obligations:
        _fail("EVIDENCE_OBLIGATIONS_REQUIRED")
    compiled: list[EvidenceObligationFormationBinding] = []
    for raw in obligations:
        if not isinstance(raw, Mapping):
            _fail("EVIDENCE_OBLIGATION_REF_INVALID")
        obligation_ref = dict(raw)
        resource_id = obligation_ref.get("resourceId")
        digest = obligation_ref.get("digest")
        if (
            obligation_ref.get("kind") != "EvidenceObligation"
            or not isinstance(resource_id, str)
            or not resource_id
            or not isinstance(digest, str)
            or not _DIGEST_PATTERN.fullmatch(digest)
        ):
            _fail("EVIDENCE_OBLIGATION_REF_INVALID")
        semantic_key = resource_id.rsplit(":", 1)[-1]
        if semantic_key in selected_card_by_domain:
            card = selected_card_by_domain[semantic_key]
            requirements = tuple(sorted(coverage_by_domain[semantic_key]))
            binding = EvidenceObligationFormationBinding(
                evidence_obligation_ref=obligation_ref,
                evidence_obligation_resource_id=resource_id,
                evidence_obligation_digest=digest,
                status="MAPPED_DOMAIN",
                requirement_refs=requirements,
                domain_ids=(semantic_key,),
                capability_card_refs=(card.ref,),
                capability_card_digests=(card.digest,),
                reason_codes=("EXACT_DOMAIN_SUFFIX_AND_COVERAGE_MATCH",),
            )
        elif semantic_key == "independent-review":
            binding = EvidenceObligationFormationBinding(
                evidence_obligation_ref=obligation_ref,
                evidence_obligation_resource_id=resource_id,
                evidence_obligation_digest=digest,
                status="MAPPED_CONTROL",
                requirement_refs=(_CONTROL_REVIEW_REQUIREMENT,),
                domain_ids=(_CONTROL_REVIEW_DOMAIN,),
                capability_card_refs=(),
                capability_card_digests=(),
                reason_codes=("INDEPENDENT_REVIEWER_TASK_REQUIRED",),
            )
        else:
            binding = EvidenceObligationFormationBinding(
                evidence_obligation_ref=obligation_ref,
                evidence_obligation_resource_id=resource_id,
                evidence_obligation_digest=digest,
                status="UNKNOWN",
                requirement_refs=(),
                domain_ids=(),
                capability_card_refs=(),
                capability_card_digests=(),
                reason_codes=("NO_DETERMINISTIC_OBLIGATION_RULE",),
            )
        compiled.append(binding)
    bindings = tuple(sorted(compiled, key=lambda item: item.evidence_obligation_resource_id))
    if len(bindings) != len({item.evidence_obligation_resource_id for item in bindings}):
        _fail("EVIDENCE_OBLIGATION_DUPLICATE")

    policy_digests = {
        "demand_formation": demand_formation_policy_digest(),
        **dict(authority_policy_digests),
    }
    policy_digests = dict(sorted(policy_digests.items()))
    return TaskFormationDecisionReceipt(
        id=f"task-formation-decision:{task.id.split(':')[-1]}",
        organization_snapshot_ref=snapshot_ref,
        organization_snapshot_digest=snapshot_digest,
        organizational_demand_ref=demand_ref,
        organizational_demand_digest=demand_digest,
        task_ref=task.id,
        task_digest=task.digest,
        template_ref=template.ref,
        template_digest=template.digest,
        coalition_plan_ref=coalition.id,
        coalition_plan_digest=coalition.digest,
        capability_catalog_digest=capability_catalog_digest(capability_cards),
        policy_digests=policy_digests,
        objective=objective.strip(),
        obligation_bindings=bindings,
        unknown_obligation_resource_ids=tuple(
            item.evidence_obligation_resource_id for item in bindings if item.status == "UNKNOWN"
        ),
        selected_domain_ids=tuple(sorted(selected_card_by_domain)),
        selected_card_refs=tuple(sorted(item.ref for item in selected_cards)),
        selected_card_digests=tuple(sorted(item.digest for item in selected_cards)),
        requirement_obligation_set_digest=sha256_digest([item.digest for item in bindings]),
    )


class TaskFormationDecisionReceiptVerifier:
    """Same-implementation deterministic recomputation.

    The supplied receipt never controls the result.  This detects receipt
    mutation but does not claim an implementation-independent oracle.
    """

    version = "task-formation-decision-receipt-verifier@1.0.0"

    @classmethod
    def verify(
        cls,
        *,
        receipt: TaskFormationDecisionReceipt,
        organization_snapshot: Mapping[str, Any],
        organizational_demand: Mapping[str, Any],
        task: TaskRequest,
        template: TaskTemplateVersion,
        coalition: CoalitionPlan,
        capability_cards: tuple[DomainCapabilityCardVersion, ...],
        authority_policy_digests: Mapping[str, str],
    ) -> TaskFormationDecisionReceipt:
        try:
            selected = TaskFormationDecisionReceipt.model_validate(receipt.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityError(f"{_ERROR}:RECEIPT_MODEL_INVALID") from exc
        expected = _compile(
            organization_snapshot=organization_snapshot,
            organizational_demand=organizational_demand,
            task=task,
            template=template,
            coalition=coalition,
            capability_cards=capability_cards,
            authority_policy_digests=authority_policy_digests,
        )
        if selected.digest != expected.digest or selected != expected:
            _fail("INDEPENDENT_RECOMPUTATION_MISMATCH")
        return selected


def build_task_formation_decision_receipt(
    **kwargs: Any,
) -> TaskFormationDecisionReceipt:
    """Compile and independently verify one decision receipt."""

    receipt = _compile(**kwargs)
    return TaskFormationDecisionReceiptVerifier.verify(receipt=receipt, **kwargs)


def verify_task_formation_decision_receipt(
    **kwargs: Any,
) -> TaskFormationDecisionReceipt:
    return TaskFormationDecisionReceiptVerifier.verify(**kwargs)


__all__ = (
    "EvidenceObligationFormationBinding",
    "TaskFormationDecisionReceipt",
    "TaskFormationDecisionReceiptVerifier",
    "build_task_formation_decision_receipt",
    "capability_catalog_digest",
    "demand_formation_policy_digest",
    "verify_task_formation_decision_receipt",
)
