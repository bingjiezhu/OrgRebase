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

# This bridge currently compiles the frozen enterprise-launch task. The legal
# obligation is not inferable from the changed Product claim alone, so its
# purpose-bound semantic contract is declared explicitly instead of guessed by
# an LLM or silently accepted from a structurally valid artifact.
_LEGAL_CLAIM_BY_CHANGE_SUBJECT: dict[str, tuple[str, Any]] = {
    "claim:product.launch_date": (
        "claim:legal.customer_notice_required",
        True,
    ),
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


def _semantic_version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    text = value.strip().removeprefix("v")
    parts = text.split(".")
    if not 1 <= len(parts) <= 3 or any(not item.isdigit() for item in parts):
        return None
    numbers = [int(item) for item in parts]
    padded = [*numbers, 0, 0]
    return padded[0], padded[1], padded[2]


def _skill_semantic_profile(
    preview: ImpactPreview,
) -> tuple[str, tuple[int, int, int], tuple[int, int, int]] | None:
    skill_results = [
        item
        for item in preview.results
        if item.object_id.startswith("skill:")
        and item.classification.value == "REQUALIFICATION_REQUIRED"
    ]
    if len(skill_results) != 1:
        return None
    result = skill_results[0]
    skill_name = result.object_id.removeprefix("skill:")
    prefix = f"skill-contract:{skill_name}@"
    version_refs = {
        ref.removeprefix(prefix)
        for step in result.proof_path
        for ref in step.provenance_refs
        if ref.startswith(prefix)
    }
    if len(version_refs) != 1:
        return None
    current = _semantic_version(version_refs.pop())
    if current is None:
        return None
    proposed = (current[0], current[1] + 1, 0)
    return result.object_id, current, proposed


def _provided_values(candidate: dict[str, Any], *field_names: str) -> list[Any]:
    return [candidate[name] for name in field_names if name in candidate]


def _skill_targets_and_versions(candidate: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    targets = _provided_values(candidate, "target", "skill_id", "subject")
    candidate_versions = _provided_values(candidate, "candidate_version")
    adapter = candidate.get("adapter")
    if isinstance(adapter, str) and "@" in adapter:
        adapter_name, adapter_version = adapter.rsplit("@", 1)
        targets.append(
            adapter_name
            if adapter_name.startswith("skill:")
            else f"skill:{adapter_name}"
        )
        candidate_versions.append(adapter_version)
    return targets, candidate_versions


def _semantic_rejection_reasons(
    *,
    producer_worker: str,
    output: dict[str, Any],
    change_set: ChangeSetRevision,
    preview: ImpactPreview,
) -> list[str]:
    """Reject a schema-valid candidate that answers a different business task."""

    if producer_worker == "product-steward":
        candidate = output.get("ClaimDeltaCandidate")
        if not isinstance(candidate, dict):
            return ["TASK_SEMANTIC_PAYLOAD_MISSING"]
        deltas = {item.object_id: item for item in change_set.deltas}
        subjects = _provided_values(candidate, "object_id", "subject")
        reasons: list[str] = []
        first_subject = subjects[0] if subjects else None
        expected_delta = (
            deltas.get(first_subject) if isinstance(first_subject, str) else None
        )
        if expected_delta is None:
            expected_delta = next(iter(change_set.deltas), None)
        if (
            expected_delta is None
            or not subjects
            or any(item != expected_delta.object_id for item in subjects)
        ):
            reasons.append("PRODUCT_SUBJECT_MISMATCH")
        if expected_delta is None:
            reasons.append("TASK_SEMANTIC_PROFILE_UNSUPPORTED")
            return reasons
        before_values = _provided_values(candidate, "base_value", "before")
        if not before_values or any(
            item != expected_delta.base_value for item in before_values
        ):
            reasons.append("PRODUCT_BEFORE_VALUE_MISMATCH")
        after_values = _provided_values(candidate, "proposed_value", "after")
        if not after_values or any(
            item != expected_delta.proposed_value for item in after_values
        ):
            reasons.append("PRODUCT_AFTER_VALUE_MISMATCH")
        return reasons

    if producer_worker == "legal-steward":
        candidate = output.get("MinimalClaimCandidate")
        if not isinstance(candidate, dict):
            return ["TASK_SEMANTIC_PAYLOAD_MISSING"]
        matching_profiles = {
            _LEGAL_CLAIM_BY_CHANGE_SUBJECT[item.object_id]
            for item in change_set.deltas
            if item.object_id in _LEGAL_CLAIM_BY_CHANGE_SUBJECT
        }
        if len(matching_profiles) != 1:
            return ["TASK_SEMANTIC_PROFILE_UNSUPPORTED"]
        expected_subject, expected_value = matching_profiles.pop()
        reasons = []
        if candidate.get("subject") != expected_subject:
            reasons.append("LEGAL_SUBJECT_MISMATCH")
        if candidate.get("value") != expected_value:
            reasons.append("LEGAL_VALUE_MISMATCH")
        if candidate.get("restricted_source_text_disclosed") is True:
            reasons.append("LEGAL_MINIMAL_DISCLOSURE_VIOLATION")
        return reasons

    if producer_worker == "gtm-steward":
        candidate = output.get("ImpactCandidate")
        if not isinstance(candidate, dict) or not candidate:
            return ["TASK_SEMANTIC_PAYLOAD_MISSING"]
        preview_by_target = {item.object_id: item for item in preview.results}
        reasons = []
        for target, classification in candidate.items():
            expected = preview_by_target.get(target)
            if expected is None:
                reasons.append("GTM_TARGET_OUT_OF_PREVIEW_SCOPE")
                continue
            actual = (
                classification.get("classification")
                if isinstance(classification, dict)
                else classification
            )
            if actual != expected.classification.value:
                reasons.append("GTM_CLASSIFICATION_MISMATCH")
        return reasons

    if producer_worker == "skill-curator":
        candidate = output.get("SkillPatchCandidate")
        if not isinstance(candidate, dict):
            return ["TASK_SEMANTIC_PAYLOAD_MISSING"]
        profile = _skill_semantic_profile(preview)
        if profile is None:
            return ["TASK_SEMANTIC_PROFILE_UNSUPPORTED"]
        expected_target, expected_from, expected_candidate = profile
        targets, candidate_versions = _skill_targets_and_versions(candidate)
        reasons = []
        if not targets or any(item != expected_target for item in targets):
            reasons.append("SKILL_TARGET_MISMATCH")
        if (
            _semantic_version(candidate.get("from_version")) != expected_from
            or not candidate_versions
            or any(
                _semantic_version(item) != expected_candidate
                for item in candidate_versions
            )
        ):
            reasons.append("SKILL_VERSION_TRANSITION_MISMATCH")
        if candidate.get("release_ceiling") != "CANARY_ONLY":
            reasons.append("SKILL_RELEASE_CEILING_MISMATCH")
        return reasons

    return ["TASK_SEMANTIC_PROFILE_UNSUPPORTED"]


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

    version = "orgrebase.agent-candidate-ingestor@1.1.0"

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
            elif task is not None:
                reasons.extend(
                    _semantic_rejection_reasons(
                        producer_worker=producer_worker,
                        output=output,
                        change_set=change_set,
                        preview=preview,
                    )
                )

            decision = "REJECTED" if reasons else "ADVISORY_ACCEPTED"
            decisions.append(
                AgentCandidateDecision(
                    artifact_ref=artifact_ref,
                    artifact_digest=expected_digest,
                    producer_worker=producer_worker,
                    decision=decision,
                    reason_codes=tuple(sorted(set(reasons)))
                    or ("EXACTLY_BOUND_ADVISORY",),
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
