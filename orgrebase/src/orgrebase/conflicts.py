"""Deterministic authority-based conflict resolution for candidate assertions."""

from __future__ import annotations

from typing import Any, ClassVar

from orgrebase.domain import (
    ConflictCandidate,
    ConflictResolutionReceipt,
    EvidenceClass,
    RunEnvelope,
)


class ConflictResolver:
    """Resolve by declared authority and source admissibility, never by agent voting."""

    authority_policy: ClassVar[dict[str, str]] = {"product.launch_date": "product"}
    policy_revision = "policy:claim-authority@r1"

    def resolve(
        self,
        *,
        claim_key: str,
        candidates: tuple[ConflictCandidate, ...],
        run_envelope: RunEnvelope,
    ) -> ConflictResolutionReceipt:
        authority = self.authority_policy.get(claim_key)
        if authority is None:
            raise RuntimeError("NO_AUTHORITY_POLICY")
        authoritative = tuple(
            item for item in candidates if item.authority_domain == authority and item.source_ref
        )
        if len(authoritative) != 1:
            raise RuntimeError("AMBIGUOUS_AUTHORITY_EVIDENCE")
        winner = authoritative[0]
        rejected = tuple(
            {
                "candidate_id": item.id,
                "reason": (
                    "NON_AUTHORITATIVE_DOMAIN"
                    if item.authority_domain != authority
                    else "DUPLICATE_AUTHORITY_EVIDENCE"
                ),
                "proposed_value": item.proposed_value,
            }
            for item in candidates
            if item.id != winner.id
        )
        return ConflictResolutionReceipt(
            id="conflict:product-launch-date@1",
            claim_key=claim_key,
            workflow_run_id=run_envelope.run_id,
            run_nonce=run_envelope.nonce,
            status="RESOLVED_CANDIDATE_ONLY",
            policy_revision=self.policy_revision,
            candidates=candidates,
            admitted_candidate_id=winner.id,
            rejected=rejected,
            rule="declared domain authority + cited source; no majority vote and no agent self-admission",
            target_writes=0,
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )

    def launch_date_drill(
        self, run_envelope: RunEnvelope
    ) -> tuple[ConflictResolutionReceipt, dict[str, Any]]:
        product = ConflictCandidate(
            id="candidate:product-launch-date@v8",
            claim_key="product.launch_date",
            proposed_value="2026-09-15",
            authority_domain="product",
            source_ref="source:product-release-plan@v13",
            submitted_by="product-steward",
        )
        gtm = ConflictCandidate(
            id="candidate:gtm-launch-date@legacy",
            claim_key="product.launch_date",
            proposed_value="2026-09-01",
            authority_domain="gtm",
            source_ref="source:gtm-customer-commitment@v4",
            submitted_by="gtm-steward",
        )
        receipt = self.resolve(
            claim_key="product.launch_date",
            candidates=(product, gtm),
            run_envelope=run_envelope,
        )
        explanation = {
            "winner": product.id,
            "loser": gtm.id,
            "why": "GTM owns downstream readiness, not the canonical Product launch-date Claim.",
            "next_action": (
                "GTM evidence remains provenance and may trigger review, "
                "but cannot overwrite Product."
            ),
        }
        return receipt, explanation
