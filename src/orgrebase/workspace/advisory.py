"""Workspace-scoped, zero-write advisory evidence for Apply compatibility."""

from __future__ import annotations

from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentCandidateDecision,
    AgentCandidateIngestionReceipt,
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
from orgrebase.workspace.models import VerifiedAdvisoryBundle


class WorkspaceChangeAdvisoryAdapter:
    version = "workspace-change-advisory@1.0.0"

    @staticmethod
    def _domain_agent(change_set: ChangeSetRevision) -> str:
        object_id = change_set.deltas[0].object_id
        if object_id.startswith("policy:finance"):
            return "finance-steward"
        if object_id.startswith("claim:product"):
            return "product-steward"
        return "domain-steward"

    def run(
        self,
        *,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        run_envelope: RunEnvelope,
    ) -> dict[str, Any]:
        domain_agent = self._domain_agent(change_set)
        refs = tuple(sorted((change_set.digest, preview.digest, preview.revision_lock.digest)))
        tasks = (
            DelegationTask(
                id=f"workspace-advisory:{change_set.id}:01",
                agent_name="change-coordinator",
                authority_domain="coordination",
                purpose="bind the admitted workspace change to advisory tasks",
                input_refs=refs,
                required_capabilities=("decompose ChangeSet",),
                allowed_output_kinds=("TaskGraph",),
                context_scope=("ChangeSetRevision", "ImpactPreview"),
                failure_disposition="ABSTAIN",
            ),
            DelegationTask(
                id=f"workspace-advisory:{change_set.id}:02",
                agent_name=domain_agent,
                authority_domain=("finance" if domain_agent == "finance-steward" else "product"),
                purpose="explain the changed authority-domain premise without admitting effects",
                depends_on=(f"workspace-advisory:{change_set.id}:01",),
                input_refs=refs,
                required_capabilities=("propose semantic explanation",),
                allowed_output_kinds=("SemanticExplanation",),
                context_scope=(change_set.deltas[0].object_id,),
                failure_disposition="ABSTAIN",
            ),
            DelegationTask(
                id=f"workspace-advisory:{change_set.id}:03",
                agent_name="gtm-steward",
                authority_domain="gtm",
                purpose="explain quote impact candidates without changing canonical state",
                depends_on=(f"workspace-advisory:{change_set.id}:02",),
                input_refs=refs,
                required_capabilities=("propose impact candidates",),
                allowed_output_kinds=("ImpactCandidate",),
                context_scope=("work:quote_acme",),
                failure_disposition="UNKNOWN",
            ),
        )
        plan = OrchestrationPlan(
            id=f"workspace-orchestration:{change_set.id}@{change_set.revision}",
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            revision_lock_digest=preview.revision_lock.digest,
            tasks=tasks,
            invariants=(
                "candidate_only_no_normative_state_writer",
                "exact_change_preview_revision_binding",
                "target_writes_zero",
            ),
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        handoffs: list[StructuredHandoff] = []
        runs: list[AgentRun] = []
        for index, task in enumerate(tasks, start=1):
            kind = task.allowed_output_kinds[0]
            payload = {
                "kind": kind,
                "input_refs": task.input_refs,
                "candidate_only": True,
                "target_writes": 0,
                "change_object_id": change_set.deltas[0].object_id,
                "preview_digest": preview.digest,
            }
            handoff = StructuredHandoff(
                id=f"workspace-handoff:{change_set.id}:{index}",
                schema_version="orgrebase.handoff.v1",
                task_id=task.id,
                change_set_id=f"{change_set.id}@{change_set.revision}",
                graph_revision=preview.revision_lock.graph_revision,
                workflow_run_id=run_envelope.run_id,
                run_nonce=run_envelope.nonce,
                orchestration_plan_digest=plan.digest,
                delegation_task_digest=task.digest,
                input_refs=task.input_refs,
                from_agent=task.agent_name,
                to_agent="orgrebase-control-plane",
                payload=payload,
                candidate_only=True,
            )
            handoffs.append(handoff)
            predecessor_ids = tuple(
                f"workspace-run:{change_set.id}:{tasks.index(next(t for t in tasks if t.id == dep)) + 1}"
                for dep in task.depends_on
            )
            runs.append(
                AgentRun(
                    id=f"workspace-run:{change_set.id}:{index}",
                    agent_name=task.agent_name,
                    task_id=task.id,
                    workflow_run_id=run_envelope.run_id,
                    run_nonce=run_envelope.nonce,
                    status="TRUSTED_COMPLETE",
                    model_version="model:deterministic-reference@v1",
                    runtime_profile_version="runtime:workspace-advisory@v1",
                    context_manifest_version=f"context:{task.agent_name}@v1",
                    skill_versions=("skill:structured-domain-handoff@1.0",),
                    policy_versions=("policy:workspace-agent-boundaries@r1",),
                    tool_versions=("tool:none-proposal-only@v1",),
                    input_digest=sha256_digest(task.input_refs),
                    output_digest=sha256_digest(payload),
                    parent_run_id=predecessor_ids[0] if predecessor_ids else None,
                    predecessor_run_ids=predecessor_ids,
                    trace_id=f"trace-{sha256_digest(task.id)[7:23]}",
                    evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
                )
            )
        coordination = CoordinationReceipt(
            id=f"workspace-coordination:{change_set.id}@{change_set.revision}",
            orchestration_plan_digest=plan.digest,
            workflow_run_id=run_envelope.run_id,
            run_nonce=run_envelope.nonce,
            handoff_digests=tuple(item.digest for item in handoffs),
            agent_run_digests=tuple(item.digest for item in runs),
            checked_invariants=plan.invariants,
            status="PASS",
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        decisions = tuple(
            AgentCandidateDecision(
                artifact_ref=item.id,
                artifact_digest=item.digest,
                producer_worker=item.from_agent,
                decision="ADVISORY_ACCEPTED",
                reason_codes=("EXACT_TASK_BINDING", "ZERO_WRITE_CANDIDATE"),
                admitted_effects=(),
            )
            for item in handoffs
        )
        ingestion = AgentCandidateIngestionReceipt(
            id=f"workspace-ingestion:{change_set.id}@{change_set.revision}",
            live_receipt_digest=coordination.digest,
            run_id=run_envelope.run_id,
            nonce=run_envelope.nonce,
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            orchestration_plan_digest=plan.digest,
            decisions=decisions,
            admitted_candidate_digests=tuple(item.digest for item in handoffs),
            rejected_candidate_digests=(),
            target_writes=0,
            source_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            verifier_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            claim_boundary="Workspace advisory candidates are accepted for explanation only and cannot write effects.",
        )
        compilation = CompilationReceipt(
            id=f"workspace-compilation:{change_set.id}@{change_set.revision}",
            task_intents_digest=sha256_digest(
                [task.model_dump(mode="json") for task in tasks]
            ),
            identity_digests=tuple(
                {"name": task.agent_name, "digest": sha256_digest((task.agent_name, task.authority_domain))}
                for task in tasks
            ),
            orchestration_plan_digest=plan.digest,
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            source_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            claim_boundary="Compilation proves the workspace advisory DAG is exactly bound; it grants no Apply authority.",
        )
        return {
            "orchestration_plan": plan,
            "compilation_receipt": compilation,
            "coordination_receipt": coordination,
            "candidate_ingestion": ingestion,
            "handoffs": tuple(handoffs),
            "agent_runs": tuple(runs),
        }


class WorkspaceApplyAdvisoryVerifier:
    def __init__(self, adapter: WorkspaceChangeAdvisoryAdapter | None = None) -> None:
        self.adapter = adapter or WorkspaceChangeAdvisoryAdapter()

    def verify(
        self,
        *,
        fixture: Any,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        collaboration: dict[str, Any],
        run_envelope: RunEnvelope,
    ) -> VerifiedAdvisoryBundle:
        expected = self.adapter.run(
            change_set=change_set,
            preview=preview,
            run_envelope=run_envelope,
        )
        names = (
            "orchestration_plan",
            "compilation_receipt",
            "coordination_receipt",
            "candidate_ingestion",
        )
        for name in names:
            submitted = collaboration.get(name)
            if submitted is None or submitted.digest != expected[name].digest:
                raise IntegrityError(f"WORKSPACE_ADVISORY_MISMATCH:{name}")
        if tuple(item.digest for item in collaboration.get("handoffs", ())) != tuple(
            item.digest for item in expected["handoffs"]
        ):
            raise IntegrityError("WORKSPACE_ADVISORY_MISMATCH:handoffs")
        if tuple(item.digest for item in collaboration.get("agent_runs", ())) != tuple(
            item.digest for item in expected["agent_runs"]
        ):
            raise IntegrityError("WORKSPACE_ADVISORY_MISMATCH:agent_runs")
        ingestion = expected["candidate_ingestion"]
        if ingestion.target_writes != 0 or any(item.admitted_effects for item in ingestion.decisions):
            raise IntegrityError("WORKSPACE_ADVISORY_NOT_ZERO_WRITE")
        return VerifiedAdvisoryBundle(
            orchestration_plan=expected["orchestration_plan"],
            compilation_receipt=expected["compilation_receipt"],
            coordination_receipt=expected["coordination_receipt"],
            ingestion_receipt=ingestion,
            handoffs=expected["handoffs"],
            agent_runs=expected["agent_runs"],
        )
