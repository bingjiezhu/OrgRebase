package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
)

const (
	protocolVersion = "oac.ctk.stdio/v1"
	profileID       = "oac.phase-a"
	profileVersion  = "v0.1"
	semanticRole    = "semantic-kernel"

	statusCompleted         = "COMPLETED"
	statusUnsupported       = "UNSUPPORTED"
	statusResourceExhausted = "RESOURCE_EXHAUSTED"
	statusError             = "ERROR"

	codeCoreSchemaInvalid = "CORE_SCHEMA_INVALID"
	codeCTKInputInvalid   = "CTK_INPUT_INVALID"
	codeNonIJSON          = "NON_I_JSON"
	codeWitnessMismatch   = "APPLICABILITY_WITNESS_MISMATCH"
	codeResourceExceeded  = "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"
	codeUnsupported       = "OPERATION_UNSUPPORTED"

	maxRequestBytes = 1_048_576
)

var operationTokens = []string{
	"canonicalize",
	"deriveIdentifier",
	"witness",
	"strongKleene",
	"closureMicro",
	"resourceCheck",
}

type wireRequest struct {
	ProtocolVersion string          `json:"protocolVersion"`
	RequestID       string          `json:"requestId"`
	Operation       string          `json:"operation"`
	Payload         json.RawMessage `json:"payload"`
}

type wireResponse struct {
	ProtocolVersion string         `json:"protocolVersion"`
	RequestID       string         `json:"requestId"`
	SUTStatus       string         `json:"sutStatus"`
	Result          any            `json:"result,omitempty"`
	Error           *responseError `json:"error,omitempty"`
}

type responseError struct {
	Code string `json:"code"`
}

type operationError struct {
	status string
	code   string
}

func (e *operationError) Error() string { return e.status + "/" + e.code }

func completed(requestID string, result any) wireResponse {
	return wireResponse{
		ProtocolVersion: protocolVersion,
		RequestID:       requestID,
		SUTStatus:       statusCompleted,
		Result:          result,
	}
}

func protocolError(requestID, status, code string) wireResponse {
	return wireResponse{
		ProtocolVersion: protocolVersion,
		RequestID:       requestID,
		SUTStatus:       status,
		Error:           &responseError{Code: code},
	}
}

func handleWireRequest(input []byte) wireResponse {
	requestID := bestEffortRequestID(input)
	var request wireRequest
	if err := decodeClosed(input, &request); err != nil {
		var depthError *jsonDepthError
		if errors.As(err, &depthError) {
			return protocolError(requestID, statusResourceExhausted, codeResourceExceeded)
		}
		return protocolError(requestID, statusError, codeCoreSchemaInvalid)
	}
	requestID = request.RequestID
	if request.ProtocolVersion != protocolVersion || request.RequestID == "" || request.Operation == "" || len(request.Payload) == 0 {
		return protocolError(requestID, statusError, codeCoreSchemaInvalid)
	}

	result, opErr := dispatch(request.Operation, request.Payload)
	if opErr != nil {
		return protocolError(requestID, opErr.status, opErr.code)
	}
	return completed(requestID, result)
}

func dispatch(operation string, payload json.RawMessage) (any, *operationError) {
	switch operation {
	case "capabilities":
		if err := requireObjectKeys(payload, nil, nil); err != nil {
			return nil, schemaError()
		}
		return capabilityStatement(), nil
	case "canonicalize":
		return canonicalizeOperation(payload)
	case "deriveIdentifier":
		return deriveIdentifierOperation(payload)
	case "witness":
		return witnessOperation(payload)
	case "strongKleene":
		return strongKleeneOperation(payload)
	case "closureMicro":
		return closureMicroOperation(payload)
	case "resourceCheck":
		return resourceCheckOperation(payload)
	default:
		return nil, &operationError{status: statusUnsupported, code: codeUnsupported}
	}
}

type capabilityTrack struct {
	Role           string `json:"role"`
	Operation      string `json:"operation"`
	ProfileID      string `json:"profileId"`
	ProfileVersion string `json:"profileVersion"`
	WireVersion    string `json:"wireVersion"`
}

type capabilitiesResult struct {
	ImplementationID       string            `json:"implementationId"`
	ImplementationVersion  string            `json:"implementationVersion"`
	AdapterProtocolVersion string            `json:"adapterProtocolVersion"`
	Tracks                 []capabilityTrack `json:"tracks"`
}

func capabilityStatement() capabilitiesResult {
	tracks := make([]capabilityTrack, 0, len(operationTokens))
	for _, operation := range operationTokens {
		tracks = append(tracks, capabilityTrack{
			Role:           semanticRole,
			Operation:      operation,
			ProfileID:      profileID,
			ProfileVersion: profileVersion,
			WireVersion:    protocolVersion,
		})
	}
	return capabilitiesResult{
		ImplementationID:       "oac.phase-a.go.internal",
		ImplementationVersion:  "0.1.0",
		AdapterProtocolVersion: protocolVersion,
		Tracks:                 tracks,
	}
}

func schemaError() *operationError {
	return &operationError{status: statusError, code: codeCTKInputInvalid}
}

func coreSchemaError() *operationError {
	return &operationError{status: statusError, code: codeCoreSchemaInvalid}
}

func decodeClosed(data []byte, destination any) error {
	if _, err := parseJSON(data); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("more than one JSON value")
		}
		return err
	}
	return nil
}

func requireObjectKeys(data []byte, allowed, required []string) error {
	value, err := parseJSON(data)
	if err != nil {
		return err
	}
	if value.kind != kindObject {
		return errors.New("expected object")
	}
	allowedSet := make(map[string]struct{}, len(allowed))
	for _, key := range allowed {
		allowedSet[key] = struct{}{}
	}
	found := make(map[string]struct{}, len(value.object))
	for _, member := range value.object {
		found[member.key] = struct{}{}
		if _, ok := allowedSet[member.key]; !ok {
			return errors.New("unknown object member")
		}
	}
	for _, key := range required {
		if _, ok := found[key]; !ok {
			return errors.New("missing object member")
		}
	}
	return nil
}

func bestEffortRequestID(input []byte) string {
	var envelope map[string]json.RawMessage
	if json.Unmarshal(input, &envelope) != nil {
		return ""
	}
	var requestID string
	if raw, ok := envelope["requestId"]; ok {
		_ = json.Unmarshal(raw, &requestID)
	}
	return requestID
}
