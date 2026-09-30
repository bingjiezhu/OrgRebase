"""Default-off product entry for one approved Quote-recovery Skill on a new run."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orgrebase.auth import (
    AuthenticationError,
    current_authorization,
    request_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.pattern_evolution import GovernedPatternService
from orgrebase.workspace.pattern_governance import (
    PatternAdoptionPolicy,
    PatternAuthorityScope,
    PrincipalPatternGovernance,
)
from orgrebase.workspace.quote_pattern_bridge import (
    build_quote_recovery_case,
    make_quote_recovery_case_resolver,
    quote_recovery_consumer_input,
)
from orgrebase.workspace.skill_packages import InvocationContext

POLICY_ENVIRONMENT_VARIABLE = "ORGREBASE_QUOTE_RECOVERY_LEARNING_CONFIG"
OPERATION_INTENT_MEDIA_TYPE = (
    "application/vnd.orgrebase.quote-recovery-operation-intent+json"
)
_OPERATION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class QuoteRecoveryOperationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class QuoteRecoveryLearningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.quote-recovery-learning-policy.v1"]
    enabled: bool = False
    policy_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    organization_id: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    candidate_ref: str = Field(min_length=1, max_length=512)
    corpus_actor_id: str = Field(min_length=1, max_length=256)
    author_actor_id: str = Field(min_length=1, max_length=256)
    evaluator_actor_id: str = Field(min_length=1, max_length=256)
    governor_actor_id: str = Field(min_length=1, max_length=256)
    runtime_actor_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    required_knowledge_refs: tuple[str, ...] = Field(min_length=1, max_length=100)
    required_qualification_refs: tuple[str, ...] = Field(min_length=1, max_length=100)
    reviewed_bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reviewed_target_skill: Literal["structured-domain-handoff"]
    reviewed_predecessor_package_digest: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    adoption_not_after_epoch: int = Field(gt=0)

    @field_validator(
        "runtime_actor_ids",
        "required_knowledge_refs",
        "required_qualification_refs",
        mode="before",
    )
    @classmethod
    def freeze_lists(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_authority_sets(self) -> QuoteRecoveryLearningPolicy:
        authorities = (
            self.corpus_actor_id,
            self.author_actor_id,
            self.evaluator_actor_id,
            self.governor_actor_id,
        )
        if len(set(authorities)) != 4:
            raise ValueError("QUOTE_RECOVERY_POLICY_AUTHORITIES_NOT_DISTINCT")
        for values in (
            self.runtime_actor_ids,
            self.required_knowledge_refs,
            self.required_qualification_refs,
        ):
            if values != tuple(sorted(set(values))) or any(not value for value in values):
                raise ValueError("QUOTE_RECOVERY_POLICY_SET_INVALID")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class QuoteRecoveryNewRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
    )
    event_id: str = Field(min_length=1, max_length=256)
    candidate_ref: str = Field(min_length=1, max_length=512)
    new_run_id: str = Field(pattern=r"^run:[A-Za-z0-9][A-Za-z0-9_.:@-]{0,251}$")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("QUOTE_RECOVERY_POLICY_DUPLICATE_KEY")
        result[key] = value
    return result


def load_quote_recovery_learning_policy(path: Path) -> QuoteRecoveryLearningPolicy:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65_536:
                raise QuoteRecoveryOperationError("QUOTE_RECOVERY_POLICY_INVALID")
            raw = stream.read(65_537)
        return QuoteRecoveryLearningPolicy.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except QuoteRecoveryOperationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise QuoteRecoveryOperationError("QUOTE_RECOVERY_POLICY_INVALID") from exc


def _configured_policy_path() -> Path:
    raw = os.environ.get(POLICY_ENVIRONMENT_VARIABLE, "")
    if not raw:
        raise QuoteRecoveryOperationError("QUOTE_RECOVERY_LEARNING_DISABLED")
    return Path(raw)


def _artifact_ref(record: dict[str, Any]) -> str:
    return (
        f"pattern-evolution:{record['kind']}:"
        f"{str(record['digest']).removeprefix('sha256:')}"
    )


def _operation_storage(workspace: Any, operation_key: str) -> tuple[str, str]:
    identity = sha256_digest(
        {
            "workspace_id": workspace.store.workspace_id,
            "operation_key": operation_key,
        }
    ).removeprefix("sha256:")
    return (
        f"quote-recovery-operation:{identity}",
        f"quote-recovery-operation-intent:{identity}",
    )


def _principal_identity_digest(principal: Any) -> str:
    return sha256_digest(
        {
            "issuer": principal.issuer,
            "subject": principal.subject,
            "tenant_id": principal.tenant_id,
            "actor_id": principal.actor_id,
        }
    )


def _operation_request_digest(
    *,
    command: QuoteRecoveryNewRun,
    policy_digest: str,
    principal_identity_digest: str,
) -> str:
    return sha256_digest(
        {
            "schema_version": "orgrebase.quote-recovery-operation-request.v2",
            "policy_digest": policy_digest,
            "principal_identity_digest": principal_identity_digest,
            "command": command.model_dump(mode="json"),
        }
    )


def _validate_operation_intent(
    workspace: Any,
    intent: dict[str, Any],
    *,
    command: QuoteRecoveryNewRun | None = None,
    policy_digest: str | None = None,
    principal_identity_digest: str | None = None,
) -> None:
    required = {
        "schema_version",
        "operation_key",
        "request_digest",
        "policy_revision",
        "policy_digest",
        "principal_identity_digest",
        "organization_id",
        "workspace_id",
        "candidate_ref",
        "event_id",
        "run_id",
        "case_digest",
        "case_outcome",
        "case_certificate_digest",
        "input_digest",
        "state",
    }
    if (
        set(intent) != required
        or intent.get("schema_version")
        != "orgrebase.quote-recovery-operation-intent.v2"
        or intent.get("state") != "DISPATCH_RESERVED"
        or intent.get("organization_id") != workspace.profile.organization_id
        or intent.get("workspace_id") != workspace.store.workspace_id
        or intent.get("case_outcome")
        not in {"SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN"}
        or any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(intent.get(field))) is None
            for field in (
                "request_digest",
                "policy_digest",
                "principal_identity_digest",
                "case_digest",
                "case_certificate_digest",
                "input_digest",
            )
        )
    ):
        raise IntegrityError("QUOTE_RECOVERY_OPERATION_INTENT_INVALID")
    if command is not None and (
        intent.get("operation_key") != command.operation_key
        or intent.get("candidate_ref") != command.candidate_ref
        or intent.get("event_id") != command.event_id
        or intent.get("run_id") != command.new_run_id
    ):
        raise RuntimeError("IDEMPOTENCY_CONFLICT")
    if policy_digest is not None and intent.get("policy_digest") != policy_digest:
        raise IntegrityError("QUOTE_RECOVERY_OPERATION_POLICY_MISMATCH")
    if (
        principal_identity_digest is not None
        and intent.get("principal_identity_digest") != principal_identity_digest
    ):
        raise AuthorizationError("QUOTE_RECOVERY_OPERATION_PRINCIPAL_MISMATCH")


def _load_operation_intent(
    workspace: Any,
    operation_key: str,
    *,
    command: QuoteRecoveryNewRun | None = None,
    policy_digest: str | None = None,
    principal_identity_digest: str | None = None,
) -> dict[str, Any] | None:
    _, artifact_id = _operation_storage(workspace, operation_key)
    try:
        intent = workspace.store.load_artifact(
            artifact_id,
            OPERATION_INTENT_MEDIA_TYPE,
        ).payload
    except KeyError:
        return None
    _validate_operation_intent(
        workspace,
        intent,
        command=command,
        policy_digest=policy_digest,
        principal_identity_digest=principal_identity_digest,
    )
    key, _ = _operation_storage(workspace, operation_key)
    recorded = workspace.store.get_idempotent(key, intent["request_digest"])
    if recorded != intent:
        raise IntegrityError("QUOTE_RECOVERY_OPERATION_INTENT_MISMATCH")
    return intent


def _existing_invocation(
    service: GovernedPatternService,
    *,
    candidate_ref: str,
    run_id: str,
    input_digest: str,
) -> dict[str, Any] | None:
    reservations = [
        item
        for item in service._family("invocation-reservation")
        if item.get("candidate_ref") == candidate_ref and item.get("run_id") == run_id
    ]
    if not reservations:
        return None
    if len(reservations) != 1:
        raise IntegrityError("QUOTE_RECOVERY_RUN_RESERVATION_AMBIGUOUS")
    reservation = reservations[0]
    if (
        reservation.get("capture_digest") is None
        or reservation.get("input_digest") != input_digest
    ):
        raise IntegrityError("QUOTE_RECOVERY_RUN_INPUT_BINDING_MISMATCH")
    reservation_ref = _artifact_ref(reservation)
    results = [
        item
        for item in service._family("invocation-result")
        if item.get("reservation_ref") == reservation_ref
    ]
    if len(results) > 1:
        raise IntegrityError("QUOTE_RECOVERY_RUN_RESULT_AMBIGUOUS")
    if results:
        receipt = results[0]["receipt"]
        result = results[0].get("result")
        if (
            receipt.get("run_id") != run_id
            or receipt.get("input_digest") != input_digest
            or receipt.get("candidate_only") is not True
            or receipt.get("target_writes") != 0
            or not isinstance(result, dict)
            or sha256_digest(result) != receipt.get("output_digest")
        ):
            raise IntegrityError("QUOTE_RECOVERY_RUN_RESULT_BINDING_MISMATCH")
        return {
            "status": "CONSUMED",
            "action": result.get("action"),
            "reason": result.get("reason"),
            "outcome": receipt.get("outcome"),
            "invocation_receipt_digest": receipt["digest"],
            "package_digest": receipt["package_digest"],
            "output_digest": receipt["output_digest"],
            "result_state": "SUCCEEDED",
        }
    terminals = [
        item
        for kind in ("invocation-terminal", "invocation-rejection")
        for item in service._family(kind)
        if item.get("reservation_ref") == reservation_ref
    ]
    if len(terminals) > 1:
        raise IntegrityError("QUOTE_RECOVERY_RUN_TERMINAL_AMBIGUOUS")
    if terminals:
        terminal = terminals[0]
        return {
            "status": terminal.get("state", "REJECTED"),
            "action": None,
            "reason": None,
            "outcome": None,
            "invocation_receipt_digest": None,
            "package_digest": reservation.get("package_digest"),
            "output_digest": None,
            "result_state": terminal.get("state", "REJECTED"),
            "reason_code": terminal["reason_code"],
        }
    return {
        "status": "RESULT_UNKNOWN",
        "action": None,
        "reason": None,
        "outcome": None,
        "invocation_receipt_digest": None,
        "package_digest": reservation.get("package_digest"),
        "output_digest": None,
        "result_state": "RESULT_UNKNOWN",
        "reason_code": "QUOTE_RECOVERY_INVOCATION_RESULT_UNKNOWN",
    }


def _reserve_operation(
    workspace: Any,
    *,
    command: QuoteRecoveryNewRun,
    policy: QuoteRecoveryLearningPolicy,
    principal_identity_digest: str,
    case_digest: str,
    case_outcome: str,
    case_certificate_digest: str,
    input_digest: str,
) -> tuple[dict[str, Any], bool]:
    key, artifact_id = _operation_storage(workspace, command.operation_key)
    request_digest = _operation_request_digest(
        command=command,
        policy_digest=policy.digest,
        principal_identity_digest=principal_identity_digest,
    )
    intent = {
        "schema_version": "orgrebase.quote-recovery-operation-intent.v2",
        "operation_key": command.operation_key,
        "request_digest": request_digest,
        "policy_revision": policy.policy_revision,
        "policy_digest": policy.digest,
        "principal_identity_digest": principal_identity_digest,
        "organization_id": policy.organization_id,
        "workspace_id": policy.workspace_id,
        "candidate_ref": command.candidate_ref,
        "event_id": command.event_id,
        "run_id": command.new_run_id,
        "case_digest": case_digest,
        "case_outcome": case_outcome,
        "case_certificate_digest": case_certificate_digest,
        "input_digest": input_digest,
        "state": "DISPATCH_RESERVED",
    }
    run_identity = sha256_digest(
        {
            "workspace_id": workspace.store.workspace_id,
            "candidate_ref": command.candidate_ref,
            "run_id": command.new_run_id,
        }
    ).removeprefix("sha256:")
    run_key = f"quote-recovery-run-operation:{run_identity}"
    run_request_digest = sha256_digest(
        {
            "schema_version": "orgrebase.quote-recovery-run-operation-request.v1",
            "operation_key": command.operation_key,
            "request_digest": request_digest,
            "input_digest": input_digest,
        }
    )
    run_binding = {
        "schema_version": "orgrebase.quote-recovery-run-operation-binding.v1",
        "operation_key": command.operation_key,
        "request_digest": request_digest,
        "candidate_ref": command.candidate_ref,
        "run_id": command.new_run_id,
        "input_digest": input_digest,
    }
    with workspace.store.transaction() as connection:
        existing_run = workspace.store.get_idempotent(
            run_key,
            run_request_digest,
            connection=connection,
        )
        if existing_run is not None and existing_run != run_binding:
            raise IntegrityError("QUOTE_RECOVERY_RUN_OPERATION_BINDING_MISMATCH")
        existing = workspace.store.get_idempotent(
            key,
            request_digest,
            connection=connection,
        )
        if existing is not None:
            _validate_operation_intent(
                workspace,
                existing,
                command=command,
                policy_digest=policy.digest,
                principal_identity_digest=principal_identity_digest,
            )
            return existing, False
        if existing_run is None:
            workspace.store.save_idempotent(
                connection,
                run_key,
                run_request_digest,
                run_binding,
            )
        workspace.store.save_idempotent(connection, key, request_digest, intent)
        workspace.store.save_artifact(
            connection,
            artifact_id,
            OPERATION_INTENT_MEDIA_TYPE,
            intent,
        )
    return intent, True


def _require_current_policy(
    path: Path,
    expected: QuoteRecoveryLearningPolicy,
    *,
    now: Callable[[], float],
) -> None:
    current = load_quote_recovery_learning_policy(path)
    if current.digest != expected.digest or not current.enabled:
        raise AuthenticationError("QUOTE_RECOVERY_POLICY_CHANGED", 403)
    if now() >= current.adoption_not_after_epoch:
        raise AuthenticationError("QUOTE_RECOVERY_ADOPTION_POLICY_EXPIRED", 403)


def _read_service(workspace: Any) -> GovernedPatternService:
    """Construct a read-only view over persisted invocation artifacts."""

    return GovernedPatternService(
        workspace.store,
        corpus_authority="quote-recovery-history:corpus",
        evaluator_authority="quote-recovery-history:evaluator",
        governance_authority="quote-recovery-history:governor",
    )


def _operation_result(
    service: GovernedPatternService,
    intent: dict[str, Any],
) -> dict[str, Any]:
    existing = _existing_invocation(
        service,
        candidate_ref=intent["candidate_ref"],
        run_id=intent["run_id"],
        input_digest=intent["input_digest"],
    )
    if existing is not None:
        return existing
    return {
        "status": "RESULT_UNKNOWN",
        "action": None,
        "reason": None,
        "outcome": None,
        "invocation_receipt_digest": None,
        "package_digest": None,
        "output_digest": None,
        "result_state": "RESULT_UNKNOWN",
        "reason_code": "QUOTE_RECOVERY_OPERATION_DISPATCH_UNKNOWN",
    }


def _operation_receipt(
    intent: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.quote-recovery-new-run-receipt.v1",
        "operation_key": intent["operation_key"],
        "policy_revision": intent["policy_revision"],
        "policy_digest": intent["policy_digest"],
        "candidate_ref": intent["candidate_ref"],
        "event_id": intent["event_id"],
        "case_digest": intent["case_digest"],
        "case_outcome": intent["case_outcome"],
        "case_certificate_digest": intent["case_certificate_digest"],
        "run_id": intent["run_id"],
        "input_digest": intent["input_digest"],
        **result,
        "old_quote_modified": False,
        "model_invocations": 0,
        "model_cost_usd": 0,
        "canonical_target_writes": 0,
    }


def read_quote_recovery_operation(
    workspace: Any,
    operation_key: str,
) -> dict[str, Any]:
    """Read one frozen operation without reauthorizing or redispatching adoption."""

    if _OPERATION_KEY_PATTERN.fullmatch(operation_key) is None:
        raise QuoteRecoveryOperationError("QUOTE_RECOVERY_OPERATION_KEY_INVALID")
    principal = request_principal.get()
    authorization_check = current_authorization()
    if principal is None or authorization_check is None:
        raise AuthenticationError("QUOTE_RECOVERY_READ_AUTHORIZATION_REQUIRED")
    authorization_check()
    if principal.tenant_id != workspace.profile.organization_id:
        raise AuthorizationError("QUOTE_RECOVERY_OPERATION_TENANT_DENIED")
    intent = _load_operation_intent(workspace, operation_key)
    if intent is None:
        raise KeyError(operation_key)
    result = _operation_result(_read_service(workspace), intent)
    authorization_check()
    return _operation_receipt(intent, result)


def execute_quote_recovery_new_run(
    workspace: Any,
    command: QuoteRecoveryNewRun,
    *,
    config_path: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    command = QuoteRecoveryNewRun.model_validate(command.model_dump(mode="json"))
    selected_policy = config_path or _configured_policy_path()
    policy = load_quote_recovery_learning_policy(selected_policy)
    if not policy.enabled:
        raise QuoteRecoveryOperationError("QUOTE_RECOVERY_LEARNING_DISABLED")
    if now() >= policy.adoption_not_after_epoch:
        raise QuoteRecoveryOperationError("QUOTE_RECOVERY_ADOPTION_POLICY_EXPIRED")
    if (
        workspace.profile.organization_id != policy.organization_id
        or workspace.store.workspace_id != policy.workspace_id
        or command.candidate_ref != policy.candidate_ref
    ):
        raise AuthorizationError("QUOTE_RECOVERY_POLICY_SCOPE_DENIED")
    principal = request_principal.get()
    if principal is None:
        raise AuthenticationError("QUOTE_RECOVERY_PRINCIPAL_REQUIRED")
    if (
        principal.tenant_id != policy.organization_id
        or principal.actor_id not in policy.runtime_actor_ids
    ):
        raise AuthorizationError("QUOTE_RECOVERY_RUNTIME_ACTOR_DENIED")
    previous_check = current_authorization()
    if previous_check is None:
        raise AuthenticationError("QUOTE_RECOVERY_CURRENT_AUTHORIZATION_REQUIRED")
    previous_check()
    _require_current_policy(selected_policy, policy, now=now)
    principal_identity_digest = _principal_identity_digest(principal)
    intent = _load_operation_intent(
        workspace,
        command.operation_key,
        command=command,
        policy_digest=policy.digest,
        principal_identity_digest=principal_identity_digest,
    )

    resolver = None
    public_input = None
    operation_created = False
    if intent is None:
        case = build_quote_recovery_case(
            workspace,
            command.event_id,
            corpus_authority=policy.corpus_actor_id,
        )
        resolver = make_quote_recovery_case_resolver(workspace)
        resolver(case)
        public_input = quote_recovery_consumer_input(case)
        if command.new_run_id == public_input["run_id"]:
            raise QuoteRecoveryOperationError("QUOTE_RECOVERY_NEW_RUN_REQUIRED")
        public_input["run_id"] = command.new_run_id
        if (
            _existing_invocation(
                _read_service(workspace),
                candidate_ref=command.candidate_ref,
                run_id=command.new_run_id,
                input_digest=sha256_digest(public_input),
            )
            is not None
        ):
            raise IntegrityError("QUOTE_RECOVERY_RUN_ALREADY_BOUND_TO_OPERATION")
        _require_current_policy(selected_policy, policy, now=now)
        intent, operation_created = _reserve_operation(
            workspace,
            command=command,
            policy=policy,
            principal_identity_digest=principal_identity_digest,
            case_digest=case.digest,
            case_outcome=case.outcome,
            case_certificate_digest=case.certificate["digest"],
            input_digest=sha256_digest(public_input),
        )

    prerequisites = {
        *policy.required_knowledge_refs,
        *policy.required_qualification_refs,
    }
    service = GovernedPatternService(
        workspace.store,
        corpus_authority=policy.corpus_actor_id,
        evaluator_authority=policy.evaluator_actor_id,
        governance_authority=policy.governor_actor_id,
        case_evidence_resolver=resolver,
        prerequisite_resolver=lambda actor, ref: (
            actor in policy.runtime_actor_ids and ref in prerequisites
        ),
    )
    existing = _existing_invocation(
        service,
        candidate_ref=intent["candidate_ref"],
        run_id=intent["run_id"],
        input_digest=intent["input_digest"],
    )
    if existing is None and operation_created:
        if public_input is None:  # pragma: no cover - new-operation invariant
            raise IntegrityError("QUOTE_RECOVERY_OPERATION_INPUT_MISSING")
        controller = PrincipalPatternGovernance(
            service,
            PatternAuthorityScope(
                tenant_id=policy.organization_id,
                workspace_id=policy.workspace_id,
                corpus_actor_id=policy.corpus_actor_id,
                author_actor_id=policy.author_actor_id,
                evaluator_actor_id=policy.evaluator_actor_id,
                governor_actor_id=policy.governor_actor_id,
                reviewed_bundle_digest=policy.reviewed_bundle_digest,
                reviewed_target_skill=policy.reviewed_target_skill,
                reviewed_predecessor_package_digest=(
                    policy.reviewed_predecessor_package_digest
                ),
            ),
            adoption=PatternAdoptionPolicy(
                enabled=True,
                candidate_refs=(policy.candidate_ref,),
            ),
        )
        context = InvocationContext(
            command.new_run_id,
            public_input["task_id"],
            public_input["delegation_id"],
            principal.actor_id,
        )
        def check_policy_and_authorization() -> None:
            previous_check()
            _require_current_policy(selected_policy, policy, now=now)

        authorization_token = request_authorization.set(
            check_policy_and_authorization
        )
        try:
            try:
                controller.invoke(
                    command.candidate_ref,
                    public_input,
                    context=context,
                    knowledge_refs=policy.required_knowledge_refs,
                    qualification_refs=policy.required_qualification_refs,
                )
            except IntegrityError as error:
                if str(error) != "PATTERN_INVOCATION_ALREADY_RESERVED":
                    raise
        finally:
            request_authorization.reset(authorization_token)
        existing = _existing_invocation(
            service,
            candidate_ref=intent["candidate_ref"],
            run_id=intent["run_id"],
            input_digest=intent["input_digest"],
        )
    if existing is None:
        existing = _operation_result(service, intent)
    _require_current_policy(selected_policy, policy, now=now)
    return _operation_receipt(intent, existing)


__all__ = (
    "QuoteRecoveryLearningPolicy",
    "QuoteRecoveryNewRun",
    "QuoteRecoveryOperationError",
    "execute_quote_recovery_new_run",
    "load_quote_recovery_learning_policy",
    "read_quote_recovery_operation",
)
