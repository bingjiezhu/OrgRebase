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
    workspace_seed_objects,
    workspace_seed_universe,
)
from orgrebase.workspace.models import (
    ActorContextProjection,
    ClaimCandidate,
    CoalitionPlan,
    DeliverableMemberBinding,
    DeliverableSetBinding,
    DeliverableSetFormationReceipt,
    DeliverableSetProfile,
    DiscountMemoTaskLiterals,
    DomainCandidateBundle,
    PreparedDeliverableSetFormation,
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
DELIVERABLE_SET_PROFILE_ARTIFACT_ID = "deliverable-set-profile:workspace@v1"
DELIVERABLE_SET_PROFILE_MEDIA_TYPE = "application/vnd.orgrebase.deliverable-set-profile+json"
DELIVERABLE_SET_BINDING_ARTIFACT_ID = "deliverable-set-binding:workspace@v1"
DELIVERABLE_SET_BINDING_MEDIA_TYPE = "application/vnd.orgrebase.deliverable-set-binding+json"
DELIVERABLE_SET_FORMATION_MEDIA_TYPE = (
    "application/vnd.orgrebase.deliverable-set-formation-receipt+json"
)
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


def quote_discount_memo_profile(runtime_configuration: object) -> DeliverableSetProfile:
    """Build the only admitted v1 multi-deliverable profile.

    The enterprise Seed/Profile remains the source and authority contract.  This
    content-addressed overlay selects a bounded pair of server-side adapters for
    a new workspace; it never mutates an existing Quote workspace binding.
    """

    profile = parse_enterprise_seed_profile(runtime_configuration.profile)
    pack_digest = str(runtime_configuration.pack_digest)
    quote_object_id = str(runtime_configuration.quote_object_id)
    if profile.default_task.template_ref != "template:enterprise_quote@v2":
        raise ValueError("DELIVERABLE_SET_REQUIRES_PRICED_QUOTE_PROFILE")
    enterprise_binding = getattr(runtime_configuration, "enterprise_binding", None)
    if enterprise_binding is None:
        raise ValueError("DELIVERABLE_SET_ENTERPRISE_BINDING_REQUIRED")
    pricing_bindings = [
        item for item in enterprise_binding.resources if item.slot_id == "pricing_policy"
    ]
    if len(pricing_bindings) != 1:
        raise ValueError("DELIVERABLE_SET_PRICING_OWNER_REQUIRED")
    quote_template = TemplateRegistry().get("template:enterprise_quote@v2")
    memo_template = TemplateRegistry().get("template:discount_exception_memo@v2")
    quote_slug = quote_object_id.split(":")[-1]
    quote_adapter = "orgrebase.workspace.execution.QuoteRenderer@2.0.0"
    memo_adapter = "orgrebase.workspace.execution.DiscountMemoRenderer@2.0.0"
    members = (
        DeliverableMemberBinding(
            object_id=f"work:discount-memo-{quote_slug}",
            deliverable_kind="DISCOUNT_MEMO",
            template_ref=memo_template.ref,
            template_digest=memo_template.digest,
            output_schema_ref=memo_template.output_schema_ref,
            adapter_ref=memo_adapter,
            adapter_digest=sha256_digest(
                {
                    "adapter_ref": memo_adapter,
                    "template_digest": memo_template.digest,
                    "output_schema_ref": memo_template.output_schema_ref,
                }
            ),
            owner_id=pricing_bindings[0].owner_id,
            approval_scopes=("discount_memo.pricing",),
        ),
        DeliverableMemberBinding(
            object_id=quote_object_id,
            deliverable_kind="QUOTE",
            template_ref=quote_template.ref,
            template_digest=quote_template.digest,
            output_schema_ref=quote_template.output_schema_ref,
            adapter_ref=quote_adapter,
            adapter_digest=sha256_digest(
                {
                    "adapter_ref": quote_adapter,
                    "template_digest": quote_template.digest,
                    "output_schema_ref": quote_template.output_schema_ref,
                }
            ),
            owner_id=profile.default_task.actor_id,
            approval_scopes=(
                "quote.launch_date",
                "quote.pricing",
                "quote.product_plan",
            ),
        ),
    )
    return DeliverableSetProfile(
        id="profile:quote-discount-memo",
        revision="r1",
        organization_id=profile.organization_id,
        base_profile_digest=profile.digest,
        pack_digest=pack_digest,
        runtime_revision="runtime:quote-discount-memo@v1",
        members=members,
    )


class QuoteDiscountMemoFormationService:
    """Atomically form one priced Quote and one independently traced Memo."""

    def __init__(
        self,
        base: WorkspaceFormationService,
        profile: DeliverableSetProfile,
        *,
        fail_after: str | None = None,
    ) -> None:
        self.base = base
        self.store = base.store
        self.profile = DeliverableSetProfile.model_validate(profile.model_dump(mode="json"))
        self.fail_after = fail_after
        if self.profile.base_profile_digest != base.profile_digest:
            raise ValueError("DELIVERABLE_SET_BASE_PROFILE_MISMATCH")
        self.quote_member = next(
            item for item in self.profile.members if item.deliverable_kind == "QUOTE"
        )
        self.memo_member = next(
            item for item in self.profile.members if item.deliverable_kind == "DISCOUNT_MEMO"
        )
        if self.quote_member.object_id != base.quote_object_id:
            raise ValueError("DELIVERABLE_SET_QUOTE_OBJECT_MISMATCH")

    @staticmethod
    def _maybe_fail(point: str, configured: str | None) -> None:
        if configured == point:
            raise RuntimeError(f"INJECTED_DELIVERABLE_FORMATION_FAILURE:{point}")

    def _memo_request(self, quote_request: TaskRequest) -> TaskRequest:
        slug = self.memo_member.object_id.split(":")[-1]
        return TaskRequest(
            id=f"task:{slug}",
            organization_id=quote_request.organization_id,
            actor_id=self.memo_member.owner_id,
            purpose="discount_exception_memo",
            deliverable_kind="DISCOUNT_MEMO",
            requested_at=quote_request.requested_at,
            template_ref=self.memo_member.template_ref,
            input_values={
                "owner": self.memo_member.owner_id,
                "quote_object_id": self.quote_member.object_id,
            },
            customer_id=quote_request.customer_id,
            idempotency_key=f"workspace:form:{slug}@v1",
        )

    def _prepare_memo(
        self,
        *,
        quote_request: TaskRequest,
        now: str,
        quote: VersionedObject,
    ) -> dict[str, object]:
        request = self._memo_request(quote_request)
        template = self.base.registry.get(request.template_ref)
        if template.digest != self.memo_member.template_digest:
            raise IntegrityError("DELIVERABLE_SET_MEMO_TEMPLATE_DRIFT")
        candidates, interpretation = self.base.interpreter.propose(
            task=request,
            template=template,
        )
        revision_lock = self.base._revision_lock()
        coalition = self.base.planner.plan(
            task=request,
            template=template,
            candidates=candidates,
            cards=default_capability_cards(template.ref),
            revision_lock=revision_lock,
        )
        source_projections = self.base.domain_registry.source_projections(
            task=request,
            template=template,
            plan=coalition,
            now=now,
        )
        claim_candidates, bundles = self.base.domain_registry.execute_selected(
            task=request,
            template=template,
            plan=coalition,
            projections=source_projections,
            now=now,
        )
        decisions = self.base.admission.admit(
            task=request,
            template=template,
            coalition=coalition,
            candidates=claim_candidates,
            now=now,
            policy_revision=revision_lock["policy"],
        )
        context, final_projections = self.base.context_compiler.compile(
            task=request,
            template=template,
            coalition=coalition,
            decisions=decisions,
            candidates=claim_candidates,
            now=now,
            revisions=revision_lock,
        )
        monitor = ExecutionReferenceMonitor(
            task=request,
            template=template,
            manifest=context,
            artifact_reader=None,
            actor_id="workspace-renderer",
            run_id=f"{self.base.workflow_run_id or self.base.default_run_id}:discount-memo",
            clock=StaticClock(now),
            id_factory=self.base._id_factory(request),
        )
        inputs = DiscountMemoInputAssembler().assemble(monitor)
        payload = DiscountMemoRenderer().render(
            task_literals=DiscountMemoTaskLiterals(
                owner=self.memo_member.owner_id,
                customer_id=request.customer_id or "customer:unknown",
                quote_object_id=quote.id,
            ),
            inputs=inputs,
        )
        memo = VersionedObject(
            id=self.memo_member.object_id,
            version="v1",
            kind="WorkItemVersion",
            label=f"{quote.label} Discount Review Memo",
            domain="finance",
            state=ObjectState.CURRENT,
            payload=payload.model_dump(mode="json"),
            source_refs=(context.ref,),
            sensitivity="CONFIDENTIAL",
            allowed_purposes=("discount_exception_memo", "change_rebase"),
            coverage_complete=True,
            coverage_basis=(CoverageBasis.RUNTIME_OBSERVED,),
        )
        trace = monitor.finish(
            output_ref=memo.ref,
            output_payload=memo.payload,
            output_field_lineage=discount_memo_output_lineage(),
        )
        coverage = self.base.coverage_verifier.verify(
            template=template,
            trace=trace,
            output_payload=memo.payload,
            observed_channels=("REFERENCE_MONITOR",),
        )
        manifest = self.base.dependency_compiler.compile(
            task=request,
            template=template,
            trace=trace,
            coverage=coverage,
            consumer_ref=memo.ref,
            consumer_domain=memo.domain,
            revision_lock=revision_lock,
            now=now,
        )
        return {
            "request": request,
            "template": template,
            "interpretation": interpretation,
            "coalition": coalition,
            "source_projections": source_projections,
            "claim_candidates": claim_candidates,
            "bundles": bundles,
            "decisions": decisions,
            "context": context,
            "final_projections": final_projections,
            "memo": memo,
            "trace": trace,
            "coverage": coverage,
            "manifest": manifest,
        }

    @staticmethod
    def _write(artifact_id: str, media_type: str, model: object) -> ArtifactWrite:
        payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
        return prepare_artifact_write(artifact_id, media_type, payload)

    def prepare(
        self,
        request: TaskRequest,
        *,
        run_id: str | None = None,
    ) -> PreparedDeliverableSetFormation:
        if self.base.registry.get(request.template_ref).ref != self.quote_member.template_ref:
            raise ValueError("DELIVERABLE_SET_QUOTE_TEMPLATE_MISMATCH")
        operation_clock = FrozenClock(self.base.clock.now())
        quote_prepared = self.base.prepare_quote(request, run_id=run_id)
        memo_values = self._prepare_memo(
            quote_request=request,
            now=operation_clock.now(),
            quote=quote_prepared.deliverable,
        )
        memo = memo_values["memo"]
        quote_manifest = next(
            write
            for write in quote_prepared.artifact_writes
            if write.media_type == RUNTIME_MANIFEST_MEDIA_TYPE
        )
        quote_runtime_manifest = __import__(
            "orgrebase.workspace.models", fromlist=["RuntimeDependencyManifest"]
        ).RuntimeDependencyManifest.model_validate(quote_manifest.payload)
        memo_manifest = memo_values["manifest"]
        universe = WorkspaceUniverse.model_validate(
            self.store.load_artifact(self.base.universe.id, UNIVERSE_MEDIA_TYPE).payload
        )
        snapshot = self.base.snapshot_builder.build(
            universe=universe,
            replacement_objects=(quote_prepared.deliverable, memo),
            replacement_manifests=(quote_runtime_manifest, memo_manifest),
            targets=tuple(sorted(set(self.base.snapshot_target_ids) | {self.memo_member.object_id})),
            scope_roots=self.base.snapshot_scope_roots,
            now=operation_clock.now(),
            version="v1",
            snapshot_id=self.base.graph_snapshot_id,
            graph_namespace=self.base.graph_pointer_id,
        )
        pointer = graph_pointer_object(
            version="v1",
            snapshot=snapshot,
            promoted_at=operation_clock.now(),
            object_id=self.base.graph_pointer_id,
        )
        memo_receipt = TaskReceipt(
            id=f"task-receipt:{self.memo_member.object_id.split(':')[-1]}@v1",
            task_ref=memo_values["request"].id,
            template_ref=memo_values["template"].ref,
            coalition_plan_ref=memo_values["coalition"].id,
            admission_decision_refs=tuple(item.id for item in memo_values["decisions"]),
            context_manifest_ref=memo_values["context"].ref,
            trace_ref=memo_values["trace"].ref,
            coverage_receipt_ref=memo_values["coverage"].id,
            dependency_manifest_ref=memo_manifest.ref,
            deliverable_ref=memo.ref,
            graph_snapshot_ref=snapshot.ref,
            revision_lock=snapshot.revisions,
            committed_at=operation_clock.now(),
        )
        binding = DeliverableSetBinding(
            id="deliverable-set-binding:workspace",
            version="v1",
            workspace_id=self.store.workspace_id,
            profile_ref=self.profile.ref,
            profile_digest=self.profile.digest,
            base_profile_digest=self.profile.base_profile_digest,
            pack_digest=self.profile.pack_digest,
            runtime_revision=self.profile.runtime_revision,
            members=self.profile.members,
            formed_at=operation_clock.now(),
        )
        deliverables = tuple(sorted((quote_prepared.deliverable, memo), key=lambda item: item.id))
        receipts = (quote_prepared.task_receipt, memo_receipt)
        formation_receipt = DeliverableSetFormationReceipt(
            id="deliverable-set-formation:workspace@v1",
            binding_ref=binding.ref,
            binding_digest=binding.digest,
            task_receipt_refs=tuple(item.id for item in receipts),
            task_receipt_digests=tuple(item.digest for item in receipts),
            deliverable_refs=tuple(item.ref for item in deliverables),
            deliverable_digests=tuple(item.digest for item in deliverables),
            graph_snapshot_ref=snapshot.ref,
            graph_snapshot_digest=snapshot.digest,
            graph_pointer_ref=pointer.ref,
            committed_at=operation_clock.now(),
        )
        writes = [
            write
            for write in quote_prepared.artifact_writes
            if not (
                write.media_type == SNAPSHOT_MEDIA_TYPE
                and write.artifact_id == snapshot.ref
            )
        ]
        for item in (memo_values["request"], memo_values["template"], memo_values["interpretation"], memo_values["coalition"]):
            media = {
                TaskRequest: MEDIA["task"],
                TaskTemplateVersion: MEDIA["template"],
                TaskInterpretationReceipt: MEDIA["interpretation"],
                CoalitionPlan: MEDIA["coalition"],
            }[type(item)]
            writes.append(self._write(item.id if not hasattr(item, "ref") else item.ref, media, item))
        for item in (*memo_values["source_projections"], *memo_values["final_projections"]):
            writes.append(self._write(item.ref, MEDIA["projection"], item))
        for item in memo_values["claim_candidates"]:
            writes.append(self._write(item.digest, MEDIA["candidate"], item))
        for item in memo_values["bundles"]:
            writes.append(self._write(item.id, MEDIA["bundle"], item))
        for item in memo_values["decisions"]:
            writes.append(self._write(item.id, MEDIA["admission"], item))
        for item, artifact_id, media in (
            (memo_values["context"], memo_values["context"].ref, MEDIA["context"]),
            (memo_values["trace"], memo_values["trace"].ref, MEDIA["trace"]),
            (memo_values["coverage"], memo_values["coverage"].id, MEDIA["coverage"]),
            (memo_manifest, memo_manifest.ref, RUNTIME_MANIFEST_MEDIA_TYPE),
            (memo_receipt, memo_receipt.id, MEDIA["receipt"]),
            (snapshot, snapshot.ref, SNAPSHOT_MEDIA_TYPE),
            (self.profile, DELIVERABLE_SET_PROFILE_ARTIFACT_ID, DELIVERABLE_SET_PROFILE_MEDIA_TYPE),
            (binding, DELIVERABLE_SET_BINDING_ARTIFACT_ID, DELIVERABLE_SET_BINDING_MEDIA_TYPE),
            (
                formation_receipt,
                formation_receipt.id,
                DELIVERABLE_SET_FORMATION_MEDIA_TYPE,
            ),
        ):
            writes.append(self._write(artifact_id, media, item))
        request_set_digest = sha256_digest(
            {
                "quote_request": request.digest,
                "memo_request": memo_values["request"].digest,
                "profile": self.profile.digest,
            }
        )
        event_payload = {
            "profile_ref": self.profile.ref,
            "profile_digest": self.profile.digest,
            "binding_ref": binding.ref,
            "binding_digest": binding.digest,
            "deliverable_refs": [item.ref for item in deliverables],
            "task_receipt_digests": [item.digest for item in receipts],
            "snapshot_ref": snapshot.ref,
            "snapshot_digest": snapshot.digest,
            "formation_receipt_digest": formation_receipt.digest,
            "external_effects": "DISABLED",
        }
        return PreparedDeliverableSetFormation(
            request_set_digest=request_set_digest,
            idempotency_key=f"workspace:form:quote-discount-memo:{request.id.split(':')[-1]}@v1",
            profile=self.profile,
            binding=binding,
            quote_prepared=quote_prepared,
            deliverables=deliverables,
            graph_pointer=pointer,
            artifact_writes=tuple(writes),
            task_receipts=receipts,
            formation_receipt=formation_receipt,
            event_payload=event_payload,
        )

    @staticmethod
    def _indexed_writes(prepared: PreparedDeliverableSetFormation) -> dict[str, ArtifactWrite]:
        indexed = {item.artifact_id: item for item in prepared.artifact_writes}
        if len(indexed) != len(prepared.artifact_writes):
            raise IntegrityError("DELIVERABLE_FORMATION_ARTIFACT_ID_DUPLICATE")
        return indexed

    def _verify_memo(self, prepared: PreparedDeliverableSetFormation) -> None:
        indexed = self._indexed_writes(prepared)
        quote = next(
            item for item in prepared.deliverables if item.payload.get("deliverable_kind") == "QUOTE"
        )
        memo = next(
            item
            for item in prepared.deliverables
            if item.payload.get("deliverable_kind") == "DISCOUNT_MEMO"
        )
        quote_request = self.base.default_request(self.base.profile)
        expected = self._prepare_memo(
            quote_request=quote_request,
            now=prepared.formation_receipt.committed_at,
            quote=quote,
        )
        expected_receipt = prepared.task_receipts[1]
        checks = (
            (memo.digest, expected["memo"].digest),
            (expected_receipt.task_ref, expected["request"].id),
            (expected_receipt.template_ref, expected["template"].ref),
            (expected_receipt.coalition_plan_ref, expected["coalition"].id),
            (expected_receipt.context_manifest_ref, expected["context"].ref),
            (expected_receipt.trace_ref, expected["trace"].ref),
            (expected_receipt.coverage_receipt_ref, expected["coverage"].id),
            (expected_receipt.dependency_manifest_ref, expected["manifest"].ref),
            (expected_receipt.deliverable_ref, memo.ref),
        )
        if any(actual != wanted for actual, wanted in checks):
            raise IntegrityError("DELIVERABLE_MEMO_REPLAY_MISMATCH")
        expected_models = (
            (expected["request"].id, expected["request"]),
            (expected["template"].ref, expected["template"]),
            (expected["interpretation"].id, expected["interpretation"]),
            (expected["coalition"].id, expected["coalition"]),
            (expected["context"].ref, expected["context"]),
            (expected["trace"].ref, expected["trace"]),
            (expected["coverage"].id, expected["coverage"]),
            (expected["manifest"].ref, expected["manifest"]),
        )
        for artifact_id, model in expected_models:
            write = indexed.get(artifact_id)
            if write is None or write.payload != model.model_dump(mode="json"):
                raise IntegrityError(f"DELIVERABLE_MEMO_EVIDENCE_MISMATCH:{artifact_id}")

    def commit(
        self,
        prepared: PreparedDeliverableSetFormation,
        *,
        connection: Connection | None = None,
    ) -> DeliverableSetFormationReceipt:
        selected = PreparedDeliverableSetFormation.model_validate(prepared.model_dump(mode="json"))
        if selected.profile.digest != self.profile.digest:
            raise IntegrityError("DELIVERABLE_SET_PROFILE_MISMATCH")
        if connection is None:
            with self.store.transaction() as owned:
                return self._commit_in_transaction(selected, owned)
        return self._commit_in_transaction(selected, connection)

    def _commit_in_transaction(
        self,
        prepared: PreparedDeliverableSetFormation,
        connection: Connection,
    ) -> DeliverableSetFormationReceipt:
        from orgrebase.workspace.formation_integrity import verify_prepared_formation

        authorization = current_authorization()
        completed = False

        def require_completion() -> None:
            if not completed:
                raise IntegrityError("DELIVERABLE_FORMATION_COMMIT_INCOMPLETE")
            if authorization is not None:
                authorization()

        self.store.require_before_commit(connection, require_completion)
        if authorization is not None:
            authorization()
        expected = self.prepare(
            self.base.default_request(self.base.profile),
            run_id=self.base.workflow_run_id,
        )
        scalar_bindings = (
            (prepared.request_set_digest, expected.request_set_digest, "REQUEST_SET_DIGEST"),
            (prepared.idempotency_key, expected.idempotency_key, "IDEMPOTENCY_KEY"),
            (prepared.event_type, expected.event_type, "EVENT_TYPE"),
        )
        for actual, wanted, field in scalar_bindings:
            if actual != wanted:
                raise IntegrityError(f"DELIVERABLE_FORMATION_{field}_MISMATCH")
        model_bindings = (
            (prepared.profile, expected.profile, "PROFILE"),
            (prepared.binding, expected.binding, "BINDING"),
            (prepared.quote_prepared, expected.quote_prepared, "QUOTE_PREPARED"),
            (prepared.graph_pointer, expected.graph_pointer, "GRAPH_POINTER"),
            (prepared.formation_receipt, expected.formation_receipt, "FORMATION_RECEIPT"),
        )
        for actual, wanted, field in model_bindings:
            if actual.digest != wanted.digest:
                raise IntegrityError(f"DELIVERABLE_FORMATION_{field}_MISMATCH")
        if (
            tuple(item.digest for item in prepared.deliverables)
            != tuple(item.digest for item in expected.deliverables)
            or tuple(item.digest for item in prepared.task_receipts)
            != tuple(item.digest for item in expected.task_receipts)
        ):
            raise IntegrityError("DELIVERABLE_FORMATION_MEMBER_SET_MISMATCH")
        if prepared.event_payload != expected.event_payload:
            raise IntegrityError("DELIVERABLE_FORMATION_EVENT_MISMATCH")
        actual_writes = self._indexed_writes(prepared)
        expected_writes = self._indexed_writes(expected)
        if set(actual_writes) != set(expected_writes):
            raise IntegrityError("DELIVERABLE_FORMATION_ARTIFACT_SET_MISMATCH")
        for artifact_id, wanted in expected_writes.items():
            actual = actual_writes[artifact_id]
            if (
                actual.media_type != wanted.media_type
                or actual.payload_digest != wanted.payload_digest
                or actual.payload != wanted.payload
            ):
                raise IntegrityError(
                    f"DELIVERABLE_FORMATION_ARTIFACT_MISMATCH:{artifact_id}"
                )
        if prepared.digest != expected.digest:
            raise IntegrityError("DELIVERABLE_FORMATION_BUNDLE_MISMATCH")
        verify_prepared_formation(
            self.base,
            prepared.quote_prepared,
            media=MEDIA,
            profile_binding_artifact_id=PROFILE_BINDING_ARTIFACT_ID,
            profile_binding_media_type=PROFILE_BINDING_MEDIA_TYPE,
            snapshot_target_ids=self.base.snapshot_target_ids,
            snapshot_scope_roots=self.base.snapshot_scope_roots,
        )
        self._verify_memo(prepared)
        indexed = actual_writes
        snapshot = __import__(
            "orgrebase.workspace.models", fromlist=["WorkspaceGraphSnapshot"]
        ).WorkspaceGraphSnapshot.model_validate(
            indexed[prepared.formation_receipt.graph_snapshot_ref].payload
        )
        if (
            snapshot.digest != prepared.formation_receipt.graph_snapshot_digest
            or prepared.graph_pointer.payload.get("snapshot_digest") != snapshot.digest
            or prepared.binding.digest != prepared.formation_receipt.binding_digest
        ):
            raise IntegrityError("DELIVERABLE_FORMATION_GRAPH_BINDING_INVALID")
        for write in prepared.artifact_writes:
            if sha256_digest(write.payload) != write.payload_digest:
                raise IntegrityError(f"DELIVERABLE_FORMATION_ARTIFACT_DIGEST_MISMATCH:{write.artifact_id}")
        existing = self.store.get_idempotent(
            prepared.idempotency_key,
            prepared.request_set_digest,
            connection=connection,
        )
        if existing is not None:
            completed = True
            return DeliverableSetFormationReceipt.model_validate(existing)
        for index, deliverable in enumerate(prepared.deliverables):
            self.store.insert_version(connection, deliverable, make_current=True)
            self._maybe_fail(f"deliverable:{deliverable.id}", self.fail_after)
            if index == 0:
                self._maybe_fail("first-deliverable", self.fail_after)
        self.store.insert_version(connection, prepared.graph_pointer, make_current=True)
        for write in prepared.artifact_writes:
            self.store.save_artifact(connection, write.artifact_id, write.media_type, write.payload)
            self._maybe_fail(f"artifact:{write.artifact_id}", self.fail_after)
        self._maybe_fail("graph-pointer", self.fail_after)
        self.store.append_event(connection, prepared.event_type, prepared.event_payload)
        self.store.save_idempotent(
            connection,
            prepared.idempotency_key,
            prepared.request_set_digest,
            prepared.formation_receipt.model_dump(mode="json"),
        )
        self.store.save_idempotent(
            connection,
            prepared.quote_prepared.idempotency_key,
            prepared.quote_prepared.request_digest,
            prepared.quote_prepared.task_receipt.model_dump(mode="json"),
        )
        completed = True
        return prepared.formation_receipt

    def form(
        self,
        request: TaskRequest,
        *,
        run_id: str | None = None,
    ) -> DeliverableSetFormationReceipt:
        return self.commit(self.prepare(request, run_id=run_id))
