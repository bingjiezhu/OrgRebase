"""Content-free OpenTelemetry OTLP/JSON evidence export."""

from __future__ import annotations

from typing import Any, ClassVar

from orgrebase import __version__
from orgrebase.digest import sha256_digest
from orgrebase.domain import RebaseReceipt
from orgrebase.store import StateStore


def _hex(value: Any, length: int) -> str:
    return sha256_digest(value).split(":", 1)[1][:length]


def _attributes(values: dict[str, str | int | bool]) -> list[dict[str, Any]]:
    attributes: list[dict[str, Any]] = []
    for key, value in sorted(values.items()):
        if isinstance(value, bool):
            encoded: dict[str, Any] = {"boolValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": str(value)}
        else:
            encoded = {"stringValue": value}
        attributes.append({"key": key, "value": encoded})
    return attributes


class ObservabilityExporter:
    """Map trusted receipts to OTLP without prompts, outputs, or business values."""

    scope: ClassVar[dict[str, str]] = {"name": "orgrebase", "version": __version__}
    resource: ClassVar[dict[str, Any]] = {
        "attributes": _attributes(
            {
                "service.name": "orgrebase",
                "service.version": __version__,
                "deployment.environment.name": "local-deterministic",
            }
        )
    }

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def export(
        self,
        *,
        collaboration: dict[str, Any],
        receipt: RebaseReceipt,
    ) -> dict[str, Any]:
        base_nanos = 1_776_000_000_000_000_000
        trace_id = _hex(receipt.digest, 32)
        control_span_id = _hex({"run": receipt.workflow_run_id, "kind": "control"}, 16)
        span_by_run = {run.id: _hex(run.id, 16) for run in receipt.agent_runs}
        task_by_id = {
            task.id: task
            for task in collaboration.get("orchestration_plan").tasks
        }
        plan_digest = collaboration["orchestration_plan"].digest
        spans: list[dict[str, Any]] = []
        for index, run in enumerate(receipt.agent_runs):
            task = task_by_id[run.task_id]
            span = {
                "traceId": trace_id,
                "spanId": span_by_run[run.id],
                "name": f"agent.run/{run.agent_name}",
                "kind": 1,
                "startTimeUnixNano": str(base_nanos + index * 2_000_000),
                "endTimeUnixNano": str(base_nanos + index * 2_000_000 + 1_000_000),
                "attributes": _attributes(
                    {
                        "gen_ai.operation.name": "invoke_agent",
                        "gen_ai.agent.name": run.agent_name,
                        "orgrebase.agent.run_id": run.id,
                        "orgrebase.workflow.run_id": receipt.workflow_run_id,
                        "orgrebase.run.nonce": receipt.run_nonce,
                        "orgrebase.input.digest": run.input_digest,
                        "orgrebase.output.digest": run.output_digest,
                        "orgrebase.orchestration.plan.digest": plan_digest,
                        "orgrebase.orchestration.task.digest": task.digest,
                        "orgrebase.orchestration.predecessor.count": len(
                            run.predecessor_run_ids
                        ),
                        "orgrebase.evidence.class": run.evidence_class.value,
                    }
                ),
                "status": {"code": 1},
            }
            if run.parent_run_id:
                span["parentSpanId"] = span_by_run[run.parent_run_id]
            else:
                span["parentSpanId"] = control_span_id
            linked_predecessors = tuple(
                predecessor
                for predecessor in run.predecessor_run_ids
                if predecessor != run.parent_run_id
            )
            if linked_predecessors:
                span["links"] = [
                    {
                        "traceId": trace_id,
                        "spanId": span_by_run[predecessor],
                        "attributes": _attributes(
                            {"orgrebase.link.kind": "orchestration_predecessor"}
                        ),
                    }
                    for predecessor in linked_predecessors
                ]
            spans.append(span)

        for index, invocation in enumerate(collaboration.get("tool_invocations", []), start=1):
            tool_receipt = invocation["receipt"]
            spans.append(
                {
                    "traceId": trace_id,
                    "spanId": _hex(tool_receipt["id"], 16),
                    "parentSpanId": span_by_run["run:gtm-steward@1"],
                    "name": "tool.call/orgrebase.read_dependency_evidence",
                    "kind": 3,
                    "startTimeUnixNano": str(base_nanos + (10 + index) * 2_000_000),
                    "endTimeUnixNano": str(base_nanos + (10 + index) * 2_000_000 + 1_000_000),
                    "attributes": _attributes(
                        {
                            "gen_ai.operation.name": "execute_tool",
                            "gen_ai.tool.name": "orgrebase.read_dependency_evidence",
                            "orgrebase.request.digest": tool_receipt["request_digest"],
                            "orgrebase.result.digest": tool_receipt["result_digest"],
                            "orgrebase.workflow.run_id": receipt.workflow_run_id,
                            "orgrebase.run.nonce": receipt.run_nonce,
                            "orgrebase.evidence.class": tool_receipt["evidence_class"],
                        }
                    ),
                    "status": {"code": 1},
                }
            )

        spans.append(
            {
                "traceId": trace_id,
                "spanId": control_span_id,
                "name": "control.apply_rebase",
                "kind": 2,
                "startTimeUnixNano": str(base_nanos),
                "endTimeUnixNano": str(base_nanos + 40_000_000),
                "attributes": _attributes(
                    {
                        "orgrebase.receipt.id": receipt.id,
                        "orgrebase.receipt.digest": receipt.digest,
                        "orgrebase.workflow.run_id": receipt.workflow_run_id,
                        "orgrebase.run.nonce": receipt.run_nonce,
                        "orgrebase.preview.ref": receipt.preview_ref,
                        "orgrebase.evidence.class": receipt.evidence_class.value,
                    }
                ),
                "status": {"code": 1},
            }
        )
        traces = {
            "resourceSpans": [
                {"resource": self.resource, "scopeSpans": [{"scope": self.scope, "spans": spans}]}
            ]
        }

        log_records = tuple(
            {
                "timeUnixNano": str(base_nanos + event["sequence_no"] * 1_000_000),
                "severityNumber": 9,
                "severityText": "INFO",
                "body": {"stringValue": event["event_type"]},
                "attributes": _attributes(
                    {
                        "orgrebase.event.sequence": event["sequence_no"],
                        "orgrebase.event.digest": event["event_digest"],
                        "orgrebase.event.previous_digest": event["previous_digest"],
                        "orgrebase.workflow.run_id": receipt.workflow_run_id,
                        "orgrebase.run.nonce": receipt.run_nonce,
                    }
                ),
            }
            for event in self.store.event_records()
        )
        logs = {
            "resourceLogs": [
                {
                    "resource": self.resource,
                    "scopeLogs": [{"scope": self.scope, "logRecords": log_records}],
                }
            ]
        }
        data_points = [
            {
                "attributes": _attributes(
                    {
                        "metric": key,
                        "orgrebase.workflow.run_id": receipt.workflow_run_id,
                        "orgrebase.run.nonce": receipt.run_nonce,
                    }
                ),
                "timeUnixNano": str(base_nanos + 32_000_000),
                "asInt": str(value),
            }
            for key, value in sorted(receipt.metrics.items())
        ]
        metrics = {
            "resourceMetrics": [
                {
                    "resource": self.resource,
                    "scopeMetrics": [
                        {
                            "scope": self.scope,
                            "metrics": [
                                {
                                    "name": "orgrebase.rebase.outcome",
                                    "unit": "{item}",
                                    "gauge": {"dataPoints": data_points},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        return {
            "schema_version": "orgrebase.observability.v1",
            "format": "OTLP_JSON_MAPPABLE",
            "semantic_conventions": {
                "release": "opentelemetry-semantic-conventions@v1.43.0",
                "source_commit": "89aae43",
                "gen_ai_stability": "DEVELOPMENT",
                "orgrebase_namespace": "orgrebase.*@v1",
            },
            "correlation": {
                "workflow_run_id": receipt.workflow_run_id,
                "run_nonce": receipt.run_nonce,
                "trace_id": trace_id,
                "receipt_digest": receipt.digest,
            },
            "privacy": {
                "content_capture": False,
                "prompt_capture": False,
                "output_capture": False,
                "identifiers": "stable IDs and SHA-256 digests only",
            },
            "traces": traces,
            "logs": logs,
            "metrics": metrics,
        }
