package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
)

const (
	protocolVersion = "oac.ctk.stdio/v2"

	statusCompleted         = "COMPLETED"
	statusUnsupported       = "UNSUPPORTED"
	statusResourceExhausted = "RESOURCE_EXHAUSTED"
	statusError             = "ERROR"

	codeCoreSchemaInvalid   = "CORE_SCHEMA_INVALID"
	codeCTKInputInvalid     = "CTK_INPUT_INVALID"
	codeNonIJSON            = "NON_I_JSON"
	codeRootDigestMismatch  = "ROOT_DIGEST_MISMATCH"
	codeCanonicalMismatch   = "CANONICAL_ADMISSION_MISMATCH"
	codeCoreKindUnknown     = "CORE_KIND_UNKNOWN"
	codeResourceExceeded    = "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"
	codeResourceCoherence   = "RESOURCE_COHERENCE_VIOLATION"
	codeDeltaSetInvalid     = "SUPPLIER_STATUS_DELTA_SET_INVALID"
	codeChangeNotAdmitted   = "CHANGE_NOT_ADMITTED"
	codeOperationInvalid    = "CHANGE_OPERATION_INCONSISTENT"
	codeUnsupportedSemantic = "UNSUPPORTED_SEMANTICS"
	codeRetractedSource     = "RETRACTED_SOURCE_EXCLUDED"
	codeUnknownDutyInvalid  = "UNKNOWN_TRANSITION_DUTY_INVALID"
	codeObligationInvalid   = "OBLIGATION_UNSATISFIED"
	codePrerequisiteMissing = "PREREQUISITE_OBLIGATION_ORDER_MISSING"
	codeOrderCycle          = "ORDER_CYCLE"
	codeUnsupported         = "OPERATION_UNSUPPORTED"

	maxRequestBytes  = 1_048_576
	maxResourceBytes = 8_388_608
	maxPlanBytes     = 16_777_216
	maxJSONDepth     = 64
	maxNodes         = 256
	maxEdges         = 1_024
	maxRules         = 512
	maxDuties        = 512
	maxSemanticDepth = 32
	maxPathPrefixes  = 4_096
	maxEvaluations   = 2_048
	maxEvalWitnesses = 256
	maxTotalWitness  = 8_192
)

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
	Code   string `json:"code"`
	Detail string `json:"detail"`
}

type operationError struct {
	status string
	code   string
	detail string
}

func (e *operationError) Error() string { return e.status + "/" + e.code + ": " + e.detail }

func opError(status, code string, err error) *operationError {
	detail := code
	if err != nil && err.Error() != "" {
		detail = err.Error()
	}
	return &operationError{status: status, code: code, detail: detail}
}

func handleWireRequest(raw []byte) wireResponse {
	requestID := bestEffortRequestID(raw)
	var request wireRequest
	if err := decodeClosed(raw, &request); err != nil {
		return failedResponse(requestID, classifyEnvelopeError(err))
	}
	requestID = request.RequestID
	if request.ProtocolVersion != protocolVersion || request.RequestID == "" || request.Operation == "" || request.Payload == nil {
		return failedResponse(requestID, opError(statusError, codeCoreSchemaInvalid, errors.New("invalid request envelope")))
	}
	result, err := dispatch(request.Operation, request.Payload)
	if err != nil {
		return failedResponse(requestID, err)
	}
	return wireResponse{ProtocolVersion: protocolVersion, RequestID: requestID, SUTStatus: statusCompleted, Result: result}
}

func failedResponse(requestID string, err *operationError) wireResponse {
	return wireResponse{
		ProtocolVersion: protocolVersion,
		RequestID:       requestID,
		SUTStatus:       err.status,
		Error:           &responseError{Code: err.code, Detail: err.detail},
	}
}

func classifyEnvelopeError(err error) *operationError {
	var depth *jsonDepthError
	if errors.As(err, &depth) {
		return opError(statusResourceExhausted, codeResourceExceeded, err)
	}
	var domain *jsonDomainError
	if errors.As(err, &domain) {
		return opError(statusError, codeNonIJSON, err)
	}
	return opError(statusError, codeCoreSchemaInvalid, err)
}

func dispatch(operation string, payload json.RawMessage) (any, *operationError) {
	switch operation {
	case "capabilities":
		if err := requireObjectKeys(payload, nil, nil); err != nil {
			return nil, opError(statusError, codeCTKInputInvalid, err)
		}
		return capabilities(), nil
	case "validateResource":
		return validateResourceOperation(payload)
	case "derive":
		return deriveOperation(payload)
	default:
		return nil, opError(statusUnsupported, codeUnsupported, errors.New("operation is not implemented"))
	}
}

type capabilityTrack struct {
	Role           string `json:"role"`
	Operation      string `json:"operation"`
	ProfileID      string `json:"profileId"`
	ProfileVersion string `json:"profileVersion"`
	WireVersion    string `json:"wireVersion"`
}

type capabilityResult struct {
	ImplementationID       string            `json:"implementationId"`
	ImplementationVersion  string            `json:"implementationVersion"`
	AdapterProtocolVersion string            `json:"adapterProtocolVersion"`
	Tracks                 []capabilityTrack `json:"tracks"`
}

func capabilities() capabilityResult {
	return capabilityResult{
		ImplementationID:       "oac.supplier.go.internal",
		ImplementationVersion:  "0.2.0-seed2",
		AdapterProtocolVersion: protocolVersion,
		Tracks: []capabilityTrack{
			{Role: "semantic-kernel", Operation: "validateResource", ProfileID: "oac.core.sealed-resource", ProfileVersion: "v1", WireVersion: protocolVersion},
			{Role: "semantic-kernel", Operation: "derive", ProfileID: "oac.supplier.transfer.portability-capsule", ProfileVersion: "v0.2-seed-2", WireVersion: protocolVersion},
		},
	}
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
	v, err := parseJSON(data)
	if err != nil {
		return err
	}
	if v.kind != kindObject {
		return errors.New("expected object")
	}
	allowedSet := make(map[string]struct{}, len(allowed))
	for _, key := range allowed {
		allowedSet[key] = struct{}{}
	}
	found := make(map[string]struct{}, len(v.object))
	for _, member := range v.object {
		if _, ok := allowedSet[member.key]; !ok {
			return errors.New("unknown object member: " + member.key)
		}
		found[member.key] = struct{}{}
	}
	for _, key := range required {
		if _, ok := found[key]; !ok {
			return errors.New("missing object member: " + key)
		}
	}
	return nil
}

func bestEffortRequestID(raw []byte) string {
	var envelope map[string]json.RawMessage
	if json.Unmarshal(raw, &envelope) != nil {
		return ""
	}
	var requestID string
	if value, ok := envelope["requestId"]; ok {
		_ = json.Unmarshal(value, &requestID)
	}
	return requestID
}
