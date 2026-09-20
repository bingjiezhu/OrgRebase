"""Proof-carrying lifecycle roots and semantic gates for Spec 009.

The module is intentionally runtime-neutral.  Execution, observation, procedure,
and evolution payloads remain extension resources bound only by ``ResourceRef``.
"""

from __future__ import annotations

from collections.abc import Sequence

from .canonical import resource_ref, verify_resource_digest
from .evolution_roots import (
    DEMAND_ROOT_DOMAIN as DEMAND_ROOT_DOMAIN,
)
from .evolution_roots import (
    EVOLUTION_ROOT_DOMAIN as EVOLUTION_ROOT_DOMAIN,
)
from .evolution_roots import (
    EXECUTION_ROOT_DOMAIN as EXECUTION_ROOT_DOMAIN,
)
from .evolution_roots import (
    OUTCOME_ROOT_DOMAIN as OUTCOME_ROOT_DOMAIN,
)
from .evolution_roots import (
    PLAN_ROOT_DOMAIN as PLAN_ROOT_DOMAIN,
)
from .evolution_roots import (
    SOURCE_ROOT_DOMAIN as SOURCE_ROOT_DOMAIN,
)
from .evolution_roots import (
    _ensure_authority_envelope,
    _ensure_kind,
    _ensure_namespace,
    _ensure_string_set,
    _ensure_unique,
    _fail,
    _identity_key,
    _ref_key,
)
from .evolution_roots import (
    project_demand_root as project_demand_root,
)
from .evolution_roots import (
    project_evolution_root as project_evolution_root,
)
from .evolution_roots import (
    project_execution_root as project_execution_root,
)
from .evolution_roots import (
    project_outcome_root as project_outcome_root,
)
from .evolution_roots import (
    project_plan_root as project_plan_root,
)
from .evolution_roots import (
    project_source_root as project_source_root,
)
from .json_types import JsonValue
from .models import (
    AdmissionStatus,
    AdmissionVerdict,
    DimensionVerdict,
    OrganizationalDemand,
    OrganizationSnapshot,
    OutcomeCertificate,
    OutcomeCertificateSpec,
    OutcomeVerdict,
    ResourceRef,
    SourceAdmissionReceipt,
)
from .outcome_profiles import get_outcome_profile
from .sealed import AdmittedSealedResource, validate_sealed_admission

_EVOLUTION_SEMANTIC_RULES: tuple[dict[str, JsonValue], ...] = (
    {
        "ruleId": "OAC-EVO-001",
        "appliesToKinds": ["OrganizationalDemand"],
        "validator": "oac.evolution.verify_organizational_demand",
        "failureReasonCodes": ["DEMAND_ROOT_MISMATCH", "DEMAND_AUTHORITY_UNRESOLVED", "EVOLUTION_PROVENANCE_MISMATCH"],
        "description": "Demand binds one exact admitted Snapshot and eligible accountable requester.",
    },
    {
        "ruleId": "OAC-EVO-002",
        "appliesToKinds": ["SourceAdmissionReceipt"],
        "validator": "oac.evolution.verify_source_admission_receipt",
        "failureReasonCodes": ["EVOLUTION_SELF_ADMISSION_FORBIDDEN", "SOURCE_ADMISSION_VERDICT_INVALID", "SUCCESSOR_LINEAGE_INCOMPLETE"],
        "description": "Admission verdict, reviewed subjects, authority separation, and lineage cohere.",
    },
    {
        "ruleId": "OAC-EVO-003",
        "appliesToKinds": ["OutcomeCertificate"],
        "validator": "oac.evolution.verify_outcome_certificate",
        "failureReasonCodes": ["EVOLUTION_ROOT_MISMATCH", "OUTCOME_SELF_CERTIFICATION_FORBIDDEN", "OUTCOME_VERDICT_INVALID"],
        "description": "Independent Outcome recomputes exact S/D/P/X roots and preserves non-success.",
    },
    {
        "ruleId": "OAC-EVO-004",
        "appliesToKinds": ["SourceAdmissionReceipt"],
        "validator": "oac.evolution.verify_successor_admission",
        "failureReasonCodes": ["EVOLUTION_AUTO_PROMOTION_FORBIDDEN", "SUCCESSOR_PREDECESSOR_MISMATCH", "EVOLUTION_AUTHORITY_MISMATCH"],
        "description": "A successor requires an exact external decision over a counterexample-retaining E root.",
    },
)


def evolution_semantic_validation_rules_json() -> dict[str, JsonValue]:
    """Return the portable semantic-validation surface for Spec 009."""

    rules = []
    for source in _EVOLUTION_SEMANTIC_RULES:
        rule = dict(source)
        validator = str(rule.pop("validator"))
        rule["ruleVersion"] = "1.0.0"
        rule["normativeRef"] = (
            "specs/009-proof-carrying-evolution-minimum-profile/spec.md"
        )
        rule["implementationBindings"] = {"python": {"validator": validator}}
        rules.append(rule)
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "registryKind": "EvolutionSemanticValidationRuleRegistry",
        "profile": "oac.evolution.minimum/v0.1",
        "jsonSchema": {"validationScope": "structural_only"},
        "semanticValidation": {"requiredAfterJsonSchema": True},
        "rules": rules,
    }


def verify_organizational_demand(
    demand: OrganizationalDemand, snapshot: OrganizationSnapshot
) -> None:
    _ensure_authority_envelope(demand)
    verify_resource_digest(snapshot)
    verify_resource_digest(demand)
    _verify_demand_fields(demand, snapshot, resource_ref(snapshot))


def verify_organizational_demand_from_admitted(
    demand_admission: AdmittedSealedResource, snapshot_admission: AdmittedSealedResource,
) -> None:
    """Check exact raw Demand/Snapshot identity before the shared semantic rules."""
    demand = validate_sealed_admission(demand_admission, "OrganizationalDemand").resource
    snapshot = validate_sealed_admission(snapshot_admission, "OrganizationSnapshot").resource
    if not isinstance(demand, OrganizationalDemand) or not isinstance(snapshot, OrganizationSnapshot):
        _fail("EVOLUTION_REF_KIND_MISMATCH", "demand validation requires exact registered kinds")
    _ensure_authority_envelope(demand)
    expected_snapshot = ResourceRef(kind=snapshot.kind, namespace=snapshot.metadata.namespace,
        resourceId=snapshot.metadata.id, revision=snapshot.metadata.revision, digest=snapshot_admission.resource_digest)
    _verify_demand_fields(demand, snapshot, expected_snapshot)


def _verify_demand_fields(demand: OrganizationalDemand, snapshot: OrganizationSnapshot,
                          expected_snapshot: ResourceRef) -> None:
    if demand.spec.snapshot_ref != expected_snapshot:
        _fail("DEMAND_ROOT_MISMATCH", "demand does not bind the exact snapshot")
    if demand.metadata.namespace != snapshot.metadata.namespace:
        _fail("EVOLUTION_NAMESPACE_MISMATCH", "demand and snapshot namespaces differ")
    if demand.metadata.governance_ref != snapshot.metadata.governance_ref:
        _fail("EVOLUTION_AUTHORITY_MISMATCH", "demand governance differs from snapshot")
    principal = next(
        (
            item
            for item in snapshot.spec.principals
            if item.principal_id == demand.spec.requester_principal_ref
        ),
        None,
    )
    role = next(
        (
            item
            for item in snapshot.spec.role_definitions
            if item.role_id == demand.spec.accountable_role_ref
        ),
        None,
    )
    if principal is None or role is None:
        _fail("DEMAND_AUTHORITY_UNRESOLVED", "requester or accountable role is absent")
    if (
        principal.status != "active"
        or principal.admission_status is not AdmissionStatus.ADMITTED
        or role.admission_status is not AdmissionStatus.ADMITTED
        or role.role_id not in principal.eligible_role_refs
    ):
        _fail("DEMAND_AUTHORITY_UNRESOLVED", "requester is not eligible for accountable role")
    groups = (
        demand.spec.subject_refs,
        demand.spec.trigger_refs,
        demand.spec.desired_outcome_refs,
        demand.spec.evidence_obligation_refs,
        demand.spec.constraint_refs,
    )
    for index, refs in enumerate(groups):
        _ensure_unique(f"demand.refs.{index}", refs)
        _ensure_namespace(demand.metadata.namespace, refs)
    expected_sources = (
        expected_snapshot.resource_id,
        *(ref.resource_id for ref in demand.spec.subject_refs),
        *(ref.resource_id for ref in demand.spec.trigger_refs),
    )
    if demand.metadata.source_refs != expected_sources:
        _fail("EVOLUTION_PROVENANCE_MISMATCH", "demand metadata.sourceRefs is not exact")


def verify_source_admission_receipt(receipt: SourceAdmissionReceipt) -> None:
    _ensure_authority_envelope(receipt)
    verify_resource_digest(receipt)
    _verify_source_admission_fields(receipt)


def verify_source_admission_receipt_from_admitted(admission: AdmittedSealedResource) -> None:
    receipt = validate_sealed_admission(admission, "SourceAdmissionReceipt").resource
    assert isinstance(receipt, SourceAdmissionReceipt)
    _ensure_authority_envelope(receipt)
    _verify_source_admission_fields(receipt)


def _verify_source_admission_fields(receipt: SourceAdmissionReceipt) -> None:
    spec = receipt.spec
    groups = (
        spec.subject_refs,
        spec.proposer_refs,
        spec.reviewer_refs,
        spec.unresolved_refs,
        spec.admitted_subject_refs,
    )
    for index, refs in enumerate(groups):
        _ensure_unique(f"admission.refs.{index}", refs)
        _ensure_namespace(receipt.metadata.namespace, refs)
    _ensure_namespace(
        receipt.metadata.namespace,
        (spec.intake_profile_ref, spec.decision_authority_ref),
    )
    _ensure_string_set("admission.reasonCodes", spec.reason_codes)
    proposer_keys = {_identity_key(ref) for ref in spec.proposer_refs}
    reviewer_keys = {_identity_key(ref) for ref in spec.reviewer_refs}
    if _identity_key(spec.decision_authority_ref) in proposer_keys or proposer_keys & reviewer_keys:
        _fail("EVOLUTION_SELF_ADMISSION_FORBIDDEN", "proposal authority is not distinct")
    subject_keys = {_ref_key(ref) for ref in spec.subject_refs}
    admitted_keys = {_ref_key(ref) for ref in spec.admitted_subject_refs}
    if not admitted_keys.issubset(subject_keys):
        _fail("SOURCE_ADMISSION_SUBJECT_MISMATCH", "admitted refs are not exact subjects")
    if spec.verdict is AdmissionVerdict.ADMITTED:
        if not admitted_keys or spec.unresolved_refs:
            _fail("SOURCE_ADMISSION_VERDICT_INVALID", "ADMITTED receipt is unresolved or empty")
    elif spec.verdict is AdmissionVerdict.REJECTED:
        if admitted_keys or not spec.reason_codes:
            _fail("SOURCE_ADMISSION_VERDICT_INVALID", "REJECTED receipt lacks reasons")
    elif admitted_keys or (not spec.reason_codes and not spec.unresolved_refs):
        _fail("SOURCE_ADMISSION_VERDICT_INVALID", "UNKNOWN receipt lacks uncertainty")
    expected_sources = (
        *(ref.resource_id for ref in spec.subject_refs),
        spec.intake_profile_ref.resource_id,
        spec.decision_authority_ref.resource_id,
    )
    if receipt.metadata.source_refs != expected_sources:
        _fail("EVOLUTION_PROVENANCE_MISMATCH", "admission metadata.sourceRefs is not exact")
    successor_fields = (
        spec.predecessor_source_root,
        spec.evolution_root,
        spec.candidate_ref,
        spec.governance_decision_ref,
    )
    if spec.admission_purpose == "successor_promotion":
        if any(value is None for value in successor_fields):
            _fail("SUCCESSOR_LINEAGE_INCOMPLETE", "successor admission lineage is incomplete")
        assert spec.candidate_ref is not None
        assert spec.governance_decision_ref is not None
        _ensure_namespace(
            receipt.metadata.namespace,
            (spec.candidate_ref, spec.governance_decision_ref),
        )
        if _ref_key(spec.candidate_ref) not in subject_keys:
            _fail("SUCCESSOR_LINEAGE_INCOMPLETE", "candidate is not an admission subject")
    elif any(value is not None for value in successor_fields):
        _fail("SUCCESSOR_LINEAGE_INCOMPLETE", "intake receipt carries successor fields")


def outcome_certificate_source_refs(spec: OutcomeCertificateSpec) -> tuple[str, ...]:
    """Project provenance using the certificate's declared profile.

    The default contract preserves every source role, including cross-role reuse.
    Explicit profiles list each ID once, in first-appearance order. Neither
    projection validates or admits the referenced objects.
    """
    authorization_refs = (spec.execution_authorization_ref,) if spec.execution_authorization_ref is not None else ()
    source_ids = (
        spec.snapshot_ref.resource_id,
        *(ref.resource_id for ref in spec.source_admission_receipt_refs),
        spec.demand_ref.resource_id,
        *(ref.resource_id for ref in spec.change_refs),
        spec.plan_ref.resource_id,
        spec.plan_certificate_ref.resource_id,
        spec.runtime_binding_ref.resource_id,
        spec.runtime_bundle_ref.resource_id,
        spec.execution_receipt_ref.resource_id,
        *(ref.resource_id for ref in spec.execution_evidence_refs),
        *(ref.resource_id for ref in spec.observation_refs),
        spec.oracle_ref.resource_id,
        spec.observation_profile_ref.resource_id,
        *(ref.resource_id for ref in spec.evidence_refs),
        *(ref.resource_id for ref in spec.unresolved_refs),
        *(ref.resource_id for ref in authorization_refs),
    )
    return source_ids if spec.profile_binding is None else tuple(dict.fromkeys(source_ids))


def verify_outcome_certificate(certificate: OutcomeCertificate) -> None:
    _ensure_authority_envelope(certificate)
    verify_resource_digest(certificate)
    _verify_outcome_fields(certificate)


def verify_outcome_certificate_from_admitted(admission: AdmittedSealedResource) -> None:
    certificate = validate_sealed_admission(admission, "OutcomeCertificate").resource
    assert isinstance(certificate, OutcomeCertificate)
    _ensure_authority_envelope(certificate)
    _verify_outcome_fields(certificate)


def _verify_outcome_fields(certificate: OutcomeCertificate) -> None:
    spec = certificate.spec
    _ensure_kind(spec.snapshot_ref, "OrganizationSnapshot")
    _ensure_kind(spec.demand_ref, "OrganizationalDemand")
    _ensure_kind(spec.plan_ref, "OrganizationPlan")
    _ensure_kind(spec.plan_certificate_ref, "PlanCertificate")
    profile = get_outcome_profile(spec.profile_binding) if spec.profile_binding is not None else None
    # Preserve the default verifier's kind-check order as well as its root bytes.
    _ensure_kind(spec.runtime_binding_ref, profile.runtime_binding_kind if profile else "RuntimeBinding")
    _ensure_kind(spec.runtime_bundle_ref, profile.runtime_bundle_kind if profile else "ZeroEffectRuntimeBundle")
    _ensure_kind(spec.execution_receipt_ref, "ExecutionReceipt")
    authorization_refs = (spec.execution_authorization_ref,) if spec.execution_authorization_ref is not None else ()
    refs = (
        spec.snapshot_ref,
        *spec.source_admission_receipt_refs,
        spec.demand_ref,
        *spec.change_refs,
        spec.plan_ref,
        spec.plan_certificate_ref,
        spec.runtime_binding_ref,
        spec.runtime_bundle_ref,
        spec.execution_receipt_ref,
        *spec.execution_evidence_refs,
        *spec.observation_refs,
        *spec.acting_principal_refs,
        spec.oracle_ref,
        spec.observation_profile_ref,
        *spec.evidence_refs,
        *spec.unresolved_refs,
        *authorization_refs,
    )
    _ensure_namespace(certificate.metadata.namespace, refs)
    for index, group in enumerate(
        (
            spec.source_admission_receipt_refs,
            spec.change_refs,
            spec.execution_evidence_refs,
            spec.observation_refs,
            spec.acting_principal_refs,
            spec.evidence_refs,
            spec.unresolved_refs,
        )
    ):
        _ensure_unique(f"outcome.refs.{index}", group)
    _ensure_string_set("outcome.reasonCodes", spec.reason_codes)
    if _identity_key(spec.oracle_ref) in {
        _identity_key(ref) for ref in spec.acting_principal_refs
    }:
        _fail("OUTCOME_SELF_CERTIFICATION_FORBIDDEN", "runtime actor is the outcome oracle")
    if spec.oracle_ref.kind in {"ExecutionReceipt", "RuntimeLoweringReceipt"}:
        _fail("OUTCOME_SELF_CERTIFICATION_FORBIDDEN", "runtime evidence is not an oracle")
    expected_roots = (
        project_source_root(spec.snapshot_ref, spec.source_admission_receipt_refs),
        project_demand_root(spec.demand_ref, spec.change_refs),
        project_plan_root(spec.plan_ref, spec.plan_certificate_ref),
        project_execution_root(
            spec.runtime_binding_ref,
            spec.runtime_bundle_ref,
            spec.execution_receipt_ref,
            spec.execution_evidence_refs,
            profile_binding=spec.profile_binding,
            execution_authorization_ref=spec.execution_authorization_ref,
        ),
    )
    if expected_roots != (
        spec.source_root,
        spec.demand_root,
        spec.plan_root,
        spec.execution_root,
    ):
        _fail("EVOLUTION_ROOT_MISMATCH", "outcome certificate roots do not recompute")
    expected_sources = outcome_certificate_source_refs(spec)
    if certificate.metadata.source_refs != expected_sources:
        _fail("EVOLUTION_PROVENANCE_MISMATCH", "outcome metadata.sourceRefs is not exact")
    dimension_names = tuple(item.name for item in spec.dimensions)
    if len(dimension_names) != len(set(dimension_names)):
        _fail("OUTCOME_VERDICT_INVALID", "outcome dimension names are duplicated")
    if profile is not None:
        _verify_disposable_dimensions(certificate, profile.required_dimensions)
    verdicts = {item.verdict for item in spec.dimensions}
    if spec.verdict is OutcomeVerdict.ACCEPT:
        coherent = verdicts == {DimensionVerdict.PASS} and not spec.reason_codes and not spec.unresolved_refs
    elif spec.verdict is OutcomeVerdict.REJECT:
        coherent = DimensionVerdict.FAIL in verdicts and bool(spec.reason_codes)
    elif spec.verdict is OutcomeVerdict.UNKNOWN:
        coherent = DimensionVerdict.UNKNOWN in verdicts and bool(
            spec.reason_codes or spec.unresolved_refs
        )
    else:
        coherent = DimensionVerdict.FAIL not in verdicts and bool(
            spec.reason_codes or spec.unresolved_refs
        )
    if not coherent:
        _fail("OUTCOME_VERDICT_INVALID", "outcome verdict and dimensions disagree")


def _verify_disposable_dimensions(
    certificate: OutcomeCertificate, required_dimensions: Sequence[str]
) -> None:
    """Conserve observed failure and uncertainty within the declared evidence closure."""
    spec = certificate.spec
    if {item.name for item in spec.dimensions} != set(required_dimensions):
        _fail("OUTCOME_DIMENSION_SET_INVALID", "disposable outcome requires the exact six dimensions")
    _ensure_kind(spec.oracle_ref, "OutcomeOracle")
    _ensure_kind(spec.observation_profile_ref, "ObservationProfile")
    for actor in spec.acting_principal_refs:
        _ensure_kind(actor, "Principal")
    runtime_refs = (
        spec.runtime_binding_ref, spec.runtime_bundle_ref, spec.execution_receipt_ref,
        *spec.execution_evidence_refs,
    )
    if spec.execution_authorization_ref is not None:
        runtime_refs += (spec.execution_authorization_ref,)
    if _identity_key(spec.oracle_ref) in {_identity_key(ref) for ref in runtime_refs}:
        _fail("OUTCOME_SELF_CERTIFICATION_FORBIDDEN", "runtime evidence/grant identity is not an outcome oracle")
    evidence = {_ref_key(ref) for ref in spec.evidence_refs}
    unresolved: set[tuple[str, str, str, int, str]] = set()
    reasons: set[str] = set()
    verdicts: set[DimensionVerdict] = set()
    for dimension in spec.dimensions:
        _ensure_namespace(certificate.metadata.namespace, (*dimension.evidence_refs, *dimension.unresolved_refs))
        _ensure_unique(f"{dimension.name}.evidenceRefs", dimension.evidence_refs)
        _ensure_unique(f"{dimension.name}.unresolvedRefs", dimension.unresolved_refs)
        _ensure_string_set(f"{dimension.name}.reasonCodes", dimension.reason_codes)
        if not {_ref_key(ref) for ref in dimension.evidence_refs} <= evidence:
            _fail("OUTCOME_DIMENSION_EVIDENCE_INVALID", "dimension evidence is absent from certificate evidenceRefs")
        if dimension.verdict is DimensionVerdict.PASS:
            coherent = bool(dimension.evidence_refs) and not dimension.reason_codes and not dimension.unresolved_refs
        elif dimension.verdict is DimensionVerdict.FAIL:
            coherent = bool(dimension.evidence_refs) and bool(dimension.reason_codes) and not dimension.unresolved_refs
        else:
            coherent = bool(dimension.unresolved_refs) and bool(dimension.reason_codes)
        if not coherent:
            _fail("OUTCOME_DIMENSION_EVIDENCE_INVALID", "dimension verdict lacks coherent evidence/reasons/unresolved refs")
        unresolved.update(_ref_key(ref) for ref in dimension.unresolved_refs)
        reasons.update(dimension.reason_codes)
        verdicts.add(dimension.verdict)
    if unresolved != {_ref_key(ref) for ref in spec.unresolved_refs} or reasons != set(spec.reason_codes):
        _fail("OUTCOME_DIMENSION_EVIDENCE_INVALID", "certificate must retain the exact dimension reasons and unresolved refs")
    expected = (
        OutcomeVerdict.REJECT if DimensionVerdict.FAIL in verdicts else
        OutcomeVerdict.UNKNOWN if DimensionVerdict.UNKNOWN in verdicts else
        OutcomeVerdict.ACCEPT
    )
    if spec.verdict is not expected:
        _fail("OUTCOME_VERDICT_INVALID", "failure and uncertainty cannot be downgraded or promoted")


def verify_successor_admission(
    receipt: SourceAdmissionReceipt,
    *,
    predecessor_source_root: str,
    candidate_refs: Sequence[ResourceRef],
    supporting_outcome_refs: Sequence[ResourceRef],
    counterexample_outcome_refs: Sequence[ResourceRef],
    replay_evidence_refs: Sequence[ResourceRef],
    governance_decision_ref: ResourceRef,
) -> str:
    verify_source_admission_receipt(receipt)
    if receipt.spec.admission_purpose != "successor_promotion":
        _fail("SUCCESSOR_LINEAGE_INCOMPLETE", "receipt is not a successor admission")
    if receipt.spec.verdict is not AdmissionVerdict.ADMITTED:
        _fail("EVOLUTION_AUTO_PROMOTION_FORBIDDEN", "candidate was not admitted")
    expected = project_evolution_root(
        candidate_refs,
        supporting_outcome_refs,
        counterexample_outcome_refs,
        replay_evidence_refs,
        governance_decision_ref,
    )
    if receipt.spec.predecessor_source_root != predecessor_source_root:
        _fail("SUCCESSOR_PREDECESSOR_MISMATCH", "predecessor Source root drifted")
    if receipt.spec.evolution_root != expected:
        _fail("EVOLUTION_ROOT_MISMATCH", "receipt does not bind the exact evolution root")
    if receipt.spec.governance_decision_ref != governance_decision_ref:
        _fail("EVOLUTION_AUTHORITY_MISMATCH", "governance decision was substituted")
    if receipt.spec.candidate_ref is None or receipt.spec.candidate_ref not in candidate_refs:
        _fail("SUCCESSOR_LINEAGE_INCOMPLETE", "receipt candidate is not in evolution root")
    return expected
