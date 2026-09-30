package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"unicode/utf8"
)

type canonicalizeInput struct {
	RawBase64 string `json:"rawBase64"`
}

type canonicalizeResult struct {
	CanonicalBase64 string `json:"canonicalBase64"`
	Digest          string `json:"digest"`
}

func canonicalizeOperation(payload json.RawMessage) (any, *operationError) {
	if err := requireObjectKeys(payload, []string{"rawBase64"}, []string{"rawBase64"}); err != nil {
		return nil, schemaError()
	}
	var input canonicalizeInput
	if err := decodeClosed(payload, &input); err != nil || input.RawBase64 == "" {
		return nil, schemaError()
	}
	raw, err := base64.StdEncoding.Strict().DecodeString(input.RawBase64)
	if err != nil || base64.StdEncoding.EncodeToString(raw) != input.RawBase64 {
		return nil, schemaError()
	}
	canonical, err := canonicalizeJSON(raw)
	if err != nil {
		var depthError *jsonDepthError
		if errors.As(err, &depthError) {
			return nil, &operationError{status: statusResourceExhausted, code: codeResourceExceeded}
		}
		var domainError *jsonDomainError
		if errors.As(err, &domainError) {
			return nil, &operationError{status: statusError, code: codeNonIJSON}
		}
		return nil, coreSchemaError()
	}
	digest := sha256.Sum256(canonical)
	return canonicalizeResult{
		CanonicalBase64: base64.StdEncoding.EncodeToString(canonical),
		Digest:          "sha256:" + hex.EncodeToString(digest[:]),
	}, nil
}

type identifierInput struct {
	IdentifierKind string          `json:"identifierKind"`
	Fields         json.RawMessage `json:"fields"`
}

type identifierResult struct {
	Identifier string `json:"identifier"`
}

type evaluationFields struct {
	SnapshotDigest   *string  `json:"snapshotDigest"`
	ChangeDigest     *string  `json:"changeDigest"`
	ChangeSubjectRef string   `json:"changeSubjectRef"`
	SourceRef        string   `json:"sourceRef"`
	PredicateVersion string   `json:"predicateVersion"`
	Result           string   `json:"result"`
	ReasonCodes      []string `json:"reasonCodes"`
	WitnessRefs      []string `json:"witnessRefs"`
}

type pathFields struct {
	SnapshotDigest *string  `json:"snapshotDigest"`
	ChangeDigest   *string  `json:"changeDigest"`
	TargetRef      string   `json:"targetRef"`
	State          string   `json:"state"`
	EdgeRefs       []string `json:"edgeRefs"`
	RuleRefs       []string `json:"ruleRefs"`
	EvaluationRefs []string `json:"evaluationRefs"`
	DutyRefs       []string `json:"dutyRefs"`
	Origin         string   `json:"origin"`
	Truncated      bool     `json:"truncated"`
}

type obligationFields struct {
	SnapshotDigest  *string  `json:"snapshotDigest"`
	ChangeDigest    *string  `json:"changeDigest"`
	Origin          string   `json:"origin"`
	TargetRef       string   `json:"targetRef"`
	RequiredRoleRef string   `json:"requiredRoleRef"`
	ObligationType  string   `json:"obligationType"`
	ResolutionState string   `json:"resolutionState"`
	PathRefs        []string `json:"pathRefs"`
}

type fullIdentifierFields struct {
	SnapshotDigest string          `json:"snapshotDigest"`
	ChangeDigest   string          `json:"changeDigest"`
	Body           json.RawMessage `json:"body"`
}

type roleInstanceBody struct {
	RoleDefinitionRef string   `json:"roleDefinitionRef"`
	PrincipalRef      string   `json:"principalRef"`
	ObligationRefs    []string `json:"obligationRefs"`
}

type workUnitBody struct {
	RoleInstanceRefs           []string `json:"roleInstanceRefs"`
	AccountableRoleInstanceRef string   `json:"accountableRoleInstanceRef"`
	ObligationRefs             []string `json:"obligationRefs"`
}

type planDecisionBody struct {
	SubjectRef  string   `json:"subjectRef"`
	InputClass  string   `json:"inputClass"`
	Disposition string   `json:"disposition"`
	ReasonCodes []string `json:"reasonCodes"`
}

func deriveIdentifierOperation(payload json.RawMessage) (any, *operationError) {
	if err := requireObjectKeys(payload, []string{"identifierKind", "fields"}, []string{"identifierKind", "fields"}); err != nil {
		return nil, schemaError()
	}
	var input identifierInput
	if err := decodeClosed(payload, &input); err != nil || input.IdentifierKind == "" || len(input.Fields) == 0 {
		return nil, schemaError()
	}

	var identifier string
	var err error
	switch input.IdentifierKind {
	case "evaluation":
		identifier, err = deriveEvaluationIdentifier(input.Fields)
	case "path":
		identifier, err = derivePathIdentifier(input.Fields)
	case "obligation":
		identifier, err = deriveObligationIdentifier(input.Fields)
	case "role-instance", "work-unit", "plan-decision":
		identifier, err = deriveFullIdentifier(input.IdentifierKind, input.Fields)
	default:
		err = errors.New("unknown identifier kind")
	}
	if err != nil {
		return nil, schemaError()
	}
	return identifierResult{Identifier: identifier}, nil
}

func deriveEvaluationIdentifier(raw json.RawMessage) (string, error) {
	keys := []string{"snapshotDigest", "changeDigest", "changeSubjectRef", "sourceRef", "predicateVersion", "result", "reasonCodes", "witnessRefs"}
	if err := requireObjectKeys(raw, keys, keys); err != nil {
		return "", err
	}
	var fields evaluationFields
	if err := decodeClosed(raw, &fields); err != nil {
		return "", err
	}
	if !validOptionalDigest(fields.SnapshotDigest) || !validOptionalDigest(fields.ChangeDigest) ||
		!validNonEmptyString(fields.ChangeSubjectRef, 4096) || !validNonEmptyString(fields.SourceRef, 4096) || !validNonEmptyString(fields.PredicateVersion, 4096) ||
		!oneOf(fields.Result, "TRUE", "FALSE", "UNKNOWN") || !validReasonCodes(fields.ReasonCodes) ||
		!validUniqueStrings(fields.WitnessRefs, true) {
		return "", errors.New("invalid evaluation identifier fields")
	}
	snapshot := "MISSING"
	if fields.SnapshotDigest != nil {
		snapshot = *fields.SnapshotDigest
	}
	change := "MISSING"
	if fields.ChangeDigest != nil {
		change = *fields.ChangeDigest
	}
	preimage := jsonObject(
		jsonMember{key: "snapshotDigest", value: jsonString(snapshot)},
		jsonMember{key: "changeDigest", value: jsonString(change)},
		jsonMember{key: "changeSubjectRef", value: jsonString(fields.ChangeSubjectRef)},
		jsonMember{key: "sourceRef", value: jsonString(fields.SourceRef)},
		jsonMember{key: "predicateVersion", value: jsonString(fields.PredicateVersion)},
		jsonMember{key: "result", value: jsonString(fields.Result)},
		jsonMember{key: "reasonCodes", value: jsonArrayStrings(fields.ReasonCodes)},
		jsonMember{key: "witnessRefs", value: jsonArrayStrings(fields.WitnessRefs)},
	)
	return legacyIdentifier("evaluation", preimage), nil
}

func derivePathIdentifier(raw json.RawMessage) (string, error) {
	keys := []string{"snapshotDigest", "changeDigest", "targetRef", "state", "edgeRefs", "ruleRefs", "evaluationRefs", "dutyRefs", "origin", "truncated"}
	if err := requireObjectKeys(raw, keys, keys); err != nil {
		return "", err
	}
	var fields pathFields
	if err := decodeClosed(raw, &fields); err != nil {
		return "", err
	}
	if !validOptionalDigest(fields.SnapshotDigest) || !validOptionalDigest(fields.ChangeDigest) || !validNonEmptyString(fields.TargetRef, 4096) ||
		!oneOf(fields.State, "affected", "unknown", "unaffected_proven", "out_of_declared_scope") || !validNonEmptyString(fields.Origin, 4096) ||
		!validUniqueStrings(fields.EdgeRefs, true) || !validUniqueStrings(fields.RuleRefs, true) ||
		!validUniqueStrings(fields.EvaluationRefs, true) || !validUniqueStrings(fields.DutyRefs, true) {
		return "", errors.New("invalid path identifier fields")
	}
	preimage := jsonObject(
		jsonMember{key: "snapshot", value: nullableJSONDigest(fields.SnapshotDigest)},
		jsonMember{key: "change", value: nullableJSONDigest(fields.ChangeDigest)},
		jsonMember{key: "target", value: jsonString(fields.TargetRef)},
		jsonMember{key: "state", value: jsonString(fields.State)},
		jsonMember{key: "edges", value: jsonArrayStrings(fields.EdgeRefs)},
		jsonMember{key: "rules", value: jsonArrayStrings(fields.RuleRefs)},
		jsonMember{key: "evaluations", value: jsonArrayStrings(fields.EvaluationRefs)},
		jsonMember{key: "duties", value: jsonArrayStrings(fields.DutyRefs)},
		jsonMember{key: "origin", value: jsonString(fields.Origin)},
		jsonMember{key: "truncated", value: jsonBool(fields.Truncated)},
	)
	return legacyIdentifier("path", preimage), nil
}

func deriveObligationIdentifier(raw json.RawMessage) (string, error) {
	keys := []string{"snapshotDigest", "changeDigest", "origin", "targetRef", "requiredRoleRef", "obligationType", "resolutionState", "pathRefs"}
	if err := requireObjectKeys(raw, keys, keys); err != nil {
		return "", err
	}
	var fields obligationFields
	if err := decodeClosed(raw, &fields); err != nil {
		return "", err
	}
	if !validOptionalDigest(fields.SnapshotDigest) || !validOptionalDigest(fields.ChangeDigest) || !validNonEmptyString(fields.Origin, 4096) ||
		!validNonEmptyString(fields.TargetRef, 4096) || !validNonEmptyString(fields.RequiredRoleRef, 4096) || !validNonEmptyString(fields.ObligationType, 4096) ||
		!oneOf(fields.ResolutionState, "affected", "unknown") || !validUniqueStrings(fields.PathRefs, true) {
		return "", errors.New("invalid obligation identifier fields")
	}
	preimage := jsonObject(
		jsonMember{key: "snapshot", value: nullableJSONDigest(fields.SnapshotDigest)},
		jsonMember{key: "change", value: nullableJSONDigest(fields.ChangeDigest)},
		jsonMember{key: "origin", value: jsonString(fields.Origin)},
		jsonMember{key: "target", value: jsonString(fields.TargetRef)},
		jsonMember{key: "role", value: jsonString(fields.RequiredRoleRef)},
		jsonMember{key: "type", value: jsonString(fields.ObligationType)},
		jsonMember{key: "state", value: jsonString(fields.ResolutionState)},
		jsonMember{key: "paths", value: jsonArrayStrings(fields.PathRefs)},
	)
	return legacyIdentifier("obligation", preimage), nil
}

func deriveFullIdentifier(kind string, raw json.RawMessage) (string, error) {
	keys := []string{"snapshotDigest", "changeDigest", "body"}
	if err := requireObjectKeys(raw, keys, keys); err != nil {
		return "", err
	}
	var fields fullIdentifierFields
	if err := decodeClosed(raw, &fields); err != nil || !validDigest(fields.SnapshotDigest) || !validDigest(fields.ChangeDigest) {
		return "", errors.New("invalid full identifier roots")
	}
	body, err := parseJSON(fields.Body)
	if err != nil || body.kind != kindObject {
		return "", errors.New("invalid full identifier body")
	}
	if err := validateFullIdentifierBody(kind, fields.Body); err != nil {
		return "", err
	}
	envelope := jsonObject(
		jsonMember{key: "scheme", value: jsonString("oac.id/sha256-rfc8785/v1")},
		jsonMember{key: "kind", value: jsonString(kind)},
		jsonMember{key: "roots", value: jsonObject(
			jsonMember{key: "snapshotDigest", value: jsonString(fields.SnapshotDigest)},
			jsonMember{key: "changeDigest", value: jsonString(fields.ChangeDigest)},
		)},
		jsonMember{key: "body", value: body},
	)
	digest := sha256.Sum256(canonicalBytes(envelope))
	return "urn:oac:id:sha256:v1:" + kind + ":" + hex.EncodeToString(digest[:]), nil
}

func validateFullIdentifierBody(kind string, raw json.RawMessage) error {
	switch kind {
	case "role-instance":
		keys := []string{"roleDefinitionRef", "principalRef", "obligationRefs"}
		if err := requireObjectKeys(raw, keys, keys); err != nil {
			return err
		}
		var body roleInstanceBody
		if err := decodeClosed(raw, &body); err != nil || !validNonEmptyString(body.RoleDefinitionRef, 4096) || !validNonEmptyString(body.PrincipalRef, 4096) || !validUniqueStrings(body.ObligationRefs, true) {
			return errors.New("invalid role-instance body")
		}
	case "work-unit":
		keys := []string{"roleInstanceRefs", "accountableRoleInstanceRef", "obligationRefs"}
		if err := requireObjectKeys(raw, keys, keys); err != nil {
			return err
		}
		var body workUnitBody
		if err := decodeClosed(raw, &body); err != nil || !validNonEmptyString(body.AccountableRoleInstanceRef, 4096) ||
			!validUniqueStrings(body.RoleInstanceRefs, true) || !validUniqueStrings(body.ObligationRefs, true) {
			return errors.New("invalid work-unit body")
		}
	case "plan-decision":
		keys := []string{"subjectRef", "inputClass", "disposition", "reasonCodes"}
		if err := requireObjectKeys(raw, keys, keys); err != nil {
			return err
		}
		var body planDecisionBody
		if err := decodeClosed(raw, &body); err != nil || !validNonEmptyString(body.SubjectRef, 4096) || !validNonEmptyString(body.InputClass, 4096) ||
			!oneOf(body.Disposition, "included", "excluded", "unresolved") || !validReasonCodes(body.ReasonCodes) {
			return errors.New("invalid plan-decision body")
		}
	default:
		return errors.New("unknown full identifier kind")
	}
	return nil
}

func legacyIdentifier(kind string, preimage jsonValue) string {
	digest := sha256.Sum256(canonicalBytes(preimage))
	return "urn:oac:mvp:" + kind + ":" + hex.EncodeToString(digest[:])[:24]
}

func nullableJSONDigest(value *string) jsonValue {
	if value == nil {
		return jsonNull()
	}
	return jsonString(*value)
}

func validOptionalDigest(value *string) bool {
	return value == nil || validDigest(*value)
}

func validDigest(value string) bool {
	if len(value) != len("sha256:")+64 || !strings.HasPrefix(value, "sha256:") {
		return false
	}
	for index := len("sha256:"); index < len(value); index++ {
		if (value[index] < '0' || value[index] > '9') && (value[index] < 'a' || value[index] > 'f') {
			return false
		}
	}
	return true
}

func validUniqueStrings(values []string, requireNonEmpty bool) bool {
	if values == nil {
		return false
	}
	seen := make(map[string]struct{}, len(values))
	for _, value := range values {
		if requireNonEmpty && !validNonEmptyString(value, 4096) {
			return false
		}
		if !utf8.ValidString(value) {
			return false
		}
		if _, exists := seen[value]; exists {
			return false
		}
		seen[value] = struct{}{}
	}
	return true
}

func validNonEmptyString(value string, maxRunes int) bool {
	if !utf8.ValidString(value) || utf8.RuneCountInString(value) > maxRunes {
		return false
	}
	for _, current := range value {
		if !isUnicodeWhiteSpace(current) {
			return true
		}
	}
	return false
}

// isUnicodeWhiteSpace freezes the Unicode White_Space binary property rather
// than inheriting Go's broader, runtime-version-dependent definition of space.
func isUnicodeWhiteSpace(value rune) bool {
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

func validReasonCodes(values []string) bool {
	if !validUniqueStrings(values, true) {
		return false
	}
	for _, value := range values {
		if utf8.RuneCountInString(value) > 512 {
			return false
		}
		parts := strings.Split(value, ":")
		if len(parts) > 2 || !validReasonHead(parts[0]) {
			return false
		}
		if len(parts) == 2 && !validReasonSuffix(parts[1]) {
			return false
		}
	}
	return true
}

func validReasonHead(value string) bool {
	if len(value) == 0 || value[0] < 'A' || value[0] > 'Z' {
		return false
	}
	for index := 1; index < len(value); index++ {
		if (value[index] < 'A' || value[index] > 'Z') && (value[index] < '0' || value[index] > '9') && value[index] != '_' {
			return false
		}
	}
	return true
}

func validReasonSuffix(value string) bool {
	if len(value) == 0 || ((value[0] < 'A' || value[0] > 'Z') && (value[0] < 'a' || value[0] > 'z')) {
		return false
	}
	for index := 1; index < len(value); index++ {
		if (value[index] < 'A' || value[index] > 'Z') && (value[index] < 'a' || value[index] > 'z') &&
			(value[index] < '0' || value[index] > '9') && value[index] != '_' {
			return false
		}
	}
	return true
}

func oneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

type witnessInput struct {
	Mode       string `json:"mode"`
	ResourceID string `json:"resourceId,omitempty"`
	Pointer    string `json:"pointer,omitempty"`
	WitnessRef string `json:"witnessRef,omitempty"`
}

func witnessOperation(payload json.RawMessage) (any, *operationError) {
	var envelope struct {
		Mode string `json:"mode"`
	}
	if err := json.Unmarshal(payload, &envelope); err != nil {
		return nil, schemaError()
	}
	switch envelope.Mode {
	case "encode":
		keys := []string{"mode", "resourceId", "pointer"}
		if err := requireObjectKeys(payload, keys, keys); err != nil {
			return nil, schemaError()
		}
		var input witnessInput
		if err := decodeClosed(payload, &input); err != nil || !validNonEmptyString(input.ResourceID, 4096) || utf8.RuneCountInString(input.Pointer) > 4096 {
			return nil, schemaError()
		}
		if !validJSONPointer(input.Pointer) {
			return nil, witnessError()
		}
		return struct {
			WitnessRef string `json:"witnessRef"`
		}{WitnessRef: percentEncode(input.ResourceID, resourceSafe) + "#" + percentEncode(input.Pointer, pointerSafe)}, nil
	case "decode":
		keys := []string{"mode", "witnessRef"}
		if err := requireObjectKeys(payload, keys, keys); err != nil {
			return nil, schemaError()
		}
		var input witnessInput
		if err := decodeClosed(payload, &input); err != nil || !validNonEmptyString(input.WitnessRef, 4096) {
			return nil, schemaError()
		}
		if strings.Count(input.WitnessRef, "#") != 1 {
			return nil, witnessError()
		}
		parts := strings.SplitN(input.WitnessRef, "#", 2)
		resource, err := percentDecodeCanonical(parts[0], resourceSafe)
		if err != nil || !validNonEmptyString(resource, 4096) {
			return nil, witnessError()
		}
		pointer, err := percentDecodeCanonical(parts[1], pointerSafe)
		if err != nil || !validJSONPointer(pointer) {
			return nil, witnessError()
		}
		return struct {
			ResourceID string `json:"resourceId"`
			Pointer    string `json:"pointer"`
		}{ResourceID: resource, Pointer: pointer}, nil
	default:
		return nil, schemaError()
	}
}

func witnessError() *operationError {
	return &operationError{status: statusError, code: codeWitnessMismatch}
}

func validJSONPointer(pointer string) bool {
	if pointer != "" && !strings.HasPrefix(pointer, "/") {
		return false
	}
	for index := 0; index < len(pointer); index++ {
		if pointer[index] == '~' {
			if index+1 >= len(pointer) || (pointer[index+1] != '0' && pointer[index+1] != '1') {
				return false
			}
			index++
		}
	}
	return utf8.ValidString(pointer)
}

func percentEncode(value string, safe func(byte) bool) string {
	const upperHex = "0123456789ABCDEF"
	var output strings.Builder
	for _, current := range []byte(value) {
		if current < utf8.RuneSelf && safe(current) {
			output.WriteByte(current)
		} else {
			output.WriteByte('%')
			output.WriteByte(upperHex[current>>4])
			output.WriteByte(upperHex[current&0x0f])
		}
	}
	return output.String()
}

func percentDecodeCanonical(encoded string, safe func(byte) bool) (string, error) {
	decoded := make([]byte, 0, len(encoded))
	for index := 0; index < len(encoded); index++ {
		if encoded[index] != '%' {
			decoded = append(decoded, encoded[index])
			continue
		}
		if index+2 >= len(encoded) || !canonicalHex(encoded[index+1]) || !canonicalHex(encoded[index+2]) {
			return "", errors.New("invalid percent escape")
		}
		high, _ := hexNibble(encoded[index+1])
		low, _ := hexNibble(encoded[index+2])
		decoded = append(decoded, high<<4|low)
		index += 2
	}
	if !utf8.Valid(decoded) {
		return "", errors.New("percent-decoded bytes are not UTF-8")
	}
	value := string(decoded)
	if percentEncode(value, safe) != encoded {
		return "", errors.New("non-canonical percent encoding")
	}
	return value, nil
}

func canonicalHex(value byte) bool {
	return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'F')
}

func unreserved(value byte) bool {
	return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z') ||
		(value >= '0' && value <= '9') || strings.ContainsRune("-._~", rune(value))
}

func resourceSafe(value byte) bool {
	return unreserved(value) || strings.ContainsRune("!$&'()*+,;=:/?@", rune(value))
}

func pointerSafe(value byte) bool {
	return unreserved(value) || value == '/'
}

type strongKleeneInput struct {
	Operator string   `json:"operator"`
	Values   []string `json:"values"`
}

func strongKleeneOperation(payload json.RawMessage) (any, *operationError) {
	keys := []string{"operator", "values"}
	if err := requireObjectKeys(payload, keys, keys); err != nil {
		return nil, schemaError()
	}
	var input strongKleeneInput
	if err := decodeClosed(payload, &input); err != nil || input.Values == nil || !oneOf(input.Operator, "all", "any") {
		return nil, schemaError()
	}
	for _, value := range input.Values {
		if !oneOf(value, "TRUE", "FALSE", "UNKNOWN") {
			return nil, schemaError()
		}
	}
	result := "TRUE"
	if input.Operator == "all" {
		for _, value := range input.Values {
			if value == "FALSE" {
				result = "FALSE"
				break
			}
			if value == "UNKNOWN" {
				result = "UNKNOWN"
			}
		}
	} else {
		result = "FALSE"
		for _, value := range input.Values {
			if value == "TRUE" {
				result = "TRUE"
				break
			}
			if value == "UNKNOWN" {
				result = "UNKNOWN"
			}
		}
	}
	return struct {
		Result string `json:"result"`
	}{Result: result}, nil
}

var resourceLimits = map[string]int64{
	"maxBundleFiles":              512,
	"maxBundleBytes":              67_108_864,
	"maxCaseBytes":                1_048_576,
	"maxRequestBytes":             1_048_576,
	"maxResourceBytes":            8_388_608,
	"maxPlanBytes":                16_777_216,
	"maxJsonDepth":                64,
	"maxNodes":                    256,
	"maxEdges":                    1_024,
	"maxRules":                    512,
	"maxDuties":                   512,
	"maxSemanticDepth":            32,
	"maxPathPrefixes":             4_096,
	"maxEvaluations":              2_048,
	"maxWitnessRefsPerEvaluation": 256,
	"maxTotalWitnessRefs":         8_192,
	"adapterTimeoutMs":            5_000,
	"maxAdapterOutputBytes":       1_048_576,
}

func resourceCheckOperation(payload json.RawMessage) (any, *operationError) {
	value, err := parseJSON(payload)
	if err != nil || value.kind != kindObject || len(value.object) == 0 {
		return nil, schemaError()
	}
	var observations map[string]json.RawMessage
	if err := json.Unmarshal(payload, &observations); err != nil {
		return nil, schemaError()
	}
	for dimension, raw := range observations {
		limit, known := resourceLimits[dimension]
		if !known {
			return nil, schemaError()
		}
		var observed int64
		if err := json.Unmarshal(raw, &observed); err != nil || observed < 0 || observed > 9_007_199_254_740_991 {
			return nil, schemaError()
		}
		if observed > limit {
			return nil, &operationError{status: statusResourceExhausted, code: codeResourceExceeded}
		}
	}
	return struct {
		ProfileID     string `json:"profileId"`
		WithinProfile bool   `json:"withinProfile"`
	}{ProfileID: "oac.supplier.transfer/conformance-resource-profile/v1", WithinProfile: true}, nil
}
