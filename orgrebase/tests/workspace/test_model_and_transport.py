from __future__ import annotations

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, RunEnvelope
from orgrebase.workspace.model_provider import (
    BoundedSchemaRepairProvider,
    DeterministicModelProvider,
    LiveHTTPModelProvider,
)
from orgrebase.workspace.models import ModelRequest, TaskRequest
from orgrebase.workspace.transport import (
    LiveAgentTeamsTransport,
    LocalDeterministicTransport,
    WorkspaceTransportCompiler,
    agentteams_status,
)


def _model_request() -> ModelRequest:
    return ModelRequest(
        request_id="model-request:test",
        run_id="run:test",
        task_ref="task:test",
        actor_id="agent:test",
        purpose="test",
        schema_name="TaskRequest",
        schema_digest=sha256_digest(TaskRequest.model_json_schema()),
        context_refs=(),
        input_refs=(),
        allowed_tool_ids=(),
        provider="deterministic",
        model_id="reference",
        model_version="v1",
        prompt_template_ref="prompt:test@v1",
        prompt_template_digest=sha256_digest({"prompt": "test"}),
        temperature=0.0,
        seed=0,
        max_output_tokens=100,
        attempt=0,
    )


def _valid_task_payload() -> dict[str, object]:
    return {
        "id": "task:model-output",
        "organization_id": "org:northstar",
        "actor_id": "employee:test",
        "purpose": "enterprise_quote",
        "deliverable_kind": "QUOTE",
        "requested_at": "2026-08-15T00:00:00Z",
        "template_ref": "template:enterprise_quote@v1",
        "input_values": {},
        "customer_id": "customer:acme",
        "idempotency_key": "model-output@1",
    }


def test_deterministic_model_provider_validates_schema() -> None:
    receipt = DeterministicModelProvider(lambda _request: _valid_task_payload()).generate_structured(
        request=_model_request(), output_model=TaskRequest
    )
    assert receipt.status == "VALID"
    assert receipt.schema_valid
    assert receipt.evidence_class == EvidenceClass.LOCAL_DETERMINISTIC


def test_schema_repair_is_bounded_to_three_total_attempts() -> None:
    calls = {"count": 0}

    def invalid(_request):
        calls["count"] += 1
        return {"not": "a task"}

    provider = BoundedSchemaRepairProvider(DeterministicModelProvider(invalid))
    receipt = provider.generate_structured(request=_model_request(), output_model=TaskRequest)
    assert receipt.status == "ABSTAIN"
    assert receipt.error_code == "SCHEMA_REPAIR_EXHAUSTED"
    assert calls["count"] == 3


def test_live_model_without_credentials_is_honest_not_run(monkeypatch) -> None:
    monkeypatch.delenv("ORGREBASE_MODEL_ENDPOINT", raising=False)
    monkeypatch.delenv("ORGREBASE_MODEL_API_KEY", raising=False)
    receipt = LiveHTTPModelProvider().generate_structured(
        request=_model_request(), output_model=TaskRequest
    )
    assert receipt.status == "NOT_RUN"
    assert receipt.evidence_class == EvidenceClass.NOT_RUN


def _transport_fixture(formed_service):
    from orgrebase.workspace.models import CoalitionPlan, TaskContextManifest

    receipt = formed_service.store.load_artifact("task-receipt:quote_acme@v1").payload
    plan = CoalitionPlan.model_validate(
        formed_service.store.load_artifact(receipt["coalition_plan_ref"]).payload
    )
    manifest = TaskContextManifest.model_validate(
        formed_service.store.load_artifact(receipt["context_manifest_ref"]).payload
    )
    projection_by_domain = {
        ref.split(":")[-1].split("@")[0]: ref for ref in manifest.actor_projection_refs
    }
    # Projection IDs include actor names; map by the selected domain order instead.
    projection_by_domain = {
        coverage.domain_id: manifest.actor_projection_refs[index]
        for index, coverage in enumerate(
            sorted(plan.coverage, key=lambda item: item.domain_id)
        )
        if index < len(manifest.actor_projection_refs)
    }
    # Multiple slots share domains; collapse to one ref per domain deterministically.
    domains = sorted({item.domain_id for item in plan.coverage})
    projection_by_domain = {
        domain: manifest.actor_projection_refs[index]
        for index, domain in enumerate(domains)
    }
    envelope = RunEnvelope(
        run_id="run:transport:test",
        nonce="n" * 64,
        issued_at="2026-08-15T00:00:00Z",
        expires_at="2026-08-16T00:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )
    delegations = WorkspaceTransportCompiler().compile(
        plan=plan, projection_refs_by_domain=projection_by_domain, run_envelope=envelope
    )
    return plan, envelope, delegations


def test_local_transport_preserves_exact_coalition_and_zero_writes(formed_service) -> None:
    plan, envelope, delegations = _transport_fixture(formed_service)
    candidates, receipt = LocalDeterministicTransport().execute_delegations(
        plan=plan, delegations=delegations, run_envelope=envelope
    )
    assert len(candidates) == 4
    assert receipt.target_writes == 0
    assert receipt.status == "PASS"
    assert set(receipt.selected_worker_ids) == {
        "product-steward",
        "legal-steward",
        "finance-steward",
        "gtm-steward",
    }


def test_live_transport_without_evidence_is_not_run(formed_service, monkeypatch) -> None:
    monkeypatch.delenv("ORGREBASE_AGENTTEAMS_EVIDENCE", raising=False)
    plan, envelope, delegations = _transport_fixture(formed_service)
    with pytest.raises(RuntimeError, match="LIVE_AGENTTEAMS_NOT_RUN"):
        LiveAgentTeamsTransport().execute_delegations(
            plan=plan, delegations=delegations, run_envelope=envelope
        )
    assert agentteams_status()["evidence_class"] == "NOT_RUN"
