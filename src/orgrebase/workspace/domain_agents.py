"""Least-authority deterministic Domain providers for the local Workspace profile."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass
from orgrebase.workspace.models import (
    ActorContextProjection,
    ClaimCandidate,
    ClaimScope,
    CoalitionPlan,
    DomainCandidateBundle,
    EvidenceSourceRef,
    SemanticKind,
    TaskRequest,
    TaskTemplateVersion,
)


@dataclass(frozen=True)
class LocalSourceValue:
    slot_id: str
    object_ref: str
    domain_id: str
    value: object
    semantic_kind: SemanticKind
    authority_ref: str
    source_id: str
    source_version: str
    sensitivity: str = "INTERNAL"
    raw_private_value: str | None = None

    @property
    def source_digest(self) -> str:
        return sha256_digest(
            {
                "source_id": self.source_id,
                "source_version": self.source_version,
                "domain": self.domain_id,
                "value": self.value,
                "raw_private_value": self.raw_private_value,
            }
        )


def default_source_values() -> dict[str, LocalSourceValue]:
    return {
        "product_plan": LocalSourceValue(
            "product_plan",
            "claim:product.enterprise_plan@v4",
            "product",
            "enterprise-plan-v4",
            SemanticKind.CLAIM,
            "authority:product@v1",
            "source:product-catalog",
            "v4",
        ),
        "launch_date": LocalSourceValue(
            "launch_date",
            "claim:product.launch_date@v7",
            "product",
            "2026-09-01",
            SemanticKind.CLAIM,
            "authority:product@v1",
            "source:product-release-plan",
            "v12",
        ),
        "data_residency": LocalSourceValue(
            "data_residency",
            "claim:product.residency_capability@v3",
            "product",
            "US region supported",
            SemanticKind.CLAIM,
            "authority:product@v1",
            "source:platform-capability",
            "v3",
        ),
        "notice_required": LocalSourceValue(
            "notice_required",
            "claim:legal.customer_notice_required@v3",
            "legal",
            True,
            SemanticKind.CLAIM,
            "authority:legal@v1",
            "source:legal.customer-contract",
            "v4",
            raw_private_value=(
                "SYNTHETIC RESTRICTED CONTRACT: notify customer before material launch-date changes"
            ),
        ),
        "price_band": LocalSourceValue(
            "price_band",
            "policy:finance.price_band@v7",
            "finance",
            "strategic",
            SemanticKind.POLICY,
            "authority:finance@v1",
            "source:finance-pricing-policy",
            "v7",
            sensitivity="CONFIDENTIAL",
            raw_private_value="SYNTHETIC INTERNAL COST FLOOR 70000",
        ),
        "currency": LocalSourceValue(
            "currency",
            "policy:finance.currency@v1",
            "finance",
            "USD",
            SemanticKind.POLICY,
            "authority:finance@v1",
            "source:finance-currency-policy",
            "v1",
            sensitivity="CONFIDENTIAL",
        ),
        "partner_terms": LocalSourceValue(
            "partner_terms",
            "claim:gtm.partner_terms@v2",
            "gtm",
            "legal-review",
            SemanticKind.CLAIM,
            "authority:gtm@v1",
            "source:gtm-partner-policy",
            "v2",
        ),
        "quote_compose_skill": LocalSourceValue(
            "quote_compose_skill",
            "skill:enterprise-quote-compose@1.0",
            "gtm",
            "skill:enterprise-quote-compose@1.0",
            SemanticKind.SKILL,
            "authority:skill-registry@v1",
            "source:skill-registry",
            "r1",
        ),
        "public_message": LocalSourceValue(
            "public_message",
            "claim:gtm.public_launch_message@v1",
            "gtm",
            "Enterprise launch remains on the approved schedule.",
            SemanticKind.CLAIM,
            "authority:gtm@v1",
            "source:gtm-public-message",
            "v1",
            sensitivity="PUBLIC",
        ),
    }


class DeterministicDomainProvider:
    def __init__(
        self,
        domain_id: str,
        worker_id: str,
        source_values: Mapping[str, LocalSourceValue],
    ) -> None:
        self.domain_id = domain_id
        self.worker_id = worker_id
        self._values = source_values

    def source_projection(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        plan: CoalitionPlan,
        slot_ids: tuple[str, ...],
        now: str,
    ) -> ActorContextProjection:
        included = tuple(sorted(self._values[slot].object_ref for slot in slot_ids))
        excluded: list[dict[str, str]] = []
        if self.domain_id != "legal":
            excluded.append(
                {
                    "object_ref": "source:legal.customer-contract@v4",
                    "reason": "SENSITIVITY_CEILING_EXCEEDED",
                }
            )
        if self.domain_id != "finance":
            excluded.append(
                {
                    "object_ref": "policy:finance.internal_cost_floor@v1",
                    "reason": "NOT_REQUIRED_FOR_TASK",
                }
            )
        return ActorContextProjection(
            id=f"actor-context:{task.id.split(':')[-1]}:{self.domain_id}",
            version="v1",
            actor_id=self.worker_id,
            task_context_ref=f"task-context:{task.id.split(':')[-1]}@v1-pre-admission",
            purpose=task.purpose,
            included_refs=included,
            excluded=tuple(excluded),
            expires_at="2026-08-16T23:59:59Z",
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )

    def produce(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        plan: CoalitionPlan,
        slot_ids: tuple[str, ...],
        actor_projection: ActorContextProjection,
        now: str,
    ) -> tuple[tuple[ClaimCandidate, ...], DomainCandidateBundle]:
        if actor_projection.actor_id != self.worker_id:
            raise PermissionError("ACTOR_PROJECTION_MISMATCH")
        if not set(slot_ids).issubset({item.slot_id for item in plan.coverage}):
            raise ValueError("DELEGATION_SLOT_OUTSIDE_COALITION")
        candidates: list[ClaimCandidate] = []
        for slot_id in sorted(slot_ids):
            value = self._values.get(slot_id)
            if value is None or value.domain_id != self.domain_id:
                raise KeyError(f"DOMAIN_SOURCE_UNAVAILABLE:{self.domain_id}:{slot_id}")
            source = EvidenceSourceRef(
                source_id=value.source_id,
                source_version=value.source_version,
                source_digest=value.source_digest,
                locator_class="SYNTHETIC_LOCAL_SOURCE",
                observed_at=now,
                valid_from="2026-08-01T00:00:00Z",
                media_type="application/json",
            )
            candidates.append(
                ClaimCandidate(
                    candidate_id=f"candidate:{task.id.split(':')[-1]}:{slot_id}@v1",
                    semantic_kind=value.semantic_kind,
                    subject_ref=value.object_ref,
                    predicate=slot_id,
                    value=value.value,
                    value_schema_ref=f"schema:workspace.{slot_id}@v1",
                    issuer_domain_id=self.domain_id,
                    authority_ref=value.authority_ref,
                    source_refs=(source,),
                    temporal_state="CURRENT",
                    sensitivity=value.sensitivity,
                    claim_scope=ClaimScope.ORGANIZATION,
                    purpose=task.purpose,
                    recipients=("workspace-renderer", self.worker_id),
                    fresh_until="2026-08-16T23:59:59Z",
                    derived_from=(source.digest,) if self.domain_id == "legal" else (),
                    transformation_ref=(
                        "transform:legal-minimal-disclosure@v1" if self.domain_id == "legal" else None
                    ),
                    conflict_keys=(slot_id,),
                )
            )
        candidate_tuple = tuple(candidates)
        bundle = DomainCandidateBundle(
            id=f"domain-bundle:{task.id.split(':')[-1]}:{self.domain_id}@v1",
            task_ref=task.id,
            template_ref=template.ref,
            coalition_plan_ref=plan.id,
            domain_id=self.domain_id,
            worker_id=self.worker_id,
            candidate_refs=tuple(item.digest for item in candidate_tuple),
            candidate_set_digest=sha256_digest(sorted(item.digest for item in candidate_tuple)),
            transport_mode="LOCAL_DETERMINISTIC",
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        return candidate_tuple, bundle


class LocalDomainCandidateRegistry:
    def __init__(self, source_values: Mapping[str, LocalSourceValue] | None = None) -> None:
        self.source_values = dict(source_values or default_source_values())
        workers = {
            "product": "product-steward",
            "legal": "legal-steward",
            "finance": "finance-steward",
            "gtm": "gtm-steward",
        }
        self._providers = {
            domain: DeterministicDomainProvider(domain, worker, self.source_values)
            for domain, worker in workers.items()
        }

    def provider_for(self, domain_id: str) -> DeterministicDomainProvider:
        try:
            return self._providers[domain_id]
        except KeyError as exc:
            raise KeyError(f"UNKNOWN_DOMAIN_PROVIDER:{domain_id}") from exc

    def source_projections(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        plan: CoalitionPlan,
        now: str,
    ) -> tuple[ActorContextProjection, ...]:
        slots_by_domain: dict[str, list[str]] = {}
        for item in plan.coverage:
            slots_by_domain.setdefault(item.domain_id, []).append(item.slot_id)
        return tuple(
            self.provider_for(domain).source_projection(
                task=task,
                template=template,
                plan=plan,
                slot_ids=tuple(sorted(slots)),
                now=now,
            )
            for domain, slots in sorted(slots_by_domain.items())
        )

    def execute_selected(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        plan: CoalitionPlan,
        projections: tuple[ActorContextProjection, ...],
        now: str,
    ) -> tuple[tuple[ClaimCandidate, ...], tuple[DomainCandidateBundle, ...]]:
        projection_by_actor = {item.actor_id: item for item in projections}
        slots_by_domain: dict[str, list[str]] = {}
        for item in plan.coverage:
            slots_by_domain.setdefault(item.domain_id, []).append(item.slot_id)
        candidates: list[ClaimCandidate] = []
        bundles: list[DomainCandidateBundle] = []
        for domain, slots in sorted(slots_by_domain.items()):
            provider = self.provider_for(domain)
            projection = projection_by_actor.get(provider.worker_id)
            if projection is None:
                raise ValueError(f"MISSING_ACTOR_PROJECTION:{provider.worker_id}")
            produced, bundle = provider.produce(
                task=task,
                template=template,
                plan=plan,
                slot_ids=tuple(sorted(slots)),
                actor_projection=projection,
                now=now,
            )
            candidates.extend(produced)
            bundles.append(bundle)
        return tuple(candidates), tuple(bundles)
