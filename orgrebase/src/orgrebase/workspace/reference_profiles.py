"""Shipped enterprise Seed/Profile constructors.

These deterministic constructors are fixtures/reference identities, not generic
enterprise profile generators.
"""

from __future__ import annotations

from orgrebase.workspace.profile_contracts import (
    REFERENCE_HANDLER_PROFILE,
    ChangeFamilyBinding,
    DefaultTaskBinding,
    EffectCeiling,
    EnterpriseSeedGap,
    EnterpriseSeedProfile,
    GovernanceBinding,
    GrowthLevel,
    ProfileLimitationCode,
    ReadinessGate,
    RuntimeCompatibilityDeclaration,
    RuntimeCompatibilityMode,
    SeedCompleteness,
    SeedComponent,
    SeedComponentKind,
    SeedDataClass,
    SeedGapKind,
    SeedInputValue,
    SeedSourceRoot,
)

VERACIER_SC008_SUBJECT_REF = "alternative:acieries-savoie"
VERACIER_SC008_DEMAND_REF = "demand:SC-008"
VERACIER_SC008_CHANGE_REF = "change:SC-008"
VERACIER_SC008_BEFORE_STATUS = "qualification_in_progress"
VERACIER_SC008_AFTER_STATUS = "qualified"


def northstar_acme_quote_profile() -> EnterpriseSeedProfile:
    """Return the exact profile supported by the current Quote handler."""

    from orgrebase.workspace.source_admission import (
        SOURCE_MEDIA_TYPE,
        component_declaration_digest,
        expected_raw_digest,
    )

    source_roots = tuple(
        SeedSourceRoot(
            id=f"source:northstar:{name}",
            revision="r1",
            media_type=SOURCE_MEDIA_TYPE,
            locator=f"packaged://northstar/{name}@r1",
            declared_digest=expected_raw_digest(f"packaged://northstar/{name}@r1"),
        )
        for name in ("domain", "knowledge", "authority", "capability", "dependency")
    )
    components = tuple(
        SeedComponent(
            kind=kind,
            completeness=SeedCompleteness.COMPLETE,
            source_root_refs=(f"source:northstar:{kind.value.lower()}",),
            declared_digest=component_declaration_digest(
                component_kind=kind,
                source_root_id=f"source:northstar:{kind.value.lower()}",
                revision="r1",
                media_type=SOURCE_MEDIA_TYPE,
                locator=f"packaged://northstar/{kind.value.lower()}@r1",
            ),
        )
        for kind in SeedComponentKind
    )
    return EnterpriseSeedProfile(
        profile_id="profile:northstar-acme-quote",
        revision="r1",
        scenario_id="northstar-acme-enterprise-quote",
        organization_id="org:northstar",
        synthetic=True,
        source_roots=source_roots,
        components=components,
        governance=GovernanceBinding(
            admission_authority_refs=("human:workspace-runtime-owner",),
            owner_refs=(
                "human:product-owner",
                "human:finance-owner",
                "employee:sales-owner",
            ),
            rejection_path_ref="procedure:workspace-intake-reject@r1",
            unknown_path_ref="procedure:workspace-unknown-review@r1",
        ),
        default_task=DefaultTaskBinding(
            id="task:quote_acme",
            actor_id="employee:sales-owner",
            purpose="enterprise_quote",
            deliverable_kind="QUOTE",
            requested_at="2026-08-15T00:00:00Z",
            template_ref="template:enterprise_quote@v1",
            input_values=(SeedInputValue(key="owner", value="sales-owner"),),
            customer_id="customer:acme",
            idempotency_key="workspace:form:quote-acme@v1",
        ),
        change_family=(
            ChangeFamilyBinding(
                kind="launch_date",
                owner_id="human:product-owner",
                change_id="changeset:workspace-launch-date",
                revision="r1",
                purpose="change_rebase",
                object_id="claim:product.launch_date",
                base_version="v7",
                proposed_version="v8",
                idempotency_key="workspace:apply:launch-date@r1",
            ),
            ChangeFamilyBinding(
                kind="currency",
                owner_id="human:finance-owner",
                change_id="changeset:workspace-currency",
                revision="r1",
                purpose="change_rebase",
                object_id="policy:finance.currency",
                base_version="v1",
                proposed_version="v2",
                idempotency_key="workspace:apply:currency@r1",
            ),
        ),
        effect_ceiling=EffectCeiling.ZERO_EXTERNAL_EFFECTS,
        data_class=SeedDataClass.SYNTHETIC_FIXTURE,
        requested_growth_level=GrowthLevel.G1_SELECT,
        runtime_compatibility=RuntimeCompatibilityDeclaration(
            mode=RuntimeCompatibilityMode.REFERENCE_HANDLER,
            handler_profile=REFERENCE_HANDLER_PROFILE,
        ),
        declared_limitation_codes=(
            ProfileLimitationCode.SYNTHETIC_DATA_ONLY,
            ProfileLimitationCode.NO_EXTERNAL_CONNECTORS,
            ProfileLimitationCode.NO_OUTCOME_ASSURANCE,
        ),
    )


def supplier_shadow_intake_profile() -> EnterpriseSeedProfile:
    """Return the immutable historical r1 fixture rooted at ``supplier:atlas``."""

    from orgrebase.workspace.source_admission import (
        SOURCE_MEDIA_TYPE,
        component_declaration_digest,
        expected_raw_digest,
    )

    source_roots = tuple(
        SeedSourceRoot(
            id=f"source:veracier:{name}",
            revision="r1",
            media_type=SOURCE_MEDIA_TYPE,
            locator=f"fixture://veracier/{name}@r1",
            declared_digest=expected_raw_digest(f"fixture://veracier/{name}@r1"),
        )
        for name in ("domain", "knowledge", "authority", "capability", "dependency")
    )
    components = tuple(
        SeedComponent(
            kind=kind,
            completeness=(
                SeedCompleteness.PARTIAL
                if kind == SeedComponentKind.DEPENDENCY
                else SeedCompleteness.COMPLETE
            ),
            source_root_refs=(f"source:veracier:{kind.value.lower()}",),
            declared_digest=component_declaration_digest(
                component_kind=kind,
                source_root_id=f"source:veracier:{kind.value.lower()}",
                revision="r1",
                media_type=SOURCE_MEDIA_TYPE,
                locator=f"fixture://veracier/{kind.value.lower()}@r1",
            ),
        )
        for kind in SeedComponentKind
    )
    gap = EnterpriseSeedGap(
        id="gap:veracier:historical-outcome-baseline",
        kind=SeedGapKind.MISSING_OUTCOME_BASELINE,
        component=SeedComponentKind.DEPENDENCY,
        path="dependencies.historicalOutcomeBaseline",
        description="Historical accepted outcome labels are not present in this public fixture.",
        owner_ref="human:veracier-procurement-owner",
        resolution_gate_ref="gate:veracier-outcome-assurance@r1",
        blocks=(ReadinessGate.OUTCOME_ASSURANCE,),
    )
    return EnterpriseSeedProfile(
        profile_id="profile:veracier-supplier-shadow",
        revision="r1",
        scenario_id="veracier-supplier-status-shadow",
        organization_id="org:veracier",
        synthetic=True,
        source_roots=source_roots,
        components=components,
        governance=GovernanceBinding(
            admission_authority_refs=("human:veracier-shadow-owner",),
            owner_refs=(
                "human:veracier-procurement-owner",
                "human:veracier-risk-owner",
            ),
            rejection_path_ref="procedure:veracier-intake-reject@r1",
            unknown_path_ref="procedure:veracier-unknown-review@r1",
        ),
        default_task=DefaultTaskBinding(
            id="task:supplier_status_review",
            actor_id="human:veracier-procurement-owner",
            purpose="supplier_status_review",
            deliverable_kind="SUPPLIER_STATUS_REVIEW",
            requested_at="2026-08-25T00:00:00Z",
            template_ref="template:supplier_status_review@v1",
            input_values=(SeedInputValue(key="supplier_id", value="supplier:atlas"),),
            customer_id=None,
            idempotency_key="veracier:shadow:supplier-status@r1",
        ),
        change_family=(
            ChangeFamilyBinding(
                kind="supplier_status",
                owner_id="human:veracier-procurement-owner",
                change_id="changeset:veracier-supplier-status",
                revision="r1",
                purpose="supplier_status_review",
                object_id="supplier:atlas.status",
                base_version="v4",
                proposed_version="v5",
                idempotency_key="veracier:shadow:supplier-status-change@r1",
            ),
        ),
        gaps=(gap,),
        effect_ceiling=EffectCeiling.ZERO_EXTERNAL_EFFECTS,
        data_class=SeedDataClass.SYNTHETIC_FIXTURE,
        requested_growth_level=GrowthLevel.G0_DESCRIBE,
        runtime_compatibility=RuntimeCompatibilityDeclaration(
            mode=RuntimeCompatibilityMode.INTAKE_ONLY,
        ),
        declared_limitation_codes=(
            ProfileLimitationCode.SYNTHETIC_DATA_ONLY,
            ProfileLimitationCode.INTAKE_ONLY,
            ProfileLimitationCode.PARTIAL_COVERAGE,
            ProfileLimitationCode.OPEN_GAPS_RETAINED,
            ProfileLimitationCode.NO_EXTERNAL_ENTERPRISE_VALIDATION,
            ProfileLimitationCode.NO_OUTCOME_ASSURANCE,
        ),
    )


def supplier_sc008_source_aligned_profile() -> EnterpriseSeedProfile:
    """Return the immutable r2 successor aligned to the exact OAC SC-008 roots.

    This is still an intake-only synthetic Profile.  Root alignment does not
    grant handler, Outcome, or production compatibility.
    """

    from orgrebase.workspace.source_admission import (
        SOURCE_MEDIA_TYPE,
        component_declaration_digest,
        expected_raw_digest,
    )

    source_roots = tuple(
        SeedSourceRoot(
            id=f"source:veracier:{name}",
            revision="r2",
            media_type=SOURCE_MEDIA_TYPE,
            locator=f"fixture://veracier/{name}@r2",
            declared_digest=expected_raw_digest(f"fixture://veracier/{name}@r2"),
        )
        for name in ("domain", "knowledge", "authority", "capability", "dependency")
    )
    components = tuple(
        SeedComponent(
            kind=kind,
            completeness=(
                SeedCompleteness.PARTIAL
                if kind == SeedComponentKind.DEPENDENCY
                else SeedCompleteness.COMPLETE
            ),
            source_root_refs=(f"source:veracier:{kind.value.lower()}",),
            declared_digest=component_declaration_digest(
                component_kind=kind,
                source_root_id=f"source:veracier:{kind.value.lower()}",
                revision="r2",
                media_type=SOURCE_MEDIA_TYPE,
                locator=f"fixture://veracier/{kind.value.lower()}@r2",
            ),
        )
        for kind in SeedComponentKind
    )
    gap = EnterpriseSeedGap(
        id="gap:veracier:historical-outcome-baseline",
        kind=SeedGapKind.MISSING_OUTCOME_BASELINE,
        component=SeedComponentKind.DEPENDENCY,
        path="dependencies.historicalOutcomeBaseline",
        description="Historical accepted outcome labels are not present in this public fixture.",
        owner_ref="human:veracier-procurement-owner",
        resolution_gate_ref="gate:veracier-outcome-assurance@r1",
        blocks=(ReadinessGate.OUTCOME_ASSURANCE,),
    )
    return EnterpriseSeedProfile(
        profile_id="profile:veracier-supplier-shadow",
        revision="r2",
        scenario_id="veracier-supplier-status-shadow",
        organization_id="org:veracier",
        synthetic=True,
        source_roots=source_roots,
        components=components,
        governance=GovernanceBinding(
            admission_authority_refs=("human:veracier-shadow-owner",),
            owner_refs=(
                "human:veracier-procurement-owner",
                "human:veracier-risk-owner",
            ),
            rejection_path_ref="procedure:veracier-intake-reject@r1",
            unknown_path_ref="procedure:veracier-unknown-review@r1",
        ),
        default_task=DefaultTaskBinding(
            id="task:supplier_status_review",
            actor_id="human:veracier-procurement-owner",
            purpose="supplier_status_review",
            deliverable_kind="SUPPLIER_STATUS_REVIEW",
            requested_at="2026-08-25T00:00:00Z",
            template_ref="template:supplier_status_review@v1",
            input_values=(
                SeedInputValue(key="supplier_id", value=VERACIER_SC008_SUBJECT_REF),
                SeedInputValue(key="demand_ref", value=VERACIER_SC008_DEMAND_REF),
            ),
            customer_id=None,
            idempotency_key="veracier:shadow:supplier-status@r2",
        ),
        change_family=(
            ChangeFamilyBinding(
                kind="supplier_status",
                owner_id="human:veracier-procurement-owner",
                change_id=VERACIER_SC008_CHANGE_REF,
                revision="r2",
                purpose="supplier_status_review",
                object_id=f"{VERACIER_SC008_SUBJECT_REF}.status",
                base_version=VERACIER_SC008_BEFORE_STATUS,
                proposed_version=VERACIER_SC008_AFTER_STATUS,
                idempotency_key="veracier:shadow:supplier-status-change@r2",
            ),
        ),
        gaps=(gap,),
        effect_ceiling=EffectCeiling.ZERO_EXTERNAL_EFFECTS,
        data_class=SeedDataClass.SYNTHETIC_FIXTURE,
        requested_growth_level=GrowthLevel.G0_DESCRIBE,
        runtime_compatibility=RuntimeCompatibilityDeclaration(
            mode=RuntimeCompatibilityMode.INTAKE_ONLY,
        ),
        declared_limitation_codes=(
            ProfileLimitationCode.SYNTHETIC_DATA_ONLY,
            ProfileLimitationCode.INTAKE_ONLY,
            ProfileLimitationCode.PARTIAL_COVERAGE,
            ProfileLimitationCode.OPEN_GAPS_RETAINED,
            ProfileLimitationCode.NO_EXTERNAL_ENTERPRISE_VALIDATION,
            ProfileLimitationCode.NO_OUTCOME_ASSURANCE,
        ),
    )
