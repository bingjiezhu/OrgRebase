"""Closed, package-pinned profiles for the shared zero-effect change relation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

import rfc8785
from pydantic import Field, ValidationError

from .canonical import OACValidationError, _raw_mapping
from .models import ChangeProfileBinding, StrictModel

SUPPLIER_PROFILE = "oac.supplier.transfer/v0.2"
RETAIL_PROFILE = "oac.retail.cancellation-review/v0.1"
PROFILE_CHOICES = (SUPPLIER_PROFILE, RETAIL_PROFILE)
_RETAIL_DIGEST = "sha256:2238581f84f2e2d2980e3e21d4143b4cad54d2216a78e0e879ac62c5856ea491"
_PROFILE_PATH = "profiles/change-profiles/retail-cancellation-review-v0.1/profile.json"


class ChangeProfileDescriptor(StrictModel):
    api_version: Literal["oac.change-profile/v0.1"] = Field(alias="apiVersion")
    kind: Literal["ChangeProfile"]
    profile_id: Literal["oac.retail.cancellation-review"] = Field(alias="profileId")
    profile_version: Literal["v0.1"] = Field(alias="profileVersion")
    semantic_type: Literal["retail.cancel_requested"] = Field(alias="semanticType")
    delta_path: Literal["/request"] = Field(alias="deltaPath")
    known_value_type: Literal["string"] = Field(alias="knownValueType")
    unknown_value_policy: Literal["explicit-state-only"] = Field(alias="unknownValuePolicy")
    effect_ceiling: Literal["zero_effect"] = Field(alias="effectCeiling")
    applicability_language: Literal["oac.supplier.applicability/v0.2+legacy"] = Field(
        alias="applicabilityLanguage"
    )
    resource_profile: Literal["oac.supplier.transfer/conformance-resource-profile/v1"] = Field(
        alias="resourceProfile"
    )


@dataclass(frozen=True, slots=True)
class ChangeProfile:
    semantic_type: str
    delta_path: str
    binding: ChangeProfileBinding | None


def parse_change_profile(raw: bytes) -> ChangeProfileDescriptor:
    if len(raw) > 16_384:
        raise OACValidationError("CHANGE_PROFILE_INVALID", "profile descriptor exceeds 16384 bytes")
    try:
        decoded, _ = _raw_mapping(raw)
        descriptor = ChangeProfileDescriptor.model_validate_json(raw)
        digest = "sha256:" + hashlib.sha256(rfc8785.dumps(decoded)).hexdigest()
    except (OACValidationError, ValidationError, ValueError) as exc:
        raise OACValidationError("CHANGE_PROFILE_INVALID", "invalid closed change profile") from exc
    if digest != _RETAIL_DIGEST:
        raise OACValidationError(
            "CHANGE_PROFILE_INVALID", "profile differs from the package-pinned descriptor"
        )
    return descriptor


def get_change_profile(profile: str) -> ChangeProfile:
    if profile == SUPPLIER_PROFILE:
        return ChangeProfile("supplier.status", "/status", None)
    if profile != RETAIL_PROFILE:
        raise OACValidationError("CHANGE_PROFILE_UNKNOWN", "unknown change profile")
    checkout = Path(__file__).resolve().parents[2] / _PROFILE_PATH
    try:
        raw = (
            checkout.read_bytes()
            if checkout.is_file()
            else files("oac").joinpath("resources", _PROFILE_PATH).read_bytes()
        )
    except OSError as exc:
        raise OACValidationError(
            "CHANGE_PROFILE_INVALID", "package-pinned profile is unavailable"
        ) from exc
    descriptor = parse_change_profile(raw)
    return ChangeProfile(
        descriptor.semantic_type,
        descriptor.delta_path,
        ChangeProfileBinding(
            profileId=descriptor.profile_id,
            profileVersion=descriptor.profile_version,
            profileDigest=_RETAIL_DIGEST,
        ),
    )


def change_profile_semantic_validation_rules_json() -> dict[str, object]:
    """Publish the semantic gate separately from structural resource validation."""
    return {
        "apiVersion": "oac.change-profile/v0.1",
        "profileChoices": list(PROFILE_CHOICES),
        "descriptorDigest": _RETAIL_DIGEST,
        "jsonSchemaValidationScope": "structural_only",
        "procedureRef": "profiles/change-profiles/retail-cancellation-review-v0.1/README.md",
        "verificationCommand": "oac verify SNAPSHOT CHANGE PLAN --profile PROFILE",
        "rules": [
            {
                "id": "CP-001",
                "requirement": "Select an explicit package-supported profile; do not infer it from untrusted source or plan fields.",
                "failureReason": "CHANGE_PROFILE_UNKNOWN",
            },
            {
                "id": "CP-002",
                "requirement": "The closed descriptor must match its package-pinned RFC 8785 SHA-256 digest.",
                "failureReason": "CHANGE_PROFILE_INVALID",
            },
            {
                "id": "CP-003",
                "requirement": "Plan profileId, profileVersion and profileDigest must exactly equal the selected descriptor binding. The verifier emits the same binding into its PlanCertificate.",
                "failureReason": "CHANGE_PROFILE_BINDING_MISMATCH",
            },
            {
                "id": "CP-004",
                "requirement": "The change contains exactly one delta at the selected path; all predicate evaluations receive that same path.",
                "failureReason": "CHANGE_PROFILE_DELTA_SET_INVALID",
            },
            {
                "id": "CP-005",
                "requirement": "The selected semanticType, explicit Unknown states, source coherence, frozen roots, evidence, ordering and zero-effect ceiling use the existing shared relation.",
                "failureReason": "UNSUPPORTED_SEMANTICS",
            },
        ],
        "authority": "No profile, plan or PlanCertificate grants execution authority.",
    }
