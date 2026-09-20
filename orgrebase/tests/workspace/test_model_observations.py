from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace import model_observations
from orgrebase.workspace.competition_worker import OLLAMA_MODEL_DIGEST, ReviewerDecision
from orgrebase.workspace.model_observations import (
    ModelAttemptObservation,
    ModelAttemptObserver,
    provider_observations,
    read_model_attempts,
)
from orgrebase.workspace.model_provider import (
    VERTEX_MODEL_ID,
    LiveHTTPModelProvider,
    LocalOllamaStructuredProvider,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.models import ModelRequest

ADVISORY = {"verdict": "PASS", "missing_domains": [], "reason_codes": ["SYNTHETIC"]}
SECRET = "test-token-never-recorded"
PRIVATE_PROMPT = "private synthetic prompt never recorded"


def _request(provider="vertex-ai"):
    model = VERTEX_MODEL_ID if provider == "vertex-ai" else "qwen2.5:3b"
    return ModelRequest(
        request_id="request:usage",
        run_id="run:usage",
        task_ref="task:usage",
        actor_id="reviewer",
        purpose="candidate_review",
        schema_name="ReviewerDecision",
        schema_digest=sha256_digest(ReviewerDecision.model_json_schema(mode="validation")),
        context_refs=(),
        input_refs=(),
        allowed_tool_ids=(),
        provider=provider,
        model_id=model,
        model_version=(f"ollama-manifest:{OLLAMA_MODEL_DIGEST}" if provider == "ollama-local" else model),
        prompt_template_ref="prompt:usage@v1",
        prompt_template_digest="sha256:" + "1" * 64,
        temperature=0.0,
        seed=None,
        max_output_tokens=2048,
        attempt=0,
    )


class Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload, ensure_ascii=False).encode()
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self):
        return self.raw


def _provider(kind, observer, monkeypatch):
    if kind == "vertex-ai":
        return VertexAIStructuredProvider(
            prompt_payload={"private": PRIVATE_PROMPT},
            project_id="test-project1",
            access_token=SECRET,
            observer=observer,
        )
    if kind == "ollama-local":
        return LocalOllamaStructuredProvider(prompt_payload={"private": PRIVATE_PROMPT}, observer=observer)
    monkeypatch.setenv("ORGREBASE_MODEL_ENDPOINT", "https://example.invalid/model")
    monkeypatch.setenv("ORGREBASE_MODEL_API_KEY", SECRET)
    return LiveHTTPModelProvider(observer=observer)


def _payload(kind, usage, *, content=ADVISORY):
    if kind == "vertex-ai":
        return {
            "responseId": "fixture-request-1",
            "modelVersion": VERTEX_MODEL_ID,
            "candidates": [{"content": {"parts": [{"text": json.dumps(content)}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                name: value
                for name, value in zip(("promptTokenCount", "candidatesTokenCount"), usage, strict=False)
            },
        }
    if kind == "ollama-local":
        return {
            "model": "qwen2.5:3b",
            "message": {"content": json.dumps(content)},
            **{name: value for name, value in zip(("prompt_eval_count", "eval_count"), usage, strict=False)},
        }
    return {
        "id": "fixture-request-1",
        "model": "qwen2.5:3b",
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {
            name: value for name, value in zip(("prompt_tokens", "completion_tokens"), usage, strict=False)
        },
    }


def _http(monkeypatch, payload, observer):
    calls = []

    def urlopen(request, *, timeout):
        if request.get_method() == "GET":
            return Response({"models": [{"name": "qwen2.5:3b", "digest": OLLAMA_MODEL_DIGEST}]})
        if observer.directory is not None:
            intents = list(observer.directory.glob("*.intent.json"))
            assert intents, "durable intent must precede the physical POST"
            latest = json.loads(max(intents, key=lambda path: path.stat().st_mtime_ns).read_text())
            assert latest["request_body_digest"] == "sha256:" + hashlib.sha256(request.data).hexdigest()
            assert latest["request_body_bytes"] == len(request.data)
        calls.append(request.data)
        return Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


@pytest.mark.parametrize("kind", ["vertex-ai", "ollama-local", "openai-compatible"])
@pytest.mark.parametrize(
    "usage,status,expected,invalid",
    [
        ([], "UNAVAILABLE", (None, None), []),
        ([0, 0], "REPORTED", (0, 0), []),
        ([7], "PARTIAL", (7, None), []),
        ([False, "8"], "UNAVAILABLE", (None, None), ["input_tokens", "output_tokens"]),
        ([-1, 8], "PARTIAL", (None, 8), ["input_tokens"]),
    ],
)
def test_provider_usage_distinguishes_missing_zero_partial_and_invalid(
    monkeypatch, tmp_path, kind, usage, status, expected, invalid
):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    payload = _payload(kind, usage)
    calls = _http(monkeypatch, payload, observer)
    receipt = _provider(kind, observer, monkeypatch).generate_structured(
        request=_request(kind), output_model=ReviewerDecision
    )
    assert receipt.status == "VALID"
    assert len(calls) == 1
    collection = read_model_attempts(observer.directory.resolve())
    record = collection["records"][0]
    assert collection["coverage"] == "DURABLE"
    assert record["response_receipt_digest"] == receipt.digest
    assert record["response_body_digest"] == "sha256:" + hashlib.sha256(Response(payload).raw).hexdigest()
    assert record["usage"]["status"] == status
    assert (record["usage"]["input_tokens"], record["usage"]["output_tokens"]) == expected
    assert record["usage"]["invalid_fields"] == invalid
    assert record["legacy_receipt_usage_is_cost_evidence"] is False
    for path in observer.directory.glob("*.json"):
        text = path.read_text()
        assert SECRET not in text and PRIVATE_PROMPT not in text
        assert "Authorization" not in text and "messages" not in text
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["vertex-ai", "ollama-local", "openai-compatible"])
def test_schema_failure_keeps_provider_usage_and_never_retries(monkeypatch, tmp_path, kind):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    calls = _http(monkeypatch, _payload(kind, [17, 9], content={"wrong": "schema"}), observer)
    receipt = _provider(kind, observer, monkeypatch).generate_structured(
        request=_request(kind), output_model=ReviewerDecision
    )
    assert receipt.status == "SCHEMA_ERROR" and len(calls) == 1
    if kind == "openai-compatible":
        assert receipt.input_tokens == receipt.output_tokens == 0  # frozen V1 wire behavior
    record = read_model_attempts(observer.directory.resolve())["records"][0]
    assert record["response_state"] == "SCHEMA_ERROR"
    assert record["usage"]["input_tokens"] == 17
    assert record["usage"]["output_tokens"] == 9


@pytest.mark.parametrize("kind", ["vertex-ai", "openai-compatible"])
@pytest.mark.parametrize(
    "request_id",
    ["_0-uapnzIP6cvdAPuOHHgAQ", "-opaque_request-1", "request:legacy/v1.2", "a" * 256],
)
def test_opaque_provider_request_id_survives_durable_receipt_binding(
    monkeypatch, tmp_path, kind, request_id
):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    payload = _payload(kind, [17, 9])
    payload["responseId" if kind == "vertex-ai" else "id"] = request_id
    calls = _http(monkeypatch, payload, observer)
    receipt = _provider(kind, observer, monkeypatch).generate_structured(
        request=_request(kind), output_model=ReviewerDecision
    )

    assert receipt.status == "VALID" and len(calls) == 1
    collection = read_model_attempts(observer.directory.resolve())
    assert collection["coverage"] == "DURABLE"
    record = collection["records"][0]
    assert record["provider_request_id"] == receipt.provider_request_id == request_id
    assert record["response_receipt_digest"] == receipt.digest
    assert record["request_digest"] == receipt.request_digest
    assert record["response_body_digest"] == "sha256:" + hashlib.sha256(Response(payload).raw).hexdigest()
    assert record["observed_model"] == receipt.model_id
    assert observer.summary()["records"] == collection["records"]


@pytest.mark.parametrize(
    "invalid_identifier",
    [
        None, False, 42, [], "", "a" * 257, "request\n", "request\r", "request\x00",
        "request\x1b", "request\t", " request", "request id", "请求", ".request", ":request", "/request",
    ],
)
def test_observation_metadata_rejects_unsafe_or_unbounded_identifiers(tmp_path, invalid_identifier):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    with observer.observe(_request()) as attempt:
        attempt.metadata(
            usage=model_observations.ModelUsage(status="UNAVAILABLE", basis="unavailable"),
            observed_model=invalid_identifier,
            provider_request_id=invalid_identifier,
        )

    record = json.loads(next(observer.directory.glob("*.result.json")).read_text())
    assert record["observed_model"] is None
    assert record["provider_request_id"] is None


def test_intent_failure_refuses_dispatch_and_result_failure_keeps_unknown(monkeypatch, tmp_path):
    write = model_observations._write_record
    for failed_phase in ("INTENT", "RESULT"):
        observer = ModelAttemptObserver(tmp_path / failed_phase)
        calls = []
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda *args, _calls=calls, **kwargs: (
                _calls.append(1) or Response(_payload("vertex-ai", [17, 9]))
            ),
        )

        def failing_write(directory, record, phase=failed_phase):
            if record.phase == phase:
                raise OSError("private disk failure details")
            write(directory, record)

        monkeypatch.setattr(model_observations, "_write_record", failing_write)
        provider = _provider("vertex-ai", observer, monkeypatch)
        receipt = provider.generate_structured(
            request=_request(), output_model=ReviewerDecision
        )
        assert len(calls) == (0 if failed_phase == "INTENT" else 1)
        assert receipt.status == ("NOT_RUN" if failed_phase == "INTENT" else "PROVIDER_ERROR")
        assert receipt.error_code == f"MODEL_OBSERVATION_{failed_phase}_WRITE_FAILED"
        assert receipt.value is None and receipt.schema_valid is False
        assert receipt.evidence_class == "NOT_RUN"
        assert provider.runtime_binding["status"] == receipt.status
        assert provider.runtime_binding["error_code"] == receipt.error_code
        retry = provider.runtime_binding["transport_retry"]
        assert retry["stop_reason"] == "OBSERVATION_DURABILITY_LOST"
        assert len(retry["attempts"]) == 1 and retry["successful_calls"] == 0
        assert retry["provider_attempts"] == (0 if failed_phase == "INTENT" else 1)
        assert retry["attempts"][0]["sent"] is (failed_phase == "RESULT")
        assert observer.summary()["coverage"] == "INCOMPLETE"
        collection = read_model_attempts(observer.directory.resolve())
        assert collection["coverage"] == "INCOMPLETE"
        assert len(collection["records"]) == 1
        record = collection["records"][0]
        assert record["dispatch_state"] == (
            "NOT_DISPATCHED" if failed_phase == "INTENT" else "DISPATCH_MAY_HAVE_OCCURRED"
        )
        assert record["response_state"] == ("NOT_RUN" if failed_phase == "INTENT" else "UNKNOWN")
        assert record["usage"]["input_tokens"] is None
        assert record["usage"]["output_tokens"] is None
        assert "private disk failure" not in json.dumps(record)


def test_intent_failure_is_preserved_when_result_persistence_also_fails(monkeypatch, tmp_path):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    phases = []

    def failing_write(directory, record):
        phases.append(record.phase)
        raise OSError("private disk failure details")

    monkeypatch.setattr(model_observations, "_write_record", failing_write)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("intent failure must not send")
    )
    provider = _provider("vertex-ai", observer, monkeypatch)
    receipt = provider.generate_structured(request=_request(), output_model=ReviewerDecision)

    assert phases == ["INTENT", "RESULT"]
    assert receipt.status == "NOT_RUN"
    assert receipt.error_code == "MODEL_OBSERVATION_INTENT_WRITE_FAILED"
    assert receipt.value is None and receipt.schema_valid is False
    assert provider.runtime_binding["error_code"] == receipt.error_code
    retry = provider.runtime_binding["transport_retry"]
    assert len(retry["attempts"]) == 1 and retry["provider_attempts"] == 0
    assert retry["stop_reason"] == "OBSERVATION_DURABILITY_LOST"
    summary = observer.summary()
    assert summary["coverage"] == "INCOMPLETE" and len(summary["records"]) == 1
    record = summary["records"][0]
    assert record["dispatch_state"] == "NOT_DISPATCHED"
    assert record["persistence"] == "WRITE_FAILED"
    assert record["error_code"] == receipt.error_code
    assert "private disk failure" not in json.dumps(summary)


def test_same_request_concurrent_calls_keep_independent_dispatch_identity(monkeypatch, tmp_path):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *args, **kwargs: Response(_payload("vertex-ai", [17, 9]))
    )
    provider = _provider("vertex-ai", observer, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _: provider.generate_structured(request=_request(), output_model=ReviewerDecision),
                range(2),
            )
        )
    assert all(receipt.status == "VALID" for receipt in receipts)
    records = read_model_attempts(observer.directory.resolve())["records"]
    assert len(records) == 2 and len({record["dispatch_id"] for record in records}) == 2
    assert {record["request_attempt"] for record in records} == {0}
    assert len(list(observer.directory.glob("*.json"))) == 4


def test_process_termination_at_http_dispatch_leaves_unknown_intent(tmp_path):
    directory = (tmp_path / "attempts").resolve()
    script = """import json, os, sys, urllib.request
from orgrebase.workspace.competition_worker import ReviewerDecision
from orgrebase.workspace.model_observations import ModelAttemptObserver
from orgrebase.workspace.model_provider import VertexAIStructuredProvider
from orgrebase.workspace.models import ModelRequest
urllib.request.urlopen = lambda *args, **kwargs: os._exit(19)
provider = VertexAIStructuredProvider(prompt_payload={}, project_id="test-project1", access_token="synthetic", observer=ModelAttemptObserver(sys.argv[1]))
provider.generate_structured(request=ModelRequest.model_validate(json.loads(sys.argv[2])), output_model=ReviewerDecision)
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script, str(directory), _request().model_dump_json()],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 19, result.stderr.decode()
    collection = read_model_attempts(directory)
    assert collection["coverage"] == "INCOMPLETE"
    record = collection["records"][0]
    assert record["phase"] == "INTENT" and record["response_state"] == "UNKNOWN"
    assert record["dispatch_state"] == "DISPATCH_MAY_HAVE_OCCURRED"
    assert record["usage"]["input_tokens"] is None


def test_reader_rejects_pair_mismatch_and_does_not_complete_orphan_result(monkeypatch, tmp_path):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    _http(monkeypatch, _payload("vertex-ai", [1, 2]), observer)
    _provider("vertex-ai", observer, monkeypatch).generate_structured(
        request=_request(), output_model=ReviewerDecision
    )
    intent = next(observer.directory.glob("*.intent.json"))
    original = intent.read_text()
    value = json.loads(original)
    value.pop("digest")
    value["run_ref"] = "run:other"
    intent.write_text(ModelAttemptObservation.model_validate(value).model_dump_json())
    with pytest.raises(ValueError, match="INTENT_RESULT_MISMATCH"):
        read_model_attempts(observer.directory.resolve())
    intent.unlink()
    assert read_model_attempts(observer.directory.resolve())["coverage"] == "INCOMPLETE"


def test_reader_bounds_input_and_rejects_symlink_parent(tmp_path):
    directory = (tmp_path / "attempts").resolve()
    directory.mkdir()
    (directory / "large.json").write_bytes(b" " * 65_537)
    with pytest.raises(ValueError, match="SIZE_LIMIT"):
        read_model_attempts(directory)
    linked = tmp_path / "linked"
    linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="SYMLINK_DENIED"):
        read_model_attempts(linked)


@pytest.mark.parametrize("phase", ["intent", "result"])
@pytest.mark.parametrize("digest", [None, "", "sha256:" + "0" * 64])
def test_reader_requires_persisted_digest(monkeypatch, tmp_path, phase, digest):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    _http(monkeypatch, _payload("vertex-ai", [1, 2]), observer)
    _provider("vertex-ai", observer, monkeypatch).generate_structured(
        request=_request(), output_model=ReviewerDecision
    )
    path = next(observer.directory.glob(f"*.{phase}.json"))
    payload = json.loads(path.read_text())
    if digest is None:
        payload.pop("digest")
    else:
        payload["digest"] = digest
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="MODEL_OBSERVATION_DIGEST_MISMATCH"):
        read_model_attempts(observer.directory.resolve())


def test_unconfigured_observer_preserves_availability_but_not_durable_claim(monkeypatch):
    observer = ModelAttemptObserver()
    _http(monkeypatch, _payload("vertex-ai", []), observer)
    provider = _provider("vertex-ai", observer, monkeypatch)
    receipt = provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert receipt.status == "VALID"
    collection = provider_observations(provider, run_ref="run:usage")
    assert collection["coverage"] == "INCOMPLETE"
    assert collection["records"][0]["persistence"] == "NON_DURABLE"
    assert provider_observations(object(), run_ref="run:usage")["records"] == []


@pytest.mark.parametrize("kind", ["vertex-ai", "ollama-local", "openai-compatible"])
def test_transport_error_keeps_possible_dispatch_and_unknown_usage(monkeypatch, tmp_path, kind):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    calls = []

    def urlopen(request, **_kwargs):
        if request.get_method() == "GET":
            return Response({"models": [{"name": "qwen2.5:3b", "digest": OLLAMA_MODEL_DIGEST}]})
        calls.append(1)
        raise TimeoutError("private provider detail")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    _provider(kind, observer, monkeypatch).generate_structured(
        request=_request(kind), output_model=ReviewerDecision
    )
    record = read_model_attempts(observer.directory.resolve())["records"][0]
    assert len(calls) == 1
    assert record["dispatch_state"] == "DISPATCH_MAY_HAVE_OCCURRED"
    assert record["usage"]["status"] == "UNAVAILABLE"
    assert record["usage"]["input_tokens"] is None
    assert "private provider detail" not in json.dumps(record)


@pytest.mark.parametrize("kind", ["ollama-local", "openai-compatible"])
def test_non_object_json_retains_received_state_without_schema_exception(monkeypatch, tmp_path, kind):
    observer = ModelAttemptObserver(tmp_path / "attempts")
    calls = _http(monkeypatch, [], observer)
    receipt = _provider(kind, observer, monkeypatch).generate_structured(
        request=_request(kind), output_model=ReviewerDecision
    )
    assert len(calls) == 1 and receipt.status == "SCHEMA_ERROR"
    record = read_model_attempts(observer.directory.resolve())["records"][0]
    assert record["dispatch_state"] == "RESPONSE_RECEIVED"
    assert record["usage"]["input_tokens"] is None


def test_existing_schema_repair_records_each_physical_call(monkeypatch, tmp_path):
    from orgrebase.workspace.model_provider import BoundedSchemaRepairProvider

    observer = ModelAttemptObserver(tmp_path / "attempts")
    calls = _http(monkeypatch, _payload("vertex-ai", [7, 3], content={"wrong": "schema"}), observer)
    provider = BoundedSchemaRepairProvider(_provider("vertex-ai", observer, monkeypatch))
    receipt = provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert receipt.status == "ABSTAIN"
    records = read_model_attempts(observer.directory.resolve())["records"]
    assert len(calls) == len(records) == 3
    assert {record["request_attempt"] for record in records} == {0, 1, 2}
    assert sum(record["usage"]["input_tokens"] for record in records) == 21
    assert all(record["response_state"] == "SCHEMA_ERROR" for record in records)
