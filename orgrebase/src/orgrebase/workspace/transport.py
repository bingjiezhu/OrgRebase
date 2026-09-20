"""Fixed-pool AgentTeams transport contracts for Workspace Domain coalitions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, IntegrityError, RunEnvelope
from orgrebase.workspace.live_evidence import WorkspaceAgentTeamsEvidenceVerifier
from orgrebase.workspace.models import (
    CoalitionPlan,
    DomainDelegationTask,
    DomainTransportCandidate,
    DomainTransportReceipt,
)
from orgrebase.workspace.templates import default_capability_cards, selected_capability_cards

WORKER_BY_DOMAIN = {
    item.domain_id: item.worker_id for item in default_capability_cards()
}


class WorkspaceTransportCompiler:
    version = "workspace-transport-compiler@1.0.0"

    def compile(
        self,
        *,
        plan: CoalitionPlan,
        projection_refs_by_domain: dict[str, str],
        run_envelope: RunEnvelope,
    ) -> tuple[DomainDelegationTask, ...]:
        cards = selected_capability_cards(plan)
        slots_by_domain: dict[str, list[str]] = {}
        for coverage in plan.coverage:
            slots_by_domain.setdefault(coverage.domain_id, []).append(coverage.slot_id)
        delegations: list[DomainDelegationTask] = []
        for card in cards.values():
            try:
                projection_ref = projection_refs_by_domain[card.domain_id]
            except KeyError as exc:
                raise IntegrityError(f"TRANSPORT_DECLARATION_MISSING:{card.ref}") from exc
            slot_ids = tuple(sorted(slots_by_domain[card.domain_id]))
            delegations.append(
                DomainDelegationTask(
                    id=f"delegation:{plan.task_ref.split(':')[-1]}:{card.domain_id}",
                    task_ref=plan.task_ref,
                    template_ref=plan.template_ref,
                    coalition_plan_ref=plan.id,
                    domain_id=card.domain_id,
                    worker_id=card.worker_id,
                    slot_ids=slot_ids,
                    actor_context_projection_ref=projection_ref,
                    allowed_output_schema_refs=card.output_schema_refs,
                    run_id=run_envelope.run_id,
                    nonce=run_envelope.nonce,
                    deadline_at=run_envelope.expires_at,
                    candidate_only=True,
                )
            )
        return tuple(sorted(delegations, key=lambda item: item.domain_id))

    @staticmethod
    def verify(
        *,
        plan: CoalitionPlan,
        delegations: tuple[DomainDelegationTask, ...],
        run_envelope: RunEnvelope,
    ) -> None:
        cards = selected_capability_cards(plan)
        expected_domains = set(cards)
        actual_domains = {item.domain_id for item in delegations}
        if expected_domains != actual_domains or len(actual_domains) != len(delegations):
            raise IntegrityError("TRANSPORT_COALITION_COVERAGE_MISMATCH")
        plan_slots = {item.slot_id: item.domain_id for item in plan.coverage}
        seen: set[str] = set()
        for task in delegations:
            card = cards.get(task.domain_id)
            if card is None or task.worker_id != card.worker_id:
                raise IntegrityError("TRANSPORT_WORKER_AUTHORITY_MISMATCH")
            if task.run_id != run_envelope.run_id or task.nonce != run_envelope.nonce:
                raise IntegrityError("TRANSPORT_RUN_ENVELOPE_MISMATCH")
            if task.task_ref != plan.task_ref or task.template_ref != plan.template_ref:
                raise IntegrityError("TRANSPORT_TASK_OR_TEMPLATE_EXPANSION")
            if task.coalition_plan_ref != plan.id or not task.candidate_only:
                raise IntegrityError("TRANSPORT_NORMATIVE_AUTHORITY_FORBIDDEN")
            if not set(task.allowed_output_schema_refs).issubset(card.output_schema_refs):
                raise IntegrityError("TRANSPORT_OUTPUT_SCHEMA_EXPANSION")
            for slot_id in task.slot_ids:
                if plan_slots.get(slot_id) != task.domain_id or slot_id in seen:
                    raise IntegrityError("TRANSPORT_SLOT_OR_AUTHORITY_EXPANSION")
                seen.add(slot_id)
        if seen != set(plan_slots):
            raise IntegrityError("TRANSPORT_SLOT_COVERAGE_INCOMPLETE")


class LocalDeterministicTransport:
    """Contract-conformant transport that emits zero-write candidate envelopes."""

    def execute_delegations(
        self,
        *,
        plan: CoalitionPlan,
        delegations: tuple[DomainDelegationTask, ...],
        run_envelope: RunEnvelope,
    ) -> tuple[tuple[DomainTransportCandidate, ...], DomainTransportReceipt]:
        WorkspaceTransportCompiler.verify(
            plan=plan, delegations=delegations, run_envelope=run_envelope
        )
        candidates = tuple(
            DomainTransportCandidate(
                delegation_task_ref=item.id,
                worker_id=item.worker_id,
                domain_id=item.domain_id,
                candidate_bundle_ref=f"transport-candidate:{item.id}",
                candidate_bundle_digest=sha256_digest(
                    {
                        "delegation": item.digest,
                        "worker": item.worker_id,
                        "slots": item.slot_ids,
                        "run_id": run_envelope.run_id,
                        "nonce": run_envelope.nonce,
                    }
                ),
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )
            for item in delegations
        )
        receipt = DomainTransportReceipt(
            id=f"transport-receipt:{plan.task_ref.split(':')[-1]}@local",
            task_ref=plan.task_ref,
            coalition_plan_ref=plan.id,
            delegation_task_digests=tuple(item.digest for item in delegations),
            candidate_digests=tuple(item.digest for item in candidates),
            selected_worker_ids=tuple(item.worker_id for item in delegations),
            run_id=run_envelope.run_id,
            nonce=run_envelope.nonce,
            target_writes=0,
            status="PASS",
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        return candidates, receipt


class LiveAgentTeamsTransport:
    """Consume a frozen live envelope verified across K8s, Matrix and provider planes."""

    def __init__(
        self,
        *,
        source_lock_path: str | Path = Path("agentteams/workspace/source-lock.json"),
    ) -> None:
        self.source_lock_path = Path(source_lock_path)

    def execute_delegations(
        self,
        *,
        plan: CoalitionPlan,
        delegations: tuple[DomainDelegationTask, ...],
        run_envelope: RunEnvelope,
    ) -> tuple[tuple[DomainTransportCandidate, ...], DomainTransportReceipt]:
        path = os.environ.get("ORGREBASE_AGENTTEAMS_EVIDENCE")
        if not path:
            raise RuntimeError("LIVE_AGENTTEAMS_NOT_RUN:MISSING_EVIDENCE_PATH")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        source_lock = json.loads(self.source_lock_path.read_text(encoding="utf-8"))
        WorkspaceTransportCompiler.verify(
            plan=plan, delegations=delegations, run_envelope=run_envelope
        )
        result = WorkspaceAgentTeamsEvidenceVerifier().verify(
            payload=payload,
            source_lock=source_lock,
            plan=plan,
            delegations=delegations,
            run_envelope=run_envelope,
        )
        return result["transport_candidates"], result["transport_receipt"]


def agentteams_status(
    *,
    source_lock_path: str | Path = Path("agentteams/workspace/source-lock.json"),
) -> dict[str, Any]:
    path = os.environ.get("ORGREBASE_AGENTTEAMS_EVIDENCE")
    if not path:
        return {
            "status": "NOT_RUN",
            "evidence_class": "NOT_RUN",
            "missing_prerequisites": [
                "ORGREBASE_AGENTTEAMS_EVIDENCE",
                "Kubernetes Team/Worker evidence",
                "Matrix membership/events",
                "candidate artifacts",
                "provider request IDs",
            ],
            "target_writes": 0,
        }
    evidence_path = Path(path)
    lock_path = Path(source_lock_path)
    if not evidence_path.is_file() or not lock_path.is_file():
        return {
            "status": "NOT_RUN",
            "evidence_class": "NOT_RUN",
            "missing_prerequisites": [
                *([] if evidence_path.is_file() else ["readable frozen evidence file"]),
                *([] if lock_path.is_file() else ["readable source lock"]),
            ],
            "path": path,
            "target_writes": 0,
        }
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        source_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        result = WorkspaceAgentTeamsEvidenceVerifier().verify_embedded(
            payload=payload, source_lock=source_lock
        )
    except (OSError, ValueError, KeyError, IntegrityError) as exc:
        return {
            "status": "FAIL",
            "evidence_class": "NOT_RUN",
            "error": str(exc),
            "path": path,
            "target_writes": 0,
        }
    return {
        "status": "LIVE_AGENTTEAMS",
        "evidence_class": "LIVE_AGENTTEAMS",
        "path": path,
        "worker_count": result["worker_count"],
        "candidate_count": result["candidate_count"],
        "provider_call_count": result["provider_call_count"],
        "transport_receipt": result["transport_receipt"].model_dump(mode="json"),
        "target_writes": 0,
    }


class EnvironmentAgentTeamsTransport:
    """Compatibility façade that reports explicit NOT_RUN without live evidence."""

    def __init__(self) -> None:
        self.invoker = None

    @staticmethod
    def prerequisites() -> tuple[str, ...]:
        return () if os.environ.get("ORGREBASE_AGENTTEAMS_EVIDENCE") else (
            "ORGREBASE_AGENTTEAMS_EVIDENCE",
        )
