package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

type bundledCase struct {
	CaseID    string          `json:"caseId"`
	Operation string          `json:"operation"`
	Input     json.RawMessage `json:"input"`
	Expect    struct {
		SUTStatus string          `json:"sutStatus"`
		Result    json.RawMessage `json:"result"`
		ErrorCode string          `json:"errorCode"`
	} `json:"expect"`
}

func TestFrozenBundleIdentity(t *testing.T) {
	bundlePath := filepath.Join("..", "..", "ctk", "bundles", "phase-a-v0.1", "bundle.json")
	raw, err := os.ReadFile(bundlePath)
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := parseJSON(raw)
	if err != nil || manifest.kind != kindObject {
		t.Fatalf("invalid bundle manifest: %v", err)
	}
	storedDigestValue, ok := objectValue(manifest, "bundleDigest")
	if !ok || storedDigestValue.kind != kindString {
		t.Fatal("bundleDigest missing")
	}
	artifacts, ok := objectValue(manifest, "artifacts")
	if !ok || artifacts.kind != kindArray || len(artifacts.array) != 36 {
		t.Fatalf("expected 36 ledger artifacts")
	}
	projection := jsonValue{kind: kindObject, object: make([]jsonMember, 0, len(manifest.object)-1)}
	for _, member := range manifest.object {
		if member.key != "bundleDigest" {
			projection.object = append(projection.object, member)
		}
	}
	digest := sha256.Sum256(canonicalBytes(projection))
	computed := "sha256:" + hex.EncodeToString(digest[:])
	const frozenDigest = "sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526"
	if storedDigestValue.string != frozenDigest || computed != frozenDigest {
		t.Fatalf("bundle identity mismatch: stored=%s computed=%s frozen=%s", storedDigestValue.string, computed, frozenDigest)
	}
}

func TestFrozenPublicBundle(t *testing.T) {
	casesDirectory := filepath.Join("..", "..", "ctk", "bundles", "phase-a-v0.1", "cases")
	entries, err := os.ReadDir(casesDirectory)
	if err != nil {
		t.Fatal(err)
	}
	caseCount := 0
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		caseCount++
		t.Run(strings.TrimSuffix(entry.Name(), ".json"), func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(casesDirectory, entry.Name()))
			if err != nil {
				t.Fatal(err)
			}
			var testCase bundledCase
			if err := json.Unmarshal(raw, &testCase); err != nil {
				t.Fatal(err)
			}
			requestJSON, err := json.Marshal(wireRequest{
				ProtocolVersion: protocolVersion,
				RequestID:       "public-case",
				Operation:       testCase.Operation,
				Payload:         testCase.Input,
			})
			if err != nil {
				t.Fatal(err)
			}
			response := handleWireRequest(requestJSON)
			if testCase.Expect.SUTStatus == statusCompleted {
				if response.SUTStatus != statusCompleted || response.Error != nil {
					t.Fatalf("expected COMPLETED, got %#v", response)
				}
				actualJSON, err := json.Marshal(response.Result)
				if err != nil {
					t.Fatal(err)
				}
				actualCanonical, err := canonicalizeJSON(actualJSON)
				if err != nil {
					t.Fatal(err)
				}
				expectedCanonical, err := canonicalizeJSON(testCase.Expect.Result)
				if err != nil {
					t.Fatal(err)
				}
				if !bytes.Equal(actualCanonical, expectedCanonical) {
					t.Fatalf("result mismatch\nactual:   %s\nexpected: %s", actualCanonical, expectedCanonical)
				}
				return
			}
			if response.SUTStatus == statusCompleted || response.Error == nil {
				t.Fatalf("expected %s/%s, got %#v", testCase.Expect.SUTStatus, testCase.Expect.ErrorCode, response)
			}
			if response.SUTStatus != testCase.Expect.SUTStatus || response.Error.Code != testCase.Expect.ErrorCode {
				t.Fatalf("expected %s/%s, got %s/%s", testCase.Expect.SUTStatus, testCase.Expect.ErrorCode, response.SUTStatus, response.Error.Code)
			}
		})
	}
	if caseCount != 24 {
		t.Fatalf("expected 24 public cases, found %d", caseCount)
	}
}

func TestCapabilitiesDeclareEveryFrozenOperation(t *testing.T) {
	result, opErr := dispatch("capabilities", json.RawMessage(`{}`))
	if opErr != nil {
		t.Fatal(opErr)
	}
	statement := result.(capabilitiesResult)
	if statement.ImplementationID != "oac.phase-a.go.internal" {
		t.Fatalf("implementationId = %q", statement.ImplementationID)
	}
	if len(statement.Tracks) != len(operationTokens) {
		t.Fatalf("got %d tracks", len(statement.Tracks))
	}
	for index, operation := range operationTokens {
		track := statement.Tracks[index]
		if track.Operation != operation || track.Role != semanticRole || track.ProfileID != profileID ||
			track.ProfileVersion != profileVersion || track.WireVersion != protocolVersion {
			t.Fatalf("invalid track: %#v", track)
		}
	}
}

func TestWireEnvelope(t *testing.T) {
	response := handleWireRequest([]byte(`{"protocolVersion":"oac.ctk.stdio/v1","requestId":"opaque","operation":"strongKleene","payload":{"operator":"all","values":[]}}`))
	if response.RequestID != "opaque" || response.SUTStatus != statusCompleted {
		t.Fatalf("unexpected response: %#v", response)
	}
	result := response.Result.(struct {
		Result string `json:"result"`
	})
	if result.Result != "TRUE" {
		t.Fatalf("empty conjunction = %s", result.Result)
	}
}

func TestJCSNumberRenderingBoundaries(t *testing.T) {
	// RFC 8785 Appendix B: every sample that is serializable as JSON.
	// The integer safety note does not constrain the JCS algorithm itself.
	renderVectors := []struct {
		bits     uint64
		expected string
	}{
		{bits: 0x0000000000000000, expected: `0`},
		{bits: 0x8000000000000000, expected: `0`},
		{bits: 0x0000000000000001, expected: `5e-324`},
		{bits: 0x8000000000000001, expected: `-5e-324`},
		{bits: 0x7fefffffffffffff, expected: `1.7976931348623157e+308`},
		{bits: 0xffefffffffffffff, expected: `-1.7976931348623157e+308`},
		{bits: 0x4340000000000000, expected: `9007199254740992`},
		{bits: 0xc340000000000000, expected: `-9007199254740992`},
		{bits: 0x4430000000000000, expected: `295147905179352830000`},
		{bits: 0x44b52d02c7e14af5, expected: `9.999999999999997e+22`},
		{bits: 0x44b52d02c7e14af6, expected: `1e+23`},
		{bits: 0x44b52d02c7e14af7, expected: `1.0000000000000001e+23`},
		{bits: 0x444b1ae4d6e2ef4e, expected: `999999999999999700000`},
		{bits: 0x444b1ae4d6e2ef4f, expected: `999999999999999900000`},
		{bits: 0x444b1ae4d6e2ef50, expected: `1e+21`},
		{bits: 0x3eb0c6f7a0b5ed8c, expected: `9.999999999999997e-7`},
		{bits: 0x3eb0c6f7a0b5ed8d, expected: `0.000001`},
		{bits: 0x41b3de4355555553, expected: `333333333.3333332`},
		{bits: 0x41b3de4355555554, expected: `333333333.33333325`},
		{bits: 0x41b3de4355555555, expected: `333333333.3333333`},
		{bits: 0x41b3de4355555556, expected: `333333333.3333334`},
		{bits: 0x41b3de4355555557, expected: `333333333.33333343`},
		{bits: 0xbecbf647612f3696, expected: `-0.0000033333333333333333`},
		{bits: 0x43143ff3c1cb0959, expected: `1424953923781206.2`},
	}
	for _, vector := range renderVectors {
		input := math.Float64frombits(vector.bits)
		if actual := formatECMAScriptNumber(input); actual != vector.expected {
			t.Fatalf("%016x (%g) rendered as %s, want %s", vector.bits, input, actual, vector.expected)
		}
	}

	rawVectors := map[string]string{
		`1e-6`:                   `0.000001`,
		`1e-7`:                   `1e-7`,
		`-0.0`:                   `0`,
		`1.234`:                  `1.234`,
		`9007199254740992`:       `9007199254740992`,
		`9007199254740993`:       `9007199254740992`,
		`-9007199254740992`:      `-9007199254740992`,
		`295147905179352830000`:  `295147905179352830000`,
		`1.7976931348623157e308`: `1.7976931348623157e+308`,
		`1e-400`:                 `0`,
	}
	for input, expected := range rawVectors {
		actual, err := canonicalizeJSON([]byte(input))
		if err != nil {
			t.Fatalf("%s: %v", input, err)
		}
		if string(actual) != expected {
			t.Fatalf("%s canonicalized to %s, want %s", input, actual, expected)
		}
	}

	for _, input := range []string{
		`1.7976931348623159e308`, `1e999`, `-1e999`,
	} {
		_, err := canonicalizeJSON([]byte(input))
		var domainError *jsonDomainError
		if err == nil || !errors.As(err, &domainError) {
			t.Fatalf("non-finite %s was not rejected as outside the IEEE-754 domain: %v", input, err)
		}
	}
	for _, input := range []string{
		`01`, `1.`, `.1`, `1e`, `+1`, `NaN`, `Infinity`,
	} {
		if _, err := canonicalizeJSON([]byte(input)); err == nil {
			t.Fatalf("invalid JSON number %s was accepted", input)
		}
	}
}

func TestCanonicalizationRejectsDomainAndSchemaFailuresSeparately(t *testing.T) {
	if _, err := canonicalizeJSON([]byte(`{"a":1,"a":2}`)); err == nil {
		t.Fatal("duplicate key accepted")
	}
	_, err := canonicalizeJSON([]byte(`{"s":"\ud800"}`))
	var domainError *jsonDomainError
	if err == nil || !strings.Contains(err.Error(), "surrogate") {
		t.Fatalf("lone surrogate error = %v", err)
	}
	if !errors.As(err, &domainError) {
		t.Fatalf("lone surrogate was not classified as non-I-JSON: %T", err)
	}
}

func TestWitnessRoundTripAndStrictSpelling(t *testing.T) {
	resource := "urn:oac:knowledge#quality/%/供应商"
	pointer := "/spec/a~1b/~0token/#/%/值"
	encoded := percentEncode(resource, resourceSafe) + "#" + percentEncode(pointer, pointerSafe)
	result, opErr := dispatch("witness", mustRawJSON(t, map[string]string{"mode": "decode", "witnessRef": encoded}))
	if opErr != nil {
		t.Fatal(opErr)
	}
	decoded := result.(struct {
		ResourceID string `json:"resourceId"`
		Pointer    string `json:"pointer"`
	})
	if decoded.ResourceID != resource || decoded.Pointer != pointer {
		t.Fatalf("round trip mismatch: %#v", decoded)
	}
	_, opErr = dispatch("witness", json.RawMessage(`{"mode":"decode","witnessRef":"urn:a%2fb#/spec"}`))
	if opErr == nil || opErr.code != codeWitnessMismatch {
		t.Fatalf("lowercase percent spelling accepted: %v", opErr)
	}
}

func TestNonEmptyStringUsesFrozenUnicodeWhiteSpaceProperty(t *testing.T) {
	whiteSpace := []rune{
		'\u0009', '\u000a', '\u000b', '\u000c', '\u000d',
		'\u0020', '\u0085', '\u00a0', '\u1680',
		'\u2000', '\u2001', '\u2002', '\u2003', '\u2004', '\u2005',
		'\u2006', '\u2007', '\u2008', '\u2009', '\u200a',
		'\u2028', '\u2029', '\u202f', '\u205f', '\u3000',
	}
	for _, value := range whiteSpace {
		t.Run("white-space-"+strconv.FormatInt(int64(value), 16), func(t *testing.T) {
			if validNonEmptyString(string(value), 1) {
				t.Fatalf("U+%04X was treated as non-empty", value)
			}
			_, opErr := dispatch("witness", mustRawJSON(t, map[string]string{
				"mode": "decode", "witnessRef": percentEncode(string(value), resourceSafe) + "#",
			}))
			if opErr == nil || opErr.code != codeWitnessMismatch {
				t.Fatalf("U+%04X blank decoded resource was accepted: %v", value, opErr)
			}
		})
	}
	if validNonEmptyString(string(whiteSpace), len(whiteSpace)) {
		t.Fatal("a string containing the complete Unicode White_Space set was treated as non-empty")
	}

	for value := rune(0x001c); value <= 0x001f; value++ {
		t.Run("non-white-space-"+strconv.FormatInt(int64(value), 16), func(t *testing.T) {
			resourceID := string(value)
			pointer := "/" + string(value)
			if !validNonEmptyString(resourceID, 1) {
				t.Fatalf("U+%04X was incorrectly treated as Unicode White_Space", value)
			}

			encoded, opErr := dispatch("witness", mustRawJSON(t, map[string]string{
				"mode": "encode", "resourceId": resourceID, "pointer": pointer,
			}))
			if opErr != nil {
				t.Fatalf("U+%04X witness encode: %v", value, opErr)
			}
			witnessRef := encoded.(struct {
				WitnessRef string `json:"witnessRef"`
			}).WitnessRef
			decoded, opErr := dispatch("witness", mustRawJSON(t, map[string]string{
				"mode": "decode", "witnessRef": witnessRef,
			}))
			if opErr != nil {
				t.Fatalf("U+%04X witness decode: %v", value, opErr)
			}
			actual := decoded.(struct {
				ResourceID string `json:"resourceId"`
				Pointer    string `json:"pointer"`
			})
			if actual.ResourceID != resourceID || actual.Pointer != pointer {
				t.Fatalf("U+%04X witness round trip = %#v", value, actual)
			}
		})
	}
}

func TestClosureMicroNormativeInputAndResourceBoundaries(t *testing.T) {
	validNodes := []closureNode{{ID: "A", Admission: "admitted"}}
	base := closureInput{Root: "A", MaxDepth: 1, MaxPathPrefixes: 1, Nodes: validNodes, Edges: []closureEdge{}}

	missingRoot := base
	missingRoot.Root = "missing"
	assertOperationError(t, "closureMicro", mustRawJSON(t, missingRoot), statusError, codeCTKInputInvalid)

	retractedRoot := base
	retractedRoot.Nodes = []closureNode{{ID: "A", Admission: "retracted"}}
	assertOperationError(t, "closureMicro", mustRawJSON(t, retractedRoot), statusError, codeCTKInputInvalid)

	overDepth := base
	overDepth.MaxDepth = 33
	assertOperationError(t, "closureMicro", mustRawJSON(t, overDepth), statusResourceExhausted, codeResourceExceeded)

	overPrefixes := base
	overPrefixes.MaxPathPrefixes = 4097
	assertOperationError(t, "closureMicro", mustRawJSON(t, overPrefixes), statusResourceExhausted, codeResourceExceeded)

	overNodes := base
	overNodes.Nodes = make([]closureNode, 257)
	for index := range overNodes.Nodes {
		overNodes.Nodes[index] = closureNode{ID: "node-" + strconv.Itoa(index), Admission: "admitted"}
	}
	// Preserve a resolvable root; the resource ceiling is evaluated before semantic graph admission.
	overNodes.Nodes[0].ID = "A"
	assertOperationError(t, "closureMicro", mustRawJSON(t, overNodes), statusResourceExhausted, codeResourceExceeded)

	overEdges := base
	overEdges.Nodes = []closureNode{{ID: "A", Admission: "admitted"}, {ID: "B", Admission: "admitted"}}
	overEdges.Edges = make([]closureEdge, 1025)
	for index := range overEdges.Edges {
		overEdges.Edges[index] = closureEdge{
			ID:        "edge-" + strconv.Itoa(index),
			Source:    "A",
			Target:    "B",
			Result:    "TRUE",
			Admission: "admitted",
			Covered:   true,
		}
	}
	assertOperationError(t, "closureMicro", mustRawJSON(t, overEdges), statusResourceExhausted, codeResourceExceeded)
}

func TestClosureMicroDepthBoundaryStillEmitsFalseFrontier(t *testing.T) {
	input := closureInput{
		Root:            "A",
		MaxDepth:        1,
		MaxPathPrefixes: 2,
		Nodes: []closureNode{
			{ID: "A", Admission: "admitted"},
			{ID: "B", Admission: "admitted"},
			{ID: "C", Admission: "admitted"},
		},
		Edges: []closureEdge{
			{ID: "e1", Source: "A", Target: "B", Result: "TRUE", Admission: "admitted", Covered: true},
			{ID: "e2", Source: "B", Target: "C", Result: "FALSE", Admission: "admitted", Covered: true},
		},
	}
	report := runClosureMicro(t, input)
	if len(report.Paths) != 2 || report.Paths[1].TargetRef != "B" || report.Paths[1].State != "affected" || report.Paths[1].Truncated {
		t.Fatalf("FALSE-only depth boundary changed the retained prefix: %#v", report.Paths)
	}
	if len(report.FalseFrontiers) != 1 {
		t.Fatalf("falseFrontiers = %#v", report.FalseFrontiers)
	}
	frontier := report.FalseFrontiers[0]
	if !frontier.Authoritative || frontier.EdgeID != "e2" || compareStringSlices(frontier.EdgeRefs, []string{"e1", "e2"}) != 0 {
		t.Fatalf("depth-boundary frontier = %#v", frontier)
	}
	if len(frontier.EdgeRefs) != int(input.MaxDepth)+1 {
		t.Fatalf("frontier edgeRefs length = %d, want maxDepth+1", len(frontier.EdgeRefs))
	}
}

func TestClosureMicroMixedBoundaryUsesPreTruncationAuthority(t *testing.T) {
	input := closureInput{
		Root:            "A",
		MaxDepth:        1,
		MaxPathPrefixes: 2,
		Nodes: []closureNode{
			{ID: "A", Admission: "admitted"},
			{ID: "B", Admission: "admitted"},
			{ID: "C", Admission: "admitted"},
			{ID: "D", Admission: "admitted"},
		},
		Edges: []closureEdge{
			{ID: "e1", Source: "A", Target: "B", Result: "TRUE", Admission: "admitted", Covered: true},
			{ID: "e2", Source: "B", Target: "C", Result: "FALSE", Admission: "admitted", Covered: true},
			{ID: "e3", Source: "B", Target: "D", Result: "TRUE", Admission: "admitted", Covered: true},
		},
	}
	report := runClosureMicro(t, input)
	if len(report.Paths) != 2 {
		t.Fatalf("non-FALSE boundary continuation consumed a prefix: %#v", report.Paths)
	}
	boundaryPath := report.Paths[1]
	if boundaryPath.TargetRef != "B" || boundaryPath.State != "unknown" || !boundaryPath.Truncated ||
		compareStringSlices(boundaryPath.ReasonCodes, []string{"IMPACT_SEARCH_TRUNCATED"}) != 0 {
		t.Fatalf("mixed boundary path = %#v", boundaryPath)
	}
	if len(report.FalseFrontiers) != 1 || !report.FalseFrontiers[0].Authoritative {
		t.Fatalf("FALSE frontier did not retain pre-truncation authority: %#v", report.FalseFrontiers)
	}
	if compareStringSlices(report.FalseFrontiers[0].EdgeRefs, []string{"e1", "e2"}) != 0 {
		t.Fatalf("mixed boundary frontier edgeRefs = %#v", report.FalseFrontiers[0].EdgeRefs)
	}
	if compareStringSlices(report.UnresolvedRefs, []string{"B"}) != 0 {
		t.Fatalf("unresolvedRefs = %#v", report.UnresolvedRefs)
	}
}

func runClosureMicro(t *testing.T, input closureInput) closureReport {
	t.Helper()
	result, opErr := dispatch("closureMicro", mustRawJSON(t, input))
	if opErr != nil {
		t.Fatal(opErr)
	}
	report, ok := result.(closureReport)
	if !ok {
		t.Fatalf("closure result type = %T", result)
	}
	return report
}

func assertOperationError(t *testing.T, operation string, payload json.RawMessage, status, code string) {
	t.Helper()
	_, opErr := dispatch(operation, payload)
	if opErr == nil || opErr.status != status || opErr.code != code {
		t.Fatalf("expected %s/%s, got %v", status, code, opErr)
	}
}

func mustRawJSON(t *testing.T, value any) json.RawMessage {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
