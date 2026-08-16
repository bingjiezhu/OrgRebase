"""Application service for the recurring Workspace build/recovery loop."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from orgrebase.certificates import build_minimal_rebase_certificate
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    ChangeSetRevision,
    EvidenceClass,
    ObjectDelta,
    RunEnvelope,
    SemanticClassification,
    ToolInvocationReceipt,
)
from orgrebase.fixture import load_fixture
from orgrebase.impact import ImpactEngine, classify_semantic_delta
from orgrebase.store import StateStore
from orgrebase.tools import DependencyEvidenceTool
from orgrebase.workflow import RebaseWorkflow
from orgrebase.workspace.advisory import WorkspaceApplyAdvisoryVerifier, WorkspaceChangeAdvisoryAdapter
from orgrebase.workspace.formation import WorkspaceFormationService, seed_workspace_store
from orgrebase.workspace.graph import SNAPSHOT_MEDIA_TYPE, WorkspaceSnapshotBuilder
from orgrebase.workspace.models import (
    TaskReceipt,
    TaskRequest,
    ToolCalledEvent,
    WorkflowIdentity,
    WorkspaceChangeSpec,
    WorkspaceGraphSnapshot,
    WorkspacePreviewBundle,
    WorkspaceRebaseReceipt,
    WorkTrace,
)
from orgrebase.workspace.rebuild import (
    QuoteRebuildPayloadHandler,
    WorkspaceGraphApplyExtension,
    WorkspaceRebuildContextProvider,
    WorkspaceWorkflowClock,
)

WORKSPACE_RUN_ID = "run:workspace:complete@v1"
WORKSPACE_RUN_NONCE = sha256_digest(
    {"run_id": WORKSPACE_RUN_ID, "mode": "LOCAL_DETERMINISTIC"}
).split(":", 1)[1]
TOOL_INVOCATION_MEDIA_TYPE = "application/vnd.orgrebase.tool-invocation+json"
TOOL_CALLED_EVENT_MEDIA_TYPE = "application/vnd.orgrebase.workspace-tool-called-event+json"

WORKSPACE_IDENTITY = WorkflowIdentity(
    namespace="workspace",
    approval_prefix="approval:workspace",
    receipt_prefix="rebase:workspace",
    coordination_prefix="coordination:workspace",
    idempotency_prefix="apply:workspace",
    extension_receipt_prefix="workspace-rebase",
)


class WorkspaceChangeSetBuilder:
    def build(self, *, fixture: object, spec: WorkspaceChangeSpec) -> ChangeSetRevision:
        base = fixture.object(spec.object_id, spec.base_version)
        proposed = fixture.object(spec.object_id, spec.proposed_version)
        base_value = base.payload["canonical_value"]
        proposed_value = proposed.payload["canonical_value"]
        semantic = classify_semantic_delta(base_value, proposed_value)
        delta = ObjectDelta(
            object_id=spec.object_id,
            base_version=spec.base_version,
            proposed_version=spec.proposed_version,
            base_value=base_value,
            proposed_value=proposed_value,
            changed_fields=(
                ()
                if semantic == SemanticClassification.NO_SEMANTIC_DELTA
                else ("canonical_value",)
            ),
            semantic_classification=semantic,
            admitted_by=(spec.owner_id if semantic == SemanticClassification.SEMANTIC_DELTA else None),
        )
        return ChangeSetRevision(
            id=spec.id,
            revision=spec.revision,
            state=(
                "READY_FOR_PREVIEW"
                if semantic == SemanticClassification.SEMANTIC_DELTA
                else "NO_SEMANTIC_DELTA"
            ),
            owner_id=spec.owner_id,
            purpose=spec.purpose,
            scope=tuple(fixture.impact_targets),
            deltas=(delta,),
        )


class WorkspaceService:
    """Stateful façade whose canonical state is entirely in :class:`StateStore`."""

    def __init__(self, *, store_path: str | Path = ":memory:") -> None:
        self.store_path = str(store_path)
        self.store = StateStore(store_path)
        seed_workspace_store(self.store)
        self.formation = WorkspaceFormationService(self.store)
        self.snapshot_builder = WorkspaceSnapshotBuilder()
        self.change_builder = WorkspaceChangeSetBuilder()
        self.advisory_factory = WorkspaceChangeAdvisoryAdapter()
        self.advisory_verifier = WorkspaceApplyAdvisoryVerifier()
        self.legacy = load_fixture()
        self.last_preview_bundle: WorkspacePreviewBundle | None = None
        self.last_rebase_receipt: object | None = None
        self.last_workspace_receipt: WorkspaceRebaseReceipt | None = None

    def close(self) -> None:
        self.store.close()

    @classmethod
    def reopen(cls, store_path: str | Path) -> WorkspaceService:
        return cls(store_path=store_path)

    def form_quote(self, request: TaskRequest | None = None) -> TaskReceipt:
        return self.formation.form_quote(request or self.formation.default_request())

    def _invoke_dependency_evidence_tool(
        self, formation: TaskReceipt
    ) -> tuple[dict[str, Any], ToolCalledEvent, str]:
        """Call the real read-only tool after Quote v1 and persist both audit artifacts."""

        trace = WorkTrace.model_validate(self.store.load_artifact(formation.trace_ref).payload)
        fixture = self._fixture_for_change(self.change_spec("launch_date"))
        tool = DependencyEvidenceTool(fixture, self.store)
        invocation = tool.invoke(
            actor_id="gtm-steward",
            workflow_run_id=WORKSPACE_RUN_ID,
            run_nonce=WORKSPACE_RUN_NONCE,
            target_ids=(formation.deliverable_ref.rsplit("@", 1)[0],),
            graph_revision=fixture.revisions["graph"],
            idempotency_key="workspace-quote-v1-dependency-evidence",
            started_at="2026-08-15T00:01:00Z",
            completed_at="2026-08-15T00:01:01Z",
            evidence_class=EvidenceClass.LOCAL_REAL_TOOL,
        )
        receipt = ToolInvocationReceipt.model_validate(invocation["receipt"])
        stored_invocation = self.store.load_artifact(receipt.id, TOOL_INVOCATION_MEDIA_TYPE)
        called_event = ToolCalledEvent(
            event_id="tool-called:dependency-evidence@v1",
            task_ref=formation.task_ref,
            run_id=WORKSPACE_RUN_ID,
            sequence=len(trace.events) + 1,
            tool_ref=receipt.tool_ref,
            invocation_receipt_ref=receipt.id,
            request_digest=receipt.request_digest,
            result_digest=receipt.result_digest,
            occurred_at=receipt.completed_at,
            idempotency_key="workspace-quote-v1-dependency-evidence:event",
            previous_event_digest=trace.head_event_digest,
        )
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                called_event.event_id,
                TOOL_CALLED_EVENT_MEDIA_TYPE,
                called_event.model_dump(mode="json"),
            )
        persisted_event = ToolCalledEvent.model_validate(
            self.store.load_artifact(
                called_event.event_id, TOOL_CALLED_EVENT_MEDIA_TYPE
            ).payload
        )
        return stored_invocation.payload, persisted_event, trace.run_id

    def current_quote(self):
        return self.store.get_object("work:quote_acme")

    def current_graph_pointer(self):
        return self.store.get_object("graph:workspace")

    def state(self) -> dict[str, Any]:
        quote = None
        with suppress(KeyError):
            quote = self.current_quote()
        pointer = None
        with suppress(KeyError):
            pointer = self.current_graph_pointer()
        return {
            "quote": quote,
            "graph_pointer": pointer,
            "event_chain": self.store.verify_event_chain(),
        }

    def current_snapshot(self) -> WorkspaceGraphSnapshot:
        pointer = self.current_graph_pointer()
        return WorkspaceGraphSnapshot.model_validate(
            self.store.load_artifact(str(pointer.payload["snapshot_ref"]), SNAPSHOT_MEDIA_TYPE).payload
        )

    def _fixture_for_change(self, spec: WorkspaceChangeSpec):
        snapshot = self.current_snapshot()
        if snapshot.ref != spec.expected_snapshot_ref or snapshot.digest != spec.expected_snapshot_digest:
            raise RuntimeError("WORKSPACE_CHANGE_EXPECTED_SNAPSHOT_MISMATCH")
        return self.snapshot_builder.to_enterprise_fixture(
            snapshot=snapshot,
            artifact_reader=self.store.load_artifact,
            object_reader=self.store.get_object,
            agents=self.legacy.agents,
            evaluation_cases=self.legacy.evaluation_cases,
            context_profiles={
                "workspace-quote-agent": {
                    "target": "work:quote_acme",
                    "include": [
                        "claim:product.enterprise_plan",
                        "claim:product.launch_date",
                        "claim:product.residency_capability",
                        "claim:legal.customer_notice_required",
                        "policy:finance.price_band",
                        "policy:finance.currency",
                        "claim:gtm.partner_terms",
                        "skill:enterprise-quote-compose",
                    ],
                    "explicitly_consider": [],
                }
            },
            change={
                "id": spec.id,
                "revision": spec.revision,
                "owner_id": spec.owner_id,
                "purpose": spec.purpose,
                "object_id": spec.object_id,
                "base_version": spec.base_version,
                "proposed_version": spec.proposed_version,
            },
        )

    def change_spec(self, kind: str) -> WorkspaceChangeSpec:
        snapshot = self.current_snapshot()
        if kind == "launch_date":
            return WorkspaceChangeSpec(
                id="changeset:workspace-launch-date",
                revision="r1",
                owner_id="human:product-owner",
                purpose="change_rebase",
                object_id="claim:product.launch_date",
                base_version="v7",
                proposed_version="v8",
                expected_snapshot_ref=snapshot.ref,
                expected_snapshot_digest=snapshot.digest,
                idempotency_key="workspace:apply:launch-date@r1",
            )
        if kind == "currency":
            return WorkspaceChangeSpec(
                id="changeset:workspace-currency",
                revision="r1",
                owner_id="human:finance-owner",
                purpose="change_rebase",
                object_id="policy:finance.currency",
                base_version="v1",
                proposed_version="v2",
                expected_snapshot_ref=snapshot.ref,
                expected_snapshot_digest=snapshot.digest,
                idempotency_key="workspace:apply:currency@r1",
            )
        raise KeyError(f"UNKNOWN_WORKSPACE_CHANGE:{kind}")

    @staticmethod
    def _run_envelope(spec: WorkspaceChangeSpec) -> RunEnvelope:
        nonce = sha256_digest({"change": spec.digest, "mode": "LOCAL_DETERMINISTIC"}).split(":", 1)[1]
        return RunEnvelope(
            run_id=f"run:workspace:{spec.id.split(':')[-1]}@{spec.revision}",
            nonce=nonce,
            issued_at="2026-08-15T00:00:00Z",
            expires_at="2026-08-16T00:00:00Z",
            mode="LOCAL_DETERMINISTIC",
            evidence_class="LOCAL_DETERMINISTIC",
        )

    def preview_change(self, kind: str) -> WorkspacePreviewBundle:
        spec = self.change_spec(kind)
        fixture = self._fixture_for_change(spec)
        change_set = self.change_builder.build(fixture=fixture, spec=spec)
        preview = ImpactEngine(fixture).preview(change_set)
        minimal = build_minimal_rebase_certificate(change_set, preview)
        envelope = self._run_envelope(spec)
        advisory_payload = self.advisory_factory.run(
            change_set=change_set, preview=preview, run_envelope=envelope
        )
        advisory = self.advisory_verifier.verify(
            fixture=fixture,
            change_set=change_set,
            preview=preview,
            collaboration=advisory_payload,
            run_envelope=envelope,
        )
        bundle = WorkspacePreviewBundle(
            change_spec=spec,
            snapshot_ref=spec.expected_snapshot_ref,
            snapshot_digest=spec.expected_snapshot_digest,
            change_set=change_set,
            preview=preview,
            minimal_rebase_certificate=minimal,
            advisory=advisory,
            run_envelope=envelope,
        )
        self.last_preview_bundle = bundle
        return bundle

    def apply_change(
        self,
        kind: str,
        *,
        fail_after: str | None = None,
        current_revisions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        bundle = self.preview_change(kind)
        fixture = self._fixture_for_change(bundle.change_spec)
        clock = WorkspaceWorkflowClock(
            approved_at="2026-08-15T00:03:00Z",
            apply_at=("2026-08-15T00:04:00Z" if kind == "launch_date" else "2026-08-15T01:04:00Z"),
            expires_at="2026-08-16T00:00:00Z",
        )
        extension = WorkspaceGraphApplyExtension(fail_after=fail_after)
        workflow = RebaseWorkflow(
            fixture,
            self.store,
            identity=WORKSPACE_IDENTITY,
            clock=clock,
            context_provider=WorkspaceRebuildContextProvider(clock=clock),
            rebuild_handlers={"QUOTE": QuoteRebuildPayloadHandler()},
            apply_extension=extension,
            advisory_verifier=self.advisory_verifier,
        )
        approval = workflow.approve(
            bundle.change_set,
            bundle.preview,
            bundle.minimal_rebase_certificate,
            actor_id=bundle.change_spec.owner_id,
        )
        collaboration = {
            "orchestration_plan": bundle.advisory.orchestration_plan,
            "compilation_receipt": bundle.advisory.compilation_receipt,
            "coordination_receipt": bundle.advisory.coordination_receipt,
            "candidate_ingestion": bundle.advisory.ingestion_receipt,
            "handoffs": bundle.advisory.handoffs,
            "agent_runs": bundle.advisory.agent_runs,
        }
        receipt = workflow.apply(
            change_set=bundle.change_set,
            preview=bundle.preview,
            minimal_rebase_certificate=bundle.minimal_rebase_certificate,
            approval=approval,
            collaboration=collaboration,
            run_envelope=bundle.run_envelope,
            current_revisions=(current_revisions or fixture.revisions),
            idempotency_key=bundle.change_spec.idempotency_key,
        )
        workspace_receipt = workflow.last_extension_receipt
        if workspace_receipt is None:
            predictable = (
                f"workspace-rebase:{bundle.change_set.id.split(':')[-1]}"
                f"@{bundle.change_set.revision}"
            )
            try:
                workspace_receipt = WorkspaceRebaseReceipt.model_validate(
                    self.store.load_artifact(
                        predictable,
                        "application/vnd.orgrebase.workspace-rebase-receipt+json",
                    ).payload
                )
            except (KeyError, ValueError):
                workspace_receipt = None
        self.last_rebase_receipt = receipt
        self.last_workspace_receipt = workspace_receipt
        return {
            "change_spec": bundle.change_spec,
            "change_set": bundle.change_set,
            "preview": bundle.preview,
            "minimal_rebase_certificate": bundle.minimal_rebase_certificate,
            "approval": approval,
            "rebase_receipt": receipt,
            "workspace_rebase_receipt": workspace_receipt,
            "quote": self.current_quote(),
            "graph_pointer": self.current_graph_pointer(),
            "event_chain": self.store.verify_event_chain(),
        }

    def run_local_loop(self) -> dict[str, Any]:
        formation = self.form_quote()
        tool_invocation, tool_called_event, formation_run_id = (
            self._invoke_dependency_evidence_tool(formation)
        )
        launch = self.apply_change("launch_date")
        if self.store_path == ":memory:":
            currency = self.apply_change("currency")
        else:
            self.close()
            reopened = WorkspaceService.reopen(self.store_path)
            try:
                currency = reopened.apply_change("currency")
                final_quote = reopened.current_quote()
                final_pointer = reopened.current_graph_pointer()
                chain = reopened.store.verify_event_chain()
            finally:
                reopened.close()
            return {
                "workflow_run_id": WORKSPACE_RUN_ID,
                "run_id": WORKSPACE_RUN_ID,
                "run_nonce": WORKSPACE_RUN_NONCE,
                "stage_run_ids": {
                    "formation": formation_run_id,
                    "dependency_evidence_tool": tool_invocation["receipt"][
                        "workflow_run_id"
                    ],
                    "launch_rebase": launch["rebase_receipt"].workflow_run_id,
                    "currency_rebase": currency["rebase_receipt"].workflow_run_id,
                },
                "formation": formation,
                "tool_invocation": tool_invocation,
                "tool_called_event": tool_called_event,
                "launch_change": launch,
                "currency_change": currency,
                "final_quote": final_quote,
                "final_graph_pointer": final_pointer,
                "event_chain": chain,
            }
        return {
            "workflow_run_id": WORKSPACE_RUN_ID,
            "run_id": WORKSPACE_RUN_ID,
            "run_nonce": WORKSPACE_RUN_NONCE,
            "stage_run_ids": {
                "formation": formation_run_id,
                "dependency_evidence_tool": tool_invocation["receipt"]["workflow_run_id"],
                "launch_rebase": launch["rebase_receipt"].workflow_run_id,
                "currency_rebase": currency["rebase_receipt"].workflow_run_id,
            },
            "formation": formation,
            "tool_invocation": tool_invocation,
            "tool_called_event": tool_called_event,
            "launch_change": launch,
            "currency_change": currency,
            "final_quote": self.current_quote(),
            "final_graph_pointer": self.current_graph_pointer(),
            "event_chain": self.store.verify_event_chain(),
        }
