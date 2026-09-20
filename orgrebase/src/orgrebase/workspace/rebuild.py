"""Workspace-specific rebuild, successor evidence, and atomic graph promotion.

The generic :class:`orgrebase.workflow.RebaseWorkflow` remains the sole writer of
business object versions.  This module supplies a payload-only Quote handler and
an in-transaction extension that turns a rebuilt Quote into the next immutable
Workspace graph revision.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

from orgrebase.clock import timestamp, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    ChangeSetRevision,
    ContextItem,
    ContextManifest,
    DependencyManifest,
    DependencyRequirementSlot,
    EvidenceClass,
    ImpactPreview,
    IntegrityError,
    ObjectDelta,
    ObjectState,
    RebaseReceipt,
    VersionedObject,
)
from orgrebase.runtime_contracts import prepare_artifact_write
from orgrebase.store import StateStore
from orgrebase.workspace.execution import (
    ExecutionReferenceMonitor,
    QuoteInputAssembler,
    QuoteRenderer,
    RuntimeDependencyCompiler,
    StaticClock,
    TraceCoverageVerifier,
    quote_output_lineage,
)
from orgrebase.workspace.graph import (
    RUNTIME_MANIFEST_MEDIA_TYPE,
    SNAPSHOT_MEDIA_TYPE,
    UNIVERSE_MEDIA_TYPE,
    WorkspaceSnapshotBuilder,
    graph_pointer_object,
    split_ref,
)
from orgrebase.workspace.models import (
    ActorContextProjection,
    AdmittedReference,
    ClaimScope,
    PreparedSuccessorEvidence,
    QuotePayload,
    QuotePricingComparison,
    QuoteTaskLiterals,
    TaskContextManifest,
    TaskRequest,
    TaskTemplateVersion,
    WorkspaceGraphEdge,
    WorkspaceGraphSnapshot,
    WorkspaceRebaseReceipt,
    WorkspaceUniverse,
)
from orgrebase.workspace.read_dependencies import ReadDependencyValidator, require_read_dependency_validator
from orgrebase.workspace.templates import TemplateRegistry

WORKSPACE_CONTEXT_MEDIA_TYPE = "application/vnd.orgrebase.task-context+json"
WORKSPACE_TRACE_MEDIA_TYPE = "application/vnd.orgrebase.work-trace+json"
WORKSPACE_COVERAGE_MEDIA_TYPE = "application/vnd.orgrebase.trace-coverage+json"
WORKSPACE_REBASE_RECEIPT_MEDIA_TYPE = "application/vnd.orgrebase.workspace-rebase-receipt+json"

_QUOTE_SLOT_OBJECTS: tuple[tuple[str, str], ...] = (
    ("product_plan", "claim:product.enterprise_plan"),
    ("launch_date", "claim:product.launch_date"),
    ("data_residency", "claim:product.residency_capability"),
    ("notice_required", "claim:legal.customer_notice_required"),
    ("price_band", "policy:finance.price_band"),
    ("currency", "policy:finance.currency"),
    ("partner_terms", "claim:gtm.partner_terms"),
    ("quote_compose_skill", "skill:enterprise-quote-compose"),
    ("quote_basket", "claim:product.quote_basket"),
    ("pricing_policy", "policy:finance.pricing"),
)


def quote_slot_objects(template_ref: str) -> tuple[tuple[str, str], ...]:
    required = {slot.slot_id for slot in TemplateRegistry().get(template_ref).slots}
    return tuple(item for item in _QUOTE_SLOT_OBJECTS if item[0] in required)

_FORBIDDEN_QUOTE_KEYS = {
    "raw_text",
    "raw_contract",
    "raw_contract_text",
    "internal_cost",
    "internal_cost_floor",
    "secret",
    "secrets",
    "api_key",
}


def _has_forbidden_quote_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            (isinstance(key, str) and key.lower() in _FORBIDDEN_QUOTE_KEYS)
            or _has_forbidden_quote_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_quote_key(item) for item in value)
    return False


def _object_projection(item: VersionedObject) -> object:
    if item.kind in {"ClaimVersion", "PolicyVersion", "SkillReferenceVersion"}:
        if "canonical_value" in item.payload:
            return item.payload["canonical_value"]
        if item.kind == "SkillReferenceVersion":
            return item.ref
    return item.payload


def _context_item(item: VersionedObject) -> ContextItem:
    return ContextItem(
        object_ref=item.ref,
        label=item.label,
        disposition="INCLUDED_AS_PREMISE",
        reason_code="WORKSPACE_CURRENT_ADMITTED_REFERENCE",
        payload={"value": _object_projection(item)},
    )


def preview_quote_pricing(*, store: StateStore, quote: VersionedObject,
                          proposal: VersionedObject, template_ref: str,
                          snapshot_digest: str) -> QuotePricingComparison | None:
    """Calculate a source-bound preview without changing any current pointer."""
    if template_ref != "template:enterprise_quote@v2":
        return None
    objects = {slot: store.get_object(object_id) for slot, object_id in quote_slot_objects(template_ref)}
    values = {slot: _object_projection(item) for slot, item in objects.items()}
    literals = QuoteTaskLiterals(owner=quote.payload["owner"], customer_id=quote.payload["customer_id"])
    renderer = QuoteRenderer()
    before = renderer.render(task_literals=literals, inputs=QuoteInputAssembler.from_values(values)).pricing
    stored = QuotePayload.model_validate(quote.payload).pricing
    if stored is None or stored != before:
        raise IntegrityError("QUOTE_PRICING_CURRENT_SOURCE_MISMATCH")
    matching = [slot for slot, item in objects.items() if item.id == proposal.id]
    if len(matching) != 1:
        raise IntegrityError("QUOTE_PRICING_PROPOSAL_SOURCE_MISMATCH")
    values[matching[0]] = _object_projection(proposal)
    after = renderer.render(task_literals=literals, inputs=QuoteInputAssembler.from_values(values)).pricing
    return QuotePricingComparison(
        predecessor_ref=quote.ref, predecessor_digest=quote.digest, proposal_digest=proposal.digest,
        snapshot_digest=snapshot_digest, before=before, after=after,
    )


@dataclass(frozen=True)
class WorkspaceWorkflowClock:
    """Deterministic clock used by the reproducible local profile."""

    approved_at: str
    apply_at: str
    expires_at: str


class WorkspaceRebuildContextProvider:
    """Compile the exact current quote premises into a legacy-compatible manifest."""

    def __init__(self, *, clock: WorkspaceWorkflowClock, read_dependency_validator: ReadDependencyValidator | None = None,
                 template_ref: str = "template:enterprise_quote@v1") -> None:
        self.clock = clock
        self.read_dependency_validator = read_dependency_validator
        self.template_ref = template_ref

    def current_premises(self, store: StateStore, now: str) -> tuple[VersionedObject, ...]:
        premises = tuple(store.get_object(item_id) for _slot_id, item_id in quote_slot_objects(self.template_ref))
        if any(item.state not in {ObjectState.CURRENT, ObjectState.ACTIVE, ObjectState.CANARY} for item in premises):
            raise IntegrityError("SOURCE_PREMISE_NOT_CURRENT")
        require_read_dependency_validator(premises, now, self.read_dependency_validator)
        return premises

    def prepare(
        self,
        *,
        fixture: object,
        store: StateStore,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        affected: tuple[object, ...],
    ) -> tuple[ContextManifest, ...]:
        manifests: list[ContextManifest] = []
        for result in affected:
            object_id = result.object_id
            current = store.get_object(object_id)
            if current.payload.get("deliverable_kind") != "QUOTE":
                raise IntegrityError(f"WORKSPACE_CONTEXT_UNSUPPORTED_TARGET:{object_id}")
            premises = self.current_premises(store, self.clock.apply_at)
            included = tuple(_context_item(item) for item in premises)
            manifests.append(
                ContextManifest(
                    id=f"context:workspace-rebuild:{object_id.split(':')[-1]}",
                    version=current.version,
                    actor_id="workspace-renderer",
                    target_object_id=object_id,
                    purpose=change_set.purpose,
                    graph_revision=preview.revision_lock.graph_revision,
                    policy_revision=preview.revision_lock.policy_revision,
                    authorization_revision=fixture.revisions["authorization"],
                    expires_at=self.clock.expires_at,
                    included=included,
                    excluded=(),
                    evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
                )
            )
        return tuple(manifests)


class QuoteRebuildPayloadHandler:
    """Pure payload transformer for Workspace Quote objects.

    It never chooses an object ID, version, state, graph pointer, or transaction.
    Those remain under :class:`RebaseWorkflow` control.
    """

    field_by_object_id: ClassVar[dict[str, str]] = {
        "claim:product.launch_date": "launch_date",
        "policy:finance.currency": "currency",
        "claim:product.enterprise_plan": "product_plan",
        "claim:product.quote_basket": "quote_basket",
        "policy:finance.pricing": "pricing_policy",
    }

    def __init__(self, *, template_ref: str = "template:enterprise_quote@v1") -> None:
        self.template_ref = template_ref

    def render_context(self, old: VersionedObject, context: ContextManifest) -> QuotePayload:
        values = {}
        for slot, object_id in quote_slot_objects(self.template_ref):
            matches = [item for item in context.included if item.object_ref.rsplit("@", 1)[0] == object_id]
            if len(matches) != 1 or matches[0].payload is None or "value" not in matches[0].payload:
                raise IntegrityError(f"QUOTE_REBUILD_SOURCE_CONTEXT_MISSING:{slot}")
            values[slot] = matches[0].payload["value"]
        return QuoteRenderer().render(
            task_literals=QuoteTaskLiterals(owner=old.payload["owner"], customer_id=old.payload["customer_id"]),
            inputs=QuoteInputAssembler.from_values(values),
        )

    def rebuild_payload(
        self,
        *,
        old: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> Mapping[str, object]:
        if not deltas or len({delta.object_id for delta in deltas}) != len(deltas):
            raise IntegrityError("QUOTE_REBUILD_SOURCE_SET_INVALID")
        payload = self.render_context(old, context).model_dump(mode="json")
        for delta in deltas:
            field = self.field_by_object_id.get(delta.object_id)
            if field is None:
                raise IntegrityError(f"QUOTE_REBUILD_UNSUPPORTED_DELTA:{delta.object_id}")
        payload["rebased_from"] = old.ref
        payload["rebase_change_set"] = deltas[0].digest if len(deltas) == 1 else sha256_digest(tuple(delta.digest for delta in deltas))
        payload["context_manifest"] = context.digest
        candidate = QuotePayload.model_validate(payload)
        self.verify_payload(
            old=old, new_payload=candidate.model_dump(mode="json"), deltas=deltas, context=context
        )
        return candidate.model_dump(mode="json")

    def verify_payload(
        self,
        *,
        old: VersionedObject,
        new_payload: Mapping[str, object],
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> None:
        if _has_forbidden_quote_key(new_payload):
            raise IntegrityError("QUOTE_REBUILD_FORBIDDEN_SENSITIVE_FIELD")
        new_quote = QuotePayload.model_validate(dict(new_payload))
        old_quote = QuotePayload.model_validate(old.payload)
        rendered = self.render_context(old, context)
        fields = set()
        for delta in deltas:
            field = self.field_by_object_id.get(delta.object_id)
            if field is None or field in fields:
                raise IntegrityError("QUOTE_REBUILD_DID_NOT_APPLY_ADMITTED_VALUE")
            if field not in {"quote_basket", "pricing_policy"} and getattr(new_quote, field) != delta.proposed_value:
                raise IntegrityError("QUOTE_REBUILD_DID_NOT_APPLY_ADMITTED_VALUE")
            fields.add(field)
            if not any(item.object_ref == f"{delta.object_id}@{delta.proposed_version}"
                       and item.payload is not None and item.payload.get("value") == delta.proposed_value
                       for item in context.included):
                raise IntegrityError("QUOTE_REBUILD_SOURCE_CONTEXT_MISMATCH")
        expected_change = deltas[0].digest if len(deltas) == 1 else sha256_digest(tuple(delta.digest for delta in deltas))
        if new_quote.rebase_change_set != expected_change:
            raise IntegrityError("QUOTE_REBUILD_CHANGE_SET_MISMATCH")
        preservation = set(QuotePayload.model_fields) - fields - {
            "rebased_from", "rebase_change_set", "context_manifest",
        }
        if fields & {"quote_basket", "pricing_policy", "currency"}:
            preservation.discard("pricing")
        for name in preservation:
            if getattr(new_quote, name) != getattr(old_quote, name):
                raise IntegrityError(f"QUOTE_REBUILD_PRESERVATION_VIOLATION:{name}")
        if new_quote.pricing != rendered.pricing:
            raise IntegrityError("QUOTE_REBUILD_PRICING_MISMATCH")
        if new_quote.rebased_from != old.ref:
            raise IntegrityError("QUOTE_REBUILD_MISSING_PREDECESSOR")
        if new_quote.context_manifest != context.digest:
            raise IntegrityError("QUOTE_REBUILD_CONTEXT_BINDING_MISMATCH")

    def verify_committed(
        self,
        *,
        old: VersionedObject,
        rebuilt: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> None:
        self.verify_payload(old=old, new_payload=rebuilt.payload, deltas=deltas, context=context)


def _workspace_task_context(
    *,
    store: StateStore,
    template: TaskTemplateVersion,
    quote: VersionedObject,
    version: str,
    now: str,
    revisions: Mapping[str, str],
    base_task: TaskRequest | None = None,
) -> tuple[TaskRequest, TaskContextManifest, ActorContextProjection]:
    selected = base_task
    task = TaskRequest(
        id=(selected.id if selected is not None else "task:quote_acme"),
        organization_id=(selected.organization_id if selected is not None else "org:northstar"),
        actor_id=(selected.actor_id if selected is not None else "employee:sales-owner"),
        purpose=(selected.purpose if selected is not None else "enterprise_quote"),
        deliverable_kind=(selected.deliverable_kind if selected is not None else "QUOTE"),
        requested_at=now,
        template_ref=(selected.template_ref if selected is not None else template.ref),
        input_values={"owner": quote.payload["owner"]},
        customer_id=quote.payload["customer_id"],
        idempotency_key=f"workspace:successor:{quote.ref}",
    )
    slot_specs = {item.slot_id: item for item in template.slots}
    bindings: list[AdmittedReference] = []
    for slot_id, object_id in quote_slot_objects(template.ref):
        item = store.get_object(object_id)
        spec = slot_specs[slot_id]
        value = _object_projection(item)
        bindings.append(
            AdmittedReference(
                slot_id=slot_id,
                object_ref=item.ref,
                object_digest=item.digest,
                projection_schema_ref=spec.value_schema_ref,
                projection=value,
                projection_digest=sha256_digest(value),
                relation=spec.relation,
                strength=spec.strength,
                semantic_kind=spec.semantic_kind,
                claim_scope=ClaimScope.ORGANIZATION,
                purpose="enterprise_quote",
                recipient_ids=("workspace-renderer",),
                expires_at=timestamp(utc_datetime(now) + timedelta(seconds=900)),
                admission_decision_ref=f"admission:successor:{quote.ref}:{slot_id}",
            )
        )
    task_slug = task.id.split(":")[-1]
    context_id = f"task-context:{task_slug}"
    context = TaskContextManifest(
        source_digest_scheme="VERSIONED_OBJECT_SHA256_V1",
        id=context_id,
        version=version,
        organization_id=task.organization_id,
        task_ref=task.id,
        template_ref=template.ref,
        coalition_plan_ref=f"coalition:{task_slug}@v1",
        revision_lock=dict(revisions),
        actor_projection_refs=(f"actor-context:{task_slug}:renderer@{version}",),
        slot_bindings=tuple(sorted(bindings, key=lambda item: item.slot_id)),
        created_at=now,
        expires_at=timestamp(utc_datetime(now) + timedelta(seconds=900)),
    )
    projection = ActorContextProjection(
        id=f"actor-context:{task_slug}:renderer",
        version=version,
        actor_id="workspace-renderer",
        task_context_ref=context.ref,
        purpose="enterprise_quote",
        included_refs=tuple(sorted(item.object_ref for item in bindings)),
        excluded=(),
        expires_at=timestamp(utc_datetime(now) + timedelta(seconds=900)),
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )
    return task, context, projection


def _rebind_imported_manifest(
    manifest: DependencyManifest,
    *,
    target_version: str,
) -> DependencyManifest:
    slots = tuple(
        DependencyRequirementSlot(
            slot_id=slot.slot_id,
            edge_id=slot.edge_id,
            source_id=slot.source_id,
            relation=slot.relation,
            strength=slot.strength,
            coverage_basis=slot.coverage_basis,
            provenance_refs=slot.provenance_refs,
        )
        for slot in manifest.requirement_slots
    )
    return DependencyManifest(
        id=manifest.id,
        version=target_version,
        target_id=manifest.target_id,
        target_version=target_version,
        issuer_id=manifest.issuer_id,
        authority_domain=manifest.authority_domain,
        completeness=manifest.completeness,
        requirement_slots=slots,
        provenance_refs=tuple(sorted(set(manifest.provenance_refs) | {"workspace-successor-rebind"})),
    )


class WorkspaceGraphApplyExtension:
    """Build and commit successor trace/manifest/snapshot under the Apply transaction."""

    def __init__(
        self,
        *,
        fail_after: str | None = None,
        run_id: str | None = None,
        task_request: TaskRequest | None = None,
        graph_pointer_id: str = "graph:workspace",
        graph_snapshot_id: str = "workspace-graph:northstar",
        snapshot_scope_roots: tuple[str, ...] = (
            "claim:product.launch_date",
            "policy:finance.currency",
        ),
    ) -> None:
        self.fail_after = fail_after
        self.run_id = run_id
        self.task_request = task_request
        self.graph_pointer_id = graph_pointer_id
        self.graph_snapshot_id = graph_snapshot_id
        self.snapshot_scope_roots = tuple(snapshot_scope_roots)
        self.snapshot_builder = WorkspaceSnapshotBuilder()
        self.assembler = QuoteInputAssembler()
        self.renderer = QuoteRenderer()
        self.coverage_verifier = TraceCoverageVerifier()
        self.dependency_compiler = RuntimeDependencyCompiler()

    @staticmethod
    def _next_version(version: str) -> str:
        if not (version.startswith("v") and version[1:].isdigit()):
            raise IntegrityError(f"WORKSPACE_GRAPH_VERSION_UNSUPPORTED:{version}")
        return f"v{int(version[1:]) + 1}"

    @staticmethod
    def _maybe_fail(point: str, configured: str | None) -> None:
        if configured == point:
            raise RuntimeError(f"INJECTED_WORKSPACE_SUCCESSOR_FAILURE:{point}")

    def _prepare_successor(
        self,
        *,
        store: StateStore,
        rebuilt: VersionedObject,
        prior_snapshot: WorkspaceGraphSnapshot,
        next_version: str,
        committed_at: str,
    ) -> PreparedSuccessorEvidence:
        template = TemplateRegistry().get(
            self.task_request.template_ref if self.task_request is not None else "template:enterprise_quote@v1"
        )
        task, task_context, projection = _workspace_task_context(
            store=store,
            template=template,
            quote=rebuilt,
            version=next_version,
            now=committed_at,
            revisions={
                **prior_snapshot.revisions,
                "graph": f"{self.graph_pointer_id}@{next_version}",
                "runtime_registry": f"runtime:workspace@{next_version}",
            },
            base_task=self.task_request,
        )
        task_slug = task.id.split(":")[-1]
        counter = {"value": 0}

        def id_factory(prefix: str) -> str:
            counter["value"] += 1
            return f"event:{task_slug}:{next_version}:{prefix}:{counter['value']:02d}"

        monitor = ExecutionReferenceMonitor(
            task=task,
            template=template,
            manifest=task_context,
            artifact_reader=store.load_artifact,
            actor_id="workspace-renderer",
            run_id=(self.run_id or f"run:workspace:{task_slug.replace('_', '-')}@{next_version}"),
            clock=StaticClock(committed_at),
            id_factory=id_factory,
            trace_version=next_version,
        )
        resolved = self.assembler.assemble(monitor)
        rendered = self.renderer.render(
            task_literals=QuoteTaskLiterals(
                owner=str(rebuilt.payload["owner"]),
                customer_id=str(rebuilt.payload["customer_id"]),
            ),
            inputs=resolved,
        )
        rendered_business = rendered.model_dump(mode="json")
        actual_business = QuotePayload.model_validate(rebuilt.payload).model_dump(mode="json")
        metadata = {"rebased_from", "rebase_change_set", "context_manifest"}
        for field in (set(rendered_business) | set(actual_business)) - metadata:
            if rendered_business.get(field) != actual_business.get(field):
                raise IntegrityError(f"SUCCESSOR_RENDER_MISMATCH:{field}")
        trace = monitor.finish(
            output_ref=rebuilt.ref,
            output_payload=rebuilt.payload,
            output_field_lineage=quote_output_lineage(priced=rendered.pricing is not None),
        )
        coverage = self.coverage_verifier.verify(
            template=template,
            trace=trace,
            output_payload=rebuilt.payload,
            observed_channels=("REFERENCE_MONITOR",),
        )
        manifest = self.dependency_compiler.compile(
            task=task,
            template=template,
            trace=trace,
            coverage=coverage,
            consumer_ref=rebuilt.ref,
            consumer_domain=rebuilt.domain,
            revision_lock=task_context.revision_lock,
            now=committed_at,
            version=next_version,
        )

        prior_universe_artifact = store.load_artifact(prior_snapshot.universe_id, UNIVERSE_MEDIA_TYPE)
        prior_universe = WorkspaceUniverse.model_validate(prior_universe_artifact.payload)
        # Keep current resources and exact versions still referenced by active evidence.
        # Earlier snapshots retain superseded versions for historical replay.
        prior_objects = []
        for ref in prior_snapshot.object_refs:
            object_id, version = split_ref(ref)
            if object_id == rebuilt.id:
                continue
            prior_objects.append(store.get_object(object_id, version))
        required_refs = {edge.provider_ref for edge in prior_snapshot.edges
                         if split_ref(edge.consumer_ref)[0] != rebuilt.id}
        objects_by_ref = {item.ref: item for item in prior_objects
                          if item.ref in required_refs or item.state == ObjectState.PROPOSED}
        for item in prior_objects:
            current = store.get_object(item.id)
            objects_by_ref[current.ref] = current
        objects_by_ref[rebuilt.ref] = rebuilt
        current_objects = tuple(sorted(objects_by_ref.values(), key=lambda item: item.ref))

        imported: list[DependencyManifest] = []
        for item in prior_universe.imported_manifests:
            current_target = store.get_object(item.target_id)
            imported.append(
                _rebind_imported_manifest(
                    item,
                    target_version=current_target.version,
                )
            )
        # Retain the exact source version of imported evidence. A new target
        # version inherits its declared relation, not a newly observed source read.
        current_ref_by_id = {
            item.id: store.get_object(item.id).ref
            for item in current_objects
            if item.state
            in {ObjectState.CURRENT, ObjectState.ACTIVE, ObjectState.CANARY, ObjectState.REVIEW_REQUIRED}
        }
        edges: list[WorkspaceGraphEdge] = []
        for edge in prior_snapshot.edges:
            consumer_id, _consumer_version = split_ref(edge.consumer_ref)
            if consumer_id == rebuilt.id:
                continue
            provider_ref = edge.provider_ref
            consumer_ref = current_ref_by_id.get(consumer_id, edge.consumer_ref)
            edge_payload = edge.model_dump(mode="json", exclude={"digest"})
            edge_payload.update(
                {
                    "provider_ref": provider_ref,
                    "provider_digest": store.get_object(*split_ref(provider_ref)).digest,
                    "consumer_ref": consumer_ref,
                }
            )
            edges.append(WorkspaceGraphEdge.model_validate(edge_payload))
        universe = WorkspaceUniverse(
            id=f"{prior_universe.universe_id}@r{next_version[1:]}",
            revision=f"r{next_version[1:]}",
            organization_id=prior_universe.organization_id,
            universe_id=prior_universe.universe_id,
            current_objects=current_objects,
            task_artifact_projections=prior_universe.task_artifact_projections,
            edges=tuple(sorted(edges, key=lambda item: item.id)),
            runtime_manifests=(manifest,),
            imported_manifests=tuple(imported),
            target_ids=prior_universe.target_ids,
            source_refs=tuple(sorted({prior_snapshot.ref, trace.ref})),
            completeness_basis=prior_universe.completeness_basis,
            built_at=committed_at,
        )
        snapshot = self.snapshot_builder.build(
            universe=universe,
            replacement_objects=(rebuilt,),
            replacement_manifests=(manifest,),
            targets=tuple(sorted(set(prior_universe.target_ids) | {rebuilt.id})),
            scope_roots=self.snapshot_scope_roots,
            now=committed_at,
            version=next_version,
            snapshot_id=self.graph_snapshot_id,
            graph_namespace=self.graph_pointer_id,
        )
        pointer = graph_pointer_object(
            version=next_version,
            snapshot=snapshot,
            promoted_at=committed_at,
            object_id=self.graph_pointer_id,
        )
        writes = (
            prepare_artifact_write(task_context.ref, WORKSPACE_CONTEXT_MEDIA_TYPE, task_context),
            prepare_artifact_write(
                projection.ref,
                "application/vnd.orgrebase.actor-context-projection+json",
                projection,
            ),
            prepare_artifact_write(trace.ref, WORKSPACE_TRACE_MEDIA_TYPE, trace),
            prepare_artifact_write(coverage.id, WORKSPACE_COVERAGE_MEDIA_TYPE, coverage),
            prepare_artifact_write(manifest.ref, RUNTIME_MANIFEST_MEDIA_TYPE, manifest),
            prepare_artifact_write(universe.id, UNIVERSE_MEDIA_TYPE, universe),
            prepare_artifact_write(snapshot.ref, SNAPSHOT_MEDIA_TYPE, snapshot),
        )
        return PreparedSuccessorEvidence(
            successor_objects=(rebuilt,),
            successor_artifact_writes=writes,
            graph_pointer=pointer,
            successor_snapshot_ref=snapshot.ref,
            successor_snapshot_digest=snapshot.digest,
        )

    def commit(
        self,
        connection: sqlite3.Connection,
        *,
        store: StateStore,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        deltas: tuple[ObjectDelta, ...],
        rebuilt_objects: tuple[VersionedObject, ...],
        contexts: tuple[ContextManifest, ...],
        base_receipt: RebaseReceipt,
        committed_at: str,
    ) -> WorkspaceRebaseReceipt | None:
        if not rebuilt_objects:
            return None
        if len(rebuilt_objects) != 1 or rebuilt_objects[0].payload.get("deliverable_kind") != "QUOTE":
            raise IntegrityError("WORKSPACE_SUCCESSOR_EXPECTS_ONE_QUOTE")
        current_pointer = store.get_object(self.graph_pointer_id)
        prior_snapshot_ref = str(current_pointer.payload["snapshot_ref"])
        prior_snapshot = WorkspaceGraphSnapshot.model_validate(
            store.load_artifact(prior_snapshot_ref, SNAPSHOT_MEDIA_TYPE).payload
        )
        next_version = self._next_version(current_pointer.version)
        prepared = self._prepare_successor(
            store=store,
            rebuilt=rebuilt_objects[0],
            prior_snapshot=prior_snapshot,
            next_version=next_version,
            committed_at=committed_at,
        )
        self._maybe_fail("prepared", self.fail_after)
        for write in prepared.successor_artifact_writes:
            if sha256_digest(write.payload) != write.payload_digest:
                raise IntegrityError(f"SUCCESSOR_ARTIFACT_DIGEST_MISMATCH:{write.artifact_id}")
            store.save_artifact(connection, write.artifact_id, write.media_type, write.payload)
            self._maybe_fail(f"artifact:{write.artifact_id}", self.fail_after)
        store.transition_current(connection, self.graph_pointer_id, ObjectState.SUPERSEDED)
        store.insert_version(connection, prepared.graph_pointer, make_current=True)
        self._maybe_fail("graph-pointer", self.fail_after)
        snapshot = WorkspaceGraphSnapshot.model_validate(
            store.load_artifact(prepared.successor_snapshot_ref, SNAPSHOT_MEDIA_TYPE).payload
        )
        if snapshot.digest != prepared.successor_snapshot_digest:
            raise IntegrityError("SUCCESSOR_SNAPSHOT_DIGEST_MISMATCH")
        receipt = WorkspaceRebaseReceipt(
            id=f"workspace-rebase:{change_set.id.split(':', 1)[-1]}@{change_set.revision}",
            base_rebase_receipt_ref=base_receipt.id,
            base_rebase_receipt_digest=base_receipt.digest,
            successor_object_refs=tuple(item.ref for item in prepared.successor_objects),
            successor_trace_refs=tuple(
                write.artifact_id
                for write in prepared.successor_artifact_writes
                if write.media_type == WORKSPACE_TRACE_MEDIA_TYPE
            ),
            successor_manifest_refs=tuple(
                write.artifact_id
                for write in prepared.successor_artifact_writes
                if write.media_type == RUNTIME_MANIFEST_MEDIA_TYPE
            ),
            successor_snapshot_ref=prepared.successor_snapshot_ref,
            successor_snapshot_digest=prepared.successor_snapshot_digest,
            graph_pointer_ref=prepared.graph_pointer.ref,
            committed_at=committed_at,
        )
        return receipt
