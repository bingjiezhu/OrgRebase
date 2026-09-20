"""Evidence-bound, read-only state projection for the Enterprise Intake profile.

The view ends at zero-effect lowering.  It neither records runtime execution nor
admits experience, and it is never an input to a governance transition.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import NoReturn

from .canonical import OACValidationError, resource_ref
from .enterprise_intake import verify_intake_change_link
from .evolution import (
    project_demand_root,
    project_plan_root,
    project_source_root,
    verify_organizational_demand_from_admitted,
    verify_source_admission_receipt,
)
from .lowering import LoweringResult, lower_plan_from_admitted
from .models import (
    AdmissionVerdict,
    ImpactState,
    LoweringStatus,
    OrganizationalDemand,
    OrganizationSnapshot,
    PlanCertificate,
    ResourceRef,
    RuntimeBinding,
    SemanticChangeSet,
    SourceAdmissionReceipt,
)
from .sealed import AdmittedSealedResource, admit_sealed_resource, validate_sealed_admission
from .supplier import (
    ProfileError,
    derive_supplier_contract_from_admitted,
    validate_supplier_input_coherence,
)
from .verifier import verify_plan_from_admitted

INTAKE_STATE_PROFILE = "oac.enterprise-intake/eight-axis/v0.1"


@dataclass(frozen=True, slots=True)
class StateAxis:
    value: str | None
    evidence_refs: tuple[ResourceRef, ...]
    basis: str


@dataclass(frozen=True, slots=True)
class IntakeStateVector:
    source_authority: StateAxis
    observability: StateAxis
    applicability_impact: StateAxis
    plan_assurance: StateAxis
    runtime_admission: StateAxis
    execution: StateAxis
    outcome_assurance: StateAxis
    evolution_governance: StateAxis
    source_root: str | None
    demand_root: str | None
    plan_root: str | None

    def as_json(self) -> dict[str, object]:
        axes = {
            "sourceAuthority": self.source_authority,
            "observability": self.observability,
            "applicabilityImpact": self.applicability_impact,
            "planAssurance": self.plan_assurance,
            "runtimeAdmission": self.runtime_admission,
            "execution": self.execution,
            "outcomeAssurance": self.outcome_assurance,
            "evolutionGovernance": self.evolution_governance,
        }
        return {
            "projectionProfile": INTAKE_STATE_PROFILE,
            "scope": "intake_and_zero_effect_lowering",
            "axes": {
                name: {
                    "value": axis.value,
                    "evidenceRefs": [ref.model_dump(mode="json", by_alias=True)
                                     for ref in axis.evidence_refs],
                    "basis": axis.basis,
                }
                for name, axis in axes.items()
            },
            "rootRefs": {
                "source": self.source_root,
                "demand": self.demand_root,
                "plan": self.plan_root,
                "execution": None,
                "outcome": None,
                "evolution": None,
            },
        }


def _ref(admission: AdmittedSealedResource) -> ResourceRef:
    resource = admission.resource
    return ResourceRef(
        kind=resource.kind,
        namespace=resource.metadata.namespace,
        resourceId=resource.metadata.id,
        revision=resource.metadata.revision,
        digest=admission.resource_digest,
    )


def _binding_error(message: str) -> NoReturn:
    raise OACValidationError("EVOLUTION_ROOT_MISMATCH", message)


def project_intake_state(
    snapshot: AdmittedSealedResource,
    change: AdmittedSealedResource,
    *,
    source_receipt: SourceAdmissionReceipt | None = None,
    demand: AdmittedSealedResource | None = None,
    plan: AdmittedSealedResource | None = None,
    certificate: PlanCertificate | None = None,
    runtime_binding: AdmittedSealedResource | None = None,
    lowering: LoweringResult | None = None,
    admitted_binding_digests: Collection[str] = (),
) -> IntakeStateVector:
    """Project independently checked evidence without creating stage authority.

    Source authority describes receipt-bound resource envelopes, not every nested
    fact. Observability summarizes the change's explicit after-values. Impact is
    UNKNOWN if any scoped derivation is unresolved; otherwise it is TRUE when an
    affected path exists and FALSE only when every path proves non-impact or is
    explicitly out of scope. Exact roots remain available for the detailed view.

    Receipt authenticity and the binding allowlist are caller-owned boundaries.
    Even a produced G1a bundle is BOUND_NOT_ADMITTED, never a runtime execution.
    """
    snapshot = validate_sealed_admission(snapshot, "OrganizationSnapshot")
    change = validate_sealed_admission(change, "SemanticChangeSet")
    snapshot_value, change_value = snapshot.resource, change.resource
    assert isinstance(snapshot_value, OrganizationSnapshot)
    assert isinstance(change_value, SemanticChangeSet)
    root_refs = (_ref(snapshot), _ref(change))
    validate_supplier_input_coherence(snapshot_value, change_value)

    source_root = None
    admitted_refs: tuple[ResourceRef, ...] = ()
    source = StateAxis("CANDIDATE", (root_refs[0],), "No admission receipt was supplied.")
    if source_receipt is not None:
        verify_source_admission_receipt(source_receipt)
        if (source_receipt.spec.admission_purpose != "enterprise_intake"
                or source_receipt.metadata.governance_ref != snapshot_value.metadata.governance_ref
                or root_refs[0] not in source_receipt.spec.subject_refs):
            _binding_error("source receipt does not review the exact intake Snapshot")
        admitted_refs = source_receipt.spec.admitted_subject_refs
        receipt_ref = resource_ref(source_receipt)
        if (source_receipt.spec.verdict is AdmissionVerdict.ADMITTED
                and root_refs[0] in admitted_refs):
            source_root = project_source_root(root_refs[0], (receipt_ref,))
            source = StateAxis("ADMITTED", (root_refs[0], receipt_ref),
                               "The receipt admits this resource envelope; nested fact authority is unchanged.")
        else:
            source = StateAxis("CANDIDATE", (root_refs[0], receipt_ref),
                               f"The supplied receipt verdict is {source_receipt.spec.verdict.value}; no Source root is admitted.")

    states = {delta.after.state for delta in change_value.spec.deltas}
    observable = (
        "NOT_OBSERVABLE" if "not_observable" in states
        else "UNKNOWN" if "unknown" in states
        else "NOT_APPLICABLE" if states == {"not_applicable"}
        else "KNOWN"
    )
    impact_basis = "Existing Supplier derivation; unresolved scoped facts remain UNKNOWN."
    try:
        derived = derive_supplier_contract_from_admitted(snapshot, change)
    except ProfileError as exc:
        impact = "UNKNOWN"
        impact_basis = f"Supplier derivation could not establish impact: {exc.reason_code}."
    else:
        path_states = {path.state for path in derived.impact_paths}
        impact = (
            "UNKNOWN" if (derived.root_applicability_unknown or derived.unresolved_refs
                          or ImpactState.UNKNOWN in path_states or not path_states)
            else "TRUE" if ImpactState.AFFECTED in path_states
            else "FALSE"
        )

    demand_root = None
    if demand is not None:
        demand = validate_sealed_admission(demand, "OrganizationalDemand")
        verify_organizational_demand_from_admitted(demand, snapshot)
        verify_intake_change_link(demand, change)
        demand_value = demand.resource
        assert isinstance(demand_value, OrganizationalDemand)
        if source_root is not None and all(ref in admitted_refs for ref in (_ref(demand), root_refs[1])):
            demand_root = project_demand_root(_ref(demand), (root_refs[1],))

    plan_root = None
    plan_axis = StateAxis(None, (), "No independently recomputed PlanCertificate was supplied.")
    if (plan is None) != (certificate is None):
        _binding_error("Plan and PlanCertificate must be supplied together")
    if plan is not None and certificate is not None:
        plan = validate_sealed_admission(plan, "OrganizationPlan")
        recomputed = verify_plan_from_admitted(snapshot, change, plan)
        if certificate != recomputed:
            _binding_error("PlanCertificate differs from the independent verifier result")
        certificate_ref = resource_ref(certificate)
        plan_root = project_plan_root(_ref(plan), certificate_ref)
        plan_axis = StateAxis(certificate.spec.verdict.value, (_ref(plan), certificate_ref),
                              "Exact Plan assurance; it grants no Source or runtime authority.")

    runtime = StateAxis("NOT_BOUND", (), "No RuntimeBinding was supplied.")
    if runtime_binding is None and lowering is not None:
        _binding_error("a lowering receipt requires its exact RuntimeBinding")
    if runtime_binding is not None:
        if plan is None or certificate is None:
            _binding_error("RuntimeBinding requires its exact Plan and PlanCertificate")
        assert plan is not None and certificate is not None
        runtime_binding = validate_sealed_admission(runtime_binding, "RuntimeBinding")
        binding_value = runtime_binding.resource
        assert isinstance(binding_value, RuntimeBinding)
        if (binding_value.spec.subject_plan_ref != _ref(plan)
                or binding_value.spec.subject_certificate_ref != resource_ref(certificate)
                or binding_value.metadata.namespace != snapshot_value.metadata.namespace
                or binding_value.metadata.governance_ref != snapshot_value.metadata.governance_ref):
            _binding_error("RuntimeBinding does not bind this exact Plan assurance")
        runtime = StateAxis("BOUND_NOT_ADMITTED", (_ref(runtime_binding),),
                            "A binding names runtime subjects; runtime admission has not occurred.")
        if lowering is not None:
            certificate_admission = admit_sealed_resource(
                certificate.model_dump_json(by_alias=True).encode(), "PlanCertificate")
            expected = lower_plan_from_admitted(
                snapshot, change, plan, certificate_admission, runtime_binding,
                admitted_binding_digests=admitted_binding_digests,
            )
            if lowering != expected:
                _binding_error("lowering evidence differs from the exact G1a result")
            evidence: tuple[ResourceRef, ...] = (_ref(runtime_binding), resource_ref(lowering.receipt))
            if lowering.bundle is not None:
                evidence += (resource_ref(lowering.bundle),)
            runtime = StateAxis(
                "BLOCKED" if lowering.receipt.spec.status is LoweringStatus.BLOCKED else "BOUND_NOT_ADMITTED",
                evidence, "G1a lowering does not invoke or admit a runtime.",
            )

    return IntakeStateVector(
        source, StateAxis(observable, (root_refs[1],), "Explicit Change after-values only."),
        StateAxis(impact, root_refs, impact_basis),
        plan_axis, runtime,
        StateAxis("NOT_RUN", (), "This projection profile performs no execution."),
        StateAxis("NOT_EVALUATED", (), "This projection profile evaluates no business Outcome."),
        StateAxis("OBSERVATION", (), "A state view is observation, never evolution admission."),
        source_root, demand_root, plan_root,
    )
