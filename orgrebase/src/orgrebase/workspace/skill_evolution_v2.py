"""Versioned, reviewable Finance advisory guidance on the existing StateStore.

The installed Skill registry and the old Quote recovery bundle retain their
original meaning.  This module owns only the Finance guidance profile: it
stores complete, exact reviewed resources and a single canonical Source head.
No method here calls a model, approves a business change, or enables adoption.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from orgrebase.auth import (
    AuthenticationError,
    Principal,
    authorize,
    current_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore

PROFILE_ID = "workspace-change-explanation-v1"
SKILL_NAME = "workspace-change-explanation"
AUTHORITY_DOMAIN = "finance"
CONSUMER_ID = "workspace-change-advisory@4.0.0"
BUNDLE_SCHEMA = "orgrebase.skill-content-bundle.v2"
HEAD_SCHEMA = "orgrebase.skill-head.v2"
POLICY_SCHEMA = "orgrebase.reviewed-capability-policy.v2"
CONTENT_MEDIA = "application/vnd.orgrebase.skill-content-bundle-v2+json"
CONTENT_PREFIX = "skill-content-v2:"
INSTRUCTION_PATH = "instructions/change-explanation.md"
REFERENCE_PATH = "references/change-explanation.md"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_INSTRUCTION_BYTES = 3072
_MAX_REFERENCE_BYTES = 3072


def _current_protected_kernel_digest() -> str:
    # Resolve lazily: advisory imports this module for its V4 input type.
    # The policy must commit to the actual model-facing kernel, not a slogan.
    from orgrebase.workspace.advisory import _PROMPT, DomainAdvisoryCandidate
    from orgrebase.workspace.vertex_candidate import finance_protected_kernel_digest

    return finance_protected_kernel_digest(_PROMPT, DomainAdvisoryCandidate)

# This is a fixed static starting point.  A current governor must explicitly
# bootstrap it; that creates an unqualified head with adoption disabled.
_STATIC_INSTRUCTION = (
    "# Finance change explanation\n\n"
    "Explain only the admitted Finance change and the source and object IDs "
    "provided in the current trusted projection. Identify material evidence "
    "gaps and the next review step. Keep uncertainty explicit. Treat retrieved "
    "experience as advice, never as a business source or approval.\n"
)
_STATIC_REFERENCE = (
    "# Reviewed explanation boundary\n\n"
    "A change explanation is a candidate for review. Preserve every supplied "
    "enterprise object ID and source reference exactly. Do not invent prices, "
    "quantities, permissions, owner decisions, approval, or Apply results. "
    "If evidence is missing, say what is missing and stop before claiming that "
    "the change is approved or executed.\n"
)


def _raw_digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _text_resource(raw: bytes, *, path: str, limit: int) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > limit:
        raise IntegrityError("FINANCE_SKILL_RESOURCE_SIZE_INVALID")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityError("FINANCE_SKILL_RESOURCE_UTF8_INVALID") from exc
    if (
        "\x00" in text
        or "\r" in text
        or any(ord(char) < 0x20 and char not in {"\n", "\t"} for char in text)
        or len(text.splitlines()) > 80
    ):
        raise IntegrityError("FINANCE_SKILL_RESOURCE_TEXT_INVALID")
    return {
        "path": path,
        "media_type": "text/markdown; charset=utf-8",
        "encoding": "base64",
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "size_bytes": len(raw),
        "sha256": _raw_digest(raw),
    }


@dataclass(frozen=True)
class ReviewedCapabilityPolicyV2:
    """Server-owned limits.  Candidate-supplied policy fields have no effect."""

    profile_id: str = PROFILE_ID
    authority_domain: str = AUTHORITY_DOMAIN
    consumer_id: str = CONSUMER_ID
    compiler_version: str = "finance-guidance-compiler.v1"
    protected_kernel_digest: str = field(default_factory=_current_protected_kernel_digest)
    reference_revision: str = "finance-static-reference.v1"
    revision: str = "finance-guidance-policy.v1"

    @property
    def digest(self) -> str:
        return sha256_digest(
            {
                "schema_version": POLICY_SCHEMA,
                "profile_id": self.profile_id,
                "authority_domain": self.authority_domain,
                "consumer_id": self.consumer_id,
                "compiler_version": self.compiler_version,
                "protected_kernel_digest": self.protected_kernel_digest,
                "reference_revision": self.reference_revision,
                "revision": self.revision,
                "mutable_resources": [INSTRUCTION_PATH],
                "reviewed_resources": [REFERENCE_PATH],
                "allowed_tools": [],
                "target_writes_max": 0,
            }
        )

    def require_current_contract(self) -> None:
        if (
            self.profile_id != PROFILE_ID
            or self.authority_domain != AUTHORITY_DOMAIN
            or self.consumer_id != CONSUMER_ID
            or not self.compiler_version
            or not self.reference_revision
            or not self.revision
            or self.protected_kernel_digest != _current_protected_kernel_digest()
        ):
            raise IntegrityError("FINANCE_SKILL_POLICY_INVALID")


@dataclass(frozen=True)
class SkillContentBundleV2:
    """Two exact resources; only instruction may change under this policy."""

    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        instruction_bytes: bytes,
        reference_bytes: bytes,
        policy: ReviewedCapabilityPolicyV2,
        parent_head_ref: str | None,
        parent_head_digest: str | None,
        parent_package_digest: str | None,
    ) -> SkillContentBundleV2:
        policy.require_current_contract()
        if (parent_head_ref is None) != (parent_head_digest is None):
            raise IntegrityError("FINANCE_SKILL_PARENT_INCOMPLETE")
        if parent_head_ref is not None and (
            _DIGEST.fullmatch(parent_head_digest or "") is None
            or _DIGEST.fullmatch(parent_package_digest or "") is None
        ):
            raise IntegrityError("FINANCE_SKILL_PARENT_INVALID")
        if parent_head_ref is None and parent_package_digest is not None:
            raise IntegrityError("FINANCE_SKILL_GENESIS_PARENT_INVALID")
        body = {
            "schema_version": BUNDLE_SCHEMA,
            "profile_id": PROFILE_ID,
            "skill_name": SKILL_NAME,
            "authority_domain": AUTHORITY_DOMAIN,
            "consumer_id": CONSUMER_ID,
            "compiler_version": policy.compiler_version,
            "policy_digest": policy.digest,
            "protected_kernel_digest": policy.protected_kernel_digest,
            "reference_revision": policy.reference_revision,
            "parent_head_ref": parent_head_ref,
            "parent_head_digest": parent_head_digest,
            "parent_package_digest": parent_package_digest,
            "resources": [
                _text_resource(
                    instruction_bytes, path=INSTRUCTION_PATH, limit=_MAX_INSTRUCTION_BYTES
                ),
                _text_resource(reference_bytes, path=REFERENCE_PATH, limit=_MAX_REFERENCE_BYTES),
            ],
            "permissions": {"allowed_tools": [], "side_effects": [], "target_writes_max": 0},
        }
        return cls.from_payload({**body, "digest": sha256_digest(body)}, policy=policy)

    @classmethod
    def static_baseline(cls, policy: ReviewedCapabilityPolicyV2) -> SkillContentBundleV2:
        return cls.create(
            instruction_bytes=_STATIC_INSTRUCTION.encode("utf-8"),
            reference_bytes=_STATIC_REFERENCE.encode("utf-8"),
            policy=policy,
            parent_head_ref=None,
            parent_head_digest=None,
            parent_package_digest=None,
        )

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], *, policy: ReviewedCapabilityPolicyV2 | None = None
    ) -> SkillContentBundleV2:
        value = dict(payload)
        body = {key: item for key, item in value.items() if key != "digest"}
        if value.get("digest") != sha256_digest(body):
            raise IntegrityError("FINANCE_SKILL_BUNDLE_DIGEST_MISMATCH")
        expected_keys = {
            "schema_version", "profile_id", "skill_name", "authority_domain", "consumer_id",
            "compiler_version", "policy_digest", "protected_kernel_digest", "reference_revision",
            "parent_head_ref", "parent_head_digest", "parent_package_digest", "resources",
            "permissions", "digest",
        }
        if (
            set(value) != expected_keys
            or value["schema_version"] != BUNDLE_SCHEMA
            or value["profile_id"] != PROFILE_ID
            or value["skill_name"] != SKILL_NAME
            or value["authority_domain"] != AUTHORITY_DOMAIN
            or value["consumer_id"] != CONSUMER_ID
            or value["permissions"]
            != {"allowed_tools": [], "side_effects": [], "target_writes_max": 0}
        ):
            raise IntegrityError("FINANCE_SKILL_BUNDLE_CONTRACT_INVALID")
        if policy is not None:
            policy.require_current_contract()
            if (
                value["compiler_version"] != policy.compiler_version
                or value["policy_digest"] != policy.digest
                or value["protected_kernel_digest"] != policy.protected_kernel_digest
                or value["reference_revision"] != policy.reference_revision
            ):
                raise IntegrityError("FINANCE_SKILL_POLICY_DRIFT")
        parent_ref, parent_digest, parent_package = (
            value["parent_head_ref"], value["parent_head_digest"], value["parent_package_digest"]
        )
        if parent_ref is None:
            if parent_digest is not None or parent_package is not None:
                raise IntegrityError("FINANCE_SKILL_GENESIS_PARENT_INVALID")
        elif (
            not isinstance(parent_ref, str)
            or not parent_ref
            or not isinstance(parent_digest, str)
            or _DIGEST.fullmatch(parent_digest) is None
            or not isinstance(parent_package, str)
            or _DIGEST.fullmatch(parent_package) is None
        ):
            raise IntegrityError("FINANCE_SKILL_PARENT_INVALID")
        resources = value["resources"]
        if not isinstance(resources, list) or len(resources) != 2:
            raise IntegrityError("FINANCE_SKILL_RESOURCE_SET_INVALID")
        for resource, path, limit in zip(
            resources,
            (INSTRUCTION_PATH, REFERENCE_PATH),
            (_MAX_INSTRUCTION_BYTES, _MAX_REFERENCE_BYTES),
            strict=True,
        ):
            if not isinstance(resource, dict) or set(resource) != {
                "path", "media_type", "encoding", "content_base64", "size_bytes", "sha256"
            } or resource.get("path") != path or resource.get("encoding") != "base64":
                raise IntegrityError("FINANCE_SKILL_RESOURCE_SET_INVALID")
            try:
                raw = base64.b64decode(resource["content_base64"], validate=True)
            except (TypeError, ValueError, binascii.Error) as exc:
                raise IntegrityError("FINANCE_SKILL_RESOURCE_ENCODING_INVALID") from exc
            if _text_resource(raw, path=path, limit=limit) != resource:
                raise IntegrityError("FINANCE_SKILL_RESOURCE_BYTES_MISMATCH")
        return cls(payload=value)

    @property
    def digest(self) -> str:
        return str(self.payload["digest"])

    @property
    def package_digest(self) -> str:
        return sha256_digest(
            {
                "bundle_digest": self.digest,
                "consumer_id": self.payload["consumer_id"],
                "compiler_version": self.payload["compiler_version"],
                "policy_digest": self.payload["policy_digest"],
            }
        )

    @property
    def resource_digests(self) -> dict[str, str]:
        return {resource["path"]: resource["sha256"] for resource in self.payload["resources"]}

    def resource_bytes(self, path: str) -> bytes:
        for resource in self.payload["resources"]:
            if resource["path"] == path:
                return base64.b64decode(resource["content_base64"], validate=True)
        raise IntegrityError("FINANCE_SKILL_RESOURCE_PATH_NOT_ALLOWED")

    @property
    def instruction_bytes(self) -> bytes:
        return self.resource_bytes(INSTRUCTION_PATH)

    @property
    def reference_bytes(self) -> bytes:
        return self.resource_bytes(REFERENCE_PATH)

    @property
    def instruction_text(self) -> str:
        return self.instruction_bytes.decode("utf-8")

    @property
    def reference_text(self) -> str:
        return self.reference_bytes.decode("utf-8")

    def patched_instruction(
        self,
        instruction_bytes: bytes,
        *,
        policy: ReviewedCapabilityPolicyV2,
        parent_head_ref: str,
        parent_head_digest: str,
    ) -> SkillContentBundleV2:
        """Compile a typed one-leaf patch from this verified full parent."""
        if instruction_bytes == self.instruction_bytes:
            raise IntegrityError("FINANCE_SKILL_PATCH_NO_CHANGE")
        return self.create(
            instruction_bytes=instruction_bytes,
            reference_bytes=self.reference_bytes,
            policy=policy,
            parent_head_ref=parent_head_ref,
            parent_head_digest=parent_head_digest,
            parent_package_digest=self.package_digest,
        )


@dataclass(frozen=True)
class FinanceSkillResolution:
    head_ref: str
    head_digest: str
    generation: int
    package_digest: str
    qualification_status: str
    adoption_enabled: bool
    bundle: SkillContentBundleV2
    previous_head_ref: str | None
    effective_version_parent_ref: str | None


class FinanceSkillHeadService:
    """One current Source pointer for one Finance profile in the current store."""

    def __init__(
        self,
        store: StateStore,
        *,
        tenant_id: str | None = None,
        policy: ReviewedCapabilityPolicyV2 | None = None,
    ) -> None:
        if (
            (store.tenant_id is None and not tenant_id)
            or (store.tenant_id is not None and tenant_id not in {None, store.tenant_id})
        ):
            raise ValueError("FINANCE_SKILL_TENANT_SCOPE_INVALID")
        self.store = store
        self.tenant_id = store.tenant_id or tenant_id
        self.policy = policy or ReviewedCapabilityPolicyV2()
        self.policy.require_current_contract()

    @property
    def scope_digest(self) -> str:
        return sha256_digest(
            {
                "tenant_id": self.tenant_id,
                "workspace_id": self.store.workspace_id,
                "skill_name": SKILL_NAME,
                "profile_identity": PROFILE_ID,
            }
        )

    @property
    def head_id(self) -> str:
        return f"skill-head:{self.scope_digest.removeprefix('sha256:')}"

    def _governor(self) -> Principal:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_SKILL_VERIFIED_PRINCIPAL_REQUIRED", 401)
        authorize(principal, "govern", self.tenant_id)
        authorization = current_authorization()
        if authorization is not None:
            authorization()
        return principal

    def _bundle_ref(self, bundle: SkillContentBundleV2) -> str:
        return CONTENT_PREFIX + bundle.digest.removeprefix("sha256:")

    def _head_object(
        self, *, generation: int, transition_kind: str, bundle: SkillContentBundleV2,
        previous: VersionedObject | None, actor_id: str,
        qualification_status: str = "UNQUALIFIED",
        qualification_ref: str | None = None,
        content_release_ref: str | None = None,
    ) -> VersionedObject:
        bundle_ref = self._bundle_ref(bundle)
        payload = {
            "schema_version": HEAD_SCHEMA,
            "scope_digest": self.scope_digest,
            "skill_name": SKILL_NAME,
            "profile_id": PROFILE_ID,
            "generation": generation,
            "transition_kind": transition_kind,
            "previous_head_ref": previous.ref if previous else None,
            "previous_head_digest": previous.digest if previous else None,
            "effective_version_ref": bundle_ref,
            "effective_version_digest": bundle.digest,
            "effective_version_parent_ref": (
                previous.payload["effective_version_ref"] if previous else None
            ),
            "effective_version_parent_digest": (
                previous.payload["effective_version_digest"] if previous else None
            ),
            "bundle_ref": bundle_ref,
            "bundle_digest": bundle.digest,
            "package_digest": bundle.package_digest,
            "resource_digests": bundle.resource_digests,
            "policy_digest": self.policy.digest,
            "protected_kernel_digest": self.policy.protected_kernel_digest,
            "reviewed_reference_revision": self.policy.reference_revision,
            "qualification_status": qualification_status,
            "adoption_enabled": False,
            "actor_id": actor_id,
            "qualification_ref": qualification_ref,
            "content_release_ref": content_release_ref,
        }
        return VersionedObject(
            id=self.head_id,
            version=f"g{generation:08d}",
            kind="Source",
            label=SKILL_NAME,
            domain="skill-governance",
            state=ObjectState.CURRENT,
            payload=payload,
            source_refs=(
                bundle_ref,
                *(() if previous is None else (previous.ref,)),
                *(() if qualification_ref is None else (qualification_ref,)),
                *(() if content_release_ref is None else (content_release_ref,)),
            ),
            allowed_purposes=("skill_evaluation", "skill_governance"),
        )

    def bootstrap(self) -> FinanceSkillResolution:
        """Explicit static GENESIS; this never qualifies or adopts the package."""
        principal = self._governor()
        bundle = SkillContentBundleV2.static_baseline(self.policy)
        source = self._head_object(
            generation=0, transition_kind="GENESIS", bundle=bundle,
            previous=None, actor_id=principal.actor_id,
        )
        with self.store.transaction() as connection:
            self._governor()
            self.store.require_before_commit(connection, self._governor)
            self.store.save_artifact(connection, self._bundle_ref(bundle), CONTENT_MEDIA, bundle.payload)
            self.store.create_current_if_absent(connection, source)
            self.store.append_event(
                connection, "FINANCE_SKILL_HEAD_BOOTSTRAPPED",
                {
                    "head_ref": source.ref,
                    "head_digest": source.digest,
                    "generation": 0,
                    "bundle_ref": self._bundle_ref(bundle),
                    "bundle_digest": bundle.digest,
                    "package_digest": bundle.package_digest,
                    "policy_digest": self.policy.digest,
                    "actor_id": principal.actor_id,
                    "qualification_status": "UNQUALIFIED",
                    "adoption_enabled": False,
                },
            )
        return self.resolve()

    def _current_source(self) -> VersionedObject:
        try:
            source = self.store.get_object(self.head_id)
        except KeyError as exc:
            raise IntegrityError("FINANCE_SKILL_HEAD_NOT_BOOTSTRAPPED") from exc
        payload = source.payload
        if (
            source.kind != "Source"
            or source.domain != "skill-governance"
            or source.state is not ObjectState.CURRENT
            or source.version != f"g{payload.get('generation', -1):08d}"
            or payload.get("schema_version") != HEAD_SCHEMA
            or payload.get("scope_digest") != self.scope_digest
            or payload.get("skill_name") != SKILL_NAME
            or payload.get("profile_id") != PROFILE_ID
            or payload.get("policy_digest") != self.policy.digest
            or payload.get("protected_kernel_digest") != self.policy.protected_kernel_digest
            or payload.get("reviewed_reference_revision") != self.policy.reference_revision
            or payload.get("adoption_enabled") is not False
        ):
            raise IntegrityError("FINANCE_SKILL_HEAD_INVALID")
        return source

    def resolve(self, *, require_current: bool = True) -> FinanceSkillResolution:
        """Reconstruct exact bytes from canonical head and verify all digests."""
        if require_current is not True:
            raise IntegrityError("FINANCE_SKILL_HISTORICAL_RESOLVE_REQUIRES_EXACT_GRANT")
        source = self._current_source()
        payload = source.payload
        ref = payload.get("bundle_ref")
        if not isinstance(ref, str) or not ref.startswith(CONTENT_PREFIX):
            raise IntegrityError("FINANCE_SKILL_BUNDLE_REF_INVALID")
        artifact = self.store.load_artifact(ref, CONTENT_MEDIA)
        bundle = SkillContentBundleV2.from_payload(artifact.payload, policy=self.policy)
        if (
            ref != self._bundle_ref(bundle)
            or payload.get("bundle_digest") != bundle.digest
            or payload.get("package_digest") != bundle.package_digest
            or payload.get("resource_digests") != bundle.resource_digests
            or payload.get("effective_version_ref") != ref
            or payload.get("effective_version_digest") != bundle.digest
        ):
            raise IntegrityError("FINANCE_SKILL_HEAD_CONTENT_MISMATCH")
        if source.payload["generation"] == 0 and (
            source.payload["transition_kind"] != "GENESIS"
            or source.payload["previous_head_ref"] is not None
            or source.payload["effective_version_parent_ref"] is not None
            or bundle.payload["parent_head_ref"] is not None
        ):
            raise IntegrityError("FINANCE_SKILL_GENESIS_INVALID")
        if source.payload["generation"] > 0 and (
            source.payload["transition_kind"] != "PROMOTE"
            or source.payload["qualification_status"] != "QUALIFIED"
            or not source.payload.get("qualification_ref")
            or not source.payload.get("content_release_ref")
            or bundle.payload["parent_head_ref"] != source.payload["previous_head_ref"]
            or bundle.payload["parent_head_digest"] != source.payload["previous_head_digest"]
            or bundle.payload["parent_package_digest"] is None
            or source.payload["effective_version_parent_ref"] is None
        ):
            raise IntegrityError("FINANCE_SKILL_PROMOTED_HEAD_INVALID")
        return FinanceSkillResolution(
            head_ref=source.ref,
            head_digest=source.digest,
            generation=payload["generation"],
            package_digest=bundle.package_digest,
            qualification_status=payload["qualification_status"],
            adoption_enabled=payload["adoption_enabled"],
            bundle=bundle,
            previous_head_ref=payload["previous_head_ref"],
            effective_version_parent_ref=payload["effective_version_parent_ref"],
        )

    def prepare_instruction_patch(
        self,
        instruction_text: str,
        *,
        expected_head_ref: str,
        expected_head_digest: str,
        expected_generation: int,
        expected_package_digest: str,
    ) -> SkillContentBundleV2:
        """Unpublished candidate material for an independent evaluator."""
        current = self.resolve()
        if (
            current.head_ref != expected_head_ref
            or current.head_digest != expected_head_digest
            or current.generation != expected_generation
            or current.package_digest != expected_package_digest
        ):
            raise IntegrityError("FINANCE_SKILL_STALE_BASE")
        if not isinstance(instruction_text, str):
            raise IntegrityError("FINANCE_SKILL_INSTRUCTION_TEXT_REQUIRED")
        return current.bundle.patched_instruction(
            instruction_text.encode("utf-8"),
            policy=self.policy,
            parent_head_ref=current.head_ref,
            parent_head_digest=current.head_digest,
        )


__all__ = [
    "CONSUMER_ID",
    "INSTRUCTION_PATH",
    "PROFILE_ID",
    "REFERENCE_PATH",
    "FinanceSkillHeadService",
    "FinanceSkillResolution",
    "ReviewedCapabilityPolicyV2",
    "SkillContentBundleV2",
]
