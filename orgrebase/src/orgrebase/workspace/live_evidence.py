"""Fail-closed verification for Workspace AgentTeams live evidence.

The verifier consumes frozen exports only. It never infers worker participation
from message text and never upgrades static/local evidence to ``LIVE_AGENTTEAMS``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, IntegrityError, RunEnvelope
from orgrebase.workspace.models import (
    CoalitionPlan,
    DomainDelegationTask,
    DomainTransportCandidate,
    DomainTransportReceipt,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _one_by(items: list[dict[str, Any]], key: str, *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = str(item.get(key, ""))
        if not value or value in result:
            raise IntegrityError(f"LIVE_AGENTTEAMS_{label}_IDENTITY_INVALID")
        result[value] = item
    return result


def _parse_time(value: str, *, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrityError(f"LIVE_AGENTTEAMS_{label}_TIME_INVALID") from exc


class WorkspaceAgentTeamsEvidenceVerifier:
    """Verify exact K8s/Matrix/artifact/provider correlation for one coalition run."""

    schema_version = "orgrebase.workspace-agentteams-live.v1"
    version = "workspace-agentteams-live-verifier@1.0.0"

    def verify_embedded(
        self,
        *,
        payload: dict[str, Any],
        source_lock: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify a self-contained frozen evidence document."""

        try:
            plan = CoalitionPlan.model_validate(payload["coalition_plan"])
            delegations = tuple(
                DomainDelegationTask.model_validate(item)
                for item in payload["delegation_tasks"]
            )
            run_envelope = RunEnvelope.model_validate(payload["run_envelope"])
        except (KeyError, ValueError, TypeError) as exc:
            raise IntegrityError("LIVE_AGENTTEAMS_EMBEDDED_CONTRACT_INVALID") from exc
        return self.verify(
            payload=payload,
            plan=plan,
            delegations=delegations,
            run_envelope=run_envelope,
            source_lock=source_lock,
        )

    def verify(
        self,
        *,
        payload: dict[str, Any],
        plan: CoalitionPlan,
        delegations: tuple[DomainDelegationTask, ...],
        run_envelope: RunEnvelope,
        source_lock: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("schema_version") != self.schema_version:
            raise IntegrityError("LIVE_AGENTTEAMS_SCHEMA_VERSION_MISMATCH")
        if payload.get("status") not in {"PASS", "LIVE_AGENTTEAMS"}:
            raise IntegrityError("LIVE_AGENTTEAMS_STATUS_NOT_PASS")
        if payload.get("evidence_class") != EvidenceClass.LIVE_AGENTTEAMS.value:
            raise IntegrityError("LIVE_AGENTTEAMS_EVIDENCE_CLASS_INVALID")
        if payload.get("target_writes") != 0:
            raise IntegrityError("LIVE_AGENTTEAMS_TARGET_WRITES_NONZERO")
        if payload.get("source_lock_digest") != sha256_digest(source_lock):
            raise IntegrityError("LIVE_AGENTTEAMS_SOURCE_LOCK_DIGEST_MISMATCH")
        if source_lock.get("agentteams_version") != "v1.2.2":
            raise IntegrityError("LIVE_AGENTTEAMS_VERSION_NOT_PINNED")
        if not _COMMIT.fullmatch(str(source_lock.get("source_commit", ""))):
            raise IntegrityError("LIVE_AGENTTEAMS_SOURCE_COMMIT_NOT_PINNED")
        worker_image = str(source_lock.get("runtime_images", {}).get("worker", ""))
        if "@sha256:" not in worker_image or ":latest" in worker_image:
            raise IntegrityError("LIVE_AGENTTEAMS_WORKER_IMAGE_NOT_IMMUTABLE")
        for digest_name in ("skill_digest", "workspace_contract_digest"):
            if not _DIGEST.fullmatch(str(source_lock.get(digest_name, ""))):
                raise IntegrityError(f"LIVE_AGENTTEAMS_{digest_name.upper()}_INVALID")

        submitted_envelope = RunEnvelope.model_validate(payload.get("run_envelope"))
        if submitted_envelope.digest != run_envelope.digest:
            raise IntegrityError("LIVE_AGENTTEAMS_RUN_ENVELOPE_MISMATCH")
        issued = _parse_time(run_envelope.issued_at, label="ISSUED")
        expires = _parse_time(run_envelope.expires_at, label="EXPIRES")
        if issued >= expires:
            raise IntegrityError("LIVE_AGENTTEAMS_RUN_WINDOW_INVALID")
        if payload.get("coalition_plan_digest") != plan.digest:
            raise IntegrityError("LIVE_AGENTTEAMS_COALITION_PLAN_MISMATCH")

        embedded_delegations = tuple(
            DomainDelegationTask.model_validate(item)
            for item in payload.get("delegation_tasks", ())
        )
        if tuple(item.digest for item in embedded_delegations) != tuple(
            item.digest for item in delegations
        ):
            raise IntegrityError("LIVE_AGENTTEAMS_DELEGATION_SET_MISMATCH")

        team = payload.get("team")
        if not isinstance(team, dict):
            raise IntegrityError("LIVE_AGENTTEAMS_TEAM_MISSING")
        if not team.get("uid") or int(team.get("generation", 0)) < 1 or not team.get("room_id"):
            raise IntegrityError("LIVE_AGENTTEAMS_TEAM_IDENTITY_INVALID")
        room_id = str(team["room_id"])

        expected = {item.worker_id: item for item in delegations}
        workers = _one_by(list(payload.get("workers", ())), "worker_id", label="WORKER")
        if set(workers) != set(expected):
            raise IntegrityError("LIVE_AGENTTEAMS_WORKER_SET_MISMATCH")
        worker_uids: set[str] = set()
        matrix_ids: dict[str, str] = {}
        for worker_id, task in expected.items():
            worker = workers[worker_id]
            uid = str(worker.get("uid", ""))
            matrix_user_id = str(worker.get("matrix_user_id", ""))
            if (
                not uid
                or uid in worker_uids
                or int(worker.get("generation", 0)) < 1
                or not matrix_user_id.startswith("@")
                or worker.get("image_id") != worker_image
                or source_lock["skill_digest"] not in set(worker.get("skill_digests", ()))
                or worker.get("workspace_digest") != source_lock["workspace_contract_digest"]
                or worker.get("domain_id") != task.domain_id
            ):
                raise IntegrityError("LIVE_AGENTTEAMS_WORKER_BINDING_INVALID")
            worker_uids.add(uid)
            matrix_ids[worker_id] = matrix_user_id

        candidates = _one_by(
            list(payload.get("candidate_artifacts", ())), "worker_id", label="CANDIDATE"
        )
        providers = _one_by(
            list(payload.get("provider_calls", ())), "worker_id", label="PROVIDER"
        )
        events = _one_by(list(payload.get("matrix_events", ())), "worker_id", label="MATRIX")
        if set(candidates) != set(expected) or set(providers) != set(expected) or set(events) != set(expected):
            raise IntegrityError("LIVE_AGENTTEAMS_CORRELATION_SET_MISMATCH")

        seen_event_ids: set[str] = set()
        seen_provider_ids: set[str] = set()
        transport_candidates: list[DomainTransportCandidate] = []
        for worker_id, task in expected.items():
            candidate = candidates[worker_id]
            provider = providers[worker_id]
            event = events[worker_id]
            candidate_digest = str(candidate.get("digest", ""))
            event_id = str(event.get("event_id", ""))
            provider_id = str(provider.get("provider_request_id", ""))
            if (
                not _DIGEST.fullmatch(candidate_digest)
                or candidate.get("bytes_digest") != candidate_digest
                or candidate.get("delegation_task_digest") != task.digest
                or candidate.get("candidate_only") is not True
                or candidate.get("run_id") != run_envelope.run_id
                or candidate.get("nonce") != run_envelope.nonce
                or not candidate.get("ref")
            ):
                raise IntegrityError("LIVE_AGENTTEAMS_CANDIDATE_BINDING_INVALID")
            if (
                not provider_id
                or provider_id in seen_provider_ids
                or provider.get("run_id") != run_envelope.run_id
                or provider.get("nonce") != run_envelope.nonce
                or provider.get("candidate_digest") != candidate_digest
                or candidate.get("provider_request_id") != provider_id
            ):
                raise IntegrityError("LIVE_AGENTTEAMS_PROVIDER_BINDING_INVALID")
            if (
                not event_id
                or event_id in seen_event_ids
                or event.get("membership") != "join"
                or event.get("room_id") != room_id
                or event.get("sender") != matrix_ids[worker_id]
                or event.get("run_id") != run_envelope.run_id
                or event.get("nonce") != run_envelope.nonce
                or event.get("delegation_task_digest") != task.digest
                or event.get("candidate_artifact_digest") != candidate_digest
            ):
                raise IntegrityError("LIVE_AGENTTEAMS_MATRIX_BINDING_INVALID")
            seen_event_ids.add(event_id)
            seen_provider_ids.add(provider_id)
            transport_candidates.append(
                DomainTransportCandidate(
                    delegation_task_ref=task.id,
                    worker_id=worker_id,
                    domain_id=task.domain_id,
                    candidate_bundle_ref=str(candidate["ref"]),
                    candidate_bundle_digest=candidate_digest,
                    room_id=room_id,
                    event_id=event_id,
                    provider_request_id=provider_id,
                    evidence_class=EvidenceClass.LIVE_AGENTTEAMS,
                )
            )

        candidate_tuple = tuple(
            sorted(transport_candidates, key=lambda item: item.worker_id)
        )
        receipt = DomainTransportReceipt(
            id=f"transport-receipt:{plan.task_ref.split(':')[-1]}@live",
            task_ref=plan.task_ref,
            coalition_plan_ref=plan.id,
            delegation_task_digests=tuple(item.digest for item in delegations),
            candidate_digests=tuple(item.digest for item in candidate_tuple),
            selected_worker_ids=tuple(item.worker_id for item in delegations),
            run_id=run_envelope.run_id,
            nonce=run_envelope.nonce,
            target_writes=0,
            status="PASS",
            evidence_class=EvidenceClass.LIVE_AGENTTEAMS,
        )
        return {
            "status": "PASS",
            "evidence_class": EvidenceClass.LIVE_AGENTTEAMS.value,
            "verifier_version": self.version,
            "team_uid": team["uid"],
            "team_generation": int(team["generation"]),
            "room_id": room_id,
            "worker_count": len(expected),
            "candidate_count": len(candidate_tuple),
            "provider_call_count": len(seen_provider_ids),
            "transport_candidates": candidate_tuple,
            "transport_receipt": receipt,
            "checked": (
                "source_commit_and_image_digest",
                "team_uid_and_generation",
                "worker_uid_generation_domain_skill_workspace",
                "exact_matrix_sender_membership_event_room",
                "run_nonce_and_time_window",
                "candidate_bytes_and_delegation_binding",
                "provider_request_binding",
                "target_writes_zero",
            ),
        }
