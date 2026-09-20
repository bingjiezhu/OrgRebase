from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime

import httpx2 as httpx
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.workspace.models import ModelInputProjection, ModelRequestV2, ModelResponseReceiptV2
from orgrebase.workspace.openai_responses import OpenAIResponsesProvider, build_openai_responses_body
from tests.workspace.test_model_budget import budget


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str
    source_refs: tuple[str, ...]
    uncertainty: str | None = None


def request(**changes) -> ModelRequestV2:
    content = {"before": {"price_band": "standard"}, "after": {"price_band": "premium"}}
    prompt = "Explain the effect on the quote using only the admitted source."
    values = dict(
        request_id="model:change:product:0", tenant_id="tenant:one", workspace_id="workspace:one",
        run_id="run:one", nonce="nonce:one", task_ref="task:quote", actor_id="agent:product",
        purpose="change-advisory", domain_id="product", object_ids=("product:one",),
        input_projections=(ModelInputProjection(
            ref="projection:product:one", source_digest=sha256_digest({"source": "one"}),
            domain_id="product", object_ids=("product:one",), content=content,
            content_digest=sha256_digest(content),
        ),),
        schema_name="Candidate", schema_digest=sha256_digest(Candidate.model_json_schema(mode="validation")),
        prompt_template_ref="prompt:change:v2", prompt_template=prompt, prompt_template_digest=sha256_digest(prompt),
        model_id="gpt-6-astra", max_output_tokens=512,
    )
    values.update(changes)
    return ModelRequestV2(**values)


def response_payload(**changes):
    result = {
        "id": "resp:1", "model": "gpt-6-astra-snapshot", "status": "completed",
        "output": [{"type": "reasoning", "summary": []}, {
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps({
                "explanation": "The quote uses the new price band.",
                "source_refs": ["projection:product:one"], "uncertainty": None,
            })}],
        }],
        "usage": {"input_tokens": 43, "output_tokens": 24},
    }
    result.update(changes)
    return result


def generate(monkeypatch, payload=None, *, model_request=None, **provider_args):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=payload if payload is not None else response_payload(),
                              headers={"x-request-id": "req:observed"})

    provider = OpenAIResponsesProvider(transport=httpx.MockTransport(handler), **provider_args)
    receipt = provider.generate_structured(request=model_request or request(), output_model=Candidate)
    return receipt, calls


def test_actual_projection_and_authority_binding_are_sent(monkeypatch):
    incoming = request(reasoning_effort="high")
    receipt, calls = generate(monkeypatch, model_request=incoming)
    assert receipt.status == "VALID"
    assert receipt.dispatch_state == "RESPONSE_RECEIVED" and receipt.evidence_class == "LIVE_MODEL"
    body = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://api.openai.com/v1/responses"
    input_value = json.loads(body["input"][0]["content"][0]["text"])
    assert input_value["input_projections"][0]["content"]["after"] == {"price_band": "premium"}
    for key in ("tenant_id", "workspace_id", "run_id", "nonce", "task_ref", "actor_id", "purpose", "domain_id"):
        assert input_value["binding"][key] == getattr(incoming, key)
    assert body["tools"] == [] and body["store"] is False and body["background"] is False
    assert body["truncation"] == "disabled"
    assert body["service_tier"] == "default"
    assert body["reasoning"] == {"effort": "high"}
    assert "seed" not in body and "temperature" not in body
    assert receipt.body_digest == sha256_digest(body)
    assert calls[0].content == canonical_json(body).encode()
    assert receipt.requested_model_id == "gpt-6-astra"
    assert receipt.observed_model_id == "gpt-6-astra-snapshot"
    assert receipt.provider_request_id == "req:observed" and receipt.provider_response_id == "resp:1"
    assert receipt.input_tokens == 43 and receipt.output_tokens == 24
    assert datetime.fromisoformat(receipt.observed_at.replace("Z", "+00:00")).date() == datetime.now(UTC).date()


def test_wire_schema_is_explicit_and_independently_reproducible(monkeypatch):
    incoming = request()
    receipt, _ = generate(monkeypatch, model_request=incoming)
    body = build_openai_responses_body(incoming, Candidate)
    schema = body["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"explanation", "source_refs", "uncertainty"}
    assert "default" not in schema["properties"]["uncertainty"]
    assert receipt.wire_schema_digest == sha256_digest(schema)
    assert receipt.schema_digest == incoming.schema_digest
    assert receipt.prompt_digest == sha256_digest({"instructions": body["instructions"], "input": body["input"]})


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": "2", "output_tokens": False},
                                       {"input_tokens": -1, "output_tokens": -1}])
def test_unknown_usage_is_never_fabricated_as_zero(monkeypatch, usage):
    receipt, _ = generate(monkeypatch, response_payload(usage=usage))
    assert receipt.status == "VALID"
    assert receipt.input_tokens is None and receipt.output_tokens is None


@pytest.mark.parametrize(("payload", "expected_status", "error"), [
    (response_payload(status="incomplete", incomplete_details={"reason": "max_output_tokens"}), "INCOMPLETE", "OPENAI_RESPONSE_INCOMPLETE"),
    (response_payload(status="cancelled"), "CANCELLED", "OPENAI_RESPONSE_CANCELLED"),
    (response_payload(status="failed", error={"code": "server_error", "message": "private-data"}), "PROVIDER_ERROR", "OPENAI_RESPONSE_NOT_COMPLETED"),
    (response_payload(status="queued"), "INCOMPLETE", "OPENAI_RESPONSE_INCOMPLETE"),
    (response_payload(output=[{"type": "function_call", "name": "write_quote"}]), "SCHEMA_ERROR", "OPENAI_OUTPUT_ITEM_UNSUPPORTED"),
    (response_payload(output=[]), "SCHEMA_ERROR", "OPENAI_OUTPUT_MISSING"),
    (response_payload(model=None), "PROVIDER_ERROR", "OPENAI_RESPONSE_METADATA_MISSING"),
    (response_payload(status={"untrusted": "object"}), "PROVIDER_ERROR", "OPENAI_RESPONSE_STATUS_INVALID"),
])
def test_protocol_failures_cannot_become_candidates(monkeypatch, payload, expected_status, error):
    receipt, calls = generate(monkeypatch, payload)
    assert len(calls) == 1
    assert receipt.status == expected_status and receipt.error_code == error
    assert receipt.value is None and receipt.output_digest is None and not receipt.schema_valid
    assert receipt.provider_request_id == "req:observed"
    assert "private-data" not in receipt.model_dump_json()


@pytest.mark.parametrize(("block", "status", "error"), [
    ({"type": "refusal", "refusal": "private refusal"}, "ABSTAIN", "OPENAI_MODEL_REFUSAL"),
    ({"type": "output_text", "text": "not-json"}, "SCHEMA_ERROR", "OPENAI_OUTPUT_SCHEMA_MISMATCH"),
    ({"type": "output_text", "text": '{"unexpected":"yes"}'}, "SCHEMA_ERROR", "OPENAI_OUTPUT_SCHEMA_MISMATCH"),
    ({"type": "output_text", "text": '{"explanation":"ok","source_refs":[]}'}, "SCHEMA_ERROR", "OPENAI_OUTPUT_SCHEMA_MISMATCH"),
    ({"type": "output_text", "text": '{"explanation":"ok","explanation":"bad","source_refs":[],"uncertainty":null}'}, "SCHEMA_ERROR", "OPENAI_OUTPUT_SCHEMA_MISMATCH"),
    ({"type": "new_unrecognized_block", "text": "hello"}, "SCHEMA_ERROR", "OPENAI_OUTPUT_BLOCK_UNSUPPORTED"),
])
def test_refusal_bad_json_and_unknown_blocks_are_explicit(monkeypatch, block, status, error):
    payload = response_payload()
    payload["output"][1]["content"] = [block]
    receipt, _ = generate(monkeypatch, payload)
    assert receipt.status == status and receipt.error_code == error
    assert "private refusal" not in receipt.model_dump_json()


def test_requested_alias_is_not_misreported_as_observed_snapshot(monkeypatch):
    receipt, _ = generate(monkeypatch, model_request=request(expected_model_id="different-snapshot"))
    assert receipt.status == "PROVIDER_ERROR" and receipt.error_code == "OPENAI_RESPONSE_MODEL_MISMATCH"
    assert receipt.observed_model_id == "gpt-6-astra-snapshot"


@pytest.mark.parametrize("field", ["temperature", "seed", "allowed_tool_ids", "model_version"])
def test_unsupported_parameters_are_rejected_before_sending(field):
    with pytest.raises(ValidationError):
        request(**{field: 1})


def test_mutated_projection_never_reaches_transport(monkeypatch):
    incoming = request()
    incoming.input_projections[0].content["after"]["price_band"] = "tampered"
    receipt, calls = generate(monkeypatch, model_request=incoming)
    assert receipt.status == "NOT_RUN" and receipt.error_code == "OPENAI_REQUEST_CONTRACT_INVALID"
    assert calls == []


@pytest.mark.parametrize("override", [{"domain_id": "finance"}, {"object_ids": ("other",)},
                                      {"input_projections": ()}, {"prompt_template": "drift"},
                                      {"max_output_tokens": 32769}])
def test_missing_inputs_and_scope_or_prompt_drift_are_rejected(override):
    with pytest.raises(ValidationError):
        request(**override)


def test_schema_drift_is_not_silently_sent(monkeypatch):
    receipt, calls = generate(monkeypatch, model_request=request(schema_digest=sha256_digest({"wrong": 1})))
    assert receipt.status == "NOT_RUN" and calls == []


def test_credentials_missing_is_not_run(monkeypatch):
    monkeypatch.delenv("ORGREBASE_OPENAI_API_KEY", raising=False)
    receipt = OpenAIResponsesProvider().generate_structured(request=request(), output_model=Candidate)
    assert receipt.status == "NOT_RUN" and receipt.error_code == "OPENAI_CREDENTIALS_MISSING"
    assert receipt.dispatch_state == "NOT_SENT" and receipt.evidence_class == "NOT_RUN"
    assert receipt.input_tokens is None and receipt.output_tokens is None


@pytest.mark.parametrize("status", [302, 400, 401, 429, 500, 503])
def test_http_errors_keep_request_identity_and_do_not_retry(monkeypatch, status):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(status, headers={"x-request-id": "error:request", "location": "https://unsafe.example"},
                              text="private source data and credentials")

    receipt = OpenAIResponsesProvider(transport=httpx.MockTransport(handler)).generate_structured(
        request=request(), output_model=Candidate,
    )
    assert len(calls) == 1
    assert receipt.status == "PROVIDER_ERROR" and receipt.error_code == f"OPENAI_HTTP_{status}"
    assert receipt.provider_request_id == "error:request"
    assert "private" not in receipt.model_dump_json()


def test_timeout_does_not_fallback_and_preserves_unknown_cost(monkeypatch):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")
    calls = []

    def handler(incoming):
        calls.append(incoming)
        raise httpx.ReadTimeout("sensitive details")

    receipt = OpenAIResponsesProvider(transport=httpx.MockTransport(handler)).generate_structured(
        request=request(), output_model=Candidate,
    )
    assert len(calls) == 1
    assert receipt.error_code == "OPENAI_TIMEOUT" and receipt.status == "PROVIDER_ERROR"
    assert receipt.dispatch_state == "SENT_UNKNOWN" and receipt.evidence_class == "MODEL_ATTEMPT"
    assert receipt.input_tokens is None and receipt.output_tokens is None
    assert "sensitive" not in receipt.model_dump_json()


def test_pre_cancel_and_late_response_do_not_produce_a_candidate(monkeypatch):
    receipt, calls = generate(monkeypatch, is_cancelled=lambda: True)
    assert receipt.status == "CANCELLED" and calls == []
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")
    cancelled = False

    def handler(_):
        nonlocal cancelled
        cancelled = True
        return httpx.Response(200, json=response_payload(), headers={"x-request-id": "cancelled:request"})

    receipt = OpenAIResponsesProvider(transport=httpx.MockTransport(handler), is_cancelled=lambda: cancelled).generate_structured(
        request=request(), output_model=Candidate,
    )
    assert receipt.status == "CANCELLED" and receipt.value is None
    assert receipt.provider_request_id == "cancelled:request"


def test_request_and_response_byte_limits(monkeypatch):
    receipt, calls = generate(monkeypatch, max_request_bytes=32)
    assert receipt.error_code == "OPENAI_REQUEST_TOO_LARGE" and calls == []
    receipt, calls = generate(monkeypatch, max_response_bytes=32)
    assert receipt.error_code == "OPENAI_RESPONSE_TOO_LARGE" and len(calls) == 1


def test_configuration_binding_contains_no_secret(monkeypatch):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "secret-do-not-export")
    binding = OpenAIResponsesProvider().configuration_binding
    assert binding["automatic_retries"] == 0 and binding["store"] is False
    assert "secret-do-not-export" not in json.dumps(binding)


def test_prerequisites_checked_before_reserving_attempt(monkeypatch):
    monkeypatch.delenv("ORGREBASE_OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_CREDENTIALS_MISSING"):
        OpenAIResponsesProvider().require_available()
    OpenAIResponsesProvider(transport=httpx.MockTransport(lambda _: httpx.Response(200))).require_available()


def test_compressed_response_is_not_expanded_past_the_byte_bound(monkeypatch):
    import gzip

    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")

    def handler(_):
        return httpx.Response(200, stream=httpx.ByteStream(gzip.compress(b"x" * 10000)),
                              headers={"content-encoding": "gzip", "x-request-id": "compressed:request"})

    receipt = OpenAIResponsesProvider(transport=httpx.MockTransport(handler)).generate_structured(
        request=request(), output_model=Candidate,
    )
    assert receipt.error_code == "OPENAI_RESPONSE_ENCODING_UNSUPPORTED" and receipt.value is None


@pytest.mark.parametrize("failure", ["body_timeout", "http_error"])
def test_received_response_without_id_is_an_attempt_with_unknown_cost(monkeypatch, failure):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")
    accepted = []

    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"id":'
            raise httpx.ReadTimeout("response lost after provider accepted input")

    def handler(incoming):
        accepted.append(json.loads(incoming.content))
        if failure == "body_timeout":
            return httpx.Response(200, stream=InterruptedBody())
        return httpx.Response(503)

    receipt = OpenAIResponsesProvider(transport=httpx.MockTransport(handler)).generate_structured(
        request=request(), output_model=Candidate,
    )
    assert len(accepted) == 1
    assert receipt.status == "PROVIDER_ERROR" and receipt.dispatch_state == "RESPONSE_RECEIVED"
    assert receipt.evidence_class == "MODEL_ATTEMPT" and receipt.provider_request_id is None
    assert receipt.error_code == ("OPENAI_TIMEOUT" if failure == "body_timeout" else "OPENAI_HTTP_503")
    assert receipt.input_tokens is None and receipt.output_tokens is None and receipt.value is None
    forged = receipt.model_dump(exclude={"digest"})
    forged["evidence_class"] = "NOT_RUN"
    with pytest.raises(ValidationError, match="MODEL_DISPATCHED_ATTEMPT_CANNOT_BE_NOT_RUN"):
        ModelResponseReceiptV2.model_validate(forged)


def test_real_transport_requires_a_price_contract_before_process_start(monkeypatch):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "test-protocol-key")
    provider = OpenAIResponsesProvider()
    with pytest.raises(ValueError, match="MODEL_PRICE_CONTRACT_REQUIRED"):
        provider.require_available()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("process must not start"))
    receipt = provider.generate_structured(request=request(), output_model=Candidate)
    assert receipt.status == "NOT_RUN" and receipt.dispatch_state == "NOT_SENT"
    assert receipt.error_code == "MODEL_PRICE_CONTRACT_REQUIRED"


@pytest.mark.parametrize("received_headers", [False, True])
def test_parent_deadline_stops_a_blocked_real_process(monkeypatch, tmp_path, received_headers):
    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "private-test-key-not-in-command")
    child = tmp_path / "blocked_http.py"
    child.write_text(
        "import sys, time\nsys.stdin.buffer.read()\n"
        "print('{\"stage\":\"SENT_UNKNOWN\"}', flush=True)\n"
        + ("print('{\"stage\":\"RESPONSE_RECEIVED\",\"request_id\":\"req:before-timeout\"}', flush=True)\n"
           if received_headers else "")
        + "time.sleep(30)\n"
    )
    original = subprocess.Popen
    processes = []

    def start(command, **kwargs):
        assert "private-test-key" not in repr(command)
        assert kwargs["env"] == {} and kwargs["close_fds"]
        process = original([*command[:-1], str(child)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    started = time.monotonic()
    provider = OpenAIResponsesProvider(timeout_seconds=5, model_budget=budget())
    receipt = provider.generate_structured(request=request(), output_model=Candidate,
                                           deadline_monotonic=started + 0.5)
    assert time.monotonic() - started < 1.5
    assert len(processes) == 1 and processes[0].poll() is not None
    assert receipt.error_code == "OPENAI_DEADLINE_EXCEEDED" and receipt.value is None
    assert receipt.dispatch_state == ("RESPONSE_RECEIVED" if received_headers else "SENT_UNKNOWN")
    assert receipt.provider_request_id == ("req:before-timeout" if received_headers else None)
    assert receipt.evidence_class == "MODEL_ATTEMPT"
    assert receipt.input_tokens is None and receipt.output_tokens is None


def test_expired_deadline_never_dispatches(monkeypatch):
    calls = []
    provider = OpenAIResponsesProvider(transport=httpx.MockTransport(lambda req: calls.append(req)))
    receipt = provider.generate_structured(request=request(), output_model=Candidate,
                                           deadline_monotonic=time.monotonic() - 1)
    assert calls == [] and receipt.dispatch_state == "NOT_SENT"
    assert receipt.error_code == "OPENAI_DEADLINE_EXCEEDED"


@pytest.mark.parametrize("tier", [None, "priority", "fast", "auto"])
def test_priced_response_cannot_silently_use_another_service_tier(monkeypatch, tier):
    receipt, _ = generate(monkeypatch, response_payload(model="gpt-6-astra", service_tier=tier),
                          model_budget=budget())
    assert receipt.status == "PROVIDER_ERROR"
    assert receipt.error_code == "OPENAI_RESPONSE_SERVICE_TIER_MISMATCH"


def test_explicit_default_tier_and_exact_priced_model_can_produce_candidate(monkeypatch):
    receipt, _ = generate(monkeypatch, response_payload(model="gpt-6-astra", service_tier="default"),
                          model_budget=budget())
    assert receipt.status == "VALID"


@pytest.mark.parametrize("usage", [
    {"input_tokens": 100001, "output_tokens": 1},
    {"input_tokens": 1, "output_tokens": 513},
])
def test_usage_outside_the_price_contract_cannot_be_accepted(monkeypatch, usage):
    receipt, _ = generate(monkeypatch, response_payload(model="gpt-6-astra", service_tier="default", usage=usage),
                          model_budget=budget())
    assert receipt.error_code == "MODEL_PRICE_USAGE_LIMIT_EXCEEDED"
    assert receipt.input_tokens == usage["input_tokens"] and receipt.output_tokens == usage["output_tokens"]
    assert receipt.value is None


def test_worker_self_deadline_does_not_depend_on_parent_termination(monkeypatch, tmp_path):
    from orgrebase.workspace import openai_http_worker

    monkeypatch.setenv("ORGREBASE_OPENAI_API_KEY", "private-test-key")
    child = tmp_path / "worker_watchdog.py"
    child.write_text(
        "import importlib.util, io, json, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('http_worker', {openai_http_worker.__file__!r})\n"
        "worker = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(worker)\n"
        "def blocked(**kwargs):\n"
        "    kwargs['observe']({'stage': 'SENT_UNKNOWN'})\n"
        "    time.sleep(30)\n"
        "worker.exchange = blocked\n"
        "wire = json.load(sys.stdin)\nwire['deadline_monotonic'] = time.monotonic() + 0.25\n"
        "sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(wire).encode()))\nworker.main()\n"
    )
    original = subprocess.Popen
    processes = []

    def start(command, **kwargs):
        process = original([*command[:-1], str(child)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    started = time.monotonic()
    receipt = OpenAIResponsesProvider(timeout_seconds=5, model_budget=budget()).generate_structured(
        request=request(), output_model=Candidate)
    assert time.monotonic() - started < 1.5
    assert processes[0].returncode == 124
    assert receipt.error_code == "OPENAI_DEADLINE_EXCEEDED"
    assert receipt.dispatch_state == "SENT_UNKNOWN" and receipt.input_tokens is None
