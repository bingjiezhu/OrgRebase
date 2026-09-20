"""Pure, total contextual-applicability evaluation.

The evaluator consumes only frozen OAC source values.  It never reads benchmark
cases, annotations, expected outputs, environment state, or model responses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .identifiers import evaluation_identifier
from .models import (
    LEGACY_PREDICATE_VERSION,
    AdmissionStatus,
    ApplicabilityEvaluation,
    ApplicabilityResult,
    ContextualApplicabilityPredicate,
    ContextualImpactRule,
    ImpactRule,
    OrganizationSnapshot,
    RelationType,
    ScopeSelector,
    SemanticChangeSet,
    SubjectSelector,
    TransferPredicate,
    UnknownTransitionDuty,
)
from .registry import PREDICATE_VERSION_REGISTRY
from .witness import format_witness_ref

type ApplicabilitySource = (
    TransferPredicate
    | ContextualApplicabilityPredicate
    | ImpactRule
    | ContextualImpactRule
    | UnknownTransitionDuty
)


@dataclass(frozen=True, slots=True)
class NormalizedApplicability:
    """In-memory normal form; it is never written back into a legacy resource."""

    predicate_version: str
    semantic_type: str
    after_state: str
    after_values: tuple[str, ...]
    subject_selector: SubjectSelector | None
    scope_selector: ScopeSelector | None
    relation_types: tuple[RelationType, ...]


@dataclass(frozen=True, slots=True)
class _Atom:
    result: ApplicabilityResult
    witnesses: tuple[str, ...]
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class _SourceContext:
    applicability: ApplicabilitySource | None
    admission_status: AdmissionStatus | None
    predicate_witness: str
    status_witness: str
    relation_type: RelationType | None
    relation_witness: str


def strong_kleene_all(values: Iterable[ApplicabilityResult]) -> ApplicabilityResult:
    """Return strong-Kleene conjunction; FALSE dominates UNKNOWN."""

    materialized = tuple(values)
    if ApplicabilityResult.FALSE in materialized:
        return ApplicabilityResult.FALSE
    if ApplicabilityResult.UNKNOWN in materialized:
        return ApplicabilityResult.UNKNOWN
    return ApplicabilityResult.TRUE


def strong_kleene_any(values: Iterable[ApplicabilityResult]) -> ApplicabilityResult:
    """Return strong-Kleene disjunction; TRUE dominates UNKNOWN."""

    materialized = tuple(values)
    if ApplicabilityResult.TRUE in materialized:
        return ApplicabilityResult.TRUE
    if ApplicabilityResult.UNKNOWN in materialized:
        return ApplicabilityResult.UNKNOWN
    return ApplicabilityResult.FALSE


def normalized_applicability(source: ApplicabilitySource) -> NormalizedApplicability:
    """Normalize legacy and contextual wire shapes without mutating source bytes."""

    if isinstance(source, (ContextualImpactRule, UnknownTransitionDuty)):
        source = source.applicability
    if isinstance(source, ContextualApplicabilityPredicate):
        return NormalizedApplicability(
            predicate_version=source.predicate_version,
            semantic_type=source.semantic_type,
            after_state=source.after_state,
            after_values=source.after_values,
            subject_selector=source.subject_selector,
            scope_selector=source.scope_selector,
            relation_types=source.relation_types,
        )
    if isinstance(source, ImpactRule):
        return NormalizedApplicability(
            predicate_version=LEGACY_PREDICATE_VERSION,
            semantic_type=source.semantic_type,
            after_state="known",
            after_values=source.after_values,
            subject_selector=None,
            scope_selector=None,
            relation_types=(),
        )
    if isinstance(source, TransferPredicate):
        return NormalizedApplicability(
            predicate_version=LEGACY_PREDICATE_VERSION,
            semantic_type=source.semantic_type,
            after_state="known",
            after_values=source.after_values,
            subject_selector=None,
            scope_selector=None,
            relation_types=(),
        )
    raise TypeError(f"unsupported applicability source: {type(source).__name__}")


def evaluate_applicability(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    applicability: ApplicabilitySource,
    *,
    source_ref: str,
    relation_type: RelationType | None = None,
    delta_path: str = "/status",
) -> ApplicabilityEvaluation:
    """Evaluate one Snapshot-owned predicate and return its exact evidence ledger fact."""

    normalized = normalized_applicability(applicability)
    source = _find_source_context(snapshot, source_ref)
    atoms: list[_Atom] = [
        _digest_atom(snapshot.metadata.id, snapshot.digest),
        _digest_atom(change.metadata.id, change.digest),
        _source_atom(source),
        _change_authority_atom(change),
    ]

    source_matches = False
    if source.applicability is not None:
        actual = normalized_applicability(source.applicability)
        source_matches = actual == normalized
        atoms.append(
            _Atom(
                ApplicabilityResult.TRUE
                if source_matches
                else ApplicabilityResult.UNKNOWN,
                (source.predicate_witness,),
                None if source_matches else "APPLICABILITY_SOURCE_MISMATCH",
            )
        )

    if normalized.predicate_version not in PREDICATE_VERSION_REGISTRY:
        atoms.append(
            _Atom(
                ApplicabilityResult.UNKNOWN,
                (source.predicate_witness,),
                "PREDICATE_VERSION_UNSUPPORTED",
            )
        )
    elif source_matches and _definition_is_invalid(
        source.applicability, normalized, source
    ):
        atoms.append(
            _Atom(
                ApplicabilityResult.UNKNOWN,
                (source.predicate_witness,),
                "PREDICATE_DEFINITION_INVALID",
            )
        )
    elif source_matches:
        atoms.extend(
            _evaluate_change_atoms(
                snapshot,
                change,
                normalized,
                source.relation_type if source.relation_type is not None else relation_type,
                source.relation_witness,
                delta_path,
            )
        )

    result = strong_kleene_all(atom.result for atom in atoms)
    reason_codes = tuple(
        sorted({atom.reason_code for atom in atoms if atom.reason_code is not None})
    )
    witness_refs = tuple(
        sorted({witness for atom in atoms for witness in atom.witnesses})
    )
    evaluation_id = _evaluation_id(
        snapshot_digest=snapshot.digest,
        change_digest=change.digest,
        change_subject_ref=change.spec.subject_ref,
        source_ref=source_ref,
        predicate_version=normalized.predicate_version,
        result=result,
        reason_codes=reason_codes,
        witness_refs=witness_refs,
    )
    return ApplicabilityEvaluation(
        evaluationId=evaluation_id,
        sourceRef=source_ref,
        predicateVersion=normalized.predicate_version,
        result=result,
        reasonCodes=reason_codes,
        witnessRefs=witness_refs,
    )


def _definition_is_invalid(
    source: ApplicabilitySource | None,
    predicate: NormalizedApplicability,
    context: _SourceContext,
) -> bool:
    """Enforce registered wire/version and source-context definition bounds."""

    if source is None:
        return True
    registry_entry = PREDICATE_VERSION_REGISTRY[predicate.predicate_version]
    contextual = isinstance(
        source,
        (
            ContextualApplicabilityPredicate,
            ContextualImpactRule,
            UnknownTransitionDuty,
        ),
    )
    wire_type = (
        "ContextualApplicabilityPredicate" if contextual else "TransferPredicate"
    )
    if registry_entry.wire_type != wire_type:
        return True
    if predicate.after_state != "known" and predicate.after_values:
        return True
    # relationTypes is meaningful only on a DependencyEdge, whose registered
    # source context owns an exact relationType. Rules and duties have none.
    return bool(predicate.relation_types and context.relation_type is None)


def _evaluate_change_atoms(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    predicate: NormalizedApplicability,
    relation_type: RelationType | None,
    relation_witness: str,
    delta_path: str,
) -> tuple[_Atom, ...]:
    change_id = change.metadata.id
    semantic_witness = _witness(change_id, "/spec/semanticType")
    atoms = [
        _Atom(
            ApplicabilityResult.TRUE
            if change.spec.semantic_type == predicate.semantic_type
            else ApplicabilityResult.FALSE,
            (semantic_witness,),
            None
            if change.spec.semantic_type == predicate.semantic_type
            else "APPLICABILITY_SEMANTIC_TYPE_MISMATCH",
        )
    ]

    delta_matches = [
        (index, delta)
        for index, delta in enumerate(change.spec.deltas)
        if delta.path == delta_path
    ]
    if len(delta_matches) != 1:
        atoms.append(
            _Atom(
                ApplicabilityResult.UNKNOWN,
                (_witness(change_id, "/spec/deltas"),),
                "APPLICABILITY_INPUT_MISSING",
            )
        )
    else:
        delta_index, delta = delta_matches[0]
        state_witness = _witness(change_id, f"/spec/deltas/{delta_index}/after/state")
        value_witness = _witness(change_id, f"/spec/deltas/{delta_index}/after/value")
        if delta.after.state == predicate.after_state:
            state_result = ApplicabilityResult.TRUE
            state_reason = None
        elif delta.after.state != "known" and predicate.after_state == "known":
            state_result = ApplicabilityResult.UNKNOWN
            state_reason = "APPLICABILITY_INPUT_UNKNOWN"
        else:
            state_result = ApplicabilityResult.FALSE
            state_reason = "APPLICABILITY_AFTER_STATE_MISMATCH"
        atoms.append(_Atom(state_result, (state_witness,), state_reason))

        if predicate.after_values:
            if delta.after.state != "known":
                atoms.append(
                    _Atom(
                        ApplicabilityResult.UNKNOWN,
                        (state_witness,),
                        "APPLICABILITY_INPUT_UNKNOWN",
                    )
                )
            elif delta.after.value == "unknown":
                atoms.append(
                    _Atom(
                        ApplicabilityResult.UNKNOWN,
                        (value_witness,),
                        "SYNTHETIC_UNKNOWN_VALUE",
                    )
                )
            else:
                value_matches = (
                    isinstance(delta.after.value, str)
                    and delta.after.value in predicate.after_values
                )
                atoms.append(
                    _Atom(
                        ApplicabilityResult.TRUE
                        if value_matches
                        else ApplicabilityResult.FALSE,
                        (value_witness,),
                        None
                        if value_matches
                        else "APPLICABILITY_AFTER_VALUE_MISMATCH",
                    )
                )

    if predicate.subject_selector is not None:
        atoms.extend(_subject_atoms(snapshot, change, predicate.subject_selector))
    if predicate.scope_selector is not None:
        atoms.append(_scope_atom(snapshot, change, predicate.scope_selector))
    if predicate.relation_types:
        if relation_type is None:
            atoms.append(
                _Atom(
                    ApplicabilityResult.UNKNOWN,
                    (relation_witness,),
                    "APPLICABILITY_INPUT_MISSING",
                )
            )
        else:
            matches = relation_type in predicate.relation_types
            atoms.append(
                _Atom(
                    ApplicabilityResult.TRUE if matches else ApplicabilityResult.FALSE,
                    (relation_witness,),
                    None if matches else "APPLICABILITY_RELATION_TYPE_MISMATCH",
                )
            )
    return tuple(atoms)


def _subject_atoms(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    selector: SubjectSelector,
) -> tuple[_Atom, ...]:
    subject_ref = change.spec.subject_ref
    change_witness = _witness(change.metadata.id, "/spec/subjectRef")
    atoms: list[_Atom] = []
    if selector.subject_refs:
        matches = subject_ref in selector.subject_refs
        atoms.append(
            _Atom(
                ApplicabilityResult.TRUE if matches else ApplicabilityResult.FALSE,
                (change_witness,),
                None if matches else "APPLICABILITY_SUBJECT_MISMATCH",
            )
        )

    node_match = next(
        (
            (index, node)
            for index, node in enumerate(snapshot.spec.nodes)
            if node.node_id == subject_ref
        ),
        None,
    )
    needs_node = bool(selector.node_types or selector.domain_refs or selector.subject_refs)
    if not needs_node:
        return tuple(atoms)
    if node_match is None:
        return (
            *atoms,
            _Atom(
                ApplicabilityResult.UNKNOWN,
                (change_witness, _witness(snapshot.metadata.id, "/spec/nodes")),
                "APPLICABILITY_INPUT_MISSING",
            ),
        )

    node_index, node = node_match
    status_witness = _witness(
        snapshot.metadata.id, f"/spec/nodes/{node_index}/admissionStatus"
    )
    if node.admission_status in {AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED}:
        atoms.append(
            _Atom(
                ApplicabilityResult.UNKNOWN,
                (status_witness,),
                "CANDIDATE_INPUT_NOT_AUTHORITY",
            )
        )
        return tuple(atoms)
    elif node.admission_status is AdmissionStatus.RETRACTED:
        atoms.append(
            _Atom(
                ApplicabilityResult.FALSE,
                (status_witness,),
                "RETRACTED_SOURCE_EXCLUDED",
            )
        )
        return tuple(atoms)
    else:
        atoms.append(_Atom(ApplicabilityResult.TRUE, (status_witness,)))

    if selector.node_types:
        matches = node.node_type in selector.node_types
        atoms.append(
            _Atom(
                ApplicabilityResult.TRUE if matches else ApplicabilityResult.FALSE,
                (_witness(snapshot.metadata.id, f"/spec/nodes/{node_index}/nodeType"),),
                None if matches else "APPLICABILITY_SUBJECT_MISMATCH",
            )
        )
    if selector.domain_refs:
        matches = node.domain_ref in selector.domain_refs
        atoms.append(
            _Atom(
                ApplicabilityResult.TRUE if matches else ApplicabilityResult.FALSE,
                (_witness(snapshot.metadata.id, f"/spec/nodes/{node_index}/domainRef"),),
                None if matches else "APPLICABILITY_SUBJECT_MISMATCH",
            )
        )
    return tuple(atoms)


def _scope_atom(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    selector: ScopeSelector,
) -> _Atom:
    ref_atoms = tuple(_scope_ref_atom(snapshot, change, selector, ref) for ref in selector.refs)
    if selector.match_mode == "all":
        result = strong_kleene_all(atom.result for atom in ref_atoms)
    else:
        result = strong_kleene_any(atom.result for atom in ref_atoms)
    reasons = sorted(
        {atom.reason_code for atom in ref_atoms if atom.reason_code is not None}
    )
    reason = None
    if result is ApplicabilityResult.FALSE:
        reason = "APPLICABILITY_SCOPE_MISMATCH"
    elif result is ApplicabilityResult.UNKNOWN:
        reason = reasons[0] if reasons else "APPLICABILITY_INPUT_UNKNOWN"
    return _Atom(
        result,
        tuple(sorted({witness for atom in ref_atoms for witness in atom.witnesses})),
        reason,
    )


def _scope_ref_atom(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    selector: ScopeSelector,
    ref: str,
) -> _Atom:
    scope_witness = _witness(change.metadata.id, "/spec/scopeRefs")
    if ref not in change.spec.scope_refs:
        if selector.missing_behavior == "unknown":
            return _Atom(
                ApplicabilityResult.UNKNOWN,
                (scope_witness,),
                "APPLICABILITY_INPUT_MISSING",
            )
        return _Atom(
            ApplicabilityResult.FALSE,
            (scope_witness,),
            "APPLICABILITY_SCOPE_MISMATCH",
        )

    node_match = next(
        (
            (index, node)
            for index, node in enumerate(snapshot.spec.nodes)
            if node.node_id == ref
        ),
        None,
    )
    if node_match is None:
        if selector.missing_behavior == "unknown":
            return _Atom(
                ApplicabilityResult.UNKNOWN,
                (scope_witness, _witness(snapshot.metadata.id, "/spec/nodes")),
                "APPLICABILITY_INPUT_MISSING",
            )
        return _Atom(
            ApplicabilityResult.FALSE,
            (scope_witness, _witness(snapshot.metadata.id, "/spec/nodes")),
            "APPLICABILITY_SCOPE_MISMATCH",
        )

    node_index, node = node_match
    status_witness = _witness(
        snapshot.metadata.id, f"/spec/nodes/{node_index}/admissionStatus"
    )
    witnesses = (scope_witness, status_witness)
    if node.admission_status is AdmissionStatus.ADMITTED:
        return _Atom(ApplicabilityResult.TRUE, witnesses)
    if node.admission_status in {AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED}:
        return _Atom(
            ApplicabilityResult.UNKNOWN,
            witnesses,
            "CANDIDATE_INPUT_NOT_AUTHORITY",
        )
    return _Atom(
        ApplicabilityResult.FALSE,
        witnesses,
        "RETRACTED_SOURCE_EXCLUDED",
    )


def _find_source_context(snapshot: OrganizationSnapshot, source_ref: str) -> _SourceContext:
    snapshot_id = snapshot.metadata.id
    for index, edge in enumerate(snapshot.spec.dependency_edges):
        if edge.edge_id == source_ref:
            base = f"/spec/dependencyEdges/{index}"
            return _SourceContext(
                edge.transfer_predicate,
                edge.admission_status,
                _witness(snapshot_id, f"{base}/transferPredicate"),
                _witness(snapshot_id, f"{base}/admissionStatus"),
                edge.relation_type,
                _witness(snapshot_id, f"{base}/relationType"),
            )
    for index, rule in enumerate(snapshot.spec.impact_rules):
        if rule.rule_id == source_ref:
            base = f"/spec/impactRules/{index}"
            predicate_path = (
                f"{base}/applicability"
                if isinstance(rule, ContextualImpactRule)
                else base
            )
            return _SourceContext(
                rule,
                rule.admission_status,
                _witness(snapshot_id, predicate_path),
                _witness(snapshot_id, f"{base}/admissionStatus"),
                None,
                _witness(snapshot_id, "/spec/dependencyEdges"),
            )
    for index, duty in enumerate(snapshot.spec.unknown_transition_duties):
        if duty.duty_id == source_ref:
            base = f"/spec/unknownTransitionDuties/{index}"
            return _SourceContext(
                duty,
                duty.admission_status,
                _witness(snapshot_id, f"{base}/applicability"),
                _witness(snapshot_id, f"{base}/admissionStatus"),
                None,
                _witness(snapshot_id, "/spec/dependencyEdges"),
            )
    missing = _witness(snapshot_id, "/spec")
    return _SourceContext(
        None,
        None,
        missing,
        missing,
        relation_type=None,
        relation_witness=_witness(snapshot_id, "/spec/dependencyEdges"),
    )


def _source_atom(source: _SourceContext) -> _Atom:
    witnesses = tuple(sorted({source.predicate_witness, source.status_witness}))
    if source.admission_status is None:
        return _Atom(
            ApplicabilityResult.UNKNOWN,
            witnesses,
            "APPLICABILITY_SOURCE_MISSING",
        )
    if source.admission_status is AdmissionStatus.ADMITTED:
        return _Atom(ApplicabilityResult.TRUE, witnesses)
    if source.admission_status in {AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED}:
        return _Atom(
            ApplicabilityResult.UNKNOWN,
            witnesses,
            "CANDIDATE_INPUT_NOT_AUTHORITY",
        )
    return _Atom(
        ApplicabilityResult.FALSE,
        witnesses,
        "RETRACTED_SOURCE_EXCLUDED",
    )


def _change_authority_atom(change: SemanticChangeSet) -> _Atom:
    witness = _witness(change.metadata.id, "/spec/admissionStatus")
    if change.spec.admission_status is AdmissionStatus.ADMITTED:
        return _Atom(ApplicabilityResult.TRUE, (witness,))
    if change.spec.admission_status in {AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED}:
        return _Atom(
            ApplicabilityResult.UNKNOWN,
            (witness,),
            "CHANGE_NOT_ADMITTED",
        )
    return _Atom(
        ApplicabilityResult.FALSE,
        (witness,),
        "RETRACTED_SOURCE_EXCLUDED",
    )


def _digest_atom(resource_id: str, digest: str | None) -> _Atom:
    witness = _witness(resource_id, "/digest")
    if digest is None:
        return _Atom(ApplicabilityResult.UNKNOWN, (witness,), "DIGEST_MISSING")
    return _Atom(ApplicabilityResult.TRUE, (witness,))


def _evaluation_id(
    *,
    snapshot_digest: str | None,
    change_digest: str | None,
    change_subject_ref: str,
    source_ref: str,
    predicate_version: str,
    result: ApplicabilityResult,
    reason_codes: tuple[str, ...],
    witness_refs: tuple[str, ...],
) -> str:
    return evaluation_identifier(
        snapshot_digest=snapshot_digest,
        change_digest=change_digest,
        change_subject_ref=change_subject_ref,
        source_ref=source_ref,
        predicate_version=predicate_version,
        result=result.value,
        reason_codes=reason_codes,
        witness_refs=witness_refs,
    )


def _witness(resource_id: str, pointer: str) -> str:
    return format_witness_ref(resource_id, pointer)
