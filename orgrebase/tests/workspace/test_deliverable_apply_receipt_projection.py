from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import IntegrityError
from orgrebase.workspace.models import DeliverableSetApplyReceipt
from tests.workspace.test_deliverable_set import approve_and_apply, make_dual_service, propose
from tests.workspace.test_priced_quote_pack import POLICY


def test_applied_set_receipt_is_read_only_and_keeps_the_exact_historical_results(tmp_path: Path) -> None:
    service, _, _ = make_dual_service(tmp_path)
    try:
        service.form_quote()
        bundle = propose(service, "discount-first", "pricing_policy", {**POLICY, "discount_bps": 1000})
        assert service.deliverable_set_change_view("discount-first")["apply_receipt"] is None
        result = approve_and_apply(service, "discount-first", bundle)
        with TestClient(create_app(workspace_service=service)) as client:
            before = service.store.verify_event_chain()
            response = client.get("/api/workspace/deliverable-set/changes/discount-first")
            assert response.status_code == 200, response.text
            batch = DeliverableSetApplyReceipt.model_validate(response.json()["apply_receipt"])
            commit = result["outcome"]["workspace_rebase_receipt"]
            assert (batch.id, batch.digest) == (
                commit["deliverable_set_receipt_ref"], commit["deliverable_set_receipt_digest"]
            )
            assert batch.candidate_set_digest == response.json()["candidate_set"]["digest"]
            assert batch.approval_set_digest == response.json()["approval_set"]["digest"]
            assert {member.result_ref for member in batch.members} == set(commit["successor_object_refs"])
            assert service.store.verify_event_chain() == before
            next_bundle = propose(service, "discount-second", "pricing_policy", {**POLICY, "discount_bps": 2000})
            approve_and_apply(service, "discount-second", next_bundle)
            assert service.current_quote().version == "v3"
            historical = client.get("/api/workspace/deliverable-set/changes/discount-first")
            assert historical.status_code == 200, historical.text
            assert historical.json()["apply_receipt"] == batch.model_dump(mode="json")
    finally:
        service.close()


def test_applied_set_read_rejects_a_missing_batch_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = make_dual_service(tmp_path)
    try:
        service.form_quote()
        bundle = propose(service, "discount", "pricing_policy", {**POLICY, "discount_bps": 1000})
        result = approve_and_apply(service, "discount", bundle)
        missing_ref = result["outcome"]["workspace_rebase_receipt"]["deliverable_set_receipt_ref"]
        original = service.store.load_artifact

        def unavailable(artifact_id, expected_media_type=None, **kwargs):
            if artifact_id == missing_ref:
                raise IntegrityError("TEST_BATCH_RECEIPT_UNAVAILABLE")
            return original(artifact_id, expected_media_type, **kwargs)

        monkeypatch.setattr(service.store, "load_artifact", unavailable)
        with pytest.raises(IntegrityError, match="TEST_BATCH_RECEIPT_UNAVAILABLE"):
            service.deliverable_set_change_view("discount")
    finally:
        service.close()
