from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from orgrebase.clock import FrozenClock
from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.workspace import source_readmission
from orgrebase.workspace.advisory import WorkspaceApplyAdvisoryVerifier
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_readmission import group_detail
from tests.workspace.test_change_advisory import RecordingProvider
from tests.workspace.test_continuous_changes import PACK, apply, make_service, proposal
from tests.workspace.test_model_budget import budget
from tests.workspace.test_source_readmission_groups import prepare


class PricedProvider(RecordingProvider):
    def __init__(self, *, limit="10", unknown=False):
        super().__init__(fail_at=1 if unknown else None)
        self.model_budget = budget(model_id="model:test", max_preview_usd=limit)

    def generate_structured(self, *, request, output_model):
        receipt = super().generate_structured(request=request, output_model=output_model)
        payload = receipt.model_dump(mode="json", exclude={"digest"})
        if receipt.status == "NOT_RUN":
            payload.update(status="PROVIDER_ERROR", dispatch_state="SENT_UNKNOWN",
                           evidence_class="MODEL_ATTEMPT", error_code="OPENAI_TIMEOUT")
        else:
            payload["observed_model_id"] = request.model_id
        return type(receipt).model_validate(payload)


def priced_service(tmp_path, provider, backend, postgres_runtime):
    pack = load_enterprise_quote_pilot_pack(PACK)
    if backend == "postgresql":
        database = postgres_runtime(tenant_id=pack.profile.organization_id)
        options = {"store_path": database["runtime_dsn"], "store_tenant_id": pack.profile.organization_id,
                   "store_migrate": False}
    else:
        options = {"store_path": tmp_path / "priced.sqlite"}
    service = WorkspaceService(**options, runtime_configuration=pack,
                               advisory_provider=provider, advisory_model_id="model:test",
                               clock=FrozenClock("2026-08-15T00:00:00Z"))
    service.form_quote()
    return service


def cost_attempts(service):
    rows = service.store.connection.execute(
        "SELECT result_json FROM idempotency_records WHERE key LIKE 'workspace-preview-attempt:%'"
    ).fetchall()
    return [json.loads(row["result_json"]) for row in rows]


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_complete_round_price_limit_rejects_before_attempt_and_any_call(tmp_path, postgres_runtime, backend):
    provider = PricedProvider(limit="0.20")  # One call fits; domain + GTM together do not.
    service = priced_service(tmp_path, provider, backend, postgres_runtime)
    try:
        with pytest.raises(IntegrityError, match="WORKSPACE_ADVISORY_COST_LIMIT_EXCEEDED"):
            service.preview_change("currency")
        assert provider.requests == []
        assert cost_attempts(service) == []
    finally:
        service.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_unknown_paid_attempt_retains_full_reservation_without_redispatch(tmp_path, postgres_runtime, backend):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, backend, postgres_runtime)
    try:
        with pytest.raises(IntegrityError):
            service.preview_change("currency")
        original = cost_attempts(service)
        assert len(original) == 1
        reserved = original[0]["cost_reservation"]
        assert reserved["calls"] == 2 and reserved["reserved_microusd"] == 270480
        result = service.store.list_artifacts(artifact_id_prefix="workspace-preview-attempt:")[0].payload
        assert result["cost_reservation"] == reserved
        assert result["usage_status"] == "UNKNOWN"
        assert result["receipts"][0]["dispatch_state"] == "SENT_UNKNOWN"
        assert result["receipts"][0]["input_tokens"] is None
        with pytest.raises(IntegrityError, match="WORKSPACE_ADVISORY_ATTEMPT_FAILED"):
            service.preview_change("currency")
        assert len(provider.requests) == 1 and cost_attempts(service) == original
    finally:
        service.close()


def test_successful_attempt_cache_keeps_original_reservation_without_new_calls(tmp_path, postgres_runtime):
    provider = PricedProvider()
    service = priced_service(tmp_path, provider, "sqlite", postgres_runtime)
    try:
        first = service.preview_change("currency")
        original = cost_attempts(service)
        assert len(provider.requests) == 2
        second = service.preview_change("currency")
        assert second.model_dump(mode="json") == first.model_dump(mode="json")
        assert len(provider.requests) == 2
        assert cost_attempts(service) == original
    finally:
        service.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_missing_result_deadline_never_refunds_or_redispatches(tmp_path, postgres_runtime, backend, monkeypatch):
    provider = PricedProvider(unknown=True)
    service = priced_service(tmp_path, provider, backend, postgres_runtime)
    original_save = service.store.save_artifact
    try:
        def lose_result(connection, artifact_id, media_type, payload):
            if artifact_id.startswith("workspace-preview-attempt:"):
                raise RuntimeError("SIMULATED_RESULT_PERSISTENCE_LOSS")
            return original_save(connection, artifact_id, media_type, payload)

        monkeypatch.setattr(service.store, "save_artifact", lose_result)
        with pytest.raises(RuntimeError, match="SIMULATED_RESULT_PERSISTENCE_LOSS"):
            service.preview_change("currency")
        monkeypatch.setattr(service.store, "save_artifact", original_save)
        original = cost_attempts(service)
        assert len(original) == 1 and original[0]["cost_reservation"]["reserved_microusd"] > 0
        with pytest.raises(IntegrityError, match="WORKSPACE_ADVISORY_IN_PROGRESS"):
            service.preview_change("currency")
        monkeypatch.setattr(service, "_wall_clock", lambda: original[0]["execution_deadline_epoch_ms"] / 1000 + 1)
        with pytest.raises(IntegrityError, match="WORKSPACE_ADVISORY_RESULT_UNKNOWN"):
            service.preview_change("currency")
        assert len(provider.requests) == 1 and cost_attempts(service) == original
    finally:
        service.close()


class BlockingAdvisory:
    def __init__(self, adapter):
        self.adapter = adapter
        self.entered, self.release = Event(), Event()
        self.calls = 0

    @property
    def configuration_binding(self):
        return self.adapter.configuration_binding

    def run(self, **kwargs):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(10):
            raise RuntimeError("TEST_ADVISORY_NOT_RELEASED")
        return self.adapter.run(**kwargs)


def block(service):
    adapter = BlockingAdvisory(service.advisory_factory)
    service.advisory_factory = adapter
    return adapter


def register(service):
    event = proposal(service, "pending-model", "product_plan", "Enterprise updated")
    service.register_change(event)
    return event


def test_preview_releases_locks_and_deduplicates_concurrent_and_completed_commands(tmp_path):
    service = make_service(tmp_path / "preview.sqlite")
    event = register(service)
    adapter = block(service)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(service.preview_command, event.event_id)
            assert adapter.entered.wait(5)
            try:
                assert pool.submit(service.state).result(timeout=3)["schema_version"]
                with pytest.raises(IntegrityError, match="ADVISORY_IN_PROGRESS"):
                    pool.submit(service.preview_change, event.event_id).result(timeout=3)
                assert adapter.calls == 1
            finally:
                adapter.release.set()
            result = first.result(timeout=5)
        again = service.preview_command(event.event_id)
        assert again["artifact_digest"] == result["artifact_digest"]
        assert adapter.calls == 1
    finally:
        adapter.release.set()
        service.close()


@pytest.mark.parametrize("mutation", ["source", "rejection"])
def test_late_candidate_cannot_restore_invalidated_or_rejected_work(tmp_path, mutation):
    service = make_service(tmp_path / "stale.sqlite")
    event = register(service)
    adapter = block(service)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(service.preview_change, event.event_id)
            assert adapter.entered.wait(5)
            try:
                if mutation == "source":
                    pool.submit(service.invalidate_source, "product_plan", "https://source.example/plan",
                                "SOURCE_FIELD_MISSING").result(timeout=3)
                else:
                    pool.submit(service.reject_change, event.event_id, actor_id=event.owner_id,
                                reason="withdraw this change").result(timeout=3)
            finally:
                adapter.release.set()
            with pytest.raises((IntegrityError, RuntimeError)):
                pending.result(timeout=5)
        assert service._preview_record(event.event_id) is None
        assert service.current_quote().payload["product_plan"] != "Enterprise updated"
    finally:
        adapter.release.set()
        service.close()


def test_group_inference_releases_locks_and_rejects_source_changes(tmp_path):
    service = make_service(tmp_path / "group.sqlite")
    adapter = block(service)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(prepare, service)
            assert adapter.entered.wait(5)
            try:
                assert pool.submit(service.state).result(timeout=3)["schema_version"]
                pool.submit(service.invalidate_source, "currency", "https://source.example/changed",
                            "SOURCE_FIELD_MISSING").result(timeout=3)
            finally:
                adapter.release.set()
            with pytest.raises((IntegrityError, RuntimeError)):
                pending.result(timeout=5)
        with pytest.raises(KeyError):
            group_detail(service, "recover-1")
        assert adapter.calls == 1
    finally:
        adapter.release.set()
        service.close()


def test_saved_candidate_survives_failure_before_preview_commit_without_new_generation(tmp_path, monkeypatch):
    service = make_service(tmp_path / "resume.sqlite")
    event = register(service)
    adapter = block(service)
    adapter.release.set()
    original = service._new_review_gate
    try:
        def interrupted(**kwargs):
            raise RuntimeError("SIMULATED_PREVIEW_COMMIT_INTERRUPTION")
        monkeypatch.setattr(service, "_new_review_gate", interrupted)
        with pytest.raises(RuntimeError, match="SIMULATED_PREVIEW"):
            service.preview_change(event.event_id)
        assert service._preview_record(event.event_id) is None
        monkeypatch.setattr(service, "_new_review_gate", original)
        assert service.preview_change(event.event_id).advisory.ingestion_receipt.target_writes == 0
        assert adapter.calls == 1
    finally:
        service.close()


def test_verification_failure_is_retained_and_never_redispatched(tmp_path, monkeypatch):
    service = make_service(tmp_path / "failed.sqlite")
    event = register(service)
    adapter = block(service)
    adapter.release.set()
    try:
        def reject(**kwargs):
            raise IntegrityError("UNTRUSTED_CANDIDATE")
        monkeypatch.setattr(service.advisory_verifier, "verify", reject)
        with pytest.raises(IntegrityError, match="UNTRUSTED_CANDIDATE"):
            service.preview_change(event.event_id)
        service.advisory_verifier = WorkspaceApplyAdvisoryVerifier(adapter.adapter)
        with pytest.raises(IntegrityError, match="ADVISORY_ATTEMPT_FAILED"):
            service.preview_change(event.event_id)
        assert adapter.calls == 1
        assert service._preview_record(event.event_id) is None
    finally:
        service.close()


def test_model_preview_apply_and_readback_keep_candidate_authority(tmp_path):
    pack = load_enterprise_quote_pilot_pack(PACK)
    provider = RecordingProvider()
    service = WorkspaceService(store_path=tmp_path / "model.sqlite", runtime_configuration=pack,
                               advisory_provider=provider, advisory_model_id="model:test",
                               clock=FrozenClock("2026-08-15T00:00:00Z"))
    try:
        service.form_quote()
        result = apply(service, "currency")
        before_calls = len(provider.requests)
        assert before_calls == 2
        assert result["outcome"]
        preview = service.preview_command("currency")
        model_handoffs = [handoff for handoff in preview["bundle"]["advisory"]["handoffs"]
                          if "model_advisory" in handoff["payload"]]
        assert len(model_handoffs) == 2
        assert len(provider.requests) == before_calls
        view = service._agentteams_operations_view(changes={"currency": {"preview": preview}}, competition_evidence=None)
        status = view["change_set_advisories"]["currency"]
        assert status["participation_status"] == "MODEL_CANDIDATE_ADVISORY"
        assert not status["live_agentteams_observed"]
        assert not status["native_agentteams_observed"]
        assert status["target_writes"] == 0
    finally:
        service.close()


def test_independent_verifier_failure_preserves_generated_model_receipts(tmp_path, monkeypatch):
    provider = RecordingProvider()
    service = WorkspaceService(store_path=tmp_path / "rejected-model.sqlite",
        runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
        advisory_provider=provider, advisory_model_id="model:test", clock=FrozenClock("2026-08-15T00:00:00Z"))
    try:
        service.form_quote()
        def reject(**kwargs):
            raise IntegrityError("REJECTED_AFTER_GENERATION")
        monkeypatch.setattr(service.advisory_verifier, "verify", reject)
        with pytest.raises(IntegrityError, match="REJECTED_AFTER_GENERATION"):
            service.preview_change("currency")
        records = service.store.list_artifacts(artifact_id_prefix="workspace-preview-attempt:")
        assert len(records) == 1 and records[0].payload["status"] == "FAILED"
        receipts = [handoff["payload"]["model_advisory"]["receipt"]
                    for handoff in records[0].payload["unverified_handoffs"]
                    if "model_advisory" in handoff["payload"]]
        assert len(receipts) == len(provider.requests) == 2
        assert all(receipt["input_tokens"] is None for receipt in receipts)
        assert service._preview_record("currency") is None
    finally:
        service.close()


def test_missing_provider_prerequisite_does_not_reserve_and_cached_result_needs_no_key(tmp_path, monkeypatch):
    class AvailableProvider(RecordingProvider):
        available = False

        def require_available(self):
            if not self.available:
                raise ValueError("OPENAI_CREDENTIALS_MISSING")

    provider = AvailableProvider()
    service = WorkspaceService(store_path=tmp_path / "configuration.sqlite",
        runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
        advisory_provider=provider, advisory_model_id="model:test", clock=FrozenClock("2026-08-15T00:00:00Z"))
    try:
        service.form_quote()
        with pytest.raises(IntegrityError, match="OPENAI_CREDENTIALS_MISSING"):
            service.preview_change("currency")
        assert not provider.requests
        original = service._new_review_gate
        def interrupt(**kwargs):
            raise RuntimeError("INTERRUPTED_BEFORE_REVIEW")
        provider.available = True
        monkeypatch.setattr(service, "_new_review_gate", interrupt)
        with pytest.raises(RuntimeError, match="INTERRUPTED_BEFORE_REVIEW"):
            service.preview_change("currency")
        provider.available = False
        monkeypatch.setattr(service, "_new_review_gate", original)
        assert service.preview_change("currency").advisory.ingestion_receipt.target_writes == 0
        assert len(provider.requests) == 2
    finally:
        service.close()


def test_postgres_two_instances_share_one_candidate_attempt(postgres_runtime):
    pack = load_enterprise_quote_pilot_pack(PACK)
    database = postgres_runtime(tenant_id=pack.profile.organization_id)
    options = {"store_path": database["runtime_dsn"], "store_tenant_id": pack.profile.organization_id,
               "runtime_configuration": pack, "store_migrate": False}
    first = WorkspaceService(**options)
    second = None
    try:
        first.form_quote()
        second = WorkspaceService(**options)
        adapter = block(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(first.preview_change, "currency")
            assert adapter.entered.wait(5)
            try:
                assert pool.submit(second.state).result(timeout=3)["schema_version"]
                with pytest.raises(IntegrityError, match="ADVISORY_IN_PROGRESS"):
                    second.preview_change("currency")
            finally:
                adapter.release.set()
            result = pending.result(timeout=5)
        assert second.preview_change("currency").model_dump(mode="json") == result.model_dump(mode="json")
        assert adapter.calls == 1
    finally:
        if second:
            second.close()
        first.close()


def test_postgres_readmission_rechecks_invalidation_after_candidate_persistence(postgres_runtime, monkeypatch):
    pack = load_enterprise_quote_pilot_pack(PACK)
    database = postgres_runtime(tenant_id=pack.profile.organization_id)
    options = {"store_path": database["runtime_dsn"], "store_tenant_id": pack.profile.organization_id,
               "runtime_configuration": pack, "store_migrate": False,
               "clock": FrozenClock("2026-08-15T00:00:00Z")}
    first = WorkspaceService(**options)
    second = None
    try:
        first.form_quote()
        second = WorkspaceService(**options)
        execute_attempt = source_readmission.execute_attempt
        executions = 0

        def invalidate_after_candidate(*args, **kwargs):
            nonlocal executions
            advisory = execute_attempt(*args, **kwargs)
            executions += 1
            # The candidate is durable, but final admission has not acquired
            # the workspace scope lock. This remains a reachable race window.
            binding = next(item for item in second.enterprise_binding.resources if item.slot_id == "currency")
            source = second.store.get_object(binding.object_id)
            quote = second.current_quote()
            snapshot = second.current_snapshot()
            assert source.state == ObjectState.STALE
            assert quote.state == ObjectState.REVIEW_REQUIRED
            second.invalidate_source("currency", "https://source.example/currency/revoked-again",
                                     "SOURCE_PERMISSION_REVOKED_AGAIN")
            assert second.store.get_object(binding.object_id).digest == source.digest
            assert second.current_quote().digest == quote.digest
            assert second.current_snapshot().digest == snapshot.digest
            return advisory

        monkeypatch.setattr(source_readmission, "execute_attempt", invalidate_after_candidate)
        with pytest.raises(IntegrityError, match="WORKSPACE_ADVISORY_SOURCE_INVALIDATED"):
            prepare(first)
        assert executions == 1
        with pytest.raises(KeyError):
            group_detail(first, "recover-1")
        assert not first.changes.journal("WORKSPACE_SOURCE_GROUP_PREVIEWED")
        attempts = first.store.list_artifacts(artifact_id_prefix="workspace-preview-attempt:")
        assert len(attempts) == 1 and attempts[0].payload["status"] == "COMPLETE"
    finally:
        if second:
            second.close()
        first.close()
