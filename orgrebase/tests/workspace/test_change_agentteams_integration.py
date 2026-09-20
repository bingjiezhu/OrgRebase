"""Service wiring and business effect of a subsequent native ChangeSet."""

from __future__ import annotations

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_agentteams import CHECKOUT, LOCK
from tests.workspace.test_continuous_changes import PACK, make_service, proposal


def test_golden_service_wires_native_preview_to_exact_approval_and_one_apply(tmp_path):
    path = tmp_path / "business.sqlite"
    service = WorkspaceService(
        store_path=path, runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
        competition_mode="golden", competition_checkout=CHECKOUT,
        competition_lock_path=LOCK, competition_evidence_root=tmp_path / "native",
    )
    try:
        assert service.advisory_factory.native_required is True
        # Deterministic initial quote; only the subsequent change claims AT execution.
        service.form_quote()
        event = proposal(service, "native-business", "product_plan", "Enterprise Plus")
        service.register_change(event)
        before = service.current_quote()
        preview = service.preview_change(event.event_id)
        assert service.current_quote() == before
        detail = change_detail(service, event.event_id)
        native = detail["preview"]["native_execution"]
        assert native["native_agentteams_observed"] is True
        assert native["binding"]["change_set_digest"] == preview.change_set.digest
        assert detail["status"] == "PREVIEWED"
        assert "APPLY" not in detail["allowed_actions"]
        operations = service.state()["agentteams_operations"]["change_set_advisories"][event.event_id]
        assert operations["participation_status"] == "CONTROLLED_LOCAL_NATIVE_OBSERVED"
        assert operations["native_execution"] == native
        approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                          preview_digest=preview.preview.digest)
        result = service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
        after = service.current_quote()
        assert after.version == f"v{int(before.version[1:]) + 1}"
        assert after.payload["product_plan"] == "Enterprise Plus"
        for field in ("owner", "deliverable_kind", "customer_id", "launch_date", "data_residency",
                      "notice_required", "price_band", "currency", "partner_terms_code"):
            assert after.payload[field] == before.payload[field]
        assert change_detail(service, event.event_id)["status"] == "APPLIED"
        assert service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"]) == result
        assert service.current_quote() == after
    finally:
        service.close()


def test_golden_without_native_configuration_keeps_history_readable_but_blocks_new_dispatch(tmp_path):
    path = tmp_path / "historical.sqlite"
    initial = make_service(path)
    initial.close()
    service = WorkspaceService.reopen(path, runtime_configuration=load_enterprise_quote_pilot_pack(PACK),
                                      competition_mode="golden")
    try:
        assert change_detail(service, "currency")["status"] == "APPLIED"
        event = proposal(service, "no-native-config", "product_plan", "Enterprise Plus")
        service.register_change(event)
        before = service.current_quote()
        with pytest.raises(IntegrityError, match="AGENTTEAMS_CONFIG_REQUIRED"):
            service.preview_change(event.event_id)
        assert service.current_quote() == before
        assert service._preview_record(event.event_id) is None
    finally:
        service.close()
