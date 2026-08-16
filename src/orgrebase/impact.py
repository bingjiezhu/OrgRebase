"""Semantic admission, typed dependency traversal, and bounded non-impact proof."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Protocol

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    ChangeSetRevision,
    CoverageBasis,
    DependencyEdge,
    DependencyManifest,
    DependencyStrength,
    EdgeStatus,
    EvidenceClass,
    ImpactCertificate,
    ImpactCertificateType,
    ImpactClassification,
    ImpactPreview,
    ImpactResult,
    IntegrityError,
    ManifestCompleteness,
    ObjectDelta,
    PathStep,
    RevisionLock,
    SemanticClassification,
)
from orgrebase.fixture import EnterpriseFixture

TRUSTED_COVERAGE = {
    CoverageBasis.RUNTIME_OBSERVED,
    CoverageBasis.OWNER_DECLARED_COMPLETE,
    CoverageBasis.CONTRACT_DECLARED,
    CoverageBasis.IMPORTED_VERIFIED,
}

STRENGTH_RANK = {
    DependencyStrength.INFORMATIONAL: 1,
    DependencyStrength.REVIEW: 2,
    DependencyStrength.HARD: 3,
}

RELATION_CEILING = {
    "ASSUMES": DependencyStrength.HARD,
    "DERIVED_FROM": DependencyStrength.HARD,
    "REQUIRES_CLAIM": DependencyStrength.HARD,
    "REQUIRES_POLICY": DependencyStrength.HARD,
    "MENTIONS": DependencyStrength.INFORMATIONAL,
}

CLASSIFICATION_RANK = {
    ImpactClassification.AFFECTED_INFORMATIONAL: 1,
    ImpactClassification.AFFECTED_REVIEW: 2,
    ImpactClassification.UNKNOWN: 3,
    ImpactClassification.AFFECTED_HARD: 4,
}

_RANK_TO_CLASSIFICATION = {
    3: ImpactClassification.AFFECTED_HARD,
    2: ImpactClassification.AFFECTED_REVIEW,
    1: ImpactClassification.AFFECTED_INFORMATIONAL,
}

_EVALUATED_KINDS = {"WorkItemVersion", "SkillContractVersion"}


class _TransferStep(Protocol):
    relation: str
    strength: DependencyStrength


def classify_semantic_delta(base_value: Any, proposed_value: Any) -> SemanticClassification:
    """Compare canonical business values, deliberately ignoring source wording."""

    if sha256_digest(base_value) == sha256_digest(proposed_value):
        return SemanticClassification.NO_SEMANTIC_DELTA
    return SemanticClassification.SEMANTIC_DELTA


def build_change_set(
    fixture: EnterpriseFixture,
    *,
    proposed_value: Any | None = None,
    source_wording: str | None = None,
) -> ChangeSetRevision:
    change = fixture.change
    base = fixture.object(change["object_id"], change["base_version"])
    proposed = fixture.object(change["object_id"], change["proposed_version"])
    base_value = base.payload["canonical_value"]
    new_value = proposed.payload["canonical_value"] if proposed_value is None else proposed_value
    semantic_classification = classify_semantic_delta(base_value, new_value)
    delta = ObjectDelta(
        object_id=change["object_id"],
        base_version=change["base_version"],
        proposed_version=change["proposed_version"],
        base_value=base_value,
        proposed_value=new_value,
        changed_fields=(() if semantic_classification == "NO_SEMANTIC_DELTA" else ("canonical_value",)),
        semantic_classification=semantic_classification,
        admitted_by=(
            "human:product-owner"
            if semantic_classification == SemanticClassification.SEMANTIC_DELTA
            else None
        ),
    )
    state = (
        "READY_FOR_PREVIEW"
        if semantic_classification == SemanticClassification.SEMANTIC_DELTA
        else "NO_SEMANTIC_DELTA"
    )
    purpose = change["purpose"]
    if source_wording:
        purpose = f"{purpose}; source wording retained only as provenance"
    return ChangeSetRevision(
        id=change["id"],
        revision=change["revision"],
        state=state,
        owner_id=change["owner_id"],
        purpose=purpose,
        scope=tuple(fixture.impact_targets),
        deltas=(delta,),
    )


class ImpactEngine:
    algorithm_version = "impact-max-severity@1.1.0"
    max_depth = 8

    def __init__(self, fixture: EnterpriseFixture) -> None:
        self.fixture = fixture
        self._adjacency: dict[str, list[DependencyEdge]] = defaultdict(list)
        self._eligible_edges: list[DependencyEdge] = []
        for edge in sorted(fixture.dependencies, key=lambda item: item.id):
            if edge.status == EdgeStatus.ADMITTED and edge.coverage_basis in TRUSTED_COVERAGE:
                self._adjacency[edge.source_id].append(edge)
                self._eligible_edges.append(edge)

    def revision_lock(self, change_set: ChangeSetRevision) -> RevisionLock:
        revisions = self.fixture.revisions
        return RevisionLock(
            change_set_revision=f"{change_set.id}@{change_set.revision}",
            change_set_digest=change_set.digest,
            graph_revision=revisions["graph"],
            policy_revision=revisions["policy"],
            skill_registry_revision=revisions["skill_registry"],
            runtime_registry_revision=revisions["runtime_registry"],
            evaluation_scope_digest=sha256_digest(
                {"targets": sorted(change_set.scope), "suite": revisions["evaluation_suite"]}
            ),
        )

    def _manifest_assessment(
        self, target_id: str
    ) -> tuple[DependencyManifest | None, tuple[str, ...]]:
        """Validate completeness as an explicit graph/manifest correspondence.

        A declaration is insufficient on its own. Every trusted admitted inbound
        edge must occupy exactly one requirement slot, and every slot must bind
        back to the same canonical edge fields.
        """

        try:
            manifest = self.fixture.dependency_manifest(target_id)
        except KeyError:
            return None, ("MISSING_DEPENDENCY_MANIFEST",)
        target = self.fixture.object(target_id)
        reasons: set[str] = set()
        if manifest.completeness != ManifestCompleteness.COMPLETE:
            reasons.add(f"MANIFEST_{manifest.completeness.value}")
        if manifest.authority_domain != target.domain:
            reasons.add("MANIFEST_AUTHORITY_DOMAIN_MISMATCH")
        if not manifest.provenance_refs:
            reasons.add("MANIFEST_PROVENANCE_MISSING")

        eligible_inbound = {
            edge.id: edge for edge in self._eligible_edges if edge.target_id == target_id
        }
        declared_ids = {slot.edge_id for slot in manifest.requirement_slots}
        if len(declared_ids) != len(manifest.requirement_slots):
            reasons.add("MANIFEST_DUPLICATE_REQUIREMENT_EDGE")
        if declared_ids != set(eligible_inbound):
            reasons.add("MANIFEST_REQUIREMENT_SET_MISMATCH")
        for slot in manifest.requirement_slots:
            edge = eligible_inbound.get(slot.edge_id)
            if edge is None:
                continue
            if (
                slot.source_id != edge.source_id
                or slot.relation != edge.relation
                or slot.strength != edge.strength
                or slot.coverage_basis != edge.coverage_basis
            ):
                reasons.add("MANIFEST_REQUIREMENT_BINDING_MISMATCH")
            if not slot.provenance_refs:
                reasons.add("MANIFEST_REQUIREMENT_PROVENANCE_MISSING")
        return manifest, tuple(sorted(reasons))

    @classmethod
    def _transfer_classification(cls, steps: tuple[_TransferStep, ...]) -> ImpactClassification:
        if not steps:
            raise ValueError("empty transfer path")
        propagated_rank = STRENGTH_RANK[DependencyStrength.HARD]
        for step in steps:
            ceiling = RELATION_CEILING.get(step.relation)
            if ceiling is None:
                return ImpactClassification.UNKNOWN
            propagated_rank = min(
                propagated_rank,
                STRENGTH_RANK[step.strength],
                STRENGTH_RANK[ceiling],
            )
        return _RANK_TO_CLASSIFICATION[propagated_rank]

    @classmethod
    def _path_rank(
        cls, edges: tuple[DependencyEdge, ...]
    ) -> tuple[int, int, tuple[str, ...]]:
        classification = cls._transfer_classification(edges)
        return (
            -CLASSIFICATION_RANK[classification],
            len(edges),
            tuple(item.id for item in edges),
        )

    def _search(
        self, source_id: str, target_id: str, *, max_depth: int | None = None
    ) -> tuple[tuple[PathStep, ...], bool]:
        """Select the strongest reachable path and whether simple-path search truncated.

        A shortest-path-only BFS can understate risk when a direct informational
        edge coexists with a longer hard dependency. Enumerating simple paths is
        bounded by ``max_depth``. Truncation is true iff some in-budget node had
        an unexplored simple-path expansion, not merely if BFS min-depth hit the
        ceiling. Weak shortcuts must not hide a longer HARD path.
        """

        depth_limit = self.max_depth if max_depth is None else max_depth
        queue: deque[tuple[str, tuple[DependencyEdge, ...], frozenset[str]]] = deque(
            [(source_id, (), frozenset({source_id}))]
        )
        candidates: list[tuple[DependencyEdge, ...]] = []
        truncated = False
        while queue:
            node, edges, visited = queue.popleft()
            outgoing = self._adjacency.get(node, [])
            if len(edges) >= depth_limit:
                if any(edge.target_id not in visited for edge in outgoing):
                    truncated = True
                continue
            for edge in outgoing:
                if edge.target_id in visited:
                    continue
                candidate = (*edges, edge)
                if edge.target_id == target_id:
                    candidates.append(candidate)
                    continue
                queue.append((edge.target_id, candidate, visited | {edge.target_id}))
        if not candidates:
            return (), truncated
        selected = min(candidates, key=self._path_rank)
        return (
            tuple(
                PathStep(
                    edge_id=item.id,
                    source_id=item.source_id,
                    target_id=item.target_id,
                    relation=item.relation,
                    strength=item.strength,
                    coverage_basis=item.coverage_basis,
                    provenance_refs=item.provenance_refs,
                )
                for item in selected
            ),
            truncated,
        )

    def _path(
        self, source_id: str, target_id: str, *, max_depth: int | None = None
    ) -> tuple[PathStep, ...]:
        path, _truncated = self._search(source_id, target_id, max_depth=max_depth)
        return path

    def _reachable_slice(
        self, source_id: str, *, max_depth: int | None = None
    ) -> tuple[tuple[str, ...], tuple[DependencyEdge, ...], tuple[str, ...]]:
        depth_limit = self.max_depth if max_depth is None else max_depth
        queue: deque[tuple[str, int]] = deque([(source_id, 0)])
        visited_depth = {source_id: 0}
        edge_ids: set[str] = set()
        truncated_frontier_edge_ids: set[str] = set()
        while queue:
            node, depth = queue.popleft()
            if depth >= depth_limit:
                truncated_frontier_edge_ids.update(
                    edge.id for edge in self._adjacency.get(node, [])
                )
                continue
            for edge in self._adjacency.get(node, []):
                edge_ids.add(edge.id)
                next_depth = depth + 1
                if next_depth < visited_depth.get(edge.target_id, depth_limit + 1):
                    visited_depth[edge.target_id] = next_depth
                    queue.append((edge.target_id, next_depth))
        edges = tuple(edge for edge in self._eligible_edges if edge.id in edge_ids)
        return (
            tuple(sorted(visited_depth)),
            edges,
            tuple(sorted(truncated_frontier_edge_ids)),
        )

    def _unevaluated_reachable_targets(
        self,
        reachable_nodes: tuple[str, ...],
        scope: tuple[str, ...],
        source_id: str,
    ) -> tuple[str, ...]:
        evaluated = set(scope)
        missing: list[str] = []
        for node_id in reachable_nodes:
            if node_id == source_id or node_id in evaluated:
                continue
            try:
                kind = self.fixture.object(node_id).kind
            except KeyError:
                continue
            if kind in _EVALUATED_KINDS:
                missing.append(node_id)
        return tuple(missing)

    def _traversal_commitment(
        self,
        *,
        source_id: str,
        target_id: str,
        path: tuple[PathStep, ...],
    ) -> dict[str, Any]:
        target = self.fixture.object(target_id)
        reachable_nodes, reachable_edges, truncated_frontier = self._reachable_slice(source_id)
        excluded_edges = []
        for edge in sorted(self.fixture.dependencies, key=lambda item: item.id):
            if edge.status != EdgeStatus.ADMITTED:
                reason = f"STATUS_{edge.status.value}"
            elif edge.coverage_basis not in TRUSTED_COVERAGE:
                reason = f"UNTRUSTED_COVERAGE_{edge.coverage_basis.value}"
            else:
                continue
            excluded_edges.append({"edge_id": edge.id, "reason": reason})
        _, path_truncated = self._search(source_id, target_id)
        selected_classification = self._path_classification(path).value if path else None
        dependency_snapshot = [
            edge.model_dump(mode="json")
            for edge in sorted(self.fixture.dependencies, key=lambda item: item.id)
        ]
        reachable_snapshot = [edge.model_dump(mode="json") for edge in reachable_edges]
        manifest, manifest_reasons = self._manifest_assessment(target_id)
        coverage = {
            "target_ref": target.ref,
            "manifest_ref": manifest.ref if manifest else None,
            "manifest_digest": manifest.digest if manifest else None,
            "manifest_completeness": manifest.completeness.value if manifest else None,
            "requirement_slot_ids": (
                tuple(slot.slot_id for slot in manifest.requirement_slots)
                if manifest
                else ()
            ),
            "assessment_failures": manifest_reasons,
        }
        return {
            "source_id": source_id,
            "target_id": target_id,
            "max_depth": self.max_depth,
            "selection_rule": (
                "strongest_conservative_transfer_then_shortest_then_lexical"
            ),
            "dependency_snapshot_digest": sha256_digest(dependency_snapshot),
            "eligible_edge_ids": tuple(edge.id for edge in self._eligible_edges),
            "excluded_edges": tuple(excluded_edges),
            "reachable_node_ids": reachable_nodes,
            "reachable_edge_ids": tuple(edge.id for edge in reachable_edges),
            "reachable_slice_digest": sha256_digest(reachable_snapshot),
            "truncated_frontier_edge_ids": truncated_frontier,
            "budget_exhausted": bool(truncated_frontier) or path_truncated,
            "selected_path_edge_ids": tuple(step.edge_id for step in path),
            "selected_path_classification": selected_classification,
            "target_coverage": coverage,
            "target_coverage_digest": sha256_digest(coverage),
        }

    def _certificate(
        self,
        *,
        change_set: ChangeSetRevision,
        result: ImpactResult,
        revision_lock: RevisionLock,
    ) -> ImpactCertificate:
        if result.classification == ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY:
            certificate_type = ImpactCertificateType.BOUNDED_NON_IMPACT
            claim_boundary = (
                "No admitted trusted path exists in the committed dependency snapshot and the "
                "target declares complete coverage; this is not a global non-impact claim."
            )
        elif result.classification == ImpactClassification.UNKNOWN:
            certificate_type = ImpactCertificateType.UNCERTAINTY_WITNESS
            claim_boundary = (
                "Coverage is insufficient, so this certificate proves uncertainty only and "
                "must never authorize KEEP_CURRENT."
            )
        else:
            certificate_type = ImpactCertificateType.POSITIVE_WITNESS
            claim_boundary = (
                "An admitted typed witness path exists in the committed dependency snapshot; "
                "causality outside that snapshot is not claimed."
            )
        delta = change_set.deltas[0]
        return ImpactCertificate(
            id=f"impact-certificate:{result.object_id}@{change_set.revision}",
            certificate_type=certificate_type,
            subject_id=result.object_id,
            classification=result.classification,
            reason_code=result.reason_code,
            change_set_digest=change_set.digest,
            result_digest=result.digest,
            revision_lock_digest=revision_lock.digest,
            traversal_commitment=self._traversal_commitment(
                source_id=delta.object_id,
                target_id=result.object_id,
                path=result.proof_path,
            ),
            claim_boundary=claim_boundary,
        )

    @classmethod
    def _path_classification(cls, path: tuple[PathStep, ...]) -> ImpactClassification:
        return cls._transfer_classification(path)

    def preview(self, change_set: ChangeSetRevision) -> ImpactPreview:
        if len(change_set.deltas) != 1:
            raise ValueError("EXACTLY_ONE_ADMITTED_SEMANTIC_DELTA_REQUIRED")
        delta = change_set.deltas[0]
        if delta.semantic_classification != SemanticClassification.SEMANTIC_DELTA:
            lock = self.revision_lock(change_set)
            return ImpactPreview(
                id=f"preview:{change_set.id.split(':', 1)[1]}@{change_set.revision}",
                change_set_ref=f"{change_set.id}@{change_set.revision}",
                state="NO_SEMANTIC_DELTA",
                algorithm_version=self.algorithm_version,
                revision_lock=lock,
                results=(),
                counts={
                    "affected_hard": 0,
                    "bounded_unaffected": 0,
                    "unknown": 0,
                    "skill_requalification": 0,
                },
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )

        revision_lock = self.revision_lock(change_set)
        scope = tuple(change_set.scope)
        if scope != tuple(self.fixture.impact_targets):
            raise IntegrityError("CHANGE_SET_SCOPE_MUST_EQUAL_EVALUATION_TARGETS")
        reachable_nodes, _reachable_edges, truncated_frontier = self._reachable_slice(
            delta.object_id
        )
        unevaluated = self._unevaluated_reachable_targets(
            reachable_nodes, scope, delta.object_id
        )
        if unevaluated:
            raise IntegrityError(
                "REACHABLE_TARGET_OUTSIDE_EVALUATION_SCOPE:" + ",".join(sorted(unevaluated))
            )
        traversal_budget_exhausted = bool(truncated_frontier)
        results: list[ImpactResult] = []
        for target_id in scope:
            target = self.fixture.object(target_id)
            path, path_truncated = self._search(delta.object_id, target_id)
            budget_exhausted = traversal_budget_exhausted or path_truncated
            manifest, manifest_failures = self._manifest_assessment(target_id)
            if path:
                path_classification = self._path_classification(path)
                if path_classification == ImpactClassification.UNKNOWN:
                    classification = ImpactClassification.UNKNOWN
                    reason = "RELATION_RULE_UNKNOWN"
                elif (
                    budget_exhausted
                    and path_classification != ImpactClassification.AFFECTED_HARD
                ):
                    classification = ImpactClassification.UNKNOWN
                    reason = "TRAVERSAL_BUDGET_EXHAUSTED"
                elif target.kind == "SkillContractVersion":
                    classification = ImpactClassification.REQUALIFICATION_REQUIRED
                    reason = "HARD_CONTRACT_DEPENDENCY_CHANGED"
                else:
                    classification = path_classification
                    reason = "ADMITTED_TYPED_PATH"
                result = ImpactResult(
                    object_id=target_id,
                    label=target.label,
                    classification=classification,
                    reason_code=reason,
                    proof_path=path,
                    missing_evidence=(
                        ("complete traversal beyond the declared max_depth",)
                        if reason == "TRAVERSAL_BUDGET_EXHAUSTED"
                        else (
                            tuple(
                                f"transfer rule for relation {step.relation}"
                                for step in path
                                if step.relation not in RELATION_CEILING
                            )
                            if path_classification == ImpactClassification.UNKNOWN
                            else ()
                        )
                    ),
                )
            elif budget_exhausted:
                result = ImpactResult(
                    object_id=target_id,
                    label=target.label,
                    classification=ImpactClassification.UNKNOWN,
                    reason_code="TRAVERSAL_BUDGET_EXHAUSTED",
                    missing_evidence=(
                        "complete traversal beyond the declared max_depth",
                    ),
                )
            elif manifest is not None and not manifest_failures:
                result = ImpactResult(
                    object_id=target_id,
                    label=target.label,
                    classification=ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY,
                    reason_code="COMPLETE_COVERAGE_NO_ADMITTED_PATH",
                    boundary={
                        "dependency_manifest": manifest.ref,
                        "dependency_manifest_digest": manifest.digest,
                        "requirement_slots": [
                            slot.slot_id for slot in manifest.requirement_slots
                        ],
                        "graph_revision": self.fixture.revisions["graph"],
                        "evaluated_change": delta.digest,
                        "scope": [target_id],
                    },
                )
            else:
                result = ImpactResult(
                    object_id=target_id,
                    label=target.label,
                    classification=ImpactClassification.UNKNOWN,
                    reason_code="DEPENDENCY_COVERAGE_INSUFFICIENT",
                    missing_evidence=(
                        "historical ContextManifest",
                        "complete authoritative DependencyManifest",
                        *manifest_failures,
                    ),
                )
            results.append(result)

        ordered = tuple(sorted(results, key=lambda item: scope.index(item.object_id)))
        counts = {
            "affected_hard": sum(
                item.classification == ImpactClassification.AFFECTED_HARD for item in ordered
            ),
            "bounded_unaffected": sum(
                item.classification == ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY
                for item in ordered
            ),
            "unknown": sum(item.classification == ImpactClassification.UNKNOWN for item in ordered),
            "skill_requalification": sum(
                item.classification == ImpactClassification.REQUALIFICATION_REQUIRED for item in ordered
            ),
        }
        certificates = tuple(
            self._certificate(
                change_set=change_set,
                result=result,
                revision_lock=revision_lock,
            )
            for result in ordered
        )
        return ImpactPreview(
            id=f"preview:{change_set.id.split(':', 1)[1]}@{change_set.revision}",
            change_set_ref=f"{change_set.id}@{change_set.revision}",
            state="READY",
            algorithm_version=self.algorithm_version,
            revision_lock=revision_lock,
            results=ordered,
            certificates=certificates,
            counts=counts,
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
