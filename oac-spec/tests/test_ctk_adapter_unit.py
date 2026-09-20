from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from oac.ctk_adapter import PROTOCOL_VERSION, handle

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "ctk/bundles/phase-a-v0.1/cases"
OAC_WHITE_SPACE = tuple(
    chr(code_point)
    for code_point in (
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)


def _request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "request-1",
        "operation": operation,
        "payload": payload,
    }


def test_reference_adapter_directly_satisfies_every_frozen_case() -> None:
    for path in sorted(CASES.glob("*.json")):
        case = json.loads(path.read_bytes())
        response = handle(_request(case["operation"], case["input"]))
        expectation = case["expect"]
        assert response["sutStatus"] == expectation["sutStatus"], path.name
        if "result" in expectation:
            assert response["result"] == expectation["result"], path.name
        if "errorCode" in expectation:
            assert response["error"]["code"] == expectation["errorCode"], path.name


def test_capability_handshake_is_fine_grained_and_complete() -> None:
    response = handle(_request("capabilities", {}))
    assert response["sutStatus"] == "COMPLETED"
    result = response["result"]
    operations = {track["operation"] for track in result["tracks"]}
    assert operations == {
        "canonicalize",
        "deriveIdentifier",
        "witness",
        "strongKleene",
        "closureMicro",
        "resourceCheck",
    }
    assert all(track["wireVersion"] == PROTOCOL_VERSION for track in result["tracks"])


def test_witness_non_blank_input_is_runtime_independent() -> None:
    for value in (*OAC_WHITE_SPACE, "".join(OAC_WHITE_SPACE)):
        response = handle(
            _request("witness", {"mode": "encode", "resourceId": value, "pointer": ""})
        )
        assert response["sutStatus"] == "ERROR"
        assert response["error"]["code"] == "CTK_INPUT_INVALID"
        decoded = handle(
            _request(
                "witness",
                {"mode": "decode", "witnessRef": f"{quote(value, safe='')}#"},
            )
        )
        assert decoded["sutStatus"] == "ERROR"
        assert decoded["error"]["code"] == "APPLICABILITY_WITNESS_MISMATCH"

    for discriminator in ("\u001c", "\u001d", "\u001e", "\u001f", "\ufeff"):
        response = handle(
            _request(
                "witness",
                {"mode": "encode", "resourceId": discriminator, "pointer": ""},
            )
        )
        assert response["sutStatus"] == "COMPLETED"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    (
        ("0", "0"),
        ("-0.0", "0"),
        ("5e-324", "5e-324"),
        ("-5e-324", "-5e-324"),
        ("1.7976931348623157e308", "1.7976931348623157e+308"),
        ("-1.7976931348623157e308", "-1.7976931348623157e+308"),
        ("9007199254740992", "9007199254740992"),
        ("-9007199254740992", "-9007199254740992"),
        ("295147905179352830000", "295147905179352830000"),
        ("9.999999999999997e22", "9.999999999999997e+22"),
        ("1e23", "1e+23"),
        ("1.0000000000000001e23", "1.0000000000000001e+23"),
        ("9.999999999999997e20", "999999999999999700000"),
        ("9.999999999999999e20", "999999999999999900000"),
        ("1e21", "1e+21"),
        ("9.999999999999997e-7", "9.999999999999997e-7"),
        ("0.000001", "0.000001"),
        ("333333333.3333332", "333333333.3333332"),
        ("333333333.33333325", "333333333.33333325"),
        ("333333333.3333333", "333333333.3333333"),
        ("333333333.3333334", "333333333.3333334"),
        ("333333333.33333343", "333333333.33333343"),
        ("-0.0000033333333333333333", "-0.0000033333333333333333"),
        ("1424953923781206.25", "1424953923781206.2"),
    ),
)
def test_reference_adapter_matches_rfc8785_appendix_b(raw: str, canonical: str) -> None:
    response = handle(
        _request(
            "canonicalize",
            {"rawBase64": base64.b64encode(raw.encode()).decode("ascii")},
        )
    )
    assert response["sutStatus"] == "COMPLETED"
    assert base64.b64decode(response["result"]["canonicalBase64"]).decode() == canonical


@pytest.mark.parametrize(
    ("raw", "error_code"),
    (
        (b"NaN", "CORE_SCHEMA_INVALID"),
        (b"Infinity", "CORE_SCHEMA_INVALID"),
        (b'"\xff"', "NON_I_JSON"),
        (b"1e9999", "NON_I_JSON"),
    ),
)
def test_canonicalize_separates_json_grammar_from_i_json_domain(
    raw: bytes, error_code: str
) -> None:
    response = handle(
        _request(
            "canonicalize",
            {"rawBase64": base64.b64encode(raw).decode("ascii")},
        )
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == error_code


def test_canonicalize_applies_the_published_json_depth_ceiling() -> None:
    at_limit = ("[" * 63 + "0" + "]" * 63).encode()
    over_limit = ("[" * 64 + "0" + "]" * 64).encode()
    accepted = handle(
        _request(
            "canonicalize",
            {"rawBase64": base64.b64encode(at_limit).decode("ascii")},
        )
    )
    rejected = handle(
        _request(
            "canonicalize",
            {"rawBase64": base64.b64encode(over_limit).decode("ascii")},
        )
    )
    assert accepted["sutStatus"] == "COMPLETED"
    assert rejected["sutStatus"] == "RESOURCE_EXHAUSTED"
    assert rejected["error"]["code"] == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"


@pytest.mark.parametrize(
    ("operation", "payload"),
    (
        ("canonicalize", {"rawBase64": "not base64!"}),
        ("canonicalize", {"rawBase64": "e30=", "extra": True}),
        ("canonicalize", {}),
        ("deriveIdentifier", {"identifierKind": "unknown", "fields": {}}),
        ("deriveIdentifier", {"identifierKind": "evaluation", "fields": {}}),
        ("deriveIdentifier", {"identifierKind": "role-instance", "fields": {}}),
        ("witness", {"mode": "unknown"}),
        ("strongKleene", {"operator": "all", "values": ["INVALID"]}),
        ("strongKleene", {"operator": "invalid", "values": []}),
        ("resourceCheck", {"unknownLimit": 1}),
        ("resourceCheck", {"maxNodes": True}),
        ("resourceCheck", {}),
    ),
)
def test_adapter_maps_invalid_operation_inputs_to_protocol_error(
    operation: str, payload: dict[str, Any]
) -> None:
    response = handle(_request(operation, payload))
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"
    assert "result" not in response


def test_unknown_operation_is_unsupported_not_domain_unknown() -> None:
    response = handle(_request("futureOperation", {}))
    assert response == {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "request-1",
        "sutStatus": "UNSUPPORTED",
        "error": {"code": "OPERATION_UNSUPPORTED"},
    }


def test_capabilities_payload_is_closed() -> None:
    response = handle(_request("capabilities", {"unexpected": True}))
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"


@pytest.mark.parametrize(
    "payload",
    (
        {
            "identifierKind": "evaluation",
            "fields": {
                "snapshotDigest": "not-a-digest",
                "changeDigest": None,
                "changeSubjectRef": "subject",
                "sourceRef": "source",
                "predicateVersion": "v1",
                "result": "TRUE",
                "reasonCodes": [],
                "witnessRefs": [],
            },
        },
        {
            "identifierKind": "evaluation",
            "fields": {
                "snapshotDigest": None,
                "changeDigest": None,
                "changeSubjectRef": "   ",
                "sourceRef": "source",
                "predicateVersion": "v1",
                "result": "TRUE",
                "reasonCodes": [],
                "witnessRefs": [],
            },
        },
        {
            "identifierKind": "evaluation",
            "fields": {
                "snapshotDigest": None,
                "changeDigest": None,
                "changeSubjectRef": "subject",
                "sourceRef": "source",
                "predicateVersion": "v1",
                "result": "INVALID",
                "reasonCodes": ["DUPLICATE", "DUPLICATE"],
                "witnessRefs": [],
            },
        },
    ),
)
def test_identifier_inputs_are_admitted_by_the_frozen_schema_before_hashing(
    payload: dict[str, Any],
) -> None:
    response = handle(_request("deriveIdentifier", payload))
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"


def test_witness_shape_and_witness_grammar_have_distinct_errors() -> None:
    shape = handle(_request("witness", {"mode": "unknown"}))
    grammar = handle(
        _request(
            "witness",
            {"mode": "decode", "witnessRef": "urn:a%2fb#/bad~2token"},
        )
    )
    assert shape["error"]["code"] == "CTK_INPUT_INVALID"
    assert grammar["error"]["code"] == "APPLICABILITY_WITNESS_MISMATCH"


@pytest.mark.parametrize(
    "request_value",
    (
        [],
        {"protocolVersion": PROTOCOL_VERSION},
        {
            "protocolVersion": "wrong",
            "requestId": "request-1",
            "operation": "capabilities",
            "payload": {},
        },
        {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": "",
            "operation": "capabilities",
            "payload": {},
        },
        {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": "request-1",
            "operation": "capabilities",
            "payload": [],
        },
    ),
)
def test_invalid_request_envelopes_are_rejected_before_dispatch(
    request_value: object,
) -> None:
    with pytest.raises(ValueError):
        handle(request_value)


@pytest.mark.parametrize(
    "payload",
    (
        {"root": "A", "maxDepth": 0, "maxPathPrefixes": 1, "nodes": [], "edges": []},
        {"root": "A", "maxDepth": 1, "maxPathPrefixes": 1, "nodes": {}, "edges": []},
        {"root": "A", "maxDepth": 1, "maxPathPrefixes": 1, "nodes": [1], "edges": []},
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [{"id": "A", "admission": "invalid"}],
            "edges": [],
        },
        {
            "root": "   ",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [{"id": "   ", "admission": "admitted"}],
            "edges": [],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [{"id": "A", "admission": "admitted", "extra": True}],
            "edges": [],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [
                {"id": "A", "admission": "admitted"},
                {"id": "A", "admission": "admitted"},
            ],
            "edges": [],
        },
        {
            "root": "missing",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [{"id": "A", "admission": "admitted"}],
            "edges": [],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [{"id": "A", "admission": "admitted"}],
            "edges": {},
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 1,
            "nodes": [{"id": "A", "admission": "admitted"}],
            "edges": [1],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 2,
            "nodes": [{"id": "A", "admission": "admitted"}],
            "edges": [
                {
                    "id": "e",
                    "source": "A",
                    "target": "missing",
                    "result": "TRUE",
                    "admission": "admitted",
                    "covered": True,
                }
            ],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 2,
            "nodes": [
                {"id": "A", "admission": "admitted"},
                {"id": "B", "admission": "admitted"},
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "A",
                    "target": "B",
                    "result": "TRUE",
                    "admission": "admitted",
                    "covered": True,
                    "extra": True,
                }
            ],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 2,
            "nodes": [
                {"id": "A", "admission": "admitted"},
                {"id": "B", "admission": "admitted"},
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "A",
                    "target": "B",
                    "result": "INVALID",
                    "admission": "admitted",
                    "covered": True,
                }
            ],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 2,
            "nodes": [
                {"id": "A", "admission": "admitted"},
                {"id": "B", "admission": "admitted"},
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "A",
                    "target": "B",
                    "result": "TRUE",
                    "admission": "invalid",
                    "covered": True,
                }
            ],
        },
        {
            "root": "A",
            "maxDepth": 1,
            "maxPathPrefixes": 2,
            "nodes": [
                {"id": "A", "admission": "admitted"},
                {"id": "B", "admission": "admitted"},
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "A",
                    "target": "B",
                    "result": "TRUE",
                    "admission": "admitted",
                    "covered": "yes",
                }
            ],
        },
    ),
)
def test_closure_micro_rejects_malformed_graphs(payload: dict[str, Any]) -> None:
    response = handle(_request("closureMicro", payload))
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"


def test_closure_micro_skips_retracted_and_cycle_edges() -> None:
    payload = {
        "root": "A",
        "maxDepth": 4,
        "maxPathPrefixes": 10,
        "nodes": [
            {"id": "A", "admission": "admitted"},
            {"id": "B", "admission": "admitted"},
            {"id": "C", "admission": "retracted"},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "A",
                "target": "B",
                "result": "TRUE",
                "admission": "admitted",
                "covered": True,
            },
            {
                "id": "e2",
                "source": "B",
                "target": "A",
                "result": "TRUE",
                "admission": "admitted",
                "covered": True,
            },
            {
                "id": "e3",
                "source": "B",
                "target": "C",
                "result": "TRUE",
                "admission": "admitted",
                "covered": True,
            },
            {
                "id": "e4",
                "source": "A",
                "target": "C",
                "result": "TRUE",
                "admission": "retracted",
                "covered": True,
            },
        ],
    }
    response = handle(_request("closureMicro", payload))
    assert response["sutStatus"] == "COMPLETED"
    assert [path["targetRef"] for path in response["result"]["paths"]] == ["A", "B"]


def test_closure_micro_rejects_retracted_root_instead_of_traversing_it() -> None:
    response = handle(
        _request(
            "closureMicro",
            {
                "root": "A",
                "maxDepth": 1,
                "maxPathPrefixes": 1,
                "nodes": [{"id": "A", "admission": "retracted"}],
                "edges": [],
            },
        )
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("maxDepth", 33),
        ("maxPathPrefixes", 4097),
    ),
)
def test_closure_micro_clamps_requested_budgets_to_the_published_profile(
    field: str, value: int
) -> None:
    payload = {
        "root": "A",
        "maxDepth": 1,
        "maxPathPrefixes": 1,
        "nodes": [{"id": "A", "admission": "admitted"}],
        "edges": [],
    }
    payload[field] = value
    response = handle(_request("closureMicro", payload))
    assert response["sutStatus"] == "RESOURCE_EXHAUSTED"
    assert response["error"]["code"] == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"
    assert "result" not in response


@pytest.mark.parametrize("include_non_false", (False, True))
def test_closure_micro_records_false_cut_beyond_depth_without_spending_a_prefix(
    include_non_false: bool,
) -> None:
    edges: list[dict[str, Any]] = [
        {
            "id": "e1",
            "source": "A",
            "target": "B",
            "result": "TRUE",
            "admission": "admitted",
            "covered": True,
        },
        {
            "id": "e-false",
            "source": "B",
            "target": "C",
            "result": "FALSE",
            "admission": "admitted",
            "covered": True,
        },
    ]
    nodes = [
        {"id": "A", "admission": "admitted"},
        {"id": "B", "admission": "admitted"},
        {"id": "C", "admission": "admitted"},
    ]
    if include_non_false:
        nodes.append({"id": "D", "admission": "admitted"})
        edges.append(
            {
                "id": "e-true",
                "source": "B",
                "target": "D",
                "result": "TRUE",
                "admission": "admitted",
                "covered": True,
            }
        )
    response = handle(
        _request(
            "closureMicro",
            {
                "root": "A",
                "maxDepth": 1,
                "maxPathPrefixes": 2,
                "nodes": nodes,
                "edges": edges,
            },
        )
    )
    assert response["sutStatus"] == "COMPLETED"
    result = response["result"]
    assert len(result["paths"]) == 2
    boundary_path = next(path for path in result["paths"] if path["targetRef"] == "B")
    assert boundary_path["truncated"] is include_non_false
    assert boundary_path["state"] == ("unknown" if include_non_false else "affected")
    assert result["falseFrontiers"] == [
        {
            "edgeId": "e-false",
            "sourceRef": "B",
            "targetRef": "C",
            "edgeRefs": ["e1", "e-false"],
            "authoritative": True,
        }
    ]
