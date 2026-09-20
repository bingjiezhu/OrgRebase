from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import rfc8785

from oac.annotation import (
    FIELD_CATEGORIES,
    AnnotationLedger,
    _constraint,
    _core_ref,
    admit_annotation,
    restore_annotation_archive,
    seal_annotation,
)
from oac.annotation_models import (
    AdjudicationResolution,
    AnnotationAuthoritySpec,
    AnnotationBenchmarkClaimSpec,
    AnnotationPacketSpec,
    EvidenceCitation,
    GoldPromotionSpec,
    ReviewerIdentity,
    ReviewerQualificationSpec,
    ReviewField,
)
from oac.canonical import OACValidationError, parse_resource, seal_resource
from oac.models import AdmissionStatus, OrgChangeCase, ResourceMetadata
from oac.sealed import admit_sealed_resource

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def encoded(value):
    return rfc8785.dumps(value)


def digest(value):
    return "sha256:" + hashlib.sha256(encoded(value)).hexdigest()


def metadata(identifier, namespace, governance):
    return ResourceMetadata(
        id=identifier,
        namespace=namespace,
        revision=1,
        ownerRef="governance",
        governanceRef=governance,
        createdAt=NOW,
    )


@dataclass
class Example:
    ledger: AnnotationLedger
    authority: object
    packet: object
    fields: tuple
    constraints: object
    clock: list
    case: object
    sources: tuple
    qualifications: tuple

    def commit(self, first=None, second=None):
        d1, _ = self.ledger.deliver("reviewer-a", 1)
        a = self.ledger.submit("reviewer-a", delivery_ref=d1.ref, fields=first or self.fields)
        d2, _ = self.ledger.deliver("reviewer-b", 2)
        b = self.ledger.submit("reviewer-b", delivery_ref=d2.ref, fields=second or self.fields)
        return a, b

    def complete(self):
        self.commit()
        self.ledger.adjudicate("adjudicator", resolutions=(), constraint_set=self.constraints)
        promotion = self.ledger.promote("governor")
        claim = self.ledger.benchmark_claim("governor", run_digest=digest({"run": "public-local"}))
        return promotion, claim


@pytest.fixture
def example():
    return make_example("CONTROLLED_LOCAL_FIXTURE")


def make_example(basis):
    case_model = parse_resource(
        (ROOT / "profiles/supplier-change/cases/SC-008-alternative-qualified.json").read_bytes(),
        verify_digest=True,
    )
    assert isinstance(case_model, OrgChangeCase)
    # This fixture authors current lossless bytes from retained typed inputs;
    # the historical files and their declared roots are never rewritten.
    case = admit_sealed_resource(
        encoded(case_model.model_dump(mode="json", by_alias=True)), "OrgChangeCase"
    )
    sources = []
    for pin in (case_model.spec.snapshot_ref, case_model.spec.change_ref):
        for path in (ROOT / "profiles/supplier-change/inputs").glob("*.json"):
            resource = parse_resource(path.read_bytes(), verify_digest=True)
            if resource.metadata.id == pin.resource_id and resource.digest == pin.digest:
                sources.append(
                    admit_sealed_resource(
                        encoded(resource.model_dump(mode="json", by_alias=True)), resource.kind
                    )
                )
                break
    assert len(sources) == 2
    namespace = case_model.metadata.namespace
    governance = case_model.metadata.governance_ref
    qualifications = []
    identities = []
    for subject, key, actions, actor_type in (
        ("reviewer-a", "person-a", ("REVIEW", "ADJUDICATE"), "human"),
        ("reviewer-b", "person-b", ("REVIEW",), "human"),
        ("adjudicator", "person-c", ("ADJUDICATE",), "human"),
        ("governor", "person-d", ("PROMOTE", "RETRACT"), "human"),
        ("model", "model-x", ("REVIEW",), "model"),
        ("scripted", "scripted-x", ("REVIEW",), "scripted"),
    ):
        qualification = seal_annotation(
            "ReviewerQualification",
            metadata("qualification:" + key, namespace, governance),
            ReviewerQualificationSpec(
                qualificationBasis=basis,
                governanceRef=governance,
                personKey=key,
                caseRefs=(_core_ref(case),),
                actions=actions,
                validFrom=NOW - timedelta(days=1),
                validUntil=NOW + timedelta(days=1),
                evidenceStatement="Synthetic trusted-authority fixture; no real human qualification claimed.",
            ),
        )
        qualifications.append(qualification)
        identities.append(
            ReviewerIdentity(
                personKey=key,
                subjects=(subject, subject + "-alias"),
                actorType=actor_type,
                qualificationRefs=(qualification.ref,),
            )
        )
    authority = seal_annotation(
        "AnnotationAuthority",
        metadata("authority:annotation", namespace, governance),
        AnnotationAuthoritySpec(
            qualificationBasis=basis,
            governanceRef=governance,
            identities=tuple(identities),
            caseRefs=(_core_ref(case),),
            validFrom=NOW - timedelta(days=1),
            validUntil=NOW + timedelta(days=1),
        ),
    )
    citation = EvidenceCitation(resourceRef=_core_ref(sources[0]), pointer="/spec/nodes/0/nodeId")
    packet = seal_annotation(
        "AnnotationPacket",
        metadata("packet:SC-008", namespace, governance),
        AnnotationPacketSpec(
            authorityRef=authority.ref,
            caseRef=_core_ref(case),
            snapshotRef=_core_ref(sources[0]),
            changeRef=_core_ref(sources[1]),
            observationBoundary=case_model.spec.observation_boundary,
            allowedEvidence=(citation,),
        ),
    )
    constraints = case_model.spec.constraint_set
    assert constraints is not None
    fields = tuple(
        ReviewField(
            field=field,
            value=value,
            status="supported",
            evidence=(citation,),
            rationale="Controlled protocol example; semantic labels require real reviewers.",
        )
        for field, value in _constraint(constraints).items()
    )
    clock = [NOW]
    ledger = AnnotationLedger(
        authority=authority,
        authority_ref=authority.ref,
        packet=packet,
        packet_ref=packet.ref,
        case=case,
        sources=tuple(sources),
        qualifications=tuple(qualifications),
        clock=lambda: clock[0],
    )
    return Example(
        ledger,
        authority,
        packet,
        fields,
        constraints,
        clock,
        case,
        tuple(sources),
        tuple(qualifications),
    )


def test_two_round_actual_views_exclude_prior_labels_and_compiler_output(example):
    d1, first_view = example.ledger.deliver("reviewer-a", 1)
    a = example.ledger.submit("reviewer-a", delivery_ref=d1.ref, fields=example.fields)
    _, second_view = example.ledger.deliver("reviewer-b", 2)
    assert set(second_view) == {
        "protocol",
        "packetRef",
        "round",
        "questions",
        "observationBoundary",
        "evidence",
    }
    assert first_view | {"round": 2} == second_view
    serialized = encoded(second_view)
    assert b"constraintSet" not in serialized and b"annotations" not in serialized
    assert a.ref.digest.encode() not in serialized and b"OrganizationPlan" not in serialized
    assert {q["category"] for q in second_view["questions"]} == {
        "role",
        "obligation",
        "authority",
        "order",
        "evidence",
        "unknown",
    }


@pytest.mark.parametrize("subject", ["reviewer-a", "reviewer-a-alias"])
def test_same_person_cannot_supply_second_round_under_alias(example, subject):
    d, _ = example.ledger.deliver("reviewer-a", 1)
    example.ledger.submit("reviewer-a", delivery_ref=d.ref, fields=example.fields)
    with pytest.raises(OACValidationError, match="same person"):
        example.ledger.deliver(subject, 2)


@pytest.mark.parametrize("subject", ["model", "scripted", "unknown", "governor"])
def test_only_trusted_current_human_qualification_can_review(example, subject):
    before = example.ledger.head
    with pytest.raises(OACValidationError) as error:
        example.ledger.deliver(subject, 1)
    assert error.value.reason_code == "ANNOTATION_AUTHORITY_DENIED"
    assert example.ledger.head == before


@pytest.mark.parametrize("round_number", [0, 2, 3])
def test_round_order_is_enforced_by_committed_log(example, round_number):
    with pytest.raises(OACValidationError) as error:
        example.ledger.deliver("reviewer-b", round_number)
    assert error.value.reason_code == "ANNOTATION_BLINDING_VIOLATION"


def test_model_assisted_draft_cannot_count_as_human_round(example):
    d, _ = example.ledger.deliver("reviewer-a", 1)
    before = example.ledger.head
    with pytest.raises(OACValidationError) as error:
        example.ledger.submit(
            "reviewer-a", delivery_ref=d.ref, fields=example.fields, model_assistance=True
        )
    assert error.value.reason_code == "ANNOTATION_REVIEW_INVALID"
    assert before == example.ledger.head


def test_review_cannot_use_other_person_delivery_or_replay_it(example):
    d, _ = example.ledger.deliver("reviewer-a", 1)
    with pytest.raises(OACValidationError):
        example.ledger.submit("reviewer-b", delivery_ref=d.ref, fields=example.fields)
    example.ledger.submit("reviewer-a", delivery_ref=d.ref, fields=example.fields)
    with pytest.raises(OACValidationError):
        example.ledger.submit("reviewer-a", delivery_ref=d.ref, fields=example.fields)


@pytest.mark.parametrize("field", list(FIELD_CATEGORIES))
def test_every_mandatory_constraint_field_is_required(example, field):
    d, _ = example.ledger.deliver("reviewer-a", 1)
    with pytest.raises(OACValidationError) as error:
        example.ledger.submit(
            "reviewer-a",
            delivery_ref=d.ref,
            fields=tuple(f for f in example.fields if f.field != field),
        )
    assert error.value.reason_code == "ANNOTATION_FIELD_COVERAGE_INVALID"


@pytest.mark.parametrize(
    "mutation", ["duplicate", "empty-evidence", "wrong-digest", "wrong-pointer"]
)
def test_fields_need_exact_unique_admissible_evidence(example, mutation):
    fields = list(example.fields)
    if mutation == "duplicate":
        fields.append(fields[0])
    elif mutation == "empty-evidence":
        fields[0] = fields[0].model_copy(update={"evidence": ()})
    else:
        c = fields[0].evidence[0]
        c = (
            c.model_copy(
                update={
                    "resource_ref": c.resource_ref.model_copy(
                        update={"digest": "sha256:" + "0" * 64}
                    )
                }
            )
            if mutation == "wrong-digest"
            else c.model_copy(update={"pointer": "/spec/unknown"})
        )
        fields[0] = fields[0].model_copy(update={"evidence": (c,)})
    d, _ = example.ledger.deliver("reviewer-a", 1)
    with pytest.raises(OACValidationError):
        example.ledger.submit("reviewer-a", delivery_ref=d.ref, fields=tuple(fields))


@pytest.mark.parametrize("subject", ["reviewer-a", "reviewer-a-alias", "reviewer-b", "model"])
def test_adjudicator_is_distinct_qualified_person(example, subject):
    example.commit()
    with pytest.raises(OACValidationError):
        example.ledger.adjudicate(subject, resolutions=(), constraint_set=example.constraints)


def test_full_proof_is_not_exported_before_blind_commit(example):
    with pytest.raises(OACValidationError):
        example.ledger.export_archive("governor")
    d, _ = example.ledger.deliver("reviewer-a", 1)
    example.ledger.submit("reviewer-a", delivery_ref=d.ref, fields=example.fields)
    with pytest.raises(OACValidationError):
        example.ledger.review_material("adjudicator")
    with pytest.raises(OACValidationError):
        example.ledger.export_archive("governor")


def test_conflict_needs_exact_resolution_not_majority_or_changed_constraint(example):
    second = list(example.fields)
    i = next(i for i, f in enumerate(second) if f.field == "admissibleRoleRefs")
    second[i] = second[i].model_copy(update={"value": ["role:other"]})
    example.commit(second=tuple(second))
    with pytest.raises(OACValidationError):
        example.ledger.adjudicate("adjudicator", resolutions=(), constraint_set=example.constraints)
    resolution = AdjudicationResolution(
        field=second[i].field,
        value=example.fields[i].value,
        status="supported",
        evidence=example.fields[i].evidence,
        acceptedRationale="Accept the exact admitted source duty.",
        rejectedRationale="The other role lacks supporting evidence in the packet.",
    )
    modified = example.constraints.model_copy(update={"admissible_role_refs": ("role:wrong",)})
    with pytest.raises(OACValidationError):
        example.ledger.adjudicate("adjudicator", resolutions=(resolution,), constraint_set=modified)
    result = example.ledger.adjudicate(
        "adjudicator", resolutions=(resolution,), constraint_set=example.constraints
    )
    assert result.document.spec.conflict_fields == ("admissibleRoleRefs",)


def test_unknown_is_preserved_in_promotion_not_inferred_away(example):
    fields = tuple(
        f.model_copy(update={"status": "unknown", "evidence": ()})
        if f.field == "acceptableUnknownRefs"
        else f
        for f in example.fields
    )
    example.commit(first=fields, second=fields)
    example.ledger.adjudicate("adjudicator", resolutions=(), constraint_set=example.constraints)
    promotion = example.ledger.promote("governor")
    assert isinstance(promotion.document.spec, GoldPromotionSpec)
    assert promotion.document.spec.unresolved_fields == ("acceptableUnknownRefs",)


def test_exact_promotion_and_two_transitive_benchmark_claims(example):
    promotion, claim = example.complete()
    p = promotion.document.spec
    assert isinstance(p, GoldPromotionSpec)
    assert p.case_ref == _core_ref(example.case)
    assert p.source_refs == tuple(_core_ref(s) for s in example.sources)
    assert p.constraint_digest == digest(_constraint(example.constraints))
    child = example.ledger.benchmark_claim(
        "governor", run_digest=digest({"run": 2}), dependencies=(claim.ref,)
    )
    assert isinstance(
        example.ledger.qualify_benchmark(child.ref, required_basis="CONTROLLED_LOCAL_FIXTURE"),
        AnnotationBenchmarkClaimSpec,
    )


@pytest.mark.parametrize(
    "target", ["case", "source", "packet", "review", "adjudication", "promotion", "claim"]
)
def test_immutable_retraction_invalidates_dependency_and_benchmark_descendants(example, target):
    promotion, claim = example.complete()
    child = example.ledger.benchmark_claim(
        "governor", run_digest=digest({"run": 2}), dependencies=(claim.ref,)
    )
    archive_before = example.ledger.export_archive("governor")
    original_records = json.loads(archive_before)["records"]
    target_ref = {
        "case": _core_ref(example.case),
        "source": _core_ref(example.sources[0]),
        "packet": example.packet.ref,
        "review": promotion.document.spec.review_refs[0],
        "adjudication": promotion.document.spec.adjudication_ref,
        "promotion": promotion.ref,
        "claim": claim.ref,
    }[target]
    result = example.ledger.retract(
        "governor", target_ref=target_ref, reason="New evidence withdrew this exact source."
    )
    assert (
        claim.ref in result.document.spec.affected_refs
        and child.ref in result.document.spec.affected_refs
    )
    assert json.loads(example.ledger.export_archive("governor"))["records"][:-1] == original_records
    for ref in (claim.ref, child.ref):
        with pytest.raises(OACValidationError) as error:
            example.ledger.qualify_benchmark(ref)
        assert error.value.reason_code == "ANNOTATION_RETRACTED"
    with pytest.raises(OACValidationError):
        example.ledger.benchmark_claim(
            "governor", run_digest=digest({"run": 3}), dependencies=(child.ref,)
        )


def test_retraction_cannot_target_other_revision_or_be_issued_by_reviewer(example):
    p, _ = example.complete()
    with pytest.raises(OACValidationError):
        example.ledger.retract("reviewer-a", target_ref=p.ref, reason="No authority")
    with pytest.raises(OACValidationError):
        example.ledger.retract(
            "governor", target_ref=p.ref.model_copy(update={"revision": 2}), reason="Wrong revision"
        )


def test_archive_replay_requires_external_pin_and_recomputes_every_record(example):
    _, claim = example.complete()
    raw = example.ledger.export_archive("governor")
    root = digest(json.loads(raw))

    def restore(data, pin):
        return restore_annotation_archive(
            data,
            archive_digest=pin,
            authority_ref=example.authority.ref,
            packet_ref=example.packet.ref,
            clock=lambda: NOW,
        )

    restored = restore(raw, root)
    assert restored.head == example.ledger.head
    assert restored.qualify_benchmark(
        claim.ref, required_basis="CONTROLLED_LOCAL_FIXTURE"
    ) == example.ledger.qualify_benchmark(claim.ref, required_basis="CONTROLLED_LOCAL_FIXTURE")
    changed = json.loads(raw)
    changed["records"][-2]["spec"]["constraintDigest"] = "sha256:" + "0" * 64
    changed["records"][-2]["digest"] = digest(
        {k: v for k, v in changed["records"][-2].items() if k != "digest"}
    )
    with pytest.raises(OACValidationError):
        restore(encoded(changed), root)
    # Even a newly pinned test archive cannot skip deterministic transition checks.
    with pytest.raises(OACValidationError) as error:
        restore(encoded(changed), digest(changed))
    assert error.value.reason_code == "ANNOTATION_ARCHIVE_INVALID"


def test_restored_retraction_stays_effective(example):
    p, claim = example.complete()
    example.ledger.retract("governor", target_ref=p.ref, reason="Withdrawn")
    raw = example.ledger.export_archive("governor")
    restored = restore_annotation_archive(
        raw,
        archive_digest=digest(json.loads(raw)),
        authority_ref=example.authority.ref,
        packet_ref=example.packet.ref,
        clock=lambda: NOW,
    )
    with pytest.raises(OACValidationError):
        restored.qualify_benchmark(claim.ref, required_basis="CONTROLLED_LOCAL_FIXTURE")


@pytest.mark.parametrize(
    "edit", ["authority-pin", "packet-pin", "source-root", "missing-qualification"]
)
def test_trust_pins_and_qualification_bytes_cannot_come_from_candidate_claims(example, edit):
    kw = dict(
        authority=example.authority,
        authority_ref=example.authority.ref,
        packet=example.packet,
        packet_ref=example.packet.ref,
        case=example.case,
        sources=example.sources,
        qualifications=example.qualifications,
        clock=lambda: NOW,
    )
    if edit == "authority-pin":
        kw["authority_ref"] = example.authority.ref.model_copy(
            update={"digest": "sha256:" + "0" * 64}
        )
    elif edit == "packet-pin":
        kw["packet_ref"] = example.packet.ref.model_copy(update={"revision": 2})
    elif edit == "source-root":
        kw["sources"] = example.sources[:1]
    else:
        kw["qualifications"] = ()
    with pytest.raises(OACValidationError):
        ledger = AnnotationLedger(**kw)
        ledger.deliver("reviewer-a", 1)


def test_expiry_and_clock_backwards_deny_without_mutation(example):
    example.clock[0] = NOW + timedelta(days=1)
    old = example.ledger.head
    with pytest.raises(OACValidationError):
        example.ledger.deliver("reviewer-a", 1)
    assert example.ledger.head == old
    example.clock[0] = NOW
    example.ledger.deliver("reviewer-a", 1)
    example.clock[0] = NOW - timedelta(seconds=1)
    with pytest.raises(OACValidationError):
        example.ledger.deliver("reviewer-a", 1)


def test_returned_review_mutation_cannot_change_stored_committed_fields(example):
    a, _b = example.commit()
    for f in a.document.spec.fields:
        if isinstance(f.value, list):
            f.value.append("FORGED")
    example.ledger.adjudicate("adjudicator", resolutions=(), constraint_set=example.constraints)
    assert example.ledger.promote("governor").document.spec.constraint_digest == digest(
        _constraint(example.constraints)
    )


@pytest.mark.parametrize(
    "edit", ["unknown-kind", "wrong-version", "wrong-digest", "unknown-field", "duplicate-key"]
)
def test_annotation_raw_admission_is_exact_and_versioned(example, edit):
    raw = json.loads(example.packet.raw)
    if edit == "unknown-kind":
        raw["kind"] = "FutureAnnotation"
    elif edit == "wrong-version":
        raw["apiVersion"] = "oac.annotation-document/v2"
    elif edit == "wrong-digest":
        raw["digest"] = "sha256:" + "0" * 64
    elif edit == "unknown-field":
        raw["spec"]["blinded"] = True
        raw["digest"] = digest({k: v for k, v in raw.items() if k != "digest"})
    data = example.packet.raw[:-1] + b',"digest":"x"}' if edit == "duplicate-key" else encoded(raw)
    with pytest.raises(OACValidationError):
        admit_annotation(data, "AnnotationPacket")


def test_generated_profile_schemas_validate_real_complete_proof(example):
    from jsonschema import Draft202012Validator

    example.complete()
    raw = json.loads(example.ledger.export_archive("governor"))
    material = [raw["authority"], raw["packet"], *raw["qualifications"], *raw["records"]]
    for value in material:
        schema = json.loads(
            (ROOT / "schemas/annotation/v1" / f"{value['kind']}.schema.json").read_text()
        )
        Draft202012Validator(schema).validate(value)
    schema = json.loads((ROOT / "schemas/annotation/v1/AnnotationArchive.schema.json").read_text())
    Draft202012Validator(schema).validate(raw)


def test_public_benchmark_boundary_blocks_retracted_annotation(example):
    from oac.benchmark import qualify_annotation_benchmark

    _, claim = example.complete()
    assert (
        qualify_annotation_benchmark(
            example.ledger, claim.ref, required_basis="CONTROLLED_LOCAL_FIXTURE"
        ).promotion_ref
        == claim.document.spec.promotion_ref
    )
    example.ledger.retract("governor", target_ref=claim.ref, reason="Claim withdrawn after review.")
    with pytest.raises(OACValidationError) as error:
        qualify_annotation_benchmark(
            example.ledger, claim.ref, required_basis="CONTROLLED_LOCAL_FIXTURE"
        )
    assert error.value.reason_code == "ANNOTATION_RETRACTED"
    with pytest.raises(OACValidationError):
        example.ledger.benchmark_claim(
            "governor", run_digest=claim.document.spec.benchmark_run_digest
        )


def test_annotation_cannot_be_promoted_with_one_round_or_without_adjudication(example):
    with pytest.raises(OACValidationError):
        example.ledger.promote("governor")
    example.commit()
    with pytest.raises(OACValidationError):
        example.ledger.promote("governor")
    example.ledger.adjudicate("adjudicator", resolutions=(), constraint_set=example.constraints)
    with pytest.raises(OACValidationError):
        example.ledger.adjudicate("adjudicator", resolutions=(), constraint_set=example.constraints)
    example.ledger.promote("governor")
    with pytest.raises(OACValidationError):
        example.ledger.promote("governor")


def test_retracted_first_review_cannot_be_consumed_by_second_round(example):
    d, _ = example.ledger.deliver("reviewer-a", 1)
    a = example.ledger.submit("reviewer-a", delivery_ref=d.ref, fields=example.fields)
    example.ledger.retract("governor", target_ref=a.ref, reason="First round withdrawn.")
    with pytest.raises(OACValidationError) as error:
        example.ledger.deliver("reviewer-b", 2)
    assert error.value.reason_code == "ANNOTATION_RETRACTED"


@pytest.mark.parametrize(
    "mutation",
    [
        "extra-action",
        "missing-action",
        "wrong-head",
        "wrong-action-subject",
        "wrong-record",
        "source-class",
    ],
)
def test_resealed_archive_cannot_invent_or_remove_semantic_actions(example, mutation):
    example.complete()
    raw = json.loads(example.ledger.export_archive("governor"))
    if mutation == "extra-action":
        raw["actions"][0]["command"]["blinded"] = True
    elif mutation == "missing-action":
        raw["actions"].pop()
    elif mutation == "wrong-head":
        raw["head"] = "sha256:" + "0" * 64
    elif mutation == "wrong-action-subject":
        raw["actions"][2]["subject"] = "reviewer-a-alias"
    elif mutation == "wrong-record":
        raw["records"][-1]["metadata"]["revision"] = 99
    elif mutation == "source-class":
        raw["sources"][0]["kind"] = "OrganizationPlan"
    with pytest.raises(OACValidationError):
        restore_annotation_archive(
            encoded(raw),
            archive_digest=digest(raw),
            authority_ref=example.authority.ref,
            packet_ref=example.packet.ref,
            clock=lambda: NOW,
        )


@pytest.mark.parametrize(
    "pointer",
    [
        "/spec/nodes/0",
        "/metadata/id",
        "/spec/nodes/01/nodeId",
        "/spec/nodes/999/nodeId",
        "/spec/nodes/0/~2id",
        "/spec/nodes/0/nodeId/deeper",
    ],
)
def test_packet_cannot_expose_unbounded_or_invalid_evidence_pointer(example, pointer):
    spec = example.packet.document.spec
    citation = spec.allowed_evidence[0].model_copy(update={"pointer": pointer})
    packet = seal_annotation(
        "AnnotationPacket",
        example.packet.document.metadata,
        spec.model_copy(update={"allowed_evidence": (citation,)}),
    )
    with pytest.raises(OACValidationError):
        AnnotationLedger(
            authority=example.authority,
            authority_ref=example.authority.ref,
            packet=packet,
            packet_ref=packet.ref,
            case=example.case,
            sources=example.sources,
            qualifications=example.qualifications,
            clock=lambda: NOW,
        )


def test_packet_does_not_accept_unqualified_candidate_source_fact(example):
    sources = list(example.sources)
    snapshot = sources[0].resource
    candidate = snapshot.spec.nodes[0].model_copy(
        update={"admission_status": AdmissionStatus.CANDIDATE}
    )
    updated = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={"nodes": (candidate, *snapshot.spec.nodes[1:])}
                )
            }
        )
    )
    updated = admit_sealed_resource(
        encoded(updated.model_dump(mode="json", by_alias=True)), updated.kind
    )
    # The original packet pins exact source bytes: a changed source cannot reuse approval.
    sources[0] = updated
    with pytest.raises(OACValidationError) as error:
        AnnotationLedger(
            authority=example.authority,
            authority_ref=example.authority.ref,
            packet=example.packet,
            packet_ref=example.packet.ref,
            case=example.case,
            sources=tuple(sources),
            qualifications=example.qualifications,
            clock=lambda: NOW,
        )
    assert error.value.reason_code == "ANNOTATION_PIN_MISMATCH"


def test_published_synthetic_example_replays_and_withdrawal_blocks_benchmark():
    from oac.benchmark import qualify_annotation_benchmark
    from oac.models import ResourceRef

    path = ROOT / "profiles/human-annotation/supplier-v1"
    pins = json.loads((path / "trusted-inputs.json").read_text())
    assert pins["evidence_class"] == "SYNTHETIC_PROTOCOL_FIXTURE_NOT_HUMAN_GOLD"
    for name in ("approved", "retracted"):
        ledger = restore_annotation_archive(
            (path / f"{name}.json").read_bytes(),
            archive_digest=pins[f"{name}_archive_digest"],
            authority_ref=ResourceRef.model_validate(pins["authority_ref"]),
            packet_ref=ResourceRef.model_validate(pins["packet_ref"]),
            clock=lambda: NOW,
        )
        claim_ref = ResourceRef.model_validate(pins["benchmark_claim_ref"])
        if name == "approved":
            assert qualify_annotation_benchmark(
                ledger, claim_ref, required_basis="CONTROLLED_LOCAL_FIXTURE"
            )
        else:
            with pytest.raises(OACValidationError) as error:
                qualify_annotation_benchmark(
                    ledger, claim_ref, required_basis="CONTROLLED_LOCAL_FIXTURE"
                )
            assert error.value.reason_code == "ANNOTATION_RETRACTED"


@pytest.mark.parametrize("round_value", [True, False, 1.0, "1"])
def test_blind_delivery_rejects_non_integer_round_without_a_record(example, round_value):
    before = example.ledger.head
    with pytest.raises(OACValidationError) as error:
        example.ledger.deliver("reviewer-a", round_value)
    assert error.value.reason_code == "ANNOTATION_BLINDING_VIOLATION"
    assert example.ledger.head == before


def test_archive_command_cannot_coerce_boolean_into_review_round(example):
    example.complete()
    raw = json.loads(example.ledger.export_archive("governor"))
    raw["actions"][0]["command"]["round"] = True
    with pytest.raises(OACValidationError) as error:
        restore_annotation_archive(
            encoded(raw),
            archive_digest=digest(raw),
            authority_ref=example.authority.ref,
            packet_ref=example.packet.ref,
            clock=lambda: NOW,
        )
    assert error.value.reason_code == "ANNOTATION_ARCHIVE_INVALID"


def test_controlled_fixture_cannot_claim_governance_attested_human_gold(example):
    from oac.benchmark import qualify_annotation_benchmark

    promotion, claim = example.complete()
    assert promotion.document.spec.qualification_basis == "CONTROLLED_LOCAL_FIXTURE"
    assert claim.document.spec.qualification_basis == "CONTROLLED_LOCAL_FIXTURE"
    with pytest.raises(OACValidationError) as error:
        qualify_annotation_benchmark(example.ledger, claim.ref)
    assert error.value.reason_code == "ANNOTATION_GOLD_NOT_QUALIFIED"
    assert (
        qualify_annotation_benchmark(
            example.ledger, claim.ref, required_basis="CONTROLLED_LOCAL_FIXTURE"
        ).qualification_basis
        == "CONTROLLED_LOCAL_FIXTURE"
    )


def test_exact_governance_basis_is_propagated_without_implying_outcome_quality():
    from oac.benchmark import qualify_annotation_benchmark

    # This tests a trusted-host assertion, not a real person's participation.
    example = make_example("GOVERNANCE_ATTESTED_HUMAN")
    promotion, claim = example.complete()
    assert promotion.document.spec.qualification_basis == "GOVERNANCE_ATTESTED_HUMAN"
    assert promotion.document.spec.claim == "QUALIFIED_ANNOTATION_ONLY_NOT_ENTERPRISE_OUTCOME"
    assert qualify_annotation_benchmark(example.ledger, claim.ref).qualification_basis == (
        "GOVERNANCE_ATTESTED_HUMAN"
    )


def test_authority_cannot_upgrade_fixture_qualifications_by_changing_its_basis(example):
    authority = seal_annotation(
        "AnnotationAuthority",
        example.authority.document.metadata,
        example.authority.document.spec.model_copy(
            update={"qualification_basis": "GOVERNANCE_ATTESTED_HUMAN"}
        ),
    )
    packet = seal_annotation(
        "AnnotationPacket",
        example.packet.document.metadata,
        example.packet.document.spec.model_copy(update={"authority_ref": authority.ref}),
    )
    ledger = AnnotationLedger(
        authority=authority,
        authority_ref=authority.ref,
        packet=packet,
        packet_ref=packet.ref,
        case=example.case,
        sources=example.sources,
        qualifications=example.qualifications,
        clock=lambda: NOW,
    )
    with pytest.raises(OACValidationError) as error:
        ledger.deliver("reviewer-a", 1)
    assert error.value.reason_code == "ANNOTATION_AUTHORITY_DENIED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("validFrom", NOW.replace(tzinfo=None)),
        ("validUntil", NOW.replace(tzinfo=None)),
        ("validUntil", NOW - timedelta(days=2)),
    ],
)
def test_qualification_intervals_are_explicitly_aware_and_increasing(example, field, value):
    from pydantic import ValidationError

    for document in (example.authority, example.qualifications[0]):
        raw = document.document.spec.model_dump(mode="python", by_alias=True)
        raw[field] = value
        with pytest.raises(ValidationError):
            type(document.document.spec).model_validate(raw)
