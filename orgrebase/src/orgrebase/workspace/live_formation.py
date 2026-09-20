"""Two-stage, fail-closed seam for live AgentTeams Workspace formation.

PREPARE freezes a least-authority execution request without writing canonical
state. RESUME verifies frozen live evidence and candidate bytes, admits the
claims, then reuses the ordinary atomic Quote formation transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

from pydantic import model_validator

from orgrebase.clock import Clock
from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, EvidenceClass, IntegrityError, RunEnvelope
from orgrebase.runtime_contracts import prepare_artifact_write
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.live_evidence import WorkspaceAgentTeamsEvidenceVerifier
from orgrebase.workspace.models import (
    ActorContextProjection,
    ClaimCandidate,
    CoalitionPlan,
    DomainCandidateBundle,
    DomainDelegationTask,
    DomainTransportCandidate,
    DomainTransportReceipt,
    FrozenModel,
    TaskInterpretationReceipt,
    TaskReceipt,
    TaskRequest,
    TaskRequirementCandidate,
    TaskTemplateVersion,
)
from orgrebase.workspace.transport import WorkspaceTransportCompiler

LIVE_PREPARE_MEDIA_TYPE = "application/vnd.orgrebase.live-formation-prepare+json"
LIVE_VERIFICATION_MEDIA_TYPE = (
    "application/vnd.orgrebase.live-formation-verification+json"
)
LIVE_TRANSPORT_RECEIPT_MEDIA_TYPE = (
    "application/vnd.orgrebase.live-domain-transport-receipt+json"
)
LIVE_TRANSPORT_CANDIDATE_MEDIA_TYPE = (
    "application/vnd.orgrebase.live-domain-transport-candidate+json"
)


def _parse_instant(value: str, *, error_code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise IntegrityError(error_code) from exc
    if parsed.tzinfo is None:
        raise IntegrityError(error_code)
    return parsed.astimezone(UTC)


def _format_instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _with_projection_expiry(
    projection: ActorContextProjection, expires_at: str
) -> ActorContextProjection:
    payload = projection.model_dump(mode="json", exclude={"digest"})
    payload["expires_at"] = expires_at
    return ActorContextProjection.model_validate(payload)


class LiveFormationPrepareBundle(ContentAddressedModel):
    schema_version: Literal["orgrebase.workspace-live-formation-prepare.v1"] = (
        "orgrebase.workspace-live-formation-prepare.v1"
    )
    id: str
    request: TaskRequest
    template: TaskTemplateVersion
    requirement_candidates: tuple[TaskRequirementCandidate, ...]
    interpretation: TaskInterpretationReceipt
    revision_lock: dict[str, str]
    coalition: CoalitionPlan
    source_projections: tuple[ActorContextProjection, ...]
    delegations: tuple[DomainDelegationTask, ...]
    run_envelope: RunEnvelope
    prepared_at: str
    expires_at: str
    state: Literal["PREPARED"] = "PREPARED"
    target_writes: Literal[0] = 0
    evidence_class: Literal["LOCAL_DETERMINISTIC"] = "LOCAL_DETERMINISTIC"

    @model_validator(mode="after")
    def verify_frozen_boundary(self) -> Self:
        issued = _parse_instant(
            self.prepared_at, error_code="LIVE_FORMATION_PREPARED_TIME_INVALID"
        )
        expires = _parse_instant(
            self.expires_at, error_code="LIVE_FORMATION_EXPIRY_TIME_INVALID"
        )
        if issued >= expires:
            raise ValueError("LIVE_FORMATION_TTL_INVALID")
        if (
            self.template.ref != self.request.template_ref
            or self.interpretation.task_ref != self.request.id
            or self.interpretation.template_ref != self.template.ref
            or self.coalition.task_ref != self.request.id
            or self.coalition.template_ref != self.template.ref
            or self.coalition.revision_lock != self.revision_lock
        ):
            raise ValueError("LIVE_FORMATION_CONTRACT_BINDING_MISMATCH")
        candidate_digests = tuple(item.digest for item in self.requirement_candidates)
        if (
            self.interpretation.candidate_refs != candidate_digests
            or self.interpretation.candidate_set_digest
            != sha256_digest(sorted(candidate_digests))
        ):
            raise ValueError("LIVE_FORMATION_INTERPRETATION_BINDING_MISMATCH")
        if (
            self.run_envelope.issued_at != self.prepared_at
            or self.run_envelope.expires_at != self.expires_at
            or self.run_envelope.mode != "LIVE_AGENTTEAMS"
            or self.run_envelope.evidence_class != EvidenceClass.NOT_RUN
        ):
            raise ValueError("LIVE_FORMATION_RUN_ENVELOPE_MISMATCH")
        projection_refs = {item.ref for item in self.source_projections}
        if len(projection_refs) != len(self.source_projections) or any(
            item.expires_at != self.expires_at for item in self.source_projections
        ):
            raise ValueError("LIVE_FORMATION_PROJECTION_SET_INVALID")
        delegation_ids = {item.id for item in self.delegations}
        if len(delegation_ids) != len(self.delegations) or any(
            item.actor_context_projection_ref not in projection_refs
            or item.run_id != self.run_envelope.run_id
            or item.nonce != self.run_envelope.nonce
            or item.deadline_at != self.expires_at
            for item in self.delegations
        ):
            raise ValueError("LIVE_FORMATION_DELEGATION_SET_INVALID")
        return self


class LiveFormationVerificationReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.workspace-live-formation-verification.v1"] = (
        "orgrebase.workspace-live-formation-verification.v1"
    )
    id: str
    prepare_digest: str
    evidence_payload_digest: str
    source_lock_digest: str
    transport_receipt_digest: str
    transport_candidate_digests: tuple[str, ...]
    domain_bundle_digests: tuple[str, ...]
    claim_candidate_digests: tuple[str, ...]
    verifier_version: str
    checked: tuple[str, ...]
    verified_at: str
    status: Literal["PASS"] = "PASS"
    candidate_target_writes: Literal[0] = 0
    evidence_class: Literal["LIVE_AGENTTEAMS"] = "LIVE_AGENTTEAMS"


class LiveFormationResumeResult(FrozenModel):
    status: Literal["COMPLETED"] = "COMPLETED"
    prepare_digest: str
    verification_receipt: LiveFormationVerificationReceipt
    task_receipt: TaskReceipt
    evidence_class: Literal["LIVE_AGENTTEAMS"] = "LIVE_AGENTTEAMS"


class WorkspaceLiveFormationService:
    """Pause formation at the external Agent boundary and resume fail closed."""

    def __init__(
        self,
        formation: WorkspaceFormationService,
        *,
        clock: Clock,
        evidence_verifier: WorkspaceAgentTeamsEvidenceVerifier | None = None,
    ) -> None:
        self.formation = formation
        self.store = formation.store
        self.clock = clock
        self.evidence_verifier = evidence_verifier or WorkspaceAgentTeamsEvidenceVerifier()
        self.transport_compiler = WorkspaceTransportCompiler()

    def prepare(
        self,
        request: TaskRequest,
        *,
        ttl_seconds: int,
    ) -> LiveFormationPrepareBundle:
        if self.store.get_idempotent(request.idempotency_key, request.digest) is not None:
            raise RuntimeError("LIVE_FORMATION_ALREADY_COMMITTED")
        if not 1 <= ttl_seconds <= 86_400:
            raise ValueError("LIVE_FORMATION_TTL_OUT_OF_RANGE")
        issued = _parse_instant(
            self.clock.now(), error_code="LIVE_FORMATION_CLOCK_INVALID"
        )
        prepared_at = _format_instant(issued)
        expires_at = _format_instant(issued + timedelta(seconds=ttl_seconds))
        (
            template,
            requirement_candidates,
            interpretation,
            revision_lock,
            coalition,
        ) = self.formation.compile_quote_contracts(request)
        source_projections = tuple(
            _with_projection_expiry(item, expires_at)
            for item in self.formation.domain_registry.source_projections(
                task=request,
                template=template,
                plan=coalition,
                now=prepared_at,
            )
        )
        nonce = sha256_digest(
            {
                "request": request.digest,
                "coalition": coalition.digest,
                "projection_digests": sorted(item.digest for item in source_projections),
                "issued_at": prepared_at,
                "expires_at": expires_at,
                "mode": "LIVE_AGENTTEAMS",
            }
        ).split(":", 1)[1]
        run_envelope = RunEnvelope(
            run_id=f"run:workspace:live-formation:{nonce[:16]}@1",
            nonce=nonce,
            issued_at=prepared_at,
            expires_at=expires_at,
            mode="LIVE_AGENTTEAMS",
            evidence_class=EvidenceClass.NOT_RUN,
        )
        projection_refs = {
            item.actor_id.removesuffix("-steward"): item.ref
            for item in source_projections
            if item.actor_id.endswith("-steward")
        }
        delegations = self.transport_compiler.compile(
            plan=coalition,
            projection_refs_by_domain=projection_refs,
            run_envelope=run_envelope,
        )
        prepared = LiveFormationPrepareBundle(
            id=f"live-formation-prepare:{request.id.split(':')[-1]}@{nonce[:16]}",
            request=request,
            template=template,
            requirement_candidates=requirement_candidates,
            interpretation=interpretation,
            revision_lock=revision_lock,
            coalition=coalition,
            source_projections=source_projections,
            delegations=delegations,
            run_envelope=run_envelope,
            prepared_at=prepared_at,
            expires_at=expires_at,
            target_writes=0,
        )
        self.transport_compiler.verify(
            plan=prepared.coalition,
            delegations=prepared.delegations,
            run_envelope=prepared.run_envelope,
        )
        return prepared

    def _require_active(self, prepared: LiveFormationPrepareBundle) -> str:
        now = _parse_instant(
            self.clock.now(), error_code="LIVE_FORMATION_CLOCK_INVALID"
        )
        issued = _parse_instant(
            prepared.prepared_at, error_code="LIVE_FORMATION_PREPARED_TIME_INVALID"
        )
        expires = _parse_instant(
            prepared.expires_at, error_code="LIVE_FORMATION_EXPIRY_TIME_INVALID"
        )
        if now < issued:
            raise RuntimeError("LIVE_FORMATION_NOT_YET_VALID")
        if now >= expires:
            raise RuntimeError("LIVE_FORMATION_EXPIRED")
        return _format_instant(now)

    @staticmethod
    def _verify_candidate_payloads(
        *,
        prepared: LiveFormationPrepareBundle,
        transport_candidates: tuple[DomainTransportCandidate, ...],
        transport_receipt: DomainTransportReceipt,
        claim_candidates: tuple[ClaimCandidate, ...],
        bundles: tuple[DomainCandidateBundle, ...],
        verified_at: str,
    ) -> None:
        delegations = {item.id: item for item in prepared.delegations}
        candidates_by_task = {
            item.delegation_task_ref: item for item in transport_candidates
        }
        bundles_by_task = {
            item.delegation_task_ref: item
            for item in bundles
            if item.delegation_task_ref is not None
        }
        if (
            len(candidates_by_task) != len(transport_candidates)
            or len(bundles_by_task) != len(bundles)
            or set(candidates_by_task) != set(delegations)
            or set(bundles_by_task) != set(delegations)
        ):
            raise IntegrityError("LIVE_FORMATION_CANDIDATE_COVERAGE_MISMATCH")
        if (
            transport_receipt.status != "PASS"
            or transport_receipt.evidence_class != EvidenceClass.LIVE_AGENTTEAMS
            or transport_receipt.target_writes != 0
            or transport_receipt.task_ref != prepared.request.id
            or transport_receipt.coalition_plan_ref != prepared.coalition.id
            or transport_receipt.run_id != prepared.run_envelope.run_id
            or transport_receipt.nonce != prepared.run_envelope.nonce
            or set(transport_receipt.delegation_task_digests)
            != {item.digest for item in prepared.delegations}
            or set(transport_receipt.candidate_digests)
            != {item.digest for item in transport_candidates}
            or set(transport_receipt.selected_worker_ids)
            != {item.worker_id for item in prepared.delegations}
        ):
            raise IntegrityError("LIVE_FORMATION_TRANSPORT_RECEIPT_INVALID")
        if len({item.digest for item in claim_candidates}) != len(claim_candidates):
            raise IntegrityError("LIVE_FORMATION_DUPLICATE_CLAIM_CANDIDATE")
        verified_time = _parse_instant(
            verified_at, error_code="LIVE_FORMATION_VERIFIED_TIME_INVALID"
        )
        expires = _parse_instant(
            prepared.expires_at, error_code="LIVE_FORMATION_EXPIRY_TIME_INVALID"
        )
        if any(
            not verified_time
            < _parse_instant(
                item.fresh_until,
                error_code="LIVE_FORMATION_CANDIDATE_FRESHNESS_TIME_INVALID",
            )
            <= expires
            for item in claim_candidates
        ):
            raise IntegrityError("LIVE_FORMATION_CANDIDATE_TTL_INVALID")
        all_bound_claims: set[str] = set()
        for task_ref, delegation in delegations.items():
            transport_candidate = candidates_by_task[task_ref]
            bundle = bundles_by_task[task_ref]
            if (
                transport_candidate.worker_id != delegation.worker_id
                or transport_candidate.domain_id != delegation.domain_id
                or transport_candidate.evidence_class != EvidenceClass.LIVE_AGENTTEAMS
                or transport_candidate.candidate_bundle_ref != bundle.id
                or transport_candidate.candidate_bundle_digest != bundle.digest
                or bundle.task_ref != prepared.request.id
                or bundle.template_ref != prepared.template.ref
                or bundle.coalition_plan_ref != prepared.coalition.id
                or bundle.domain_id != delegation.domain_id
                or bundle.worker_id != delegation.worker_id
                or bundle.transport_mode != "LIVE_AGENTTEAMS"
                or bundle.evidence_class != EvidenceClass.LIVE_AGENTTEAMS
            ):
                raise IntegrityError("LIVE_FORMATION_DOMAIN_BUNDLE_BINDING_INVALID")
            domain_claims = tuple(
                item
                for item in claim_candidates
                if item.issuer_domain_id == delegation.domain_id
            )
            domain_digests = tuple(item.digest for item in domain_claims)
            if (
                len(set(bundle.candidate_refs)) != len(bundle.candidate_refs)
                or set(bundle.candidate_refs) != set(domain_digests)
                or bundle.candidate_set_digest != sha256_digest(sorted(domain_digests))
                or {item.predicate for item in domain_claims} != set(delegation.slot_ids)
            ):
                raise IntegrityError("LIVE_FORMATION_DOMAIN_CANDIDATE_SET_INVALID")
            all_bound_claims.update(domain_digests)
        if all_bound_claims != {item.digest for item in claim_candidates}:
            raise IntegrityError("LIVE_FORMATION_UNBOUND_CLAIM_CANDIDATE")

    def resume(
        self,
        prepared: LiveFormationPrepareBundle,
        *,
        evidence_payload: dict[str, Any],
        source_lock: dict[str, Any],
        claim_candidates: tuple[ClaimCandidate, ...],
        bundles: tuple[DomainCandidateBundle, ...],
    ) -> LiveFormationResumeResult:
        verified_at = self._require_active(prepared)
        self.transport_compiler.verify(
            plan=prepared.coalition,
            delegations=prepared.delegations,
            run_envelope=prepared.run_envelope,
        )
        verified = self.evidence_verifier.verify(
            payload=evidence_payload,
            source_lock=source_lock,
            plan=prepared.coalition,
            delegations=prepared.delegations,
            run_envelope=prepared.run_envelope,
        )
        transport_candidates = tuple(verified["transport_candidates"])
        transport_receipt = verified["transport_receipt"]
        if not isinstance(transport_receipt, DomainTransportReceipt):
            raise IntegrityError("LIVE_FORMATION_VERIFIER_RECEIPT_MISSING")
        self._verify_candidate_payloads(
            prepared=prepared,
            transport_candidates=transport_candidates,
            transport_receipt=transport_receipt,
            claim_candidates=claim_candidates,
            bundles=bundles,
            verified_at=verified_at,
        )
        evidence_digest = sha256_digest(evidence_payload)
        verification_id = (
            f"live-formation-verification:{prepared.request.id.split(':')[-1]}@v1"
        )
        existing = self.store.get_idempotent(
            prepared.request.idempotency_key, prepared.request.digest
        )
        if existing is not None:
            stored = LiveFormationVerificationReceipt.model_validate(
                self.store.load_artifact(
                    verification_id, LIVE_VERIFICATION_MEDIA_TYPE
                ).payload
            )
            if (
                stored.prepare_digest != prepared.digest
                or stored.evidence_payload_digest != evidence_digest
                or set(stored.claim_candidate_digests)
                != {item.digest for item in claim_candidates}
            ):
                raise IntegrityError("LIVE_FORMATION_RESUME_CONFLICT")
            return LiveFormationResumeResult(
                prepare_digest=prepared.digest,
                verification_receipt=stored,
                task_receipt=TaskReceipt.model_validate(existing),
            )
        verification = LiveFormationVerificationReceipt(
            id=verification_id,
            prepare_digest=prepared.digest,
            evidence_payload_digest=evidence_digest,
            source_lock_digest=sha256_digest(source_lock),
            transport_receipt_digest=transport_receipt.digest,
            transport_candidate_digests=tuple(
                item.digest for item in transport_candidates
            ),
            domain_bundle_digests=tuple(sorted(item.digest for item in bundles)),
            claim_candidate_digests=tuple(
                sorted(item.digest for item in claim_candidates)
            ),
            verifier_version=str(verified["verifier_version"]),
            checked=tuple(verified["checked"]),
            verified_at=verified_at,
            candidate_target_writes=0,
        )
        additional_writes = (
            prepare_artifact_write(
                prepared.id,
                LIVE_PREPARE_MEDIA_TYPE,
                prepared.model_dump(mode="json"),
            ),
            prepare_artifact_write(
                verification.id,
                LIVE_VERIFICATION_MEDIA_TYPE,
                verification.model_dump(mode="json"),
            ),
            prepare_artifact_write(
                transport_receipt.id,
                LIVE_TRANSPORT_RECEIPT_MEDIA_TYPE,
                transport_receipt.model_dump(mode="json"),
            ),
            *tuple(
                prepare_artifact_write(
                    f"live-transport-candidate:{item.worker_id}@v1",
                    LIVE_TRANSPORT_CANDIDATE_MEDIA_TYPE,
                    item.model_dump(mode="json"),
                )
                for item in transport_candidates
            ),
        )
        quote_prepared = self.formation.prepare_quote_from_candidates(
            request=prepared.request,
            template=prepared.template,
            interpretation=prepared.interpretation,
            revision_lock=prepared.revision_lock,
            coalition=prepared.coalition,
            source_projections=prepared.source_projections,
            claim_candidates=claim_candidates,
            bundles=bundles,
            clock=self.clock,
            context_expires_at=prepared.expires_at,
            run_id=prepared.run_envelope.run_id,
            additional_artifact_writes=additional_writes,
            event_metadata={
                "execution_mode": "LIVE_AGENTTEAMS",
                "live_prepare_digest": prepared.digest,
                "live_verification_receipt_digest": verification.digest,
                "candidate_target_writes": 0,
            },
        )
        task_receipt = self.formation.commit_quote(quote_prepared)
        return LiveFormationResumeResult(
            prepare_digest=prepared.digest,
            verification_receipt=verification,
            task_receipt=task_receipt,
        )
