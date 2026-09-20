"""Canonical JSON and content-addressing helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Preserve the orgrebase.python-json.v1 representation of stored content.

    Cross-language transport uses the explicitly versioned codec in wire.py;
    changing this function would invalidate historical content addresses.
    """

    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_digest(value: Any) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def content_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude={"digest"})


def verify_content_digest(value: Mapping[str, Any], digest: str) -> bool:
    payload = {key: item for key, item in value.items() if key != "digest"}
    return sha256_digest(payload) == digest
