package main

import (
	"encoding/json"
	"testing"
)

func TestCompleteStrongKleeneTruthTables(t *testing.T) {
	values := []string{"TRUE", "FALSE", "UNKNOWN"}
	tables := map[string][3][3]string{
		"all": {{"TRUE", "FALSE", "UNKNOWN"}, {"FALSE", "FALSE", "FALSE"}, {"UNKNOWN", "FALSE", "UNKNOWN"}},
		"any": {{"TRUE", "TRUE", "TRUE"}, {"TRUE", "FALSE", "UNKNOWN"}, {"TRUE", "UNKNOWN", "UNKNOWN"}},
	}
	check := func(t *testing.T, operator string, operands []string, expected string) {
		t.Helper()
		raw, err := json.Marshal(map[string]any{"protocolVersion": "oac.ctk.stdio/v1", "requestId": "truth-table", "operation": "strongKleene", "payload": map[string]any{"operator": operator, "values": operands}})
		if err != nil {
			t.Fatal(err)
		}
		response := handleWireRequest(raw)
		if response.SUTStatus != statusCompleted {
			t.Fatalf("truth-table operation failed: %#v", response)
		}
		resultRaw, err := json.Marshal(response.Result)
		if err != nil {
			t.Fatal(err)
		}
		var result struct {
			Result string `json:"result"`
		}
		if err := json.Unmarshal(resultRaw, &result); err != nil {
			t.Fatal(err)
		}
		if result.Result != expected {
			t.Fatalf("%s(%q) = %s, want %s", operator, operands, result.Result, expected)
		}
	}
	for _, operator := range []string{"all", "any"} {
		for i, left := range values {
			for j, right := range values {
				t.Run(operator+"/"+left+"/"+right, func(t *testing.T) { check(t, operator, []string{left, right}, tables[operator][i][j]) })
			}
		}
		empty := "TRUE"
		if operator == "any" {
			empty = "FALSE"
		}
		t.Run(operator+"/empty", func(t *testing.T) { check(t, operator, []string{}, empty) })
	}
}
