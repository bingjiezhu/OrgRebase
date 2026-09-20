"""Independent synthetic observation and assurance for the SC-008 slice.

This module deliberately imports no compiler, executor, or handler
implementation.  A controlled producer records actual-value observations under
an authority distinct from the runtime.  The oracle then consumes that record;
it never treats runtime evidence as business truth.  This remains synthetic
process assurance, not external or enterprise ground truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.evolution_contracts import (
    ExecutionEvidence,
    ExecutionStatus,
    LocalOutcomeDecision,
    OACExecutionReceipt,
    OutcomeDimension,
    OutcomeFact,
    OutcomeObservation,
    OutcomeVerdict,
)

OUTCOME_OBSERVATION_MEDIA_TYPE = "application/vnd.orgrebase.oac-outcome-observation+json"
OUTCOME_DECISION_MEDIA_TYPE = "application/vnd.orgrebase.oac-outcome-decision+json"
EXECUTION_RECEIPT_MEDIA_TYPE = "application/vnd.orgrebase.oac-execution-receipt+json"
ORACLE_ID = "oracle:veracier-sc008-controlled-process"
ORACLE_BUILD = "orgrebase.sc008-controlled-process-oracle/v1"
ORACLE_AUTHORITY = "authority:veracier-controlled-outcome-assurance"
ORACLE_PROFILE = "oac.supplier.sc008.synthetic-oracle/v1"
OBSERVATION_PRODUCER_ID = "producer:veracier-controlled-observation-fixture"
OBSERVATION_PRODUCER_AUTHORITY = "authority:veracier-controlled-observation-fixture"

_EXPECTED_VALUES: dict[str, tuple[str, str]] = {
    "assess_dependency_impact": (
        "dependency_impact_satisfied",
        "impact_within_declared_scope",
    ),
    "commercial-switch-review": (
        "commercial_switch_satisfied",
        "commercial_switch_approved",
    ),
    "continuity-option-selection": (
        "continuity_option_satisfied",
        "continuity_option_available",
    ),
    "qualification-evidence-check": (
        "qualification_evidence_satisfied",
        "qualification_record_approved",
    ),
}


def oracle_policy_digest() -> str:
    return sha256_digest(
        {
            "oracle_id": ORACLE_ID,
            "oracle_build": ORACLE_BUILD,
            "authority": ORACLE_AUTHORITY,
            "profile": ORACLE_PROFILE,
            "expected_values": _EXPECTED_VALUES,
            "accepted_observation_authority": OBSERVATION_PRODUCER_AUTHORITY,
            "claim_boundary": "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
        }
    )


def controlled_reference_actual_values() -> dict[str, str]:
    """Return a fresh exact-value fixture; callers must make counterexamples explicit."""

    return {name: expected for name, (_, expected) in _EXPECTED_VALUES.items()}


def build_controlled_outcome_observation(
    receipt: OACExecutionReceipt,
    *,
    observation_id: str,
    actual_values: Mapping[str, str],
    producer_id: str = OBSERVATION_PRODUCER_ID,
    producer_authority_ref: str = OBSERVATION_PRODUCER_AUTHORITY,
    produced_at: str = "2026-08-26T00:02:00Z",
) -> OutcomeObservation:
    """Build an actual-value record without reading runtime-produced values."""

    receipt = receipt.revalidated()
    if set(actual_values) != set(_EXPECTED_VALUES):
        raise IntegrityError("OUTCOME_OBSERVATION_FACT_SET_MISMATCH")
    facts = tuple(
        OutcomeFact(
            fact_id=f"outcome-fact:{observation_id}:{name}",
            name=name,
            state="KNOWN",
            value=value,
            evidence_refs=(observation_id,),
        )
        for name, value in sorted(actual_values.items(), key=lambda item: item[0].encode())
    )
    return OutcomeObservation(
        observation_id=observation_id,
        observation_profile=ORACLE_PROFILE,
        subject_ref="alternative:acieries-savoie",
        demand_ref="demand:SC-008",
        execution_receipt_ref=receipt.receipt_id,
        execution_receipt_digest=receipt.digest,
        facts=facts,
        producer_id=producer_id,
        producer_authority_ref=producer_authority_ref,
        produced_at=produced_at,
    )


def _dimension(
    name: str,
    verdict: Literal["PASS", "FAIL", "UNKNOWN"],
    reason: str,
    evidence_refs: tuple[str, ...],
) -> OutcomeDimension:
    return OutcomeDimension(
        name=name,
        verdict=verdict,
        reason_codes=(reason,),
        evidence_refs=tuple(sorted(set(evidence_refs), key=str.encode)),
    )


class ControlledProcessOutcomeAssurance:
    """Separate authority over serialized execution facts."""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def _verify_persisted_inputs(
        self,
        receipt: OACExecutionReceipt,
        evidence: tuple[ExecutionEvidence, ...],
    ) -> None:
        receipt = receipt.revalidated()
        persisted = self.store.load_artifact(
            receipt.receipt_id,
            EXECUTION_RECEIPT_MEDIA_TYPE,
        )
        if persisted.payload != receipt.model_dump(mode="json"):
            raise IntegrityError("OUTCOME_EXECUTION_RECEIPT_NOT_CANONICAL")
        by_ref = {item.evidence_id: item.revalidated() for item in evidence}
        if len(by_ref) != len(evidence) or tuple(sorted(by_ref, key=str.encode)) != receipt.evidence_refs:
            raise IntegrityError("OUTCOME_EVIDENCE_SET_MISMATCH")
        for ref, digest in zip(receipt.evidence_refs, receipt.evidence_digests, strict=True):
            item = by_ref[ref]
            stored = self.store.load_artifact(
                ref,
                "application/vnd.orgrebase.oac-execution-evidence+json",
            )
            if item.digest != digest:
                raise IntegrityError("OUTCOME_EVIDENCE_DIGEST_MISMATCH")
            if stored.payload != item.model_dump(mode="json"):
                raise IntegrityError("OUTCOME_EVIDENCE_NOT_CANONICAL")

    def _verify_observation(
        self,
        receipt: OACExecutionReceipt,
        observation: OutcomeObservation,
    ) -> OutcomeObservation:
        try:
            observation = observation.revalidated()
        except (TypeError, ValueError) as exc:
            raise IntegrityError("OUTCOME_OBSERVATION_MODEL_INVALID") from exc
        runtime_authorities = {receipt.executor_id, receipt.runtime_owner_id}
        observation_authorities = {
            observation.producer_id,
            observation.producer_authority_ref,
        }
        if runtime_authorities & observation_authorities:
            raise IntegrityError("OUTCOME_OBSERVATION_AUTHORITY_COLLIDES_WITH_RUNTIME")
        if {ORACLE_ID, ORACLE_AUTHORITY} & observation_authorities:
            raise IntegrityError("OUTCOME_OBSERVATION_AUTHORITY_COLLIDES_WITH_ORACLE")
        if observation.producer_authority_ref != OBSERVATION_PRODUCER_AUTHORITY:
            raise IntegrityError("OUTCOME_OBSERVATION_AUTHORITY_NOT_ADMITTED")
        if (
            observation.execution_receipt_ref != receipt.receipt_id
            or observation.execution_receipt_digest != receipt.digest
            or observation.subject_ref != "alternative:acieries-savoie"
            or observation.demand_ref != receipt.demand_ref
        ):
            raise IntegrityError("OUTCOME_OBSERVATION_EXECUTION_BINDING_MISMATCH")
        facts = {fact.name: fact for fact in observation.facts}
        if set(facts) != set(_EXPECTED_VALUES):
            raise IntegrityError("OUTCOME_OBSERVATION_FACT_SET_MISMATCH")
        persisted = self.store.load_artifact(
            observation.observation_id,
            OUTCOME_OBSERVATION_MEDIA_TYPE,
        )
        if persisted.payload != observation.model_dump(mode="json"):
            raise IntegrityError("OUTCOME_OBSERVATION_NOT_CANONICAL")
        return observation

    @staticmethod
    def _provenance_dimension(
        receipt: OACExecutionReceipt,
        evidence: tuple[ExecutionEvidence, ...],
    ) -> OutcomeDimension:
        valid = all(
            item.plan_digest == receipt.plan_digest
            and item.subject_ref == "alternative:acieries-savoie"
            and item.target_writes == 0
            and item.external_effects == "NONE"
            and item.evidence_id in receipt.evidence_refs
            for item in evidence
        )
        return _dimension(
            "evidence_provenance",
            "PASS" if valid else "FAIL",
            "CONTROLLED_EVIDENCE_PROVENANCE_VALID" if valid else "CONTROLLED_EVIDENCE_PROVENANCE_INVALID",
            tuple(item.evidence_id for item in evidence),
        )

    @staticmethod
    def _effect_dimension(
        receipt: OACExecutionReceipt,
        evidence: tuple[ExecutionEvidence, ...],
    ) -> OutcomeDimension:
        valid = (
            receipt.target_writes == 0
            and receipt.external_effects == "NONE"
            and all(item.target_writes == 0 and item.external_effects == "NONE" for item in evidence)
        )
        return _dimension(
            "effect_reconciliation",
            "PASS" if valid else "FAIL",
            "ZERO_EFFECT_RECONCILED" if valid else "FORBIDDEN_EFFECT_OBSERVED",
            tuple(item.evidence_id for item in evidence),
        )

    @staticmethod
    def _execution_dimension(receipt: OACExecutionReceipt) -> OutcomeDimension:
        valid = receipt.status is ExecutionStatus.COMPLETED
        return _dimension(
            "execution_completeness",
            "PASS" if valid else "UNKNOWN",
            "EXECUTION_COMPLETED" if valid else "EXECUTION_NOT_COMPLETED",
            (receipt.receipt_id,),
        )

    @staticmethod
    def _obligation_dimensions(
        observation: OutcomeObservation,
    ) -> tuple[OutcomeDimension, ...]:
        facts = {fact.name: fact for fact in observation.facts}
        dimensions: list[OutcomeDimension] = []
        for obligation_type, (name, expected_value) in sorted(_EXPECTED_VALUES.items()):
            fact = facts[obligation_type]
            if fact.state != "KNOWN":
                dimensions.append(
                    _dimension(
                        name,
                        "UNKNOWN",
                        f"{obligation_type.upper().replace('-', '_')}_NOT_OBSERVABLE",
                        (observation.observation_id,),
                    )
                )
                continue
            valid = fact.value == expected_value
            dimensions.append(
                _dimension(
                    name,
                    "PASS" if valid else "FAIL",
                    f"{obligation_type.upper().replace('-', '_')}_SATISFIED"
                    if valid
                    else f"{obligation_type.upper().replace('-', '_')}_NOT_SATISFIED",
                    (observation.observation_id,),
                )
            )
        return tuple(dimensions)

    def evaluate(
        self,
        receipt: OACExecutionReceipt,
        evidence: tuple[ExecutionEvidence, ...],
        observation: OutcomeObservation,
        *,
        decision_id: str,
        issued_at: str = "2026-08-26T00:02:01Z",
        oracle_id: str = ORACLE_ID,
        issuer_authority_ref: str = ORACLE_AUTHORITY,
    ) -> tuple[OutcomeObservation, LocalOutcomeDecision]:
        receipt = receipt.revalidated()
        evidence = tuple(item.revalidated() for item in evidence)
        self._verify_persisted_inputs(receipt, evidence)
        observation = self._verify_observation(receipt, observation)
        if oracle_id in {receipt.executor_id, receipt.runtime_owner_id} or issuer_authority_ref in {
            receipt.executor_id,
            receipt.runtime_owner_id,
        }:
            raise IntegrityError("OUTCOME_AUTHORITY_COLLIDES_WITH_RUNTIME")
        dimensions = (
            self._execution_dimension(receipt),
            self._effect_dimension(receipt, evidence),
            self._provenance_dimension(receipt, evidence),
            *self._obligation_dimensions(observation),
        )
        dimensions = tuple(sorted(dimensions, key=lambda item: item.name.encode()))
        verdicts = {item.verdict for item in dimensions}
        verdict = (
            OutcomeVerdict.REJECT
            if "FAIL" in verdicts
            else OutcomeVerdict.UNKNOWN
            if "UNKNOWN" in verdicts
            else OutcomeVerdict.ACCEPT
        )
        decision = LocalOutcomeDecision(
            decision_id=decision_id,
            observation_ref=observation.observation_id,
            observation_digest=observation.digest,
            execution_receipt_ref=receipt.receipt_id,
            execution_receipt_digest=receipt.digest,
            verdict=verdict,
            dimensions=dimensions,
            reason_codes=(
                "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ACCEPT"
                if verdict is OutcomeVerdict.ACCEPT
                else "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_REJECT"
                if verdict is OutcomeVerdict.REJECT
                else "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_UNKNOWN",
            ),
            oracle_id=oracle_id,
            oracle_build=ORACLE_BUILD,
            issuer_authority_ref=issuer_authority_ref,
            issued_at=issued_at,
        )
        key = f"oac-outcome:{decision_id}"
        request_digest = sha256_digest(
            {
                "receipt_digest": receipt.digest,
                "evidence_digests": list(receipt.evidence_digests),
                "observation_digest": observation.digest,
                "decision_id": decision_id,
                "oracle_policy_digest": oracle_policy_digest(),
                "issued_at": issued_at,
            }
        )
        existing = self.store.get_idempotent(key, request_digest)
        if existing is not None:
            return (
                observation,
                LocalOutcomeDecision.model_validate(existing["decision"]),
            )
        result: Mapping[str, object] = {
            "decision": decision.model_dump(mode="json"),
        }
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                decision.decision_id,
                OUTCOME_DECISION_MEDIA_TYPE,
                decision.model_dump(mode="json"),
            )
            self.store.append_event(
                connection,
                "OAC_CONTROLLED_OUTCOME_ISSUED",
                {
                    "decision_ref": decision.decision_id,
                    "decision_digest": decision.digest,
                    "verdict": decision.verdict.value,
                    "claim_boundary": "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
                },
            )
            self.store.save_idempotent(connection, key, request_digest, result)
        return observation, decision


class ControlledOutcomeObservationProducer:
    """Persist an observation before the independent oracle evaluates it."""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def record(
        self,
        receipt: OACExecutionReceipt,
        *,
        observation_id: str,
        actual_values: Mapping[str, str],
        producer_id: str = OBSERVATION_PRODUCER_ID,
        producer_authority_ref: str = OBSERVATION_PRODUCER_AUTHORITY,
        produced_at: str = "2026-08-26T00:02:00Z",
    ) -> OutcomeObservation:
        receipt = receipt.revalidated()
        if producer_id in {receipt.executor_id, receipt.runtime_owner_id, ORACLE_ID} or (
            producer_authority_ref in {receipt.executor_id, receipt.runtime_owner_id, ORACLE_AUTHORITY}
        ):
            raise IntegrityError("OUTCOME_OBSERVATION_AUTHORITY_COLLISION")
        if producer_id != OBSERVATION_PRODUCER_ID or producer_authority_ref != OBSERVATION_PRODUCER_AUTHORITY:
            raise IntegrityError("OUTCOME_OBSERVATION_PRODUCER_NOT_ADMITTED")
        persisted_receipt = self.store.load_artifact(
            receipt.receipt_id,
            EXECUTION_RECEIPT_MEDIA_TYPE,
        )
        if persisted_receipt.payload != receipt.model_dump(mode="json"):
            raise IntegrityError("OUTCOME_OBSERVATION_EXECUTION_NOT_CANONICAL")
        observation = build_controlled_outcome_observation(
            receipt,
            observation_id=observation_id,
            actual_values=actual_values,
            producer_id=producer_id,
            producer_authority_ref=producer_authority_ref,
            produced_at=produced_at,
        )
        request_digest = sha256_digest(
            {
                "receipt_digest": receipt.digest,
                "observation_digest": observation.digest,
            }
        )
        key = f"oac-observation:{observation_id}"
        existing = self.store.get_idempotent(key, request_digest)
        if existing is not None:
            return OutcomeObservation.model_validate(existing)
        payload = observation.model_dump(mode="json")
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                observation.observation_id,
                OUTCOME_OBSERVATION_MEDIA_TYPE,
                payload,
            )
            self.store.append_event(
                connection,
                "OAC_CONTROLLED_OBSERVATION_RECORDED",
                {
                    "observation_ref": observation.observation_id,
                    "observation_digest": observation.digest,
                    "execution_receipt_ref": receipt.receipt_id,
                    "claim_boundary": "SYNTHETIC_CONTROLLED_OBSERVATION_ONLY",
                },
            )
            self.store.save_idempotent(connection, key, request_digest, payload)
        return observation
