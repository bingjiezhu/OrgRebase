"""Protocol fixtures only: these tests make no DeepSeek API call."""

from __future__ import annotations

import json

import httpx2 as httpx
import pytest
from pydantic import BaseModel, ConfigDict

from orgrebase.digest import sha256_digest
from orgrebase.process_policy import candidate_environment
from orgrebase.workspace.deepseek_provider import ENDPOINT, DeepSeekStructuredProvider
from orgrebase.workspace.models import ModelRequest


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str


def request(**changes):
    values = dict(
        request_id="r",
        run_id="run",
        task_ref="task",
        actor_id="agent",
        purpose="candidate",
        schema_name="Candidate",
        schema_digest=sha256_digest(Candidate.model_json_schema()),
        context_refs=(),
        input_refs=(),
        allowed_tool_ids=(),
        provider="deepseek",
        model_id="deepseek-flash",
        model_version="deepseek-flash",
        prompt_template_ref="p",
        prompt_template_digest=sha256_digest({}),
        temperature=0,
        seed=None,
        max_output_tokens=128,
        attempt=0,
    )
    return ModelRequest(**{**values, **changes})


def response(**changes):
    return {
        "id": "provider-1",
        "model": "deepseek-flash",
        "choices": [{"message": {"content": '{"verdict":"REPLAN"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        **changes,
    }


def provider(monkeypatch, handler):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    return DeepSeekStructuredProvider(
        prompt_payload={"case": "synthetic"}, transport=httpx.MockTransport(handler)
    )


def test_json_object_wire_and_local_schema(monkeypatch):
    def handle(req):
        assert str(req.url) == ENDPOINT
        body = json.loads(req.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"} and "seed" not in body
        assert "JSON schema" in body["messages"][0]["content"]
        return httpx.Response(200, json=response())

    p = provider(monkeypatch, handle)
    r = p.generate_structured(request=request(), output_model=Candidate)
    assert r.status == "VALID" and r.value == {"verdict": "REPLAN"} and r.input_tokens == 12
    assert p.runtime_binding["model_binding"] == "UNPINNED_PROVIDER_ALIAS"
    assert "private-test-key" not in r.model_dump_json() + json.dumps(p.runtime_binding)


@pytest.mark.parametrize(
    "body,error",
    [
        (response(choices=[{"message": {"content": ""}, "finish_reason": "stop"}]), "DEEPSEEK_OUTPUT_EMPTY"),
        (
            response(choices=[{"message": {"content": "{}"}, "finish_reason": "length"}]),
            "DEEPSEEK_OUTPUT_INCOMPLETE",
        ),
        (
            response(choices=[{"message": {"content": '{"other":true}'}, "finish_reason": "stop"}]),
            "DEEPSEEK_SCHEMA_MISMATCH",
        ),
        (response(id=None), "DEEPSEEK_RESPONSE_IDENTITY_MISSING"),
    ],
)
def test_invalid_output_cannot_be_valid(monkeypatch, body, error):
    p = provider(monkeypatch, lambda _: httpx.Response(200, json=body))
    r = p.generate_structured(request=request(), output_model=Candidate)
    assert r.status != "VALID" and r.error_code == error and r.value is None


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_error_no_redirect_or_error_body_disclosure(monkeypatch, status):
    calls = []

    def handle(req):
        calls.append(str(req.url))
        return httpx.Response(
            status, text="private-test-key PRIVATE BODY", headers={"location": "https://other.example"}
        )

    p = provider(monkeypatch, handle)
    r = p.generate_structured(request=request(), output_model=Candidate)
    assert r.status == "PROVIDER_ERROR" and calls == [ENDPOINT]
    assert "PRIVATE" not in r.model_dump_json() and "private-test-key" not in r.model_dump_json()


def test_missing_credentials_no_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    p = DeepSeekStructuredProvider(prompt_payload={})
    assert p.generate_structured(request=request(), output_model=Candidate).status == "NOT_RUN"


@pytest.mark.parametrize(
    "changes",
    [
        {"model_id": "deepseek-4.1-flash"},
        {"schema_digest": "sha256:" + "0" * 64},
        {"seed": 42},
        {"allowed_tool_ids": ("write",)},
    ],
)
def test_binding_error_before_dispatch(monkeypatch, changes):
    def denied(req):
        raise AssertionError("must not send")

    r = provider(monkeypatch, denied).generate_structured(request=request(**changes), output_model=Candidate)
    assert r.status in {"NOT_RUN", "SCHEMA_ERROR"}


def test_key_only_reaches_selected_reviewer(tmp_path):
    env = {"DEEPSEEK_API_KEY": "s", "ORGREBASE_VERTEX_API_KEY": "v", "DATABASE_URL": "private"}
    assert candidate_environment(env, home=tmp_path, model_provider="deepseek")["DEEPSEEK_API_KEY"] == "s"
    assert "DEEPSEEK_API_KEY" not in candidate_environment(env, home=tmp_path, model_provider="vertex-ai")
    assert "DEEPSEEK_API_KEY" not in candidate_environment(env, home=tmp_path)
    assert "ORGREBASE_VERTEX_API_KEY" not in candidate_environment(
        env, home=tmp_path, model_provider="deepseek"
    )


def test_native_reviewer_keeps_deterministic_authority_and_auditable_attempt(monkeypatch):
    from orgrebase.workspace import competition_worker
    from orgrebase.workspace.competition_run import _reviewer_model_attempt_evidence
    from scripts import finalize_golden_pilot_evidence, verify_golden_pilot_evidence
    from tests.workspace.test_competition_worker_review_boundaries import _review_input

    payload = _review_input(1)
    payload["model_provider"] = "deepseek"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")

    def factory(**kwargs):
        body = response(
            choices=[
                {
                    "message": {
                        "content": json.dumps(
                            {"verdict": "PASS", "missing_domains": [], "reason_codes": ["ADVISORY_ONLY"]}
                        )
                    },
                    "finish_reason": "stop",
                }
            ]
        )
        return DeepSeekStructuredProvider(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
        )

    monkeypatch.setattr(competition_worker, "DeepSeekStructuredProvider", factory)
    result = competition_worker.review_domain_results(payload)
    assert result["status"] == "PASS" and result["model_advisory"]["verdict"] == "PASS"
    assert result["decision"]["verdict"] == "REPLAN" and result["model_advisory_accepted"] is False
    assert result["candidate_only"] is True and result["target_writes"] == 0
    attempt = _reviewer_model_attempt_evidence(reviewer_input=payload, reviewer_result=result, model_provider="deepseek")
    for module in (finalize_golden_pilot_evidence, verify_golden_pilot_evidence):
        assert module._deepseek_binding_valid(
            result["model_receipt"], result["model_runtime_binding"], attempt
        )
        wrong = {**attempt, "provider_request_id": "different"}
        assert not module._deepseek_binding_valid(
            result["model_receipt"], result["model_runtime_binding"], wrong
        )
    assert "private-test-key" not in json.dumps(result)


def test_provider_reuse_clears_previous_response_identity(monkeypatch):
    p = provider(monkeypatch, lambda _: httpx.Response(200, json=response()))
    assert p.generate_structured(request=request(), output_model=Candidate).status == "VALID"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert p.generate_structured(request=request(), output_model=Candidate).status == "NOT_RUN"
    assert "observed_model_version" not in p.runtime_binding


@pytest.mark.parametrize("phase,expected_status,expected_calls", [("INTENT", "NOT_RUN", 0), ("RESULT", "PROVIDER_ERROR", 1)])
def test_observation_write_failure_cannot_admit_a_candidate(tmp_path, monkeypatch, phase, expected_status, expected_calls):
    from orgrebase.workspace import model_observations

    writes, calls = [], []
    original = model_observations._write_record

    def write(directory, record):
        writes.append(record.phase)
        if record.phase == phase:
            raise OSError("controlled audit disk failure")
        return original(directory, record)

    def handle(req):
        calls.append(str(req.url))
        return httpx.Response(200, json=response())

    monkeypatch.setattr(model_observations, "_write_record", write)
    p = provider(monkeypatch, handle)
    p.observer = model_observations.ModelAttemptObserver(tmp_path)
    receipt = p.generate_structured(request=request(), output_model=Candidate)
    assert receipt.status == expected_status and receipt.value is None and receipt.schema_valid is False
    assert receipt.error_code == f"MODEL_OBSERVATION_{phase}_WRITE_FAILED"
    assert len(calls) == expected_calls and writes == ["INTENT", "RESULT"]
    assert p.runtime_binding["status"] == expected_status
    assert p.observer.summary()["records"][0]["persistence"] == "WRITE_FAILED"
    if phase == "RESULT":
        retained = list(tmp_path.glob("*.json"))
        assert len(retained) == 1 and retained[0].name.endswith(".intent.json")


@pytest.mark.parametrize("observed", ["deepseek-v4-pro", "deepseek-4.1-flash", "deepseek-flash-unqualified"])
def test_different_response_model_is_not_accepted(monkeypatch, observed):
    p = provider(monkeypatch, lambda _: httpx.Response(200, json=response(model=observed)))
    r = p.generate_structured(request=request(), output_model=Candidate)
    assert r.status == "PROVIDER_ERROR" and r.error_code == "DEEPSEEK_RESPONSE_MODEL_MISMATCH"
    assert r.value is None
