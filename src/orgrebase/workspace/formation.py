"""Atomic Task→Quote→Trace→Graph formation transaction."""

from __future__ import annotations

import json
from collections.abc import Mapping

from orgrebase.digest import sha256_digest
from orgrebase.domain import CoverageBasis, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace.admission import AdmissionController
from orgrebase.workspace.context import TaskContextCompiler
from orgrebase.workspace.domain_agents import LocalDomainCandidateRegistry
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
    workspace_seed_objects,
    workspace_seed_universe,
)
from orgrebase.workspace.models import (
    ArtifactWrite,
    PreparedFormationBundle,
    QuoteTaskLiterals,
    TaskReceipt,
    TaskRequest,
)
from orgrebase.workspace.planner import CoalitionPlanner
from orgrebase.workspace.task_agent import TemplateBoundTaskInterpreter
from orgrebase.workspace.templates import TemplateRegistry, default_capability_cards

MEDIA = {
    "task": "application/vnd.orgrebase.workspace-task+json",
    "template": "application/vnd.orgrebase.task-template+json",
    "interpretation": "application/vnd.orgrebase.task-interpretation+json",
    "coalition": "application/vnd.orgrebase.coalition-plan+json",
    "projection": "application/vnd.orgrebase.actor-context-projection+json",
    "candidate": "application/vnd.orgrebase.claim-candidate+json",
    "bundle": "application/vnd.orgrebase.domain-candidate-bundle+json",
    "admission": "application/vnd.orgrebase.admission-decision+json",
    "context": "application/vnd.orgrebase.task-context+json",
    "trace": "application/vnd.orgrebase.work-trace+json",
    "coverage": "application/vnd.orgrebase.trace-coverage+json",
    "receipt": "application/vnd.orgrebase.task-receipt+json",
}


def _artifact_write(artifact_id: str, media_type: str, payload: Mapping[str, object]) -> ArtifactWrite:
    data = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    return ArtifactWrite(
        artifact_id=artifact_id,
        media_type=media_type,
        payload=data,
        payload_digest=sha256_digest(data),
    )


def seed_workspace_store(store: StateStore) -> None:
    """Load immutable Workspace seed objects without touching legacy fixture bytes."""

    try:
        store.get_object("policy:finance.currency", "v1")
        store.load_artifact("workspace-universe:northstar@r1", UNIVERSE_MEDIA_TYPE)
        return
    except (KeyError, ValueError):
        pass
    with store.transaction() as connection:
        for item in workspace_seed_objects():
            try:
                store.get_object(item.id, item.version)
            except KeyError:
                store.insert_version(
                    connection,
                    item,
                    make_current=item.state in {ObjectState.CURRENT, ObjectState.ACTIVE},
                )
        static_universe = workspace_seed_universe()
        actual_objects = tuple(
            store.get_object(item.id, item.version) for item in static_universe.current_objects
        )
        universe_payload = static_universe.model_dump(mode="json", exclude={"digest"})
        universe_payload["current_objects"] = [
            item.model_dump(mode="json") for item in actual_objects
        ]
        universe = type(static_universe).model_validate(universe_payload)
        store.save_artifact(
            connection,
            universe.id,
            UNIVERSE_MEDIA_TYPE,
            universe.model_dump(mode="json"),
        )
        store.append_event(
            connection,
            "WORKSPACE_SEED_LOADED",
            {"universe_ref": universe.id, "universe_digest": universe.digest},
        )


class WorkspaceFormationService:
    def __init__(
        self,
        store: StateStore,
        *,
        registry: TemplateRegistry | None = None,
        domain_registry: LocalDomainCandidateRegistry | None = None,
    ) -> None:
        self.store = store
        seed_workspace_store(store)
        self.registry = registry or TemplateRegistry()
        self.domain_registry = domain_registry or LocalDomainCandidateRegistry()
        self.interpreter = TemplateBoundTaskInterpreter()
        self.planner = CoalitionPlanner()
        self.admission = AdmissionController()
        self.context_compiler = TaskContextCompiler()
        self.assembler = QuoteInputAssembler()
        self.renderer = QuoteRenderer()
        self.coverage_verifier = TraceCoverageVerifier()
        self.dependency_compiler = RuntimeDependencyCompiler()
        self.snapshot_builder = WorkspaceSnapshotBuilder()
        self.clock = StaticClock()

    @staticmethod
    def default_request() -> TaskRequest:
        return TaskRequest(
            id="task:quote_acme",
            organization_id="org:northstar",
            actor_id="employee:sales-owner",
            purpose="enterprise_quote",
            deliverable_kind="QUOTE",
            requested_at="2026-08-15T00:00:00Z",
            template_ref="template:enterprise_quote@v1",
            input_values={"owner": "sales-owner"},
            customer_id="customer:acme",
            idempotency_key="workspace:form:quote-acme@v1",
        )

    def _id_factory(self, task: TaskRequest):
        counter = {"value": 0}

        def factory(prefix: str) -> str:
            counter["value"] += 1
            return f"event:{task.id.split(':')[-1]}:{prefix}:{counter['value']:02d}"

        return factory

    def prepare_quote(self, request: TaskRequest) -> PreparedFormationBundle:
        template = self.registry.get(request.template_ref)
        if template.deliverable_kind != request.deliverable_kind:
            raise ValueError("TASK_TEMPLATE_DELIVERABLE_MISMATCH")
        candidates, interpretation = self.interpreter.propose(task=request, template=template)
        revision_lock = {
            "graph": "graph:workspace@seed-r1",
            "policy": "policy:workspace@r1",
            "authorization": "authz:workspace@r1",
            "skill_registry": "skills:registry@r1",
            "runtime_registry": "runtime:workspace@r1",
        }
        coalition = self.planner.plan(
            task=request,
            template=template,
            candidates=candidates,
            cards=default_capability_cards(),
            revision_lock=revision_lock,
        )
        source_projections = self.domain_registry.source_projections(
            task=request,
            template=template,
            plan=coalition,
            now=self.clock.now(),
        )
        claim_candidates, bundles = self.domain_registry.execute_selected(
            task=request,
            template=template,
            plan=coalition,
            projections=source_projections,
            now=self.clock.now(),
        )
        decisions = self.admission.admit(
            task=request,
            template=template,
            coalition=coalition,
            candidates=claim_candidates,
            now=self.clock.now(),
            policy_revision=revision_lock["policy"],
        )
        task_context, final_projections = self.context_compiler.compile(
            task=request,
            template=template,
            coalition=coalition,
            decisions=decisions,
            candidates=claim_candidates,
            now=self.clock.now(),
            revisions=revision_lock,
        )
        run_id = "run:workspace:quote-acme@v1"
        monitor = ExecutionReferenceMonitor(
            task=request,
            template=template,
            manifest=task_context,
            artifact_reader=None,
            actor_id="workspace-renderer",
            run_id=run_id,
            clock=self.clock,
            id_factory=self._id_factory(request),
        )
        inputs = self.assembler.assemble(monitor)
        quote_payload = self.renderer.render(
            task_literals=QuoteTaskLiterals(
                owner=str(request.input_values.get("owner", request.actor_id)),
                customer_id=request.customer_id or "customer:unknown",
            ),
            inputs=inputs,
        )
        quote = VersionedObject(
            id="work:quote_acme",
            version="v1",
            kind="WorkItemVersion",
            label="Acme Enterprise Quote",
            domain="gtm",
            state=ObjectState.CURRENT,
            payload=quote_payload.model_dump(mode="json"),
            source_refs=(task_context.ref,),
            sensitivity="CONFIDENTIAL",
            allowed_purposes=("enterprise_quote", "change_rebase"),
            coverage_complete=True,
            coverage_basis=(CoverageBasis.RUNTIME_OBSERVED,),
        )
        trace = monitor.finish(
            output_ref=quote.ref,
            output_payload=quote.payload,
            output_field_lineage=quote_output_lineage(),
        )
        coverage = self.coverage_verifier.verify(
            template=template,
            trace=trace,
            output_payload=quote.payload,
            observed_channels=("REFERENCE_MONITOR",),
        )
        runtime_manifest = self.dependency_compiler.compile(
            task=request,
            template=template,
            trace=trace,
            coverage=coverage,
            consumer_ref=quote.ref,
            consumer_domain=quote.domain,
            revision_lock=revision_lock,
            now=self.clock.now(),
        )
        universe = __import__(
            "orgrebase.workspace.models", fromlist=["WorkspaceUniverse"]
        ).WorkspaceUniverse.model_validate(
            self.store.load_artifact(
                "workspace-universe:northstar@r1", UNIVERSE_MEDIA_TYPE
            ).payload
        )
        snapshot = self.snapshot_builder.build(
            universe=universe,
            replacement_objects=(quote,),
            replacement_manifests=(runtime_manifest,),
            targets=(
                quote.id,
                "work:finance_analysis_d",
                "work:partner_brief_e",
                "skill:enterprise-launch-readiness",
            ),
            scope_roots=("claim:product.launch_date", "policy:finance.currency"),
            now=self.clock.now(),
            version="v1",
        )
        pointer = graph_pointer_object(version="v1", snapshot=snapshot, promoted_at=self.clock.now())
        task_receipt = TaskReceipt(
            id="task-receipt:quote_acme@v1",
            task_ref=request.id,
            template_ref=template.ref,
            coalition_plan_ref=coalition.id,
            admission_decision_refs=tuple(item.id for item in decisions),
            context_manifest_ref=task_context.ref,
            trace_ref=trace.ref,
            coverage_receipt_ref=coverage.id,
            dependency_manifest_ref=runtime_manifest.ref,
            deliverable_ref=quote.ref,
            graph_snapshot_ref=snapshot.ref,
            revision_lock=snapshot.revisions,
            committed_at=self.clock.now(),
        )
        writes: list[ArtifactWrite] = []
        def add(artifact_id: str, media: str, model: object) -> None:
            payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
            writes.append(_artifact_write(artifact_id, media, payload))

        add(request.id, MEDIA["task"], request)
        add(template.ref, MEDIA["template"], template)
        add(interpretation.id, MEDIA["interpretation"], interpretation)
        add(coalition.id, MEDIA["coalition"], coalition)
        for item in (*source_projections, *final_projections):
            add(item.ref, MEDIA["projection"], item)
        for item in claim_candidates:
            add(item.digest, MEDIA["candidate"], item)
        for item in bundles:
            add(item.id, MEDIA["bundle"], item)
        for item in decisions:
            add(item.id, MEDIA["admission"], item)
        add(task_context.ref, MEDIA["context"], task_context)
        add(trace.ref, MEDIA["trace"], trace)
        add(coverage.id, MEDIA["coverage"], coverage)
        add(runtime_manifest.ref, RUNTIME_MANIFEST_MEDIA_TYPE, runtime_manifest)
        add(universe.id, UNIVERSE_MEDIA_TYPE, universe)
        add(snapshot.ref, SNAPSHOT_MEDIA_TYPE, snapshot)
        add(task_receipt.id, MEDIA["receipt"], task_receipt)
        return PreparedFormationBundle(
            request_digest=request.digest,
            idempotency_key=request.idempotency_key,
            deliverable=quote,
            graph_pointer=pointer,
            artifact_writes=tuple(writes),
            task_receipt=task_receipt,
            event_payload={
                "task_ref": request.id,
                "deliverable_ref": quote.ref,
                "trace_ref": trace.ref,
                "manifest_ref": runtime_manifest.ref,
                "snapshot_ref": snapshot.ref,
                "snapshot_digest": snapshot.digest,
                "task_receipt_digest": task_receipt.digest,
            },
        )

    def commit_quote(self, prepared: PreparedFormationBundle) -> TaskReceipt:
        existing = self.store.get_idempotent(prepared.idempotency_key, prepared.request_digest)
        if existing is not None:
            return TaskReceipt.model_validate(existing)
        for write in prepared.artifact_writes:
            if sha256_digest(write.payload) != write.payload_digest:
                raise ValueError(f"PREPARED_ARTIFACT_DIGEST_MISMATCH:{write.artifact_id}")
        with self.store.transaction() as connection:
            self.store.insert_version(connection, prepared.deliverable, make_current=True)
            self.store.insert_version(connection, prepared.graph_pointer, make_current=True)
            for write in prepared.artifact_writes:
                self.store.save_artifact(
                    connection,
                    write.artifact_id,
                    write.media_type,
                    write.payload,
                )
            self.store.append_event(connection, prepared.event_type, prepared.event_payload)
            self.store.save_idempotent(
                connection,
                prepared.idempotency_key,
                prepared.request_digest,
                prepared.task_receipt.model_dump(mode="json"),
            )
        return prepared.task_receipt

    def form_quote(self, request: TaskRequest) -> TaskReceipt:
        return self.commit_quote(self.prepare_quote(request))
