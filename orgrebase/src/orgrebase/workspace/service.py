"""Application service for the recurring Workspace build/recovery loop."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from orgrebase.auth import (
    CONTROLLED_LOCAL_SESSION_IDENTITY,
    AuthenticationError,
    current_authorization,
    identity_claim_boundary,
)
from orgrebase.certificates import build_minimal_rebase_certificate
from orgrebase.clock import Clock, FrozenClock, SystemClock, utc_datetime, workflow_times
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    Approval,
    AuthorizationError,
    ChangeSetRevision,
    EvidenceClass,
    IntegrityError,
    ObjectDelta,
    ObjectState,
    RebaseReceipt,
    RunEnvelope,
    SemanticClassification,
    ToolInvocationReceipt,
    VersionedObject,
)
from orgrebase.fixture import EnterpriseFixture, load_fixture
from orgrebase.impact import ImpactEngine, classify_semantic_delta
from orgrebase.private_records import PrivateRecordStore
from orgrebase.runtime_contracts import StoredArtifact, WorkflowIdentity
from orgrebase.store import StateStore
from orgrebase.tools import (
    DependencyEvidenceTool,
    PreparedDependencyEvidenceInvocation,
)
from orgrebase.workflow import RebaseWorkflow
from orgrebase.workspace.advisory import WorkspaceApplyAdvisoryVerifier, WorkspaceChangeAdvisoryAdapter
from orgrebase.workspace.agentteams_execution_plan import AgentTeamsExecutionPlan
from orgrebase.workspace.changes import ChangeRegistry
from orgrebase.workspace.context import exclusion_reason_counts
from orgrebase.workspace.context_residency import TaskAgentContextEnvelope
from orgrebase.workspace.demand_formation import TaskFormationDecisionReceipt
from orgrebase.workspace.domain_agents import LocalDomainCandidateRegistry
from orgrebase.workspace.experience import GovernedExperienceService
from orgrebase.workspace.formation import (
    PROFILE_BINDING_ARTIFACT_ID,
    PROFILE_BINDING_MEDIA_TYPE,
    WorkspaceFormationService,
)
from orgrebase.workspace.graph import SNAPSHOT_MEDIA_TYPE, WorkspaceSnapshotBuilder, split_ref
from orgrebase.workspace.models import (
    ActorContextProjection,
    ChangeEvent,
    DomainPack,
    EnterpriseBinding,
    EnterpriseResourceBinding,
    ModelRequestV2,
    ModelResponseReceiptV2,
    OACActivationConsumptionReceipt,
    PreparedFormationBundle,
    TaskContextManifest,
    TaskReceipt,
    TaskRequest,
    ToolCalledEvent,
    WorkspaceApprovalBinding,
    WorkspaceChangeSpec,
    WorkspaceGraphSnapshot,
    WorkspacePreviewBundle,
    WorkspaceRebaseReceipt,
    WorkTrace,
)
from orgrebase.workspace.ports import ModelProvider
from orgrebase.workspace.preview_execution import execute_attempt, require_attempt_sources, reserve_attempt
from orgrebase.workspace.profile import (
    EnterpriseSeedAdmissionReceipt,
    EnterpriseSeedProfile,
    northstar_acme_quote_profile,
    parse_enterprise_seed_admission_receipt,
    parse_enterprise_seed_profile,
    profile_summary,
    require_reference_runtime_compatible,
)
from orgrebase.workspace.quote_skill_qualification import has_complete_case_identity
from orgrebase.workspace.read_dependencies import (
    ReadDependencyError,
    validate_read_dependencies,
    validate_source_observations,
)
from orgrebase.workspace.rebuild import (
    QuoteRebuildPayloadHandler,
    WorkspaceGraphApplyExtension,
    WorkspaceRebuildContextProvider,
    WorkspaceWorkflowClock,
)
from orgrebase.workspace.runtime_revision import (
    bind_preview_runtime,
    preview_runtime_status,
    require_preview_runtime,
)
from orgrebase.workspace.skill_packages import PARTITIONS, SKILL_REGISTRY_AUTHORITY
from orgrebase.workspace.source_admission import (
    EnterpriseSeedRuntimeProjectionReceipt,
    EnterpriseSeedSourceAdmissionReceipt,
    admit_enterprise_seed_sources,
    parse_enterprise_seed_runtime_projection_receipt,
    parse_enterprise_seed_source_admission_receipt,
    verify_reference_runtime_projections,
)

WORKSPACE_RUN_ID = "run:workspace:complete@v1"
WORKSPACE_RUN_NONCE = sha256_digest({"run_id": WORKSPACE_RUN_ID, "mode": "LOCAL_DETERMINISTIC"}).split(
    ":", 1
)[1]
TOOL_INVOCATION_MEDIA_TYPE = "application/vnd.orgrebase.tool-invocation+json"
TOOL_CALLED_EVENT_MEDIA_TYPE = "application/vnd.orgrebase.workspace-tool-called-event+json"
DEPENDENCY_TOOL_IDEMPOTENCY_KEY = "workspace-quote-v1-dependency-evidence"
DEPENDENCY_TOOL_RECEIPT_ID = f"tool-call:gtm-steward:{DEPENDENCY_TOOL_IDEMPOTENCY_KEY}"
DEPENDENCY_TOOL_CALLED_EVENT_ID = "tool-called:dependency-evidence@v1"
WORKSPACE_PREVIEW_MEDIA_TYPE = "application/vnd.orgrebase.workspace-preview-bundle+json"
WORKSPACE_APPROVAL_MEDIA_TYPE = "application/vnd.orgrebase.workspace-approval+json"
WORKSPACE_APPROVAL_BINDING_MEDIA_TYPE = "application/vnd.orgrebase.workspace-approval-binding+json"
WORKSPACE_OUTCOME_MEDIA_TYPE = "application/vnd.orgrebase.workspace-apply-outcome+json"
WORKSPACE_REVIEW_GATE_MEDIA_TYPE = "application/vnd.orgrebase.workspace-review-gate+json"
WORKSPACE_TASK_CONTEXT_MEDIA_TYPE = "application/vnd.orgrebase.task-context+json"
WORKSPACE_ACTOR_CONTEXT_MEDIA_TYPE = "application/vnd.orgrebase.actor-context-projection+json"
OAC_ACTIVATION_CONSUMPTION_ARTIFACT_ID = "oac-activation-consumption:quote-v1@r1"
OAC_ACTIVATION_CONSUMPTION_MEDIA_TYPE = (
    "application/vnd.orgrebase.oac-activation-consumption-receipt+json"
)
TASK_INTAKE_RUN_ARTIFACT_ID = "task-intake:quote-v1@r1"
TASK_INTAKE_RUN_MEDIA_TYPE = (
    "application/vnd.orgrebase.workspace-task-intake-run-receipt+json"
)
TASK_INTAKE_BOUND_EVENT_TYPE = "WORKSPACE_TASK_INTAKE_BOUND"
TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID = (
    "task-intake-private-work-description:quote-v1@r1"
)
TASK_INTAKE_WORK_DESCRIPTION_MEDIA_TYPE = (
    "application/vnd.orgrebase.workspace-task-intake-work-description-record+json"
)
TASK_INTAKE_WORKSPACE_INSTANCE_KEY = "workspace-task-intake-instance:v1"
TASK_INTAKE_WORKSPACE_INSTANCE_REQUEST_DIGEST = sha256_digest(
    {"schema_version": "orgrebase.workspace-task-intake-instance-request.v1"}
)
GOLDEN_COMPETITION_ARTIFACT_ID = "competition-evidence:golden@v1"
GOLDEN_COMPETITION_MEDIA_TYPE = "application/vnd.orgrebase.golden-competition-evidence+json"
GOLDEN_COMPETITION_EVENT_TYPE = "GOLDEN_COMPETITION_ACCEPTED"
GOLDEN_EXECUTION_RESERVATION_ARTIFACT_ID = "competition-execution-reservation:golden@v1"
GOLDEN_EXECUTION_RESERVATION_MEDIA_TYPE = (
    "application/vnd.orgrebase.golden-execution-reservation+json"
)
GOLDEN_COMPETITION_MODE = "golden"
COMPETITION_OFF_MODE = "off"
_SUPPORTED_COMPETITION_MODES = {
    GOLDEN_COMPETITION_MODE,
    COMPETITION_OFF_MODE,
}
_SUPPORTED_COMPETITION_MODEL_PROVIDERS = {"ollama-local", "vertex-ai", "deepseek"}
BODY_ACTOR_COMPATIBILITY = "BODY_ACTOR_COMPATIBILITY"
CONTROLLED_LOCAL_HEADER_IDENTITY = "CONTROLLED_LOCAL_HEADER_IDENTITY"
VERIFIED_PRINCIPAL_IDENTITY = "VERIFIED_PRINCIPAL_IDENTITY"
_SUPPORTED_APPROVAL_IDENTITY_MODES = {
    BODY_ACTOR_COMPATIBILITY,
    VERIFIED_PRINCIPAL_IDENTITY,
    CONTROLLED_LOCAL_HEADER_IDENTITY,
    CONTROLLED_LOCAL_SESSION_IDENTITY,
}

_REFERENCE_PROFILE = northstar_acme_quote_profile()
WORKSPACE_SCENARIO = _REFERENCE_PROFILE.scenario_view()
WORKSPACE_BOUNDARIES = {
    "execution_profile": "LOCAL_DETERMINISTIC",
    "agentteams": "NOT_RUN",
    "external_enterprise_systems": "NOT_RUN",
    "external_user_validation": "NOT_RUN",
    "oac_runtime_bridge": "NOT_USED_IN_THIS_RUN",
    "data_profile": _REFERENCE_PROFILE.data_class.value,
}
EXPLICIT_OWNER_APPROVAL_MODE = "EXPLICIT_OWNER_COMMAND"
CONTROLLED_LOCAL_APPROVAL_INPUT_MODE = "CONTROLLED_LOCAL_SCRIPTED_COMMAND"


def _serialized(method):
    """Keep one workspace command and its read view coherent."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._command_lock:
            depth = getattr(self, "_command_depth", 0)
            if depth == 0 and hasattr(self, "changes") and kwargs.get("connection") is None:
                self.changes.refresh()
            self._command_depth = depth + 1
            try:
                return method(self, *args, **kwargs)
            finally:
                self._command_depth = depth


    return wrapped


class WorkspaceReviewGatePending(RuntimeError):
    """Stable, machine-readable conflict raised before review time elapses."""

    code = "WORKSPACE_REVIEW_GATE_NOT_READY"

    def __init__(self, *, remaining_ms: int, not_before: str) -> None:
        self.remaining_ms = remaining_ms
        self.not_before = not_before
        super().__init__(f"{self.code}:remaining_ms={remaining_ms},not_before={not_before}")


WORKSPACE_IDENTITY = WorkflowIdentity(
    namespace="workspace",
    approval_prefix="approval:workspace",
    receipt_prefix="rebase:workspace",
    coordination_prefix="coordination:workspace",
    idempotency_prefix="apply:workspace",
    extension_receipt_prefix="workspace-rebase",
)


class WorkspaceChangeSetBuilder:
    def build(
        self,
        *,
        fixture: EnterpriseFixture,
        spec: WorkspaceChangeSpec,
    ) -> ChangeSetRevision:
        base = fixture.object(spec.object_id, spec.base_version)
        proposed = fixture.object(spec.object_id, spec.proposed_version)
        base_value = base.payload["canonical_value"]
        proposed_value = proposed.payload["canonical_value"]
        semantic = classify_semantic_delta(base_value, proposed_value)
        readmission = spec.operation == "READMIT"
        if readmission and semantic == SemanticClassification.NO_SEMANTIC_DELTA:
            semantic = SemanticClassification.TRUST_REVALIDATION
        changed_fields = (() if base_value == proposed_value else ("canonical_value",))
        if readmission:
            changed_fields = (*changed_fields, "trust_state")
        delta = ObjectDelta(
            object_id=spec.object_id,
            base_version=spec.base_version,
            proposed_version=spec.proposed_version,
            base_value=base_value,
            proposed_value=proposed_value,
            changed_fields=changed_fields,
            semantic_classification=semantic,
            admitted_by=(spec.owner_id if readmission or semantic == SemanticClassification.SEMANTIC_DELTA else None),
        )
        return ChangeSetRevision(
            id=spec.id,
            revision=spec.revision,
            state=("READMISSION_ADMITTED" if readmission else
                   "READY_FOR_PREVIEW" if semantic == SemanticClassification.SEMANTIC_DELTA else "NO_SEMANTIC_DELTA"),
            owner_id=spec.owner_id,
            purpose=spec.purpose,
            scope=tuple(fixture.impact_targets),
            deltas=(delta,),
        )


class WorkspaceService:
    """Stateful façade whose canonical state is entirely in :class:`StateStore`."""

    def __init__(
        self,
        *,
        store_path: str | Path = ":memory:",
        profile: EnterpriseSeedProfile | None = None,
        enterprise_binding: EnterpriseBinding | None = None,
        owner_change_policy: str = "disabled",
        workflow_run_id: str | None = None,
        runtime_configuration: Any | None = None,
        review_duration_seconds: float = 0,
        wall_clock: Callable[[], float] | None = None,
        clock: Clock | None = None,
        store_tenant_id: str | None = None,
        workspace_id: str = "default",
        store_migrate: bool = True,
        private_retention_seconds: int = 86_400,
        approval_identity_mode: str = BODY_ACTOR_COMPATIBILITY,
        task_intake_required: bool = False,
        competition_mode: str = COMPETITION_OFF_MODE,
        competition_evidence_root: str | Path | None = None,
        competition_pack_path: str | Path | None = None,
        competition_checkout: str | Path | None = None,
        competition_lock_path: str | Path | None = None,
        competition_model_provider: str = "ollama-local",
        competition_ollama_endpoint: str | None = None,
        competition_vertex_project: str | None = None,
        competition_runner: Callable[..., dict[str, Any]] | None = None,
        advisory_provider: ModelProvider[ModelRequestV2, ModelResponseReceiptV2] | None = None,
        advisory_model_id: str | None = None,
    ) -> None:
        if workflow_run_id is not None and not workflow_run_id.strip():
            raise ValueError("WORKSPACE_WORKFLOW_RUN_ID_EMPTY")
        try:
            selected_review_duration = float(review_duration_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("WORKSPACE_REVIEW_DURATION_INVALID") from exc
        if not math.isfinite(selected_review_duration) or selected_review_duration < 0:
            raise ValueError("WORKSPACE_REVIEW_DURATION_INVALID")
        selected_identity_mode = str(approval_identity_mode).strip()
        if selected_identity_mode not in _SUPPORTED_APPROVAL_IDENTITY_MODES:
            raise ValueError("WORKSPACE_APPROVAL_IDENTITY_MODE_INVALID")
        if not isinstance(task_intake_required, bool):
            raise ValueError("WORKSPACE_TASK_INTAKE_REQUIRED_INVALID")
        selected_competition_mode = str(competition_mode).strip().lower()
        if selected_competition_mode not in _SUPPORTED_COMPETITION_MODES:
            raise ValueError("WORKSPACE_COMPETITION_MODE_INVALID")
        selected_model_provider = str(competition_model_provider).strip().lower()
        if selected_model_provider not in _SUPPORTED_COMPETITION_MODEL_PROVIDERS:
            raise ValueError("WORKSPACE_COMPETITION_MODEL_PROVIDER_INVALID")
        self.review_duration_ms = math.ceil(selected_review_duration * 1000)
        self._wall_clock = wall_clock or time.time
        native_advisory = (advisory_provider is not None
                           or os.environ.get("ORGREBASE_CHANGE_MODEL_PROVIDER") == "openai-responses")
        self.clock = clock or (SystemClock() if native_advisory else FrozenClock("2026-08-15T00:00:00Z"))
        self._execution_clock = self.clock if native_advisory else clock
        self.store_tenant_id = store_tenant_id
        self.workspace_id = workspace_id
        self.store_migrate = store_migrate
        self.private_retention_seconds = private_retention_seconds
        self.approval_identity_mode = selected_identity_mode
        self.task_intake_required = task_intake_required
        self.competition_mode = selected_competition_mode
        self.competition_evidence_root = (
            Path(competition_evidence_root).expanduser().resolve()
            if competition_evidence_root is not None
            else None
        )
        self.competition_pack_path = (
            Path(competition_pack_path).expanduser().resolve() if competition_pack_path is not None else None
        )
        self.competition_checkout = (
            Path(competition_checkout).expanduser().resolve() if competition_checkout is not None else None
        )
        self.competition_lock_path = (
            Path(competition_lock_path).expanduser().resolve() if competition_lock_path is not None else None
        )
        self.competition_model_provider = selected_model_provider
        self.competition_ollama_endpoint = competition_ollama_endpoint
        self.competition_vertex_project = (
            str(competition_vertex_project).strip()
            if competition_vertex_project is not None
            and str(competition_vertex_project).strip()
            else None
        )
        self._competition_runner = competition_runner
        self._advisory_provider = advisory_provider
        self._advisory_model_id = advisory_model_id
        # ``None`` preserves every historical Workspace digest.  An explicit
        # value is the integration seam used by a parent OAC run so Formation,
        # Tool, Preview, Approval, Apply, and successor traces share one run id.
        self._declared_workflow_correlation_id = workflow_run_id
        self.workflow_run_id = workflow_run_id
        self.effective_workflow_run_id = workflow_run_id or WORKSPACE_RUN_ID
        # Golden Competition executions receive a unique run ID while retaining
        # this stable Pack-derived ID as their business correlation identity.
        self.workflow_correlation_id = self.effective_workflow_run_id
        self.workflow_run_nonce = sha256_digest(
            {
                "run_id": self.effective_workflow_run_id,
                "mode": "LOCAL_DETERMINISTIC",
            }
        ).split(":", 1)[1]
        if profile is not None and runtime_configuration is not None:
            raise ValueError("WORKSPACE_PROFILE_AND_RUNTIME_CONFIGURATION_CONFLICT")
        if owner_change_policy not in {"disabled", "mutual-consent-v1"}:
            raise ValueError("OWNER_CHANGE_POLICY_INVALID")
        self.owner_change_policy = owner_change_policy
        self._declared_enterprise_binding = enterprise_binding
        self.runtime_configuration = runtime_configuration
        configured_profile = runtime_configuration.profile if runtime_configuration is not None else profile
        self.profile = parse_enterprise_seed_profile(configured_profile or northstar_acme_quote_profile())
        self.source_admission: EnterpriseSeedSourceAdmissionReceipt = (
            parse_enterprise_seed_source_admission_receipt(runtime_configuration.source_admission)
            if runtime_configuration is not None
            else admit_enterprise_seed_sources(self.profile)
        )
        self.profile_admission: EnterpriseSeedAdmissionReceipt = require_reference_runtime_compatible(
            self.profile,
            source_admission=self.source_admission,
        )
        self.runtime_projection: EnterpriseSeedRuntimeProjectionReceipt = (
            parse_enterprise_seed_runtime_projection_receipt(runtime_configuration.runtime_projection)
            if runtime_configuration is not None
            else verify_reference_runtime_projections(
                self.profile,
                self.source_admission,
            )
        )
        self.profile_digest = self.profile.digest
        self.scenario = dict(getattr(runtime_configuration, "scenario", self.profile.scenario_view()))
        self.boundaries = dict(WORKSPACE_BOUNDARIES)
        self.boundaries["data_profile"] = self.profile.data_class.value
        if runtime_configuration is not None:
            self.boundaries.update(dict(runtime_configuration.boundaries))
        self.quote_object_id = getattr(runtime_configuration, "quote_object_id", "work:quote_acme")
        self.quote_label = getattr(runtime_configuration, "quote_label", "Acme Enterprise Quote")
        self.graph_pointer_id = getattr(runtime_configuration, "graph_pointer_id", "graph:workspace")
        self.graph_snapshot_id = getattr(
            runtime_configuration, "graph_snapshot_id", "workspace-graph:northstar"
        )
        self.task_receipt_id = getattr(runtime_configuration, "task_receipt_id", "task-receipt:quote_acme@v1")
        self.default_run_id = getattr(runtime_configuration, "default_run_id", "run:workspace:quote-acme@v1")
        self.snapshot_target_ids = tuple(
            getattr(runtime_configuration, "snapshot_target_ids", ())
            or (
                "work:quote_acme",
                "work:finance_analysis_d",
                "work:partner_brief_e",
                "skill:enterprise-launch-readiness",
            )
        )
        self.snapshot_scope_roots = tuple(
            getattr(runtime_configuration, "snapshot_scope_roots", ())
            or ("claim:product.launch_date", "policy:finance.currency")
        )
        self.context_profiles = dict(
            getattr(runtime_configuration, "context_profiles", {})
            or {
                "workspace-quote-agent": {
                    "target": self.quote_object_id,
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
            }
        )
        self.deployment_binding = (
            {
                "schema_version": "orgrebase.enterprise-quote-pilot-store-binding.v1",
                "pack_id": runtime_configuration.pack_id,
                "pack_revision": runtime_configuration.pack_revision,
                "pack_digest": runtime_configuration.pack_digest,
                "adapter_id": runtime_configuration.adapter_id,
                "profile_ref": runtime_configuration.profile.ref,
                "profile_digest": runtime_configuration.profile.digest,
                "ordered_source_root_digests": [
                    item.observed_digest for item in runtime_configuration.source_admission.root_observations
                ],
                "universe_ref": runtime_configuration.universe.id,
                "universe_digest": runtime_configuration.universe.digest,
                "quote_object_id": runtime_configuration.quote_object_id,
                "graph_snapshot_id": runtime_configuration.graph_snapshot_id,
            }
            if runtime_configuration is not None
            else {}
        )
        self.store_path = str(store_path)
        self.store = StateStore(store_path, tenant_id=store_tenant_id,
                                workspace_id=workspace_id, migrate=store_migrate)
        self._command_lock = RLock()
        try:
            self._configure_runtime()
            self._restore_competition_execution_binding()
        except BaseException:
            self.store.close()
            raise

    def _configure_runtime(self) -> None:
        """Bind stateless services to the current store handle."""

        # Reject a different valid enterprise pack against an existing database
        # before the new runtime is allowed to seed even immutable objects.
        self._verify_persisted_profile_binding()
        self.store.bind_workspace(profile_digest=self.profile_digest,
                                  pack_digest=self.deployment_binding.get("pack_digest"),
                                  quote_object_id=self.quote_object_id)
        self.private_records = PrivateRecordStore(self.store, self.clock, retention_seconds=self.private_retention_seconds)
        self.private_records.migrate_artifact(TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID, TASK_INTAKE_WORK_DESCRIPTION_MEDIA_TYPE)
        runtime = self.runtime_configuration
        domain_registry = LocalDomainCandidateRegistry(runtime.source_values) if runtime is not None else None
        self.formation = WorkspaceFormationService(
            self.store,
            domain_registry=domain_registry,
            read_dependency_validator=lambda premises, now: validate_read_dependencies(self, premises, now),
            profile=self.profile,
            source_admission=self.source_admission,
            runtime_projection=self.runtime_projection,
            workflow_run_id=self.workflow_run_id,
            seed_objects=(runtime.seed_objects if runtime is not None else None),
            universe=(runtime.universe if runtime is not None else None),
            snapshot_target_ids=self.snapshot_target_ids,
            snapshot_scope_roots=self.snapshot_scope_roots,
            quote_object_id=self.quote_object_id,
            quote_label=self.quote_label,
            graph_pointer_id=self.graph_pointer_id,
            graph_snapshot_id=self.graph_snapshot_id,
            task_receipt_id=self.task_receipt_id,
            default_run_id=self.default_run_id,
            deployment_binding=self.deployment_binding,
        )
        self.formation.clock = self.clock
        if self.formation.profile_digest != self.profile_digest:
            raise RuntimeError("WORKSPACE_PROFILE_BINDING_MISMATCH")
        self.snapshot_builder = WorkspaceSnapshotBuilder()
        self.change_builder = WorkspaceChangeSetBuilder()
        provider = self._advisory_provider
        model_id = self._advisory_model_id
        mode = os.environ.get("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic")
        if mode not in {"local-deterministic", "openai-responses"}:
            raise ValueError("WORKSPACE_CHANGE_MODEL_PROVIDER_INVALID")
        if provider is None and mode == "openai-responses":
            from orgrebase.workspace.model_budget import ModelBudget
            from orgrebase.workspace.openai_responses import OpenAIResponsesProvider
            model_id = model_id or os.environ.get("ORGREBASE_CHANGE_MODEL_ID")
            if not model_id:
                raise ValueError("WORKSPACE_CHANGE_MODEL_ID_REQUIRED")
            budget_path = os.environ.get("ORGREBASE_CHANGE_MODEL_BUDGET_PATH")
            model_budget = ModelBudget.from_file(budget_path) if budget_path else None
            provider = OpenAIResponsesProvider(model_budget=model_budget)
        if provider is None and model_id is not None:
            raise ValueError("WORKSPACE_CHANGE_MODEL_PROVIDER_REQUIRED")
        from orgrebase.workspace.change_agentteams import ChangeAgentTeamsConfig
        native_required = self.competition_mode == GOLDEN_COMPETITION_MODE
        native_config = None
        if native_required and all((self.competition_checkout, self.competition_lock_path,
                                    self.competition_evidence_root)):
            native_config = ChangeAgentTeamsConfig(
                checkout=self.competition_checkout,
                lock_path=self.competition_lock_path,
                evidence_root=self.competition_evidence_root / "change-taskflows",
            )
        self.advisory_factory = WorkspaceChangeAdvisoryAdapter(
            quote_object_id=self.quote_object_id, provider=provider, model_id=model_id,
            tenant_id=self.profile.organization_id, workspace_id=self.store.workspace_id,
            native_config=native_config, native_required=native_required,
        )
        self.advisory_verifier = WorkspaceApplyAdvisoryVerifier(self.advisory_factory, clock=lambda: self.clock.now())
        if provider is not None:
            self.boundaries["change_candidate_provider"] = "OPENAI_RESPONSES_CONFIGURED"
        self.experience = GovernedExperienceService(
            self.store,
            wall_clock=self._wall_clock,
        )
        self.legacy = runtime.support_fixture if runtime is not None else load_fixture()
        self.last_preview_bundle: WorkspacePreviewBundle | None = None
        self.last_rebase_receipt: object | None = None
        self.last_workspace_receipt: WorkspaceRebaseReceipt | None = None
        self.domain_pack = DomainPack.enterprise_quote(self.profile.default_task.template_ref)
        owner_by_domain = {
            self.store.get_object(item.object_id, item.base_version).domain: item.owner_id
            for item in self.profile.change_family
        }
        resources = (
            ("launch_date", "claim:product.launch_date", "product"),
            ("currency", "policy:finance.currency", "finance"),
            ("product_plan", "claim:product.enterprise_plan", "product"),
        )
        declared_binding = self._declared_enterprise_binding or (runtime.enterprise_binding if runtime is not None else None)
        self._seed_enterprise_binding = declared_binding or EnterpriseBinding(
            organization_id=self.profile.organization_id,
            quote_object_id=self.quote_object_id,
            domain_pack_digest=self.domain_pack.digest,
            resources=tuple(
                EnterpriseResourceBinding(slot_id=slot, object_id=object_id,
                    domain_id=domain, owner_id=owner_by_domain[domain])
                for slot, object_id, domain in resources if domain in owner_by_domain
            ),
        )
        if (self._seed_enterprise_binding.organization_id != self.profile.organization_id
                or self._seed_enterprise_binding.quote_object_id != self.quote_object_id
                or self._seed_enterprise_binding.domain_pack_digest != self.domain_pack.digest
                or not self._seed_enterprise_binding.resources
                or any(item.owner_id not in self.profile.governance.owner_refs for item in self._seed_enterprise_binding.resources)):
            raise IntegrityError("ENTERPRISE_BINDING_INVALID")
        with self.store.transaction() as connection:
            self.store.save_artifact(connection, "enterprise-binding:workspace@r1",
                "application/vnd.orgrebase.enterprise-binding+json",
                self._seed_enterprise_binding.model_dump(mode="json"))
        from orgrebase.workspace.approval_authority import require_approval_actor
        self.changes = ChangeRegistry(self.store, self.domain_pack, self.enterprise_binding, workspace=self,
                                      verify_approval_actor=lambda event_id, actor_id: require_approval_actor(self, event_id, actor_id))
        if declared_binding is None:
            self._register_fixture_changes()

    @property
    def enterprise_binding(self) -> EnterpriseBinding:
        from orgrebase.workspace.enterprise_binding import current_binding
        return current_binding(self)

    def _register_fixture_changes(self) -> None:
        """Convert declared fixture inputs to the same admitted event contract."""
        objects = {item.ref: item for item in self.formation.seed_objects}
        for binding in self.profile.change_family:
            try:
                self.changes.get(binding.kind)
            except KeyError:
                pass
            else:
                continue
            base = objects[f"{binding.object_id}@{binding.base_version}"]
            proposal = objects[f"{binding.object_id}@{binding.proposed_version}"]
            self.register_change(ChangeEvent(
                event_id=binding.kind,
                organization_id=self.profile.organization_id,
                slot_id=binding.kind,
                owner_id=binding.owner_id,
                base_version=base.version,
                base_digest=base.digest,
                proposal=proposal,
                occurred_at=self.profile.default_task.requested_at,
            ))

    @property
    def change_order(self) -> tuple[str, ...]:
        return tuple(event.event_id for event in self.changes.all())

    @property
    def change_owner(self) -> Mapping[str, str]:
        return self.changes.owners

    @_serialized
    def register_change(self, event: ChangeEvent, *, connection: Any | None = None,
                        _source_observation_admission: bool = False) -> dict[str, Any]:
        admitted = self.changes.register(event, connection=connection,
            _source_observation_admission=_source_observation_admission)
        return {"event_id": admitted.event_id, "event_digest": admitted.digest,
                "event": admitted.model_dump(mode="json")}

    @_serialized
    def change_history(self, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
        from orgrebase.workspace.approval_authority import authority_detail, authority_records

        with self.store.read_snapshot():
            page = self.changes.page(after=after, limit=limit)
            observed_at = self.clock.now()
            event_ids = tuple(event.event_id for event in page["items"])
            records = self._change_records(event_ids)
            authorities = authority_records(self, event_ids)
            items = []
            for event in page["items"]:
                change = records[event.event_id]
                status = self._change_status(event.event_id, records=change, observed_at=observed_at)
                authority = authority_detail(self, event.event_id, include_eligible_delegates=False,
                                             records=authorities, observed_at=observed_at)
                decision_actor = None
                if status == "REJECTED":
                    rejection = self.changes.rejection(event.event_id)
                    decision_actor = rejection["actor_id"] if rejection else None
                elif change["approval"] is not None:
                    decision_actor = change["approval"]["approval"]["actor_id"]
                items.append({"event": event.model_dump(mode="json"), "status": status,
                              "responsibility": {
                                  "owner_id": authority["owner_id"],
                                  "delegate_id": authority["active_owner_id"] if authority["delegation_active"] else None,
                                  "blocked_reason": authority["blocked_reason"],
                                  "decision_actor_id": decision_actor,
                              }})
            return {**page, "items": items, "observed_at": observed_at}

    @_serialized
    def reject_change(self, event_id: str, *, actor_id: str, reason: str) -> dict[str, Any]:
        self._require_ungrouped(event_id)
        with self.store.transaction() as connection:
            if self._outcome_record(event_id) is not None:
                raise IntegrityError("CHANGE_ALREADY_APPLIED")
            preview_record = self._preview_record(event_id)
            if preview_record is not None:
                bundle = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
                receipt_id = (
                    f"{WORKSPACE_IDENTITY.receipt_prefix}:"
                    f"{bundle.change_set.id.split(':', 1)[-1]}@{bundle.change_set.revision}"
                )
                try:
                    committed = RebaseReceipt.model_validate(self.store.load_artifact(
                        receipt_id, "application/vnd.orgrebase.rebase-receipt+json"
                    ).payload)
                except KeyError:
                    committed = None
                if committed is not None:
                    if (
                        committed.change_set_ref != f"{bundle.change_set.id}@{bundle.change_set.revision}"
                        or committed.revision_lock.change_set_digest != bundle.change_set.digest
                    ):
                        raise IntegrityError("WORKSPACE_CORE_RECEIPT_BINDING_INVALID")
                    raise IntegrityError("CHANGE_ALREADY_APPLIED")
            receipt = self.changes.reject(event_id, actor_id, reason, connection=connection)
        return {"rejection": receipt, "state": self.state()}

    def _require_ungrouped(self, event_id: str) -> None:
        from orgrebase.workspace.source_readmission import membership
        if membership(self, event_id) is not None:
            raise IntegrityError("SOURCE_GROUP_COMMAND_REQUIRED")

    @_serialized
    def completion_history(self) -> dict[str, Any]:
        chain = self.store.verify_event_chain()
        chain["envelopes"] = list(self.store.event_envelopes())
        chain["records"] = [{key: value for key, value in event.items() if key != "payload"}
                            for event in chain["envelopes"]]
        from orgrebase.workspace.source_readmission import completion_groups, membership
        return {"source_readmission_groups": completion_groups(self), "changes": {event_id: {"preview": self._preview_record(event_id),
                                       "approval": self._approval_record(event_id),
                                       "outcome": self._outcome_record(event_id)}
                            for event_id in self.change_order},
                "change_events": [{"event_id": event.event_id, "event_digest": event.digest, "slot_id": event.slot_id,
                                   "owner_id": event.owner_id, "status": self._change_status(event.event_id),
                                   **({"source_group_id": membership(self, event.event_id)["group_id"]} if membership(self, event.event_id) else {})}
                                  for event in self.changes.all()],
                "event_chain": chain, "event_scopes": self._event_scopes(chain),
                "completion_observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}

    @_serialized
    def invalidate_source(self, slot_id: str, source_ref: str, reason: str, *, connection: Any | None = None) -> dict[str, Any]:
        return self.changes.invalidate(slot_id, source_ref, reason, connection=connection)

    def _change_status(self, event_id: str, *, records: Mapping[str, Any] | None = None,
                       observed_at: str | None = None) -> str:
        return self._change_status_with_reason(event_id, records=records, observed_at=observed_at)[0]

    def _change_status_with_reason(
        self, event_id: str, *, records: Mapping[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> tuple[str, str | None]:
        from orgrebase.workspace.source_readmission import member_status
        grouped = member_status(self, event_id)
        if grouped is not None:
            return grouped, None
        if self.changes.rejection(event_id) is not None:
            return "REJECTED", None
        if (self._outcome_record(event_id) if records is None else records["outcome"]) is not None:
            return "APPLIED", None
        event = self.changes.get(event_id)
        preview = self._preview_record(event_id) if records is None else records["preview"]
        approval = self._approval_record(event_id) if records is None else records["approval"]
        if preview is not None and approval is not None:
            bundle = WorkspacePreviewBundle.model_validate(preview["bundle"])
            if bundle.change_spec.id != f"changeset:workspace-{event_id}":
                raise IntegrityError("WORKSPACE_COMMITTED_RECEIPT_BINDING_INVALID")
            recovered = self._recover_apply_result(
                bundle=bundle,
                approval=Approval.model_validate(approval["approval"]),
                include_event_chain=False,
            )
            if recovered is not None:
                return "RECOVERY_REQUIRED", None
        from orgrebase.domain import FreshnessError
        from orgrebase.workspace.enterprise_binding import require_change_owner
        try:
            require_change_owner(self, event)
        except FreshnessError:
            return "EXPIRED", "CHANGE_OWNER_REPLAN_REQUIRED"
        observed_at = observed_at or self.clock.now()
        now = utc_datetime(observed_at)
        if utc_datetime(event.proposal.valid_from) > now:
            return "SCHEDULED", "PROPOSAL_NOT_YET_EFFECTIVE"
        if event.proposal.valid_to is not None and utc_datetime(event.proposal.valid_to) <= now:
            return "EXPIRED", "PROPOSAL_VALIDITY_EXPIRED"
        current = self.store.get_object(event.proposal.id)
        expected_states = {ObjectState.STALE, ObjectState.QUARANTINED} if event.operation == "READMIT" else {ObjectState.CURRENT, ObjectState.ACTIVE}
        if current.state not in expected_states:
            return "STALE", "CHANGE_BASE_STATE_CHANGED"
        if current.version != event.base_version or current.digest != event.base_digest:
            return "STALE", "CHANGE_BASE_VERSION_CHANGED"
        try:
            validate_source_observations(self, (event.proposal,), observed_at)
        except ReadDependencyError as error:
            return "STALE", error.code
        if preview is not None:
            bundle = WorkspacePreviewBundle.model_validate(preview["bundle"])
            runtime_status = preview_runtime_status(self, event_id, bundle.preview.digest)
            if runtime_status["decision"] != "CONTINUE":
                return "EXPIRED", runtime_status["reason"]
            snapshot = self.current_snapshot()
            if bundle.snapshot_ref != snapshot.ref or bundle.snapshot_digest != snapshot.digest:
                return "STALE", "CHANGE_SNAPSHOT_CHANGED"
            if utc_datetime(bundle.run_envelope.expires_at) <= now:
                return "EXPIRED", "PREVIEW_VALIDITY_EXPIRED"
        if approval is not None:
            if utc_datetime(approval["approval"]["expires_at"]) <= now:
                return "EXPIRED", "APPROVAL_VALIDITY_EXPIRED"
            from orgrebase.workspace.approval_authority import verify_approval_authority
            try:
                verify_approval_authority(self, event_id, Approval.model_validate(approval["approval"]), current=True, observed_at=observed_at)
            except (AuthenticationError, AuthorizationError, FreshnessError):
                return "EXPIRED", "APPROVAL_AUTHORITY_CHANGED"
            return "APPROVED", None
        if preview is None:
            from orgrebase.workspace.change_recovery import latest_request, resume_record
            request = latest_request(self, event_id)
            if request is not None and resume_record(self, event_id, request) is None:
                return "EVIDENCE_REQUIRED", "CHANGE_RECOVERY_EVIDENCE_REQUIRED"
        return ("PREVIEWED" if preview is not None else "RECEIVED"), None

    def _active_event_id(self) -> str | None:
        snapshot = None
        with suppress(KeyError):
            snapshot = self.current_snapshot()
        return next((event_id for event_id in self.changes.eligible_ids(
            now=self.clock.now(), snapshot_ref=snapshot.ref if snapshot else None,
            snapshot_digest=snapshot.digest if snapshot else None)
            if self._change_status(event_id) in {"RECEIVED", "PREVIEWED", "APPROVED", "RECOVERY_REQUIRED"}), None)

    def _business_complete(self) -> bool:
        try:
            quote = self.current_quote()
        except KeyError:
            return False
        return (getattr(quote, "state", None) == ObjectState.CURRENT
                and not self.changes.gaps() and self.changes.pending_count == 0)

    def _profile_binding_record(self) -> dict[str, Any] | None:
        try:
            stored = self.store.load_artifact(
                PROFILE_BINDING_ARTIFACT_ID,
                PROFILE_BINDING_MEDIA_TYPE,
            )
        except KeyError:
            return None
        return {
            "artifact_id": PROFILE_BINDING_ARTIFACT_ID,
            "artifact_digest": stored.payload_digest,
            "binding": stored.payload,
        }

    def _oac_activation_consumption_record(self) -> dict[str, Any] | None:
        try:
            stored = self.store.load_artifact(
                OAC_ACTIVATION_CONSUMPTION_ARTIFACT_ID,
                OAC_ACTIVATION_CONSUMPTION_MEDIA_TYPE,
            )
        except KeyError:
            return None
        try:
            receipt = OACActivationConsumptionReceipt.model_validate(stored.payload)
        except (TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_CONSUMPTION_INVALID") from exc
        if receipt.execution_run_id != self.effective_workflow_run_id:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_CONSUMPTION_RUN_MISMATCH")
        if receipt.profile_digest != self.profile_digest:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_CONSUMPTION_PROFILE_MISMATCH")
        runtime = self.runtime_configuration
        if runtime is None or receipt.pack_digest != runtime.pack_digest:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_CONSUMPTION_PACK_MISMATCH")
        return {
            "artifact_id": OAC_ACTIVATION_CONSUMPTION_ARTIFACT_ID,
            "artifact_payload_digest": stored.payload_digest,
            "receipt": receipt.model_dump(mode="json"),
        }

    def _task_intake_run_record(self) -> dict[str, Any] | None:
        """Load and independently bind the durable employee-intake receipt."""

        from orgrebase.workspace.task_intake import (
            TaskIntakeRunSummary,
            verify_task_intake_approval,
            verify_task_intake_candidate,
        )

        try:
            stored = self.store.load_artifact(
                TASK_INTAKE_RUN_ARTIFACT_ID,
                TASK_INTAKE_RUN_MEDIA_TYPE,
            )
        except KeyError:
            return None
        try:
            receipt = TaskIntakeRunSummary.model_validate(stored.payload)
        except (TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_RUN_RECEIPT_INVALID") from exc
        formation = self._formation_record()
        if formation is None:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_FORMATION_MISSING")
        if (
            receipt.run_id != self.effective_workflow_run_id
            or receipt.task_digest
            != self.profile.task_request().revalidated().digest
            or receipt.formation_receipt_digest != formation.digest
            or receipt.quote_ref != formation.deliverable_ref
        ):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_FORMATION_BINDING_MISMATCH")
        oac_record = self._oac_activation_consumption_record()
        expected_oac_digest = (
            OACActivationConsumptionReceipt.model_validate(
                oac_record["receipt"]
            ).activation_binding_digest
            if oac_record is not None
            else None
        )
        if receipt.oac_activation_binding_digest != expected_oac_digest:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_OAC_BINDING_MISMATCH")
        template = self.formation.registry.get(receipt.candidate_receipt.template_ref)
        workspace_instance_nonce = self.task_intake_workspace_instance_nonce(create=False)
        candidate = verify_task_intake_candidate(
            receipt.candidate_receipt,
            profile=self.profile,
            template=template,
            expected_run_id=self.effective_workflow_run_id,
            expected_workspace_instance_nonce=workspace_instance_nonce,
            expected_oac_activation_binding_digest=expected_oac_digest,
            oac_required=expected_oac_digest is not None,
        )
        confirmation = verify_task_intake_approval(
            receipt.confirmation_receipt,
            candidate=candidate,
            actor_id=self.profile.default_task.actor_id,
            profile=self.profile,
            template=template,
            expected_run_id=self.effective_workflow_run_id,
            expected_workspace_instance_nonce=workspace_instance_nonce,
            expected_oac_activation_binding_digest=expected_oac_digest,
            oac_required=expected_oac_digest is not None,
        )
        if (
            receipt.workspace_instance_nonce != workspace_instance_nonce
            or receipt.candidate_digest != candidate.digest
            or receipt.approval_digest != confirmation.digest
        ):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_RECEIPT_REPLAY_BINDING_MISMATCH")
        matching_events = self.changes.journal(
            TASK_INTAKE_BOUND_EVENT_TYPE, subject_key=receipt.run_id, limit=2
        )
        if len(matching_events) != 1:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_EVENT_BINDING_MISSING")
        event = matching_events[0]
        if (
            event["payload"].get("task_intake_receipt_digest") != receipt.digest
            or event["payload"].get("artifact_id") != TASK_INTAKE_RUN_ARTIFACT_ID
            or event["payload"].get("artifact_payload_digest")
            != stored.payload_digest
            or event["payload"].get("workspace_instance_nonce")
            != receipt.workspace_instance_nonce
            or event["payload"].get("prompt_digest") != receipt.prompt_digest
            or event["payload"].get("formation_receipt_digest")
            != receipt.formation_receipt_digest
            or event["payload"].get("candidate_digest")
            != receipt.candidate_digest
            or event["payload"].get("approval_digest") != receipt.approval_digest
            or event["payload"].get("task_digest") != receipt.task_digest
            or event["payload"].get("oac_activation_binding_digest")
            != receipt.oac_activation_binding_digest
            or event["payload"].get("canonical_target_writes") != 0
        ):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_EVENT_BINDING_INVALID")
        return {
            **receipt.model_dump(mode="json"),
            "artifact_id": TASK_INTAKE_RUN_ARTIFACT_ID,
            "artifact_payload_digest": stored.payload_digest,
            "event_digest": event["event_digest"],
        }

    def _build_task_intake_run_summary(
        self,
        *,
        selected: TaskRequest,
        formation: TaskReceipt,
        candidate_payload: Mapping[str, Any],
        approval_payload: Mapping[str, Any],
        activation_consumption: OACActivationConsumptionReceipt | None,
    ) -> Any:
        """Revalidate the exact candidate/approval pair at the state boundary."""

        from orgrebase.workspace.task_intake import (
            TaskIntakeRunSummary,
            verify_task_intake_approval,
            verify_task_intake_candidate,
        )

        try:
            template = self.formation.registry.get(selected.template_ref).revalidated()
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_BINDING_INVALID") from exc
        expected_oac_digest = (
            activation_consumption.activation_binding_digest
            if activation_consumption is not None
            else None
        )
        workspace_instance_nonce = self.task_intake_workspace_instance_nonce(create=False)
        candidate = verify_task_intake_candidate(
            candidate_payload,
            profile=self.profile,
            template=template,
            expected_run_id=self.effective_workflow_run_id,
            expected_workspace_instance_nonce=workspace_instance_nonce,
            expected_oac_activation_binding_digest=expected_oac_digest,
            oac_required=expected_oac_digest is not None,
        )
        approval = verify_task_intake_approval(
            approval_payload,
            candidate=candidate,
            actor_id=selected.actor_id,
            profile=self.profile,
            template=template,
            expected_run_id=self.effective_workflow_run_id,
            expected_workspace_instance_nonce=workspace_instance_nonce,
            expected_oac_activation_binding_digest=expected_oac_digest,
            oac_required=expected_oac_digest is not None,
        )
        if candidate.task_request.model_dump(mode="json") != selected.model_dump(
            mode="json"
        ):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_BINDING_MISMATCH")
        return TaskIntakeRunSummary(
            run_id=self.effective_workflow_run_id,
            workspace_instance_nonce=workspace_instance_nonce,
            actor_id=candidate.actor_id,
            prompt_digest=candidate.prompt_digest,
            prompt_length=candidate.prompt_length,
            candidate_digest=candidate.digest,
            approval_digest=approval.digest,
            task_digest=candidate.task_digest,
            formation_receipt_digest=formation.digest,
            quote_ref=formation.deliverable_ref,
            oac_activation_binding_digest=candidate.oac_activation_binding_digest,
            candidate_receipt=candidate,
            confirmation_receipt=approval,
        )

    def _build_task_intake_work_description_record(
        self,
        *,
        work_description: str,
        run_summary: Any,
    ) -> Any:
        """Build the private source-text binding for the same Formation."""

        from orgrebase.workspace.task_intake import (
            TaskIntakeRunSummary,
            TaskIntakeWorkDescriptionRecord,
            validate_task_intake_work_description,
        )

        summary = TaskIntakeRunSummary.model_validate(
            run_summary.model_dump(mode="json")
            if isinstance(run_summary, TaskIntakeRunSummary)
            else run_summary
        )
        selected = validate_task_intake_work_description(work_description)
        if (
            sha256_digest(selected) != summary.prompt_digest
            or len(selected) != summary.prompt_length
        ):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_MISMATCH")
        return TaskIntakeWorkDescriptionRecord(
            run_id=summary.run_id,
            workspace_instance_nonce=summary.workspace_instance_nonce,
            actor_id=summary.actor_id,
            work_description=selected,
            prompt_digest=summary.prompt_digest,
            prompt_length=summary.prompt_length,
            prompt_utf8_bytes=len(selected.encode("utf-8")),
            candidate_digest=summary.candidate_digest,
            approval_digest=summary.approval_digest,
            formation_receipt_digest=summary.formation_receipt_digest,
            quote_ref=summary.quote_ref,
        )

    def _task_intake_work_description_record(self) -> dict[str, Any] | None:
        """Load the private source text without projecting it into public state."""

        from orgrebase.workspace.task_intake import TaskIntakeWorkDescriptionRecord

        payload = self.private_records.read(TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID)
        if payload is None:
            return None
        try:
            record = TaskIntakeWorkDescriptionRecord.model_validate(payload)
        except (TypeError, ValueError) as exc:
            raise IntegrityError(
                "WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_RECORD_INVALID"
            ) from exc
        run_record = self._task_intake_run_record()
        if run_record is None:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_RUN_MISSING")
        if (
            record.run_id != run_record["run_id"]
            or record.workspace_instance_nonce
            != run_record["workspace_instance_nonce"]
            or record.actor_id != run_record["actor_id"]
            or record.prompt_digest != run_record["prompt_digest"]
            or record.prompt_length != run_record["prompt_length"]
            or record.candidate_digest != run_record["candidate_digest"]
            or record.approval_digest != run_record["approval_digest"]
            or record.formation_receipt_digest
            != run_record["formation_receipt_digest"]
            or record.quote_ref != run_record["quote_ref"]
        ):
            raise IntegrityError(
                "WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_RUN_BINDING_MISMATCH"
            )
        return record.model_dump(mode="json")

    def task_intake_work_description(self, *, actor_id: str) -> dict[str, Any] | None:
        """Return private source text only to the exact admitted task actor."""

        expected_actor = self.profile.default_task.actor_id
        if actor_id != expected_actor:
            raise AuthorizationError(
                f"TASK_INTAKE_ACTOR_DENIED:expected={expected_actor},actual={actor_id}"
            )
        record = self._task_intake_work_description_record()
        if record is not None and record["actor_id"] != actor_id:
            raise AuthorizationError("TASK_INTAKE_WORK_DESCRIPTION_ACTOR_DENIED")
        return record

    def task_intake_workspace_instance_nonce(self, *, create: bool = True) -> str:
        """Return one durable, non-secret binding unique to this StateStore."""

        with self._command_lock:
            record = self.store.get_idempotent(
                TASK_INTAKE_WORKSPACE_INSTANCE_KEY,
                TASK_INTAKE_WORKSPACE_INSTANCE_REQUEST_DIGEST,
            )
            if record is None:
                if not create:
                    raise IntegrityError("WORKSPACE_TASK_INTAKE_INSTANCE_BINDING_MISSING")
                with self.store.transaction() as connection:
                    record = self.store.get_idempotent(
                        TASK_INTAKE_WORKSPACE_INSTANCE_KEY,
                        TASK_INTAKE_WORKSPACE_INSTANCE_REQUEST_DIGEST,
                        connection=connection,
                    )
                    if record is None:
                        body = {
                            "schema_version": "orgrebase.workspace-task-intake-instance.v1",
                            "nonce": sha256_digest({"workspace_instance_uuid": str(uuid4())}),
                        }
                        record = {**body, "digest": sha256_digest(body)}
                        self.store.save_idempotent(
                            connection, TASK_INTAKE_WORKSPACE_INSTANCE_KEY,
                            TASK_INTAKE_WORKSPACE_INSTANCE_REQUEST_DIGEST, record,
                        )
            nonce = record.get("nonce")
            body = {key: value for key, value in record.items() if key != "digest"}
            if (
                record.get("schema_version")
                != "orgrebase.workspace-task-intake-instance.v1"
                or not isinstance(nonce, str)
                or len(nonce) != 71
                or not nonce.startswith("sha256:")
                or any(character not in "0123456789abcdef" for character in nonce[7:])
                or record.get("digest") != sha256_digest(body)
            ):
                raise IntegrityError("WORKSPACE_TASK_INTAKE_INSTANCE_BINDING_INVALID")
            return nonce

    def oac_activation_state(self) -> dict[str, Any]:
        """Project persisted OAC use without inferring it from deployment mode."""

        record = self._oac_activation_consumption_record()
        if record is None:
            return {
                "schema_version": "orgrebase.workspace-oac-activation-view.v1",
                "status": "NOT_USED_IN_THIS_RUN",
                "execution_run_id": self.effective_workflow_run_id,
                "adaptation_run_id": None,
                "activation_binding_digest": None,
                "adapter_capsule_digest": None,
                "consumption_receipt_digest": None,
                "consumed_at": None,
                "consumption_phase": None,
                "binding_effect": None,
                "canonical_target_writes": 0,
                "claim_ceiling": (
                    "NO_PERSISTED_OAC_ACTIVATION_CONSUMPTION_FOR_THIS_RUN"
                ),
            }
        receipt = OACActivationConsumptionReceipt.model_validate(record["receipt"])
        return {
            "schema_version": "orgrebase.workspace-oac-activation-view.v1",
            "status": receipt.status,
            "execution_run_id": receipt.execution_run_id,
            "adaptation_run_id": receipt.adaptation_run_id,
            "activation_binding_digest": receipt.activation_binding_digest,
            "adapter_capsule_digest": receipt.adapter_capsule_digest,
            "consumption_receipt_digest": receipt.digest,
            "artifact_id": record["artifact_id"],
            "artifact_payload_digest": record["artifact_payload_digest"],
            "profile_digest": receipt.profile_digest,
            "pack_digest": receipt.pack_digest,
            "task_request_digest": receipt.task_request_digest,
            "formation_receipt_digest": receipt.formation_receipt_digest,
            "quote_ref": receipt.quote_ref,
            "consumed_at": receipt.consumed_at,
            "consumption_phase": receipt.consumption_phase,
            "binding_effect": receipt.binding_effect,
            "canonical_write_authority": receipt.canonical_write_authority,
            "canonical_target_writes": receipt.canonical_target_writes,
            "claim_ceiling": receipt.claim_ceiling,
        }

    def _build_oac_activation_consumption_receipt(
        self,
        *,
        binding_payload: Mapping[str, Any],
        selected: TaskRequest,
        formation: TaskReceipt,
    ) -> OACActivationConsumptionReceipt:
        # Keep the portable OAC model out of the core import graph while still
        # revalidating every field at this command boundary.
        from orgrebase.workspace.oac_quote_adaptation import (
            OACAdapterActivationBinding,
        )

        try:
            binding = OACAdapterActivationBinding.model_validate(dict(binding_payload))
        except (TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_BINDING_INVALID") from exc
        runtime = self.runtime_configuration
        if (
            runtime is None
            or binding.profile_digest != self.profile_digest
            or binding.pack_digest != runtime.pack_digest
            or binding.execution_run_id != self.effective_workflow_run_id
        ):
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_BINDING_MISMATCH")
        return OACActivationConsumptionReceipt(
            adaptation_run_id=binding.adaptation_run_id,
            activation_binding_digest=binding.digest,
            adapter_capsule_digest=binding.adapter_capsule_digest,
            profile_digest=binding.profile_digest,
            pack_digest=binding.pack_digest,
            execution_run_id=binding.execution_run_id,
            task_request_digest=selected.digest,
            formation_receipt_digest=formation.digest,
            quote_ref=formation.deliverable_ref,
            consumed_at=formation.committed_at,
        )

    @staticmethod
    def _require_same_oac_activation_consumption(
        persisted: Mapping[str, Any],
        expected: OACActivationConsumptionReceipt,
    ) -> None:
        try:
            actual = OACActivationConsumptionReceipt.model_validate(
                persisted["receipt"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_CONSUMPTION_INVALID") from exc
        if actual.digest != expected.digest:
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_CONSUMPTION_CONFLICT")

    def _verify_persisted_profile_binding(self) -> None:
        binding = self._profile_binding_record()
        formation = self._formation_record()
        if binding is None and formation is None:
            return
        if binding is None:
            raise RuntimeError("WORKSPACE_PERSISTED_PROFILE_BINDING_MISSING")
        if formation is None:
            raise RuntimeError("WORKSPACE_PROFILE_BINDING_WITHOUT_FORMATION")
        payload = binding["binding"]
        profile = payload.get("profile", {})
        admission = payload.get("admission_receipt", {})
        persisted_admission = parse_enterprise_seed_admission_receipt(admission)
        persisted_source = parse_enterprise_seed_source_admission_receipt(
            payload.get("source_admission_receipt", {})
        )
        persisted_runtime = parse_enterprise_seed_runtime_projection_receipt(
            payload.get("runtime_projection_receipt", {})
        )
        if (
            profile.get("profile_ref") != self.profile.ref
            or profile.get("digest") != self.profile_digest
            or persisted_admission.profile_digest != self.profile_digest
            or persisted_admission.reference_runtime_compatible is not True
            or persisted_admission.source_admission_receipt_digest != self.source_admission.digest
            or persisted_source.digest != self.source_admission.digest
            or persisted_source.profile_projection_digest != self.source_admission.profile_projection_digest
            or payload.get("source_admission_receipt_digest") != self.source_admission.digest
            or tuple(payload.get("admitted_source_root_digests", ()))
            != tuple(item.observed_digest for item in self.source_admission.root_observations)
            or tuple(payload.get("component_admission_digests", ()))
            != tuple(item.digest for item in self.source_admission.component_admissions)
            or payload.get("source_profile_projection_digest")
            != self.source_admission.profile_projection_digest
            or persisted_runtime.digest != self.runtime_projection.digest
            or persisted_runtime.runtime_projection_digest
            != self.runtime_projection.runtime_projection_digest
            or payload.get("runtime_projection_receipt_digest") != self.runtime_projection.digest
            or payload.get("runtime_projection_digest") != self.runtime_projection.runtime_projection_digest
            or payload.get("handler_profile") != self.profile.runtime_compatibility.handler_profile
            or payload.get("task_ref") != formation.task_ref
        ):
            raise RuntimeError("WORKSPACE_PERSISTED_PROFILE_BINDING_MISMATCH")
        if dict(payload.get("enterprise_pilot_pack") or {}) != self.deployment_binding:
            raise RuntimeError("PILOT_PACK_STORE_BINDING_MISMATCH")

    def close(self) -> None:
        self.store.close()

    @classmethod
    def reopen(
        cls,
        store_path: str | Path,
        *,
        profile: EnterpriseSeedProfile | None = None,
        enterprise_binding: EnterpriseBinding | None = None,
        workflow_run_id: str | None = None,
        runtime_configuration: Any | None = None,
        review_duration_seconds: float = 0,
        wall_clock: Callable[[], float] | None = None,
        clock: Clock | None = None,
        store_tenant_id: str | None = None,
        workspace_id: str = "default",
        store_migrate: bool = True,
        private_retention_seconds: int = 86_400,
        approval_identity_mode: str = BODY_ACTOR_COMPATIBILITY,
        task_intake_required: bool = False,
        competition_mode: str = COMPETITION_OFF_MODE,
        competition_evidence_root: str | Path | None = None,
        competition_pack_path: str | Path | None = None,
        competition_checkout: str | Path | None = None,
        competition_lock_path: str | Path | None = None,
        competition_model_provider: str = "ollama-local",
        competition_ollama_endpoint: str | None = None,
        competition_vertex_project: str | None = None,
        competition_runner: Callable[..., dict[str, Any]] | None = None,
        advisory_provider: ModelProvider[ModelRequestV2, ModelResponseReceiptV2] | None = None,
        advisory_model_id: str | None = None,
    ) -> WorkspaceService:
        return cls(
            store_path=store_path,
            profile=profile,
            enterprise_binding=enterprise_binding,
            workflow_run_id=workflow_run_id,
            runtime_configuration=runtime_configuration,
            review_duration_seconds=review_duration_seconds,
            wall_clock=wall_clock,
            clock=clock,
            store_tenant_id=store_tenant_id,
            workspace_id=workspace_id,
            store_migrate=store_migrate,
            private_retention_seconds=private_retention_seconds,
            approval_identity_mode=approval_identity_mode,
            task_intake_required=task_intake_required,
            competition_mode=competition_mode,
            competition_evidence_root=competition_evidence_root,
            competition_pack_path=competition_pack_path,
            competition_checkout=competition_checkout,
            competition_lock_path=competition_lock_path,
            competition_model_provider=competition_model_provider,
            competition_ollama_endpoint=competition_ollama_endpoint,
            competition_vertex_project=competition_vertex_project,
            competition_runner=competition_runner,
            advisory_provider=advisory_provider,
            advisory_model_id=advisory_model_id,
        )

    def _select_formation_request(
        self,
        request: TaskRequest | None,
    ) -> tuple[TaskRequest, TaskReceipt | None]:
        try:
            selected = (request or self.formation.default_request(self.profile)).revalidated()
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_TASK_REQUEST_MODEL_INVALID") from exc
        # Check the idempotency ledger before scenario validation.  This preserves
        # the stronger fail-closed signal when a caller reuses the frozen command
        # key with different bytes.
        existing = self.store.get_idempotent(selected.idempotency_key, selected.digest)
        if existing is not None:
            return selected, TaskReceipt.model_validate(existing)
        return self.formation.require_profile_bound_request(selected), None

    @_serialized
    def form_quote(self, request: TaskRequest | None = None) -> TaskReceipt:
        if self.task_intake_required:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_REQUIRED")
        selected, existing = self._select_formation_request(request)
        if existing is not None:
            authorization = current_authorization()
            if authorization is not None:
                authorization()
            return existing
        return self.formation.form_quote(
            selected,
            run_id=self.workflow_run_id,
        )

    @staticmethod
    def _prepared_formation_trace(prepared: PreparedFormationBundle) -> WorkTrace:
        trace_ref = prepared.task_receipt.trace_ref
        trace_write = next(
            (write for write in prepared.artifact_writes if write.artifact_id == trace_ref),
            None,
        )
        if trace_write is None:  # pragma: no cover - formation contract invariant
            raise RuntimeError("WORKSPACE_PREPARED_FORMATION_TRACE_MISSING")
        return WorkTrace.model_validate(trace_write.payload)

    @staticmethod
    def _load_golden_json(path: Path, error_code: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(error_code) from exc
        if not isinstance(value, dict):
            raise RuntimeError(error_code)
        return value

    @staticmethod
    def _load_golden_json_array(path: Path, error_code: str) -> list[dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(error_code) from exc
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise RuntimeError(error_code)
        return value

    @staticmethod
    def _require_golden_digest(value: Mapping[str, Any], error_code: str) -> str:
        body = dict(value)
        observed = body.pop("digest", None)
        if not isinstance(observed, str) or sha256_digest(body) != observed:
            raise RuntimeError(error_code)
        return observed

    @staticmethod
    def _golden_compact_record(value: Mapping[str, Any]) -> dict[str, Any]:
        body = json.loads(json.dumps(dict(value), ensure_ascii=False))
        return {**body, "digest": sha256_digest(body)}

    @staticmethod
    def _golden_review_decision(value: Any) -> dict[str, Any] | None:
        """Expose bounded review facts, never the model's free-form explanation."""
        if not isinstance(value, dict) or value.get("verdict") not in ("PASS", "REPLAN"):
            return None
        domains = value.get("missing_domains")
        if not isinstance(domains, list) or any(
            domain not in ("product", "legal", "finance", "gtm") for domain in domains
        ):
            return None
        return {"verdict": value["verdict"], "missing_domains": sorted(set(domains))}

    @classmethod
    def _verify_golden_skill_release_evidence(
        cls,
        *,
        package: Mapping[str, Any],
        evaluation: Mapping[str, Any],
        release_history: list[dict[str, Any]],
        invocation: Mapping[str, Any],
        result: Mapping[str, Any],
        summary: Mapping[str, Any],
        execution_run_id: str,
    ) -> dict[str, Any]:
        """Verify the exact package -> evaluation -> release -> invocation authority chain."""

        package_body = {key: value for key, value in package.items() if key != "manifest_digest"}
        package_digest = package.get("manifest_digest")
        release_artifact = package.get("release_artifact")
        package_evaluation = package.get("evaluation")
        permissions = package.get("permissions")
        dependencies = package.get("dependencies")
        if (
            package.get("schema_version") != "orgrebase.skill-package-manifest.v2"
            or package.get("name") != "enterprise-quote-compose"
            or package.get("entry_point") != "QUOTE_COMPOSE_V1"
            or not isinstance(package_digest, str)
            or sha256_digest(package_body) != package_digest
            or not isinstance(release_artifact, Mapping)
            or release_artifact.get("source_candidate_executable") is not False
            or not isinstance(package_evaluation, Mapping)
            or tuple(package_evaluation.get("partitions", ())) != PARTITIONS
            or package_evaluation.get("security_min_pass_rate") != 1.0
            or package_evaluation.get("target_writes_max") != 0
            or permissions
            != {
                "allowed_tools": [],
                "side_effects": [],
                "effect_ceiling": "CANDIDATE_ONLY",
            }
            or not isinstance(dependencies, Mapping)
            or not dependencies
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_SKILL_PACKAGE_INVALID")

        evaluation_digest = cls._require_golden_digest(
            evaluation,
            "WORKSPACE_GOLDEN_SKILL_EVALUATION_DIGEST_INVALID",
        )
        case_results = evaluation.get("case_results")
        gate_results = evaluation.get("gate_results")
        premise_lock = evaluation.get("premise_lock")
        expected_candidate = release_artifact.get("source_candidate_digest") or package_digest
        expected_gate_ids = {
            "exact_package_digest",
            "critical_security_failures",
            "target_writes",
            *(f"partition:{partition.lower()}" for partition in PARTITIONS),
        }
        if (
            evaluation_digest != summary.get("skill_evaluation_receipt_digest")
            or evaluation.get("verdict") != "CANARY"
            or summary.get("skill_evaluation_verdict") != "CANARY"
            or not isinstance(case_results, list)
            or not has_complete_case_identity(evaluation, execution_run_id)
            or summary.get("skill_evaluation_partition_count") != len(PARTITIONS)
            or summary.get("skill_evaluation_case_count", len(PARTITIONS)) != len(case_results)
            or {item.get("partition") for item in case_results if isinstance(item, Mapping)}
            != set(PARTITIONS)
            or not all(isinstance(item, Mapping) and item.get("passed") is True for item in case_results)
            or not isinstance(gate_results, list)
            or not gate_results
            or {item.get("gate_id") for item in gate_results if isinstance(item, Mapping)}
            != expected_gate_ids
            or not all(isinstance(item, Mapping) and item.get("passed") is True for item in gate_results)
            or evaluation.get("candidate_digest") != expected_candidate
            or evaluation.get("candidate_program_digest") != package.get("program_content_digest")
            or not isinstance(premise_lock, Mapping)
            or premise_lock.get("package") != package_digest
            or premise_lock.get("dependencies") != sha256_digest(dict(dependencies))
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_SKILL_EVALUATION_INVALID")

        expected_transitions = (
            ("DRAFT", "EVALUATED"),
            ("EVALUATED", "SHADOW"),
            ("SHADOW", "CANARY"),
        )
        if len(release_history) != len(expected_transitions) or summary.get(
            "skill_release_transition_count"
        ) != len(expected_transitions):
            raise RuntimeError("WORKSPACE_GOLDEN_SKILL_RELEASE_LEDGER_INVALID")
        previous_digest: str | None = None
        premise_digest = sha256_digest(dict(premise_lock))
        for index, (receipt, transition) in enumerate(
            zip(release_history, expected_transitions, strict=True),
            start=1,
        ):
            receipt_digest = cls._require_golden_digest(
                receipt,
                "WORKSPACE_GOLDEN_SKILL_RELEASE_DIGEST_INVALID",
            )
            if (
                receipt.get("schema_version") != "orgrebase.skill-release-receipt.v1"
                or receipt.get("event_index") != index
                or receipt.get("event_type") != "RELEASE"
                or (receipt.get("from_state"), receipt.get("to_state")) != transition
                or receipt.get("previous_receipt_digest") != previous_digest
                or receipt.get("actor_id") != SKILL_REGISTRY_AUTHORITY
                or receipt.get("reason_codes") != [f"GOLDEN_RUN_QUALIFIED_FOR_{transition[1]}"]
                or receipt.get("changed_dependency_refs") != []
                or receipt.get("skill_name") != package.get("name")
                or receipt.get("release_artifact_id") != release_artifact.get("id")
                or receipt.get("package_digest") != package_digest
                or receipt.get("effective_package_digest") != package_digest
                or receipt.get("predecessor_package_digest")
                != release_artifact.get("predecessor_package_digest")
                or receipt.get("predecessor_executable") is not False
                or receipt.get("restoration_status") != "NOT_APPLICABLE"
                or receipt.get("source_candidate_ref") != release_artifact.get("source_candidate_ref")
                or receipt.get("source_candidate_digest") != release_artifact.get("source_candidate_digest")
                or receipt.get("source_candidate_executable") is not False
                or receipt.get("evaluation_receipt_digest") != evaluation_digest
                or receipt.get("evaluation_premise_lock_digest") != premise_digest
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_SKILL_RELEASE_LEDGER_INVALID")
            previous_digest = receipt_digest

        release_head = release_history[-1]
        invocation_digest = cls._require_golden_digest(
            invocation,
            "WORKSPACE_GOLDEN_SKILL_RECEIPT_DIGEST_INVALID",
        )
        if (
            summary.get("skill_authorization_mode") != "RELEASE"
            or summary.get("skill_release_state") != "CANARY"
            or summary.get("skill_release_receipt_digest") != release_head["digest"]
            or invocation_digest != summary.get("skill_invocation_receipt_digest")
            or invocation.get("run_id") != execution_run_id
            or invocation.get("package_id") != package.get("package_id")
            or invocation.get("package_digest") != package_digest
            or invocation.get("manifest_digest") != package_digest
            or invocation.get("program_digest") != package.get("program_content_digest")
            or invocation.get("release_artifact_id") != release_artifact.get("id")
            or invocation.get("authorization_mode") != "RELEASE"
            or invocation.get("release_receipt_digest") != release_head["digest"]
            or invocation.get("candidate_only") is not True
            or invocation.get("target_writes") != 0
            or result.get("candidate_only") is not True
            or result.get("target_writes") != 0
            or result.get("action") != "APPLY_QUOTE"
            or sha256_digest(result) != invocation.get("output_digest")
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_SKILL_RELEASE_AUTHORITY_INVALID")

        return {
            "package_digest": package_digest,
            "authorization_mode": "RELEASE",
            "evaluation_receipt_digest": evaluation_digest,
            "evaluation_verdict": "CANARY",
            "evaluation_partition_count": len(PARTITIONS),
            **({"evaluation_case_count": len(case_results),
                "qualification_suite_revision": premise_lock["qualification_suite_revision"],
                "qualification_suite_digest": premise_lock["qualification_suite_digest"]}
               if "qualification_suite_revision" in premise_lock else {}),
            "release_receipt_digest": release_head["digest"],
            "release_state": "CANARY",
            "release_transition_count": len(release_history),
        }

    def _competition_evidence_record(self) -> dict[str, Any] | None:
        try:
            stored = self.store.load_artifact(
                GOLDEN_COMPETITION_ARTIFACT_ID,
                GOLDEN_COMPETITION_MEDIA_TYPE,
            )
        except KeyError:
            return None
        payload = stored.payload
        self._require_golden_digest(
            payload,
            "WORKSPACE_GOLDEN_COMPETITION_ARTIFACT_DIGEST_INVALID",
        )
        if (
            payload.get("correlation_id") != self.workflow_correlation_id
            or payload.get("status") != "PASS"
            or not isinstance(payload.get("run_id"), str)
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_COMPETITION_ARTIFACT_BINDING_INVALID")
        return payload

    def _competition_execution_reservation_record(self) -> dict[str, Any] | None:
        try:
            stored = self.store.load_artifact(
                GOLDEN_EXECUTION_RESERVATION_ARTIFACT_ID,
                GOLDEN_EXECUTION_RESERVATION_MEDIA_TYPE,
            )
        except KeyError:
            return None
        payload = stored.payload
        self._require_golden_digest(
            payload,
            "WORKSPACE_GOLDEN_EXECUTION_RESERVATION_DIGEST_INVALID",
        )
        run_id = payload.get("run_id")
        if (
            payload.get("schema_version")
            != "orgrebase.golden-execution-reservation.v1"
            or payload.get("status") != "RESERVED"
            or payload.get("correlation_id") != self.workflow_correlation_id
            or not isinstance(run_id, str)
            or not run_id.startswith("run:golden-competition:")
            or run_id == self.workflow_correlation_id
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_EXECUTION_RESERVATION_INVALID")
        return payload

    def _ensure_competition_execution_reservation(self) -> dict[str, Any]:
        reservation = self._competition_execution_reservation_record()
        if reservation is not None:
            return reservation
        reservation = self._golden_compact_record(
            {
                "schema_version": "orgrebase.golden-execution-reservation.v1",
                "status": "RESERVED",
                "run_id": f"run:golden-competition:{uuid4()}",
                "correlation_id": self.workflow_correlation_id,
                "canonical_target_writes": 0,
                "claim_boundary": "EXECUTION_ID_RESERVED_NOT_EXECUTION_EVIDENCE",
            }
        )
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                GOLDEN_EXECUTION_RESERVATION_ARTIFACT_ID,
                GOLDEN_EXECUTION_RESERVATION_MEDIA_TYPE,
                reservation,
            )
        return reservation

    def _activate_competition_execution_binding(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise RuntimeError("WORKSPACE_GOLDEN_EXECUTION_RUN_ID_INVALID")
        self.workflow_run_id = run_id
        self.effective_workflow_run_id = run_id
        self.workflow_run_nonce = sha256_digest({"run_id": run_id, "mode": "LOCAL_DETERMINISTIC"}).split(
            ":", 1
        )[1]
        self.formation.workflow_run_id = run_id

    def _restore_competition_execution_binding(self) -> None:
        evidence = self._competition_evidence_record()
        if evidence is not None:
            reservation = self._competition_execution_reservation_record()
            if reservation is not None and reservation["run_id"] != evidence["run_id"]:
                raise RuntimeError("WORKSPACE_GOLDEN_EXECUTION_RESERVATION_MISMATCH")
            self._activate_competition_execution_binding(str(evidence["run_id"]))
            return
        if self.competition_mode == GOLDEN_COMPETITION_MODE:
            reservation = self._ensure_competition_execution_reservation()
            self._activate_competition_execution_binding(str(reservation["run_id"]))
            return
        self._restore_correlation_binding()

    def _ensure_terminal_experience(self) -> dict[str, Any] | None:
        """Extract experience only after the complete Quote v1 -> v3 journey."""

        evidence = self._competition_evidence_record()
        if evidence is None:
            return None
        if not self._business_complete():
            return None
        return self.experience.ensure_candidate(evidence)

    def _restore_correlation_binding(self) -> None:
        self.workflow_run_id = self._declared_workflow_correlation_id
        self.effective_workflow_run_id = self.workflow_correlation_id
        self.workflow_run_nonce = sha256_digest(
            {
                "run_id": self.workflow_correlation_id,
                "mode": "LOCAL_DETERMINISTIC",
            }
        ).split(":", 1)[1]
        self.formation.workflow_run_id = self._declared_workflow_correlation_id

    @contextmanager
    def _competition_execution_scope(self, run_id: str | None):
        if run_id is None:
            yield
            return
        self._activate_competition_execution_binding(run_id)
        try:
            yield
        except Exception:
            self._restore_competition_execution_binding()
            raise

    def _next_competition_output_dir(self) -> Path:
        root = self.competition_evidence_root
        if root is None:
            raise RuntimeError("WORKSPACE_GOLDEN_EVIDENCE_ROOT_REQUIRED")
        root.mkdir(parents=True, exist_ok=True)
        for sequence in range(1, 100_000):
            output = root / f"run-{sequence:05d}"
            try:
                output.mkdir()
            except FileExistsError:
                continue
            return output
        raise RuntimeError("WORKSPACE_GOLDEN_EVIDENCE_SEQUENCE_EXHAUSTED")

    def _prepare_golden_competition(
        self,
        request: TaskRequest,
        *,
        task_formation_decision_receipt: Mapping[str, Any] | None = None,
        context_envelope: Mapping[str, Any] | None = None,
    ) -> tuple[PreparedFormationBundle, dict[str, Any]]:
        if self.runtime_configuration is None or self.competition_pack_path is None:
            raise RuntimeError("WORKSPACE_GOLDEN_PACK_REQUIRED")
        if self.competition_checkout is None or self.competition_lock_path is None:
            raise RuntimeError("WORKSPACE_GOLDEN_AGENTTEAMS_SOURCE_REQUIRED")
        output = self._next_competition_output_dir()
        runner = self._competition_runner
        if runner is None:
            from orgrebase.workspace.competition_run import run_golden_competition

            runner = run_golden_competition
        reserved_execution_run_id = self.effective_workflow_run_id
        if reserved_execution_run_id == self.workflow_correlation_id:
            raise RuntimeError("WORKSPACE_GOLDEN_EXECUTION_RESERVATION_REQUIRED")
        runner_kwargs: dict[str, Any] = {
            "repo_root": Path(__file__).resolve().parents[3],
            "output_dir": output,
            "checkout": self.competition_checkout,
            "lock_path": self.competition_lock_path,
            "pack_path": self.competition_pack_path,
            "model_provider": self.competition_model_provider,
            "ollama_endpoint": self.competition_ollama_endpoint,
            "vertex_project": self.competition_vertex_project,
            "execution_run_id": reserved_execution_run_id,
        }
        if (task_formation_decision_receipt is None) != (context_envelope is None):
            raise IntegrityError("WORKSPACE_OAC_EXECUTION_ROOTS_INCOMPLETE")
        if task_formation_decision_receipt is not None and context_envelope is not None:
            context_digest = context_envelope.get("digest")
            if not isinstance(context_digest, str):
                raise IntegrityError("WORKSPACE_OAC_CONTEXT_DIGEST_REQUIRED")
            runner_kwargs.update(
                {
                    "context_envelope_digest": context_digest,
                    "task_formation_decision_receipt": task_formation_decision_receipt,
                    "context_envelope": context_envelope,
                }
            )
        returned_summary = runner(
            **runner_kwargs,
        )
        if not isinstance(returned_summary, dict):
            raise RuntimeError("WORKSPACE_GOLDEN_SUMMARY_INVALID")
        summary = self._load_golden_json(
            output / "summary.json",
            "WORKSPACE_GOLDEN_SUMMARY_MISSING",
        )
        if returned_summary != summary:
            raise RuntimeError("WORKSPACE_GOLDEN_SUMMARY_RETURN_MISMATCH")
        summary_digest = self._require_golden_digest(
            summary,
            "WORKSPACE_GOLDEN_SUMMARY_DIGEST_INVALID",
        )
        if summary.get("status") != "PASS":
            raise RuntimeError(
                "WORKSPACE_GOLDEN_COMPETITION_NOT_PASS:"
                + str(summary.get("reason", summary.get("status", "UNKNOWN")))
            )
        execution_run_id = summary.get("run_id")
        pack = summary.get("enterprise_pack")
        if (
            not isinstance(execution_run_id, str)
            or not execution_run_id.strip()
            or execution_run_id != reserved_execution_run_id
            or summary.get("correlation_id") != self.workflow_correlation_id
            or execution_run_id == self.workflow_correlation_id
            or summary.get("canonical_target_writes") != 0
            or summary.get("project_terminal_state") != "completed"
            or not isinstance(pack, dict)
            or pack.get("pack_digest") != getattr(self.runtime_configuration, "pack_digest", None)
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_SUMMARY_BINDING_INVALID")

        bound_execution_plan: AgentTeamsExecutionPlan | None = None
        bound_formation_receipt: TaskFormationDecisionReceipt | None = None
        bound_context_envelope: TaskAgentContextEnvelope | None = None
        if task_formation_decision_receipt is not None and context_envelope is not None:
            try:
                bound_formation_receipt = TaskFormationDecisionReceipt.model_validate(
                    task_formation_decision_receipt
                )
                bound_context_envelope = TaskAgentContextEnvelope.model_validate(
                    context_envelope
                )
                bound_execution_plan = AgentTeamsExecutionPlan.model_validate(
                    self._load_golden_json(
                        output / "agentteams" / "execution-plan.json",
                        "WORKSPACE_GOLDEN_OAC_EXECUTION_PLAN_MISSING",
                    )
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError("WORKSPACE_GOLDEN_OAC_EXECUTION_PLAN_INVALID") from exc
            execution_envelope = self._load_golden_json(
                output / "execution-envelope.json",
                "WORKSPACE_GOLDEN_EXECUTION_ENVELOPE_MISSING",
            )
            retained_formation_root = self._load_golden_json(
                output / "inputs" / "task-formation-decision-receipt.json",
                "WORKSPACE_GOLDEN_OAC_FORMATION_ROOT_MISSING",
            )
            retained_context_root = self._load_golden_json(
                output / "inputs" / "task-agent-context-envelope.json",
                "WORKSPACE_GOLDEN_OAC_CONTEXT_ROOT_MISSING",
            )
            receipt_domains = bound_formation_receipt.selected_domain_ids
            context_domains = tuple(
                item.domain_id for item in bound_context_envelope.domain_bindings
            )
            if (
                retained_formation_root
                != bound_formation_receipt.model_dump(mode="json")
                or retained_context_root
                != bound_context_envelope.model_dump(mode="json")
                or bound_formation_receipt.task_ref != request.id
                or bound_formation_receipt.task_digest != request.digest
                or bound_context_envelope.task_ref != request.id
                or bound_context_envelope.task_digest != request.digest
                or bound_execution_plan.task_ref != request.id
                or bound_execution_plan.task_digest != request.digest
                or bound_context_envelope.task_formation_decision_receipt_digest
                != bound_formation_receipt.digest
                or bound_context_envelope.admitted_organizational_intent_digest
                != bound_formation_receipt.organizational_demand_digest
                or bound_context_envelope.coalition_plan_ref
                != bound_formation_receipt.coalition_plan_ref
                or bound_context_envelope.coalition_plan_digest
                != bound_formation_receipt.coalition_plan_digest
                or bound_execution_plan.coalition_plan_ref
                != bound_formation_receipt.coalition_plan_ref
                or bound_execution_plan.coalition_plan_digest
                != bound_formation_receipt.coalition_plan_digest
                or bound_execution_plan.selected_domain_ids != receipt_domains
                or context_domains != receipt_domains
                or bound_execution_plan.formation_receipt_digest
                != bound_formation_receipt.digest
                or bound_execution_plan.context_envelope_digest
                != bound_context_envelope.digest
                or summary.get("task_formation_decision_receipt_digest")
                != bound_formation_receipt.digest
                or summary.get("context_envelope_digest")
                != bound_context_envelope.digest
                or summary.get("agentteams_execution_plan_digest")
                != bound_execution_plan.digest
                or execution_envelope.get("task_formation_decision_receipt_digest")
                != bound_formation_receipt.digest
                or execution_envelope.get("context_envelope_digest")
                != bound_context_envelope.digest
                or execution_envelope.get("agentteams_execution_plan_digest")
                != bound_execution_plan.digest
                or tuple(summary.get("planned_domain_ids", ()))
                != bound_execution_plan.selected_domain_ids
                or tuple(summary.get("actual_agentteams_domain_ids", ()))
                != bound_execution_plan.selected_domain_ids
                or summary.get("topology_match") is not True
                or summary.get("context_freshness_basis") != "LOGICAL_EVENT_TIME"
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_OAC_EXECUTION_ROOT_BINDING_INVALID")

        raw_bindings = summary.get("task_bindings")
        if not isinstance(raw_bindings, list) or len(raw_bindings) != 7:
            raise RuntimeError("WORKSPACE_GOLDEN_TASK_BINDINGS_INVALID")
        logical_plan_tasks = (
            {item.task_id: item for item in bound_execution_plan.tasks}
            if bound_execution_plan is not None
            else {}
        )
        bindings: list[dict[str, Any]] = []
        for raw in raw_bindings:
            if not isinstance(raw, dict):
                raise RuntimeError("WORKSPACE_GOLDEN_TASK_BINDINGS_INVALID")
            self._require_golden_digest(
                raw,
                "WORKSPACE_GOLDEN_TASK_BINDING_DIGEST_INVALID",
            )
            if (
                raw.get("run_id") != execution_run_id
                or raw.get("correlation_id") != self.workflow_correlation_id
                or raw.get("candidate_only") is not True
                or raw.get("target_writes") != 0
                or raw.get("status") != "COMPLETED"
                or raw.get("role") not in {"DOMAIN_WORKER", "REVIEWER"}
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_TASK_BINDING_INVALID")
            if bound_execution_plan is not None:
                logical_task = logical_plan_tasks.get(raw.get("logical_plan_task_id"))
                if (
                    raw.get("agentteams_execution_plan_digest")
                    != bound_execution_plan.digest
                    or raw.get("formation_receipt_digest")
                    != bound_execution_plan.formation_receipt_digest
                    or raw.get("context_envelope_digest")
                    != bound_execution_plan.context_envelope_digest
                    or logical_task is None
                    or raw.get("logical_plan_task_digest") != logical_task.digest
                    or raw.get("sealed_plan_task")
                    != logical_task.model_dump(mode="json")
                ):
                    raise RuntimeError("WORKSPACE_GOLDEN_TASK_OAC_BINDING_INVALID")
            bindings.append(raw)
        if (
            sum(item["role"] == "DOMAIN_WORKER" for item in bindings) != 5
            or sum(item["role"] == "REVIEWER" for item in bindings) != 2
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_TASK_ROLE_COUNTS_INVALID")

        try:
            process_receipts = json.loads((output / "process-receipts.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_RECEIPTS_MISSING") from exc
        if not isinstance(process_receipts, list) or len(process_receipts) != 7:
            raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_RECEIPTS_INVALID")
        binding_by_task = {item["task_id"]: item for item in bindings}
        process_by_task: dict[str, dict[str, Any]] = {}
        for raw in process_receipts:
            if not isinstance(raw, dict):
                raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_RECEIPTS_INVALID")
            digest = self._require_golden_digest(
                raw,
                "WORKSPACE_GOLDEN_PROCESS_RECEIPT_DIGEST_INVALID",
            )
            task_id = raw.get("task_id")
            if (
                not isinstance(task_id, str)
                or task_id in process_by_task
                or raw.get("independent_process") is not True
                or raw.get("canonical_target_writes") != 0
                or raw.get("exit_code") != 0
                or raw.get("run_id") != execution_run_id
                or raw.get("correlation_id") != self.workflow_correlation_id
                or digest not in summary.get("process_receipt_digests", [])
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_RECEIPT_INVALID")
            binding = binding_by_task.get(task_id)
            if bound_execution_plan is not None and (
                binding is None
                or raw.get("input_digest") != binding.get("input_digest")
                or raw.get("started_after_ack_action_digest")
                != binding.get("ack_action_digest")
                or any(
                    raw.get(field) != binding.get(field)
                    for field in (
                        "context_envelope_digest",
                        "agentteams_execution_plan_digest",
                        "logical_plan_task_id",
                        "logical_plan_task_digest",
                        "formation_receipt_id",
                        "formation_receipt_digest",
                    )
                )
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_OAC_BINDING_INVALID")
            process_by_task[task_id] = raw
        if set(process_by_task) != {item["task_id"] for item in bindings}:
            raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_TASK_SET_MISMATCH")

        outputs: dict[str, dict[str, Any]] = {}
        for binding in bindings:
            task_id = binding["task_id"]
            result = self._load_golden_json(
                output / "process-outputs" / f"{task_id}.json",
                "WORKSPACE_GOLDEN_PROCESS_OUTPUT_MISSING",
            )
            if (
                result.get("run_id") != execution_run_id
                or result.get("correlation_id") != self.workflow_correlation_id
                or result.get("task_id") != task_id
                or result.get("candidate_only") is not True
                or result.get("target_writes") != 0
                or sha256_digest(result) != binding.get("observed_result_digest")
                or sha256_digest(result) != process_by_task[task_id].get("output_digest")
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_OUTPUT_BINDING_INVALID")
            if bound_execution_plan is not None and any(
                result.get(field) != binding.get(field)
                for field in (
                    "agentteams_execution_plan_digest",
                    "context_envelope_digest",
                    "logical_plan_task_id",
                    "logical_plan_task_digest",
                    "formation_receipt_id",
                    "formation_receipt_digest",
                )
            ):
                raise RuntimeError("WORKSPACE_GOLDEN_PROCESS_OUTPUT_OAC_BINDING_INVALID")
            outputs[task_id] = result

        tool = self._load_golden_json(
            output / "tool" / "invocation.json",
            "WORKSPACE_GOLDEN_TOOL_EVIDENCE_MISSING",
        )
        tool_receipt = tool.get("receipt")
        if (
            tool.get("run_id") != execution_run_id
            or tool.get("candidate_only") is not True
            or tool.get("target_writes") != 0
            or not isinstance(tool_receipt, dict)
            or tool_receipt.get("run_id") != execution_run_id
            or tool_receipt.get("status") != "SUCCEEDED"
            or tool_receipt.get("target_writes") != 0
            or self._require_golden_digest(
                tool_receipt,
                "WORKSPACE_GOLDEN_TOOL_RECEIPT_DIGEST_INVALID",
            )
            != summary.get("tool_receipt_digest")
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_TOOL_EVIDENCE_INVALID")

        skill_package = self._load_golden_json(
            output / "skill" / "package.json",
            "WORKSPACE_GOLDEN_SKILL_PACKAGE_MISSING",
        )
        skill_evaluation = self._load_golden_json(
            output / "skill" / "evaluation.json",
            "WORKSPACE_GOLDEN_SKILL_EVALUATION_MISSING",
        )
        skill_release_history = self._load_golden_json_array(
            output / "skill" / "release-ledger.json",
            "WORKSPACE_GOLDEN_SKILL_RELEASE_LEDGER_MISSING",
        )
        skill_receipt = self._load_golden_json(
            output / "skill" / "receipt.json",
            "WORKSPACE_GOLDEN_SKILL_RECEIPT_MISSING",
        )
        skill_result = self._load_golden_json(
            output / "skill" / "result.json",
            "WORKSPACE_GOLDEN_SKILL_RESULT_MISSING",
        )
        skill_release = self._verify_golden_skill_release_evidence(
            package=skill_package,
            evaluation=skill_evaluation,
            release_history=skill_release_history,
            invocation=skill_receipt,
            result=skill_result,
            summary=summary,
            execution_run_id=execution_run_id,
        )

        prepared_payload = self._load_golden_json(
            output / "prepared-formation-bundle.json",
            "WORKSPACE_GOLDEN_PREPARED_FORMATION_MISSING",
        )
        try:
            prepared = PreparedFormationBundle.model_validate(prepared_payload)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("WORKSPACE_GOLDEN_PREPARED_FORMATION_INVALID") from exc
        trace = self._prepared_formation_trace(prepared)
        if (
            prepared.digest != summary.get("prepared_formation_digest")
            or prepared.request_digest != request.digest
            or prepared.idempotency_key != request.idempotency_key
            or prepared.task_receipt.task_ref != request.id
            or prepared.deliverable.ref != summary.get("prepared_quote_ref")
            or prepared.deliverable.payload != summary.get("prepared_quote_payload")
            or trace.run_id != execution_run_id
            or prepared.event_payload.get("golden_competition_run_id") != execution_run_id
            or prepared.event_payload.get("golden_competition_correlation_id") != self.workflow_correlation_id
            or prepared.event_payload.get("canonical_target_writes") != 0
            or prepared.event_payload.get("reviewer_result_digest")
            != next(
                (
                    item.get("observed_result_digest")
                    for item in bindings
                    if item.get("role") == "REVIEWER" and item.get("attempt") == 2
                ),
                None,
            )
            or prepared.event_payload.get("tool_receipt_digest") != summary.get("tool_receipt_digest")
            or prepared.event_payload.get("skill_invocation_receipt_digest")
            != summary.get("skill_invocation_receipt_digest")
            or prepared.event_payload.get("skill_authorization_mode") != "RELEASE"
            or prepared.event_payload.get("skill_evaluation_receipt_digest")
            != skill_release["evaluation_receipt_digest"]
            or prepared.event_payload.get("skill_release_receipt_digest")
            != skill_release["release_receipt_digest"]
            or prepared.event_payload.get("skill_release_state") != "CANARY"
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_PREPARED_FORMATION_BINDING_INVALID")
        if bound_execution_plan is not None and (
            prepared.event_payload.get("task_formation_decision_receipt_digest")
            != bound_execution_plan.formation_receipt_digest
            or prepared.event_payload.get("context_envelope_digest")
            != bound_execution_plan.context_envelope_digest
            or prepared.event_payload.get("agentteams_execution_plan_digest")
            != bound_execution_plan.digest
            or tuple(prepared.event_payload.get("planned_domain_ids", ()))
            != bound_execution_plan.selected_domain_ids
            or prepared.event_payload.get("topology_match") is not True
        ):
            raise RuntimeError("WORKSPACE_GOLDEN_PREPARED_OAC_BINDING_INVALID")

        tasks: list[dict[str, Any]] = []
        agent_runs: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = []
        reviewer_a1 = next(
            item["task_id"] for item in bindings if item["role"] == "REVIEWER" and item["attempt"] == 1
        )
        final_domain_tasks = {
            item["domain"]: item["task_id"]
            for item in bindings
            if item["role"] == "DOMAIN_WORKER" and (item["domain"] != "finance" or item["attempt"] == 2)
        }
        coordinator_task = self._golden_compact_record(
            {
                "id": summary["project_id"],
                "agent_name": "change-coordinator",
                "authority_domain": "coordination",
                "depends_on": [],
                "input_refs": [
                    item
                    for item in (
                        pack["pack_digest"],
                        bound_execution_plan and bound_execution_plan.formation_receipt_digest,
                        bound_execution_plan and bound_execution_plan.context_envelope_digest,
                        bound_execution_plan and bound_execution_plan.digest,
                    )
                    if item
                ],
                "candidate_only": True,
                "target_writes": 0,
            }
        )
        tasks.append(coordinator_task)
        agent_runs.append(
            self._golden_compact_record(
                {
                    "id": f"agent-run:{summary['project_id']}:manager",
                    "task_id": summary["project_id"],
                    "agent_name": "change-coordinator",
                    "role": "COORDINATOR",
                    "attempt": 1,
                    "authority_domain": "coordination",
                    "status": "TRUSTED_COMPLETE",
                    "input_digest": pack["pack_digest"],
                    "output_digest": summary["plan_revisions"][-1]["digest"],
                    "model_version": "NONE_DETERMINISTIC_MANAGER",
                    "tool_versions": [
                        f"AgentTeams TeamHarness@{summary['source_verification']['commit']}"
                    ],
                    "skill_versions": [],
                    "trace_id": summary_digest,
                    "evidence_class": "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
                    "candidate_only": True,
                    "target_writes": 0,
                }
            )
        )
        for binding in bindings:
            task_id = binding["task_id"]
            result = outputs[task_id]
            assignee = str(binding["assignee"]).removeprefix("@").split(":", 1)[0]
            if binding["role"] == "REVIEWER":
                depends_on = (
                    [
                        item["task_id"]
                        for item in bindings
                        if item["role"] == "DOMAIN_WORKER" and item["attempt"] == 1
                    ]
                    if binding["attempt"] == 1
                    else list(final_domain_tasks.values())
                )
                authority_domain = "review"
                model_receipt = result.get("model_receipt") or {}
                observed_model_version = model_receipt.get("model_version")
                if not observed_model_version:
                    model_version = "NOT_OBSERVED"
                elif model_receipt.get("provider") == "vertex-ai":
                    model_version = str(observed_model_version)
                else:
                    model_version = (
                        f"{model_receipt.get('model_id', 'qwen2.5:3b')}@"
                        f"{observed_model_version}"
                    )
            else:
                depends_on = [reviewer_a1] if binding["attempt"] == 2 else []
                authority_domain = binding["domain"]
                model_version = "NONE_DETERMINISTIC_DOMAIN_WORKER"
            task = self._golden_compact_record(
                {
                    "id": task_id,
                    "agent_name": assignee,
                    "authority_domain": authority_domain,
                    "role": binding["role"],
                    "attempt": binding["attempt"],
                    "depends_on": depends_on,
                    "input_refs": [
                        item
                        for item in (
                            binding.get("projection_digest"),
                            binding.get("tool_receipt_digest"),
                        )
                        if item
                    ],
                    "input_digest": binding["input_digest"],
                    "output_digest": binding["observed_result_digest"],
                    "candidate_only": True,
                    "target_writes": 0,
                    "binding_digest": binding["digest"],
                    **(
                        {
                            "agentteams_execution_plan_digest": (
                                binding["agentteams_execution_plan_digest"]
                            ),
                            "logical_plan_task_id": binding["logical_plan_task_id"],
                            "logical_plan_task_digest": binding["logical_plan_task_digest"],
                            "formation_receipt_digest": binding["formation_receipt_digest"],
                            "context_envelope_digest": binding["context_envelope_digest"],
                        }
                        if bound_execution_plan is not None
                        else {}
                    ),
                }
            )
            tasks.append(task)
            process = process_by_task[task_id]
            agent_runs.append(
                self._golden_compact_record(
                    {
                        "id": f"agent-run:{task_id}",
                        "task_id": task_id,
                        "agent_name": assignee,
                        "role": binding["role"],
                        "attempt": binding["attempt"],
                        "authority_domain": authority_domain,
                        "status": (
                            "TRUSTED_COMPLETE"
                            if result.get("status") == "PASS"
                            else str(result.get("status", "NOT_OBSERVED"))
                        ),
                        "input_digest": process["input_digest"],
                        "output_digest": process["output_digest"],
                        "model_version": model_version,
                        "model_evidence_class": (
                            model_receipt.get("evidence_class")
                            if binding["role"] == "REVIEWER"
                            else "NOT_APPLICABLE"
                        ),
                        "model_provider": (
                            model_receipt.get("provider")
                            if binding["role"] == "REVIEWER"
                            else "NOT_APPLICABLE"
                        ),
                        "provider_request_id": (
                            model_receipt.get("provider_request_id")
                            if binding["role"] == "REVIEWER"
                            else None
                        ),
                        "model_advisory_accepted": (
                            result.get("model_advisory_accepted")
                            if binding["role"] == "REVIEWER"
                            else None
                        ),
                        "model_advisory": (
                            self._golden_review_decision(result.get("model_advisory"))
                            if binding["role"] == "REVIEWER"
                            else None
                        ),
                        "deterministic_review": (
                            self._golden_review_decision(result.get("decision"))
                            if binding["role"] == "REVIEWER"
                            else None
                        ),
                        "model_claim_boundary": (
                            (result.get("model_runtime_binding") or {}).get("claim_boundary")
                            if binding["role"] == "REVIEWER"
                            else "NOT_APPLICABLE"
                        ),
                        "tool_versions": (
                            ["dependency-evidence@CONTROLLED_LOCAL_REAL_HTTP"]
                            if binding.get("tool_receipt_digest")
                            else []
                        ),
                        "skill_versions": [],
                        "trace_id": process["digest"],
                        "process_id": process["process_id"],
                        "independent_process": True,
                        "evidence_class": "CONTROLLED_LOCAL_INDEPENDENT_PROCESS",
                        "candidate_only": True,
                        "target_writes": 0,
                        **(
                            {
                                "agentteams_execution_plan_digest": (
                                    binding["agentteams_execution_plan_digest"]
                                ),
                                "logical_plan_task_id": binding["logical_plan_task_id"],
                                "logical_plan_task_digest": binding["logical_plan_task_digest"],
                                "formation_receipt_digest": binding["formation_receipt_digest"],
                                "context_envelope_digest": binding["context_envelope_digest"],
                            }
                            if bound_execution_plan is not None
                            else {}
                        ),
                    }
                )
            )
            handoffs.append(
                self._golden_compact_record(
                    {
                        "id": f"handoff:{task_id}",
                        "task_id": task_id,
                        "from_agent": assignee,
                        "to_agent": (
                            "change-coordinator" if binding["role"] == "REVIEWER" else "quote-reviewer"
                        ),
                        "input_refs": [binding["observed_result_digest"]],
                        "candidate_only": True,
                        "payload": {
                            "kind": "REVIEW_RESULT" if binding["role"] == "REVIEWER" else "DOMAIN_CANDIDATE",
                            "target_writes": 0,
                            "output_digest": binding["observed_result_digest"],
                        },
                    }
                )
            )
        orchestration_plan = self._golden_compact_record(
            {
                "project_id": summary["project_id"],
                "run_id": execution_run_id,
                "status": "COMPLETED",
                "plan_revisions": summary["plan_revisions"],
                "tasks": tasks,
            }
        )
        collaboration = self._golden_compact_record(
            {
                "orchestration_plan": orchestration_plan,
                "agent_runs": agent_runs,
                "handoffs": handoffs,
                "reviewer": {
                    "attempt_1": summary["reviewer_attempt_1"],
                    "attempt_2": summary["reviewer_attempt_2"],
                    "model_provider": summary["model_provider"],
                    "model_version": summary["reviewer_model_attempts"][-1][
                        "observed_model_version"
                    ],
                    "model_attempts": summary["reviewer_model_attempts"],
                    "model_evidence_class": summary["reviewer_model_evidence_class"],
                    "model_claim_boundary": summary["reviewer_model_claim_boundary"],
                    "target_writes": 0,
                },
                "tool": {
                    "operation": tool_receipt["operation"],
                    "status": tool_receipt["status"],
                    "receipt_digest": tool_receipt["digest"],
                    "evidence_class": tool_receipt["evidence_class"],
                    "target_writes": 0,
                },
                "skill": {
                    "package_id": skill_receipt["package_id"],
                    "status": skill_receipt["outcome"],
                    "receipt_digest": skill_receipt["digest"],
                    "action": skill_result["action"],
                    **skill_release,
                    "target_writes": 0,
                },
                "authority": {
                    "candidate_target_writes": 0,
                    "agent_target_writes": 0,
                    "reviewer_target_writes": 0,
                    "tool_target_writes": 0,
                    "skill_target_writes": 0,
                    "canonical_state": "OrgRebase StateStore and RebaseWorkflow",
                    "formation_quote_target_writes": 1,
                    "formation_graph_pointer_target_writes": 1,
                    "formation_commit_owner": "DETERMINISTIC_CONTROL_PLANE",
                },
                **(
                    {
                        "oac_task_formation": {
                            "status": "BOUND_TO_CURRENT_RUN",
                            "objective": bound_formation_receipt.objective,
                            "selected_domain_ids": list(
                                bound_execution_plan.selected_domain_ids
                            ),
                            "unknown_obligation_resource_ids": list(
                                bound_formation_receipt.unknown_obligation_resource_ids
                            ),
                            "task_formation_decision_receipt_digest": (
                                bound_formation_receipt.digest
                            ),
                            "context_envelope_ref": bound_context_envelope.ref,
                            "context_envelope_digest": bound_context_envelope.digest,
                            "task_context_ref": bound_context_envelope.task_context_ref,
                            "task_context_digest": bound_context_envelope.task_context_digest,
                            "context_expires_at": bound_context_envelope.expires_at,
                            "agentteams_execution_plan_digest": (
                                bound_execution_plan.digest
                            ),
                            "domain_bindings": [
                                item.model_dump(mode="json")
                                for item in bound_context_envelope.domain_bindings
                            ],
                            "plan_tasks": [
                                {
                                    "task_id": item.task_id,
                                    "task_kind": item.task_kind,
                                    "domain_id": item.domain_id,
                                    "assignee_actor_id": item.assignee_actor_id,
                                    "depends_on": list(item.depends_on),
                                    "capability_card_ref": item.capability_card_ref,
                                    "actor_projection_ref": item.actor_projection_ref,
                                    "input_schema_refs": list(item.input_schema_refs),
                                    "output_schema_refs": list(item.output_schema_refs),
                                }
                                for item in bound_execution_plan.tasks
                            ],
                            "candidate_only": True,
                            "canonical_target_writes": 0,
                        }
                    }
                    if bound_execution_plan is not None
                    and bound_formation_receipt is not None
                    and bound_context_envelope is not None
                    else {}
                ),
            }
        )
        evidence = self._golden_compact_record(
            {
                "schema_version": "orgrebase.workspace-golden-competition-evidence.v1",
                "status": "PASS",
                "run_id": execution_run_id,
                "correlation_id": self.workflow_correlation_id,
                "evidence_class": summary["evidence_class"],
                "model_provider": summary["model_provider"],
                "claim_boundary": summary["claim_boundary"],
                "summary_digest": summary_digest,
                "source_verification": summary["source_verification"],
                "output_run": output.name,
                "project_terminal_state": summary["project_terminal_state"],
                "agentteams_action_count": summary["agentteams_action_count"],
                "independent_domain_worker_processes": summary["independent_domain_worker_processes"],
                "independent_reviewer_processes": summary["independent_reviewer_processes"],
                "prepared_formation_digest": prepared.digest,
                "prepared_quote_ref": prepared.deliverable.ref,
                **(
                    {
                        "task_formation_decision_receipt_digest": (
                            bound_execution_plan.formation_receipt_digest
                        ),
                        "context_envelope_digest": (
                            bound_execution_plan.context_envelope_digest
                        ),
                        "agentteams_execution_plan_digest": (
                            bound_execution_plan.digest
                        ),
                        "selected_domain_ids": list(
                            bound_execution_plan.selected_domain_ids
                        ),
                    }
                    if bound_execution_plan is not None
                    else {}
                ),
                **(
                    {
                        "oac_agentteams_lineage": {
                            "status": "OAC_BOUND_EXECUTION_PLAN_REALIZED",
                            "task_formation_decision_receipt_digest": (
                                bound_formation_receipt.digest
                            ),
                            "task_agent_context_envelope_digest": (
                                bound_context_envelope.digest
                            ),
                            "agentteams_execution_plan_digest": (
                                bound_execution_plan.digest
                            ),
                            "organization_snapshot_digest": (
                                bound_formation_receipt.organization_snapshot_digest
                            ),
                            "organizational_demand_digest": (
                                bound_formation_receipt.organizational_demand_digest
                            ),
                            "planned_domain_ids": list(
                                bound_execution_plan.selected_domain_ids
                            ),
                            "actual_agentteams_domain_ids": list(
                                bound_execution_plan.selected_domain_ids
                            ),
                            "topology_match": True,
                            "context_freshness_basis": "LOGICAL_EVENT_TIME",
                            "candidate_only": True,
                            "canonical_target_writes": 0,
                        }
                    }
                    if bound_execution_plan is not None
                    and bound_formation_receipt is not None
                    and bound_context_envelope is not None
                    else {}
                ),
                "agent_collaboration": collaboration,
                "canonical_target_writes_before_control_commit": 0,
                "canonical_formation_commit": "SAME_SQLITE_TRANSACTION",
                "external_promotion_status": "NOT_RUN",
            }
        )
        return prepared, evidence

    def _invoke_dependency_evidence_tool(
        self,
        formation: TaskReceipt,
    ) -> tuple[DependencyEvidenceTool, PreparedDependencyEvidenceInvocation]:
        """Prepare the real read-only tool call without writing canonical state."""

        fixture = self.snapshot_builder.to_enterprise_fixture(
            snapshot=self.current_snapshot(), artifact_reader=self.store.load_artifact,
            object_reader=self.store.get_object, agents=self.legacy.agents,
            evaluation_cases=self.legacy.evaluation_cases,
            context_profiles=self.context_profiles, change={},
        )
        tool = DependencyEvidenceTool(fixture, self.store)
        prepared = tool.prepare(
            actor_id="gtm-steward",
            workflow_run_id=self.effective_workflow_run_id,
            run_nonce=self.workflow_run_nonce,
            target_ids=(formation.deliverable_ref.rsplit("@", 1)[0],),
            graph_revision=fixture.revisions["graph"],
            idempotency_key=DEPENDENCY_TOOL_IDEMPOTENCY_KEY,
            started_at=self.clock.now(),
            completed_at=self.clock.now(),
            evidence_class=EvidenceClass.LOCAL_REAL_TOOL,
        )
        return tool, prepared

    def _tool_called_event(
        self,
        *,
        formation: TaskReceipt,
        trace: WorkTrace,
        invocation: dict[str, Any],
    ) -> ToolCalledEvent:
        receipt = ToolInvocationReceipt.model_validate(invocation["receipt"])
        return ToolCalledEvent(
            event_id=DEPENDENCY_TOOL_CALLED_EVENT_ID,
            task_ref=formation.task_ref,
            run_id=self.effective_workflow_run_id,
            sequence=len(trace.events) + 1,
            tool_ref=receipt.tool_ref,
            invocation_receipt_ref=receipt.id,
            request_digest=receipt.request_digest,
            result_digest=receipt.result_digest,
            occurred_at=receipt.completed_at,
            idempotency_key="workspace-quote-v1-dependency-evidence:event",
            previous_event_digest=trace.head_event_digest,
        )

    def _dependency_evidence_tool_record(self) -> dict[str, Any]:
        invocation_artifact = None
        called_event_artifact = None
        with suppress(KeyError):
            invocation_artifact = self.store.load_artifact(
                DEPENDENCY_TOOL_RECEIPT_ID,
                TOOL_INVOCATION_MEDIA_TYPE,
            )
        with suppress(KeyError):
            called_event_artifact = self.store.load_artifact(
                DEPENDENCY_TOOL_CALLED_EVENT_ID,
                TOOL_CALLED_EVENT_MEDIA_TYPE,
            )
        if invocation_artifact is None and called_event_artifact is None:
            return {
                "status": "NOT_RUN",
                "evidence_class": "NOT_RUN",
                "target_writes": 0,
            }
        if invocation_artifact is None or called_event_artifact is None:
            raise RuntimeError("WORKSPACE_DEPENDENCY_TOOL_AUDIT_INCOMPLETE")
        formation = self._formation_record()
        if formation is None:
            raise RuntimeError("WORKSPACE_DEPENDENCY_TOOL_FORMATION_MISSING")
        invocation = invocation_artifact.payload
        receipt = ToolInvocationReceipt.model_validate(invocation.get("receipt"))
        called_event = ToolCalledEvent.model_validate(called_event_artifact.payload)
        trace = WorkTrace.model_validate(self.store.load_artifact(formation.trace_ref).payload)
        audit_event = self.changes.journal_event(receipt.audit_event_digest)
        if audit_event is None:
            self.changes.refresh()
            audit_event = self.changes.journal_event(receipt.audit_event_digest)
        if (
            receipt.id != DEPENDENCY_TOOL_RECEIPT_ID
            or receipt.status != "SUCCEEDED"
            or receipt.evidence_class != EvidenceClass.LOCAL_REAL_TOOL
            or audit_event is None
            or audit_event["event_type"] != "TOOL_INVOKED"
            or called_event.event_id != DEPENDENCY_TOOL_CALLED_EVENT_ID
            or called_event.task_ref != formation.task_ref
            or called_event.run_id != receipt.workflow_run_id
            or called_event.tool_ref != receipt.tool_ref
            or called_event.invocation_receipt_ref != receipt.id
            or called_event.request_digest != receipt.request_digest
            or called_event.result_digest != receipt.result_digest
            or called_event.previous_event_digest != trace.head_event_digest
        ):
            raise RuntimeError("WORKSPACE_DEPENDENCY_TOOL_AUDIT_BINDING_INVALID")
        return {
            "status": receipt.status,
            "evidence_class": receipt.evidence_class.value,
            "target_writes": 0,
            "formation_trace_ref": formation.trace_ref,
            "formation_trace_head_digest": trace.head_event_digest,
            "invocation_artifact_id": DEPENDENCY_TOOL_RECEIPT_ID,
            "invocation_artifact_digest": invocation_artifact.payload_digest,
            "called_event_artifact_id": DEPENDENCY_TOOL_CALLED_EVENT_ID,
            "called_event_artifact_digest": called_event_artifact.payload_digest,
            "invocation": invocation,
            "called_event": called_event.model_dump(mode="json"),
        }

    @_serialized
    def form_quote_with_dependency_evidence(
        self,
        request: TaskRequest | None = None,
        *,
        oac_activation_binding: Mapping[str, Any] | None = None,
        task_formation_decision_receipt: Mapping[str, Any] | None = None,
        context_envelope: Mapping[str, Any] | None = None,
        task_intake_candidate: Mapping[str, Any] | None = None,
        task_intake_approval: Mapping[str, Any] | None = None,
        task_intake_work_description: str | None = None,
    ) -> dict[str, Any]:
        """Atomically persist intake, OAC use, Quote v1, and Tool evidence."""

        if (task_intake_candidate is None) != (task_intake_approval is None):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_PAIR_REQUIRED")
        if (task_intake_candidate is None) != (task_intake_work_description is None):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_PAIR_REQUIRED")
        if (task_formation_decision_receipt is None) != (context_envelope is None):
            raise IntegrityError("WORKSPACE_OAC_EXECUTION_ROOTS_INCOMPLETE")
        if oac_activation_binding is not None and (
            task_formation_decision_receipt is None or context_envelope is None
        ):
            raise IntegrityError("WORKSPACE_OAC_EXECUTION_ROOTS_REQUIRED")
        if oac_activation_binding is None and (
            task_formation_decision_receipt is not None or context_envelope is not None
        ):
            raise IntegrityError("WORKSPACE_OAC_ACTIVATION_BINDING_REQUIRED")
        if (
            self.task_intake_required
            and task_intake_candidate is None
            and self._task_intake_run_record() is None
        ):
            raise IntegrityError("WORKSPACE_TASK_INTAKE_REQUIRED")

        selected, formation = self._select_formation_request(request)
        prepared_formation = None
        activation_consumption = None
        persisted_activation_consumption = self._oac_activation_consumption_record()
        competition_evidence = self._competition_evidence_record()
        if formation is None:
            if self.competition_mode == GOLDEN_COMPETITION_MODE:
                if task_formation_decision_receipt is not None and context_envelope is not None:
                    prepared_formation, competition_evidence = self._prepare_golden_competition(
                        selected,
                        task_formation_decision_receipt=task_formation_decision_receipt,
                        context_envelope=context_envelope,
                    )
                else:
                    prepared_formation, competition_evidence = self._prepare_golden_competition(
                        selected
                    )
            else:
                prepared_formation = self.formation.prepare_quote(
                    selected,
                    run_id=self.workflow_run_id,
                )
            formation = prepared_formation.task_receipt
            trace = self._prepared_formation_trace(prepared_formation)
            if oac_activation_binding is not None:
                activation_consumption = self._build_oac_activation_consumption_receipt(
                    binding_payload=oac_activation_binding,
                    selected=selected,
                    formation=formation,
                )
                if persisted_activation_consumption is not None:
                    self._require_same_oac_activation_consumption(
                        persisted_activation_consumption,
                        activation_consumption,
                    )
                if competition_evidence is not None:
                    lineage = competition_evidence.get("oac_agentteams_lineage")
                    if not isinstance(lineage, Mapping):
                        raise IntegrityError(
                            "WORKSPACE_OAC_AGENTTEAMS_LINEAGE_REQUIRED"
                        )
                    evidence_payload = {
                        key: value
                        for key, value in competition_evidence.items()
                        if key != "digest"
                    }
                    evidence_payload["oac_agentteams_lineage"] = {
                        **dict(lineage),
                        "activation_binding_digest": (
                            activation_consumption.activation_binding_digest
                        ),
                    }
                    competition_evidence = self._golden_compact_record(
                        evidence_payload
                    )
            elif persisted_activation_consumption is not None:
                raise IntegrityError("WORKSPACE_OAC_ACTIVATION_BINDING_REQUIRED")
        else:
            if self.competition_mode == GOLDEN_COMPETITION_MODE and competition_evidence is None:
                raise RuntimeError("WORKSPACE_GOLDEN_EVIDENCE_REQUIRED_FOR_EXISTING_FORMATION")
            trace = WorkTrace.model_validate(self.store.load_artifact(formation.trace_ref).payload)
            if oac_activation_binding is not None:
                activation_consumption = self._build_oac_activation_consumption_receipt(
                    binding_payload=oac_activation_binding,
                    selected=selected,
                    formation=formation,
                )
                if persisted_activation_consumption is None:
                    raise IntegrityError(
                        "WORKSPACE_OAC_ACTIVATION_RETROACTIVE_CONSUMPTION_DENIED"
                    )
                self._require_same_oac_activation_consumption(
                    persisted_activation_consumption,
                    activation_consumption,
                )
            elif persisted_activation_consumption is not None:
                raise IntegrityError("WORKSPACE_OAC_ACTIVATION_BINDING_REQUIRED")
        competition_run_id = (
            str(competition_evidence["run_id"])
            if prepared_formation is not None and competition_evidence is not None
            else None
        )
        persisted_task_intake = self._task_intake_run_record()
        prepared_task_intake = None
        prepared_work_description = None
        if task_intake_candidate is not None and task_intake_approval is not None:
            expected_task_intake = self._build_task_intake_run_summary(
                selected=selected,
                formation=formation,
                candidate_payload=task_intake_candidate,
                approval_payload=task_intake_approval,
                activation_consumption=activation_consumption,
            )
            if persisted_task_intake is not None:
                if persisted_task_intake.get("digest") != expected_task_intake.digest:
                    raise IntegrityError("WORKSPACE_TASK_INTAKE_RECEIPT_CONFLICT")
                expected_work_description = (
                    self._build_task_intake_work_description_record(
                        work_description=str(task_intake_work_description),
                        run_summary=expected_task_intake,
                    )
                )
                if not self.private_records.matches(
                    TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID,
                    expected_work_description.model_dump(mode="json"),
                ):
                    raise IntegrityError("WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_CONFLICT")
            elif prepared_formation is None:
                raise IntegrityError("WORKSPACE_TASK_INTAKE_RETROACTIVE_BINDING_DENIED")
            else:
                prepared_task_intake = expected_task_intake
                prepared_work_description = (
                    self._build_task_intake_work_description_record(
                        work_description=str(task_intake_work_description),
                        run_summary=expected_task_intake,
                    )
                )
        with self._competition_execution_scope(competition_run_id), self.store.transaction() as connection:
            authorization = current_authorization()
            if authorization is not None:
                authorization()
            if prepared_formation is not None:
                if activation_consumption is not None:
                    artifact_payload_digest = self.store.save_artifact(
                        connection,
                        OAC_ACTIVATION_CONSUMPTION_ARTIFACT_ID,
                        OAC_ACTIVATION_CONSUMPTION_MEDIA_TYPE,
                        activation_consumption.model_dump(mode="json"),
                    )
                    self.store.append_event(
                        connection,
                        "OAC_ADAPTER_ACTIVATION_CONSUMED",
                        {
                            "adaptation_run_id": activation_consumption.adaptation_run_id,
                            "execution_run_id": activation_consumption.execution_run_id,
                            "activation_binding_digest": (
                                activation_consumption.activation_binding_digest
                            ),
                            "adapter_capsule_digest": (
                                activation_consumption.adapter_capsule_digest
                            ),
                            "consumption_receipt_digest": activation_consumption.digest,
                            "artifact_payload_digest": artifact_payload_digest,
                            "task_request_digest": (
                                activation_consumption.task_request_digest
                            ),
                            "formation_receipt_digest": (
                                activation_consumption.formation_receipt_digest
                            ),
                            "quote_ref": activation_consumption.quote_ref,
                            "consumption_phase": activation_consumption.consumption_phase,
                            "canonical_write_authority": (
                                activation_consumption.canonical_write_authority
                            ),
                            "canonical_target_writes": 0,
                            "claim_ceiling": activation_consumption.claim_ceiling,
                        },
                    )
                formation = self.formation.commit_quote(
                    prepared_formation,
                    connection=connection,
                )
                if competition_evidence is not None:
                    competition_artifact_digest = self.store.save_artifact(
                        connection,
                        GOLDEN_COMPETITION_ARTIFACT_ID,
                        GOLDEN_COMPETITION_MEDIA_TYPE,
                        competition_evidence,
                    )
                    self.store.append_event(
                        connection,
                        GOLDEN_COMPETITION_EVENT_TYPE,
                        {
                            "run_id": competition_evidence["run_id"],
                            "correlation_id": competition_evidence["correlation_id"],
                            "competition_artifact_id": (GOLDEN_COMPETITION_ARTIFACT_ID),
                            "competition_artifact_digest": (competition_artifact_digest),
                            "summary_digest": competition_evidence["summary_digest"],
                            "prepared_formation_digest": prepared_formation.digest,
                            "prepared_quote_ref": prepared_formation.deliverable.ref,
                            "candidate_target_writes": 0,
                            "agent_target_writes": 0,
                            "reviewer_target_writes": 0,
                            "tool_target_writes": 0,
                            "skill_target_writes": 0,
                            "canonical_write_owner": ("DETERMINISTIC_CONTROL_PLANE"),
                            **(
                                {
                                    "oac_activation_binding_digest": (
                                        competition_evidence["oac_agentteams_lineage"][
                                            "activation_binding_digest"
                                        ]
                                    ),
                                    "task_formation_decision_receipt_digest": (
                                        competition_evidence["oac_agentteams_lineage"][
                                            "task_formation_decision_receipt_digest"
                                        ]
                                    ),
                                    "task_agent_context_envelope_digest": (
                                        competition_evidence["oac_agentteams_lineage"][
                                            "task_agent_context_envelope_digest"
                                        ]
                                    ),
                                    "agentteams_execution_plan_digest": (
                                        competition_evidence["oac_agentteams_lineage"][
                                            "agentteams_execution_plan_digest"
                                        ]
                                    ),
                                    "topology_match": competition_evidence[
                                        "oac_agentteams_lineage"
                                    ].get("topology_match"),
                                }
                                if isinstance(
                                    competition_evidence.get("oac_agentteams_lineage"),
                                    Mapping,
                                )
                                else {}
                            ),
                        },
                    )
                if prepared_task_intake is not None:
                    intake_payload_digest = self.store.save_artifact(
                        connection,
                        TASK_INTAKE_RUN_ARTIFACT_ID,
                        TASK_INTAKE_RUN_MEDIA_TYPE,
                        prepared_task_intake.model_dump(mode="json"),
                    )
                    self.store.append_event(
                        connection,
                        TASK_INTAKE_BOUND_EVENT_TYPE,
                        {
                            "run_id": prepared_task_intake.run_id,
                            "workspace_instance_nonce": (
                                prepared_task_intake.workspace_instance_nonce
                            ),
                            "task_intake_receipt_digest": prepared_task_intake.digest,
                            "artifact_id": TASK_INTAKE_RUN_ARTIFACT_ID,
                            "artifact_payload_digest": intake_payload_digest,
                            "prompt_digest": prepared_task_intake.prompt_digest,
                            "candidate_digest": prepared_task_intake.candidate_digest,
                            "approval_digest": prepared_task_intake.approval_digest,
                            "task_digest": prepared_task_intake.task_digest,
                            "formation_receipt_digest": (
                                prepared_task_intake.formation_receipt_digest
                            ),
                            "oac_activation_binding_digest": (
                                prepared_task_intake.oac_activation_binding_digest
                            ),
                            "canonical_target_writes": 0,
                        },
                    )
                    if prepared_work_description is None:  # pragma: no cover
                        raise RuntimeError(
                            "WORKSPACE_TASK_INTAKE_WORK_DESCRIPTION_PREPARE_MISSING"
                        )
                    self.private_records.write(
                        connection,
                        record_id=TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID,
                        scope_ref=prepared_work_description.run_id,
                        owner_id=prepared_work_description.actor_id,
                        payload=prepared_work_description.model_dump(mode="json"),
                    )
            else:
                persisted_formation = self.store.get_idempotent(
                    selected.idempotency_key,
                    selected.digest,
                    connection=connection,
                )
                if persisted_formation is None:  # pragma: no cover - immutable ledger
                    raise RuntimeError("WORKSPACE_FORMATION_IDEMPOTENCY_MISSING")
                formation = TaskReceipt.model_validate(persisted_formation)
            # The uncommitted graph is now readable through this SQLite handle;
            # tool preparation remains side-effect free and its commit stays in
            # the same transaction as formation and ToolCalledEvent.
            tool, prepared_tool = self._invoke_dependency_evidence_tool(formation)
            invocation = tool.commit(prepared_tool, connection=connection)
            called_event = self._tool_called_event(
                formation=formation,
                trace=trace,
                invocation=invocation,
            )
            self.store.save_artifact(
                connection,
                called_event.event_id,
                TOOL_CALLED_EVENT_MEDIA_TYPE,
                called_event.model_dump(mode="json"),
            )
        tool_evidence = self._dependency_evidence_tool_record()
        if tool_evidence["status"] != "SUCCEEDED":  # pragma: no cover - postcondition
            raise RuntimeError("WORKSPACE_DEPENDENCY_TOOL_POSTCONDITION_FAILED")
        result = {
            "receipt": formation,
            "tool_invocation": invocation,
            "tool_called_event": called_event,
            "formation_run_id": trace.run_id,
            "tool_evidence": tool_evidence,
            "oac_activation": self.oac_activation_state(),
            "state": self.state(),
        }
        task_intake_record = self._task_intake_run_record()
        if task_intake_record is not None:
            result["task_intake"] = task_intake_record
        return result

    def current_quote(self):
        return self.store.get_object(self.quote_object_id)

    def current_graph_pointer(self):
        return self.store.get_object(self.graph_pointer_id)

    def _preview_artifact_id(self, kind: str) -> str:
        from orgrebase.workspace.change_recovery import round_suffix
        return f"workspace-preview:{kind}{round_suffix(self, kind)}"

    @staticmethod
    def _approval_artifact_id(kind: str) -> str:
        slug = kind
        return f"approval:workspace:workspace-{slug}@r1"

    @staticmethod
    def _approval_binding_artifact_id(kind: str) -> str:
        slug = kind
        return f"approval-binding:workspace:workspace-{slug}@r1"

    def _review_gate_artifact_id(self, kind: str) -> str:
        from orgrebase.workspace.change_recovery import round_suffix
        return f"review-gate:workspace:workspace-{kind}{round_suffix(self, kind)}"

    @staticmethod
    def _outcome_artifact_id(kind: str) -> str:
        return f"workspace-outcome:{kind}@r1"

    @staticmethod
    def _epoch_ms_timestamp(epoch_ms: int) -> str:
        return (
            datetime.fromtimestamp(epoch_ms / 1000, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _wall_clock_epoch_ms(self) -> int:
        try:
            observed = float(self._wall_clock())
        except (TypeError, ValueError) as exc:
            raise RuntimeError("WORKSPACE_WALL_CLOCK_INVALID") from exc
        if not math.isfinite(observed):
            raise RuntimeError("WORKSPACE_WALL_CLOCK_INVALID")
        return math.floor(observed * 1000)

    def _review_gate_record(self, kind: str) -> dict[str, Any] | None:
        artifact_id = self._review_gate_artifact_id(kind)
        try:
            stored = self.store.load_artifact(
                artifact_id,
                WORKSPACE_REVIEW_GATE_MEDIA_TYPE,
            )
        except KeyError:
            return None
        payload = stored.payload
        claimed_digest = payload.get("digest")
        if not isinstance(claimed_digest, str):
            raise IntegrityError("WORKSPACE_REVIEW_GATE_DIGEST_MISSING")
        body = {key: value for key, value in payload.items() if key != "digest"}
        if sha256_digest(body) != claimed_digest:
            raise IntegrityError("WORKSPACE_REVIEW_GATE_DIGEST_MISMATCH")
        return {
            "artifact_id": artifact_id,
            "artifact_digest": stored.payload_digest,
            "gate": payload,
        }

    def _new_review_gate(
        self,
        *,
        kind: str,
        bundle: WorkspacePreviewBundle,
        preview_artifact_digest: str,
    ) -> dict[str, Any]:
        # The compatibility profile remains byte-stable across fresh runs.  A
        # live wall clock is only part of the contract when a non-zero review
        # duration is explicitly enabled.
        previewed_at_epoch_ms = self._wall_clock_epoch_ms() if self.review_duration_ms > 0 else 0
        not_before_epoch_ms = previewed_at_epoch_ms + self.review_duration_ms
        body = {
            "schema_version": "orgrebase.workspace-review-gate.v1",
            "kind": kind,
            "preview_artifact_ref": self._preview_artifact_id(kind),
            "preview_artifact_digest": preview_artifact_digest,
            "preview_digest": bundle.preview.digest,
            "owner_id": bundle.change_spec.owner_id,
            "workflow_run_id": bundle.run_envelope.run_id,
            "run_nonce": bundle.run_envelope.nonce,
            "pack_digest": self.deployment_binding.get("pack_digest"),
            "profile_digest": self.profile_digest,
            "review_duration_ms": self.review_duration_ms,
            "previewed_at": self._epoch_ms_timestamp(previewed_at_epoch_ms),
            "previewed_at_epoch_ms": previewed_at_epoch_ms,
            "not_before": self._epoch_ms_timestamp(not_before_epoch_ms),
            "not_before_epoch_ms": not_before_epoch_ms,
            "approval_identity_mode": self.approval_identity_mode,
            "identity_claim_boundary": identity_claim_boundary(self.approval_identity_mode),
            "canonical_target_writes": 0,
        }
        return {**body, "digest": sha256_digest(body)}

    def _require_review_gate_binding(
        self,
        *,
        kind: str,
        bundle: WorkspacePreviewBundle,
        preview_artifact_digest: str,
    ) -> dict[str, Any]:
        record = self._review_gate_record(kind)
        if record is None:
            raise RuntimeError("WORKSPACE_REVIEW_GATE_REQUIRED")
        gate = record["gate"]
        if (
            gate.get("kind") != kind
            or gate.get("preview_artifact_ref") != self._preview_artifact_id(kind)
            or gate.get("preview_artifact_digest") != preview_artifact_digest
            or gate.get("preview_digest") != bundle.preview.digest
            or gate.get("owner_id") != bundle.change_spec.owner_id
            or gate.get("workflow_run_id") != bundle.run_envelope.run_id
            or gate.get("run_nonce") != bundle.run_envelope.nonce
            or gate.get("pack_digest") != self.deployment_binding.get("pack_digest")
            or gate.get("profile_digest") != self.profile_digest
            or gate.get("approval_identity_mode") != self.approval_identity_mode
        ):
            raise RuntimeError("WORKSPACE_REVIEW_GATE_BINDING_MISMATCH")
        if not isinstance(gate.get("not_before_epoch_ms"), int):
            raise IntegrityError("WORKSPACE_REVIEW_GATE_NOT_BEFORE_INVALID")
        return record

    def _formation_record(self) -> TaskReceipt | None:
        try:
            stored = self.store.load_artifact(self.task_receipt_id)
        except KeyError:
            return None
        return TaskReceipt.model_validate(stored.payload)

    def _preview_record(self, kind: str) -> dict[str, Any] | None:
        artifact_id = self._preview_artifact_id(kind)
        try:
            stored = self.store.load_artifact(artifact_id, WORKSPACE_PREVIEW_MEDIA_TYPE)
        except KeyError:
            return None
        return self._preview_view(kind, stored)

    def _preview_view(self, kind: str, stored: StoredArtifact) -> dict[str, Any]:
        bundle = WorkspacePreviewBundle.model_validate(stored.payload)
        review_gate = self._review_gate_record(kind)
        from orgrebase.workspace.change_agentteams import verified_native_bundle
        native_execution = verified_native_bundle(self, bundle)
        return {
            "kind": kind,
            "artifact_id": stored.artifact_id,
            "artifact_digest": stored.payload_digest,
            "preview_digest": bundle.preview.digest,
            "bundle": bundle.model_dump(mode="json"),
            "review_gate": review_gate,
            **({"pricing_comparison": bundle.pricing_comparison.model_dump(mode="json")}
               if bundle.pricing_comparison is not None else {}),
            **({"native_execution": native_execution} if native_execution is not None else {}),
        }

    def _approval_record(self, kind: str) -> dict[str, Any] | None:
        artifact_id = self._approval_artifact_id(kind)
        try:
            stored = self.store.load_artifact(artifact_id, WORKSPACE_APPROVAL_MEDIA_TYPE)
        except KeyError:
            return None
        return self._approval_view(kind, stored)

    def _approval_view(self, kind: str, stored: StoredArtifact) -> dict[str, Any]:
        approval = Approval.model_validate(stored.payload)
        binding_artifact_id = self._approval_binding_artifact_id(kind)
        binding = None
        with suppress(KeyError):
            stored_binding = self.store.load_artifact(
                binding_artifact_id,
                WORKSPACE_APPROVAL_BINDING_MEDIA_TYPE,
            )
            binding = WorkspaceApprovalBinding.model_validate(stored_binding.payload)
        review_evidence = None
        authority_commitment = None
        for event in self.changes.journal("WORKSPACE_CHANGE_APPROVED", subject_key=kind, limit=1, descending=True):
            payload = event["payload"]
            if event["event_type"] != "WORKSPACE_CHANGE_APPROVED" or payload.get("kind") != kind:
                continue
            authority_commitment = payload.get("authority_digest")
            observed_epoch_ms = payload.get("approval_observed_at_epoch_ms")
            not_before_epoch_ms = payload.get("review_not_before_epoch_ms")
            if observed_epoch_ms is not None or not_before_epoch_ms is not None:
                if (
                    not isinstance(observed_epoch_ms, int)
                    or not isinstance(not_before_epoch_ms, int)
                    or observed_epoch_ms < not_before_epoch_ms
                    or payload.get("review_wait_satisfied") is not True
                ):
                    raise RuntimeError("WORKSPACE_APPROVAL_REVIEW_EVIDENCE_INVALID")
                review_evidence = {
                    "approval_observed_at": payload["approval_observed_at"],
                    "approval_observed_at_epoch_ms": observed_epoch_ms,
                    "review_not_before": payload["review_not_before"],
                    "review_not_before_epoch_ms": not_before_epoch_ms,
                    "review_wait_satisfied": True,
                    "review_duration_ms": payload["review_duration_ms"],
                    "event_sequence_no": event["sequence_no"],
                    "event_digest": event["event_digest"],
                }
            break
        from orgrebase.workspace.approval_authority import approval_authority_evidence
        authority = approval_authority_evidence(self, kind, approval)
        if authority is not None and authority_commitment != authority["provenance_digest"]:
            raise IntegrityError("WORKSPACE_APPROVAL_AUTHORITY_EVENT_MISMATCH")
        return {
            "kind": kind,
            "artifact_id": stored.artifact_id,
            "artifact_digest": stored.payload_digest,
            "approval_digest": approval.digest,
            "approval": approval.model_dump(mode="json"),
            "binding_artifact_id": binding_artifact_id if binding is not None else None,
            "binding_digest": binding.digest if binding is not None else None,
            "binding": binding.model_dump(mode="json") if binding is not None else None,
            "approval_review_evidence": review_evidence,
            **({"authority": authority} if authority is not None else {}),
        }

    def _require_approval_binding(
        self,
        *,
        kind: str,
        bundle: WorkspacePreviewBundle,
        approval: Approval,
        require_current_predecessor: bool,
    ) -> WorkspaceApprovalBinding:
        record = self._approval_record(kind)
        if record is None or record["binding"] is None:
            raise RuntimeError("WORKSPACE_APPROVAL_BINDING_REQUIRED")
        binding = WorkspaceApprovalBinding.model_validate(record["binding"])
        if (
            binding.approval_ref != approval.id
            or binding.approval_digest != approval.digest
            or binding.change_kind != kind
            or binding.workflow_run_id != bundle.run_envelope.run_id
            or binding.run_nonce != bundle.run_envelope.nonce
            or (
                self.workflow_run_id is not None and binding.workflow_run_id != self.effective_workflow_run_id
            )
            or (self.workflow_run_id is not None and binding.run_nonce != self.workflow_run_nonce)
            or binding.pack_digest != self.deployment_binding.get("pack_digest")
            or binding.profile_digest != self.profile_digest
            or binding.change_set_digest != bundle.change_set.digest
            or binding.preview_digest != bundle.preview.digest
            or binding.minimal_rebase_certificate_digest != bundle.minimal_rebase_certificate.digest
            or binding.owner_id != bundle.change_spec.owner_id
        ):
            raise RuntimeError("WORKSPACE_APPROVAL_AUTHORITY_BINDING_MISMATCH")
        if require_current_predecessor:
            predecessor = self.current_quote()
            if binding.predecessor_ref != predecessor.ref or binding.predecessor_digest != predecessor.digest:
                raise RuntimeError("WORKSPACE_APPROVAL_PREDECESSOR_STALE")
        from orgrebase.workspace.approval_authority import verify_approval_authority
        verify_approval_authority(self, kind, approval, current=require_current_predecessor)
        return binding

    def _outcome_record(self, kind: str) -> dict[str, Any] | None:
        artifact_id = self._outcome_artifact_id(kind)
        try:
            stored = self.store.load_artifact(artifact_id, WORKSPACE_OUTCOME_MEDIA_TYPE)
        except KeyError:
            return None
        return self._outcome_view(kind, stored)

    @staticmethod
    def _outcome_view(kind: str, stored: StoredArtifact) -> dict[str, Any]:
        return {
            "kind": kind,
            "artifact_id": stored.artifact_id,
            "artifact_digest": stored.payload_digest,
            "outcome": stored.payload,
        }

    def _change_records(self, event_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        """Read the selected history inside the caller's consistent snapshot."""
        records: dict[str, dict[str, Any]] = {event_id: {} for event_id in event_ids}
        for name, identity, media_type, view in (
            ("preview", self._preview_artifact_id, WORKSPACE_PREVIEW_MEDIA_TYPE, self._preview_view),
            ("approval", self._approval_artifact_id, WORKSPACE_APPROVAL_MEDIA_TYPE, self._approval_view),
            ("outcome", self._outcome_artifact_id, WORKSPACE_OUTCOME_MEDIA_TYPE, self._outcome_view),
        ):
            artifacts = self.store.load_artifacts(
                [identity(event_id) for event_id in event_ids], expected_media_type=media_type,
            )
            for event_id in event_ids:
                stored = artifacts.get(identity(event_id))
                records[event_id][name] = view(event_id, stored) if stored is not None else None
        return records

    @staticmethod
    def _stage(quote: dict[str, Any] | None, active_status: str | None) -> str:
        if quote is None:
            return "EMPTY"
        return active_status if active_status in {"PREVIEWED", "APPROVED", "RECOVERY_REQUIRED"} else "CURRENT"

    @staticmethod
    def _actions(stage: str, active_event: ChangeEvent | None, active_status: str | None) -> dict[str, Any]:
        event = active_event if stage != "EMPTY" else None
        event_id = event.event_id if event is not None else None
        operation = None
        if event is not None:
            operation = {"RECEIVED": "preview", "PREVIEWED": "approve", "APPROVED": "apply", "RECOVERY_REQUIRED": "apply"}.get(
                active_status or "")
        return {
            "can_form": stage == "EMPTY",
            "can_preview": operation == "preview",
            "can_approve": operation == "approve",
            "can_apply": operation == "apply",
            "can_reset": True,
            "can_export_quote": stage != "EMPTY",
            "next_operation": operation,
            "next_kind": event_id,
            "next_event_id": event_id,
            "owner_id": event.owner_id if event is not None else None,
        }

    def _human_interrupts_view(
        self,
        changes: Mapping[str, Mapping[str, Any] | None],
    ) -> dict[str, Any]:
        """Project the persisted owner gate without creating a second state machine."""

        by_change: dict[str, dict[str, Any]] = {}
        active_kinds: list[str] = []
        for kind in changes:
            change = changes.get(kind)
            if not isinstance(change, Mapping):
                continue
            preview = change.get("preview")
            if not isinstance(preview, Mapping):
                continue
            approval = change.get("approval")
            outcome = change.get("outcome")
            gate_record = preview.get("review_gate")
            gate = (
                gate_record.get("gate")
                if isinstance(gate_record, Mapping)
                and isinstance(gate_record.get("gate"), Mapping)
                else None
            )

            if isinstance(outcome, Mapping):
                status = "COMPLETED"
            elif isinstance(approval, Mapping):
                status = "RESUMED"
            elif gate is not None:
                status = "WAITING_HUMAN"
                active_kinds.append(kind)
            else:
                status = "BLOCKED_MISSING_PERSISTED_GATE"

            resume_action = None
            if status == "WAITING_HUMAN":
                header_identity = self.approval_identity_mode == CONTROLLED_LOCAL_HEADER_IDENTITY
                resume_action = {
                    "operation": "APPROVE_CHANGE",
                    "method": "POST",
                    "endpoint": f"/api/workspace/approve/{kind}",
                    "required_body_fields": (
                        ["preview_digest"]
                        if self.approval_identity_mode != BODY_ACTOR_COMPATIBILITY
                        else ["actor_id", "preview_digest"]
                    ),
                    "required_header_fields": (
                        ["X-OrgRebase-Actor"] if header_identity else []
                    ),
                }

            next_action = None
            if status == "RESUMED" and isinstance(approval, Mapping):
                approval_digest = approval.get("approval_digest")
                next_action = {
                    "operation": "APPLY_APPROVED_CHANGE",
                    "method": "POST",
                    "endpoint": f"/api/workspace/apply/{kind}",
                    "binding_field": "approval_digest",
                    "binding_value": approval_digest,
                }

            by_change[kind] = {
                "status": status,
                "owner_id": gate.get("owner_id") if gate is not None else None,
                "workflow_run_id": (
                    gate.get("workflow_run_id") if gate is not None else None
                ),
                "gate_artifact_ref": (
                    gate_record.get("artifact_id")
                    if isinstance(gate_record, Mapping)
                    else None
                ),
                "gate_artifact_digest": (
                    gate_record.get("artifact_digest")
                    if isinstance(gate_record, Mapping)
                    else None
                ),
                "preview_artifact_digest": preview.get("artifact_digest"),
                "preview_digest": preview.get("preview_digest"),
                "review_not_before": (
                    gate.get("not_before") if gate is not None else None
                ),
                "resume_action": resume_action,
                "next_action": next_action,
                "expiry": {
                    "status": "NOT_CONFIGURED",
                    "expires_at": None,
                },
                "reassignment": "REQUIRES_NEW_OWNER_BOUND_PREVIEW",
                "restart_recoverable": self.store_path != ":memory:",
                "outcome_artifact_digest": (
                    outcome.get("artifact_digest")
                    if isinstance(outcome, Mapping)
                    else None
                ),
                "read_model_target_writes": 0,
            }
        return {
            "schema_version": "orgrebase.workspace-human-interrupts-view.v1",
            "active_kind": active_kinds[0] if len(active_kinds) == 1 else None,
            "active_kinds": active_kinds,
            "by_change": by_change,
            "read_model_target_writes": 0,
            "claim_boundary": (
                "DERIVED_FROM_PERSISTED_GATE_APPROVAL_AND_OUTCOME;"
                "RESUME_DOES_NOT_GRANT_APPLY_AUTHORITY"
            ),
        }

    def _agentteams_operations_view(
        self,
        *,
        changes: Mapping[str, Mapping[str, Any] | None],
        competition_evidence: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Aggregate exact AT participation for a read-only operations surface."""

        collaboration = (
            competition_evidence.get("agent_collaboration", {})
            if isinstance(competition_evidence, Mapping)
            else {}
        )
        plan = (
            collaboration.get("orchestration_plan")
            if isinstance(collaboration, Mapping)
            else None
        )
        agent_runs = (
            collaboration.get("agent_runs")
            if isinstance(collaboration, Mapping)
            else None
        )
        authority = (
            collaboration.get("authority", {})
            if isinstance(collaboration, Mapping)
            else {}
        )
        action_count = (
            competition_evidence.get("agentteams_action_count")
            if isinstance(competition_evidence, Mapping)
            else None
        )
        project_terminal_state = (
            competition_evidence.get("project_terminal_state")
            if isinstance(competition_evidence, Mapping)
            else None
        )
        native_observed = bool(
            isinstance(competition_evidence, Mapping)
            and competition_evidence.get("status") == "PASS"
            and type(action_count) is int
            and action_count > 0
            and isinstance(project_terminal_state, str)
            and project_terminal_state.lower() == "completed"
            and isinstance(plan, Mapping)
            and isinstance(agent_runs, list)
            and bool(agent_runs)
            and isinstance(authority, Mapping)
            and authority.get("agent_target_writes") == 0
        )
        formation_status = (
            "CONTROLLED_LOCAL_NATIVE_OBSERVED"
            if native_observed
            else "UNQUALIFIED_EVIDENCE"
            if competition_evidence is not None
            else "NOT_RUN"
        )
        formation_taskflow = {
            "scope": "INITIAL_QUOTE_FORMATION_ONLY",
            "participation_status": formation_status,
            "native_taskflow_observed": native_observed,
            "live_distributed_observed": False,
            "run_id": (
                competition_evidence.get("run_id")
                if isinstance(competition_evidence, Mapping)
                else None
            ),
            "evidence_class": (
                competition_evidence.get("evidence_class")
                if isinstance(competition_evidence, Mapping)
                else None
            ),
            "summary_digest": (
                competition_evidence.get("summary_digest")
                if isinstance(competition_evidence, Mapping)
                else None
            ),
            "action_count": action_count if type(action_count) is int else None,
            "project_terminal_state": project_terminal_state,
            "agentteams_canonical_target_writes": (
                authority.get("agent_target_writes")
                if isinstance(authority, Mapping)
                else None
            ),
        }

        change_set_advisories: dict[str, dict[str, Any]] = {}
        for kind in changes:
            change = changes.get(kind)
            preview = change.get("preview") if isinstance(change, Mapping) else None
            if not isinstance(preview, Mapping):
                continue
            bundle = preview.get("bundle")
            advisory = (
                bundle.get("advisory") if isinstance(bundle, Mapping) else None
            )
            advisory_plan = (
                advisory.get("orchestration_plan")
                if isinstance(advisory, Mapping)
                else None
            )
            coordination = (
                advisory.get("coordination_receipt")
                if isinstance(advisory, Mapping)
                else None
            )
            ingestion = (
                advisory.get("ingestion_receipt")
                if isinstance(advisory, Mapping)
                else None
            )
            runs = (
                advisory.get("agent_runs")
                if isinstance(advisory, Mapping)
                else None
            )
            source_class = (
                ingestion.get("source_evidence_class")
                if isinstance(ingestion, Mapping)
                else None
            )
            target_writes = (
                ingestion.get("target_writes")
                if isinstance(ingestion, Mapping)
                else None
            )
            model_receipts = [
                handoff.get("payload", {}).get("model_advisory", {}).get("receipt", {})
                for handoff in advisory.get("handoffs", [])
                if isinstance(handoff, Mapping) and isinstance(handoff.get("payload"), Mapping)
                and isinstance(handoff["payload"].get("model_advisory"), Mapping)
            ] if isinstance(advisory, Mapping) else []
            model_candidates = bool(
                source_class == "LOCAL_REAL_TOOL" and isinstance(runs, list)
                and len(model_receipts) == len(runs) - 1 and model_receipts
                and all(isinstance(receipt, Mapping) and receipt.get("status") == "VALID"
                        and receipt.get("evidence_class") == "LIVE_MODEL"
                        and receipt.get("provider_request_id") and receipt.get("observed_model_id")
                        for receipt in model_receipts)
            )
            advisory_verified = bool(
                isinstance(advisory_plan, Mapping)
                and advisory_plan.get("evidence_class") == "LOCAL_DETERMINISTIC"
                and isinstance(coordination, Mapping)
                and coordination.get("status") == "PASS"
                and (source_class == "LOCAL_DETERMINISTIC" or model_candidates)
                and target_writes == 0
                and isinstance(runs, list)
                and bool(runs)
            )
            native_execution = preview.get("native_execution")
            native_change_observed = bool(
                advisory_verified and isinstance(native_execution, Mapping)
                and native_execution.get("native_agentteams_observed") is True
                and native_execution.get("status") == "COMPLETED"
            )
            change_set_advisories[kind] = {
                "scope": "CHANGE_PREVIEW_EXPLANATION_ONLY",
                "participation_status": (
                    ("CONTROLLED_LOCAL_NATIVE_OBSERVED" if native_change_observed
                     else "MODEL_CANDIDATE_ADVISORY" if model_candidates else "LOCAL_DETERMINISTIC_ADVISORY")
                    if advisory_verified
                    else "UNQUALIFIED_ADVISORY"
                ),
                "native_agentteams_observed": native_change_observed,
                "live_agentteams_observed": False,
                "evidence_class": source_class,
                "run_id": (
                    bundle.get("run_envelope", {}).get("run_id")
                    if isinstance(bundle, Mapping)
                    and isinstance(bundle.get("run_envelope"), Mapping)
                    else None
                ),
                "plan_digest": (
                    advisory_plan.get("digest")
                    if isinstance(advisory_plan, Mapping)
                    else None
                ),
                "coordination_receipt_digest": (
                    coordination.get("digest")
                    if isinstance(coordination, Mapping)
                    else None
                ),
                "role_projection_count": len(runs) if isinstance(runs, list) else 0,
                "skill_invocation_status": "NOT_OBSERVED",
                "tool_invocation_status": "NOT_OBSERVED",
                "target_writes": target_writes,
                **({"native_execution": native_execution} if native_change_observed else {}),
            }

        return {
            "schema_version": "orgrebase.agentteams-operations-view.v1",
            "surface_contract": {
                "customer_surface": "ORGREBASE_WORKSPACE",
                "agentteams_element_role": "BACK_OFFICE_TASK_OPERATIONS_AND_EVIDENCE",
                "element_embedding": "NOT_USED",
                "read_model_endpoint": "/api/workspace/agentteams-operations",
                "element_connection": "EXTERNAL_CONFIGURATION_REQUIRED",
            },
            "authority": {
                "agentteams_authority": "TASK_LIFECYCLE_AND_CANDIDATES_ONLY",
                "candidate_admission_authority": "ORGREBASE_CONTROL_PLANE",
                "human_approval_authority": "EXACT_DOMAIN_OWNER",
                "canonical_authority": "ORGREBASE_STATESTORE_AND_REBASE_WORKFLOW",
            },
            "formation_taskflow": formation_taskflow,
            "change_set_advisories": change_set_advisories,
            "read_model_target_writes": 0,
            "claim_boundary": (
                "ONLY_VERIFIED_CHANGE_TASKFLOW_RECEIPTS_ESTABLISH_CONTROLLED_LOCAL_NATIVE_EXECUTION;"
                "CANDIDATES_AND_TASK_COMPLETION_GRANT_NO_APPROVAL_OR_APPLY_AUTHORITY"
            ),
        }

    def _execution_activity(
        self,
        *,
        formation: dict[str, Any] | None,
        dependency_tool: dict[str, Any],
        changes: dict[str, dict[str, Any] | None],
        competition_evidence: dict[str, Any] | None = None,
        task_intake: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project trusted runtime records into one read-only, run-scoped ledger."""

        rows: list[dict[str, Any]] = []

        def append(
            *,
            plane: str,
            actor_or_domain: str,
            action: str,
            status: str,
            evidence_class: str,
            permission: str,
            target_writes: int | None,
            receipt_digest: str | None,
            source_ref: str | None,
            tool_or_skill: str | None = None,
            occurred_at: str | None = None,
            observed_run_id: str | None = None,
        ) -> None:
            if self.workflow_run_id is not None and observed_run_id not in (
                None,
                self.effective_workflow_run_id,
            ):
                raise RuntimeError("WORKSPACE_EXECUTION_LEDGER_RUN_MISMATCH")
            rows.append(
                {
                    "sequence": len(rows) + 1,
                    "run_id": self.effective_workflow_run_id,
                    "source_run_id": observed_run_id or self.effective_workflow_run_id,
                    "plane": plane,
                    "actor_or_domain": actor_or_domain,
                    "action": action,
                    "tool_or_skill": tool_or_skill,
                    "status": status,
                    "evidence_class": evidence_class,
                    "permission": permission,
                    "target_writes": target_writes,
                    "receipt_digest": receipt_digest,
                    "source_ref": source_ref,
                    "occurred_at": occurred_at or "NOT_OBSERVED",
                }
            )

        append(
            plane="ADMISSION",
            actor_or_domain="system:pack-admission",
            action="ADMIT_PACK_PROFILE_SOURCE_AUTHORITY_CLOSURE",
            status="PASS",
            evidence_class="LOCAL_DETERMINISTIC",
            permission="READ_ONLY",
            target_writes=0,
            receipt_digest=self.source_admission.digest,
            source_ref=self.profile.ref,
        )
        if task_intake is not None:
            intake_run_id = str(task_intake["run_id"])
            append(
                plane="TASK_INTAKE",
                actor_or_domain=str(task_intake["actor_id"]),
                action="SUBMIT_WORK_DESCRIPTION_AS_CANDIDATE",
                status="CANDIDATE_BOUND",
                evidence_class="LOCAL_DETERMINISTIC",
                permission="CANDIDATE_ONLY",
                target_writes=0,
                receipt_digest=str(task_intake["candidate_digest"]),
                source_ref=str(task_intake["task_digest"]),
                observed_run_id=intake_run_id,
            )
            append(
                plane="HUMAN_CONFIRMATION",
                actor_or_domain=str(task_intake["actor_id"]),
                action="CONFIRM_EXACT_TASK_CANDIDATE",
                status="CONFIRMED_FOR_FORMATION_REQUEST",
                evidence_class="LOCAL_DETERMINISTIC",
                permission="FORMATION_ONLY",
                target_writes=0,
                receipt_digest=str(task_intake["approval_digest"]),
                source_ref=str(task_intake["candidate_digest"]),
                observed_run_id=intake_run_id,
            )
            append(
                plane="CONTROL_PLANE",
                actor_or_domain="orgrebase-control-plane",
                action="BIND_INTAKE_TO_FORMATION",
                status="FORMATION_COMPLETED",
                evidence_class="LOCAL_DETERMINISTIC",
                permission="CANONICAL_FORMATION_AUTHORITY",
                target_writes=0,
                receipt_digest=str(task_intake["digest"]),
                source_ref=str(task_intake["artifact_id"]),
                observed_run_id=intake_run_id,
            )
        if competition_evidence is not None:
            collaboration = competition_evidence.get("agent_collaboration", {})
            agent_runs = [
                item
                for item in collaboration.get("agent_runs", [])
                if isinstance(item, dict)
            ]
            semantic_reviewer_runs = [
                item for item in agent_runs if item.get("role") == "REVIEWER"
            ]
            if semantic_reviewer_runs:
                semantic_by_attempt = {
                    attempt: [
                        item
                        for item in semantic_reviewer_runs
                        if item.get("attempt") == attempt
                    ]
                    for attempt in (1, 2)
                }
                reviewer_task_ids = (
                    {
                        str(matches[0].get("task_id"))
                        for matches in semantic_by_attempt.values()
                    }
                    if all(
                        len(matches) == 1
                        and matches[0].get("task_id") is not None
                        for matches in semantic_by_attempt.values()
                    )
                    else set()
                )
                final_reviewer_runs = semantic_by_attempt[2]
            else:
                legacy_by_attempt = {
                    attempt: [
                        item
                        for item in agent_runs
                        if str(item.get("task_id", "")).endswith(
                            f"-reviewer-a{attempt}"
                        )
                    ]
                    for attempt in (1, 2)
                }
                reviewer_task_ids = (
                    {
                        str(matches[0].get("task_id"))
                        for matches in legacy_by_attempt.values()
                    }
                    if all(len(matches) == 1 for matches in legacy_by_attempt.values())
                    else set()
                )
                final_reviewer_runs = legacy_by_attempt[2]
            reviewer_actor = (
                str(final_reviewer_runs[0].get("agent_name"))
                if reviewer_task_ids
                and len(final_reviewer_runs) == 1
                and final_reviewer_runs[0].get("agent_name")
                else "reviewer-identity-not-observed"
            )
            for agent_run in agent_runs:
                if not isinstance(agent_run, dict):
                    continue
                append(
                    plane=(
                        "REVIEWER"
                        if str(agent_run.get("task_id")) in reviewer_task_ids
                        else "AGENTTEAMS"
                    ),
                    actor_or_domain=str(agent_run.get("agent_name", "controlled-local-agent")),
                    action="EXECUTE_NATIVE_TASKFLOW_CANDIDATE",
                    status=str(agent_run.get("status", "NOT_OBSERVED")),
                    evidence_class=str(
                        agent_run.get(
                            "evidence_class",
                            "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
                        )
                    ),
                    permission="CANDIDATE_ONLY",
                    target_writes=int(agent_run.get("target_writes", 0)),
                    receipt_digest=agent_run.get("digest"),
                    source_ref=agent_run.get("task_id"),
                    tool_or_skill=", ".join(
                        [
                            *[str(item) for item in agent_run.get("tool_versions", [])],
                            *[str(item) for item in agent_run.get("skill_versions", [])],
                        ]
                    )
                    or None,
                    observed_run_id=competition_evidence.get("run_id"),
                )
            reviewer = collaboration.get("reviewer", {})
            if isinstance(reviewer, dict):
                reviewer_model_provider = str(
                    reviewer.get("model_provider", "model-provider-not-observed")
                )
                reviewer_model_version = str(
                    reviewer.get("model_version", "model-version-not-observed")
                )
                append(
                    plane="REVIEWER",
                    actor_or_domain=reviewer_actor,
                    action="REPLAN_THEN_ACCEPT_EXACT_PROVENANCE",
                    status=str(reviewer.get("attempt_2", {}).get("verdict", "NOT_OBSERVED")),
                    evidence_class=str(reviewer.get("model_evidence_class", "NOT_OBSERVED")),
                    permission="CANDIDATE_ONLY",
                    target_writes=int(reviewer.get("target_writes", 0)),
                    receipt_digest=competition_evidence.get("summary_digest"),
                    source_ref="reviewer:attempt-2",
                    tool_or_skill=(
                        f"{reviewer_model_provider}:{reviewer_model_version} advisory"
                        " + deterministic verifier"
                    ),
                    observed_run_id=competition_evidence.get("run_id"),
                )
            for plane, key, action in (
                ("TOOL", "tool", "RECOVER_FINANCE_DEPENDENCY"),
                ("SKILL", "skill", "COMPOSE_REVIEWED_QUOTE_CANDIDATE"),
            ):
                record = collaboration.get(key, {})
                if not isinstance(record, dict):
                    continue
                append(
                    plane=plane,
                    actor_or_domain=("finance-steward" if key == "tool" else "gtm-steward"),
                    action=action,
                    status=str(record.get("status", record.get("action", "NOT_OBSERVED"))),
                    evidence_class=str(
                        record.get(
                            "evidence_class",
                            competition_evidence.get("evidence_class", "NOT_OBSERVED"),
                        )
                    ),
                    permission="CANDIDATE_ONLY",
                    target_writes=int(record.get("target_writes", 0)),
                    receipt_digest=record.get("receipt_digest"),
                    source_ref=str(record.get("operation", record.get("package_id", key))),
                    tool_or_skill=str(record.get("operation", record.get("package_id", key))),
                    observed_run_id=competition_evidence.get("run_id"),
                )
        if formation is not None:
            append(
                plane="FORMATION",
                actor_or_domain="system:workspace-formation",
                action="FORM_DOMAIN_COALITION_CONTEXT_AND_QUOTE_V1",
                status=str(formation.get("status", "COMPLETED")),
                evidence_class=(
                    str(competition_evidence.get("evidence_class"))
                    if competition_evidence is not None
                    else "LOCAL_DETERMINISTIC"
                ),
                permission="CANONICAL_WRITE",
                target_writes=None,
                receipt_digest=formation.get("digest"),
                source_ref=formation.get("id"),
                occurred_at=formation.get("committed_at"),
            )

        invocation = dependency_tool.get("invocation", {})
        tool_receipt = invocation.get("receipt", {}) if isinstance(invocation, dict) else {}
        if dependency_tool.get("status") not in (None, "NOT_RUN"):
            append(
                plane="TOOL",
                actor_or_domain=str(tool_receipt.get("actor_id", "gtm-steward")),
                action="READ_DEPENDENCY_EVIDENCE",
                status=str(dependency_tool.get("status")),
                evidence_class=str(dependency_tool.get("evidence_class", "LOCAL_REAL_TOOL")),
                permission="READ_ONLY",
                target_writes=int(dependency_tool.get("target_writes", 0)),
                receipt_digest=tool_receipt.get("digest"),
                source_ref=tool_receipt.get("id"),
                tool_or_skill=tool_receipt.get("tool_ref"),
                occurred_at=tool_receipt.get("completed_at"),
                observed_run_id=tool_receipt.get("workflow_run_id"),
            )

        for kind in changes:
            change = changes.get(kind, {})
            if not isinstance(change, dict):
                continue
            preview = change.get("preview")
            if isinstance(preview, dict):
                bundle = preview.get("bundle", {})
                advisory = bundle.get("advisory", {}) if isinstance(bundle, dict) else {}
                plan = advisory.get("orchestration_plan", {})
                tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
                task_by_id = {
                    task.get("id"): task for task in tasks if isinstance(task, dict) and task.get("id")
                }
                for agent_run in advisory.get("agent_runs", []):
                    if not isinstance(agent_run, dict):
                        continue
                    task = task_by_id.get(agent_run.get("task_id"), {})
                    tool_or_skill = ", ".join(
                        [
                            *[str(item) for item in agent_run.get("tool_versions", [])],
                            *[str(item) for item in agent_run.get("skill_versions", [])],
                        ]
                    )
                    append(
                        plane="ADVISORY",
                        actor_or_domain=str(agent_run.get("agent_name", "deterministic-domain-candidate")),
                        action=str(task.get("purpose", f"{kind.upper()}_ADVISORY")),
                        status=str(agent_run.get("status", "TRUSTED_COMPLETE")),
                        evidence_class=str(agent_run.get("evidence_class", "LOCAL_DETERMINISTIC")),
                        permission="CANDIDATE_ONLY",
                        target_writes=0,
                        receipt_digest=agent_run.get("digest"),
                        source_ref=agent_run.get("id"),
                        tool_or_skill=tool_or_skill or None,
                        observed_run_id=agent_run.get("workflow_run_id"),
                    )
                for handoff in advisory.get("handoffs", []):
                    if not isinstance(handoff, dict):
                        continue
                    payload = handoff.get("payload", {})
                    append(
                        plane="HANDOFF",
                        actor_or_domain=(
                            f"{handoff.get('from_agent', 'candidate')}"
                            f"→{handoff.get('to_agent', 'control-plane')}"
                        ),
                        action=str(payload.get("kind", f"{kind.upper()}_HANDOFF")),
                        status="CANDIDATE_DELIVERED",
                        evidence_class="LOCAL_DETERMINISTIC",
                        permission="CANDIDATE_ONLY",
                        target_writes=int(payload.get("target_writes", 0)),
                        receipt_digest=handoff.get("digest"),
                        source_ref=handoff.get("id"),
                        observed_run_id=handoff.get("workflow_run_id"),
                    )
                run_envelope = bundle.get("run_envelope", {})
                append(
                    plane="CONTROL",
                    actor_or_domain="system:impact-control",
                    action=f"LOCK_{kind.upper()}_PREVIEW",
                    status="LOCKED",
                    evidence_class=str(run_envelope.get("evidence_class", "LOCAL_DETERMINISTIC")),
                    permission="READ_ONLY",
                    target_writes=0,
                    receipt_digest=preview.get("preview_digest"),
                    source_ref=preview.get("artifact_id"),
                    observed_run_id=run_envelope.get("run_id"),
                )

            approval = change.get("approval")
            if isinstance(approval, dict):
                approval_record = approval.get("approval", {})
                binding = approval.get("binding", {})
                append(
                    plane="HUMAN",
                    actor_or_domain=str(approval_record.get("actor_id", "exact-domain-owner")),
                    action=f"APPROVE_{kind.upper()}_PREVIEW",
                    status="APPROVED",
                    evidence_class="LOCAL_EXPLICIT_WORKSPACE_APPROVAL",
                    permission="APPROVAL",
                    target_writes=int(binding.get("target_writes", 0)),
                    receipt_digest=approval.get("approval_digest"),
                    source_ref=approval.get("artifact_id"),
                    occurred_at=approval_record.get("approved_at"),
                    observed_run_id=binding.get("workflow_run_id"),
                )

            outcome = change.get("outcome")
            if isinstance(outcome, dict):
                outcome_record = outcome.get("outcome", {})
                base_receipt = outcome_record.get("rebase_receipt", {})
                workspace_receipt = outcome_record.get("workspace_rebase_receipt", {})
                quote = outcome_record.get("quote", {})
                append(
                    plane="CONTROL",
                    actor_or_domain="system:orgrebase-control-plane",
                    action=f"APPLY_{kind.upper()}_SELECTIVE_REBASE",
                    status=str(workspace_receipt.get("status", "COMPLETED")),
                    evidence_class="LOCAL_DETERMINISTIC_GOVERNED_APPLY",
                    permission="CANONICAL_WRITE",
                    target_writes=None,
                    receipt_digest=workspace_receipt.get("digest") or outcome.get("artifact_digest"),
                    source_ref=quote.get("ref")
                    or (
                        f"{quote.get('id')}@{quote.get('version')}"
                        if quote.get("id") and quote.get("version")
                        else outcome.get("artifact_id")
                    ),
                    occurred_at=workspace_receipt.get("committed_at"),
                    observed_run_id=base_receipt.get("workflow_run_id"),
                )

        body = {
            "schema_version": "orgrebase.workspace-execution-activity.v1",
            "run_id": self.effective_workflow_run_id,
            "row_count": len(rows),
            "read_model_target_writes": 0,
            "canonical_write_rows": sum(item["permission"] == "CANONICAL_WRITE" for item in rows),
            "rows": rows,
        }
        return {**body, "digest": sha256_digest(body)}

    def _enterprise_data_source_view(self) -> dict[str, Any]:
        """Expose only admitted, presentation-safe enterprise input projections."""

        runtime = self.runtime_configuration
        admitted_and_matched = (
            runtime is not None
            and self.source_admission.verdict == "ADMITTED"
            and self.runtime_projection.verdict == "MATCH"
        )
        values = []
        if admitted_and_matched:
            values = [
                {
                    "slot_id": item.slot_id,
                    "object_ref": item.object_ref,
                    "domain_id": item.domain_id,
                    "value": item.value,
                    "semantic_kind": item.semantic_kind.value,
                    "authority_ref": item.authority_ref,
                    "source_ref": f"{item.source_id}@{item.source_version}",
                    "sensitivity": item.sensitivity,
                }
                for item in sorted(
                    runtime.source_values.values(),
                    key=lambda candidate: candidate.slot_id,
                )
            ]
        task = self.profile.default_task
        components = [
            {
                "kind": component.kind.value,
                "completeness": component.completeness.value,
                "source_root_refs": list(component.source_root_refs),
                "declared_digest": component.declared_digest,
            }
            for component in self.profile.components
        ]
        status = "ADMITTED_AND_MATCHED" if admitted_and_matched else "UNOBSERVED"
        return {
            "status": status,
            "profile_ref": self.profile.ref,
            "pack_ref": (
                f"{runtime.pack_id}@{runtime.pack_revision}"
                if runtime is not None
                else None
            ),
            "pack_digest": runtime.pack_digest if runtime is not None else None,
            "component_count": len(components),
            "components": components,
            "task": {
                "task_ref": task.id,
                "actor_id": task.actor_id,
                "purpose": task.purpose,
                "deliverable_kind": task.deliverable_kind,
                "customer_id": task.customer_id,
                "template_ref": task.template_ref,
                "input_values": {
                    item.key: item.value for item in task.input_values
                },
            },
            "values": values,
        }

    def _enterprise_data_oac_view(
        self,
        oac_activation: Mapping[str, Any],
        competition_evidence: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        execution_lineage = (
            competition_evidence.get("oac_agentteams_lineage")
            if isinstance(competition_evidence, Mapping)
            else None
        )
        exact_execution_bound = bool(
            isinstance(execution_lineage, Mapping)
            and execution_lineage.get("status")
            == "OAC_BOUND_EXECUTION_PLAN_REALIZED"
            and execution_lineage.get("activation_binding_digest")
            == oac_activation.get("activation_binding_digest")
            and execution_lineage.get("topology_match") is True
        )
        lineage_projection = {
            "execution_binding_status": (
                "OAC_BOUND_EXECUTION_PLAN_REALIZED"
                if exact_execution_bound
                else "NOT_OBSERVED"
            ),
            "selected_domain_ids": (
                list(execution_lineage.get("planned_domain_ids", ()))
                if exact_execution_bound
                else []
            ),
            "actual_agentteams_domain_ids": (
                list(execution_lineage.get("actual_agentteams_domain_ids", ()))
                if exact_execution_bound
                else []
            ),
            "topology_match": (
                execution_lineage.get("topology_match")
                if exact_execution_bound
                else None
            ),
            "context_freshness_basis": (
                execution_lineage.get("context_freshness_basis")
                if exact_execution_bound
                else None
            ),
        }
        admission_by_kind = {
            item.component_kind.value: item
            for item in self.source_admission.component_admissions
        }
        projection_by_kind = {
            item.component_kind.value: item
            for item in self.runtime_projection.observations
        }
        bindings = []
        for component in self.profile.components:
            kind = component.kind.value
            admission = admission_by_kind.get(kind)
            projection = projection_by_kind.get(kind)
            if admission is None or projection is None:
                return {
                    "source_admission_verdict": "UNOBSERVED",
                    "runtime_projection_verdict": "UNOBSERVED",
                    "component_bindings": [],
                    "canonical_target_writes": 0,
                    "activation_status": str(
                        oac_activation.get("status", "NOT_USED_IN_THIS_RUN")
                    ),
                    "adaptation_run_id": oac_activation.get("adaptation_run_id"),
                    "activation_binding_digest": oac_activation.get(
                        "activation_binding_digest"
                    ),
                    "adapter_capsule_digest": oac_activation.get(
                        "adapter_capsule_digest"
                    ),
                    "consumption_receipt_digest": oac_activation.get(
                        "consumption_receipt_digest"
                    ),
                    "binding_effect": oac_activation.get("binding_effect"),
                    "formation_authority": "ORGREBASE_CONTROL_PLANE",
                    **lineage_projection,
                    "claim_boundary": (
                        "OAC_ADMISSION_AND_ACTIVATION_ARE_FORMATION_PRECONDITIONS_"
                        "NOT_OAC_AGENTIC_CONTEXT_OR_SHADOW_EXECUTION"
                    ),
                }
            bindings.append(
                {
                    "kind": kind,
                    "source_admission_verdict": admission.verdict,
                    "runtime_projection_status": projection.status,
                    "source_root_refs": list(admission.source_root_refs),
                    "admitted_projection_digest": (
                        admission.admitted_projection_digest
                    ),
                    "runtime_projection_digest": (
                        projection.runtime_projection_digest
                    ),
                }
            )
        return {
            "source_admission_verdict": self.source_admission.verdict,
            "runtime_projection_verdict": self.runtime_projection.verdict,
            "component_bindings": bindings,
            "canonical_target_writes": (
                self.source_admission.canonical_target_writes
                + self.runtime_projection.canonical_target_writes
                + int(oac_activation.get("canonical_target_writes", 0))
            ),
            "activation_status": str(
                oac_activation.get("status", "NOT_USED_IN_THIS_RUN")
            ),
            "adaptation_run_id": oac_activation.get("adaptation_run_id"),
            "activation_binding_digest": oac_activation.get(
                "activation_binding_digest"
            ),
            "adapter_capsule_digest": oac_activation.get(
                "adapter_capsule_digest"
            ),
            "consumption_receipt_digest": oac_activation.get(
                "consumption_receipt_digest"
            ),
            "binding_effect": oac_activation.get("binding_effect"),
            "formation_authority": "ORGREBASE_CONTROL_PLANE",
            **lineage_projection,
            "claim_boundary": (
                "OAC_FORMATION_CONTEXT_AND_AGENTTEAMS_PLAN_BOUND_TO_CURRENT_RUN"
                if exact_execution_bound
                else "OAC_ADMISSION_AND_ACTIVATION_ARE_FORMATION_PRECONDITIONS_"
                "NOT_OAC_BOUND_TO_CURRENT_AGENTTEAMS_RUN"
            ),
        }

    def _enterprise_data_context_view(
        self,
        formation: TaskReceipt | None,
    ) -> dict[str, Any]:
        base = {
            "status": "WAITING_FOR_FORMATION",
            "authority": "ORGREBASE_CONTROL_PLANE_TASK_CONTEXT_COMPILER",
            "manifest_ref": None,
            "slot_count": 0,
            "actor_count": 0,
            "domain_actor_count": 0,
            "actors": [],
        }
        if formation is None:
            return base
        try:
            stored_context = self.store.load_artifact(
                formation.context_manifest_ref,
                WORKSPACE_TASK_CONTEXT_MEDIA_TYPE,
            )
        except KeyError:
            return {
                **base,
                "status": "UNOBSERVED",
                "manifest_ref": formation.context_manifest_ref,
            }
        try:
            manifest = TaskContextManifest.model_validate(stored_context.payload)
        except (TypeError, ValueError) as exc:
            raise IntegrityError("WORKSPACE_DATA_LINEAGE_CONTEXT_INVALID") from exc
        if (
            manifest.ref != formation.context_manifest_ref
            or manifest.task_ref != formation.task_ref
        ):
            raise IntegrityError("WORKSPACE_DATA_LINEAGE_CONTEXT_BINDING_MISMATCH")
        object_to_slots: dict[str, list[str]] = {}
        for binding in manifest.slot_bindings:
            object_to_slots.setdefault(binding.object_ref, []).append(binding.slot_id)
        projections: list[ActorContextProjection] = []
        for projection_ref in manifest.actor_projection_refs:
            try:
                stored_projection = self.store.load_artifact(
                    projection_ref,
                    WORKSPACE_ACTOR_CONTEXT_MEDIA_TYPE,
                )
            except KeyError:
                return {
                    **base,
                    "status": "UNOBSERVED",
                    "manifest_ref": manifest.ref,
                    "manifest_digest": manifest.digest,
                    "slot_count": len(manifest.slot_bindings),
                }
            try:
                projection = ActorContextProjection.model_validate(
                    stored_projection.payload
                )
            except (TypeError, ValueError) as exc:
                raise IntegrityError(
                    "WORKSPACE_DATA_LINEAGE_ACTOR_CONTEXT_INVALID"
                ) from exc
            if (
                projection.ref != projection_ref
                or projection.task_context_ref != manifest.ref
            ):
                raise IntegrityError(
                    "WORKSPACE_DATA_LINEAGE_ACTOR_CONTEXT_BINDING_MISMATCH"
                )
            if any(ref not in object_to_slots for ref in projection.included_refs):
                raise IntegrityError(
                    "WORKSPACE_DATA_LINEAGE_ACTOR_CONTEXT_SCOPE_INVALID"
                )
            projections.append(projection)
        if len(projections) != len({item.ref for item in projections}):
            raise IntegrityError("WORKSPACE_DATA_LINEAGE_ACTOR_CONTEXT_DUPLICATE")
        actors = [
            {
                "actor_id": projection.actor_id,
                "actor_kind": (
                    "DETERMINISTIC_RENDERER"
                    if projection.actor_id == "workspace-renderer"
                    else "DOMAIN_AGENT"
                ),
                "projection_ref": projection.ref,
                "projection_digest": projection.digest,
                "included_slot_ids": [
                    slot for ref in projection.included_refs for slot in object_to_slots[ref]
                ],
                "included_count": len(projection.included_refs),
                "excluded_count": len(projection.excluded),
                "excluded_reason_counts": exclusion_reason_counts(projection),
            }
            for projection in projections
        ]
        return {
            **base,
            "status": "READY",
            "manifest_ref": manifest.ref,
            "manifest_digest": manifest.digest,
            "slot_count": len(manifest.slot_bindings),
            "actor_count": len(actors),
            "domain_actor_count": sum(
                item["actor_kind"] == "DOMAIN_AGENT" for item in actors
            ),
            "actors": actors,
        }

    def _enterprise_data_quote_history_view(self) -> dict[str, Any]:
        base = {
            "status": "NOT_OBSERVED",
            "current_version": None,
            "versions": [],
        }
        try:
            current = self.current_quote()
        except KeyError:
            return base
        current_version = getattr(current, "version", None)
        if current_version is None:
            serializer = getattr(current, "model_dump", None)
            serialized = serializer(mode="json") if callable(serializer) else {}
            current_version = (
                serialized.get("version") if isinstance(serialized, Mapping) else None
            )
        if not isinstance(current_version, str):
            return {**base, "status": "INCOMPLETE_HISTORY"}
        version_number = current_version.removeprefix("v")
        if not version_number.isdigit() or int(version_number) < 1:
            return {
                **base,
                "status": "INCOMPLETE_HISTORY",
                "current_version": current_version,
            }
        versions = []
        first_version = max(1, int(version_number) - 49)
        for number in range(first_version, int(version_number) + 1):
            try:
                item = self.store.get_object(self.quote_object_id, f"v{number}")
            except KeyError:
                return {
                    **base,
                    "status": "INCOMPLETE_HISTORY",
                    "current_version": current_version,
                    "versions": versions,
                }
            versions.append(
                {
                    "ref": item.ref,
                    "version": item.version,
                    "digest": item.digest,
                    "state": item.state.value,
                    "payload": item.payload,
                    "source_refs": list(item.source_refs),
                }
            )
        return {
            "status": "READY",
            "current_version": current_version,
            "total_versions": int(version_number),
            "first_returned_version": first_version,
            "versions": versions,
        }

    def _enterprise_data_change_views(
        self,
        changes: Mapping[str, Mapping[str, Any] | None],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for kind in changes:
            records = changes.get(kind) or {}
            preview_record = records.get("preview")
            approval_record = records.get("approval")
            outcome_record = records.get("outcome")
            row: dict[str, Any] = {
                "kind": kind,
                "status": "NOT_OBSERVED",
                "owner_id": self.change_owner.get(kind),
                "delta": None,
                "approval_status": "NOT_OBSERVED",
                "approval_actor_id": None,
                "predecessor_ref": None,
                "successor_ref": None,
                "receipt_status": "NOT_OBSERVED",
                "receipt_digest": None,
                "workspace_receipt_status": "NOT_OBSERVED",
                "workspace_receipt_digest": None,
                "preview_evidence_status": "NOT_OBSERVED",
                "candidate_metrics": None,
                "metrics": None,
            }
            if preview_record is None:
                if approval_record is not None or outcome_record is not None:
                    row["status"] = "INCOMPLETE_EVIDENCE"
                rows.append(row)
                continue
            try:
                bundle = WorkspacePreviewBundle.model_validate(
                    preview_record["bundle"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise IntegrityError(
                    "WORKSPACE_DATA_LINEAGE_PREVIEW_INVALID"
                ) from exc
            if len(bundle.change_set.deltas) != 1:
                raise IntegrityError("WORKSPACE_DATA_LINEAGE_DELTA_CARDINALITY_INVALID")
            delta = bundle.change_set.deltas[0]
            if bundle.change_spec.owner_id != bundle.change_set.owner_id:
                raise IntegrityError("WORKSPACE_DATA_LINEAGE_CHANGE_OWNER_MISMATCH")
            expected_minimal = build_minimal_rebase_certificate(
                bundle.change_set,
                bundle.preview,
            )
            if expected_minimal.digest != bundle.minimal_rebase_certificate.digest:
                raise IntegrityError("WORKSPACE_DATA_LINEAGE_VMRC_INVALID")
            classifications = [item.classification.value for item in bundle.preview.results]
            candidate_metrics = {
                "affected_hard": classifications.count("AFFECTED_HARD"),
                "bounded_unaffected": classifications.count(
                    "UNAFFECTED_WITHIN_DECLARED_BOUNDARY"
                ),
                "unknown": classifications.count("UNKNOWN"),
                "human_review": sum(
                    classifications.count(value)
                    for value in (
                        "UNKNOWN",
                        "AFFECTED_REVIEW",
                        "AFFECTED_INFORMATIONAL",
                    )
                ),
                "skill_requalification": classifications.count(
                    "REQUALIFICATION_REQUIRED"
                ),
            }
            for count_key in (
                "affected_hard",
                "bounded_unaffected",
                "unknown",
                "skill_requalification",
            ):
                if bundle.preview.counts.get(count_key, 0) != candidate_metrics[count_key]:
                    raise IntegrityError("WORKSPACE_DATA_LINEAGE_PREVIEW_COUNTS_INVALID")
            row.update(
                {
                    "status": "PREVIEWED",
                    "owner_id": bundle.change_set.owner_id,
                    "delta": delta.model_dump(mode="json"),
                    "preview_digest": bundle.preview.digest,
                    "preview_evidence_status": "VMRC_EXACT_BOUND",
                    "candidate_metrics": candidate_metrics,
                }
            )
            approval = None
            binding = None
            if approval_record is not None:
                try:
                    approval = Approval.model_validate(approval_record["approval"])
                    binding_payload = approval_record.get("binding")
                    binding = (
                        WorkspaceApprovalBinding.model_validate(binding_payload)
                        if binding_payload is not None
                        else None
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise IntegrityError(
                        "WORKSPACE_DATA_LINEAGE_APPROVAL_INVALID"
                    ) from exc
                from orgrebase.workspace.approval_authority import verify_approval_authority
                authority_valid = True
                try:
                    verify_approval_authority(self, kind, approval, current=False)
                except (AuthenticationError, AuthorizationError, IntegrityError):
                    authority_valid = False
                if (
                    binding is None
                    or not authority_valid
                    or binding.owner_id != bundle.change_set.owner_id
                    or binding.approval_digest != approval.digest
                    or binding.preview_digest != bundle.preview.digest
                ):
                    row["status"] = "INCOMPLETE_EVIDENCE"
                else:
                    row.update(
                        {
                            "status": "APPROVED",
                            "approval_status": "APPROVED",
                            "approval_actor_id": approval.actor_id,
                            "predecessor_ref": binding.predecessor_ref,
                        }
                    )
            if outcome_record is not None:
                try:
                    outcome = outcome_record["outcome"]
                    receipt = RebaseReceipt.model_validate(
                        outcome["rebase_receipt"]
                    )
                    workspace_payload = outcome.get("workspace_rebase_receipt")
                    workspace_receipt = (
                        WorkspaceRebaseReceipt.model_validate(workspace_payload)
                        if workspace_payload is not None
                        else None
                    )
                    successor_payload = outcome["quote"]
                    successor = self.store.get_object(
                        str(successor_payload["id"]),
                        str(successor_payload["version"]),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise IntegrityError(
                        "WORKSPACE_DATA_LINEAGE_OUTCOME_INVALID"
                    ) from exc
                if (
                    approval is None
                    or binding is None
                    or workspace_receipt is None
                    or receipt.approval_digest != approval.digest
                    or receipt.approval_actor_id != approval.actor_id
                    or successor.digest != successor_payload.get("digest")
                    or (
                        self.workflow_run_id is not None
                        and receipt.workflow_run_id != self.effective_workflow_run_id
                    )
                ):
                    row["status"] = "INCOMPLETE_EVIDENCE"
                else:
                    row.update(
                        {
                            "status": "COMPLETED",
                            "successor_ref": successor.ref,
                            "receipt_status": receipt.status,
                            "receipt_digest": receipt.digest,
                            "workspace_receipt_status": workspace_receipt.status,
                            "workspace_receipt_digest": workspace_receipt.digest,
                            "metrics": dict(receipt.metrics),
                        }
                    )
            rows.append(row)
        return rows

    def _enterprise_data_lineage(
        self,
        *,
        formation: TaskReceipt | None,
        changes: Mapping[str, Mapping[str, Any] | None],
        oac_activation: Mapping[str, Any],
        competition_evidence: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Build one evidence-bound, zero-write data journey for product display."""

        source = self._enterprise_data_source_view()
        oac = self._enterprise_data_oac_view(
            oac_activation,
            competition_evidence,
        )
        context = self._enterprise_data_context_view(formation)
        quotes = self._enterprise_data_quote_history_view()
        change_views = self._enterprise_data_change_views(changes)
        if formation is None:
            status = "WAITING_FOR_FORMATION"
        elif (
            source["status"] == "ADMITTED_AND_MATCHED"
            and oac["activation_status"] == "CONSUMED_BY_QUOTE_FORMATION"
            and context["status"] == "READY"
            and quotes["status"] == "READY"
            and all(item["status"] == "COMPLETED" for item in change_views)
        ):
            status = "READY"
        else:
            status = "IN_PROGRESS"
        body = {
            "schema_version": "orgrebase.enterprise-data-lineage-view.v1",
            "status": status,
            "run_id": self.effective_workflow_run_id,
            "read_model_target_writes": 0,
            "data_class": self.profile.data_class.value,
            "source": source,
            "oac": oac,
            "context": context,
            "quotes": quotes,
            "changes": change_views,
        }
        serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
        if "raw_private_value" in serialized or "internal_cost_floor" in serialized:
            raise IntegrityError("WORKSPACE_DATA_LINEAGE_PRIVATE_VALUE_EXPOSURE")
        return {**body, "digest": sha256_digest(body)}

    @staticmethod
    def _event_scopes(event_chain: dict[str, Any]) -> dict[str, Any]:
        from orgrebase.event_projection import append_scope, empty_scopes, scope_view
        scopes = empty_scopes()
        for record in event_chain.get("records", ()):
            scopes = append_scope(scopes, record)
        return scope_view(scopes, events=event_chain["events"], head_digest=event_chain["head_digest"],
                          status=event_chain["status"])

    @_serialized
    def state(self, *, history_limit: int = 50) -> dict[str, Any]:
        with self.store.read_snapshot():
            return self._state_snapshot(history_limit=history_limit)

    def _state_snapshot(self, *, history_limit: int) -> dict[str, Any]:
        from orgrebase.workspace.approval_authority import authority_detail
        from orgrebase.workspace.source_readmission import membership
        if not 1 <= history_limit <= 100:
            raise ValueError("WORKSPACE_HISTORY_LIMIT_INVALID")
        self.changes.refresh()
        visible_events = list(self.changes.recent(history_limit))
        active_id = self._active_event_id()
        if active_id is not None and all(item.event_id != active_id for item in visible_events):
            visible_events.insert(0, self.changes.get(active_id))
        visible_ids = tuple(item.event_id for item in visible_events)
        quote = None
        with suppress(KeyError):
            quote = self.current_quote().model_dump(mode="json")
        pointer = None
        with suppress(KeyError):
            pointer = self.current_graph_pointer().model_dump(mode="json")
        snapshot = None
        if pointer is not None:
            snapshot = self.current_snapshot().model_dump(mode="json")
        formation = self._formation_record()
        formation_payload = formation.model_dump(mode="json") if formation is not None else None
        coalition = None
        if formation is not None:
            coalition = {
                "plan_ref": formation.coalition_plan_ref,
                "admission_decision_refs": list(formation.admission_decision_refs),
                "context_manifest_ref": formation.context_manifest_ref,
                "trace_ref": formation.trace_ref,
                "coverage_receipt_ref": formation.coverage_receipt_ref,
                "dependency_manifest_ref": formation.dependency_manifest_ref,
            }
        changes = self._change_records(visible_ids)

        def latest(record_type: str) -> dict[str, Any] | None:
            for kind in reversed(visible_ids):
                record = changes[kind][record_type]
                if record is not None:
                    return record
            return None

        change_events = []
        active_event = None
        active_status = None
        for event in visible_events:
            status = self._change_status(event.event_id, records=changes[event.event_id])
            group = membership(self, event.event_id)
            change_events.append({
                "event_id": event.event_id,
                "event_digest": event.digest,
                "slot_id": event.slot_id,
                "operation": event.operation,
                "object_id": event.proposal.id,
                "owner_id": event.owner_id,
                "status": status,
                "authority_revision": authority_detail(self, event.event_id, include_eligible_delegates=False)["revision"],
                **({"source_group_id": group["group_id"]} if group else {}),
            })
            if event.event_id == active_id:
                active_event = event
                active_status = status
        stage = self._stage(quote, active_status)
        event_chain, event_scopes = self.changes.state_history()
        dependency_tool = self._dependency_evidence_tool_record()
        competition_evidence = self._competition_evidence_record()
        agentteams_operations = self._agentteams_operations_view(
            changes=changes,
            competition_evidence=competition_evidence,
        )
        human_interrupts = self._human_interrupts_view(changes)
        oac_activation = self.oac_activation_state()
        task_intake = self._task_intake_run_record()
        if self.task_intake_required and formation is not None and task_intake is None:
            raise IntegrityError("WORKSPACE_TASK_INTAKE_RECEIPT_REQUIRED")
        enterprise_data_lineage = self._enterprise_data_lineage(
            formation=formation,
            changes=changes,
            oac_activation=oac_activation,
            competition_evidence=competition_evidence,
        )
        effective_boundaries = dict(self.boundaries)
        if competition_evidence is not None:
            effective_boundaries.update(
                {
                    "execution_profile": ("CONTROLLED_LOCAL_NATIVE_TASKFLOW_EXECUTED"),
                    "agentteams": "CONTROLLED_LOCAL_NATIVE_TASKFLOW_EXECUTED",
                    "worker_process_mode": "INDEPENDENT_LOCAL_PROCESSES",
                    "agents_are_candidate_only": True,
                }
            )
            oac_lineage = competition_evidence.get("oac_agentteams_lineage")
            if (
                isinstance(oac_lineage, Mapping)
                and oac_lineage.get("status")
                == "OAC_BOUND_EXECUTION_PLAN_REALIZED"
            ):
                effective_boundaries["oac_runtime_bridge"] = (
                    "OAC_FORMATION_CONTEXT_BOUND_TO_CURRENT_AGENTTEAMS_RUN"
                )
        execution = {
            "schema_version": "orgrebase.workspace-execution-view.v1",
            "run_id": self.effective_workflow_run_id,
            "scenario_id": self.scenario.get("id"),
            "mode": effective_boundaries.get("execution_profile", "LOCAL_DETERMINISTIC"),
            "candidate_runtime": (
                "PINNED_AGENTTEAMS_WITH_INDEPENDENT_LOCAL_PROCESSES"
                if competition_evidence is not None
                else "DETERMINISTIC_DOMAIN_PROVIDERS"
            ),
            "canonical_authority": effective_boundaries.get(
                "canonical_state_owner",
                "OrgRebase StateStore and RebaseWorkflow",
            ),
            "agentteams": effective_boundaries.get("agentteams", "NOT_RUN"),
            "oac_admission": oac_activation["status"],
            "oac_activation": oac_activation,
            "oac_agentteams_lineage": (
                competition_evidence.get("oac_agentteams_lineage")
                if competition_evidence is not None
                else None
            ),
            "external_writes": effective_boundaries.get("external_writes", "DISABLED"),
        }
        execution_activity = self._execution_activity(
            formation=formation_payload,
            task_intake=task_intake,
            dependency_tool=dependency_tool,
            changes=changes,
            competition_evidence=competition_evidence,
        )
        business_complete = self._business_complete()
        result = {
            "schema_version": "orgrebase.workspace-state.v2",
            "stage": stage,
            "execution": execution,
            "execution_activity": execution_activity,
            "agentteams_operations": agentteams_operations,
            "human_interrupts": human_interrupts,
            "approval_control": {
                "schema_version": "orgrebase.workspace-approval-control.v1",
                "review_duration_ms": self.review_duration_ms,
                "gate_store": "POSTGRESQL_ARTIFACT" if self.store.backend == "postgresql" else "FILE_BACKED_SQLITE_ARTIFACT"
                if self.store_path != ":memory:"
                else "IN_MEMORY_SQLITE_ARTIFACT",
                "restart_enforced": self.store_path != ":memory:",
                "identity_mode": self.approval_identity_mode,
                "identity_claim_boundary": identity_claim_boundary(self.approval_identity_mode),
                "external_iam": "NOT_RUN",
            },
            "scenario": dict(self.scenario),
            "boundaries": effective_boundaries,
            "enterprise_seed_profile": profile_summary(
                self.profile,
                receipt=self.profile_admission,
            ),
            "enterprise_seed_admission": parse_enterprise_seed_admission_receipt(
                self.profile_admission
            ).model_dump(mode="json"),
            "enterprise_seed_source_admission": (
                parse_enterprise_seed_source_admission_receipt(self.source_admission).model_dump(mode="json")
            ),
            "enterprise_seed_runtime_projection": (
                parse_enterprise_seed_runtime_projection_receipt(self.runtime_projection).model_dump(
                    mode="json"
                )
            ),
            "enterprise_seed_runtime_binding": self._profile_binding_record(),
            "enterprise_data_lineage": enterprise_data_lineage,
            "task_intake": task_intake,
            "quote": quote,
            "formation": formation_payload,
            "coalition": coalition,
            "dependency_evidence_tool": dependency_tool,
            "graph_pointer": pointer,
            "graph_snapshot": snapshot,
            "latest_preview": latest("preview"),
            "latest_approval": latest("approval"),
            "latest_outcome": latest("outcome"),
            "changes": changes,
            "source_gaps": list(self.changes.gaps()),
            "change_events": change_events,
            "change_history": {"total": self.changes.count, "pending": self.changes.pending_count,
                               "limit": history_limit, "returned": len(visible_events)},
            "active_event_id": active_id,
            "business_complete": business_complete,
            "event_chain": event_chain,
            "event_scopes": event_scopes,
            "actions": self._actions(stage, active_event, active_status),
        }
        if competition_evidence is not None or self.competition_mode == GOLDEN_COMPETITION_MODE:
            result["competition_evidence"] = competition_evidence
        if competition_evidence is not None:
            if business_complete:
                result["experience_governance"] = self.experience.view(
                    str(competition_evidence["run_id"])
                )
            else:
                result["experience_governance"] = {
                    "schema_version": "orgrebase.experience-governance-view.v1",
                    "run_id": competition_evidence["run_id"],
                    "status": "WAITING_FOR_PENDING_CHANGES",
                    "reason": "BACKGROUND_REVIEW_STARTS_AFTER_BUSINESS_TERMINAL",
                    "candidate_only": True,
                    "target_writes": 0,
                }
        return result

    @_serialized
    def experience_view(self, *, run_id: str | None = None) -> dict[str, Any]:
        evidence = self._competition_evidence_record()
        if evidence is None:
            return {
                "schema_version": "orgrebase.experience-governance-view.v1",
                "run_id": run_id,
                "status": "NOT_RUN",
                "reason": "GOLDEN_COMPETITION_EVIDENCE_REQUIRED",
                "candidate_only": True,
                "target_writes": 0,
            }
        actual_run_id = str(evidence["run_id"])
        if run_id is not None and run_id != actual_run_id:
            raise IntegrityError("EXPERIENCE_CROSS_RUN_ACCESS_DENIED")
        if not self._business_complete():
            return {
                "schema_version": "orgrebase.experience-governance-view.v1",
                "run_id": actual_run_id,
                "status": "WAITING_FOR_PENDING_CHANGES",
                "reason": "BACKGROUND_REVIEW_STARTS_AFTER_BUSINESS_TERMINAL",
                "candidate_only": True,
                "target_writes": 0,
            }
        return self.experience.view(actual_run_id)

    @_serialized
    def decide_experience(
        self,
        *,
        run_id: str,
        decision: str,
        actor_id: str,
        candidate_digest: str,
        evaluation_digest: str,
        observed_skill_head_digest: str,
    ) -> dict[str, Any]:
        evidence = self._competition_evidence_record()
        if evidence is None or run_id != evidence.get("run_id"):
            raise IntegrityError("EXPERIENCE_CROSS_RUN_ACCESS_DENIED")
        experience = self.experience.decide(
            run_id=run_id,
            decision=decision,
            actor_id=actor_id,
            candidate_digest=candidate_digest,
            evaluation_digest=evaluation_digest,
            observed_skill_head_digest=observed_skill_head_digest,
        )
        return {"experience_governance": experience, "state": self.state()}

    @_serialized
    def reset(self) -> dict[str, Any]:
        """Start a new local synthetic session after an explicit user command."""

        self.store.reset_local_session(authorize=current_authorization())
        self._restore_correlation_binding()
        self._configure_runtime()
        self._restore_competition_execution_binding()
        return {"status": "RESET", "state": self.state()}

    @_serialized
    def export_quote(self) -> dict[str, Any]:
        if self.changes.gaps():
            raise RuntimeError("OWNER_READMISSION_REQUIRED")
        state = self.state()
        if state["quote"] is None:
            raise RuntimeError("WORKSPACE_QUOTE_NOT_FORMED")
        document = {
            "schema_version": "orgrebase.workspace-quote-export.v2",
            "workspace_state_schema_version": state["schema_version"],
            "business_complete": state["business_complete"],
            "scenario": state["scenario"],
            "enterprise_seed_profile": state["enterprise_seed_profile"],
            "enterprise_seed_admission": state["enterprise_seed_admission"],
            "enterprise_seed_source_admission": state["enterprise_seed_source_admission"],
            "enterprise_seed_runtime_projection": state["enterprise_seed_runtime_projection"],
            "enterprise_seed_runtime_binding": state["enterprise_seed_runtime_binding"],
            "stage": state["stage"],
            "quote": state["quote"],
            "graph_pointer": state["graph_pointer"],
            "boundaries": state["boundaries"],
        }
        return {**document, "digest": sha256_digest(document)}

    @_serialized
    def export_evidence(self) -> dict[str, Any]:
        state = self.state()
        if state["quote"] is None:
            raise RuntimeError("WORKSPACE_SESSION_EMPTY")
        full_chain = self.store.verify_event_chain()
        full_chain["records"] = list(self.store.event_records())
        state["changes"] = {event_id: {"preview": self._preview_record(event_id),
                                      "approval": self._approval_record(event_id),
                                      "outcome": self._outcome_record(event_id)}
                            for event_id in self.change_order}
        state["event_chain"] = full_chain
        state["event_scopes"] = self._event_scopes(full_chain)
        document = {
            "schema_version": "orgrebase.workspace-evidence-export.v2",
            "workspace_state_schema_version": state["schema_version"],
            "business_complete": state["business_complete"],
            "digest_contract": "sha256(canonical-json(all fields except digest))",
            "stage": state["stage"],
            "scenario": state["scenario"],
            "enterprise_seed_profile": state["enterprise_seed_profile"],
            "enterprise_seed_admission": state["enterprise_seed_admission"],
            "enterprise_seed_source_admission": state["enterprise_seed_source_admission"],
            "enterprise_seed_runtime_projection": state["enterprise_seed_runtime_projection"],
            "enterprise_seed_runtime_binding": state["enterprise_seed_runtime_binding"],
            "boundaries": state["boundaries"],
            "task_intake": state["task_intake"],
            "quote": state["quote"],
            "formation": state["formation"],
            "formation_graph_snapshot": self.store.load_artifact(
                state["formation"]["graph_snapshot_ref"], SNAPSHOT_MEDIA_TYPE
            ).payload,
            "formation_commit_events": list(self.changes.journal("WORKSPACE_TASK_COMMITTED")),
            "coalition": state["coalition"],
            "dependency_evidence_tool": state["dependency_evidence_tool"],
            "change_events": [event.model_dump(mode="json") for event in self.changes.all()],
            "change_registration_events": list(self.changes.journal("WORKSPACE_CHANGE_REGISTERED")),
            "changes": state["changes"],
            "graph_pointer": state["graph_pointer"],
            "graph_snapshot": state["graph_snapshot"],
            "event_chain": state["event_chain"],
            "event_scopes": state["event_scopes"],
            "oac_activation_consumption": state["execution"]["oac_activation"],
        }
        if state.get("competition_evidence") is not None:
            document["competition_evidence"] = state["competition_evidence"]
        if state.get("experience_governance") is not None:
            document["experience_governance"] = state["experience_governance"]
        return {**document, "digest": sha256_digest(document)}

    def current_snapshot(self) -> WorkspaceGraphSnapshot:
        pointer = self.current_graph_pointer()
        return WorkspaceGraphSnapshot.model_validate(
            self.store.load_artifact(str(pointer.payload["snapshot_ref"]), SNAPSHOT_MEDIA_TYPE).payload
        )

    def _fixture_for_change(self, spec: WorkspaceChangeSpec) -> EnterpriseFixture:
        snapshot = self.current_snapshot()
        if snapshot.ref != spec.expected_snapshot_ref or snapshot.digest != spec.expected_snapshot_digest:
            raise RuntimeError("WORKSPACE_CHANGE_EXPECTED_SNAPSHOT_MISMATCH")
        fixture = self.snapshot_builder.to_enterprise_fixture(
            snapshot=snapshot,
            artifact_reader=self.store.load_artifact,
            object_reader=self.store.get_object,
            agents=self.legacy.agents,
            evaluation_cases=self.legacy.evaluation_cases,
            context_profiles=self.context_profiles,
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
        proposal = self.store.get_object(spec.object_id, spec.proposed_version)
        if not any(item.ref == proposal.ref for item in fixture.objects):
            fixture = fixture.model_copy(update={"objects": (*fixture.objects, proposal)})
        return fixture

    def change_spec(self, kind: str) -> WorkspaceChangeSpec:
        snapshot = self.current_snapshot()
        event = self.changes.get(kind)
        return WorkspaceChangeSpec(
            id=f"changeset:workspace-{event.event_id}", operation=event.operation,
            revision="r1", owner_id=event.owner_id, purpose=event.purpose,
            object_id=event.proposal.id, base_version=event.base_version,
            proposed_version=event.proposal.version,
            expected_snapshot_ref=snapshot.ref, expected_snapshot_digest=snapshot.digest,
            idempotency_key=f"workspace:apply:{event.event_id}@r1",
        )

    def _run_envelope(self, spec: WorkspaceChangeSpec) -> RunEnvelope:
        times = workflow_times(self.clock)
        return RunEnvelope(
            run_id=self.effective_workflow_run_id,
            nonce=self.workflow_run_nonce,
            issued_at=times.approved_at, expires_at=times.expires_at,
            mode="LOCAL_DETERMINISTIC", evidence_class="LOCAL_DETERMINISTIC",
        )

    @staticmethod
    def _require_stage(actual: str, expected: str) -> None:
        if actual != expected:
            raise RuntimeError(f"WORKSPACE_STAGE_CONFLICT:expected={expected},actual={actual}")

    def _ensure_review_gate(
        self,
        *,
        kind: str,
        bundle: WorkspacePreviewBundle,
        preview_artifact_digest: str,
    ) -> dict[str, Any]:
        existing = self._review_gate_record(kind)
        if existing is None:
            gate = self._new_review_gate(
                kind=kind,
                bundle=bundle,
                preview_artifact_digest=preview_artifact_digest,
            )
            with self.store.transaction() as connection:
                self.store.save_artifact(
                    connection,
                    self._review_gate_artifact_id(kind),
                    WORKSPACE_REVIEW_GATE_MEDIA_TYPE,
                    gate,
                )
        return self._require_review_gate_binding(
            kind=kind,
            bundle=bundle,
            preview_artifact_digest=preview_artifact_digest,
        )

    def _reuse_preview(self, kind: str, existing: dict[str, Any]) -> WorkspacePreviewBundle:
        bundle = WorkspacePreviewBundle.model_validate(existing["bundle"])
        if self._outcome_record(kind) is None:
            require_preview_runtime(self, kind, bundle.preview.digest)
            validate_source_observations(self, (self.changes.get(kind).proposal,), self.clock.now())
        self._ensure_review_gate(kind=kind, bundle=bundle, preview_artifact_digest=existing["artifact_digest"])
        self.last_preview_bundle = bundle
        return bundle

    def preview_change(self, kind: str) -> WorkspacePreviewBundle:
        """Capture inputs, compute once without locks, then admit the exact result."""
        from orgrebase.workspace.change_proposals import require_action
        from orgrebase.workspace.change_recovery import (
            attempt_command,
            recovery_adapter,
            require_recovery_inputs,
        )
        from orgrebase.workspace.rebuild import preview_quote_pricing
        from orgrebase.workspace.runtime_revision import workspace_revision

        with self._command_lock:
            require_action(self, "propose")
            self.changes.refresh()
            self._require_ungrouped(kind)
            existing = self._preview_record(kind)
            if existing is not None:
                return self._reuse_preview(kind, existing)
            with self.store.transaction() as connection:
                from orgrebase.workspace.enterprise_binding import lock_binding_scope
                lock_binding_scope(self, connection)
                if self._formation_record() is None:
                    raise RuntimeError("WORKSPACE_QUOTE_NOT_FORMED")
                if self._change_status(kind) != "RECEIVED":
                    raise RuntimeError("WORKSPACE_CHANGE_NOT_PREVIEWABLE")
                spec = self.change_spec(kind)
                fixture = self._fixture_for_change(spec)
                change_set = self.change_builder.build(fixture=fixture, spec=spec)
                preview = ImpactEngine(fixture).preview(change_set)
                minimal = build_minimal_rebase_certificate(change_set, preview)
                pricing_comparison = preview_quote_pricing(
                    store=self.store, quote=self.current_quote(), proposal=self.changes.get(kind).proposal,
                    template_ref=self.profile.default_task.template_ref, snapshot_digest=spec.expected_snapshot_digest,
                )
                validate_source_observations(self, (self.changes.get(kind).proposal,), self.clock.now())
                captured_runtime = workspace_revision(self)
                adapter, verifier = recovery_adapter(self, kind)
                recovery = require_recovery_inputs(self, kind)
                attempt = reserve_attempt(self, connection, command=attempt_command(self, kind), fixture=fixture,
                    change_set=change_set, preview=preview, envelope=self._run_envelope(spec),
                    request_binding=recovery["digest"] if recovery else None)
        advisory = execute_attempt(self, attempt, fixture=fixture, change_set=change_set, preview=preview,
                                   adapter=adapter, verifier=verifier)
        bundle = WorkspacePreviewBundle(
            change_spec=spec, snapshot_ref=spec.expected_snapshot_ref, snapshot_digest=spec.expected_snapshot_digest,
            change_set=change_set, preview=preview, minimal_rebase_certificate=minimal,
            advisory=advisory, run_envelope=attempt.envelope,
            pricing_comparison=pricing_comparison,
        )
        with self._command_lock, self.store.transaction() as connection:
            from orgrebase.workspace.enterprise_binding import lock_binding_scope
            lock_binding_scope(self, connection)
            require_action(self, "propose")
            if require_recovery_inputs(self, kind) != recovery:
                raise IntegrityError("CHANGE_RECOVERY_CONTEXT_CHANGED")
            self.changes.refresh()
            self.store.get_idempotent(attempt.key, attempt.request_digest, connection=connection)
            self._require_ungrouped(kind)
            if workspace_revision(self) != captured_runtime:
                raise IntegrityError("WORKSPACE_ADVISORY_RUNTIME_CHANGED")
            existing = self._preview_record(kind)
            if existing is not None:
                return self._reuse_preview(kind, existing)
            if self._change_status(kind) != "RECEIVED":
                raise IntegrityError("WORKSPACE_ADVISORY_CHANGE_NO_LONGER_PENDING")
            current_fixture = self._fixture_for_change(spec)
            if sha256_digest(current_fixture.model_dump(mode="json")) != sha256_digest(fixture.model_dump(mode="json")):
                raise IntegrityError("WORKSPACE_ADVISORY_INPUT_CHANGED")
            if utc_datetime(self.clock.now()) >= utc_datetime(attempt.envelope.expires_at):
                raise IntegrityError("WORKSPACE_ADVISORY_PREVIEW_EXPIRED")
            validate_source_observations(self, (self.changes.get(kind).proposal,), self.clock.now())
            require_attempt_sources(self, attempt)
            payload = bundle.model_dump(mode="json")
            artifact_id = self._preview_artifact_id(kind)
            preview_artifact_digest = sha256_digest(payload)
            review_gate = self._new_review_gate(kind=kind, bundle=bundle,
                                                preview_artifact_digest=preview_artifact_digest)
            artifact_digest = self.store.save_artifact(connection, artifact_id, WORKSPACE_PREVIEW_MEDIA_TYPE, payload)
            bind_preview_runtime(self, connection, kind, bundle.preview.digest)
            review_gate_artifact_digest = self.store.save_artifact(connection,
                self._review_gate_artifact_id(kind), WORKSPACE_REVIEW_GATE_MEDIA_TYPE, review_gate)
            preview_event = {"kind": kind, "artifact_id": artifact_id, "artifact_digest": artifact_digest,
                             "preview_digest": bundle.preview.digest, "snapshot_digest": bundle.snapshot_digest}
            if self.review_duration_ms > 0:
                preview_event.update({"review_gate_ref": self._review_gate_artifact_id(kind),
                    "review_gate_artifact_digest": review_gate_artifact_digest,
                    "review_not_before": review_gate["not_before"], "review_duration_ms": self.review_duration_ms})
            self.store.append_event(connection, "WORKSPACE_CHANGE_PREVIEWED", preview_event)
        self.last_preview_bundle = bundle
        return bundle

    def preview_command(self, kind: str) -> dict[str, Any]:
        self.preview_change(kind)
        with self._command_lock:
            record = self._preview_record(kind)
            if record is None:  # pragma: no cover - transaction postcondition
                raise RuntimeError("WORKSPACE_PREVIEW_PERSISTENCE_FAILED")
            return {**record, "state": self.state()}

    def _clock(self, kind: str) -> WorkspaceWorkflowClock:
        times = workflow_times(self.clock)
        return WorkspaceWorkflowClock(
            approved_at=times.approved_at, apply_at=times.apply_at, expires_at=times.expires_at,
        )

    def _workflow(
        self,
        *,
        kind: str,
        bundle: WorkspacePreviewBundle,
        fail_after: str | None = None,
    ) -> tuple[EnterpriseFixture, RebaseWorkflow]:
        fixture = self._fixture_for_change(bundle.change_spec)
        request_check = current_authorization()

        def authorize_apply() -> None:
            from orgrebase.workspace.enterprise_binding import require_change_owner
            require_change_owner(self, self.changes.get(kind))
            if request_check is not None:
                request_check()
            validate_source_observations(self, (self.changes.get(kind).proposal,), self.clock.now())
            require_preview_runtime(self, kind, bundle.preview.digest)
            approval_record = self._approval_record(kind)
            if approval_record is not None:
                from orgrebase.workspace.approval_authority import verify_approval_authority
                verify_approval_authority(self, kind, Approval.model_validate(approval_record["approval"]), current=True)

        from orgrebase.workspace.change_recovery import recovery_adapter
        return fixture, self._create_rebase_workflow(
            fixture, authorize_commit=authorize_apply, fail_after=fail_after,
            advisory_verifier=recovery_adapter(self, kind)[1],
        )

    def _create_rebase_workflow(
        self, fixture: EnterpriseFixture, *, authorize_commit: Callable[[], None],
        authorize_completion: Callable[[], None] | None = None,
        fail_after: str | None = None,
        advisory_verifier: Any | None = None,
    ) -> RebaseWorkflow:
        clock = self._clock("")
        provider = WorkspaceRebuildContextProvider(
            clock=clock, read_dependency_validator=lambda premises, now: validate_read_dependencies(self, premises, now),
            template_ref=self.profile.default_task.template_ref,
        )

        def verify_completion() -> None:
            (authorize_completion or authorize_commit)()
            provider.current_premises(self.store, self.clock.now())

        return RebaseWorkflow(
            fixture,
            self.store,
            identity=WORKSPACE_IDENTITY,
            clock=clock,
            execution_clock=self._execution_clock,
            authorize_commit=authorize_commit,
            authorize_completion=verify_completion,
            context_provider=provider,
            rebuild_handlers={"QUOTE": QuoteRebuildPayloadHandler(template_ref=self.profile.default_task.template_ref)},
            apply_extension=WorkspaceGraphApplyExtension(
                fail_after=fail_after,
                run_id=self.workflow_run_id,
                task_request=self.profile.task_request(),
                graph_pointer_id=self.graph_pointer_id,
                graph_snapshot_id=self.graph_snapshot_id,
                snapshot_scope_roots=self.snapshot_scope_roots,
            ),
            advisory_verifier=advisory_verifier or self.advisory_verifier,
        )

    @_serialized
    def approve_change(
        self,
        kind: str,
        *,
        actor_id: str,
        preview_digest: str,
        recovery_digest: str | None = None,
    ) -> dict[str, Any]:
        self._require_ungrouped(kind)
        from orgrebase.workspace.change_recovery import require_recovery_approval
        require_recovery_approval(self, kind, recovery_digest)
        if kind not in self.change_owner:
            raise KeyError(f"UNKNOWN_WORKSPACE_CHANGE:{kind}")
        if self.changes.rejection(kind) is not None:
            raise RuntimeError("WORKSPACE_CHANGE_REJECTED")
        if self._outcome_record(kind) is None:
            validate_source_observations(self, (self.changes.get(kind).proposal,), self.clock.now())
        status, status_reason = self._change_status_with_reason(kind)
        if status == "EXPIRED":
            if status_reason in {"RUNTIME_BINDING_MISSING", "RUNTIME_BINDING_SCOPE_CHANGED", "RUNTIME_IMPLEMENTATION_CHANGED"}:
                raise RuntimeError("WORKSPACE_RUNTIME_REPLAN_REQUIRED:" + status_reason)
            raise RuntimeError("WORKSPACE_PROPOSAL_EXPIRED")
        preview_record = self._preview_record(kind)
        if preview_record is None:
            raise RuntimeError(f"WORKSPACE_PREVIEW_REQUIRED:{kind}")
        bundle = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
        require_preview_runtime(self, kind, bundle.preview.digest)
        if preview_digest != bundle.preview.digest:
            raise RuntimeError("WORKSPACE_PREVIEW_DIGEST_MISMATCH:approval must bind the persisted preview")
        from orgrebase.workspace.approval_authority import record_approval_authority, require_approval_actor
        require_approval_actor(self, kind, actor_id)
        review_gate_record = self._ensure_review_gate(
            kind=kind,
            bundle=bundle,
            preview_artifact_digest=preview_record["artifact_digest"],
        )
        existing = self._approval_record(kind)
        if existing is not None:
            approval = Approval.model_validate(existing["approval"])
            if approval.actor_id != actor_id or approval.preview_digest != preview_digest:
                raise RuntimeError("WORKSPACE_APPROVAL_COMMAND_CONFLICT")
            self._require_approval_binding(
                kind=kind,
                bundle=bundle,
                approval=approval,
                require_current_predecessor=True,
            )
            return {**existing, "state": self.state()}
        review_gate = review_gate_record["gate"]
        now_epoch_ms = self._wall_clock_epoch_ms()
        not_before_epoch_ms = review_gate["not_before_epoch_ms"]
        if now_epoch_ms < not_before_epoch_ms:
            raise WorkspaceReviewGatePending(
                remaining_ms=not_before_epoch_ms - now_epoch_ms,
                not_before=review_gate["not_before"],
            )
        if self._change_status(kind) != "PREVIEWED":
            raise RuntimeError("WORKSPACE_CHANGE_NOT_APPROVABLE")
        _, workflow = self._workflow(kind=kind, bundle=bundle)
        approval = workflow.approve(
            bundle.change_set,
            bundle.preview,
            bundle.minimal_rebase_certificate,
            actor_id=actor_id,
        )
        predecessor = self.current_quote()
        approval_binding = WorkspaceApprovalBinding(
            id=self._approval_binding_artifact_id(kind),
            approval_ref=approval.id,
            approval_digest=approval.digest,
            change_kind=kind,
            workflow_run_id=bundle.run_envelope.run_id,
            run_nonce=bundle.run_envelope.nonce,
            pack_digest=self.deployment_binding.get("pack_digest"),
            profile_digest=self.profile_digest,
            predecessor_ref=predecessor.ref,
            predecessor_digest=predecessor.digest,
            change_set_digest=bundle.change_set.digest,
            preview_digest=bundle.preview.digest,
            minimal_rebase_certificate_digest=(bundle.minimal_rebase_certificate.digest),
            owner_id=bundle.change_spec.owner_id,
        )
        artifact_id = self._approval_artifact_id(kind)
        with self.store.transaction() as connection:
            from orgrebase.workspace.enterprise_binding import lock_binding_scope
            lock_binding_scope(self, connection)
            require_recovery_approval(self, kind, recovery_digest)
            authorization = current_authorization()
            if authorization is not None:
                authorization()
            if self._change_status(kind) != "PREVIEWED":
                raise RuntimeError("WORKSPACE_CHANGE_NOT_APPROVABLE")
            artifact_digest = self.store.save_artifact(
                connection,
                artifact_id,
                WORKSPACE_APPROVAL_MEDIA_TYPE,
                approval.model_dump(mode="json"),
            )
            authority_digest = record_approval_authority(self, kind, approval, connection)
            binding_digest = self.store.save_artifact(
                connection,
                approval_binding.id,
                WORKSPACE_APPROVAL_BINDING_MEDIA_TYPE,
                approval_binding.model_dump(mode="json"),
            )
            approval_event = {
                "kind": kind,
                "artifact_id": artifact_id,
                "artifact_digest": artifact_digest,
                "preview_digest": preview_digest,
                "approval_digest": approval.digest,
                "actor_id": actor_id,
            }
            if authority_digest is not None:
                approval_event["authority_digest"] = authority_digest
            if review_gate["review_duration_ms"] > 0:
                approval_event.update(
                    {
                        "review_gate_ref": review_gate_record["artifact_id"],
                        "review_gate_artifact_digest": review_gate_record["artifact_digest"],
                        "review_not_before": review_gate["not_before"],
                        "review_not_before_epoch_ms": not_before_epoch_ms,
                        "approval_observed_at": self._epoch_ms_timestamp(now_epoch_ms),
                        "approval_observed_at_epoch_ms": now_epoch_ms,
                        "review_wait_satisfied": True,
                        "review_duration_ms": review_gate["review_duration_ms"],
                        "approval_identity_mode": review_gate["approval_identity_mode"],
                    }
                )
            # Preserve the frozen preliminary event commitment for the legacy
            # reference profile while making the deployable Pilot's exact Pack
            # binding part of its append-only event chain.  Both paths persist
            # and enforce the binding artifact before Apply; only a Pack-bound
            # deployment publishes the extra deployment identity in the event.
            if approval_binding.pack_digest is not None:
                approval_event.update(
                    {
                        "approval_binding_ref": approval_binding.id,
                        "approval_binding_digest": binding_digest,
                    }
                )
            self.store.append_event(
                connection,
                "WORKSPACE_CHANGE_APPROVED",
                approval_event,
            )
        self.changes.refresh()
        record = self._approval_record(kind)
        if record is None:  # pragma: no cover - transaction postcondition
            raise RuntimeError("WORKSPACE_APPROVAL_PERSISTENCE_FAILED")
        return {**record, "state": self.state()}

    @staticmethod
    def _collaboration(bundle: WorkspacePreviewBundle) -> dict[str, Any]:
        return {
            "orchestration_plan": bundle.advisory.orchestration_plan,
            "compilation_receipt": bundle.advisory.compilation_receipt,
            "coordination_receipt": bundle.advisory.coordination_receipt,
            "candidate_ingestion": bundle.advisory.ingestion_receipt,
            "handoffs": bundle.advisory.handoffs,
            "agent_runs": bundle.advisory.agent_runs,
            **({"native_execution": bundle.advisory.native_execution}
               if bundle.advisory.native_execution is not None else {}),
        }

    def _workspace_receipt(
        self,
        *,
        bundle: WorkspacePreviewBundle,
        workflow: RebaseWorkflow,
    ) -> WorkspaceRebaseReceipt | None:
        workspace_receipt = workflow.last_extension_receipt
        if workspace_receipt is not None:
            return workspace_receipt
        predictable = f"workspace-rebase:{bundle.change_set.id.split(':', 1)[-1]}@{bundle.change_set.revision}"
        try:
            return WorkspaceRebaseReceipt.model_validate(
                self.store.load_artifact(
                    predictable,
                    "application/vnd.orgrebase.workspace-rebase-receipt+json",
                ).payload
            )
        except (KeyError, ValueError):
            return None

    def _execute_apply(
        self,
        *,
        kind: str,
        bundle: WorkspacePreviewBundle,
        approval: Approval,
        fail_after: str | None = None,
        current_revisions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        fixture, workflow = self._workflow(
            kind=kind,
            bundle=bundle,
            fail_after=fail_after,
        )
        receipt = workflow.apply(
            change_set=bundle.change_set,
            preview=bundle.preview,
            minimal_rebase_certificate=bundle.minimal_rebase_certificate,
            approval=approval,
            collaboration=self._collaboration(bundle),
            run_envelope=bundle.run_envelope,
            current_revisions=(current_revisions or fixture.revisions),
            idempotency_key=bundle.change_spec.idempotency_key,
        )
        workspace_receipt = self._workspace_receipt(bundle=bundle, workflow=workflow)
        quote, graph_pointer = self._apply_successors(receipt, workspace_receipt)
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
            "quote": quote,
            "graph_pointer": graph_pointer,
            "event_chain": self.store.verify_event_chain(),
        }

    def _apply_successors(
        self,
        receipt: RebaseReceipt,
        workspace_receipt: WorkspaceRebaseReceipt | None,
    ) -> tuple[VersionedObject, VersionedObject]:
        """Resolve this commit's versions even when a later change is current."""
        if (
            workspace_receipt is None
            or workspace_receipt.base_rebase_receipt_ref != receipt.id
            or workspace_receipt.base_rebase_receipt_digest != receipt.digest
            or len(workspace_receipt.successor_object_refs) != 1
        ):
            raise IntegrityError("WORKSPACE_APPLIED_SUCCESSOR_BINDING_INVALID")
        quote = self.store.get_object(*split_ref(workspace_receipt.successor_object_refs[0]))
        pointer = self.store.get_object(*split_ref(workspace_receipt.graph_pointer_ref))
        snapshot = WorkspaceGraphSnapshot.model_validate(
            self.store.load_artifact(
                workspace_receipt.successor_snapshot_ref, SNAPSHOT_MEDIA_TYPE
            ).payload
        )
        if (
            quote.id != self.quote_object_id
            or pointer.id != self.graph_pointer_id
            or snapshot.ref != workspace_receipt.successor_snapshot_ref
            or snapshot.digest != workspace_receipt.successor_snapshot_digest
            or pointer.payload.get("snapshot_ref") != snapshot.ref
            or pointer.payload.get("snapshot_digest") != snapshot.digest
            or not any(
                item.object_ref == quote.ref and item.digest == quote.digest
                for item in snapshot.object_digests
            )
        ):
            raise IntegrityError("WORKSPACE_APPLIED_SUCCESSOR_BINDING_INVALID")
        return quote, pointer

    def _recover_apply_result(
        self,
        *,
        bundle: WorkspacePreviewBundle,
        approval: Approval,
        include_event_chain: bool = True,
    ) -> dict[str, Any] | None:
        slug = bundle.change_set.id.split(":", 1)[-1]
        receipt_id = f"rebase:workspace:{slug}@{bundle.change_set.revision}"
        try:
            receipt = RebaseReceipt.model_validate(
                self.store.load_artifact(
                    receipt_id,
                    "application/vnd.orgrebase.rebase-receipt+json",
                ).payload
            )
        except KeyError:
            return None
        if receipt.approval_digest != approval.digest:
            raise RuntimeError("WORKSPACE_APPLIED_APPROVAL_DIGEST_CONFLICT")
        kind = bundle.change_spec.id.removeprefix("changeset:workspace-")
        event = self.changes.get(kind)
        binding = self._require_approval_binding(
            kind=kind, bundle=bundle, approval=approval, require_current_predecessor=False,
        )
        deltas = bundle.change_set.deltas
        if (
            bundle.change_set.id != bundle.change_spec.id
            or bundle.change_set.revision != bundle.change_spec.revision
            or bundle.change_spec.object_id != event.proposal.id
            or bundle.change_spec.base_version != event.base_version
            or bundle.change_spec.proposed_version != event.proposal.version
            or bundle.change_spec.operation != event.operation
            or len(deltas) != 1
            or deltas[0].object_id != event.proposal.id
            or deltas[0].base_version != event.base_version
            or deltas[0].proposed_version != event.proposal.version
            or deltas[0].proposed_value != event.proposal.payload["canonical_value"]
            or approval.change_set_digest != bundle.change_set.digest
            or approval.preview_digest != bundle.preview.digest
            or approval.minimal_rebase_certificate_digest != bundle.minimal_rebase_certificate.digest
            or receipt.id != receipt_id
            or receipt.status != "COMPLETED"
            or receipt.workflow_run_id != bundle.run_envelope.run_id
            or receipt.run_nonce != bundle.run_envelope.nonce
            or receipt.change_set_ref != f"{bundle.change_set.id}@{bundle.change_set.revision}"
            or receipt.revision_lock != bundle.preview.revision_lock
            or receipt.revision_lock.change_set_digest != bundle.change_set.digest
            or receipt.preview_ref != bundle.preview.id
            or receipt.minimal_rebase_certificate_digest != bundle.minimal_rebase_certificate.digest
            or receipt.approval_ref != approval.id
            or receipt.approval_actor_id != approval.actor_id
        ):
            raise IntegrityError("WORKSPACE_COMMITTED_RECEIPT_BINDING_INVALID")
        workspace_receipt_id = f"workspace-rebase:{slug}@{bundle.change_set.revision}"
        workspace_receipt = WorkspaceRebaseReceipt.model_validate(
            self.store.load_artifact(
                workspace_receipt_id,
                "application/vnd.orgrebase.workspace-rebase-receipt+json",
            ).payload
        )
        quote, graph_pointer = self._apply_successors(receipt, workspace_receipt)
        predecessor = self.store.get_object(*split_ref(binding.predecessor_ref))
        if (
            workspace_receipt.id != workspace_receipt_id
            or quote.payload.get("rebased_from") != binding.predecessor_ref
            or predecessor.digest != binding.predecessor_digest
        ):
            raise IntegrityError("WORKSPACE_APPLIED_SUCCESSOR_BINDING_INVALID")
        return {
            "change_spec": bundle.change_spec,
            "change_set": bundle.change_set,
            "preview": bundle.preview,
            "minimal_rebase_certificate": bundle.minimal_rebase_certificate,
            "approval": approval,
            "rebase_receipt": receipt,
            "workspace_rebase_receipt": workspace_receipt,
            "quote": quote,
            "graph_pointer": graph_pointer,
            **({"event_chain": self.store.verify_event_chain()} if include_event_chain else {}),
        }

    @staticmethod
    def _outcome_payload(kind: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": kind,
            "change_spec": result["change_spec"].model_dump(mode="json"),
            "change_set": result["change_set"].model_dump(mode="json"),
            "preview_digest": result["preview"].digest,
            "approval": result["approval"].model_dump(mode="json"),
            "approval_digest": result["approval"].digest,
            "rebase_receipt": result["rebase_receipt"].model_dump(mode="json"),
            "workspace_rebase_receipt": (
                result["workspace_rebase_receipt"].model_dump(mode="json")
                if result["workspace_rebase_receipt"] is not None
                else None
            ),
            "quote": result["quote"].model_dump(mode="json"),
            "graph_pointer": result["graph_pointer"].model_dump(mode="json"),
        }

    def _persist_outcome(self, kind: str, result: dict[str, Any]) -> dict[str, Any]:
        payload = self._outcome_payload(kind, result)
        artifact_id = self._outcome_artifact_id(kind)
        with self.store.transaction() as connection:
            artifact_digest = self.store.save_artifact(
                connection,
                artifact_id,
                WORKSPACE_OUTCOME_MEDIA_TYPE,
                payload,
            )
            self.store.append_event(
                connection,
                "WORKSPACE_CHANGE_OUTCOME_RECORDED",
                {
                    "kind": kind,
                    "artifact_id": artifact_id,
                    "artifact_digest": artifact_digest,
                    "approval_digest": payload["approval_digest"],
                    "quote_ref": (f"{payload['quote']['id']}@{payload['quote']['version']}"),
                },
            )
        self.changes.refresh()
        record = self._outcome_record(kind)
        if record is None:  # pragma: no cover - transaction postcondition
            raise RuntimeError("WORKSPACE_OUTCOME_PERSISTENCE_FAILED")
        return record

    @_serialized
    def apply_approved_change(self, kind: str, *, approval_digest: str) -> dict[str, Any]:
        self._require_ungrouped(kind)
        if kind not in self.change_owner:
            raise KeyError(f"UNKNOWN_WORKSPACE_CHANGE:{kind}")
        approval_record = self._approval_record(kind)
        if approval_record is None:
            raise RuntimeError(f"WORKSPACE_APPROVAL_REQUIRED:{kind}")
        approval = Approval.model_validate(approval_record["approval"])
        if approval_digest != approval.digest:
            raise RuntimeError("WORKSPACE_APPROVAL_DIGEST_MISMATCH:apply must bind the persisted approval")
        existing = self._outcome_record(kind)
        if existing is not None:
            outcome_approval_digest = existing["outcome"].get("approval_digest")
            if outcome_approval_digest != approval_digest:
                raise RuntimeError("WORKSPACE_APPLY_COMMAND_CONFLICT")
            preview_record = self._preview_record(kind)
            if preview_record is None:  # pragma: no cover - approval implies preview
                raise RuntimeError(f"WORKSPACE_PREVIEW_REQUIRED:{kind}")
            bundle = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
            self._require_approval_binding(
                kind=kind,
                bundle=bundle,
                approval=approval,
                require_current_predecessor=False,
            )
            self._ensure_terminal_experience()
            return {**existing, "state": self.state()}
        preview_record = self._preview_record(kind)
        if preview_record is None:  # pragma: no cover - approval implies preview
            raise RuntimeError(f"WORKSPACE_PREVIEW_REQUIRED:{kind}")
        bundle = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
        self._require_approval_binding(
            kind=kind,
            bundle=bundle,
            approval=approval,
            require_current_predecessor=False,
        )
        from orgrebase.workspace.approval_authority import verify_approval_authority
        verify_approval_authority(self, kind, approval, current=False)
        if (
            approval.change_set_digest != bundle.change_set.digest
            or approval.preview_digest != bundle.preview.digest
            or approval.minimal_rebase_certificate_digest != bundle.minimal_rebase_certificate.digest
        ):
            raise RuntimeError("WORKSPACE_APPROVAL_BINDING_INVALID")
        recovered = self._recover_apply_result(bundle=bundle, approval=approval)
        if recovered is None:
            self._require_approval_binding(
                kind=kind,
                bundle=bundle,
                approval=approval,
                require_current_predecessor=True,
            )
            if self._change_status(kind) != "APPROVED":
                raise RuntimeError("WORKSPACE_CHANGE_NOT_APPLICABLE")
            result = self._execute_apply(
                kind=kind,
                bundle=bundle,
                approval=approval,
            )
        else:
            result = recovered
        record = self._persist_outcome(kind, result)
        self._ensure_terminal_experience()
        return {**record, "state": self.state()}

    def _run_explicit_change_command(
        self,
        kind: str,
        *,
        rejected_actor_probe: str | None = None,
        probe_stale_approval: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run the same Preview/Approve/Apply commands exposed by the product API."""

        preview_record = self.preview_command(kind)
        bundle = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
        rejection_probe = None
        if rejected_actor_probe is not None:
            before_events = self.store.event_records()
            before_artifacts = self.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            try:
                self.approve_change(
                    kind,
                    actor_id=rejected_actor_probe,
                    preview_digest=bundle.preview.digest,
                )
            except AuthorizationError as exc:
                rejection_probe = {
                    "status": "REJECTED",
                    "actor_id": rejected_actor_probe,
                    "error_code": str(exc),
                    "canonical_target_writes": 0,
                }
            else:  # pragma: no cover - fail-closed acceptance invariant
                raise RuntimeError("WORKSPACE_WRONG_OWNER_PROBE_NOT_REJECTED")
            if (
                self.store.event_records() != before_events
                or self.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
                != before_artifacts
            ):
                raise RuntimeError("WORKSPACE_WRONG_OWNER_PROBE_WROTE_STATE")
        owner_id = self.change_owner[kind]
        approval_record = self.approve_change(
            kind,
            actor_id=owner_id,
            preview_digest=bundle.preview.digest,
        )
        approval = Approval.model_validate(approval_record["approval"])
        stale_approval_probe = None
        if probe_stale_approval:
            before_counts = {
                "events": len(self.store.event_records()),
                "artifacts": self.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
                "object_versions": self.store.connection.execute(
                    "SELECT COUNT(*) FROM object_versions"
                ).fetchone()[0],
                "current_pointers": self.store.connection.execute(
                    "SELECT COUNT(*) FROM current_pointers"
                ).fetchone()[0],
            }
            rejected_digest = "sha256:" + "0" * 64
            if rejected_digest == approval.digest:  # pragma: no cover - digest guard
                rejected_digest = "sha256:" + "1" * 64
            try:
                self.apply_approved_change(kind, approval_digest=rejected_digest)
            except RuntimeError as exc:
                if "WORKSPACE_APPROVAL_DIGEST_MISMATCH" not in str(exc):
                    raise
                after_counts = {
                    "events": len(self.store.event_records()),
                    "artifacts": self.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[
                        0
                    ],
                    "object_versions": self.store.connection.execute(
                        "SELECT COUNT(*) FROM object_versions"
                    ).fetchone()[0],
                    "current_pointers": self.store.connection.execute(
                        "SELECT COUNT(*) FROM current_pointers"
                    ).fetchone()[0],
                }
                if after_counts != before_counts:
                    raise RuntimeError("WORKSPACE_STALE_APPROVAL_PROBE_WROTE_STATE") from exc
                stale_approval_probe = {
                    "status": "REJECTED",
                    "rejected_digest": rejected_digest,
                    "error_code": str(exc).split(":", 1)[0],
                    "before_counts": before_counts,
                    "after_counts": after_counts,
                    "canonical_target_writes": 0,
                }
            else:  # pragma: no cover - fail-closed acceptance invariant
                raise RuntimeError("WORKSPACE_STALE_APPROVAL_PROBE_NOT_REJECTED")
        outcome_record = self.apply_approved_change(
            kind,
            approval_digest=approval.digest,
        )
        if outcome_record["outcome"].get("approval_digest") != approval.digest:
            raise RuntimeError("WORKSPACE_EXPLICIT_APPLY_APPROVAL_BINDING_FAILED")
        result = self._recover_apply_result(bundle=bundle, approval=approval)
        if result is None:  # pragma: no cover - Apply persistence postcondition
            raise RuntimeError("WORKSPACE_EXPLICIT_APPLY_RECEIPT_MISSING")
        command = {
            "kind": kind,
            "mode": EXPLICIT_OWNER_APPROVAL_MODE,
            "input_mode": CONTROLLED_LOCAL_APPROVAL_INPUT_MODE,
            "actor_id": approval.actor_id,
            "preview_artifact_ref": preview_record["artifact_id"],
            "preview_artifact_digest": preview_record["artifact_digest"],
            "preview_digest": bundle.preview.digest,
            "approval_artifact_ref": approval_record["artifact_id"],
            "approval_artifact_digest": approval_record["artifact_digest"],
            "approval_digest": approval.digest,
            "outcome_artifact_ref": outcome_record["artifact_id"],
            "outcome_artifact_digest": outcome_record["artifact_digest"],
            "apply_status": result["rebase_receipt"].status,
        }
        if rejection_probe is not None:
            command["wrong_owner_probe"] = rejection_probe
        if stale_approval_probe is not None:
            command["stale_approval_probe"] = stale_approval_probe
        return result, command

    @classmethod
    def run_explicit_local_product_loop(
        cls,
        store_path: str | Path,
        *,
        allow_scripted_approval: bool = False,
        workflow_run_id: str | None = None,
        runtime_configuration: Any | None = None,
        probe_wrong_owner: bool = False,
    ) -> dict[str, Any]:
        """Execute the controlled-local journey with scripted, persisted owner commands.

        This entrypoint deliberately requires file-backed SQLite so Quote v2 is
        closed and recovered through a fresh service instance before the Finance
        command. It remains a local deterministic run: Domain candidates and
        change advisories are reference providers, Workspace AgentTeams is
        ``NOT_RUN``, and OAC admission is ``NOT_USED_IN_THIS_RUN``.  The
        separately executable local bridge must not be inferred from this Quote run.
        """

        if not allow_scripted_approval:
            raise ValueError("SCRIPTED_APPROVAL_OPT_IN_REQUIRED")

        selected_path = str(store_path)
        if selected_path == ":memory:":
            raise ValueError("WORKSPACE_EXPLICIT_LOOP_REQUIRES_FILE_BACKED_STORE")

        first = cls(
            store_path=selected_path,
            workflow_run_id=workflow_run_id,
            runtime_configuration=runtime_configuration,
        )
        try:
            cls._require_stage(first.state()["stage"], "EMPTY")
            formed = first.form_quote_with_dependency_evidence()
            formation = formed["receipt"]
            tool_invocation = formed["tool_invocation"]
            tool_called_event = formed["tool_called_event"]
            formation_run_id = formed["formation_run_id"]
            launch, launch_command = first._run_explicit_change_command(
                "launch_date",
                rejected_actor_probe=(first.change_owner.get("currency") if probe_wrong_owner else None),
                probe_stale_approval=probe_wrong_owner,
            )
            before_restart = first.state()
            cls._require_stage(before_restart["stage"], "CURRENT")
            before_restart_digest = sha256_digest(before_restart)
        finally:
            first.close()

        reopened = cls.reopen(
            selected_path,
            workflow_run_id=workflow_run_id,
            runtime_configuration=runtime_configuration,
        )
        try:
            after_restart = reopened.state()
            cls._require_stage(after_restart["stage"], "CURRENT")
            after_restart_digest = sha256_digest(after_restart)
            if after_restart_digest != before_restart_digest:
                raise RuntimeError("WORKSPACE_RESTART_STATE_DIGEST_MISMATCH")
            currency, currency_command = reopened._run_explicit_change_command(
                "currency",
                rejected_actor_probe=(
                    reopened.change_owner.get("launch_date") if probe_wrong_owner else None
                ),
                probe_stale_approval=probe_wrong_owner,
            )
            final_state = reopened.state()
            if not final_state["business_complete"]:
                raise RuntimeError("WORKSPACE_REFERENCE_EVENTS_INCOMPLETE")
            final_quote = reopened.current_quote()
            final_pointer = reopened.current_graph_pointer()
            chain = reopened.store.verify_event_chain()
        finally:
            reopened.close()

        return {
            "workflow_run_id": first.effective_workflow_run_id,
            "run_id": first.effective_workflow_run_id,
            "run_nonce": first.workflow_run_nonce,
            "approval_mode": EXPLICIT_OWNER_APPROVAL_MODE,
            "approval_input_mode": CONTROLLED_LOCAL_APPROVAL_INPUT_MODE,
            "boundaries": dict(first.boundaries),
            "approval_commands": {
                "launch_date": launch_command,
                "currency": currency_command,
            },
            "restart": {
                "workspace_state_schema_version": "orgrebase.workspace-state.v2",
                "store_profile": "FILE_BACKED_SQLITE",
                "closed_stage": before_restart["stage"],
                "reopened_stage": after_restart["stage"],
                "state_digest_before_close": before_restart_digest,
                "state_digest_after_reopen": after_restart_digest,
            },
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
            "final_quote": final_quote,
            "final_graph_pointer": final_pointer,
            "event_chain": chain,
        }
