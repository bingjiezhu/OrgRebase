"""Upstream business changes must exercise the current product, not a label table."""
from __future__ import annotations

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.domain import AuthorizationError
from orgrebase.impact import ImpactEngine
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


def test_source_driven_date_currency_and_product_changes_survive_restart(tmp_path, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    path = tmp_path / "upstream.sqlite"
    service = WorkspaceService(store_path=path, runtime_configuration=runtime)
    calls = []
    original = ImpactEngine.preview

    def observed_preview(self, *args, **kwargs):
        calls.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ImpactEngine, "preview", observed_preview)
    try:
        # An explicitly bound workspace starts with facts, not preset future edits.
        assert service.changes.count == 0
        service.form_quote()
        for index, (slot, value, domain) in enumerate([
            ("launch_date", "2031-11-05", "product"),
            ("currency", "CAD", "finance"),
            ("product_plan", "Enterprise Resilience", "product"),
        ]):
            event_id = f"upstream-{index}"
            before = service.current_quote()
            field = next(item for item in change_options(service)["fields"] if item["slot_id"] == slot)
            assert field["current"]["value"] != value
            submit_change(service, ChangeProposalInput(
                event_id=event_id, slot_id=slot, value=value,
                base_version=field["current"]["version"], base_digest=field["current"]["digest"],
                source_ref=f"source:controlled-{domain}-decision@{index}",
            ))
            call_count = len(calls)
            bundle = service.preview_change(event_id)
            assert len(calls) > call_count, "each change must reach the real ImpactEngine"
            assert {task.authority_domain for task in bundle.advisory.orchestration_plan.tasks} == {
                "coordination", domain, "gtm",
            }
            assert any(result.object_id == before.id and result.classification == "AFFECTED_HARD"
                       for result in bundle.preview.results)
            assert service.current_quote().digest == before.digest
            assert service._approval_record(event_id) is None
            other_owner = next(item.owner_id for item in service.enterprise_binding.resources
                               if item.domain_id != domain)
            with pytest.raises(AuthorizationError, match="WORKSPACE_APPROVER_MISMATCH"):
                service.approve_change(event_id, actor_id=other_owner, preview_digest=bundle.preview.digest)
            approval = service.approve_change(event_id, actor_id=field["owner_id"],
                                              preview_digest=bundle.preview.digest)
            result = service.apply_approved_change(event_id, approval_digest=approval["approval_digest"])
            after = service.current_quote()
            provenance = {"context_manifest", "rebased_from", "rebase_change_set"}
            assert {key: value for key, value in after.payload.items() if key not in provenance} == {
                **{key: value for key, value in before.payload.items() if key not in provenance}, slot: value,
            }
            assert after.payload["rebased_from"] == before.ref
            assert after.payload["rebase_change_set"] == bundle.change_set.deltas[0].digest
            assert after.version != before.version
            assert service.apply_approved_change(event_id, approval_digest=approval["approval_digest"]) == result
            service.close()
            service = WorkspaceService.reopen(path, runtime_configuration=runtime)
            assert service.current_quote().digest == after.digest
            assert service._change_status(event_id) == "APPLIED"
    finally:
        service.close()


def test_broken_impact_engine_cannot_produce_an_upstream_preview(tmp_path, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    service = WorkspaceService(store_path=tmp_path / "broken.sqlite", runtime_configuration=runtime)
    try:
        service.form_quote()
        before = service.current_quote()
        field = next(item for item in change_options(service)["fields"] if item["slot_id"] == "launch_date")
        submit_change(service, ChangeProposalInput(
            event_id="upstream-broken", slot_id="launch_date", value="2031-11-05",
            base_version=field["current"]["version"], base_digest=field["current"]["digest"],
            source_ref="source:controlled-product-decision@1",
        ))

        def fail(*args, **kwargs):
            raise RuntimeError("IMPACT_ENGINE_UNAVAILABLE_PROBE")

        monkeypatch.setattr(ImpactEngine, "preview", fail)
        with pytest.raises(RuntimeError, match="IMPACT_ENGINE_UNAVAILABLE_PROBE"):
            service.preview_change("upstream-broken")
        assert service._preview_record("upstream-broken") is None
        assert service._approval_record("upstream-broken") is None
        assert service.current_quote().digest == before.digest
    finally:
        service.close()
