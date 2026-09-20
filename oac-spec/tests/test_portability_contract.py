from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError

from oac.canonical import OACValidationError
from oac.cli import main, normative_semantic_validation_rules_json
from oac.sealed import _exact_json_equal, _jcs, admit_sealed_resource

ROOT = Path(__file__).resolve().parents[1]
CHANGE_PATH = ROOT / "profiles/supplier-change/inputs/SC-008.change.json"
SNAPSHOT_PATH = ROOT / "profiles/supplier-change/inputs/veracier-proc01-contextual.snapshot.json"


def _reseal(value: dict[str, Any]) -> bytes:
    projection = copy.deepcopy(value)
    projection.pop("digest", None)
    value = {**projection, "digest": f"sha256:{hashlib.sha256(_jcs(projection)).hexdigest()}"}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _change() -> dict[str, Any]:
    return json.loads(CHANGE_PATH.read_bytes())


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_bytes())


def _reason(raw: bytes, expected_kind: str) -> str:
    with pytest.raises(OACValidationError) as exc_info:
        admit_sealed_resource(raw, expected_kind)
    return exc_info.value.reason_code


def test_sealed_resource_admits_raw_resealed_resource_without_materializing_defaults() -> None:
    value = _change()
    value.pop("digest")
    assert "effectiveTo" not in value["metadata"]
    admitted = admit_sealed_resource(_reseal(value), "SemanticChangeSet")

    assert admitted.resource.kind == "SemanticChangeSet"
    assert admitted.resource.metadata.effective_to is None
    assert admitted.resource_digest == admitted.raw_map["digest"]
    assert admitted.raw is admitted.raw_map
    assert admitted.decoded is admitted.raw_map
    assert admitted.typed is admitted.resource
    assert "effectiveTo" not in admitted.raw_map["metadata"]  # type: ignore[operator]
    with pytest.raises(TypeError):
        admitted.raw_map["kind"] = "OrganizationSnapshot"  # type: ignore[index]


def test_sealed_resource_preserves_explicit_nullable_default() -> None:
    value = _change()
    value["metadata"]["effectiveTo"] = None
    admitted = admit_sealed_resource(_reseal(value), "SemanticChangeSet")
    assert admitted.raw_map["metadata"]["effectiveTo"] is None  # type: ignore[index]


@pytest.mark.parametrize("missing", ["apiVersion", "kind", "digest"])
def test_sealed_resource_requires_explicit_non_null_envelope(missing: str) -> None:
    value = _change()
    value.pop(missing)
    raw = json.dumps(value, separators=(",", ":")).encode()
    assert _reason(raw, "SemanticChangeSet") == "CORE_SCHEMA_INVALID"

    if missing == "digest":
        value["digest"] = None
        assert _reason(json.dumps(value).encode(), "SemanticChangeSet") == ("CORE_SCHEMA_INVALID")


def test_sealed_resource_rejects_identifier_trim() -> None:
    whitespace = _change()
    whitespace["metadata"]["id"] = " change:SC-008"
    assert _reason(_reseal(whitespace), "SemanticChangeSet") == ("CANONICAL_ADMISSION_MISMATCH")


@pytest.mark.parametrize(
    ("container", "field", "value"),
    (
        ("metadata", "createdAt", "2026-08-23T00:00:00.1Z"),
        ("metadata", "effectiveFrom", "2025-03-01T09:00:00+00:00"),
        ("metadata", "effectiveTo", "2025-03-01T09:00:00-07:00"),
        ("spec", "observedAt", "2025-03-01T09:00:00.000000Z"),
        ("spec", "effectiveAt", "2025-03-01T09:00:00z"),
    ),
)
def test_sealed_resource_rejects_non_whole_second_utc_timestamp_lexemes(
    container: str, field: str, value: str
) -> None:
    timestamp_alias = _change()
    timestamp_alias[container][field] = value
    assert _reason(_reseal(timestamp_alias), "SemanticChangeSet") == "CORE_SCHEMA_INVALID"


def test_sealed_resource_rejects_duplicate_json_and_non_finite_i_json() -> None:
    valid = _reseal(_change())
    duplicate = valid.replace(
        b'"apiVersion":"oac.dev/v0alpha1"',
        b'"apiVersion":"oac.dev/v0alpha1","apiVersion":"oac.dev/v0alpha1"',
        1,
    )
    assert _reason(duplicate, "SemanticChangeSet") == "CORE_SCHEMA_INVALID"

    non_finite = valid.replace(b'"revision":1', b'"revision":NaN', 1)
    assert _reason(non_finite, "SemanticChangeSet") == "CORE_SCHEMA_INVALID"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"\xff", "CORE_SCHEMA_INVALID"),
        (b"{", "CORE_SCHEMA_INVALID"),
        (b"[]", "CORE_SCHEMA_INVALID"),
    ],
)
def test_sealed_resource_rejects_non_object_or_malformed_input(raw: bytes, reason: str) -> None:
    assert _reason(raw, "SemanticChangeSet") == reason

    with pytest.raises(TypeError):
        admit_sealed_resource("{}", "SemanticChangeSet")  # type: ignore[arg-type]


def test_sealed_resource_rejects_digest_kind_and_set_owned_duplicate() -> None:
    mismatch = _change()
    mismatch["digest"] = "sha256:" + "0" * 64
    assert _reason(json.dumps(mismatch).encode(), "SemanticChangeSet") == ("ROOT_DIGEST_MISMATCH")

    assert _reason(_reseal(_change()), "OrganizationSnapshot") == "CORE_SCHEMA_INVALID"

    duplicate_set = _change()
    duplicate_set["spec"]["scopeRefs"].append(duplicate_set["spec"]["scopeRefs"][0])
    assert _reason(_reseal(duplicate_set), "SemanticChangeSet") == "CORE_SCHEMA_INVALID"

    unsupported = _change()
    unsupported["kind"] = "FutureResource"
    assert _reason(_reseal(unsupported), "SemanticChangeSet") == "CORE_KIND_UNKNOWN"

    assert _reason(_reseal(_change()), "FutureResource") == "CORE_KIND_UNKNOWN"


def test_sealed_resource_rejects_out_of_domain_integer_and_typed_schema_failure() -> None:
    out_of_domain = _change()
    out_of_domain["metadata"]["revision"] = 10**400
    # A syntactically valid claimed digest cannot make the projection enter the
    # finite RFC 8785 domain; admission fails while computing the real digest.
    out_of_domain["digest"] = "sha256:" + "0" * 64
    assert _reason(json.dumps(out_of_domain).encode(), "SemanticChangeSet") == "NON_I_JSON"

    invalid_typed_resource = _change()
    del invalid_typed_resource["spec"]["reason"]
    assert _reason(_reseal(invalid_typed_resource), "SemanticChangeSet") == ("CORE_SCHEMA_INVALID")


def test_exact_json_comparison_does_not_inherit_python_boolean_integer_equality() -> None:
    assert not _exact_json_equal(True, 1)
    assert not _exact_json_equal({"a": 1}, {"b": 1})
    assert not _exact_json_equal([1], [1, 2])


def test_sealed_resource_accepts_existing_raw_bound_snapshot() -> None:
    admitted = admit_sealed_resource(SNAPSHOT_PATH.read_bytes(), "OrganizationSnapshot")
    assert admitted.resource_digest == _snapshot()["digest"]


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
        ("9007199254740993", "9007199254740992"),
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
def test_sealed_jcs_matches_rfc8785_appendix_b(raw: str, canonical: str) -> None:
    assert _jcs(json.loads(raw)) == canonical.encode()


def test_sealed_jcs_rejects_non_finite_or_non_json_projection_values() -> None:
    with pytest.raises(OACValidationError) as non_finite:
        _jcs(float("inf"))
    assert non_finite.value.reason_code == "NON_I_JSON"

    with pytest.raises(OACValidationError) as integer_overflow:
        _jcs(10**400)
    assert integer_overflow.value.reason_code == "NON_I_JSON"

    with pytest.raises(OACValidationError) as non_json:
        _jcs(object())
    assert non_json.value.reason_code == "NON_I_JSON"


@pytest.mark.parametrize(
    ("revision", "decoded_revision"),
    (
        (9007199254740992, 9007199254740992),
        (9007199254740993, 9007199254740992),
    ),
)
def test_sealed_resource_admits_finite_binary64_integer_tokens(
    revision: int, decoded_revision: int
) -> None:
    value = _change()
    value["metadata"]["revision"] = revision
    admitted = admit_sealed_resource(_reseal(value), "SemanticChangeSet")
    assert admitted.resource.metadata.revision == decoded_revision
    assert admitted.raw_map["metadata"]["revision"] == decoded_revision  # type: ignore[index]


def test_binary64_rounding_cannot_create_same_digest_with_different_typed_semantics() -> None:
    exact_value = _change()
    exact_value["metadata"]["revision"] = 9007199254740992
    exact_raw = _reseal(exact_value)
    rounded_raw = exact_raw.replace(b"9007199254740992", b"9007199254740993", 1)

    exact = admit_sealed_resource(exact_raw, "SemanticChangeSet")
    rounded = admit_sealed_resource(rounded_raw, "SemanticChangeSet")

    assert exact.resource_digest == rounded.resource_digest
    assert exact.raw_map == rounded.raw_map
    assert exact.resource == rounded.resource
    assert rounded.resource.metadata.revision == 9007199254740992


def test_normative_registry_is_digest_bound_and_has_no_binding_leakage(
    capfd: pytest.CaptureFixture[str],
) -> None:
    registry = normative_semantic_validation_rules_json()
    projection = {key: value for key, value in registry.items() if key != "digest"}
    expected = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    assert registry["digest"] == expected
    assert set(registry) == {"apiVersion", "registryKind", "rules", "digest"}

    encoded = json.dumps(registry, ensure_ascii=False).casefold()
    for forbidden in (
        "implementationbindings",
        "runtime",
        "cli",
        "library",
        "validator",
        "ffi",
        "process",
        "service",
    ):
        assert forbidden not in encoded

    schema = json.loads(
        (ROOT / "ctk/schemas/NormativeSemanticRuleRegistry.schema.json").read_bytes()
    )
    Draft202012Validator(schema).validate(registry)

    assert main(["registry", "normative-semantic-validation-rules"]) == 0
    assert json.loads(capfd.readouterr().out) == registry


def _independence_statement() -> dict[str, Any]:
    digest = "sha256:" + "1" * 64
    return {
        "apiVersion": "oac.ctk.independence/v0.1",
        "kind": "IndependenceStatement",
        "statementId": "statement:internal-go-v0.1",
        "implementation": {
            "implementationId": "oac.supplier.go.internal",
            "implementationVersion": "0.1.0",
            "artifactDigest": digest,
        },
        "maintainer": {
            "maintainerId": "maintainer:project",
            "organizationRef": None,
            "evidenceRefs": [],
        },
        "source": {
            "repositoryRef": "local:experiments/supplier-v02-portability",
            "revision": "uncommitted-development-tree",
            "sourceDigest": digest,
        },
        "build": {
            "buildSystem": "go build",
            "recipeDigest": digest,
            "environmentDigest": digest,
            "artifactDigest": digest,
        },
        "sbom": {
            "format": "SPDX",
            "documentRef": "sbom:internal-go-v0.1",
            "documentDigest": digest,
        },
        "referenceAccess": {
            "level": "reference_source_observed",
            "artifactRefs": ["src/oac/supplier.py"],
            "attestation": "Internally authored differential evidence; not a clean room.",
        },
        "generatedCode": {
            "used": False,
            "generatorRefs": [],
            "generatedSourceDigest": None,
        },
        "dependencies": {
            "language": [
                {
                    "name": "Go",
                    "version": "1.25",
                    "artifactDigest": digest,
                    "sharedWithReference": False,
                    "semanticRole": "semantic_kernel",
                }
            ],
            "runtime": [],
            "library": [],
            "ffi": [],
            "process": [],
            "service": [],
        },
        "claimLimit": "provenance_and_dependency_disclosure_only",
        "digest": digest,
    }


def test_independence_statement_is_closed_and_cannot_self_award_independence() -> None:
    schema = json.loads((ROOT / "ctk/schemas/IndependenceStatement.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    statement = _independence_statement()
    validator.validate(statement)

    inflated = copy.deepcopy(statement)
    inflated["organizationalIndependence"] = True
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(inflated)

    inflated = copy.deepcopy(statement)
    inflated["claimLimit"] = "organizational_independence"
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(inflated)

    incomplete = copy.deepcopy(statement)
    del incomplete["dependencies"]["service"]
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(incomplete)
