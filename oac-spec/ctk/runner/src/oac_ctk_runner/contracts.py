"""Successor CTK wire schemas, generated without importing a SUT implementation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SUCCESSOR_FORMAT = "oac.ctk.bundle/v0alpha2"
RESULT_VERSION = "oac.ctk.run-result/v0alpha2"
HARNESS_PROBES = (
    "bundle-traversal", "bundle-absolute", "bundle-backslash", "bundle-nul",
    "bundle-duplicate", "bundle-casefold", "bundle-missing", "bundle-unlisted",
    "bundle-digest", "bundle-size", "bundle-symlink", "bundle-hardlink", "bundle-fifo",
    "response-malformed", "response-duplicate-key", "response-invalid-utf8",
    "response-wrong-request", "response-partial-exhaustion", "response-timeout",
    "response-crash", "response-output-overflow", "response-no-input-read",
)


def _closed(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def successor_schemas(previous: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The old resources remain byte-identical; this exports a new coordinate."""
    schemas = deepcopy(previous)
    for name, schema in schemas.items():
        schema["$id"] = f"https://oac.dev/schemas/ctk/v0alpha2/{name}.schema.json"
        schema.pop("$comment", None)
    schemas["CTKBundle"]["properties"]["bundleFormatVersion"] = {"const": SUCCESSOR_FORMAT}
    schemas["DisagreementRecord"]["properties"]["apiVersion"] = {"const": "oac.ctk.disagreement/v0.2"}
    schemas["DisagreementRecord"]["properties"]["classification"]["enum"].append("UNCLASSIFIED")
    case = schemas["ConformanceCase"]
    case["properties"]["testTarget"] = {"enum": ["SUT", "HARNESS"]}
    case["required"].append("testTarget")
    case["properties"]["operation"] = {"anyOf": [
        {"$ref": "#/$defs/operation"}, {"const": "harnessProbe"}]}
    case["allOf"].append({"if": {"properties": {"testTarget": {"const": "HARNESS"}}},
        "then": {"properties": {"operation": {"const": "harnessProbe"},
            "input": _closed({"probe": {"enum": list(HARNESS_PROBES)}}),
            "expect": _closed({"harnessStatus": {"const": "BOUNDARY_ENFORCED"}})}},
        "else": {"properties": {"operation": {"$ref": "#/$defs/operation"}}}})
    result = schemas["RunResult"]
    result["properties"]["runResultVersion"] = {"const": RESULT_VERSION}
    digest = {"$ref": "#/$defs/sha256Digest"}
    artifact = _closed({"name": {"type": "string", "minLength": 1},
                        "size": {"type": "integer", "minimum": 0}, "digest": digest})
    build = _closed({"evidenceClass": {"const": "SELF_ATTESTED_LOCAL_MATERIAL_OBSERVATION"},
                     "commandDigest": digest, "executable": artifact,
                     "sutInputs": {"type": "array", "minItems": 1, "items": artifact},
                     "runnerInputs": {"type": "array", "minItems": 1, "items": artifact}})
    diagnostic = _closed({"caseId": {"type": "string"}, "testTarget": {"enum": ["SUT", "HARNESS"]},
        "requestKind": {"enum": ["STDIO_REQUEST", "HARNESS_RECIPE"]},
        "decodedResponse": {"type": ["object", "null"]}, "stderrText": {"type": "string", "maxLength": 8388608},
        "requestDigest": digest, "responseDigest": {"anyOf": [digest, {"type": "null"}]},
        "stderrTextDigest": digest, "sutStatus": {"type": "string"}, "stage": {"type": "string"},
        "errorCode": {"type": ["string", "null"]}, "elapsedMs": {"type": "integer", "minimum": 0}})
    properties = {"resourceProfileDigest": digest, "implementationBuild": build,
        "implementationBuildDigest": digest, "diagnostics": {"type": "array", "items": diagnostic},
        "diagnosticsDigest": digest, "disagreements": {"type": "array", "items": schemas["DisagreementRecord"]},
        "disagreementsDigest": digest, "resultDigest": digest,
        "claimBoundary": {"const": "BOUNDED_CTK_NOT_ORGANIZATIONAL_INDEPENDENCE_OR_EXECUTION_ATTESTATION"},
        "targetSummary": _closed({name: _closed({key: {"type": "integer", "minimum": 0}
            for key in ("required", "passed", "failed")}) for name in ("SUT", "HARNESS")})}
    # Keep the embedded disagreement schema's local references self-contained.
    result["$defs"]["disagreement"] = schemas["DisagreementRecord"]
    properties["disagreements"]["items"] = {"$ref": "#/$defs/disagreement"}
    result["properties"].update(properties)
    result["required"].extend(properties)
    result["$defs"]["caseResult"]["properties"]["testTarget"] = {"enum": ["SUT", "HARNESS"]}
    result["$defs"]["caseResult"]["required"].append("testTarget")
    return schemas
