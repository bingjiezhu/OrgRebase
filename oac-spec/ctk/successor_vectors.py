"""Bounded successor vectors derived from published Phase A formulas.

This module has no reference-implementation imports. Identifier preimages are
ASCII-only, so sorted compact JSON is exactly their RFC 8785 representation;
this is a fixture construction rule, not a general JCS implementation.
"""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from typing import Any

RESOURCE_LIMITS = {
    "adapterTimeoutMs": 5_000,
    "maxAdapterOutputBytes": 1_048_576,
    "maxBundleBytes": 67_108_864,
    "maxBundleFiles": 512,
    "maxCaseBytes": 1_048_576,
    "maxRequestBytes": 1_048_576,
    "maxDuties": 512,
    "maxEdges": 1_024,
    "maxEvaluations": 2_048,
    "maxJsonDepth": 64,
    "maxNodes": 256,
    "maxPathPrefixes": 4_096,
    "maxPlanBytes": 16_777_216,
    "maxResourceBytes": 8_388_608,
    "maxRules": 512,
    "maxSemanticDepth": 32,
    "maxTotalWitnessRefs": 8_192,
    "maxWitnessRefsPerEvaluation": 256,
}
RESOURCE_PROFILE_ID = "oac.supplier.transfer/conformance-resource-profile/v1"
ROOTS = {"snapshotDigest": "sha256:" + "1" * 64, "changeDigest": "sha256:" + "2" * 64}


def _case(
    name: str,
    operation: str,
    payload: dict[str, Any],
    expectation: dict[str, Any],
    requirement: str,
) -> dict[str, Any]:
    return {
        "caseId": f"S-{name}",
        "operation": operation,
        "input": payload,
        "expect": expectation,
        "requirementRefs": [requirement],
    }


def _completed(result: dict[str, Any]) -> dict[str, Any]:
    return {"sutStatus": "COMPLETED", "result": result}


def _exhausted() -> dict[str, str]:
    return {
        "sutStatus": "RESOURCE_EXHAUSTED",
        "errorCode": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED",
    }


def _identifier_case(name: str, kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    # standard/oac-derived-identifiers-v0.1.md, Section 4, exact envelope.
    preimage = {
        "scheme": "oac.id/sha256-rfc8785/v1",
        "kind": kind,
        "roots": {key: fields[key] for key in ROOTS},
        "body": fields["body"],
    }
    raw = json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
    return _case(
        name,
        "deriveIdentifier",
        {"identifierKind": kind, "fields": fields},
        _completed({"identifier": f"urn:oac:id:sha256:v1:{kind}:{digest}"}),
        "REQ-INSTANCE-ID-V1",
    )


def _identifier_cases() -> list[dict[str, Any]]:
    bodies = {
        "work-unit": {
            "roleInstanceRefs": ["urn:r:one", "urn:r:two"],
            "accountableRoleInstanceRef": "urn:r:one",
            "obligationRefs": ["urn:o:one", "urn:o:two"],
        },
        "plan-decision": {
            "subjectRef": "urn:node:one",
            "inputClass": "admitted",
            "disposition": "included",
            "reasonCodes": ["INPUT_ADMITTED", "SCOPE_INCLUDED"],
        },
    }
    mutations = {
        "work-unit": {
            "ACCOUNTABLE": ("accountableRoleInstanceRef", "urn:r:two"),
            "ROLE": ("roleInstanceRefs", ["urn:r:one", "urn:r:three"]),
            "OBLIGATION": ("obligationRefs", ["urn:o:one", "urn:o:three"]),
            "ROLE-ORDER": ("roleInstanceRefs", ["urn:r:two", "urn:r:one"]),
            "OBLIGATION-ORDER": ("obligationRefs", ["urn:o:two", "urn:o:one"]),
        },
        "plan-decision": {
            "SUBJECT": ("subjectRef", "urn:node:two"),
            "INPUT-CLASS": ("inputClass", "candidate"),
            "DISPOSITION": ("disposition", "unresolved"),
            "REASON": ("reasonCodes", ["INPUT_ADMITTED", "SCOPE_EXCLUDED"]),
            "REASON-ORDER": ("reasonCodes", ["SCOPE_INCLUDED", "INPUT_ADMITTED"]),
        },
    }
    cases = []
    for kind, body in bodies.items():
        prefix = "ID-" + kind.upper()
        fields = {**ROOTS, "body": body}
        cases.append(_identifier_case(f"{prefix}-BASE", kind, deepcopy(fields)))
        reordered = {
            "body": dict(reversed(list(body.items()))),
            **dict(reversed(list(ROOTS.items()))),
        }
        cases.append(_identifier_case(f"{prefix}-KEY-ORDER", kind, reordered))
        for label, (key, value) in mutations[kind].items():
            changed = deepcopy(fields)
            changed["body"][key] = value
            cases.append(_identifier_case(f"{prefix}-{label}", kind, changed))
        for label, key in (("SNAPSHOT", "snapshotDigest"), ("CHANGE", "changeDigest")):
            changed = deepcopy(fields)
            changed[key] = "sha256:" + "3" * 64
            cases.append(_identifier_case(f"{prefix}-{label}", kind, changed))
    return cases


def _witness_cases() -> list[dict[str, Any]]:
    # Explicit decoded/wire triples from the Section 5 safe sets and RFC 6901.
    valid = (
        ("EMPTY-POINTER", "urn:source:a", "", "urn:source:a#"),
        ("EMPTY-TOKEN", "urn:source:a", "/", "urn:source:a#/"),
        ("REPEATED-EMPTY-TOKEN", "urn:source:a", "//", "urn:source:a#//"),
        ("POINTER-ESCAPES", "urn:source:a", "/~01/~1/~0", "urn:source:a#/~01/~1/~0"),
        (
            "SINGLE-DECODE",
            "urn:source%2Fsegment",
            "/%23/%252F",
            "urn:source%252Fsegment#/%2523/%25252F",
        ),
        ("DELIMITER", "urn:a#b", "/a#b", "urn:a%23b#/a%23b"),
        ("SPACE", "urn:a b", "/a b", "urn:a%20b#/a%20b"),
        (
            "RESERVED",
            "urn:a:/?@!$&'()*+,;=[]",
            "/:?@!$&'()*+,;=[]",
            "urn:a:/?@!$&'()*+,;=%5B%5D#/%3A%3F%40%21%24%26%27%28%29%2A%2B%2C%3B%3D%5B%5D",
        ),
        ("UNICODE", "urn:供应商", "/值", "urn:%E4%BE%9B%E5%BA%94%E5%95%86#/%E5%80%BC"),
        ("LEGACY", "change:SC-008", "/spec/subjectRef", "change:SC-008#/spec/subjectRef"),
    )
    cases = []
    for label, resource, pointer, wire in valid:
        cases.append(
            _case(
                f"WITNESS-{label}-ENCODE",
                "witness",
                {"mode": "encode", "resourceId": resource, "pointer": pointer},
                _completed({"witnessRef": wire}),
                "REQ-WITNESS-GRAMMAR",
            )
        )
        cases.append(
            _case(
                f"WITNESS-{label}-DECODE",
                "witness",
                {"mode": "decode", "witnessRef": wire},
                _completed({"resourceId": resource, "pointer": pointer}),
                "REQ-WITNESS-GRAMMAR",
            )
        )
    invalid = {
        "NO-DELIMITER": "urn:a/spec",
        "MULTIPLE-DELIMITERS": "urn:a#/a#b",
        "EMPTY-RESOURCE": "#/spec",
        "BLANK-RESOURCE": "%20#/spec",
        "INCOMPLETE-PERCENT": "urn:a%#/spec",
        "SHORT-PERCENT": "urn:a%2#/spec",
        "NONHEX-PERCENT": "urn:a%GG#/spec",
        "LOWERCASE-PERCENT": "urn:a%2f#/spec",
        "ENCODED-SAFE-RESOURCE": "urn:a%2F#/spec",
        "ENCODED-SAFE-POINTER": "urn:a#%2Fspec",
        "MISSING-POINTER-SLASH": "urn:a#spec",
        "TRAILING-TILDE": "urn:a#/a~",
        "INVALID-TILDE": "urn:a#/a~2",
        "ENCODED-TILDE": "urn:a#/a%7E0",
        "DOUBLE-DECODE-SLASH": "urn:a#%252Fspec",
        "UNESCAPED-UNICODE": "urn:供应商#/spec",
    }
    for label, octets in (
        ("CONTINUATION", "%80"),
        ("OVERLONG", "%C0%AF"),
        ("TRUNCATED", "%E2%82"),
        ("SURROGATE", "%ED%A0%80"),
        ("ABOVE-UNICODE", "%F4%90%80%80"),
        ("INVALID-LEAD", "%FF"),
    ):
        invalid[f"UTF8-{label}-RESOURCE"] = f"urn:a{octets}#/spec"
        invalid[f"UTF8-{label}-POINTER"] = f"urn:a#/spec/{octets}"
    for label, wire in invalid.items():
        cases.append(
            _case(
                f"WITNESS-{label}",
                "witness",
                {"mode": "decode", "witnessRef": wire},
                {"sutStatus": "ERROR", "errorCode": "APPLICABILITY_WITNESS_MISMATCH"},
                "REQ-WITNESS-GRAMMAR",
            )
        )
    return cases


def _resource_cases() -> list[dict[str, Any]]:
    cases = []
    for dimension, maximum in RESOURCE_LIMITS.items():
        for label, value in (("EXACT", maximum), ("OVER", maximum + 1)):
            cases.append(
                _case(
                    f"RESOURCE-{dimension}-{label}",
                    "resourceCheck",
                    {dimension: value},
                    _completed({"withinProfile": True, "profileId": RESOURCE_PROFILE_ID})
                    if label == "EXACT"
                    else _exhausted(),
                    "REQ-RESOURCE-EXHAUSTION",
                )
            )
    return cases


def _edge(identifier: str, source: str, target: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "source": source,
        "target": target,
        "result": "TRUE",
        "admission": "admitted",
        "covered": True,
    }


def _path(target: str, edges: list[str]) -> dict[str, Any]:
    return {
        "targetRef": target,
        "edgeRefs": edges,
        "state": "affected",
        "reasonCodes": [],
        "truncated": False,
    }


def _enforcement_cases() -> list[dict[str, Any]]:
    cases = []
    for depth in (64, 65):
        # Root counts as one; a scalar inside 63 arrays has depth 64.
        raw = ("[" * (depth - 1) + "0" + "]" * (depth - 1)).encode("ascii")
        encoded = base64.b64encode(raw).decode("ascii")
        cases.append(
            _case(
                f"ENFORCE-JSON-DEPTH-{depth}",
                "canonicalize",
                {"rawBase64": encoded},
                _completed(
                    {
                        "canonicalBase64": encoded,
                        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    }
                )
                if depth == 64
                else _exhausted(),
                "REQ-RESOURCE-EXHAUSTION",
            )
        )
    # 1 seed + 63 single-edge paths + 63*64 two-edge paths = 4096.
    # Parallel edges are distinct node-simple paths, not duplicate nodes.
    first = [f"a-{index:02d}" for index in range(63)]
    second = [f"b-{index:02d}" for index in range(64)]
    payload = {
        "root": "A",
        "maxDepth": 2,
        "maxPathPrefixes": 4_096,
        "nodes": [{"id": node, "admission": "admitted"} for node in "ABCD"],
        "edges": [_edge(edge, "A", "B") for edge in first]
        + [_edge(edge, "B", "C") for edge in second],
    }
    paths = [_path("A", [])]
    for a in first:
        paths.append(_path("B", [a]))
        paths.extend(_path("C", [a, b]) for b in second)
    cases.append(
        _case(
            "ENFORCE-PREFIX-EXACT",
            "closureMicro",
            deepcopy(payload),
            _completed(
                {
                    "kind": "ClosureMicroReport",
                    "rootRef": "A",
                    "paths": paths,
                    "falseFrontiers": [],
                    "unresolvedRefs": [],
                }
            ),
            "REQ-RESOURCE-EXHAUSTION",
        )
    )
    payload["edges"].append(_edge("c-00", "A", "D"))
    cases.append(
        _case(
            "ENFORCE-PREFIX-OVER",
            "closureMicro",
            payload,
            _exhausted(),
            "REQ-RESOURCE-EXHAUSTION",
        )
    )
    # Disconnected self-loops consume input resources without consuming paths.
    payload = {
        "root": "n-000",
        "maxDepth": 32,
        "maxPathPrefixes": 4_096,
        "nodes": [{"id": f"n-{index:03d}", "admission": "admitted"} for index in range(256)],
        "edges": [_edge(f"e-{index:04d}", "n-001", "n-001") for index in range(1_024)],
    }
    cases.append(
        _case(
            "ENFORCE-STRUCTURAL-EXACT",
            "closureMicro",
            payload,
            _completed(
                {
                    "kind": "ClosureMicroReport",
                    "rootRef": "n-000",
                    "paths": [_path("n-000", [])],
                    "falseFrontiers": [],
                    "unresolvedRefs": [],
                }
            ),
            "REQ-RESOURCE-EXHAUSTION",
        )
    )
    return cases


def successor_cases() -> list[dict[str, Any]]:
    """Return fresh cases without reading or changing the frozen 24-case suite."""
    return _identifier_cases() + _witness_cases() + _resource_cases() + _enforcement_cases()
