"""Authority-separated candidate collaboration with AgentTeams-compatible traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orgrebase.agentteams_ingest import ingest_local_proposals
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentIdentity,
    AgentRun,
    ChangeSetRevision,
    CompilationReceipt,
    CoordinationReceipt,
    DelegationTask,
    EvidenceClass,
    ImpactPreview,
    IntegrityError,
    OrchestrationPlan,
    RunEnvelope,
    StructuredHandoff,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.store import StateStore
from orgrebase.tools import DEPENDENCY_EVIDENCE_CONTRACT, DependencyEvidenceTool

TASK_INTENTS_PATH = runtime_asset_path("orchestration/task-intents.json")
IDENTITIES_DIR = runtime_asset_path("agentteams/identities")

def load_task_intents(path: Path | None = None) -> tuple[dict[str, Any], str]:
    intents_path = path or TASK_INTENTS_PATH
    payload = json.loads(intents_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "orgrebase.task-intents.v1":
        raise IntegrityError("task intents schema is not orgrebase.task-intents.v1")
    intents = payload.get("intents")
    if not isinstance(intents, list) or not intents:
        raise IntegrityError("task intents file has no intents")
    return payload, sha256_digest(payload)


def load_disk_identities(directory: Path | None = None) -> dict[str, AgentIdentity]:
    identity_dir = directory or IDENTITIES_DIR
    identities: dict[str, AgentIdentity] = {}
    for path in sorted(identity_dir.glob("*.json")):
        item = AgentIdentity.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if item.name in identities:
            raise IntegrityError(f"duplicate disk Agent identity: {item.name}")
        identities[item.name] = item
    if not identities:
        raise IntegrityError("no disk Agent identity contracts found")
    return identities


def _identity_payload(item: AgentIdentity) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    payload.pop("digest", None)
    return payload


class OrchestrationCompiler:
    """Compile declared Agent identities into a least-authority, digest-bound task DAG."""

    version = "orgrebase.authority-aware-orchestration-compiler@1.0.0"
    invariants = (
        "exactly_one_task_per_declared_agent",
        "acyclic_declared_dependencies",
        "capabilities_and_outputs_are_identity_subsets",
        "all_tasks_bind_exact_changeset_preview_and_revision_lock",
        "candidate_only_no_normative_state_writer",
    )

    def __init__(
        self,
        fixture: EnterpriseFixture,
        *,
        identities_dir: Path | None = None,
        intents_path: Path | None = None,
    ) -> None:
        self.fixture = fixture
        self.identities = {identity.name: identity for identity in fixture.agents}
        self._identities_dir = identities_dir or IDENTITIES_DIR
        self._intents_path = intents_path or TASK_INTENTS_PATH
        self._disk_identities = load_disk_identities(self._identities_dir)
        self._assert_identities_match_disk()

    def _assert_identities_match_disk(self) -> None:
        if set(self._disk_identities) != set(self.identities):
            raise IntegrityError("disk Agent identities do not cover the declared team")
        for name, ident in self.identities.items():
            if _identity_payload(ident) != _identity_payload(self._disk_identities[name]):
                raise IntegrityError(f"Agent identity drifted from disk contract: {name}")

    def compile(
        self, change_set: ChangeSetRevision, preview: ImpactPreview
    ) -> OrchestrationPlan:
        self._assert_identities_match_disk()
        payload, _intents_digest = load_task_intents(self._intents_path)
        input_refs = tuple(
            sorted((change_set.digest, preview.digest, preview.revision_lock.digest))
        )
        tasks = tuple(
            DelegationTask(
                id=str(blueprint["id"]),
                agent_name=str(blueprint["agent"]),
                authority_domain=self.identities[str(blueprint["agent"])].authority_domain,
                purpose=str(blueprint["purpose"]),
                depends_on=tuple(blueprint["depends_on"]),
                input_refs=input_refs,
                required_capabilities=tuple(blueprint["required_capabilities"]),
                allowed_output_kinds=tuple(blueprint["allowed_outputs"]),
                context_scope=tuple(blueprint["context_scope"]),
                failure_disposition=str(blueprint["failure"]),
            )
            for blueprint in payload["intents"]
        )
        plan = OrchestrationPlan(
            id=str(payload.get("plan_id", "orchestration-plan:launch-change@1")),
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            revision_lock_digest=preview.revision_lock.digest,
            tasks=tasks,
            invariants=self.invariants,
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        self.verify_plan(plan)
        return plan

    def compilation_receipt(
        self,
        plan: OrchestrationPlan,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
    ) -> CompilationReceipt:
        _payload, intents_digest = load_task_intents(self._intents_path)
        self._assert_identities_match_disk()
        if (
            plan.change_set_digest != change_set.digest
            or plan.preview_digest != preview.digest
        ):
            raise IntegrityError("compilation receipt is not bound to ChangeSet/Preview")
        return CompilationReceipt(
            id=f"compilation:{plan.id}",
            task_intents_digest=intents_digest,
            identity_digests=tuple(
                {"name": name, "digest": self._disk_identities[name].digest}
                for name in sorted(self._disk_identities)
            ),
            orchestration_plan_digest=plan.digest,
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            source_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            claim_boundary=(
                "Compilation binds disk identity contracts and frozen task intents. "
                "It does not grant Apply or canonical-write authority."
            ),
        )

    def verify_plan(self, plan: OrchestrationPlan) -> None:
        expected_inputs = {
            plan.change_set_digest,
            plan.preview_digest,
            plan.revision_lock_digest,
        }
        task_ids: set[str] = set()
        agent_names: set[str] = set()
        for task in plan.tasks:
            if task.id in task_ids or task.agent_name in agent_names:
                raise IntegrityError("orchestration plan duplicates a task or Agent")
            if not set(task.depends_on).issubset(task_ids):
                raise IntegrityError("orchestration plan is cyclic or not topologically ordered")
            identity = self.identities.get(task.agent_name)
            if identity is None or task.authority_domain != identity.authority_domain:
                raise IntegrityError("orchestration task violates declared Agent authority")
            if not set(task.required_capabilities).issubset(identity.capabilities):
                raise IntegrityError("orchestration task requires undeclared Agent capability")
            if not set(task.allowed_output_kinds).issubset(identity.outputs):
                raise IntegrityError("orchestration task permits undeclared Agent output")
            if set(task.input_refs) != expected_inputs or not task.candidate_only:
                raise IntegrityError("orchestration task is not exactly input-bound candidate work")
            task_ids.add(task.id)
            agent_names.add(task.agent_name)
        if agent_names != set(self.identities) or tuple(plan.invariants) != self.invariants:
            raise IntegrityError("orchestration plan does not cover the declared five-Agent team")

    def verify_execution(
        self,
        *,
        plan: OrchestrationPlan,
        handoffs: tuple[StructuredHandoff, ...],
        runs: tuple[AgentRun, ...],
        run_envelope: RunEnvelope,
    ) -> CoordinationReceipt:
        self.verify_plan(plan)
        task_by_id = {task.id: task for task in plan.tasks}
        handoff_by_task = {handoff.task_id: handoff for handoff in handoffs}
        run_by_task = {run.task_id: run for run in runs}
        if (
            len(handoffs) != len(plan.tasks)
            or len(runs) != len(plan.tasks)
            or len(handoff_by_task) != len(handoffs)
            or len(run_by_task) != len(runs)
            or set(task_by_id) != set(handoff_by_task)
            or set(task_by_id) != set(run_by_task)
        ):
            raise IntegrityError("orchestration execution does not exactly cover the compiled DAG")
        for task in plan.tasks:
            handoff = handoff_by_task[task.id]
            run = run_by_task[task.id]
            predecessor_runs = tuple(run_by_task[item].id for item in task.depends_on)
            allowed_parents = set(predecessor_runs) | {None}
            if (
                handoff.from_agent != task.agent_name
                or handoff.orchestration_plan_digest != plan.digest
                or handoff.delegation_task_digest != task.digest
                or set(handoff.input_refs) != set(task.input_refs)
                or tuple(handoff.payload.get("input_refs", ())) != task.input_refs
                or handoff.payload.get("kind") not in task.allowed_output_kinds
                or not handoff.candidate_only
            ):
                raise IntegrityError("handoff escaped its compiled delegation task")
            if (
                run.agent_name != task.agent_name
                or run.workflow_run_id != run_envelope.run_id
                or run.run_nonce != run_envelope.nonce
                or run.predecessor_run_ids != predecessor_runs
                or run.parent_run_id not in allowed_parents
                or run.input_digest != sha256_digest(task.input_refs)
                or run.output_digest != sha256_digest(handoff.payload)
            ):
                raise IntegrityError("Agent run is not bound to its compiled handoff")
        return CoordinationReceipt(
            id="coordination:launch-change@1",
            orchestration_plan_digest=plan.digest,
            workflow_run_id=run_envelope.run_id,
            run_nonce=run_envelope.nonce,
            handoff_digests=tuple(item.digest for item in handoffs),
            agent_run_digests=tuple(item.digest for item in runs),
            checked_invariants=self.invariants,
            status="PASS",
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )


class CollaborationAdapter:
    """Deterministic replay adapter for the five AgentTeams identities.

    This intentionally produces candidate artifacts only. A live AgentTeams adapter can
    replace the transport without gaining control-plane write authority.
    """

    runtime_profile = "runtime:agentteams-v1.2.2-adapter@v1"
    model_version = "model:deterministic-reference@v1"
    policy_versions = ("policy:agent-boundaries@r1",)
    tool_versions = ("tool:none-proposal-only@v1",)

    def __init__(self, fixture: EnterpriseFixture, store: StateStore | None = None) -> None:
        self.fixture = fixture
        self.store = store
        self.orchestration_compiler = OrchestrationCompiler(fixture)
        self.dependency_tool = DependencyEvidenceTool(fixture, store) if store is not None else None

    def validate_identities(self) -> dict[str, Any]:
        required_fields = (
            "name",
            "role",
            "capabilities",
            "inputs",
            "outputs",
            "dependencies",
            "decision_boundary",
            "trace",
        )
        failures: list[str] = []
        for identity in self.fixture.agents:
            for field in required_fields:
                if not getattr(identity, field):
                    failures.append(f"{identity.name}:{field}")
        return {
            "status": "PASS" if not failures else "FAIL",
            "agent_count": len(self.fixture.agents),
            "required_fields": list(required_fields),
            "failures": failures,
            "agentteams_version": "v1.2.2",
            "evidence_class": EvidenceClass.PASS_STATIC.value,
        }

    @staticmethod
    def _payload(
        agent_name: str,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        input_refs: tuple[str, ...],
    ) -> dict[str, Any]:
        payloads: dict[str, dict[str, Any]] = {
            "change-coordinator": {
                "kind": "TaskGraph",
                "tasks": ["interpret-delta", "inspect-domain-boundaries", "inspect-work", "patch-skill"],
                "requested_state_change": None,
            },
            "product-steward": {
                "kind": "ClaimDeltaCandidate",
                "semantic_classification": "SEMANTIC_DELTA",
                "source_refs": ["source:product-release-plan@v13"],
                "requested_state_change": "CURRENT",
                "note": "candidate; Product Owner admission still required",
            },
            "legal-steward": {
                "kind": "MinimalClaimCandidate",
                "claim": {"legal.customer_notice_required": True},
                "full_contract_disclosed": False,
                "impact_candidate": "UNAFFECTED",
            },
            "gtm-steward": {
                "kind": "ImpactCandidate",
                "candidate_targets": ["work:sales_quote_a", "work:support_doc_b"],
                "coverage_gap": ["work:partner_brief_e"],
                "requested_state_change": "STALE",
            },
            "skill-curator": {
                "kind": "SkillPatchCandidate",
                "from_version": "1.2",
                "candidate_version": "1.3",
                "patch": "map typed classifications to bounded actions; never broadcast by department list",
                "publish_requested": False,
                "suspected_confounders": ["synthetic fixture", "small matched trajectory set"],
            },
        }
        return {
            **payloads[agent_name],
            "input_refs": input_refs,
            "change_set_digest": change_set.digest,
            "preview_digest": preview.digest,
            "revision_lock_digest": preview.revision_lock.digest,
        }

    def run(
        self,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        run_envelope: RunEnvelope,
    ) -> dict[str, Any]:
        plan = self.orchestration_compiler.compile(change_set, preview)
        compilation_receipt = self.orchestration_compiler.compilation_receipt(
            plan, change_set, preview
        )
        task_by_agent = {task.agent_name: task for task in plan.tasks}
        handoffs: list[StructuredHandoff] = []
        runs: list[AgentRun] = []
        tool_invocations: list[dict[str, Any]] = []
        if self.dependency_tool is not None:
            tool_invocations.append(
                self.dependency_tool.invoke(
                    actor_id="gtm-steward",
                    workflow_run_id=run_envelope.run_id,
                    run_nonce=run_envelope.nonce,
                    target_ids=tuple(self.fixture.impact_targets),
                    graph_revision=self.fixture.revisions["graph"],
                    idempotency_key="launch-impact-r1",
                )
            )
        run_id_by_task = {
            task.id: f"run:{task.agent_name}@1" for task in plan.tasks
        }
        for identity in self.fixture.agents:
            task = task_by_agent[identity.name]
            payload = self._payload(identity.name, change_set, preview, task.input_refs)
            handoff = StructuredHandoff(
                id=f"handoff:{identity.name}@1",
                schema_version="orgrebase.handoff.v1",
                task_id=task.id,
                change_set_id=f"{change_set.id}@{change_set.revision}",
                graph_revision=self.fixture.revisions["graph"],
                workflow_run_id=run_envelope.run_id,
                run_nonce=run_envelope.nonce,
                orchestration_plan_digest=plan.digest,
                delegation_task_digest=task.digest,
                input_refs=task.input_refs,
                from_agent=identity.name,
                to_agent="orgrebase-control-plane",
                payload=payload,
                candidate_only=True,
            )
            handoffs.append(handoff)
            output_digest = sha256_digest(payload)
            runs.append(
                AgentRun(
                    id=f"run:{identity.name}@1",
                    agent_name=identity.name,
                    task_id=task.id,
                    workflow_run_id=run_envelope.run_id,
                    run_nonce=run_envelope.nonce,
                    status="TRUSTED_COMPLETE",
                    model_version=self.model_version,
                    runtime_profile_version=self.runtime_profile,
                    context_manifest_version=f"context:{identity.name}@v1",
                    skill_versions=(
                        "skill:enterprise-launch-readiness@1.2"
                        if identity.name == "skill-curator"
                        else "skill:structured-domain-handoff@1.0",
                    ),
                    policy_versions=self.policy_versions,
                    tool_versions=(
                        (
                            f"{DEPENDENCY_EVIDENCE_CONTRACT.id}"
                            f"@{DEPENDENCY_EVIDENCE_CONTRACT.version}"
                        ,)
                        if identity.name == "gtm-steward" and tool_invocations
                        else self.tool_versions
                    ),
                    input_digest=sha256_digest(task.input_refs),
                    output_digest=output_digest,
                    parent_run_id=(
                        run_id_by_task[task.depends_on[0]] if task.depends_on else None
                    ),
                    predecessor_run_ids=tuple(
                        run_id_by_task[dependency] for dependency in task.depends_on
                    ),
                    trace_id=f"trace-{sha256_digest(task.id)[7:23]}",
                    evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
                )
            )
        handoff_tuple = tuple(handoffs)
        run_tuple = tuple(runs)
        coordination_receipt = self.orchestration_compiler.verify_execution(
            plan=plan,
            handoffs=handoff_tuple,
            runs=run_tuple,
            run_envelope=run_envelope,
        )
        candidate_ingestion = ingest_local_proposals(
            change_set=change_set,
            preview=preview,
            plan=plan,
            handoffs=handoff_tuple,
            coordination_receipt=coordination_receipt,
            run_envelope=run_envelope,
        )
        if candidate_ingestion.rejected_candidate_digests:
            raise IntegrityError("local proposal ingestion rejected a bound candidate")
        return {
            "task_graph": {
                "id": plan.id,
                "manager_workers": "AgentTeams v1.2.2",
                "agent_count": len(runs),
                "candidate_only": True,
                "plan_digest": plan.digest,
            },
            "orchestration_plan": plan,
            "compilation_receipt": compilation_receipt,
            "coordination_receipt": coordination_receipt,
            "candidate_ingestion": candidate_ingestion,
            "run_envelope": run_envelope,
            "handoffs": handoff_tuple,
            "agent_runs": run_tuple,
            "tool_invocations": tool_invocations,
            "identity_validation": self.validate_identities(),
            "live_runtime": {
                "status": EvidenceClass.NOT_RUN.value,
                "reason": "No AgentTeams deployment credentials/runtime were supplied to this clean project",
            },
        }

    @staticmethod
    def prove_agents_cannot_decide_state(collaboration: dict[str, Any], preview: ImpactPreview) -> bool:
        receipt = collaboration["coordination_receipt"]
        if getattr(receipt, "status", None) != "PASS":
            return False
        handoffs = collaboration["handoffs"]
        if not handoffs:
            return False
        canonical_classifications = {result.classification.value for result in preview.results}
        control_plane_kinds = {
            "ImpactPreview",
            "ImpactCertificate",
            "MinimalRebaseCertificate",
            "RebaseReceipt",
            "Approval",
        }
        for handoff in handoffs:
            payload = handoff.payload
            if not handoff.candidate_only or payload.get("kind") in control_plane_kinds:
                return False
            if payload.get("publish_requested"):
                return False
            if payload.get("impact_candidate") in canonical_classifications:
                return False
        return True
