from __future__ import annotations

from dataclasses import replace

import pytest
import rfc8785
from test_evolution import _demand, _intake_receipt
from test_runtime_lowering import _binding, _plan, _roots

from oac.canonical import OACValidationError, calculate_digest, resource_ref, seal_resource
from oac.intake_state import project_intake_state
from oac.lowering import lower_plan_from_admitted
from oac.models import AdmissionVerdict, ResourceBase, Verdict
from oac.sealed import admit_sealed_resource
from oac.verifier import verify_plan_from_admitted


def _admit(resource: ResourceBase):
    return admit_sealed_resource(resource.model_dump_json(by_alias=True).encode(), resource.kind)


def _bound_demand(snapshot, change):
    demand = _demand(snapshot)
    return seal_resource(demand.model_copy(update={
        "metadata": demand.metadata.model_copy(update={
            "id": change.spec.demand_ref,
            "source_refs": (snapshot.metadata.id,
                            *(ref.resource_id for ref in demand.spec.subject_refs), change.metadata.id),
        }),
        "spec": demand.spec.model_copy(update={"trigger_refs": (resource_ref(change),)}),
    }))


@pytest.fixture
def inputs():
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    admissions = tuple(map(_admit, (snapshot, change, plan)))
    certificate = verify_plan_from_admitted(*admissions)
    assert certificate.spec.verdict is Verdict.ACCEPT
    return snapshot, change, admissions, certificate


def test_plan_acceptance_has_eight_separate_axes_and_no_derived_runtime_success(inputs):
    snapshot, _, (s, c, p), certificate = inputs
    result = project_intake_state(s, c, source_receipt=_intake_receipt(snapshot),
                                  plan=p, certificate=certificate)
    assert result.source_authority.value == "ADMITTED"
    assert result.observability.value == "KNOWN"
    assert result.plan_assurance.value == "ACCEPT"
    assert result.runtime_admission.value == "NOT_BOUND"
    assert result.execution.value == "NOT_RUN"
    assert result.outcome_assurance.value == "NOT_EVALUATED"
    assert result.evolution_governance.value == "OBSERVATION"
    value = result.as_json()
    assert len(value["axes"]) == 8
    assert "kind" not in value and "allGood" not in value
    assert value["rootRefs"]["execution"] is None
    assert value["rootRefs"]["outcome"] is None
    assert value["rootRefs"]["evolution"] is None
    assert rfc8785.dumps(value) == rfc8785.dumps(result.as_json())
    assert result.source_root and result.plan_root
    assert result.demand_root is None
    assert result.plan_assurance.evidence_refs[-1] == resource_ref(certificate)


def test_absent_admission_cannot_be_inferred_from_an_accepted_plan(inputs):
    _, _, (s, c, p), certificate = inputs
    state = project_intake_state(s, c, plan=p, certificate=certificate)
    assert state.source_authority.value == "CANDIDATE"
    assert state.source_root is None
    assert state.plan_assurance.value == "ACCEPT"
    without_plan = project_intake_state(s, c)
    assert without_plan.plan_assurance.value is None
    assert without_plan.plan_root is None


@pytest.mark.parametrize("verdict", (AdmissionVerdict.UNKNOWN, AdmissionVerdict.REJECTED))
def test_nonadmission_preserves_receipt_evidence_without_admitted_source_root(inputs, verdict):
    snapshot, _, (s, c, _), _ = inputs
    receipt = _intake_receipt(snapshot)
    receipt = seal_resource(receipt.model_copy(update={"spec": receipt.spec.model_copy(update={
        "verdict": verdict, "admitted_subject_refs": (),
        "reason_codes": ("EVOLUTION_AUTHORITY_MISMATCH",),
    })}))
    state = project_intake_state(s, c, source_receipt=receipt)
    assert state.source_authority.value == "CANDIDATE"
    assert verdict.value in state.source_authority.basis
    assert state.source_authority.evidence_refs[-1] == resource_ref(receipt)
    assert state.source_root is None


def test_unresolved_supplier_state_does_not_become_false_or_success():
    snapshot, change = _roots("SC-010")
    state = project_intake_state(_admit(snapshot), _admit(change))
    assert state.applicability_impact.value == "UNKNOWN"
    assert state.execution.value == "NOT_RUN"
    assert state.outcome_assurance.value == "NOT_EVALUATED"


@pytest.mark.parametrize("after_state,expected", (("unknown", "UNKNOWN"), ("not_observable", "NOT_OBSERVABLE"), ("not_applicable", "NOT_APPLICABLE")))
def test_observation_axis_uses_explicit_values_not_plan_status(inputs, after_state, expected):
    _, change, (s, _, _), _ = inputs
    delta = change.spec.deltas[0]
    updated = delta.model_copy(update={
        "operation": {"unknown": "unknown_transition", "not_observable": "invalidate", "not_applicable": "remove"}[after_state],
        "after": delta.after.model_copy(update={"state": after_state, "value": None}),
    })
    change = seal_resource(change.model_copy(update={"spec": change.spec.model_copy(update={"deltas": (updated,)})}))
    state = project_intake_state(s, _admit(change))
    assert state.observability.value == expected
    assert state.plan_assurance.value is None


def test_demand_root_requires_receipt_for_exact_demand_and_change(inputs):
    snapshot, change, (s, c, _), _ = inputs
    demand = _bound_demand(snapshot, change)
    receipt = _intake_receipt(snapshot)
    assert project_intake_state(s, c, source_receipt=receipt, demand=_admit(demand)).demand_root is None
    subjects = (resource_ref(snapshot), resource_ref(demand), resource_ref(change))
    receipt = seal_resource(receipt.model_copy(update={
        "metadata": receipt.metadata.model_copy(update={"source_refs": (
            *(ref.resource_id for ref in subjects), receipt.spec.intake_profile_ref.resource_id,
            receipt.spec.decision_authority_ref.resource_id,
        )}),
        "spec": receipt.spec.model_copy(update={"subject_refs": subjects, "admitted_subject_refs": subjects}),
    }))
    state = project_intake_state(s, c, source_receipt=receipt, demand=_admit(demand))
    assert state.demand_root
    substituted = seal_resource(demand.model_copy(update={"metadata": demand.metadata.model_copy(update={"id": "demand:substituted"})}))
    with pytest.raises(OACValidationError) as error:
        project_intake_state(s, c, source_receipt=receipt, demand=_admit(substituted))
    assert error.value.reason_code == "RESOURCE_COHERENCE_VIOLATION"


@pytest.mark.parametrize("field,value", (
    ("requester_principal_ref", "principal:nonexistent"),
    ("accountable_role_ref", "role:nonexistent"),
    ("accountable_role_ref", "role:quality-qualification"),
))
def test_exact_receipt_cannot_make_invalid_demand_authority_valid(inputs, field, value):
    snapshot, change, (s, c, _), _ = inputs
    demand = _bound_demand(snapshot, change)
    demand = seal_resource(demand.model_copy(update={
        "metadata": demand.metadata.model_copy(update={"id": change.spec.demand_ref}),
        "spec": demand.spec.model_copy(update={field: value}),
    }))
    receipt = _intake_receipt(snapshot)
    subjects = (resource_ref(snapshot), resource_ref(demand), resource_ref(change))
    receipt = seal_resource(receipt.model_copy(update={
        "metadata": receipt.metadata.model_copy(update={"source_refs": (
            *(ref.resource_id for ref in subjects), receipt.spec.intake_profile_ref.resource_id,
            receipt.spec.decision_authority_ref.resource_id,
        )}),
        "spec": receipt.spec.model_copy(update={"subject_refs": subjects, "admitted_subject_refs": subjects}),
    }))
    with pytest.raises(OACValidationError) as error:
        project_intake_state(s, c, source_receipt=receipt, demand=_admit(demand))
    assert error.value.reason_code == "DEMAND_AUTHORITY_UNRESOLVED"


@pytest.mark.parametrize("field,value", (("digest", "sha256:" + "9" * 64), ("revision", 99)))
def test_demand_trigger_requires_exact_change_ref_even_with_exact_admission_receipt(inputs, field, value):
    snapshot, change, (s, c, _), _ = inputs
    demand = _bound_demand(snapshot, change)
    demand = seal_resource(demand.model_copy(update={
        "spec": demand.spec.model_copy(update={
            "trigger_refs": (resource_ref(change).model_copy(update={field: value}),),
        }),
    }))
    receipt = _intake_receipt(snapshot)
    subjects = (resource_ref(snapshot), resource_ref(demand), resource_ref(change))
    receipt = seal_resource(receipt.model_copy(update={
        "metadata": receipt.metadata.model_copy(update={"source_refs": (
            *(ref.resource_id for ref in subjects), receipt.spec.intake_profile_ref.resource_id,
            receipt.spec.decision_authority_ref.resource_id,
        )}),
        "spec": receipt.spec.model_copy(update={"subject_refs": subjects, "admitted_subject_refs": subjects}),
    }))
    with pytest.raises(OACValidationError) as error:
        project_intake_state(s, c, source_receipt=receipt, demand=_admit(demand))
    assert error.value.reason_code == "DEMAND_ROOT_MISMATCH"


@pytest.mark.parametrize("admitted", (False, True))
def test_g1a_evidence_is_recomputed_and_never_displayed_as_runtime_admission(inputs, admitted):
    snapshot, _, (s, c, p), certificate = inputs
    binding = _binding(snapshot, p.resource, certificate)
    b = _admit(binding)
    allowlist = (binding.digest,) if admitted else ()
    result = lower_plan_from_admitted(s, c, p, _admit(certificate), b,
                                     admitted_binding_digests=allowlist)
    state = project_intake_state(s, c, plan=p, certificate=certificate,
                                runtime_binding=b, lowering=result,
                                admitted_binding_digests=allowlist)
    assert state.runtime_admission.value == ("BOUND_NOT_ADMITTED" if admitted else "BLOCKED")
    assert state.execution.value == "NOT_RUN"
    assert state.outcome_assurance.value == "NOT_EVALUATED"
    assert state.evolution_governance.value == "OBSERVATION"
    assert resource_ref(result.receipt) in state.runtime_admission.evidence_refs
    state_without_lowering = project_intake_state(s, c, plan=p, certificate=certificate, runtime_binding=b)
    assert state_without_lowering.runtime_admission.value == "BOUND_NOT_ADMITTED"
    forged = replace(result, receipt=seal_resource(result.receipt.model_copy(update={
        "metadata": result.receipt.metadata.model_copy(update={"id": "receipt:substituted"}),
    })))
    with pytest.raises(OACValidationError, match="lowering evidence differs"):
        project_intake_state(s, c, plan=p, certificate=certificate, runtime_binding=b,
                             lowering=forged, admitted_binding_digests=allowlist)


@pytest.mark.parametrize("attack", ("receipt", "certificate", "binding", "plan_only", "certificate_only", "binding_only", "lowering_only"))
def test_cross_stage_substitution_and_incomplete_evidence_are_rejected(inputs, attack):
    snapshot, _, (s, c, p), certificate = inputs
    binding = _binding(snapshot, p.resource, certificate)
    options = {"plan": p, "certificate": certificate}
    if attack == "receipt":
        receipt = _intake_receipt(snapshot)
        options["source_receipt"] = seal_resource(receipt.model_copy(update={
            "metadata": receipt.metadata.model_copy(update={"governance_ref": "policy:foreign"}),
        }))
    elif attack == "certificate":
        options["certificate"] = seal_resource(certificate.model_copy(update={
            "metadata": certificate.metadata.model_copy(update={"id": "certificate:foreign"}),
        }))
    elif attack == "binding":
        options["runtime_binding"] = _admit(seal_resource(binding.model_copy(update={
            "metadata": binding.metadata.model_copy(update={"governance_ref": "policy:foreign"}),
        })))
    elif attack == "plan_only":
        del options["certificate"]
    elif attack == "certificate_only":
        del options["plan"]
    elif attack == "binding_only":
        options = {"runtime_binding": _admit(binding)}
    else:
        options["lowering"] = lower_plan_from_admitted(s, c, p, _admit(certificate), _admit(binding), admitted_binding_digests=())
    with pytest.raises(OACValidationError) as error:
        project_intake_state(s, c, **options)
    assert error.value.reason_code == "EVOLUTION_ROOT_MISMATCH"


def test_omitted_optional_members_keep_the_raw_plan_identity(inputs):
    _, _, (s, c, p), _ = inputs
    raw = p.resource.model_dump(mode="json", by_alias=True)
    del raw["metadata"]["effectiveFrom"]
    raw["digest"] = calculate_digest(raw)
    p = admit_sealed_resource(rfc8785.dumps(raw), "OrganizationPlan")
    certificate = verify_plan_from_admitted(s, c, p)
    state = project_intake_state(s, c, plan=p, certificate=certificate)
    assert state.plan_assurance.value == "ACCEPT"
    assert state.plan_assurance.evidence_refs[0].digest == raw["digest"]


def test_caller_cannot_inject_execution_outcome_or_promotion_state(inputs):
    _, _, (s, c, _), _ = inputs
    for name in ("execution", "outcome_assurance", "evolution_governance"):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            project_intake_state(s, c, **{name: "SUCCEEDED"})
