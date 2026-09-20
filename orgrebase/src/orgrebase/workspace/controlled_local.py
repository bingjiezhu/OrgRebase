"""Controlled-local HTTP connectors and an OTLP/HTTP evidence backend.

This module proves protocol and correlation behavior with synthetic data over a
real loopback TCP boundary.  It is deliberately not a production connector,
collector, SLA monitor, or enterprise IAM implementation.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.local_storage import prepare_private_sqlite_path

CONTROLLED_HTTP_EVIDENCE_CLASS = "CONTROLLED_LOCAL_REAL_HTTP"
CONTROLLED_OPS_EVIDENCE_CLASS = "OBSERVED_CONTROLLED_LOCAL"
CAUSAL_LAYER_ORDER = ("SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "TERMINAL")
CONTROLLED_TIMING_CLASS = "SYNTHETIC_DETERMINISTIC_PROJECTION"
_SIGNAL_ROOT = {
    "traces": "resourceSpans",
    "logs": "resourceLogs",
    "metrics": "resourceMetrics",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _raw_sha256(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


class ControlledHTTPReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.controlled-http-receipt.v1"] = (
        "orgrebase.controlled-http-receipt.v1"
    )
    id: str
    connector_kind: Literal["SOURCE", "TOOL", "OTLP"]
    operation: str
    run_id: str
    request_digest: str
    response_digest: str
    status: Literal["SUCCEEDED", "NOT_MODIFIED", "FAILED"]
    http_status: int
    attempts: int = Field(ge=1)
    etag: str | None = None
    endpoint_class: Literal["LOOPBACK_TCP"] = "LOOPBACK_TCP"
    evidence_class: Literal["CONTROLLED_LOCAL_REAL_HTTP"] = CONTROLLED_HTTP_EVIDENCE_CLASS
    target_writes: int = Field(ge=0)


class TelemetryQueryReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.telemetry-query-receipt.v1"] = (
        "orgrebase.telemetry-query-receipt.v1"
    )
    id: str
    query: dict[str, str | int]
    query_digest: str
    matched_ingestion_ids: tuple[int, ...]
    matched_payload_digests: tuple[str, ...]
    result_digest: str
    count: int = Field(ge=0)
    evidence_class: Literal["OBSERVED_CONTROLLED_LOCAL"] = CONTROLLED_OPS_EVIDENCE_CLASS


class AlertRecord(ContentAddressedModel):
    schema_version: Literal["orgrebase.telemetry-alert.v1"] = "orgrebase.telemetry-alert.v1"
    id: str
    run_id: str
    rule_id: str
    severity: Literal["WARNING", "CRITICAL"]
    reason_code: str
    evidence_refs: tuple[str, ...] = ()


class AlertEvaluationReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.alert-evaluation-receipt.v1"] = (
        "orgrebase.alert-evaluation-receipt.v1"
    )
    id: str
    run_id: str
    required_layers: tuple[str, ...]
    observed_layers: tuple[str, ...]
    alerts: tuple[AlertRecord, ...]
    input_digest: str
    status: Literal["PASS", "ALERT"]
    evidence_class: Literal["OBSERVED_CONTROLLED_LOCAL"] = CONTROLLED_OPS_EVIDENCE_CLASS


class RetentionReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.telemetry-retention-receipt.v1"] = (
        "orgrebase.telemetry-retention-receipt.v1"
    )
    id: str
    cutoff_epoch_seconds: int
    rejected_cutoff_epoch_seconds: int
    deleted_ingestion_ids: tuple[int, ...]
    deleted_rejection_ids: tuple[int, ...]
    retained_count: int = Field(ge=0)
    retained_rejection_count: int = Field(ge=0)
    canonical_business_evidence_deleted: Literal[False] = False
    evidence_class: Literal["OBSERVED_CONTROLLED_LOCAL"] = CONTROLLED_OPS_EVIDENCE_CLASS


class BackupRestoreReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.backup-restore-receipt.v1"] = (
        "orgrebase.backup-restore-receipt.v1"
    )
    id: str
    source_digest: str
    backup_digest: str
    restored_digest: str
    source_event_head: str
    restored_event_head: str
    object_count: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    status: Literal["PASS", "FAIL"]
    evidence_class: Literal["OBSERVED_CONTROLLED_LOCAL"] = CONTROLLED_OPS_EVIDENCE_CLASS
    production_sla_claimed: Literal[False] = False


class CapacitySmokeReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.capacity-smoke-receipt.v1"] = (
        "orgrebase.capacity-smoke-receipt.v1"
    )
    id: str
    operation: str
    sample_count: int = Field(gt=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    observed_requests_per_second: float = Field(ge=0)
    environment_facts: dict[str, str | int]
    evidence_class: Literal["OBSERVED_CONTROLLED_LOCAL"] = CONTROLLED_OPS_EVIDENCE_CLASS
    sla_met_claimed: Literal[False] = False
    production_ready_claimed: Literal[False] = False


class _EnterpriseHTTPHandler(BaseHTTPRequestHandler):
    server: _EnterpriseHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:  # pragma: no cover - noise guard
        return

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.server.token}"

    def _send_json(self, status: int, payload: Mapping[str, Any], *, etag: str | None = None) -> None:
        raw = _canonical_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path in {"/healthz", "/readyz"}:
            self._send_json(200, {"status": "ok", "profile": "controlled-local"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "UNAUTHORIZED"})
            return
        if self.path != "/v1/quote-source/acme":
            self._send_json(404, {"error": "NOT_FOUND"})
            return
        if self.headers.get("If-None-Match") == self.server.source_etag:
            self.send_response(304)
            self.send_header("ETag", self.server.source_etag)
            self.end_headers()
            return
        self._send_json(200, self.server.source_payload, etag=self.server.source_etag)

    def do_POST(self) -> None:
        if not self._authorized():
            self._send_json(401, {"error": "UNAUTHORIZED"})
            return
        if self.path != "/v1/dependency-tool":
            self._send_json(404, {"error": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "MALFORMED_JSON"})
            return
        required = {"run_id", "task_id", "target_id", "graph_digest", "request_digest"}
        if not isinstance(payload, dict) or set(payload) != required:
            self._send_json(422, {"error": "TOOL_REQUEST_SCHEMA_MISMATCH"})
            return
        expected = sha256_digest({key: payload[key] for key in sorted(required - {"request_digest"})})
        if payload["request_digest"] != expected:
            self._send_json(409, {"error": "TOOL_REQUEST_DIGEST_MISMATCH"})
            return
        response = {
            "schema_version": "orgrebase.controlled-dependency-result.v1",
            "run_id": payload["run_id"],
            "task_id": payload["task_id"],
            "target_id": payload["target_id"],
            "graph_digest": payload["graph_digest"],
            "dependencies": list(self.server.dependencies),
            "target_writes": 0,
        }
        if self.server.dependency_source_values:
            response["source_values"] = self.server.dependency_source_values
        self._send_json(200, response)


class _EnterpriseHTTPServer(ThreadingHTTPServer):
    token: str
    source_payload: dict[str, Any]
    source_etag: str
    dependencies: tuple[str, ...]
    dependency_source_values: dict[str, dict[str, Any]]


class ControlledEnterpriseServer(AbstractContextManager["ControlledEnterpriseServer"]):
    """Synthetic Source and Tool endpoints over a real loopback TCP socket."""

    def __init__(
        self,
        *,
        token: str = "controlled-local-token",
        dependencies: tuple[str, ...] | None = None,
        dependency_source_values: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        payload = {
            "schema_version": "orgrebase.controlled-quote-source.v1",
            "source_id": "source:controlled:quote-acme",
            "revision": "r1",
            "customer_id": "customer:acme",
            "organization_id": "org:northstar",
            "synthetic": True,
        }
        self._server = _EnterpriseHTTPServer(("127.0.0.1", 0), _EnterpriseHTTPHandler)
        self._server.token = token
        self._server.source_payload = payload
        self._server.source_etag = f'"{_raw_sha256(_canonical_bytes(payload)).split(":", 1)[1]}"'
        self._server.dependencies = dependencies or (
            "claim:product.launch_date@v7",
            "policy:finance.currency@v1",
        )
        self._server.dependency_source_values = json.loads(
            json.dumps(dict(dependency_source_values or {}), ensure_ascii=False)
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.token = token

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> ControlledEnterpriseServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class ControlledEnterpriseClient:
    def __init__(self, base_url: str, *, token: str, timeout_seconds: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, request: urllib.request.Request) -> tuple[int, bytes, Mapping[str, str]]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def health(self) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.base_url}/healthz")
        status, raw, _headers = self._request(request)
        payload = json.loads(raw or b"{}")
        if status != 200 or payload != {"profile": "controlled-local", "status": "ok"}:
            raise IntegrityError(f"CONTROLLED_CONNECTOR_UNHEALTHY:{status}")
        return payload

    def read_source(self, *, run_id: str, etag: str | None = None) -> tuple[dict[str, Any] | None, ControlledHTTPReceipt]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(f"{self.base_url}/v1/quote-source/acme", headers=headers)
        status, raw, response_headers = self._request(request)
        request_view = {"method": "GET", "path": "/v1/quote-source/acme", "etag": etag}
        if status == 304:
            payload = None
            receipt_status: Literal["SUCCEEDED", "NOT_MODIFIED", "FAILED"] = "NOT_MODIFIED"
        elif status == 200:
            payload = json.loads(raw or b"{}")
            receipt_status = "SUCCEEDED"
            expected_keys = {
                "customer_id",
                "organization_id",
                "revision",
                "schema_version",
                "source_id",
                "synthetic",
            }
            response_etag = response_headers.get("ETag")
            expected_etag = f'"{_raw_sha256(raw).split(":", 1)[1]}"'
            if (
                not isinstance(payload, dict)
                or set(payload) != expected_keys
                or payload.get("schema_version") != "orgrebase.controlled-quote-source.v1"
                or payload.get("synthetic") is not True
                or not all(
                    isinstance(payload.get(key), str)
                    for key in ("customer_id", "organization_id", "revision", "source_id")
                )
                or response_etag != expected_etag
            ):
                raise IntegrityError("CONTROLLED_SOURCE_SCHEMA_OR_ETAG_MISMATCH")
        else:
            payload = json.loads(raw or b"{}")
            receipt_status = "FAILED"
        receipt = ControlledHTTPReceipt(
            id=f"http-source:{run_id}",
            connector_kind="SOURCE",
            operation="READ_QUOTE_SOURCE",
            run_id=run_id,
            request_digest=sha256_digest(request_view),
            response_digest=_raw_sha256(raw),
            status=receipt_status,
            http_status=status,
            attempts=1,
            etag=response_headers.get("ETag"),
            target_writes=0,
        )
        if receipt.status == "FAILED":
            raise IntegrityError(f"CONTROLLED_SOURCE_HTTP_FAILED:{status}")
        return payload, receipt

    def call_dependency_tool(
        self,
        *,
        run_id: str,
        task_id: str,
        target_id: str,
        graph_digest: str,
    ) -> tuple[dict[str, Any], ControlledHTTPReceipt]:
        body = {
            "run_id": run_id,
            "task_id": task_id,
            "target_id": target_id,
            "graph_digest": graph_digest,
        }
        payload = {**body, "request_digest": sha256_digest(body)}
        raw_request = _canonical_bytes(payload)
        request = urllib.request.Request(
            f"{self.base_url}/v1/dependency-tool",
            data=raw_request,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        status, raw, _headers = self._request(request)
        response = json.loads(raw or b"{}")
        required_response_keys = {
            "dependencies",
            "graph_digest",
            "run_id",
            "schema_version",
            "target_id",
            "target_writes",
            "task_id",
        }
        response_keys = set(response) if isinstance(response, dict) else set()
        source_values = response.get("source_values") if isinstance(response, dict) else None
        response_valid = (
            isinstance(response, dict)
            and response_keys
            in (required_response_keys, required_response_keys | {"source_values"})
            and response.get("schema_version") == "orgrebase.controlled-dependency-result.v1"
            and response.get("run_id") == run_id
            and response.get("task_id") == task_id
            and response.get("target_id") == target_id
            and response.get("graph_digest") == graph_digest
            and response.get("target_writes") == 0
            and isinstance(response.get("dependencies"), list)
            and all(isinstance(item, str) for item in response.get("dependencies", []))
            and (
                "source_values" not in response
                or (
                    isinstance(source_values, dict)
                    and all(
                        isinstance(key, str) and isinstance(value, dict)
                        for key, value in source_values.items()
                    )
                )
            )
        )
        receipt = ControlledHTTPReceipt(
            id=f"http-tool:{run_id}:{task_id}",
            connector_kind="TOOL",
            operation="READ_DEPENDENCY_EVIDENCE",
            run_id=run_id,
            request_digest=_raw_sha256(raw_request),
            response_digest=_raw_sha256(raw),
            status="SUCCEEDED" if status == 200 else "FAILED",
            http_status=status,
            attempts=1,
            target_writes=0 if not response_valid else response["target_writes"],
        )
        if receipt.status != "SUCCEEDED" or not response_valid:
            raise IntegrityError(f"CONTROLLED_TOOL_HTTP_FAILED:{status}")
        return response, receipt


def _decode_otlp_attributes(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            continue
        encoded = item["value"]
        scalar = next((encoded[key] for key in ("stringValue", "intValue", "boolValue", "doubleValue") if key in encoded), None)
        if isinstance(item.get("key"), str) and scalar is not None:
            result[item["key"]] = scalar
    return result


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _extract_indexes(payload: dict[str, Any]) -> dict[str, set[str]]:
    indexes = {
        "run_id": set(),
        "trace_id": set(),
        "receipt_digest": set(),
        "skill_digest": set(),
        "task_id": set(),
        "evidence_class": set(),
        "layer": set(),
        "chain_sequence": set(),
        "status": set(),
        "target_write_count": set(),
        "attempt_fenced": set(),
        "error_code": set(),
    }
    attribute_map = {
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
    for node in _walk(payload):
        trace_id = node.get("traceId")
        if isinstance(trace_id, str) and trace_id:
            indexes["trace_id"].add(trace_id)
        attributes = _decode_otlp_attributes(node.get("attributes"))
        for source, target in attribute_map.items():
            value = attributes.get(source)
            if value is not None and str(value):
                indexes[target].add(str(value))
    return indexes


_FORBIDDEN_TELEMETRY_KEYS = {
    "access_token",
    "api_key",
    "api_token",
    "auth_token",
    "authorization",
    "authorization_header",
    "bearer_token",
    "client_secret",
    "cookie",
    "csrf_token",
    "customer_email",
    "id_token",
    "password",
    "prompt",
    "raw_prompt",
    "raw_response",
    "refresh_token",
    "output",
    "raw_output",
    "secret",
    "session_token",
    "source_content",
    "token",
    "xsrf_token",
}
_FORBIDDEN_TELEMETRY_SUFFIXES = _FORBIDDEN_TELEMETRY_KEYS - {"token"}
_OPERATIONAL_LOG_BODY = re.compile(r"[A-Z][A-Z0-9_]{0,63}:[A-Z][A-Z0-9_]{0,63}\Z")
_FORBIDDEN_TELEMETRY_CONTENT = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
)


def _normalize_telemetry_key(value: object) -> str:
    """Normalize common identifier styles without fuzzy substring matching."""

    text = str(value)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").casefold()


def _is_forbidden_telemetry_key(value: object) -> bool:
    normalized = _normalize_telemetry_key(value)
    return normalized in _FORBIDDEN_TELEMETRY_KEYS or any(
        normalized.endswith(f"_{forbidden}")
        for forbidden in _FORBIDDEN_TELEMETRY_SUFFIXES
    )


def _telemetry_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _telemetry_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _telemetry_strings(item)


def _assert_privacy_safe_telemetry(value: Any) -> None:
    """Accept structured operational facts only; never persist raw content or credentials."""

    # Inspect the complete structure before any value scan so a sensitive key
    # always produces the more specific, actionable rejection code.
    for node in _walk(value):
        attribute_name = node.get("key")
        if isinstance(attribute_name, str) and _is_forbidden_telemetry_key(attribute_name):
            raise IntegrityError(f"OTLP_RESTRICTED_ATTRIBUTE:{attribute_name}")
        for key in node:
            if _is_forbidden_telemetry_key(key):
                raise IntegrityError(f"OTLP_RESTRICTED_FIELD:{key}")

    for node in _walk(value):
        for key, item in node.items():
            strings = tuple(_telemetry_strings(item))
            for text in strings:
                if "ORGREBASE_CANARY_SECRET_" in text:
                    raise IntegrityError("OTLP_PRIVACY_CANARY_DETECTED")
                if any(pattern.search(text) for pattern in _FORBIDDEN_TELEMETRY_CONTENT):
                    raise IntegrityError("OTLP_RESTRICTED_CONTENT")
            if _normalize_telemetry_key(key) in {"body", "message"} and any(
                not _OPERATIONAL_LOG_BODY.fullmatch(text) for text in strings
            ):
                raise IntegrityError(
                    f"OTLP_UNSTRUCTURED_CONTENT_FORBIDDEN:{key}"
                )


class TelemetryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = prepare_private_sqlite_path(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS otlp_ingestions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              signal TEXT NOT NULL,
              payload_digest TEXT NOT NULL UNIQUE,
              ingested_at INTEGER NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS otlp_indexes (
              ingestion_id INTEGER NOT NULL REFERENCES otlp_ingestions(id) ON DELETE CASCADE,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              PRIMARY KEY (ingestion_id, key, value)
            );
            CREATE INDEX IF NOT EXISTS idx_otlp_indexes_lookup ON otlp_indexes(key, value);
            CREATE TABLE IF NOT EXISTS otlp_rejections (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              signal TEXT NOT NULL,
              run_id TEXT,
              payload_digest TEXT NOT NULL,
              reason_code TEXT NOT NULL,
              received_at INTEGER NOT NULL
            );
            """
        )
        self.connection.commit()
        self._lock = threading.RLock()

    def close(self) -> None:
        self.connection.close()

    def ingest(self, signal: str, payload: dict[str, Any], *, ingested_at: int | None = None) -> tuple[int, str]:
        if signal not in _SIGNAL_ROOT or set(payload) != {_SIGNAL_ROOT[signal]}:
            raise IntegrityError(f"OTLP_{signal.upper()}_ROOT_INVALID")
        _assert_privacy_safe_telemetry(payload)
        raw = _canonical_bytes(payload)
        digest = _raw_sha256(raw)
        indexes = _extract_indexes(payload)
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO otlp_ingestions(signal,payload_digest,ingested_at,payload_json) VALUES (?,?,?,?)",
                (signal, digest, int(ingested_at if ingested_at is not None else time.time()), raw.decode("utf-8")),
            )
            row = self.connection.execute(
                "SELECT id FROM otlp_ingestions WHERE payload_digest=?", (digest,)
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite postcondition
                raise RuntimeError("OTLP_INGESTION_MISSING")
            ingestion_id = int(row[0])
            for key, values in indexes.items():
                for value in sorted(values):
                    self.connection.execute(
                        "INSERT OR IGNORE INTO otlp_indexes(ingestion_id,key,value) VALUES (?,?,?)",
                        (ingestion_id, key, value),
                    )
        return ingestion_id, digest

    def record_rejection(
        self,
        *,
        signal: str,
        raw_payload: bytes,
        reason_code: str,
        run_id: str | None = None,
        received_at: int | None = None,
    ) -> int:
        """Persist only a digest and reason; rejected raw bytes are never retained."""

        with self._lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO otlp_rejections(signal,run_id,payload_digest,reason_code,received_at) "
                "VALUES (?,?,?,?,?)",
                (
                    signal,
                    run_id,
                    _raw_sha256(raw_payload),
                    reason_code,
                    int(received_at if received_at is not None else time.time()),
                ),
            )
        if cursor.lastrowid is None:  # pragma: no cover - SQLite postcondition
            raise RuntimeError("OTLP_REJECTION_RECORD_MISSING")
        return int(cursor.lastrowid)

    def query(
        self,
        *,
        start_epoch_seconds: int | None = None,
        end_epoch_seconds: int | None = None,
        **filters: str,
    ) -> tuple[list[dict[str, Any]], TelemetryQueryReceipt]:
        allowed = {
            "run_id",
            "trace_id",
            "receipt_digest",
            "skill_digest",
            "task_id",
            "evidence_class",
            "layer",
            "chain_sequence",
            "status",
            "target_write_count",
            "attempt_fenced",
            "error_code",
        }
        if not filters or not set(filters).issubset(allowed) or any(not value for value in filters.values()):
            raise ValueError("TELEMETRY_QUERY_FILTER_INVALID")
        if (
            start_epoch_seconds is not None
            and end_epoch_seconds is not None
            and start_epoch_seconds > end_epoch_seconds
        ):
            raise ValueError("TELEMETRY_QUERY_TIME_WINDOW_INVALID")
        clauses: list[str] = []
        params: list[str] = []
        for index, (key, value) in enumerate(sorted(filters.items())):
            alias = f"i{index}"
            clauses.append(
                f"EXISTS (SELECT 1 FROM otlp_indexes {alias} WHERE {alias}.ingestion_id=o.id AND {alias}.key=? AND {alias}.value=?)"
            )
            params.extend((key, value))
        if start_epoch_seconds is not None:
            clauses.append("o.ingested_at>=?")
            params.append(str(start_epoch_seconds))
        if end_epoch_seconds is not None:
            clauses.append("o.ingested_at<=?")
            params.append(str(end_epoch_seconds))
        rows = self.connection.execute(
            "SELECT o.id,o.signal,o.payload_digest,o.ingested_at,o.payload_json FROM otlp_ingestions o WHERE "
            + " AND ".join(clauses)
            + " ORDER BY o.id",
            params,
        ).fetchall()
        records = [
            {
                "id": int(row[0]),
                "signal": row[1],
                "payload_digest": row[2],
                "ingested_at": int(row[3]),
                "payload": json.loads(row[4]),
            }
            for row in rows
        ]
        projection = [
            {key: record[key] for key in ("id", "signal", "payload_digest", "ingested_at")}
            for record in records
        ]
        query: dict[str, str | int] = dict(sorted(filters.items()))
        if start_epoch_seconds is not None:
            query["start_epoch_seconds"] = start_epoch_seconds
        if end_epoch_seconds is not None:
            query["end_epoch_seconds"] = end_epoch_seconds
        query = dict(sorted(query.items()))
        receipt = TelemetryQueryReceipt(
            id=f"telemetry-query:{sha256_digest(query).split(':', 1)[1][:16]}",
            query=query,
            query_digest=sha256_digest(query),
            matched_ingestion_ids=tuple(record["id"] for record in records),
            matched_payload_digests=tuple(record["payload_digest"] for record in records),
            result_digest=sha256_digest(projection),
            count=len(records),
        )
        return records, receipt

    def prune(
        self,
        *,
        cutoff_epoch_seconds: int,
        rejected_cutoff_epoch_seconds: int | None = None,
    ) -> RetentionReceipt:
        rejected_cutoff = (
            cutoff_epoch_seconds
            if rejected_cutoff_epoch_seconds is None
            else rejected_cutoff_epoch_seconds
        )
        rows = self.connection.execute(
            "SELECT id FROM otlp_ingestions WHERE ingested_at < ? ORDER BY id",
            (cutoff_epoch_seconds,),
        ).fetchall()
        deleted = tuple(int(row[0]) for row in rows)
        rejected_rows = self.connection.execute(
            "SELECT id FROM otlp_rejections WHERE received_at < ? ORDER BY id",
            (rejected_cutoff,),
        ).fetchall()
        deleted_rejections = tuple(int(row[0]) for row in rejected_rows)
        with self.connection:
            self.connection.execute(
                "DELETE FROM otlp_ingestions WHERE ingested_at < ?", (cutoff_epoch_seconds,)
            )
            self.connection.execute(
                "DELETE FROM otlp_rejections WHERE received_at < ?", (rejected_cutoff,)
            )
        retained = int(self.connection.execute("SELECT COUNT(*) FROM otlp_ingestions").fetchone()[0])
        retained_rejections = int(
            self.connection.execute("SELECT COUNT(*) FROM otlp_rejections").fetchone()[0]
        )
        return RetentionReceipt(
            id=f"telemetry-retention:{cutoff_epoch_seconds}",
            cutoff_epoch_seconds=cutoff_epoch_seconds,
            rejected_cutoff_epoch_seconds=rejected_cutoff,
            deleted_ingestion_ids=deleted,
            deleted_rejection_ids=deleted_rejections,
            retained_count=retained,
            retained_rejection_count=retained_rejections,
        )


class _OtlpHTTPHandler(BaseHTTPRequestHandler):
    server: _OtlpHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:  # pragma: no cover - noise guard
        return

    def do_POST(self) -> None:
        signal = self.path.removeprefix("/v1/")
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self.send_response(401)
            self.end_headers()
            return
        if signal not in _SIGNAL_ROOT:
            self.send_response(404)
            self.end_headers()
            return
        raw = b""
        payload: Any = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            self.server.store.ingest(signal, payload)
        except (ValueError, json.JSONDecodeError, IntegrityError) as exc:
            run_ids = _extract_indexes(payload)["run_id"] if isinstance(payload, dict) else set()
            self.server.store.record_rejection(
                signal=signal,
                raw_payload=raw,
                reason_code=str(exc) or type(exc).__name__,
                run_id=next(iter(sorted(run_ids)), None),
            )
            self.send_response(400)
            self.end_headers()
            return
        raw = b'{"partialSuccess":{}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _OtlpHTTPServer(ThreadingHTTPServer):
    store: TelemetryStore
    token: str


class OtlpHTTPReceiver(AbstractContextManager["OtlpHTTPReceiver"]):
    def __init__(self, store: TelemetryStore, *, token: str = "controlled-otlp-token") -> None:
        self._server = _OtlpHTTPServer(("127.0.0.1", 0), _OtlpHTTPHandler)
        self._server.store = store
        self._server.token = token
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.token = token

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> OtlpHTTPReceiver:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class OtlpHTTPExporter:
    def __init__(self, base_url: str, *, token: str, timeout_seconds: float = 2.0, retries: int = 1) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def export(self, *, run_id: str, signal: str, payload: dict[str, Any]) -> ControlledHTTPReceipt:
        if signal not in _SIGNAL_ROOT:
            raise ValueError("OTLP_SIGNAL_UNSUPPORTED")
        raw = _canonical_bytes(payload)
        attempts = 0
        status = 0
        response_raw = b""
        for attempts in range(1, self.retries + 2):
            request = urllib.request.Request(
                f"{self.base_url}/v1/{signal}",
                data=raw,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    status = response.status
                    response_raw = response.read()
                break
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    response_raw = exc.read()
                except OSError:
                    response_raw = b""
                if status not in {408, 429} and status < 500:
                    break
                if attempts > self.retries:
                    break
            except (urllib.error.URLError, TimeoutError):
                if attempts > self.retries:
                    break
        receipt = ControlledHTTPReceipt(
            id=f"otlp-export:{run_id}:{signal}",
            connector_kind="OTLP",
            operation=f"EXPORT_{signal.upper()}",
            run_id=run_id,
            request_digest=_raw_sha256(raw),
            response_digest=_raw_sha256(response_raw),
            status="SUCCEEDED" if status == 200 else "FAILED",
            http_status=status,
            attempts=attempts,
            target_writes=0,
        )
        if receipt.status != "SUCCEEDED":
            raise IntegrityError(f"OTLP_HTTP_EXPORT_FAILED:{signal}:{status}")
        return receipt


def _otlp_attributes(values: Mapping[str, str | int | bool]) -> list[dict[str, Any]]:
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


def _ordered_chain_layers(layers: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Return a deterministic causal order for full chains and bounded probes."""

    extended_order = {
        "SOURCE": 10,
        "AGENTTEAMS": 20,
        "TOOL": 30,
        "SKILL": 40,
        "APPROVAL": 50,
        "APPLY": 60,
        "TERMINAL": 70,
    }
    unknown = sorted(set(layers) - set(extended_order))
    if unknown:
        raise IntegrityError(f"OTLP_CAUSAL_LAYER_UNKNOWN:{','.join(unknown)}")
    return tuple(sorted(layers.items(), key=lambda item: extended_order[item[0]]))


def _add_fact_binding(
    attributes: dict[str, str | int | bool],
    *,
    value_key: str,
    binding_key: str,
) -> None:
    """Describe an absent correlation fact honestly instead of inventing one."""

    attributes[binding_key] = "BOUND" if value_key in attributes else "NOT_BOUND"


def build_joint_otlp(
    *,
    run_id: str,
    organization_id: str,
    receipt_digest: str,
    task_id: str,
    skill_digest: str,
    layers: Mapping[str, str],
    correlation: Mapping[str, str | int | bool] | None = None,
    evidence_class: str = "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
) -> dict[str, dict[str, Any]]:
    """Build a privacy-safe, causally ordered controlled-local OTLP projection.

    The deterministic timestamps are projection coordinates, not observed
    runtime latency.  Missing upstream facts remain explicit ``NOT_BOUND`` or
    ``NOT_OBSERVED`` attributes.
    """

    trace_id = sha256(run_id.encode("utf-8")).hexdigest()[:32]
    base_nanos = 1_777_000_000_000_000_000
    resource = {
        "attributes": _otlp_attributes(
            {
                "service.name": "orgrebase",
                "service.version": "0.4.0",
                "deployment.environment.name": "controlled-local",
            }
        )
    }
    common_attributes: dict[str, str | int | bool] = {
        "orgrebase.organization.id": organization_id,
        "orgrebase.workflow.run_id": run_id,
        "orgrebase.receipt.digest": receipt_digest,
        "orgrebase.agentteams.task.id": task_id,
        "orgrebase.agentteams.task.binding": "BOUND",
        "orgrebase.skill.package.digest": skill_digest,
        "orgrebase.skill.package.binding": "BOUND",
        "orgrebase.evidence.class": evidence_class,
        "orgrebase.telemetry.timing.class": CONTROLLED_TIMING_CLASS,
        "orgrebase.stage.duration.status": "NOT_OBSERVED",
        "orgrebase.queue.duration.status": "NOT_OBSERVED",
    }
    common_attributes.update(dict(correlation or {}))
    for value_key, binding_key in (
        ("orgrebase.workflow.nonce", "orgrebase.workflow.nonce.binding"),
        (
            "orgrebase.agentteams.project.id",
            "orgrebase.agentteams.project.binding",
        ),
        (
            "orgrebase.agentteams.attempt.id",
            "orgrebase.agentteams.attempt.binding",
        ),
        (
            "orgrebase.agentteams.delegation.id",
            "orgrebase.agentteams.delegation.id.binding",
        ),
        (
            "orgrebase.agentteams.delegation.digest",
            "orgrebase.agentteams.delegation.digest.binding",
        ),
        (
            "orgrebase.agentteams.retry.count",
            "orgrebase.agentteams.retry.binding",
        ),
        (
            "orgrebase.agentteams.reassign.count",
            "orgrebase.agentteams.reassign.binding",
        ),
        ("orgrebase.skill.name", "orgrebase.skill.name.binding"),
        ("orgrebase.skill.version", "orgrebase.skill.version.binding"),
        (
            "orgrebase.skill.invocation.receipt.digest",
            "orgrebase.skill.invocation.binding",
        ),
        (
            "orgrebase.tool.receipt.digest",
            "orgrebase.tool.receipt.binding",
        ),
        (
            "orgrebase.tool.result.digest",
            "orgrebase.tool.result.binding",
        ),
        ("orgrebase.tool.retry.count", "orgrebase.tool.retry.binding"),
        (
            "orgrebase.coalition.result_binding.digest",
            "orgrebase.coalition.result_binding.binding",
        ),
    ):
        _add_fact_binding(
            common_attributes,
            value_key=value_key,
            binding_key=binding_key,
        )
    _assert_privacy_safe_telemetry(common_attributes)
    ordered_layers = _ordered_chain_layers(layers)
    spans: list[dict[str, Any]] = []
    previous_span_id: str | None = None
    span_bindings: list[tuple[str, str, str, int]] = []
    for index, (layer, status) in enumerate(ordered_layers, start=1):
        span_id = sha256(f"{run_id}:{index}:{layer}".encode()).hexdigest()[:16]
        attributes = {
            **common_attributes,
            "orgrebase.chain.layer": layer,
            "orgrebase.chain.sequence": index,
            "orgrebase.chain.status": status,
        }
        span = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": f"orgrebase.chain/{layer.lower()}",
            "kind": 1,
            "startTimeUnixNano": str(base_nanos + index * 1_000_000),
            "endTimeUnixNano": str(base_nanos + index * 1_000_000 + 500_000),
            "attributes": _otlp_attributes(attributes),
            "status": {
                "code": 1
                if status
                in {"PASS", "SUCCEEDED", "COMPLETED", "ACCEPT", "CANARY"}
                else 2
            },
        }
        if previous_span_id is not None:
            span["parentSpanId"] = previous_span_id
        spans.append(span)
        span_bindings.append((layer, status, span_id, index))
        previous_span_id = span_id
    traces = {"resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": {"name": "orgrebase.joint"}, "spans": spans}]}]}
    logs = {
        "resourceLogs": [
            {
                "resource": resource,
                "scopeLogs": [
                    {
                        "scope": {"name": "orgrebase.joint"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(base_nanos + index * 1_000_000),
                                "traceId": trace_id,
                                "spanId": span_id,
                                "severityNumber": 9,
                                "body": {"stringValue": f"{layer}:{status}"},
                                "attributes": _otlp_attributes(
                                    {
                                        **common_attributes,
                                        "orgrebase.chain.layer": layer,
                                        "orgrebase.chain.sequence": index,
                                        "orgrebase.chain.status": status,
                                    }
                                ),
                            }
                            for layer, status, span_id, index in span_bindings
                        ],
                    }
                ],
            }
        ]
    }
    metrics = {
        "resourceMetrics": [
            {
                "resource": resource,
                "scopeMetrics": [
                    {
                        "scope": {"name": "orgrebase.joint"},
                        "metrics": [
                            {
                                "name": "orgrebase.chain.layer.count",
                                "unit": "{layer}",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "attributes": _otlp_attributes(common_attributes),
                                            "timeUnixNano": str(base_nanos),
                                            "asInt": str(len(ordered_layers)),
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    return {"traces": traces, "logs": logs, "metrics": metrics}


def evaluate_run_alerts(
    store: TelemetryStore,
    *,
    run_id: str,
    required_layers: tuple[str, ...],
) -> AlertEvaluationReceipt:
    records, query_receipt = store.query(run_id=run_id)
    observed: set[str] = set()
    statuses: dict[str, set[str]] = {}
    for record in records:
        # Preserve the layer/status relation from each individual OTLP
        # observation.  Aggregating the two attributes independently lets a
        # successful status from one layer mask a failure in another (for
        # example SOURCE=COMPLETED used to make TERMINAL=FAILED look healthy).
        for node in _walk(record["payload"]):
            attributes = _decode_otlp_attributes(node.get("attributes"))
            layer = attributes.get("orgrebase.chain.layer")
            status = attributes.get("orgrebase.chain.status")
            if isinstance(layer, str) and layer:
                observed.add(layer)
                if status is not None and str(status):
                    statuses.setdefault(layer, set()).add(str(status))
    success_statuses = {
        "SOURCE": {"PASS", "SUCCEEDED", "COMPLETED"},
        "AGENTTEAMS": {"PASS", "ACCEPT", "SUCCEEDED", "COMPLETED"},
        "SKILL": {"PASS", "ACTIVE", "CANARY", "SUCCEEDED", "COMPLETED"},
        "TOOL": {"PASS", "SUCCEEDED", "COMPLETED"},
        "APPROVAL": {"PASS", "APPROVED"},
        "APPLY": {"PASS", "SUCCEEDED", "COMPLETED"},
        "TERMINAL": {"ACCEPT", "COMPLETED"},
    }
    alerts: list[AlertRecord] = []
    for layer in required_layers:
        if layer not in observed:
            alerts.append(
                AlertRecord(
                    id=f"alert:{run_id}:{layer}:missing",
                    run_id=run_id,
                    rule_id="same_run_layer_required",
                    severity="CRITICAL",
                    reason_code=f"MISSING_LAYER:{layer}",
                    evidence_refs=(query_receipt.digest,),
                )
            )
            continue
        allowed = success_statuses.get(layer)
        layer_statuses = statuses.get(layer, set())
        if allowed is not None and layer_statuses and layer_statuses <= allowed:
            continue
        mixed_statuses = bool(
            allowed is not None
            and layer_statuses & allowed
            and layer_statuses - allowed
        )
        if mixed_statuses:
            reason_code = f"MIXED_LAYER_STATUS:{layer}"
            rule_id = "same_run_layer_status_conflict"
        elif layer == "TERMINAL":
            reason_code = "TERMINAL_STATE_MISSING"
            rule_id = "terminal_state_required"
        elif layer == "TOOL" and layer_statuses & {"ERROR", "FAILED"}:
            reason_code = "TOOL_FAILURE"
            rule_id = "tool_success_required"
        elif layer == "SKILL" and "QUARANTINED" in layer_statuses:
            reason_code = "SKILL_QUARANTINED"
            rule_id = "skill_release_required"
        else:
            reason_code = f"UNHEALTHY_LAYER_STATUS:{layer}"
            rule_id = "same_run_layer_success_required"
        alerts.append(
            AlertRecord(
                id=f"alert:{run_id}:{layer}:unhealthy",
                run_id=run_id,
                rule_id=rule_id,
                severity="CRITICAL",
                reason_code=reason_code,
                evidence_refs=(query_receipt.digest,),
            )
        )
    rejected_rows = store.connection.execute(
        "SELECT id FROM otlp_rejections WHERE run_id=? ORDER BY id", (run_id,)
    ).fetchall()
    if rejected_rows:
        alerts.append(
            AlertRecord(
                id=f"alert:{run_id}:receiver-ingestion-failure",
                run_id=run_id,
                rule_id="telemetry_ingestion_required",
                severity="WARNING",
                reason_code="RECEIVER_INGESTION_FAILURE",
                evidence_refs=(query_receipt.digest,),
            )
        )
    apply_succeeded = bool(statuses.get("APPLY", set()) & {"PASS", "SUCCEEDED", "COMPLETED"})
    approval_succeeded = bool(statuses.get("APPROVAL", set()) & {"PASS", "APPROVED"})
    if apply_succeeded and not approval_succeeded:
        alerts.append(
            AlertRecord(
                id=f"alert:{run_id}:approval-apply-mismatch",
                run_id=run_id,
                rule_id="apply_requires_approval",
                severity="CRITICAL",
                reason_code="APPROVAL_APPLY_MISMATCH",
                evidence_refs=(query_receipt.digest,),
            )
        )
    indexed_values: dict[str, set[str]] = {}
    for record in records:
        for key, values in _extract_indexes(record["payload"]).items():
            indexed_values.setdefault(key, set()).update(values)
    if any(value != "0" for value in indexed_values.get("target_write_count", set())):
        alerts.append(
            AlertRecord(
                id=f"alert:{run_id}:candidate-write",
                run_id=run_id,
                rule_id="candidate_must_not_write",
                severity="CRITICAL",
                reason_code="NON_ZERO_CANDIDATE_WRITES",
                evidence_refs=(query_receipt.digest,),
            )
        )
    if any(
        value.lower() == "true" for value in indexed_values.get("attempt_fenced", set())
    ):
        alerts.append(
            AlertRecord(
                id=f"alert:{run_id}:late-fenced-result",
                run_id=run_id,
                rule_id="fenced_result_rejected",
                severity="WARNING",
                reason_code="LATE_FENCED_RESULT",
                evidence_refs=(query_receipt.digest,),
            )
        )
    input_view = {
        "query_receipt": query_receipt.digest,
        "observed_layers": sorted(observed),
        "statuses": {key: sorted(value) for key, value in sorted(statuses.items())},
    }
    return AlertEvaluationReceipt(
        id=f"alert-evaluation:{run_id}",
        run_id=run_id,
        required_layers=required_layers,
        observed_layers=tuple(sorted(observed)),
        alerts=tuple(alerts),
        input_digest=sha256_digest(input_view),
        status="PASS" if not alerts else "ALERT",
    )


def _sqlite_logical_digest(path: Path) -> tuple[str, str, int, int]:
    connection = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        projection: dict[str, list[list[Any]]] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            projection[table] = [list(row) for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid")]
        event_row = connection.execute(
            "SELECT event_digest FROM domain_events ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone()
        event_head = str(event_row[0]) if event_row else "GENESIS"
        return (
            sha256_digest(projection),
            event_head,
            len(projection.get("object_versions", [])),
            len(projection.get("artifacts", [])),
        )
    finally:
        connection.close()


def run_sqlite_backup_restore_drill(
    source: str | Path,
    *,
    backup: str | Path,
    restored: str | Path,
) -> BackupRestoreReceipt:
    source_path = Path(source)
    backup_path = Path(backup)
    restored_path = Path(restored)
    started = time.perf_counter_ns()
    source_digest, source_head, object_count, artifact_count = _sqlite_logical_digest(source_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    restored_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists() or restored_path.exists():
        raise FileExistsError("BACKUP_RESTORE_TARGET_ALREADY_EXISTS")
    private_backup_path = prepare_private_sqlite_path(backup_path)
    source_connection = sqlite3.connect(source_path)
    backup_connection = sqlite3.connect(private_backup_path)
    try:
        source_connection.backup(backup_connection)
    finally:
        source_connection.close()
        backup_connection.close()
    private_restored_path = prepare_private_sqlite_path(restored_path)
    shutil.copyfile(private_backup_path, private_restored_path)
    prepare_private_sqlite_path(private_restored_path)
    backup_digest, _backup_head, _backup_objects, _backup_artifacts = _sqlite_logical_digest(backup_path)
    restored_digest, restored_head, restored_objects, restored_artifacts = _sqlite_logical_digest(restored_path)
    duration_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
    status = (
        "PASS"
        if source_digest == backup_digest == restored_digest
        and source_head == restored_head
        and object_count == restored_objects
        and artifact_count == restored_artifacts
        else "FAIL"
    )
    return BackupRestoreReceipt(
        id="backup-restore:controlled-local@v1",
        source_digest=source_digest,
        backup_digest=backup_digest,
        restored_digest=restored_digest,
        source_event_head=source_head,
        restored_event_head=restored_head,
        object_count=object_count,
        artifact_count=artifact_count,
        duration_ms=duration_ms,
        status=status,
    )


def run_capacity_smoke(
    operation: str,
    calls: Iterable[Callable[[], object]],
) -> CapacitySmokeReceipt:
    started = time.perf_counter_ns()
    successes = 0
    failures = 0
    latencies_ms: list[float] = []
    for call in calls:
        call_started = time.perf_counter_ns()
        try:
            call()
            successes += 1
        except Exception:  # pragma: no cover - failure count is the contract
            failures += 1
        finally:
            latencies_ms.append((time.perf_counter_ns() - call_started) / 1_000_000)
    elapsed_ms = max(1, (time.perf_counter_ns() - started) // 1_000_000)
    count = successes + failures
    if count == 0:
        raise ValueError("CAPACITY_SMOKE_REQUIRES_CALLS")
    ordered = sorted(latencies_ms)

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
        return round(ordered[index], 4)

    return CapacitySmokeReceipt(
        id=f"capacity-smoke:{operation}",
        operation=operation,
        sample_count=count,
        successes=successes,
        failures=failures,
        elapsed_ms=elapsed_ms,
        latency_p50_ms=percentile(0.50),
        latency_p95_ms=percentile(0.95),
        observed_requests_per_second=round(count / (elapsed_ms / 1000), 4),
        environment_facts={
            "cpu_count": int(os.cpu_count() or 1),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "system": platform.system(),
            "system_release": platform.release(),
        },
    )
