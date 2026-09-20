"""Agent-assisted, candidate-only OAC enterprise-intake mapping.

This module deliberately sits *above* the OAC contract and *before* source
admission.  A pinned AgentTeams task receives an admitted Enterprise Quote
Pack projection, acknowledges the hand-off, and only then asks Vertex Gemini
for one schema-constrained mapping candidate.  Deterministic checks remain the
only authority for target paths, Unknown preservation, authority non-expansion,
and zero effects.

The deterministic mapper in :mod:`oac_quote_adaptation` remains the keyless
baseline.  A missing credential, provider failure, schema failure, or semantic
violation produces ``HOLD`` and can never be relabelled as a live Agent result.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from orgrebase.agentteams_source import load_teamharness_lock
from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel
from orgrebase.workspace.model_observations import ModelAttemptObserver, provider_observations
from orgrebase.workspace.model_provider import (
    VERTEX_MODEL_ID,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.models import ModelRequest, ModelResponseReceipt
from orgrebase.workspace.native_taskflow import (
    ControlledLocalMatrix,
    LifecycleJournal,
    NativeTaskflowError,
    load_pinned_teamharness,
)
from orgrebase.workspace.oac_quote_adaptation import CandidateSemanticMapping
from orgrebase.workspace.pilot import EnterpriseQuotePilotRuntime
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile, SeedComponentKind

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]

AGENT_PRINCIPAL = "principal:oac-intake-mapper"
EFFECT_CEILING = "ZERO_EXTERNAL_EFFECTS"
MAPPING_SCHEMA_VERSION = "orgrebase.oac-agent-mapping-candidate.v1"
RECEIPT_SCHEMA_VERSION = "orgrebase.oac-agent-mapping-receipt.v1"
PROMPT_TEMPLATE_REF = "prompt:oac-agent-intake-mapper@v1"
PROMPT_TEMPLATE_DIGEST = sha256_digest(
    {
        "ref": PROMPT_TEMPLATE_REF,
        "rules": [
            "candidate-only",
            "map-five-admitted-components",
            "preserve-unknowns",
            "do-not-expand-authority",
            "return-schema-only",
        ],
    }
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OACAgentComponentProposal(_StrictFrozenModel):
    component_kind: SeedComponentKind
    source_root_ref: str = Field(min_length=1)
    source_digest: Digest
    target_oac_paths: tuple[str, ...] = Field(min_length=1)
    declared_unknowns: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    authority_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        for values in (
            self.target_oac_paths,
            self.declared_unknowns,
            self.reason_codes,
            self.authority_refs,
        ):
            if len(values) != len(set(values)):
                raise ValueError("OAC_AGENT_MAPPING_DUPLICATE_VALUE")
        if bool(self.declared_unknowns) != bool(self.reason_codes):
            raise ValueError("OAC_AGENT_MAPPING_UNKNOWN_REASON_BINDING_INVALID")
        return self


class OACAgentMappingCandidate(_StrictFrozenModel):
    """Provider output contract.  Structural validity is not admission."""

    schema_version: Literal["orgrebase.oac-agent-mapping-candidate.v1"] = MAPPING_SCHEMA_VERSION
    adaptation_run_id: str = Field(min_length=1)
    profile_digest: Digest
    pack_digest: Digest
    producer_ref: Literal["principal:oac-intake-mapper"] = AGENT_PRINCIPAL
    requested_approval_authority_ref: str = Field(min_length=1)
    component_mappings: tuple[OACAgentComponentProposal, ...] = Field(
        min_length=5,
        max_length=5,
    )
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = EFFECT_CEILING

    @model_validator(mode="after")
    def validate_component_set(self) -> Self:
        kinds = tuple(item.component_kind for item in self.component_mappings)
        if len(kinds) != len(set(kinds)) or set(kinds) != set(SeedComponentKind):
            raise ValueError("OAC_AGENT_MAPPING_COMPONENT_SET_INVALID")
        return self


class OACAgentValidationCheck(_StrictFrozenModel):
    check_id: str = Field(min_length=1)
    passed: bool
    reason_code: str = Field(min_length=1)


class OACAgentValidationReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-agent-validation.v1"] = "orgrebase.oac-agent-validation.v1"
    verdict: Literal["PASS", "HOLD"]
    checks: tuple[OACAgentValidationCheck, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    accepted_mapping_set_digest: Digest | None = None
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        failed = tuple(item.reason_code for item in self.checks if not item.passed)
        if self.verdict == "PASS":
            if failed or self.reason_codes != ("ALL_DETERMINISTIC_CHECKS_PASS",):
                raise ValueError("OAC_AGENT_VALIDATION_PASS_SHAPE_INVALID")
            if self.accepted_mapping_set_digest is None:
                raise ValueError("OAC_AGENT_VALIDATION_MAPPING_DIGEST_REQUIRED")
        elif not failed or self.accepted_mapping_set_digest is not None:
            raise ValueError("OAC_AGENT_VALIDATION_HOLD_SHAPE_INVALID")
        return self


class OACAgentModelObservation(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-agent-model-observation.v1"] = (
        "orgrebase.oac-agent-model-observation.v1"
    )
    provider: Literal["vertex-ai"] = "vertex-ai"
    model_id: Literal["gemini-3.7-flash", "gemini-3.8-flash"]
    model_version: Literal["gemini-3.7-flash", "gemini-3.8-flash"]
    request_digest: Digest
    input_digest: Digest
    output_schema_digest: Digest
    provider_response_receipt_digest: Digest
    output_digest: Digest | None = None
    provider_request_id: str | None = None
    status: Literal["VALID", "ABSTAIN", "SCHEMA_ERROR", "PROVIDER_ERROR", "NOT_RUN"]
    evidence_class: str = Field(min_length=1)
    error_code: str | None = None
    finish_reason: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    credential_material_persisted: Literal[False] = False
    raw_prompt_persisted: Literal[False] = False
    raw_provider_body_persisted: Literal[False] = False


class OACAgentTeamsMappingLifecycle(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-agentteams-mapping-lifecycle.v1"] = (
        "orgrebase.oac-agentteams-mapping-lifecycle.v1"
    )
    evidence_class: Literal["CONTROLLED_LOCAL_NATIVE_TASKFLOW"] = "CONTROLLED_LOCAL_NATIVE_TASKFLOW"
    claim_boundary: Literal[
        "PINNED_IN_PROCESS_CALL_TOOL_WITH_LIVE_MODEL_CANDIDATE_NOT_LIVE_DISTRIBUTED_WORKER"
    ] = "PINNED_IN_PROCESS_CALL_TOOL_WITH_LIVE_MODEL_CANDIDATE_NOT_LIVE_DISTRIBUTED_WORKER"
    agentteams_version: str = Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")
    agentteams_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_lock_digest: Digest
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    worker_ref: Literal["@oac-intake-mapper:controlled.local"] = "@oac-intake-mapper:controlled.local"
    context_digest: Digest
    task_spec_digest: Digest
    submitted_result_digest: Digest
    observed_result_digest: Digest
    actions: tuple[dict[str, Any], ...] = Field(min_length=9, max_length=9)
    action_sequence: tuple[
        Literal[
            "create_project",
            "plan_dag",
            "ready_nodes",
            "delegate_task",
            "ack_task",
            "submit_task",
            "check_task",
            "accept_task_result",
            "complete_project",
        ],
        ...,
    ]
    matrix_request_count: int = Field(ge=1)
    assignment_event_id: str = Field(min_length=1)
    project_terminal_state: Literal["completed"] = "completed"
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        expected = (
            "create_project",
            "plan_dag",
            "ready_nodes",
            "delegate_task",
            "ack_task",
            "submit_task",
            "check_task",
            "accept_task_result",
            "complete_project",
        )
        if self.action_sequence != expected:
            raise ValueError("OAC_AGENTTEAMS_ACTION_SEQUENCE_INVALID")
        if tuple(str(item.get("action")) for item in self.actions) != expected:
            raise ValueError("OAC_AGENTTEAMS_ACTION_RECEIPT_SEQUENCE_INVALID")
        if self.submitted_result_digest != self.observed_result_digest:
            raise ValueError("OAC_AGENTTEAMS_RESULT_ROUNDTRIP_MISMATCH")
        return self


class OACAgentMappingReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.oac-agent-mapping-receipt.v1"] = RECEIPT_SCHEMA_VERSION
    status: Literal["VALIDATED_CANDIDATE", "HOLD"]
    adaptation_run_id: str = Field(min_length=1)
    profile_digest: Digest
    pack_digest: Digest
    mapping_mode: Literal["LIVE_AGENT_ATTEMPT"] = "LIVE_AGENT_ATTEMPT"
    baseline_mapping_set_digest: Digest
    input_digest: Digest
    model_observation: OACAgentModelObservation
    native_agentteams: OACAgentTeamsMappingLifecycle
    validation: OACAgentValidationReceipt
    accepted_mappings: tuple[CandidateSemanticMapping, ...] = ()
    accepted_mapping_set_digest: Digest | None = None
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = EFFECT_CEILING
    human_approval_granted: Literal[False] = False
    oac_source_admitted: Literal[False] = False

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.native_agentteams.run_id != self.adaptation_run_id:
            raise ValueError("OAC_AGENT_MAPPING_RUN_BINDING_MISMATCH")
        expected = (
            sha256_digest([item.digest for item in self.accepted_mappings])
            if self.accepted_mappings
            else None
        )
        if expected != self.accepted_mapping_set_digest:
            raise ValueError("OAC_AGENT_MAPPING_SET_DIGEST_MISMATCH")
        if self.status == "VALIDATED_CANDIDATE":
            if (
                len(self.accepted_mappings) != 5
                or self.accepted_mapping_set_digest is None
                or self.validation.verdict != "PASS"
                or self.validation.accepted_mapping_set_digest != self.accepted_mapping_set_digest
                or self.model_observation.status != "VALID"
                or self.model_observation.evidence_class != "LIVE_MODEL"
                or not self.model_observation.provider_request_id
                or self.model_observation.output_digest is None
            ):
                raise ValueError("OAC_AGENT_MAPPING_VALIDATED_SHAPE_INVALID")
        elif (
            self.accepted_mappings
            or self.accepted_mapping_set_digest is not None
            or self.validation.verdict != "HOLD"
        ):
            raise ValueError("OAC_AGENT_MAPPING_HOLD_SHAPE_INVALID")
        return self


_IMPLEMENTATION_MEDIA = "application/vnd.orgrebase.oac-admission-implementation+json"
_IMPLEMENTATION_MODULES = ("oac_agent_adaptation.py", "oac_quote_adaptation.py", "oac_agentic_runtime.py")


def _current_agentteams_mapping_identity() -> dict[str, str]:
    lock = load_teamharness_lock()
    return {
        "agentteams_version": lock["tag"],
        "agentteams_commit": lock["commit"],
        "source_lock_digest": sha256_digest(lock),
    }


def require_current_agent_mapping_source(receipt: OACAgentMappingReceipt) -> None:
    """Allow a new admission only from the active, exactly pinned source."""

    lifecycle = receipt.native_agentteams.revalidated()
    if any(getattr(lifecycle, key) != value
           for key, value in _current_agentteams_mapping_identity().items()):
        raise OACAgentAdaptationError("OAC_AGENTTEAMS_SOURCE_REPLAN_REQUIRED")


def current_oac_admission_implementation() -> dict[str, Any]:
    """Code identity supplements the declared rules and exact candidate inputs."""

    from orgrebase.workspace.oac_quote_adaptation import quote_adaptation_rule_set_digest

    root = Path(__file__).parent
    body = {
        "schema_version": "orgrebase.oac-admission-implementation.v1",
        "rule_set_digest": quote_adaptation_rule_set_digest(),
        "agentteams_source": _current_agentteams_mapping_identity(),
        "implementation": {name: "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
                           for name in _IMPLEMENTATION_MODULES},
    }
    return {**body, "revision_digest": sha256_digest(body)}


def _mapping_implementation_payload(store: Any, receipt: OACAgentMappingReceipt,
                                    implementation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": store.tenant_id, "workspace_id": store.workspace_id,
        "adaptation_run_id": receipt.adaptation_run_id, "mapping_receipt_digest": receipt.digest,
        "profile_digest": receipt.profile_digest, "pack_digest": receipt.pack_digest,
        "baseline_mapping_set_digest": receipt.baseline_mapping_set_digest,
        "implementation": dict(implementation),
    }


def bind_oac_mapping_implementation(store: Any, receipt: OACAgentMappingReceipt, *,
                                    implementation: Mapping[str, Any]) -> None:
    """Retain the implementation captured around a newly executed mapper."""

    if dict(implementation) != current_oac_admission_implementation():
        raise OACAgentAdaptationError("OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED")
    require_current_agent_mapping_source(receipt)
    body = _mapping_implementation_payload(store, receipt, implementation)
    with store.transaction() as connection:
        store.save_artifact(connection, f"oac-mapping-implementation:{receipt.digest}", _IMPLEMENTATION_MEDIA, body)


def require_oac_mapping_implementation(store: Any, receipt: OACAgentMappingReceipt) -> None:
    expected = _mapping_implementation_payload(store, receipt, current_oac_admission_implementation())
    try:
        saved = store.load_artifact(f"oac-mapping-implementation:{receipt.digest}", _IMPLEMENTATION_MEDIA).payload
    except KeyError as exc:
        raise OACAgentAdaptationError("OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING") from exc
    if saved != expected:
        raise OACAgentAdaptationError("OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED")


class _StructuredProvider(Protocol):
    def generate_structured(
        self, *, request: ModelRequest, output_model: type[OACAgentMappingCandidate]
    ) -> ModelResponseReceipt: ...


class OACAgentAdaptationError(RuntimeError):
    """Stable fail-closed producer error."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OACAgentAdaptationError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _public_mapping_payload(mapping: CandidateSemanticMapping) -> dict[str, Any]:
    return {
        "component_kind": mapping.component_kind.value,
        "source_root_ref": mapping.source_root_ref,
        "source_digest": mapping.source_digest,
        "target_oac_paths": list(mapping.target_oac_paths),
        "declared_unknowns": list(mapping.declared_unknowns),
        "reason_codes": list(mapping.reason_codes),
    }


def build_oac_agent_prompt(
    *,
    profile: EnterpriseSeedProfile,
    runtime: EnterpriseQuotePilotRuntime,
    baseline_mappings: Sequence[CandidateSemanticMapping],
    adaptation_run_id: str,
) -> dict[str, Any]:
    """Build the provider-only prompt; callers persist only its digest.

    The admitted task projection is intentionally useful to the mapper, while
    ``raw_private_value`` and local file paths are omitted.  This is not a
    secrecy boundary for the enterprise source itself; it prevents accidental
    persistence of local credentials and private fixture annotations in the
    public evidence package.
    """

    source_values = []
    for slot_id, value in sorted(runtime.source_values.items()):
        source_values.append(
            {
                "slot_id": slot_id,
                "object_ref": value.object_ref,
                "domain_id": value.domain_id,
                "value": value.value,
                "semantic_kind": value.semantic_kind.value,
                "authority_ref": value.authority_ref,
                "source_id": value.source_id,
                "source_version": value.source_version,
                "sensitivity": value.sensitivity,
            }
        )
    allowed_authorities = sorted(
        {
            *profile.governance.admission_authority_refs,
            *profile.governance.owner_refs,
            *(str(item["authority_ref"]) for item in source_values),
        }
    )
    example = {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "adaptation_run_id": adaptation_run_id,
        "profile_digest": profile.digest,
        "pack_digest": runtime.pack_digest,
        "producer_ref": AGENT_PRINCIPAL,
        "requested_approval_authority_ref": profile.governance.admission_authority_refs[0],
        "component_mappings": [
            {
                **_public_mapping_payload(item),
                "authority_refs": [profile.governance.admission_authority_refs[0]],
            }
            for item in baseline_mappings
        ],
        "candidate_only": True,
        "canonical_target_writes": 0,
        "effect_ceiling": EFFECT_CEILING,
    }
    return {
        "instruction": (
            "Map the five admitted enterprise components to OAC paths. Return only "
            "the response schema. Preserve every declared Unknown, use only the "
            "listed target paths and authority refs, never self-approve, and make "
            "no canonical writes. The example is the deterministic safety envelope; "
            "change it only when the admitted projection requires an additional "
            "Unknown, never to expand authority or target paths."
        ),
        "task": {
            "adaptation_run_id": adaptation_run_id,
            "profile_ref": profile.ref,
            "profile_digest": profile.digest,
            "pack_digest": runtime.pack_digest,
            "organization_id": profile.organization_id,
            "purpose": profile.default_task.purpose,
            "effect_ceiling": profile.effect_ceiling.value,
            "candidate_only": True,
            "canonical_target_writes": 0,
        },
        "governance": {
            "producer_ref": AGENT_PRINCIPAL,
            "required_human_approval_ref": profile.governance.admission_authority_refs[0],
            "allowed_authority_refs": allowed_authorities,
        },
        "enterprise_projection": {
            "source_values": source_values,
            "proposed_values": [
                {
                    "change_kind": key,
                    "object_ref": value.object_ref,
                    "value": value.value,
                    "source_id": value.source_id,
                    "source_version": value.source_version,
                }
                for key, value in sorted(runtime.proposed_values.items())
            ],
            "runtime_projection_digest": runtime.runtime_projection.runtime_projection_digest,
            "source_admission_receipt_digest": runtime.source_admission.digest,
        },
        "allowed_mapping_envelope": example,
    }


def expected_oac_agent_candidate(
    *,
    profile: EnterpriseSeedProfile,
    runtime: EnterpriseQuotePilotRuntime,
    baseline_mappings: Sequence[CandidateSemanticMapping],
    adaptation_run_id: str,
) -> OACAgentMappingCandidate:
    """Return the exact safe candidate used by deterministic test providers."""

    return OACAgentMappingCandidate.model_validate(
        build_oac_agent_prompt(
            profile=profile,
            runtime=runtime,
            baseline_mappings=baseline_mappings,
            adaptation_run_id=adaptation_run_id,
        )["allowed_mapping_envelope"]
    )


def _check(
    check_id: str,
    passed: bool,
    failure_code: str,
) -> OACAgentValidationCheck:
    return OACAgentValidationCheck(
        check_id=check_id,
        passed=passed,
        reason_code="PASS" if passed else failure_code,
    )


def validate_oac_agent_candidate(
    *,
    candidate: OACAgentMappingCandidate,
    profile: EnterpriseSeedProfile,
    runtime: EnterpriseQuotePilotRuntime,
    baseline_mappings: Sequence[CandidateSemanticMapping],
    adaptation_run_id: str,
    task_id: str,
    provider_response_digest: str,
) -> tuple[OACAgentValidationReceipt, tuple[CandidateSemanticMapping, ...]]:
    """Apply deterministic authority and preservation rules to one candidate."""

    baseline_by_kind = {item.component_kind: item for item in baseline_mappings}
    proposed_by_kind = {item.component_kind: item for item in candidate.component_mappings}
    allowed_authorities = {
        *profile.governance.admission_authority_refs,
        *profile.governance.owner_refs,
        *(value.authority_ref for value in runtime.source_values.values()),
    }
    checks: list[OACAgentValidationCheck] = [
        _check(
            "run-binding",
            candidate.adaptation_run_id == adaptation_run_id,
            "ADAPTATION_RUN_SUBSTITUTION",
        ),
        _check(
            "profile-binding",
            candidate.profile_digest == profile.digest,
            "PROFILE_SUBSTITUTION",
        ),
        _check(
            "pack-binding",
            candidate.pack_digest == runtime.pack_digest,
            "PACK_SUBSTITUTION",
        ),
        _check(
            "candidate-only",
            candidate.candidate_only and candidate.canonical_target_writes == 0,
            "EFFECT_CEILING_EXPANSION",
        ),
        _check(
            "approval-separation",
            candidate.producer_ref != candidate.requested_approval_authority_ref
            and candidate.requested_approval_authority_ref == profile.governance.admission_authority_refs[0],
            "SELF_APPROVAL_FORBIDDEN",
        ),
    ]
    accepted: list[CandidateSemanticMapping] = []
    for kind in SeedComponentKind:
        baseline = baseline_by_kind[kind]
        proposal = proposed_by_kind[kind]
        checks.extend(
            (
                _check(
                    f"{kind.value.lower()}-source-ref",
                    proposal.source_root_ref == baseline.source_root_ref
                    and proposal.source_digest == baseline.source_digest,
                    "SOURCE_BINDING_SUBSTITUTION",
                ),
                _check(
                    f"{kind.value.lower()}-target-paths",
                    set(proposal.target_oac_paths) == set(baseline.target_oac_paths),
                    "TARGET_PATH_NOT_ALLOWED",
                ),
                _check(
                    f"{kind.value.lower()}-unknown-preservation",
                    set(baseline.declared_unknowns).issubset(proposal.declared_unknowns),
                    "UNKNOWN_ERASURE",
                ),
                _check(
                    f"{kind.value.lower()}-authority",
                    bool(proposal.authority_refs)
                    and set(proposal.authority_refs).issubset(allowed_authorities),
                    "AUTHORITY_EXPANSION",
                ),
            )
        )
        accepted.append(
            CandidateSemanticMapping(
                adaptation_run_id=adaptation_run_id,
                component_kind=kind,
                source_root_ref=baseline.source_root_ref,
                source_digest=baseline.source_digest,
                producer=f"agentteams:vertex-ai:{provider_response_digest}",
                task_id=task_id,
                target_oac_paths=baseline.target_oac_paths,
                declared_unknowns=proposal.declared_unknowns,
                reason_codes=proposal.reason_codes,
            )
        )
    failures = tuple(item.reason_code for item in checks if not item.passed)
    if failures:
        return (
            OACAgentValidationReceipt(
                verdict="HOLD",
                checks=tuple(checks),
                reason_codes=tuple(dict.fromkeys(failures)),
            ),
            (),
        )
    accepted_tuple = tuple(accepted)
    mapping_set_digest = sha256_digest([item.digest for item in accepted_tuple])
    return (
        OACAgentValidationReceipt(
            verdict="PASS",
            checks=tuple(checks),
            reason_codes=("ALL_DETERMINISTIC_CHECKS_PASS",),
            accepted_mapping_set_digest=mapping_set_digest,
        ),
        accepted_tuple,
    )


def _model_request(
    *,
    adaptation_run_id: str,
    task_id: str,
    profile_digest: str,
    pack_digest: str,
    input_digest: str,
) -> ModelRequest:
    schema_digest = sha256_digest(OACAgentMappingCandidate.model_json_schema(mode="validation"))
    return ModelRequest(
        request_id=f"model-request:{adaptation_run_id}:oac-intake-mapper",
        run_id=adaptation_run_id,
        task_ref=task_id,
        actor_id=AGENT_PRINCIPAL,
        purpose="oac_enterprise_intake_mapping_candidate",
        schema_name=MAPPING_SCHEMA_VERSION,
        schema_digest=schema_digest,
        context_refs=(
            f"profile-digest:{profile_digest}",
            f"pack-digest:{pack_digest}",
            f"input-digest:{input_digest}",
        ),
        input_refs=("enterprise-pack:admitted-projection",),
        allowed_tool_ids=(),
        provider="vertex-ai",
        model_id=VERTEX_MODEL_ID,
        model_version=VERTEX_MODEL_ID,
        prompt_template_ref=PROMPT_TEMPLATE_REF,
        prompt_template_digest=PROMPT_TEMPLATE_DIGEST,
        temperature=0.0,
        seed=None,
        max_output_tokens=4096,
        attempt=0,
    )


def _model_observation(
    *,
    response: ModelResponseReceipt,
    input_digest: str,
    output_schema_digest: str,
) -> OACAgentModelObservation:
    return OACAgentModelObservation(
        model_id=response.model_id,
        model_version=response.model_version,
        request_digest=response.request_digest,
        input_digest=input_digest,
        output_schema_digest=output_schema_digest,
        provider_response_receipt_digest=response.digest,
        output_digest=response.output_digest,
        provider_request_id=response.provider_request_id,
        status=response.status,
        evidence_class=str(response.evidence_class),
        error_code=response.error_code,
        finish_reason=response.finish_reason,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        latency_ms=response.latency_ms,
    )


def _unwrap_call_tool(response: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(response["content"][0]["text"]))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise NativeTaskflowError("OAC_AGENTTEAMS_MCP_RESPONSE_INVALID") from exc
    if not isinstance(value, dict):
        raise NativeTaskflowError("OAC_AGENTTEAMS_MCP_PAYLOAD_INVALID")
    return value


def _invoke(
    module: ModuleType,
    journal: LifecycleJournal,
    *,
    key: str,
    tool: str,
    action: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {"action": action, **arguments}
    wrapper = module.call_tool(tool, request)
    payload = _unwrap_call_tool(wrapper)
    entry = journal.record(
        key=key,
        tool=tool,
        action=action,
        arguments=request,
        response=wrapper,
        payload=payload,
    )
    if payload.get("ok") is not True:
        raise NativeTaskflowError(f"OAC_AGENTTEAMS_ACTION_FAILED:{key}")
    return payload, entry


def _require_task_status(payload: Mapping[str, Any], expected: str, key: str) -> None:
    task = payload.get("task")
    if not isinstance(task, Mapping) or task.get("status") != expected:
        raise NativeTaskflowError(f"OAC_AGENTTEAMS_TASK_STATE_MISMATCH:{key}:{expected}")


def _create_mc_adapter(root: Path) -> tuple[Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = root / "mc-invocations.jsonl"
    executable = bin_dir / "mc"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "p=os.environ.get('ORGREBASE_OAC_MC_LOG')\n"
        "if p:\n"
        "  with open(p, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(list(sys.argv[1:]), separators=(',', ':'))+'\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, log


@contextlib.contextmanager
def _environment(overrides: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _provider_failure_response(request: ModelRequest, exc: Exception) -> ModelResponseReceipt:
    return ModelResponseReceipt(
        id=f"model-response:{request.request_id}:attempt-{request.attempt}",
        request_ref=request.request_id,
        request_digest=request.digest,
        status="PROVIDER_ERROR",
        provider="vertex-ai",
        model_id=VERTEX_MODEL_ID,
        model_version=VERTEX_MODEL_ID,
        schema_valid=False,
        error_code=f"OAC_AGENT_PROVIDER_EXCEPTION:{type(exc).__name__}",
        completed_at="2026-08-28T00:00:00Z",
        evidence_class="NOT_RUN",
    )


def _manifest(root: Path) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _digest_bytes(path.read_bytes()),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode())
        if path.is_file() and path != root / "manifest.json"
    ]
    payload = {
        "schema_version": "orgrebase.oac-agent-mapping-evidence-manifest.v1",
        "status": "CLOSED_WORLD",
        "entries": entries,
        "entry_count": len(entries),
        "pack_digest": sha256_digest(entries),
    }
    return {**payload, "digest": sha256_digest(payload)}


def reseal_oac_agent_adaptation_evidence(
    output_dir: str | Path,
    *,
    forbidden_secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Rebuild the closed-world manifest after a runner adds derived evidence."""

    root = Path(output_dir).expanduser().resolve()
    if not (root / "mapping-receipt.json").is_file():
        raise OACAgentAdaptationError("OAC_AGENT_MAPPING_RECEIPT_MISSING")
    _assert_public_evidence_safe(root, forbidden_secret_values)
    manifest = _manifest(root)
    _write_json(root / "manifest.json", manifest)
    _assert_public_evidence_safe(root, forbidden_secret_values)
    return manifest


def _assert_public_evidence_safe(root: Path, forbidden_values: Sequence[str]) -> None:
    forbidden = tuple(value for value in forbidden_values if value)
    markers = (b"/" + b"Users/", b"/private/tmp/", b"/var/folders/")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if any(marker in raw for marker in markers):
            raise OACAgentAdaptationError(f"PRIVATE_PATH_PERSISTED:{path.name}")
        for secret in forbidden:
            if secret.encode("utf-8") in raw:
                raise OACAgentAdaptationError(f"SECRET_CANARY_PERSISTED:{path.name}")


def run_oac_agent_adaptation(
    *,
    checkout: str | Path,
    lock_path: str | Path,
    output_dir: str | Path,
    profile: EnterpriseSeedProfile,
    runtime: EnterpriseQuotePilotRuntime,
    baseline_mappings: Sequence[CandidateSemanticMapping],
    adaptation_run_id: str,
    provider: _StructuredProvider | None = None,
    vertex_project: str | None = None,
    forbidden_secret_values: Sequence[str] = (),
) -> OACAgentMappingReceipt:
    """Run one ACK-before-provider AgentTeams mapping task and retain evidence."""

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise OACAgentAdaptationError("OUTPUT_EVIDENCE_DIR_NOT_EMPTY")
    baseline = tuple(item.revalidated() for item in baseline_mappings)
    if tuple(item.component_kind for item in baseline) != tuple(SeedComponentKind):
        raise OACAgentAdaptationError("BASELINE_MAPPING_ORDER_INVALID")
    baseline_mapping_set_digest = sha256_digest([item.digest for item in baseline])
    prompt = build_oac_agent_prompt(
        profile=profile,
        runtime=runtime,
        baseline_mappings=baseline,
        adaptation_run_id=adaptation_run_id,
    )
    input_digest = sha256_digest(prompt)
    output_schema_digest = sha256_digest(OACAgentMappingCandidate.model_json_schema(mode="validation"))
    suffix = hashlib.sha256(adaptation_run_id.encode("utf-8")).hexdigest()[:16]
    project_id = f"oac-intake-{suffix}"
    task_id = f"{project_id}-mapper-a1"
    worker = "@oac-intake-mapper:controlled.local"
    room_id = "!oac-intake:controlled.local"
    context = {
        "adaptation_run_id": adaptation_run_id,
        "profile_digest": profile.digest,
        "pack_digest": runtime.pack_digest,
        "input_digest": input_digest,
        "output_schema_digest": output_schema_digest,
        "effect_ceiling": EFFECT_CEILING,
        "candidate_only": True,
        "canonical_target_writes": 0,
    }
    context_digest = sha256_digest(context)
    spec_payload = {
        "schema_version": "orgrebase.oac-agentteams-mapping-task.v1",
        **context,
        "context_digest": context_digest,
        "provider": "vertex-ai",
        "model_id": VERTEX_MODEL_ID,
        "human_approval_authority_ref": profile.governance.admission_authority_refs[0],
    }
    spec_text = json.dumps(spec_payload, ensure_ascii=False, indent=2, sort_keys=True)
    task_spec_digest = _digest_bytes(spec_text.encode("utf-8"))
    module, source_verification = load_pinned_teamharness(checkout, lock_path)
    source_identity = _current_agentteams_mapping_identity()
    if (source_verification["commit"], source_verification["source_lock_digest"]) != (
        source_identity["agentteams_commit"], source_identity["source_lock_digest"]
    ):
        raise OACAgentAdaptationError("OAC_AGENTTEAMS_SOURCE_REPLAN_REQUIRED")
    actions: list[dict[str, Any]] = []
    assignment_event_id = ""

    with tempfile.TemporaryDirectory(prefix="orgrebase-oac-agentteams-") as temporary:
        temp_root = Path(temporary)
        runtime_root = temp_root / "workspace"
        runtime_root.mkdir()
        mc_bin, mc_log = _create_mc_adapter(temp_root)
        replacements = {
            str(Path(checkout).expanduser().resolve()): "agentteams://pinned-checkout",
            str(out): "orgrebase://oac-agent-evidence",
            str(temp_root): "orgrebase://oac-agent-runtime",
        }
        journal = LifecycleJournal(
            out / "action-journal.json",
            out / "raw-mcp",
            public_path_replacements=replacements,
        )
        with (
            ControlledLocalMatrix((worker,)) as matrix,
            _environment(
                {
                    "PATH": str(mc_bin) + os.pathsep + os.environ.get("PATH", ""),
                    "ORGREBASE_OAC_MC_LOG": str(mc_log),
                    "AGENTTEAMS_MATRIX_URL": matrix.url,
                    "AGENTTEAMS_WORKER_MATRIX_TOKEN": "controlled-local-token",
                    "AGENTTEAMS_SHARED_STORAGE_PREFIX": "agentteams/shared",
                    "MC_HOST_agentteams": "http://controlled:local@127.0.0.1:9000",
                    "AGENTTEAMS_AGENT_ROLE": "leader",
                }
            ),
        ):
            payload, entry = _invoke(
                module,
                journal,
                key="project:create",
                tool="projectflow",
                action="create_project",
                arguments={
                    "workspaceDir": str(runtime_root),
                    "payload": {
                        "projectId": project_id,
                        "title": "OAC enterprise intake mapping candidate",
                    },
                },
            )
            if payload.get("project", {}).get("status") != "active":
                raise NativeTaskflowError("OAC_AGENTTEAMS_PROJECT_CREATE_STATE_INVALID")
            actions.append(entry)
            node = {
                "taskId": task_id,
                "title": "Map admitted enterprise pack to candidate OAC paths",
                "assignedTo": worker,
                "dependsOn": [],
            }
            payload, entry = _invoke(
                module,
                journal,
                key="project:plan",
                tool="projectflow",
                action="plan_dag",
                arguments={
                    "workspaceDir": str(runtime_root),
                    "payload": {"projectId": project_id, "tasks": [node]},
                },
            )
            tasks = payload.get("project", {}).get("tasks", [])
            if [item.get("task_id") for item in tasks] != [task_id]:
                raise NativeTaskflowError("OAC_AGENTTEAMS_PLAN_TASK_INVALID")
            actions.append(entry)
            payload, entry = _invoke(
                module,
                journal,
                key="project:ready",
                tool="projectflow",
                action="ready_nodes",
                arguments={
                    "workspaceDir": str(runtime_root),
                    "payload": {"projectId": project_id},
                },
            )
            if [item.get("task_id") for item in payload.get("readyNodes", [])] != [task_id]:
                raise NativeTaskflowError("OAC_AGENTTEAMS_READY_TASK_INVALID")
            actions.append(entry)
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:delegate",
                tool="taskflow",
                action="delegate_task",
                arguments={
                    "role": "leader",
                    "workspaceDir": str(runtime_root),
                    "payload": {
                        "projectId": project_id,
                        "taskId": task_id,
                        "assignedTo": worker,
                        "roomId": room_id,
                        "spec": spec_text,
                    },
                },
            )
            _require_task_status(payload, "assigned", "delegate")
            notification = payload.get("notification")
            if (
                payload.get("synced") is not True
                or not isinstance(notification, Mapping)
                or notification.get("sent") is not True
                or not notification.get("eventId")
            ):
                raise NativeTaskflowError("OAC_AGENTTEAMS_DELEGATION_INCOMPLETE")
            assignment_event_id = str(notification["eventId"])
            actions.append(entry)
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:ack",
                tool="taskflow",
                action="ack_task",
                arguments={
                    "role": "worker",
                    "workspaceDir": str(runtime_root),
                    "payload": {"taskId": task_id},
                },
            )
            _require_task_status(payload, "in_progress", "ack")
            if (
                payload.get("pulled") is not True
                or payload.get("synced") is not True
                or payload.get("spec") != spec_text + "\n"
            ):
                raise NativeTaskflowError("OAC_AGENTTEAMS_ACK_CONTEXT_ROUNDTRIP_FAILED")
            actions.append(entry)

            request = _model_request(
                adaptation_run_id=adaptation_run_id,
                task_id=task_id,
                profile_digest=profile.digest,
                pack_digest=runtime.pack_digest,
                input_digest=input_digest,
            )
            selected_provider = provider or VertexAIStructuredProvider(
                prompt_payload=prompt,
                project_id=vertex_project,
                model_id=VERTEX_MODEL_ID,
                observer=ModelAttemptObserver(out / "model-attempts"),
            )
            try:
                response = selected_provider.generate_structured(
                    request=request,
                    output_model=OACAgentMappingCandidate,
                )
            except Exception as exc:  # provider bugs must fail closed, never abort authority checks
                response = _provider_failure_response(request, exc)
            observation = _model_observation(
                response=response,
                input_digest=input_digest,
                output_schema_digest=output_schema_digest,
            )
            accepted: tuple[CandidateSemanticMapping, ...] = ()
            if (
                response.status == "VALID"
                and response.schema_valid
                and response.value is not None
                and str(response.evidence_class) == "LIVE_MODEL"
                and bool(response.provider_request_id)
            ):
                try:
                    candidate = OACAgentMappingCandidate.model_validate(response.value)
                    validation, accepted = validate_oac_agent_candidate(
                        candidate=candidate,
                        profile=profile,
                        runtime=runtime,
                        baseline_mappings=baseline,
                        adaptation_run_id=adaptation_run_id,
                        task_id=task_id,
                        provider_response_digest=response.digest,
                    )
                except Exception as exc:
                    validation = OACAgentValidationReceipt(
                        verdict="HOLD",
                        checks=(
                            OACAgentValidationCheck(
                                check_id="candidate-revalidation",
                                passed=False,
                                reason_code=f"CANDIDATE_REVALIDATION_FAILED_{type(exc).__name__.upper()}",
                            ),
                        ),
                        reason_codes=(f"CANDIDATE_REVALIDATION_FAILED_{type(exc).__name__.upper()}",),
                    )
            else:
                reason = response.error_code or (
                    "LIVE_PROVIDER_EVIDENCE_REQUIRED"
                    if response.status == "VALID"
                    else f"PROVIDER_{response.status}"
                )
                validation = OACAgentValidationReceipt(
                    verdict="HOLD",
                    checks=(
                        OACAgentValidationCheck(
                            check_id="live-provider-response",
                            passed=False,
                            reason_code=reason,
                        ),
                    ),
                    reason_codes=(reason,),
                )
            status = "VALIDATED_CANDIDATE" if validation.verdict == "PASS" else "HOLD"
            result_payload = {
                "schema_version": "orgrebase.oac-agentteams-mapping-task-result.v1",
                "status": status,
                "adaptation_run_id": adaptation_run_id,
                "model_observation_digest": observation.digest,
                "validation_digest": validation.digest,
                "accepted_mapping_set_digest": validation.accepted_mapping_set_digest,
                "candidate_only": True,
                "canonical_target_writes": 0,
            }
            result_text = _canonical_bytes(result_payload).decode("utf-8")
            submitted_result_digest = _digest_bytes(result_text.encode("utf-8"))
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:submit",
                tool="taskflow",
                action="submit_task",
                arguments={
                    "role": "worker",
                    "workspaceDir": str(runtime_root),
                    "payload": {
                        "taskId": task_id,
                        "status": "SUCCESS",
                        "summary": result_text,
                        "deliverables": [],
                    },
                },
            )
            _require_task_status(payload, "submitted", "submit")
            if payload.get("synced") is not True:
                raise NativeTaskflowError("OAC_AGENTTEAMS_SUBMIT_SYNC_FAILED")
            actions.append(entry)
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:check",
                tool="taskflow",
                action="check_task",
                arguments={
                    "role": "leader",
                    "workspaceDir": str(runtime_root),
                    "payload": {"taskId": task_id},
                },
            )
            _require_task_status(payload, "submitted", "check")
            if payload.get("effective") is not True or payload.get("validationErrors") != []:
                raise NativeTaskflowError("OAC_AGENTTEAMS_CHECK_NOT_EFFECTIVE")
            observed_result = str(payload.get("result", {}).get("summary") or "")
            observed_result_digest = _digest_bytes(observed_result.encode("utf-8"))
            if observed_result_digest != submitted_result_digest:
                raise NativeTaskflowError("OAC_AGENTTEAMS_RESULT_ROUNDTRIP_FAILED")
            actions.append(entry)
            payload, entry = _invoke(
                module,
                journal,
                key=f"{task_id}:accept",
                tool="projectflow",
                action="accept_task_result",
                arguments={
                    "workspaceDir": str(runtime_root),
                    "payload": {
                        "projectId": project_id,
                        "taskId": task_id,
                        "resultStatus": "SUCCESS",
                        "accepted": True,
                    },
                },
            )
            if payload.get("accepted") is not True or payload.get("nodeStatus") != "completed":
                raise NativeTaskflowError("OAC_AGENTTEAMS_ACCEPT_FAILED")
            actions.append(entry)
            payload, entry = _invoke(
                module,
                journal,
                key="project:complete",
                tool="projectflow",
                action="complete_project",
                arguments={
                    "workspaceDir": str(runtime_root),
                    "payload": {"projectId": project_id},
                },
            )
            project_terminal_state = str(payload.get("project", {}).get("status") or "")
            if project_terminal_state != "completed":
                raise NativeTaskflowError("OAC_AGENTTEAMS_PROJECT_NOT_COMPLETED")
            actions.append(entry)
            matrix_request_count = matrix.state.request_count

    lifecycle = OACAgentTeamsMappingLifecycle(
        agentteams_version=source_identity["agentteams_version"],
        agentteams_commit=str(source_verification["commit"]),
        source_lock_digest=str(source_verification["source_lock_digest"]),
        run_id=adaptation_run_id,
        project_id=project_id,
        task_id=task_id,
        context_digest=context_digest,
        task_spec_digest=task_spec_digest,
        submitted_result_digest=submitted_result_digest,
        observed_result_digest=observed_result_digest,
        actions=tuple(actions),
        action_sequence=tuple(str(item["action"]) for item in actions),
        matrix_request_count=matrix_request_count,
        assignment_event_id=assignment_event_id,
    )
    accepted_mapping_set_digest = sha256_digest([item.digest for item in accepted]) if accepted else None
    receipt = OACAgentMappingReceipt(
        status=status,
        adaptation_run_id=adaptation_run_id,
        profile_digest=profile.digest,
        pack_digest=runtime.pack_digest,
        baseline_mapping_set_digest=baseline_mapping_set_digest,
        input_digest=input_digest,
        model_observation=observation,
        native_agentteams=lifecycle,
        validation=validation,
        accepted_mappings=accepted,
        accepted_mapping_set_digest=accepted_mapping_set_digest,
    )
    _write_json(out / "model-observation.json", observation.model_dump(mode="json"))
    _write_json(
        out / "model-attempt-observations.json",
        provider_observations(
            selected_provider,
            run_ref=adaptation_run_id,
        ),
    )
    provider_binding = getattr(selected_provider, "runtime_binding", None)
    transport_retry = provider_binding.get("transport_retry") if isinstance(provider_binding, Mapping) else None
    if transport_retry is not None:
        _write_json(out / "model-transport-attempts.json", {
            "schema_version": "orgrebase.model-transport-attempts.v1",
            "run_id": adaptation_run_id, "transport_retry": transport_retry,
            "canonical_target_writes": 0,
        })
    _write_json(out / "validation.json", validation.model_dump(mode="json"))
    _write_json(
        out / "deterministic-baseline.json",
        {
            "schema_version": "orgrebase.oac-deterministic-mapping-baseline.v1",
            "mapping_set_digest": baseline_mapping_set_digest,
            "mappings": [item.model_dump(mode="json") for item in baseline],
            "claim_boundary": "KEYLESS_BASELINE_NOT_LIVE_AGENT_EVIDENCE",
            "canonical_target_writes": 0,
        },
    )
    _write_json(out / "mapping-receipt.json", receipt.model_dump(mode="json"))
    _write_json(out / "source-verification.json", source_verification)
    _assert_public_evidence_safe(out, forbidden_secret_values)
    reseal_oac_agent_adaptation_evidence(
        out,
        forbidden_secret_values=forbidden_secret_values,
    )
    return receipt


def require_verified_agent_mapping_receipt(
    receipt: Mapping[str, Any] | OACAgentMappingReceipt,
    *,
    adaptation_run_id: str,
    profile_digest: str,
    pack_digest: str,
    baseline_mapping_set_digest: str,
) -> OACAgentMappingReceipt:
    """Check receipt integrity and lineage, including for historical reads.

    New execution or admission must additionally require the current source
    and implementation; persisted evidence does not expire when code changes.
    """

    selected = (
        receipt.revalidated()
        if isinstance(receipt, OACAgentMappingReceipt)
        else OACAgentMappingReceipt.model_validate(dict(receipt))
    )
    if selected.status != "VALIDATED_CANDIDATE":
        raise OACAgentAdaptationError("OAC_AGENT_MAPPING_RECEIPT_NOT_VALIDATED")
    if (
        selected.adaptation_run_id != adaptation_run_id
        or selected.profile_digest != profile_digest
        or selected.pack_digest != pack_digest
        or selected.baseline_mapping_set_digest != baseline_mapping_set_digest
    ):
        raise OACAgentAdaptationError("OAC_AGENT_MAPPING_RECEIPT_BINDING_MISMATCH")
    if (
        selected.canonical_target_writes != 0
        or selected.human_approval_granted
        or selected.oac_source_admitted
    ):
        raise OACAgentAdaptationError("OAC_AGENT_MAPPING_RECEIPT_AUTHORITY_EXPANDED")
    return selected


__all__ = (
    "AGENT_PRINCIPAL",
    "MAPPING_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "OACAgentAdaptationError",
    "OACAgentComponentProposal",
    "OACAgentMappingCandidate",
    "OACAgentMappingReceipt",
    "OACAgentModelObservation",
    "OACAgentTeamsMappingLifecycle",
    "OACAgentValidationReceipt",
    "build_oac_agent_prompt",
    "expected_oac_agent_candidate",
    "require_current_agent_mapping_source",
    "require_verified_agent_mapping_receipt",
    "reseal_oac_agent_adaptation_evidence",
    "run_oac_agent_adaptation",
    "validate_oac_agent_candidate",
)
