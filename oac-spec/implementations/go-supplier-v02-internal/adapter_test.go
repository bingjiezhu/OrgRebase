package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type capsuleManifest struct {
	Cases []struct {
		CaseID   string `json:"caseId"`
		Snapshot struct {
			Path string `json:"path"`
		} `json:"snapshot"`
		Change struct {
			Path string `json:"path"`
		} `json:"change"`
		Expected struct {
			Path         string `json:"path"`
			ReportDigest string `json:"reportDigest"`
		} `json:"expected"`
	} `json:"cases"`
}

func capsuleDirectory(t *testing.T) string {
	t.Helper()
	path, err := filepath.Abs(filepath.Join("..", "..", "experiments", "supplier-v02-portability"))
	if err != nil {
		t.Fatal(err)
	}
	return path
}

func loadCapsule(t *testing.T) (string, capsuleManifest) {
	t.Helper()
	directory := capsuleDirectory(t)
	raw, err := os.ReadFile(filepath.Join(directory, "capsule.json"))
	if err != nil {
		t.Fatal(err)
	}
	var capsule capsuleManifest
	if err := json.Unmarshal(raw, &capsule); err != nil {
		t.Fatal(err)
	}
	return directory, capsule
}

func TestPublicCapsuleReportsMatchExactly(t *testing.T) {
	directory, capsule := loadCapsule(t)
	if len(capsule.Cases) != 3 {
		t.Fatalf("public capsule has %d cases, want 3", len(capsule.Cases))
	}
	for _, testCase := range capsule.Cases {
		t.Run(testCase.CaseID, func(t *testing.T) {
			snapshot := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Snapshot.Path)))
			change := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Change.Path)))
			response := deriveWire(t, snapshot, change)
			if response.SUTStatus != statusCompleted {
				t.Fatalf("derive failed: %#v", response.Error)
			}
			result, ok := response.Result.(deriveResult)
			if !ok {
				t.Fatalf("unexpected result type %T", response.Result)
			}
			if result.ReportDigest != testCase.Expected.ReportDigest {
				t.Fatalf("report digest = %s, want %s", result.ReportDigest, testCase.Expected.ReportDigest)
			}
			expectedRaw := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Expected.Path)))
			actualRaw, err := json.Marshal(result.Report)
			if err != nil {
				t.Fatal(err)
			}
			expectedJCS, err := canonicalizeJSON(expectedRaw)
			if err != nil {
				t.Fatal(err)
			}
			actualJCS, err := canonicalizeJSON(actualRaw)
			if err != nil {
				t.Fatal(err)
			}
			if string(actualJCS) != string(expectedJCS) {
				t.Fatal("report JCS bytes differ from the public expected report")
			}
		})
	}
}

func TestRawAdmissionAndDigestDiscriminators(t *testing.T) {
	directory, capsule := loadCapsule(t)
	testCase := capsule.Cases[0]
	snapshot := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Snapshot.Path)))
	change := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Change.Path)))

	valid := validateWire(t, "OrganizationSnapshot", snapshot)
	if valid.SUTStatus != statusCompleted {
		t.Fatalf("valid raw snapshot rejected: %#v", valid.Error)
	}

	duplicate := strings.Replace(string(change), `"kind":"SemanticChangeSet"`, `"kind":"SemanticChangeSet","kind":"SemanticChangeSet"`, 1)
	response := validateWire(t, "SemanticChangeSet", []byte(duplicate))
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)

	badDigest := mutateAndSeal(t, change, func(root map[string]any) {
		root["digest"] = "sha256:" + strings.Repeat("0", 64)
	}, false)
	response = validateWire(t, "SemanticChangeSet", badDigest)
	assertWireError(t, response, statusError, codeRootDigestMismatch)

	extra := mutateAndSeal(t, change, func(root map[string]any) { root["unowned"] = true }, true)
	response = validateWire(t, "SemanticChangeSet", extra)
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)

	candidateChange := mutateAndSeal(t, change, func(root map[string]any) {
		root["spec"].(map[string]any)["admissionStatus"] = "candidate"
	}, true)
	response = deriveWire(t, snapshot, candidateChange)
	assertWireError(t, response, statusError, codeChangeNotAdmitted)

	candidateEdge := mutateAndSeal(t, snapshot, func(root map[string]any) {
		edges := root["spec"].(map[string]any)["dependencyEdges"].([]any)
		edges[0].(map[string]any)["admissionStatus"] = "candidate"
	}, true)
	response = deriveWire(t, candidateEdge, change)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("candidate edge should preserve Unknown: %#v", response.Error)
	}
	result := response.Result.(deriveResult)
	found := false
	for _, evaluation := range result.Report.ApplicabilityEvaluations {
		if evaluation.SourceRef == "edge:contextual-alternative-aero" {
			found = evaluation.Result == "UNKNOWN" && contains(evaluation.ReasonCodes, "CANDIDATE_INPUT_NOT_AUTHORITY") && !contains(evaluation.ReasonCodes, "CANDIDATE_EDGE_NOT_AUTHORITY")
		}
	}
	if !found {
		t.Fatal("candidate edge source evaluation did not use the generic non-authority reason")
	}
	found = false
	for _, path := range result.Report.ImpactPaths {
		if contains(path.EdgeRefs, "edge:contextual-alternative-aero") {
			found = path.State == "unknown" && contains(path.ReasonCodes, "CANDIDATE_EDGE_NOT_AUTHORITY")
		}
	}
	if !found {
		t.Fatal("candidate dependency path did not add the edge-specific non-authority reason")
	}

	retractedNegativeControl := mutateAndSeal(t, snapshot, func(root map[string]any) {
		rules := root["spec"].(map[string]any)["impactRules"].([]any)
		rules[len(rules)-1].(map[string]any)["admissionStatus"] = "retracted"
	}, true)
	response = deriveWire(t, retractedNegativeControl, change)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("retracted negative control should be excluded: %#v", response.Error)
	}
	found = false
	for _, evaluation := range response.Result.(deriveResult).Report.ApplicabilityEvaluations {
		if evaluation.SourceRef == "rule:contextual-nuclear-finance-negative-control" {
			found = evaluation.Result == "FALSE" && contains(evaluation.ReasonCodes, "RETRACTED_SOURCE_EXCLUDED")
		}
	}
	if !found {
		t.Fatal("retracted source was not retained as a FALSE ledger evaluation")
	}
	for _, path := range response.Result.(deriveResult).Report.ImpactPaths {
		if contains(path.RuleRefs, "rule:contextual-nuclear-finance-negative-control") {
			t.Fatal("retracted source materialized a path")
		}
	}
}

func TestMetamorphicRawRepresentationsAndSetRules(t *testing.T) {
	directory, capsule := loadCapsule(t)
	testCase := capsule.Cases[1]
	snapshot := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Snapshot.Path)))
	change := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Change.Path)))
	baseline := deriveWire(t, snapshot, change)
	if baseline.SUTStatus != statusCompleted {
		t.Fatalf("baseline failed: %#v", baseline.Error)
	}

	var generic any
	if err := json.Unmarshal(change, &generic); err != nil {
		t.Fatal(err)
	}
	pretty, err := json.MarshalIndent(generic, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	pretty = append(pretty, '\n')
	representational := deriveWire(t, snapshot, pretty)
	if representational.SUTStatus != statusCompleted || representational.Result.(deriveResult).ReportDigest != baseline.Result.(deriveResult).ReportDigest {
		t.Fatal("insignificant raw whitespace changed derivation")
	}

	reorderedScope := mutateAndSeal(t, change, func(root map[string]any) {
		scope := root["spec"].(map[string]any)["scopeRefs"].([]any)
		for left, right := 0, len(scope)-1; left < right; left, right = left+1, right-1 {
			scope[left], scope[right] = scope[right], scope[left]
		}
	}, true)
	reordered := deriveWire(t, snapshot, reorderedScope)
	if reordered.SUTStatus != statusCompleted {
		t.Fatalf("set reordering failed: %#v", reordered.Error)
	}
	if !reflect.DeepEqual(evaluationOutcomes(baseline.Result.(deriveResult).Report), evaluationOutcomes(reordered.Result.(deriveResult).Report)) {
		t.Fatal("scopeRefs set reordering changed applicability truth results")
	}

	duplicateScope := mutateAndSeal(t, change, func(root map[string]any) {
		spec := root["spec"].(map[string]any)
		scope := spec["scopeRefs"].([]any)
		spec["scopeRefs"] = append(scope, scope[0])
	}, true)
	response := deriveWire(t, snapshot, duplicateScope)
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)

	nonPropagating := mutateAndSeal(t, snapshot, func(root map[string]any) {
		edge := root["spec"].(map[string]any)["dependencyEdges"].([]any)[2].(map[string]any)
		edge["relationType"] = "traceability"
		edge["transferPredicate"].(map[string]any)["relationTypes"] = []any{"traceability"}
	}, true)
	response = deriveWire(t, nonPropagating, change)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("closed non-propagating relation mutation failed: %#v", response.Error)
	}
	for _, path := range response.Result.(deriveResult).Report.ImpactPaths {
		if contains(path.EdgeRefs, "edge:contextual-supplier-nuclear-order") {
			t.Fatal("traceability relation propagated impact")
		}
	}
}

func TestCandidatePrerequisiteSuccessorFailsClosed(t *testing.T) {
	directory, capsule := loadCapsule(t)
	testCase := capsule.Cases[1]
	snapshot := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Snapshot.Path)))
	change := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Change.Path)))
	mutated := mutateAndSeal(t, snapshot, func(root map[string]any) {
		for _, item := range root["spec"].(map[string]any)["nodes"].([]any) {
			node := item.(map[string]any)
			if node["nodeId"] == "production:energy" {
				node["admissionStatus"] = "candidate"
			}
		}
	}, true)
	response := deriveWire(t, mutated, change)
	assertWireError(t, response, statusError, codePrerequisiteMissing)
}

func TestProductionHasNoCapsuleCaseIdentifiers(t *testing.T) {
	entries, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range entries {
		if strings.HasSuffix(path, "_test.go") {
			continue
		}
		raw := mustRead(t, path)
		for _, forbidden := range []string{"SC-" + "008", "SC-" + "009", "SC-" + "010"} {
			if strings.Contains(string(raw), forbidden) {
				t.Fatalf("production source %s contains forbidden capsule case identifier", path)
			}
		}
	}
}

func TestIndependenceStatementIsDigestBoundAndNonIndependent(t *testing.T) {
	raw := mustRead(t, "independence-statement.json")
	root, err := parseJSON(raw)
	if err != nil || root.kind != kindObject {
		t.Fatalf("invalid independence statement JSON: %v", err)
	}
	claimed, ok := objectString(root, "digest")
	if !ok || !validDigest(claimed) {
		t.Fatal("independence statement has no valid digest")
	}
	detached := jsonValue{kind: kindObject}
	for _, member := range root.object {
		if member.key != "digest" {
			detached.object = append(detached.object, member)
		}
	}
	digest := sha256.Sum256(canonicalBytes(detached))
	if claimed != "sha256:"+hex.EncodeToString(digest[:]) {
		t.Fatal("independence statement detached digest mismatch")
	}
	text := string(raw)
	for _, required := range []string{"reference_source_observed", "internal-differential", "non-clean-room", "non-independent", "provenance_and_dependency_disclosure_only"} {
		if !strings.Contains(text, required) {
			t.Fatalf("independence statement omits %q", required)
		}
	}
}

func TestCanonicalAdmissionRejectsNonIJSON(t *testing.T) {
	raw := []byte(`"\uD800"`)
	response := validateWire(t, "SemanticChangeSet", raw)
	assertWireError(t, response, statusError, codeNonIJSON)
}

func TestInvalidUTF8AndNonJSONNumbersAreSchemaErrors(t *testing.T) {
	response := validateWire(t, "SemanticChangeSet", []byte{0xff, 0xfe})
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)
	for _, token := range []string{"NaN", "Infinity", "-Infinity"} {
		response = validateWire(t, "SemanticChangeSet", []byte(token))
		assertWireError(t, response, statusError, codeCoreSchemaInvalid)
	}
}

func TestSealedProtocolEdgeCases(t *testing.T) {
	directory, capsule := loadCapsule(t)
	testCase := capsule.Cases[0]
	snapshot := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Snapshot.Path)))
	change := mustRead(t, filepath.Join(directory, filepath.FromSlash(testCase.Change.Path)))

	response := requestWire(t, "validateResource", validateResourceInput{ExpectedKind: "SemanticChangeSet", RawBase64: "Zh=="})
	assertWireError(t, response, statusError, codeCTKInputInvalid)

	unknownKind := mutateAndSeal(t, change, func(root map[string]any) { root["kind"] = "UnregisteredThing" }, true)
	response = validateWire(t, "SemanticChangeSet", unknownKind)
	assertWireError(t, response, statusError, codeCoreKindUnknown)

	fractionalTime := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["createdAt"] = "2026-08-23T00:00:00.000000Z"
	}, true)
	response = validateWire(t, "SemanticChangeSet", fractionalTime)
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)

	offsetTime := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["createdAt"] = "2026-08-23T00:00:00+00:00"
	}, true)
	response = validateWire(t, "SemanticChangeSet", offsetTime)
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)

	yearZero := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["createdAt"] = "0000-08-23T00:00:00Z"
	}, true)
	response = validateWire(t, "SemanticChangeSet", yearZero)
	assertWireError(t, response, statusError, codeCoreSchemaInvalid)

	year9999 := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["createdAt"] = "9999-08-23T00:00:00Z"
	}, true)
	response = validateWire(t, "SemanticChangeSet", year9999)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("whole-second UTC year 9999 was rejected: %#v", response.Error)
	}

	frozenSpaces := []rune{'\u0009', '\u000a', '\u000b', '\u000c', '\u000d', '\u0020', '\u0085', '\u00a0', '\u1680', '\u2000', '\u2001', '\u2002', '\u2003', '\u2004', '\u2005', '\u2006', '\u2007', '\u2008', '\u2009', '\u200a', '\u2028', '\u2029', '\u202f', '\u205f', '\u3000'}
	for _, space := range frozenSpaces {
		blankID := mutateAndSeal(t, change, func(root map[string]any) {
			root["metadata"].(map[string]any)["id"] = string(space)
		}, true)
		response = validateWire(t, "SemanticChangeSet", blankID)
		if response.SUTStatus != statusError || response.Error == nil || response.Error.Code != codeCoreSchemaInvalid {
			t.Fatalf("blank Identifier U+%04X classified as %#v", space, response)
		}
	}

	spacePreserved := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["id"] = " change:space-preserved "
	}, true)
	response = validateWire(t, "SemanticChangeSet", spacePreserved)
	assertWireError(t, response, statusError, codeCanonicalMismatch)

	omittedDefault := mutateAndSeal(t, snapshot, func(root map[string]any) {
		nodes := root["spec"].(map[string]any)["nodes"].([]any)
		delete(nodes[0].(map[string]any), "admissionStatus")
	}, true)
	response = validateWire(t, "OrganizationSnapshot", omittedDefault)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("schema-declared omitted default was not admitted without rewriting: %#v", response.Error)
	}

	binary64ExactInteger := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["revision"] = json.Number("9007199254740992")
	}, true)
	binary64RoundedInteger := mutateAndSeal(t, change, func(root map[string]any) {
		root["metadata"].(map[string]any)["revision"] = json.Number("9007199254740993")
	}, true)
	var exactEnvelope, roundedEnvelope map[string]any
	if err := json.Unmarshal(binary64ExactInteger, &exactEnvelope); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(binary64RoundedInteger, &roundedEnvelope); err != nil {
		t.Fatal(err)
	}
	if exactEnvelope["digest"] != roundedEnvelope["digest"] {
		t.Fatal("binary64-colliding integer spellings produced different JCS root digests")
	}
	response = validateWire(t, "SemanticChangeSet", binary64RoundedInteger)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("finite schema integer above 2^53 was not admitted under binary64 JCS: %#v", response.Error)
	}
	admitted, err := admitResource(binary64RoundedInteger, "SemanticChangeSet")
	if err != nil {
		t.Fatal(err)
	}
	if admitted.Change.Metadata.Revision != 9007199254740992 {
		t.Fatalf("typed revision preserved pre-binary64 token: %d", admitted.Change.Metadata.Revision)
	}

	var equivalentDigest string
	for _, token := range []string{"1", "1e0", "1.0", "1.00e+0"} {
		variant := mutateAndSeal(t, change, func(root map[string]any) {
			root["metadata"].(map[string]any)["revision"] = json.Number(token)
		}, true)
		var envelope map[string]any
		if err := json.Unmarshal(variant, &envelope); err != nil {
			t.Fatal(err)
		}
		if equivalentDigest == "" {
			equivalentDigest = envelope["digest"].(string)
		} else if envelope["digest"] != equivalentDigest {
			t.Fatalf("integral binary64 spelling %s changed detached digest", token)
		}
		response = validateWire(t, "SemanticChangeSet", variant)
		if response.SUTStatus != statusCompleted {
			t.Fatalf("integral binary64 spelling %s rejected: %#v", token, response.Error)
		}
		admitted, err := admitResource(variant, "SemanticChangeSet")
		if err != nil || admitted.Change.Metadata.Revision != 1 {
			t.Fatalf("integral binary64 spelling %s decoded incorrectly: revision=%d err=%v", token, admitted.Change.Metadata.Revision, err)
		}
	}

	spacedScalar := mutateAndSeal(t, change, func(root map[string]any) {
		delta := root["spec"].(map[string]any)["deltas"].([]any)[0].(map[string]any)
		delta["after"].(map[string]any)["value"] = " restructured "
	}, true)
	response = validateWire(t, "SemanticChangeSet", spacedScalar)
	if response.SUTStatus != statusCompleted {
		t.Fatalf("plain ObservedScalar whitespace was normalized or rejected: %#v", response.Error)
	}
	admitted, err = admitResource(spacedScalar, "SemanticChangeSet")
	if err != nil || string(admitted.Change.Spec.Deltas[0].After.Value) != `" restructured "` {
		t.Fatalf("plain ObservedScalar whitespace was not preserved: value=%s err=%v", admitted.Change.Spec.Deltas[0].After.Value, err)
	}
}

func evaluationOutcomes(report profileDerivationReport) map[string]string {
	result := map[string]string{}
	for _, evaluation := range report.ApplicabilityEvaluations {
		result[evaluation.SourceRef] = evaluation.Result + ":" + strings.Join(evaluation.ReasonCodes, ",")
	}
	return result
}

func deriveWire(t *testing.T, snapshot, change []byte) wireResponse {
	t.Helper()
	payload := deriveInput{SnapshotBase64: base64.StdEncoding.EncodeToString(snapshot), ChangeBase64: base64.StdEncoding.EncodeToString(change)}
	return requestWire(t, "derive", payload)
}

func validateWire(t *testing.T, kind string, raw []byte) wireResponse {
	t.Helper()
	return requestWire(t, "validateResource", validateResourceInput{ExpectedKind: kind, RawBase64: base64.StdEncoding.EncodeToString(raw)})
}

func requestWire(t *testing.T, operation string, payload any) wireResponse {
	t.Helper()
	request := map[string]any{"protocolVersion": protocolVersion, "requestId": "unit", "operation": operation, "payload": payload}
	raw, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return handleWireRequest(raw)
}

func assertWireError(t *testing.T, response wireResponse, status, code string) {
	t.Helper()
	if response.SUTStatus != status || response.Error == nil || response.Error.Code != code {
		t.Fatalf("response = %#v, want %s/%s", response, status, code)
	}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func mutateAndSeal(t *testing.T, raw []byte, mutate func(map[string]any), reseal bool) []byte {
	t.Helper()
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	var root map[string]any
	if err := decoder.Decode(&root); err != nil {
		t.Fatal(err)
	}
	mutate(root)
	if reseal {
		delete(root, "digest")
		projection, err := json.Marshal(root)
		if err != nil {
			t.Fatal(err)
		}
		canonical, err := canonicalizeJSON(projection)
		if err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(canonical)
		root["digest"] = "sha256:" + hex.EncodeToString(digest[:])
	}
	result, err := json.Marshal(root)
	if err != nil {
		t.Fatal(err)
	}
	return result
}
