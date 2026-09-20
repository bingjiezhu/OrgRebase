"""Compiler-independent OrganizationPlan verifier with a shared Profile oracle.

This module deliberately does not import :mod:`oac.compiler`, trust compiler
decisions, or require producer hidden state.  It consumes only registered models,
exact source roots, the SupplierChange profile rules, and the sealed plan.  It
does share :func:`oac.supplier.derive_supplier_contract` with the reference
compiler; Spec 003, not this verifier, owns clean-room semantic independence.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import rfc8785

from .canonical import OACValidationError, seal_resource, verify_resource_digest
from .change_profiles import SUPPLIER_PROFILE, get_change_profile
from .models import (
    MAX_DIMENSION_WITNESSES,
    AdmissionStatus,
    ApplicabilityResult,
    ChangeProfileBinding,
    CoverageObligation,
    DimensionVerdict,
    EffectCeiling,
    ImpactPath,
    ImpactRule,
    ImpactState,
    OrganizationPlan,
    OrganizationSnapshot,
    PlanCertificate,
    PlanCertificateSpec,
    ResourceBase,
    ResourceMetadata,
    ResourceRef,
    SemanticChangeSet,
    TransferPredicate,
    Verdict,
    VerificationDimension,
)
from .registry import REASON_CODE_REGISTRY
from .sealed import AdmittedSealedResource, validate_sealed_admission
from .supplier import (
    DerivedContract,
    ProfileError,
    RequiredOrder,
    derive_change_contract,
    derive_change_contract_from_admitted,
    validate_supplier_input_coherence,
)

CERTIFIER_ID = "oac.reference.plan-verifier"
CERTIFIER_BUILD = "0.3.0a0"
_ZERO_DIGEST = "sha256:" + ("0" * 64)


@dataclass(frozen=True, slots=True)
class _Check:
    name: str
    verdict: DimensionVerdict
    reason_codes: tuple[str, ...] = ()
    witnesses: tuple[str, ...] = ()
    witnesses_omitted: int = 0

    def dimension(self) -> VerificationDimension:
        return VerificationDimension(
            name=self.name,
            verdict=self.verdict,
            reasonCodes=self.reason_codes,
            witnesses=self.witnesses,
            witnessesOmitted=self.witnesses_omitted,
        )


def _bounded_witnesses(witnesses: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
    ordered = tuple(sorted(set(witnesses)))
    return (
        ordered[:MAX_DIMENSION_WITNESSES],
        max(0, len(ordered) - MAX_DIMENSION_WITNESSES),
    )


def _failed(name: str, code: str, *witnesses: str) -> _Check:
    bounded, omitted = _bounded_witnesses(witnesses)
    return _Check(name, DimensionVerdict.FAIL, (code,), bounded, omitted)


def _passed(name: str) -> _Check:
    return _Check(name, DimensionVerdict.PASS)


def _unknown(name: str, code: str, *witnesses: str) -> _Check:
    bounded, omitted = _bounded_witnesses(witnesses)
    return _Check(name, DimensionVerdict.UNKNOWN, (code,), bounded, omitted)


def _failed_with_codes(
    name: str, codes: set[str], witnesses: tuple[str, ...]
) -> _Check:
    bounded, omitted = _bounded_witnesses(witnesses)
    return _Check(
        name,
        DimensionVerdict.FAIL,
        tuple(sorted(codes)),
        bounded,
        omitted,
    )


def _stored_ref(resource: ResourceBase) -> ResourceRef:
    return ResourceRef(
        kind=resource.kind,
        namespace=resource.metadata.namespace,
        resourceId=resource.metadata.id,
        revision=resource.metadata.revision,
        digest=resource.digest or _ZERO_DIGEST,
    )


def _integrity_checks(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    verify_identity: Callable[[ResourceBase], None],
    profile_binding: ChangeProfileBinding | None = None,
) -> tuple[list[_Check], bool, str | None]:
    checks: list[_Check] = []
    roots_valid = True
    block_codes: list[str] = []
    failures: list[tuple[str, str]] = []
    for resource in (snapshot, change, plan):
        try:
            verify_identity(resource)
        except OACValidationError as exc:
            failures.append((exc.reason_code, resource.metadata.id))
            roots_valid = False
            block_codes.append(exc.reason_code)
    if failures:
        codes = tuple(sorted({code for code, _ in failures}))
        witnesses, omitted = _bounded_witnesses(
            tuple(resource_id for _, resource_id in failures)
        )
        checks.append(
            _Check(
                "integrity",
                DimensionVerdict.FAIL,
                codes,
                witnesses,
                omitted,
            )
        )
    else:
        checks.append(_passed("integrity"))

    if plan.spec.profile_binding != profile_binding:
        checks.append(
            _failed("profile_binding", "CHANGE_PROFILE_BINDING_MISMATCH", plan.metadata.id)
        )
        roots_valid = False
        block_codes.append("CHANGE_PROFILE_BINDING_MISMATCH")
    elif profile_binding is not None:
        checks.append(_passed("profile_binding"))

    expected_snapshot_ref = _stored_ref(snapshot)
    expected_change_ref = _stored_ref(change)
    if plan.spec.snapshot_ref != expected_snapshot_ref or plan.spec.change_ref != expected_change_ref:
        checks.append(
            _failed(
                "input_roots",
                "INPUT_ROOT_MISMATCH",
                plan.spec.snapshot_ref.resource_id,
                plan.spec.change_ref.resource_id,
            )
        )
        roots_valid = False
        block_codes.append("INPUT_ROOT_MISMATCH")
    else:
        checks.append(_passed("input_roots"))

    coherence_witnesses: list[str] = []
    try:
        validate_supplier_input_coherence(snapshot, change)
    except ProfileError:
        coherence_witnesses.append(change.metadata.id)

    expected_plan_sources = (snapshot.metadata.id, change.metadata.id)
    if plan.metadata.namespace != snapshot.metadata.namespace:
        coherence_witnesses.append("plan.metadata.namespace")
    if (
        plan.metadata.governance_ref != snapshot.metadata.governance_ref
        or change.metadata.governance_ref != snapshot.metadata.governance_ref
    ):
        coherence_witnesses.append("plan.metadata.governanceRef")
    if plan.metadata.owner_ref != snapshot.metadata.owner_ref:
        coherence_witnesses.append("plan.metadata.ownerRef")
    if plan.metadata.source_refs != expected_plan_sources:
        coherence_witnesses.append("plan.metadata.sourceRefs")
    if coherence_witnesses:
        checks.append(
            _failed(
                "resource_coherence",
                "RESOURCE_COHERENCE_VIOLATION",
                *coherence_witnesses,
            )
        )
        roots_valid = False
        block_codes.append("RESOURCE_COHERENCE_VIOLATION")
    else:
        checks.append(_passed("resource_coherence"))
    return checks, roots_valid, min(block_codes) if block_codes else None


def verify_plan(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
) -> PlanCertificate:
    """Issue a deterministic plan-stage certificate over exact frozen roots."""

    return verify_change(snapshot, change, plan, profile=SUPPLIER_PROFILE)


def verify_change(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    *,
    profile: str,
) -> PlanCertificate:
    """Verify the same plan relation under an explicit package-pinned profile."""
    selected = get_change_profile(profile)

    def derive(snapshot: OrganizationSnapshot, change: SemanticChangeSet) -> DerivedContract:
        return derive_change_contract(snapshot, change, profile=profile)

    return _verify_plan(
        snapshot, change, plan, verify_resource_digest, derive, profile_binding=selected.binding
    )


def verify_plan_from_admitted(
    snapshot_admission: AdmittedSealedResource,
    change_admission: AdmittedSealedResource,
    plan_admission: AdmittedSealedResource,
) -> PlanCertificate:
    return verify_change_from_admitted(
        snapshot_admission, change_admission, plan_admission, profile=SUPPLIER_PROFILE
    )


def verify_change_from_admitted(
    snapshot_admission: AdmittedSealedResource,
    change_admission: AdmittedSealedResource,
    plan_admission: AdmittedSealedResource,
    *,
    profile: str,
) -> PlanCertificate:
    """Verify raw-map roots using the same plan relation as typed builders."""
    selected = get_change_profile(profile)
    admissions = {
        "OrganizationSnapshot": validate_sealed_admission(
            snapshot_admission, "OrganizationSnapshot"
        ),
        "SemanticChangeSet": validate_sealed_admission(change_admission, "SemanticChangeSet"),
        "OrganizationPlan": validate_sealed_admission(plan_admission, "OrganizationPlan"),
    }
    snapshot = admissions["OrganizationSnapshot"].resource
    change = admissions["SemanticChangeSet"].resource
    plan = admissions["OrganizationPlan"].resource
    if (
        not isinstance(snapshot, OrganizationSnapshot)
        or not isinstance(change, SemanticChangeSet)
        or not isinstance(plan, OrganizationPlan)
    ):
        raise OACValidationError(
            "CORE_SCHEMA_INVALID", "plan verification requires exact admitted resource kinds"
        )

    def verify_identity(resource: ResourceBase) -> None:
        admission = admissions.get(resource.kind)
        if (
            admission is None
            or admission.resource is not resource
            or resource.digest != admission.resource_digest
        ):
            raise OACValidationError(
                "CANONICAL_ADMISSION_MISMATCH", "resource is outside the admitted plan inputs"
            )

    def derive(
        admitted_snapshot: OrganizationSnapshot, admitted_change: SemanticChangeSet
    ) -> DerivedContract:
        if admitted_snapshot is not snapshot or admitted_change is not change:
            raise OACValidationError(
                "CANONICAL_ADMISSION_MISMATCH", "derivation roots are outside admitted inputs"
            )
        return derive_change_contract_from_admitted(
            admissions["OrganizationSnapshot"], admissions["SemanticChangeSet"], profile=profile
        )

    return _verify_plan(
        snapshot, change, plan, verify_identity, derive, profile_binding=selected.binding
    )


def _verify_plan(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    verify_identity: Callable[[ResourceBase], None],
    derive: Callable[[OrganizationSnapshot, SemanticChangeSet], DerivedContract],
    *,
    profile_binding: ChangeProfileBinding | None = None,
) -> PlanCertificate:
    checks, roots_valid, block_code = _integrity_checks(
        snapshot, change, plan, verify_identity, profile_binding
    )
    derived: DerivedContract | None = None
    if roots_valid:
        try:
            derived = derive(snapshot, change)
        except ProfileError as exc:
            check = (
                _failed("derivation", exc.reason_code, change.metadata.id)
                if exc.reason_code
                in {
                    "CHANGE_NOT_ADMITTED",
                    "CHANGE_OPERATION_INCONSISTENT",
                    "RESOURCE_COHERENCE_VIOLATION",
                    "RETRACTED_SOURCE_EXCLUDED",
                    "SUPPLIER_STATUS_DELTA_SET_INVALID",
                    "CHANGE_PROFILE_DELTA_SET_INVALID",
                    "UNSUPPORTED_SEMANTICS",
                    "APPLICABILITY_SOURCE_MISSING",
                    "APPLICABILITY_SOURCE_MISMATCH",
                    "PREDICATE_DEFINITION_INVALID",
                    "PREDICATE_VERSION_UNSUPPORTED",
                    "PREREQUISITE_OBLIGATION_ORDER_MISSING",
                    "SYNTHETIC_UNKNOWN_VALUE",
                    "OBLIGATION_UNSATISFIED",
                    "UNKNOWN_TRANSITION_DUTY_INVALID",
                }
                else _unknown("derivation", exc.reason_code, change.metadata.id)
            )
            checks.append(check)
    else:
        checks.append(
            _unknown(
                "derivation",
                block_code or "ROOT_DIGEST_MISMATCH",
                plan.metadata.id,
            )
        )

    if derived is not None:
        checks.extend(_verify_derived_contract(snapshot, plan, derived))
    else:
        for name in (
            "applicability_evaluations",
            "impact_paths",
            "unknown_transition_duties",
            "unknown_transition_duty_obligations",
            "obligations",
            "unknown_preservation",
            "root_applicability",
            "plan_status",
            "coverage",
            "responsibility_binding",
            "qualification",
            "separation_of_duties",
            "ordering",
            "order_constraints",
            "evidence",
            "effect_ceiling",
            "minimality",
            "consideration_ledger",
        ):
            checks.append(_unknown(name, "UNSUPPORTED_SEMANTICS", plan.metadata.id))

    failures = [check for check in checks if check.verdict is DimensionVerdict.FAIL]
    unknowns = [check for check in checks if check.verdict is DimensionVerdict.UNKNOWN]
    if failures:
        verdict = Verdict.REJECT
    elif unknowns:
        verdict = Verdict.UNKNOWN
    elif derived is not None and derived.unresolved_refs:
        verdict = Verdict.PROVISIONAL
    else:
        verdict = Verdict.ACCEPT

    reason_codes = {code for check in checks for code in check.reason_codes}
    if verdict is Verdict.PROVISIONAL and derived is not None:
        for evaluation in derived.applicability_evaluations:
            if evaluation.evaluation_id in derived.unresolved_refs:
                reason_codes.update(evaluation.reason_codes)
        for path in derived.impact_paths:
            if path.path_id in derived.unresolved_refs:
                reason_codes.update(path.reason_codes)
    restrictions = (
        ("zero_effect", "discovery_and_evidence_only")
        if verdict is Verdict.PROVISIONAL
        else (() if verdict is Verdict.ACCEPT else ("activation_prohibited",))
    )
    certificate_spec = PlanCertificateSpec(
        profileBinding=profile_binding,
        subjectPlanRef=_stored_ref(plan),
        snapshotRef=_stored_ref(snapshot),
        changeRef=_stored_ref(change),
        verdict=verdict,
        dimensions=tuple(check.dimension() for check in checks),
        reasonCodes=tuple(sorted(reason_codes)),
        restrictions=restrictions,
        unresolvedRefs=derived.unresolved_refs if derived is not None else (plan.metadata.id,),
        certifierId=CERTIFIER_ID,
        certifierBuild=CERTIFIER_BUILD,
    )
    certificate = PlanCertificate(
        metadata=ResourceMetadata(
            id=f"urn:oac:mvp:plan-certificate:{plan.metadata.id}",
            namespace=snapshot.metadata.namespace,
            revision=1,
            ownerRef=snapshot.metadata.owner_ref,
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=change.spec.observed_at,
            effectiveFrom=change.spec.effective_at,
            sourceRefs=(plan.metadata.id, snapshot.metadata.id, change.metadata.id),
        ),
        spec=certificate_spec,
    )
    return seal_resource(certificate)


def _verify_derived_contract(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
    derived: DerivedContract,
) -> list[_Check]:
    if _is_legacy_plan(snapshot, plan):
        return _verify_legacy_plan(snapshot, plan, derived)

    checks: list[_Check] = []
    expected_evaluations = {
        item.evaluation_id: item for item in derived.applicability_evaluations
    }
    actual_evaluations = {
        item.evaluation_id: item for item in plan.spec.applicability_evaluations
    }
    if expected_evaluations != actual_evaluations:
        missing = sorted(set(expected_evaluations) - set(actual_evaluations))
        extra = sorted(set(actual_evaluations) - set(expected_evaluations))
        altered = sorted(
            evaluation_id
            for evaluation_id in set(expected_evaluations) & set(actual_evaluations)
            if expected_evaluations[evaluation_id] != actual_evaluations[evaluation_id]
        )
        codes: set[str] = set()
        if missing:
            codes.add("APPLICABILITY_EVALUATION_MISSING")
        if extra or altered:
            codes.add("APPLICABILITY_EVALUATION_MISMATCH")
        if any(
            expected_evaluations[evaluation_id].witness_refs
            != actual_evaluations[evaluation_id].witness_refs
            for evaluation_id in altered
        ):
            codes.add("APPLICABILITY_WITNESS_MISMATCH")
        if extra or any(
            actual_evaluations[evaluation_id].result is ApplicabilityResult.TRUE
            and expected_evaluations[evaluation_id].result
            is not ApplicabilityResult.TRUE
            for evaluation_id in altered
        ):
            codes.add("APPLICABILITY_SCOPE_WIDENED")
        checks.append(
            _failed_with_codes(
                "applicability_evaluations",
                codes,
                tuple(missing + extra + altered),
            )
        )
    else:
        checks.append(_passed("applicability_evaluations"))

    expected_paths = {item.path_id: item for item in derived.impact_paths}
    actual_paths = {item.path_id: item for item in plan.spec.impact_paths}
    if expected_paths != actual_paths:
        omitted = sorted(set(expected_paths) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected_paths))
        altered = sorted(
            path_id
            for path_id in set(expected_paths) & set(actual_paths)
            if expected_paths[path_id] != actual_paths[path_id]
        )
        codes = set()
        if omitted or altered:
            codes.add("IMPACT_PATH_OMITTED")
        if extra or any(
            actual_paths[path_id].state
            in {ImpactState.AFFECTED, ImpactState.UNAFFECTED_PROVEN}
            and expected_paths[path_id].state is ImpactState.UNKNOWN
            for path_id in altered
        ):
            codes.add("APPLICABILITY_SCOPE_WIDENED")
        false_sources = {
            evaluation.source_ref
            for evaluation in derived.applicability_evaluations
            if evaluation.result is ApplicabilityResult.FALSE
        }
        false_evaluation_ids = {
            evaluation.evaluation_id
            for evaluation in derived.applicability_evaluations
            if evaluation.result is ApplicabilityResult.FALSE
        }
        if any(
            false_sources.intersection(
                {*actual_paths[path_id].edge_refs, *actual_paths[path_id].rule_refs}
            )
            or false_evaluation_ids.intersection(
                actual_paths[path_id].evaluation_refs
            )
            for path_id in (*extra, *altered)
        ):
            codes.add("PREDICATE_FALSE_PATH_INCLUDED")
        checks.append(
            _failed_with_codes(
                "impact_paths", codes, tuple(omitted + extra + altered)
            )
        )
    else:
        checks.append(_passed("impact_paths"))

    expected_duty_paths = {
        path.path_id: path for path in derived.impact_paths if path.duty_refs
    }
    actual_duty_paths = {
        path.path_id: path for path in plan.spec.impact_paths if path.duty_refs
    }
    missing_duty_paths = sorted(
        path_id
        for path_id, path in expected_duty_paths.items()
        if actual_duty_paths.get(path_id) != path
    )
    invalid_duty_paths = sorted(
        path_id
        for path_id in set(actual_duty_paths) - set(expected_duty_paths)
    )
    if missing_duty_paths or invalid_duty_paths:
        duty_codes = (
            {"UNKNOWN_TRANSITION_DUTY_MISSING"} if missing_duty_paths else set()
        )
        if invalid_duty_paths:
            duty_codes.add("UNKNOWN_TRANSITION_DUTY_INVALID")
        checks.append(
            _failed_with_codes(
                "unknown_transition_duties",
                duty_codes,
                tuple(missing_duty_paths + invalid_duty_paths),
            )
        )
    else:
        checks.append(_passed("unknown_transition_duties"))

    expected_obligations = {item.obligation_id: item for item in derived.obligations}
    actual_obligations = {item.obligation_id: item for item in plan.spec.obligations}
    if expected_obligations != actual_obligations:
        mismatches = sorted(set(expected_obligations) ^ set(actual_obligations))
        mismatches.extend(
            sorted(
                obligation_id
                for obligation_id in set(expected_obligations) & set(actual_obligations)
                if expected_obligations[obligation_id] != actual_obligations[obligation_id]
            )
        )
        checks.append(_failed("obligations", "OBLIGATION_SET_MISMATCH", *mismatches))
    else:
        checks.append(_passed("obligations"))

    duty_path_ids = {
        path.path_id for path in derived.impact_paths if path.duty_refs
    }
    expected_duty_obligation_ids = {
        obligation.obligation_id
        for obligation in derived.obligations
        if duty_path_ids.intersection(obligation.path_refs)
    }
    missing_duty_obligations = sorted(
        expected_duty_obligation_ids - set(actual_obligations)
    )
    if missing_duty_obligations:
        checks.append(
            _failed(
                "unknown_transition_duty_obligations",
                "UNKNOWN_TRANSITION_DUTY_MISSING",
                *missing_duty_obligations,
            )
        )
    else:
        checks.append(_passed("unknown_transition_duty_obligations"))

    if tuple(sorted(plan.spec.unresolved_refs)) != derived.unresolved_refs:
        checks.append(
            _failed(
                "unknown_preservation",
                "UNKNOWN_NOT_PRESERVED",
                *set(derived.unresolved_refs).symmetric_difference(plan.spec.unresolved_refs),
            )
        )
    else:
        checks.append(_passed("unknown_preservation"))

    if derived.root_applicability_unknown:
        checks.append(
            _unknown(
                "root_applicability",
                "ROOT_APPLICABILITY_UNKNOWN",
                plan.spec.change_ref.resource_id,
            )
        )
    else:
        checks.append(_passed("root_applicability"))

    expected_status = "guarded_unresolved" if derived.unresolved_refs else "planned"
    if plan.spec.status != expected_status:
        checks.append(
            _failed("plan_status", "PLAN_STATUS_MISMATCH", plan.spec.status, expected_status)
        )
    else:
        checks.append(_passed("plan_status"))

    checks.append(_check_coverage(plan, expected_obligations))
    checks.append(_check_responsibility_bindings(plan))
    checks.append(_check_qualification(snapshot, plan, expected_obligations))
    checks.append(_check_separation(snapshot, plan))
    checks.extend(_check_ordering(plan, derived))
    checks.append(_check_evidence(plan, expected_obligations))
    checks.append(_check_effect_ceiling(plan))
    checks.append(_check_minimality(snapshot, plan, expected_obligations))
    checks.append(_check_decisions(snapshot, plan, derived))
    return checks


def _is_legacy_plan(snapshot: OrganizationSnapshot, plan: OrganizationPlan) -> bool:
    completeness = snapshot.spec.completeness
    return (
        plan.spec.compiler_version == "0.1.0a0"
        and not plan.spec.applicability_evaluations
        and not snapshot.spec.unknown_transition_duties
        and completeness.discovery_target_ref is None
        and all(
            isinstance(edge.transfer_predicate, TransferPredicate)
            for edge in snapshot.spec.dependency_edges
        )
        and all(isinstance(rule, ImpactRule) for rule in snapshot.spec.impact_rules)
    )


def _path_semantic_key(path: ImpactPath) -> tuple[object, ...]:
    return (
        path.target_ref,
        path.state,
        path.edge_refs,
        path.rule_refs,
        path.origin,
        path.truncated,
    )


def _legacy_stable_id(prefix: str, payload: Mapping[str, str | bool | list[str] | None]) -> str:
    """Recompute the frozen v0alpha1 hash-derived identifier formula."""

    digest = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()[:24]
    return f"urn:oac:mvp:{prefix}:{digest}"


def _legacy_expected_path_id(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
    path: ImpactPath,
) -> str:
    return _legacy_stable_id(
        "path",
        {
            "snapshot": snapshot.digest,
            "change": plan.spec.change_ref.digest,
            "target": path.target_ref,
            "state": path.state.value,
            "edges": list(path.edge_refs),
            "rules": list(path.rule_refs),
            "origin": path.origin,
            "truncated": path.truncated,
        },
    )


def _legacy_expected_obligation_id(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
    obligation: CoverageObligation,
) -> str:
    return _legacy_stable_id(
        "obligation",
        {
            "snapshot": snapshot.digest,
            "change": plan.spec.change_ref.digest,
            "origin": obligation.origin,
            "target": obligation.target_ref,
            "role": obligation.required_role_ref,
            "type": obligation.obligation_type,
            "state": obligation.resolution_state,
            "paths": list(obligation.path_refs),
        },
    )


def _legacy_expected_reasons(snapshot: OrganizationSnapshot, path: ImpactPath) -> tuple[str, ...]:
    reasons = set(path.reason_codes)
    if "APPLICABILITY_INPUT_UNKNOWN" in reasons:
        reasons.remove("APPLICABILITY_INPUT_UNKNOWN")
        reasons.add("CHANGE_VALUE_UNKNOWN")
    reasons.difference_update(
        code
        for code in tuple(reasons)
        if code.startswith("APPLICABILITY_")
    )
    edge_by_id = {edge.edge_id: edge for edge in snapshot.spec.dependency_edges}
    candidate_edges = [
        edge
        for edge_ref in path.edge_refs
        if (edge := edge_by_id.get(edge_ref)) is not None
        and edge.admission_status
        in {AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED}
    ]
    node_by_id = {node.node_id: node for node in snapshot.spec.nodes}
    if candidate_edges and all(
        (source := node_by_id.get(edge.source_ref)) is not None
        and source.admission_status is AdmissionStatus.ADMITTED
        and (target := node_by_id.get(edge.target_ref)) is not None
        and target.admission_status is AdmissionStatus.ADMITTED
        for edge in candidate_edges
    ):
        reasons.discard("CANDIDATE_INPUT_NOT_AUTHORITY")
    return tuple(sorted(reasons))


def _obligation_semantic_key(
    obligation: CoverageObligation, paths_by_id: Mapping[str, ImpactPath]
) -> tuple[object, ...]:
    path_keys = tuple(
        sorted(
            (
                _path_semantic_key(path)
                if (path := paths_by_id.get(path_ref)) is not None
                else ("dangling_path_ref", path_ref)
                for path_ref in obligation.path_refs
            ),
            key=repr,
        )
    )
    return (
        obligation.origin,
        obligation.target_ref,
        obligation.domain_ref,
        obligation.required_role_ref,
        obligation.obligation_type,
        obligation.required_evidence,
        path_keys,
        obligation.resolution_state,
    )


def _legacy_required_orders(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
) -> tuple[RequiredOrder, ...]:
    edge_by_id = {edge.edge_id: edge for edge in snapshot.spec.dependency_edges}
    node_by_id = {node.node_id: node for node in snapshot.spec.nodes}
    required_roles = {
        obligation.required_role_ref for obligation in plan.spec.obligations
    }
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path in plan.spec.impact_paths:
        for edge_ref in path.edge_refs:
            edge = edge_by_id.get(edge_ref)
            if edge is None:
                # The path-set check owns the stable rejection code. Never
                # dereference an untrusted legacy plan ref while deriving order.
                continue
            source = node_by_id.get(edge.source_ref)
            target = node_by_id.get(edge.target_ref)
            if source is None or target is None:
                continue
            source_role = source.owner_role_ref
            target_role = target.owner_role_ref
            if path.state is ImpactState.UNKNOWN:
                target_role = snapshot.spec.completeness.discovery_role_ref
            if (
                source_role in required_roles
                and target_role in required_roles
                and source_role != target_role
            ):
                pairs[(source_role, target_role)].add(path.path_id)
    return tuple(
        RequiredOrder(
            left,
            right,
            tuple(sorted(reason_refs)),
            role_wide=True,
            dependency_reason_refs=tuple(sorted(reason_refs)),
        )
        for (left, right), reason_refs in sorted(pairs.items())
    )


def _verify_legacy_plan(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
    derived: DerivedContract,
) -> list[_Check]:
    """Verify frozen v0alpha1 plans through a semantic compatibility projection.

    Legacy plans have no evaluation ledger and their hash-derived path/obligation
    IDs predate evaluationRefs.  The verifier therefore compares the complete
    topology and obligation semantics, then runs every authority/work check over
    the legacy IDs.  This gate is unavailable to contextual snapshots or v0.2
    producers.
    """

    checks: list[_Check] = [_passed("applicability_evaluations")]
    expected_path_counts = Counter(
        _path_semantic_key(path) for path in derived.impact_paths
    )
    actual_path_counts = Counter(
        _path_semantic_key(path) for path in plan.spec.impact_paths
    )
    expected_by_key = {
        _path_semantic_key(path): path for path in derived.impact_paths
    }
    invalid_paths = sorted(
        {
            repr(key)
            for key in set(expected_path_counts) | set(actual_path_counts)
            if expected_path_counts[key] != actual_path_counts[key]
        }
    )
    for path in plan.spec.impact_paths:
        key = _path_semantic_key(path)
        expected_path = expected_by_key.get(key)
        if expected_path is None:
            invalid_paths.append(path.path_id)
            continue
        if (
            path.path_id != _legacy_expected_path_id(snapshot, plan, path)
            or path.evaluation_refs
            or path.duty_refs
            or path.reason_codes
            != _legacy_expected_reasons(snapshot, expected_path)
            or any(code not in REASON_CODE_REGISTRY for code in path.reason_codes)
        ):
            invalid_paths.append(path.path_id)
    invalid_paths.sort()
    if invalid_paths:
        checks.append(_failed("impact_paths", "IMPACT_PATH_OMITTED", *invalid_paths))
    else:
        checks.append(_passed("impact_paths"))
    checks.append(_passed("unknown_transition_duties"))

    expected_paths = {path.path_id: path for path in derived.impact_paths}
    actual_paths = {path.path_id: path for path in plan.spec.impact_paths}
    expected_obligations = Counter(
        _obligation_semantic_key(obligation, expected_paths)
        for obligation in derived.obligations
    )
    actual_obligations = Counter(
        _obligation_semantic_key(obligation, actual_paths)
        for obligation in plan.spec.obligations
    )
    dangling_obligations = sorted(
        obligation.obligation_id
        for obligation in plan.spec.obligations
        if not set(obligation.path_refs).issubset(actual_paths)
    )
    invalid_obligation_ids = sorted(
        obligation.obligation_id
        for obligation in plan.spec.obligations
        if obligation.obligation_id
        != _legacy_expected_obligation_id(snapshot, plan, obligation)
        or obligation.path_refs != tuple(sorted(obligation.path_refs))
    )
    if (
        expected_obligations != actual_obligations
        or dangling_obligations
        or invalid_obligation_ids
    ):
        checks.append(
            _failed(
                "obligations",
                "OBLIGATION_SET_MISMATCH",
                *(dangling_obligations + invalid_obligation_ids or (plan.metadata.id,)),
            )
        )
    else:
        checks.append(_passed("obligations"))
    checks.append(_passed("unknown_transition_duty_obligations"))

    expected_unresolved = tuple(
        sorted(
            path.path_id
            for path in plan.spec.impact_paths
            if path.state is ImpactState.UNKNOWN
        )
    )
    if tuple(sorted(plan.spec.unresolved_refs)) != expected_unresolved:
        checks.append(
            _failed(
                "unknown_preservation",
                "UNKNOWN_NOT_PRESERVED",
                *set(expected_unresolved).symmetric_difference(plan.spec.unresolved_refs),
            )
        )
    else:
        checks.append(_passed("unknown_preservation"))
    if derived.root_applicability_unknown:
        checks.append(
            _unknown(
                "root_applicability",
                "ROOT_APPLICABILITY_UNKNOWN",
                plan.spec.change_ref.resource_id,
            )
        )
    else:
        checks.append(_passed("root_applicability"))
    expected_status = "guarded_unresolved" if expected_unresolved else "planned"
    checks.append(
        _passed("plan_status")
        if plan.spec.status == expected_status
        else _failed("plan_status", "PLAN_STATUS_MISMATCH", plan.spec.status)
    )

    obligations_by_id = {
        obligation.obligation_id: obligation for obligation in plan.spec.obligations
    }
    legacy_order_contract = DerivedContract(
        snapshot_digest=derived.snapshot_digest,
        change_digest=derived.change_digest,
        applicability_evaluations=(),
        impact_paths=(),
        obligations=plan.spec.obligations,
        required_orders=_legacy_required_orders(snapshot, plan),
        unresolved_refs=expected_unresolved,
        root_applicability_unknown=derived.root_applicability_unknown,
    )
    checks.append(_check_coverage(plan, obligations_by_id))
    checks.append(_check_responsibility_bindings(plan))
    checks.append(_check_qualification(snapshot, plan, obligations_by_id))
    checks.append(_check_separation(snapshot, plan))
    checks.extend(_check_ordering(plan, legacy_order_contract))
    checks.append(_check_evidence(plan, obligations_by_id))
    checks.append(_check_effect_ceiling(plan))
    checks.append(_check_minimality(snapshot, plan, obligations_by_id))
    checks.append(_check_decisions(snapshot, plan, legacy_order_contract))
    return checks


def _check_coverage(plan: OrganizationPlan, obligations: Mapping[str, CoverageObligation]) -> _Check:
    work_coverage = Counter(
        obligation_ref
        for work_unit in plan.spec.work_units
        for obligation_ref in work_unit.obligation_refs
    )
    role_coverage = Counter(
        obligation_ref
        for role in plan.spec.role_instances
        for obligation_ref in role.obligation_refs
    )
    expected = set(obligations)
    invalid = sorted(
        obligation_ref
        for obligation_ref in expected | set(work_coverage) | set(role_coverage)
        if obligation_ref not in expected
        or work_coverage[obligation_ref] != 1
        or role_coverage[obligation_ref] != 1
    )
    role_ids = {item.role_instance_id for item in plan.spec.role_instances}
    dangling = sorted(
        work.work_unit_id
        for work in plan.spec.work_units
        if work.accountable_role_instance_ref not in work.role_instance_refs
        or not set(work.role_instance_refs).issubset(role_ids)
    )
    if invalid or dangling:
        return _failed("coverage", "OBLIGATION_UNSATISFIED", *(invalid + dangling))
    return _passed("coverage")


def _check_responsibility_bindings(plan: OrganizationPlan) -> _Check:
    roles = {item.role_instance_id: item for item in plan.spec.role_instances}
    invalid: list[str] = []
    for work in plan.spec.work_units:
        work_obligations = set(work.obligation_refs)
        contributing_obligations: set[str] = set()
        for role_ref in work.role_instance_refs:
            role = roles.get(role_ref)
            if role is None:
                continue
            contribution = work_obligations.intersection(role.obligation_refs)
            if not contribution:
                invalid.append(f"{work.work_unit_id}:{role_ref}")
            contributing_obligations.update(contribution)
        accountable = roles.get(work.accountable_role_instance_ref)
        if (
            accountable is None
            or not work_obligations.intersection(accountable.obligation_refs)
        ):
            invalid.append(f"{work.work_unit_id}:accountable")
        if contributing_obligations != work_obligations:
            invalid.append(work.work_unit_id)
    if invalid:
        return _failed(
            "responsibility_binding",
            "RESPONSIBILITY_BINDING_INVALID",
            *invalid,
        )
    return _passed("responsibility_binding")


def _check_qualification(
    snapshot: OrganizationSnapshot, plan: OrganizationPlan, obligations: Mapping[str, CoverageObligation]
) -> _Check:
    roles = {item.role_id: item for item in snapshot.spec.role_definitions}
    principals = {item.principal_id: item for item in snapshot.spec.principals}
    invalid: list[str] = []
    for instance in plan.spec.role_instances:
        role = roles.get(instance.role_definition_ref)
        principal = principals.get(instance.principal_ref)
        if role is None or principal is None:
            invalid.append(instance.role_instance_id)
            continue
        obligation_roles = {
            obligations[ref].required_role_ref
            for ref in instance.obligation_refs
            if ref in obligations
        }
        if (
            role.admission_status is not AdmissionStatus.ADMITTED
            or principal.admission_status is not AdmissionStatus.ADMITTED
            or principal.status != "active"
            or role.role_id not in principal.eligible_role_refs
            or not set(role.required_qualifications).issubset(principal.qualification_refs)
            or tuple(instance.qualification_refs) != tuple(role.required_qualifications)
            or obligation_roles != {role.role_id}
            or instance.mission != role.mission
        ):
            invalid.append(instance.role_instance_id)
    if invalid:
        return _failed("qualification", "QUALIFICATION_INVALID", *invalid)
    return _passed("qualification")


def _check_separation(snapshot: OrganizationSnapshot, plan: OrganizationPlan) -> _Check:
    principal_by_role: dict[str, set[str]] = defaultdict(set)
    for instance in plan.spec.role_instances:
        principal_by_role[instance.role_definition_ref].add(instance.principal_ref)
    invalid: list[str] = []
    for constraint in snapshot.spec.separation_constraints:
        if principal_by_role[constraint.left_role_ref] & principal_by_role[constraint.right_role_ref]:
            invalid.append(constraint.constraint_id)
    if invalid:
        return _failed(
            "separation_of_duties", "SEPARATION_OF_DUTIES_VIOLATION", *invalid
        )
    return _passed("separation_of_duties")


def _check_ordering(plan: OrganizationPlan, derived: DerivedContract) -> list[_Check]:
    role_by_instance = {
        item.role_instance_id: item.role_definition_ref for item in plan.spec.role_instances
    }
    roles_by_work = {
        work.work_unit_id: {
            role_by_instance[ref] for ref in work.role_instance_refs if ref in role_by_instance
        }
        for work in plan.spec.work_units
    }
    required_orders = {
        (item.predecessor_role_ref, item.successor_role_ref): item
        for item in derived.required_orders
    }
    obligation_by_id = {
        obligation.obligation_id: obligation for obligation in derived.obligations
    }
    obligation_ids_by_role: dict[str, set[str]] = defaultdict(set)
    for obligation in derived.obligations:
        obligation_ids_by_role[obligation.required_role_ref].add(
            obligation.obligation_id
        )
    obligations_by_work = {
        work.work_unit_id: set(work.obligation_refs) for work in plan.spec.work_units
    }
    expected_work_pairs_by_order: dict[tuple[str, str], set[tuple[str, str]]] = {}
    expected_reasons_by_order_work_pair: dict[
        tuple[tuple[str, str], tuple[str, str]], tuple[str, ...]
    ] = {}
    orders_by_work_pair: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    unrepresentable_orders: set[tuple[str, str]] = set()
    for role_pair, order in required_orders.items():
        expected_reasons, unrepresentable = _order_expected_work_pair_reasons(
            order,
            obligations_by_work,
            obligation_by_id,
            obligation_ids_by_role,
        )
        expected_pairs = set(expected_reasons)
        if unrepresentable or not expected_pairs:
            unrepresentable_orders.add(role_pair)
        for work_pair, reason_refs in expected_reasons.items():
            orders_by_work_pair[work_pair].add(role_pair)
            expected_reasons_by_order_work_pair[(role_pair, work_pair)] = reason_refs
        expected_work_pairs_by_order[role_pair] = expected_pairs

    covered_work_pairs: set[tuple[str, str]] = set()
    invalid_constraints: list[str] = []
    constraint_keys = Counter(
        (edge.predecessor_ref, edge.successor_ref, edge.relation)
        for edge in plan.spec.happens_before
    )
    invalid_constraints.extend(
        f"{left}->{right}"
        for (left, right, _), count in constraint_keys.items()
        if count != 1
    )

    nodes = {work.work_unit_id for work in plan.spec.work_units}
    for edge in plan.spec.happens_before:
        edge_ref = f"{edge.predecessor_ref}->{edge.successor_ref}"
        if edge.predecessor_ref not in nodes or edge.successor_ref not in nodes:
            invalid_constraints.append(edge_ref)
            continue
        work_pair = (edge.predecessor_ref, edge.successor_ref)
        induced_pairs = {
            (left_role, right_role)
            for left_role in roles_by_work[edge.predecessor_ref]
            for right_role in roles_by_work[edge.successor_ref]
        }
        applicable_pairs = orders_by_work_pair.get(work_pair, set())
        expected_reason_refs = tuple(
            sorted(
                {
                    reason_ref
                    for pair in applicable_pairs
                    for reason_ref in expected_reasons_by_order_work_pair[
                        (pair, work_pair)
                    ]
                }
            )
        )
        edge_valid = (
            bool(applicable_pairs)
            and induced_pairs == applicable_pairs
            and edge.reason_refs == expected_reason_refs
        )
        if edge_valid:
            covered_work_pairs.add(work_pair)
        else:
            invalid_constraints.append(edge_ref)

    missing_orders = tuple(
        item
        for item in derived.required_orders
        if (
            (pair := (item.predecessor_role_ref, item.successor_role_ref))
            in unrepresentable_orders
            or not expected_work_pairs_by_order[pair].issubset(covered_work_pairs)
        )
    )
    missing = sorted(
        f"{item.predecessor_role_ref}->{item.successor_role_ref}"
        for item in missing_orders
    )
    order_check = (
        _failed_with_codes(
            "ordering",
            {
                "ORDER_CONSTRAINT_MISSING",
                *(
                    {"PREREQUISITE_OBLIGATION_ORDER_MISSING"}
                    if any(
                        any(ref in obligation_by_id for ref in item.reason_refs)
                        for item in missing_orders
                    )
                    else set()
                ),
            },
            tuple(missing),
        )
        if missing
        else _passed("ordering")
    )
    constraint_check = (
        _failed(
            "order_constraints",
            "ORDER_CONSTRAINT_INVALID",
            *invalid_constraints,
        )
        if invalid_constraints
        else _passed("order_constraints")
    )

    work_cycle_nodes = _cycle_nodes(
        (edge.predecessor_ref, edge.successor_ref)
        for edge in plan.spec.happens_before
        if edge.predecessor_ref in nodes and edge.successor_ref in nodes
    )
    role_cycle_nodes = _cycle_nodes(
        (order.predecessor_role_ref, order.successor_role_ref)
        for order in derived.required_orders
    )
    cycle_witnesses = tuple(sorted(work_cycle_nodes | role_cycle_nodes))
    cycle_check = (
        _failed("order_acyclic", "ORDER_CYCLE", *cycle_witnesses)
        if cycle_witnesses
        else _passed("order_acyclic")
    )
    return [order_check, constraint_check, cycle_check]


def _order_expected_work_pair_reasons(
    order: RequiredOrder,
    obligations_by_work: dict[str, set[str]],
    obligation_by_id: dict[str, CoverageObligation],
    obligation_ids_by_role: dict[str, set[str]],
) -> tuple[dict[tuple[str, str], tuple[str, ...]], bool]:
    expected: dict[tuple[str, str], set[str]] = defaultdict(set)
    unrepresentable = False

    def bind_matrix(
        predecessor_required: set[str],
        successor_required: set[str],
        reason_refs: tuple[str, ...],
    ) -> None:
        nonlocal unrepresentable
        predecessor_work_refs = {
            work_ref
            for work_ref, obligation_refs in obligations_by_work.items()
            if obligation_refs.intersection(predecessor_required)
        }
        successor_work_refs = {
            work_ref
            for work_ref, obligation_refs in obligations_by_work.items()
            if obligation_refs.intersection(successor_required)
        }
        if not predecessor_work_refs or not successor_work_refs:
            unrepresentable = True
        for predecessor_ref in predecessor_work_refs:
            for successor_ref in successor_work_refs:
                if predecessor_ref == successor_ref:
                    unrepresentable = True
                    continue
                expected[(predecessor_ref, successor_ref)].update(reason_refs)

    if order.dependency_reason_refs:
        bind_matrix(
            obligation_ids_by_role[order.predecessor_role_ref],
            obligation_ids_by_role[order.successor_role_ref],
            order.dependency_reason_refs,
        )

    for reason_group in order.prerequisite_reason_groups:
        predecessor_required = {
            ref
            for ref in reason_group
            if ref in obligation_by_id
            and obligation_by_id[ref].required_role_ref
            == order.predecessor_role_ref
        }
        successor_required = {
            ref
            for ref in reason_group
            if ref in obligation_by_id
            and obligation_by_id[ref].required_role_ref == order.successor_role_ref
        }
        if not predecessor_required or not successor_required:
            unrepresentable = True
            continue
        bind_matrix(predecessor_required, successor_required, reason_group)

    if not order.dependency_reason_refs and not order.prerequisite_reason_groups:
        reason_obligation_ids = {
            ref for ref in order.reason_refs if ref in obligation_by_id
        }
        predecessor_required = (
            {
                ref
                for ref in reason_obligation_ids
                if obligation_by_id[ref].required_role_ref
                == order.predecessor_role_ref
            }
            if reason_obligation_ids
            else obligation_ids_by_role[order.predecessor_role_ref]
        )
        successor_required = (
            {
                ref
                for ref in reason_obligation_ids
                if obligation_by_id[ref].required_role_ref
                == order.successor_role_ref
            }
            if reason_obligation_ids
            else obligation_ids_by_role[order.successor_role_ref]
        )
        bind_matrix(predecessor_required, successor_required, order.reason_refs)

    return (
        {
            work_pair: tuple(sorted(reason_refs))
            for work_pair, reason_refs in expected.items()
        },
        unrepresentable,
    )


def _cycle_nodes(edges: Iterable[tuple[str, str]]) -> set[str]:
    materialized = tuple(edges)
    nodes = {node for edge in materialized for node in edge}
    indegree = dict.fromkeys(nodes, 0)
    successors: dict[str, set[str]] = defaultdict(set)
    for predecessor, successor in materialized:
        if successor not in successors[predecessor]:
            successors[predecessor].add(successor)
            indegree[successor] += 1
    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    visited: set[str] = set()
    while queue:
        node = queue.popleft()
        visited.add(node)
        for successor in sorted(successors[node]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    return nodes - visited if len(visited) != len(nodes) else set()


def _check_evidence(plan: OrganizationPlan, obligations: Mapping[str, CoverageObligation]) -> _Check:
    missing: list[str] = []
    for work in plan.spec.work_units:
        required = {
            evidence
            for obligation_ref in work.obligation_refs
            if obligation_ref in obligations
            for evidence in obligations[obligation_ref].required_evidence
        }
        if not required.issubset(work.evidence_outputs):
            missing.append(work.work_unit_id)
    if missing:
        return _failed("evidence", "EVIDENCE_DUTY_MISSING", *missing)
    return _passed("evidence")


def _check_effect_ceiling(plan: OrganizationPlan) -> _Check:
    invalid: list[str] = []
    if plan.spec.effect_ceiling is not EffectCeiling.ZERO_EFFECT:
        invalid.append(plan.metadata.id)
    invalid.extend(
        item.role_instance_id
        for item in plan.spec.role_instances
        if item.effect_ceiling is not EffectCeiling.ZERO_EFFECT
    )
    invalid.extend(
        item.work_unit_id
        for item in plan.spec.work_units
        if item.effect_ceiling is not EffectCeiling.ZERO_EFFECT
    )
    if invalid:
        return _failed("effect_ceiling", "EFFECT_CEILING_EXCEEDED", *invalid)
    return _passed("effect_ceiling")


def _check_minimality(
    snapshot: OrganizationSnapshot, plan: OrganizationPlan, obligations: Mapping[str, CoverageObligation]
) -> _Check:
    role_ids = {item.role_instance_id for item in plan.spec.role_instances}
    work_ids = {item.work_unit_id for item in plan.spec.work_units}
    referenced_roles = {ref for work in plan.spec.work_units for ref in work.role_instance_refs}
    non_removable = role_ids | work_ids
    invalid: list[str] = []
    invalid.extend(sorted(role_ids - referenced_roles))
    invalid.extend(
        sorted(
            work.work_unit_id
            for work in plan.spec.work_units
            if not set(work.obligation_refs).intersection(obligations)
        )
    )
    if plan.spec.minimality.level != "inclusion_minimal":
        invalid.append(plan.metadata.id)
    if set(plan.spec.minimality.non_removable_refs) != non_removable:
        invalid.append("minimality.nonRemovableRefs")
    if set(plan.spec.minimality.considered_role_refs) != {
        role.role_id for role in snapshot.spec.role_definitions
    }:
        invalid.append("minimality.consideredRoleRefs")
    if set(plan.spec.minimality.considered_principal_refs) != {
        principal.principal_id for principal in snapshot.spec.principals
    }:
        invalid.append("minimality.consideredPrincipalRefs")
    if invalid:
        return _failed("minimality", "PLAN_NOT_MINIMAL", *invalid)
    return _passed("minimality")


def _check_decisions(
    snapshot: OrganizationSnapshot, plan: OrganizationPlan, derived: DerivedContract
) -> _Check:
    del derived
    expected_subjects = {
        role.role_id for role in snapshot.spec.role_definitions
    } | {principal.principal_id for principal in snapshot.spec.principals}
    expected_subjects |= {
        edge.edge_id
        for edge in snapshot.spec.dependency_edges
        if edge.admission_status is not AdmissionStatus.ADMITTED
    }
    expected_subjects |= {
        rule.rule_id
        for rule in snapshot.spec.impact_rules
        if rule.admission_status is not AdmissionStatus.ADMITTED
    }
    expected_subjects |= {
        duty.duty_id
        for duty in snapshot.spec.unknown_transition_duties
        if duty.admission_status is not AdmissionStatus.ADMITTED
    }
    decision_subjects = {decision.subject_ref for decision in plan.spec.decisions}
    invalid = sorted(expected_subjects.symmetric_difference(decision_subjects))
    if len(decision_subjects) != len(plan.spec.decisions):
        invalid.append("decisions.duplicateSubjectRef")
    decision_by_subject = {decision.subject_ref: decision for decision in plan.spec.decisions}
    selected_roles = {instance.role_definition_ref for instance in plan.spec.role_instances}
    selected_principals = {instance.principal_ref for instance in plan.spec.role_instances}
    for role in snapshot.spec.role_definitions:
        decision = decision_by_subject.get(role.role_id)
        selected = role.role_id in selected_roles
        if decision is not None and (
            decision.input_class != role.admission_status.value
            or decision.disposition != ("included" if selected else "excluded")
            or decision.reason_codes
            != (
                "ROLE_SELECTED_FOR_OBLIGATION" if selected else "NOT_REQUIRED_BY_CONTRACT",
            )
        ):
            invalid.append(decision.decision_id)
    for principal in snapshot.spec.principals:
        decision = decision_by_subject.get(principal.principal_id)
        selected = principal.principal_id in selected_principals
        if decision is not None and (
            decision.input_class != principal.admission_status.value
            or decision.disposition != ("included" if selected else "excluded")
            or decision.reason_codes
            != (
                "PRINCIPAL_SELECTED_QUALIFIED" if selected else "PRINCIPAL_NOT_SELECTED",
            )
        ):
            invalid.append(decision.decision_id)
    for edge in snapshot.spec.dependency_edges:
        if edge.admission_status is AdmissionStatus.ADMITTED:
            continue
        decision = decision_by_subject.get(edge.edge_id)
        retracted = edge.admission_status is AdmissionStatus.RETRACTED
        if decision is not None and (
            decision.input_class != edge.admission_status.value
            or decision.disposition != ("excluded" if retracted else "unresolved")
            or decision.reason_codes
            != (
                "RETRACTED_SOURCE_EXCLUDED"
                if retracted
                else "CANDIDATE_EDGE_NOT_AUTHORITY",
            )
        ):
            invalid.append(decision.decision_id)
    for rule in snapshot.spec.impact_rules:
        if rule.admission_status is AdmissionStatus.ADMITTED:
            continue
        decision = decision_by_subject.get(rule.rule_id)
        retracted = rule.admission_status is AdmissionStatus.RETRACTED
        if decision is not None and (
            decision.input_class != rule.admission_status.value
            or decision.disposition != ("excluded" if retracted else "unresolved")
            or decision.reason_codes
            != (
                "RETRACTED_SOURCE_EXCLUDED"
                if retracted
                else "CANDIDATE_INPUT_NOT_AUTHORITY",
            )
        ):
            invalid.append(decision.decision_id)
    for duty in snapshot.spec.unknown_transition_duties:
        if duty.admission_status is AdmissionStatus.ADMITTED:
            continue
        decision = decision_by_subject.get(duty.duty_id)
        retracted = duty.admission_status is AdmissionStatus.RETRACTED
        if decision is not None and (
            decision.input_class != duty.admission_status.value
            or decision.disposition != ("excluded" if retracted else "unresolved")
            or decision.reason_codes
            != (
                "RETRACTED_SOURCE_EXCLUDED"
                if retracted
                else "CANDIDATE_INPUT_NOT_AUTHORITY",
            )
        ):
            invalid.append(decision.decision_id)
    invalid.extend(
        sorted(
            decision.decision_id
            for decision in plan.spec.decisions
            if any(code not in REASON_CODE_REGISTRY for code in decision.reason_codes)
        )
    )
    if invalid:
        return _failed("consideration_ledger", "PLAN_NOT_MINIMAL", *invalid)
    return _passed("consideration_ledger")
