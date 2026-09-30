"""Durable change work items and bounded candidate-preparation worker.

The canonical change journal is already the durable source of pending work and
``preview_execution`` already owns dispatch identity and unknown-result
handling.  This module composes those two boundaries; it does not introduce a
second queue or grant a background worker approval or Apply authority.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import (
    AuthenticationError,
    JWTAuthenticator,
    authorize,
    current_authorization,
    request_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import FreshnessError, IntegrityError
from orgrebase.workspace.change_budget import (
    DeploymentDispatchBudget,
    dispatch_budget,
    reconcile_expired_dispatch_quotas,
)
from orgrebase.workspace.change_proposals import change_detail, require_action
from orgrebase.workspace.change_recovery import attempt_command
from orgrebase.workspace.preview_execution import read_attempt_summary


class ChangePreparationConfig(BaseModel):
    """Deployment-owned policy for automatic candidate preparation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.change-preparation.v1", "orgrebase.change-preparation.v2"]
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    access_token_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    enabled: bool = False
    provider_mode: Literal["local-deterministic", "vertex-ai"] = "local-deterministic"
    max_changes_per_run: int = Field(default=20, ge=1, le=100)
    dispatch_budget: DeploymentDispatchBudget | None = None

    @model_validator(mode="after")
    def validate_provider_qualification(self) -> ChangePreparationConfig:
        if self.provider_mode == "vertex-ai":
            if self.schema_version != "orgrebase.change-preparation.v2":
                raise ValueError("CHANGE_PREPARATION_VERTEX_REQUIRES_V2")
            if self.dispatch_budget is None:
                raise ValueError("CHANGE_PREPARATION_VERTEX_DISPATCH_BUDGET_REQUIRED")
            if (
                self.dispatch_budget.max_reserved_calls <= 0
                or self.dispatch_budget.max_reserved_microusd <= 0
            ):
                raise ValueError("CHANGE_PREPARATION_VERTEX_DISPATCH_BUDGET_INVALID")
        elif self.schema_version != "orgrebase.change-preparation.v1":
            raise ValueError("CHANGE_PREPARATION_V2_VERTEX_REQUIRED")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("CHANGE_PREPARATION_CONFIG_DUPLICATE_KEY")
        result[key] = value
    return result


def load_change_preparation_config(path: Path) -> ChangePreparationConfig:
    """Read one private, regular deployment descriptor without following links."""

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65_536:
                raise ValueError("CHANGE_PREPARATION_CONFIG_INVALID")
            raw = stream.read(65_537)
        return ChangePreparationConfig.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("CHANGE_PREPARATION_"):
            raise
        raise ValueError("CHANGE_PREPARATION_CONFIG_INVALID") from exc


def _work_item(detail: dict[str, Any]) -> dict[str, Any]:
    event = detail["event"]
    preview = detail.get("preview")
    approval = detail.get("approval")
    outcome = detail.get("outcome")
    recovery = detail.get("recovery") or {}
    attempt = detail.get("advisory_attempt")
    return {
        "event_id": event["event_id"],
        "event_digest": event["digest"],
        "slot_id": event["slot_id"],
        "domain_id": event["proposal"]["domain"],
        "status": detail["status"],
        "status_reason": detail.get("status_reason"),
        "active_owner_id": detail["active_owner_id"],
        "authority_revision": detail["authority"]["revision"],
        "allowed_actions": list(detail["allowed_actions"]),
        "preview_digest": preview["preview_digest"] if preview else None,
        "approval_digest": approval["approval_digest"] if approval else None,
        "outcome_state": (outcome or {}).get("outcome", {}).get("status"),
        "attempt_state": (attempt or {}).get("state"),
        "recovery_state": recovery.get("state"),
        "recovery_round": recovery.get("round", 0),
        "source_group_id": detail.get("source_group_id"),
        "requires_human_decision": any(
            action in detail["allowed_actions"]
            for action in ("APPROVE", "REJECT", "APPLY", "RETURN_FOR_EVIDENCE")
        ),
        "candidate_only": outcome is None,
    }


def change_work_items(
    workspace: Any, *, after: int = 0, limit: int = 50
) -> dict[str, Any]:
    """Return a bounded, authority-derived inbox without exposing source values."""

    require_action(workspace, "read")
    page = workspace.changes.page(after=after, limit=limit)
    items = tuple(_work_item(change_detail(workspace, event.event_id)) for event in page["items"])
    return {
        "schema_version": "orgrebase.change-work-items.v1",
        "items": items,
        "next_cursor": page["next_cursor"],
        "total": page["total"],
        "authority": "CURRENT_SERVER_AUTHORIZATION_PROJECTION",
        "notification_delivery": "NOT_IMPLIED_BY_WORK_ITEM",
        "canonical_writes": 0,
        "target_writes": 0,
    }


_EXPECTED_BLOCKS = frozenset(
    {
        "WORKSPACE_ADVISORY_IN_PROGRESS",
        "WORKSPACE_ADVISORY_RESULT_UNKNOWN",
        "WORKSPACE_ADVISORY_ATTEMPT_FAILED",
        "CHANGE_RECOVERY_EVIDENCE_REQUIRED",
        "WORKSPACE_CHANGE_NOT_PREVIEWABLE",
    }
)

# These are per-attempt execution failures, not authorization, price contracts,
# storage integrity, or deployment-quota failures. A durable terminal receipt is
# still required before the worker may continue after a newly failed execution.
_ATTEMPT_EXECUTION_FAILURES = frozenset({
    "WORKSPACE_ADVISORY_PROVIDER_RESULT_UNAVAILABLE",
    "WORKSPACE_ADVISORY_BUDGET_EXHAUSTED",
    "WORKSPACE_ADVISORY_LATE_RESULT",
    "WORKSPACE_ADVISORY_PREVIEW_EXPIRED",
    "BOUNDED_EXECUTION_CANCELLATION_UNCONFIRMED",
})


def prepare_pending_changes(
    workspace: Any,
    *,
    max_changes: int = 20,
    dry_run: bool = False,
    dispatch_policy: DeploymentDispatchBudget | None = None,
) -> dict[str, Any]:
    """Prepare eligible candidates once, using the existing preview identity.

    Multiple processes may observe the same pending row.  The preview reserve
    transaction and its database idempotency lock select the sole dispatch;
    followers observe IN_PROGRESS/UNKNOWN and never create a replacement run.
    """

    if isinstance(max_changes, bool) or not isinstance(max_changes, int) or not 1 <= max_changes <= 100:
        raise ValueError("CHANGE_PREPARATION_BUDGET_INVALID")
    with dispatch_budget(dispatch_policy):
        return _prepare_pending_changes(workspace, max_changes=max_changes, dry_run=dry_run)


def _prepare_pending_changes(
    workspace: Any, *, max_changes: int, dry_run: bool
) -> dict[str, Any]:
    require_action(workspace, "propose")
    if not dry_run:
        reconcile_expired_dispatch_quotas(workspace)
    records: list[dict[str, Any]] = []
    blocked_attempts = {"FAILED": 0, "IN_PROGRESS": 0, "RESULT_UNKNOWN": 0}
    for event_id in workspace.changes.pending_ids():
        if len(records) >= max_changes:
            break
        check = current_authorization()
        if check is not None:
            check()
        detail = change_detail(workspace, event_id)
        if (
            detail["source_group_id"] is not None
            or detail["status"] != "RECEIVED"
            or detail["preview"] is not None
            or "PREVIEW" not in detail["allowed_actions"]
        ):
            continue
        attempt_state = (detail.get("advisory_attempt") or {}).get("state")
        if attempt_state in blocked_attempts:
            # Existing attempts cannot be dispatched again. Do not let an old
            # failed/unknown head consume a fresh-work limit of one indefinitely.
            blocked_attempts[attempt_state] += 1
            continue
        if dry_run:
            records.append(
                {
                    "event_id": event_id,
                    "event_digest": detail["event"]["digest"],
                    "result": "ELIGIBLE_NOT_DISPATCHED",
                }
            )
            continue
        try:
            bundle = workspace.preview_change(event_id)
        except (IntegrityError, FreshnessError, RuntimeError) as exc:
            code = str(exc).split(":", 1)[0]
            if code not in _EXPECTED_BLOCKS:
                if code not in _ATTEMPT_EXECUTION_FAILURES:
                    raise
                attempt = read_attempt_summary(workspace, command=attempt_command(workspace, event_id))
                if attempt is None or attempt["state"] not in {"FAILED", "RESULT_UNKNOWN"}:
                    raise
            records.append(
                {
                    "event_id": event_id,
                    "event_digest": detail["event"]["digest"],
                    "result": "BLOCKED",
                    "reason_code": code,
                }
            )
        else:
            records.append(
                {
                    "event_id": event_id,
                    "event_digest": detail["event"]["digest"],
                    "result": "CANDIDATE_PREPARED",
                    "preview_digest": bundle.preview.digest,
                    "canonical_writes": 0,
                }
            )
    return {
        "schema_version": (
            "orgrebase.change-preparation-run.v2"
            if getattr(workspace.advisory_factory, "uses_vertex_v3", False)
            else "orgrebase.change-preparation-run.v1"
        ),
        "mode": (
            "DRY_RUN" if dry_run else
            "VERTEX_V3_CANDIDATE_ONLY"
            if getattr(workspace.advisory_factory, "uses_vertex_v3", False)
            else "LOCAL_DETERMINISTIC_CANDIDATE_ONLY"
        ),
        "records": records,
        "processed": len(records),
        "blocked_attempts": blocked_attempts,
        "canonical_writes": 0,
        "target_writes": 0,
    }


def run_change_preparation(config_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Open an authenticated workspace and run one bounded worker pass."""

    from orgrebase.runtime_config import DeploymentSettings, open_workspace

    config = load_change_preparation_config(config_path)
    if not config.enabled and not dry_run:
        raise ValueError("CHANGE_PREPARATION_DISABLED")
    settings = DeploymentSettings.from_environment()
    if settings.mode != "production":
        raise ValueError("CHANGE_PREPARATION_REQUIRES_AUTHENTICATED_DEPLOYMENT")
    settings = settings.for_workspace(config.workspace_id)
    selected_provider = (
        os.environ.get("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic").strip()
        or "local-deterministic"
    )
    if selected_provider != config.provider_mode:
        raise ValueError("CHANGE_PREPARATION_PROVIDER_MISMATCH")
    if config.provider_mode == "vertex-ai":
        from orgrebase.workspace.model_budget import ModelBudget
        from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID

        budget_path = os.environ.get("ORGREBASE_CHANGE_MODEL_BUDGET_PATH")
        if not budget_path:
            raise ValueError("MODEL_PRICE_CONTRACT_REQUIRED")
        model_budget = ModelBudget.from_file(budget_path)
        model_budget.require_current()
        if model_budget.model_id != VERTEX_CANDIDATE_MODEL_ID:
            raise ValueError("MODEL_PRICE_MODEL_MISMATCH")
    authenticator = JWTAuthenticator(settings.identity)
    principal = authenticator.authenticate(
        f"Bearer {os.environ.get(config.access_token_variable, '')}"
    )
    authorize(principal, "propose", settings.identity.tenant_id)
    settings.authorize_workspace(principal.subject)

    def check_authorization() -> None:
        renewed = authenticator.authenticate(
            f"Bearer {os.environ.get(config.access_token_variable, '')}"
        )
        if (renewed.issuer, renewed.subject, renewed.tenant_id, renewed.actor_id) != (
            principal.issuer,
            principal.subject,
            principal.tenant_id,
            principal.actor_id,
        ):
            raise AuthenticationError("CHANGE_PREPARATION_IDENTITY_CHANGED", 403)
        authorize(renewed, "propose", settings.identity.tenant_id)
        settings.authorize_workspace(renewed.subject)
        if load_change_preparation_config(config_path).digest != config.digest:
            raise ValueError("CHANGE_PREPARATION_CONFIG_CHANGED")
        request_principal.set(renewed)

    workspace = open_workspace(settings)
    observed_vertex = getattr(workspace.advisory_factory, "uses_vertex_v3", False)
    observed_provider = getattr(workspace.advisory_factory, "provider", None)
    if (
        (config.provider_mode == "vertex-ai" and not observed_vertex)
        or (config.provider_mode == "local-deterministic" and observed_provider is not None)
    ):
        workspace.close()
        raise ValueError("CHANGE_PREPARATION_PROVIDER_MISMATCH")
    authorization_token = request_authorization.set(check_authorization)
    principal_token = request_principal.set(principal)
    try:
        check_authorization()
        result = prepare_pending_changes(
            workspace,
            max_changes=config.max_changes_per_run,
            dry_run=dry_run,
            dispatch_policy=config.dispatch_budget,
        )
        return {**result, "workspace_id": config.workspace_id, "policy_digest": config.digest}
    finally:
        request_principal.reset(principal_token)
        request_authorization.reset(authorization_token)
        workspace.close()
