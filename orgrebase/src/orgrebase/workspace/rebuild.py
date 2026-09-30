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
    DiscountMemoInputAssembler,
    DiscountMemoRenderer,
    ExecutionReferenceMonitor,
    QuoteInputAssembler,
    QuoteRenderer,
    RuntimeDependencyCompiler,
    StaticClock,
    TraceCoverageVerifier,
    discount_memo_output_lineage,
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
    DeliverableApplyMember,
    DeliverableApprovalSet,
    DeliverableCandidateMember,
    DeliverableCandidateSet,
    DeliverableReviewProjection,
    DeliverableSetApplyReceipt,
    DeliverableSetBinding,
    DeliverableSetProfile,
    DiscountMemoPayload,
    DiscountMemoTaskLiterals,
    PreparedDeliverableCandidateSet,
    PreparedSuccessorEvidence,
    QuotePayload,
    QuotePricingComparison,
    QuoteTaskLiterals,
    RuntimeDependencyManifest,
    TaskContextManifest,
    TaskRequest,
    TaskTemplateVersion,
    TraceCoverageReceipt,
    WorkspaceGraphEdge,
    WorkspaceGraphSnapshot,
    WorkspaceRebaseReceipt,
    WorkspaceUniverse,
    WorkTrace,
)
from orgrebase.workspace.read_dependencies import ReadDependencyValidator, require_read_dependency_validator
from orgrebase.workspace.templates import TemplateRegistry

WORKSPACE_CONTEXT_MEDIA_TYPE = "application/vnd.orgrebase.task-context+json"
WORKSPACE_TRACE_MEDIA_TYPE = "application/vnd.orgrebase.work-trace+json"
WORKSPACE_COVERAGE_MEDIA_TYPE = "application/vnd.orgrebase.trace-coverage+json"
WORKSPACE_REBASE_RECEIPT_MEDIA_TYPE = "application/vnd.orgrebase.workspace-rebase-receipt+json"
DELIVERABLE_SET_APPLY_RECEIPT_MEDIA_TYPE = (
    "application/vnd.orgrebase.deliverable-set-apply-receipt+json"
)
DELIVERABLE_REVIEW_PROJECTION_MEDIA_TYPE = (
    "application/vnd.orgrebase.deliverable-review-projection+json"
)

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


def deliverable_slot_objects(template_ref: str) -> tuple[tuple[str, str], ...]:
    template = TemplateRegistry().get(template_ref)
    if template.deliverable_kind not in {"QUOTE", "DISCOUNT_MEMO"}:
        raise IntegrityError(f"DELIVERABLE_TEMPLATE_UNSUPPORTED:{template_ref}")
    return quote_slot_objects(template_ref)


def _successor_id_factory(task_slug: str, version: str):
    counter = {"value": 0}

    def factory(prefix: str) -> str:
        counter["value"] += 1
        return f"event:{task_slug}:{version}:{prefix}:{counter['value']:02d}"

    return factory

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

    def __init__(
        self,
        *,
        clock: WorkspaceWorkflowClock,
        read_dependency_validator: ReadDependencyValidator | None = None,
        template_ref: str = "template:enterprise_quote@v1",
        template_refs: Mapping[str, str] | None = None,
    ) -> None:
        self.clock = clock
        self.read_dependency_validator = read_dependency_validator
        self.template_ref = template_ref
        self.template_refs = {"QUOTE": template_ref, **dict(template_refs or {})}

    def current_premises(
        self,
        store: StateStore,
        now: str,
        *,
        deliverable_kind: str = "QUOTE",
    ) -> tuple[VersionedObject, ...]:
        try:
            template_ref = self.template_refs[deliverable_kind]
        except KeyError as exc:
            raise IntegrityError(
                f"WORKSPACE_CONTEXT_UNSUPPORTED_DELIVERABLE:{deliverable_kind}"
            ) from exc
        premises = tuple(
            store.get_object(item_id)
            for _slot_id, item_id in deliverable_slot_objects(template_ref)
        )
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
        candidate: bool = False,
    ) -> tuple[ContextManifest, ...]:
        manifests: list[ContextManifest] = []
        for result in affected:
            object_id = result.object_id
            current = store.get_object(object_id)
            deliverable_kind = str(current.payload.get("deliverable_kind"))
            if deliverable_kind not in self.template_refs:
                raise IntegrityError(f"WORKSPACE_CONTEXT_UNSUPPORTED_TARGET:{object_id}")
            premises = self.current_premises(
                store,
                self.clock.apply_at,
                deliverable_kind=deliverable_kind,
            )
            if candidate:
                proposed_by_id = {
                    item.object_id: store.get_object(item.object_id, item.proposed_version)
                    for item in change_set.deltas
                }
                premises = tuple(proposed_by_id.get(item.id, item) for item in premises)
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


class DiscountMemoRebuildPayloadHandler:
    """Pure, independently verified rebuild for the priced Memo v2 contract."""

    field_by_object_id: ClassVar[dict[str, str]] = {
        "policy:finance.currency": "currency",
        "claim:product.quote_basket": "quote_basket",
        "policy:finance.pricing": "pricing_policy",
    }

    def __init__(
        self,
        *,
        change_set_ref: str,
        change_set_digest: str,
        template_ref: str = "template:discount_exception_memo@v2",
    ) -> None:
        self.change_set_ref = change_set_ref
        self.change_set_digest = change_set_digest
        self.template_ref = template_ref

    def render_context(
        self,
        old: VersionedObject,
        context: ContextManifest,
    ) -> DiscountMemoPayload:
        values = {}
        for slot, object_id in deliverable_slot_objects(self.template_ref):
            matches = [
                item
                for item in context.included
                if item.object_ref.rsplit("@", 1)[0] == object_id
            ]
            if (
                len(matches) != 1
                or matches[0].payload is None
                or "value" not in matches[0].payload
            ):
                raise IntegrityError(f"DISCOUNT_MEMO_REBUILD_SOURCE_CONTEXT_MISSING:{slot}")
            values[slot] = matches[0].payload["value"]
        return DiscountMemoRenderer().render(
            task_literals=DiscountMemoTaskLiterals(
                owner=str(old.payload["owner"]),
                customer_id=str(old.payload["customer_id"]),
                quote_object_id=str(old.payload["quote_object_id"]),
            ),
            inputs=DiscountMemoInputAssembler.from_values(values),
            change_set_ref=self.change_set_ref,
            change_set_digest=self.change_set_digest,
            previous_pricing=DiscountMemoPayload.model_validate(old.payload).pricing,
        )

    def rebuild_payload(
        self,
        *,
        old: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> Mapping[str, object]:
        if not deltas or len({item.object_id for item in deltas}) != len(deltas):
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_SOURCE_SET_INVALID")
        if any(item.object_id not in self.field_by_object_id for item in deltas):
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_UNSUPPORTED_DELTA")
        payload = self.render_context(old, context).model_dump(mode="json")
        payload["rebased_from"] = old.ref
        payload["context_manifest"] = context.digest
        candidate = DiscountMemoPayload.model_validate(payload)
        self.verify_payload(
            old=old,
            new_payload=candidate.model_dump(mode="json"),
            deltas=deltas,
            context=context,
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
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_FORBIDDEN_SENSITIVE_FIELD")
        before = DiscountMemoPayload.model_validate(old.payload)
        after = DiscountMemoPayload.model_validate(dict(new_payload))
        rendered = self.render_context(old, context)
        if any(item.object_id not in self.field_by_object_id for item in deltas):
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_UNSUPPORTED_DELTA")
        for delta in deltas:
            if not any(
                item.object_ref == f"{delta.object_id}@{delta.proposed_version}"
                and item.payload is not None
                and item.payload.get("value") == delta.proposed_value
                for item in context.included
            ):
                raise IntegrityError("DISCOUNT_MEMO_REBUILD_SOURCE_CONTEXT_MISMATCH")
        for field in ("owner", "customer_id", "quote_object_id"):
            if getattr(after, field) != getattr(before, field):
                raise IntegrityError(f"DISCOUNT_MEMO_REBUILD_PRESERVATION_VIOLATION:{field}")
        if after.pricing != rendered.pricing or after.pricing_basis_digest != rendered.pricing_basis_digest:
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_PRICING_MISMATCH")
        if (
            after.previous_pricing != rendered.previous_pricing
            or after.comparison != rendered.comparison
            or after.reason_refs != rendered.reason_refs
            or after.limitations != rendered.limitations
        ):
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_COMPARISON_MISMATCH")
        if (
            after.last_price_change_set_ref != self.change_set_ref
            or after.last_price_change_set_digest != self.change_set_digest
        ):
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_CHANGE_BINDING_MISMATCH")
        if after.rebased_from != old.ref:
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_MISSING_PREDECESSOR")
        if after.context_manifest != context.digest:
            raise IntegrityError("DISCOUNT_MEMO_REBUILD_CONTEXT_BINDING_MISMATCH")

    def verify_committed(
        self,
        *,
        old: VersionedObject,
        rebuilt: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> None:
        self.verify_payload(
            old=old,
            new_payload=rebuilt.payload,
            deltas=deltas,
            context=context,
        )


def _deliverable_evidence_scope(change_set_digest: str, preview_digest: str) -> str:
    return sha256_digest({"change_set_digest": change_set_digest, "preview_digest": preview_digest})


def _scoped_evidence_id(identifier: str, evidence_scope: str | None) -> str:
    if evidence_scope is None:
        return identifier
    return f"{identifier}-changeset-{evidence_scope.removeprefix('sha256:')}"


def _scope_runtime_manifest(
    manifest: RuntimeDependencyManifest, evidence_scope: str | None,
) -> RuntimeDependencyManifest:
    if evidence_scope is None:
        return manifest
    return RuntimeDependencyManifest.model_validate({
        **manifest.model_dump(mode="json", exclude={"digest"}),
        "id": _scoped_evidence_id(manifest.id, evidence_scope),
    })


def _workspace_task_context(
    *,
    store: StateStore,
    template: TaskTemplateVersion,
    deliverable: VersionedObject,
    version: str,
    now: str,
    revisions: Mapping[str, str],
    base_task: TaskRequest | None = None,
    source_overrides: Mapping[str, VersionedObject] | None = None,
    evidence_scope: str | None = None,
) -> tuple[TaskRequest, TaskContextManifest, ActorContextProjection]:
    selected = base_task
    deliverable_kind = str(deliverable.payload.get("deliverable_kind"))
    default_purpose = (
        "enterprise_quote" if deliverable_kind == "QUOTE" else "discount_exception_memo"
    )
    input_values = {"owner": deliverable.payload["owner"]}
    if deliverable_kind == "DISCOUNT_MEMO":
        input_values["quote_object_id"] = deliverable.payload["quote_object_id"]
    task = TaskRequest(
        id=_scoped_evidence_id(
            selected.id if selected is not None else f"task:{deliverable.id.split(':')[-1]}",
            evidence_scope,
        ),
        organization_id=(selected.organization_id if selected is not None else "org:northstar"),
        actor_id=(selected.actor_id if selected is not None else "employee:sales-owner"),
        purpose=(selected.purpose if selected is not None else default_purpose),
        deliverable_kind=(selected.deliverable_kind if selected is not None else deliverable_kind),
        requested_at=now,
        template_ref=(selected.template_ref if selected is not None else template.ref),
        input_values=(selected.input_values if selected is not None else input_values),
        customer_id=deliverable.payload["customer_id"],
        idempotency_key=f"workspace:successor:{deliverable.ref}",
    )
    slot_specs = {item.slot_id: item for item in template.slots}
    bindings: list[AdmittedReference] = []
    for slot_id, object_id in deliverable_slot_objects(template.ref):
        item = (source_overrides or {}).get(object_id) or store.get_object(object_id)
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
                purpose=task.purpose,
                recipient_ids=("workspace-renderer",),
                expires_at=timestamp(utc_datetime(now) + timedelta(seconds=900)),
                admission_decision_ref=f"admission:successor:{deliverable.ref}:{slot_id}",
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
        purpose=task.purpose,
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
            deliverable=rebuilt,
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


def build_deliverable_candidate_set(
    *,
    store: StateStore,
    fixture: object,
    profile: DeliverableSetProfile,
    binding: DeliverableSetBinding,
    change_set: ChangeSetRevision,
    preview: ImpactPreview,
    snapshot: WorkspaceGraphSnapshot,
    context_provider: WorkspaceRebuildContextProvider,
    handlers: Mapping[str, object],
    dispositions: Mapping[str, str],
    task_requests: Mapping[str, TaskRequest],
    graph_pointer_id: str,
    run_id: str | None = None,
) -> PreparedDeliverableCandidateSet:
    """Prepare an exact, side-effect-free candidate set for output approval."""

    members_by_id = {item.object_id: item for item in profile.members}
    if set(members_by_id) != {item.object_id for item in binding.members}:
        raise IntegrityError("DELIVERABLE_SET_BINDING_MEMBER_MISMATCH")
    result_by_id = {item.object_id: item for item in preview.results}
    if not set(members_by_id) <= set(result_by_id):
        raise IntegrityError("DELIVERABLE_SET_PREVIEW_MEMBER_MISSING")
    affected = tuple(
        result_by_id[object_id]
        for object_id in sorted(members_by_id)
        if dispositions.get(object_id) == "REBUILD"
    )
    contexts = context_provider.prepare(
        fixture=fixture,
        store=store,
        change_set=change_set,
        preview=preview,
        affected=affected,
        candidate=True,
    )
    context_by_id = {item.target_object_id: item for item in contexts}
    runtime_by_consumer: dict[str, RuntimeDependencyManifest] = {}
    for manifest_ref in snapshot.manifest_refs:
        try:
            stored = store.load_artifact(manifest_ref, RUNTIME_MANIFEST_MEDIA_TYPE)
        except KeyError:
            continue
        manifest = RuntimeDependencyManifest.model_validate(stored.payload)
        runtime_by_consumer[manifest.consumer_ref] = manifest

    pointer = store.get_object(graph_pointer_id)
    next_version = WorkspaceGraphApplyExtension._next_version(pointer.version)
    revisions = {
        **snapshot.revisions,
        "graph": f"{graph_pointer_id}@{next_version}",
        "runtime_registry": f"runtime:workspace@{next_version}",
    }
    source_overrides = {
        item.object_id: store.get_object(item.object_id, item.proposed_version)
        for item in change_set.deltas
    }
    evidence_scope = _deliverable_evidence_scope(change_set.digest, preview.digest)
    candidate_members = []
    evidence_writes = []
    for object_id in sorted(members_by_id):
        member = members_by_id[object_id]
        predecessor = store.get_object(object_id)
        disposition = dispositions.get(object_id)
        if disposition not in {
            "REBUILD",
            "PRESERVE_WITHIN_BOUNDARY",
            "HOLD_FOR_REVIEW",
        }:
            raise IntegrityError(f"DELIVERABLE_SET_DISPOSITION_INVALID:{object_id}")
        if disposition == "REBUILD":
            handler = handlers.get(member.deliverable_kind)
            context = context_by_id.get(object_id)
            if handler is None or context is None:
                raise IntegrityError(f"DELIVERABLE_SET_HANDLER_MISSING:{member.deliverable_kind}")
            candidate_payload = dict(
                handler.rebuild_payload(  # type: ignore[attr-defined]
                    old=predecessor,
                    deltas=change_set.deltas,
                    context=context,
                )
            )
            if not predecessor.version.startswith("v") or not predecessor.version[1:].isdigit():
                raise IntegrityError("DELIVERABLE_SET_VERSION_UNSUPPORTED")
            candidate_ref = f"{object_id}@v{int(predecessor.version[1:]) + 1}"
            scope_by_source = {
                "claim:product.launch_date": "quote.launch_date",
                "claim:product.enterprise_plan": "quote.product_plan",
                "claim:product.quote_basket": "quote.pricing",
                "policy:finance.pricing": "quote.pricing",
                "policy:finance.currency": "quote.pricing",
            }
            if member.deliverable_kind == "DISCOUNT_MEMO":
                required_scopes = ("discount_memo.pricing",)
            else:
                required_scopes = tuple(
                    sorted(
                        {
                            scope_by_source[item.object_id]
                            for item in change_set.deltas
                            if item.object_id in scope_by_source
                        }
                    )
                )
            if not set(required_scopes) <= set(member.approval_scopes):
                raise IntegrityError("DELIVERABLE_CANDIDATE_SCOPE_UNADMITTED")
            candidate_object = VersionedObject.model_validate(
                {
                    **predecessor.model_dump(mode="json", exclude={"digest"}),
                    "version": candidate_ref.rsplit("@", 1)[1],
                    "state": "CURRENT",
                    "payload": candidate_payload,
                    "valid_from": context_provider.clock.apply_at,
                }
            )
            template = TemplateRegistry().get(member.template_ref)
            task, task_context, projection = _workspace_task_context(
                store=store,
                template=template,
                deliverable=candidate_object,
                version=next_version,
                now=context_provider.clock.apply_at,
                revisions=revisions,
                base_task=task_requests.get(member.deliverable_kind),
                source_overrides=source_overrides,
                evidence_scope=evidence_scope,
            )
            task_slug = task.id.split(":")[-1]
            monitor = ExecutionReferenceMonitor(
                task=task,
                template=template,
                manifest=task_context,
                artifact_reader=store.load_artifact,
                actor_id="workspace-renderer",
                run_id=(run_id or f"run:workspace:{task_slug}@{next_version}"),
                clock=StaticClock(context_provider.clock.apply_at),
                id_factory=_successor_id_factory(task_slug, next_version),
                trace_version=next_version,
            )
            if member.deliverable_kind == "QUOTE":
                rendered = QuoteRenderer().render(
                    task_literals=QuoteTaskLiterals(
                        owner=str(candidate_payload["owner"]),
                        customer_id=str(candidate_payload["customer_id"]),
                    ),
                    inputs=QuoteInputAssembler().assemble(monitor),
                )
                lineage = quote_output_lineage(priced=rendered.pricing is not None)
                metadata = {"rebased_from", "rebase_change_set", "context_manifest"}
            else:
                rendered = DiscountMemoRenderer().render(
                    task_literals=DiscountMemoTaskLiterals(
                        owner=str(candidate_payload["owner"]),
                        customer_id=str(candidate_payload["customer_id"]),
                        quote_object_id=str(candidate_payload["quote_object_id"]),
                    ),
                    inputs=DiscountMemoInputAssembler().assemble(monitor),
                    change_set_ref=f"{change_set.id}@{change_set.revision}",
                    change_set_digest=change_set.digest,
                    previous_pricing=DiscountMemoPayload.model_validate(
                        predecessor.payload
                    ).pricing,
                )
                lineage = discount_memo_output_lineage()
                metadata = {"rebased_from", "context_manifest"}
            rendered_payload = rendered.model_dump(mode="json")
            for field in (set(rendered_payload) | set(candidate_payload)) - metadata:
                if rendered_payload.get(field) != candidate_payload.get(field):
                    raise IntegrityError(
                        f"DELIVERABLE_CANDIDATE_RENDER_MISMATCH:{object_id}:{field}"
                    )
            trace = monitor.finish(
                output_ref=candidate_ref,
                output_payload=candidate_payload,
                output_field_lineage=lineage,
            )
            coverage = TraceCoverageVerifier().verify(
                template=template,
                trace=trace,
                output_payload=candidate_payload,
                observed_channels=("REFERENCE_MONITOR",),
            )
            manifest = RuntimeDependencyCompiler().compile(
                task=task,
                template=template,
                trace=trace,
                coverage=coverage,
                consumer_ref=candidate_ref,
                consumer_domain=predecessor.domain,
                revision_lock=task_context.revision_lock,
                now=context_provider.clock.apply_at,
                version=next_version,
            )
            manifest = _scope_runtime_manifest(manifest, evidence_scope)
            evidence_mode = "CANDIDATE"
            evidence_writes.extend(
                (
                    prepare_artifact_write(
                        task_context.ref, WORKSPACE_CONTEXT_MEDIA_TYPE, task_context
                    ),
                    prepare_artifact_write(
                        projection.ref,
                        "application/vnd.orgrebase.actor-context-projection+json",
                        projection,
                    ),
                    prepare_artifact_write(trace.ref, WORKSPACE_TRACE_MEDIA_TYPE, trace),
                    prepare_artifact_write(
                        coverage.id, WORKSPACE_COVERAGE_MEDIA_TYPE, coverage
                    ),
                    prepare_artifact_write(
                        manifest.ref, RUNTIME_MANIFEST_MEDIA_TYPE, manifest
                    ),
                )
            )
        else:
            candidate_payload = predecessor.payload
            candidate_ref = predecessor.ref
            required_scopes = ()
            manifest = runtime_by_consumer.get(predecessor.ref)
            if manifest is None:
                raise IntegrityError(
                    f"DELIVERABLE_SET_PREDECESSOR_MANIFEST_MISSING:{object_id}"
                )
            trace_artifact = store.load_artifact(
                manifest.trace_ref, WORKSPACE_TRACE_MEDIA_TYPE
            )
            trace = WorkTrace.model_validate(trace_artifact.payload)
            context_artifact = store.load_artifact(
                trace.context_manifest_ref, WORKSPACE_CONTEXT_MEDIA_TYPE
            )
            task_context = TaskContextManifest.model_validate(context_artifact.payload)
            coverage_artifact = store.load_artifact(
                manifest.coverage_receipt_ref, WORKSPACE_COVERAGE_MEDIA_TYPE
            )
            coverage = TraceCoverageReceipt.model_validate(coverage_artifact.payload)
            evidence_mode = (
                "PREDECESSOR_PRESERVED"
                if disposition == "PRESERVE_WITHIN_BOUNDARY"
                else "PREDECESSOR_UNRESOLVED"
            )
        safe_fields = (
            {
                "owner",
                "deliverable_kind",
                "customer_id",
                "product_plan",
                "launch_date",
                "data_residency",
                "notice_required",
                "price_band",
                "currency",
                "partner_terms_code",
                "pricing",
            }
            if member.deliverable_kind == "QUOTE"
            else {
                "owner",
                "deliverable_kind",
                "customer_id",
                "quote_object_id",
                "pricing",
                "pricing_basis_digest",
                "previous_pricing",
                "comparison",
                "reason_refs",
                "limitations",
                "last_price_change_set_ref",
                "last_price_change_set_digest",
            }
        )
        review = DeliverableReviewProjection(
            id=(
                "deliverable-review:"
                f"{change_set.id.split(':', 1)[-1]}:{object_id.split(':')[-1]}"
                f"@{change_set.revision}"
            ),
            candidate_ref=candidate_ref,
            candidate_payload_digest=sha256_digest(candidate_payload),
            deliverable_kind=member.deliverable_kind,
            owner_id=member.owner_id,
            change_set_digest=change_set.digest,
            binding_digest=binding.digest,
            safe_payload={
                key: value for key, value in candidate_payload.items() if key in safe_fields
            },
            forbidden_fields_checked=tuple(
                sorted({"raw_contract_text", "internal_cost_floor", "secret", "private_source"})
            ),
        )
        evidence_writes.append(
            prepare_artifact_write(
                review.id, DELIVERABLE_REVIEW_PROJECTION_MEDIA_TYPE, review
            )
        )
        candidate_members.append(
            DeliverableCandidateMember(
                object_id=object_id,
                deliverable_kind=member.deliverable_kind,
                disposition=disposition,
                predecessor_ref=predecessor.ref,
                predecessor_digest=predecessor.digest,
                candidate_ref=candidate_ref,
                candidate_payload_digest=sha256_digest(candidate_payload),
                evidence_mode=evidence_mode,
                review_projection_ref=review.id,
                review_projection_digest=review.digest,
                context_ref=task_context.ref,
                context_digest=task_context.digest,
                trace_ref=trace.ref,
                trace_digest=trace.digest,
                coverage_ref=coverage.id,
                coverage_digest=coverage.digest,
                manifest_ref=manifest.ref,
                manifest_digest=manifest.digest,
                owner_id=member.owner_id,
                required_scopes=required_scopes,
                reason_code=result_by_id[object_id].reason_code,
            )
        )
    state = (
        "UNKNOWN"
        if any(item.disposition == "HOLD_FOR_REVIEW" for item in candidate_members)
        else "READY"
    )
    candidate_set = DeliverableCandidateSet(
        id=f"deliverable-candidate-set:{change_set.id.split(':', 1)[-1]}@{change_set.revision}",
        change_set_ref=f"{change_set.id}@{change_set.revision}",
        change_set_digest=change_set.digest,
        preview_digest=preview.digest,
        binding_ref=binding.ref,
        binding_digest=binding.digest,
        runtime_revision=profile.runtime_revision,
        snapshot_ref=snapshot.ref,
        snapshot_digest=snapshot.digest,
        members=tuple(candidate_members),
        state=state,
    )
    return PreparedDeliverableCandidateSet(
        candidate_set=candidate_set,
        artifact_writes=tuple(evidence_writes),
    )


class DeliverableSetGraphApplyExtension:
    """Promote a bounded Quote+Memo set and its evidence in the core transaction."""

    def __init__(
        self,
        *,
        profile: DeliverableSetProfile,
        binding: DeliverableSetBinding,
        candidate_set: DeliverableCandidateSet,
        approval_set: DeliverableApprovalSet,
        authority_revisions: Mapping[str, str],
        task_requests: Mapping[str, TaskRequest],
        graph_pointer_id: str,
        graph_snapshot_id: str,
        snapshot_scope_roots: tuple[str, ...],
        run_id: str | None = None,
        fail_after: str | None = None,
    ) -> None:
        self.profile = DeliverableSetProfile.model_validate(profile.model_dump(mode="json"))
        self.binding = DeliverableSetBinding.model_validate(binding.model_dump(mode="json"))
        self.candidate_set = DeliverableCandidateSet.model_validate(
            candidate_set.model_dump(mode="json")
        )
        self.approval_set = DeliverableApprovalSet.model_validate(
            approval_set.model_dump(mode="json")
        )
        self.authority_revisions = dict(authority_revisions)
        self.task_requests = dict(task_requests)
        self.graph_pointer_id = graph_pointer_id
        self.graph_snapshot_id = graph_snapshot_id
        self.snapshot_scope_roots = tuple(snapshot_scope_roots)
        self.run_id = run_id
        self.fail_after = fail_after
        self.snapshot_builder = WorkspaceSnapshotBuilder()
        self.coverage_verifier = TraceCoverageVerifier()
        self.dependency_compiler = RuntimeDependencyCompiler()
        if (
            self.binding.profile_ref != self.profile.ref
            or self.binding.profile_digest != self.profile.digest
            or self.binding.members != self.profile.members
            or self.candidate_set.binding_ref != self.binding.ref
            or self.candidate_set.binding_digest != self.binding.digest
            or self.candidate_set.runtime_revision != self.profile.runtime_revision
        ):
            raise IntegrityError("DELIVERABLE_SET_RUNTIME_BINDING_INVALID")

    @staticmethod
    def _next_version(version: str) -> str:
        return WorkspaceGraphApplyExtension._next_version(version)

    @staticmethod
    def _maybe_fail(point: str, configured: str | None) -> None:
        WorkspaceGraphApplyExtension._maybe_fail(point, configured)

    def _verify_approval_set(self, applied_at: str) -> None:
        if self.candidate_set.state != "READY":
            raise IntegrityError("DELIVERABLE_SET_UNKNOWN_BLOCKS_APPLY")
        if (
            self.approval_set.status != "COMPLETE"
            or self.approval_set.candidate_set_digest != self.candidate_set.digest
        ):
            raise IntegrityError("DELIVERABLE_APPROVAL_SET_INCOMPLETE")
        required: dict[str, set[str]] = {}
        for member in self.candidate_set.members:
            if member.disposition == "REBUILD":
                required.setdefault(member.owner_id, set()).update(member.required_scopes)
        decisions = {item.owner_id: item for item in self.approval_set.decisions}
        if set(decisions) != set(required):
            raise IntegrityError("DELIVERABLE_APPROVAL_OWNER_COVERAGE_INVALID")
        observed = utc_datetime(applied_at)
        for owner_id, scopes in required.items():
            decision = decisions[owner_id]
            if (
                decision.actor_id != owner_id
                or decision.decision != "APPROVED"
                or set(decision.scopes) != scopes
                or decision.authority_revision != self.authority_revisions.get(owner_id)
                or not utc_datetime(decision.approved_at)
                <= observed
                < utc_datetime(decision.expires_at)
            ):
                raise IntegrityError("DELIVERABLE_APPROVAL_AUTHORITY_INVALID")

    def _member_evidence(
        self,
        *,
        store: StateStore,
        rebuilt: VersionedObject,
        change_set: ChangeSetRevision,
        next_version: str,
        committed_at: str,
        revisions: Mapping[str, str],
    ) -> tuple[tuple[object, ...], RuntimeDependencyManifest]:
        kind = str(rebuilt.payload.get("deliverable_kind"))
        member = next(
            item for item in self.profile.members if item.deliverable_kind == kind
        )
        template = TemplateRegistry().get(member.template_ref)
        if template.digest != member.template_digest:
            raise IntegrityError("DELIVERABLE_SET_TEMPLATE_DRIFT")
        evidence_scope = _deliverable_evidence_scope(change_set.digest, self.candidate_set.preview_digest)
        task, context, projection = _workspace_task_context(
            store=store,
            template=template,
            deliverable=rebuilt,
            version=next_version,
            now=committed_at,
            revisions=revisions,
            base_task=self.task_requests.get(kind),
            evidence_scope=evidence_scope,
        )
        task_slug = task.id.split(":")[-1]
        counter = {"value": 0}

        def id_factory(prefix: str) -> str:
            counter["value"] += 1
            return f"event:{task_slug}:{next_version}:{prefix}:{counter['value']:02d}"

        monitor = ExecutionReferenceMonitor(
            task=task,
            template=template,
            manifest=context,
            artifact_reader=store.load_artifact,
            actor_id="workspace-renderer",
            run_id=(self.run_id or f"run:workspace:{task_slug}@{next_version}"),
            clock=StaticClock(committed_at),
            id_factory=id_factory,
            trace_version=next_version,
        )
        if kind == "QUOTE":
            rendered = QuoteRenderer().render(
                task_literals=QuoteTaskLiterals(
                    owner=str(rebuilt.payload["owner"]),
                    customer_id=str(rebuilt.payload["customer_id"]),
                ),
                inputs=QuoteInputAssembler().assemble(monitor),
            )
            actual = QuotePayload.model_validate(rebuilt.payload)
            lineage = quote_output_lineage(priced=rendered.pricing is not None)
            metadata = {"rebased_from", "rebase_change_set", "context_manifest"}
        elif kind == "DISCOUNT_MEMO":
            predecessor_ref = str(rebuilt.payload.get("rebased_from", ""))
            if not predecessor_ref:
                raise IntegrityError("DISCOUNT_MEMO_SUCCESSOR_PREDECESSOR_MISSING")
            predecessor = store.get_object(*split_ref(predecessor_ref))
            rendered = DiscountMemoRenderer().render(
                task_literals=DiscountMemoTaskLiterals(
                    owner=str(rebuilt.payload["owner"]),
                    customer_id=str(rebuilt.payload["customer_id"]),
                    quote_object_id=str(rebuilt.payload["quote_object_id"]),
                ),
                inputs=DiscountMemoInputAssembler().assemble(monitor),
                change_set_ref=f"{change_set.id}@{change_set.revision}",
                change_set_digest=change_set.digest,
                previous_pricing=DiscountMemoPayload.model_validate(
                    predecessor.payload
                ).pricing,
            )
            actual = DiscountMemoPayload.model_validate(rebuilt.payload)
            lineage = discount_memo_output_lineage()
            metadata = {"rebased_from", "context_manifest"}
        else:  # pragma: no cover - profile validator closes this branch
            raise IntegrityError(f"DELIVERABLE_SET_KIND_UNSUPPORTED:{kind}")
        rendered_body = rendered.model_dump(mode="json")
        actual_body = actual.model_dump(mode="json")
        for field in (set(rendered_body) | set(actual_body)) - metadata:
            if rendered_body.get(field) != actual_body.get(field):
                raise IntegrityError(f"SUCCESSOR_RENDER_MISMATCH:{kind}:{field}")
        trace = monitor.finish(
            output_ref=rebuilt.ref,
            output_payload=rebuilt.payload,
            output_field_lineage=lineage,
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
            revision_lock=context.revision_lock,
            now=committed_at,
            version=next_version,
        )
        manifest = _scope_runtime_manifest(manifest, evidence_scope)
        return (context, projection, trace, coverage), manifest

    def _prepare(
        self,
        *,
        store: StateStore,
        rebuilt_objects: tuple[VersionedObject, ...],
        change_set: ChangeSetRevision,
        prior_snapshot: WorkspaceGraphSnapshot,
        next_version: str,
        committed_at: str,
    ) -> PreparedSuccessorEvidence:
        revisions = {
            **prior_snapshot.revisions,
            "graph": f"{self.graph_pointer_id}@{next_version}",
            "runtime_registry": f"runtime:workspace@{next_version}",
        }
        evidence: list[tuple[object, ...]] = []
        manifests: list[RuntimeDependencyManifest] = []
        for rebuilt in sorted(rebuilt_objects, key=lambda item: item.id):
            artifacts, manifest = self._member_evidence(
                store=store,
                rebuilt=rebuilt,
                change_set=change_set,
                next_version=next_version,
                committed_at=committed_at,
                revisions=revisions,
            )
            evidence.append(artifacts)
            manifests.append(manifest)

        prior_universe = WorkspaceUniverse.model_validate(
            store.load_artifact(prior_snapshot.universe_id, UNIVERSE_MEDIA_TYPE).payload
        )
        rebuilt_ids = {item.id for item in rebuilt_objects}
        prior_objects = [store.get_object(*split_ref(ref)) for ref in prior_snapshot.object_refs]
        required_refs = {
            edge.provider_ref
            for edge in prior_snapshot.edges
            if split_ref(edge.consumer_ref)[0] not in rebuilt_ids
        }
        objects_by_ref = {
            item.ref: item
            for item in prior_objects
            if item.id not in rebuilt_ids
            and (item.ref in required_refs or item.state == ObjectState.PROPOSED)
        }
        for item in prior_objects:
            if item.id in rebuilt_ids:
                continue
            current = store.get_object(item.id)
            objects_by_ref[current.ref] = current
        for item in rebuilt_objects:
            objects_by_ref[item.ref] = item
        current_objects = tuple(sorted(objects_by_ref.values(), key=lambda item: item.ref))

        current_ref_by_id = {
            item.id: store.get_object(item.id).ref
            for item in current_objects
            if item.state
            in {
                ObjectState.CURRENT,
                ObjectState.ACTIVE,
                ObjectState.CANARY,
                ObjectState.REVIEW_REQUIRED,
            }
        }
        edges: list[WorkspaceGraphEdge] = []
        for edge in prior_snapshot.edges:
            consumer_id, _ = split_ref(edge.consumer_ref)
            if consumer_id in rebuilt_ids:
                continue
            body = edge.model_dump(mode="json", exclude={"digest"})
            body["consumer_ref"] = current_ref_by_id.get(consumer_id, edge.consumer_ref)
            edges.append(WorkspaceGraphEdge.model_validate(body))

        preserved_runtime: list[RuntimeDependencyManifest] = []
        for manifest_ref in prior_snapshot.manifest_refs:
            try:
                artifact = store.load_artifact(manifest_ref, RUNTIME_MANIFEST_MEDIA_TYPE)
            except KeyError:
                continue
            manifest = RuntimeDependencyManifest.model_validate(artifact.payload)
            if split_ref(manifest.consumer_ref)[0] not in rebuilt_ids:
                preserved_runtime.append(manifest)
        imported = tuple(
            _rebind_imported_manifest(
                item,
                target_version=store.get_object(item.target_id).version,
            )
            for item in prior_universe.imported_manifests
        )
        universe = WorkspaceUniverse(
            id=f"{prior_universe.universe_id}@r{next_version[1:]}",
            revision=f"r{next_version[1:]}",
            organization_id=prior_universe.organization_id,
            universe_id=prior_universe.universe_id,
            current_objects=current_objects,
            task_artifact_projections=prior_universe.task_artifact_projections,
            edges=tuple(sorted(edges, key=lambda item: item.id)),
            runtime_manifests=tuple(
                sorted((*preserved_runtime, *manifests), key=lambda item: item.ref)
            ),
            imported_manifests=imported,
            target_ids=tuple(
                sorted(set(prior_universe.target_ids) | {item.object_id for item in self.profile.members})
            ),
            source_refs=tuple(
                sorted({prior_snapshot.ref, *(item.trace_ref for item in manifests)})
            ),
            completeness_basis=prior_universe.completeness_basis,
            built_at=committed_at,
        )
        snapshot = self.snapshot_builder.build(
            universe=universe,
            replacement_objects=tuple(rebuilt_objects),
            replacement_manifests=tuple(manifests),
            targets=universe.target_ids,
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
        writes = []
        for artifacts, manifest in zip(evidence, manifests, strict=True):
            context, projection, trace, coverage = artifacts
            writes.extend(
                (
                    prepare_artifact_write(context.ref, WORKSPACE_CONTEXT_MEDIA_TYPE, context),
                    prepare_artifact_write(
                        projection.ref,
                        "application/vnd.orgrebase.actor-context-projection+json",
                        projection,
                    ),
                    prepare_artifact_write(trace.ref, WORKSPACE_TRACE_MEDIA_TYPE, trace),
                    prepare_artifact_write(coverage.id, WORKSPACE_COVERAGE_MEDIA_TYPE, coverage),
                    prepare_artifact_write(manifest.ref, RUNTIME_MANIFEST_MEDIA_TYPE, manifest),
                )
            )
        writes.extend(
            (
                prepare_artifact_write(universe.id, UNIVERSE_MEDIA_TYPE, universe),
                prepare_artifact_write(snapshot.ref, SNAPSHOT_MEDIA_TYPE, snapshot),
            )
        )
        return PreparedSuccessorEvidence(
            successor_objects=tuple(sorted(rebuilt_objects, key=lambda item: item.id)),
            successor_artifact_writes=tuple(writes),
            graph_pointer=pointer,
            successor_snapshot_ref=snapshot.ref,
            successor_snapshot_digest=snapshot.digest,
        )

    def _batch_receipt(
        self,
        *,
        store: StateStore,
        base_receipt: RebaseReceipt,
        prepared: PreparedSuccessorEvidence,
        committed_at: str,
    ) -> DeliverableSetApplyReceipt:
        rebuilt = {item.id: item for item in prepared.successor_objects}
        candidate_members = {item.object_id: item for item in self.candidate_set.members}
        results = []
        for member in self.profile.members:
            candidate = candidate_members[member.object_id]
            if candidate.disposition == "HOLD_FOR_REVIEW":
                raise IntegrityError("DELIVERABLE_SET_UNKNOWN_BLOCKS_APPLY")
            result = rebuilt.get(member.object_id) or store.get_object(member.object_id)
            if candidate.candidate_ref != result.ref:
                raise IntegrityError("DELIVERABLE_CANDIDATE_RESULT_REF_MISMATCH")
            if candidate.candidate_payload_digest != sha256_digest(result.payload):
                raise IntegrityError("DELIVERABLE_CANDIDATE_RESULT_DIGEST_MISMATCH")
            results.append(
                DeliverableApplyMember(
                    object_id=member.object_id,
                    deliverable_kind=member.deliverable_kind,
                    disposition=candidate.disposition,
                    predecessor_ref=candidate.predecessor_ref,
                    result_ref=result.ref,
                    result_digest=result.digest,
                    reason_code=candidate.reason_code,
                )
            )
        return DeliverableSetApplyReceipt(
            id=f"deliverable-set-apply:{base_receipt.change_set_ref.replace('@', ':')}",
            base_rebase_receipt_ref=base_receipt.id,
            base_rebase_receipt_digest=base_receipt.digest,
            binding_ref=self.binding.ref,
            binding_digest=self.binding.digest,
            candidate_set_digest=self.candidate_set.digest,
            approval_set_digest=self.approval_set.digest,
            members=tuple(results),
            graph_pointer_ref=prepared.graph_pointer.ref,
            snapshot_ref=prepared.successor_snapshot_ref,
            snapshot_digest=prepared.successor_snapshot_digest,
            committed_at=committed_at,
        )

    def _verify_prepared_candidate_evidence(
        self,
        prepared: PreparedSuccessorEvidence,
    ) -> None:
        writes = {item.artifact_id: item for item in prepared.successor_artifact_writes}
        for member in self.candidate_set.members:
            if member.disposition != "REBUILD":
                continue
            expected = (
                (member.context_ref, member.context_digest, TaskContextManifest),
                (member.trace_ref, member.trace_digest, WorkTrace),
                (member.coverage_ref, member.coverage_digest, TraceCoverageReceipt),
                (member.manifest_ref, member.manifest_digest, RuntimeDependencyManifest),
            )
            for artifact_id, digest, model_type in expected:
                write = writes.get(artifact_id)
                if write is None:
                    raise IntegrityError(
                        f"DELIVERABLE_CANDIDATE_APPLY_EVIDENCE_MISSING:{artifact_id}"
                    )
                model = model_type.model_validate(write.payload)
                if model.digest != digest:
                    raise IntegrityError(
                        f"DELIVERABLE_CANDIDATE_APPLY_EVIDENCE_MISMATCH:{artifact_id}"
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
    ) -> WorkspaceRebaseReceipt:
        del preview, deltas, contexts
        self._verify_approval_set(committed_at)
        expected_rebuild_ids = {
            item.object_id
            for item in self.candidate_set.members
            if item.disposition == "REBUILD"
        }
        if {item.id for item in rebuilt_objects} != expected_rebuild_ids:
            raise IntegrityError("DELIVERABLE_SET_REBUILD_MEMBER_MISMATCH")
        current_pointer = store.get_object(self.graph_pointer_id)
        prior_snapshot = WorkspaceGraphSnapshot.model_validate(
            store.load_artifact(
                str(current_pointer.payload["snapshot_ref"]),
                SNAPSHOT_MEDIA_TYPE,
            ).payload
        )
        if (
            self.candidate_set.change_set_ref != f"{change_set.id}@{change_set.revision}"
            or self.candidate_set.change_set_digest != change_set.digest
            or self.candidate_set.snapshot_ref != prior_snapshot.ref
            or self.candidate_set.snapshot_digest != prior_snapshot.digest
        ):
            raise IntegrityError("DELIVERABLE_CANDIDATE_SET_STALE")
        next_version = self._next_version(current_pointer.version)
        prepared = self._prepare(
            store=store,
            rebuilt_objects=rebuilt_objects,
            change_set=change_set,
            prior_snapshot=prior_snapshot,
            next_version=next_version,
            committed_at=committed_at,
        )
        self._verify_prepared_candidate_evidence(prepared)
        self._maybe_fail("prepared", self.fail_after)
        for write in prepared.successor_artifact_writes:
            if sha256_digest(write.payload) != write.payload_digest:
                raise IntegrityError(f"SUCCESSOR_ARTIFACT_DIGEST_MISMATCH:{write.artifact_id}")
            store.save_artifact(connection, write.artifact_id, write.media_type, write.payload)
            self._maybe_fail(f"artifact:{write.artifact_id}", self.fail_after)
        store.transition_current(connection, self.graph_pointer_id, ObjectState.SUPERSEDED)
        store.insert_version(connection, prepared.graph_pointer, make_current=True)
        self._maybe_fail("graph-pointer", self.fail_after)
        batch_receipt = self._batch_receipt(
            store=store,
            base_receipt=base_receipt,
            prepared=prepared,
            committed_at=committed_at,
        )
        store.save_artifact(
            connection,
            batch_receipt.id,
            DELIVERABLE_SET_APPLY_RECEIPT_MEDIA_TYPE,
            batch_receipt.model_dump(mode="json"),
        )
        self._maybe_fail("batch-receipt", self.fail_after)
        return WorkspaceRebaseReceipt(
            schema_version="orgrebase.workspace-rebase-receipt.v2",
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
            deliverable_set_receipt_ref=batch_receipt.id,
            deliverable_set_receipt_digest=batch_receipt.digest,
            committed_at=committed_at,
        )
