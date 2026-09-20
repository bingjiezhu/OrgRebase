"""Lossless admission for digest-bound OAC resources.

``parse_resource`` is the stable legacy convenience parser.  This module is a
stricter A1 overlay: the decoded wire object, rather than a normalized model
dump, owns the digest and is required to survive typed validation unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

import rfc8785
from pydantic import ValidationError

from .canonical import OACValidationError
from .json_types import JsonValue
from .models import Resource
from .registry import KIND_MODELS
from .resource_profile import ResourceProfileExceeded

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WHOLE_SECOND_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Supplier v0.2 declares these input arrays as sets.  Their order remains
# observable in the admitted raw resource, but duplicate members are invalid;
# admission never silently deduplicates or sorts them.
_SUPPLIER_SET_OWNED_ARRAY_KEYS = frozenset(
    {
        "afterValues",
        "coveredNodeRefs",
        "coveredRelationTypes",
        "discoveryEvidence",
        "domainRefs",
        "eligibleRoleRefs",
        "knownGaps",
        "nodeTypes",
        "prerequisiteObligationTypes",
        "qualificationRefs",
        "refs",
        "relationTypes",
        "requiredEvidence",
        "requiredQualifications",
        "responsibilityTypes",
        "scopeRefs",
        "sourceRefs",
        "subjectRefs",
    }
)

type FrozenJSON = (
    bool | int | float | str | tuple["FrozenJSON", ...] | Mapping[str, "FrozenJSON"] | None
)


@dataclass(frozen=True, slots=True)
class AdmittedSealedResource:
    """Immutable result of ``SealedResource/v1`` admission."""

    raw_map: Mapping[str, FrozenJSON]
    resource: Resource
    resource_digest: str

    @property
    def raw(self) -> Mapping[str, FrozenJSON]:
        """Short alias for callers that name the admitted decoded value ``raw``."""

        return self.raw_map

    @property
    def decoded(self) -> Mapping[str, FrozenJSON]:
        """Explicit alias emphasizing that lexical JSON whitespace is not retained."""

        return self.raw_map

    @property
    def typed(self) -> Resource:
        """Alias naming the validated Resource side of the admission record."""

        return self.resource


# The long name emphasizes that admission already happened; the short public
# alias matches the contract name used by stdio-v2 and its prose.
SealedResource = AdmittedSealedResource


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OACValidationError("CORE_SCHEMA_INVALID", f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise OACValidationError("CORE_SCHEMA_INVALID", f"non-JSON numeric token: {value}")


def _normalize_decoded_binary64(value: object) -> object:
    """Materialize the decoded JSON value after binary64 parsing.

    Integral doubles become Python integers so strict typed integer fields do
    not depend on whether the source token used decimal or exponent notation.
    This conversion cannot recover precision absent from the parsed binary64;
    notably, the token ``9007199254740993`` becomes ``9007199254740992``.
    """

    if isinstance(value, float):
        if not math.isfinite(value):
            raise OACValidationError("NON_I_JSON", "JSON number is outside binary64")
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        return [_normalize_decoded_binary64(child) for child in value]
    if isinstance(value, dict):
        return {key: _normalize_decoded_binary64(child) for key, child in value.items()}
    return value


def _decode_raw_object(
    raw: bytes, *, max_json_depth: int | None = None
) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", f"invalid UTF-8 JSON: {exc}") from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_int=float,
            parse_float=float,
            parse_constant=_reject_non_finite_constant,
        )
        if not isinstance(decoded, dict):
            raise OACValidationError("CORE_SCHEMA_INVALID", "an OAC resource must be a JSON object")
        if max_json_depth is not None:
            depth = _json_depth(decoded)
            if depth > max_json_depth:
                raise ResourceProfileExceeded("jsonDepth", depth, max_json_depth)
        normalized = _normalize_decoded_binary64(decoded)
    except OACValidationError:
        raise
    except RecursionError as exc:
        if max_json_depth is not None:
            raise ResourceProfileExceeded("jsonDepth", None, max_json_depth) from exc
        raise OACValidationError("CORE_SCHEMA_INVALID", "JSON nesting exceeds the decoder limit") from exc
    except json.JSONDecodeError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", f"invalid JSON: {exc}") from exc
    assert isinstance(normalized, dict)
    return normalized


def _binary64_projection(value: JsonValue) -> JsonValue:
    """Project decoded JSON numbers into RFC 8785's IEEE-754 domain.

    In-memory callers may still supply arbitrary Python integers while JCS
    specifies ECMAScript binary64 number serialization.  This projection
    produces the required rounding and rendering, including ``2**53``; raw
    admission has already applied the same binary64 interpretation at decode.
    """

    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError as exc:
            raise OACValidationError("NON_I_JSON", str(exc)) from exc
        if not math.isfinite(number):
            raise OACValidationError("NON_I_JSON", "JSON number is outside binary64")
        return number
    if isinstance(value, list):
        return [_binary64_projection(child) for child in value]
    if isinstance(value, dict):
        return {key: _binary64_projection(child) for key, child in value.items()}
    return value


def _jcs(value: JsonValue) -> bytes:
    try:
        return rfc8785.dumps(_binary64_projection(value))
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise OACValidationError("NON_I_JSON", str(exc)) from exc


def _reject_supplier_set_duplicates(
    value: object, *, pointer: str = "", ordered_outcome_provenance: bool = False
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = f"{pointer}/{key}"
            is_role_sequence = (
                ordered_outcome_provenance and child_pointer == "/metadata/sourceRefs"
            )
            if key in _SUPPLIER_SET_OWNED_ARRAY_KEYS and isinstance(child, list) and not is_role_sequence:
                encoded_members = [_jcs(member) for member in child]
                if len(encoded_members) != len(set(encoded_members)):
                    raise OACValidationError(
                        "CORE_SCHEMA_INVALID",
                        f"set-owned array contains a duplicate member: {child_pointer}",
                    )
            _reject_supplier_set_duplicates(
                child, pointer=child_pointer, ordered_outcome_provenance=ordered_outcome_provenance
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_supplier_set_duplicates(
                child, pointer=f"{pointer}/{index}", ordered_outcome_provenance=ordered_outcome_provenance
            )


def _validate_timestamp_lexemes(decoded: dict[str, Any], kind: str) -> None:
    paths: list[tuple[str, str, bool]] = [
        ("metadata", "createdAt", False),
        ("metadata", "effectiveFrom", True),
        ("metadata", "effectiveTo", True),
    ]
    if kind == "SemanticChangeSet":
        paths.extend(
            (
                ("spec", "observedAt", False),
                ("spec", "effectiveAt", False),
            )
        )
    for container_name, field, nullable in paths:
        container = decoded.get(container_name)
        if not isinstance(container, dict) or field not in container:
            continue
        value = container[field]
        if nullable and value is None:
            continue
        if not isinstance(value, str) or _WHOLE_SECOND_UTC_RE.fullmatch(value) is None:
            raise OACValidationError(
                "CORE_SCHEMA_INVALID",
                f"/{container_name}/{field} must use whole-second UTC YYYY-MM-DDTHH:MM:SSZ",
            )


def _exact_json_equal(left: object, right: object) -> bool:
    """Compare decoded JSON without Python's ``True == 1`` coercion."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if not isinstance(right, dict) or left.keys() != right.keys():
            return False
        return all(_exact_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return (
            isinstance(right, list)
            and len(left) == len(right)
            and all(
                _exact_json_equal(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        )
    return left == right


def _freeze_json(value: object) -> FrozenJSON:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return cast(bool | int | float | str | None, value)


def _json_depth(value: object) -> int:
    maximum = 1
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(current, dict):
            pending.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
    return maximum


def admit_sealed_resource(
    raw: bytes,
    expected_kind: str,
    *,
    max_json_depth: int | None = None,
) -> AdmittedSealedResource:
    """Admit exact raw bytes through the ``SealedResource/v1`` boundary.

    The digest is SHA-256 over RFC 8785 bytes of the decoded map after removing
    only its top-level ``digest`` member.  Typed validation is deliberately
    non-authoritative: its JSON projection must equal the admitted map exactly.
    """

    decoded = _decode_raw_object(raw, max_json_depth=max_json_depth)

    if "apiVersion" not in decoded or not isinstance(decoded["apiVersion"], str):
        raise OACValidationError("CORE_SCHEMA_INVALID", "explicit apiVersion is required")
    if "kind" not in decoded or not isinstance(decoded["kind"], str):
        raise OACValidationError("CORE_SCHEMA_INVALID", "explicit kind is required")
    claimed_digest = decoded.get("digest")
    if not isinstance(claimed_digest, str) or _DIGEST_RE.fullmatch(claimed_digest) is None:
        raise OACValidationError(
            "CORE_SCHEMA_INVALID", "explicit non-null lowercase SHA-256 digest is required"
        )

    if not isinstance(expected_kind, str) or expected_kind not in KIND_MODELS:
        raise OACValidationError(
            "CORE_KIND_UNKNOWN", f"unsupported expected resource kind: {expected_kind!r}"
        )
    raw_kind = decoded["kind"]
    if raw_kind not in KIND_MODELS:
        raise OACValidationError("CORE_KIND_UNKNOWN", f"unsupported resource kind: {raw_kind!r}")
    if raw_kind != expected_kind:
        raise OACValidationError(
            "CORE_SCHEMA_INVALID",
            f"expected resource kind {expected_kind!r}, got {raw_kind!r}",
        )

    _validate_timestamp_lexemes(decoded, raw_kind)

    projection = dict(decoded)
    projection.pop("digest")
    resource_digest = f"sha256:{hashlib.sha256(_jcs(projection)).hexdigest()}"
    if claimed_digest != resource_digest:
        raise OACValidationError(
            "ROOT_DIGEST_MISMATCH",
            f"expected {resource_digest}, got {claimed_digest}",
        )

    # Default Outcome provenance is an ordered role sequence, not a Supplier set.
    # Its verifier checks exact role order and uniqueness within each role.
    raw_spec = decoded.get("spec")
    _reject_supplier_set_duplicates(
        decoded,
        ordered_outcome_provenance=(
            raw_kind == "OutcomeCertificate"
            and isinstance(raw_spec, dict)
            and "profileBinding" not in raw_spec
        ),
    )

    model = KIND_MODELS[expected_kind]
    try:
        typed_input = json.dumps(
            decoded,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        resource = model.model_validate_json(typed_input)
    except ValidationError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", str(exc)) from exc

    typed_projection = resource.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
    )
    if not _exact_json_equal(decoded, typed_projection):
        raise OACValidationError(
            "CANONICAL_ADMISSION_MISMATCH",
            "typed validation changed the admitted decoded resource",
        )

    frozen = _freeze_json(decoded)
    assert isinstance(frozen, Mapping)
    return AdmittedSealedResource(
        raw_map=frozen,
        resource=cast(Resource, resource),
        resource_digest=resource_digest,
    )


def _thaw_json(value: FrozenJSON) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def validate_sealed_admission(admission: AdmittedSealedResource, expected_kind: str) -> AdmittedSealedResource:
    """Revalidate exact raw identity and return fresh typed semantic views.

    An admission record is a data structure, not an unforgeable authority token.
    Semantic entrypoints therefore check both its raw commitment and its typed
    projection; omitted members must stay omitted and explicit nulls stay present.
    """
    admitted = admit_sealed_resource(_jcs(_thaw_json(admission.raw_map)), expected_kind)
    if (admission.resource_digest != admitted.resource_digest
            or not isinstance(admission.resource, type(admitted.resource))
            or not _exact_json_equal(
                admission.resource.model_dump(mode="json", by_alias=True, exclude_unset=True),
                admitted.resource.model_dump(mode="json", by_alias=True, exclude_unset=True))):
        raise OACValidationError("CANONICAL_ADMISSION_MISMATCH", "admission record does not bind its exact typed resource")
    return admitted
