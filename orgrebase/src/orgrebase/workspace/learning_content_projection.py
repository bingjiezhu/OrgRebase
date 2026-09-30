"""Default-safe historical read projection for embedded learning content.

This module changes response *views*, not canonical artifacts or model input.
No generic state/history/export caller receives a historical-content grant by
holding ordinary read authority. Exact content remains available only to the
separately authorized command path that owns its current purpose and retention
checks. Legacy inline copies remain physically present until Spec011 migration.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from orgrebase.digest import sha256_digest

ProjectionSurface = Literal["state", "history", "export", "api", "cli", "other"]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_:-]{1,127}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,511}$")
_SENSITIVE_FIELDS = frozenset({
    "advice_text", "instruction_text", "reference_text", "prompt_template",
    "error_message", "exception_message", "traceback", "raw_output",
    "raw_response", "response_text", "content_base64", "skill_bytes_base64",
    "request_json", "response_json", "wire_json", "raw_request",
})
_SAFE_VIEW_PREFIX = "orgrebase.learning-"
_SAFE_VIEW_SUFFIX = "-view.v1"

# Inventory is the default disclosure class, not a claim that a given source
# or released content is currently qualified for historical disclosure.
CONTENT_CLASSES: dict[str, dict[str, str]] = {
    "PUBLIC_REVIEWED_SKILL_RESOURCE": {
        "origin": "SkillContentBundleV2 reviewed release",
        "stored_at": "StateStore skill-content-v2 artifact and embedded V4 request",
        "generic_history": "METADATA_ONLY_UNTIL_RELEASE_SCOPE_VERIFIED",
    },
    "REVIEWED_LIMITED_LESSON": {
        "origin": "ExperienceLessonService reviewed publication",
        "stored_at": "PrivateRecordStore and embedded V4 advice_text/wire/Preview",
        "generic_history": "METADATA_ONLY_NO_HISTORY_GRANT",
    },
    "UNREVIEWED_CANDIDATE_OR_TRACE": {
        "origin": "author/optimizer/model candidate and development trace",
        "stored_at": "PrivateRecordStore or legacy inline candidate/trace",
        "generic_history": "METADATA_ONLY_NO_PUBLICATION_INFERENCE",
    },
    "MODEL_GENERATED_OUTPUT": {
        "origin": "V4 provider response/candidate explanation",
        "stored_at": "V4 receipt, advisory handoff and Preview",
        "generic_history": "METADATA_ONLY_NO_QUALITY_INFERENCE",
    },
    "MODEL_WIRE_COPY": {
        "origin": "compiled request/final provider body",
        "stored_at": "advisory/Preview and private evaluation trace",
        "generic_history": "METADATA_ONLY_NO_WIRE_REPLAY",
    },
    "UNKNOWN_LEARNING_TEXT": {
        "origin": "unclassified historical text/error field",
        "stored_at": "legacy inline or future extension",
        "generic_history": "METADATA_ONLY_FAIL_CLOSED",
    },
}


def _view_body(
    surface: ProjectionSurface, payload: Any, redacted_paths: tuple[str, ...],
    content_classes: tuple[str, ...], original_logical_digest: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.learning-content-safe-view.v1",
        "surface": surface,
        "payload": payload,
        "redacted_count": len(redacted_paths),
        "content_classes": list(content_classes),
        "original_logical_digest": original_logical_digest,
        "content_status": "METADATA_ONLY_NO_HISTORICAL_CONTENT_GRANT",
        "storage_status": "LEGACY_CANONICAL_INLINE_MAY_REMAIN",
        "digest_meaning": "VIEW_DIGEST_NOT_ORIGINAL_LOGICAL_DIGEST",
    }


@dataclass(frozen=True)
class LearningContentProjection:
    surface: ProjectionSurface
    payload: Any
    redacted_paths: tuple[str, ...]
    content_classes: tuple[str, ...]
    original_logical_digest: str | None
    view_digest: str

    def as_response(self) -> dict[str, Any]:
        """Versioned view wrapper; never masquerade as the original object."""
        body = _view_body(
            self.surface, self.payload, self.redacted_paths,
            self.content_classes, self.original_logical_digest,
        )
        if sha256_digest(body) != self.view_digest:
            raise ValueError("LEARNING_PROJECTION_MUTATED_AFTER_BUILD")
        return {**body, "view_digest": self.view_digest}


def _digest(value: Any) -> str | None:
    return value if isinstance(value, str) and _DIGEST.fullmatch(value) else None


def _code(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None


def _project_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"schema_version": "orgrebase.learning-advice-context-view.v1",
                "content_status": "UNKNOWN", "reason_code": "CONTEXT_INVALID"}
    lessons = value.get("lessons")
    items = []
    if isinstance(lessons, list):
        for item in lessons[:3]:
            if isinstance(item, Mapping):
                items.append({
                    "ref": _id(item.get("ref")),
                    "revision": item.get("revision") if type(item.get("revision")) is int else None,
                    "content_digest": _digest(item.get("content_digest")),
                })
    return {
        "schema_version": "orgrebase.learning-advice-context-view.v1",
        "profile": _id(value.get("profile")),
        "execution_mode": _id(value.get("execution_mode")),
        "head_ref": _id(value.get("head_ref")),
        "head_digest": _digest(value.get("head_digest")),
        "package_ref": _id(value.get("package_ref")),
        "package_digest": _digest(value.get("package_digest")),
        "instruction_ref": _id(value.get("instruction_ref")),
        "instruction_digest": _digest(value.get("instruction_digest")),
        "reference_ref": _id(value.get("reference_ref")),
        "reference_digest": _digest(value.get("reference_digest")),
        "memory_snapshot_ref": _id(value.get("memory_snapshot_ref")),
        "memory_snapshot_digest": _digest(value.get("memory_snapshot_digest")),
        "recall_manifest_ref": _id(value.get("recall_manifest_ref")),
        "recall_manifest_digest": _digest(value.get("recall_manifest_digest")),
        "selection_mode": _id(value.get("selection_mode")),
        "recall_coverage": _id(value.get("recall_coverage")),
        "lessons": items,
        "advice_bytes_digest": _digest(value.get("advice_bytes_digest")),
        "advice_byte_count": value.get("advice_byte_count")
        if type(value.get("advice_byte_count")) is int else None,
        "content_status": "NOT_AUTHORIZED_FOR_GENERIC_HISTORY",
        "content_classes": ["PUBLIC_REVIEWED_SKILL_RESOURCE", "REVIEWED_LIMITED_LESSON"],
    }


def _project_request(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.learning-model-request-view.v1",
        "contract_version": "4",
        "request_ref": _id(value.get("request_id")),
        "logical_request_digest": _digest(value.get("digest")),
        "task_ref": _id(value.get("task_ref")),
        "run_id": _id(value.get("run_id")),
        "provider": _id(value.get("provider")),
        "model_id": _id(value.get("model_id")),
        "business_projection_digest": _digest(value.get("business_projection_digest")),
        "advice_digest": _digest(value.get("advice_digest")),
        "prompt_template_digest": _digest(value.get("prompt_template_digest")),
        "schema_digest": _digest(value.get("schema_digest")),
        "advice_context": _project_context(value.get("advice_context")),
        "content_status": "METADATA_ONLY_NO_HISTORICAL_CONTENT_GRANT",
        "original_object_readable_as_request": False,
    }


def _project_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.learning-model-receipt-view.v1",
        "contract_version": "4",
        "logical_receipt_digest": _digest(value.get("digest")),
        "request_digest": _digest(value.get("request_digest")),
        "provider_request_id": _id(value.get("provider_request_id")),
        "status": _code(value.get("status")) or "UNKNOWN",
        "dispatch_state": _code(value.get("dispatch_state")) or "UNKNOWN",
        "body_digest": _digest(value.get("body_digest")),
        "prompt_digest": _digest(value.get("prompt_digest")),
        "wire_schema_digest": _digest(value.get("wire_schema_digest")),
        "business_wire_digest": _digest(value.get("business_wire_digest")),
        "advice_wire_digest": _digest(value.get("advice_wire_digest")),
        "output_digest": _digest(value.get("output_digest")),
        "error_code": _code(value.get("error_code")),
        "content_status": "METADATA_ONLY_MODEL_OUTPUT_NOT_DISCLOSED",
        "original_object_readable_as_receipt": False,
    }


def _project_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    resources = value.get("resources")
    items = []
    if isinstance(resources, list):
        for item in resources[:16]:
            if isinstance(item, Mapping):
                items.append({"path": _id(item.get("path")),
                              "content_digest": _digest(item.get("sha256")),
                              "size_bytes": item.get("size_bytes")
                              if type(item.get("size_bytes")) is int else None})
    return {
        "schema_version": "orgrebase.learning-content-bundle-view.v1",
        "profile_id": _id(value.get("profile_id")),
        "bundle_digest": _digest(value.get("digest")),
        "consumer_id": _id(value.get("consumer_id")),
        "resources": items,
        "content_status": "PUBLICATION_SCOPE_NOT_VERIFIED_FOR_GENERIC_HISTORY",
    }


def _project_lesson_body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.learning-lesson-body-view.v1",
        "body_digest": _digest(value.get("digest")),
        "kind": _id(value.get("kind")),
        "problem_code": _id(value.get("problem_code")),
        "content_status": "REVIEWED_LIMITED_NO_GENERIC_HISTORY_GRANT",
    }


def safe_response_view_digest(value: Mapping[str, Any]) -> str:
    """Digest the final safe response without either recursive digest field.

    A top-level export ``digest`` covers the response including this marker's
    ``view_digest``. The marker digest covers the same response without those
    two fields, so both can be independently recomputed without a cycle.
    """
    marker = value.get("learning_content_view")
    if not isinstance(marker, Mapping) or marker.get("schema_version") != (
        "orgrebase.learning-content-safe-view.v1"
    ):
        raise ValueError("LEARNING_PROJECTION_MARKER_INVALID")
    body = {key: item for key, item in value.items() if key != "digest"}
    body["learning_content_view"] = {
        key: item for key, item in marker.items() if key != "view_digest"
    }
    return sha256_digest(body)


def project_learning_content(value: Any, *, surface: ProjectionSurface) -> LearningContentProjection:
    """Return a metadata-only copy of generic historical data.

    Callers must serialize ``as_response()`` or explicitly mark ``payload`` as
    a view: nested legacy logical digests remain identities of original bytes,
    not hashes of this redacted representation.
    """
    if surface not in {"state", "history", "export", "api", "cli", "other"}:
        raise ValueError("LEARNING_PROJECTION_SURFACE_INVALID")
    redacted: list[str] = []
    classes: set[str] = set()

    def mark(path: str, *content_classes: str) -> None:
        redacted.append(path)
        classes.update(content_classes)

    def walk(item: Any, path: str) -> Any:
        if isinstance(item, Mapping):
            schema = item.get("schema_version")
            version = item.get("contract_version")
            if isinstance(schema, str) and schema.startswith(_SAFE_VIEW_PREFIX) and schema.endswith(
                _SAFE_VIEW_SUFFIX
            ):
                if schema == "orgrebase.learning-advice-context-view.v1":
                    result = _project_context(item)
                    lessons = item.get("lessons")
                    if isinstance(lessons, list):
                        for index, lesson in enumerate(lessons[:3]):
                            if isinstance(lesson, Mapping):
                                for key in sorted(lesson.keys() - {"ref", "revision", "content_digest"}):
                                    mark(f"{path}/lessons/{index}/{key}", "UNKNOWN_LEARNING_TEXT")
                        if len(lessons) > 3:
                            mark(f"{path}/lessons/3", "UNKNOWN_LEARNING_TEXT")
                elif schema == "orgrebase.learning-model-request-view.v1":
                    result = _project_request({
                        **item,
                        "request_id": item.get("request_ref"),
                        "digest": item.get("logical_request_digest"),
                    })
                    context = item.get("advice_context")
                    if isinstance(context, Mapping) and context.get("schema_version") == (
                        "orgrebase.learning-advice-context-view.v1"
                    ):
                        result["advice_context"] = walk(context, f"{path}/advice_context")
                    else:
                        mark(f"{path}/advice_context", "UNKNOWN_LEARNING_TEXT")
                elif schema == "orgrebase.learning-model-receipt-view.v1":
                    result = _project_receipt({
                        **item,
                        "digest": item.get("logical_receipt_digest"),
                    })
                elif schema == "orgrebase.learning-model-advisory-view.v1":
                    request = item.get("request")
                    receipt = item.get("receipt")
                    if not isinstance(request, Mapping) or not isinstance(receipt, Mapping):
                        mark(path, "UNKNOWN_LEARNING_TEXT")
                        return {"schema_version": "orgrebase.learning-unknown-advisory-view.v1",
                                "content_status": "UNKNOWN_CLASS_FAIL_CLOSED"}
                    result = {
                        "schema_version": schema,
                        "request": walk(request, f"{path}/request"),
                        "receipt": walk(receipt, f"{path}/receipt"),
                        "content_status": "METADATA_ONLY_NO_HISTORICAL_CONTENT_GRANT",
                        "storage_status": "LEGACY_CANONICAL_INLINE_MAY_REMAIN",
                    }
                elif schema == "orgrebase.learning-content-bundle-view.v1":
                    resources = item.get("resources")
                    if isinstance(resources, list):
                        for index, resource in enumerate(resources[:16]):
                            if isinstance(resource, Mapping):
                                for key in sorted(resource.keys() - {
                                    "path", "content_digest", "size_bytes",
                                }):
                                    mark(f"{path}/resources/{index}/{key}", "UNKNOWN_LEARNING_TEXT")
                        if len(resources) > 16:
                            mark(f"{path}/resources/16", "UNKNOWN_LEARNING_TEXT")
                    result = _project_bundle({
                        **item,
                        "digest": item.get("bundle_digest"),
                        "resources": [
                            {**resource, "sha256": resource.get("content_digest")}
                            for resource in resources if isinstance(resource, Mapping)
                        ] if isinstance(resources, list) else [],
                    })
                elif schema == "orgrebase.learning-lesson-body-view.v1":
                    result = _project_lesson_body({**item, "digest": item.get("body_digest")})
                elif schema == "orgrebase.learning-wire-view.v1":
                    result = {"schema_version": schema,
                              "body_digest": _digest(item.get("body_digest")),
                              "content_status": "NOT_AUTHORIZED_FOR_GENERIC_HISTORY"}
                elif schema in {"orgrebase.learning-unknown-v4-view.v1",
                                "orgrebase.learning-unknown-advisory-view.v1"}:
                    result = {"schema_version": schema,
                              "content_status": "UNKNOWN_CLASS_FAIL_CLOSED"}
                else:
                    mark(path, "UNKNOWN_LEARNING_TEXT")
                    return {"schema_version": "orgrebase.learning-unknown-view.v1",
                            "content_status": "UNKNOWN_CLASS_FAIL_CLOSED"}
                for key in sorted(item.keys() - result.keys()):
                    child = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
                    mark(child, "UNKNOWN_LEARNING_TEXT")
                    result[key] = {
                        "content_status": "UNKNOWN_CLASS_FAIL_CLOSED",
                        "content_digest": sha256_digest(item[key]) if item[key] is not None else None,
                    }
                return result
            if version == "4" and "advice_context" in item:
                mark(path, "PUBLIC_REVIEWED_SKILL_RESOURCE", "REVIEWED_LIMITED_LESSON", "MODEL_WIRE_COPY")
                return _project_request(item)
            if version == "4" and "request_digest" in item:
                mark(path, "MODEL_GENERATED_OUTPUT", "MODEL_WIRE_COPY")
                return _project_receipt(item)
            if version in {"4", 4}:
                mark(path, "UNKNOWN_LEARNING_TEXT")
                return {"schema_version": "orgrebase.learning-unknown-v4-view.v1",
                        "content_status": "UNKNOWN_CLASS_FAIL_CLOSED"}
            if schema == "orgrebase.skill-content-bundle.v2":
                mark(path, "PUBLIC_REVIEWED_SKILL_RESOURCE", "UNREVIEWED_CANDIDATE_OR_TRACE")
                return _project_bundle(item)
            if schema == "orgrebase.lesson-body.v1":
                mark(path, "REVIEWED_LIMITED_LESSON")
                return _project_lesson_body(item)
            if "systemInstruction" in item or {"contents", "generationConfig"}.issubset(item):
                mark(path, "MODEL_WIRE_COPY")
                return {"schema_version": "orgrebase.learning-wire-view.v1",
                        "body_digest": sha256_digest(dict(item)),
                        "content_status": "NOT_AUTHORIZED_FOR_GENERIC_HISTORY"}
            result: dict[str, Any] = {}
            for key, nested in item.items():
                child = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
                if key == "model_advisory":
                    request = nested.get("request") if isinstance(nested, Mapping) else None
                    if (isinstance(nested, Mapping) and nested.get("schema_version") == (
                            "orgrebase.learning-model-advisory-view.v1")):
                        result[key] = walk(nested, child)
                    elif (isinstance(nested, Mapping)
                            and "request" not in nested and "receipt" not in nested
                            and "advice_context" not in nested):
                        # The older Golden reviewer field is a separate product
                        # contract, not a Finance learning V4 request.
                        result[key] = walk(nested, child)
                    elif isinstance(request, Mapping) and request.get("contract_version") in {"2", "3"}:
                        result[key] = walk(nested, child)
                    elif isinstance(request, Mapping) and request.get("contract_version") == "4":
                        mark(child, "PUBLIC_REVIEWED_SKILL_RESOURCE", "REVIEWED_LIMITED_LESSON",
                             "MODEL_GENERATED_OUTPUT", "MODEL_WIRE_COPY")
                        result[key] = {
                            "schema_version": "orgrebase.learning-model-advisory-view.v1",
                            "request": _project_request(request),
                            "receipt": _project_receipt(
                                nested["receipt"] if isinstance(nested.get("receipt"), Mapping) else {}
                            ),
                            "content_status": "METADATA_ONLY_NO_HISTORICAL_CONTENT_GRANT",
                            "storage_status": "LEGACY_CANONICAL_INLINE_MAY_REMAIN",
                        }
                    else:
                        mark(child, "UNKNOWN_LEARNING_TEXT")
                        result[key] = {"schema_version": "orgrebase.learning-unknown-advisory-view.v1",
                                       "content_status": "UNKNOWN_CLASS_FAIL_CLOSED"}
                elif key == "advice_context":
                    if isinstance(nested, Mapping) and nested.get("schema_version") == (
                        "orgrebase.learning-advice-context-view.v1"
                    ):
                        result[key] = walk(nested, child)
                    else:
                        mark(child, "REVIEWED_LIMITED_LESSON", "PUBLIC_REVIEWED_SKILL_RESOURCE")
                        result[key] = _project_context(nested)
                elif key in {"finance_wire_body", "raw_wire_body", "wire_body"}:
                    if isinstance(nested, Mapping) and nested.get("schema_version") == (
                        "orgrebase.learning-wire-view.v1"
                    ):
                        result[key] = walk(nested, child)
                    else:
                        mark(child, "MODEL_WIRE_COPY")
                        result[key] = {"schema_version": "orgrebase.learning-wire-view.v1",
                                       "body_digest": sha256_digest(nested) if nested is not None else None,
                                       "content_status": "NOT_AUTHORIZED_FOR_GENERIC_HISTORY"}
                elif key in _SENSITIVE_FIELDS:
                    mark(child, "UNKNOWN_LEARNING_TEXT")
                    result[key] = {"content_status": "UNKNOWN_CLASS_FAIL_CLOSED",
                                   "content_digest": sha256_digest(nested) if nested is not None else None}
                elif key in {"error", "exception", "failure_message"}:
                    mark(child, "UNKNOWN_LEARNING_TEXT")
                    result[key] = {"content_status": "UNKNOWN_CLASS_FAIL_CLOSED",
                                   "reason_code": _code(nested)}
                else:
                    result[key] = walk(nested, child)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(nested, f"{path}/{index}") for index, nested in enumerate(item)]
        return item

    projected = walk(value, "")
    original_digest = _digest(value.get("digest")) if isinstance(value, Mapping) else None
    paths = tuple(redacted)
    categories = tuple(sorted(classes))
    return LearningContentProjection(
        surface=surface, payload=projected,
        redacted_paths=paths, content_classes=categories,
        original_logical_digest=original_digest,
        view_digest=sha256_digest(_view_body(surface, projected, paths, categories, original_digest)),
    )


__all__ = (
    "CONTENT_CLASSES", "LearningContentProjection", "project_learning_content",
    "safe_response_view_digest",
)
