"""One deterministic, replaceable compiler for closed OAC change profiles."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from .canonical import seal_resource
from .change_profiles import SUPPLIER_PROFILE
from .identifiers import instance_identifier
from .models import (
    AdmissionStatus,
    CoverageObligation,
    EffectCeiling,
    MinimalityClaim,
    OrderConstraint,
    OrganizationPlan,
    OrganizationPlanSpec,
    OrganizationSnapshot,
    PlanDecision,
    Principal,
    ResourceMetadata,
    ResourceRef,
    RoleInstance,
    SemanticChangeSet,
    WorkUnit,
)
from .resource_profile import enforce_plan_output
from .sealed import AdmittedSealedResource, validate_sealed_admission
from .supplier import (
    DerivedContract,
    ProfileError,
    RequiredOrder,
    derive_change_contract,
    derive_change_contract_from_admitted,
)

COMPILER_ID = "oac.reference.supplier-change"
COMPILER_VERSION = "0.3.0a0"


class CompilationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _stable_suffix(value: str) -> str:
    return value.rsplit(":", 1)[-1]


def _assign_principals(
    snapshot: OrganizationSnapshot, required_roles: tuple[str, ...]
) -> dict[str, Principal]:
    roles = {role.role_id: role for role in snapshot.spec.role_definitions}
    constraints = {
        frozenset((item.left_role_ref, item.right_role_ref))
        for item in snapshot.spec.separation_constraints
    }
    candidates: dict[str, tuple[Principal, ...]] = {}
    for role_ref in required_roles:
        role = roles.get(role_ref)
        if role is None or role.admission_status is not AdmissionStatus.ADMITTED:
            raise CompilationError("ROLE_SCOPE_WIDENED", f"role is not admitted: {role_ref}")
        qualified = tuple(
            sorted(
                (
                    principal
                    for principal in snapshot.spec.principals
                    if principal.admission_status is AdmissionStatus.ADMITTED
                    and principal.status == "active"
                    and role_ref in principal.eligible_role_refs
                    and set(role.required_qualifications).issubset(principal.qualification_refs)
                ),
                key=lambda item: item.principal_id,
            )
        )
        if not qualified:
            raise CompilationError("QUALIFICATION_INVALID", f"no qualified principal for {role_ref}")
        candidates[role_ref] = qualified

    result: dict[str, Principal] = {}

    def search(index: int) -> bool:
        if index == len(required_roles):
            return True
        role_ref = required_roles[index]
        for principal in candidates[role_ref]:
            violates = any(
                frozenset((role_ref, selected_role)) in constraints
                and selected.principal_id == principal.principal_id
                for selected_role, selected in result.items()
            )
            if violates:
                continue
            result[role_ref] = principal
            if search(index + 1):
                return True
            result.pop(role_ref)
        return False

    if not search(0):
        raise CompilationError(
            "SEPARATION_OF_DUTIES_VIOLATION",
            "qualified principals exist but no separation-compliant binding exists",
        )
    return result


def compile_supplier_change(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet
) -> OrganizationPlan:
    """Compile using the fixed Supplier profile and the shared change relation."""
    return compile_change(snapshot, change, profile=SUPPLIER_PROFILE)


def compile_change(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet, *, profile: str
) -> OrganizationPlan:
    """Compile historical typed resources under one package-pinned profile."""
    try:
        derived = derive_change_contract(snapshot, change, profile=profile)
    except ProfileError as exc:
        raise CompilationError(exc.reason_code, str(exc)) from exc
    return _compile_contract(snapshot, change, derived)


def compile_change_from_admitted(
    snapshot_admission: AdmittedSealedResource,
    change_admission: AdmittedSealedResource,
    *,
    profile: str,
) -> OrganizationPlan:
    """Compile exact raw-map roots using the shared candidate-plan strategy."""
    snapshot_admission = validate_sealed_admission(snapshot_admission, "OrganizationSnapshot")
    change_admission = validate_sealed_admission(change_admission, "SemanticChangeSet")
    snapshot, change = snapshot_admission.resource, change_admission.resource
    if not isinstance(snapshot, OrganizationSnapshot) or not isinstance(change, SemanticChangeSet):
        raise CompilationError("CORE_SCHEMA_INVALID", "compilation requires exact admitted resource kinds")
    try:
        derived = derive_change_contract_from_admitted(
            snapshot_admission, change_admission, profile=profile
        )
    except ProfileError as exc:
        raise CompilationError(exc.reason_code, str(exc)) from exc
    return _compile_contract(snapshot, change, derived)


def _source_ref(
    resource: OrganizationSnapshot | SemanticChangeSet, digest: str
) -> ResourceRef:
    """Project the identity already verified by the derivation entrypoint."""
    return ResourceRef(
        kind=resource.kind,
        namespace=resource.metadata.namespace,
        resourceId=resource.metadata.id,
        revision=resource.metadata.revision,
        digest=digest,
    )


def _compile_contract(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet, derived: DerivedContract
) -> OrganizationPlan:

    obligations_by_role: dict[str, list[CoverageObligation]] = defaultdict(list)
    for obligation in derived.obligations:
        obligations_by_role[obligation.required_role_ref].append(obligation)
    required_roles = tuple(sorted(obligations_by_role))
    assignments = _assign_principals(snapshot, required_roles)
    role_definitions = {role.role_id: role for role in snapshot.spec.role_definitions}

    role_instances: list[RoleInstance] = []
    work_units: list[WorkUnit] = []
    work_ref_by_role: dict[str, str] = {}
    for role_ref in required_roles:
        role = role_definitions[role_ref]
        obligations = tuple(
            sorted(obligations_by_role[role_ref], key=lambda item: item.obligation_id)
        )
        obligation_refs = tuple(item.obligation_id for item in obligations)
        role_instance_id = instance_identifier(
            "role-instance",
            snapshot_digest=snapshot.digest or "MISSING",
            change_digest=change.digest or "MISSING",
            body={
                "roleDefinitionRef": role_ref,
                "principalRef": assignments[role_ref].principal_id,
                "obligationRefs": list(obligation_refs),
            },
        )
        work_unit_id = instance_identifier(
            "work-unit",
            snapshot_digest=snapshot.digest or "MISSING",
            change_digest=change.digest or "MISSING",
            body={
                "roleInstanceRefs": [role_instance_id],
                "accountableRoleInstanceRef": role_instance_id,
                "obligationRefs": list(obligation_refs),
            },
        )
        role_instances.append(
            RoleInstance(
                roleInstanceId=role_instance_id,
                roleDefinitionRef=role_ref,
                principalRef=assignments[role_ref].principal_id,
                mission=role.mission,
                obligationRefs=obligation_refs,
                qualificationRefs=role.required_qualifications,
                effectCeiling=EffectCeiling.ZERO_EFFECT,
            )
        )
        evidence = tuple(
            sorted({item for obligation in obligations for item in obligation.required_evidence})
        )
        work_units.append(
            WorkUnit(
                workUnitId=work_unit_id,
                roleInstanceRefs=(role_instance_id,),
                accountableRoleInstanceRef=role_instance_id,
                obligationRefs=obligation_refs,
                evidenceOutputs=evidence,
                effectCeiling=EffectCeiling.ZERO_EFFECT,
            )
        )
        work_ref_by_role[role_ref] = work_unit_id

    happens_before = _compile_orders(derived.required_orders, work_ref_by_role)
    decisions = _compile_decisions(
        snapshot,
        required_roles,
        assignments,
        snapshot_digest=snapshot.digest or "MISSING",
        change_digest=change.digest or "MISSING",
    )
    non_removable = tuple(
        sorted(
            [item.role_instance_id for item in role_instances]
            + [item.work_unit_id for item in work_units]
        )
    )
    plan_spec = OrganizationPlanSpec(
        snapshotRef=_source_ref(snapshot, derived.snapshot_digest),
        changeRef=_source_ref(change, derived.change_digest),
        status="guarded_unresolved" if derived.unresolved_refs else "planned",
        effectCeiling=EffectCeiling.ZERO_EFFECT,
        compilerId=COMPILER_ID if derived.profile_binding is None else "oac.reference.declarative-change",
        profileBinding=derived.profile_binding,
        compilerVersion=COMPILER_VERSION,
        applicabilityEvaluations=derived.applicability_evaluations,
        impactPaths=derived.impact_paths,
        obligations=derived.obligations,
        roleInstances=tuple(role_instances),
        workUnits=tuple(work_units),
        happensBefore=happens_before,
        decisions=decisions,
        unresolvedRefs=derived.unresolved_refs,
        minimality=MinimalityClaim(
            level="inclusion_minimal",
            consideredRoleRefs=tuple(
                sorted(role.role_id for role in snapshot.spec.role_definitions)
            ),
            consideredPrincipalRefs=tuple(
                sorted(principal.principal_id for principal in snapshot.spec.principals)
            ),
            nonRemovableRefs=non_removable,
        ),
    )
    plan_id = (
        f"urn:oac:mvp:plan:{_stable_suffix(snapshot.digest or '')}:"
        f"{_stable_suffix(change.digest or '')}"
    )
    if derived.profile_binding is not None:
        plan_id = f"urn:oac:change-plan:{_stable_suffix(derived.profile_binding.profile_digest)}:{_stable_suffix(snapshot.digest or '')}:{_stable_suffix(change.digest or '')}"
    plan = OrganizationPlan(
        metadata=ResourceMetadata(
            id=plan_id,
            namespace=snapshot.metadata.namespace,
            revision=1,
            ownerRef=snapshot.metadata.owner_ref,
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=change.spec.observed_at,
            effectiveFrom=change.spec.effective_at,
            sourceRefs=(snapshot.metadata.id, change.metadata.id),
        ),
        spec=plan_spec,
    )
    sealed = seal_resource(plan)
    enforce_plan_output(sealed)
    return sealed


def _compile_orders(
    required_orders: tuple[RequiredOrder, ...], work_ref_by_role: dict[str, str]
) -> tuple[OrderConstraint, ...]:
    return tuple(
        OrderConstraint(
            predecessorRef=work_ref_by_role[item.predecessor_role_ref],
            successorRef=work_ref_by_role[item.successor_role_ref],
            reasonRefs=item.reason_refs,
        )
        for item in required_orders
        if item.predecessor_role_ref in work_ref_by_role
        and item.successor_role_ref in work_ref_by_role
    )


def _compile_decisions(
    snapshot: OrganizationSnapshot,
    required_roles: tuple[str, ...],
    assignments: dict[str, Principal],
    *,
    snapshot_digest: str,
    change_digest: str,
) -> tuple[PlanDecision, ...]:
    decisions: list[PlanDecision] = []
    selected_principals = {principal.principal_id for principal in assignments.values()}

    def decision(
        *,
        subject_ref: str,
        input_class: AdmissionStatus,
        disposition: Literal["included", "excluded", "unresolved"],
        reason_codes: tuple[str, ...],
    ) -> PlanDecision:
        decision_id = instance_identifier(
            "plan-decision",
            snapshot_digest=snapshot_digest,
            change_digest=change_digest,
            body={
                "subjectRef": subject_ref,
                "inputClass": input_class.value,
                "disposition": disposition,
                "reasonCodes": list(reason_codes),
            },
        )
        return PlanDecision(
            decisionId=decision_id,
            subjectRef=subject_ref,
            inputClass=input_class.value,
            disposition=disposition,
            reasonCodes=reason_codes,
        )

    for role in sorted(snapshot.spec.role_definitions, key=lambda item: item.role_id):
        included = role.role_id in required_roles
        decisions.append(
            decision(
                subject_ref=role.role_id,
                input_class=role.admission_status,
                disposition="included" if included else "excluded",
                reason_codes=(
                    "ROLE_SELECTED_FOR_OBLIGATION" if included else "NOT_REQUIRED_BY_CONTRACT",
                ),
            )
        )
    for principal in sorted(snapshot.spec.principals, key=lambda item: item.principal_id):
        selected = principal.principal_id in selected_principals
        decisions.append(
            decision(
                subject_ref=principal.principal_id,
                input_class=principal.admission_status,
                disposition="included" if selected else "excluded",
                reason_codes=(
                    "PRINCIPAL_SELECTED_QUALIFIED" if selected else "PRINCIPAL_NOT_SELECTED",
                ),
            )
        )
    for edge in sorted(snapshot.spec.dependency_edges, key=lambda item: item.edge_id):
        if edge.admission_status is AdmissionStatus.ADMITTED:
            continue
        retracted = edge.admission_status is AdmissionStatus.RETRACTED
        decisions.append(
            decision(
                subject_ref=edge.edge_id,
                input_class=edge.admission_status,
                disposition="excluded" if retracted else "unresolved",
                reason_codes=(
                    "RETRACTED_SOURCE_EXCLUDED"
                    if retracted
                    else "CANDIDATE_EDGE_NOT_AUTHORITY",
                ),
            )
        )
    for rule in sorted(snapshot.spec.impact_rules, key=lambda item: item.rule_id):
        if rule.admission_status is AdmissionStatus.ADMITTED:
            continue
        retracted = rule.admission_status is AdmissionStatus.RETRACTED
        decisions.append(
            decision(
                subject_ref=rule.rule_id,
                input_class=rule.admission_status,
                disposition="excluded" if retracted else "unresolved",
                reason_codes=(
                    "RETRACTED_SOURCE_EXCLUDED"
                    if retracted
                    else "CANDIDATE_INPUT_NOT_AUTHORITY",
                ),
            )
        )
    for duty in sorted(
        snapshot.spec.unknown_transition_duties, key=lambda item: item.duty_id
    ):
        if duty.admission_status is AdmissionStatus.ADMITTED:
            continue
        retracted = duty.admission_status is AdmissionStatus.RETRACTED
        decisions.append(
            decision(
                subject_ref=duty.duty_id,
                input_class=duty.admission_status,
                disposition="excluded" if retracted else "unresolved",
                reason_codes=(
                    "RETRACTED_SOURCE_EXCLUDED"
                    if retracted
                    else "CANDIDATE_INPUT_NOT_AUTHORITY",
                ),
            )
        )
    return tuple(sorted(decisions, key=lambda item: item.decision_id))
