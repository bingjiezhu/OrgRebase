"""Caller-pinned human annotation, blind delivery, and immutable proof lineage.

This bounded protocol has no network, identity login, compiler, or external
writes. The host supplies authenticated subjects, trusted authority/packet pins
and time. Pseudonymous qualification assertions are supplied by that authority;
local tests cannot establish that a real person performed a review.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NoReturn

from pydantic import ValidationError

from .annotation_models import (
    ANNOTATION_MODELS,
    PROTOCOL,
    AdjudicationRecordSpec,
    AdjudicationResolution,
    AnnotationAuthoritySpec,
    AnnotationBenchmarkClaimSpec,
    AnnotationDocument,
    AnnotationPacketSpec,
    AnnotationRetractionSpec,
    BlindDeliverySpec,
    EvidenceCitation,
    GoldPromotionSpec,
    QualificationBasis,
    ReviewAttestationSpec,
    ReviewerIdentity,
    ReviewerQualificationSpec,
    ReviewField,
)
from .canonical import OACValidationError
from .json_types import JsonValue
from .models import (
    CandidateConstraintSet,
    OrgChangeCase,
    ResourceMetadata,
    ResourceRef,
    StrictModel,
)
from .sealed import (
    AdmittedSealedResource,
    _decode_raw_object,
    _exact_json_equal,
    _jcs,
    _thaw_json,
    validate_sealed_admission,
)

# Complete fields of the existing constraint-set contract, including empty sets.
# Reviewer-facing questions carry no candidate labels or selected topology.
FIELD_CATEGORIES = {
    "requiredObligationRefs": "obligation",
    "requiredObligationTypes": "obligation",
    "admissibleRoleRefs": "role",
    "admissiblePrincipalRefs": "authority",
    "forbiddenCombinations": "authority",
    "happensBefore": "order",
    "evidenceDuties": "evidence",
    "acceptableUnknownRefs": "unknown",
    "minimalityLevel": "authority",
    "acceptableVerdicts": "unknown",
}
REASON_CODES = {
    "ANNOTATION_DOCUMENT_INVALID": "A versioned annotation document is malformed or changes under typed admission.",
    "ANNOTATION_PIN_MISMATCH": "The material does not match a separately pinned exact authority, packet or resource reference.",
    "ANNOTATION_AUTHORITY_DENIED": "The current subject lacks live, exact-scope human qualification.",
    "ANNOTATION_BLINDING_VIOLATION": "The requested delivery violates the independent blind round protocol.",
    "ANNOTATION_FIELD_COVERAGE_INVALID": "Review or adjudication fields do not cover the complete declared constraint contract.",
    "ANNOTATION_EVIDENCE_INVALID": "A field does not cite exact admitted, packet-approved source evidence.",
    "ANNOTATION_REVIEW_INVALID": "The immutable review does not bind its one authorized blind delivery.",
    "ANNOTATION_ADJUDICATION_INVALID": "Conflicts, distinct adjudicator or exact resolved constraint set do not verify.",
    "ANNOTATION_PROMOTION_INVALID": "Promotion does not bind the complete exact qualified proof chain.",
    "ANNOTATION_GOLD_NOT_QUALIFIED": "A controlled-local fixture does not establish governance-attested human qualification.",
    "ANNOTATION_RETRACTED": "A proof dependency has been immutably retracted.",
    "ANNOTATION_ARCHIVE_INVALID": "An archive does not bind its caller-pinned root or replayed protocol history.",
}


def _fail(code: str, message: str) -> NoReturn:
    raise OACValidationError(code, message)


def _digest(value: JsonValue) -> str:
    return "sha256:" + hashlib.sha256(_jcs(value)).hexdigest()


def _wire(value: StrictModel) -> dict[str, JsonValue]:
    return value.model_dump(mode="json", by_alias=True)


def _constraint(value: CandidateConstraintSet) -> dict[str, JsonValue]:
    result = _wire(value)
    for key in FIELD_CATEGORIES:
        result.setdefault(key, [])
    return result


def _key(ref: ResourceRef) -> tuple[str, str, str, int, str]:
    return ref.kind, ref.namespace, ref.resource_id, ref.revision, ref.digest


@dataclass(frozen=True, slots=True)
class AdmittedAnnotation:
    raw: bytes
    document: AnnotationDocument[StrictModel]
    ref: ResourceRef


def admit_annotation(raw: bytes, expected_kind: str) -> AdmittedAnnotation:
    """Check raw document identity and shape; this grants no review or Gold authority."""
    if len(raw) > 1_048_576 or expected_kind not in ANNOTATION_MODELS:
        _fail("ANNOTATION_DOCUMENT_INVALID", "unknown kind or material byte ceiling exceeded")
    decoded = _decode_annotation_object(raw, "ANNOTATION_DOCUMENT_INVALID")
    if decoded.get("apiVersion") != PROTOCOL or decoded.get("kind") != expected_kind:
        _fail("ANNOTATION_DOCUMENT_INVALID", "explicit annotation protocol and kind required")
    projection = dict(decoded)
    claimed = projection.pop("digest", None)
    if claimed != _digest(projection):
        _fail("ANNOTATION_PIN_MISMATCH", "detached annotation digest does not verify")
    try:
        document = ANNOTATION_MODELS[expected_kind].model_validate_json(_jcs(decoded))
    except ValidationError as exc:
        raise OACValidationError("ANNOTATION_DOCUMENT_INVALID", str(exc)) from exc
    if not isinstance(document, AnnotationDocument):
        _fail("ANNOTATION_DOCUMENT_INVALID", "expected annotation envelope")
    if not _exact_json_equal(
        decoded, document.model_dump(mode="json", by_alias=True, exclude_unset=True)
    ):
        _fail("ANNOTATION_DOCUMENT_INVALID", "typed admission changed raw identity")
    ref = ResourceRef(
        kind=expected_kind,
        namespace=document.metadata.namespace,
        resourceId=document.metadata.id,
        revision=document.metadata.revision,
        digest=document.digest,
    )
    return AdmittedAnnotation(_jcs(decoded), document, ref)


def _decode_annotation_object(raw: bytes, reason_code: str) -> dict[str, Any]:
    try:
        return _decode_raw_object(raw, max_json_depth=64)
    except OACValidationError as exc:
        if exc.reason_code == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED":
            raise OACValidationError(reason_code, "annotation nesting ceiling exceeded") from exc
        raise


def seal_annotation(kind: str, metadata: ResourceMetadata, spec: StrictModel) -> AdmittedAnnotation:
    """Author a new profile document; never normalize an existing sealed input."""
    projection: dict[str, JsonValue] = {
        "apiVersion": PROTOCOL,
        "kind": kind,
        "metadata": _wire(metadata),
        "spec": _wire(spec),
    }
    return admit_annotation(_jcs({**projection, "digest": _digest(projection)}), kind)


def _pin(value: AdmittedAnnotation, expected: ResourceRef, kind: str) -> AdmittedAnnotation:
    actual = admit_annotation(value.raw, kind)
    if actual.ref != expected or actual.document != value.document or value.ref != actual.ref:
        _fail("ANNOTATION_PIN_MISMATCH", "exact annotation pin or admitted view mismatch")
    return actual


def _core_ref(value: AdmittedSealedResource) -> ResourceRef:
    resource = value.resource
    return ResourceRef(
        kind=resource.kind,
        namespace=resource.metadata.namespace,
        resourceId=resource.metadata.id,
        revision=resource.metadata.revision,
        digest=value.resource_digest,
    )


def _pointer(raw: Mapping[str, JsonValue], pointer: str) -> JsonValue:
    if not pointer.startswith("/spec/"):
        _fail("ANNOTATION_EVIDENCE_INVALID", "only explicit source fact pointers are admissible")
    value: JsonValue = raw
    for token in pointer[1:].split("/"):
        # RFC6901, one decoding pass; malformed escapes never get a fallback.
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    _fail("ANNOTATION_EVIDENCE_INVALID", "malformed evidence pointer escape")
                index += 1
            index += 1
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            if value.get("admissionStatus") not in (None, "admitted"):
                _fail(
                    "ANNOTATION_EVIDENCE_INVALID", "candidate/disputed source fact is not authority"
                )
            if key not in value:
                _fail("ANNOTATION_EVIDENCE_INVALID", "evidence pointer is absent")
            value = value[key]
        elif isinstance(value, Sequence) and not isinstance(value, str):
            if (
                not key.isascii()
                or not key.isdecimal()
                or str(int(key)) != key
                or int(key) >= len(value)
            ):
                _fail("ANNOTATION_EVIDENCE_INVALID", "evidence array index is invalid")
            value = value[int(key)]
        else:
            _fail("ANNOTATION_EVIDENCE_INVALID", "evidence pointer crosses a scalar")
    if value is None or isinstance(value, (Mapping, list, tuple)):
        _fail(
            "ANNOTATION_EVIDENCE_INVALID", "cite an observable source scalar, not an entire graph"
        )
    return value


class AnnotationLedger:
    """One case's caller-owned immutable annotation log and access boundary.

    Reviewers can obtain only ``deliver`` views; full proof export requires a
    qualified governor and two committed rounds. Host authentication must not
    let a caller choose another subject or the pinned authority/clock.
    """

    def __init__(
        self,
        *,
        authority: AdmittedAnnotation,
        authority_ref: ResourceRef,
        packet: AdmittedAnnotation,
        packet_ref: ResourceRef,
        case: AdmittedSealedResource,
        sources: tuple[AdmittedSealedResource, ...],
        qualifications: tuple[AdmittedAnnotation, ...],
        clock: Callable[[], datetime],
    ):
        self._authority = _pin(authority, authority_ref, "AnnotationAuthority")
        self._packet = _pin(packet, packet_ref, "AnnotationPacket")
        if not isinstance(self._authority.document.spec, AnnotationAuthoritySpec) or not isinstance(
            self._packet.document.spec, AnnotationPacketSpec
        ):
            _fail("ANNOTATION_DOCUMENT_INVALID", "typed profile document mismatch")
        self._policy = self._authority.document.spec
        self._scope = self._packet.document.spec
        self._case = validate_sealed_admission(case, "OrgChangeCase")
        assert isinstance(self._case.resource, OrgChangeCase)
        self._clock = clock
        self._qualifications = {
            _key(q.ref): _pin(q, q.ref, "ReviewerQualification") for q in qualifications
        }
        if len(self._qualifications) != len(qualifications):
            _fail("ANNOTATION_AUTHORITY_DENIED", "duplicate qualification material")
        self._sources = {
            _key(_core_ref(s)): validate_sealed_admission(s, s.resource.kind) for s in sources
        }
        expected_sources = {_key(self._scope.snapshot_ref), _key(self._scope.change_ref)}
        if (
            len(self._sources) != len(sources)
            or set(self._sources) != expected_sources
            or self._scope.snapshot_ref.kind != "OrganizationSnapshot"
            or self._scope.change_ref.kind != "SemanticChangeSet"
            or self._scope.case_ref != _core_ref(self._case)
            or self._scope.case_ref not in self._policy.case_refs
            or self._scope.authority_ref != authority_ref
            or self._case.resource.spec.snapshot_ref != self._scope.snapshot_ref
            or self._case.resource.spec.change_ref != self._scope.change_ref
            or self._case.resource.spec.observation_boundary != self._scope.observation_boundary
        ):
            _fail("ANNOTATION_PIN_MISMATCH", "case, packet, authority and source roots must match")
        governance = self._policy.governance_ref
        namespace = packet_ref.namespace
        if any(
            x.resource.metadata.namespace != namespace
            or x.resource.metadata.governance_ref != governance
            for x in (self._case,)
        ) or any(
            x.document.metadata.namespace != namespace
            or x.document.metadata.governance_ref != governance
            for x in (authority, packet)
        ):
            _fail("ANNOTATION_PIN_MISMATCH", "cross-namespace or governance input")
        if not self._scope.allowed_evidence or len(
            {_digest(_wire(c)) for c in self._scope.allowed_evidence}
        ) != len(self._scope.allowed_evidence):
            _fail("ANNOTATION_EVIDENCE_INVALID", "empty or duplicated evidence whitelist")
        for citation in self._scope.allowed_evidence:
            self._evidence_value(citation)
        self._records: list[AdmittedAnnotation] = []
        self._actions: list[dict[str, JsonValue]] = []
        self._last_time: datetime | None = None

    @property
    def head(self) -> str:
        return _digest(
            {
                "authority": _wire(self._authority.ref),
                "packet": _wire(self._packet.ref),
                "records": [_wire(record.ref) for record in self._records],
            }
        )

    def _now(self) -> datetime:
        now = self._clock()
        if (
            now.tzinfo is None
            or now.microsecond
            or (self._last_time is not None and now < self._last_time)
        ):
            _fail(
                "ANNOTATION_AUTHORITY_DENIED", "host clock must be monotone whole-second UTC time"
            )
        if not self._policy.valid_from <= now < self._policy.valid_until:
            _fail("ANNOTATION_AUTHORITY_DENIED", "pinned authority has expired or is not yet valid")
        return now

    def _actor(self, subject: str, action: str, now: datetime) -> ReviewerIdentity:
        matches = [p for p in self._policy.identities if subject in p.subjects]
        if len(matches) != 1 or matches[0].actor_type != "human":
            _fail("ANNOTATION_AUTHORITY_DENIED", "subject is not a qualified independent human")
        actor = matches[0]
        for qualification_ref in actor.qualification_refs:
            qualification = self._qualifications.get(_key(qualification_ref))
            if qualification is None:
                continue
            spec = qualification.document.spec
            if (
                isinstance(spec, ReviewerQualificationSpec)
                and qualification.ref.namespace == self._packet.ref.namespace
                and qualification.document.metadata.governance_ref == self._policy.governance_ref
                and spec.governance_ref == self._policy.governance_ref
                and spec.qualification_basis == self._policy.qualification_basis
                and spec.person_key == actor.person_key
                and action in spec.actions
                and self._scope.case_ref in spec.case_refs
                and spec.valid_from <= now < spec.valid_until
            ):
                return actor
        _fail("ANNOTATION_AUTHORITY_DENIED", "no exact-scope current qualification bytes resolve")

    def _append(
        self,
        kind: str,
        spec: StrictModel,
        subject: str,
        now: datetime,
        command: dict[str, JsonValue],
    ) -> AdmittedAnnotation:
        record = seal_annotation(
            kind,
            ResourceMetadata(
                id=f"{self._packet.ref.resource_id}/{len(self._records) + 1}",
                namespace=self._packet.ref.namespace,
                revision=1,
                ownerRef=subject,
                governanceRef=self._policy.governance_ref,
                createdAt=now,
                sourceRefs=(self._packet.ref.resource_id,),
            ),
            spec,
        )
        self._records.append(admit_annotation(record.raw, kind))
        self._actions.append({"subject": subject, "at": now.isoformat(), "command": command})
        self._last_time = now
        return record

    def _by_kind(self, kind: str) -> list[AdmittedAnnotation]:
        return [
            admit_annotation(record.raw, kind)
            for record in self._records
            if record.ref.kind == kind
        ]

    def _evidence_value(self, citation: EvidenceCitation) -> JsonValue:
        source = self._sources.get(_key(citation.resource_ref))
        if source is None:
            _fail("ANNOTATION_EVIDENCE_INVALID", "evidence is not an exact packet source")
        raw = _thaw_json(source.raw_map)
        assert isinstance(raw, Mapping)
        return _pointer(raw, citation.pointer)

    def _fields(self, fields: tuple[ReviewField, ...]) -> dict[str, ReviewField]:
        by_field = {item.field: item for item in fields}
        if len(by_field) != len(fields) or set(by_field) != set(FIELD_CATEGORIES):
            _fail(
                "ANNOTATION_FIELD_COVERAGE_INVALID",
                "all constraint fields must appear exactly once",
            )
        allowed = {_digest(_wire(c)) for c in self._scope.allowed_evidence}
        for item in fields:
            citations = [_digest(_wire(c)) for c in item.evidence]
            if (
                len(citations) != len(set(citations))
                or not set(citations) <= allowed
                or (item.status == "supported" and not citations)
            ):
                _fail(
                    "ANNOTATION_EVIDENCE_INVALID",
                    "supported fields need unique packet-approved evidence",
                )
            for citation in item.evidence:
                self._evidence_value(citation)
        return by_field

    def _active(self, ref: ResourceRef) -> None:
        for record in self._by_kind("AnnotationRetraction"):
            spec = record.document.spec
            assert isinstance(spec, AnnotationRetractionSpec)
            if ref in spec.affected_refs:
                _fail("ANNOTATION_RETRACTED", "an exact proof dependency was retracted")

    def deliver(
        self, subject: str, round_number: Literal[1, 2]
    ) -> tuple[AdmittedAnnotation, dict[str, JsonValue]]:
        now = self._now()
        actor = self._actor(subject, "REVIEW", now)
        self._active(self._packet.ref)
        reviews = self._by_kind("ReviewAttestation")
        deliveries = self._by_kind("BlindDelivery")
        for previous in reviews:
            self._active(previous.ref)
        if (
            type(round_number) is not int
            or round_number not in (1, 2)
            or len(reviews) != round_number - 1
            or len(deliveries) != round_number - 1
        ):
            _fail(
                "ANNOTATION_BLINDING_VIOLATION",
                "round must follow the preceding immutable commitment",
            )
        if any(
            isinstance(r.document.spec, ReviewAttestationSpec)
            and r.document.spec.reviewer_key == actor.person_key
            for r in reviews
        ):
            _fail(
                "ANNOTATION_BLINDING_VIOLATION",
                "another alias of the same person cannot perform round two",
            )
        view: dict[str, JsonValue] = {
            "protocol": PROTOCOL,
            "packetRef": _wire(self._packet.ref),
            "round": round_number,
            "questions": [
                {"field": field, "category": category}
                for field, category in FIELD_CATEGORIES.items()
            ],
            "observationBoundary": self._scope.observation_boundary,
            "evidence": [
                {"citation": _wire(c), "value": self._evidence_value(c)}
                for c in self._scope.allowed_evidence
            ],
        }
        record = self._append(
            "BlindDelivery",
            BlindDeliverySpec(
                packetRef=self._packet.ref,
                reviewerKey=actor.person_key,
                round=round_number,
                viewDigest=_digest(view),
            ),
            subject,
            now,
            {"action": "DELIVER", "round": round_number},
        )
        return record, view

    def submit(
        self,
        subject: str,
        *,
        delivery_ref: ResourceRef,
        fields: tuple[ReviewField, ...],
        model_assistance: bool = False,
    ) -> AdmittedAnnotation:
        now = self._now()
        actor = self._actor(subject, "REVIEW", now)
        self._active(self._packet.ref)
        deliveries = self._by_kind("BlindDelivery")
        reviews = self._by_kind("ReviewAttestation")
        if (
            not deliveries
            or len(deliveries) != len(reviews) + 1
            or delivery_ref != deliveries[-1].ref
            or model_assistance
        ):
            _fail(
                "ANNOTATION_REVIEW_INVALID",
                "review requires its unconsumed blind delivery and no model-assisted round",
            )
        self._active(delivery_ref)
        delivery = deliveries[-1].document.spec
        assert isinstance(delivery, BlindDeliverySpec)
        if delivery.reviewer_key != actor.person_key:
            _fail("ANNOTATION_REVIEW_INVALID", "review subject differs from delivery recipient")
        self._fields(fields)
        return self._append(
            "ReviewAttestation",
            ReviewAttestationSpec(
                packetRef=self._packet.ref,
                deliveryRef=delivery_ref,
                reviewerKey=actor.person_key,
                round=delivery.round,
                fields=fields,
                modelAssistance=False,
            ),
            subject,
            now,
            {
                "action": "SUBMIT",
                "deliveryRef": _wire(delivery_ref),
                "fields": [_wire(x) for x in fields],
                "modelAssistance": False,
            },
        )

    def review_material(self, subject: str) -> tuple[AdmittedAnnotation, AdmittedAnnotation]:
        now = self._now()
        actor = self._actor(subject, "ADJUDICATE", now)
        reviews = self._by_kind("ReviewAttestation")
        if len(reviews) != 2 or any(
            isinstance(r.document.spec, ReviewAttestationSpec)
            and r.document.spec.reviewer_key == actor.person_key
            for r in reviews
        ):
            _fail(
                "ANNOTATION_BLINDING_VIOLATION",
                "only a distinct adjudicator sees two committed rounds",
            )
        for review in reviews:
            self._active(review.ref)
        return reviews[0], reviews[1]

    def adjudicate(
        self,
        subject: str,
        *,
        resolutions: tuple[AdjudicationResolution, ...],
        constraint_set: CandidateConstraintSet,
    ) -> AdmittedAnnotation:
        now = self._now()
        actor = self._actor(subject, "ADJUDICATE", now)
        first, second = self.review_material(subject)
        self._active(self._packet.ref)
        if self._by_kind("AdjudicationRecord"):
            _fail(
                "ANNOTATION_ADJUDICATION_INVALID",
                "adjudication is immutable; use a new packet to revise",
            )
        a = first.document.spec
        b = second.document.spec
        assert isinstance(a, ReviewAttestationSpec) and isinstance(b, ReviewAttestationSpec)
        left = self._fields(a.fields)
        right = self._fields(b.fields)
        conflicts = tuple(
            field
            for field in FIELD_CATEGORIES
            if (
                left[field].status != right[field].status
                or left[field].status == "contested"
                or _jcs(left[field].value) != _jcs(right[field].value)
            )
        )
        resolved = {x.field: x for x in resolutions}
        if len(resolved) != len(resolutions) or set(resolved) != set(conflicts):
            _fail(
                "ANNOTATION_ADJUDICATION_INVALID",
                "every and only conflict needs a reasoned distinct resolution",
            )
        final = dict(left)
        for field, value in resolved.items():
            final[field] = ReviewField(
                field=field,
                value=value.value,
                status=value.status,
                evidence=value.evidence,
                rationale=value.accepted_rationale,
            )
        self._fields(tuple(final.values()))
        if _jcs({field: value.value for field, value in final.items()}) != _jcs(
            _constraint(constraint_set)
        ):
            _fail(
                "ANNOTATION_ADJUDICATION_INVALID",
                "constraint set differs from every exact resolved field",
            )
        return self._append(
            "AdjudicationRecord",
            AdjudicationRecordSpec(
                packetRef=self._packet.ref,
                reviewRefs=(first.ref, second.ref),
                adjudicatorKey=actor.person_key,
                conflictFields=conflicts,
                resolutions=resolutions,
                constraintSet=constraint_set,
                constraintDigest=_digest(_constraint(constraint_set)),
            ),
            subject,
            now,
            {
                "action": "ADJUDICATE",
                "resolutions": [_wire(x) for x in resolutions],
                "constraintSet": _wire(constraint_set),
            },
        )

    def promote(self, subject: str) -> AdmittedAnnotation:
        now = self._now()
        actor = self._actor(subject, "PROMOTE", now)
        self._active(self._packet.ref)
        reviews = self._by_kind("ReviewAttestation")
        adjudications = self._by_kind("AdjudicationRecord")
        if (
            len(reviews) != 2
            or len(adjudications) != 1
            or self._by_kind("GoldPromotionCertificate")
        ):
            _fail(
                "ANNOTATION_PROMOTION_INVALID",
                "promotion needs two rounds and one distinct adjudication",
            )
        for record in (*reviews, *adjudications):
            self._active(record.ref)
        adjudication = adjudications[0].document.spec
        assert isinstance(adjudication, AdjudicationRecordSpec)
        first = reviews[0].document.spec
        assert isinstance(first, ReviewAttestationSpec)
        statuses = {x.field: x.status for x in first.fields}
        statuses.update({x.field: x.status for x in adjudication.resolutions})
        return self._append(
            "GoldPromotionCertificate",
            GoldPromotionSpec(
                qualificationBasis=self._policy.qualification_basis,
                packetRef=self._packet.ref,
                caseRef=self._scope.case_ref,
                sourceRefs=(self._scope.snapshot_ref, self._scope.change_ref),
                reviewRefs=(reviews[0].ref, reviews[1].ref),
                adjudicationRef=adjudications[0].ref,
                constraintDigest=adjudication.constraint_digest,
                governorKey=actor.person_key,
                unresolvedFields=tuple(
                    field for field in FIELD_CATEGORIES if statuses[field] != "supported"
                ),
            ),
            subject,
            now,
            {"action": "PROMOTE"},
        )

    def benchmark_claim(
        self, subject: str, *, run_digest: str, dependencies: tuple[ResourceRef, ...] = ()
    ) -> AdmittedAnnotation:
        now = self._now()
        self._actor(subject, "PROMOTE", now)
        promotions = self._by_kind("GoldPromotionCertificate")
        if len(promotions) != 1:
            _fail("ANNOTATION_PROMOTION_INVALID", "a benchmark claim needs a complete promotion")
        self._active(promotions[0].ref)
        if any(
            isinstance(r.document.spec, AnnotationBenchmarkClaimSpec)
            and r.document.spec.benchmark_run_digest == run_digest
            for r in self._by_kind("AnnotationBenchmarkClaim")
        ):
            _fail(
                "ANNOTATION_PROMOTION_INVALID",
                "a benchmark run cannot be republished to revive a withdrawn claim",
            )
        known = {r.ref for r in self._by_kind("AnnotationBenchmarkClaim")}
        if len(set(dependencies)) != len(dependencies) or not set(dependencies) <= known:
            _fail(
                "ANNOTATION_PROMOTION_INVALID",
                "benchmark dependencies must be exact existing claims",
            )
        for ref in dependencies:
            self._active(ref)
        return self._append(
            "AnnotationBenchmarkClaim",
            AnnotationBenchmarkClaimSpec(
                qualificationBasis=self._policy.qualification_basis,
                promotionRef=promotions[0].ref,
                benchmarkRunDigest=run_digest,
                dependentClaimRefs=dependencies,
            ),
            subject,
            now,
            {
                "action": "BENCHMARK",
                "runDigest": run_digest,
                "dependencies": [_wire(x) for x in dependencies],
            },
        )

    def _dependencies(self, record: AdmittedAnnotation) -> tuple[ResourceRef, ...]:
        s = record.document.spec
        if isinstance(s, BlindDeliverySpec):
            return (s.packet_ref,)
        if isinstance(s, ReviewAttestationSpec):
            return s.packet_ref, s.delivery_ref
        if isinstance(s, AdjudicationRecordSpec):
            return (s.packet_ref, *s.review_refs)
        if isinstance(s, GoldPromotionSpec):
            return (s.packet_ref, s.case_ref, *s.source_refs, *s.review_refs, s.adjudication_ref)
        if isinstance(s, AnnotationBenchmarkClaimSpec):
            return (s.promotion_ref, *s.dependent_claim_refs)
        return ()

    def retract(self, subject: str, *, target_ref: ResourceRef, reason: str) -> AdmittedAnnotation:
        now = self._now()
        actor = self._actor(subject, "RETRACT", now)
        base = (
            self._packet.ref,
            self._scope.case_ref,
            self._scope.snapshot_ref,
            self._scope.change_ref,
        )
        known = (*base, *(r.ref for r in self._records if r.ref.kind != "AnnotationRetraction"))
        if target_ref not in known:
            _fail("ANNOTATION_PIN_MISMATCH", "retraction must name an exact known dependency")
        self._active(target_ref)
        affected = {target_ref}
        # Packet depends on the exact case and both source roots.
        if target_ref in base[1:]:
            affected.add(self._packet.ref)
        for record in self._records:
            if affected.intersection(self._dependencies(record)):
                affected.add(record.ref)
        ordered = tuple(ref for ref in known if ref in affected)
        return self._append(
            "AnnotationRetraction",
            AnnotationRetractionSpec(
                packetRef=self._packet.ref,
                targetRef=target_ref,
                predecessorHead=self.head,
                affectedRefs=ordered,
                governorKey=actor.person_key,
                reason=reason,
            ),
            subject,
            now,
            {"action": "RETRACT", "targetRef": _wire(target_ref), "reason": reason},
        )

    def qualify_benchmark(
        self,
        claim_ref: ResourceRef,
        *,
        required_basis: QualificationBasis = "GOVERNANCE_ATTESTED_HUMAN",
    ) -> AnnotationBenchmarkClaimSpec:
        matches = [r for r in self._by_kind("AnnotationBenchmarkClaim") if r.ref == claim_ref]
        if len(matches) != 1:
            _fail(
                "ANNOTATION_PROMOTION_INVALID", "claim is not present in the trusted current ledger"
            )
        self._active(claim_ref)
        spec = matches[0].document.spec
        assert isinstance(spec, AnnotationBenchmarkClaimSpec)
        if (
            required_basis not in ("CONTROLLED_LOCAL_FIXTURE", "GOVERNANCE_ATTESTED_HUMAN")
            or spec.qualification_basis != required_basis
        ):
            _fail(
                "ANNOTATION_GOLD_NOT_QUALIFIED",
                "the claim does not satisfy the requested qualification basis",
            )
        return spec

    def export_archive(self, subject: str) -> bytes:
        now = self._now()
        self._actor(subject, "PROMOTE", now)
        if len(self._by_kind("ReviewAttestation")) != 2:
            _fail(
                "ANNOTATION_BLINDING_VIOLATION",
                "full annotation proof is hidden until both rounds commit",
            )
        return _jcs(
            {
                "protocol": "oac.annotation-archive/v1",
                "authority": _decode_raw_object(self._authority.raw),
                "packet": _decode_raw_object(self._packet.raw),
                "case": _thaw_json(self._case.raw_map),
                "sources": [_thaw_json(x.raw_map) for x in self._sources.values()],
                "qualifications": [
                    _decode_raw_object(x.raw) for x in self._qualifications.values()
                ],
                "records": [_decode_raw_object(x.raw) for x in self._records],
                "actions": self._actions,
                "head": self.head,
            }
        )


def restore_annotation_archive(
    raw: bytes,
    *,
    archive_digest: str,
    authority_ref: ResourceRef,
    packet_ref: ResourceRef,
    clock: Callable[[], datetime],
) -> AnnotationLedger:
    """Replay the sole protocol from an independently pinned archive digest.

    The archive cannot supply its own trust anchor. All actions are recomputed;
    a freshly sealed forged promotion, missing denial/retraction or changed
    subject does not inherit a previously trusted artifact's authority.
    """
    from .annotation_models import (
        AdjudicationCommand,
        AnnotationArchive,
        BenchmarkCommand,
        DeliveryCommand,
        PromotionCommand,
        RetractionCommand,
        SubmissionCommand,
    )
    from .sealed import admit_sealed_resource

    if len(raw) > 16_777_216:
        _fail("ANNOTATION_ARCHIVE_INVALID", "archive exceeds its profile byte ceiling")
    decoded = _decode_annotation_object(raw, "ANNOTATION_ARCHIVE_INVALID")
    if _digest(decoded) != archive_digest:
        _fail("ANNOTATION_ARCHIVE_INVALID", "archive does not match caller-owned trust anchor")
    try:
        archive = AnnotationArchive.model_validate_json(raw)
    except ValidationError as exc:
        raise OACValidationError("ANNOTATION_ARCHIVE_INVALID", str(exc)) from exc
    if not archive.actions or len(archive.actions) != len(archive.records):
        _fail("ANNOTATION_ARCHIVE_INVALID", "archive actions and records disagree")
    current = [archive.actions[0].at]
    sources = []
    for source in archive.sources:
        kind = source.get("kind")
        if kind not in ("OrganizationSnapshot", "SemanticChangeSet"):
            _fail("ANNOTATION_ARCHIVE_INVALID", "archive source class is forbidden")
        assert isinstance(kind, str)
        sources.append(admit_sealed_resource(_jcs(source), kind))
    ledger = AnnotationLedger(
        authority=admit_annotation(_jcs(archive.authority), "AnnotationAuthority"),
        authority_ref=authority_ref,
        packet=admit_annotation(_jcs(archive.packet), "AnnotationPacket"),
        packet_ref=packet_ref,
        case=admit_sealed_resource(_jcs(archive.case), "OrgChangeCase"),
        sources=tuple(sources),
        qualifications=tuple(
            admit_annotation(_jcs(q), "ReviewerQualification") for q in archive.qualifications
        ),
        clock=lambda: current[0],
    )
    for action, expected in zip(archive.actions, archive.records, strict=True):
        current[0] = action.at
        command = action.command
        if isinstance(command, DeliveryCommand):
            record, _ = ledger.deliver(action.subject, command.round)
        elif isinstance(command, SubmissionCommand):
            record = ledger.submit(
                action.subject,
                delivery_ref=command.delivery_ref,
                fields=command.fields,
                model_assistance=command.model_assistance,
            )
        elif isinstance(command, AdjudicationCommand):
            record = ledger.adjudicate(
                action.subject,
                resolutions=command.resolutions,
                constraint_set=command.constraint_set,
            )
        elif isinstance(command, PromotionCommand):
            record = ledger.promote(action.subject)
        elif isinstance(command, BenchmarkCommand):
            record = ledger.benchmark_claim(
                action.subject, run_digest=command.run_digest, dependencies=command.dependencies
            )
        elif isinstance(command, RetractionCommand):
            record = ledger.retract(
                action.subject, target_ref=command.target_ref, reason=command.reason
            )
        else:
            _fail("ANNOTATION_ARCHIVE_INVALID", "unknown action")
        if record.raw != _jcs(expected):
            _fail("ANNOTATION_ARCHIVE_INVALID", "record differs from exact protocol replay")
    if ledger.head != archive.head:
        _fail("ANNOTATION_ARCHIVE_INVALID", "replayed head differs from the frozen record chain")
    ledger._clock = clock
    return ledger
