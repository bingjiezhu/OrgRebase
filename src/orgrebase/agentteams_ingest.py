"""Fail-closed bridge from probabilistic AgentTeams candidates to the control plane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentCandidateDecision,
    AgentCandidateIngestionReceipt,
    ChangeSetRevision,
    CoordinationReceipt,
    EvidenceClass,
    ImpactPreview,
    IntegrityError,
    OrchestrationPlan,
    RunEnvelope,
    StructuredHandoff,
)

REQUIRED_PROHIBITIONS = {
    "no_canonical_write",
    "no_approval",
    "no_apply",
    "no_cross_domain_authority",
}


def _raw_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _safe_artifact(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise IntegrityError(f"unsafe or missing AgentTeams artifact: {relative}")
    return candidate


def assert_zero_write_ingestion(receipt: AgentCandidateIngestionReceipt) -> None:
    if receipt.target_writes != 0:
        raise IntegrityError("candidate ingestion granted target writes")
    if any(item.admitted_effects for item in receipt.decisions):
        raise IntegrityError("candidate ingestion admitted effects")


def ingest_local_proposals(
    *,
    change_set: ChangeSetRevision,
    preview: ImpactPreview,
    plan: OrchestrationPlan,
    handoffs: tuple[StructuredHandoff, ...],
    coordination_receipt: CoordinationReceipt,
    run_envelope: RunEnvelope,
) -> AgentCandidateIngestionReceipt:
    """Admit local adapter handoffs as advice only; never grant Apply effects."""

    if (
        plan.change_set_digest != change_set.digest
        or plan.preview_digest != preview.digest
        or plan.revision_lock_digest != preview.revision_lock.digest
    ):
        raise IntegrityError("local ingestion plan is not bound to ChangeSet/Preview")
    if (
        coordination_receipt.orchestration_plan_digest != plan.digest
        or coordination_receipt.workflow_run_id != run_envelope.run_id
        or coordination_receipt.run_nonce != run_envelope.nonce
        or coordination_receipt.status != "PASS"
    ):
        raise IntegrityError("local ingestion coordination receipt is not bound")
    if len({item.id for item in handoffs}) != len(handoffs):
        raise IntegrityError("local ingestion has duplicate candidate refs")

    task_by_id = {task.id: task for task in plan.tasks}
    decisions: list[AgentCandidateDecision] = []
    for handoff in sorted(handoffs, key=lambda item: item.id):
        reasons: list[str] = []
        task = task_by_id.get(handoff.task_id)
        payload = handoff.payload if isinstance(handoff.payload, dict) else {}
        if (
            not handoff.candidate_only
            or handoff.schema_version != "orgrebase.handoff.v1"
            or handoff.workflow_run_id != run_envelope.run_id
            or handoff.run_nonce != run_envelope.nonce
            or not isinstance(handoff.payload, dict)
        ):
            reasons.append("CANDIDATE_ENVELOPE_INVALID")
        if task is None or list(handoff.input_refs) != list(task.input_refs):
            reasons.append("EXACT_INPUT_BINDING_MISSING")
        if (
            task is None
            or handoff.orchestration_plan_digest != plan.digest
            or handoff.delegation_task_digest != task.digest
            or payload.get("change_set_digest") != change_set.digest
            or payload.get("preview_digest") != preview.digest
            or payload.get("revision_lock_digest") != preview.revision_lock.digest
        ):
            reasons.append("ORCHESTRATION_BINDING_MISSING")
        if task is None or payload.get("kind") not in task.allowed_output_kinds:
            reasons.append("UNDECLARED_OUTPUT_KIND")
        decision = "REJECTED" if reasons else "ADVISORY_ACCEPTED"
        decisions.append(
            AgentCandidateDecision(
                artifact_ref=handoff.id,
                artifact_digest=handoff.digest,
                producer_worker=handoff.from_agent,
                decision=decision,
                reason_codes=tuple(sorted(set(reasons))) or ("EXACTLY_BOUND_ADVISORY",),
                admitted_effects=(),
            )
        )
    admitted = tuple(
        item.artifact_digest
        for item in decisions
        if item.decision == "ADVISORY_ACCEPTED"
    )
    rejected = tuple(
        item.artifact_digest for item in decisions if item.decision == "REJECTED"
    )
    receipt = AgentCandidateIngestionReceipt(
        id=f"agent-candidate-ingestion:local:{str(run_envelope.run_id).rsplit(':', 1)[-1]}",
        live_receipt_digest=coordination_receipt.digest,
        run_id=run_envelope.run_id,
        nonce=run_envelope.nonce,
        change_set_digest=change_set.digest,
        preview_digest=preview.digest,
        orchestration_plan_digest=plan.digest,
        decisions=tuple(decisions),
        admitted_candidate_digests=admitted,
        rejected_candidate_digests=rejected,
        target_writes=0,
        source_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        verifier_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        claim_boundary=(
            "Candidates may contribute bound advice only. This receipt grants no "
            "approval, canonical-state, or Apply authority."
        ),
    )
    assert_zero_write_ingestion(receipt)
    return receipt


class AgentTeamsCandidateIngestor:
    """Validate candidate provenance and admit advice without granting write power."""

    version = "orgrebase.agent-candidate-ingestor@1.0.0"

    def ingest(
        self,
        *,
        live_receipt: dict[str, Any],
        artifact_manifest: dict[str, Any],
        artifact_root: Path,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        orchestration_plan: OrchestrationPlan,
    ) -> AgentCandidateIngestionReceipt:
        if (
            orchestration_plan.change_set_digest != change_set.digest
            or orchestration_plan.preview_digest != preview.digest
            or orchestration_plan.revision_lock_digest != preview.revision_lock.digest
        ):
            raise IntegrityError("orchestration plan is not bound to the current inputs")
        evidence = live_receipt.get("evidence")
        if (
            live_receipt.get("status") != "PASS"
            or live_receipt.get("evidence_class") != EvidenceClass.LIVE_AGENTTEAMS.value
            or not isinstance(evidence, dict)
            or live_receipt.get("receipt_digest") != sha256_digest(evidence)
        ):
            raise IntegrityError("invalid LIVE_AGENTTEAMS receipt")
        run_id = evidence.get("run_id")
        nonce = evidence.get("nonce")
        matrix = evidence.get("matrix")
        if (
            not isinstance(matrix, dict)
            or matrix.get("orchestration_plan_digest") != orchestration_plan.digest
        ):
            raise IntegrityError("live run is not bound to the current orchestration plan")
        matrix_bindings_raw = matrix.get("candidate_task_bindings")
        if not isinstance(matrix_bindings_raw, list):
            raise IntegrityError("live run has no candidate task bindings")
        matrix_bindings = {
            item.get("worker_name"): item
            for item in matrix_bindings_raw
            if isinstance(item, dict) and isinstance(item.get("worker_name"), str)
        }
        if len(matrix_bindings) != len(matrix_bindings_raw):
            raise IntegrityError("live run has ambiguous candidate task bindings")
        if (
            artifact_manifest.get("schema_version")
            != "orgrebase.agentteams-artifact-manifest.v1"
            or artifact_manifest.get("run_id") != run_id
            or artifact_manifest.get("nonce") != nonce
        ):
            raise IntegrityError("AgentTeams artifact manifest is not bound to the live run")

        live_artifact_list = [
            item
            for item in evidence.get("artifacts", {}).get("candidate_artifacts", [])
            if item.get("ref") != "result:summary"
        ]
        live_refs = [item["ref"] for item in live_artifact_list]
        if len(set(live_refs)) != len(live_refs):
            raise IntegrityError("live run has duplicate candidate refs")
        live_candidates = {item["ref"]: item["digest"] for item in live_artifact_list}
        manifest_items = {
            item["ref"]: item
            for item in artifact_manifest.get("artifacts", [])
            if item.get("ref") != "result:summary"
        }
        if set(live_candidates) != set(manifest_items):
            raise IntegrityError("AgentTeams candidate set differs from the live receipt")

        task_by_worker = {
            task.agent_name: task
            for task in orchestration_plan.tasks
            if task.agent_name != "change-coordinator"
        }
        decisions: list[AgentCandidateDecision] = []
        for artifact_ref in sorted(live_candidates):
            declared = manifest_items[artifact_ref]
            expected_digest = live_candidates[artifact_ref]
            if declared.get("digest") != expected_digest:
                raise IntegrityError(f"candidate digest mismatch: {artifact_ref}")
            path = _safe_artifact(artifact_root, str(declared.get("path", "")))
            data = path.read_bytes()
            if _raw_digest(data) != expected_digest:
                raise IntegrityError(f"candidate bytes mismatch: {artifact_ref}")
            try:
                candidate = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IntegrityError(f"candidate is not valid JSON: {artifact_ref}") from exc
            if not isinstance(candidate, dict):
                candidate = {}
                reasons: list[str] = ["CANDIDATE_ENVELOPE_INVALID"]
            else:
                reasons = []
            if (
                "CANDIDATE_ENVELOPE_INVALID" not in reasons
                and (
                    candidate.get("schema_version") != "orgrebase.candidate-result.v1"
                    or candidate.get("run_id") != run_id
                    or candidate.get("nonce") != nonce
                    or candidate.get("worker_name") != declared.get("producer_worker")
                    or candidate.get("candidate_only") is not True
                )
            ):
                reasons.append("CANDIDATE_ENVELOPE_INVALID")
            producer_worker = str(declared.get("producer_worker", ""))
            task = task_by_worker.get(producer_worker)
            candidate_input_refs = candidate.get("input_refs")
            required_refs = list(task.input_refs) if task is not None else []
            if candidate_input_refs != required_refs:
                reasons.append("EXACT_INPUT_BINDING_MISSING")
            if (
                task is None
                or candidate.get("orchestration_plan_digest") != orchestration_plan.digest
                or candidate.get("delegation_task_digest") != task.digest
                or producer_worker not in matrix_bindings
                or matrix_bindings[producer_worker].get("delegation_task_digest")
                != task.digest
                or matrix_bindings[producer_worker].get("input_refs")
                != list(task.input_refs)
            ):
                reasons.append("ORCHESTRATION_BINDING_MISSING")
            if not REQUIRED_PROHIBITIONS.issubset(
                set(candidate.get("prohibited_actions_respected", []))
            ):
                reasons.append("AUTHORITY_BOUNDARY_UNPROVEN")
            output = candidate.get("output")
            if not isinstance(output, dict) or not output:
                reasons.append("STRUCTURED_OUTPUT_MISSING")
            elif task is not None and not set(output).issubset(task.allowed_output_kinds):
                reasons.append("UNDECLARED_OUTPUT_KIND")

            decision = "REJECTED" if reasons else "ADVISORY_ACCEPTED"
            decisions.append(
                AgentCandidateDecision(
                    artifact_ref=artifact_ref,
                    artifact_digest=expected_digest,
                    producer_worker=producer_worker,
                    decision=decision,
                    reason_codes=tuple(sorted(reasons)) or ("EXACTLY_BOUND_ADVISORY",),
                    admitted_effects=(),
                )
            )

        admitted = tuple(
            item.artifact_digest
            for item in decisions
            if item.decision == "ADVISORY_ACCEPTED"
        )
        rejected = tuple(
            item.artifact_digest for item in decisions if item.decision == "REJECTED"
        )
        receipt = AgentCandidateIngestionReceipt(
            id=f"agent-candidate-ingestion:{str(run_id).rsplit(':', 1)[-1]}@1",
            live_receipt_digest=str(live_receipt["receipt_digest"]),
            run_id=str(run_id),
            nonce=str(nonce),
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            orchestration_plan_digest=orchestration_plan.digest,
            decisions=tuple(decisions),
            admitted_candidate_digests=admitted,
            rejected_candidate_digests=rejected,
            target_writes=0,
            source_evidence_class=EvidenceClass.LIVE_AGENTTEAMS,
            verifier_evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            claim_boundary=(
                "Candidates may contribute bound advice only. This receipt grants no "
                "approval, canonical-state, or Apply authority."
            ),
        )
        assert_zero_write_ingestion(receipt)
        return receipt
