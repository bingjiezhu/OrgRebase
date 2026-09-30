package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestPlanVerifyCapabilityUsesOnlyTheStdioV3Track(t *testing.T) {
	v3 := handlePlanVerifyWireRequest([]byte(`{"protocolVersion":"oac.ctk.stdio/v3","requestId":"caps","operation":"capabilities","payload":{}}`))
	if v3.SUTStatus != statusCompleted || v3.ProtocolVersion != planVerifyProtocolVersion {
		t.Fatalf("v3 capability failed: %#v", v3)
	}
	capability, ok := v3.Result.(capabilityResult)
	if !ok || capability.ImplementationID != "oac.supplier.go.internal.plan-verifier" || len(capability.Tracks) != 1 {
		t.Fatalf("unexpected v3 capability: %#v", v3.Result)
	}
	wantedTrack := capabilityTrack{
		Role: "plan-verifier", Operation: "verifyPlan",
		ProfileID:      "oac.supplier.plan-verification.plural-capsule",
		ProfileVersion: "v0.1-seed-1", WireVersion: planVerifyProtocolVersion,
	}
	if capability.Tracks[0] != wantedTrack {
		t.Fatalf("unexpected v3 track: %#v", capability.Tracks[0])
	}
}

func TestFixedPlanVerifierAcceptsSC008AndPreservesSC010Unknown(t *testing.T) {
	snapshot, change, plan := loadPlanCase(t, "veracier-proc01-contextual.snapshot.json", "SC-008.change.json", "SC-008-contextual.plan.json")
	report, err := deriveProfile(snapshot, change)
	if err != nil {
		t.Fatalf("SC-008 derivation failed: %v", err)
	}
	verdict, reasons := verifyFixedPlan(snapshot, change, plan, report)
	if verdict != "ACCEPT" || len(reasons) != 0 {
		t.Fatalf("SC-008 = %s %v, want ACCEPT []", verdict, reasons)
	}

	snapshot, change, plan = loadPlanCase(t, "veracier-proc01-truncated-contextual.snapshot.json", "SC-010.change.json", "SC-010-unknown.plan.json")
	report, err = deriveProfile(snapshot, change)
	if err != nil {
		t.Fatalf("SC-010 derivation failed: %v", err)
	}
	verdict, reasons = verifyFixedPlan(snapshot, change, plan, report)
	if verdict != "UNKNOWN" || !reflect.DeepEqual(reasons, []string{"ROOT_APPLICABILITY_UNKNOWN"}) {
		t.Fatalf("SC-010 = %s %v, want UNKNOWN [ROOT_APPLICABILITY_UNKNOWN]", verdict, reasons)
	}
}

func TestFixedPlanVerifierRejectsMajorSemanticMutations(t *testing.T) {
	snapshot, change, base := loadPlanCase(t, "veracier-proc01-contextual.snapshot.json", "SC-008.change.json", "SC-008-contextual.plan.json")
	report, err := deriveProfile(snapshot, change)
	if err != nil {
		t.Fatalf("SC-008 derivation failed: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*organizationPlan)
		code   string
	}{
		{
			name: "missing-evidence",
			mutate: func(plan *organizationPlan) {
				plan.Spec.WorkUnits[0].EvidenceOutputs = []string{"urn:evidence:deliberately-wrong"}
			},
			code: "EVIDENCE_DUTY_MISSING",
		},
		{
			name: "missing-order",
			mutate: func(plan *organizationPlan) {
				plan.Spec.HappensBefore = append([]orderConstraint(nil), plan.Spec.HappensBefore[1:]...)
			},
			code: "ORDER_CONSTRAINT_MISSING",
		},
		{
			name: "unqualified-principal",
			mutate: func(plan *organizationPlan) {
				plan.Spec.RoleInstances[0].PrincipalRef = "principal:procurement-alex"
			},
			code: "QUALIFICATION_INVALID",
		},
		{
			name: "obligation-omission",
			mutate: func(plan *organizationPlan) {
				plan.Spec.Obligations = append([]coverageObligation(nil), plan.Spec.Obligations[1:]...)
			},
			code: "OBLIGATION_SET_MISMATCH",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			plan := clonePlan(t, base)
			test.mutate(plan)
			verdict, reasons := verifyFixedPlan(snapshot, change, plan, report)
			if verdict != "REJECT" || !contains(reasons, test.code) {
				t.Fatalf("mutation = %s %v, want REJECT containing %s", verdict, reasons, test.code)
			}
		})
	}
}

func TestFixedPlanVerifierRejectsUnknownErasure(t *testing.T) {
	snapshot, change, plan := loadPlanCase(t, "veracier-proc01-truncated-contextual.snapshot.json", "SC-010.change.json", "SC-010-unknown.plan.json")
	report, err := deriveProfile(snapshot, change)
	if err != nil {
		t.Fatalf("SC-010 derivation failed: %v", err)
	}
	plan.Spec.UnresolvedRefs = []string{}
	plan.Spec.Status = "planned"
	verdict, reasons := verifyFixedPlan(snapshot, change, plan, report)
	if verdict != "REJECT" || !contains(reasons, "UNKNOWN_NOT_PRESERVED") {
		t.Fatalf("unknown erasure = %s %v", verdict, reasons)
	}
}

func TestPlanAdmissionRejectsDuplicateOrderConstraint(t *testing.T) {
	_, _, plan := loadPlanCase(t, "veracier-proc01-contextual.snapshot.json", "SC-008.change.json", "SC-008-contextual.plan.json")
	plan.Spec.HappensBefore = append(
		plan.Spec.HappensBefore,
		plan.Spec.HappensBefore[0],
	)
	if err := validatePlanCollections(&plan.Spec); err == nil || err.Error() != "duplicate plan order constraint" {
		t.Fatalf("duplicate order admission error = %v", err)
	}
}

func loadPlanCase(t *testing.T, snapshotName, changeName, planName string) (*organizationSnapshot, *semanticChangeSet, *organizationPlan) {
	t.Helper()
	root := filepath.Join("..", "..")
	var snapshot organizationSnapshot
	decodeFixture(t, filepath.Join(root, "profiles", "supplier-change", "inputs", snapshotName), &snapshot)
	if err := validateSnapshot(&snapshot); err != nil {
		t.Fatalf("validate snapshot: %v", err)
	}
	var change semanticChangeSet
	decodeFixture(t, filepath.Join(root, "profiles", "supplier-change", "inputs", changeName), &change)
	for index := range change.Spec.Deltas {
		normalizeObservedBinary64(&change.Spec.Deltas[index].Before)
		normalizeObservedBinary64(&change.Spec.Deltas[index].After)
	}
	if err := validateChange(&change); err != nil {
		t.Fatalf("validate change: %v", err)
	}
	var plan organizationPlan
	decodeFixture(t, filepath.Join(root, "tck", "fixtures", "positive", planName), &plan)
	return &snapshot, &change, &plan
}

func decodeFixture(t *testing.T, path string, destination any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := decodeClosed(raw, destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func clonePlan(t *testing.T, source *organizationPlan) *organizationPlan {
	t.Helper()
	raw, err := json.Marshal(source)
	if err != nil {
		t.Fatal(err)
	}
	var clone organizationPlan
	if err := json.Unmarshal(raw, &clone); err != nil {
		t.Fatal(err)
	}
	return &clone
}
