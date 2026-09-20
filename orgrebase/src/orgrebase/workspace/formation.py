"""Atomic Task→Quote→Trace→Graph formation transaction."""

from __future__ import annotations

import json
from collections.abc import Mapping

from orgrebase.auth import current_authorization
from orgrebase.clock import Clock, FrozenClock, utc_datetime
from orgrebase.database import Connection
from orgrebase.digest import sha256_digest
from orgrebase.domain import CoverageBasis, IntegrityError, ObjectState, VersionedObject
from orgrebase.runtime_contracts import ArtifactWrite, prepare_artifact_write
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
    ActorContextProjection,
    ClaimCandidate,
    CoalitionPlan,
    DomainCandidateBundle,
    PreparedFormationBundle,
    QuoteTaskLiterals,
    TaskInterpretationReceipt,
    TaskReceipt,
    TaskRequest,
    TaskRequirementCandidate,
    TaskTemplateVersion,
    WorkspaceUniverse,
)
from orgrebase.workspace.planner import CoalitionPlanner
from orgrebase.workspace.profile import (
    EnterpriseSeedProfile,
    northstar_acme_quote_profile,
    parse_enterprise_seed_admission_receipt,
    parse_enterprise_seed_profile,
    profile_summary,
    require_reference_runtime_compatible,
)
from orgrebase.workspace.read_dependencies import ReadDependencyValidator
from orgrebase.workspace.source_admission import (
    EnterpriseSeedRuntimeProjectionReceipt,
    EnterpriseSeedSourceAdmissionReceipt,
    admit_enterprise_seed_sources,
    parse_enterprise_seed_runtime_projection_receipt,
    parse_enterprise_seed_source_admission_receipt,
    verify_reference_runtime_projections,
)
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
    "profile_binding": "application/vnd.orgrebase.enterprise-seed-runtime-binding+json",
}

PROFILE_BINDING_ARTIFACT_ID = "enterprise-seed-runtime-binding:workspace@r1"
PROFILE_BINDING_MEDIA_TYPE = MEDIA["profile_binding"]
SNAPSHOT_TARGET_IDS = (
    "work:quote_acme",
    "work:finance_analysis_d",
    "work:partner_brief_e",
    "skill:enterprise-launch-readiness",
)
SNAPSHOT_SCOPE_ROOTS = ("claim:product.launch_date", "policy:finance.currency")


def seed_workspace_store(
    store: StateStore,
    *,
    seed_objects: tuple[VersionedObject, ...] | None = None,
    universe: WorkspaceUniverse | None = None,
) -> None:
    """Load immutable Workspace seed objects without touching legacy fixture bytes."""

    selected_objects = seed_objects or workspace_seed_objects()
    selected_universe = universe or workspace_seed_universe()
    try:
        store.load_artifact(selected_universe.id, UNIVERSE_MEDIA_TYPE)
        return
    except (KeyError, ValueError):
        pass
    with store.transaction() as connection:
        for item in selected_objects:
            try:
                store.get_object(item.id, item.version)
            except KeyError:
                store.insert_version(
                    connection,
                    item,
                    make_current=item.state in {ObjectState.CURRENT, ObjectState.ACTIVE},
                )
        static_universe = selected_universe
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
        profile: EnterpriseSeedProfile | None = None,
        source_admission: EnterpriseSeedSourceAdmissionReceipt | None = None,
        runtime_projection: EnterpriseSeedRuntimeProjectionReceipt | None = None,
        workflow_run_id: str | None = None,
        seed_objects: tuple[VersionedObject, ...] | None = None,
        universe: WorkspaceUniverse | None = None,
        snapshot_target_ids: tuple[str, ...] = SNAPSHOT_TARGET_IDS,
        snapshot_scope_roots: tuple[str, ...] = SNAPSHOT_SCOPE_ROOTS,
        quote_object_id: str = "work:quote_acme",
        quote_label: str = "Acme Enterprise Quote",
        graph_pointer_id: str = "graph:workspace",
        graph_snapshot_id: str = "workspace-graph:northstar",
        task_receipt_id: str = "task-receipt:quote_acme@v1",
        default_run_id: str = "run:workspace:quote-acme@v1",
        revision_lock: Mapping[str, str] | None = None,
        deployment_binding: Mapping[str, object] | None = None,
        read_dependency_validator: ReadDependencyValidator | None = None,
    ) -> None:
        if workflow_run_id is not None and not workflow_run_id.strip():
            raise ValueError("FORMATION_WORKFLOW_RUN_ID_EMPTY")
        self.workflow_run_id = workflow_run_id
        self.profile = parse_enterprise_seed_profile(
            profile or northstar_acme_quote_profile()
        )
        self.source_admission = parse_enterprise_seed_source_admission_receipt(
            source_admission or admit_enterprise_seed_sources(self.profile)
        )
        self.profile_admission = require_reference_runtime_compatible(
            self.profile,
            source_admission=self.source_admission,
        )
        self.runtime_projection = parse_enterprise_seed_runtime_projection_receipt(
            runtime_projection
            or verify_reference_runtime_projections(
                self.profile,
                self.source_admission,
            )
        )
        if (
            self.runtime_projection.profile_digest != self.profile.digest
            or self.runtime_projection.source_admission_receipt_digest
            != self.source_admission.digest
        ):
            raise ValueError("FORMATION_SOURCE_RUNTIME_BINDING_MISMATCH")
        self.profile_digest = self.profile.digest
        self.store = store
        self.seed_objects = seed_objects or workspace_seed_objects()
        self.universe = universe or workspace_seed_universe()
        self.snapshot_target_ids = tuple(snapshot_target_ids)
        self.snapshot_scope_roots = tuple(snapshot_scope_roots)
        self.quote_object_id = quote_object_id
        self.quote_label = quote_label
        self.graph_pointer_id = graph_pointer_id
        self.graph_snapshot_id = graph_snapshot_id
        self.task_receipt_id = task_receipt_id
        self.default_run_id = default_run_id
        self.revision_lock = dict(revision_lock or self._default_revision_lock())
        self.deployment_binding = dict(deployment_binding or {})
        seed_workspace_store(
            store,
            seed_objects=self.seed_objects,
            universe=self.universe,
        )
        self.registry = registry or TemplateRegistry()
        self.domain_registry = domain_registry or LocalDomainCandidateRegistry()
        self.domain_registry.bind_source_reader(self.store.get_object)
        self.interpreter = TemplateBoundTaskInterpreter()
        self.planner = CoalitionPlanner()
        self.admission = AdmissionController()
        self.context_compiler = TaskContextCompiler(self.store.get_object,
            read_dependency_validator=read_dependency_validator)
        self.assembler = QuoteInputAssembler()
        self.renderer = QuoteRenderer()
        self.coverage_verifier = TraceCoverageVerifier()
        self.dependency_compiler = RuntimeDependencyCompiler()
        self.snapshot_builder = WorkspaceSnapshotBuilder()
        self.clock = StaticClock()

    @staticmethod
    def default_request(profile: EnterpriseSeedProfile | None = None) -> TaskRequest:
        return (profile or northstar_acme_quote_profile()).task_request()

    @staticmethod
    def _revalidate(value, error_code: str):
        """Deeply reparse a model so cached digests cannot cross this boundary."""

        try:
            return type(value).model_validate(value.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityError(error_code) from exc

    def _require_profile_request_digest(self, request_digest: str) -> None:
        expected = self.default_request(self.profile)
        if request_digest != expected.digest:
            raise ValueError(
                "UNSUPPORTED_SYNTHETIC_SCENARIO:request does not match the "
                f"admitted profile {self.profile.ref}"
            )

    def require_profile_bound_request(
        self,
        request: TaskRequest,
        *,
        error_code: str = "WORKSPACE_TASK_REQUEST_MODEL_INVALID",
    ) -> TaskRequest:
        """Admit only the exact TaskRequest projected by this runtime Profile.

        The current Reference Runtime intentionally implements one exact
        Northstar scenario.  Keeping this check at the Formation boundary makes
        direct, facade, and live paths obey the same profile/organization lock.
        """

        selected = self._revalidate(request, error_code)
        self._require_profile_request_digest(selected.digest)
        return selected

    @staticmethod
    def _default_revision_lock() -> dict[str, str]:
        return {
            "graph": "graph:workspace@seed-r1",
            "policy": "policy:workspace@r1",
            "authorization": "authz:workspace@r1",
            "skill_registry": "skills:registry@r1",
            "runtime_registry": "runtime:workspace@r1",
        }

    def _revision_lock(self) -> dict[str, str]:
        return dict(self.revision_lock)

    def _id_factory(self, task: TaskRequest):
        counter = {"value": 0}

        def factory(prefix: str) -> str:
            counter["value"] += 1
            return f"event:{task.id.split(':')[-1]}:{prefix}:{counter['value']:02d}"

        return factory

    def _quote_object(
        self,
        task_context,
        quote_payload,
    ) -> VersionedObject:
        return VersionedObject(
            id=self.quote_object_id,
            version="v1",
            kind="WorkItemVersion",
            label=self.quote_label,
            domain="gtm",
            state=ObjectState.CURRENT,
            payload=quote_payload.model_dump(mode="json"),
            source_refs=(task_context.ref,),
            sensitivity="CONFIDENTIAL",
            allowed_purposes=("enterprise_quote", "change_rebase"),
            coverage_complete=True,
            coverage_basis=(CoverageBasis.RUNTIME_OBSERVED,),
        )

    def _profile_binding_payload(self, request: TaskRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "orgrebase.enterprise-seed-runtime-binding.v1",
            "profile": profile_summary(
                self.profile,
                receipt=self.profile_admission,
            ),
            "admission_receipt": parse_enterprise_seed_admission_receipt(
                self.profile_admission
            ).model_dump(mode="json"),
            "source_admission_receipt": (
                parse_enterprise_seed_source_admission_receipt(
                    self.source_admission
                ).model_dump(mode="json")
            ),
            "source_admission_receipt_digest": self.source_admission.digest,
            "admitted_source_root_digests": [
                item.observed_digest for item in self.source_admission.root_observations
            ],
            "component_admission_digests": [
                item.digest for item in self.source_admission.component_admissions
            ],
            "source_profile_projection_digest": (
                self.source_admission.profile_projection_digest
            ),
            "runtime_projection_receipt": (
                parse_enterprise_seed_runtime_projection_receipt(
                    self.runtime_projection
                ).model_dump(mode="json")
            ),
            "runtime_projection_receipt_digest": self.runtime_projection.digest,
            "runtime_projection_digest": (
                self.runtime_projection.runtime_projection_digest
            ),
            "runtime_projection_bindings": [
                {
                    "component_kind": item.component_kind.value,
                    "runtime_projection_digest": item.runtime_projection_digest,
                }
                for item in self.runtime_projection.observations
            ],
            "handler_profile": self.profile.runtime_compatibility.handler_profile,
            "task_ref": request.id,
            "canonical_target_writes_outside_formation_transaction": 0,
        }
        if self.deployment_binding:
            payload["enterprise_pilot_pack"] = dict(self.deployment_binding)
        return payload

    def compile_quote_contracts(
        self, request: TaskRequest
    ) -> tuple[
        TaskTemplateVersion,
        tuple[TaskRequirementCandidate, ...],
        TaskInterpretationReceipt,
        dict[str, str],
        CoalitionPlan,
    ]:
        """Compile the deterministic task boundary shared by local and live modes."""

        request = self.require_profile_bound_request(request)

        template = self.registry.get(request.template_ref)
        if template.deliverable_kind != request.deliverable_kind:
            raise ValueError("TASK_TEMPLATE_DELIVERABLE_MISMATCH")
        candidates, interpretation = self.interpreter.propose(
            task=request, template=template
        )
        revision_lock = self._revision_lock()
        coalition = self.planner.plan(
            task=request,
            template=template,
            candidates=candidates,
            cards=default_capability_cards(template.ref),
            revision_lock=revision_lock,
        )
        return template, candidates, interpretation, revision_lock, coalition

    def prepare_quote_from_candidates(
        self,
        *,
        request: TaskRequest,
        template: TaskTemplateVersion,
        interpretation: TaskInterpretationReceipt,
        revision_lock: dict[str, str],
        coalition: CoalitionPlan,
        source_projections: tuple[ActorContextProjection, ...],
        claim_candidates: tuple[ClaimCandidate, ...],
        bundles: tuple[DomainCandidateBundle, ...],
        clock: Clock | None = None,
        context_expires_at: str | None = None,
        run_id: str | None = None,
        additional_artifact_writes: tuple[ArtifactWrite, ...] = (),
        event_metadata: Mapping[str, object] | None = None,
    ) -> PreparedFormationBundle:
        """Prepare one Quote from already-produced candidate-only domain results."""

        request = self.require_profile_bound_request(
            request,
            error_code="FORMATION_REQUEST_MODEL_INVALID",
        )
        template = self._revalidate(template, "FORMATION_TEMPLATE_MODEL_INVALID")
        interpretation = self._revalidate(
            interpretation,
            "FORMATION_INTERPRETATION_MODEL_INVALID",
        )
        coalition = self._revalidate(coalition, "FORMATION_COALITION_MODEL_INVALID")
        source_projections = tuple(
            self._revalidate(item, "FORMATION_PROJECTION_MODEL_INVALID")
            for item in source_projections
        )
        claim_candidates = tuple(
            self._revalidate(item, "FORMATION_CANDIDATE_MODEL_INVALID")
            for item in claim_candidates
        )
        bundles = tuple(
            self._revalidate(item, "FORMATION_BUNDLE_MODEL_INVALID")
            for item in bundles
        )
        selected_clock = FrozenClock((clock or self.clock).now())
        if (
            template.ref != request.template_ref
            or interpretation.task_ref != request.id
            or interpretation.template_ref != template.ref
            or coalition.task_ref != request.id
            or coalition.template_ref != template.ref
            or coalition.revision_lock != revision_lock
            or revision_lock != self._revision_lock()
        ):
            raise ValueError("FORMATION_CONTRACT_BINDING_MISMATCH")
        decisions = self.admission.admit(
            task=request,
            template=template,
            coalition=coalition,
            candidates=claim_candidates,
            now=selected_clock.now(),
            policy_revision=revision_lock["policy"],
        )
        task_context, final_projections = self.context_compiler.compile(
            task=request,
            template=template,
            coalition=coalition,
            decisions=decisions,
            candidates=claim_candidates,
            now=selected_clock.now(),
            revisions=revision_lock,
            expires_at=context_expires_at,
        )
        selected_run_id = run_id or self.default_run_id
        monitor = ExecutionReferenceMonitor(
            task=request,
            template=template,
            manifest=task_context,
            artifact_reader=None,
            actor_id="workspace-renderer",
            run_id=selected_run_id,
            clock=selected_clock,
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
        quote = self._quote_object(task_context, quote_payload)
        trace = monitor.finish(
            output_ref=quote.ref,
            output_payload=quote.payload,
            output_field_lineage=quote_output_lineage(priced=quote_payload.pricing is not None),
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
            now=selected_clock.now(),
        )
        universe = WorkspaceUniverse.model_validate(
            self.store.load_artifact(
                self.universe.id, UNIVERSE_MEDIA_TYPE
            ).payload
        )
        snapshot = self.snapshot_builder.build(
            universe=universe,
            replacement_objects=(quote,),
            replacement_manifests=(runtime_manifest,),
            targets=self.snapshot_target_ids,
            scope_roots=self.snapshot_scope_roots,
            now=selected_clock.now(),
            version="v1",
            snapshot_id=self.graph_snapshot_id,
            graph_namespace=self.graph_pointer_id,
        )
        pointer = graph_pointer_object(
            version="v1",
            snapshot=snapshot,
            promoted_at=selected_clock.now(),
            object_id=self.graph_pointer_id,
        )
        task_receipt = TaskReceipt(
            id=self.task_receipt_id,
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
            committed_at=selected_clock.now(),
        )
        writes: list[ArtifactWrite] = []
        def add(artifact_id: str, media: str, model: object) -> None:
            payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
            writes.append(prepare_artifact_write(artifact_id, media, payload))

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
        profile_binding_payload = self._profile_binding_payload(request)
        profile_binding_write = prepare_artifact_write(
            PROFILE_BINDING_ARTIFACT_ID,
            PROFILE_BINDING_MEDIA_TYPE,
            profile_binding_payload,
        )
        writes.append(profile_binding_write)
        writes.extend(additional_artifact_writes)
        event_payload = {
            "task_ref": request.id,
            "deliverable_ref": quote.ref,
            "trace_ref": trace.ref,
            "manifest_ref": runtime_manifest.ref,
            "snapshot_ref": snapshot.ref,
            "snapshot_digest": snapshot.digest,
            "task_receipt_digest": task_receipt.digest,
            "enterprise_seed_profile_ref": self.profile.ref,
            "enterprise_seed_profile_digest": self.profile_digest,
            "enterprise_seed_source_admission_receipt_digest": (
                self.source_admission.digest
            ),
            "enterprise_seed_runtime_projection_digest": (
                self.runtime_projection.runtime_projection_digest
            ),
            "enterprise_seed_binding_artifact_id": PROFILE_BINDING_ARTIFACT_ID,
            "enterprise_seed_binding_artifact_digest": profile_binding_write.payload_digest,
        }
        if event_metadata:
            reserved = set(event_payload).intersection(event_metadata)
            if reserved:
                raise ValueError(
                    "FORMATION_EVENT_METADATA_RESERVED:" + ",".join(sorted(reserved))
                )
            event_payload.update(
                json.loads(json.dumps(dict(event_metadata), ensure_ascii=False))
            )
        return PreparedFormationBundle(
            request_digest=request.digest,
            idempotency_key=request.idempotency_key,
            deliverable=quote,
            graph_pointer=pointer,
            artifact_writes=tuple(writes),
            task_receipt=task_receipt,
            event_payload=event_payload,
        )

    def prepare_quote(
        self,
        request: TaskRequest,
        *,
        run_id: str | None = None,
    ) -> PreparedFormationBundle:
        if (
            run_id is not None
            and self.workflow_run_id is not None
            and run_id != self.workflow_run_id
        ):
            raise IntegrityError("FORMATION_WORKFLOW_RUN_ID_MISMATCH")
        selected_run_id = run_id or self.workflow_run_id
        operation_clock = FrozenClock(self.clock.now())
        request = self.require_profile_bound_request(request)
        (
            template,
            _requirement_candidates,
            interpretation,
            revision_lock,
            coalition,
        ) = self.compile_quote_contracts(request)
        source_projections = self.domain_registry.source_projections(
            task=request,
            template=template,
            plan=coalition,
            now=operation_clock.now(),
        )
        claim_candidates, bundles = self.domain_registry.execute_selected(
            task=request,
            template=template,
            plan=coalition,
            projections=source_projections,
            now=operation_clock.now(),
        )
        return self.prepare_quote_from_candidates(
            request=request,
            template=template,
            interpretation=interpretation,
            revision_lock=revision_lock,
            coalition=coalition,
            source_projections=source_projections,
            claim_candidates=claim_candidates,
            bundles=bundles,
            clock=operation_clock,
            run_id=selected_run_id,
            event_metadata=(
                {"parent_workflow_run_id": selected_run_id}
                if selected_run_id is not None
                else None
            ),
        )

    def commit_quote(
        self,
        prepared: PreparedFormationBundle,
        *,
        connection: Connection | None = None,
    ) -> TaskReceipt:
        """Commit in an owned or borrowed StateStore transaction.

        A borrowed receipt stays provisional until the caller exits the
        transaction; authorization and the original local lease are checked
        there, after all of the caller's writes.
        """

        prepared = self._revalidate(
            prepared,
            "WORKSPACE_PREPARED_FORMATION_MODEL_INVALID",
        )
        self._require_profile_request_digest(prepared.request_digest)
        if connection is None:
            with self.store.transaction() as owned_connection:
                return self._commit_quote_in_transaction(prepared, owned_connection)
        return self._commit_quote_in_transaction(prepared, connection)

    def _commit_quote_in_transaction(
        self,
        prepared: PreparedFormationBundle,
        connection: Connection,
    ) -> TaskReceipt:
        from orgrebase.workspace.formation_integrity import (
            verify_prepared_formation,
        )

        authorization = current_authorization()
        completed = False
        lease: tuple[str, str] | None = None

        def require_completion() -> None:
            if not completed:
                raise IntegrityError("FORMATION_COMMIT_INCOMPLETE")
            if authorization is not None:
                authorization()
            if lease is not None:
                commit_time = utc_datetime(self.clock.now())
                if not utc_datetime(lease[0]) <= commit_time < utc_datetime(lease[1]):
                    raise IntegrityError(
                        "PREPARED_FORMATION_PROOF_GRAPH_INVALID:LOCAL_FORMATION_CLOCK_OUTSIDE_LEASE"
                    )

        self.store.require_before_commit(connection, require_completion)
        if authorization is not None:
            authorization()
        lease = verify_prepared_formation(
            self,
            prepared,
            media=MEDIA,
            profile_binding_artifact_id=PROFILE_BINDING_ARTIFACT_ID,
            profile_binding_media_type=PROFILE_BINDING_MEDIA_TYPE,
            snapshot_target_ids=self.snapshot_target_ids,
            snapshot_scope_roots=self.snapshot_scope_roots,
        )
        existing = self.store.get_idempotent(
            prepared.idempotency_key,
            prepared.request_digest,
            connection=connection,
        )
        if existing is not None:
            receipt = TaskReceipt.model_validate(existing)
            completed = True
            return receipt
        for write in prepared.artifact_writes:
            if sha256_digest(write.payload) != write.payload_digest:
                raise ValueError(f"PREPARED_ARTIFACT_DIGEST_MISMATCH:{write.artifact_id}")
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
        completed = True
        return prepared.task_receipt

    def form_quote(
        self,
        request: TaskRequest,
        *,
        run_id: str | None = None,
    ) -> TaskReceipt:
        return self.commit_quote(self.prepare_quote(request, run_id=run_id))
