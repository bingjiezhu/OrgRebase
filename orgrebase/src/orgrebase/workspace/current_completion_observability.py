"""Receipt counts for continuous runs, without synthetic execution spans."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.wire import WIRE_SCHEME, protocol_digest
from orgrebase.workspace.archive_readmission import group_archive_record
from orgrebase.workspace.history_codec import business_is_complete

SCHEMA = "orgrebase.workspace-completed-run-observability-view.v2"
EVIDENCE = "VERIFIED_RECEIPT_PROJECTION"
BOUNDARY = "CURRENT_RUN_RECEIPT_COUNTS_NOT_REALTIME_NOT_PRODUCTION_OR_SLA"


def build_current_completion_observability(state: Mapping[str, Any], archive: Mapping[str, Any]) -> dict[str, Any]:
    run_id = state.get("execution", {}).get("run_id")
    base = {"schema_version": SCHEMA, "run_id": run_id, "status": "INVALID", "failures": [],
            "completion_binding": None, "otlp": None, "projection_receipt": None,
            "projection_target_writes": 0, "evidence_class": EVIDENCE,
            "claim_ceiling": EVIDENCE, "claim_boundary": BOUNDARY}
    if not business_is_complete(state) and archive.get("status") == "PENDING":
        return {**base, "status": "PENDING"}
    try:
        record = archive["record"]
        quote = state["quote"]
        if (not run_id or not business_is_complete(state) or archive.get("status") != "ARCHIVED"
                or archive.get("failures") != [] or archive["run_id"] != run_id or record["run_id"] != run_id
                or record["canonical_authority"] != "ORGREBASE_CONTROL_PLANE"
                or record["quote"]["ref"] != f"{quote['id']}@{quote['version']}"
                or record["quote"]["digest"] != quote["digest"]):
            raise IntegrityError("CURRENT_COMPLETION_BINDING_INVALID")
        chain = state["event_chain"]
        previous = "sha256:" + "0" * 64
        if (chain["status"] != "PASS" or len(chain["records"]) != chain["events"]
                or len(chain["envelopes"]) != chain["events"]):
            raise IntegrityError("CURRENT_COMPLETION_AUDIT_REQUIRED")
        for index, event in enumerate(chain["envelopes"], start=1):
            body = {key: event[key] for key in ("sequence_no", "event_type", "payload", "previous_digest")}
            if (event["sequence_no"] != index or event["previous_digest"] != previous
                    or sha256_digest(body) != event["event_digest"]
                    or chain["records"][index - 1] != {key: value for key, value in event.items() if key != "payload"}):
                raise IntegrityError("CURRENT_COMPLETION_EVENT_CHAIN_INVALID")
            previous = event["event_digest"]
        if chain["head_digest"] != previous:
            raise IntegrityError("CURRENT_COMPLETION_EVENT_HEAD_INVALID")
        receipts = record["selective_rebase_receipts"]
        receipt_keys = [(item["kind"], item.get("group_id")) for item in receipts]
        final_version = int(quote["version"].removeprefix("v"))
        if (len(receipt_keys) != len(set(receipt_keys))
                or [item["successor_quote_version"] for item in receipts]
                != [f"v{version}" for version in range(2, final_version + 1)]
                or (receipts and receipts[-1]["successor_quote_digest"] != quote["digest"])):
            raise IntegrityError("CURRENT_COMPLETION_SUCCESSION_INVALID")
        approvals = 0
        for receipt in receipts:
            if receipt["kind"] == "source_readmission_group":
                group = next(item for item in state["source_readmission_groups"]
                             if item["group"]["id"] == receipt["group_id"])
                expected = group_archive_record(group, run_id=run_id, quote_id=quote["id"],
                                                event_metadata=state["change_events"])
                if receipt != expected:
                    raise IntegrityError("CURRENT_COMPLETION_SOURCE_GROUP_INVALID")
                approvals += receipt["human_approval_count"]
            else:
                change = state["changes"][receipt["kind"]]
                approval = change["approval"]
                outcome = change["outcome"]["outcome"]
                if (receipt["approval_digest"] != approval["approval_digest"]
                        or receipt["preview_digest"] != change["preview"]["preview_digest"]
                        or receipt["rebase_receipt_digest"] != outcome["rebase_receipt"]["digest"]
                        or receipt["workspace_receipt_digest"] != outcome["workspace_rebase_receipt"]["digest"]
                        or receipt["successor_quote_version"] != outcome["quote"]["version"]
                        or receipt["successor_quote_digest"] != outcome["quote"]["digest"]):
                    raise IntegrityError("CURRENT_COMPLETION_RECEIPT_INVALID")
                approvals += 1
        if approvals != record["human_approval_count"]:
            raise IntegrityError("CURRENT_COMPLETION_APPROVAL_COUNT_INVALID")
        binding = {"schema_version": "orgrebase.current-completion-binding.v1", "run_id": run_id,
                   "final_quote_ref": record["quote"]["ref"], "final_quote_digest": quote["digest"],
                   "archive_digest": sha256_digest(dict(archive)),
                   "archive_wire": {"scheme": WIRE_SCHEME, "digest": protocol_digest(dict(archive))}, "event_head": previous,
                   "events": chain["events"], "approval_count": approvals, "rebase_count": len(receipts)}
        binding["digest"] = sha256_digest(binding)
        observed_at = utc_datetime(state["completion_observed_at"])
        elapsed = observed_at - datetime(1970, 1, 1, tzinfo=UTC)
        observed_ns = str((elapsed.days * 86400 + elapsed.seconds) * 1_000_000_000 + elapsed.microseconds * 1000)
        metrics = [{"name": "orgrebase.completed_run.receipts", "unit": "{receipt}", "gauge": {"dataPoints": [
            {"attributes": [{"key": "kind", "value": {"stringValue": name}}], "asInt": str(count),
             "timeUnixNano": observed_ns}
            for name, count in (("owner_approval", approvals), ("quote_rebase", len(receipts)))
        ]}}]
        otlp = {"resourceMetrics": [{"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "orgrebase"}}]},
            "scopeMetrics": [{"scope": {"name": "orgrebase.completed_run", "version": "2"}, "metrics": metrics}]}]}
        projection = {"schema_version": "orgrebase.current-completion-projection.v1",
                      "completion_binding_digest": binding["digest"], "otlp_digest": sha256_digest(otlp),
                      "evidence_class": EVIDENCE, "claim_boundary": BOUNDARY,
                      "realtime_observation_claimed": False, "production_backend_claimed": False,
                      "production_sla_claimed": False, "synthetic_spans_created": 0,
                      "observed_at": state["completion_observed_at"], "time_basis": "RECEIPT_PROJECTION_OBSERVATION"}
        projection["digest"] = sha256_digest(projection)
    except (AttributeError, IntegrityError, KeyError, TypeError, ValueError, StopIteration) as error:
        return {**base, "failures": [str(error)]}
    return {**base, "status": "PROJECTED", "completion_binding": binding, "otlp": otlp,
            "projection_receipt": projection}
