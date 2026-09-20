"""Strict parsing, admission, compatibility, and public profile views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from orgrebase.workspace.profile_contracts import (
    ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE,
    REFERENCE_HANDLER_PROFILE,
    AdmissionDimension,
    AdmissionDimensionStatus,
    AdmittedClaimCeiling,
    AdmittedGapAction,
    EnterpriseSeedAdmissionError,
    EnterpriseSeedAdmissionReceipt,
    EnterpriseSeedProfile,
    GrowthLevel,
    ProfileLimitationCode,
    ReadinessGate,
    RuntimeCompatibilityMode,
    SeedCompleteness,
    minimum_gap_gates,
)
from orgrebase.workspace.reference_profiles import northstar_acme_quote_profile

if TYPE_CHECKING:
    from orgrebase.workspace.source_admission import EnterpriseSeedSourceAdmissionReceipt


def parse_enterprise_seed_admission_receipt(
    value: EnterpriseSeedAdmissionReceipt | dict[str, object],
) -> EnterpriseSeedAdmissionReceipt:
    try:
        payload = (
            value.model_dump(mode="json") if isinstance(value, EnterpriseSeedAdmissionReceipt) else value
        )
        return EnterpriseSeedAdmissionReceipt.model_validate(payload)
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("PROFILE_ADMISSION_RECEIPT_INVALID", str(exc)) from exc


def parse_enterprise_seed_profile(
    value: EnterpriseSeedProfile | dict[str, object],
) -> EnterpriseSeedProfile:
    try:
        # Pydantic's model_copy(update=...) intentionally skips validation.  Never
        # trust a model instance or its cached digest at this boundary: serialize
        # it and rebuild a fresh instance so the content digest and every nested
        # validator run again.
        payload = value.model_dump(mode="json") if isinstance(value, EnterpriseSeedProfile) else value
        return EnterpriseSeedProfile.model_validate(payload)
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("PROFILE_VALIDATION_FAILED", str(exc)) from exc


def load_enterprise_seed_profile(path: str | Path) -> EnterpriseSeedProfile:
    selected = Path(path)
    try:
        raw = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EnterpriseSeedAdmissionError("PROFILE_PARSE_FAILED", str(exc)) from exc
    if not isinstance(raw, dict):
        raise EnterpriseSeedAdmissionError("PROFILE_ROOT_NOT_OBJECT")
    return parse_enterprise_seed_profile(raw)


def admit_enterprise_seed_profile(
    value: EnterpriseSeedProfile | dict[str, object],
    *,
    source_admission: EnterpriseSeedSourceAdmissionReceipt | None = None,
) -> EnterpriseSeedAdmissionReceipt:
    from orgrebase.workspace.source_admission import (
        admit_enterprise_seed_sources,
        parse_enterprise_seed_source_admission_receipt,
    )

    profile = parse_enterprise_seed_profile(value)
    source_receipt = parse_enterprise_seed_source_admission_receipt(
        source_admission or admit_enterprise_seed_sources(profile)
    )
    if source_receipt.profile_ref != profile.ref or source_receipt.profile_digest != profile.digest:
        raise EnterpriseSeedAdmissionError("PROFILE_SOURCE_ADMISSION_MISMATCH")
    gap_actions = tuple(
        AdmittedGapAction(
            gap_ref=gap.id,
            gap_digest=gap.digest,
            kind=gap.kind,
            component=gap.component,
            owner_ref=gap.owner_ref,
            effective_blocks=tuple(
                gate
                for gate in ReadinessGate
                if gate in set(gap.blocks) | set(minimum_gap_gates(gap.kind, gap.component))
            ),
            minimum_blocks=minimum_gap_gates(gap.kind, gap.component),
            resolution_gate_ref=gap.resolution_gate_ref,
        )
        for gap in profile.gaps
    )
    blockers = {gate for action in gap_actions for gate in action.effective_blocks}
    seed_ready = ReadinessGate.SEED_READY not in blockers
    shadow_intake_admissible = seed_ready and ReadinessGate.SHADOW_READY not in blockers
    reference = northstar_acme_quote_profile()
    exact_reference_compatible = (
        shadow_intake_admissible
        and profile.runtime_compatibility.mode == RuntimeCompatibilityMode.REFERENCE_HANDLER
        and profile.runtime_compatibility.handler_profile == REFERENCE_HANDLER_PROFILE
        and profile.digest == reference.digest
    )
    pilot_runtime_compatible = (
        shadow_intake_admissible
        and not profile.gaps
        and all(
            component.completeness == SeedCompleteness.COMPLETE
            for component in profile.components
        )
        and profile.runtime_compatibility.mode
        == RuntimeCompatibilityMode.REFERENCE_HANDLER
        and profile.runtime_compatibility.handler_profile
        == ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE
    )
    reference_runtime_compatible = (
        exact_reference_compatible or pilot_runtime_compatible
    )
    if exact_reference_compatible:
        reference_reasons = ("EXACT_REFERENCE_PROFILE_DIGEST",)
    elif pilot_runtime_compatible:
        reference_reasons = (
            "ENTERPRISE_QUOTE_PILOT_HANDLER_SUPPORTED",
            "COMPLETE_FIVE_COMPONENT_SEED",
            "ZERO_EFFECT_SHADOW_READY",
        )
    elif profile.runtime_compatibility.mode == RuntimeCompatibilityMode.INTAKE_ONLY:
        reference_reasons = (
            "INTAKE_ONLY_NOT_RUNTIME_EXECUTABLE",
            "CURRENT_QUOTE_HANDLER_REQUIRES_EXACT_NORTHSTAR_PROFILE_DIGEST",
        )
    elif (
        profile.runtime_compatibility.handler_profile
        == ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE
    ):
        reference_reasons = (
            "ENTERPRISE_QUOTE_PILOT_REQUIRES_COMPLETE_SEED",
            "ENTERPRISE_QUOTE_PILOT_REQUIRES_NO_OPEN_RUNTIME_GAPS",
            "ENTERPRISE_QUOTE_PILOT_REQUIRES_SHADOW_READY",
        )
    elif profile.runtime_compatibility.handler_profile not in {
        REFERENCE_HANDLER_PROFILE,
        ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE,
    }:
        reference_reasons = (
            "HANDLER_PROFILE_MISMATCH",
            "CURRENT_QUOTE_HANDLER_REQUIRES_EXACT_NORTHSTAR_PROFILE_DIGEST",
        )
    else:
        reference_reasons = (
            "REFERENCE_PROFILE_DIGEST_MISMATCH",
            "CURRENT_QUOTE_HANDLER_REQUIRES_EXACT_NORTHSTAR_PROFILE_DIGEST",
        )
    limitations = set(profile.declared_limitation_codes)
    limitations.add(ProfileLimitationCode.NO_OUTCOME_ASSURANCE)
    if profile.synthetic:
        limitations.add(ProfileLimitationCode.SYNTHETIC_DATA_ONLY)
    if profile.runtime_compatibility.mode == RuntimeCompatibilityMode.INTAKE_ONLY:
        limitations.update(
            {
                ProfileLimitationCode.INTAKE_ONLY,
                ProfileLimitationCode.NO_EXTERNAL_ENTERPRISE_VALIDATION,
            }
        )
    else:
        limitations.add(ProfileLimitationCode.NO_EXTERNAL_CONNECTORS)
    if profile.gaps:
        limitations.add(ProfileLimitationCode.OPEN_GAPS_RETAINED)
    if any(component.completeness != SeedCompleteness.COMPLETE for component in profile.components):
        limitations.add(ProfileLimitationCode.PARTIAL_COVERAGE)
    return EnterpriseSeedAdmissionReceipt(
        profile_ref=profile.ref,
        profile_digest=profile.digest,
        source_admission_receipt_digest=source_receipt.digest,
        admitted_source_root_digests=tuple(item.observed_digest for item in source_receipt.root_observations),
        component_admission_digests=tuple(item.digest for item in source_receipt.component_admissions),
        source_profile_projection_digest=source_receipt.profile_projection_digest,
        seed_ready=seed_ready,
        shadow_intake_admissible=shadow_intake_admissible,
        reference_runtime_compatible=reference_runtime_compatible,
        dimensions=(
            AdmissionDimension(
                name="PARSE",
                status=AdmissionDimensionStatus.PASS,
                reason_codes=("STRICT_PROFILE_SCHEMA_AND_PROFILE_DIGEST_VALID",),
            ),
            AdmissionDimension(
                name="SOURCE_BYTES",
                status=AdmissionDimensionStatus.PASS,
                reason_codes=(
                    "EXACT_SOURCE_BYTES_AND_COMPONENT_DIGESTS_ADMITTED",
                    "UNKNOWN_LOCATORS_NOT_DEREFERENCED",
                ),
            ),
            AdmissionDimension(
                name="SEED_READY",
                status=(AdmissionDimensionStatus.PASS if seed_ready else AdmissionDimensionStatus.HOLD),
                reason_codes=(("NO_SEED_BLOCKING_GAP",) if seed_ready else ("OPEN_GAP_BLOCKS_SEED_READY",)),
            ),
            AdmissionDimension(
                name="SHADOW_INTAKE_ADMISSIBLE",
                status=(
                    AdmissionDimensionStatus.PASS
                    if shadow_intake_admissible
                    else AdmissionDimensionStatus.HOLD
                ),
                reason_codes=(
                    ("ZERO_EXTERNAL_EFFECT_SHADOW_ADMISSIBLE",)
                    if shadow_intake_admissible
                    else ("OPEN_GAP_BLOCKS_SHADOW_READY",)
                ),
            ),
            AdmissionDimension(
                name="REFERENCE_RUNTIME",
                status=(
                    AdmissionDimensionStatus.PASS
                    if reference_runtime_compatible
                    else AdmissionDimensionStatus.REJECT
                ),
                reason_codes=reference_reasons,
            ),
        ),
        gap_actions=gap_actions,
        admitted_claim_ceiling=(
            AdmittedClaimCeiling.REFERENCE_PROFILE_INTAKE
            if reference_runtime_compatible
            else AdmittedClaimCeiling.INTAKE_VALIDATED
        ),
        verified_growth_level=(
            GrowthLevel.G1_SELECT if reference_runtime_compatible else GrowthLevel.G0_DESCRIBE
        ),
        limitations=tuple(code for code in ProfileLimitationCode if code in limitations),
    )


def require_reference_runtime_compatible(
    profile: EnterpriseSeedProfile,
    *,
    source_admission: EnterpriseSeedSourceAdmissionReceipt | None = None,
) -> EnterpriseSeedAdmissionReceipt:
    receipt = admit_enterprise_seed_profile(
        profile,
        source_admission=source_admission,
    )
    if not receipt.reference_runtime_compatible:
        raise EnterpriseSeedAdmissionError(
            "UNSUPPORTED_HANDLER_PROFILE",
            f"profile={profile.ref},digest={profile.digest}",
        )
    return receipt


def profile_summary(
    profile: EnterpriseSeedProfile,
    receipt: EnterpriseSeedAdmissionReceipt | None = None,
) -> dict[str, object]:
    """Return the bounded public identity used by state, API, and evidence views."""

    profile = parse_enterprise_seed_profile(profile)
    receipt = parse_enterprise_seed_admission_receipt(receipt or admit_enterprise_seed_profile(profile))
    if receipt.profile_digest != profile.digest:
        raise EnterpriseSeedAdmissionError("PROFILE_SUMMARY_RECEIPT_MISMATCH")
    return {
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "profile_ref": profile.ref,
        "digest": profile.digest,
        "scenario_id": profile.scenario_id,
        "organization_id": profile.organization_id,
        "data_class": profile.data_class.value,
        "verified_growth_level": receipt.verified_growth_level.value,
        "effect_ceiling": profile.effect_ceiling.value,
        "admitted_claim_ceiling": receipt.admitted_claim_ceiling.value,
        "seed_ready": receipt.seed_ready,
        "shadow_intake_admissible": receipt.shadow_intake_admissible,
        "reference_runtime_compatible": receipt.reference_runtime_compatible,
        "runtime_compatibility": profile.runtime_compatibility.model_dump(mode="json"),
        "gap_actions": [action.model_dump(mode="json") for action in receipt.gap_actions],
        "limitations": [code.value for code in receipt.limitations],
    }
