"""One durable candidate attempt shared by ordinary and grouped previews."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from orgrebase.database import idempotency_records
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, RunEnvelope, StructuredHandoff
from orgrebase.http_errors import public_error
from orgrebase.workspace.change_agentteams import (
    NATIVE_PROGRESS_MEDIA,
    ChangeAdvisoryNativeSession,
    execution_binding,
    native_tenant_id,
    progress_summary,
)
from orgrebase.workspace.models import VerifiedAdvisoryBundle
from orgrebase.workspace.runtime_revision import workspace_revision

_MEDIA = "application/vnd.orgrebase.preview-candidate-attempt+json"


def _attempt_key(command: str, run_id: str, nonce: str) -> str:
    return "workspace-preview-attempt:" + sha256_digest({
        "command": command, "run_id": run_id, "nonce": nonce,
    })[7:]


def _receipt_summaries(result: dict[str, Any]) -> list[dict[str, Any]]:
    receipts = result.get("receipts", [])
    if not receipts:
        handoffs = result.get("advisory", {}).get("handoffs", result.get("unverified_handoffs", []))
        receipts = [item["payload"]["model_advisory"]["receipt"] for item in handoffs
                    if "model_advisory" in item.get("payload", {})]
    summaries = []
    for receipt in receipts:
        request_id = receipt.get("provider_request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", request_id):
            request_id = None
        dispatch = receipt.get("dispatch_state")
        if dispatch not in {"NOT_SENT", "SENT_UNKNOWN", "RESPONSE_RECEIVED"}:
            dispatch = "UNKNOWN"
        tokens = {}
        for name in ("input_tokens", "output_tokens"):
            value = receipt.get(name)
            tokens[name] = value if type(value) is int and value >= 0 and dispatch == "RESPONSE_RECEIVED" else None
        summaries.append({
            "dispatch_state": dispatch,
            "provider_request_id": request_id,
            **tokens,
        })
    return summaries


def read_attempt_summary(workspace: Any, *, command: str) -> dict[str, Any] | None:
    """Project persisted diagnostics without reserving, dispatching, or admitting work."""
    key = _attempt_key(command, workspace.effective_workflow_run_id, workspace.workflow_run_nonce)
    with workspace.store.read_snapshot() as connection:
        row = workspace.store.execute(connection, select(
            idempotency_records.c.request_digest, idempotency_records.c.result_json,
        ).where(idempotency_records.c.key == key)).fetchone()
        if row is None:
            return None
        reservation = json.loads(row["result_json"])
        try:
            result = workspace.store.load_artifact(key, _MEDIA).payload
        except KeyError:
            result = None
        if result is not None and result["request_digest"] != row["request_digest"]:
            raise IntegrityError("WORKSPACE_ADVISORY_ATTEMPT_BINDING_INVALID")
        native_progress = workspace.store.list_artifacts(
            artifact_id_prefix=key + ":agentteams:", expected_media_type=NATIVE_PROGRESS_MEDIA)
        native = max((item.payload for item in native_progress), key=lambda item: (
            len(item["actions"]), item["status"] != "RUNNING"), default=None)
        if native is not None and (native["binding"]["attempt_key"] != key
                or native["binding"]["request_digest"] != row["request_digest"]
                or native["binding"]["run_id"] != workspace.effective_workflow_run_id
                or native["binding"]["nonce"] != workspace.workflow_run_nonce
                or native["binding"]["tenant_id"] != native_tenant_id(workspace)
                or native["binding"]["workspace_id"] != workspace.store.workspace_id):
            raise IntegrityError("WORKSPACE_ADVISORY_AGENTTEAMS_EVIDENCE_INVALID")
    deadline = reservation["deadline_epoch_ms"]
    state = (result["status"] if result is not None else
             "IN_PROGRESS" if workspace._wall_clock_epoch_ms() < deadline else "RESULT_UNKNOWN")
    cost = reservation.get("cost_reservation")
    # Price sources and arbitrary exception/response text never enter this read contract.
    cost_summary = ({name: cost[name] for name in
                     ("scope", "currency", "reserved_microusd", "limit_microusd", "calls")}
                    if cost is not None else None)
    receipts = _receipt_summaries(result) if result is not None else []
    usage_observed = (state == "COMPLETE" and bool(receipts)
                      and (cost is None or len(receipts) == cost["calls"])
                      and all(item["input_tokens"] is not None and item["output_tokens"] is not None
                              for item in receipts))
    error_code = None
    if result is not None and result["status"] == "FAILED":
        error_code = public_error(RuntimeError(result["error_code"]))["code"]
    elif state in {"IN_PROGRESS", "RESULT_UNKNOWN"}:
        error_code = f"WORKSPACE_ADVISORY_{state}"
    return {
        "state": state,
        "deadline_epoch_ms": deadline,
        "cost_reservation": cost_summary,
        "usage_status": "OBSERVED" if usage_observed else "UNKNOWN",
        "receipt_summaries": receipts,
        "public_error_code": error_code,
        **({"native_execution": native} if native is not None else {}),
    }


@dataclass(frozen=True)
class PreviewAttempt:
    key: str
    request_digest: str
    envelope: RunEnvelope
    invalidation_digest: str | None
    cached: VerifiedAdvisoryBundle | None = None
    execution_deadline_epoch_ms: int | None = None
    cost_reservation: dict[str, object] | None = None


def source_invalidation_digest(workspace: Any) -> str | None:
    events = workspace.changes.journal("WORKSPACE_SOURCE_INVALIDATED", limit=1, descending=True)
    return events[0]["event_digest"] if events else None


def require_attempt_sources(workspace: Any, attempt: PreviewAttempt) -> None:
    # A repeated invalidation can leave object state STALE while invalidating
    # the observation used for readmission. State equality alone is insufficient.
    if source_invalidation_digest(workspace) != attempt.invalidation_digest:
        raise IntegrityError("WORKSPACE_ADVISORY_SOURCE_INVALIDATED")


def reserve_attempt(workspace: Any, connection: Any, *, command: str, fixture: Any,
                    change_set: Any, preview: Any, envelope: RunEnvelope,
                    request_binding: Any = None) -> PreviewAttempt:
    """Reserve under the existing database's scoped idempotency lock.

    A process lost between dispatch and receipt persistence cannot safely infer
    that the provider did no work. Repeating this command never redispatches it.
    """
    key = _attempt_key(command, envelope.run_id, envelope.nonce)
    invalidation_digest = source_invalidation_digest(workspace)
    request_digest = sha256_digest({
        "tenant_id": workspace.store.tenant_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_digest": workspace.profile_digest,
        "fixture_digest": sha256_digest(fixture.model_dump(mode="json")),
        "change_set_digest": change_set.digest, "preview_digest": preview.digest,
        "request_binding": request_binding,
        "source_invalidation_digest": invalidation_digest,
        "runtime": workspace_revision(workspace),
    })
    try:
        previous = workspace.store.get_idempotent(key, request_digest, connection=connection)
    except RuntimeError as error:
        if str(error) == "IDEMPOTENCY_CONFLICT":
            raise IntegrityError("WORKSPACE_ADVISORY_INPUT_CHANGED_REQUIRE_NEW_EVENT") from error
        raise
    if previous is not None:
        try:
            result = workspace.store.load_artifact(key, _MEDIA).payload
        except KeyError as exc:
            code = ("WORKSPACE_ADVISORY_IN_PROGRESS" if workspace._wall_clock_epoch_ms()
                    < previous["deadline_epoch_ms"] else "WORKSPACE_ADVISORY_RESULT_UNKNOWN")
            raise IntegrityError(code) from exc
        if result["request_digest"] != request_digest:
            raise IntegrityError("WORKSPACE_ADVISORY_ATTEMPT_BINDING_INVALID")
        if result["status"] != "COMPLETE":
            raise IntegrityError("WORKSPACE_ADVISORY_ATTEMPT_FAILED:" + result["error_code"])
        return PreviewAttempt(key, request_digest, RunEnvelope.model_validate(previous["run_envelope"]), invalidation_digest,
                              VerifiedAdvisoryBundle.model_validate(result["advisory"]))
    if (getattr(workspace.advisory_factory, "provider", None) is not None
            or getattr(workspace.advisory_factory, "native_required", False)):
        if getattr(workspace, "_command_depth", 0):
            raise IntegrityError("WORKSPACE_ADVISORY_REQUIRES_INDEPENDENT_COMMAND")
        workspace.advisory_factory.require_available()
    reserve_cost = getattr(workspace.advisory_factory, "cost_reservation", None)
    cost_reservation = (reserve_cost(fixture=fixture, change_set=change_set, preview=preview)
                        if reserve_cost is not None else None)
    execution_deadline = workspace._wall_clock_epoch_ms() + int(
        getattr(workspace.advisory_factory, "max_elapsed_seconds", 120) * 1000
    )
    workspace.store.save_idempotent(connection, key, request_digest, {
        "schema_version": "orgrebase.preview-candidate-attempt.v2",
        "run_envelope": envelope.model_dump(mode="json"),
        "deadline_epoch_ms": execution_deadline,
        "execution_deadline_epoch_ms": execution_deadline,
        "cost_reservation": cost_reservation,
    })
    return PreviewAttempt(key, request_digest, envelope, invalidation_digest,
                          execution_deadline_epoch_ms=execution_deadline, cost_reservation=cost_reservation)


def execute_attempt(workspace: Any, attempt: PreviewAttempt, *, fixture: Any,
                    change_set: Any, preview: Any, adapter: Any, verifier: Any) -> VerifiedAdvisoryBundle:
    """Compute outside command/SQL locks and retain the result before admission."""
    if attempt.cached is not None:
        return attempt.cached
    collaboration: dict[str, Any] = {}
    native = None
    def save_progress(receipt: dict[str, Any]) -> None:
        summary = progress_summary(receipt)
        with workspace.store.transaction() as connection:
            workspace.store.save_artifact(connection,
                f"{attempt.key}:agentteams:{len(receipt['actions']):04d}:{receipt['status']}",
                NATIVE_PROGRESS_MEDIA, summary)
    try:
        limits = {}
        if attempt.execution_deadline_epoch_ms is not None:
            limits["deadline_monotonic"] = time.monotonic() + max(
                0, (attempt.execution_deadline_epoch_ms - workspace._wall_clock_epoch_ms()) / 1000
            )
        native_config = getattr(adapter, "native_config", None)
        if native_config is not None:
            plan, _ = adapter.compile(fixture=fixture, change_set=change_set, preview=preview)
            binding = execution_binding(tenant_id=native_tenant_id(workspace), workspace_id=workspace.store.workspace_id,
                attempt_key=attempt.key, request_digest=attempt.request_digest, change_set=change_set,
                preview=preview, run_envelope=attempt.envelope, plan=plan)
            native = ChangeAdvisoryNativeSession(native_config, binding=binding, plan=plan,
                deadline_monotonic=limits.get("deadline_monotonic", time.monotonic() + adapter.max_elapsed_seconds),
                on_progress=save_progress)
            native.start()
            limits["native_session"] = native
        collaboration = adapter.run(fixture=fixture, change_set=change_set,
                                    preview=preview, run_envelope=attempt.envelope, now=workspace.clock.now(), **limits)
        if native is not None:
            native.begin_review()
        advisory = verifier.verify(fixture=fixture, change_set=change_set, preview=preview,
                                   collaboration=collaboration, run_envelope=attempt.envelope, now=workspace.clock.now(),
                                   **({"require_native": False} if native is not None else {}))
        if native is not None:
            advisory = advisory.model_copy(update={"native_execution": native.finish(advisory)})
    except BaseException as error:
        receipts = tuple(getattr(error, "receipts", ()))
        # Provider adapters expose bounded public codes; arbitrary exception
        # messages can contain credentials or remote response bodies.
        code = getattr(error, "reason_code", type(error).__name__)
        if native is not None:
            native.status = "RESULT_UNKNOWN" if "RESULT_UNKNOWN" in code else "FAILED"
            save_progress(native.snapshot())
        with workspace.store.transaction() as connection:
            workspace.store.save_artifact(connection, attempt.key, _MEDIA, {
                "request_digest": attempt.request_digest, "status": "FAILED",
                "error_code": code,
                "cost_reservation": attempt.cost_reservation,
                "usage_status": "UNKNOWN",
                "receipts": [receipt.model_dump(mode="json") for receipt in receipts],
                "unverified_handoffs": [item.model_dump(mode="json")
                    for item in collaboration.get("handoffs", ()) if isinstance(item, StructuredHandoff)],
                **({"native_execution": native.snapshot()} if native is not None else {}),
            })
        raise
    finally:
        if native is not None:
            native.close()
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, attempt.key, _MEDIA, {
            "request_digest": attempt.request_digest, "status": "COMPLETE",
            "cost_reservation": attempt.cost_reservation,
            "advisory": advisory.model_dump(mode="json"),
        })
    return advisory
