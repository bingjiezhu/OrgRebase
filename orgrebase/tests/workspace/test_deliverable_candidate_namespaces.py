"""Distinct proposals against one canonical base retain distinct candidate evidence."""

from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import FreshnessError, IntegrityError
from orgrebase.workspace import rebuild
from orgrebase.workspace.change_proposals import (
    ChangeProposalInput,
    change_detail,
    change_options,
    submit_change,
)
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_deliverable_set import approve_all, identity_directory, make_dual_service, propose
from tests.workspace.test_priced_quote_pack import BASKET, POLICY


def candidate_members(service, bundle):
    return {item.object_id: item for item in service._deliverable_candidate_record(bundle).members}


@pytest.mark.parametrize("second_slot", ["pricing_policy", "quote_basket"])
def test_same_base_proposals_have_distinct_evidence_and_only_one_can_apply(tmp_path: Path, second_slot):
    service, runtime, profile = make_dual_service(tmp_path)
    try:
        service.form_quote()
        before = {item.id: item.digest for item in service.current_deliverables()}
        pointer = service.current_graph_pointer().digest
        policy = service.store.get_object("policy:finance.pricing").digest
        first = propose(service, "first-discount", "pricing_policy", {**POLICY, "discount_bps": 1000})
        second_value = ({**POLICY, "discount_bps": 1500} if second_slot == "pricing_policy" else
                        {**BASKET, "items": [{**BASKET["items"][0], "quantity": 4}]})
        second = propose(service, "second-change", second_slot, second_value)
        first_members, second_members = candidate_members(service, first), candidate_members(service, second)
        assert first.change_set.digest != second.change_set.digest
        for object_id, left in first_members.items():
            right = second_members[object_id]
            assert left.candidate_ref == right.candidate_ref
            for field in ("context_ref", "trace_ref", "coverage_ref", "manifest_ref"):
                assert getattr(left, field) != getattr(right, field), field
        assert {item.id: item.digest for item in service.current_deliverables()} == before
        assert service.current_graph_pointer().digest == pointer
        assert service.store.get_object("policy:finance.pricing").digest == policy
        source, _ = approve_all(service, "first-discount", first)
        service.apply_approved_change("first-discount", approval_digest=source["approval_digest"])
        assert service.current_quote().payload["pricing"]["total"] == "40.50"
        assert change_detail(service, "second-change")["status"] == "STALE"
        committed = {item.id: item.digest for item in service.current_deliverables()}
        with service._test_as_actor(service.change_owner["second-change"]), pytest.raises(
            (FreshnessError, IntegrityError, RuntimeError),
        ):
            service.approve_change("second-change", actor_id=service.change_owner["second-change"],
                                   preview_digest=second.preview.digest)
        assert {item.id: item.digest for item in service.current_deliverables()} == committed
        refs = [getattr(member, field) for member in first_members.values()
                for field in ("context_ref", "trace_ref", "coverage_ref", "manifest_ref")]
        records = {ref: service.store.load_artifact(ref).payload_digest for ref in refs}
    finally:
        service.close()
    reopened = WorkspaceService.reopen(tmp_path / "dual.sqlite", runtime_configuration=runtime,
                                      deliverable_set_profile=profile, review_duration_seconds=0)
    try:
        assert {ref: reopened.store.load_artifact(ref).payload_digest for ref in refs} == records
        assert {item.id: item.digest for item in reopened.current_deliverables()} == committed
        assert reopened.store.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()


def test_rejected_proposal_can_be_revised_and_previewed_again_after_restart(tmp_path: Path):
    service, runtime, profile = make_dual_service(tmp_path)
    try:
        service.form_quote()
        rejected = propose(service, "rejected-discount", "pricing_policy", {**POLICY, "discount_bps": 1000})
        old_members = candidate_members(service, rejected)
        with service._test_as_actor(service.change_owner["rejected-discount"]):
            service.reject_change("rejected-discount", actor_id=service.change_owner["rejected-discount"],
                                  reason="Use the revised discount proposal")
        field = next(item for item in change_options(service)["fields"] if item["slot_id"] == "pricing_policy")
        source_ref = "source:revised-pricing@v2"
        submit_change(service, ChangeProposalInput(
            event_id="revised-discount", slot_id="pricing_policy", base_version=field["current"]["version"],
            base_digest=field["current"]["digest"], value={**POLICY, "discount_bps": 1500, "source_ref": source_ref},
            source_ref=source_ref, revises_event_id="rejected-discount",
        ))
        revised = service.preview_change("revised-discount")
        new_members = candidate_members(service, revised)
        assert all(new_members[key].context_ref != old_members[key].context_ref for key in old_members)
        assert {item.version for item in service.current_deliverables()} == {"v1"}
    finally:
        service.close()
    reopened = WorkspaceService.reopen(tmp_path / "dual.sqlite", runtime_configuration=runtime,
                                      deliverable_set_profile=profile, review_duration_seconds=0)
    try:
        members, as_actor = identity_directory(reopened)
        reopened._test_members, reopened._test_as_actor = members, as_actor
        recovered = reopened.preview_change("revised-discount")
        assert recovered.preview.digest == revised.preview.digest
        source, _ = approve_all(reopened, "revised-discount", recovered)
        reopened.apply_approved_change("revised-discount", approval_digest=source["approval_digest"])
        # 37.50 subtotal - 5.63 rounded discount + 6.37 rounded tax.
        assert reopened.current_quote().payload["pricing"]["total"] == "38.24"
        assert change_detail(reopened, "rejected-discount")["status"] == "REJECTED"
    finally:
        reopened.close()


def test_postgres_proposals_keep_disjoint_evidence_and_recover_pending_apply(postgres_runtime, tmp_path):
    from orgrebase.workspace.formation import quote_discount_memo_profile
    from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
    from orgrebase.workspace.pilot_authoring import (
        initialize_enterprise_quote_pilot_draft,
        seal_enterprise_quote_pilot_pack,
    )

    initialize_enterprise_quote_pilot_draft(tmp_path / "draft", template_name="priced-quote")
    seal_enterprise_quote_pilot_pack(tmp_path / "draft", tmp_path / "pack")
    runtime = load_enterprise_quote_pilot_pack(tmp_path / "pack")
    profile = quote_discount_memo_profile(runtime)
    credentials = postgres_runtime(tenant_id=runtime.profile.organization_id)
    options = {
        "store_path": credentials["runtime_dsn"], "store_tenant_id": runtime.profile.organization_id,
        "store_migrate": False, "runtime_configuration": runtime,
        "deliverable_set_profile": profile, "review_duration_seconds": 0,
    }
    service = WorkspaceService(**options)
    try:
        service.form_quote()
        policy = dict(runtime.source_values["pricing_policy"].value)
        first = propose(service, "pg-discount-first", "pricing_policy", {**policy, "discount_bps": 1000})
        second = propose(service, "pg-discount-second", "pricing_policy", {**policy, "discount_bps": 1500})
        left, right = candidate_members(service, first), candidate_members(service, second)
        assert all(left[key].manifest_ref != right[key].manifest_ref for key in left)
        assert {item.version for item in service.current_deliverables()} == {"v1"}
        source, _ = approve_all(service, "pg-discount-first", first)
    finally:
        service.close()
    restarted = WorkspaceService(**options)
    try:
        members, as_actor = identity_directory(restarted)
        restarted._test_members, restarted._test_as_actor = members, as_actor
        restarted.apply_approved_change("pg-discount-first", approval_digest=source["approval_digest"])
        assert restarted.current_quote().payload["pricing"]["total"] == "90.00"
        assert change_detail(restarted, "pg-discount-second")["status"] == "STALE"
        assert restarted.store.runtime_role_safe() is True
        assert restarted.store.verify_event_chain()["status"] == "PASS"
    finally:
        restarted.close()


def test_committed_legacy_evidence_names_remain_readable_without_byte_rewrites(tmp_path, monkeypatch):
    service, runtime, profile = make_dual_service(tmp_path)
    try:
        service.form_quote()
        # Generate a controlled fixture using the original unscoped identifier contract.
        with monkeypatch.context() as legacy:
            legacy.setattr(rebuild, "_scoped_evidence_id", lambda identifier, _scope: identifier)
            bundle = propose(service, "legacy-discount", "pricing_policy", {**POLICY, "discount_bps": 1000})
            members = candidate_members(service, bundle)
            assert all("-changeset-" not in member.context_ref for member in members.values())
            source, _ = approve_all(service, "legacy-discount", bundle)
            service.apply_approved_change("legacy-discount", approval_digest=source["approval_digest"])
        refs = [getattr(member, field) for member in members.values()
                for field in ("context_ref", "trace_ref", "coverage_ref", "manifest_ref")]
        raw_before = {ref: service.store.connection.execute(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?", (ref,),
        ).fetchone()[0] for ref in refs}
        assert change_detail(service, "legacy-discount")["status"] == "APPLIED"
    finally:
        service.close()
    reopened = WorkspaceService.reopen(tmp_path / "dual.sqlite", runtime_configuration=runtime,
                                      deliverable_set_profile=profile, review_duration_seconds=0)
    try:
        assert change_detail(reopened, "legacy-discount")["status"] == "APPLIED"
        raw_after = {ref: reopened.store.connection.execute(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?", (ref,),
        ).fetchone()[0] for ref in refs}
        assert raw_after == raw_before
    finally:
        reopened.close()


def test_runtime_change_holds_original_preview_and_requires_a_distinct_revised_event(tmp_path, monkeypatch):
    from orgrebase.workspace import runtime_revision

    service, _, _ = make_dual_service(tmp_path)
    try:
        service.form_quote()
        original = propose(service, "before-update", "pricing_policy", {**POLICY, "discount_bps": 1000})
        original_members = candidate_members(service, original)
        frozen_members = {key: value.model_dump(mode="json") for key, value in original_members.items()}
        before = {item.id: item.digest for item in service.current_deliverables()}
        updated = {**runtime_revision.current_revision(), "handler_contract": "controlled-runtime-update"}
        updated["revision_digest"] = sha256_digest({key: value for key, value in updated.items() if key != "revision_digest"})
        monkeypatch.setattr(runtime_revision, "current_revision", lambda: updated)
        with pytest.raises(RuntimeError, match="WORKSPACE_RUNTIME_REPLAN_REQUIRED"):
            service.preview_change("before-update")
        field = next(item for item in change_options(service)["fields"] if item["slot_id"] == "pricing_policy")
        source_ref = "source:after-update@v1"
        submit_change(service, ChangeProposalInput(
            event_id="after-update", slot_id="pricing_policy", base_version=field["current"]["version"],
            base_digest=field["current"]["digest"],
            value={**POLICY, "discount_bps": 1000, "source_ref": source_ref},
            source_ref=source_ref, revises_event_id="before-update",
        ))
        revised = service.preview_change("after-update")
        replay = service.preview_change("after-update")
        assert replay.preview.digest == revised.preview.digest
        assert service._deliverable_candidate_record(replay).digest == service._deliverable_candidate_record(revised).digest
        new_members = candidate_members(service, revised)
        assert all(new_members[key].manifest_ref != original_members[key].manifest_ref for key in original_members)
        assert {key: value.model_dump(mode="json") for key, value in candidate_members(service, original).items()} == frozen_members
        assert {item.id: item.digest for item in service.current_deliverables()} == before
    finally:
        service.close()
