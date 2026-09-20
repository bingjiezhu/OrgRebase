"""Deterministic G1a lowering into a local, zero-effect runtime bundle.

This module does not invoke a runtime.  It turns one independently reverified,
unrestricted ACCEPT plan into a content-bound serial projection that still
requires a separate runtime admission decision.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

import rfc8785
from pydantic import ValidationError

from .canonical import OACValidationError, seal_resource, verify_resource_digest
from .json_types import JsonValue
from .models import (
    DimensionVerdict,
    EffectCeiling,
    LoweringStatus,
    OrganizationPlan,
    OrganizationSnapshot,
    PlanCertificate,
    ResourceBase,
    ResourceMetadata,
    ResourceRef,
    RuntimeBinding,
    RuntimeLoweringReceipt,
    RuntimeLoweringReceiptSpec,
    RuntimeStepHandlerBinding,
    RuntimeStepRoleBinding,
    SemanticChangeSet,
    Verdict,
    ZeroEffectRuntimeBundle,
    ZeroEffectRuntimeBundleSpec,
    ZeroEffectRuntimeStep,
)
from .sealed import AdmittedSealedResource, validate_sealed_admission
from .verifier import verify_plan, verify_plan_from_admitted

LOWERING_PROFILE: Final = "oac.runtime-lowering/zero-effect/v0.1"
OBLIGATION_CONTRACT_PROJECTION = "oac.obligation-contract/topology-free/v0.1"
_ZERO_DIGEST = "sha256:" + ("0" * 64)


@dataclass(frozen=True, slots=True)
class LoweringResult:
    """One auditable lowering decision and its optional produced bundle."""

    receipt: RuntimeLoweringReceipt
    bundle: ZeroEffectRuntimeBundle | None

    def as_json(self) -> dict[str, object]:
        return {
            "receipt": self.receipt.model_dump(mode="json", by_alias=True),
            **(
                {"bundle": self.bundle.model_dump(mode="json", by_alias=True)}
                if self.bundle is not None
                else {}
            ),
        }


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8")


def _sorted_ids(values: Collection[str]) -> tuple[str, ...]:
    return tuple(sorted(values, key=_utf8_key))


def _jcs_digest(value: JsonValue) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


def _declared_ref(resource: ResourceBase) -> ResourceRef:
    if resource.digest is None:
        raise OACValidationError("DIGEST_MISSING", f"{resource.kind} has no detached digest")
    return ResourceRef(
        kind=resource.kind,
        namespace=resource.metadata.namespace,
        resourceId=resource.metadata.id,
        revision=resource.metadata.revision,
        digest=resource.digest,
    )


def _admit_input_digests(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    supplied_certificate: PlanCertificate,
    runtime_binding: RuntimeBinding,
) -> None:
    """Admit every sealed lowering input before any domain decision.

    A claimed digest is an identity coordinate, not a trusted alias for the
    supplied object bytes.  Do not sign a receipt until all five resources
    have proved that coordinate.  Error detail deliberately exposes only the
    registered Kind, never a payload or mismatching value.
    """

    for resource in (
        snapshot,
        change,
        plan,
        supplied_certificate,
        runtime_binding,
    ):
        try:
            verify_resource_digest(resource)
        except OACValidationError as exc:
            if isinstance(resource, RuntimeBinding):
                reason_code = "LOWERING_BINDING_DIGEST_INVALID"
            else:
                reason_code = "LOWERING_INPUT_DIGEST_INVALID"
            raise OACValidationError(
                reason_code,
                f"{resource.kind} detached digest is missing or does not match its canonical projection",
            ) from exc


def _certificate_matches(supplied: PlanCertificate, recomputed: PlanCertificate) -> bool:
    return supplied.model_dump(mode="json", by_alias=True) == recomputed.model_dump(
        mode="json", by_alias=True
    )


def _eligibility_reasons(
    supplied: PlanCertificate,
    recomputed: PlanCertificate,
) -> set[str]:
    reasons: set[str] = set()
    if not _certificate_matches(supplied, recomputed):
        reasons.add("LOWERING_CERTIFICATE_MISMATCH")
    if recomputed.spec.verdict is not Verdict.ACCEPT:
        reasons.add("LOWERING_VERDICT_NOT_ACCEPT")
    if recomputed.spec.restrictions:
        reasons.add("LOWERING_RESTRICTIONS_PRESENT")
    if recomputed.spec.unresolved_refs:
        reasons.add("LOWERING_UNRESOLVED_REFS_PRESENT")
    if any(
        dimension.verdict is not DimensionVerdict.PASS for dimension in recomputed.spec.dimensions
    ):
        reasons.add("LOWERING_DIMENSION_NOT_PASS")
    return reasons


def _binding_reasons(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
    certificate: PlanCertificate,
    binding: RuntimeBinding,
    admitted_binding_digests: Collection[str],
) -> set[str]:
    reasons: set[str] = set()
    if binding.digest not in admitted_binding_digests:
        reasons.add("LOWERING_BINDING_NOT_ADMITTED")

    if (
        binding.spec.subject_plan_ref != _declared_ref(plan)
        or binding.spec.subject_certificate_ref != _declared_ref(certificate)
        or binding.metadata.namespace != snapshot.metadata.namespace
        or binding.metadata.owner_ref != snapshot.metadata.owner_ref
        or binding.metadata.governance_ref != snapshot.metadata.governance_ref
        or binding.metadata.source_refs != (plan.metadata.id, certificate.metadata.id)
    ):
        reasons.add("LOWERING_BINDING_ROOT_MISMATCH")

    plan_roles = {role.role_instance_id: role for role in plan.spec.role_instances}
    declared_role_refs = [item.role_instance_ref for item in binding.spec.role_bindings]
    role_bindings = {item.role_instance_ref: item for item in binding.spec.role_bindings}
    if (
        len(declared_role_refs) != len(set(declared_role_refs))
        or set(role_bindings) != set(plan_roles)
        or any(
            len(item.capability_refs) != len(set(item.capability_refs))
            for item in binding.spec.role_bindings
        )
    ):
        reasons.add("LOWERING_BINDING_INCOMPLETE")

    for role_ref in set(role_bindings).intersection(plan_roles):
        if role_bindings[role_ref].principal_ref != plan_roles[role_ref].principal_ref:
            reasons.add("LOWERING_PRINCIPAL_MISMATCH")

    principals_by_subject: dict[str, set[str]] = defaultdict(set)
    for item in binding.spec.role_bindings:
        selected_role = plan_roles.get(item.role_instance_ref)
        selected_principal = (
            selected_role.principal_ref if selected_role is not None else item.principal_ref
        )
        principals_by_subject[item.runtime_subject].add(selected_principal)
    if any(len(principals) > 1 for principals in principals_by_subject.values()):
        reasons.add("LOWERING_RUNTIME_SUBJECT_COLLISION")

    obligations = {obligation.obligation_id: obligation for obligation in plan.spec.obligations}
    expected_types = {item.obligation_type for item in obligations.values()}
    declared_types = [item.obligation_type for item in binding.spec.handler_bindings]
    handlers = {item.obligation_type: item for item in binding.spec.handler_bindings}
    if len(declared_types) != len(set(declared_types)) or set(handlers) != expected_types:
        reasons.add("LOWERING_HANDLER_INCOMPLETE")

    expected_evidence_by_type: dict[str, set[str]] = defaultdict(set)
    for obligation in obligations.values():
        expected_evidence_by_type[obligation.obligation_type].update(obligation.required_evidence)

    for obligation_type in set(handlers).intersection(expected_types):
        handler = handlers[obligation_type]
        if handler.evidence_output_refs != _sorted_ids(expected_evidence_by_type[obligation_type]):
            reasons.add("LOWERING_HANDLER_INCOMPLETE")

    for role_ref in set(role_bindings).intersection(plan_roles):
        role = plan_roles[role_ref]
        required_capabilities = {
            handlers[obligations[obligation_ref].obligation_type].capability_ref
            for obligation_ref in role.obligation_refs
            if obligation_ref in obligations
            and obligations[obligation_ref].obligation_type in handlers
        }
        declared_capabilities = role_bindings[role_ref].capability_refs
        if not required_capabilities.issubset(declared_capabilities):
            reasons.add("LOWERING_CAPABILITY_MISSING")
        if declared_capabilities != _sorted_ids(required_capabilities):
            reasons.add("LOWERING_CAPABILITY_SET_MISMATCH")

    if (
        plan.spec.effect_ceiling is not EffectCeiling.ZERO_EFFECT
        or any(
            role.effect_ceiling is not EffectCeiling.ZERO_EFFECT
            for role in plan.spec.role_instances
        )
        or any(
            work.effect_ceiling is not EffectCeiling.ZERO_EFFECT for work in plan.spec.work_units
        )
        or binding.spec.effect_ceiling is not EffectCeiling.ZERO_EFFECT
        or binding.spec.target_writes != 0
        or any(
            handler.effect_ceiling is not EffectCeiling.ZERO_EFFECT
            for handler in binding.spec.handler_bindings
        )
    ):
        reasons.add("LOWERING_EFFECT_EXPANSION")
    return reasons


def _plan_projection_reasons(plan: OrganizationPlan) -> set[str]:
    """Reject admitted Plans whose set projection is not closed for G1a.

    Plan verification owns organizational validity.  Lowering additionally
    requires an exact, duplicate-free projection into serial runtime steps.
    This preflight keeps such failures auditable instead of leaking a model
    validation exception from bundle construction.
    """

    roles = {item.role_instance_id: item for item in plan.spec.role_instances}
    obligations = {item.obligation_id: item for item in plan.spec.obligations}
    if not plan.spec.work_units:
        return {"LOWERING_PLAN_PROJECTION_UNSUPPORTED"}

    projection_invalid = any(
        len(obligation.required_evidence) != len(set(obligation.required_evidence))
        for obligation in obligations.values()
    )
    projection_invalid = projection_invalid or any(
        len(role.obligation_refs) != len(set(role.obligation_refs))
        or not set(role.obligation_refs).issubset(obligations)
        for role in roles.values()
    )
    for work in plan.spec.work_units:
        role_refs = work.role_instance_refs
        obligation_refs = work.obligation_refs
        evidence_refs = work.evidence_outputs
        expected_evidence = {
            evidence
            for obligation_ref in obligation_refs
            if obligation_ref in obligations
            for evidence in obligations[obligation_ref].required_evidence
        }
        if (
            len(role_refs) != len(set(role_refs))
            or not set(role_refs).issubset(roles)
            or work.accountable_role_instance_ref not in role_refs
            or len(obligation_refs) != len(set(obligation_refs))
            or not set(obligation_refs).issubset(obligations)
            or len(evidence_refs) != len(set(evidence_refs))
            or set(evidence_refs) != expected_evidence
        ):
            projection_invalid = True
    return {"LOWERING_PLAN_PROJECTION_UNSUPPORTED"} if projection_invalid else set()


def _canonical_work_order(plan: OrganizationPlan) -> tuple[str, ...] | None:
    work_refs = {work.work_unit_id for work in plan.spec.work_units}
    successors: dict[str, set[str]] = {ref: set() for ref in work_refs}
    indegree = {ref: 0 for ref in work_refs}
    for edge in plan.spec.happens_before:
        if edge.predecessor_ref not in work_refs or edge.successor_ref not in work_refs:
            return None
        if edge.successor_ref not in successors[edge.predecessor_ref]:
            successors[edge.predecessor_ref].add(edge.successor_ref)
            indegree[edge.successor_ref] += 1

    ready = sorted((ref for ref, degree in indegree.items() if degree == 0), key=_utf8_key)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for successor in sorted(successors[current], key=_utf8_key):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort(key=_utf8_key)
    if len(ordered) != len(work_refs):
        return None
    return tuple(ordered)


def _obligation_contract_digest(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
) -> str:
    obligations = sorted(
        (item.model_dump(mode="json", by_alias=True) for item in plan.spec.obligations),
        key=lambda item: _utf8_key(str(item["obligationId"])),
    )
    return _jcs_digest(
        {
            "projection": OBLIGATION_CONTRACT_PROJECTION,
            "snapshotDigest": snapshot.digest,
            "changeDigest": change.digest,
            "obligations": obligations,
        }
    )


def _topology_digest(plan: OrganizationPlan) -> str:
    role_instances = sorted(
        (
            {
                "roleInstanceId": item.role_instance_id,
                "roleDefinitionRef": item.role_definition_ref,
                "principalRef": item.principal_ref,
                "mission": item.mission,
                "obligationRefs": _sorted_ids(set(item.obligation_refs)),
                "qualificationRefs": _sorted_ids(set(item.qualification_refs)),
                "effectCeiling": item.effect_ceiling.value,
            }
            for item in plan.spec.role_instances
        ),
        key=lambda item: _utf8_key(str(item["roleInstanceId"])),
    )
    work_units = sorted(
        (
            {
                "workUnitId": item.work_unit_id,
                "roleInstanceRefs": _sorted_ids(set(item.role_instance_refs)),
                "accountableRoleInstanceRef": item.accountable_role_instance_ref,
                "obligationRefs": _sorted_ids(set(item.obligation_refs)),
                "evidenceOutputs": _sorted_ids(set(item.evidence_outputs)),
                "effectCeiling": item.effect_ceiling.value,
            }
            for item in plan.spec.work_units
        ),
        key=lambda item: _utf8_key(str(item["workUnitId"])),
    )
    happens_before = sorted(
        (
            {
                "predecessorRef": item.predecessor_ref,
                "successorRef": item.successor_ref,
                "relation": item.relation,
                "reasonRefs": _sorted_ids(set(item.reason_refs)),
            }
            for item in plan.spec.happens_before
        ),
        key=lambda item: (
            _utf8_key(str(item["predecessorRef"])),
            _utf8_key(str(item["successorRef"])),
        ),
    )
    return _jcs_digest(
        {
            "roleInstances": role_instances,
            "workUnits": work_units,
            "happensBefore": happens_before,
        }
    )


def _build_bundle(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    certificate: PlanCertificate,
    binding: RuntimeBinding,
    ordered_work_refs: tuple[str, ...],
) -> ZeroEffectRuntimeBundle:
    work_by_ref = {item.work_unit_id: item for item in plan.spec.work_units}
    role_by_ref = {item.role_instance_id: item for item in plan.spec.role_instances}
    obligation_by_ref = {item.obligation_id: item for item in plan.spec.obligations}
    runtime_role_by_ref = {item.role_instance_ref: item for item in binding.spec.role_bindings}
    handler_by_type = {item.obligation_type: item for item in binding.spec.handler_bindings}
    predecessors: dict[str, set[str]] = defaultdict(set)
    for edge in plan.spec.happens_before:
        predecessors[edge.successor_ref].add(edge.predecessor_ref)

    steps: list[ZeroEffectRuntimeStep] = []
    for index, work_ref in enumerate(ordered_work_refs):
        work = work_by_ref[work_ref]
        role_bindings = tuple(
            RuntimeStepRoleBinding(
                roleInstanceRef=role_ref,
                principalRef=role_by_ref[role_ref].principal_ref,
                runtimeSubject=runtime_role_by_ref[role_ref].runtime_subject,
            )
            for role_ref in sorted(work.role_instance_refs, key=_utf8_key)
        )
        accountable_role = runtime_role_by_ref[work.accountable_role_instance_ref]
        accountable = RuntimeStepRoleBinding(
            roleInstanceRef=work.accountable_role_instance_ref,
            principalRef=role_by_ref[work.accountable_role_instance_ref].principal_ref,
            runtimeSubject=accountable_role.runtime_subject,
        )
        handler_bindings = []
        for obligation_ref in sorted(work.obligation_refs, key=_utf8_key):
            obligation = obligation_by_ref[obligation_ref]
            handler = handler_by_type[obligation.obligation_type]
            handler_bindings.append(
                RuntimeStepHandlerBinding(
                    obligationRef=obligation_ref,
                    obligationType=obligation.obligation_type,
                    handlerRef=handler.handler_ref,
                    handlerDigest=handler.handler_digest,
                    capabilityRef=handler.capability_ref,
                    evidenceOutputRefs=_sorted_ids(obligation.required_evidence),
                    effectCeiling=EffectCeiling.ZERO_EFFECT,
                )
            )
        steps.append(
            ZeroEffectRuntimeStep(
                stepIndex=index,
                workUnitRef=work_ref,
                roleBindings=role_bindings,
                accountable=accountable,
                obligationRefs=_sorted_ids(work.obligation_refs),
                handlerBindings=tuple(handler_bindings),
                evidenceOutputRefs=_sorted_ids(work.evidence_outputs),
                predecessorRefs=_sorted_ids(predecessors[work_ref]),
                effectCeiling=EffectCeiling.ZERO_EFFECT,
                targetWrites=0,
            )
        )

    bundle_identity = _jcs_digest(
        {
            "planDigest": plan.digest,
            "certificateDigest": certificate.digest,
            "runtimeBindingDigest": binding.digest,
            "loweringProfile": LOWERING_PROFILE,
        }
    ).removeprefix("sha256:")
    bundle = ZeroEffectRuntimeBundle(
        metadata=ResourceMetadata(
            id=f"urn:oac:runtime-bundle:{bundle_identity}",
            namespace=snapshot.metadata.namespace,
            revision=1,
            ownerRef=snapshot.metadata.owner_ref,
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=change.spec.observed_at,
            effectiveFrom=change.spec.effective_at,
            sourceRefs=(
                snapshot.metadata.id,
                change.metadata.id,
                plan.metadata.id,
                certificate.metadata.id,
                binding.metadata.id,
            ),
        ),
        spec=ZeroEffectRuntimeBundleSpec(
            snapshotRef=_declared_ref(snapshot),
            changeRef=_declared_ref(change),
            subjectPlanRef=_declared_ref(plan),
            certificateRef=_declared_ref(certificate),
            runtimeBindingRef=_declared_ref(binding),
            loweringProfile=LOWERING_PROFILE,
            obligationContractDigest=_obligation_contract_digest(snapshot, change, plan),
            topologyDigest=_topology_digest(plan),
            steps=tuple(steps),
            effectCeiling=EffectCeiling.ZERO_EFFECT,
            targetWrites=0,
            requiresRuntimeAdmission=True,
        ),
    )
    return seal_resource(bundle)


def _build_receipt(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    supplied_certificate: PlanCertificate,
    recomputed_certificate: PlanCertificate,
    binding: RuntimeBinding,
    reasons: set[str],
    bundle: ZeroEffectRuntimeBundle | None,
) -> RuntimeLoweringReceipt:
    status = LoweringStatus.BLOCKED if reasons else LoweringStatus.PRODUCED
    identity = _jcs_digest(
        {
            "planDigest": plan.digest or _ZERO_DIGEST,
            "suppliedCertificateDigest": supplied_certificate.digest or _ZERO_DIGEST,
            "recomputedCertificateDigest": recomputed_certificate.digest or _ZERO_DIGEST,
            "runtimeBindingDigest": binding.digest or _ZERO_DIGEST,
            "status": status.value,
            "reasonCodes": sorted(reasons),
            "bundleDigest": bundle.digest if bundle is not None else None,
        }
    ).removeprefix("sha256:")
    receipt = RuntimeLoweringReceipt(
        metadata=ResourceMetadata(
            id=f"urn:oac:runtime-lowering-receipt:{identity}",
            namespace=snapshot.metadata.namespace,
            revision=1,
            ownerRef=snapshot.metadata.owner_ref,
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=change.spec.observed_at,
            effectiveFrom=change.spec.effective_at,
            sourceRefs=(
                snapshot.metadata.id,
                change.metadata.id,
                plan.metadata.id,
                supplied_certificate.metadata.id,
                recomputed_certificate.metadata.id,
                binding.metadata.id,
            ),
        ),
        spec=RuntimeLoweringReceiptSpec(
            snapshotRef=_declared_ref(snapshot),
            changeRef=_declared_ref(change),
            subjectPlanRef=_declared_ref(plan),
            suppliedCertificateRef=_declared_ref(supplied_certificate),
            recomputedCertificateRef=_declared_ref(recomputed_certificate),
            runtimeBindingRef=_declared_ref(binding),
            status=status,
            reasonCodes=_sorted_ids(reasons),
            bundleRef=_declared_ref(bundle) if bundle is not None else None,
            proofScope="lowering_only",
            runtimeInvoked=False,
            runtimeAdmissionPerformed=False,
            targetWrites=0,
        ),
    )
    return seal_resource(receipt)


def lower_plan(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    supplied_certificate: PlanCertificate,
    runtime_binding: RuntimeBinding,
    *,
    admitted_binding_digests: Collection[str],
) -> LoweringResult:
    """Reverify and lower one exact Plan without invoking any runtime.

    ``admitted_binding_digests`` is runner-owned ambient authority.  It is not
    read from the binding, Plan, certificate, environment, or bundle.
    """

    _admit_input_digests(
        snapshot,
        change,
        plan,
        supplied_certificate,
        runtime_binding,
    )

    recomputed_certificate = verify_plan(snapshot, change, plan)
    return _lower_plan(snapshot, change, plan, supplied_certificate, runtime_binding,
                       recomputed_certificate, admitted_binding_digests=admitted_binding_digests)


def lower_plan_from_admitted(
    snapshot_admission: AdmittedSealedResource,
    change_admission: AdmittedSealedResource,
    plan_admission: AdmittedSealedResource,
    certificate_admission: AdmittedSealedResource,
    binding_admission: AdmittedSealedResource,
    *,
    admitted_binding_digests: Collection[str],
) -> LoweringResult:
    """Lower exact external sealed inputs through the existing zero-effect relation."""
    validated = []
    for admission, kind in zip(
        (snapshot_admission, change_admission, plan_admission, certificate_admission, binding_admission),
        ("OrganizationSnapshot", "SemanticChangeSet", "OrganizationPlan", "PlanCertificate", "RuntimeBinding"),
        strict=True,
    ):
        try:
            validated.append(validate_sealed_admission(admission, kind))
        except OACValidationError as exc:
            code = "LOWERING_BINDING_DIGEST_INVALID" if kind == "RuntimeBinding" else "LOWERING_INPUT_DIGEST_INVALID"
            raise OACValidationError(code, f"{kind} detached digest is missing or does not match its canonical projection") from exc
    snapshot, change, plan, certificate, binding = (item.resource for item in validated)
    if (not isinstance(snapshot, OrganizationSnapshot) or not isinstance(change, SemanticChangeSet)
            or not isinstance(plan, OrganizationPlan) or not isinstance(certificate, PlanCertificate)
            or not isinstance(binding, RuntimeBinding)):
        raise OACValidationError("CORE_SCHEMA_INVALID", "lowering requires the five exact registered resource kinds")
    recomputed = verify_plan_from_admitted(*validated[:3])
    return _lower_plan(snapshot, change, plan, certificate, binding, recomputed,
                       admitted_binding_digests=admitted_binding_digests)


def _lower_plan(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    supplied_certificate: PlanCertificate,
    runtime_binding: RuntimeBinding,
    recomputed_certificate: PlanCertificate,
    *,
    admitted_binding_digests: Collection[str],
) -> LoweringResult:
    reasons = _eligibility_reasons(supplied_certificate, recomputed_certificate)
    reasons.update(_plan_projection_reasons(plan))
    reasons.update(
        _binding_reasons(
            snapshot,
            plan,
            recomputed_certificate,
            runtime_binding,
            frozenset(admitted_binding_digests),
        )
    )

    ordered_work_refs = _canonical_work_order(plan)
    if ordered_work_refs is None:
        reasons.add("LOWERING_ORDER_CYCLE")

    bundle = None
    if not reasons and ordered_work_refs is not None:
        try:
            bundle = _build_bundle(
                snapshot,
                change,
                plan,
                recomputed_certificate,
                runtime_binding,
                ordered_work_refs,
            )
        except (KeyError, ValidationError):
            reasons.add("LOWERING_PLAN_PROJECTION_UNSUPPORTED")
    receipt = _build_receipt(
        snapshot,
        change,
        plan,
        supplied_certificate,
        recomputed_certificate,
        runtime_binding,
        reasons,
        bundle,
    )
    return LoweringResult(receipt=receipt, bundle=bundle)


__all__ = [
    "LOWERING_PROFILE",
    "OBLIGATION_CONTRACT_PROJECTION",
    "LoweringResult",
    "lower_plan",
    "lower_plan_from_admitted",
]
