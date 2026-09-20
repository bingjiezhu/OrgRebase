"""Shared reference checks and pure domain-separated lifecycle root projections.

These functions commit references only. They do not load or execute a runtime,
confer authority, or interpret a business oracle.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from typing import NoReturn

import rfc8785

from .canonical import OACValidationError
from .json_types import JsonValue
from .models import OutcomeProfileBinding, ResourceRef
from .outcome_profiles import get_outcome_profile

SOURCE_ROOT_DOMAIN = "oac.root/source/v0.1"
DEMAND_ROOT_DOMAIN = "oac.root/demand/v0.1"
PLAN_ROOT_DOMAIN = "oac.root/plan/v0.1"
EXECUTION_ROOT_DOMAIN = "oac.state/execution-evidence/v0.1"
OUTCOME_ROOT_DOMAIN = "oac.root/outcome/v0.1"
EVOLUTION_ROOT_DOMAIN = "oac.root/evolution/v0.1"

def _fail(code: str, message: str) -> NoReturn:
    raise OACValidationError(code, message)


def _ref_key(ref: ResourceRef) -> tuple[str, str, str, int, str]:
    return ref.kind, ref.namespace, ref.resource_id, ref.revision, ref.digest


def _identity_key(ref: ResourceRef) -> tuple[str, str]:
    return ref.namespace, ref.resource_id


def _ensure_digest(value: str) -> None:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        _fail("EVOLUTION_ROOT_MISMATCH", "lifecycle root is not an exact SHA-256 digest")


def _ensure_string_set(label: str, values: Sequence[str]) -> None:
    if tuple(values) != tuple(sorted(set(values))):
        _fail("EVOLUTION_DUPLICATE_REF", f"{label} must be sorted and unique")


def _ensure_authority_envelope(resource: object) -> None:
    metadata = resource.metadata  # type: ignore[attr-defined]
    if metadata.owner_ref is None or metadata.governance_ref is None:
        _fail("EVOLUTION_AUTHORITY_MISMATCH", "lifecycle authority envelope is incomplete")
    if not metadata.source_refs:
        _fail("EVOLUTION_PROVENANCE_MISMATCH", "lifecycle sourceRefs is empty")


def _ensure_unique(label: str, refs: Sequence[ResourceRef]) -> None:
    keys = tuple(_ref_key(ref) for ref in refs)
    if len(keys) != len(set(keys)):
        _fail("EVOLUTION_DUPLICATE_REF", f"duplicate exact ref in {label}")


def _ensure_namespace(namespace: str, refs: Iterable[ResourceRef]) -> None:
    if any(ref.namespace != namespace for ref in refs):
        _fail("EVOLUTION_NAMESPACE_MISMATCH", "lifecycle refs cross namespace")


def _ensure_kind(ref: ResourceRef, expected: str) -> None:
    if ref.kind != expected:
        _fail("EVOLUTION_REF_KIND_MISMATCH", f"expected {expected}, got {ref.kind}")


def _ref_json(ref: ResourceRef) -> dict[str, JsonValue]:
    return ref.model_dump(mode="json", by_alias=True)


def _root(domain: str, sections: Sequence[tuple[str, Sequence[ResourceRef] | str]]) -> str:
    projection: dict[str, JsonValue] = {"domain": domain}
    for name, value in sections:
        if isinstance(value, str):
            projection[name] = value
        else:
            _ensure_unique(name, value)
            projection[name] = [_ref_json(ref) for ref in value]
    return f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"


def project_source_root(
    snapshot_ref: ResourceRef,
    source_admission_receipt_refs: Sequence[ResourceRef],
) -> str:
    _ensure_kind(snapshot_ref, "OrganizationSnapshot")
    if not source_admission_receipt_refs:
        _fail("EVOLUTION_ROOT_INCOMPLETE", "source root requires an admission receipt")
    for ref in source_admission_receipt_refs:
        _ensure_kind(ref, "SourceAdmissionReceipt")
    _ensure_namespace(snapshot_ref.namespace, source_admission_receipt_refs)
    return _root(
        SOURCE_ROOT_DOMAIN,
        (
            ("snapshot", (snapshot_ref,)),
            ("sourceAdmissionReceipts", tuple(source_admission_receipt_refs)),
        ),
    )


def project_demand_root(demand_ref: ResourceRef, change_refs: Sequence[ResourceRef]) -> str:
    _ensure_kind(demand_ref, "OrganizationalDemand")
    if not change_refs:
        _fail("EVOLUTION_ROOT_INCOMPLETE", "demand root requires at least one change")
    for ref in change_refs:
        _ensure_kind(ref, "SemanticChangeSet")
    _ensure_namespace(demand_ref.namespace, change_refs)
    return _root(
        DEMAND_ROOT_DOMAIN,
        (("demand", (demand_ref,)), ("changes", tuple(change_refs))),
    )


def project_plan_root(plan_ref: ResourceRef, certificate_ref: ResourceRef) -> str:
    _ensure_kind(plan_ref, "OrganizationPlan")
    _ensure_kind(certificate_ref, "PlanCertificate")
    _ensure_namespace(plan_ref.namespace, (certificate_ref,))
    return _root(
        PLAN_ROOT_DOMAIN,
        (("plan", (plan_ref,)), ("planCertificate", (certificate_ref,))),
    )


def project_execution_root(
    runtime_binding_ref: ResourceRef,
    runtime_bundle_ref: ResourceRef,
    execution_receipt_ref: ResourceRef,
    execution_evidence_refs: Sequence[ResourceRef],
    *,
    profile_binding: OutcomeProfileBinding | None = None,
    execution_authorization_ref: ResourceRef | None = None,
) -> str:
    """Commit execution evidence; an explicit disposable profile requires its own grant.

    The default projection is byte-for-byte Spec 009. Neither projection grants
    execution authority, authenticates an issuer, or runs a business oracle.
    """
    profile = get_outcome_profile(profile_binding) if profile_binding is not None else None
    _ensure_kind(runtime_binding_ref, profile.runtime_binding_kind if profile else "RuntimeBinding")
    _ensure_kind(runtime_bundle_ref, profile.runtime_bundle_kind if profile else "ZeroEffectRuntimeBundle")
    # Exact external kinds remain outside the core execution resource registry.
    _ensure_kind(execution_receipt_ref, "ExecutionReceipt")
    if not execution_evidence_refs:
        _fail("EVOLUTION_ROOT_INCOMPLETE", "execution root requires evidence")
    extra_refs: tuple[ResourceRef, ...] = ()
    profile_sections: tuple[tuple[str, Sequence[ResourceRef] | str], ...] = ()
    if profile is not None:
        if execution_authorization_ref is None:
            _fail("OUTCOME_EXECUTION_AUTHORIZATION_INVALID", "disposable execution requires a separate grant")
        _ensure_kind(execution_authorization_ref, profile.execution_authorization_kind)
        extra_refs = (execution_authorization_ref,)
        assert profile_binding is not None
        profile_sections = (
            ("profileId", profile_binding.profile_id),
            ("profileVersion", profile_binding.profile_version),
            ("profileDigest", profile_binding.profile_digest),
            ("executionAuthorization", extra_refs),
        )
    elif execution_authorization_ref is not None:
        _fail("OUTCOME_EXECUTION_AUTHORIZATION_INVALID", "default zero-effect root cannot carry a disposable grant")
    _ensure_namespace(
        runtime_binding_ref.namespace,
        (runtime_bundle_ref, execution_receipt_ref, *execution_evidence_refs, *extra_refs),
    )
    return _root(
        profile.execution_root_domain if profile else EXECUTION_ROOT_DOMAIN,
        (
            *profile_sections,
            ("runtimeBinding", (runtime_binding_ref,)),
            ("runtimeBundle", (runtime_bundle_ref,)),
            ("executionReceipt", (execution_receipt_ref,)),
            ("executionEvidence", tuple(execution_evidence_refs)),
        ),
    )


def project_outcome_root(
    source_root: str,
    demand_root: str,
    plan_root: str,
    execution_root: str,
    outcome_certificate_ref: ResourceRef,
) -> str:
    _ensure_kind(outcome_certificate_ref, "OutcomeCertificate")
    for value in (source_root, demand_root, plan_root, execution_root):
        _ensure_digest(value)
    return _root(
        OUTCOME_ROOT_DOMAIN,
        (
            ("sourceRoot", source_root),
            ("demandRoot", demand_root),
            ("planRoot", plan_root),
            ("executionRoot", execution_root),
            ("outcomeCertificate", (outcome_certificate_ref,)),
        ),
    )


def project_evolution_root(
    candidate_refs: Sequence[ResourceRef],
    supporting_outcome_refs: Sequence[ResourceRef],
    counterexample_outcome_refs: Sequence[ResourceRef],
    replay_evidence_refs: Sequence[ResourceRef],
    governance_decision_ref: ResourceRef,
) -> str:
    if not all(
        (candidate_refs, supporting_outcome_refs, counterexample_outcome_refs, replay_evidence_refs)
    ):
        _fail(
            "EVOLUTION_ROOT_INCOMPLETE",
            "evolution root requires candidate, support, counterexample, and replay evidence",
        )
    for ref in (*supporting_outcome_refs, *counterexample_outcome_refs):
        _ensure_kind(ref, "OutcomeCertificate")
    evidence_refs = (
        *candidate_refs,
        *supporting_outcome_refs,
        *counterexample_outcome_refs,
        *replay_evidence_refs,
    )
    _ensure_unique("evolution.candidates-support-counterexamples-replay", evidence_refs)
    _ensure_namespace(candidate_refs[0].namespace, (*evidence_refs, governance_decision_ref))
    return _root(
        EVOLUTION_ROOT_DOMAIN,
        (
            ("candidates", tuple(candidate_refs)),
            ("supportingOutcomes", tuple(supporting_outcome_refs)),
            ("counterexampleOutcomes", tuple(counterexample_outcome_refs)),
            ("replayEvidence", tuple(replay_evidence_refs)),
            ("governanceDecision", (governance_decision_ref,)),
        ),
    )


