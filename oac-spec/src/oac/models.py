"""Strict, storage-neutral models for the OAC Shadow MVP.

The models intentionally cover one falsifiable vertical slice.  They are not a
general workflow DSL and they do not grant authority merely by naming a role or
principal.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

API_VERSION: Final = "oac.dev/v0alpha1"
LEGACY_PREDICATE_VERSION = "oac.supplier.transfer/v0alpha1"
CONTEXTUAL_PREDICATE_VERSION = "oac.supplier.applicability/v0.2"
DERIVATION_REPORT_VERSION: Final = "oac.derivation/v0alpha1"
MAX_DIMENSION_WITNESSES = 32

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")]
type ObservedScalar = str | int | bool


class StrictModel(BaseModel):
    """Common strictness policy used by every normative or benchmark value."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class AdmissionStatus(StrEnum):
    ADMITTED = "admitted"
    CANDIDATE = "candidate"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class BoundaryStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ImpactState(StrEnum):
    AFFECTED = "affected"
    UNAFFECTED_PROVEN = "unaffected_proven"
    UNKNOWN = "unknown"
    OUT_OF_DECLARED_SCOPE = "out_of_declared_scope"


class Verdict(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    PROVISIONAL = "PROVISIONAL"
    UNKNOWN = "UNKNOWN"


class DimensionVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class EffectCeiling(StrEnum):
    ZERO_EFFECT = "zero_effect"


class LoweringStatus(StrEnum):
    """Outcome of the local, zero-effect lowering decision."""

    PRODUCED = "PRODUCED"
    BLOCKED = "BLOCKED"


class RelationType(StrEnum):
    BUSINESS_DEPENDENCY = "business_dependency"
    CONTRACTUAL_DEPENDENCY = "contractual_dependency"
    FINANCIAL_EXPOSURE = "financial_exposure"
    COMPLIANCE_DEPENDENCY = "compliance_dependency"
    OPERATIONAL_DEPENDENCY = "operational_dependency"
    TRACEABILITY = "traceability"
    SIMILARITY = "similarity"
    CORRELATION = "correlation"


class ApplicabilityResult(StrEnum):
    """Strong-Kleene result emitted by a contextual predicate evaluation."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


PROPAGATING_RELATIONS: frozenset[RelationType] = frozenset(
    {
        RelationType.BUSINESS_DEPENDENCY,
        RelationType.CONTRACTUAL_DEPENDENCY,
        RelationType.FINANCIAL_EXPOSURE,
        RelationType.COMPLIANCE_DEPENDENCY,
        RelationType.OPERATIONAL_DEPENDENCY,
    }
)


class ResourceMetadata(StrictModel):
    id: Identifier
    namespace: Identifier
    revision: PositiveInt
    owner_ref: Identifier | None = Field(default=None, alias="ownerRef")
    governance_ref: Identifier | None = Field(default=None, alias="governanceRef")
    created_at: datetime = Field(alias="createdAt")
    effective_from: datetime | None = Field(default=None, alias="effectiveFrom")
    effective_to: datetime | None = Field(default=None, alias="effectiveTo")
    source_refs: tuple[Identifier, ...] = Field(default=(), alias="sourceRefs")

    @model_validator(mode="after")
    def valid_interval(self) -> ResourceMetadata:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("effectiveTo must be later than effectiveFrom")
        return self


class ResourceRef(StrictModel):
    api_version: Literal["oac.dev/v0alpha1"] = Field(default=API_VERSION, alias="apiVersion")
    kind: Identifier
    namespace: Identifier
    resource_id: Identifier = Field(alias="resourceId")
    revision: PositiveInt
    digest: Digest


class ResourceBase(StrictModel):
    api_version: Literal["oac.dev/v0alpha1"] = Field(default=API_VERSION, alias="apiVersion")
    kind: Identifier
    metadata: ResourceMetadata
    digest: Digest | None = None


class OrganizationNode(StrictModel):
    node_id: Identifier = Field(alias="nodeId")
    node_type: Identifier = Field(alias="nodeType")
    domain_ref: Identifier = Field(alias="domainRef")
    owner_role_ref: Identifier | None = Field(default=None, alias="ownerRoleRef")
    admission_status: AdmissionStatus = Field(
        default=AdmissionStatus.ADMITTED, alias="admissionStatus"
    )


class RoleDefinition(StrictModel):
    role_id: Identifier = Field(alias="roleId")
    domain_ref: Identifier = Field(alias="domainRef")
    mission: Identifier
    responsibility_types: tuple[Identifier, ...] = Field(alias="responsibilityTypes")
    required_qualifications: tuple[Identifier, ...] = Field(
        default=(), alias="requiredQualifications"
    )
    effect_ceiling: EffectCeiling = Field(
        default=EffectCeiling.ZERO_EFFECT, alias="effectCeiling"
    )
    admission_status: AdmissionStatus = Field(
        default=AdmissionStatus.ADMITTED, alias="admissionStatus"
    )


class Principal(StrictModel):
    principal_id: Identifier = Field(alias="principalId")
    principal_type: Literal["human", "agent", "service", "team"] = Field(
        alias="principalType"
    )
    eligible_role_refs: tuple[Identifier, ...] = Field(alias="eligibleRoleRefs")
    qualification_refs: tuple[Identifier, ...] = Field(default=(), alias="qualificationRefs")
    status: Literal["active", "inactive"]
    admission_status: AdmissionStatus = Field(
        default=AdmissionStatus.ADMITTED, alias="admissionStatus"
    )


class TransferPredicate(StrictModel):
    """Profile-declared condition under which one edge transfers this change."""

    semantic_type: Identifier = Field(alias="semanticType")
    after_values: tuple[Identifier, ...] = Field(alias="afterValues", min_length=1)


class SubjectSelector(StrictModel):
    """Bounded subject atoms; no arbitrary attributes or executable expressions."""

    subject_refs: tuple[Identifier, ...] = Field(
        default=(), alias="subjectRefs", exclude_if=lambda value: not value
    )
    node_types: tuple[Identifier, ...] = Field(
        default=(), alias="nodeTypes", exclude_if=lambda value: not value
    )
    domain_refs: tuple[Identifier, ...] = Field(
        default=(), alias="domainRefs", exclude_if=lambda value: not value
    )


class ScopeSelector(StrictModel):
    """Explicit interpretation of change ``scopeRefs`` for one predicate."""

    refs: tuple[Identifier, ...] = Field(min_length=1)
    match_mode: Literal["all", "any"] = Field(alias="matchMode")
    missing_behavior: Literal["false", "unknown"] = Field(alias="missingBehavior")


class ContextualApplicabilityPredicate(StrictModel):
    """Versioned, non-executable conjunction evaluated with strong Kleene logic."""

    predicate_version: Identifier = Field(alias="predicateVersion")
    semantic_type: Identifier = Field(alias="semanticType")
    after_state: Literal["known", "unknown", "not_observable", "not_applicable"] = Field(
        alias="afterState"
    )
    after_values: tuple[Identifier, ...] = Field(
        default=(), alias="afterValues", exclude_if=lambda value: not value
    )
    subject_selector: SubjectSelector | None = Field(
        default=None, alias="subjectSelector", exclude_if=lambda value: value is None
    )
    scope_selector: ScopeSelector | None = Field(
        default=None, alias="scopeSelector", exclude_if=lambda value: value is None
    )
    relation_types: tuple[RelationType, ...] = Field(
        default=(), alias="relationTypes", exclude_if=lambda value: not value
    )


class DependencyEdge(StrictModel):
    edge_id: Identifier = Field(alias="edgeId")
    source_ref: Identifier = Field(alias="sourceRef")
    target_ref: Identifier = Field(alias="targetRef")
    relation_type: RelationType = Field(alias="relationType")
    transfer_predicate: TransferPredicate | ContextualApplicabilityPredicate = Field(
        alias="transferPredicate"
    )
    admission_status: AdmissionStatus = Field(alias="admissionStatus")


class ImpactRule(StrictModel):
    rule_id: Identifier = Field(alias="ruleId")
    semantic_type: Identifier = Field(alias="semanticType")
    after_values: tuple[Identifier, ...] = Field(alias="afterValues", min_length=1)
    target_ref: Identifier = Field(alias="targetRef")
    required_role_ref: Identifier = Field(alias="requiredRoleRef")
    obligation_type: Identifier = Field(alias="obligationType")
    required_evidence: tuple[Identifier, ...] = Field(alias="requiredEvidence", min_length=1)
    admission_status: AdmissionStatus = Field(alias="admissionStatus")


class ContextualImpactRule(StrictModel):
    rule_id: Identifier = Field(alias="ruleId")
    applicability: ContextualApplicabilityPredicate
    target_ref: Identifier = Field(alias="targetRef")
    required_role_ref: Identifier = Field(alias="requiredRoleRef")
    obligation_type: Identifier = Field(alias="obligationType")
    required_evidence: tuple[Identifier, ...] = Field(alias="requiredEvidence", min_length=1)
    prerequisite_obligation_types: tuple[Identifier, ...] = Field(
        default=(),
        alias="prerequisiteObligationTypes",
        exclude_if=lambda value: not value,
    )
    admission_status: AdmissionStatus = Field(alias="admissionStatus")


class UnknownTransitionDuty(StrictModel):
    """Snapshot-owned obligation emitted only for an UNKNOWN applicability result."""

    duty_id: Identifier = Field(alias="dutyId")
    applicability: ContextualApplicabilityPredicate
    target_ref: Identifier = Field(alias="targetRef")
    required_role_ref: Identifier = Field(alias="requiredRoleRef")
    obligation_type: Identifier = Field(alias="obligationType")
    required_evidence: tuple[Identifier, ...] = Field(alias="requiredEvidence", min_length=1)
    prerequisite_obligation_types: tuple[Identifier, ...] = Field(
        default=(),
        alias="prerequisiteObligationTypes",
        exclude_if=lambda value: not value,
    )
    admission_status: AdmissionStatus = Field(alias="admissionStatus")


class SeparationConstraint(StrictModel):
    constraint_id: Identifier = Field(alias="constraintId")
    left_role_ref: Identifier = Field(alias="leftRoleRef")
    right_role_ref: Identifier = Field(alias="rightRoleRef")


class CompletenessManifest(StrictModel):
    status: BoundaryStatus
    covered_node_refs: tuple[Identifier, ...] = Field(alias="coveredNodeRefs")
    covered_relation_types: tuple[RelationType, ...] = Field(alias="coveredRelationTypes")
    max_depth: PositiveInt = Field(alias="maxDepth")
    known_gaps: tuple[Identifier, ...] = Field(default=(), alias="knownGaps")
    discovery_role_ref: Identifier = Field(alias="discoveryRoleRef")
    discovery_target_ref: Identifier | None = Field(
        default=None,
        alias="discoveryTargetRef",
        exclude_if=lambda value: value is None,
    )
    discovery_obligation_type: Identifier | None = Field(
        default=None,
        alias="discoveryObligationType",
        exclude_if=lambda value: value is None,
    )
    discovery_evidence: tuple[Identifier, ...] = Field(
        default=(),
        alias="discoveryEvidence",
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def complete_has_no_gaps(self) -> CompletenessManifest:
        if self.status is BoundaryStatus.COMPLETE and self.known_gaps:
            raise ValueError("a complete boundary cannot declare known gaps")
        contextual_discovery = (
            self.discovery_target_ref is not None,
            self.discovery_obligation_type is not None,
            bool(self.discovery_evidence),
        )
        if any(contextual_discovery) and not all(contextual_discovery):
            raise ValueError(
                "discoveryTargetRef, discoveryObligationType, and discoveryEvidence "
                "must be declared together"
            )
        return self


class OrganizationSnapshotSpec(StrictModel):
    nodes: tuple[OrganizationNode, ...] = Field(min_length=1)
    role_definitions: tuple[RoleDefinition, ...] = Field(alias="roleDefinitions", min_length=1)
    principals: tuple[Principal, ...] = Field(min_length=1)
    dependency_edges: tuple[DependencyEdge, ...] = Field(
        default=(), alias="dependencyEdges"
    )
    impact_rules: tuple[ImpactRule | ContextualImpactRule, ...] = Field(
        default=(), alias="impactRules"
    )
    unknown_transition_duties: tuple[UnknownTransitionDuty, ...] = Field(
        default=(),
        alias="unknownTransitionDuties",
        exclude_if=lambda value: not value,
    )
    separation_constraints: tuple[SeparationConstraint, ...] = Field(
        default=(), alias="separationConstraints"
    )
    completeness: CompletenessManifest

    @model_validator(mode="after")
    def validate_references(self) -> OrganizationSnapshotSpec:
        node_ids = _unique_ids("node", (item.node_id for item in self.nodes))
        role_ids = _unique_ids("role", (item.role_id for item in self.role_definitions))
        principal_ids = _unique_ids(
            "principal", (item.principal_id for item in self.principals)
        )
        edge_ids = _unique_ids("edge", (item.edge_id for item in self.dependency_edges))
        rule_ids = _unique_ids("rule", (item.rule_id for item in self.impact_rules))
        duty_ids = _unique_ids(
            "unknown transition duty",
            (item.duty_id for item in self.unknown_transition_duties),
        )
        _unique_ids("applicability source", (*edge_ids, *rule_ids, *duty_ids))
        _unique_ids(
            "separation constraint",
            (item.constraint_id for item in self.separation_constraints),
        )
        del principal_ids, edge_ids

        for node in self.nodes:
            if node.owner_role_ref is not None and node.owner_role_ref not in role_ids:
                raise ValueError(f"unknown ownerRoleRef: {node.owner_role_ref}")
        for role in self.role_definitions:
            if role.domain_ref not in {node.domain_ref for node in self.nodes}:
                raise ValueError(f"role domain is not represented by a node: {role.domain_ref}")
        for principal in self.principals:
            unknown_roles = set(principal.eligible_role_refs) - role_ids
            if unknown_roles:
                raise ValueError(f"principal has unknown eligible role(s): {sorted(unknown_roles)}")
        for edge in self.dependency_edges:
            if edge.source_ref not in node_ids or edge.target_ref not in node_ids:
                raise ValueError(f"edge {edge.edge_id} references an unknown node")
        for rule in self.impact_rules:
            if rule.target_ref not in node_ids or rule.required_role_ref not in role_ids:
                raise ValueError(f"rule {rule.rule_id} references an unknown node or role")
        for duty in self.unknown_transition_duties:
            if duty.target_ref not in node_ids or duty.required_role_ref not in role_ids:
                raise ValueError(f"duty {duty.duty_id} references an unknown node or role")
        for constraint in self.separation_constraints:
            if constraint.left_role_ref not in role_ids or constraint.right_role_ref not in role_ids:
                raise ValueError(f"constraint {constraint.constraint_id} references an unknown role")
        if self.completeness.discovery_role_ref not in role_ids:
            raise ValueError("discoveryRoleRef must resolve to a RoleDefinition")
        if (
            self.completeness.discovery_target_ref is not None
            and self.completeness.discovery_target_ref not in node_ids
        ):
            raise ValueError("discoveryTargetRef must resolve to an OrganizationNode")
        if not set(self.completeness.covered_node_refs).issubset(node_ids):
            raise ValueError("coveredNodeRefs contains an unknown node")
        return self


class OrganizationSnapshot(ResourceBase):
    kind: Literal["OrganizationSnapshot"] = "OrganizationSnapshot"
    spec: OrganizationSnapshotSpec

    @model_validator(mode="after")
    def has_single_enterprise_authority_envelope(self) -> OrganizationSnapshot:
        if self.metadata.owner_ref is None:
            raise ValueError("OrganizationSnapshot requires ownerRef")
        admitted_role_refs = {
            role.role_id
            for role in self.spec.role_definitions
            if role.admission_status is AdmissionStatus.ADMITTED
        }
        if self.metadata.owner_ref not in admitted_role_refs:
            raise ValueError(
                "OrganizationSnapshot ownerRef must resolve to an admitted RoleDefinition"
            )
        if self.metadata.governance_ref is None:
            raise ValueError("OrganizationSnapshot requires governanceRef")
        if not self.metadata.source_refs:
            raise ValueError("OrganizationSnapshot requires at least one sourceRef")
        return self


class ObservedValue(StrictModel):
    state: Literal["known", "unknown", "not_observable", "not_applicable"]
    value: ObservedScalar | None = None

    @model_validator(mode="after")
    def state_matches_value(self) -> ObservedValue:
        if self.state == "known" and self.value is None:
            raise ValueError("known values require value")
        if self.state != "known" and self.value is not None:
            raise ValueError("non-known values cannot carry value")
        return self


class ChangeDelta(StrictModel):
    path: Annotated[str, StringConstraints(pattern=r"^/.*")]
    operation: Literal["add", "remove", "replace", "invalidate", "unknown_transition"]
    before: ObservedValue
    after: ObservedValue
    before_version: Identifier | None = Field(default=None, alias="beforeVersion")
    after_version: Identifier | None = Field(default=None, alias="afterVersion")

    def operation_is_consistent(self) -> bool:
        """Return whether the operation agrees with the explicit value states."""

        before_state = self.before.state
        after_state = self.after.state
        if self.operation == "add":
            return before_state == "not_applicable" and after_state != "not_applicable"
        if self.operation == "remove":
            return before_state != "not_applicable" and after_state == "not_applicable"
        if self.operation == "replace":
            return (
                before_state != "not_applicable"
                and after_state == "known"
                and self.before != self.after
            )
        if self.operation == "invalidate":
            return before_state == "known" and after_state == "not_observable"
        return (
            self.operation == "unknown_transition"
            and before_state not in {"unknown", "not_applicable"}
            and after_state == "unknown"
        )

    @model_validator(mode="after")
    def operation_matches_value_states(self) -> ChangeDelta:
        if not self.operation_is_consistent():
            raise ValueError(
                f"operation {self.operation!r} is inconsistent with "
                f"{self.before.state!r}->{self.after.state!r}"
            )
        return self


class SemanticChangeSetSpec(StrictModel):
    demand_ref: Identifier = Field(alias="demandRef")
    subject_ref: Identifier = Field(alias="subjectRef")
    semantic_type: Identifier = Field(alias="semanticType")
    deltas: tuple[ChangeDelta, ...] = Field(min_length=1)
    observed_at: datetime = Field(alias="observedAt")
    effective_at: datetime = Field(alias="effectiveAt")
    source_ref: Identifier = Field(alias="sourceRef")
    scope_refs: tuple[Identifier, ...] = Field(alias="scopeRefs", min_length=1)
    reason: Identifier
    admission_status: AdmissionStatus = Field(alias="admissionStatus")

    @model_validator(mode="after")
    def has_unique_deltas(self) -> SemanticChangeSetSpec:
        paths = [delta.path for delta in self.deltas]
        if len(paths) != len(set(paths)):
            raise ValueError("delta paths must be unique")
        return self


class SemanticChangeSet(ResourceBase):
    kind: Literal["SemanticChangeSet"] = "SemanticChangeSet"
    spec: SemanticChangeSetSpec

    @model_validator(mode="after")
    def has_single_enterprise_authority_envelope(self) -> SemanticChangeSet:
        if self.metadata.owner_ref is None:
            raise ValueError("SemanticChangeSet requires ownerRef")
        if self.metadata.governance_ref is None:
            raise ValueError("SemanticChangeSet requires governanceRef")
        if self.spec.source_ref not in self.metadata.source_refs:
            raise ValueError("spec.sourceRef must be present in metadata.sourceRefs")
        return self


class ApplicabilityEvaluation(StrictModel):
    """Deterministic ledger fact for one edge, rule, or unknown-transition duty."""

    evaluation_id: Identifier = Field(alias="evaluationId")
    source_ref: Identifier = Field(alias="sourceRef")
    predicate_version: Identifier = Field(alias="predicateVersion")
    result: ApplicabilityResult
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    witness_refs: tuple[Identifier, ...] = Field(alias="witnessRefs", min_length=1)


class ImpactPath(StrictModel):
    path_id: Identifier = Field(alias="pathId")
    target_ref: Identifier = Field(alias="targetRef")
    state: ImpactState
    edge_refs: tuple[Identifier, ...] = Field(default=(), alias="edgeRefs")
    rule_refs: tuple[Identifier, ...] = Field(default=(), alias="ruleRefs")
    evaluation_refs: tuple[Identifier, ...] = Field(
        default=(), alias="evaluationRefs", exclude_if=lambda value: not value
    )
    duty_refs: tuple[Identifier, ...] = Field(
        default=(), alias="dutyRefs", exclude_if=lambda value: not value
    )
    origin: Literal["dependency", "rule", "gap", "bounded_non_impact", "excluded"]
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    truncated: bool = False


class CoverageObligation(StrictModel):
    obligation_id: Identifier = Field(alias="obligationId")
    origin: Literal["dependency_path", "organizational_rule", "discovery_gap"]
    target_ref: Identifier = Field(alias="targetRef")
    domain_ref: Identifier = Field(alias="domainRef")
    required_role_ref: Identifier = Field(alias="requiredRoleRef")
    obligation_type: Identifier = Field(alias="obligationType")
    required_evidence: tuple[Identifier, ...] = Field(alias="requiredEvidence", min_length=1)
    path_refs: tuple[Identifier, ...] = Field(alias="pathRefs", min_length=1)
    resolution_state: Literal["affected", "unknown", "unresolved"] = Field(
        alias="resolutionState"
    )


class DerivationOrderRequirement(StrictModel):
    """Topology-neutral ordering requirement emitted by Profile derivation."""

    predecessor_role_ref: Identifier = Field(alias="predecessorRoleRef")
    successor_role_ref: Identifier = Field(alias="successorRoleRef")
    reason_refs: tuple[Identifier, ...] = Field(alias="reasonRefs", min_length=1)
    role_wide: bool = Field(alias="roleWide")
    dependency_reason_refs: tuple[Identifier, ...] = Field(
        default=(), alias="dependencyReasonRefs"
    )
    prerequisite_reason_groups: tuple[tuple[Identifier, ...], ...] = Field(
        default=(), alias="prerequisiteReasonGroups"
    )


class ProfileDerivationReport(StrictModel):
    """Deterministic Profile semantics without any selected Agent topology."""

    api_version: Literal["oac.derivation/v0alpha1"] = Field(
        alias="apiVersion"
    )
    kind: Literal["ProfileDerivationReport"]
    profile_id: Literal["oac.supplier.transfer"] = Field(alias="profileId")
    profile_version: Literal["v0.2"] = Field(alias="profileVersion")
    snapshot_digest: Digest = Field(alias="snapshotDigest")
    change_digest: Digest = Field(alias="changeDigest")
    applicability_evaluations: tuple[ApplicabilityEvaluation, ...] = Field(
        alias="applicabilityEvaluations"
    )
    impact_paths: tuple[ImpactPath, ...] = Field(alias="impactPaths")
    obligations: tuple[CoverageObligation, ...]
    required_orders: tuple[DerivationOrderRequirement, ...] = Field(
        alias="requiredOrders"
    )
    unresolved_refs: tuple[Identifier, ...] = Field(alias="unresolvedRefs")
    root_applicability_unknown: bool = Field(alias="rootApplicabilityUnknown")


class RoleInstance(StrictModel):
    role_instance_id: Identifier = Field(alias="roleInstanceId")
    role_definition_ref: Identifier = Field(alias="roleDefinitionRef")
    principal_ref: Identifier = Field(alias="principalRef")
    mission: Identifier
    obligation_refs: tuple[Identifier, ...] = Field(alias="obligationRefs", min_length=1)
    qualification_refs: tuple[Identifier, ...] = Field(alias="qualificationRefs")
    effect_ceiling: EffectCeiling = Field(alias="effectCeiling")


class WorkUnit(StrictModel):
    work_unit_id: Identifier = Field(alias="workUnitId")
    role_instance_refs: tuple[Identifier, ...] = Field(alias="roleInstanceRefs", min_length=1)
    accountable_role_instance_ref: Identifier = Field(alias="accountableRoleInstanceRef")
    obligation_refs: tuple[Identifier, ...] = Field(alias="obligationRefs", min_length=1)
    evidence_outputs: tuple[Identifier, ...] = Field(alias="evidenceOutputs", min_length=1)
    effect_ceiling: EffectCeiling = Field(alias="effectCeiling")


class OrderConstraint(StrictModel):
    predecessor_ref: Identifier = Field(alias="predecessorRef")
    successor_ref: Identifier = Field(alias="successorRef")
    relation: Literal["must_complete_before"] = "must_complete_before"
    reason_refs: tuple[Identifier, ...] = Field(alias="reasonRefs", min_length=1)

    @model_validator(mode="after")
    def has_unique_reasons_and_distinct_endpoints(self) -> OrderConstraint:
        if self.predecessor_ref == self.successor_ref:
            raise ValueError("a happensBefore constraint cannot be a self-edge")
        if len(self.reason_refs) != len(set(self.reason_refs)):
            raise ValueError("reasonRefs must be unique")
        return self


class PlanDecision(StrictModel):
    decision_id: Identifier = Field(alias="decisionId")
    subject_ref: Identifier = Field(alias="subjectRef")
    input_class: Literal["admitted", "candidate", "disputed", "retracted"] = Field(
        alias="inputClass"
    )
    disposition: Literal["included", "excluded", "unresolved"]
    reason_codes: tuple[ReasonCode, ...] = Field(alias="reasonCodes", min_length=1)


class MinimalityClaim(StrictModel):
    level: Literal["none", "inclusion_minimal"]
    considered_role_refs: tuple[Identifier, ...] = Field(alias="consideredRoleRefs")
    considered_principal_refs: tuple[Identifier, ...] = Field(alias="consideredPrincipalRefs")
    non_removable_refs: tuple[Identifier, ...] = Field(alias="nonRemovableRefs")


class ChangeProfileBinding(StrictModel):
    profile_id: Identifier = Field(alias="profileId")
    profile_version: Identifier = Field(alias="profileVersion")
    profile_digest: Digest = Field(alias="profileDigest")


class OrganizationPlanSpec(StrictModel):
    profile_binding: ChangeProfileBinding | None = Field(
        default=None, alias="profileBinding", exclude_if=lambda value: value is None
    )
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    change_ref: ResourceRef = Field(alias="changeRef")
    status: Literal["planned", "guarded_unresolved"]
    effect_ceiling: EffectCeiling = Field(alias="effectCeiling")
    compiler_id: Identifier = Field(alias="compilerId")
    compiler_version: Identifier = Field(alias="compilerVersion")
    applicability_evaluations: tuple[ApplicabilityEvaluation, ...] = Field(
        default=(),
        alias="applicabilityEvaluations",
        exclude_if=lambda value: not value,
    )
    impact_paths: tuple[ImpactPath, ...] = Field(alias="impactPaths")
    obligations: tuple[CoverageObligation, ...]
    role_instances: tuple[RoleInstance, ...] = Field(alias="roleInstances")
    work_units: tuple[WorkUnit, ...] = Field(alias="workUnits")
    happens_before: tuple[OrderConstraint, ...] = Field(alias="happensBefore")
    decisions: tuple[PlanDecision, ...]
    unresolved_refs: tuple[Identifier, ...] = Field(alias="unresolvedRefs")
    minimality: MinimalityClaim

    @model_validator(mode="after")
    def unique_plan_ids(self) -> OrganizationPlanSpec:
        evaluation_ids = _unique_ids(
            "applicability evaluation",
            (item.evaluation_id for item in self.applicability_evaluations),
        )
        _unique_ids("impact path", (item.path_id for item in self.impact_paths))
        _unique_ids("obligation", (item.obligation_id for item in self.obligations))
        _unique_ids("role instance", (item.role_instance_id for item in self.role_instances))
        _unique_ids("work unit", (item.work_unit_id for item in self.work_units))
        _unique_ids("decision", (item.decision_id for item in self.decisions))
        order_keys = [
            (item.predecessor_ref, item.successor_ref, item.relation)
            for item in self.happens_before
        ]
        if len(order_keys) != len(set(order_keys)):
            raise ValueError("happensBefore constraints must be unique")
        dangling_evaluations = {
            evaluation_ref
            for path in self.impact_paths
            for evaluation_ref in path.evaluation_refs
            if evaluation_ref not in evaluation_ids
        }
        if dangling_evaluations:
            raise ValueError(
                "impactPaths contains unknown evaluationRefs: "
                f"{sorted(dangling_evaluations)}"
            )
        return self


class OrganizationPlan(ResourceBase):
    kind: Literal["OrganizationPlan"] = "OrganizationPlan"
    spec: OrganizationPlanSpec

    @model_validator(mode="after")
    def roots_match_single_enterprise_envelope(self) -> OrganizationPlan:
        if self.metadata.owner_ref is None:
            raise ValueError("OrganizationPlan requires ownerRef")
        if self.metadata.governance_ref is None:
            raise ValueError("OrganizationPlan requires governanceRef")
        if self.spec.snapshot_ref.kind != "OrganizationSnapshot":
            raise ValueError("snapshotRef must reference OrganizationSnapshot")
        if self.spec.change_ref.kind != "SemanticChangeSet":
            raise ValueError("changeRef must reference SemanticChangeSet")
        namespaces = {
            self.metadata.namespace,
            self.spec.snapshot_ref.namespace,
            self.spec.change_ref.namespace,
        }
        if len(namespaces) != 1:
            raise ValueError("plan roots must share metadata.namespace")
        expected_sources = (
            self.spec.snapshot_ref.resource_id,
            self.spec.change_ref.resource_id,
        )
        if self.metadata.source_refs != expected_sources:
            raise ValueError("plan metadata.sourceRefs must be the ordered exact input roots")
        return self


class VerificationDimension(StrictModel):
    name: Identifier
    verdict: DimensionVerdict
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    witnesses: tuple[Identifier, ...] = Field(default=(), max_length=MAX_DIMENSION_WITNESSES)
    witnesses_omitted: NonNegativeInt = Field(default=0, alias="witnessesOmitted")

    @model_validator(mode="after")
    def witnesses_are_deterministic_and_bounded(self) -> VerificationDimension:
        if self.witnesses != tuple(sorted(set(self.witnesses))):
            raise ValueError("witnesses must be unique and deterministically sorted")
        return self


class PlanCertificateSpec(StrictModel):
    profile_binding: ChangeProfileBinding | None = Field(
        default=None, alias="profileBinding", exclude_if=lambda value: value is None
    )
    subject_plan_ref: ResourceRef = Field(alias="subjectPlanRef")
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    change_ref: ResourceRef = Field(alias="changeRef")
    verdict: Verdict
    dimensions: tuple[VerificationDimension, ...]
    reason_codes: tuple[ReasonCode, ...] = Field(alias="reasonCodes")
    restrictions: tuple[Identifier, ...]
    unresolved_refs: tuple[Identifier, ...] = Field(alias="unresolvedRefs")
    certifier_id: Identifier = Field(alias="certifierId")
    certifier_build: Identifier = Field(alias="certifierBuild")


class PlanCertificate(ResourceBase):
    kind: Literal["PlanCertificate"] = "PlanCertificate"
    spec: PlanCertificateSpec

    @model_validator(mode="after")
    def roots_match_single_enterprise_envelope(self) -> PlanCertificate:
        if self.metadata.owner_ref is None:
            raise ValueError("PlanCertificate requires ownerRef")
        if self.metadata.governance_ref is None:
            raise ValueError("PlanCertificate requires governanceRef")
        refs = (
            (self.spec.subject_plan_ref, "OrganizationPlan"),
            (self.spec.snapshot_ref, "OrganizationSnapshot"),
            (self.spec.change_ref, "SemanticChangeSet"),
        )
        if any(ref.kind != expected_kind for ref, expected_kind in refs):
            raise ValueError("certificate root kinds are inconsistent")
        roots_are_coherent = all(
            ref.namespace == self.metadata.namespace for ref, _ in refs
        )
        is_bounded_rejection_evidence = (
            self.spec.verdict is Verdict.REJECT
            and "RESOURCE_COHERENCE_VIOLATION" in self.spec.reason_codes
        )
        if not roots_are_coherent and not is_bounded_rejection_evidence:
            raise ValueError("certificate roots must share metadata.namespace")
        expected_sources = tuple(ref.resource_id for ref, _ in refs)
        if self.metadata.source_refs != expected_sources:
            raise ValueError("certificate metadata.sourceRefs must be the ordered exact roots")
        return self


class RuntimeRoleBinding(StrictModel):
    """Runner-owned identity and capability mapping for one exact RoleInstance."""

    role_instance_ref: Identifier = Field(alias="roleInstanceRef")
    principal_ref: Identifier = Field(alias="principalRef")
    runtime_subject: Identifier = Field(alias="runtimeSubject")
    capability_refs: tuple[Identifier, ...] = Field(
        default=(), alias="capabilityRefs"
    )


class RuntimeHandlerBinding(StrictModel):
    """Content-bound, non-executable handler identity for one obligation type."""

    obligation_type: Identifier = Field(alias="obligationType")
    handler_ref: Identifier = Field(alias="handlerRef")
    handler_digest: Digest = Field(alias="handlerDigest")
    capability_ref: Identifier = Field(alias="capabilityRef")
    evidence_output_refs: tuple[Identifier, ...] = Field(
        alias="evidenceOutputRefs", min_length=1
    )
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")


class RuntimeBindingSpec(StrictModel):
    """Closed mapping supplied by a runtime owner; it grants no runtime admission."""

    subject_plan_ref: ResourceRef = Field(alias="subjectPlanRef")
    subject_certificate_ref: ResourceRef = Field(alias="subjectCertificateRef")
    role_bindings: tuple[RuntimeRoleBinding, ...] = Field(alias="roleBindings")
    handler_bindings: tuple[RuntimeHandlerBinding, ...] = Field(alias="handlerBindings")
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")
    target_writes: Literal[0] = Field(alias="targetWrites")


class RuntimeBinding(ResourceBase):
    kind: Literal["RuntimeBinding"] = "RuntimeBinding"
    spec: RuntimeBindingSpec

    @model_validator(mode="after")
    def roots_match_single_enterprise_envelope(self) -> RuntimeBinding:
        if self.metadata.owner_ref is None:
            raise ValueError("RuntimeBinding requires ownerRef")
        if self.metadata.governance_ref is None:
            raise ValueError("RuntimeBinding requires governanceRef")
        refs = (
            (self.spec.subject_plan_ref, "OrganizationPlan"),
            (self.spec.subject_certificate_ref, "PlanCertificate"),
        )
        if any(ref.kind != expected_kind for ref, expected_kind in refs):
            raise ValueError("runtime binding root kinds are inconsistent")
        if any(ref.namespace != self.metadata.namespace for ref, _ in refs):
            raise ValueError("runtime binding roots must share metadata.namespace")
        expected_sources = tuple(ref.resource_id for ref, _ in refs)
        if self.metadata.source_refs != expected_sources:
            raise ValueError(
                "runtime binding metadata.sourceRefs must be the ordered exact roots"
            )
        return self


class RuntimeStepRoleBinding(StrictModel):
    role_instance_ref: Identifier = Field(alias="roleInstanceRef")
    principal_ref: Identifier = Field(alias="principalRef")
    runtime_subject: Identifier = Field(alias="runtimeSubject")


class RuntimeStepHandlerBinding(StrictModel):
    obligation_ref: Identifier = Field(alias="obligationRef")
    obligation_type: Identifier = Field(alias="obligationType")
    handler_ref: Identifier = Field(alias="handlerRef")
    handler_digest: Digest = Field(alias="handlerDigest")
    capability_ref: Identifier = Field(alias="capabilityRef")
    evidence_output_refs: tuple[Identifier, ...] = Field(
        alias="evidenceOutputRefs", min_length=1
    )
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")


class ZeroEffectRuntimeStep(StrictModel):
    step_index: NonNegativeInt = Field(alias="stepIndex")
    work_unit_ref: Identifier = Field(alias="workUnitRef")
    role_bindings: tuple[RuntimeStepRoleBinding, ...] = Field(
        alias="roleBindings", min_length=1
    )
    accountable: RuntimeStepRoleBinding
    obligation_refs: tuple[Identifier, ...] = Field(alias="obligationRefs", min_length=1)
    handler_bindings: tuple[RuntimeStepHandlerBinding, ...] = Field(
        alias="handlerBindings", min_length=1
    )
    evidence_output_refs: tuple[Identifier, ...] = Field(
        alias="evidenceOutputRefs", min_length=1
    )
    predecessor_refs: tuple[Identifier, ...] = Field(alias="predecessorRefs")
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")
    target_writes: Literal[0] = Field(alias="targetWrites")


class ZeroEffectRuntimeBundleSpec(StrictModel):
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    change_ref: ResourceRef = Field(alias="changeRef")
    subject_plan_ref: ResourceRef = Field(alias="subjectPlanRef")
    certificate_ref: ResourceRef = Field(alias="certificateRef")
    runtime_binding_ref: ResourceRef = Field(alias="runtimeBindingRef")
    lowering_profile: Literal["oac.runtime-lowering/zero-effect/v0.1"] = Field(
        alias="loweringProfile"
    )
    obligation_contract_digest: Digest = Field(alias="obligationContractDigest")
    topology_digest: Digest = Field(alias="topologyDigest")
    steps: tuple[ZeroEffectRuntimeStep, ...] = Field(min_length=1)
    effect_ceiling: Literal[EffectCeiling.ZERO_EFFECT] = Field(alias="effectCeiling")
    target_writes: Literal[0] = Field(alias="targetWrites")
    requires_runtime_admission: Literal[True] = Field(alias="requiresRuntimeAdmission")

    @model_validator(mode="after")
    def steps_are_closed_and_canonical(self) -> ZeroEffectRuntimeBundleSpec:
        if tuple(step.step_index for step in self.steps) != tuple(range(len(self.steps))):
            raise ValueError("runtime step indices must be contiguous from zero")
        work_refs = tuple(step.work_unit_ref for step in self.steps)
        if len(work_refs) != len(set(work_refs)):
            raise ValueError("runtime step workUnitRefs must be unique")
        work_ref_set = set(work_refs)
        predecessors_by_work = {
            step.work_unit_ref: set(step.predecessor_refs) for step in self.steps
        }
        if any(
            not predecessors.issubset(work_ref_set) or work_ref in predecessors
            for work_ref, predecessors in predecessors_by_work.items()
        ):
            raise ValueError("runtime step predecessors must resolve without self-edges")
        emitted: set[str] = set()
        role_projection: dict[str, tuple[str, str]] = {}
        principals_by_subject: dict[str, set[str]] = {}
        for step in self.steps:
            ready = sorted(
                (
                    work_ref
                    for work_ref in work_ref_set - emitted
                    if predecessors_by_work[work_ref].issubset(emitted)
                ),
                key=str.encode,
            )
            if not ready or step.work_unit_ref != ready[0]:
                raise ValueError("runtime steps must use canonical UTF-8 Kahn order")
            role_refs = tuple(item.role_instance_ref for item in step.role_bindings)
            if role_refs != tuple(sorted(set(role_refs), key=str.encode)):
                raise ValueError("runtime step roleBindings must be sorted and unique")
            if step.accountable not in step.role_bindings:
                raise ValueError("runtime step accountable binding must be a role binding")
            for role in step.role_bindings:
                projection = (role.principal_ref, role.runtime_subject)
                previous = role_projection.setdefault(role.role_instance_ref, projection)
                if previous != projection:
                    raise ValueError("runtime role projection must be stable across steps")
                principals_by_subject.setdefault(role.runtime_subject, set()).add(
                    role.principal_ref
                )
            if step.obligation_refs != tuple(
                sorted(set(step.obligation_refs), key=str.encode)
            ):
                raise ValueError("runtime step obligationRefs must be sorted and unique")
            handler_refs = tuple(
                item.obligation_ref for item in step.handler_bindings
            )
            if handler_refs != step.obligation_refs:
                raise ValueError("runtime step handlers must exactly follow obligations")
            for handler in step.handler_bindings:
                if handler.evidence_output_refs != tuple(
                    sorted(set(handler.evidence_output_refs), key=str.encode)
                ):
                    raise ValueError("runtime handler evidence refs must be sorted and unique")
            if step.evidence_output_refs != tuple(
                sorted(set(step.evidence_output_refs), key=str.encode)
            ):
                raise ValueError("runtime step evidence refs must be sorted and unique")
            handler_evidence = {
                evidence
                for handler in step.handler_bindings
                for evidence in handler.evidence_output_refs
            }
            if handler_evidence != set(step.evidence_output_refs):
                raise ValueError("runtime handlers must exactly cover step evidence")
            if step.predecessor_refs != tuple(
                sorted(set(step.predecessor_refs), key=str.encode)
            ) or not set(step.predecessor_refs).issubset(emitted):
                raise ValueError("runtime step predecessors must be sorted prior steps")
            emitted.add(step.work_unit_ref)
        if any(len(principals) > 1 for principals in principals_by_subject.values()):
            raise ValueError("runtime bundle cannot collapse distinct principals")
        return self


class ZeroEffectRuntimeBundle(ResourceBase):
    kind: Literal["ZeroEffectRuntimeBundle"] = "ZeroEffectRuntimeBundle"
    spec: ZeroEffectRuntimeBundleSpec

    @model_validator(mode="after")
    def roots_match_single_enterprise_envelope(self) -> ZeroEffectRuntimeBundle:
        if self.metadata.owner_ref is None:
            raise ValueError("ZeroEffectRuntimeBundle requires ownerRef")
        if self.metadata.governance_ref is None:
            raise ValueError("ZeroEffectRuntimeBundle requires governanceRef")
        refs = (
            (self.spec.snapshot_ref, "OrganizationSnapshot"),
            (self.spec.change_ref, "SemanticChangeSet"),
            (self.spec.subject_plan_ref, "OrganizationPlan"),
            (self.spec.certificate_ref, "PlanCertificate"),
            (self.spec.runtime_binding_ref, "RuntimeBinding"),
        )
        if any(ref.kind != expected_kind for ref, expected_kind in refs):
            raise ValueError("runtime bundle root kinds are inconsistent")
        if any(ref.namespace != self.metadata.namespace for ref, _ in refs):
            raise ValueError("runtime bundle roots must share metadata.namespace")
        expected_sources = tuple(ref.resource_id for ref, _ in refs)
        if self.metadata.source_refs != expected_sources:
            raise ValueError(
                "runtime bundle metadata.sourceRefs must be the ordered exact roots"
            )
        return self


class RuntimeLoweringReceiptSpec(StrictModel):
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    change_ref: ResourceRef = Field(alias="changeRef")
    subject_plan_ref: ResourceRef = Field(alias="subjectPlanRef")
    supplied_certificate_ref: ResourceRef = Field(alias="suppliedCertificateRef")
    recomputed_certificate_ref: ResourceRef = Field(alias="recomputedCertificateRef")
    runtime_binding_ref: ResourceRef = Field(alias="runtimeBindingRef")
    status: LoweringStatus
    reason_codes: tuple[ReasonCode, ...] = Field(alias="reasonCodes")
    bundle_ref: ResourceRef | None = Field(
        default=None, alias="bundleRef", exclude_if=lambda value: value is None
    )
    proof_scope: Literal["lowering_only"] = Field(alias="proofScope")
    runtime_invoked: Literal[False] = Field(alias="runtimeInvoked")
    runtime_admission_performed: Literal[False] = Field(alias="runtimeAdmissionPerformed")
    target_writes: Literal[0] = Field(alias="targetWrites")

    @model_validator(mode="after")
    def status_matches_bundle(self) -> RuntimeLoweringReceiptSpec:
        if self.reason_codes != tuple(sorted(set(self.reason_codes), key=str.encode)):
            raise ValueError("lowering reasonCodes must be sorted and unique")
        if self.status is LoweringStatus.PRODUCED:
            if self.bundle_ref is None or self.reason_codes:
                raise ValueError("PRODUCED requires bundleRef and no reasonCodes")
        elif self.bundle_ref is not None or not self.reason_codes:
            raise ValueError("BLOCKED forbids bundleRef and requires reasonCodes")
        return self


class RuntimeLoweringReceipt(ResourceBase):
    kind: Literal["RuntimeLoweringReceipt"] = "RuntimeLoweringReceipt"
    spec: RuntimeLoweringReceiptSpec

    @model_validator(mode="after")
    def roots_match_single_enterprise_envelope(self) -> RuntimeLoweringReceipt:
        if self.metadata.owner_ref is None:
            raise ValueError("RuntimeLoweringReceipt requires ownerRef")
        if self.metadata.governance_ref is None:
            raise ValueError("RuntimeLoweringReceipt requires governanceRef")
        refs = (
            (self.spec.snapshot_ref, "OrganizationSnapshot"),
            (self.spec.change_ref, "SemanticChangeSet"),
            (self.spec.subject_plan_ref, "OrganizationPlan"),
            (self.spec.supplied_certificate_ref, "PlanCertificate"),
            (self.spec.recomputed_certificate_ref, "PlanCertificate"),
            (self.spec.runtime_binding_ref, "RuntimeBinding"),
        )
        if self.spec.bundle_ref is not None and self.spec.bundle_ref.kind != (
            "ZeroEffectRuntimeBundle"
        ):
            raise ValueError("PRODUCED bundleRef must reference ZeroEffectRuntimeBundle")
        if any(ref.kind != expected_kind for ref, expected_kind in refs):
            raise ValueError("runtime lowering receipt root kinds are inconsistent")
        if any(ref.namespace != self.metadata.namespace for ref, _ in refs) or (
            self.spec.bundle_ref is not None
            and self.spec.bundle_ref.namespace != self.metadata.namespace
        ):
            raise ValueError("runtime lowering receipt roots must share metadata.namespace")
        expected_sources = tuple(ref.resource_id for ref, _ in refs)
        if self.metadata.source_refs != expected_sources:
            raise ValueError(
                "runtime lowering receipt metadata.sourceRefs must be the ordered exact roots"
            )
        return self


class CandidateConstraintSet(StrictModel):
    required_obligation_refs: tuple[Identifier, ...] = Field(
        default=(),
        alias="requiredObligationRefs",
        exclude_if=lambda value: not value,
    )
    required_obligation_types: tuple[Identifier, ...] = Field(
        default=(),
        alias="requiredObligationTypes",
        exclude_if=lambda value: not value,
    )
    admissible_role_refs: tuple[Identifier, ...] = Field(alias="admissibleRoleRefs")
    admissible_principal_refs: tuple[Identifier, ...] = Field(alias="admissiblePrincipalRefs")
    forbidden_combinations: tuple[tuple[Identifier, Identifier], ...] = Field(
        default=(), alias="forbiddenCombinations"
    )
    happens_before: tuple[OrderConstraint, ...] = Field(default=(), alias="happensBefore")
    evidence_duties: tuple[Identifier, ...] = Field(default=(), alias="evidenceDuties")
    acceptable_unknown_refs: tuple[Identifier, ...] = Field(
        default=(), alias="acceptableUnknownRefs"
    )
    minimality_level: Literal["none", "inclusion_minimal"] = Field(alias="minimalityLevel")
    acceptable_verdicts: tuple[Verdict, ...] = Field(alias="acceptableVerdicts", min_length=1)


class FieldAnnotation(StrictModel):
    field: Identifier
    value: Identifier
    evidence_refs: tuple[Identifier, ...] = Field(alias="evidenceRefs")
    status: Literal["source_gt", "oac_candidate", "unknown", "contested"]


class OrgChangeCaseSpec(StrictModel):
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    change_ref: ResourceRef = Field(alias="changeRef")
    observation_boundary: Identifier = Field(alias="observationBoundary")
    annotation_status: Literal[
        "candidate",
        "exploratory",
        "contested",
        "retracted",
    ] = Field(alias="annotationStatus")
    annotations: tuple[FieldAnnotation, ...]
    constraint_set: CandidateConstraintSet | None = Field(default=None, alias="constraintSet")
    witness_refs: tuple[ResourceRef, ...] = Field(default=(), alias="witnessRefs")
    mutation_family: Identifier = Field(alias="mutationFamily")
    outcome_observability: Literal["none", "structural", "executable"] = Field(
        alias="outcomeObservability"
    )

    @model_validator(mode="after")
    def exploratory_oac_labels_are_candidates(self) -> OrgChangeCaseSpec:
        if self.annotation_status in {"candidate", "exploratory"}:
            for annotation in self.annotations:
                if annotation.status == "source_gt" and annotation.field.startswith("oac."):
                    raise ValueError("exploratory OAC labels cannot be marked source_gt")
        return self


class OrgChangeCase(ResourceBase):
    kind: Literal["OrgChangeCase"] = "OrgChangeCase"
    spec: OrgChangeCaseSpec


class AdmissionVerdict(StrEnum):
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class OutcomeVerdict(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    PROVISIONAL = "PROVISIONAL"
    UNKNOWN = "UNKNOWN"


class OrganizationalDemandSpec(StrictModel):
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    requester_principal_ref: Identifier = Field(alias="requesterPrincipalRef")
    accountable_role_ref: Identifier = Field(alias="accountableRoleRef")
    objective: Identifier
    subject_refs: tuple[ResourceRef, ...] = Field(alias="subjectRefs", min_length=1)
    trigger_refs: tuple[ResourceRef, ...] = Field(alias="triggerRefs", min_length=1)
    desired_outcome_refs: tuple[ResourceRef, ...] = Field(
        alias="desiredOutcomeRefs", min_length=1
    )
    evidence_obligation_refs: tuple[ResourceRef, ...] = Field(
        alias="evidenceObligationRefs", min_length=1
    )
    constraint_refs: tuple[ResourceRef, ...] = Field(default=(), alias="constraintRefs")
    priority: PositiveInt = 1
    effect_ceiling: EffectCeiling = Field(
        default=EffectCeiling.ZERO_EFFECT, alias="effectCeiling"
    )


class OrganizationalDemand(ResourceBase):
    kind: Literal["OrganizationalDemand"] = "OrganizationalDemand"
    spec: OrganizationalDemandSpec


class SourceAdmissionReceiptSpec(StrictModel):
    admission_purpose: Literal["enterprise_intake", "successor_promotion"] = Field(
        alias="admissionPurpose"
    )
    subject_refs: tuple[ResourceRef, ...] = Field(alias="subjectRefs", min_length=1)
    intake_manifest_digest: Digest = Field(alias="intakeManifestDigest")
    intake_profile_ref: ResourceRef = Field(alias="intakeProfileRef")
    rule_set_digest: Digest = Field(alias="ruleSetDigest")
    proposer_refs: tuple[ResourceRef, ...] = Field(alias="proposerRefs", min_length=1)
    decision_authority_ref: ResourceRef = Field(alias="decisionAuthorityRef")
    reviewer_refs: tuple[ResourceRef, ...] = Field(alias="reviewerRefs", min_length=1)
    verdict: AdmissionVerdict
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    unresolved_refs: tuple[ResourceRef, ...] = Field(default=(), alias="unresolvedRefs")
    admitted_subject_refs: tuple[ResourceRef, ...] = Field(
        default=(), alias="admittedSubjectRefs"
    )
    predecessor_source_root: Digest | None = Field(
        default=None, alias="predecessorSourceRoot"
    )
    evolution_root: Digest | None = Field(default=None, alias="evolutionRoot")
    candidate_ref: ResourceRef | None = Field(default=None, alias="candidateRef")
    governance_decision_ref: ResourceRef | None = Field(
        default=None, alias="governanceDecisionRef"
    )


class SourceAdmissionReceipt(ResourceBase):
    kind: Literal["SourceAdmissionReceipt"] = "SourceAdmissionReceipt"
    spec: SourceAdmissionReceiptSpec


class OutcomeDimension(StrictModel):
    name: Identifier
    verdict: DimensionVerdict
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[ResourceRef, ...] = Field(default=(), alias="evidenceRefs")
    unresolved_refs: tuple[ResourceRef, ...] = Field(default=(), alias="unresolvedRefs")


class OutcomeProfileBinding(StrictModel):
    profile_id: Identifier = Field(alias="profileId")
    profile_version: Identifier = Field(alias="profileVersion")
    profile_digest: Digest = Field(alias="profileDigest")


class OutcomeCertificateSpec(StrictModel):
    profile_binding: OutcomeProfileBinding | None = Field(
        default=None, alias="profileBinding", exclude_if=lambda value: value is None
    )
    execution_authorization_ref: ResourceRef | None = Field(
        default=None, alias="executionAuthorizationRef", exclude_if=lambda value: value is None
    )
    source_root: Digest = Field(alias="sourceRoot")
    demand_root: Digest = Field(alias="demandRoot")
    plan_root: Digest = Field(alias="planRoot")
    execution_root: Digest = Field(alias="executionRoot")
    snapshot_ref: ResourceRef = Field(alias="snapshotRef")
    source_admission_receipt_refs: tuple[ResourceRef, ...] = Field(
        alias="sourceAdmissionReceiptRefs", min_length=1
    )
    demand_ref: ResourceRef = Field(alias="demandRef")
    change_refs: tuple[ResourceRef, ...] = Field(alias="changeRefs", min_length=1)
    plan_ref: ResourceRef = Field(alias="planRef")
    plan_certificate_ref: ResourceRef = Field(alias="planCertificateRef")
    runtime_binding_ref: ResourceRef = Field(alias="runtimeBindingRef")
    runtime_bundle_ref: ResourceRef = Field(alias="runtimeBundleRef")
    execution_receipt_ref: ResourceRef = Field(alias="executionReceiptRef")
    execution_evidence_refs: tuple[ResourceRef, ...] = Field(
        alias="executionEvidenceRefs", min_length=1
    )
    observation_refs: tuple[ResourceRef, ...] = Field(alias="observationRefs", min_length=1)
    acting_principal_refs: tuple[ResourceRef, ...] = Field(
        alias="actingPrincipalRefs", min_length=1
    )
    oracle_ref: ResourceRef = Field(alias="oracleRef")
    oracle_build_digest: Digest = Field(alias="oracleBuildDigest")
    observation_profile_ref: ResourceRef = Field(alias="observationProfileRef")
    verdict: OutcomeVerdict
    dimensions: tuple[OutcomeDimension, ...] = Field(min_length=1)
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[ResourceRef, ...] = Field(alias="evidenceRefs", min_length=1)
    unresolved_refs: tuple[ResourceRef, ...] = Field(default=(), alias="unresolvedRefs")


class OutcomeCertificate(ResourceBase):
    kind: Literal["OutcomeCertificate"] = "OutcomeCertificate"
    spec: OutcomeCertificateSpec


type Resource = (
    OrganizationSnapshot
    | SemanticChangeSet
    | OrganizationPlan
    | PlanCertificate
    | RuntimeBinding
    | ZeroEffectRuntimeBundle
    | RuntimeLoweringReceipt
    | OrgChangeCase
    | OrganizationalDemand
    | SourceAdmissionReceipt
    | OutcomeCertificate
)


def _unique_ids(label: str, values: Iterable[str]) -> set[str]:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"duplicate {label} id")
    return set(materialized)
