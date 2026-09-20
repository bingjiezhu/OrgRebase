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
        self._dependencies = tuple(sorted(fixture.dependencies, key=lambda item: item.id))
        self._adjacency: dict[str, list[DependencyEdge]] = defaultdict(list)
        self._inbound: dict[str, dict[str, DependencyEdge]] = defaultdict(dict)
        self._eligible_edges: list[DependencyEdge] = []
        for edge in self._dependencies:
            if edge.status == EdgeStatus.ADMITTED and edge.coverage_basis in TRUSTED_COVERAGE:
                self._adjacency[edge.source_id].append(edge)
                self._inbound[edge.target_id][edge.id] = edge
                self._eligible_edges.append(edge)
        self._acyclic = self._is_acyclic()

    def dependency_closure(self, source_refs: tuple[str, ...]) -> dict[str, Any]:
        """Return a bounded closure over admitted evidence edges, without creating authority."""
        if not source_refs or len(source_refs) != len(set(source_refs)):
            raise ValueError("DEPENDENCY_CLOSURE_SOURCES_INVALID")
        affected: set[str] = set()
        edges: set[str] = set()
        truncated: set[str] = set()
        for source in source_refs:
            nodes, traversed, frontier = self._reachable_slice(source)
            affected.update(nodes)
            edges.update(item.id for item in traversed)
            truncated.update(frontier)
        return {"affected_refs": sorted(affected), "edge_refs": sorted(edges),
                "truncated_edge_refs": sorted(truncated), "complete": self._acyclic and not truncated}

    def _is_acyclic(self) -> bool:
        """Prove the prerequisite for merging path prefixes, including disconnected edges."""
        indegrees: dict[str, int] = defaultdict(int)
        for edge in self._eligible_edges:
            indegrees.setdefault(edge.source_id, 0)
            indegrees[edge.target_id] += 1
        ready = deque(node for node, degree in indegrees.items() if degree == 0)
        visited = 0
        while ready:
            node = ready.popleft()
            visited += 1
            for edge in self._adjacency.get(node, ()):
                indegrees[edge.target_id] -= 1
                if indegrees[edge.target_id] == 0:
                    ready.append(edge.target_id)
        return visited == len(indegrees)

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

        eligible_inbound = self._inbound.get(target_id, {})
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
        """Select the exact witness using at most four states per node and depth.

        On a DAG, prefixes with the same transfer state, node and depth have
        identical possible continuations. Keep only their lexically first path.
        All four states are needed: a later weak edge can downgrade HARD below
        UNKNOWN. The target is absorbing and depth remains part of the state, so
        weak shortcuts cannot hide truncation of a longer path. Cycles require
        visited-path history and are explicitly unsupported, never deleted.
        """
        if not self._acyclic:
            raise IntegrityError("CYCLIC_DEPENDENCY_GRAPH_UNSUPPORTED")
        depth_limit = self.max_depth if max_depth is None else max_depth
        if depth_limit < 0:
            raise ValueError("max_depth must be non-negative")
        states: dict[tuple[str, ImpactClassification], tuple[DependencyEdge, ...]] = {
            (source_id, ImpactClassification.AFFECTED_HARD): ()
        }
        selected: tuple[DependencyEdge, ...] = ()
        truncated = False
        for depth in range(depth_limit + 1):
            if depth == depth_limit:
                truncated = any(self._adjacency.get(node) for node, _ in states)
                break
            following: dict[tuple[str, ImpactClassification], tuple[DependencyEdge, ...]] = {}
            for (node, _classification), prefix in states.items():
                for edge in self._adjacency.get(node, ()):
                    candidate = (*prefix, edge)
                    if edge.target_id == target_id:
                        if not selected or self._path_rank(candidate) < self._path_rank(selected):
                            selected = candidate
                        continue
                    key = (edge.target_id, self._transfer_classification(candidate))
                    prior = following.get(key)
                    if prior is None or tuple(item.id for item in candidate) < tuple(
                        item.id for item in prior
                    ):
                        following[key] = candidate
            states = following
            if not states:
                break
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

    def _traversal_snapshot(self, source_id: str) -> dict[str, Any]:
        """Build source-wide certificate commitments once per preview."""
        reachable_nodes, reachable_edges, truncated_frontier = self._reachable_slice(source_id)
        excluded_edges = []
        for edge in self._dependencies:
            if edge.status != EdgeStatus.ADMITTED:
                reason = f"STATUS_{edge.status.value}"
            elif edge.coverage_basis not in TRUSTED_COVERAGE:
                reason = f"UNTRUSTED_COVERAGE_{edge.coverage_basis.value}"
            else:
                continue
            excluded_edges.append({"edge_id": edge.id, "reason": reason})
        dependency_snapshot = []
        reachable_snapshot = []
        reachable_ids = {edge.id for edge in reachable_edges}
        for edge in self._dependencies:
            payload = edge.model_dump(mode="json")
            dependency_snapshot.append(payload)
            if (
                edge.id in reachable_ids
                and edge.status == EdgeStatus.ADMITTED
                and edge.coverage_basis in TRUSTED_COVERAGE
            ):
                reachable_snapshot.append(payload)
        return {
            "source_id": source_id,
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
        }

    def _traversal_commitment(
        self,
        *,
        target_id: str,
        path: tuple[PathStep, ...],
        path_truncated: bool,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        target = self.fixture.object(target_id)
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
        truncated_frontier = snapshot["truncated_frontier_edge_ids"]
        selected_classification = self._path_classification(path).value if path else None
        return {
            **snapshot,
            "target_id": target_id,
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
        traversal_snapshot: dict[str, Any],
        path_truncated: bool,
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
                target_id=result.object_id,
                path=result.proof_path,
                snapshot=traversal_snapshot,
                path_truncated=path_truncated,
            ),
            claim_boundary=claim_boundary,
        )

    @classmethod
    def _path_classification(cls, path: tuple[PathStep, ...]) -> ImpactClassification:
        return cls._transfer_classification(path)

    def preview(self, change_set: ChangeSetRevision) -> ImpactPreview:
        deltas = change_set.deltas
        if not deltas or len(deltas) > 3 or len({item.object_id for item in deltas}) != len(deltas):
            raise ValueError("ADMITTED_SOURCE_SET_INVALID")
        if len(deltas) > 1 and change_set.state != "READMISSION_GROUP_ADMITTED":
            raise ValueError("MULTI_SOURCE_REQUIRES_READMISSION_GROUP")
        sources = tuple(self._preview_source(change_set, delta) for delta in deltas)
        if len(sources) == 1:
            return sources[0]
        if any(source.state != "READY" for source in sources):
            raise IntegrityError("READMISSION_GROUP_REQUIRES_ADMITTED_DELTAS")
        results, certificates = [], []
        blocked = False
        for index, target_id in enumerate(change_set.scope):
            components = tuple(source.results[index] for source in sources)
            classes = {item.classification for item in components}
            hard = ImpactClassification.AFFECTED_HARD in classes
            uncertain = bool(classes & {ImpactClassification.UNKNOWN, ImpactClassification.AFFECTED_REVIEW,
                                        ImpactClassification.AFFECTED_INFORMATIONAL})
            if hard and uncertain:
                classification = ImpactClassification.UNKNOWN
                blocked = True
            elif ImpactClassification.UNKNOWN in classes:
                classification = ImpactClassification.UNKNOWN
            elif hard:
                classification = ImpactClassification.AFFECTED_HARD
            elif ImpactClassification.REQUALIFICATION_REQUIRED in classes:
                raise IntegrityError("READMISSION_GROUP_SKILL_REQUALIFICATION_UNSUPPORTED")
            elif ImpactClassification.AFFECTED_REVIEW in classes:
                classification = ImpactClassification.AFFECTED_REVIEW
            elif ImpactClassification.AFFECTED_INFORMATIONAL in classes:
                classification = ImpactClassification.AFFECTED_INFORMATIONAL
            else:
                classification = ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY
            witnesses = [{"delta_digest": delta.digest, "result": component.model_dump(mode="json"),
                          "certificate": source.certificates[index].model_dump(mode="json")}
                         for delta, component, source in zip(deltas, components, sources, strict=True)]
            selected = next((item for item in components if item.classification == classification), components[0])
            result = ImpactResult(object_id=target_id, label=selected.label, classification=classification,
                                  reason_code="MULTI_SOURCE_IMPACT_UNRESOLVED" if hard and uncertain else "EXACT_SOURCE_SET_IMPACT",
                                  proof_path=selected.proof_path,
                                  missing_evidence=tuple(sorted({item for component in components for item in component.missing_evidence})),
                                  boundary={"source_witnesses_digest": sha256_digest(witnesses),
                                            "aggregation_rule": "ALL_SOURCES_PRESERVE_NO_UNKNOWN_OVERRIDE_V1"})
            certificate = ImpactCertificate(
                id=f"impact-certificate:{change_set.id}:{target_id}@{change_set.revision}",
                schema_version="orgrebase.impact-certificate.v2",
                certificate_type=(ImpactCertificateType.BOUNDED_NON_IMPACT if classification == ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY
                                  else ImpactCertificateType.UNCERTAINTY_WITNESS if classification == ImpactClassification.UNKNOWN
                                  else ImpactCertificateType.POSITIVE_WITNESS),
                subject_id=target_id, classification=classification, reason_code=result.reason_code,
                change_set_digest=change_set.digest, result_digest=result.digest,
                revision_lock_digest=sources[0].revision_lock.digest,
                traversal_commitment={"source_witnesses": witnesses,
                                      "aggregation_rule": "ALL_SOURCES_PRESERVE_NO_UNKNOWN_OVERRIDE_V1"},
                claim_boundary="Exact bounded source set; every source witness is required; unknown cannot be overridden.",
                verifier_version="orgrebase.impact-certificate-verifier@2.0.0",
            )
            results.append(result)
            certificates.append(certificate)
        return ImpactPreview(
            id=sources[0].id, change_set_ref=sources[0].change_set_ref,
            state="BLOCKED" if blocked else "READY", algorithm_version=f"{self.algorithm_version}/source-set-v1",
            revision_lock=sources[0].revision_lock, results=tuple(results), certificates=tuple(certificates),
            counts={"affected_hard": sum(item.classification == ImpactClassification.AFFECTED_HARD for item in results),
                    "bounded_unaffected": sum(item.classification == ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY for item in results),
                    "unknown": sum(item.classification == ImpactClassification.UNKNOWN for item in results),
                    "skill_requalification": 0},
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )

    def _preview_source(self, change_set: ChangeSetRevision, delta: ObjectDelta) -> ImpactPreview:
        if delta.semantic_classification not in {SemanticClassification.SEMANTIC_DELTA, SemanticClassification.TRUST_REVALIDATION}:
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
        if not self._acyclic:
            raise IntegrityError("CYCLIC_DEPENDENCY_GRAPH_UNSUPPORTED")
        traversal_snapshot = self._traversal_snapshot(delta.object_id)
        reachable_nodes = traversal_snapshot["reachable_node_ids"]
        truncated_frontier = traversal_snapshot["truncated_frontier_edge_ids"]
        unevaluated = self._unevaluated_reachable_targets(
            reachable_nodes, scope, delta.object_id
        )
        if unevaluated:
            raise IntegrityError(
                "REACHABLE_TARGET_OUTSIDE_EVALUATION_SCOPE:" + ",".join(sorted(unevaluated))
            )
        traversal_budget_exhausted = bool(truncated_frontier)
        results: list[ImpactResult] = []
        path_truncation: dict[str, bool] = {}
        for target_id in scope:
            target = self.fixture.object(target_id)
            path, path_truncated = self._search(delta.object_id, target_id)
            path_truncation[target_id] = path_truncated
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
                traversal_snapshot=traversal_snapshot,
                path_truncated=path_truncation[result.object_id],
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
