from __future__ import annotations

import io
import json
import subprocess
import urllib.error
import urllib.request
from http import HTTPStatus
from http.client import HTTPConnection
from threading import Thread
from typing import Any

import pytest
from pydantic import BaseModel

import orgrebase.vertex_tool_bridge as bridge_module
import orgrebase.workspace.model_provider as model_provider_module
from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass
from orgrebase.vertex_tool_bridge import (
    MAX_BODY_BYTES,
    SignatureCache,
    VertexToolBridgeHandler,
    VertexToolBridgeServer,
    read_http_body,
)
from orgrebase.workspace.model_provider import (
    VERTEX_MODEL_ID,
    BoundedSchemaRepairProvider,
    DeterministicModelProvider,
    LiveHTTPModelProvider,
    LocalOllamaStructuredProvider,
    RecordedModelProvider,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.models import ModelRequest


class BoundaryOutput(BaseModel):
    value: str


def _request(
    *,
    provider: str = "deterministic",
    model_id: str = "boundary-model",
    model_version: str = "v1",
    seed: int | None = 0,
    schema_digest: str | None = None,
) -> ModelRequest:
    return ModelRequest(
        request_id=f"model-request:boundary:{provider}",
        run_id="run:boundary",
        task_ref="task:boundary",
        actor_id="agent:boundary",
        purpose="adapter_boundary_test",
        schema_name="BoundaryOutput",
        schema_digest=schema_digest
        or sha256_digest(BoundaryOutput.model_json_schema(mode="validation")),
        context_refs=("sha256:" + "1" * 64,),
        input_refs=("sha256:" + "2" * 64,),
        allowed_tool_ids=(),
        provider=provider,
        model_id=model_id,
        model_version=model_version,
        prompt_template_ref="prompt:boundary@v1",
        prompt_template_digest="sha256:" + "3" * 64,
        temperature=0.0,
        seed=seed,
        max_output_tokens=64,
        attempt=0,
    )


class _HTTPResponse:
    def __init__(
        self,
        payload: Any | None = None,
        *,
        raw: bytes | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> _HTTPResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _live_http_provider(monkeypatch: pytest.MonkeyPatch) -> LiveHTTPModelProvider:
    monkeypatch.setenv("BOUNDARY_MODEL_ENDPOINT", "https://provider.invalid/v1/chat")
    monkeypatch.setenv("BOUNDARY_MODEL_KEY", "in-memory-test-key")
    return LiveHTTPModelProvider(
        endpoint_env="BOUNDARY_MODEL_ENDPOINT",
        key_env="BOUNDARY_MODEL_KEY",
    )


def test_recorded_provider_covers_bound_success_missing_and_malformed_records() -> None:
    request = _request()

    valid = RecordedModelProvider({request.digest: {"value": "recorded"}}).generate_structured(
        request=request,
        output_model=BoundaryOutput,
    )
    missing = RecordedModelProvider({}).generate_structured(
        request=request,
        output_model=BoundaryOutput,
    )
    malformed = RecordedModelProvider({request.digest: {"wrong": "shape"}}).generate_structured(
        request=request,
        output_model=BoundaryOutput,
    )

    assert valid.status == "VALID"
    assert valid.value == {"value": "recorded"}
    assert valid.finish_reason == "replay"
    assert missing.status == "ABSTAIN"
    assert missing.error_code == "RECORDED_RESPONSE_NOT_FOUND"
    assert missing.evidence_class == EvidenceClass.NOT_RUN
    assert malformed.status == "SCHEMA_ERROR"
    assert malformed.error_code == "RECORDED_RESPONSE_SCHEMA_MISMATCH"


@pytest.mark.parametrize(
    ("provider_request_id", "expected_evidence"),
    [
        ("provider-request-1", "LIVE_MODEL"),
        (None, EvidenceClass.NOT_RUN),
    ],
)
def test_live_http_success_requires_provider_id_for_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
    provider_request_id: str | None,
    expected_evidence: str,
) -> None:
    provider = _live_http_provider(monkeypatch)
    observed: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> _HTTPResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        headers = {"x-request-id": provider_request_id} if provider_request_id else {}
        return _HTTPResponse(
            {
                "choices": [
                    {
                        "message": {"content": json.dumps({"value": "live"})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
            headers=headers,
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    receipt = provider.generate_structured(
        request=_request(provider="openai-compatible"),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "VALID"
    assert receipt.value == {"value": "live"}
    assert receipt.provider_request_id == provider_request_id
    assert receipt.evidence_class == expected_evidence
    assert receipt.input_tokens == 7
    assert receipt.output_tokens == 3
    assert observed["timeout"] == 30
    sent = observed["request"]
    assert sent.get_header("Authorization") == "Bearer in-memory-test-key"
    assert json.loads(sent.data)["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (TimeoutError("deadline"), "LIVE_MODEL_PROVIDER_ERROR:TimeoutError"),
        (
            urllib.error.URLError("offline"),
            "LIVE_MODEL_PROVIDER_ERROR:URLError",
        ),
    ],
)
def test_live_http_timeout_and_transport_errors_are_not_live(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_error: str,
) -> None:
    provider = _live_http_provider(monkeypatch)

    def fail_urlopen(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    receipt = provider.generate_structured(
        request=_request(provider="openai-compatible"),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == expected_error
    assert receipt.evidence_class == EvidenceClass.NOT_RUN
    assert receipt.provider_request_id is None


def test_live_http_malformed_provider_json_is_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _live_http_provider(monkeypatch)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(raw=b"{not-json"),
    )

    receipt = provider.generate_structured(
        request=_request(provider="openai-compatible"),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == "LIVE_MODEL_PROVIDER_ERROR:JSONDecodeError"
    assert receipt.evidence_class == EvidenceClass.NOT_RUN


def test_live_http_schema_rejection_keeps_only_observed_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _live_http_provider(monkeypatch)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(
            {"id": "provider-request-invalid", "choices": []}
        ),
    )

    receipt = provider.generate_structured(
        request=_request(provider="openai-compatible"),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "SCHEMA_ERROR"
    assert receipt.schema_valid is False
    assert receipt.provider_request_id == "provider-request-invalid"
    assert receipt.evidence_class == "LIVE_MODEL"
    assert receipt.error_code == "LIVE_MODEL_SCHEMA_MISMATCH"


OLLAMA_DIGEST = "a" * 64


def _ollama_request(**overrides: Any) -> ModelRequest:
    values = {
        "provider": "ollama-local",
        "model_id": "boundary-ollama:1b",
        "model_version": f"ollama-manifest:{OLLAMA_DIGEST}",
        "seed": 42,
    }
    values.update(overrides)
    return _request(**values)


def _ollama_provider(**overrides: Any) -> LocalOllamaStructuredProvider:
    values = {
        "prompt_payload": {"task": "bounded-review"},
        "model_id": "boundary-ollama:1b",
        "expected_model_digest": OLLAMA_DIGEST,
        "timeout_seconds": 2.0,
    }
    values.update(overrides)
    return LocalOllamaStructuredProvider(**values)


def _ollama_tags() -> dict[str, Any]:
    return {
        "models": [
            {
                "name": "boundary-ollama:1b",
                "digest": OLLAMA_DIGEST,
            }
        ]
    }


def test_ollama_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(ValueError, match="OLLAMA_ENDPOINT_MUST_BE_LOOPBACK_HTTP"):
        _ollama_provider(endpoint="https://ollama.example.com")


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "different-provider"},
        {"model_id": "different-model"},
        {"model_version": "ollama-manifest:" + "b" * 64},
    ],
)
def test_ollama_request_binding_mismatch_never_calls_network(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
) -> None:
    def unexpected_network(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        raise AssertionError("binding rejection must happen before network access")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_network)
    receipt = _ollama_provider().generate_structured(
        request=_ollama_request(**overrides),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "OLLAMA_REQUEST_BINDING_MISMATCH"


def test_ollama_tags_timeout_is_honest_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ollama_provider()

    def timeout(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        raise TimeoutError("tags deadline")

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    receipt = provider.generate_structured(
        request=_ollama_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "OLLAMA_TAGS_UNAVAILABLE:TimeoutError"
    assert provider.runtime_binding["error_code"] == receipt.error_code


def test_ollama_malformed_tags_response_is_honest_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ollama_provider()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(raw=b"{not-json"),
    )

    receipt = provider.generate_structured(
        request=_ollama_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "OLLAMA_TAGS_UNAVAILABLE:JSONDecodeError"
    assert receipt.evidence_class == EvidenceClass.NOT_RUN


def test_ollama_requires_exact_local_model_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ollama_provider()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(
            {"models": [{"name": "boundary-ollama:1b", "digest": "different"}]}
        ),
    )

    receipt = provider.generate_structured(
        request=_ollama_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "OLLAMA_EXACT_MODEL_DIGEST_NOT_AVAILABLE"
    assert provider.runtime_binding["status"] == "NOT_RUN"
    assert provider.runtime_binding["observed_model_digest"] == "different"


def test_ollama_valid_response_is_bound_to_tags_and_local_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ollama_provider()
    observed_requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> _HTTPResponse:
        observed_requests.append(request)
        if request.full_url.endswith("/api/tags"):
            assert timeout == 2.0
            return _HTTPResponse(_ollama_tags())
        assert request.full_url.endswith("/api/chat")
        assert timeout == 2.0
        return _HTTPResponse(
            {
                "model": "boundary-ollama:1b",
                "message": {"content": json.dumps({"value": "local"})},
                "prompt_eval_count": 11,
                "eval_count": 5,
                "done_reason": "stop",
                "created_at": "2026-09-02T00:00:00Z",
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    receipt = provider.generate_structured(
        request=_ollama_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "VALID"
    assert receipt.value == {"value": "local"}
    assert receipt.evidence_class == "LOCAL_OLLAMA_MODEL"
    assert receipt.input_tokens == 11
    assert receipt.output_tokens == 5
    assert provider.runtime_binding["status"] == "BOUND"
    assert provider.runtime_binding["response_observation_digest"].startswith("sha256:")
    assert provider.runtime_binding["client_request_id"].startswith("ollama-client:")
    assert len(observed_requests) == 2


@pytest.mark.parametrize(
    ("chat_result", "expected_status", "expected_error"),
    [
        (
            _HTTPResponse(
                {
                    "model": "wrong-model",
                    "message": {"content": json.dumps({"value": "ignored"})},
                }
            ),
            "SCHEMA_ERROR",
            "OLLAMA_SCHEMA_MISMATCH:ValueError",
        ),
        (
            _HTTPResponse(raw=b"not-json"),
            "NOT_RUN",
            "OLLAMA_UNAVAILABLE_OR_MODEL_MISSING:JSONDecodeError",
        ),
        (
            TimeoutError("chat deadline"),
            "NOT_RUN",
            "OLLAMA_UNAVAILABLE_OR_MODEL_MISSING:TimeoutError",
        ),
    ],
)
def test_ollama_chat_mismatch_malformed_json_and_timeout_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    chat_result: _HTTPResponse | Exception,
    expected_status: str,
    expected_error: str,
) -> None:
    provider = _ollama_provider()
    calls = 0

    def fake_urlopen(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _HTTPResponse(_ollama_tags())
        if isinstance(chat_result, Exception):
            raise chat_result
        return chat_result

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    receipt = provider.generate_structured(
        request=_ollama_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == expected_status
    assert receipt.error_code == expected_error
    assert receipt.schema_valid is False
    assert calls == 2


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (subprocess.CompletedProcess([], 0, stdout="resolved\n"), "resolved"),
        (subprocess.CompletedProcess([], 1, stdout="ignored\n"), None),
        (subprocess.CompletedProcess([], 0, stdout="(unset)\n"), None),
    ],
)
def test_gcloud_value_accepts_only_successful_nonempty_values(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
    expected: str | None,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: result)
    assert model_provider_module._gcloud_value("config", "get-value", "project") == expected


def test_gcloud_value_timeout_is_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("gcloud", 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert model_provider_module._gcloud_value("auth", "print-access-token") is None


def _vertex_request(*, schema_digest: str | None = None) -> ModelRequest:
    return _request(
        provider="vertex-ai",
        model_id=VERTEX_MODEL_ID,
        model_version=VERTEX_MODEL_ID,
        seed=None,
        schema_digest=schema_digest,
    )


def _vertex_provider(**overrides: Any) -> VertexAIStructuredProvider:
    values = {
        "prompt_payload": {"task": "bounded-review"},
        "project_id": "orgrebase-boundary1",
        "access_token": "short-lived-token",
        "adc_token_resolver": lambda: None,
        "project_resolver": lambda: None,
        "timeout_seconds": 3.0,
    }
    values.update(overrides)
    return VertexAIStructuredProvider(**values)


def _vertex_payload(*, parts: Any = None) -> dict[str, Any]:
    return {
        "responseId": "vertex-boundary-1",
        "modelVersion": VERTEX_MODEL_ID,
        "createTime": "2026-09-02T00:00:00Z",
        "candidates": [
            {
                "content": {
                    "parts": parts
                    if parts is not None
                    else [{"text": json.dumps({"value": "vertex"})}]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {},
    }


def test_vertex_rejects_schema_and_project_before_credential_resolution() -> None:
    def unexpected_credential() -> str | None:
        raise AssertionError("credentials must not be resolved")

    schema_receipt = _vertex_provider(
        access_token=None,
        adc_token_resolver=unexpected_credential,
    ).generate_structured(
        request=_vertex_request(schema_digest="sha256:" + "f" * 64),
        output_model=BoundaryOutput,
    )
    project_receipt = _vertex_provider(
        project_id="bad",
        access_token=None,
        adc_token_resolver=unexpected_credential,
    ).generate_structured(
        request=_vertex_request(),
        output_model=BoundaryOutput,
    )

    assert schema_receipt.status == "SCHEMA_ERROR"
    assert schema_receipt.error_code == "VERTEX_OUTPUT_SCHEMA_BINDING_MISMATCH"
    assert schema_receipt.evidence_class == EvidenceClass.NOT_RUN
    assert project_receipt.status == "NOT_RUN"
    assert project_receipt.error_code == "VERTEX_PROJECT_MISSING_OR_INVALID"


def test_vertex_timeout_is_provider_error_without_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        raise TimeoutError("vertex deadline")

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    provider = _vertex_provider()
    receipt = provider.generate_structured(
        request=_vertex_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == "VERTEX_PROVIDER_ERROR:TimeoutError"
    assert receipt.evidence_class == EvidenceClass.NOT_RUN
    assert provider.runtime_binding["status"] == "NOT_RUN"


def test_vertex_malformed_http_json_is_provider_error_without_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(raw=b"{not-json"),
    )
    provider = _vertex_provider()

    receipt = provider.generate_structured(
        request=_vertex_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == "VERTEX_PROVIDER_ERROR:JSONDecodeError"
    assert receipt.evidence_class == EvidenceClass.NOT_RUN
    assert provider.runtime_binding["status"] == "NOT_RUN"


@pytest.mark.parametrize("missing_field", ["responseId", "modelVersion"])
def test_vertex_response_requires_provider_and_model_bindings(
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
) -> None:
    payload = _vertex_payload()
    payload.pop(missing_field)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(payload),
    )
    provider = _vertex_provider()
    receipt = provider.generate_structured(
        request=_vertex_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == "VERTEX_RESPONSE_BINDING_MISSING"
    assert receipt.provider_request_id == (
        None if missing_field == "responseId" else "vertex-boundary-1"
    )
    assert receipt.evidence_class == EvidenceClass.NOT_RUN
    assert provider.runtime_binding["status"] == "NOT_RUN"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "different-provider"),
        ("model_id", "different-model"),
        ("model_version", "different-version"),
        ("seed", 0),
    ],
)
def test_vertex_request_binding_mismatch_never_resolves_credentials_or_network(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str | int,
) -> None:
    def unexpected_credential() -> str | None:
        raise AssertionError("binding rejection must happen before credential resolution")

    def unexpected_network(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        raise AssertionError("binding rejection must happen before network access")

    payload = _vertex_request().model_dump(mode="json", exclude={"digest"})
    payload[field] = value
    request = ModelRequest.model_validate(payload)
    provider = _vertex_provider(
        access_token=None,
        adc_token_resolver=unexpected_credential,
    )
    monkeypatch.setattr(urllib.request, "urlopen", unexpected_network)

    receipt = provider.generate_structured(
        request=request,
        output_model=BoundaryOutput,
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "VERTEX_REQUEST_BINDING_MISMATCH"
    assert receipt.evidence_class == EvidenceClass.NOT_RUN


def test_vertex_empty_text_parts_are_schema_error_with_observed_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(
            _vertex_payload(parts=[{"inlineData": {"mimeType": "text/plain"}}])
        ),
    )
    provider = _vertex_provider()
    receipt = provider.generate_structured(
        request=_vertex_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "SCHEMA_ERROR"
    assert receipt.error_code == "VERTEX_SCHEMA_MISMATCH:ValueError"
    assert receipt.provider_request_id == "vertex-boundary-1"
    assert receipt.evidence_class == "LIVE_MODEL"
    assert provider.runtime_binding["status"] == "SCHEMA_ERROR"


def test_vertex_can_resolve_project_and_api_key_from_dedicated_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORGREBASE_VERTEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("ORGREBASE_VERTEX_API_KEY", "environment-api-key")
    monkeypatch.setenv("ORGREBASE_VERTEX_PROJECT_ID", "orgrebase-env1")
    observed: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> _HTTPResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return _HTTPResponse(_vertex_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = _vertex_provider(
        project_id=None,
        access_token=None,
        api_key=None,
    )
    clock = [100.0]
    monkeypatch.setattr(model_provider_module.time, "monotonic", lambda: clock[0])
    resolve_project = provider._resolve_project

    def delayed_project_resolution(*, deadline: float | None = None) -> str | None:
        clock[0] += 0.25
        return resolve_project(deadline=deadline)

    monkeypatch.setattr(provider, "_resolve_project", delayed_project_resolution)
    receipt = provider.generate_structured(
        request=_vertex_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "VALID"
    assert provider.runtime_binding["auth_mode"] == "ENV_API_KEY"
    assert observed["request"].get_header("X-goog-api-key") == "environment-api-key"
    assert observed["timeout"] == 2.75


@pytest.mark.parametrize("terminal_status", ["VALID", "NOT_RUN", "PROVIDER_ERROR"])
def test_bounded_schema_repair_stops_on_terminal_provider_status(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    calls = 0
    request = _request()

    if terminal_status == "VALID":
        def resolver(_request: ModelRequest) -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"value": "ok"}

        inner: Any = DeterministicModelProvider(resolver)
    else:
        error = (
            TimeoutError("provider deadline")
            if terminal_status == "PROVIDER_ERROR"
            else None
        )

        class TerminalProvider:
            def generate_structured(
                self,
                *,
                request: ModelRequest,
                output_model: type[BoundaryOutput],
            ) -> Any:
                nonlocal calls
                calls += 1
                if error is not None:
                    provider = _live_http_provider(monkeypatch)

                    def fail(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
                        raise error

                    monkeypatch.setattr(urllib.request, "urlopen", fail)
                    return provider.generate_structured(
                        request=request,
                        output_model=output_model,
                    )
                monkeypatch.delenv("BOUNDARY_MODEL_ENDPOINT", raising=False)
                monkeypatch.delenv("BOUNDARY_MODEL_KEY", raising=False)
                return LiveHTTPModelProvider(
                    endpoint_env="BOUNDARY_MODEL_ENDPOINT",
                    key_env="BOUNDARY_MODEL_KEY",
                ).generate_structured(request=request, output_model=output_model)

        inner = TerminalProvider()

    receipt = BoundedSchemaRepairProvider(inner).generate_structured(
        request=request,
        output_model=BoundaryOutput,
    )

    assert receipt.status == terminal_status
    assert calls == 1


def test_bounded_schema_repair_rebinds_each_attempt_and_stops_on_valid_output() -> None:
    attempts: list[ModelRequest] = []

    def resolver(request: ModelRequest) -> dict[str, str]:
        attempts.append(request)
        if request.attempt < 2:
            return {"wrong": "shape"}
        return {"value": "repaired"}

    receipt = BoundedSchemaRepairProvider(
        DeterministicModelProvider(resolver)
    ).generate_structured(
        request=_request(),
        output_model=BoundaryOutput,
    )

    assert receipt.status == "VALID"
    assert receipt.value == {"value": "repaired"}
    assert [request.attempt for request in attempts] == [0, 1, 2]
    assert len({request.digest for request in attempts}) == 3
    assert receipt.request_digest == attempts[-1].digest


class _BridgeHarness(VertexToolBridgeHandler):
    def __init__(
        self,
        *,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
        self.cache = SignatureCache()
        self.upstream_url = "https://aiplatform.googleapis.com/openai"
        self.upstream_timeout_seconds = 4
        self.response_status: int | None = None
        self.response_headers: list[tuple[str, str]] = []
        self.logs: list[tuple[str, tuple[Any, ...]]] = []

    def send_response(self, code: int, _message: str | None = None) -> None:
        self.response_status = int(code)

    def send_header(self, keyword: str, value: str) -> None:
        self.response_headers.append((keyword, value))

    def end_headers(self) -> None:
        return None

    def log_message(self, format: str, *args: Any) -> None:
        self.logs.append((format, args))


def test_bridge_forwards_injected_call_and_captures_upstream_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_call = {
        "id": "provider-call-1",
        "type": "function",
        "function": {"name": "agents_list", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "opaque-1"}},
    }
    body = json.dumps(
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "provider-call-1",
                            "type": "function",
                            "function": {"name": "agents_list", "arguments": "{}"},
                        }
                    ],
                }
            ]
        }
    ).encode()
    handler = _BridgeHarness(
        path="/v1/chat/completions?trace=1",
        body=body,
        headers={"Authorization": "Bearer delegated", "Accept": "text/event-stream"},
    )
    handler.cache.put("provider-call-1", provider_call)
    upstream_body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "provider-call-2",
                                "function": {"name": "agents_message", "arguments": "{}"},
                                "extra_content": {
                                    "google": {"thought_signature": "opaque-2"}
                                },
                            }
                        ]
                    }
                }
            ]
        }
    ).encode()
    observed: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> _HTTPResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return _HTTPResponse(
            raw=upstream_body,
            status=200,
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    handler.do_POST()

    forwarded = json.loads(observed["request"].data)
    forwarded_call = forwarded["messages"][0]["tool_calls"][0]
    assert forwarded_call == provider_call
    assert observed["request"].full_url.endswith("/openai/chat/completions")
    assert observed["request"].get_header("Authorization") == "Bearer delegated"
    assert observed["timeout"] == 4
    assert handler.response_status == HTTPStatus.OK
    assert ("X-OrgRebase-Signatures-Injected", "1") in handler.response_headers
    assert handler.wfile.getvalue() == upstream_body
    assert handler.cache.contains("provider-call-2")
    assert handler.logs


@pytest.mark.parametrize(
    ("path", "body", "headers", "expected_status", "expected_body"),
    [
        (
            "/unsupported",
            b"",
            {},
            HTTPStatus.NOT_FOUND,
            b'{"error":"unsupported_path"}',
        ),
        (
            "/chat/completions",
            b"",
            {"Content-Length": "invalid"},
            HTTPStatus.BAD_REQUEST,
            b'{"error":"invalid_http_body"}',
        ),
        (
            "/chat/completions",
            b"{}",
            {"Content-Length": str(MAX_BODY_BYTES + 1)},
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            b'{"error":"invalid_content_length"}',
        ),
        (
            "/chat/completions",
            b"{",
            {},
            HTTPStatus.BAD_REQUEST,
            b'{"error":"invalid_json"}',
        ),
        (
            "/chat/completions",
            b"[]",
            {},
            HTTPStatus.BAD_REQUEST,
            b'{"error":"invalid_payload"}',
        ),
    ],
)
def test_bridge_rejects_unsupported_and_malformed_requests_before_upstream(
    path: str,
    body: bytes,
    headers: dict[str, str],
    expected_status: HTTPStatus,
    expected_body: bytes,
) -> None:
    handler = _BridgeHarness(path=path, body=body, headers=headers)

    handler.do_POST()

    assert handler.response_status == expected_status
    assert handler.wfile.getvalue() == expected_body


def test_bridge_timeout_returns_bounded_bad_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"messages": []}).encode()
    handler = _BridgeHarness(path="/chat/completions", body=body)

    def timeout(*_args: Any, **_kwargs: Any) -> _HTTPResponse:
        raise TimeoutError("upstream deadline")

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    handler.do_POST()

    assert handler.response_status == HTTPStatus.BAD_GATEWAY
    assert handler.wfile.getvalue() == b'{"error":"upstream_unavailable"}'


def test_bridge_preserves_upstream_http_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"messages": []}).encode()
    handler = _BridgeHarness(path="/chat/completions", body=body)
    rejection = b'{"error":"rate_limited"}'

    def reject(request: urllib.request.Request, *, timeout: int) -> _HTTPResponse:
        raise urllib.error.HTTPError(
            request.full_url,
            HTTPStatus.TOO_MANY_REQUESTS,
            "rate limited",
            {"Content-Type": "application/problem+json"},
            io.BytesIO(rejection),
        )

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    handler.do_POST()

    assert handler.response_status == HTTPStatus.TOO_MANY_REQUESTS
    assert ("Content-Type", "application/problem+json") in handler.response_headers
    assert handler.wfile.getvalue() == rejection


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_body"),
    [
        ("/healthz", HTTPStatus.OK, b'{"status":"ok"}'),
        ("/unknown", HTTPStatus.NOT_FOUND, b'{"error":"not_found"}'),
    ],
)
def test_bridge_get_surface_is_health_only(
    path: str,
    expected_status: HTTPStatus,
    expected_body: bytes,
) -> None:
    handler = _BridgeHarness(path=path)

    handler.do_GET()

    assert handler.response_status == expected_status
    assert handler.wfile.getvalue() == expected_body


def test_signature_cache_is_bounded_and_returns_defensive_copies() -> None:
    cache = SignatureCache(max_entries=1)
    original = {"id": "first", "extra_content": {"signature": "one"}}
    cache.put("first", original)
    cached = cache.get("first")
    assert cached is not None and cached.provider_tool_call is not None
    cached.provider_tool_call["extra_content"]["signature"] = "mutated"
    assert cache.get("first").provider_tool_call == original  # type: ignore[union-attr]

    cache.put("second", {"id": "second"})
    assert cache.get("first") is None
    assert cache.contains("second")
    assert len(cache) == 1


@pytest.mark.parametrize(
    ("raw", "expected_error"),
    [
        (b"broken", "invalid chunk header"),
        (b"zz\r\n", "invalid chunk size"),
    ],
)
def test_chunked_body_rejects_invalid_framing(raw: bytes, expected_error: str) -> None:
    with pytest.raises(ValueError, match=expected_error):
        read_http_body(io.BytesIO(raw), {"Transfer-Encoding": "chunked"})


def test_chunked_body_rejects_oversized_declared_chunk() -> None:
    raw = f"{MAX_BODY_BYTES + 1:x}\r\n".encode()
    with pytest.raises(OverflowError, match="request body too large"):
        read_http_body(io.BytesIO(raw), {"Transfer-Encoding": "chunked"})


def test_bridge_main_rejects_non_vertex_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERTEX_UPSTREAM_URL", "https://example.invalid/openai")
    with pytest.raises(SystemExit, match="must use the Vertex AI HTTPS endpoint"):
        bridge_module.main()


@pytest.mark.parametrize("host", (None, "0.0.0.0", "127.0.0.2"))
def test_bridge_main_starts_configured_server_without_real_socket(
    monkeypatch: pytest.MonkeyPatch,
    host: str | None,
) -> None:
    observed: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, address: tuple[str, int], *, upstream_url: str) -> None:
            observed["address"] = address
            observed["upstream_url"] = upstream_url

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            observed["closed"] = True

        def serve_forever(self) -> None:
            observed["served"] = True

    monkeypatch.setenv(
        "VERTEX_UPSTREAM_URL",
        "https://aiplatform.googleapis.com/openai",
    )
    monkeypatch.setenv("PORT", "9090")
    if host is None:
        monkeypatch.delenv("VERTEX_BRIDGE_HOST", raising=False)
    else:
        monkeypatch.setenv("VERTEX_BRIDGE_HOST", host)
    monkeypatch.setattr(bridge_module, "VertexToolBridgeServer", FakeServer)

    bridge_module.main()

    assert observed == {
        "address": (host or "127.0.0.1", 9090),
        "upstream_url": "https://aiplatform.googleapis.com/openai",
        "served": True,
        "closed": True,
    }


def test_bridge_rejects_empty_bind_address_before_opening_socket(monkeypatch) -> None:
    monkeypatch.setenv("VERTEX_UPSTREAM_URL", "https://aiplatform.googleapis.com/openai")
    monkeypatch.setenv("VERTEX_BRIDGE_HOST", " ")
    with pytest.raises(SystemExit, match="VERTEX_BRIDGE_HOST"):
        bridge_module.main()


def test_bridge_instances_keep_upstream_and_signature_cache_separate(monkeypatch) -> None:
    provider_call = {
        "id": "same-provider-call", "type": "function",
        "function": {"name": "agents_list", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "first-bridge-only"}},
    }
    observed = []

    def upstream(request, *, timeout):
        payload = json.loads(request.data)
        observed.append((request.full_url, payload))
        calls = [provider_call] if payload["messages"][0]["role"] == "user" else []
        return _HTTPResponse({"choices": [{"message": {"tool_calls": calls}}]})

    monkeypatch.setattr(urllib.request, "urlopen", upstream)

    def post(server, payload):
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()
        connection = HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request("POST", "/v1/chat/completions", json.dumps(payload),
                               {"Content-Type": "application/json", "Connection": "close"})
            response = connection.getresponse()
            response.read()
            assert response.status == HTTPStatus.OK
        finally:
            connection.close()
            thread.join(timeout=2)
        assert not thread.is_alive()

    with (
        VertexToolBridgeServer(("127.0.0.1", 0), upstream_url="https://aiplatform.googleapis.com/first") as first,
        VertexToolBridgeServer(("127.0.0.1", 0), upstream_url="https://aiplatform.googleapis.com/second") as second,
    ):
        first.timeout = second.timeout = 2
        post(first, {"messages": [{"role": "user", "content": "seed"}]})
        followup = {"messages": [{"role": "assistant", "tool_calls": [
            {key: value for key, value in provider_call.items() if key != "extra_content"},
        ]}]}
        post(second, followup)
        post(first, followup)
        assert first.signature_cache.contains("same-provider-call")
        assert not second.signature_cache.contains("same-provider-call")

    assert [url for url, _ in observed] == [
        "https://aiplatform.googleapis.com/first/chat/completions",
        "https://aiplatform.googleapis.com/second/chat/completions",
        "https://aiplatform.googleapis.com/first/chat/completions",
    ]
    assert "extra_content" not in observed[1][1]["messages"][0]["tool_calls"][0]
    assert observed[2][1]["messages"][0]["tool_calls"][0] == provider_call
