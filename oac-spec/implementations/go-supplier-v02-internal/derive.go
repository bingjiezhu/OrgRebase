package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
)

type deriveInput struct {
	SnapshotBase64 string `json:"snapshotBase64"`
	ChangeBase64   string `json:"changeBase64"`
}

type deriveResult struct {
	Report       profileDerivationReport `json:"report"`
	ReportDigest string                  `json:"reportDigest"`
}

func deriveOperation(payload json.RawMessage) (any, *operationError) {
	keys := []string{"snapshotBase64", "changeBase64"}
	if err := requireObjectKeys(payload, keys, keys); err != nil {
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	var input deriveInput
	if err := decodeClosed(payload, &input); err != nil || input.SnapshotBase64 == "" || input.ChangeBase64 == "" {
		if err == nil {
			err = errors.New("invalid derive payload")
		}
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	snapshotRaw, err := decodeCanonicalBase64(input.SnapshotBase64)
	if err != nil {
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	changeRaw, err := decodeCanonicalBase64(input.ChangeBase64)
	if err != nil {
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	snapshotAdmission, err := admitResource(snapshotRaw, "OrganizationSnapshot")
	if err != nil {
		return nil, admissionOperationError(err)
	}
	changeAdmission, err := admitResource(changeRaw, "SemanticChangeSet")
	if err != nil {
		return nil, admissionOperationError(err)
	}
	report, deriveErr := deriveProfile(snapshotAdmission.Snapshot, changeAdmission.Change)
	if deriveErr != nil {
		return nil, deriveErr
	}
	digest, err := reportDigest(report)
	if err != nil {
		return nil, opError(statusError, codeCoreSchemaInvalid, err)
	}
	return deriveResult{Report: report, ReportDigest: digest}, nil
}

type evalSource struct {
	ID        string
	Kind      string
	Index     int
	Admission string
	Predicate predicate
	Relation  *string
}

type truthAccumulator struct {
	falseAtom   bool
	unknownAtom bool
	reasons     []string
}

func (a *truthAccumulator) falseWith(code string) {
	a.falseAtom = true
	a.reasons = append(a.reasons, code)
}

func (a *truthAccumulator) unknownWith(code string) {
	a.unknownAtom = true
	a.reasons = append(a.reasons, code)
}

func (a truthAccumulator) result() string {
	if a.falseAtom {
		return "FALSE"
	}
	if a.unknownAtom {
		return "UNKNOWN"
	}
	return "TRUE"
}

type derivation struct {
	snapshot        *organizationSnapshot
	change          *semanticChangeSet
	evaluations     map[string]applicabilityEvaluation
	evalBySource    map[string]applicabilityEvaluation
	paths           map[string]impactPath
	pathAttempts    int
	obligationSeeds []obligationSeed
	unknownPaths    []impactPath
	dependencyPaths []impactPath
	totalWitnesses  int
	truncations     []truncationFrontier
}

type truncationFrontier struct {
	targetRef string
	nodes     []string
}

func deriveProfile(snapshot *organizationSnapshot, change *semanticChangeSet) (profileDerivationReport, *operationError) {
	rootUnknown, err := validateSupplierRoots(snapshot, change)
	if err != nil {
		return profileDerivationReport{}, err
	}
	d := &derivation{
		snapshot:     snapshot,
		change:       change,
		evaluations:  make(map[string]applicabilityEvaluation),
		evalBySource: make(map[string]applicabilityEvaluation),
		paths:        make(map[string]impactPath),
	}
	if err := d.evaluateAllSources(); err != nil {
		return profileDerivationReport{}, err
	}
	if err := d.validateFiringUnknownTransitionDuties(); err != nil {
		return profileDerivationReport{}, err
	}
	if err := d.materializeClosure(rootUnknown); err != nil {
		return profileDerivationReport{}, err
	}
	obligations, err := d.finalizeObligations()
	if err != nil {
		return profileDerivationReport{}, err
	}
	orders, err := d.finalizeOrders(obligations)
	if err != nil {
		return profileDerivationReport{}, err
	}

	evaluations := make([]applicabilityEvaluation, 0, len(d.evaluations))
	for _, evaluation := range d.evaluations {
		evaluations = append(evaluations, evaluation)
	}
	sort.Slice(evaluations, func(i, j int) bool { return evaluations[i].EvaluationID < evaluations[j].EvaluationID })
	paths := make([]impactPath, 0, len(d.paths))
	unresolved := make([]string, 0)
	for _, path := range d.paths {
		paths = append(paths, path)
		if path.State == "unknown" {
			unresolved = append(unresolved, path.PathID)
		}
	}
	sort.Slice(paths, func(i, j int) bool { return paths[i].PathID < paths[j].PathID })
	referencedEvaluations := make(map[string]struct{})
	for _, path := range paths {
		for _, evaluationRef := range path.EvaluationRefs {
			referencedEvaluations[evaluationRef] = struct{}{}
		}
	}
	for _, evaluation := range evaluations {
		if evaluation.Result == "UNKNOWN" {
			if _, referenced := referencedEvaluations[evaluation.EvaluationID]; !referenced {
				continue
			}
			unresolved = append(unresolved, evaluation.EvaluationID)
		}
	}
	if rootUnknown {
		unresolved = append(unresolved, change.Metadata.ID)
	}
	unresolved = sortedUnique(unresolved)
	return profileDerivationReport{
		APIVersion: "oac.derivation/v0alpha1", Kind: "ProfileDerivationReport",
		ProfileID: "oac.supplier.transfer", ProfileVersion: "v0.2",
		SnapshotDigest: snapshot.Digest, ChangeDigest: change.Digest,
		ApplicabilityEvaluations: evaluations, ImpactPaths: paths,
		Obligations: obligations, RequiredOrders: orders, UnresolvedRefs: unresolved,
		RootApplicabilityUnknown: rootUnknown,
	}, nil
}

func validateSupplierRoots(snapshot *organizationSnapshot, change *semanticChangeSet) (bool, *operationError) {
	if snapshot.Metadata.Namespace != change.Metadata.Namespace || snapshot.Metadata.GovernanceRef == nil || change.Metadata.GovernanceRef == nil || *snapshot.Metadata.GovernanceRef != *change.Metadata.GovernanceRef {
		return false, opError(statusError, codeResourceCoherence, errors.New("snapshot and change authority roots differ"))
	}
	if change.Spec.AdmissionStatus != "admitted" {
		return false, opError(statusError, codeChangeNotAdmitted, errors.New("Supplier Profile requires an admitted change"))
	}
	if change.Spec.SemanticType != "supplier.status" {
		return false, opError(statusError, codeUnsupportedSemantic, errors.New("semanticType is not supplier.status"))
	}
	if len(change.Spec.Deltas) != 1 || change.Spec.Deltas[0].Path != "/status" {
		return false, opError(statusError, codeDeltaSetInvalid, errors.New("exactly one /status delta is required"))
	}
	if !operationMatches(change.Spec.Deltas[0]) {
		return false, opError(statusError, codeOperationInvalid, errors.New("status delta operation is inconsistent"))
	}
	owner := ""
	if change.Metadata.OwnerRef != nil {
		owner = *change.Metadata.OwnerRef
	}
	if role := snapshot.roleByID[owner]; role == nil || role.AdmissionStatus != "admitted" {
		return false, opError(statusError, codeResourceCoherence, errors.New("change ownerRef is not an admitted snapshot role"))
	}
	subject := snapshot.nodeByID[change.Spec.SubjectRef]
	if subject == nil {
		return false, opError(statusError, codeResourceCoherence, errors.New("changed subject does not resolve in snapshot"))
	}
	if subject.AdmissionStatus == "retracted" {
		return false, opError(statusError, codeRetractedSource, errors.New("the change subject is a retracted source object"))
	}
	for _, scope := range change.Spec.ScopeRefs {
		if snapshot.nodeByID[scope] == nil {
			return false, opError(statusError, codeResourceCoherence, errors.New("change scopeRef does not resolve in snapshot"))
		}
	}
	after := change.Spec.Deltas[0].After
	rootUnknown := after.State == "unknown" || after.State == "not_observable" || subject.AdmissionStatus == "candidate" || subject.AdmissionStatus == "disputed"
	if after.State == "known" {
		value, ok := knownString(after)
		if !ok || value == "unknown" {
			return false, opError(statusError, codeUnsupportedSemantic, errors.New("known Supplier status must be a non-synthetic string"))
		}
	}
	return rootUnknown, nil
}

func knownString(value observedValue) (string, bool) {
	if value.State != "known" || len(value.Value) == 0 {
		return "", false
	}
	var text string
	if json.Unmarshal(value.Value, &text) != nil {
		return "", false
	}
	return text, true
}

func (d *derivation) evaluateAllSources() *operationError {
	for i := range d.snapshot.Spec.parsedEdges {
		edge := d.snapshot.Spec.parsedEdges[i]
		relation := edge.RelationType
		if err := d.addEvaluation(evalSource{ID: edge.EdgeID, Kind: "edge", Index: edge.Index, Admission: edge.AdmissionStatus, Predicate: edge.Predicate, Relation: &relation}); err != nil {
			return err
		}
	}
	for i := range d.snapshot.Spec.parsedRules {
		rule := d.snapshot.Spec.parsedRules[i]
		if err := d.addEvaluation(evalSource{ID: rule.RuleID, Kind: "rule", Index: rule.Index, Admission: rule.AdmissionStatus, Predicate: rule.Predicate}); err != nil {
			return err
		}
	}
	for i := range d.snapshot.Spec.UnknownTransitionDuties {
		duty := d.snapshot.Spec.UnknownTransitionDuties[i]
		if err := d.addEvaluation(evalSource{ID: duty.DutyID, Kind: "duty", Index: duty.Index, Admission: duty.AdmissionStatus, Predicate: duty.Predicate}); err != nil {
			return err
		}
	}
	return nil
}

// validateFiringUnknownTransitionDuties is deliberately run before path and
// prerequisite materialization. An admitted duty whose predicate fires Unknown
// but whose role lacks admitted authority is itself malformed; a later rule
// ordering error must not mask that primary defect.
func (d *derivation) validateFiringUnknownTransitionDuties() *operationError {
	duties := append([]duty(nil), d.snapshot.Spec.UnknownTransitionDuties...)
	sort.Slice(duties, func(i, j int) bool { return duties[i].DutyID < duties[j].DutyID })
	for _, duty := range duties {
		if duty.AdmissionStatus != "admitted" {
			continue
		}
		evaluation, ok := d.evalBySource[duty.DutyID]
		if !ok || evaluation.Result != "UNKNOWN" {
			continue
		}
		target := d.snapshot.nodeByID[duty.TargetRef]
		if target == nil || target.AdmissionStatus == "retracted" {
			continue
		}
		role := d.snapshot.roleByID[duty.RequiredRoleRef]
		if role == nil || role.AdmissionStatus != "admitted" {
			return opError(statusError, codeUnknownDutyInvalid, fmt.Errorf("unknown-transition duty lacks an admitted role: %s", duty.DutyID))
		}
	}
	return nil
}

func (d *derivation) addEvaluation(source evalSource) *operationError {
	if len(d.evaluations) >= maxEvaluations {
		return opError(statusResourceExhausted, codeResourceExceeded, errors.New("maxEvaluations exceeded"))
	}
	evaluation := d.evaluatePredicate(source)
	if len(evaluation.WitnessRefs) > maxEvalWitnesses || d.totalWitnesses+len(evaluation.WitnessRefs) > maxTotalWitness {
		return opError(statusResourceExhausted, codeResourceExceeded, errors.New("applicability witness ceiling exceeded"))
	}
	d.totalWitnesses += len(evaluation.WitnessRefs)
	d.evaluations[evaluation.EvaluationID] = evaluation
	d.evalBySource[source.ID] = evaluation
	return nil
}

func (d *derivation) evaluatePredicate(source evalSource) applicabilityEvaluation {
	predicate := source.Predicate
	change, snapshot := d.change, d.snapshot
	delta := change.Spec.Deltas[0]
	acc := truthAccumulator{}
	witnesses := []string{
		witness(change.Metadata.ID, "/digest"),
		witness(change.Metadata.ID, "/spec/admissionStatus"),
		witness(change.Metadata.ID, "/spec/deltas/0/after/state"),
		witness(change.Metadata.ID, "/spec/semanticType"),
		witness(change.Metadata.ID, "/spec/subjectRef"),
		witness(snapshot.Metadata.ID, "/digest"),
	}
	if len(delta.After.Value) != 0 && string(delta.After.Value) != "null" {
		witnesses = append(witnesses, witness(change.Metadata.ID, "/spec/deltas/0/after/value"))
	}
	if predicate.ScopeSelector != nil {
		witnesses = append(witnesses, witness(change.Metadata.ID, "/spec/scopeRefs"))
	}
	switch source.Kind {
	case "edge":
		base := fmt.Sprintf("/spec/dependencyEdges/%d", source.Index)
		witnesses = append(witnesses, witness(snapshot.Metadata.ID, base+"/admissionStatus"), witness(snapshot.Metadata.ID, base+"/relationType"), witness(snapshot.Metadata.ID, base+"/transferPredicate"))
	case "rule":
		base := fmt.Sprintf("/spec/impactRules/%d", source.Index)
		if predicate.Legacy {
			witnesses = append(witnesses, witness(snapshot.Metadata.ID, base))
		} else {
			witnesses = append(witnesses, witness(snapshot.Metadata.ID, base+"/admissionStatus"), witness(snapshot.Metadata.ID, base+"/applicability"))
		}
	case "duty":
		base := fmt.Sprintf("/spec/unknownTransitionDuties/%d", source.Index)
		witnesses = append(witnesses, witness(snapshot.Metadata.ID, base+"/admissionStatus"), witness(snapshot.Metadata.ID, base+"/applicability"))
	}

	if source.Admission == "candidate" || source.Admission == "disputed" {
		acc.unknownWith("CANDIDATE_INPUT_NOT_AUTHORITY")
	} else if source.Admission == "retracted" {
		acc.falseWith("RETRACTED_SOURCE_EXCLUDED")
	}
	if predicate.SemanticType != change.Spec.SemanticType {
		acc.falseWith("APPLICABILITY_SEMANTIC_TYPE_MISMATCH")
	}

	afterState := delta.After.State
	if afterState == "unknown" || afterState == "not_observable" {
		if predicate.AfterState != afterState {
			acc.unknownWith("APPLICABILITY_INPUT_UNKNOWN")
		}
	} else if predicate.AfterState != afterState {
		acc.falseWith("APPLICABILITY_AFTER_STATE_MISMATCH")
	}
	if afterState == "known" && predicate.AfterState == "known" && len(predicate.AfterValues) > 0 {
		value, ok := knownString(delta.After)
		if !ok {
			acc.unknownWith("APPLICABILITY_INPUT_UNKNOWN")
		} else if !contains(predicate.AfterValues, value) {
			acc.falseWith("APPLICABILITY_AFTER_VALUE_MISMATCH")
		}
	}

	if selector := predicate.SubjectSelector; selector != nil {
		if len(selector.SubjectRefs) > 0 && !contains(selector.SubjectRefs, change.Spec.SubjectRef) {
			acc.falseWith("APPLICABILITY_SUBJECT_MISMATCH")
		}
		node := snapshot.nodeByID[change.Spec.SubjectRef]
		if node == nil {
			witnesses = append(witnesses, witness(snapshot.Metadata.ID, "/spec/nodes"))
			acc.unknownWith("APPLICABILITY_INPUT_MISSING")
		} else {
			index := snapshot.nodeIndex[node.NodeID]
			base := fmt.Sprintf("/spec/nodes/%d", index)
			witnesses = append(witnesses, witness(snapshot.Metadata.ID, base+"/admissionStatus"))
			if node.AdmissionStatus == "candidate" || node.AdmissionStatus == "disputed" {
				acc.unknownWith("CANDIDATE_INPUT_NOT_AUTHORITY")
			} else if node.AdmissionStatus == "retracted" {
				acc.falseWith("RETRACTED_SOURCE_EXCLUDED")
			} else {
				if len(selector.NodeTypes) > 0 {
					witnesses = append(witnesses, witness(snapshot.Metadata.ID, base+"/nodeType"))
					if !contains(selector.NodeTypes, node.NodeType) {
						acc.falseWith("APPLICABILITY_SUBJECT_MISMATCH")
					}
				}
				if len(selector.DomainRefs) > 0 {
					witnesses = append(witnesses, witness(snapshot.Metadata.ID, base+"/domainRef"))
					if !contains(selector.DomainRefs, node.DomainRef) {
						acc.falseWith("APPLICABILITY_SUBJECT_MISMATCH")
					}
				}
			}
		}
	}

	if selector := predicate.ScopeSelector; selector != nil {
		scope := truthAccumulator{}
		hasTrue := false
		missing := func() {
			if selector.MissingBehavior == "false" {
				scope.falseWith("APPLICABILITY_SCOPE_MISMATCH")
			} else {
				scope.unknownWith("APPLICABILITY_INPUT_MISSING")
			}
		}
		for _, ref := range selector.Refs {
			if !contains(change.Spec.ScopeRefs, ref) {
				missing()
				continue
			}
			node := snapshot.nodeByID[ref]
			if node == nil {
				witnesses = append(witnesses, witness(snapshot.Metadata.ID, "/spec/nodes"))
				missing()
				continue
			}
			base := fmt.Sprintf("/spec/nodes/%d/admissionStatus", snapshot.nodeIndex[ref])
			witnesses = append(witnesses, witness(snapshot.Metadata.ID, base))
			switch node.AdmissionStatus {
			case "admitted":
				hasTrue = true
			case "candidate", "disputed":
				scope.unknownWith("CANDIDATE_INPUT_NOT_AUTHORITY")
			case "retracted":
				scope.falseWith("RETRACTED_SOURCE_EXCLUDED")
			}
		}
		result := scope.result()
		if selector.MatchMode == "any" {
			switch {
			case hasTrue:
				result = "TRUE"
			case scope.unknownAtom:
				result = "UNKNOWN"
			default:
				result = "FALSE"
			}
		}
		// Scope is one predicate atom. A decisive result must not leak a
		// non-decisive member's uncertainty into the outer conjunction.
		switch result {
		case "FALSE":
			acc.falseWith("APPLICABILITY_SCOPE_MISMATCH")
		case "UNKNOWN":
			acc.unknownWith(sortedUnique(scope.reasons)[0])
		}
	}

	if len(predicate.RelationTypes) > 0 {
		if source.Relation == nil {
			witnesses = append(witnesses, witness(snapshot.Metadata.ID, "/spec/dependencyEdges"))
			acc.unknownWith("APPLICABILITY_INPUT_MISSING")
		} else if !contains(predicate.RelationTypes, *source.Relation) {
			acc.falseWith("APPLICABILITY_RELATION_TYPE_MISMATCH")
		}
	}
	reasons := sortedUnique(acc.reasons)
	witnesses = sortedUnique(witnesses)
	result := acc.result()
	id := evaluationIdentifier(snapshot.Digest, change.Digest, change.Spec.SubjectRef, source.ID, predicate.PredicateVersion, result, reasons, witnesses)
	return applicabilityEvaluation{EvaluationID: id, SourceRef: source.ID, PredicateVersion: predicate.PredicateVersion, Result: result, ReasonCodes: reasons, WitnessRefs: witnesses}
}

type queuedPath struct {
	path  impactPath
	nodes []string
	depth int
}

var propagatingRelations = map[string]struct{}{
	"business_dependency":    {},
	"contractual_dependency": {},
	"financial_exposure":     {},
	"compliance_dependency":  {},
	"operational_dependency": {},
}

func (d *derivation) materializeClosure(rootUnknown bool) *operationError {
	subject := d.snapshot.nodeByID[d.change.Spec.SubjectRef]
	seed := impactPath{TargetRef: d.change.Spec.SubjectRef, State: "affected", EdgeRefs: []string{}, RuleRefs: []string{}, Origin: "dependency", ReasonCodes: []string{}, Truncated: false}
	if subject.AdmissionStatus == "candidate" || subject.AdmissionStatus == "disputed" {
		seed.State, seed.Origin, seed.ReasonCodes = "unknown", "gap", []string{"CANDIDATE_INPUT_NOT_AUTHORITY"}
	} else if subject.AdmissionStatus == "retracted" {
		seed.State, seed.Origin, seed.ReasonCodes = "out_of_declared_scope", "excluded", []string{"RETRACTED_SOURCE_EXCLUDED"}
	}
	seed, err := d.addPath(seed)
	if err != nil {
		return err
	}
	queue := []queuedPath{{path: seed, nodes: []string{d.change.Spec.SubjectRef}, depth: 0}}
	if seed.State == "unknown" {
		d.unknownPaths = append(d.unknownPaths, seed)
	}
	if seed.State != "out_of_declared_scope" {
		for len(queue) > 0 {
			current := queue[0]
			queue = queue[1:]
			if current.depth >= d.snapshot.Spec.Completeness.MaxDepth || current.path.Truncated {
				continue
			}
			outgoing := d.outgoingEdges(current.path.TargetRef)
			for _, edge := range outgoing {
				if _, ok := propagatingRelations[edge.RelationType]; !ok || edge.AdmissionStatus == "retracted" {
					continue
				}
				target := d.snapshot.nodeByID[edge.TargetRef]
				if target == nil || target.AdmissionStatus == "retracted" || contains(current.nodes, edge.TargetRef) {
					continue
				}
				evaluation, ok := d.evalBySource[edge.EdgeID]
				if !ok || evaluation.Result == "FALSE" {
					continue
				}
				path := impactPath{
					TargetRef:      edge.TargetRef,
					EdgeRefs:       append(append([]string(nil), current.path.EdgeRefs...), edge.EdgeID),
					RuleRefs:       []string{},
					EvaluationRefs: append(append([]string(nil), current.path.EvaluationRefs...), evaluation.EvaluationID),
					State:          "affected", Origin: "dependency", ReasonCodes: append([]string(nil), current.path.ReasonCodes...),
				}
				covered := d.edgeCovered(edge)
				sourceNode := d.snapshot.nodeByID[edge.SourceRef]
				if current.path.State != "affected" || evaluation.Result != "TRUE" || edge.AdmissionStatus != "admitted" || sourceNode == nil || sourceNode.AdmissionStatus != "admitted" || target.AdmissionStatus != "admitted" || !covered {
					path.State, path.Origin = "unknown", "gap"
					path.ReasonCodes = append(path.ReasonCodes, evaluation.ReasonCodes...)
					if edge.AdmissionStatus == "candidate" || edge.AdmissionStatus == "disputed" {
						path.ReasonCodes = append(path.ReasonCodes, "CANDIDATE_EDGE_NOT_AUTHORITY")
					}
					if (sourceNode != nil && (sourceNode.AdmissionStatus == "candidate" || sourceNode.AdmissionStatus == "disputed")) || target.AdmissionStatus == "candidate" || target.AdmissionStatus == "disputed" {
						path.ReasonCodes = append(path.ReasonCodes, "CANDIDATE_INPUT_NOT_AUTHORITY")
					}
					if !covered {
						path.ReasonCodes = append(path.ReasonCodes, "GRAPH_COVERAGE_PARTIAL")
					}
				}
				nodes := append(append([]string(nil), current.nodes...), edge.TargetRef)
				depth := current.depth + 1
				if depth == d.snapshot.Spec.Completeness.MaxDepth && d.hasViableContinuation(edge.TargetRef, nodes) {
					path.State, path.Origin, path.Truncated = "unknown", "gap", true
					path.ReasonCodes = append(path.ReasonCodes, "IMPACT_SEARCH_TRUNCATED")
					d.truncations = append(d.truncations, truncationFrontier{targetRef: edge.TargetRef, nodes: append([]string(nil), nodes...)})
				}
				path.ReasonCodes = sortedUnique(path.ReasonCodes)
				path, err = d.addPath(path)
				if err != nil {
					return err
				}
				if path.State == "affected" {
					d.dependencyPaths = append(d.dependencyPaths, path)
					node := d.snapshot.nodeByID[path.TargetRef]
					if node.OwnerRoleRef == nil || d.snapshot.roleByID[*node.OwnerRoleRef] == nil || d.snapshot.roleByID[*node.OwnerRoleRef].AdmissionStatus != "admitted" {
						return opError(statusError, codeObligationInvalid, errors.New("affected dependency target lacks an admitted owner role"))
					}
					d.obligationSeeds = append(d.obligationSeeds, obligationSeed{Origin: "dependency_path", TargetRef: path.TargetRef, RequiredRoleRef: *node.OwnerRoleRef, ObligationType: "assess_dependency_impact", ResolutionState: "affected", DomainRef: node.DomainRef, RequiredEvidence: []string{"impact_assessment"}, PathRefs: []string{path.PathID}})
				} else if path.State == "unknown" {
					d.unknownPaths = append(d.unknownPaths, path)
				}
				if !path.Truncated {
					queue = append(queue, queuedPath{path: path, nodes: nodes, depth: depth})
				}
			}
		}
	}

	if err := d.materializeRules(); err != nil {
		return err
	}
	if err := d.materializeDuties(); err != nil {
		return err
	}
	if err := d.materializeBoundaryGaps(); err != nil {
		return err
	}
	if err := d.materializeCompleteResiduals(); err != nil {
		return err
	}
	d.assignUnknownPathDuties()
	return nil
}

func (d *derivation) outgoingEdges(sourceRef string) []depEdge {
	result := make([]depEdge, 0)
	for _, edge := range d.snapshot.Spec.parsedEdges {
		if edge.SourceRef == sourceRef {
			result = append(result, edge)
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].EdgeID < result[j].EdgeID })
	return result
}

func (d *derivation) edgeCovered(edge depEdge) bool {
	c := d.snapshot.Spec.Completeness
	return contains(c.CoveredNodeRefs, edge.SourceRef) && contains(c.CoveredNodeRefs, edge.TargetRef) && contains(c.CoveredRelationTypes, edge.RelationType)
}

func (d *derivation) hasViableContinuation(sourceRef string, visited []string) bool {
	for _, edge := range d.outgoingEdges(sourceRef) {
		if _, ok := propagatingRelations[edge.RelationType]; !ok || edge.AdmissionStatus == "retracted" || contains(visited, edge.TargetRef) {
			continue
		}
		target := d.snapshot.nodeByID[edge.TargetRef]
		if target == nil || target.AdmissionStatus == "retracted" {
			continue
		}
		if evaluation, ok := d.evalBySource[edge.EdgeID]; ok && evaluation.Result != "FALSE" {
			return true
		}
	}
	return false
}

func (d *derivation) addPath(path impactPath) (impactPath, *operationError) {
	d.pathAttempts++
	if d.pathAttempts > maxPathPrefixes {
		return impactPath{}, opError(statusResourceExhausted, codeResourceExceeded, errors.New("maxPathPrefixes exceeded"))
	}
	if path.EdgeRefs == nil {
		path.EdgeRefs = []string{}
	}
	if path.RuleRefs == nil {
		path.RuleRefs = []string{}
	}
	if path.ReasonCodes == nil {
		path.ReasonCodes = []string{}
	}
	path.ReasonCodes = sortedUnique(path.ReasonCodes)
	path.PathID = pathIdentifier(d.snapshot.Digest, d.change.Digest, path)
	if existing, ok := d.paths[path.PathID]; ok {
		existing.ReasonCodes = sortedUnique(append(existing.ReasonCodes, path.ReasonCodes...))
		d.paths[path.PathID] = existing
		return existing, nil
	}
	d.paths[path.PathID] = path
	return path, nil
}

func (d *derivation) materializeRules() *operationError {
	if d.snapshot.nodeByID[d.change.Spec.SubjectRef].AdmissionStatus == "retracted" {
		return nil
	}
	rules := append([]rule(nil), d.snapshot.Spec.parsedRules...)
	sort.Slice(rules, func(i, j int) bool { return rules[i].RuleID < rules[j].RuleID })
	for _, rule := range rules {
		if rule.AdmissionStatus == "retracted" {
			continue
		}
		evaluation, ok := d.evalBySource[rule.RuleID]
		if !ok || evaluation.Result == "FALSE" {
			continue
		}
		target, role := d.snapshot.nodeByID[rule.TargetRef], d.snapshot.roleByID[rule.RequiredRoleRef]
		if target == nil || role == nil || target.AdmissionStatus == "retracted" || role.AdmissionStatus == "retracted" {
			continue
		}
		path := impactPath{TargetRef: rule.TargetRef, State: "affected", EdgeRefs: []string{}, RuleRefs: []string{rule.RuleID}, EvaluationRefs: []string{evaluation.EvaluationID}, Origin: "rule", ReasonCodes: []string{}}
		subject := d.snapshot.nodeByID[d.change.Spec.SubjectRef]
		if evaluation.Result != "TRUE" || rule.AdmissionStatus != "admitted" || subject.AdmissionStatus != "admitted" || target.AdmissionStatus != "admitted" || role.AdmissionStatus != "admitted" {
			path.State, path.Origin, path.ReasonCodes = "unknown", "gap", append([]string(nil), evaluation.ReasonCodes...)
			if target.AdmissionStatus == "candidate" || target.AdmissionStatus == "disputed" || role.AdmissionStatus == "candidate" || role.AdmissionStatus == "disputed" {
				path.ReasonCodes = append(path.ReasonCodes, "CANDIDATE_INPUT_NOT_AUTHORITY")
			}
		}
		path, err := d.addPath(path)
		if err != nil {
			return err
		}
		if path.State == "affected" {
			d.obligationSeeds = append(d.obligationSeeds, obligationSeed{Origin: "organizational_rule", TargetRef: rule.TargetRef, RequiredRoleRef: rule.RequiredRoleRef, ObligationType: rule.ObligationType, ResolutionState: "affected", DomainRef: target.DomainRef, RequiredEvidence: rule.RequiredEvidence, PathRefs: []string{path.PathID}, PrerequisiteObligationTypes: rule.PrerequisiteObligationTypes, PrerequisitePathRefs: []string{path.PathID}})
		} else {
			if len(rule.PrerequisiteObligationTypes) > 0 {
				return opError(statusError, codePrerequisiteMissing, errors.New("non-FALSE prerequisite rule did not materialize a successor obligation"))
			}
			d.unknownPaths = append(d.unknownPaths, path)
		}
	}
	return nil
}

func (d *derivation) materializeDuties() *operationError {
	duties := append([]duty(nil), d.snapshot.Spec.UnknownTransitionDuties...)
	sort.Slice(duties, func(i, j int) bool { return duties[i].DutyID < duties[j].DutyID })
	for _, duty := range duties {
		evaluation, ok := d.evalBySource[duty.DutyID]
		if !ok || evaluation.Result != "UNKNOWN" {
			continue
		}
		target, role := d.snapshot.nodeByID[duty.TargetRef], d.snapshot.roleByID[duty.RequiredRoleRef]
		if target == nil || role == nil || target.AdmissionStatus == "retracted" {
			continue
		}
		dutyRefs := []string(nil)
		if duty.AdmissionStatus == "admitted" {
			if role.AdmissionStatus != "admitted" {
				return opError(statusError, codeUnknownDutyInvalid, fmt.Errorf("unknown-transition duty lacks an admitted role: %s", duty.DutyID))
			}
			dutyRefs = []string{duty.DutyID}
		}
		path := impactPath{TargetRef: duty.TargetRef, State: "unknown", EdgeRefs: []string{}, RuleRefs: []string{}, EvaluationRefs: []string{evaluation.EvaluationID}, DutyRefs: dutyRefs, Origin: "gap", ReasonCodes: append([]string(nil), evaluation.ReasonCodes...)}
		path, err := d.addPath(path)
		if err != nil {
			return err
		}
		d.unknownPaths = append(d.unknownPaths, path)
		if duty.AdmissionStatus == "admitted" {
			d.obligationSeeds = append(d.obligationSeeds, obligationSeed{Origin: "organizational_rule", TargetRef: duty.TargetRef, RequiredRoleRef: duty.RequiredRoleRef, ObligationType: duty.ObligationType, ResolutionState: "unknown", DomainRef: role.DomainRef, RequiredEvidence: duty.RequiredEvidence, PathRefs: []string{path.PathID}, PrerequisiteObligationTypes: duty.PrerequisiteObligationTypes, PrerequisitePathRefs: []string{path.PathID}})
		}
	}
	return nil
}

func (d *derivation) materializeBoundaryGaps() *operationError {
	c := d.snapshot.Spec.Completeness
	if c.Status == "complete" {
		return nil
	}
	gaps := append([]string(nil), c.KnownGaps...)
	sort.Strings(gaps)
	if len(gaps) == 0 {
		if c.DiscoveryTargetRef != nil {
			gaps = []string{*c.DiscoveryTargetRef}
		} else {
			gaps = []string{"urn:oac:boundary:unobserved"}
		}
	}
	for _, gap := range gaps {
		path := impactPath{TargetRef: gap, State: "unknown", EdgeRefs: []string{}, RuleRefs: []string{}, Origin: "gap", ReasonCodes: []string{"GRAPH_COVERAGE_PARTIAL"}}
		path, err := d.addPath(path)
		if err != nil {
			return err
		}
		d.unknownPaths = append(d.unknownPaths, path)
	}
	return nil
}

func (d *derivation) materializeCompleteResiduals() *operationError {
	if d.snapshot.Spec.Completeness.Status != "complete" {
		return nil
	}
	reachedTargets := map[string]bool{}
	for _, path := range d.paths {
		reachedTargets[path.TargetRef] = true
	}
	truncatedTargets := d.truncatedContinuationTargets()
	nodes := append([]orgNode(nil), d.snapshot.Spec.Nodes...)
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].NodeID < nodes[j].NodeID })
	for _, node := range nodes {
		if reachedTargets[node.NodeID] {
			continue
		}
		proof := d.boundedNonimpactProof(node.NodeID)
		covered := contains(d.snapshot.Spec.Completeness.CoveredNodeRefs, node.NodeID)
		path := impactPath{TargetRef: node.NodeID, EdgeRefs: []string{}, RuleRefs: []string{}, ReasonCodes: []string{}}
		switch {
		case node.AdmissionStatus == "retracted":
			path.State, path.Origin = "out_of_declared_scope", "excluded"
			path.ReasonCodes = []string{"RETRACTED_SOURCE_EXCLUDED"}
		case truncatedTargets[node.NodeID]:
			path.State, path.Origin = "unknown", "gap"
			path.ReasonCodes = []string{"IMPACT_SEARCH_TRUNCATED"}
			if node.AdmissionStatus != "admitted" {
				path.ReasonCodes = append(path.ReasonCodes, "CANDIDATE_INPUT_NOT_AUTHORITY")
			}
			if !covered {
				path.ReasonCodes = append(path.ReasonCodes, "GRAPH_COVERAGE_PARTIAL")
			}
		case node.AdmissionStatus != "admitted" || !covered:
			if proof != nil && proof.hasRoute && proof.authoritativeCut && len(proof.evaluationRefs) > 0 {
				path.State, path.Origin = "unaffected_proven", "bounded_non_impact"
				path.EvaluationRefs = append([]string(nil), proof.evaluationRefs...)
			} else {
				path.State, path.Origin = "unknown", "gap"
				if node.AdmissionStatus != "admitted" {
					path.ReasonCodes = []string{"CANDIDATE_INPUT_NOT_AUTHORITY"}
				} else {
					path.ReasonCodes = []string{"GRAPH_COVERAGE_PARTIAL"}
				}
			}
		case proof == nil:
			path.State, path.Origin = "unknown", "gap"
			path.ReasonCodes = []string{"GRAPH_COVERAGE_PARTIAL"}
		default:
			path.State, path.Origin = "unaffected_proven", "bounded_non_impact"
			path.EvaluationRefs = append([]string(nil), proof.evaluationRefs...)
		}
		path, err := d.addPath(path)
		if err != nil {
			return err
		}
		if path.State == "unknown" {
			d.unknownPaths = append(d.unknownPaths, path)
		}
	}
	return nil
}

type nonImpactProof struct {
	evaluationRefs   []string
	hasRoute         bool
	authoritativeCut bool
}

// semanticEdges is the Profile's topology: only propagating relation kinds and
// non-retracted edges participate. Retraction is nevertheless retained in the
// applicability ledger by evaluateAllSources.
func (d *derivation) semanticEdges() []depEdge {
	edges := make([]depEdge, 0, len(d.snapshot.Spec.parsedEdges))
	for _, edge := range d.snapshot.Spec.parsedEdges {
		if _, propagates := propagatingRelations[edge.RelationType]; !propagates || edge.AdmissionStatus == "retracted" {
			continue
		}
		edges = append(edges, edge)
	}
	sort.Slice(edges, func(i, j int) bool { return edges[i].EdgeID < edges[j].EdgeID })
	return edges
}

func (d *derivation) semanticOutgoing(sourceRef string) []depEdge {
	result := make([]depEdge, 0)
	for _, edge := range d.semanticEdges() {
		if edge.SourceRef == sourceRef {
			result = append(result, edge)
		}
	}
	return result
}

func (d *derivation) truncatedContinuationTargets() map[string]bool {
	reachable := map[string]bool{}
	for _, frontier := range d.truncations {
		forbidden := map[string]bool{}
		for _, ref := range frontier.nodes[:len(frontier.nodes)-1] {
			forbidden[ref] = true
		}
		visited := map[string]bool{frontier.targetRef: true}
		pending := []string{frontier.targetRef}
		for len(pending) > 0 {
			sourceRef := pending[0]
			pending = pending[1:]
			for _, edge := range d.semanticOutgoing(sourceRef) {
				source, target := d.snapshot.nodeByID[edge.SourceRef], d.snapshot.nodeByID[edge.TargetRef]
				if source == nil || target == nil || source.AdmissionStatus == "retracted" || target.AdmissionStatus == "retracted" {
					continue
				}
				evaluation, ok := d.evalBySource[edge.EdgeID]
				if !ok || evaluation.Result == "FALSE" || forbidden[edge.TargetRef] || visited[edge.TargetRef] {
					continue
				}
				visited[edge.TargetRef] = true
				reachable[edge.TargetRef] = true
				pending = append(pending, edge.TargetRef)
			}
		}
	}
	return reachable
}

func (d *derivation) authoritativeTruePrefixReachable(rootRef string) map[string]bool {
	root := d.snapshot.nodeByID[rootRef]
	if root == nil || root.AdmissionStatus != "admitted" {
		return map[string]bool{}
	}
	reachable := map[string]bool{rootRef: true}
	pending := []string{rootRef}
	for len(pending) > 0 {
		sourceRef := pending[0]
		pending = pending[1:]
		for _, edge := range d.semanticOutgoing(sourceRef) {
			source, target := d.snapshot.nodeByID[edge.SourceRef], d.snapshot.nodeByID[edge.TargetRef]
			evaluation, ok := d.evalBySource[edge.EdgeID]
			if !ok || edge.AdmissionStatus != "admitted" || evaluation.Result != "TRUE" || source == nil || target == nil || source.AdmissionStatus != "admitted" || target.AdmissionStatus != "admitted" || reachable[edge.TargetRef] {
				continue
			}
			reachable[edge.TargetRef] = true
			pending = append(pending, edge.TargetRef)
		}
	}
	return reachable
}

func (d *derivation) topologyReachable(rootRef string, blocked map[string]bool) map[string]bool {
	root := d.snapshot.nodeByID[rootRef]
	if root == nil || root.AdmissionStatus == "retracted" {
		return map[string]bool{}
	}
	reachable := map[string]bool{rootRef: true}
	pending := []string{rootRef}
	for len(pending) > 0 {
		sourceRef := pending[0]
		pending = pending[1:]
		for _, edge := range d.semanticOutgoing(sourceRef) {
			if blocked[edge.EdgeID] {
				continue
			}
			source, target := d.snapshot.nodeByID[edge.SourceRef], d.snapshot.nodeByID[edge.TargetRef]
			if source == nil || target == nil || source.AdmissionStatus == "retracted" || target.AdmissionStatus == "retracted" || reachable[edge.TargetRef] {
				continue
			}
			reachable[edge.TargetRef] = true
			pending = append(pending, edge.TargetRef)
		}
	}
	return reachable
}

func (d *derivation) topologyReverseReachable(targetRef string) map[string]bool {
	reachable := map[string]bool{targetRef: true}
	pending := []string{targetRef}
	edges := d.semanticEdges()
	for len(pending) > 0 {
		targetRef := pending[0]
		pending = pending[1:]
		for _, edge := range edges {
			if edge.TargetRef != targetRef {
				continue
			}
			source, target := d.snapshot.nodeByID[edge.SourceRef], d.snapshot.nodeByID[edge.TargetRef]
			if source == nil || target == nil || source.AdmissionStatus == "retracted" || target.AdmissionStatus == "retracted" || reachable[edge.SourceRef] {
				continue
			}
			reachable[edge.SourceRef] = true
			pending = append(pending, edge.SourceRef)
		}
	}
	return reachable
}

// boundedNonimpactProof ports the public Supplier Profile proof relation. It
// proves that every declared route is closed by a FALSE predicate and selects
// authoritative FALSE witnesses when the target itself is not authoritative.
func (d *derivation) boundedNonimpactProof(targetRef string) *nonImpactProof {
	rootRef := d.change.Spec.SubjectRef
	root := d.snapshot.nodeByID[rootRef]
	rootAdmitted := root != nil && root.AdmissionStatus == "admitted"
	edges := d.semanticEdges()
	allFalseEdges := map[string]bool{}
	for _, edge := range edges {
		if evaluation, ok := d.evalBySource[edge.EdgeID]; ok && evaluation.Result == "FALSE" {
			allFalseEdges[edge.EdgeID] = true
		}
	}
	authoritativePrefix := d.authoritativeTruePrefixReachable(rootRef)
	admittedFalseEdges := map[string]bool{}
	for _, edge := range edges {
		source, target := d.snapshot.nodeByID[edge.SourceRef], d.snapshot.nodeByID[edge.TargetRef]
		evaluation, ok := d.evalBySource[edge.EdgeID]
		if ok && edge.AdmissionStatus == "admitted" && authoritativePrefix[edge.SourceRef] && source != nil && target != nil && source.AdmissionStatus == "admitted" && target.AdmissionStatus != "retracted" && evaluation.Result == "FALSE" {
			admittedFalseEdges[edge.EdgeID] = true
		}
	}

	topologyReachable := d.topologyReachable(rootRef, map[string]bool{})
	hasGraphRoute := topologyReachable[targetRef]
	graphAuthoritativeCut := true
	semanticFalseRefs := map[string]bool{}
	authoritativeFalseRefs := map[string]bool{}
	if hasGraphRoute {
		if d.topologyReachable(rootRef, allFalseEdges)[targetRef] {
			return nil
		}
		graphAuthoritativeCut = !d.topologyReachable(rootRef, admittedFalseEdges)[targetRef]
		reverseReachable := d.topologyReverseReachable(targetRef)
		for _, edge := range edges {
			if !topologyReachable[edge.SourceRef] || !reverseReachable[edge.TargetRef] {
				continue
			}
			evaluation := d.evalBySource[edge.EdgeID]
			if allFalseEdges[edge.EdgeID] {
				semanticFalseRefs[evaluation.EvaluationID] = true
			}
			if admittedFalseEdges[edge.EdgeID] {
				authoritativeFalseRefs[evaluation.EvaluationID] = true
			}
		}
	}

	type directSource struct {
		id        string
		admission string
	}
	directSources := make([]directSource, 0)
	for _, rule := range d.snapshot.Spec.parsedRules {
		if rule.TargetRef == targetRef && rule.AdmissionStatus != "retracted" {
			directSources = append(directSources, directSource{id: rule.RuleID, admission: rule.AdmissionStatus})
		}
	}
	for _, duty := range d.snapshot.Spec.UnknownTransitionDuties {
		evaluation, ok := d.evalBySource[duty.DutyID]
		if duty.TargetRef == targetRef && duty.AdmissionStatus != "retracted" && ok && evaluation.Result == "FALSE" {
			directSources = append(directSources, directSource{id: duty.DutyID, admission: duty.AdmissionStatus})
		}
	}
	directAuthoritativeCut := rootAdmitted
	for _, source := range directSources {
		evaluation, ok := d.evalBySource[source.id]
		if !ok || evaluation.Result != "FALSE" {
			return nil
		}
		semanticFalseRefs[evaluation.EvaluationID] = true
		if rootAdmitted && source.admission == "admitted" {
			authoritativeFalseRefs[evaluation.EvaluationID] = true
		}
		if source.admission != "admitted" {
			directAuthoritativeCut = false
		}
	}

	target := d.snapshot.nodeByID[targetRef]
	targetRequiresAuthority := target != nil && (target.AdmissionStatus != "admitted" || !contains(d.snapshot.Spec.Completeness.CoveredNodeRefs, targetRef))
	selectedRefs := semanticFalseRefs
	if targetRequiresAuthority {
		selectedRefs = authoritativeFalseRefs
	}
	refs := make([]string, 0, len(selectedRefs))
	for ref := range selectedRefs {
		refs = append(refs, ref)
	}
	sort.Strings(refs)
	return &nonImpactProof{
		evaluationRefs:   refs,
		hasRoute:         hasGraphRoute || len(directSources) > 0,
		authoritativeCut: graphAuthoritativeCut && directAuthoritativeCut,
	}
}

func (d *derivation) assignUnknownPathDuties() {
	firedByTarget := map[string][]int{}
	for index := range d.obligationSeeds {
		seed := d.obligationSeeds[index]
		if seed.ResolutionState == "unknown" && seed.Origin == "organizational_rule" {
			firedByTarget[seed.TargetRef] = append(firedByTarget[seed.TargetRef], index)
		}
	}
	assigned := map[string]bool{}
	for _, path := range d.unknownPaths {
		if len(path.DutyRefs) > 0 {
			assigned[path.PathID] = true
			continue
		}
		indices := firedByTarget[path.TargetRef]
		if len(indices) > 0 {
			for _, index := range indices {
				d.obligationSeeds[index].PathRefs = append(d.obligationSeeds[index].PathRefs, path.PathID)
			}
			assigned[path.PathID] = true
		}
	}
	paths := make([]string, 0)
	for _, path := range d.unknownPaths {
		if !assigned[path.PathID] {
			paths = append(paths, path.PathID)
		}
	}
	if len(paths) == 0 {
		return
	}
	c := d.snapshot.Spec.Completeness
	if c.DiscoveryTargetRef != nil && c.DiscoveryObligationType != nil && len(c.DiscoveryEvidence) > 0 {
		role := d.snapshot.roleByID[c.DiscoveryRoleRef]
		if role != nil {
			d.obligationSeeds = append(d.obligationSeeds, obligationSeed{Origin: "discovery_gap", TargetRef: *c.DiscoveryTargetRef, RequiredRoleRef: c.DiscoveryRoleRef, ObligationType: *c.DiscoveryObligationType, ResolutionState: "unknown", DomainRef: role.DomainRef, RequiredEvidence: c.DiscoveryEvidence, PathRefs: paths})
		}
		return
	}
	for _, pathID := range paths {
		path := d.paths[pathID]
		role := d.snapshot.roleByID[c.DiscoveryRoleRef]
		if role == nil {
			continue
		}
		d.obligationSeeds = append(d.obligationSeeds, obligationSeed{Origin: "discovery_gap", TargetRef: path.TargetRef, RequiredRoleRef: c.DiscoveryRoleRef, ObligationType: "discover_dependency", ResolutionState: "unknown", DomainRef: role.DomainRef, RequiredEvidence: []string{"dependency_admission_decision"}, PathRefs: []string{pathID}})
	}
}

type obligationKey struct {
	origin, target, role, obligationType, state string
}

func semanticObligationKey(origin, target, role, obligationType, state string) obligationKey {
	return obligationKey{origin, target, role, obligationType, state}
}

func (d *derivation) finalizeObligations() ([]coverageObligation, *operationError) {
	groups := map[obligationKey]obligationSeed{}
	for _, seed := range d.obligationSeeds {
		if d.snapshot.roleByID[seed.RequiredRoleRef] == nil || d.snapshot.roleByID[seed.RequiredRoleRef].AdmissionStatus != "admitted" {
			return nil, opError(statusError, codeObligationInvalid, errors.New("obligation requiredRoleRef is not admitted"))
		}
		key := semanticObligationKey(seed.Origin, seed.TargetRef, seed.RequiredRoleRef, seed.ObligationType, seed.ResolutionState)
		if existing, ok := groups[key]; ok {
			existing.PathRefs = append(existing.PathRefs, seed.PathRefs...)
			existing.RequiredEvidence = append(existing.RequiredEvidence, seed.RequiredEvidence...)
			groups[key] = existing
		} else {
			groups[key] = seed
		}
	}
	obligations := make([]coverageObligation, 0, len(groups))
	for _, seed := range groups {
		pathRefs := sortedUnique(seed.PathRefs)
		if len(pathRefs) == 0 || len(seed.RequiredEvidence) == 0 {
			return nil, opError(statusError, codeObligationInvalid, errors.New("obligation has no supporting path or evidence"))
		}
		obligation := coverageObligation{
			Origin: seed.Origin, TargetRef: seed.TargetRef, DomainRef: seed.DomainRef,
			RequiredRoleRef: seed.RequiredRoleRef, ObligationType: seed.ObligationType,
			RequiredEvidence: sortedUnique(seed.RequiredEvidence), PathRefs: pathRefs,
			ResolutionState: seed.ResolutionState,
		}
		obligation.ObligationID = obligationIdentifier(d.snapshot.Digest, d.change.Digest, obligation)
		obligations = append(obligations, obligation)
	}
	sort.Slice(obligations, func(i, j int) bool { return obligations[i].ObligationID < obligations[j].ObligationID })
	return obligations, nil
}

type orderAccumulator struct {
	pred       string
	succ       string
	dependency []string
	groups     [][]string
}

type derivationRolePair struct {
	predecessor, successor string
}

func orderPairKey(pred, succ string) derivationRolePair { return derivationRolePair{pred, succ} }

func (d *derivation) finalizeOrders(obligations []coverageObligation) ([]derivationOrderRequirement, *operationError) {
	orders := map[derivationRolePair]*orderAccumulator{}
	addDependency := func(pred, succ, reason string) {
		if pred == succ {
			return
		}
		key := orderPairKey(pred, succ)
		entry := orders[key]
		if entry == nil {
			entry = &orderAccumulator{pred: pred, succ: succ}
			orders[key] = entry
		}
		entry.dependency = append(entry.dependency, reason)
	}
	for _, path := range d.dependencyPaths {
		if path.State != "affected" || len(path.EdgeRefs) == 0 {
			continue
		}
		edge := d.lookupEdge(path.EdgeRefs[len(path.EdgeRefs)-1])
		if edge == nil {
			continue
		}
		source, target := d.snapshot.nodeByID[edge.SourceRef], d.snapshot.nodeByID[edge.TargetRef]
		if source == nil || target == nil || source.OwnerRoleRef == nil || target.OwnerRoleRef == nil {
			continue
		}
		addDependency(*source.OwnerRoleRef, *target.OwnerRoleRef, path.PathID)
	}

	byKey := map[obligationKey]coverageObligation{}
	byType := map[string][]coverageObligation{}
	for _, obligation := range obligations {
		key := semanticObligationKey(obligation.Origin, obligation.TargetRef, obligation.RequiredRoleRef, obligation.ObligationType, obligation.ResolutionState)
		byKey[key] = obligation
		byType[obligation.ObligationType] = append(byType[obligation.ObligationType], obligation)
	}
	for _, seed := range d.obligationSeeds {
		if len(seed.PrerequisiteObligationTypes) == 0 {
			continue
		}
		successor, ok := byKey[semanticObligationKey(seed.Origin, seed.TargetRef, seed.RequiredRoleRef, seed.ObligationType, seed.ResolutionState)]
		if !ok {
			return nil, opError(statusError, codePrerequisiteMissing, errors.New("prerequisite successor obligation did not materialize"))
		}
		for _, prerequisiteType := range seed.PrerequisiteObligationTypes {
			matches := byType[prerequisiteType]
			if len(matches) != 1 {
				return nil, opError(statusError, codePrerequisiteMissing, errors.New("prerequisite obligation type is missing or ambiguous"))
			}
			predecessor := matches[0]
			if predecessor.RequiredRoleRef == successor.RequiredRoleRef {
				return nil, opError(statusError, codePrerequisiteMissing, errors.New("same-role prerequisites are forbidden"))
			}
			key := orderPairKey(predecessor.RequiredRoleRef, successor.RequiredRoleRef)
			entry := orders[key]
			if entry == nil {
				entry = &orderAccumulator{pred: predecessor.RequiredRoleRef, succ: successor.RequiredRoleRef}
				orders[key] = entry
			}
			paths := seed.PrerequisitePathRefs
			if len(paths) == 0 {
				paths = seed.PathRefs
			}
			for _, pathID := range paths {
				entry.groups = append(entry.groups, sortedUnique([]string{predecessor.ObligationID, successor.ObligationID, pathID}))
			}
		}
	}

	if hasOrderCycle(orders) {
		return nil, opError(statusError, codeOrderCycle, errors.New("derived role order contains a cycle"))
	}
	result := make([]derivationOrderRequirement, 0, len(orders))
	for _, entry := range orders {
		dependencies := sortedUnique(entry.dependency)
		groups := sortUniqueGroups(entry.groups)
		reasons := append([]string(nil), dependencies...)
		for _, group := range groups {
			reasons = append(reasons, group...)
		}
		result = append(result, derivationOrderRequirement{
			PredecessorRoleRef: entry.pred, SuccessorRoleRef: entry.succ,
			ReasonRefs: sortedUnique(reasons), RoleWide: len(dependencies) > 0,
			DependencyReasonRefs: dependencies, PrerequisiteReasonGroups: groups,
		})
	}
	sort.Slice(result, func(i, j int) bool { return compareOrders(result[i], result[j]) < 0 })
	return result, nil
}

func (d *derivation) lookupEdge(id string) *depEdge {
	for i := range d.snapshot.Spec.parsedEdges {
		if d.snapshot.Spec.parsedEdges[i].EdgeID == id {
			return &d.snapshot.Spec.parsedEdges[i]
		}
	}
	return nil
}

func sortUniqueGroups(groups [][]string) [][]string {
	seen := map[string]struct{}{}
	result := make([][]string, 0, len(groups))
	for _, group := range groups {
		group = sortedUnique(group)
		key := strings.Join(group, "\x00")
		if _, duplicate := seen[key]; !duplicate {
			seen[key] = struct{}{}
			result = append(result, group)
		}
	}
	sort.Slice(result, func(i, j int) bool { return compareStringSlices(result[i], result[j]) < 0 })
	return result
}

func hasOrderCycle(orders map[derivationRolePair]*orderAccumulator) bool {
	adjacency := map[string][]string{}
	vertices := map[string]struct{}{}
	for _, order := range orders {
		adjacency[order.pred] = append(adjacency[order.pred], order.succ)
		vertices[order.pred], vertices[order.succ] = struct{}{}, struct{}{}
	}
	state := map[string]uint8{}
	var visit func(string) bool
	visit = func(node string) bool {
		if state[node] == 1 {
			return true
		}
		if state[node] == 2 {
			return false
		}
		state[node] = 1
		for _, next := range adjacency[node] {
			if visit(next) {
				return true
			}
		}
		state[node] = 2
		return false
	}
	for node := range vertices {
		if visit(node) {
			return true
		}
	}
	return false
}

func compareOrders(left, right derivationOrderRequirement) int {
	if left.PredecessorRoleRef != right.PredecessorRoleRef {
		return strings.Compare(left.PredecessorRoleRef, right.PredecessorRoleRef)
	}
	if left.SuccessorRoleRef != right.SuccessorRoleRef {
		return strings.Compare(left.SuccessorRoleRef, right.SuccessorRoleRef)
	}
	if left.RoleWide != right.RoleWide {
		if !left.RoleWide {
			return -1
		}
		return 1
	}
	if result := compareStringSlices(left.ReasonRefs, right.ReasonRefs); result != 0 {
		return result
	}
	if result := compareStringSlices(left.DependencyReasonRefs, right.DependencyReasonRefs); result != 0 {
		return result
	}
	for i := 0; i < len(left.PrerequisiteReasonGroups) && i < len(right.PrerequisiteReasonGroups); i++ {
		if result := compareStringSlices(left.PrerequisiteReasonGroups[i], right.PrerequisiteReasonGroups[i]); result != 0 {
			return result
		}
	}
	if len(left.PrerequisiteReasonGroups) < len(right.PrerequisiteReasonGroups) {
		return -1
	}
	if len(left.PrerequisiteReasonGroups) > len(right.PrerequisiteReasonGroups) {
		return 1
	}
	return 0
}

func compareStringSlices(left, right []string) int {
	limit := len(left)
	if len(right) < limit {
		limit = len(right)
	}
	for i := 0; i < limit; i++ {
		if left[i] != right[i] {
			return strings.Compare(left[i], right[i])
		}
	}
	if len(left) < len(right) {
		return -1
	}
	if len(left) > len(right) {
		return 1
	}
	return 0
}
