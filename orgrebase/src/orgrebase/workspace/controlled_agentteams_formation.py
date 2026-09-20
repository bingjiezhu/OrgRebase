"""Typed commit proof for the controlled-local AgentTeams execution class.

This contract is intentionally distinct from both deterministic local replay
and distributed ``LIVE_AGENTTEAMS`` evidence.  It lets the canonical Formation
verifier admit exact candidates produced by the pinned TeamHarness control
plane and independent local executor processes without overstating that a
distributed production runtime was observed.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from orgrebase.domain import ContentAddressedModel
from orgrebase.workspace.models import ActorContextProjection

CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE = (
    "application/vnd.orgrebase.controlled-agentteams-formation-receipt+json"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOMAINS = frozenset({"product", "legal", "finance", "gtm"})
_ACTORS = frozenset(
    {
        "product-steward",
        "legal-steward",
        "finance-steward",
        "gtm-steward",
    }
)


class ControlledAgentTeamsFormationReceipt(ContentAddressedModel):
    """Exact zero-write roots required before controlled candidates can commit."""

    schema_version: Literal["orgrebase.controlled-agentteams-formation-receipt.v1"] = (
        "orgrebase.controlled-agentteams-formation-receipt.v1"
    )
    id: str
    run_id: str
    correlation_id: str
    project_id: str
    source_projections: tuple[ActorContextProjection, ...]
    task_binding_digests: tuple[str, ...]
    domain_result_digests: dict[str, str]
    manager_source_bindings: dict[str, dict[str, dict[str, str]]]
    task_purpose: str
    claim_candidate_digests: tuple[str, ...]
    domain_bundle_digests: tuple[str, ...]
    reviewer_input_digest: str
    reviewer_result_digest: str
    tool_receipt_digest: str
    tool_result_digest: str
    skill_invocation_receipt_digest: str
    source_verification_digest: str
    agentteams_action_digests: tuple[str, ...]
    agentteams_action_count: int = Field(ge=1)
    agentteams_execution_plan_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    planned_domain_ids: tuple[str, ...] = ()
    actual_agentteams_domain_ids: tuple[str, ...] = ()
    topology_match: bool | None = None
    reviewer_verdict: Literal["PASS"] = "PASS"
    project_terminal_state: Literal["completed"] = "completed"
    verified_at: str
    candidate_target_writes: Literal[0] = 0
    canonical_target_writes: Literal[0] = 0
    evidence_class: Literal["CONTROLLED_LOCAL_AGENTTEAMS"] = "CONTROLLED_LOCAL_AGENTTEAMS"
    claim_boundary: str

    @model_validator(mode="after")
    def verify_exact_roots(self) -> Self:
        scalar_digests = (
            self.reviewer_input_digest,
            self.reviewer_result_digest,
            self.tool_receipt_digest,
            self.tool_result_digest,
            self.skill_invocation_receipt_digest,
            self.source_verification_digest,
        )
        grouped_digests = (
            self.task_binding_digests,
            tuple(self.domain_result_digests.values()),
            self.claim_candidate_digests,
            self.domain_bundle_digests,
            self.agentteams_action_digests,
        )
        plan_bound = self.agentteams_execution_plan_digest is not None
        if plan_bound:
            if (
                self.planned_domain_ids != tuple(sorted(set(self.planned_domain_ids)))
                or self.actual_agentteams_domain_ids != tuple(sorted(set(self.actual_agentteams_domain_ids)))
                or self.planned_domain_ids != self.actual_agentteams_domain_ids
                or self.topology_match is not True
                or set(self.planned_domain_ids) != _DOMAINS
            ):
                raise ValueError("CONTROLLED_AGENTTEAMS_PLAN_TOPOLOGY_INVALID")
        elif self.planned_domain_ids or self.actual_agentteams_domain_ids or self.topology_match is not None:
            raise ValueError("CONTROLLED_AGENTTEAMS_PLAN_BINDING_INCOMPLETE")
        if (
            not self.run_id.strip()
            or not self.correlation_id.strip()
            or not self.project_id.strip()
            or not self.claim_boundary.strip()
            or set(self.domain_result_digests) != _DOMAINS
            or set(self.manager_source_bindings) != _DOMAINS
            or not self.task_purpose.strip()
            or any(not values for values in self.manager_source_bindings.values())
            or len(self.source_projections) != len(_DOMAINS)
            or {item.actor_id for item in self.source_projections} != _ACTORS
            or len({item.ref for item in self.source_projections}) != len(_DOMAINS)
            or len(self.task_binding_digests) != 7
            or len(set(self.task_binding_digests)) != 7
            or len(self.domain_bundle_digests) != len(_DOMAINS)
            or len(set(self.domain_bundle_digests)) != len(_DOMAINS)
            or len(set(self.claim_candidate_digests)) != len(self.claim_candidate_digests)
            or self.agentteams_action_count != len(self.agentteams_action_digests)
            or not all(_DIGEST.fullmatch(value) for value in scalar_digests)
            or any(
                not values or not all(_DIGEST.fullmatch(value) for value in values)
                for values in grouped_digests
            )
        ):
            raise ValueError("CONTROLLED_AGENTTEAMS_FORMATION_ROOTS_INVALID")
        return self


__all__ = [
    "CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE",
    "ControlledAgentTeamsFormationReceipt",
]
