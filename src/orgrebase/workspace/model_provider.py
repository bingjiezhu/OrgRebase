"""Structured model-provider adapters with bounded schema repair and explicit NOT_RUN."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass
from orgrebase.workspace.models import ModelRequest, ModelResponseReceipt

T = TypeVar("T", bound=BaseModel)


def _receipt(
    request: ModelRequest,
    *,
    status: str,
    value: Any | None,
    provider_request_id: str | None,
    schema_valid: bool,
    error_code: str | None,
    evidence_class: str,
    latency_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    finish_reason: str | None = None,
) -> ModelResponseReceipt:
    normalized = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return ModelResponseReceipt(
        id=f"model-response:{request.request_id}:attempt-{request.attempt}",
        request_ref=request.request_id,
        request_digest=request.digest,
        status=status,
        value=normalized,
        output_digest=sha256_digest(normalized) if normalized is not None else None,
        provider_request_id=provider_request_id,
        provider=request.provider,
        model_id=request.model_id,
        model_version=request.model_version,
        schema_valid=schema_valid,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        seed_supported=request.seed is not None,
        error_code=error_code,
        completed_at="2026-08-16T00:00:00Z",
        evidence_class=evidence_class,
    )


class DeterministicModelProvider:
    """Reference provider for CI; resolver receives only the registered request."""

    def __init__(self, resolver: Callable[[ModelRequest], Any]) -> None:
        self.resolver = resolver

    def generate_structured(
        self, *, request: ModelRequest, output_model: type[T]
    ) -> ModelResponseReceipt:
        started = time.perf_counter()
        try:
            value = output_model.model_validate(self.resolver(request))
        except (ValidationError, ValueError, TypeError) as exc:
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=f"SCHEMA_VALIDATION_FAILED:{type(exc).__name__}",
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return _receipt(
            request,
            status="VALID",
            value=value,
            provider_request_id=f"deterministic:{request.digest[7:23]}",
            schema_valid=True,
            error_code=None,
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason="stop",
        )


class RecordedModelProvider:
    """Replay provider bound to exact request digests."""

    def __init__(self, records: Mapping[str, Any]) -> None:
        self.records = dict(records)

    def generate_structured(
        self, *, request: ModelRequest, output_model: type[T]
    ) -> ModelResponseReceipt:
        if request.digest not in self.records:
            return _receipt(
                request,
                status="ABSTAIN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="RECORDED_RESPONSE_NOT_FOUND",
                evidence_class=EvidenceClass.NOT_RUN,
            )
        try:
            value = output_model.model_validate(self.records[request.digest])
        except ValidationError:
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="RECORDED_RESPONSE_SCHEMA_MISMATCH",
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )
        return _receipt(
            request,
            status="VALID",
            value=value,
            provider_request_id=f"recorded:{request.digest[7:23]}",
            schema_valid=True,
            error_code=None,
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            finish_reason="replay",
        )


class LiveHTTPModelProvider:
    """Minimal OpenAI-compatible JSON endpoint adapter.

    No claim of live execution is made unless credentials and endpoint are supplied
    and a provider request ID is returned.
    """

    def __init__(
        self,
        *,
        endpoint_env: str = "ORGREBASE_MODEL_ENDPOINT",
        key_env: str = "ORGREBASE_MODEL_API_KEY",
    ) -> None:
        self.endpoint_env = endpoint_env
        self.key_env = key_env

    def generate_structured(
        self, *, request: ModelRequest, output_model: type[T]
    ) -> ModelResponseReceipt:
        endpoint = os.environ.get(self.endpoint_env)
        key = os.environ.get(self.key_env)
        if not endpoint or not key:
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="LIVE_MODEL_CREDENTIALS_MISSING",
                evidence_class=EvidenceClass.NOT_RUN,
            )
        body = {
            "model": request.model_id,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "seed": request.seed,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": output_model.model_json_schema(mode="validation"),
                    "strict": True,
                },
            },
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_ref": request.task_ref,
                            "purpose": request.purpose,
                            "context_refs": request.context_refs,
                            "input_refs": request.input_refs,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        }
        data = json.dumps(body).encode("utf-8")
        http_request = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(http_request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                provider_request_id = response.headers.get("x-request-id") or payload.get("id")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return _receipt(
                request,
                status="PROVIDER_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=f"LIVE_MODEL_PROVIDER_ERROR:{type(exc).__name__}",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        try:
            raw_content = payload["choices"][0]["message"]["content"]
            decoded = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
            value = output_model.model_validate(decoded)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError):
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=str(provider_request_id) if provider_request_id else None,
                schema_valid=False,
                error_code="LIVE_MODEL_SCHEMA_MISMATCH",
                evidence_class="LIVE_MODEL" if provider_request_id else EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        return _receipt(
            request,
            status="VALID",
            value=value,
            provider_request_id=str(provider_request_id) if provider_request_id else None,
            schema_valid=True,
            error_code=None,
            evidence_class="LIVE_MODEL" if provider_request_id else EvidenceClass.NOT_RUN,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            finish_reason=str(payload.get("choices", [{}])[0].get("finish_reason", "stop")),
        )


class BoundedSchemaRepairProvider:
    """At most initial attempt plus two schema-repair attempts, then ABSTAIN."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def generate_structured(
        self, *, request: ModelRequest, output_model: type[T]
    ) -> ModelResponseReceipt:
        current = request
        last: ModelResponseReceipt | None = None
        for attempt in range(3):
            current_payload = current.model_dump(mode="json", exclude={"digest"})
            current_payload["attempt"] = attempt
            current = ModelRequest.model_validate(current_payload)
            last = self.provider.generate_structured(request=current, output_model=output_model)
            if last.status in {"VALID", "NOT_RUN", "PROVIDER_ERROR"}:
                return last
        assert last is not None
        return _receipt(
            current,
            status="ABSTAIN",
            value=None,
            provider_request_id=last.provider_request_id,
            schema_valid=False,
            error_code="SCHEMA_REPAIR_EXHAUSTED",
            evidence_class=last.evidence_class,
            latency_ms=last.latency_ms,
        )
