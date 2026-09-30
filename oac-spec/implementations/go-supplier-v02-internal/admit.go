package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

type validateResourceInput struct {
	ExpectedKind string `json:"expectedKind"`
	RawBase64    string `json:"rawBase64"`
}

type validateResourceResult struct {
	Kind           string `json:"kind"`
	ResourceID     string `json:"resourceId"`
	ResourceDigest string `json:"resourceDigest"`
}

type admittedResource struct {
	Kind     string
	ID       string
	Digest   string
	Snapshot *organizationSnapshot
	Change   *semanticChangeSet
}

type admissionError struct {
	code   string
	detail string
}

func (e *admissionError) Error() string { return e.detail }

func admissionFailure(code, detail string) error {
	return &admissionError{code: code, detail: detail}
}

func validateResourceOperation(payload json.RawMessage) (any, *operationError) {
	keys := []string{"expectedKind", "rawBase64"}
	if err := requireObjectKeys(payload, keys, keys); err != nil {
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	var input validateResourceInput
	if err := decodeClosed(payload, &input); err != nil || !oneOf(input.ExpectedKind, "OrganizationSnapshot", "SemanticChangeSet", "OrganizationPlan") || input.RawBase64 == "" {
		if err == nil {
			err = errors.New("invalid validateResource payload")
		}
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	raw, err := decodeCanonicalBase64(input.RawBase64)
	if err != nil {
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	admitted, err := admitResource(raw, input.ExpectedKind)
	if err != nil {
		return nil, admissionOperationError(err)
	}
	return validateResourceResult{Kind: admitted.Kind, ResourceID: admitted.ID, ResourceDigest: admitted.Digest}, nil
}

func decodeCanonicalBase64(value string) ([]byte, error) {
	raw, err := base64.StdEncoding.Strict().DecodeString(value)
	if err != nil || base64.StdEncoding.EncodeToString(raw) != value {
		return nil, errors.New("rawBase64 is not canonical strict Base64")
	}
	return raw, nil
}

func admissionOperationError(err error) *operationError {
	var failure *admissionError
	if errors.As(err, &failure) {
		status := statusError
		if failure.code == codeResourceExceeded {
			status = statusResourceExhausted
		}
		return opError(status, failure.code, errors.New(failure.detail))
	}
	return opError(statusError, codeCoreSchemaInvalid, err)
}

func admitResource(raw []byte, expectedKind string) (*admittedResource, error) {
	limit := maxResourceBytes
	if expectedKind == "OrganizationPlan" {
		limit = maxPlanBytes
	}
	if len(raw) > limit {
		return nil, admissionFailure(codeResourceExceeded, "decoded resource exceeds byte ceiling")
	}
	root, err := parseJSON(raw)
	if err != nil {
		var depth *jsonDepthError
		if errors.As(err, &depth) {
			return nil, admissionFailure(codeResourceExceeded, err.Error())
		}
		var domain *jsonDomainError
		if errors.As(err, &domain) {
			return nil, admissionFailure(codeNonIJSON, err.Error())
		}
		return nil, admissionFailure(codeCoreSchemaInvalid, err.Error())
	}
	if root.kind != kindObject {
		return nil, admissionFailure(codeCoreSchemaInvalid, "resource root must be an object")
	}
	api, ok := objectString(root, "apiVersion")
	if !ok || api != "oac.dev/v0alpha1" {
		return nil, admissionFailure(codeCoreSchemaInvalid, "explicit apiVersion is required")
	}
	kind, ok := objectString(root, "kind")
	if !ok {
		return nil, admissionFailure(codeCoreSchemaInvalid, "explicit string kind is required")
	}
	if !oneOf(kind, "OrganizationSnapshot", "SemanticChangeSet", "OrganizationPlan") {
		return nil, admissionFailure(codeCoreKindUnknown, "resource kind is not implemented by this capsule")
	}
	if kind != expectedKind {
		return nil, admissionFailure(codeCoreSchemaInvalid, "kind does not match expectedKind")
	}
	claimed, ok := objectString(root, "digest")
	if !ok || !validDigest(claimed) {
		return nil, admissionFailure(codeCoreSchemaInvalid, "explicit lowercase SHA-256 digest is required")
	}
	detached := jsonValue{kind: kindObject, object: make([]jsonMember, 0, len(root.object)-1)}
	for _, member := range root.object {
		if member.key != "digest" {
			detached.object = append(detached.object, member)
		}
	}
	digest := sha256.Sum256(canonicalBytes(detached))
	computed := "sha256:" + hex.EncodeToString(digest[:])
	if claimed != computed {
		return nil, admissionFailure(codeRootDigestMismatch, "claimed digest does not bind the raw decoded map")
	}
	if rawOwnedStringsContainBlank(root) {
		return nil, admissionFailure(codeCoreSchemaInvalid, "Identifier/NonBlank field is blank")
	}
	if rawTypedStringsRequireTrimming(root) {
		return nil, admissionFailure(codeCanonicalMismatch, "typed string admission would trim the raw decoded map")
	}

	admitted := &admittedResource{Kind: kind, Digest: claimed}
	switch kind {
	case "OrganizationSnapshot":
		var resource organizationSnapshot
		if err := decodeClosed(raw, &resource); err != nil {
			return nil, admissionFailure(codeCoreSchemaInvalid, err.Error())
		}
		resource.rawRoot, resource.rawBytes = root, append([]byte(nil), raw...)
		if err := validateSnapshot(&resource); err != nil {
			var preserved *admissionError
			if errors.As(err, &preserved) {
				return nil, preserved
			}
			return nil, admissionFailure(codeCoreSchemaInvalid, err.Error())
		}
		admitted.ID, admitted.Snapshot = resource.Metadata.ID, &resource
	case "SemanticChangeSet":
		var resource semanticChangeSet
		if err := decodeClosed(raw, &resource); err != nil {
			return nil, admissionFailure(codeCoreSchemaInvalid, err.Error())
		}
		for i := range resource.Spec.Deltas {
			normalizeObservedBinary64(&resource.Spec.Deltas[i].Before)
			normalizeObservedBinary64(&resource.Spec.Deltas[i].After)
		}
		resource.rawRoot, resource.rawBytes = root, append([]byte(nil), raw...)
		if err := validateChange(&resource); err != nil {
			return nil, admissionFailure(codeCoreSchemaInvalid, err.Error())
		}
		admitted.ID, admitted.Change = resource.Metadata.ID, &resource
	case "OrganizationPlan":
		id, err := validatePlanResource(raw)
		if err != nil {
			return nil, admissionFailure(codeCoreSchemaInvalid, err.Error())
		}
		admitted.ID = id
	default:
		return nil, admissionFailure(codeCoreSchemaInvalid, "kind is outside the capsule")
	}
	return admitted, nil
}

func objectString(v jsonValue, key string) (string, bool) {
	for _, member := range v.object {
		if member.key == key && member.value.kind == kindString {
			return member.value.string, true
		}
	}
	return "", false
}

var normalizationOwnedStringFields = map[string]struct{}{
	"id": {}, "namespace": {}, "ownerRef": {}, "governanceRef": {}, "sourceRefs": {},
	"nodeId": {}, "nodeType": {}, "domainRef": {}, "ownerRoleRef": {}, "roleId": {},
	"mission": {}, "responsibilityTypes": {}, "requiredQualifications": {}, "principalId": {},
	"eligibleRoleRefs": {}, "qualificationRefs": {}, "edgeId": {}, "sourceRef": {}, "targetRef": {},
	"semanticType": {}, "subjectRefs": {}, "nodeTypes": {}, "domainRefs": {}, "refs": {},
	"ruleId": {}, "requiredRoleRef": {}, "obligationType": {}, "requiredEvidence": {},
	"prerequisiteObligationTypes": {}, "dutyId": {}, "constraintId": {}, "leftRoleRef": {},
	"rightRoleRef": {}, "coveredNodeRefs": {}, "knownGaps": {}, "discoveryRoleRef": {},
	"discoveryTargetRef": {}, "discoveryObligationType": {}, "discoveryEvidence": {},
	"demandRef": {}, "subjectRef": {}, "scopeRefs": {}, "reason": {}, "compilerId": {},
	"compilerVersion": {}, "resourceId": {}, "evaluationId": {}, "predicateVersion": {},
	"witnessRefs": {}, "pathId": {}, "edgeRefs": {}, "ruleRefs": {}, "evaluationRefs": {},
	"dutyRefs": {}, "obligationId": {}, "pathRefs": {}, "roleInstanceId": {},
	"roleDefinitionRef": {}, "principalRef": {}, "obligationRefs": {}, "workUnitId": {},
	"roleInstanceRefs": {}, "accountableRoleInstanceRef": {}, "evidenceOutputs": {},
	"predecessorRef": {}, "successorRef": {}, "reasonRefs": {}, "decisionId": {},
	"consideredRoleRefs": {}, "consideredPrincipalRefs": {}, "nonRemovableRefs": {}, "unresolvedRefs": {},
}

func rawTypedStringsRequireTrimming(value jsonValue) bool {
	switch value.kind {
	case kindArray:
		for _, item := range value.array {
			if rawTypedStringsRequireTrimming(item) {
				return true
			}
		}
	case kindObject:
		for _, member := range value.object {
			if _, owned := normalizationOwnedStringFields[member.key]; owned && ownedStringNeedsTrimming(member.value) {
				return true
			}
			if rawTypedStringsRequireTrimming(member.value) {
				return true
			}
		}
	}
	return false
}

func rawOwnedStringsContainBlank(value jsonValue) bool {
	switch value.kind {
	case kindArray:
		for _, item := range value.array {
			if rawOwnedStringsContainBlank(item) {
				return true
			}
		}
	case kindObject:
		for _, member := range value.object {
			if _, owned := normalizationOwnedStringFields[member.key]; owned && ownedStringIsBlank(member.value) {
				return true
			}
			if rawOwnedStringsContainBlank(member.value) {
				return true
			}
		}
	}
	return false
}

func ownedStringIsBlank(value jsonValue) bool {
	if value.kind == kindString {
		if value.string == "" {
			return true
		}
		for _, current := range value.string {
			if !frozenWhiteSpace(current) {
				return false
			}
		}
		return true
	}
	if value.kind == kindArray {
		for _, item := range value.array {
			if ownedStringIsBlank(item) {
				return true
			}
		}
	}
	return false
}

func ownedStringNeedsTrimming(value jsonValue) bool {
	if value.kind == kindString {
		if value.string == "" {
			return false
		}
		first, _ := utf8.DecodeRuneInString(value.string)
		last, _ := utf8.DecodeLastRuneInString(value.string)
		return frozenWhiteSpace(first) || frozenWhiteSpace(last)
	}
	if value.kind == kindArray {
		for _, item := range value.array {
			if ownedStringNeedsTrimming(item) {
				return true
			}
		}
	}
	return false
}

func (value *binary64Integer) UnmarshalJSON(raw []byte) error {
	parsed, err := parseJSON(raw)
	if err != nil {
		return err
	}
	if parsed.kind != kindNumber || math.Trunc(parsed.number) != parsed.number || parsed.number >= math.Exp2(63) || parsed.number < -math.Exp2(63) {
		return errors.New("expected finite integral binary64 number")
	}
	*value = binary64Integer(int64(parsed.number))
	return nil
}

func frozenWhiteSpace(value rune) bool {
	switch {
	case value >= '\u0009' && value <= '\u000d':
		return true
	case value == '\u0020', value == '\u0085', value == '\u00a0', value == '\u1680':
		return true
	case value >= '\u2000' && value <= '\u200a':
		return true
	case value == '\u2028', value == '\u2029', value == '\u202f', value == '\u205f', value == '\u3000':
		return true
	default:
		return false
	}
}

func validateSnapshot(snapshot *organizationSnapshot) error {
	if snapshot.APIVersion != "oac.dev/v0alpha1" || snapshot.Kind != "OrganizationSnapshot" || !validDigest(snapshot.Digest) {
		return errors.New("invalid OrganizationSnapshot envelope")
	}
	if err := validateMetadata(snapshot.Metadata, true); err != nil {
		return err
	}
	spec := &snapshot.Spec
	if len(spec.Nodes) == 0 || len(spec.RoleDefinitions) == 0 || len(spec.Principals) == 0 {
		return errors.New("nodes, roleDefinitions, and principals must be non-empty")
	}
	if len(spec.Nodes) > maxNodes || len(spec.DependencyEdges) > maxEdges || len(spec.ImpactRules) > maxRules || len(spec.UnknownTransitionDuties) > maxDuties {
		return admissionFailure(codeResourceExceeded, "snapshot semantic collection ceiling exceeded")
	}
	snapshot.nodeByID = make(map[string]*orgNode, len(spec.Nodes))
	snapshot.roleByID = make(map[string]*roleDef, len(spec.RoleDefinitions))
	snapshot.edgeByID = make(map[string]*depEdge, len(spec.DependencyEdges))
	snapshot.ruleByID = make(map[string]*rule, len(spec.ImpactRules))
	snapshot.dutyByID = make(map[string]*duty, len(spec.UnknownTransitionDuties))
	snapshot.nodeIndex = make(map[string]int, len(spec.Nodes))

	for i := range spec.Nodes {
		node := &spec.Nodes[i]
		defaultAdmission(&node.AdmissionStatus)
		if !validID(node.NodeID) || !validID(node.NodeType) || !validID(node.DomainRef) || !validAdmission(node.AdmissionStatus) || !validOptionalID(node.OwnerRoleRef) {
			return fmt.Errorf("invalid node at index %d", i)
		}
		if _, exists := snapshot.nodeByID[node.NodeID]; exists {
			return errors.New("duplicate nodeId")
		}
		snapshot.nodeByID[node.NodeID], snapshot.nodeIndex[node.NodeID] = node, i
	}
	nodeDomains := map[string]struct{}{}
	for _, node := range spec.Nodes {
		nodeDomains[node.DomainRef] = struct{}{}
	}
	for i := range spec.RoleDefinitions {
		role := &spec.RoleDefinitions[i]
		defaultAdmission(&role.AdmissionStatus)
		if role.EffectCeiling == "" {
			role.EffectCeiling = "zero_effect"
		}
		if !validID(role.RoleID) || !validID(role.DomainRef) || !validID(role.Mission) || role.EffectCeiling != "zero_effect" || !validAdmission(role.AdmissionStatus) ||
			!validStringSet(role.ResponsibilityTypes, false) || !validStringSet(role.RequiredQualifications, true) {
			return fmt.Errorf("invalid roleDefinition at index %d", i)
		}
		if _, exists := snapshot.roleByID[role.RoleID]; exists {
			return errors.New("duplicate roleId")
		}
		if _, represented := nodeDomains[role.DomainRef]; !represented {
			return errors.New("role domainRef is not represented by a snapshot node")
		}
		snapshot.roleByID[role.RoleID] = role
	}
	principalIDs := map[string]struct{}{}
	for i := range spec.Principals {
		principal := &spec.Principals[i]
		defaultAdmission(&principal.AdmissionStatus)
		if !validID(principal.PrincipalID) || !oneOf(principal.PrincipalType, "human", "agent", "service", "team") || !oneOf(principal.Status, "active", "inactive") ||
			!validAdmission(principal.AdmissionStatus) || !validStringSet(principal.EligibleRoleRefs, false) || !validStringSet(principal.QualificationRefs, true) {
			return fmt.Errorf("invalid principal at index %d", i)
		}
		for _, roleID := range principal.EligibleRoleRefs {
			if snapshot.roleByID[roleID] == nil {
				return errors.New("principal eligibleRoleRef does not resolve")
			}
		}
		if _, duplicate := principalIDs[principal.PrincipalID]; duplicate {
			return errors.New("duplicate principalId")
		}
		principalIDs[principal.PrincipalID] = struct{}{}
	}
	if err := parseSnapshotSources(snapshot); err != nil {
		return err
	}
	if err := validateCompleteness(snapshot); err != nil {
		return err
	}
	for _, node := range spec.Nodes {
		if node.OwnerRoleRef != nil && snapshot.roleByID[*node.OwnerRoleRef] == nil {
			return errors.New("node ownerRoleRef does not resolve")
		}
	}
	for _, edge := range spec.parsedEdges {
		if snapshot.nodeByID[edge.SourceRef] == nil || snapshot.nodeByID[edge.TargetRef] == nil {
			return errors.New("dependency edge endpoint does not resolve")
		}
	}
	for _, source := range appendRulesAndDuties(spec.parsedRules, spec.UnknownTransitionDuties) {
		if snapshot.nodeByID[source.target] == nil || snapshot.roleByID[source.role] == nil {
			return errors.New("rule or duty target/role does not resolve")
		}
	}
	constraintIDs := map[string]struct{}{}
	for _, constraint := range spec.SeparationConstraints {
		if !validID(constraint.ConstraintID) || constraint.LeftRoleRef == constraint.RightRoleRef || snapshot.roleByID[constraint.LeftRoleRef] == nil || snapshot.roleByID[constraint.RightRoleRef] == nil {
			return errors.New("invalid separation constraint")
		}
		if _, duplicate := constraintIDs[constraint.ConstraintID]; duplicate {
			return errors.New("duplicate separation constraint ID")
		}
		constraintIDs[constraint.ConstraintID] = struct{}{}
	}
	owner := ""
	if snapshot.Metadata.OwnerRef != nil {
		owner = *snapshot.Metadata.OwnerRef
	}
	if role := snapshot.roleByID[owner]; role == nil || role.AdmissionStatus != "admitted" {
		return errors.New("snapshot ownerRef must resolve to an admitted role")
	}
	return nil
}

type targetRole struct{ target, role string }

func appendRulesAndDuties(rules []rule, duties []duty) []targetRole {
	result := make([]targetRole, 0, len(rules)+len(duties))
	for _, item := range rules {
		result = append(result, targetRole{item.TargetRef, item.RequiredRoleRef})
	}
	for _, item := range duties {
		result = append(result, targetRole{item.TargetRef, item.RequiredRoleRef})
	}
	return result
}

func parseSnapshotSources(snapshot *organizationSnapshot) error {
	spec := &snapshot.Spec
	allSourceIDs := map[string]struct{}{}
	for i, wire := range spec.DependencyEdges {
		if !validID(wire.EdgeID) || !validID(wire.SourceRef) || !validID(wire.TargetRef) || !validRelation(wire.RelationType) || !validAdmission(wire.AdmissionStatus) {
			return fmt.Errorf("invalid dependency edge at index %d", i)
		}
		predicate, err := parsePredicate(wire.TransferPredicate, true)
		if err != nil {
			return fmt.Errorf("edge %s: %w", wire.EdgeID, err)
		}
		if _, exists := allSourceIDs[wire.EdgeID]; exists {
			return errors.New("duplicate applicability source ID")
		}
		allSourceIDs[wire.EdgeID] = struct{}{}
		edge := depEdge{EdgeID: wire.EdgeID, SourceRef: wire.SourceRef, TargetRef: wire.TargetRef, RelationType: wire.RelationType, Predicate: predicate, AdmissionStatus: wire.AdmissionStatus, Index: i}
		spec.parsedEdges = append(spec.parsedEdges, edge)
		snapshot.edgeByID[edge.EdgeID] = &spec.parsedEdges[len(spec.parsedEdges)-1]
	}
	for i, raw := range spec.ImpactRules {
		rule, err := parseRule(raw, i)
		if err != nil {
			return err
		}
		if _, exists := allSourceIDs[rule.RuleID]; exists {
			return errors.New("duplicate applicability source ID")
		}
		allSourceIDs[rule.RuleID] = struct{}{}
		spec.parsedRules = append(spec.parsedRules, rule)
		snapshot.ruleByID[rule.RuleID] = &spec.parsedRules[len(spec.parsedRules)-1]
	}
	for i := range spec.UnknownTransitionDuties {
		duty := &spec.UnknownTransitionDuties[i]
		duty.Index = i
		if !validID(duty.DutyID) || !validID(duty.TargetRef) || !validID(duty.RequiredRoleRef) || !validID(duty.ObligationType) || !validAdmission(duty.AdmissionStatus) ||
			!validStringSetNonEmpty(duty.RequiredEvidence) || !validStringSet(duty.PrerequisiteObligationTypes, true) {
			return fmt.Errorf("invalid unknownTransitionDuty at index %d", i)
		}
		predicate, err := parsePredicate(duty.Applicability, false)
		if err != nil || predicate.Legacy {
			return fmt.Errorf("invalid duty predicate at index %d", i)
		}
		duty.Predicate = predicate
		if _, exists := allSourceIDs[duty.DutyID]; exists {
			return errors.New("duplicate applicability source ID")
		}
		allSourceIDs[duty.DutyID] = struct{}{}
		snapshot.dutyByID[duty.DutyID] = duty
	}
	return nil
}

func parseRule(raw json.RawMessage, index int) (rule, error) {
	var shape map[string]json.RawMessage
	if err := json.Unmarshal(raw, &shape); err != nil {
		return rule{}, fmt.Errorf("invalid impact rule at index %d", index)
	}
	if _, contextual := shape["applicability"]; contextual {
		var wire contextualRuleWire
		if err := decodeClosed(raw, &wire); err != nil {
			return rule{}, err
		}
		predicate, err := parsePredicate(wire.Applicability, false)
		if err != nil || predicate.Legacy {
			return rule{}, errors.New("invalid contextual rule predicate")
		}
		if !validRuleFields(wire.RuleID, wire.TargetRef, wire.RequiredRoleRef, wire.ObligationType, wire.RequiredEvidence, wire.PrerequisiteObligationTypes, wire.AdmissionStatus) {
			return rule{}, errors.New("invalid contextual impact rule")
		}
		return rule{RuleID: wire.RuleID, Predicate: predicate, TargetRef: wire.TargetRef, RequiredRoleRef: wire.RequiredRoleRef, ObligationType: wire.ObligationType, RequiredEvidence: wire.RequiredEvidence, PrerequisiteObligationTypes: wire.PrerequisiteObligationTypes, AdmissionStatus: wire.AdmissionStatus, Index: index}, nil
	}
	var wire legacyRuleWire
	if err := decodeClosed(raw, &wire); err != nil {
		return rule{}, err
	}
	if !validRuleFields(wire.RuleID, wire.TargetRef, wire.RequiredRoleRef, wire.ObligationType, wire.RequiredEvidence, nil, wire.AdmissionStatus) || !validID(wire.SemanticType) || !validStringSetNonEmpty(wire.AfterValues) {
		return rule{}, errors.New("invalid legacy impact rule")
	}
	return rule{RuleID: wire.RuleID, Predicate: predicate{PredicateVersion: "oac.supplier.transfer/v0alpha1", SemanticType: wire.SemanticType, AfterState: "known", AfterValues: wire.AfterValues, Legacy: true}, TargetRef: wire.TargetRef, RequiredRoleRef: wire.RequiredRoleRef, ObligationType: wire.ObligationType, RequiredEvidence: wire.RequiredEvidence, AdmissionStatus: wire.AdmissionStatus, Legacy: true, Index: index}, nil
}

func validRuleFields(id, target, role, obligation string, evidence, prerequisites []string, admission string) bool {
	return validID(id) && validID(target) && validID(role) && validID(obligation) && validStringSetNonEmpty(evidence) && validStringSet(prerequisites, true) && validAdmission(admission)
}

func parsePredicate(raw json.RawMessage, allowLegacy bool) (predicate, error) {
	var shape map[string]json.RawMessage
	if err := json.Unmarshal(raw, &shape); err != nil {
		return predicate{}, err
	}
	if _, contextual := shape["predicateVersion"]; !contextual {
		if !allowLegacy {
			return predicate{}, errors.New("contextual predicate required")
		}
		var wire legacyPredicateWire
		if err := decodeClosed(raw, &wire); err != nil || !validID(wire.SemanticType) || !validStringSetNonEmpty(wire.AfterValues) {
			return predicate{}, errors.New("invalid legacy transfer predicate")
		}
		return predicate{PredicateVersion: "oac.supplier.transfer/v0alpha1", SemanticType: wire.SemanticType, AfterState: "known", AfterValues: wire.AfterValues, Legacy: true}, nil
	}
	var wire predicateWire
	if err := decodeClosed(raw, &wire); err != nil {
		return predicate{}, err
	}
	if wire.PredicateVersion != "oac.supplier.applicability/v0.2" || !validID(wire.SemanticType) || !oneOf(wire.AfterState, "known", "unknown", "not_observable", "not_applicable") ||
		!validStringSet(wire.AfterValues, true) || !validStringSet(wire.RelationTypes, true) {
		return predicate{}, errors.New("invalid contextual predicate")
	}
	for _, relation := range wire.RelationTypes {
		if !validRelation(relation) {
			return predicate{}, errors.New("invalid predicate relation type")
		}
	}
	if wire.SubjectSelector != nil && (!validStringSet(wire.SubjectSelector.SubjectRefs, true) || !validStringSet(wire.SubjectSelector.NodeTypes, true) || !validStringSet(wire.SubjectSelector.DomainRefs, true)) {
		return predicate{}, errors.New("invalid subject selector")
	}
	if wire.ScopeSelector != nil && (len(wire.ScopeSelector.Refs) == 0 || !validStringSet(wire.ScopeSelector.Refs, false) || !oneOf(wire.ScopeSelector.MatchMode, "all", "any") || !oneOf(wire.ScopeSelector.MissingBehavior, "false", "unknown")) {
		return predicate{}, errors.New("invalid scope selector")
	}
	return predicate{PredicateVersion: wire.PredicateVersion, SemanticType: wire.SemanticType, AfterState: wire.AfterState, AfterValues: wire.AfterValues, SubjectSelector: wire.SubjectSelector, ScopeSelector: wire.ScopeSelector, RelationTypes: wire.RelationTypes}, nil
}

func validateCompleteness(snapshot *organizationSnapshot) error {
	c := snapshot.Spec.Completeness
	if !oneOf(c.Status, "complete", "partial", "unknown") || c.MaxDepth <= 0 || c.MaxDepth > maxSemanticDepth || !validID(c.DiscoveryRoleRef) ||
		!validStringSet(c.CoveredNodeRefs, true) || !validStringSet(c.CoveredRelationTypes, true) || !validStringSet(c.KnownGaps, true) || !validStringSet(c.DiscoveryEvidence, true) {
		return errors.New("invalid completeness manifest")
	}
	if c.Status == "complete" && len(c.KnownGaps) != 0 {
		return errors.New("complete boundary cannot declare known gaps")
	}
	triple := 0
	if c.DiscoveryTargetRef != nil {
		triple++
	}
	if c.DiscoveryObligationType != nil {
		triple++
	}
	if len(c.DiscoveryEvidence) != 0 {
		triple++
	}
	if triple != 0 && triple != 3 {
		return errors.New("contextual discovery fields must be all present or all absent")
	}
	for _, node := range c.CoveredNodeRefs {
		if snapshot.nodeByID[node] == nil {
			return errors.New("coveredNodeRef does not resolve")
		}
	}
	for _, relation := range c.CoveredRelationTypes {
		if !validRelation(relation) {
			return errors.New("covered relation type is invalid")
		}
	}
	if role := snapshot.roleByID[c.DiscoveryRoleRef]; role == nil {
		return errors.New("discoveryRoleRef does not resolve")
	}
	if c.DiscoveryTargetRef != nil && snapshot.nodeByID[*c.DiscoveryTargetRef] == nil {
		return errors.New("discoveryTargetRef does not resolve")
	}
	return nil
}

func validateChange(change *semanticChangeSet) error {
	if change.APIVersion != "oac.dev/v0alpha1" || change.Kind != "SemanticChangeSet" || !validDigest(change.Digest) {
		return errors.New("invalid SemanticChangeSet envelope")
	}
	if err := validateMetadata(change.Metadata, true); err != nil {
		return err
	}
	spec := change.Spec
	if !validID(spec.DemandRef) || !validID(spec.SubjectRef) || !validID(spec.SemanticType) || !validID(spec.SourceRef) || !validID(spec.Reason) || !validAdmission(spec.AdmissionStatus) ||
		len(spec.Deltas) == 0 || len(spec.ScopeRefs) == 0 || !validStringSet(spec.ScopeRefs, false) || !validTimestamp(spec.ObservedAt) || !validTimestamp(spec.EffectiveAt) {
		return errors.New("invalid SemanticChangeSet spec")
	}
	if !contains(change.Metadata.SourceRefs, spec.SourceRef) {
		return errors.New("change sourceRef is not pinned by metadata.sourceRefs")
	}
	paths := map[string]struct{}{}
	for i, delta := range spec.Deltas {
		if delta.Path == "" || !strings.HasPrefix(delta.Path, "/") || !oneOf(delta.Operation, "add", "remove", "replace", "invalidate", "unknown_transition") || !validObserved(delta.Before) || !validObserved(delta.After) {
			return fmt.Errorf("invalid change delta at index %d", i)
		}
		if _, duplicate := paths[delta.Path]; duplicate {
			return errors.New("duplicate delta path")
		}
		paths[delta.Path] = struct{}{}
		if !operationMatches(delta) {
			return errors.New("change operation is inconsistent with value states")
		}
	}
	return nil
}

func validObserved(value observedValue) bool {
	if !oneOf(value.State, "known", "unknown", "not_observable", "not_applicable") {
		return false
	}
	if value.State == "known" {
		if len(value.Value) == 0 || string(value.Value) == "null" {
			return false
		}
		parsed, err := parseJSON(value.Value)
		if err != nil || !oneOfJSONKind(parsed.kind, kindString, kindNumber, kindBool) {
			return false
		}
		return parsed.kind != kindNumber || parsed.number == float64(int64(parsed.number))
	}
	return len(value.Value) == 0 || string(value.Value) == "null"
}

func normalizeObservedBinary64(value *observedValue) {
	if len(value.Value) == 0 {
		return
	}
	parsed, err := parseJSON(value.Value)
	if err == nil && parsed.kind == kindNumber {
		value.Value = json.RawMessage(canonicalBytes(parsed))
	}
}

func oneOfJSONKind(value jsonKind, choices ...jsonKind) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

func operationMatches(delta changeDelta) bool {
	before, after := delta.Before.State, delta.After.State
	switch delta.Operation {
	case "add":
		return before == "not_applicable" && after != "not_applicable"
	case "remove":
		return before != "not_applicable" && after == "not_applicable"
	case "replace":
		if before != "known" || after != "known" {
			return false
		}
		return string(delta.Before.Value) != string(delta.After.Value)
	case "invalidate":
		return before == "known" && after == "not_observable"
	case "unknown_transition":
		return (before == "known" || before == "not_observable") && after == "unknown"
	default:
		return false
	}
}

func validateMetadata(metadata resourceMetadata, authority bool) error {
	if !validID(metadata.ID) || !validID(metadata.Namespace) || metadata.Revision <= 0 || !validTimestamp(metadata.CreatedAt) ||
		!validOptionalTimestamp(metadata.EffectiveFrom) || !validOptionalTimestamp(metadata.EffectiveTo) || !validStringSet(metadata.SourceRefs, true) {
		return errors.New("invalid resource metadata")
	}
	if authority && (metadata.OwnerRef == nil || !validID(*metadata.OwnerRef) || metadata.GovernanceRef == nil || !validID(*metadata.GovernanceRef)) {
		return errors.New("authority envelope is incomplete")
	}
	if metadata.EffectiveFrom != nil && metadata.EffectiveTo != nil {
		from, _ := time.Parse(time.RFC3339Nano, *metadata.EffectiveFrom)
		to, _ := time.Parse(time.RFC3339Nano, *metadata.EffectiveTo)
		if !to.After(from) {
			return errors.New("effectiveTo must be after effectiveFrom")
		}
	}
	return nil
}

var utcTimestamp = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`)

func validTimestamp(value string) bool {
	if !utcTimestamp.MatchString(value) || strings.HasPrefix(value, "0000-") {
		return false
	}
	_, err := time.Parse(time.RFC3339Nano, value)
	return err == nil
}

func validOptionalTimestamp(value *string) bool { return value == nil || validTimestamp(*value) }

func validDigest(value string) bool {
	if len(value) != 71 || !strings.HasPrefix(value, "sha256:") {
		return false
	}
	for _, c := range value[7:] {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

func validID(value string) bool {
	return value != "" && utf8.ValidString(value) && utf8.RuneCountInString(value) <= 512
}

func validOptionalID(value *string) bool { return value == nil || validID(*value) }

func validAdmission(value string) bool {
	return oneOf(value, "admitted", "candidate", "disputed", "retracted")
}

func defaultAdmission(value *string) {
	if *value == "" {
		*value = "admitted"
	}
}

func validRelation(value string) bool {
	return oneOf(value, "business_dependency", "contractual_dependency", "financial_exposure", "compliance_dependency", "operational_dependency", "traceability", "similarity", "correlation")
}

func validStringSet(values []string, nilAllowed bool) bool {
	if values == nil && !nilAllowed {
		return false
	}
	seen := map[string]struct{}{}
	for _, value := range values {
		if !validID(value) {
			return false
		}
		if _, duplicate := seen[value]; duplicate {
			return false
		}
		seen[value] = struct{}{}
	}
	return true
}

func validStringSetNonEmpty(values []string) bool {
	return len(values) > 0 && validStringSet(values, false)
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func oneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

func validatePlanResource(raw []byte) (string, error) {
	var plan organizationPlan
	if err := decodeClosed(raw, &plan); err != nil {
		return "", err
	}
	if plan.APIVersion != "oac.dev/v0alpha1" || plan.Kind != "OrganizationPlan" || !validDigest(plan.Digest) {
		return "", errors.New("invalid OrganizationPlan envelope")
	}
	if err := validateMetadata(plan.Metadata, true); err != nil {
		return "", err
	}
	spec := plan.Spec
	if !oneOf(spec.Status, "planned", "guarded_unresolved") || spec.EffectCeiling != "zero_effect" || !validID(spec.CompilerID) || !validID(spec.CompilerVersion) ||
		spec.ImpactPaths == nil || spec.Obligations == nil || spec.RoleInstances == nil || spec.WorkUnits == nil || spec.HappensBefore == nil || spec.Decisions == nil || spec.UnresolvedRefs == nil {
		return "", errors.New("invalid OrganizationPlan spec")
	}
	if err := validateResourceRef(spec.SnapshotRef, "OrganizationSnapshot", plan.Metadata.Namespace); err != nil {
		return "", err
	}
	if err := validateResourceRef(spec.ChangeRef, "SemanticChangeSet", plan.Metadata.Namespace); err != nil {
		return "", err
	}
	if err := validatePlanCollections(&spec); err != nil {
		return "", err
	}
	return plan.Metadata.ID, nil
}

func validateResourceRef(ref resourceRef, expectedKind, namespace string) error {
	if ref.APIVersion != "" && ref.APIVersion != "oac.dev/v0alpha1" {
		return errors.New("invalid ResourceRef apiVersion")
	}
	if ref.Kind != expectedKind || ref.Namespace != namespace || !validID(ref.ResourceID) || ref.Revision <= 0 || !validDigest(ref.Digest) {
		return errors.New("invalid OrganizationPlan root reference")
	}
	return nil
}

func validatePlanCollections(spec *planSpec) error {
	evaluations := map[string]struct{}{}
	for _, evaluation := range spec.ApplicabilityEvaluations {
		if !validID(evaluation.EvaluationID) || !validID(evaluation.SourceRef) || !validID(evaluation.PredicateVersion) || !oneOf(evaluation.Result, "TRUE", "FALSE", "UNKNOWN") ||
			!validReasonSet(evaluation.ReasonCodes, false) || !validStringSetNonEmpty(evaluation.WitnessRefs) {
			return errors.New("invalid plan applicability evaluation")
		}
		if _, duplicate := evaluations[evaluation.EvaluationID]; duplicate {
			return errors.New("duplicate plan evaluationId")
		}
		evaluations[evaluation.EvaluationID] = struct{}{}
	}
	paths := map[string]struct{}{}
	for _, path := range spec.ImpactPaths {
		if !validID(path.PathID) || !validID(path.TargetRef) || !oneOf(path.State, "affected", "unaffected_proven", "unknown", "out_of_declared_scope") || !oneOf(path.Origin, "dependency", "rule", "gap", "bounded_non_impact", "excluded") ||
			!validStringSet(path.EdgeRefs, true) || !validStringSet(path.RuleRefs, true) || !validStringSet(path.EvaluationRefs, true) || !validStringSet(path.DutyRefs, true) || !validReasonSet(path.ReasonCodes, false) {
			return errors.New("invalid plan impact path")
		}
		if _, duplicate := paths[path.PathID]; duplicate {
			return errors.New("duplicate plan pathId")
		}
		paths[path.PathID] = struct{}{}
		for _, evaluationRef := range path.EvaluationRefs {
			if _, ok := evaluations[evaluationRef]; !ok {
				return errors.New("plan path evaluationRef does not resolve")
			}
		}
	}
	obligations := map[string]struct{}{}
	for _, obligation := range spec.Obligations {
		if !validID(obligation.ObligationID) || !oneOf(obligation.Origin, "dependency_path", "organizational_rule", "discovery_gap") || !validID(obligation.TargetRef) || !validID(obligation.DomainRef) ||
			!validID(obligation.RequiredRoleRef) || !validID(obligation.ObligationType) || !validStringSetNonEmpty(obligation.RequiredEvidence) || !validStringSetNonEmpty(obligation.PathRefs) || !oneOf(obligation.ResolutionState, "affected", "unknown", "unresolved") {
			return errors.New("invalid plan obligation")
		}
		if _, duplicate := obligations[obligation.ObligationID]; duplicate {
			return errors.New("duplicate plan obligationId")
		}
		obligations[obligation.ObligationID] = struct{}{}
	}
	roles := map[string]struct{}{}
	for _, role := range spec.RoleInstances {
		if !validID(role.RoleInstanceID) || !validID(role.RoleDefinitionRef) || !validID(role.PrincipalRef) || !validID(role.Mission) || !validStringSetNonEmpty(role.ObligationRefs) || role.QualificationRefs == nil || !validStringSet(role.QualificationRefs, false) || role.EffectCeiling != "zero_effect" {
			return errors.New("invalid plan RoleInstance")
		}
		if _, duplicate := roles[role.RoleInstanceID]; duplicate {
			return errors.New("duplicate roleInstanceId")
		}
		roles[role.RoleInstanceID] = struct{}{}
	}
	workUnits := map[string]struct{}{}
	for _, work := range spec.WorkUnits {
		if !validID(work.WorkUnitID) || !validStringSetNonEmpty(work.RoleInstanceRefs) || !validID(work.AccountableRoleInstanceRef) || !validStringSetNonEmpty(work.ObligationRefs) || !validStringSetNonEmpty(work.EvidenceOutputs) || work.EffectCeiling != "zero_effect" {
			return errors.New("invalid plan WorkUnit")
		}
		if _, duplicate := workUnits[work.WorkUnitID]; duplicate {
			return errors.New("duplicate workUnitId")
		}
		workUnits[work.WorkUnitID] = struct{}{}
	}
	for _, order := range spec.HappensBefore {
		if !validID(order.PredecessorRef) || !validID(order.SuccessorRef) || order.PredecessorRef == order.SuccessorRef || !validStringSetNonEmpty(order.ReasonRefs) || (order.Relation != "" && order.Relation != "must_complete_before") {
			return errors.New("invalid plan order constraint")
		}
	}
	decisions := map[string]struct{}{}
	for _, decision := range spec.Decisions {
		if !validID(decision.DecisionID) || !validID(decision.SubjectRef) || !validAdmission(decision.InputClass) || !oneOf(decision.Disposition, "included", "excluded", "unresolved") || !validReasonSet(decision.ReasonCodes, true) {
			return errors.New("invalid plan decision")
		}
		if _, duplicate := decisions[decision.DecisionID]; duplicate {
			return errors.New("duplicate plan decisionId")
		}
		decisions[decision.DecisionID] = struct{}{}
	}
	if !validStringSet(spec.UnresolvedRefs, false) || !oneOf(spec.Minimality.Level, "none", "inclusion_minimal") || spec.Minimality.ConsideredRoleRefs == nil || spec.Minimality.ConsideredPrincipalRefs == nil || spec.Minimality.NonRemovableRefs == nil ||
		!validStringSet(spec.Minimality.ConsideredRoleRefs, false) || !validStringSet(spec.Minimality.ConsideredPrincipalRefs, false) || !validStringSet(spec.Minimality.NonRemovableRefs, false) {
		return errors.New("invalid plan unresolved/minimality projection")
	}
	return nil
}

var reasonCodePattern = regexp.MustCompile(`^[A-Z][A-Z0-9_]{2,127}$`)

func validReasonSet(values []string, required bool) bool {
	if required && len(values) == 0 {
		return false
	}
	if values == nil {
		return !required
	}
	if !validStringSet(values, false) {
		return false
	}
	for _, value := range values {
		if !reasonCodePattern.MatchString(value) {
			return false
		}
	}
	return true
}

func sortedUnique(values []string) []string {
	seen := map[string]struct{}{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		if _, ok := seen[value]; !ok {
			seen[value] = struct{}{}
			result = append(result, value)
		}
	}
	sort.Strings(result)
	return result
}
