"""Human-reviewed Skill source and immutable revision-draft artifacts."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from orgrebase.auth import current_authorization
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.skill_packages import SkillPackageRegistry

SKILL_DRAFT_MEDIA_TYPE = (
    "application/vnd.orgrebase.skill-revision-draft+json;version=1"
)
SKILL_DRAFT_PREFIX = "skill-revision-draft:"
SKILL_STEWARD_ACTOR = "human:skill-steward"
MAX_SKILL_SOURCE_BYTES = 65_536
_SEMVER = re.compile(
    r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
_FRONTMATTER_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.+?)\s*$")


def _frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise IntegrityError("SKILL_DRAFT_FRONTMATTER_REQUIRED")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise IntegrityError("SKILL_DRAFT_FRONTMATTER_INVALID") from exc
    values: dict[str, str] = {}
    section: str | None = None
    for line in lines[1:closing]:
        if line == "metadata:":
            if "metadata" in values:
                raise IntegrityError("SKILL_DRAFT_FRONTMATTER_DUPLICATE")
            values["metadata"] = "mapping"
            section = "metadata"
            continue
        if line.startswith("  "):
            if section != "metadata":
                raise IntegrityError("SKILL_DRAFT_FRONTMATTER_INVALID")
            match = _FRONTMATTER_LINE.fullmatch(line.strip())
            if match is None:
                raise IntegrityError("SKILL_DRAFT_FRONTMATTER_INVALID")
            key, value = match.groups()
            flattened = f"metadata.{key}"
            if flattened in values:
                raise IntegrityError("SKILL_DRAFT_FRONTMATTER_DUPLICATE")
            values[flattened] = value
            continue
        section = None
        match = _FRONTMATTER_LINE.fullmatch(line)
        if match is None:
            raise IntegrityError("SKILL_DRAFT_FRONTMATTER_INVALID")
        key, value = match.groups()
        if key in values:
            raise IntegrityError("SKILL_DRAFT_FRONTMATTER_DUPLICATE")
        values[key] = value
    return values


def _semver_is_later(candidate: str, current: str) -> bool:
    candidate_match = _SEMVER.fullmatch(candidate)
    current_match = _SEMVER.fullmatch(current)
    if candidate_match is None or current_match is None:
        return False
    candidate_core = tuple(
        int(candidate_match.group(part)) for part in ("major", "minor", "patch")
    )
    current_core = tuple(
        int(current_match.group(part)) for part in ("major", "minor", "patch")
    )
    if candidate_core != current_core:
        return candidate_core > current_core
    candidate_prerelease = candidate_match.group("prerelease")
    current_prerelease = current_match.group("prerelease")
    if candidate_prerelease is None:
        return current_prerelease is not None
    if current_prerelease is None:
        return False

    def identifiers(value: str) -> tuple[tuple[int, int | str], ...]:
        return tuple(
            (0, int(identifier)) if identifier.isdigit() else (1, identifier)
            for identifier in value.split(".")
        )

    return identifiers(candidate_prerelease) > identifiers(current_prerelease)


def _drafts_for_skill(store: StateStore, name: str) -> list[dict[str, Any]]:
    artifacts = store.list_artifacts(
        artifact_id_prefix=f"{SKILL_DRAFT_PREFIX}{name}@",
        expected_media_type=SKILL_DRAFT_MEDIA_TYPE,
    )
    return [
        {
            **artifact.payload,
            "artifact_id": artifact.artifact_id,
            "artifact_payload_digest": artifact.payload_digest,
        }
        for artifact in artifacts
    ]


def skill_source_catalog(
    store: StateStore,
    *,
    registry: SkillPackageRegistry | None = None,
) -> dict[str, Any]:
    selected = registry or SkillPackageRegistry()
    packages: list[dict[str, Any]] = []
    for discovered in selected.discover():
        package = selected.load(
            discovered["name"],
            expected_package_digest=discovered["package_digest"],
        )
        try:
            content = package.skill_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntegrityError("SKILL_SOURCE_NOT_UTF8") from exc
        packages.append(
            {
                "name": package.name,
                "version": package.version,
                "package_id": package.manifest["package_id"],
                "package_digest": package.package_digest,
                "skill_digest": package.resource_digests["skill"],
                "content": content,
                "content_bytes": len(package.skill_bytes),
                "resource_mode": selected.resource_mode,
                "release_state": "PUBLISHED_IMMUTABLE",
                "editable": False,
                "drafts": _drafts_for_skill(store, package.name),
            }
        )
    return {
        "schema_version": "orgrebase.skill-source-catalog.v1",
        "status": "PASS",
        "authority": "authority:skill-registry",
        "packages": packages,
        "published_source_writes": 0,
        "claim_boundary": (
            "EXACT_PUBLISHED_BYTES_ARE_READ_ONLY_DRAFTS_REQUIRE_REEVALUATION_AND_RELEASE"
        ),
    }


def create_skill_revision_draft(
    store: StateStore,
    *,
    name: str,
    actor_id: str,
    source_package_digest: str,
    source_skill_digest: str,
    proposed_version: str,
    content: str,
    registry: SkillPackageRegistry | None = None,
) -> dict[str, Any]:
    if actor_id != SKILL_STEWARD_ACTOR:
        raise AuthorizationError(
            f"SKILL_DRAFT_ACTOR_DENIED:expected={SKILL_STEWARD_ACTOR},actual={actor_id}"
        )
    selected = registry or SkillPackageRegistry()
    try:
        package = selected.load(name, expected_package_digest=source_package_digest)
    except KeyError as exc:
        raise IntegrityError("SKILL_DRAFT_SOURCE_NOT_FOUND") from exc
    if package.resource_digests["skill"] != source_skill_digest:
        raise IntegrityError("SKILL_DRAFT_SOURCE_DIGEST_MISMATCH")
    if not isinstance(content, str) or "\x00" in content:
        raise IntegrityError("SKILL_DRAFT_CONTENT_INVALID")
    encoded = content.encode("utf-8")
    if len(encoded) < 64 or len(encoded) > MAX_SKILL_SOURCE_BYTES:
        raise IntegrityError("SKILL_DRAFT_CONTENT_SIZE_INVALID")
    if not isinstance(proposed_version, str) or _SEMVER.fullmatch(proposed_version) is None:
        raise IntegrityError("SKILL_DRAFT_VERSION_INVALID")
    if not _semver_is_later(proposed_version, package.version):
        raise IntegrityError("SKILL_DRAFT_VERSION_NOT_ADVANCED")
    frontmatter = _frontmatter(content)
    if frontmatter.get("name") != package.name:
        raise IntegrityError("SKILL_DRAFT_NAME_MISMATCH")
    if frontmatter.get("metadata.version") != proposed_version:
        raise IntegrityError("SKILL_DRAFT_VERSION_MISMATCH")
    if encoded == package.skill_bytes:
        raise IntegrityError("SKILL_DRAFT_CONTENT_UNCHANGED")

    payload = {
        "schema_version": "orgrebase.skill-revision-draft.v1",
        "status": "DRAFT_SAVED",
        "name": package.name,
        "source_version": package.version,
        "proposed_version": proposed_version,
        "source_package_digest": package.package_digest,
        "source_skill_digest": package.resource_digests["skill"],
        "draft_skill_digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "content": content,
        "actor_id": actor_id,
        "candidate_only": True,
        "evaluation_status": "NOT_EVALUATED",
        "release_status": "NOT_RELEASED",
        "executable": False,
        "registry_writes": 0,
        "canonical_target_writes": 0,
        "draft_store_writes": 1,
        "claim_boundary": "DRAFT_DOES_NOT_CHANGE_CURRENT_AGENT_CAPABILITY",
    }
    artifact_payload_digest = sha256_digest(payload)
    artifact_id = f"{SKILL_DRAFT_PREFIX}{package.name}@{artifact_payload_digest[7:]}"
    with store.transaction() as connection:
        authorization = current_authorization()
        if authorization is not None:
            authorization()
            store.require_before_commit(connection, authorization)
        existed = (
            connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            is not None
        )
        stored_digest = store.save_artifact(
            connection,
            artifact_id,
            SKILL_DRAFT_MEDIA_TYPE,
            payload,
        )
        if not existed:
            store.append_event(
                connection,
                "SKILL_REVISION_DRAFT_CREATED",
                {
                    "artifact_id": artifact_id,
                    "artifact_payload_digest": stored_digest,
                    "name": package.name,
                    "source_version": package.version,
                    "proposed_version": proposed_version,
                    "actor_id": actor_id,
                    "registry_writes": 0,
                    "canonical_target_writes": 0,
                },
            )
    return {
        **payload,
        "artifact_id": artifact_id,
        "artifact_payload_digest": stored_digest,
        "idempotent_replay": existed,
    }
