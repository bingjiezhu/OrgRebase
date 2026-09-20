"""Canonical OAC witness-reference encoding.

A witness is an escaped resource identifier, one literal ``#`` delimiter, and
an escaped RFC 6901 JSON Pointer.  Decoders reject alternate spellings so a
single semantic witness has a single wire representation.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote_to_bytes

from .canonical import OACValidationError
from .text import has_oac_non_whitespace

_RESOURCE_SAFE = ":/?@!$&'()*+,;=-._~"
_POINTER_SAFE = "/~-._"
_PERCENT_ESCAPE = re.compile(r"%[0-9A-F]{2}")


def _encode_component(value: str, *, safe: str) -> str:
    return quote(value, safe=safe, encoding="utf-8", errors="strict")


def _decode_component(value: str, *, safe: str) -> str:
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if not _PERCENT_ESCAPE.fullmatch(value[index : index + 3]):
            raise OACValidationError(
                "APPLICABILITY_WITNESS_MISMATCH",
                "witness percent escapes must use uppercase hexadecimal",
            )
        index += 3
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OACValidationError(
            "APPLICABILITY_WITNESS_MISMATCH",
            "witness component is not canonical UTF-8",
        ) from exc
    if _encode_component(decoded, safe=safe) != value:
        raise OACValidationError(
            "APPLICABILITY_WITNESS_MISMATCH",
            "witness component has a non-canonical percent encoding",
        )
    return decoded


def _validate_pointer(pointer: str) -> None:
    if pointer and not pointer.startswith("/"):
        raise OACValidationError(
            "APPLICABILITY_WITNESS_MISMATCH",
            "witness pointer must be an RFC 6901 JSON Pointer",
        )
    for token in pointer.split("/")[1:]:
        index = 0
        while index < len(token):
            if token[index] != "~":
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in "01":
                raise OACValidationError(
                    "APPLICABILITY_WITNESS_MISMATCH",
                    "witness pointer contains an invalid RFC 6901 escape",
                )
            index += 2


def format_witness_ref(resource_id: str, pointer: str) -> str:
    """Encode a resource identifier and decoded JSON Pointer canonically."""

    if not resource_id or not has_oac_non_whitespace(resource_id):
        raise OACValidationError(
            "APPLICABILITY_WITNESS_MISMATCH",
            "witness resource identifier is empty or White_Space-only",
        )
    _validate_pointer(pointer)
    resource = _encode_component(resource_id, safe=_RESOURCE_SAFE)
    encoded_pointer = _encode_component(pointer, safe=_POINTER_SAFE)
    return f"{resource}#{encoded_pointer}"


def parse_witness_ref(value: str) -> tuple[str, str]:
    """Decode a canonical witness and return ``(resource_id, json_pointer)``."""

    if value.count("#") != 1:
        raise OACValidationError(
            "APPLICABILITY_WITNESS_MISMATCH",
            "witness must contain exactly one literal # delimiter",
        )
    resource_component, pointer_component = value.split("#", 1)
    resource_id = _decode_component(resource_component, safe=_RESOURCE_SAFE)
    pointer = _decode_component(pointer_component, safe=_POINTER_SAFE)
    if not resource_id or not has_oac_non_whitespace(resource_id):
        raise OACValidationError(
            "APPLICABILITY_WITNESS_MISMATCH",
            "witness resource identifier is empty or White_Space-only",
        )
    _validate_pointer(pointer)
    if format_witness_ref(resource_id, pointer) != value:
        raise OACValidationError("APPLICABILITY_WITNESS_MISMATCH", "witness is not canonical")
    return resource_id, pointer
