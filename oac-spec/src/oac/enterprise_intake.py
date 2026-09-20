"""Pure, caller-pinned enterprise intake for the bounded Supplier profile.

Transport records are not OAC Kinds. The module performs no I/O, identity login,
source mutation or runtime execution. Its authority record must be pinned by a
caller outside the candidate package; this is not proof of enterprise identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NoReturn

import rfc8785
from pydantic import Field, ValidationError

from .canonical import OACValidationError, _raw_mapping, resource_ref, seal_resource
from .evolution import (
    project_demand_root,
    project_source_root,
    verify_organizational_demand_from_admitted,
    verify_source_admission_receipt,
)
from .json_types import JsonValue
from .models import (
    AdmissionStatus,
    AdmissionVerdict,
    Digest,
    EffectCeiling,
    Identifier,
    OrganizationalDemand,
    OrganizationSnapshot,
    ResourceMetadata,
    ResourceRef,
    SemanticChangeSet,
    SourceAdmissionReceipt,
    SourceAdmissionReceiptSpec,
    StrictModel,
)
from .registry import REASON_CODE_REGISTRY
from .sealed import (
    AdmittedSealedResource,
    _exact_json_equal,
    _json_depth,
    admit_sealed_resource,
    validate_sealed_admission,
)
from .supplier import DerivedContract, derive_supplier_contract_from_admitted

_MAX_BYTES = 1_048_576
_MAX_TOTAL_BYTES = 16_777_216
_MAX_JSON_DEPTH = 64
_CORE_CLASSES = {
    "organization_fact": "OrganizationSnapshot",
    "bounded_demand": "OrganizationalDemand",
    "semantic_change": "SemanticChangeSet",
}


class IntakeResource(StrictModel):
    media_type: Literal["application/json"] = Field(alias="mediaType")
    source_ref: Identifier = Field(alias="sourceRef")
    raw_digest: Digest = Field(alias="rawDigest")
    resource_ref: ResourceRef = Field(alias="resourceRef")
    declared_class: Identifier = Field(alias="declaredClass")
    proposer_ref: Identifier = Field(alias="proposerRef")


class IntakeManifest(StrictModel):
    intake_protocol: Literal["oac.enterprise-intake/v0.1"] = Field(alias="intakeProtocol")
    package_id: Identifier = Field(alias="packageId")
    namespace: Identifier
    governance_ref: Identifier = Field(alias="governanceRef")
    intake_profile_ref: Identifier = Field(alias="intakeProfileRef")
    observed_at: datetime = Field(alias="observedAt")
    effective_at: datetime = Field(alias="effectiveAt")
    resources: tuple[IntakeResource, ...] = Field(min_length=1, max_length=128)
    declared_scope_refs: tuple[Identifier, ...] = Field(alias="declaredScopeRefs", min_length=1)


class ExtensionRule(StrictModel):
    kind: Identifier
    declared_class: Literal[
        "subject", "outcome_criterion", "evidence_obligation", "constraint", "trigger"
    ] = Field(alias="declaredClass")


class IntakeProfileSpec(StrictModel):
    profile_version: Literal["oac.supplier.enterprise-intake/v0.1"] = Field(alias="profileVersion")
    rule_set_digest: Digest = Field(alias="ruleSetDigest")
    scope_refs: tuple[Identifier, ...] = Field(alias="scopeRefs", min_length=1)
    required_classes: tuple[Identifier, ...] = Field(alias="requiredClasses", min_length=3)
    extensions: tuple[ExtensionRule, ...]
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")


class IntakeActor(StrictModel):
    ref: ResourceRef
    actor_type: Literal["HUMAN", "AI", "AI_CONTROLLED"] = Field(alias="actorType")
    actions: tuple[Literal["PROPOSE", "REVIEW", "ADMIT"], ...] = Field(min_length=1)


class IntakeAuthoritySpec(StrictModel):
    profile_ref: ResourceRef = Field(alias="profileRef")
    manifest_digest: Digest = Field(alias="manifestDigest")
    subject_refs: tuple[ResourceRef, ...] = Field(alias="subjectRefs", min_length=1)
    actors: tuple[IntakeActor, ...] = Field(min_length=1)
    decision_authority_ref: ResourceRef = Field(alias="decisionAuthorityRef")
    reviewer_refs: tuple[ResourceRef, ...] = Field(alias="reviewerRefs", min_length=1)
    decision: AdmissionVerdict
    reason_codes: tuple[Identifier, ...] = Field(default=(), alias="reasonCodes")
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    scope_refs: tuple[Identifier, ...] = Field(alias="scopeRefs", min_length=1)
    evidence_class: Literal["SCRIPTED_LOCAL", "EXTERNAL_ASSERTION"] = Field(alias="evidenceClass")


class IntakeExtensionSpec(StrictModel):
    purpose: Literal["subject", "outcome_criterion", "evidence_obligation", "constraint", "trigger"]
    statement: str = Field(
        min_length=1,
        max_length=2000,
        pattern=r"[^ \t\n\r\f\v\u001c-\u001f\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]",
    )
    scope_refs: tuple[Identifier, ...] = Field(alias="scopeRefs", min_length=1)
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")


class _Envelope(StrictModel):
    api_version: Literal["oac.enterprise-intake.document/v1"] = Field(alias="apiVersion")
    kind: Identifier
    metadata: ResourceMetadata
    spec: dict[str, JsonValue]
    digest: Digest


@dataclass(frozen=True, slots=True)
class _Document[SpecT: StrictModel]:
    ref: ResourceRef
    metadata: ResourceMetadata
    spec: SpecT


@dataclass(frozen=True, slots=True)
class IntakeAdmissionResult:
    manifest_digest: str
    transport_digest: str
    receipt: SourceAdmissionReceipt
    source_root: str | None
    demand_root: str | None
    derived_contract: DerivedContract | None
    evidence_class: str


def _fail(code: str, message: str) -> NoReturn:
    raise OACValidationError(code, message)


def _raw(raw: bytes) -> dict[str, JsonValue]:
    if len(raw) > _MAX_BYTES:
        _fail("CONFORMANCE_RESOURCE_PROFILE_EXCEEDED", "intake material exceeds byte ceiling")
    try:
        value, _ = _raw_mapping(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", "intake material must use UTF-8") from exc
    except RecursionError as exc:
        raise OACValidationError(
            "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED", "intake nesting exceeds parser ceiling"
        ) from exc
    if _json_depth(value) > _MAX_JSON_DEPTH:
        _fail("CONFORMANCE_RESOURCE_PROFILE_EXCEEDED", "intake nesting exceeds declared ceiling")
    try:
        rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, ValueError, TypeError) as exc:
        raise OACValidationError("NON_I_JSON", "intake material is not I-JSON") from exc
    return value


def _digest(value: JsonValue) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def intake_admission_rules() -> dict[str, JsonValue]:
    """Return the public rules owned by this intake profile, independent of configuration."""
    return {
        "registryVersion": "oac.enterprise-intake.admission-rules/v1",
        "profile": "oac.supplier.enterprise-intake/v0.1",
        "normativeRef": "docs/architecture/enterprise-intake.md",
        "effectCeiling": "zero_effect",
        "resourceLimits": {
            "maxMaterialBytes": _MAX_BYTES,
            "maxTotalBytes": _MAX_TOTAL_BYTES,
            "maxJsonDepth": _MAX_JSON_DEPTH,
            "maxResources": 128,
        },
        "identity": {
            "logicalManifestProjection": "omit-only-packageId",
            "transportDigest": "sha256-exact-bytes",
            "resourceDigest": "sha256-rfc8785-omit-only-digest",
            "ruleSetDigest": "sha256-rfc8785-complete-rule-registry",
        },
        "rules": [
            {
                "ruleId": "INTAKE-001",
                "requirement": "Closed UTF-8/I-JSON; no duplicate keys or inventory identities; enforce all declared resource ceilings before mapping.",
            },
            {
                "ruleId": "INTAKE-002",
                "requirement": "Manifest inventory exactly equals material bytes; raw and semantic references are independently verified without adding defaults.",
            },
            {
                "ruleId": "INTAKE-003",
                "requirement": "Caller-pinned Profile selects this exact rule set; namespace, governance, required classes and finite extension mappings are explicit.",
            },
            {
                "ruleId": "INTAKE-004",
                "requirement": "Caller-pinned Authority covers exact Profile, logical manifest, subjects, scope and evaluation time within issuedAt <= time < expiresAt.",
            },
            {
                "ruleId": "INTAKE-005",
                "requirement": "Review and decision require HUMAN actors with exact permitted actions; neither may alias any proposer, including by revision or digest.",
            },
            {
                "ruleId": "INTAKE-006",
                "requirement": "Every known resource and transport-document owner resolves to an admitted Snapshot role; Demand requester and role pass the existing evolution relation.",
            },
            {
                "ruleId": "INTAKE-007",
                "requirement": "Demand and Change bind exact roots, governance and bounded scope; subject/outcome/evidence/constraint/trigger refs resolve to real admitted-profile material bytes.",
            },
            {
                "ruleId": "INTAKE-008",
                "requirement": "Unknown or missing mappings retain unresolved findings; only complete valid inputs plus an independent ADMITTED decision can admit subjects.",
            },
            {
                "ruleId": "INTAKE-009",
                "requirement": "Receipt reasons and refs are sorted unique projections; REJECTED/UNKNOWN admit no subjects and produce no Source/Demand roots or derived work.",
            },
            {
                "ruleId": "INTAKE-010",
                "requirement": "Reuse SourceAdmissionReceipt and the unique Supplier derivation; transport admission never promotes nested candidate facts, executes work or changes the effect ceiling.",
            },
        ],
    }


def intake_rule_set_digest() -> str:
    return _digest(intake_admission_rules())


def _parse[ModelT: StrictModel](raw: bytes, model: type[ModelT]) -> ModelT:
    value = _raw(raw)
    try:
        parsed = model.model_validate_json(raw)
    except ValidationError as exc:
        raise OACValidationError(
            "CORE_SCHEMA_INVALID", "intake material has invalid fields"
        ) from exc
    if not _exact_json_equal(
        value, parsed.model_dump(mode="json", by_alias=True, exclude_unset=True)
    ):
        _fail("CANONICAL_ADMISSION_MISMATCH", "intake parsing changed member presence or values")
    return parsed


def _unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        _fail("EVOLUTION_DUPLICATE_REF", f"duplicate {label}")


def _ref_key(ref: ResourceRef) -> tuple[str, str, str, int, str]:
    return ref.kind, ref.namespace, ref.resource_id, ref.revision, ref.digest


def _identity(ref: ResourceRef) -> tuple[str, str]:
    return ref.namespace, ref.resource_id


def _time(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("CORE_SCHEMA_INVALID", f"{label} requires an explicit timezone")


def parse_intake_manifest(raw: bytes) -> IntakeManifest:
    manifest = _parse(raw, IntakeManifest)
    _unique(tuple(item.source_ref for item in manifest.resources), "source locator")
    _unique(
        tuple(item.resource_ref.resource_id for item in manifest.resources), "resource identity"
    )
    _unique(manifest.declared_scope_refs, "declared scope")
    for instant in (manifest.observed_at, manifest.effective_at):
        _time(instant, "manifest time")
    if any(item.resource_ref.namespace != manifest.namespace for item in manifest.resources):
        _fail("EVOLUTION_NAMESPACE_MISMATCH", "manifest resources cross namespaces")
    return manifest


def _document[SpecT: StrictModel](
    raw: bytes, ref: ResourceRef, model: type[SpecT]
) -> _Document[SpecT]:
    envelope = _parse(raw, _Envelope)
    value = _raw(raw)
    projection = {key: child for key, child in value.items() if key != "digest"}
    if envelope.digest != _digest(projection):
        _fail("ROOT_DIGEST_MISMATCH", "intake document digest does not bind its bytes")
    observed = ResourceRef(
        kind=envelope.kind,
        namespace=envelope.metadata.namespace,
        resourceId=envelope.metadata.id,
        revision=envelope.metadata.revision,
        digest=envelope.digest,
    )
    if observed != ref:
        _fail("EVOLUTION_ROOT_MISMATCH", "intake document differs from caller-pinned ref")
    spec = _parse(rfc8785.dumps(envelope.spec), model)
    return _Document(observed, envelope.metadata, spec)


def _envelope_matches(metadata: ResourceMetadata, manifest: IntakeManifest) -> None:
    if metadata.namespace != manifest.namespace:
        _fail("EVOLUTION_NAMESPACE_MISMATCH", "intake namespace differs")
    if metadata.governance_ref != manifest.governance_ref:
        _fail("EVOLUTION_AUTHORITY_MISMATCH", "intake governance differs")


def _authority(
    manifest: IntakeManifest,
    manifest_digest: str,
    authority: _Document[IntakeAuthoritySpec],
    profile_ref: ResourceRef,
    evaluated_at: datetime,
) -> tuple[ResourceRef, tuple[ResourceRef, ...], tuple[ResourceRef, ...]]:
    decision = authority.spec
    _time(evaluated_at, "evaluation time")
    for instant in (decision.issued_at, decision.expires_at):
        _time(instant, "authority time")
    _envelope_matches(authority.metadata, manifest)
    if (
        decision.profile_ref != profile_ref
        or decision.manifest_digest != manifest_digest
        or set(map(_ref_key, decision.subject_refs))
        != {_ref_key(item.resource_ref) for item in manifest.resources}
        or len(decision.subject_refs) != len(manifest.resources)
        or set(decision.scope_refs) != set(manifest.declared_scope_refs)
        or not decision.issued_at <= evaluated_at < decision.expires_at
        or manifest.observed_at > evaluated_at
        or manifest.effective_at > evaluated_at
    ):
        _fail("EVOLUTION_AUTHORITY_MISMATCH", "authority does not cover the exact current intake")
    _unique(decision.scope_refs, "authority scope")
    identities = [_identity(actor.ref) for actor in decision.actors]
    if len(identities) != len(set(identities)):
        _fail("EVOLUTION_SELF_ADMISSION_FORBIDDEN", "authority aliases one principal")
    if any(
        actor.ref.kind != "Principal" or actor.ref.namespace != manifest.namespace
        for actor in decision.actors
    ):
        _fail(
            "EVOLUTION_REF_KIND_MISMATCH", "authority roster requires same-namespace Principal refs"
        )
    for roster_actor in decision.actors:
        _unique(roster_actor.actions, "actor action")
    actors = {actor.ref.resource_id: actor for actor in decision.actors}
    proposer_ids = sorted({item.proposer_ref for item in manifest.resources})
    proposer_refs = []
    for identifier in proposer_ids:
        actor = actors.get(identifier)
        if actor is None or "PROPOSE" not in actor.actions:
            _fail(
                "EVOLUTION_AUTHORITY_MISMATCH",
                "proposer is not covered by the pinned authority record",
            )
        assert actor is not None
        proposer_refs.append(actor.ref)
    for ref, action in (
        (decision.decision_authority_ref, "ADMIT"),
        *((ref, "REVIEW") for ref in decision.reviewer_refs),
    ):
        actor = actors.get(ref.resource_id)
        if actor is None or actor.ref != ref or action not in actor.actions:
            _fail("EVOLUTION_AUTHORITY_MISMATCH", "review/decision authority is not covered")
        assert actor is not None
        if actor.actor_type != "HUMAN" or ref.resource_id in proposer_ids:
            _fail(
                "EVOLUTION_SELF_ADMISSION_FORBIDDEN", "AI or producer cannot issue intake admission"
            )
    _unique(tuple(ref.resource_id for ref in decision.reviewer_refs), "reviewer")
    _unique(decision.reason_codes, "decision reason")
    if any(code not in REASON_CODE_REGISTRY for code in decision.reason_codes):
        _fail("SOURCE_ADMISSION_VERDICT_INVALID", "decision uses unregistered reasons")
    if (decision.decision is AdmissionVerdict.ADMITTED and decision.reason_codes) or (
        decision.decision is not AdmissionVerdict.ADMITTED and not decision.reason_codes
    ):
        _fail("SOURCE_ADMISSION_VERDICT_INVALID", "decision and reasons disagree")
    return decision.decision_authority_ref, tuple(proposer_refs), tuple(decision.reviewer_refs)


def verify_intake_change_link(
    demand: AdmittedSealedResource,
    change: AdmittedSealedResource,
) -> None:
    """Validate the exact Demand/Change relation shared by intake and read-only projection."""
    demand = validate_sealed_admission(demand, "OrganizationalDemand")
    change = validate_sealed_admission(change, "SemanticChangeSet")
    demand_value, change_value = demand.resource, change.resource
    assert isinstance(demand_value, OrganizationalDemand)
    assert isinstance(change_value, SemanticChangeSet)
    if change_value.spec.demand_ref != demand_value.metadata.id:
        _fail("RESOURCE_COHERENCE_VIOLATION", "Change does not bind the exact Demand ID")
    change_ref = ResourceRef(
        kind=change_value.kind,
        namespace=change_value.metadata.namespace,
        resourceId=change_value.metadata.id,
        revision=change_value.metadata.revision,
        digest=change.resource_digest,
    )
    if change_ref not in demand_value.spec.trigger_refs:
        _fail("DEMAND_ROOT_MISMATCH", "Demand trigger does not bind the exact Change")


def admit_intake(
    manifest_raw: bytes,
    resources: Mapping[str, bytes],
    profile_raw: bytes,
    authority_raw: bytes,
    *,
    profile_ref: ResourceRef,
    authority_ref: ResourceRef,
    evaluated_at: datetime,
) -> IntakeAdmissionResult:
    """Evaluate one exact package; only a complete, independently approved package derives work."""
    manifest = parse_intake_manifest(manifest_raw)
    logical_manifest = _raw(manifest_raw)
    logical_manifest.pop("packageId")
    manifest_digest = _digest(logical_manifest)
    transport_digest = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    if profile_ref.kind != "IntakeProfile" or authority_ref.kind != "IntakeAuthority":
        _fail(
            "EVOLUTION_REF_KIND_MISMATCH",
            "intake requires independently pinned profile and authority refs",
        )
    profile = _document(profile_raw, profile_ref, IntakeProfileSpec)
    authority = _document(authority_raw, authority_ref, IntakeAuthoritySpec)
    _envelope_matches(profile.metadata, manifest)
    rule_set_digest = intake_rule_set_digest()
    if profile.spec.rule_set_digest != rule_set_digest:
        _fail(
            "EVOLUTION_AUTHORITY_MISMATCH",
            "profile does not select the current public admission rules",
        )
    if manifest.intake_profile_ref != profile_ref.resource_id:
        _fail("EVOLUTION_AUTHORITY_MISMATCH", "manifest selects a different profile")
    _unique(profile.spec.scope_refs, "profile scope")
    _unique(profile.spec.required_classes, "required class")
    extension_rules = {(item.kind, item.declared_class) for item in profile.spec.extensions}
    if len(extension_rules) != len(profile.spec.extensions):
        _fail("EVOLUTION_DUPLICATE_REF", "duplicate extension mapping")
    if not set(_CORE_CLASSES).issubset(profile.spec.required_classes):
        _fail("CORE_SCHEMA_INVALID", "Supplier intake requires Snapshot, Demand and Change")
    if not set(manifest.declared_scope_refs).issubset(profile.spec.scope_refs):
        _fail("RESOURCE_COHERENCE_VIOLATION", "manifest exceeds profile scope")
    decision_ref, proposer_refs, reviewer_refs = _authority(
        manifest, manifest_digest, authority, profile_ref, evaluated_at
    )
    if set(resources) != {item.source_ref for item in manifest.resources}:
        _fail(
            "SOURCE_ADMISSION_SUBJECT_MISMATCH",
            "payload inventory must exactly equal manifest inventory",
        )
    if (
        sum(map(len, resources.values()))
        + len(manifest_raw)
        + len(profile_raw)
        + len(authority_raw)
        > _MAX_TOTAL_BYTES
    ):
        _fail(
            "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED", "intake package exceeds aggregate byte ceiling"
        )
    admitted: dict[str, AdmittedSealedResource] = {}
    owner_refs = [profile.metadata.owner_ref, authority.metadata.owner_ref]
    extensions: dict[tuple[str, str, str, int, str], str] = {}
    unresolved: list[ResourceRef] = []
    present = set()
    for item in manifest.resources:
        raw = resources[item.source_ref]
        _raw(raw)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != item.raw_digest:
            _fail("ROOT_DIGEST_MISMATCH", "transport bytes differ from the manifest commitment")
        present.add(item.declared_class)
        if item.declared_class in _CORE_CLASSES:
            kind = _CORE_CLASSES[item.declared_class]
            if kind in admitted:
                _fail("DUPLICATE_ID", "multiple core resources of one intake role")
            candidate = admit_sealed_resource(raw, kind)
            value = candidate.resource
            _envelope_matches(value.metadata, manifest)
            ref = ResourceRef(
                kind=value.kind,
                namespace=value.metadata.namespace,
                resourceId=value.metadata.id,
                revision=value.metadata.revision,
                digest=candidate.resource_digest,
            )
            if ref != item.resource_ref:
                _fail("EVOLUTION_ROOT_MISMATCH", "core input differs from its exact manifest ref")
            admitted[kind] = candidate
        elif (item.resource_ref.kind, item.declared_class) in extension_rules:
            extension = _document(raw, item.resource_ref, IntakeExtensionSpec)
            _envelope_matches(extension.metadata, manifest)
            owner_refs.append(extension.metadata.owner_ref)
            if extension.spec.purpose != item.declared_class or not set(
                extension.spec.scope_refs
            ).issubset(manifest.declared_scope_refs):
                _fail("RESOURCE_COHERENCE_VIOLATION", "extension mapping or scope differs")
            _unique(extension.spec.scope_refs, "extension scope")
            extensions[_ref_key(item.resource_ref)] = item.declared_class
        else:
            unresolved.append(item.resource_ref)
    reasons = set(authority.spec.reason_codes)
    if (
        not set(profile.spec.required_classes).issubset(present)
        or unresolved
        or set(admitted) != set(_CORE_CLASSES.values())
    ):
        reasons.add("UNSUPPORTED_SEMANTICS")
    snapshot = admitted.get("OrganizationSnapshot")
    demand = admitted.get("OrganizationalDemand")
    change = admitted.get("SemanticChangeSet")
    if snapshot is not None and demand is not None and change is not None:
        verify_organizational_demand_from_admitted(demand, snapshot)
        snapshot_value, demand_value, change_value = (
            snapshot.resource,
            demand.resource,
            change.resource,
        )
        assert isinstance(snapshot_value, OrganizationSnapshot)
        assert isinstance(demand_value, OrganizationalDemand)
        assert isinstance(change_value, SemanticChangeSet)
        admitted_roles = {
            role.role_id
            for role in snapshot_value.spec.role_definitions
            if role.admission_status is AdmissionStatus.ADMITTED
        }
        owner_refs.extend(value.resource.metadata.owner_ref for value in admitted.values())
        if any(owner_ref not in admitted_roles for owner_ref in owner_refs):
            _fail(
                "DEMAND_AUTHORITY_UNRESOLVED", "resource owner does not resolve to an admitted role"
            )
        verify_intake_change_link(demand, change)
        if (
            change_value.metadata.governance_ref != demand_value.metadata.governance_ref
            or demand_value.spec.effect_ceiling is not EffectCeiling.ZERO_EFFECT
            or set(manifest.declared_scope_refs)
            != {ref.resource_id for ref in demand_value.spec.subject_refs}
            or not set(change_value.spec.scope_refs).issubset(manifest.declared_scope_refs)
            or not set(manifest.declared_scope_refs).issubset(
                {node.node_id for node in snapshot_value.spec.nodes}
            )
        ):
            _fail("RESOURCE_COHERENCE_VIOLATION", "Demand, Change and declared scope do not close")
        groups = (
            (demand_value.spec.subject_refs, "subject"),
            (demand_value.spec.desired_outcome_refs, "outcome_criterion"),
            (demand_value.spec.evidence_obligation_refs, "evidence_obligation"),
            (demand_value.spec.constraint_refs, "constraint"),
        )
        for refs, expected_class in groups:
            for ref in refs:
                if extensions.get(_ref_key(ref)) != expected_class:
                    unresolved.append(ref)
                    reasons.add("UNSUPPORTED_SEMANTICS")
        change_ref = next(
            item.resource_ref
            for item in manifest.resources
            if item.resource_ref.kind == "SemanticChangeSet"
        )
        for ref in demand_value.spec.trigger_refs:
            if ref != change_ref and extensions.get(_ref_key(ref)) != "trigger":
                unresolved.append(ref)
                reasons.add("UNSUPPORTED_SEMANTICS")
    verdict = authority.spec.decision
    if verdict is AdmissionVerdict.ADMITTED and reasons:
        verdict = AdmissionVerdict.UNKNOWN
    subjects = tuple(sorted((item.resource_ref for item in manifest.resources), key=_ref_key))
    unique_unresolved = tuple(
        sorted({_ref_key(ref): ref for ref in unresolved}.values(), key=_ref_key)
    )
    receipt = seal_resource(
        SourceAdmissionReceipt(
            metadata=ResourceMetadata(
                id="intake-receipt:"
                + _digest(
                    [
                        manifest_digest,
                        authority_ref.model_dump(mode="json", by_alias=True),
                        evaluated_at.isoformat(),
                    ]
                )[7:],
                namespace=manifest.namespace,
                revision=1,
                ownerRef=profile.metadata.owner_ref,
                governanceRef=manifest.governance_ref,
                createdAt=evaluated_at,
                sourceRefs=(
                    *(ref.resource_id for ref in subjects),
                    profile_ref.resource_id,
                    decision_ref.resource_id,
                ),
            ),
            spec=SourceAdmissionReceiptSpec(
                admissionPurpose="enterprise_intake",
                subjectRefs=subjects,
                intakeManifestDigest=manifest_digest,
                intakeProfileRef=profile_ref,
                ruleSetDigest=rule_set_digest,
                proposerRefs=proposer_refs,
                decisionAuthorityRef=decision_ref,
                reviewerRefs=reviewer_refs,
                verdict=verdict,
                reasonCodes=tuple(sorted(reasons)),
                unresolvedRefs=() if verdict is AdmissionVerdict.REJECTED else unique_unresolved,
                admittedSubjectRefs=subjects if verdict is AdmissionVerdict.ADMITTED else (),
            ),
        )
    )
    verify_source_admission_receipt(receipt)
    derived, source_root, demand_root = None, None, None
    if verdict is AdmissionVerdict.ADMITTED:
        assert snapshot is not None and demand is not None and change is not None
        derived = derive_supplier_contract_from_admitted(snapshot, change)
        root_refs = {
            item.resource_ref.kind: item.resource_ref
            for item in manifest.resources
            if item.resource_ref.kind in _CORE_CLASSES.values()
        }
        source_root = project_source_root(
            root_refs["OrganizationSnapshot"], (resource_ref(receipt),)
        )
        demand_root = project_demand_root(
            root_refs["OrganizationalDemand"], (root_refs["SemanticChangeSet"],)
        )
    return IntakeAdmissionResult(
        manifest_digest,
        transport_digest,
        receipt,
        source_root,
        demand_root,
        derived,
        authority.spec.evidence_class,
    )
