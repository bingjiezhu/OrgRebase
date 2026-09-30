package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"reflect"
	"testing"
)

// These tables are the public Strong Kleene relation, independent of either evaluator.
var semanticTruthValues = []string{"TRUE", "FALSE", "UNKNOWN"}
var semanticTruthTables = map[string][3][3]string{
	"all": {{"TRUE", "FALSE", "UNKNOWN"}, {"FALSE", "FALSE", "FALSE"}, {"UNKNOWN", "FALSE", "UNKNOWN"}},
	"any": {{"TRUE", "TRUE", "TRUE"}, {"TRUE", "FALSE", "UNKNOWN"}, {"TRUE", "UNKNOWN", "UNKNOWN"}},
}

func TestScopeSelectorCompleteTruthTables(t *testing.T) {
	for _, operator := range []string{"all", "any"} {
		for i, left := range semanticTruthValues {
			for j, right := range semanticTruthValues {
				t.Run(operator+"/"+left+"/"+right, func(t *testing.T) {
					snapshot, change := semanticRoots(t, func(spec map[string]any) {
						for index, value := range []string{left, right} {
							admission := "admitted"
							if value == "UNKNOWN" {
								admission = "candidate"
							}
							addSemanticNode(spec, fmt.Sprintf("scope:%d", index), "role:quality-qualification", admission)
						}
						predicate := semanticPredicate()
						predicate["scopeSelector"] = map[string]any{"refs": []any{"scope:0", "scope:1"}, "matchMode": operator, "missingBehavior": "false"}
						spec["impactRules"] = []any{semanticRule("rule:scope", "alternative:acieries-savoie", "role:quality-qualification", "evidence:scope", predicate)}
					}, func(spec map[string]any) {
						refs := []any{"alternative:acieries-savoie"}
						for index, value := range []string{left, right} {
							if value != "FALSE" {
								refs = append(refs, fmt.Sprintf("scope:%d", index))
							}
						}
						spec["scopeRefs"] = refs
					})
					report := semanticReport(t, snapshot, change)
					evaluation := semanticEvaluation(t, report, "rule:scope")
					expected := semanticTruthTables[operator][i][j]
					if operator == "any" && left == "TRUE" && right == "UNKNOWN" {
						logSemanticEvidence(t, snapshot, change, report)
					}
					if evaluation.Result != expected {
						t.Fatalf("scope %s(%s,%s) = %s, want %s", operator, left, right, evaluation.Result, expected)
					}
					reasons := []string{}
					if expected == "FALSE" {
						reasons = []string{"APPLICABILITY_SCOPE_MISMATCH"}
					}
					if expected == "UNKNOWN" {
						reasons = []string{"CANDIDATE_INPUT_NOT_AUTHORITY"}
						if operator == "any" && (left == "FALSE" || right == "FALSE") {
							reasons = []string{"APPLICABILITY_SCOPE_MISMATCH"}
						}
					}
					if !reflect.DeepEqual(evaluation.ReasonCodes, reasons) {
						t.Fatalf("scope reasons = %q, want %q", evaluation.ReasonCodes, reasons)
					}
					if !contains(evaluation.WitnessRefs, witness("change:SC-008", "/spec/scopeRefs")) {
						t.Fatal("scope membership witness missing")
					}
					paths := 0
					for _, path := range report.ImpactPaths {
						if contains(path.RuleRefs, "rule:scope") {
							paths++
							want := "affected"
							if expected == "UNKNOWN" {
								want = "unknown"
							}
							if path.State != want || !contains(path.EvaluationRefs, evaluation.EvaluationID) {
								t.Fatalf("scope evaluation not preserved by path: %#v", path)
							}
						}
					}
					wantPaths := 1
					if expected == "FALSE" {
						wantPaths = 0
					}
					if paths != wantPaths {
						t.Fatalf("materialized rule paths = %d, want %d", paths, wantPaths)
					}
				})
			}
		}
	}
}

func TestObligationGroupingPreservesEmbeddedNULIdentities(t *testing.T) {
	targets, roles := []string{"target:a\x00b", "target:a"}, []string{"role:c", "b\x00role:c"}
	snapshot, change := semanticRoots(t, func(spec map[string]any) {
		rules := []any{}
		for index := range targets {
			addSemanticRole(spec, roles[index])
			addSemanticNode(spec, targets[index], roles[index], "admitted")
			rules = append(rules, semanticRule(fmt.Sprintf("rule:group:%d", index), targets[index], roles[index], fmt.Sprintf("evidence:group:%d", index), semanticPredicate()))
		}
		spec["impactRules"] = rules
	}, nil)
	report := semanticReport(t, snapshot, change)
	logSemanticEvidence(t, snapshot, change, report)
	if len(report.Obligations) != 2 {
		t.Fatalf("distinct semantic tuples produced %d obligations, want 2", len(report.Obligations))
	}
	for index := range targets {
		found := false
		for _, obligation := range report.Obligations {
			if obligation.TargetRef != targets[index] || obligation.RequiredRoleRef != roles[index] {
				continue
			}
			found = true
			if !reflect.DeepEqual(obligation.RequiredEvidence, []string{fmt.Sprintf("evidence:group:%d", index)}) || len(obligation.PathRefs) != 1 {
				t.Fatalf("evidence crossed semantic tuples: %#v", obligation)
			}
			for _, path := range report.ImpactPaths {
				if path.PathID == obligation.PathRefs[0] && (path.TargetRef != targets[index] || !contains(path.RuleRefs, fmt.Sprintf("rule:group:%d", index))) {
					t.Fatalf("obligation borrowed another tuple's path: %#v", obligation)
				}
			}
		}
		if !found {
			t.Fatalf("obligation for target %q and role %q missing", targets[index], roles[index])
		}
	}
}

func TestRequiredOrdersPreserveEmbeddedNULRolePairs(t *testing.T) {
	roles := []string{"role:a\x00b", "role:c", "role:a", "b\x00role:c"}
	snapshot, change := semanticRoots(t, func(spec map[string]any) {
		for index, role := range roles {
			addSemanticRole(spec, role)
			addSemanticNode(spec, fmt.Sprintf("node:order:%d", index), role, "admitted")
		}
		edges := []any{}
		for index, pair := range [][2]string{{"alternative:acieries-savoie", "node:order:0"}, {"node:order:0", "node:order:1"}, {"alternative:acieries-savoie", "node:order:2"}, {"node:order:2", "node:order:3"}} {
			edges = append(edges, map[string]any{"edgeId": fmt.Sprintf("edge:order:%d", index), "sourceRef": pair[0], "targetRef": pair[1], "relationType": "business_dependency", "admissionStatus": "admitted", "transferPredicate": semanticPredicate()})
		}
		spec["dependencyEdges"] = edges
	}, nil)
	report := semanticReport(t, snapshot, change)
	logSemanticEvidence(t, snapshot, change, report)
	if len(report.RequiredOrders) != 4 {
		t.Fatalf("distinct role pairs produced %d required orders, want 4", len(report.RequiredOrders))
	}
	for _, index := range []int{0, 2} {
		found := false
		for _, order := range report.RequiredOrders {
			if order.PredecessorRoleRef != roles[index] || order.SuccessorRoleRef != roles[index+1] {
				continue
			}
			found = true
			if !order.RoleWide || len(order.DependencyReasonRefs) != 1 {
				t.Fatalf("dependency reasons crossed role pairs: %#v", order)
			}
			for _, path := range report.ImpactPaths {
				if path.PathID == order.DependencyReasonRefs[0] && path.TargetRef != fmt.Sprintf("node:order:%d", index+1) {
					t.Fatalf("order borrowed another pair's path: %#v", order)
				}
			}
		}
		if !found {
			t.Fatalf("required role pair %q -> %q missing", roles[index], roles[index+1])
		}
	}
}

func semanticRoots(t *testing.T, editSnapshot, editChange func(map[string]any)) ([]byte, []byte) {
	t.Helper()
	snapshot, change := capsuleCaseBytes(t, "SC-008")
	snapshot = mutateAndSeal(t, snapshot, func(root map[string]any) {
		spec := root["spec"].(map[string]any)
		spec["dependencyEdges"], spec["impactRules"], spec["unknownTransitionDuties"] = []any{}, []any{}, []any{}
		for _, item := range spec["nodes"].([]any) {
			item.(map[string]any)["admissionStatus"] = "admitted"
		}
		editSnapshot(spec)
	}, true)
	if editChange != nil {
		change = mutateAndSeal(t, change, func(root map[string]any) { editChange(root["spec"].(map[string]any)) }, true)
	}
	return snapshot, change
}

func semanticPredicate() map[string]any {
	return map[string]any{"predicateVersion": "oac.supplier.applicability/v0.2", "semanticType": "supplier.status", "afterState": "known", "afterValues": []any{"qualified"}}
}

func semanticRule(id, target, role, evidence string, predicate map[string]any) map[string]any {
	return map[string]any{"ruleId": id, "targetRef": target, "requiredRoleRef": role, "obligationType": "review", "requiredEvidence": []any{evidence}, "admissionStatus": "admitted", "applicability": predicate}
}

func addSemanticRole(spec map[string]any, role string) {
	spec["roleDefinitions"] = append(spec["roleDefinitions"].([]any), map[string]any{"roleId": role, "domainRef": "domain:test", "mission": "review the declared evidence", "responsibilityTypes": []any{"review"}, "requiredQualifications": []any{}, "effectCeiling": "zero_effect", "admissionStatus": "admitted"})
}

func addSemanticNode(spec map[string]any, id, role, admission string) {
	spec["nodes"] = append(spec["nodes"].([]any), map[string]any{"nodeId": id, "nodeType": "test-subject", "domainRef": "domain:test", "ownerRoleRef": role, "admissionStatus": admission})
	coverage := spec["completeness"].(map[string]any)
	coverage["coveredNodeRefs"] = append(coverage["coveredNodeRefs"].([]any), id)
}

func semanticEvaluation(t *testing.T, report profileDerivationReport, source string) applicabilityEvaluation {
	t.Helper()
	for _, evaluation := range report.ApplicabilityEvaluations {
		if evaluation.SourceRef == source {
			return evaluation
		}
	}
	t.Fatalf("evaluation missing for %s", source)
	return applicabilityEvaluation{}
}

func semanticReport(t *testing.T, snapshot, change []byte) profileDerivationReport {
	t.Helper()
	for _, input := range []struct {
		kind string
		raw  []byte
	}{{"OrganizationSnapshot", snapshot}, {"SemanticChangeSet", change}} {
		response := validateWire(t, input.kind, input.raw)
		if response.SUTStatus != statusCompleted {
			t.Fatalf("valid counterexample rejected at %s admission: %#v", input.kind, response.Error)
		}
	}
	response := deriveWire(t, snapshot, change)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("derive rejected admitted counterexample: %#v", response.Error)
	}
	result := response.Result.(deriveResult)
	raw, err := json.Marshal(result.Report)
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := canonicalizeJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(canonical)
	digest := "sha256:" + hex.EncodeToString(sum[:])
	if digest != result.ReportDigest {
		t.Fatalf("report digest = %s, want %s", result.ReportDigest, digest)
	}
	repeated := deriveWire(t, snapshot, change)
	if !reflect.DeepEqual(response.Result, repeated.Result) {
		t.Fatal("same roots produced different canonical report")
	}
	return result.Report
}

func logSemanticEvidence(t *testing.T, snapshot, change []byte, report profileDerivationReport) {
	t.Helper()
	raw, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("counterexample snapshot=%s change=%s report=%s", snapshot, change, raw)
}
