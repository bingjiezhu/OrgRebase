"""Exact-byte enterprise Seed source admission and reference projection locks.

The current runtime deliberately resolves only ten packaged demonstration assets.
Unknown locators are rejected before any filesystem or network operation.  A
successful Northstar admission additionally proves that the five projections
frozen by those bytes still match the five projections computed from the actual
reference runtime implementation.  This is a bounded equivalence proof, not a
claim that arbitrary enterprise runtimes are generated from data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self

from pydantic import Field, ValidationError, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.workspace.profile_contracts import (
    EnterpriseSeedAdmissionError,
    EnterpriseSeedProfile,
    RuntimeCompatibilityMode,
    SeedComponentKind,
)

COMPONENT_ROOT_SCHEMA_VERSION = "orgrebase.enterprise-seed-component-root.v1"
SOURCE_ADMISSION_SCHEMA_VERSION = "orgrebase.enterprise-seed-source-admission-receipt.v1"
RUNTIME_PROJECTION_SCHEMA_VERSION = "orgrebase.enterprise-seed-runtime-projection-receipt.v1"
SOURCE_ADMISSION_POLICY_REF = "policy:orgrebase-enterprise-seed-source-admission@v1"
SOURCE_MEDIA_TYPE = "application/vnd.orgrebase.enterprise-seed-component+json"
MAX_SOURCE_BYTES = 1_048_576
PROFILE_MIGRATION_SCHEMA_VERSION = "orgrebase.enterprise-seed-profile-migration-receipt.v1"
VERACIER_SC008_MIGRATION_POLICY_REF = "policy:veracier-sc008-root-identity-migration@v1"


class RuntimeBindingMode(StrEnum):
    EXACT_REFERENCE_PROJECTION = "EXACT_REFERENCE_PROJECTION"
    INTAKE_ONLY = "INTAKE_ONLY"


class SourceAdmissionVerdict(StrEnum):
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class ProjectionMatchStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


_HISTORICAL_LOCATOR_ASSETS: dict[str, str] = {
    **{
        f"packaged://northstar/{kind.value.lower()}@r1": (
            f"fixtures/enterprise-seed/northstar/{kind.value.lower()}.json"
        )
        for kind in SeedComponentKind
    },
    **{
        f"fixture://veracier/{kind.value.lower()}@r1": (
            f"fixtures/enterprise-seed/veracier/{kind.value.lower()}.json"
        )
        for kind in SeedComponentKind
    },
}

_SUCCESSOR_LOCATOR_ASSETS: dict[str, str] = {
    f"fixture://veracier/{kind.value.lower()}@r2": (
        f"fixtures/enterprise-seed/veracier-r2/{kind.value.lower()}.json"
    )
    for kind in SeedComponentKind
}

_LOCATOR_ASSETS: dict[str, str] = {
    **_HISTORICAL_LOCATOR_ASSETS,
    **_SUCCESSOR_LOCATOR_ASSETS,
}

# Frozen expected raw-byte digests for the ten shipped assets.  These values are
# intentionally independent of the bytes read at runtime: changing an asset
# without changing the contract fails closed instead of silently blessing drift.
_EXPECTED_RAW_DIGESTS: dict[str, str] = {
    "packaged://northstar/domain@r1": "sha256:48f36b7d1c315b7012e0319dedc72ad045fd78c0610b496ae3d38ed9dd141339",
    "packaged://northstar/knowledge@r1": "sha256:7a8981032e0a6564b40f92247b0855703f2e3416f7a1c4a54da687673802edff",
    "packaged://northstar/authority@r1": "sha256:fd03a8fbb8b72242c1cca767e7daa3917f93283c41d2dd2c0f5c41b705b9ce2d",
    "packaged://northstar/capability@r1": "sha256:ffe9233079bd1a6d06cb6fa035218803a7ad3ed49503229ac592f2439056d2f8",
    "packaged://northstar/dependency@r1": "sha256:8277aab48f2a025d1b4b2be9edf177d73ac955cfe98a90277a6cced7997ec16d",
    "fixture://veracier/domain@r1": "sha256:6f6ff65d5fb1ff5ede680374edbe708bb82957f8c17f91084ae6b49a17fcfc91",
    "fixture://veracier/knowledge@r1": "sha256:3e9eefa9049f91d396436a69979234bad3dcdc4d3bc765e10364980fca0d69bb",
    "fixture://veracier/authority@r1": "sha256:8197f00b5a5b7a9f78c9fe00f727a1cfa69ef0a92e25793af176ea3e971cae26",
    "fixture://veracier/capability@r1": "sha256:0437e4bd129096ae706f1085eabe8d1fc4583bac663db532316dcf51f132440b",
    "fixture://veracier/dependency@r1": "sha256:08af649d4861b7ee5ac42bb8e8d2738d2e221101e35a23fc4385c32cad7240bd",
    # Filled from immutable r2 packaged bytes.  These values deliberately do
    # not derive from the bytes read during admission.
    "fixture://veracier/domain@r2": "sha256:b2aec1bc4a5d48da866cecb1d2d906580954ee7a805f3b9b8baa27af2dd425b7",
    "fixture://veracier/knowledge@r2": "sha256:e4d3d5c2df77f269ba310ca499b24028138b14bec5f585dac566c36f7c905f47",
    "fixture://veracier/authority@r2": "sha256:75e100363879e0b1d16a4836a36c3f4e548bdc5346180320f084848d4521b9a7",
    "fixture://veracier/capability@r2": "sha256:d51c3ca99cd3bbb7f8b136404f49b3dd6819a189d008601133aced444fd30075",
    "fixture://veracier/dependency@r2": "sha256:ea44263aad9aac5c3083cd75712f37644ca81cf6aba2698e42b0c03876de25b4",
}


def exact_locator_assets(*, include_successors: bool = False) -> dict[str, str]:
    """Return immutable locator bindings used by public evidence surfaces.

    The default remains the historical ten-root view so existing release-fact
    corpora remain reproducible.  New callers can request the complete resolver
    view explicitly; the default packaged resolver always recognizes both.
    """

    selected = _LOCATOR_ASSETS if include_successors else _HISTORICAL_LOCATOR_ASSETS
    return dict(selected)


@dataclass(frozen=True, slots=True)
class ResolvedSeedSource:
    raw: bytes
    logical_asset_ref: str


class SeedSourceResolver(Protocol):
    """Restricted exact-byte resolver injected at the Source trust boundary."""

    def resolve(self, locator: str) -> ResolvedSeedSource: ...


class PackagedSeedSourceResolver:
    """Resolve only the fixed packaged locator registry."""

    def __init__(self, locator_assets: Mapping[str, str] | None = None) -> None:
        self._locator_assets = MappingProxyType(dict(locator_assets or _LOCATOR_ASSETS))

    def resolve(self, locator: str) -> ResolvedSeedSource:
        try:
            relative = self._locator_assets[locator]
        except KeyError as exc:
            raise EnterpriseSeedAdmissionError("SOURCE_LOCATOR_NOT_ALLOWLISTED", locator) from exc
        try:
            selected = runtime_asset_path(relative)
            with selected.open("rb") as handle:
                raw = handle.read(MAX_SOURCE_BYTES + 1)
        except OSError as exc:
            raise EnterpriseSeedAdmissionError("SOURCE_ASSET_READ_FAILED", relative) from exc
        return ResolvedSeedSource(raw=raw, logical_asset_ref=relative)


class DirectorySeedSourceResolver:
    """Resolve an injected exact locator map under one fixed directory.

    Locators never become paths.  Only caller-provided exact mappings are
    accepted; absolute paths, traversal and symlink components fail closed.
    """

    def __init__(self, root: str | Path, locator_assets: Mapping[str, str]) -> None:
        selected_root = Path(root)
        try:
            resolved_root = selected_root.resolve(strict=True)
        except OSError as exc:
            raise EnterpriseSeedAdmissionError("SOURCE_DIRECTORY_ROOT_INVALID", str(selected_root)) from exc
        if not resolved_root.is_dir():
            raise EnterpriseSeedAdmissionError("SOURCE_DIRECTORY_ROOT_NOT_DIRECTORY", str(resolved_root))
        normalized: dict[str, str] = {}
        for locator, relative in locator_assets.items():
            relative_path = Path(relative)
            if (
                not locator
                or not relative
                or relative_path.is_absolute()
                or ".." in relative_path.parts
                or relative_path.as_posix() in {".", ""}
            ):
                raise EnterpriseSeedAdmissionError(
                    "SOURCE_DIRECTORY_MAPPING_INVALID", f"{locator}:{relative}"
                )
            normalized[locator] = relative_path.as_posix()
        if len(normalized) != len(locator_assets):
            raise EnterpriseSeedAdmissionError("SOURCE_DIRECTORY_LOCATOR_DUPLICATE")
        self._root = resolved_root
        self._locator_assets = MappingProxyType(normalized)

    def resolve(self, locator: str) -> ResolvedSeedSource:
        try:
            relative = self._locator_assets[locator]
        except KeyError as exc:
            raise EnterpriseSeedAdmissionError("SOURCE_LOCATOR_NOT_ALLOWLISTED", locator) from exc
        unresolved = self._root / relative
        cursor = self._root
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise EnterpriseSeedAdmissionError("SOURCE_DIRECTORY_SYMLINK_FORBIDDEN", relative)
        try:
            selected = unresolved.resolve(strict=True)
            selected.relative_to(self._root)
        except (OSError, ValueError) as exc:
            raise EnterpriseSeedAdmissionError("SOURCE_DIRECTORY_PATH_INVALID", relative) from exc
        if not selected.is_file():
            raise EnterpriseSeedAdmissionError("SOURCE_DIRECTORY_ASSET_NOT_FILE", relative)
        try:
            with selected.open("rb") as handle:
                raw = handle.read(MAX_SOURCE_BYTES + 1)
        except OSError as exc:
            raise EnterpriseSeedAdmissionError("SOURCE_ASSET_READ_FAILED", relative) from exc
        return ResolvedSeedSource(raw=raw, logical_asset_ref=f"directory://{relative}")


def expected_raw_digest(locator: str) -> str:
    try:
        return _EXPECTED_RAW_DIGESTS[locator]
    except KeyError as exc:
        raise EnterpriseSeedAdmissionError("SOURCE_LOCATOR_NOT_ALLOWLISTED", locator) from exc


def component_declaration_digest(
    *,
    component_kind: SeedComponentKind,
    source_root_id: str,
    revision: str,
    media_type: str,
    locator: str,
) -> str:
    """Compute the declared one-root component digest from frozen source metadata."""

    return sha256_digest(
        {
            "schema_version": "orgrebase.enterprise-seed-component-admission.v1",
            "component_kind": component_kind.value,
            "ordered_source_roots": [
                {
                    "id": source_root_id,
                    "revision": revision,
                    "media_type": media_type,
                    "observed_digest": expected_raw_digest(locator),
                }
            ],
        }
    )


def component_declaration_digest_for_observed_root(
    *,
    component_kind: SeedComponentKind,
    source_root_id: str,
    revision: str,
    media_type: str,
    observed_digest: str,
) -> str:
    """Compute a component declaration from an already observed exact file digest.

    Unlike :func:`component_declaration_digest`, this helper does not consult the
    packaged fixture allowlist.  It is the authoring seam for a directory-backed
    Enterprise Quote Pack; runtime admission still rereads the bytes and verifies
    the declared digest before accepting the component.
    """

    return sha256_digest(
        {
            "schema_version": "orgrebase.enterprise-seed-component-admission.v1",
            "component_kind": component_kind.value,
            "ordered_source_roots": [
                {
                    "id": source_root_id,
                    "revision": revision,
                    "media_type": media_type,
                    "observed_digest": observed_digest,
                }
            ],
        }
    )


class EnterpriseSeedComponentRoot(ContentAddressedModel):
    schema_version: Literal["orgrebase.enterprise-seed-component-root.v1"] = COMPONENT_ROOT_SCHEMA_VERSION
    profile_ref: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    component_kind: SeedComponentKind
    revision: str = Field(min_length=1)
    runtime_binding_mode: RuntimeBindingMode
    projection: dict[str, Any]
    projection_digest: str

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if not self.projection:
            raise ValueError("SOURCE_COMPONENT_PROJECTION_EMPTY")
        if self.projection_digest != sha256_digest(self.projection):
            raise ValueError("SOURCE_COMPONENT_PROJECTION_DIGEST_MISMATCH")
        return self


class SourceRootObservation(ContentAddressedModel):
    source_root_ref: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    logical_asset_ref: str = Field(min_length=1)
    media_type: Literal["application/vnd.orgrebase.enterprise-seed-component+json"] = SOURCE_MEDIA_TYPE
    declared_digest: str
    observed_digest: str
    byte_length: int = Field(gt=0, le=MAX_SOURCE_BYTES)
    component_kind: SeedComponentKind
    projection_digest: str
    verdict: Literal["ADMITTED"] = "ADMITTED"
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if self.declared_digest != self.observed_digest:
            raise ValueError("SOURCE_ROOT_OBSERVED_DIGEST_MISMATCH")
        if not self.reason_codes or len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("SOURCE_ROOT_REASON_CODES_INVALID")
        return self


class ComponentAdmission(ContentAddressedModel):
    component_kind: SeedComponentKind
    source_root_refs: tuple[str, ...]
    declared_component_digest: str
    computed_component_digest: str
    admitted_projection_digest: str
    verdict: Literal["ADMITTED"] = "ADMITTED"
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        if not self.source_root_refs or len(self.source_root_refs) != len(set(self.source_root_refs)):
            raise ValueError("SOURCE_COMPONENT_ROOT_REFS_INVALID")
        if self.declared_component_digest != self.computed_component_digest:
            raise ValueError("SOURCE_COMPONENT_DECLARED_DIGEST_MISMATCH")
        if not self.reason_codes:
            raise ValueError("SOURCE_COMPONENT_REASON_CODES_MISSING")
        return self


class ProjectionDigestBinding(ContentAddressedModel):
    component_kind: SeedComponentKind
    projection_digest: str


class EnterpriseSeedSourceAdmissionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.enterprise-seed-source-admission-receipt.v1"] = (
        SOURCE_ADMISSION_SCHEMA_VERSION
    )
    profile_ref: str = Field(min_length=1)
    profile_digest: str
    admission_policy_ref: Literal["policy:orgrebase-enterprise-seed-source-admission@v1"] = (
        SOURCE_ADMISSION_POLICY_REF
    )
    authority_assurance: Literal["DECLARED_NOT_AUTHENTICATED"] = "DECLARED_NOT_AUTHENTICATED"
    verdict: Literal["ADMITTED"] = "ADMITTED"
    root_observations: tuple[SourceRootObservation, ...]
    component_admissions: tuple[ComponentAdmission, ...]
    admitted_projection_bindings: tuple[ProjectionDigestBinding, ...]
    unresolved_refs: tuple[str, ...] = ()
    source_projection_digest: str
    component_projection_digest: str
    profile_projection_digest: str
    canonical_target_writes: Literal[0] = 0
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        expected_kinds = tuple(SeedComponentKind)
        root_kinds = tuple(item.component_kind for item in self.root_observations)
        component_kinds = tuple(item.component_kind for item in self.component_admissions)
        binding_kinds = tuple(item.component_kind for item in self.admitted_projection_bindings)
        if root_kinds != expected_kinds or component_kinds != expected_kinds:
            raise ValueError("SOURCE_ADMISSION_COMPONENT_ORDER_INVALID")
        if binding_kinds != expected_kinds:
            raise ValueError("SOURCE_ADMISSION_PROJECTION_BINDINGS_INVALID")
        if self.unresolved_refs:
            raise ValueError("SOURCE_ADMISSION_UNRESOLVED_REFS_PRESENT")
        source_digest = _source_projection_digest(self.root_observations)
        component_digest = _component_projection_digest(self.component_admissions)
        profile_digest = _profile_projection_digest(
            profile_digest=self.profile_digest,
            source_projection_digest=source_digest,
            component_projection_digest=component_digest,
            bindings=self.admitted_projection_bindings,
        )
        if self.source_projection_digest != source_digest:
            raise ValueError("SOURCE_ADMISSION_SOURCE_PROJECTION_DIGEST_MISMATCH")
        if self.component_projection_digest != component_digest:
            raise ValueError("SOURCE_ADMISSION_COMPONENT_PROJECTION_DIGEST_MISMATCH")
        if self.profile_projection_digest != profile_digest:
            raise ValueError("SOURCE_ADMISSION_PROFILE_PROJECTION_DIGEST_MISMATCH")
        if not self.limitations:
            raise ValueError("SOURCE_ADMISSION_LIMITATIONS_MISSING")
        return self


class RuntimeProjectionObservation(ContentAddressedModel):
    component_kind: SeedComponentKind
    admitted_projection_digest: str
    runtime_projection_digest: str
    status: Literal["MATCH"] = "MATCH"

    @model_validator(mode="after")
    def validate_match(self) -> Self:
        if self.admitted_projection_digest != self.runtime_projection_digest:
            raise ValueError("RUNTIME_PROJECTION_DIGEST_MISMATCH")
        return self


class EnterpriseSeedRuntimeProjectionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.enterprise-seed-runtime-projection-receipt.v1"] = (
        RUNTIME_PROJECTION_SCHEMA_VERSION
    )
    profile_ref: str = Field(min_length=1)
    profile_digest: str
    source_admission_receipt_digest: str
    observations: tuple[RuntimeProjectionObservation, ...]
    runtime_projection_digest: str
    verdict: Literal["MATCH"] = "MATCH"
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_runtime_receipt(self) -> Self:
        if tuple(item.component_kind for item in self.observations) != tuple(SeedComponentKind):
            raise ValueError("RUNTIME_PROJECTION_COMPONENT_ORDER_INVALID")
        expected = sha256_digest(
            [
                {
                    "component_kind": item.component_kind.value,
                    "runtime_projection_digest": item.runtime_projection_digest,
                }
                for item in self.observations
            ]
        )
        if self.runtime_projection_digest != expected:
            raise ValueError("RUNTIME_PROJECTION_AGGREGATE_DIGEST_MISMATCH")
        return self


VERACIER_SC008_CHANGED_FIELD_ALLOWLIST: tuple[str, ...] = (
    "change_family[0].base_version",
    "change_family[0].change_id",
    "change_family[0].idempotency_key",
    "change_family[0].object_id",
    "change_family[0].proposed_version",
    "change_family[0].revision",
    "components[0].declared_digest",
    "components[1].declared_digest",
    "components[2].declared_digest",
    "components[3].declared_digest",
    "components[4].declared_digest",
    "default_task.idempotency_key",
    "default_task.input_values[0].value",
    "default_task.input_values[1]",
    "revision",
    "source_roots[0].declared_digest",
    "source_roots[0].locator",
    "source_roots[0].revision",
    "source_roots[1].declared_digest",
    "source_roots[1].locator",
    "source_roots[1].revision",
    "source_roots[2].declared_digest",
    "source_roots[2].locator",
    "source_roots[2].revision",
    "source_roots[3].declared_digest",
    "source_roots[3].locator",
    "source_roots[3].revision",
    "source_roots[4].declared_digest",
    "source_roots[4].locator",
    "source_roots[4].revision",
)


class EnterpriseSeedProfileMigrationReceipt(ContentAddressedModel):
    """Exact, zero-write admission of the historical r1 to SC-008 r2 migration."""

    schema_version: Literal["orgrebase.enterprise-seed-profile-migration-receipt.v1"] = (
        PROFILE_MIGRATION_SCHEMA_VERSION
    )
    migration_ref: Literal["migration:veracier-supplier-shadow:r1-to-r2"] = (
        "migration:veracier-supplier-shadow:r1-to-r2"
    )
    policy_ref: Literal["policy:veracier-sc008-root-identity-migration@v1"] = (
        VERACIER_SC008_MIGRATION_POLICY_REF
    )
    reason: Literal["ROOT_IDENTITY_ALIGNMENT"] = "ROOT_IDENTITY_ALIGNMENT"
    predecessor_profile_ref: Literal["profile:veracier-supplier-shadow@r1"]
    predecessor_profile_digest: Literal[
        "sha256:4f60cf1b06451778cfec0e624a57fc5f9d133307a12e4af22dec9d85b94020a6"
    ]
    successor_profile_ref: Literal["profile:veracier-supplier-shadow@r2"]
    successor_profile_digest: Literal[
        "sha256:82af1e95b91b42fbc007700fd6c4b69d3a22f15e90385ab739fe80da47dac4d8"
    ]
    changed_fields: tuple[str, ...]
    changed_field_allowlist_digest: Literal[
        "sha256:17683c4958fbe04e92253d2c210b962144e69b40c9c99ab05438d8adfa36bda2"
    ]
    historical_subject_ref: Literal["supplier:atlas"] = "supplier:atlas"
    successor_subject_ref: Literal["alternative:acieries-savoie"] = (
        "alternative:acieries-savoie"
    )
    demand_ref: Literal["demand:SC-008"] = "demand:SC-008"
    change_ref: Literal["change:SC-008"] = "change:SC-008"
    before_status: Literal["qualification_in_progress"] = "qualification_in_progress"
    after_status: Literal["qualified"] = "qualified"
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_migration(self) -> Self:
        if self.predecessor_profile_digest == self.successor_profile_digest:
            raise ValueError("SOURCE_PROFILE_MIGRATION_DIGESTS_NOT_DISTINCT")
        if self.changed_fields != VERACIER_SC008_CHANGED_FIELD_ALLOWLIST:
            raise ValueError("SOURCE_PROFILE_MIGRATION_CHANGED_FIELDS_INVALID")
        expected_allowlist_digest = sha256_digest(list(VERACIER_SC008_CHANGED_FIELD_ALLOWLIST))
        if self.changed_field_allowlist_digest != expected_allowlist_digest:
            raise ValueError("SOURCE_PROFILE_MIGRATION_ALLOWLIST_DIGEST_MISMATCH")
        return self


def _raw_sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnterpriseSeedAdmissionError("SOURCE_JSON_DUPLICATE_KEY", key)
        result[key] = value
    return result


def _load_component_root_once(
    locator: str,
    *,
    resolver: SeedSourceResolver | None = None,
) -> tuple[bytes, str, EnterpriseSeedComponentRoot]:
    resolved = (resolver or PackagedSeedSourceResolver()).resolve(locator)
    raw = resolved.raw
    relative = resolved.logical_asset_ref
    if len(raw) > MAX_SOURCE_BYTES:
        raise EnterpriseSeedAdmissionError("SOURCE_ASSET_SIZE_LIMIT_EXCEEDED", relative)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EnterpriseSeedAdmissionError("SOURCE_ASSET_NOT_UTF8", relative) from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except EnterpriseSeedAdmissionError:
        raise
    except json.JSONDecodeError as exc:
        raise EnterpriseSeedAdmissionError("SOURCE_ASSET_JSON_INVALID", relative) from exc
    if not isinstance(value, dict):
        raise EnterpriseSeedAdmissionError("SOURCE_ASSET_ROOT_NOT_OBJECT", relative)
    try:
        root = EnterpriseSeedComponentRoot.model_validate(value)
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("SOURCE_COMPONENT_SCHEMA_INVALID", str(exc)) from exc
    return raw, relative, root


def _changed_profile_fields(
    predecessor: EnterpriseSeedProfile,
    successor: EnterpriseSeedProfile,
) -> tuple[str, ...]:
    changed: list[str] = []

    def visit(before: object, after: object, path: str = "") -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                child = f"{path}.{key}" if path else key
                if key not in before or key not in after:
                    changed.append(child)
                else:
                    visit(before[key], after[key], child)
            return
        if isinstance(before, list) and isinstance(after, list):
            for index in range(max(len(before), len(after))):
                child = f"{path}[{index}]"
                if index >= len(before) or index >= len(after):
                    changed.append(child)
                else:
                    visit(before[index], after[index], child)
            return
        if before != after:
            changed.append(path)

    visit(
        predecessor.model_dump(mode="json", exclude={"digest"}),
        successor.model_dump(mode="json", exclude={"digest"}),
    )
    return tuple(changed)


def admit_veracier_sc008_profile_migration(
    predecessor: EnterpriseSeedProfile,
    successor: EnterpriseSeedProfile,
) -> EnterpriseSeedProfileMigrationReceipt:
    """Admit only the shipped, explicitly allowlisted r1-to-r2 root repair."""

    try:
        predecessor = EnterpriseSeedProfile.model_validate(predecessor.model_dump(mode="json"))
        successor = EnterpriseSeedProfile.model_validate(successor.model_dump(mode="json"))
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("SOURCE_PROFILE_MIGRATION_PROFILE_INVALID", str(exc)) from exc

    from orgrebase.workspace.reference_profiles import (
        VERACIER_SC008_AFTER_STATUS,
        VERACIER_SC008_BEFORE_STATUS,
        VERACIER_SC008_CHANGE_REF,
        VERACIER_SC008_DEMAND_REF,
        VERACIER_SC008_SUBJECT_REF,
        supplier_sc008_source_aligned_profile,
        supplier_shadow_intake_profile,
    )

    expected_predecessor = supplier_shadow_intake_profile()
    expected_successor = supplier_sc008_source_aligned_profile()
    if predecessor != expected_predecessor:
        raise EnterpriseSeedAdmissionError("SOURCE_PROFILE_MIGRATION_PREDECESSOR_MISMATCH")
    if successor != expected_successor:
        raise EnterpriseSeedAdmissionError("SOURCE_PROFILE_MIGRATION_SUCCESSOR_MISMATCH")
    changed_fields = _changed_profile_fields(predecessor, successor)
    if changed_fields != VERACIER_SC008_CHANGED_FIELD_ALLOWLIST:
        raise EnterpriseSeedAdmissionError("SOURCE_PROFILE_MIGRATION_CHANGED_FIELDS_INVALID")
    inputs = {item.key: item.value for item in successor.default_task.input_values}
    change = successor.change_family[0]
    semantic_checks = {
        "SOURCE_PROFILE_MIGRATION_SUBJECT_MISMATCH": (
            inputs == {
                "supplier_id": VERACIER_SC008_SUBJECT_REF,
                "demand_ref": VERACIER_SC008_DEMAND_REF,
            }
        ),
        "SOURCE_PROFILE_MIGRATION_CHANGE_REF_MISMATCH": (
            len(successor.change_family) == 1 and change.change_id == VERACIER_SC008_CHANGE_REF
        ),
        "SOURCE_PROFILE_MIGRATION_CHANGE_SUBJECT_MISMATCH": (
            change.object_id == f"{VERACIER_SC008_SUBJECT_REF}.status"
        ),
        "SOURCE_PROFILE_MIGRATION_STATUS_TRANSITION_MISMATCH": (
            change.base_version == VERACIER_SC008_BEFORE_STATUS
            and change.proposed_version == VERACIER_SC008_AFTER_STATUS
        ),
    }
    failed = next((code for code, passed in semantic_checks.items() if not passed), None)
    if failed:
        raise EnterpriseSeedAdmissionError(failed)
    return EnterpriseSeedProfileMigrationReceipt(
        predecessor_profile_ref=predecessor.ref,
        predecessor_profile_digest=predecessor.digest,
        successor_profile_ref=successor.ref,
        successor_profile_digest=successor.digest,
        changed_fields=changed_fields,
        changed_field_allowlist_digest=sha256_digest(list(changed_fields)),
    )


def _validate_veracier_sc008_source_closure(
    profile: EnterpriseSeedProfile,
    roots: Mapping[SeedComponentKind, EnterpriseSeedComponentRoot],
) -> None:
    """Close the r2 Profile task/change roots against the five exact components."""

    if profile.ref != "profile:veracier-supplier-shadow@r2":
        return
    from orgrebase.workspace.reference_profiles import (
        VERACIER_SC008_AFTER_STATUS,
        VERACIER_SC008_BEFORE_STATUS,
        VERACIER_SC008_CHANGE_REF,
        VERACIER_SC008_DEMAND_REF,
        VERACIER_SC008_SUBJECT_REF,
    )

    inputs = {item.key: item.value for item in profile.default_task.input_values}
    change = profile.change_family[0]
    domain = roots[SeedComponentKind.DOMAIN].projection
    knowledge = roots[SeedComponentKind.KNOWLEDGE].projection
    authority = roots[SeedComponentKind.AUTHORITY].projection
    capability = roots[SeedComponentKind.CAPABILITY].projection
    dependency = roots[SeedComponentKind.DEPENDENCY].projection
    expected_record = {
        "supplier_id": VERACIER_SC008_SUBJECT_REF,
        "declared_status": VERACIER_SC008_BEFORE_STATUS,
        "proposed_status": VERACIER_SC008_AFTER_STATUS,
        "source_version": "SC-008",
    }
    checks = {
        "SOURCE_SC008_PROFILE_TASK_ROOT_MISMATCH": inputs
        == {
            "supplier_id": VERACIER_SC008_SUBJECT_REF,
            "demand_ref": VERACIER_SC008_DEMAND_REF,
        },
        "SOURCE_SC008_PROFILE_CHANGE_ROOT_MISMATCH": (
            len(profile.change_family) == 1
            and change.change_id == VERACIER_SC008_CHANGE_REF
            and change.object_id == f"{VERACIER_SC008_SUBJECT_REF}.status"
            and change.base_version == VERACIER_SC008_BEFORE_STATUS
            and change.proposed_version == VERACIER_SC008_AFTER_STATUS
        ),
        "SOURCE_SC008_DOMAIN_ROOT_MISMATCH": (
            domain.get("primary_entity") == VERACIER_SC008_SUBJECT_REF
            and domain.get("demand_ref") == VERACIER_SC008_DEMAND_REF
            and domain.get("change_ref") == VERACIER_SC008_CHANGE_REF
        ),
        "SOURCE_SC008_KNOWLEDGE_ROOT_MISMATCH": (
            knowledge.get("supplier_records") == [expected_record]
            and knowledge.get("demand_ref") == VERACIER_SC008_DEMAND_REF
            and knowledge.get("change_ref") == VERACIER_SC008_CHANGE_REF
        ),
        "SOURCE_SC008_AUTHORITY_ROOT_MISMATCH": (
            authority.get("demand_ref") == VERACIER_SC008_DEMAND_REF
        ),
        "SOURCE_SC008_CAPABILITY_ROOT_MISMATCH": (
            capability.get("demand_ref") == VERACIER_SC008_DEMAND_REF
        ),
        "SOURCE_SC008_DEPENDENCY_ROOT_MISMATCH": (
            dependency.get("declared_dependencies")
            == [f"{VERACIER_SC008_SUBJECT_REF}.status"]
            and dependency.get("demand_ref") == VERACIER_SC008_DEMAND_REF
            and dependency.get("change_ref") == VERACIER_SC008_CHANGE_REF
            and dependency.get("status_transition")
            == {
                "before": VERACIER_SC008_BEFORE_STATUS,
                "after": VERACIER_SC008_AFTER_STATUS,
            }
        ),
    }
    failed = next((code for code, passed in checks.items() if not passed), None)
    if failed:
        raise EnterpriseSeedAdmissionError(failed)


def _component_digest_payload(
    component_kind: SeedComponentKind,
    observations: tuple[SourceRootObservation, ...],
) -> dict[str, object]:
    return {
        "schema_version": "orgrebase.enterprise-seed-component-admission.v1",
        "component_kind": component_kind.value,
        "ordered_source_roots": [
            {
                "id": item.source_root_ref.rsplit("@", 1)[0],
                "revision": item.source_root_ref.rsplit("@", 1)[1],
                "media_type": item.media_type,
                "observed_digest": item.observed_digest,
            }
            for item in observations
        ],
    }


def _source_projection_digest(
    observations: tuple[SourceRootObservation, ...],
) -> str:
    return sha256_digest(
        [
            {
                "source_root_ref": item.source_root_ref,
                "observed_digest": item.observed_digest,
                "projection_digest": item.projection_digest,
            }
            for item in observations
        ]
    )


def _component_projection_digest(
    admissions: tuple[ComponentAdmission, ...],
) -> str:
    return sha256_digest(
        [
            {
                "component_kind": item.component_kind.value,
                "component_admission_digest": item.digest,
            }
            for item in admissions
        ]
    )


def _profile_projection_digest(
    *,
    profile_digest: str,
    source_projection_digest: str,
    component_projection_digest: str,
    bindings: tuple[ProjectionDigestBinding, ...],
) -> str:
    return sha256_digest(
        {
            "profile_digest": profile_digest,
            "source_projection_digest": source_projection_digest,
            "component_projection_digest": component_projection_digest,
            "admitted_projection_digests": [
                {
                    "component_kind": item.component_kind.value,
                    "projection_digest": item.projection_digest,
                }
                for item in bindings
            ],
        }
    )


def admit_enterprise_seed_sources(
    profile: EnterpriseSeedProfile,
    *,
    resolver: SeedSourceResolver | None = None,
) -> EnterpriseSeedSourceAdmissionReceipt:
    """Resolve and admit all five exact component roots without canonical writes."""

    # Rebuild at the trust boundary so model_copy cannot preserve a stale digest.
    try:
        profile = EnterpriseSeedProfile.model_validate(profile.model_dump(mode="json"))
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("PROFILE_VALIDATION_FAILED", str(exc)) from exc
    if profile.ref == "profile:veracier-supplier-shadow@r2":
        from orgrebase.workspace.reference_profiles import supplier_shadow_intake_profile

        admit_veracier_sc008_profile_migration(supplier_shadow_intake_profile(), profile)
    if len(profile.source_roots) != len(SeedComponentKind):
        raise EnterpriseSeedAdmissionError("SOURCE_ROOT_SET_NOT_EXACT")
    root_by_id = {item.id: item for item in profile.source_roots}
    expected_mode = (
        RuntimeBindingMode.EXACT_REFERENCE_PROJECTION
        if profile.runtime_compatibility.mode == RuntimeCompatibilityMode.REFERENCE_HANDLER
        else RuntimeBindingMode.INTAKE_ONLY
    )
    observation_by_id: dict[str, SourceRootObservation] = {}
    projection_by_kind: dict[SeedComponentKind, str] = {}
    parsed_by_kind: dict[SeedComponentKind, EnterpriseSeedComponentRoot] = {}
    selected_resolver = resolver or PackagedSeedSourceResolver()
    for declared in profile.source_roots:
        raw, relative, parsed = _load_component_root_once(
            declared.locator,
            resolver=selected_resolver,
        )
        observed_digest = _raw_sha256(raw)
        expected_kind = next(
            (component.kind for component in profile.components if declared.id in component.source_root_refs),
            None,
        )
        if expected_kind is None:
            raise EnterpriseSeedAdmissionError("SOURCE_ROOT_NOT_BOUND_TO_COMPONENT", declared.id)
        checks = {
            "SOURCE_ROOT_DECLARED_DIGEST_MISMATCH": (observed_digest == declared.declared_digest),
            "SOURCE_ROOT_MEDIA_TYPE_MISMATCH": declared.media_type == SOURCE_MEDIA_TYPE,
            "SOURCE_COMPONENT_PROFILE_MISMATCH": parsed.profile_ref == profile.ref,
            "SOURCE_COMPONENT_ORGANIZATION_MISMATCH": (parsed.organization_id == profile.organization_id),
            "SOURCE_COMPONENT_KIND_MISMATCH": parsed.component_kind == expected_kind,
            "SOURCE_COMPONENT_REVISION_MISMATCH": parsed.revision == declared.revision,
            "SOURCE_COMPONENT_BINDING_MODE_MISMATCH": (parsed.runtime_binding_mode == expected_mode),
        }
        failed = next((code for code, passed in checks.items() if not passed), None)
        if failed:
            raise EnterpriseSeedAdmissionError(failed, declared.id)
        if expected_kind in projection_by_kind:
            raise EnterpriseSeedAdmissionError(
                "SOURCE_COMPONENT_MULTIPLE_ROOTS_UNSUPPORTED", expected_kind.value
            )
        observation = SourceRootObservation(
            source_root_ref=f"{declared.id}@{declared.revision}",
            locator=declared.locator,
            logical_asset_ref=relative,
            declared_digest=declared.declared_digest,
            observed_digest=observed_digest,
            byte_length=len(raw),
            component_kind=expected_kind,
            projection_digest=parsed.projection_digest,
            reason_codes=(
                "EXACT_LOCATOR_ALLOWLIST",
                "RAW_BYTES_SHA256_MATCH",
                "STRICT_UTF8_JSON_SCHEMA_MATCH",
            ),
        )
        observation_by_id[declared.id] = observation
        projection_by_kind[expected_kind] = parsed.projection_digest
        parsed_by_kind[expected_kind] = parsed

    if set(observation_by_id) != set(root_by_id):
        raise EnterpriseSeedAdmissionError("SOURCE_ROOT_COVERAGE_INCOMPLETE")
    _validate_veracier_sc008_source_closure(profile, parsed_by_kind)
    ordered_observations = tuple(
        next(observation for observation in observation_by_id.values() if observation.component_kind == kind)
        for kind in SeedComponentKind
    )
    component_admissions: list[ComponentAdmission] = []
    for kind in SeedComponentKind:
        component = next(item for item in profile.components if item.kind == kind)
        if len(component.source_root_refs) != 1:
            raise EnterpriseSeedAdmissionError("SOURCE_COMPONENT_EXACTLY_ONE_ROOT_REQUIRED", kind.value)
        selected_observations = tuple(
            observation_by_id[source_ref] for source_ref in component.source_root_refs
        )
        computed = sha256_digest(_component_digest_payload(kind, selected_observations))
        if component.declared_digest != computed:
            raise EnterpriseSeedAdmissionError("SOURCE_COMPONENT_DECLARED_DIGEST_MISMATCH", kind.value)
        component_admissions.append(
            ComponentAdmission(
                component_kind=kind,
                source_root_refs=component.source_root_refs,
                declared_component_digest=computed,
                computed_component_digest=computed,
                admitted_projection_digest=projection_by_kind[kind],
                reason_codes=("ALL_BOUND_ROOT_BYTES_ADMITTED", "COMPONENT_DIGEST_MATCH"),
            )
        )
    admitted_components = tuple(component_admissions)
    bindings = tuple(
        ProjectionDigestBinding(
            component_kind=kind,
            projection_digest=projection_by_kind[kind],
        )
        for kind in SeedComponentKind
    )
    source_digest = _source_projection_digest(ordered_observations)
    component_digest = _component_projection_digest(admitted_components)
    return EnterpriseSeedSourceAdmissionReceipt(
        profile_ref=profile.ref,
        profile_digest=profile.digest,
        root_observations=ordered_observations,
        component_admissions=admitted_components,
        admitted_projection_bindings=bindings,
        source_projection_digest=source_digest,
        component_projection_digest=component_digest,
        profile_projection_digest=_profile_projection_digest(
            profile_digest=profile.digest,
            source_projection_digest=source_digest,
            component_projection_digest=component_digest,
            bindings=bindings,
        ),
        limitations=(
            (
                "PACKAGED_EXACT_LOCATORS_ONLY"
                if isinstance(selected_resolver, PackagedSeedSourceResolver)
                else "INJECTED_DIRECTORY_EXACT_LOCATORS_ONLY"
            ),
            "SOURCE_AUTHORITY_DECLARED_NOT_AUTHENTICATED",
            "NO_NETWORK_OR_EXTERNAL_CONNECTOR_RESOLUTION",
        ),
    )


def parse_enterprise_seed_source_admission_receipt(
    value: EnterpriseSeedSourceAdmissionReceipt | dict[str, object],
) -> EnterpriseSeedSourceAdmissionReceipt:
    try:
        payload = (
            value.model_dump(mode="json")
            if isinstance(value, EnterpriseSeedSourceAdmissionReceipt)
            else value
        )
        return EnterpriseSeedSourceAdmissionReceipt.model_validate(payload)
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("SOURCE_ADMISSION_RECEIPT_INVALID", str(exc)) from exc


def northstar_runtime_projections(
    profile: EnterpriseSeedProfile,
) -> dict[SeedComponentKind, dict[str, object]]:
    """Compute the five exact projections consumed by the reference runtime."""

    from orgrebase.workspace.domain_agents import default_source_values
    from orgrebase.workspace.graph import workspace_seed_universe
    from orgrebase.workspace.templates import TemplateRegistry, default_capability_cards

    source_values = default_source_values()
    cards = default_capability_cards()
    universe = workspace_seed_universe()
    domain_projection: dict[str, object] = {
        "organization_id": profile.organization_id,
        "scenario_id": profile.scenario_id,
        "default_task": profile.default_task.model_dump(mode="json"),
        "change_family": [item.model_dump(mode="json") for item in profile.change_family],
        "domains": sorted({item.domain_id for item in source_values.values()}),
    }
    knowledge_projection: dict[str, object] = {
        "source_values": [
            {
                "slot_id": item.slot_id,
                "object_ref": item.object_ref,
                "domain_id": item.domain_id,
                "value": item.value,
                "semantic_kind": item.semantic_kind.value,
                "authority_ref": item.authority_ref,
                "source_id": item.source_id,
                "source_version": item.source_version,
                "sensitivity": item.sensitivity,
                "raw_private_value": item.raw_private_value,
            }
            for _, item in sorted(source_values.items())
        ]
    }
    authority_projection: dict[str, object] = {
        "governance": profile.governance.model_dump(mode="json"),
        "source_authority_refs": sorted({item.authority_ref for item in source_values.values()}),
        "capability_authority_refs": sorted(
            {authority for card in cards for authority in card.authority_refs}
        ),
        "admission_controller_contract": ("orgrebase.workspace.admission.AdmissionController@v1"),
    }
    capability_projection: dict[str, object] = {
        "templates": [{"ref": item.ref, "digest": item.digest} for item in TemplateRegistry().templates
                      if item.ref in {"template:enterprise_quote@v1", "template:public_launch_summary@v1",
                                      "template:residency_faq@v1", "template:discount_exception_memo@v1"}],
        "capability_cards": [{"ref": item.ref, "digest": item.digest} for item in cards],
        "runtime_components": {
            "planner": "orgrebase.workspace.planner.CoalitionPlanner@v1",
            "context_compiler": "orgrebase.workspace.context.TaskContextCompiler@v1",
            "renderer": "orgrebase.workspace.execution.QuoteRenderer@1.0.0",
            "dependency_compiler": ("orgrebase.workspace.execution.RuntimeDependencyCompiler@v1"),
        },
    }
    dependency_projection: dict[str, object] = {
        "universe_ref": universe.id,
        "universe_digest": universe.digest,
        "object_bindings": [{"ref": item.ref, "digest": item.digest} for item in universe.current_objects],
        "edge_bindings": [{"id": item.id, "digest": item.digest} for item in universe.edges],
        "runtime_manifest_bindings": [
            {"ref": item.ref, "digest": item.digest} for item in universe.runtime_manifests
        ],
        "imported_manifest_bindings": [
            {"id": item.id, "digest": item.digest} for item in universe.imported_manifests
        ],
        "target_ids": list(universe.target_ids),
        "source_refs": list(universe.source_refs),
        "completeness_basis": [item.value for item in universe.completeness_basis],
    }
    return {
        SeedComponentKind.DOMAIN: domain_projection,
        SeedComponentKind.KNOWLEDGE: knowledge_projection,
        SeedComponentKind.AUTHORITY: authority_projection,
        SeedComponentKind.CAPABILITY: capability_projection,
        SeedComponentKind.DEPENDENCY: dependency_projection,
    }


def verify_runtime_projections(
    profile: EnterpriseSeedProfile,
    source_receipt: EnterpriseSeedSourceAdmissionReceipt,
    runtime_projections: Mapping[SeedComponentKind, Mapping[str, object]],
) -> EnterpriseSeedRuntimeProjectionReceipt:
    """Fail closed unless all five active runtime projections match source bytes."""

    source_receipt = parse_enterprise_seed_source_admission_receipt(source_receipt)
    if source_receipt.profile_ref != profile.ref or source_receipt.profile_digest != profile.digest:
        raise EnterpriseSeedAdmissionError("RUNTIME_SOURCE_RECEIPT_PROFILE_MISMATCH")
    if profile.runtime_compatibility.mode != RuntimeCompatibilityMode.REFERENCE_HANDLER:
        raise EnterpriseSeedAdmissionError("RUNTIME_PROJECTION_INTAKE_ONLY_PROFILE")
    admitted = {
        item.component_kind: item.projection_digest for item in source_receipt.admitted_projection_bindings
    }
    if set(runtime_projections) != set(SeedComponentKind):
        raise EnterpriseSeedAdmissionError("RUNTIME_PROJECTION_COMPONENT_SET_INVALID")
    observations: list[RuntimeProjectionObservation] = []
    for kind in SeedComponentKind:
        runtime_digest = sha256_digest(runtime_projections[kind])
        if admitted[kind] != runtime_digest:
            raise EnterpriseSeedAdmissionError("RUNTIME_PROJECTION_DIGEST_MISMATCH", kind.value)
        observations.append(
            RuntimeProjectionObservation(
                component_kind=kind,
                admitted_projection_digest=admitted[kind],
                runtime_projection_digest=runtime_digest,
            )
        )
    observation_tuple = tuple(observations)
    return EnterpriseSeedRuntimeProjectionReceipt(
        profile_ref=profile.ref,
        profile_digest=profile.digest,
        source_admission_receipt_digest=source_receipt.digest,
        observations=observation_tuple,
        runtime_projection_digest=sha256_digest(
            [
                {
                    "component_kind": item.component_kind.value,
                    "runtime_projection_digest": item.runtime_projection_digest,
                }
                for item in observation_tuple
            ]
        ),
    )


def verify_reference_runtime_projections(
    profile: EnterpriseSeedProfile,
    source_receipt: EnterpriseSeedSourceAdmissionReceipt,
) -> EnterpriseSeedRuntimeProjectionReceipt:
    """Verify the shipped Northstar runtime without weakening legacy evidence."""

    return verify_runtime_projections(
        profile,
        source_receipt,
        northstar_runtime_projections(profile),
    )


def parse_enterprise_seed_runtime_projection_receipt(
    value: EnterpriseSeedRuntimeProjectionReceipt | dict[str, object],
) -> EnterpriseSeedRuntimeProjectionReceipt:
    try:
        payload = (
            value.model_dump(mode="json")
            if isinstance(value, EnterpriseSeedRuntimeProjectionReceipt)
            else value
        )
        return EnterpriseSeedRuntimeProjectionReceipt.model_validate(payload)
    except ValidationError as exc:
        raise EnterpriseSeedAdmissionError("RUNTIME_PROJECTION_RECEIPT_INVALID", str(exc)) from exc
