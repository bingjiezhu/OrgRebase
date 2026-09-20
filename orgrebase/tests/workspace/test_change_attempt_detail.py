from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orgrebase.auth import AuthenticationError
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.advisory import AdvisoryGenerationError
from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.preview_execution import read_attempt_summary
from orgrebase.workspace.routes import change_router
from orgrebase.workspace.source_readmission import group_attempt_detail, group_detail
from tests.workspace.test_continuous_changes import proposal
from tests.workspace.test_preview_execution import PricedProvider, cost_attempts, priced_service
from tests.workspace.test_source_readmission_groups import prepare


class ObservedProvider(PricedProvider):
    request_id = "req_controlled-local-1"

    def generate_structured(self, *, request, output_model):
        receipt = super().generate_structured(request=request, output_model=output_model)
        return type(receipt).model_validate({
            **receipt.model_dump(mode="json", exclude={"digest"}),
            "provider_request_id": self.request_id, "input_tokens": 100, "output_tokens": 25,
        })


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_failed_attempt_detail_retains_unknown_usage_and_reservation_without_retry(
    tmp_path, postgres_runtime, backend,
):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, backend, postgres_runtime)
    try:
        assert change_detail(service, "currency")["advisory_attempt"] is None
        with pytest.raises(IntegrityError):
            service.preview_change("currency")
        reservation = cost_attempts(service)
        summary = change_detail(service, "currency")["advisory_attempt"]
        assert summary["state"] == "FAILED"
        assert summary["public_error_code"] == "WORKSPACE_ADVISORY_MODEL_INCOMPLETE"
        assert summary["usage_status"] == "UNKNOWN"
        assert summary["cost_reservation"] == {
            "scope": "ONE_PREVIEW_ATTEMPT", "currency": "USD", "reserved_microusd": 270480,
            "limit_microusd": 10_000_000, "calls": 2,
        }
        assert summary["receipt_summaries"] == [{
            "dispatch_state": "SENT_UNKNOWN", "provider_request_id": None,
            "input_tokens": None, "output_tokens": None,
        }]
        assert change_detail(service, "currency")["advisory_attempt"] == summary
        assert cost_attempts(service) == reservation and len(provider.requests) == 1
        service.register_change(proposal(service, "other-proposal", "product_plan", "Enterprise revised"))
        assert change_detail(service, "other-proposal")["advisory_attempt"] is None
    finally:
        service.close()


def test_missing_result_detail_exposes_deadline_without_writing_or_redispatching(tmp_path, postgres_runtime, monkeypatch):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, "sqlite", postgres_runtime)
    original_save = service.store.save_artifact
    try:
        def lose_result(connection, artifact_id, media_type, payload):
            if artifact_id.startswith("workspace-preview-attempt:"):
                raise RuntimeError("CONTROLLED_PERSISTENCE_LOSS")
            return original_save(connection, artifact_id, media_type, payload)

        monkeypatch.setattr(service.store, "save_artifact", lose_result)
        with pytest.raises(RuntimeError, match="CONTROLLED_PERSISTENCE_LOSS"):
            service.preview_change("currency")
        monkeypatch.setattr(service.store, "save_artifact", original_save)
        before = tuple(service.store.connection.iterdump())
        running = change_detail(service, "currency")["advisory_attempt"]
        assert running["state"] == "IN_PROGRESS" and running["usage_status"] == "UNKNOWN"
        monkeypatch.setattr(service, "_wall_clock", lambda: running["deadline_epoch_ms"] / 1000 + 1)
        unknown = change_detail(service, "currency")["advisory_attempt"]
        assert unknown["state"] == "RESULT_UNKNOWN"
        assert unknown["public_error_code"] == "WORKSPACE_ADVISORY_RESULT_UNKNOWN"
        assert unknown["receipt_summaries"] == []
        assert unknown["cost_reservation"] == running["cost_reservation"]
        assert tuple(service.store.connection.iterdump()) == before and len(provider.requests) == 1
    finally:
        service.close()


@pytest.mark.parametrize("request_id", ["req_controlled-local-1", "<script>response body</script>", "x" * 257])
def test_completed_attempt_projects_observed_usage_and_filters_provider_identifiers(
    tmp_path, postgres_runtime, request_id,
):
    provider = ObservedProvider()
    provider.request_id = request_id
    service = priced_service(tmp_path, provider, "sqlite", postgres_runtime)
    try:
        service.preview_change("currency")
        before = tuple(service.store.connection.iterdump())
        detail = change_detail(service, "currency")
        summary = detail["advisory_attempt"]
        assert detail["status"] == "PREVIEWED" and "APPROVE" in detail["allowed_actions"]
        assert summary["state"] == "COMPLETE" and summary["usage_status"] == "OBSERVED"
        assert summary["public_error_code"] is None
        assert len(summary["receipt_summaries"]) == 2
        assert all(item == {
            "dispatch_state": "RESPONSE_RECEIVED",
            "provider_request_id": request_id if request_id == "req_controlled-local-1" else None,
            "input_tokens": 100, "output_tokens": 25,
        } for item in summary["receipt_summaries"])
        assert set(summary) == {"state", "deadline_epoch_ms", "cost_reservation", "usage_status",
                                "receipt_summaries", "public_error_code"}
        assert "contract" not in summary["cost_reservation"]
        assert tuple(service.store.connection.iterdump()) == before and len(provider.requests) == 2
    finally:
        service.close()


def test_generated_but_rejected_candidates_keep_receipts_without_claiming_preview_admission(
    tmp_path, postgres_runtime, monkeypatch,
):
    provider = ObservedProvider()
    service = priced_service(tmp_path, provider, "sqlite", postgres_runtime)
    try:
        def reject(**kwargs):
            raise IntegrityError("REJECTED_AFTER_GENERATION")

        monkeypatch.setattr(service.advisory_verifier, "verify", reject)
        with pytest.raises(IntegrityError, match="REJECTED_AFTER_GENERATION"):
            service.preview_change("currency")
        detail = change_detail(service, "currency")
        assert detail["status"] == "RECEIVED" and detail["preview"] is None
        summary = detail["advisory_attempt"]
        assert summary["state"] == "FAILED" and summary["usage_status"] == "UNKNOWN"
        assert len(summary["receipt_summaries"]) == 2
        assert all(item["provider_request_id"] == provider.request_id for item in summary["receipt_summaries"])
        assert "APPROVE" not in detail["allowed_actions"] and len(provider.requests) == 2
    finally:
        service.close()


def test_attempt_detail_does_not_expose_arbitrary_failure_text(tmp_path, postgres_runtime, monkeypatch):
    provider = PricedProvider()
    service = priced_service(tmp_path, provider, "sqlite", postgres_runtime)
    secret = "secret-file /private/path bearer token response-body"
    try:
        def fail(**kwargs):
            raise AdvisoryGenerationError(secret)

        monkeypatch.setattr(service.advisory_factory, "run", fail)
        with pytest.raises(AdvisoryGenerationError):
            service.preview_change("currency")
        summary = change_detail(service, "currency")["advisory_attempt"]
        assert summary["public_error_code"] == "WORKSPACE_REQUEST_FAILED"
        assert secret not in json.dumps(summary) and summary["usage_status"] == "UNKNOWN"
        assert provider.requests == []
        service.approval_identity_mode = "VERIFIED_PRINCIPAL_IDENTITY"
        with pytest.raises(AuthenticationError, match="AUTH_BEARER_REQUIRED"):
            change_detail(service, "currency")
    finally:
        service.close()


def test_attempt_lookup_cannot_read_another_workspace_with_the_same_run_and_event(tmp_path, postgres_runtime):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, "postgresql", postgres_runtime)
    try:
        with pytest.raises(IntegrityError):
            service.preview_change("currency")
        assert change_detail(service, "currency")["advisory_attempt"]["state"] == "FAILED"
        service.store.register_workspace("another-quote", profile_digest=service.profile_digest,
                                         pack_digest=None, quote_object_id="quote:another",
                                         created_at=service.clock.now())
        with StateStore(service.store.path, tenant_id=service.store.tenant_id,
                        workspace_id="another-quote", migrate=False) as other_store:
            other = SimpleNamespace(
                store=other_store, effective_workflow_run_id=service.effective_workflow_run_id,
                workflow_run_nonce=service.workflow_run_nonce,
            )
            assert read_attempt_summary(other, command="change:currency") is None
        assert len(provider.requests) == 1
    finally:
        service.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("lose_result", [False, True])
def test_unadmitted_group_attempt_is_readable_without_group_creation_or_retry(
    tmp_path, postgres_runtime, monkeypatch, backend, lose_result,
):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, backend, postgres_runtime)
    save = service.store.save_artifact
    try:
        if lose_result:
            def unavailable(connection, artifact_id, media_type, payload):
                if artifact_id.startswith("workspace-preview-attempt:"):
                    raise RuntimeError("CONTROLLED_PERSISTENCE_LOSS")
                return save(connection, artifact_id, media_type, payload)
            monkeypatch.setattr(service.store, "save_artifact", unavailable)
        with pytest.raises((IntegrityError, RuntimeError)):
            prepare(service)
        monkeypatch.setattr(service.store, "save_artifact", save)
        before = (service.store.audit_head(), cost_attempts(service),
                  tuple((item.artifact_id, item.payload_digest)
                        for item in service.store.list_artifacts(artifact_id_prefix="workspace-")))
        dump = tuple(service.store.connection.iterdump()) if backend == "sqlite" else None
        app = FastAPI()
        app.include_router(change_router(lambda: service))
        with TestClient(app) as client:
            prefix = "/api/workspace/source-readmission-groups/"
            assert client.get(prefix + "recover-1").status_code == 404
            assert client.get(prefix + "missing/attempt").status_code == 404
            response = client.get(prefix + "recover-1/attempt")
            assert response.status_code == 200
            body = response.json()
            assert set(body) == {"group_id", "advisory_attempt"} and body["group_id"] == "recover-1"
            summary = body["advisory_attempt"]
            assert summary["state"] == ("IN_PROGRESS" if lose_result else "FAILED")
            assert summary["usage_status"] == "UNKNOWN" and summary["cost_reservation"]["reserved_microusd"] > 0
            if lose_result:
                monkeypatch.setattr(service, "_wall_clock", lambda: summary["deadline_epoch_ms"] / 1000 + 1)
                assert client.get(prefix + "recover-1/attempt").json()["advisory_attempt"]["state"] == "RESULT_UNKNOWN"
            else:
                assert summary["receipt_summaries"][0]["dispatch_state"] == "SENT_UNKNOWN"
            service.approval_identity_mode = "VERIFIED_PRINCIPAL_IDENTITY"
            assert client.get(prefix + "recover-1/attempt").status_code == 401
        after = (service.store.audit_head(), cost_attempts(service),
                 tuple((item.artifact_id, item.payload_digest)
                       for item in service.store.list_artifacts(artifact_id_prefix="workspace-")))
        assert after == before and len(provider.requests) == 1
        if dump is not None:
            assert tuple(service.store.connection.iterdump()) == dump
    finally:
        service.close()


def test_group_attempt_remains_readable_after_admission(tmp_path, postgres_runtime):
    provider = ObservedProvider()
    service = priced_service(tmp_path, provider, "sqlite", postgres_runtime)
    try:
        admitted = prepare(service)
        before = tuple(service.store.connection.iterdump())
        summary = group_attempt_detail(service, "recover-1")
        assert summary["advisory_attempt"]["state"] == "COMPLETE"
        assert summary["advisory_attempt"]["usage_status"] == "OBSERVED"
        assert group_detail(service, "recover-1") == admitted
        assert tuple(service.store.connection.iterdump()) == before
    finally:
        service.close()


def test_group_attempt_read_cannot_cross_workspace_scope(tmp_path, postgres_runtime):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, "postgresql", postgres_runtime)
    try:
        with pytest.raises(IntegrityError):
            prepare(service)
        assert group_attempt_detail(service, "recover-1")["advisory_attempt"]["state"] == "FAILED"
        service.store.register_workspace("another-quote", profile_digest=service.profile_digest,
                                         pack_digest=None, quote_object_id="quote:another",
                                         created_at=service.clock.now())
        with StateStore(service.store.path, tenant_id=service.store.tenant_id,
                        workspace_id="another-quote", migrate=False) as other_store:
            other = SimpleNamespace(
                store=other_store, effective_workflow_run_id=service.effective_workflow_run_id,
                workflow_run_nonce=service.workflow_run_nonce, _command_lock=service._command_lock,
                profile=service.profile, approval_identity_mode="CONTROLLED_LOCAL_HEADER_IDENTITY",
            )
            with pytest.raises(KeyError):
                group_attempt_detail(other, "recover-1")
        assert len(provider.requests) == 1
    finally:
        service.close()
