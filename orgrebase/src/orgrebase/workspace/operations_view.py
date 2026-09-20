"""Bounded operational observations with explicit measurement provenance."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select, true

from orgrebase.clock import SystemClock, utc_datetime
from orgrebase.database import domain_events, effect_intents, workspace_registry
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import require_action

_STATES = ("READY", "DISPATCHING", "COMMIT_UNKNOWN", "CONFIRMED", "REJECTED")


def operations_snapshot(workspace: Any, *, after: int = 0, limit: int = 100) -> dict[str, Any]:
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("OPERATIONS_PAGE_INVALID")
    require_action(workspace, "read")
    now = workspace.clock.now()
    stamp = utc_datetime(now)
    store = workspace.store
    # One statement gives all database metrics and the event page one snapshot,
    # including when another API or connector process commits concurrently.
    effects = (
        select(
            *(func.count(case((effect_intents.c.state == state, 1))).label(state) for state in _STATES),
            func.count(case((effect_intents.c.state.not_in(_STATES), 1))).label("invalid_states"),
            func.min(case((effect_intents.c.state == "COMMIT_UNKNOWN", effect_intents.c.created_at)))
            .label("oldest_unknown"),
        )
        .where(effect_intents.c.workspace_id == store.workspace_id)
        .subquery()
    )
    events = (
        select(domain_events)
        .where(domain_events.c.workspace_id == store.workspace_id, domain_events.c.sequence_no > after)
        .order_by(domain_events.c.sequence_no)
        .limit(limit + 1)
        .subquery()
    )
    statement = (
        select(workspace_registry.c.audit_sequence, workspace_registry.c.audit_head,
               workspace_registry.c.pending_change_count, effects, events)
        .select_from(workspace_registry.join(effects, true()).outerjoin(
            events, workspace_registry.c.workspace_id == events.c.workspace_id))
        .where(workspace_registry.c.workspace_id == store.workspace_id)
        .order_by(events.c.sequence_no)
    )
    with workspace._command_lock, store.read_connection() as connection:
        require_action(workspace, "read")
        rows = store.execute(connection, statement).fetchall()
    if not rows:
        raise IntegrityError("STATE_STORE_WORKSPACE_NOT_REGISTERED")
    checkpoint = rows[0]
    if checkpoint["invalid_states"]:
        raise RuntimeError("OPERATIONAL_EFFECT_STATE_UNKNOWN")
    counts = {state: int(checkpoint[state]) for state in _STATES}
    oldest_unknown = checkpoint["oldest_unknown"]
    unknown_age = max(0, int((stamp - utc_datetime(oldest_unknown)).total_seconds())) if oldest_unknown else None
    head = {"sequence_no": int(checkpoint["audit_sequence"]), "head_digest": checkpoint["audit_head"]}
    pending = int(checkpoint["pending_change_count"])
    items = tuple(store._event_envelope(row) for row in rows[:limit] if row["sequence_no"] is not None)
    page = {"items": items, "next_cursor": items[-1]["sequence_no"] if len(rows) > limit else None}

    source = {"status": "NOT_CONFIGURED", "observed_at": None, "expires_at": None,
              "age_seconds": None, "reason_codes": []}
    source_path = getattr(workspace, "source_config_path", None)
    if source_path is not None:
        from orgrebase.workspace.dataverse import SourceError
        from orgrebase.workspace.source_bindings import load_source_config, source_coverage
        try:
            coverage = source_coverage(workspace, load_source_config(source_path), now=now)
            observed = coverage.get("observed_at")
            source = {"status": coverage["status"], "observed_at": observed,
                      "expires_at": coverage.get("expires_at"), "reason_codes": coverage.get("reasons", []),
                      "age_seconds": max(0, int((stamp - utc_datetime(observed)).total_seconds())) if observed else None,
                      "coverage_digest": coverage["coverage_digest"]}
        except (SourceError, ValueError, OSError):
            source = {**source, "status": "UNKNOWN", "reason_codes": ["SOURCE_COVERAGE_UNAVAILABLE"]}

    observations = []
    latency_observations = []
    for event in page["items"]:
        payload = event["payload"]
        if isinstance(workspace.clock, SystemClock) and event["event_type"] in {
            "WORKSPACE_CHANGE_PREVIEWED", "WORKSPACE_CHANGE_APPROVED",
        }:
            from orgrebase.workspace.change_proposals import submission
            event_id = payload.get("kind")
            gate_record = workspace._review_gate_record(event_id)
            submitted = submission(workspace, event_id)
            if gate_record and gate_record["gate"]["previewed_at_epoch_ms"] > 0:
                preview_time = gate_record["gate"]["previewed_at_epoch_ms"] / 1000
                if event["event_type"] == "WORKSPACE_CHANGE_PREVIEWED" and submitted:
                    duration = preview_time - utc_datetime(submitted["submitted_at"]).timestamp()
                    metric = "proposal_to_preview_seconds"
                elif event["event_type"] == "WORKSPACE_CHANGE_APPROVED":
                    approval = workspace._approval_record(event_id)
                    duration = utc_datetime(approval["approval"]["approved_at"]).timestamp() - preview_time
                    metric = "preview_to_approval_elapsed_seconds"
                else:
                    continue
                if duration >= 0:
                    latency_observations.append({"sequence_no": event["sequence_no"],
                                                 "event_digest": event["event_digest"], "metric": metric,
                                                 "seconds": round(duration, 3), "measurement": "SERVER_OBSERVED_ELAPSED_TIME"})
        if event["event_type"] != "WORKSPACE_REVIEW_OBSERVATION_RECORDED":
            continue
        if (payload.get("measurement") != "CLIENT_REPORTED_ACTIVE_REVIEW_TIME"
                or type(payload.get("active_ms")) is not int or not 0 <= payload["active_ms"] <= 86_400_000):
            raise RuntimeError("OPERATIONAL_REVIEW_OBSERVATION_INVALID")
        observations.append({"sequence_no": event["sequence_no"], "event_digest": event["event_digest"],
                             "received_at": payload["received_at"], "active_ms": payload["active_ms"],
                             "event_id": payload.get("event_id"),
                             "observation_ref": payload.get("observation_ref"),
                             "observation_digest": payload.get("observation_digest"),
                             "action": payload.get("action"), "outcome": payload.get("outcome"),
                             "measurement": "CLIENT_REPORTED_ACTIVE_REVIEW_TIME"})
    alerts = []
    if counts["COMMIT_UNKNOWN"]:
        alerts.append({"code": "EXTERNAL_OUTCOME_UNKNOWN", "severity": "REQUIRES_ACTION",
                       "responsible_role": "Workspace executor and original approval owner",
                       "action": "Query the original operation; retain its target barrier until a positive receipt or fenced cancellation."})
    if source["status"] == "UNKNOWN":
        alerts.append({"code": "SOURCE_COVERAGE_UNKNOWN", "severity": "REQUIRES_ACTION",
                       "responsible_role": "Source connector operator and field owner",
                       "action": "Restore source visibility and finish synchronization; renew mapping approval after a binding or schema change."})

    def point(value: int, labels: dict[str, str] | None = None) -> dict[str, Any]:
        return {"timeUnixNano": str(int(stamp.timestamp()) * 1_000_000_000), "asInt": str(value),
                "attributes": [{"key": key, "value": {"stringValue": item}} for key, item in (labels or {}).items()]}

    metrics = [
        {"name": "orgrebase.workspace.unresolved_changes", "unit": "{change}", "gauge": {"dataPoints": [point(pending)]}},
        {"name": "orgrebase.workspace.effects", "unit": "{effect}",
         "gauge": {"dataPoints": [point(counts[state], {"state": state}) for state in _STATES]}},
    ]
    if source["age_seconds"] is not None:
        metrics.append({"name": "orgrebase.source.observation_age", "unit": "s",
                        "gauge": {"dataPoints": [point(source["age_seconds"])]}})
    report = {"schema_version": "orgrebase.workspace-operations.v1", "observed_at": now,
              "checkpoint": head, "history_verification": "APPEND_PROJECTION_ONLY",
              "effect_counts": counts, "unknown_oldest_observed_age_seconds": unknown_age,
              "unknown_age_sample_limit": None, "unknown_age_sample_complete": True,
              "unresolved_changes": pending, "source": source, "alerts": alerts,
              "review_observations": observations,
              "latency_observations": latency_observations,
              "event_page": {"after": after, "through": page["items"][-1]["sequence_no"] if page["items"] else after,
                             "next_cursor": page["next_cursor"], "count": len(page["items"]), "limit": limit},
              "metrics": {"resourceMetrics": [{"resource": {"attributes": [
                  {"key": "service.name", "value": {"stringValue": "orgrebase"}}]},
                  "scopeMetrics": [{"scope": {"name": "orgrebase.workspace.operations", "version": "1"}, "metrics": metrics}]}]},
              "measurement_boundaries": {"active_review": "CLIENT_REPORTED_NOT_INDEPENDENT_LABOR_MEASUREMENT",
                                         "pending": "UNRESOLVED_INCLUDES_EXPIRED_OR_STALE_REQUIRING_DISPOSITION",
                                         "clock": type(workspace.clock).__name__, "production_roi": "NOT_MEASURED",
                                         "target_drift": "NOT_CONTINUOUSLY_MONITORED_USE_CONDITIONAL_WRITE_AND_READBACK"},
              "database_writes": 0, "target_writes": 0}
    return {**report, "digest": sha256_digest(report)}
