"""Structured model-provider adapters with bounded schema repair and explicit NOT_RUN."""

from __future__ import annotations

import json
import math
import os
import random
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass
from orgrebase.workspace.model_observations import (
    ModelAttempt,
    ModelAttemptObserver,
    legacy_token_count,
    observed_usage,
)
from orgrebase.workspace.models import ModelRequest, ModelResponseReceipt
from orgrebase.workspace.openai_responses import OpenAIResponsesProvider as OpenAIResponsesProvider

T = TypeVar("T", bound=BaseModel)

DEFAULT_VERTEX_MODEL_ID = "gemini-3.7-flash"
SUPPORTED_VERTEX_MODEL_IDS = frozenset(
    {DEFAULT_VERTEX_MODEL_ID, "gemini-3.8-flash"}
)


def configured_vertex_model_id() -> str:
    """Resolve the explicitly bounded Vertex model used by this process."""

    selected = os.environ.get(
        "ORGREBASE_VERTEX_MODEL_ID", DEFAULT_VERTEX_MODEL_ID
    ).strip()
    if selected not in SUPPORTED_VERTEX_MODEL_IDS:
        supported = ", ".join(sorted(SUPPORTED_VERTEX_MODEL_IDS))
        raise ValueError(
            f"ORGREBASE_VERTEX_MODEL_ID must be one of: {supported}"
        )
    return selected


VERTEX_MODEL_ID = configured_vertex_model_id()
VERTEX_LOCATION = "global"
VERTEX_EVIDENCE_CLASS = "LIVE_VERTEX_MODEL"
_VERTEX_CLAIM_BOUNDARY = (
    "LIVE_VERTEX_STRUCTURED_ADVISORY_NOT_DETERMINISTIC_AUTHORITY_"
    "NOT_CANONICAL_WRITE"
)
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")


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
    completed_at: str = "2026-08-16T00:00:00Z",
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
        completed_at=completed_at,
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
        observer: ModelAttemptObserver | None = None,
    ) -> None:
        self.endpoint_env = endpoint_env
        self.key_env = key_env
        self.observer = observer or ModelAttemptObserver()

    def generate_structured(self, *, request: ModelRequest, output_model: type[T]) -> ModelResponseReceipt:
        with self.observer.observe(request) as observation:
            receipt = self._generate_structured(
                request=request, output_model=output_model, observation=observation
            )
            observation.receipt = receipt
            return receipt

    def _generate_structured(
        self,
        *,
        request: ModelRequest,
        output_model: type[T],
        observation: ModelAttempt,
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
        if not observation.before_send(data):
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=observation.error_code,
                evidence_class=EvidenceClass.NOT_RUN,
            )
        try:
            with urllib.request.urlopen(http_request, timeout=30) as response:
                raw_response = response.read()
                observation.response(raw_response)
                payload = json.loads(raw_response.decode("utf-8"))
                provider_request_id = response.headers.get("x-request-id") or (
                    payload.get("id") if isinstance(payload, dict) else None
                )
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
        usage = observed_usage(
            payload.get("usage") if isinstance(payload, dict) else None,
            {
                "input_tokens": "prompt_tokens",
                "output_tokens": "completion_tokens",
                "total_tokens": "total_tokens",
            },
        )
        observation.metadata(
            usage=usage,
            observed_model=payload.get("model") if isinstance(payload, dict) else None,
            provider_request_id=provider_request_id,
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
        return _receipt(
            request,
            status="VALID",
            value=value,
            provider_request_id=str(provider_request_id) if provider_request_id else None,
            schema_valid=True,
            error_code=None,
            evidence_class="LIVE_MODEL" if provider_request_id else EvidenceClass.NOT_RUN,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=legacy_token_count(usage.input_tokens),
            output_tokens=legacy_token_count(usage.output_tokens),
            finish_reason=str(payload.get("choices", [{}])[0].get("finish_reason", "stop")),
        )


class LocalOllamaStructuredProvider:
    """Loopback-only Ollama adapter for an observed local structured-model call.

    The provider never falls back to a deterministic answer.  An unavailable
    endpoint, a missing exact model, malformed JSON, or a schema mismatch is
    returned as ``NOT_RUN``/``SCHEMA_ERROR`` so callers can fail closed.
    ``prompt_payload`` is caller-owned evidence and is sent only to the local
    endpoint; the immutable ``ModelRequest`` still binds the public request
    metadata and expected schema.
    """

    def __init__(
        self,
        *,
        prompt_payload: Mapping[str, Any],
        endpoint: str | None = None,
        model_id: str = "qwen2.5:3b",
        expected_model_digest: str = ("357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"),
        timeout_seconds: float = 30.0,
        observer: ModelAttemptObserver | None = None,
    ) -> None:
        self.prompt_payload = json.loads(
            json.dumps(dict(prompt_payload), ensure_ascii=False, allow_nan=False)
        )
        self.endpoint = (
            endpoint
            or os.environ.get("ORGREBASE_OLLAMA_ENDPOINT")
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        self.model_id = model_id
        self.expected_model_digest = expected_model_digest
        self.timeout_seconds = timeout_seconds
        self.observer = observer or ModelAttemptObserver()
        self.runtime_binding: dict[str, Any] = {
            "status": "NOT_RUN",
            "claim_boundary": "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER",
            "request_id_source": "CLIENT_DERIVED_REQUEST_DIGEST",
            "model_id": self.model_id,
            "expected_model_digest": self.expected_model_digest,
        }
        parsed = urllib.parse.urlparse(self.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("OLLAMA_ENDPOINT_MUST_BE_LOOPBACK_HTTP")

    def generate_structured(self, *, request: ModelRequest, output_model: type[T]) -> ModelResponseReceipt:
        with self.observer.observe(request) as observation:
            receipt = self._generate_structured(
                request=request, output_model=output_model, observation=observation
            )
            observation.receipt = receipt
            return receipt

    def _generate_structured(
        self,
        *,
        request: ModelRequest,
        output_model: type[T],
        observation: ModelAttempt,
    ) -> ModelResponseReceipt:
        if (
            request.provider != "ollama-local"
            or request.model_id != self.model_id
            or request.model_version != f"ollama-manifest:{self.expected_model_digest}"
        ):
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="OLLAMA_REQUEST_BINDING_MISMATCH",
                evidence_class=EvidenceClass.NOT_RUN,
            )
        tags_request = urllib.request.Request(f"{self.endpoint}/api/tags")
        tags_started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                tags_request, timeout=min(self.timeout_seconds, 5.0)
            ) as response:
                tags_payload = json.loads(response.read().decode("utf-8"))
            models = tags_payload.get("models") if isinstance(tags_payload, dict) else None
            observed = next(
                (
                    item
                    for item in models or []
                    if isinstance(item, dict) and item.get("name") == self.model_id
                ),
                None,
            )
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            self.runtime_binding["error_code"] = f"OLLAMA_TAGS_UNAVAILABLE:{type(exc).__name__}"
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=str(self.runtime_binding["error_code"]),
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - tags_started) * 1000),
                completed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        observed_digest = observed.get("digest") if isinstance(observed, dict) else None
        self.runtime_binding.update(
            {
                "observed_model_digest": observed_digest,
                "tags_response_digest": sha256_digest(tags_payload),
                "status": (
                    "BOUND"
                    if observed_digest == self.expected_model_digest
                    else "NOT_RUN"
                ),
            }
        )
        if observed_digest != self.expected_model_digest:
            self.runtime_binding["error_code"] = "OLLAMA_EXACT_MODEL_DIGEST_NOT_AVAILABLE"
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="OLLAMA_EXACT_MODEL_DIGEST_NOT_AVAILABLE",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - tags_started) * 1000),
                completed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        body = {
            "model": self.model_id,
            "stream": False,
            "format": output_model.model_json_schema(mode="validation"),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a candidate-only enterprise workflow reviewer. "
                        "Return only JSON matching the supplied schema. A worker "
                        "ABSTAIN or missing required field requires REPLAN; otherwise PASS."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        self.prompt_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "options": {
                "temperature": request.temperature,
                "seed": request.seed,
                "num_predict": request.max_output_tokens,
            },
        }
        raw_request = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.runtime_binding["client_request_id"] = "ollama-client:" + sha256_digest(
            body
        ).removeprefix("sha256:")[:24]
        http_request = urllib.request.Request(
            f"{self.endpoint}/api/chat",
            data=raw_request,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        if not observation.before_send(raw_request):
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=observation.error_code,
                evidence_class=EvidenceClass.NOT_RUN,
            )
        try:
            with urllib.request.urlopen(
                http_request, timeout=self.timeout_seconds
            ) as response:
                raw_response = response.read()
                observation.response(raw_response)
            payload = json.loads(raw_response.decode("utf-8"))
            self.runtime_binding["response_observation_digest"] = sha256_digest(
                payload
            )
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=f"OLLAMA_UNAVAILABLE_OR_MODEL_MISSING:{type(exc).__name__}",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
                completed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        usage = observed_usage(payload, {"input_tokens": "prompt_eval_count", "output_tokens": "eval_count"})
        observation.metadata(
            usage=usage, observed_model=payload.get("model") if isinstance(payload, dict) else None
        )
        try:
            if not isinstance(payload, dict) or payload.get("model") != self.model_id:
                raise ValueError("OLLAMA_MODEL_RESPONSE_MISMATCH")
            content = payload["message"]["content"]
            decoded = json.loads(content) if isinstance(content, str) else content
            value = output_model.model_validate(decoded)
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=f"OLLAMA_SCHEMA_MISMATCH:{type(exc).__name__}",
                evidence_class="LOCAL_OLLAMA_MODEL",
                latency_ms=int((time.perf_counter() - started) * 1000),
                input_tokens=legacy_token_count(usage.input_tokens),
                output_tokens=legacy_token_count(usage.output_tokens),
                finish_reason=str(payload.get("done_reason") or "unknown")
                if isinstance(payload, dict)
                else "unknown",
                completed_at=str(
                    (payload.get("created_at") if isinstance(payload, dict) else None)
                    or datetime.now(UTC).isoformat().replace("+00:00", "Z")
                ),
            )
        return _receipt(
            request,
            status="VALID",
            value=value,
            provider_request_id=None,
            schema_valid=True,
            error_code=None,
            evidence_class="LOCAL_OLLAMA_MODEL",
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=legacy_token_count(usage.input_tokens),
            output_tokens=legacy_token_count(usage.output_tokens),
            finish_reason=str(payload.get("done_reason") or "stop"),
            completed_at=str(
                payload.get("created_at") or datetime.now(UTC).isoformat().replace("+00:00", "Z")
            ),
        )


def _gcloud_value(*arguments: str, timeout: float = 20) -> str | None:
    """Resolve non-persisted local gcloud state without echoing its output.

    The access token returned by this helper lives only in process memory.  No
    command error text, stdout, credential path, or token is copied into a
    runtime binding or model receipt.
    """

    try:
        result = subprocess.run(
            ["gcloud", *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or not value or value == "(unset)":
        return None
    return value


def _read_vertex_response(
    response: Any, deadline: float | None, *, max_bytes: int = 2 * 1024 * 1024
) -> bytes:
    """Bound HTTPS body reads by the same operation deadline and a byte ceiling."""
    if not callable(getattr(response, "isclosed", None)):
        # In-memory protocol fixtures; real HTTPS responses use the bounded loop.
        value = response.read()
        if len(value) > max_bytes:
            raise OSError("VERTEX_RESPONSE_TOO_LARGE")
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("VERTEX_RESPONSE_DEADLINE")
        return value
    chunks = bytearray()
    while not response.isclosed():
        remaining = 60.0 if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("VERTEX_RESPONSE_DEADLINE")
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is None:
            raise OSError("VERTEX_RESPONSE_DEADLINE_UNAVAILABLE")
        sock.settimeout(remaining)
        chunk = response.read1(65536)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise OSError("VERTEX_RESPONSE_TOO_LARGE")
    if getattr(response, "length", None) not in (None, 0):
        raise OSError("VERTEX_RESPONSE_INCOMPLETE")
    return bytes(chunks)


class VertexAIStructuredProvider:
    """Minimal native Vertex ``generateContent`` structured-output adapter.

    Credentials are accepted only as in-memory arguments, dedicated process
    environment values, or a short-lived ADC token resolved through ``gcloud``.
    The request URL, runtime binding, receipt, and error path deliberately never
    expose headers, keys, tokens, local credential paths, or provider error
    bodies.  A live response is admissible only when Vertex returns both a
    response ID and an observed model version and Pydantic accepts the JSON.
    """

    def __init__(
        self,
        *,
        prompt_payload: Mapping[str, Any],
        project_id: str | None = None,
        model_id: str = VERTEX_MODEL_ID,
        access_token: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        adc_token_resolver: Callable[[], str | None] | None = None,
        project_resolver: Callable[[], str | None] | None = None,
        observer: ModelAttemptObserver | None = None,
    ) -> None:
        self.prompt_payload = json.loads(
            json.dumps(dict(prompt_payload), ensure_ascii=False, allow_nan=False)
        )
        self.project_id = project_id
        self.model_id = model_id
        self._access_token = access_token
        self._api_key = api_key
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ValueError("VERTEX_TIMEOUT_INVALID")
        self.timeout_seconds = timeout_seconds
        self.observer = observer or ModelAttemptObserver()
        self._default_adc_resolver = adc_token_resolver is None
        self._default_project_resolver = project_resolver is None
        self._adc_token_resolver = adc_token_resolver or (
            lambda: _gcloud_value(
                "auth", "application-default", "print-access-token"
            )
        )
        self._project_resolver = project_resolver or (
            lambda: _gcloud_value("config", "get-value", "project")
        )
        self.runtime_binding: dict[str, Any] = {
            "status": "NOT_RUN",
            "provider": "vertex-ai",
            "provider_evidence_class": VERTEX_EVIDENCE_CLASS,
            "model_id": self.model_id,
            "location": VERTEX_LOCATION,
            "endpoint_origin": "https://aiplatform.googleapis.com",
            "endpoint_path_template": (
                "/v1/projects/{project}/locations/global/publishers/google/"
                "models/{model}:generateContent"
            ),
            "claim_boundary": _VERTEX_CLAIM_BOUNDARY,
            "credentials_disclosed": False,
            "request_or_response_content_disclosed": False,
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _resolve_project(self, *, deadline: float | None = None) -> str | None:
        value = (
            self.project_id
            or os.environ.get("ORGREBASE_VERTEX_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
            or (_gcloud_value("config", "get-value", "project", timeout=max(0.001, min(20, deadline - time.monotonic())))
                if deadline is not None and self._default_project_resolver else self._project_resolver())
        )
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _resolve_credential(self, *, deadline: float | None = None) -> tuple[str, str] | None:
        explicit_token = self._access_token
        if explicit_token:
            return "EXPLICIT_ACCESS_TOKEN", explicit_token
        explicit_key = self._api_key
        if explicit_key:
            return "EXPLICIT_API_KEY", explicit_key
        environment_token = os.environ.get("ORGREBASE_VERTEX_ACCESS_TOKEN")
        if environment_token:
            return "ENV_ACCESS_TOKEN", environment_token
        environment_key = os.environ.get("ORGREBASE_VERTEX_API_KEY")
        if environment_key:
            return "ENV_API_KEY", environment_key
        if deadline is not None and time.monotonic() > deadline:
            return None
        adc_token = (_gcloud_value("auth", "application-default", "print-access-token",
                                   timeout=max(0.001, min(20, deadline - time.monotonic())))
                     if deadline is not None and self._default_adc_resolver else self._adc_token_resolver())
        if adc_token:
            return "ADC_GCLOUD_ACCESS_TOKEN", adc_token
        return None

    def candidate_environment_values(self) -> dict[str, str]:
        """Resolve credentials in the parent; never copy an ADC home into a worker."""
        if self.model_id not in SUPPORTED_VERTEX_MODEL_IDS:
            raise ValueError("VERTEX_PARENT_MODEL_INVALID")
        project = self._resolve_project()
        credential = self._resolve_credential()
        if project is None or _PROJECT_ID.fullmatch(project) is None or credential is None:
            raise ValueError("VERTEX_PARENT_CREDENTIALS_UNAVAILABLE")
        source, value = credential
        key = "ORGREBASE_VERTEX_API_KEY" if source.endswith("API_KEY") else "ORGREBASE_VERTEX_ACCESS_TOKEN"
        return {"ORGREBASE_VERTEX_PROJECT_ID": project, "ORGREBASE_VERTEX_MODEL_ID": self.model_id, key: value}

    def generate_structured(self, *, request: ModelRequest, output_model: type[T]) -> ModelResponseReceipt:
        for name in ("error_code", "response_observation_digest", "observed_model_version", "provider_request_id_present"):
            self.runtime_binding.pop(name, None)
        self.runtime_binding["status"] = "NOT_RUN"
        operation_started = time.monotonic()
        deadline = operation_started + self.timeout_seconds
        transport_context: dict[str, Any] = {}
        retry = {"max_attempts": 3, "deadline_seconds": self.timeout_seconds,
                 "attempts": [], "provider_attempts": 0, "successful_calls": 0,
                 "receipt_latency_scope": "FINAL_ATTEMPT_ONLY"}
        self.runtime_binding["transport_retry"] = retry
        for index in range(3):
            self.runtime_binding.pop("retry_after_seconds", None)
            self.runtime_binding.pop("http_status", None)
            self.runtime_binding.pop("error_code", None)
            with self.observer.observe(request) as observation:
                receipt = self._generate_structured(
                    request=request, output_model=output_model, observation=observation, deadline=deadline, transport_context=transport_context,
                )
                observation.receipt = receipt
            sent = observation.dispatch_state != "NOT_DISPATCHED"
            retry["provider_attempts"] += int(sent)
            row = {"dispatch_id": observation.dispatch_id, "response_receipt_digest": receipt.digest,
                   "status": receipt.status, "error_code": receipt.error_code,
                   "http_status": self.runtime_binding.get("http_status"), "sent": sent}
            retry["attempts"].append(row)
            if self.observer.directory is not None and self.observer.summary()["records"][-1]["persistence"] != "DURABLE":
                retry["stop_reason"] = "OBSERVATION_DURABILITY_LOST"
                retry["elapsed_ms"] = int((time.monotonic() - operation_started) * 1000)
                if observation.write_failed:
                    # A failed intent already refused dispatch; preserve that phase.
                    self.runtime_binding.update(status="NOT_RUN", error_code=receipt.error_code)
                    return receipt
                self.runtime_binding.update(
                    status="PROVIDER_ERROR", error_code="MODEL_OBSERVATION_RESULT_WRITE_FAILED"
                )
                return _receipt(request, status="PROVIDER_ERROR", value=None, provider_request_id=receipt.provider_request_id,
                                schema_valid=False, error_code="MODEL_OBSERVATION_RESULT_WRITE_FAILED",
                                evidence_class=EvidenceClass.NOT_RUN, completed_at=self._now())
            if receipt.status == "VALID":
                retry["successful_calls"] = 1
            if receipt.error_code != "VERTEX_HTTP_ERROR:429" or index == 2:
                retry["stop_reason"] = "MAX_ATTEMPTS" if receipt.error_code == "VERTEX_HTTP_ERROR:429" else "TERMINAL_RESPONSE"
                retry["elapsed_ms"] = int((time.monotonic() - operation_started) * 1000)
                return receipt
            requested_wait = self.runtime_binding.get("retry_after_seconds")
            wait = requested_wait if requested_wait is not None else random.uniform(0.5, 1.0) * (2 ** (index + 1))
            row["wait_reason"] = "RETRY_AFTER" if requested_wait is not None else "HTTP_429_BACKOFF"
            row["planned_wait_seconds"] = wait
            if wait >= deadline - time.monotonic():
                retry["stop_reason"] = "TOTAL_DEADLINE_EXHAUSTED"
                retry["elapsed_ms"] = int((time.monotonic() - operation_started) * 1000)
                return receipt
            sleep_started = time.monotonic()
            time.sleep(wait)
            row["backoff_elapsed_ms"] = int((time.monotonic() - sleep_started) * 1000)
        raise AssertionError("unreachable bounded retry")

    def _generate_structured(
        self,
        *,
        request: ModelRequest,
        output_model: type[T],
        observation: ModelAttempt,
        deadline: float | None = None,
        transport_context: dict[str, Any] | None = None,
    ) -> ModelResponseReceipt:
        if (
            request.provider != "vertex-ai"
            or request.model_id != self.model_id
            or request.model_version != self.model_id
            or request.seed is not None
        ):
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="VERTEX_REQUEST_BINDING_MISMATCH",
                evidence_class=EvidenceClass.NOT_RUN,
                completed_at=self._now(),
            )
        response_schema = output_model.model_json_schema(mode="validation")
        expected_schema_digest = sha256_digest(response_schema)
        if expected_schema_digest != request.schema_digest:
            self.runtime_binding["error_code"] = (
                "VERTEX_OUTPUT_SCHEMA_BINDING_MISMATCH"
            )
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="VERTEX_OUTPUT_SCHEMA_BINDING_MISMATCH",
                evidence_class=EvidenceClass.NOT_RUN,
                completed_at=self._now(),
            )
        transport_context = transport_context if transport_context is not None else {}
        project_id = transport_context.get("project_id") or self._resolve_project(deadline=deadline)
        if project_id is None or _PROJECT_ID.fullmatch(project_id) is None:
            self.runtime_binding["error_code"] = "VERTEX_PROJECT_MISSING_OR_INVALID"
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="VERTEX_PROJECT_MISSING_OR_INVALID",
                evidence_class=EvidenceClass.NOT_RUN,
                completed_at=self._now(),
            )
        credential = transport_context.get("credential") or self._resolve_credential(deadline=deadline)
        if credential is None:
            self.runtime_binding["error_code"] = "VERTEX_CREDENTIALS_MISSING"
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code="VERTEX_CREDENTIALS_MISSING",
                evidence_class=EvidenceClass.NOT_RUN,
                completed_at=self._now(),
            )
        auth_mode, secret = credential
        transport_context.update(project_id=project_id, credential=credential)
        self.runtime_binding.update(
            {
                "auth_mode": auth_mode,
                "project_id_digest": sha256_digest(project_id),
            }
        )
        generation_config: dict[str, Any] = {
            "maxOutputTokens": request.max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
            "thinkingConfig": {"thinkingLevel": "LOW"},
        }
        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are a candidate-only enterprise workflow reviewer. "
                            "Return only JSON matching the supplied response schema. "
                            "A worker ABSTAIN or any missing required field requires "
                            "REPLAN; otherwise return PASS. Your answer is advisory "
                            "and cannot approve or write canonical state."
                        )
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                self.prompt_payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                        }
                    ],
                }
            ],
            "generationConfig": generation_config,
        }
        raw_request = json.dumps(
            body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        self.runtime_binding.update(
            {
                "request_payload_digest": sha256_digest(body),
                "max_output_tokens": request.max_output_tokens,
                "thinking_level": "LOW",
            }
        )
        endpoint = (
            "https://aiplatform.googleapis.com/v1/projects/"
            f"{urllib.parse.quote(project_id, safe='')}/locations/global/"
            "publishers/google/models/"
            f"{urllib.parse.quote(self.model_id, safe='')}:generateContent"
        )
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "OrgRebase/vertex-structured-advisory",
        }
        if auth_mode.endswith("API_KEY"):
            headers["X-goog-api-key"] = secret
        else:
            headers["Authorization"] = f"Bearer {secret}"
        http_request = urllib.request.Request(
            endpoint,
            data=raw_request,
            headers=headers,
            method="POST",
        )
        started = time.perf_counter()
        remaining = self.timeout_seconds if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            return _receipt(request, status="NOT_RUN", value=None, provider_request_id=None,
                            schema_valid=False, error_code="VERTEX_TOTAL_DEADLINE_EXHAUSTED",
                            evidence_class=EvidenceClass.NOT_RUN, completed_at=self._now())
        if not observation.before_send(raw_request):
            return _receipt(
                request,
                status="NOT_RUN",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=observation.error_code,
                evidence_class=EvidenceClass.NOT_RUN,
                completed_at=self._now(),
            )
        try:
            with urllib.request.urlopen(
                http_request, timeout=remaining
            ) as response:
                self.runtime_binding["http_status"] = getattr(response, "status", 200)
                raw_response = _read_vertex_response(response, deadline)
                observation.response(raw_response)
            payload = json.loads(raw_response.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            observation.dispatch_state = "RESPONSE_RECEIVED"
            self.runtime_binding["http_status"] = exc.code
            if exc.code == 429:
                observation.error_code = "VERTEX_RATE_LIMITED"
                try:
                    error_body = _read_vertex_response(exc.fp, deadline, max_bytes=65536)
                    if len(error_body) <= 65536:
                        observation.response(error_body)
                        error_payload = json.loads(error_body)
                        if isinstance(error_payload, dict):
                            observation.metadata(
                                usage=observed_usage(error_payload.get("usageMetadata"), {
                                    "input_tokens": "promptTokenCount", "output_tokens": "candidatesTokenCount",
                                    "thinking_tokens": "thoughtsTokenCount", "total_tokens": "totalTokenCount",
                                    "cached_tokens": "cachedContentTokenCount",
                                }), observed_model=error_payload.get("modelVersion"),
                                provider_request_id=error_payload.get("responseId"),
                            )
                except (OSError, TypeError, ValueError, AttributeError):
                    pass
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        try:
                            seconds = float(retry_after)
                        except ValueError:
                            seconds = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                        if math.isfinite(seconds) and seconds >= 0:
                            self.runtime_binding["retry_after_seconds"] = seconds
                    except (TypeError, ValueError, OverflowError):
                        pass
            exc.close()
            self.runtime_binding.update(
                {
                    "status": "NOT_RUN",
                    "error_code": f"VERTEX_HTTP_ERROR:{exc.code}",
                }
            )
            return _receipt(
                request,
                status="PROVIDER_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=f"VERTEX_HTTP_ERROR:{exc.code}",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
                completed_at=self._now(),
            )
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            self.runtime_binding.update(
                {
                    "status": "NOT_RUN",
                    "error_code": f"VERTEX_PROVIDER_ERROR:{type(exc).__name__}",
                }
            )
            return _receipt(
                request,
                status="PROVIDER_ERROR",
                value=None,
                provider_request_id=None,
                schema_valid=False,
                error_code=f"VERTEX_PROVIDER_ERROR:{type(exc).__name__}",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
                completed_at=self._now(),
            )
        response_digest = sha256_digest(payload)
        provider_request_id = (
            str(payload.get("responseId") or "") if isinstance(payload, dict) else ""
        )
        observed_model_version = (
            str(payload.get("modelVersion") or "") if isinstance(payload, dict) else ""
        )
        usage = observed_usage(
            payload.get("usageMetadata") if isinstance(payload, dict) else None,
            {
                "input_tokens": "promptTokenCount",
                "output_tokens": "candidatesTokenCount",
                "thinking_tokens": "thoughtsTokenCount",
                "total_tokens": "totalTokenCount",
                "cached_tokens": "cachedContentTokenCount",
            },
        )
        observation.metadata(
            usage=usage, observed_model=observed_model_version, provider_request_id=provider_request_id
        )
        self.runtime_binding.update(
            {
                "response_observation_digest": response_digest,
                "observed_model_version": observed_model_version or None,
            }
        )
        if not provider_request_id or not observed_model_version:
            self.runtime_binding.update(
                {"status": "NOT_RUN", "error_code": "VERTEX_RESPONSE_BINDING_MISSING"}
            )
            return _receipt(
                request,
                status="PROVIDER_ERROR",
                value=None,
                provider_request_id=provider_request_id or None,
                schema_valid=False,
                error_code="VERTEX_RESPONSE_BINDING_MISSING",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
                completed_at=self._now(),
            )
        if observed_model_version != self.model_id:
            self.runtime_binding.update(
                {
                    "status": "NOT_RUN",
                    "error_code": "VERTEX_MODEL_VERSION_MISMATCH",
                }
            )
            return _receipt(
                request,
                status="PROVIDER_ERROR",
                value=None,
                provider_request_id=provider_request_id,
                schema_valid=False,
                error_code="VERTEX_MODEL_VERSION_MISMATCH",
                evidence_class=EvidenceClass.NOT_RUN,
                latency_ms=int((time.perf_counter() - started) * 1000),
                completed_at=self._now(),
            )
        candidates = payload.get("candidates")
        first_candidate = (
            candidates[0]
            if isinstance(candidates, list)
            and candidates
            and isinstance(candidates[0], dict)
            else {}
        )
        finish_reason = str(first_candidate.get("finishReason") or "unknown")
        input_tokens = legacy_token_count(usage.input_tokens)
        output_tokens = legacy_token_count(usage.output_tokens)
        self.runtime_binding.update(
            {
                "thinking_tokens": usage.thinking_tokens,
                "total_tokens": usage.total_tokens,
            }
        )
        if finish_reason != "STOP":
            self.runtime_binding.update(
                {
                    "status": "SCHEMA_ERROR",
                    "error_code": f"VERTEX_FINISH_REASON_NOT_STOP:{finish_reason}",
                }
            )
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=provider_request_id,
                schema_valid=False,
                error_code=f"VERTEX_FINISH_REASON_NOT_STOP:{finish_reason}",
                evidence_class="LIVE_MODEL",
                latency_ms=int((time.perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                completed_at=str(payload.get("createTime") or self._now()),
            )
        try:
            parts = first_candidate["content"]["parts"]
            raw_content = "".join(
                str(part["text"])
                for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
            if not raw_content:
                raise ValueError("VERTEX_RESPONSE_TEXT_MISSING")
            decoded = json.loads(raw_content)
            value = output_model.model_validate(decoded)
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            self.runtime_binding.update(
                {
                    "status": "SCHEMA_ERROR",
                    "error_code": f"VERTEX_SCHEMA_MISMATCH:{type(exc).__name__}",
                }
            )
            return _receipt(
                request,
                status="SCHEMA_ERROR",
                value=None,
                provider_request_id=provider_request_id,
                schema_valid=False,
                error_code=f"VERTEX_SCHEMA_MISMATCH:{type(exc).__name__}",
                evidence_class="LIVE_MODEL",
                latency_ms=int((time.perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                completed_at=str(payload.get("createTime") or self._now()),
            )
        self.runtime_binding.update(
            {
                "status": "OBSERVED",
                "provider_request_id_present": True,
                "response_schema_digest": expected_schema_digest,
            }
        )
        return _receipt(
            request,
            status="VALID",
            value=value,
            provider_request_id=provider_request_id,
            schema_valid=True,
            error_code=None,
            evidence_class="LIVE_MODEL",
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            completed_at=str(payload.get("createTime") or self._now()),
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
