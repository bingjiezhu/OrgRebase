package main

import (
	"strings"
	"testing"
)

func capsuleCaseBytes(t *testing.T, caseID string) ([]byte, []byte) {
	t.Helper()
	directory, capsule := loadCapsule(t)
	for _, testCase := range capsule.Cases {
		if testCase.CaseID == caseID {
			return mustRead(t, directory+"/"+testCase.Snapshot.Path), mustRead(t, directory+"/"+testCase.Change.Path)
		}
	}
	t.Fatalf("capsule case %s not found", caseID)
	return nil, nil
}

func admittedPair(t *testing.T, snapshotRaw, changeRaw []byte) (*organizationSnapshot, *semanticChangeSet) {
	t.Helper()
	snapshotAdmission, err := admitResource(snapshotRaw, "OrganizationSnapshot")
	if err != nil {
		t.Fatalf("snapshot admission failed: %v", err)
	}
	changeAdmission, err := admitResource(changeRaw, "SemanticChangeSet")
	if err != nil {
		t.Fatalf("change admission failed: %v", err)
	}
	return snapshotAdmission.Snapshot, changeAdmission.Change
}

func internalDerivation(snapshot *organizationSnapshot, change *semanticChangeSet) *derivation {
	return &derivation{
		snapshot:     snapshot,
		change:       change,
		evaluations:  make(map[string]applicabilityEvaluation),
		evalBySource: make(map[string]applicabilityEvaluation),
		paths:        make(map[string]impactPath),
	}
}

func TestSemanticMatrixRetractedRootFailsBeforeDerivation(t *testing.T) {
	snapshot, change := capsuleCaseBytes(t, "SC-009")
	retracted := mutateAndSeal(t, snapshot, func(root map[string]any) {
		for _, item := range root["spec"].(map[string]any)["nodes"].([]any) {
			node := item.(map[string]any)
			if node["nodeId"] == "supplier:forges-martelliere" {
				node["admissionStatus"] = "retracted"
			}
		}
	}, true)
	assertWireError(t, deriveWire(t, retracted, change), statusError, codeRetractedSource)
}

func TestSemanticMatrixRetractedSourcesRemainFalseLedgerOnly(t *testing.T) {
	snapshotRaw, changeRaw := capsuleCaseBytes(t, "SC-009")
	tests := []struct {
		name      string
		arrayName string
		idName    string
		sourceID  string
	}{
		{"edge", "dependencyEdges", "edgeId", "edge:contextual-supplier-nuclear-order"},
		{"rule", "impactRules", "ruleId", "rule:contextual-nuclear-finance-negative-control"},
		{"duty", "unknownTransitionDuties", "dutyId", "duty:contextual-nuclear-compliance-discovery"},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			mutated := mutateAndSeal(t, snapshotRaw, func(root map[string]any) {
				for _, item := range root["spec"].(map[string]any)[testCase.arrayName].([]any) {
					source := item.(map[string]any)
					if source[testCase.idName] == testCase.sourceID {
						source["admissionStatus"] = "retracted"
					}
				}
			}, true)
			snapshot, change := admittedPair(t, mutated, changeRaw)
			d := internalDerivation(snapshot, change)
			if err := d.evaluateAllSources(); err != nil {
				t.Fatal(err)
			}
			evaluation, ok := d.evalBySource[testCase.sourceID]
			if !ok || evaluation.Result != "FALSE" || !contains(evaluation.ReasonCodes, "RETRACTED_SOURCE_EXCLUDED") {
				t.Fatalf("retracted %s evaluation = %#v", testCase.name, evaluation)
			}
			if err := d.validateFiringUnknownTransitionDuties(); err != nil {
				t.Fatal(err)
			}
			if err := d.materializeClosure(false); err != nil {
				t.Fatal(err)
			}
			for _, path := range d.paths {
				if contains(path.EdgeRefs, testCase.sourceID) || contains(path.RuleRefs, testCase.sourceID) || contains(path.DutyRefs, testCase.sourceID) {
					t.Fatalf("retracted %s materialized path %#v", testCase.name, path)
				}
			}
		})
	}
}

func TestSemanticMatrixCandidateAndDisputedRootsStayUnknown(t *testing.T) {
	snapshotRaw, change := capsuleCaseBytes(t, "SC-009")
	for _, admission := range []string{"candidate", "disputed"} {
		t.Run(admission, func(t *testing.T) {
			mutated := mutateAndSeal(t, snapshotRaw, func(root map[string]any) {
				spec := root["spec"].(map[string]any)
				for _, item := range spec["nodes"].([]any) {
					node := item.(map[string]any)
					if node["nodeId"] == "supplier:forges-martelliere" {
						node["admissionStatus"] = admission
					}
				}
				for _, item := range spec["impactRules"].([]any) {
					delete(item.(map[string]any), "prerequisiteObligationTypes")
				}
			}, true)
			response := deriveWire(t, mutated, change)
			if response.SUTStatus != statusCompleted {
				t.Fatalf("derive failed: %#v", response.Error)
			}
			report := response.Result.(deriveResult).Report
			if !report.RootApplicabilityUnknown || !contains(report.UnresolvedRefs, "change:SC-009") {
				t.Fatalf("candidate root projection is not Unknown: %#v", report.UnresolvedRefs)
			}
			for _, evaluation := range report.ApplicabilityEvaluations {
				for _, witnessRef := range evaluation.WitnessRefs {
					if strings.Contains(witnessRef, "/spec/nodes/0/") && (strings.HasSuffix(witnessRef, "/nodeType") || strings.HasSuffix(witnessRef, "/domainRef")) {
						t.Fatalf("non-authoritative subject attribute leaked into witness identity: %s", witnessRef)
					}
				}
			}
			for _, path := range report.ImpactPaths {
				if path.State == "affected" && (path.TargetRef == "supplier:forges-martelliere" || len(path.RuleRefs) > 0) {
					t.Fatalf("non-authoritative root was upgraded by path %#v", path)
				}
			}
		})
	}
}

func TestSemanticMatrixCompleteBoundaryResiduals(t *testing.T) {
	snapshotRaw, change := capsuleCaseBytes(t, "SC-009")
	t.Run("authoritative false cut can prove candidate target", func(t *testing.T) {
		mutated := mutateAndSeal(t, snapshotRaw, func(root map[string]any) {
			setNodeAdmission(root, "contract:forges-msa", "candidate")
		}, true)
		report := completedReport(t, mutated, change)
		path := requireTargetPath(t, report, "contract:forges-msa")
		if path.State != "unaffected_proven" || path.Origin != "bounded_non_impact" || len(path.EvaluationRefs) == 0 {
			t.Fatalf("candidate target false-cut proof = %#v", path)
		}
	})
	t.Run("candidate target without a route remains unknown", func(t *testing.T) {
		mutated := mutateAndSeal(t, snapshotRaw, func(root map[string]any) {
			setNodeAdmission(root, "order:aero-av3000", "candidate")
		}, true)
		path := requireTargetPath(t, completedReport(t, mutated, change), "order:aero-av3000")
		if path.State != "unknown" || !contains(path.ReasonCodes, "CANDIDATE_INPUT_NOT_AUTHORITY") {
			t.Fatalf("candidate residual = %#v", path)
		}
	})
	t.Run("retracted residual is explicitly excluded", func(t *testing.T) {
		mutated := mutateAndSeal(t, snapshotRaw, func(root map[string]any) {
			setNodeAdmission(root, "order:aero-av3000", "retracted")
		}, true)
		path := requireTargetPath(t, completedReport(t, mutated, change), "order:aero-av3000")
		if path.State != "out_of_declared_scope" || path.Origin != "excluded" || !contains(path.ReasonCodes, "RETRACTED_SOURCE_EXCLUDED") {
			t.Fatalf("retracted residual = %#v", path)
		}
	})
	t.Run("continuations beyond maxDepth stay unknown", func(t *testing.T) {
		mutated := mutateAndSeal(t, snapshotRaw, func(root map[string]any) {
			spec := root["spec"].(map[string]any)
			spec["completeness"].(map[string]any)["maxDepth"] = 1
			for _, item := range spec["impactRules"].([]any) {
				item.(map[string]any)["admissionStatus"] = "retracted"
			}
			for _, item := range spec["unknownTransitionDuties"].([]any) {
				item.(map[string]any)["admissionStatus"] = "retracted"
			}
		}, true)
		path := requireTargetPath(t, completedReport(t, mutated, change), "production:energy")
		if path.State != "unknown" || !contains(path.ReasonCodes, "IMPACT_SEARCH_TRUNCATED") {
			t.Fatalf("truncated continuation residual = %#v", path)
		}
	})
}

func TestSemanticMatrixBoundaryFallbackAndReferencedUnresolved(t *testing.T) {
	t.Run("implicit partial boundary uses canonical discovery contract", func(t *testing.T) {
		snapshot, change := capsuleCaseBytes(t, "SC-008")
		partial := mutateAndSeal(t, snapshot, func(root map[string]any) {
			root["spec"].(map[string]any)["completeness"].(map[string]any)["status"] = "partial"
		}, true)
		report := completedReport(t, partial, change)
		path := requireTargetPath(t, report, "urn:oac:boundary:unobserved")
		if path.State != "unknown" || !contains(path.ReasonCodes, "GRAPH_COVERAGE_PARTIAL") {
			t.Fatalf("implicit boundary path = %#v", path)
		}
		found := false
		for _, obligation := range report.Obligations {
			if obligation.TargetRef == path.TargetRef && obligation.ObligationType == "discover_dependency" && contains(obligation.RequiredEvidence, "dependency_admission_decision") {
				found = true
			}
		}
		if !found {
			t.Fatal("canonical legacy discovery obligation was not emitted")
		}
	})

	t.Run("unrelated unknown evaluation is not unresolved", func(t *testing.T) {
		snapshot, change := capsuleCaseBytes(t, "SC-008")
		mutated := mutateAndSeal(t, snapshot, func(root map[string]any) {
			edge := root["spec"].(map[string]any)["dependencyEdges"].([]any)[0].(map[string]any)
			edge["relationType"] = "traceability"
			edge["admissionStatus"] = "candidate"
			edge["transferPredicate"].(map[string]any)["relationTypes"] = []any{"traceability"}
		}, true)
		report := completedReport(t, mutated, change)
		var evaluationID string
		for _, evaluation := range report.ApplicabilityEvaluations {
			if evaluation.SourceRef == "edge:contextual-alternative-aero" {
				if evaluation.Result != "UNKNOWN" {
					t.Fatalf("negative-control evaluation = %#v", evaluation)
				}
				evaluationID = evaluation.EvaluationID
			}
		}
		if evaluationID == "" || contains(report.UnresolvedRefs, evaluationID) {
			t.Fatalf("unreferenced Unknown evaluation leaked into unresolvedRefs: %s", evaluationID)
		}
	})
}

func TestSemanticMatrixInvalidDutyRolePrecedesPrerequisiteFailure(t *testing.T) {
	snapshot, change := capsuleCaseBytes(t, "SC-009")
	mutated := mutateAndSeal(t, snapshot, func(root map[string]any) {
		for _, item := range root["spec"].(map[string]any)["roleDefinitions"].([]any) {
			role := item.(map[string]any)
			if role["roleId"] == "role:compliance-reviewer" {
				role["admissionStatus"] = "candidate"
			}
		}
	}, true)
	assertWireError(t, deriveWire(t, mutated, change), statusError, codeUnknownDutyInvalid)
}

func setNodeAdmission(root map[string]any, nodeID, admission string) {
	for _, item := range root["spec"].(map[string]any)["nodes"].([]any) {
		node := item.(map[string]any)
		if node["nodeId"] == nodeID {
			node["admissionStatus"] = admission
			return
		}
	}
}

func completedReport(t *testing.T, snapshot, change []byte) profileDerivationReport {
	t.Helper()
	response := deriveWire(t, snapshot, change)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("derive failed: %#v", response.Error)
	}
	return response.Result.(deriveResult).Report
}

func requireTargetPath(t *testing.T, report profileDerivationReport, targetRef string) impactPath {
	t.Helper()
	for _, path := range report.ImpactPaths {
		if path.TargetRef == targetRef {
			return path
		}
	}
	t.Fatalf("no impact path for %s", targetRef)
	return impactPath{}
}
