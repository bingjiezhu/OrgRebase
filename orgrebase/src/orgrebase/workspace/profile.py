"""Backward-compatible public facade for enterprise Seed/Profile intake.

The implementation is split by responsibility:

* :mod:`profile_contracts` owns immutable contracts and validators;
* :mod:`reference_profiles` owns shipped deterministic constructors; and
* :mod:`profile_admission` owns parsing, admission, and public views.

All previously public imports remain available from this module.
"""

from orgrebase.workspace.profile_admission import (
    admit_enterprise_seed_profile,
    load_enterprise_seed_profile,
    parse_enterprise_seed_admission_receipt,
    parse_enterprise_seed_profile,
    profile_summary,
    require_reference_runtime_compatible,
)
from orgrebase.workspace.profile_contracts import (
    PROFILE_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    REFERENCE_HANDLER_PROFILE,
    AdmissionDimension,
    AdmissionDimensionStatus,
    AdmittedClaimCeiling,
    AdmittedGapAction,
    ChangeFamilyBinding,
    DefaultTaskBinding,
    EffectCeiling,
    EnterpriseSeedAdmissionError,
    EnterpriseSeedAdmissionReceipt,
    EnterpriseSeedGap,
    EnterpriseSeedProfile,
    FrozenModel,
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
    minimum_gap_gates,
)
from orgrebase.workspace.reference_profiles import (
    northstar_acme_quote_profile,
    supplier_shadow_intake_profile,
)

__all__ = (
    "PROFILE_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "REFERENCE_HANDLER_PROFILE",
    "AdmissionDimension",
    "AdmissionDimensionStatus",
    "AdmittedClaimCeiling",
    "AdmittedGapAction",
    "ChangeFamilyBinding",
    "DefaultTaskBinding",
    "EffectCeiling",
    "EnterpriseSeedAdmissionError",
    "EnterpriseSeedAdmissionReceipt",
    "EnterpriseSeedGap",
    "EnterpriseSeedProfile",
    "FrozenModel",
    "GovernanceBinding",
    "GrowthLevel",
    "ProfileLimitationCode",
    "ReadinessGate",
    "RuntimeCompatibilityDeclaration",
    "RuntimeCompatibilityMode",
    "SeedCompleteness",
    "SeedComponent",
    "SeedComponentKind",
    "SeedDataClass",
    "SeedGapKind",
    "SeedInputValue",
    "SeedSourceRoot",
    "admit_enterprise_seed_profile",
    "load_enterprise_seed_profile",
    "minimum_gap_gates",
    "northstar_acme_quote_profile",
    "parse_enterprise_seed_admission_receipt",
    "parse_enterprise_seed_profile",
    "profile_summary",
    "require_reference_runtime_compatible",
    "supplier_shadow_intake_profile",
)
