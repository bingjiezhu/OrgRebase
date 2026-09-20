"""Frozen lexical primitives shared by OAC conformance operations."""

from __future__ import annotations


def is_oac_whitespace(character: str) -> bool:
    """Return whether one code point is in Unicode's White_Space property.

    The explicit set avoids runtime-specific predicates such as ``str.isspace``.
    In particular, U+001C..U+001F are control characters but are not White_Space.
    """

    code_point = ord(character)
    return (
        0x0009 <= code_point <= 0x000D
        or code_point in {0x0020, 0x0085, 0x00A0, 0x1680}
        or 0x2000 <= code_point <= 0x200A
        or code_point in {0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
    )


def has_oac_non_whitespace(value: str) -> bool:
    """Return whether ``value`` contains a code point outside White_Space."""

    return any(not is_oac_whitespace(character) for character in value)
