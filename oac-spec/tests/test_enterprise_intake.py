from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import rfc8785
from jsonschema import Draft202012Validator

from oac.canonical import OACValidationError
from oac.models import AdmissionVerdict, ResourceRef
from oac.supplier import ProfileError

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "oac.examples.supplier"
GOVERNANCE = "policy:oac-shadow-zero-effect"
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
SCOPE = ["alternative:acieries-savoie", "production:aero", "production:energy"]
FIXTURE_ROOT = ROOT / "profiles/enterprise-intake/supplier-review-v0.1"
EXPECTED_OBLIGATIONS = {
    ("assess_dependency_impact", "production:energy", "role:operations-continuity"),
    ("assess_dependency_impact", "production:aero", "role:operations-continuity"),
    ("commercial-switch-review", "supplier:forges-martelliere", "role:procurement-owner"),
    ("continuity-option-selection", "production:aero", "role:operations-continuity"),
    ("qualification-evidence-check", "alternative:acieries-savoie", "role:quality-qualification"),
}
EXPECTED_AXES = {
    "sourceAuthority": "ADMITTED", "observability": "KNOWN", "applicabilityImpact": "TRUE",
    "planAssurance": None, "runtimeAdmission": "NOT_BOUND", "execution": "NOT_RUN",
    "outcomeAssurance": "NOT_EVALUATED", "evolutionGovernance": "OBSERVATION",
}


def _bytes(value: object) -> bytes:
    return rfc8785.dumps(value) + b"\n"


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _logical_manifest_digest(manifest: dict) -> str:
    identity = {key: value for key, value in manifest.items() if key != "packageId"}
    return _digest(rfc8785.dumps(identity))


def _published_rules_digest() -> str:
    rules = json.loads((ROOT / "schemas/enterprise-intake/v1/admission-rules.json").read_bytes())
    return _digest(rfc8785.dumps(rules))


def _seal(value: dict) -> dict:
    result = copy.deepcopy(value)
    result.pop("digest", None)
    result["digest"] = _digest(rfc8785.dumps(result))
    return result


def _metadata(resource_id: str, *, source_refs: list[str] | None = None) -> dict:
    return {"id": resource_id, "namespace": NAMESPACE, "revision": 1,
            "ownerRef": "role:evidence-discovery", "governanceRef": GOVERNANCE,
            "createdAt": "2026-09-09T00:00:00Z", "effectiveFrom": None,
            "effectiveTo": None, "sourceRefs": source_refs or []}


def _ref(document: dict) -> dict:
    metadata = document["metadata"]
    return {"apiVersion": "oac.dev/v0alpha1", "kind": document["kind"],
            "namespace": metadata["namespace"], "resourceId": metadata["id"],
            "revision": metadata["revision"], "digest": document["digest"]}


def _document(kind: str, resource_id: str, spec: dict) -> dict:
    return _seal({"apiVersion": "oac.enterprise-intake.document/v1", "kind": kind,
                  "metadata": _metadata(resource_id), "spec": spec})


@dataclass
class IntakeCase:
    manifest: dict
    resources: dict[str, bytes]
    profile: dict
    authority: dict

    def arguments(self) -> dict:
        return {"manifest_raw": _bytes(self.manifest), "resources": self.resources,
                "profile_raw": _bytes(self.profile), "authority_raw": _bytes(self.authority),
                "profile_ref": ResourceRef.model_validate(_ref(self.profile)),
                "authority_ref": ResourceRef.model_validate(_ref(self.authority)),
                "evaluated_at": NOW}

    def reauthorize(self) -> None:
        self.authority["spec"]["manifestDigest"] = _logical_manifest_digest(self.manifest)
        self.authority["spec"]["subjectRefs"] = [entry["resourceRef"] for entry in self.manifest["resources"]]
        self.authority["spec"]["profileRef"] = _ref(self.profile)
        self.authority = _seal(self.authority)

    def replace_resource(self, source: str, document: dict, *, reauthorize: bool = True) -> None:
        document = _seal(document)
        raw = _bytes(document)
        self.resources[source] = raw
        descriptor = next(item for item in self.manifest["resources"] if item["sourceRef"] == source)
        descriptor["rawDigest"] = _digest(raw)
        descriptor["resourceRef"] = _ref(document)
        if reauthorize:
            self.reauthorize()


def make_intake_case(*, edit_documents: Callable | None = None,
                     edit_profile: Callable | None = None,
                     edit_authority: Callable | None = None) -> IntakeCase:
    """Create genuine extension payloads and independently pinned local governance bytes."""
    input_root = ROOT / "profiles/supplier-change/inputs"
    snapshot = _seal(json.loads((input_root / "veracier-proc01-contextual.snapshot.json").read_bytes()))
    change = _seal(json.loads((input_root / "SC-008.change.json").read_bytes()))
    extension_kinds = {
        "subject": "OrganizationSubject", "outcome_criterion": "OutcomeCriterion",
        "evidence_obligation": "EvidenceObligation", "constraint": "IntakeConstraint",
        "trigger": "IntakeTrigger",
    }
    documents = {"source:snapshot": snapshot, "source:change": change}
    classes = {"source:snapshot": "organization_fact", "source:change": "semantic_change"}
    for index, subject in enumerate(SCOPE):
        name = f"source:subject-{index}"
        documents[name] = _document("OrganizationSubject", subject, {
            "purpose": "subject", "statement": f"The bounded subject is {subject}.",
            "scopeRefs": [subject], "effectCeiling": "zero_effect"})
        classes[name] = "subject"
    for purpose, name in (("outcome_criterion", "criterion:review-complete"),
                          ("evidence_obligation", "obligation:review-evidence"),
                          ("constraint", "constraint:no-external-effect"),
                          ("trigger", "trigger:accountable-review")):
        locator = "source:" + purpose
        documents[locator] = _document(extension_kinds[purpose], name, {
            "purpose": purpose, "statement": f"Explicit {purpose} for this controlled local review.",
            "scopeRefs": list(SCOPE), "effectCeiling": "zero_effect"})
        classes[locator] = purpose
    subject_refs = [_ref(documents[f"source:subject-{index}"]) for index in range(len(SCOPE))]
    trigger_refs = [_ref(change), _ref(documents["source:trigger"])]
    demand_metadata = _metadata("demand:SC-008", source_refs=[snapshot["metadata"]["id"],
        *(ref["resourceId"] for ref in subject_refs), *(ref["resourceId"] for ref in trigger_refs)])
    demand_metadata["ownerRef"] = "role:procurement-owner"
    documents["source:demand"] = _seal({"apiVersion": "oac.dev/v0alpha1", "kind": "OrganizationalDemand",
        "metadata": demand_metadata, "spec": {"snapshotRef": _ref(snapshot),
            "requesterPrincipalRef": "principal:procurement-agent",
            "accountableRoleRef": "role:procurement-owner", "objective": "objective:review-alternative-supplier",
            "subjectRefs": subject_refs, "triggerRefs": trigger_refs,
            "desiredOutcomeRefs": [_ref(documents["source:outcome_criterion"])],
            "evidenceObligationRefs": [_ref(documents["source:evidence_obligation"])],
            "constraintRefs": [_ref(documents["source:constraint"])], "priority": 1,
            "effectCeiling": "zero_effect"}})
    classes["source:demand"] = "bounded_demand"
    if edit_documents is not None:
        edit_documents(documents, classes)
    documents = {key: _seal(value) for key, value in documents.items()}
    profile = _document("IntakeProfile", "profile:supplier-intake@1", {
        "profileVersion": "oac.supplier.enterprise-intake/v0.1", "scopeRefs": list(SCOPE),
        "ruleSetDigest": _published_rules_digest(),
        "requiredClasses": ["organization_fact", "bounded_demand", "semantic_change",
                            "subject", "outcome_criterion", "evidence_obligation", "constraint", "trigger"],
        "extensions": [{"kind": kind, "declaredClass": purpose} for purpose, kind in extension_kinds.items()],
        "effectCeiling": "zero_effect"})
    if edit_profile is not None:
        edit_profile(profile)
        profile = _seal(profile)
    resources = {key: _bytes(value) for key, value in sorted(documents.items())}
    manifest = {"intakeProtocol": "oac.enterprise-intake/v0.1", "packageId": "local-test-intake-1",
        "namespace": NAMESPACE, "governanceRef": GOVERNANCE, "intakeProfileRef": profile["metadata"]["id"],
        "observedAt": "2026-09-09T00:00:00Z", "effectiveAt": "2026-09-09T00:00:00Z",
        "declaredScopeRefs": list(SCOPE), "resources": [
            {"mediaType": "application/json", "sourceRef": key, "rawDigest": _digest(raw),
             "resourceRef": _ref(documents[key]), "declaredClass": classes[key],
             "proposerRef": "principal:intake-producer"} for key, raw in resources.items()]}

    def actor(identifier: str, actor_type: str, actions: list[str]) -> dict:
        body = {"namespace": NAMESPACE, "principalId": identifier, "actorType": actor_type, "actions": actions}
        return {"ref": {"apiVersion": "oac.dev/v0alpha1", "kind": "Principal", "namespace": NAMESPACE,
                        "resourceId": identifier, "revision": 1, "digest": _digest(rfc8785.dumps(body))},
                "actorType": actor_type, "actions": actions}

    proposer = actor("principal:intake-producer", "AI", ["PROPOSE"])
    reviewer = actor("principal:intake-reviewer", "HUMAN", ["REVIEW"])
    authority_actor = actor("principal:intake-owner", "HUMAN", ["ADMIT"])
    authority = _document("IntakeAuthority", "authority:review-intake-1", {
        "profileRef": _ref(profile), "manifestDigest": _logical_manifest_digest(manifest),
        "subjectRefs": [entry["resourceRef"] for entry in manifest["resources"]],
        "actors": [proposer, reviewer, authority_actor], "decisionAuthorityRef": copy.deepcopy(authority_actor["ref"]),
        "reviewerRefs": [copy.deepcopy(reviewer["ref"])], "decision": "ADMITTED", "reasonCodes": [],
        "issuedAt": "2026-09-09T00:00:00Z", "expiresAt": "2026-09-10T00:00:00Z",
        "scopeRefs": list(SCOPE), "evidenceClass": "SCRIPTED_LOCAL"})
    if edit_authority is not None:
        edit_authority(authority)
        authority = _seal(authority)
    return IntakeCase(manifest, resources, profile, authority)


def _admit(case: IntakeCase, **overrides):
    from oac.enterprise_intake import admit_intake

    return admit_intake(**(case.arguments() | overrides))


def _reject(case: IntakeCase, code: str | None = None, **overrides) -> None:
    with pytest.raises(OACValidationError) as error:
        _admit(case, **overrides)
    if code is not None:
        assert error.value.reason_code == code


def test_real_supplier_intake_derives_known_obligations_and_bound_roots():
    case = make_intake_case()
    before = copy.deepcopy(case)
    result = _admit(case)
    assert case == before
    assert result.receipt.spec.verdict is AdmissionVerdict.ADMITTED
    assert result.manifest_digest == _logical_manifest_digest(case.manifest)
    assert result.transport_digest == _digest(_bytes(case.manifest))
    assert result.receipt.spec.intake_manifest_digest == result.manifest_digest
    assert result.receipt.spec.rule_set_digest == _published_rules_digest()
    assert result.receipt.spec.rule_set_digest != case.profile["digest"]
    assert set(result.receipt.spec.admitted_subject_refs) == {
        ResourceRef.model_validate(item["resourceRef"]) for item in case.manifest["resources"]}
    assert result.source_root is not None and result.demand_root is not None
    assert result.evidence_class == "SCRIPTED_LOCAL"
    derived = result.derived_contract
    assert derived is not None
    assert {(item.obligation_type, item.target_ref, item.required_role_ref)
            for item in derived.obligations} == EXPECTED_OBLIGATIONS
    assert derived.unresolved_refs == ()
    assert len(derived.applicability_evaluations) == 16
    assert len(derived.impact_paths) == 18
    assert _admit(case) == result


@pytest.mark.parametrize("source", ["source:snapshot", "source:change", "source:demand", "source:constraint"])
def test_transport_bytes_are_bound_even_when_semantic_json_is_unchanged(source):
    case = make_intake_case()
    case.resources[source] += b"\n"
    _reject(case, "ROOT_DIGEST_MISMATCH")


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_payload_inventory_is_exact(mutation):
    case = make_intake_case()
    if mutation == "missing":
        del case.resources["source:evidence_obligation"]
    else:
        case.resources["unlisted:extra"] = b"{}"
    _reject(case, "SOURCE_ADMISSION_SUBJECT_MISMATCH")


@pytest.mark.parametrize("target", ["profile", "authority"])
def test_caller_pins_cannot_be_replaced_by_resealed_candidate_documents(target):
    case = make_intake_case()
    document = copy.deepcopy(getattr(case, target))
    document["metadata"]["revision"] += 1
    _reject(case, "EVOLUTION_ROOT_MISMATCH", **{target + "_raw": _bytes(_seal(document))})


def test_changed_manifest_cannot_reuse_old_governance_decision():
    case = make_intake_case()
    document = json.loads(case.resources["source:constraint"])
    document["spec"]["statement"] = "A changed reviewed statement."
    case.replace_resource("source:constraint", document, reauthorize=False)
    _reject(case, "EVOLUTION_AUTHORITY_MISMATCH")


def test_changed_profile_cannot_reuse_old_governance_decision():
    case = make_intake_case()
    case.profile["metadata"]["revision"] += 1
    case.profile = _seal(case.profile)
    _reject(case, "EVOLUTION_AUTHORITY_MISMATCH")


@pytest.mark.parametrize("target", ["source:snapshot", "source:demand", "source:change", "source:constraint"])
def test_manifest_semantic_ref_must_match_its_actual_payload(target):
    case = make_intake_case()
    entry = next(item for item in case.manifest["resources"] if item["sourceRef"] == target)
    entry["resourceRef"]["digest"] = "sha256:" + "0" * 64
    case.reauthorize()
    _reject(case, "EVOLUTION_ROOT_MISMATCH")


@pytest.mark.parametrize("field,value", [("namespace", "another.enterprise"),
                                         ("governanceRef", "policy:foreign")])
def test_cross_enterprise_or_governance_resource_cannot_be_admitted(field, value):
    case = make_intake_case()
    document = json.loads(case.resources["source:constraint"])
    document["metadata"][field] = value
    case.replace_resource("source:constraint", document)
    _reject(case, "EVOLUTION_NAMESPACE_MISMATCH" if field == "namespace" else "EVOLUTION_AUTHORITY_MISMATCH")


@pytest.mark.parametrize("part", ["demand-id", "demand-snapshot", "change-revision"])
def test_demand_and_change_bind_complete_root_identity(part):
    case = make_intake_case()
    if part == "change-revision":
        change = json.loads(case.resources["source:change"])
        change["metadata"]["revision"] += 1
        case.replace_resource("source:change", change)
    else:
        demand = json.loads(case.resources["source:demand"])
        if part == "demand-id":
            demand["metadata"]["id"] = "demand:substitute"
        else:
            demand["spec"]["snapshotRef"]["digest"] = "sha256:" + "0" * 64
        case.replace_resource("source:demand", demand)
    _reject(case, "RESOURCE_COHERENCE_VIOLATION" if part == "demand-id" else "DEMAND_ROOT_MISMATCH")


@pytest.mark.parametrize("field,value", [("requesterPrincipalRef", "principal:absent"),
                                         ("accountableRoleRef", "role:legal-reviewer")])
def test_demand_accountability_must_resolve_and_be_eligible(field, value):
    case = make_intake_case()
    demand = json.loads(case.resources["source:demand"])
    demand["spec"][field] = value
    case.replace_resource("source:demand", demand)
    _reject(case, "DEMAND_AUTHORITY_UNRESOLVED")


@pytest.mark.parametrize("actor_type", ["AI", "AI_CONTROLLED"])
@pytest.mark.parametrize("actor_index", [1, 2])
def test_ai_review_or_admission_cannot_promote_another_ai_proposal(actor_type, actor_index):
    case = make_intake_case(edit_authority=lambda document: document["spec"]["actors"][actor_index].update(actorType=actor_type))
    _reject(case, "EVOLUTION_SELF_ADMISSION_FORBIDDEN")


def test_proposer_cannot_alias_itself_as_reviewer_using_another_revision():
    def mutate(document):
        proposal = copy.deepcopy(document["spec"]["actors"][0])
        proposal["ref"]["revision"] = 2
        proposal["ref"]["digest"] = "sha256:" + "1" * 64
        proposal["actorType"] = "HUMAN"
        proposal["actions"] = ["REVIEW"]
        document["spec"]["actors"][1] = proposal
        document["spec"]["reviewerRefs"] = [proposal["ref"]]

    _reject(make_intake_case(edit_authority=mutate), "EVOLUTION_SELF_ADMISSION_FORBIDDEN")


@pytest.mark.parametrize("mutation", ["scope", "subject", "actor", "action"])
def test_pinned_authority_still_must_cover_exact_package_and_roles(mutation):
    def mutate(document):
        spec = document["spec"]
        if mutation == "scope":
            spec["scopeRefs"] = SCOPE[:-1]
        elif mutation == "subject":
            spec["subjectRefs"] = spec["subjectRefs"][:-1]
        elif mutation == "actor":
            spec["decisionAuthorityRef"]["digest"] = "sha256:" + "2" * 64
        else:
            spec["actors"][2]["actions"] = ["REVIEW"]

    _reject(make_intake_case(edit_authority=mutate), "EVOLUTION_AUTHORITY_MISMATCH")


@pytest.mark.parametrize("instant", [datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 10, tzinfo=UTC)])
def test_authority_must_be_current_at_explicit_evaluation_time(instant):
    _reject(make_intake_case(), "EVOLUTION_AUTHORITY_MISMATCH", evaluated_at=instant)


def test_naive_evaluation_time_is_rejected():
    _reject(make_intake_case(), "CORE_SCHEMA_INVALID", evaluated_at=datetime(2026, 9, 9, 12))


@pytest.mark.parametrize("decision", ["REJECTED", "UNKNOWN"])
def test_nonadmitted_decision_never_produces_roots_or_derived_work(decision):
    case = make_intake_case(edit_authority=lambda document: document["spec"].update(
        decision=decision, reasonCodes=["SOURCE_ADMISSION_VERDICT_INVALID"]))
    result = _admit(case)
    assert result.receipt.spec.verdict.value == decision
    assert result.receipt.spec.admitted_subject_refs == ()
    assert result.source_root is None and result.demand_root is None and result.derived_contract is None


@pytest.mark.parametrize("mutation", ["unregistered-reason", "admitted-with-reason", "rejected-without-reason"])
def test_authority_decision_and_reason_policy_are_closed(mutation):
    def mutate(document):
        if mutation == "unregistered-reason":
            document["spec"].update(decision="UNKNOWN", reasonCodes=["MADE_UP_REASON"])
        elif mutation == "admitted-with-reason":
            document["spec"]["reasonCodes"] = ["SOURCE_ADMISSION_VERDICT_INVALID"]
        else:
            document["spec"]["decision"] = "REJECTED"

    _reject(make_intake_case(edit_authority=mutate), "SOURCE_ADMISSION_VERDICT_INVALID")


@pytest.mark.parametrize("source", ["source:subject-0", "source:outcome_criterion", "source:evidence_obligation", "source:constraint", "source:trigger"])
def test_unmapped_actual_extension_is_unknown_and_cannot_create_source_authority(source):
    case = make_intake_case()
    entry = next(item for item in case.manifest["resources"] if item["sourceRef"] == source)
    entry["declaredClass"] = "unmapped_extension"
    case.reauthorize()
    result = _admit(case)
    assert result.receipt.spec.verdict is AdmissionVerdict.UNKNOWN
    assert "UNSUPPORTED_SEMANTICS" in result.receipt.spec.reason_codes
    assert result.receipt.spec.unresolved_refs
    assert not result.receipt.spec.admitted_subject_refs
    assert result.source_root is None and result.demand_root is None and result.derived_contract is None


@pytest.mark.parametrize("source", ["source:demand", "source:constraint"])
def test_effect_ceiling_cannot_be_increased_by_a_pinned_package(source):
    case = make_intake_case()
    document = json.loads(case.resources[source])
    document["spec"]["effectCeiling"] = "production_write"
    case.replace_resource(source, document)
    _reject(case, "CORE_SCHEMA_INVALID")


def test_profile_cannot_expand_effect_ceiling():
    case = make_intake_case(edit_profile=lambda document: document["spec"].update(effectCeiling="production_write"))
    _reject(case, "CORE_SCHEMA_INVALID")


@pytest.mark.parametrize("source", ["source:demand", "source:constraint"])
def test_omitted_optional_defaults_preserve_raw_identity_through_intake(source):
    case = make_intake_case()
    document = json.loads(case.resources[source])
    del document["metadata"]["effectiveFrom"]
    case.replace_resource(source, document)
    if source == "source:constraint":
        demand = json.loads(case.resources["source:demand"])
        demand["spec"]["constraintRefs"] = [_ref(_seal(document))]
        case.replace_resource("source:demand", demand)
    result = _admit(case)
    assert result.receipt.spec.verdict is AdmissionVerdict.ADMITTED
    assert result.derived_contract is not None
    assert _ref(_seal(document))["digest"] in {ref.digest for ref in result.receipt.spec.admitted_subject_refs}


@pytest.mark.parametrize("payload", [b'{"intakeProtocol":"a","intakeProtocol":"b"}', b'{"x":NaN}', b'\xff'])
def test_manifest_rejects_duplicate_keys_nonfinite_values_and_invalid_utf8(payload):
    from oac.enterprise_intake import parse_intake_manifest

    with pytest.raises(OACValidationError):
        parse_intake_manifest(payload)


@pytest.mark.parametrize("mutation", ["duplicate-locator", "duplicate-identity", "extra-field"])
def test_manifest_is_closed_and_has_unique_inventory(mutation):
    from oac.enterprise_intake import parse_intake_manifest

    case = make_intake_case()
    if mutation == "extra-field":
        case.manifest["admissionStatus"] = "ADMITTED"
    else:
        duplicate = copy.deepcopy(case.manifest["resources"][0])
        if mutation == "duplicate-identity":
            duplicate["sourceRef"] = "source:another-locator"
            duplicate["resourceRef"]["revision"] += 1
        case.manifest["resources"].append(duplicate)
    with pytest.raises(OACValidationError):
        parse_intake_manifest(_bytes(case.manifest))


@pytest.mark.parametrize("missing_class", ["organization_fact", "bounded_demand", "semantic_change", "constraint"])
def test_required_class_absence_is_unknown_even_when_the_remaining_inventory_is_approved(missing_class):
    case = make_intake_case()
    removed = {item["sourceRef"] for item in case.manifest["resources"] if item["declaredClass"] == missing_class}
    case.manifest["resources"] = [item for item in case.manifest["resources"] if item["sourceRef"] not in removed]
    for source in removed:
        del case.resources[source]
    case.reauthorize()
    result = _admit(case)
    assert result.receipt.spec.verdict is AdmissionVerdict.UNKNOWN
    assert "UNSUPPORTED_SEMANTICS" in result.receipt.spec.reason_codes
    assert not result.receipt.spec.admitted_subject_refs
    assert result.source_root is None and result.demand_root is None and result.derived_contract is None


def test_demand_cannot_substitute_an_extension_with_the_same_id_and_another_digest():
    case = make_intake_case()
    demand = json.loads(case.resources["source:demand"])
    demand["spec"]["constraintRefs"][0]["digest"] = "sha256:" + "9" * 64
    case.replace_resource("source:demand", demand)
    result = _admit(case)
    assert result.receipt.spec.verdict is AdmissionVerdict.UNKNOWN
    assert ResourceRef.model_validate(demand["spec"]["constraintRefs"][0]) in result.receipt.spec.unresolved_refs
    assert result.source_root is None and result.derived_contract is None


def test_candidate_change_cannot_establish_mandatory_work_from_an_admitted_transport_label():
    case = make_intake_case()
    change = json.loads(case.resources["source:change"])
    change["spec"]["admissionStatus"] = "candidate"
    case.replace_resource("source:change", change)
    demand = json.loads(case.resources["source:demand"])
    demand["spec"]["triggerRefs"][0] = _ref(_seal(change))
    case.replace_resource("source:demand", demand)
    with pytest.raises(ProfileError) as error:
        _admit(case)
    assert error.value.reason_code == "CHANGE_NOT_ADMITTED"


@pytest.mark.parametrize("source", ["source:snapshot", "source:change", "source:demand"])
def test_core_owner_must_be_an_admitted_role(source):
    case = make_intake_case()
    document = json.loads(case.resources[source])
    document["metadata"]["ownerRef"] = "role:unresolved-owner"
    case.replace_resource(source, document)
    _reject(case, "CORE_SCHEMA_INVALID" if source == "source:snapshot" else "DEMAND_AUTHORITY_UNRESOLVED")


@pytest.mark.parametrize("document_name", ["profile", "authority", "source:constraint"])
def test_transport_document_owner_must_be_an_admitted_role(document_name):
    case = make_intake_case()
    if document_name.startswith("source:"):
        document = json.loads(case.resources[document_name])
        document["metadata"]["ownerRef"] = "role:unresolved-owner"
        case.replace_resource(document_name, document)
    else:
        document = getattr(case, document_name)
        document["metadata"]["ownerRef"] = "role:unresolved-owner"
        setattr(case, document_name, _seal(document))
        case.reauthorize()
    _reject(case, "DEMAND_AUTHORITY_UNRESOLVED")


@pytest.mark.parametrize("statement", ["", "   ", "\t\n"])
def test_extension_statement_cannot_be_empty_or_whitespace(statement):
    case = make_intake_case()
    document = json.loads(case.resources["source:constraint"])
    document["spec"]["statement"] = statement
    case.replace_resource("source:constraint", document)
    _reject(case)


@pytest.mark.parametrize("depth", [64, 65])
def test_unmapped_extension_json_depth_exact_boundary(depth):
    case = make_intake_case()
    nested = None
    # The envelope root, spec object, and future value consume three levels.
    for _ in range(depth - 3):
        nested = {"next": nested}
    document = json.loads(case.resources["source:constraint"])
    document["spec"] = {"future": nested}
    entry = next(item for item in case.manifest["resources"] if item["sourceRef"] == "source:constraint")
    entry["declaredClass"] = "unmapped_extension"
    case.replace_resource("source:constraint", document)
    if depth == 64:
        result = _admit(case)
        assert result.receipt.spec.verdict is AdmissionVerdict.UNKNOWN
        assert result.source_root is None
    else:
        _reject(case, "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED")


def test_material_byte_ceiling_is_a_resource_failure_even_with_a_correct_transport_digest():
    case = make_intake_case()
    raw = case.resources["source:constraint"] + b" " * 1_048_576
    case.resources["source:constraint"] = raw
    entry = next(item for item in case.manifest["resources"] if item["sourceRef"] == "source:constraint")
    entry["rawDigest"] = _digest(raw)
    case.reauthorize()
    _reject(case, "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED")


def test_json_parser_recursion_limit_is_reported_as_a_resource_failure():
    from oac.enterprise_intake import parse_intake_manifest

    raw = b'{"nested":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    with pytest.raises(OACValidationError) as error:
        parse_intake_manifest(raw)
    assert error.value.reason_code == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"


def test_aggregate_material_ceiling_applies_before_unknown_mapping_fallback():
    case = make_intake_case()
    for index in range(17):
        document = _document("FutureMaterial", f"future:material-{index}", {"padding": "x" * 990_000})
        raw = _bytes(document)
        assert len(raw) < 1_048_576
        source = f"source:future-{index}"
        case.resources[source] = raw
        case.manifest["resources"].append({"mediaType": "application/json", "sourceRef": source,
            "rawDigest": _digest(raw), "resourceRef": _ref(document), "declaredClass": "future_material",
            "proposerRef": "principal:intake-producer"})
    assert sum(map(len, case.resources.values())) > 16 * 1_048_576
    case.reauthorize()
    _reject(case, "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED")


@pytest.mark.parametrize("document_name", ["profile", "authority", "source:constraint"])
def test_transport_owner_cannot_use_a_present_but_candidate_role(document_name):
    case = make_intake_case()
    snapshot = json.loads(case.resources["source:snapshot"])
    role = copy.deepcopy(snapshot["spec"]["roleDefinitions"][0])
    role["roleId"] = "role:candidate-owner"
    role["admissionStatus"] = "candidate"
    snapshot["spec"]["roleDefinitions"].append(role)
    case.replace_resource("source:snapshot", snapshot)
    demand = json.loads(case.resources["source:demand"])
    demand["spec"]["snapshotRef"] = _ref(_seal(snapshot))
    case.replace_resource("source:demand", demand)
    if document_name.startswith("source:"):
        document = json.loads(case.resources[document_name])
        document["metadata"]["ownerRef"] = "role:candidate-owner"
        case.replace_resource(document_name, document)
    else:
        document = getattr(case, document_name)
        document["metadata"]["ownerRef"] = "role:candidate-owner"
        setattr(case, document_name, _seal(document))
        case.reauthorize()
    _reject(case, "DEMAND_AUTHORITY_UNRESOLVED")


def test_package_routing_identity_does_not_change_governed_resource_identity():
    case = make_intake_case()
    original = _admit(case)
    original_pin = copy.deepcopy(case.arguments()["authority_ref"])
    case.manifest["packageId"] = "another-transport-route"
    changed = _admit(case)
    assert case.arguments()["authority_ref"] == original_pin
    assert changed.manifest_digest == original.manifest_digest
    assert changed.transport_digest != original.transport_digest
    assert changed.transport_digest == _digest(_bytes(case.manifest))
    assert changed.receipt == original.receipt
    assert changed.source_root == original.source_root
    assert changed.demand_root == original.demand_root
    assert changed.derived_contract == original.derived_contract


@pytest.mark.parametrize("mutation", ["order", "owner", "scope", "observedAt", "effectiveAt", "sourceRef"])
def test_semantic_manifest_changes_cannot_reuse_the_original_authority_pin(mutation):
    case = make_intake_case()
    if mutation == "order":
        case.manifest["resources"].reverse()
    elif mutation == "owner":
        document = json.loads(case.resources["source:constraint"])
        document["metadata"]["ownerRef"] = "role:procurement-owner"
        case.replace_resource("source:constraint", document, reauthorize=False)
    elif mutation == "scope":
        case.manifest["declaredScopeRefs"].reverse()
    elif mutation == "sourceRef":
        descriptor = case.manifest["resources"][0]
        case.resources["source:another"] = case.resources.pop(descriptor["sourceRef"])
        descriptor["sourceRef"] = "source:another"
    else:
        case.manifest[mutation] = "2026-09-09T01:00:00Z"
    _reject(case, "EVOLUTION_AUTHORITY_MISMATCH")


def test_actor_actions_cannot_repeat_within_the_governed_roster():
    case = make_intake_case(edit_authority=lambda document: document["spec"]["actors"][2].update(
        actions=["ADMIT", "ADMIT"]))
    _reject(case, "EVOLUTION_DUPLICATE_REF")


def _schema_document(case: IntakeCase, name: str) -> dict:
    if name == "IntakeManifest":
        return copy.deepcopy(case.manifest)
    if name == "IntakeProfile":
        return copy.deepcopy(case.profile)
    if name == "IntakeAuthority":
        return copy.deepcopy(case.authority)
    return json.loads(case.resources["source:constraint"])


def _schema(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas/enterprise-intake/v1" / f"{name}.schema.json").read_bytes())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@pytest.mark.parametrize("name", ["IntakeManifest", "IntakeProfile", "IntakeAuthority", "IntakeExtension"])
def test_published_intake_schemas_validate_real_fixture_documents(name):
    _schema(name).validate(_schema_document(make_intake_case(), name))


@pytest.mark.parametrize("name", ["IntakeManifest", "IntakeProfile", "IntakeAuthority", "IntakeExtension"])
def test_published_intake_schemas_reject_unknown_envelope_fields(name):
    document = _schema_document(make_intake_case(), name)
    document["assumeAdmitted"] = True
    errors = list(_schema(name).iter_errors(document))
    assert any(error.validator == "additionalProperties" for error in errors)


@pytest.mark.parametrize("name", ["IntakeProfile", "IntakeAuthority", "IntakeExtension"])
def test_published_intake_schemas_reject_unknown_spec_fields(name):
    document = _schema_document(make_intake_case(), name)
    document["spec"]["assumeAdmitted"] = True
    errors = list(_schema(name).iter_errors(document))
    assert any(error.validator == "additionalProperties" for error in errors)


@pytest.mark.parametrize("statement", ["", "   ", "\t\n"])
def test_published_extension_schema_rejects_blank_statements(statement):
    document = _schema_document(make_intake_case(), "IntakeExtension")
    document["spec"]["statement"] = statement
    errors = list(_schema("IntakeExtension").iter_errors(document))
    assert any(error.validator in {"minLength", "pattern"} for error in errors)


def test_public_admission_rules_match_the_published_coordinate_and_independent_digest():
    from oac.enterprise_intake import intake_admission_rules, intake_rule_set_digest

    published = json.loads((ROOT / "schemas/enterprise-intake/v1/admission-rules.json").read_bytes())
    assert published["registryVersion"] == "oac.enterprise-intake.admission-rules/v1"
    assert published["identity"]["logicalManifestProjection"] == "omit-only-packageId"
    assert published["resourceLimits"] == {"maxMaterialBytes": 1_048_576,
        "maxTotalBytes": 16_777_216, "maxJsonDepth": 64, "maxResources": 128}
    assert intake_admission_rules() == published
    assert intake_rule_set_digest() == _digest(rfc8785.dumps(published))
    candidate = intake_admission_rules()
    candidate["rules"].clear()
    assert intake_admission_rules() == published


@pytest.mark.parametrize("mutation", ["missing", "forged", "older-rules"])
def test_profile_must_explicitly_select_the_current_admission_rules(mutation):
    def mutate(document):
        if mutation == "missing":
            del document["spec"]["ruleSetDigest"]
        elif mutation == "forged":
            document["spec"]["ruleSetDigest"] = "sha256:" + "9" * 64
        else:
            rules = json.loads((ROOT / "schemas/enterprise-intake/v1/admission-rules.json").read_bytes())
            rules["registryVersion"] = "oac.enterprise-intake.admission-rules/v0"
            document["spec"]["ruleSetDigest"] = _digest(rfc8785.dumps(rules))

    case = make_intake_case(edit_profile=mutate)
    _reject(case, "CORE_SCHEMA_INVALID" if mutation == "missing" else "EVOLUTION_AUTHORITY_MISMATCH")
    if mutation == "missing":
        assert any(error.validator == "required" for error in _schema("IntakeProfile").iter_errors(case.profile))


def test_configuration_changes_preserve_rule_identity_and_cannot_replace_it_with_profile_identity():
    original = make_intake_case()
    changed = make_intake_case(edit_profile=lambda document: document["spec"]["extensions"].reverse())
    original_result, changed_result = _admit(original), _admit(changed)
    assert changed.profile["digest"] != original.profile["digest"]
    assert changed_result.receipt.spec.intake_profile_ref != original_result.receipt.spec.intake_profile_ref
    assert changed_result.receipt.spec.rule_set_digest == original_result.receipt.spec.rule_set_digest
    assert changed_result.receipt.spec.rule_set_digest == _published_rules_digest()
    changed.profile["spec"]["ruleSetDigest"] = changed.profile["digest"]
    changed.profile = _seal(changed.profile)
    changed.reauthorize()
    _reject(changed, "EVOLUTION_AUTHORITY_MISMATCH")


def _project(case: IntakeCase, receipt):
    from oac.intake_state import project_intake_state
    from oac.sealed import admit_sealed_resource

    return project_intake_state(
        admit_sealed_resource(case.resources["source:snapshot"], "OrganizationSnapshot"),
        admit_sealed_resource(case.resources["source:change"], "SemanticChangeSet"),
        source_receipt=receipt,
        demand=admit_sealed_resource(case.resources["source:demand"], "OrganizationalDemand"),
    )


def test_actual_admission_projects_all_eight_axes_and_the_same_roots():
    case = make_intake_case()
    admission = _admit(case)
    state = _project(case, admission.receipt)
    view = state.as_json()
    assert {name: axis["value"] for name, axis in view["axes"].items()} == EXPECTED_AXES
    assert view["rootRefs"] == {"source": admission.source_root, "demand": admission.demand_root,
        "plan": None, "execution": None, "outcome": None, "evolution": None}
    assert state.source_root is not None and state.demand_root is not None


def _caller_context(case: IntakeCase) -> dict:
    return {"profileRef": _ref(case.profile), "authorityRef": _ref(case.authority),
        "evaluatedAt": "2026-09-09T12:00:00Z", "resourceFiles": {
            source: "resources/" + source.removeprefix("source:") + ".json"
            for source in case.resources}}


def _expected_fixture() -> dict:
    return {"obligations": [{"obligationType": item[0], "targetRef": item[1], "requiredRoleRef": item[2]}
            for item in sorted(EXPECTED_OBLIGATIONS)],
        "applicabilityEvaluationCount": 16, "impactPathCount": 18, "axes": EXPECTED_AXES}


def test_published_supplier_review_files_match_the_explicit_fixture():
    case = make_intake_case()
    expected_files = {
        "manifest.json": _bytes(case.manifest), "profile.json": _bytes(case.profile),
        "authority.json": _bytes(case.authority), "caller-context.json": _bytes(_caller_context(case)),
        "expected.json": _bytes(_expected_fixture()),
    }
    expected_files.update({_caller_context(case)["resourceFiles"][source]: raw
                           for source, raw in case.resources.items()})
    observed_files = {path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
                      for path in FIXTURE_ROOT.rglob("*") if path.is_file()}
    assert observed_files == expected_files


def test_published_supplier_review_files_execute_admission_and_state_projection():
    from oac.enterprise_intake import admit_intake

    context = json.loads((FIXTURE_ROOT / "caller-context.json").read_bytes())
    expected = json.loads((FIXTURE_ROOT / "expected.json").read_bytes())
    manifest_raw = (FIXTURE_ROOT / "manifest.json").read_bytes()
    profile_raw = (FIXTURE_ROOT / "profile.json").read_bytes()
    authority_raw = (FIXTURE_ROOT / "authority.json").read_bytes()
    resources = {source: (FIXTURE_ROOT / relative).read_bytes()
                 for source, relative in context["resourceFiles"].items()}
    admission = admit_intake(manifest_raw, resources, profile_raw, authority_raw,
        profile_ref=ResourceRef.model_validate(context["profileRef"]),
        authority_ref=ResourceRef.model_validate(context["authorityRef"]),
        evaluated_at=datetime.fromisoformat(context["evaluatedAt"]))
    assert admission.receipt.spec.verdict is AdmissionVerdict.ADMITTED
    assert admission.derived_contract is not None
    actual_obligations = {(item.obligation_type, item.target_ref, item.required_role_ref)
                         for item in admission.derived_contract.obligations}
    assert actual_obligations == {(item["obligationType"], item["targetRef"], item["requiredRoleRef"])
                                 for item in expected["obligations"]}
    assert len(admission.derived_contract.applicability_evaluations) == expected["applicabilityEvaluationCount"]
    assert len(admission.derived_contract.impact_paths) == expected["impactPathCount"]
    case = IntakeCase(json.loads(manifest_raw), resources, json.loads(profile_raw), json.loads(authority_raw))
    state = _project(case, admission.receipt)
    assert {name: axis["value"] for name, axis in state.as_json()["axes"].items()} == expected["axes"]
    assert state.source_root == admission.source_root
    assert state.demand_root == admission.demand_root
