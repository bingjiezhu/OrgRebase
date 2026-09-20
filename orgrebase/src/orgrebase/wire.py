"""Versioned JSON transport digests; stored content keeps its original codec."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import rfc8785
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from orgrebase.digest import canonical_json

WIRE_SCHEME = "orgrebase.jcs-safe-number.v1"
CONTENT_SCHEME = "orgrebase.python-json.v1"
MAX_SAFE_NUMBER = 9_007_199_254_740_991


def _validate(value: Any, depth: int = 0) -> None:
    if depth > 128:
        raise ValueError("WIRE_DEPTH_EXCEEDED")
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        if abs(value) > MAX_SAFE_NUMBER or not math.isfinite(value):
            raise ValueError("WIRE_NUMBER_OUT_OF_RANGE")
        return
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("WIRE_UNICODE_INVALID") from None
        return
    if type(value) is list:
        for item in value:
            _validate(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, item in value.items():
            _validate(key, depth + 1)
            _validate(item, depth + 1)
        return
    raise ValueError("WIRE_JSON_VALUE_REQUIRED")


def canonical_wire(value: Any) -> bytes:
    _validate(value)
    return rfc8785.dumps(value)


def protocol_digest(value: Any, scheme: str = WIRE_SCHEME) -> str:
    if scheme == WIRE_SCHEME:
        raw = canonical_wire(value)
    elif scheme == CONTENT_SCHEME:
        raw = canonical_json(value).encode("utf-8")
    else:
        raise ValueError("WIRE_SCHEME_UNSUPPORTED")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_wire(raw: str | bytes) -> Any:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("WIRE_DUPLICATE_KEY")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique_keys)
    _validate(value)
    return value


class WireJSONResponse(JSONResponse):
    def __init__(self, content: Any, status_code: int = 200, headers: Mapping[str, str] | None = None,
                 media_type: str | None = None, background: BackgroundTask | None = None) -> None:
        super().__init__(content, status_code=status_code, headers=headers, media_type=media_type, background=background)
        # Hash what is actually sent. This header is a transport consistency
        # check, not a signature, approval, or replacement for artifact digests.
        self.headers["OrgRebase-Wire-Scheme"] = WIRE_SCHEME
        self.headers["OrgRebase-Wire-Digest"] = protocol_digest(parse_wire(self.body))
        self.headers["OrgRebase-Content-Scheme"] = CONTENT_SCHEME
