package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"unicode/utf8"
)

func evaluationIdentifier(snapshotDigest, changeDigest, subjectRef, sourceRef, version, result string, reasons, witnesses []string) string {
	preimage := jsonObject(
		jsonMember{key: "snapshotDigest", value: jsonString(snapshotDigest)},
		jsonMember{key: "changeDigest", value: jsonString(changeDigest)},
		jsonMember{key: "changeSubjectRef", value: jsonString(subjectRef)},
		jsonMember{key: "sourceRef", value: jsonString(sourceRef)},
		jsonMember{key: "predicateVersion", value: jsonString(version)},
		jsonMember{key: "result", value: jsonString(result)},
		jsonMember{key: "reasonCodes", value: jsonArrayStrings(reasons)},
		jsonMember{key: "witnessRefs", value: jsonArrayStrings(witnesses)},
	)
	return legacyID("evaluation", preimage)
}

func pathIdentifier(snapshotDigest, changeDigest string, path impactPath) string {
	preimage := jsonObject(
		jsonMember{key: "snapshot", value: jsonString(snapshotDigest)},
		jsonMember{key: "change", value: jsonString(changeDigest)},
		jsonMember{key: "target", value: jsonString(path.TargetRef)},
		jsonMember{key: "state", value: jsonString(path.State)},
		jsonMember{key: "edges", value: jsonArrayStrings(path.EdgeRefs)},
		jsonMember{key: "rules", value: jsonArrayStrings(path.RuleRefs)},
		jsonMember{key: "evaluations", value: jsonArrayStrings(path.EvaluationRefs)},
		jsonMember{key: "duties", value: jsonArrayStrings(path.DutyRefs)},
		jsonMember{key: "origin", value: jsonString(path.Origin)},
		jsonMember{key: "truncated", value: jsonBool(path.Truncated)},
	)
	return legacyID("path", preimage)
}

func obligationIdentifier(snapshotDigest, changeDigest string, obligation coverageObligation) string {
	preimage := jsonObject(
		jsonMember{key: "snapshot", value: jsonString(snapshotDigest)},
		jsonMember{key: "change", value: jsonString(changeDigest)},
		jsonMember{key: "origin", value: jsonString(obligation.Origin)},
		jsonMember{key: "target", value: jsonString(obligation.TargetRef)},
		jsonMember{key: "role", value: jsonString(obligation.RequiredRoleRef)},
		jsonMember{key: "type", value: jsonString(obligation.ObligationType)},
		jsonMember{key: "state", value: jsonString(obligation.ResolutionState)},
		jsonMember{key: "paths", value: jsonArrayStrings(obligation.PathRefs)},
	)
	return legacyID("obligation", preimage)
}

func legacyID(kind string, preimage jsonValue) string {
	digest := sha256.Sum256(canonicalBytes(preimage))
	return "urn:oac:mvp:" + kind + ":" + hex.EncodeToString(digest[:])[:24]
}

func reportDigest(report profileDerivationReport) (string, error) {
	raw, err := json.Marshal(report)
	if err != nil {
		return "", err
	}
	canonical, err := canonicalizeJSON(raw)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func witness(resourceID, pointer string) string {
	return percentEncode(resourceID, resourceWitnessSafe) + "#" + percentEncode(pointer, pointerWitnessSafe)
}

func percentEncode(value string, safe func(byte) bool) string {
	const upperHex = "0123456789ABCDEF"
	var out strings.Builder
	for _, current := range []byte(value) {
		if current < utf8.RuneSelf && safe(current) {
			out.WriteByte(current)
		} else {
			out.WriteByte('%')
			out.WriteByte(upperHex[current>>4])
			out.WriteByte(upperHex[current&15])
		}
	}
	return out.String()
}

func witnessUnreserved(value byte) bool {
	return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z') || (value >= '0' && value <= '9') || strings.ContainsRune("-._~", rune(value))
}

func resourceWitnessSafe(value byte) bool {
	return witnessUnreserved(value) || strings.ContainsRune("!$&'()*+,;=:/?@", rune(value))
}

func pointerWitnessSafe(value byte) bool { return witnessUnreserved(value) || value == '/' }

func validateWitnessPointer(pointer string) error {
	if pointer != "" && !strings.HasPrefix(pointer, "/") {
		return errors.New("witness pointer must be an RFC 6901 pointer")
	}
	for i := 0; i < len(pointer); i++ {
		if pointer[i] == '~' {
			if i+1 >= len(pointer) || (pointer[i+1] != '0' && pointer[i+1] != '1') {
				return errors.New("invalid RFC 6901 escape")
			}
			i++
		}
	}
	return nil
}
