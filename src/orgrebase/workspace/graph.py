"""Complete Workspace universe, immutable graph snapshots and Core bridge."""

from __future__ import annotations

from collections.abc import Callable

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentIdentity,
    CoverageBasis,
    DependencyEdge,
    DependencyManifest,
    DependencyRequirementSlot,
    EdgeStatus,
    ObjectState,
    VersionedObject,
)
from orgrebase.fixture import EnterpriseFixture, load_fixture
from orgrebase.workspace.models import (
    GraphPointerPayload,
    ObjectDigestBinding,
    RuntimeDependencyManifest,
    StoredArtifact,
    WorkspaceGraphEdge,
    WorkspaceGraphSnapshot,
    WorkspaceUniverse,
)

UNIVERSE_MEDIA_TYPE = "application/vnd.orgrebase.workspace-universe+json"
SNAPSHOT_MEDIA_TYPE = "application/vnd.orgrebase.workspace-graph-snapshot+json"
RUNTIME_MANIFEST_MEDIA_TYPE = "application/vnd.orgrebase.runtime-dependency-manifest+json"


def split_ref(ref: str) -> tuple[str, str]:
    if "@" not in ref:
        raise ValueError(f"INVALID_VERSIONED_REF:{ref}")
    return ref.rsplit("@", 1)


def graph_pointer_object(
    *,
    version: str,
    snapshot: WorkspaceGraphSnapshot,
    promoted_at: str,
) -> VersionedObject:
    payload = GraphPointerPayload(
        snapshot_ref=snapshot.ref,
        snapshot_digest=snapshot.digest,
        graph_namespace=snapshot.graph_namespace,
        universe_id=snapshot.universe_id,
        base_revision=snapshot.base_revision,
        promoted_at=promoted_at,
    )
    return VersionedObject(
        id="graph:workspace",
        version=version,
        kind="WorkspaceGraphPointerVersion",
        label="Workspace graph pointer",
        domain="platform",
        state=ObjectState.CURRENT,
        payload=payload.model_dump(mode="json"),
        sensitivity="INTERNAL",
        allowed_purposes=("change_rebase", "workspace_formation"),
        coverage_complete=True,
        coverage_basis=(CoverageBasis.RUNTIME_OBSERVED,),
    )


def _new_object(
    object_id: str,
    version: str,
    kind: str,
    label: str,
    domain: str,
    value: object,
    *,
    state: ObjectState = ObjectState.CURRENT,
    sensitivity: str = "INTERNAL",
    allowed_purposes: tuple[str, ...] = ("enterprise_quote", "change_rebase"),
) -> VersionedObject:
    if kind == "SkillContractVersion":
        payload = {"strategy": value, "contract_ref": f"workspace:{object_id}@{version}"}
    else:
        payload = {"canonical_value": value, "authority": domain}
    return VersionedObject(
        id=object_id,
        version=version,
        kind=kind,
        label=label,
        domain=domain,
        state=state,
        payload=payload,
        source_refs=(f"source:workspace-seed:{object_id}@{version}",),
        sensitivity=sensitivity,
        allowed_purposes=allowed_purposes,
        coverage_complete=True,
        coverage_basis=(CoverageBasis.OWNER_DECLARED_COMPLETE,),
    )


def workspace_seed_objects() -> tuple[VersionedObject, ...]:
    legacy = load_fixture()
    keep = {
        ("claim:product.launch_date", "v7"),
        ("claim:product.launch_date", "v8"),
        ("work:finance_analysis_d", "v1"),
        ("policy:finance.pricing", "v7"),
        ("work:partner_brief_e", "v1"),
        ("skill:enterprise-launch-readiness", "1.2"),
        ("skill:enterprise-launch-readiness", "1.3"),
    }
    objects = [item for item in legacy.objects if (item.id, item.version) in keep]
    objects.extend(
        [
            _new_object(
                "claim:product.enterprise_plan",
                "v4",
                "ClaimVersion",
                "Enterprise product plan",
                "product",
                "enterprise-plan-v4",
            ),
            _new_object(
                "claim:product.residency_capability",
                "v3",
                "ClaimVersion",
                "US residency capability",
                "product",
                "US region supported",
            ),
            _new_object(
                "claim:legal.customer_notice_required",
                "v3",
                "ClaimVersion",
                "Minimum customer notice obligation",
                "legal",
                True,
            ),
            _new_object(
                "policy:finance.price_band",
                "v7",
                "PolicyVersion",
                "Approved price band",
                "finance",
                "strategic",
                sensitivity="CONFIDENTIAL",
            ),
            _new_object(
                "policy:finance.currency",
                "v1",
                "PolicyVersion",
                "Billing currency",
                "finance",
                "USD",
                sensitivity="CONFIDENTIAL",
            ),
            _new_object(
                "policy:finance.currency",
                "v2",
                "PolicyVersion",
                "Billing currency",
                "finance",
                "EUR",
                state=ObjectState.PROPOSED,
                sensitivity="CONFIDENTIAL",
            ),
            _new_object(
                "claim:gtm.partner_terms",
                "v2",
                "ClaimVersion",
                "Partner review condition",
                "gtm",
                "legal-review",
            ),
            _new_object(
                "claim:gtm.public_launch_message",
                "v1",
                "ClaimVersion",
                "Approved public launch message",
                "gtm",
                "Enterprise launch remains on the approved schedule.",
                sensitivity="PUBLIC",
            ),
            _new_object(
                "skill:enterprise-quote-compose",
                "1.0",
                "SkillReferenceVersion",
                "Enterprise quote composition Skill",
                "gtm",
                "typed-quote-composition",
            ),
        ]
    )
    return tuple(objects)


def _workspace_edge(
    edge_id: str,
    provider_ref: str,
    consumer_ref: str,
    relation: str,
    strength: str,
    manifest_ref: str,
    *,
    coverage_basis: CoverageBasis,
    evidence: str,
) -> WorkspaceGraphEdge:
    from orgrebase.domain import DependencyStrength

    return WorkspaceGraphEdge(
        id=edge_id,
        provider_ref=provider_ref,
        provider_digest=sha256_digest({"provider_ref": provider_ref}),
        consumer_ref=consumer_ref,
        relation=relation,
        strength=DependencyStrength(strength),
        source_manifest_ref=manifest_ref,
        source_evidence_ref=evidence,
        coverage_basis=coverage_basis,
        valid_from="2026-08-15T00:00:00Z",
    )


def workspace_seed_universe() -> WorkspaceUniverse:
    legacy = load_fixture()
    objects = workspace_seed_objects()
    legacy_manifests = tuple(
        item
        for item in legacy.dependency_manifests
        if item.target_id
        in {"work:finance_analysis_d", "work:partner_brief_e", "skill:enterprise-launch-readiness"}
    )
    edges = (
        _workspace_edge(
            "edge:pricing-finance",
            "policy:finance.pricing@v7",
            "work:finance_analysis_d@v1",
            "REQUIRES_POLICY",
            "HARD",
            "dependency-manifest:finance-analysis-d@v1",
            coverage_basis=CoverageBasis.OWNER_DECLARED_COMPLETE,
            evidence="owner-attestation:finance@r1",
        ),
        _workspace_edge(
            "edge:launch-partner-candidate",
            "claim:product.launch_date@v7",
            "work:partner_brief_e@v1",
            "ASSUMES",
            "REVIEW",
            "dependency-manifest:partner-brief-e@v1",
            coverage_basis=CoverageBasis.AGENT_INFERRED,
            evidence="handoff:gtm-candidate",
        ),
        _workspace_edge(
            "edge:launch-skill",
            "claim:product.launch_date@v7",
            "skill:enterprise-launch-readiness@1.2",
            "REQUIRES_CLAIM",
            "HARD",
            "dependency-manifest:launch-readiness-skill@1.2",
            coverage_basis=CoverageBasis.CONTRACT_DECLARED,
            evidence="skill-contract:enterprise-launch-readiness@1.2",
        ),
    )
    targets = (
        "work:finance_analysis_d",
        "work:partner_brief_e",
        "skill:enterprise-launch-readiness",
    )
    return WorkspaceUniverse(
        id="workspace-universe:northstar@r1",
        revision="r1",
        organization_id="org:northstar",
        universe_id="workspace-universe:northstar",
        current_objects=objects,
        task_artifact_projections=(),
        edges=edges,
        runtime_manifests=(),
        imported_manifests=legacy_manifests,
        target_ids=targets,
        source_refs=("fixtures/canonical-enterprise.json", "workspace-seed@v1"),
        completeness_basis=(
            CoverageBasis.RUNTIME_OBSERVED,
            CoverageBasis.OWNER_DECLARED_COMPLETE,
            CoverageBasis.CONTRACT_DECLARED,
        ),
        built_at="2026-08-15T00:00:00Z",
    )


class SeedAndRuntimeUniverseSource:
    def __init__(self, seed: WorkspaceUniverse | None = None) -> None:
        self.seed = seed or workspace_seed_universe()

    def load_complete_universe(
        self,
        *,
        organization_id: str,
        universe_id: str,
        at_revision: str,
    ) -> WorkspaceUniverse:
        if (
            organization_id != self.seed.organization_id
            or universe_id != self.seed.universe_id
            or at_revision != self.seed.revision
        ):
            raise KeyError("WORKSPACE_UNIVERSE_NOT_FOUND")
        return self.seed


class WorkspaceSnapshotBuilder:
    version = "workspace-snapshot-builder@1.0.0"

    def build(
        self,
        *,
        universe: WorkspaceUniverse,
        replacement_objects: tuple[VersionedObject, ...],
        replacement_manifests: tuple[RuntimeDependencyManifest, ...],
        targets: tuple[str, ...],
        scope_roots: tuple[str, ...],
        now: str,
        version: str = "v1",
    ) -> WorkspaceGraphSnapshot:
        replaced_ids = {item.id for item in replacement_objects}
        current = [item for item in universe.current_objects if item.id not in replaced_ids]
        current.extend(replacement_objects)
        objects = tuple(sorted(current, key=lambda item: item.ref))
        object_refs = {item.ref for item in objects}
        replacement_consumers = {item.consumer_ref for item in replacement_manifests}
        edges = [item for item in universe.edges if item.consumer_ref not in replacement_consumers]
        for manifest in replacement_manifests:
            for entry in manifest.entries:
                edges.append(
                    WorkspaceGraphEdge(
                        id=f"edge:runtime:{sha256_digest((entry.provider_ref, entry.consumer_ref, entry.slot_id))[7:23]}",
                        provider_ref=entry.provider_ref,
                        provider_digest=entry.provider_digest,
                        consumer_ref=entry.consumer_ref,
                        relation=entry.relation,
                        strength=entry.strength,
                        source_manifest_ref=manifest.ref,
                        source_evidence_ref=entry.source_event_ref,
                        source_trace_event_ref=entry.source_event_ref,
                        coverage_basis=CoverageBasis.RUNTIME_OBSERVED,
                        valid_from=entry.valid_from,
                        valid_to=entry.valid_to,
                    )
                )
        edge_tuple = tuple(sorted(edges, key=lambda item: item.id))
        for edge in edge_tuple:
            if edge.provider_ref not in object_refs or edge.consumer_ref not in object_refs:
                raise ValueError(f"SNAPSHOT_ENDPOINT_NOT_CLOSED:{edge.id}")
        manifest_refs = tuple(
            sorted(
                [item.ref for item in universe.imported_manifests if item.target_id not in replaced_ids]
                + [item.ref for item in replacement_manifests]
            )
        )
        target_refs: list[str] = []
        for target_id in sorted(targets):
            candidates = [
                item
                for item in objects
                if item.id == target_id
                and item.state
                in {
                    ObjectState.CURRENT,
                    ObjectState.ACTIVE,
                    ObjectState.CANARY,
                    ObjectState.REVIEW_REQUIRED,
                }
            ]
            if len(candidates) != 1:
                raise ValueError(f"SNAPSHOT_CURRENT_TARGET_CARDINALITY:{target_id}:{len(candidates)}")
            target_refs.append(candidates[0].ref)
        bindings = tuple(
            ObjectDigestBinding(
                object_ref=item.ref,
                object_id=item.id,
                version=item.version,
                kind=item.kind,
                digest=item.digest,
                storage_class="OBJECT_VERSION",
            )
            for item in objects
        )
        revisions = {
            "graph": f"graph:workspace@{version}",
            "policy": "policy:context@r1",
            "authorization": "authz:relations@r1",
            "skill_registry": "skills:registry@r1",
            "runtime_registry": f"runtime:workspace@{version}",
            "evaluation_suite": "evaluation:workspace@r1",
        }
        return WorkspaceGraphSnapshot(
            id="workspace-graph:northstar",
            version=version,
            organization_id=universe.organization_id,
            graph_namespace="graph:workspace",
            universe_id=universe.id,
            universe_digest=universe.digest,
            base_revision=universe.revision,
            scope_roots=tuple(sorted(scope_roots)),
            object_refs=tuple(sorted(object_refs)),
            object_digests=bindings,
            edges=edge_tuple,
            manifest_refs=manifest_refs,
            target_refs=tuple(sorted(target_refs)),
            revisions=revisions,
            edge_set_digest=sha256_digest([item.model_dump(mode="json") for item in edge_tuple]),
            built_at=now,
            builder_version=self.version,
        )

    def to_enterprise_fixture(
        self,
        *,
        snapshot: WorkspaceGraphSnapshot,
        artifact_reader: Callable[[str, str | None], StoredArtifact],
        object_reader: Callable[[str, str | None], VersionedObject],
        agents: tuple[AgentIdentity, ...],
        evaluation_cases: tuple[dict[str, object], ...],
        context_profiles: dict[str, dict[str, object]],
        change: dict[str, object],
    ) -> EnterpriseFixture:
        universe_artifact = artifact_reader(snapshot.universe_id, UNIVERSE_MEDIA_TYPE)
        universe = WorkspaceUniverse.model_validate(universe_artifact.payload)
        if universe.digest != snapshot.universe_digest:
            raise ValueError("SNAPSHOT_UNIVERSE_DIGEST_MISMATCH")
        objects = tuple(object_reader(*split_ref(ref)) for ref in snapshot.object_refs)
        object_by_ref = {item.ref: item for item in objects}
        for binding in snapshot.object_digests:
            item = object_by_ref.get(binding.object_ref)
            if item is None or item.digest != binding.digest:
                raise ValueError(f"SNAPSHOT_OBJECT_DIGEST_MISMATCH:{binding.object_ref}")
        core_edges = tuple(
            DependencyEdge(
                id=edge.id,
                source_id=split_ref(edge.provider_ref)[0],
                target_id=split_ref(edge.consumer_ref)[0],
                relation=edge.relation,
                strength=edge.strength,
                coverage_basis=edge.coverage_basis,
                status=(
                    EdgeStatus.ADMITTED
                    if edge.coverage_basis
                    in {
                        CoverageBasis.RUNTIME_OBSERVED,
                        CoverageBasis.OWNER_DECLARED_COMPLETE,
                        CoverageBasis.CONTRACT_DECLARED,
                        CoverageBasis.IMPORTED_VERIFIED,
                    }
                    else EdgeStatus.PROPOSED_EDGE
                ),
                provenance_refs=(edge.source_manifest_ref, edge.source_evidence_ref),
            )
            for edge in snapshot.edges
        )
        edge_by_consumer: dict[str, list[DependencyEdge]] = {}
        for edge in core_edges:
            edge_by_consumer.setdefault(edge.target_id, []).append(edge)
        imported_by_ref = {item.ref: item for item in universe.imported_manifests}
        manifests: list[DependencyManifest] = []
        for manifest_ref in snapshot.manifest_refs:
            if manifest_ref in imported_by_ref:
                imported = imported_by_ref[manifest_ref]
                # Rebind edge IDs/fields to the committed workspace snapshot.
                target_edges = edge_by_consumer.get(imported.target_id, [])
                rebound_slots = []
                for slot in imported.requirement_slots:
                    edge = next((item for item in target_edges if item.id == slot.edge_id), None)
                    if edge is None:
                        continue
                    rebound_slots.append(
                        DependencyRequirementSlot(
                            slot_id=slot.slot_id,
                            edge_id=edge.id,
                            source_id=edge.source_id,
                            relation=edge.relation,
                            strength=edge.strength,
                            coverage_basis=edge.coverage_basis,
                            provenance_refs=tuple(sorted(set(slot.provenance_refs) | set(edge.provenance_refs))),
                        )
                    )
                slots = tuple(rebound_slots)
                manifests.append(
                    DependencyManifest(
                        id=imported.id,
                        version=imported.version,
                        target_id=imported.target_id,
                        target_version=imported.target_version,
                        issuer_id=imported.issuer_id,
                        authority_domain=imported.authority_domain,
                        completeness=imported.completeness,
                        requirement_slots=slots,
                        provenance_refs=imported.provenance_refs,
                    )
                )
                continue
            artifact = artifact_reader(manifest_ref, RUNTIME_MANIFEST_MEDIA_TYPE)
            runtime = RuntimeDependencyManifest.model_validate(artifact.payload)
            target_id, target_version = split_ref(runtime.consumer_ref)
            target_edges = {edge.source_id: edge for edge in edge_by_consumer.get(target_id, [])}
            slots = []
            for entry in runtime.entries:
                source_id = split_ref(entry.provider_ref)[0]
                edge = target_edges.get(source_id)
                if edge is None:
                    raise ValueError(f"RUNTIME_MANIFEST_EDGE_MISSING:{entry.slot_id}")
                slots.append(
                    DependencyRequirementSlot(
                        slot_id=f"slot:{target_id}:{entry.slot_id}",
                        edge_id=edge.id,
                        source_id=source_id,
                        relation=entry.relation,
                        strength=entry.strength,
                        coverage_basis=CoverageBasis.RUNTIME_OBSERVED,
                        provenance_refs=(entry.source_event_ref, runtime.trace_ref),
                    )
                )
            target_object = next(item for item in objects if item.id == target_id and item.version == target_version)
            manifests.append(
                DependencyManifest(
                    id=f"dependency-manifest:{target_id.split(':')[-1]}",
                    version=target_version,
                    target_id=target_id,
                    target_version=target_version,
                    issuer_id=runtime.issuer_id,
                    authority_domain=target_object.domain,
                    completeness=runtime.completeness,
                    requirement_slots=tuple(slots),
                    provenance_refs=runtime.provenance_refs,
                )
            )
        targets = tuple(split_ref(ref)[0] for ref in snapshot.target_refs)
        return EnterpriseFixture(
            schema_version="orgrebase.fixture.workspace.v1",
            organization_id=snapshot.organization_id,
            revisions=snapshot.revisions,
            change=change,
            objects=objects,
            dependencies=core_edges,
            dependency_manifests=tuple(manifests),
            impact_targets=targets,
            context_profiles=context_profiles,
            agents=agents,
            evaluation_cases=evaluation_cases,
        )
