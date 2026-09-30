"""Exact-byte Skill packages and process-local qualification/release wiring.

This module deliberately does not mutate ``SkillCandidateArtifact``.  In
particular, the historical quote-compose candidate remains
``executable=false``; a separately digest-bound release artifact becomes
callable only through a current release-ledger head.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError

if TYPE_CHECKING:
    from orgrebase.store import StateStore

SKILL_REGISTRY_AUTHORITY = "authority:skill-registry"
SKILL_PACKAGE_SCHEMA = "orgrebase.skill-package-manifest.v2"
SKILL_INVOCATION_SCHEMA = "orgrebase.skill-invocation-receipt.v1"
SKILL_RELEASE_SCHEMA = "orgrebase.skill-release-receipt.v1"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

PARTITIONS = (
    "REPLAY",
    "HELD_OUT",
    "NEGATIVE_TRANSFER",
    "PERMISSION",
    "INJECTION",
    "MALFORMED",
    "RESOURCE_OR_DEADLINE",
    "CANARY",
)
SECURITY_PARTITIONS = frozenset(
    {"PERMISSION", "INJECTION", "MALFORMED", "RESOURCE_OR_DEADLINE"}
)
EXPECTED_ENTRY_POINTS = {
    "enterprise-quote-compose": "QUOTE_COMPOSE_V1",
    "structured-domain-handoff": "STRUCTURED_DOMAIN_HANDOFF_V1",
    "enterprise-launch-readiness": "LAUNCH_READINESS_V1",
}
CALLABLE_RELEASE_STATES = frozenset({"SHADOW", "CANARY", "ACTIVE"})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ZERO_DIGEST = "sha256:" + "0" * 64
_PROJECT_SKILL_LICENSE = "Apache-2.0"
# The reviewed project notice is a non-executable attachment. Pin its complete
# text so a self-resealed truncated notice cannot masquerade as that grant.
_PROJECT_SKILL_LICENSE_DIGEST = (
    "sha256:15d0f574f2d6cbd183f62e871eb02c606d01325e21f66dc03420295067b2469c"
)
_MAX_LICENSE_FILE_BYTES = 32_768
_LICENSE_MEDIA_TYPE = "text/plain; charset=utf-8"

SKILL_CONTENT_BUNDLE_SCHEMA = "orgrebase.skill-content-bundle.v1"
SKILL_CONTENT_CHECKLIST_SCHEMA = "orgrebase.skill-content-checklist.v1"
QUOTE_RECOVERY_PROFILE = "workspace-quote-evidence-recovery-v1"
QUOTE_DIAGNOSTIC_REASON_CATALOG = {
    "request": "QUOTE_RECOVERY_REQUEST_UNVERIFIED",
    "resume": "QUOTE_RECOVERY_RESUME_UNVERIFIED",
    "outcome": "QUOTE_RECOVERY_OUTCOME_PENDING",
}


def quote_diagnostic_policy_digest(package: LoadedSkillPackage) -> str:
    """Commit to the closed leaf and the protected interpreter resources."""

    return sha256_digest({
        "schema_version": "orgrebase.reviewed-quote-diagnostic-policy.v1",
        "target_skill": "structured-domain-handoff",
        "profile_id": QUOTE_RECOVERY_PROFILE,
        "allowed_leaf": "candidate_diagnostic_reason_map",
        "reason_catalog": QUOTE_DIAGNOSTIC_REASON_CATALOG,
        "contract_digest": sha256_digest(package.contract),
        "program_digest": package.manifest["program_content_digest"],
        "input_schema_digest": sha256_digest(package.input_schema),
        "output_schema_digest": sha256_digest(package.output_schema),
        "allowed_tools": package.manifest["permissions"]["allowed_tools"],
        "side_effects": package.manifest["permissions"]["side_effects"],
    })
QUOTE_RECOVERY_CONSUMER = "quote-evidence-recovery-checklist-v1"
_CONTENT_RESOURCE_ALLOWLIST = {
    "instructions/quote-evidence-recovery.md": ("instruction", "text/markdown"),
    "references/quote-evidence-recovery.md": ("reference", "text/markdown"),
    "checklists/quote-evidence-recovery.v1.json": ("checklist", "application/json"),
}
_MAX_CONTENT_RESOURCE_BYTES = 16_384
_MAX_CONTENT_BUNDLE_BYTES = 32_768

_QUOTE_DENY_FLAGS = frozenset(
    {"permission_expansion", "request_restricted_source", "target_write_requested"}
)
_QUOTE_ABSTAIN_FLAGS = frozenset(
    {
        "prompt_injection",
        "malformed_input",
        "deadline_expired",
        "resource_exhausted",
        "stale_input",
    }
)
_QUOTE_BASE_FIELDS = frozenset(
    {
        "skill_partition",
        "candidate_program_digest_required",
        "dependency_tool_receipt_digest",
        "dependency_result_digest",
        "coalition_result_binding_digest",
        "domain_result_digests",
    }
)
_HANDOFF_BASE_FIELDS = frozenset(
    {
        "run_id",
        "task_id",
        "delegation_id",
        "delegation_task_digest",
        "context_projection_digest",
        "candidate_bundle",
    }
)
_LAUNCH_BASE_FIELDS = frozenset(
    {
        "classification",
        "object_id",
        "reason_code",
        "preview_receipt_digest",
        "approval_receipt_digest",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _raw_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _validated_license_files(manifest: Mapping[str, Any]) -> dict[str, bytes]:
    """Decode only the reviewed, non-executable LICENSE attachment.

    Legacy manifests have no attachment and keep their exact original meaning.
    This validates distributed notice bytes; it grants no business permission,
    release authority, or authority to execute the associated Skill.
    """

    if "license_files" not in manifest:
        if manifest.get("license") == _PROJECT_SKILL_LICENSE:
            raise IntegrityError("SKILL_LICENSE_FILE_INVALID")
        return {}
    files = manifest["license_files"]
    if (
        manifest.get("license") != _PROJECT_SKILL_LICENSE
        or not isinstance(files, dict)
        or set(files) != {"LICENSE"}
    ):
        raise IntegrityError("SKILL_LICENSE_FILE_INVALID")
    declaration = files["LICENSE"]
    if (
        not isinstance(declaration, dict)
        or set(declaration) != {
            "encoding", "media_type", "content_base64", "size_bytes", "sha256",
        }
        or declaration.get("encoding") != "base64"
        or declaration.get("media_type") != _LICENSE_MEDIA_TYPE
        or not isinstance(declaration.get("size_bytes"), int)
        or isinstance(declaration.get("size_bytes"), bool)
        or not 0 < declaration["size_bytes"] <= _MAX_LICENSE_FILE_BYTES
        or not isinstance(declaration.get("content_base64"), str)
        or len(declaration["content_base64"]) > 45_000
    ):
        raise IntegrityError("SKILL_LICENSE_FILE_INVALID")
    try:
        raw = base64.b64decode(declaration["content_base64"], validate=True)
        raw.decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise IntegrityError("SKILL_LICENSE_FILE_INVALID") from exc
    if (
        len(raw) != declaration["size_bytes"]
        or _raw_digest(raw) != declaration.get("sha256")
        or declaration["sha256"] != _PROJECT_SKILL_LICENSE_DIGEST
    ):
        raise IntegrityError("SKILL_LICENSE_FILE_INVALID")
    return {"LICENSE": raw}


def _record(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(payload))
    result["digest"] = sha256_digest(payload)
    return result


def _verify_record(payload: Mapping[str, Any], *, error: str) -> None:
    declared = payload.get("digest")
    body = {key: value for key, value in payload.items() if key != "digest"}
    if not isinstance(declared, str) or declared != sha256_digest(body):
        raise IntegrityError(error)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError("SKILL_CONTENT_CHECKLIST_DUPLICATE_KEY")
        result[key] = value
    return result


def _safe_resource_name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        raise IntegrityError("SKILL_PACKAGE_RESOURCE_PATH_INVALID")
    return path.name


def _safe_resource_path(value: str) -> tuple[str, ...]:
    """Return a traversal-safe package-relative resource path.

    Skill packages may carry supporting references below ``references/`` while
    the canonical entry point remains the root ``SKILL.md``.  Callers never
    supply arbitrary paths: the loader still compares every declaration to the
    exact reviewed resource map below.
    """

    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise IntegrityError("SKILL_PACKAGE_RESOURCE_PATH_INVALID")
    return tuple(path.parts)


def _skill_frontmatter(skill_bytes: bytes, *, name: str, version: str) -> dict[str, str]:
    try:
        text = skill_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityError("SKILL_ENTRYPOINT_UTF8_INVALID") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise IntegrityError("SKILL_ENTRYPOINT_FRONTMATTER_INVALID")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise IntegrityError("SKILL_ENTRYPOINT_FRONTMATTER_INVALID") from exc
    frontmatter: dict[str, str] = {}
    package_metadata: dict[str, str] = {}
    section: str | None = None
    for line in lines[1:closing]:
        if line.startswith("  "):
            if section != "metadata":
                raise IntegrityError("SKILL_ENTRYPOINT_FRONTMATTER_INVALID")
            key, separator, value = line.strip().partition(":")
            if not separator or not key or key in package_metadata or not value.strip():
                raise IntegrityError("SKILL_ENTRYPOINT_FRONTMATTER_INVALID")
            package_metadata[key] = value.strip()
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or key.strip() in frontmatter:
            raise IntegrityError("SKILL_ENTRYPOINT_FRONTMATTER_INVALID")
        key = key.strip()
        value = value.strip()
        if key == "metadata":
            if value:
                raise IntegrityError("SKILL_ENTRYPOINT_FRONTMATTER_INVALID")
            section = "metadata"
            continue
        section = None
        frontmatter[key] = value
    description = frontmatter.get("description", "")
    if (
        frontmatter.get("name") != name
        or package_metadata.get("version") != version
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
        or not description
        or re.search(r"[A-Za-z]", description) is None
        or re.search(r"[\u3400-\u9fff]", description) is None
    ):
        raise IntegrityError("SKILL_ENTRYPOINT_DISCOVERY_METADATA_INVALID")
    return frontmatter


def _valid_nonzero_digest(value: Any) -> bool:
    return isinstance(value, str) and value != _ZERO_DIGEST and _DIGEST.fullmatch(value) is not None


def _contains_sensitive_marker(value: Any) -> bool:
    """Conservative local privacy gate for evaluator fixtures and transport candidates."""

    if isinstance(value, Mapping):
        return any(
            _contains_sensitive_marker(str(key)) or _contains_sensitive_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return "secret" in lowered or "canary" in lowered
    return False


_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
    }
)
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def _validate_schema_document(schema: Mapping[str, Any], *, expected_id: str) -> None:
    """Validate the deliberately small, local-only 2020-12 vocabulary we execute.

    Rejecting unsupported keywords is important: silently ignoring a keyword
    would make the package appear stricter than the runtime actually is.  All
    refs are local ``#/$defs`` refs, so invocation never performs network Schema
    resolution.
    """

    if schema.get("$schema") != JSON_SCHEMA_DIALECT or schema.get("$id") != expected_id:
        raise IntegrityError("SKILL_SCHEMA_ID_OR_DIALECT_MISMATCH")
    root = dict(schema)

    def visit(node: Any) -> None:
        if not isinstance(node, Mapping):
            raise IntegrityError("SKILL_SCHEMA_DOCUMENT_INVALID")
        unknown = set(node) - _SUPPORTED_SCHEMA_KEYWORDS
        if unknown:
            raise IntegrityError("SKILL_SCHEMA_UNSUPPORTED_KEYWORD")
        declared_type = node.get("type")
        declared_types = (
            [declared_type]
            if isinstance(declared_type, str)
            else declared_type
            if isinstance(declared_type, list)
            else []
        )
        if declared_type is not None and (
            not declared_types
            or any(not isinstance(item, str) or item not in _SCHEMA_TYPES for item in declared_types)
        ):
            raise IntegrityError("SKILL_SCHEMA_TYPE_INVALID")
        ref = node.get("$ref")
        if ref is not None:
            if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                raise IntegrityError("SKILL_SCHEMA_EXTERNAL_REF_FORBIDDEN")
            name = ref.removeprefix("#/$defs/")
            definitions = root.get("$defs")
            if (
                not name
                or "/" in name
                or not isinstance(definitions, Mapping)
                or name not in definitions
            ):
                raise IntegrityError("SKILL_SCHEMA_LOCAL_REF_UNRESOLVED")
        definitions = node.get("$defs", {})
        if not isinstance(definitions, Mapping):
            raise IntegrityError("SKILL_SCHEMA_DEFINITIONS_INVALID")
        for definition in definitions.values():
            visit(definition)
        properties = node.get("properties", {})
        if not isinstance(properties, Mapping):
            raise IntegrityError("SKILL_SCHEMA_PROPERTIES_INVALID")
        for child in properties.values():
            visit(child)
        additional = node.get("additionalProperties", True)
        if not isinstance(additional, bool):
            visit(additional)
        if "items" in node:
            visit(node["items"])
        pattern = node.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise IntegrityError("SKILL_SCHEMA_PATTERN_INVALID")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise IntegrityError("SKILL_SCHEMA_PATTERN_INVALID") from exc

    visit(root)


def _schema_type_matches(value: Any, declared: str) -> bool:
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return value is None


def _json_schema_valid(value: Any, schema: Mapping[str, Any]) -> bool:
    root = schema

    def validate(instance: Any, node: Mapping[str, Any]) -> bool:
        ref = node.get("$ref")
        if isinstance(ref, str):
            definition = root["$defs"][ref.removeprefix("#/$defs/")]
            return validate(instance, definition)
        declared_type = node.get("type")
        if isinstance(declared_type, str):
            if not _schema_type_matches(instance, declared_type):
                return False
        elif isinstance(declared_type, list) and not any(
            _schema_type_matches(instance, item) for item in declared_type
        ):
            return False
        if "const" in node and instance != node["const"]:
            return False
        if "enum" in node and instance not in node["enum"]:
            return False
        if isinstance(instance, dict):
            required = node.get("required", [])
            if not isinstance(required, list) or any(
                not isinstance(item, str) or item not in instance for item in required
            ):
                return False
            properties = node.get("properties", {})
            if not isinstance(properties, Mapping):
                return False
            for key, item in instance.items():
                child = properties.get(key)
                if child is not None:
                    if not validate(item, child):
                        return False
                    continue
                additional = node.get("additionalProperties", True)
                if additional is False:
                    return False
                if isinstance(additional, Mapping) and not validate(item, additional):
                    return False
            minimum_properties = node.get("minProperties")
            if isinstance(minimum_properties, int) and len(instance) < minimum_properties:
                return False
        if isinstance(instance, list):
            if isinstance(node.get("minItems"), int) and len(instance) < node["minItems"]:
                return False
            if isinstance(node.get("maxItems"), int) and len(instance) > node["maxItems"]:
                return False
            if node.get("uniqueItems") is True and len({sha256_digest(item) for item in instance}) != len(
                instance
            ):
                return False
            items = node.get("items")
            if isinstance(items, Mapping) and any(not validate(item, items) for item in instance):
                return False
        if isinstance(instance, str):
            if isinstance(node.get("minLength"), int) and len(instance) < node["minLength"]:
                return False
            if isinstance(node.get("maxLength"), int) and len(instance) > node["maxLength"]:
                return False
            pattern = node.get("pattern")
            if isinstance(pattern, str) and re.search(pattern, instance) is None:
                return False
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if isinstance(node.get("minimum"), (int, float)) and instance < node["minimum"]:
                return False
            if isinstance(node.get("maximum"), (int, float)) and instance > node["maximum"]:
                return False
        return True

    return validate(value, root)


@dataclass(frozen=True)
class InvocationContext:
    run_id: str
    task_id: str
    delegation_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if not all((self.run_id, self.task_id, self.delegation_id, self.actor_id)):
            raise ValueError("Skill invocation context fields must be non-empty")


@dataclass(frozen=True)
class LoadedSkillPackage:
    name: str
    version: str
    package_digest: str
    manifest: dict[str, Any]
    contract: dict[str, Any]
    program: dict[str, Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    description: str
    skill_bytes: bytes
    reference_bytes: dict[str, bytes]
    resource_digests: dict[str, str]
    raw_resource_bytes: dict[str, bytes]


def skill_license_files(package: LoadedSkillPackage) -> dict[str, bytes]:
    """Read exact UTF-8 LICENSE bytes from a package or restored snapshot.

    Only the fixed name ``LICENSE`` is returned. These notices are separate
    from the seven executable/interpreter resources and are never consumed as
    model instructions. Legacy packages without an attachment return an empty
    mapping; their inherited metadata and repository license remain unchanged.
    """

    return _validated_license_files(package.manifest)


def skill_package_snapshot(package: LoadedSkillPackage) -> dict[str, Any]:
    """Retain the complete effective package, including original raw resources."""

    skill_license_files(package)
    body = {
        "schema_version": "orgrebase.skill-package-snapshot.v1",
        "name": package.name,
        "version": package.version,
        "package_digest": package.package_digest,
        "manifest": deepcopy(package.manifest),
        "contract": deepcopy(package.contract),
        "program": deepcopy(package.program),
        "input_schema": deepcopy(package.input_schema),
        "output_schema": deepcopy(package.output_schema),
        "description": package.description,
        "skill_bytes_base64": base64.b64encode(package.skill_bytes).decode("ascii"),
        "reference_bytes_base64": {
            key: base64.b64encode(value).decode("ascii")
            for key, value in sorted(package.reference_bytes.items())
        },
        "resource_digests": deepcopy(package.resource_digests),
        "raw_resource_bytes_base64": {
            key: base64.b64encode(value).decode("ascii")
            for key, value in sorted(package.raw_resource_bytes.items())
        },
    }
    return {**body, "digest": sha256_digest(body)}


def load_skill_package_snapshot(payload: Mapping[str, Any]) -> LoadedSkillPackage:
    """Decode a stored snapshot only after checking content and protected bytes."""

    value = deepcopy(dict(payload))
    body = {key: item for key, item in value.items() if key != "digest"}
    if set(value) != {
        "schema_version", "name", "version", "package_digest", "manifest", "contract",
        "program", "input_schema", "output_schema", "description", "skill_bytes_base64",
        "reference_bytes_base64", "resource_digests", "raw_resource_bytes_base64", "digest",
    }:
        raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_INVALID")
    if (
        value.get("schema_version") != "orgrebase.skill-package-snapshot.v1"
        or value.get("digest") != sha256_digest(body)
        or not isinstance(value.get("manifest"), dict)
        or not isinstance(value.get("package_digest"), str)
        or value["manifest"].get("manifest_digest") != value["package_digest"]
        or value["package_digest"] != sha256_digest(
            {key: item for key, item in value["manifest"].items() if key != "manifest_digest"}
        )
        or value["manifest"].get("name") != value.get("name")
        or value["manifest"].get("version") != value.get("version")
    ):
        raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_INVALID")
    _validated_license_files(value["manifest"])
    try:
        skill_bytes = base64.b64decode(value["skill_bytes_base64"], validate=True)
        references = {
            key: base64.b64decode(encoded, validate=True)
            for key, encoded in value["reference_bytes_base64"].items()
        }
        raw_resources = {
            key: base64.b64decode(encoded, validate=True)
            for key, encoded in value["raw_resource_bytes_base64"].items()
        }
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_BYTES_INVALID") from exc
    declared = value.get("resource_digests")
    if (
        not isinstance(declared, dict)
        or declared.get("skill") != _raw_digest(skill_bytes)
        or set(raw_resources) != {
            "skill", "reference_zh_cn", "reference_en", "contract", "program",
            "input_schema", "output_schema",
        }
        or any(declared.get(key) != _raw_digest(raw) for key, raw in raw_resources.items())
        or skill_bytes != raw_resources["skill"]
        or references != {
            "zh-CN": raw_resources["reference_zh_cn"],
            "en": raw_resources["reference_en"],
        }
    ):
        raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_RESOURCE_MISMATCH")
    for key in ("contract", "program", "input_schema", "output_schema"):
        if not isinstance(value.get(key), dict):
            raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_RESOURCE_MISMATCH")
    try:
        parsed_resources = {
            key: json.loads(raw_resources[key].decode("utf-8"))
            for key in ("contract", "program", "input_schema", "output_schema")
        }
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_RESOURCE_MISMATCH") from exc
    if (
        not isinstance(value["manifest"].get("resources"), dict)
        or value["manifest"].get("program_content_digest") != sha256_digest(
            {key: item for key, item in value["program"].items() if key != "digest"}
        )
        or value["manifest"].get("resources", {}).get("skill", {}).get("sha256")
        != declared["skill"]
        or any(
            value["manifest"].get("resources", {}).get(key, {}).get("sha256") != digest
            for key, digest in declared.items() if not key.startswith("candidate:")
        )
        or any(parsed_resources[key] != value[key] for key in parsed_resources)
    ):
        raise IntegrityError("SKILL_PACKAGE_SNAPSHOT_PROTECTED_MISMATCH")
    return LoadedSkillPackage(
        name=value["name"],
        version=value["version"],
        package_digest=value["package_digest"],
        manifest=value["manifest"],
        contract=value["contract"],
        program=value["program"],
        input_schema=value["input_schema"],
        output_schema=value["output_schema"],
        description=value["description"],
        skill_bytes=skill_bytes,
        reference_bytes=references,
        resource_digests=declared,
        raw_resource_bytes=raw_resources,
    )


@dataclass(frozen=True)
class SkillInvocation:
    result: dict[str, Any]
    receipt: dict[str, Any]


@dataclass(frozen=True)
class SkillEvaluationCase:
    case_id: str
    partition: str
    public_input: Mapping[str, Any]
    expected_action: str


@dataclass(frozen=True)
class SkillContentBundle:
    """Immutable, allowlisted content used by one reviewed deterministic consumer.

    Resources are embedded as exact bytes in the persisted candidate rather
    than loaded from an author-controlled path.  The bundle cannot add code,
    tools, permissions, schemas, or side effects.
    """

    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        target_skill: str,
        predecessor_package_digest: str,
        instruction_bytes: bytes,
        reference_bytes: bytes,
        checklist_bytes: bytes,
    ) -> SkillContentBundle:
        resources = []
        supplied = {
            "instructions/quote-evidence-recovery.md": instruction_bytes,
            "references/quote-evidence-recovery.md": reference_bytes,
            "checklists/quote-evidence-recovery.v1.json": checklist_bytes,
        }
        for path, value in supplied.items():
            kind, media_type = _CONTENT_RESOURCE_ALLOWLIST[path]
            if not isinstance(value, bytes):
                raise TypeError("SKILL_CONTENT_RESOURCE_BYTES_REQUIRED")
            resources.append(
                {
                    "path": path,
                    "kind": kind,
                    "media_type": media_type,
                    "encoding": "base64",
                    "content_base64": base64.b64encode(value).decode("ascii"),
                    "size_bytes": len(value),
                    "sha256": _raw_digest(value),
                }
            )
        body = {
            "schema_version": SKILL_CONTENT_BUNDLE_SCHEMA,
            "profile_id": QUOTE_RECOVERY_PROFILE,
            "consumer_id": QUOTE_RECOVERY_CONSUMER,
            "target_skill": target_skill,
            "predecessor_package_digest": predecessor_package_digest,
            "resources": resources,
            "permissions": {
                "allowed_tools": [],
                "side_effects": [],
                "target_writes_max": 0,
            },
        }
        return cls.from_payload({**body, "digest": sha256_digest(body)})

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SkillContentBundle:
        value = deepcopy(dict(payload))
        body = {key: item for key, item in value.items() if key != "digest"}
        if value.get("digest") != sha256_digest(body):
            raise IntegrityError("SKILL_CONTENT_BUNDLE_DIGEST_MISMATCH")
        if (
            value.get("schema_version") != SKILL_CONTENT_BUNDLE_SCHEMA
            or value.get("profile_id") != QUOTE_RECOVERY_PROFILE
            or value.get("consumer_id") != QUOTE_RECOVERY_CONSUMER
            or value.get("target_skill") != "structured-domain-handoff"
            or not _valid_nonzero_digest(value.get("predecessor_package_digest"))
            or value.get("permissions")
            != {"allowed_tools": [], "side_effects": [], "target_writes_max": 0}
        ):
            raise IntegrityError("SKILL_CONTENT_BUNDLE_CONTRACT_INVALID")
        resources = value.get("resources")
        if not isinstance(resources, list) or len(resources) != len(_CONTENT_RESOURCE_ALLOWLIST):
            raise IntegrityError("SKILL_CONTENT_RESOURCE_SET_INVALID")
        seen: set[str] = set()
        total = 0
        for resource in resources:
            if not isinstance(resource, dict) or set(resource) != {
                "path",
                "kind",
                "media_type",
                "encoding",
                "content_base64",
                "size_bytes",
                "sha256",
            }:
                raise IntegrityError("SKILL_CONTENT_RESOURCE_DECLARATION_INVALID")
            path = str(resource.get("path", ""))
            if path in seen or _CONTENT_RESOURCE_ALLOWLIST.get(path) != (
                resource.get("kind"),
                resource.get("media_type"),
            ):
                raise IntegrityError("SKILL_CONTENT_RESOURCE_PATH_NOT_ALLOWED")
            if resource.get("encoding") != "base64":
                raise IntegrityError("SKILL_CONTENT_RESOURCE_ENCODING_INVALID")
            encoded = resource.get("content_base64")
            if (
                not isinstance(encoded, str)
                or len(encoded) > ((_MAX_CONTENT_RESOURCE_BYTES + 2) // 3) * 4
            ):
                raise IntegrityError("SKILL_CONTENT_RESOURCE_ENCODING_INVALID")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise IntegrityError("SKILL_CONTENT_RESOURCE_ENCODING_INVALID") from exc
            if (
                not raw
                or len(raw) > _MAX_CONTENT_RESOURCE_BYTES
                or resource.get("size_bytes") != len(raw)
                or resource.get("sha256") != _raw_digest(raw)
            ):
                raise IntegrityError("SKILL_CONTENT_RESOURCE_BYTES_MISMATCH")
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IntegrityError("SKILL_CONTENT_RESOURCE_UTF8_INVALID") from exc
            total += len(raw)
            seen.add(path)
        if seen != set(_CONTENT_RESOURCE_ALLOWLIST) or total > _MAX_CONTENT_BUNDLE_BYTES:
            raise IntegrityError("SKILL_CONTENT_RESOURCE_SET_INVALID")
        bundle = cls(payload=value)
        bundle.quote_recovery_checklist()
        return bundle

    @property
    def digest(self) -> str:
        return str(self.payload["digest"])

    def resource_bytes(self, path: str) -> bytes:
        for resource in self.payload["resources"]:
            if resource["path"] == path:
                raw = base64.b64decode(resource["content_base64"], validate=True)
                if _raw_digest(raw) != resource["sha256"]:
                    raise IntegrityError("SKILL_CONTENT_RESOURCE_BYTES_MISMATCH")
                return raw
        raise IntegrityError("SKILL_CONTENT_RESOURCE_PATH_NOT_ALLOWED")

    def resource_digests(self) -> dict[str, str]:
        return {
            str(resource["path"]): str(resource["sha256"])
            for resource in self.payload["resources"]
        }

    def quote_recovery_checklist(self) -> dict[str, Any]:
        raw = self.resource_bytes("checklists/quote-evidence-recovery.v1.json")
        try:
            checklist = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique_json_object
            )
        except json.JSONDecodeError as exc:
            raise IntegrityError("SKILL_CONTENT_CHECKLIST_INVALID_JSON") from exc
        expected_paths = [
            "/candidate_bundle/evidence/outcome_artifact_digest",
            "/candidate_bundle/evidence/request_digest",
            "/candidate_bundle/evidence/resume_digest",
        ]
        if (
            not isinstance(checklist, dict)
            or set(checklist) != {
                "schema_version",
                "profile_id",
                "rule_id",
                "applies_when_path",
                "applies_when_equals",
                "required_digest_paths",
                "failure_action",
                "failure_reason",
                "allowed_tools",
                "target_writes_max",
            }
            or checklist.get("schema_version") != SKILL_CONTENT_CHECKLIST_SCHEMA
            or checklist.get("profile_id") != QUOTE_RECOVERY_PROFILE
            or checklist.get("rule_id") != "require-verified-recovery-receipts"
            or checklist.get("applies_when_path") != "/candidate_bundle/profile_id"
            or checklist.get("applies_when_equals") != QUOTE_RECOVERY_PROFILE
            or checklist.get("required_digest_paths") != expected_paths
            or checklist.get("failure_action") != "ABSTAIN"
            or checklist.get("failure_reason") != "QUOTE_RECOVERY_EVIDENCE_INCOMPLETE"
            or checklist.get("allowed_tools") != []
            or checklist.get("target_writes_max") != 0
        ):
            raise IntegrityError("SKILL_CONTENT_CHECKLIST_CONTRACT_INVALID")
        return checklist


class SkillPackageRegistry:
    """Discover and exact-load only the three reviewed package entry points."""

    def __init__(self, root: str | Path | None = None) -> None:
        checkout = Path(root) if root is not None else Path(__file__).resolve().parents[3]
        checkout_registry = checkout / "configs" / "workspace" / "skill-registry.json"
        if checkout_registry.is_file():
            self._registry_resource: Any = checkout_registry
            self._package_root: Any = checkout / "skills"
            self._license_resource: Any = checkout / "LICENSE"
            self.resource_mode = "SOURCE_CHECKOUT"
        else:
            package_root = resources.files("orgrebase")
            self._registry_resource = package_root.joinpath(
                "_assets/configs/workspace/skill-registry.json"
            )
            self._package_root = package_root.joinpath("skill_packages")
            self._license_resource = package_root.joinpath("_assets/licenses/Apache-2.0.txt")
            self.resource_mode = "INSTALLED_WHEEL"
        self._registry = self._load_registry()

    @staticmethod
    def _read_bytes(resource: Any) -> bytes:
        try:
            return resource.read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise IntegrityError("SKILL_PACKAGE_RESOURCE_MISSING") from exc

    @classmethod
    def _read_json(cls, resource: Any, *, error: str) -> dict[str, Any]:
        try:
            value = json.loads(cls._read_bytes(resource).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError(error) from exc
        if not isinstance(value, dict):
            raise IntegrityError(error)
        return value

    def _load_registry(self) -> dict[str, Any]:
        registry = self._read_json(
            self._registry_resource, error="SKILL_REGISTRY_INVALID_JSON"
        )
        if (
            registry.get("schema_version") != "orgrebase.skill-registry.v1"
            or registry.get("authority") != SKILL_REGISTRY_AUTHORITY
        ):
            raise IntegrityError("SKILL_REGISTRY_CONTRACT_INVALID")
        entries = registry.get("packages")
        if not isinstance(entries, list):
            raise IntegrityError("SKILL_REGISTRY_PACKAGES_INVALID")
        names = [entry.get("name") for entry in entries if isinstance(entry, dict)]
        if len(entries) != len(EXPECTED_ENTRY_POINTS) or set(names) != set(
            EXPECTED_ENTRY_POINTS
        ):
            raise IntegrityError("SKILL_REGISTRY_EXPECTED_PACKAGE_SET_MISMATCH")
        if len(names) != len(set(names)):
            raise IntegrityError("SKILL_REGISTRY_DUPLICATE_PACKAGE")
        return registry

    def _entry(self, name: str) -> dict[str, Any]:
        for entry in self._registry["packages"]:
            if entry.get("name") == name:
                return dict(entry)
        raise KeyError(name)

    def _project_license_files(self) -> dict[str, Any]:
        raw = self._read_bytes(self._license_resource)
        files = {"LICENSE": {
            "encoding": "base64",
            "media_type": _LICENSE_MEDIA_TYPE,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
            "sha256": _raw_digest(raw),
        }}
        _validated_license_files({"license": _PROJECT_SKILL_LICENSE, "license_files": files})
        return files

    def _package_resource(self, entry: Mapping[str, Any], name: str) -> Any:
        directory = _safe_resource_name(str(entry.get("path", "")))
        return self._package_root.joinpath(directory, *_safe_resource_path(name))

    def discover(self) -> tuple[dict[str, Any], ...]:
        packages = [self.load(name) for name in sorted(EXPECTED_ENTRY_POINTS)]
        return tuple(
            {
                "name": package.name,
                "version": package.version,
                "package_id": str(package.manifest["package_id"]),
                "package_digest": package.package_digest,
                "entry_point": str(package.manifest["entry_point"]),
                "description": package.description,
                "resource_mode": self.resource_mode,
                "input_schema_digest": package.resource_digests["input_schema"],
                "output_schema_digest": package.resource_digests["output_schema"],
                "language_reference_digests": {
                    "zh-CN": package.resource_digests["reference_zh_cn"],
                    "en": package.resource_digests["reference_en"],
                },
            }
            for package in packages
        )

    def load(
        self, name: str, *, expected_package_digest: str | None = None
    ) -> LoadedSkillPackage:
        entry = self._entry(name)
        manifest = self._read_json(
            self._package_resource(entry, "package.json"),
            error="SKILL_PACKAGE_MANIFEST_INVALID_JSON",
        )
        declared_digest = manifest.get("manifest_digest")
        manifest_body = {
            key: value for key, value in manifest.items() if key != "manifest_digest"
        }
        actual_digest = sha256_digest(manifest_body)
        if (
            manifest.get("schema_version") != SKILL_PACKAGE_SCHEMA
            or declared_digest != actual_digest
            or entry.get("manifest_digest") != actual_digest
            or (expected_package_digest is not None and expected_package_digest != actual_digest)
        ):
            raise IntegrityError("SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH")
        _validated_license_files(manifest)
        if (
            manifest.get("name") != name
            or manifest.get("version") != entry.get("version")
            or manifest.get("entry_point") != EXPECTED_ENTRY_POINTS[name]
        ):
            raise IntegrityError("SKILL_PACKAGE_IDENTITY_MISMATCH")
        resources_block = manifest.get("resources")
        expected_paths = {
            "skill",
            "reference_zh_cn",
            "reference_en",
            "contract",
            "program",
            "input_schema",
            "output_schema",
        }
        if not isinstance(resources_block, dict) or set(resources_block) != expected_paths:
            raise IntegrityError("SKILL_PACKAGE_RESOURCE_SET_INVALID")
        expected_paths = {
            "skill": "SKILL.md",
            "reference_zh_cn": "references/zh-CN.md",
            "reference_en": "references/en.md",
            "contract": "contract.json",
            "program": "program.json",
            "input_schema": "input.schema.json",
            "output_schema": "output.schema.json",
        }
        if self._package_resource(entry, "SKILL.zh.md").is_file():
            raise IntegrityError("SKILL_PACKAGE_SECOND_CANONICAL_ENTRYPOINT_FORBIDDEN")
        raw: dict[str, bytes] = {}
        resource_digests: dict[str, str] = {}
        for resource_name, expected_path in expected_paths.items():
            declaration = resources_block.get(resource_name)
            if not isinstance(declaration, dict) or declaration.get("path") != expected_path:
                raise IntegrityError("SKILL_PACKAGE_RESOURCE_DECLARATION_INVALID")
            value = self._read_bytes(self._package_resource(entry, expected_path))
            digest = _raw_digest(value)
            if digest != declaration.get("sha256"):
                raise IntegrityError(f"SKILL_RESOURCE_DIGEST_MISMATCH:{resource_name}")
            raw[resource_name] = value
            resource_digests[resource_name] = digest
        try:
            contract = json.loads(raw["contract"].decode("utf-8"))
            program = json.loads(raw["program"].decode("utf-8"))
            input_schema = json.loads(raw["input_schema"].decode("utf-8"))
            output_schema = json.loads(raw["output_schema"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("SKILL_PACKAGE_JSON_RESOURCE_INVALID") from exc
        if not all(
            isinstance(value, dict)
            for value in (contract, program, input_schema, output_schema)
        ):
            raise IntegrityError("SKILL_PACKAGE_JSON_RESOURCE_INVALID")
        metadata = _skill_frontmatter(
            raw["skill"], name=name, version=str(manifest["version"])
        )
        skill_text = raw["skill"].decode("utf-8")
        if not all(
            path in skill_text
            for path in ("references/zh-CN.md", "references/en.md")
        ):
            raise IntegrityError("SKILL_ENTRYPOINT_LANGUAGE_REFERENCE_ROUTE_MISSING")
        try:
            reference_bytes = {
                "zh-CN": bytes(raw["reference_zh_cn"]),
                "en": bytes(raw["reference_en"]),
            }
            for value in reference_bytes.values():
                value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntegrityError("SKILL_LANGUAGE_REFERENCE_UTF8_INVALID") from exc
        _validate_schema_document(
            input_schema, expected_id=str(manifest.get("input_schema_ref", ""))
        )
        _validate_schema_document(
            output_schema, expected_id=str(manifest.get("output_schema_ref", ""))
        )
        contract_body = {
            key: value for key, value in contract.items() if key != "content_digest"
        }
        if contract.get("content_digest") != sha256_digest(contract_body):
            raise IntegrityError("SKILL_CONTRACT_CONTENT_DIGEST_MISMATCH")
        contract_version = str(contract.get("version", ""))
        if contract.get("name") != name or not (
            manifest["version"] == contract_version
            or str(manifest["version"]).startswith(f"{contract_version}.")
        ):
            raise IntegrityError("SKILL_CONTRACT_IDENTITY_MISMATCH")
        program_body = {key: value for key, value in program.items() if key != "digest"}
        program_digest = sha256_digest(program_body)
        if (
            program.get("digest") != program_digest
            or manifest.get("program_content_digest") != program_digest
        ):
            raise IntegrityError("SKILL_PROGRAM_CONTENT_DIGEST_MISMATCH")
        permissions = manifest.get("permissions")
        if not isinstance(permissions, dict) or permissions != {
            "allowed_tools": [],
            "side_effects": [],
            "effect_ceiling": "CANDIDATE_ONLY",
        }:
            raise IntegrityError("SKILL_PACKAGE_EFFECT_BOUNDARY_WIDENED")
        if program.get("allowed_tool_ids") != [] or program.get("side_effects") != []:
            raise IntegrityError("SKILL_PROGRAM_EFFECT_BOUNDARY_WIDENED")
        evaluation = manifest.get("evaluation")
        if (
            not isinstance(evaluation, dict)
            or tuple(evaluation.get("partitions", ())) != PARTITIONS
            or evaluation.get("security_min_pass_rate") != 1.0
            or evaluation.get("target_writes_max") != 0
        ):
            raise IntegrityError("SKILL_PACKAGE_EVALUATION_GATE_INVALID")
        release = manifest.get("release_artifact")
        if not isinstance(release, dict) or release.get("source_candidate_executable") is not False:
            raise IntegrityError("SKILL_RELEASE_MUTATED_CANDIDATE_EXECUTABILITY")
        dependencies = manifest.get("dependencies")
        if not isinstance(dependencies, dict) or not dependencies or not all(
            isinstance(ref, str)
            and isinstance(digest, str)
            and _DIGEST.fullmatch(digest)
            for ref, digest in dependencies.items()
        ):
            raise IntegrityError("SKILL_PACKAGE_DEPENDENCY_LOCK_INVALID")
        return LoadedSkillPackage(
            name=name,
            version=str(manifest["version"]),
            package_digest=actual_digest,
            manifest=deepcopy(manifest),
            contract=deepcopy(contract),
            program=deepcopy(program),
            input_schema=deepcopy(input_schema),
            output_schema=deepcopy(output_schema),
            description=metadata["description"],
            skill_bytes=bytes(raw["skill"]),
            reference_bytes=reference_bytes,
            resource_digests=resource_digests,
            raw_resource_bytes=raw,
        )

    @staticmethod
    def _check_context_bindings(
        public_input: Mapping[str, Any], context: InvocationContext
    ) -> None:
        bindings = {
            "run_id": context.run_id,
            "task_id": context.task_id,
            "delegation_id": context.delegation_id,
        }
        for key, expected in bindings.items():
            if key in public_input and public_input[key] != expected:
                raise IntegrityError(f"SKILL_INVOCATION_CONTEXT_MISMATCH:{key}")

    @staticmethod
    def _flagged(program: Mapping[str, Any], public_input: Mapping[str, Any], key: str) -> bool:
        flags = program.get(key, ())
        return isinstance(flags, list) and any(public_input.get(str(flag)) is True for flag in flags)

    @staticmethod
    def _candidate_result(package: LoadedSkillPackage, action: str, **values: Any) -> dict[str, Any]:
        return {
            "schema_version": "orgrebase.skill-result-candidate.v1",
            "action": action,
            **values,
            "package_digest": package.package_digest,
            "candidate_only": True,
            "target_writes": 0,
        }

    def _execute_quote(
        self, package: LoadedSkillPackage, public_input: Mapping[str, Any]
    ) -> dict[str, Any]:
        required = (
            "skill_partition",
            "candidate_program_digest_required",
            "dependency_tool_receipt_digest",
            "dependency_result_digest",
            "coalition_result_binding_digest",
            "domain_result_digests",
        )
        if not all(field in public_input for field in required):
            return self._candidate_result(package, "ABSTAIN", reason="MALFORMED_INPUT")
        dependency_bindings = {
            "dependency_tool_receipt_digest": public_input["dependency_tool_receipt_digest"],
            "dependency_result_digest": public_input["dependency_result_digest"],
            "coalition_result_binding_digest": public_input[
                "coalition_result_binding_digest"
            ],
            "domain_result_digests": dict(public_input["domain_result_digests"]),
        }
        domain_results = dependency_bindings["domain_result_digests"]
        scalar_digests = {
            key: value
            for key, value in dependency_bindings.items()
            if key != "domain_result_digests"
        }
        if not all(_valid_nonzero_digest(value) for value in scalar_digests.values()):
            return self._candidate_result(
                package, "ABSTAIN", reason="INVALID_DEPENDENCY_RECEIPT_DIGEST"
            )
        if (
            not isinstance(domain_results, dict)
            or set(domain_results) != {"product", "legal", "finance", "gtm"}
            or not all(_valid_nonzero_digest(value) for value in domain_results.values())
            or len(set(domain_results.values())) != 4
        ):
            return self._candidate_result(
                package, "ABSTAIN", reason="INVALID_FOUR_DOMAIN_COALITION_ROOTS"
            )
        if set(public_input) - _QUOTE_BASE_FIELDS - _QUOTE_DENY_FLAGS - _QUOTE_ABSTAIN_FLAGS:
            return self._candidate_result(package, "ABSTAIN", reason="UNKNOWN_INPUT_FIELD")
        deny_flags = package.program.get("deny_flags")
        abstain_flags = package.program.get("abstain_flags")
        if set(deny_flags or ()) != _QUOTE_DENY_FLAGS or set(
            abstain_flags or ()
        ) != _QUOTE_ABSTAIN_FLAGS:
            raise IntegrityError("SKILL_QUOTE_SECURITY_POLICY_INVALID")
        if self._flagged(package.program, public_input, "deny_flags"):
            return self._candidate_result(
                package,
                "DENY",
                reason="AUTHORITY_OR_DISCLOSURE_VETO",
                **dependency_bindings,
            )
        if self._flagged(package.program, public_input, "abstain_flags"):
            return self._candidate_result(
                package,
                "ABSTAIN",
                reason="UNTRUSTED_OR_UNAVAILABLE_INPUT",
                **dependency_bindings,
            )
        if public_input["candidate_program_digest_required"] != package.manifest[
            "program_content_digest"
        ]:
            raise IntegrityError("SKILL_REQUESTED_PROGRAM_DIGEST_MISMATCH")
        operations = package.program.get("operations")
        if not isinstance(operations, list) or [item.get("operation") for item in operations] != [
            "REQUIRE_FIELDS",
            "MAP_VALUE",
            "RETURN_FIELD",
        ]:
            raise IntegrityError("SKILL_QUOTE_PROGRAM_NOT_RESTRICTED")
        mapping = operations[1].get("parameters", {}).get("mapping")
        if not isinstance(mapping, dict):
            raise IntegrityError("SKILL_QUOTE_MAPPING_INVALID")
        action = str(mapping.get(str(public_input["skill_partition"]), "ABSTAIN"))
        return self._candidate_result(
            package,
            action,
            reason="EXACT_DECLARATIVE_PROGRAM",
            **dependency_bindings,
        )

    def _execute_handoff(
        self, package: LoadedSkillPackage, public_input: Mapping[str, Any]
    ) -> dict[str, Any]:
        required = package.program.get("required_fields", ())
        if not isinstance(required, list) or not all(field in public_input for field in required):
            return self._candidate_result(package, "ABSTAIN", reason="MALFORMED_INPUT")
        allowed_fields = _HANDOFF_BASE_FIELDS | frozenset(package.program.get("deny_flags", ())) | frozenset(
            package.program.get("abstain_flags", ())
        )
        if set(public_input) - allowed_fields:
            return self._candidate_result(package, "ABSTAIN", reason="UNKNOWN_INPUT_FIELD")
        if self._flagged(package.program, public_input, "deny_flags"):
            return self._candidate_result(package, "DENY", reason="AUTHORITY_OR_DISCLOSURE_VETO")
        if self._flagged(package.program, public_input, "abstain_flags"):
            return self._candidate_result(package, "ABSTAIN", reason="FRESHNESS_OR_RESOURCE_VETO")
        identifiers = ("run_id", "task_id", "delegation_id")
        if not all(
            isinstance(public_input.get(field), str) and public_input[field].strip()
            for field in identifiers
        ):
            return self._candidate_result(package, "ABSTAIN", reason="INVALID_TASK_BINDING")
        if not all(
            _DIGEST.fullmatch(str(public_input.get(field, ""))) is not None
            for field in ("delegation_task_digest", "context_projection_digest")
        ):
            return self._candidate_result(package, "ABSTAIN", reason="INVALID_BINDING_DIGEST")
        candidate_bundle = public_input.get("candidate_bundle")
        if not isinstance(candidate_bundle, dict):
            return self._candidate_result(package, "ABSTAIN", reason="INVALID_CANDIDATE_BUNDLE")
        if _contains_sensitive_marker(candidate_bundle):
            return self._candidate_result(package, "DENY", reason="SENSITIVE_MARKER_DETECTED")
        return self._candidate_result(
            package,
            "HANDOFF",
            reason="TASK_BOUND_CANDIDATE",
            run_id=public_input["run_id"],
            task_id=public_input["task_id"],
            delegation_id=public_input["delegation_id"],
            delegation_task_digest=public_input["delegation_task_digest"],
            context_projection_digest=public_input["context_projection_digest"],
            candidate_bundle_digest=sha256_digest(public_input["candidate_bundle"]),
        )

    def _execute_launch(
        self, package: LoadedSkillPackage, public_input: Mapping[str, Any]
    ) -> dict[str, Any]:
        required = package.program.get("required_fields", ())
        if not isinstance(required, list) or not all(field in public_input for field in required):
            return self._candidate_result(package, "ABSTAIN", reason="MALFORMED_INPUT")
        allowed_fields = _LAUNCH_BASE_FIELDS | frozenset(package.program.get("deny_flags", ())) | frozenset(
            package.program.get("abstain_flags", ())
        )
        if set(public_input) - allowed_fields:
            return self._candidate_result(package, "ABSTAIN", reason="UNKNOWN_INPUT_FIELD")
        preview_digest = public_input.get("preview_receipt_digest")
        approval_digest = public_input.get("approval_receipt_digest")
        if not _valid_nonzero_digest(preview_digest):
            return self._candidate_result(
                package, "ABSTAIN", reason="INVALID_PREVIEW_RECEIPT_DIGEST"
            )
        if not _valid_nonzero_digest(approval_digest):
            return self._candidate_result(
                package, "ABSTAIN", reason="INVALID_APPROVAL_RECEIPT_DIGEST"
            )
        if self._flagged(package.program, public_input, "deny_flags"):
            action = "DENY"
            reason = "AUTHORITY_OR_DISCLOSURE_VETO"
        elif self._flagged(package.program, public_input, "abstain_flags"):
            action = "ABSTAIN"
            reason = "FRESHNESS_OR_RESOURCE_VETO"
        else:
            mapping = package.program.get("mapping")
            if not isinstance(mapping, dict):
                raise IntegrityError("SKILL_LAUNCH_MAPPING_INVALID")
            action = str(mapping.get(str(public_input.get("classification")), "ABSTAIN"))
            reason = str(public_input.get("reason_code"))
        return self._candidate_result(
            package,
            action,
            reason=reason,
            preview_receipt_digest=preview_digest,
            approval_receipt_digest=approval_digest,
            object_id=public_input.get("object_id"),
            contract_digest=package.contract["content_digest"],
        )

    def _execute(
        self, package: LoadedSkillPackage, public_input: Mapping[str, Any]
    ) -> dict[str, Any]:
        entry_point = package.manifest["entry_point"]
        if entry_point == "QUOTE_COMPOSE_V1":
            return self._execute_quote(package, public_input)
        if entry_point == "STRUCTURED_DOMAIN_HANDOFF_V1":
            return self._execute_handoff(package, public_input)
        if entry_point == "LAUNCH_READINESS_V1":
            return self._execute_launch(package, public_input)
        raise IntegrityError("SKILL_ENTRY_POINT_NOT_ALLOWED")

    @staticmethod
    def _verify_dependency_lock(
        package: LoadedSkillPackage, observed_dependencies: Mapping[str, str]
    ) -> None:
        expected = package.manifest["dependencies"]
        if dict(observed_dependencies) != expected:
            raise IntegrityError("SKILL_DEPENDENCY_RECEIPT_STALE")

    @staticmethod
    def _verify_release_authorization(
        package: LoadedSkillPackage, receipt: Mapping[str, Any]
    ) -> None:
        _verify_record(receipt, error="SKILL_RELEASE_RECEIPT_DIGEST_MISMATCH")
        release = package.manifest["release_artifact"]
        if (
            receipt.get("schema_version") != SKILL_RELEASE_SCHEMA
            or receipt.get("actor_id") != SKILL_REGISTRY_AUTHORITY
            or receipt.get("release_artifact_id") != release["id"]
            or receipt.get("package_digest") != package.package_digest
            or receipt.get("effective_package_digest") != package.package_digest
            or receipt.get("to_state") not in CALLABLE_RELEASE_STATES
        ):
            raise IntegrityError("SKILL_RELEASE_AUTHORIZATION_INVALID")

    @staticmethod
    def _pointer_value(value: Mapping[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("/")[1:]:
            key = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or key not in current:
                return None
            current = current[key]
        return current

    def _apply_candidate_content(
        self,
        package: LoadedSkillPackage,
        public_input: Mapping[str, Any],
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload = package.manifest.get("candidate_content_bundle")
        if payload is None:
            return result, None
        if not isinstance(payload, Mapping):
            raise IntegrityError("SKILL_CONTENT_BUNDLE_CONTRACT_INVALID")
        bundle = SkillContentBundle.from_payload(payload)
        if (
            bundle.payload["target_skill"] != package.name
            or bundle.payload["predecessor_package_digest"]
            != package.manifest["release_artifact"]["predecessor_package_digest"]
        ):
            raise IntegrityError("SKILL_CONTENT_BUNDLE_PACKAGE_BINDING_MISMATCH")
        checklist = bundle.quote_recovery_checklist()
        applies = (
            self._pointer_value(public_input, str(checklist["applies_when_path"]))
            == checklist["applies_when_equals"]
        )
        consumed: list[str] = []
        passed: bool | None = None
        if applies:
            checklist_path = "checklists/quote-evidence-recovery.v1.json"
            consumed.append(bundle.resource_digests()[checklist_path])
            missing = [
                path for path in checklist["required_digest_paths"]
                if not _valid_nonzero_digest(self._pointer_value(public_input, path))
            ]
            passed = not missing
            if not passed and result.get("action") == "HANDOFF":
                diagnostic_map = package.manifest.get("candidate_diagnostic_reason_map", {})
                if diagnostic_map and package.manifest.get(
                    "candidate_diagnostic_policy_digest"
                ) != quote_diagnostic_policy_digest(package):
                    raise IntegrityError("SKILL_DIAGNOSTIC_POLICY_DRIFT")
                if not isinstance(diagnostic_map, dict) or any(
                    key not in QUOTE_DIAGNOSTIC_REASON_CATALOG
                    or value != QUOTE_DIAGNOSTIC_REASON_CATALOG[key]
                    for key, value in diagnostic_map.items()
                ):
                    raise IntegrityError("SKILL_DIAGNOSTIC_REASON_POLICY_DENIED")
                reason = str(checklist["failure_reason"])
                for key, path in (
                    ("request", "/candidate_bundle/evidence/request_digest"),
                    ("resume", "/candidate_bundle/evidence/resume_digest"),
                    ("outcome", "/candidate_bundle/evidence/outcome_artifact_digest"),
                ):
                    if path in missing:
                        reason = diagnostic_map.get(key, reason)
                        break
                result = self._candidate_result(
                    package,
                    str(checklist["failure_action"]),
                    reason=reason,
                )
        return result, {
            "bundle_digest": bundle.digest,
            "consumer_id": bundle.payload["consumer_id"],
            "loaded_resource_digests": sorted(bundle.resource_digests().values()),
            "consumed_resource_digests": consumed,
            "applicable": applies,
            "checklist_passed": passed,
            "target_writes": 0,
        }

    def _interpret_candidate_with_trace(
        self, package: LoadedSkillPackage, public_input: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        input_payload = deepcopy(dict(public_input))
        trace = None
        if _json_schema_valid(input_payload, package.input_schema):
            result = self._execute(package, input_payload)
            result, trace = self._apply_candidate_content(package, input_payload, result)
            applicability = package.manifest.get("candidate_applicability", {})
            for path, expected in applicability.items():
                observed: Any = input_payload
                for part in path.split("/")[1:]:
                    key = part.replace("~1", "/").replace("~0", "~")
                    if not isinstance(observed, Mapping) or key not in observed:
                        observed = {"observation": "MISSING"}
                        break
                    observed = observed[key]
                if (
                    sha256_digest(observed) != sha256_digest(expected)
                    and result.get("action")
                    not in {"DENY", "ABSTAIN", "SAFE_ABSTAIN", "ESCALATE"}
                ):
                    result = self._candidate_result(
                        package, "ABSTAIN", reason="CANDIDATE_PRECONDITION_NOT_MET"
                    )
                    break
        else:
            result = self._candidate_result(
                package,
                "ABSTAIN",
                reason="INPUT_SCHEMA_VALIDATION_FAILED",
            )
            result, trace = self._apply_candidate_content(package, input_payload, result)
        if result.get("target_writes") != 0 or result.get("candidate_only") is not True:
            raise IntegrityError("SKILL_RESULT_EFFECT_BOUNDARY_WIDENED")
        if not _json_schema_valid(result, package.output_schema):
            raise IntegrityError("SKILL_OUTPUT_SCHEMA_VALIDATION_FAILED")
        return result, trace

    def interpret_candidate(
        self, package: LoadedSkillPackage, public_input: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Run an already verified package through the same bounded interpreter.

        This produces a candidate only; it grants no release or write authority.
        """
        result, _ = self._interpret_candidate_with_trace(package, public_input)
        return result

    def _invoke(
        self,
        name: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        expected_package_digest: str,
        authorization_mode: str,
        release_receipt: Mapping[str, Any] | None,
        observed_dependencies: Mapping[str, str] | None,
        created_at: str,
    ) -> SkillInvocation:
        package = self.load(name, expected_package_digest=expected_package_digest)
        self._check_context_bindings(public_input, context)
        if authorization_mode == "RELEASE":
            if release_receipt is None or observed_dependencies is None:
                raise IntegrityError("SKILL_RELEASE_AUTHORIZATION_REQUIRED")
            self._verify_release_authorization(package, release_receipt)
            self._verify_dependency_lock(package, observed_dependencies)
            release_digest: str | None = str(release_receipt["digest"])
        elif authorization_mode == "EVALUATION":
            if release_receipt is not None:
                raise IntegrityError("SKILL_EVALUATION_CANNOT_PRESENT_RELEASE_AUTHORITY")
            release_digest = None
        else:
            raise IntegrityError("SKILL_INVOCATION_MODE_INVALID")
        input_payload = deepcopy(dict(public_input))
        result, content_trace = self._interpret_candidate_with_trace(package, input_payload)
        action = str(result.get("action", "ABSTAIN"))
        if action == "DENY":
            outcome = "DENY"
        elif action in {"ABSTAIN", "SAFE_ABSTAIN", "ESCALATE"}:
            outcome = "ABSTAIN"
        else:
            outcome = "SUCCESS"
        input_digest = sha256_digest(input_payload)
        invocation_binding_digest = sha256_digest(
            {
                "run_id": context.run_id,
                "task_id": context.task_id,
                "delegation_id": context.delegation_id,
                "input_digest": input_digest,
                "package_digest": package.package_digest,
            }
        )
        receipt_body = {
            "schema_version": SKILL_INVOCATION_SCHEMA,
            "id": f"skill-invocation:{invocation_binding_digest.removeprefix('sha256:')}",
            "run_id": context.run_id,
            "task_id": context.task_id,
            "delegation_id": context.delegation_id,
            "actor_id": context.actor_id,
            "package_id": package.manifest["package_id"],
            "package_digest": package.package_digest,
            "manifest_digest": package.manifest["manifest_digest"],
            "program_digest": package.manifest["program_content_digest"],
            "release_artifact_id": package.manifest["release_artifact"]["id"],
            "entry_point": package.manifest["entry_point"],
            "authorization_mode": authorization_mode,
            "release_receipt_digest": release_digest,
            "input_digest": input_digest,
            "output_digest": sha256_digest(result),
            "outcome": outcome,
            "candidate_only": True,
            "target_writes": 0,
            "created_at": created_at,
        }
        if content_trace is not None:
            receipt_body["candidate_content"] = content_trace
        return SkillInvocation(result=result, receipt=_record(receipt_body))

    def invoke(
        self,
        name: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        expected_package_digest: str,
        release_receipt: Mapping[str, Any],
        observed_dependencies: Mapping[str, str],
        created_at: str | None = None,
    ) -> SkillInvocation:
        del (
            name,
            public_input,
            context,
            expected_package_digest,
            release_receipt,
            observed_dependencies,
            created_at,
        )
        raise IntegrityError("SKILL_RELEASE_LEDGER_REQUIRED")

    def _invoke_released(
        self,
        name: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        expected_package_digest: str,
        release_receipt: Mapping[str, Any],
        observed_dependencies: Mapping[str, str],
        created_at: str | None = None,
    ) -> SkillInvocation:
        return self._invoke(
            name,
            public_input,
            context=context,
            expected_package_digest=expected_package_digest,
            authorization_mode="RELEASE",
            release_receipt=release_receipt,
            observed_dependencies=observed_dependencies,
            created_at=created_at or _utc_now(),
        )

    def invoke_for_evaluation(
        self,
        name: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        expected_package_digest: str,
        created_at: str | None = None,
    ) -> SkillInvocation:
        return self._invoke(
            name,
            public_input,
            context=context,
            expected_package_digest=expected_package_digest,
            authorization_mode="EVALUATION",
            release_receipt=None,
            observed_dependencies=None,
            created_at=created_at or _utc_now(),
        )


class SkillCandidateOverlayRegistry(SkillPackageRegistry):
    """One-process, evaluation-only vNext view of an existing Skill package.

    The overlay deliberately reuses the reviewed package resources and the one
    existing registry namespace.  It changes only the content-addressed release
    identity so an ``ExperienceCandidate`` can be evaluated and, after an
    external human decision, authorized by :class:`SkillReleaseLedger` without
    writing a fourth package into ``skills/`` or creating a second registry.

    Constructing this object grants no authority.  Callers are expected to keep
    it private until their own approval contract has passed; the release ledger
    remains the only way to make its exact digest callable.
    """

    TARGET_SKILL = "structured-domain-handoff"

    def _project_license_files(self) -> dict[str, Any]:
        return self.base_registry._project_license_files()

    def __init__(
        self,
        base_registry: SkillPackageRegistry,
        *,
        candidate_ref: str,
        candidate_digest: str,
        proposed_version: str,
        source_run_id: str,
        target_skill: str = TARGET_SKILL,
        applicability: Mapping[str, Any] | None = None,
        boundary: Mapping[str, Any] | None = None,
        content_bundle: Mapping[str, Any] | None = None,
        diagnostic_reason_map: Mapping[str, str] | None = None,
        historical_package_digest: str | None = None,
    ) -> None:
        if not candidate_ref.strip() or not source_run_id.strip():
            raise ValueError("SKILL_CANDIDATE_OVERLAY_BINDING_EMPTY")
        if not _valid_nonzero_digest(candidate_digest):
            raise IntegrityError("SKILL_CANDIDATE_OVERLAY_DIGEST_INVALID")
        if not proposed_version.strip():
            raise ValueError("SKILL_CANDIDATE_OVERLAY_VERSION_EMPTY")
        if target_skill not in EXPECTED_ENTRY_POINTS:
            raise IntegrityError("SKILL_CANDIDATE_OVERLAY_TARGET_INVALID")
        if historical_package_digest is not None and not _valid_nonzero_digest(
            historical_package_digest
        ):
            raise IntegrityError("SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH")
        self.TARGET_SKILL = target_skill
        self.base_registry = base_registry
        self.resource_mode = "EVALUATION_ONLY_CANDIDATE_OVERLAY"
        base = base_registry.load(self.TARGET_SKILL)
        verified_bundle = (
            SkillContentBundle.from_payload(content_bundle)
            if content_bundle is not None
            else None
        )
        if verified_bundle is not None and (
            verified_bundle.payload["target_skill"] != self.TARGET_SKILL
            or verified_bundle.payload["predecessor_package_digest"] != base.package_digest
        ):
            raise IntegrityError("SKILL_CONTENT_BUNDLE_PACKAGE_BINDING_MISMATCH")
        if diagnostic_reason_map is not None and (
            any(
                key not in QUOTE_DIAGNOSTIC_REASON_CATALOG
                or value != QUOTE_DIAGNOSTIC_REASON_CATALOG[key]
                for key, value in diagnostic_reason_map.items()
            )
        ):
            raise IntegrityError("SKILL_DIAGNOSTIC_REASON_POLICY_DENIED")
        manifest = deepcopy(base.manifest)
        # A v1 content bundle binds the exact predecessor.  It cannot be
        # inherited as the next version's own bundle without re-review.
        manifest.pop("candidate_content_bundle", None)
        manifest["package_id"] = (
            f"skill-package:{self.TARGET_SKILL}@{proposed_version}"
        )
        manifest["version"] = proposed_version
        manifest["release_artifact"] = {
            "id": f"skill-release:{self.TARGET_SKILL}@{proposed_version}",
            "source_candidate_ref": candidate_ref,
            "source_candidate_digest": candidate_digest,
            "source_candidate_executable": False,
            "predecessor_package_digest": base.package_digest,
        }
        manifest["candidate_overlay"] = {
            "mode": "EVALUATION_ONLY",
            "source_run_id": source_run_id,
            "candidate_digest": candidate_digest,
            "base_package_digest": base.package_digest,
            "resource_change": (
                "ALLOWLISTED_CONTENT_ONLY" if verified_bundle is not None else "NONE"
            ),
            "claim_boundary": "CONTROLLED_LOCAL_GOVERNANCE_PROOF_NOT_PRODUCTION_GENERALIZATION",
        }
        if verified_bundle is not None:
            manifest["candidate_content_bundle"] = deepcopy(verified_bundle.payload)
        if diagnostic_reason_map is not None:
            manifest["candidate_diagnostic_reason_map"] = dict(sorted(diagnostic_reason_map.items()))
            manifest["candidate_diagnostic_policy_digest"] = quote_diagnostic_policy_digest(base)
        if applicability is not None:
            if not applicability or any(not key.startswith("/") for key in applicability):
                raise IntegrityError("SKILL_CANDIDATE_APPLICABILITY_INVALID")
            manifest["candidate_applicability"] = deepcopy(dict(applicability))
        if boundary is not None:
            manifest["candidate_boundary"] = deepcopy(dict(boundary))
        provenance = list(manifest.get("provenance_refs", ()))
        provenance.append(candidate_ref)
        manifest["provenance_refs"] = provenance
        # New candidates always carry the current grant. Only a verified
        # historical read may reconstruct the original notice-free bytes, and
        # only when their complete package digest exactly matches that read.
        historical_body = {
            key: value for key, value in manifest.items() if key != "manifest_digest"
        }
        if (
            historical_package_digest is None
            or sha256_digest(historical_body) != historical_package_digest
        ):
            manifest["license"] = _PROJECT_SKILL_LICENSE
            manifest["license_files"] = base_registry._project_license_files()
        manifest_body = {
            key: value for key, value in manifest.items() if key != "manifest_digest"
        }
        package_digest = sha256_digest(manifest_body)
        if historical_package_digest is not None and package_digest != historical_package_digest:
            raise IntegrityError("SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH")
        manifest["manifest_digest"] = package_digest
        resource_digests = deepcopy(base.resource_digests)
        if verified_bundle is not None:
            resource_digests.update(
                {
                    f"candidate:{path}": digest
                    for path, digest in verified_bundle.resource_digests().items()
                }
            )
        self._overlay = LoadedSkillPackage(
            name=base.name,
            version=proposed_version,
            package_digest=package_digest,
            manifest=manifest,
            contract=deepcopy(base.contract),
            program=deepcopy(base.program),
            input_schema=deepcopy(base.input_schema),
            output_schema=deepcopy(base.output_schema),
            description=base.description,
            skill_bytes=bytes(base.skill_bytes),
            reference_bytes=deepcopy(base.reference_bytes),
            resource_digests=resource_digests,
            raw_resource_bytes=deepcopy(base.raw_resource_bytes),
        )

    def load(
        self, name: str, *, expected_package_digest: str | None = None
    ) -> LoadedSkillPackage:
        package = (
            self._overlay
            if name == self.TARGET_SKILL
            else self.base_registry.load(name)
        )
        if (
            expected_package_digest is not None
            and expected_package_digest != package.package_digest
        ):
            raise IntegrityError("SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH")
        return LoadedSkillPackage(
            name=package.name,
            version=package.version,
            package_digest=package.package_digest,
            manifest=deepcopy(package.manifest),
            contract=deepcopy(package.contract),
            program=deepcopy(package.program),
            input_schema=deepcopy(package.input_schema),
            output_schema=deepcopy(package.output_schema),
            description=package.description,
            skill_bytes=bytes(package.skill_bytes),
            reference_bytes=deepcopy(package.reference_bytes),
            resource_digests=deepcopy(package.resource_digests),
            raw_resource_bytes=deepcopy(package.raw_resource_bytes),
        )

    def discover(self) -> tuple[dict[str, Any], ...]:
        """Return the same three Skill names, replacing only the target head."""

        packages = [self.load(name) for name in sorted(EXPECTED_ENTRY_POINTS)]
        return tuple(
            {
                "name": package.name,
                "version": package.version,
                "package_id": str(package.manifest["package_id"]),
                "package_digest": package.package_digest,
                "entry_point": str(package.manifest["entry_point"]),
                "description": package.description,
                "resource_mode": self.resource_mode,
                "input_schema_digest": package.resource_digests["input_schema"],
                "output_schema_digest": package.resource_digests["output_schema"],
                "language_reference_digests": {
                    "zh-CN": package.resource_digests["reference_zh_cn"],
                    "en": package.resource_digests["reference_en"],
                },
            }
            for package in packages
        )


class SkillPackageEvaluator:
    """Evaluate caller-owned cases without packaging evaluator gold."""

    def __init__(self, registry: SkillPackageRegistry, *, before_invocation: Callable[[], None] | None = None) -> None:
        self.registry = registry
        self._before_invocation = before_invocation
        self._issued_receipts: dict[int, tuple[Mapping[str, Any], str]] = {}
        self._invocation_observations: dict[int, tuple[dict[str, Any], ...]] = {}

    def require_issued(self, receipt: Mapping[str, Any]) -> None:
        """Require exact object membership in this process-local evaluator instance."""

        _verify_record(receipt, error="SKILL_EVALUATION_RECEIPT_DIGEST_MISMATCH")
        digest = receipt.get("digest")
        issued = self._issued_receipts.get(id(receipt))
        if issued is None or issued[0] is not receipt or issued[1] != digest:
            raise IntegrityError("SKILL_EVALUATION_RECEIPT_NOT_ISSUED")

    def invocation_observations(self, receipt: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        """Return results actually observed by this evaluator, without another call."""
        self.require_issued(receipt)
        return deepcopy(self._invocation_observations[id(receipt)])

    @staticmethod
    def _security_action_allowed(partition: str, action: str) -> bool:
        allowed = {
            "PERMISSION": {"DENY", "ABSTAIN", "SAFE_ABSTAIN"},
            "INJECTION": {"DENY", "ABSTAIN", "SAFE_ABSTAIN"},
            "MALFORMED": {"DENY", "ABSTAIN", "SAFE_ABSTAIN"},
            "RESOURCE_OR_DEADLINE": {"DENY", "ABSTAIN", "SAFE_ABSTAIN", "ESCALATE"},
        }
        return action in allowed.get(partition, {action})

    def evaluate(
        self,
        name: str,
        cases: Sequence[SkillEvaluationCase],
        *,
        evaluated_at: str | None = None,
        premise_lock: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        package = self.registry.load(name)
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise IntegrityError("SKILL_EVALUATION_DUPLICATE_CASE_ID")
        partitions = {case.partition for case in cases}
        if partitions != set(PARTITIONS) or any(
            not case.case_id or case.partition not in PARTITIONS for case in cases
        ):
            raise IntegrityError("SKILL_EVALUATION_PARTITION_COVERAGE_INCOMPLETE")
        partition_results: dict[str, list[bool]] = {partition: [] for partition in PARTITIONS}
        case_results: list[dict[str, Any]] = []
        security_failures = 0
        target_writes = 0
        suite_bindings: list[dict[str, str]] = []
        observations: list[dict[str, Any]] = []
        timestamp = evaluated_at or _utc_now()
        for case in cases:
            public_input = deepcopy(dict(case.public_input))
            context = InvocationContext(
                run_id=str(public_input.get("run_id", f"evaluation:{name}")),
                task_id=str(public_input.get("task_id", f"evaluation-task:{case.case_id}")),
                delegation_id=str(
                    public_input.get("delegation_id", f"evaluation-delegation:{case.case_id}")
                ),
                actor_id="evaluator:skill-package-independent",
            )
            if self._before_invocation is not None:
                self._before_invocation()
            invocation = self.registry.invoke_for_evaluation(
                name,
                public_input,
                context=context,
                expected_package_digest=package.package_digest,
                created_at=timestamp,
            )
            action = str(invocation.result.get("action", "ABSTAIN"))
            observations.append({
                "case_ref": case.case_id,
                "input_digest": invocation.receipt["input_digest"],
                "result": deepcopy(invocation.result),
                "receipt": deepcopy(invocation.receipt),
            })
            passed = action == case.expected_action
            partition_results[case.partition].append(passed)
            target_writes += int(invocation.result.get("target_writes", 0))
            security_ok = (
                case.partition not in SECURITY_PARTITIONS
                or (
                    passed
                    and self._security_action_allowed(case.partition, action)
                    and invocation.result.get("candidate_only") is True
                    and invocation.result.get("target_writes") == 0
                )
            )
            security_failures += int(not security_ok)
            suite_bindings.append(
                {
                    "case_id": case.case_id,
                    "partition": case.partition,
                    "input_digest": invocation.receipt["input_digest"],
                    "expected_output_digest": sha256_digest({"action": case.expected_action}),
                }
            )
            case_results.append(
                {
                    "case_ref": case.case_id,
                    "partition": case.partition,
                    "baseline_output_digest": sha256_digest(
                        {"case_id": case.case_id, "baseline": "NOT_SCORED"}
                    ),
                    "candidate_output_digest": invocation.receipt["output_digest"],
                    "passed": passed,
                    "repaired_baseline_failure": False,
                    "regressed_baseline_success": False,
                    "reason_codes": [
                        "EXPECTED_ACTION_MATCH" if passed else "EXPECTED_ACTION_MISMATCH"
                    ],
                }
            )
        threshold = float(package.manifest["evaluation"]["functional_min_pass_rate"])
        gates: list[dict[str, Any]] = [
            {
                "gate_id": "exact_package_digest",
                "passed": True,
                "observed": package.package_digest,
                "threshold": package.manifest["manifest_digest"],
                "reason_code": "EXACT_PACKAGE_LOADED",
            }
        ]
        for partition in PARTITIONS:
            values = partition_results[partition]
            pass_rate = sum(values) / len(values)
            required = (
                float(package.manifest["evaluation"]["security_min_pass_rate"])
                if partition in SECURITY_PARTITIONS
                else threshold
            )
            gates.append(
                {
                    "gate_id": f"partition:{partition.lower()}",
                    "passed": pass_rate >= required,
                    "observed": round(pass_rate, 6),
                    "threshold": required,
                    "reason_code": "UNIFORM_PARTITION_GATE",
                }
            )
        gates.extend(
            [
                {
                    "gate_id": "critical_security_failures",
                    "passed": security_failures == 0,
                    "observed": security_failures,
                    "threshold": 0,
                    "reason_code": "SECURITY_VETO",
                },
                {
                    "gate_id": "target_writes",
                    "passed": target_writes == 0,
                    "observed": target_writes,
                    "threshold": 0,
                    "reason_code": "CANDIDATE_ONLY_EFFECT_CEILING",
                },
            ]
        )
        verdict = "CANARY" if all(gate["passed"] for gate in gates) else "QUARANTINED"
        release = package.manifest["release_artifact"]
        receipt_body = {
            "id": f"skill-evaluation:{name}@{package.version}",
            "candidate_ref": release["source_candidate_ref"] or release["id"],
            "candidate_digest": release["source_candidate_digest"] or package.package_digest,
            "candidate_program_digest": package.manifest["program_content_digest"],
            "evaluation_suite_digest": sha256_digest(suite_bindings),
            "case_results": case_results,
            "gate_results": gates,
            "verdict": verdict,
            "premise_lock": dict(
                premise_lock
                or {
                    "package": package.package_digest,
                    "runtime": "restricted-skill-registry@1.0.0",
                    "dependencies": sha256_digest(package.manifest["dependencies"]),
                    "gold_boundary": "evaluator-only:not-packaged",
                }
            ),
            "evaluated_at": timestamp,
        }
        receipt = _record(receipt_body)
        self._issued_receipts[id(receipt)] = (receipt, str(receipt["digest"]))
        self._invocation_observations[id(receipt)] = tuple(observations)
        return receipt


class SkillReleaseLedger:
    """Append-only in-memory control-plane ledger for package release authority."""

    _ALLOWED_RELEASE_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        "DRAFT": {"EVALUATED"},
        "EVALUATED": {"SHADOW"},
        "SHADOW": {"CANARY"},
        "CANARY": {"ACTIVE"},
        "REQUALIFICATION_REQUIRED": {"EVALUATED"},
    }

    def __init__(
        self, registry: SkillPackageRegistry, evaluator: SkillPackageEvaluator
    ) -> None:
        if evaluator.registry is not registry:
            raise IntegrityError("SKILL_EVALUATOR_REGISTRY_MISMATCH")
        self.registry = registry
        self.evaluator = evaluator
        self._events: list[dict[str, Any]] = []
        self._heads: dict[str, dict[str, Any]] = {}

    @property
    def history(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._events))

    def head(self, name: str) -> dict[str, Any] | None:
        value = self._heads.get(name)
        return deepcopy(value) if value is not None else None

    @staticmethod
    def _require_authority(actor_id: str) -> None:
        if actor_id != SKILL_REGISTRY_AUTHORITY:
            raise IntegrityError("SKILL_RELEASE_AUTHORITY_DENIED")

    def _verify_evaluation(
        self,
        package: LoadedSkillPackage,
        evaluation_receipt: Mapping[str, Any],
    ) -> None:
        self.evaluator.require_issued(evaluation_receipt)
        release = package.manifest["release_artifact"]
        expected_candidate = release["source_candidate_digest"] or package.package_digest
        gates = evaluation_receipt.get("gate_results")
        premise_lock = evaluation_receipt.get("premise_lock")
        if (
            evaluation_receipt.get("verdict") != "CANARY"
            or evaluation_receipt.get("candidate_digest") != expected_candidate
            or evaluation_receipt.get("candidate_program_digest")
            != package.manifest["program_content_digest"]
            or not isinstance(gates, list)
            or not gates
            or not all(isinstance(gate, dict) and gate.get("passed") is True for gate in gates)
            or not isinstance(premise_lock, Mapping)
            or premise_lock.get("package") != package.package_digest
            or premise_lock.get("dependencies")
            != sha256_digest(package.manifest["dependencies"])
        ):
            raise IntegrityError("SKILL_EVALUATION_NOT_RELEASABLE")

    def _restore_verified_history(
        self, name: str, evaluation_receipt: Mapping[str, Any], history: Sequence[Mapping[str, Any]],
        *, store: StateStore, source_ref: str, governance_authority: str, evaluator_authority: str,
    ) -> None:
        """Restore a controller-verified canonical admission without re-executing gold cases.

        Only the persisted admission controller calls this internal method after
        resolving its exact Source, independent decision and evaluation records.
        Public invocation still requires this ledger's verified current head.
        """
        if self._events or self._heads:
            raise IntegrityError("SKILL_LEDGER_RESTORE_REQUIRES_EMPTY_LEDGER")
        package = self.registry.load(name)
        source_id, version = source_ref.rsplit("@", 1)
        source = store.get_object(source_id, version)
        if source.state.value != "CURRENT" or source.kind != "Source":
            raise IntegrityError("SKILL_RESTORATION_SOURCE_NOT_CURRENT")
        decision = store.load_artifact(source.payload["decision_ref"]).payload
        evaluation = store.load_artifact(source.payload["evaluation_ref"]).payload
        candidate = store.load_artifact(source.payload["candidate_ref"]).payload
        for record in (decision, evaluation, candidate):
            _verify_record(record, error="SKILL_RESTORATION_CANONICAL_RECORD_INVALID")
        release = package.manifest["release_artifact"]
        if (source.payload.get("release_history") != list(history)
            or source.payload.get("package_digest") != package.package_digest
            or evaluation.get("current") != dict(evaluation_receipt)
            or evaluation.get("verdict") != "QUALIFIED"
            or evaluation.get("issuer") != evaluator_authority
            or evaluation.get("candidate_ref") != source.payload["candidate_ref"]
            or decision.get("candidate_ref") != source.payload["candidate_ref"]
            or decision.get("evaluation_ref") != source.payload["evaluation_ref"]
            or decision.get("verdict") != "ADMIT" or decision.get("actor_id") != governance_authority
            or governance_authority == evaluator_authority or governance_authority == candidate.get("author_id")
            or candidate.get("digest") != release["source_candidate_digest"]
            or source.payload["candidate_ref"] != release["source_candidate_ref"]):
            raise IntegrityError("SKILL_RESTORATION_CANONICAL_ADMISSION_MISMATCH")
        _verify_record(evaluation_receipt, error="SKILL_RESTORED_EVALUATION_DIGEST_INVALID")
        gates = evaluation_receipt.get("gate_results", ())
        cases = evaluation_receipt.get("case_results", ())
        premise = evaluation_receipt.get("premise_lock", {})
        release = package.manifest["release_artifact"]
        if (evaluation_receipt.get("verdict") != "CANARY"
            or evaluation_receipt.get("candidate_digest") != (release["source_candidate_digest"] or package.package_digest)
            or evaluation_receipt.get("candidate_program_digest") != package.manifest["program_content_digest"]
            or not gates or not all(item.get("passed") is True for item in gates)
            or {item.get("partition") for item in cases} != set(PARTITIONS)
            or not all(item.get("passed") is True for item in cases)
            or premise.get("package") != package.package_digest
            or premise.get("dependencies") != sha256_digest(package.manifest["dependencies"])
            or len(history) != 3):
            raise IntegrityError("SKILL_RESTORED_EVALUATION_NOT_QUALIFIED")
        staged = SkillReleaseLedger(self.registry, self.evaluator)
        for state, supplied in zip(("EVALUATED", "SHADOW", "CANARY"), history, strict=True):
            _verify_record(supplied, error="SKILL_RESTORED_HISTORY_DIGEST_INVALID")
            actual = staged._append(package=package, event_type="RELEASE", to_state=state,
                                  evaluation_receipt_digest=str(evaluation_receipt["digest"]),
                                  evaluation_premise_lock_digest=sha256_digest(premise),
                                  actor_id=SKILL_REGISTRY_AUTHORITY,
                                  reason_codes=supplied.get("reason_codes", ()), changed_dependency_refs=(),
                                  created_at=str(supplied.get("created_at", "")))
            if actual != dict(supplied):
                raise IntegrityError("SKILL_RESTORED_HISTORY_BINDING_MISMATCH")

        self._events = deepcopy(staged._events)
        self._heads = deepcopy(staged._heads)

    def _append(
        self,
        *,
        package: LoadedSkillPackage,
        event_type: str,
        to_state: str,
        evaluation_receipt_digest: str,
        evaluation_premise_lock_digest: str,
        actor_id: str,
        reason_codes: Sequence[str],
        changed_dependency_refs: Sequence[str],
        created_at: str,
    ) -> dict[str, Any]:
        self._require_authority(actor_id)
        if not reason_codes or not all(isinstance(item, str) and item for item in reason_codes):
            raise ValueError("Skill release reason_codes must be non-empty")
        current = self._heads.get(package.name)
        from_state = str(current["to_state"]) if current else "DRAFT"
        predecessor = package.manifest["release_artifact"]["predecessor_package_digest"]
        effective = package.package_digest
        restoration_status = (
            "NOT_RUN" if to_state == "ROLLBACK_DECISION_RECORDED" else "NOT_APPLICABLE"
        )
        previous = self._events[-1]["digest"] if self._events else None
        release = package.manifest["release_artifact"]
        body = {
            "schema_version": SKILL_RELEASE_SCHEMA,
            "id": f"skill-release-event:{package.name}:{len(self._events) + 1:04d}",
            "event_index": len(self._events) + 1,
            "event_type": event_type,
            "skill_name": package.name,
            "release_artifact_id": release["id"],
            "package_digest": package.package_digest,
            "predecessor_package_digest": predecessor,
            "effective_package_digest": effective,
            "predecessor_executable": False,
            "restoration_status": restoration_status,
            "source_candidate_ref": release["source_candidate_ref"],
            "source_candidate_digest": release["source_candidate_digest"],
            "source_candidate_executable": False,
            "evaluation_receipt_digest": evaluation_receipt_digest,
            "evaluation_premise_lock_digest": evaluation_premise_lock_digest,
            "previous_receipt_digest": previous,
            "from_state": from_state,
            "to_state": to_state,
            "actor_id": actor_id,
            "reason_codes": list(reason_codes),
            "changed_dependency_refs": sorted(set(changed_dependency_refs)),
            "created_at": created_at,
        }
        receipt = _record(body)
        self._events.append(receipt)
        self._heads[package.name] = receipt
        return deepcopy(receipt)

    def transition(
        self,
        name: str,
        evaluation_receipt: Mapping[str, Any],
        *,
        to_state: str,
        actor_id: str,
        reason_codes: Sequence[str],
        observed_dependencies: Mapping[str, str] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        package = self.registry.load(name)
        self._require_authority(actor_id)
        self._verify_evaluation(package, evaluation_receipt)
        current = self._heads.get(name)
        from_state = str(current["to_state"]) if current else "DRAFT"
        if to_state not in self._ALLOWED_RELEASE_TRANSITIONS.get(from_state, set()):
            raise IntegrityError(f"SKILL_RELEASE_TRANSITION_INVALID:{from_state}->{to_state}")
        if from_state == "REQUALIFICATION_REQUIRED":
            if (
                observed_dependencies is None
                or dict(observed_dependencies) != package.manifest["dependencies"]
            ):
                raise IntegrityError("SKILL_REQUALIFICATION_DEPENDENCIES_NOT_RESTORED")
            if evaluation_receipt.get("digest") == current.get("evaluation_receipt_digest"):
                raise IntegrityError("SKILL_REQUALIFICATION_EVALUATION_NOT_FRESH")
            try:
                evaluated_at = datetime.fromisoformat(
                    str(evaluation_receipt["evaluated_at"]).replace("Z", "+00:00")
                )
                drifted_at = datetime.fromisoformat(
                    str(current["created_at"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise IntegrityError("SKILL_REQUALIFICATION_EVALUATION_TIME_INVALID") from exc
            if evaluated_at <= drifted_at:
                raise IntegrityError("SKILL_REQUALIFICATION_EVALUATION_NOT_FRESH")
        elif observed_dependencies is not None:
            raise IntegrityError("SKILL_REQUALIFICATION_DEPENDENCIES_UNEXPECTED")
        return self._append(
            package=package,
            event_type="RELEASE",
            to_state=to_state,
            evaluation_receipt_digest=str(evaluation_receipt["digest"]),
            evaluation_premise_lock_digest=sha256_digest(evaluation_receipt["premise_lock"]),
            actor_id=actor_id,
            reason_codes=reason_codes,
            changed_dependency_refs=(),
            created_at=created_at or _utc_now(),
        )

    def dependency_drift(
        self, name: str, observed_dependencies: Mapping[str, str]
    ) -> tuple[str, ...]:
        package = self.registry.load(name)
        expected = package.manifest["dependencies"]
        return tuple(
            sorted(
                ref
                for ref in set(expected) | set(observed_dependencies)
                if observed_dependencies.get(ref) != expected.get(ref)
            )
        )

    def mark_requalification(
        self,
        name: str,
        observed_dependencies: Mapping[str, str],
        *,
        actor_id: str,
        reason_codes: Sequence[str] = ("DEPENDENCY_DRIFT",),
        created_at: str | None = None,
    ) -> dict[str, Any]:
        package = self.registry.load(name)
        current = self._heads.get(name)
        if current is None or current["to_state"] not in {"CANARY", "ACTIVE"}:
            raise IntegrityError("SKILL_REQUALIFICATION_STATE_INVALID")
        changed = self.dependency_drift(name, observed_dependencies)
        if not changed:
            raise IntegrityError("SKILL_REQUALIFICATION_WITHOUT_DRIFT")
        return self._append(
            package=package,
            event_type="REQUALIFICATION",
            to_state="REQUALIFICATION_REQUIRED",
            evaluation_receipt_digest=current["evaluation_receipt_digest"],
            evaluation_premise_lock_digest=current["evaluation_premise_lock_digest"],
            actor_id=actor_id,
            reason_codes=reason_codes,
            changed_dependency_refs=changed,
            created_at=created_at or _utc_now(),
        )

    def quarantine(
        self,
        name: str,
        *,
        actor_id: str,
        reason_codes: Sequence[str],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        package = self.registry.load(name)
        current = self._heads.get(name)
        if current is None or current["to_state"] == "ROLLBACK_DECISION_RECORDED":
            raise IntegrityError("SKILL_QUARANTINE_STATE_INVALID")
        return self._append(
            package=package,
            event_type="QUARANTINE",
            to_state="QUARANTINED",
            evaluation_receipt_digest=current["evaluation_receipt_digest"],
            evaluation_premise_lock_digest=current["evaluation_premise_lock_digest"],
            actor_id=actor_id,
            reason_codes=reason_codes,
            changed_dependency_refs=(),
            created_at=created_at or _utc_now(),
        )

    def record_rollback_decision(
        self,
        name: str,
        *,
        actor_id: str,
        reason_codes: Sequence[str],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        package = self.registry.load(name)
        current = self._heads.get(name)
        if current is None or current["to_state"] not in {
            "CANARY",
            "ACTIVE",
            "REQUALIFICATION_REQUIRED",
            "QUARANTINED",
        }:
            raise IntegrityError("SKILL_ROLLBACK_DECISION_STATE_INVALID")
        return self._append(
            package=package,
            event_type="ROLLBACK_DECISION",
            to_state="ROLLBACK_DECISION_RECORDED",
            evaluation_receipt_digest=current["evaluation_receipt_digest"],
            evaluation_premise_lock_digest=current["evaluation_premise_lock_digest"],
            actor_id=actor_id,
            reason_codes=reason_codes,
            changed_dependency_refs=(),
            created_at=created_at or _utc_now(),
        )

    def invoke(
        self,
        name: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        observed_dependencies: Mapping[str, str],
        created_at: str | None = None,
    ) -> SkillInvocation:
        package = self.registry.load(name)
        current = self._heads.get(name)
        if current is None or current["to_state"] not in CALLABLE_RELEASE_STATES:
            raise IntegrityError("SKILL_RELEASE_HEAD_NOT_CALLABLE")
        return self.registry._invoke_released(
            name,
            public_input,
            context=context,
            expected_package_digest=package.package_digest,
            release_receipt=current,
            observed_dependencies=observed_dependencies,
            created_at=created_at,
        )
