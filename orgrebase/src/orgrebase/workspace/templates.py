"""Versioned task templates and deterministic Domain capability cards."""

from __future__ import annotations

from orgrebase.domain import DependencyStrength, IntegrityError, ObjectState
from orgrebase.workspace.models import (
    ClaimScope,
    CoalitionPlan,
    DomainCapabilityCardVersion,
    ExtraRequirementPolicy,
    HealthStatus,
    RequirementSlotSpec,
    SemanticKind,
    TaskTemplateCatalogSummary,
    TaskTemplateVersion,
)


def _slot(
    slot_id: str,
    kind: SemanticKind,
    domain: str,
    *,
    required: bool = True,
    relation: str = "REQUIRES_CLAIM",
    strength: DependencyStrength = DependencyStrength.HARD,
    sensitivity: str = "INTERNAL",
    recipients: tuple[str, ...] = ("workspace-renderer",),
    output: tuple[str, ...] = (),
) -> RequirementSlotSpec:
    return RequirementSlotSpec(
        slot_id=slot_id,
        semantic_kind=kind,
        domain_id=domain,
        value_schema_ref=f"schema:workspace.{slot_id}@v1",
        required=required,
        relation=relation,
        strength=strength,
        claim_scope=ClaimScope.ORGANIZATION,
        freshness_seconds=86_400,
        sensitivity_ceiling=sensitivity,
        allowed_purposes=(
            "enterprise_quote",
            "public_launch_summary",
            "residency_faq",
            "discount_exception_memo",
            "change_rebase",
        ),
        allowed_recipients=recipients,
        output_field_paths=output or (slot_id,),
    )


def enterprise_quote_template() -> TaskTemplateVersion:
    slots = (
        _slot("product_plan", SemanticKind.CLAIM, "product"),
        _slot("launch_date", SemanticKind.CLAIM, "product"),
        _slot("data_residency", SemanticKind.CLAIM, "product"),
        _slot("notice_required", SemanticKind.CLAIM, "legal"),
        _slot(
            "price_band",
            SemanticKind.POLICY,
            "finance",
            relation="REQUIRES_POLICY",
            strength=DependencyStrength.REVIEW,
        ),
        _slot(
            "currency",
            SemanticKind.POLICY,
            "finance",
            relation="REQUIRES_POLICY",
        ),
        _slot(
            "partner_terms",
            SemanticKind.CLAIM,
            "gtm",
            strength=DependencyStrength.REVIEW,
            output=("partner_terms_code",),
        ),
        _slot(
            "quote_compose_skill",
            SemanticKind.SKILL,
            "gtm",
            relation="REQUIRES_SKILL",
            output=(),
        ),
    )
    return TaskTemplateVersion(
        id="template:enterprise_quote",
        version="v1",
        deliverable_kind="QUOTE",
        slots=slots,
        extra_requirement_policy=ExtraRequirementPolicy.REJECT,
        renderer_id="renderer:enterprise-quote",
        renderer_version="1.0.0",
        preservation_fields=(
            "owner",
            "customer_id",
            "product_plan",
            "data_residency",
            "notice_required",
            "price_band",
            "currency",
            "partner_terms_code",
        ),
        forbidden_fields=(
            "raw_contract_text",
            "internal_cost_floor",
            "secret",
            "private_source",
        ),
        output_schema_ref="schema:workspace.quote-payload@v1",
        state=ObjectState.ACTIVE,
    )


def public_launch_summary_template() -> TaskTemplateVersion:
    return TaskTemplateVersion(
        id="template:public_launch_summary",
        version="v1",
        deliverable_kind="PUBLIC_SUMMARY",
        slots=(
            _slot("product_plan", SemanticKind.CLAIM, "product"),
            _slot("launch_date", SemanticKind.CLAIM, "product"),
            _slot(
                "public_message",
                SemanticKind.CLAIM,
                "gtm",
                strength=DependencyStrength.INFORMATIONAL,
            ),
        ),
        extra_requirement_policy=ExtraRequirementPolicy.REJECT,
        renderer_id="renderer:public-launch-summary",
        renderer_version="1.0.0",
        preservation_fields=("customer_id", "product_plan"),
        forbidden_fields=("raw_contract_text", "internal_cost_floor", "price_band"),
        output_schema_ref="schema:workspace.public-summary@v1",
        state=ObjectState.ACTIVE,
    )


def priced_enterprise_quote_template() -> TaskTemplateVersion:
    """The priced contract uses the same governed renderer with two more inputs."""
    base = enterprise_quote_template()
    value = base.model_dump(mode="json", exclude={"digest"})
    value.update(
        version="v2",
        renderer_version="2.0.0",
        output_schema_ref="schema:workspace.quote-payload@v2",
        slots=[
            *value["slots"],
            _slot("quote_basket", SemanticKind.CLAIM, "product", output=("pricing",)).model_dump(mode="json"),
            _slot("pricing_policy", SemanticKind.POLICY, "finance", relation="REQUIRES_POLICY",
                  output=("pricing",)).model_dump(mode="json"),
        ],
    )
    return TaskTemplateVersion.model_validate(value)


def residency_faq_template() -> TaskTemplateVersion:
    return TaskTemplateVersion(
        id="template:residency_faq",
        version="v1",
        deliverable_kind="RESIDENCY_FAQ",
        slots=(
            _slot("data_residency", SemanticKind.CLAIM, "product"),
            _slot(
                "notice_required",
                SemanticKind.CLAIM,
                "legal",
                strength=DependencyStrength.REVIEW,
            ),
        ),
        extra_requirement_policy=ExtraRequirementPolicy.REJECT,
        renderer_id="renderer:residency-faq",
        renderer_version="1.0.0",
        preservation_fields=("customer_id",),
        forbidden_fields=("raw_contract_text", "internal_cost_floor"),
        output_schema_ref="schema:workspace.residency-faq@v1",
        state=ObjectState.ACTIVE,
    )


def discount_exception_template() -> TaskTemplateVersion:
    return TaskTemplateVersion(
        id="template:discount_exception_memo",
        version="v1",
        deliverable_kind="DISCOUNT_MEMO",
        slots=(
            _slot(
                "price_band",
                SemanticKind.POLICY,
                "finance",
                relation="REQUIRES_POLICY",
            ),
            _slot(
                "currency",
                SemanticKind.POLICY,
                "finance",
                relation="REQUIRES_POLICY",
            ),
            _slot(
                "partner_terms",
                SemanticKind.CLAIM,
                "gtm",
                strength=DependencyStrength.REVIEW,
            ),
            _slot(
                "notice_required",
                SemanticKind.CLAIM,
                "legal",
                strength=DependencyStrength.REVIEW,
            ),
        ),
        extra_requirement_policy=ExtraRequirementPolicy.REJECT,
        renderer_id="renderer:discount-exception",
        renderer_version="1.0.0",
        preservation_fields=("customer_id",),
        forbidden_fields=("raw_contract_text", "internal_cost_floor"),
        output_schema_ref="schema:workspace.discount-memo@v1",
        state=ObjectState.ACTIVE,
    )


class TemplateRegistry:
    def __init__(self, templates: tuple[TaskTemplateVersion, ...] | None = None) -> None:
        values = templates or (
            priced_enterprise_quote_template(),
            enterprise_quote_template(),
            public_launch_summary_template(),
            residency_faq_template(),
            discount_exception_template(),
        )
        self._templates = {item.ref: item for item in values}
        self._by_kind = {item.deliverable_kind: item for item in values}

    def get(self, template_ref: str) -> TaskTemplateVersion:
        try:
            return self._templates[template_ref]
        except KeyError as exc:
            raise KeyError(f"NO_TEMPLATE_MATCH:{template_ref}") from exc

    def by_deliverable_kind(self, deliverable_kind: str) -> TaskTemplateVersion:
        try:
            return self._by_kind[deliverable_kind]
        except KeyError as exc:
            raise KeyError(f"NO_TEMPLATE_MATCH:{deliverable_kind}") from exc

    def list_templates(self) -> TaskTemplateCatalogSummary:
        refs = tuple(sorted(self._templates))
        kinds = tuple(sorted(self._by_kind))
        return TaskTemplateCatalogSummary(
            id="template-catalog:workspace@v1",
            template_refs=refs,
            deliverable_kinds=kinds,
            catalog_digest=__import__("orgrebase.digest", fromlist=["sha256_digest"]).sha256_digest(
                {ref: self._templates[ref].digest for ref in refs}
            ),
        )

    def describe(self, template_ref: str) -> TaskTemplateVersion:
        return self.get(template_ref)

    @property
    def templates(self) -> tuple[TaskTemplateVersion, ...]:
        return tuple(self._templates[ref] for ref in sorted(self._templates))


def default_capability_cards(template_ref: str | None = None) -> tuple[DomainCapabilityCardVersion, ...]:
    common = {
        "accepted_input_schema_refs": ("schema:workspace.domain-delegation@v1",),
        "output_schema_refs": ("schema:workspace.domain-candidate-bundle@v1",),
        "allowed_tool_ids": ("workspace.domain.read@v1",),
        "allowed_purposes": (
            "enterprise_quote",
            "public_launch_summary",
            "residency_faq",
            "discount_exception_memo",
        ),
        "freshness_sla_seconds": 86_400,
        "health_status": HealthStatus.ACTIVE,
        "valid_from": "2026-08-15T00:00:00Z",
    }
    cards = (
        DomainCapabilityCardVersion(
            id="capability-card:product",
            version="v1",
            domain_id="product",
            worker_id="product-steward",
            authority_refs=("authority:product@v1",),
            capabilities=("read-product-claims", "propose-product-candidates"),
            supported_slot_ids=("product_plan", "launch_date", "data_residency"),
            resource_refs=("resource:product-catalog", "resource:release-plan"),
            disclosure_policy_ref="policy:product-disclosure@v1",
            declared_cost=2,
            **common,
        ),
        DomainCapabilityCardVersion(
            id="capability-card:legal",
            version="v1",
            domain_id="legal",
            worker_id="legal-steward",
            authority_refs=("authority:legal@v1",),
            capabilities=("read-restricted-legal", "derive-minimal-legal-claim"),
            supported_slot_ids=("notice_required",),
            resource_refs=("source:legal.customer-contract",),
            disclosure_policy_ref="policy:legal-minimal-disclosure@v1",
            declared_cost=4,
            **common,
        ),
        DomainCapabilityCardVersion(
            id="capability-card:finance",
            version="v1",
            domain_id="finance",
            worker_id="finance-steward",
            authority_refs=("authority:finance@v1",),
            capabilities=("read-finance-policy", "propose-price-candidates"),
            supported_slot_ids=("price_band", "currency"),
            resource_refs=("policy:finance.price-band", "policy:finance.currency"),
            disclosure_policy_ref="policy:finance-disclosure@v1",
            declared_cost=3,
            **common,
        ),
        DomainCapabilityCardVersion(
            id="capability-card:gtm",
            version="v1",
            domain_id="gtm",
            worker_id="gtm-steward",
            authority_refs=("authority:gtm@v1",),
            capabilities=("read-gtm-claims", "compose-customer-safe-candidate"),
            supported_slot_ids=(
                "partner_terms",
                "public_message",
                "quote_compose_skill",
            ),
            resource_refs=("resource:gtm-partner-policy", "skill:enterprise-quote-compose"),
            disclosure_policy_ref="policy:gtm-disclosure@v1",
            declared_cost=1,
            **common,
        ),
    )
    if template_ref != "template:enterprise_quote@v2":
        return cards
    additions = {
        "product": ("quote_basket", "claim:product.quote_basket"),
        "finance": ("pricing_policy", "policy:finance.pricing"),
    }
    result = []
    for card in cards:
        if card.domain_id not in additions:
            result.append(card)
            continue
        slot_id, resource_ref = additions[card.domain_id]
        value = card.model_dump(mode="json", exclude={"digest"})
        value.update(version="v2", supported_slot_ids=[*card.supported_slot_ids, slot_id],
                     resource_refs=[*card.resource_refs, resource_ref])
        result.append(DomainCapabilityCardVersion.model_validate(value))
    return tuple(result)


def selected_capability_cards(plan: CoalitionPlan) -> dict[str, DomainCapabilityCardVersion]:
    """Resolve the exact selected card versions and their declared slot ownership."""

    catalog = (default_capability_cards(plan.template_ref)
               if plan.template_ref == "template:enterprise_quote@v2" else default_capability_cards())
    by_ref = {card.ref: card for card in catalog}
    if len(by_ref) != len(catalog):
        raise IntegrityError("CAPABILITY_CATALOG_REFERENCE_DUPLICATE")
    selected: dict[str, DomainCapabilityCardVersion] = {}
    for ref in plan.selected_card_refs:
        card = by_ref.get(ref)
        if card is None:
            raise IntegrityError(f"CAPABILITY_CARD_UNKNOWN:{ref}")
        if card.domain_id in selected:
            raise IntegrityError(f"CAPABILITY_DOMAIN_DUPLICATE:{card.domain_id}")
        selected[card.domain_id] = card

    seen_slots: set[str] = set()
    covered_domains: set[str] = set()
    for coverage in plan.coverage:
        card = selected.get(coverage.domain_id)
        if card is None or coverage.card_ref != card.ref:
            raise IntegrityError(f"CAPABILITY_COVERAGE_AUTHORITY_MISMATCH:{coverage.slot_id}")
        if coverage.slot_id not in card.supported_slot_ids or coverage.slot_id in seen_slots:
            raise IntegrityError(f"CAPABILITY_SLOT_COVERAGE_INVALID:{coverage.slot_id}")
        seen_slots.add(coverage.slot_id)
        covered_domains.add(coverage.domain_id)
    if covered_domains != set(selected) or seen_slots != set(plan.admitted_slot_ids):
        raise IntegrityError("CAPABILITY_COALITION_COVERAGE_MISMATCH")
    return selected
