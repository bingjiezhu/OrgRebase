"""One package-pinned disposable outcome contract; no runtime or oracle dispatch."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Literal

import rfc8785
from pydantic import Field, ValidationError

from .canonical import OACValidationError, _raw_mapping
from .models import OutcomeProfileBinding, StrictModel

DISPOSABLE_OUTCOME_PROFILE_ID = "oac.outcome.disposable-local"
DISPOSABLE_OUTCOME_PROFILE_VERSION = "v0.1"
DISPOSABLE_OUTCOME_PROFILE_DIGEST = (
    "sha256:73d922826b1eef350a13a81fb4626ccfff1aca68b426eacc79d9db84311d9e58"
)
DISPOSABLE_EXECUTION_ROOT_DOMAIN = "oac.state/disposable-execution-evidence/v0.1"
DISPOSABLE_OUTCOME_DIMENSIONS = (
    "task_goal",
    "forbidden_effects",
    "evidence_completion",
    "scope",
    "replayability",
    "unresolved_observations",
)
_PROFILE_PATH = "profiles/outcome-profiles/disposable-local-v0.1/profile.json"


class OutcomeProfileDescriptor(StrictModel):
    api_version: Literal["oac.outcome-profile/v0.1"] = Field(alias="apiVersion")
    kind: Literal["OutcomeProfile"]
    profile_id: Literal["oac.outcome.disposable-local"] = Field(alias="profileId")
    profile_version: Literal["v0.1"] = Field(alias="profileVersion")
    runtime_binding_kind: Literal["DisposableRuntimeBinding"] = Field(alias="runtimeBindingKind")
    runtime_bundle_kind: Literal["DisposableRuntimeBundle"] = Field(alias="runtimeBundleKind")
    execution_receipt_kind: Literal["ExecutionReceipt"] = Field(alias="executionReceiptKind")
    execution_authorization_kind: Literal["DisposableExecutionGrant"] = Field(
        alias="executionAuthorizationKind"
    )
    execution_root_domain: Literal["oac.state/disposable-execution-evidence/v0.1"] = Field(
        alias="executionRootDomain"
    )
    plan_effect_ceiling: Literal["zero_effect"] = Field(alias="planEffectCeiling")
    execution_authority: Literal["separate-disposable-local-controller-grant"] = Field(
        alias="executionAuthority"
    )
    required_dimensions: tuple[
        Literal["task_goal"],
        Literal["forbidden_effects"],
        Literal["evidence_completion"],
        Literal["scope"],
        Literal["replayability"],
        Literal["unresolved_observations"],
    ] = Field(alias="requiredDimensions")
    verdict_policy: Literal["FAIL-reject-else-UNKNOWN-unknown-else-all-PASS-accept"] = Field(
        alias="verdictPolicy"
    )
    evidence_policy: Literal["per-dimension-refs-with-exact-reason-and-unresolved-unions"] = Field(
        alias="evidencePolicy"
    )
    verification_scope: Literal["reference-root-and-verdict-consistency-only"] = Field(
        alias="verificationScope"
    )


def parse_outcome_profile(raw: bytes) -> OutcomeProfileDescriptor:
    """Validate the closed descriptor, including its pinned canonical digest."""
    if len(raw) > 16_384:
        raise OACValidationError("OUTCOME_PROFILE_INVALID", "descriptor exceeds 16384 bytes")
    try:
        decoded, _ = _raw_mapping(raw)
        descriptor = OutcomeProfileDescriptor.model_validate_json(raw)
        digest = "sha256:" + hashlib.sha256(rfc8785.dumps(decoded)).hexdigest()
    except (OACValidationError, ValidationError, ValueError) as exc:
        raise OACValidationError(
            "OUTCOME_PROFILE_INVALID", "invalid closed outcome profile"
        ) from exc
    if digest != DISPOSABLE_OUTCOME_PROFILE_DIGEST:
        raise OACValidationError(
            "OUTCOME_PROFILE_INVALID", "descriptor differs from the package pin"
        )
    return descriptor


def disposable_outcome_binding() -> OutcomeProfileBinding:
    """Return the exact supported binding, never execution authorization."""
    return OutcomeProfileBinding(
        profileId=DISPOSABLE_OUTCOME_PROFILE_ID,
        profileVersion=DISPOSABLE_OUTCOME_PROFILE_VERSION,
        profileDigest=DISPOSABLE_OUTCOME_PROFILE_DIGEST,
    )


def get_outcome_profile(binding: OutcomeProfileBinding) -> OutcomeProfileDescriptor:
    """Resolve only the exact built-in profile; untrusted descriptors cannot extend it."""
    if (binding.profile_id, binding.profile_version) != (
        DISPOSABLE_OUTCOME_PROFILE_ID,
        DISPOSABLE_OUTCOME_PROFILE_VERSION,
    ):
        raise OACValidationError(
            "OUTCOME_PROFILE_UNKNOWN", "unsupported outcome profile identity/version"
        )
    if binding != disposable_outcome_binding():
        raise OACValidationError(
            "OUTCOME_PROFILE_INVALID", "outcome profile digest differs from the pin"
        )
    checkout = Path(__file__).resolve().parents[2] / _PROFILE_PATH
    try:
        raw = (
            checkout.read_bytes()
            if checkout.is_file()
            else files("oac").joinpath("resources", _PROFILE_PATH).read_bytes()
        )
    except OSError as exc:
        raise OACValidationError(
            "OUTCOME_PROFILE_INVALID", "package-pinned descriptor is unavailable"
        ) from exc
    return parse_outcome_profile(raw)


def outcome_profile_semantic_validation_rules_json() -> dict[str, object]:
    return {
        "apiVersion": "oac.outcome-profile/v0.1",
        "binding": disposable_outcome_binding().model_dump(mode="json", by_alias=True),
        "jsonSchemaValidationScope": "structural_only",
        "procedureRef": "profiles/outcome-profiles/disposable-local-v0.1/README.md",
        "verificationCommand": "oac validate-evolution OUTCOME_CERTIFICATE",
        "validator": "oac.evolution.verify_outcome_certificate",
        "rules": [
            {
                "id": "OP-001",
                "requirement": "Exact package-pinned descriptor identity, version and digest; explicit binding selects the profile.",
                "failureReason": "OUTCOME_PROFILE_INVALID",
            },
            {
                "id": "OP-002",
                "requirement": "Exact disposable binding/bundle, receipt, and independent grant are bound in a separate execution-root domain.",
                "failureReason": "OUTCOME_EXECUTION_AUTHORIZATION_INVALID",
            },
            {
                "id": "OP-003",
                "requirement": "Exactly six named dimensions carry explicit evidence and close over certificate reasons and unresolved refs.",
                "failureReason": "OUTCOME_DIMENSION_EVIDENCE_INVALID",
            },
            {
                "id": "OP-004",
                "requirement": "FAIL dominates UNKNOWN; UNKNOWN dominates PASS. No PROVISIONAL or success promotion is permitted.",
                "failureReason": "OUTCOME_VERDICT_INVALID",
            },
        ],
        "authority": "Plans remain zero_effect and grant no credentials. Reference consistency does not authenticate grant issuers or independently execute an oracle.",
    }
