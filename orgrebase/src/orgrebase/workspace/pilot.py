"""Deployable Enterprise Quote Pack v1 loader and runtime compiler.

The Pack is a deployment boundary for the existing governed Workspace loop.  It
does not own state, orchestration, approval, or canonical writes.  It turns one
strict directory of enterprise declarations into the immutable inputs already
consumed by the Workspace formation and selective-Rebase services.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_serializer, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentIdentity,
    CoverageBasis,
    DependencyEdge,
    DependencyManifest,
    DependencyRequirementSlot,
    DependencyStrength,
    EdgeStatus,
    ManifestCompleteness,
    ObjectState,
    VersionedObject,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.workspace.domain_agents import LocalSourceValue
from orgrebase.workspace.models import (
    DomainPack,
    EnterpriseBinding,
    SemanticKind,
    WorkspaceGraphEdge,
    WorkspaceUniverse,
)
from orgrebase.workspace.pricing import PricingPolicy, QuoteBasket, calculate_quote
from orgrebase.workspace.profile import EnterpriseSeedProfile
from orgrebase.workspace.profile_contracts import (
    ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE,
    RuntimeCompatibilityMode,
    SeedComponentKind,
)
from orgrebase.workspace.source_admission import (
    MAX_SOURCE_BYTES,
    DirectorySeedSourceResolver,
    EnterpriseSeedComponentRoot,
    EnterpriseSeedRuntimeProjectionReceipt,
    EnterpriseSeedSourceAdmissionReceipt,
    admit_enterprise_seed_sources,
    verify_runtime_projections,
)
from orgrebase.workspace.templates import TemplateRegistry, default_capability_cards

PILOT_PACK_SCHEMA_VERSION = "orgrebase.enterprise-quote-pilot-pack.v1"
PILOT_HANDLER_PROFILE = ENTERPRISE_QUOTE_PILOT_HANDLER_PROFILE
PILOT_ADAPTER_ID = "enterprise-quote-v1"
PILOT_PACK_FILENAME = "pack.json"

_STANDARD_SLOT_BINDINGS: dict[str, tuple[str, str, SemanticKind]] = {
    "product_plan": ("claim:product.enterprise_plan", "product", SemanticKind.CLAIM),
    "launch_date": ("claim:product.launch_date", "product", SemanticKind.CLAIM),
    "data_residency": (
        "claim:product.residency_capability",
        "product",
        SemanticKind.CLAIM,
    ),
    "notice_required": (
        "claim:legal.customer_notice_required",
        "legal",
        SemanticKind.CLAIM,
    ),
    "price_band": ("policy:finance.price_band", "finance", SemanticKind.POLICY),
    "currency": ("policy:finance.currency", "finance", SemanticKind.POLICY),
    "partner_terms": ("claim:gtm.partner_terms", "gtm", SemanticKind.CLAIM),
    "quote_compose_skill": (
        "skill:enterprise-quote-compose",
        "gtm",
        SemanticKind.SKILL,
    ),
    "public_message": ("claim:gtm.public_launch_message", "gtm", SemanticKind.CLAIM),
}

_PRICED_SLOT_BINDINGS: dict[str, tuple[str, str, SemanticKind]] = {
    "quote_basket": ("claim:product.quote_basket", "product", SemanticKind.CLAIM),
    "pricing_policy": ("policy:finance.pricing", "finance", SemanticKind.POLICY),
}


def quote_slot_bindings(template_ref: str) -> dict[str, tuple[str, str, SemanticKind]]:
    """Preserve the original exact slot set for historical quote contracts."""
    return {**_STANDARD_SLOT_BINDINGS, **(
        _PRICED_SLOT_BINDINGS if template_ref == "template:enterprise_quote@v2" else {}
    )}


def _validate_priced_value(slot_id: str, value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("PILOT_PRICED_SLOT_VALUE_NOT_OBJECT")
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode("utf-8")) > 65_536:
        raise ValueError("PILOT_PRICED_SLOT_VALUE_TOO_LARGE")
    model = QuoteBasket if slot_id == "quote_basket" else PricingPolicy
    model.model_validate_json(raw)


class EnterpriseQuotePilotPackError(ValueError):
    """Stable startup failure for an invalid or unsafe Pilot Pack."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}:{detail}")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_relative_file(value: str, *, code: str) -> str:
    selected = Path(value)
    if not value or selected.is_absolute() or ".." in selected.parts or selected.as_posix() in {"", "."}:
        raise ValueError(code)
    return selected.as_posix()


class PilotLocatorBinding(_FrozenModel):
    component_kind: SeedComponentKind
    locator: str = Field(min_length=1)
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        object.__setattr__(
            self,
            "path",
            _require_relative_file(self.path, code="PILOT_COMPONENT_PATH_INVALID"),
        )
        return self


class PilotRuntimeDeclaration(_FrozenModel):
    quote_object_id: str = Field(min_length=1)
    quote_label: str = Field(min_length=1)
    graph_pointer_id: str = Field(min_length=1)
    graph_snapshot_id: str = Field(min_length=1)
    task_receipt_id: str = Field(min_length=1)
    default_run_id: str = Field(min_length=1)
    universe_artifact_id: str = Field(min_length=1)
    universe_id: str = Field(min_length=1)
    universe_revision: str = Field(min_length=1)
    built_at: str = Field(min_length=1)


class PilotScenarioDeclaration(_FrozenModel):
    label: str = Field(min_length=1)
    primary_user: Literal["Enterprise Quote Operator"] = "Enterprise Quote Operator"
    workflow: Literal["enterprise_quote"] = "enterprise_quote"
    deployment_unit: Literal["single_enterprise_single_quote"] = "single_enterprise_single_quote"


class PilotBoundaryDeclaration(_FrozenModel):
    execution_profile: Literal["LOCAL_DETERMINISTIC", "AUTHENTICATED_SINGLE_TENANT"] = "LOCAL_DETERMINISTIC"
    enterprise_input: Literal["EXACT_DIRECTORY_PACK"] = "EXACT_DIRECTORY_PACK"
    external_writes: Literal["DISABLED"] = "DISABLED"
    approval_mode: Literal["EXPLICIT_OWNER_COMMAND"] = "EXPLICIT_OWNER_COMMAND"
    deployment_maturity: Literal["SINGLE_ENTERPRISE_PILOT", "AUTHENTICATED_SINGLE_TENANT"] = "SINGLE_ENTERPRISE_PILOT"


class EnterpriseQuotePilotPack(_FrozenModel):
    schema_version: Literal["orgrebase.enterprise-quote-pilot-pack.v1", "orgrebase.enterprise-quote-pilot-pack.v2"] = PILOT_PACK_SCHEMA_VERSION
    pack_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    adapter_id: Literal["enterprise-quote-v1"] = PILOT_ADAPTER_ID
    profile_path: str = Field(min_length=1)
    components: tuple[PilotLocatorBinding, ...]
    runtime: PilotRuntimeDeclaration
    scenario: PilotScenarioDeclaration
    boundaries: PilotBoundaryDeclaration
    enterprise_binding: EnterpriseBinding | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if self.enterprise_binding is None:
            data.pop("enterprise_binding", None)
        return data

    @model_validator(mode="after")
    def validate_pack(self) -> Self:
        if self.schema_version.endswith(".v2"):
            if self.enterprise_binding is None:
                raise ValueError("ENTERPRISE_BINDING_REQUIRED")
            if self.boundaries.execution_profile != "AUTHENTICATED_SINGLE_TENANT":
                raise ValueError("ENTERPRISE_EXECUTION_PROFILE_REQUIRED")
        elif self.enterprise_binding is not None:
            raise ValueError("ENTERPRISE_BINDING_REQUIRES_PACK_V2")
        object.__setattr__(
            self,
            "profile_path",
            _require_relative_file(self.profile_path, code="PILOT_PROFILE_PATH_INVALID"),
        )
        kinds = tuple(item.component_kind for item in self.components)
        if kinds != tuple(SeedComponentKind):
            raise ValueError("PILOT_COMPONENT_ORDER_OR_SET_INVALID")
        locators = tuple(item.locator for item in self.components)
        paths = tuple(item.path for item in self.components)
        if len(locators) != len(set(locators)):
            raise ValueError("PILOT_COMPONENT_LOCATOR_DUPLICATE")
        if len(paths) != len(set(paths)):
            raise ValueError("PILOT_COMPONENT_PATH_DUPLICATE")
        return self


class PilotSourceValue(_FrozenModel):
    slot_id: str = Field(min_length=1)
    object_ref: str = Field(min_length=1)
    domain_id: Literal["product", "legal", "finance", "gtm"]
    value: str | bool | dict[str, Any]
    semantic_kind: SemanticKind
    authority_ref: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    sensitivity: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL"] = "INTERNAL"
    raw_private_value: str | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.slot_id in _PRICED_SLOT_BINDINGS:
            _validate_priced_value(self.slot_id, self.value)
        elif isinstance(self.value, dict):
            raise ValueError("PILOT_STRUCTURED_SLOT_UNSUPPORTED")
        return self


class PilotProposedValue(_FrozenModel):
    change_kind: Literal["launch_date", "currency", "product_plan", "quote_basket", "pricing_policy"]
    object_ref: str = Field(min_length=1)
    value: str | dict[str, Any]
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.change_kind in _PRICED_SLOT_BINDINGS:
            _validate_priced_value(self.change_kind, self.value)
        elif not isinstance(self.value, str) or not self.value:
            raise ValueError("PILOT_PROPOSED_VALUE_NOT_STRING")
        return self


class PilotDomainProjection(_FrozenModel):
    organization_id: str
    scenario_id: str
    default_task: dict[str, Any]
    change_family: tuple[dict[str, Any], ...]
    domains: tuple[Literal["finance", "gtm", "legal", "product"], ...]


class PilotKnowledgeProjection(_FrozenModel):
    source_values: tuple[PilotSourceValue, ...]
    proposed_values: tuple[PilotProposedValue, ...] = ()


class PilotAuthorityProjection(_FrozenModel):
    governance: dict[str, Any]
    source_authority_refs: tuple[str, ...]
    capability_authority_refs: tuple[str, ...]
    admission_controller_contract: Literal["orgrebase.workspace.admission.AdmissionController@v1"]


class PilotComponentBinding(_FrozenModel):
    ref: str
    digest: str


class PilotCapabilityProjection(_FrozenModel):
    template: PilotComponentBinding
    capability_cards: tuple[PilotComponentBinding, ...]
    runtime_components: dict[str, str]


class PilotDependencyDeclaration(_FrozenModel):
    slot_id: str
    source_slot: str
    relation: str = Field(min_length=1)
    strength: DependencyStrength
    coverage_basis: CoverageBasis


class PilotTargetDeclaration(_FrozenModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    kind: Literal["WorkItemVersion"] = "WorkItemVersion"
    label: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    state: Literal["CURRENT"] = "CURRENT"
    owner: str = Field(min_length=1)
    dependencies: tuple[PilotDependencyDeclaration, ...] = Field(min_length=1)


class PilotDependencyProjection(_FrozenModel):
    targets: tuple[PilotTargetDeclaration, ...] = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    completeness_basis: tuple[CoverageBasis, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class EnterpriseQuotePilotRuntime:
    """All immutable inputs needed to bind the existing Workspace services."""

    profile: EnterpriseSeedProfile
    source_admission: EnterpriseSeedSourceAdmissionReceipt
    runtime_projection: EnterpriseSeedRuntimeProjectionReceipt
    source_values: Mapping[str, LocalSourceValue]
    proposed_values: Mapping[str, PilotProposedValue]
    seed_objects: tuple[VersionedObject, ...]
    universe: WorkspaceUniverse
    support_fixture: EnterpriseFixture
    quote_object_id: str
    quote_label: str
    graph_pointer_id: str
    graph_snapshot_id: str
    task_receipt_id: str
    default_run_id: str
    snapshot_target_ids: tuple[str, ...]
    snapshot_scope_roots: tuple[str, ...]
    scenario: Mapping[str, object]
    boundaries: Mapping[str, object]
    context_profiles: Mapping[str, Mapping[str, object]]
    change_order: tuple[str, ...]
    change_owners: Mapping[str, str]
    pack_id: str
    pack_revision: str
    adapter_id: str
    pack_digest: str
    pack_root: Path
    schema_version: str = PILOT_PACK_SCHEMA_VERSION
    enterprise_binding: EnterpriseBinding | None = None


@dataclass(frozen=True, slots=True)
class _CompiledPack:
    seed_objects: tuple[VersionedObject, ...]
    universe: WorkspaceUniverse
    support_fixture: EnterpriseFixture
    context_profiles: dict[str, dict[str, object]]


def enterprise_quote_pilot_run_id(runtime: EnterpriseQuotePilotRuntime) -> str:
    """Return the stable correlation root for one admitted Pack revision."""

    organization = runtime.profile.organization_id.split(":")[-1]
    pack_fingerprint = runtime.pack_digest.split(":", 1)[-1][:16]
    return f"run:enterprise-pilot:{organization}:{pack_fingerprint}@v1"


def _strict_json(raw: bytes, *, logical_ref: str) -> dict[str, Any]:
    if len(raw) > MAX_SOURCE_BYTES:
        raise EnterpriseQuotePilotPackError("PILOT_JSON_SIZE_LIMIT_EXCEEDED", logical_ref)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EnterpriseQuotePilotPackError("PILOT_JSON_NOT_UTF8", logical_ref) from exc

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EnterpriseQuotePilotPackError("PILOT_JSON_DUPLICATE_KEY", key)
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=reject_duplicates)
    except EnterpriseQuotePilotPackError:
        raise
    except json.JSONDecodeError as exc:
        raise EnterpriseQuotePilotPackError("PILOT_JSON_INVALID", logical_ref) from exc
    if not isinstance(value, dict):
        raise EnterpriseQuotePilotPackError("PILOT_JSON_ROOT_NOT_OBJECT", logical_ref)
    return value


def _read_pack_file(root: Path, relative: str, *, code: str) -> bytes:
    relative = _require_relative_file(relative, code=f"{code}_PATH_INVALID")
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise EnterpriseQuotePilotPackError(f"{code}_SYMLINK_FORBIDDEN", relative)
    try:
        selected = (root / relative).resolve(strict=True)
        selected.relative_to(root)
    except (OSError, ValueError) as exc:
        raise EnterpriseQuotePilotPackError(f"{code}_PATH_INVALID", relative) from exc
    if not selected.is_file():
        raise EnterpriseQuotePilotPackError(f"{code}_NOT_FILE", relative)
    try:
        with selected.open("rb") as handle:
            return handle.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise EnterpriseQuotePilotPackError(f"{code}_READ_FAILED", relative) from exc


def _parse_model(model_type, value: object, *, code: str):
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise EnterpriseQuotePilotPackError(code, str(exc)) from exc


def _split_ref(ref: str) -> tuple[str, str]:
    if "@" not in ref:
        raise EnterpriseQuotePilotPackError("PILOT_VERSIONED_REF_INVALID", ref)
    return ref.rsplit("@", 1)


def _slot_object(source: PilotSourceValue, *, state: ObjectState) -> VersionedObject:
    object_id, version = _split_ref(source.object_ref)
    expected_id, expected_domain, expected_kind = {**_STANDARD_SLOT_BINDINGS, **_PRICED_SLOT_BINDINGS}[source.slot_id]
    if object_id != expected_id:
        raise EnterpriseQuotePilotPackError("PILOT_SLOT_OBJECT_ID_INVALID", f"{source.slot_id}:{object_id}")
    if source.domain_id != expected_domain or source.semantic_kind != expected_kind:
        raise EnterpriseQuotePilotPackError("PILOT_SLOT_SEMANTICS_INVALID", source.slot_id)
    kind = {
        SemanticKind.CLAIM: "ClaimVersion",
        SemanticKind.POLICY: "PolicyVersion",
        SemanticKind.SKILL: "SkillReferenceVersion",
    }[source.semantic_kind]
    return VersionedObject(
        id=object_id,
        version=version,
        kind=kind,
        label=source.slot_id.replace("_", " ").title(),
        domain=source.domain_id,
        state=state,
        payload={"canonical_value": source.value, "authority": source.domain_id},
        source_refs=(f"{source.source_id}@{source.source_version}",),
        sensitivity=source.sensitivity,
        allowed_purposes=("enterprise_quote", "change_rebase"),
        coverage_complete=True,
        coverage_basis=(CoverageBasis.OWNER_DECLARED_COMPLETE,),
    )


def _proposed_object(
    proposed: PilotProposedValue,
    *,
    current: PilotSourceValue,
) -> VersionedObject:
    object_id, version = _split_ref(proposed.object_ref)
    expected_id, _domain, semantic_kind = {**_STANDARD_SLOT_BINDINGS, **_PRICED_SLOT_BINDINGS}[proposed.change_kind]
    if object_id != expected_id:
        raise EnterpriseQuotePilotPackError("PILOT_PROPOSED_OBJECT_ID_INVALID", proposed.change_kind)
    current_id, _current_version = _split_ref(current.object_ref)
    if current_id != object_id:
        raise EnterpriseQuotePilotPackError("PILOT_PROPOSED_OBJECT_FAMILY_MISMATCH", proposed.change_kind)
    kind = "ClaimVersion" if semantic_kind == SemanticKind.CLAIM else "PolicyVersion"
    return VersionedObject(
        id=object_id,
        version=version,
        kind=kind,
        label=proposed.change_kind.replace("_", " ").title(),
        domain=current.domain_id,
        state=ObjectState.PROPOSED,
        payload={"canonical_value": proposed.value, "authority": current.domain_id},
        source_refs=(f"{proposed.source_id}@{proposed.source_version}",),
        sensitivity=current.sensitivity,
        allowed_purposes=("enterprise_quote", "change_rebase"),
        coverage_complete=True,
        coverage_basis=(CoverageBasis.OWNER_DECLARED_COMPLETE,),
    )


def _target_object(target: PilotTargetDeclaration) -> VersionedObject:
    payload: dict[str, object] = {"owner": target.owner, "deliverable": target.id}
    return VersionedObject(
        id=target.id,
        version=target.version,
        kind=target.kind,
        label=target.label,
        domain=target.domain,
        state=ObjectState(target.state),
        payload=payload,
        source_refs=(f"source:pilot-target:{target.id}@{target.version}",),
        sensitivity="INTERNAL",
        allowed_purposes=("enterprise_quote", "change_rebase"),
        coverage_complete=all(
            item.coverage_basis != CoverageBasis.AGENT_INFERRED for item in target.dependencies
        ),
        coverage_basis=tuple(dict.fromkeys(item.coverage_basis for item in target.dependencies)),
    )


def _support_agents() -> tuple[AgentIdentity, ...]:
    specifications = (
        ("change-coordinator", "coordination"),
        ("product-steward", "product"),
        ("legal-steward", "legal"),
        ("finance-steward", "finance"),
        ("gtm-steward", "gtm"),
        ("skill-curator", "skill-governance"),
    )
    return tuple(
        AgentIdentity(
            name=name,
            role=f"{domain} candidate producer",
            capabilities=("produce scoped candidate", "cite admitted source"),
            inputs=("CompiledDelegationTask", "ContextManifest"),
            outputs=("Candidate", "EvidenceBinding"),
            dependencies=("OrgRebase read-only context",),
            decision_boundary=(
                "cannot approve",
                "cannot apply",
                "cannot write canonical state",
            ),
            trace=("input_digest", "output_digest", "source_ref"),
            authority_domain=domain,
        )
        for name, domain in specifications
    )


def _compile_pack(
    *,
    manifest: EnterpriseQuotePilotPack,
    profile: EnterpriseSeedProfile,
    knowledge: PilotKnowledgeProjection,
    dependency: PilotDependencyProjection,
) -> tuple[_CompiledPack, dict[str, LocalSourceValue], dict[str, PilotProposedValue]]:
    if manifest.schema_version.endswith(".v2") and (profile.change_family or knowledge.proposed_values):
        raise EnterpriseQuotePilotPackError("ENTERPRISE_PACK_FUTURE_CHANGES_FORBIDDEN")
    if manifest.enterprise_binding is not None:
        binding = manifest.enterprise_binding
        if binding.domain_pack_digest != DomainPack.enterprise_quote(profile.default_task.template_ref).digest:
            raise EnterpriseQuotePilotPackError("ENTERPRISE_BINDING_DOMAIN_PACK_MISMATCH")
        sources_by_slot = {item.slot_id: item for item in knowledge.source_values}
        if binding.organization_id != profile.organization_id or binding.quote_object_id != manifest.runtime.quote_object_id:
            raise EnterpriseQuotePilotPackError("ENTERPRISE_BINDING_SCOPE_MISMATCH")
        if not binding.resources:
            raise EnterpriseQuotePilotPackError("ENTERPRISE_BINDING_RESOURCES_REQUIRED")
        for resource in binding.resources:
            source = sources_by_slot.get(resource.slot_id)
            if (source is None or source.object_ref.rsplit("@", 1)[0] != resource.object_id
                    or source.domain_id != resource.domain_id or resource.owner_id not in profile.governance.owner_refs):
                raise EnterpriseQuotePilotPackError("ENTERPRISE_BINDING_RESOURCE_MISMATCH")
    sources = {item.slot_id: item for item in knowledge.source_values}
    slot_bindings = quote_slot_bindings(profile.default_task.template_ref)
    if tuple(sorted(sources)) != tuple(sorted(slot_bindings)):
        raise EnterpriseQuotePilotPackError("PILOT_STANDARD_SLOT_SET_INVALID")
    if len(sources) != len(knowledge.source_values):
        raise EnterpriseQuotePilotPackError("PILOT_SOURCE_SLOT_DUPLICATE")
    if not isinstance(sources["notice_required"].value, bool):
        raise EnterpriseQuotePilotPackError("PILOT_NOTICE_REQUIRED_NOT_BOOLEAN")
    for slot, source in sources.items():
        if slot not in {"notice_required", *_PRICED_SLOT_BINDINGS} and not isinstance(source.value, str):
            raise EnterpriseQuotePilotPackError("PILOT_SLOT_VALUE_NOT_STRING", slot)
    if "quote_basket" in sources:
        try:
            calculate_quote(
                QuoteBasket.model_validate_json(json.dumps(sources["quote_basket"].value)),
                PricingPolicy.model_validate_json(json.dumps(sources["pricing_policy"].value)),
                currency=str(sources["currency"].value),
            )
        except ValueError as exc:
            raise EnterpriseQuotePilotPackError("PILOT_PRICING_INPUT_INVALID", str(exc)) from exc

    proposed = {item.change_kind: item for item in knowledge.proposed_values}
    mutable_slots = DomainPack.enterprise_quote(profile.default_task.template_ref).mutable_slots
    if not set(proposed) <= set(mutable_slots) or len(proposed) != len(knowledge.proposed_values):
        raise EnterpriseQuotePilotPackError("PILOT_PROPOSED_CHANGE_SET_INVALID")

    change_by_kind = {item.kind: item for item in profile.change_family}
    if set(change_by_kind) != set(proposed):
        raise EnterpriseQuotePilotPackError("PILOT_PROFILE_CHANGE_SET_INVALID")
    for kind in proposed:
        binding = change_by_kind[kind]
        current_id, current_version = _split_ref(sources[kind].object_ref)
        proposed_id, proposed_version = _split_ref(proposed[kind].object_ref)
        if (
            binding.object_id != current_id
            or proposed_id != current_id
            or binding.base_version != current_version
            or binding.proposed_version != proposed_version
        ):
            raise EnterpriseQuotePilotPackError("PILOT_CHANGE_PROFILE_BINDING_MISMATCH", kind)

    local_values = {
        slot: LocalSourceValue(
            slot_id=item.slot_id,
            object_ref=item.object_ref,
            domain_id=item.domain_id,
            value=item.value,
            semantic_kind=item.semantic_kind,
            authority_ref=item.authority_ref,
            source_id=item.source_id,
            source_version=item.source_version,
            sensitivity=item.sensitivity,
            raw_private_value=item.raw_private_value,
        )
        for slot, item in sources.items()
    }
    source_objects = tuple(_slot_object(item, state=ObjectState.CURRENT) for item in sources.values())
    proposed_objects = tuple(
        _proposed_object(proposed[kind], current=sources[kind]) for kind in proposed
    )
    target_ids = tuple(item.id for item in dependency.targets)
    if len(target_ids) != len(set(target_ids)) or manifest.runtime.quote_object_id in target_ids:
        raise EnterpriseQuotePilotPackError("PILOT_TARGET_ID_SET_INVALID")
    targets = tuple(_target_object(item) for item in dependency.targets)
    object_by_ref = {item.ref: item for item in (*source_objects, *proposed_objects, *targets)}

    edges: list[WorkspaceGraphEdge] = []
    manifests: list[DependencyManifest] = []
    for target_spec, target in zip(dependency.targets, targets, strict=True):
        slots: list[DependencyRequirementSlot] = []
        for item in target_spec.dependencies:
            if item.source_slot not in sources:
                raise EnterpriseQuotePilotPackError("PILOT_TARGET_SOURCE_SLOT_UNKNOWN", item.source_slot)
            if item.source_slot in mutable_slots and item.strength == DependencyStrength.HARD:
                raise EnterpriseQuotePilotPackError(
                    "PILOT_EXTERNAL_HARD_REBUILD_UNSUPPORTED",
                    f"{target_spec.id}:{item.source_slot}",
                )
            source = object_by_ref[sources[item.source_slot].object_ref]
            edge_id = f"edge:pilot:{target.id.split(':')[-1]}:{item.slot_id}"
            evidence_ref = f"owner-declaration:{target.id}:{item.slot_id}@r1"
            manifest_id = f"dependency-manifest:{target.id.split(':')[-1]}"
            edges.append(
                WorkspaceGraphEdge(
                    id=edge_id,
                    provider_ref=source.ref,
                    provider_digest=source.digest,
                    consumer_ref=target.ref,
                    relation=item.relation,
                    strength=item.strength,
                    source_manifest_ref=f"{manifest_id}@{target.version}",
                    source_evidence_ref=evidence_ref,
                    coverage_basis=item.coverage_basis,
                    valid_from=manifest.runtime.built_at,
                )
            )
            slots.append(
                DependencyRequirementSlot(
                    slot_id=item.slot_id,
                    edge_id=edge_id,
                    source_id=source.id,
                    relation=item.relation,
                    strength=item.strength,
                    coverage_basis=item.coverage_basis,
                    provenance_refs=(evidence_ref,),
                )
            )
        manifests.append(
            DependencyManifest(
                id=f"dependency-manifest:{target.id.split(':')[-1]}",
                version=target.version,
                target_id=target.id,
                target_version=target.version,
                issuer_id=target_spec.owner,
                authority_domain=target.domain,
                completeness=ManifestCompleteness.COMPLETE,
                requirement_slots=tuple(slots),
                provenance_refs=(f"pilot-pack:{profile.ref}",),
            )
        )

    seed_objects = tuple(sorted((*source_objects, *proposed_objects, *targets), key=lambda item: item.ref))
    universe = WorkspaceUniverse(
        id=manifest.runtime.universe_artifact_id,
        revision=manifest.runtime.universe_revision,
        organization_id=profile.organization_id,
        universe_id=manifest.runtime.universe_id,
        current_objects=seed_objects,
        task_artifact_projections=(),
        edges=tuple(sorted(edges, key=lambda item: item.id)),
        runtime_manifests=(),
        imported_manifests=tuple(sorted(manifests, key=lambda item: item.ref)),
        target_ids=tuple(sorted(target_ids)),
        source_refs=tuple(sorted(set(dependency.source_refs) | {profile.ref})),
        completeness_basis=dependency.completeness_basis,
        built_at=manifest.runtime.built_at,
    )
    context_profiles = {
        "enterprise-quote-operator": {
            "target": manifest.runtime.quote_object_id,
            "include": [
                _split_ref(sources[slot].object_ref)[0]
                for slot in slot_bindings
                if slot != "public_message"
            ],
            "explicitly_consider": [],
        }
    }
    fixture = EnterpriseFixture(
        schema_version="orgrebase.fixture.enterprise-quote-pilot.v1",
        organization_id=profile.organization_id,
        revisions={
            "graph": f"{manifest.runtime.graph_pointer_id}@v1",
            "policy": "policy:context@r1",
            "authorization": "authz:relations@r1",
            "skill_registry": "skills:registry@r1",
            "runtime_registry": "runtime:workspace@v1",
            "evaluation_suite": "evaluation:enterprise-quote-pilot@r1",
        },
        change={
            "id": profile.change_family[0].change_id,
            "revision": profile.change_family[0].revision,
            "owner_id": profile.change_family[0].owner_id,
            "purpose": profile.change_family[0].purpose,
            "object_id": profile.change_family[0].object_id,
            "base_version": profile.change_family[0].base_version,
            "proposed_version": profile.change_family[0].proposed_version,
        } if profile.change_family else {},
        objects=seed_objects,
        dependencies=tuple(
            DependencyEdge(
                id=edge.id,
                source_id=_split_ref(edge.provider_ref)[0],
                target_id=_split_ref(edge.consumer_ref)[0],
                relation=edge.relation,
                strength=edge.strength,
                coverage_basis=edge.coverage_basis,
                status=(
                    EdgeStatus.PROPOSED_EDGE
                    if edge.coverage_basis == CoverageBasis.AGENT_INFERRED
                    else EdgeStatus.ADMITTED
                ),
                provenance_refs=(edge.source_manifest_ref, edge.source_evidence_ref),
            )
            for edge in universe.edges
        ),
        dependency_manifests=universe.imported_manifests,
        impact_targets=universe.target_ids,
        context_profiles=context_profiles,
        agents=_support_agents(),
        evaluation_cases=(
            {
                "id": "PILOT-E01",
                "partition": "replay",
                "input": {"classification": "AFFECTED_HARD"},
                "expected_action": "REBASE",
            },
            {
                "id": "PILOT-E02",
                "partition": "coverage",
                "input": {"classification": "UNKNOWN"},
                "expected_action": "ESCALATE",
            },
        ),
    )
    return (
        _CompiledPack(
            seed_objects=seed_objects,
            universe=universe,
            support_fixture=fixture,
            context_profiles=context_profiles,
        ),
        local_values,
        proposed,
    )


def _expected_runtime_projections(
    *,
    manifest: EnterpriseQuotePilotPack,
    profile: EnterpriseSeedProfile,
    domain: PilotDomainProjection,
    knowledge: PilotKnowledgeProjection,
    authority: PilotAuthorityProjection,
    capability: PilotCapabilityProjection,
    dependency: PilotDependencyProjection,
) -> dict[SeedComponentKind, dict[str, Any]]:
    expected_domain = PilotDomainProjection(
        organization_id=profile.organization_id,
        scenario_id=profile.scenario_id,
        default_task=profile.default_task.model_dump(mode="json"),
        change_family=tuple(item.model_dump(mode="json") for item in profile.change_family),
        domains=("finance", "gtm", "legal", "product"),
    )
    if domain != expected_domain:
        raise EnterpriseQuotePilotPackError("PILOT_DOMAIN_PROJECTION_RUNTIME_MISMATCH")

    expected_governance = profile.governance.model_dump(mode="json")
    if authority.governance != expected_governance:
        raise EnterpriseQuotePilotPackError("PILOT_AUTHORITY_GOVERNANCE_MISMATCH")
    source_authorities = tuple(sorted({item.authority_ref for item in knowledge.source_values}))
    if authority.source_authority_refs != source_authorities:
        raise EnterpriseQuotePilotPackError("PILOT_SOURCE_AUTHORITY_SET_MISMATCH")

    template = TemplateRegistry().get(profile.default_task.template_ref)
    if capability.template != PilotComponentBinding(ref=template.ref, digest=template.digest):
        raise EnterpriseQuotePilotPackError("PILOT_TEMPLATE_RUNTIME_MISMATCH")
    cards = default_capability_cards(profile.default_task.template_ref)
    expected_cards = tuple(PilotComponentBinding(ref=item.ref, digest=item.digest) for item in cards)
    if capability.capability_cards != expected_cards:
        raise EnterpriseQuotePilotPackError("PILOT_CAPABILITY_CARD_RUNTIME_MISMATCH")
    expected_components = {
        "planner": "orgrebase.workspace.planner.CoalitionPlanner@v1",
        "context_compiler": "orgrebase.workspace.context.TaskContextCompiler@v1",
        "renderer": f"orgrebase.workspace.execution.QuoteRenderer@{template.renderer_version}",
        "dependency_compiler": "orgrebase.workspace.execution.RuntimeDependencyCompiler@v1",
        "pack_compiler": f"orgrebase.workspace.pilot@{PILOT_HANDLER_PROFILE}",
    }
    if capability.runtime_components != expected_components:
        raise EnterpriseQuotePilotPackError("PILOT_RUNTIME_COMPONENT_SET_MISMATCH")
    if profile.runtime_compatibility.mode != RuntimeCompatibilityMode.REFERENCE_HANDLER or (
        profile.runtime_compatibility.handler_profile != PILOT_HANDLER_PROFILE
    ):
        raise EnterpriseQuotePilotPackError("PILOT_PROFILE_HANDLER_MISMATCH")
    if manifest.runtime.quote_object_id == profile.default_task.id:
        raise EnterpriseQuotePilotPackError("PILOT_TASK_QUOTE_ID_COLLISION")
    return {
        SeedComponentKind.DOMAIN: domain.model_dump(mode="json"),
        SeedComponentKind.KNOWLEDGE: knowledge.model_dump(mode="json"),
        SeedComponentKind.AUTHORITY: authority.model_dump(mode="json"),
        SeedComponentKind.CAPABILITY: capability.model_dump(mode="json"),
        SeedComponentKind.DEPENDENCY: dependency.model_dump(mode="json"),
    }


def load_enterprise_quote_pilot_pack(
    pack_root: str | Path,
) -> EnterpriseQuotePilotRuntime:
    """Load, admit, compile, and bind one exact Enterprise Quote Pack directory."""

    selected_root = Path(pack_root)
    if selected_root.is_symlink():
        raise EnterpriseQuotePilotPackError("PILOT_PACK_ROOT_SYMLINK_FORBIDDEN")
    try:
        root = selected_root.resolve(strict=True)
    except OSError as exc:
        raise EnterpriseQuotePilotPackError("PILOT_PACK_ROOT_INVALID", str(selected_root)) from exc
    if not root.is_dir():
        raise EnterpriseQuotePilotPackError("PILOT_PACK_ROOT_NOT_DIRECTORY", str(root))

    manifest_value = _strict_json(
        _read_pack_file(root, PILOT_PACK_FILENAME, code="PILOT_PACK_MANIFEST"),
        logical_ref=PILOT_PACK_FILENAME,
    )
    manifest = _parse_model(
        EnterpriseQuotePilotPack,
        manifest_value,
        code="PILOT_PACK_SCHEMA_INVALID",
    )
    profile_value = _strict_json(
        _read_pack_file(root, manifest.profile_path, code="PILOT_PROFILE"),
        logical_ref=manifest.profile_path,
    )
    profile = _parse_model(
        EnterpriseSeedProfile,
        profile_value,
        code="PILOT_PROFILE_SCHEMA_INVALID",
    )

    locator_assets = {item.locator: item.path for item in manifest.components}
    declared_locators = tuple(item.locator for item in profile.source_roots)
    if declared_locators != tuple(locator_assets):
        raise EnterpriseQuotePilotPackError("PILOT_PROFILE_LOCATOR_MAP_MISMATCH")
    resolver = DirectorySeedSourceResolver(root, locator_assets)
    source_admission = admit_enterprise_seed_sources(profile, resolver=resolver)

    roots: dict[SeedComponentKind, EnterpriseSeedComponentRoot] = {}
    for binding in manifest.components:
        resolved = resolver.resolve(binding.locator)
        value = _strict_json(resolved.raw, logical_ref=resolved.logical_asset_ref)
        root_model = _parse_model(
            EnterpriseSeedComponentRoot,
            value,
            code="PILOT_COMPONENT_SCHEMA_INVALID",
        )
        roots[binding.component_kind] = root_model

    domain = _parse_model(
        PilotDomainProjection,
        roots[SeedComponentKind.DOMAIN].projection,
        code="PILOT_DOMAIN_PROJECTION_INVALID",
    )
    knowledge = _parse_model(
        PilotKnowledgeProjection,
        roots[SeedComponentKind.KNOWLEDGE].projection,
        code="PILOT_KNOWLEDGE_PROJECTION_INVALID",
    )
    authority = _parse_model(
        PilotAuthorityProjection,
        roots[SeedComponentKind.AUTHORITY].projection,
        code="PILOT_AUTHORITY_PROJECTION_INVALID",
    )
    capability = _parse_model(
        PilotCapabilityProjection,
        roots[SeedComponentKind.CAPABILITY].projection,
        code="PILOT_CAPABILITY_PROJECTION_INVALID",
    )
    dependency = _parse_model(
        PilotDependencyProjection,
        roots[SeedComponentKind.DEPENDENCY].projection,
        code="PILOT_DEPENDENCY_PROJECTION_INVALID",
    )
    compiled, source_values, proposed_values = _compile_pack(
        manifest=manifest,
        profile=profile,
        knowledge=knowledge,
        dependency=dependency,
    )
    runtime_projections = _expected_runtime_projections(
        manifest=manifest,
        profile=profile,
        domain=domain,
        knowledge=knowledge,
        authority=authority,
        capability=capability,
        dependency=dependency,
    )
    runtime_projection = verify_runtime_projections(
        profile,
        source_admission,
        runtime_projections,
    )
    pack_digest = sha256_digest(
        {
            "manifest": manifest.model_dump(mode="json"),
            "profile_digest": profile.digest,
            "source_admission_digest": source_admission.digest,
            "runtime_projection_digest": runtime_projection.digest,
            "universe_digest": compiled.universe.digest,
        }
    )
    scenario = {
        **profile.scenario_view(),
        **manifest.scenario.model_dump(mode="json"),
        "pack_digest": pack_digest,
    }
    boundaries = {
        **manifest.boundaries.model_dump(mode="json"),
        "data_profile": profile.data_class.value,
        "canonical_state_owner": "OrgRebase StateStore and RebaseWorkflow",
        "agents_are_candidate_only": True,
    }
    change_owners = {item.kind: item.owner_id for item in profile.change_family}
    context_profiles = MappingProxyType(
        {
            key: MappingProxyType(
                {
                    field: tuple(selected) if isinstance(selected, list) else selected
                    for field, selected in value.items()
                }
            )
            for key, value in compiled.context_profiles.items()
        }
    )
    return EnterpriseQuotePilotRuntime(
        profile=profile,
        source_admission=source_admission,
        runtime_projection=runtime_projection,
        source_values=MappingProxyType(dict(source_values)),
        proposed_values=MappingProxyType(dict(proposed_values)),
        seed_objects=compiled.seed_objects,
        universe=compiled.universe,
        support_fixture=compiled.support_fixture,
        quote_object_id=manifest.runtime.quote_object_id,
        quote_label=manifest.runtime.quote_label,
        graph_pointer_id=manifest.runtime.graph_pointer_id,
        graph_snapshot_id=manifest.runtime.graph_snapshot_id,
        task_receipt_id=manifest.runtime.task_receipt_id,
        default_run_id=manifest.runtime.default_run_id,
        snapshot_target_ids=(manifest.runtime.quote_object_id, *compiled.universe.target_ids),
        snapshot_scope_roots=(tuple(item.object_id for item in manifest.enterprise_binding.resources)
                              if manifest.enterprise_binding is not None else tuple(item.object_id for item in profile.change_family)),
        scenario=MappingProxyType(scenario),
        boundaries=MappingProxyType(boundaries),
        context_profiles=context_profiles,
        change_order=tuple(item.kind for item in profile.change_family),
        change_owners=MappingProxyType(change_owners),
        pack_id=manifest.pack_id,
        pack_revision=manifest.revision,
        adapter_id=manifest.adapter_id,
        pack_digest=pack_digest,
        pack_root=root,
        schema_version=manifest.schema_version,
        enterprise_binding=manifest.enterprise_binding,
    )


__all__ = (
    "PILOT_ADAPTER_ID",
    "PILOT_HANDLER_PROFILE",
    "PILOT_PACK_SCHEMA_VERSION",
    "EnterpriseQuotePilotPack",
    "EnterpriseQuotePilotPackError",
    "EnterpriseQuotePilotRuntime",
    "enterprise_quote_pilot_run_id",
    "load_enterprise_quote_pilot_pack",
)
