package main

import (
	"encoding/json"
	"errors"
	"reflect"
	"sort"
)

const planVerifyProtocolVersion = "oac.ctk.stdio/v3"

type verifyPlanInput struct {
	SnapshotBase64 string `json:"snapshotBase64"`
	ChangeBase64   string `json:"changeBase64"`
	PlanBase64     string `json:"planBase64"`
}

type verifyPlanResult struct {
	SnapshotDigest string   `json:"snapshotDigest"`
	ChangeDigest   string   `json:"changeDigest"`
	PlanDigest     string   `json:"planDigest"`
	Verdict        string   `json:"verdict"`
	ReasonCodes    []string `json:"reasonCodes"`
}

func handlePlanVerifyWireRequest(raw []byte) wireResponse {
	requestID := bestEffortRequestID(raw)
	var request wireRequest
	if err := decodeClosed(raw, &request); err != nil {
		return failedPlanVerifyResponse(requestID, classifyEnvelopeError(err))
	}
	requestID = request.RequestID
	if request.ProtocolVersion != planVerifyProtocolVersion || request.RequestID == "" || request.Operation == "" || request.Payload == nil {
		return failedPlanVerifyResponse(requestID, opError(statusError, codeCoreSchemaInvalid, errors.New("invalid request envelope")))
	}
	result, err := dispatchPlanVerify(request.Operation, request.Payload)
	if err != nil {
		return failedPlanVerifyResponse(requestID, err)
	}
	return wireResponse{ProtocolVersion: planVerifyProtocolVersion, RequestID: requestID, SUTStatus: statusCompleted, Result: result}
}

func failedPlanVerifyResponse(requestID string, err *operationError) wireResponse {
	return wireResponse{
		ProtocolVersion: planVerifyProtocolVersion,
		RequestID:       requestID,
		SUTStatus:       err.status,
		Error:           &responseError{Code: err.code, Detail: err.detail},
	}
}

func dispatchPlanVerify(operation string, payload json.RawMessage) (any, *operationError) {
	switch operation {
	case "capabilities":
		if err := requireObjectKeys(payload, nil, nil); err != nil {
			return nil, opError(statusError, codeCTKInputInvalid, err)
		}
		return capabilityResult{
			ImplementationID:       "oac.supplier.go.internal.plan-verifier",
			ImplementationVersion:  "0.1.0-seed1",
			AdapterProtocolVersion: planVerifyProtocolVersion,
			Tracks: []capabilityTrack{{
				Role: "plan-verifier", Operation: "verifyPlan",
				ProfileID:      "oac.supplier.plan-verification.plural-capsule",
				ProfileVersion: "v0.1-seed-1", WireVersion: planVerifyProtocolVersion,
			}},
		}, nil
	case "verifyPlan":
		return verifyPlanOperation(payload)
	default:
		return nil, opError(statusUnsupported, codeUnsupported, errors.New("operation is not implemented"))
	}
}

func verifyPlanOperation(payload json.RawMessage) (any, *operationError) {
	keys := []string{"snapshotBase64", "changeBase64", "planBase64"}
	if err := requireObjectKeys(payload, keys, keys); err != nil {
		return nil, opError(statusError, codeCTKInputInvalid, err)
	}
	var input verifyPlanInput
	if err := decodeClosed(payload, &input); err != nil || input.SnapshotBase64 == "" || input.ChangeBase64 == "" || input.PlanBase64 == "" {
		if err == nil {
			err = errors.New("invalid verifyPlan payload")
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
	planRaw, err := decodeCanonicalBase64(input.PlanBase64)
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
	planAdmission, err := admitResource(planRaw, "OrganizationPlan")
	if err != nil {
		return nil, admissionOperationError(err)
	}
	report, deriveErr := deriveProfile(snapshotAdmission.Snapshot, changeAdmission.Change)
	if deriveErr != nil {
		return nil, deriveErr
	}
	verdict, reasons := verifyFixedPlan(snapshotAdmission.Snapshot, changeAdmission.Change, planAdmission.Plan, report)
	return verifyPlanResult{
		SnapshotDigest: snapshotAdmission.Digest,
		ChangeDigest:   changeAdmission.Digest,
		PlanDigest:     planAdmission.Digest,
		Verdict:        verdict,
		ReasonCodes:    reasons,
	}, nil
}

type rolePair struct{ left, right string }
type workPair struct{ left, right string }

func verifyFixedPlan(snapshot *organizationSnapshot, change *semanticChangeSet, plan *organizationPlan, report profileDerivationReport) (string, []string) {
	reasons := map[string]struct{}{}
	add := func(code string) { reasons[code] = struct{}{} }

	if !resourceRefMatches(plan.Spec.SnapshotRef, snapshot.Kind, snapshot.Metadata, snapshot.Digest) ||
		!resourceRefMatches(plan.Spec.ChangeRef, change.Kind, change.Metadata, change.Digest) {
		add("INPUT_ROOT_MISMATCH")
	}
	if plan.Metadata.Namespace != snapshot.Metadata.Namespace ||
		plan.Metadata.GovernanceRef == nil || snapshot.Metadata.GovernanceRef == nil || change.Metadata.GovernanceRef == nil ||
		*plan.Metadata.GovernanceRef != *snapshot.Metadata.GovernanceRef || *change.Metadata.GovernanceRef != *snapshot.Metadata.GovernanceRef ||
		!optionalStringEqual(plan.Metadata.OwnerRef, snapshot.Metadata.OwnerRef) ||
		!reflect.DeepEqual(plan.Metadata.SourceRefs, []string{snapshot.Metadata.ID, change.Metadata.ID}) {
		add("RESOURCE_COHERENCE_VIOLATION")
	}

	checkEvaluationProjection(plan.Spec.ApplicabilityEvaluations, report.ApplicabilityEvaluations, add)
	checkPathProjection(plan.Spec.ImpactPaths, report.ImpactPaths, report.ApplicabilityEvaluations, add)
	checkObligationProjection(plan.Spec.Obligations, report.Obligations, report.ImpactPaths, add)
	if !equalStrings(sortedCopy(plan.Spec.UnresolvedRefs), report.UnresolvedRefs) {
		add("UNKNOWN_NOT_PRESERVED")
	}
	expectedStatus := "planned"
	if len(report.UnresolvedRefs) != 0 {
		expectedStatus = "guarded_unresolved"
	}
	if plan.Spec.Status != expectedStatus {
		add("PLAN_STATUS_MISMATCH")
	}

	obligations := make(map[string]coverageObligation, len(report.Obligations))
	for _, obligation := range report.Obligations {
		obligations[obligation.ObligationID] = obligation
	}
	checkCoverage(plan, obligations, add)
	checkResponsibilityBindings(plan, add)
	checkQualifications(snapshot, plan, obligations, add)
	checkSeparation(snapshot, plan, add)
	checkOrdering(plan, report, add)
	checkEvidence(plan, obligations, add)
	checkMinimality(snapshot, plan, obligations, add)
	checkDecisions(snapshot, plan, add)

	if len(reasons) != 0 {
		return "REJECT", sortedKeys(reasons)
	}
	if report.RootApplicabilityUnknown {
		return "UNKNOWN", []string{"ROOT_APPLICABILITY_UNKNOWN"}
	}
	if len(report.UnresolvedRefs) != 0 {
		for _, evaluation := range report.ApplicabilityEvaluations {
			if contains(report.UnresolvedRefs, evaluation.EvaluationID) {
				for _, code := range evaluation.ReasonCodes {
					add(code)
				}
			}
		}
		for _, path := range report.ImpactPaths {
			if contains(report.UnresolvedRefs, path.PathID) {
				for _, code := range path.ReasonCodes {
					add(code)
				}
			}
		}
		return "PROVISIONAL", sortedKeys(reasons)
	}
	return "ACCEPT", []string{}
}

func resourceRefMatches(ref resourceRef, kind string, metadata resourceMetadata, digest string) bool {
	return (ref.APIVersion == "" || ref.APIVersion == "oac.dev/v0alpha1") && ref.Kind == kind &&
		ref.Namespace == metadata.Namespace && ref.ResourceID == metadata.ID && ref.Revision == metadata.Revision && ref.Digest == digest
}

func optionalStringEqual(left, right *string) bool {
	return (left == nil && right == nil) || (left != nil && right != nil && *left == *right)
}

func checkEvaluationProjection(actual, expected []applicabilityEvaluation, add func(string)) {
	expectedByID := map[string]applicabilityEvaluation{}
	actualByID := map[string]applicabilityEvaluation{}
	for _, item := range expected {
		expectedByID[item.EvaluationID] = item
	}
	for _, item := range actual {
		actualByID[item.EvaluationID] = item
	}
	for id, wanted := range expectedByID {
		got, ok := actualByID[id]
		if !ok {
			add("APPLICABILITY_EVALUATION_MISSING")
			continue
		}
		if !reflect.DeepEqual(got, wanted) {
			add("APPLICABILITY_EVALUATION_MISMATCH")
			if !reflect.DeepEqual(got.WitnessRefs, wanted.WitnessRefs) {
				add("APPLICABILITY_WITNESS_MISMATCH")
			}
			if got.Result == "TRUE" && wanted.Result != "TRUE" {
				add("APPLICABILITY_SCOPE_WIDENED")
			}
		}
	}
	for id := range actualByID {
		if _, ok := expectedByID[id]; !ok {
			add("APPLICABILITY_EVALUATION_MISMATCH")
			add("APPLICABILITY_SCOPE_WIDENED")
		}
	}
}

func checkPathProjection(actual, expected []impactPath, evaluations []applicabilityEvaluation, add func(string)) {
	expectedByID := map[string]impactPath{}
	actualByID := map[string]impactPath{}
	falseSources, falseEvaluations := map[string]struct{}{}, map[string]struct{}{}
	for _, item := range expected {
		expectedByID[item.PathID] = item
	}
	for _, item := range actual {
		actualByID[item.PathID] = item
	}
	for _, evaluation := range evaluations {
		if evaluation.Result == "FALSE" {
			falseSources[evaluation.SourceRef] = struct{}{}
			falseEvaluations[evaluation.EvaluationID] = struct{}{}
		}
	}
	for id, wanted := range expectedByID {
		got, ok := actualByID[id]
		if !ok {
			add("IMPACT_PATH_OMITTED")
			continue
		}
		if !reflect.DeepEqual(got, wanted) {
			add("IMPACT_PATH_OMITTED")
			if wanted.State == "unknown" && (got.State == "affected" || got.State == "unaffected_proven") {
				add("APPLICABILITY_SCOPE_WIDENED")
			}
			if pathUsesFalseSource(got, falseSources, falseEvaluations) {
				add("PREDICATE_FALSE_PATH_INCLUDED")
			}
		}
	}
	for id, got := range actualByID {
		if _, ok := expectedByID[id]; !ok {
			add("APPLICABILITY_SCOPE_WIDENED")
			if pathUsesFalseSource(got, falseSources, falseEvaluations) {
				add("PREDICATE_FALSE_PATH_INCLUDED")
			}
		}
	}
	expectedDuty, actualDuty := map[string]impactPath{}, map[string]impactPath{}
	for _, path := range expected {
		if len(path.DutyRefs) != 0 {
			expectedDuty[path.PathID] = path
		}
	}
	for _, path := range actual {
		if len(path.DutyRefs) != 0 {
			actualDuty[path.PathID] = path
		}
	}
	for id, wanted := range expectedDuty {
		if got, ok := actualDuty[id]; !ok || !reflect.DeepEqual(got, wanted) {
			add("UNKNOWN_TRANSITION_DUTY_MISSING")
		}
	}
	for id := range actualDuty {
		if _, ok := expectedDuty[id]; !ok {
			add("UNKNOWN_TRANSITION_DUTY_INVALID")
		}
	}
}

func pathUsesFalseSource(path impactPath, sources, evaluations map[string]struct{}) bool {
	for _, ref := range append(append([]string{}, path.EdgeRefs...), path.RuleRefs...) {
		if _, ok := sources[ref]; ok {
			return true
		}
	}
	for _, ref := range path.EvaluationRefs {
		if _, ok := evaluations[ref]; ok {
			return true
		}
	}
	return false
}

func checkObligationProjection(actual, expected []coverageObligation, paths []impactPath, add func(string)) {
	expectedByID, actualByID := map[string]coverageObligation{}, map[string]coverageObligation{}
	for _, item := range expected {
		expectedByID[item.ObligationID] = item
	}
	for _, item := range actual {
		actualByID[item.ObligationID] = item
	}
	if !reflect.DeepEqual(expectedByID, actualByID) {
		add("OBLIGATION_SET_MISMATCH")
	}
	dutyPaths := map[string]struct{}{}
	for _, path := range paths {
		if len(path.DutyRefs) != 0 {
			dutyPaths[path.PathID] = struct{}{}
		}
	}
	for _, obligation := range expected {
		for _, ref := range obligation.PathRefs {
			if _, duty := dutyPaths[ref]; duty {
				if _, present := actualByID[obligation.ObligationID]; !present {
					add("UNKNOWN_TRANSITION_DUTY_MISSING")
				}
			}
		}
	}
}

func checkCoverage(plan *organizationPlan, obligations map[string]coverageObligation, add func(string)) {
	workCount, roleCount := map[string]int{}, map[string]int{}
	roleIDs := map[string]struct{}{}
	for _, role := range plan.Spec.RoleInstances {
		roleIDs[role.RoleInstanceID] = struct{}{}
		for _, ref := range role.ObligationRefs {
			roleCount[ref]++
		}
	}
	for _, work := range plan.Spec.WorkUnits {
		for _, ref := range work.ObligationRefs {
			workCount[ref]++
		}
		if !contains(work.RoleInstanceRefs, work.AccountableRoleInstanceRef) {
			add("OBLIGATION_UNSATISFIED")
		}
		for _, ref := range work.RoleInstanceRefs {
			if _, ok := roleIDs[ref]; !ok {
				add("OBLIGATION_UNSATISFIED")
			}
		}
	}
	all := map[string]struct{}{}
	for ref := range obligations {
		all[ref] = struct{}{}
	}
	for ref := range workCount {
		all[ref] = struct{}{}
	}
	for ref := range roleCount {
		all[ref] = struct{}{}
	}
	for ref := range all {
		if _, ok := obligations[ref]; !ok || workCount[ref] != 1 || roleCount[ref] != 1 {
			add("OBLIGATION_UNSATISFIED")
		}
	}
}

func checkResponsibilityBindings(plan *organizationPlan, add func(string)) {
	roles := map[string]roleInstance{}
	for _, role := range plan.Spec.RoleInstances {
		roles[role.RoleInstanceID] = role
	}
	for _, work := range plan.Spec.WorkUnits {
		workObligations, contributed := stringSet(work.ObligationRefs), map[string]struct{}{}
		valid := true
		for _, roleRef := range work.RoleInstanceRefs {
			role, ok := roles[roleRef]
			if !ok {
				continue
			}
			contribution := intersection(workObligations, stringSet(role.ObligationRefs))
			if len(contribution) == 0 {
				valid = false
			}
			for ref := range contribution {
				contributed[ref] = struct{}{}
			}
		}
		accountable, ok := roles[work.AccountableRoleInstanceRef]
		if !ok || len(intersection(workObligations, stringSet(accountable.ObligationRefs))) == 0 || !reflect.DeepEqual(workObligations, contributed) {
			valid = false
		}
		if !valid {
			add("RESPONSIBILITY_BINDING_INVALID")
		}
	}
}

func checkQualifications(snapshot *organizationSnapshot, plan *organizationPlan, obligations map[string]coverageObligation, add func(string)) {
	roles := map[string]roleDef{}
	principals := map[string]principal{}
	for _, role := range snapshot.Spec.RoleDefinitions {
		roles[role.RoleID] = role
	}
	for _, principal := range snapshot.Spec.Principals {
		principals[principal.PrincipalID] = principal
	}
	for _, instance := range plan.Spec.RoleInstances {
		role, roleOK := roles[instance.RoleDefinitionRef]
		principal, principalOK := principals[instance.PrincipalRef]
		valid := roleOK && principalOK
		if valid {
			valid = role.AdmissionStatus == "admitted" && principal.AdmissionStatus == "admitted" && principal.Status == "active" &&
				contains(principal.EligibleRoleRefs, role.RoleID) && subset(stringSet(role.RequiredQualifications), stringSet(principal.QualificationRefs)) &&
				equalStrings(instance.QualificationRefs, role.RequiredQualifications) && instance.Mission == role.Mission
		}
		obligationRoles := map[string]struct{}{}
		for _, ref := range instance.ObligationRefs {
			if obligation, ok := obligations[ref]; ok {
				obligationRoles[obligation.RequiredRoleRef] = struct{}{}
			}
		}
		if !valid || len(obligationRoles) != 1 {
			add("QUALIFICATION_INVALID")
			continue
		}
		if _, ok := obligationRoles[role.RoleID]; !ok {
			add("QUALIFICATION_INVALID")
		}
	}
}

func checkSeparation(snapshot *organizationSnapshot, plan *organizationPlan, add func(string)) {
	principalsByRole := map[string]map[string]struct{}{}
	for _, instance := range plan.Spec.RoleInstances {
		if principalsByRole[instance.RoleDefinitionRef] == nil {
			principalsByRole[instance.RoleDefinitionRef] = map[string]struct{}{}
		}
		principalsByRole[instance.RoleDefinitionRef][instance.PrincipalRef] = struct{}{}
	}
	for _, constraint := range snapshot.Spec.SeparationConstraints {
		if len(intersection(principalsByRole[constraint.LeftRoleRef], principalsByRole[constraint.RightRoleRef])) != 0 {
			add("SEPARATION_OF_DUTIES_VIOLATION")
		}
	}
}

func checkOrdering(plan *organizationPlan, report profileDerivationReport, add func(string)) {
	roleByInstance := map[string]string{}
	for _, role := range plan.Spec.RoleInstances {
		roleByInstance[role.RoleInstanceID] = role.RoleDefinitionRef
	}
	rolesByWork, obligationsByWork := map[string]map[string]struct{}{}, map[string]map[string]struct{}{}
	for _, work := range plan.Spec.WorkUnits {
		rolesByWork[work.WorkUnitID] = map[string]struct{}{}
		for _, ref := range work.RoleInstanceRefs {
			if role, ok := roleByInstance[ref]; ok {
				rolesByWork[work.WorkUnitID][role] = struct{}{}
			}
		}
		obligationsByWork[work.WorkUnitID] = stringSet(work.ObligationRefs)
	}
	obligationByID, obligationIDsByRole := map[string]coverageObligation{}, map[string]map[string]struct{}{}
	for _, obligation := range report.Obligations {
		obligationByID[obligation.ObligationID] = obligation
		if obligationIDsByRole[obligation.RequiredRoleRef] == nil {
			obligationIDsByRole[obligation.RequiredRoleRef] = map[string]struct{}{}
		}
		obligationIDsByRole[obligation.RequiredRoleRef][obligation.ObligationID] = struct{}{}
	}
	expectedPairsByOrder := map[rolePair]map[workPair]struct{}{}
	expectedReasons := map[rolePair]map[workPair][]string{}
	ordersByWorkPair := map[workPair]map[rolePair]struct{}{}
	unrepresentable := map[rolePair]struct{}{}
	for _, order := range report.RequiredOrders {
		pair := rolePair{order.PredecessorRoleRef, order.SuccessorRoleRef}
		reasons, invalid := expectedWorkPairReasons(order, obligationsByWork, obligationByID, obligationIDsByRole)
		expectedReasons[pair] = reasons
		expectedPairsByOrder[pair] = map[workPair]struct{}{}
		if invalid || len(reasons) == 0 {
			unrepresentable[pair] = struct{}{}
		}
		for work, reasonRefs := range reasons {
			expectedPairsByOrder[pair][work] = struct{}{}
			if ordersByWorkPair[work] == nil {
				ordersByWorkPair[work] = map[rolePair]struct{}{}
			}
			ordersByWorkPair[work][pair] = struct{}{}
			_ = reasonRefs
		}
	}
	covered := map[workPair]struct{}{}
	constraintCounts := map[string]int{}
	for _, edge := range plan.Spec.HappensBefore {
		relation := edge.Relation
		if relation == "" {
			relation = "must_complete_before"
		}
		constraintCounts[edge.PredecessorRef+"\x00"+edge.SuccessorRef+"\x00"+relation]++
	}
	for _, count := range constraintCounts {
		if count != 1 {
			add("ORDER_CONSTRAINT_INVALID")
		}
	}
	for _, edge := range plan.Spec.HappensBefore {
		leftRoles, leftOK := rolesByWork[edge.PredecessorRef]
		rightRoles, rightOK := rolesByWork[edge.SuccessorRef]
		if !leftOK || !rightOK {
			add("ORDER_CONSTRAINT_INVALID")
			continue
		}
		work := workPair{edge.PredecessorRef, edge.SuccessorRef}
		induced := map[rolePair]struct{}{}
		for left := range leftRoles {
			for right := range rightRoles {
				induced[rolePair{left, right}] = struct{}{}
			}
		}
		applicable := ordersByWorkPair[work]
		reasonSet := map[string]struct{}{}
		for pair := range applicable {
			for _, ref := range expectedReasons[pair][work] {
				reasonSet[ref] = struct{}{}
			}
		}
		expectedReasonRefs := sortedKeys(reasonSet)
		if len(applicable) != 0 && reflect.DeepEqual(induced, applicable) && reflect.DeepEqual(edge.ReasonRefs, expectedReasonRefs) {
			covered[work] = struct{}{}
		} else {
			add("ORDER_CONSTRAINT_INVALID")
		}
	}
	for _, order := range report.RequiredOrders {
		pair := rolePair{order.PredecessorRoleRef, order.SuccessorRoleRef}
		_, invalid := unrepresentable[pair]
		if !invalid {
			for expected := range expectedPairsByOrder[pair] {
				if _, ok := covered[expected]; !ok {
					invalid = true
					break
				}
			}
		}
		if invalid {
			add("ORDER_CONSTRAINT_MISSING")
			for _, ref := range order.ReasonRefs {
				if _, ok := obligationByID[ref]; ok {
					add("PREREQUISITE_OBLIGATION_ORDER_MISSING")
					break
				}
			}
		}
	}
	workEdges := make([]workPair, 0, len(plan.Spec.HappensBefore))
	for _, edge := range plan.Spec.HappensBefore {
		if _, l := rolesByWork[edge.PredecessorRef]; l {
			if _, r := rolesByWork[edge.SuccessorRef]; r {
				workEdges = append(workEdges, workPair{edge.PredecessorRef, edge.SuccessorRef})
			}
		}
	}
	roleEdges := make([]workPair, 0, len(report.RequiredOrders))
	for _, order := range report.RequiredOrders {
		roleEdges = append(roleEdges, workPair{order.PredecessorRoleRef, order.SuccessorRoleRef})
	}
	if hasCycle(workEdges) || hasCycle(roleEdges) {
		add("ORDER_CYCLE")
	}
}

func expectedWorkPairReasons(order derivationOrderRequirement, obligationsByWork map[string]map[string]struct{}, obligationByID map[string]coverageObligation, obligationIDsByRole map[string]map[string]struct{}) (map[workPair][]string, bool) {
	expected := map[workPair]map[string]struct{}{}
	unrepresentable := false
	bind := func(predecessors, successors map[string]struct{}, reasons []string) {
		leftWorks, rightWorks := map[string]struct{}{}, map[string]struct{}{}
		for work, refs := range obligationsByWork {
			if len(intersection(refs, predecessors)) != 0 {
				leftWorks[work] = struct{}{}
			}
			if len(intersection(refs, successors)) != 0 {
				rightWorks[work] = struct{}{}
			}
		}
		if len(leftWorks) == 0 || len(rightWorks) == 0 {
			unrepresentable = true
		}
		for left := range leftWorks {
			for right := range rightWorks {
				if left == right {
					unrepresentable = true
					continue
				}
				pair := workPair{left, right}
				if expected[pair] == nil {
					expected[pair] = map[string]struct{}{}
				}
				for _, reason := range reasons {
					expected[pair][reason] = struct{}{}
				}
			}
		}
	}
	if len(order.DependencyReasonRefs) != 0 {
		bind(obligationIDsByRole[order.PredecessorRoleRef], obligationIDsByRole[order.SuccessorRoleRef], order.DependencyReasonRefs)
	}
	for _, group := range order.PrerequisiteReasonGroups {
		left, right := map[string]struct{}{}, map[string]struct{}{}
		for _, ref := range group {
			if obligation, ok := obligationByID[ref]; ok {
				if obligation.RequiredRoleRef == order.PredecessorRoleRef {
					left[ref] = struct{}{}
				}
				if obligation.RequiredRoleRef == order.SuccessorRoleRef {
					right[ref] = struct{}{}
				}
			}
		}
		if len(left) == 0 || len(right) == 0 {
			unrepresentable = true
			continue
		}
		bind(left, right, group)
	}
	if len(order.DependencyReasonRefs) == 0 && len(order.PrerequisiteReasonGroups) == 0 {
		reasonObligations := map[string]struct{}{}
		for _, ref := range order.ReasonRefs {
			if _, ok := obligationByID[ref]; ok {
				reasonObligations[ref] = struct{}{}
			}
		}
		left, right := obligationIDsByRole[order.PredecessorRoleRef], obligationIDsByRole[order.SuccessorRoleRef]
		if len(reasonObligations) != 0 {
			left, right = map[string]struct{}{}, map[string]struct{}{}
			for ref := range reasonObligations {
				obligation := obligationByID[ref]
				if obligation.RequiredRoleRef == order.PredecessorRoleRef {
					left[ref] = struct{}{}
				}
				if obligation.RequiredRoleRef == order.SuccessorRoleRef {
					right[ref] = struct{}{}
				}
			}
		}
		bind(left, right, order.ReasonRefs)
	}
	result := map[workPair][]string{}
	for pair, reasons := range expected {
		result[pair] = sortedKeys(reasons)
	}
	return result, unrepresentable
}

func hasCycle(edges []workPair) bool {
	nodes, successors, indegree := map[string]struct{}{}, map[string]map[string]struct{}{}, map[string]int{}
	for _, edge := range edges {
		nodes[edge.left], nodes[edge.right] = struct{}{}, struct{}{}
		if successors[edge.left] == nil {
			successors[edge.left] = map[string]struct{}{}
		}
		if _, exists := successors[edge.left][edge.right]; !exists {
			successors[edge.left][edge.right] = struct{}{}
			indegree[edge.right]++
		}
	}
	queue := []string{}
	for node := range nodes {
		if indegree[node] == 0 {
			queue = append(queue, node)
		}
	}
	sort.Strings(queue)
	visited := 0
	for len(queue) != 0 {
		node := queue[0]
		queue = queue[1:]
		visited++
		for successor := range successors[node] {
			indegree[successor]--
			if indegree[successor] == 0 {
				queue = append(queue, successor)
				sort.Strings(queue)
			}
		}
	}
	return visited != len(nodes)
}

func checkEvidence(plan *organizationPlan, obligations map[string]coverageObligation, add func(string)) {
	for _, work := range plan.Spec.WorkUnits {
		required := map[string]struct{}{}
		for _, ref := range work.ObligationRefs {
			if obligation, ok := obligations[ref]; ok {
				for _, evidence := range obligation.RequiredEvidence {
					required[evidence] = struct{}{}
				}
			}
		}
		if !subset(required, stringSet(work.EvidenceOutputs)) {
			add("EVIDENCE_DUTY_MISSING")
		}
	}
}

func checkMinimality(snapshot *organizationSnapshot, plan *organizationPlan, obligations map[string]coverageObligation, add func(string)) {
	roleIDs, workIDs, referencedRoles := map[string]struct{}{}, map[string]struct{}{}, map[string]struct{}{}
	for _, role := range plan.Spec.RoleInstances {
		roleIDs[role.RoleInstanceID] = struct{}{}
	}
	for _, work := range plan.Spec.WorkUnits {
		workIDs[work.WorkUnitID] = struct{}{}
		for _, ref := range work.RoleInstanceRefs {
			referencedRoles[ref] = struct{}{}
		}
		if len(intersection(stringSet(work.ObligationRefs), obligationKeys(obligations))) == 0 {
			add("PLAN_NOT_MINIMAL")
		}
	}
	if !subset(roleIDs, referencedRoles) {
		add("PLAN_NOT_MINIMAL")
	}
	nonRemovable := map[string]struct{}{}
	for ref := range roleIDs {
		nonRemovable[ref] = struct{}{}
	}
	for ref := range workIDs {
		nonRemovable[ref] = struct{}{}
	}
	consideredRoles, consideredPrincipals := map[string]struct{}{}, map[string]struct{}{}
	for _, role := range snapshot.Spec.RoleDefinitions {
		consideredRoles[role.RoleID] = struct{}{}
	}
	for _, principal := range snapshot.Spec.Principals {
		consideredPrincipals[principal.PrincipalID] = struct{}{}
	}
	if plan.Spec.Minimality.Level != "inclusion_minimal" || !reflect.DeepEqual(stringSet(plan.Spec.Minimality.NonRemovableRefs), nonRemovable) ||
		!reflect.DeepEqual(stringSet(plan.Spec.Minimality.ConsideredRoleRefs), consideredRoles) || !reflect.DeepEqual(stringSet(plan.Spec.Minimality.ConsideredPrincipalRefs), consideredPrincipals) {
		add("PLAN_NOT_MINIMAL")
	}
}

func checkDecisions(snapshot *organizationSnapshot, plan *organizationPlan, add func(string)) {
	expectedSubjects := map[string]struct{}{}
	for _, role := range snapshot.Spec.RoleDefinitions {
		expectedSubjects[role.RoleID] = struct{}{}
	}
	for _, principal := range snapshot.Spec.Principals {
		expectedSubjects[principal.PrincipalID] = struct{}{}
	}
	for _, edge := range snapshot.Spec.parsedEdges {
		if edge.AdmissionStatus != "admitted" {
			expectedSubjects[edge.EdgeID] = struct{}{}
		}
	}
	for _, rule := range snapshot.Spec.parsedRules {
		if rule.AdmissionStatus != "admitted" {
			expectedSubjects[rule.RuleID] = struct{}{}
		}
	}
	for _, duty := range snapshot.Spec.UnknownTransitionDuties {
		if duty.AdmissionStatus != "admitted" {
			expectedSubjects[duty.DutyID] = struct{}{}
		}
	}
	decisions, actualSubjects := map[string]planDecision{}, map[string]struct{}{}
	for _, decision := range plan.Spec.Decisions {
		if _, duplicate := decisions[decision.SubjectRef]; duplicate {
			add("PLAN_NOT_MINIMAL")
		}
		decisions[decision.SubjectRef] = decision
		actualSubjects[decision.SubjectRef] = struct{}{}
	}
	if !reflect.DeepEqual(expectedSubjects, actualSubjects) {
		add("PLAN_NOT_MINIMAL")
	}
	selectedRoles, selectedPrincipals := map[string]struct{}{}, map[string]struct{}{}
	for _, instance := range plan.Spec.RoleInstances {
		selectedRoles[instance.RoleDefinitionRef] = struct{}{}
		selectedPrincipals[instance.PrincipalRef] = struct{}{}
	}
	for _, role := range snapshot.Spec.RoleDefinitions {
		_, selected := selectedRoles[role.RoleID]
		disposition, reason := "excluded", "NOT_REQUIRED_BY_CONTRACT"
		if selected {
			disposition, reason = "included", "ROLE_SELECTED_FOR_OBLIGATION"
		}
		if !decisionMatches(decisions[role.RoleID], role.AdmissionStatus, disposition, reason) {
			add("PLAN_NOT_MINIMAL")
		}
	}
	for _, principal := range snapshot.Spec.Principals {
		_, selected := selectedPrincipals[principal.PrincipalID]
		disposition, reason := "excluded", "PRINCIPAL_NOT_SELECTED"
		if selected {
			disposition, reason = "included", "PRINCIPAL_SELECTED_QUALIFIED"
		}
		if !decisionMatches(decisions[principal.PrincipalID], principal.AdmissionStatus, disposition, reason) {
			add("PLAN_NOT_MINIMAL")
		}
	}
	for _, edge := range snapshot.Spec.parsedEdges {
		if edge.AdmissionStatus == "admitted" {
			continue
		}
		disposition, reason := nonAuthorityDecision(edge.AdmissionStatus, "CANDIDATE_EDGE_NOT_AUTHORITY")
		if !decisionMatches(decisions[edge.EdgeID], edge.AdmissionStatus, disposition, reason) {
			add("PLAN_NOT_MINIMAL")
		}
	}
	for _, rule := range snapshot.Spec.parsedRules {
		if rule.AdmissionStatus == "admitted" {
			continue
		}
		disposition, reason := nonAuthorityDecision(rule.AdmissionStatus, "CANDIDATE_INPUT_NOT_AUTHORITY")
		if !decisionMatches(decisions[rule.RuleID], rule.AdmissionStatus, disposition, reason) {
			add("PLAN_NOT_MINIMAL")
		}
	}
	for _, duty := range snapshot.Spec.UnknownTransitionDuties {
		if duty.AdmissionStatus == "admitted" {
			continue
		}
		disposition, reason := nonAuthorityDecision(duty.AdmissionStatus, "CANDIDATE_INPUT_NOT_AUTHORITY")
		if !decisionMatches(decisions[duty.DutyID], duty.AdmissionStatus, disposition, reason) {
			add("PLAN_NOT_MINIMAL")
		}
	}
}

func nonAuthorityDecision(admission, candidateReason string) (string, string) {
	if admission == "retracted" {
		return "excluded", "RETRACTED_SOURCE_EXCLUDED"
	}
	return "unresolved", candidateReason
}
func decisionMatches(decision planDecision, admission, disposition, reason string) bool {
	return decision.SubjectRef != "" && decision.InputClass == admission && decision.Disposition == disposition && reflect.DeepEqual(decision.ReasonCodes, []string{reason})
}

func stringSet(values []string) map[string]struct{} {
	result := make(map[string]struct{}, len(values))
	for _, value := range values {
		result[value] = struct{}{}
	}
	return result
}
func intersection(left, right map[string]struct{}) map[string]struct{} {
	result := map[string]struct{}{}
	for value := range left {
		if _, ok := right[value]; ok {
			result[value] = struct{}{}
		}
	}
	return result
}
func subset(left, right map[string]struct{}) bool {
	for value := range left {
		if _, ok := right[value]; !ok {
			return false
		}
	}
	return true
}
func obligationKeys(obligations map[string]coverageObligation) map[string]struct{} {
	result := map[string]struct{}{}
	for ref := range obligations {
		result[ref] = struct{}{}
	}
	return result
}
func sortedKeys(values map[string]struct{}) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
func sortedCopy(values []string) []string {
	result := append([]string(nil), values...)
	sort.Strings(result)
	return result
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}
