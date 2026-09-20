"""Shared deterministic impact and obligation relation for closed change profiles.

Applicability is evaluated only by :mod:`oac.applicability`. This module turns
the resulting tri-valued ledger into graph paths, obligations, and explicit
ordering constraints; it never reimplements predicate atoms or consults
benchmark annotations.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal, TypedDict

from .applicability import evaluate_applicability
from .canonical import verify_resource_digest
from .change_profiles import SUPPLIER_PROFILE, ChangeProfile, get_change_profile
from .identifiers import coverage_obligation_identifier, impact_path_identifier
from .models import (
    PROPAGATING_RELATIONS,
    AdmissionStatus,
    ApplicabilityEvaluation,
    ApplicabilityResult,
    BoundaryStatus,
    ChangeDelta,
    ChangeProfileBinding,
    ContextualImpactRule,
    CoverageObligation,
    DependencyEdge,
    ImpactPath,
    ImpactState,
    OrganizationNode,
    OrganizationSnapshot,
    SemanticChangeSet,
    UnknownTransitionDuty,
)
from .resource_profile import (
    enforce_evaluation_budget,
    enforce_path_prefix_budget,
    enforce_supplier_inputs,
)
from .sealed import AdmittedSealedResource, validate_sealed_admission

type _PathOrigin = Literal["dependency", "rule", "gap", "bounded_non_impact", "excluded"]
type _ObligationOrigin = Literal["dependency_path", "organizational_rule", "discovery_gap"]
type _ObligationState = Literal["affected", "unknown"]
type _ObligationKey = tuple[_ObligationOrigin, str, str, str, _ObligationState]


class _ObligationGroup(TypedDict):
    domain: str
    evidence: set[str]
    paths: set[str]


class ProfileError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RequiredOrder:
    predecessor_role_ref: str
    successor_role_ref: str
    reason_refs: tuple[str, ...]
    role_wide: bool = False
    dependency_reason_refs: tuple[str, ...] = ()
    prerequisite_reason_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class DerivedContract:
    snapshot_digest: str
    change_digest: str
    applicability_evaluations: tuple[ApplicabilityEvaluation, ...]
    impact_paths: tuple[ImpactPath, ...]
    obligations: tuple[CoverageObligation, ...]
    required_orders: tuple[RequiredOrder, ...]
    unresolved_refs: tuple[str, ...]
    root_applicability_unknown: bool
    profile_binding: ChangeProfileBinding | None = None


@dataclass(frozen=True, slots=True)
class _NonImpactProof:
    evaluation_refs: tuple[str, ...]
    has_route: bool
    authoritative_cut: bool


def _profile_delta(change: SemanticChangeSet, profile: ChangeProfile) -> ChangeDelta:
    return next(delta for delta in change.spec.deltas if delta.path == profile.delta_path)


def _validate_known_values(delta: ChangeDelta, profile: ChangeProfile) -> None:
    """Reject ill-typed or synthetic values on either side of the transition."""

    for side, observed in (("before", delta.before), ("after", delta.after)):
        if observed.state != "known":
            continue
        if isinstance(observed.value, bool) or not isinstance(observed.value, str):
            raise ProfileError(
                "UNSUPPORTED_SEMANTICS",
                (
                    "SupplierChange requires a non-boolean string status "
                    if profile.binding is None
                    else "Selected change profile requires a string value "
                )
                + f"when {side}.state is known",
            )
        if observed.value == "unknown":
            raise ProfileError(
                "SYNTHETIC_UNKNOWN_VALUE",
                f"the known string 'unknown' cannot stand for an unknown {side} "
                + ("status" if profile.binding is None else "value"),
            )


def validate_supplier_input_coherence(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet
) -> None:
    """Enforce the MVP's single-enterprise input boundary."""

    violations: list[str] = []
    if snapshot.metadata.namespace != change.metadata.namespace:
        violations.append("metadata.namespace")
    if (
        snapshot.metadata.governance_ref is None
        or change.metadata.governance_ref is None
        or snapshot.metadata.governance_ref != change.metadata.governance_ref
    ):
        violations.append("metadata.governanceRef")
    if snapshot.metadata.owner_ref is None:
        violations.append("snapshot.metadata.ownerRef")
    if change.metadata.owner_ref is None:
        violations.append("change.metadata.ownerRef")

    admitted_role_ids = {
        role.role_id
        for role in snapshot.spec.role_definitions
        if role.admission_status is AdmissionStatus.ADMITTED
    }
    if change.metadata.owner_ref not in admitted_role_ids:
        violations.append("change.metadata.ownerRef.authority")
    if not snapshot.metadata.source_refs:
        violations.append("snapshot.metadata.sourceRefs")
    if change.spec.source_ref not in change.metadata.source_refs:
        violations.append("change.spec.sourceRef")

    node_ids = {node.node_id for node in snapshot.spec.nodes}
    allowed_scope_refs = node_ids | {snapshot.metadata.namespace}
    if not set(change.spec.scope_refs).issubset(allowed_scope_refs):
        violations.append("change.spec.scopeRefs.foreign")
    if (
        change.spec.subject_ref not in change.spec.scope_refs
        and snapshot.metadata.namespace not in change.spec.scope_refs
    ):
        violations.append("change.spec.scopeRefs.anchor")

    if violations:
        raise ProfileError(
            "RESOURCE_COHERENCE_VIOLATION",
            "single-enterprise roots are incoherent: " + ", ".join(sorted(violations)),
        )


def derive_supplier_contract(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet
) -> DerivedContract:
    """Derive from legacy resources after their historical typed digest check."""

    return derive_change_contract(snapshot, change, profile=SUPPLIER_PROFILE)


def derive_change_contract(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet, *, profile: str
) -> DerivedContract:
    return _derive_contract(
        snapshot, change, profile=get_change_profile(profile), verify_legacy_digests=True
    )


def derive_supplier_contract_from_admitted(
    snapshot_admission: AdmittedSealedResource,
    change_admission: AdmittedSealedResource,
) -> DerivedContract:
    return derive_change_contract_from_admitted(
        snapshot_admission, change_admission, profile=SUPPLIER_PROFILE
    )


def derive_change_contract_from_admitted(
    snapshot_admission: AdmittedSealedResource,
    change_admission: AdmittedSealedResource,
    *,
    profile: str,
) -> DerivedContract:
    """Derive from roots already admitted through ``SealedResource/v1``.

    The A1 raw-resource digest intentionally differs from the legacy
    model-materialized digest for inputs that omit optional defaults.  This
    entrypoint accepts only immutable admission records and therefore must not
    re-run the incompatible legacy digest projection.
    """

    snapshot_admission = validate_sealed_admission(snapshot_admission, "OrganizationSnapshot")
    change_admission = validate_sealed_admission(change_admission, "SemanticChangeSet")
    snapshot = snapshot_admission.resource
    change = change_admission.resource
    if not isinstance(snapshot, OrganizationSnapshot) or not isinstance(change, SemanticChangeSet):
        raise ProfileError(
            "RESOURCE_COHERENCE_VIOLATION",
            "admitted roots must be OrganizationSnapshot and SemanticChangeSet",
        )
    if (
        snapshot.digest != snapshot_admission.resource_digest
        or change.digest != change_admission.resource_digest
    ):
        raise ProfileError(
            "RESOURCE_COHERENCE_VIOLATION",
            "admission record digests do not match typed root coordinates",
        )
    return _derive_contract(snapshot, change, profile=get_change_profile(profile), verify_legacy_digests=False)


def _derive_contract(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    *,
    verify_legacy_digests: bool,
    profile: ChangeProfile,
) -> DerivedContract:
    """Shared total relation after the selected root-admission boundary."""

    if verify_legacy_digests:
        verify_resource_digest(snapshot)
        verify_resource_digest(change)
    enforce_supplier_inputs(snapshot, change)
    validate_supplier_input_coherence(snapshot, change)
    inconsistent_deltas = tuple(
        sorted(delta.path for delta in change.spec.deltas if not delta.operation_is_consistent())
    )
    if inconsistent_deltas:
        raise ProfileError(
            "CHANGE_OPERATION_INCONSISTENT",
            "operation does not match before/after states at: " + ", ".join(inconsistent_deltas),
        )
    if change.spec.admission_status is not AdmissionStatus.ADMITTED:
        raise ProfileError("CHANGE_NOT_ADMITTED", "only admitted changes can be compiled")
    if change.spec.semantic_type != profile.semantic_type:
        raise ProfileError(
            "UNSUPPORTED_SEMANTICS",
            (
                "SupplierChange requires semanticType supplier.status"
                if profile.binding is None
                else f"Selected change profile requires semanticType {profile.semantic_type}"
            ),
        )
    if len(change.spec.deltas) != 1 or change.spec.deltas[0].path != profile.delta_path:
        raise ProfileError(
            "SUPPLIER_STATUS_DELTA_SET_INVALID"
            if profile.binding is None
            else "CHANGE_PROFILE_DELTA_SET_INVALID",
            (
                "SupplierChange requires exactly one delta and its path must be /status"
                if profile.binding is None
                else f"Selected change profile requires exactly one delta at {profile.delta_path}"
            ),
        )
    if change.spec.subject_ref not in {node.node_id for node in snapshot.spec.nodes}:
        raise ProfileError("UNSUPPORTED_SEMANTICS", "change subject is not in the snapshot")

    status_delta = _profile_delta(change, profile)
    _validate_known_values(status_delta, profile)

    node_by_id = {node.node_id: node for node in snapshot.spec.nodes}
    role_by_id = {role.role_id: role for role in snapshot.spec.role_definitions}
    subject = node_by_id[change.spec.subject_ref]
    if subject.admission_status is AdmissionStatus.RETRACTED:
        raise ProfileError(
            "RETRACTED_SOURCE_EXCLUDED", "the change subject is a retracted source object"
        )

    evaluations = _evaluate_snapshot_applicability(snapshot, change, delta_path=profile.delta_path)
    enforce_evaluation_budget(
        tuple(len(evaluation.witness_refs) for evaluation in evaluations)
    )
    blocking_evaluation_codes = {
        "APPLICABILITY_SOURCE_MISSING",
        "APPLICABILITY_SOURCE_MISMATCH",
        "PREDICATE_DEFINITION_INVALID",
        "PREDICATE_VERSION_UNSUPPORTED",
        "SYNTHETIC_UNKNOWN_VALUE",
    }
    blocking = sorted(
        {
            code
            for evaluation in evaluations
            for code in evaluation.reason_codes
            if code in blocking_evaluation_codes
        }
    )
    if blocking:
        raise ProfileError(blocking[0], "invalid contextual applicability definition")
    evaluation_by_source = {item.source_ref: item for item in evaluations}

    outgoing: dict[str, list[DependencyEdge]] = defaultdict(list)
    for edge in snapshot.spec.dependency_edges:
        if (
            edge.relation_type in PROPAGATING_RELATIONS
            and edge.admission_status is not AdmissionStatus.RETRACTED
        ):
            outgoing[edge.source_ref].append(edge)
    for edges in outgoing.values():
        edges.sort(key=lambda edge: edge.edge_id)

    raw_paths: dict[str, ImpactPath] = {}
    truncation_frontiers: set[tuple[str, tuple[str, ...]]] = set()
    materialized_path_prefixes = 0

    def add_path(
        *,
        target_ref: str,
        state: ImpactState,
        edge_refs: tuple[str, ...] = (),
        rule_refs: tuple[str, ...] = (),
        evaluation_refs: tuple[str, ...] = (),
        duty_refs: tuple[str, ...] = (),
        origin: _PathOrigin,
        reason_codes: tuple[str, ...] = (),
        truncated: bool = False,
    ) -> ImpactPath:
        nonlocal materialized_path_prefixes
        materialized_path_prefixes += 1
        enforce_path_prefix_budget(materialized_path_prefixes)
        ordered_reasons = tuple(sorted(set(reason_codes)))
        path_id = impact_path_identifier(
            snapshot_digest=snapshot.digest,
            change_digest=change.digest,
            target_ref=target_ref,
            state=state.value,
            edge_refs=edge_refs,
            rule_refs=rule_refs,
            evaluation_refs=evaluation_refs,
            duty_refs=duty_refs,
            origin=origin,
            truncated=truncated,
        )
        path = ImpactPath(
            pathId=path_id,
            targetRef=target_ref,
            state=state,
            edgeRefs=edge_refs,
            ruleRefs=rule_refs,
            evaluationRefs=evaluation_refs,
            dutyRefs=duty_refs,
            origin=origin,
            reasonCodes=ordered_reasons,
            truncated=truncated,
        )
        raw_paths[path_id] = path
        return path

    seed_state = (
        ImpactState.AFFECTED
        if subject.admission_status is AdmissionStatus.ADMITTED
        else ImpactState.UNKNOWN
    )
    seed_reasons = (
        ()
        if seed_state is ImpactState.AFFECTED
        else ("CANDIDATE_INPUT_NOT_AUTHORITY",)
    )
    seed_path = add_path(
        target_ref=change.spec.subject_ref,
        state=seed_state,
        origin="dependency" if seed_state is ImpactState.AFFECTED else "gap",
        reason_codes=seed_reasons,
    )

    # Keep every independent simple path. A FALSE edge is absent from closure;
    # UNKNOWN remains traversable but can never be upgraded by a later TRUE edge.
    queue: deque[
        tuple[
            str,
            ImpactState,
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
        ]
    ] = deque(
        [
            (
                change.spec.subject_ref,
                seed_state,
                (),
                (),
                (change.spec.subject_ref,),
                seed_reasons,
            )
        ]
    )
    while queue:
        source_ref, source_state, edge_path, evaluation_path, node_path, source_reasons = (
            queue.popleft()
        )
        for edge in outgoing.get(source_ref, []):
            evaluation = evaluation_by_source[edge.edge_id]
            if evaluation.result is ApplicabilityResult.FALSE:
                continue
            if (
                node_by_id[edge.source_ref].admission_status
                is AdmissionStatus.RETRACTED
                or node_by_id[edge.target_ref].admission_status
                is AdmissionStatus.RETRACTED
            ):
                continue
            if edge.target_ref in node_path:
                continue
            next_edges = (*edge_path, edge.edge_id)
            if len(next_edges) > snapshot.spec.completeness.max_depth:
                continue
            next_evaluations = (*evaluation_path, evaluation.evaluation_id)

            covered = (
                edge.source_ref in snapshot.spec.completeness.covered_node_refs
                and edge.target_ref in snapshot.spec.completeness.covered_node_refs
                and edge.relation_type in snapshot.spec.completeness.covered_relation_types
            )
            endpoints_admitted = (
                node_by_id[edge.source_ref].admission_status is AdmissionStatus.ADMITTED
                and node_by_id[edge.target_ref].admission_status is AdmissionStatus.ADMITTED
            )
            authoritative = (
                evaluation.result is ApplicabilityResult.TRUE
                and edge.admission_status is AdmissionStatus.ADMITTED
                and covered
                and endpoints_admitted
            )
            next_state = (
                ImpactState.AFFECTED
                if source_state is ImpactState.AFFECTED and authoritative
                else ImpactState.UNKNOWN
            )
            reasons = set(source_reasons)
            reasons.update(evaluation.reason_codes)
            if edge.admission_status is not AdmissionStatus.ADMITTED:
                reasons.add("CANDIDATE_EDGE_NOT_AUTHORITY")
            if not endpoints_admitted:
                reasons.add("CANDIDATE_INPUT_NOT_AUTHORITY")
            if not covered:
                reasons.add("GRAPH_COVERAGE_PARTIAL")

            next_nodes = (*node_path, edge.target_ref)
            at_limit = (
                len(next_edges) == snapshot.spec.completeness.max_depth
                and _has_continuable_edge(
                    edge.target_ref,
                    next_nodes,
                    outgoing,
                    evaluation_by_source,
                    node_by_id,
                )
            )
            if at_limit:
                next_state = ImpactState.UNKNOWN
                reasons.add("IMPACT_SEARCH_TRUNCATED")
                truncation_frontiers.add((edge.target_ref, next_nodes))
            add_path(
                target_ref=edge.target_ref,
                state=next_state,
                edge_refs=next_edges,
                evaluation_refs=next_evaluations,
                origin="dependency" if next_state is ImpactState.AFFECTED else "gap",
                reason_codes=tuple(reasons),
                truncated=at_limit,
            )
            if not at_limit:
                queue.append(
                    (
                        edge.target_ref,
                        next_state,
                        next_edges,
                        next_evaluations,
                        next_nodes,
                        tuple(reasons),
                    )
                )

    for rule in sorted(snapshot.spec.impact_rules, key=lambda item: item.rule_id):
        evaluation = evaluation_by_source[rule.rule_id]
        if (
            evaluation.result is ApplicabilityResult.FALSE
            or rule.admission_status is AdmissionStatus.RETRACTED
        ):
            continue
        target = node_by_id[rule.target_ref]
        required_role = role_by_id[rule.required_role_ref]
        if (
            target.admission_status is AdmissionStatus.RETRACTED
            or required_role.admission_status is AdmissionStatus.RETRACTED
        ):
            continue
        authoritative = (
            evaluation.result is ApplicabilityResult.TRUE
            and rule.admission_status is AdmissionStatus.ADMITTED
            and subject.admission_status is AdmissionStatus.ADMITTED
            and target.admission_status is AdmissionStatus.ADMITTED
            and required_role.admission_status is AdmissionStatus.ADMITTED
        )
        state = ImpactState.AFFECTED if authoritative else ImpactState.UNKNOWN
        reasons = set(evaluation.reason_codes)
        if not authoritative and evaluation.result is ApplicabilityResult.TRUE:
            reasons.add("CANDIDATE_INPUT_NOT_AUTHORITY")
        add_path(
            target_ref=rule.target_ref,
            state=state,
            rule_refs=(rule.rule_id,),
            evaluation_refs=(evaluation.evaluation_id,),
            origin="rule" if state is ImpactState.AFFECTED else "gap",
            reason_codes=tuple(reasons),
        )

    for duty in sorted(
        snapshot.spec.unknown_transition_duties, key=lambda item: item.duty_id
    ):
        evaluation = evaluation_by_source[duty.duty_id]
        if evaluation.result is not ApplicabilityResult.UNKNOWN:
            continue
        target = node_by_id[duty.target_ref]
        required_role = role_by_id[duty.required_role_ref]
        admitted_duty = duty.admission_status is AdmissionStatus.ADMITTED
        if target.admission_status is AdmissionStatus.RETRACTED:
            continue
        if admitted_duty and required_role.admission_status is not AdmissionStatus.ADMITTED:
            raise ProfileError(
                "UNKNOWN_TRANSITION_DUTY_INVALID",
                f"unknown-transition duty lacks an admitted role: {duty.duty_id}",
            )
        add_path(
            target_ref=duty.target_ref,
            state=ImpactState.UNKNOWN,
            evaluation_refs=(evaluation.evaluation_id,),
            duty_refs=(duty.duty_id,) if admitted_duty else (),
            origin="gap",
            reason_codes=evaluation.reason_codes,
        )

    for gap in sorted(snapshot.spec.completeness.known_gaps):
        add_path(
            target_ref=gap,
            state=ImpactState.UNKNOWN,
            origin="gap",
            reason_codes=("GRAPH_COVERAGE_PARTIAL",),
        )
    if (
        snapshot.spec.completeness.status is not BoundaryStatus.COMPLETE
        and not snapshot.spec.completeness.known_gaps
    ):
        add_path(
            target_ref=(
                snapshot.spec.completeness.discovery_target_ref
                or "urn:oac:boundary:unobserved"
            ),
            state=ImpactState.UNKNOWN,
            origin="gap",
            reason_codes=("GRAPH_COVERAGE_PARTIAL",),
        )

    reached_targets = {path.target_ref for path in raw_paths.values()}
    truncated_reachable_targets = _truncated_continuation_targets(
        truncation_frontiers,
        outgoing,
        evaluation_by_source,
        node_by_id,
    )
    if snapshot.spec.completeness.status is BoundaryStatus.COMPLETE:
        for node in sorted(snapshot.spec.nodes, key=lambda item: item.node_id):
            if node.node_id in reached_targets:
                continue
            nonimpact_proof = _bounded_nonimpact_proof(
                snapshot,
                change.spec.subject_ref,
                node.node_id,
                outgoing,
                evaluation_by_source,
                node_by_id,
            )
            if node.admission_status is AdmissionStatus.RETRACTED:
                add_path(
                    target_ref=node.node_id,
                    state=ImpactState.OUT_OF_DECLARED_SCOPE,
                    origin="excluded",
                    reason_codes=("RETRACTED_SOURCE_EXCLUDED",),
                )
            elif node.node_id in truncated_reachable_targets:
                reasons = {"IMPACT_SEARCH_TRUNCATED"}
                if node.admission_status is not AdmissionStatus.ADMITTED:
                    reasons.add("CANDIDATE_INPUT_NOT_AUTHORITY")
                if node.node_id not in snapshot.spec.completeness.covered_node_refs:
                    reasons.add("GRAPH_COVERAGE_PARTIAL")
                add_path(
                    target_ref=node.node_id,
                    state=ImpactState.UNKNOWN,
                    origin="gap",
                    reason_codes=tuple(reasons),
                )
            elif (
                node.admission_status is not AdmissionStatus.ADMITTED
                or node.node_id not in snapshot.spec.completeness.covered_node_refs
            ):
                if (
                    nonimpact_proof is not None
                    and nonimpact_proof.has_route
                    and nonimpact_proof.authoritative_cut
                    and nonimpact_proof.evaluation_refs
                ):
                    add_path(
                        target_ref=node.node_id,
                        state=ImpactState.UNAFFECTED_PROVEN,
                        evaluation_refs=nonimpact_proof.evaluation_refs,
                        origin="bounded_non_impact",
                    )
                else:
                    add_path(
                        target_ref=node.node_id,
                        state=ImpactState.UNKNOWN,
                        origin="gap",
                        reason_codes=(
                            "CANDIDATE_INPUT_NOT_AUTHORITY"
                            if node.admission_status is not AdmissionStatus.ADMITTED
                            else "GRAPH_COVERAGE_PARTIAL",
                        ),
                    )
            else:
                if nonimpact_proof is None:
                    add_path(
                        target_ref=node.node_id,
                        state=ImpactState.UNKNOWN,
                        origin="gap",
                        reason_codes=("GRAPH_COVERAGE_PARTIAL",),
                    )
                else:
                    add_path(
                        target_ref=node.node_id,
                        state=ImpactState.UNAFFECTED_PROVEN,
                        evaluation_refs=nonimpact_proof.evaluation_refs,
                        origin="bounded_non_impact",
                    )

    paths = tuple(sorted(raw_paths.values(), key=lambda item: item.path_id))
    obligations = _derive_obligations(
        snapshot,
        change,
        paths,
        seed_path.path_id,
        evaluations,
    )
    orders = _derive_orders(snapshot, paths, obligations)
    root_unknown = (
        status_delta.after.state != "known"
        or subject.admission_status
        in {AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED}
    )
    referenced_evaluation_refs = {
        evaluation_ref for path in paths for evaluation_ref in path.evaluation_refs
    }
    unresolved = {
        path.path_id for path in paths if path.state is ImpactState.UNKNOWN
    } | {
        evaluation.evaluation_id
        for evaluation in evaluations
        if evaluation.result is ApplicabilityResult.UNKNOWN
        and evaluation.evaluation_id in referenced_evaluation_refs
    }
    if root_unknown:
        unresolved.add(change.metadata.id)
    assert snapshot.digest is not None
    assert change.digest is not None
    return DerivedContract(
        snapshot_digest=snapshot.digest,
        change_digest=change.digest,
        applicability_evaluations=evaluations,
        impact_paths=paths,
        obligations=obligations,
        required_orders=orders,
        unresolved_refs=tuple(sorted(unresolved)),
        root_applicability_unknown=root_unknown,
        profile_binding=profile.binding,
    )


def _evaluate_snapshot_applicability(
    snapshot: OrganizationSnapshot, change: SemanticChangeSet, *, delta_path: str = "/status"
) -> tuple[ApplicabilityEvaluation, ...]:
    evaluations: list[ApplicabilityEvaluation] = []
    for edge in snapshot.spec.dependency_edges:
        evaluations.append(
            evaluate_applicability(
                snapshot,
                change,
                edge.transfer_predicate,
                source_ref=edge.edge_id,
                delta_path=delta_path,
                relation_type=edge.relation_type,
            )
        )
    for rule in snapshot.spec.impact_rules:
        evaluations.append(
            evaluate_applicability(
                snapshot,
                change,
                rule,
                source_ref=rule.rule_id,
                delta_path=delta_path,
            )
        )
    for duty in snapshot.spec.unknown_transition_duties:
        evaluations.append(
            evaluate_applicability(
                snapshot,
                change,
                duty,
                source_ref=duty.duty_id,
                delta_path=delta_path,
            )
        )
    return tuple(sorted(evaluations, key=lambda item: item.evaluation_id))


def _has_continuable_edge(
    source_ref: str,
    node_path: tuple[str, ...],
    outgoing: dict[str, list[DependencyEdge]],
    evaluation_by_source: dict[str, ApplicabilityEvaluation],
    node_by_id: dict[str, OrganizationNode],
) -> bool:
    return any(
        edge.admission_status is not AdmissionStatus.RETRACTED
        and node_by_id[edge.source_ref].admission_status
        is not AdmissionStatus.RETRACTED
        and node_by_id[edge.target_ref].admission_status
        is not AdmissionStatus.RETRACTED
        and edge.target_ref not in node_path
        and evaluation_by_source[edge.edge_id].result
        in {ApplicabilityResult.TRUE, ApplicabilityResult.UNKNOWN}
        for edge in outgoing.get(source_ref, ())
    )


def _truncated_continuation_targets(
    frontiers: set[tuple[str, tuple[str, ...]]],
    outgoing: dict[str, list[DependencyEdge]],
    evaluation_by_source: dict[str, ApplicabilityEvaluation],
    node_by_id: dict[str, OrganizationNode],
) -> frozenset[str]:
    """Return nodes conservatively reachable beyond a max-depth frontier.

    Each frontier is evaluated against its own prefix. Removing that prefix and
    using ordinary reachability is equivalent to asking whether a simple
    continuation exists, without enumerating every cycle permutation.
    """

    reachable: set[str] = set()
    for frontier_ref, node_path in sorted(frontiers):
        forbidden = set(node_path[:-1])
        visited = {frontier_ref}
        pending = deque([frontier_ref])
        while pending:
            source_ref = pending.popleft()
            for edge in outgoing.get(source_ref, ()):
                if edge.admission_status is AdmissionStatus.RETRACTED:
                    continue
                if (
                    node_by_id[edge.source_ref].admission_status
                    is AdmissionStatus.RETRACTED
                    or node_by_id[edge.target_ref].admission_status
                    is AdmissionStatus.RETRACTED
                ):
                    continue
                if (
                    evaluation_by_source[edge.edge_id].result
                    is ApplicabilityResult.FALSE
                ):
                    continue
                if edge.target_ref in forbidden or edge.target_ref in visited:
                    continue
                visited.add(edge.target_ref)
                reachable.add(edge.target_ref)
                pending.append(edge.target_ref)
    return frozenset(reachable)


def _bounded_nonimpact_proof(
    snapshot: OrganizationSnapshot,
    root_ref: str,
    target_ref: str,
    outgoing: dict[str, list[DependencyEdge]],
    evaluation_by_source: dict[str, ApplicabilityEvaluation],
    node_by_id: dict[str, OrganizationNode],
) -> _NonImpactProof | None:
    """Prove non-impact and bind the FALSE cut that closes every known route."""

    root_is_admitted = (
        root_ref in node_by_id
        and node_by_id[root_ref].admission_status is AdmissionStatus.ADMITTED
    )
    all_false_edges = {
        edge.edge_id
        for edges in outgoing.values()
        for edge in edges
        if evaluation_by_source[edge.edge_id].result is ApplicabilityResult.FALSE
    }
    authoritative_prefix = _authoritative_true_prefix_reachable(
        root_ref,
        outgoing,
        evaluation_by_source,
        node_by_id,
    )
    admitted_false_edges = {
        edge.edge_id
        for edges in outgoing.values()
        for edge in edges
        if edge.admission_status is AdmissionStatus.ADMITTED
        and edge.source_ref in authoritative_prefix
        and node_by_id[edge.source_ref].admission_status
        is AdmissionStatus.ADMITTED
        and node_by_id[edge.target_ref].admission_status
        is not AdmissionStatus.RETRACTED
        and evaluation_by_source[edge.edge_id].result is ApplicabilityResult.FALSE
    }
    topology_reachable = _topology_reachable(
        root_ref, outgoing, node_by_id, blocked_edge_refs=frozenset()
    )
    has_graph_route = target_ref in topology_reachable
    graph_authoritative_cut = True
    semantic_false_evaluation_refs: set[str] = set()
    authoritative_false_evaluation_refs: set[str] = set()
    if has_graph_route:
        reachable_without_false = _topology_reachable(
            root_ref,
            outgoing,
            node_by_id,
            blocked_edge_refs=frozenset(all_false_edges),
        )
        if target_ref in reachable_without_false:
            return None
        reachable_without_admitted_false = _topology_reachable(
            root_ref,
            outgoing,
            node_by_id,
            blocked_edge_refs=frozenset(admitted_false_edges),
        )
        graph_authoritative_cut = target_ref not in reachable_without_admitted_false
        reverse_reachable = _topology_reverse_reachable(
            target_ref, outgoing, node_by_id
        )
        semantic_false_evaluation_refs.update(
            evaluation_by_source[edge.edge_id].evaluation_id
            for edges in outgoing.values()
            for edge in edges
            if edge.edge_id in all_false_edges
            and edge.source_ref in topology_reachable
            and edge.target_ref in reverse_reachable
        )
        authoritative_false_evaluation_refs.update(
            evaluation_by_source[edge.edge_id].evaluation_id
            for edges in outgoing.values()
            for edge in edges
            if edge.edge_id in admitted_false_edges
            and edge.source_ref in topology_reachable
            and edge.target_ref in reverse_reachable
        )

    direct_sources = [
        (rule.rule_id, rule.admission_status)
        for rule in snapshot.spec.impact_rules
        if rule.target_ref == target_ref
        and rule.admission_status is not AdmissionStatus.RETRACTED
    ]
    direct_sources.extend(
        (duty.duty_id, duty.admission_status)
        for duty in snapshot.spec.unknown_transition_duties
        if duty.target_ref == target_ref
        and duty.admission_status is not AdmissionStatus.RETRACTED
        and evaluation_by_source[duty.duty_id].result is ApplicabilityResult.FALSE
    )
    if any(
        evaluation_by_source[source_ref].result is not ApplicabilityResult.FALSE
        for source_ref, _ in direct_sources
    ):
        return None
    semantic_false_evaluation_refs.update(
        evaluation_by_source[source_ref].evaluation_id
        for source_ref, _ in direct_sources
    )
    authoritative_false_evaluation_refs.update(
        evaluation_by_source[source_ref].evaluation_id
        for source_ref, admission_status in direct_sources
        if root_is_admitted and admission_status is AdmissionStatus.ADMITTED
    )
    direct_authoritative_cut = root_is_admitted and all(
        admission_status is AdmissionStatus.ADMITTED for _, admission_status in direct_sources
    )
    has_route = has_graph_route or bool(direct_sources)
    authoritative_cut = graph_authoritative_cut and direct_authoritative_cut
    target_requires_authority = (
        node_by_id[target_ref].admission_status is not AdmissionStatus.ADMITTED
        or target_ref not in snapshot.spec.completeness.covered_node_refs
    )
    return _NonImpactProof(
        evaluation_refs=tuple(
            sorted(
                authoritative_false_evaluation_refs
                if target_requires_authority
                else semantic_false_evaluation_refs
            )
        ),
        has_route=has_route,
        authoritative_cut=authoritative_cut,
    )


def _authoritative_true_prefix_reachable(
    root_ref: str,
    outgoing: dict[str, list[DependencyEdge]],
    evaluation_by_source: dict[str, ApplicabilityEvaluation],
    node_by_id: dict[str, OrganizationNode],
) -> set[str]:
    """Reach nodes through admitted TRUE edges without crossing an authority gap."""

    if (
        root_ref not in node_by_id
        or node_by_id[root_ref].admission_status is not AdmissionStatus.ADMITTED
    ):
        return set()
    reachable = {root_ref}
    pending = deque([root_ref])
    while pending:
        source_ref = pending.popleft()
        for edge in outgoing.get(source_ref, ()):
            if (
                edge.admission_status is not AdmissionStatus.ADMITTED
                or evaluation_by_source[edge.edge_id].result
                is not ApplicabilityResult.TRUE
                or node_by_id[edge.source_ref].admission_status
                is not AdmissionStatus.ADMITTED
                or node_by_id[edge.target_ref].admission_status
                is not AdmissionStatus.ADMITTED
                or edge.target_ref in reachable
            ):
                continue
            reachable.add(edge.target_ref)
            pending.append(edge.target_ref)
    return reachable


def _topology_reachable(
    root_ref: str,
    outgoing: dict[str, list[DependencyEdge]],
    node_by_id: dict[str, OrganizationNode],
    *,
    blocked_edge_refs: frozenset[str],
) -> set[str]:
    if (
        root_ref not in node_by_id
        or node_by_id[root_ref].admission_status is AdmissionStatus.RETRACTED
    ):
        return set()
    reachable = {root_ref}
    pending = deque([root_ref])
    while pending:
        source_ref = pending.popleft()
        for edge in outgoing.get(source_ref, ()):
            if edge.edge_id in blocked_edge_refs:
                continue
            if (
                node_by_id[edge.source_ref].admission_status
                is AdmissionStatus.RETRACTED
                or node_by_id[edge.target_ref].admission_status
                is AdmissionStatus.RETRACTED
                or edge.target_ref in reachable
            ):
                continue
            reachable.add(edge.target_ref)
            pending.append(edge.target_ref)
    return reachable


def _topology_reverse_reachable(
    target_ref: str,
    outgoing: dict[str, list[DependencyEdge]],
    node_by_id: dict[str, OrganizationNode],
) -> set[str]:
    incoming: dict[str, list[DependencyEdge]] = defaultdict(list)
    for edges in outgoing.values():
        for edge in edges:
            if (
                node_by_id[edge.source_ref].admission_status
                is not AdmissionStatus.RETRACTED
                and node_by_id[edge.target_ref].admission_status
                is not AdmissionStatus.RETRACTED
            ):
                incoming[edge.target_ref].append(edge)
    reachable = {target_ref}
    pending = deque([target_ref])
    while pending:
        target = pending.popleft()
        for edge in incoming.get(target, ()):
            if edge.source_ref in reachable:
                continue
            reachable.add(edge.source_ref)
            pending.append(edge.source_ref)
    return reachable


def _derive_obligations(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    paths: tuple[ImpactPath, ...],
    seed_path_id: str,
    evaluations: tuple[ApplicabilityEvaluation, ...],
) -> tuple[CoverageObligation, ...]:
    node_by_id = {node.node_id: node for node in snapshot.spec.nodes}
    role_by_id = {role.role_id: role for role in snapshot.spec.role_definitions}
    rule_by_id = {rule.rule_id: rule for rule in snapshot.spec.impact_rules}
    duty_by_id = {
        duty.duty_id: duty for duty in snapshot.spec.unknown_transition_duties
    }
    grouped: dict[_ObligationKey, _ObligationGroup] = {}

    def add(
        *,
        origin: _ObligationOrigin,
        target_ref: str,
        role_ref: str,
        obligation_type: str,
        evidence: tuple[str, ...],
        path_ref: str,
        state: _ObligationState,
    ) -> None:
        role = role_by_id[role_ref]
        key: _ObligationKey = (origin, target_ref, role_ref, obligation_type, state)
        current = grouped.setdefault(
            key,
            {"domain": role.domain_ref, "evidence": set(), "paths": set()},
        )
        current["evidence"].update(evidence)
        current["paths"].add(path_ref)

    completeness = snapshot.spec.completeness
    discovery_target = completeness.discovery_target_ref
    discovery_type = completeness.discovery_obligation_type or "discover_dependency"
    discovery_evidence = completeness.discovery_evidence or (
        "dependency_admission_decision",
    )
    explicit_duties_by_target: dict[str, list[UnknownTransitionDuty]] = defaultdict(list)
    for path in paths:
        for duty_ref in path.duty_refs:
            duty = duty_by_id[duty_ref]
            explicit_duties_by_target[duty.target_ref].append(duty)

    for path in paths:
        if path.state is ImpactState.AFFECTED:
            if path.rule_refs:
                for rule_ref in path.rule_refs:
                    rule = rule_by_id.get(rule_ref)
                    if rule is not None and rule.admission_status is AdmissionStatus.ADMITTED:
                        add(
                            origin="organizational_rule",
                            target_ref=path.target_ref,
                            role_ref=rule.required_role_ref,
                            obligation_type=rule.obligation_type,
                            evidence=rule.required_evidence,
                            path_ref=path.path_id,
                            state="affected",
                        )
            elif path.path_id != seed_path_id:
                node = node_by_id[path.target_ref]
                owner_role = (
                    role_by_id.get(node.owner_role_ref)
                    if node.owner_role_ref is not None
                    else None
                )
                if (
                    owner_role is None
                    or owner_role.admission_status is not AdmissionStatus.ADMITTED
                ):
                    raise ProfileError(
                        "OBLIGATION_UNSATISFIED",
                        "affected dependency target lacks an admitted owner role: "
                        f"{path.target_ref}",
                    )
                add(
                    origin="dependency_path",
                    target_ref=path.target_ref,
                    role_ref=owner_role.role_id,
                    obligation_type="assess_dependency_impact",
                    evidence=("impact_assessment",),
                    path_ref=path.path_id,
                    state="affected",
                )
        elif path.state is ImpactState.UNKNOWN:
            covering_duties = explicit_duties_by_target.get(path.target_ref, ())
            if covering_duties:
                for duty in covering_duties:
                    add(
                        origin="organizational_rule",
                        target_ref=duty.target_ref,
                        role_ref=duty.required_role_ref,
                        obligation_type=duty.obligation_type,
                        evidence=duty.required_evidence,
                        path_ref=path.path_id,
                        state="unknown",
                    )
                # An admitted explicit UnknownTransitionDuty covers every
                # Unknown path at the same declared target boundary. This both
                # aggregates its witnesses and suppresses a second generic duty.
                continue
            add(
                origin="discovery_gap",
                target_ref=discovery_target or path.target_ref,
                role_ref=completeness.discovery_role_ref,
                obligation_type=discovery_type,
                evidence=discovery_evidence,
                path_ref=path.path_id,
                state="unknown",
            )

    obligations: list[CoverageObligation] = []
    for key, data in sorted(grouped.items()):
        origin, target, role, obligation_type, state = key
        path_refs = tuple(sorted(data["paths"]))
        obligation_id = coverage_obligation_identifier(
            snapshot_digest=snapshot.digest,
            change_digest=change.digest,
            origin=origin,
            target_ref=target,
            required_role_ref=role,
            obligation_type=obligation_type,
            resolution_state=state,
            path_refs=path_refs,
        )
        obligations.append(
            CoverageObligation(
                obligationId=obligation_id,
                origin=origin,
                targetRef=target,
                domainRef=data["domain"],
                requiredRoleRef=role,
                obligationType=obligation_type,
                requiredEvidence=tuple(sorted(data["evidence"])),
                pathRefs=path_refs,
                resolutionState=state,
            )
        )
    ordered = tuple(sorted(obligations, key=lambda item: item.obligation_id))
    seed_path = next(path for path in paths if path.path_id == seed_path_id)
    evaluation_result_by_id = {
        evaluation.evaluation_id: evaluation.result for evaluation in evaluations
    }
    has_bounded_nonimpact_closure = any(
        path.origin == "bounded_non_impact"
        and path.evaluation_refs
        and all(
            evaluation_result_by_id.get(evaluation_ref) is ApplicabilityResult.FALSE
            for evaluation_ref in path.evaluation_refs
        )
        for path in paths
    )
    if (
        seed_path.state is ImpactState.AFFECTED
        and not ordered
        and not has_bounded_nonimpact_closure
    ):
        raise ProfileError(
            "OBLIGATION_UNSATISFIED",
            "affected change closure materialized no obligation",
        )
    return ordered


def _derive_orders(
    snapshot: OrganizationSnapshot,
    paths: tuple[ImpactPath, ...],
    obligations: tuple[CoverageObligation, ...],
) -> tuple[RequiredOrder, ...]:
    edge_by_id = {edge.edge_id: edge for edge in snapshot.spec.dependency_edges}
    node_by_id = {node.node_id: node for node in snapshot.spec.nodes}
    rule_by_id = {rule.rule_id: rule for rule in snapshot.spec.impact_rules}
    duty_by_id = {
        duty.duty_id: duty for duty in snapshot.spec.unknown_transition_duties
    }
    required_roles = {obligation.required_role_ref for obligation in obligations}
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    role_wide_pairs: set[tuple[str, str]] = set()
    dependency_reasons: dict[tuple[str, str], set[str]] = defaultdict(set)
    prerequisite_reason_groups: dict[
        tuple[str, str], set[tuple[str, ...]]
    ] = defaultdict(set)

    # Preserve the legacy admitted dependency order, but never infer ordering
    # from an UNKNOWN path. Unknown duties carry explicit prerequisites.
    for path in paths:
        if path.state is not ImpactState.AFFECTED or not path.edge_refs:
            continue
        for edge_ref in path.edge_refs:
            edge = edge_by_id[edge_ref]
            source_role = node_by_id[edge.source_ref].owner_role_ref
            target_role = node_by_id[edge.target_ref].owner_role_ref
            if (
                source_role in required_roles
                and target_role in required_roles
                and source_role != target_role
            ):
                role_pair = (source_role, target_role)
                pairs[role_pair].add(path.path_id)
                role_wide_pairs.add(role_pair)
                dependency_reasons[role_pair].add(path.path_id)

    obligations_by_type: dict[str, list[CoverageObligation]] = defaultdict(list)
    obligations_by_path: dict[str, list[CoverageObligation]] = defaultdict(list)
    for obligation in obligations:
        obligations_by_type[obligation.obligation_type].append(obligation)
        for path_ref in obligation.path_refs:
            obligations_by_path[path_ref].append(obligation)

    for path in paths:
        sources: list[ContextualImpactRule | UnknownTransitionDuty] = []
        sources.extend(
            rule
            for rule_ref in path.rule_refs
            if isinstance((rule := rule_by_id.get(rule_ref)), ContextualImpactRule)
        )
        sources.extend(
            duty_by_id[duty_ref]
            for duty_ref in path.duty_refs
            if duty_ref in duty_by_id
        )
        for source in sources:
            if not source.prerequisite_obligation_types:
                continue
            successor_obligations = [
                obligation
                for obligation in obligations_by_path.get(path.path_id, ())
                if obligation.obligation_type == source.obligation_type
            ]
            if not successor_obligations:
                raise ProfileError(
                    "PREREQUISITE_OBLIGATION_ORDER_MISSING",
                    f"no successor obligation materialized for {source.obligation_type}",
                )
            for prerequisite_type in source.prerequisite_obligation_types:
                predecessor_obligations = obligations_by_type.get(prerequisite_type, ())
                if len(predecessor_obligations) != 1:
                    detail = "missing" if not predecessor_obligations else "ambiguous"
                    raise ProfileError(
                        "PREREQUISITE_OBLIGATION_ORDER_MISSING",
                        f"{detail} prerequisite obligation type: {prerequisite_type}",
                    )
                predecessor = predecessor_obligations[0]
                for successor in successor_obligations:
                    if predecessor.required_role_ref == successor.required_role_ref:
                        raise ProfileError(
                            "PREREQUISITE_OBLIGATION_ORDER_MISSING",
                            "same-role prerequisite cannot be represented by "
                            "one WorkUnit per role",
                        )
                    role_pair = (
                        predecessor.required_role_ref,
                        successor.required_role_ref,
                    )
                    reason_group = tuple(
                        sorted(
                            {
                                path.path_id,
                                predecessor.obligation_id,
                                successor.obligation_id,
                            }
                        )
                    )
                    pairs[role_pair].update(reason_group)
                    prerequisite_reason_groups[role_pair].add(reason_group)
    return tuple(
        RequiredOrder(
            left,
            right,
            tuple(sorted(reason_refs)),
            role_wide=(left, right) in role_wide_pairs,
            dependency_reason_refs=tuple(
                sorted(dependency_reasons.get((left, right), ()))
            ),
            prerequisite_reason_groups=tuple(
                sorted(prerequisite_reason_groups.get((left, right), ()))
            ),
        )
        for (left, right), reason_refs in sorted(pairs.items())
    )
