"""Server-owned Finance adoption at the ordinary pre-Apply advisory boundary.

An installed or evaluated package does not authorize adoption.  The only
ordinary V4 path requires a qualified current head, a deployment policy for
this exact case cluster, a frozen Recall manifest and a durable preview attempt.
This module never changes Impact, Approval or Apply authority.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orgrebase.auth import request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.workspace.advisory import (
    DomainAdvisoryCandidate,
    WorkspaceApplyAdvisoryVerifier,
    WorkspaceChangeAdvisoryAdapter,
    finance_case_revision,
    finance_evaluation_advice_context,
)
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_contracts import MemorySnapshot
from orgrebase.workspace.experience_governance_operations import ExperiencePhaseActors, _recall
from orgrebase.workspace.experience_recall import SNAPSHOT_MEDIA
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID, VertexAIStructuredProvider
from orgrebase.workspace.models import ModelAdviceContextV4, ModelRequestV4, ModelResponseReceiptV4
from orgrebase.workspace.preview_execution import _attempt_key
from orgrebase.workspace.read_dependencies import validate_change_proposal_sources
from orgrebase.workspace.skill_evolution_v2 import PROFILE_ID, FinanceSkillHeadService
from orgrebase.workspace.vertex_candidate import build_vertex_advice_body

POLICY_ENV = "ORGREBASE_FINANCE_ADOPTION_POLICY_PATH"
SELECTION_MEDIA = "application/vnd.orgrebase.finance-adoption-selection.v1+json"
ATTEMPT_MEDIA = "application/vnd.orgrebase.finance-adoption-attempt.v1+json"
USE_MEDIA = "application/vnd.orgrebase.finance-adoption-use.v1+json"
SUCCESSION_MEDIA = "application/vnd.orgrebase.finance-adoption-succession.v1+json"
RECOVERY_HOLD_MEDIA = "application/vnd.orgrebase.finance-adoption-recovery-hold.v1+json"
_DIGEST = r"^sha256:[0-9a-f]{64}$"
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"


class FinanceAdoptionPolicy(BaseModel):
    """Deployment file, never a client request field or a package flag."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["orgrebase.finance-adoption-policy.v1"]
    mode: Literal["OFF", "SHADOW_ONLY", "ADOPTED"] = "OFF"
    revision: str = Field(pattern=_ID)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    profile_id: Literal["workspace-change-explanation-v1"] = PROFILE_ID
    head_ref: str = Field(min_length=1)
    head_digest: str = Field(pattern=_DIGEST)
    head_generation: int = Field(ge=1)
    package_digest: str = Field(pattern=_DIGEST)
    qualification_ref: str = Field(min_length=1)
    qualification_digest: str = Field(pattern=_DIGEST)
    content_release_ref: str = Field(min_length=1)
    cluster_digests: tuple[str, ...] = Field(min_length=1, max_length=100)
    runtime_actor_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    max_cases: int = Field(gt=0, le=100)
    max_calls: int = Field(gt=0, le=500)
    max_reserved_microusd: int = Field(gt=0)
    expires_at_epoch_s: int = Field(gt=0)

    @field_validator("cluster_digests", "runtime_actor_ids", mode="before")
    @classmethod
    def freeze_lists(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_sets(self):
        if (
            self.cluster_digests != tuple(sorted(set(self.cluster_digests)))
            or any(re.fullmatch(_DIGEST, value) is None for value in self.cluster_digests)
            or self.runtime_actor_ids != tuple(sorted(set(self.runtime_actor_ids)))
            or any(re.fullmatch(_ID, value) is None for value in self.runtime_actor_ids)
        ):
            raise ValueError("FINANCE_ADOPTION_POLICY_SET_INVALID")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("FINANCE_ADOPTION_POLICY_DUPLICATE_KEY")
        result[name] = value
    return result


def configured_policy() -> FinanceAdoptionPolicy | None:
    raw_path = os.environ.get(POLICY_ENV, "").strip()
    if not raw_path:
        return None
    try:
        descriptor = os.open(Path(raw_path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65_536:
                raise ValueError("FINANCE_ADOPTION_POLICY_FILE_INVALID")
            payload = stream.read(65_537)
        return FinanceAdoptionPolicy.model_validate(
            json.loads(payload, object_pairs_hook=_unique_pairs)
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise IntegrityError("FINANCE_ADOPTION_POLICY_INVALID") from exc


def finance_cluster_digest(workspace: Any, event_id: str, change_set: Any) -> str:
    """Stable conservative cohort unit for one admitted source change."""

    event = workspace.changes.get(event_id)
    return sha256_digest({
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "event_digest": event.digest,
        "source_ref": event.proposal.ref,
        "changed_objects": sorted(delta.object_id for delta in change_set.deltas),
    })


class FinanceSelectionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.finance-adoption-selection.v1"] = (
        "orgrebase.finance-adoption-selection.v1"
    )
    tenant_id: str
    workspace_id: str
    profile_id: Literal["workspace-change-explanation-v1"] = PROFILE_ID
    event_id: str
    cluster_digest: str = Field(pattern=_DIGEST)
    attempt_key: str
    run_id: str
    run_nonce: str
    task_id: str
    actor_id: str
    execution_mode: Literal["ADOPTED"] = "ADOPTED"
    change_set_ref: str
    change_set_digest: str = Field(pattern=_DIGEST)
    preview_ref: str
    preview_digest: str = Field(pattern=_DIGEST)
    case_revision: str = Field(pattern=_DIGEST)
    recovery_digest: str | None = Field(default=None, pattern=_DIGEST)
    policy_revision: str
    policy_digest: str = Field(pattern=_DIGEST)
    head_ref: str
    head_digest: str = Field(pattern=_DIGEST)
    head_generation: int = Field(ge=1)
    package_digest: str = Field(pattern=_DIGEST)
    qualification_ref: str
    qualification_digest: str = Field(pattern=_DIGEST)
    snapshot_ref: str
    snapshot_digest: str = Field(pattern=_DIGEST)
    manifest_ref: str
    manifest_digest: str = Field(pattern=_DIGEST)
    advice_digest: str = Field(pattern=_DIGEST)
    business_projection_digest: str = Field(pattern=_DIGEST)
    finance_request_digest: str = Field(pattern=_DIGEST)
    cost_contract_digest: str = Field(pattern=_DIGEST)
    reserved_calls: int = Field(gt=0)
    reserved_microusd: int = Field(gt=0)

    @property
    def ref(self) -> str:
        return ("finance-adoption-selection:" + self.policy_digest[7:]
                + ":" + sha256_digest(self.attempt_key)[7:])


@dataclass(frozen=True)
class PreparedAdoption:
    selection: FinanceSelectionReceipt
    advice: ModelAdviceContextV4
    adapter: WorkspaceChangeAdvisoryAdapter
    verifier: WorkspaceApplyAdvisoryVerifier
    policy: FinanceAdoptionPolicy
    fixture: Any
    change_set: Any
    preview: Any


def _require_policy_current(workspace: Any, expected: FinanceAdoptionPolicy) -> None:
    current = configured_policy()
    if current is None or current.mode != "ADOPTED" or current.digest != expected.digest:
        raise IntegrityError("FINANCE_ADOPTION_POLICY_CHANGED")
    if workspace._wall_clock_epoch_ms() >= current.expires_at_epoch_s * 1000:
        raise IntegrityError("FINANCE_ADOPTION_POLICY_EXPIRED")
    if (current.tenant_id != workspace.profile.organization_id
            or current.workspace_id != workspace.store.workspace_id):
        raise IntegrityError("FINANCE_ADOPTION_POLICY_SCOPE_MISMATCH")


def _require_head(workspace: Any, policy: FinanceAdoptionPolicy):
    head_service = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id
    )
    current = head_service.resolve()
    source = workspace.store.get_object(head_service.head_id)
    if (
        current.qualification_status != "QUALIFIED"
        or current.generation < 1
        or current.head_ref != policy.head_ref
        or current.head_digest != policy.head_digest
        or current.generation != policy.head_generation
        or current.package_digest != policy.package_digest
        or source.payload.get("qualification_ref") != policy.qualification_ref
        or source.payload.get("content_release_ref") != policy.content_release_ref
        or source.payload.get("qualification_status") != "QUALIFIED"
    ):
        raise IntegrityError("FINANCE_ADOPTION_CURRENT_QUALIFICATION_REQUIRED")
    from orgrebase.workspace.finance_skill_qualification import (
        CONTENT_RELEASE_MEDIA,
        QUALIFICATION_MEDIA,
        verify_current_release_for_adoption,
    )
    qualification = workspace.store.load_artifact(policy.qualification_ref, QUALIFICATION_MEDIA)
    release = workspace.store.load_artifact(policy.content_release_ref, CONTENT_RELEASE_MEDIA)
    if (
        qualification.payload_digest != policy.qualification_digest
        or qualification.payload.get("status") != "QUALIFIED"
        or qualification.payload.get("candidate_bundle_digest") != current.bundle.digest
        or qualification.payload.get("candidate_package_digest") != current.package_digest
        or release.payload.get("qualification_ref") != policy.qualification_ref
        or release.payload.get("qualification_digest") != policy.qualification_digest
        or release.payload.get("candidate_bundle_digest") != current.bundle.digest
        or release.payload.get("candidate_package_digest") != current.package_digest
        or release.payload.get("adoption_enabled") is not False
    ):
        raise IntegrityError("FINANCE_ADOPTION_QUALIFICATION_EVIDENCE_INVALID")
    # This default is owned by the independent qualification service. It
    # currently fails closed until sealed-family trial lineage is verifiable.
    # A service-local override is only a controlled mechanism test seam.
    verifier = getattr(
        workspace, "finance_adoption_qualification_verifier",
        verify_current_release_for_adoption,
    )
    if not callable(verifier):
        raise IntegrityError("FINANCE_ADOPTION_INDEPENDENT_VERIFIER_INVALID")
    verifier(
        workspace,
        expected_head_ref=current.head_ref,
        expected_head_digest=current.head_digest,
        expected_package_digest=current.package_digest,
        qualification_ref=policy.qualification_ref,
        qualification_digest=policy.qualification_digest,
        content_release_ref=policy.content_release_ref,
    )
    return current


def _require_actor(workspace: Any, policy: FinanceAdoptionPolicy, *, action: str) -> str:
    principal = request_principal.get()
    if (principal is None or principal.tenant_id != workspace.profile.organization_id
            or workspace.store.tenant_id != workspace.profile.organization_id):
        raise IntegrityError("FINANCE_ADOPTION_TENANT_PRINCIPAL_REQUIRED")
    actor_id = require_action(workspace, action)
    if action == "propose" and actor_id not in policy.runtime_actor_ids:
        raise IntegrityError("FINANCE_ADOPTION_ACTOR_NOT_ADMITTED")
    return actor_id


def _clone_adapter(
    base: WorkspaceChangeAdvisoryAdapter, advice: ModelAdviceContextV4,
    validator: Any,
) -> WorkspaceChangeAdvisoryAdapter:
    adapted = WorkspaceChangeAdvisoryAdapter(
        quote_object_id=base.quote_object_id, provider=base.provider,
        tenant_id=base.tenant_id, workspace_id=base.workspace_id,
        model_id=base.model_id, expected_model_id=base.expected_model_id,
        reasoning_effort=base.reasoning_effort,
        max_output_tokens=base.max_output_tokens, max_calls=base.max_calls,
        max_total_output_tokens=base.max_total_output_tokens,
        max_total_input_bytes=base.max_total_input_bytes,
        max_elapsed_seconds=base.max_elapsed_seconds,
        max_parallel_tasks=base.max_parallel_tasks,
        native_config=base.native_config, native_required=base.native_required,
        finance_advice=advice, validate_finance_advice=validator,
        finance_adoption_gate=validator,
    )
    adapted.recovery_context = base.recovery_context
    return adapted


def _finance_task(adapter: WorkspaceChangeAdvisoryAdapter, fixture: Any,
                  change_set: Any, preview: Any):
    plan, _ = adapter.compile(fixture=fixture, change_set=change_set, preview=preview)
    matches = tuple(task for task in plan.tasks if task.authority_domain == "finance")
    if len(matches) != 1:
        return None
    return matches[0]


def _attempt_ref(attempt_key: str) -> str:
    return "finance-adoption-attempt:" + sha256_digest(attempt_key)[7:]


def _event_attempt_guard(
    workspace: Any, event_id: str, *, recovery_request: dict[str, Any] | None = None,
) -> tuple[str, str]:
    identity = {
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": PROFILE_ID,
        "event_id": event_id,
    }
    if recovery_request is not None:
        identity["recovery_request_digest"] = recovery_request["digest"]
    return "finance-adoption-event:" + sha256_digest(identity)[7:], sha256_digest(identity)


def _successor_ref(attempt_key: str) -> str:
    return "finance-adoption-succession:" + sha256_digest(attempt_key)[7:]


def _successor_context(
    workspace: Any, event_id: str, *, require_resume: bool = True,
) -> dict[str, Any] | None:
    """Check an exact, owner-requested recovery before granting another dispatch identity.

    The business guard remains bound to the first event/attempt. A recovery
    request alone is not authority to retry an in-flight or unknown send.
    """
    from orgrebase.workspace.change_recovery import latest_request, resume_record
    from orgrebase.workspace.preview_execution import read_attempt_summary

    request = latest_request(workspace, event_id)
    if request is None:
        return None
    resume = resume_record(workspace, event_id, request)
    if require_resume and resume is None:
        raise IntegrityError("FINANCE_ADOPTION_RECOVERY_EVIDENCE_REQUIRED")
    round_number = request["round"]
    predecessor_command = (
        f"change:{event_id}:recovery:{round_number - 1}"
        if round_number > 1 else f"change:{event_id}"
    )
    predecessor_key = _attempt_key(
        predecessor_command,
        workspace.effective_workflow_run_id,
        workspace.workflow_run_nonce,
    )
    predecessor_request = None
    if round_number > 1:
        from orgrebase.workspace.change_recovery import requests
        chain = requests(workspace, event_id)
        predecessor_request = chain[round_number - 2]
        if resume_record(workspace, event_id, predecessor_request) is None:
            raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_RECOVERY_MISSING")
    prior_guard_key, prior_guard_digest = _event_attempt_guard(
        workspace, event_id, recovery_request=predecessor_request,
    )
    prior_guard = workspace.store.get_idempotent(prior_guard_key, prior_guard_digest)
    if prior_guard != {"attempt_key": predecessor_key}:
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_ATTEMPT_MISSING")
    selection = _read_selection(workspace, predecessor_key)
    if selection is None or selection.event_id != event_id:
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_SELECTION_MISSING")
    summary = read_attempt_summary(workspace, command=predecessor_command)
    if summary is None or summary["state"] not in {"COMPLETE", "FAILED"}:
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_NOT_TERMINAL")
    if summary["state"] != request["preserved_context"].get("attempt_state"):
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_STATE_CHANGED")
    use = workspace.store.load_artifact(use_ref(selection), USE_MEDIA)
    if (use.payload.get("selection_ref") != selection.ref
            or use.payload.get("selection_digest") != selection.digest):
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_USE_INVALID")
    previous_preview_ref = request["preserved_context"].get("previous_preview_artifact_id")
    previous_preview_digest = request["preserved_context"].get("previous_preview_artifact_digest")
    if summary["state"] == "COMPLETE":
        if not previous_preview_ref or not previous_preview_digest:
            raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_PREVIEW_MISSING")
        from orgrebase.workspace.models import WorkspacePreviewBundle
        from orgrebase.workspace.service import WORKSPACE_PREVIEW_MEDIA_TYPE
        previous_preview = workspace.store.load_artifact(
            previous_preview_ref, WORKSPACE_PREVIEW_MEDIA_TYPE,
        )
        bundle = WorkspacePreviewBundle.model_validate(previous_preview.payload)
        if (previous_preview.payload_digest != previous_preview_digest
                or selection_for_bundle(workspace, event_id, bundle) != selection):
            raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_PREVIEW_CHANGED")
        verify_use(workspace, selection, bundle.advisory)
    elif (previous_preview_ref is not None or use.payload.get("status") != "FAILED"
          or not summary["receipt_summaries"]
          or any(item["dispatch_state"] != "NOT_SENT"
                 for item in summary["receipt_summaries"])):
        # A FAILED exception without an explicit NOT_SENT receipt does not
        # prove that the provider did no work. Keep the old identity frozen.
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_DISPATCH_UNCERTAIN")
    context = {
        "schema_version": "orgrebase.finance-adoption-succession.v1",
        "event_id": event_id,
        "recovery_round": round_number,
        "recovery_request_digest": request["digest"],
        "recovery_resume_digest": resume["digest"] if resume is not None else None,
        "predecessor_attempt_key": predecessor_key,
        "predecessor_selection_ref": selection.ref,
        "predecessor_selection_digest": selection.digest,
        "predecessor_use_ref": use_ref(selection),
        "predecessor_use_digest": use.payload_digest,
        "predecessor_state": summary["state"],
        "predecessor_preview_ref": previous_preview_ref,
        "predecessor_preview_digest": previous_preview_digest,
    }
    return context


def _current_attempt_guard(
    workspace: Any, event_id: str,
) -> tuple[str, str, dict[str, Any] | None]:
    from orgrebase.workspace.change_recovery import latest_request

    request = latest_request(workspace, event_id)
    successor = _successor_context(workspace, event_id) if request is not None else None
    key, digest = _event_attempt_guard(
        workspace, event_id, recovery_request=request,
    )
    return key, digest, successor


def _require_business_lineage(
    workspace: Any, event_id: str, *, current_key: str,
    prior_business: dict[str, Any] | None, successor: dict[str, Any] | None,
) -> None:
    if prior_business is None:
        if successor is not None:
            raise IntegrityError("FINANCE_ADOPTION_BUSINESS_INPUT_ALREADY_BOUND")
        return
    if prior_business.get("event_id") != event_id:
        raise IntegrityError("FINANCE_ADOPTION_BUSINESS_INPUT_ALREADY_BOUND")
    if prior_business.get("attempt_key") == current_key:
        return
    original_key = _attempt_key(
        f"change:{event_id}",
        workspace.effective_workflow_run_id,
        workspace.workflow_run_nonce,
    )
    if (successor is None or prior_business.get("mode") != "ADOPTED"
            or prior_business.get("attempt_key") != original_key):
        raise IntegrityError("FINANCE_ADOPTION_EVENT_ATTEMPT_BOUND")


def _business_attempt_guard(workspace: Any, event_id: str) -> tuple[str, str]:
    """Bind a semantic change even when a caller invents a fresh event ID."""

    event = workspace.changes.get(event_id)
    identity = {
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": PROFILE_ID,
        "slot_id": event.slot_id,
        "operation": event.operation,
        "base_version": event.base_version,
        "base_digest": event.base_digest,
        "proposed_value": event.proposal.payload.get("canonical_value"),
        # Caller-written source labels, observation aliases and read-witness
        # wrappers are deliberately excluded. They cannot mint a new budget
        # identity for the same base object and requested business value.
    }
    digest = sha256_digest(identity)
    return "finance-adoption-business:" + digest[7:], digest


def _recovery_hold_ref(policy_digest: str, attempt_key: str) -> str:
    return ("finance-adoption-recovery-hold:" + policy_digest[7:]
            + ":" + sha256_digest(attempt_key)[7:])


def _policy_usage(workspace: Any, policy: FinanceAdoptionPolicy) -> dict[str, Any]:
    """Count selections and unconsumed recovery holds under the policy lock."""
    selected_rows = workspace.store.list_artifacts(
        artifact_id_prefix="finance-adoption-selection:" + policy.digest[7:] + ":",
        expected_media_type=SELECTION_MEDIA,
    )
    selections = tuple(FinanceSelectionReceipt.model_validate(row.payload).revalidated()
                       for row in selected_rows)
    if any(row.ref != artifact.artifact_id for row, artifact in zip(selections, selected_rows, strict=True)):
        raise IntegrityError("FINANCE_ADOPTION_QUOTA_LEDGER_INVALID")
    by_attempt = {row.attempt_key: row for row in selections}
    if len(by_attempt) != len(selections):
        raise IntegrityError("FINANCE_ADOPTION_QUOTA_LEDGER_INVALID")
    hold_rows = workspace.store.list_artifacts(
        artifact_id_prefix="finance-adoption-recovery-hold:" + policy.digest[7:] + ":",
        expected_media_type=RECOVERY_HOLD_MEDIA,
    )
    if len(selected_rows) + len(hold_rows) > 1100:
        raise IntegrityError("FINANCE_ADOPTION_QUOTA_LEDGER_UNBOUNDED")
    holds: dict[str, dict[str, Any]] = {}
    clusters = {row.cluster_digest for row in selections}
    calls = sum(row.reserved_calls for row in selections)
    microusd = sum(row.reserved_microusd for row in selections)
    for artifact in hold_rows:
        hold = artifact.payload
        attempt_key = hold.get("attempt_key")
        if (hold.get("schema_version") != "orgrebase.finance-adoption-recovery-hold.v1"
                or hold.get("policy_digest") != policy.digest
                or not isinstance(attempt_key, str)
                or artifact.artifact_id != _recovery_hold_ref(policy.digest, attempt_key)
                or hold.get("cluster_digest") not in policy.cluster_digests
                or type(hold.get("reserved_calls")) is not int or hold["reserved_calls"] <= 0
                or type(hold.get("reserved_microusd")) is not int or hold["reserved_microusd"] <= 0
                or attempt_key in holds):
            raise IntegrityError("FINANCE_ADOPTION_QUOTA_LEDGER_INVALID")
        holds[attempt_key] = hold
        selected = by_attempt.get(attempt_key)
        if selected is not None:
            if (selected.event_id != hold.get("event_id")
                    or selected.cluster_digest != hold["cluster_digest"]
                    or selected.recovery_digest != hold.get("recovery_resume_digest")
                    or selected.reserved_calls != hold["reserved_calls"]
                    or selected.reserved_microusd != hold["reserved_microusd"]
                    or selected.cost_contract_digest != hold.get("cost_contract_digest")):
                raise IntegrityError("FINANCE_ADOPTION_QUOTA_LEDGER_INVALID")
            continue
        clusters.add(hold["cluster_digest"])
        calls += hold["reserved_calls"]
        microusd += hold["reserved_microusd"]
    return {"selections": selections, "holds": holds,
            "clusters": clusters, "calls": calls, "microusd": microusd}


def require_finance_recovery_preflight(
    workspace: Any, event_id: str, *, fixture: Any,
    change_set: Any, preview: Any, immediate: bool,
    connection: Any, resume_digest: str,
) -> None:
    """Reserve a successor's family budget before evidence advances state."""
    from orgrebase.workspace.change_recovery import attempt_command

    business_key, business_digest = _business_attempt_guard(workspace, event_id)
    prior_business = workspace.store.get_idempotent(business_key, business_digest)
    if prior_business is None:
        return
    if prior_business.get("mode") != "ADOPTED":
        current_policy = configured_policy()
        if current_policy is not None and current_policy.mode == "ADOPTED":
            raise IntegrityError("FINANCE_ADOPTION_PRIOR_V3_EXPOSURE")
        return
    successor = _successor_context(workspace, event_id, require_resume=False)
    if successor is None:
        raise IntegrityError("FINANCE_ADOPTION_RECOVERY_REQUEST_REQUIRED")
    current_key = _attempt_key(
        attempt_command(workspace, event_id),
        workspace.effective_workflow_run_id, workspace.workflow_run_nonce,
    )
    _require_business_lineage(
        workspace, event_id, current_key=current_key,
        prior_business=prior_business, successor=successor,
    )
    prior_selection = _read_selection(workspace, successor["predecessor_attempt_key"])
    if prior_selection is None:
        raise IntegrityError("FINANCE_ADOPTION_PREDECESSOR_SELECTION_MISSING")
    policy = _current_policy_for_selection(workspace, prior_selection)
    if finance_cluster_digest(workspace, event_id, change_set) not in policy.cluster_digests:
        raise IntegrityError("FINANCE_ADOPTION_POLICY_CHANGED")
    _require_head(workspace, policy)
    if immediate:
        _require_actor(workspace, policy, action="propose")
    provider = getattr(workspace.advisory_factory, "provider", None)
    if not isinstance(provider, VertexAIStructuredProvider) or provider.model_budget is None:
        raise IntegrityError("FINANCE_ADOPTION_MODEL_PRICE_REQUIRED")
    workspace.advisory_factory.require_available()
    cost = workspace.advisory_factory.cost_reservation(
        fixture=fixture, change_set=change_set, preview=preview,
    )
    if cost is None or cost.get("calls", 0) <= 0 or cost.get("reserved_microusd", 0) <= 0:
        raise IntegrityError("FINANCE_ADOPTION_COST_RESERVATION_REQUIRED")
    cluster = finance_cluster_digest(workspace, event_id, change_set)
    hold = {
        "schema_version": "orgrebase.finance-adoption-recovery-hold.v1",
        "policy_digest": policy.digest,
        "policy_revision": policy.revision,
        "event_id": event_id,
        "attempt_key": current_key,
        "cluster_digest": cluster,
        "recovery_request_digest": successor["recovery_request_digest"],
        "recovery_resume_digest": resume_digest,
        "predecessor_attempt_key": successor["predecessor_attempt_key"],
        "predecessor_use_digest": successor["predecessor_use_digest"],
        "reserved_calls": cost["calls"],
        "reserved_microusd": cost["reserved_microusd"],
        "cost_contract_digest": sha256_digest(cost),
    }
    workspace.store.get_idempotent(
        "finance-adoption-policy-lock:" + policy.digest[7:],
        policy.digest, connection=connection,
    )
    usage = _policy_usage(workspace, policy)
    existing_hold = usage["holds"].get(current_key)
    if existing_hold is not None:
        if existing_hold != hold:
            raise IntegrityError("FINANCE_ADOPTION_RECOVERY_HOLD_CHANGED")
        return
    if (len(usage["clusters"] | {cluster}) > policy.max_cases
            or usage["calls"] + cost["calls"] > policy.max_calls
            or usage["microusd"] + cost["reserved_microusd"] > policy.max_reserved_microusd):
        raise IntegrityError("FINANCE_ADOPTION_POLICY_BUDGET_EXHAUSTED")
    workspace.store.save_artifact(
        connection, _recovery_hold_ref(policy.digest, current_key),
        RECOVERY_HOLD_MEDIA, hold,
    )


def _preview_mode_for_business(
    workspace: Any, event_id: str, change_set: Any,
    policy: FinanceAdoptionPolicy | None,
) -> str:
    return (
        "ADOPTED"
        if policy is not None and policy.mode == "ADOPTED"
        and finance_cluster_digest(workspace, event_id, change_set) in policy.cluster_digests
        else "LEGACY_V3"
    )


def reserve_finance_business_preview_identity(
    workspace: Any, connection: Any, *, event_id: str,
    fixture: Any, change_set: Any, preview: Any, envelope: Any,
    policy: FinanceAdoptionPolicy | None,
) -> None:
    """Serialize V3 and V4 identities before either path may dispatch."""

    if not any(
        item.domain == "finance" for item in fixture.objects
        if item.id in change_set.scope
    ):
        return
    from orgrebase.workspace.change_recovery import attempt_command

    attempt_key = _attempt_key(
        attempt_command(workspace, event_id), envelope.run_id, envelope.nonce,
    )
    key, digest = _business_attempt_guard(workspace, event_id)
    mode = _preview_mode_for_business(workspace, event_id, change_set, policy)
    prior = workspace.store.get_idempotent(key, digest, connection=connection)
    armed = policy is not None and policy.mode == "ADOPTED"
    vertex = isinstance(
        getattr(workspace.advisory_factory, "provider", None), VertexAIStructuredProvider,
    )
    if not armed and not vertex:
        # Deterministic and legacy V2 recovery keep the original semantics.
        return
    if not armed:
        # OFF-mode Vertex V3 writes a first-exposure marker for a future
        # rollout, but it does not change old V3 return/revision behavior.
        if prior is None:
            workspace.store.save_idempotent(
                connection, key, digest,
                {"event_id": event_id, "attempt_key": attempt_key, "mode": "LEGACY_V3"},
            )
        elif prior.get("mode") != "LEGACY_V3":
            raise IntegrityError("FINANCE_ADOPTION_BUSINESS_INPUT_ALREADY_BOUND")
        return
    if mode == "ADOPTED":
        _, _, successor = _current_attempt_guard(workspace, event_id)
        _require_business_lineage(
            workspace, event_id, current_key=attempt_key,
            prior_business=prior, successor=successor,
        )
        _require_head(workspace, policy)
        if not vertex or workspace.advisory_factory.provider.model_budget is None:
            raise IntegrityError("FINANCE_ADOPTION_MODEL_PRICE_REQUIRED")
        workspace.advisory_factory.require_available()
        if workspace.advisory_factory.cost_reservation(
            fixture=fixture, change_set=change_set,
            preview=preview,
        ) is None:
            raise IntegrityError("FINANCE_ADOPTION_COST_RESERVATION_REQUIRED")
    if prior is not None:
        if prior.get("mode") != mode or prior.get("event_id") != event_id:
            raise IntegrityError("FINANCE_ADOPTION_BUSINESS_INPUT_ALREADY_BOUND")
        return
    workspace.store.save_idempotent(
        connection, key, digest,
        {"event_id": event_id, "attempt_key": attempt_key, "mode": mode},
    )


def require_existing_finance_event_attempt(
    workspace: Any, *, event_id: str, change_set: Any, envelope: Any,
    policy: FinanceAdoptionPolicy | None,
) -> None:
    """Never fall back to V3 after this event has an adopted attempt."""

    business_key, business_digest = _business_attempt_guard(workspace, event_id)
    prior_business = workspace.store.get_idempotent(business_key, business_digest)
    if prior_business is not None and prior_business.get("mode") == "ADOPTED":
        guard_key, guard_digest, successor = _current_attempt_guard(workspace, event_id)
    else:
        guard_key, guard_digest = _event_attempt_guard(workspace, event_id)
        successor = None
    previous = workspace.store.get_idempotent(guard_key, guard_digest)
    armed = policy is not None and policy.mode == "ADOPTED"
    if (
        not armed and previous is None
        and prior_business is not None
        and prior_business.get("mode") == "LEGACY_V3"
    ):
        return
    from orgrebase.workspace.change_recovery import attempt_command

    current_key = _attempt_key(
        attempt_command(workspace, event_id), envelope.run_id, envelope.nonce,
    )
    _require_business_lineage(
        workspace, event_id, current_key=current_key,
        prior_business=prior_business, successor=successor,
    )
    current_mode = _preview_mode_for_business(workspace, event_id, change_set, policy)
    if armed and current_mode == "ADOPTED" and prior_business is not None and prior_business.get("mode") == "LEGACY_V3":
        raise IntegrityError("FINANCE_ADOPTION_PRIOR_V3_EXPOSURE")
    if prior_business is not None and prior_business.get("mode") not in {None, current_mode}:
        raise IntegrityError("FINANCE_ADOPTION_POLICY_CHANGED")
    if previous is None:
        if successor is None and prior_business is not None and prior_business.get("mode") == "ADOPTED":
            raise IntegrityError("FINANCE_ADOPTION_EVENT_SELECTION_MISSING")
        return
    attempt_key = current_key
    if previous.get("attempt_key") != attempt_key:
        raise IntegrityError("FINANCE_ADOPTION_EVENT_ATTEMPT_BOUND")
    selection = _read_selection(workspace, attempt_key)
    if selection is None:
        raise IntegrityError("FINANCE_ADOPTION_EVENT_SELECTION_MISSING")
    if (
        policy is None or policy.mode != "ADOPTED"
        or policy.digest != selection.policy_digest
        or finance_cluster_digest(workspace, event_id, change_set)
        != selection.cluster_digest
        or selection.cluster_digest not in policy.cluster_digests
    ):
        raise IntegrityError("FINANCE_ADOPTION_POLICY_CHANGED")


def _read_selection(workspace: Any, attempt_key: str) -> FinanceSelectionReceipt | None:
    try:
        binding = workspace.store.load_artifact(_attempt_ref(attempt_key), ATTEMPT_MEDIA)
    except KeyError:
        return None
    if (binding.payload.get("attempt_key") != attempt_key
            or binding.payload.get("schema_version") != "orgrebase.finance-adoption-attempt.v1"):
        raise IntegrityError("FINANCE_ADOPTION_ATTEMPT_BINDING_INVALID")
    selection_ref = binding.payload.get("selection_ref")
    selection = FinanceSelectionReceipt.model_validate(
        workspace.store.load_artifact(selection_ref, SELECTION_MEDIA).payload
    ).revalidated()
    if (selection.ref != selection_ref or selection.digest != binding.payload.get("selection_digest")
            or selection.attempt_key != attempt_key):
        raise IntegrityError("FINANCE_ADOPTION_SELECTION_BINDING_INVALID")
    if selection.recovery_digest is not None:
        from orgrebase.workspace.change_recovery import REQUEST_MEDIA, RESUME_MEDIA, _checked, _prefix
        from orgrebase.workspace.preview_execution import _MEDIA as PREVIEW_ATTEMPT_MEDIA
        from orgrebase.workspace.service import WORKSPACE_PREVIEW_MEDIA_TYPE

        try:
            successor = workspace.store.load_artifact(
                _successor_ref(attempt_key), SUCCESSION_MEDIA,
            )
            body = successor.payload
            number = body.get("recovery_round")
            if type(number) is not int or not 1 <= number <= 1000:
                raise IntegrityError("FINANCE_ADOPTION_SUCCESSION_BINDING_INVALID")
            prefix = _prefix(selection.event_id)
            request = _checked(workspace.store.load_artifact(
                prefix + f"request:{number:04d}", REQUEST_MEDIA,
            ).payload)
            resume = _checked(workspace.store.load_artifact(
                prefix + f"resume:{number:04d}", RESUME_MEDIA,
            ).payload)
            previous_command = (
                f"change:{selection.event_id}:recovery:{number - 1}"
                if number > 1 else f"change:{selection.event_id}"
            )
            previous_key = _attempt_key(previous_command, selection.run_id, selection.run_nonce)
            previous_binding = workspace.store.load_artifact(
                _attempt_ref(previous_key), ATTEMPT_MEDIA,
            ).payload
            previous_ref = previous_binding.get("selection_ref")
            previous_selection = FinanceSelectionReceipt.model_validate(
                workspace.store.load_artifact(previous_ref, SELECTION_MEDIA).payload,
            ).revalidated()
            previous_use_ref = use_ref(previous_selection)
            previous_use = workspace.store.load_artifact(previous_use_ref, USE_MEDIA)
            previous_attempt = workspace.store.load_artifact(
                previous_key, PREVIEW_ATTEMPT_MEDIA,
            ).payload
            context = request["preserved_context"]
            previous_preview_ref = context.get("previous_preview_artifact_id")
            previous_preview_digest = context.get("previous_preview_artifact_digest")
            if previous_preview_ref is not None:
                previous_preview = workspace.store.load_artifact(
                    previous_preview_ref, WORKSPACE_PREVIEW_MEDIA_TYPE,
                )
            else:
                previous_preview = None
        except KeyError as error:
            raise IntegrityError("FINANCE_ADOPTION_SUCCESSION_BINDING_MISSING") from error
        expected = {
            "schema_version": "orgrebase.finance-adoption-succession.v1",
            "event_id": selection.event_id,
            "recovery_round": number,
            "recovery_request_digest": request["digest"],
            "recovery_resume_digest": resume["digest"],
            "predecessor_attempt_key": previous_key,
            "predecessor_selection_ref": previous_selection.ref,
            "predecessor_selection_digest": previous_selection.digest,
            "predecessor_use_ref": previous_use_ref,
            "predecessor_use_digest": previous_use.payload_digest,
            "predecessor_state": previous_attempt.get("status"),
            "predecessor_preview_ref": previous_preview_ref,
            "predecessor_preview_digest": previous_preview_digest,
            "attempt_key": attempt_key,
            "selection_ref": selection.ref,
            "selection_digest": selection.digest,
        }
        if (body != expected
                or request.get("event_id") != selection.event_id
                or resume.get("event_id") != selection.event_id
                or request.get("round") != number or resume.get("round") != number
                or request.get("run_id") != selection.run_id
                or resume.get("request_digest") != request["digest"]
                or resume["digest"] != selection.recovery_digest
                or previous_binding.get("attempt_key") != previous_key
                or previous_binding.get("selection_digest") != previous_selection.digest
                or previous_selection.ref != previous_ref
                or previous_selection.event_id != selection.event_id
                or previous_selection.attempt_key != previous_key
                or previous_selection.run_id != selection.run_id
                or previous_selection.run_nonce != selection.run_nonce
                or previous_use.payload.get("selection_ref") != previous_selection.ref
                or previous_use.payload.get("selection_digest") != previous_selection.digest
                or previous_attempt.get("status") not in {"COMPLETE", "FAILED"}
                or context.get("attempt_state") != previous_attempt.get("status")
                or (previous_attempt.get("status") == "COMPLETE"
                    and (previous_preview is None
                         or previous_preview.payload_digest != previous_preview_digest
                         or previous_use.payload.get("finance_receipt_digest") is None))
                or (previous_attempt.get("status") == "FAILED"
                    and (previous_preview is not None
                         or previous_use.payload.get("status") != "FAILED"))):
            raise IntegrityError("FINANCE_ADOPTION_SUCCESSION_BINDING_INVALID")
    return selection


def _recall_for_workspace(workspace: Any):
    actors = ExperiencePhaseActors.from_deployment()
    return _recall(workspace, actors)


def _finance_recall_context_tags(workspace: Any, event_id: str) -> tuple[str, ...]:
    """Derive source-review only from this change's current controlled source.

    A caller's source_refs, label or proposed context tags cannot confer this
    condition. Source-backed proposals with stale coverage/revision and human
    proposals with stale formal read witnesses both fail selection rather than
    silently falling back to no-memory advice.
    """

    proposal = workspace.changes.get(event_id).proposal
    validate_change_proposal_sources(workspace, (proposal,), workspace.clock.now())
    if not proposal.version.startswith("source-"):
        return ("finance",)
    return ("finance", "finance:source-review")


def _frozen_advice(
    workspace: Any, selection: FinanceSelectionReceipt,
    *, change_set: Any, preview: Any, envelope: Any,
    action: Literal["propose", "approve", "execute"],
) -> tuple[ModelAdviceContextV4, Any]:
    _finance_recall_context_tags(workspace, selection.event_id)
    recall = _recall_for_workspace(workspace)
    if action in {"approve", "execute"}:
        manifest, text = recall.validate_frozen_manifest_for_approval(
            selection.manifest_ref, snapshot_ref=selection.snapshot_ref,
            purpose=PROFILE_ID, recipient="workspace-advisory",
            expected_manifest_digest=selection.manifest_digest,
            action=action,
        )
    else:
        manifest, text = recall.validate_manifest_for_consumption(
            selection.manifest_ref, snapshot_ref=selection.snapshot_ref,
            purpose=PROFILE_ID, recipient="workspace-advisory",
        )
    snapshot = MemorySnapshot.model_validate(
        workspace.store.load_artifact(selection.snapshot_ref, SNAPSHOT_MEDIA).payload
    ).revalidated()
    if snapshot.digest != selection.snapshot_digest:
        raise IntegrityError("FINANCE_ADOPTION_SNAPSHOT_CHANGED")
    resolution = _require_head(workspace, _current_policy_for_selection(workspace, selection))
    advice = finance_evaluation_advice_context(
        resolution=resolution, snapshot=snapshot, manifest=manifest,
        change_set=change_set, preview=preview, run_envelope=envelope,
        operation_id=selection.attempt_key, snapshot_ref=selection.snapshot_ref,
        manifest_ref=selection.manifest_ref, advice_text=text, execution_mode="ADOPTED",
    )
    if advice.digest != selection.advice_digest:
        raise IntegrityError("FINANCE_ADOPTION_ADVICE_CHANGED")
    return advice, manifest


def _current_policy_for_selection(workspace: Any, selection: FinanceSelectionReceipt) -> FinanceAdoptionPolicy:
    policy = configured_policy()
    if policy is None or policy.digest != selection.policy_digest or policy.revision != selection.policy_revision:
        raise IntegrityError("FINANCE_ADOPTION_POLICY_CHANGED")
    _require_policy_current(workspace, policy)
    return policy


def prepare_normal_adoption(
    workspace: Any, *, event_id: str, fixture: Any, change_set: Any,
    preview: Any, envelope: Any, base_adapter: WorkspaceChangeAdvisoryAdapter,
    recovery_digest: str | None,
) -> PreparedAdoption | None:
    """Prepare a new ordinary selection; caller persists it with reserve_attempt."""

    policy = configured_policy()
    if policy is None or policy.mode != "ADOPTED":
        return None
    cluster = finance_cluster_digest(workspace, event_id, change_set)
    if cluster not in policy.cluster_digests:
        return None
    _require_policy_current(workspace, policy)
    actor_id = _require_actor(workspace, policy, action="propose")
    if not isinstance(base_adapter.provider, VertexAIStructuredProvider):
        raise IntegrityError("FINANCE_ADOPTION_VERTEX_PROVIDER_REQUIRED")
    if (base_adapter.model_id != VERTEX_CANDIDATE_MODEL_ID
            or base_adapter.provider.model_budget is None):
        raise IntegrityError("FINANCE_ADOPTION_MODEL_PRICE_REQUIRED")
    base_adapter.require_available()
    current = _require_head(workspace, policy)
    task = _finance_task(base_adapter, fixture, change_set, preview)
    if task is None:
        raise IntegrityError("FINANCE_ADOPTION_FINANCE_TASK_REQUIRED")
    from orgrebase.workspace.change_recovery import attempt_command
    attempt_key = _attempt_key(
        attempt_command(workspace, event_id), envelope.run_id, envelope.nonce,
    )
    guard_key, guard_digest, _ = _current_attempt_guard(workspace, event_id)
    prior_attempt = workspace.store.get_idempotent(guard_key, guard_digest)
    if prior_attempt is not None and prior_attempt.get("attempt_key") != attempt_key:
        raise IntegrityError("FINANCE_ADOPTION_EVENT_ATTEMPT_BOUND")
    existing = _read_selection(workspace, attempt_key)
    if existing is not None:
        if (existing.change_set_digest != change_set.digest or existing.preview_digest != preview.digest
                or existing.recovery_digest != recovery_digest):
            raise IntegrityError("FINANCE_ADOPTION_ATTEMPT_INPUT_CHANGED")
        advice, _ = _frozen_advice(
            workspace, existing, change_set=change_set, preview=preview,
            envelope=envelope, action="propose",
        )
        def validator(value: ModelAdviceContextV4) -> None:
            require_selection_current(
                workspace, existing, value, change_set=change_set,
                preview=preview, envelope=envelope, action="propose",
            )
        adapter = _clone_adapter(base_adapter, advice, validator)
        return PreparedAdoption(
            existing, advice, adapter,
            WorkspaceApplyAdvisoryVerifier(adapter, clock=workspace.clock.now),
            policy, fixture, change_set, preview,
        )
    recall = _recall_for_workspace(workspace)
    context_tags = _finance_recall_context_tags(workspace, event_id)
    snapshot_ref = recall.build_snapshot(purpose=PROFILE_ID, recipient="workspace-advisory")
    context_digest = sha256_digest({
        "change_set_digest": change_set.digest, "preview_digest": preview.digest,
        "run_envelope_digest": envelope.digest, "recovery_digest": recovery_digest,
        "task_digest": task.digest,
    })
    case_revision = finance_case_revision(change_set, preview)
    manifest_ref, _ = recall.select_for_case(
        operation_id=attempt_key, snapshot_ref=snapshot_ref,
        task_id=task.id, attempt_id=attempt_key, context_digest=context_digest,
        case_id=change_set.id, case_revision=case_revision,
        evaluation_arm="ADOPTED", query_text="finance " + " ".join(
            delta.object_id for delta in change_set.deltas
        ),
        context_tags=context_tags,
        object_kinds=tuple(sorted({fixture.object(delta.object_id, delta.base_version).kind
                                   for delta in change_set.deltas})),
    )
    manifest, advice_text = recall.validate_manifest_for_consumption(
        manifest_ref, snapshot_ref=snapshot_ref, purpose=PROFILE_ID,
        recipient="workspace-advisory",
    )
    snapshot = MemorySnapshot.model_validate(
        workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload
    ).revalidated()
    advice = finance_evaluation_advice_context(
        resolution=current, snapshot=snapshot, manifest=manifest,
        change_set=change_set, preview=preview, run_envelope=envelope,
        operation_id=attempt_key, snapshot_ref=snapshot_ref,
        manifest_ref=manifest_ref, advice_text=advice_text, execution_mode="ADOPTED",
    )
    # A preliminary adapter is used only for pure request compilation and cost
    # preflight. The durable selection below is the dispatch authority.
    preliminary = _clone_adapter(base_adapter, advice, lambda _: None)
    request = preliminary._request(
        task=task, fixture=fixture, change_set=change_set, preview=preview,
        run_envelope=envelope, predecessors=(),
    )
    if not isinstance(request, ModelRequestV4):
        raise IntegrityError("FINANCE_ADOPTION_V4_REQUEST_REQUIRED")
    cost = preliminary.cost_reservation(
        fixture=fixture, change_set=change_set, preview=preview,
    )
    if (cost is None or type(cost.get("calls")) is not int
            or type(cost.get("reserved_microusd")) is not int
            or cost["calls"] <= 0 or cost["reserved_microusd"] <= 0):
        raise IntegrityError("FINANCE_ADOPTION_COST_RESERVATION_REQUIRED")
    selection = FinanceSelectionReceipt(
        tenant_id=workspace.profile.organization_id,
        workspace_id=workspace.store.workspace_id, event_id=event_id,
        cluster_digest=cluster, attempt_key=attempt_key,
        run_id=envelope.run_id, run_nonce=envelope.nonce,
        task_id=task.id, actor_id=actor_id,
        change_set_ref=f"{change_set.id}@{change_set.revision}",
        change_set_digest=change_set.digest,
        preview_ref=preview.id, preview_digest=preview.digest,
        case_revision=case_revision, recovery_digest=recovery_digest,
        policy_revision=policy.revision, policy_digest=policy.digest,
        head_ref=current.head_ref, head_digest=current.head_digest,
        head_generation=current.generation, package_digest=current.package_digest,
        qualification_ref=policy.qualification_ref,
        qualification_digest=policy.qualification_digest,
        snapshot_ref=snapshot_ref, snapshot_digest=snapshot.digest,
        manifest_ref=manifest_ref, manifest_digest=manifest.digest,
        advice_digest=advice.digest,
        business_projection_digest=request.business_projection_digest,
        finance_request_digest=request.digest,
        cost_contract_digest=sha256_digest(cost),
        reserved_calls=cost["calls"], reserved_microusd=cost["reserved_microusd"],
    )
    def validator(value: ModelAdviceContextV4) -> None:
        require_selection_current(
            workspace, selection, value, change_set=change_set,
            preview=preview, envelope=envelope, action="propose",
        )
    adapter = _clone_adapter(base_adapter, advice, validator)
    return PreparedAdoption(
        selection, advice, adapter,
        WorkspaceApplyAdvisoryVerifier(adapter, clock=workspace.clock.now),
        policy, fixture, change_set, preview,
    )


def reserve_selection(workspace: Any, connection: Any, prepared: PreparedAdoption,
                      *, expected_attempt_key: str) -> None:
    """Atomic with preview attempt reservation; limit the whole policy family."""

    selection = prepared.selection.revalidated()
    if selection.attempt_key != expected_attempt_key:
        raise IntegrityError("FINANCE_ADOPTION_ATTEMPT_BINDING_INVALID")
    _require_policy_current(workspace, prepared.policy)
    actor_id = _require_actor(workspace, prepared.policy, action="propose")
    head = _require_head(workspace, prepared.policy)
    if (
        selection.tenant_id != workspace.profile.organization_id
        or selection.workspace_id != workspace.store.workspace_id
        or selection.attempt_key != prepared.advice.operation_id
        or selection.actor_id != actor_id
        or selection.cluster_digest not in prepared.policy.cluster_digests
        or selection.cluster_digest != finance_cluster_digest(
            workspace, selection.event_id, prepared.change_set,
        )
        or selection.change_set_digest != prepared.change_set.digest
        or selection.preview_digest != prepared.preview.digest
        or selection.policy_digest != prepared.policy.digest
        or selection.policy_revision != prepared.policy.revision
        or selection.head_ref != head.head_ref
        or selection.head_digest != head.head_digest
        or selection.head_generation != head.generation
        or selection.package_digest != head.package_digest
        or selection.qualification_ref != prepared.policy.qualification_ref
        or selection.qualification_digest != prepared.policy.qualification_digest
        or selection.advice_digest != prepared.advice.digest
    ):
        raise IntegrityError("FINANCE_ADOPTION_SELECTION_SCOPE_INVALID")
    if prepared.adapter.finance_adoption_gate is None:
        raise IntegrityError("FINANCE_ADOPTION_VERIFIER_REQUIRED")
    prepared.adapter.finance_adoption_gate(prepared.advice)
    current_cost = prepared.adapter.cost_reservation(
        fixture=prepared.fixture, change_set=prepared.change_set,
        preview=prepared.preview,
    )
    if (
        current_cost is None
        or sha256_digest(current_cost) != selection.cost_contract_digest
        or current_cost.get("calls") != selection.reserved_calls
        or current_cost.get("reserved_microusd") != selection.reserved_microusd
    ):
        raise IntegrityError("FINANCE_ADOPTION_COST_CONTRACT_CHANGED")
    guard_key, guard_digest, successor = _current_attempt_guard(workspace, selection.event_id)
    business_key, business_digest = _business_attempt_guard(workspace, selection.event_id)
    prior_business = workspace.store.get_idempotent(
        business_key, business_digest, connection=connection,
    )
    _require_business_lineage(
        workspace, selection.event_id, current_key=selection.attempt_key,
        prior_business=prior_business, successor=successor,
    )
    prior_attempt = workspace.store.get_idempotent(
        guard_key, guard_digest, connection=connection,
    )
    if prior_attempt is not None and prior_attempt.get("attempt_key") != selection.attempt_key:
        raise IntegrityError("FINANCE_ADOPTION_EVENT_ATTEMPT_BOUND")
    # The stable idempotency lock serializes quota calculation across PG
    # processes; SQLite uses its transaction write lock.
    workspace.store.get_idempotent(
        "finance-adoption-policy-lock:" + prepared.policy.digest[7:],
        prepared.policy.digest, connection=connection,
    )
    usage = _policy_usage(workspace, prepared.policy)
    hold = usage["holds"].get(selection.attempt_key)
    if successor is not None:
        if (hold is None or hold.get("policy_digest") != selection.policy_digest
                or hold.get("policy_revision") != selection.policy_revision
                or hold.get("event_id") != selection.event_id
                or hold.get("cluster_digest") != selection.cluster_digest
                or hold.get("recovery_request_digest") != successor["recovery_request_digest"]
                or hold.get("recovery_resume_digest") != selection.recovery_digest
                or hold.get("predecessor_attempt_key") != successor["predecessor_attempt_key"]
                or hold.get("predecessor_use_digest") != successor["predecessor_use_digest"]
                or hold.get("reserved_calls") != selection.reserved_calls
                or hold.get("reserved_microusd") != selection.reserved_microusd
                or hold.get("cost_contract_digest") != selection.cost_contract_digest):
            raise IntegrityError("FINANCE_ADOPTION_RECOVERY_HOLD_MISSING")
    elif hold is not None:
        raise IntegrityError("FINANCE_ADOPTION_RECOVERY_HOLD_INVALID")
    existing = _read_selection(workspace, selection.attempt_key)
    if existing is not None:
        if existing.digest != selection.digest:
            raise IntegrityError("FINANCE_ADOPTION_SELECTION_CHANGED")
        if successor is not None:
            expected = {**successor, "attempt_key": selection.attempt_key,
                        "selection_ref": selection.ref, "selection_digest": selection.digest}
            stored = workspace.store.load_artifact(
                _successor_ref(selection.attempt_key), SUCCESSION_MEDIA,
            )
            if stored.payload != expected or stored.payload_digest != sha256_digest(expected):
                raise IntegrityError("FINANCE_ADOPTION_SUCCESSION_BINDING_INVALID")
        return
    extra_calls = 0 if successor is not None else selection.reserved_calls
    extra_microusd = 0 if successor is not None else selection.reserved_microusd
    if (
        len(usage["clusters"] | {selection.cluster_digest}) > prepared.policy.max_cases
        or usage["calls"] + extra_calls > prepared.policy.max_calls
        or usage["microusd"] + extra_microusd > prepared.policy.max_reserved_microusd
    ):
        raise IntegrityError("FINANCE_ADOPTION_POLICY_BUDGET_EXHAUSTED")
    if prior_attempt is None:
        workspace.store.save_idempotent(
            connection, guard_key, guard_digest,
            {"attempt_key": selection.attempt_key},
        )
    if prior_business is None:
        workspace.store.save_idempotent(
            connection, business_key, business_digest,
            {"event_id": selection.event_id, "attempt_key": selection.attempt_key,
             "mode": "ADOPTED"},
        )
    if successor is not None:
        body = {**successor, "attempt_key": selection.attempt_key,
                "selection_ref": selection.ref, "selection_digest": selection.digest}
        workspace.store.save_artifact(
            connection, _successor_ref(selection.attempt_key), SUCCESSION_MEDIA, body,
        )
    workspace.store.save_artifact(
        connection, selection.ref, SELECTION_MEDIA, selection.model_dump(mode="json")
    )
    workspace.store.save_artifact(connection, _attempt_ref(selection.attempt_key), ATTEMPT_MEDIA, {
        "schema_version": "orgrebase.finance-adoption-attempt.v1",
        "attempt_key": selection.attempt_key,
        "selection_ref": selection.ref,
        "selection_digest": selection.digest,
    })


def require_selection_current(
    workspace: Any, selection: FinanceSelectionReceipt, advice: ModelAdviceContextV4,
    *, change_set: Any, preview: Any, envelope: Any,
    action: Literal["propose", "approve", "execute"],
) -> None:
    selection = selection.revalidated()
    advice = advice.revalidated()
    policy = _current_policy_for_selection(workspace, selection)
    _require_actor(workspace, policy, action=action)
    if (
        selection.execution_mode != "ADOPTED"
        or selection.cluster_digest not in policy.cluster_digests
        or selection.head_ref != policy.head_ref
        or selection.head_digest != policy.head_digest
        or selection.head_generation != policy.head_generation
        or selection.package_digest != policy.package_digest
        or selection.qualification_ref != policy.qualification_ref
        or selection.qualification_digest != policy.qualification_digest
        or selection.change_set_digest != change_set.digest
        or selection.preview_digest != preview.digest
        or selection.run_id != envelope.run_id
        or selection.run_nonce != envelope.nonce
        or selection.case_revision != finance_case_revision(change_set, preview)
        or advice.digest != selection.advice_digest
        or advice.execution_mode != "ADOPTED"
    ):
        raise IntegrityError("FINANCE_ADOPTION_SELECTION_STALE")
    rebuilt, _ = _frozen_advice(
        workspace, selection, change_set=change_set, preview=preview,
        envelope=envelope, action=action,
    )
    if rebuilt.digest != advice.digest:
        raise IntegrityError("FINANCE_ADOPTION_SELECTION_STALE")
    # A price or transport-attempt change after the durable reservation must
    # stop dispatch. Provider preflight alone would otherwise send at a higher
    # cost while the policy family still counts the old reservation.
    from orgrebase.impact import ImpactEngine

    spec = workspace.change_spec(selection.event_id)
    fixture = workspace._fixture_for_change(spec)
    current_change_set = workspace.change_builder.build(fixture=fixture, spec=spec)
    current_preview = ImpactEngine(fixture).preview(current_change_set)
    if current_change_set.digest != change_set.digest or current_preview.digest != preview.digest:
        raise IntegrityError("FINANCE_ADOPTION_SELECTION_STALE")
    cost_adapter = _clone_adapter(workspace.advisory_factory, advice, lambda _: None)
    current_cost = cost_adapter.cost_reservation(
        fixture=fixture, change_set=change_set, preview=preview,
    )
    if (
        current_cost is None
        or sha256_digest(current_cost) != selection.cost_contract_digest
        or current_cost.get("calls") != selection.reserved_calls
        or current_cost.get("reserved_microusd") != selection.reserved_microusd
    ):
        raise IntegrityError("FINANCE_ADOPTION_COST_CONTRACT_CHANGED")


def adopted_adapter_for_preview(
    workspace: Any, *, event_id: str, bundle: Any,
    base_adapter: WorkspaceChangeAdvisoryAdapter,
    action: Literal["approve", "execute"] = "execute",
) -> tuple[WorkspaceChangeAdvisoryAdapter, WorkspaceApplyAdvisoryVerifier] | None:
    """Recreate one frozen V4 verifier for Apply; never select a new version."""

    from orgrebase.workspace.change_recovery import attempt_command
    attempt_key = _attempt_key(
        attempt_command(workspace, event_id),
        bundle.run_envelope.run_id, bundle.run_envelope.nonce,
    )
    selection = _read_selection(workspace, attempt_key)
    if selection is None:
        return None
    advice, _ = _frozen_advice(
        workspace, selection, change_set=bundle.change_set,
        preview=bundle.preview, envelope=bundle.run_envelope, action=action,
    )
    def validator(value: ModelAdviceContextV4) -> None:
        require_selection_current(
            workspace, selection, value, change_set=bundle.change_set,
            preview=bundle.preview, envelope=bundle.run_envelope, action=action,
        )
    adapted = _clone_adapter(base_adapter, advice, validator)
    return adapted, WorkspaceApplyAdvisoryVerifier(adapted, clock=workspace.clock.now)


def selection_for_bundle(workspace: Any, event_id: str, bundle: Any) -> FinanceSelectionReceipt | None:
    # A historical Preview keeps its original V4 operation identity even after
    # the current event advances to another recovery round.
    matches: list[FinanceSelectionReceipt] = []
    for handoff in bundle.advisory.handoffs:
        generated = handoff.payload.get("model_advisory")
        if not isinstance(generated, dict) or "request" not in generated:
            continue
        if generated["request"].get("contract_version") != "4":
            continue
        request = ModelRequestV4.model_validate(generated["request"]).revalidated()
        if request.advice_context.execution_mode != "ADOPTED":
            continue
        selection = _read_selection(workspace, request.advice_context.operation_id)
        if selection is None:
            raise IntegrityError("FINANCE_ADOPTION_PREVIEW_SELECTION_MISSING")
        if (selection.event_id != event_id or selection.task_id != handoff.task_id
                or selection.change_set_digest != bundle.change_set.digest
                or selection.preview_digest != bundle.preview.digest
                or selection.run_id != bundle.run_envelope.run_id
                or selection.run_nonce != bundle.run_envelope.nonce
                or selection.finance_request_digest != request.digest):
            raise IntegrityError("FINANCE_ADOPTION_PREVIEW_SELECTION_CHANGED")
        matches.append(selection)
    if len(matches) > 1:
        raise IntegrityError("FINANCE_ADOPTION_PREVIEW_SELECTION_AMBIGUOUS")
    return matches[0] if matches else None


def require_existing_preview_adoption(
    workspace: Any, event_id: str, bundle: Any,
    *, action: Literal["propose", "approve", "execute"],
) -> None:
    """A repeated command checks the frozen selection; it never reselects."""

    selection = selection_for_bundle(workspace, event_id, bundle)
    if selection is None:
        return
    advice, _ = _frozen_advice(
        workspace, selection, change_set=bundle.change_set,
        preview=bundle.preview, envelope=bundle.run_envelope, action=action,
    )
    require_selection_current(
        workspace, selection, advice, change_set=bundle.change_set,
        preview=bundle.preview, envelope=bundle.run_envelope, action=action,
    )
    verify_use(workspace, selection, bundle.advisory)


def use_payload(workspace: Any, selection: FinanceSelectionReceipt, advisory: Any) -> dict[str, Any]:
    """Record independently observable V4 wire and candidate; no efficacy claim."""

    finance = tuple(item for item in advisory.handoffs
                    if item.task_id == selection.task_id)
    if len(finance) != 1:
        raise IntegrityError("FINANCE_ADOPTION_FINANCE_HANDOFF_REQUIRED")
    payload = finance[0].payload
    if payload.get("target_writes") != 0 or payload.get("candidate_only") is not True:
        raise IntegrityError("FINANCE_ADOPTION_CANDIDATE_ONLY_REQUIRED")
    generated = payload.get("model_advisory")
    if not isinstance(generated, dict):
        raise IntegrityError("FINANCE_ADOPTION_MODEL_USE_REQUIRED")
    request = ModelRequestV4.model_validate(generated["request"]).revalidated()
    receipt = ModelResponseReceiptV4.model_validate(generated["receipt"]).revalidated()
    body = build_vertex_advice_body(request, DomainAdvisoryCandidate)
    if (
        request.digest != selection.finance_request_digest
        or request.advice_digest != selection.advice_digest
        or request.business_projection_digest != selection.business_projection_digest
        or request.advice_context.execution_mode != "ADOPTED"
        or request.advice_context.head_ref != selection.head_ref
        or request.advice_context.recall_manifest_digest != selection.manifest_digest
        or receipt.request_digest != request.digest
        or receipt.body_digest != sha256_digest(body)
        or receipt.status != "VALID"
        or receipt.dispatch_state != "RESPONSE_RECEIVED"
    ):
        raise IntegrityError("FINANCE_ADOPTION_USE_BINDING_INVALID")
    return {
        "schema_version": "orgrebase.finance-adoption-use.v1",
        "selection_ref": selection.ref,
        "selection_digest": selection.digest,
        "execution_mode": "ADOPTED",
        "finance_request_digest": request.digest,
        "finance_receipt_digest": receipt.digest,
        "wire_body_digest": receipt.body_digest,
        "business_projection_digest": request.business_projection_digest,
        "advice_digest": request.advice_digest,
        "candidate_digest": sha256_digest(receipt.value),
        "provider_request_id": receipt.provider_request_id,
        "usage_status": "OBSERVED" if receipt.input_tokens is not None and receipt.output_tokens is not None else "UNKNOWN",
        "target_writes": 0,
    }


def use_ref(selection: FinanceSelectionReceipt) -> str:
    return "finance-adoption-use:" + sha256_digest(selection.attempt_key)[7:]


def failed_use_payload(selection: FinanceSelectionReceipt, attempt_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Observe a failed/unknown attempt without claiming resource consumption."""

    state = (attempt_summary or {}).get("state")
    if state not in {"FAILED", "RESULT_UNKNOWN", "IN_PROGRESS"}:
        state = "RESULT_UNKNOWN"
    return {
        "schema_version": "orgrebase.finance-adoption-use.v1",
        "selection_ref": selection.ref,
        "selection_digest": selection.digest,
        "execution_mode": "ADOPTED",
        "status": state,
        "finance_request_digest": selection.finance_request_digest,
        "finance_receipt_digest": None,
        "wire_body_digest": None,
        "business_projection_digest": selection.business_projection_digest,
        "advice_digest": selection.advice_digest,
        "candidate_digest": None,
        "provider_request_id": None,
        "usage_status": "UNKNOWN",
        "public_error_code": (attempt_summary or {}).get("public_error_code"),
        "target_writes": 0,
    }


def verify_use(workspace: Any, selection: FinanceSelectionReceipt, advisory: Any) -> None:
    expected = use_payload(workspace, selection, advisory)
    saved = workspace.store.load_artifact(use_ref(selection), USE_MEDIA)
    if saved.payload != expected or saved.payload_digest != sha256_digest(expected):
        raise IntegrityError("FINANCE_ADOPTION_USE_CHANGED")
