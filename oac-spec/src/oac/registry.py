"""Machine-readable registries implemented by the Shadow MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

from .models import (
    CONTEXTUAL_PREDICATE_VERSION,
    LEGACY_PREDICATE_VERSION,
    OrganizationalDemand,
    OrganizationPlan,
    OrganizationSnapshot,
    OrgChangeCase,
    OutcomeCertificate,
    PlanCertificate,
    Resource,
    RuntimeBinding,
    RuntimeLoweringReceipt,
    SemanticChangeSet,
    SourceAdmissionReceipt,
    ZeroEffectRuntimeBundle,
)


@dataclass(frozen=True, slots=True)
class KindEntry:
    kind: str
    document_class: str
    placement: str
    owner: str
    schema_file: str
    detached_fields: tuple[str, ...] = ("digest",)

    def as_json(self) -> dict[str, object]:
        value = asdict(self)
        value["documentClass"] = value.pop("document_class")
        value["schemaFile"] = value.pop("schema_file")
        value["detachedFields"] = value.pop("detached_fields")
        return value


@dataclass(frozen=True, slots=True)
class PredicateVersionEntry:
    predicate_version: str
    wire_type: str
    status: str
    operators: tuple[str, ...]
    truth_algebra: str
    legacy_normalization: dict[str, object]

    def as_json(self) -> dict[str, object]:
        value = asdict(self)
        value["predicateVersion"] = value.pop("predicate_version")
        value["wireType"] = value.pop("wire_type")
        value["truthAlgebra"] = value.pop("truth_algebra")
        value["legacyNormalization"] = value.pop("legacy_normalization")
        value["operators"] = list(value["operators"])
        return value


KIND_MODELS: Final[dict[str, type[Resource]]] = {
    "OrganizationSnapshot": OrganizationSnapshot,
    "SemanticChangeSet": SemanticChangeSet,
    "OrganizationPlan": OrganizationPlan,
    "PlanCertificate": PlanCertificate,
    "RuntimeBinding": RuntimeBinding,
    "RuntimeLoweringReceipt": RuntimeLoweringReceipt,
    "OrgChangeCase": OrgChangeCase,
    "ZeroEffectRuntimeBundle": ZeroEffectRuntimeBundle,
    "OrganizationalDemand": OrganizationalDemand,
    "SourceAdmissionReceipt": SourceAdmissionReceipt,
    "OutcomeCertificate": OutcomeCertificate,
}

KIND_REGISTRY: Final[dict[str, KindEntry]] = {
    "OrganizationSnapshot": KindEntry(
        kind="OrganizationSnapshot",
        document_class="source",
        placement="top_level",
        owner="OAC-023/MVP-001",
        schema_file="OrganizationSnapshot.schema.json",
    ),
    "SemanticChangeSet": KindEntry(
        kind="SemanticChangeSet",
        document_class="source",
        placement="top_level",
        owner="OAC-025/MVP-001",
        schema_file="SemanticChangeSet.schema.json",
    ),
    "OrganizationPlan": KindEntry(
        kind="OrganizationPlan",
        document_class="plan",
        placement="top_level",
        owner="OAC-027/MVP-001",
        schema_file="OrganizationPlan.schema.json",
    ),
    "PlanCertificate": KindEntry(
        kind="PlanCertificate",
        document_class="evidence",
        placement="top_level",
        owner="OAC-028/MVP-001",
        schema_file="PlanCertificate.schema.json",
    ),
    "RuntimeBinding": KindEntry(
        kind="RuntimeBinding",
        document_class="source",
        placement="top_level",
        owner="OAC-007/G1a",
        schema_file="RuntimeBinding.schema.json",
    ),
    "ZeroEffectRuntimeBundle": KindEntry(
        kind="ZeroEffectRuntimeBundle",
        document_class="plan",
        placement="top_level",
        owner="OAC-007/G1a",
        schema_file="ZeroEffectRuntimeBundle.schema.json",
    ),
    "RuntimeLoweringReceipt": KindEntry(
        kind="RuntimeLoweringReceipt",
        document_class="evidence",
        placement="top_level",
        owner="OAC-007/G1a",
        schema_file="RuntimeLoweringReceipt.schema.json",
    ),
    "OrgChangeCase": KindEntry(
        kind="OrgChangeCase",
        document_class="benchmark",
        placement="top_level_fixture",
        owner="OAC-030/MVP-001",
        schema_file="OrgChangeCase.schema.json",
    ),
    "OrganizationalDemand": KindEntry(
        kind="OrganizationalDemand",
        document_class="source",
        placement="top_level",
        owner="OAC-009/E0b",
        schema_file="OrganizationalDemand.schema.json",
    ),
    "SourceAdmissionReceipt": KindEntry(
        kind="SourceAdmissionReceipt",
        document_class="evidence",
        placement="top_level",
        owner="OAC-009/E0b",
        schema_file="SourceAdmissionReceipt.schema.json",
    ),
    "OutcomeCertificate": KindEntry(
        kind="OutcomeCertificate",
        document_class="evidence",
        placement="top_level",
        owner="OAC-009/E0b",
        schema_file="OutcomeCertificate.schema.json",
    ),
}


PREDICATE_VERSION_REGISTRY: Final[dict[str, PredicateVersionEntry]] = {
    LEGACY_PREDICATE_VERSION: PredicateVersionEntry(
        predicate_version=LEGACY_PREDICATE_VERSION,
        wire_type="TransferPredicate",
        status="legacy_supported",
        operators=("semanticType", "afterValues"),
        truth_algebra="strong_kleene",
        legacy_normalization={
            "afterState": "known",
            "subjectSelector": "absent",
            "scopeSelector": "absent",
            "relationTypes": [],
        },
    ),
    CONTEXTUAL_PREDICATE_VERSION: PredicateVersionEntry(
        predicate_version=CONTEXTUAL_PREDICATE_VERSION,
        wire_type="ContextualApplicabilityPredicate",
        status="current",
        operators=(
            "semanticType",
            "afterState",
            "afterValues",
            "subjectSelector.subjectRefs",
            "subjectSelector.nodeTypes",
            "subjectSelector.domainRefs",
            "scopeSelector.refs",
            "scopeSelector.matchMode",
            "scopeSelector.missingBehavior",
            "relationTypes",
        ),
        truth_algebra="strong_kleene",
        legacy_normalization={},
    ),
}


# Stable codes are append-only inside the v0alpha1 semantic surface.  Descriptions are
# informative; tests and integrations bind to the code, not the prose.
REASON_CODE_REGISTRY: Final[dict[str, str]] = {
    "CHANGE_PROFILE_UNKNOWN": "The requested change profile is not registered in this package.",
    "CHANGE_PROFILE_INVALID": "The descriptor does not match its closed, package-pinned change profile.",
    "CHANGE_PROFILE_BINDING_MISMATCH": "The plan or certificate does not bind the selected change profile identity.",
    "CHANGE_PROFILE_DELTA_SET_INVALID": "The change does not contain exactly the selected profile delta path.",
    "CORE_SCHEMA_INVALID": "The JSON resource does not satisfy its registered strict model.",
    "CORE_KIND_UNKNOWN": "The resource kind is not implemented by this profile.",
    "DIGEST_MISSING": "A required detached SHA-256 digest is absent.",
    "ROOT_DIGEST_MISMATCH": "Canonical bytes do not match the pinned digest.",
    "NON_I_JSON": "The value cannot be represented by RFC 8785 over I-JSON.",
    "CANONICAL_ADMISSION_MISMATCH": "Typed validation changed an admitted decoded resource.",
    "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED": "A conformance resource ceiling was exceeded; no partial domain result is permitted.",
    "INPUT_ROOT_MISMATCH": "The plan does not bind the exact supplied source roots.",
    "RESOURCE_COHERENCE_VIOLATION": "Single-enterprise roots disagree on namespace, governance, owner authority, provenance, or scope.",
    "CHANGE_NOT_ADMITTED": "The semantic change is not an admitted source fact.",
    "CHANGE_OPERATION_INCONSISTENT": "A delta operation contradicts its explicit before/after value states.",
    "SUPPLIER_STATUS_DELTA_SET_INVALID": "The Supplier Status Change Profile requires exactly one delta at /status.",
    "CHANGE_VALUE_UNKNOWN": "The supplier status transition is not observable as a known value.",
    "UNSUPPORTED_SEMANTICS": "The profile cannot evaluate the requested semantics.",
    "CANDIDATE_EDGE_NOT_AUTHORITY": "A candidate or disputed edge is preserved but not authoritative.",
    "CANDIDATE_INPUT_NOT_AUTHORITY": "A candidate or disputed endpoint cannot establish mandatory truth.",
    "RETRACTED_SOURCE_EXCLUDED": "A retracted source input cannot contribute current authority.",
    "GRAPH_COVERAGE_PARTIAL": "The declared dependency boundary is incomplete.",
    "IMPACT_SEARCH_TRUNCATED": "The declared impact search depth cut off a possible path.",
    "IMPACT_PATH_OMITTED": "The plan omitted a required affected, unknown, or bounded path.",
    "UNKNOWN_NOT_PRESERVED": "An input or derived Unknown was erased or upgraded.",
    "OBLIGATION_SET_MISMATCH": "Plan obligations differ from independent derivation.",
    "OBLIGATION_UNSATISFIED": "A mandatory obligation has no unique admissible discharge.",
    "ROLE_SCOPE_WIDENED": "A RoleInstance is not a monotone narrowing of its RoleDefinition.",
    "RESPONSIBILITY_BINDING_INVALID": "A WorkUnit binds obligations to roles that do not own those obligations.",
    "QUALIFICATION_INVALID": "A selected principal lacks a required admitted qualification.",
    "SEPARATION_OF_DUTIES_VIOLATION": "Separated roles resolve to the same principal.",
    "ORDER_CONSTRAINT_MISSING": "A required happens-before relation is absent.",
    "ORDER_CONSTRAINT_INVALID": "A happens-before relation is duplicated, dangling, unjustified, or carries forged reason references.",
    "ORDER_CYCLE": "The mandatory happens-before graph contains a cycle.",
    "EVIDENCE_DUTY_MISSING": "A WorkUnit omits evidence required by an obligation.",
    "EFFECT_CEILING_EXCEEDED": "The plan exceeds the zero-effect Shadow boundary.",
    "PLAN_NOT_MINIMAL": "A selected plan element has no necessary contract-relative work.",
    "ROLE_SELECTED_FOR_OBLIGATION": "A role is required by at least one derived obligation.",
    "PRINCIPAL_SELECTED_QUALIFIED": "A principal is the deterministic qualified binding.",
    "NOT_REQUIRED_BY_CONTRACT": "An admitted role is outside the derived obligation set.",
    "PRINCIPAL_NOT_SELECTED": "An eligible principal was considered but not selected.",
    "PLAN_DIGEST_MISMATCH": "The sealed OrganizationPlan digest is invalid.",
    "PLAN_STATUS_MISMATCH": "Plan status is inconsistent with its derived unresolved set.",
    "DUPLICATE_ID": "A set-like resource collection contains a duplicate identifier.",
    "TCK_MANIFEST_INVALID": "The TCK manifest or one of its fixture references is unreadable or invalid.",
    "APPLICABILITY_SOURCE_MISSING": "The evaluated edge, rule, or duty does not resolve in the frozen Snapshot.",
    "APPLICABILITY_SOURCE_MISMATCH": "The supplied predicate differs from its frozen Snapshot source.",
    "PREDICATE_VERSION_UNSUPPORTED": "The predicateVersion is not present in the implemented predicate-version registry.",
    "PREDICATE_DEFINITION_INVALID": "The predicate combines operators in a form forbidden by its registered version.",
    "APPLICABILITY_SEMANTIC_TYPE_MISMATCH": "The change semantic type does not match the predicate.",
    "APPLICABILITY_AFTER_STATE_MISMATCH": "The observed after-state does not match the predicate.",
    "APPLICABILITY_AFTER_VALUE_MISMATCH": "The known observed after-value does not match the predicate vocabulary.",
    "APPLICABILITY_SUBJECT_MISMATCH": "The admitted subject does not satisfy the bounded subject selector.",
    "APPLICABILITY_SCOPE_MISMATCH": "The declared context anchors do not satisfy the scope selector.",
    "APPLICABILITY_RELATION_TYPE_MISMATCH": "The dependency relation does not satisfy the predicate relationTypes.",
    "APPLICABILITY_INPUT_MISSING": "A source field required by the predicate is absent.",
    "APPLICABILITY_INPUT_UNKNOWN": "A source field required by the predicate is explicitly Unknown.",
    "SYNTHETIC_UNKNOWN_VALUE": "The literal string unknown was supplied as a known business value.",
    "APPLICABILITY_EVALUATION_MISSING": "A required applicability ledger fact is absent from the Plan.",
    "APPLICABILITY_EVALUATION_MISMATCH": "A Plan applicability result differs from independent recomputation.",
    "APPLICABILITY_WITNESS_MISMATCH": "Applicability witness refs are missing, forged, or non-canonical.",
    "APPLICABILITY_SCOPE_WIDENED": "Derived closure escaped the frozen contextual selector.",
    "UNKNOWN_TRANSITION_DUTY_MISSING": "An admitted duty for an Unknown evaluation was not materialized.",
    "UNKNOWN_TRANSITION_DUTY_INVALID": "An unknown-transition duty fired without an Unknown evaluation or valid source binding.",
    "ROOT_APPLICABILITY_UNKNOWN": "A root predicate input is Unknown and the final verdict must remain UNKNOWN.",
    "PREREQUISITE_OBLIGATION_ORDER_MISSING": "A contextual prerequisite obligation is not represented by happensBefore.",
    "PREDICATE_FALSE_PATH_INCLUDED": "A path continued through a predicate whose result was FALSE.",
    "LOWERING_CERTIFICATE_MISMATCH": "The supplied PlanCertificate is not exactly the sealed certificate recomputed from the supplied roots.",
    "LOWERING_VERDICT_NOT_ACCEPT": "The recomputed PlanCertificate verdict is not ACCEPT.",
    "LOWERING_RESTRICTIONS_PRESENT": "The recomputed PlanCertificate still carries restrictions.",
    "LOWERING_UNRESOLVED_REFS_PRESENT": "The recomputed PlanCertificate still carries unresolved references.",
    "LOWERING_DIMENSION_NOT_PASS": "At least one recomputed verification dimension is not PASS.",
    "LOWERING_BINDING_NOT_ADMITTED": "The RuntimeBinding digest is absent from the runner-owned admission allowlist.",
    "LOWERING_INPUT_DIGEST_INVALID": "A non-binding sealed lowering input has a missing detached digest or one that does not match its canonical projection; lowering admission fails without a receipt.",
    "LOWERING_BINDING_DIGEST_INVALID": "The RuntimeBinding detached digest is missing or does not match its canonical projection; lowering admission fails without a receipt.",
    "LOWERING_BINDING_ROOT_MISMATCH": "The RuntimeBinding does not bind the exact supplied OrganizationPlan, recomputed PlanCertificate, and authority envelope.",
    "LOWERING_BINDING_INCOMPLETE": "Runtime role bindings are missing, duplicated, or extend beyond the exact Plan RoleInstances.",
    "LOWERING_PRINCIPAL_MISMATCH": "A RuntimeBinding principal does not equal the principal selected by the exact Plan RoleInstance.",
    "LOWERING_RUNTIME_SUBJECT_COLLISION": "Different selected principals collapse to the same runtimeSubject.",
    "LOWERING_HANDLER_INCOMPLETE": "Obligation handler bindings are missing, duplicated, extra, or disagree with required evidence outputs.",
    "LOWERING_CAPABILITY_MISSING": "A selected runtime subject does not declare the capability required by its obligation handler.",
    "LOWERING_CAPABILITY_SET_MISMATCH": "A runtime role capability set is not the exact canonical set required by that role's obligation handlers.",
    "LOWERING_EFFECT_EXPANSION": "A Plan or RuntimeBinding value exceeds the zero-effect lowering ceiling.",
    "LOWERING_ORDER_CYCLE": "The WorkUnit happens-before projection cannot be canonically topologically ordered.",
    "LOWERING_PLAN_PROJECTION_UNSUPPORTED": "An otherwise admitted Plan cannot be projected exactly because lowering-consumed references or evidence sets are duplicated, dangling, empty, or over-complete.",
    "DEMAND_ROOT_MISMATCH": "An OrganizationalDemand does not bind the exact admitted Snapshot root.",
    "DEMAND_AUTHORITY_UNRESOLVED": "The demand requester or accountable role is absent, inactive, non-admitted, or ineligible.",
    "EVOLUTION_DUPLICATE_REF": "A lifecycle root or evidence set contains a duplicate exact ResourceRef.",
    "EVOLUTION_NAMESPACE_MISMATCH": "Lifecycle resources cross enterprise namespaces.",
    "EVOLUTION_REF_KIND_MISMATCH": "A lifecycle ResourceRef has the wrong exact kind.",
    "EVOLUTION_ROOT_INCOMPLETE": "A lifecycle root lacks a mandatory exact reference class.",
    "EVOLUTION_ROOT_MISMATCH": "A supplied lifecycle root differs from independent recomputation.",
    "EVOLUTION_AUTHORITY_MISMATCH": "A governance or authority coordinate differs from the exact admitted lineage.",
    "EVOLUTION_PROVENANCE_MISMATCH": "Lifecycle metadata.sourceRefs does not equal the required ordered provenance.",
    "EVOLUTION_SELF_ADMISSION_FORBIDDEN": "A proposal producer participates in admitting its own Source candidate.",
    "SOURCE_ADMISSION_SUBJECT_MISMATCH": "Admitted Source refs are not an exact subset of reviewed subjects.",
    "SOURCE_ADMISSION_VERDICT_INVALID": "Source admission verdict, reasons, unresolved refs, and admitted refs disagree.",
    "SUCCESSOR_LINEAGE_INCOMPLETE": "A successor admission lacks its exact predecessor, candidate, evolution, or governance lineage.",
    "SUCCESSOR_PREDECESSOR_MISMATCH": "A successor admission references a different predecessor Source root.",
    "OUTCOME_SELF_CERTIFICATION_FORBIDDEN": "The acting runtime or its execution evidence attempts to certify the business outcome.",
    "OUTCOME_PROFILE_UNKNOWN": "An outcome profile identity or version is not package-supported.",
    "OUTCOME_PROFILE_INVALID": "The outcome descriptor or its exact binding differs from the pinned profile.",
    "OUTCOME_EXECUTION_AUTHORIZATION_INVALID": "The disposable execution root lacks its separate sandbox grant, or a default root carries one.",
    "OUTCOME_DIMENSION_SET_INVALID": "The disposable outcome lacks the exact six required dimensions.",
    "OUTCOME_DIMENSION_EVIDENCE_INVALID": "Dimension evidence, reasons, and unresolved observations do not close over the certificate.",
    "OUTCOME_VERDICT_INVALID": "Outcome verdict, dimensions, reasons, and unresolved evidence disagree.",
    "OUTCOME_FORBIDDEN_EFFECT_OBSERVED": "Independent observation found an effect forbidden by the admitted demand.",
    "OUTCOME_OBSERVATION_UNRESOLVED": "The independent oracle could not observe a required outcome dimension.",
    "EVOLUTION_AUTO_PROMOTION_FORBIDDEN": "An evolution candidate was used without an exact external admission decision.",
}


def kind_registry_json() -> dict[str, object]:
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "entries": [KIND_REGISTRY[kind].as_json() for kind in sorted(KIND_REGISTRY)],
    }


def reason_registry_json() -> dict[str, object]:
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "codes": [
            {"code": code, "description": REASON_CODE_REGISTRY[code]}
            for code in sorted(REASON_CODE_REGISTRY)
        ],
    }


def predicate_version_registry_json() -> dict[str, object]:
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "registryKind": "PredicateVersionRegistry",
        "entries": [
            PREDICATE_VERSION_REGISTRY[version].as_json()
            for version in sorted(PREDICATE_VERSION_REGISTRY)
        ],
    }
