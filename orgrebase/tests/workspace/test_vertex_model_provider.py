from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace.competition_worker import ReviewerDecision
from orgrebase.workspace.model_provider import (
    VERTEX_EVIDENCE_CLASS,
    VERTEX_MODEL_ID,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.models import ModelRequest


def _request() -> ModelRequest:
    schema = ReviewerDecision.model_json_schema(mode="validation")
    return ModelRequest(
        request_id="model-request:run:vertex:review-1",
        run_id="run:vertex",
        task_ref="task:reviewer-a1",
        actor_id="reviewer:quote-coalition",
        purpose="candidate_coalition_review",
        schema_name="ReviewerDecision",
        schema_digest=sha256_digest(schema),
        context_refs=("sha256:" + "1" * 64,),
        input_refs=("sha256:" + "2" * 64,),
        allowed_tool_ids=(),
        provider="vertex-ai",
        model_id=VERTEX_MODEL_ID,
        model_version=VERTEX_MODEL_ID,
        prompt_template_ref="prompt:golden-competition-reviewer@v1",
        prompt_template_digest="sha256:" + "3" * 64,
        temperature=0.0,
        seed=None,
        max_output_tokens=2048,
        attempt=0,
    )


def _provider(**overrides: Any) -> VertexAIStructuredProvider:
    values = {
        "prompt_payload": {
            "run_id": "run:vertex",
            "review_phase": 1,
            "domain_results": [{"domain": "finance", "status": "ABSTAIN"}],
        },
        "project_id": "orgrebase-test1",
        "adc_token_resolver": lambda: None,
        "project_resolver": lambda: None,
    }
    values.update(overrides)
    return VertexAIStructuredProvider(**values)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _live_payload(*, content: Any, response_id: str = "vertex-response-1") -> dict:
    return {
        "responseId": response_id,
        "modelVersion": VERTEX_MODEL_ID,
        "createTime": "2026-08-27T12:00:00Z",
        "candidates": [
            {
                "content": {"parts": [{"text": content}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 41,
            "candidatesTokenCount": 13,
            "thoughtsTokenCount": 21,
            "totalTokenCount": 75,
        },
    }


def test_vertex_without_credentials_is_honest_not_run(monkeypatch) -> None:
    monkeypatch.delenv("ORGREBASE_VERTEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("ORGREBASE_VERTEX_API_KEY", raising=False)
    provider = _provider()

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.evidence_class == "NOT_RUN"
    assert receipt.error_code == "VERTEX_CREDENTIALS_MISSING"
    assert provider.runtime_binding["status"] == "NOT_RUN"
    assert "auth_mode" not in provider.runtime_binding


def test_vertex_valid_response_has_redacted_digest_bound_receipt(
    monkeypatch,
) -> None:
    secret = "test-access-token-never-persist"
    observed: dict[str, Any] = {}
    advisory = {
        "verdict": "REPLAN",
        "missing_domains": ["finance"],
        "reason_codes": ["FINANCE_ABSTAIN"],
    }

    def fake_urlopen(request, *, timeout):
        observed["url"] = request.full_url
        observed["headers"] = dict(request.header_items())
        observed["body"] = json.loads(request.data.decode("utf-8"))
        observed["timeout"] = timeout
        return _Response(
            _live_payload(
                content=json.dumps(advisory, separators=(",", ":")),
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = _provider(access_token=secret)

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "VALID"
    assert receipt.value == advisory
    assert receipt.provider_request_id == "vertex-response-1"
    assert receipt.model_version == VERTEX_MODEL_ID
    assert receipt.input_tokens == 41
    assert receipt.output_tokens == 13
    assert receipt.finish_reason == "STOP"
    assert receipt.latency_ms >= 0
    assert observed["url"].endswith(
        f"/locations/global/publishers/google/models/{VERTEX_MODEL_ID}:generateContent"
    )
    assert observed["headers"]["Authorization"] == f"Bearer {secret}"
    generation = observed["body"]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert "temperature" not in generation
    assert "seed" not in generation
    assert "topP" not in generation
    assert "topK" not in generation
    assert "frequencyPenalty" not in generation
    assert "presencePenalty" not in generation
    assert "candidateCount" not in generation
    assert generation["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert generation["maxOutputTokens"] == 2048
    assert generation["responseJsonSchema"]["required"] == [
        "verdict",
        "missing_domains",
        "reason_codes",
    ]
    binding = provider.runtime_binding
    assert binding["status"] == "OBSERVED"
    assert receipt.seed_supported is False
    assert binding["provider_evidence_class"] == VERTEX_EVIDENCE_CLASS
    assert binding["observed_model_version"] == VERTEX_MODEL_ID
    assert binding["thinking_level"] == "LOW"
    assert binding["thinking_tokens"] == 21
    assert binding["total_tokens"] == 75
    assert binding["max_output_tokens"] == 2048
    assert binding["request_payload_digest"].startswith("sha256:")
    assert binding["response_observation_digest"].startswith("sha256:")
    public = json.dumps(
        {
            "receipt": receipt.model_dump(mode="json"),
            "runtime_binding": binding,
        },
        sort_keys=True,
    )
    assert secret not in public
    assert "Authorization" not in public
    assert "orgrebase-test1" not in public


def test_vertex_can_resolve_short_lived_adc_token_without_persisting_it(
    monkeypatch,
) -> None:
    secret = "test-adc-token-never-persist"
    advisory = {
        "verdict": "PASS",
        "missing_domains": [],
        "reason_codes": ["ALL_DOMAINS_PRESENT"],
    }

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, *, timeout: _Response(
            _live_payload(content=json.dumps(advisory))
        ),
    )
    provider = _provider(adc_token_resolver=lambda: secret)

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "VALID"
    assert provider.runtime_binding["auth_mode"] == "ADC_GCLOUD_ACCESS_TOKEN"
    public = json.dumps(provider.runtime_binding, sort_keys=True)
    assert secret not in public
    assert "credential_path" not in public


def test_vertex_http_error_does_not_persist_provider_body_or_secret(
    monkeypatch,
) -> None:
    secret = "test-api-key-never-persist"
    provider_body_secret = "provider-body-must-not-leak"

    def fail_urlopen(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(provider_body_secret.encode("utf-8")),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    provider = _provider(api_key=secret)

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.evidence_class == "NOT_RUN"
    assert receipt.error_code == "VERTEX_HTTP_ERROR:403"
    public = json.dumps(
        {
            "receipt": receipt.model_dump(mode="json"),
            "runtime_binding": provider.runtime_binding,
        },
        sort_keys=True,
    )
    assert secret not in public
    assert provider_body_secret not in public
    assert "X-goog-api-key" not in public


def test_vertex_network_failure_is_provider_error_not_live(monkeypatch) -> None:
    def fail_urlopen(request, *, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    provider = _provider(access_token="test-short-lived-token")

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.evidence_class == "NOT_RUN"
    assert receipt.error_code == "VERTEX_PROVIDER_ERROR:URLError"
    assert provider.runtime_binding["status"] == "NOT_RUN"


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"verdict": "PASS", "missing_domains": []}),
    ],
)
def test_vertex_non_json_or_schema_mismatch_fails_closed(
    monkeypatch, content: str
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, *, timeout: _Response(_live_payload(content=content)),
    )
    provider = _provider(access_token="test-short-lived-token")

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "SCHEMA_ERROR"
    assert receipt.schema_valid is False
    assert receipt.provider_request_id == "vertex-response-1"
    assert receipt.evidence_class == "LIVE_MODEL"
    assert provider.runtime_binding["status"] == "SCHEMA_ERROR"


def test_vertex_observed_model_version_mismatch_is_not_live_evidence(
    monkeypatch,
) -> None:
    payload = _live_payload(
        content=json.dumps(
            {
                "verdict": "PASS",
                "missing_domains": [],
                "reason_codes": ["ALL_DOMAINS_PRESENT"],
            }
        )
    )
    payload["modelVersion"] = "different-model"
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, *, timeout: _Response(payload),
    )
    provider = _provider(access_token="test-short-lived-token")

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == "VERTEX_MODEL_VERSION_MISMATCH"
    assert receipt.evidence_class == "NOT_RUN"
    assert provider.runtime_binding["status"] == "NOT_RUN"


def test_vertex_max_tokens_finish_reason_fails_closed(monkeypatch) -> None:
    payload = _live_payload(content='{"verdict":"PASS"}')
    payload["candidates"][0]["finishReason"] = "MAX_TOKENS"
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, *, timeout: _Response(payload),
    )
    provider = _provider(access_token="test-short-lived-token")

    receipt = provider.generate_structured(
        request=_request(), output_model=ReviewerDecision
    )

    assert receipt.status == "SCHEMA_ERROR"
    assert receipt.finish_reason == "MAX_TOKENS"
    assert receipt.error_code == "VERTEX_FINISH_REASON_NOT_STOP:MAX_TOKENS"
    assert receipt.evidence_class == "LIVE_MODEL"


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [("PASS", 0), ("NOT_RUN", 2)],
)
def test_golden_cli_exit_code_tracks_summary_status(
    monkeypatch, capsys, tmp_path, status: str, expected_exit: int
) -> None:
    from scripts import run_golden_competition as command

    monkeypatch.setattr(
        command,
        "run_golden_competition",
        lambda **_kwargs: {
            "status": status,
            "run_id": "run:test",
            "digest": "sha256:" + "1" * 64,
            "model_provider": "vertex-ai",
            "canonical_target_writes": 0,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_golden_competition.py",
            "--output",
            str(tmp_path / "orgrebase-cli-test-unused"),
            "--model-provider",
            "vertex-ai",
        ],
    )

    assert command.main() == expected_exit
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == status
    assert printed["model_provider"] == "vertex-ai"


def test_vertex_request_binding_mismatch_never_resolves_credentials() -> None:
    called = False

    def resolver() -> str | None:
        nonlocal called
        called = True
        return "unexpected"

    payload = _request().model_dump(mode="json", exclude={"digest"})
    payload["model_id"] = "different-model"
    payload["model_version"] = "different-model"
    mismatched = ModelRequest.model_validate(payload)
    provider = VertexAIStructuredProvider(
        prompt_payload={"run_id": "run:vertex"},
        project_id="orgrebase-test1",
        adc_token_resolver=resolver,
        project_resolver=lambda: None,
    )

    receipt = provider.generate_structured(
        request=mismatched, output_model=ReviewerDecision
    )

    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "VERTEX_REQUEST_BINDING_MISMATCH"
    assert called is False
