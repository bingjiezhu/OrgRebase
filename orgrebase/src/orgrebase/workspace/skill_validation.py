"""Bounded, workspace-scoped checks of three retained Skill version pairs.

This command exercises the existing interpreter and release gates on declared
synthetic inputs. Its release ledger is private to the rehearsal; neither the
business capability registry nor a Quote is changed. The quote pair exercises
release and recovery; the other two pairs check compatibility and predecessor
invocation without moving a release head.
"""

from __future__ import annotations

import time
from typing import Any

from orgrebase.auth import (
    PRINCIPAL_IDENTITY_MODES,
    AuthenticationError,
    authorize,
    current_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.competition_run import _quote_skill_evaluation_cases
from orgrebase.workspace.controlled_local import ControlledEnterpriseClient, ControlledEnterpriseServer
from orgrebase.workspace.quote_skill_qualification import (
    CASES,
    REVISION,
    SUITE_DIGEST,
    qualification_premise,
)
from orgrebase.workspace.skill_compatibility import (
    CALLS_PER_PAIR,
    COMPATIBILITY_SKILLS,
    check_retained_compatibility,
    retained_pairs,
)
from orgrebase.workspace.skill_packages import (
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)
from orgrebase.workspace.skill_revision import SKILL_STEWARD_ACTOR
from orgrebase.workspace.skill_rollback import (
    PREDECESSOR_NAME,
    SkillPredecessorRollbackExecutor,
    invoke_restricted_predecessor,
    load_frozen_predecessor,
)

MEDIA = "application/vnd.orgrebase.skill-validation+json;version=1"
SCHEMA = "orgrebase.skill-validation.v1"
MAX_SECONDS = 30
MAX_SKILL_CALLS = len(CASES) + 3 + CALLS_PER_PAIR * len(COMPATIBILITY_SKILLS)


def _record(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": sha256_digest(value)}


def _scope(workspace: Any) -> tuple[Any, Any, Any, dict[str, Any], str]:
    registry = SkillPackageRegistry()
    package = registry.load(PREDECESSOR_NAME)
    predecessor = load_frozen_predecessor()
    if package.manifest["release_artifact"]["predecessor_package_digest"] != predecessor.package_digest:
        raise IntegrityError("SKILL_VALIDATION_PREDECESSOR_MISMATCH")
    binding = {"protocol": SCHEMA, "tenant_id": workspace.store.tenant_id or workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id, "package_digest": package.package_digest,
        "predecessor_digest": predecessor.package_digest,
        "qualification_suite_revision": REVISION,
        "qualification_suite_digest": SUITE_DIGEST,
        "compatibility_catalog_digest": sha256_digest(retained_pairs(registry))}
    return registry, package, predecessor, binding, "skill-validation:" + sha256_digest(binding)[7:]


def _require_authorization(workspace: Any, connection: Any) -> None:
    check = current_authorization()
    if check is not None:
        check()
        workspace.store.require_before_commit(connection, check)


def validation_view(workspace: Any) -> dict[str, Any]:
    registry, package, predecessor, binding, key = _scope(workspace)
    with workspace.store.read_snapshot():
        reservation = workspace.store.get_idempotent(key, sha256_digest(binding))
        if reservation is not None and (reservation.get("binding") != binding
                or reservation.get("actor_id") != SKILL_STEWARD_ACTOR
                or type(reservation.get("deadline_epoch_ms")) is not int
                or reservation["deadline_epoch_ms"] <= 0):
            raise IntegrityError("SKILL_VALIDATION_RESERVATION_BINDING_INVALID")
        try:
            result = workspace.store.load_artifact(key, MEDIA).payload
        except KeyError:
            result = None
        if result is not None and (reservation is None or result.get("status") not in {"PASS", "FAILED"}
                or result.get("actor_id") != SKILL_STEWARD_ACTOR
                or result.get("binding") != binding or result.get("digest") != sha256_digest(
                {name: value for name, value in result.items() if name != "digest"})):
            raise IntegrityError("SKILL_VALIDATION_RESULT_BINDING_INVALID")
    state = (result["status"] if result is not None else "NOT_RUN" if reservation is None else
             "IN_PROGRESS" if workspace._wall_clock_epoch_ms() < reservation["deadline_epoch_ms"] else "RESULT_UNKNOWN")
    principal = request_principal.get()
    permitted = workspace.approval_identity_mode not in PRINCIPAL_IDENTITY_MODES
    if principal is not None:
        try:
            authorize(principal, "govern", workspace.profile.organization_id)
            permitted = principal.actor_id == SKILL_STEWARD_ACTOR
        except AuthenticationError:
            permitted = False
    return {"schema_version": SCHEMA, "state": state, "binding": binding,
        "name": package.name, "current_version": package.version,
        "predecessor_version": predecessor.manifest["version"],
        "program_unchanged": package.program["digest"] == predecessor.program["digest"],
        "compatibility_catalog": list(retained_pairs(registry)),
        "source": {"resource_mode": registry.resource_mode,
            "skill_digest": package.resource_digests["skill"],
            "program_digest": package.program["digest"],
            "predecessor_program_digest": predecessor.program["digest"],
            "predecessor_provenance_digest": predecessor.provenance["digest"],
            "verification_scope": "PACKAGE_RESOURCE_BYTES_AND_RETAINED_PROVENANCE",
            "source_wheel_reopened": False,
            "predecessor_wheel_digest": predecessor.provenance["source_wheel"]["sha256"]},
        "limits": {"max_seconds": MAX_SECONDS, "max_skill_calls": MAX_SKILL_CALLS, "max_http_calls": 1},
        "synthetic_inputs": True, "production_canary": False,
        "business_skill_pointer_writes": 0, "canonical_target_writes": 0,
        "required_actor": SKILL_STEWARD_ACTOR, "can_execute": state == "NOT_RUN" and permitted,
        "result": result}


def _execute(registry: Any, package: Any, predecessor: Any, *, binding: dict[str, Any],
             key: str, actor_id: str, created_at: str, deadline: float) -> dict[str, Any]:
    calls = 0
    def charge() -> None:
        nonlocal calls
        calls += 1
        if calls > MAX_SKILL_CALLS or time.monotonic() >= deadline:
            raise IntegrityError("SKILL_VALIDATION_LIMIT_EXCEEDED")

    run_id, task_id = key, key + ":controlled-trial"
    domain_results = {domain: sha256_digest({"fixture": "SYNTHETIC_SKILL_VALIDATION",
        "binding": binding, "domain": domain}) for domain in ("product", "legal", "finance", "gtm")}
    coalition_digest = sha256_digest(domain_results)
    with ControlledEnterpriseServer(dependencies=("fixture:skill-validation:quote-policy@v1",)) as server:
        tool_result, observed_tool = ControlledEnterpriseClient(server.base_url, token=server.token).call_dependency_tool(
            run_id=run_id, task_id=task_id, target_id="fixture:skill-validation:quote", graph_digest=coalition_digest)
    tool_receipt = observed_tool.model_dump(mode="json")
    public_input = {"skill_partition": "replay", "candidate_program_digest_required": package.program["digest"],
        "dependency_tool_receipt_digest": tool_receipt["digest"], "dependency_result_digest": sha256_digest(tool_result),
        "coalition_result_binding_digest": coalition_digest, "domain_result_digests": domain_results}
    predecessor_input = {**public_input, "candidate_program_digest_required": predecessor.program["digest"]}
    charge()
    baseline = invoke_restricted_predecessor(predecessor, predecessor_input)
    evaluator = SkillPackageEvaluator(registry, before_invocation=charge)
    evaluation = evaluator.evaluate(PREDECESSOR_NAME,
        _quote_skill_evaluation_cases(run_id=run_id, public_input=public_input), evaluated_at=created_at,
        premise_lock=qualification_premise(package))
    if evaluation["verdict"] != "CANARY":
        raise IntegrityError("SKILL_VALIDATION_QUALIFICATION_REJECTED")
    ledger = SkillReleaseLedger(registry, evaluator)
    for state in ("EVALUATED", "SHADOW", "CANARY"):
        ledger.transition(PREDECESSOR_NAME, evaluation, to_state=state, actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("HUMAN_REQUESTED_ISOLATED_VALIDATION",), created_at=created_at)
    charge()
    trial = ledger.invoke(PREDECESSOR_NAME, public_input,
        context=InvocationContext(run_id=run_id, task_id=task_id, delegation_id=key + ":delegation", actor_id=actor_id),
        observed_dependencies=package.manifest["dependencies"], created_at=created_at)
    charge()
    restored = SkillPredecessorRollbackExecutor(predecessor).execute(
        run_id=run_id, actor_id=SKILL_REGISTRY_AUTHORITY, idempotency_key=key + ":restore",
        current_manifest=package.manifest, current_invocation_receipt=trial.receipt,
        current_input=public_input, current_result=trial.result, tool_receipt=tool_receipt, tool_result=tool_result,
        reason_codes=("HUMAN_REQUESTED_CONTROLLED_RECOVERY_REHEARSAL",), created_at=created_at)
    if time.monotonic() >= deadline or restored.result != baseline:
        raise IntegrityError("SKILL_VALIDATION_RESTORATION_MISMATCH")
    compatibility = check_retained_compatibility(registry, key=key,
        expected_catalog_digest=binding["compatibility_catalog_digest"], before_invocation=charge)
    return {"scope": "ISOLATED_CONTROLLED_SKILL_VALIDATION", "run_id": run_id,
        "synthetic_inputs": True, "skill_calls": calls, "http_calls": 1,
        "baseline": {"input_digest": sha256_digest(predecessor_input), "result": baseline,
                     "output_digest": sha256_digest(baseline)},
        "evaluation": evaluation, "release_history": list(ledger.history),
        "compatibility": compatibility,
        "trial": {"input": public_input, "result": trial.result, "receipt": trial.receipt},
        "rollback": {"result": restored.result, "receipt": restored.receipt},
        "dependency_tool": {"result": tool_result, "receipt": tool_receipt},
        "restored_baseline_equal": True, "business_skill_pointer_writes": 0, "canonical_target_writes": 0}


def execute_validation(workspace: Any, *, actor_id: str, expected_package_digest: str,
                       expected_predecessor_digest: str, expected_catalog_digest: str) -> dict[str, Any]:
    if actor_id != SKILL_STEWARD_ACTOR:
        raise AuthorizationError("SKILL_VALIDATION_ACTOR_DENIED")
    principal = request_principal.get()
    if principal is not None:
        authorize(principal, "govern", workspace.profile.organization_id)
        if principal.actor_id != actor_id:
            raise AuthorizationError("SKILL_VALIDATION_ACTOR_DENIED")
    elif workspace.approval_identity_mode in PRINCIPAL_IDENTITY_MODES:
        raise AuthenticationError("AUTH_VERIFIED_PRINCIPAL_REQUIRED")
    registry, package, predecessor, binding, key = _scope(workspace)
    if (expected_package_digest != package.package_digest or expected_predecessor_digest != predecessor.package_digest
            or expected_catalog_digest != binding["compatibility_catalog_digest"]):
        raise IntegrityError("SKILL_VALIDATION_REVIEWED_VERSION_CHANGED")
    request_digest = sha256_digest(binding)
    with workspace.store.transaction() as connection:
        _require_authorization(workspace, connection)
        previous = workspace.store.get_idempotent(key, request_digest, connection=connection)
        if previous is None:
            workspace.store.save_idempotent(connection, key, request_digest, {
                "binding": binding, "actor_id": actor_id,
                "deadline_epoch_ms": workspace._wall_clock_epoch_ms() + MAX_SECONDS * 1000})
    if previous is not None:
        return validation_view(workspace)
    created_at = workspace.clock.now()
    try:
        evidence = _execute(registry, package, predecessor, binding=binding, key=key, actor_id=actor_id,
                            created_at=created_at, deadline=time.monotonic() + MAX_SECONDS)
        result = _record({"status": "PASS", "binding": binding, "actor_id": actor_id,
                          "created_at": created_at, "evidence": evidence})
        with workspace.store.transaction() as connection:
            _require_authorization(workspace, connection)
            workspace.store.save_artifact(connection, key, MEDIA, result)
    except Exception as error:
        code = str(error) if isinstance(error, IntegrityError) and str(error).startswith("SKILL_") else type(error).__name__
        result = _record({"status": "FAILED", "binding": binding, "actor_id": actor_id,
                          "created_at": created_at, "error_code": code})
        with workspace.store.transaction() as connection:
            workspace.store.save_artifact(connection, key, MEDIA, result)
        raise
    return validation_view(workspace)
