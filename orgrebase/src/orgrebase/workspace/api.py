"""Read-only public views for enterprise Profile and intake admission."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from orgrebase.workspace.profile import (
    EnterpriseSeedProfile,
    admit_enterprise_seed_profile,
    parse_enterprise_seed_profile,
    profile_summary,
)


def workspace_profile_view(
    profile: EnterpriseSeedProfile,
) -> dict[str, Any]:
    profile = parse_enterprise_seed_profile(profile)
    receipt = admit_enterprise_seed_profile(profile)
    return {
        "schema_version": "orgrebase.enterprise-seed-profile-view.v1",
        "profile": profile.model_dump(mode="json"),
        "summary": profile_summary(profile, receipt),
        "admission_receipt": receipt.model_dump(mode="json"),
        "canonical_target_writes": 0,
    }


def enterprise_intake_view(
    values: Iterable[EnterpriseSeedProfile | Mapping[str, object]],
) -> dict[str, Any]:
    profiles = tuple(
        parse_enterprise_seed_profile(value if isinstance(value, EnterpriseSeedProfile) else dict(value))
        for value in values
    )
    if not profiles:
        raise ValueError("PROFILE_INTAKE_BATCH_EMPTY")
    profile_refs = tuple(profile.ref for profile in profiles)
    if len(profile_refs) != len(set(profile_refs)):
        raise ValueError("PROFILE_DUPLICATE_PROFILE_REF")
    receipts = tuple(admit_enterprise_seed_profile(profile) for profile in profiles)
    return {
        "schema_version": "orgrebase.enterprise-seed-intake-batch.v1",
        "profiles": [
            profile_summary(profile, receipt)
            for profile, receipt in zip(profiles, receipts, strict=True)
        ],
        "receipts": [receipt.model_dump(mode="json") for receipt in receipts],
        "intake_count": len(profiles),
        "reference_runtime_compatible_count": sum(
            receipt.reference_runtime_compatible for receipt in receipts
        ),
        "canonical_target_writes": 0,
        "claim_boundary": (
            "An intake receipt does not prove handler execution, Outcome, ROI, "
            "external-enterprise validation, or arbitrary-enterprise generalization."
        ),
    }
