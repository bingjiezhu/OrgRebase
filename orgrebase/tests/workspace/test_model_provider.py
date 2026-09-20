from __future__ import annotations

from pydantic import BaseModel

from orgrebase.workspace.model_provider import DeterministicModelProvider, LiveHTTPModelProvider
from orgrebase.workspace.models import ModelRequest


class Output(BaseModel):
    value: str


def request() -> ModelRequest:
    return ModelRequest(
        request_id="model-request:1",
        run_id="run:1",
        task_ref="task:1",
        actor_id="agent:product",
        purpose="candidate_generation",
        schema_name="Output",
        schema_digest="sha256:" + "1" * 64,
        context_refs=(),
        input_refs=(),
        allowed_tool_ids=(),
        provider="deterministic",
        model_id="reference",
        model_version="v1",
        prompt_template_ref="prompt:1",
        prompt_template_digest="sha256:" + "2" * 64,
        temperature=0.0,
        seed=0,
        max_output_tokens=128,
        attempt=0,
    )


def test_deterministic_provider_returns_schema_valid_receipt() -> None:
    receipt = DeterministicModelProvider(lambda _request: {"value": "ok"}).generate_structured(request=request(), output_model=Output)
    assert receipt.status == "VALID"
    assert receipt.schema_valid
    assert receipt.value == {"value": "ok"}


def test_invalid_deterministic_output_is_schema_error() -> None:
    receipt = DeterministicModelProvider(lambda _request: {"wrong": "field"}).generate_structured(request=request(), output_model=Output)
    assert receipt.status == "SCHEMA_ERROR"
    assert not receipt.schema_valid


def test_live_provider_without_environment_is_honest_not_run(monkeypatch) -> None:
    monkeypatch.delenv("ORGREBASE_LIVE_MODEL_ENABLED", raising=False)
    receipt = LiveHTTPModelProvider().generate_structured(request=request(), output_model=Output)
    assert receipt.status == "NOT_RUN"
    assert receipt.evidence_class == "NOT_RUN"


def test_v1_request_and_receipt_digest_vectors_remain_readable() -> None:
    from orgrebase.workspace.model_provider import _receipt
    from orgrebase.workspace.models import ModelResponseReceipt

    incoming = request()
    assert incoming.digest == "sha256:ce15ee818d98a0d619e944ce8aed56ddb8140e671c0532f33ea3b242a2684bec"
    old = _receipt(incoming, status="VALID", value={"value": "ok"}, provider_request_id="fixed:old",
                   schema_valid=True, error_code=None, evidence_class="LOCAL_DETERMINISTIC")
    assert old.digest == "sha256:acd0ecb61bad81f3d88ea81621b25c775a5510ab4ff8e7aa0d38895a845a2206"
    assert ModelResponseReceipt.model_validate_json(old.model_dump_json()) == old
