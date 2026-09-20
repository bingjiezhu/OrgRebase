"""RFC 8785 canonicalization and detached resource digests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

import rfc8785
from pydantic import ValidationError

from .models import Resource, ResourceBase, ResourceRef
from .registry import KIND_MODELS, KIND_REGISTRY


class OACValidationError(ValueError):
    """A deterministic validation failure with one stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OACValidationError("CORE_SCHEMA_INVALID", f"duplicate object key: {key}")
        result[key] = value
    return result


def _raw_mapping(data: bytes | str | Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    if isinstance(data, Mapping):
        try:
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        except (TypeError, ValueError, RecursionError) as exc:
            raise OACValidationError("NON_I_JSON", str(exc)) from exc
        return dict(data), raw
    raw = data.encode() if isinstance(data, str) else data
    try:
        decoded = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except OACValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", str(exc)) from exc
    if not isinstance(decoded, dict):
        raise OACValidationError("CORE_SCHEMA_INVALID", "an OAC resource must be a JSON object")
    return decoded, raw


def parse_resource(
    data: bytes | str | Mapping[str, Any], *, verify_digest: bool = False
) -> Resource:
    """Parse one registered resource without kind guessing or permissive extras."""

    decoded, raw = _raw_mapping(data)
    kind = decoded.get("kind")
    if not isinstance(kind, str) or kind not in KIND_MODELS:
        raise OACValidationError("CORE_KIND_UNKNOWN", f"unsupported resource kind: {kind!r}")
    model = KIND_MODELS[kind]
    try:
        resource = model.model_validate_json(raw)
    except ValidationError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", str(exc)) from exc
    if verify_digest:
        verify_resource_digest(resource)
    return resource


def canonical_projection(resource: ResourceBase | Mapping[str, Any]) -> dict[str, Any]:
    """Return the registered non-self-referential digest projection."""

    kind: object
    if isinstance(resource, ResourceBase):
        value = resource.model_dump(mode="json", by_alias=True)
        kind = resource.kind
    else:
        value = dict(resource)
        kind = value.get("kind")
    entry = KIND_REGISTRY.get(cast(str, kind))
    if entry is None:
        raise OACValidationError("CORE_KIND_UNKNOWN", f"unsupported resource kind: {kind!r}")
    for field in entry.detached_fields:
        value.pop(field, None)
    return value


def canonical_bytes(resource: ResourceBase | Mapping[str, Any]) -> bytes:
    try:
        return rfc8785.dumps(canonical_projection(resource))
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise OACValidationError("NON_I_JSON", str(exc)) from exc


def calculate_digest(resource: ResourceBase | Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(resource)).hexdigest()}"


def seal_resource[ResourceT: ResourceBase](resource: ResourceT) -> ResourceT:
    """Return an immutable successor carrying its detached digest."""

    return cast(ResourceT, resource.model_copy(update={"digest": calculate_digest(resource)}))


def verify_resource_digest(resource: ResourceBase) -> None:
    if resource.digest is None:
        raise OACValidationError("DIGEST_MISSING", "resource has no detached digest")
    expected = calculate_digest(resource)
    if resource.digest != expected:
        code = (
            "PLAN_DIGEST_MISMATCH"
            if resource.kind == "OrganizationPlan"
            else "ROOT_DIGEST_MISMATCH"
        )
        raise OACValidationError(code, f"expected {expected}, got {resource.digest}")


def resource_ref(resource: ResourceBase) -> ResourceRef:
    verify_resource_digest(resource)
    return ResourceRef(
        kind=resource.kind,
        namespace=resource.metadata.namespace,
        resourceId=resource.metadata.id,
        revision=resource.metadata.revision,
        digest=cast(str, resource.digest),
    )
