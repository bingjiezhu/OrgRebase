package main

import (
	"encoding/base64"
	"strings"
	"testing"
)

func TestFrozenOperationInputErrorTaxonomy(t *testing.T) {
	tests := []struct {
		name      string
		operation string
		payload   string
		code      string
	}{
		{
			name:      "canonicalize payload shape",
			operation: "canonicalize",
			payload:   `{"rawBase64":"e30=","extra":true}`,
			code:      codeCTKInputInvalid,
		},
		{
			name:      "canonicalize raw grammar",
			operation: "canonicalize",
			payload:   `{"rawBase64":"TmFO"}`,
			code:      codeCoreSchemaInvalid,
		},
		{
			name:      "canonicalize raw I-JSON domain",
			operation: "canonicalize",
			payload:   `{"rawBase64":"Iv8i"}`,
			code:      codeNonIJSON,
		},
		{
			name:      "identifier schema",
			operation: "deriveIdentifier",
			payload:   `{"identifierKind":"evaluation","fields":{}}`,
			code:      codeCTKInputInvalid,
		},
		{
			name:      "witness mode schema",
			operation: "witness",
			payload:   `{"mode":"unknown"}`,
			code:      codeCTKInputInvalid,
		},
		{
			name:      "witness grammar",
			operation: "witness",
			payload:   `{"mode":"decode","witnessRef":"urn:a%2fb#/bad~2token"}`,
			code:      codeWitnessMismatch,
		},
		{
			name:      "strong Kleene schema",
			operation: "strongKleene",
			payload:   `{"operator":"all","values":["INVALID"]}`,
			code:      codeCTKInputInvalid,
		},
		{
			name:      "resource schema",
			operation: "resourceCheck",
			payload:   `{}`,
			code:      codeCTKInputInvalid,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, operationError := dispatch(test.operation, []byte(test.payload))
			if operationError == nil || operationError.status != statusError || operationError.code != test.code {
				t.Fatalf("got %#v, want ERROR/%s", operationError, test.code)
			}
		})
	}
}

func TestCanonicalizeAcceptsRFC8785SafeIntegerBoundaryAsNumber(t *testing.T) {
	raw := []byte(`{"n":9007199254740992}`)
	payload := mustRawJSON(t, map[string]string{
		"rawBase64": base64.StdEncoding.EncodeToString(raw),
	})
	result, operationError := dispatch("canonicalize", payload)
	if operationError != nil {
		t.Fatal(operationError)
	}
	canonical := result.(canonicalizeResult)
	decoded, err := base64.StdEncoding.DecodeString(canonical.CanonicalBase64)
	if err != nil {
		t.Fatal(err)
	}
	if string(decoded) != string(raw) {
		t.Fatalf("canonical bytes = %s", decoded)
	}
}

func TestWireRequestCeilingIsIndependentFromResourceDocumentCeilings(t *testing.T) {
	if maxRequestBytes != 1_048_576 {
		t.Fatalf("maxRequestBytes = %d", maxRequestBytes)
	}
	if resourceLimits["maxRequestBytes"] != maxRequestBytes {
		t.Fatalf("published maxRequestBytes = %d", resourceLimits["maxRequestBytes"])
	}
}

func TestCanonicalizeAppliesPublishedJSONDepthCeiling(t *testing.T) {
	atLimit := strings.Repeat("[", 63) + "0" + strings.Repeat("]", 63)
	overLimit := strings.Repeat("[", 64) + "0" + strings.Repeat("]", 64)
	for _, test := range []struct {
		name   string
		raw    string
		status string
		code   string
	}{
		{name: "at limit", raw: atLimit, status: statusCompleted},
		{name: "over limit", raw: overLimit, status: statusResourceExhausted, code: codeResourceExceeded},
	} {
		t.Run(test.name, func(t *testing.T) {
			payload := mustRawJSON(t, map[string]string{
				"rawBase64": base64.StdEncoding.EncodeToString([]byte(test.raw)),
			})
			_, operationError := dispatch("canonicalize", payload)
			if test.status == statusCompleted {
				if operationError != nil {
					t.Fatal(operationError)
				}
				return
			}
			if operationError == nil || operationError.status != test.status || operationError.code != test.code {
				t.Fatalf("got %#v, want %s/%s", operationError, test.status, test.code)
			}
		})
	}
}
