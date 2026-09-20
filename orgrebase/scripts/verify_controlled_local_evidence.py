#!/usr/bin/env python3
"""Product-independent verifier for a controlled-local operations pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from itertools import pairwise
from pathlib import Path
from typing import Any

REQUIRED_LAYER_ORDER = ("SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "TERMINAL")
REQUIRED_LAYERS = set(REQUIRED_LAYER_ORDER)
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_DIGEST = "sha256:" + "0" * 64
EXPECTED_LAYER_STATUS_ORDER = (
    ("SOURCE", "SUCCEEDED"),
    ("AGENTTEAMS", "ACCEPT"),
    ("TOOL", "SUCCEEDED"),
    ("SKILL", "CANARY"),
    ("TERMINAL", "COMPLETED"),
)
EXPECTED_LAYER_STATUSES = set(EXPECTED_LAYER_STATUS_ORDER)
CONTROLLED_TIMING_CLASS = "SYNTHETIC_DETERMINISTIC_PROJECTION"
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
SIGNAL_ROOTS = {
    "traces": "resourceSpans",
    "logs": "resourceLogs",
    "metrics": "resourceMetrics",
}
FORBIDDEN_TELEMETRY_KEYS = {
    "authorization",
    "prompt",
    "raw_prompt",
    "output",
    "raw_output",
    "secret",
    "source_content",
    "token",
}
INDEX_ATTRIBUTE_MAP = {
    "orgrebase.workflow.run_id": "run_id",
    "orgrebase.receipt.digest": "receipt_digest",
    "orgrebase.skill.package.digest": "skill_digest",
    "orgrebase.agentteams.task.id": "task_id",
    "orgrebase.evidence.class": "evidence_class",
    "orgrebase.chain.layer": "layer",
    "orgrebase.chain.sequence": "chain_sequence",
    "orgrebase.chain.status": "status",
    "orgrebase.target.write.count": "target_write_count",
    "orgrebase.attempt.fenced": "attempt_fenced",
    "orgrebase.chain.error_code": "error_code",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_ok(value: Any) -> bool:
    return isinstance(value, dict) and value.get("digest") == _digest(
        {key: item for key, item in value.items() if key != "digest"}
    )


def _model_ok(value: Any) -> bool:
    return _record_ok(value)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None and value != ZERO_DIGEST


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _decode_attributes(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            continue
        encoded = item["value"]
        scalar = next(
            (
                encoded[key]
                for key in ("stringValue", "intValue", "boolValue", "doubleValue")
                if key in encoded
            ),
            None,
        )
        if isinstance(item.get("key"), str) and scalar is not None:
            result[item["key"]] = scalar
    return result


def _privacy_safe(value: Any) -> bool:
    for node in _walk(value):
        attribute_name = node.get("key")
        if (
            isinstance(attribute_name, str)
            and attribute_name.lower() in FORBIDDEN_TELEMETRY_KEYS
        ):
            return False
        for key, item in node.items():
            if str(key).lower() in FORBIDDEN_TELEMETRY_KEYS:
                return False
            if isinstance(item, str) and "ORGREBASE_CANARY_SECRET_" in item:
                return False
    return True


def _attribute_values(payload: dict[str, Any], name: str) -> set[str]:
    values: set[str] = set()
    for node in _walk(payload):
        value = _decode_attributes(node.get("attributes")).get(name)
        if value is not None and str(value):
            values.add(str(value))
    return values


def _layer_status_pairs(payload: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for node in _walk(payload):
        attributes = _decode_attributes(node.get("attributes"))
        layer = attributes.get("orgrebase.chain.layer")
        status = attributes.get("orgrebase.chain.status")
        if isinstance(layer, str) and layer and status is not None and str(status):
            pairs.add((layer, str(status)))
    return pairs


def _trace_spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    resources = payload.get("resourceSpans")
    if not isinstance(resources, list) or len(resources) != 1:
        return []
    scopes = resources[0].get("scopeSpans") if isinstance(resources[0], dict) else None
    if not isinstance(scopes, list) or len(scopes) != 1:
        return []
    spans = scopes[0].get("spans") if isinstance(scopes[0], dict) else None
    return spans if isinstance(spans, list) and all(isinstance(item, dict) for item in spans) else []


def _log_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    resources = payload.get("resourceLogs")
    if not isinstance(resources, list) or len(resources) != 1:
        return []
    scopes = resources[0].get("scopeLogs") if isinstance(resources[0], dict) else None
    if not isinstance(scopes, list) or len(scopes) != 1:
        return []
    records = scopes[0].get("logRecords") if isinstance(scopes[0], dict) else None
    return records if isinstance(records, list) and all(isinstance(item, dict) for item in records) else []


def _causal_chain_failures(
    traces: dict[str, Any], logs: dict[str, Any]
) -> list[str]:
    """Validate topology independently of producer or retained SQLite indexes."""

    failures: list[str] = []
    spans = _trace_spans(traces)
    records = _log_records(logs)
    if len(spans) != len(EXPECTED_LAYER_STATUS_ORDER):
        failures.append("OTLP_CAUSAL_TRACE_SHAPE")
    if len(records) != len(EXPECTED_LAYER_STATUS_ORDER):
        failures.append("OTLP_CAUSAL_LOG_SHAPE")
    if len(spans) != len(EXPECTED_LAYER_STATUS_ORDER) or len(records) != len(
        EXPECTED_LAYER_STATUS_ORDER
    ):
        return failures

    span_facts: list[tuple[str, str, str]] = []
    trace_ids: list[str] = []
    span_ids: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for sequence, span in enumerate(spans, start=1):
        attributes = _decode_attributes(span.get("attributes"))
        layer = str(attributes.get("orgrebase.chain.layer", ""))
        status = str(attributes.get("orgrebase.chain.status", ""))
        observed_sequence = str(attributes.get("orgrebase.chain.sequence", ""))
        span_facts.append((layer, status, observed_sequence))
        trace_ids.append(str(span.get("traceId", "")))
        span_ids.append(str(span.get("spanId", "")))
        try:
            starts.append(int(span.get("startTimeUnixNano", "")))
            ends.append(int(span.get("endTimeUnixNano", "")))
        except (TypeError, ValueError):
            starts.append(-1)
            ends.append(-1)
        if sequence == 1:
            if "parentSpanId" in span:
                failures.append("OTLP_CAUSAL_PARENT")
        elif span.get("parentSpanId") != spans[sequence - 2].get("spanId"):
            failures.append("OTLP_CAUSAL_PARENT")
        if (
            span.get("name") != f"orgrebase.chain/{layer.lower()}"
            or span.get("kind") != 1
            or span.get("status") != {"code": 1}
        ):
            failures.append("OTLP_CAUSAL_SPAN_SHAPE")

    expected_facts = [
        (layer, status, str(sequence))
        for sequence, (layer, status) in enumerate(
            EXPECTED_LAYER_STATUS_ORDER, start=1
        )
    ]
    if span_facts != expected_facts:
        failures.append("OTLP_CAUSAL_ORDER")
    if (
        len(set(trace_ids)) != 1
        or not TRACE_ID.fullmatch(trace_ids[0])
        or trace_ids[0] == "0" * 32
    ):
        failures.append("OTLP_CAUSAL_TRACE_ID")
    if (
        len(set(span_ids)) != len(span_ids)
        or any(not SPAN_ID.fullmatch(item) or item == "0" * 16 for item in span_ids)
    ):
        failures.append("OTLP_CAUSAL_SPAN_ID")
    if (
        any(start < 0 or end <= start for start, end in zip(starts, ends, strict=True))
        or any(later <= earlier for earlier, later in pairwise(starts))
        or any(
            ends[index] > starts[index + 1] for index in range(len(starts) - 1)
        )
    ):
        failures.append("OTLP_CAUSAL_TIME")

    log_facts: list[tuple[str, str, str]] = []
    for index, record in enumerate(records):
        attributes = _decode_attributes(record.get("attributes"))
        log_facts.append(
            (
                str(attributes.get("orgrebase.chain.layer", "")),
                str(attributes.get("orgrebase.chain.status", "")),
                str(attributes.get("orgrebase.chain.sequence", "")),
            )
        )
        if (
            record.get("traceId") != spans[index].get("traceId")
            or record.get("spanId") != spans[index].get("spanId")
            or record.get("timeUnixNano") != spans[index].get("startTimeUnixNano")
            or record.get("body")
            != {
                "stringValue": (
                    f"{EXPECTED_LAYER_STATUS_ORDER[index][0]}:"
                    f"{EXPECTED_LAYER_STATUS_ORDER[index][1]}"
                )
            }
        ):
            failures.append("OTLP_CAUSAL_LOG_BINDING")
    if log_facts != expected_facts:
        failures.append("OTLP_CAUSAL_LOG_ORDER")
    return list(dict.fromkeys(failures))


def _expected_index_rows(payload: dict[str, Any]) -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for node in _walk(payload):
        trace_id = node.get("traceId")
        if isinstance(trace_id, str) and trace_id:
            rows.add(("trace_id", trace_id))
        attributes = _decode_attributes(node.get("attributes"))
        for source, target in INDEX_ATTRIBUTE_MAP.items():
            value = attributes.get(source)
            if value is not None and str(value):
                rows.add((target, str(value)))
    return rows


def _sqlite_logical_digest(path: Path) -> tuple[str, str, int, int]:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        projection: dict[str, list[list[Any]]] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            projection[table] = [
                list(row)
                for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid")
            ]
        event_row = connection.execute(
            "SELECT event_digest FROM domain_events ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone()
        return (
            _digest(projection),
            str(event_row[0]) if event_row else "GENESIS",
            len(projection.get("object_versions", [])),
            len(projection.get("artifacts", [])),
        )
    finally:
        connection.close()


def verify(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    index = _load(root / "evidence-index.json")
    entries = index.get("entries", []) if isinstance(index, dict) else []
    if not _record_ok(index):
        failures.append("INDEX_DIGEST")
    observed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "evidence-index.json"
    }
    indexed = {
        str(item.get("path")) for item in entries if isinstance(item, dict)
    }
    if (
        indexed != observed
        or index.get("entry_count") != len(observed)
        or not isinstance(entries, list)
        or len(entries) != len(observed)
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "bytes"}
            for item in entries
        )
    ):
        failures.append("INDEX_CLOSURE")
    if index.get("pack_digest") != _digest(entries):
        failures.append("PACK_DIGEST")
    for item in entries:
        path = (root / str(item.get("path"))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            failures.append(f"PATH_ESCAPE:{item.get('path')}")
            continue
        if (
            not path.is_file()
            or item.get("sha256") != _file_digest(path)
            or item.get("bytes") != path.stat().st_size
        ):
            failures.append(f"FILE_BINDING:{item.get('path')}")

    summary = _load(root / "summary.json")
    if (
        not _record_ok(summary)
        or summary.get("status") != "PASS"
        or summary.get("evidence_class")
        != "CONTROLLED_LOCAL_INTEGRATED_OPERATIONS"
        or summary.get("production_readiness") is not False
        or summary.get("approval_apply_status") != "NOT_RUN"
        or summary.get("external_enterprise_connectors") != "NOT_RUN"
        or summary.get("canonical_target_writes") != 0
        or set(summary.get("required_layers", [])) != REQUIRED_LAYERS
        or summary.get("causal_chain") != list(REQUIRED_LAYER_ORDER)
        or summary.get("telemetry_timing_class") != CONTROLLED_TIMING_CLASS
    ):
        failures.append("SUMMARY")

    run_id = str(summary.get("run_id") or "")
    task_id = str(summary.get("task_id") or "")
    delegation_id = str(summary.get("delegation_id") or "")
    authority_digest_fields = (
        "native_receipt_digest",
        "skill_package_digest",
        "skill_invocation_receipt_digest",
        "graph_digest",
    )
    expected_correlation_root = _digest(
        {
            "run_id": run_id,
            "task_id": task_id,
            "delegation_id": delegation_id,
            "coalition_result_binding_digest": summary.get(
                "coalition_result_binding_digest"
            ),
            **{field: summary.get(field) for field in authority_digest_fields},
        }
    )
    if (
        not run_id
        or not task_id
        or not delegation_id
        or any(not _valid_digest(summary.get(field)) for field in authority_digest_fields)
        or (
            summary.get("coalition_result_binding_digest") != "NOT_BOUND"
            and not _valid_digest(summary.get("coalition_result_binding_digest"))
        )
        or summary.get("correlation_root") != expected_correlation_root
    ):
        failures.append("CORRELATION_ROOT")

    source_payload = _load(root / "source/payload.json")
    source = _load(root / "source/receipt.json")
    revalidation = _load(root / "source/not-modified-receipt.json")
    source_payload_digest = _digest(source_payload)
    source_etag = f'"{source_payload_digest.split(":", 1)[1]}"'
    tool = _load(root / "tool/receipt.json")
    tool_result = _load(root / "tool/result.json")
    if (
        not isinstance(source_payload, dict)
        or set(source_payload)
        != {
            "customer_id",
            "organization_id",
            "revision",
            "schema_version",
            "source_id",
            "synthetic",
        }
        or source_payload.get("schema_version")
        != "orgrebase.controlled-quote-source.v1"
        or source_payload.get("synthetic") is not True
        or not all(
            isinstance(source_payload.get(field), str) and source_payload.get(field)
            for field in ("customer_id", "organization_id", "revision", "source_id")
        )
        or not _model_ok(source)
        or source.get("run_id") != run_id
        or source.get("id") != f"http-source:{run_id}"
        or source.get("connector_kind") != "SOURCE"
        or source.get("operation") != "READ_QUOTE_SOURCE"
        or source.get("status") != "SUCCEEDED"
        or source.get("http_status") != 200
        or source.get("attempts") != 1
        or source.get("endpoint_class") != "LOOPBACK_TCP"
        or source.get("evidence_class") != "CONTROLLED_LOCAL_REAL_HTTP"
        or source.get("target_writes") != 0
        or source.get("request_digest")
        != _digest(
            {"method": "GET", "path": "/v1/quote-source/acme", "etag": None}
        )
        or source.get("response_digest") != source_payload_digest
        or source.get("etag") != source_etag
        or summary.get("source_receipt_digest") != source.get("digest")
    ):
        failures.append("SOURCE_RECEIPT")
    if (
        not _model_ok(revalidation)
        or revalidation.get("run_id") != run_id
        or revalidation.get("id") != f"http-source:{run_id}"
        or revalidation.get("connector_kind") != "SOURCE"
        or revalidation.get("operation") != "READ_QUOTE_SOURCE"
        or revalidation.get("status") != "NOT_MODIFIED"
        or revalidation.get("http_status") != 304
        or revalidation.get("attempts") != 1
        or revalidation.get("endpoint_class") != "LOOPBACK_TCP"
        or revalidation.get("evidence_class") != "CONTROLLED_LOCAL_REAL_HTTP"
        or revalidation.get("target_writes") != 0
        or revalidation.get("request_digest")
        != _digest(
            {
                "method": "GET",
                "path": "/v1/quote-source/acme",
                "etag": source_etag,
            }
        )
        or revalidation.get("response_digest")
        != "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        or revalidation.get("etag") != source_etag
        or summary.get("source_revalidation_receipt_digest")
        != revalidation.get("digest")
    ):
        failures.append("SOURCE_REVALIDATION")
    expected_tool_body = {
        "run_id": run_id,
        "task_id": task_id,
        "target_id": "work:enterprise-quote",
        "graph_digest": summary.get("graph_digest"),
    }
    expected_tool_request = {
        **expected_tool_body,
        "request_digest": _digest(expected_tool_body),
    }
    if (
        not _model_ok(tool)
        or tool.get("run_id") != run_id
        or tool.get("id") != f"http-tool:{run_id}:{task_id}"
        or tool.get("connector_kind") != "TOOL"
        or tool.get("operation") != "READ_DEPENDENCY_EVIDENCE"
        or tool.get("status") != "SUCCEEDED"
        or tool.get("http_status") != 200
        or tool.get("attempts") != 1
        or tool.get("endpoint_class") != "LOOPBACK_TCP"
        or tool.get("evidence_class") != "CONTROLLED_LOCAL_REAL_HTTP"
        or tool.get("target_writes") != 0
        or tool.get("request_digest") != _digest(expected_tool_request)
        or tool.get("response_digest") != _digest(tool_result)
        or summary.get("tool_receipt_digest") != tool.get("digest")
    ):
        failures.append("TOOL_RECEIPT")
    if (
        not isinstance(tool_result, dict)
        or set(tool_result)
        != {
            "dependencies",
            "graph_digest",
            "run_id",
            "schema_version",
            "target_id",
            "target_writes",
            "task_id",
        }
        or tool_result.get("schema_version")
        != "orgrebase.controlled-dependency-result.v1"
        or tool_result.get("run_id") != run_id
        or tool_result.get("task_id") != task_id
        or tool_result.get("target_id") != "work:enterprise-quote"
        or tool_result.get("graph_digest") != summary.get("graph_digest")
        or tool_result.get("target_writes") != 0
        or not isinstance(tool_result.get("dependencies"), list)
        or not all(isinstance(item, str) for item in tool_result.get("dependencies", []))
    ):
        failures.append("TOOL_RESULT")
    expected_tool_result_digest = _digest(tool_result)
    if summary.get("tool_result_digest") != expected_tool_result_digest:
        failures.append("TOOL_RESULT_DIGEST")

    retained_payloads = {
        signal: _load(root / "observability" / f"{signal}.otlp.json")
        for signal in SIGNAL_ROOTS
    }
    export_receipts = _load(root / "observability/export-receipts.json")
    export_by_signal = {
        str(item.get("operation", "")).removeprefix("EXPORT_").lower(): item
        for item in export_receipts
        if isinstance(item, dict)
    } if isinstance(export_receipts, list) else {}
    expected_correlation_attributes = {
        "orgrebase.workflow.run_id": run_id,
        "orgrebase.receipt.digest": expected_correlation_root,
        "orgrebase.agentteams.task.id": task_id,
        "orgrebase.skill.package.digest": summary.get("skill_package_digest"),
        "orgrebase.evidence.class": summary.get("evidence_class"),
        "orgrebase.native.receipt.digest": summary.get("native_receipt_digest"),
        "orgrebase.skill.invocation.receipt.digest": summary.get(
            "skill_invocation_receipt_digest"
        ),
        "orgrebase.tool.receipt.digest": tool.get("digest"),
        "orgrebase.tool.result.digest": expected_tool_result_digest,
        "orgrebase.tool.http.attempts": "1",
        "orgrebase.tool.retry.count": "0",
        "orgrebase.tool.retry.binding": "BOUND",
        "orgrebase.skill.name": summary.get("skill_name"),
        "orgrebase.skill.name.binding": "BOUND",
        "orgrebase.skill.package.binding": "BOUND",
        "orgrebase.skill.invocation.binding": "BOUND",
        "orgrebase.tool.receipt.binding": "BOUND",
        "orgrebase.tool.result.binding": "BOUND",
        "orgrebase.agentteams.task.binding": "BOUND",
        "orgrebase.telemetry.timing.class": CONTROLLED_TIMING_CLASS,
        "orgrebase.stage.duration.status": "NOT_OBSERVED",
        "orgrebase.queue.duration.status": "NOT_OBSERVED",
        "orgrebase.target.write.count": "0",
    }
    optional_fact_expectations: dict[str, str | int | None] = {}

    def bind_optional(
        *,
        value_attribute: str,
        binding_attribute: str,
        summary_value: Any,
        summary_binding: str,
    ) -> None:
        expected_correlation_attributes[binding_attribute] = summary_binding
        if summary_binding == "BOUND":
            optional_fact_expectations[value_attribute] = summary_value
            expected_correlation_attributes[value_attribute] = summary_value
        else:
            optional_fact_expectations[value_attribute] = None

    bind_optional(
        value_attribute="orgrebase.workflow.nonce",
        binding_attribute="orgrebase.workflow.nonce.binding",
        summary_value=summary.get("native_nonce"),
        summary_binding=str(summary.get("native_nonce_binding")),
    )
    bind_optional(
        value_attribute="orgrebase.agentteams.project.id",
        binding_attribute="orgrebase.agentteams.project.binding",
        summary_value=summary.get("agentteams_project_id"),
        summary_binding=str(summary.get("agentteams_project_binding")),
    )
    bind_optional(
        value_attribute="orgrebase.agentteams.attempt.id",
        binding_attribute="orgrebase.agentteams.attempt.binding",
        summary_value=summary.get("agentteams_attempt_id"),
        summary_binding=str(summary.get("agentteams_attempt_binding")),
    )
    if summary.get("agentteams_attempt_binding") == "BOUND":
        expected_correlation_attributes["orgrebase.agentteams.attempt.number"] = summary.get(
            "agentteams_attempt_number"
        )
    else:
        optional_fact_expectations["orgrebase.agentteams.attempt.number"] = None
    bind_optional(
        value_attribute="orgrebase.agentteams.retry.count",
        binding_attribute="orgrebase.agentteams.retry.binding",
        summary_value=summary.get("agentteams_retry_count"),
        summary_binding=(
            "NOT_BOUND"
            if summary.get("agentteams_retry_count") == "NOT_BOUND"
            else "BOUND"
        ),
    )
    bind_optional(
        value_attribute="orgrebase.agentteams.reassign.count",
        binding_attribute="orgrebase.agentteams.reassign.binding",
        summary_value=summary.get("agentteams_reassign_count"),
        summary_binding=(
            "NOT_BOUND"
            if summary.get("agentteams_reassign_count") == "NOT_BOUND"
            else "BOUND"
        ),
    )
    bind_optional(
        value_attribute="orgrebase.skill.version",
        binding_attribute="orgrebase.skill.version.binding",
        summary_value=summary.get("skill_version"),
        summary_binding=str(summary.get("skill_version_binding")),
    )
    coalition_digest = summary.get("coalition_result_binding_digest")
    bind_optional(
        value_attribute="orgrebase.coalition.result_binding.digest",
        binding_attribute="orgrebase.coalition.result_binding.binding",
        summary_value=coalition_digest,
        summary_binding="NOT_BOUND" if coalition_digest == "NOT_BOUND" else "BOUND",
    )
    if delegation_id.startswith("sha256:"):
        expected_correlation_attributes[
            "orgrebase.agentteams.delegation.digest"
        ] = delegation_id
        expected_correlation_attributes[
            "orgrebase.agentteams.delegation.digest.binding"
        ] = "BOUND"
        expected_correlation_attributes[
            "orgrebase.agentteams.delegation.id.binding"
        ] = "NOT_BOUND"
        optional_fact_expectations["orgrebase.agentteams.delegation.id"] = None
    else:
        expected_correlation_attributes["orgrebase.agentteams.delegation.id"] = delegation_id
        expected_correlation_attributes[
            "orgrebase.agentteams.delegation.id.binding"
        ] = "BOUND"
        expected_correlation_attributes[
            "orgrebase.agentteams.delegation.digest.binding"
        ] = "NOT_BOUND"
        optional_fact_expectations["orgrebase.agentteams.delegation.digest"] = None
    for signal, payload in retained_payloads.items():
        export = export_by_signal.get(signal, {})
        if (
            not isinstance(payload, dict)
            or set(payload) != {SIGNAL_ROOTS[signal]}
            or not _privacy_safe(payload)
            or not _model_ok(export)
            or export.get("run_id") != run_id
            or export.get("connector_kind") != "OTLP"
            or export.get("operation") != f"EXPORT_{signal.upper()}"
            or export.get("status") != "SUCCEEDED"
            or export.get("http_status") != 200
            or export.get("endpoint_class") != "LOOPBACK_TCP"
            or export.get("evidence_class") != "CONTROLLED_LOCAL_REAL_HTTP"
            or export.get("target_writes") != 0
            or export.get("request_digest") != _digest(payload)
            or export.get("response_digest") != _digest({"partialSuccess": {}})
            or any(
                _attribute_values(payload, attribute) != {str(expected)}
                for attribute, expected in expected_correlation_attributes.items()
            )
            or any(
                expected is None and _attribute_values(payload, attribute)
                for attribute, expected in optional_fact_expectations.items()
            )
        ):
            failures.append(f"OTLP_PAYLOAD_BINDING:{signal}")
    if set(export_by_signal) != set(SIGNAL_ROOTS):
        failures.append("OTLP_EXPORTS")
    observed_pairs = _layer_status_pairs(retained_payloads["traces"]) | _layer_status_pairs(
        retained_payloads["logs"]
    )
    if (
        observed_pairs != EXPECTED_LAYER_STATUSES
        or _layer_status_pairs(retained_payloads["metrics"])
    ):
        failures.append("OTLP_LAYER_STATUS_BINDING")
    failures.extend(
        _causal_chain_failures(
            retained_payloads["traces"], retained_payloads["logs"]
        )
    )
    query = _load(root / "observability/query-receipt.json")
    alerts = _load(root / "observability/alert-receipt.json")
    negative_alert = _load(root / "observability/negative-alert-probe.json")
    retention = _load(root / "observability/retention-receipt.json")
    privacy = _load(root / "observability/privacy-probe.json")
    rejection = _load(root / "observability/privacy-rejection.json")
    if (
        not _model_ok(query)
        or query.get("count") != 3
        or query.get("query", {}).get("run_id") != run_id
        or summary.get("telemetry_query_receipt_digest") != query.get("digest")
    ):
        failures.append("TELEMETRY_QUERY")
    if (
        not _model_ok(alerts)
        or alerts.get("run_id") != run_id
        or alerts.get("status") != "PASS"
        or alerts.get("alerts") != []
        or set(alerts.get("observed_layers", [])) != REQUIRED_LAYERS
        or summary.get("alert_receipt_digest") != alerts.get("digest")
    ):
        failures.append("ALERT_SUCCESS_PATH")
    if (
        not _model_ok(negative_alert)
        or negative_alert.get("status") != "ALERT"
        or not negative_alert.get("alerts")
    ):
        failures.append("ALERT_NEGATIVE_PROBE")
    if (
        not _model_ok(retention)
        or retention.get("canonical_business_evidence_deleted") is not False
        or not retention.get("deleted_ingestion_ids")
        or summary.get("retention_receipt_digest") != retention.get("digest")
    ):
        failures.append("RETENTION")
    if (
        privacy
        != {
            "exception_class": "IntegrityError",
            "raw_payload_retained": False,
            "reason_code": "PRIVACY_PAYLOAD_REJECTED",
            "status": "PASS",
        }
        or rejection.get("raw_payload_retained") is not False
        or not str(rejection.get("payload_digest", "")).startswith("sha256:")
        or "ORGREBASE_CANARY_SECRET_" in json.dumps(rejection)
    ):
        failures.append("PRIVACY_REJECTION")

    database = root / "observability/telemetry.sqlite"
    if b"ORGREBASE_CANARY_SECRET_" in database.read_bytes():
        failures.append("PRIVACY_CANARY_PERSISTED")
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    main_records: list[dict[str, Any]] = []
    try:
        rows = connection.execute(
            "SELECT id,signal,payload_digest,ingested_at,payload_json "
            "FROM otlp_ingestions ORDER BY id"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row[4])
            except json.JSONDecodeError:
                failures.append(f"TELEMETRY_DATABASE_JSON:{row[0]}")
                continue
            if (
                not isinstance(payload, dict)
                or str(row[1]) not in SIGNAL_ROOTS
                or set(payload) != {SIGNAL_ROOTS.get(str(row[1]), "")}
                or _digest(payload) != row[2]
            ):
                failures.append(f"TELEMETRY_DATABASE_DIGEST:{row[0]}")
            if not _privacy_safe(payload):
                failures.append(f"TELEMETRY_DATABASE_PRIVACY:{row[0]}")
            observed_index_rows = {
                (str(index_row[0]), str(index_row[1]))
                for index_row in connection.execute(
                    "SELECT key,value FROM otlp_indexes WHERE ingestion_id=?",
                    (row[0],),
                )
            }
            if observed_index_rows != _expected_index_rows(payload):
                failures.append(f"TELEMETRY_DATABASE_INDEX:{row[0]}")
            if _attribute_values(payload, "orgrebase.workflow.run_id") == {run_id}:
                main_records.append(
                    {
                        "id": int(row[0]),
                        "signal": str(row[1]),
                        "payload_digest": str(row[2]),
                        "ingested_at": int(row[3]),
                        "payload": payload,
                    }
                )
        rejection_rows = connection.execute(
            "SELECT signal,run_id,payload_digest,reason_code FROM otlp_rejections "
            "WHERE run_id=? ORDER BY id DESC",
            (f"{run_id}:privacy-probe",),
        ).fetchall()
    finally:
        connection.close()
    main_by_signal = {record["signal"]: record for record in main_records}
    if (
        len(main_records) != 3
        or set(main_by_signal) != set(SIGNAL_ROOTS)
        or any(
            record["payload_digest"] != _digest(retained_payloads[signal])
            or record["payload"] != retained_payloads[signal]
            for signal, record in main_by_signal.items()
        )
        or (
            _layer_status_pairs(main_by_signal.get("traces", {}).get("payload", {}))
            | _layer_status_pairs(main_by_signal.get("logs", {}).get("payload", {}))
        )
        != EXPECTED_LAYER_STATUSES
    ):
        failures.append("TELEMETRY_DATABASE_CHAIN")
    ordered_main = sorted(main_records, key=lambda item: item["id"])
    query_projection = [
        {
            key: record[key]
            for key in ("id", "signal", "payload_digest", "ingested_at")
        }
        for record in ordered_main
    ]
    expected_query = {
        "run_id": run_id,
        "skill_digest": summary.get("skill_package_digest"),
    }
    if (
        query.get("query") != expected_query
        or query.get("query_digest") != _digest(expected_query)
        or query.get("matched_ingestion_ids")
        != [record["id"] for record in ordered_main]
        or query.get("matched_payload_digests")
        != [record["payload_digest"] for record in ordered_main]
        or query.get("result_digest") != _digest(query_projection)
    ):
        failures.append("TELEMETRY_QUERY_REPLAY")
    if (
        len(rejection_rows) != 1
        or rejection
        != {
            "signal": str(rejection_rows[0][0]),
            "run_id": str(rejection_rows[0][1]),
            "payload_digest": str(rejection_rows[0][2]),
            "reason_code": str(rejection_rows[0][3]),
            "raw_payload_retained": False,
        }
    ):
        failures.append("PRIVACY_REJECTION_DATABASE")

    backup = _load(root / "operations/backup-restore-receipt.json")
    capacity = _load(root / "operations/capacity-smoke-receipt.json")
    deployment = _load(root / "operations/deployment-profile.json")
    sbom = _load(root / "operations/sbom.cdx.json")
    state_paths = (
        root / "operations/canonical-state.sqlite",
        root / "operations/canonical-state.backup.sqlite",
        root / "operations/canonical-state.restored.sqlite",
    )
    try:
        state_facts = tuple(_sqlite_logical_digest(path) for path in state_paths)
    except (OSError, sqlite3.DatabaseError):
        state_facts = ()
    state_digests = tuple(item[0] for item in state_facts)
    state_heads = tuple(item[1] for item in state_facts)
    state_object_counts = tuple(item[2] for item in state_facts)
    state_artifact_counts = tuple(item[3] for item in state_facts)
    if (
        not _model_ok(backup)
        or backup.get("status") != "PASS"
        or backup.get("production_sla_claimed") is not False
        or summary.get("backup_restore_receipt_digest") != backup.get("digest")
        or len(state_facts) != 3
        or len(set(state_digests)) != 1
        or len(set(state_heads)) != 1
        or len(set(state_object_counts)) != 1
        or len(set(state_artifact_counts)) != 1
        or backup.get("source_digest") != state_digests[0]
        or backup.get("backup_digest") != state_digests[1]
        or backup.get("restored_digest") != state_digests[2]
        or backup.get("source_event_head") != state_heads[0]
        or backup.get("restored_event_head") != state_heads[2]
        or backup.get("object_count") != state_object_counts[0]
        or backup.get("artifact_count") != state_artifact_counts[0]
    ):
        failures.append("BACKUP_RESTORE")
    if (
        not _model_ok(capacity)
        or capacity.get("sample_count") != 25
        or capacity.get("failures") != 0
        or capacity.get("sla_met_claimed") is not False
        or capacity.get("production_ready_claimed") is not False
        or summary.get("capacity_receipt_digest") != capacity.get("digest")
    ):
        failures.append("CAPACITY")
    external = deployment.get("external_connectors", {})
    if (
        deployment.get("production_ready") is not False
        or not external
        or set(external.values()) != {"NOT_RUN"}
    ):
        failures.append("DEPLOYMENT_BOUNDARY")
    if (
        sbom.get("bomFormat") != "CycloneDX"
        or sbom.get("specVersion") != "1.5"
        or not sbom.get("components")
        or {
            item.get("name"): item.get("value")
            for item in sbom.get("properties", [])
        }.get("orgrebase.vulnerability-absence-claimed")
        != "false"
    ):
        failures.append("SBOM")

    if failures:
        raise SystemExit("CONTROLLED_LOCAL_EVIDENCE_VERIFY_FAILED:" + ",".join(failures))
    return {
        "status": "PASS",
        "run_id": run_id,
        "task_id": task_id,
        "required_layers": sorted(REQUIRED_LAYERS),
        "evidence_class": summary["evidence_class"],
        "approval_apply_status": "NOT_RUN",
        "production_readiness": False,
        "pack_digest": index["pack_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.evidence.resolve()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
