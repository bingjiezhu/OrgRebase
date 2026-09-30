"""Vertex candidate V3 protocol tests; every HTTP exchange is an in-memory substitute."""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import EvidenceClass, IntegrityError
from orgrebase.workspace.advisory import (
    DomainAdvisoryCandidate,
    WorkspaceApplyAdvisoryVerifier,
    WorkspaceChangeAdvisoryAdapter,
)
from orgrebase.workspace.model_budget import ModelBudget
from orgrebase.workspace.model_observations import ModelAttemptObserver
from orgrebase.workspace.model_provider import (
    VERTEX_CANDIDATE_MODEL_ID,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.models import (
    CandidateModelRequest,
    CandidateModelResponseReceipt,
    ModelInputProjection,
    ModelRequest,
    ModelRequestV2,
    ModelRequestV3,
    ModelResponseReceipt,
    ModelResponseReceiptV2,
    ModelResponseReceiptV3,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.vertex_candidate import (
    build_vertex_candidate_body,
    vertex_candidate_wire_digests,
)
from tests.workspace.test_change_advisory import NOW, contracts


class _Response:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self.body = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _budget() -> ModelBudget:
    return ModelBudget(
        schema_version="orgrebase.model-budget.v1",
        model_id=VERTEX_CANDIDATE_MODEL_ID,
        context_window_tokens=1_000_000,
        input_usd_per_million_ceiling="0.00000001",
        output_usd_per_million_ceiling="0.00000001",
        max_preview_usd="100.00000000",
        price_source_ref="test://vertex-candidate-price-ceiling",
        valid_until="2030-01-01T00:00:00Z",
    )


def _provider(*, observer: ModelAttemptObserver | None = None) -> VertexAIStructuredProvider:
    return VertexAIStructuredProvider(
        prompt_payload={"legacy_payload": "must-not-be-sent-for-v3"},
        project_id="orgrebase-test1",
        model_id=VERTEX_CANDIDATE_MODEL_ID,
        access_token="in-memory-test-token",
        adc_token_resolver=lambda: None,
        project_resolver=lambda: None,
        observer=observer,
        model_budget=_budget(),
    )


def _projection() -> ModelInputProjection:
    content = {
        "binding": {
            "issued_at": "2026-08-15T00:00:00Z",
            "expires_at": "2026-08-15T01:00:00Z",
        },
        "projection": {"source_ref": "rule:finance", "change": "after"},
    }
    return ModelInputProjection(
        ref="rule:finance@v1",
        source_digest="sha256:" + "1" * 64,
        domain_id="finance",
        object_ids=("rule:finance",),
        content=content,
        content_digest=sha256_digest(content),
    )


def _request_v3() -> ModelRequestV3:
    projection = _projection()
    projections = (projection,)
    prompt = "Explain only the admitted finance change."
    return ModelRequestV3(
        request_id="model-request:vertex-candidate:finance",
        tenant_id="tenant:test",
        workspace_id="workspace:test",
        run_id="run:vertex-candidate",
        nonce="nonce:vertex-candidate",
        task_ref="task:finance",
        actor_id="finance-steward",
        purpose="candidate_explanation",
        domain_id="finance",
        object_ids=("rule:finance",),
        input_projections=projections,
        projection_digest=sha256_digest(
            [item.model_dump(mode="json") for item in projections]
        ),
        schema_name=DomainAdvisoryCandidate.__name__,
        schema_digest=sha256_digest(
            DomainAdvisoryCandidate.model_json_schema(mode="validation")
        ),
        prompt_template_ref="workspace-domain-advisory@1",
        prompt_template=prompt,
        prompt_template_digest=sha256_digest(prompt),
        max_output_tokens=512,
    )


def _candidate(request: ModelRequestV3) -> dict[str, Any]:
    return {
        "domain_id": request.domain_id,
        "object_ids": list(request.object_ids),
        "source_refs": [item.ref for item in request.input_projections],
        "explanation": "Review the admitted finance change.",
    }


def _payload(
    request: ModelRequestV3,
    *,
    response_id: str = "_vertex-response-1",
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    payload = {
        "responseId": response_id,
        "modelVersion": VERTEX_CANDIDATE_MODEL_ID,
        "createTime": NOW,
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                _candidate(request),
                                separators=(",", ":"),
                            )
                        }
                    ]
                },
                "finishReason": "STOP",
            }
        ],
    }
    if usage is not None:
        payload["usageMetadata"] = usage
    return payload


def _replace(model, **updates):
    return type(model).model_validate(
        {**model.model_dump(mode="json", exclude={"digest"}), **updates}
    )


def test_vertex_v3_wire_is_provider_native_and_binds_each_surface() -> None:
    request = _request_v3()
    body = build_vertex_candidate_body(request, DomainAdvisoryCandidate)
    digests = vertex_candidate_wire_digests(request, DomainAdvisoryCandidate)

    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert body["generationConfig"]["maxOutputTokens"] == 512
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "text" not in body and "tools" not in body
    assert "legacy_payload" not in canonical_json(body)
    assert request.input_projections[0].ref in body["contents"][0]["parts"][0]["text"]
    assert set(digests) == {
        "projection_digest",
        "schema_digest",
        "wire_schema_digest",
        "prompt_digest",
        "body_digest",
    }
    assert all(value.startswith("sha256:") for value in digests.values())
    assert digests["schema_digest"] == digests["wire_schema_digest"]


def test_vertex_v3_missing_usage_stays_unknown_and_observation_is_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request_v3()
    observer = ModelAttemptObserver()
    observed: dict[str, Any] = {}

    def send(http_request: urllib.request.Request, *, timeout: float) -> _Response:
        observed["body"] = json.loads(http_request.data)
        observed["timeout"] = timeout
        return _Response(_payload(request))

    monkeypatch.setattr(urllib.request, "urlopen", send)
    provider = _provider(observer=observer)
    receipt = provider.generate_structured(
        request=request,
        output_model=DomainAdvisoryCandidate,
    )

    assert isinstance(receipt, ModelResponseReceiptV3)
    assert receipt.status == "VALID"
    assert receipt.provider == "vertex-ai"
    assert receipt.requested_model_id == VERTEX_CANDIDATE_MODEL_ID
    assert receipt.observed_model_id == VERTEX_CANDIDATE_MODEL_ID
    assert receipt.provider_request_id == "_vertex-response-1"
    assert (
        receipt.input_tokens,
        receipt.output_tokens,
        receipt.thinking_tokens,
        receipt.total_tokens,
        receipt.cached_tokens,
    ) == (None, None, None, None, None)
    assert receipt.body_digest == sha256_digest(observed["body"])
    assert 0 < observed["timeout"] <= provider.timeout_seconds
    record = observer.summary()["records"][0]
    assert record["request_digest"] == request.digest
    assert record["provider_request_id"] == "_vertex-response-1"
    assert record["observed_model"] == VERTEX_CANDIDATE_MODEL_ID
    assert record["usage"]["status"] == "UNAVAILABLE"
    assert record["usage"]["input_tokens"] is None


def test_vertex_v3_429_keeps_distinct_dispatches_and_shared_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request_v3()
    observer = ModelAttemptObserver()
    sends: list[float] = []

    def send(_http_request: urllib.request.Request, *, timeout: float) -> _Response:
        sends.append(timeout)
        if len(sends) == 1:
            raise urllib.error.HTTPError(
                "https://aiplatform.googleapis.com/model",
                429,
                "limited",
                {},
                io.BytesIO(
                    json.dumps(
                        {
                            "responseId": "vertex-rate-limit-1",
                            "modelVersion": VERTEX_CANDIDATE_MODEL_ID,
                        }
                    ).encode()
                ),
            )
        return _Response(
            _payload(
                request,
                response_id="vertex-response-after-retry",
                usage={"promptTokenCount": 9, "candidatesTokenCount": 4},
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", send)
    monkeypatch.setattr("orgrebase.workspace.model_provider.time.sleep", lambda _seconds: None)
    provider = _provider(observer=observer)
    receipt = provider.generate_structured(
        request=request,
        output_model=DomainAdvisoryCandidate,
    )

    assert receipt.status == "VALID"
    assert len(sends) == 2 and 0 < sends[1] <= sends[0]
    records = observer.summary()["records"]
    assert len(records) == 2
    assert len({item["dispatch_id"] for item in records}) == 2
    assert {item["request_digest"] for item in records} == {request.digest}
    assert records[0]["usage"]["status"] == "UNAVAILABLE"
    assert records[0]["usage"]["input_tokens"] is None
    assert records[0]["provider_request_id"] == "vertex-rate-limit-1"
    assert records[0]["observed_model"] == VERTEX_CANDIDATE_MODEL_ID
    assert records[1]["provider_request_id"] == "vertex-response-after-retry"
    assert provider.runtime_binding["transport_retry"]["provider_attempts"] == 2


@pytest.mark.parametrize(
    "field",
    ["projection_digest", "schema_digest", "wire_schema_digest", "prompt_digest", "body_digest"],
)
def test_vertex_v3_resealed_receipt_digest_tampering_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    request = _request_v3()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(_payload(request)),
    )
    receipt = _provider().generate_structured(
        request=request,
        output_model=DomainAdvisoryCandidate,
    )
    assert isinstance(receipt, ModelResponseReceiptV3)
    tampered = _replace(receipt, **{field: "sha256:" + "f" * 64})

    with pytest.raises(
        IntegrityError,
        match=r"WORKSPACE_ADVISORY_(MODEL_INCOMPLETE|PROVIDER_BINDING)",
    ):
        WorkspaceChangeAdvisoryAdapter._check_receipt(request, tampered)


def test_vertex_v3_request_discriminator_and_projection_digest_fail_closed() -> None:
    request = _request_v3()
    payload = request.model_dump(mode="json", exclude={"digest"})
    payload["provider"] = "openai-responses"
    with pytest.raises(ValidationError):
        ModelRequestV3.model_validate(payload)

    payload = request.model_dump(mode="json", exclude={"digest"})
    payload["input_projections"][0]["content"]["projection"]["change"] = "tampered"
    payload["input_projections"][0]["content_digest"] = sha256_digest(
        payload["input_projections"][0]["content"]
    )
    payload["input_projections"][0].pop("digest")
    with pytest.raises(ValidationError, match="MODEL_INPUT_PROJECTION_DIGEST_MISMATCH"):
        ModelRequestV3.model_validate(payload)

    payload = request.model_dump(mode="json", exclude={"digest"})
    payload["input_projections"] = []
    payload["object_ids"] = []
    payload["projection_digest"] = sha256_digest([])
    with pytest.raises(ValidationError):
        ModelRequestV3.model_validate(payload)


def test_vertex_v3_deadline_size_and_unknown_send_fail_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request_v3()
    calls: list[str] = []

    def unexpected(*_args, **_kwargs):
        calls.append("unexpected")
        raise AssertionError("pre-dispatch rejection must not call the provider")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    expired = _provider().generate_structured(
        request=request,
        output_model=DomainAdvisoryCandidate,
        deadline_monotonic=time.monotonic() - 1,
    )
    assert expired.status == "NOT_RUN"
    assert expired.dispatch_state == "NOT_SENT"
    assert expired.error_code == "VERTEX_TOTAL_DEADLINE_EXHAUSTED"

    too_small = VertexAIStructuredProvider(
        prompt_payload={},
        project_id="orgrebase-test1",
        model_id=VERTEX_CANDIDATE_MODEL_ID,
        access_token="in-memory-test-token",
        model_budget=_budget(),
        max_request_bytes=64,
    ).generate_structured(
        request=request,
        output_model=DomainAdvisoryCandidate,
    )
    assert too_small.status == "NOT_RUN"
    assert too_small.dispatch_state == "NOT_SENT"
    assert too_small.error_code == "VERTEX_REQUEST_TOO_LARGE"
    assert calls == []

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("unknown")),
    )
    unknown = _provider().generate_structured(
        request=request,
        output_model=DomainAdvisoryCandidate,
    )
    assert unknown.status == "PROVIDER_ERROR"
    assert unknown.dispatch_state == "SENT_UNKNOWN"
    assert unknown.evidence_class == "MODEL_ATTEMPT"
    assert unknown.provider_request_id is None
    assert unknown.observed_model_id is None
    assert unknown.input_tokens is None and unknown.output_tokens is None


def test_old_v1_and_v2_records_round_trip_without_byte_drift() -> None:
    projection = _projection()
    request_v1 = ModelRequest(
        request_id="legacy-request-v1",
        run_id="legacy-run",
        task_ref="legacy-task",
        actor_id="legacy-actor",
        purpose="legacy-purpose",
        schema_name="DomainAdvisoryCandidate",
        schema_digest=sha256_digest(
            DomainAdvisoryCandidate.model_json_schema(mode="validation")
        ),
        context_refs=(projection.digest,),
        input_refs=(projection.ref,),
        allowed_tool_ids=(),
        provider="vertex-ai",
        model_id="gemini-3.7-flash",
        model_version="gemini-3.7-flash",
        prompt_template_ref="legacy-prompt@1",
        prompt_template_digest="sha256:" + "2" * 64,
        temperature=0.0,
        seed=None,
        max_output_tokens=256,
        attempt=0,
    )
    receipt_v1 = ModelResponseReceipt(
        id="legacy-receipt-v1",
        request_ref=request_v1.request_id,
        request_digest=request_v1.digest,
        status="NOT_RUN",
        provider="vertex-ai",
        model_id="gemini-3.7-flash",
        model_version="gemini-3.7-flash",
        schema_valid=False,
        error_code="LEGACY_NOT_RUN",
        completed_at=NOW,
        evidence_class=EvidenceClass.NOT_RUN,
    )
    prompt = "Legacy Responses prompt."
    request_v2 = ModelRequestV2(
        request_id="legacy-request-v2",
        tenant_id="tenant:test",
        workspace_id="workspace:test",
        run_id="legacy-run",
        nonce="legacy-nonce",
        task_ref="legacy-task",
        actor_id="legacy-actor",
        purpose="legacy-purpose",
        domain_id="finance",
        object_ids=("rule:finance",),
        input_projections=(projection,),
        schema_name="DomainAdvisoryCandidate",
        schema_digest=sha256_digest(
            DomainAdvisoryCandidate.model_json_schema(mode="validation")
        ),
        prompt_template_ref="legacy-prompt@2",
        prompt_template=prompt,
        prompt_template_digest=sha256_digest(prompt),
        model_id="gpt-test",
        max_output_tokens=256,
    )
    receipt_v2 = ModelResponseReceiptV2(
        id="legacy-receipt-v2",
        request_ref=request_v2.request_id,
        request_digest=request_v2.digest,
        status="NOT_RUN",
        dispatch_state="NOT_SENT",
        requested_model_id=request_v2.model_id,
        schema_digest=request_v2.schema_digest,
        error_code="LEGACY_NOT_RUN",
        observed_at=NOW,
        evidence_class="NOT_RUN",
    )

    for record in (request_v1, receipt_v1, request_v2, receipt_v2):
        raw = record.model_dump_json()
        assert type(record).model_validate_json(raw).model_dump_json() == raw
    assert isinstance(
        TypeAdapter(CandidateModelRequest).validate_python(request_v2.model_dump(mode="json")),
        ModelRequestV2,
    )
    assert isinstance(
        TypeAdapter(CandidateModelRequest).validate_python(_request_v3().model_dump(mode="json")),
        ModelRequestV3,
    )
    assert isinstance(
        TypeAdapter(CandidateModelResponseReceipt).validate_python(
            receipt_v2.model_dump(mode="json")
        ),
        ModelResponseReceiptV2,
    )


def test_workspace_advisory_uses_vertex_v3_without_responses_fallback(
    fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = contracts(fixture, (("rule:finance", "finance"),))
    response_ids: list[str] = []

    def send(http_request: urllib.request.Request, *, timeout: float) -> _Response:
        del timeout
        body = json.loads(http_request.data)
        user_payload = json.loads(body["contents"][0]["parts"][0]["text"])
        binding = user_payload["binding"]
        projections = user_payload["input_projections"]
        candidate = {
            "domain_id": binding["domain_id"],
            "object_ids": binding["object_ids"],
            "source_refs": [item["ref"] for item in projections],
            "explanation": "Review the admitted Workspace change.",
        }
        response_id = f"vertex-advisory-{len(response_ids) + 1}"
        response_ids.append(response_id)
        return _Response(
            {
                "responseId": response_id,
                "modelVersion": VERTEX_CANDIDATE_MODEL_ID,
                "createTime": NOW,
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(candidate, separators=(",", ":")),
                                }
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", send)
    provider = _provider()
    adapter = WorkspaceChangeAdvisoryAdapter(
        provider=provider,
        tenant_id="tenant:test",
        workspace_id="workspace:test",
        model_id=VERTEX_CANDIDATE_MODEL_ID,
    )
    result = adapter.run(**inputs, now=NOW)
    verified = WorkspaceApplyAdvisoryVerifier(adapter).verify(
        **inputs,
        collaboration=result,
        now=NOW,
    )

    assert response_ids == ["vertex-advisory-1", "vertex-advisory-2"]
    generated = [
        item.payload["model_advisory"]
        for item in result["handoffs"]
        if "model_advisory" in item.payload
    ]
    assert all(item["schema_version"] == "workspace-model-advisory.v2" for item in generated)
    assert all(item["request"]["provider"] == "vertex-ai" for item in generated)
    assert all(item["receipt"]["provider"] == "vertex-ai" for item in generated)
    assert all(item["receipt"]["input_tokens"] is None for item in generated)
    assert verified.ingestion_receipt.target_writes == 0


def test_workspace_vertex_wire_keeps_cross_domain_raw_sentinels_out(
    fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = contracts(
        fixture,
        (("rule:finance", "finance"), ("rule:legal", "legal")),
    )
    change = inputs["change_set"]
    finance, legal = change.deltas
    change = _replace(
        change,
        deltas=(
            _replace(finance, proposed_value="FINANCE_SENTINEL_ONLY"),
            _replace(legal, proposed_value="LEGAL_SENTINEL_ONLY"),
        ),
    )
    inputs["change_set"] = change
    inputs["preview"] = _replace(
        inputs["preview"],
        revision_lock=_replace(
            inputs["preview"].revision_lock,
            change_set_digest=change.digest,
        ),
    )
    wires: dict[str, str] = {}

    def send(http_request: urllib.request.Request, *, timeout: float) -> _Response:
        del timeout
        body = json.loads(http_request.data)
        text = body["contents"][0]["parts"][0]["text"]
        user_payload = json.loads(text)
        binding = user_payload["binding"]
        wires[binding["domain_id"]] = text
        candidate = {
            "domain_id": binding["domain_id"],
            "object_ids": binding["object_ids"],
            "source_refs": [item["ref"] for item in user_payload["input_projections"]],
            "explanation": "Checked candidate without raw cross-domain content.",
        }
        return _Response(
            {
                "responseId": f"vertex-{binding['domain_id']}",
                "modelVersion": VERTEX_CANDIDATE_MODEL_ID,
                "createTime": NOW,
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(candidate, separators=(",", ":"))}]
                        },
                        "finishReason": "STOP",
                    }
                ],
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", send)
    adapter = WorkspaceChangeAdvisoryAdapter(
        provider=_provider(),
        tenant_id="tenant:test",
        workspace_id="workspace:test",
        model_id=VERTEX_CANDIDATE_MODEL_ID,
    )
    adapter.run(**inputs, now=NOW)

    assert "LEGAL_SENTINEL_ONLY" not in wires["finance"]
    assert "FINANCE_SENTINEL_ONLY" not in wires["legal"]
    assert "FINANCE_SENTINEL_ONLY" not in wires["gtm"]
    assert "LEGAL_SENTINEL_ONLY" not in wires["gtm"]


def test_workspace_service_explicit_vertex_mode_binds_only_candidate_v3(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget_path = tmp_path / "vertex-budget.json"
    budget_path.write_text(_budget().model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "vertex-ai")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_ID", VERTEX_CANDIDATE_MODEL_ID)
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_BUDGET_PATH", str(budget_path))
    monkeypatch.setenv("ORGREBASE_VERTEX_PROJECT_ID", "orgrebase-test1")

    service = WorkspaceService(store_path=tmp_path / "workspace.sqlite")
    try:
        assert service.advisory_factory.uses_vertex_v3 is True
        assert service.advisory_factory.model_id == VERTEX_CANDIDATE_MODEL_ID
        assert service.advisory_factory.configuration_binding["mode"] == "vertex-generate-content-v3"
        assert (
            service.boundaries["change_candidate_provider"]
            == "VERTEX_GEMINI_3_8_FLASH_CANDIDATE_CONFIGURED"
        )
    finally:
        service.close()


def test_workspace_service_vertex_mode_requires_exact_model_and_price_contract(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_PROVIDER", "vertex-ai")
    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_ID", "gemini-3.7-flash")
    monkeypatch.delenv("ORGREBASE_CHANGE_MODEL_BUDGET_PATH", raising=False)
    with pytest.raises(ValueError, match="WORKSPACE_ADVISORY_VERTEX_MODEL_REQUIRED"):
        WorkspaceService(store_path=tmp_path / "wrong-model.sqlite")

    monkeypatch.setenv("ORGREBASE_CHANGE_MODEL_ID", VERTEX_CANDIDATE_MODEL_ID)
    with pytest.raises(ValueError, match="MODEL_PRICE_CONTRACT_REQUIRED"):
        WorkspaceService(store_path=tmp_path / "no-budget.sqlite")
