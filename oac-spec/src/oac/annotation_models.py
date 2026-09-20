"""Versioned, zero-effect documents owned by the human annotation profile."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from .json_types import JsonValue
from .models import (
    CandidateConstraintSet,
    Digest,
    Identifier,
    ResourceMetadata,
    ResourceRef,
    StrictModel,
)

PROTOCOL = "oac.annotation-document/v1"
type QualificationBasis = Literal["CONTROLLED_LOCAL_FIXTURE", "GOVERNANCE_ATTESTED_HUMAN"]


def _strict_round(value: object) -> object:
    if type(value) is not int:
        raise ValueError("annotation round requires a JSON integer")
    return value


type AnnotationRound = Annotated[Literal[1, 2], BeforeValidator(_strict_round)]


def _require_interval(start: datetime, end: datetime) -> None:
    if start.utcoffset() is None or end.utcoffset() is None or end <= start:
        raise ValueError("annotation qualification requires an aware increasing interval")


class AnnotationDocument[SpecT: StrictModel](StrictModel):
    api_version: Literal["oac.annotation-document/v1"] = Field(
        default="oac.annotation-document/v1", alias="apiVersion"
    )
    kind: Identifier
    metadata: ResourceMetadata
    spec: SpecT
    digest: Digest


class ReviewerIdentity(StrictModel):
    person_key: Identifier = Field(alias="personKey")
    subjects: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    actor_type: Literal["human", "model", "agent", "ai_controlled", "scripted"] = Field(
        alias="actorType"
    )
    qualification_refs: tuple[ResourceRef, ...] = Field(alias="qualificationRefs", min_length=1)


class AnnotationAuthoritySpec(StrictModel):
    qualification_basis: QualificationBasis = Field(alias="qualificationBasis")
    governance_ref: Identifier = Field(alias="governanceRef")
    identities: tuple[ReviewerIdentity, ...] = Field(min_length=1, max_length=128)
    case_refs: tuple[ResourceRef, ...] = Field(alias="caseRefs", min_length=1, max_length=128)
    valid_from: datetime = Field(alias="validFrom")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def distinct_directory(self) -> AnnotationAuthoritySpec:
        people = [member.person_key for member in self.identities]
        subjects = [subject for member in self.identities for subject in member.subjects]
        if len(set(people)) != len(people) or len(set(subjects)) != len(subjects):
            raise ValueError("annotation identity directory contains an ambiguous subject")
        _require_interval(self.valid_from, self.valid_until)
        return self


class ReviewerQualificationSpec(StrictModel):
    qualification_basis: QualificationBasis = Field(alias="qualificationBasis")
    governance_ref: Identifier = Field(alias="governanceRef")
    person_key: Identifier = Field(alias="personKey")
    case_refs: tuple[ResourceRef, ...] = Field(alias="caseRefs", min_length=1)
    actions: tuple[Literal["REVIEW", "ADJUDICATE", "PROMOTE", "RETRACT"], ...] = Field(min_length=1)
    valid_from: datetime = Field(alias="validFrom")
    valid_until: datetime = Field(alias="validUntil")
    evidence_statement: Identifier = Field(alias="evidenceStatement")

    @model_validator(mode="after")
    def current_interval(self) -> ReviewerQualificationSpec:
        _require_interval(self.valid_from, self.valid_until)
        return self


class EvidenceCitation(StrictModel):
    resource_ref: ResourceRef = Field(alias="resourceRef")
    pointer: Identifier


class AnnotationPacketSpec(StrictModel):
    authority_ref: ResourceRef = Field(alias="authorityRef")
    case_ref: ResourceRef = Field(alias="caseRef")
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    change_ref: ResourceRef = Field(alias="changeRef")
    observation_boundary: Identifier = Field(alias="observationBoundary")
    allowed_evidence: tuple[EvidenceCitation, ...] = Field(
        alias="allowedEvidence", min_length=1, max_length=128
    )


class BlindDeliverySpec(StrictModel):
    packet_ref: ResourceRef = Field(alias="packetRef")
    reviewer_key: Identifier = Field(alias="reviewerKey")
    round: AnnotationRound
    view_digest: Digest = Field(alias="viewDigest")


class ReviewField(StrictModel):
    field: Identifier
    value: JsonValue
    status: Literal["supported", "unknown", "contested"]
    evidence: tuple[EvidenceCitation, ...]
    rationale: Identifier


class ReviewAttestationSpec(StrictModel):
    packet_ref: ResourceRef = Field(alias="packetRef")
    delivery_ref: ResourceRef = Field(alias="deliveryRef")
    reviewer_key: Identifier = Field(alias="reviewerKey")
    round: AnnotationRound
    fields: tuple[ReviewField, ...] = Field(min_length=1, max_length=32)
    model_assistance: bool = Field(alias="modelAssistance")


class AdjudicationResolution(StrictModel):
    field: Identifier
    value: JsonValue
    status: Literal["supported", "unknown", "contested"]
    evidence: tuple[EvidenceCitation, ...]
    accepted_rationale: Identifier = Field(alias="acceptedRationale")
    rejected_rationale: Identifier = Field(alias="rejectedRationale")


class AdjudicationRecordSpec(StrictModel):
    packet_ref: ResourceRef = Field(alias="packetRef")
    review_refs: tuple[ResourceRef, ResourceRef] = Field(alias="reviewRefs")
    adjudicator_key: Identifier = Field(alias="adjudicatorKey")
    conflict_fields: tuple[Identifier, ...] = Field(alias="conflictFields")
    resolutions: tuple[AdjudicationResolution, ...] = Field(max_length=32)
    constraint_set: CandidateConstraintSet = Field(alias="constraintSet")
    constraint_digest: Digest = Field(alias="constraintDigest")


class GoldPromotionSpec(StrictModel):
    qualification_basis: QualificationBasis = Field(alias="qualificationBasis")
    packet_ref: ResourceRef = Field(alias="packetRef")
    case_ref: ResourceRef = Field(alias="caseRef")
    source_refs: tuple[ResourceRef, ResourceRef] = Field(alias="sourceRefs")
    review_refs: tuple[ResourceRef, ResourceRef] = Field(alias="reviewRefs")
    adjudication_ref: ResourceRef = Field(alias="adjudicationRef")
    constraint_digest: Digest = Field(alias="constraintDigest")
    governor_key: Identifier = Field(alias="governorKey")
    unresolved_fields: tuple[Identifier, ...] = Field(alias="unresolvedFields")
    claim: Literal["QUALIFIED_ANNOTATION_ONLY_NOT_ENTERPRISE_OUTCOME"] = (
        "QUALIFIED_ANNOTATION_ONLY_NOT_ENTERPRISE_OUTCOME"
    )


class AnnotationBenchmarkClaimSpec(StrictModel):
    qualification_basis: QualificationBasis = Field(alias="qualificationBasis")
    promotion_ref: ResourceRef = Field(alias="promotionRef")
    benchmark_run_digest: Digest = Field(alias="benchmarkRunDigest")
    dependent_claim_refs: tuple[ResourceRef, ...] = Field(alias="dependentClaimRefs")
    claim: Literal["ANNOTATION_LINEAGE_ONLY"] = "ANNOTATION_LINEAGE_ONLY"


class AnnotationRetractionSpec(StrictModel):
    packet_ref: ResourceRef = Field(alias="packetRef")
    target_ref: ResourceRef = Field(alias="targetRef")
    predecessor_head: Digest = Field(alias="predecessorHead")
    affected_refs: tuple[ResourceRef, ...] = Field(alias="affectedRefs", min_length=1)
    governor_key: Identifier = Field(alias="governorKey")
    reason: Identifier


ANNOTATION_MODELS: dict[str, type[StrictModel]] = {
    "AnnotationAuthority": AnnotationDocument[AnnotationAuthoritySpec],
    "ReviewerQualification": AnnotationDocument[ReviewerQualificationSpec],
    "AnnotationPacket": AnnotationDocument[AnnotationPacketSpec],
    "BlindDelivery": AnnotationDocument[BlindDeliverySpec],
    "ReviewAttestation": AnnotationDocument[ReviewAttestationSpec],
    "AdjudicationRecord": AnnotationDocument[AdjudicationRecordSpec],
    "GoldPromotionCertificate": AnnotationDocument[GoldPromotionSpec],
    "AnnotationBenchmarkClaim": AnnotationDocument[AnnotationBenchmarkClaimSpec],
    "AnnotationRetraction": AnnotationDocument[AnnotationRetractionSpec],
}


class DeliveryCommand(StrictModel):
    action: Literal["DELIVER"]
    round: AnnotationRound


class SubmissionCommand(StrictModel):
    action: Literal["SUBMIT"]
    delivery_ref: ResourceRef = Field(alias="deliveryRef")
    fields: tuple[ReviewField, ...]
    model_assistance: bool = Field(alias="modelAssistance")


class AdjudicationCommand(StrictModel):
    action: Literal["ADJUDICATE"]
    resolutions: tuple[AdjudicationResolution, ...]
    constraint_set: CandidateConstraintSet = Field(alias="constraintSet")


class PromotionCommand(StrictModel):
    action: Literal["PROMOTE"]


class BenchmarkCommand(StrictModel):
    action: Literal["BENCHMARK"]
    run_digest: Digest = Field(alias="runDigest")
    dependencies: tuple[ResourceRef, ...]


class RetractionCommand(StrictModel):
    action: Literal["RETRACT"]
    target_ref: ResourceRef = Field(alias="targetRef")
    reason: Identifier


class AnnotationAction(StrictModel):
    subject: Identifier
    at: datetime
    command: (
        DeliveryCommand
        | SubmissionCommand
        | AdjudicationCommand
        | PromotionCommand
        | BenchmarkCommand
        | RetractionCommand
    ) = Field(discriminator="action")


class AnnotationArchive(StrictModel):
    protocol: Literal["oac.annotation-archive/v1"]
    authority: dict[str, JsonValue]
    packet: dict[str, JsonValue]
    case: dict[str, JsonValue]
    sources: tuple[dict[str, JsonValue], ...]
    qualifications: tuple[dict[str, JsonValue], ...]
    records: tuple[dict[str, JsonValue], ...]
    actions: tuple[AnnotationAction, ...]
    head: Digest
