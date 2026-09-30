"""Default-off, post-Apply Quote diagnostic evaluation on the S05 consumer.

An unpublished candidate may be exercised here without release authority. The
durable selection and use receipts are evaluation evidence only: they cannot
be replayed as a production invocation or promoted into a business outcome.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import (
    AuthenticationError,
    authorize,
    current_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace import quote_pattern_bridge
from orgrebase.workspace.change_recovery import requests, resume_record
from orgrebase.workspace.pattern_evolution import GovernedPatternService, _EffectiveSkillRegistry
from orgrebase.workspace.quote_pattern_bridge import (
    PROFILE_ID,
    build_quote_recovery_case,
    quote_recovery_consumer_input,
)
from orgrebase.workspace.quote_recovery_learning import TARGET_SKILL
from orgrebase.workspace.skill_packages import InvocationContext

POLICY_ENVIRONMENT_VARIABLE = "ORGREBASE_QUOTE_RECOVERY_EVALUATION_CONFIG"
SELECTION_MEDIA = "application/vnd.orgrebase.quote-recovery-evaluation-selection.v1+json"
USE_MEDIA = "application/vnd.orgrebase.quote-recovery-evaluation-use.v1+json"
INVOCATION_MEDIA = "application/vnd.orgrebase.quote-recovery-evaluation-invocation.v1+json"
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_NEXT_STEP_RUBRIC = {
    "schema_version": "orgrebase.quote-recovery-next-step-rubric.v1",
    "profile_id": PROFILE_ID,
    "source": "current-business-event-request-resume-outcome",
    "handoff_condition": "APPLIED_WITH_BOUND_OUTCOME",
    "otherwise": "ABSTAIN_UNTIL_EXISTING_OUTCOME_OR_REVIEW",
    "claim_ceiling": "ACTION_CONSISTENCY_ONLY_NOT_SKILL_IMPROVEMENT",
}
NEXT_STEP_RUBRIC_DIGEST = sha256_digest(_NEXT_STEP_RUBRIC)


class QuoteRecoveryEvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.quote-recovery-evaluation-policy.v1"]
    enabled: bool = False
    policy_revision: str = Field(pattern=_IDENTIFIER)
    organization_id: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(pattern=_IDENTIFIER)
    candidate_ref: str = Field(min_length=1, max_length=512)
    corpus_actor_id: str = Field(min_length=1, max_length=256)
    author_actor_id: str = Field(min_length=1, max_length=256)
    evaluator_actor_id: str = Field(min_length=1, max_length=256)
    governor_actor_id: str = Field(min_length=1, max_length=256)
    not_after_epoch: int = Field(gt=0)
    max_total_attempts: int = Field(ge=1, le=1000)
    max_attempts_per_case: int = Field(ge=1, le=32)

    @model_validator(mode="after")
    def independent_evaluator(self) -> QuoteRecoveryEvaluationPolicy:
        if len({self.corpus_actor_id, self.author_actor_id,
                self.evaluator_actor_id, self.governor_actor_id}) != 4:
            raise ValueError("QUOTE_EVALUATION_PHASE_ACTORS_NOT_DISTINCT")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class QuoteRecoveryEvaluationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_key: str = Field(pattern=_IDENTIFIER)
    event_id: str = Field(pattern=_IDENTIFIER)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    candidate_ref: str = Field(min_length=1, max_length=512)
    previous_selection_ref: str | None = None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("QUOTE_EVALUATION_POLICY_DUPLICATE_KEY")
        result[key] = value
    return result


def load_quote_recovery_evaluation_policy(path: Path) -> QuoteRecoveryEvaluationPolicy:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65_536:
                raise IntegrityError("QUOTE_EVALUATION_POLICY_INVALID")
            raw = stream.read(65_537)
        return QuoteRecoveryEvaluationPolicy.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except IntegrityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise IntegrityError("QUOTE_EVALUATION_POLICY_INVALID") from exc


def _policy_path(config_path: Path | None) -> Path:
    if config_path is not None:
        return config_path
    configured = os.environ.get(POLICY_ENVIRONMENT_VARIABLE, "")
    if not configured:
        raise IntegrityError("QUOTE_EVALUATION_DISABLED")
    return Path(configured)


def _require_policy(path: Path, frozen: QuoteRecoveryEvaluationPolicy, now: Callable[[], float]) -> None:
    current = load_quote_recovery_evaluation_policy(path)
    if current.digest != frozen.digest or not current.enabled or now() >= current.not_after_epoch:
        raise AuthenticationError("QUOTE_EVALUATION_POLICY_CHANGED", 403)


def _principal(workspace: Any, policy: QuoteRecoveryEvaluationPolicy) -> tuple[str, str]:
    principal = request_principal.get()
    check = current_authorization()
    if principal is None or check is None:
        raise AuthenticationError("QUOTE_EVALUATION_CURRENT_AUTHORIZATION_REQUIRED")
    authorize(principal, "govern", policy.organization_id)
    check()
    if (
        principal.actor_id != policy.evaluator_actor_id
        or principal.tenant_id != workspace.profile.organization_id
    ):
        raise AuthorizationError("QUOTE_EVALUATION_EVALUATOR_REQUIRED")
    return principal.actor_id, sha256_digest({
        "issuer": principal.issuer, "subject": principal.subject,
        "tenant_id": principal.tenant_id, "actor_id": principal.actor_id,
    })


def _selection_ref(workspace: Any, command: QuoteRecoveryEvaluationCommand) -> str:
    return "quote-recovery-eval-selection:" + sha256_digest({
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": PROFILE_ID,
        "event_id": command.event_id,
        "attempt_id": command.attempt_id,
    })[7:]


def _use_ref(selection_ref: str) -> str:
    return "quote-recovery-eval-use:" + selection_ref.rsplit(":", 1)[1]


def _invocation_ref(receipt: dict[str, Any]) -> str:
    return "quote-recovery-eval-invocation:" + receipt["digest"].removeprefix("sha256:")


def _verify_stored_invocation(
    workspace: Any, ref: str, *, receipt_digest: str,
    input_digest: str, package_digest: str, output_digest: str,
) -> None:
    if not isinstance(ref, str) or not ref.startswith("quote-recovery-eval-invocation:"):
        raise IntegrityError("QUOTE_EVALUATION_INVOCATION_REF_INVALID")
    receipt = workspace.store.load_artifact(ref, INVOCATION_MEDIA).payload
    if (
        receipt.get("digest") != receipt_digest
        or receipt_digest != sha256_digest({
            key: value for key, value in receipt.items() if key != "digest"
        })
        or ref != _invocation_ref(receipt)
        or receipt.get("authorization_mode") != "EVALUATION"
        or receipt.get("release_receipt_digest") is not None
        or receipt.get("input_digest") != input_digest
        or receipt.get("package_digest") != package_digest
        or receipt.get("output_digest") != output_digest
        or receipt.get("target_writes") != 0
    ):
        raise IntegrityError("QUOTE_EVALUATION_INVOCATION_BINDING_INVALID")


def _validate_replay(
    selection: dict[str, Any], command: QuoteRecoveryEvaluationCommand,
    policy: QuoteRecoveryEvaluationPolicy, identity_digest: str,
) -> None:
    if (
        selection.get("operation_key") != command.operation_key
        or selection.get("candidate_ref") != command.candidate_ref
        or selection.get("previous_selection_ref") != command.previous_selection_ref
        or selection.get("policy_digest") != policy.digest
        or selection.get("principal_identity_digest") != identity_digest
    ):
        raise IntegrityError("QUOTE_EVALUATION_ATTEMPT_IDENTITY_CONFLICT")


def _independent_next_step(workspace: Any, event_id: str) -> dict[str, Any]:
    """Derive an owner handoff gate from canonical receipts, never candidate text."""
    event = workspace.changes.get(event_id)
    history = requests(workspace, event_id)
    if not history:
        raise IntegrityError("QUOTE_EVALUATION_RECOVERY_REQUEST_REQUIRED")
    request = history[-1]
    resume = resume_record(workspace, event_id, request)
    if resume is None:
        raise IntegrityError("QUOTE_EVALUATION_RECOVERY_RESUME_REQUIRED")
    detail = quote_pattern_bridge.change_detail(workspace, event_id)
    outcome = (detail.get("outcome") or {}).get("artifact_digest")
    if outcome is not None and (not isinstance(outcome, str) or _DIGEST.fullmatch(outcome) is None):
        raise IntegrityError("QUOTE_EVALUATION_OUTCOME_DIGEST_INVALID")
    if (
        request.get("event_digest") != event.digest
        or resume.get("event_digest") != event.digest
        or request.get("workspace_id") != workspace.store.workspace_id
        or not event.owner_id
    ):
        raise IntegrityError("QUOTE_EVALUATION_ORACLE_SOURCE_INVALID")
    complete = detail.get("status") == "APPLIED" and outcome is not None
    basis = {
        "event_digest": event.digest,
        "request_digest": request["digest"],
        "resume_digest": resume["digest"],
        "required_evidence_refs_digest": sha256_digest(sorted(request["required_evidence_refs"])),
        "owner_id_digest": sha256_digest(event.owner_id),
        "business_status": detail.get("status"),
        "outcome_artifact_digest": outcome,
    }
    cluster_id = "quote-recovery-source-cluster:" + sha256_digest({
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "object_id": event.proposal.id,
        "source_refs": sorted(event.proposal.source_refs),
    })[7:]
    return {
        "expected_action": "HANDOFF" if complete else "ABSTAIN",
        "next_step_code": "OWNER_REVIEW_HANDOFF" if complete else "WAIT_FOR_EXISTING_OUTCOME",
        "basis_digest": sha256_digest(basis),
        "rubric_digest": NEXT_STEP_RUBRIC_DIGEST,
        "case_cluster_id": cluster_id,
        "cluster_status": "PROVISIONAL_NOT_INDEPENDENCE_PROOF",
        "outcome_artifact_digest": outcome,
        "request_digest": request["digest"],
        "resume_digest": resume["digest"],
    }


def compare_quote_next_step_actions(
    oracle: dict[str, Any], baseline: dict[str, Any], candidate: dict[str, Any],
) -> str:
    """A reason/hash/wording change never counts as a behavior improvement."""
    if oracle.get("rubric_digest") != NEXT_STEP_RUBRIC_DIGEST:
        raise IntegrityError("QUOTE_EVALUATION_RUBRIC_INVALID")
    before, after = baseline.get("action"), candidate.get("action")
    if before == after:
        return "NO_BEHAVIOR_DELTA"
    if after == oracle["expected_action"] and before != oracle["expected_action"]:
        return "ACTION_FIX_REQUIRES_INDEPENDENT_REVIEW"
    return "REGRESSION_OR_UNDETERMINED"


def _read_use(workspace: Any, selection: dict[str, Any]) -> dict[str, Any]:
    try:
        use = workspace.store.load_artifact(_use_ref(selection["selection_ref"]), USE_MEDIA).payload
    except KeyError:
        return {
            "status": "RESULT_UNKNOWN", "action": None,
            "reason": None, "oracle_status": "NOT_EVALUATED",
            "use_ref": None, "qualification_status": "NOT_QUALIFIED",
            "quality_status": "NOT_EVALUATED",
            "behavior_delta_status": "NOT_EVALUATED",
            "pair_gate_status": "NOT_EVALUATED",
            "baseline_action": None,
            "baseline_invocation_receipt_digest": None,
            "baseline_invocation_ref": None,
            "baseline_output_digest": None,
            "candidate_invocation_receipt_digest": None,
            "candidate_invocation_ref": None,
            "candidate_output_digest": None,
            "baseline_loaded_resource_digests": [],
            "baseline_consumed_resource_digests": [],
            "interpreter_invocations": None,
        }
    if (
        use.get("schema_version") != "orgrebase.quote-recovery-evaluation-use.v1"
        or use.get("selection_ref") != selection["selection_ref"]
        or use.get("selection_digest") != sha256_digest(selection)
        or use.get("execution_mode") != "EVALUATION_ONLY"
        or use.get("target_writes") != 0
        or use.get("qualification_status") != "NOT_QUALIFIED"
        or (
            "paired_input_digest" in use
            and (
                use["paired_input_digest"] != selection.get("input_digest")
                or (
                    use.get("status") == "CONSUMED"
                    and (
                        use.get("interpreter_invocations") != 2
                        or _DIGEST.fullmatch(use.get("baseline_invocation_receipt_digest") or "") is None
                        or _DIGEST.fullmatch(use.get("invocation_receipt_digest") or "") is None
                        or not use.get("baseline_invocation_ref")
                        or not use.get("candidate_invocation_ref")
                        or use.get("pair_gate_status") not in {
                            "REJECTED_NO_BEHAVIOR_DELTA",
                            "REJECTED_REGRESSION_OR_UNDETERMINED",
                            "PENDING_INDEPENDENT_REVIEW",
                        }
                    )
                )
            )
        )
    ):
        raise IntegrityError("QUOTE_EVALUATION_USE_BINDING_INVALID")
    if "paired_input_digest" in use and use.get("status") == "CONSUMED":
        _verify_stored_invocation(
            workspace, use["baseline_invocation_ref"],
            receipt_digest=use["baseline_invocation_receipt_digest"],
            input_digest=selection["input_digest"],
            package_digest=selection["baseline_package_digest"],
            output_digest=use["baseline_output_digest"],
        )
        _verify_stored_invocation(
            workspace, use["candidate_invocation_ref"],
            receipt_digest=use["invocation_receipt_digest"],
            input_digest=selection["input_digest"],
            package_digest=selection["package_digest"],
            output_digest=use["output_digest"],
        )
    return {
        "status": use["status"], "action": use.get("action"),
        "reason": use.get("reason"), "oracle_status": use["oracle_status"],
        "use_ref": _use_ref(selection["selection_ref"]),
        "qualification_status": "NOT_QUALIFIED",
        "quality_status": "NOT_EVALUATED",
        "behavior_delta_status": use.get("behavior_delta_status", "NOT_EVALUATED"),
        "pair_gate_status": use.get("pair_gate_status", "NOT_EVALUATED"),
        "baseline_action": use.get("baseline_action"),
        "baseline_invocation_receipt_digest": use.get("baseline_invocation_receipt_digest"),
        "baseline_invocation_ref": use.get("baseline_invocation_ref"),
        "baseline_output_digest": use.get("baseline_output_digest"),
        "candidate_invocation_receipt_digest": use.get("invocation_receipt_digest"),
        "candidate_invocation_ref": use.get("candidate_invocation_ref"),
        "candidate_output_digest": use.get("output_digest"),
        "interpreter_invocations": use.get("interpreter_invocations"),
        "paired_input_digest": use.get("paired_input_digest"),
        "baseline_loaded_resource_digests": use.get("baseline_loaded_resource_digests", []),
        "baseline_consumed_resource_digests": use.get("baseline_consumed_resource_digests", []),
        "loaded_resource_digests": use.get("loaded_resource_digests", []),
        "consumed_resource_digests": use.get("consumed_resource_digests", []),
    }


def _projection(workspace: Any, selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.quote-recovery-evaluation-projection.v1",
        "selection_ref": selection["selection_ref"],
        "operation_key": selection["operation_key"],
        "event_id": selection["event_id"],
        "attempt_id": selection["attempt_id"],
        "previous_selection_ref": selection["previous_selection_ref"],
        "case_revision_digest": selection["case_revision_digest"],
        "case_cluster_id": selection["case_cluster_id"],
        "cluster_status": selection["cluster_status"],
        "outcome_artifact_digest": selection["outcome_artifact_digest"],
        "next_step_code": selection["next_step_code"],
        "next_step_rubric_digest": selection["next_step_rubric_digest"],
        "candidate_ref": selection["candidate_ref"],
        "candidate_digest": selection["candidate_digest"],
        "input_digest": selection["input_digest"],
        "package_digest": selection["package_digest"],
        "base_head_ref": selection.get("base_head_ref"),
        "base_head_digest": selection.get("base_head_digest"),
        "base_head_generation": selection.get("base_head_generation"),
        "baseline_package_digest": selection.get("baseline_package_digest"),
        "policy_revision": selection["policy_revision"],
        "execution_mode": "EVALUATION_ONLY",
        "business_effect": "NONE_POST_APPLY_DIAGNOSTIC_ONLY",
        "model_invocations": 0, "model_cost_usd": 0,
        **_read_use(workspace, selection),
    }


def read_quote_recovery_evaluation(workspace: Any, selection_ref: str) -> dict[str, Any]:
    """Read a frozen evaluation even after the evaluation policy was disabled."""
    principal = request_principal.get()
    check = current_authorization()
    if principal is None or check is None:
        raise AuthenticationError("QUOTE_EVALUATION_READ_AUTHORIZATION_REQUIRED")
    authorize(principal, "read", workspace.profile.organization_id)
    check()
    if not selection_ref.startswith("quote-recovery-eval-selection:"):
        raise IntegrityError("QUOTE_EVALUATION_SELECTION_REF_INVALID")
    selection = workspace.store.load_artifact(selection_ref, SELECTION_MEDIA).payload
    if (
        selection.get("selection_ref") != selection_ref
        or selection_ref != "quote-recovery-eval-selection:" + sha256_digest({
            "tenant_id": selection.get("organization_id"),
            "workspace_id": selection.get("workspace_id"),
            "profile_id": PROFILE_ID,
            "event_id": selection.get("event_id"),
            "attempt_id": selection.get("attempt_id"),
        })[7:]
        or selection.get("organization_id") != workspace.profile.organization_id
        or selection.get("workspace_id") != workspace.store.workspace_id
        or selection.get("execution_mode") != "EVALUATION_ONLY"
    ):
        raise IntegrityError("QUOTE_EVALUATION_SELECTION_INVALID")
    if principal.actor_id != selection.get("evaluator_actor_id"):
        raise AuthorizationError("QUOTE_EVALUATION_READER_NOT_EVALUATOR")
    check()
    return _projection(workspace, selection)


def _selection_page(workspace: Any) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = workspace.store.artifact_page(
            artifact_id_prefix="quote-recovery-eval-selection:",
            after=cursor, limit=100, expected_media_type=SELECTION_MEDIA,
        )
        records.extend(item.payload for item in page["items"])
        if len(records) > 1000:
            raise IntegrityError("QUOTE_EVALUATION_LEDGER_UNBOUNDED")
        cursor = page["next_cursor"]
        if cursor is None:
            return tuple(records)


def evaluate_quote_recovery_candidate(
    workspace: Any,
    command: QuoteRecoveryEvaluationCommand,
    *,
    config_path: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Freeze selection, run the exact bounded interpreter, then save use.

    The evaluator may test an unpublished candidate; no Pattern release or
    business handoff operation is written. An interrupted selected attempt is
    RESULT_UNKNOWN and can only be read back, never redispatched with a new ID.
    """
    command = QuoteRecoveryEvaluationCommand.model_validate(command.model_dump(mode="json"))
    path = _policy_path(config_path)
    policy = load_quote_recovery_evaluation_policy(path)
    if not policy.enabled or now() >= policy.not_after_epoch:
        raise IntegrityError("QUOTE_EVALUATION_DISABLED")
    if (
        policy.organization_id != workspace.profile.organization_id
        or policy.workspace_id != workspace.store.workspace_id
        or policy.candidate_ref != command.candidate_ref
    ):
        raise AuthorizationError("QUOTE_EVALUATION_SCOPE_DENIED")
    actor_id, identity_digest = _principal(workspace, policy)
    _require_policy(path, policy, now)
    selection_ref = _selection_ref(workspace, command)
    try:
        existing = workspace.store.load_artifact(selection_ref, SELECTION_MEDIA).payload
    except KeyError:
        existing = None
    if existing is not None:
        _validate_replay(existing, command, policy, identity_digest)
        return _projection(workspace, existing)

    case = build_quote_recovery_case(
        workspace, command.event_id, corpus_authority=policy.corpus_actor_id,
    )
    oracle = _independent_next_step(workspace, command.event_id)
    if (
        oracle["request_digest"] != case.certificate["evidence"].get("request_digest")
        or oracle["resume_digest"] != case.certificate["evidence"].get("resume_digest")
        or oracle["outcome_artifact_digest"]
        != case.certificate["evidence"].get("outcome_artifact_digest")
    ):
        raise IntegrityError("QUOTE_EVALUATION_ORACLE_CASE_MISMATCH")
    public_input = quote_recovery_consumer_input(case)
    run_id = "run:quote-evaluation-" + selection_ref.rsplit(":", 1)[1][:32]
    if run_id == public_input["run_id"]:
        raise IntegrityError("QUOTE_EVALUATION_NEW_RUN_REQUIRED")
    public_input["run_id"] = run_id
    service = GovernedPatternService(
        workspace.store,
        corpus_authority=policy.corpus_actor_id,
        evaluator_authority=policy.evaluator_actor_id,
        governance_authority=policy.governor_actor_id,
    )
    service._require_current_candidate_evidence(command.candidate_ref)
    candidate = service._load(command.candidate_ref, "skill-candidate")
    if (
        candidate.get("author_id") != policy.author_actor_id
        or candidate.get("skill_name") != TARGET_SKILL
        or not isinstance(candidate.get("content_bundle"), dict)
    ):
        raise IntegrityError("QUOTE_EVALUATION_CANDIDATE_SCOPE_INVALID")
    service._require_candidate_head(candidate)
    base_head = service._skill_head_capture(TARGET_SKILL)
    if base_head != (
        candidate.get("base_head_ref"), candidate.get("base_head_digest"),
        candidate.get("base_head_generation"), candidate["base_package_digest"],
    ):
        raise IntegrityError("QUOTE_EVALUATION_BASE_HEAD_CHANGED")
    baseline_package = service._package_for_digest(
        TARGET_SKILL, candidate["base_package_digest"]
    )
    baseline_registry = _EffectiveSkillRegistry(service.registry, baseline_package)
    overlay = service._overlay(candidate)
    package = overlay.load(TARGET_SKILL)
    if package.manifest.get("candidate_content_bundle", {}).get("digest") != candidate["content_bundle"]["digest"]:
        raise IntegrityError("QUOTE_EVALUATION_PACKAGE_CONTENT_INVALID")
    selection = {
        "schema_version": "orgrebase.quote-recovery-evaluation-selection.v1",
        "selection_ref": selection_ref,
        "organization_id": policy.organization_id,
        "workspace_id": policy.workspace_id,
        "profile_id": PROFILE_ID,
        "event_id": command.event_id,
        "attempt_id": command.attempt_id,
        "operation_key": command.operation_key,
        "previous_selection_ref": command.previous_selection_ref,
        "case_id": case.case_id,
        "case_cluster_id": oracle["case_cluster_id"],
        "cluster_status": oracle["cluster_status"],
        "case_revision_digest": case.revision,
        "case_certificate_digest": case.certificate["digest"],
        "outcome_artifact_digest": case.certificate["evidence"].get("outcome_artifact_digest"),
        "next_step_code": oracle["next_step_code"],
        "next_step_basis_digest": oracle["basis_digest"],
        "next_step_rubric_digest": oracle["rubric_digest"],
        "case_outcome": case.outcome,
        "run_id": run_id,
        "input_digest": sha256_digest(public_input),
        "candidate_ref": command.candidate_ref,
        "candidate_digest": candidate["digest"],
        "package_digest": package.package_digest,
        "base_head_ref": base_head[0],
        "base_head_digest": base_head[1],
        "base_head_generation": base_head[2],
        "baseline_package_digest": baseline_package.package_digest,
        "bundle_digest": candidate["content_bundle"]["digest"],
        "policy_revision": policy.policy_revision,
        "policy_digest": policy.digest,
        "evaluator_actor_id": actor_id,
        "principal_identity_digest": identity_digest,
        "execution_mode": "EVALUATION_ONLY",
        "target_writes": 0,
    }
    scope_lock = "quote-recovery-eval-scope:" + sha256_digest({
        "workspace_id": policy.workspace_id, "profile_id": PROFILE_ID,
    })[7:]
    with workspace.store.transaction() as connection:
        _principal(workspace, policy)
        _require_policy(path, policy, now)
        workspace.store.get_idempotent(
            scope_lock, sha256_digest(scope_lock), connection=connection,
        )
        try:
            concurrent = workspace.store.load_artifact(selection_ref, SELECTION_MEDIA).payload
        except KeyError:
            concurrent = None
        if concurrent is not None:
            _validate_replay(concurrent, command, policy, identity_digest)
            return _projection(workspace, concurrent)
        recorded = _selection_page(workspace)
        if len(recorded) >= policy.max_total_attempts:
            raise IntegrityError("QUOTE_EVALUATION_TOTAL_BUDGET_EXHAUSTED")
        siblings = [row for row in recorded if row.get("event_id") == command.event_id]
        if len(siblings) >= policy.max_attempts_per_case:
            raise IntegrityError("QUOTE_EVALUATION_CASE_BUDGET_EXHAUSTED")
        if siblings:
            referenced = {row.get("previous_selection_ref") for row in siblings}
            heads = [row for row in siblings if row.get("selection_ref") not in referenced]
            if len(heads) != 1 or command.previous_selection_ref != heads[0]["selection_ref"]:
                raise IntegrityError("QUOTE_EVALUATION_PREVIOUS_ATTEMPT_REQUIRED")
            if (
                heads[0]["case_revision_digest"] == case.revision
                or _read_use(workspace, heads[0])["status"] != "CONSUMED"
            ):
                raise IntegrityError("QUOTE_EVALUATION_PREDECESSOR_NOT_TERMINAL_OR_NEW")
        elif command.previous_selection_ref is not None:
            raise IntegrityError("QUOTE_EVALUATION_PREVIOUS_ATTEMPT_UNEXPECTED")
        operation_key = "quote-recovery-eval-operation:" + sha256_digest({
            "workspace_id": policy.workspace_id, "operation_key": command.operation_key,
        })[7:]
        operation_digest = sha256_digest(command.model_dump(mode="json"))
        if workspace.store.get_idempotent(
            operation_key, operation_digest, connection=connection,
        ) is not None:
            raise IntegrityError("QUOTE_EVALUATION_OPERATION_ALREADY_RESERVED")
        check = current_authorization()
        assert check is not None
        workspace.store.require_before_commit(connection, check)
        workspace.store.require_before_commit(
            connection, lambda: service._require_candidate_head(candidate)
        )
        workspace.store.save_idempotent(
            connection, operation_key, operation_digest, {"selection_ref": selection_ref},
        )
        workspace.store.save_artifact(connection, selection_ref, SELECTION_MEDIA, selection)
        workspace.store.append_event(connection, "QUOTE_RECOVERY_EVALUATION_SELECTED", {
            "selection_ref": selection_ref, "selection_digest": sha256_digest(selection),
            "case_id": case.case_id, "case_revision_digest": case.revision,
            "execution_mode": "EVALUATION_ONLY", "target_writes": 0,
        })

    # A crash after selection is intentionally ambiguous. The next caller sees
    # RESULT_UNKNOWN; it must not repeat the consumer with a different attempt.
    status = "CONSUMED"
    reason_code = None
    action = None
    reason = None
    oracle_status = "NOT_EVALUATED"
    loaded: list[str] = []
    consumed: list[str] = []
    receipt_digest = None
    output_digest = None
    baseline_action = None
    baseline_receipt_digest = None
    baseline_output_digest = None
    baseline_receipt: dict[str, Any] | None = None
    candidate_receipt: dict[str, Any] | None = None
    baseline_loaded: list[str] = []
    baseline_consumed: list[str] = []
    pair_gate_status = "NOT_EVALUATED"
    interpreter_invocations = 0
    behavior_delta_status = "NOT_EVALUATED"
    try:
        service._require_candidate_head(candidate)
        context = InvocationContext(
            run_id, public_input["task_id"], public_input["delegation_id"], actor_id,
        )
        interpreter_invocations += 1
        baseline_invocation = baseline_registry.invoke_for_evaluation(
            TARGET_SKILL, public_input, context=context,
            expected_package_digest=baseline_package.package_digest,
        )
        interpreter_invocations += 1
        invocation = overlay.invoke_for_evaluation(
            TARGET_SKILL, public_input, context=context,
            expected_package_digest=package.package_digest,
        )
        baseline_result = baseline_invocation.result
        baseline_trace = baseline_invocation.receipt.get("candidate_content")
        if (
            baseline_invocation.receipt.get("authorization_mode") != "EVALUATION"
            or baseline_invocation.receipt.get("input_digest") != selection["input_digest"]
            or baseline_invocation.receipt.get("package_digest")
            != selection["baseline_package_digest"]
            or baseline_invocation.receipt.get("output_digest")
            != sha256_digest(baseline_result)
            or baseline_result.get("target_writes") != 0
            or baseline_result.get("candidate_only") is not True
            or (
                baseline_package.manifest.get("candidate_content_bundle") is not None
                and (
                    not isinstance(baseline_trace, dict)
                    or baseline_trace.get("bundle_digest")
                    != baseline_package.manifest["candidate_content_bundle"]["digest"]
                    or not baseline_trace.get("consumed_resource_digests")
                    or not set(baseline_trace["consumed_resource_digests"]).issubset(
                        baseline_trace.get("loaded_resource_digests", [])
                    )
                )
            )
        ):
            raise IntegrityError("QUOTE_EVALUATION_BASELINE_CONSUMER_BINDING_INVALID")
        trace = invocation.receipt.get("candidate_content")
        if (
            invocation.receipt.get("authorization_mode") != "EVALUATION"
            or invocation.receipt.get("input_digest") != selection["input_digest"]
            or invocation.receipt.get("package_digest") != selection["package_digest"]
            or invocation.receipt.get("output_digest") != sha256_digest(invocation.result)
            or invocation.result.get("target_writes") != 0
            or invocation.result.get("candidate_only") is not True
            or not isinstance(trace, dict)
            or trace.get("bundle_digest") != selection["bundle_digest"]
        ):
            raise IntegrityError("QUOTE_EVALUATION_CONSUMER_BINDING_INVALID")
        loaded = list(trace["loaded_resource_digests"])
        consumed = list(trace["consumed_resource_digests"])
        if not consumed or not set(consumed).issubset(loaded):
            raise IntegrityError("QUOTE_EVALUATION_RESOURCE_NOT_CONSUMED")
        baseline_action = baseline_result["action"]
        baseline_receipt_digest = baseline_invocation.receipt["digest"]
        baseline_output_digest = baseline_invocation.receipt["output_digest"]
        baseline_receipt = baseline_invocation.receipt
        candidate_receipt = invocation.receipt
        if isinstance(baseline_trace, dict):
            baseline_loaded = list(baseline_trace["loaded_resource_digests"])
            baseline_consumed = list(baseline_trace["consumed_resource_digests"])
        action = invocation.result["action"]
        reason = invocation.result.get("reason")
        oracle_status = (
            "ACTION_MATCH_ONLY" if action == oracle["expected_action"]
            else "ACTION_MISMATCH"
        )
        behavior_delta_status = compare_quote_next_step_actions(
            oracle, baseline_result, invocation.result,
        )
        pair_gate_status = {
            "NO_BEHAVIOR_DELTA": "REJECTED_NO_BEHAVIOR_DELTA",
            "REGRESSION_OR_UNDETERMINED": "REJECTED_REGRESSION_OR_UNDETERMINED",
            "ACTION_FIX_REQUIRES_INDEPENDENT_REVIEW": "PENDING_INDEPENDENT_REVIEW",
        }[behavior_delta_status]
        receipt_digest = invocation.receipt["digest"]
        output_digest = invocation.receipt["output_digest"]
        _principal(workspace, policy)
        _require_policy(path, policy, now)
        service._require_current_candidate_evidence(command.candidate_ref)
        service._require_candidate_head(candidate)
        if service._load(command.candidate_ref, "skill-candidate")["digest"] != candidate["digest"]:
            raise IntegrityError("QUOTE_EVALUATION_CANDIDATE_CHANGED")
        current = build_quote_recovery_case(
            workspace, command.event_id, corpus_authority=policy.corpus_actor_id,
        )
        current_oracle = _independent_next_step(workspace, command.event_id)
        if current.digest != case.digest or current_oracle != oracle:
            raise IntegrityError("QUOTE_EVALUATION_CASE_CHANGED")
    except (AuthenticationError, AuthorizationError, IntegrityError, ValueError):
        status = "REJECTED"
        reason_code = "QUOTE_EVALUATION_RECHECK_FAILED"
        action = reason = None
        oracle_status = "NOT_EVALUATED"
        behavior_delta_status = "NOT_EVALUATED"
        pair_gate_status = "NOT_EVALUATED"
        baseline_action = baseline_receipt_digest = baseline_output_digest = None
        baseline_receipt = candidate_receipt = None
        baseline_loaded = baseline_consumed = []
        loaded = consumed = []
        receipt_digest = output_digest = None
    except Exception:
        status = "RESULT_UNKNOWN"
        reason_code = "QUOTE_EVALUATION_CONSUMER_UNKNOWN"
        action = reason = None
        oracle_status = "NOT_EVALUATED"
        behavior_delta_status = "NOT_EVALUATED"
        pair_gate_status = "NOT_EVALUATED"
        baseline_action = baseline_receipt_digest = baseline_output_digest = None
        baseline_receipt = candidate_receipt = None
        baseline_loaded = baseline_consumed = []
        loaded = consumed = []
        receipt_digest = output_digest = None
    use = {
        "schema_version": "orgrebase.quote-recovery-evaluation-use.v1",
        "selection_ref": selection_ref, "selection_digest": sha256_digest(selection),
        "execution_mode": "EVALUATION_ONLY",
        "status": status, "reason_code": reason_code,
        "action": action, "reason": reason,
        "oracle_status": oracle_status,
        "behavior_delta_status": behavior_delta_status,
        "pair_gate_status": pair_gate_status,
        "baseline_action": baseline_action,
        "baseline_invocation_receipt_digest": baseline_receipt_digest,
        "baseline_invocation_ref": (
            _invocation_ref(baseline_receipt) if baseline_receipt is not None else None
        ),
        "baseline_output_digest": baseline_output_digest,
        "baseline_loaded_resource_digests": baseline_loaded,
        "baseline_consumed_resource_digests": baseline_consumed,
        "paired_input_digest": selection["input_digest"],
        "interpreter_invocations": interpreter_invocations,
        "loaded_resource_digests": loaded,
        "consumed_resource_digests": consumed,
        "invocation_receipt_digest": receipt_digest,
        "candidate_invocation_ref": (
            _invocation_ref(candidate_receipt) if candidate_receipt is not None else None
        ),
        "output_digest": output_digest,
        "qualification_status": "NOT_QUALIFIED",
        "quality_status": "NOT_EVALUATED",
        "business_effect": "NONE_POST_APPLY_DIAGNOSTIC_ONLY",
        "model_invocations": 0, "model_cost_usd": 0,
        "target_writes": 0,
    }
    with workspace.store.transaction() as connection:
        if status == "CONSUMED":
            def revalidate_success() -> None:
                _principal(workspace, policy)
                _require_policy(path, policy, now)
                service._require_current_candidate_evidence(command.candidate_ref)
                service._require_candidate_head(candidate)
                if service._load(command.candidate_ref, "skill-candidate")["digest"] != candidate["digest"]:
                    raise IntegrityError("QUOTE_EVALUATION_CANDIDATE_CHANGED")
                current = build_quote_recovery_case(
                    workspace, command.event_id,
                    corpus_authority=policy.corpus_actor_id,
                )
                if (
                    current.digest != case.digest
                    or _independent_next_step(workspace, command.event_id) != oracle
                ):
                    raise IntegrityError("QUOTE_EVALUATION_CASE_CHANGED")

            revalidate_success()
            workspace.store.require_before_commit(connection, revalidate_success)
            assert baseline_receipt is not None and candidate_receipt is not None
            workspace.store.save_artifact(
                connection, _invocation_ref(baseline_receipt),
                INVOCATION_MEDIA, baseline_receipt,
            )
            workspace.store.save_artifact(
                connection, _invocation_ref(candidate_receipt),
                INVOCATION_MEDIA, candidate_receipt,
            )
        workspace.store.save_artifact(connection, _use_ref(selection_ref), USE_MEDIA, use)
        workspace.store.append_event(connection, "QUOTE_RECOVERY_EVALUATION_OBSERVED", {
            "selection_ref": selection_ref, "use_ref": _use_ref(selection_ref),
            "use_digest": sha256_digest(use), "status": status,
            "execution_mode": "EVALUATION_ONLY", "target_writes": 0,
        })
    return _projection(workspace, selection)


__all__ = (
    "QuoteRecoveryEvaluationCommand", "QuoteRecoveryEvaluationPolicy",
    "compare_quote_next_step_actions",
    "evaluate_quote_recovery_candidate", "load_quote_recovery_evaluation_policy",
    "read_quote_recovery_evaluation",
)
